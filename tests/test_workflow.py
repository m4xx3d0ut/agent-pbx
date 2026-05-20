from pathlib import Path

from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig


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
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    acked = client.post(
        f"/v1/commands/{command.json()['command_id']}/ack",
        json={"result": {"ok": True}},
    )
    events = client.get("/v1/events")

    assert registered.status_code == 200
    assert report.status_code == 200
    assert fetched_report.json()["detail"] == "Full details are persisted at report time."
    assert latest_reports.json()[0]["report_id"] == report.json()["report_id"]
    assert command.status_code == 200
    assert polled.json()[0]["status"] == "delivered"
    assert acked.json()["status"] == "acked"
    assert [event["type"] for event in events.json()] == [
        "agent_registered",
        "report_created",
        "command_queued",
        "command_delivered",
        "command_acked",
    ]
