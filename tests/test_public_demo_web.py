from __future__ import annotations

import importlib.util
import json
import shutil

import pytest

from agent_pbx.cli import main
from agent_pbx.demo.contract import DEMO_CONTRACT_VERSION
from agent_pbx.demo.web import (
    build_public_demo_server,
    public_demo_doctor,
)


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
        max_sessions=2,
        session_ttl_seconds=60,
    )
    client = TestClient(TestServer(await server._make_app()))
    try:
        await client.start_server()
        health = await client.get("/healthz")
        assert health.status == 200
        assert (await health.json())["max_sessions"] == 2
        assert health.headers["X-Content-Type-Options"] == "nosniff"

        contract = await client.get("/demo-contract.json")
        assert contract.status == 200
        assert (await contract.json())["api_version"] == DEMO_CONTRACT_VERSION

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
