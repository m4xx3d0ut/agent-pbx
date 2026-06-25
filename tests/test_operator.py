from pathlib import Path

import pytest

from agent_pbx import tmux as tmux_support
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.operator import OperatorService, operator_runbook_payload
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def tool_json(result: object) -> object:
    import json

    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        return json.loads(content[0].text)
    return json.loads(result[0].text)  # type: ignore[index, attr-defined]


def register_operator_and_caller(store: Store, tmp_path: Path) -> None:
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "nohup",
                "cwd": str(tmp_path / "caller-1"),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        )
    )


def test_operator_runbook_requires_visible_pbx_forks() -> None:
    runbook = operator_runbook_payload()
    delivery = "\n".join(runbook["delivery"])

    assert "Agent PBX forked operator session" in delivery
    assert "visible tmux/Codex pane" in delivery
    assert "Do not spawn or use Codex internal subagents" in delivery
    assert "multi_agent_v1" in delivery


def test_operator_campaign_queue_delivery_and_state(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        status="running",
        metadata={"pbx_mode": "nohup"},
    )

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Update projects",
        objective="Apply the same fix across callers.",
        criteria=["tests pass"],
        assignments=[
            {
                "target_agent_id": "caller-1",
                "title": "Patch caller 1",
                "prompt": "Make the requested fix.",
                "criteria": ["pytest passes"],
            }
        ],
        delivery="queue",
    )

    assignment = campaign["assignments"][0]
    command = store.get_command(assignment["last_command_id"])

    assert campaign["status"] == "running"
    assert assignment["state"] == "waiting"
    assert assignment["operator_fork_id"] == fork["operator_fork_id"]
    assert command is not None
    assert command["agent_id"] == "operator-0-fork-caller-1"
    assert command["status"] == "queued"
    assert command["payload"]["campaign_id"] == campaign["campaign_id"]
    assert command["payload"]["target_agent_id"] == "caller-1"
    assert command["payload"]["fork_agent_id"] == "operator-0-fork-caller-1"
    agents = {agent["agent_id"]: agent for agent in store.list_agents()}
    assert agents["caller-1"]["active_campaign_count"] == 1
    campaign_events = [
        event
        for event in store.list_events(limit=50)
        if event["type"] == "operator_campaign_event"
        and event["payload"].get("campaign_id") == campaign["campaign_id"]
    ]
    assert campaign_events
    assert campaign_events[-1]["payload"]["operator_agent_id"] == "operator-0"
    assert campaign_events[-1]["payload"]["operator_fork_id"] == fork["operator_fork_id"]
    assert campaign_events[-1]["payload"]["fork_agent_id"] == "operator-0-fork-caller-1"

    updated = service.report_assignment(
        operator_agent_id="operator-0",
        campaign_id=campaign["campaign_id"],
        assignment_id=assignment["assignment_id"],
        state="complete",
        summary="Caller complete",
        detail="Caller reported tests passed.",
    )

    assert updated["state"] == "complete"
    assert updated["last_report_id"]
    report = store.get_report(updated["last_report_id"])
    assert report is not None
    assert report["metadata"]["campaign_id"] == campaign["campaign_id"]
    assert report["metadata"]["completion_state"] == "complete"


def test_operator_campaign_tmux_delivery_records_sent_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        )
    )
    sent: list[tuple[str, str]] = []
    pane = tmux_support.TmuxPane(
        session_name="s",
        window_index="0",
        pane_index="1",
        pane_id="%1",
        active=True,
        current_command="codex",
        title="caller-1",
        cwd=str(caller_cwd),
        width=100,
        height=30,
        history_size=10,
        window_name="operator-0-fork-caller-1",
    )
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.list_panes",
        lambda tmux_bin="tmux": [pane],
    )
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.send_text",
        lambda target, text, **kwargs: sent.append((target, text)),
    )
    service = OperatorService(store)
    fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        tmux_pane_id="%1",
        status="running",
        metadata={"tmux_pane_id": "%1"},
    )

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Local caller",
        objective="Dispatch through tmux.",
        criteria=[],
        assignments=[
            {
                "target_agent_id": "caller-1",
                "prompt": "Work locally.",
            }
        ],
        delivery="tmux",
    )

    assignment = campaign["assignments"][0]
    command = store.get_command(assignment["last_command_id"])

    assert sent and sent[0][0] == "%1"
    assert command is not None
    assert command["agent_id"] == "operator-0-fork-caller-1"
    assert command["status"] == "sent"
    assert command["payload"]["tmux_pane_id"] == "%1"
    assert command["payload"]["operator_fork_id"] == fork["operator_fork_id"]
    assert assignment["state"] == "sent"


@pytest.mark.asyncio
async def test_mcp_operator_campaign_tools_queue_delivery(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
    )
    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "pbx_mode": "nohup",
                "cwd": str(tmp_path / "caller-1"),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        },
    )
    fork = tool_json(
        await mcp.call_tool(
            "pbx_operator_ensure_fork",
            {
                "operator_agent_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "fork_agent_id": "operator-0-fork-caller-1",
                "status": "running",
                "metadata": {"pbx_mode": "nohup"},
            },
        )
    )

    campaign = tool_json(
        await mcp.call_tool(
            "pbx_operator_start_campaign",
            {
                "operator_agent_id": "operator-0",
                "title": "MCP campaign",
                "objective": "Verify MCP operator tools.",
                "criteria": ["done report"],
                "assignments": [
                    {
                        "target_agent_id": "caller-1",
                        "prompt": "Please complete the task.",
                    }
                ],
                "delivery": "queue",
            },
        )
    )
    status = tool_json(
        await mcp.call_tool(
            "pbx_operator_campaign_status",
            {"operator_agent_id": "operator-0"},
        )
    )
    assignment = campaign["assignments"][0]
    reported = tool_json(
        await mcp.call_tool(
            "pbx_operator_report_assignment",
            {
                "operator_agent_id": "operator-0",
                "campaign_id": campaign["campaign_id"],
                "assignment_id": assignment["assignment_id"],
                "state": "complete",
                "summary": "Complete",
                "detail": "Done.",
            },
        )
    )

    assert status[0]["campaign_id"] == campaign["campaign_id"]
    assert assignment["state"] == "waiting"
    assert assignment["operator_fork_id"] == fork["operator_fork_id"]
    assert reported["state"] == "complete"


def test_operator_campaign_blocks_when_caller_has_no_codex_session(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path / "caller-1")},
        )
    )
    service = OperatorService(store)

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Missing fork metadata",
        objective="Show blocked fork state.",
        criteria=[],
        assignments=[{"target_agent_id": "caller-1", "prompt": "Work."}],
        delivery="tmux",
    )

    assignment = campaign["assignments"][0]
    forks = store.list_operator_forks(logical_operator_agent_id="operator-0")

    assert assignment["state"] == "blocked"
    assert forks[0]["status"] == "blocked"
    assert "metadata.codex_session_id" in forks[0]["summary"]
    placeholder_agent = store.get_agent(forks[0]["fork_agent_id"])
    assert placeholder_agent is not None
    assert placeholder_agent["pbx_active"] is False
    assert placeholder_agent["dismissed_at"] is not None


def test_operator_missing_session_refuses_running_external_fork(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path / "caller-1")},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="external-worker",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"operator_role": "root", "reason": "caller has no session"},
            pbx_active=True,
        )
    )
    service = OperatorService(store)

    fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="external-worker",
        status="running",
        summary="External worker launched after dispatch failed.",
    )
    external_worker = store.get_agent("external-worker")

    assert fork["status"] == "blocked"
    assert "metadata.codex_session_id" in fork["summary"]
    assert fork["metadata"]["operator_fork_launchable"] is False
    assert external_worker is not None
    assert external_worker["pbx_active"] is False
    assert external_worker["dismissed_at"] is not None


def test_operator_pending_fork_can_be_activated_after_tui_launch(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Pending fork",
        objective="Create pending fork state before launch.",
        criteria=[],
        assignments=[{"target_agent_id": "caller-1", "prompt": "Work."}],
        delivery="tmux",
    )
    pending = store.list_operator_forks(logical_operator_agent_id="operator-0")[0]
    pending_agent = store.get_agent(pending["fork_agent_id"])

    assert campaign["assignments"][0]["state"] == "blocked"
    assert pending["status"] == "blocked"
    assert pending["metadata"]["operator_fork_pending"] is True
    assert pending_agent is not None
    assert pending_agent["pbx_active"] is False

    activated = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id=pending["fork_agent_id"],
        tmux_pane_id="%42",
        status="running",
        summary="Fork launched from Agent PBX TUI.",
        metadata={"pbx_mode": "report", "tmux_pane_id": "%42"},
    )
    activated_agent = store.get_agent(pending["fork_agent_id"])

    assert activated["operator_fork_id"] == pending["operator_fork_id"]
    assert activated["status"] == "running"
    assert activated["tmux_pane_id"] == "%42"
    assert activated["metadata"]["operator_fork_pending"] is False
    assert activated_agent is not None
    assert activated_agent["pbx_active"] is True
    assert activated_agent["metadata"]["operator_fork_pending"] is False
    assert activated_agent["metadata"]["tmux_pane_id"] == "%42"


def test_operator_campaign_tmux_delivery_prefers_repaired_fork_pane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        )
    )
    service = OperatorService(store)
    fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        tmux_pane_id="%1",
        status="running",
        metadata={"pbx_mode": "report", "tmux_pane_id": "%1", "cwd": str(caller_cwd)},
    )
    repaired = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id=fork["fork_agent_id"],
        tmux_pane_id="%2",
        status="running",
        summary="Fork pane repaired after operator restore.",
        metadata={"active_tmux_pane_id": "%2"},
    )
    fork_agent = store.get_agent(fork["fork_agent_id"])

    assert repaired["operator_fork_id"] == fork["operator_fork_id"]
    assert repaired["tmux_pane_id"] == "%2"
    assert fork_agent is not None
    assert fork_agent["metadata"]["tmux_pane_id"] == "%2"
    assert fork_agent["metadata"]["active_tmux_pane_id"] == "%2"

    stale_pane = tmux_support.TmuxPane(
        session_name="s",
        window_index="0",
        pane_index="1",
        pane_id="%1",
        active=False,
        current_command="codex",
        title="old-fork",
        cwd=str(caller_cwd),
        width=100,
        height=30,
        history_size=10,
        window_name="old-fork",
    )
    repaired_pane = tmux_support.TmuxPane(
        session_name="s",
        window_index="0",
        pane_index="2",
        pane_id="%2",
        active=True,
        current_command="codex",
        title="new-fork",
        cwd=str(caller_cwd),
        width=100,
        height=30,
        history_size=10,
        window_name="operator-0-fork-caller-1",
    )
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.list_panes",
        lambda tmux_bin="tmux": [stale_pane, repaired_pane],
    )
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.send_text",
        lambda target, text, **kwargs: sent.append((target, text)),
    )

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Repaired fork route",
        objective="Use the active fork pane after restore.",
        criteria=[],
        assignments=[{"target_agent_id": "caller-1", "prompt": "Continue."}],
        delivery="tmux",
    )
    assignment = campaign["assignments"][0]
    command = store.get_command(assignment["last_command_id"])

    assert sent and sent[0][0] == "%2"
    assert command is not None
    assert command["agent_id"] == "operator-0-fork-caller-1"
    assert command["payload"]["target_agent_id"] == "caller-1"
    assert command["payload"]["fork_agent_id"] == "operator-0-fork-caller-1"
    assert command["payload"]["tmux_pane_id"] == "%2"


def test_operator_campaign_tmux_delivery_prefers_fork_window_over_caller_pane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path)},
        )
    )
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        )
    )
    service = OperatorService(store)
    service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        tmux_pane_id="%1",
        status="running",
        metadata={"pbx_mode": "report", "tmux_pane_id": "%1", "cwd": str(caller_cwd)},
    )

    caller_pane = tmux_support.TmuxPane(
        session_name="caller-session",
        window_index="0",
        pane_index="1",
        pane_id="%1",
        active=False,
        current_command="codex",
        title="caller-1",
        cwd=str(caller_cwd),
        width=100,
        height=30,
        history_size=10,
        window_name="zsh",
    )
    fork_pane = tmux_support.TmuxPane(
        session_name="operator-session",
        window_index="0",
        pane_index="2",
        pane_id="%2",
        active=True,
        current_command="codex",
        title="agent-pbx",
        cwd=str(tmp_path / "agent-pbx"),
        width=100,
        height=30,
        history_size=10,
        window_name="operator-0-fork-caller-1",
    )
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.list_panes",
        lambda tmux_bin="tmux": [caller_pane, fork_pane],
    )
    monkeypatch.setattr(
        "agent_pbx.operator.tmux_support.send_text",
        lambda target, text, **kwargs: sent.append((target, text)),
    )

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Fork window route",
        objective="Route to the fork window, not a same-cwd caller pane.",
        criteria=[],
        assignments=[{"target_agent_id": "caller-1", "prompt": "Continue."}],
        delivery="tmux",
    )
    assignment = campaign["assignments"][0]
    command = store.get_command(assignment["last_command_id"])

    assert sent and sent[0][0] == "%2"
    assert command is not None
    assert command["agent_id"] == "operator-0-fork-caller-1"
    assert command["payload"]["tmux_pane_id"] == "%2"
