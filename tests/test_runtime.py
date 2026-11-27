# SPDX-License-Identifier: Apache-2.0

"""Durable session/task runtime, model-gateway and privacy tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.adapters.models.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.cli import _proposal_preview, app
from eis.config import ConsentRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.core.policy_service import PolicyService
from eis.core.runtime import RuntimeStateError, SessionTaskRuntime
from eis.policy import Policy, PolicyEngine
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.sessions import SessionTaskStore

FAKE_KEY = "test-only-secret-value"
PROVIDER = "openai"
MODEL = "test-model"
ANSWER_RESPONSE = json.dumps(
    {"kind": "answer", "content": "response-text-marker"},
    sort_keys=True,
    separators=(",", ":"),
)
runner = CliRunner()


class FakeProvider:
    name = PROVIDER

    def __init__(self) -> None:
        self.model = MODEL
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            text=ANSWER_RESPONSE,
            model=MODEL,
            provider=self.name,
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
            provider_request_id="req_test",
        )


class FailingProvider(FakeProvider):
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        raise ProviderError("provider unavailable")


def _policy(*, allow_task: bool = True, version: str = "test-v1") -> Policy:
    rules = []
    if allow_task:
        rules.append(
            {
                "id": "allow-task",
                "principal": "agent:eis.primary",
                "action": "task.run",
                "resource": "project:demo",
                "effect": "allow",
            }
        )
    return Policy.from_dict({"version": version, "default": "deny", "rules": rules})


def _setup(
    tmp_path: Path,
    *,
    allow_task: bool = True,
    grant_consent: bool = True,
) -> tuple[SqliteDatabase, SessionTaskStore, SessionTaskRuntime, FakeProvider, ConsentStore]:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    store = SessionTaskStore(database)
    policy = PolicyService(
        PolicyEngine(_policy(allow_task=allow_task)),
        SqlitePolicyDecisionSink(database),
    )
    consent = ConsentStore(database)
    if grant_consent:
        consent.grant(
            ConsentRecord(
                consent_id="cns_runtime",
                provider="openai",
                data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
                purpose="runtime tests",
            )
        )
    provider = FakeProvider()
    gateway = ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database))
    runtime = SessionTaskRuntime(store, policy, gateway)
    return database, store, runtime, provider, consent


def test_session_task_lifecycle_and_chained_state(tmp_path: Path) -> None:
    database, store, runtime, provider, _ = _setup(tmp_path)
    session = runtime.create_session(title="Demo", project_id="demo")
    task = runtime.create_task(session.session_id, "goal-marker")
    assert task.state == "created"
    assert task.goal_hash == hashlib.sha256(b"goal-marker").hexdigest()

    outcome = asyncio.run(runtime.run_task(task.task_id))
    assert outcome.task.state == "blocked"
    assert outcome.task.stop_reason == "human_review_required"
    assert outcome.proposal_text == "response-text-marker"
    assert provider.calls == 1
    assert outcome.task.request_hash is not None
    assert outcome.task.response_hash == hashlib.sha256(ANSWER_RESPONSE.encode()).hexdigest()

    stored = store.get_task(task.task_id)
    assert stored is not None
    assert stored.state == "blocked"
    assert stored.finish_reason == "stop"
    assert stored.usage == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "completions": 1,
        "tool_calls": 0,
    }
    assert store.get_session(session.session_id).status == "idle"  # type: ignore[union-attr]

    resumed = runtime.resume_session(session.session_id)
    assert resumed.status == "active"
    closed = runtime.close_session(session.session_id)
    assert closed.status == "closed"
    with pytest.raises(RuntimeStateError, match="closed"):
        runtime.create_task(session.session_id, "another goal")

    with database.connect() as connection:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM audit_events ORDER BY seq"
            ).fetchall()
        ]
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
    assert "session_created" in event_types
    assert "task_created" in event_types
    assert event_types.count("task_state_changed") == 2
    assert "model_call" in event_types
    assert AuditChainStore(database).status().ok is True

    with (
        pytest.raises(sqlite3.DatabaseError, match="invalid session status transition"),
        database.session(immediate=True) as connection,
    ):
        connection.execute(
            "UPDATE sessions SET status = 'active' WHERE id = ?", (session.session_id,)
        )

    with (
        pytest.raises(sqlite3.DatabaseError, match="invalid task state transition"),
        database.session(immediate=True) as connection,
    ):
        connection.execute("UPDATE tasks SET state = 'completed' WHERE id = ?", (task.task_id,))


def test_restart_resumes_a_created_task(tmp_path: Path) -> None:
    database, store, first, provider, _ = _setup(tmp_path)
    session = first.create_session(title="Restart", project_id="demo")
    task = first.create_task(session.session_id, "resume-marker")
    assert provider.calls == 0

    consent = ConsentStore(database)
    policy = PolicyService(
        PolicyEngine(_policy()),
        SqlitePolicyDecisionSink(database),
    )
    second = SessionTaskRuntime(
        SessionTaskStore(database),
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
    )
    outcome = asyncio.run(second.resume_task(task.task_id))
    assert outcome.task.state == "blocked"
    assert outcome.proposal_text == "response-text-marker"
    assert provider.calls == 1
    assert store.get_task(task.task_id).state == "blocked"  # type: ignore[union-attr]


def test_interrupted_running_task_fails_closed_without_a_second_model_call(
    tmp_path: Path,
) -> None:
    _, store, runtime, provider, _ = _setup(tmp_path)
    session = runtime.create_session(title="Interrupted", project_id="demo")
    task = runtime.create_task(session.session_id, "interrupted-goal")
    store.transition_task(
        task.task_id,
        expected_state="created",
        new_state="running",
        session_status="active",
    )

    outcome = asyncio.run(runtime.resume_task(task.task_id))
    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "interrupted"
    assert provider.calls == 0


def test_interrupted_task_with_closed_session_still_fails_closed(tmp_path: Path) -> None:
    _, store, runtime, provider, _ = _setup(tmp_path)
    session = runtime.create_session(title="Closed interrupted", project_id="demo")
    task = runtime.create_task(session.session_id, "closed-goal")
    store.transition_task(
        task.task_id,
        expected_state="created",
        new_state="running",
        session_status="active",
    )
    runtime.close_session(session.session_id)

    outcome = asyncio.run(runtime.resume_task(task.task_id))
    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "interrupted"
    assert store.get_session(session.session_id).status == "closed"  # type: ignore[union-attr]
    assert provider.calls == 0


def test_provider_failure_transitions_task_without_output(tmp_path: Path) -> None:
    database, store, runtime, _, _ = _setup(tmp_path)
    session = runtime.create_session(title="Provider failure", project_id="demo")
    task = runtime.create_task(session.session_id, "provider-goal")
    failing = FailingProvider()
    failing_runtime = SessionTaskRuntime(
        store,
        PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(database)),
        ModelGateway(
            failing,
            ConsentGate(lookup=ConsentStore(database)),
            SqliteAuditSink(database),
        ),
    )

    outcome = asyncio.run(failing_runtime.run_task(task.task_id))
    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "provider_error"
    assert outcome.proposal_text is None
    assert failing.calls == 1
    stored = store.get_task(task.task_id)
    assert stored.response_hash is None  # type: ignore[union-attr]
    with database.connect() as connection:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM model_calls ORDER BY rowid"
            ).fetchall()
        ]
    assert statuses == ["provider_error"]


def test_corrupted_chain_rolls_back_new_session_and_task(tmp_path: Path) -> None:
    database, store, runtime, _, _ = _setup(tmp_path)
    session = runtime.create_session(title="Atomic", project_id="demo")
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE seq = 1")

    with pytest.raises(AuditIntegrityError):
        runtime.create_session(title="Should roll back")
    with pytest.raises(AuditIntegrityError):
        runtime.create_task(session.session_id, "should roll back")
    assert len(store.list_sessions()) == 1
    assert store.list_tasks(session.session_id) == ()


def test_policy_denial_blocks_model_call_and_is_chained(tmp_path: Path) -> None:
    database, store, runtime, provider, _ = _setup(tmp_path, allow_task=False)
    session = runtime.create_session(title="Denied", project_id="demo")
    task = runtime.create_task(session.session_id, "denied-goal")
    outcome = asyncio.run(runtime.run_task(task.task_id))

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "policy_denied"
    assert outcome.proposal_text is None
    assert provider.calls == 0
    with database.connect() as connection:
        decision = connection.execute(
            "SELECT decision, payload_json FROM audit_events WHERE event_type = 'policy_decision'"
        ).fetchone()
        task_event = connection.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type = 'task_state_changed' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert decision["decision"] == "deny"
    assert json.loads(decision["payload_json"])["action"] == "task.run"
    payload = json.loads(task_event["payload_json"])
    assert payload["stop_reason"] == "policy_denied"
    assert store.get_task(task.task_id).state == "failed"  # type: ignore[union-attr]


def test_consent_denial_blocks_model_call_and_records_task_failure(tmp_path: Path) -> None:
    database, store, runtime, provider, _ = _setup(tmp_path, grant_consent=False)
    session = runtime.create_session(title="No consent", project_id="demo")
    task = runtime.create_task(session.session_id, "consent-goal")
    outcome = asyncio.run(runtime.run_task(task.task_id))

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "consent_denied"
    assert provider.calls == 0
    stored = store.get_task(task.task_id)
    assert stored.state == "failed"  # type: ignore[union-attr]
    assert stored.response_hash is None  # type: ignore[union-attr]
    with database.connect() as connection:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM model_calls ORDER BY rowid"
            ).fetchall()
        ]
    assert statuses == ["consent_denied"]


def test_corrupted_chain_blocks_runtime_before_model_call(tmp_path: Path) -> None:
    database, store, runtime, provider, _ = _setup(tmp_path)
    session = runtime.create_session(title="Broken", project_id="demo")
    task = runtime.create_task(session.session_id, "broken-goal")
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE seq = 1")

    with pytest.raises(AuditIntegrityError):
        asyncio.run(runtime.run_task(task.task_id))
    assert provider.calls == 0
    assert store.get_task(task.task_id).state == "created"  # type: ignore[union-attr]


def test_task_goals_and_responses_are_not_in_audit_or_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, store, runtime, _, _ = _setup(tmp_path)
    session = runtime.create_session(title="Privacy", project_id="demo")
    prompt_marker = "prompt-marker"
    response_marker = "response-text-marker"
    task = runtime.create_task(session.session_id, prompt_marker)
    outcome = asyncio.run(runtime.run_task(task.task_id))
    assert outcome.proposal_text == response_marker
    assert FAKE_KEY.encode() not in database.path.read_bytes()

    with database.connect() as connection:
        audit_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
        )
        task_row = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task.task_id,)
        ).fetchone()
    assert prompt_marker not in audit_blob
    assert response_marker not in audit_blob
    assert prompt_marker in task_row["goal"]  # private local task input
    assert response_marker not in json.dumps(dict(task_row))

    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: runtime)
    monkeypatch.setattr("eis.cli._session_store", lambda: store)
    submitted = runner.invoke(
        app,
        [
            "task",
            "submit",
            session.session_id,
            "--goal",
            prompt_marker,
        ],
    )
    assert submitted.exit_code == 0, submitted.output
    assert prompt_marker not in submitted.output
    assert response_marker not in submitted.output
    assert "answer=untrusted_model_generated_no_action_taken" in submitted.output

    status = runner.invoke(app, ["task", "status", task.task_id])
    assert status.exit_code == 0, status.output
    assert prompt_marker not in status.output
    assert response_marker not in status.output

    shown = runner.invoke(app, ["session", "show", session.session_id])
    assert shown.exit_code == 0, shown.output
    assert prompt_marker not in shown.output
    assert response_marker not in shown.output

    preview = runner.invoke(
        app,
        [
            "task",
            "submit",
            session.session_id,
            "--goal",
            prompt_marker,
            "--show-proposal",
        ],
    )
    assert preview.exit_code == 0, preview.output
    assert "model_generated_answer_begin" in preview.output
    assert response_marker in preview.output
    assert prompt_marker not in preview.output


def test_proposal_preview_strips_control_characters() -> None:
    preview = _proposal_preview("line1\nline2\ttab\x1b[31mred\x00end")
    assert "\x1b" not in preview
    assert "\x00" not in preview
    assert "line1\nline2\ttab" in preview
    assert "\ufffd" in preview
    assert len(_proposal_preview("x" * 5000)) == 4000


def test_cli_session_create_list_and_task_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")

    created = runner.invoke(
        app, ["session", "create", "--title", "CLI Session", "--project", "demo"]
    )
    assert created.exit_code == 0, created.output
    session_id = created.output.split("session_id=")[1].split()[0]

    listed = runner.invoke(app, ["session", "list"])
    assert listed.exit_code == 0, listed.output
    assert session_id in listed.output
    assert "title='CLI Session'" in listed.output

    database = SqliteDatabase(tmp_path / "db" / "eis.sqlite3")
    store = SessionTaskStore(database)
    policy = PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(database))
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns_cli",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
            purpose="cli runtime tests",
        )
    )
    provider = FakeProvider()
    runtime = SessionTaskRuntime(
        store,
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
    )
    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: runtime)

    submitted = runner.invoke(
        app,
        ["task", "submit", session_id, "--goal", "cli-goal-marker"],
    )
    assert submitted.exit_code == 0, submitted.output
    assert "state=blocked" in submitted.output
    task_id = submitted.output.split("task_id=")[1].split()[0]

    status = runner.invoke(app, ["task", "status", task_id])
    assert status.exit_code == 0, status.output
    assert "state=blocked" in status.output
    assert "human_review_required" in status.output

    tasks = runner.invoke(app, ["task", "list", session_id])
    assert tasks.exit_code == 0, tasks.output
    assert task_id in tasks.output
