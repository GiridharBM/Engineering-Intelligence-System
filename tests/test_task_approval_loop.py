# SPDX-License-Identifier: Apache-2.0

"""Task proposal → human approval → write execution acceptance path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eis.adapters.models.base import CompletionRequest, CompletionResponse, TokenUsage
from eis.config import ConsentRecord
from eis.core.consent import ConsentGate
from eis.core.execution import ExecutionGateway
from eis.core.model_envelope import ModelEnvelopeError, WriteProposalEnvelope, parse_model_response
from eis.core.model_gateway import ModelGateway
from eis.core.model_plan import ModelPlanError, parse_plan_response
from eis.core.policy_service import PolicyService
from eis.core.project_roots import root_status
from eis.core.project_tools import ReadOnlyProjectTools
from eis.core.runtime import RuntimeStateError, SessionTaskRuntime
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

CONTENT_MARKER = "new-approved-content-marker"
GOAL_MARKER = "change this project file"


def test_write_proposal_envelope_is_strict_and_relative() -> None:
    valid = json.dumps(
        {
            "kind": "write_proposal",
            "path": "src/module.py",
            "content": "safe code",
            "summary": "Update module",
        }
    )
    envelope = parse_model_response(valid)
    assert isinstance(envelope, WriteProposalEnvelope)
    assert envelope.path == "src/module.py"
    drive_path = "C" + ":/outside.py"
    for path in ("../outside.py", "/absolute.py", drive_path, "src\\module.py"):
        unsafe = json.dumps(
            {
                "kind": "write_proposal",
                "path": path,
                "content": "safe code",
                "summary": "Update module",
            }
        )
        with pytest.raises(ModelEnvelopeError, match="invalid_write_path"):
            parse_model_response(unsafe)
    with pytest.raises(ModelEnvelopeError, match="unexpected_field"):
        parse_model_response(valid[:-1] + ',"project_id":"outside"}')


def test_write_proposal_is_a_final_plan_step() -> None:
    proposal = {
        "id": "propose",
        "tool": "project.propose_write",
        "arguments": {"path": "app.py", "content": "new", "summary": "update"},
        "depends_on": [],
    }
    read = {
        "id": "read",
        "tool": "project.read_text",
        "arguments": {"path": "app.py"},
        "depends_on": [],
    }
    accepted = parse_plan_response(json.dumps({"kind": "plan", "steps": [read, proposal]}))
    assert [item.tool for item in accepted.steps] == ["project.read_text", "project.propose_write"]
    with pytest.raises(ModelPlanError, match="write_proposal_must_be_final"):
        parse_plan_response(json.dumps({"kind": "plan", "steps": [proposal, read]}))


class ProposalProvider:
    name = "openai"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        self.calls += 1
        text = json.dumps(
            {
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
                        "tool": "project.propose_write",
                        "arguments": {
                            "path": "app.py",
                            "content": CONTENT_MARKER,
                            "summary": "Apply the requested project update",
                        },
                        "depends_on": ["read"],
                    },
                ],
            },
            separators=(",", ":"),
        )
        return CompletionResponse(
            text=text,
            provider=self.name,
            model=self.model,
            finish_reason="stop",
            usage=TokenUsage(1, 2, 3),
        )


def _policy() -> Policy:
    rules = [
        ("agent:eis.primary", "task.run", "project:demo"),
        ("agent:eis.primary", "project.read", "path:demo/app.py"),
        ("agent:eis.primary", "project.propose", "project:demo"),
        ("user", "project.read", "path:demo/app.py"),
        ("user", "project.write", "path:demo/app.py"),
    ]
    return Policy.from_dict(
        {
            "version": "task-approval-test-v1",
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


def _run_immediate(coroutine):
    """Drive test coroutines that must complete without scheduling or I/O waits."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("test coroutine unexpectedly suspended")


@pytest.mark.parametrize("interrupt_before_block", [False, True])
def test_fake_provider_read_propose_grant_execute_and_complete(
    tmp_path: Path, monkeypatch, interrupt_before_block: bool
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("old-content\n", encoding="utf-8")
    database = SqliteDatabase(tmp_path / "eis.sqlite3")
    sessions = SessionTaskStore(database)
    roots = ProjectRootStore(database)
    root_record = roots.register("demo", root, actor="test")
    assert root_status(root_record) == "available"
    policy = PolicyService(PolicyEngine(_policy()), SqlitePolicyDecisionSink(database))
    audit = AuditChainStore(database)
    approvals = ApprovalStore(database)
    tools = ReadOnlyProjectTools(project_roots=roots, policy=policy, audit=audit)
    consent = ConsentStore(database)
    consent.grant(
        ConsentRecord(
            consent_id="cns_task_approval",
            provider="openai",
            data_classes=frozenset({"C0", "C1"}),  # type: ignore[arg-type]
            purpose="task approval acceptance test",
        )
    )
    provider = ProposalProvider()
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
        ModelGateway(provider, ConsentGate(lookup=consent), SqliteAuditSink(database)),
        project_tools=tools,
        plans=PlanStore(database),
        write_proposals=write_proposer,
    )
    session = runtime.create_session(title="Approval acceptance", project_id="demo")

    async def immediate_wait(awaitable, timeout=None):
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", immediate_wait)
    task = runtime.create_task(session.session_id, GOAL_MARKER)
    if interrupt_before_block:

        def simulate_crash(*args, **kwargs):
            raise RuntimeStateError("simulated process interruption")

        monkeypatch.setattr(sessions, "block_plan_task_for_approval", simulate_crash)
        with pytest.raises(RuntimeStateError, match="simulated process interruption"):
            _run_immediate(runtime.run_plan(task.task_id))
        approval = approvals.get_for_task(task.task_id)
        assert approval is not None
        assert sessions.get_task(task.task_id).state == "running"  # type: ignore[union-attr]
        interrupted_plan = PlanStore(database).get_plan_for_task(task.task_id)
        assert interrupted_plan is not None and interrupted_plan.state == "running"
    else:
        outcome = _run_immediate(runtime.run_plan(task.task_id))
        assert outcome.task.state == "blocked"
        assert outcome.task.stop_reason == "approval_required"
        assert outcome.plan is not None and outcome.plan.state == "blocked"
        assert outcome.approval_id is not None
        assert outcome.approval_digest is not None
        approval = approvals.get(outcome.approval_id)
        assert approval is not None
    assert provider.calls == 1
    assert approval.task_id == task.task_id
    assert approval.status == "pending"
    with database.connect() as connection:
        events = [
            row[0] for row in connection.execute("SELECT event_type FROM audit_events ORDER BY seq")
        ]
    assert events.index("tool_invocation_completed") < events.index("approval_requested")

    policy_model = PolicyEngine(_policy())
    execution_policy = PolicyService(policy_model, SqlitePolicyDecisionSink(database))
    execution = ExecutionGateway(
        project_roots={"demo": root},
        trusted_projects=frozenset(),
        backup_dir=tmp_path / "backups",
        policy=execution_policy,
        approvals=approvals,
        integrity=audit,
    )
    execution.grant(approval.approval_id, confirmed_digest=approval.request_digest)
    result = execution.execute_write(approval.approval_id, confirmed_digest=approval.request_digest)
    completed = sessions.complete_task_after_approval(task.task_id, approval.approval_id)
    completed_plan = PlanStore(database).get_plan_for_task(task.task_id)

    assert result.status == "success"
    assert completed.state == "completed"
    assert completed.stop_reason == "approved_write_completed"
    assert completed_plan is not None and completed_plan.state == "completed"
    assert target.read_text(encoding="utf-8") == CONTENT_MARKER
    with database.connect() as connection:
        payloads = [row[0] for row in connection.execute("SELECT payload_json FROM audit_events")]
    assert all(CONTENT_MARKER not in payload for payload in payloads)
    assert audit.status().ok
