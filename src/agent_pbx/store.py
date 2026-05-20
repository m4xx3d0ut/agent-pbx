from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import AgentRegisterRequest, CommandCreateRequest, ReportCreateRequest
from .security import hash_secret, now_ts


SCHEMA_VERSION = 1


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
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL
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

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    subject_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

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

    def register_agent(self, request: AgentRegisterRequest) -> dict[str, Any]:
        current = now_ts()
        metadata_json = json.dumps(request.metadata)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, project, name, status, metadata_json, created_at, last_seen_at)
                VALUES (?, ?, ?, 'online', ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    project = excluded.project,
                    name = excluded.name,
                    status = 'online',
                    metadata_json = excluded.metadata_json,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    request.agent_id,
                    request.project,
                    request.name,
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
                SELECT agent_id, project, name, status, metadata_json, created_at, last_seen_at
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
                SELECT agent_id, project, name, status, metadata_json, created_at, last_seen_at
                FROM agents
                ORDER BY last_seen_at DESC, agent_id ASC
                """
            ).fetchall()
        return [self._agent_from_row(row) for row in rows]

    def create_report(
        self, agent_id: str, request: ReportCreateRequest
    ) -> dict[str, Any]:
        report_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agents SET status = ?, last_seen_at = ?
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
                    json.dumps(request.plan_options),
                    current,
                ),
            )
        report = self.get_report(report_id)
        if report is None:
            raise RuntimeError("report insert failed")
        return report

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

    def claim_commands(self, agent_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        current = now_ts()
        with self.connect() as conn:
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
                    SET status = 'delivered', claimed_at = ?
                    WHERE command_id IN ({placeholders})
                    """,
                    (current, *command_ids),
                )
        return [self.get_command(command_id) for command_id in command_ids if command_id]

    def ack_command(
        self, command_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE commands
                SET status = 'acked', acked_at = ?, result_json = ?
                WHERE command_id = ?
                """,
                (current, json.dumps(result or {}), command_id),
            )
        return self.get_command(command_id)

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return data

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
