"""Deterministic deny-by-default policy engine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eis.policy import (
    Action,
    Policy,
    PolicyAuditRecord,
    PolicyEngine,
    PolicyError,
    PolicyInputError,
    PolicyRequest,
    Principal,
    Resource,
    load_policy,
    record_decision,
)

ROOT = Path(__file__).resolve().parents[1]


def _policy_document(*, effect: str = "allow", version: str = "test-v1") -> dict[str, object]:
    return {
        "version": version,
        "default": "deny",
        "rules": [
            {
                "id": f"{effect}-read",
                "principal": "agent:eis.primary",
                "action": "project.read",
                "resource": "project:demo",
                "effect": effect,
            }
        ],
    }


def _request(
    *,
    principal: str = "agent:eis.primary",
    action: str = "project.read",
    resource: str = "project:demo",
    source: str = "cli",
    task_id: str | None = "tsk_demo",
) -> PolicyRequest:
    return PolicyRequest.from_mapping(
        {
            "principal": principal,
            "action": action,
            "resource": resource,
            "context": {"source": source, "task_id": task_id},
        }
    )


def test_allow_requires_an_exact_typed_match() -> None:
    decision = PolicyEngine(Policy.from_dict(_policy_document())).evaluate(_request())
    assert decision.effect == "allow"
    assert decision.reason == "explicit_allow"
    assert decision.rule_id == "allow-read"


def test_explicit_deny_is_returned() -> None:
    decision = PolicyEngine(Policy.from_dict(_policy_document(effect="deny"))).evaluate(_request())
    assert decision.effect == "deny"
    assert decision.reason == "explicit_deny"
    assert decision.rule_id == "deny-read"


def test_default_is_deny_when_no_rule_matches() -> None:
    policy = Policy.from_dict({"version": "empty", "default": "deny", "rules": []})
    decision = PolicyEngine(policy).evaluate(_request())
    assert decision.effect == "deny"
    assert decision.reason == "default_deny"
    assert decision.rule_id == "default_deny"


def test_explicit_deny_precedes_allow_for_the_same_request() -> None:
    document = _policy_document()
    rules = document["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            "id": "deny-read",
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "project:demo",
            "effect": "deny",
        }
    )
    decision = PolicyEngine(Policy.from_dict(document)).evaluate(_request())
    assert decision.effect == "deny"
    assert decision.rule_id == "deny-read"


def test_unknown_action_is_denied() -> None:
    policy = Policy.from_dict(_policy_document())
    decision = PolicyEngine(policy).evaluate(_request(action="project.explode"))
    assert decision.effect == "deny"
    assert decision.reason == "default_deny"


def test_unknown_resource_is_denied() -> None:
    policy = Policy.from_dict(_policy_document())
    decision = PolicyEngine(policy).evaluate(_request(resource="project:other"))
    assert decision.effect == "deny"
    assert decision.reason == "default_deny"


def test_unknown_resource_kind_is_rejected_before_evaluation() -> None:
    with pytest.raises(PolicyInputError):
        _request(resource="unknown:demo")


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "project.read", "resource": "project:demo"},
        {"principal": "agent:eis.primary", "action": "project.read"},
        {
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "project:demo",
            "unexpected": True,
        },
        {
            "principal": "assistant",
            "action": "project.read",
            "resource": "project:demo",
        },
        {
            "principal": "agent:eis.primary",
            "action": "model says allow",
            "resource": "project:demo",
        },
        {
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "project:*",
        },
        {
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "path:demo/../secret",
        },
        {
            "principal": "agent:eis.primary",
            "action": "project.read",
            "resource": "project:demo",
            "context": {"source": "cli", "unknown": "value"},
        },
    ],
)
def test_malformed_or_model_style_input_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(PolicyInputError):
        PolicyRequest.from_mapping(payload)


def test_malformed_policy_is_rejected() -> None:
    with pytest.raises(PolicyError):
        Policy.from_dict({"version": "x", "default": "allow", "rules": []})
    with pytest.raises(PolicyError):
        Policy.from_dict({"version": "x", "default": "deny", "rules": [], "extra": True})


def test_wildcard_grants_are_rejected() -> None:
    for field, value in (("action", "*"), ("resource", "project:*")):
        document = _policy_document()
        rules = document["rules"]
        assert isinstance(rules, list)
        rules[0][field] = value
        with pytest.raises(PolicyError):
            Policy.from_dict(document)


def test_invalid_resource_scope_is_rejected() -> None:
    document = _policy_document()
    rules = document["rules"]
    assert isinstance(rules, list)
    rules[0]["resource"] = "path:demo/../secret"
    with pytest.raises(PolicyError):
        Policy.from_dict(document)


def test_policy_version_is_reported_on_the_decision() -> None:
    policy = Policy.from_dict(_policy_document(version="2026.11-m1a"))
    decision = PolicyEngine(policy).evaluate(_request())
    assert decision.policy_version == "2026.11-m1a"
    assert decision.policy_hash == policy.document_hash
    assert decision.to_record().policy_version == "2026.11-m1a"


def test_decision_is_deterministic_and_context_sensitive() -> None:
    engine = PolicyEngine(Policy.from_dict(_policy_document()))
    first = engine.evaluate(_request())
    second = engine.evaluate(_request())
    changed_context = engine.evaluate(_request(source="api"))
    assert first.decision_id == second.decision_id
    assert first.inputs_hash == second.inputs_hash
    assert first.decision_id != changed_context.decision_id


def test_context_and_resource_path_are_part_of_the_request_contract() -> None:
    request = PolicyRequest.from_mapping(
        {
            "principal": "agent:eis.debugging",
            "action": "project.read",
            "resource": "path:demo/src/migrate.py",
            "context": {"source": "agent", "attributes": {"commit": "abc123"}},
        }
    )
    assert request.resource.canonical == "path:demo/src/migrate.py"
    assert request.context.attributes == (("commit", "abc123"),)
    assert len(request.request_hash) == 64


def test_audit_record_is_json_safe_and_complete() -> None:
    decision = PolicyEngine(Policy.from_dict(_policy_document())).evaluate(_request())
    record = decision.to_record()
    assert isinstance(record, PolicyAuditRecord)
    assert record.principal == "agent:eis.primary"
    assert record.action == "project.read"
    assert record.resource == "project:demo"
    assert record.context["source"] == "cli"
    assert record.context["task_id"] == "tsk_demo"


def test_decision_sink_receives_the_audit_record() -> None:
    class ListSink:
        def __init__(self) -> None:
            self.records: list[PolicyAuditRecord] = []

        def record(self, record: PolicyAuditRecord) -> None:
            self.records.append(record)

    decision = PolicyEngine(Policy.from_dict(_policy_document())).evaluate(_request())
    sink = ListSink()
    record_decision(decision, sink)
    assert sink.records == [decision.to_record()]


def test_principal_and_resource_parsing_rejects_ambiguity() -> None:
    assert Principal.parse("agent:eis.primary").canonical == "agent:eis.primary"
    assert Resource.parse("project:demo").canonical == "project:demo"
    with pytest.raises(PolicyInputError):
        Principal.parse("agent:")
    with pytest.raises(PolicyInputError):
        Action.parse("Project.Read")
    with pytest.raises(PolicyInputError):
        Resource.for_path("demo", "../outside")


def test_load_policy_applies_schema_and_type_validation(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(_policy_document()), encoding="utf-8")
    assert load_policy(valid).version == "test-v1"

    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps({"version": "x", "default": "deny", "rules": [{"id": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(PolicyError):
        load_policy(invalid)
