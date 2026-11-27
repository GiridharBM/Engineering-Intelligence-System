# SPDX-License-Identifier: Apache-2.0

"""M1 crash-recovery boundaries and durable-evidence tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from eis.adapters.models.base import CompletionRequest, CompletionResponse, TokenUsage
from eis.config import ConsentRecord
from eis.core.audit_chain import AuditIntegrityError
from eis.core.consent import ConsentGate
from eis.core.execution import ExecutionGateway
from eis.core.model_gateway import ModelGateway
from eis.core.model_plan import WRITE_PROPOSAL_TOOL
from eis.core.policy_service import PolicyService
from eis.core.project_tools import ReadOnlyProjectTools
from eis.core.runtime import SessionTaskRuntime
from eis.core.task_approval import RegisteredProjectWriteProposer
from eis.policy import Policy, PolicyEngine
from eis.storage.approvals import ApprovalStore
from eis.storage.audit import SqliteAuditSink
from eis.storage.audit_chain import AuditChainStore
from eis.storage.consents import ConsentStore
from eis.storage.db import SqliteDatabase
from eis.storage.plans import PlanStore
from eis.storage.policy_decisions import SqlitePolicyDecisionSink
from eis.storage.project_roots import ProjectRootStore
from eis.storage.sessions import SessionTaskStore

OLD_CONTENT = "old-content\n"
NEW_CONTENT = "new-approved-content-marker"
GOAL = "update the project file"


class ProposalProvider:
    name = "openai"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls += 1
        plan = {
            "kind": "plan",
            "steps": [
                {
                    "id": "read",
                    "tool": "project.read_text",
                    "arguments": {"path": "app.py"},
                    "depends_on": [],
                },
                {
                    "id": "propose",
                    "tool": WRITE_PROPOSAL_TOOL,
                    "arguments": {
                        "path": "app.py",
                        "content": NEW_CONTENT,
                        "summary": "Apply the requested update",
                    },
                    "depends_on": ["read"],
                },
            ],
        }
        return CompletionResponse(
            text=json.dumps(plan, separators=(",", ":")),
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 1, 1),
        )


def _policy() -> Policy:
    rules = [
        ("agent:eis.primary", "task.run", "project:demo"),
        ("agent:eis.primary", "task.cancel", "project:demo"),
        ("agent:eis.primary", "project.read", "path:demo/app.py"),
        ("agent:eis.primary", "project.propose", "project:demo"),
        ("user", "project.read", "path:demo/app.py"),
        ("user", "project.write", "path:demo/app.py"),
    ]
    return Policy.from_dict(
        {
            "version": "crash-recovery-v1",
            "default": "deny",
            "rules": [
                {
                    "id": f"allow-{index}",
                    "principal": principal,
                    "action": action,
                    "resource": resource,
                    "effect": "allow",
                }
                for index, (principal, action, resource) in enumerate(rules)
            ],
        }
    )


@dataclass
class _ApprovalFx:
    root: Path
    target: Path
    database: SqliteDatabase
    sessions: SessionTaskStore
    approvals: ApprovalStore
    plans: PlanStore
    audit: AuditChainStore
    runtime: SessionTaskRuntime
    task_id: str
    approval_id: str


def _approval_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _ApprovalFx:
    root = tmp_path / "project"
    root.mkdir(parents=True)
    target = root / "app.py"
    target.write_text(OLD_CONTENT, encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    roots = ProjectRootStore(database)
    roots.register("demo", root, actor="test")
    audit = AuditChainStore(database)
    approvals = ApprovalStore(database)
    plans = PlanStore(database)
    policy = PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(database))
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns_recovery",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
            purpose="recovery tests",
        )
    )
    tools = ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=audit)
    write_proposer = RegisteredProjectWriteProposer(
        roots=roots,
        policy=policy,
        approvals=approvals,
        integrity=audit,
        backup_dir=tmp_path / "backups",
    )
    runtime = SessionTaskRuntime(
        sessions,
        policy,
        ModelGateway(ProposalProvider(), ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=tools,
        plans=plans,
        write_proposals=write_proposer,
    )

    async def immediate_wait(awaitable, timeout=None):
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", immediate_wait)
    session = runtime.create_session(title="recovery", project_id="demo")
    task = runtime.create_task(session.session_id, GOAL)
    outcome = asyncio.run(runtime.run_plan(task.task_id))
    assert outcome.task.state == "blocked"
    assert outcome.approval_id is not None
    return _ApprovalFx(
        root,
        target,
        database,
        sessions,
        approvals,
        plans,
        audit,
        runtime,
        task.task_id,
        outcome.approval_id,
    )


def _gateway(fx: _ApprovalFx) -> ExecutionGateway:
    return ExecutionGateway(
        project_roots={"demo": fx.root},
        trusted_projects=frozenset(),
        backup_dir=fx.database.path.parent / "backups2",
        policy=PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(fx.database)),
        approvals=fx.approvals,
        integrity=fx.audit,
    )


def test_consumed_approval_without_execution_evidence_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _approval_fixture(tmp_path, monkeypatch)
    fx.approvals.decide(fx.approval_id, grant=True, confirmed_digest=_digest(fx))

    def no_evidence(*args: object, **kwargs: object) -> object:
        raise AuditIntegrityError("execution evidence write failed")

    monkeypatch.setattr(fx.approvals, "record_execution", no_evidence)
    with pytest.raises(AuditIntegrityError):
        _gateway(fx).execute_write(fx.approval_id, confirmed_digest=_digest(fx))
    # The file side effect happened; no successful execution row exists.
    assert fx.target.read_text(encoding="utf-8") == NEW_CONTENT
    assert fx.approvals.successful_execution(fx.approval_id) is None

    failed = fx.sessions.fail_task_after_approval_without_evidence(
        fx.task_id, fx.approval_id, actor="test"
    )
    assert failed.state == "failed"
    assert failed.stop_reason == "execution_unverified"
    plan = fx.plans.get_plan_for_task(fx.task_id)
    assert (
        plan is not None and plan.state == "failed" and plan.stop_reason == "execution_unverified"
    )
    steps = fx.plans.list_steps(plan.plan_id)
    write_step = next(item for item in steps if item.tool == WRITE_PROPOSAL_TOOL)
    assert write_step.status == "failed" and write_step.error_code == "execution_unverified"
    # Repeated recovery is a no-op and never rewrites the file.
    again = fx.sessions.fail_task_after_approval_without_evidence(
        fx.task_id, fx.approval_id, actor="test"
    )
    assert again == failed
    assert fx.target.read_text(encoding="utf-8") == NEW_CONTENT
    assert (
        fx.database.connect()
        .execute("SELECT COUNT(*) FROM execution_runs WHERE approval_id = ?", (fx.approval_id,))
        .fetchone()[0]
        == 0
    )
    payloads = [
        row[0] for row in fx.database.connect().execute("SELECT payload_json FROM audit_events")
    ]
    assert all(NEW_CONTENT not in payload for payload in payloads)


def _digest(fx: _ApprovalFx) -> str:
    record = fx.approvals.get(fx.approval_id)
    assert record is not None
    return record.request_digest
