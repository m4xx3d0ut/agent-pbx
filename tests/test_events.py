from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.events import EVENT_STREAM_API_VERSION, EventClientRegistry, EventStreamService
from agent_pbx.store import Store


def populated_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "events.sqlite")
    store.init()
    for index in range(5):
        store.append_event("fixture", {"index": index}, f"subject-{index}")
    return store


def test_event_envelope_supports_snapshot_resume_and_more_available(tmp_path: Path) -> None:
    service = EventStreamService(populated_store(tmp_path))
    initial = service.envelope(limit=2, include_state=True)
    assert initial["api_version"] == EVENT_STREAM_API_VERSION
    assert initial["kind"] == "snapshot"
    assert [event["payload"]["index"] for event in initial["events"]] == [3, 4]
    assert initial["cursor"] == 5
    assert initial["state"] == {"agents": []}

    resumed = service.envelope(after_id=2, limit=2)
    assert resumed["kind"] == "events"
    assert [event["payload"]["index"] for event in resumed["events"]] == [2, 3]
    assert resumed["cursor"] == 4
    assert resumed["more_available"] is True


def test_event_envelope_requests_resync_after_retention_gap(tmp_path: Path) -> None:
    store = populated_store(tmp_path)
    with store.connect() as connection:
        connection.execute("DELETE FROM events WHERE event_id < 4")
    envelope = EventStreamService(store).envelope(after_id=1, limit=10)
    assert envelope["kind"] == "snapshot"
    assert envelope["reset_required"] is True
    assert envelope["oldest_event_id"] == 4
    assert envelope["state"] == {"agents": []}


def test_event_client_registry_tracks_independent_cursors() -> None:
    registry = EventClientRegistry()
    first = registry.register(role="observer", cursor=3, client_id="one")
    second = registry.register(role="controller", cursor=7, client_id="two")
    registry.update(first.client_id, 5)
    cursors = {item["client_id"]: item["cursor"] for item in registry.snapshot()}
    assert cursors == {"one": 5, "two": 7}
    registry.unregister(second.client_id)
    assert [item["client_id"] for item in registry.snapshot()] == ["one"]


def test_authenticated_event_snapshot_and_websocket_replay(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    unauthorized = client.get("/v2/events/snapshot")
    assert unauthorized.status_code == 401
    headers = {"Authorization": "Bearer secret"}

    registered = client.post(
        "/v1/agents/register",
        headers=headers,
        json={"agent_id": "agent-1", "project": "demo"},
    )
    assert registered.status_code == 200
    snapshot = client.get("/v2/events/snapshot?limit=20", headers=headers)
    assert snapshot.status_code == 200
    assert snapshot.json()["api_version"] == EVENT_STREAM_API_VERSION
    cursor = snapshot.json()["cursor"]

    with client.websocket_connect(
        f"/v2/events/ws?after_id={cursor}", headers=headers
    ) as websocket:
        initial = websocket.receive_json()
        assert initial["kind"] == "events"
        assert initial["role"] == "controller"
        report = client.post(
            "/v1/agents/agent-1/reports",
            headers=headers,
            json={
                "project": "demo",
                "status": "working",
                "summary": "stream me",
                "detail": "event stream fixture",
            },
        )
        assert report.status_code == 200
        replay = websocket.receive_json()
        assert replay["kind"] == "events"
        assert any(event["type"] == "report_created" for event in replay["events"])
        websocket.send_json({"type": "ping"})
        pong = websocket.receive_json()
        assert pong["kind"] == "pong"

    assert app.state.event_clients.snapshot() == []


def test_event_websocket_rejects_invalid_token(tmp_path: Path) -> None:
    client = TestClient(
        create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    )
    with pytest.raises(WebSocketDisconnect) as raised:
        with client.websocket_connect(
            "/v2/events/ws", headers={"Authorization": "Bearer wrong"}
        ):
            pass
    assert raised.value.code == 4401
