from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Mapping

from ...security import now_ts
from ..connection import SQLiteConnectionFactory
from ..transactions import begin_immediate


class RuntimeRepository:
    """Persist tmux runtime mappings, writer leases, and migration batches."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    @staticmethod
    def managed_stop_identity(mapping: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "server_id": str(mapping.get("server_id") or ""),
            "socket_path": str(mapping.get("socket_path") or ""),
            "session_name": str(mapping.get("session_name") or ""),
            "window_id": str(mapping.get("window_id") or ""),
            "pane_id": str(mapping.get("pane_id") or ""),
            "pane_pid": mapping.get("pane_pid"),
            "process_start_ticks": mapping.get("process_start_ticks"),
            "codex_session_id": mapping.get("codex_session_id"),
        }

    def upsert_mapping(
        self,
        *,
        entity_id: str,
        server_mode: str,
        server_id: str,
        socket_path: str,
        session_name: str,
        pane_id: str,
        window_id: str | None = None,
        window_name: str | None = None,
        pane_pid: int | None = None,
        process_start_ticks: int | None = None,
        codex_session_id: str | None = None,
        cwd: str | None = None,
        origin_client_tty: str | None = None,
        origin_session_name: str | None = None,
        state: str = "registered",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = now_ts()
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO tmux_runtime_mappings(
                    entity_id, server_mode, server_id, socket_path,
                    session_name, window_id, window_name, pane_id, pane_pid,
                    process_start_ticks, codex_session_id, cwd,
                    origin_client_tty, origin_session_name, state,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    server_mode = excluded.server_mode,
                    server_id = excluded.server_id,
                    socket_path = excluded.socket_path,
                    session_name = excluded.session_name,
                    window_id = excluded.window_id,
                    window_name = excluded.window_name,
                    pane_id = excluded.pane_id,
                    pane_pid = excluded.pane_pid,
                    process_start_ticks = excluded.process_start_ticks,
                    codex_session_id = excluded.codex_session_id,
                    cwd = excluded.cwd,
                    origin_client_tty = COALESCE(
                        NULLIF(excluded.origin_client_tty, ''),
                        tmux_runtime_mappings.origin_client_tty
                    ),
                    origin_session_name = COALESCE(
                        NULLIF(excluded.origin_session_name, ''),
                        tmux_runtime_mappings.origin_session_name
                    ),
                    state = excluded.state,
                    writer_client_id = CASE
                        WHEN tmux_runtime_mappings.server_id != excluded.server_id
                          OR tmux_runtime_mappings.pane_id != excluded.pane_id
                        THEN NULL ELSE tmux_runtime_mappings.writer_client_id END,
                    writer_lease_expires_at = CASE
                        WHEN tmux_runtime_mappings.server_id != excluded.server_id
                          OR tmux_runtime_mappings.pane_id != excluded.pane_id
                        THEN NULL ELSE tmux_runtime_mappings.writer_lease_expires_at END,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    entity_id,
                    server_mode,
                    server_id,
                    socket_path,
                    session_name,
                    window_id,
                    window_name,
                    pane_id,
                    pane_pid,
                    process_start_ticks,
                    codex_session_id,
                    cwd,
                    origin_client_tty,
                    origin_session_name,
                    state,
                    json.dumps(metadata or {}),
                    current,
                    current,
                ),
            )
        mapping = self.get_mapping(entity_id)
        assert mapping is not None
        return mapping

    def get_mapping(self, entity_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM tmux_runtime_mappings WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
        return self.mapping_from_row(row) if row is not None else None

    def bind_managed_codex_session(
        self,
        entity_id: str,
        *,
        codex_session_id: str,
        expected_pane_id: str,
        expected_bootstrap_id: str,
    ) -> dict[str, Any] | None:
        """Bind a late Codex registration to its exact managed runtime.

        Managed launch and rehydration create the tmux pane before Codex has a
        thread id.  The first caller registration may fill that one empty
        field, but it must not retarget an unmanaged, mismatched, or already
        bound runtime.
        """

        session_id = str(codex_session_id or "").strip()
        pane_id = str(expected_pane_id or "").strip()
        bootstrap_id = str(expected_bootstrap_id or "").strip()
        if not session_id or not pane_id or not bootstrap_id:
            return None
        current = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM tmux_runtime_mappings WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                return None
            current_mapping = self.mapping_from_row(row)
            metadata = current_mapping["metadata"]
            if not (
                metadata.get("managed_launch") is True
                or metadata.get("managed_rehydration") is True
            ):
                return None
            if str(row["pane_id"] or "") != pane_id:
                return None
            if str(metadata.get("managed_bootstrap_id") or "") != bootstrap_id:
                return None
            existing_session_id = str(row["codex_session_id"] or "").strip()
            if existing_session_id:
                return None
            connection.execute(
                """
                UPDATE tmux_runtime_mappings
                SET codex_session_id = ?, updated_at = ?
                WHERE entity_id = ? AND (codex_session_id IS NULL OR codex_session_id = '')
                """,
                (session_id, current, entity_id),
            )
        return self.get_mapping(entity_id)

    def list_mappings(self) -> list[dict[str, Any]]:
        with self._connections.managed() as connection:
            rows = connection.execute(
                "SELECT * FROM tmux_runtime_mappings ORDER BY updated_at DESC, entity_id"
            ).fetchall()
        return [self.mapping_from_row(row) for row in rows]

    def set_mapping_state(
        self,
        entity_id: str,
        *,
        state: str,
        metadata: dict[str, Any] | None = None,
        clear_writer_lease: bool = False,
    ) -> dict[str, Any]:
        mapping = self.get_mapping(entity_id)
        if mapping is None:
            raise ValueError("tmux runtime mapping not found")
        merged = dict(mapping.get("metadata") or {})
        merged.update(metadata or {})
        current = now_ts()
        with self._connections.managed() as connection:
            if clear_writer_lease:
                connection.execute(
                    """
                    UPDATE tmux_runtime_mappings
                    SET state = ?, metadata_json = ?, writer_client_id = NULL,
                        writer_lease_expires_at = NULL, updated_at = ?
                    WHERE entity_id = ?
                    """,
                    (state, json.dumps(merged), current, entity_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE tmux_runtime_mappings
                    SET state = ?, metadata_json = ?, updated_at = ?
                    WHERE entity_id = ?
                    """,
                    (state, json.dumps(merged), current, entity_id),
                )
        updated = self.get_mapping(entity_id)
        assert updated is not None
        return updated

    def acquire_writer_lease(
        self,
        entity_id: str,
        *,
        client_id: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        current = now_ts()
        expires_at = current + max(5.0, min(300.0, float(ttl_seconds)))
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                """
                SELECT state, writer_client_id, writer_lease_expires_at
                FROM tmux_runtime_mappings WHERE entity_id = ?
                """,
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ValueError("tmux runtime mapping not found")
            if str(row["state"] or "") == "stopping":
                raise ValueError("tmux runtime is stopping; writer acquisition is disabled")
            owner = str(row["writer_client_id"] or "")
            owner_expiry = row["writer_lease_expires_at"]
            if (
                owner
                and owner != client_id
                and owner_expiry is not None
                and float(owner_expiry) > current
            ):
                raise ValueError(f"tmux runtime writer is leased to {owner!r}")
            connection.execute(
                """
                UPDATE tmux_runtime_mappings
                SET writer_client_id = ?, writer_lease_expires_at = ?, updated_at = ?
                WHERE entity_id = ?
                """,
                (client_id, expires_at, current, entity_id),
            )
        mapping = self.get_mapping(entity_id)
        assert mapping is not None
        return mapping

    def release_writer_lease(
        self,
        entity_id: str,
        *,
        client_id: str,
    ) -> dict[str, Any]:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT writer_client_id FROM tmux_runtime_mappings WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                raise ValueError("tmux runtime mapping not found")
            owner = str(row["writer_client_id"] or "")
            if owner and owner != client_id:
                raise ValueError(f"tmux runtime writer is leased to {owner!r}")
            connection.execute(
                """
                UPDATE tmux_runtime_mappings
                SET writer_client_id = NULL, writer_lease_expires_at = NULL,
                    updated_at = ? WHERE entity_id = ?
                """,
                (now_ts(), entity_id),
            )
        mapping = self.get_mapping(entity_id)
        assert mapping is not None
        return mapping

    def delete_mapping(self, entity_id: str) -> bool:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                "DELETE FROM tmux_runtime_mappings WHERE entity_id = ?",
                (entity_id,),
            )
        return bool(cursor.rowcount)

    def create_migration_batch(
        self,
        *,
        target_cli_version: str,
        retention_until: float,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        batch_id = f"runtime-migration-{uuid.uuid4().hex}"
        created_at = now_ts()
        with self._connections.managed() as connection:
            connection.execute(
                """
                INSERT INTO runtime_migration_batches
                    (batch_id, status, target_cli_version, retention_until,
                     snapshot_json, results_json, created_at)
                VALUES (?, 'previewed', ?, ?, ?, '[]', ?)
                """,
                (
                    batch_id,
                    target_cli_version or None,
                    float(retention_until),
                    json.dumps(snapshot),
                    created_at,
                ),
            )
        batch = self.get_migration_batch(batch_id)
        assert batch is not None
        return batch

    def get_migration_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_migration_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return self.migration_batch_from_row(row) if row is not None else None

    def list_migration_batches(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(1000, int(limit)))
        with self._connections.managed() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_migration_batches
                ORDER BY created_at DESC LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [self.migration_batch_from_row(row) for row in rows]

    def update_migration_batch(
        self,
        batch_id: str,
        *,
        status: str,
        results: list[dict[str, Any]],
        applied: bool = False,
        rolled_back: bool = False,
    ) -> dict[str, Any]:
        if self.get_migration_batch(batch_id) is None:
            raise ValueError("runtime migration batch not found")
        current = now_ts()
        with self._connections.managed() as connection:
            connection.execute(
                """
                UPDATE runtime_migration_batches
                SET status = ?, results_json = ?,
                    applied_at = CASE WHEN ? THEN COALESCE(applied_at, ?) ELSE applied_at END,
                    rolled_back_at = CASE WHEN ? THEN COALESCE(rolled_back_at, ?) ELSE rolled_back_at END
                WHERE batch_id = ?
                """,
                (
                    status,
                    json.dumps(results),
                    int(applied),
                    current,
                    int(rolled_back),
                    current,
                    batch_id,
                ),
            )
        updated = self.get_migration_batch(batch_id)
        assert updated is not None
        return updated

    def prune_expired_migration_batches(self, *, current: float) -> int:
        with self._connections.managed() as connection:
            cursor = connection.execute(
                "DELETE FROM runtime_migration_batches WHERE retention_until < ?",
                (current,),
            )
        return int(cursor.rowcount)

    @staticmethod
    def mapping_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw_metadata = data.pop("metadata_json", "{}")
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        expires_at = data.get("writer_lease_expires_at")
        data["writer_lease_active"] = bool(
            data.get("writer_client_id")
            and expires_at is not None
            and float(expires_at) > now_ts()
        )
        return data

    @staticmethod
    def migration_batch_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for source, target, expected, default in (
            ("snapshot_json", "snapshot", dict, {}),
            ("results_json", "results", list, []),
        ):
            raw = data.pop(source, json.dumps(default))
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = default
            data[target] = decoded if isinstance(decoded, expected) else default
        data["retained"] = float(data.get("retention_until") or 0) >= now_ts()
        return data
