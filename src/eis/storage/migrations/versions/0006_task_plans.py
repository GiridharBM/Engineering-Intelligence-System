# SPDX-License-Identifier: Apache-2.0
"""Durable task plans and ordered steps.

Revision ID: 0006_task_plans
Revises: 0005_project_roots
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op

revision = "0006_task_plans"
down_revision = "0005_project_roots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_plans (
            id             TEXT PRIMARY KEY,
            task_id        TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE RESTRICT,
            project_id     TEXT NULL,
            state          TEXT NOT NULL CHECK (state IN ('created','running','blocked',
                                                   'completed','failed','cancelled')),
            policy_version TEXT NOT NULL,
            policy_hash    TEXT NOT NULL CHECK (length(policy_hash) = 64),
            plan_hash      TEXT NOT NULL CHECK (length(plan_hash) = 64),
            stop_reason    TEXT NULL,
            created_at     INTEGER NOT NULL,
            updated_at     INTEGER NOT NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_task_plans_state_created ON task_plans(state, created_at)")
    op.execute(
        """
        CREATE TRIGGER task_plans_state_transition BEFORE UPDATE OF state ON task_plans
        WHEN NOT (
            (OLD.state = 'created' AND NEW.state IN ('running','failed','cancelled'))
            OR (OLD.state = 'running'
                AND NEW.state IN ('blocked','completed','failed','cancelled'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task plan state transition');
        END
        """
    )
    op.execute(
        """
        CREATE TABLE task_plan_steps (
            id                TEXT PRIMARY KEY,
            plan_id           TEXT NOT NULL REFERENCES task_plans(id) ON DELETE CASCADE,
            step_index        INTEGER NOT NULL CHECK (step_index >= 0),
            step_ref          TEXT NOT NULL CHECK (length(step_ref) BETWEEN 1 AND 24),
            tool              TEXT NOT NULL CHECK (length(tool) BETWEEN 1 AND 64),
            depends_on_json   TEXT NOT NULL CHECK (json_valid(depends_on_json)
                               AND json_type(depends_on_json) = 'array'),
            status            TEXT NOT NULL CHECK (status IN ('pending','running','completed',
                                                   'failed','blocked','skipped')),
            policy_decision_id TEXT NULL REFERENCES policy_decisions(decision_id)
                               ON DELETE RESTRICT,
            output_sha256     TEXT NULL CHECK (
                output_sha256 IS NULL OR length(output_sha256) = 64),
            error_code        TEXT NULL,
            created_at        INTEGER NOT NULL,
            updated_at        INTEGER NOT NULL,
            UNIQUE (plan_id, step_index),
            UNIQUE (plan_id, step_ref)
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_task_plan_steps_plan ON task_plan_steps(plan_id, step_index)")
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


def downgrade() -> None:
    op.execute("DROP TRIGGER task_plan_steps_status_transition")
    op.execute("DROP INDEX idx_task_plan_steps_plan")
    op.execute("DROP TABLE task_plan_steps")
    op.execute("DROP TRIGGER task_plans_state_transition")
    op.execute("DROP INDEX idx_task_plans_state_created")
    op.execute("DROP TABLE task_plans")
