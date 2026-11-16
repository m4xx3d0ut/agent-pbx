from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory
from ..transactions import begin_immediate


class JoplinSessionRepository:
    """Persist Joplin response logs, local edits, and conflict records."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def start_log(
        self,
        *,
        agent_id: str,
        project: str,
        session_id: str,
        note_id: str,
        title: str,
    ) -> dict[str, Any]:
        log_id = str(uuid.uuid4())
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            connection.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = COALESCE(stopped_at, ?), updated_at = ?
                WHERE agent_id = ? AND active = 1
                """,
                (current, current, agent_id),
            )
            connection.execute(
                """
                INSERT INTO joplin_logs
                    (log_id, agent_id, project, session_id, note_id, title,
                     active, started_at, stopped_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?)
                """,
                (
                    log_id,
                    agent_id,
                    project,
                    session_id,
                    note_id,
                    title,
                    current,
                    current,
                ),
            )
            row = connection.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs WHERE log_id = ?
                """,
                (log_id,),
            ).fetchone()
            assert row is not None
            return self.log_from_row(row)

    def get_log(self, log_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs
                WHERE log_id = ?
                """,
                (log_id,),
            ).fetchone()
        return self.log_from_row(row) if row else None

    def get_active_log(self, agent_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs
                WHERE agent_id = ? AND active = 1
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return self.log_from_row(row) if row else None

    def stop_active_log(self, agent_id: str) -> dict[str, Any] | None:
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            active = connection.execute(
                """
                SELECT log_id FROM joplin_logs
                WHERE agent_id = ? AND active = 1
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            if active is None:
                return None
            log_id = str(active["log_id"])
            connection.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = ?, updated_at = ?
                WHERE log_id = ?
                """,
                (current, current, log_id),
            )
            row = connection.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs WHERE log_id = ?
                """,
                (log_id,),
            ).fetchone()
            assert row is not None
            return self.log_from_row(row)

    def touch_log(self, log_id: str) -> None:
        with self._connections.managed() as connection:
            connection.execute(
                "UPDATE joplin_logs SET updated_at = ? WHERE log_id = ?",
                (now_ts(), log_id),
            )

    def begin_edit(
        self,
        *,
        client_id: str,
        scope_key: str,
        note: dict[str, Any],
        agent_id: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        note_id = str(note.get("id") or "")
        if not note_id:
            raise ValueError("Joplin edit requires a note id")
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            active = connection.execute(
                """
                SELECT * FROM joplin_edit_sessions
                WHERE client_id = ? AND scope_key = ? AND note_id = ?
                  AND status IN ('editing', 'conflict')
                LIMIT 1
                """,
                (client_id, scope_key, note_id),
            ).fetchone()
            if active is not None:
                return self.edit_from_row(active)
            edit_id = str(uuid.uuid4())
            title = str(note.get("title") or "")
            body = str(note.get("body") or "")
            connection.execute(
                """
                INSERT INTO joplin_edit_sessions
                    (edit_id, client_id, scope_key, agent_id, project, note_id,
                     base_revision, base_updated_time, base_user_updated_time,
                     base_title_hash, base_body_hash, base_title, base_body,
                     draft_title, draft_body, dirty, status, sync_generation,
                     created_at, updated_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                        'editing', 0, ?, ?, NULL)
                """,
                (
                    edit_id,
                    client_id,
                    scope_key,
                    agent_id,
                    project,
                    note_id,
                    str(note.get("revision") or ""),
                    note.get("updated_time"),
                    note.get("user_updated_time"),
                    str(note.get("title_hash") or ""),
                    str(note.get("body_hash") or ""),
                    title,
                    body,
                    title,
                    body,
                    current,
                    current,
                ),
            )
            row = connection.execute(
                "SELECT * FROM joplin_edit_sessions WHERE edit_id = ?",
                (edit_id,),
            ).fetchone()
            assert row is not None
            return self.edit_from_row(row)

    def get_edit(self, edit_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM joplin_edit_sessions WHERE edit_id = ?",
                (edit_id,),
            ).fetchone()
        return self.edit_from_row(row) if row else None

    def update_edit_draft(
        self,
        edit_id: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE joplin_edit_sessions
                SET draft_title = ?, draft_body = ?, dirty = 1, updated_at = ?
                WHERE edit_id = ? AND status IN ('editing', 'conflict')
                """,
                (title, body, now_ts(), edit_id),
            )
        return self.get_edit(edit_id) if cursor.rowcount else None

    def complete_edit(
        self,
        edit_id: str,
        *,
        status: str = "saved",
        sync_generation: int = 0,
    ) -> dict[str, Any] | None:
        current = now_ts()
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE joplin_edit_sessions
                SET dirty = 0, status = ?, sync_generation = ?,
                    updated_at = ?, resolved_at = ?
                WHERE edit_id = ?
                """,
                (status, sync_generation, current, current, edit_id),
            )
        return self.get_edit(edit_id) if cursor.rowcount else None

    def rebase_edit(
        self,
        edit_id: str,
        *,
        current_note: dict[str, Any],
        draft_title: str,
        draft_body: str,
    ) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE joplin_edit_sessions
                SET base_revision = ?, base_updated_time = ?,
                    base_user_updated_time = ?, base_title_hash = ?,
                    base_body_hash = ?, base_title = ?, base_body = ?,
                    draft_title = ?, draft_body = ?, dirty = 1,
                    status = 'editing', updated_at = ?, resolved_at = NULL
                WHERE edit_id = ?
                """,
                (
                    str(current_note.get("revision") or ""),
                    current_note.get("updated_time"),
                    current_note.get("user_updated_time"),
                    str(current_note.get("title_hash") or ""),
                    str(current_note.get("body_hash") or ""),
                    str(current_note.get("title") or ""),
                    str(current_note.get("body") or ""),
                    draft_title,
                    draft_body,
                    now_ts(),
                    edit_id,
                ),
            )
        return self.get_edit(edit_id) if cursor.rowcount else None

    def record_conflict(
        self,
        *,
        edit_id: str,
        current_note: dict[str, Any],
        local_title: str,
        local_body: str,
        merged_title: str,
        merged_body: str,
    ) -> dict[str, Any]:
        conflict_id = str(uuid.uuid4())
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            edit_row = connection.execute(
                "SELECT * FROM joplin_edit_sessions WHERE edit_id = ?",
                (edit_id,),
            ).fetchone()
            if edit_row is None:
                raise ValueError("Joplin edit session does not exist")
            edit = self.edit_from_row(edit_row)
            connection.execute(
                """
                UPDATE joplin_edit_sessions
                SET status = 'conflict', draft_title = ?, draft_body = ?,
                    dirty = 1, updated_at = ?
                WHERE edit_id = ?
                """,
                (local_title, local_body, current, edit_id),
            )
            connection.execute(
                """
                INSERT INTO joplin_note_conflicts
                    (conflict_id, edit_id, note_id, base_revision,
                     current_revision, base_title, base_body, local_title,
                     local_body, current_title, current_body, merged_title,
                     merged_body, status, resolution, created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                        NULL, ?, NULL)
                """,
                (
                    conflict_id,
                    edit_id,
                    edit["note_id"],
                    edit["base_revision"],
                    str(current_note.get("revision") or ""),
                    edit["base_title"],
                    edit["base_body"],
                    local_title,
                    local_body,
                    str(current_note.get("title") or ""),
                    str(current_note.get("body") or ""),
                    merged_title,
                    merged_body,
                    current,
                ),
            )
            row = connection.execute(
                "SELECT * FROM joplin_note_conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
            assert row is not None
            return dict(row)

    def get_conflict(self, conflict_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM joplin_note_conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_open_conflict_for_edit(self, edit_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                """
                SELECT * FROM joplin_note_conflicts
                WHERE edit_id = ? AND status = 'open'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (edit_id,),
            ).fetchone()
        return dict(row) if row else None

    def resolve_conflict(
        self,
        conflict_id: str,
        *,
        resolution: str,
    ) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE joplin_note_conflicts
                SET status = 'resolved', resolution = ?, resolved_at = ?
                WHERE conflict_id = ? AND status = 'open'
                """,
                (resolution, now_ts(), conflict_id),
            )
        return self.get_conflict(conflict_id) if cursor.rowcount else None

    @staticmethod
    def log_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["active"] = bool(data["active"])
        return data

    @staticmethod
    def edit_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["dirty"] = bool(data.get("dirty"))
        return data
