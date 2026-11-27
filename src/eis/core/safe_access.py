# SPDX-License-Identifier: Apache-2.0

"""Race-resistant, read-only filesystem access inside a registered root.

POSIX uses a file-descriptor walk with ``O_NOFOLLOW`` and ``dir_fd`` so the
containment check and the read refer to the same opened objects. Windows has
no equivalent ``openat`` in the Python standard library: each component is
checked for links/reparse points immediately before opening, and the opened
handle identity is compared with the pre-open identity. A narrow swap window
remains on Windows and is documented in the security documentation.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eis.core.project_roots import (
    ProjectRootRecord,
    is_junction,
    stat_identity,
)

_CHUNK = 65_536
_REPARSE_JUNCTION = 0xA0000003


class SafeAccessError(RuntimeError):
    """Raised with a stable, content-free error code; callers fail closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DirectoryEntry:
    name: str
    kind: Literal["file", "directory"]
    size: int | None


def read_file(record: ProjectRootRecord, relative: str, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` from a regular file inside the root."""
    if os.name == "posix":
        return _read_posix(record, relative, max_bytes)
    return _read_windows(record, relative, max_bytes)


def list_directory(
    record: ProjectRootRecord,
    relative: str,
    *,
    scan_limit: int,
) -> tuple[list[DirectoryEntry], bool]:
    """List direct children, stopping after ``scan_limit`` entries."""
    if os.name == "posix":
        return _list_posix(record, relative, scan_limit)
    return _list_windows(record, relative, scan_limit)


def verify_root(record: ProjectRootRecord) -> Path:
    """Verify the registered root identity and return its path."""
    if os.name == "posix":
        descriptor = _open_root_posix(record)
        os.close(descriptor)
        return Path(record.root_path)
    return _verify_windows_root(record)


def _read_posix(record: ProjectRootRecord, relative: str, max_bytes: int) -> bytes:
    root_descriptor = _open_root_posix(record)
    try:
        descriptor = _walk_posix(root_descriptor, relative, directory=False)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise SafeAccessError("not_a_regular_file")
            if metadata.st_size > max_bytes:
                raise SafeAccessError("file_too_large")
            return _read_bounded(descriptor, max_bytes)
        finally:
            os.close(descriptor)
    finally:
        os.close(root_descriptor)


def _list_posix(
    record: ProjectRootRecord,
    relative: str,
    scan_limit: int,
) -> tuple[list[DirectoryEntry], bool]:
    root_descriptor = _open_root_posix(record)
    try:
        descriptor = _walk_posix(root_descriptor, relative, directory=True)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise SafeAccessError("not_a_directory")
            entries: list[DirectoryEntry] = []
            scanned = 0
            truncated = False
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    scanned += 1
                    if scanned > scan_limit:
                        truncated = True
                        break
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISLNK(metadata.st_mode):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        entries.append(DirectoryEntry(entry.name, "directory", None))
                    elif stat.S_ISREG(metadata.st_mode):
                        entries.append(DirectoryEntry(entry.name, "file", metadata.st_size))
            return entries, truncated
        finally:
            os.close(descriptor)
    finally:
        os.close(root_descriptor)


def _open_root_posix(record: ProjectRootRecord) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(record.root_path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SafeAccessError("project_root_replaced") from None
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            raise SafeAccessError("project_unavailable") from None
        raise
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SafeAccessError("project_root_replaced")
        if stat_identity(metadata) != record.root_identity:
            raise SafeAccessError("project_root_replaced")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _walk_posix(root_descriptor: int, relative: str, *, directory: bool) -> int:
    parts = relative.split("/") if relative else []
    descriptor = os.dup(root_descriptor)
    try:
        for index, part in enumerate(parts):
            last = index == len(parts) - 1
            child = _open_child_posix(
                descriptor,
                part,
                directory=directory if last else True,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_child_posix(parent: int, name: str, *, directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)  # never block opening a FIFO/device
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SafeAccessError("symlink_denied") from None
        if exc.errno == errno.ENOENT:
            raise SafeAccessError("path_not_found") from None
        if exc.errno in {errno.ENOTDIR, errno.EISDIR}:
            raise SafeAccessError(
                "not_a_directory" if directory else "not_a_regular_file"
            ) from None
        raise


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(_CHUNK, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise SafeAccessError("file_too_large")
    return data


def _read_windows(record: ProjectRootRecord, relative: str, max_bytes: int) -> bytes:
    root = _verify_windows_root(record)
    target = _walk_windows(root, relative, directory=False)
    _assert_contained(root, target)
    try:
        before = os.lstat(target)
    except OSError:
        raise SafeAccessError("path_not_found") from None
    if not stat.S_ISREG(before.st_mode):
        raise SafeAccessError("not_a_regular_file")
    if before.st_size > max_bytes:
        raise SafeAccessError("file_too_large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise SafeAccessError("path_not_found") from None
        raise
    try:
        opened = os.fstat(descriptor)
        if stat_identity(opened) != stat_identity(before):
            raise SafeAccessError("path_changed")
        if not stat.S_ISREG(opened.st_mode):
            raise SafeAccessError("not_a_regular_file")
        data = _read_bounded(descriptor, max_bytes)
    finally:
        os.close(descriptor)
    # Re-check after the read so a detected swap discards the bytes.
    _verify_windows_root(record)
    _walk_windows(root, relative, directory=False)
    _assert_contained(root, target)
    return data


def _list_windows(
    record: ProjectRootRecord,
    relative: str,
    scan_limit: int,
) -> tuple[list[DirectoryEntry], bool]:
    root = _verify_windows_root(record)
    target = _walk_windows(root, relative, directory=True)
    _assert_contained(root, target)
    entries: list[DirectoryEntry] = []
    scanned = 0
    truncated = False
    try:
        with os.scandir(target) as iterator:
            for entry in iterator:
                scanned += 1
                if scanned > scan_limit:
                    truncated = True
                    break
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    entries.append(DirectoryEntry(entry.name, "directory", None))
                elif stat.S_ISREG(metadata.st_mode):
                    entries.append(DirectoryEntry(entry.name, "file", metadata.st_size))
    except OSError:
        raise SafeAccessError("not_a_directory") from None
    _verify_windows_root(record)
    return entries, truncated


def _verify_windows_root(record: ProjectRootRecord) -> Path:
    path = Path(record.root_path)
    try:
        metadata = os.lstat(path)
    except OSError:
        raise SafeAccessError("project_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or is_junction(path):
        raise SafeAccessError("project_root_replaced")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SafeAccessError("project_root_replaced")
    if stat_identity(metadata) != record.root_identity:
        raise SafeAccessError("project_root_replaced")
    return path


def _walk_windows(root: Path, relative: str, *, directory: bool) -> Path:
    parts = relative.split("/") if relative else []
    cursor = root
    for index, part in enumerate(parts):
        cursor = cursor / part
        try:
            metadata = os.lstat(cursor)
        except OSError:
            raise SafeAccessError("path_not_found") from None
        if stat.S_ISLNK(metadata.st_mode) or is_junction(cursor):
            raise SafeAccessError("symlink_denied")
        last = index == len(parts) - 1
        if not last or directory:
            if not stat.S_ISDIR(metadata.st_mode):
                raise SafeAccessError("not_a_directory")
        elif not stat.S_ISREG(metadata.st_mode):
            raise SafeAccessError("not_a_regular_file")
    if not parts and not directory:
        raise SafeAccessError("not_a_regular_file")
    return cursor


def _assert_contained(root: Path, target: Path) -> None:
    try:
        Path(os.path.realpath(target)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        raise SafeAccessError("path_escape_denied") from None


def _is_reparse(metadata: os.stat_result) -> bool:
    return getattr(metadata, "st_reparse_tag", 0) == _REPARSE_JUNCTION
