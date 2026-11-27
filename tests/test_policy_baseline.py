"""Policy schema, loading and audit-contract baseline tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from eis.policy import (
    Policy,
    PolicyEngine,
    PolicyError,
    PolicyRequest,
    load_default_policy,
    load_policy,
    record_decision,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_SCHEMA = json.loads((ROOT / "schemas" / "policy.schema.json").read_text(encoding="utf-8"))
DECISION_SCHEMA = json.loads(
    (ROOT / "schemas" / "policy-decision.schema.json").read_text(encoding="utf-8")
)


def _allow_policy() -> dict[str, object]:
    return {
        "version": "test",
        "default": "deny",
        "rules": [
            {
                "id": "allow-read",
                "principal": "agent:eis.primary",
                "action": "project.read",
                "resource": "project:demo",
                "effect": "allow",
            }
        ],
    }


def _request(**context: str) -> PolicyRequest:
    return PolicyRequest.from_mapping(
        {
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "project:demo",
            "context": {"source": "cli", **context},
        }
    )


def test_default_policy_is_valid_and_denies_by_default() -> None:
    document = json.loads((ROOT / "policies" / "default.json").read_text(encoding="utf-8"))
    validate(instance=document, schema=POLICY_SCHEMA)
    policy = load_default_policy()
    assert policy.default == "deny"
    assert policy.rules == ()
    decision = PolicyEngine(policy).evaluate(_request())
    assert decision.effect == "deny"
    assert decision.reason == "default_deny"
    validate(instance=decision.as_dict(), schema=DECISION_SCHEMA)


def test_decision_reports_policy_version_and_hashes() -> None:
    policy = Policy.from_dict(_allow_policy())
    decision = PolicyEngine(policy).evaluate(_request())
    assert decision.policy_version == policy.version == "test"
    assert len(decision.policy_hash) == 64
    assert len(decision.inputs_hash) == 64
    assert decision.decision_id.startswith("pol_")


def test_decision_audit_record_has_the_contract_fields() -> None:
    policy = Policy.from_dict(_allow_policy())
    decision = PolicyEngine(policy).evaluate(_request())
    record = decision.to_record()
    assert record.principal == "agent:eis.primary"
    assert record.action == "project.read"
    assert record.resource == "project:demo"
    assert record.effect == "allow"
    assert record.context["source"] == "cli"
    validate(instance=record.as_dict(), schema=DECISION_SCHEMA)


def test_decision_sink_contract_receives_the_record() -> None:
    class ListSink:
        def __init__(self) -> None:
            self.records: list[object] = []

        def record(self, record: object) -> None:
            self.records.append(record)

    policy = Policy.from_dict(_allow_policy())
    decision = PolicyEngine(policy).evaluate(_request())
    sink = ListSink()
    record_decision(decision, sink)
    assert len(sink.records) == 1
    assert sink.records[0] == decision.to_record()


def test_malformed_policy_file_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "policy.json"
    bad.write_text('{"version": "x", "default": "allow", "rules": []}', encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(bad)


def test_schema_rejects_wildcard_grants() -> None:
    document = _allow_policy()
    rules = document["rules"]
    assert isinstance(rules, list)
    rules[0]["resource"] = "project:*"
    with pytest.raises(ValidationError):
        validate(instance=document, schema=POLICY_SCHEMA)
