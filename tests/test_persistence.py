from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_pbx.persistence.connection import SQLiteConnectionFactory
from agent_pbx.persistence.repositories import AgentRepository, EventRepository
from agent_pbx.persistence.transactions import (
    ATOMIC_STORE_DELEGATES,
    ATOMIC_STORE_OPERATIONS,
    begin_immediate,
)
from agent_pbx.store import Store


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

    assert len(ATOMIC_STORE_OPERATIONS) == 32
    assert "append_event_if_changed" in ATOMIC_STORE_OPERATIONS
    assert "merge_agent_metadata" in ATOMIC_STORE_OPERATIONS
    assert ATOMIC_STORE_DELEGATES == {
        "acquire_tmux_writer_lease": "acquire_writer_lease",
        "append_event_if_changed": "append_if_changed",
        "begin_joplin_edit": "begin_edit",
        "claim_next_joplin_sync_job": "claim_next",
        "enqueue_joplin_sync": "enqueue",
        "mark_model_elevation_revert_pending": "mark_revert_pending",
        "request_or_reuse_model_elevation": "request_or_reuse",
        "record_joplin_conflict": "record_conflict",
        "start_joplin_log": "start_log",
        "stop_active_joplin_log": "stop_active_log",
        "update_model_elevation_transition": "update_transition",
    }


def test_event_repository_preserves_order_bounds_and_deduplication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pbx.sqlite"
    Store(path).init()
    repository = EventRepository(SQLiteConnectionFactory(path))

    first = repository.append(
        "state",
        {"value": 1},
        "agent-1",
        created_at=1.0,
    )
    assert repository.append_if_changed(
        "state",
        {"value": 1},
        "agent-1",
        created_at=2.0,
    ) is None
    second = repository.append_if_changed(
        "state",
        {"value": 2},
        "agent-1",
        created_at=3.0,
    )

    assert second is not None
    assert [item["event_id"] for item in repository.list_events()] == [
        first["event_id"],
        second["event_id"],
    ]
    assert [
        item["payload"]["value"]
        for item in repository.list_subject_events("agent-1", limit=1)
    ] == [2]
    assert repository.list_recent_events(limit=1) == [second]
    assert repository.bounds() == {
        "oldest_event_id": first["event_id"],
        "latest_event_id": second["event_id"],
        "event_count": 2,
    }


def test_agent_repository_preserves_base_row_and_simple_write_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pbx.sqlite"
    Store(path).init()
    connections = SQLiteConnectionFactory(path)
    repository = AgentRepository(connections)
    with connections.managed() as connection:
        repository.upsert_registration(
            connection,
            agent_id="agent-1",
            agent_type="caller",
            project="demo",
            name="Agent One",
            pbx_active=True,
            metadata_json=json.dumps({"cwd": str(tmp_path)}),
            current=10.0,
        )

    raw = repository.raw_agent("agent-1")
    assert raw is not None
    assert raw["project"] == "demo"
    assert raw["pbx_active"] == 1
    assert raw["metadata_json"] == json.dumps({"cwd": str(tmp_path)})

    assert repository.set_starred("agent-1", starred_at=11.0) is True
    assert repository.set_pbx_active("agent-1", active=False, current=12.0) is True
    with connections.managed() as connection:
        rows = repository.list_rows(connection, include_hidden=False)
    assert len(rows) == 1
    assert rows[0]["starred_at"] == 11.0
    assert rows[0]["pbx_active"] == 0
    assert rows[0]["last_seen_at"] == 12.0
