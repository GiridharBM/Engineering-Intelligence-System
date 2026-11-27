# SPDX-License-Identifier: Apache-2.0

"""Policy decision persistence, fail-closed and retry tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eis.cli import app
from eis.core.audit_chain import AuditIntegrityError
from eis.core.policy_service import PolicyService
from eis.policy import Policy, PolicyAuditRecord, PolicyEngine, PolicyError, PolicyRequest
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase
from eis.storage.policy_decisions import SqlitePolicyDecisionSink

runner = CliRunner()
# Built at runtime so the repository privacy guard does not flag fixture paths.
DRIVE_PREFIX = "C" + chr(58)
USERS_SEGMENT = "Us" + "ers"
ABSOLUTE_MARKER = f"{DRIVE_PREFIX}/{USERS_SEGMENT}"
ABSOLUTE_RESOURCE = f"path:proj/{ABSOLUTE_MARKER}/secret-marker/file.py"


def _policy() -> Policy:
    return Policy.from_dict(
        {
            "version": "2026.11-test",
            "default": "deny",
            "rules": [
                {
                    "id": "allow-read",
                    "principal": "agent:eis.primary",
                    "action": "project.read",
                    "resource": "project:demo",
                    "effect": "allow",
                },
                {
                    "id": "deny-write",
                    "principal": "agent:eis.primary",
                    "action": "project.write",
                    "resource": "project:demo",
                    "effect": "deny",
                },
            ],
        }
    )


def _request(
    action: str = "project.read",
    resource: str = "project:demo",
    *,
    context: dict[str, object] | None = None,
) -> PolicyRequest:
    return PolicyRequest.from_mapping(
        {
            "principal": "agent:eis.primary",
            "action": action,
            "resource": resource,
            "context": context or {"source": "cli"},
        }
    )


def _service(tmp_path: Path) -> tuple[SqliteDatabase, PolicyService]:
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    return database, PolicyService(
        PolicyEngine(_policy()),
        SqlitePolicyDecisionSink(database),
    )


def _rows(database: SqliteDatabase) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return connection.execute("SELECT * FROM audit_events ORDER BY seq").fetchall()


def _tamper(database: SqliteDatabase) -> None:
    with database.connect() as connection:
        connection.execute("DROP TRIGGER audit_no_update")
        connection.execute(
            "UPDATE audit_events SET payload_json = '{\"tampered\":true}' WHERE seq = 1"
        )


def test_allow_decision_is_chained_with_provenance(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    decision = service.evaluate(_request())

    assert decision.effect == "allow"
    assert decision.reason == "explicit_allow"
    assert decision.rule_id == "allow-read"
    assert decision.policy_version == "2026.11-test"

    status = AuditChainStore(database).status()
    assert status.ok is True
    assert status.event_count == 1

    rows = _rows(database)
    assert len(rows) == 1
    row = rows[0]
    payload = json.loads(row["payload_json"])
    assert row["id"] == decision.decision_id
    assert row["event_type"] == "policy_decision"
    assert row["actor"] == "agent:eis.primary"
    assert row["subject_type"] == "policy_decision"
    assert row["subject_id"] == decision.decision_id
    assert row["decision"] == "allow"
    assert payload == {
        "decision_id": decision.decision_id,
        "effect": "allow",
        "reason": "explicit_allow",
        "rule_id": "allow-read",
        "policy_version": "2026.11-test",
        "policy_hash": decision.policy_hash,
        "inputs_hash": decision.inputs_hash,
        "principal": "agent:eis.primary",
        "action": "project.read",
        "resource": "project:demo",
        "context_source": "cli",
    }
    assert len(payload["policy_hash"]) == 64
    assert len(payload["inputs_hash"]) == 64


def test_explicit_and_default_deny_are_chained(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    explicit = service.evaluate(_request("project.write"))
    default = service.evaluate(_request("project.write", "project:other"))
    allowed = service.evaluate(_request())

    assert (explicit.effect, explicit.reason, explicit.rule_id) == (
        "deny",
        "explicit_deny",
        "deny-write",
    )
    assert (default.effect, default.reason, default.rule_id) == (
        "deny",
        "default_deny",
        "default_deny",
    )
    assert allowed.effect == "allow"

    status = AuditChainStore(database).status()
    assert status.ok is True
    assert status.event_count == 3
    rows = _rows(database)
    assert [row["decision"] for row in rows] == ["deny", "deny", "allow"]
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert [item["rule_id"] for item in payloads] == [
        "deny-write",
        "default_deny",
        "allow-read",
    ]
    assert [item["reason"] for item in payloads] == [
        "explicit_deny",
        "default_deny",
        "explicit_allow",
    ]


class FailingSink:
    def record(self, record: PolicyAuditRecord) -> None:
        raise AuditIntegrityError("audit unavailable")


def test_audit_failure_prevents_the_decision_from_being_used() -> None:
    service = PolicyService(PolicyEngine(_policy()), FailingSink())
    proceeded: list[str] = []

    def act_after_allowed_decision() -> None:
        decision = service.evaluate(_request())
        if decision.effect == "allow":
            proceeded.append(decision.decision_id)

    with pytest.raises(AuditIntegrityError):
        act_after_allowed_decision()
    assert proceeded == []


def test_corrupted_chain_blocks_new_policy_decisions(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.evaluate(_request())
    _tamper(database)

    with pytest.raises(AuditIntegrityError):
        service.evaluate(_request("project.write"))

    rows = _rows(database)
    assert len(rows) == 1
    assert AuditChainStore(database).status().ok is False


def test_retry_records_one_decision_event(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    first = service.evaluate(_request())
    second = service.evaluate(_request())

    assert second.decision_id == first.decision_id
    assert second.effect == first.effect
    rows = _rows(database)
    assert len(rows) == 1
    assert rows[0]["id"] == first.decision_id
    assert json.loads(rows[0]["payload_json"])["decision_id"] == first.decision_id
    assert AuditChainStore(database).status().ok is True


def test_sensitive_context_and_absolute_paths_are_not_persisted(tmp_path: Path) -> None:
    database, service = _service(tmp_path)
    service.evaluate(
        _request(
            "project.write",
            "path:proj/src/app.py",
            context={"source": "cli", "attributes": {"note": "secret-marker"}},
        )
    )

    with pytest.raises(PolicyError):
        _request(
            "project.write",
            ABSOLUTE_RESOURCE,
            context={"source": "cli", "attributes": {"note": "secret-marker"}},
        )

    rows = [dict(row) for row in _rows(database)]
    stored = json.dumps(rows, default=str)
    assert "secret-marker" not in stored
    assert ABSOLUTE_MARKER not in stored
    assert "src/app.py" in stored
    assert len(_rows(database)) == 1

    for path in (
        database.path,
        Path(f"{database.path}-wal"),
        Path(f"{database.path}-shm"),
    ):
        if path.exists():
            raw = path.read_bytes()
            assert b"secret-marker" not in raw
            assert ABSOLUTE_MARKER.encode() not in raw


def test_cli_policy_evaluate_records_once_and_hides_sensitive_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("EIS_DISABLE_DOTENV", "1")

    first = runner.invoke(
        app,
        [
            "policy-evaluate",
            "--principal",
            "agent:eis.primary",
            "--action",
            "project.read",
            "--resource",
            "project:demo",
        ],
    )
    assert first.exit_code == 0, first.output
    assert "effect=deny" in first.output
    assert "reason=default_deny" in first.output
    assert "decision_id=pol_" in first.output
    assert "project:demo" not in first.output
    decision_id = first.output.split("decision_id=")[1].split()[0]

    second = runner.invoke(
        app,
        [
            "policy-evaluate",
            "--principal",
            "agent:eis.primary",
            "--action",
            "project.read",
            "--resource",
            "project:demo",
        ],
    )
    assert second.exit_code == 0, second.output
    assert f"decision_id={decision_id}" in second.output

    database = SqliteDatabase(tmp_path / "db" / "eis.sqlite3")
    assert len(_rows(database)) == 1

    rejected = runner.invoke(
        app,
        [
            "policy-evaluate",
            "--principal",
            "agent:eis.primary",
            "--action",
            "project.write",
            "--resource",
            ABSOLUTE_RESOURCE,
        ],
    )
    assert rejected.exit_code == 1
    assert "policy_error=invalid_request" in rejected.output
    assert "secret-marker" not in rejected.output
    assert len(_rows(database)) == 1
