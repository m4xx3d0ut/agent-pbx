from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory


class DynamicResourceRepository:
    """Persist typed dynamic-resource requests and resource-local events."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM dynamic_resource_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return self.from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        campaign_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(min(max(int(limit), 1), 1000))
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM dynamic_resource_requests
                {clause}
                ORDER BY created_at DESC, request_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self.from_row(row) for row in rows]

    def transition(
        self,
        request_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        operator_agent_id: str,
        event_type: str,
        approval: dict[str, Any] | None = None,
        provider_receipt: dict[str, Any] | None = None,
        cleanup_receipt: dict[str, Any] | None = None,
        event_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not expected_statuses:
            raise ValueError("dynamic resource update requires expected states")
        current = now_ts()
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, current]
        if approval is not None:
            assignments.append("approval_json = ?")
            values.append(json.dumps(approval, sort_keys=True))
        if provider_receipt is not None:
            assignments.append("provider_receipt_json = ?")
            values.append(json.dumps(provider_receipt, sort_keys=True))
        if cleanup_receipt is not None:
            assignments.append("cleanup_receipt_json = ?")
            values.append(json.dumps(cleanup_receipt, sort_keys=True))
        placeholders = ", ".join("?" for _ in expected_statuses)
        values.extend((request_id, *expected_statuses))
        with self._connections.managed() as connection:
            cursor = connection.execute(
                f"""
                UPDATE dynamic_resource_requests
                SET {', '.join(assignments)}
                WHERE request_id = ? AND status IN ({placeholders})
                """,
                values,
            )
            if cursor.rowcount != 1:
                raise ValueError("dynamic resource state changed")
            connection.execute(
                """
                INSERT INTO dynamic_resource_events(
                    request_id, event_type, operator_agent_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    event_type,
                    operator_agent_id,
                    json.dumps(event_metadata or {}, sort_keys=True),
                    current,
                ),
            )
        request = self.get(request_id)
        if request is None:
            raise RuntimeError("dynamic resource request disappeared")
        return request

    def list_events(
        self,
        *,
        request_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if request_id:
            where = "WHERE request_id = ?"
            params.append(request_id)
        params.append(min(max(int(limit), 1), 2000))
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM dynamic_resource_events
                {where}
                ORDER BY event_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                metadata = json.loads(item.pop("metadata_json") or "{}")
            except json.JSONDecodeError:
                metadata = {}
            item["metadata"] = metadata if isinstance(metadata, dict) else {}
            events.append(item)
        return events

    @staticmethod
    def from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for column, target in (
            ("spec_json", "spec"),
            ("approval_json", "approval"),
            ("provider_receipt_json", "provider_receipt"),
            ("cleanup_receipt_json", "cleanup_receipt"),
        ):
            raw = item.pop(column)
            try:
                value = json.loads(raw) if raw is not None else None
            except json.JSONDecodeError:
                value = None
            item[target] = value
        for key in ("ttl_seconds", "quota", "max_uses", "use_count"):
            item[key] = int(item.get(key) or 0)
        item["restart_required"] = bool(item.get("restart_required"))
        return item
