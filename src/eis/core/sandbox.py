# SPDX-License-Identifier: Apache-2.0

"""Narrow T2 sandbox interface and a Linux-container backend.

Only immutable, application-registered images and fixed command profiles can be
run here. Nothing in this module accepts an executable, argument, image, mount,
environment value or isolation setting from model output. A backend does not
claim T2 unless every required protection is demonstrably available; otherwise
the gateway returns ``isolation_unavailable`` and executes nothing.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

MAX_ENV_KEYS = 16
MAX_ENV_VALUE_CHARS = 512
PROBE_TIMEOUT_SECONDS = 20
CLEANUP_TIMEOUT_SECONDS = 5
CONTAINER_USER = "10001:10001"
CONTAINER_WORKDIR = "/workspace"
_DIGEST_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_SECRET_KEY_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "SSH",
    "AWS",
    "AZURE",
    "GCP",
    "API",
    "COOKIE",
    "AUTH",
    "PRIVATE",
    "EIS_",
)
_IGNORED_NAMES = frozenset(
    {".git", ".eis", ".ssh", ".aws", ".gnupg", "keys", "secrets", "credentials", "backups"}
)
_IGNORED_SUFFIXES = (
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".db",
    ".db-wal",
    ".db-shm",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keystore",
    ".jks",
)


class SandboxError(RuntimeError):
    """Raised for a backend configuration that cannot satisfy T2."""


@dataclass(frozen=True)
class SandboxCapabilities:
    """The protections a backend can actually demonstrate."""

    platform: str
    engine: str
    immutable_image: bool = False
    non_root: bool = False
    no_network: bool = False
    read_only_rootfs: bool = False
    read_only_project_mount: bool = False
    capabilities_dropped: bool = False
    no_new_privileges: bool = False
    seccomp: bool = False
    pids_limit: bool = False
    resource_limits: bool = False
    disposable_copy: bool = False

    @property
    def sufficient(self) -> bool:
        """True only when every T2 protection is present on Linux."""
        return (
            self.platform.startswith("linux")
            and bool(self.engine)
            and all(
                (
                    self.immutable_image,
                    self.non_root,
                    self.no_network,
                    self.read_only_rootfs,
                    self.capabilities_dropped,
                    self.no_new_privileges,
                    self.seccomp,
                    self.pids_limit,
                    self.resource_limits,
                    self.disposable_copy,
                )
            )
        )


@dataclass(frozen=True)
class SandboxRequest:
    """Trusted inputs assembled by the gateway, never by a model."""

    run_id: str
    profile_id: str
    argv: tuple[str, ...]
    project_root: Path
    env: Mapping[str, str]
    timeout_seconds: int
    output_cap_bytes: int
    writable_copy: bool = False
    cpu_limit: str = "1"
    memory_limit: str = "2g"
    pids_limit: int = 256


@dataclass(frozen=True)
class SandboxResult:
    status: Literal["success", "failed", "unavailable", "refused"]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool
    timed_out: bool
    error_code: str | None


class SandboxBackend(Protocol):
    """Run one fixed profile inside an isolated, disposable workspace."""

    @property
    def capabilities(self) -> SandboxCapabilities: ...

    def run(self, request: SandboxRequest) -> SandboxResult: ...


def validate_env_allowlist(names: frozenset[str]) -> None:
    """Reject secret-shaped or excessive declared environment keys."""
    if len(names) > MAX_ENV_KEYS:
        raise ValueError(f"environment allowlist has more than {MAX_ENV_KEYS} keys")
    for name in names:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name):
            raise ValueError("environment keys must be uppercase identifiers")
        if any(marker in name for marker in _SECRET_KEY_MARKERS):
            raise ValueError("environment allowlist contains a secret-shaped key")


def declared_env(profile: object) -> dict[str, str]:
    """Read only application-declared, non-secret values from the host."""
    allowlist: frozenset[str] = frozenset(getattr(profile, "env_allowlist", frozenset()))
    validate_env_allowlist(allowlist)
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    for name in sorted(allowlist):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value[:MAX_ENV_VALUE_CHARS]
    return env


class LinuxContainerBackend:
    """Docker/Podman backend with fixed, fail-closed container flags.

    The image must be an immutable digest chosen by the application. No image,
    mount, executable or isolation flag can be supplied by a caller or model.
    The optional ``probe`` callable is injectable so tests can exercise the
    configuration paths without touching a container engine.
    """

    def __init__(
        self,
        *,
        engine: str,
        image: str,
        seccomp_profile: Path,
        probe: Callable[[str, str, Path], SandboxCapabilities] | None = None,
    ) -> None:
        if engine not in {"docker", "podman"}:
            raise SandboxError("unsupported container engine")
        if not _DIGEST_RE.fullmatch(image):
            raise SandboxError("T2 requires an immutable image digest")
        self._engine = engine
        self._image = image
        self._seccomp = Path(seccomp_profile).resolve()
        checker = probe or probe_linux_container
        self._capabilities = checker(engine, image, self._seccomp)
        if not isinstance(self._capabilities, SandboxCapabilities):
            raise SandboxError("container capability probe returned an invalid value")

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._capabilities

    def run(self, request: SandboxRequest) -> SandboxResult:
        if not self.capabilities.sufficient:
            return _unavailable("isolation_unavailable")
        if not request.argv or not Path(request.argv[0]).is_absolute():
            return _unavailable("invalid_profile")
        if not re.fullmatch(r"run_[0-9a-f]{32}", request.run_id):
            return _unavailable("invalid_run_id")
        validate_env_allowlist(frozenset(request.env))
        with tempfile.TemporaryDirectory(prefix="eis-t2-") as scratch:
            workspace = Path(scratch) / "workspace"
            try:
                shutil.copytree(
                    request.project_root,
                    workspace,
                    symlinks=True,
                    ignore=_copy_ignore,
                )
            except (OSError, shutil.Error):
                return _unavailable("workspace_copy_failed")
            mount_mode = "rw" if request.writable_copy else "ro"
            container_name = f"eis-{request.run_id}"
            argv = self._build_argv(request, workspace, mount_mode, container_name)
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    close_fds=True,
                )
            except OSError:
                return _unavailable("backend_unavailable")
            try:
                return _collect(process, request)
            finally:
                self._cleanup(container_name)

    def _build_argv(
        self,
        request: SandboxRequest,
        workspace: Path,
        mount_mode: str,
        container_name: str,
    ) -> list[str]:
        argv = [
            self._engine,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--user",
            CONTAINER_USER,
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            f"seccomp={self._seccomp}",
            "--pids-limit",
            str(request.pids_limit),
            "--cpus",
            request.cpu_limit,
            "--memory",
            request.memory_limit,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=8m",
            "--volume",
            f"{workspace}:{CONTAINER_WORKDIR}:{mount_mode}",
            "--workdir",
            CONTAINER_WORKDIR,
        ]
        for name, value in sorted(request.env.items()):
            if len(value) > MAX_ENV_VALUE_CHARS or "\x00" in value or "\n" in value:
                raise SandboxError("declared environment value is invalid")
            argv.extend(["--env", f"{name}={value}"])
        argv.append(self._image)
        argv.extend(request.argv)
        return argv

    def _cleanup(self, container_name: str) -> None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self._engine, "rm", "--force", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CLEANUP_TIMEOUT_SECONDS,
                shell=False,
                check=False,
            )


def probe_linux_container(engine: str, image: str, seccomp_profile: Path) -> SandboxCapabilities:
    """Probe an engine with the exact protections used for real runs.

    A run is accepted only when a fixed probe container executes as non-root,
    with a read-only root filesystem and read-only workspace, under the full
    flag set below. Any missing or refused protection yields insufficient
    capabilities. The probe uses ``--pull=never``; it never downloads an image.
    """
    caps = SandboxCapabilities(platform=sys.platform, engine=engine)
    seccomp_profile = Path(seccomp_profile).resolve()
    if not sys.platform.startswith("linux"):
        return caps
    if shutil.which(engine) is None or not seccomp_profile.is_file():
        return caps
    if not _DIGEST_RE.fullmatch(image):
        return caps
    argv = [
        engine,
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user",
        CONTAINER_USER,
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--security-opt",
        f"seccomp={seccomp_profile}",
        "--pids-limit",
        "256",
        "--cpus",
        "1",
        "--memory",
        "2g",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=8m",
        "--volume",
        f"{seccomp_profile.parent}:{CONTAINER_WORKDIR}:ro",
        "--workdir",
        CONTAINER_WORKDIR,
        image,
        "/bin/sh",
        "-c",
        'test ! -w / && test ! -w /workspace && test "$(id -u)" = 10001 && '
        "cat /proc/self/status && printf '\\n__NET__\\n' && ls /sys/class/net && "
        "printf '__PIDS__\\n' && cat /sys/fs/cgroup/pids.max && "
        "printf '__CPU__\\n' && cat /sys/fs/cgroup/cpu.max && "
        "printf '__MEM__\\n' && cat /sys/fs/cgroup/memory.max",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return caps
    if completed.returncode != 0:
        return caps
    try:
        observed = _parse_probe(completed.stdout)
    except (ValueError, IndexError, AttributeError):
        return caps
    return SandboxCapabilities(
        platform=sys.platform,
        engine=engine,
        immutable_image=True,
        non_root=True,
        no_network=observed["no_network"],
        read_only_rootfs=True,
        read_only_project_mount=True,
        capabilities_dropped=observed["capabilities_dropped"],
        no_new_privileges=observed["no_new_privileges"],
        seccomp=observed["seccomp"],
        pids_limit=observed["pids_limit"],
        resource_limits=observed["resource_limits"],
        disposable_copy=True,
    )


def _parse_probe(stdout: str) -> dict[str, bool]:
    def section(name: str) -> str:
        return stdout.split(f"__{name}__\n", 1)[1].split("\n", 1)[0].strip()

    pids = section("PIDS")
    cpu = section("CPU")
    memory = section("MEM")
    return {
        "no_network": section("NET").split() == ["lo"],
        "capabilities_dropped": bool(re.search(r"(?m)^CapEff:\s*0+\s*$", stdout)),
        "no_new_privileges": bool(re.search(r"(?m)^NoNewPrivs:\s*1\s*$", stdout)),
        "seccomp": bool(re.search(r"(?m)^Seccomp:\s*[1-9][0-9]*\s*$", stdout)),
        "pids_limit": pids.isdigit() and int(pids) <= 256,
        "resource_limits": (
            bool(cpu) and not cpu.startswith("max") and bool(memory) and memory != "max"
        ),
    }


def _collect(process: subprocess.Popen[bytes], request: SandboxRequest) -> SandboxResult:
    stdout = bytearray()
    stderr = bytearray()
    total = 0
    lock = threading.Lock()
    overflow = threading.Event()

    def drain(stream: object, captured: bytearray) -> None:
        nonlocal total
        while chunk := stream.read(8192):  # type: ignore[attr-defined]
            with lock:
                remaining = max(0, request.output_cap_bytes - total)
                if remaining:
                    captured.extend(chunk[:remaining])
                    total += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    overflow.set()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=request.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if process.poll() is None:
            process.kill()
        process.wait()
    for thread in threads:
        thread.join(timeout=2)
    truncated = overflow.is_set()
    exit_code = process.returncode
    if timed_out:
        return _result(
            "failed", exit_code, bytes(stdout), bytes(stderr), truncated, True, "timeout"
        )
    if truncated:
        return _result(
            "failed", exit_code, bytes(stdout), bytes(stderr), True, False, "output_truncated"
        )
    status: Literal["success", "failed"] = "success" if exit_code == 0 else "failed"
    return _result(
        status,
        exit_code,
        bytes(stdout),
        bytes(stderr),
        False,
        False,
        None if status == "success" else "nonzero_exit",
    )


def _unavailable(code: str) -> SandboxResult:
    return _result("unavailable", None, b"", b"", False, False, code)


def _result(
    status: Literal["success", "failed", "unavailable", "refused"],
    exit_code: int | None,
    stdout: bytes,
    stderr: bytes,
    truncated: bool,
    timed_out: bool,
    error_code: str | None,
) -> SandboxResult:
    return SandboxResult(status, exit_code, stdout, stderr, truncated, timed_out, error_code)


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lowered = name.casefold()
        if (
            lowered in _IGNORED_NAMES
            or lowered == ".env"
            or lowered.startswith(".env.")
            or lowered.endswith(_IGNORED_SUFFIXES)
        ):
            ignored.add(name)
    return ignored
