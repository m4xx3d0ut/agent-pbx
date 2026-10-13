from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil

import pytest

from agent_pbx.cli import main
from agent_pbx.demo.contract import DEMO_CONTRACT_VERSION
from agent_pbx.demo.web import (
    _receive_initial_public_demo_terminal_size,
    build_public_demo_server,
    inject_public_demo_terminal_resize_bootstrap,
    public_demo_doctor,
)


class _FakeWSMsgType:
    TEXT = "text"
    CLOSE = "close"
    CLOSED = "closed"
    ERROR = "error"


class _FakeWSMessage:
    def __init__(self, type_: str, value: object = None) -> None:
        self.type = type_
        self.value = value

    def json(self) -> object:
        return self.value


class _FakeWebSocket:
    def __init__(self, messages: list[_FakeWSMessage]) -> None:
        self.messages = list(messages)

    async def receive(self) -> _FakeWSMessage:
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(60)
        raise AssertionError("unreachable")


def test_demo_contract_cli_emits_versioned_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "contract"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["api_version"] == DEMO_CONTRACT_VERSION
    assert value["transport"]["per_connection_runtime"] is True


def test_demo_doctor_reports_required_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_pbx.demo.web.shutil.which",
        lambda command: f"/usr/bin/{command}" if command in {"git", "tmux"} else None,
    )
    monkeypatch.setattr(
        "agent_pbx.demo.web.importlib.util.find_spec",
        lambda name: object() if name == "textual_serve" else None,
    )

    report = public_demo_doctor()

    assert report["ok"] is True
    assert report["failures"] == []
    assert report["checks"]["textual_serve"] is True


def test_demo_web_cli_passes_only_explicit_public_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("agent_pbx.demo.web.serve_public_demo", fake_serve)
    assert (
        main(
            [
                "demo",
                "web",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--public-url",
                "https://demo.example",
                "--allowed-origin",
                "https://www.example",
                "--allow-public-bind",
                "--max-sessions",
                "4",
                "--session-ttl-seconds",
                "300",
            ]
        )
        == 0
    )
    assert captured == {
        "host": "0.0.0.0",
        "port": 9000,
        "public_url": "https://demo.example",
        "allowed_origins": ("https://www.example",),
        "allow_public_bind": True,
        "max_sessions": 4,
        "session_ttl_seconds": 300,
        "tmux_bin": "tmux",
        "debug": False,
    }


def test_public_demo_terminal_resize_bootstrap_is_idempotent() -> None:
    html = "<html><head><title>Demo</title></head><body></body></html>"

    injected = inject_public_demo_terminal_resize_bootstrap(html)

    assert injected.count('id="agent-pbx-terminal-resize-sync"') == 1
    assert 'src="/agent-pbx-terminal-resize.js"' in injected
    assert inject_public_demo_terminal_resize_bootstrap(injected) == injected


async def test_initial_terminal_handshake_uses_authoritative_resize() -> None:
    ping = _FakeWSMessage(_FakeWSMsgType.TEXT, ["ping", "before-resize"])
    websocket = _FakeWebSocket(
        [
            ping,
            _FakeWSMessage(
                _FakeWSMsgType.TEXT,
                ["resize", {"width": 181, "height": 61}],
            ),
        ]
    )

    width, height, buffered, connected = (
        await _receive_initial_public_demo_terminal_size(
            websocket,
            _FakeWSMsgType,
            fallback_width=80,
            fallback_height=24,
            timeout_seconds=0.1,
        )
    )

    assert (width, height) == (181, 61)
    assert buffered == (ping,)
    assert connected is True


async def test_initial_terminal_handshake_falls_back_after_timeout() -> None:
    websocket = _FakeWebSocket([])

    width, height, buffered, connected = (
        await _receive_initial_public_demo_terminal_size(
            websocket,
            _FakeWSMsgType,
            fallback_width=999,
            fallback_height=2,
            timeout_seconds=0.01,
        )
    )

    assert (width, height) == (300, 12)
    assert buffered == ()
    assert connected is True


async def test_initial_terminal_handshake_stops_on_disconnect() -> None:
    websocket = _FakeWebSocket([_FakeWSMessage(_FakeWSMsgType.CLOSED)])

    width, height, buffered, connected = (
        await _receive_initial_public_demo_terminal_size(
            websocket,
            _FakeWSMsgType,
            fallback_width=120,
            fallback_height=40,
            timeout_seconds=0.1,
        )
    )

    assert (width, height) == (120, 40)
    assert buffered == ()
    assert connected is False


@pytest.mark.skipif(
    importlib.util.find_spec("textual_serve") is None,
    reason="requires the optional web-demo dependency",
)
async def test_public_demo_server_exposes_health_contract_and_origin_guard() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    server, web_root = build_public_demo_server(
        host="127.0.0.1",
        port=8123,
        public_url="http://127.0.0.1:8123",
        allowed_origins=("https://www.example",),
        max_sessions=2,
        session_ttl_seconds=60,
    )
    assert "AGENT_PBX_TOKEN" not in server.public_demo_environment
    assert "OPENAI_API_KEY" not in server.public_demo_environment
    assert "TMUX" not in server.public_demo_environment
    client = TestClient(TestServer(await server._make_app()))
    try:
        await client.start_server()
        health = await client.get("/healthz")
        assert health.status == 200
        assert (await health.json())["max_sessions"] == 2
        assert health.headers["X-Content-Type-Options"] == "nosniff"

        index = await client.get("/")
        index_text = await index.text()
        assert index.status == 200
        assert index_text.count('id="agent-pbx-terminal-resize-sync"') == 1
        assert 'src="/agent-pbx-terminal-resize.js"' in index_text

        resize_script = await client.get("/agent-pbx-terminal-resize.js")
        resize_script_text = await resize_script.text()
        assert resize_script.status == 200
        assert resize_script.content_type == "application/javascript"
        assert resize_script.headers["Cache-Control"] == "no-cache"
        assert "ResizeObserver" in resize_script_text
        assert "document.fonts?.ready" in resize_script_text
        assert "window.visualViewport" in resize_script_text
        assert "requestAnimationFrame(() => requestAnimationFrame" in resize_script_text
        assert "const upstreamLoad = window.onload" in resize_script_text
        assert "window.onload = null" in resize_script_text
        assert "await document.fonts.ready" in resize_script_text
        assert resize_script_text.count("await nextPaint()") == 2
        assert "upstreamLoad.call(window, event)" in resize_script_text
        assert 'typeof window.onresize !== "function"' in resize_script_text
        assert "setInterval" not in resize_script_text

        contract = await client.get(
            "/demo-contract.json", headers={"Origin": "https://www.example"}
        )
        assert contract.status == 200
        assert (await contract.json())["api_version"] == DEMO_CONTRACT_VERSION
        assert contract.headers["Access-Control-Allow-Origin"] == "https://www.example"
        assert contract.headers["Vary"] == "Origin"

        denied_contract = await client.get(
            "/demo-contract.json", headers={"Origin": "https://denied.example"}
        )
        assert denied_contract.status == 200
        assert "Access-Control-Allow-Origin" not in denied_contract.headers
        assert denied_contract.headers["Vary"] == "Origin"

        same_origin_contract = await client.get("/demo-contract.json")
        assert same_origin_contract.status == 200
        assert "Access-Control-Allow-Origin" not in same_origin_contract.headers
        assert same_origin_contract.headers["Vary"] == "Origin"

        forbidden = await client.get("/ws")
        assert forbidden.status == 403
        assert await forbidden.text() == "browser origin is not allowed"
    finally:
        await client.close()
        shutil.rmtree(web_root, ignore_errors=True)


@pytest.mark.serial
@pytest.mark.skipif(
    importlib.util.find_spec("textual_serve") is None
    or shutil.which("tmux") is None
    or shutil.which("git") is None,
    reason="requires the optional web-demo dependency, tmux, and git",
)
async def test_browser_connection_starts_real_tui_and_cleans_up() -> None:
    import asyncio

    from aiohttp import WSMsgType
    from aiohttp.test_utils import TestClient, TestServer

    origin = "http://127.0.0.1:8124"
    server, web_root = build_public_demo_server(
        host="127.0.0.1",
        port=8124,
        public_url=origin,
        max_sessions=1,
        session_ttl_seconds=60,
    )
    client = TestClient(TestServer(await server._make_app()))
    try:
        await client.start_server()
        websocket = await client.ws_connect(
            "/ws?width=120&height=40", headers={"Origin": origin}
        )
        await websocket.send_json(["resize", {"width": 140, "height": 44}])
        received_terminal_frame = False
        for _ in range(100):
            try:
                message = await websocket.receive(timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if message.type in {WSMsgType.BINARY, WSMsgType.TEXT}:
                received_terminal_frame = True
                break
            if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break
        assert received_terminal_frame
        assert server.active_sessions == 1
        await websocket.close()
        for _ in range(100):
            if server.active_sessions == 0:
                break
            await asyncio.sleep(0.05)
        assert server.active_sessions == 0
    finally:
        await client.close()
        shutil.rmtree(web_root, ignore_errors=True)
