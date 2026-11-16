from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..connection import SQLiteConnectionFactory
from ..transactions import begin_immediate


EVENT_SELECT = "event_id, type, subject_id, payload_json, created_at"


class EventRepository:
    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        subject_id: str | None,
        *,
        created_at: float,
    ) -> dict[str, Any]:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, subject_id, json.dumps(payload), created_at),
            )
            event_id = int(cursor.lastrowid)
        return self.event(event_id, event_type, subject_id, payload, created_at)

    def append_if_changed(
        self,
        event_type: str,
        payload: dict[str, Any],
        subject_id: str | None,
        *,
        created_at: float,
    ) -> dict[str, Any] | None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                """
                SELECT payload_json
                FROM events
                WHERE type = ? AND subject_id IS ?
                ORDER BY event_id DESC
                LIMIT 1
                """,
                (event_type, subject_id),
            ).fetchone()
            if row is not None:
                try:
                    previous = json.dumps(
                        json.loads(str(row["payload_json"])),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    previous = str(row["payload_json"])
                if previous == encoded:
                    return None
            cursor = connection.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, subject_id, encoded, created_at),
            )
            event_id = int(cursor.lastrowid)
        return self.event(event_id, event_type, subject_id, payload, created_at)

    def list_events(
        self,
        *,
        after_id: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT {EVENT_SELECT}
                FROM events
                WHERE event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
        return [self.event_from_row(row) for row in rows]

    def list_subject_events(
        self,
        subject_id: str,
        *,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 1000))
        clauses = ["subject_id = ?"]
        values: list[object] = [subject_id]
        if event_type:
            clauses.append("type = ?")
            values.append(event_type)
        values.append(bounded_limit)
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT {EVENT_SELECT}
                FROM events
                WHERE {' AND '.join(clauses)}
                ORDER BY event_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self.event_from_row(row) for row in reversed(rows)]

    def list_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT {EVENT_SELECT}
                FROM (
                    SELECT {EVENT_SELECT}
                    FROM events
                    ORDER BY event_id DESC
                    LIMIT ?
                )
                ORDER BY event_id ASC
                """,
                (limit,),
            ).fetchall()
        return [self.event_from_row(row) for row in rows]

    def bounds(self) -> dict[str, int | None]:
        with self._connections.managed() as connection:
            row = connection.execute(
                """
                SELECT MIN(event_id) AS oldest_event_id,
                       MAX(event_id) AS latest_event_id,
                       COUNT(*) AS event_count
                FROM events
                """
            ).fetchone()
        return {
            "oldest_event_id": int(row["oldest_event_id"])
            if row and row["oldest_event_id"] is not None
            else None,
            "latest_event_id": int(row["latest_event_id"])
            if row and row["latest_event_id"] is not None
            else None,
            "event_count": int(row["event_count"]) if row else 0,
        }

    @staticmethod
    def event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    @staticmethod
    def event(
        event_id: int,
        event_type: str,
        subject_id: str | None,
        payload: dict[str, Any],
        created_at: float,
    ) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "type": event_type,
            "subject_id": subject_id,
            "payload": payload,
            "created_at": created_at,
        }
