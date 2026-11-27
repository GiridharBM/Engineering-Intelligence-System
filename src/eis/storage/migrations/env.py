# SPDX-License-Identifier: Apache-2.0

"""Alembic environment for the local EIS SQLite database."""

from __future__ import annotations

from alembic import context

config = context.config
connection = config.attributes.get("connection")

if connection is not None:
    context.configure(
        connection=connection,
        target_metadata=None,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    from sqlalchemy import engine_from_config, pool

    section = config.get_section(config.config_ini_section)
    if section is None:
        raise RuntimeError("missing database configuration for Alembic")
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
