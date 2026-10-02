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


@dataclass
class EventClientRegistry:
    _clients: dict[str, EventClient] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, *, role: str, cursor: int, client_id: str | None = None) -> EventClient:
        now = time.time()
        client = EventClient(
            client_id=client_id or str(uuid.uuid4()),
            role=role,
            connected_at=now,
            cursor=max(0, int(cursor)),
            last_seen_at=now,
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
        cursor = int(events[-1]["event_id"]) if events else min(after_id, int(latest or 0))
        state: dict[str, Any] | None = None
        if include_state or kind == "snapshot":
            state = {
                "agents": self.store.list_agents(include_hidden=True),
            }
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

