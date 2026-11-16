from __future__ import annotations

import sqlite3
from typing import Any

from ..connection import SQLiteConnectionFactory


AGENT_SELECT = """
    SELECT agent_id, agent_type, project, name, status, pbx_active, metadata_json,
           created_at, last_seen_at, last_poll_at,
           latest_report_seen_at, starred_at, dismissed_at
    FROM agents
"""


class AgentRepository:
    """Own base Agent rows while Store retains cross-domain projections."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    @staticmethod
    def registration_row(
        connection: sqlite3.Connection,
        agent_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT name, agent_type, project, metadata_json
            FROM agents
            WHERE agent_id = ?
            """,
            (agent_id,),
        ).fetchone()

    @staticmethod
    def upsert_registration(
        connection: sqlite3.Connection,
        *,
        agent_id: str,
        agent_type: str,
        project: str,
        name: str | None,
        pbx_active: bool,
        metadata_json: str,
        current: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO agents
                (agent_id, agent_type, project, name, status, pbx_active,
                 metadata_json, created_at, last_seen_at, dismissed_at)
            VALUES (?, ?, ?, ?, 'online', ?, ?, ?, ?, NULL)
            ON CONFLICT(agent_id) DO UPDATE SET
                agent_type = excluded.agent_type,
                project = excluded.project,
                name = excluded.name,
                status = 'online',
                pbx_active = excluded.pbx_active,
                metadata_json = excluded.metadata_json,
                last_seen_at = excluded.last_seen_at,
                dismissed_at = NULL
            """,
            (
                agent_id,
                agent_type,
                project,
                name,
                int(pbx_active),
                metadata_json,
                current,
                current,
            ),
        )

    @staticmethod
    def get_row(
        connection: sqlite3.Connection,
        agent_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"{AGENT_SELECT} WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()

    @staticmethod
    def list_rows(
        connection: sqlite3.Connection,
        *,
        include_hidden: bool,
    ) -> list[sqlite3.Row]:
        where_clause = "" if include_hidden else "WHERE dismissed_at IS NULL"
        return connection.execute(
            f"""
            {AGENT_SELECT}
            {where_clause}
            ORDER BY last_seen_at DESC, agent_id ASC
            """
        ).fetchall()

    def unhide(self, agent_id: str) -> bool:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                "UPDATE agents SET dismissed_at = NULL WHERE agent_id = ?",
                (agent_id,),
            )
            return cursor.rowcount > 0

    def set_pbx_active(
        self,
        agent_id: str,
        *,
        active: bool,
        current: float,
    ) -> bool:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE agents
                SET pbx_active = ?, last_seen_at = ?, dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (int(active), current, agent_id),
            )
            return cursor.rowcount > 0

    def set_starred(self, agent_id: str, *, starred_at: float | None) -> bool:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                "UPDATE agents SET starred_at = ? WHERE agent_id = ?",
                (starred_at, agent_id),
            )
            return cursor.rowcount > 0

    def raw_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Return an unprojected row for repository-level diagnostics/tests."""

        with self._connections.managed() as connection:
            row = self.get_row(connection, agent_id)
        return dict(row) if row is not None else None
