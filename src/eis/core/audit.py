# SPDX-License-Identifier: Apache-2.0

"""Typed, content-free egress and model-call audit records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from eis.adapters.models.base import TokenUsage
from eis.config import DataClass, ModelProviderName

EgressEffect = Literal["allow", "deny"]
CallStatus = Literal["success", "provider_error", "consent_denied"]


@dataclass(frozen=True)
class EgressDecision:
    provider: ModelProviderName
    model: str
    data_class: DataClass
    decision: EgressEffect
    reason: str
    consent_id: str | None
    occurred_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "egress_decision",
            "provider": self.provider,
            "model": self.model,
            "data_class": self.data_class,
            "decision": self.decision,
            "reason": self.reason,
            "consent_id": self.consent_id,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True)
class ModelCallAudit:
    provider: ModelProviderName
    model: str
    request_hash: str
    started_at: str
    latency_ms: float
    status: CallStatus
    finish_reason: str | None
    usage: TokenUsage | None
    provider_request_id: str | None
    consent_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "model_call",
            "provider": self.provider,
            "model": self.model,
            "request_hash": self.request_hash,
            "started_at": self.started_at,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "finish_reason": self.finish_reason,
            "usage": self.usage.as_dict() if self.usage is not None else None,
            "provider_request_id": self.provider_request_id,
            "consent_id": self.consent_id,
        }


class AuditSink(Protocol):
    def record_egress(self, decision: EgressDecision) -> None: ...

    def record_model_call(self, record: ModelCallAudit) -> None: ...


class InMemoryAuditSink:
    """Deterministic sink for tests and local diagnostics."""

    def __init__(self) -> None:
        self.egress: list[EgressDecision] = []
        self.model_calls: list[ModelCallAudit] = []

    def record_egress(self, decision: EgressDecision) -> None:
        self.egress.append(decision)

    def record_model_call(self, record: ModelCallAudit) -> None:
        self.model_calls.append(record)


class JsonlAuditSink:
    """Append-only JSONL sink for runtime data; never stores request content."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def record_egress(self, decision: EgressDecision) -> None:
        self._append(decision.as_dict())

    def record_model_call(self, record: ModelCallAudit) -> None:
        self._append(record.as_dict())

    def _append(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
