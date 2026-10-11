from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest
import websockets
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.codex.event_bridge import (
    AppServerJsonlClient,
    AppServerWebSocketClient,
    CodexAppServerEventBridge,
    associate_child_threads,
    safe_child_item,
)
from agent_pbx.codex.runtime import CodexRuntimeService
from agent_pbx.config import ServerConfig
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


class FakeAppServerClient:
    def __init__(
        self,
        *,
        threads: list[dict[str, Any]],
        items_by_thread: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.threads = threads
        self.items_by_thread = items_by_thread or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((method, dict(params)))
        if method == "thread/list":
            return {"data": list(self.threads), "nextCursor": None}
        if method == "thread/items/list":
            return {
                "data": list(self.items_by_thread.get(str(params["threadId"]), [])),
                "nextCursor": None,
            }
        raise AssertionError(f"unexpected app-server method: {method}")


def registered_runtime(tmp_path: Path) -> tuple[Store, CodexRuntimeService]:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-a",
            project="demo",
            metadata={
                "cwd": str(tmp_path),
                "codex_session_id": "thread-root",
            },
        )
    )
    return store, CodexRuntimeService(store)


def child_thread(
    child_id: str = "thread-child",
    *,
    parent_id: str = "thread-root",
    status: str = "active",
) -> dict[str, Any]:
    return {
        "id": child_id,
        "parentThreadId": None,
        "source": {
            "subAgent": {
                "thread_spawn": {
                    "parent_thread_id": parent_id,
                    "agent_role": "explorer",
                    "agent_path": "/private/agents/explorer.toml",
                }
            }
        },
        "status": {"type": status},
        "model": "gpt-5.6-sol",
        "reasoningEffort": "high",
        "agentRole": "explorer",
        "preview": "private child prompt",
        "path": "/private/rollout.jsonl",
        "createdAt": 100.0,
        "updatedAt": 110.0,
    }


@pytest.mark.asyncio
async def test_bridge_projects_exact_child_and_discards_private_fields(
    tmp_path: Path,
) -> None:
    store, runtime = registered_runtime(tmp_path)
    item = {
        "type": "subAgentActivity",
        "id": "activity-1",
        "agentThreadId": "thread-child",
        "kind": "interacted",
        "model": "gpt-5.6-sol",
        "reasoningEffort": "high",
        "agentPath": "/private/agents/explorer.toml",
        "prompt": "private item prompt",
        "reasoning": "private hidden reasoning",
    }
    client = FakeAppServerClient(
        threads=[child_thread()],
        items_by_thread={
            "thread-root": [
                {
                    "turnId": "turn-1",
                    "item": item,
                    "startedAtMs": 105_000,
                    "completedAtMs": None,
                }
            ]
        },
    )
    bridge = CodexAppServerEventBridge(store, runtime, transport="sidecar")
    runtime.observe_report("agent-a", status="ready", observed_at=120.0)

    await bridge.reconcile_once(client)  # type: ignore[arg-type]

    snapshot = runtime.snapshot("agent-a")
    assert snapshot["capabilities"]["native_subagents"] == "observed"
    assert snapshot["children"] == [
        {
            "child_id": "thread-child",
            "parent_id": "agent-a",
            "state": "thinking",
            "role": "explorer",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "started_at": 100.0,
            "finished_at": None,
        }
    ]
    # Historical child hydration must not make an idle root look active.
    assert snapshot["state"] == "ready"
    # A sidecar reports foreign live threads as notLoaded. A later metadata
    # update must not erase the lifecycle state already read from root items.
    client.threads[0]["status"] = {"type": "notLoaded"}
    client.threads[0]["updatedAt"] = 120.0
    await bridge.reconcile_once(client)  # type: ignore[arg-type]
    assert runtime.snapshot("agent-a")["children"][0]["state"] == "thinking"
    serialized = json.dumps(store.list_subject_events("agent-a"))
    for private_value in (
        "private child prompt",
        "private item prompt",
        "private hidden reasoning",
        "/private/agents",
        "/private/rollout",
    ):
        assert private_value not in serialized
    assert [method for method, _params in client.calls] == [
        "thread/list",
        "thread/items/list",
        "thread/list",
        "thread/items/list",
    ]


@pytest.mark.asyncio
async def test_bridge_ignores_unrelated_and_ambiguous_sessions(tmp_path: Path) -> None:
    store, runtime = registered_runtime(tmp_path)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-b",
            project="demo",
            metadata={"codex_session_id": "thread-root"},
        )
    )
    client = FakeAppServerClient(threads=[child_thread()])
    bridge = CodexAppServerEventBridge(store, runtime, transport="sidecar")

    await bridge.reconcile_once(client)  # type: ignore[arg-type]

    assert bridge.status()["target_count"] == 0
    assert bridge.status()["ambiguous_session_count"] == 1
    assert bridge.status()["child_count"] == 0
    assert runtime.snapshot("agent-a")["children"] == []
    assert [method for method, _params in client.calls] == ["thread/list"]


def test_child_association_walks_nested_parents_and_stays_exact() -> None:
    from agent_pbx.codex.event_bridge import BridgeTarget

    target = BridgeTarget(agent_id="agent-a", thread_id="thread-root")
    direct = child_thread("child-one")
    nested = child_thread("child-two", parent_id="child-one")
    unrelated = child_thread("child-other", parent_id="other-root")

    associated = associate_child_threads(
        [direct, nested, unrelated],
        {"thread-root": target},
    )

    assert [(item.agent_id, row["id"]) for item, row in associated] == [
        ("agent-a", "child-one"),
        ("agent-a", "child-two"),
    ]


def test_safe_child_item_is_allowlist_only() -> None:
    assert safe_child_item(
        {
            "id": "activity-1",
            "type": "subAgentActivity",
            "agentThreadId": "child-1",
            "kind": "started",
            "agentRole": "reviewer",
            "agentPath": "/private/reviewer.toml",
            "prompt": "private",
            "reasoning": "hidden",
            "message": "private status",
        }
    ) == {
        "id": "activity-1",
        "type": "subAgentActivity",
        "agentThreadId": "child-1",
        "kind": "started",
        "agentRole": "reviewer",
    }
    assert safe_child_item({"type": "agentMessage", "text": "private"}) is None


@pytest.mark.asyncio
async def test_live_notification_routes_only_to_exact_bound_thread(
    tmp_path: Path,
) -> None:
    from agent_pbx.codex.event_bridge import BridgeTarget

    store, runtime = registered_runtime(tmp_path)
    bridge = CodexAppServerEventBridge(store, runtime, transport="sidecar")
    bridge._targets_by_thread = {
        "thread-root": BridgeTarget("agent-a", "thread-root")
    }
    params = {
        "threadId": "thread-root",
        "startedAtMs": 200_000,
        "item": {
            "id": "activity-live",
            "type": "subAgentActivity",
            "agentThreadId": "child-live",
            "kind": "started",
            "prompt": "must be discarded",
        },
    }

    await bridge._handle_notification("item/started", params)
    await bridge._handle_notification(
        "item/started",
        {**params, "threadId": "thread-unrelated"},
    )

    snapshot = runtime.snapshot("agent-a")
    assert snapshot["state"] == "delegating"
    assert [child["child_id"] for child in snapshot["children"]] == ["child-live"]
    assert "must be discarded" not in json.dumps(store.list_subject_events("agent-a"))


@pytest.mark.asyncio
async def test_jsonl_client_initializes_and_uses_read_only_requests(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fake_app_server.py"
    script.write_text(
        """
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"userAgent": "fake", "codexHome": "/tmp", "platformFamily": "unix", "platformOs": "linux"}
    elif method == "thread/list":
        result = {"data": [], "nextCursor": None}
    else:
        result = {}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    client = AppServerJsonlClient(
        (sys.executable, str(script)),
        request_timeout=2.0,
    )

    await client.start()
    result = await client.request("thread/list", {"limit": 1})
    await client.close()

    assert result == {"data": [], "nextCursor": None}
    assert client.process is None


@pytest.mark.asyncio
async def test_shared_websocket_client_uses_user_local_unix_socket(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "app-server.sock"
    methods: list[str] = []

    async def handler(websocket: Any) -> None:
        async for encoded in websocket:
            request = json.loads(encoded)
            methods.append(str(request.get("method")))
            if "id" not in request:
                continue
            result = (
                {
                    "userAgent": "fake",
                    "codexHome": "/tmp",
                    "platformFamily": "unix",
                    "platformOs": "linux",
                }
                if request.get("method") == "initialize"
                else {"data": [], "nextCursor": None}
            )
            await websocket.send(json.dumps({"id": request["id"], "result": result}))

    async with websockets.unix_serve(handler, str(socket_path)):
        client = AppServerWebSocketClient(socket_path, request_timeout=2.0)
        await client.start()
        result = await client.request("thread/list", {"limit": 1})
        await client.close()

    assert result == {"data": [], "nextCursor": None}
    assert methods == ["initialize", "initialized", "thread/list"]


@pytest.mark.asyncio
async def test_bridge_stop_does_not_stop_or_mutate_codex_threads(tmp_path: Path) -> None:
    store, runtime = registered_runtime(tmp_path)
    bridge = CodexAppServerEventBridge(
        store,
        runtime,
        enabled=False,
        transport="sidecar",
    )

    await bridge.start()
    await bridge.stop()

    assert bridge.status()["running"] is False
    assert bridge.status()["connected"] is False


def test_bridge_health_is_bounded_and_diagnostics_require_auth(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            token="secret",
            codex_event_bridge_enabled=False,
        )
    )
    with TestClient(app) as client:
        public = client.get("/healthz").json()["codex_event_bridge"]
        denied = client.get("/v2/codex/event-bridge")
        private = client.get(
            "/v2/codex/event-bridge",
            headers={"Authorization": "Bearer secret"},
        )

    assert set(public) == {
        "enabled",
        "running",
        "connected",
        "transport",
        "last_error",
    }
    assert denied.status_code == 401
    assert private.status_code == 200
    assert "target_count" in private.json()
