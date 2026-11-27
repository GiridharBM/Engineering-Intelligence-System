# SPDX-License-Identifier: Apache-2.0

"""SQLite audit sink for content-free egress and model-call records."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from eis.core.audit import EgressDecision, ModelCallAudit
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase


class SqliteAuditSink:
    """Durable AuditSink that also appends each event to the audit chain."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        database.migrate()
        self._chain = AuditChainStore(database)

    @property
    def database(self) -> SqliteDatabase:
        return self._database

    def record_egress(self, decision: EgressDecision) -> None:
        occurred_at = _to_ms(decision.occurred_at)
        with self._database.session(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO egress_decisions
                    (id, provider, model, data_class, decision, reason, consent_id, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"egr_{uuid.uuid4().hex}",
                    decision.provider,
                    decision.model,
                    decision.data_class,
                    decision.decision,
                    decision.reason,
                    decision.consent_id,
                    occurred_at,
                ),
            )
            self._chain.append_in(
                connection,
                event_type="egress_decision",
                actor="model_gateway",
                subject_type="provider",
                subject_id=decision.provider,
                decision=decision.decision,
                occurred_at_ms=occurred_at,
                payload={
                    "model": decision.model,
                    "data_class": decision.data_class,
                    "reason": decision.reason,
                    "consent_id": decision.consent_id,
                },
            )

    def record_model_call(self, record: ModelCallAudit) -> None:
        usage = (
            json.dumps(record.usage.as_dict(), sort_keys=True) if record.usage is not None else None
        )
        occurred_at = _to_ms(record.started_at)
        with self._database.session(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO model_calls
                    (id, task_id, provider, model, purpose, request_hash,
                     response_hash, prompt_tokens, completion_tokens, total_tokens,
                     latency_ms, egress_consent_id, status, created_at,
                     finish_reason, provider_request_id, usage_json)
                VALUES (?, NULL, ?, ?, 'model_completion', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"mdl_{uuid.uuid4().hex}",
                    record.provider,
                    record.model,
                    record.request_hash,
                    record.usage.input_tokens if record.usage is not None else None,
                    record.usage.output_tokens if record.usage is not None else None,
                    record.usage.total_tokens if record.usage is not None else None,
                    record.latency_ms,
                    record.consent_id,
                    record.status,
                    occurred_at,
                    record.finish_reason,
                    record.provider_request_id,
                    usage,
                ),
            )
            self._chain.append_in(
                connection,
                event_type="model_call",
                actor="model_gateway",
                subject_type="provider",
                subject_id=record.provider,
                occurred_at_ms=occurred_at,
                payload={
                    "model": record.model,
                    "request_hash": record.request_hash,
                    "latency_ms": record.latency_ms,
                    "status": record.status,
                    "finish_reason": record.finish_reason,
                    "usage": record.usage.as_dict() if record.usage is not None else None,
                    "provider_request_id": record.provider_request_id,
                    "consent_id": record.consent_id,
                },
            )


def _to_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)
