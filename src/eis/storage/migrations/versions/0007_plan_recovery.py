# SPDX-License-Identifier: Apache-2.0
"""Persist bounded plan inputs for explicit crash recovery.

Revision ID: 0007_plan_recovery
Revises: 0006_task_plans
Create Date: 2026-11-24
"""

from __future__ import annotations

from alembic import op

revision = "0007_plan_recovery"
down_revision = "0006_task_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE task_plan_steps ADD COLUMN arguments_json TEXT NULL
            CHECK (arguments_json IS NULL OR
                   (json_valid(arguments_json) AND json_type(arguments_json) = 'object'))
        """
    )
    op.execute(
        """
        ALTER TABLE task_plan_steps ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
            CHECK (attempt_count BETWEEN 0 AND 2)
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_plan_step_arguments_immutable
        BEFORE UPDATE OF arguments_json ON task_plan_steps
        BEGIN
            SELECT RAISE(ABORT, 'plan step arguments are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_plan_step_attempt_guard
        BEFORE UPDATE OF attempt_count ON task_plan_steps
        WHEN NEW.attempt_count <> OLD.attempt_count + CASE
            WHEN OLD.status = 'pending' AND NEW.status = 'running' THEN 1 ELSE 0 END
        BEGIN
            SELECT RAISE(ABORT, 'invalid plan step attempt count');
        END
        """
    )
    op.execute("DROP TRIGGER task_plan_steps_status_transition")
    op.execute(
        """
        CREATE TRIGGER task_plan_steps_status_transition
        BEFORE UPDATE OF status ON task_plan_steps
        WHEN NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','blocked','skipped'))
            OR (OLD.status = 'running'
                AND NEW.status IN ('completed','failed','skipped'))
            OR (OLD.status = 'running' AND NEW.status = 'pending'
                AND NEW.error_code = 'interrupted_retry'
                AND OLD.attempt_count < 2)
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan step status transition');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER task_plan_steps_status_transition")
    op.execute("DROP TRIGGER task_plan_step_attempt_guard")
    op.execute("DROP TRIGGER task_plan_step_arguments_immutable")
    with op.batch_alter_table("task_plan_steps") as batch:
        batch.drop_column("attempt_count")
        batch.drop_column("arguments_json")
    op.execute(
        """
        CREATE TRIGGER task_plan_steps_status_transition
        BEFORE UPDATE OF status ON task_plan_steps
        WHEN NOT (
            (OLD.status = 'pending' AND NEW.status IN ('running','blocked','skipped'))
            OR (OLD.status = 'running' AND NEW.status IN ('completed','failed','skipped'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan step status transition');
        END
        """
    )
