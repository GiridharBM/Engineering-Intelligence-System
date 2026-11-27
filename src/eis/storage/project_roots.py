# SPDX-License-Identifier: Apache-2.0

"""Durable project-root registry with content-free audit events."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from eis.core.project_roots import (
    ProjectRootError,
    ProjectRootRecord,
    validate_root,
)
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase


class ProjectRootStore:
    """Register explicit local roots; a model can never add or move one."""

    def __init__(
        self,
        database: SqliteDatabase,
        *,
        reserved_paths: Iterable[Path] = (),
    ) -> None:
        self._database = database
        self._chain = AuditChainStore(database)
        self._reserved = tuple(Path(path) for path in reserved_paths)

    @property
    def database(self) -> SqliteDatabase:
        return self._database

    def register(
        self,
        project_id: str,
        root: Path,
        *,
        actor: str = "user",
    ) -> ProjectRootRecord:
        root_path, identity = validate_root(project_id, root, self._reserved)
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM project_roots WHERE project_id = ?", (project_id,)
            ).fetchone()
            if existing is not None:
                record = _from_row(existing)
                if record.root_path == root_path and record.root_identity == identity:
                    return record
                raise ProjectRootError("project_already_registered")
            for row in connection.execute("SELECT * FROM project_roots"):
                other = _from_row(row)
                if other.root_path == root_path:
                    raise ProjectRootError("root_already_registered")
                if _overlaps(Path(other.root_path), Path(root_path)):
                    raise ProjectRootError("root_overlap")
            connection.execute(
                """
                INSERT INTO project_roots
                    (project_id, root_path, root_identity, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, root_path, identity, now, now),
            )
            self._chain.append_in(
                connection,
                event_type="project_root_registered",
                actor=actor,
                subject_type="project_root",
                subject_id=project_id,
                decision="register",
                occurred_at_ms=now,
                payload={
                    "project_id": project_id,
                    "root_sha256": _sha256(root_path),
                    "root_identity": identity,
                },
            )
        return ProjectRootRecord(project_id, root_path, identity, now, now)

    def get(self, project_id: str) -> ProjectRootRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM project_roots WHERE project_id = ?", (project_id,)
            ).fetchone()
        return _from_row(row) if row is not None else None

    def list(self) -> tuple[ProjectRootRecord, ...]:
        with self._database.session() as connection:
            rows = connection.execute("SELECT * FROM project_roots ORDER BY project_id").fetchall()
        return tuple(_from_row(row) for row in rows)

    def remove(self, project_id: str, *, actor: str = "user") -> bool:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM project_roots WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                return False
            record = _from_row(row)
            connection.execute("DELETE FROM project_roots WHERE project_id = ?", (project_id,))
            self._chain.append_in(
                connection,
                event_type="project_root_removed",
                actor=actor,
                subject_type="project_root",
                subject_id=project_id,
                decision="remove",
                occurred_at_ms=now,
                payload={
                    "project_id": project_id,
                    "root_sha256": _sha256(record.root_path),
                    "root_identity": record.root_identity,
                },
            )
        return True


def _from_row(row: sqlite3.Row) -> ProjectRootRecord:
    return ProjectRootRecord(
        project_id=row["project_id"],
        root_path=row["root_path"],
        root_identity=row["root_identity"],
        created_at_ms=row["created_at"],
        updated_at_ms=row["updated_at"],
    )


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
