# SPDX-License-Identifier: Apache-2.0

"""Durable task plans: strict schema, bounded lifecycle, gating and privacy."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from typer.testing import CliRunner

from eis.adapters.models.base import CompletionRequest, CompletionResponse, TokenUsage
from eis.cli import app
from eis.config import ConsentRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.core.model_plan import (
    MAX_PLAN_STEPS,
    ModelPlanError,
    parse_plan_response,
)
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

runner = CliRunner()
ROOT_ID = "demo"
GOAL = "private-goal-marker"
FILE_CONTENT = "private-file-content-marker"


def _run_immediate(coroutine):
    """Drive a coroutine that must complete without scheduling or suspension."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("coroutine unexpectedly suspended")


def step(
    step_id: str,
    tool: str,
    arguments: dict[str, object] | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": step_id,
        "tool": tool,
        "arguments": arguments or {},
        "depends_on": depends_on or [],
    }


def plan(steps: list[dict[str, object]]) -> str:
    return json.dumps({"kind": "plan", "steps": steps}, sort_keys=True, separators=(",", ":"))


READ_STEP = step("s1", "project.read_text", {"path": "app.py"})
REVIEW_STEP = step("s2", "human_review", {}, ["s1"])


class ScriptedProvider:
    name = "openai"
    model = "test-model"

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        self.requests.append(request)
        index = min(self.calls - 1, len(self._responses) - 1)
        return CompletionResponse(
            text=self._responses[index],
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
        )


@dataclass
class Fixture:
    runtime: SessionTaskRuntime
    provider: ScriptedProvider
    store: SessionTaskStore
    plans: PlanStore
    database: SqliteDatabase
    tools: ReadOnlyProjectTools
    policy: PolicyService


def _policy(*, allow_read: bool = True) -> Policy:
    rules: list[dict[str, str]] = [
        {
            "id": "run-task",
            "principal": "agent:eis.primary",
            "action": "task.run",
            "resource": "project:demo",
            "effect": "allow",
        }
    ]
    if allow_read:
        rules.extend(
            [
                {
                    "id": "read-project",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "project:demo",
                    "effect": "allow",
                },
                {
                    "id": "read-app",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/app.py",
                    "effect": "allow",
                },
                {
                    "id": "list-projects",
                    "principal": "agent:eis.primary",
                    "action": "project.list",
                    "resource": "global",
                    "effect": "allow",
                },
            ]
        )
    return Policy.from_dict({"version": "plan-slice-v1", "default": "deny", "rules": rules})


def _setup(tmp_path: Path, responses: list[str], *, allow_read: bool = True) -> Fixture:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(FILE_CONTENT, encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    plans = PlanStore(database)
    policy = PolicyService(
        PolicyEngine(_policy(allow_read=allow_read)),
        SqlitePolicyDecisionSink(database),
    )
    roots = ProjectRootStore(database)
    roots.register(ROOT_ID, root, actor="test")
    tools = ReadOnlyProjectTools(
        project_roots=roots, policy=policy, audit=AuditChainStore(database)
    )
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns_plans",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
            purpose="plan tests",
        )
    )
    provider = ScriptedProvider(responses)
    runtime = SessionTaskRuntime(
        sessions,
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=tools,
        plans=plans,
    )
    return Fixture(runtime, provider, sessions, plans, database, tools, policy)


def _submit(fx: Fixture, goal: str = GOAL):
    session = fx.runtime.create_session(title="Plan", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, goal)
    return session, task, asyncio.run(fx.runtime.run_plan(task.task_id))


def _running_task(fx: Fixture):
    session = fx.runtime.create_session(title="Plan guard", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, GOAL)
    running = fx.store.transition_task(
        task.task_id,
        expected_state="created",
        new_state="running",
        session_status="active",
    )
    return session, running


def _created_plan(fx: Fixture, steps: list[dict[str, object]] | None = None):
    _, task = _running_task(fx)
    envelope = parse_plan_response(plan(steps or [READ_STEP]))
    record = fx.plans.create_plan(
        task_id=task.task_id,
        project_id=ROOT_ID,
        policy_version="plan-slice-v1",
        policy_hash=fx.policy.policy_hash,
        plan_hash=envelope.plan_hash,
        steps=envelope.steps,
        actor=task.principal,
    )
    return task, record


def _events(database: SqliteDatabase) -> list[str]:
    with database.connect() as connection:
        return [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]


def test_migration_restart_and_audit_chain(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP, REVIEW_STEP])])
    _, task, outcome = _submit(fx)

    assert outcome.task.state == "blocked"
    assert outcome.plan is not None
    assert outcome.plan.state == "blocked"
    assert outcome.plan.plan_hash == parse_plan_response(plan([READ_STEP, REVIEW_STEP])).plan_hash
    assert outcome.plan.policy_hash == fx.policy.policy_hash
    assert len(outcome.steps) == 2
    with fx.database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        triggers = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        }
    assert {"task_plans", "task_plan_steps"} <= tables
    assert {"task_plans_state_transition", "task_plan_steps_status_transition"} <= triggers

    # A new store instance reads the persisted plan and steps after a restart.
    restarted = PlanStore(fx.database)
    reloaded = restarted.get_plan(outcome.plan.plan_id)
    assert reloaded is not None
    assert reloaded.task_id == task.task_id
    assert reloaded.project_id == ROOT_ID
    assert [(item.step_ref, item.status) for item in restarted.list_steps(reloaded.plan_id)] == [
        ("s1", "completed"),
        ("s2", "blocked"),
    ]
    assert restarted.list_steps(reloaded.plan_id)[0].arguments["path"] == "app.py"
    assert restarted.list_steps(reloaded.plan_id)[0].attempt_count == 1
    assert AuditChainStore(fx.database).status().ok


def test_migration_upgrades_previous_release_without_data_loss(tmp_path: Path) -> None:
    database = SqliteDatabase(tmp_path / "legacy.sqlite3")
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[1] / "src" / "eis" / "storage" / "migrations"),
    )
    engine = create_engine(f"sqlite+pysqlite:///{database.path.as_posix()}")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0006_task_plans")
    finally:
        engine.dispose()
    with database.session(immediate=True) as connection:
        connection.execute(
            "INSERT INTO sessions (id, title, status, project_id, created_at, updated_at) "
            "VALUES ('ses_legacy', 'Legacy', 'idle', 'demo', 1, 1)"
        )
        connection.execute(
            """
            INSERT INTO tasks
                (id, session_id, state, principal, goal, goal_hash, policy_version,
                 runtime_version, created_at)
            VALUES ('tsk_legacy', 'ses_legacy', 'created', 'agent:eis.primary',
                    'legacy goal', ?, 'legacy-v1', 'legacy-runtime', 1)
            """,
            ("a" * 64,),
        )
    database.migrate()

    assert SessionTaskStore(database).get_task("tsk_legacy") is not None
    assert PlanStore(database).get_plan_for_task("tsk_legacy") is None
    with database.connect() as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert version == "0010_approval_execution_recovery"
    assert {"task_plans", "task_plan_steps"} <= tables


def test_explicit_resume_replays_only_interrupted_read_only_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = [
        step("s1", "project.list"),
        step("s2", "project.read_text", {"path": "app.py"}, ["s1"]),
    ]
    fx = _setup(tmp_path, [plan(steps)])
    _, task = _running_task(fx)
    envelope = parse_plan_response(plan(steps))
    created = fx.plans.create_plan(
        task_id=task.task_id,
        project_id=ROOT_ID,
        policy_version=fx.policy.policy_version,
        policy_hash=fx.policy.policy_hash,
        plan_hash=envelope.plan_hash,
        steps=envelope.steps,
        actor=task.principal,
    )
    running_plan = fx.plans.transition_plan(
        created.plan_id, expected_state="created", new_state="running"
    )
    first, interrupted = fx.plans.list_steps(running_plan.plan_id)
    fx.plans.transition_step(first.step_id, expected_status="pending", new_status="running")
    fx.plans.transition_step(
        first.step_id,
        expected_status="running",
        new_status="completed",
        output_sha256="a" * 64,
    )
    fx.plans.transition_step(interrupted.step_id, expected_status="pending", new_status="running")
    invoked: list[str] = []
    original_execute = fx.tools._execute

    def spy(call):
        invoked.append(call.tool)
        return original_execute(call)

    monkeypatch.setattr(fx.tools, "_execute", spy)

    outcome = _run_immediate(fx.runtime.resume_plan(task.task_id))

    assert outcome.task.state == "completed"
    assert outcome.plan is not None and outcome.plan.state == "completed"
    assert [(item.status, item.attempt_count) for item in outcome.steps] == [
        ("completed", 1),
        ("completed", 2),
    ]
    assert invoked == ["project.read_text"]
    assert fx.provider.calls == 0
    assert AuditChainStore(fx.database).status().ok


def test_interrupted_step_retry_is_bounded(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    _, task = _running_task(fx)
    envelope = parse_plan_response(plan([READ_STEP]))
    created = fx.plans.create_plan(
        task_id=task.task_id,
        project_id=ROOT_ID,
        policy_version=fx.policy.policy_version,
        policy_hash=fx.policy.policy_hash,
        plan_hash=envelope.plan_hash,
        steps=envelope.steps,
        actor=task.principal,
    )
    running_plan = fx.plans.transition_plan(
        created.plan_id, expected_state="created", new_state="running"
    )
    record = fx.plans.list_steps(running_plan.plan_id)[0]
    fx.plans.transition_step(record.step_id, expected_status="pending", new_status="running")
    fx.plans.recover_interrupted_steps(running_plan.plan_id)
    fx.plans.transition_step(record.step_id, expected_status="pending", new_status="running")
    with pytest.raises(RuntimeStateError, match="retry limit"):
        fx.plans.recover_interrupted_steps(running_plan.plan_id)
    assert fx.store.get_task(task.task_id) is not None


def test_plan_and_step_transitions_are_guarded(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    _, created = _created_plan(fx)
    with pytest.raises(RuntimeStateError, match="invalid plan transition"):
        fx.plans.transition_plan(created.plan_id, expected_state="created", new_state="completed")
    with pytest.raises(RuntimeStateError, match="expected running"):
        fx.plans.transition_plan(created.plan_id, expected_state="running", new_state="completed")

    running_plan = fx.plans.transition_plan(
        created.plan_id, expected_state="created", new_state="running"
    )
    with pytest.raises(RuntimeStateError, match="invalid plan transition"):
        fx.plans.transition_plan(
            running_plan.plan_id, expected_state="running", new_state="created"
        )
    first_step = fx.plans.list_steps(running_plan.plan_id)[0]
    with pytest.raises(RuntimeStateError, match="invalid step transition"):
        fx.plans.transition_step(
            first_step.step_id, expected_status="pending", new_status="completed"
        )
    with pytest.raises(RuntimeStateError, match="expected running"):
        fx.plans.transition_step(first_step.step_id, expected_status="running", new_status="failed")
    with pytest.raises(sqlite3.IntegrityError), fx.database.session(immediate=True) as connection:
        connection.execute(
            "UPDATE task_plans SET state = 'created' WHERE id = ?", (running_plan.plan_id,)
        )


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('{"kind":"plan","kind":"plan","steps":[]}', "malformed_plan"),
        (json.dumps({"kind": "plan", "steps": [], "extra": 1}), "unexpected_field"),
        (json.dumps({"kind": "answer", "steps": []}), "unexpected_field"),
        (plan([step("s1", "project.list") for _ in range(MAX_PLAN_STEPS + 1)]), "limit_exceeded"),
        (plan([]), "limit_exceeded"),
        (plan([step("s1", "shell.exec")]), "invalid_tool"),
        (plan([step("s1", "project.list", {"project_id": "demo"})]), "forbidden_instruction"),
        (plan([step("s1", "project.list", {"foo": 1})]), "invalid_arguments"),
        (
            plan([step("s1", "project.read_text", {"path": "delete-all.txt"})]),
            "forbidden_instruction",
        ),
        (plan([step("s1", "project.read_text", {"path": "write.txt"})]), "forbidden_instruction"),
        (plan([step("s1", "project.read_text", {"path": "network.txt"})]), "forbidden_instruction"),
        (
            plan([step("s1", "project.read_text", {"path": "approval.txt"})]),
            "forbidden_instruction",
        ),
        (plan([step("s1", "project.read_text", {"path": "command.txt"})]), "forbidden_instruction"),
        (plan([step("s1", "project.read_text", {"path": "root.txt"})]), "forbidden_instruction"),
        (plan([step("s1", "project.read_text", {"path": "policy.txt"})]), "forbidden_instruction"),
        (
            plan([step("s1", "project.read_text", {"path": "principal.txt"})]),
            "forbidden_instruction",
        ),
        (plan([step("s1", "human_review", {"path": "app.py"})]), "invalid_arguments"),
        (plan([step("s1", "project.read_text", {"path": "x" * 30_000})]), "plan_too_large"),
        (plan([step("s1", "project.list", {}, ["missing"])]), "missing_dependency"),
        (plan([step("s1", "project.list", {}, ["s1"])]), "dependency_cycle"),
        (
            plan(
                [
                    step("s1", "project.list", {}, ["s2"]),
                    step("s2", "project.list", {}, ["s1"]),
                ]
            ),
            "dependency_cycle",
        ),
    ],
)
def test_strict_plan_schema_rejects_hostile_input(text: str, code: str) -> None:
    with pytest.raises(ModelPlanError) as caught:
        parse_plan_response(text)
    assert caught.value.code == code


def test_dependency_ready_steps_run_in_dependency_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = step("s1", "project.read_text", {"path": "app.py"})
    second = step("s2", "project.list_files", {"path": ""}, ["s1"])
    fx = _setup(tmp_path, [plan([second, first])])
    executed: list[str] = []
    original = fx.tools._execute

    def spy(call: ToolCall) -> object:
        executed.append(call.tool)
        return original(call)

    monkeypatch.setattr(fx.tools, "_execute", spy)
    _, task, outcome = _submit(fx)

    assert executed == ["project.read_text", "project.list_files"]
    assert outcome.task.state == "completed"
    assert outcome.task.stop_reason == "plan_completed"
    assert outcome.task.usage["tool_calls"] == 2
    assert outcome.plan is not None and outcome.plan.state == "completed"
    assert all(item.status == "completed" for item in outcome.steps)
    assert "plan_created" in _events(fx.database)
    assert "plan_step_state_changed" in _events(fx.database)


def test_human_review_step_blocks_the_plan_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_first = step("s1", "human_review")
    later_read = step("s2", "project.read_text", {"path": "app.py"}, ["s1"])
    fx = _setup(tmp_path, [plan([review_first, later_read])])
    executed: list[str] = []
    original = fx.tools._execute

    def spy(call: ToolCall) -> object:
        executed.append(call.tool)
        return original(call)

    monkeypatch.setattr(fx.tools, "_execute", spy)
    _, _, outcome = _submit(fx)
    assert executed == []
    assert outcome.task.state == "blocked"
    assert outcome.plan is not None and outcome.plan.state == "blocked"
    assert [item.status for item in outcome.steps] == ["blocked", "pending"]
    assert outcome.steps[0].error_code == "human_review_required"


def test_policy_denial_stops_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])], allow_read=False)
    executed: list[ToolCall] = []
    original = fx.tools._execute

    def spy(call: ToolCall) -> object:
        executed.append(call)
        return original(call)

    monkeypatch.setattr(fx.tools, "_execute", spy)
    _, _, outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "policy_denied"
    assert executed == []
    assert outcome.plan is not None and outcome.plan.state == "failed"
    assert outcome.steps[0].status == "failed"
    assert "tool_invocation_started" not in _events(fx.database)
    assert "tool_invocation_denied" in _events(fx.database)


def test_audit_failure_propagates_and_no_step_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    original = fx.tools._audit.append

    def failing_append(*args: object, **kwargs: object) -> object:
        if kwargs.get("event_type") in {"tool_invocation_started", "tool_invocation_denied"}:
            raise AuditIntegrityError("audit unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(fx.tools._audit, "append", failing_append)
    session = fx.runtime.create_session(title="Audit failure", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, GOAL)
    with pytest.raises(AuditIntegrityError):
        asyncio.run(fx.runtime.run_plan(task.task_id))
    assert fx.plans.get_plan_for_task(task.task_id) is not None
    assert all(
        item.status != "completed"
        for item in fx.plans.list_steps(
            fx.plans.get_plan_for_task(task.task_id).plan_id  # type: ignore[union-attr]
        )
    )


def test_repeated_identical_step_stops_the_plan(tmp_path: Path) -> None:
    duplicate = step("s2", "project.read_text", {"path": "app.py"}, ["s1"])
    fx = _setup(tmp_path, [plan([READ_STEP, duplicate])])
    _, _, outcome = _submit(fx)
    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "repeated_tool_call"
    assert outcome.plan is not None and outcome.plan.state == "failed"
    assert outcome.task.usage["tool_calls"] == 1


def test_interrupted_running_plan_resumes_only_on_explicit_request(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    task, created = _created_plan(fx)
    running = fx.plans.transition_plan(
        created.plan_id, expected_state="created", new_state="running"
    )
    first_step = fx.plans.list_steps(running.plan_id)[0]
    fx.plans.transition_step(first_step.step_id, expected_status="pending", new_status="running")

    outcome = _run_immediate(fx.runtime.resume_plan(task.task_id))
    assert outcome.task.state == "completed"
    assert outcome.task.stop_reason == "plan_completed"
    assert outcome.plan is not None and outcome.plan.state == "completed"
    assert outcome.steps[0].status == "completed"
    assert outcome.steps[0].attempt_count == 2
    assert fx.provider.calls == 0


def test_private_content_never_enters_database_or_default_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    session, _, outcome = _submit(fx)
    blob = fx.database.path.read_bytes()
    assert FILE_CONTENT.encode() not in blob
    with fx.database.connect() as connection:
        step_rows = [dict(row) for row in connection.execute("SELECT * FROM task_plan_steps")]
        plan_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM task_plans")] + step_rows
        )
        audit_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
        )
    for marker in (GOAL, FILE_CONTENT):
        assert marker not in plan_blob
    assert any(json.loads(row["arguments_json"]).get("path") == "app.py" for row in step_rows)
    for marker in (GOAL, FILE_CONTENT):
        assert marker not in audit_blob

    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx.runtime)
    monkeypatch.setattr("eis.cli._plan_store", lambda: fx.plans)
    default = runner.invoke(app, ["plan", "run", session.session_id, "--goal", GOAL])
    assert default.exit_code == 0, default.output
    assert "plan=untrusted_model_generated_no_action_taken" in default.output
    assert FILE_CONTENT not in default.output
    assert GOAL not in default.output
    assert "app.py" not in default.output

    plan_id = outcome.plan.plan_id  # type: ignore[union-attr]
    shown = runner.invoke(app, ["plan", "show", plan_id])
    steps = runner.invoke(app, ["plan", "steps", plan_id])
    assert shown.exit_code == 0 and steps.exit_code == 0
    for output in (shown.output, steps.output):
        assert FILE_CONTENT not in output
        assert "app.py" not in output
        assert GOAL not in output


def test_plan_proposal_requires_explicit_opt_in_and_is_labeled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [plan([READ_STEP])])
    session = fx.runtime.create_session(title="Proposal", project_id=ROOT_ID)
    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx.runtime)
    monkeypatch.setattr("eis.cli._plan_store", lambda: fx.plans)

    hidden = runner.invoke(app, ["plan", "run", session.session_id, "--goal", GOAL])
    assert hidden.exit_code == 0, hidden.output
    assert '"kind":"plan"' not in hidden.output
    assert "model_generated_plan_begin" not in hidden.output

    shown = runner.invoke(
        app, ["plan", "run", session.session_id, "--goal", GOAL, "--show-proposal"]
    )
    assert shown.exit_code == 0, shown.output
    assert "model_generated_plan_begin" in shown.output
    assert "model_generated_plan_end" in shown.output
    assert '"kind":"plan"' in shown.output
    assert GOAL not in shown.output
