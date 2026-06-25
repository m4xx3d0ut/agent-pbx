from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import (
    AgentRegisterRequest,
    CommandCreateRequest,
    ReportCreateRequest,
    plan_options_to_jsonable,
)
from .security import hash_secret, now_ts


SCHEMA_VERSION = 12
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4
POLL_BASE_TOKEN_ESTIMATE = 80
DELIVERED_COMMAND_TOKEN_ESTIMATE = 120
USAGE_WARN_TOKENS_PER_HOUR = 10_000
POLL_WARN_PER_HOUR = 24
STALE_WORKING_SECONDS = 600
STALE_WORKING_STATUSES = {"running", "working"}


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class TokenRecord:
    token_hash: str
    kind: str
    label: str | None
    created_at: float
    expires_at: float | None
    revoked_at: float | None


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL
                );

                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    agent_type TEXT NOT NULL DEFAULT 'caller',
                    project TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL,
                    pbx_active INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_poll_at REAL,
                    latest_report_seen_at REAL,
                    starred_at REAL,
                    dismissed_at REAL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    needs_input INTEGER NOT NULL DEFAULT 0,
                    plan_options_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    claimed_at REAL,
                    acked_at REAL,
                    result_json TEXT,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS poll_events (
                    poll_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    delivered_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    subject_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS joplin_logs (
                    log_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    note_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    stopped_at REAL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS joplin_sync_jobs (
                    sync_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    agent_id TEXT,
                    note_id TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    operator_agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    criteria_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'running',
                    summary TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaign_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    target_agent_id TEXT NOT NULL,
                    operator_fork_id TEXT,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    criteria_json TEXT NOT NULL DEFAULT '[]',
                    state TEXT NOT NULL DEFAULT 'pending',
                    last_report_id TEXT,
                    last_command_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaign_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    assignment_id TEXT,
                    operator_agent_id TEXT NOT NULL,
                    target_agent_id TEXT,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    report_id TEXT,
                    command_id TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS operator_forks (
                    operator_fork_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    fork_agent_id TEXT NOT NULL UNIQUE,
                    source_caller_agent_id TEXT NOT NULL,
                    source_codex_session_id TEXT NOT NULL,
                    fork_codex_session_id TEXT,
                    campaign_id TEXT,
                    cwd TEXT NOT NULL,
                    codex_home TEXT,
                    codex_host_id TEXT,
                    tmux_pane_id TEXT,
                    status TEXT NOT NULL DEFAULT 'starting',
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(fork_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_caller_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_fork_edges (
                    edge_id TEXT PRIMARY KEY,
                    from_fork_id TEXT NOT NULL,
                    to_fork_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(from_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(to_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE CASCADE,
                    UNIQUE(from_fork_id, to_fork_id, edge_type)
                );

                CREATE INDEX IF NOT EXISTS idx_operator_campaigns_operator_updated
                    ON operator_campaigns(operator_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_campaigns_status_updated
                    ON operator_campaigns(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_assignments_campaign_state
                    ON operator_campaign_assignments(campaign_id, state);
                CREATE INDEX IF NOT EXISTS idx_operator_assignments_target_updated
                    ON operator_campaign_assignments(target_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_events_campaign_created
                    ON operator_campaign_events(campaign_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_events_assignment_created
                    ON operator_campaign_events(assignment_id, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_forks_source_session
                    ON operator_forks(
                        logical_operator_agent_id,
                        source_caller_agent_id,
                        source_codex_session_id
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_forks_logical_updated
                    ON operator_forks(logical_operator_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_forks_source_updated
                    ON operator_forks(source_caller_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_forks_fork_agent
                    ON operator_forks(fork_agent_id);
                CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_from
                    ON operator_fork_edges(from_fork_id);
                CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_to
                    ON operator_fork_edges(to_fork_id);
                """
            )
            previous_schema_version = self._schema_version(conn)
            conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = 'queued', started_at = NULL
                WHERE status = 'running'
                """
            )
            self._ensure_column(conn, "agents", "last_poll_at", "REAL")
            self._ensure_column(
                conn,
                "agents",
                "agent_type",
                "TEXT NOT NULL DEFAULT 'caller'",
            )
            self._ensure_column(conn, "agents", "latest_report_seen_at", "REAL")
            self._ensure_column(conn, "agents", "starred_at", "REAL")
            self._ensure_column(conn, "agents", "dismissed_at", "REAL")
            self._ensure_column(
                conn,
                "agents",
                "pbx_active",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                conn,
                "reports",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "operator_campaign_assignments",
                "operator_fork_id",
                "TEXT",
            )
            self._backfill_agent_type(conn)
            if previous_schema_version < 7:
                self._backfill_latest_report_seen(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _backfill_latest_report_seen(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE agents
            SET latest_report_seen_at = (
                SELECT MAX(reports.created_at)
                FROM reports
                WHERE reports.agent_id = agents.agent_id
            )
            WHERE latest_report_seen_at IS NULL
              AND EXISTS (
                SELECT 1
                FROM reports
                WHERE reports.agent_id = agents.agent_id
              )
            """
        )

    @staticmethod
    def _backfill_agent_type(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT agent_id, agent_type, metadata_json FROM agents"
        ).fetchall()
        for row in rows:
            agent_type = str(row["agent_type"] or "").strip().lower()
            metadata: dict[str, Any] = {}
            try:
                decoded = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                metadata = decoded
            metadata_type = str(metadata.get("agent_type") or "").strip().lower()
            resolved = (
                metadata_type
                if metadata_type in {"caller", "operator"}
                else agent_type
            )
            if resolved not in {"caller", "operator"}:
                resolved = "caller"
            if metadata_type or resolved == "operator":
                metadata["agent_type"] = resolved
            conn.execute(
                """
                UPDATE agents
                SET agent_type = ?, metadata_json = ?
                WHERE agent_id = ?
                """,
                (resolved, json.dumps(metadata), row["agent_id"]),
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def has_tokens(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tokens WHERE revoked_at IS NULL LIMIT 1"
            ).fetchone()
            return row is not None

    def add_token(
        self,
        raw_token: str,
        *,
        kind: str,
        label: str | None = None,
        ttl_seconds: int | None = None,
    ) -> TokenRecord:
        created_at = now_ts()
        expires_at = created_at + ttl_seconds if ttl_seconds else None
        record = TokenRecord(
            token_hash=hash_secret(raw_token),
            kind=kind,
            label=label,
            created_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tokens
                    (token_hash, kind, label, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    record.token_hash,
                    record.kind,
                    record.label,
                    record.created_at,
                    record.expires_at,
                ),
            )
        return record

    def verify_token(self, raw_token: str) -> TokenRecord | None:
        token_hash = hash_secret(raw_token)
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT token_hash, kind, label, created_at, expires_at, revoked_at
                FROM tokens
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        record = TokenRecord(**dict(row))
        if record.expires_at is not None and record.expires_at < current:
            return None
        return record

    def create_pairing_code(self, code: str, *, ttl_seconds: int) -> None:
        created_at = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pairing_codes
                    (code_hash, created_at, expires_at, used_at)
                VALUES (?, ?, ?, NULL)
                """,
                (hash_secret(code), created_at, created_at + ttl_seconds),
            )

    def consume_pairing_code(self, code: str) -> bool:
        code_hash = hash_secret(code)
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT code_hash FROM pairing_codes
                WHERE code_hash = ? AND used_at IS NULL AND expires_at >= ?
                """,
                (code_hash, current),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE code_hash = ?",
                (current, code_hash),
            )
            return True

    def append_event(
        self, event_type: str, payload: dict[str, Any], subject_id: str | None = None
    ) -> dict[str, Any]:
        created_at = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, subject_id, json.dumps(payload), created_at),
            )
            event_id = int(cursor.lastrowid)
        return {
            "event_id": event_id,
            "type": event_type,
            "subject_id": subject_id,
            "payload": payload,
            "created_at": created_at,
        }

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, type, subject_id, payload_json, created_at
                FROM events
                WHERE event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, type, subject_id, payload_json, created_at
                FROM (
                    SELECT event_id, type, subject_id, payload_json, created_at
                    FROM events
                    ORDER BY event_id DESC
                    LIMIT ?
                )
                ORDER BY event_id ASC
                """,
                (limit,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def register_agent(self, request: AgentRegisterRequest) -> dict[str, Any]:
        current = now_ts()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT name, agent_type, project, metadata_json
                FROM agents
                WHERE agent_id = ?
                """,
                (request.agent_id,),
            ).fetchone()
            request_metadata = dict(request.metadata)
            existing_type = (
                self._normalized_agent_type(existing["agent_type"])
                if existing
                else "caller"
            )
            requested_type = self._normalized_agent_type(request.agent_type)
            has_metadata_agent_type = "agent_type" in request_metadata
            metadata_type = (
                self._normalized_agent_type(request_metadata.get("agent_type"))
                if has_metadata_agent_type
                else ""
            )
            preserve_existing_operator_identity = (
                existing_type == "operator"
                and requested_type == "caller"
                and not has_metadata_agent_type
            )
            if requested_type == "operator" or metadata_type == "operator":
                agent_type = "operator"
            elif existing_type == "operator" and metadata_type != "caller":
                agent_type = "operator"
            else:
                agent_type = "caller"
            if agent_type == "operator" or has_metadata_agent_type:
                request_metadata["agent_type"] = agent_type
            if preserve_existing_operator_identity:
                for key in (
                    "agent_type",
                    "operator_role",
                    "logical_operator_id",
                    "source_caller_agent_id",
                    "source_codex_session_id",
                    "cwd",
                    "tmux_pane_id",
                    "tmux_session",
                    "launched_by",
                    "mcp_url",
                    "token_env",
                    "codex_command",
                    "operator_session_history",
                    "last_resume_codex_session_id",
                ):
                    request_metadata.pop(key, None)
            metadata = self._merged_agent_metadata(
                existing["metadata_json"] if existing else None,
                request_metadata,
            )
            metadata_json = json.dumps(metadata)
            name = request.name
            project = request.project
            if preserve_existing_operator_identity and existing:
                name = existing["name"]
                project = existing["project"]
            elif name is None and existing:
                name = existing["name"]
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                     created_at, last_seen_at, dismissed_at)
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
                    request.agent_id,
                    agent_type,
                    project,
                    name,
                    int(request.pbx_active),
                    metadata_json,
                    current,
                    current,
                ),
            )
        return self.get_agent(request.agent_id) or {}

    @staticmethod
    def _normalized_agent_type(value: Any) -> str:
        agent_type = str(value or "caller").strip().lower()
        if agent_type == "operator":
            return "operator"
        return "caller"

    @staticmethod
    def _merged_agent_metadata(
        existing_json: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        existing: dict[str, Any] = {}
        if existing_json:
            try:
                decoded = json.loads(existing_json)
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                existing = decoded

        merged = dict(existing)
        for key, value in metadata.items():
            if value is None:
                continue
            if (
                key == "cwd"
                and not _non_empty_string(value)
                and _non_empty_string(merged.get("cwd"))
            ):
                continue
            merged[key] = value
        Store._backfill_agent_cwd(merged)
        return merged

    @staticmethod
    def _backfill_agent_cwd(metadata: dict[str, Any]) -> None:
        if _non_empty_string(metadata.get("cwd")):
            return
        repo = metadata.get("repo")
        if not _non_empty_string(repo):
            return
        repo_path = Path(str(repo)).expanduser()
        if repo_path.is_absolute() and repo_path.is_dir():
            metadata["cwd"] = str(repo_path)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, starred_at, dismissed_at
                FROM agents
                WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
        return self._agent_from_row(row) if row else None

    def list_agents(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        where_clause = "" if include_hidden else "WHERE dismissed_at IS NULL"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, starred_at, dismissed_at
                FROM agents
                {where_clause}
                ORDER BY last_seen_at DESC, agent_id ASC
                """
            ).fetchall()
            agents = [self._agent_from_row(row) for row in rows]
            for agent in agents:
                self._add_latest_report_summary(conn, agent)
                self._add_queue_summary(conn, agent)
                self._add_campaign_summary(conn, agent)
                self._add_usage_summary(conn, agent)
        return agents

    def unhide_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def dismiss_agent(
        self, agent_id: str, *, delete_thread: bool = False
    ) -> dict[str, Any] | None:
        agent = self.get_agent(agent_id)
        if agent is None:
            return None
        current = now_ts()
        with self.connect() as conn:
            if delete_thread:
                conn.execute("DELETE FROM reports WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM commands WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM poll_events WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM joplin_logs WHERE agent_id = ?", (agent_id,))
                conn.execute(
                    "DELETE FROM joplin_sync_jobs WHERE agent_id = ?",
                    (agent_id,),
                )
            cursor = conn.execute(
                """
                UPDATE agents
                SET dismissed_at = ?, last_seen_at = ?
                WHERE agent_id = ?
                """,
                (current, current, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def set_agent_pbx_active(self, agent_id: str, active: bool) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET pbx_active = ?, last_seen_at = ?, dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (int(active), current, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def create_report(
        self, agent_id: str, request: ReportCreateRequest
    ) -> dict[str, Any]:
        report_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agents
                SET status = ?, last_seen_at = ?, dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (request.status, current, agent_id),
            )
            conn.execute(
                """
                INSERT INTO reports
                    (report_id, agent_id, project, status, summary, detail,
                     needs_input, plan_options_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    agent_id,
                    request.project,
                    request.status,
                    request.summary,
                    request.detail,
                    int(request.needs_input),
                    json.dumps(plan_options_to_jsonable(request.plan_options)),
                    json.dumps(request.metadata),
                    current,
                ),
            )
        report = self.get_report(report_id)
        if report is None:
            raise RuntimeError("report insert failed")
        return report

    def mark_latest_report_seen(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT created_at
                FROM reports
                WHERE agent_id = ?
                ORDER BY created_at DESC, report_id DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM agents WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return self.get_agent(agent_id) if exists is not None else None
            seen_at = float(row["created_at"])
            cursor = conn.execute(
                """
                UPDATE agents
                SET latest_report_seen_at = MAX(
                    COALESCE(latest_report_seen_at, 0),
                    ?
                )
                WHERE agent_id = ?
                """,
                (seen_at, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def set_agent_starred(
        self, agent_id: str, *, starred: bool
    ) -> dict[str, Any] | None:
        starred_at = now_ts() if starred else None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET starred_at = ?
                WHERE agent_id = ?
                """,
                (starred_at, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def start_joplin_log(
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
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = COALESCE(stopped_at, ?), updated_at = ?
                WHERE agent_id = ? AND active = 1
                """,
                (current, current, agent_id),
            )
            conn.execute(
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
        log = self.get_joplin_log(log_id)
        if log is None:
            raise RuntimeError("joplin log insert failed")
        return log

    def get_joplin_log(self, log_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs
                WHERE log_id = ?
                """,
                (log_id,),
            ).fetchone()
        return self._joplin_log_from_row(row) if row else None

    def get_active_joplin_log(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
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
        return self._joplin_log_from_row(row) if row else None

    def stop_active_joplin_log(self, agent_id: str) -> dict[str, Any] | None:
        active = self.get_active_joplin_log(agent_id)
        if active is None:
            return None
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = ?, updated_at = ?
                WHERE log_id = ?
                """,
                (current, current, active["log_id"]),
            )
        return self.get_joplin_log(active["log_id"])

    def touch_joplin_log(self, log_id: str) -> None:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                "UPDATE joplin_logs SET updated_at = ? WHERE log_id = ?",
                (current, log_id),
            )

    def enqueue_joplin_sync(
        self,
        *,
        reason: str,
        agent_id: str | None = None,
        note_id: str | None = None,
    ) -> dict[str, Any]:
        sync_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO joplin_sync_jobs
                    (sync_id, status, reason, agent_id, note_id, error,
                     created_at, started_at, finished_at, attempts)
                VALUES (?, 'queued', ?, ?, ?, NULL, ?, NULL, NULL, 0)
                """,
                (sync_id, reason, agent_id, note_id, current),
            )
        job = self.get_joplin_sync_job(sync_id)
        if job is None:
            raise RuntimeError("joplin sync job insert failed")
        return job

    def claim_next_joplin_sync_job(self) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
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
            cursor = conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = 'running',
                    started_at = ?,
                    finished_at = NULL,
                    error = NULL,
                    attempts = attempts + 1
                WHERE sync_id = ? AND status = 'queued'
                """,
                (current, sync_id),
            )
            if not cursor.rowcount:
                return None
        return self.get_joplin_sync_job(sync_id)

    def complete_joplin_sync_job(
        self,
        sync_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        current = now_ts()
        status = "succeeded" if success else "failed"
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = ?, error = ?, finished_at = ?
                WHERE sync_id = ? AND status = 'running'
                """,
                (status, error, current, sync_id),
            )
        return self.get_joplin_sync_job(sync_id) if cursor.rowcount else None

    def get_joplin_sync_job(self, sync_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE sync_id = ?
                """,
                (sync_id,),
            ).fetchone()
        return self._joplin_sync_job_from_row(row) if row else None

    def list_joplin_sync_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                ORDER BY created_at DESC, sync_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._joplin_sync_job_from_row(row) for row in rows]

    def joplin_sync_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            count_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM joplin_sync_jobs
                GROUP BY status
                """
            ).fetchall()
            latest = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                ORDER BY COALESCE(finished_at, started_at, created_at) DESC,
                         sync_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_success = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE status = 'succeeded'
                ORDER BY finished_at DESC, sync_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_error = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE status = 'failed'
                ORDER BY finished_at DESC, sync_id DESC
                LIMIT 1
                """
            ).fetchone()
        counts = {str(row["status"]): int(row["count"] or 0) for row in count_rows}
        return {
            "pending": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "latest": self._joplin_sync_job_from_row(latest) if latest else None,
            "latest_success": (
                self._joplin_sync_job_from_row(latest_success)
                if latest_success
                else None
            ),
            "latest_error": (
                self._joplin_sync_job_from_row(latest_error)
                if latest_error
                else None
            ),
        }

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                WHERE report_id = ?
                """,
                (report_id,),
            ).fetchone()
        return self._report_from_row(row) if row else None

    def list_reports(self, agent_id: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        if agent_id is None:
            query = """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                ORDER BY created_at DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (safe_limit,)
        else:
            query = """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """
            params = (agent_id, safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._report_from_row(row) for row in rows]

    def list_thread(self, agent_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 200)
        reports = self.list_reports(agent_id, limit=safe_limit)
        with self.connect() as conn:
            command_rows = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_id, safe_limit),
            ).fetchall()
        commands = [self._command_from_row(row) for row in command_rows]
        items = [self._thread_report(report) for report in reports]
        items.extend(
            self._thread_command(command)
            for command in commands
            if command is not None and command["agent_id"] is not None
        )
        latest = sorted(
            items,
            key=lambda item: (float(item["created_at"]), str(item["item_id"])),
            reverse=True,
        )[:safe_limit]
        return sorted(
            latest,
            key=lambda item: (float(item["created_at"]), str(item["item_id"])),
        )

    def create_operator_campaign(
        self,
        *,
        operator_agent_id: str,
        title: str,
        objective: str,
        criteria: list[str],
        assignments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_campaigns
                    (campaign_id, operator_agent_id, title, objective,
                     criteria_json, status, summary, created_at, updated_at,
                     completed_at)
                VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, ?, NULL)
                """,
                (
                    campaign_id,
                    operator_agent_id,
                    title,
                    objective,
                    json.dumps(criteria),
                    current,
                    current,
                ),
            )
            for assignment in assignments:
                assignment_id = str(uuid.uuid4())
                target_agent_id = str(assignment["target_agent_id"])
                assignment_title = str(
                    assignment.get("title") or f"Assignment for {target_agent_id}"
                )
                conn.execute(
                    """
                    INSERT INTO operator_campaign_assignments
                        (assignment_id, campaign_id, target_agent_id, operator_fork_id, title,
                         prompt, criteria_json, state, last_report_id,
                         last_command_id, created_at, updated_at, completed_at)
                    VALUES (?, ?, ?, NULL, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        assignment_id,
                        campaign_id,
                        target_agent_id,
                        assignment_title,
                        str(assignment["prompt"]),
                        json.dumps(assignment.get("criteria") or []),
                        current,
                        current,
                    ),
                )
            conn.execute(
                """
                INSERT INTO operator_campaign_events
                    (campaign_id, assignment_id, operator_agent_id,
                     target_agent_id, event_type, summary, detail_json,
                     report_id, command_id, created_at)
                VALUES (?, NULL, ?, NULL, 'campaign_started', ?, ?, NULL, NULL, ?)
                """,
                (
                    campaign_id,
                    operator_agent_id,
                    f"Campaign started: {title}",
                    json.dumps({"objective": objective, "criteria": criteria}),
                    current,
                ),
            )
        campaign = self.get_operator_campaign(campaign_id)
        if campaign is None:
            raise RuntimeError("operator campaign insert failed")
        return campaign

    def get_operator_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        campaigns = self.list_operator_campaigns(campaign_id=campaign_id, limit=1)
        return campaigns[0] if campaigns else None

    def list_operator_campaigns(
        self,
        *,
        operator_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        include_events: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 200)
        where: list[str] = []
        params: list[Any] = []
        if operator_agent_id:
            where.append("operator_agent_id = ?")
            params.append(operator_agent_id)
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT campaign_id, operator_agent_id, title, objective,
                   criteria_json, status, summary, created_at, updated_at,
                   completed_at
            FROM operator_campaigns
            {clause}
            ORDER BY updated_at DESC, campaign_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        campaigns = [self._operator_campaign_from_row(row) for row in rows]
        for campaign in campaigns:
            campaign["assignments"] = self.list_operator_campaign_assignments(
                campaign_id=campaign["campaign_id"]
            )
            campaign["events"] = (
                self.list_operator_campaign_events(
                    campaign_id=campaign["campaign_id"],
                    limit=25,
                )
                if include_events
                else []
            )
        return campaigns

    def list_operator_campaign_assignments(
        self,
        *,
        campaign_id: str | None = None,
        target_agent_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if target_agent_id:
            where.append("target_agent_id = ?")
            params.append(target_agent_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT assignment_id, campaign_id, target_agent_id, operator_fork_id,
                   title, prompt,
                   criteria_json, state, last_report_id, last_command_id,
                   created_at, updated_at, completed_at
            FROM operator_campaign_assignments
            {clause}
            ORDER BY updated_at DESC, assignment_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_assignment_from_row(row) for row in rows]

    def get_operator_campaign_assignment(
        self,
        assignment_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT assignment_id, campaign_id, target_agent_id, operator_fork_id,
                       title, prompt,
                       criteria_json, state, last_report_id, last_command_id,
                       created_at, updated_at, completed_at
                FROM operator_campaign_assignments
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
        return self._operator_assignment_from_row(row) if row else None

    def update_operator_assignment(
        self,
        assignment_id: str,
        *,
        state: str | None = None,
        last_report_id: str | None = None,
        last_command_id: str | None = None,
        operator_fork_id: str | None = None,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        assignment = self.get_operator_campaign_assignment(assignment_id)
        if assignment is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else assignment.get("completed_at")
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE operator_campaign_assignments
                SET state = COALESCE(?, state),
                    last_report_id = COALESCE(?, last_report_id),
                    last_command_id = COALESCE(?, last_command_id),
                    operator_fork_id = COALESCE(?, operator_fork_id),
                    updated_at = ?,
                    completed_at = ?
                WHERE assignment_id = ?
                """,
                (
                    state,
                    last_report_id,
                    last_command_id,
                    operator_fork_id,
                    current,
                    completed_at,
                    assignment_id,
                ),
            )
            conn.execute(
                """
                UPDATE operator_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (current, assignment["campaign_id"]),
            )
        return self.get_operator_campaign_assignment(assignment_id)

    def update_operator_campaign(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        campaign = self.get_operator_campaign(campaign_id)
        if campaign is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else campaign.get("completed_at")
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE operator_campaigns
                SET status = COALESCE(?, status),
                    summary = COALESCE(?, summary),
                    updated_at = ?,
                    completed_at = ?
                WHERE campaign_id = ?
                """,
                (status, summary, current, completed_at, campaign_id),
            )
        return self.get_operator_campaign(campaign_id)

    def add_operator_campaign_event(
        self,
        *,
        campaign_id: str,
        operator_agent_id: str,
        event_type: str,
        summary: str,
        assignment_id: str | None = None,
        target_agent_id: str | None = None,
        detail: dict[str, Any] | None = None,
        report_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO operator_campaign_events
                    (campaign_id, assignment_id, operator_agent_id,
                     target_agent_id, event_type, summary, detail_json,
                     report_id, command_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    assignment_id,
                    operator_agent_id,
                    target_agent_id,
                    event_type,
                    summary,
                    json.dumps(detail or {}),
                    report_id,
                    command_id,
                    current,
                ),
            )
            event_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE operator_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (current, campaign_id),
            )
        event = self.get_operator_campaign_event(event_id)
        if event is None:
            raise RuntimeError("operator campaign event insert failed")
        operator_fork_id = None
        fork_agent_id = None
        if assignment_id:
            assignment = self.get_operator_campaign_assignment(assignment_id)
            if assignment is not None:
                operator_fork_id = str(assignment.get("operator_fork_id") or "").strip() or None
        if detail:
            operator_fork_id = (
                operator_fork_id
                or str(detail.get("operator_fork_id") or "").strip()
                or None
            )
            fork_agent_id = str(detail.get("fork_agent_id") or "").strip() or None
        if operator_fork_id and not fork_agent_id:
            fork = self.get_operator_fork(operator_fork_id)
            if fork is not None:
                fork_agent_id = str(fork.get("fork_agent_id") or "").strip() or None
        self.append_event(
            "operator_campaign_event",
            {
                "campaign_id": campaign_id,
                "assignment_id": assignment_id,
                "operator_agent_id": operator_agent_id,
                "target_agent_id": target_agent_id,
                "operator_fork_id": operator_fork_id,
                "fork_agent_id": fork_agent_id,
                "event_type": event_type,
                "summary": summary,
                "report_id": report_id,
                "command_id": command_id,
            },
            campaign_id,
        )
        return event

    def get_operator_campaign_event(self, event_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT event_id, campaign_id, assignment_id, operator_agent_id,
                       target_agent_id, event_type, summary, detail_json,
                       report_id, command_id, created_at
                FROM operator_campaign_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._operator_campaign_event_from_row(row) if row else None

    def list_operator_campaign_events(
        self,
        *,
        campaign_id: str | None = None,
        assignment_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if assignment_id:
            where.append("assignment_id = ?")
            params.append(assignment_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT event_id, campaign_id, assignment_id, operator_agent_id,
                   target_agent_id, event_type, summary, detail_json,
                   report_id, command_id, created_at
            FROM operator_campaign_events
            {clause}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_campaign_event_from_row(row) for row in rows]

    def create_operator_fork(
        self,
        *,
        logical_operator_agent_id: str,
        fork_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
        cwd: str,
        fork_codex_session_id: str | None = None,
        campaign_id: str | None = None,
        codex_home: str | None = None,
        codex_host_id: str | None = None,
        tmux_pane_id: str | None = None,
        status: str = "starting",
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_operator_fork_for_source(
            logical_operator_agent_id=logical_operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=source_codex_session_id,
        )
        if existing is not None:
            updated = self.update_operator_fork(
                existing["operator_fork_id"],
                fork_agent_id=fork_agent_id,
                fork_codex_session_id=fork_codex_session_id,
                campaign_id=campaign_id,
                cwd=cwd,
                codex_home=codex_home,
                codex_host_id=codex_host_id,
                tmux_pane_id=tmux_pane_id,
                status=status,
                summary=summary,
                metadata=metadata,
                touch=True,
            )
            if updated is None:
                raise RuntimeError("operator fork update failed")
            return updated

        operator_fork_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_forks
                    (operator_fork_id, logical_operator_agent_id, fork_agent_id,
                     source_caller_agent_id, source_codex_session_id,
                     fork_codex_session_id, campaign_id, cwd, codex_home,
                     codex_host_id, tmux_pane_id, status, summary,
                     metadata_json, created_at, updated_at, last_used_at,
                     completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    operator_fork_id,
                    logical_operator_agent_id,
                    fork_agent_id,
                    source_caller_agent_id,
                    source_codex_session_id,
                    fork_codex_session_id,
                    campaign_id,
                    cwd,
                    codex_home,
                    codex_host_id,
                    tmux_pane_id,
                    status,
                    summary,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    current,
                ),
            )
        fork = self.get_operator_fork(operator_fork_id)
        if fork is None:
            raise RuntimeError("operator fork insert failed")
        return fork

    def get_operator_fork(self, operator_fork_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE operator_fork_id = ?
                """,
                (operator_fork_id,),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def get_operator_fork_for_source(
        self,
        *,
        logical_operator_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE logical_operator_agent_id = ?
                  AND source_caller_agent_id = ?
                  AND source_codex_session_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    logical_operator_agent_id,
                    source_caller_agent_id,
                    source_codex_session_id,
                ),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def get_operator_fork_for_agent(self, fork_agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE fork_agent_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (fork_agent_id,),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def list_operator_forks(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        source_caller_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        include_edges: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("logical_operator_agent_id = ?")
            params.append(logical_operator_agent_id)
        if source_caller_agent_id:
            where.append("source_caller_agent_id = ?")
            params.append(source_caller_agent_id)
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                   source_caller_agent_id, source_codex_session_id,
                   fork_codex_session_id, campaign_id, cwd, codex_home,
                   codex_host_id, tmux_pane_id, status, summary,
                   metadata_json, created_at, updated_at, last_used_at,
                   completed_at
            FROM operator_forks
            {clause}
            ORDER BY updated_at DESC, operator_fork_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        forks = [self._operator_fork_from_row(row) for row in rows]
        if include_edges:
            for fork in forks:
                fork["edges"] = self.list_operator_fork_edges(
                    operator_fork_id=fork["operator_fork_id"]
                )
        return forks

    def update_operator_fork(
        self,
        operator_fork_id: str,
        *,
        fork_agent_id: str | None = None,
        fork_codex_session_id: str | None = None,
        campaign_id: str | None = None,
        cwd: str | None = None,
        codex_home: str | None = None,
        codex_host_id: str | None = None,
        tmux_pane_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        touch: bool = False,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        fork = self.get_operator_fork(operator_fork_id)
        if fork is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else fork.get("completed_at")
        )
        merged_metadata = dict(fork.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_forks
                SET fork_agent_id = COALESCE(?, fork_agent_id),
                    fork_codex_session_id = COALESCE(?, fork_codex_session_id),
                    campaign_id = COALESCE(?, campaign_id),
                    cwd = COALESCE(?, cwd),
                    codex_home = COALESCE(?, codex_home),
                    codex_host_id = COALESCE(?, codex_host_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    status = COALESCE(?, status),
                    summary = COALESCE(?, summary),
                    metadata_json = ?,
                    updated_at = ?,
                    last_used_at = CASE WHEN ? THEN ? ELSE last_used_at END,
                    completed_at = ?
                WHERE operator_fork_id = ?
                """,
                (
                    fork_agent_id,
                    fork_codex_session_id,
                    campaign_id,
                    cwd,
                    codex_home,
                    codex_host_id,
                    tmux_pane_id,
                    status,
                    summary,
                    json.dumps(merged_metadata),
                    current,
                    int(touch),
                    current,
                    completed_at,
                    operator_fork_id,
                ),
            )
        return self.get_operator_fork(operator_fork_id) if cursor.rowcount else None

    def create_operator_fork_edge(
        self,
        *,
        from_fork_id: str,
        to_fork_id: str,
        edge_type: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(edge_type or "").strip().lower()
        if normalized_type not in {"related", "depends_on", "blocks"}:
            raise ValueError("edge_type must be related, depends_on, or blocks")
        if from_fork_id == to_fork_id:
            raise ValueError("operator fork edge cannot point to itself")
        current = now_ts()
        with self.connect() as conn:
            if self._operator_fork_edge_path_exists(conn, to_fork_id, from_fork_id):
                raise ValueError("operator fork edge would create a cycle")
            existing = conn.execute(
                """
                SELECT edge_id
                FROM operator_fork_edges
                WHERE from_fork_id = ? AND to_fork_id = ? AND edge_type = ?
                """,
                (from_fork_id, to_fork_id, normalized_type),
            ).fetchone()
            if existing is not None:
                edge_id = str(existing["edge_id"])
                conn.execute(
                    """
                    UPDATE operator_fork_edges
                    SET summary = COALESCE(?, summary),
                        metadata_json = ?
                    WHERE edge_id = ?
                    """,
                    (summary, json.dumps(metadata or {}), edge_id),
                )
            else:
                edge_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO operator_fork_edges
                        (edge_id, from_fork_id, to_fork_id, edge_type,
                         summary, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        from_fork_id,
                        to_fork_id,
                        normalized_type,
                        summary,
                        json.dumps(metadata or {}),
                        current,
                    ),
                )
        edge = self.get_operator_fork_edge(edge_id)
        if edge is None:
            raise RuntimeError("operator fork edge insert failed")
        return edge

    @staticmethod
    def _operator_fork_edge_path_exists(
        conn: sqlite3.Connection,
        start_fork_id: str,
        target_fork_id: str,
    ) -> bool:
        pending = [start_fork_id]
        seen: set[str] = set()
        while pending:
            fork_id = pending.pop()
            if fork_id == target_fork_id:
                return True
            if fork_id in seen:
                continue
            seen.add(fork_id)
            rows = conn.execute(
                """
                SELECT to_fork_id
                FROM operator_fork_edges
                WHERE from_fork_id = ?
                """,
                (fork_id,),
            ).fetchall()
            pending.extend(str(row["to_fork_id"]) for row in rows)
        return False

    def get_operator_fork_edge(self, edge_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT edge_id, from_fork_id, to_fork_id, edge_type,
                       summary, metadata_json, created_at
                FROM operator_fork_edges
                WHERE edge_id = ?
                """,
                (edge_id,),
            ).fetchone()
        return self._operator_fork_edge_from_row(row) if row else None

    def list_operator_fork_edges(
        self,
        *,
        operator_fork_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where = ""
        params: list[Any] = []
        if operator_fork_id:
            where = "WHERE from_fork_id = ? OR to_fork_id = ?"
            params.extend([operator_fork_id, operator_fork_id])
        query = f"""
            SELECT edge_id, from_fork_id, to_fork_id, edge_type,
                   summary, metadata_json, created_at
            FROM operator_fork_edges
            {where}
            ORDER BY created_at DESC, edge_id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_fork_edge_from_row(row) for row in rows]

    def create_command(
        self,
        request: CommandCreateRequest,
        *,
        status: str = "queued",
    ) -> dict[str, Any]:
        command_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO commands
                    (command_id, agent_id, type, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    request.agent_id,
                    request.type,
                    json.dumps(request.payload),
                    status,
                    current,
                ),
            )
        command = self.get_command(command_id)
        if command is None:
            raise RuntimeError("command insert failed")
        return command

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
        return self._command_from_row(row) if row else None

    def delete_queued_command(self, command_id: str) -> dict[str, Any] | None:
        command = self.get_command(command_id)
        if command is None or command["status"] != "queued":
            return None
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM commands WHERE command_id = ? AND status = 'queued'",
                (command_id,),
            )
        return command if cursor.rowcount else None

    def claim_commands(self, agent_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                "UPDATE agents SET last_poll_at = ?, dismissed_at = NULL WHERE agent_id = ?",
                (current, agent_id),
            )
            rows = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE status = 'queued' AND (agent_id IS NULL OR agent_id = ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (agent_id, limit),
            ).fetchall()
            command_ids = [row["command_id"] for row in rows]
            if command_ids:
                placeholders = ",".join("?" for _ in command_ids)
                conn.execute(
                    f"""
                    UPDATE commands
                    SET status = 'delivered',
                        claimed_at = ?,
                        agent_id = COALESCE(agent_id, ?)
                    WHERE command_id IN ({placeholders}) AND status = 'queued'
                    """,
                    (current, agent_id, *command_ids),
                )
        return [self.get_command(command_id) for command_id in command_ids if command_id]

    def record_poll(self, agent_id: str, *, delivered_count: int) -> None:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO poll_events(agent_id, delivered_count, created_at)
                SELECT ?, ?, ?
                WHERE EXISTS (SELECT 1 FROM agents WHERE agent_id = ?)
                """,
                (agent_id, max(0, delivered_count), current, agent_id),
            )

    def ack_command(
        self, command_id: str, agent_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'acked', acked_at = ?, result_json = ?
                WHERE command_id = ? AND agent_id = ? AND status = 'delivered'
                """,
                (current, json.dumps(result or {}), command_id, agent_id),
            )
        if not cursor.rowcount:
            return None
        return self.get_command(command_id)

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["agent_type"] = Store._normalized_agent_type(data.get("agent_type"))
        data["pbx_active"] = bool(data["pbx_active"])
        data["starred"] = data.get("starred_at") is not None
        data["metadata"] = json.loads(data.pop("metadata_json"))
        Store._add_effective_status(data)
        data["queued_command_count"] = 0
        data["oldest_queued_command_age_seconds"] = None
        data["polls_per_hour"] = 0
        data["empty_polls_per_hour"] = 0
        data["reports_per_hour"] = 0
        data["pings_per_hour"] = 0
        data["estimated_visible_tokens_per_hour"] = 0
        data["usage_warning"] = None
        data["latest_report_id"] = None
        data["latest_report_created_at"] = None
        data["latest_report_status"] = None
        data["latest_report_needs_input"] = False
        data["latest_report_plan_option_count"] = 0
        data["latest_report_action_required"] = False
        data["active_campaign_count"] = 0
        return data

    @staticmethod
    def _add_effective_status(agent: dict[str, Any]) -> None:
        status = str(agent.get("status") or "")
        last_seen_at = agent.get("last_seen_at")
        age: float | None = None
        if last_seen_at is not None:
            try:
                age = max(0.0, now_ts() - float(last_seen_at))
            except (TypeError, ValueError):
                age = None
        stale = (
            age is not None
            and age >= STALE_WORKING_SECONDS
            and status.lower() in STALE_WORKING_STATUSES
        )
        agent["status_age_seconds"] = age
        agent["status_stale"] = stale
        agent["effective_status"] = f"stale-{status}" if stale else status

    @staticmethod
    def _add_latest_report_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        row = conn.execute(
            """
            SELECT report_id, status, needs_input, plan_options_json, created_at
            FROM reports
            WHERE agent_id = ?
            ORDER BY created_at DESC, report_id DESC
            LIMIT 1
            """,
            (agent["agent_id"],),
        ).fetchone()
        if row is None:
            return
        try:
            plan_options = json.loads(row["plan_options_json"])
        except json.JSONDecodeError:
            plan_options = []
        option_count = len(plan_options) if isinstance(plan_options, list) else 0
        needs_input = bool(row["needs_input"])
        agent["latest_report_id"] = row["report_id"]
        agent["latest_report_created_at"] = row["created_at"]
        agent["latest_report_status"] = row["status"]
        agent["latest_report_needs_input"] = needs_input
        agent["latest_report_plan_option_count"] = option_count
        agent["latest_report_action_required"] = needs_input or option_count > 0

    @staticmethod
    def _add_queue_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        current = now_ts()
        row = conn.execute(
            """
            SELECT COUNT(*) AS queued_count, MIN(created_at) AS oldest_created_at
            FROM commands
            WHERE status = 'queued' AND (agent_id IS NULL OR agent_id = ?)
            """,
            (agent["agent_id"],),
        ).fetchone()
        queued_count = int(row["queued_count"] or 0) if row else 0
        oldest = row["oldest_created_at"] if row else None
        agent["queued_command_count"] = queued_count
        agent["oldest_queued_command_age_seconds"] = (
            max(0.0, current - float(oldest)) if oldest is not None else None
        )

    @staticmethod
    def _add_campaign_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        agent_id = agent["agent_id"]
        if agent.get("agent_type") == "operator":
            row = conn.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM operator_campaigns
                WHERE operator_agent_id = ?
                  AND status NOT IN ('complete', 'completed', 'blocked',
                                     'failed', 'canceled')
                """,
                (agent_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS active_count
                FROM operator_campaign_assignments
                WHERE target_agent_id = ?
                  AND state NOT IN ('complete', 'completed', 'blocked',
                                    'failed', 'canceled')
                """,
                (agent_id,),
            ).fetchone()
        agent["active_campaign_count"] = int(row["active_count"] or 0) if row else 0

    @staticmethod
    def _add_usage_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        since = now_ts() - 3600
        agent_id = agent["agent_id"]
        poll_row = conn.execute(
            """
            SELECT COUNT(*) AS poll_count,
                   SUM(CASE WHEN delivered_count = 0 THEN 1 ELSE 0 END) AS empty_count,
                   SUM(delivered_count) AS delivered_count
            FROM poll_events
            WHERE agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        report_row = conn.execute(
            """
            SELECT COUNT(*) AS report_count,
                   COALESCE(SUM(LENGTH(summary) + LENGTH(detail)), 0) AS report_chars
            FROM reports
            WHERE agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        command_row = conn.execute(
            """
            SELECT COUNT(*) AS command_count,
                   SUM(CASE WHEN type = 'ping' THEN 1 ELSE 0 END) AS ping_count,
                   COALESCE(SUM(LENGTH(payload_json) + COALESCE(LENGTH(result_json), 0)), 0) AS command_chars
            FROM commands
            WHERE (agent_id = ? OR agent_id IS NULL) AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        poll_count = int(poll_row["poll_count"] or 0) if poll_row else 0
        empty_count = int(poll_row["empty_count"] or 0) if poll_row else 0
        delivered_count = int(poll_row["delivered_count"] or 0) if poll_row else 0
        report_count = int(report_row["report_count"] or 0) if report_row else 0
        report_chars = int(report_row["report_chars"] or 0) if report_row else 0
        ping_count = int(command_row["ping_count"] or 0) if command_row else 0
        command_chars = int(command_row["command_chars"] or 0) if command_row else 0
        estimated_tokens = (
            poll_count * POLL_BASE_TOKEN_ESTIMATE
            + delivered_count * DELIVERED_COMMAND_TOKEN_ESTIMATE
            + (report_chars + command_chars) // TOKEN_ESTIMATE_CHARS_PER_TOKEN
        )
        warning = None
        if estimated_tokens >= USAGE_WARN_TOKENS_PER_HOUR:
            warning = "high estimated token use"
        elif poll_count >= POLL_WARN_PER_HOUR and empty_count == poll_count:
            warning = "idle polling"
        agent["polls_per_hour"] = poll_count
        agent["empty_polls_per_hour"] = empty_count
        agent["reports_per_hour"] = report_count
        agent["pings_per_hour"] = ping_count
        agent["estimated_visible_tokens_per_hour"] = estimated_tokens
        agent["usage_warning"] = warning

    @staticmethod
    def _report_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["needs_input"] = bool(data["needs_input"])
        data["plan_options"] = json.loads(data.pop("plan_options_json"))
        metadata_json = data.pop("metadata_json", "{}")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _command_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        result_json = data.pop("result_json")
        data["result"] = json.loads(result_json) if result_json else None
        return data

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    @staticmethod
    def _joplin_log_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["active"] = bool(data["active"])
        return data

    @staticmethod
    def _joplin_sync_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _operator_campaign_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["criteria"] = json.loads(data.pop("criteria_json"))
        data["assignments"] = []
        data["events"] = []
        return data

    @staticmethod
    def _operator_assignment_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["criteria"] = json.loads(data.pop("criteria_json"))
        return data

    @staticmethod
    def _operator_campaign_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        detail_json = data.pop("detail_json")
        try:
            detail = json.loads(detail_json)
        except json.JSONDecodeError:
            detail = {}
        data["detail"] = detail if isinstance(detail, dict) else {}
        return data

    @staticmethod
    def _operator_fork_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        data["edges"] = []
        return data

    @staticmethod
    def _operator_fork_edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _thread_report(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": f"report:{report['report_id']}",
            "kind": "report",
            "agent_id": report["agent_id"],
            "created_at": report["created_at"],
            "status": report["status"],
            "title": report["summary"],
            "body": report["detail"],
            "metadata": {
                "report_id": report["report_id"],
                "project": report["project"],
                "needs_input": report["needs_input"],
                "plan_options": report["plan_options"],
                "report_metadata": report.get("metadata", {}),
            },
        }

    @staticmethod
    def _thread_command(command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        if command["type"] == "send_input":
            title = "Follow-up input"
            body = str(payload.get("message") or payload)
        elif command["type"] == "send_key":
            key = str(payload.get("key") or "").strip() or "key"
            title = f"Send key: {key}"
            body = str(payload.get("request") or payload)
        elif command["type"] == "request_detail":
            title = "Detail request"
            body = str(payload.get("request") or payload)
        elif command["type"] == "ping":
            title = "Ping"
            body = str(payload.get("request") or "Ping agent and extend polling.")
        else:
            title = command["type"].replace("_", " ").title()
            body = json.dumps(payload, indent=2, sort_keys=True)
        return {
            "item_id": f"command:{command['command_id']}",
            "kind": "command",
            "agent_id": command["agent_id"],
            "created_at": command["created_at"],
            "status": command["status"],
            "title": title,
            "body": body,
            "metadata": {
                "command_id": command["command_id"],
                "type": command["type"],
                "payload": payload,
                "claimed_at": command["claimed_at"],
                "acked_at": command["acked_at"],
                "result": command["result"],
            },
        }
