from pathlib import Path
import json

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
    assert "must not implement caller repo changes directly" in delivery
    assert "Do not spawn or use Codex internal subagents" in delivery
    assert "Manual KB seed runs" in delivery
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


def test_operator_knowledge_link_proposal_approval_keeps_fork_graph(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-B",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "nohup", "cwd": str(tmp_path / "operator-B")},
        )
    )
    service = OperatorService(store)
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        status="running",
        metadata={"pbx_mode": "report"},
    )

    proposed = service.propose_knowledge_handoff(
        operator_agent_id=review["fork_agent_id"],
        source_agent_id=review["fork_agent_id"],
        target_agent_id="operator-B",
        source_operator_fork_id=review["operator_fork_id"],
        message="Transfer the routing model and ask follow-up questions as needed.",
        summary="Routing handoff",
    )
    link = proposed["link"]
    turn = proposed["turn"]

    assert link["logical_operator_agent_id"] == "operator-0"
    assert link["operator_agent_id"] == review["fork_agent_id"]
    assert link["source_operator_fork_id"] == review["operator_fork_id"]
    assert link["status"] == "proposed"
    assert link["pending_turn_count"] == 1
    assert turn["delivery_status"] == "pending_approval"
    assert "does not change operator fork ownership" in turn["message"]
    assert store.list_operator_fork_edges() == []
    assert len(store.list_operator_forks(logical_operator_agent_id="operator-0")) == 1
    assert store.get_operator_fork_for_agent("operator-B") is None

    delivered = service.approve_knowledge_turn(
        operator_agent_id="operator-0",
        link_id=link["link_id"],
        turn_id=turn["turn_id"],
        delivery="queue",
        metadata={"approved_by": "test"},
    )
    command = store.get_command(delivered["command"]["command_id"])

    assert delivered["link"]["status"] == "active"
    assert delivered["turn"]["delivery_status"] == "queued"
    assert delivered["turn"]["metadata"]["approved_by_operator_agent_id"] == "operator-0"
    assert command is not None
    assert command["agent_id"] == "operator-B"
    assert command["payload"]["source"] == "operator_knowledge_turn"
    assert command["payload"]["knowledge_link_id"] == link["link_id"]
    assert command["payload"]["sender_agent_id"] == review["fork_agent_id"]
    assert store.list_operator_fork_edges() == []
    assert len(store.list_operator_forks(logical_operator_agent_id="operator-0")) == 1
    assert store.get_operator_fork_for_agent("operator-B") is None

    response = service.send_knowledge_turn(
        operator_agent_id="operator-0",
        link_id=link["link_id"],
        sender_agent_id="operator-B",
        recipient_agent_id=review["fork_agent_id"],
        message="What constraints should I preserve?",
        turn_type="question",
        delivery="record_only",
    )

    assert response["turn"]["turn_type"] == "question"
    assert response["turn"]["delivery_status"] == "recorded"
    context = service.knowledge_context(
        operator_agent_id="operator-0",
        link_id=link["link_id"],
    )
    assert [item["turn_id"] for item in context["turns"]] == [
        turn["turn_id"],
        response["turn"]["turn_id"],
    ]

    closed = service.close_knowledge_link(
        operator_agent_id="operator-0",
        link_id=link["link_id"],
        summary="Knowledge transfer complete",
    )
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None


def test_operator_handoff_blocks_until_required_target_fork_launch(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-B",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "nohup", "cwd": str(tmp_path / "operator-B")},
        )
    )
    service = OperatorService(store)
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        status="running",
        metadata={"pbx_mode": "report"},
    )

    proposed = service.propose_knowledge_handoff(
        operator_agent_id=review["fork_agent_id"],
        source_agent_id=review["fork_agent_id"],
        target_agent_id="operator-B",
        source_operator_fork_id=review["operator_fork_id"],
        message="Teach operator-B the routing model and start the checkpoint.",
        summary="Routing checkpoint handoff",
        metadata={
            "target_caller_agent_id": "caller-1",
            "allowed_mutation_scope": "static/unit validation only",
            "required_artifacts": [{"path": "artifacts/contract.json"}],
            "artifact_bundle": [{"path": "secret.env", "redacted": True}],
            "expires_at": 4_000_000_000.0,
        },
    )
    handoff = proposed["handoff"]

    assert handoff["status"] == "proposed"
    assert handoff["target_caller_agent_id"] == "caller-1"
    assert handoff["required_artifacts"][0]["path"] == "artifacts/contract.json"
    assert store.list_operator_fork_edges() == []

    pending = service.approve_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
        delivery="queue",
    )
    pending_handoff = pending["handoff"]
    pending_fork = store.get_operator_fork(pending_handoff["target_operator_fork_id"])

    assert pending["command"] is None
    assert pending_handoff["status"] == "pending_launch"
    assert pending_fork is not None
    assert pending_fork["logical_operator_agent_id"] == "operator-B"
    assert pending_fork["source_caller_agent_id"] == "caller-1"
    assert pending_fork["metadata"]["operator_fork_pending"] is True
    assert store.get_operator_knowledge_turn(proposed["turn"]["turn_id"])["delivery_status"] == "pending_approval"  # type: ignore[index]

    launched = service.ensure_fork(
        operator_agent_id="operator-B",
        source_caller_agent_id="caller-1",
        fork_agent_id=pending_fork["fork_agent_id"],
        tmux_pane_id="%42",
        status="running",
        metadata={"pbx_mode": "nohup", "tmux_pane_id": "%42"},
    )
    assert launched["operator_fork_id"] == pending_fork["operator_fork_id"]
    assert launched["metadata"]["operator_fork_pending"] is False

    delivered = service.approve_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
        delivery="queue",
    )
    command = store.get_command(delivered["command"]["command_id"])
    delivered_turn = store.get_operator_knowledge_turn(proposed["turn"]["turn_id"])

    assert delivered["handoff"]["status"] == "sent"
    assert delivered["handoff"]["delivery_evidence"]["agent_acknowledged"] is False
    assert command is not None
    assert command["agent_id"] == "operator-B"
    assert command["payload"]["source"] == "operator_handoff"
    assert command["payload"]["handoff_id"] == handoff["handoff_id"]
    assert delivered_turn is not None
    assert delivered_turn["delivery_status"] == "queued"
    assert store.get_operator_knowledge_link(proposed["link"]["link_id"])["status"] == "active"  # type: ignore[index]

    running = service.ack_handoff(
        operator_agent_id="operator-B",
        handoff_id=handoff["handoff_id"],
        status="running",
        summary="Received handoff and started.",
        detail="Inputs present; target fork launched.",
    )

    assert running["status"] == "running"
    assert running["acknowledged_at"] is not None
    assert running["started_at"] is not None
    assert running["delivery_evidence"]["agent_acknowledged"] is True
    assert running["delivery_evidence"]["agent_started"] is True
    assert store.list_operator_fork_edges() == []


def test_operator_handoff_expires_before_start(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-B",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "nohup", "cwd": str(tmp_path / "operator-B")},
        )
    )
    service = OperatorService(store)
    handoff = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-B",
        message="Use the short-lived helper now.",
        objective="TTL-sensitive handoff",
        expires_at=1.0,
    )

    expired = service.get_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
    )

    assert expired["status"] == "expired"
    assert expired["completed_at"] is not None
    assert expired["safe_to_start"] is False


def test_operator_custom_terminal_states_do_not_count_active(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        status="running",
        metadata={"pbx_mode": "nohup"},
    )
    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Custom terminal state",
        objective="Finish with a caveated status.",
        criteria=[],
        assignments=[{"target_agent_id": "caller-1", "prompt": "Continue."}],
        delivery="queue",
    )
    assignment = campaign["assignments"][0]

    updated_assignment = service.report_assignment(
        operator_agent_id="operator-0",
        campaign_id=campaign["campaign_id"],
        assignment_id=assignment["assignment_id"],
        state="complete_with_caveats",
        summary="Done with caveats",
        detail="Validation passed with a documented caveat.",
    )
    updated_campaign = service.finish_campaign(
        operator_agent_id="operator-0",
        campaign_id=campaign["campaign_id"],
        status="complete_readiness_partial",
        summary="Campaign complete with partial readiness.",
        detail="All known action items are terminal.",
    )

    agents = {agent["agent_id"]: agent for agent in store.list_agents()}
    assert updated_assignment["completed_at"] is not None
    assert updated_campaign["completed_at"] is not None
    assert agents["caller-1"]["active_campaign_count"] == 0
    assert agents["operator-0"]["active_campaign_count"] == 0


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
        session_name="agent-pbx-operators",
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


@pytest.mark.asyncio
async def test_mcp_operator_project_spawn_request_tool(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
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
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        },
    )
    review = tool_json(
        await mcp.call_tool(
            "pbx_operator_ensure_fork",
            {
                "operator_agent_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "fork_agent_id": "operator-0-fork-caller-1-review-1",
                "fork_track_id": "review-1",
                "fork_purpose": "review",
                "access_mode": "review_readonly",
                "source_cwd": str(caller_cwd),
                "work_root": str(tmp_path / ".agent-pbx-review" / "review"),
                "status": "running",
                "metadata": {"pbx_mode": "report"},
            },
        )
    )

    request = tool_json(
        await mcp.call_tool(
            "pbx_operator_request_project_spawn",
            {
                "operator_agent_id": "operator-0",
                "review_fork_id": review["operator_fork_id"],
                "project_name": "Next Demo",
                "instructions": "Build the extracted app.",
                "mode": "empty",
            },
        )
    )

    assert request["status"] == "pending"
    assert request["target_path"] == str(tmp_path / "Next-Demo")
    assert request["instructions"] == "Build the extracted app."


@pytest.mark.asyncio
async def test_mcp_operator_handoff_tools_create_approve_and_ack(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    for payload in [
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
        {
            "agent_id": "operator-B",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "nohup", "cwd": str(tmp_path / "operator-B")},
        },
    ]:
        await mcp.call_tool("pbx_register_agent", payload)

    handoff = tool_json(
        await mcp.call_tool(
            "pbx_operator_create_handoff",
            {
                "operator_agent_id": "operator-0",
                "source_agent_id": "operator-0",
                "target_operator_agent_id": "operator-B",
                "message": "Share the domain model.",
                "objective": "Knowledge share",
                "required_artifacts": ["notes.md"],
                "expires_at": 4_000_000_000.0,
            },
        )
    )
    approved = tool_json(
        await mcp.call_tool(
            "pbx_operator_approve_handoff",
            {
                "operator_agent_id": "operator-0",
                "handoff_id": handoff["handoff_id"],
                "delivery": "queue",
            },
        )
    )
    listed = tool_json(
        await mcp.call_tool(
            "pbx_operator_list_handoffs",
            {"target_operator_agent_id": "operator-B"},
        )
    )
    fetched = tool_json(
        await mcp.call_tool(
            "pbx_operator_get_handoff",
            {
                "operator_agent_id": "operator-B",
                "handoff_id": handoff["handoff_id"],
            },
        )
    )
    acked = tool_json(
        await mcp.call_tool(
            "pbx_operator_ack_handoff",
            {
                "operator_agent_id": "operator-B",
                "handoff_id": handoff["handoff_id"],
                "summary": "Received handoff.",
                "status": "acknowledged",
            },
        )
    )

    assert approved["handoff"]["status"] == "sent"
    assert approved["command"]["agent_id"] == "operator-B"
    assert approved["command"]["payload"]["source"] == "operator_handoff"
    assert listed[0]["handoff_id"] == handoff["handoff_id"]
    assert fetched["safe_to_start"] is True
    assert acked["status"] == "acknowledged"
    assert acked["delivery_evidence"]["agent_acknowledged"] is True


@pytest.mark.asyncio
async def test_mcp_operator_knowledge_link_tools_scope_and_deliver(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    mcp = build_mcp_server(store)

    for payload in [
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
        {
            "agent_id": "operator-B",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "nohup", "cwd": str(tmp_path / "operator-B")},
        },
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-1",
                "codex_host_id": "local",
            },
        },
    ]:
        await mcp.call_tool("pbx_register_agent", payload)
    review = tool_json(
        await mcp.call_tool(
            "pbx_operator_ensure_fork",
            {
                "operator_agent_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "fork_agent_id": "operator-0-fork-caller-1-review-1",
                "fork_track_id": "review-1",
                "fork_purpose": "review",
                "access_mode": "review_readonly",
                "source_cwd": str(caller_cwd),
                "work_root": str(tmp_path / ".agent-pbx-review" / "review"),
                "status": "running",
                "metadata": {"pbx_mode": "report"},
            },
        )
    )

    proposed = tool_json(
        await mcp.call_tool(
            "pbx_operator_propose_knowledge_handoff",
            {
                "operator_agent_id": review["fork_agent_id"],
                "source_agent_id": review["fork_agent_id"],
                "target_agent_id": "operator-B",
                "source_operator_fork_id": review["operator_fork_id"],
                "message": "Share the source routing constraints.",
                "summary": "Routing context",
            },
        )
    )
    link = proposed["link"]
    turn = proposed["turn"]

    listed = tool_json(
        await mcp.call_tool(
            "pbx_operator_list_knowledge_links",
            {"operator_agent_id": "operator-0"},
        )
    )
    context = tool_json(
        await mcp.call_tool(
            "pbx_operator_get_knowledge_context",
            {
                "operator_agent_id": "operator-0",
                "link_id": link["link_id"],
            },
        )
    )
    delivered = tool_json(
        await mcp.call_tool(
            "pbx_operator_approve_knowledge_turn",
            {
                "operator_agent_id": "operator-0",
                "link_id": link["link_id"],
                "turn_id": turn["turn_id"],
                "delivery": "queue",
            },
        )
    )

    assert [item["link_id"] for item in listed] == [link["link_id"]]
    assert context["link"]["link_id"] == link["link_id"]
    assert context["turns"][0]["turn_id"] == turn["turn_id"]
    assert delivered["turn"]["delivery_status"] == "queued"
    assert delivered["command"]["agent_id"] == "operator-B"
    assert delivered["command"]["payload"]["source"] == "operator_knowledge_turn"
    assert store.list_operator_fork_edges() == []
    assert len(store.list_operator_forks(logical_operator_agent_id="operator-0")) == 1


@pytest.mark.asyncio
async def test_mcp_operator_kb_tools_lifecycle(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    for payload in [
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
        {
            "agent_id": "operator-B",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path / "operator-B")},
        },
    ]:
        await mcp.call_tool("pbx_register_agent", payload)

    proposed = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_propose",
            {
                "operator_agent_id": "operator-0",
                "scope": "project",
                "project": "agent-pbx",
                "title": "Operator KB routing",
                "summary": "Active KB entries are shared across operators.",
                "body": "Promote only after root-operator review.",
                "tags": ["kb", "routing"],
            },
        )
    )
    proposed_entries = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_search",
            {
                "operator_agent_id": "operator-0",
                "status": "proposed",
                "query": "routing",
            },
        )
    )
    updated = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_update",
            {
                "operator_agent_id": "operator-0",
                "kb_id": proposed["kb_id"],
                "updates": {
                    "summary": "Active KB entries are visible across operators.",
                    "body": "Root operators promote reviewed KB entries.",
                    "tags": ["kb", "routing", "reviewed"],
                },
            },
        )
    )
    promoted = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_promote",
            {
                "operator_agent_id": "operator-0",
                "kb_id": proposed["kb_id"],
            },
        )
    )
    reject_candidate = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_propose",
            {
                "operator_agent_id": "operator-0",
                "scope": "project",
                "project": "agent-pbx",
                "title": "Discarded KB",
                "summary": "Superseded proposal.",
                "body": "Do not publish.",
            },
        )
    )
    rejected = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_reject",
            {
                "operator_agent_id": "operator-0",
                "kb_id": reject_candidate["kb_id"],
                "summary": "Superseded.",
            },
        )
    )
    active_entries = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_search",
            {
                "operator_agent_id": "operator-B",
                "query": "routing",
            },
        )
    )
    fetched = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_get",
            {
                "operator_agent_id": "operator-B",
                "kb_id": proposed["kb_id"],
            },
        )
    )
    exported = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_export",
            {
                "operator_agent_id": "operator-0",
                "query": "routing",
            },
        )
    )
    imported = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_import",
            {
                "operator_agent_id": "operator-B",
                "bundle": exported,
                "import_status": "proposed",
            },
        )
    )

    assert proposed["status"] == "proposed"
    assert proposed["redaction_status"] == "clean"
    assert [entry["kb_id"] for entry in proposed_entries] == [proposed["kb_id"]]
    assert updated["summary"] == "Active KB entries are visible across operators."
    assert updated["tags"] == ["kb", "routing", "reviewed"]
    assert promoted["status"] == "active"
    assert rejected["status"] == "rejected"
    assert active_entries[0]["kb_id"] == proposed["kb_id"]
    assert fetched["kb_id"] == proposed["kb_id"]
    assert exported["format"] == "agent-pbx-operator-kb-v1"
    assert imported["imported_count"] == 1
    assert imported["entries"][0]["created_by_operator_agent_id"] == "operator-B"


async def test_mcp_operator_kb_seed_run_lifecycle(tmp_path: Path) -> None:
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    for payload in [
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
        {
            "agent_id": "operator-0-fork-review",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_cwd": str(source_cwd),
                "pbx_mode": "nohup",
            },
        },
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "pbx_mode": "report",
                "cwd": str(source_cwd),
                "codex_session_id": "session-caller-1",
            },
        },
    ]:
        await mcp.call_tool("pbx_register_agent", payload)

    seeded = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_seed",
            {
                "operator_agent_id": "operator-0-fork-review",
                "scope": "repo",
                "project": "demo",
                "repo_root": str(source_cwd),
                "delivery": "queue",
            },
        )
    )
    seed_run = seeded["seed_run"]
    command = seeded["command"]
    seed_sync_key = seed_run["metadata"]["seed_sync_key"]

    listed = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_list_seed_runs",
            {"operator_agent_id": "operator-0", "status": "queued"},
        )
    )
    fetched = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_get_seed_run",
            {
                "operator_agent_id": "operator-0-fork-review",
                "seed_run_id": seed_run["seed_run_id"],
            },
        )
    )
    proposed = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_propose",
            {
                "operator_agent_id": "operator-0-fork-review",
                "scope": "repo",
                "project": "demo",
                "repo_root": str(source_cwd),
                "title": "Seeded operator workflow",
                "summary": "Seed runs propose durable operator workflow guidance.",
                "body": "Use seed-run metadata to keep imported knowledge auditable.",
                "metadata": {
                    "seed_run_id": seed_run["seed_run_id"],
                    "seed_sync_key": seed_sync_key,
                    "seed_type": seed_run["seed_type"],
                    "extraction_version": "operator_kb_seed_v1",
                },
            },
        )
    )
    completed = tool_json(
        await mcp.call_tool(
            "pbx_operator_kb_update_seed_run",
            {
                "operator_agent_id": "operator-0-fork-review",
                "seed_run_id": seed_run["seed_run_id"],
                "status": "complete",
                "summary": "Created one KB proposal.",
                "metadata": {"proposal_count": 1},
            },
        )
    )

    assert seed_run["status"] == "queued"
    assert seed_run["logical_operator_agent_id"] == "operator-0"
    assert seed_run["source_operator_agent_id"] == "operator-0-fork-review"
    assert "pbx_operator_kb_propose" in seed_run["prompt"]
    assert "pbx_operator_kb_update_seed_run" in seed_run["prompt"]
    assert command["agent_id"] == "operator-0-fork-review"
    assert command["payload"]["source"] == "operator_kb_seed_run"
    assert command["payload"]["seed_run_id"] == seed_run["seed_run_id"]
    assert [run["seed_run_id"] for run in listed] == [seed_run["seed_run_id"]]
    assert fetched["seed_run_id"] == seed_run["seed_run_id"]
    assert proposed["created_by_operator_agent_id"] == "operator-0"
    assert proposed["created_by_agent_id"] == "operator-0-fork-review"
    assert proposed["metadata"]["seed_run_id"] == seed_run["seed_run_id"]
    assert len(proposed["sources"]) == 1
    source = proposed["sources"][0]
    assert source["source_type"] == "kb_seed_run"
    assert source["source_id"] == seed_run["seed_run_id"]
    assert source["metadata"] == {
        "seed_type": seed_run["seed_type"],
        "seed_sync_key": seed_sync_key,
    }
    assert completed["status"] == "complete"
    assert completed["completed_at"] is not None
    assert completed["metadata"]["proposal_count"] == 1


def test_operator_kb_seed_run_provenance_requires_seeded_source(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    for agent_id in [
        "operator-0",
        "operator-0-fork-review-1",
        "operator-0-fork-review-2",
    ]:
        metadata = {"pbx_mode": "report", "cwd": str(tmp_path)}
        if "-fork-" in agent_id:
            metadata = {
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_cwd": str(source_cwd),
                "pbx_mode": "nohup",
            }
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata=metadata,
            )
        )
    service = OperatorService(store)

    seeded = service.start_kb_seed_run(
        operator_agent_id="operator-0-fork-review-1",
        scope="repo",
        project="demo",
        repo_root=str(source_cwd),
        delivery="queue",
    )
    seed_run = seeded["seed_run"]
    seed_sync_key = seed_run["metadata"]["seed_sync_key"]

    with pytest.raises(ValueError, match="different source operator"):
        service.propose_kb_entry(
            operator_agent_id="operator-0-fork-review-2",
            title="Wrong fork seed proposal",
            summary="This should not attach to another fork's seed run.",
            body="Seed provenance belongs to the seeded source operator.",
            metadata={
                "seed_run_id": seed_run["seed_run_id"],
                "seed_sync_key": seed_sync_key,
            },
        )
    with pytest.raises(ValueError, match="different source operator"):
        service.update_kb_seed_run(
            operator_agent_id="operator-0-fork-review-2",
            seed_run_id=seed_run["seed_run_id"],
            status="complete",
            summary="Wrong fork complete.",
        )
    completed = service.update_kb_seed_run(
        operator_agent_id="operator-0",
        seed_run_id=seed_run["seed_run_id"],
        status="complete",
        summary="Root closed the seed run.",
    )
    assert completed["status"] == "complete"


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


def test_operator_rebind_fork_source_session_preserves_identity(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    source_cwd = tmp_path / "caller-1"
    fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1-oldhash",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        source_cwd=str(source_cwd),
        work_root=str(tmp_path / ".agent-pbx-review" / "review-1"),
        tmux_pane_id="%42",
        status="running",
        metadata={"pbx_mode": "report"},
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "nohup",
                "cwd": str(source_cwd),
                "codex_session_id": "session-caller-2",
                "codex_host_id": "local",
            },
        )
    )

    updated = service.rebind_fork_source_session(
        operator_agent_id="operator-0",
        operator_fork_id=fork["operator_fork_id"],
        source_caller_agent_id="caller-1",
        old_source_codex_session_id="session-caller-1",
        new_source_codex_session_id="session-caller-2",
        source_cwd=str(source_cwd),
        codex_host_id="local",
        reason="caller restarted",
    )

    assert updated["operator_fork_id"] == fork["operator_fork_id"]
    assert updated["fork_agent_id"] == fork["fork_agent_id"]
    assert updated["source_codex_session_id"] == "session-caller-2"
    assert updated["tmux_pane_id"] == "%42"
    assert updated["metadata"]["source_codex_session_id"] == "session-caller-2"
    assert (
        updated["metadata"]["rebound_from_source_codex_session_id"]
        == "session-caller-1"
    )
    assert updated["metadata"]["previous_source_codex_session_ids"] == [
        "session-caller-1"
    ]
    agent = store.get_agent(fork["fork_agent_id"])
    assert agent is not None
    assert agent["metadata"]["source_codex_session_id"] == "session-caller-2"
    events = [
        event
        for event in store.list_events()
        if event["type"] == "operator_fork_source_session_rebound"
    ]
    assert events
    assert events[0]["payload"]["old_source_codex_session_id"] == "session-caller-1"
    assert events[0]["payload"]["new_source_codex_session_id"] == "session-caller-2"


def test_operator_rebind_fork_source_session_rejects_track_collision(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    source_cwd = tmp_path / "caller-1"
    stale = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1-oldhash",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        source_cwd=str(source_cwd),
        work_root=str(tmp_path / ".agent-pbx-review" / "review-1"),
        tmux_pane_id="%42",
        status="running",
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={
                "pbx_mode": "nohup",
                "cwd": str(source_cwd),
                "codex_session_id": "session-caller-2",
                "codex_host_id": "local",
            },
        )
    )
    service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1-newhash",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        source_cwd=str(source_cwd),
        work_root=str(tmp_path / ".agent-pbx-review" / "review-1"),
        tmux_pane_id="%43",
        status="running",
    )

    with pytest.raises(ValueError, match="current source session already has this fork track"):
        service.rebind_fork_source_session(
            operator_agent_id="operator-0",
            operator_fork_id=stale["operator_fork_id"],
            source_caller_agent_id="caller-1",
            old_source_codex_session_id="session-caller-1",
            new_source_codex_session_id="session-caller-2",
            source_cwd=str(source_cwd),
            codex_host_id="local",
        )


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
        session_name="agent-pbx-operators",
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
        session_name="agent-pbx-operators",
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
        session_name="agent-pbx-operators",
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


def test_operator_allows_default_and_review_forks_for_same_source_session(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)

    default = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        status="running",
        metadata={"pbx_mode": "nohup"},
    )
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        work_root=str(tmp_path / "review"),
        status="running",
        metadata={"pbx_mode": "nohup"},
    )

    assert default["operator_fork_id"] != review["operator_fork_id"]
    assert default["fork_track_id"] == "default"
    assert default["fork_purpose"] == "edit"
    assert review["fork_track_id"] == "review-1"
    assert review["fork_purpose"] == "review"
    assert review["access_mode"] == "review_readonly"
    assert review["work_root"] == str(tmp_path / "review")
    assert (
        service.default_fork_agent_id("operator-0", "caller-1", "session-caller-1")
        == "operator-0-fork-caller-1-86e61603"
    )
    review_forks = service.list_forks(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_track_id="review-1",
    )
    assert [fork["operator_fork_id"] for fork in review_forks] == [
        review["operator_fork_id"]
    ]


def test_operator_campaign_can_dispatch_to_explicit_review_fork(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        status="running",
        metadata={"pbx_mode": "nohup"},
    )
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        work_root=str(tmp_path / "review"),
        status="running",
        metadata={"pbx_mode": "nohup"},
    )

    campaign = service.start_campaign(
        operator_agent_id="operator-0",
        title="Parallel review",
        objective="Review without touching source.",
        criteria=["Findings reported"],
        assignments=[
            {
                "target_agent_id": "caller-1",
                "operator_fork_id": review["operator_fork_id"],
                "fork_track_id": "review-1",
                "prompt": "Review the diff.",
            }
        ],
        delivery="queue",
    )

    assignment = campaign["assignments"][0]
    command = store.get_command(assignment["last_command_id"])

    assert assignment["operator_fork_id"] == review["operator_fork_id"]
    assert assignment["fork_track_id"] == "review-1"
    assert command is not None
    assert command["agent_id"] == "operator-0-fork-caller-1-review-1"
    assert command["payload"]["operator_fork_id"] == review["operator_fork_id"]
    assert command["payload"]["fork_track_id"] == "review-1"
    assert command["payload"]["fork_purpose"] == "review"
    assert command["payload"]["access_mode"] == "review_readonly"


def test_operator_review_escalation_routes_to_idle_default_edit_fork(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    default = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        status="running",
        metadata={"pbx_mode": "nohup"},
    )
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        work_root=str(tmp_path / "review"),
        status="running",
        metadata={"pbx_mode": "nohup"},
    )

    command = service.route_review_escalation(
        operator_agent_id="operator-0",
        review_fork_id=review["operator_fork_id"],
        message="Apply this small fix in the source tree.",
        delivery="queue",
    )

    assert command["agent_id"] == "operator-0-fork-caller-1"
    assert command["payload"]["operator_fork_id"] == default["operator_fork_id"]
    assert command["payload"]["fork_track_id"] == "default"
    assert command["payload"]["source"] == "operator_review_escalation"
    assert (
        "Review fork: operator-0-fork-caller-1-review-1"
        in command["payload"]["message"]
    )
    events = [
        event
        for event in store.list_events(limit=20)
        if event["type"] == "operator_review_escalation"
    ]
    assert events
    assert events[-1]["payload"]["route"] == "primary_idle_edit_fork"


def test_operator_review_fork_can_request_sibling_project_spawn(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    register_operator_and_caller(store, tmp_path)
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
    review = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1-review-1",
        fork_track_id="review-1",
        fork_purpose="review",
        access_mode="review_readonly",
        source_cwd=str(caller_cwd),
        work_root=str(tmp_path / ".agent-pbx-review" / "review"),
        status="running",
        metadata={"pbx_mode": "report"},
    )

    request = service.request_project_spawn(
        operator_agent_id="operator-0",
        review_fork_id=review["operator_fork_id"],
        project_name="Next Demo",
        instructions="Create a focused extraction.",
        mode="clone_source",
    )

    assert request["status"] == "pending"
    assert request["mode"] == "clone_source"
    assert request["target_slug"] == "Next-Demo"
    assert request["target_parent"] == str(tmp_path)
    assert request["target_path"] == str(tmp_path / "Next-Demo")
    assert request["source_cwd"] == str(caller_cwd)
    assert request["review_fork_agent_id"] == "operator-0-fork-caller-1-review-1"
    listed = service.list_project_spawn_requests(
        operator_agent_id="operator-0",
        status="pending",
    )
    assert [item["spawn_request_id"] for item in listed] == [
        request["spawn_request_id"]
    ]
    store.register_agent(
        AgentRegisterRequest(
            agent_id="codex-Next-Demo",
            project="Next-Demo",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path / "Next-Demo")},
        )
    )
    updated = service.update_project_spawn_request(
        spawn_request_id=request["spawn_request_id"],
        status="launched",
        launched_agent_id="codex-Next-Demo",
        tmux_pane_id="%42",
    )
    assert updated["status"] == "launched"
    assert updated["launched_agent_id"] == "codex-Next-Demo"
    assert updated["completed_at"] is not None
    events = [
        event
        for event in store.list_events(limit=20)
        if event["type"].startswith("operator_project_spawn_")
    ]
    assert [event["type"] for event in events] == [
        "operator_project_spawn_requested",
        "operator_project_spawn_launched",
    ]


def test_operator_project_spawn_requires_review_readonly_fork(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator_and_caller(store, tmp_path)
    service = OperatorService(store)
    edit_fork = service.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-0-fork-caller-1",
        fork_track_id="default",
        fork_purpose="edit",
        access_mode="edit",
        source_cwd=str(tmp_path / "caller-1"),
        status="running",
        metadata={"pbx_mode": "report"},
    )

    with pytest.raises(ValueError, match="review fork"):
        service.request_project_spawn(
            operator_agent_id="operator-0",
            review_fork_id=edit_fork["operator_fork_id"],
            project_name="Next Demo",
            instructions="Create a sibling project.",
        )


def test_operator_campaign_tmux_delivery_survives_drifted_fork_metadata(
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
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE agents
            SET project = ?,
                metadata_json = ?
            WHERE agent_id = ?
            """,
            (
                "agent-pbx-operator",
                json.dumps(
                    {
                        "agent_type": "operator",
                        "operator_role": "root",
                        "pbx_mode": "report",
                        "cwd": str(caller_cwd),
                        "tmux_pane_id": "%1",
                    }
                ),
                "operator-0-fork-caller-1",
            ),
        )

    caller_pane = tmux_support.TmuxPane(
        session_name="k1s",
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
        window_name="zsh",
    )
    fork_pane = tmux_support.TmuxPane(
        session_name="agent-pbx-operators",
        window_index="0",
        pane_index="2",
        pane_id="%2",
        active=True,
        current_command="codex",
        title="operator-0-fork-caller-1",
        cwd=str(tmp_path),
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
        title="Drifted fork route",
        objective="Route to the fork window despite stale agent metadata.",
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
