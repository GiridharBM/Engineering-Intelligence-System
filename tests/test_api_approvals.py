# SPDX-License-Identifier: Apache-2.0

"""Route-level tests for digest-bound approval decision/execution routes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eis.api import create_app
from eis.core.execution import ExecutionGateway
from eis.core.model_plan import WRITE_PROPOSAL_TOOL, PlanStep
from eis.core.policy_service import PolicyService
from eis.policy import PolicyEngine, load_policy
from eis.storage.approvals import ApprovalStore
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

TOKEN = "T" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NEW_CONTENT = "new-approved-content-marker"


def _write_policy(path: Path, *, allow_write: bool) -> Path:
    rules: list[dict[str, str]] = [
        {
            "id": "agent-propose",
            "principal": "agent:eis.primary",
            "action": "project.propose",
            "resource": "project:demo",
            "effect": "allow",
        },
        {
            "id": "agent-read",
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "path:demo/app.py",
            "effect": "allow",
        },
        {
            "id": "user-read",
            "principal": "user",
            "action": "project.read",
            "resource": "path:demo/app.py",
            "effect": "allow",
        },
    ]
    if allow_write:
        rules.append(
            {
                "id": "user-write",
                "principal": "user",
                "action": "project.write",
                "resource": "path:demo/app.py",
                "effect": "allow",
            }
        )
    path.write_text(
        json.dumps({"version": "route-test-v1", "default": "deny", "rules": rules}),
        encoding="utf-8",
    )
    return path


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    allow_write: bool = True,
) -> tuple[TestClient, SqliteDatabase, Path, str, str]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("old-content\n", encoding="utf-8")
    data = tmp_path / "eis"
    db_dir = data / "db"
    db_dir.mkdir(parents=True)
    policy_path = _write_policy(tmp_path / "policy.json", allow_write=allow_write)
    monkeypatch.setenv("EIS_DATA_DIR", str(data))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(data))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")
    monkeypatch.delenv("EIS_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("EIS_POLICY_FILE", str(policy_path))

    database = SqliteDatabase(db_dir / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    roots = ProjectRootStore(database)
    roots.register("demo", root, actor="test")
    audit = AuditChainStore(database)
    approvals = ApprovalStore(database)
    policy = PolicyService(
        PolicyEngine(load_policy(policy_path)), SqlitePolicyDecisionSink(database)
    )
    session = sessions.create_session(title="route test", project_id="demo", actor="test")
    task = sessions.create_task(
        session_id=session.session_id,
        goal="update app",
        goal_hash=hashlib.sha256(b"update app").hexdigest(),
        principal="agent:eis.primary",
        policy_version=policy.policy_version,
        runtime_version="test",
        actor="test",
    )
    task = sessions.transition_task(task.task_id, expected_state="created", new_state="running")
    gateway = ExecutionGateway(
        project_roots={"demo": root},
        trusted_projects=frozenset(),
        backup_dir=data / "backups",
        policy=policy,
        approvals=approvals,
        integrity=audit,
    )
    approval = gateway.propose_write(
        project_id="demo",
        task_id=task.task_id,
        relative_path="app.py",
        new_content=NEW_CONTENT,
        on_behalf_of="agent:eis.primary",
        summary="Update app",
    )
    plan_store = PlanStore(database)
    plan = plan_store.create_plan(
        task_id=task.task_id,
        project_id="demo",
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        plan_hash="b" * 64,
        steps=[
            PlanStep(
                id="propose",
                tool=WRITE_PROPOSAL_TOOL,
                arguments={"path": "app.py", "content": NEW_CONTENT, "summary": "Update app"},
                depends_on=(),
            )
        ],
        actor="test",
    )
    plan = plan_store.transition_plan(plan.plan_id, expected_state="created", new_state="running")
    steps = plan_store.list_steps(plan.plan_id)
    sessions.block_plan_task_for_approval(
        task.task_id, plan.plan_id, steps[0].step_id, approval.approval_id
    )
    app = create_app(token_provider=lambda: TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1")
    return (
        client,
        database,
        root,
        task.task_id,
        approval.approval_id + ":" + approval.request_digest,
    )


def test_approval_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    client, _, _, _, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    body = {"decision": "approve", "digest": digest}
    assert client.post(f"/api/v1/approvals/{approval_id}/decision", json=body).status_code == 401
    assert (
        client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json=body,
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )
    assert (
        client.post(f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/v1/approvals/{approval_id}/execute",
            json={"digest": digest},
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )


def test_bad_digest_is_rejected_and_changes_nothing(tmp_path: Path, monkeypatch) -> None:
    client, database, _, _, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    bad = {"decision": "approve", "digest": "f" * 64}
    response = client.post(f"/api/v1/approvals/{approval_id}/decision", json=bad, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"] == "approval_decision_rejected"
    # Schema-invalid digest is a 422, not a silent pass.
    assert (
        client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"decision": "approve", "digest": "not-a-digest"},
            headers=AUTH,
        ).status_code
        == 422
    )
    with database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()[0]
        runs = connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
    assert status == "pending"
    assert runs == 0


def test_default_deny_policy_blocks_execution_without_a_run(tmp_path: Path, monkeypatch) -> None:
    client, database, _, _, token_and_digest = _prepare(tmp_path, monkeypatch, allow_write=True)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    approved = client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approve", "digest": digest},
        headers=AUTH,
    )
    assert approved.status_code == 200, approved.text
    # Even an approved, digest-bound write is rechecked against a deny-by-default policy.
    deny = _write_policy(tmp_path / "deny.json", allow_write=False)
    monkeypatch.setenv("EIS_POLICY_FILE", str(deny))
    response = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}, headers=AUTH
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "approval_execution_rejected"
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
        state = connection.execute(
            "SELECT state FROM tasks WHERE id = ?", (approved.json()["task_id"],)
        ).fetchone()
    assert runs == 0
    assert state[0] != "completed"


def test_rejected_approval_blocks_task_and_cannot_execute(tmp_path: Path, monkeypatch) -> None:
    client, database, root, task_id, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    rejected = client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "reject", "digest": digest, "note": "not now"},
        headers=AUTH,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    # No write happened; task/plan/step are closed.
    assert (root / "app.py").read_text(encoding="utf-8") == "old-content\n"
    with database.connect() as connection:
        task_state = connection.execute(
            "SELECT state FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()[0]
        plan_state = connection.execute(
            "SELECT state FROM task_plans WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
        step_status = connection.execute(
            "SELECT status FROM task_plan_steps WHERE tool = ?", (WRITE_PROPOSAL_TOOL,)
        ).fetchone()[0]
    assert task_state == "failed"
    assert plan_state == "failed"
    assert step_status == "failed"
    response = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}, headers=AUTH
    )
    assert response.status_code == 409


def test_approve_execute_completes_task_and_plan_with_durable_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    client, database, root, task_id, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    approved = client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approve", "digest": digest},
        headers=AUTH,
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}, headers=AUTH
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["status"] == "success"
    assert body["task_id"] == task_id
    assert body["task_state"] == "completed"
    assert body["run_id"]
    assert (root / "app.py").read_text(encoding="utf-8") == NEW_CONTENT
    with database.connect() as connection:
        task = connection.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]
        plan = connection.execute(
            "SELECT state FROM task_plans WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
        step = connection.execute(
            "SELECT status FROM task_plan_steps WHERE tool = ?", (WRITE_PROPOSAL_TOOL,)
        ).fetchone()[0]
        run = connection.execute(
            "SELECT status, output_sha256 FROM execution_runs WHERE id = ?", (body["run_id"],)
        ).fetchone()
        event = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'task_approved_write_completed' "
            "AND decision = 'completed'"
        ).fetchone()[0]
    assert (task, plan, step) == ("completed", "completed", "completed")
    assert run["status"] == "success" and run["output_sha256"]
    assert event == 1


def test_cancellation_races_with_approval_execution(tmp_path: Path, monkeypatch) -> None:
    client, database, root, task_id, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    approvals = ApprovalStore(database)
    approvals.decide(approval_id, grant=True, confirmed_digest=digest)
    policy_path = Path(__import__("os").environ["EIS_POLICY_FILE"])
    policy = PolicyService(
        PolicyEngine(load_policy(policy_path)), SqlitePolicyDecisionSink(database)
    )
    execution = ExecutionGateway(
        project_roots={"demo": root},
        trusted_projects=frozenset(),
        backup_dir=tmp_path / "backups2",
        policy=policy,
        approvals=approvals,
        integrity=AuditChainStore(database),
    )
    result = execution.execute_write(approval_id, confirmed_digest=digest)
    assert result.status == "success"
    sessions = SessionTaskStore(database)
    # Successful execution evidence exists; cancellation is refused, nothing is undone.
    with pytest.raises(RuntimeError, match="successful_execution_evidence_exists"):
        sessions.cancel_task(task_id, actor="user")
    assert sessions.get_task(task_id).state == "blocked"  # type: ignore[union-attr]
    assert (root / "app.py").read_text(encoding="utf-8") == NEW_CONTENT
    completed = sessions.complete_task_after_approval(task_id, approval_id)
    assert completed.state == "completed"
    with pytest.raises(RuntimeError, match="terminal"):
        sessions.cancel_task(task_id, actor="user")


def test_retry_after_execution_recovers_without_duplicate_write(
    tmp_path: Path, monkeypatch
) -> None:
    client, database, root, task_id, token_and_digest = _prepare(tmp_path, monkeypatch)
    approval_id, digest = token_and_digest.rsplit(":", 1)
    client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approve", "digest": digest},
        headers=AUTH,
    )
    first = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}, headers=AUTH
    )
    assert first.status_code == 200, first.text
    # Retry returns the same durable evidence; no second write or execution row.
    second = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": digest}, headers=AUTH
    )
    assert second.status_code == 200, second.text
    assert second.json()["task_state"] == "completed"
    assert second.json()["run_id"] == first.json()["run_id"]
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
    assert runs == 1
    # Wrong digest on retry is still rejected and cannot re-trigger anything.
    bad = client.post(
        f"/api/v1/approvals/{approval_id}/execute", json={"digest": "f" * 64}, headers=AUTH
    )
    assert bad.status_code == 409
    with database.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
    assert runs == 1
