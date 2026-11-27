# SPDX-License-Identifier: Apache-2.0

"""Bounded, read-only repository walker for explicitly registered roots.

The walker resolves a project ID through the durable root registry and only
lists directory metadata through `safe_access.list_directory`. It never reads
file contents, never joins an absolute host path, and never accepts a root path
from a caller or model. Symlinks and junctions are skipped fail-closed by the
safe-access layer; reserved and configured exclusions are filtered here.

Limits are deterministic: admitted file count, maximum directory depth, the sum
of admitted file sizes, and a per-directory entry cap. A result reports whether
it is incomplete and why. Scan results contain relative paths, kinds and sizes
only; no source content is stored or returned.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

from eis.core.project_roots import ProjectRootRecord, ProjectRootRepository
from eis.core.safe_access import SafeAccessError, list_directory, verify_root

MAX_WALK_FILES = 100_000
MAX_WALK_DEPTH = 128
MAX_WALK_BYTES = 8 * 1024 * 1024 * 1024
MAX_EXCLUDE_PATTERNS = 100
MAX_EXCLUDE_PATTERN_CHARS = 256
PER_DIRECTORY_ENTRY_LIMIT = 5_000

LimitReason = Literal["file_count", "depth", "bytes", "entry_limit"]

# Mirrors the read-only tool exclusions and adds common cache/build outputs.
# `.git` internals are always excluded; the M2 Git adapter will read metadata
# through its own bounded interface, not by walking `.git`.
_DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".eis",
        ".ssh",
        ".aws",
        ".gnupg",
        "keys",
        "secrets",
        "credentials",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".cache",
        ".idea",
        ".vscode",
        "dist",
        "build",
        "target",
        "coverage",
        "htmlcov",
    }
)
_DEFAULT_EXCLUDED_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pyd",
        ".class",
        ".o",
        ".obj",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".zip",
        ".tar",
        ".tgz",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".keystore",
        ".jks",
        ".sqlite3",
        ".db",
        ".db-wal",
        ".db-shm",
    }
)


class WalkError(RuntimeError):
    """A stable, content-free walker failure; callers fail closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WalkConfig:
    """Per-invocation scan limits and additive exclusion patterns."""

    max_files: int = 10_000
    max_depth: int = 32
    max_total_bytes: int = 256 * 1024 * 1024
    exclude_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= MAX_WALK_FILES:
            raise ValueError("max_files is outside the supported range")
        if not 0 <= self.max_depth <= MAX_WALK_DEPTH:
            raise ValueError("max_depth is outside the supported range")
        if not 0 <= self.max_total_bytes <= MAX_WALK_BYTES:
            raise ValueError("max_total_bytes is outside the supported range")
        if len(self.exclude_patterns) > MAX_EXCLUDE_PATTERNS:
            raise ValueError("too many exclusion patterns")
        for pattern in self.exclude_patterns:
            if (
                not pattern
                or len(pattern) > MAX_EXCLUDE_PATTERN_CHARS
                or "\x00" in pattern
                or any(character in pattern for character in "\r\n")
            ):
                raise ValueError("invalid exclusion pattern")


@dataclass(frozen=True)
class WalkFile:
    path: str
    size: int


@dataclass(frozen=True)
class WalkResult:
    project_id: str
    files: tuple[WalkFile, ...]
    directories: int
    total_bytes: int
    excluded: int
    truncated: bool
    limit_reason: LimitReason | None
    case_collisions: tuple[tuple[str, ...], ...]

    @property
    def file_count(self) -> int:
        return len(self.files)


def walk_project(
    repository: ProjectRootRepository,
    project_id: str,
    config: WalkConfig | None = None,
) -> WalkResult:
    """Scan one registered root with deterministic, fail-closed limits."""
    selected = config or WalkConfig()
    record = repository.get(project_id)
    if record is None:
        raise WalkError("project_unavailable")
    _verify(record)

    files: list[WalkFile] = []
    directories = 0
    excluded = 0
    total_bytes = 0
    truncated = False
    limit_reason: LimitReason | None = None
    depth_limited = False
    queue: list[tuple[str, int]] = [("", 0)]

    while queue and not truncated:
        relative, depth = queue.pop(0)
        directories += 1
        try:
            entries, listing_truncated = list_directory(
                record,
                relative,
                scan_limit=PER_DIRECTORY_ENTRY_LIMIT,
            )
        except SafeAccessError as exc:
            raise WalkError(exc.code) from None
        ordered = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
        for entry in ordered:
            child = f"{relative}/{entry.name}" if relative else entry.name
            if _entry_name_unsafe(entry.name) or _is_excluded(
                entry.name, child, selected.exclude_patterns
            ):
                excluded += 1
                continue
            if entry.kind == "directory":
                if depth >= selected.max_depth:
                    depth_limited = True
                    continue
                queue.append((child, depth + 1))
                continue
            size = entry.size or 0
            if len(files) >= selected.max_files:
                truncated = True
                limit_reason = "file_count"
                break
            if total_bytes + size > selected.max_total_bytes:
                truncated = True
                limit_reason = "bytes"
                break
            files.append(WalkFile(path=child, size=size))
            total_bytes += size
        if listing_truncated and not truncated:
            truncated = True
            limit_reason = "entry_limit"

    if depth_limited and not truncated:
        truncated = True
        limit_reason = "depth"

    _verify(record)
    files.sort(key=lambda item: (item.path.casefold(), item.path))
    collisions = _case_collisions(files)
    return WalkResult(
        project_id=project_id,
        files=tuple(files),
        directories=directories,
        total_bytes=total_bytes,
        excluded=excluded,
        truncated=truncated,
        limit_reason=limit_reason,
        case_collisions=collisions,
    )


def _verify(record: ProjectRootRecord) -> None:
    try:
        verify_root(record)
    except SafeAccessError as exc:
        raise WalkError(exc.code) from None


def _entry_name_unsafe(name: str) -> bool:
    return (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or any(character in name for character in "\r\n")
    )


def _is_excluded(name: str, relative: str, patterns: tuple[str, ...]) -> bool:
    folded_name = name.casefold()
    folded_path = relative.casefold()
    if folded_name in _DEFAULT_EXCLUDED_NAMES:
        return True
    if folded_name == ".env" or folded_name.startswith(".env."):
        return True
    if any(folded_name.endswith(suffix) for suffix in _DEFAULT_EXCLUDED_SUFFIXES):
        return True
    for pattern in patterns:
        folded_pattern = pattern.casefold()
        if fnmatch.fnmatchcase(folded_name, folded_pattern) or fnmatch.fnmatchcase(
            folded_path, folded_pattern
        ):
            return True
    return False


def _case_collisions(files: list[WalkFile]) -> tuple[tuple[str, ...], ...]:
    groups: dict[str, list[str]] = {}
    for item in files:
        groups.setdefault(item.path.casefold(), []).append(item.path)
    return tuple(tuple(sorted(group)) for _key, group in sorted(groups.items()) if len(group) > 1)
