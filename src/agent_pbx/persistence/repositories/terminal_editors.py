from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory


class TerminalEditorRepository:
    """Persist managed terminal-editor runtime identity and state."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def upsert(
        self,
        runtime: dict[str, Any],
        *,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        verified: bool = False,
    ) -> dict[str, Any]:
        agent_id = str(runtime.get("agent_id") or "").strip()
        runtime_id = str(runtime.get("runtime_id") or "").strip()
        if not runtime_id:
            raise ValueError("terminal editor runtime_id is required")
        current = now_ts()
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO terminal_editor_runtimes(
                    runtime_id, agent_id, project, access_mode, profile_id,
                    path, root, socket_path, rpc_socket_path, rpc_enabled,
                    server_id, session_name, window_id, pane_id, pane_pid,
                    process_start_ticks, tmux_bin, state, last_error,
                    metadata_json, created_at, updated_at, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    runtime_id = excluded.runtime_id,
                    project = excluded.project,
                    access_mode = excluded.access_mode,
                    profile_id = excluded.profile_id,
                    path = excluded.path,
                    root = excluded.root,
                    socket_path = excluded.socket_path,
                    rpc_socket_path = excluded.rpc_socket_path,
                    rpc_enabled = excluded.rpc_enabled,
                    server_id = excluded.server_id,
                    session_name = excluded.session_name,
                    window_id = excluded.window_id,
                    pane_id = excluded.pane_id,
                    pane_pid = excluded.pane_pid,
                    process_start_ticks = excluded.process_start_ticks,
                    tmux_bin = excluded.tmux_bin,
                    state = excluded.state,
                    last_error = excluded.last_error,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    last_verified_at = COALESCE(
                        excluded.last_verified_at,
                        terminal_editor_runtimes.last_verified_at
                    )
                """,
                (
                    runtime_id,
                    agent_id,
                    str(runtime.get("project") or ""),
                    str(runtime.get("access_mode") or "edit"),
                    str(runtime.get("profile_id") or ""),
                    str(runtime.get("path") or ""),
                    str(runtime.get("root") or ""),
                    str(runtime.get("socket_path") or ""),
                    str(runtime.get("rpc_socket_path") or "") or None,
                    int(bool(runtime.get("rpc_enabled"))),
                    str(runtime.get("server_id") or ""),
                    str(runtime.get("session_name") or ""),
                    str(runtime.get("window_id") or "") or None,
                    str(runtime.get("pane_id") or ""),
                    runtime.get("pane_pid"),
                    runtime.get("process_start_ticks"),
                    str(runtime.get("tmux_bin") or ""),
                    str(runtime.get("state") or "ready"),
                    last_error,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    current if verified else None,
                ),
            )
        stored = self.get(agent_id)
        assert stored is not None
        return stored

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM terminal_editor_runtimes WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return self.from_row(row) if row is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._connections.managed() as connection:
            rows = connection.execute(
                "SELECT * FROM terminal_editor_runtimes ORDER BY updated_at DESC"
            ).fetchall()
        return [self.from_row(row) for row in rows]

    def update_state(
        self,
        agent_id: str,
        *,
        state: str,
        last_error: str | None = None,
        verified: bool = False,
        path: str | None = None,
    ) -> dict[str, Any] | None:
        current = now_ts()
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE terminal_editor_runtimes
                SET state = ?, last_error = ?, updated_at = ?,
                    last_verified_at = CASE WHEN ? THEN ? ELSE last_verified_at END,
                    path = COALESCE(?, path)
                WHERE agent_id = ?
                """,
                (
                    str(state),
                    last_error,
                    current,
                    int(verified),
                    current,
                    path,
                    agent_id,
                ),
            )
        return self.get(agent_id) if cursor.rowcount else None

    def delete(self, agent_id: str) -> bool:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                "DELETE FROM terminal_editor_runtimes WHERE agent_id = ?",
                (agent_id,),
            )
        return bool(cursor.rowcount)

    @staticmethod
    def from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw_metadata = data.pop("metadata_json", "{}")
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        data["rpc_enabled"] = bool(data.get("rpc_enabled"))
        return data
