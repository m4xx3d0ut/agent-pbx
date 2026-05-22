import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.store import STALE_WORKING_SECONDS


def test_report_command_event_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    registered = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "demo",
            "name": "Agent One",
            "metadata": {"workspace": "/tmp/demo"},
        },
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "needs_input",
            "summary": "Need a decision",
            "detail": "Full details are persisted at report time.",
            "needs_input": True,
            "plan_options": ["Proceed", "Stop"],
        },
    )
    fetched_report = client.get(f"/v1/reports/{report.json()['report_id']}")
    latest_reports = client.get("/v1/agents/agent-1/reports?limit=1")
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Proceed"},
        },
    )
    agents_before_poll = client.get("/v1/agents")
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    agents_after_poll = client.get("/v1/agents")
    acked = client.post(
        f"/v1/commands/{command.json()['command_id']}/ack",
        json={"result": {"ok": True}},
    )
    events = client.get("/v1/events")

    assert registered.status_code == 200
    assert registered.json()["pbx_active"] is True
    assert report.status_code == 200
    assert fetched_report.json()["detail"] == "Full details are persisted at report time."
    assert latest_reports.json()[0]["report_id"] == report.json()["report_id"]
    assert command.status_code == 200
    assert agents_before_poll.json()[0]["queued_command_count"] == 1
    assert agents_before_poll.json()[0]["last_poll_at"] is None
    assert agents_before_poll.json()[0]["latest_report_id"] == report.json()["report_id"]
    assert agents_before_poll.json()[0]["latest_report_status"] == "needs_input"
    assert agents_before_poll.json()[0]["latest_report_needs_input"] is True
    assert agents_before_poll.json()[0]["latest_report_plan_option_count"] == 2
    assert agents_before_poll.json()[0]["latest_report_action_required"] is True
    assert agents_before_poll.json()[0]["polls_per_hour"] == 0
    assert polled.json()[0]["status"] == "delivered"
    assert agents_after_poll.json()[0]["queued_command_count"] == 0
    assert agents_after_poll.json()[0]["last_poll_at"] is not None
    assert agents_after_poll.json()[0]["polls_per_hour"] == 1
    assert agents_after_poll.json()[0]["reports_per_hour"] == 1
    assert agents_after_poll.json()[0]["estimated_visible_tokens_per_hour"] > 0
    assert acked.json()["status"] == "acked"
    assert [event["type"] for event in events.json()] == [
        "agent_registered",
        "report_created",
        "command_queued",
        "command_delivered",
        "command_acked",
    ]


def test_agent_thread_merges_reports_and_commands(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "waiting",
            "summary": "Need review",
            "detail": "Full report detail",
            "needs_input": True,
            "plan_options": ["Approve"],
        },
    ).json()
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "ping",
            "payload": {"request": "Please pong"},
        },
    ).json()
    client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"result": {"ok": True}},
    )

    response = client.get("/v1/agents/agent-1/thread")
    missing = client.get("/v1/agents/missing/thread")

    assert response.status_code == 200
    assert missing.status_code == 404
    thread = response.json()
    assert [item["kind"] for item in thread] == ["report", "command"]
    assert thread[0]["item_id"] == f"report:{report['report_id']}"
    assert thread[0]["body"] == "Full report detail"
    assert thread[0]["metadata"]["plan_options"] == ["Approve"]
    assert thread[1]["item_id"] == f"command:{command['command_id']}"
    assert thread[1]["title"] == "Ping"
    assert thread[1]["body"] == "Please pong"
    assert thread[1]["metadata"]["result"] == {"ok": True}


def test_working_agent_reports_effective_stale_status(tmp_path: Path) -> None:
    db_path = tmp_path / "pbx.sqlite"
    client = TestClient(create_app(ServerConfig(db_path=db_path)))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "working",
            "summary": "Still working",
            "detail": "The last known report said work was in progress.",
        },
    )
    stale_seen_at = time.time() - STALE_WORKING_SECONDS - 5
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = ?",
            (stale_seen_at, "agent-1"),
        )

    agent = client.get("/v1/agents").json()[0]

    assert agent["status"] == "working"
    assert agent["effective_status"] == "stale-working"
    assert agent["status_stale"] is True
    assert agent["status_age_seconds"] >= STALE_WORKING_SECONDS


def test_delete_queued_command_removes_it_from_thread_and_poll(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Never mind"},
        },
    ).json()

    deleted = client.delete(f"/v1/commands/{command['command_id']}")
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    thread = client.get("/v1/agents/agent-1/thread")
    agents = client.get("/v1/agents")
    events = client.get("/v1/events")

    assert deleted.status_code == 200
    assert deleted.json()["command_id"] == command["command_id"]
    assert deleted.json()["status"] == "queued"
    assert polled.json() == []
    assert thread.json() == []
    assert agents.json()[0]["queued_command_count"] == 0
    assert [event["type"] for event in events.json()] == [
        "agent_registered",
        "command_queued",
        "command_deleted",
    ]


def test_delete_delivered_command_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Proceed"},
        },
    ).json()
    client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    deleted = client.delete(f"/v1/commands/{command['command_id']}")

    assert deleted.status_code == 409
    assert deleted.json()["detail"] == "command is not queued"


def test_dismiss_agent_hides_from_list_but_keeps_thread_until_reconnect(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "done",
            "summary": "Finished",
            "detail": "Work complete.",
        },
    ).json()
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Follow up later"},
        },
    ).json()

    dismissed = client.delete("/v1/agents/agent-1")
    agents_after_dismiss = client.get("/v1/agents").json()
    thread_after_dismiss = client.get("/v1/agents/agent-1/thread").json()
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    agents_after_reconnect = client.get("/v1/agents").json()
    events = client.get("/v1/events").json()

    assert dismissed.status_code == 200
    assert dismissed.json()["agent_id"] == "agent-1"
    assert agents_after_dismiss == []
    assert {item["item_id"] for item in thread_after_dismiss} == {
        f"report:{report['report_id']}",
        f"command:{command['command_id']}",
    }
    assert agents_after_reconnect[0]["agent_id"] == "agent-1"
    assert agents_after_reconnect[0]["latest_report_id"] == report["report_id"]
    assert [event["type"] for event in events] == [
        "agent_registered",
        "report_created",
        "command_queued",
        "agent_dismissed",
        "agent_registered",
    ]


def test_dismiss_agent_with_delete_thread_purges_agent_history(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "done",
            "summary": "Finished",
            "detail": "Work complete.",
        },
    )
    client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Discard this"},
        },
    )
    client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    dismissed = client.delete("/v1/agents/agent-1?delete_thread=true")
    thread_after_dismiss = client.get("/v1/agents/agent-1/thread").json()
    polled_after_dismiss = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    agents_after_reconnect = client.get("/v1/agents").json()

    assert dismissed.status_code == 200
    assert thread_after_dismiss == []
    assert polled_after_dismiss.json() == []
    assert agents_after_reconnect[0]["latest_report_id"] is None
    assert agents_after_reconnect[0]["queued_command_count"] == 0


def test_agent_workerbee_status_endpoint(tmp_path: Path, monkeypatch) -> None:
    def fake_status_for_agent(self, agent):  # noqa: ANN001, ARG001
        return {
            "configured": True,
            "available": True,
            "agent_id": agent["agent_id"],
            "cwd": agent["metadata"]["cwd"],
            "workerbee_bin": "/tmp/workerbee",
            "checked_at": 123.0,
            "project": "demo-dev-123",
            "mode": "lazy",
            "running": False,
            "status_kind": "stopped",
            "dashboard_url": None,
            "state_dir": "/tmp/state",
            "description": "no app workload deployed yet",
            "error": None,
            "project_status": {"running": False},
            "project_card": {"status_kind": "stopped"},
            "global_dashboard": {"running": True},
            "app_status": {"state": "no_workload_deployed"},
            "latest_deployment": None,
        }

    monkeypatch.setattr(
        "agent_pbx.workerbee.WorkerBeeStatusService.status_for_agent",
        fake_status_for_agent,
    )
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "demo",
            "metadata": {"cwd": str(tmp_path)},
        },
    )

    response = client.get("/v1/agents/agent-1/workerbee")
    missing = client.get("/v1/agents/missing/workerbee")

    assert response.status_code == 200
    assert response.json()["project"] == "demo-dev-123"
    assert response.json()["description"] == "no app workload deployed yet"
    assert missing.status_code == 404
