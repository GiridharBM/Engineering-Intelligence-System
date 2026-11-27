# SPDX-License-Identifier: Apache-2.0

"""Minimal durable session/task runtime with a bounded model-to-tool loop.

This slice persists lifecycle state and runs a strictly typed loop: each
completion must be an exact JSON envelope holding either a final answer or one
registered read-only tool call. Model output is untrusted text; session, task,
principal and project identity are always derived from durable state, tool
requests go through the existing registry and policy gate, and prompts,
responses, file contents and tool results never leave memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from eis import __version__
from eis.adapters.models.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
)
from eis.core.audit_chain import AuditIntegrityError
from eis.core.model_envelope import (
    AnswerEnvelope,
    ModelEnvelopeError,
    ToolCallEnvelope,
    WriteProposalEnvelope,
    parse_model_response,
)
from eis.core.model_gateway import ModelGateway
from eis.core.model_plan import (
    WRITE_PROPOSAL_TOOL,
    ModelPlanError,
    PlanStep,
    parse_plan_response,
)
from eis.core.policy_service import PolicyService
from eis.core.project_tools import (
    ReadOnlyProjectTools,
    ToolCall,
    ToolOutcome,
    ToolRequestError,
    parse_tool_call,
)
from eis.core.redaction import redact_text
from eis.policy import Action, PolicyContext, PolicyRequest, Principal, Resource

SessionStatus = Literal["active", "idle", "closed"]
TaskState = Literal[
    "created",
    "running",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]
PlanState = Literal["created", "running", "blocked", "completed", "failed", "cancelled"]
PlanStepStatus = Literal["pending", "running", "completed", "failed", "blocked", "skipped"]

MAX_TITLE_CHARS = 200
MAX_GOAL_CHARS = 20_000
DEFAULT_PRINCIPAL = "agent:eis.primary"
DEFAULT_MAX_TOKENS = 512
RUNTIME_VERSION = __version__

# Bounded loop limits; a run stops as soon as one is reached.
MAX_COMPLETIONS_PER_RUN = 3
MAX_TOOL_CALLS_PER_RUN = 2
RUN_WALL_TIME_SECONDS = 60.0
MAX_RUN_INPUT_CHARS = 32_768
MAX_TOOL_FEEDBACK_CHARS = 4_000

# Codes parse_tool_call may raise, used as content-free stop reasons.
_TOOL_REQUEST_CODES = frozenset(
    {"invalid_tool_call", "unknown_tool", "secret_path_denied", "invalid_relative_path"}
)

# Content-free contract sent with every run so a provider can answer in envelope form.
SYSTEM_PROMPT = (
    "Reply with exactly one JSON object and nothing else. "
    'Either {"kind":"answer","content":"<final answer>"} or '
    '{"kind":"tool_call","tool":"<tool>","arguments":{...}} or '
    '{"kind":"write_proposal","path":"<relative POSIX path>",'
    '"content":"<complete replacement>","summary":"<short explanation>"}. '
    "A write proposal only requests human approval; it never executes by itself. "
    'Read-only tools: project.list {}, project.list_files {"path","limit"}, '
    'project.read_text {"path","max_bytes"}, git.status {}. '
    "The project is implicit; never send project_id, session_id, task_id, "
    "principal, root or policy fields."
)
PLAN_SYSTEM_PROMPT = (
    "Reply with exactly one JSON object and nothing else: "
    '{"kind":"plan","steps":[{"id":"s1","tool":"<tool>","arguments":{},'
    '"depends_on":[]}]}. At most 6 steps. Tools: project.list, '
    "project.list_files, project.read_text, git.status, human_review, and "
    "project.propose_write as the final step only. That step's arguments are "
    "exactly path, content, summary and only requests human approval. "
    "The project is implicit; never send identity, root, policy, command, "
    "delete, network or approval fields."
)


class RuntimeStateError(RuntimeError):
    """Raised when a session/task transition or run is invalid."""


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    title: str
    status: SessionStatus
    project_id: str | None
    created_at_ms: int
    updated_at_ms: int
    closed_at_ms: int | None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    session_id: str
    state: TaskState
    principal: str
    goal: str
    goal_hash: str
    policy_decision_id: str | None
    policy_version: str
    runtime_version: str
    stop_reason: str | None
    request_hash: str | None
    response_hash: str | None
    finish_reason: str | None
    usage: Mapping[str, int]
    created_at_ms: int
    started_at_ms: int | None
    ended_at_ms: int | None


@dataclass(frozen=True)
class TaskOutcome:
    task: TaskRecord
    proposal_text: str | None = None
    approval_id: str | None = None
    approval_digest: str | None = None


@dataclass(frozen=True)
class WriteApprovalRequest:
    approval_id: str
    request_digest: str


class WriteProposalSubmitter(Protocol):
    def propose_write(
        self,
        *,
        project_id: str,
        task_id: str,
        relative_path: str,
        new_content: str,
        on_behalf_of: str,
        summary: str,
    ) -> WriteApprovalRequest: ...


@dataclass(frozen=True)
class ToolTaskOutcome:
    task: TaskRecord
    tool: ToolOutcome


@dataclass(frozen=True)
class PlanRecord:
    plan_id: str
    task_id: str
    project_id: str | None
    state: PlanState
    policy_version: str
    policy_hash: str
    plan_hash: str
    stop_reason: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class PlanStepRecord:
    step_id: str
    plan_id: str
    step_index: int
    step_ref: str
    tool: str
    arguments: Mapping[str, object] | None
    depends_on: tuple[str, ...]
    status: PlanStepStatus
    attempt_count: int
    policy_decision_id: str | None
    output_sha256: str | None
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True)
class PlanOutcome:
    task: TaskRecord
    plan: PlanRecord | None = None
    steps: tuple[PlanStepRecord, ...] = ()
    proposal_text: str | None = None
    approval_id: str | None = None
    approval_digest: str | None = None


class SessionTaskRepository(Protocol):
    def create_session(
        self, *, title: str, project_id: str | None, actor: str
    ) -> SessionRecord: ...

    def get_session(self, session_id: str) -> SessionRecord | None: ...

    def list_sessions(self, *, limit: int = 100) -> tuple[SessionRecord, ...]: ...

    def resume_session(self, session_id: str, *, actor: str) -> SessionRecord: ...

    def close_session(self, session_id: str, *, actor: str) -> SessionRecord: ...

    def create_task(
        self,
        *,
        session_id: str,
        goal: str,
        goal_hash: str,
        principal: str,
        policy_version: str,
        runtime_version: str,
        actor: str,
    ) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(self, session_id: str, *, limit: int = 100) -> tuple[TaskRecord, ...]: ...

    def transition_task(
        self,
        task_id: str,
        *,
        expected_state: TaskState,
        new_state: TaskState,
        stop_reason: str | None = None,
        policy_decision_id: str | None = None,
        policy_version: str | None = None,
        request_hash: str | None = None,
        response_hash: str | None = None,
        finish_reason: str | None = None,
        usage: Mapping[str, int] | None = None,
        session_status: SessionStatus | None = None,
        actor: str = "runtime",
    ) -> TaskRecord: ...

    def cancel_task(self, task_id: str, *, actor: str = "user") -> TaskRecord: ...

    def continue_human_review(
        self, task_id: str, step_ref: str, *, actor: str = "user"
    ) -> TaskRecord: ...

    def block_task_for_approval(
        self,
        task_id: str,
        approval_id: str,
        *,
        request_hash: str | None = None,
        response_hash: str | None = None,
        usage: Mapping[str, int] | None = None,
        actor: str = "runtime",
    ) -> TaskRecord: ...

    def block_plan_task_for_approval(
        self,
        task_id: str,
        plan_id: str,
        step_id: str,
        approval_id: str,
        *,
        request_hash: str | None = None,
        response_hash: str | None = None,
        usage: Mapping[str, int] | None = None,
        actor: str = "runtime",
    ) -> tuple[TaskRecord, PlanRecord, tuple[PlanStepRecord, ...]]: ...


class PlanRepository(Protocol):
    def create_plan(
        self,
        *,
        task_id: str,
        project_id: str | None,
        policy_version: str,
        policy_hash: str,
        plan_hash: str,
        steps: Sequence[PlanStep],
        actor: str,
    ) -> PlanRecord: ...

    def get_plan(self, plan_id: str) -> PlanRecord | None: ...

    def get_plan_for_task(self, task_id: str) -> PlanRecord | None: ...

    def list_steps(self, plan_id: str) -> tuple[PlanStepRecord, ...]: ...

    def transition_plan(
        self,
        plan_id: str,
        *,
        expected_state: PlanState,
        new_state: PlanState,
        stop_reason: str | None = None,
        actor: str = "runtime",
    ) -> PlanRecord: ...

    def transition_step(
        self,
        step_id: str,
        *,
        expected_status: PlanStepStatus,
        new_status: PlanStepStatus,
        policy_decision_id: str | None = None,
        output_sha256: str | None = None,
        error_code: str | None = None,
        actor: str = "runtime",
    ) -> PlanStepRecord: ...

    def interrupt_plan(self, plan_id: str, *, actor: str = "runtime") -> PlanRecord: ...

    def recover_interrupted_steps(
        self, plan_id: str, *, actor: str = "runtime"
    ) -> tuple[PlanStepRecord, ...]: ...


class SessionTaskRuntime:
    """Runs one bounded, strictly typed model-to-tool loop per task."""

    def __init__(
        self,
        repository: SessionTaskRepository,
        policy: PolicyService,
        gateway: ModelGateway | None,
        *,
        principal: str = DEFAULT_PRINCIPAL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        project_tools: ReadOnlyProjectTools | None = None,
        plans: PlanRepository | None = None,
        write_proposals: WriteProposalSubmitter | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._gateway = gateway
        self._project_tools = project_tools
        self._plans = plans
        self._write_proposals = write_proposals
        self._principal = _agent_principal(principal).canonical
        if not 1 <= max_tokens <= 8192:
            raise ValueError("max_tokens must be between 1 and 8192")
        self._max_tokens = max_tokens

    @property
    def principal(self) -> str:
        return self._principal

    def create_session(
        self,
        *,
        title: str = "Untitled",
        project_id: str | None = None,
        actor: str = "user",
    ) -> SessionRecord:
        clean_title = title.strip()
        if not clean_title or len(clean_title) > MAX_TITLE_CHARS:
            raise RuntimeStateError("session title must contain 1 to 200 characters")
        if project_id is not None:
            Resource("project", identifier=project_id)
        return self._repository.create_session(
            title=clean_title,
            project_id=project_id,
            actor=actor,
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self._repository.get_session(session_id)

    def list_sessions(self, *, limit: int = 100) -> tuple[SessionRecord, ...]:
        return self._repository.list_sessions(limit=limit)

    def resume_session(self, session_id: str, *, actor: str = "user") -> SessionRecord:
        return self._repository.resume_session(session_id, actor=actor)

    def close_session(self, session_id: str, *, actor: str = "user") -> SessionRecord:
        return self._repository.close_session(session_id, actor=actor)

    def list_tasks(self, session_id: str, *, limit: int = 100) -> tuple[TaskRecord, ...]:
        return self._repository.list_tasks(session_id, limit=limit)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._repository.get_task(task_id)

    def create_task(
        self,
        session_id: str,
        goal: str,
        *,
        principal: str | None = None,
        actor: str = "runtime",
    ) -> TaskRecord:
        clean_goal = goal.strip()
        if not clean_goal or len(clean_goal) > MAX_GOAL_CHARS:
            raise RuntimeStateError("task goal must contain 1 to 20000 characters")
        if "\x00" in clean_goal:
            raise RuntimeStateError("task goal must not contain NUL")
        session = self._repository.get_session(session_id)
        if session is None:
            raise RuntimeStateError("session not found")
        if session.status == "closed":
            raise RuntimeStateError("session is closed")
        selected = _agent_principal(principal or self._principal)
        return self._repository.create_task(
            session_id=session_id,
            goal=clean_goal,
            goal_hash=_sha256(clean_goal),
            principal=selected.canonical,
            policy_version=self._policy.policy_version,
            runtime_version=RUNTIME_VERSION,
            actor=actor,
        )

    async def submit_task(
        self,
        session_id: str,
        goal: str,
        *,
        principal: str | None = None,
        actor: str = "runtime",
    ) -> TaskOutcome:
        task = self.create_task(session_id, goal, principal=principal, actor=actor)
        return await self.run_task(task.task_id)

    async def run_task(self, task_id: str) -> TaskOutcome:
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state == "running":
            raise RuntimeStateError("task is already running; use resume_task")
        if task.state != "created":
            return TaskOutcome(task)

        session = self._repository.get_session(task.session_id)
        if session is None:
            raise RuntimeStateError("session not found")
        if session.status == "closed":
            return TaskOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="created",
                    new_state="failed",
                    stop_reason="session_closed",
                )
            )

        actor = _agent_principal(task.principal)
        resource = (
            Resource("project", identifier=session.project_id)
            if session.project_id is not None
            else Resource("global")
        )
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=actor,
                action=Action("task.run"),
                resource=resource,
                context=PolicyContext(source="agent", task_id=task.task_id),
            )
        )
        if decision.effect != "allow":
            return TaskOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="created",
                    new_state="failed",
                    stop_reason="policy_denied",
                    policy_decision_id=decision.decision_id,
                    policy_version=decision.policy_version,
                    session_status="idle",
                )
            )

        running = self._repository.transition_task(
            task.task_id,
            expected_state="created",
            new_state="running",
            policy_decision_id=decision.decision_id,
            policy_version=decision.policy_version,
            session_status="active",
        )
        if self._gateway is None:
            return TaskOutcome(
                self._fail(running, "provider_unavailable", usage=_run_usage(0, 0, {}))
            )
        return await self._run_loop(self._gateway, running, session)

    async def _run_loop(
        self,
        gateway: ModelGateway,
        running: TaskRecord,
        session: SessionRecord,
    ) -> TaskOutcome:
        """Run at most three completions and two read-only tool calls."""
        actor = _agent_principal(running.principal)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(role="user", content=redact_text(running.goal)),
        ]
        deadline = time.monotonic() + RUN_WALL_TIME_SECONDS
        seen_calls: set[str] = set()
        completions = 0
        tool_calls = 0
        request_hash: str | None = None
        response_hash: str | None = None
        finish_reason: str | None = None
        token_usage: dict[str, int] = {}

        def stop(reason: str) -> TaskOutcome:
            return TaskOutcome(
                self._repository.transition_task(
                    running.task_id,
                    expected_state="running",
                    new_state="failed",
                    stop_reason=reason,
                    request_hash=request_hash,
                    response_hash=response_hash,
                    finish_reason=finish_reason,
                    usage=_run_usage(completions, tool_calls, token_usage),
                    session_status="idle",
                )
            )

        while True:
            if completions >= MAX_COMPLETIONS_PER_RUN:
                return stop("limit_exhausted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return stop("wall_time_exceeded")
            request = CompletionRequest(messages=tuple(messages), max_tokens=self._max_tokens)
            request_hash = request.request_hash
            try:
                response = await asyncio.wait_for(
                    gateway.complete(request, data_class="C1"), remaining
                )
            except TimeoutError:
                return stop("wall_time_exceeded")
            except PermissionError:
                return stop("consent_denied")
            except ProviderError:
                return stop("provider_error")
            except AuditIntegrityError:
                raise
            except Exception:
                return stop("runtime_error")

            completions += 1
            response_hash = completion_hash(response)
            finish_reason = response.finish_reason
            token_usage = response.usage.as_dict() if response.usage is not None else {}

            try:
                envelope = parse_model_response(response.text)
            except ModelEnvelopeError as exc:
                return stop(exc.code)

            if isinstance(envelope, AnswerEnvelope):
                return TaskOutcome(
                    self._repository.transition_task(
                        running.task_id,
                        expected_state="running",
                        new_state="blocked",
                        stop_reason="human_review_required",
                        request_hash=request_hash,
                        response_hash=response_hash,
                        finish_reason=finish_reason,
                        usage=_run_usage(completions, tool_calls, token_usage),
                        session_status="idle",
                    ),
                    proposal_text=redact_text(envelope.text),
                )

            if isinstance(envelope, WriteProposalEnvelope):
                if tool_calls >= MAX_TOOL_CALLS_PER_RUN:
                    return stop("limit_exhausted")
                if session.project_id is None:
                    return stop("project_root_unavailable")
                if self._write_proposals is None:
                    return stop("write_approval_unavailable")
                if (
                    redact_text(envelope.content) != envelope.content
                    or redact_text(envelope.summary) != envelope.summary
                ):
                    return stop("sensitive_write_proposal")
                try:
                    approval = self._write_proposals.propose_write(
                        project_id=session.project_id,
                        task_id=running.task_id,
                        relative_path=envelope.path,
                        new_content=envelope.content,
                        on_behalf_of=actor.canonical,
                        summary=envelope.summary,
                    )
                except AuditIntegrityError:
                    raise
                except Exception:
                    return stop("approval_request_failed")
                approved_task = self._repository.block_task_for_approval(
                    running.task_id,
                    approval.approval_id,
                    request_hash=request_hash,
                    response_hash=response_hash,
                    usage=_run_usage(completions, tool_calls + 1, token_usage),
                )
                return TaskOutcome(
                    approved_task,
                    approval_id=approval.approval_id,
                    approval_digest=approval.request_digest,
                )

            if tool_calls >= MAX_TOOL_CALLS_PER_RUN:
                return stop("limit_exhausted")
            if self._project_tools is None:
                return stop("project_tools_unavailable")
            project_id = session.project_id
            if project_id is None or not self._project_tools.supports_project(project_id):
                return stop("project_root_unavailable")
            # The project comes from durable session state, never from model output.
            if not isinstance(envelope, ToolCallEnvelope):
                return stop("invalid_model_envelope")
            arguments = dict(envelope.arguments)
            if envelope.tool != "project.list":
                arguments["project_id"] = project_id
            try:
                call = parse_tool_call({"tool": envelope.tool, "arguments": arguments})
            except ToolRequestError as exc:
                # ToolRequestError messages are fixed source-level codes; anything
                # else collapses to a generic code so no input reaches the audit.
                code = str(exc)
                return stop(code if code in _TOOL_REQUEST_CODES else "invalid_tool_call")

            call_key = _canonical({"tool": call.tool, "arguments": call.arguments_dict()})
            if call_key in seen_calls:
                return stop("repeated_tool_call")
            if sum(len(message.content) for message in messages) >= MAX_RUN_INPUT_CHARS:
                return stop("input_too_large")
            seen_calls.add(call_key)
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=redact_text(
                        _canonical(
                            {
                                "kind": "tool_call",
                                "tool": call.tool,
                                "arguments": call.arguments_dict(),
                            }
                        )
                    ),
                )
            )
            try:
                outcome = self._project_tools.invoke(
                    call=call,
                    task_id=running.task_id,
                    principal=actor,
                )
            except AuditIntegrityError:
                raise
            if outcome.status == "denied":
                return stop("policy_denied")
            if outcome.status == "failed":
                return stop(outcome.error_code or "tool_failed")
            tool_calls += 1
            available = MAX_RUN_INPUT_CHARS - sum(len(message.content) for message in messages)
            messages.append(
                ChatMessage(
                    role="user",
                    content=_tool_feedback(outcome.data, max(available, 0)),
                )
            )

    def invoke_tool(
        self,
        session_id: str,
        raw_call: object,
        *,
        actor: str = "cli",
    ) -> ToolTaskOutcome:
        """Dispatch one explicitly supplied, strictly typed read-only tool call.

        This path is deliberately separate from model completion. No response
        text is parsed or implicitly dispatched as an action.
        """
        if self._project_tools is None:
            raise RuntimeStateError("read-only project tools are unavailable")
        try:
            call = parse_tool_call(raw_call)
        except ToolRequestError:
            raise
        session = self._repository.get_session(session_id)
        if session is None:
            raise RuntimeStateError("session not found")
        if session.status == "closed":
            raise RuntimeStateError("session is closed")
        if call.tool != "project.list" and call.project_id != session.project_id:
            raise RuntimeStateError("tool project must match the session project")
        if session.project_id is None or not self._project_tools.supports_project(
            session.project_id
        ):
            raise RuntimeStateError("session project root is not registered")

        task = self.create_task(
            session_id,
            f"Read-only tool invocation: {call.tool}",
            principal=self._principal,
            actor=actor,
        )
        running = self._repository.transition_task(
            task.task_id,
            expected_state="created",
            new_state="running",
            session_status="active",
            actor=actor,
        )
        outcome = self._project_tools.invoke(
            call=call,
            task_id=running.task_id,
            principal=_agent_principal(self._principal),
        )
        if outcome.status == "success":
            state: TaskState = "completed"
            stop_reason = "tool_completed"
        else:
            state = "failed"
            if outcome.status == "denied":
                stop_reason = "policy_denied"
            else:
                stop_reason = outcome.error_code or "tool_failed"
        finished = self._repository.transition_task(
            running.task_id,
            expected_state="running",
            new_state=state,
            stop_reason=stop_reason,
            request_hash=_sha256(_canonical_tool_call(call)),
            response_hash=outcome.output_sha256,
            session_status="idle",
            actor=actor,
        )
        return ToolTaskOutcome(task=finished, tool=outcome)

    async def resume_task(self, task_id: str) -> TaskOutcome:
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state == "running":
            if self._plans is not None and self._plans.get_plan_for_task(task_id) is not None:
                resumed = await self.resume_plan(task_id)
                return TaskOutcome(resumed.task)
            return TaskOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="running",
                    new_state="failed",
                    stop_reason="interrupted",
                    session_status="idle",
                )
            )
        if task.state == "created":
            return await self.run_task(task.task_id)
        return TaskOutcome(task)

    async def run_plan(self, task_id: str) -> PlanOutcome:
        """Generate, persist and run one bounded plan of read-only steps."""
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state == "running":
            raise RuntimeStateError("task is already running; use resume_plan")
        if task.state != "created":
            return PlanOutcome(task)
        if self._plans is None:
            raise RuntimeStateError("plan storage is unavailable")

        session = self._repository.get_session(task.session_id)
        if session is None:
            raise RuntimeStateError("session not found")
        if session.status == "closed":
            return PlanOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="created",
                    new_state="failed",
                    stop_reason="session_closed",
                )
            )

        actor = _agent_principal(task.principal)
        resource = (
            Resource("project", identifier=session.project_id)
            if session.project_id is not None
            else Resource("global")
        )
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=actor,
                action=Action("task.run"),
                resource=resource,
                context=PolicyContext(source="agent", task_id=task.task_id),
            )
        )
        if decision.effect != "allow":
            return PlanOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="created",
                    new_state="failed",
                    stop_reason="policy_denied",
                    policy_decision_id=decision.decision_id,
                    policy_version=decision.policy_version,
                    session_status="idle",
                )
            )

        running = self._repository.transition_task(
            task.task_id,
            expected_state="created",
            new_state="running",
            policy_decision_id=decision.decision_id,
            policy_version=decision.policy_version,
            session_status="active",
        )
        if self._gateway is None:
            return PlanOutcome(
                self._fail(running, "provider_unavailable", usage=_run_usage(0, 0, {}))
            )

        request = CompletionRequest(
            messages=(
                ChatMessage(role="system", content=PLAN_SYSTEM_PROMPT),
                ChatMessage(role="user", content=redact_text(running.goal)),
            ),
            max_tokens=self._max_tokens,
        )
        request_hash = request.request_hash
        try:
            response = await asyncio.wait_for(
                self._gateway.complete(request, data_class="C1"), RUN_WALL_TIME_SECONDS
            )
        except TimeoutError:
            return PlanOutcome(self._fail(running, "wall_time_exceeded"))
        except PermissionError:
            return PlanOutcome(self._fail(running, "consent_denied"))
        except ProviderError:
            return PlanOutcome(self._fail(running, "provider_error"))
        except AuditIntegrityError:
            raise
        except Exception:
            return PlanOutcome(self._fail(running, "runtime_error"))

        response_hash = completion_hash(response)
        finish_reason = response.finish_reason
        token_usage = response.usage.as_dict() if response.usage is not None else {}
        try:
            envelope = parse_plan_response(response.text)
        except ModelPlanError as exc:
            return PlanOutcome(
                self._repository.transition_task(
                    running.task_id,
                    expected_state="running",
                    new_state="failed",
                    stop_reason=exc.code,
                    request_hash=request_hash,
                    response_hash=response_hash,
                    finish_reason=finish_reason,
                    usage=_run_usage(1, 0, token_usage),
                    session_status="idle",
                )
            )

        try:
            plan = self._plans.create_plan(
                task_id=running.task_id,
                project_id=session.project_id,
                policy_version=decision.policy_version,
                policy_hash=self._policy.policy_hash,
                plan_hash=envelope.plan_hash,
                steps=envelope.steps,
                actor=running.principal,
            )
        except AuditIntegrityError:
            raise
        except Exception:
            return PlanOutcome(self._fail(running, "plan_persistence_failed"))
        outcome = await self._run_plan_steps(
            running,
            session,
            plan,
            envelope.steps,
            actor,
            token_usage,
            request_hash,
            response_hash,
        )
        return replace(outcome, proposal_text=redact_text(envelope.canonical_text))

    async def _run_plan_steps(
        self,
        task: TaskRecord,
        session: SessionRecord,
        created: PlanRecord,
        model_steps: Sequence[PlanStep],
        actor: Principal,
        token_usage: Mapping[str, int],
        request_hash: str,
        response_hash: str,
        *,
        existing_running: bool = False,
    ) -> PlanOutcome:
        assert self._plans is not None
        plan = (
            created
            if existing_running
            else self._plans.transition_plan(
                created.plan_id,
                expected_state="created",
                new_state="running",
                actor=task.principal,
            )
        )
        records = {item.step_ref: item for item in self._plans.list_steps(plan.plan_id)}
        completed: set[str] = {
            step_ref for step_ref, record in records.items() if record.status == "completed"
        }
        remaining = [step for step in model_steps if step.id not in completed]
        seen_calls: set[str] = set()
        tool_calls = 0

        def usage() -> dict[str, int]:
            return _run_usage(1, tool_calls, token_usage)

        def stop(reason: str) -> PlanOutcome:
            failed = self._plans.transition_plan(  # type: ignore[union-attr]
                plan.plan_id,
                expected_state=plan.state,
                new_state="failed",
                stop_reason=reason,
                actor=task.principal,
            )
            return PlanOutcome(
                self._repository.transition_task(
                    task.task_id,
                    expected_state="running",
                    new_state="failed",
                    stop_reason=reason,
                    request_hash=request_hash,
                    response_hash=response_hash,
                    usage=usage(),
                    session_status="idle",
                ),
                failed,
                tuple(self._plans.list_steps(plan.plan_id)),  # type: ignore[union-attr]
            )

        while remaining:
            progressed = False
            for step in list(remaining):
                if any(dependency not in completed for dependency in step.depends_on):
                    continue
                remaining.remove(step)
                progressed = True
                record = records[step.id]

                if step.tool == "human_review":
                    self._plans.transition_step(
                        record.step_id,
                        expected_status="pending",
                        new_status="blocked",
                        error_code="human_review_required",
                        actor=task.principal,
                    )
                    blocked = self._plans.transition_plan(
                        plan.plan_id,
                        expected_state="running",
                        new_state="blocked",
                        stop_reason="human_review_required",
                        actor=task.principal,
                    )
                    return PlanOutcome(
                        self._repository.transition_task(
                            task.task_id,
                            expected_state="running",
                            new_state="blocked",
                            stop_reason="human_review_required",
                            usage=usage(),
                            session_status="idle",
                        ),
                        blocked,
                        tuple(self._plans.list_steps(plan.plan_id)),
                    )

                if step.tool == WRITE_PROPOSAL_TOOL:
                    if tool_calls >= MAX_TOOL_CALLS_PER_RUN:
                        return stop("limit_exhausted")
                    if self._write_proposals is None or session.project_id is None:
                        return stop("write_approval_unavailable")
                    if redact_text(str(step.arguments.get("content", ""))) != step.arguments.get(
                        "content"
                    ) or redact_text(str(step.arguments.get("summary", ""))) != step.arguments.get(
                        "summary"
                    ):
                        return stop("sensitive_write_proposal")
                    try:
                        approval = self._write_proposals.propose_write(
                            project_id=session.project_id,
                            task_id=task.task_id,
                            relative_path=str(step.arguments["path"]),
                            new_content=str(step.arguments["content"]),
                            on_behalf_of=actor.canonical,
                            summary=str(step.arguments["summary"]),
                        )
                    except AuditIntegrityError:
                        raise
                    except Exception:
                        self._plans.transition_step(
                            record.step_id,
                            expected_status="running",
                            new_status="failed",
                            error_code="approval_request_failed",
                            actor=task.principal,
                        )
                        return stop("approval_request_failed")
                    blocked_task, blocked_plan, blocked_steps = (
                        self._repository.block_plan_task_for_approval(
                            task.task_id,
                            plan.plan_id,
                            record.step_id,
                            approval.approval_id,
                            request_hash=request_hash,
                            response_hash=response_hash,
                            usage=usage() | {"tool_calls": tool_calls + 1},
                        )
                    )
                    return PlanOutcome(
                        blocked_task,
                        blocked_plan,
                        blocked_steps,
                        approval_id=approval.approval_id,
                        approval_digest=approval.request_digest,
                    )

                if self._project_tools is None:
                    return stop("project_tools_unavailable")
                project_id = session.project_id
                if project_id is None or not self._project_tools.supports_project(project_id):
                    return stop("project_root_unavailable")
                call_key = _canonical({"tool": step.tool, "arguments": step.arguments})
                if call_key in seen_calls:
                    return stop("repeated_tool_call")
                seen_calls.add(call_key)
                arguments = dict(step.arguments)
                if step.tool != "project.list":
                    # The project is injected from the durable session, not from model text.
                    arguments["project_id"] = project_id
                try:
                    call = parse_tool_call({"tool": step.tool, "arguments": arguments})
                except ToolRequestError:
                    return stop("invalid_tool_call")

                self._plans.transition_step(
                    record.step_id,
                    expected_status="pending",
                    new_status="running",
                    actor=task.principal,
                )
                try:
                    outcome = self._project_tools.invoke(
                        call=call, task_id=task.task_id, principal=actor
                    )
                except AuditIntegrityError:
                    raise
                if outcome.status != "success":
                    error_code = outcome.error_code or "tool_failed"
                    self._plans.transition_step(
                        record.step_id,
                        expected_status="running",
                        new_status="failed",
                        error_code=error_code,
                        actor=task.principal,
                    )
                    return stop("policy_denied" if outcome.status == "denied" else error_code)
                tool_calls += 1
                self._plans.transition_step(
                    record.step_id,
                    expected_status="running",
                    new_status="completed",
                    output_sha256=outcome.output_sha256,
                    policy_decision_id=outcome.policy_decision_id,
                    actor=task.principal,
                )
                completed.add(step.id)
            if not progressed:
                return stop("unsatisfied_dependencies")

        finished_plan = self._plans.transition_plan(
            plan.plan_id,
            expected_state="running",
            new_state="completed",
            actor=task.principal,
        )
        return PlanOutcome(
            self._repository.transition_task(
                task.task_id,
                expected_state="running",
                new_state="completed",
                stop_reason="plan_completed",
                request_hash=request_hash,
                response_hash=response_hash,
                usage=usage(),
                session_status="idle",
            ),
            finished_plan,
            tuple(self._plans.list_steps(plan.plan_id)),
        )

    async def resume_plan(self, task_id: str) -> PlanOutcome:
        """Explicitly resume a durable read-only plan after checking its bindings."""
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state != "running":
            return PlanOutcome(task)
        if self._plans is None:
            raise RuntimeStateError("plan storage is unavailable")
        plans = self._plans
        plan = plans.get_plan_for_task(task_id)
        if plan is None:
            return PlanOutcome((await self.resume_task(task_id)).task)
        if plan.state != "running":
            return PlanOutcome(task, plan, tuple(plans.list_steps(plan.plan_id)))

        def fail_plan(reason: str, steps: Sequence[PlanStepRecord] | None = None) -> PlanOutcome:
            failed_plan = plans.transition_plan(
                plan.plan_id,
                expected_state="running",
                new_state="failed",
                stop_reason=reason,
                actor=task.principal,
            )
            failed_task = self._fail(task, reason)
            return PlanOutcome(
                failed_task,
                failed_plan,
                tuple(steps or plans.list_steps(plan.plan_id)),
            )

        session = self._repository.get_session(task.session_id)
        if session is None or session.status == "closed":
            return fail_plan("session_closed")
        if (
            plan.policy_hash != self._policy.policy_hash
            or plan.policy_version != self._policy.policy_version
        ):
            return fail_plan("policy_changed")
        if self._project_tools is None:
            return fail_plan("project_tools_unavailable")
        if session.project_id is None or not self._project_tools.supports_project(
            session.project_id
        ):
            return fail_plan("project_root_unavailable")
        actor = _agent_principal(task.principal)
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=actor,
                action=Action("task.run"),
                resource=Resource("project", identifier=session.project_id),
                context=PolicyContext(source="agent", task_id=task.task_id),
            )
        )
        if decision.effect != "allow":
            return fail_plan("policy_denied")
        try:
            steps = plans.recover_interrupted_steps(plan.plan_id, actor=task.principal)
        except RuntimeStateError:
            return fail_plan("resume_limit_exceeded")
        if any(step.status != "completed" and step.arguments is None for step in steps):
            return fail_plan("resume_arguments_unavailable", steps)
        model_steps = tuple(
            PlanStep(
                id=step.step_ref,
                tool=step.tool,
                arguments=dict(step.arguments or {}),
                depends_on=step.depends_on,
            )
            for step in steps
        )
        return await self._run_plan_steps(
            task,
            session,
            plan,
            model_steps,
            actor,
            {},
            task.request_hash or plan.plan_hash,
            task.response_hash or plan.plan_hash,
            existing_running=True,
        )

    def cancel_task(self, task_id: str, *, actor: str = "runtime") -> PlanOutcome:
        """Cancel unexecuted work; successful execution evidence always wins."""
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state in {"completed", "failed", "cancelled"}:
            raise RuntimeStateError(f"task is already terminal: {task.state}")
        session = self._repository.get_session(task.session_id)
        if session is None:
            raise RuntimeStateError("session not found")
        resource = (
            Resource("project", identifier=session.project_id)
            if session.project_id is not None
            else Resource("global")
        )
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=_agent_principal(task.principal),
                action=Action("task.cancel"),
                resource=resource,
                context=PolicyContext(source="agent", task_id=task_id),
            )
        )
        if decision.effect != "allow":
            raise RuntimeStateError("policy_denied")
        cancelled = self._repository.cancel_task(task_id, actor=actor)
        plan = self._plans.get_plan_for_task(task_id) if self._plans is not None else None
        steps = (
            tuple(self._plans.list_steps(plan.plan_id))
            if self._plans is not None and plan is not None
            else ()
        )
        return PlanOutcome(cancelled, plan, steps)

    async def continue_plan_review(
        self, task_id: str, step_ref: str, *, actor: str = "user"
    ) -> PlanOutcome:
        """Continue a blocked human-review step; no permission or approval grant."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", step_ref):
            raise RuntimeStateError("invalid_review_step")
        if self._plans is None:
            raise RuntimeStateError("plan storage is unavailable")
        plans = self._plans
        task = self._repository.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found")
        if task.state != "blocked" or task.stop_reason != "human_review_required":
            raise RuntimeStateError("task is not waiting for human review")
        plan = plans.get_plan_for_task(task_id)
        if plan is None or plan.state != "blocked" or plan.stop_reason != "human_review_required":
            raise RuntimeStateError("plan is not waiting for human review")
        steps = plans.list_steps(plan.plan_id)
        reviewed = next(
            (
                step
                for step in steps
                if step.step_ref == step_ref
                and step.tool == "human_review"
                and step.status == "blocked"
                and step.error_code == "human_review_required"
            ),
            None,
        )
        if reviewed is None:
            raise RuntimeStateError("human_review_step_not_found")
        session = self._repository.get_session(task.session_id)
        if session is None or session.status == "closed":
            raise RuntimeStateError("session_closed")
        if session.project_id != plan.project_id:
            raise RuntimeStateError("project_binding_changed")
        if (
            plan.policy_version != self._policy.policy_version
            or plan.policy_hash != self._policy.policy_hash
        ):
            raise RuntimeStateError("policy_changed")
        if self._project_tools is None or session.project_id is None:
            raise RuntimeStateError("project_tools_unavailable")
        if not self._project_tools.supports_project(session.project_id):
            raise RuntimeStateError("project_root_unavailable")
        actor_principal = _agent_principal(task.principal)
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=actor_principal,
                action=Action("task.run"),
                resource=Resource("project", identifier=session.project_id),
                context=PolicyContext(source="agent", task_id=task_id),
            )
        )
        if decision.effect != "allow":
            raise RuntimeStateError("policy_denied")
        running = self._repository.continue_human_review(task_id, step_ref, actor=actor)
        refreshed = plans.get_plan_for_task(task_id)
        assert refreshed is not None
        model_steps = tuple(
            PlanStep(
                id=step.step_ref,
                tool=step.tool,
                arguments=dict(step.arguments or {}),
                depends_on=step.depends_on,
            )
            for step in plans.list_steps(refreshed.plan_id)
            if step.arguments is not None
        )
        return await self._run_plan_steps(
            running,
            session,
            refreshed,
            model_steps,
            actor_principal,
            {},
            task.request_hash or plan.plan_hash,
            task.response_hash or plan.plan_hash,
            existing_running=True,
        )

    def _fail(
        self,
        task: TaskRecord,
        reason: str,
        *,
        usage: Mapping[str, int] | None = None,
    ) -> TaskRecord:
        return self._repository.transition_task(
            task.task_id,
            expected_state="running",
            new_state="failed",
            stop_reason=reason,
            usage=usage,
            session_status="idle",
        )


def _agent_principal(value: str) -> Principal:
    try:
        principal = Principal.parse(value)
    except ValueError as exc:
        raise RuntimeStateError("invalid task principal") from exc
    if principal.kind != "agent":
        raise RuntimeStateError("task principal must be an agent")
    return principal


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_tool_call(call: ToolCall) -> str:
    return _canonical({"tool": call.tool, "arguments": call.arguments_dict()})


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _run_usage(completions: int, tool_calls: int, tokens: Mapping[str, int]) -> dict[str, int]:
    """Content-free run counters merged with the last token usage."""
    merged = dict(tokens)
    merged["completions"] = completions
    merged["tool_calls"] = tool_calls
    return merged


def _tool_feedback(data: object, available: int) -> str:
    """Bound one tool result before it re-enters the prompt; memory only."""
    text = redact_text(_canonical(data))
    if len(text) <= available and len(text) <= MAX_TOOL_FEEDBACK_CHARS:
        return text
    budget = min(available, MAX_TOOL_FEEDBACK_CHARS)
    if budget <= 0:
        return "[truncated]"
    return text[:budget] + "\n...[truncated]"


def completion_hash(response: CompletionResponse) -> str:
    """Hash untrusted model text without persisting it."""
    return _sha256(response.text)
