# SPDX-License-Identifier: Apache-2.0

"""SQLite storage foundation and migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from eis.config import Settings


@dataclass(frozen=True)
class SqliteDatabase:
    path: Path

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = Config()
        config.set_main_option(
            "script_location",
            str(Path(__file__).resolve().parent / "migrations"),
        )
        engine = create_engine(f"sqlite+pysqlite:///{self.path.as_posix()}")
        try:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            engine.dispose()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def session(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                if immediate:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            connection.close()


def default_database(settings: Settings) -> SqliteDatabase:
    return SqliteDatabase(settings.data_dir / "db" / "eis.sqlite3")
