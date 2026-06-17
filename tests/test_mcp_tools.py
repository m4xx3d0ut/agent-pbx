import asyncio
import json
from pathlib import Path

import pytest

from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.store import Store


def write_codex_session(codex_home: Path, cwd: Path, session_id: str) -> None:
    session_path = (
        codex_home
        / "sessions"
        / "2026"
        / "06"
        / "17"
        / f"rollout-2026-06-17T18-52-25-{session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": str(cwd),
                    "timestamp": "2026-06-17T18:52:25.014Z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def tool_json(result: object) -> object:
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        return json.loads(content[0].text)
    return json.loads(result[0].text)  # type: ignore[index, attr-defined]


@pytest.mark.asyncio
async def test_mcp_reporting_and_command_tools(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    agent = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {"agent_id": "agent-1", "project": "demo", "name": "Agent One"},
        )
    )
    command = tool_json(
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "agent-1",
                "command_type": "send_input",
                "payload": {"message": "Proceed"},
            },
        )
    )
    ping = tool_json(
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "agent-1",
                "command_type": "ping",
                "payload": {"request": "pong"},
            },
        )
    )
    report = tool_json(
        await mcp.call_tool(
            "pbx_report_turn",
            {
                "agent_id": "agent-1",
                "project": "demo",
                "summary": "Done",
                "detail": "Full detail",
            },
        )
    )
    polled = tool_json(
        await mcp.call_tool(
            "pbx_poll_commands", {"agent_id": "agent-1", "wait_seconds": 0}
        )
    )
    acked = tool_json(
        await mcp.call_tool(
            "pbx_ack_command",
            {
                "command_id": command["command_id"],
                "agent_id": "agent-1",
                "result": {"ok": True},
            },
        )
    )
    inactive = tool_json(
        await mcp.call_tool(
            "pbx_set_active",
            {"agent_id": "agent-1", "active": False},
        )
    )

    assert agent["agent_id"] == "agent-1"
    assert agent["pbx_active"] is True
    assert ping["type"] == "ping"
    assert report["detail"] == "Full detail"
    assert polled[0]["command_id"] == command["command_id"]
    assert acked["status"] == "acked"
    assert inactive["pbx_active"] is False
    ack_events = [
        event for event in store.list_events() if event["type"] == "command_acked"
    ]
    assert ack_events[0]["payload"]["agent_id"] == "agent-1"
    active_events = [
        event
        for event in store.list_events()
        if event["type"] == "agent_pbx_active_changed"
    ]
    assert active_events[0]["payload"] == {
        "agent_id": "agent-1",
        "pbx_active": False,
    }


@pytest.mark.asyncio
async def test_mcp_register_agent_infers_codex_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    session_id = "019ed6ed-6e25-7d82-bf2f-0b3c377bd3c9"
    cwd.mkdir()
    write_codex_session(codex_home, cwd, session_id)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    agent = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "codex-k1s-workerbee-private-20260617",
                "project": "k1s-workerbee-private",
                "metadata": {"cwd": str(cwd), "pbx_mode": "report"},
            },
        )
    )

    assert agent["metadata"]["codex_session_id"] == session_id
    assert agent["metadata"]["codex_thread_id"] == session_id


@pytest.mark.asyncio
async def test_mcp_register_operator_does_not_infer_codex_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    session_id = "019ed6ed-6e25-7d82-bf2f-0b3c377bd3c9"
    cwd.mkdir()
    write_codex_session(codex_home, cwd, session_id)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    agent = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "operator-0-fork-caller-1",
                "project": "agent-pbx-operator",
                "agent_type": "operator",
                "metadata": {
                    "cwd": str(cwd),
                    "operator_role": "fork",
                    "source_codex_session_id": session_id,
                },
            },
        )
    )

    assert "codex_session_id" not in agent["metadata"]
    assert agent["metadata"]["source_codex_session_id"] == session_id


@pytest.mark.asyncio
async def test_mcp_set_active_unhides_dismissed_agent(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "agent-1", "project": "demo"},
    )
    store.dismiss_agent("agent-1")

    assert store.list_agents() == []

    active = tool_json(
        await mcp.call_tool(
            "pbx_set_active",
            {"agent_id": "agent-1", "active": True},
        )
    )

    assert active["agent_id"] == "agent-1"
    assert active["pbx_active"] is True
    assert [agent["agent_id"] for agent in store.list_agents()] == ["agent-1"]


@pytest.mark.asyncio
async def test_mcp_agent_runbook_tool(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    runbook = tool_json(await mcp.call_tool("pbx_agent_runbook", {}))

    assert runbook["title"] == "Agent PBX Runbook"
    assert any("report mode" in item for item in runbook["pbx_modes"])
    assert any("nohup" in item for item in runbook["pbx_modes"])
    assert any("pbx_poll_commands" in item for item in runbook["active_loop"])
    assert any("alert pickup mechanism" in item for item in runbook["active_loop"])
    assert "request_detail" in runbook["commands"]
    assert any("pbx_set_active" in item for item in runbook["session_stop"])


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_agents_and_invalid_acks(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "agent-1", "project": "demo"},
    )
    command = tool_json(
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "agent-1",
                "command_type": "send_input",
                "payload": {"message": "Proceed"},
            },
        )
    )

    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "missing",
                "command_type": "send_input",
                "payload": {"message": "Nope"},
            },
        )
    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_poll_commands", {"agent_id": "missing", "wait_seconds": 0}
        )
    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_ack_command",
            {
                "command_id": command["command_id"],
                "agent_id": "agent-1",
                "result": {"ok": True},
            },
        )

    await mcp.call_tool(
        "pbx_poll_commands", {"agent_id": "agent-1", "wait_seconds": 0}
    )
    acked = tool_json(
        await mcp.call_tool(
            "pbx_ack_command",
            {
                "command_id": command["command_id"],
                "agent_id": "agent-1",
                "result": {"ok": True},
            },
        )
    )

    assert acked["status"] == "acked"


@pytest.mark.asyncio
async def test_mcp_repeated_poll_picks_up_late_command(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "agent-1", "project": "demo"},
    )
    poll_task = asyncio.create_task(
        mcp.call_tool(
            "pbx_poll_commands",
            {
                "agent_id": "agent-1",
                "wait_seconds": 0,
                "max_wait_seconds": 1,
                "interval_seconds": 0.05,
            },
        )
    )
    await asyncio.sleep(0.1)
    command = tool_json(
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "agent-1",
                "command_type": "send_input",
                "payload": {"message": "late"},
            },
        )
    )

    polled = tool_json(await poll_task)
    agent = store.get_agent("agent-1")

    assert polled[0]["command_id"] == command["command_id"]
    assert polled[0]["status"] == "delivered"
    assert agent is not None
    assert agent["last_poll_at"] is not None
