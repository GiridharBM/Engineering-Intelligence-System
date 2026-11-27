# SPDX-License-Identifier: Apache-2.0

"""Bounded read-only repository walker tests for M2 project discovery."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.config import load_settings
from eis.core.repo_walker import WalkConfig, WalkError, walk_project
from eis.storage.db import SqliteDatabase, default_database
from eis.storage.project_roots import ProjectRootStore

ROOT_ID = "demo"
runner = CliRunner()
CONTENT_MARKER = "source-content-must-not-be-returned"


def _store(tmp_path: Path) -> tuple[SqliteDatabase, ProjectRootStore]:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    return database, ProjectRootStore(database)


def _fixture(tmp_path: Path) -> tuple[SqliteDatabase, ProjectRootStore, Path]:
    root = tmp_path / "project"
    root.mkdir()
    database, store = _store(tmp_path)
    store.register(ROOT_ID, root, actor="test")
    return database, store, root


def test_walk_resolves_registered_root_and_applies_exclusions(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "app.py").write_text(CONTENT_MARKER, encoding="utf-8")
    (root / "notes.txt").write_text("notes\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text("docs\n", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "app.log").write_text("log\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("git\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=not-read\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "key.txt").write_text("secret\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep").mkdir()
    (root / "node_modules" / "dep" / "index.js").write_text("dep\n", encoding="utf-8")
    (root / "bundle.zip").write_text("zip\n", encoding="utf-8")

    result = walk_project(
        store,
        ROOT_ID,
        WalkConfig(exclude_patterns=("*.log", "docs", "logs")),
    )

    paths = [item.path for item in result.files]
    assert paths == ["app.py", "notes.txt", "src/main.py"]
    for excluded in (
        ".git/config",
        ".env",
        "secrets/key.txt",
        "node_modules/dep/index.js",
        "bundle.zip",
        "docs/readme.md",
        "logs/app.log",
    ):
        assert excluded not in paths
    assert result.excluded >= 6
    assert result.truncated is False
    assert result.limit_reason is None
    assert result.case_collisions == ()
    assert result.directories == 2  # root and src
    assert result.total_bytes == sum((root / path).stat().st_size for path in paths)
    assert CONTENT_MARKER not in repr(result)


def test_walk_skips_symlink_and_junction_escape(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "inside.txt").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    try:
        (root / "escape.txt").symlink_to(outside / "outside.txt")
        (root / "escape-dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")

    result = walk_project(store, ROOT_ID)

    paths = [item.path for item in result.files]
    assert "inside.txt" in paths
    assert "escape.txt" not in paths
    assert "escape-dir/outside.txt" not in paths
    assert not any(path.startswith("escape") for path in paths)


def test_missing_replaced_and_unknown_roots_fail_closed(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "app.py").write_text("content\n", encoding="utf-8")

    assert walk_project(store, ROOT_ID).file_count == 1

    moved = tmp_path / "moved"
    root.rename(moved)
    with pytest.raises(WalkError) as unavailable:
        walk_project(store, ROOT_ID)
    assert unavailable.value.code == "project_unavailable"

    moved.rename(root)
    replacement = tmp_path / "replacement"
    root.rename(replacement)
    root.mkdir()
    (root / "app.py").write_text("replacement\n", encoding="utf-8")
    with pytest.raises(WalkError) as replaced:
        walk_project(store, ROOT_ID)
    assert replaced.value.code == "project_root_replaced"

    with pytest.raises(WalkError) as unknown:
        walk_project(store, "not-registered")
    assert unknown.value.code == "project_unavailable"


def test_file_count_limit_reports_incomplete(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")

    result = walk_project(store, ROOT_ID, WalkConfig(max_files=2))

    assert [item.path for item in result.files] == ["a.txt", "b.txt"]
    assert result.truncated is True
    assert result.limit_reason == "file_count"


def test_depth_limit_reports_incomplete(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "root.txt").write_text("root\n", encoding="utf-8")
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "b" / "c" / "deep.txt").write_text("deep\n", encoding="utf-8")

    result = walk_project(store, ROOT_ID, WalkConfig(max_depth=1))

    paths = [item.path for item in result.files]
    assert "root.txt" in paths
    assert "a/b/c/deep.txt" not in paths
    assert result.truncated is True
    assert result.limit_reason == "depth"


def test_byte_limit_reports_incomplete(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "a.txt").write_text("12345", encoding="utf-8")
    (root / "b.txt").write_text("67890", encoding="utf-8")

    result = walk_project(store, ROOT_ID, WalkConfig(max_total_bytes=7))

    assert [item.path for item in result.files] == ["a.txt"]
    assert result.total_bytes == 5
    assert result.truncated is True
    assert result.limit_reason == "bytes"


def test_deterministic_output_and_sorted_paths(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    for name in ("Zed.py", "alpha.py", "beta.py"):
        (root / name).write_text(name, encoding="utf-8")

    first = walk_project(store, ROOT_ID)
    second = walk_project(store, ROOT_ID)

    assert first == second
    assert [item.path for item in first.files] == ["alpha.py", "beta.py", "Zed.py"]


def test_case_collision_is_reported_deterministically(tmp_path: Path) -> None:
    _, store, root = _fixture(tmp_path)
    (root / "Alpha.py").write_text("a", encoding="utf-8")
    (root / "alpha.py").write_text("b", encoding="utf-8")
    if len(set(os.listdir(root))) < 2:
        pytest.skip("case-sensitive filesystem required")

    result = walk_project(store, ROOT_ID)

    assert result.file_count == 2
    assert result.case_collisions == (("Alpha.py", "alpha.py"),)
    assert [item.path for item in result.files] == ["Alpha.py", "alpha.py"]


def test_invalid_walk_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        WalkConfig(max_files=0)
    with pytest.raises(ValueError):
        WalkConfig(max_depth=-1)
    with pytest.raises(ValueError):
        WalkConfig(max_total_bytes=-1)
    with pytest.raises(ValueError):
        WalkConfig(exclude_patterns=("",))


def test_cli_project_scan_reports_summary_without_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "config"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(CONTENT_MARKER, encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("git\n", encoding="utf-8")
    (root / "run.log").write_text("log\n", encoding="utf-8")
    ProjectRootStore(default_database(load_settings(require_provider=False))).register(
        ROOT_ID, root, actor="test"
    )

    result = runner.invoke(app, ["project", "scan", ROOT_ID, "--exclude", "*.log"])

    assert result.exit_code == 0, result.output
    assert "scan=complete" in result.output
    assert "files=1" in result.output
    assert "excluded=" in result.output
    assert "case_collisions=0" in result.output
    assert "limit_reason=none" in result.output
    assert str(root) not in result.output
    assert CONTENT_MARKER not in result.output

    (root / "second.py").write_text("second\n", encoding="utf-8")
    limited = runner.invoke(
        app, ["project", "scan", ROOT_ID, "--max-files", "1", "--exclude", "*.log"]
    )
    assert limited.exit_code == 0, limited.output
    assert "scan=incomplete" in limited.output
    assert "files=1" in limited.output
    assert "limit_reason=file_count" in limited.output


@pytest.mark.skipif(
    os.environ.get("EIS_RUN_PERF") != "1",
    reason="set EIS_RUN_PERF=1 to run the 50k-file scan benchmark",
)
def test_large_fixture_scan_meets_nfr01_budget(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    root = tmp_path / "large"
    root.mkdir()
    for directory in range(100):
        folder = root / f"d{directory:03d}"
        folder.mkdir()
        for index in range(500):
            (folder / f"f{index:03d}.txt").touch()
    store.register(ROOT_ID, root, actor="test")

    started = time.monotonic()
    result = walk_project(
        store,
        ROOT_ID,
        WalkConfig(max_files=50_000, max_depth=4, max_total_bytes=256 * 1024 * 1024),
    )
    elapsed = time.monotonic() - started

    assert result.file_count == 50_000
    assert result.directories == 101
    assert result.truncated is False
    assert elapsed < 60.0
    print(f"large_fixture_scan files={result.file_count} elapsed_seconds={elapsed:.3f}")
