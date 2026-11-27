# SPDX-License-Identifier: Apache-2.0

"""Strict plan envelope for untrusted model output.

A plan is accepted only when it is one JSON object with an exact key set, at
most ``MAX_PLAN_STEPS`` ordered steps, registered read-only tools (or an
explicit human-review marker) and a dependency graph with no missing or cyclic
edges. Every rejected value yields a fixed, content-free code. Nothing here
executes anything or grants authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from eis.core.model_envelope import (
    FORBIDDEN_ARGUMENT_KEYS,
    WriteProposalEnvelope,
    parse_model_response,
    unique_object,
)
from eis.core.project_tools import (
    REGISTERED_TOOL_NAMES,
    ToolRequestError,
    parse_tool_call,
)

MAX_PLAN_BYTES = 24_576
MAX_PLAN_STEPS = 6
MAX_STEP_ID_CHARS = 24
STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
HUMAN_REVIEW_TOOL = "human_review"
WRITE_PROPOSAL_TOOL = "project.propose_write"
_VALIDATION_PROJECT = "plan-validation"

# Words that would let a plan smuggle authority or an out-of-scope action.
# Matching is on token boundaries in keys and string values, so an ordinary
# file name such as "commands.md" is not rejected.
FORBIDDEN_TOKENS = frozenset(
    {
        "identity",
        "principal",
        "root",
        "policy",
        "command",
        "shell",
        "exec",
        "execute",
        "write",
        "delete",
        "remove",
        "network",
        "http",
        "https",
        "curl",
        "wget",
        "approval",
        "approve",
        "grant",
        "capability",
        "secret",
        "credential",
        "token",
        "env",
        "sudo",
        "subprocess",
        "delegate",
        "spawn",
    }
)
_TOKEN_RES = tuple(
    re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])")
    for token in sorted(FORBIDDEN_TOKENS)
)


class ModelPlanError(ValueError):
    """Raised with a stable, content-free code; the plan is never persisted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PlanStep:
    """One validated step; arguments are untrusted even after validation."""

    id: str
    tool: str
    arguments: dict[str, object]
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class PlanEnvelope:
    kind: Literal["plan"]
    steps: tuple[PlanStep, ...]
    plan_hash: str
    canonical_text: str


def parse_plan_response(text: str) -> PlanEnvelope:
    """Validate one untrusted plan response; malformed output stops the run."""
    if not isinstance(text, str) or not text.strip():
        raise ModelPlanError("malformed_plan")
    if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
        raise ModelPlanError("plan_too_large")
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError):
        raise ModelPlanError("malformed_plan") from None
    if not isinstance(value, dict) or set(value) != {"kind", "steps"} or value["kind"] != "plan":
        raise ModelPlanError("unexpected_field")
    raw_steps = value["steps"]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_PLAN_STEPS:
        raise ModelPlanError("limit_exceeded")

    steps: list[PlanStep] = []
    seen: set[str] = set()
    for item in raw_steps:
        step = _parse_step(item)
        if step.id in seen:
            raise ModelPlanError("duplicate_step")
        seen.add(step.id)
        steps.append(step)

    for step in steps:
        if any(dependency not in seen for dependency in step.depends_on):
            raise ModelPlanError("missing_dependency")
    _require_acyclic(steps)
    write_steps = [index for index, step in enumerate(steps) if step.tool == WRITE_PROPOSAL_TOOL]
    if len(write_steps) > 1 or (write_steps and write_steps[0] != len(steps) - 1):
        raise ModelPlanError("write_proposal_must_be_final")

    canonical = _canonical({"kind": "plan", "steps": [_step_dict(step) for step in steps]})
    return PlanEnvelope(
        kind="plan",
        steps=tuple(steps),
        plan_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        canonical_text=canonical,
    )


def _parse_step(item: object) -> PlanStep:
    if not isinstance(item, dict) or set(item) != {"id", "tool", "arguments", "depends_on"}:
        raise ModelPlanError("invalid_step")
    step_id = item["id"]
    if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
        raise ModelPlanError("invalid_step")
    tool = item["tool"]
    if not isinstance(tool, str) or tool not in (
        REGISTERED_TOOL_NAMES | {HUMAN_REVIEW_TOOL, WRITE_PROPOSAL_TOOL}
    ):
        raise ModelPlanError("invalid_tool")
    arguments = item["arguments"]
    if not isinstance(arguments, dict):
        raise ModelPlanError("invalid_arguments")
    raw_dependencies = item["depends_on"]
    if (
        not isinstance(raw_dependencies, list)
        or len(raw_dependencies) > MAX_PLAN_STEPS
        or any(
            not isinstance(value, str) or not STEP_ID_RE.fullmatch(value)
            for value in raw_dependencies
        )
        or len(set(raw_dependencies)) != len(raw_dependencies)
    ):
        raise ModelPlanError("invalid_dependency")
    if FORBIDDEN_ARGUMENT_KEYS & set(arguments):
        raise ModelPlanError("forbidden_instruction")
    if tool != WRITE_PROPOSAL_TOOL and (
        _has_forbidden_instruction(arguments) or _has_forbidden_instruction(step_id)
    ):
        raise ModelPlanError("forbidden_instruction")

    if tool == HUMAN_REVIEW_TOOL:
        if arguments:
            raise ModelPlanError("invalid_arguments")
        cleaned: dict[str, object] = {}
    elif tool == WRITE_PROPOSAL_TOOL:
        try:
            envelope = parse_model_response(_canonical({"kind": "write_proposal", **arguments}))
        except ValueError:
            raise ModelPlanError("invalid_write_proposal") from None
        if not isinstance(envelope, WriteProposalEnvelope):
            raise ModelPlanError("invalid_write_proposal")
        cleaned = {
            "path": envelope.path,
            "content": envelope.content,
            "summary": envelope.summary,
        }
    else:
        probe = dict(arguments)
        if tool != "project.list":
            # The project is injected at execution from durable state, never from the plan.
            probe["project_id"] = _VALIDATION_PROJECT
        try:
            call = parse_tool_call({"tool": tool, "arguments": probe})
        except (ToolRequestError, ValueError, TypeError):
            raise ModelPlanError("invalid_arguments") from None
        cleaned = call.arguments_dict()
        cleaned.pop("project_id", None)
    return PlanStep(
        id=step_id,
        tool=tool,
        arguments=cleaned,
        depends_on=tuple(raw_dependencies),
    )


def _require_acyclic(steps: list[PlanStep]) -> None:
    graph = {step.id: step.depends_on for step in steps}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise ModelPlanError("dependency_cycle")
        if step_id in done:
            return
        visiting.add(step_id)
        for dependency in graph[step_id]:
            visit(dependency)
        visiting.remove(step_id)
        done.add(step_id)

    for step in steps:
        visit(step.id)


def _has_forbidden_instruction(value: object) -> bool:
    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            lowered = item.casefold()
            if any(pattern.search(lowered) for pattern in _TOKEN_RES):
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return False


def _step_dict(step: PlanStep) -> dict[str, object]:
    return {
        "id": step.id,
        "tool": step.tool,
        "arguments": step.arguments,
        "depends_on": list(step.depends_on),
    }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
