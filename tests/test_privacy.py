"""Privacy guard detection tests using synthetic violations."""

from __future__ import annotations

from pathlib import Path

from eis.privacy import Violation, find_violations


def _rules(root: Path) -> set[str]:
    return {violation.rule for violation in find_violations(root, use_git=False)}


def test_clean_directory_has_no_violations(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("clean\n", encoding="utf-8")
    assert find_violations(tmp_path, use_git=False) == []


def test_detects_absolute_drive_path(tmp_path: Path) -> None:
    leak = "C:" + "\\" + "Users" + "\\" + "someone"
    (tmp_path / "leak.txt").write_text(leak, encoding="utf-8")
    assert "absolute_drive_path" in _rules(tmp_path)


def test_detects_home_path(tmp_path: Path) -> None:
    leak = "/home/" + "someone" + "/project"
    (tmp_path / "leak.txt").write_text(leak, encoding="utf-8")
    assert "absolute_home_path" in _rules(tmp_path)


def test_detects_secret_shaped_values(tmp_path: Path) -> None:
    aws = "AK" + "IA" + "A" * 16
    github = "gh" + "p_" + "a" * 36
    private_key = "-----BEGIN " + "RSA PRIVATE KEY-----"
    (tmp_path / "leak.txt").write_text(
        f"{aws}\n{github}\n{private_key}\n",
        encoding="utf-8",
    )
    rules = _rules(tmp_path)
    assert {"aws_access_key", "github_token", "private_key"} <= rules


def test_detects_runtime_data_file(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "eis.sqlite3").write_bytes(b"")
    assert "runtime_directory" in _rules(tmp_path)


def test_violation_never_contains_the_secret_value() -> None:
    violation = Violation("leak.txt", "aws_access_key", 4)
    assert "AKIA" not in str(violation)
