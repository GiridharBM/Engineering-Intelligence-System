# SPDX-License-Identifier: Apache-2.0

"""SQLite migration, consent and audit persistence tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine

from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)
from eis.config import ConsentRecord, ProviderConfig, Settings
from eis.core.audit import EgressDecision, ModelCallAudit
from eis.core.audit_chain import (
    GENESIS_PREV_HASH,
    AuditEvent,
    canonical_event_json,
    compute_event_hash,
)
from eis.core.consent import ConsentGate, ConsentRequired
from eis.core.model_gateway import ModelGateway
from eis.storage.audit import SqliteAuditSink
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.gateway import build_sqlite_gateway
from eis.storage.policy_decision_store import PolicyDecisionStore

FAKE_KEY = "test-only-secret-value"
PROVIDER = "openai"
MODEL = "test-model"


def _db(tmp_path: Path) -> SqliteDatabase:
    return SqliteDatabase(tmp_path / "eis.sqlite3")


def _consent(
    consent_id: str = "cns_test",
    *,
    data_classes: frozenset[str] = frozenset({"C0", "C1"}),
    expires_at_ms: int | None = None,
) -> ConsentRecord:
    return ConsentRecord(
        consent_id=consent_id,
        provider="openai",
        data_classes=frozenset(data_classes),  # type: ignore[arg-type]
        purpose="tests",
        expires_at_ms=expires_at_ms,
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


def test_migration_creates_expected_tables_and_pragmas(tmp_path: Path) -> None:
    database = _db(tmp_path)
    database.migrate()
    database.migrate()  # idempotent

    with database.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "consents",
            "egress_decisions",
            "model_calls",
            "audit_events",
            "alembic_version",
            "policy_decisions",
            "approvals",
            "execution_runs",
            "sessions",
            "tasks",
            "project_roots",
            "task_plans",
            "task_plan_steps",
        } <= tables
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        triggers = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {
            "audit_chain_insert",
            "audit_no_update",
            "audit_no_delete",
            "policy_decisions_no_update",
            "policy_decisions_no_delete",
            "approvals_binding_immutable",
            "approvals_task_binding_immutable",
            "approvals_status_transition",
            "approvals_decision_note_once",
            "execution_runs_no_update",
            "execution_runs_no_delete",
            "sessions_state_transition",
            "tasks_state_transition",
            "task_plans_state_transition",
            "task_plan_steps_status_transition",
            "task_plan_step_arguments_immutable",
            "task_plan_step_attempt_guard",
        } <= triggers
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version[0] == "0009_task_cancellation_review"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(task_plan_steps)")}
        assert {"arguments_json", "attempt_count"} <= columns


def test_consent_persists_reloads_and_revokes(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = ConsentStore(database)
    record = _consent()
    store.grant(record)

    reloaded = ConsentStore(_db(tmp_path))
    assert reloaded.find_active("openai", "C1", 1_000) is not None
    assert reloaded.find_active("openai", "C2", 1_000) is None
    assert reloaded.find_active("anthropic", "C1", 1_000) is None

    assert reloaded.revoke(record.consent_id) is True
    assert reloaded.revoke(record.consent_id) is False
    assert reloaded.find_active("openai", "C1", 1_000) is None

    reloaded.ensure(record)
    assert reloaded.find_active("openai", "C1", 1_000) is None


def test_consent_expiry_is_enforced(tmp_path: Path) -> None:
    store = ConsentStore(_db(tmp_path))
    store.grant(_consent(expires_at_ms=2_000))
    assert store.find_active("openai", "C1", 1_999) is not None
    assert store.find_active("openai", "C1", 2_000) is None


def test_consent_gate_uses_durable_lookup_and_denies_c3(tmp_path: Path) -> None:
    store = ConsentStore(_db(tmp_path))
    store.grant(_consent(data_classes=frozenset({"C0"})))
    gate = ConsentGate(lookup=store, clock_ms=lambda: 1_000)
    assert gate.authorize("openai", "C0").consent_id == "cns_test"
    with pytest.raises(ConsentRequired):
        gate.authorize("openai", "C1")
    with pytest.raises(ConsentRequired):
        gate.authorize("openai", "C3")


def test_in_memory_gate_honours_revocation_and_expiry() -> None:
    gate = ConsentGate(
        [_consent(data_classes=frozenset({"C1"}), expires_at_ms=2_000)],
        clock_ms=lambda: 1_999,
    )
    assert gate.authorize("openai", "C1").consent_id == "cns_test"
    expired = ConsentGate(
        [_consent(data_classes=frozenset({"C1"}), expires_at_ms=2_000)],
        clock_ms=lambda: 2_000,
    )
    with pytest.raises(ConsentRequired):
        expired.authorize("openai", "C1")


def test_audit_records_persist_and_contain_no_request_or_secret(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = ConsentStore(database)
    store.grant(_consent())
    sink = SqliteAuditSink(database)
    sink.record_egress(
        EgressDecision(
            provider="openai",
            model=MODEL,
            data_class="C1",
            decision="allow",
            reason="consent_granted",
            consent_id="cns_test",
            occurred_at="2026-11-20T00:00:00+00:00",
        )
    )
    sink.record_model_call(
        ModelCallAudit(
            provider="openai",
            model=MODEL,
            request_hash="a" * 64,
            started_at="2026-11-20T00:00:01+00:00",
            latency_ms=12.5,
            status="success",
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
            provider_request_id="req_test",
            consent_id="cns_test",
        )
    )

    restarted = SqliteAuditSink(_db(tmp_path))
    with restarted.database.connect() as connection:
        egress = connection.execute("SELECT * FROM egress_decisions").fetchone()
        call = connection.execute("SELECT * FROM model_calls").fetchone()
    assert egress["consent_id"] == "cns_test"
    assert egress["decision"] == "allow"
    assert call["provider"] == "openai"
    assert call["request_hash"] == "a" * 64
    assert json.loads(call["usage_json"]) == {
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }
    assert call["provider_request_id"] == "req_test"

    raw = (tmp_path / "eis.sqlite3").read_bytes()
    assert FAKE_KEY.encode() not in raw
    assert b"prompt-marker" not in raw
    assert b"response-text-marker" not in raw


def test_gateway_persists_allow_and_denies_after_revocation(tmp_path: Path) -> None:
    database = _db(tmp_path)
    store = ConsentStore(database)
    store.grant(_consent())
    sink = SqliteAuditSink(database)
    provider = FakeProvider()
    gateway = ModelGateway(provider, ConsentGate(lookup=store), sink)
    request = CompletionRequest(
        messages=(ChatMessage(role="user", content="prompt-marker"),),
        max_tokens=16,
    )

    response = asyncio.run(gateway.complete(request))
    assert response.text == "response-text-marker"
    assert provider.calls == 1

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM egress_decisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 1

    assert store.revoke("cns_test") is True
    with pytest.raises(ConsentRequired):
        asyncio.run(gateway.complete(request))
    assert provider.calls == 1

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM egress_decisions").fetchone()[0] == 2
        denied_call = connection.execute(
            "SELECT status, egress_consent_id FROM model_calls ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert denied_call["status"] == "consent_denied"
    assert denied_call["egress_consent_id"] is None

    raw = (tmp_path / "eis.sqlite3").read_bytes()
    assert b"prompt-marker" not in raw
    assert b"response-text-marker" not in raw
    assert FAKE_KEY.encode() not in raw


def test_sqlite_gateway_factory_persists_env_consent_and_call(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        config_dir=tmp_path,
        model_provider="openai",
        provider_config=ProviderConfig(
            provider="openai",
            model=MODEL,
            api_key=SecretStr(FAKE_KEY),
            base_url="https://api.openai.com/v1",
        ),
        consent=_consent(),
        allow_cloud=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "req_test",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = build_sqlite_gateway(settings, client=client)
            return await gateway.complete(
                CompletionRequest(
                    messages=(ChatMessage(role="user", content="prompt-marker"),),
                    max_tokens=16,
                )
            )

    response = asyncio.run(run())
    assert response.text == "ok"

    database = SqliteDatabase(tmp_path / "db" / "eis.sqlite3")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM consents").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM egress_decisions").fetchone()[0] == 1
        call = connection.execute("SELECT request_hash, status FROM model_calls").fetchone()
    assert len(call["request_hash"]) == 64
    assert call["status"] == "success"

    raw = (tmp_path / "db" / "eis.sqlite3").read_bytes()
    assert b"prompt-marker" not in raw
    assert FAKE_KEY.encode() not in raw


def _upgrade_database(database: SqliteDatabase, revision: str) -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[1] / "src" / "eis" / "storage" / "migrations"),
    )
    engine = create_engine(f"sqlite+pysqlite:///{database.path.as_posix()}")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    finally:
        engine.dispose()


def test_migration_backfills_existing_policy_decision_events(tmp_path: Path) -> None:
    database = _db(tmp_path)
    _upgrade_database(database, "0002_audit_chain")

    payload: dict[str, object] = {
        "decision_id": "pol_legacy",
        "effect": "allow",
        "reason": "explicit_allow",
        "rule_id": "legacy-allow",
        "policy_version": "legacy-v1",
        "policy_hash": "a" * 64,
        "inputs_hash": "b" * 64,
        "principal": "user",
        "action": "project.read",
        "resource": "project:demo",
        "context_source": "cli",
    }
    draft = AuditEvent(
        seq=1,
        event_id="pol_legacy",
        occurred_at_ms=1_700_000_000_000,
        actor="user",
        event_type="policy_decision",
        subject_type="policy_decision",
        subject_id="pol_legacy",
        decision="allow",
        payload=payload,
        prev_hash=GENESIS_PREV_HASH,
        event_hash="",
    )
    event_hash = compute_event_hash(GENESIS_PREV_HASH, draft.canonical_body())
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO audit_events
                (seq, id, occurred_at, actor, event_type, subject_type,
                 subject_id, decision, payload_json, prev_hash, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft.seq,
                draft.event_id,
                draft.occurred_at_ms,
                draft.actor,
                draft.event_type,
                draft.subject_type,
                draft.subject_id,
                draft.decision,
                canonical_event_json(draft.payload),
                draft.prev_hash,
                event_hash,
            ),
        )

    _upgrade_database(database, "head")

    stored = PolicyDecisionStore(database).get("pol_legacy")
    assert stored is not None
    assert stored.policy_version == "legacy-v1"
    assert stored.effect == "allow"
    assert stored.rule_id == "legacy-allow"
    assert stored.context_source == "cli"
    assert stored.created_at_ms == draft.occurred_at_ms


def test_migration_adds_project_roots_without_fabricating_registrations(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    _upgrade_database(database, "0004_sessions_tasks")
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions
                (id, title, status, project_id, created_at, updated_at, closed_at)
            VALUES ('ses_legacy', 'Legacy', 'active', 'demo', 1, 1, NULL)
            """
        )

    _upgrade_database(database, "head")

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_roots").fetchone()[0] == 0
        row = connection.execute(
            "SELECT project_id FROM sessions WHERE id = 'ses_legacy'"
        ).fetchone()
    assert row["project_id"] == "demo"
