from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
import uuid
from typing import Any

from .store import Store


EVENT_STREAM_API_VERSION = "agent-pbx.events/v2"
DEFAULT_EVENT_REPLAY_LIMIT = 100
MAX_EVENT_REPLAY_LIMIT = 500


@dataclass
class EventClient:
    client_id: str
    role: str
    connected_at: float
    cursor: int
    last_seen_at: float
    message_window_started_at: float
    message_count: int = 0


@dataclass
class EventClientRegistry:
    max_clients: int = 16
    message_rate_per_minute: int = 120
    _clients: dict[str, EventClient] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, *, role: str, cursor: int, client_id: str | None = None) -> EventClient:
        now = time.time()
        resolved_client_id = client_id or str(uuid.uuid4())
        with self._lock:
            if len(self._clients) >= max(1, int(self.max_clients)):
                raise RuntimeError("remote client limit reached")
            if resolved_client_id in self._clients:
                raise RuntimeError("remote client id is already connected")
        client = EventClient(
            client_id=resolved_client_id,
            role=role,
            connected_at=now,
            cursor=max(0, int(cursor)),
            last_seen_at=now,
            message_window_started_at=now,
        )
        with self._lock:
            self._clients[client.client_id] = client
        return client

    def update(self, client_id: str, cursor: int) -> EventClient | None:
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                return None
            client.cursor = max(0, int(cursor))
            client.last_seen_at = time.time()
            return client

    def unregister(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def allow_message(self, client_id: str) -> bool:
        now = time.time()
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                return False
            if now - client.message_window_started_at >= 60:
                client.message_window_started_at = now
                client.message_count = 0
            if client.message_count >= max(1, int(self.message_rate_per_minute)):
                return False
            client.message_count += 1
            client.last_seen_at = now
            return True

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            clients = tuple(self._clients.values())
        return [
            {
                "client_id": client.client_id,
                "role": client.role,
                "connected_at": client.connected_at,
                "cursor": client.cursor,
                "last_seen_at": client.last_seen_at,
            }
            for client in clients
        ]


class EventStreamService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def envelope(
        self,
        *,
        after_id: int = 0,
        limit: int = DEFAULT_EVENT_REPLAY_LIMIT,
        include_state: bool = False,
        role: str | None = None,
        allowed_agent_ids: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        after_id = max(0, int(after_id))
        limit = min(MAX_EVENT_REPLAY_LIMIT, max(1, int(limit)))
        bounds = self.store.event_bounds()
        oldest = bounds["oldest_event_id"]
        latest = bounds["latest_event_id"]
        reset_required = bool(
            after_id > 0 and oldest is not None and after_id < int(oldest) - 1
        )
        if after_id == 0 or reset_required:
            events = self.store.list_recent_events(limit=limit)
            kind = "snapshot"
        else:
            events = self.store.list_events(after_id=after_id, limit=limit)
            kind = "events"
        source_cursor = (
            int(events[-1]["event_id"])
            if events
            else min(after_id, int(latest or 0))
        )
        allowed = {str(value) for value in allowed_agent_ids if str(value).strip()}
        if role is not None:
            events = [
                self._project_event(event)
                for event in events
                if self._event_allowed(event, allowed)
            ]
        cursor = source_cursor
        state: dict[str, Any] | None = None
        if include_state or kind == "snapshot":
            agents = self.store.list_agents(include_hidden=True)
            if role is not None:
                agents = [
                    self._project_agent(agent)
                    for agent in agents
                    if not allowed or str(agent.get("agent_id") or "") in allowed
                ]
            state = {"agents": agents}
        return {
            "api_version": EVENT_STREAM_API_VERSION,
            "kind": kind,
            "cursor": cursor,
            "oldest_event_id": oldest,
            "latest_event_id": latest,
            "reset_required": reset_required,
            "more_available": bool(latest is not None and cursor < int(latest)),
            "events": events,
            "state": state,
            "server_time": time.time(),
        }

    @staticmethod
    def _event_allowed(event: dict[str, Any], allowed: set[str]) -> bool:
        if not allowed:
            return True
        if str(event.get("subject_id") or "") in allowed:
            return True
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return False
        for key, value in payload.items():
            if key.endswith("agent_id") or key in {"entity_id", "subject_id"}:
                if str(value or "") in allowed:
                    return True
        return False

    @classmethod
    def _project_event(cls, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "type": event.get("type"),
            "subject_id": event.get("subject_id"),
            "payload": cls._redact_payload(event.get("payload")),
            "created_at": event.get("created_at"),
        }

    @classmethod
    def _redact_payload(cls, value: Any, *, key: str = "") -> Any:
        normalized = key.lower()
        if any(
            marker in normalized
            for marker in (
                "authorization",
                "body",
                "credential",
                "detail",
                "message",
                "password",
                "prompt",
                "reasoning",
                "secret",
                "token",
            )
        ):
            return "[redacted]"
        if isinstance(value, dict):
            return {
                str(item_key): cls._redact_payload(item_value, key=str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_payload(item, key=key) for item in value]
        return value

    @staticmethod
    def _project_agent(agent: dict[str, Any]) -> dict[str, Any]:
        return {
            key: agent.get(key)
            for key in (
                "agent_id",
                "agent_type",
                "project",
                "name",
                "status",
                "effective_status",
                "pbx_active",
                "starred",
                "created_at",
                "last_seen_at",
                "dismissed_at",
            )
        }
