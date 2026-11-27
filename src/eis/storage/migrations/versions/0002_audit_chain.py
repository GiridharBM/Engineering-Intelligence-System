# SPDX-License-Identifier: Apache-2.0
"""Append-only, hash-chained audit events.

Revision ID: 0002_audit_chain
Revises: 0001_provider_persistence
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0002_audit_chain"
down_revision = "0001_provider_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_events (
            seq           INTEGER PRIMARY KEY AUTOINCREMENT,
            id            TEXT NOT NULL UNIQUE,
            occurred_at   INTEGER NOT NULL,
            actor         TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            subject_type  TEXT NOT NULL,
            subject_id    TEXT NOT NULL,
            decision      TEXT NULL,
            payload_json  TEXT NOT NULL
                          CHECK (json_valid(payload_json)
                                 AND json_type(payload_json) = 'object'),
            prev_hash     TEXT NOT NULL
                          CHECK (length(prev_hash) = 64
                                 AND prev_hash NOT GLOB '*[^0-9a-f]*'),
            hash          TEXT NOT NULL UNIQUE
                          CHECK (length(hash) = 64
                                 AND hash NOT GLOB '*[^0-9a-f]*')
        ) STRICT
        """
    )

    op.execute(
        """
        CREATE TRIGGER audit_chain_insert BEFORE INSERT ON audit_events
        WHEN NEW.seq <> (SELECT COALESCE(MAX(seq), 0) + 1 FROM audit_events)
          OR NEW.prev_hash <> COALESCE(
                 (SELECT hash FROM audit_events WHERE seq = NEW.seq - 1),
                 '0000000000000000000000000000000000000000000000000000000000000000'
             )
        BEGIN
            SELECT RAISE(ABORT, 'audit_events chain violation');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events is append-only');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_no_delete")
    op.execute("DROP TRIGGER audit_no_update")
    op.execute("DROP TRIGGER audit_chain_insert")
    op.execute("DROP TABLE audit_events")
