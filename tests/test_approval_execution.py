# SPDX-License-Identifier: Apache-2.0

"""Approval lifecycle and narrowly scoped file-write gateway tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.core.approvals import ApprovalError, approval_digest
from eis.core.audit_chain import AuditIntegrityError
from eis.core.execution import CommandProfile, ExecutionDenied, ExecutionGateway
from eis.core.policy_service import PolicyService
from eis.policy import Policy, PolicyEngine
from eis.storage.approvals import ApprovalStore
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decision_store import PolicyDecisionStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink

runner = CliRunner()


def _policy_document(
    *, allow_write: bool = True, allow_verify: bool = True, version: str = "test-v1"
) -> dict[str, object]:
    rules: list[dict[str, str]] = [
        {
            "id": "agent-propose",
            "principal": "agent:eis.primary",
            "action": "project.propose",
            "resource": "project:demo",
            "effect": "allow",
        },
        {
            "id": "user-read",
            "principal": "user",
            "action": "project.read",
            "resource": "path:demo/app.txt",
            "effect": "allow",
        },
    ]
    if allow_write:
        rules.append(
            {
                "id": "user-write",
                "principal": "user",
                "action": "project.write",
                "resource": "path:demo/app.txt",
                "effect": "allow",
            }
        )
    if allow_verify:
        rules.append(
            {
                "id": "agent-verify",
                "principal": "agent:eis.primary",
                "action": "project.verify",
                "resource": "project:demo",
                "effect": "allow",
            }
        )
    return {"version": version, "default": "deny", "rules": rules}


def _policy(
    *, allow_write: bool = True, allow_verify: bool = True, version: str = "test-v1"
) -> Policy:
    return Policy.from_dict(
        _policy_document(allow_write=allow_write, allow_verify=allow_verify, version=version)
    )


def _gateway(
    tmp_path: Path,
    root: Path,
    *,
    allow_write: bool = True,
    allow_verify: bool = True,
    trusted: frozenset[str] = frozenset(),
    profiles: Mapping[str, CommandProfile] | None = None,
    database: SqliteDatabase | None = None,
    policy: Policy | None = None,
) -> tuple[SqliteDatabase, ExecutionGateway]:
    database = database or SqliteDatabase(tmp_path / "eis.sqlite3")
    selected_policy = policy or _policy(allow_write=allow_write, allow_verify=allow_verify)
    service = PolicyService(PolicyEngine(selected_policy), SqlitePolicyDecisionSink(database))
    gateway = ExecutionGateway(
        project_roots={"demo": root},
        trusted_projects=trusted,
        backup_dir=tmp_path / "backups",
        policy=service,
        approvals=ApprovalStore(database),
        integrity=AuditChainStore(database),
        command_profiles=profiles,
    )
    return database, gateway


def _proposal(gateway: ExecutionGateway):
    return gateway.propose_write(
        project_id="demo",
        relative_path="app.txt",
        new_content="updated\n",
        on_behalf_of="agent:eis.primary",
        summary="Update app text",
    )


def test_policy_decision_projection_and_approved_write(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)

    approval = _proposal(gateway)
    assert approval.status == "pending"
    projected = PolicyDecisionStore(database).list()
    assert len(projected) == 3
    assert PolicyDecisionStore(database).get(approval.policy_decision_id) is not None
    shown_diff = approval.diff_text
    assert "-original" in shown_diff and "+updated" in shown_diff

    approved = gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    assert approved.status == "approved"
    result = gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "updated\n"
    assert result.backup_id is not None
    assert (tmp_path / "backups" / f"{result.backup_id}.bin").read_bytes() == b"original\n"
    assert ApprovalStore(database).get(approval.approval_id).status == "consumed"  # type: ignore[union-attr]
    assert AuditChainStore(database).status().ok


def test_write_denied_without_user_policy_allow(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    _, gateway = _gateway(tmp_path, root, allow_write=False)
    with pytest.raises(ExecutionDenied, match="write denied"):
        _proposal(gateway)
    assert (root / "app.txt").read_bytes() == b"original\n"


def test_changed_target_invalidates_approved_write(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    _, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    target.write_text("changed by user\n", encoding="utf-8")
    with pytest.raises(ExecutionDenied, match="target changed"):
        gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert target.read_text(encoding="utf-8") == "changed by user\n"


def test_path_traversal_and_symlink_are_denied(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    _, gateway = _gateway(tmp_path, root)
    with pytest.raises(ExecutionDenied, match="normalized"):
        gateway.propose_write(
            project_id="demo",
            relative_path="../outside.txt",
            new_content="x",
            on_behalf_of="agent:eis.primary",
            summary="invalid",
        )
    with pytest.raises(ExecutionDenied, match="reserved"):
        gateway.propose_write(
            project_id="demo",
            relative_path=".env.local",
            new_content="x",
            on_behalf_of="agent:eis.primary",
            summary="invalid",
        )
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ExecutionDenied, match="symlink"):
        gateway.propose_write(
            project_id="demo",
            relative_path="link.txt",
            new_content="x",
            on_behalf_of="agent:eis.primary",
            summary="invalid",
        )
    assert hashlib.sha256(outside.read_bytes()).hexdigest()


def test_broken_chain_blocks_grant_and_execution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json='{}' WHERE seq=1")
    with pytest.raises(AuditIntegrityError):
        gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    assert (root / "app.txt").read_bytes() == b"original\n"


def test_approval_cannot_be_granted_with_another_digest(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    _, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    with pytest.raises(ApprovalError, match="digest"):
        gateway.grant(approval.approval_id, confirmed_digest="0" * 64)


def test_approval_expiry_blocks_grant_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1_000_000]
    monkeypatch.setattr("eis.storage.approvals._now_ms", lambda: clock[0])
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)

    approval = _proposal(gateway)
    clock[0] += 1_800_000 + 1
    with pytest.raises(ApprovalError, match="expired"):
        gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    assert ApprovalStore(database).get(approval.approval_id).status == "pending"  # type: ignore[union-attr]
    assert ApprovalStore(database).list_pending() == ()

    second = _proposal(gateway)
    gateway.grant(second.approval_id, confirmed_digest=second.request_digest)
    clock[0] += 1_800_000 + 1
    with pytest.raises(ApprovalError, match="expired"):
        gateway.execute_write(second.approval_id, confirmed_digest=second.request_digest)
    assert target.read_bytes() == b"original\n"
    assert ApprovalStore(database).get(second.approval_id).status == "approved"  # type: ignore[union-attr]


def test_approval_state_transitions_are_single_use(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)

    with pytest.raises(ApprovalError, match="not pending"):
        gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    with pytest.raises(ApprovalError, match="not pending"):
        gateway.reject(
            approval.approval_id,
            confirmed_digest=approval.request_digest,
            reason="too late",
        )

    gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    with pytest.raises(ApprovalError, match="already been used"):
        gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)

    rejected = _proposal(gateway)
    gateway.reject(
        rejected.approval_id, confirmed_digest=rejected.request_digest, reason="not needed"
    )
    with pytest.raises(ApprovalError, match="not approved"):
        gateway.execute_write(rejected.approval_id, confirmed_digest=rejected.request_digest)

    with (
        pytest.raises(sqlite3.DatabaseError, match="invalid approval status transition"),
        database.session(immediate=True) as connection,
    ):
        connection.execute(
            "UPDATE approvals SET status = 'consumed' WHERE id = ?",
            (rejected.approval_id,),
        )


def test_rejection_reason_is_not_stored_raw_in_the_audit_chain(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    reason = "operator-note-marker"
    gateway.reject(
        approval.approval_id,
        confirmed_digest=approval.request_digest,
        reason=reason,
    )

    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = 'approval_rejected'"
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert reason not in json.dumps(payload)
    assert payload["reason_sha256"] == hashlib.sha256(reason.encode()).hexdigest()
    assert ApprovalStore(database).get(approval.approval_id).decision_note == reason  # type: ignore[union-attr]


def test_policy_change_after_approval_blocks_execution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)

    _, changed = _gateway(
        tmp_path,
        root,
        database=database,
        policy=_policy(version="test-v2"),
    )
    with pytest.raises(ExecutionDenied, match="policy changed"):
        changed.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert target.read_bytes() == b"original\n"
    assert ApprovalStore(database).get(approval.approval_id).status == "approved"  # type: ignore[union-attr]

    second = _proposal(gateway)
    gateway.grant(second.approval_id, confirmed_digest=second.request_digest)
    _, changed_content = _gateway(
        tmp_path,
        root,
        database=database,
        policy=_policy(version="test-v1", allow_write=False),
    )
    with pytest.raises(ExecutionDenied, match="policy changed"):
        changed_content.execute_write(second.approval_id, confirmed_digest=second.request_digest)
    assert target.read_bytes() == b"original\n"


def test_project_root_binding_and_atomic_replace(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)

    other_root = tmp_path / "other-project"
    other_root.mkdir()
    (other_root / "app.txt").write_bytes(b"original\n")
    _, other = _gateway(tmp_path, other_root, database=database)
    with pytest.raises(ExecutionDenied, match="root changed"):
        other.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert target.read_bytes() == b"original\n"

    result = gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert result.status == "success"
    assert target.read_text(encoding="utf-8") == "updated\n"
    assert list(root.glob(".eis-write-*")) == []


def test_broken_chain_after_approval_blocks_execution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE seq = 1")

    with pytest.raises(AuditIntegrityError):
        gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert target.read_bytes() == b"original\n"
    assert ApprovalStore(database).get(approval.approval_id).status == "approved"  # type: ignore[union-attr]


def test_execution_run_and_chain_events_are_recorded(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.txt").write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    result = gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)

    with database.connect() as connection:
        run = connection.execute(
            "SELECT * FROM execution_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM audit_events ORDER BY seq"
            ).fetchall()
        ]
        completed = connection.execute(
            "SELECT payload_json FROM audit_events WHERE event_type = 'execution_completed'"
        ).fetchone()

    assert run["status"] == "success"
    assert run["approval_id"] == approval.approval_id
    assert run["backup_id"] == result.backup_id
    assert event_types.count("policy_decision") == 3
    assert event_types.count("approval_requested") == 1
    assert event_types.count("approval_granted") == 1
    assert event_types.count("approval_consumed") == 1
    assert event_types.count("execution_started") == 1
    assert event_types.count("execution_completed") == 1
    payload = json.loads(completed["payload_json"])
    assert "updated\n" not in json.dumps(payload)
    assert "original\n" not in json.dumps(payload)


def _profile_gateway(
    tmp_path: Path,
    root: Path,
    *,
    profiles: Mapping[str, CommandProfile],
    trusted: bool = True,
) -> tuple[SqliteDatabase, ExecutionGateway]:
    return _gateway(
        tmp_path,
        root,
        trusted=frozenset({"demo"}) if trusted else frozenset(),
        profiles=profiles,
    )


def test_fixed_profile_success_records_execution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profiles = {
        "ok": CommandProfile(
            profile_id="ok",
            argv=(sys.executable, "-c", "print('ok')"),
            timeout_seconds=10,
            output_cap_bytes=4096,
        )
    }
    database, gateway = _profile_gateway(tmp_path, root, profiles=profiles)
    result = gateway.run_profile("demo", "ok", principal="agent:eis.primary")

    assert result.status == "success"
    assert b"ok" in result.stdout
    with database.connect() as connection:
        run = connection.execute(
            "SELECT status, profile_id FROM execution_runs WHERE id = ?", (result.run_id,)
        ).fetchone()
        started = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'execution_started'"
        ).fetchone()[0]
        completed = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'execution_completed'"
        ).fetchone()[0]
    assert run["status"] == "success"
    assert run["profile_id"] == "ok"
    assert started == 1
    assert completed == 1


def test_profile_output_cap_truncates_and_terminates(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profiles = {
        "noisy": CommandProfile(
            profile_id="noisy",
            argv=(
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 200000); sys.stdout.flush()",
            ),
            timeout_seconds=10,
            output_cap_bytes=1024,
        )
    }
    _, gateway = _profile_gateway(tmp_path, root, profiles=profiles)
    result = gateway.run_profile("demo", "noisy", principal="agent:eis.primary")

    assert result.truncated is True
    assert result.status == "failed"
    assert result.error_code == "output_truncated"
    assert len(result.stdout) <= 1024


def test_profile_timeout_terminates_the_process(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profiles = {
        "slow": CommandProfile(
            profile_id="slow",
            argv=(sys.executable, "-c", "import time; time.sleep(20)"),
            timeout_seconds=1,
            output_cap_bytes=1024,
        )
    }
    _, gateway = _profile_gateway(tmp_path, root, profiles=profiles)
    started = time.monotonic()
    result = gateway.run_profile("demo", "slow", principal="agent:eis.primary")

    assert time.monotonic() - started < 10
    assert result.timed_out is True
    assert result.status == "failed"
    assert result.error_code == "timeout"


def test_unavailable_and_refused_profiles_are_recorded(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    profiles = {
        "missing-executable": CommandProfile(
            profile_id="missing-executable",
            argv=(str(tmp_path / "does-not-exist.exe"),),
        ),
        "t2": CommandProfile(
            profile_id="t2",
            argv=(sys.executable, "-c", "print('t2')"),
            minimum_tier="T2",
        ),
        "t1": CommandProfile(
            profile_id="t1",
            argv=(sys.executable, "-c", "print('t1')"),
        ),
    }
    database, gateway = _profile_gateway(tmp_path, root, profiles=profiles)

    assert (
        gateway.run_profile("demo", "absent", principal="agent:eis.primary").status == "unavailable"
    )
    assert (
        gateway.run_profile("demo", "missing-executable", principal="agent:eis.primary").error_code
        == "executable_unavailable"
    )
    assert (
        gateway.run_profile("demo", "t2", principal="agent:eis.primary").error_code
        == "isolation_unavailable"
    )
    _, untrusted = _gateway(
        tmp_path,
        root,
        database=database,
        trusted=frozenset(),
        profiles=profiles,
    )
    assert (
        untrusted.run_profile("demo", "t1", principal="agent:eis.primary").error_code
        == "isolation_unavailable"
    )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 4


def test_approval_digest_binds_policy_decision_and_diff() -> None:
    base = {
        "on_behalf_of": "agent:eis.primary",
        "resource": "path:demo/app.txt",
        "project_root_hash": "a" * 64,
        "expected_sha256": "b" * 64,
        "new_sha256": "c" * 64,
        "policy_decision_id": "pol_one",
        "policy_version": "test-v1",
        "expires_at_ms": 1_800_000,
        "summary": "Update app",
        "diff_text": "-old\n+new\n",
    }
    original = approval_digest(**base)
    changed_policy = approval_digest(**{**base, "policy_decision_id": "pol_two"})
    changed_diff = approval_digest(**{**base, "diff_text": "-old\n+other\n"})
    changed_summary = approval_digest(**{**base, "summary": "Different summary"})
    task_bound = approval_digest(**base, task_id="tsk_one")
    another_task = approval_digest(**base, task_id="tsk_two")
    assert (
        len({original, changed_policy, changed_diff, changed_summary, task_bound, another_task})
        == 6
    )


def test_tampered_approval_binding_is_rejected_at_consumption(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    database, gateway = _gateway(tmp_path, root)
    approval = _proposal(gateway)
    gateway.grant(approval.approval_id, confirmed_digest=approval.request_digest)

    with database.connect() as connection:
        connection.execute("DROP TRIGGER approvals_binding_immutable")
        connection.execute(
            "UPDATE approvals SET diff_text = diff_text || 'tampered' WHERE id = ?",
            (approval.approval_id,),
        )
    with pytest.raises(ApprovalError, match="stored approval binding is invalid"):
        gateway.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    assert target.read_bytes() == b"original\n"
    assert ApprovalStore(database).get(approval.approval_id).status == "approved"  # type: ignore[union-attr]


def test_cli_approval_propose_grant_execute_and_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.txt"
    target.write_bytes(b"original\n")
    content = tmp_path / "new.txt"
    content.write_text("updated\n", encoding="utf-8")
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(_policy_document()), encoding="utf-8")

    proposed = runner.invoke(
        app,
        [
            "approval",
            "propose-write",
            str(root),
            str(content),
            "--project-id",
            "demo",
            "--path",
            "app.txt",
            "--policy-file",
            str(policy_file),
            "--summary",
            "Update app",
        ],
    )
    assert proposed.exit_code == 0, proposed.output
    approval_id = proposed.output.split("approval_id=")[1].split()[0]
    digest = proposed.output.split("digest=")[1].split()[0]

    shown = runner.invoke(app, ["approval", "show", approval_id])
    assert shown.exit_code == 0, shown.output
    assert "-original" in shown.output and "+updated" in shown.output
    assert digest in shown.output

    listed = runner.invoke(app, ["approval", "list"])
    assert listed.exit_code == 0, listed.output
    assert approval_id in listed.output

    granted = runner.invoke(
        app, ["approval", "grant", approval_id, "--digest", digest], input="y\n"
    )
    assert granted.exit_code == 0, granted.output
    assert "status=approved" in granted.output

    wrong_root = tmp_path / "wrong-project"
    wrong_root.mkdir()
    (wrong_root / "app.txt").write_bytes(b"original\n")
    wrong = runner.invoke(
        app,
        [
            "approval",
            "execute",
            approval_id,
            str(wrong_root),
            "--digest",
            digest,
            "--policy-file",
            str(policy_file),
        ],
    )
    assert wrong.exit_code == 1
    assert "root changed" in wrong.output
    assert target.read_text(encoding="utf-8") == "original\n"

    executed = runner.invoke(
        app,
        [
            "approval",
            "execute",
            approval_id,
            str(root),
            "--digest",
            digest,
            "--policy-file",
            str(policy_file),
        ],
    )
    assert executed.exit_code == 0, executed.output
    assert "status=success" in executed.output
    assert target.read_text(encoding="utf-8") == "updated\n"

    second_content = tmp_path / "second.txt"
    second_content.write_text("second\n", encoding="utf-8")
    second = runner.invoke(
        app,
        [
            "approval",
            "propose-write",
            str(root),
            str(second_content),
            "--project-id",
            "demo",
            "--path",
            "app.txt",
            "--policy-file",
            str(policy_file),
            "--summary",
            "Second change",
        ],
    )
    assert second.exit_code == 0, second.output
    second_id = second.output.split("approval_id=")[1].split()[0]
    second_digest = second.output.split("digest=")[1].split()[0]
    rejected = runner.invoke(
        app,
        [
            "approval",
            "reject",
            second_id,
            "--digest",
            second_digest,
            "--reason",
            "operator declined",
        ],
    )
    assert rejected.exit_code == 0, rejected.output
    assert "status=rejected" in rejected.output
    shown_rejected = runner.invoke(app, ["approval", "show", second_id])
    assert "decision_note=operator declined" in shown_rejected.output
    blocked = runner.invoke(
        app,
        [
            "approval",
            "execute",
            second_id,
            str(root),
            "--digest",
            second_digest,
            "--policy-file",
            str(policy_file),
        ],
    )
    assert blocked.exit_code == 1

    decisions = runner.invoke(app, ["policy-decisions", "--limit", "20"])
    assert decisions.exit_code == 0, decisions.output
    assert "effect=allow" in decisions.output
