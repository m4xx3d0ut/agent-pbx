from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ...security import now_ts
from ..connection import SQLiteConnectionFactory
from ..transactions import begin_immediate


MODEL_ELEVATION_OPEN_STATUSES = {
    "pending",
    "approved",
    "active",
    "revert_pending",
}


class ModelElevationRepository:
    """Persist the bounded model-elevation lease state machine."""

    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self._connections = connections

    def request_or_reuse(
        self,
        *,
        agent_id: str,
        requested_profile: str,
        justification: str,
        scope: str,
        prior_profile: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self.expire()
        lease_id = str(uuid.uuid4())
        requested_at = now_ts()
        with self._connections.managed() as connection:
            begin_immediate(connection)
            existing = connection.execute(
                """
                SELECT * FROM model_elevation_leases
                WHERE agent_id = ?
                  AND status IN ('pending', 'approved', 'active', 'revert_pending')
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            if existing is not None:
                lease = self.from_row(existing)
                if str(lease.get("requested_profile") or "") != requested_profile:
                    raise ValueError(
                        f"agent {agent_id!r} already has an open model elevation "
                        f"for {lease.get('requested_profile')!r}"
                    )
                return lease, False
            connection.execute(
                """
                INSERT INTO model_elevation_leases(
                    lease_id, agent_id, requested_profile, prior_profile,
                    justification, scope, status, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    lease_id,
                    agent_id,
                    requested_profile,
                    prior_profile,
                    justification,
                    scope,
                    requested_at,
                ),
            )
            created = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            assert created is not None
            return self.from_row(created), True

    def decide(
        self,
        lease_id: str,
        *,
        approved: bool,
        approved_by: str,
        duration_seconds: float,
    ) -> dict[str, Any]:
        lease = self.get(lease_id)
        if lease is None:
            raise ValueError("model elevation lease not found")
        if (
            approved
            and lease["status"] in {"approved", "active", "revert_pending"}
            and str(lease.get("approved_by") or "") == approved_by
        ):
            return lease
        if not approved and lease["status"] == "rejected":
            return lease
        if lease["status"] != "pending":
            raise ValueError("model elevation lease is no longer pending")
        decided_at = now_ts()
        status_value = "approved" if approved else "rejected"
        expires_at = decided_at + max(1.0, duration_seconds) if approved else None
        with self._connections.managed() as connection:
            connection.execute(
                """
                UPDATE model_elevation_leases
                SET status = ?, approved_at = ?, approved_by = ?,
                    expires_at = ?, finished_at = ?
                WHERE lease_id = ? AND status = 'pending'
                """,
                (
                    status_value,
                    decided_at,
                    approved_by,
                    expires_at,
                    None if approved else decided_at,
                    lease_id,
                ),
            )
        decided = self.get(lease_id)
        assert decided is not None
        return decided

    def activate(
        self,
        lease_id: str,
        *,
        transition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lease = self.get(lease_id)
        if lease is None:
            raise ValueError("model elevation lease not found")
        current = now_ts()
        if lease["status"] == "active":
            if lease.get("expires_at") is not None and float(lease["expires_at"]) <= current:
                self.expire(current_time=current)
                raise ValueError("model elevation lease has expired and awaits reversion")
            return self.update_transition(lease_id, transition=transition)
        if lease["status"] != "approved":
            raise ValueError("model elevation lease is not approved")
        if lease.get("expires_at") is not None and float(lease["expires_at"]) <= current:
            self.finish(lease_id, status="expired")
            raise ValueError("model elevation lease has expired")
        with self._connections.managed() as connection:
            connection.execute(
                """
                UPDATE model_elevation_leases
                SET status = 'active', activated_at = ?, transition_json = ?
                WHERE lease_id = ? AND status = 'approved'
                """,
                (current, json.dumps(transition or {}), lease_id),
            )
        activated = self.get(lease_id)
        assert activated is not None
        return activated

    def finish(
        self,
        lease_id: str,
        *,
        status: str,
        transition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"expired", "failed", "reverted"}:
            raise ValueError(f"invalid terminal elevation status {status!r}")
        lease = self.get(lease_id)
        if lease is None:
            raise ValueError("model elevation lease not found")
        merged = dict(lease.get("transition") or {})
        merged.update(transition or {})
        with self._connections.managed() as connection:
            connection.execute(
                """
                UPDATE model_elevation_leases
                SET status = ?, finished_at = ?, transition_json = ?
                WHERE lease_id = ?
                """,
                (status, now_ts(), json.dumps(merged), lease_id),
            )
        finished = self.get(lease_id)
        assert finished is not None
        return finished

    def update_transition(
        self,
        lease_id: str,
        *,
        transition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            if row is None:
                raise ValueError("model elevation lease not found")
            lease = self.from_row(row)
            if str(lease.get("status") or "") not in MODEL_ELEVATION_OPEN_STATUSES:
                raise ValueError("model elevation lease is already terminal")
            merged = dict(lease.get("transition") or {})
            merged.update(transition or {})
            connection.execute(
                "UPDATE model_elevation_leases SET transition_json = ? WHERE lease_id = ?",
                (json.dumps(merged), lease_id),
            )
            updated = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            assert updated is not None
            return self.from_row(updated)

    def mark_revert_pending(
        self,
        lease_id: str,
        *,
        reason: str,
        transition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connections.managed() as connection:
            begin_immediate(connection)
            row = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            if row is None:
                raise ValueError("model elevation lease not found")
            lease = self.from_row(row)
            status = str(lease.get("status") or "")
            if status not in {"active", "revert_pending"}:
                raise ValueError("only an active model elevation can request reversion")
            merged = dict(lease.get("transition") or {})
            merged.update(transition or {})
            if status == "active":
                merged.update(
                    {
                        "revert_reason": reason,
                        "revert_requested_at": now_ts(),
                    }
                )
            cursor = connection.execute(
                """
                UPDATE model_elevation_leases
                SET status = 'revert_pending', transition_json = ?
                WHERE lease_id = ? AND status IN ('active', 'revert_pending')
                """,
                (json.dumps(merged), lease_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("model elevation changed before reversion was recorded")
            updated = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            assert updated is not None
            return self.from_row(updated)

    def expire(self, *, current_time: float | None = None) -> int:
        current = now_ts() if current_time is None else float(current_time)
        with self._connections.managed() as connection:
            approved = connection.execute(
                """
                UPDATE model_elevation_leases
                SET status = 'expired', finished_at = ?
                WHERE status = 'approved'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (current, current),
            )
            active_rows = connection.execute(
                """
                SELECT lease_id, transition_json
                FROM model_elevation_leases
                WHERE status = 'active'
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (current,),
            ).fetchall()
            active_count = 0
            for row in active_rows:
                try:
                    transition = json.loads(row["transition_json"] or "{}")
                except json.JSONDecodeError:
                    transition = {}
                if not isinstance(transition, dict):
                    transition = {}
                transition.update(
                    {
                        "revert_reason": "lease_expired",
                        "revert_requested_at": current,
                    }
                )
                cursor = connection.execute(
                    """
                    UPDATE model_elevation_leases
                    SET status = 'revert_pending', transition_json = ?
                    WHERE lease_id = ? AND status = 'active'
                    """,
                    (json.dumps(transition), str(row["lease_id"])),
                )
                active_count += int(cursor.rowcount)
        return int(approved.rowcount) + active_count

    def get(self, lease_id: str) -> dict[str, Any] | None:
        with self._connections.managed() as connection:
            row = connection.execute(
                "SELECT * FROM model_elevation_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
        return self.from_row(row) if row is not None else None

    def list(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(500, limit))
        with self._connections.managed() as connection:
            if agent_id:
                rows = connection.execute(
                    """
                    SELECT * FROM model_elevation_leases
                    WHERE agent_id = ? ORDER BY requested_at DESC LIMIT ?
                    """,
                    (agent_id, bounded_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM model_elevation_leases
                    ORDER BY requested_at DESC LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
        return [self.from_row(row) for row in rows]

    @staticmethod
    def from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw_transition = data.pop("transition_json", "{}")
        try:
            transition = json.loads(raw_transition)
        except json.JSONDecodeError:
            transition = {}
        data["transition"] = transition if isinstance(transition, dict) else {}
        expires_at = data.get("expires_at")
        data["expired"] = bool(
            data.get("status") == "expired"
            or (expires_at is not None and float(expires_at) <= now_ts())
        )
        return data
