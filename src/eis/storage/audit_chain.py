# SPDX-License-Identifier: Apache-2.0

"""SQLite-backed append-only audit chain."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import replace
from typing import cast

from eis.core.audit_chain import (
    GENESIS_PREV_HASH,
    AuditEvent,
    AuditIntegrityError,
    ChainStatus,
    canonical_event_json,
    compute_event_hash,
    verify_chain,
)
from eis.storage.db import SqliteDatabase


class AuditChainStore:
    """Append and verify hash-linked audit events.

    Every append re-verifies the existing chain, so a broken chain blocks
    further audit-sensitive writes. This is tamper evidence, not tamper-proof
    storage: an actor who can rewrite the database and its triggers can forge a
    new chain, but cannot alter this one undetected.
    """

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        database.migrate()

    @property
    def database(self) -> SqliteDatabase:
        return self._database

    def append(
        self,
        *,
        event_type: str,
        actor: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, object],
        decision: str | None = None,
        occurred_at_ms: int | None = None,
        event_id: str | None = None,
    ) -> AuditEvent:
        with self._database.session(immediate=True) as connection:
            return self.append_in(
                connection,
                event_type=event_type,
                actor=actor,
                subject_type=subject_type,
                subject_id=subject_id,
                payload=payload,
                decision=decision,
                occurred_at_ms=occurred_at_ms,
                event_id=event_id,
            )

    def append_in(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        actor: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, object],
        decision: str | None = None,
        occurred_at_ms: int | None = None,
        event_id: str | None = None,
    ) -> AuditEvent:
        """Append in the caller's transaction; the caller must write-lock first.

        A caller-supplied ``event_id`` makes the append idempotent: an existing
        event with the same fields is returned without appending, while a
        different event under the same ID fails closed.
        """
        # shortcut: full-chain verification per append; add a checkpoint/anchor when volume grows.
        events = self._read_events(connection)
        status = verify_chain(events)
        if not status.ok:
            raise AuditIntegrityError(status.failure or "audit chain integrity failure")

        if event_id is not None:
            existing = next((item for item in events if item.event_id == event_id), None)
            if existing is not None:
                if _same_event(
                    existing,
                    event_type=event_type,
                    actor=actor,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    decision=decision,
                    payload=payload,
                ):
                    return existing
                raise AuditIntegrityError(f"audit event ID collision: {event_id}")

        seq = events[-1].seq + 1 if events else 1
        prev_hash = events[-1].event_hash if events else GENESIS_PREV_HASH
        draft = AuditEvent(
            seq=seq,
            event_id=event_id or f"evt_{uuid.uuid4().hex}",
            occurred_at_ms=_now_ms() if occurred_at_ms is None else occurred_at_ms,
            actor=actor,
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            decision=decision,
            payload=payload,
            prev_hash=prev_hash,
            event_hash="",
        )
        event = replace(
            draft,
            event_hash=compute_event_hash(prev_hash, draft.canonical_body()),
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (seq, id, occurred_at, actor, event_type, subject_type,
                 subject_id, decision, payload_json, prev_hash, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.seq,
                event.event_id,
                event.occurred_at_ms,
                event.actor,
                event.event_type,
                event.subject_type,
                event.subject_id,
                event.decision,
                canonical_event_json(event.payload),
                event.prev_hash,
                event.event_hash,
            ),
        )
        return event

    def status(self) -> ChainStatus:
        try:
            with self._database.session() as connection:
                events = self._read_events(connection)
        except (json.JSONDecodeError, TypeError, ValueError, sqlite3.DatabaseError) as exc:
            return ChainStatus(
                ok=False,
                event_count=0,
                head_hash=GENESIS_PREV_HASH,
                failure=f"unreadable audit event: {type(exc).__name__}",
            )
        return verify_chain(events)

    def require_intact(self) -> ChainStatus:
        status = self.status()
        if not status.ok:
            raise AuditIntegrityError(status.failure or "audit chain integrity failure")
        return status

    def _read_events(self, connection: sqlite3.Connection) -> tuple[AuditEvent, ...]:
        rows = connection.execute(
            """
            SELECT seq, id, occurred_at, actor, event_type, subject_type,
                   subject_id, decision, payload_json, prev_hash, hash
            FROM audit_events
            ORDER BY seq
            """
        ).fetchall()
        events: list[AuditEvent] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError("audit payload is not an object")
            events.append(
                AuditEvent(
                    seq=row["seq"],
                    event_id=row["id"],
                    occurred_at_ms=row["occurred_at"],
                    actor=row["actor"],
                    event_type=row["event_type"],
                    subject_type=row["subject_type"],
                    subject_id=row["subject_id"],
                    decision=row["decision"],
                    payload=cast(dict[str, object], payload),
                    prev_hash=row["prev_hash"],
                    event_hash=row["hash"],
                )
            )
        return tuple(events)


def _same_event(
    event: AuditEvent,
    *,
    event_type: str,
    actor: str,
    subject_type: str,
    subject_id: str,
    decision: str | None,
    payload: dict[str, object],
) -> bool:
    return (
        event.event_type == event_type
        and event.actor == actor
        and event.subject_type == subject_type
        and event.subject_id == subject_id
        and event.decision == decision
        and dict(event.payload) == payload
    )


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
