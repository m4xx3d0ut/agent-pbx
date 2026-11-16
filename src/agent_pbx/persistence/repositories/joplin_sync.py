from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory
from ..transactions import begin_immediate


_JOB_COLUMNS = """
    sync_id, status, reason, agent_id, note_id, error,
    created_at, started_at, finished_at, attempts,
    profile_key, note_ids_json, reasons_json, generation,
    progress_json, error_code
"""


class JoplinSyncRepository:
    """Persist durable Joplin synchronization jobs and status lanes."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def enqueue(
        self,
        *,
        reason: str,
        agent_id: str | None = None,
        note_id: str | None = None,
        profile_key: str = "default",
    ) -> dict[str, Any]:
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            existing = connection.execute(
                """
                SELECT sync_id, note_ids_json, reasons_json, generation
                FROM joplin_sync_jobs
                WHERE status = 'queued' AND profile_key = ? AND reason = ?
                  AND COALESCE(agent_id, '') = COALESCE(?, '')
                ORDER BY created_at ASC, sync_id ASC
                LIMIT 1
                """,
                (profile_key, reason, agent_id),
            ).fetchone()
            coalesced = existing is not None
            if existing is not None:
                note_ids = self.json_list(existing["note_ids_json"])
                reasons = self.json_list(existing["reasons_json"])
                if note_id and note_id not in note_ids:
                    note_ids.append(note_id)
                if reason not in reasons:
                    reasons.append(reason)
                sync_id = str(existing["sync_id"])
                connection.execute(
                    """
                    UPDATE joplin_sync_jobs
                    SET note_ids_json = ?, reasons_json = ?,
                        generation = generation + 1,
                        reason = ?, agent_id = COALESCE(agent_id, ?),
                        note_id = COALESCE(note_id, ?)
                    WHERE sync_id = ?
                    """,
                    (
                        json.dumps(note_ids),
                        json.dumps(reasons),
                        reason,
                        agent_id,
                        note_id,
                        sync_id,
                    ),
                )
            else:
                sync_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT INTO joplin_sync_jobs
                        (sync_id, status, reason, agent_id, note_id, error,
                         created_at, started_at, finished_at, attempts,
                         profile_key, note_ids_json, reasons_json, generation,
                         progress_json, error_code)
                    VALUES (?, 'queued', ?, ?, ?, NULL, ?, NULL, NULL, 0,
                            ?, ?, ?, 1, '{}', NULL)
                    """,
                    (
                        sync_id,
                        reason,
                        agent_id,
                        note_id,
                        current,
                        profile_key,
                        json.dumps([note_id] if note_id else []),
                        json.dumps([reason]),
                    ),
                )
            row = connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM joplin_sync_jobs WHERE sync_id = ?",
                (sync_id,),
            ).fetchone()
            assert row is not None
            job = self.from_row(row)
            if coalesced:
                job["coalesced"] = True
            return job

    def claim_next(self) -> dict[str, Any] | None:
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                """
                SELECT sync_id
                FROM joplin_sync_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, sync_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            sync_id = str(row["sync_id"])
            cursor = connection.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = 'running',
                    started_at = ?,
                    finished_at = NULL,
                    error = NULL,
                    error_code = NULL,
                    progress_json = '{"phase":"running"}',
                    attempts = attempts + 1
                WHERE sync_id = ? AND status = 'queued'
                """,
                (current, sync_id),
            )
            if not cursor.rowcount:
                return None
            claimed = connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM joplin_sync_jobs WHERE sync_id = ?",
                (sync_id,),
            ).fetchone()
            assert claimed is not None
            return self.from_row(claimed)

    def complete(
        self,
        sync_id: str,
        *,
        success: bool,
        error: str | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        current = now_ts()
        status = "succeeded" if success else "failed"
        with self._connections.managed() as connection:
            cursor = connection.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = ?, error = ?, error_code = ?, finished_at = ?,
                    progress_json = ?
                WHERE sync_id = ? AND status = 'running'
                """,
                (
                    status,
                    error,
                    error_code,
                    current,
                    json.dumps({"phase": status}),
                    sync_id,
                ),
            )
        return self.get(sync_id) if cursor.rowcount else None

    def get(self, sync_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM joplin_sync_jobs
                WHERE sync_id = ?
                """,
                (sync_id,),
            ).fetchone()
        return self.from_row(row) if row else None

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM joplin_sync_jobs
                ORDER BY created_at DESC, sync_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self.from_row(row) for row in rows]

    def latest_failed_write(self, agent_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM joplin_sync_jobs
                WHERE status = 'failed' AND agent_id = ?
                  AND (
                    COALESCE(note_id, '') <> ''
                    OR COALESCE(note_ids_json, '[]') NOT IN ('', '[]', 'null')
                  )
                ORDER BY COALESCE(finished_at, started_at, created_at) DESC,
                         sync_id DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return self.from_row(row) if row else None

    def active_write_for_targets(
        self,
        *,
        agent_id: str,
        reason: str,
        note_ids: list[str],
    ) -> dict[str, Any] | None:
        expected = {str(note_id).strip() for note_id in note_ids if str(note_id).strip()}
        with self._connections.managed() as connection:
            rows = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM joplin_sync_jobs
                WHERE status IN ('queued', 'running')
                  AND agent_id = ? AND reason = ?
                ORDER BY created_at ASC, sync_id ASC
                """,
                (agent_id, reason),
            ).fetchall()
        for row in rows:
            job = self.from_row(row)
            targets = {
                str(note_id).strip()
                for note_id in job.get("note_ids", [])
                if str(note_id).strip()
            }
            if expected <= targets:
                return job
        return None

    def status(self) -> dict[str, Any]:
        with self._connections.managed() as connection:
            count_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM joplin_sync_jobs
                GROUP BY status
                """
            ).fetchall()
            latest = self._latest_row(connection)
            latest_success = self._latest_row(connection, status="succeeded")
            latest_error = self._latest_row(connection, status="failed")
            write_sync = self._lane_status(connection, targeted=True)
            profile_validation = self._lane_status(connection, targeted=False)
        counts = {str(row["status"]): int(row["count"] or 0) for row in count_rows}
        return {
            "pending": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "latest": self.from_row(latest) if latest else None,
            "latest_success": self.from_row(latest_success) if latest_success else None,
            "latest_error": self.from_row(latest_error) if latest_error else None,
            "write_sync": write_sync,
            "profile_validation": profile_validation,
        }

    def _lane_status(
        self,
        connection: sqlite3.Connection,
        *,
        targeted: bool,
    ) -> dict[str, Any]:
        targeted_expression = """
            COALESCE(note_id, '') <> ''
            OR COALESCE(note_ids_json, '[]') NOT IN ('', '[]', 'null')
        """
        scope_clause = (
            f"({targeted_expression})" if targeted else f"NOT ({targeted_expression})"
        )
        count_rows = connection.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM joplin_sync_jobs
            WHERE {scope_clause}
            GROUP BY status
            """
        ).fetchall()
        counts = {str(row["status"]): int(row["count"] or 0) for row in count_rows}
        latest = self._latest_row(connection, scope_clause=scope_clause)
        latest_success = self._latest_row(
            connection,
            status="succeeded",
            scope_clause=scope_clause,
        )
        latest_error = self._latest_row(
            connection,
            status="failed",
            scope_clause=scope_clause,
        )
        if counts.get("running", 0):
            current_status = "running"
        elif counts.get("queued", 0):
            current_status = "queued"
        elif latest is not None:
            current_status = str(latest["status"] or "ready")
        else:
            current_status = "ready"
        return {
            "current_status": current_status,
            "pending": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "latest": self.from_row(latest) if latest else None,
            "latest_success": self.from_row(latest_success) if latest_success else None,
            "latest_error": self.from_row(latest_error) if latest_error else None,
        }

    @staticmethod
    def _latest_row(
        connection: sqlite3.Connection,
        *,
        status: str | None = None,
        scope_clause: str | None = None,
    ) -> sqlite3.Row | None:
        status_clause = "" if status is None else "AND status = ?"
        parameters: tuple[object, ...] = () if status is None else (status,)
        scope = scope_clause or "1 = 1"
        return connection.execute(
            f"""
            SELECT {_JOB_COLUMNS}
            FROM joplin_sync_jobs
            WHERE {scope} {status_clause}
            ORDER BY COALESCE(finished_at, started_at, created_at) DESC,
                     sync_id DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()

    @staticmethod
    def from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["note_ids"] = JoplinSyncRepository.json_list(
            data.pop("note_ids_json", "[]")
        )
        data["reasons"] = JoplinSyncRepository.json_list(
            data.pop("reasons_json", "[]")
        )
        progress_raw = data.pop("progress_json", "{}")
        try:
            progress = json.loads(progress_raw or "{}")
        except (TypeError, json.JSONDecodeError):
            progress = {}
        data["progress"] = progress if isinstance(progress, dict) else {}
        return data

    @staticmethod
    def json_list(value: Any) -> list[str]:
        try:
            decoded = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(decoded, list):
            return []
        return [str(item) for item in decoded if str(item)]
