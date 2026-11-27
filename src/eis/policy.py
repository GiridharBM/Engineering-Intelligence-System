# SPDX-License-Identifier: Apache-2.0

"""Deterministic, deny-by-default policy engine (first M1 slice).

The engine evaluates a typed request against a schema-validated policy. It has
no model, network or filesystem dependency at evaluation time, and it never
writes storage. The caller is responsible for executing nothing when the
decision is deny or when evaluation raises ``PolicyInputError``.

Persistence lives at the service boundary: ``PolicyService`` evaluates with
this engine and records the decision before returning it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Literal, Protocol, cast

import jsonschema

Effect = Literal["allow", "deny"]
Reason = Literal["explicit_allow", "explicit_deny", "default_deny"]
PrincipalKind = Literal["user", "agent", "skill", "job", "system"]
ResourceKind = Literal[
    "project",
    "path",
    "tool",
    "host",
    "skill",
    "artifact",
    "record",
    "global",
]
RequestSource = Literal["user", "cli", "api", "agent", "skill", "job", "system"]

_POLICY_KEYS = frozenset({"version", "default", "rules"})
_RULE_KEYS = frozenset({"id", "principal", "action", "resource", "effect"})
_PRINCIPAL_KINDS = frozenset({"user", "agent", "skill", "job", "system"})
_RESOURCE_KINDS = frozenset(
    {"project", "path", "tool", "host", "skill", "artifact", "record", "global"}
)
_REQUEST_SOURCES = frozenset({"user", "cli", "api", "agent", "skill", "job", "system"})

_ACTION_RE = re.compile(r"^[a-z][a-z0-9_.]*$")
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_AGENT_RE = re.compile(r"^[a-z][a-z0-9._-]*$")
_SKILL_RE = re.compile(r"^[a-z0-9-]+@[0-9]+\.[0-9]+\.[0-9]+$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TOOL_RE = re.compile(r"^[a-z][a-z0-9._-]*@[A-Za-z0-9._-]+$")
_HOST_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")


class PolicyError(ValueError):
    """Raised when a policy or request is malformed; callers must fail closed."""


class PolicyInputError(PolicyError):
    """Raised when a request field is invalid or unknown."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PolicyInputError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise PolicyInputError(f"{label} keys must be strings")
        result[key] = item
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyInputError(f"{label} must be a non-empty string")
    return value


def _no_wildcard(value: str, label: str) -> None:
    if "*" in value:
        raise PolicyInputError(f"{label} must not contain a wildcard")


def _validate_project_id(value: str, label: str = "project_id") -> str:
    text = _text(value, label)
    _no_wildcard(text, label)
    if not _PROJECT_RE.fullmatch(text):
        raise PolicyInputError(f"{label} is not a valid project identifier")
    return text


def _validate_relative_path(value: str) -> str:
    text = _text(value, "path")
    if text.startswith(("/", "\\")) or "\\" in text or "*" in text:
        raise PolicyInputError("path must be a relative POSIX path without wildcards")
    if re.match(r"^[A-Za-z]:", text):
        raise PolicyInputError("path must not contain a drive-letter prefix")
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PolicyInputError("path must not contain empty, '.' or '..' segments")
    return text


@dataclass(frozen=True)
class Principal:
    """A typed actor: user, agent role, skill version, job or system."""

    kind: PrincipalKind
    identifier: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _PRINCIPAL_KINDS:
            raise PolicyInputError(f"unknown principal kind: {self.kind}")
        if self.kind in {"user", "system"}:
            if self.identifier:
                raise PolicyInputError(f"{self.kind} principal must not have an identifier")
            return
        identifier = _text(self.identifier, f"principal.{self.kind}")
        _no_wildcard(identifier, f"principal.{self.kind}")
        if self.kind == "agent" and not _AGENT_RE.fullmatch(identifier):
            raise PolicyInputError("agent identifier is invalid")
        if self.kind == "skill" and not _SKILL_RE.fullmatch(identifier):
            raise PolicyInputError("skill identifier must be name@major.minor.patch")
        if self.kind == "job" and not _JOB_RE.fullmatch(identifier):
            raise PolicyInputError("job identifier is invalid")

    @property
    def canonical(self) -> str:
        return self.kind if self.kind in {"user", "system"} else f"{self.kind}:{self.identifier}"

    @classmethod
    def parse(cls, value: str) -> Principal:
        text = _text(value, "principal")
        if text in {"user", "system"}:
            return cls(cast(PrincipalKind, text))
        kind, separator, identifier = text.partition(":")
        if not separator or kind not in {"agent", "skill", "job"}:
            raise PolicyInputError(f"unknown principal: {text}")
        return cls(cast(PrincipalKind, kind), identifier)

    @classmethod
    def from_value(cls, value: object) -> Principal:
        if isinstance(value, Principal):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise PolicyInputError("principal must be a string or Principal")


@dataclass(frozen=True)
class Action:
    """A validated action name such as ``project.read``."""

    name: str

    def __post_init__(self) -> None:
        name = _text(self.name, "action")
        _no_wildcard(name, "action")
        if not _ACTION_RE.fullmatch(name):
            raise PolicyInputError("action must match ^[a-z][a-z0-9_.]*$")

    @classmethod
    def parse(cls, value: str) -> Action:
        return cls(value)

    @classmethod
    def from_value(cls, value: object) -> Action:
        if isinstance(value, Action):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise PolicyInputError("action must be a string or Action")


@dataclass(frozen=True)
class Resource:
    """A scoped resource.

    Canonical forms:
    - ``global``
    - ``project:<id>``
    - ``path:<project-id>/<relative-path>``
    - ``tool:<name>@<version>``
    - ``host:<scheme://host:port>``
    - ``skill:<name>@<version>``
    - ``artifact:<id>``
    - ``record:<id>``
    """

    kind: ResourceKind
    identifier: str = ""
    project_id: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _RESOURCE_KINDS:
            raise PolicyInputError(f"unknown resource kind: {self.kind}")
        if self.kind == "global":
            if self.identifier or self.project_id or self.path:
                raise PolicyInputError("global resource must not carry an identifier")
            return
        if self.kind == "path":
            if self.identifier or self.project_id is None or self.path is None:
                raise PolicyInputError("path resource requires project_id and path")
            _validate_project_id(self.project_id)
            _validate_relative_path(self.path)
            return
        if not self.identifier or self.project_id is not None or self.path is not None:
            raise PolicyInputError(f"{self.kind} resource requires only an identifier")
        identifier = _text(self.identifier, f"resource.{self.kind}")
        _no_wildcard(identifier, f"resource.{self.kind}")
        if self.kind == "project":
            _validate_project_id(identifier, "resource.project")
        elif self.kind == "tool" and not _TOOL_RE.fullmatch(identifier):
            raise PolicyInputError("tool resource must be name@version")
        elif self.kind == "host" and not _HOST_RE.fullmatch(identifier):
            raise PolicyInputError("host resource must be a scheme://host[:port] value")
        elif self.kind == "skill" and not _SKILL_RE.fullmatch(identifier):
            raise PolicyInputError("skill resource must be name@major.minor.patch")

    @property
    def canonical(self) -> str:
        if self.kind == "global":
            return "global"
        if self.kind == "path":
            return f"path:{self.project_id}/{self.path}"
        return f"{self.kind}:{self.identifier}"

    @classmethod
    def parse(cls, value: str) -> Resource:
        text = _text(value, "resource")
        _no_wildcard(text, "resource")
        if text == "global":
            return cls("global")
        kind, separator, remainder = text.partition(":")
        if not separator or kind not in _RESOURCE_KINDS:
            raise PolicyInputError(f"unknown resource: {text}")
        if kind == "path":
            project_id, path_separator, path = remainder.partition("/")
            if not path_separator:
                raise PolicyInputError("path resource must include a project and path")
            return cls("path", project_id=project_id, path=path)
        return cls(cast(ResourceKind, kind), identifier=remainder)

    @classmethod
    def for_path(cls, project_id: str, path: str) -> Resource:
        return cls("path", project_id=project_id, path=path)

    @classmethod
    def from_value(cls, value: object) -> Resource:
        if isinstance(value, Resource):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        raise PolicyInputError("resource must be a string or Resource")


@dataclass(frozen=True)
class PolicyContext:
    """Deterministic context included in the decision audit record."""

    source: RequestSource = "user"
    task_id: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.source not in _REQUEST_SOURCES:
            raise PolicyInputError(f"unknown request source: {self.source}")
        if self.task_id is not None:
            task_id = _text(self.task_id, "context.task_id")
            _no_wildcard(task_id, "context.task_id")
        seen: set[str] = set()
        for key, value in self.attributes:
            key_text = _text(key, "context.attribute key")
            value_text = _text(value, f"context.attribute {key_text}")
            _no_wildcard(key_text, "context.attribute key")
            _no_wildcard(value_text, f"context.attribute {key_text}")
            if key_text in seen:
                raise PolicyInputError(f"duplicate context attribute: {key_text}")
            seen.add(key_text)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "task_id": self.task_id,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_mapping(cls, value: object) -> PolicyContext:
        data = _mapping(value, "context")
        allowed = {"source", "task_id", "attributes"}
        if not set(data) <= allowed:
            raise PolicyInputError(f"context contains unknown keys: {sorted(set(data) - allowed)}")
        source = _text(data.get("source", "user"), "context.source")
        if source not in _REQUEST_SOURCES:
            raise PolicyInputError(f"unknown request source: {source}")
        task_id: str | None = None
        raw_task_id = data.get("task_id")
        if raw_task_id is not None:
            task_id = _text(raw_task_id, "context.task_id")
        raw_attributes = data.get("attributes", {})
        attribute_map = _mapping(raw_attributes, "context.attributes")
        attributes = tuple(
            sorted(
                (key, _text(item, f"context.attribute {key}"))
                for key, item in attribute_map.items()
            )
        )
        return cls(
            source=cast(RequestSource, source),
            task_id=task_id,
            attributes=attributes,
        )


@dataclass(frozen=True)
class PolicyRequest:
    """A typed request; malformed values are rejected before evaluation."""

    principal: Principal
    action: Action
    resource: Resource
    context: PolicyContext = field(default_factory=PolicyContext)

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal):
            raise PolicyInputError("principal must be a Principal")
        if not isinstance(self.action, Action):
            raise PolicyInputError("action must be an Action")
        if not isinstance(self.resource, Resource):
            raise PolicyInputError("resource must be a Resource")
        if not isinstance(self.context, PolicyContext):
            raise PolicyInputError("context must be a PolicyContext")

    def canonical_dict(self) -> dict[str, object]:
        return {
            "principal": self.principal.canonical,
            "action": self.action.name,
            "resource": self.resource.canonical,
            "context": self.context.as_dict(),
        }

    @property
    def request_hash(self) -> str:
        return _sha256(self.canonical_dict())

    @classmethod
    def from_mapping(cls, value: object) -> PolicyRequest:
        data = _mapping(value, "request")
        required = {"principal", "action", "resource"}
        allowed = required | {"context"}
        if not required <= set(data) or not set(data) <= allowed:
            raise PolicyInputError(
                f"request must contain {sorted(required)} and only optional context"
            )
        raw_context = data.get("context", {})
        context = (
            raw_context
            if isinstance(raw_context, PolicyContext)
            else PolicyContext.from_mapping(raw_context)
        )
        return cls(
            principal=Principal.from_value(data["principal"]),
            action=Action.from_value(data["action"]),
            resource=Resource.from_value(data["resource"]),
            context=context,
        )


@dataclass(frozen=True)
class PolicyRule:
    """An exact-match rule. Explicit deny always takes precedence."""

    id: str
    principal: Principal
    action: Action
    resource: Resource
    effect: Effect

    @classmethod
    def from_dict(cls, value: object, index: int) -> PolicyRule:
        data = _mapping(value, f"rule[{index}]")
        if set(data) != _RULE_KEYS:
            raise PolicyError(f"rule[{index}] must contain exactly {sorted(_RULE_KEYS)}")
        rule_id = _text(data["id"], f"rule[{index}].id")
        _no_wildcard(rule_id, f"rule[{index}].id")
        if not _RULE_ID_RE.fullmatch(rule_id):
            raise PolicyError(f"rule[{index}].id is invalid")
        effect_text = _text(data["effect"], f"rule[{index}].effect")
        if effect_text not in {"allow", "deny"}:
            raise PolicyError(f"rule[{index}].effect must be allow or deny")
        try:
            principal = Principal.from_value(data["principal"])
            action = Action.from_value(data["action"])
            resource = Resource.from_value(data["resource"])
        except PolicyInputError as exc:
            raise PolicyError(f"rule[{index}] is invalid: {exc}") from exc
        return cls(
            id=rule_id,
            principal=principal,
            action=action,
            resource=resource,
            effect=cast(Effect, effect_text),
        )

    def matches(self, request: PolicyRequest) -> bool:
        return (
            self.principal == request.principal
            and self.action == request.action
            and self.resource == request.resource
        )


@dataclass(frozen=True)
class PolicyAuditRecord:
    """JSON-safe decision record consumed by the audited policy service."""

    decision_id: str
    effect: Effect
    reason: Reason
    rule_id: str
    policy_version: str
    policy_hash: str
    inputs_hash: str
    principal: str
    action: str
    resource: str
    context: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "effect": self.effect,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "inputs_hash": self.inputs_hash,
            "principal": self.principal,
            "action": self.action,
            "resource": self.resource,
            "context": dict(self.context),
        }


class DecisionSink(Protocol):
    """Contract for an audited decision sink."""

    def record(self, record: PolicyAuditRecord) -> None: ...


def record_decision(decision: PolicyDecision, sink: DecisionSink) -> None:
    """Hand a deterministic decision to an audit sink without changing it."""
    sink.record(decision.to_record())


@dataclass(frozen=True)
class PolicyDecision:
    """Typed output of one deterministic evaluation."""

    decision_id: str
    effect: Effect
    reason: Reason
    rule_id: str
    policy_version: str
    policy_hash: str
    inputs_hash: str
    principal: Principal
    action: Action
    resource: Resource
    context: PolicyContext

    def to_record(self) -> PolicyAuditRecord:
        return PolicyAuditRecord(
            decision_id=self.decision_id,
            effect=self.effect,
            reason=self.reason,
            rule_id=self.rule_id,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
            inputs_hash=self.inputs_hash,
            principal=self.principal.canonical,
            action=self.action.name,
            resource=self.resource.canonical,
            context=self.context.as_dict(),
        )

    def as_dict(self) -> dict[str, object]:
        return self.to_record().as_dict()


@dataclass(frozen=True)
class Policy:
    """A schema-validated deny-by-default policy."""

    version: str
    default: Literal["deny"]
    rules: tuple[PolicyRule, ...]
    document_hash: str

    @classmethod
    def from_dict(cls, value: object) -> Policy:
        """Build from a parsed document; ``load_policy`` adds JSON Schema validation."""
        data = _mapping(value, "policy")
        if set(data) != _POLICY_KEYS:
            raise PolicyError(f"policy must contain exactly {sorted(_POLICY_KEYS)}")
        version = _text(data["version"], "policy.version")
        default = _text(data["default"], "policy.default")
        if default != "deny":
            raise PolicyError("policy.default must be deny")
        raw_rules = data["rules"]
        if not isinstance(raw_rules, list):
            raise PolicyError("policy.rules must be an array")
        if len(raw_rules) > 1000:
            raise PolicyError("policy.rules must contain at most 1000 entries")
        rules = tuple(PolicyRule.from_dict(item, index) for index, item in enumerate(raw_rules))
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise PolicyError("policy rule ids must be unique")
        return cls(
            version=version,
            default="deny",
            rules=rules,
            document_hash=_sha256(data),
        )

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Return allow only for an exact match; deny rules always win."""
        if not isinstance(request, PolicyRequest):
            raise PolicyInputError("request must be a PolicyRequest")
        for rule in self.rules:
            if rule.effect == "deny" and rule.matches(request):
                return self._decision(request, "deny", "explicit_deny", rule.id)
        for rule in self.rules:
            if rule.effect == "allow" and rule.matches(request):
                return self._decision(request, "allow", "explicit_allow", rule.id)
        return self._decision(request, "deny", "default_deny", "default_deny")

    def _decision(
        self,
        request: PolicyRequest,
        effect: Effect,
        reason: Reason,
        rule_id: str,
    ) -> PolicyDecision:
        inputs_hash = _sha256(
            {
                "request": request.canonical_dict(),
                "policy_version": self.version,
                "policy_hash": self.document_hash,
            }
        )
        return PolicyDecision(
            decision_id=f"pol_{inputs_hash[:24]}",
            effect=effect,
            reason=reason,
            rule_id=rule_id,
            policy_version=self.version,
            policy_hash=self.document_hash,
            inputs_hash=inputs_hash,
            principal=request.principal,
            action=request.action,
            resource=request.resource,
            context=request.context,
        )


class PolicyEngine:
    """Stable facade over one loaded policy; pure and model-independent."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def policy_version(self) -> str:
        return self._policy.version

    @property
    def policy_hash(self) -> str:
        return self._policy.document_hash

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return self._policy.evaluate(request)

    def evaluate_payload(self, payload: object) -> PolicyDecision:
        """Validate an untrusted mapping, then evaluate it; never defaults to allow."""
        return self.evaluate(PolicyRequest.from_mapping(payload))


@lru_cache(maxsize=1)
def _policy_schema() -> dict[str, object]:
    try:
        raw = json.loads(_schema_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read policy schema: {exc}") from exc
    schema = _mapping(raw, "policy schema")
    return schema


def _schema_text() -> str:
    packaged = files("eis").joinpath("_resources", "schemas", "policy.schema.json")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "schemas" / "policy.schema.json"
    return source.read_text(encoding="utf-8")


def _validate_policy_document(document: object) -> None:
    try:
        jsonschema.validate(instance=document, schema=_policy_schema())
    except jsonschema.ValidationError as exc:
        raise PolicyError(f"policy schema validation failed: {exc.message}") from exc
    except jsonschema.SchemaError as exc:
        raise PolicyError("bundled policy schema is invalid") from exc


def load_policy(path: Path) -> Policy:
    """Load, schema-validate and type-check a policy file; fail closed on error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    _validate_policy_document(raw)
    return Policy.from_dict(raw)


def _default_policy_text() -> str:
    packaged = files("eis").joinpath("_resources", "policies", "default.json")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    source = Path(__file__).resolve().parents[2] / "policies" / "default.json"
    return source.read_text(encoding="utf-8")


def load_default_policy() -> Policy:
    """Load the bundled default policy."""
    try:
        raw = json.loads(_default_policy_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read default policy: {exc}") from exc
    _validate_policy_document(raw)
    return Policy.from_dict(raw)


def load_engine(path: Path) -> PolicyEngine:
    return PolicyEngine(load_policy(path))
