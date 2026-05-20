from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
