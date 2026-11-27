"""Repository boundary and privacy regression checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eis.privacy import RUNTIME_DIRS, RUNTIME_SUFFIXES, VISION_FILE, find_violations

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _require_git() -> None:
    # Clean copies, sdist extracts and source archives have no work tree, and a
    # copy may sit inside an unrelated repository; these assertions only make
    # sense against the EIS work tree itself (CI always provides one).
    result = _git("rev-parse", "--show-toplevel")
    if result.returncode != 0:
        pytest.skip("not a git work tree")
    if Path(result.stdout.strip()).resolve() != ROOT.resolve():
        pytest.skip("not the EIS work tree")


def test_git_root_is_exactly_project_root() -> None:
    _require_git()
    result = _git("rev-parse", "--show-toplevel")
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT.resolve()


def test_no_submodules_are_configured() -> None:
    assert not (ROOT / ".gitmodules").exists()
    _require_git()
    assert _git("rev-parse", "--show-toplevel").returncode == 0


def test_vision_document_is_never_tracked() -> None:
    _require_git()
    tracked = _git("ls-files", "--error-unmatch", VISION_FILE)
    assert tracked.returncode != 0, "vision document must not be tracked"
    if (ROOT / VISION_FILE).exists():
        ignored = _git("check-ignore", "--quiet", VISION_FILE)
        assert ignored.returncode == 0, "vision document must be ignored"


def test_runtime_paths_are_ignored() -> None:
    _require_git()
    paths = (
        "data/eis.sqlite3",
        "artifacts/sample.json",
        "logs/eis.log",
        "keys/api-token",
        "backups/snapshot.db",
        "quarantine/skill.zip",
        "cache/state.bin",
    )
    for path in paths:
        result = _git("check-ignore", "--quiet", path)
        assert result.returncode == 0, f"runtime path is not ignored: {path}"


def test_candidate_files_contain_no_runtime_data() -> None:
    _require_git()
    result = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    assert result.returncode == 0, result.stderr
    names = [name for name in result.stdout.split("\0") if name]
    for name in names:
        parts = Path(name).parts
        assert not any(part in RUNTIME_DIRS for part in parts), name
        assert not name.endswith(RUNTIME_SUFFIXES), name


def test_privacy_guard_reports_no_violations() -> None:
    assert find_violations(ROOT) == []
