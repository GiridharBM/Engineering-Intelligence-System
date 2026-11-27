# SPDX-License-Identifier: Apache-2.0

"""Durable provider consent storage."""

from __future__ import annotations

import json
from typing import cast

from eis.config import ConsentRecord, DataClass, ModelProviderName
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase


class ConsentStore:
    """SQLite-backed consent lookup, revocation and chained lifecycle events."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        database.migrate()
        self._chain = AuditChainStore(database)

    @property
    def database(self) -> SqliteDatabase:
        return self._database

    def grant(self, record: ConsentRecord, *, actor: str = "local_user") -> None:
        granted_at = _now_ms()
        with self._database.session(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO consents
                    (id, scope, subject, endpoint, data_classes, purpose,
                     retention_unknown, granted_at, expires_at, revoked_at, evidence_note)
                VALUES (?, 'model_provider', ?, NULL, ?, ?, 1, ?, ?, ?, NULL)
                """,
                (
                    record.consent_id,
                    record.provider,
                    json.dumps(sorted(record.data_classes)),
                    record.purpose,
                    granted_at,
                    record.expires_at_ms,
                    record.revoked_at_ms,
                ),
            )
            self._chain.append_in(
                connection,
                event_type="consent_granted",
                actor=actor,
                subject_type="consent",
                subject_id=record.consent_id,
                decision="grant",
                occurred_at_ms=granted_at,
                payload={
                    "scope": "model_provider",
                    "provider": record.provider,
                    "data_classes": sorted(record.data_classes),
                    "purpose": record.purpose,
                    "expires_at_ms": record.expires_at_ms,
                },
            )

    def ensure(self, record: ConsentRecord) -> None:
        """Insert an environment bootstrap only if the consent ID is new.

        A revoked row keeps its ID, so restarting with the same environment
        consent does not silently re-grant it.
        """
        granted_at = _now_ms()
        with self._database.session(immediate=True) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO consents
                    (id, scope, subject, endpoint, data_classes, purpose,
                     retention_unknown, granted_at, expires_at, revoked_at, evidence_note)
                VALUES (?, 'model_provider', ?, NULL, ?, ?, 1, ?, ?, ?, NULL)
                """,
                (
                    record.consent_id,
                    record.provider,
                    json.dumps(sorted(record.data_classes)),
                    record.purpose,
                    granted_at,
                    record.expires_at_ms,
                    record.revoked_at_ms,
                ),
            )
            if cursor.rowcount == 1:
                self._chain.append_in(
                    connection,
                    event_type="consent_granted",
                    actor="environment",
                    subject_type="consent",
                    subject_id=record.consent_id,
                    decision="grant",
                    occurred_at_ms=granted_at,
                    payload={
                        "scope": "model_provider",
                        "provider": record.provider,
                        "data_classes": sorted(record.data_classes),
                        "purpose": record.purpose,
                        "expires_at_ms": record.expires_at_ms,
                    },
                )

    def find_active(
        self,
        provider: ModelProviderName,
        data_class: DataClass,
        now_ms: int,
    ) -> ConsentRecord | None:
        if data_class == "C3":
            return None
        with self._database.session() as connection:
            rows = connection.execute(
                """
                SELECT id, subject, data_classes, purpose, expires_at, revoked_at
                FROM consents
                WHERE scope = 'model_provider'
                  AND subject = ?
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY granted_at DESC
                """,
                (provider, now_ms),
            ).fetchall()
        for row in rows:
            classes = _classes(row["data_classes"])
            if data_class not in classes:
                continue
            return ConsentRecord(
                consent_id=row["id"],
                provider=cast(ModelProviderName, row["subject"]),
                data_classes=classes,
                purpose=row["purpose"],
                expires_at_ms=row["expires_at"],
                revoked_at_ms=row["revoked_at"],
            )
        return None

    def revoke(self, consent_id: str, *, actor: str = "local_user") -> bool:
        revoked_at = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT subject FROM consents WHERE id = ?",
                (consent_id,),
            ).fetchone()
            if row is None:
                return False
            cursor = connection.execute(
                "UPDATE consents SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (revoked_at, consent_id),
            )
            if cursor.rowcount != 1:
                return False
            self._chain.append_in(
                connection,
                event_type="consent_revoked",
                actor=actor,
                subject_type="consent",
                subject_id=consent_id,
                decision="revoke",
                occurred_at_ms=revoked_at,
                payload={"provider": row["subject"]},
            )
            return True

    def list_all(self) -> list[ConsentRecord]:
        with self._database.session() as connection:
            rows = connection.execute(
                """
                SELECT id, subject, data_classes, purpose, expires_at, revoked_at
                FROM consents
                ORDER BY granted_at DESC
                """
            ).fetchall()
        return [
            ConsentRecord(
                consent_id=row["id"],
                provider=cast(ModelProviderName, row["subject"]),
                data_classes=_classes(row["data_classes"]),
                purpose=row["purpose"],
                expires_at_ms=row["expires_at"],
                revoked_at_ms=row["revoked_at"],
            )
            for row in rows
        ]


def _classes(value: object) -> frozenset[DataClass]:
    if not isinstance(value, str):
        return frozenset()
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return frozenset()
    return frozenset(cast(DataClass, item) for item in parsed if item in {"C0", "C1", "C2"})


def _now_ms() -> int:
    import time

    return time.time_ns() // 1_000_000
