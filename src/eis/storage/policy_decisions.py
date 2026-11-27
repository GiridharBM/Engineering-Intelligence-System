# SPDX-License-Identifier: Apache-2.0

"""Chained persistence for deterministic policy decisions."""

from __future__ import annotations

from eis.policy import PolicyAuditRecord
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase


class SqlitePolicyDecisionSink:
    """Record policy decisions as idempotent `policy_decision` chain events.

    The event ID is the deterministic decision ID, so retrying the same
    evaluation returns the existing event instead of appending a duplicate.
    Context attributes and task IDs are omitted; `inputs_hash` still binds them.
    """

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._chain = AuditChainStore(database)

    def record(self, record: PolicyAuditRecord) -> None:
        source = record.context.get("source")
        occurred_at = _now_ms()
        with self._database.session(immediate=True) as connection:
            self._chain.append_in(
                connection,
                event_id=record.decision_id,
                event_type="policy_decision",
                actor=record.principal,
                subject_type="policy_decision",
                subject_id=record.decision_id,
                decision=record.effect,
                occurred_at_ms=occurred_at,
                payload={
                    "decision_id": record.decision_id,
                    "effect": record.effect,
                    "reason": record.reason,
                    "rule_id": record.rule_id,
                    "policy_version": record.policy_version,
                    "policy_hash": record.policy_hash,
                    "inputs_hash": record.inputs_hash,
                    "principal": record.principal,
                    "action": record.action,
                    "resource": record.resource,
                    "context_source": source if isinstance(source, str) else None,
                },
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO policy_decisions
                    (decision_id, policy_version, policy_hash, inputs_hash, principal,
                     action, resource, effect, reason, rule_id, context_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.policy_version,
                    record.policy_hash,
                    record.inputs_hash,
                    record.principal,
                    record.action,
                    record.resource,
                    record.effect,
                    record.reason,
                    record.rule_id,
                    source if isinstance(source, str) else None,
                    occurred_at,
                ),
            )


def _now_ms() -> int:
    from datetime import UTC, datetime

    return int(datetime.now(UTC).timestamp() * 1000)
