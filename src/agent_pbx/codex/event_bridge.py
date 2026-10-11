from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

import websockets

from ..contracts import CodexRuntimeState
from ..store import Store
from .runtime import CodexRuntimeService, runtime_identity


CODEX_EVENT_BRIDGE_ENV = "AGENT_PBX_CODEX_EVENT_BRIDGE"
CODEX_EVENT_BRIDGE_POLL_SECONDS_ENV = "AGENT_PBX_CODEX_EVENT_BRIDGE_POLL_SECONDS"
CODEX_EVENT_BRIDGE_TRANSPORT_ENV = "AGENT_PBX_CODEX_EVENT_BRIDGE_TRANSPORT"
CODEX_EVENT_BRIDGE_BIN_ENV = "AGENT_PBX_CODEX_BIN"

SUBAGENT_SOURCE_KINDS = (
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
)
SUBAGENT_ITEM_TYPES = {"collabAgentToolCall", "subAgentActivity"}
CHILD_THREAD_STATES = {
    "active": CodexRuntimeState.THINKING,
    "idle": CodexRuntimeState.READY,
    "notLoaded": CodexRuntimeState.UNKNOWN,
    "systemError": CodexRuntimeState.ERROR,
}


class AppServerBridgeError(RuntimeError):
    """Base error for the passive Codex app-server bridge."""


class AppServerRequestError(AppServerBridgeError):
    def __init__(self, code: object, message: object) -> None:
        self.code = str(code or "request_failed")[:80]
        # Keep protocol text in memory only long enough to classify a failure.
        # Public bridge status deliberately exposes only the bounded code.
        self.protocol_message = str(message or "")[:240]
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class BridgeTarget:
    agent_id: str
    thread_id: str


class AppServerJsonlClient:
    """Small request client for the local Codex app-server JSONL protocol."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        request_timeout: float = 5.0,
        output_limit: int = 16 * 1024 * 1024,
    ) -> None:
        self.command = tuple(command)
        self.request_timeout = max(0.1, request_timeout)
        self.output_limit = max(64 * 1024, output_limit)
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self.notification_handler: Any = None

    async def start(self) -> None:
        if self.process is not None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=self.output_limit,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(),
            name="agent-pbx-codex-app-server-reader",
        )
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "agent-pbx",
                    "title": "Agent PBX passive runtime bridge",
                    "version": "2",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "account/updated",
                        "configWarning",
                        "remoteControl/status/changed",
                    ],
                },
            },
        )
        await self.notify("initialized", {})

    async def close(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        self._fail_pending(AppServerBridgeError("app-server connection closed"))

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerBridgeError("app-server is not connected")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                }
            )
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
        except Exception:
            self._pending.pop(request_id, None)
            raise
        error = response.get("error")
        if isinstance(error, Mapping):
            raise AppServerRequestError(error.get("code"), error.get("message"))
        result = response.get("result")
        return dict(result) if isinstance(result, Mapping) else {}

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        await self._write({"method": method, "params": dict(params or {})})

    async def _write(self, payload: Mapping[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AppServerBridgeError("app-server is not connected")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        async with self._write_lock:
            process.stdin.write(encoded.encode("utf-8") + b"\n")
            await process.stdin.drain()

    async def _read_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise AppServerBridgeError("app-server exited")
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                response_id = message.get("id")
                if isinstance(response_id, int) and "method" not in message:
                    future = self._pending.pop(response_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                method = message.get("method")
                params = message.get("params")
                if (
                    isinstance(method, str)
                    and isinstance(params, Mapping)
                    and self.notification_handler is not None
                ):
                    result = self.notification_handler(method, dict(params))
                    if asyncio.iscoroutine(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(exc)

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


class AppServerWebSocketClient:
    """JSON-RPC client for Codex's user-local shared daemon socket."""

    def __init__(
        self,
        socket_path: Path,
        *,
        request_timeout: float = 5.0,
        output_limit: int = 16 * 1024 * 1024,
    ) -> None:
        self.socket_path = socket_path
        self.request_timeout = max(0.1, request_timeout)
        self.output_limit = max(64 * 1024, output_limit)
        self.websocket: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self.notification_handler: Any = None

    async def start(self) -> None:
        if self.websocket is not None:
            return
        self.websocket = await websockets.unix_connect(
            str(self.socket_path),
            uri="ws://localhost/",
            open_timeout=self.request_timeout,
            close_timeout=1.0,
            max_size=self.output_limit,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(),
            name="agent-pbx-codex-shared-app-server-reader",
        )
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "agent-pbx",
                    "title": "Agent PBX passive runtime bridge",
                    "version": "2",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "account/updated",
                        "configWarning",
                        "remoteControl/status/changed",
                    ],
                },
            },
        )
        await self.notify("initialized", {})

    async def close(self) -> None:
        websocket = self.websocket
        self.websocket = None
        if websocket is not None:
            await websocket.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        self._fail_pending(AppServerBridgeError("app-server connection closed"))

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.websocket is None:
            raise AppServerBridgeError("app-server is not connected")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write(
                {
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                }
            )
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
        except Exception:
            self._pending.pop(request_id, None)
            raise
        error = response.get("error")
        if isinstance(error, Mapping):
            raise AppServerRequestError(error.get("code"), error.get("message"))
        result = response.get("result")
        return dict(result) if isinstance(result, Mapping) else {}

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        await self._write({"method": method, "params": dict(params or {})})

    async def _write(self, payload: Mapping[str, Any]) -> None:
        websocket = self.websocket
        if websocket is None:
            raise AppServerBridgeError("app-server is not connected")
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        async with self._write_lock:
            await websocket.send(encoded)

    async def _read_loop(self) -> None:
        websocket = self.websocket
        if websocket is None:
            return
        try:
            async for encoded in websocket:
                if not isinstance(encoded, (str, bytes)):
                    continue
                try:
                    message = json.loads(encoded)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                response_id = message.get("id")
                if isinstance(response_id, int) and "method" not in message:
                    future = self._pending.pop(response_id, None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                method = message.get("method")
                params = message.get("params")
                if (
                    isinstance(method, str)
                    and isinstance(params, Mapping)
                    and self.notification_handler is not None
                ):
                    result = self.notification_handler(method, dict(params))
                    if asyncio.iscoroutine(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(exc)

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


class CodexAppServerEventBridge:
    """Read-only bridge from Codex structured state into PBX runtime views.

    The bridge never starts, resumes, forks, steers, interrupts, or writes to a
    Codex thread. It discovers child threads from the local state database via
    app-server and reads recent child lifecycle items only for exact PBX-bound
    root thread IDs. Ambiguous PBX mappings are skipped.
    """

    def __init__(
        self,
        store: Store,
        runtime: CodexRuntimeService,
        *,
        codex_bin: str = "codex",
        enabled: bool = True,
        poll_seconds: float = 5.0,
        request_timeout: float = 5.0,
        transport: str = "auto",
        control_socket: Path | None = None,
        child_page_size: int = 100,
        child_max_pages: int = 5,
        item_page_size: int = 128,
        client_factory: Any = AppServerJsonlClient,
        shared_client_factory: Any = AppServerWebSocketClient,
    ) -> None:
        if transport not in {"auto", "shared", "sidecar"}:
            raise ValueError("Codex event bridge transport must be auto, shared, or sidecar")
        self.store = store
        self.runtime = runtime
        self.codex_bin = codex_bin
        self.enabled = enabled
        self.poll_seconds = max(0.25, poll_seconds)
        self.request_timeout = max(0.1, request_timeout)
        self.transport = transport
        self.control_socket = control_socket or (
            Path.home() / ".codex" / "app-server-control" / "app-server-control.sock"
        )
        self.child_page_size = max(1, min(500, child_page_size))
        self.child_max_pages = max(1, min(20, child_max_pages))
        self.item_page_size = max(1, min(500, item_page_size))
        self.client_factory = client_factory
        self.shared_client_factory = shared_client_factory
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._client: AppServerJsonlClient | AppServerWebSocketClient | None = None
        self._targets_by_thread: dict[str, BridgeTarget] = {}
        self._fingerprints: OrderedDict[str, None] = OrderedDict()
        self._status: dict[str, Any] = {
            "enabled": enabled,
            "running": False,
            "connected": False,
            "transport": None,
            "last_error": None,
            "last_reconcile_at": None,
            "target_count": 0,
            "ambiguous_session_count": 0,
            "child_count": 0,
            "observed_updates": 0,
            "reconnects": 0,
        }

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self.run(),
            name="agent-pbx-codex-event-bridge",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_client()
        self._status["running"] = False
        self._status["connected"] = False

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def public_status(self) -> dict[str, Any]:
        return {
            key: self._status[key]
            for key in (
                "enabled",
                "running",
                "connected",
                "transport",
                "last_error",
            )
        }

    async def run(self) -> None:
        self._status["running"] = True
        backoff = 1.0
        try:
            while not self._stop.is_set():
                try:
                    client, selected_transport = await self._connect()
                    self._client = client
                    self._status.update(
                        connected=True,
                        transport=selected_transport,
                        last_error=None,
                    )
                    backoff = 1.0
                    while not self._stop.is_set():
                        await self.reconcile_once(client)
                        try:
                            await asyncio.wait_for(
                                self._stop.wait(),
                                timeout=self.poll_seconds,
                            )
                        except asyncio.TimeoutError:
                            pass
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._status["connected"] = False
                    self._status["last_error"] = bridge_error_code(exc)
                    self._status["reconnects"] += 1
                    await self._close_client()
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(30.0, backoff * 2.0)
        finally:
            await self._close_client()
            self._status["running"] = False
            self._status["connected"] = False

    async def reconcile_once(self, client: Any) -> None:
        targets, ambiguous = await asyncio.to_thread(self._load_targets)
        self._targets_by_thread = {target.thread_id: target for target in targets}
        self._status["target_count"] = len(targets)
        self._status["ambiguous_session_count"] = ambiguous
        rows = await self._list_subagent_threads(client)
        associated = associate_child_threads(rows, self._targets_by_thread)
        self._status["child_count"] = len(associated)

        root_threads: set[str] = set()
        for target, row in associated:
            root_threads.add(target.thread_id)
            await self._observe_discovered_thread(target, row)
        for thread_id in sorted(root_threads):
            target = self._targets_by_thread.get(thread_id)
            if target is not None:
                await self._read_recent_child_items(client, target)
        self._status["last_reconcile_at"] = time.time()

    async def _connect(self) -> tuple[Any, str]:
        candidates: list[tuple[str, object]] = []
        if self.transport in {"auto", "shared"} and self.control_socket.exists():
            candidates.append(("shared", self.control_socket))
        if self.transport in {"auto", "sidecar"}:
            candidates.append(("sidecar", (self.codex_bin, "app-server")))
        if not candidates:
            raise AppServerBridgeError("shared app-server control socket is unavailable")
        last_error: Exception | None = None
        for name, target in candidates:
            if name == "shared":
                client = self.shared_client_factory(
                    target,
                    request_timeout=min(self.request_timeout, 2.0),
                )
            else:
                client = self.client_factory(
                    target,
                    request_timeout=self.request_timeout,
                )
            client.notification_handler = self._handle_notification
            try:
                await client.start()
            except Exception as exc:
                last_error = exc
                await client.close()
                continue
            return client, name
        raise AppServerBridgeError(
            bridge_error_code(last_error or AppServerBridgeError("connect failed"))
        )

    async def _close_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.close()

    def _load_targets(self) -> tuple[list[BridgeTarget], int]:
        by_thread: dict[str, list[str]] = {}
        for agent in self.store.list_agents(include_hidden=False):
            identity = runtime_identity(self.store, agent)
            thread_id = str(identity.codex_session_id or "").strip()
            if thread_id:
                by_thread.setdefault(thread_id, []).append(identity.entity_id)
        targets: list[BridgeTarget] = []
        ambiguous = 0
        for thread_id, agent_ids in by_thread.items():
            if len(agent_ids) != 1:
                ambiguous += 1
                continue
            targets.append(BridgeTarget(agent_id=agent_ids[0], thread_id=thread_id))
        return targets, ambiguous

    async def _list_subagent_threads(
        self,
        client: Any,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(self.child_max_pages):
            params: dict[str, Any] = {
                "limit": self.child_page_size,
                "sortDirection": "desc",
                "sortKey": "updated_at",
                "sourceKinds": list(SUBAGENT_SOURCE_KINDS),
                "useStateDbOnly": True,
            }
            if cursor:
                params["cursor"] = cursor
            result = await client.request("thread/list", params)
            data = result.get("data")
            if not isinstance(data, list):
                break
            rows.extend(item for item in data if isinstance(item, dict))
            raw_cursor = result.get("nextCursor")
            cursor = str(raw_cursor) if raw_cursor else None
            if not cursor:
                break
        return rows

    async def _observe_discovered_thread(
        self,
        target: BridgeTarget,
        row: Mapping[str, Any],
    ) -> None:
        child_id = public_text(row.get("id"), 240)
        if not child_id:
            return
        status = row.get("status")
        status_type = public_text(
            status.get("type") if isinstance(status, Mapping) else status,
            80,
        )
        state = CHILD_THREAD_STATES.get(status_type or "", CodexRuntimeState.UNKNOWN)
        source = thread_spawn_source(row)
        role = public_text(row.get("agentRole"), 120) or public_text(
            source.get("agent_role"),
            120,
        )
        started_at = safe_timestamp(row.get("createdAt"))
        observed_at = safe_timestamp(row.get("updatedAt")) or time.time()
        fingerprint = child_fingerprint(
            "thread",
            target.agent_id,
            {
                "child_id": child_id,
                "state": state.value,
                "role": role,
                "model": public_text(row.get("model"), 160),
                "reasoning_effort": public_text(row.get("reasoningEffort"), 80),
                "started_at": started_at,
                "observed_at": observed_at,
            },
        )
        if not self._remember(fingerprint):
            return
        await asyncio.to_thread(
            self.runtime.observe_discovered_child,
            target.agent_id,
            thread_id=target.thread_id,
            child_id=child_id,
            state=state,
            role=role,
            model=public_text(row.get("model"), 160),
            reasoning_effort=public_text(row.get("reasoningEffort"), 80),
            started_at=started_at,
            observed_at=observed_at,
        )
        self._status["observed_updates"] += 1

    async def _read_recent_child_items(
        self,
        client: Any,
        target: BridgeTarget,
    ) -> None:
        try:
            result = await client.request(
                "thread/items/list",
                {
                    "threadId": target.thread_id,
                    "limit": self.item_page_size,
                    "sortDirection": "desc",
                },
            )
        except AppServerRequestError:
            return
        data = result.get("data")
        if not isinstance(data, list):
            return
        for wrapper in reversed(data):
            if not isinstance(wrapper, Mapping):
                continue
            item = safe_child_item(wrapper.get("item"))
            if item is None:
                continue
            method = (
                "item/completed"
                if safe_timestamp_ms(wrapper.get("completedAtMs")) is not None
                else "item/started"
            )
            observed_at = (
                safe_timestamp_ms(wrapper.get("completedAtMs"))
                or safe_timestamp_ms(wrapper.get("startedAtMs"))
                or time.time()
            )
            fingerprint = child_fingerprint(method, target.agent_id, item)
            if not self._remember(fingerprint):
                continue
            try:
                await asyncio.to_thread(
                    self.runtime.observe_historical_item,
                    target.agent_id,
                    thread_id=target.thread_id,
                    method=method,
                    params={"item": item},
                    observed_at=observed_at,
                )
            except ValueError:
                continue
            self._status["observed_updates"] += 1

    async def _handle_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if method not in {"item/started", "item/completed"}:
            return
        thread_id = public_text(params.get("threadId"), 240)
        target = self._targets_by_thread.get(thread_id or "")
        item = safe_child_item(params.get("item"))
        if target is None or item is None:
            return
        timestamp_key = "completedAtMs" if method == "item/completed" else "startedAtMs"
        observed_at = safe_timestamp_ms(params.get(timestamp_key)) or time.time()
        fingerprint = child_fingerprint(method, target.agent_id, item)
        if not self._remember(fingerprint):
            return
        try:
            await asyncio.to_thread(
                self.runtime.observe_protocol,
                target.agent_id,
                thread_id=target.thread_id,
                method=method,
                params={"item": item},
                observed_at=observed_at,
            )
        except (KeyError, ValueError):
            return
        self._status["observed_updates"] += 1

    def _remember(self, fingerprint: str) -> bool:
        if fingerprint in self._fingerprints:
            self._fingerprints.move_to_end(fingerprint)
            return False
        self._fingerprints[fingerprint] = None
        while len(self._fingerprints) > 8192:
            self._fingerprints.popitem(last=False)
        return True


def associate_child_threads(
    rows: Sequence[Mapping[str, Any]],
    targets_by_thread: Mapping[str, BridgeTarget],
) -> list[tuple[BridgeTarget, Mapping[str, Any]]]:
    by_id = {
        child_id: row
        for row in rows
        if (child_id := public_text(row.get("id"), 240))
    }
    result: list[tuple[BridgeTarget, Mapping[str, Any]]] = []
    for row in rows:
        parent_id = child_parent_thread_id(row)
        seen: set[str] = set()
        target: BridgeTarget | None = None
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            target = targets_by_thread.get(parent_id)
            if target is not None:
                break
            parent_row = by_id.get(parent_id)
            parent_id = child_parent_thread_id(parent_row) if parent_row else None
        if target is not None:
            result.append((target, row))
    return result


def child_parent_thread_id(row: Mapping[str, Any]) -> str | None:
    return public_text(row.get("parentThreadId"), 240) or public_text(
        thread_spawn_source(row).get("parent_thread_id"),
        240,
    )


def thread_spawn_source(row: Mapping[str, Any]) -> Mapping[str, Any]:
    source = row.get("source")
    if not isinstance(source, Mapping):
        return {}
    subagent = source.get("subAgent")
    if not isinstance(subagent, Mapping):
        return {}
    spawn = subagent.get("thread_spawn")
    return spawn if isinstance(spawn, Mapping) else {}


def safe_child_item(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    item_type = public_text(raw.get("type"), 80)
    if item_type not in SUBAGENT_ITEM_TYPES:
        return None
    item: dict[str, Any] = {"type": item_type}
    for key, limit in (
        ("id", 240),
        ("tool", 80),
        ("status", 80),
        ("kind", 80),
        ("senderThreadId", 240),
        ("agentThreadId", 240),
        ("model", 160),
        ("reasoningEffort", 80),
        ("agentRole", 120),
    ):
        value = public_text(raw.get(key), limit)
        if value:
            item[key] = value
    receivers = raw.get("receiverThreadIds")
    if isinstance(receivers, list):
        item["receiverThreadIds"] = [
            value
            for entry in receivers[:32]
            if (value := public_text(entry, 240))
        ]
    states = raw.get("agentsStates")
    if isinstance(states, Mapping):
        safe_states: dict[str, dict[str, str]] = {}
        for raw_id, raw_state in list(states.items())[:32]:
            child_id = public_text(raw_id, 240)
            if not child_id or not isinstance(raw_state, Mapping):
                continue
            status = public_text(raw_state.get("status"), 80)
            safe_states[child_id] = {"status": status or ""}
        item["agentsStates"] = safe_states
    return item


def child_fingerprint(kind: str, agent_id: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        [kind, agent_id, payload],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    # This is a deduplication key over an already-redacted public projection,
    # not a retained hash of prompt or tool content.
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def public_text(value: object, limit: int) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip().replace("\r", " ").replace("\n", " ")
    return text[:limit] if text else None


def safe_timestamp(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def safe_timestamp_ms(value: object) -> float | None:
    parsed = safe_timestamp(value)
    return parsed / 1000.0 if parsed is not None else None


def bridge_error_code(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "app_server_timeout"
    if isinstance(exc, AppServerRequestError):
        return f"app_server_request:{exc.code}"
    if isinstance(exc, FileNotFoundError):
        return "codex_not_found"
    if isinstance(exc, PermissionError):
        return "app_server_permission_denied"
    if isinstance(exc, AppServerBridgeError):
        return "app_server_unavailable"
    return "bridge_error"


def env_bridge_enabled(default: bool = True) -> bool:
    raw = os.getenv(CODEX_EVENT_BRIDGE_ENV)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def env_bridge_poll_seconds(default: float = 5.0) -> float:
    try:
        return max(0.25, float(os.getenv(CODEX_EVENT_BRIDGE_POLL_SECONDS_ENV, "")))
    except ValueError:
        return default
