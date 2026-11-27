# SPDX-License-Identifier: Apache-2.0

"""Queryable read model for chained policy decisions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from eis.storage.db import SqliteDatabase


@dataclass(frozen=True)
class StoredPolicyDecision:
    decision_id: str
    policy_version: str
    policy_hash: str
    inputs_hash: str
    principal: str
    action: str
    resource: str
    effect: str
    reason: str
    rule_id: str
    context_source: str | None
    created_at_ms: int


class PolicyDecisionStore:
    """Read the policy-decision projection; audit_events remains authoritative."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        database.migrate()

    def list(self, *, limit: int = 100, offset: int = 0) -> tuple[StoredPolicyDecision, ...]:
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset must be non-negative")
        with self._database.session() as connection:
            rows = connection.execute(
                """SELECT decision_id, policy_version, policy_hash, inputs_hash,
                          principal, action, resource, effect, reason, rule_id,
                          context_source, created_at
                   FROM policy_decisions ORDER BY created_at DESC, decision_id LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def get(self, decision_id: str) -> StoredPolicyDecision | None:
        with self._database.session() as connection:
            row = connection.execute(
                """SELECT decision_id, policy_version, policy_hash, inputs_hash,
                          principal, action, resource, effect, reason, rule_id,
                          context_source, created_at
                   FROM policy_decisions WHERE decision_id = ?""",
                (decision_id,),
            ).fetchone()
        return _from_row(row) if row is not None else None


def _from_row(row: sqlite3.Row) -> StoredPolicyDecision:
    return StoredPolicyDecision(
        decision_id=row["decision_id"],
        policy_version=row["policy_version"],
        policy_hash=row["policy_hash"],
        inputs_hash=row["inputs_hash"],
        principal=row["principal"],
        action=row["action"],
        resource=row["resource"],
        effect=row["effect"],
        reason=row["reason"],
        rule_id=row["rule_id"],
        context_source=row["context_source"],
        created_at_ms=row["created_at"],
    )
