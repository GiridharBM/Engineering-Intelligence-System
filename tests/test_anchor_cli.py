# SPDX-License-Identifier: Apache-2.0

"""Operator CLI tests for `eis anchor create` and `eis anchor verify`.

Sinks are injected fakes; no AWS SDK client and no network call is used.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.config import load_settings
from eis.core.audit_anchor import AnchorReceipt, AnchorUnavailable, compute_receipt_hash
from eis.core.audit_chain import canonical_event_json, compute_event_hash
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import default_database

runner = CliRunner()
PRIVATE_MARKER = "private-project-content-marker"
ANCHOR_OUTPUT_RE = re.compile(r"anchor_id=(anc_[0-9a-f]{32})")


@dataclass
class FakeAnchorSink:
    receipts: dict[str, AnchorReceipt] = field(default_factory=dict)
    submit_calls: int = 0
    verify_calls: int = 0
    fail_submit: bool = False
    fail_verify: bool = False
    reject_verify: bool = False

    def submit(self, anchor: object) -> AnchorReceipt:
        self.submit_calls += 1
        if self.fail_submit:
            raise AnchorUnavailable("s3_anchor_put_failed:service_error")
        anchor_id = anchor.anchor_id  # type: ignore[attr-defined]
        existing = self.receipts.get(anchor_id)
        if existing is not None:
            return existing
        anchor_hash = anchor.anchor_hash  # type: ignore[attr-defined]
        version = "ver_" + anchor_hash[:32]
        external_ref = "ext_" + hashlib.sha256(f"{anchor_hash}:{version}".encode()).hexdigest()[:32]
        received_at_ms = anchor.created_at_ms + 1  # type: ignore[attr-defined]
        receipt = AnchorReceipt(
            external_ref=external_ref,
            received_at_ms=received_at_ms,
            receipt_hash=compute_receipt_hash(
                anchor_hash=anchor_hash,
                external_ref=external_ref,
                received_at_ms=received_at_ms,
                external_version=version,
            ),
            external_version=version,
        )
        self.receipts[anchor_id] = receipt
        return receipt

    def verify(self, anchor: object, receipt: AnchorReceipt) -> bool:
        self.verify_calls += 1
        if self.fail_verify:
            raise AnchorUnavailable("s3_anchor_retention_failed:service_error")
        if self.reject_verify:
            return False
        return self.receipts.get(anchor.anchor_id) == receipt  # type: ignore[attr-defined]


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sink: FakeAnchorSink) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("EIS_ANCHOR_BACKEND", "s3")
    monkeypatch.setenv("EIS_ANCHOR_S3_BUCKET", "eis-anchor-test")
    monkeypatch.setenv("EIS_ANCHOR_S3_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("EIS_ANCHOR_S3_REGION", "us-east-1")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_MODE", "COMPLIANCE")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_DAYS", "30")
    monkeypatch.setattr("eis.cli.build_s3_object_lock_sink", lambda config: sink)


def _seed_chain(count: int = 2) -> AuditChainStore:
    store = AuditChainStore(default_database(load_settings(require_provider=False)))
    for index in range(count):
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id=f"t{index}",
            payload={
                "hash": hashlib.sha256(str(index).encode()).hexdigest(),
                "note": PRIVATE_MARKER,
            },
            occurred_at_ms=100 + index,
        )
    return store


def _anchor_id(output: str) -> str:
    match = ANCHOR_OUTPUT_RE.search(output)
    assert match is not None, output
    return match.group(1)


def test_create_and_verify_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sentinel-credential-value")

    created = runner.invoke(app, ["anchor", "create"])

    assert created.exit_code == 0, created.output
    assert "anchor_create=ok" in created.output
    anchor_id = _anchor_id(created.output)
    assert "up_to_seq=2" in created.output
    assert "external_ref=ext_" in created.output
    assert "external_version=ver_" in created.output
    assert "receipt_hash=" in created.output
    assert "head_hash" not in created.output
    assert PRIVATE_MARKER not in created.output
    assert "sentinel-credential-value" not in created.output
    assert sink.submit_calls == 1
    event = store.event_by_id(f"anchor_{anchor_id}")
    assert event is not None
    assert event.event_type == "audit_anchor_submitted"
    assert event.payload["external_version"].startswith("ver_")
    assert PRIVATE_MARKER not in str(event.payload)
    assert store.status().ok

    verified = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert verified.exit_code == 0, verified.output
    assert "anchor_verify=ok" in verified.output
    assert f"anchor_id={anchor_id}" in verified.output
    assert "external_version=ver_" in verified.output
    assert PRIVATE_MARKER not in verified.output
    assert "sentinel-credential-value" not in verified.output
    assert sink.verify_calls == 1


def test_create_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()

    first = runner.invoke(app, ["anchor", "create"])
    second = runner.invoke(app, ["anchor", "create"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert _anchor_id(first.output) == _anchor_id(second.output)
    assert sink.submit_calls == 1
    events = store.events_up_to(store.status().event_count)
    assert sum(1 for event in events if event.event_type == "audit_anchor_submitted") == 1


@pytest.mark.parametrize(
    ("anchor_id", "reason"),
    [("anc_" + "a" * 32, "anchor_not_found"), ("not-an-anchor", "anchor_id_invalid")],
)
def test_verify_unknown_or_invalid_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    anchor_id: str,
    reason: str,
) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    _seed_chain()

    result = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert result.exit_code == 1
    assert f"reason={reason}" in result.output
    assert PRIVATE_MARKER not in result.output
    assert sink.verify_calls == 0


def test_create_fails_closed_on_corrupted_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()
    with store.database.connect() as connection:
        connection.execute("DROP TRIGGER IF EXISTS audit_no_update")
        connection.execute("UPDATE audit_events SET hash = ? WHERE seq = 1", ("b" * 64,))

    result = runner.invoke(app, ["anchor", "create"])

    assert result.exit_code == 1
    assert "reason=audit_integrity" in result.output
    assert "anchor_create=ok" not in result.output
    assert sink.submit_calls == 0


def test_verify_fails_closed_on_corrupted_chain_after_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()
    created = runner.invoke(app, ["anchor", "create"])
    anchor_id = _anchor_id(created.output)
    with store.database.connect() as connection:
        connection.execute("DROP TRIGGER IF EXISTS audit_no_update")
        connection.execute("UPDATE audit_events SET hash = ? WHERE seq = 1", ("b" * 64,))

    result = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert result.exit_code == 1
    assert "reason=audit_integrity" in result.output
    assert sink.verify_calls == 0


def test_verify_rejects_tampered_local_receipt_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()
    created = runner.invoke(app, ["anchor", "create"])
    anchor_id = _anchor_id(created.output)
    events = list(store.events_up_to(store.status().event_count))
    recorded = next(event for event in events if event.event_type == "audit_anchor_submitted")
    payload = dict(recorded.payload)
    payload["external_version"] = "ver_tampered"
    tampered = replace(recorded, payload=payload, event_hash="")
    tampered = replace(
        tampered,
        event_hash=compute_event_hash(tampered.prev_hash, tampered.canonical_body()),
    )
    with store.database.connect() as connection:
        connection.execute("DROP TRIGGER IF EXISTS audit_no_update")
        connection.execute(
            "UPDATE audit_events SET payload_json = ?, hash = ? WHERE seq = ?",
            (canonical_event_json(payload), tampered.event_hash, recorded.seq),
        )
    assert store.status().ok

    result = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert result.exit_code == 1
    assert "reason=receipt hash mismatch" in result.output
    assert sink.verify_calls == 0


def test_verify_rejects_sink_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sink = FakeAnchorSink()
    _configure(monkeypatch, tmp_path, sink)
    _seed_chain()
    created = runner.invoke(app, ["anchor", "create"])
    anchor_id = _anchor_id(created.output)
    sink.receipts[anchor_id] = replace(sink.receipts[anchor_id], external_version="ver_other")

    result = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert result.exit_code == 1
    assert "reason=external anchor verification failed" in result.output


def test_create_and_verify_fail_closed_on_service_outage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sink = FakeAnchorSink(fail_submit=True)
    _configure(monkeypatch, tmp_path, sink)
    store = _seed_chain()

    created = runner.invoke(app, ["anchor", "create"])

    assert created.exit_code == 1
    assert "reason=s3_anchor_put_failed:service_error" in created.output
    assert "anchor_create=ok" not in created.output
    assert not any(
        event.event_type == "audit_anchor_submitted"
        for event in store.events_up_to(store.status().event_count)
    )

    sink.fail_submit = False
    created = runner.invoke(app, ["anchor", "create"])
    anchor_id = _anchor_id(created.output)
    sink.fail_verify = True

    verified = runner.invoke(app, ["anchor", "verify", anchor_id])

    assert verified.exit_code == 1
    assert "reason=s3_anchor_retention_failed:service_error" in verified.output


def test_anchor_commands_fail_closed_without_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("EIS_ANCHOR_BACKEND", raising=False)
    _seed_chain()

    created = runner.invoke(app, ["anchor", "create"])
    verified = runner.invoke(app, ["anchor", "verify", "anc_" + "a" * 32])

    assert created.exit_code == 1
    assert "reason=anchor_backend_unavailable" in created.output
    assert verified.exit_code == 1
    assert "reason=anchor_backend_unavailable" in verified.output


def test_anchor_create_fails_closed_on_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("EIS_ANCHOR_BACKEND", "s3")
    monkeypatch.setenv("EIS_ANCHOR_S3_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("EIS_ANCHOR_S3_REGION", "us-east-1")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_MODE", "COMPLIANCE")
    monkeypatch.setenv("EIS_ANCHOR_S3_RETENTION_DAYS", "30")
    monkeypatch.delenv("EIS_ANCHOR_S3_BUCKET", raising=False)

    result = runner.invoke(app, ["anchor", "create"])

    assert result.exit_code == 1
    assert "reason=configuration_error" in result.output
    assert "EIS_ANCHOR_S3_BUCKET" in result.output
