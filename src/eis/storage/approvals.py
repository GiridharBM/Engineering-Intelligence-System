# SPDX-License-Identifier: Apache-2.0

"""Durable approval lifecycle with hash-bound, single-use consumption."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from eis.core.approvals import (
    ApprovalError,
    ApprovalRecord,
    ApprovalStatus,
    approval_digest,
    content_hash,
)
from eis.policy import PolicyDecision
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase


@dataclass(frozen=True)
class ExecutionRun:
    run_id: str
    exit_code: int | None
    backup_id: str | None


class ApprovalStore:
    """Persist private approval material and chain only content-free metadata."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._chain = AuditChainStore(database)

    def request_write(
        self,
        *,
        decision: PolicyDecision,
        task_id: str | None = None,
        on_behalf_of: str,
        resource: str,
        project_root_hash: str,
        expected_sha256: str | None,
        new_sha256: str,
        diff_text: str,
        new_content: str,
        summary: str,
        ttl_seconds: int = 1800,
    ) -> ApprovalRecord:
        if decision.effect != "allow" or decision.principal.canonical != "user":
            raise ApprovalError("an allowed user policy decision is required")
        if decision.action.name != "project.write" or decision.resource.canonical != resource:
            raise ApprovalError("policy decision does not match the requested write")
        if not on_behalf_of.startswith("agent:") or not on_behalf_of[6:]:
            raise ApprovalError("delegated write requires an agent principal")
        if len(new_sha256) != 64 or (expected_sha256 is not None and len(expected_sha256) != 64):
            raise ApprovalError("invalid content digest")
        if len(project_root_hash) != 64:
            raise ApprovalError("invalid project-root binding")
        if not 1 <= ttl_seconds <= 1800:
            raise ApprovalError("approval expiry must be between 1 and 1800 seconds")
        if not summary.strip() or len(summary) > 500:
            raise ApprovalError("summary must contain 1 to 500 characters")
        if (
            len(diff_text.encode("utf-8")) > 2_000_000
            or len(new_content.encode("utf-8")) > 1_000_000
        ):
            raise ApprovalError("proposed write exceeds size limits")
        actual_new_hash = content_hash(new_content.encode("utf-8"))
        if not hmac.compare_digest(actual_new_hash, new_sha256):
            raise ApprovalError("proposed content digest does not match")

        now_ms = _now_ms()
        expires_at_ms = now_ms + ttl_seconds * 1000
        approval_id = f"apr_{uuid.uuid4().hex}"
        diff_sha256 = content_hash(diff_text.encode("utf-8"))
        digest = approval_digest(
            on_behalf_of=on_behalf_of,
            resource=resource,
            project_root_hash=project_root_hash,
            expected_sha256=expected_sha256,
            new_sha256=new_sha256,
            policy_decision_id=decision.decision_id,
            policy_version=decision.policy_version,
            expires_at_ms=expires_at_ms,
            summary=summary.strip(),
            diff_text=diff_text,
            task_id=task_id,
        )
        values = (
            approval_id,
            task_id,
            on_behalf_of,
            resource,
            project_root_hash,
            decision.decision_id,
            decision.policy_version,
            expected_sha256,
            new_sha256,
            diff_sha256,
            diff_text,
            new_content,
            summary.strip(),
            digest,
            now_ms,
            expires_at_ms,
        )
        with self._database.session(immediate=True) as connection:
            if task_id is not None:
                task = connection.execute(
                    "SELECT t.state, t.principal, s.project_id FROM tasks t "
                    "JOIN sessions s ON s.id = t.session_id WHERE t.id = ?",
                    (task_id,),
                ).fetchone()
                resource_project = resource.partition(":")[2].partition("/")[0]
                if (
                    task is None
                    or task["state"] != "running"
                    or task["principal"] != on_behalf_of
                    or task["project_id"] != resource_project
                ):
                    raise ApprovalError("task binding does not match the write proposal")
            connection.execute(
                """INSERT INTO approvals
                   (id, task_id, action, on_behalf_of, resource, project_root_hash,
                    policy_decision_id, policy_version, expected_sha256, new_sha256,
                    diff_sha256, diff_text, new_content, summary, risk_class,
                    request_digest, status, requested_at, expires_at)
                   VALUES (?, ?, 'project.write', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'A3', ?, 'pending', ?, ?)""",
                values,
            )
            self._chain.append_in(
                connection,
                event_type="approval_requested",
                actor=on_behalf_of,
                subject_type="approval",
                subject_id=approval_id,
                decision="pending",
                occurred_at_ms=now_ms,
                payload={
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "action": "project.write",
                    "resource": resource,
                    "project_root_hash": project_root_hash,
                    "policy_decision_id": decision.decision_id,
                    "risk_class": "A3",
                    "request_digest": digest,
                    "diff_sha256": diff_sha256,
                    "summary_sha256": content_hash(summary.strip().encode("utf-8")),
                    "expires_at_ms": expires_at_ms,
                },
            )
        return self.get(approval_id)  # type: ignore[return-value]

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return _from_row(row) if row is not None else None

    def get_for_task(self, task_id: str) -> ApprovalRecord | None:
        """Return the unique approval bound to a task, if one exists."""
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _from_row(row) if row is not None else None

    def list_pending(self, *, limit: int = 100) -> tuple[ApprovalRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        with self._database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE status = 'pending' AND expires_at > ? "
                "ORDER BY requested_at LIMIT ?",
                (_now_ms(), limit),
            ).fetchall()
        return tuple(_from_row(row) for row in rows)

    def decide(
        self,
        approval_id: str,
        *,
        grant: bool,
        confirmed_digest: str,
        reason: str = "",
    ) -> ApprovalRecord:
        if len(reason) > 500:
            raise ApprovalError("decision reason is too long")
        now_ms = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError("approval not found")
            record = _from_row(row)
            if record.status != "pending":
                raise ApprovalError("approval is not pending")
            if record.expires_at_ms <= now_ms:
                raise ApprovalError("approval has expired")
            if not hmac.compare_digest(record.request_digest, confirmed_digest):
                raise ApprovalError("approval digest does not match")
            status: ApprovalStatus = "approved" if grant else "rejected"
            note = None if grant else (reason.strip() or None)
            cursor = connection.execute(
                "UPDATE approvals SET status = ?, decided_at = ?, decision_note = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, now_ms, note, approval_id),
            )
            if cursor.rowcount != 1:
                raise ApprovalError("approval state changed; reload and review it")
            self._chain.append_in(
                connection,
                event_type="approval_granted" if grant else "approval_rejected",
                actor="user",
                subject_type="approval",
                subject_id=approval_id,
                decision=status,
                occurred_at_ms=now_ms,
                payload={
                    "approval_id": approval_id,
                    "request_digest": record.request_digest,
                    "risk_class": record.risk_class,
                    "reason_sha256": (
                        hashlib.sha256(note.encode("utf-8")).hexdigest() if note else None
                    ),
                },
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return _from_row(row)

    def consume(self, approval_id: str, *, confirmed_digest: str) -> ApprovalRecord:
        now_ms = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError("approval not found")
            record = _from_row(row)
            if record.status != "approved":
                raise ApprovalError("approval is not approved or has already been used")
            if record.expires_at_ms <= now_ms:
                raise ApprovalError("approval has expired")
            expected = approval_digest(
                on_behalf_of=record.on_behalf_of,
                resource=record.resource,
                project_root_hash=record.project_root_hash,
                expected_sha256=record.expected_sha256,
                new_sha256=record.new_sha256,
                policy_decision_id=record.policy_decision_id,
                policy_version=record.policy_version,
                expires_at_ms=record.expires_at_ms,
                summary=record.summary,
                diff_text=record.diff_text,
                task_id=record.task_id,
            )
            if (
                not hmac.compare_digest(record.request_digest, expected)
                or not hmac.compare_digest(
                    content_hash(record.new_content.encode("utf-8")), record.new_sha256
                )
                or not hmac.compare_digest(
                    content_hash(record.diff_text.encode("utf-8")), record.diff_sha256
                )
            ):
                raise ApprovalError("stored approval binding is invalid")
            if not hmac.compare_digest(record.request_digest, confirmed_digest):
                raise ApprovalError("approval digest does not match")
            cursor = connection.execute(
                "UPDATE approvals SET status = 'consumed', consumed_at = ? "
                "WHERE id = ? AND status = 'approved'",
                (now_ms, approval_id),
            )
            if cursor.rowcount != 1:
                raise ApprovalError("approval was already consumed")
            self._chain.append_in(
                connection,
                event_type="approval_consumed",
                actor="user",
                subject_type="approval",
                subject_id=approval_id,
                decision="consume",
                occurred_at_ms=now_ms,
                payload={
                    "approval_id": approval_id,
                    "request_digest": record.request_digest,
                    "resource": record.resource,
                    "diff_sha256": record.diff_sha256,
                },
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return _from_row(row)

    def record_execution(
        self,
        *,
        run_id: str,
        approval_id: str | None,
        action: str,
        resource: str,
        profile_id: str | None,
        status: str,
        started_at_ms: int,
        completed_at_ms: int,
        exit_code: int | None = None,
        output_sha256: str | None = None,
        error_code: str | None = None,
        backup_id: str | None = None,
    ) -> None:
        event_id = f"execution_{run_id}"
        event_type = "execution_completed" if status == "success" else "execution_failed"
        with self._database.session(immediate=True) as connection:
            connection.execute(
                """INSERT INTO execution_runs
                   (id, approval_id, action, resource, profile_id, status, started_at,
                    completed_at, exit_code, output_sha256, error_code, backup_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    approval_id,
                    action,
                    resource,
                    profile_id,
                    status,
                    started_at_ms,
                    completed_at_ms,
                    exit_code,
                    output_sha256,
                    error_code,
                    backup_id,
                ),
            )
            self._chain.append_in(
                connection,
                event_id=event_id,
                event_type=event_type,
                actor="execution_gateway",
                subject_type="execution_run",
                subject_id=run_id,
                decision=status,
                occurred_at_ms=completed_at_ms,
                payload={
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "action": action,
                    "resource": resource,
                    "profile_id": profile_id,
                    "status": status,
                    "exit_code": exit_code,
                    "output_sha256": output_sha256,
                    "error_code": error_code,
                    "backup_id": backup_id,
                    "started_at_ms": started_at_ms,
                },
            )

    def task_is_terminal(self, task_id: str) -> bool:
        with self._database.session() as connection:
            row = connection.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row is None or row["state"] in {"completed", "failed", "cancelled"}

    def successful_execution(self, approval_id: str) -> ExecutionRun | None:
        """Durable successful execution evidence for retry recovery."""
        with self._database.session() as connection:
            row = connection.execute(
                """
                SELECT id, exit_code, backup_id FROM execution_runs
                WHERE approval_id = ? AND status = 'success'
                ORDER BY completed_at DESC LIMIT 1
                """,
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        return ExecutionRun(
            run_id=row["id"], exit_code=row["exit_code"], backup_id=row["backup_id"]
        )

    def record_execution_start(
        self,
        *,
        run_id: str,
        approval_id: str | None,
        action: str,
        resource: str,
        profile_id: str | None,
        started_at_ms: int,
    ) -> None:
        with self._database.session(immediate=True) as connection:
            self._chain.append_in(
                connection,
                event_id=f"execution_start_{run_id}",
                event_type="execution_started",
                actor="execution_gateway",
                subject_type="execution_run",
                subject_id=run_id,
                decision="start",
                occurred_at_ms=started_at_ms,
                payload={
                    "run_id": run_id,
                    "approval_id": approval_id,
                    "action": action,
                    "resource": resource,
                    "profile_id": profile_id,
                },
            )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _from_row(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["id"],
        task_id=row["task_id"],
        action=row["action"],
        on_behalf_of=row["on_behalf_of"],
        resource=row["resource"],
        project_root_hash=row["project_root_hash"],
        policy_decision_id=row["policy_decision_id"],
        policy_version=row["policy_version"],
        expected_sha256=row["expected_sha256"],
        new_sha256=row["new_sha256"],
        diff_sha256=row["diff_sha256"],
        diff_text=row["diff_text"],
        new_content=row["new_content"],
        summary=row["summary"],
        risk_class=row["risk_class"],
        request_digest=row["request_digest"],
        status=cast(ApprovalStatus, row["status"]),
        requested_at_ms=row["requested_at"],
        expires_at_ms=row["expires_at"],
        decided_at_ms=row["decided_at"],
        consumed_at_ms=row["consumed_at"],
        decision_note=row["decision_note"],
    )
