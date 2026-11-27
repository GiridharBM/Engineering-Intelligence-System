# SPDX-License-Identifier: Apache-2.0

"""Local HTTP API with public liveness/metadata and authenticated runtime routes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from eis import __version__
from eis.config import ConfigurationError, Settings, load_settings
from eis.core.api_auth import load_or_create_token, token_matches
from eis.core.approvals import ApprovalError, ApprovalRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.execution import ExecutionDenied, ExecutionGateway
from eis.core.policy_service import PolicyService
from eis.core.project_roots import ProjectRootError, root_status
from eis.core.project_tools import ReadOnlyProjectTools
from eis.core.runtime import RuntimeStateError, SessionTaskRuntime, TaskRecord
from eis.core.structured_logging import CorrelationLoggingMiddleware
from eis.core.task_approval import RegisteredProjectWriteProposer
from eis.policy import PolicyEngine, PolicyError, load_default_policy, load_policy
from eis.storage.approvals import ApprovalStore
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import default_database
from eis.storage.gateway import build_sqlite_gateway
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    name: str
    version: str


class MetaResponse(BaseModel):
    name: str
    version: str
    mode: Literal["single-user"]
    model_provider: str | None
    model_configured: bool
    model: str | None
    allow_cloud: bool
    project_roots: int
    policy_default: Literal["deny"]
    policy_version: str
    policy_hash: str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SessionCreate(StrictModel):
    title: str = Field(default="Untitled", min_length=1, max_length=200)
    project_id: str | None = Field(default=None, min_length=1, max_length=200)


class SessionResponse(BaseModel):
    session_id: str
    title: str
    status: str
    project_id: str | None
    created_at_ms: int
    updated_at_ms: int
    closed_at_ms: int | None


class TaskSubmit(StrictModel):
    goal: str = Field(min_length=1, max_length=20_000)


class ReviewContinuation(StrictModel):
    step_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{1,24}$")


class TaskResponse(BaseModel):
    task_id: str
    session_id: str
    state: str
    stop_reason: str | None
    request_hash: str | None
    response_hash: str | None
    usage: dict[str, int]
    approval_id: str | None = None
    approval_digest: str | None = None


class ApprovalSummary(BaseModel):
    approval_id: str
    task_id: str | None
    status: str
    resource: str
    summary: str
    expires_at_ms: int
    request_digest: str


class ApprovalDetail(ApprovalSummary):
    diff_text: str


class ApprovalDecision(StrictModel):
    decision: Literal["approve", "reject"]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str = Field(default="", max_length=500)


class ApprovalExecution(StrictModel):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApprovalExecutionResponse(BaseModel):
    approval_id: str
    status: str
    run_id: str
    exit_code: int | None
    backup_id: str | None
    task_id: str | None
    task_state: str | None


class AuditVerifyResponse(BaseModel):
    ok: bool
    event_count: int
    failed_seq: int | None
    failure: str | None


class AuditEventSummary(BaseModel):
    seq: int
    occurred_at_ms: int
    event_type: str
    actor: str
    subject_type: str
    subject_id: str
    decision: str | None
    prev_hash: str
    event_hash: str


class AuditEventPage(BaseModel):
    items: list[AuditEventSummary]
    next_cursor: str | None
    integrity: AuditVerifyResponse


@dataclass(frozen=True)
class _AuditCursor:
    after_seq: int
    head_seq: int
    head_hash: str

    def body(self) -> dict[str, object]:
        return {
            "after_seq": self.after_seq,
            "head_hash": self.head_hash,
            "head_seq": self.head_seq,
            "v": 1,
        }


_CURSOR_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _encode_cursor(cursor: _AuditCursor, key: str) -> str:
    raw = json.dumps(cursor.body(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}"


def _decode_cursor(value: str, key: str) -> _AuditCursor:
    if not _CURSOR_RE.fullmatch(value):
        raise ValueError("invalid cursor encoding")
    body, signature = value.split(".", 1)
    raw = _b64decode(body)
    expected = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(signature)):
        raise ValueError("cursor signature mismatch")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"after_seq", "head_seq", "head_hash", "v"}:
        raise ValueError("invalid cursor fields")
    if payload["v"] != 1:
        raise ValueError("unsupported cursor version")
    after, head = payload["after_seq"], payload["head_seq"]
    if type(after) is not int or type(head) is not int or after < 0 or head < 1 or after > head:
        raise ValueError("invalid cursor sequence")
    if not isinstance(payload["head_hash"], str) or not _HASH_RE.fullmatch(payload["head_hash"]):
        raise ValueError("invalid cursor head hash")
    return _AuditCursor(after, head, payload["head_hash"])


def _strict_audit_query(request: Request) -> None:
    names = [name for name, _ in request.query_params.multi_items()]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="duplicate_query_parameter")
    if not set(names) <= {"limit", "cursor"}:
        raise HTTPException(status_code=422, detail="unexpected_query_parameter")


RuntimeFactory = Callable[[], SessionTaskRuntime]
TokenProvider = Callable[[], str]


def _runtime(settings: Settings) -> SessionTaskRuntime:
    database = default_database(settings)
    roots = ProjectRootStore(
        database,
        reserved_paths=(settings.data_dir, settings.config_dir),
    )
    policy = PolicyService(PolicyEngine(load_default_policy()), SqlitePolicyDecisionSink(database))
    integrity = AuditChainStore(database)
    approvals = ApprovalStore(database)
    gateway = build_sqlite_gateway(settings) if settings.provider_config is not None else None
    return SessionTaskRuntime(
        SessionTaskStore(database),
        policy,
        gateway,
        project_tools=ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=integrity),
        plans=PlanStore(database),
        write_proposals=RegisteredProjectWriteProposer(
            roots=roots,
            policy=policy,
            approvals=approvals,
            integrity=integrity,
            backup_dir=settings.data_dir / "backups",
        ),
    )


def _session_response(record: object) -> SessionResponse:
    return SessionResponse.model_validate(record, from_attributes=True)


def _task_response(
    task: TaskRecord, *, approval_id: str | None = None, approval_digest: str | None = None
) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        session_id=task.session_id,
        state=task.state,
        stop_reason=task.stop_reason,
        request_hash=task.request_hash,
        response_hash=task.response_hash,
        usage=dict(task.usage),
        approval_id=approval_id,
        approval_digest=approval_digest,
    )


def create_app(
    *,
    runtime_factory: RuntimeFactory | None = None,
    token_provider: TokenProvider | None = None,
) -> FastAPI:
    """Create the API without binding a network listener."""
    app = FastAPI(
        title="EIS",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(
        CorrelationLoggingMiddleware,
        logger=logging.getLogger("eis.api"),
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]"],
    )
    settings_cache: Settings | None = None

    def settings() -> Settings:
        nonlocal settings_cache
        if settings_cache is None:
            settings_cache = load_settings()
        return settings_cache

    def expected_token() -> str:
        if token_provider is not None:
            return token_provider()
        return load_or_create_token(load_settings(require_provider=False).config_dir)

    def authorize(authorization: str | None = Header(default=None)) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token_matches(expected_token(), supplied):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication_required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def runtime() -> SessionTaskRuntime:
        if runtime_factory is not None:
            return runtime_factory()
        try:
            return _runtime(settings())
        except (ConfigurationError, PolicyError) as exc:
            raise HTTPException(status_code=503, detail="runtime_unavailable") from exc

    def execution_gateway(
        approval_id: str,
    ) -> tuple[ApprovalStore, SessionTaskStore, ExecutionGateway, ApprovalRecord]:
        configured = settings()
        database = default_database(configured)
        approvals = ApprovalStore(database)
        record = approvals.get(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail="approval_not_found")
        _, separator, rest = record.resource.partition(":")
        project_id, path_separator, _ = rest.partition("/")
        if not separator or not path_separator or not project_id:
            raise HTTPException(status_code=409, detail="approval_resource_invalid")
        roots = ProjectRootStore(
            database,
            reserved_paths=(configured.data_dir, configured.config_dir),
        )
        root = roots.get(project_id)
        if root is None or root_status(root) != "available":
            raise HTTPException(status_code=409, detail="registered_project_unavailable")
        policy_path = os.environ.get("EIS_POLICY_FILE")
        selected_policy = load_policy(Path(policy_path)) if policy_path else load_default_policy()
        policy = PolicyService(PolicyEngine(selected_policy), SqlitePolicyDecisionSink(database))
        gateway = ExecutionGateway(
            project_roots={project_id: Path(root.root_path)},
            trusted_projects=frozenset(),
            backup_dir=configured.data_dir / "backups",
            policy=policy,
            approvals=approvals,
            integrity=AuditChainStore(database),
        )
        return approvals, SessionTaskStore(database), gateway, record

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(name="EIS", version=__version__)

    @app.get("/api/v1/meta", response_model=MetaResponse, tags=["system"])
    def meta() -> MetaResponse:
        try:
            configured = settings()
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail="configuration_unavailable") from exc
        policy = load_default_policy()
        roots = ProjectRootStore(
            default_database(configured),
            reserved_paths=(configured.data_dir, configured.config_dir),
        )
        return MetaResponse(
            name="EIS",
            version=__version__,
            mode="single-user",
            model_provider=configured.model_provider,
            model_configured=configured.provider_config is not None,
            model=(configured.provider_config.model if configured.provider_config else None),
            allow_cloud=configured.allow_cloud,
            project_roots=len(roots.list()),
            policy_default=policy.default,
            policy_version=policy.version,
            policy_hash=policy.document_hash,
        )

    @app.post("/api/v1/sessions", response_model=SessionResponse, dependencies=[Depends(authorize)])
    def create_session(payload: SessionCreate) -> SessionResponse:
        runtime_instance = runtime()
        if payload.project_id is not None:
            configured = settings()
            roots = ProjectRootStore(
                default_database(configured),
                reserved_paths=(configured.data_dir, configured.config_dir),
            )
            root = roots.get(payload.project_id)
            if root is None or root_status(root) != "available":
                raise HTTPException(status_code=404, detail="registered_project_not_found")
        try:
            return _session_response(
                runtime_instance.create_session(
                    title=payload.title,
                    project_id=payload.project_id,
                    actor="api_user",
                )
            )
        except (RuntimeStateError, ProjectRootError, PolicyError) as exc:
            raise HTTPException(status_code=422, detail="invalid_session") from exc

    @app.get(
        "/api/v1/sessions",
        response_model=list[SessionResponse],
        dependencies=[Depends(authorize)],
    )
    def list_sessions(limit: int = 100) -> list[SessionResponse]:
        try:
            return [_session_response(item) for item in runtime().list_sessions(limit=limit)]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_limit") from exc

    @app.get(
        "/api/v1/sessions/{session_id}",
        response_model=SessionResponse,
        dependencies=[Depends(authorize)],
    )
    def get_session(session_id: str) -> SessionResponse:
        record = runtime().get_session(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return _session_response(record)

    @app.post(
        "/api/v1/sessions/{session_id}/close",
        response_model=SessionResponse,
        dependencies=[Depends(authorize)],
    )
    def close_session(session_id: str) -> SessionResponse:
        try:
            return _session_response(runtime().close_session(session_id, actor="api_user"))
        except RuntimeStateError as exc:
            raise HTTPException(status_code=404, detail="session_not_found") from exc

    @app.post(
        "/api/v1/sessions/{session_id}/tasks",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    async def submit_task(session_id: str, payload: TaskSubmit) -> TaskResponse:
        try:
            runtime_instance = runtime()
            task = runtime_instance.create_task(session_id, payload.goal, actor="api_user")
            outcome = await runtime_instance.run_plan(task.task_id)
        except RuntimeStateError as exc:
            raise HTTPException(status_code=404, detail="session_unavailable") from exc
        except (AuditIntegrityError, PolicyError) as exc:
            raise HTTPException(status_code=503, detail="task_runtime_unavailable") from exc
        return _task_response(
            outcome.task,
            approval_id=outcome.approval_id,
            approval_digest=outcome.approval_digest,
        )

    @app.get(
        "/api/v1/tasks/{task_id}",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    def get_task(task_id: str) -> TaskResponse:
        task = runtime().get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task_not_found")
        return _task_response(task)

    @app.post(
        "/api/v1/tasks/{task_id}/cancel",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    def cancel_task(task_id: str) -> TaskResponse:
        try:
            outcome = runtime().cancel_task(task_id, actor="api_user")
        except (PolicyError, RuntimeStateError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=409, detail="task_cancel_rejected") from exc
        return _task_response(outcome.task)

    @app.post(
        "/api/v1/tasks/{task_id}/review/continue",
        response_model=TaskResponse,
        dependencies=[Depends(authorize)],
    )
    async def continue_task_review(task_id: str, payload: ReviewContinuation) -> TaskResponse:
        try:
            outcome = await runtime().continue_plan_review(
                task_id, payload.step_ref, actor="api_user"
            )
        except (PolicyError, RuntimeStateError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=409, detail="review_continuation_rejected") from exc
        return _task_response(outcome.task)

    @app.get(
        "/api/v1/approvals",
        response_model=list[ApprovalSummary],
        dependencies=[Depends(authorize)],
    )
    def list_approvals(limit: int = 100) -> list[ApprovalSummary]:
        try:
            records = ApprovalStore(default_database(settings())).list_pending(limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid_limit") from exc
        return [
            ApprovalSummary(
                approval_id=record.approval_id,
                task_id=record.task_id,
                status=record.status,
                resource=record.resource,
                summary=record.summary,
                expires_at_ms=record.expires_at_ms,
                request_digest=record.request_digest,
            )
            for record in records
        ]

    @app.get(
        "/api/v1/approvals/{approval_id}",
        response_model=ApprovalDetail,
        dependencies=[Depends(authorize)],
    )
    def get_approval(approval_id: str) -> ApprovalDetail:
        record = ApprovalStore(default_database(settings())).get(approval_id)
        if record is None:
            raise HTTPException(status_code=404, detail="approval_not_found")
        return ApprovalDetail(
            approval_id=record.approval_id,
            task_id=record.task_id,
            status=record.status,
            resource=record.resource,
            summary=record.summary,
            expires_at_ms=record.expires_at_ms,
            request_digest=record.request_digest,
            diff_text=record.diff_text,
        )

    @app.post(
        "/api/v1/approvals/{approval_id}/decision",
        response_model=ApprovalSummary,
        dependencies=[Depends(authorize)],
    )
    def decide_approval(approval_id: str, payload: ApprovalDecision) -> ApprovalSummary:
        _, sessions, gateway, record = execution_gateway(approval_id)
        try:
            if payload.decision == "approve":
                updated = gateway.grant(approval_id, confirmed_digest=payload.digest)
            else:
                updated = gateway.reject(
                    approval_id,
                    confirmed_digest=payload.digest,
                    reason=payload.note,
                )
            if updated.status == "rejected" and updated.task_id is not None:
                try:
                    sessions.fail_task_after_approval_rejection(
                        updated.task_id, approval_id, actor="api_user"
                    )
                except RuntimeStateError:
                    task = sessions.get_task(updated.task_id)
                    if task is None or task.state != "failed":
                        raise
        except (ApprovalError, ExecutionDenied, AuditIntegrityError) as exc:
            raise HTTPException(status_code=409, detail="approval_decision_rejected") from exc
        except RuntimeStateError as exc:
            raise HTTPException(status_code=409, detail="approval_task_state_conflict") from exc
        return ApprovalSummary(
            approval_id=updated.approval_id,
            task_id=updated.task_id,
            status=updated.status,
            resource=updated.resource,
            summary=updated.summary,
            expires_at_ms=updated.expires_at_ms,
            request_digest=updated.request_digest,
        )

    @app.post(
        "/api/v1/approvals/{approval_id}/execute",
        response_model=ApprovalExecutionResponse,
        dependencies=[Depends(authorize)],
    )
    def execute_approval(approval_id: str, payload: ApprovalExecution) -> ApprovalExecutionResponse:
        approvals, sessions, gateway, record = execution_gateway(approval_id)
        try:
            # Retry after execution but before/around task completion is recoverable:
            # never repeat a consumed write; require durable successful evidence.
            if record.status == "consumed":
                if payload.digest != record.request_digest:
                    raise ApprovalError("confirmed digest does not match approval")
                run = approvals.successful_execution(approval_id)
                if run is None or record.task_id is None:
                    raise ApprovalError("no durable successful execution evidence")
                recovered_task = sessions.complete_task_after_approval(
                    record.task_id, approval_id, actor="api_user"
                )
                return ApprovalExecutionResponse(
                    approval_id=approval_id,
                    status="success",
                    run_id=run.run_id,
                    exit_code=run.exit_code,
                    backup_id=run.backup_id,
                    task_id=recovered_task.task_id,
                    task_state=recovered_task.state,
                )
            result = gateway.execute_write(approval_id, confirmed_digest=payload.digest)
            task: TaskRecord | None = None
            if result.status == "success" and record.task_id is not None:
                task = sessions.complete_task_after_approval(
                    record.task_id, approval_id, actor="api_user"
                )
        except (ApprovalError, ExecutionDenied, AuditIntegrityError) as exc:
            raise HTTPException(status_code=409, detail="approval_execution_rejected") from exc
        except RuntimeStateError as exc:
            raise HTTPException(status_code=409, detail="approval_task_state_conflict") from exc
        return ApprovalExecutionResponse(
            approval_id=approval_id,
            status=result.status,
            run_id=result.run_id,
            exit_code=result.exit_code,
            backup_id=result.backup_id,
            task_id=task.task_id if task is not None else record.task_id,
            task_state=task.state if task is not None else None,
        )

    @app.get(
        "/api/v1/audit/verify",
        response_model=AuditVerifyResponse,
        dependencies=[Depends(authorize)],
    )
    def verify_audit() -> AuditVerifyResponse:
        audit = AuditChainStore(default_database(settings())).status()
        return AuditVerifyResponse(
            ok=audit.ok,
            event_count=audit.event_count,
            failed_seq=audit.failed_seq,
            failure=audit.failure,
        )

    @app.get(
        "/api/v1/audit/events",
        response_model=AuditEventPage,
        dependencies=[Depends(authorize), Depends(_strict_audit_query)],
    )
    def browse_audit_events(
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
    ) -> AuditEventPage:
        audit = AuditChainStore(default_database(settings()))
        status = audit.status()
        if not status.ok:
            raise HTTPException(status_code=503, detail="audit_chain_invalid")
        if cursor is None:
            after_seq, head_seq = 0, status.event_count
            expected_head_hash = status.head_hash
        else:
            try:
                decoded = _decode_cursor(cursor, expected_token())
            except (ValueError, UnicodeError, json.JSONDecodeError):
                raise HTTPException(status_code=422, detail="invalid_cursor") from None
            if decoded.head_seq > status.event_count:
                raise HTTPException(status_code=409, detail="stale_cursor")
            after_seq, head_seq = decoded.after_seq, decoded.head_seq
            expected_head_hash = decoded.head_hash
        events = audit.events_up_to(head_seq)
        prefix = audit.status_for(events)
        if not prefix.ok or len(events) != head_seq or prefix.head_hash != expected_head_hash:
            raise HTTPException(status_code=503, detail="audit_chain_invalid")
        selected = [item for item in events if item.seq > after_seq][:limit]
        next_cursor = None
        if after_seq + len(selected) < head_seq:
            next_cursor = _encode_cursor(
                _AuditCursor(after_seq + len(selected), head_seq, expected_head_hash),
                expected_token(),
            )
        return AuditEventPage(
            items=[
                AuditEventSummary(
                    seq=item.seq,
                    occurred_at_ms=item.occurred_at_ms,
                    event_type=item.event_type,
                    actor=item.actor,
                    subject_type=item.subject_type,
                    subject_id=item.subject_id,
                    decision=item.decision,
                    prev_hash=item.prev_hash,
                    event_hash=item.event_hash,
                )
                for item in selected
            ],
            next_cursor=next_cursor,
            integrity=AuditVerifyResponse(
                ok=True,
                event_count=head_seq,
                failed_seq=None,
                failure=None,
            ),
        )

    return app


app = create_app()
