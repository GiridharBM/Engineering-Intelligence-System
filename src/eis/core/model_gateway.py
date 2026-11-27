# SPDX-License-Identifier: Apache-2.0

"""Provider-neutral model gateway with consent and audit enforcement.

This is not the agent loop and does not execute tools. It only normalizes one
external text completion call and enforces consent before any network access.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import cast

from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ModelProvider,
    ProviderError,
)
from eis.config import DataClass, ModelProviderName
from eis.core.audit import AuditSink, CallStatus, EgressDecision, ModelCallAudit
from eis.core.consent import ConsentGate
from eis.core.redaction import redact_text

_SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SAFE_FINISH_REASON = re.compile(r"^[a-z0-9_.:-]{1,64}$")


class ModelGateway:
    """The only path for a text completion; no consent means no network call."""

    def __init__(
        self,
        provider: ModelProvider,
        consent_gate: ConsentGate,
        audit_sink: AuditSink,
    ) -> None:
        self._provider = provider
        self._consent_gate = consent_gate
        self._audit_sink = audit_sink

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    async def complete(
        self,
        request: CompletionRequest,
        data_class: DataClass = "C1",
    ) -> CompletionResponse:
        provider_name = cast(ModelProviderName, self._provider.name)
        model = self._provider.model

        try:
            consent = self._consent_gate.authorize(provider_name, data_class)
        except PermissionError:
            self._audit_sink.record_egress(
                EgressDecision(
                    provider=provider_name,
                    model=model,
                    data_class=data_class,
                    decision="deny",
                    reason="consent_required",
                    consent_id=None,
                    occurred_at=_utc_now(),
                )
            )
            self._audit_sink.record_model_call(
                _audit_record(
                    provider_name,
                    model,
                    request,
                    _utc_now(),
                    0.0,
                    "consent_denied",
                    None,
                    None,
                )
            )
            raise

        self._audit_sink.record_egress(
            EgressDecision(
                provider=provider_name,
                model=model,
                data_class=data_class,
                decision="allow",
                reason="consent_granted",
                consent_id=consent.consent_id,
                occurred_at=_utc_now(),
            )
        )

        started_at = _utc_now()
        started = time.perf_counter()
        try:
            # The provider receives a redacted copy; the audit keeps the original
            # request hash for correlation and never stores message content.
            response = await self._provider.complete(redact_completion_request(request))
        except ProviderError:
            self._audit_sink.record_model_call(
                _audit_record(
                    provider_name,
                    model,
                    request,
                    started_at,
                    time.perf_counter() - started,
                    "provider_error",
                    None,
                    consent.consent_id,
                )
            )
            raise
        except Exception:  # noqa: BLE001 - sanitize unexpected adapter failures
            self._audit_sink.record_model_call(
                _audit_record(
                    provider_name,
                    model,
                    request,
                    started_at,
                    time.perf_counter() - started,
                    "provider_error",
                    None,
                    consent.consent_id,
                )
            )
            raise ProviderError("provider call failed") from None

        self._audit_sink.record_model_call(
            _audit_record(
                provider_name,
                model,
                request,
                started_at,
                time.perf_counter() - started,
                "success",
                response,
                consent.consent_id,
            )
        )
        return response


def _audit_record(
    provider: ModelProviderName,
    model: str,
    request: CompletionRequest,
    started_at: str,
    elapsed_seconds: float,
    status: CallStatus,
    response: CompletionResponse | None,
    consent_id: str | None,
) -> ModelCallAudit:
    return ModelCallAudit(
        provider=provider,
        model=model,
        request_hash=request.request_hash,
        started_at=started_at,
        latency_ms=round(elapsed_seconds * 1000, 3),
        status=status,
        finish_reason=_safe_finish_reason(response.finish_reason) if response else None,
        usage=response.usage if response else None,
        provider_request_id=(_safe_provider_id(response.provider_request_id) if response else None),
        consent_id=consent_id,
    )


def redact_completion_request(request: CompletionRequest) -> CompletionRequest:
    """Return a redacted provider-facing copy without mutating the caller's request."""
    return CompletionRequest(
        messages=tuple(
            ChatMessage(role=message.role, content=redact_text(message.content))
            for message in request.messages
        ),
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )


def _safe_provider_id(value: str | None) -> str | None:
    """Keep only a bounded, credential-free provider request identifier."""
    if value is None or redact_text(value) != value:
        return None
    return value if _SAFE_PROVIDER_ID.fullmatch(value) else None


def _safe_finish_reason(value: str | None) -> str | None:
    """Keep only a bounded, credential-free finish-reason code."""
    if value is None or redact_text(value) != value:
        return None
    return value if _SAFE_FINISH_REASON.fullmatch(value) else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
