from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory


class DurableHelperRepository:
    """Persist reviewed durable-helper bundles and lifecycle events."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def create(
        self,
        *,
        helper_id: str,
        name: str,
        object_sha256: str,
        object_bytes: int,
        source_path: str,
        manifest: dict[str, Any],
        provenance: dict[str, Any],
        scopes: list[str],
        kb_refs: list[str],
        status: str,
        created_by_operator_agent_id: str,
    ) -> dict[str, Any]:
        current = now_ts()
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO durable_helper_bundles
                    (helper_id, name, object_sha256, object_bytes, source_path,
                     manifest_json, provenance_json, scopes_json, kb_refs_json,
                     status, created_by_operator_agent_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    helper_id,
                    name,
                    object_sha256,
                    int(object_bytes),
                    source_path,
                    json.dumps(manifest, sort_keys=True),
                    json.dumps(provenance, sort_keys=True),
                    json.dumps(scopes, sort_keys=True),
                    json.dumps(kb_refs, sort_keys=True),
                    status,
                    created_by_operator_agent_id,
                    current,
                    current,
                ),
            )
            connection.execute(
                """
                INSERT INTO durable_helper_events
                    (helper_id, event_type, operator_agent_id, metadata_json, created_at)
                VALUES (?, 'proposed', ?, '{}', ?)
                """,
                (helper_id, created_by_operator_agent_id, current),
            )
        bundle = self.get(helper_id)
        if bundle is None:
            raise RuntimeError("durable helper insert failed")
        return bundle

    def get(self, helper_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM durable_helper_bundles WHERE helper_id = ?",
                (helper_id,),
            ).fetchone()
        return self.from_row(row) if row else None

    def find(
        self,
        *,
        name: str,
        object_sha256: str,
    ) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                """
                SELECT * FROM durable_helper_bundles
                WHERE name = ? AND object_sha256 = ?
                """,
                (name, object_sha256),
            ).fetchone()
        return self.from_row(row) if row else None

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if status is not None:
            where = "WHERE status = ?"
            params.append(status)
        params.append(min(max(int(limit), 1), 1000))
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM durable_helper_bundles
                {where}
                ORDER BY updated_at DESC, helper_id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self.from_row(row) for row in rows]

    def update(
        self,
        helper_id: str,
        *,
        status: str | None = None,
        validation_receipt: dict[str, Any] | None = None,
        reviewed_by_operator_agent_id: str | None = None,
        activated_by_operator_agent_id: str | None = None,
        event_type: str,
        operator_agent_id: str,
        event_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = now_ts()
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [current]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if validation_receipt is not None:
            assignments.append("validation_receipt_json = ?")
            values.append(json.dumps(validation_receipt, sort_keys=True))
        if reviewed_by_operator_agent_id is not None:
            assignments.append("reviewed_by_operator_agent_id = ?")
            values.append(reviewed_by_operator_agent_id)
        if activated_by_operator_agent_id is not None:
            assignments.append("activated_by_operator_agent_id = ?")
            values.append(activated_by_operator_agent_id)
        values.append(helper_id)
        with self._connections.managed() as connection:
            cursor = connection.execute(
                f"UPDATE durable_helper_bundles SET {', '.join(assignments)} "
                "WHERE helper_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
            connection.execute(
                """
                INSERT INTO durable_helper_events
                    (helper_id, event_type, operator_agent_id, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    helper_id,
                    event_type,
                    operator_agent_id,
                    json.dumps(event_metadata or {}, sort_keys=True),
                    current,
                ),
            )
        return self.get(helper_id)

    def list_events(
        self,
        *,
        helper_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if helper_id is not None:
            where = "WHERE helper_id = ?"
            params.append(helper_id)
        params.append(min(max(int(limit), 1), 2000))
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM durable_helper_events
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
        for column, target, fallback in (
            ("manifest_json", "manifest", {}),
            ("provenance_json", "provenance", {}),
            ("validation_receipt_json", "validation_receipt", None),
            ("scopes_json", "scopes", []),
            ("kb_refs_json", "kb_refs", []),
        ):
            raw = item.pop(column)
            try:
                value = json.loads(raw) if raw is not None else fallback
            except json.JSONDecodeError:
                value = fallback
            item[target] = value
        item["object_bytes"] = int(item.get("object_bytes") or 0)
        return item
