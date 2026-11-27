# SPDX-License-Identifier: Apache-2.0
"""Policy projection and approval-gated execution records.

Revision ID: 0003_approvals_policy_projection
Revises: 0002_audit_chain
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0003_approvals_policy_projection"
down_revision = "0002_audit_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE policy_decisions (
            decision_id TEXT PRIMARY KEY REFERENCES audit_events(id) ON DELETE RESTRICT,
            policy_version TEXT NOT NULL,
            policy_hash TEXT NOT NULL CHECK (length(policy_hash) = 64),
            inputs_hash TEXT NOT NULL CHECK (length(inputs_hash) = 64),
            principal TEXT NOT NULL,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            effect TEXT NOT NULL CHECK (effect IN ('allow','deny')),
            reason TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            context_source TEXT NULL,
            created_at INTEGER NOT NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_policy_decisions_created ON policy_decisions(created_at)")
    op.execute("CREATE INDEX idx_policy_decisions_action ON policy_decisions(action, effect)")
    op.execute(
        """
        INSERT INTO policy_decisions
            (decision_id, policy_version, policy_hash, inputs_hash, principal,
             action, resource, effect, reason, rule_id, context_source, created_at)
        SELECT id,
               json_extract(payload_json, '$.policy_version'),
               json_extract(payload_json, '$.policy_hash'),
               json_extract(payload_json, '$.inputs_hash'),
               json_extract(payload_json, '$.principal'),
               json_extract(payload_json, '$.action'),
               json_extract(payload_json, '$.resource'),
               json_extract(payload_json, '$.effect'),
               json_extract(payload_json, '$.reason'),
               json_extract(payload_json, '$.rule_id'),
               json_extract(payload_json, '$.context_source'),
               occurred_at
        FROM audit_events WHERE event_type = 'policy_decision'
        """
    )
    op.execute(
        """
        CREATE TRIGGER policy_decisions_no_update BEFORE UPDATE ON policy_decisions
        BEGIN SELECT RAISE(ABORT, 'policy_decisions is append-only'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER policy_decisions_no_delete BEFORE DELETE ON policy_decisions
        BEGIN SELECT RAISE(ABORT, 'policy_decisions is append-only'); END
        """
    )

    op.execute(
        """
        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL CHECK (action = 'project.write'),
            on_behalf_of TEXT NOT NULL,
            resource TEXT NOT NULL,
            project_root_hash TEXT NOT NULL CHECK (length(project_root_hash) = 64),
            policy_decision_id TEXT NOT NULL REFERENCES policy_decisions(decision_id)
                ON DELETE RESTRICT,
            policy_version TEXT NOT NULL,
            expected_sha256 TEXT NULL CHECK (
                expected_sha256 IS NULL OR length(expected_sha256) = 64
            ),
            new_sha256 TEXT NOT NULL CHECK (length(new_sha256) = 64),
            diff_sha256 TEXT NOT NULL CHECK (length(diff_sha256) = 64),
            diff_text TEXT NOT NULL,
            new_content TEXT NOT NULL,
            summary TEXT NOT NULL,
            risk_class TEXT NOT NULL CHECK (risk_class = 'A3'),
            request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
            status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','consumed')),
            requested_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            decided_at INTEGER NULL,
            consumed_at INTEGER NULL,
            decision_note TEXT NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_approvals_status_expiry ON approvals(status, expires_at)")
    op.execute(
        """
        CREATE TRIGGER approvals_binding_immutable BEFORE UPDATE ON approvals
        WHEN NEW.id <> OLD.id OR NEW.action <> OLD.action
          OR NEW.on_behalf_of <> OLD.on_behalf_of OR NEW.resource <> OLD.resource
          OR NEW.project_root_hash <> OLD.project_root_hash
          OR NEW.policy_decision_id <> OLD.policy_decision_id
          OR NEW.policy_version <> OLD.policy_version
          OR NEW.expected_sha256 IS NOT OLD.expected_sha256
          OR NEW.new_sha256 <> OLD.new_sha256 OR NEW.diff_text <> OLD.diff_text
          OR NEW.diff_sha256 <> OLD.diff_sha256
          OR NEW.new_content <> OLD.new_content OR NEW.summary <> OLD.summary
          OR NEW.risk_class <> OLD.risk_class OR NEW.request_digest <> OLD.request_digest
          OR NEW.requested_at <> OLD.requested_at OR NEW.expires_at <> OLD.expires_at
        BEGIN SELECT RAISE(ABORT, 'approval binding is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER approvals_decision_note_once BEFORE UPDATE OF decision_note ON approvals
        WHEN OLD.decision_note IS NOT NULL OR NEW.status = OLD.status
        BEGIN SELECT RAISE(ABORT, 'approval decision note is immutable'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER approvals_status_transition BEFORE UPDATE OF status ON approvals
        WHEN NOT ((OLD.status = 'pending' AND NEW.status IN ('approved','rejected'))
               OR (OLD.status = 'approved' AND NEW.status = 'consumed'))
        BEGIN SELECT RAISE(ABORT, 'invalid approval status transition'); END
        """
    )

    op.execute(
        """
        CREATE TABLE execution_runs (
            id TEXT PRIMARY KEY,
            approval_id TEXT NULL REFERENCES approvals(id) ON DELETE RESTRICT,
            action TEXT NOT NULL,
            resource TEXT NOT NULL,
            profile_id TEXT NULL,
            status TEXT NOT NULL CHECK (status IN ('success','failed','refused','unavailable')),
            started_at INTEGER NOT NULL,
            completed_at INTEGER NOT NULL,
            exit_code INTEGER NULL,
            output_sha256 TEXT NULL CHECK (output_sha256 IS NULL OR length(output_sha256) = 64),
            error_code TEXT NULL,
            backup_id TEXT NULL
        ) STRICT
        """
    )
    op.execute("CREATE INDEX idx_execution_runs_started ON execution_runs(started_at)")
    op.execute(
        """
        CREATE TRIGGER execution_runs_no_update BEFORE UPDATE ON execution_runs
        BEGIN SELECT RAISE(ABORT, 'execution_runs is append-only'); END
        """
    )
    op.execute(
        """
        CREATE TRIGGER execution_runs_no_delete BEFORE DELETE ON execution_runs
        BEGIN SELECT RAISE(ABORT, 'execution_runs is append-only'); END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER execution_runs_no_delete")
    op.execute("DROP TRIGGER execution_runs_no_update")
    op.execute("DROP TABLE execution_runs")
    op.execute("DROP TRIGGER approvals_decision_note_once")
    op.execute("DROP TRIGGER approvals_status_transition")
    op.execute("DROP TRIGGER approvals_binding_immutable")
    op.execute("DROP TABLE approvals")
    op.execute("DROP TRIGGER policy_decisions_no_delete")
    op.execute("DROP TRIGGER policy_decisions_no_update")
    op.execute("DROP TABLE policy_decisions")
