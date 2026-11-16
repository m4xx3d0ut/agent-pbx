from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_pbx.persistence.connection import SQLiteConnectionFactory
from agent_pbx.persistence.transactions import (
    ATOMIC_STORE_OPERATIONS,
    begin_immediate,
)


def test_connection_factory_preserves_store_connection_contract(
    tmp_path: Path,
) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "nested" / "pbx.sqlite")

    with factory.managed() as connection:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.isolation_level == ""
        assert connection.in_transaction is False

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_managed_connection_commits_and_rolls_back(tmp_path: Path) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "pbx.sqlite")
    with factory.managed() as connection:
        connection.execute("CREATE TABLE records(value TEXT NOT NULL)")
        connection.execute("INSERT INTO records(value) VALUES ('committed')")

    with pytest.raises(RuntimeError, match="rollback fixture"):
        with factory.managed() as connection:
            connection.execute("INSERT INTO records(value) VALUES ('rolled-back')")
            raise RuntimeError("rollback fixture")

    with factory.managed() as connection:
        values = [
            str(row["value"])
            for row in connection.execute("SELECT value FROM records").fetchall()
        ]
    assert values == ["committed"]


def test_begin_immediate_uses_reviewed_atomic_operation_manifest(
    tmp_path: Path,
) -> None:
    factory = SQLiteConnectionFactory(tmp_path / "pbx.sqlite")
    with factory.managed() as connection:
        begin_immediate(connection)
        assert connection.in_transaction is True

    assert len(ATOMIC_STORE_OPERATIONS) == 26
    assert "append_event_if_changed" in ATOMIC_STORE_OPERATIONS
    assert "merge_agent_metadata" in ATOMIC_STORE_OPERATIONS
