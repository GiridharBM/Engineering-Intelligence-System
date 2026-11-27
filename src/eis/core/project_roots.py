# SPDX-License-Identifier: Apache-2.0

"""Project-root identity and validation shared by storage and tool access.

A project root is selected only by an explicit local-user registration. The
model never supplies a filesystem path: tool calls carry a project identifier
that is resolved through the durable registry.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from eis.policy import Resource

ProjectRootStatus = Literal["available", "unavailable", "replaced"]


class ProjectRootError(ValueError):
    """Raised for an unsafe or invalid root registration; callers fail closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProjectRootRecord:
    """A registered root; the path is local metadata, never audit content."""

    project_id: str
    root_path: str
    root_identity: str
    created_at_ms: int
    updated_at_ms: int


class ProjectRootRepository(Protocol):
    def get(self, project_id: str) -> ProjectRootRecord | None: ...

    def list(self) -> tuple[ProjectRootRecord, ...]: ...


def stat_identity(value: os.stat_result) -> str:
    """Hash the platform directory identity used to detect replacement."""
    return hashlib.sha256(f"{value.st_dev}:{value.st_ino}".encode("ascii")).hexdigest()


def is_junction(path: Path) -> bool:
    """Detect a Windows junction, including on Python 3.11.

    ``Path.is_junction`` exists from 3.12; the reparse tag is available on
    older supported Windows builds.
    """
    check = getattr(path, "is_junction", None)
    if callable(check):
        return bool(check())
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return getattr(metadata, "st_reparse_tag", 0) == 0xA0000003


def canonical_root(path: Path) -> str:
    """Return the normalized absolute path stored in the registry."""
    text = str(path)
    return os.path.normcase(text) if os.name == "nt" else text


def validate_root(
    project_id: str,
    root: Path,
    reserved_paths: Iterable[Path] = (),
) -> tuple[str, str]:
    """Validate a proposed root and return its canonical path and identity.

    Rejects missing paths, non-directories, symlink/junction components,
    filesystem roots and locations overlapping runtime data or config.
    """
    try:
        Resource("project", identifier=project_id)
    except ValueError:
        raise ProjectRootError("invalid_project_id") from None
    candidate = Path(os.path.abspath(os.path.expanduser(str(root))))
    _assert_no_link_components(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise ProjectRootError("root_not_found") from None
    if not resolved.is_dir():
        raise ProjectRootError("root_not_directory")
    if resolved.parent == resolved:
        raise ProjectRootError("root_too_broad")
    for reserved in reserved_paths:
        reserved_resolved = Path(os.path.expanduser(str(reserved))).resolve()
        if _overlaps(resolved, reserved_resolved):
            raise ProjectRootError("root_reserved")
    try:
        metadata = resolved.stat()
    except OSError:
        raise ProjectRootError("root_not_found") from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProjectRootError("root_not_directory")
    return canonical_root(resolved), stat_identity(metadata)


def root_status(record: ProjectRootRecord) -> ProjectRootStatus:
    """Classify the registered path without following links."""
    path = Path(record.root_path)
    try:
        metadata = os.lstat(path)
    except OSError:
        return "unavailable"
    if stat.S_ISLNK(metadata.st_mode) or is_junction(path):
        return "replaced"
    if not stat.S_ISDIR(metadata.st_mode):
        return "replaced"
    if stat_identity(metadata) != record.root_identity:
        return "replaced"
    return "available"


def _assert_no_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not parts:
        raise ProjectRootError("root_not_found")
    cursor = Path(parts[0])
    for part in parts[1:]:
        cursor = cursor / part
        try:
            metadata = os.lstat(cursor)
        except FileNotFoundError:
            raise ProjectRootError("root_not_found") from None
        except OSError:
            raise ProjectRootError("root_unavailable") from None
        if stat.S_ISLNK(metadata.st_mode) or is_junction(cursor):
            raise ProjectRootError("root_symlink_denied")


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)
