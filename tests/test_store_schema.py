from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import SCHEMA_VERSION, Store


LEGACY_SCHEMA_MARKERS = (6, 19, 20, 28, 30, 39, 43, 44, 45)


def _schema_snapshot(path: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(path) as conn:
        return [
            (str(kind), str(name), str(table), str(sql))
            for kind, name, table, sql in conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                ORDER BY type, name
                """
            )
        ]


def test_empty_schema_bootstrap_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "pbx.sqlite"
    store = Store(path)

    store.init()
    first = _schema_snapshot(path)
    store.init()

    assert store.schema_version() == SCHEMA_VERSION
    assert _schema_snapshot(path) == first


@pytest.mark.parametrize("legacy_version", LEGACY_SCHEMA_MARKERS)
def test_reviewed_legacy_markers_upgrade_without_schema_or_data_drift(
    tmp_path: Path,
    legacy_version: int,
) -> None:
    path = tmp_path / f"pbx-{legacy_version}.sqlite"
    store = Store(path)
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id=f"legacy-{legacy_version}",
            project="schema-characterization",
            metadata={"cwd": str(tmp_path)},
        )
    )
    current_schema = _schema_snapshot(path)
    with store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(legacy_version),),
        )

    Store(path).init()
    reopened = Store(path)

    assert reopened.schema_version() == SCHEMA_VERSION
    assert _schema_snapshot(path) == current_schema
    assert reopened.get_agent(f"legacy-{legacy_version}") is not None
