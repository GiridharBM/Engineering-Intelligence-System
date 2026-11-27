# SPDX-License-Identifier: Apache-2.0

"""Durable project-root registration and race-resistant read tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.core.audit_chain import AuditIntegrityError
from eis.core.policy_service import PolicyService
from eis.core.project_roots import ProjectRootError, root_status
from eis.core.project_tools import (
    MAX_READ_BYTES,
    ReadOnlyProjectTools,
    ToolCall,
    parse_tool_call,
)
from eis.core.runtime import RuntimeStateError, SessionTaskRuntime
from eis.core.safe_access import SafeAccessError, read_file
from eis.policy import Policy, PolicyEngine, Principal
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

runner = CliRunner()
ROOT_ID = "demo"
PRINCIPAL = Principal("agent", "eis.primary")


def _store(
    tmp_path: Path,
    *,
    reserved_paths: tuple[Path, ...] = (),
) -> tuple[SqliteDatabase, ProjectRootStore]:
    database = SqliteDatabase(tmp_path / "db" / "eis.sqlite3")
    return database, ProjectRootStore(database, reserved_paths=reserved_paths)


def _policy() -> Policy:
    return Policy.from_dict(
        {
            "version": "roots-test-v1",
            "default": "deny",
            "rules": [
                {
                    "id": "list-projects",
                    "principal": "agent:eis.primary",
                    "action": "project.list",
                    "resource": "global",
                    "effect": "allow",
                },
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
                    "id": "read-sub",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "path:demo/sub",
                    "effect": "allow",
                },
            ],
        }
    )


def _tools(database: SqliteDatabase, store: ProjectRootStore) -> ReadOnlyProjectTools:
    policy = PolicyService(
        PolicyEngine(_policy()),
        SqlitePolicyDecisionSink(database),
    )
    return ReadOnlyProjectTools(
        project_roots=store,
        policy=policy,
        audit=AuditChainStore(database),
    )


def _call(tool: str, arguments: dict[str, object] | None = None) -> dict[str, object]:
    return {"tool": tool, "arguments": arguments or {}}


def _parsed(tool: str, arguments: dict[str, object] | None = None) -> ToolCall:
    return parse_tool_call(_call(tool, arguments))


def test_registration_persists_across_restart_and_audits_without_paths(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    database, store = _store(base)

    record = store.register(ROOT_ID, root, actor="test")
    assert record.project_id == ROOT_ID
    assert Path(record.root_path) == root
    assert len(record.root_identity) == 64

    reloaded = ProjectRootStore(database)
    assert reloaded.get(ROOT_ID) == record
    assert reloaded.list() == (record,)
    assert reloaded.register(ROOT_ID, root, actor="test") == record

    with database.connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM audit_events WHERE event_type LIKE 'project_root_%'"
            )
        ]
    assert [row["event_type"] for row in rows] == ["project_root_registered"]
    audit_text = json.dumps(rows, default=str)
    assert str(root) not in audit_text
    assert str(base) not in audit_text

    assert reloaded.remove(ROOT_ID, actor="test") is True
    assert reloaded.get(ROOT_ID) is None
    assert reloaded.remove(ROOT_ID, actor="test") is False
    with database.connect() as connection:
        event_types = [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]
    assert event_types == ["project_root_registered", "project_root_removed"]
    assert AuditChainStore(database).status().ok


def test_registration_rejects_missing_files_reserved_locations_and_overlaps(
    tmp_path: Path,
) -> None:
    base = tmp_path.resolve()
    database, store = _store(base, reserved_paths=(base / "data",))

    with pytest.raises(ProjectRootError) as missing:
        store.register("missing", base / "does-not-exist")
    assert missing.value.code == "root_not_found"

    file_path = base / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ProjectRootError) as file_error:
        store.register("file", file_path)
    assert file_error.value.code == "root_not_directory"

    reserved = base / "data" / "project"
    reserved.mkdir(parents=True)
    with pytest.raises(ProjectRootError) as reserved_error:
        store.register("reserved", reserved)
    assert reserved_error.value.code == "root_reserved"

    containing = base / "project"
    (containing / "runtime").mkdir(parents=True)
    with pytest.raises(ProjectRootError) as containing_error:
        ProjectRootStore(database, reserved_paths=(containing / "runtime",)).register(
            "containing", containing
        )
    assert containing_error.value.code == "root_reserved"

    with pytest.raises(ProjectRootError) as invalid:
        store.register("../bad", base / "other")
    assert invalid.value.code == "invalid_project_id"

    with pytest.raises(ProjectRootError) as broad:
        store.register("broad", Path(base.anchor))
    assert broad.value.code == "root_too_broad"

    first = base / "one"
    first.mkdir()
    other = base / "other"
    other.mkdir()
    store.register("one", first, actor="test")
    overlap_base = base / "overlap"
    overlap_base.mkdir()
    overlap_first = overlap_base / "one"
    overlap_first.mkdir()
    store.register("overlap-one", overlap_first, actor="test")
    nested = overlap_first / "sub"
    nested.mkdir()
    with pytest.raises(ProjectRootError) as overlap:
        store.register("nested", nested, actor="test")
    assert overlap.value.code == "root_overlap"
    with pytest.raises(ProjectRootError) as parent_overlap:
        store.register("parent", overlap_base, actor="test")
    assert parent_overlap.value.code == "root_overlap"
    with pytest.raises(ProjectRootError) as duplicate:
        store.register("duplicate", overlap_first, actor="test")
    assert duplicate.value.code == "root_already_registered"
    with pytest.raises(ProjectRootError) as bound:
        store.register("one", other, actor="test")
    assert bound.value.code == "project_already_registered"


def test_registration_rejects_symlink_and_junction_components(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    target = base / "real"
    target.mkdir()
    link = base / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation unavailable")
    database, store = _store(base)

    with pytest.raises(ProjectRootError) as linked_root:
        store.register("linked", link)
    assert linked_root.value.code == "root_symlink_denied"

    nested = target / "nested"
    nested.mkdir()
    through_link = link / "nested"
    with pytest.raises(ProjectRootError) as linked_component:
        store.register("through-link", through_link)
    assert linked_component.value.code == "root_symlink_denied"

    if os.name == "nt":
        junction = base / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("junction creation unavailable")
        with pytest.raises(ProjectRootError) as junction_error:
            ProjectRootStore(database).register("junction", junction)
        assert junction_error.value.code == "root_symlink_denied"


def test_moved_replaced_and_unavailable_roots_fail_closed(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    (root / "app.py").write_text("content-marker", encoding="utf-8")
    database, store = _store(base)
    store.register(ROOT_ID, root, actor="test")
    tools = _tools(database, store)

    first = tools.invoke(
        call=_parsed("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
        task_id="tsk_first",
        principal=PRINCIPAL,
    )
    assert first.status == "success"

    moved = base / "moved"
    root.rename(moved)
    unavailable = tools.invoke(
        call=_parsed("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
        task_id="tsk_moved",
        principal=PRINCIPAL,
    )
    assert unavailable.status == "failed"
    assert unavailable.error_code == "project_unavailable"
    assert root_status(store.get(ROOT_ID)) == "unavailable"  # type: ignore[arg-type]

    moved.rename(root)
    restored = tools.invoke(
        call=_parsed("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
        task_id="tsk_restored",
        principal=PRINCIPAL,
    )
    assert restored.status == "success"

    moved_again = base / "moved-again"
    root.rename(moved_again)
    root.mkdir()
    (root / "app.py").write_text("replacement-marker", encoding="utf-8")
    replaced = tools.invoke(
        call=_parsed("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
        task_id="tsk_replaced",
        principal=PRINCIPAL,
    )
    assert replaced.status == "failed"
    assert replaced.error_code == "project_root_replaced"
    assert root_status(store.get(ROOT_ID)) == "replaced"  # type: ignore[arg-type]

    (root / "app.py").unlink()
    root.rmdir()
    root.write_text("now a file", encoding="utf-8")
    assert root_status(store.get(ROOT_ID)) == "replaced"  # type: ignore[arg-type]


def test_runtime_resolves_the_registered_root_not_a_caller_path(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    (root / "app.py").write_text("registered-content", encoding="utf-8")
    database, store = _store(base)
    store.register(ROOT_ID, root, actor="test")
    policy = PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(database))
    runtime = SessionTaskRuntime(
        SessionTaskStore(database),
        policy,
        None,
        project_tools=_tools(database, store),
    )
    session = runtime.create_session(title="Registry", project_id=ROOT_ID)

    outcome = runtime.invoke_tool(
        session.session_id,
        _call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
    )
    assert outcome.tool.status == "success"
    assert outcome.tool.data == {
        "path": "app.py",
        "byte_count": 18,
        "text": "registered-content",
    }

    store.remove(ROOT_ID, actor="test")
    with pytest.raises(RuntimeStateError, match="not registered"):
        runtime.invoke_tool(
            session.session_id,
            _call("project.read_text", {"project_id": ROOT_ID, "path": "app.py"}),
        )


def test_registration_audit_failure_rolls_back_the_row(tmp_path: Path) -> None:
    base = tmp_path.resolve()
    first = base / "first"
    first.mkdir()
    second = base / "second"
    second.mkdir()
    database, store = _store(base)
    store.register("first", first, actor="test")
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET payload_json = '{}' WHERE seq = 1")

    with pytest.raises(AuditIntegrityError):
        store.register("second", second, actor="test")
    assert store.get("second") is None


def test_read_bound_survives_file_growth_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    (root / "app.py").write_text("small", encoding="utf-8")
    _, store = _store(base)
    record = store.register(ROOT_ID, root, actor="test")
    real_read = os.read

    def growing(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        return chunk + b"x" * (MAX_READ_BYTES + 1) if chunk else chunk

    monkeypatch.setattr("eis.core.safe_access.os.read", growing)
    with pytest.raises(SafeAccessError) as error:
        read_file(record, "app.py", MAX_READ_BYTES)
    assert error.value.code == "file_too_large"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle identity path")
def test_windows_detects_file_replacement_between_stat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("original", encoding="utf-8")
    replacement = base / "replacement"
    replacement.write_text("replacement-content", encoding="utf-8")
    _, store = _store(base)
    record = store.register(ROOT_ID, root, actor="test")
    real_open = os.open

    def swapping_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).casefold().endswith("app.py"):
            os.replace(replacement, target)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("eis.core.safe_access.os.open", swapping_open)
    with pytest.raises(SafeAccessError) as error:
        read_file(record, "app.py", MAX_READ_BYTES)
    assert error.value.code == "path_changed"


@pytest.mark.skipif(os.name != "posix", reason="POSIX openat path")
def test_posix_symlink_swap_is_rejected_during_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("original", encoding="utf-8")
    outside = base / "outside.txt"
    outside.write_text("outside-marker", encoding="utf-8")
    _, store = _store(base)
    record = store.register(ROOT_ID, root, actor="test")
    real_open = os.open

    def swapping_open(name: object, flags: int, *args: object, **kwargs: object) -> int:
        if kwargs.get("dir_fd") is not None and str(name) == "app.py":
            try:
                target.unlink()
                target.symlink_to(outside)
            except (OSError, NotImplementedError):
                pytest.skip("symlink creation unavailable")
        return real_open(name, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("eis.core.safe_access.os.open", swapping_open)
    with pytest.raises(SafeAccessError) as error:
        read_file(record, "app.py", MAX_READ_BYTES)
    assert error.value.code in {"symlink_denied", "project_root_replaced"}


def test_cli_registration_and_listing_hide_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    root = base / "project"
    root.mkdir()
    database, store = _store(base)
    monkeypatch.setattr("eis.cli._project_store", lambda: store)

    registered = runner.invoke(app, ["project", "register", ROOT_ID, str(root)])
    assert registered.exit_code == 0, registered.output
    assert str(root) not in registered.output

    listed = runner.invoke(app, ["project", "list"])
    assert listed.exit_code == 0, listed.output
    assert ROOT_ID in listed.output
    assert "status=available" in listed.output
    assert str(root) not in listed.output

    removed = runner.invoke(app, ["project", "remove", ROOT_ID])
    assert removed.exit_code == 0, removed.output
    assert store.get(ROOT_ID) is None
    assert AuditChainStore(database).status().ok


def test_cli_no_longer_accepts_a_caller_supplied_root(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "task",
            "invoke-tool",
            "ses_demo",
            str(tmp_path.resolve()),
            "--call-json",
            json.dumps(_call("project.list")),
        ],
    )
    assert result.exit_code == 2
