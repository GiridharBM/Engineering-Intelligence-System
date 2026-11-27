# SPDX-License-Identifier: Apache-2.0

"""Verify the real Linux T2 container backend on a hosted Ubuntu runner.

The image must be digest-pinned and pulled before this script runs. Container
executions use the backend's fixed flags (no network, read-only root/workspace,
non-root, dropped capabilities, no-new-privileges, seccomp, cgroup limits,
bounded output/timeout, disposable copy and forced cleanup). This proves those
runner protections at a point in time; it does not defend against a compromised
runner, daemon or kernel.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from eis.core.sandbox import (
    LinuxContainerBackend,
    SandboxError,
    SandboxRequest,
    SandboxResult,
    probe_linux_container,
)

FIXTURE_TEXT = "t2-fixture-only\n"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment: {name}")
    return value


def _request(
    root: Path, argv: tuple[str, ...], *, writable: bool = False, **kwargs: object
) -> SandboxRequest:
    return SandboxRequest(
        run_id=f"run_{uuid.uuid4().hex}",
        profile_id="t2-hosted-probe",
        argv=argv,
        project_root=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=int(kwargs.get("timeout_seconds", 10)),
        output_cap_bytes=int(kwargs.get("output_cap_bytes", 4096)),
        writable_copy=writable,
    )


def _describe(result: SandboxResult) -> str:
    return (
        f"status={result.status} error={result.error_code} "
        f"truncated={result.truncated} timed_out={result.timed_out} "
        f"stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}"
    )


def _clean_containers() -> None:
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=^eis-run_"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.stdout.strip():
        raise SystemExit("T2 container cleanup failed: leftover containers")


def main() -> int:
    image = _require_env("EIS_T2_IMAGE")
    seccomp = Path(_require_env("EIS_T2_SECCOMP")).resolve()
    if not seccomp.is_file():
        raise SystemExit("seccomp profile is missing")
    if sys.platform != "linux":
        raise SystemExit("real T2 verification requires Linux")

    caps = probe_linux_container("docker", image, seccomp)
    if not caps.sufficient:
        missing = [
            name
            for name in (
                "immutable_image",
                "non_root",
                "no_network",
                "read_only_rootfs",
                "read_only_project_mount",
                "capabilities_dropped",
                "no_new_privileges",
                "seccomp",
                "pids_limit",
                "resource_limits",
                "disposable_copy",
            )
            if not bool(getattr(caps, name))
        ]
        print(f"::error::T2 probe missing protections: {','.join(missing)}")
        raise SystemExit("hosted T2 capability probe did not demonstrate all protections")
    print("probe_platform=linux")
    print("probe_engine=docker")
    print(f"probe_image_digest={image}")
    for name in (
        "immutable_image",
        "non_root",
        "no_network",
        "read_only_rootfs",
        "read_only_project_mount",
        "capabilities_dropped",
        "no_new_privileges",
        "seccomp",
        "pids_limit",
        "resource_limits",
        "disposable_copy",
    ):
        print(f"probe_{name}={str(bool(getattr(caps, name))).lower()}")

    backend = LinuxContainerBackend(engine="docker", image=image, seccomp_profile=seccomp)
    with tempfile.TemporaryDirectory(prefix="eis-t2-hosted-") as scratch:
        root = Path(scratch)
        # copytree preserves this mode on the mounted copy; the container runs
        # as uid 10001, so a private (0700) fixture would be unreadable there.
        root.chmod(0o777)
        (root / "fixture.txt").write_text(FIXTURE_TEXT, encoding="utf-8")
        ok = backend.run(
            _request(
                root,
                ("/bin/sh", "-c", "id -u && cat /workspace/fixture.txt"),
            )
        )
        if ok.status != "success" or b"10001" not in ok.stdout:
            raise SystemExit(f"read-only T2 execution did not run as non-root: {_describe(ok)}")
        if FIXTURE_TEXT.encode() not in ok.stdout:
            raise SystemExit("disposable project mount did not contain the fixture")
        print("run_read_only=success")

        truncated = backend.run(
            _request(root, ("/bin/sh", "-c", "printf 1234567890"), output_cap_bytes=4)
        )
        if not truncated.truncated or truncated.status != "failed":
            raise SystemExit(f"output cap was not enforced: {_describe(truncated)}")
        print("run_output_cap=enforced")

        timed_out = backend.run(_request(root, ("/bin/sh", "-c", "sleep 10"), timeout_seconds=1))
        if not timed_out.timed_out or timed_out.status != "failed":
            raise SystemExit(f"timeout was not enforced: {_describe(timed_out)}")
        print("run_timeout=enforced")

        writable = backend.run(
            _request(
                root,
                ("/bin/sh", "-c", "echo t2-only > marker.txt && cat marker.txt"),
                writable=True,
            )
        )
        if writable.status != "success" or b"t2-only" not in writable.stdout:
            raise SystemExit(
                f"writable disposable copy did not accept the probe write: {_describe(writable)}"
            )
        if (root / "marker.txt").exists():
            raise SystemExit("writable probe modified the host fixture")
        if (root / "fixture.txt").read_text(encoding="utf-8") != FIXTURE_TEXT:
            raise SystemExit("host fixture changed during T2 execution")
        print("run_disposable_copy=discarded")

    # Missing required capability must fail closed without executing a container.
    missing = LinuxContainerBackend(
        engine="docker", image=image, seccomp_profile=Path("/nonexistent-seccomp.json")
    )
    if missing.capabilities.sufficient:
        raise SystemExit("missing seccomp profile was accepted")
    refused = missing.run(_request(root, ("/bin/sh", "-c", "id")))
    if refused.status != "unavailable" or refused.error_code != "isolation_unavailable":
        raise SystemExit(f"missing capability did not fail closed: {_describe(refused)}")
    print("fail_closed_missing_capability=ok")

    try:
        LinuxContainerBackend(
            engine="docker",
            image="docker.io/library/alpine:latest",
            seccomp_profile=seccomp,
        )
    except SandboxError:
        print("fail_closed_mutable_image=ok")
    else:
        raise SystemExit("mutable image tag was accepted")

    _clean_containers()
    print("cleanup=no_leftover_containers")
    return 0


if __name__ == "__main__":
    try:
        status = main()
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(f"::error::{exc.code}")
        raise
    raise SystemExit(status)
