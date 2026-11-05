import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator

import pytest

from agent_pbx import mcp_tools
from agent_pbx.config import ServerConfig
from agent_pbx.host_process_evidence import HostProcessAttestor
from agent_pbx.mcp_tools import BearerAuthASGIMiddleware, build_mcp_server
from agent_pbx.schemas import AgentRegisterRequest
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


@contextmanager
def authenticated_controller() -> Iterator[None]:
    token = mcp_tools._authenticated_controller.set(True)
    try:
        yield
    finally:
        mcp_tools._authenticated_controller.reset(token)


def write_fake_process(proc_root: Path, pid: int) -> None:
    process_root = proc_root / str(pid)
    namespace_root = process_root / "ns"
    namespace_root.mkdir(parents=True)
    (process_root / "exe").symlink_to("/usr/bin/keeper")
    (process_root / "cmdline").write_bytes(b"keeper\0--private-body\0")
    (namespace_root / "user").symlink_to("user:[4026531837]")
    (namespace_root / "mnt").symlink_to("mnt:[4026531841]")


def host_process_authority_fixture(
    tmp_path: Path,
    *,
    source_operator_agent_id: str = "operator-0",
) -> tuple[Store, object, dict[str, object]]:
    store = Store(tmp_path / "host-process-pbx.sqlite")
    store.init()
    workspace_digest = "a" * 64
    for request in (
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"operator_role": "root"},
        ),
        AgentRegisterRequest(
            agent_id="operator-6",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"operator_role": "root"},
        ),
        AgentRegisterRequest(
            agent_id="operator-6-fork-caller",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "operator_role": "fork",
                "logical_operator_id": "operator-6",
                "operator_authority_activated_workspace_set_digest": workspace_digest,
            },
        ),
    ):
        store.register_agent(request)
    if source_operator_agent_id != "operator-0":
        store.register_agent(
            AgentRegisterRequest(
                agent_id=source_operator_agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={"operator_role": "root"},
            )
        )

    campaign = store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="Host process evidence",
        objective="Attest one approved daemon-host process",
        criteria=[],
        assignments=[],
    )
    campaign_id = str(campaign["campaign_id"])
    approved_evidence = {
        "candidate_pids": [321],
        "expected_executable": "/usr/bin/keeper",
        "expected_argv0": "keeper",
        "expected_user_namespace": "user:[4026531837]",
        "expected_mount_namespace": "mnt:[4026531841]",
    }
    handoff = store.create_operator_handoff(
        logical_operator_agent_id="operator-0",
        source_operator_agent_id=source_operator_agent_id,
        target_operator_agent_id="operator-6",
        source_agent_id=source_operator_agent_id,
        objective="Approved host process attestation",
        message="Read only the bounded approved proc identity.",
        execution_scope={
            "campaign_id": campaign_id,
            "workspace_set_digest": workspace_digest,
            "action_class": "host_process_identity_attestation",
            "host_process_evidence": approved_evidence,
        },
        metadata={
            "campaign_id": campaign_id,
            "requires_f8_approval": True,
            "approved_by": "agent-pbx-tui",
            "approved_by_operator_agent_id": "operator-0",
        },
    )
    handoff_id = str(handoff["handoff_id"])
    store.update_operator_handoff(
        handoff_id,
        status="running",
        acknowledge=True,
        start=True,
        metadata={
            "campaign_id": campaign_id,
            "requires_f8_approval": True,
            "approved_by": "agent-pbx-tui",
            "approved_by_operator_agent_id": "operator-0",
            "manual_approval": {
                "approved": True,
                "method": "f8",
                "approval_id": "approval-1",
                "approved_at": 1_793_853_600.0,
                "campaign_id": campaign_id,
                "handoff_id": handoff_id,
                "workspace_set_digest": workspace_digest,
            },
        },
    )
    proc_root = tmp_path / "fake-proc"
    write_fake_process(proc_root, 321)
    mcp = build_mcp_server(
        store,
        host_process_attestor=HostProcessAttestor(
            proc_root,
            require_host_proc=False,
            clock=lambda: 1_793_853_601.0,
        ),
    )
    arguments: dict[str, object] = {
        "requesting_agent_id": "operator-6-fork-caller",
        "operator_agent_id": "operator-6",
        "campaign_id": campaign_id,
        "handoff_id": handoff_id,
        "workspace_set_digest": workspace_digest,
        "approval_id": "approval-1",
        **approved_evidence,
    }
    return store, mcp, arguments


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
async def test_mcp_canonicalizes_caller_project_and_report_project(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)
    repo = tmp_path / "1337-b04rd"
    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "board-agent",
            "project": "1337-board",
            "metadata": {"cwd": str(repo)},
        },
    )

    refreshed = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "board-agent",
                "project": str(repo),
                "metadata": {"cwd": str(repo)},
            },
        )
    )
    report = tool_json(
        await mcp.call_tool(
            "pbx_report_turn",
            {
                "agent_id": "board-agent",
                "project": str(repo),
                "summary": "Done",
                "detail": "Canonical project test",
            },
        )
    )

    assert refreshed["project"] == "1337-board"
    assert report["project"] == "1337-board"
    assert report["metadata"]["requested_project"] == str(repo)
    events = store.list_events()
    registration = [
        event
        for event in events
        if event["type"] == "agent_registration_project_canonicalized"
    ]
    report_events = [
        event for event in events if event["type"] == "report_project_canonicalized"
    ]
    assert registration[-1]["payload"]["requested_project"] == str(repo)
    assert registration[-1]["payload"]["project"] == "1337-board"
    assert report_events[-1]["payload"] == {
        "agent_id": "board-agent",
        "requested_project": str(repo),
        "project": "1337-board",
    }


@pytest.mark.asyncio
async def test_mcp_polling_can_be_disabled_without_removing_tool(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store, legacy_polling_enabled=False)
    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "agent-1", "project": "demo"},
    )

    with pytest.raises(Exception, match="Legacy nohup polling is disabled"):
        await mcp.call_tool(
            "pbx_poll_commands", {"agent_id": "agent-1", "wait_seconds": 0}
        )


@pytest.mark.asyncio
async def test_mcp_report_turn_rejects_declared_identity_mismatch(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "operator-0", "project": "agent-pbx-operator", "agent_type": "operator"},
    )
    await mcp.call_tool(
        "pbx_register_agent",
        {"agent_id": "caller-1", "project": "demo"},
    )

    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_report_turn",
            {
                "agent_id": "caller-1",
                "project": "demo",
                "summary": "Wrong identity",
                "detail": "This report is from an operator session.",
                "reporting_agent_id": "operator-0",
            },
        )

    assert store.list_reports("caller-1") == []
    events = [
        event
        for event in store.list_events()
        if event["type"] == "report_identity_violation"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["agent_id"] == "caller-1"
    assert events[0]["payload"]["reporting_agent_id"] == "operator-0"


@pytest.mark.asyncio
async def test_mcp_register_agent_rejects_source_caller_identity_drift(
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-source"
    operator_cwd = tmp_path / "operator-source"
    source_cwd.mkdir()
    operator_cwd.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "cwd": str(operator_cwd),
                "codex_session_id": "operator-session",
                "default_source_caller_agent_id": "caller-1",
            },
        },
    )
    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "cwd": str(source_cwd),
                "codex_session_id": "caller-session",
            },
        },
    )

    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "caller-1",
                "project": "demo",
                "metadata": {
                    "cwd": str(operator_cwd),
                    "codex_session_id": "operator-session",
                },
            },
        )

    caller = store.get_agent("caller-1")
    assert caller is not None
    assert caller["metadata"]["cwd"] == str(source_cwd)
    assert caller["metadata"]["codex_session_id"] == "caller-session"
    events = [
        event
        for event in store.list_events()
        if event["type"] == "agent_registration_identity_violation"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["agent_id"] == "caller-1"
    assert events[0]["payload"]["operator_session_collision"] == {
        "operator_agent_id": "operator-0",
        "metadata_key": "codex_session_id",
        "codex_session_id": "operator-session",
    }


@pytest.mark.asyncio
async def test_mcp_register_agent_allows_source_caller_same_cwd_restart(
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-source"
    operator_cwd = tmp_path / "operator-source"
    source_cwd.mkdir()
    operator_cwd.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "cwd": str(operator_cwd),
                "codex_session_id": "operator-session",
                "default_source_caller_agent_id": "caller-1",
            },
        },
    )
    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "cwd": str(source_cwd),
                "codex_session_id": "caller-session-1",
            },
        },
    )

    restarted = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "caller-1",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "caller-session-2",
                },
            },
        )
    )

    assert restarted["metadata"]["cwd"] == str(source_cwd)
    assert restarted["metadata"]["codex_session_id"] == "caller-session-2"
    assert [
        event
        for event in store.list_events()
        if event["type"] == "agent_registration_identity_violation"
    ] == []


@pytest.mark.asyncio
async def test_mcp_register_agent_allows_source_caller_idempotent_legacy_session(
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-source"
    operator_cwd = tmp_path / "operator-source"
    source_cwd.mkdir()
    operator_cwd.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "cwd": str(operator_cwd),
                "codex_session_id": "shared-legacy-session",
                "default_source_caller_agent_id": "caller-1",
            },
        },
    )
    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "cwd": str(source_cwd),
                "codex_session_id": "shared-legacy-session",
            },
        },
    )

    registered = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "caller-1",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "shared-legacy-session",
                },
            },
        )
    )

    assert registered["metadata"]["cwd"] == str(source_cwd)
    assert registered["metadata"]["codex_session_id"] == "shared-legacy-session"
    assert [
        event
        for event in store.list_events()
        if event["type"] == "agent_registration_identity_violation"
    ] == []


@pytest.mark.asyncio
async def test_mcp_register_root_operator_canonicalizes_project_drift(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store)

    agent = tool_json(
        await mcp.call_tool(
            "pbx_register_agent",
            {
                "agent_id": "operator-0",
                "project": "k1s-workerbee-private",
                "agent_type": "operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "cwd": str(tmp_path / "k1s-workerbee-private"),
                },
            },
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "agent-pbx-operator"
    assert agent["metadata"]["operator_role"] == "root"
    events = store.list_events()
    registered = [event for event in events if event["type"] == "agent_registered"]
    canonicalized = [
        event
        for event in events
        if event["type"] == "agent_registration_project_canonicalized"
    ]
    assert registered[-1]["payload"]["project"] == "agent-pbx-operator"
    assert canonicalized[-1]["payload"] == {
        "agent_id": "operator-0",
        "requested_project": "k1s-workerbee-private",
        "project": "agent-pbx-operator",
        "agent_type": "operator",
        "pbx_active": True,
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


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_requires_authenticated_controller(
    tmp_path: Path,
) -> None:
    _, mcp, arguments = host_process_authority_fixture(tmp_path)

    with pytest.raises(Exception, match="authenticated controller required"):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_returns_bounded_approved_evidence(
    tmp_path: Path,
) -> None:
    store, mcp, arguments = host_process_authority_fixture(tmp_path)

    with authenticated_controller():
        evidence = tool_json(
            await mcp.call_tool(
                "pbx_attest_host_process_identity", {"request": arguments}
            )
        )

    assert evidence == {
        "match_count": 1,
        "unique_host_pid": 321,
        "executable_equal": True,
        "argv0_equal": True,
        "user_namespace_equal": True,
        "mount_namespace_equal": True,
        "timestamp": 1_793_853_601.0,
        "fail_closed_reason": None,
    }
    events = [
        event
        for event in store.list_events()
        if event["type"] == "host_process_identity_attested"
    ]
    assert len(events) == 1
    event_text = json.dumps(events[0], sort_keys=True)
    assert "private-body" not in event_text
    assert "/usr/bin/keeper" not in event_text
    assert '"keeper"' not in event_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changed_argument", "changed_value", "reason"),
    [
        ("workspace_set_digest", "b" * 64, "workspace_set_not_activated"),
        ("approval_id", "wrong-approval", "manual_approval_invalid"),
        ("campaign_id", "wrong-campaign", "campaign_not_running"),
        ("handoff_id", "wrong-handoff", "handoff_not_approved"),
        ("expected_argv0", "unapproved", "process_predicates_not_approved"),
        ("candidate_pids", [321, 322], "process_predicates_not_approved"),
    ],
)
async def test_mcp_host_process_attestation_rejects_scope_or_approval_drift(
    tmp_path: Path,
    changed_argument: str,
    changed_value: object,
    reason: str,
) -> None:
    store, mcp, arguments = host_process_authority_fixture(tmp_path)
    arguments[changed_argument] = changed_value

    with authenticated_controller(), pytest.raises(Exception, match=reason):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})

    assert not any(
        event["type"] == "host_process_identity_attested"
        for event in store.list_events()
    )


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_rejects_inactive_campaign(
    tmp_path: Path,
) -> None:
    store, mcp, arguments = host_process_authority_fixture(tmp_path)
    store.update_operator_campaign(
        str(arguments["campaign_id"]), status="blocked", complete=True
    )

    with authenticated_controller(), pytest.raises(
        Exception, match="campaign_not_running"
    ):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_rejects_missing_manual_approval(
    tmp_path: Path,
) -> None:
    store, mcp, arguments = host_process_authority_fixture(tmp_path)
    store.update_operator_handoff(
        str(arguments["handoff_id"]), metadata={"manual_approval": {}}
    )

    with authenticated_controller(), pytest.raises(
        Exception, match="manual_approval_invalid"
    ):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_preserves_delegation_boundary(
    tmp_path: Path,
) -> None:
    _, mcp, arguments = host_process_authority_fixture(
        tmp_path, source_operator_agent_id="operator-9"
    )

    with authenticated_controller(), pytest.raises(
        Exception, match="handoff_delegation_mismatch"
    ):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_rejects_unbounded_identifiers(
    tmp_path: Path,
) -> None:
    _, mcp, arguments = host_process_authority_fixture(tmp_path)
    arguments["approval_id"] = "x" * 129

    with authenticated_controller(), pytest.raises(
        Exception, match="invalid_authority_identifier"
    ):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_mcp_host_process_attestation_has_no_process_action_surface(
    tmp_path: Path,
) -> None:
    _, mcp, arguments = host_process_authority_fixture(tmp_path)
    arguments["signal"] = 9

    with authenticated_controller(), pytest.raises(Exception):
        await mcp.call_tool("pbx_attest_host_process_identity", {"request": arguments})


@pytest.mark.asyncio
async def test_bearer_middleware_marks_only_authenticated_controller_context(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "auth.sqlite")
    store.init()
    observed: list[bool] = []

    async def downstream(scope: object, receive: object, send: object) -> None:
        del scope, receive, send
        observed.append(mcp_tools._authenticated_controller.get())

    middleware = BearerAuthASGIMiddleware(
        downstream,  # type: ignore[arg-type]
        store,
        ServerConfig(token="controller-secret"),
    )

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        del message

    await middleware(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer controller-secret")],
        },  # type: ignore[arg-type]
        receive,  # type: ignore[arg-type]
        send,  # type: ignore[arg-type]
    )

    assert observed == [True]
    assert mcp_tools._authenticated_controller.get() is False
