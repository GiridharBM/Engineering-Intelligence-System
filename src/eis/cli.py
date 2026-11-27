# SPDX-License-Identifier: Apache-2.0

"""Command-line entry point for EIS."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import platform
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import typer

from eis import __version__
from eis.adapters.models.base import CompletionRequest, CompletionResponse
from eis.config import (
    EXTERNAL_PROVIDERS,
    UNTRUSTED_EXECUTION_POLICY,
    ConfigurationError,
    ConsentRecord,
    DataClass,
    ModelProviderName,
    Settings,
    is_within,
    load_local_env,
    load_settings,
)
from eis.core.api_auth import load_or_create_token, rotate_token
from eis.core.approvals import ApprovalError
from eis.core.audit_chain import AuditIntegrityError
from eis.core.execution import ExecutionDenied, ExecutionGateway
from eis.core.policy_service import PolicyService
from eis.core.project_roots import ProjectRootError, root_status
from eis.core.project_tools import ReadOnlyProjectTools, ToolRequestError, parse_tool_call
from eis.core.runtime import (
    PlanRecord,
    PlanStepRecord,
    RuntimeStateError,
    SessionTaskRuntime,
    TaskRecord,
)
from eis.core.task_approval import RegisteredProjectWriteProposer
from eis.policy import (
    PolicyEngine,
    PolicyError,
    PolicyRequest,
    load_default_policy,
    load_policy,
)
from eis.privacy import find_violations
from eis.storage.approvals import ApprovalStore
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import default_database
from eis.storage.gateway import build_sqlite_gateway
from eis.storage.plans import PlanStore
from eis.storage.policy_decision_store import PolicyDecisionStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

app = typer.Typer(no_args_is_help=True, help="Local-first engineering intelligence.")
consent_app = typer.Typer(no_args_is_help=True, help="Manage durable provider consent.")
audit_app = typer.Typer(no_args_is_help=True, help="Inspect the tamper-evident audit chain.")
approval_app = typer.Typer(
    no_args_is_help=True, help="Review and manage delegated write approvals."
)
session_app = typer.Typer(no_args_is_help=True, help="Create and manage durable sessions.")
task_app = typer.Typer(no_args_is_help=True, help="Submit and inspect durable tasks.")
project_app = typer.Typer(no_args_is_help=True, help="Register explicit local project roots.")
plan_app = typer.Typer(no_args_is_help=True, help="Run and inspect bounded task plans.")
app.add_typer(consent_app, name="consent")
app.add_typer(audit_app, name="audit")
app.add_typer(approval_app, name="approval")
app.add_typer(session_app, name="session")
app.add_typer(task_app, name="task")
app.add_typer(project_app, name="project")
app.add_typer(plan_app, name="plan")


def _load_settings() -> Settings:
    try:
        return load_settings()
    except ConfigurationError as exc:
        typer.echo(f"configuration_error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def version() -> None:
    """Print the installed EIS version."""
    typer.echo(f"eis {__version__}")


@app.command()
def doctor() -> None:
    """Print resolved settings and fail if runtime data is inside the repository."""
    settings = _load_settings()
    policy = load_default_policy()
    runtime_outside_repository = not is_within(settings.data_dir, Path.cwd())

    typer.echo(f"python={platform.python_version()}")
    typer.echo(f"data_dir={settings.data_dir}")
    typer.echo(f"config_dir={settings.config_dir}")
    typer.echo(f"runtime_outside_repository={str(runtime_outside_repository).lower()}")
    typer.echo(f"project_roots={len(settings.project_roots)}")
    typer.echo(f"model_provider={settings.model_provider or 'none'}")
    typer.echo(f"model_configured={str(settings.model_configured).lower()}")
    typer.echo(f"allow_cloud={str(settings.allow_cloud).lower()}")
    if settings.provider_config is not None:
        typer.echo(f"model={settings.provider_config.model}")
        typer.echo(f"model_base_url={settings.provider_config.base_url}")
    typer.echo(
        "env_consent="
        + (
            ",".join(sorted(settings.consent.data_classes))
            if settings.consent is not None
            else "none"
        )
    )
    typer.echo(f"policy_default={policy.default}")
    typer.echo(f"policy_version={policy.version}")
    typer.echo(f"session_retention_days={settings.session_retention_days}")
    typer.echo(f"audit_retention_days={settings.audit_retention_days}")
    typer.echo(f"engineering_retention={settings.engineering_retention}")
    typer.echo(f"untrusted_execution={UNTRUSTED_EXECUTION_POLICY}")

    if not runtime_outside_repository:
        typer.echo("error: runtime data directory is inside the repository", err=True)
        raise typer.Exit(1)


@app.command("provider-check")
def provider_check() -> None:
    """Validate provider configuration without making a network request."""
    settings = _load_settings()
    if settings.provider_config is None:
        typer.echo(f"provider={settings.model_provider or 'none'} configured=false")
        return
    config = settings.provider_config
    consent = (
        ",".join(sorted(settings.consent.data_classes)) if settings.consent is not None else "none"
    )
    typer.echo(
        f"provider={config.provider} configured=true "
        f"model={config.model} "
        f"base_url={config.base_url} "
        f"api_key=set env_consent={consent}"
    )


@consent_app.command("grant")
def consent_grant(
    provider: str = typer.Option(..., help="External provider name."),
    classes: str = typer.Option(..., help="Comma-separated C0, C1 or C2."),
    purpose: str = typer.Option("model completion", help="Purpose label."),
    expires_days: int | None = typer.Option(
        None, help="Optional expiry in days; omit for no expiry."
    ),
) -> None:
    """Persist explicit provider consent; never prints or stores a key."""
    if provider not in EXTERNAL_PROVIDERS:
        typer.echo(f"error: unsupported provider: {provider}", err=True)
        raise typer.Exit(1)
    selected = {item.strip().upper() for item in classes.split(",") if item.strip()}
    if not selected or not selected <= {"C0", "C1", "C2"}:
        typer.echo("error: classes must contain only C0, C1 or C2", err=True)
        raise typer.Exit(1)
    if expires_days is not None and expires_days <= 0:
        typer.echo("error: expires-days must be positive", err=True)
        raise typer.Exit(1)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    record = ConsentRecord(
        consent_id=f"cns_{uuid.uuid4().hex[:16]}",
        provider=cast(ModelProviderName, provider),
        data_classes=frozenset(cast(DataClass, item) for item in selected),
        purpose=purpose,
        expires_at_ms=(now_ms + expires_days * 86_400_000 if expires_days is not None else None),
    )
    _consent_store().grant(record, actor="cli")
    typer.echo(
        f"consent_id={record.consent_id} provider={record.provider} "
        f"classes={','.join(sorted(record.data_classes))} "
        f"expires_at_ms={record.expires_at_ms}"
    )


@consent_app.command("revoke")
def consent_revoke(consent_id: str = typer.Argument(...)) -> None:
    """Revoke a consent; the next model call must re-check and deny."""
    if not _consent_store().revoke(consent_id, actor="cli"):
        typer.echo(f"error: active consent not found: {consent_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"revoked={consent_id}")


@consent_app.command("list")
def consent_list() -> None:
    """List stored consent records without secrets."""
    for record in _consent_store().list_all():
        expires = (
            datetime.fromtimestamp(record.expires_at_ms / 1000, UTC).isoformat()
            if record.expires_at_ms is not None
            else "none"
        )
        revoked = (
            datetime.fromtimestamp(record.revoked_at_ms / 1000, UTC).isoformat()
            if record.revoked_at_ms is not None
            else "no"
        )
        typer.echo(
            f"{record.consent_id} provider={record.provider} "
            f"classes={','.join(sorted(record.data_classes))} "
            f"purpose={record.purpose!r} expires={expires} revoked={revoked}"
        )


@audit_app.command("verify")
def audit_verify() -> None:
    """Verify sequence, hashes and previous-hash links; print no payloads."""
    status = AuditChainStore(default_database(load_settings(require_provider=False))).status()
    if not status.ok:
        typer.echo(
            f"audit_chain=failed events={status.event_count} "
            f"failed_seq={status.failed_seq} reason={status.failure}",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(f"audit_chain=ok events={status.event_count}")


def _consent_store() -> ConsentStore:
    settings = load_settings(require_provider=False)
    return ConsentStore(default_database(settings))


def _execution_gateway(
    root: Path, project_id: str, policy_file: Path | None = None
) -> ExecutionGateway:
    settings = load_settings(require_provider=False)
    database = default_database(settings)
    selected_policy = load_policy(policy_file) if policy_file is not None else load_default_policy()
    policy = PolicyService(PolicyEngine(selected_policy), SqlitePolicyDecisionSink(database))
    return ExecutionGateway(
        project_roots={project_id: root},
        trusted_projects=frozenset(),
        backup_dir=settings.data_dir / "backups",
        policy=policy,
        approvals=ApprovalStore(database),
        integrity=AuditChainStore(database),
    )


def _session_store() -> SessionTaskStore:
    settings = load_settings(require_provider=False)
    return SessionTaskStore(default_database(settings))


def _plan_store() -> PlanStore:
    settings = load_settings(require_provider=False)
    return PlanStore(default_database(settings))


def _session_runtime(
    policy_file: Path | None = None, *, fake_provider: bool = False
) -> SessionTaskRuntime:
    settings = load_settings(require_provider=not fake_provider)
    database = default_database(settings)
    selected_policy = load_policy(policy_file) if policy_file is not None else load_default_policy()
    policy = PolicyService(PolicyEngine(selected_policy), SqlitePolicyDecisionSink(database))
    roots = _project_root_store(settings)
    integrity = AuditChainStore(database)
    approvals = ApprovalStore(database)
    tools = ReadOnlyProjectTools(
        project_roots=roots,
        policy=policy,
        audit=integrity,
    )
    gateway = (
        _FakePlanGateway()
        if fake_provider
        else build_sqlite_gateway(settings)
        if settings.provider_config is not None
        else None
    )
    return SessionTaskRuntime(
        SessionTaskStore(database),
        policy,
        gateway,  # type: ignore[arg-type]
        project_tools=tools,
        plans=PlanStore(database),
        write_proposals=RegisteredProjectWriteProposer(
            roots=roots,
            policy=policy,
            approvals=approvals,
            integrity=integrity,
            backup_dir=settings.data_dir / "backups",
        ),
    )


class _FakePlanGateway:
    """Deterministic local plan response for CLI/runtime smoke tests only."""

    async def complete(
        self, request: CompletionRequest, data_class: str = "C1"
    ) -> CompletionResponse:
        del request, data_class
        return CompletionResponse(
            text=(
                '{"kind":"plan","steps":[{"id":"review","tool":"human_review",'
                '"arguments":{},"depends_on":[]}]}'
            ),
            provider="fake",
            model="deterministic-fake",
            finish_reason="stop",
        )


def _project_root_store(settings: Settings) -> ProjectRootStore:
    return ProjectRootStore(
        default_database(settings),
        reserved_paths=(settings.data_dir, settings.config_dir),
    )


def _tool_runtime(
    session_id: str,
    policy_file: Path | None = None,
) -> SessionTaskRuntime:
    settings = load_settings(require_provider=False)
    database = default_database(settings)
    sessions = SessionTaskStore(database)
    session = sessions.get_session(session_id)
    if session is None or session.project_id is None:
        raise RuntimeStateError("session must exist and be bound to a project")
    roots = _project_root_store(settings)
    if roots.get(session.project_id) is None:
        raise RuntimeStateError("session project root is not registered")
    selected_policy = load_policy(policy_file) if policy_file is not None else load_default_policy()
    policy = PolicyService(PolicyEngine(selected_policy), SqlitePolicyDecisionSink(database))
    tools = ReadOnlyProjectTools(
        project_roots=roots,
        policy=policy,
        audit=AuditChainStore(database),
    )
    return SessionTaskRuntime(
        sessions,
        policy,
        None,
        project_tools=tools,
    )


def _path_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _task_summary(task: TaskRecord) -> str:
    return (
        f"task_id={task.task_id} state={task.state} "
        f"stop_reason={task.stop_reason or 'none'} "
        f"request_hash={task.request_hash or 'none'} "
        f"response_hash={task.response_hash or 'none'} "
        f"finish_reason={task.finish_reason or 'none'} "
        f"completions={task.usage.get('completions', 0)} "
        f"tool_calls={task.usage.get('tool_calls', 0)}"
    )


def _plan_summary(plan: PlanRecord, steps: Sequence[PlanStepRecord]) -> str:
    completed = sum(1 for step in steps if step.status == "completed")
    return (
        f"plan_id={plan.plan_id} task_id={plan.task_id} state={plan.state} "
        f"project={plan.project_id or 'none'} policy_version={plan.policy_version} "
        f"policy_hash={plan.policy_hash} plan_hash={plan.plan_hash} "
        f"stop_reason={plan.stop_reason or 'none'} steps={len(steps)} "
        f"completed={completed} created_at_ms={plan.created_at_ms}"
    )


def _proposal_preview(text: str) -> str:
    cleaned = "".join(
        character if character.isprintable() or character in {"\n", "\t"} else "\ufffd"
        for character in text
    )
    return cleaned[:4000]


@session_app.command("create")
def session_create(
    title: str = typer.Option("Untitled", help="Short non-sensitive session label."),
    project: str | None = typer.Option(None, help="Validated project identifier."),
) -> None:
    """Create a durable active session."""
    try:
        record = _session_store().create_session(title=title, project_id=project, actor="user")
    except (RuntimeStateError, PolicyError, AuditIntegrityError) as exc:
        typer.echo(f"session_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"session_id={record.session_id} status={record.status} "
        f"project={record.project_id or 'none'}"
    )


@session_app.command("list")
def session_list(limit: int = typer.Option(50, min=1, max=1000)) -> None:
    """List recent sessions without task goals or model content."""
    for record in _session_store().list_sessions(limit=limit):
        typer.echo(
            f"{record.session_id} status={record.status} "
            f"project={record.project_id or 'none'} title={record.title!r} "
            f"updated_at_ms={record.updated_at_ms}"
        )


@session_app.command("show")
def session_show(session_id: str = typer.Argument(...)) -> None:
    """Show one session and its task states, without task goals."""
    store = _session_store()
    record = store.get_session(session_id)
    if record is None:
        typer.echo("session_error: not found", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"session_id={record.session_id} status={record.status} "
        f"project={record.project_id or 'none'} title={record.title!r} "
        f"created_at_ms={record.created_at_ms} closed_at_ms={record.closed_at_ms or 'none'}"
    )
    for task in store.list_tasks(session_id):
        typer.echo(_task_summary(task))


@session_app.command("resume")
def session_resume(session_id: str = typer.Argument(...)) -> None:
    """Reopen an idle session; a closed session remains closed."""
    try:
        record = _session_store().resume_session(session_id, actor="user")
    except (RuntimeStateError, AuditIntegrityError) as exc:
        typer.echo(f"session_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"session_id={record.session_id} status={record.status}")


@session_app.command("close")
def session_close(session_id: str = typer.Argument(...)) -> None:
    """Close a session so no new tasks can be created."""
    try:
        record = _session_store().close_session(session_id, actor="user")
    except (RuntimeStateError, AuditIntegrityError) as exc:
        typer.echo(f"session_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"session_id={record.session_id} status={record.status}")


def _project_store() -> ProjectRootStore:
    return _project_root_store(load_settings(require_provider=False))


@project_app.command("register")
def project_register(
    project_id: str = typer.Argument(...),
    root: Path = typer.Argument(  # noqa: B008
        ..., help="Existing local directory; never supplied by a model."
    ),
) -> None:
    """Register one explicit local project root for read-only tool access."""
    try:
        record = _project_store().register(project_id, root, actor="user")
    except ProjectRootError as exc:
        typer.echo(f"project_error: {exc.code}", err=True)
        raise typer.Exit(1) from None
    except (AuditIntegrityError, OSError):
        typer.echo("project_error: registration_failed", err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"project_id={record.project_id} status=registered "
        f"root_sha256={_path_sha256(record.root_path)} "
        f"root_identity={record.root_identity}"
    )


@project_app.command("list")
def project_list() -> None:
    """List registered roots without printing absolute paths."""
    for record in _project_store().list():
        typer.echo(
            f"{record.project_id} status={root_status(record)} "
            f"root_sha256={_path_sha256(record.root_path)} "
            f"root_identity={record.root_identity} "
            f"created_at_ms={record.created_at_ms}"
        )


@project_app.command("remove")
def project_remove(project_id: str = typer.Argument(...)) -> None:
    """Remove a registration; no files are deleted."""
    try:
        removed = _project_store().remove(project_id, actor="user")
    except (AuditIntegrityError, OSError):
        typer.echo("project_error: removal_failed", err=True)
        raise typer.Exit(1) from None
    if not removed:
        typer.echo(f"project_error: not_registered: {project_id}", err=True)
        raise typer.Exit(1)
    typer.echo(f"project_id={project_id} status=removed")


@task_app.command("submit")
def task_submit(
    session_id: str = typer.Argument(...),
    goal: str = typer.Option(..., help="Task objective; never printed or audited raw."),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
    principal: str = typer.Option("agent:eis.primary"),
    show_proposal: bool = typer.Option(
        False,
        help="Print the final model-generated answer or plan preview when available.",
    ),
) -> None:
    """Run a bounded task loop; proposed writes stop for explicit approval."""
    try:
        runtime = _session_runtime(policy_file)
        outcome = asyncio.run(runtime.submit_task(session_id, goal, principal=principal))
    except (
        ConfigurationError,
        PolicyError,
        RuntimeStateError,
        AuditIntegrityError,
    ) as exc:
        typer.echo(f"task_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.approval_id is not None:
        typer.echo(
            f"approval_required approval_id={outcome.approval_id} digest={outcome.approval_digest}"
        )
        typer.echo("review with `eis approval show`, then grant and execute the exact digest")
    if outcome.proposal_text is not None:
        typer.echo("answer=untrusted_model_generated_no_action_taken")
        if show_proposal:
            typer.echo("model_generated_answer_begin")
            typer.echo(_proposal_preview(outcome.proposal_text))
            typer.echo("model_generated_answer_end")
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@task_app.command("cancel")
def task_cancel(
    task_id: str = typer.Argument(...),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Cancel unexecuted task/plan/steps; never undo successful writes."""
    try:
        outcome = _session_runtime(policy_file).cancel_task(task_id, actor="cli")
    except (PolicyError, RuntimeStateError, AuditIntegrityError) as exc:
        typer.echo(f"task_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.plan is not None:
        typer.echo(_plan_summary(outcome.plan, outcome.steps))


@task_app.command("resume")
def task_resume(
    task_id: str = typer.Argument(...),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Resume a created task or explicitly continue a recoverable plan."""
    try:
        runtime = _session_runtime(policy_file)
        outcome = asyncio.run(runtime.resume_task(task_id))
    except (
        ConfigurationError,
        PolicyError,
        RuntimeStateError,
        AuditIntegrityError,
    ) as exc:
        typer.echo(f"task_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@task_app.command("status")
def task_status(task_id: str = typer.Argument(...)) -> None:
    """Show task state and content-free hashes only."""
    task = _session_store().get_task(task_id)
    if task is None:
        typer.echo("task_error: not found", err=True)
        raise typer.Exit(1)
    typer.echo(_task_summary(task))


@task_app.command("list")
def task_list(
    session_id: str = typer.Argument(...),
    limit: int = typer.Option(50, min=1, max=1000),
) -> None:
    """List a session's tasks without goals or model text."""
    for task in _session_store().list_tasks(session_id, limit=limit):
        typer.echo(_task_summary(task))


@task_app.command("invoke-tool")
def task_invoke_tool(
    session_id: str = typer.Argument(...),
    call_json: str = typer.Option(
        ...,
        "--call-json",
        help="Strict JSON for one registered read-only tool call; never inferred from model text.",
    ),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Explicitly invoke one policy-gated read-only project tool."""
    try:
        call = parse_tool_call(call_json)
        runtime = _tool_runtime(session_id, policy_file)
        outcome = runtime.invoke_tool(session_id, call, actor="cli")
    except ToolRequestError:
        typer.echo("tool_error: invalid_or_unsafe_tool_call", err=True)
        raise typer.Exit(1) from None
    except (PolicyError, RuntimeStateError, AuditIntegrityError, OSError, ValueError) as exc:
        typer.echo(f"tool_error: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from None

    result = outcome.tool
    typer.echo(
        f"task_id={outcome.task.task_id} state={outcome.task.state} "
        f"invocation_id={result.invocation_id} tool={result.tool} status={result.status} "
        f"output_sha256={result.output_sha256 or 'none'} output_bytes={result.output_bytes} "
        f"error_code={result.error_code or 'none'}"
    )
    if result.status != "success":
        raise typer.Exit(1)


@plan_app.command("run")
def plan_run(
    session_id: str = typer.Argument(...),
    goal: str = typer.Option(..., help="Task objective; never printed or audited raw."),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
    show_proposal: bool = typer.Option(
        False,
        help="Print the generated plan; it is untrusted and steps are policy-gated.",
    ),
) -> None:
    """Create a task, generate a strict plan and run its read-only steps."""
    try:
        runtime = _session_runtime(policy_file)
        task = runtime.create_task(session_id, goal, actor="cli")
        outcome = asyncio.run(runtime.run_plan(task.task_id))
    except (
        ConfigurationError,
        PolicyError,
        RuntimeStateError,
        AuditIntegrityError,
    ) as exc:
        typer.echo(f"plan_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.plan is not None:
        typer.echo(_plan_summary(outcome.plan, outcome.steps))
    if outcome.proposal_text is not None:
        typer.echo("plan=untrusted_model_generated_no_action_taken")
        if show_proposal:
            typer.echo("model_generated_plan_begin")
            typer.echo(_proposal_preview(outcome.proposal_text))
            typer.echo("model_generated_plan_end")
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@app.command("ask")
def ask(
    goal: str = typer.Argument(..., help="Task objective; it is stored locally."),
    session_id: str | None = typer.Option(None, help="Resume an existing session."),
    project_id: str | None = typer.Option(None, help="Registered project ID for a new session."),
    fake: bool = typer.Option(False, "--fake", help="Use a deterministic local smoke response."),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Submit one bounded task plan; --fake makes no model-provider request."""
    try:
        runtime = _session_runtime(policy_file, fake_provider=fake)
        if session_id is None:
            session = runtime.create_session(title="CLI ask", project_id=project_id, actor="cli")
            selected_session = session.session_id
        else:
            existing_session = runtime.get_session(session_id)
            if existing_session is None:
                raise RuntimeStateError("session not found")
            if project_id is not None and project_id != existing_session.project_id:
                raise RuntimeStateError("project ID does not match the selected session")
            selected_session = session_id
        task = runtime.create_task(selected_session, goal, actor="cli")
        outcome = asyncio.run(runtime.run_plan(task.task_id))
    except (
        ConfigurationError,
        PolicyError,
        RuntimeStateError,
        AuditIntegrityError,
        typer.BadParameter,
    ) as exc:
        typer.echo(f"ask_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"session_id={selected_session}")
    typer.echo(_task_summary(outcome.task))
    if outcome.approval_id is not None:
        typer.echo(
            f"approval_required approval_id={outcome.approval_id} digest={outcome.approval_digest}"
        )
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@plan_app.command("resume")
def plan_resume(
    task_id: str = typer.Argument(...),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Explicitly resume an interrupted, bounded read-only task plan."""
    try:
        outcome = asyncio.run(_session_runtime(policy_file).resume_plan(task_id))
    except (ConfigurationError, PolicyError, RuntimeStateError, AuditIntegrityError) as exc:
        typer.echo(f"plan_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.plan is not None:
        typer.echo(
            f"plan_id={outcome.plan.plan_id} state={outcome.plan.state} "
            f"stop_reason={outcome.plan.stop_reason or 'none'}"
        )
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@plan_app.command("continue-review")
def plan_continue_review(
    task_id: str = typer.Argument(...),
    step_ref: str = typer.Argument(..., help="Blocked human-review step reference."),
    policy_file: Path | None = typer.Option(  # noqa: B008
        None, exists=True, dir_okay=False, resolve_path=True
    ),
) -> None:
    """Continue a strictly validated human-review step; grants no permission."""
    try:
        outcome = asyncio.run(
            _session_runtime(policy_file).continue_plan_review(task_id, step_ref, actor="cli")
        )
    except (ConfigurationError, PolicyError, RuntimeStateError, AuditIntegrityError) as exc:
        typer.echo(f"plan_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(_task_summary(outcome.task))
    if outcome.plan is not None:
        typer.echo(_plan_summary(outcome.plan, outcome.steps))
    if outcome.task.state == "failed":
        raise typer.Exit(1)


@plan_app.command("show")
def plan_show(plan_id: str = typer.Argument(...)) -> None:
    """Show plan identity, state and hashes; no goals, arguments or results."""
    plan = _plan_store().get_plan(plan_id)
    if plan is None:
        typer.echo("plan_error: not found", err=True)
        raise typer.Exit(1)
    typer.echo(_plan_summary(plan, _plan_store().list_steps(plan_id)))


@plan_app.command("steps")
def plan_steps(plan_id: str = typer.Argument(...)) -> None:
    """List ordered step statuses without arguments, content or results."""
    store = _plan_store()
    if store.get_plan(plan_id) is None:
        typer.echo("plan_error: not found", err=True)
        raise typer.Exit(1)
    for step in store.list_steps(plan_id):
        typer.echo(
            f"{step.step_index} ref={step.step_ref} tool={step.tool} status={step.status} "
            f"depends_on={','.join(step.depends_on) or 'none'} "
            f"policy_decision_id={step.policy_decision_id or 'none'} "
            f"output_sha256={step.output_sha256 or 'none'} "
            f"error_code={step.error_code or 'none'}"
        )


@app.command("policy-decisions")
def policy_decisions(limit: int = typer.Option(50, min=1, max=1000)) -> None:
    """List content-free persisted policy decisions."""
    database = default_database(load_settings(require_provider=False))
    for item in PolicyDecisionStore(database).list(limit=limit):
        typer.echo(
            f"{item.decision_id} effect={item.effect} principal={item.principal} "
            f"action={item.action} resource={item.resource} reason={item.reason} "
            f"rule_id={item.rule_id} policy_version={item.policy_version}"
        )


@approval_app.command("propose-write")
def approval_propose_write(
    project_id: str = typer.Option(...),
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),  # noqa: B008
    path: str = typer.Option(..., help="Project relative POSIX file path."),
    content_file: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),  # noqa: B008
    policy_file: Path | None = typer.Option(None, exists=True, dir_okay=False, resolve_path=True),  # noqa: B008
    on_behalf_of: str = typer.Option("agent:eis.primary"),
    summary: str = typer.Option(...),
) -> None:
    """Create a pending approval from a UTF-8 proposed content file."""
    try:
        content = content_file.read_text(encoding="utf-8")
        record = _execution_gateway(root, project_id, policy_file).propose_write(
            project_id=project_id,
            relative_path=path,
            new_content=content,
            on_behalf_of=on_behalf_of,
            summary=summary,
        )
    except (
        OSError,
        UnicodeError,
        PolicyError,
        ApprovalError,
        ExecutionDenied,
        AuditIntegrityError,
    ) as exc:
        typer.echo(f"approval_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"approval_id={record.approval_id} status={record.status} digest={record.request_digest}"
    )


@approval_app.command("list")
def approval_list() -> None:
    """List pending approvals without displaying proposed content."""
    database = default_database(load_settings(require_provider=False))
    for record in ApprovalStore(database).list_pending():
        typer.echo(
            f"{record.approval_id} status={record.status} resource={record.resource} "
            f"summary={record.summary!r} expires_at_ms={record.expires_at_ms} "
            f"digest={record.request_digest}"
        )


@approval_app.command("show")
def approval_show(approval_id: str = typer.Argument(...)) -> None:
    """Display the exact diff and digest that a user can approve."""
    database = default_database(load_settings(require_provider=False))
    record = ApprovalStore(database).get(approval_id)
    if record is None:
        typer.echo("approval_error: not found", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"approval_id={record.approval_id} status={record.status} action={record.action}\n"
        f"task_id={record.task_id or 'none'}\n"
        f"on_behalf_of={record.on_behalf_of} resource={record.resource}\n"
        f"summary={record.summary}\nexpires_at_ms={record.expires_at_ms}\n"
        f"digest={record.request_digest}\nrisk_class={record.risk_class}\n"
        + (f"decision_note={record.decision_note}\n" if record.decision_note else "")
        + "--- diff ---\n"
        + record.diff_text
    )


@approval_app.command("grant")
def approval_grant(approval_id: str = typer.Argument(...), digest: str = typer.Option(...)) -> None:
    """Grant only after comparing the supplied digest with `approval show`."""
    record = ApprovalStore(default_database(load_settings(require_provider=False))).get(approval_id)
    if record is None:
        typer.echo("approval_error: not found", err=True)
        raise typer.Exit(1)
    if digest != record.request_digest:
        typer.echo("approval_error: digest mismatch", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Reviewing approval {approval_id} for {record.resource}\n"
        f"summary={record.summary}\ndigest={record.request_digest}\n--- diff ---\n"
        + record.diff_text
    )
    if not typer.confirm(f"Grant this exact A3 project write? digest={record.request_digest}"):
        raise typer.Exit(1)
    project_id, _ = _approval_project_path(record.resource)
    try:
        updated = _execution_gateway(_approval_root(record), project_id).grant(
            approval_id, confirmed_digest=digest
        )
    except (
        PolicyError,
        ApprovalError,
        ExecutionDenied,
        AuditIntegrityError,
        RuntimeStateError,
    ) as exc:
        typer.echo(f"approval_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"approval_id={updated.approval_id} status={updated.status}")


@approval_app.command("reject")
def approval_reject(
    approval_id: str = typer.Argument(...),
    digest: str = typer.Option(...),
    reason: str = typer.Option(""),
) -> None:
    """Reject a pending approval bound to its displayed digest."""
    store = ApprovalStore(default_database(load_settings(require_provider=False)))
    record = store.get(approval_id)
    if record is None:
        typer.echo("approval_error: not found", err=True)
        raise typer.Exit(1)
    project_id, _ = _approval_project_path(record.resource)
    try:
        updated = _execution_gateway(_approval_root(record), project_id).reject(
            approval_id, confirmed_digest=digest, reason=reason
        )
        failed_task = (
            _session_store().fail_task_after_approval_rejection(record.task_id, approval_id)
            if record.task_id is not None
            else None
        )
    except (PolicyError, ApprovalError, ExecutionDenied, AuditIntegrityError) as exc:
        typer.echo(f"approval_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except RuntimeStateError as exc:
        typer.echo(f"approval_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"approval_id={updated.approval_id} status={updated.status} "
        f"task_id={failed_task.task_id if failed_task is not None else 'none'} "
        f"task_state={failed_task.state if failed_task is not None else 'none'}"
    )


@approval_app.command("execute")
def approval_execute(
    approval_id: str = typer.Argument(...),
    digest: str = typer.Option(...),
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),  # noqa: B008
    policy_file: Path | None = typer.Option(None, exists=True, dir_okay=False, resolve_path=True),  # noqa: B008
) -> None:
    """Apply one already approved write after rechecking policy and file hashes."""
    sessions = _session_store()
    approvals = ApprovalStore(default_database(load_settings(require_provider=False)))
    record = approvals.get(approval_id)
    if record is None:
        typer.echo("approval_error: not found", err=True)
        raise typer.Exit(1)
    if record.status == "consumed":
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not hmac.compare_digest(
            digest, record.request_digest
        ):
            typer.echo("execution_error: digest_mismatch", err=True)
            raise typer.Exit(1)
        if record.task_id is None:
            typer.echo("execution_error: approval was already consumed", err=True)
            raise typer.Exit(1)
        if approvals.successful_execution(approval_id) is None:
            if record.task_id is not None:
                try:
                    sessions.fail_task_after_approval_without_evidence(
                        record.task_id, approval_id, actor="cli"
                    )
                except RuntimeStateError as exc:
                    typer.echo(f"execution_error: {exc}", err=True)
                    raise typer.Exit(1) from exc
            typer.echo("execution_error: approval_execution_unverified", err=True)
            raise typer.Exit(1)
        try:
            already_completed = sessions.complete_task_after_approval(record.task_id, approval_id)
        except RuntimeStateError as exc:
            typer.echo(f"execution_error: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(
            f"approval_id={approval_id} status=already_executed "
            f"task_id={already_completed.task_id} task_state={already_completed.state}"
        )
        return
    project_id, _ = _approval_project_path(record.resource)
    try:
        result = _execution_gateway(root, project_id, policy_file).execute_write(
            approval_id, confirmed_digest=digest
        )
        completed = (
            sessions.complete_task_after_approval(record.task_id, approval_id)
            if record.task_id is not None and result.status == "success"
            else None
        )
    except (
        PolicyError,
        ApprovalError,
        ExecutionDenied,
        AuditIntegrityError,
        RuntimeStateError,
    ) as exc:
        typer.echo(f"execution_error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"run_id={result.run_id} status={result.status} backup_id={result.backup_id or 'none'} "
        f"task_id={completed.task_id if completed is not None else 'none'} "
        f"task_state={completed.state if completed is not None else 'none'}"
    )


def _approval_project_path(resource: str) -> tuple[str, str]:
    kind, separator, value = resource.partition(":")
    project_id, path_separator, relative_path = value.partition("/")
    if kind != "path" or not separator or not path_separator:
        raise typer.BadParameter("approval resource is not a project path")
    return project_id, relative_path


def _approval_root(record: object) -> Path:
    # The approval binds the canonical root by digest, not by persisting an absolute path.
    # Users must run grant/reject from the registered project root in this CLI slice.
    del record
    return Path.cwd()


@app.command("policy-evaluate")
def policy_evaluate(
    principal: str = typer.Option(..., help="user, system or typed agent/skill/job identifier."),
    action: str = typer.Option(..., help="Validated action such as project.read."),
    resource: str = typer.Option(..., help="Validated resource such as project:example."),
    source: str = typer.Option("cli", help="Request source such as cli, api, agent or system."),
) -> None:
    """Evaluate one request and chain the decision before printing it."""
    try:
        request = PolicyRequest.from_mapping(
            {
                "principal": principal,
                "action": action,
                "resource": resource,
                "context": {"source": source},
            }
        )
    except PolicyError:
        typer.echo("policy_error=invalid_request", err=True)
        raise typer.Exit(1) from None

    service = PolicyService(
        PolicyEngine(load_default_policy()),
        SqlitePolicyDecisionSink(default_database(load_settings(require_provider=False))),
    )
    try:
        decision = service.evaluate(request)
    except AuditIntegrityError:
        typer.echo("audit_chain=failed action=blocked", err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"policy_version={decision.policy_version} effect={decision.effect} "
        f"reason={decision.reason} rule_id={decision.rule_id} "
        f"decision_id={decision.decision_id} audit_event={decision.decision_id}"
    )


@app.command("policy-check")
def policy_check() -> None:
    """Verify that an unspecified action is denied by the default policy."""
    policy = load_default_policy()
    request = PolicyRequest.from_mapping(
        {
            "principal": "agent:eis.primary",
            "action": "project.write",
            "resource": "project:unregistered",
            "context": {"source": "cli"},
        }
    )
    decision = PolicyEngine(policy).evaluate(request)
    typer.echo(
        f"default={policy.default} rules={len(policy.rules)} "
        f"policy_version={policy.version} probe={decision.effect} "
        f"reason={decision.reason} decision_id={decision.decision_id}"
    )
    if decision.effect != "deny":
        raise typer.Exit(1)


@app.command("privacy-check")
def privacy_check() -> None:
    """Scan candidate repository files for private paths, runtime data and secrets."""
    violations = find_violations(Path.cwd())
    if not violations:
        typer.echo("privacy: ok")
        return
    for violation in violations:
        typer.echo(str(violation), err=True)
    raise typer.Exit(1)


@app.command("api-token")
def api_token(rotate: bool = typer.Option(False, "--rotate")) -> None:
    """Display or rotate the local bearer token for authenticated API access."""
    settings = load_settings(require_provider=False)
    token = (
        rotate_token(settings.config_dir) if rotate else load_or_create_token(settings.config_dir)
    )
    typer.echo(token)


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", help="Loopback interface only."),
    port: int = typer.Option(8765, min=1, max=65535),
) -> None:
    """Start the local authenticated HTTP API."""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        typer.echo("server_error: only loopback hosts are allowed", err=True)
        raise typer.Exit(2)
    settings = load_settings(require_provider=False)
    load_or_create_token(settings.config_dir)
    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError as exc:
        typer.echo("server_error: install the declared uvicorn dependency", err=True)
        raise typer.Exit(1) from exc
    uvicorn.run("eis.api:app", host=host, port=port, log_config=None)


def main() -> None:
    """Console entry point: load local .env, then dispatch the CLI."""
    load_local_env()
    app()


if __name__ == "__main__":
    main()
