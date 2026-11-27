# SPDX-License-Identifier: Apache-2.0

"""Repository privacy guard used by tests, pre-commit and CI.

The guard is a second line of defence behind ``.gitignore``; it fails when a
private-looking path, runtime data file or secret-shaped value is present in a
candidate file (tracked or untracked-but-not-ignored).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

VISION_FILE = "EIS_Complete_Project_Vision_and_Technical_Plan.txt"

RUNTIME_DIRS = frozenset({"data", "artifacts", "logs", "backups", "keys", "quarantine", "cache"})
RUNTIME_SUFFIXES = (
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".db",
    ".db-wal",
    ".db-shm",
)
TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".toml",
        ".json",
        ".yaml",
        ".yml",
        ".cfg",
        ".ini",
        ".sh",
        ".ps1",
        ".cff",
    }
)
NO_SUFFIX_TEXT = frozenset({".gitignore", ".gitattributes", "LICENSE", "NOTICE"})
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".uv-cache",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("absolute_drive_path", re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/](?![\\/])")),
    ("absolute_home_path", re.compile(r"(?i)(?:/home/[a-z0-9._-]+/|/users/[a-z0-9._-]+/)")),
    ("unc_path", re.compile(r"\\\\[a-z0-9._-]+\\[a-z0-9$._-]+", re.IGNORECASE)),
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*" + "PRIVATE KEY-----")),
)


@dataclass(frozen=True)
class Violation:
    """A privacy-gate finding. Secret values are never included."""

    path: str
    rule: str
    line: int = 0

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"{location}: {self.rule}"


def _git_candidates(root: Path) -> list[Path] | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    candidates: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", errors="replace")
        path = root / name
        if path.is_file():
            candidates.append(path)
    return candidates


def _fallback_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file():
                candidates.append(path)
    return candidates


def _is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name in NO_SUFFIX_TEXT


def _scan_file(path: Path, root: Path) -> list[Violation]:
    relative = path.relative_to(root).as_posix()
    parts = path.relative_to(root).parts
    if any(part in RUNTIME_DIRS for part in parts):
        return [Violation(relative, "runtime_directory")]
    if path.suffix.lower() in RUNTIME_SUFFIXES:
        return [Violation(relative, "runtime_data_file")]
    if not _is_text(path):
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    violations: list[Violation] = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in _PATH_PATTERNS:
            if pattern.search(line):
                violations.append(Violation(relative, rule, number))
        for rule, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                violations.append(Violation(relative, rule, number))
    return violations


def find_violations(root: Path, *, use_git: bool = True) -> list[Violation]:
    """Return privacy violations for a repository work tree."""
    resolved = root.resolve()
    violations: list[Violation] = []

    if (resolved / ".gitmodules").exists():
        violations.append(Violation(".gitmodules", "submodules_forbidden"))

    if use_git and (resolved / ".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", VISION_FILE],
            cwd=resolved,
            check=False,
            capture_output=True,
        )
        if tracked.returncode == 0:
            violations.append(Violation(VISION_FILE, "vision_document_tracked"))

    candidates = _git_candidates(resolved) if use_git else None
    if candidates is None:
        candidates = _fallback_candidates(resolved)

    for path in candidates:
        violations.extend(_scan_file(path, resolved))

    return sorted(violations, key=lambda item: (item.path, item.line, item.rule))
