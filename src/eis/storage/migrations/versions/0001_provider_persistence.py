# SPDX-License-Identifier: Apache-2.0
"""Provider consent and content-free audit persistence.

Revision ID: 0001_provider_persistence
Revises:
Create Date: 2026-11-20
"""

from __future__ import annotations

from alembic import op

revision = "0001_provider_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE consents (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL CHECK (scope IN ('model_provider')),
            subject TEXT NOT NULL,
            endpoint TEXT NULL,
            data_classes TEXT NOT NULL CHECK (json_valid(data_classes)),
            purpose TEXT NOT NULL,
            retention_unknown INTEGER NOT NULL DEFAULT 1 CHECK (retention_unknown IN (0,1)),
            granted_at INTEGER NOT NULL,
            expires_at INTEGER NULL,
            revoked_at INTEGER NULL,
            evidence_note TEXT NULL,
            CHECK (json_type(data_classes) = 'array')
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_consents_scope_subject ON consents(scope, subject, revoked_at)")

    op.execute(
        """
        CREATE TABLE egress_decisions (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            data_class TEXT NOT NULL CHECK (data_class IN ('C0','C1','C2','C3')),
            decision TEXT NOT NULL CHECK (decision IN ('allow','deny')),
            reason TEXT NOT NULL,
            consent_id TEXT NULL REFERENCES consents(id) ON DELETE RESTRICT,
            occurred_at INTEGER NOT NULL,
            CHECK (decision <> 'allow' OR consent_id IS NOT NULL)
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_egress_provider_time ON egress_decisions(provider, occurred_at)")

    op.execute(
        """
        CREATE TABLE model_calls (
            id TEXT PRIMARY KEY,
            task_id TEXT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'model_completion',
            request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
            response_hash TEXT NULL,
            prompt_tokens INTEGER NULL,
            completion_tokens INTEGER NULL,
            total_tokens INTEGER NULL,
            latency_ms REAL NOT NULL,
            egress_consent_id TEXT NULL REFERENCES consents(id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('success','provider_error','consent_denied')),
            created_at INTEGER NOT NULL,
            finish_reason TEXT NULL,
            provider_request_id TEXT NULL,
            usage_json TEXT NULL CHECK (json_valid(usage_json)),
            CHECK (status <> 'success' OR egress_consent_id IS NOT NULL)
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_model_calls_provider_time ON model_calls(provider, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE model_calls")
    op.execute("DROP TABLE egress_decisions")
    op.execute("DROP TABLE consents")
