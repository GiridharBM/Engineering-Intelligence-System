# SPDX-License-Identifier: Apache-2.0

"""External audit-chain checkpoint interface.

An anchor is a content-free checkpoint of a verified local audit-chain prefix.
The authoritative copy must be retained by a separately controlled external
system; a local SQLite row or local file is only a receipt/reference and is
never described as external anchoring. No provider credential, anchor secret,
project content or audit payload text is included in an anchor or receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from eis.core.audit_chain import AuditEvent, AuditIntegrityError, ChainStatus

ALGORITHM: Literal["eis-audit-anchor-v1"] = "eis-audit-anchor-v1"
ANCHOR_ID_RE = re.compile(r"^anc_[0-9a-f]{32}$")
EXTERNAL_REF_RE = re.compile(r"^ext_[0-9a-f]{32}$")
EXTERNAL_VERSION_RE = re.compile(r"^[\x21-\x7e]{1,1024}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AnchorUnavailable(RuntimeError):
    """Raised when no enabled external anchor backend can accept a checkpoint."""


class AnchorVerificationError(RuntimeError):
    """Raised when an external receipt or its local reference does not verify."""


@dataclass(frozen=True)
class AuditAnchor:
    anchor_id: str
    up_to_seq: int
    head_hash: str
    created_at_ms: int
    anchor_hash: str
    algorithm: Literal["eis-audit-anchor-v1"] = ALGORITHM

    def __post_init__(self) -> None:
        if not ANCHOR_ID_RE.fullmatch(self.anchor_id):
            raise ValueError("invalid anchor id")
        if self.up_to_seq < 1:
            raise ValueError("anchor sequence must be positive")
        if not HASH_RE.fullmatch(self.head_hash) or not HASH_RE.fullmatch(self.anchor_hash):
            raise ValueError("anchor hashes must be 64 lowercase hex characters")
        if self.created_at_ms < 1 or self.algorithm != ALGORITHM:
            raise ValueError("invalid anchor metadata")

    def body(self) -> dict[str, object]:
        # ``anchor_id`` is derived from this body, so it is intentionally excluded.
        return {
            "algorithm": self.algorithm,
            "created_at_ms": self.created_at_ms,
            "head_hash": self.head_hash,
            "up_to_seq": self.up_to_seq,
        }


@dataclass(frozen=True)
class AnchorReceipt:
    """Opaque, content-free acknowledgement from the external backend.

    ``external_version`` pins the exact external object version that retained
    the checkpoint (for example, an S3 version ID). It is opaque and reveals no
    content.
    """

    external_ref: str
    received_at_ms: int
    receipt_hash: str
    external_version: str

    def __post_init__(self) -> None:
        if not EXTERNAL_REF_RE.fullmatch(self.external_ref):
            raise ValueError("invalid external reference")
        if self.received_at_ms < 1 or not HASH_RE.fullmatch(self.receipt_hash):
            raise ValueError("invalid anchor receipt")
        if not EXTERNAL_VERSION_RE.fullmatch(self.external_version):
            raise ValueError("invalid external version")


@dataclass(frozen=True)
class AnchorSubmission:
    anchor: AuditAnchor
    receipt: AnchorReceipt


class AuditAnchorSink(Protocol):
    """Separately controlled retention and verification of audit checkpoints."""

    def submit(self, anchor: AuditAnchor) -> AnchorReceipt: ...

    def verify(self, anchor: AuditAnchor, receipt: AnchorReceipt) -> bool: ...


class AnchorEventStore(Protocol):
    def status(self) -> ChainStatus: ...

    def status_for(self, events: tuple[AuditEvent, ...]) -> ChainStatus: ...

    def events_up_to(self, seq: int) -> tuple[AuditEvent, ...]: ...

    def event_by_id(self, event_id: str) -> AuditEvent | None: ...

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
    ) -> AuditEvent: ...


def compute_anchor_hash(body: dict[str, object]) -> str:
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def compute_receipt_hash(
    *,
    anchor_hash: str,
    external_ref: str,
    received_at_ms: int,
    external_version: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "anchor_hash": anchor_hash,
                "external_ref": external_ref,
                "external_version": external_version,
                "received_at_ms": received_at_ms,
            }
        ).encode("utf-8")
    ).hexdigest()


def new_anchor(*, up_to_seq: int, head_hash: str, created_at_ms: int) -> AuditAnchor:
    body = {
        "algorithm": ALGORITHM,
        "created_at_ms": created_at_ms,
        "head_hash": head_hash,
        "up_to_seq": up_to_seq,
    }
    digest = compute_anchor_hash(body)
    return AuditAnchor(
        anchor_id=f"anc_{digest[:32]}",
        up_to_seq=up_to_seq,
        head_hash=head_hash,
        created_at_ms=created_at_ms,
        anchor_hash=digest,
    )


class AuditAnchorService:
    """Create, store references for, and verify external audit checkpoints."""

    def __init__(self, *, events: AnchorEventStore, sink: AuditAnchorSink | None) -> None:
        self._events = events
        self._sink = sink

    def create(self, *, actor: str, created_at_ms: int | None = None) -> AnchorSubmission:
        sink = self._sink
        if sink is None:
            raise AnchorUnavailable("anchor_backend_unavailable")
        status = self._events.status()
        if not status.ok:
            raise AuditIntegrityError(status.failure or "audit chain integrity failure")
        if status.event_count < 1:
            raise AnchorUnavailable("anchor_chain_empty")
        when = status.head_time_ms if created_at_ms is None else created_at_ms
        anchor = new_anchor(
            up_to_seq=status.event_count,
            head_hash=status.head_hash,
            created_at_ms=when,
        )
        existing = self._events.event_by_id(f"anchor_{anchor.anchor_id}")
        if existing is not None:
            return _submission_from_event(existing, anchor)
        events = self._events.events_up_to(status.event_count)
        if events and events[-1].event_type == "audit_anchor_submitted":
            # Retry immediately after a committed checkpoint: do not re-anchor
            # the event that only records the checkpoint itself.
            recorded = events[-1]
            return _submission_from_event(recorded, _anchor_from_event(recorded))
        receipt = sink.submit(anchor)
        expected = compute_receipt_hash(
            anchor_hash=anchor.anchor_hash,
            external_ref=receipt.external_ref,
            received_at_ms=receipt.received_at_ms,
            external_version=receipt.external_version,
        )
        if receipt.receipt_hash != expected:
            raise AnchorVerificationError("invalid external receipt hash")
        self._events.append(
            event_type="audit_anchor_submitted",
            actor=actor,
            subject_type="audit_anchor",
            subject_id=anchor.anchor_id,
            decision="created",
            event_id=f"anchor_{anchor.anchor_id}",
            occurred_at_ms=receipt.received_at_ms,
            payload={
                "algorithm": anchor.algorithm,
                "anchor_hash": anchor.anchor_hash,
                "created_at_ms": anchor.created_at_ms,
                "external_ref": receipt.external_ref,
                "external_version": receipt.external_version,
                "head_hash": anchor.head_hash,
                "receipt_hash": receipt.receipt_hash,
                "received_at_ms": receipt.received_at_ms,
                "up_to_seq": anchor.up_to_seq,
            },
        )
        return AnchorSubmission(anchor, receipt)

    def verify(self, submission: AnchorSubmission) -> bool:
        sink = self._sink
        if sink is None:
            raise AnchorUnavailable("anchor_backend_unavailable")
        anchor, receipt = submission.anchor, submission.receipt
        status = self._events.status()
        if not status.ok:
            raise AuditIntegrityError(status.failure or "audit chain integrity failure")
        events = self._events.events_up_to(anchor.up_to_seq)
        if len(events) != anchor.up_to_seq:
            raise AuditIntegrityError("anchored sequence is unavailable")
        prefix = self._events.status_for(events)
        if not prefix.ok or prefix.head_hash != anchor.head_hash:
            raise AuditIntegrityError("anchored chain prefix does not match")
        if compute_anchor_hash(anchor.body()) != anchor.anchor_hash:
            raise AuditIntegrityError("anchor hash mismatch")
        if (
            compute_receipt_hash(
                anchor_hash=anchor.anchor_hash,
                external_ref=receipt.external_ref,
                received_at_ms=receipt.received_at_ms,
                external_version=receipt.external_version,
            )
            != receipt.receipt_hash
        ):
            raise AnchorVerificationError("receipt hash mismatch")
        event = self._events.event_by_id(f"anchor_{anchor.anchor_id}")
        if (
            event is None
            or event.payload.get("external_ref") != receipt.external_ref
            or event.payload.get("external_version") != receipt.external_version
            or event.payload.get("receipt_hash") != receipt.receipt_hash
        ):
            raise AnchorVerificationError("anchor reference is not recorded")
        if not sink.verify(anchor, receipt):
            raise AnchorVerificationError("external anchor verification failed")
        return True

    def verify_recorded(self, anchor_id: str) -> AnchorSubmission:
        """Reconstruct a recorded checkpoint from its audit event and verify it."""
        if not ANCHOR_ID_RE.fullmatch(anchor_id):
            raise AnchorVerificationError("anchor_id_invalid")
        event = self._events.event_by_id(f"anchor_{anchor_id}")
        if (
            event is None
            or event.event_type != "audit_anchor_submitted"
            or event.subject_id != anchor_id
        ):
            raise AnchorVerificationError("anchor_not_found")
        try:
            submission = _submission_from_event(event, _anchor_from_event(event))
        except (KeyError, TypeError, ValueError):
            raise AnchorVerificationError("anchor_event_invalid") from None
        self.verify(submission)
        return submission


def _submission_from_event(event: AuditEvent, anchor: AuditAnchor) -> AnchorSubmission:
    payload = event.payload
    if payload.get("anchor_hash") != anchor.anchor_hash:
        raise AuditIntegrityError("anchor event hash mismatch")
    external_version = payload.get("external_version")
    if not isinstance(external_version, str):
        raise AuditIntegrityError("anchor event version missing")
    receipt = AnchorReceipt(
        external_ref=str(payload["external_ref"]),
        received_at_ms=int(str(payload["received_at_ms"])),
        receipt_hash=str(payload["receipt_hash"]),
        external_version=external_version,
    )
    return AnchorSubmission(anchor, receipt)


def _anchor_from_event(event: AuditEvent) -> AuditAnchor:
    payload = event.payload
    algorithm = payload.get("algorithm")
    return AuditAnchor(
        anchor_id=event.subject_id.removeprefix("anchor_"),
        up_to_seq=int(str(payload["up_to_seq"])),
        head_hash=str(payload["head_hash"]),
        created_at_ms=int(str(payload["created_at_ms"])),
        anchor_hash=str(payload["anchor_hash"]),
        algorithm=algorithm if algorithm == ALGORITHM else ALGORITHM,
    )


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
