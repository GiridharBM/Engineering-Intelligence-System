# SPDX-License-Identifier: Apache-2.0

"""Strict JSON envelopes for untrusted model responses in the bounded loop.

The gateway stays text-only. A run may only continue when the response is
exactly one of two objects: a final answer or a call to one registered
read-only tool, or one proposed project write that must pass human approval.
Identity, root, policy, session, task and approval fields are rejected here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

MAX_ENVELOPE_BYTES = 8_192

# Never accepted from model output; the runtime derives all of them.
FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "project_id",
        "principal",
        "root",
        "root_path",
        "policy",
        "policy_file",
        "policy_version",
        "session_id",
        "task_id",
        "capability",
        "approval_id",
    }
)


class ModelEnvelopeError(ValueError):
    """Raised with a stable, content-free code; the run stops closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AnswerEnvelope:
    kind: Literal["answer"]
    text: str


@dataclass(frozen=True)
class ToolCallEnvelope:
    kind: Literal["tool_call"]
    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class WriteProposalEnvelope:
    kind: Literal["write_proposal"]
    path: str
    content: str
    summary: str


ModelEnvelope = AnswerEnvelope | ToolCallEnvelope | WriteProposalEnvelope


def parse_model_response(text: str) -> ModelEnvelope:
    """Parse one untrusted response; malformed output always fails the run."""
    if not isinstance(text, str) or not text.strip():
        raise ModelEnvelopeError("malformed_response")
    if len(text.encode("utf-8")) > MAX_ENVELOPE_BYTES:
        raise ModelEnvelopeError("response_too_large")
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, ValueError):
        raise ModelEnvelopeError("malformed_response") from None
    if not isinstance(value, dict):
        raise ModelEnvelopeError("malformed_response")

    kind = value.get("kind")
    if kind == "answer":
        if set(value) != {"kind", "content"}:
            raise ModelEnvelopeError("unexpected_field")
        content = value["content"]
        if not isinstance(content, str) or not content.strip():
            raise ModelEnvelopeError("invalid_answer")
        return AnswerEnvelope(kind="answer", text=content)
    if kind == "tool_call":
        if set(value) != {"kind", "tool", "arguments"}:
            raise ModelEnvelopeError("unexpected_field")
        tool = value["tool"]
        arguments = value["arguments"]
        if not isinstance(tool, str) or not tool:
            raise ModelEnvelopeError("malformed_response")
        if not isinstance(arguments, dict):
            raise ModelEnvelopeError("malformed_response")
        if FORBIDDEN_ARGUMENT_KEYS & set(arguments):
            raise ModelEnvelopeError("unexpected_field")
        return ToolCallEnvelope(kind="tool_call", tool=tool, arguments=dict(arguments))
    if kind == "write_proposal":
        if set(value) != {"kind", "path", "content", "summary"}:
            raise ModelEnvelopeError("unexpected_field")
        path = value["path"]
        content = value["content"]
        summary = value["summary"]
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 1024
            or "\x00" in path
            or path.startswith(("/", "\\"))
            or ":" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or "\\" in path
        ):
            raise ModelEnvelopeError("invalid_write_path")
        if not isinstance(content, str) or "\x00" in content:
            raise ModelEnvelopeError("invalid_write_content")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
            raise ModelEnvelopeError("invalid_write_summary")
        return WriteProposalEnvelope(
            kind="write_proposal", path=path, content=content, summary=summary.strip()
        )
    if kind is None:
        raise ModelEnvelopeError("malformed_response")
    raise ModelEnvelopeError("unknown_kind")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
