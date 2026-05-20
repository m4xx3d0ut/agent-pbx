import json
from pathlib import Path

import pytest

from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.store import Store


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
            {"command_id": command["command_id"], "result": {"ok": True}},
        )
    )

    assert agent["agent_id"] == "agent-1"
    assert report["detail"] == "Full detail"
    assert polled[0]["command_id"] == command["command_id"]
    assert acked["status"] == "acked"
