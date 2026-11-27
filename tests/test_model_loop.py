# SPDX-License-Identifier: Apache-2.0

"""Bounded model-to-tool loop: envelopes, limits, gating and privacy."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.adapters.models.base import (
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    TokenUsage,
)
from eis.cli import app
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
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

runner = CliRunner()
ROOT_ID = "demo"
GOAL = "private-goal-marker"
FILE_CONTENT = "private-file-content-marker"
FINAL_ANSWER = "private-answer-marker"


def answer(text: str) -> str:
    return json.dumps({"kind": "answer", "content": text}, sort_keys=True, separators=(",", ":"))


def call(tool: str, arguments: dict[str, object] | None = None) -> str:
    return json.dumps(
        {"kind": "tool_call", "tool": tool, "arguments": arguments or {}},
        sort_keys=True,
        separators=(",", ":"),
    )


class ScriptedProvider:
    """Replays a fixed queue so every loop stop path is deterministic."""

    name = "openai"
    model = "test-model"

    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls = 0
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        self.requests.append(request)
        item = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str)
        return CompletionResponse(
            text=item,
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
        )


class _FailingAudit:
    def append(self, *args: object, **kwargs: object) -> None:
        raise AuditIntegrityError("audit unavailable")


@dataclass
class Fixture:
    runtime: SessionTaskRuntime
    provider: ScriptedProvider
    store: SessionTaskStore
    database: SqliteDatabase
    tools: ReadOnlyProjectTools
    root: Path


def _policy(*, allow_read: bool = True) -> Policy:
    rules: list[dict[str, str]] = [
        {
            "id": "run-task",
            "principal": "agent:eis.primary",
            "action": "task.run",
            "resource": "project:demo",
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
    if allow_read:
        rules.extend(
            [
                {
                    "id": "read-root",
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
                    "id": "read-big",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/big.txt",
                    "effect": "allow",
                },
            ]
        )
    return Policy.from_dict({"version": "model-loop-v1", "default": "deny", "rules": rules})


def _setup(
    tmp_path: Path,
    responses: list[object],
    *,
    allow_read: bool = True,
    grant_consent: bool = True,
) -> Fixture:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(FILE_CONTENT, encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    policy = PolicyService(
        PolicyEngine(_policy(allow_read=allow_read)),
        SqlitePolicyDecisionSink(database),
    )
    roots = ProjectRootStore(database)
    roots.register(ROOT_ID, root, actor="test")
    tools = ReadOnlyProjectTools(
        project_roots=roots,
        policy=policy,
        audit=AuditChainStore(database),
    )
    consent = ConsentStore(database)
    if grant_consent:
        consent.grant(
            ConsentRecord(
                consent_id="cns_model_loop",
                provider="openai",
                data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
                purpose="model loop tests",
            )
        )
    provider = ScriptedProvider(responses)
    runtime = SessionTaskRuntime(
        sessions,
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=tools,
    )
    return Fixture(
        runtime=runtime,
        provider=provider,
        store=sessions,
        database=database,
        tools=tools,
        root=root,
    )


def _events(database: SqliteDatabase) -> list[str]:
    with database.connect() as connection:
        return [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]


def _spy_execution(monkeypatch: pytest.MonkeyPatch, tools: ReadOnlyProjectTools) -> list[ToolCall]:
    executed: list[ToolCall] = []
    original = tools._execute

    def spy(tool_call: ToolCall) -> object:
        executed.append(tool_call)
        return original(tool_call)

    monkeypatch.setattr(tools, "_execute", spy)
    return executed


def _submit(fx: Fixture, goal: str = GOAL):
    session = fx.runtime.create_session(title="Loop", project_id=ROOT_ID)
    return asyncio.run(fx.runtime.submit_task(session.session_id, goal))


def test_valid_answer_envelope_completes_the_run(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [answer("final answer")])
    outcome = _submit(fx)

    assert outcome.task.state == "blocked"
    assert outcome.task.stop_reason == "human_review_required"
    assert outcome.proposal_text == "final answer"
    assert fx.provider.calls == 1
    assert outcome.task.usage["completions"] == 1
    assert outcome.task.usage["tool_calls"] == 0
    assert "tool_invocation_started" not in _events(fx.database)


def test_tool_call_envelope_dispatches_through_the_registry_then_answers(
    tmp_path: Path,
) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "app.py"}), answer("done")])
    outcome = _submit(fx)

    assert outcome.task.state == "blocked"
    assert outcome.proposal_text == "done"
    assert fx.provider.calls == 2
    assert outcome.task.usage["completions"] == 2
    assert outcome.task.usage["tool_calls"] == 1

    events = _events(fx.database)
    assert events.count("tool_invocation_started") == 1
    assert events.count("tool_invocation_completed") == 1
    with fx.database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type = 'tool_invocation_started' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert json.loads(row[0])["project_id"] == ROOT_ID

    # File contents re-enter the prompt in memory only.
    feedback = [message.content for message in fx.provider.requests[1].messages]
    assert any(FILE_CONTENT in content for content in feedback)
    assert FILE_CONTENT.encode() not in fx.database.path.read_bytes()


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "app.py", "project_id": "attacker"},
        {"path": "app.py", "root_path": "outside"},
    ],
)
def test_identity_and_root_fields_from_model_output_are_rejected(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    fx = _setup(tmp_path, [call("project.read_text", arguments)])
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "unexpected_field"
    assert outcome.proposal_text is None
    assert fx.provider.calls == 1
    assert "tool_invocation_started" not in _events(fx.database)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ('{"kind":"answer","kind":"answer","content":"x"}', "malformed_response"),
        ("plain prose, not an envelope", "malformed_response"),
        ("[]", "malformed_response"),
        ('{"kind":"answer"}', "unexpected_field"),
        ('{"kind":"answer","content":"x","session_id":"ses_1"}', "unexpected_field"),
        ('{"kind":"plan","content":"x"}', "unknown_kind"),
        ('{"kind":"answer","content":"   "}', "invalid_answer"),
        ('{"kind":"tool_call","tool":"project.list"}', "unexpected_field"),
        (answer("x" * 9000), "response_too_large"),
    ],
)
def test_invalid_responses_stop_the_run_closed(
    tmp_path: Path, response: str, expected: str
) -> None:
    fx = _setup(tmp_path, [response])
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == expected
    assert outcome.proposal_text is None
    assert fx.provider.calls == 1
    assert "tool_invocation_started" not in _events(fx.database)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (call("shell.exec", {"command": "ls"}), "unknown_tool"),
        (call("project.read_text", {"path": "../outside"}), "invalid_relative_path"),
        (call("project.read_text", {"path": "secrets.txt"}), "secret_path_denied"),
    ],
)
def test_unknown_or_unsafe_tool_requests_stop_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str, expected: str
) -> None:
    fx = _setup(tmp_path, [response])
    executed = _spy_execution(monkeypatch, fx.tools)
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == expected
    assert executed == []
    assert fx.provider.calls == 1
    assert "tool_invocation_started" not in _events(fx.database)


def test_policy_denial_precedes_any_filesystem_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(
        tmp_path,
        [call("project.read_text", {"path": "app.py"}), answer("unused")],
        allow_read=False,
    )
    executed = _spy_execution(monkeypatch, fx.tools)
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "policy_denied"
    assert outcome.proposal_text is None
    assert executed == []
    assert fx.provider.calls == 1
    events = _events(fx.database)
    assert "tool_invocation_denied" in events
    assert "tool_invocation_started" not in events


def test_audit_failure_prevents_tool_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "app.py"}), answer("unused")])
    executed = _spy_execution(monkeypatch, fx.tools)
    monkeypatch.setattr(fx.tools, "_audit", _FailingAudit())
    session = fx.runtime.create_session(title="Broken audit", project_id=ROOT_ID)

    with pytest.raises(AuditIntegrityError):
        asyncio.run(fx.runtime.submit_task(session.session_id, GOAL))

    assert executed == []
    assert "tool_invocation_started" not in _events(fx.database)
    task = fx.store.list_tasks(session.session_id)[0]
    assert task.state == "running"


def test_consent_failure_stops_before_any_tool_work(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "app.py"})], grant_consent=False)
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "consent_denied"
    assert fx.provider.calls == 0
    assert "tool_invocation_started" not in _events(fx.database)


def test_provider_failure_mid_loop_keeps_content_free_counts(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "app.py"}), ProviderError("down")])
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "provider_error"
    assert fx.provider.calls == 2
    assert outcome.task.usage["completions"] == 1
    assert outcome.task.usage["tool_calls"] == 1
    events = _events(fx.database)
    assert events.count("tool_invocation_completed") == 1
    assert FILE_CONTENT.encode() not in fx.database.path.read_bytes()


def test_loop_stops_at_the_documented_call_and_completion_limits(tmp_path: Path) -> None:
    fx = _setup(
        tmp_path,
        [
            call("project.list"),
            call("project.read_text", {"path": "app.py"}),
            call("project.list_files", {"path": ""}),
        ],
    )
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "limit_exhausted"
    assert fx.provider.calls == 3
    assert outcome.task.usage["completions"] == 3
    assert outcome.task.usage["tool_calls"] == 2
    assert _events(fx.database).count("tool_invocation_started") == 2


def test_repeated_identical_tool_calls_stop_the_run(tmp_path: Path) -> None:
    same = call("project.read_text", {"path": "app.py"})
    fx = _setup(tmp_path, [same, same])
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "repeated_tool_call"
    assert fx.provider.calls == 2
    assert outcome.task.usage["tool_calls"] == 1
    assert _events(fx.database).count("tool_invocation_started") == 1


def test_wall_time_limit_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("eis.core.runtime.RUN_WALL_TIME_SECONDS", 0.0)
    fx = _setup(tmp_path, [answer("never reached")])
    outcome = _submit(fx)

    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "wall_time_exceeded"
    assert outcome.proposal_text is None
    assert fx.provider.calls == 0


def test_large_tool_results_are_truncated_before_reentering_the_prompt(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "big.txt"}), answer("done")])
    (fx.root / "big.txt").write_text("y" * 20_000, encoding="utf-8")
    outcome = _submit(fx)

    assert outcome.task.state == "blocked"
    assert outcome.proposal_text == "done"
    feedback = [
        message.content for message in fx.provider.requests[1].messages if message.role == "user"
    ][-1]
    assert feedback.endswith("...[truncated]")
    assert len(feedback) <= 4_100


def test_private_content_stays_out_of_storage_and_default_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [call("project.read_text", {"path": "app.py"}), answer(FINAL_ANSWER)])
    session = fx.runtime.create_session(title="Privacy", project_id=ROOT_ID)
    outcome = asyncio.run(fx.runtime.submit_task(session.session_id, GOAL))
    assert outcome.proposal_text == FINAL_ANSWER

    blob = fx.database.path.read_bytes()
    assert FILE_CONTENT.encode() not in blob
    assert FINAL_ANSWER.encode() not in blob
    with fx.database.connect() as connection:
        audit_blob = json.dumps(
            [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
        )
    assert GOAL not in audit_blob
    assert FILE_CONTENT not in audit_blob
    assert FINAL_ANSWER not in audit_blob

    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx.runtime)
    shown = runner.invoke(
        app, ["task", "submit", session.session_id, "--goal", GOAL, "--show-proposal"]
    )
    assert shown.exit_code == 0, shown.output
    assert FINAL_ANSWER in shown.output
    assert FILE_CONTENT not in shown.output
    assert GOAL not in shown.output


def test_cli_final_answer_requires_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _setup(tmp_path, [answer("model-answer-text")])
    session = fx.runtime.create_session(title="CLI answer", project_id=ROOT_ID)
    monkeypatch.setattr("eis.cli._session_runtime", lambda policy_file=None: fx.runtime)

    default = runner.invoke(app, ["task", "submit", session.session_id, "--goal", GOAL])
    assert default.exit_code == 0, default.output
    assert "answer=untrusted_model_generated_no_action_taken" in default.output
    assert "model-answer-text" not in default.output
    assert "completions=1" in default.output
    assert "tool_calls=0" in default.output

    shown = runner.invoke(
        app, ["task", "submit", session.session_id, "--goal", GOAL, "--show-proposal"]
    )
    assert shown.exit_code == 0, shown.output
    assert "model_generated_answer_begin" in shown.output
    assert "model-answer-text" in shown.output
    assert GOAL not in shown.output


def test_interrupted_running_task_is_never_auto_resumed(tmp_path: Path) -> None:
    fx = _setup(tmp_path, [answer("never reached")])
    session = fx.runtime.create_session(title="Interrupted", project_id=ROOT_ID)
    task = fx.runtime.create_task(session.session_id, GOAL)
    fx.store.transition_task(
        task.task_id,
        expected_state="created",
        new_state="running",
        session_status="active",
    )

    with pytest.raises(RuntimeStateError, match="already running"):
        asyncio.run(fx.runtime.run_task(task.task_id))
    assert fx.provider.calls == 0

    outcome = asyncio.run(fx.runtime.resume_task(task.task_id))
    assert outcome.task.state == "failed"
    assert outcome.task.stop_reason == "interrupted"
    assert fx.provider.calls == 0
