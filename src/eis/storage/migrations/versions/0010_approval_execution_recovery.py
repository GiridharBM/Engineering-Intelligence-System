# SPDX-License-Identifier: Apache-2.0
"""Fail-closed recovery for consumed approvals without execution evidence.

Revision ID: 0010_approval_execution_recovery
Revises: 0009_task_cancellation_review
Create Date: 2026-11-27
"""

from __future__ import annotations

from alembic import op

revision = "0010_approval_execution_recovery"
down_revision = "0009_task_cancellation_review"
branch_labels = None
depends_on = None

_NO_SUCCESS = (
    "NOT EXISTS (SELECT 1 FROM execution_runs r "
    "WHERE r.approval_id = a.id AND r.status = 'success')"
)


def upgrade() -> None:
    op.execute("DROP TRIGGER tasks_state_transition")
    op.execute(
        f"""
        CREATE TRIGGER tasks_state_transition BEFORE UPDATE OF state ON tasks
        WHEN NOT (
            (OLD.state = 'created' AND NEW.state IN ('running','failed','cancelled'))
            OR (OLD.state = 'running'
                AND NEW.state IN ('blocked','completed','failed','cancelled'))
            OR (OLD.state = 'blocked' AND NEW.state = 'running'
                AND OLD.stop_reason = 'human_review_required' AND NEW.stop_reason IS NULL)
            OR (OLD.state = 'blocked' AND NEW.state = 'cancelled'
                AND OLD.stop_reason = 'human_review_required')
            OR (OLD.state = 'blocked' AND NEW.state = 'completed'
                AND OLD.stop_reason = 'approval_required'
                AND NEW.stop_reason = 'approved_write_completed'
                AND EXISTS (
                    SELECT 1 FROM approvals a JOIN execution_runs r ON r.approval_id = a.id
                    WHERE a.task_id = OLD.id AND a.status = 'consumed' AND r.status = 'success'
                ))
            OR (OLD.state = 'blocked' AND NEW.state = 'failed'
                AND OLD.stop_reason = 'approval_required'
                AND ((NEW.stop_reason = 'approval_rejected'
                      AND EXISTS (SELECT 1 FROM approvals a
                                  WHERE a.task_id = OLD.id AND a.status = 'rejected'))
                     OR (NEW.stop_reason = 'execution_unverified'
                         AND EXISTS (SELECT 1 FROM approvals a
                                     WHERE a.task_id = OLD.id AND a.status = 'consumed'
                                       AND {_NO_SUCCESS}))))
        )
        AND NOT (
            NEW.state = 'cancelled' AND EXISTS (
                SELECT 1 FROM approvals a JOIN execution_runs r ON r.approval_id = a.id
                WHERE a.task_id = OLD.id AND a.status = 'consumed' AND r.status = 'success'
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task state transition');
        END
        """
    )
    op.execute("DROP TRIGGER task_plans_state_transition")
    op.execute(
        f"""
        CREATE TRIGGER task_plans_state_transition BEFORE UPDATE OF state ON task_plans
        WHEN NOT (
            (OLD.state = 'created' AND NEW.state IN ('running','failed','cancelled'))
            OR (OLD.state = 'running'
                AND NEW.state IN ('blocked','completed','failed','cancelled'))
            OR (OLD.state = 'blocked' AND NEW.state = 'running'
                AND OLD.stop_reason = 'human_review_required' AND NEW.stop_reason IS NULL)
            OR (OLD.state = 'blocked' AND NEW.state = 'cancelled'
                AND OLD.stop_reason = 'human_review_required')
            OR (OLD.state = 'blocked' AND NEW.state = 'completed'
                AND OLD.stop_reason = 'approval_required'
                AND NEW.stop_reason = 'approved_write_completed'
                AND EXISTS (
                    SELECT 1 FROM task_plan_steps s
                    JOIN approvals a ON a.task_id = OLD.task_id
                    JOIN execution_runs r ON r.approval_id = a.id
                    WHERE s.plan_id = OLD.id AND s.tool = 'project.propose_write'
                      AND s.status = 'completed' AND a.status = 'consumed' AND r.status = 'success'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM task_plan_steps s
                    WHERE s.plan_id = OLD.id AND s.status <> 'completed'
                ))
            OR (OLD.state = 'blocked' AND NEW.state = 'failed'
                AND OLD.stop_reason = 'approval_required'
                AND ((NEW.stop_reason = 'approval_rejected'
                      AND EXISTS (SELECT 1 FROM approvals a
                                  WHERE a.task_id = OLD.task_id AND a.status = 'rejected'))
                     OR (NEW.stop_reason = 'execution_unverified'
                         AND EXISTS (SELECT 1 FROM approvals a
                                     WHERE a.task_id = OLD.task_id AND a.status = 'consumed'
                                       AND {_NO_SUCCESS}))))
        )
        AND NOT (
            NEW.state = 'cancelled' AND EXISTS (
                SELECT 1 FROM approvals a JOIN execution_runs r ON r.approval_id = a.id
                WHERE a.task_id = OLD.task_id AND a.status = 'consumed' AND r.status = 'success'
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan state transition');
        END
        """
    )
    op.execute("DROP TRIGGER task_plan_steps_status_transition")
    op.execute(
        f"""
        CREATE TRIGGER task_plan_steps_status_transition
        BEFORE UPDATE OF status ON task_plan_steps
        WHEN NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','blocked','failed','skipped'))
            OR (OLD.status = 'running' AND NEW.status IN ('completed','failed','skipped'))
            OR (OLD.status = 'running' AND NEW.status = 'pending'
                AND NEW.error_code = 'interrupted_retry' AND OLD.attempt_count < 2)
            OR (OLD.status = 'running' AND NEW.status = 'blocked'
                AND OLD.tool = 'project.propose_write' AND NEW.error_code = 'approval_required')
            OR (OLD.status = 'blocked' AND NEW.status = 'completed'
                AND OLD.tool = 'human_review' AND OLD.error_code = 'human_review_required'
                AND NEW.error_code IS NULL)
            OR (OLD.status = 'blocked' AND NEW.status = 'skipped' AND OLD.tool = 'human_review')
            OR (OLD.status = 'blocked' AND NEW.status = 'completed'
                AND OLD.tool = 'project.propose_write' AND OLD.error_code = 'approval_required'
                AND EXISTS (
                    SELECT 1 FROM task_plans p
                    JOIN approvals a ON a.task_id = p.task_id
                    JOIN execution_runs r ON r.approval_id = a.id
                    WHERE p.id = OLD.plan_id AND a.status = 'consumed' AND r.status = 'success'
                ))
            OR (OLD.status = 'blocked' AND NEW.status = 'failed'
                AND OLD.tool = 'project.propose_write' AND OLD.error_code = 'approval_required'
                AND ((NEW.error_code = 'approval_rejected'
                      AND EXISTS (SELECT 1 FROM task_plans p
                                  JOIN approvals a ON a.task_id = p.task_id
                                  WHERE p.id = OLD.plan_id AND a.status = 'rejected'))
                     OR (NEW.error_code = 'execution_unverified'
                         AND EXISTS (SELECT 1 FROM task_plans p
                                     JOIN approvals a ON a.task_id = p.task_id
                                     WHERE p.id = OLD.plan_id AND a.status = 'consumed'
                                       AND {_NO_SUCCESS}))))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan step status transition');
        END
        """
    )


def downgrade() -> None:
    # New fail-closed evidence transitions cannot be represented by 0009 triggers.
    op.execute("DROP TRIGGER task_plan_steps_status_transition")
    op.execute(
        """
        CREATE TRIGGER task_plan_steps_status_transition
        BEFORE UPDATE OF status ON task_plan_steps
        WHEN NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','blocked','failed','skipped'))
            OR (OLD.status = 'running' AND NEW.status IN ('completed','failed','skipped'))
            OR (OLD.status = 'running' AND NEW.status = 'pending'
                AND NEW.error_code = 'interrupted_retry' AND OLD.attempt_count < 2)
            OR (OLD.status = 'running' AND NEW.status = 'blocked'
                AND OLD.tool = 'project.propose_write' AND NEW.error_code = 'approval_required')
            OR (OLD.status = 'blocked' AND NEW.status = 'completed'
                AND OLD.tool = 'human_review' AND OLD.error_code = 'human_review_required'
                AND NEW.error_code IS NULL)
            OR (OLD.status = 'blocked' AND NEW.status = 'skipped' AND OLD.tool = 'human_review')
            OR (OLD.status = 'blocked' AND NEW.status = 'completed'
                AND OLD.tool = 'project.propose_write' AND OLD.error_code = 'approval_required'
                AND EXISTS (
                    SELECT 1 FROM task_plans p
                    JOIN approvals a ON a.task_id = p.task_id
                    JOIN execution_runs r ON r.approval_id = a.id
                    WHERE p.id = OLD.plan_id AND a.status = 'consumed' AND r.status = 'success'
                ))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan step status transition');
        END
        """
    )
