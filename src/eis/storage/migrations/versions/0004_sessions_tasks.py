# SPDX-License-Identifier: Apache-2.0
"""Durable sessions and tasks.

Revision ID: 0004_sessions_tasks
Revises: 0003_approvals_policy_projection
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0004_sessions_tasks"
down_revision = "0003_approvals_policy_projection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sessions (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            status      TEXT NOT NULL CHECK (status IN ('active','idle','closed')),
            project_id  TEXT NULL,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL,
            closed_at   INTEGER NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_sessions_status_updated ON sessions(status, updated_at)")
    op.execute(
        """
        CREATE TRIGGER sessions_state_transition BEFORE UPDATE OF status ON sessions
        WHEN NOT (
            (OLD.status = 'active' AND NEW.status IN ('idle','closed'))
            OR (OLD.status = 'idle' AND NEW.status IN ('active','closed'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid session status transition');
        END
        """
    )

    op.execute(
        """
        CREATE TABLE tasks (
            id                 TEXT PRIMARY KEY,
            session_id         TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
            state              TEXT NOT NULL
                               CHECK (state IN ('created','running','blocked',
                                                'completed','failed','cancelled')),
            principal          TEXT NOT NULL,
            goal               TEXT NOT NULL,
            goal_hash          TEXT NOT NULL CHECK (length(goal_hash) = 64),
            policy_decision_id TEXT NULL REFERENCES policy_decisions(decision_id)
                               ON DELETE RESTRICT,
            policy_version     TEXT NOT NULL,
            runtime_version    TEXT NOT NULL,
            stop_reason        TEXT NULL,
            request_hash       TEXT NULL CHECK (
                request_hash IS NULL OR length(request_hash) = 64
            ),
            response_hash      TEXT NULL CHECK (
                response_hash IS NULL OR length(response_hash) = 64
            ),
            finish_reason      TEXT NULL,
            usage_json         TEXT NULL CHECK (
                usage_json IS NULL OR (json_valid(usage_json)
                                       AND json_type(usage_json) = 'object')
            ),
            created_at         INTEGER NOT NULL,
            started_at         INTEGER NULL,
            ended_at           INTEGER NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_tasks_session_created ON tasks(session_id, created_at)")
    op.execute("CREATE INDEX idx_tasks_state ON tasks(state)")
    op.execute(
        """
        CREATE TRIGGER tasks_state_transition BEFORE UPDATE OF state ON tasks
        WHEN NOT (
            (OLD.state = 'created' AND NEW.state IN ('running','failed','cancelled'))
            OR (OLD.state = 'running'
                AND NEW.state IN ('blocked','completed','failed','cancelled'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'invalid task state transition');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER tasks_state_transition")
    op.execute("DROP TABLE tasks")
    op.execute("DROP TRIGGER sessions_state_transition")
    op.execute("DROP TABLE sessions")
