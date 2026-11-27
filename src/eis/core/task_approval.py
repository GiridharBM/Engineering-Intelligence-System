# SPDX-License-Identifier: Apache-2.0

"""Task-bound bridge from an untrusted write proposal to human approval."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eis.core.approvals import ApprovalError, content_hash
from eis.core.audit_chain import AuditIntegrityGate
from eis.core.execution import ExecutionDenied, ExecutionGateway
from eis.core.policy_service import PolicyService
from eis.core.project_roots import root_status
from eis.core.runtime import WriteApprovalRequest
from eis.policy import Resource
from eis.storage.approvals import ApprovalStore
from eis.storage.project_roots import ProjectRootStore


class RegisteredProjectWriteProposer:
    """Resolve project authority from the durable registry and request approval."""

    def __init__(
        self,
        *,
        roots: ProjectRootStore,
        policy: PolicyService,
        approvals: ApprovalStore,
        integrity: AuditIntegrityGate,
        backup_dir: Path,
    ) -> None:
        self._roots = roots
        self._policy = policy
        self._approvals = approvals
        self._integrity = integrity
        self._backup_dir = backup_dir

    def propose_write(
        self,
        *,
        project_id: str,
        task_id: str,
        relative_path: str,
        new_content: str,
        on_behalf_of: str,
        summary: str,
    ) -> WriteApprovalRequest:
        record = self._roots.get(project_id)
        if record is None or root_status(record) != "available":
            raise ExecutionDenied("registered project root is unavailable")
        existing = self._approvals.get_for_task(task_id)
        if existing is not None:
            expected_resource = Resource.for_path(project_id, relative_path).canonical
            expected_hash = content_hash(new_content.encode("utf-8"))
            if (
                existing.resource != expected_resource
                or existing.new_sha256 != expected_hash
                or existing.summary != summary.strip()
                or existing.on_behalf_of != on_behalf_of
                or existing.status == "rejected"
                or (existing.status != "consumed" and existing.expires_at_ms <= _now_ms())
            ):
                raise ApprovalError("existing task approval does not match this proposal")
            return WriteApprovalRequest(existing.approval_id, existing.request_digest)
        gateway = ExecutionGateway(
            project_roots={project_id: Path(record.root_path)},
            trusted_projects=frozenset(),
            backup_dir=self._backup_dir,
            policy=self._policy,
            approvals=self._approvals,
            integrity=self._integrity,
        )
        approval = gateway.propose_write(
            project_id=project_id,
            task_id=task_id,
            relative_path=relative_path,
            new_content=new_content,
            on_behalf_of=on_behalf_of,
            summary=summary,
        )
        return WriteApprovalRequest(approval.approval_id, approval.request_digest)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
