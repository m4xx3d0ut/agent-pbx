from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .schemas import CommandCreateRequest
from .security import now_ts
from .store import Store


class TerminalDeliveryRepository:
    """Persistence boundary for restart-stable terminal delivery attempts."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def create_idempotent_command(
        self,
        request: CommandCreateRequest,
        *,
        delivery_request_id: str,
        delivery_request_sha256: str,
        status: str,
    ) -> tuple[dict[str, Any], bool]:
        request_id = str(delivery_request_id or "").strip()
        request_sha256 = str(delivery_request_sha256 or "").strip().lower()
        if not request_id or len(request_id) > 240:
            raise ValueError("delivery_request_id must contain 1-240 characters")
        if re.fullmatch(r"[0-9a-f]{64}", request_sha256) is None:
            raise ValueError("delivery_request_sha256 must be a SHA-256 digest")
        payload = dict(request.payload)
        payload["delivery_request_id"] = request_id
        payload["delivery_request_sha256"] = request_sha256
        command_id = ""
        created = False
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json
                FROM commands
                WHERE json_extract(payload_json, '$.delivery_request_id') = ?
                ORDER BY created_at ASC, command_id ASC
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            if row is not None:
                existing_payload = json.loads(row["payload_json"] or "{}")
                if not isinstance(existing_payload, dict):
                    existing_payload = {}
                if (
                    str(row["agent_id"] or "") != str(request.agent_id or "")
                    or str(row["type"] or "") != str(request.type or "")
                    or str(
                        existing_payload.get("delivery_request_sha256") or ""
                    ).lower()
                    != request_sha256
                ):
                    raise ValueError(
                        "delivery_request_id was already used for a different request"
                    )
                command_id = str(row["command_id"])
            else:
                command_id = str(uuid.uuid4())
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
                        json.dumps(payload),
                        status,
                        now_ts(),
                    ),
                )
                created = True
        command = self.store.get_command(command_id)
        if command is None:
            raise RuntimeError("idempotent delivery command is unavailable")
        return command, created

    def finish_command(
        self,
        command_id: str,
        *,
        expected_status: str,
        status: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = ?, result_json = ?, acked_at = ?
                WHERE command_id = ? AND status = ?
                """,
                (
                    str(status),
                    json.dumps(result),
                    now_ts(),
                    str(command_id),
                    str(expected_status),
                ),
            )
        command = self.store.get_command(str(command_id))
        if command is None:
            raise RuntimeError("delivery command disappeared during finalization")
        if cursor.rowcount == 0 and str(command.get("status") or "") != str(status):
            raise ValueError("delivery command changed before finalization")
        return command
