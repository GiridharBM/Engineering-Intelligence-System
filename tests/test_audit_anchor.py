# SPDX-License-Identifier: Apache-2.0

"""External audit-anchor interface tests with a fake independently controlled sink."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from eis.core.audit_anchor import (
    AnchorReceipt,
    AnchorSubmission,
    AnchorUnavailable,
    AnchorVerificationError,
    AuditAnchorService,
    compute_receipt_hash,
)
from eis.core.audit_chain import AuditEvent, AuditIntegrityError, verify_chain
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase

PRIVATE_MARKER = "private-project-content-marker"


class MutableEventStore:
    """Fake in-memory chain already containing an independently anchored checkpoint."""

    def __init__(self, events: list[AuditEvent]) -> None:
        self.events = events

    def status(self) -> object:
        return verify_chain(tuple(self.events))

    @staticmethod
    def status_for(events: tuple[AuditEvent, ...]) -> object:
        return verify_chain(events)

    def events_up_to(self, seq: int) -> tuple[AuditEvent, ...]:
        return tuple(self.events[:seq])

    def event_by_id(self, event_id: str) -> AuditEvent | None:
        return next((item for item in self.events if item.event_id == event_id), None)

    def append(self, **kwargs: object) -> AuditEvent:
        raise AssertionError("tampered-chain fixtures must not append")


@dataclass
class FakeExternalAnchorSink:
    """In-memory stand-in for a separately controlled external retention service."""

    receipts: dict[str, AnchorReceipt]
    submissions: int = 0
    verifications: int = 0
    fail_submit: bool = False
    fail_verify: bool = False

    @classmethod
    def create(cls) -> FakeExternalAnchorSink:
        return cls(receipts={})

    def submit(self, anchor: object) -> AnchorReceipt:
        if self.fail_submit:
            raise AnchorUnavailable("external_backend_unavailable")
        self.submissions += 1
        existing = self.receipts.get(anchor.anchor_id)  # type: ignore[attr-defined]
        if existing is not None:
            return existing
        external_ref = (
            "ext_"
            + hashlib.sha256(
                anchor.anchor_hash.encode("ascii")  # type: ignore[attr-defined]
            ).hexdigest()[:32]
        )
        received_at_ms = anchor.created_at_ms + 1  # type: ignore[attr-defined]
        external_version = (
            "ver_" + hashlib.sha256(anchor.anchor_hash.encode("ascii")).hexdigest()  # type: ignore[attr-defined]
        )
        receipt = AnchorReceipt(
            external_ref=external_ref,
            received_at_ms=received_at_ms,
            receipt_hash=compute_receipt_hash(
                anchor_hash=anchor.anchor_hash,  # type: ignore[attr-defined]
                external_ref=external_ref,
                received_at_ms=received_at_ms,
                external_version=external_version,
            ),
            external_version=external_version,
        )
        self.receipts[anchor.anchor_id] = receipt  # type: ignore[attr-defined]
        return receipt

    def verify(self, anchor: object, receipt: AnchorReceipt) -> bool:
        self.verifications += 1
        if self.fail_verify:
            return False
        return self.receipts.get(anchor.anchor_id) == receipt  # type: ignore[attr-defined]


def _chain(tmp_path: Path, count: int = 2) -> tuple[SqliteDatabase, AuditChainStore]:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    store = AuditChainStore(database)
    for index in range(count):
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id=f"t{index}",
            payload={"hash": hashlib.sha256(str(index).encode()).hexdigest()},
            occurred_at_ms=100 + index,
        )
    return database, store


def test_checkpoint_creation_verification_and_content_free_event(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    service = AuditAnchorService(events=store, sink=sink)

    submission = service.create(actor="test")

    assert submission.anchor.up_to_seq == 2
    assert sink.submissions == 1
    assert service.verify(submission) is True
    event = store.event_by_id(f"anchor_{submission.anchor.anchor_id}")
    assert event is not None
    assert event.event_type == "audit_anchor_submitted"
    payload = dict(event.payload)
    assert payload["up_to_seq"] == 2
    assert payload["head_hash"] == submission.anchor.head_hash
    assert payload["external_ref"] == submission.receipt.external_ref
    assert payload["external_version"] == submission.receipt.external_version
    assert PRIVATE_MARKER not in str(payload)
    assert "secret" not in str(payload).lower()
    assert store.status().ok


def test_idempotent_replay_and_mismatched_receipt_fail_closed(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    service = AuditAnchorService(events=store, sink=sink)

    first = service.create(actor="test")
    replay = service.create(actor="test")
    assert replay == first
    assert sink.submissions == 1
    assert (
        sum(
            1
            for item in store.events_up_to(store.status().event_count)
            if item.event_type == "audit_anchor_submitted"
        )
        == 1
    )

    forged = AnchorReceipt(
        external_ref="ext_" + "c" * 32,
        received_at_ms=first.receipt.received_at_ms,
        receipt_hash=first.receipt.receipt_hash,
        external_version=first.receipt.external_version,
    )
    with pytest.raises(AnchorVerificationError):
        service.verify(AnchorSubmission(first.anchor, forged))

    tampered_anchor = replace(first.anchor, head_hash="b" * 64)
    with pytest.raises(AuditIntegrityError):
        service.verify(AnchorSubmission(tampered_anchor, first.receipt))


def test_stale_anchor_still_verifies_historical_prefix_but_new_anchor_advances(
    tmp_path: Path,
) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    service = AuditAnchorService(events=store, sink=sink)
    old = service.create(actor="test")
    store.append(
        event_type="test_event",
        actor="test",
        subject_type="test",
        subject_id="later",
        payload={"hash": "a" * 64},
        occurred_at_ms=200,
    )
    fresh = service.create(actor="test")
    assert old.anchor.up_to_seq == 2
    assert fresh.anchor.up_to_seq == 4
    assert sink.submissions == 2
    assert service.verify(old) is True
    assert service.verify(fresh) is True


def test_tampered_chain_fails_closed(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    service = AuditAnchorService(events=store, sink=FakeExternalAnchorSink.create())
    submission = service.create(actor="test")
    events = list(store.events_up_to(store.status().event_count))
    events[0] = replace(events[0], event_hash="b" * 64)
    tampered = AuditAnchorService(
        events=MutableEventStore(events), sink=FakeExternalAnchorSink.create()
    )
    with pytest.raises(AuditIntegrityError):
        tampered.verify(submission)


def test_truncated_chain_fails_closed(tmp_path: Path) -> None:
    _, store = _chain(tmp_path, count=3)
    service = AuditAnchorService(events=store, sink=FakeExternalAnchorSink.create())
    submission = service.create(actor="test")
    events = [item for item in store.events_up_to(store.status().event_count) if item.seq != 3]
    truncated = AuditAnchorService(
        events=MutableEventStore(events), sink=FakeExternalAnchorSink.create()
    )
    with pytest.raises(AuditIntegrityError):
        truncated.verify(submission)


def test_external_failure_leaves_no_local_event(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    sink.fail_submit = True
    service = AuditAnchorService(events=store, sink=sink)
    with pytest.raises(AnchorUnavailable):
        service.create(actor="test")
    assert sink.submissions == 0
    assert store.event_by_id("anchor_" + "a" * 32) is None
    assert not any(
        event.event_type == "audit_anchor_submitted"
        for event in store.events_up_to(store.status().event_count)
    )


def test_external_verification_failure_is_fail_closed(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    service = AuditAnchorService(events=store, sink=sink)
    submission = service.create(actor="test")
    sink.fail_verify = True
    with pytest.raises(AnchorVerificationError):
        service.verify(submission)


def test_audit_failure_retry_after_restart_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    service = AuditAnchorService(events=store, sink=sink)

    def failing_append(*args: object, **kwargs: object) -> object:
        raise AuditIntegrityError("audit unavailable")

    monkeypatch.setattr(store, "append", failing_append)
    with pytest.raises(AuditIntegrityError):
        service.create(actor="test")
    assert sink.submissions == 1
    assert not any(
        event.event_type == "audit_anchor_submitted"
        for event in store.events_up_to(store.status().event_count)
    )

    monkeypatch.undo()
    restarted = AuditAnchorService(events=store, sink=sink)
    retried = restarted.create(actor="test")
    assert retried.anchor.up_to_seq == 2
    assert restarted.verify(retried) is True
    assert sink.submissions == 2  # replay returns the same external receipt
    assert (
        sum(
            1
            for event in store.events_up_to(store.status().event_count)
            if event.event_type == "audit_anchor_submitted"
        )
        == 1
    )


def test_no_external_backend_fails_closed(tmp_path: Path) -> None:
    _, store = _chain(tmp_path)
    sink = FakeExternalAnchorSink.create()
    submission = AuditAnchorService(events=store, sink=sink).create(actor="test")
    no_backend = AuditAnchorService(events=store, sink=None)
    with pytest.raises(AnchorUnavailable):
        no_backend.create(actor="test")
    with pytest.raises(AnchorUnavailable):
        no_backend.verify(submission)
