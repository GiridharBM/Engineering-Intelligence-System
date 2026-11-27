# SPDX-License-Identifier: Apache-2.0

"""Provider-scoped, classification-based consent gate."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Protocol

from eis.config import ConsentRecord, DataClass, ModelProviderName


class ConsentRequired(PermissionError):
    """Raised before any network call when consent is absent or insufficient."""


class ConsentLookup(Protocol):
    """Storage-facing lookup; implemented by the SQLite consent store."""

    def find_active(
        self,
        provider: ModelProviderName,
        data_class: DataClass,
        now_ms: int,
    ) -> ConsentRecord | None: ...


class ConsentGate:
    """Fail-closed gate for external model requests.

    Consent is explicit per provider and data class. C3 is never permitted.
    An optional durable lookup takes precedence over in-memory records.
    """

    def __init__(
        self,
        records: Iterable[ConsentRecord] = (),
        *,
        lookup: ConsentLookup | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._records = tuple(records)
        self._lookup = lookup
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def authorize(
        self,
        provider: ModelProviderName,
        data_class: DataClass,
        now_ms: int | None = None,
    ) -> ConsentRecord:
        if data_class not in {"C0", "C1", "C2", "C3"}:
            raise ConsentRequired(f"unknown data class: {data_class}")
        if data_class == "C3":
            raise ConsentRequired("C3 data can never be sent to an external provider")

        now = self._clock_ms() if now_ms is None else now_ms
        if self._lookup is not None:
            record = self._lookup.find_active(provider, data_class, now)
            if record is not None:
                return record
            raise ConsentRequired(
                f"no active consent for provider={provider} data_class={data_class}"
            )

        for record in self._records:
            if record.provider != provider or data_class not in record.data_classes:
                continue
            if record.revoked_at_ms is not None:
                continue
            if record.expires_at_ms is not None and record.expires_at_ms <= now:
                continue
            return record
        raise ConsentRequired(f"no active consent for provider={provider} data_class={data_class}")
