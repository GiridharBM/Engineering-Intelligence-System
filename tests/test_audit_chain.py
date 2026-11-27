# SPDX-License-Identifier: Apache-2.0

"""Hash-chain, append-only and integrity-gate tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)
from eis.cli import app
from eis.config import ConsentRecord
from eis.core.audit import EgressDecision
from eis.core.audit_chain import (
    GENESIS_PREV_HASH,
    AuditEvent,
    AuditIntegrityError,
    AuditIntegrityGate,
    ChainStatus,
    compute_event_hash,
    verify_chain,
)
from eis.core.consent import ConsentGate, ConsentRequired
from eis.core.model_gateway import ModelGateway
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase

FAKE_KEY = "test-only-secret-value"
PROVIDER = "openai"
MODEL = "test-model"
ALTERED_HASH = "b" * 64
runner = CliRunner()


def _db(tmp_path: Path) -> SqliteDatabase:
    return SqliteDatabase(tmp_path / "eis.sqlite3")


def _consent() -> ConsentRecord:
    return ConsentRecord(
        consent_id="cns_test",
        provider="openai",
        data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
        purpose="tests",
    )


def _request() -> CompletionRequest:
    return CompletionRequest(
        messages=(ChatMessage(role="user", content="prompt-marker"),),
        max_tokens=16,
    )


class FakeProvider:
    name = "openai"

    def __init__(self) -> None:
        self.model = MODEL
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            text="response-text-marker",
            model=MODEL,
            provider=self.name,
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
            provider_request_id="req_test",
        )


def _append_three(store: AuditChainStore) -> tuple[AuditEvent, AuditEvent, AuditEvent]:
    return (
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id="one",
            payload={"n": 1},
        ),
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id="two",
            payload={"n": 2},
        ),
        store.append(
            event_type="test_event",
            actor="test",
            subject_type="test",
            subject_id="three",
            payload={"n": 3},
        ),
    )


def _tamper(database: SqliteDatabase, trigger: str, statement: str) -> None:
    with database.connect() as connection:
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        connection.execute(statement)


def _approval_sensitive_operation(gate: AuditIntegrityGate) -> str:
    """Stand-in for a future approval/execution path."""
    gate.require_intact()
    return "approved"


def test_valid_chain_survives_reopen_and_continues_sequence(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    first, second, third = _append_three(store)

    assert first.seq == 1
    assert first.prev_hash == GENESIS_PREV_HASH
    assert first.event_hash == compute_event_hash(first.prev_hash, first.canonical_body())
    assert second.prev_hash == first.event_hash
    assert third.prev_hash == second.event_hash

    status = store.status()
    assert status.ok is True
    assert status.event_count == 3
    assert status.head_hash == third.event_hash

    reopened = AuditChainStore(_db(tmp_path))
    fourth = reopened.append(
        event_type="test_event",
        actor="test",
        subject_type="test",
        subject_id="four",
        payload={"n": 4},
    )
    assert fourth.seq == 4
    assert fourth.prev_hash == third.event_hash
    assert reopened.status().ok is True


def test_empty_chain_is_valid() -> None:
    status = verify_chain(())
    assert status.ok is True
    assert status.event_count == 0
    assert status.head_hash == GENESIS_PREV_HASH


def test_append_only_triggers_reject_update_delete_and_broken_links(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    _append_three(store)

    with (
        pytest.raises(sqlite3.DatabaseError, match="append-only"),
        database.session(immediate=True) as connection,
    ):
        connection.execute("UPDATE audit_events SET actor = 'tampered' WHERE seq = 1")

    with (
        pytest.raises(sqlite3.DatabaseError, match="append-only"),
        database.session(immediate=True) as connection,
    ):
        connection.execute("DELETE FROM audit_events WHERE seq = 2")

    with (
        pytest.raises(sqlite3.DatabaseError, match="chain violation"),
        database.session(immediate=True) as connection,
    ):
        connection.execute(
            """
            INSERT INTO audit_events
                (seq, id, occurred_at, actor, event_type, subject_type,
                 subject_id, decision, payload_json, prev_hash, hash)
            VALUES (99, 'evt_direct', 0, 'test', 'test', 'test', 'test',
                    NULL, '{}', ?, ?)
            """,
            (GENESIS_PREV_HASH, "a" * 64),
        )


def test_altered_payload_is_detected(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    _append_three(store)
    _tamper(
        database,
        "audit_no_update",
        "UPDATE audit_events SET payload_json = '{\"n\":99}' WHERE seq = 2",
    )

    status = store.status()
    assert status.ok is False
    assert status.failed_seq == 2
    assert status.failure == "event hash mismatch at seq 2"
    assert '{"n":99}' not in (status.failure or "")


def test_altered_hash_and_previous_link_are_detected(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    _, second, _ = _append_three(store)

    _tamper(
        database,
        "audit_no_update",
        f"UPDATE audit_events SET hash = '{ALTERED_HASH}' WHERE seq = 2",
    )
    status = store.status()
    assert status.ok is False
    assert status.failed_seq == 2
    assert status.failure == "event hash mismatch at seq 2"

    _tamper(
        database,
        "audit_no_update",
        f"UPDATE audit_events SET hash = '{second.event_hash}' WHERE seq = 2",
    )
    _tamper(
        database,
        "audit_no_update",
        f"UPDATE audit_events SET prev_hash = '{GENESIS_PREV_HASH}' WHERE seq = 3",
    )
    status = store.status()
    assert status.ok is False
    assert status.failed_seq == 3
    assert status.failure == "previous hash mismatch at seq 3"


def test_deleted_and_reordered_events_are_detected(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    first, second, third = _append_three(store)

    reordered = verify_chain((second, first, third))
    assert reordered.ok is False
    assert reordered.failure == "sequence gap: expected 1, found 2"

    reorder_database = SqliteDatabase(tmp_path / "reorder.sqlite3")
    reorder_store = AuditChainStore(reorder_database)
    _append_three(reorder_store)
    with reorder_database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute("UPDATE audit_events SET seq = 99 WHERE seq = 1")
        connection.execute("UPDATE audit_events SET seq = 1 WHERE seq = 2")
        connection.execute("UPDATE audit_events SET seq = 2 WHERE seq = 99")
    reordered_rows = reorder_store.status()
    assert reordered_rows.ok is False
    assert reordered_rows.failed_seq == 1
    assert reordered_rows.failure == "previous hash mismatch at seq 1"

    _tamper(database, "audit_no_delete", "DELETE FROM audit_events WHERE seq = 2")
    status = store.status()
    assert status.ok is False
    assert status.failed_seq == 3
    assert status.failure == "sequence gap: expected 2, found 3"


def test_deleting_first_event_breaks_the_initial_state(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = AuditChainStore(database)
    _append_three(store)
    _tamper(database, "audit_no_delete", "DELETE FROM audit_events WHERE seq = 1")

    status = store.status()
    assert status.ok is False
    assert status.failed_seq == 2
    assert status.failure == "sequence gap: expected 1, found 2"


def test_integrity_failure_blocks_audit_and_provider_operations(tmp_path: Path) -> None:
    database = _db(tmp_path)
    consent_store = ConsentStore(database)
    consent_store.grant(_consent())
    sink = SqliteAuditSink(database)
    provider = FakeProvider()
    gateway = ModelGateway(provider, ConsentGate(lookup=consent_store), sink)
    integrity = AuditChainStore(database)

    assert _approval_sensitive_operation(integrity) == "approved"
    _tamper(
        database,
        "audit_no_update",
        "UPDATE audit_events SET payload_json = '{\"tampered\":true}' WHERE seq = 1",
    )

    with pytest.raises(AuditIntegrityError):
        integrity.require_intact()
    with pytest.raises(AuditIntegrityError):
        _approval_sensitive_operation(integrity)
    with pytest.raises(AuditIntegrityError):
        sink.record_egress(
            EgressDecision(
                provider="openai",
                model=MODEL,
                data_class="C1",
                decision="allow",
                reason="consent_granted",
                consent_id="cns_test",
                occurred_at="2026-11-21T00:00:00+00:00",
            )
        )
    with pytest.raises(AuditIntegrityError):
        asyncio.run(gateway.complete(_request()))

    assert provider.calls == 0
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM egress_decisions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0


def test_consent_egress_and_model_calls_are_all_chained(tmp_path: Path) -> None:
    database = _db(tmp_path)
    consent_store = ConsentStore(database)
    consent_store.grant(_consent())
    sink = SqliteAuditSink(database)
    provider = FakeProvider()
    gateway = ModelGateway(provider, ConsentGate(lookup=consent_store), sink)

    response = asyncio.run(gateway.complete(_request()))
    assert response.text == "response-text-marker"
    assert consent_store.revoke("cns_test") is True
    with pytest.raises(ConsentRequired):
        asyncio.run(gateway.complete(_request()))

    chain = AuditChainStore(database)
    status = chain.status()
    assert status.ok is True
    assert status.event_count == 6

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, decision, payload_json FROM audit_events ORDER BY seq"
        ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "consent_granted",
        "egress_decision",
        "model_call",
        "consent_revoked",
        "egress_decision",
        "model_call",
    ]
    assert [row["decision"] for row in rows] == [
        "grant",
        "allow",
        None,
        "revoke",
        "deny",
        None,
    ]
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert payloads[1]["consent_id"] == "cns_test"
    assert payloads[2]["status"] == "success"
    assert payloads[2]["request_hash"] == _request().request_hash
    assert payloads[4]["reason"] == "consent_required"
    assert payloads[5]["status"] == "consent_denied"
    assert provider.calls == 1


def test_chain_payloads_and_database_never_contain_request_or_secret(tmp_path: Path) -> None:
    database = _db(tmp_path)
    consent_store = ConsentStore(database)
    consent_store.grant(_consent())
    sink = SqliteAuditSink(database)
    gateway = ModelGateway(FakeProvider(), ConsentGate(lookup=consent_store), sink)
    asyncio.run(gateway.complete(_request()))

    with database.connect() as connection:
        rows = [
            dict(row)
            for table in ("audit_events", "egress_decisions", "model_calls")
            for row in connection.execute(f"SELECT * FROM {table}")
        ]
    stored = json.dumps(rows, default=str)
    for marker in ("prompt-marker", "response-text-marker", FAKE_KEY):
        assert marker not in stored

    for path in (
        database.path,
        Path(f"{database.path}-wal"),
        Path(f"{database.path}-shm"),
    ):
        if path.exists():
            raw = path.read_bytes()
            for marker in ("prompt-marker", "response-text-marker", FAKE_KEY):
                assert marker.encode() not in raw


def test_cli_verify_reports_pass_and_failure_without_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")

    empty = runner.invoke(app, ["audit", "verify"])
    assert empty.exit_code == 0, empty.output
    assert "audit_chain=ok events=0" in empty.output

    database = SqliteDatabase(tmp_path / "db" / "eis.sqlite3")
    store = AuditChainStore(database)
    store.append(
        event_type="test_event",
        actor="test",
        subject_type="test",
        subject_id="one",
        payload={"note": "cli-payload-marker"},
    )
    store.append(
        event_type="test_event",
        actor="test",
        subject_type="test",
        subject_id="two",
        payload={"n": 2},
    )

    passed = runner.invoke(app, ["audit", "verify"])
    assert passed.exit_code == 0, passed.output
    assert "audit_chain=ok events=2" in passed.output
    assert "cli-payload-marker" not in passed.output

    _tamper(
        database,
        "audit_no_update",
        'UPDATE audit_events SET payload_json = \'{"note":"cli-payload-marker"}\' WHERE seq = 2',
    )
    failed = runner.invoke(app, ["audit", "verify"])
    assert failed.exit_code == 1
    assert "audit_chain=failed" in failed.output
    assert "event hash mismatch at seq 2" in failed.output
    assert "cli-payload-marker" not in failed.output


def test_chain_status_type_is_usable_by_future_paths() -> None:
    status: ChainStatus = verify_chain(())
    assert status.ok is True
