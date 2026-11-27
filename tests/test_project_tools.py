# SPDX-License-Identifier: Apache-2.0

"""Policy-gated, bounded read-only project tool tests."""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from eis.adapters.models.base import CompletionRequest, CompletionResponse
from eis.cli import app
from eis.config import ConsentRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.core.policy_service import PolicyService
from eis.core.project_tools import (
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_TOOL_OUTPUT_BYTES,
    ReadOnlyProjectTools,
    ToolRequestError,
    parse_tool_call,
)
from eis.core.runtime import SessionTaskRuntime
from eis.policy import Policy, PolicyEngine, Principal
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

runner = CliRunner()
ROOT_ID = "demo"
PRINCIPAL = Principal("agent", "eis.primary")
PRIVATE_CONTENT = "file-content-must-not-be-audited"
DRIVE_PREFIX = "C" + chr(58)


class FakeProvider:
    name = "openai"
    model = "test-model"

    def __init__(self, text: str = "proposal only") -> None:
        self.text = text
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            text=self.text,
            provider=self.name,
            model=self.model,
        )


def _policy(
    *,
    allow: bool = True,
    version: str = "tools-test-v1",
) -> Policy:
    rules: list[dict[str, str]] = []
    if allow:
        rules.extend(
            [
                {
                    "id": "read-app",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/src/app.py",
                    "effect": "allow",
                },
                {
                    "id": "read-escape-test",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/escape.txt",
                    "effect": "allow",
                },
                {
                    "id": "read-root-app-test",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/app.py",
                    "effect": "allow",
                },
                {
                    "id": "read-large-test",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/large.txt",
                    "effect": "allow",
                },
                {
                    "id": "list-src",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/src",
                    "effect": "allow",
                },
                {
                    "id": "read-project",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "project:demo",
                    "effect": "allow",
                },
                {
                    "id": "git-read",
                    "principal": "agent:eis.primary",
                    "action": "git.read",
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
        )
    return Policy.from_dict({"version": version, "default": "deny", "rules": rules})


def _registry(
    tmp_path: Path,
    root: Path,
    *,
    allow: bool = True,
    database: SqliteDatabase | None = None,
    principal: Principal = PRINCIPAL,
) -> tuple[SqliteDatabase, ReadOnlyProjectTools]:
    db = database or SqliteDatabase(tmp_path / "eis.sqlite3")
    roots = ProjectRootStore(db)
    roots.register(ROOT_ID, root, actor="test")
    service = PolicyService(
        PolicyEngine(_policy(allow=allow)),
        SqlitePolicyDecisionSink(db),
    )
    return db, ReadOnlyProjectTools(
        project_roots=roots,
        policy=service,
        audit=AuditChainStore(db),
    )


def _call(tool: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {"tool": tool, "arguments": arguments or {}}


def test_tool_call_schema_is_strict_and_registry_limited() -> None:
    call = parse_tool_call(
        _call("project.read_text", {"project_id": ROOT_ID, "path": "src/app.py"})
    )
    assert call.tool == "project.read_text"
    assert call.arguments_dict() == {
        "project_id": ROOT_ID,
        "path": "src/app.py",
        "max_bytes": MAX_READ_BYTES,
    }
    for malformed in (
        {"tool": "unknown.execute", "arguments": {}},
        {"tool": "project.list", "arguments": {}, "principal": "user"},
        {"tool": "project.list_files", "arguments": {"project_id": ROOT_ID, "shell": "git"}},
        _call("project.list_files", {"project_id": ROOT_ID, "limit": True}),
        _call(
            "project.read_text",
            {"project_id": ROOT_ID, "path": f"{DRIVE_PREFIX}/secret.txt"},
        ),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "../secret.txt"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "/etc/passwd"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": ".env"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": ".env.local"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "keys/token.txt"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "secrets.txt"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "credentials.json"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "id_rsa"}),
        _call("project.read_text", {"project_id": ROOT_ID, "path": "secret.pem"}),
        '{"tool":"project.list","tool":"git.status","arguments":{}}',
    ):
        with pytest.raises(ToolRequestError):
            parse_tool_call(malformed)


def test_allowed_tools_use_policy_and_return_bounded_results(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_bytes(b"print('hello')\n")
    (root / ".env").write_text("DO_NOT_READ=secret", encoding="utf-8")
    (root / "src" / ".env.local").write_text("hidden", encoding="utf-8")
    db, tools = _registry(tmp_path, root)

    listing = tools.invoke(
        call=parse_tool_call(_call("project.list")),
        task_id="tsk_list",
        principal=PRINCIPAL,
    )
    assert listing.status == "success"
    assert listing.data == {"projects": [{"project_id": ROOT_ID}]}

    files = tools.invoke(
        call=parse_tool_call(
            _call(
                "project.list_files",
                {"project_id": ROOT_ID, "path": "src", "limit": 10},
            )
        ),
        task_id="tsk_files",
        principal=PRINCIPAL,
    )
    assert files.status == "success"
    assert files.data == {
        "entries": [{"path": "src/app.py", "kind": "file", "size": 15}],
        "truncated": False,
    }

    read = tools.invoke(
        call=parse_tool_call(
            _call("project.read_text", {"project_id": ROOT_ID, "path": "src/app.py"})
        ),
        task_id="tsk_read",
        principal=PRINCIPAL,
    )
    assert read.status == "success"
    assert read.data == {
        "path": "src/app.py",
        "byte_count": 15,
        "text": "print('hello')\n",
    }

    with db.connect() as connection:
        events = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM audit_events WHERE event_type LIKE 'tool_invocation_%'"
            )
        ]
        all_events = [dict(row) for row in connection.execute("SELECT * FROM audit_events")]
    event_blob = json.dumps(events, default=str)
    full_audit = json.dumps(all_events, default=str)
    assert PRIVATE_CONTENT not in event_blob
    assert "print('hello')" not in full_audit
    assert "DO_NOT_READ=secret" not in full_audit
    assert "src/app.py" not in event_blob
    assert [event["event_type"] for event in events].count("tool_invocation_started") == 3
    assert [event["event_type"] for event in events].count("tool_invocation_completed") == 3
    with db.connect() as connection:
        event_types = [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]
    for index, event_type in enumerate(event_types):
        if event_type == "tool_invocation_started":
            assert "policy_decision" in event_types[:index]
    assert AuditChainStore(db).status().ok


def test_policy_denial_and_audit_corruption_prevent_tool_work(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("private-content", encoding="utf-8")
    db, denied_tools = _registry(tmp_path, root, allow=False)
    denied_tools._execute = lambda call: pytest.fail(  # type: ignore[method-assign]
        "denied tool must not execute"
    )
    denied = denied_tools.invoke(
        call=parse_tool_call(_call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"})),
        task_id="tsk_denied",
        principal=PRINCIPAL,
    )
    assert denied.status == "denied"
    assert denied.data is None
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = 'tool_invocation_started'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = 'tool_invocation_denied'"
            ).fetchone()[0]
            == 1
        )

    with db.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE seq = 1")
    _, corrupted_tools = _registry(tmp_path, root, database=db)
    corrupted_tools._execute = lambda call: pytest.fail(  # type: ignore[method-assign]
        "corrupted-chain tool must not execute"
    )
    with pytest.raises(AuditIntegrityError):
        corrupted_tools.invoke(
            call=parse_tool_call(
                _call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"})
            ),
            task_id="tsk_corrupt",
            principal=PRINCIPAL,
        )
    assert target.read_text(encoding="utf-8") == "private-content"
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type = 'tool_invocation_started'"
            ).fetchone()[0]
            == 0
        )


def test_tool_start_audit_failure_prevents_tool_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("content", encoding="utf-8")
    _, tools = _registry(tmp_path, root)
    original_append = tools._audit.append

    def fail_start(**kwargs: object) -> object:
        if kwargs.get("event_type") == "tool_invocation_started":
            raise OSError("private low-level storage detail")
        return original_append(**kwargs)

    monkeypatch.setattr(tools._audit, "append", fail_start)
    tools._execute = lambda call: pytest.fail(  # type: ignore[method-assign]
        "tool must not execute after audit failure"
    )
    with pytest.raises(AuditIntegrityError, match="tool start audit write failed"):
        tools.invoke(
            call=parse_tool_call(
                _call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"})
            ),
            task_id="tsk_start-audit-fail",
            principal=PRINCIPAL,
        )
    assert target.read_text(encoding="utf-8") == "content"


def test_traversal_symlink_and_secret_files_are_rejected_or_excluded(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-marker", encoding="utf-8")
    (root / ".env").write_text("SECRET_MARKER", encoding="utf-8")
    (root / "id_rsa").write_text("PRIVATE_KEY_MARKER", encoding="utf-8")
    db, tools = _registry(tmp_path, root)

    for path in (
        "../outside.txt",
        f"{DRIVE_PREFIX}/outside.txt",
        ".env",
        "id_rsa",
        "secret.pem",
    ):
        with pytest.raises(ToolRequestError):
            parse_tool_call(_call("project.read_text", {"project_id": ROOT_ID, "path": path}))

    listing = tools.invoke(
        call=parse_tool_call(_call("project.list_files", {"project_id": ROOT_ID, "limit": 100})),
        task_id="tsk_secret-list",
        principal=PRINCIPAL,
    )
    assert listing.status == "success"
    entries = cast(dict[str, object], listing.data)["entries"]
    assert entries == []

    link = root / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    link_result = tools.invoke(
        call=parse_tool_call(
            _call("project.read_text", {"project_id": ROOT_ID, "path": "escape.txt"})
        ),
        task_id="tsk_symlink",
        principal=PRINCIPAL,
    )
    assert link_result.status == "failed"
    assert link_result.error_code == "symlink_denied"

    with db.connect() as connection:
        payloads = [
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM audit_events WHERE event_type LIKE 'tool_invocation_%'"
            )
        ]
    event_text = "\n".join(payloads)
    assert "SECRET_MARKER" not in event_text
    assert "PRIVATE_KEY_MARKER" not in event_text
    assert "outside-marker" not in event_text


def test_list_and_read_bounds_are_enforced(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    for index in range(MAX_LIST_ENTRIES + 5):
        (root / f"file-{index:03}.txt").write_text("x", encoding="utf-8")
    (root / "large.txt").write_bytes(b"x" * (MAX_READ_BYTES + 1))
    db, tools = _registry(tmp_path, root)

    listed = tools.invoke(
        call=parse_tool_call(
            _call(
                "project.list_files",
                {"project_id": ROOT_ID, "limit": MAX_LIST_ENTRIES},
            )
        ),
        task_id="tsk_bound-list",
        principal=PRINCIPAL,
    )
    assert listed.status == "success"
    listing = cast(dict[str, object], listed.data)
    assert len(cast(list[object], listing["entries"])) == MAX_LIST_ENTRIES
    assert listing["truncated"] is True
    assert listed.output_bytes <= MAX_TOOL_OUTPUT_BYTES

    read = tools.invoke(
        call=parse_tool_call(
            _call("project.read_text", {"project_id": ROOT_ID, "path": "large.txt"})
        ),
        task_id="tsk_bound-read",
        principal=PRINCIPAL,
    )
    assert read.status == "failed"
    assert read.error_code == "file_too_large"
    with pytest.raises(ToolRequestError):
        parse_tool_call(
            _call(
                "project.read_text",
                {
                    "project_id": ROOT_ID,
                    "path": "large.txt",
                    "max_bytes": MAX_READ_BYTES + 1,
                },
            )
        )


def test_git_status_uses_fixed_command_and_sanitizes_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    db, tools = _registry(tmp_path, root)

    class FailedProcess:
        stdout = io.BytesIO(b"fatal: raw stderr not captured\n")
        returncode = 128

        def wait(self, timeout: float) -> int:
            return self.returncode

    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kwargs: object) -> FailedProcess:
        calls.append(args)
        assert kwargs.get("shell") is False
        return FailedProcess()

    monkeypatch.setattr("eis.core.project_tools.subprocess.Popen", fake_popen)
    result = tools.invoke(
        call=parse_tool_call(_call("git.status", {"project_id": ROOT_ID})),
        task_id="tsk_git-failure",
        principal=PRINCIPAL,
    )
    assert result.status == "failed"
    assert result.error_code == "git_status_failed"
    assert len(calls) == 1
    assert calls[0][0] == "git"
    assert "status" in calls[0]
    assert calls[0][-1] == "--ignore-submodules=all"
    with db.connect() as connection:
        payloads = [
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM audit_events WHERE event_type LIKE 'tool_invocation_%'"
            )
        ]
    assert "raw stderr" not in "\n".join(payloads)


def test_git_status_success_returns_counts_not_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    class SuccessProcess:
        stdout = io.BytesIO(b"## main\n M src/app.py\n D .env\n")
        returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    monkeypatch.setattr(
        "eis.core.project_tools.subprocess.Popen",
        lambda *args, **kwargs: SuccessProcess(),
    )
    _, tools = _registry(tmp_path, root)
    result = tools.invoke(
        call=parse_tool_call(_call("git.status", {"project_id": ROOT_ID})),
        task_id="tsk_git-success",
        principal=PRINCIPAL,
    )
    assert result.status == "success"
    assert result.data == {
        "branch": "main",
        "counts": {"changed": 1, "added": 0, "deleted": 1, "conflicted": 0},
    }
    assert ".env" not in json.dumps(result.data)


def test_git_status_timeout_and_output_cap_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _, tools = _registry(tmp_path, root)

    class WaitingProcess:
        stdout = io.BytesIO(b"")
        returncode = -9
        killed = False

        def wait(self, timeout: float | None = None) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired("git", timeout or 0)
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            return None

    monkeypatch.setattr(
        "eis.core.project_tools.subprocess.Popen",
        lambda *args, **kwargs: WaitingProcess(),
    )
    timed_out = tools.invoke(
        call=parse_tool_call(_call("git.status", {"project_id": ROOT_ID})),
        task_id="tsk_git-timeout",
        principal=PRINCIPAL,
    )
    assert timed_out.status == "failed"
    assert timed_out.error_code == "git_timeout"

    class OversizedProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"x" * (MAX_TOOL_OUTPUT_BYTES + 8192))
            self.returncode = 0
            self.killed = False

        def wait(self, timeout: float) -> int:
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    oversized = OversizedProcess()
    monkeypatch.setattr(
        "eis.core.project_tools.subprocess.Popen",
        lambda *args, **kwargs: oversized,
    )
    too_large = tools.invoke(
        call=parse_tool_call(_call("git.status", {"project_id": ROOT_ID})),
        task_id="tsk_git-size",
        principal=PRINCIPAL,
    )
    assert too_large.status == "failed"
    assert too_large.error_code == "output_too_large"
    assert oversized.killed is True


def test_runtime_dispatch_is_explicit_and_model_proposals_are_not_actions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("content-marker", encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    project_policy = Policy.from_dict(
        {
            "version": "runtime-tool-v1",
            "default": "deny",
            "rules": [
                {
                    "id": "run-task",
                    "principal": "agent:eis.primary",
                    "action": "task.run",
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
            ],
        }
    )
    policy = PolicyService(
        PolicyEngine(project_policy),
        SqlitePolicyDecisionSink(database),
    )
    roots = ProjectRootStore(database)
    roots.register(ROOT_ID, root, actor="test")
    registry = ReadOnlyProjectTools(
        project_roots=roots,
        policy=policy,
        audit=AuditChainStore(database),
    )
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns_tool-runtime",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
            purpose="tool runtime test",
        )
    )
    provider = FakeProvider(
        '{"tool":"project.read_text","arguments":{"project_id":"demo","path":"app.py"}}'
    )
    runtime = SessionTaskRuntime(
        sessions,
        policy,
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=registry,
    )
    session = runtime.create_session(title="Explicit", project_id=ROOT_ID)

    proposal = asyncio.run(runtime.submit_task(session.session_id, "read app"))
    assert proposal.task.state == "failed"
    assert proposal.task.stop_reason == "malformed_response"
    assert proposal.proposal_text is None
    assert provider.calls == 1
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE event_type LIKE 'tool_invocation_%'"
            ).fetchone()[0]
            == 0
        )

    result = runtime.invoke_tool(
        session.session_id,
        _call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
    )
    assert result.task.state == "completed"
    assert result.tool.status == "success"
    assert cast(dict[str, object], result.tool.data)["text"] == "content-marker"
    assert provider.calls == 1

    denied = runtime.invoke_tool(
        session.session_id,
        _call("project.read_text", {"project_id": ROOT_ID, "path": "other.py"}),
    )
    assert denied.task.state == "failed"
    assert denied.tool.status == "denied"
    assert denied.tool.error_code == "policy_denied"

    with database.connect() as connection:
        events = [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]
        count = (
            connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
            if _table_exists(connection, "tool_calls")
            else 0
        )
        assert count == 0
    assert events.count("tool_invocation_started") == 1
    assert events.count("tool_invocation_completed") == 1
    assert events.count("tool_invocation_denied") == 1
    assert AuditChainStore(database).status().ok


def test_cli_explicit_tool_invocation_hides_file_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("cli-private-content", encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    session = sessions.create_session(title="CLI Tools", project_id=ROOT_ID, actor="test")
    policy = PolicyService(
        PolicyEngine(_policy()),
        SqlitePolicyDecisionSink(database),
    )
    roots = ProjectRootStore(database)
    roots.register(ROOT_ID, root, actor="test")
    tools = ReadOnlyProjectTools(
        project_roots=roots,
        policy=policy,
        audit=AuditChainStore(database),
    )
    runtime = SessionTaskRuntime(sessions, policy, None, project_tools=tools)
    monkeypatch.setattr(
        "eis.cli._tool_runtime",
        lambda session_id, policy_file=None: runtime,
    )
    call_json = json.dumps(
        _call("project.read_text", {"project_id": ROOT_ID, "path": "src/app.py"})
    )
    result = runner.invoke(
        app,
        [
            "task",
            "invoke-tool",
            session.session_id,
            "--call-json",
            call_json,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "status=success" in result.output
    assert "output_sha256=" in result.output
    assert "cli-private-content" not in result.output

    invalid = runner.invoke(
        app,
        [
            "task",
            "invoke-tool",
            session.session_id,
            "--call-json",
            json.dumps(_call("project.read_text", {"project_id": ROOT_ID, "path": "../outside"})),
        ],
    )
    assert invalid.exit_code == 1
    assert "invalid_or_unsafe_tool_call" in invalid.output
    assert "outside" not in invalid.output


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )
