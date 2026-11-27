# SPDX-License-Identifier: Apache-2.0

"""T2 sandbox routing and fail-closed configuration tests with a fake backend."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from eis.core.audit_chain import AuditIntegrityError
from eis.core.execution import CommandProfile, ExecutionGateway
from eis.core.policy_service import PolicyService
from eis.core.sandbox import (
    LinuxContainerBackend,
    SandboxCapabilities,
    SandboxRequest,
    SandboxResult,
    _parse_probe,
    probe_linux_container,
    validate_env_allowlist,
)
from eis.policy import Policy, PolicyEngine
from eis.storage.approvals import ApprovalStore
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decisions import SqlitePolicyDecisionSink

IMAGE = "eis/verify@sha256:" + "a" * 64
EXE = sys.executable
PRINCIPAL = "agent:eis.primary"
GOOD_CAPS = SandboxCapabilities(
    platform="linux",
    engine="docker",
    immutable_image=True,
    non_root=True,
    no_network=True,
    read_only_rootfs=True,
    read_only_project_mount=True,
    capabilities_dropped=True,
    no_new_privileges=True,
    seccomp=True,
    pids_limit=True,
    resource_limits=True,
    disposable_copy=True,
)


@dataclass
class FakeBackend:
    capabilities_value: SandboxCapabilities = GOOD_CAPS
    result: SandboxResult | None = None
    error: Exception | None = None
    requests: list[SandboxRequest] | None = None

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self.capabilities_value

    def run(self, request: SandboxRequest) -> SandboxResult:
        if self.requests is not None:
            self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return SandboxResult("success", 0, b"verified", b"", False, False, None)


def _policy(*, allow_verify: bool = True) -> Policy:
    rules: list[dict[str, str]] = []
    if allow_verify:
        rules.append(
            {
                "id": "verify",
                "principal": PRINCIPAL,
                "action": "project.verify",
                "resource": "project:demo",
                "effect": "allow",
            }
        )
    return Policy.from_dict({"version": "t2-v1", "default": "deny", "rules": rules})


def _gateway(
    tmp_path: Path,
    root: Path,
    backend: FakeBackend | None,
    *,
    profiles: dict[str, CommandProfile],
    trusted: bool = True,
    allow_verify: bool = True,
) -> tuple[SqliteDatabase, ExecutionGateway, PolicyService]:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    service = PolicyService(
        PolicyEngine(_policy(allow_verify=allow_verify)),
        SqlitePolicyDecisionSink(database),
    )
    gateway = ExecutionGateway(
        project_roots={"demo": root},
        trusted_projects=frozenset({"demo"}) if trusted else frozenset(),
        backup_dir=tmp_path / "backups",
        policy=service,
        approvals=ApprovalStore(database),
        integrity=AuditChainStore(database),
        command_profiles=profiles,
        sandbox=backend,
    )
    return database, gateway, service


def _profile(*, writable: bool = False, env: frozenset[str] = frozenset()) -> CommandProfile:
    return CommandProfile(
        profile_id="verify",
        argv=(EXE, "-m", "pytest", "-q"),
        timeout_seconds=17,
        output_cap_bytes=4096,
        minimum_tier="T2",
        needs_writable_workspace=writable,
        env_allowlist=env,
    )


def test_profile_configuration_rejects_secret_and_unsafe_values() -> None:
    with pytest.raises(ValueError, match="T2 isolation"):
        CommandProfile(profile_id="w", argv=(EXE,), needs_writable_workspace=True)
    with pytest.raises(ValueError, match="secret-shaped"):
        _profile(env=frozenset({"OPENAI_API_KEY"}))
    with pytest.raises(ValueError, match="more than"):
        _profile(env=frozenset(f"VAR_{index}" for index in range(17)))
    with pytest.raises(ValueError, match="uppercase"):
        validate_env_allowlist(frozenset({"lowercase"}))


def test_linux_backend_requires_verified_protections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("eis.core.sandbox.sys.platform", "win32")
    with pytest.raises(Exception, match="image digest"):
        LinuxContainerBackend(engine="docker", image="latest", seccomp_profile=seccomp)
    with pytest.raises(Exception, match="engine"):
        LinuxContainerBackend(
            engine="unknown", image=IMAGE, seccomp_profile=seccomp, probe=lambda *a: GOOD_CAPS
        )
    insufficient = LinuxContainerBackend(
        engine="docker",
        image=IMAGE,
        seccomp_profile=seccomp,
        probe=lambda *a: SandboxCapabilities(platform="linux", engine="docker"),
    )
    assert not insufficient.capabilities.sufficient
    # On this host the real probe cannot demonstrate Linux container protections.
    assert not probe_linux_container("docker", IMAGE, seccomp).sufficient


def test_t2_profile_runs_only_through_the_registered_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "secret.env").write_text("host-secret", encoding="utf-8")
    monkeypatch.setenv("VERIFY_FLAG", "declared-value")
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-reach-container")
    requests: list[SandboxRequest] = []
    backend = FakeBackend(requests=requests)
    database, gateway, _ = _gateway(
        tmp_path, root, backend, profiles={"verify": _profile(env=frozenset({"VERIFY_FLAG"}))}
    )
    result = gateway.run_profile("demo", "verify", principal=PRINCIPAL)

    assert result.status == "success"
    assert result.stdout == b"verified"
    assert len(requests) == 1
    request = requests[0]
    assert request.argv == (EXE, "-m", "pytest", "-q")
    assert request.timeout_seconds == 17
    assert request.output_cap_bytes == 4096
    assert request.writable_copy is False
    assert request.env["VERIFY_FLAG"] == "declared-value"
    assert "OPENAI_API_KEY" not in request.env
    assert "PYTHONDONTWRITEBYTECODE" in request.env
    assert hashlib.sha256(b"host-secret").hexdigest() not in json.dumps(
        [dict(row) for row in database.connect().execute("SELECT * FROM audit_events")]
    )
    with database.connect() as connection:
        run = connection.execute(
            "SELECT status, profile_id FROM execution_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
        events = [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]
    assert (run["status"], run["profile_id"]) == ("success", "verify")
    assert "execution_started" in events and "execution_completed" in events


def test_policy_and_audit_denials_stop_before_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    requests: list[SandboxRequest] = []
    _, denied, _ = _gateway(
        tmp_path,
        root,
        FakeBackend(requests=requests),
        profiles={"verify": _profile()},
        allow_verify=False,
    )
    result = denied.run_profile("demo", "verify", principal=PRINCIPAL)
    assert result.error_code == "policy_denied"
    assert requests == []

    _, gateway, _ = _gateway(
        tmp_path / "audit", root, FakeBackend(requests=requests), profiles={"verify": _profile()}
    )
    monkeypatch.setattr(
        gateway._integrity,
        "require_intact",
        lambda: (_ for _ in ()).throw(AuditIntegrityError("chain broken")),
    )
    with pytest.raises(AuditIntegrityError):
        gateway.run_profile("demo", "verify", principal=PRINCIPAL)
    assert requests == []


def test_untrusted_project_routes_to_t2_and_fails_closed_without_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    requests: list[SandboxRequest] = []
    trusted = CommandProfile(profile_id="t1", argv=(EXE, "-m", "pytest", "-q"))
    _, gateway, _ = _gateway(
        tmp_path, root, FakeBackend(requests=requests), profiles={"t1": trusted}, trusted=False
    )
    assert gateway.run_profile("demo", "t1", principal=PRINCIPAL).status == "success"
    assert len(requests) == 1

    _, no_backend, _ = _gateway(
        tmp_path / "none", root, None, profiles={"t1": trusted}, trusted=False
    )
    refused = no_backend.run_profile("demo", "t1", principal=PRINCIPAL)
    assert refused.status == "refused"
    assert refused.error_code == "isolation_unavailable"

    insufficient = FakeBackend(SandboxCapabilities(platform="windows", engine="docker"))
    _, misconfigured, _ = _gateway(
        tmp_path / "bad", root, insufficient, profiles={"t1": trusted}, trusted=False
    )
    assert (
        misconfigured.run_profile("demo", "t1", principal=PRINCIPAL).error_code
        == "isolation_unavailable"
    )


def test_backend_timeout_and_truncation_are_mapped_content_free(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    backend = FakeBackend(
        result=SandboxResult("failed", None, b"x" * 4096, b"", True, True, "timeout")
    )
    _, gateway, _ = _gateway(tmp_path, root, backend, profiles={"verify": _profile()})
    result = gateway.run_profile("demo", "verify", principal=PRINCIPAL)
    assert result.status == "failed"
    assert result.timed_out is True
    assert result.truncated is True
    assert result.error_code == "timeout"
    assert len(result.stdout) <= 4096


def test_backend_os_error_is_reported_as_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    backend = FakeBackend(error=OSError("engine gone"))
    _, gateway, _ = _gateway(tmp_path, root, backend, profiles={"verify": _profile()})
    result = gateway.run_profile("demo", "verify", principal=PRINCIPAL)
    assert result.status == "unavailable"
    assert result.error_code == "isolation_unavailable"


def test_container_argv_enforces_all_required_protections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text("{}", encoding="utf-8")
    backend = LinuxContainerBackend(
        engine="docker",
        image=IMAGE,
        seccomp_profile=seccomp,
        probe=lambda *args: GOOD_CAPS,
    )
    root = tmp_path / "project"
    root.mkdir()
    request = SandboxRequest(
        run_id="run_" + "b" * 32,
        profile_id="verify",
        argv=(EXE, "-m", "pytest", "-q"),
        project_root=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=17,
        output_cap_bytes=4096,
    )
    workspace = tmp_path / "workspace"
    argv = backend._build_argv(request, workspace, "ro", "eis-run")

    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "no-new-privileges=true" in argv
    assert f"seccomp={seccomp}" in argv
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--cpus") + 1] == "1"
    assert argv[argv.index("--memory") + 1] == "2g"
    assert "--pull=never" in argv
    assert f"{workspace}:/workspace:ro" in argv
    assert not {"--privileged", "--network=host", "-v", "--env-file"} & set(argv)
    assert argv[-5:] == [IMAGE, EXE, "-m", "pytest", "-q"]
    assert not any("API_KEY" in item or "OPENAI" in item for item in argv)
    assert backend.capabilities.sufficient


def test_writable_profile_uses_disposable_copy_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text("{}", encoding="utf-8")
    backend = LinuxContainerBackend(
        engine="docker",
        image=IMAGE,
        seccomp_profile=seccomp,
        probe=lambda *args: GOOD_CAPS,
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text("not-in-copy", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeProcess:
        def wait(self, timeout: int | None = None) -> int:
            return 0

    def fake_popen(argv: list[str], **kwargs: object) -> FakeProcess:
        captured["argv"] = argv
        return FakeProcess()

    monkeypatch.setattr("eis.core.sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "eis.core.sandbox._collect",
        lambda process, request: SandboxResult("success", 0, b"", b"", False, False, None),
    )
    monkeypatch.setattr(backend, "_cleanup", lambda name: captured.update(cleanup=name))
    request = SandboxRequest(
        run_id="run_" + "c" * 32,
        profile_id="verify",
        argv=(EXE, "-m", "pytest", "-q"),
        project_root=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        timeout_seconds=17,
        output_cap_bytes=4096,
        writable_copy=True,
    )
    result = backend.run(request)

    assert result.status == "success"
    argv = captured["argv"]
    assert isinstance(argv, list)
    mount = next(item for item in argv if item.endswith(":/workspace:rw"))
    workspace = Path(mount.rsplit(":", 2)[0])
    assert captured["cleanup"] == "eis-run_cccccccccccccccccccccccccccccccc"
    assert not workspace.exists()  # temporary writable copy discarded
    assert not (workspace.parent / ".env").exists()


_GOOD_PROBE = (
    "CapEff:\t0000000000000000\n"
    "NoNewPrivs:\t1\n"
    "Seccomp:\t2\n"
    "__NET__\n"
    "lo\n"
    "__PIDS__\n"
    "256\n"
    "__CPU__\n"
    "100000 100000\n"
    "__MEM__\n"
    "2147483648\n"
)


def test_probe_parser_accepts_all_demonstrated_protections() -> None:
    assert all(_parse_probe(_GOOD_PROBE).values())


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        ("CapEff:\t0000000000000001", "capabilities_dropped"),
        ("NoNewPrivs:\t0", "no_new_privileges"),
        ("Seccomp:\t0", "seccomp"),
        ("__NET__\neth0", "no_network"),
        ("__PIDS__\nmax", "pids_limit"),
        ("__CPU__\nmax 100000", "resource_limits"),
        ("__MEM__\nmax", "resource_limits"),
    ],
)
def test_probe_parser_rejects_missing_protection(replacement: str, code: str) -> None:
    original = {
        "CapEff:\t0000000000000000": "CapEff:\t0000000000000001",
        "NoNewPrivs:\t1": "NoNewPrivs:\t0",
        "Seccomp:\t2": "Seccomp:\t0",
        "__NET__\nlo": "__NET__\neth0",
        "__PIDS__\n256": "__PIDS__\nmax",
        "__CPU__\n100000 100000": "__CPU__\nmax 100000",
        "__MEM__\n2147483648": "__MEM__\nmax",
    }
    old = next(key for key, value in original.items() if value == replacement)
    text = _GOOD_PROBE.replace(old, replacement)
    assert _parse_probe(text)[code] is False
