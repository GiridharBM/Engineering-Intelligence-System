# SPDX-License-Identifier: Apache-2.0

"""Cancellation and human-review continuation for durable plans."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from eis.adapters.models.base import CompletionRequest, CompletionResponse, TokenUsage
from eis.config import ConsentRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.core.policy_service import PolicyService
from eis.core.project_tools import ReadOnlyProjectTools, ToolCall
from eis.core.runtime import RuntimeStateError, SessionTaskRuntime
from eis.policy import Policy, PolicyEngine
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

ROOT_ID = "demo"
GOAL = "private-goal-marker"
FILE_CONTENT = "private-file-content-marker"


def _plan_json(steps: list[dict[str, object]]) -> str:
    return json.dumps({"kind": "plan", "steps": steps}, separators=(",", ":"))


REVIEW_PLAN = _plan_json(
    [
        {
            "id": "read",
            "tool": "project.read_text",
            "arguments": {"path": "app.py"},
            "depends_on": [],
        },
        {"id": "review", "tool": "human_review", "arguments": {}, "depends_on": ["read"]},
        {"id": "after", "tool": "project.list", "arguments": {}, "depends_on": ["review"]},
    ]
)


class ScriptedProvider:
    name = "openai"
    model = "test-model"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            text=self._text,
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 1, 1),
        )


@dataclass
class Fx:
    runtime: SessionTaskRuntime
    sessions: SessionTaskStore
    plans: PlanStore
    database: SqliteDatabase
    provider: ScriptedProvider
    tools: ReadOnlyProjectTools
    audit: AuditChainStore


def _policy(*, allow_cancel: bool = True, allow_run: bool = True) -> Policy:
    rules: list[dict[str, str]] = [
        {
            "id": "read",
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "path:demo/app.py",
            "effect": "allow",
        },
        {
            "id": "list",
            "principal": "agent:eis.primary",
            "action": "project.list",
            "resource": "global",
            "effect": "allow",
        },
    ]
    if allow_run:
        rules.append(
            {
                "id": "run",
                "principal": "agent:eis.primary",
                "action": "task.run",
                "resource": "project:demo",
                "effect": "allow",
            }
        )
    if allow_cancel:
        rules.append(
            {
                "id": "cancel",
                "principal": "agent:eis.primary",
                "action": "task.cancel",
                "resource": "project:demo",
                "effect": "allow",
            }
        )
    return Policy.from_dict({"version": "cancel-review-v1", "default": "deny", "rules": rules})


def _fx(tmp_path: Path, *, allow_cancel: bool = True, allow_run: bool = True) -> Fx:
    root = tmp_path / "project"
    root.mkdir(parents=True)
    (root / "app.py").write_text(FILE_CONTENT, encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    plans = PlanStore(database)
    policy = PolicyService(
        PolicyEngine(_policy(allow_cancel=allow_cancel, allow_run=allow_run)),
        SqlitePolicyDecisionSink(database),
    )
    roots = ProjectRootStore(database)
    roots.register(ROOT_ID, root, actor="test")
    audit = AuditChainStore(database)
    tools = ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=audit)
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),
            purpose="test",
        )
    )  # type: ignore[arg-type]
    provider = ScriptedProvider(REVIEW_PLAN)
    runtime = SessionTaskRuntime(
        sessions,
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=tools,
        plans=plans,
    )
    return Fx(runtime, sessions, plans, database, provider, tools, audit)


def _blocked_review(fx: Fx, monkeypatch: pytest.MonkeyPatch):
    session = fx.runtime.create_session(title="review", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, GOAL)
    executed: list[str] = []
    original = fx.tools._execute

    def spy(call: ToolCall) -> object:
        executed.append(call.tool)
        return original(call)

    monkeypatch.setattr(fx.tools, "_execute", spy)
    outcome = asyncio.run(fx.runtime.run_plan(task.task_id))
    assert outcome.task.state == "blocked"
    assert outcome.task.stop_reason == "human_review_required"
    return session, task, executed


def test_human_review_continuation_resumes_without_repeating_or_granting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fx(tmp_path)
    session, task, executed = _blocked_review(fx, monkeypatch)
    assert executed == ["project.read_text"]  # read before review happened once

    completed = asyncio.run(fx.runtime.continue_plan_review(task.task_id, "review", actor="user"))
    assert completed.task.state == "completed"
    assert completed.task.stop_reason == "plan_completed"
    assert executed == ["project.read_text", "project.list"]  # no duplicate read; `after` runs once
    assert fx.provider.calls == 1  # no new model completion
    assert not any(name in json.dumps(executed) for name in ("write", "approval"))
    with fx.database.connect() as connection:
        event = connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type = 'plan_step_state_changed' AND decision = 'completed' "
            "AND subject_id IN (SELECT id FROM task_plan_steps WHERE tool = 'human_review')"
        ).fetchone()[0]
    assert event == 1
    # Content-free audit; no model text or file content persisted in the new plan/audit state.
    with fx.database.connect() as connection:
        audit_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
        )
        plan_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM task_plans")]
            + [dict(row) for row in connection.execute("SELECT * FROM task_plan_steps")]
        )
    assert FILE_CONTENT not in fx.database.path.read_bytes().decode("utf-8", errors="replace")
    for marker in (GOAL, FILE_CONTENT):
        assert marker not in audit_blob and marker not in plan_blob
    assert fx.audit.status().ok


def test_human_review_malformed_input_and_policy_denial_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fx(tmp_path)
    _, task, _ = _blocked_review(fx, monkeypatch)
    for bad in ("", "bad step!", "x" * 25, "review\n", "../review", "review;exec"):
        with pytest.raises(RuntimeStateError):
            asyncio.run(fx.runtime.continue_plan_review(task.task_id, bad, actor="user"))
    with pytest.raises(RuntimeStateError):
        asyncio.run(fx.runtime.continue_plan_review(task.task_id, "missing", actor="user"))
    # unchanged after malformed attempts
    assert fx.sessions.get_task(task.task_id).state == "blocked"  # type: ignore[union-attr]

    denied = _fx(tmp_path / "denied", allow_run=True)
    _, task2, _ = _blocked_review(denied, monkeypatch)
    # Blocked review exists, but `task.run` is no longer allowed at continuation time.
    monkeypatch.setattr(
        denied.runtime,
        "_policy",
        PolicyService(
            PolicyEngine(_policy(allow_run=False)), SqlitePolicyDecisionSink(denied.database)
        ),
    )
    with pytest.raises(RuntimeStateError, match="policy"):
        asyncio.run(denied.runtime.continue_plan_review(task2.task_id, "review", actor="user"))
    assert denied.sessions.get_task(task2.task_id).state == "blocked"  # type: ignore[union-attr]


def test_human_review_audit_failure_is_fail_closed_and_restart_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fx(tmp_path)
    _, task, _ = _blocked_review(fx, monkeypatch)
    monkeypatch.setattr(
        fx.sessions._chain,
        "append_in",
        lambda *a, **k: (_ for _ in ()).throw(AuditIntegrityError("audit unavailable")),
    )
    with pytest.raises(AuditIntegrityError):
        asyncio.run(fx.runtime.continue_plan_review(task.task_id, "review", actor="user"))
    # Failure occurred before any transition; a fresh store sees blocked state.
    restarted = SessionTaskStore(fx.database)
    assert restarted.get_task(task.task_id).state == "blocked"  # type: ignore[union-attr]
    assert PlanStore(fx.database).get_plan_for_task(task.task_id).state == "blocked"  # type: ignore[union-attr]
    # Duplicate attempt after restart still targets the same blocked step; nothing extra executed.
    monkeypatch.undo()
    outcome = asyncio.run(fx.runtime.continue_plan_review(task.task_id, "review", actor="user"))
    assert outcome.task.state == "completed"


def test_cancel_task_atomic_semantics_and_policy_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fx(tmp_path)
    session, task, _ = _blocked_review(fx, monkeypatch)
    cancelled = fx.runtime.cancel_task(task.task_id, actor="user")
    assert cancelled.task.state == "cancelled"
    assert cancelled.plan is not None and cancelled.plan.state == "cancelled"
    statuses = {(s.tool, s.step_ref, s.status, s.error_code) for s in cancelled.steps}
    assert ("project.read_text", "read", "completed", None) in statuses
    assert ("human_review", "review", "skipped", "cancelled") in statuses
    assert ("project.list", "after", "skipped", "cancelled") in statuses
    # Cancellation never rewinds the completed read; no write; nothing auto-resumes.
    with pytest.raises(RuntimeStateError, match="terminal"):
        fx.runtime.cancel_task(task.task_id, actor="user")
    with pytest.raises(RuntimeStateError, match="not waiting"):
        asyncio.run(fx.runtime.continue_plan_review(task.task_id, "review", actor="user"))
    asyncio.run(fx.runtime.resume_plan(task.task_id))  # no re-cancel, no resume
    assert fx.sessions.get_task(task.task_id).state == "cancelled"  # type: ignore[union-attr]

    denied = _fx(tmp_path / "denied", allow_cancel=False)
    _, task2, _ = _blocked_review(denied, monkeypatch)
    with pytest.raises(RuntimeStateError, match="policy_denied"):
        denied.runtime.cancel_task(task2.task_id, actor="user")
    assert denied.sessions.get_task(task2.task_id).state == "blocked"  # type: ignore[union-attr]


def test_api_cancel_and_review_continuation_require_auth_strict_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from eis.api import create_app

    fx = _fx(tmp_path / "review")
    _, task, _ = _blocked_review(fx, monkeypatch)
    app = create_app(token_provider=lambda: "T" * 43, runtime_factory=lambda: fx.runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    auth = {"Authorization": "Bearer " + "T" * 43}
    assert (
        client.post(
            f"/api/v1/tasks/{task.task_id}/review/continue", json={"step_ref": "review"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task.task_id}/review/continue",
            json={"step_ref": "bad step!"},
            headers=auth,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/tasks/{task.task_id}/review/continue",
            json={"step_ref": "review", "grant": True},
            headers=auth,
        ).status_code
        == 422
    )
    ok = client.post(
        f"/api/v1/tasks/{task.task_id}/review/continue", json={"step_ref": "review"}, headers=auth
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "completed"

    fx2 = _fx(tmp_path / "cancel")
    _, task2, _ = _blocked_review(fx2, monkeypatch)
    app2 = create_app(token_provider=lambda: "T" * 43, runtime_factory=lambda: fx2.runtime)
    client2 = TestClient(app2, base_url="http://127.0.0.1")
    assert client2.post(f"/api/v1/tasks/{task2.task_id}/cancel").status_code == 401
    cancelled = client2.post(f"/api/v1/tasks/{task2.task_id}/cancel", headers=auth)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"


def test_cli_cancel_and_continue_review_use_same_fail_closed_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from eis.cli import app

    runner = CliRunner()
    fx = _fx(tmp_path / "review")
    _, task, _ = _blocked_review(fx, monkeypatch)
    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx.runtime)
    shown = runner.invoke(app, ["plan", "continue-review", task.task_id, "review"])
    assert shown.exit_code == 0, shown.output
    assert "state=completed" in shown.output
    assert GOAL not in shown.output and FILE_CONTENT not in shown.output

    fx2 = _fx(tmp_path / "cancel")
    _, task2, _ = _blocked_review(fx2, monkeypatch)
    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx2.runtime)
    cancelled = runner.invoke(app, ["task", "cancel", task2.task_id])
    assert cancelled.exit_code == 0, cancelled.output
    assert "state=cancelled" in cancelled.output
    assert GOAL not in cancelled.output


def test_cancellation_on_created_task_and_audit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _fx(tmp_path)
    session = fx.runtime.create_session(title="x", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, GOAL)
    cancelled = fx.runtime.cancel_task(task.task_id, actor="user")
    assert cancelled.task.state == "cancelled"

    fx2 = _fx(tmp_path / "audit")
    session2 = fx2.runtime.create_session(title="x", project_id=ROOT_ID)
    task2 = fx2.runtime.create_task(session2.session_id, GOAL)
    monkeypatch.setattr(
        fx2.audit, "append_in", lambda *a, **k: (_ for _ in ()).throw(AuditIntegrityError("x"))
    )
    # cancel_task uses its own chain instance; patch the store's chain.
    monkeypatch.setattr(
        fx2.sessions._chain,
        "append_in",
        lambda *a, **k: (_ for _ in ()).throw(AuditIntegrityError("x")),
    )
    with pytest.raises(AuditIntegrityError):
        fx2.runtime.cancel_task(task2.task_id, actor="user")
    assert fx2.sessions.get_task(task2.task_id).state == "created"  # type: ignore[union-attr]
