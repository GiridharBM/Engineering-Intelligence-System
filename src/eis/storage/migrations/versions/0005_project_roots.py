# SPDX-License-Identifier: Apache-2.0
"""Durable project-root registry.

Revision ID: 0005_project_roots
Revises: 0004_sessions_tasks
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

revision = "0005_project_roots"
down_revision = "0004_sessions_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_roots (
            project_id    TEXT PRIMARY KEY,
            root_path     TEXT NOT NULL CHECK (length(root_path) BETWEEN 1 AND 4096),
            root_identity TEXT NOT NULL CHECK (length(root_identity) = 64),
            created_at    INTEGER NOT NULL,
            updated_at    INTEGER NOT NULL
        ) STRICT
        """
    )
    op.execute("CREATE UNIQUE INDEX idx_project_roots_path ON project_roots(root_path)")


def downgrade() -> None:
    op.execute("DROP TABLE project_roots")
