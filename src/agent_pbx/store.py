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


SCHEMA_VERSION = 7
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4
POLL_BASE_TOKEN_ESTIMATE = 80
DELIVERED_COMMAND_TOKEN_ESTIMATE = 120
USAGE_WARN_TOKENS_PER_HOUR = 10_000
POLL_WARN_PER_HOUR = 24
STALE_WORKING_SECONDS = 600
STALE_WORKING_STATUSES = {"running", "working"}


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
                    project TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL,
                    pbx_active INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_poll_at REAL,
                    latest_report_seen_at REAL,
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
                """
            )
            previous_schema_version = self._schema_version(conn)
            self._ensure_column(conn, "agents", "last_poll_at", "REAL")
            self._ensure_column(conn, "agents", "latest_report_seen_at", "REAL")
            self._ensure_column(conn, "agents", "dismissed_at", "REAL")
            self._ensure_column(
                conn,
                "agents",
                "pbx_active",
                "INTEGER NOT NULL DEFAULT 1",
            )
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
        metadata_json = json.dumps(request.metadata)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, project, name, status, pbx_active, metadata_json,
                     created_at, last_seen_at, dismissed_at)
                VALUES (?, ?, ?, 'online', ?, ?, ?, ?, NULL)
                ON CONFLICT(agent_id) DO UPDATE SET
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
                    request.project,
                    request.name,
                    int(request.pbx_active),
                    metadata_json,
                    current,
                    current,
                ),
            )
        return self.get_agent(request.agent_id) or {}

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_id, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, dismissed_at
                FROM agents
                WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
        return self._agent_from_row(row) if row else None

    def list_agents(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_id, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, dismissed_at
                FROM agents
                WHERE dismissed_at IS NULL
                ORDER BY last_seen_at DESC, agent_id ASC
                """
            ).fetchall()
            agents = [self._agent_from_row(row) for row in rows]
            for agent in agents:
                self._add_latest_report_summary(conn, agent)
                self._add_queue_summary(conn, agent)
                self._add_usage_summary(conn, agent)
        return agents

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
                     needs_input, plan_options_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, created_at
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
                       needs_input, plan_options_json, created_at
                FROM reports
                ORDER BY created_at DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (safe_limit,)
        else:
            query = """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, created_at
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

    def create_command(self, request: CommandCreateRequest) -> dict[str, Any]:
        command_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO commands
                    (command_id, agent_id, type, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (
                    command_id,
                    request.agent_id,
                    request.type,
                    json.dumps(request.payload),
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
        data["pbx_active"] = bool(data["pbx_active"])
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
