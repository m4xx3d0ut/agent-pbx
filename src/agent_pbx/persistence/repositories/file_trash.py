from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory


class FileTrashRepository:
    """Persist recoverable file-trash entries and prune batches."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def record(
        self,
        *,
        agent_id: str,
        project: str,
        root_path: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        trash_id = str(result.get("trash_id") or "")
        manifest = result.get("manifest")
        if not trash_id or not isinstance(manifest, dict):
            raise ValueError("trash result is missing durable metadata")
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO file_trash_entries
                    (trash_id, agent_id, project, root_path, original_path,
                     trash_path, kind, git_state, sha256, size, item_count,
                     status, actor, manifest_json, created_at, expires_at,
                     restored_at, pruned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'trashed', ?, ?, ?, ?,
                        NULL, NULL)
                """,
                (
                    trash_id,
                    agent_id,
                    project,
                    root_path,
                    str(result.get("path") or manifest.get("original_path") or ""),
                    str(result.get("trash_path") or "") or None,
                    str(result.get("kind") or manifest.get("kind") or "file"),
                    str(
                        result.get("git_state")
                        or manifest.get("git_state")
                        or "untracked"
                    ),
                    result.get("sha256"),
                    result.get("size"),
                    result.get("item_count"),
                    str(result.get("actor") or manifest.get("actor") or "operator"),
                    json.dumps(manifest, sort_keys=True),
                    float(result.get("created_at") or now_ts()),
                    result.get("expires_at"),
                ),
            )
        entry = self.get(trash_id)
        if entry is None:
            raise RuntimeError("file trash entry insert failed")
        return entry

    def get(self, trash_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM file_trash_entries WHERE trash_id = ?",
                (trash_id,),
            ).fetchone()
        return self.from_row(row) if row else None

    def list(
        self,
        agent_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 1000)
        where = "agent_id = ?"
        params: list[Any] = [agent_id]
        if status:
            where += " AND status = ?"
            params.append(status)
        params.append(safe_limit)
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM file_trash_entries
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self.from_row(row) for row in rows]

    def mark_restored(self, trash_id: str) -> dict[str, Any] | None:
        current = now_ts()
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE file_trash_entries
                SET status = 'restored', restored_at = ?
                WHERE trash_id = ? AND status = 'trashed'
                """,
                (current, trash_id),
            )
        return self.get(trash_id) if cursor.rowcount else None

    def record_prune_batch(
        self,
        *,
        agent_id: str,
        preview_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        batch_id = str(result.get("batch_id") or "")
        if not batch_id:
            raise ValueError("prune result is missing batch id")
        current = now_ts()
        trash_ids = [str(item) for item in result.get("trash_ids", []) if str(item)]
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO file_trash_prune_batches
                    (batch_id, agent_id, status, preview_token, snapshot_json,
                     created_at, applied_at, undone_at)
                VALUES (?, ?, 'applied', ?, ?, ?, ?, NULL)
                """,
                (
                    batch_id,
                    agent_id,
                    preview_token,
                    json.dumps(result, sort_keys=True),
                    current,
                    current,
                ),
            )
            if trash_ids:
                placeholders = ",".join("?" for _ in trash_ids)
                connection.execute(
                    f"""
                    UPDATE file_trash_entries
                    SET status = 'pruned', pruned_at = ?
                    WHERE agent_id = ? AND trash_id IN ({placeholders})
                    """,
                    (current, agent_id, *trash_ids),
                )
        return self.get_prune_batch(batch_id) or {}

    def get_prune_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM file_trash_prune_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["snapshot"] = self.json_object(data.pop("snapshot_json"))
        return data

    def mark_prune_undone(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_prune_batch(batch_id)
        if batch is None or batch.get("status") != "applied":
            return None
        trash_ids = [
            str(item)
            for item in batch.get("snapshot", {}).get("trash_ids", [])
            if str(item)
        ]
        current = now_ts()
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE file_trash_prune_batches
                SET status = 'undone', undone_at = ?
                WHERE batch_id = ? AND status = 'applied'
                """,
                (current, batch_id),
            )
            if cursor.rowcount and trash_ids:
                placeholders = ",".join("?" for _ in trash_ids)
                connection.execute(
                    f"""
                    UPDATE file_trash_entries
                    SET status = 'trashed', pruned_at = NULL
                    WHERE trash_id IN ({placeholders})
                    """,
                    trash_ids,
                )
        return self.get_prune_batch(batch_id) if cursor.rowcount else None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["manifest"] = cls.json_object(data.pop("manifest_json"))
        return data

    @staticmethod
    def json_object(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
