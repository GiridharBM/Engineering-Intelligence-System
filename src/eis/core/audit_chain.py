# SPDX-License-Identifier: Apache-2.0

"""Canonical, content-free audit events and hash-chain verification.

Algorithm (v1):
- Canonical JSON is UTF-8, keys sorted, separators ``","`` and ``":"`` and
  non-ASCII characters preserved.
- ``hash = SHA-256(prev_hash || canonical_json(event_without_hash))`` where
  ``event_without_hash`` excludes only ``hash`` and ``prev_hash``.
- ``prev_hash`` for sequence 1 is 64 ASCII zeros. Sequence numbers start at 1
  and are contiguous in append order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

GENESIS_PREV_HASH = "0" * 64
HASH_ALGORITHM = "sha256"


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    event_id: str
    occurred_at_ms: int
    actor: str
    event_type: str
    subject_type: str
    subject_id: str
    decision: str | None
    payload: Mapping[str, object]
    prev_hash: str
    event_hash: str

    def canonical_body(self) -> dict[str, object]:
        """Event fields covered by the hash; ``event_hash`` is excluded."""
        return {
            "actor": self.actor,
            "decision": self.decision,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at_ms": self.occurred_at_ms,
            "payload": dict(self.payload),
            "seq": self.seq,
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
        }


@dataclass(frozen=True)
class ChainStatus:
    ok: bool
    event_count: int
    head_hash: str
    failure: str | None = None
    failed_seq: int | None = None
    head_time_ms: int = 0


class AuditIntegrityError(RuntimeError):
    """Raised when audit-sensitive work must stop because the chain is broken."""


class AuditIntegrityGate(Protocol):
    """Status interface that approval-sensitive paths must check first.

    Approval and execution paths are not implemented yet. When they are, they
    must call ``require_intact`` before granting or executing anything.
    """

    def status(self) -> ChainStatus: ...

    def require_intact(self) -> ChainStatus: ...


def canonical_event_json(body: Mapping[str, object]) -> str:
    return json.dumps(
        dict(body),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_event_hash(prev_hash: str, body: Mapping[str, object]) -> str:
    canonical = canonical_event_json(body).encode("utf-8")
    return hashlib.sha256(prev_hash.encode("ascii") + canonical).hexdigest()


def verify_chain(events: Iterable[AuditEvent]) -> ChainStatus:
    """Recompute the chain from sequence 1 and report the first mismatch."""
    ordered = tuple(events)
    previous_hash = GENESIS_PREV_HASH
    expected_seq = 1

    for event in ordered:
        if event.seq != expected_seq:
            return _failure(
                ordered,
                f"sequence gap: expected {expected_seq}, found {event.seq}",
                event.seq,
            )
        if event.prev_hash != previous_hash:
            return _failure(
                ordered,
                f"previous hash mismatch at seq {event.seq}",
                event.seq,
            )
        expected_hash = compute_event_hash(event.prev_hash, event.canonical_body())
        if event.event_hash != expected_hash:
            return _failure(
                ordered,
                f"event hash mismatch at seq {event.seq}",
                event.seq,
            )
        previous_hash = event.event_hash
        expected_seq += 1

    return ChainStatus(
        ok=True,
        event_count=len(ordered),
        head_hash=previous_hash,
        head_time_ms=ordered[-1].occurred_at_ms if ordered else 0,
    )


def _failure(events: tuple[AuditEvent, ...], failure: str, failed_seq: int) -> ChainStatus:
    head = events[-1].event_hash if events else GENESIS_PREV_HASH
    return ChainStatus(
        ok=False,
        event_count=len(events),
        head_hash=head,
        failure=failure,
        failed_seq=failed_seq,
    )
