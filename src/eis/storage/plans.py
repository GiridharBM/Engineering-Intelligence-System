# SPDX-License-Identifier: Apache-2.0

"""SQLite-backed task plans and ordered steps with guarded transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Sequence
from typing import cast

from eis.core.model_plan import MAX_PLAN_STEPS, WRITE_PROPOSAL_TOOL, PlanStep
from eis.core.runtime import (
    PlanRecord,
    PlanState,
    PlanStepRecord,
    PlanStepStatus,
    RuntimeStateError,
)
from eis.policy import Resource
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase

_PLAN_TRANSITIONS: dict[PlanState, frozenset[PlanState]] = {
    "created": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"running", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_STEP_TRANSITIONS: dict[PlanStepStatus, frozenset[PlanStepStatus]] = {
    "pending": frozenset({"running", "blocked", "failed", "skipped"}),
    "running": frozenset({"blocked", "completed", "failed", "pending", "skipped"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset({"completed", "failed", "skipped"}),
    "skipped": frozenset(),
}


class PlanStore:
    """Persist plan state and content-free audit events in one transaction."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._chain = AuditChainStore(database)

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
    ) -> PlanRecord:
        if project_id is not None:
            Resource("project", identifier=project_id)
        if (
            not 1 <= len(steps) <= MAX_PLAN_STEPS
            or not _is_hash(policy_hash)
            or not _is_hash(plan_hash)
        ):
            raise RuntimeStateError("invalid plan record")
        plan_id = f"pln_{uuid.uuid4().hex}"
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute(
                "SELECT state FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task_row is None:
                raise RuntimeStateError("task not found")
            if task_row["state"] != "running":
                raise RuntimeStateError("plan requires a running task")
            connection.execute(
                """
                INSERT INTO task_plans
                    (id, task_id, project_id, state, policy_version, policy_hash,
                     plan_hash, stop_reason, created_at, updated_at)
                VALUES (?, ?, ?, 'created', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    plan_id,
                    task_id,
                    project_id,
                    policy_version,
                    policy_hash,
                    plan_hash,
                    now,
                    now,
                ),
            )
            self._chain.append_in(
                connection,
                event_type="plan_created",
                actor=actor,
                subject_type="task_plan",
                subject_id=plan_id,
                decision="create",
                occurred_at_ms=now,
                payload={
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "project_id": project_id,
                    "policy_version": policy_version,
                    "policy_hash": policy_hash,
                    "plan_hash": plan_hash,
                    "step_count": len(steps),
                    "state": "created",
                },
            )
            for index, step in enumerate(steps):
                step_id = f"pst_{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO task_plan_steps
                        (id, plan_id, step_index, step_ref, tool, arguments_json,
                         depends_on_json, status, policy_decision_id, output_sha256, error_code,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        step_id,
                        plan_id,
                        index,
                        step.id,
                        step.tool,
                        _canonical(step.arguments),
                        _canonical(list(step.depends_on)),
                        now,
                        now,
                    ),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_step_created",
                    actor=actor,
                    subject_type="task_plan_step",
                    subject_id=step_id,
                    decision="create",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan_id,
                        "step_id": step_id,
                        "step_index": index,
                        "tool": step.tool,
                        "step_sha256": _step_hash(step),
                        "status": "pending",
                    },
                )
        return self._plan(plan_id)

    def get_plan(self, plan_id: str) -> PlanRecord | None:
        with self._database.session() as connection:
            row = connection.execute("SELECT * FROM task_plans WHERE id = ?", (plan_id,)).fetchone()
        return _plan_from_row(row) if row is not None else None

    def get_plan_for_task(self, task_id: str) -> PlanRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM task_plans WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _plan_from_row(row) if row is not None else None

    def list_steps(self, plan_id: str) -> tuple[PlanStepRecord, ...]:
        with self._database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM task_plan_steps WHERE plan_id = ? ORDER BY step_index", (plan_id,)
            ).fetchall()
        return tuple(_step_from_row(row) for row in rows)

    def transition_plan(
        self,
        plan_id: str,
        *,
        expected_state: PlanState,
        new_state: PlanState,
        stop_reason: str | None = None,
        actor: str = "runtime",
    ) -> PlanRecord:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM task_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise RuntimeStateError("plan not found")
            current = _plan_from_row(row)
            if current.state != expected_state:
                raise RuntimeStateError(f"plan state is {current.state}, expected {expected_state}")
            if new_state not in _PLAN_TRANSITIONS[current.state]:
                raise RuntimeStateError(f"invalid plan transition: {current.state} -> {new_state}")
            if current.state == "blocked" and new_state == "completed":
                completed_write = connection.execute(
                    """
                    SELECT 1 FROM task_plan_steps s
                    JOIN approvals a ON a.task_id = (
                        SELECT p.task_id FROM task_plans p WHERE p.id = s.plan_id)
                    JOIN execution_runs r ON r.approval_id = a.id
                    WHERE s.plan_id = ? AND s.tool = ? AND s.status = 'completed'
                      AND a.status = 'consumed' AND r.status = 'success'
                      AND NOT EXISTS (
                          SELECT 1 FROM task_plan_steps other
                          WHERE other.plan_id = s.plan_id AND other.status <> 'completed')
                    LIMIT 1
                    """,
                    (plan_id, WRITE_PROPOSAL_TOOL),
                ).fetchone()
                if completed_write is None:
                    raise RuntimeStateError("plan has no completed approved write evidence")
            self._set_plan_state(connection, current, new_state, stop_reason, now, actor)
        return self._plan(plan_id)

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
    ) -> PlanStepRecord:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM task_plan_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if row is None:
                raise RuntimeStateError("plan step not found")
            current = _step_from_row(row)
            if current.status != expected_status:
                raise RuntimeStateError(
                    f"step status is {current.status}, expected {expected_status}"
                )
            if new_status not in _STEP_TRANSITIONS[current.status]:
                raise RuntimeStateError(
                    f"invalid step transition: {current.status} -> {new_status}"
                )
            if new_status == "pending" and error_code != "interrupted_retry":
                raise RuntimeStateError("only interrupted steps can be requeued")
            if (
                current.status == "running"
                and new_status == "blocked"
                and not (current.tool == WRITE_PROPOSAL_TOOL and error_code == "approval_required")
            ):
                raise RuntimeStateError("only a write proposal step can block for approval")
            if current.status == "blocked" and new_status == "completed":
                if current.tool == "human_review":
                    # Human review is not a write; a validated human continuation clears it.
                    if current.error_code != "human_review_required":
                        raise RuntimeStateError("human-review step is not awaiting review")
                else:
                    approved_write = connection.execute(
                        """
                        SELECT 1 FROM task_plans p
                        JOIN approvals a ON a.task_id = p.task_id
                        JOIN execution_runs r ON r.approval_id = a.id
                        WHERE p.id = ? AND a.status = 'consumed' AND r.status = 'success'
                        LIMIT 1
                        """,
                        (current.plan_id,),
                    ).fetchone()
                    if (
                        current.tool != WRITE_PROPOSAL_TOOL
                        or current.error_code != "approval_required"
                        or approved_write is None
                    ):
                        raise RuntimeStateError("blocked step lacks successful approval evidence")
            if new_status == "running" and current.attempt_count >= 2:
                raise RuntimeStateError("plan step retry limit exceeded")
            self._set_step_state(
                connection,
                current,
                new_status,
                policy_decision_id,
                output_sha256,
                error_code,
                now,
                actor,
            )
        return self._step(step_id)

    def recover_interrupted_steps(
        self, plan_id: str, *, actor: str = "runtime"
    ) -> tuple[PlanStepRecord, ...]:
        """Requeue only in-flight read-only steps, with at most one retry."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            plan_row = connection.execute(
                "SELECT state FROM task_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if plan_row is None or plan_row["state"] != "running":
                raise RuntimeStateError("only a running plan can be resumed")
            rows = connection.execute(
                "SELECT * FROM task_plan_steps WHERE plan_id = ? AND status = 'running' "
                "ORDER BY step_index",
                (plan_id,),
            ).fetchall()
            for row in rows:
                step = _step_from_row(row)
                if step.attempt_count >= 2:
                    raise RuntimeStateError("plan step retry limit exceeded")
                self._set_step_state(
                    connection,
                    step,
                    "pending",
                    None,
                    None,
                    "interrupted_retry",
                    now,
                    actor,
                )
        return tuple(self.list_steps(plan_id))

    def interrupt_plan(self, plan_id: str, *, actor: str = "runtime") -> PlanRecord:
        """Fail running steps and the plan in one transaction after a restart."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM task_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise RuntimeStateError("plan not found")
            plan = _plan_from_row(row)
            if plan.state != "running":
                raise RuntimeStateError("only a running plan can be interrupted")
            steps = [
                _step_from_row(item)
                for item in connection.execute(
                    "SELECT * FROM task_plan_steps WHERE plan_id = ? ORDER BY step_index",
                    (plan_id,),
                ).fetchall()
            ]
            for step in steps:
                if step.status == "running":
                    self._set_step_state(
                        connection, step, "failed", None, None, "interrupted", now, actor
                    )
                elif step.status == "pending":
                    self._set_step_state(
                        connection, step, "skipped", None, None, "interrupted", now, actor
                    )
            self._set_plan_state(connection, plan, "failed", "interrupted", now, actor)
        return self._plan(plan_id)

    def _set_plan_state(
        self,
        connection: sqlite3.Connection,
        current: PlanRecord,
        new_state: PlanState,
        stop_reason: str | None,
        now: int,
        actor: str,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE task_plans SET state = ?, stop_reason = ?, updated_at = ?
            WHERE id = ? AND state = ?
            """,
            (new_state, stop_reason, now, current.plan_id, current.state),
        )
        if cursor.rowcount != 1:
            raise RuntimeStateError("plan state changed; reload and retry")
        self._chain.append_in(
            connection,
            event_type="plan_state_changed",
            actor=actor,
            subject_type="task_plan",
            subject_id=current.plan_id,
            decision=new_state,
            occurred_at_ms=now,
            payload={
                "plan_id": current.plan_id,
                "task_id": current.task_id,
                "previous_state": current.state,
                "state": new_state,
                "stop_reason": stop_reason,
            },
        )

    def _set_step_state(
        self,
        connection: sqlite3.Connection,
        current: PlanStepRecord,
        new_status: PlanStepStatus,
        policy_decision_id: str | None,
        output_sha256: str | None,
        error_code: str | None,
        now: int,
        actor: str,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE task_plan_steps
            SET status = ?,
                attempt_count = attempt_count + CASE
                    WHEN ? = 'running' AND ? = 'pending' THEN 1 ELSE 0 END,
                policy_decision_id = COALESCE(?, policy_decision_id),
                output_sha256 = COALESCE(?, output_sha256),
                error_code = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                new_status,
                new_status,
                current.status,
                policy_decision_id,
                output_sha256,
                error_code,
                now,
                current.step_id,
                current.status,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeStateError("step status changed; reload and retry")
        self._chain.append_in(
            connection,
            event_type="plan_step_state_changed",
            actor=actor,
            subject_type="task_plan_step",
            subject_id=current.step_id,
            decision=new_status,
            occurred_at_ms=now,
            payload={
                "plan_id": current.plan_id,
                "step_id": current.step_id,
                "step_index": current.step_index,
                "previous_status": current.status,
                "status": new_status,
                "tool": current.tool,
                "policy_decision_id": policy_decision_id,
                "output_sha256": output_sha256,
                "error_code": error_code,
            },
        )

    def _plan(self, plan_id: str) -> PlanRecord:
        record = self.get_plan(plan_id)
        if record is None:
            raise RuntimeStateError("plan not found after write")
        return record

    def _step(self, step_id: str) -> PlanStepRecord:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM task_plan_steps WHERE id = ?", (step_id,)
            ).fetchone()
        if row is None:
            raise RuntimeStateError("step not found after write")
        return _step_from_row(row)


def _plan_from_row(row: sqlite3.Row) -> PlanRecord:
    return PlanRecord(
        plan_id=row["id"],
        task_id=row["task_id"],
        project_id=row["project_id"],
        state=cast(PlanState, row["state"]),
        policy_version=row["policy_version"],
        policy_hash=row["policy_hash"],
        plan_hash=row["plan_hash"],
        stop_reason=row["stop_reason"],
        created_at_ms=row["created_at"],
        updated_at_ms=row["updated_at"],
    )


def _step_from_row(row: sqlite3.Row) -> PlanStepRecord:
    arguments_raw = row["arguments_json"]
    return PlanStepRecord(
        step_id=row["id"],
        plan_id=row["plan_id"],
        step_index=row["step_index"],
        step_ref=row["step_ref"],
        tool=row["tool"],
        arguments=(
            cast(dict[str, object], json.loads(arguments_raw))
            if arguments_raw is not None
            else None
        ),
        depends_on=tuple(cast(list[str], json.loads(row["depends_on_json"]))),
        status=cast(PlanStepStatus, row["status"]),
        attempt_count=row["attempt_count"],
        policy_decision_id=row["policy_decision_id"],
        output_sha256=row["output_sha256"],
        error_code=row["error_code"],
        created_at_ms=row["created_at"],
        updated_at_ms=row["updated_at"],
    )


def _step_hash(step: PlanStep) -> str:
    return _sha256(
        _canonical(
            {
                "id": step.id,
                "tool": step.tool,
                "arguments": step.arguments,
                "depends_on": list(step.depends_on),
            }
        )
    )


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
