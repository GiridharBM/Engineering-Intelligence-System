# SPDX-License-Identifier: Apache-2.0

"""SQLite-backed sessions and tasks with chained state transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from typing import cast

from eis.core.runtime import (
    MAX_GOAL_CHARS,
    MAX_TITLE_CHARS,
    PlanRecord,
    PlanStepRecord,
    RuntimeStateError,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    TaskState,
)
from eis.policy import Resource
from eis.storage.audit_chain import AuditChainStore
from eis.storage.db import SqliteDatabase

_TERMINAL_TASK_STATES = frozenset({"blocked", "completed", "failed", "cancelled"})
_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    "created": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"running", "completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    "active": frozenset({"idle", "closed"}),
    "idle": frozenset({"active", "closed"}),
    "closed": frozenset(),
}


class SessionTaskStore:
    """Persist lifecycle rows and content-free audit events atomically."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._chain = AuditChainStore(database)

    @property
    def database(self) -> SqliteDatabase:
        return self._database

    def create_session(self, *, title: str, project_id: str | None, actor: str) -> SessionRecord:
        clean_title = title.strip()
        if not clean_title or len(clean_title) > MAX_TITLE_CHARS:
            raise RuntimeStateError("session title must contain 1 to 200 characters")
        if project_id is not None:
            Resource("project", identifier=project_id)
        session_id = f"ses_{uuid.uuid4().hex}"
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO sessions
                    (id, title, status, project_id, created_at, updated_at, closed_at)
                VALUES (?, ?, 'active', ?, ?, ?, NULL)
                """,
                (session_id, clean_title, project_id, now, now),
            )
            self._chain.append_in(
                connection,
                event_type="session_created",
                actor=actor,
                subject_type="session",
                subject_id=session_id,
                decision="create",
                occurred_at_ms=now,
                payload={
                    "session_id": session_id,
                    "project_id": project_id,
                    "title_sha256": _sha256(clean_title),
                    "status": "active",
                },
            )
        return self._session(session_id)

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def list_sessions(self, *, limit: int = 100) -> tuple[SessionRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        with self._database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, id LIMIT ?", (limit,)
            ).fetchall()
        return tuple(_session_from_row(row) for row in rows)

    def resume_session(self, session_id: str, *, actor: str) -> SessionRecord:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise RuntimeStateError("session not found")
            current = _session_from_row(row)
            if current.status == "closed":
                return current
            if current.status == "idle":
                connection.execute(
                    "UPDATE sessions SET status = 'active', updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                self._chain.append_in(
                    connection,
                    event_type="session_state_changed",
                    actor=actor,
                    subject_type="session",
                    subject_id=session_id,
                    decision="active",
                    occurred_at_ms=now,
                    payload={
                        "session_id": session_id,
                        "previous_state": "idle",
                        "state": "active",
                        "project_id": current.project_id,
                    },
                )
        return self._session(session_id)

    def close_session(self, session_id: str, *, actor: str) -> SessionRecord:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise RuntimeStateError("session not found")
            current = _session_from_row(row)
            if current.status == "closed":
                return current
            connection.execute(
                "UPDATE sessions SET status = 'closed', updated_at = ?, closed_at = ? WHERE id = ?",
                (now, now, session_id),
            )
            self._chain.append_in(
                connection,
                event_type="session_state_changed",
                actor=actor,
                subject_type="session",
                subject_id=session_id,
                decision="closed",
                occurred_at_ms=now,
                payload={
                    "session_id": session_id,
                    "previous_state": current.status,
                    "state": "closed",
                    "project_id": current.project_id,
                },
            )
        return self._session(session_id)

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
    ) -> TaskRecord:
        clean_goal = goal.strip()
        if not clean_goal or len(clean_goal) > MAX_GOAL_CHARS or "\x00" in clean_goal:
            raise RuntimeStateError("task goal is invalid or exceeds the size limit")
        if _sha256(clean_goal) != goal_hash:
            raise RuntimeStateError("task goal digest does not match")
        task_id = f"tsk_{uuid.uuid4().hex}"
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            session_row = connection.execute(
                "SELECT status, project_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session_row is None:
                raise RuntimeStateError("session not found")
            if session_row["status"] == "closed":
                raise RuntimeStateError("session is closed")
            connection.execute(
                """
                INSERT INTO tasks
                    (id, session_id, state, principal, goal, goal_hash,
                     policy_decision_id, policy_version, runtime_version,
                     stop_reason, request_hash, response_hash, finish_reason,
                     usage_json, created_at, started_at, ended_at)
                VALUES (?, ?, 'created', ?, ?, ?, NULL, ?, ?, NULL, NULL, NULL,
                        NULL, NULL, ?, NULL, NULL)
                """,
                (
                    task_id,
                    session_id,
                    principal,
                    clean_goal,
                    goal_hash,
                    policy_version,
                    runtime_version,
                    now,
                ),
            )
            self._chain.append_in(
                connection,
                event_type="task_created",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision="create",
                occurred_at_ms=now,
                payload={
                    "task_id": task_id,
                    "session_id": session_id,
                    "project_id": session_row["project_id"],
                    "goal_sha256": goal_hash,
                    "principal": principal,
                    "policy_version": policy_version,
                    "runtime_version": runtime_version,
                    "state": "created",
                },
            )
        return self._task(task_id)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._database.session() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row is not None else None

    def list_tasks(self, session_id: str, *, limit: int = 100) -> tuple[TaskRecord, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        with self._database.session() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at DESC, id LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return tuple(_task_from_row(row) for row in rows)

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
    ) -> TaskRecord:
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise RuntimeStateError("task not found")
            current = _task_from_row(row)
            if current.state != expected_state:
                raise RuntimeStateError(f"task state is {current.state}, expected {expected_state}")
            if new_state not in _TASK_TRANSITIONS[current.state]:
                raise RuntimeStateError(f"invalid task transition: {current.state} -> {new_state}")

            started_at = current.started_at_ms
            if new_state == "running" and started_at is None:
                started_at = now
            ended_at = (
                now
                if new_state in _TERMINAL_TASK_STATES
                else None
                if new_state == "running"
                else current.ended_at_ms
            )
            usage_json = json.dumps(dict(usage), sort_keys=True) if usage is not None else None
            cursor = connection.execute(
                """
                UPDATE tasks
                SET state = ?, stop_reason = ?,
                    policy_decision_id = COALESCE(?, policy_decision_id),
                    policy_version = COALESCE(?, policy_version),
                    request_hash = COALESCE(?, request_hash),
                    response_hash = COALESCE(?, response_hash),
                    finish_reason = COALESCE(?, finish_reason),
                    usage_json = COALESCE(?, usage_json),
                    started_at = ?, ended_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    new_state,
                    stop_reason,
                    policy_decision_id,
                    policy_version,
                    request_hash,
                    response_hash,
                    finish_reason,
                    usage_json,
                    started_at,
                    ended_at,
                    task_id,
                    expected_state,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeStateError("task state changed; reload and retry")

            session_row = connection.execute(
                "SELECT status FROM sessions WHERE id = ?", (current.session_id,)
            ).fetchone()
            if session_row is None:
                raise RuntimeStateError("session not found")
            previous_session_status = cast(SessionStatus, session_row["status"])
            if (
                session_status is not None
                and session_status != previous_session_status
                and previous_session_status != "closed"
            ):
                if session_status not in _SESSION_TRANSITIONS[previous_session_status]:
                    raise RuntimeStateError(
                        f"invalid session transition: {previous_session_status} -> {session_status}"
                    )
                closed_at = now if session_status == "closed" else None
                connection.execute(
                    "UPDATE sessions SET status = ?, updated_at = ?, "
                    "closed_at = COALESCE(?, closed_at) WHERE id = ?",
                    (session_status, now, closed_at, current.session_id),
                )
                self._chain.append_in(
                    connection,
                    event_type="session_state_changed",
                    actor=actor,
                    subject_type="session",
                    subject_id=current.session_id,
                    decision=session_status,
                    occurred_at_ms=now,
                    payload={
                        "session_id": current.session_id,
                        "previous_state": previous_session_status,
                        "state": session_status,
                        "project_id": self._project_id(connection, current.session_id),
                    },
                )

            self._chain.append_in(
                connection,
                event_type="task_state_changed",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision=new_state,
                occurred_at_ms=now,
                payload={
                    "task_id": task_id,
                    "session_id": current.session_id,
                    "previous_state": current.state,
                    "state": new_state,
                    "stop_reason": stop_reason,
                    "policy_decision_id": policy_decision_id,
                    "request_hash": request_hash,
                    "response_hash": response_hash,
                    "finish_reason": finish_reason,
                    "usage": dict(usage) if usage is not None else None,
                },
            )
        return self._task(task_id)

    def block_task_for_approval(
        self,
        task_id: str,
        approval_id: str,
        *,
        request_hash: str | None = None,
        response_hash: str | None = None,
        usage: Mapping[str, int] | None = None,
        actor: str = "runtime",
    ) -> TaskRecord:
        """Atomically link a task to its durable pending approval state."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise RuntimeStateError("task not found")
            task = _task_from_row(row)
            if task.state != "running":
                raise RuntimeStateError("approval requires a running task")
            approval = connection.execute(
                "SELECT id, status FROM approvals WHERE id = ? AND task_id = ?",
                (approval_id, task_id),
            ).fetchone()
            if approval is None or approval["status"] not in {"pending", "approved"}:
                raise RuntimeStateError("task approval is unavailable")
            self._set_task_blocked_for_approval(
                connection, task, now, actor, request_hash, response_hash, usage
            )
        return self._task(task_id)

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
    ) -> tuple[TaskRecord, PlanRecord, tuple[PlanStepRecord, ...]]:
        """Block the task, plan and final write step in one audited transaction."""
        from eis.storage.plans import _plan_from_row, _step_from_row

        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            plan_row = connection.execute(
                "SELECT * FROM task_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            step_row = connection.execute(
                "SELECT * FROM task_plan_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if task_row is None or plan_row is None or step_row is None:
                raise RuntimeStateError("plan approval binding is unavailable")
            task = _task_from_row(task_row)
            plan = _plan_from_row(plan_row)
            step = _step_from_row(step_row)
            if (
                task.state != "running"
                or plan.task_id != task_id
                or plan.state != "running"
                or step.plan_id != plan_id
                or step.tool != "project.propose_write"
                or step.status != "pending"
            ):
                raise RuntimeStateError("plan is not ready for write approval")
            approval = connection.execute(
                "SELECT id, status FROM approvals WHERE id = ? AND task_id = ?",
                (approval_id, task_id),
            ).fetchone()
            if approval is None or approval["status"] not in {"pending", "approved"}:
                raise RuntimeStateError("task approval is unavailable")

            connection.execute(
                "UPDATE task_plan_steps SET status = 'blocked', error_code = 'approval_required', "
                "updated_at = ? WHERE id = ? AND status = 'pending'",
                (now, step_id),
            )
            self._chain.append_in(
                connection,
                event_type="plan_step_state_changed",
                actor=actor,
                subject_type="task_plan_step",
                subject_id=step_id,
                decision="blocked",
                occurred_at_ms=now,
                payload={
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "step_index": step.step_index,
                    "previous_status": "pending",
                    "status": "blocked",
                    "tool": step.tool,
                    "approval_id": approval_id,
                    "error_code": "approval_required",
                },
            )
            connection.execute(
                "UPDATE task_plans SET state = 'blocked', stop_reason = 'approval_required', "
                "updated_at = ? WHERE id = ? AND state = 'running'",
                (now, plan_id),
            )
            self._chain.append_in(
                connection,
                event_type="plan_state_changed",
                actor=actor,
                subject_type="task_plan",
                subject_id=plan_id,
                decision="blocked",
                occurred_at_ms=now,
                payload={
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "previous_state": "running",
                    "state": "blocked",
                    "stop_reason": "approval_required",
                },
            )
            self._set_task_blocked_for_approval(
                connection, task, now, actor, request_hash, response_hash, usage
            )
        from eis.storage.plans import PlanStore

        plan_record = PlanStore(self._database).get_plan(plan_id)
        if plan_record is None:
            raise RuntimeStateError("plan not found after approval block")
        steps = tuple(PlanStore(self._database).list_steps(plan_id))
        return self._task(task_id), plan_record, steps

    def _set_task_blocked_for_approval(
        self,
        connection: sqlite3.Connection,
        task: TaskRecord,
        now: int,
        actor: str,
        request_hash: str | None,
        response_hash: str | None,
        usage: Mapping[str, int] | None,
    ) -> None:
        usage_json = json.dumps(dict(usage), sort_keys=True) if usage is not None else None
        cursor = connection.execute(
            "UPDATE tasks SET state = 'blocked', stop_reason = 'approval_required', "
            "request_hash = COALESCE(?, request_hash), response_hash = COALESCE(?, response_hash), "
            "usage_json = COALESCE(?, usage_json), ended_at = ? "
            "WHERE id = ? AND state = 'running'",
            (request_hash, response_hash, usage_json, now, task.task_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeStateError("task state changed; reload and retry")
        session_row = connection.execute(
            "SELECT status, project_id FROM sessions WHERE id = ?", (task.session_id,)
        ).fetchone()
        if session_row is None:
            raise RuntimeStateError("session not found")
        if session_row["status"] != "closed" and session_row["status"] != "idle":
            connection.execute(
                "UPDATE sessions SET status = 'idle', updated_at = ? WHERE id = ?",
                (now, task.session_id),
            )
            self._chain.append_in(
                connection,
                event_type="session_state_changed",
                actor=actor,
                subject_type="session",
                subject_id=task.session_id,
                decision="idle",
                occurred_at_ms=now,
                payload={
                    "session_id": task.session_id,
                    "previous_state": session_row["status"],
                    "state": "idle",
                    "project_id": session_row["project_id"],
                },
            )
        self._chain.append_in(
            connection,
            event_type="task_state_changed",
            actor=actor,
            subject_type="task",
            subject_id=task.task_id,
            decision="blocked",
            occurred_at_ms=now,
            payload={
                "task_id": task.task_id,
                "session_id": task.session_id,
                "previous_state": "running",
                "state": "blocked",
                "stop_reason": "approval_required",
                "request_hash": request_hash,
                "response_hash": response_hash,
                "usage": dict(usage) if usage is not None else None,
            },
        )

    def complete_task_after_approval(
        self, task_id: str, approval_id: str, *, actor: str = "user"
    ) -> TaskRecord:
        """Complete a blocked task only after its linked approved write succeeded."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                raise RuntimeStateError("task not found")
            task = _task_from_row(task_row)
            if task.state == "completed" and task.stop_reason == "approved_write_completed":
                completed_approval = connection.execute(
                    "SELECT id FROM approvals WHERE id = ? AND task_id = ?",
                    (approval_id, task_id),
                ).fetchone()
                if completed_approval is not None:
                    return task
                raise RuntimeStateError("approval does not match the completed task")
            recovering_unblocked = task.state == "running"
            if not recovering_unblocked and (
                task.state != "blocked" or task.stop_reason != "approval_required"
            ):
                raise RuntimeStateError("task is not waiting for an approval outcome")
            approval = connection.execute(
                "SELECT id, status FROM approvals WHERE id = ? AND task_id = ?",
                (approval_id, task_id),
            ).fetchone()
            if approval is None or approval["status"] != "consumed":
                raise RuntimeStateError("linked approval has not been consumed")
            run = connection.execute(
                "SELECT id, output_sha256, backup_id FROM execution_runs "
                "WHERE approval_id = ? AND status = 'success' ORDER BY completed_at DESC LIMIT 1",
                (approval_id,),
            ).fetchone()
            if run is None or run["output_sha256"] is None:
                raise RuntimeStateError("successful execution evidence is unavailable")
            plan = connection.execute(
                "SELECT id, state, stop_reason FROM task_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if recovering_unblocked:
                if plan is not None:
                    if plan["state"] != "running":
                        raise RuntimeStateError("running task plan is not recoverable")
                    pending_step = connection.execute(
                        "SELECT * FROM task_plan_steps WHERE plan_id = ? "
                        "AND tool = 'project.propose_write' AND status = 'pending'",
                        (plan["id"],),
                    ).fetchone()
                    if pending_step is None:
                        raise RuntimeStateError("pending task write step is unavailable")
                    connection.execute(
                        "UPDATE task_plan_steps SET status = 'blocked', "
                        "error_code = 'approval_required', updated_at = ? "
                        "WHERE id = ? AND status = 'pending'",
                        (now, pending_step["id"]),
                    )
                    self._chain.append_in(
                        connection,
                        event_type="plan_step_state_changed",
                        actor=actor,
                        subject_type="task_plan_step",
                        subject_id=pending_step["id"],
                        decision="blocked",
                        occurred_at_ms=now,
                        payload={
                            "plan_id": plan["id"],
                            "step_id": pending_step["id"],
                            "step_index": pending_step["step_index"],
                            "previous_status": "pending",
                            "status": "blocked",
                            "tool": pending_step["tool"],
                            "approval_id": approval_id,
                            "error_code": "approval_required",
                        },
                    )
                    connection.execute(
                        "UPDATE task_plans SET state = 'blocked', stop_reason = ?, updated_at = ? "
                        "WHERE id = ? AND state = 'running'",
                        ("approval_required", now, plan["id"]),
                    )
                    self._chain.append_in(
                        connection,
                        event_type="plan_state_changed",
                        actor=actor,
                        subject_type="task_plan",
                        subject_id=plan["id"],
                        decision="blocked",
                        occurred_at_ms=now,
                        payload={
                            "plan_id": plan["id"],
                            "task_id": task_id,
                            "previous_state": "running",
                            "state": "blocked",
                            "stop_reason": "approval_required",
                        },
                    )
                    plan = connection.execute(
                        "SELECT id, state, stop_reason FROM task_plans WHERE id = ?",
                        (plan["id"],),
                    ).fetchone()
                connection.execute(
                    "UPDATE tasks SET state = 'blocked', stop_reason = ?, ended_at = ? "
                    "WHERE id = ? AND state = 'running'",
                    ("approval_required", now, task_id),
                )
                session_row = connection.execute(
                    "SELECT status, project_id FROM sessions WHERE id = ?", (task.session_id,)
                ).fetchone()
                if session_row is not None and session_row["status"] == "active":
                    connection.execute(
                        "UPDATE sessions SET status = 'idle', updated_at = ? WHERE id = ?",
                        (now, task.session_id),
                    )
                    self._chain.append_in(
                        connection,
                        event_type="session_state_changed",
                        actor=actor,
                        subject_type="session",
                        subject_id=task.session_id,
                        decision="idle",
                        occurred_at_ms=now,
                        payload={
                            "session_id": task.session_id,
                            "previous_state": "active",
                            "state": "idle",
                            "project_id": session_row["project_id"],
                        },
                    )
                self._chain.append_in(
                    connection,
                    event_type="task_state_changed",
                    actor=actor,
                    subject_type="task",
                    subject_id=task_id,
                    decision="blocked",
                    occurred_at_ms=now,
                    payload={
                        "task_id": task_id,
                        "session_id": task.session_id,
                        "previous_state": "running",
                        "state": "blocked",
                        "stop_reason": "approval_required",
                        "recovered_from_execution_evidence": True,
                    },
                )
            if plan is not None:
                step = connection.execute(
                    "SELECT * FROM task_plan_steps WHERE plan_id = ? "
                    "AND tool = 'project.propose_write' AND status = 'blocked' "
                    "AND error_code = 'approval_required'",
                    (plan["id"],),
                ).fetchone()
                if (
                    plan["state"] != "blocked"
                    or plan["stop_reason"] != "approval_required"
                    or step is None
                ):
                    raise RuntimeStateError("plan is not waiting for this approval")
                connection.execute(
                    "UPDATE task_plan_steps SET status = 'completed', error_code = NULL, "
                    "output_sha256 = ?, updated_at = ? WHERE id = ? AND status = 'blocked'",
                    (run["output_sha256"], now, step["id"]),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_step_state_changed",
                    actor=actor,
                    subject_type="task_plan_step",
                    subject_id=step["id"],
                    decision="completed",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan["id"],
                        "step_id": step["id"],
                        "step_index": step["step_index"],
                        "previous_status": "blocked",
                        "status": "completed",
                        "tool": step["tool"],
                        "approval_id": approval_id,
                        "execution_run_id": run["id"],
                        "output_sha256": run["output_sha256"],
                    },
                )
                connection.execute(
                    "UPDATE task_plans SET state = 'completed', stop_reason = ?, updated_at = ? "
                    "WHERE id = ? AND state = 'blocked'",
                    ("approved_write_completed", now, plan["id"]),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_state_changed",
                    actor=actor,
                    subject_type="task_plan",
                    subject_id=plan["id"],
                    decision="completed",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan["id"],
                        "task_id": task_id,
                        "previous_state": "blocked",
                        "state": "completed",
                        "stop_reason": "approved_write_completed",
                    },
                )
            cursor = connection.execute(
                "UPDATE tasks SET state = 'completed', stop_reason = ?, ended_at = ? "
                "WHERE id = ? AND state = 'blocked' AND stop_reason = 'approval_required'",
                ("approved_write_completed", now, task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeStateError("task state changed; reload and retry")
            self._chain.append_in(
                connection,
                event_type="task_approved_write_completed",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision="completed",
                occurred_at_ms=now,
                payload={
                    "task_id": task_id,
                    "approval_id": approval_id,
                    "execution_run_id": run["id"],
                    "output_sha256": run["output_sha256"],
                    "backup_id": run["backup_id"],
                    "state": "completed",
                },
            )
        return self._task(task_id)

    def fail_task_after_approval_rejection(
        self, task_id: str, approval_id: str, *, actor: str = "user"
    ) -> TaskRecord:
        """Close a blocked task after the human rejects its linked proposal."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                raise RuntimeStateError("task not found")
            task = _task_from_row(task_row)
            if task.state != "blocked" or task.stop_reason != "approval_required":
                raise RuntimeStateError("task is not waiting for an approval decision")
            approval = connection.execute(
                "SELECT id, status FROM approvals WHERE id = ? AND task_id = ?",
                (approval_id, task_id),
            ).fetchone()
            if approval is None or approval["status"] != "rejected":
                raise RuntimeStateError("linked approval has not been rejected")
            plan = connection.execute(
                "SELECT id, state, stop_reason FROM task_plans WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if plan is not None:
                step = connection.execute(
                    "SELECT * FROM task_plan_steps WHERE plan_id = ? "
                    "AND tool = 'project.propose_write' AND status = 'blocked' "
                    "AND error_code = 'approval_required'",
                    (plan["id"],),
                ).fetchone()
                if (
                    plan["state"] != "blocked"
                    or plan["stop_reason"] != "approval_required"
                    or step is None
                ):
                    raise RuntimeStateError("plan is not waiting for this approval")
                connection.execute(
                    "UPDATE task_plan_steps SET status = 'failed', error_code = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'blocked'",
                    ("approval_rejected", now, step["id"]),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_step_state_changed",
                    actor=actor,
                    subject_type="task_plan_step",
                    subject_id=step["id"],
                    decision="failed",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan["id"],
                        "step_id": step["id"],
                        "step_index": step["step_index"],
                        "previous_status": "blocked",
                        "status": "failed",
                        "tool": step["tool"],
                        "approval_id": approval_id,
                        "error_code": "approval_rejected",
                    },
                )
                connection.execute(
                    "UPDATE task_plans SET state = 'failed', stop_reason = ?, updated_at = ? "
                    "WHERE id = ? AND state = 'blocked'",
                    ("approval_rejected", now, plan["id"]),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_state_changed",
                    actor=actor,
                    subject_type="task_plan",
                    subject_id=plan["id"],
                    decision="failed",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan["id"],
                        "task_id": task_id,
                        "previous_state": "blocked",
                        "state": "failed",
                        "stop_reason": "approval_rejected",
                    },
                )
            cursor = connection.execute(
                "UPDATE tasks SET state = 'failed', stop_reason = ?, ended_at = ? "
                "WHERE id = ? AND state = 'blocked' AND stop_reason = 'approval_required'",
                ("approval_rejected", now, task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeStateError("task state changed; reload and retry")
            self._chain.append_in(
                connection,
                event_type="task_approval_rejected",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision="failed",
                occurred_at_ms=now,
                payload={"task_id": task_id, "approval_id": approval_id, "state": "failed"},
            )
        return self._task(task_id)

    def cancel_task(self, task_id: str, *, actor: str = "user") -> TaskRecord:
        """Atomically cancel a task, its plan and unexecuted steps; never undo writes."""
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                raise RuntimeStateError("task not found")
            task = _task_from_row(task_row)
            if task.state in {"completed", "failed", "cancelled"}:
                raise RuntimeStateError(f"task is already terminal: {task.state}")
            if _has_successful_execution(connection, task_id):
                raise RuntimeStateError("successful_execution_evidence_exists")
            if task.state == "blocked" and task.stop_reason == "approval_required":
                raise RuntimeStateError("approval_required_cannot_cancel")
            if task.state == "blocked" and task.stop_reason != "human_review_required":
                raise RuntimeStateError("blocked task cannot be cancelled in this state")
            session_row = connection.execute(
                "SELECT status, project_id FROM sessions WHERE id = ?", (task.session_id,)
            ).fetchone()
            if session_row is None:
                raise RuntimeStateError("session not found")

            plan_row = connection.execute(
                "SELECT * FROM task_plans WHERE task_id = ?", (task_id,)
            ).fetchone()
            plan_id = plan_row["id"] if plan_row is not None else None
            if plan_row is not None and plan_row["state"] not in {
                "completed",
                "failed",
                "cancelled",
            }:
                connection.execute(
                    "UPDATE task_plans SET state = 'cancelled', stop_reason = 'cancelled', "
                    "updated_at = ? WHERE id = ? AND state NOT IN "
                    "('completed','failed','cancelled')",
                    (now, plan_id),
                )
                self._chain.append_in(
                    connection,
                    event_type="plan_state_changed",
                    actor=actor,
                    subject_type="task_plan",
                    subject_id=str(plan_id),
                    decision="cancelled",
                    occurred_at_ms=now,
                    payload={
                        "plan_id": plan_id,
                        "task_id": task_id,
                        "previous_state": plan_row["state"],
                        "state": "cancelled",
                        "stop_reason": "cancelled",
                    },
                )
            if plan_id is not None:
                steps = connection.execute(
                    "SELECT * FROM task_plan_steps WHERE plan_id = ? AND status NOT IN "
                    "('completed','failed','cancelled','skipped') ORDER BY step_index",
                    (plan_id,),
                ).fetchall()
                for step in steps:
                    new_status = "failed" if step["status"] == "running" else "skipped"
                    connection.execute(
                        "UPDATE task_plan_steps SET status = ?, error_code = 'cancelled', "
                        "updated_at = ? WHERE id = ? AND status = ?",
                        (new_status, now, step["id"], step["status"]),
                    )
                    self._chain.append_in(
                        connection,
                        event_type="plan_step_state_changed",
                        actor=actor,
                        subject_type="task_plan_step",
                        subject_id=step["id"],
                        decision=new_status,
                        occurred_at_ms=now,
                        payload={
                            "plan_id": plan_id,
                            "step_id": step["id"],
                            "step_index": step["step_index"],
                            "previous_status": step["status"],
                            "status": new_status,
                            "tool": step["tool"],
                            "error_code": "cancelled",
                        },
                    )
            connection.execute(
                "UPDATE tasks SET state = 'cancelled', stop_reason = 'cancelled', ended_at = ? "
                "WHERE id = ? AND state IN ('created','running','blocked')",
                (now, task_id),
            )
            self._chain.append_in(
                connection,
                event_type="task_state_changed",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision="cancelled",
                occurred_at_ms=now,
                payload={
                    "task_id": task_id,
                    "session_id": task.session_id,
                    "previous_state": task.state,
                    "state": "cancelled",
                    "stop_reason": "cancelled",
                },
            )
            if session_row["status"] == "active":
                connection.execute(
                    "UPDATE sessions SET status = 'idle', updated_at = ? WHERE id = ?",
                    (now, task.session_id),
                )
                self._chain.append_in(
                    connection,
                    event_type="session_state_changed",
                    actor=actor,
                    subject_type="session",
                    subject_id=task.session_id,
                    decision="idle",
                    occurred_at_ms=now,
                    payload={
                        "session_id": task.session_id,
                        "previous_state": "active",
                        "state": "idle",
                        "project_id": session_row["project_id"],
                    },
                )
        return self._task(task_id)

    def continue_human_review(
        self, task_id: str, step_ref: str, *, actor: str = "user"
    ) -> TaskRecord:
        """Complete one blocked human-review step and resume; no permission grant."""
        if not step_ref or len(step_ref) > 24:
            raise RuntimeStateError("invalid_review_step")
        now = _now_ms()
        with self._database.session(immediate=True) as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task_row is None:
                raise RuntimeStateError("task not found")
            task = _task_from_row(task_row)
            if task.state != "blocked" or task.stop_reason != "human_review_required":
                raise RuntimeStateError("task is not waiting for human review")
            plan_row = connection.execute(
                "SELECT * FROM task_plans WHERE task_id = ?", (task_id,)
            ).fetchone()
            if (
                plan_row is None
                or plan_row["state"] != "blocked"
                or plan_row["stop_reason"] != "human_review_required"
            ):
                raise RuntimeStateError("plan is not waiting for human review")
            step_row = connection.execute(
                "SELECT * FROM task_plan_steps WHERE plan_id = ? AND step_ref = ? "
                "AND tool = 'human_review' AND status = 'blocked' "
                "AND error_code = 'human_review_required'",
                (plan_row["id"], step_ref),
            ).fetchone()
            if step_row is None:
                raise RuntimeStateError("human_review_step_not_found")
            connection.execute(
                "UPDATE task_plan_steps SET status = 'completed', error_code = NULL, "
                "updated_at = ? WHERE id = ? AND status = 'blocked'",
                (now, step_row["id"]),
            )
            self._chain.append_in(
                connection,
                event_type="plan_step_state_changed",
                actor=actor,
                subject_type="task_plan_step",
                subject_id=step_row["id"],
                decision="completed",
                occurred_at_ms=now,
                payload={
                    "plan_id": plan_row["id"],
                    "step_id": step_row["id"],
                    "step_index": step_row["step_index"],
                    "previous_status": "blocked",
                    "status": "completed",
                    "tool": "human_review",
                },
            )
            connection.execute(
                "UPDATE task_plans SET state = 'running', stop_reason = NULL, updated_at = ? "
                "WHERE id = ? AND state = 'blocked'",
                (now, plan_row["id"]),
            )
            self._chain.append_in(
                connection,
                event_type="plan_state_changed",
                actor=actor,
                subject_type="task_plan",
                subject_id=plan_row["id"],
                decision="running",
                occurred_at_ms=now,
                payload={
                    "plan_id": plan_row["id"],
                    "task_id": task_id,
                    "previous_state": "blocked",
                    "state": "running",
                    "stop_reason": None,
                },
            )
            session_row = connection.execute(
                "SELECT status, project_id FROM sessions WHERE id = ?", (task.session_id,)
            ).fetchone()
            connection.execute(
                "UPDATE tasks SET state = 'running', stop_reason = NULL, ended_at = NULL "
                "WHERE id = ? AND state = 'blocked'",
                (task_id,),
            )
            self._chain.append_in(
                connection,
                event_type="task_state_changed",
                actor=actor,
                subject_type="task",
                subject_id=task_id,
                decision="running",
                occurred_at_ms=now,
                payload={
                    "task_id": task_id,
                    "session_id": task.session_id,
                    "previous_state": "blocked",
                    "state": "running",
                    "stop_reason": None,
                },
            )
            if session_row is not None and session_row["status"] == "idle":
                connection.execute(
                    "UPDATE sessions SET status = 'active', updated_at = ? WHERE id = ?",
                    (now, task.session_id),
                )
                self._chain.append_in(
                    connection,
                    event_type="session_state_changed",
                    actor=actor,
                    subject_type="session",
                    subject_id=task.session_id,
                    decision="active",
                    occurred_at_ms=now,
                    payload={
                        "session_id": task.session_id,
                        "previous_state": "idle",
                        "state": "active",
                        "project_id": session_row["project_id"],
                    },
                )
        return self._task(task_id)

    def _session(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session is None:
            raise RuntimeStateError("session not found after write")
        return session

    def _task(self, task_id: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise RuntimeStateError("task not found after write")
        return task

    @staticmethod
    def _project_id(connection: sqlite3.Connection, session_id: str) -> str | None:
        row = connection.execute(
            "SELECT project_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return cast(str | None, row["project_id"]) if row is not None else None


def _has_successful_execution(connection: sqlite3.Connection, task_id: str) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM approvals a JOIN execution_runs r ON r.approval_id = a.id
        WHERE a.task_id = ? AND a.status = 'consumed' AND r.status = 'success' LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return row is not None


def _session_from_row(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["id"],
        title=row["title"],
        status=cast(SessionStatus, row["status"]),
        project_id=row["project_id"],
        created_at_ms=row["created_at"],
        updated_at_ms=row["updated_at"],
        closed_at_ms=row["closed_at"],
    )


def _task_from_row(row: sqlite3.Row) -> TaskRecord:
    usage_raw = row["usage_json"]
    usage = cast(dict[str, int], json.loads(usage_raw)) if usage_raw is not None else {}
    return TaskRecord(
        task_id=row["id"],
        session_id=row["session_id"],
        state=cast(TaskState, row["state"]),
        principal=row["principal"],
        goal=row["goal"],
        goal_hash=row["goal_hash"],
        policy_decision_id=row["policy_decision_id"],
        policy_version=row["policy_version"],
        runtime_version=row["runtime_version"],
        stop_reason=row["stop_reason"],
        request_hash=row["request_hash"],
        response_hash=row["response_hash"],
        finish_reason=row["finish_reason"],
        usage=usage,
        created_at_ms=row["created_at"],
        started_at_ms=row["started_at"],
        ended_at_ms=row["ended_at"],
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
