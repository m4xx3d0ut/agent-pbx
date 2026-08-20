import base64
import json
import sqlite3
import struct
import time
from pathlib import Path
import zlib

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.store import STALE_WORKING_SECONDS


def tiny_png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + b"\0\0\0\0"

    raw = bytes(
        [
            0,
            255,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            0,
            255,
            255,
            255,
            255,
        ]
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


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
        json={"agent_id": "agent-1", "result": {"ok": True}},
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
    assert events.json()[1]["payload"]["created_at"] == report.json()["created_at"]


def test_report_endpoint_rejects_declared_identity_mismatch(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
        },
    )
    client.post(
        "/v1/agents/register",
        json={"agent_id": "caller-1", "project": "demo"},
    )

    report = client.post(
        "/v1/agents/caller-1/reports",
        json={
            "project": "demo",
            "summary": "Wrong identity",
            "detail": "This report is from an operator session.",
            "reporting_agent_id": "operator-0",
        },
    )
    reports = client.get("/v1/agents/caller-1/reports")
    events = client.get("/v1/events")

    assert report.status_code == 409
    assert reports.json() == []
    violations = [
        event
        for event in events.json()
        if event["type"] == "report_identity_violation"
    ]
    assert len(violations) == 1
    assert violations[0]["payload"]["agent_id"] == "caller-1"
    assert violations[0]["payload"]["reporting_agent_id"] == "operator-0"


def test_register_agent_infers_codex_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    session_id = "019ed6ed-6e25-7d82-bf2f-0b3c377bd3c9"
    cwd.mkdir()
    write_codex_session(codex_home, cwd, session_id)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    registered = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "codex-k1s-workerbee-private-20260617",
            "project": "k1s-workerbee-private",
            "metadata": {"cwd": str(cwd), "pbx_mode": "report"},
        },
    )

    assert registered.status_code == 200
    assert registered.json()["metadata"]["codex_session_id"] == session_id
    assert registered.json()["metadata"]["codex_thread_id"] == session_id


def test_register_operator_does_not_infer_codex_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    session_id = "019ed6ed-6e25-7d82-bf2f-0b3c377bd3c9"
    cwd.mkdir()
    write_codex_session(codex_home, cwd, session_id)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    registered = client.post(
        "/v1/agents/register",
        json={
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

    assert registered.status_code == 200
    assert "codex_session_id" not in registered.json()["metadata"]
    assert registered.json()["metadata"]["source_codex_session_id"] == session_id


def test_set_agent_pbx_active_endpoint_updates_agent_and_events(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    registered = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
        },
    )
    updated = client.put("/v1/agents/operator-0/pbx-active", json={"active": False})
    missing = client.put("/v1/agents/missing/pbx-active", json={"active": False})
    events = client.get("/v1/events")

    assert registered.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["pbx_active"] is False
    assert missing.status_code == 404
    assert [event["type"] for event in events.json()] == [
        "agent_registered",
        "agent_pbx_active_changed",
    ]
    assert events.json()[-1]["payload"]["pbx_active"] is False


def test_mark_latest_report_seen_is_shared_state(tmp_path: Path) -> None:
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
            "summary": "Done",
            "detail": "Details",
        },
    ).json()
    before = client.get("/v1/agents").json()[0]

    seen = client.post("/v1/agents/agent-1/latest/seen")
    after = client.get("/v1/agents").json()[0]
    events = client.get("/v1/events").json()

    assert before["latest_report_id"] == report["report_id"]
    assert before["latest_report_created_at"] == report["created_at"]
    assert before["latest_report_seen_at"] is None
    assert seen.status_code == 200
    assert seen.json()["latest_report_seen_at"] == report["created_at"]
    assert after["latest_report_seen_at"] == report["created_at"]
    assert events[-1]["type"] == "latest_seen"
    assert events[-1]["payload"]["agent_id"] == "agent-1"
    assert events[-1]["payload"]["latest_report_seen_at"] == report["created_at"]


def test_report_metadata_can_suppress_tui_alerts(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "working",
            "summary": "Synthetic validation report",
            "detail": "Should update state without a TUI alert.",
            "metadata": {"suppress_tui_alerts": True},
        },
    )
    agents = client.get("/v1/agents").json()
    events = client.get("/v1/events").json()

    assert report.status_code == 200
    assert agents[0]["latest_report_suppress_tui_alerts"] is True
    assert events[-1]["type"] == "report_created"
    assert events[-1]["payload"]["suppress_tui_alerts"] is True


def test_audited_working_report_clears_stale_blocked_status(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {"cwd": str(tmp_path)},
        },
    )
    blocked = client.post(
        "/v1/agents/caller-1/reports",
        json={
            "project": "demo",
            "status": "blocked",
            "summary": "Fork unavailable",
            "detail": "Diagnostic report posted before fork was available.",
        },
    ).json()
    unblocked = client.post(
        "/v1/agents/caller-1/reports",
        json={
            "project": "demo",
            "status": "working",
            "summary": "Caller unblocked; active operator fork is ready.",
            "detail": "Audited unblock after fork became ready.",
            "metadata": {
                "manual_unblock": True,
                "resolved_report_id": blocked["report_id"],
            },
        },
    ).json()

    agent = client.get("/v1/agents").json()[0]
    report = client.get(f"/v1/reports/{unblocked['report_id']}").json()

    assert agent["status"] == "working"
    assert agent["latest_report_id"] == unblocked["report_id"]
    assert agent["latest_report_status"] == "working"
    assert report["metadata"]["manual_unblock"] is True
    assert report["metadata"]["resolved_report_id"] == blocked["report_id"]


def test_star_agent_is_shared_state(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    before = client.get("/v1/agents").json()[0]
    starred = client.post("/v1/agents/agent-1/star")
    after_star = client.get("/v1/agents").json()[0]
    unstarred = client.delete("/v1/agents/agent-1/star")
    after_unstar = client.get("/v1/agents").json()[0]
    events = client.get("/v1/events").json()

    assert before["starred"] is False
    assert before["starred_at"] is None
    assert starred.status_code == 200
    assert starred.json()["starred"] is True
    assert starred.json()["starred_at"] is not None
    assert after_star["starred"] is True
    assert after_star["starred_at"] == starred.json()["starred_at"]
    assert unstarred.status_code == 200
    assert unstarred.json()["starred"] is False
    assert unstarred.json()["starred_at"] is None
    assert after_unstar["starred"] is False
    assert after_unstar["starred_at"] is None
    assert [event["type"] for event in events[-2:]] == [
        "agent_starred_changed",
        "agent_starred_changed",
    ]
    assert events[-2]["payload"]["starred"] is True
    assert events[-1]["payload"]["starred"] is False


def test_events_tail_returns_latest_events(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))

    for index in range(105):
        client.post(
            "/v1/agents/register",
            json={"agent_id": f"agent-{index:03d}", "project": "demo"},
        )

    first_page = client.get("/v1/events", params={"limit": 5}).json()
    latest_page = client.get(
        "/v1/events",
        params={"tail": "true", "limit": 5},
    ).json()

    assert [event["subject_id"] for event in first_page] == [
        "agent-000",
        "agent-001",
        "agent-002",
        "agent-003",
        "agent-004",
    ]
    assert [event["subject_id"] for event in latest_page] == [
        "agent-100",
        "agent-101",
        "agent-102",
        "agent-103",
        "agent-104",
    ]
    assert [event["event_id"] for event in latest_page] == sorted(
        event["event_id"] for event in latest_page
    )


def test_schema_upgrade_backfills_existing_latest_reports_as_seen(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "pbx.sqlite"
    client = TestClient(create_app(ServerConfig(db_path=db_path)))

    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "done",
            "summary": "Historical report",
            "detail": "Existing before shared seen migration",
        },
    ).json()
    before = client.get("/v1/agents").json()[0]
    assert before["latest_report_seen_at"] is None

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE metadata SET value = '6' WHERE key = 'schema_version'"
        )

    upgraded = TestClient(create_app(ServerConfig(db_path=db_path)))
    after = upgraded.get("/v1/agents").json()[0]
    assert after["latest_report_seen_at"] == report["created_at"]

    new_report = upgraded.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "working",
            "summary": "New report",
            "detail": "Should still be unseen after migration",
        },
    ).json()
    current = upgraded.get("/v1/agents").json()[0]
    assert current["latest_report_created_at"] == new_report["created_at"]
    assert current["latest_report_seen_at"] == report["created_at"]


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
        json={"agent_id": "agent-1", "result": {"ok": True}},
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


def test_command_lifecycle_rejects_unknown_agents_and_invalid_acks(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-2", "project": "demo"},
    )
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Proceed"},
        },
    ).json()

    unknown_target = client.post(
        "/v1/commands",
        json={
            "agent_id": "missing",
            "type": "send_input",
            "payload": {"message": "Nope"},
        },
    )
    unknown_poll = client.get("/v1/agents/missing/commands?wait_seconds=0")
    early_ack = client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"agent_id": "agent-1", "result": {"ok": True}},
    )
    client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    wrong_agent_ack = client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"agent_id": "agent-2", "result": {"ok": True}},
    )
    valid_ack = client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"agent_id": "agent-1", "result": {"ok": True}},
    )
    repeat_ack = client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"agent_id": "agent-1", "result": {"ok": True}},
    )

    assert unknown_target.status_code == 404
    assert unknown_poll.status_code == 404
    assert early_ack.status_code == 409
    assert early_ack.json()["detail"] == "command is queued, not delivered"
    assert wrong_agent_ack.status_code == 409
    assert wrong_agent_ack.json()["detail"] == "command is not owned by agent"
    assert valid_ack.status_code == 200
    assert valid_ack.json()["status"] == "acked"
    assert repeat_ack.status_code == 409
    assert repeat_ack.json()["detail"] == "command is acked, not delivered"


def test_broadcast_command_is_claimed_by_first_polling_agent(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    for agent_id in ("agent-1", "agent-2"):
        client.post(
            "/v1/agents/register",
            json={"agent_id": agent_id, "project": "demo"},
        )
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": None,
            "type": "send_input",
            "payload": {"message": "Broadcast"},
        },
    ).json()

    first_poll = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    second_poll = client.get("/v1/agents/agent-2/commands?wait_seconds=0")
    thread_1 = client.get("/v1/agents/agent-1/thread").json()
    thread_2 = client.get("/v1/agents/agent-2/thread").json()
    ack = client.post(
        f"/v1/commands/{command['command_id']}/ack",
        json={"agent_id": "agent-1", "result": {"ok": True}},
    )

    assert first_poll.status_code == 200
    assert first_poll.json()[0]["agent_id"] == "agent-1"
    assert second_poll.json() == []
    assert [item["item_id"] for item in thread_1] == [
        f"command:{command['command_id']}"
    ]
    assert thread_2 == []
    assert ack.status_code == 200


def test_structured_plan_options_round_trip(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )

    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "needs_input",
            "summary": "Choose",
            "detail": "Pick one.",
            "needs_input": True,
            "plan_options": [
                "Legacy",
                {
                    "id": "incremental",
                    "label": "Incremental hardening",
                    "description": "Fix lifecycle first.",
                },
            ],
        },
    )
    latest = client.get("/v1/agents/agent-1/reports?limit=1")
    thread = client.get("/v1/agents/agent-1/thread")

    assert report.status_code == 200
    assert latest.json()[0]["plan_options"][1] == {
        "id": "incremental",
        "label": "Incremental hardening",
        "description": "Fix lifecycle first.",
    }
    assert thread.json()[0]["metadata"]["plan_options"][0] == "Legacy"
    assert thread.json()[0]["metadata"]["plan_options"][1]["id"] == "incremental"


def test_send_key_command_round_trip(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )

    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_key",
            "payload": {"key": "escape", "request": "Send Escape"},
        },
    )
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")
    thread = client.get("/v1/agents/agent-1/thread")

    assert command.status_code == 200
    assert command.json()["type"] == "send_key"
    assert polled.json()[0]["payload"]["key"] == "escape"
    assert thread.json()[0]["title"] == "Send key: escape"


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


def test_hidden_agents_can_be_listed_and_unhidden(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )
    client.delete("/v1/agents/agent-1")

    visible = client.get("/v1/agents").json()
    hidden = client.get("/v1/agents?include_hidden=true").json()
    restored = client.put("/v1/agents/agent-1/unhide")
    visible_after_restore = client.get("/v1/agents").json()
    events = client.get("/v1/events").json()

    assert visible == []
    assert hidden[0]["agent_id"] == "agent-1"
    assert hidden[0]["dismissed_at"] is not None
    assert restored.status_code == 200
    assert restored.json()["dismissed_at"] is None
    assert visible_after_restore[0]["agent_id"] == "agent-1"
    assert [event["type"] for event in events] == [
        "agent_registered",
        "agent_dismissed",
        "agent_unhidden",
    ]


def test_agent_prune_preview_apply_and_undo_respects_guards(tmp_path: Path) -> None:
    db_path = tmp_path / "pbx.sqlite"
    client = TestClient(create_app(ServerConfig(db_path=db_path)))
    old = time.time() - (45 * 86_400)
    for agent_id, status in {
        "old-done": "done",
        "old-starred": "done",
        "old-queued": "done",
        "old-linked": "done",
        "old-stale": "working",
        "recent-done": "done",
    }.items():
        client.post(
            "/v1/agents/register",
            json={"agent_id": agent_id, "project": "demo"},
        )
        client.post(
            f"/v1/agents/{agent_id}/reports",
            json={
                "project": "demo",
                "status": status,
                "summary": status,
                "detail": status,
            },
        )
    client.post(
        "/v1/agents/register",
        json={"agent_id": "operator-0", "project": "ops", "agent_type": "operator"},
    )
    client.post("/v1/agents/old-starred/star")
    client.post(
        "/v1/commands",
        json={
            "agent_id": "old-queued",
            "type": "send_input",
            "payload": {"message": "still pending"},
        },
    )
    client.post(
        "/v1/operator/knowledge-links",
        json={
            "operator_agent_id": "operator-0",
            "source_agent_id": "old-linked",
            "target_agent_id": "operator-0",
            "link_type": "domain_context",
            "summary": "active link protects source",
        },
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id != 'recent-done'",
            (old,),
        )
        conn.execute(
            "UPDATE agents SET last_seen_at = ? WHERE agent_id = 'recent-done'",
            (time.time(),),
        )

    preview = client.post(
        "/v1/agents/prune/preview",
        json={"preset": "terminal-callers", "min_age_days": 30},
    )
    applied = client.post(
        "/v1/agents/prune/apply",
        json={"preset": "terminal-callers", "min_age_days": 30},
    )
    visible_after_apply = {
        agent["agent_id"] for agent in client.get("/v1/agents").json()
    }
    batch = applied.json()["batch"]
    undo = client.post(f"/v1/agents/prune/batches/{batch['batch_id']}/undo")
    visible_after_undo = {
        agent["agent_id"] for agent in client.get("/v1/agents").json()
    }

    assert preview.status_code == 200
    assert {item["agent_id"] for item in preview.json()["candidates"]} == {"old-done"}
    skipped = {
        item["agent_id"]: item["guard_reasons"]
        for item in preview.json()["skipped"]
    }
    assert "starred" in skipped["old-starred"]
    assert "queued_commands" in skipped["old-queued"]
    assert "active_knowledge_link" in skipped["old-linked"]
    assert applied.status_code == 200
    assert batch["hidden_count"] == 1
    assert "old-done" not in visible_after_apply
    assert "old-starred" in visible_after_apply
    assert undo.status_code == 200
    assert undo.json()["undone_at"] is not None
    assert "old-done" in visible_after_undo


def test_agent_prune_operator_forks_requires_no_tmux_pane(tmp_path: Path) -> None:
    db_path = tmp_path / "pbx.sqlite"
    client = TestClient(create_app(ServerConfig(db_path=db_path)))
    old = time.time() - (45 * 86_400)
    for agent_id, metadata in {
        "fork-no-pane": {"operator_role": "fork", "logical_operator_id": "operator-0"},
        "fork-with-pane": {
            "operator_role": "fork",
            "logical_operator_id": "operator-0",
            "tmux_pane_id": "%1",
        },
    }.items():
        client.post(
            "/v1/agents/register",
            json={
                "agent_id": agent_id,
                "project": "demo",
                "agent_type": "operator",
                "metadata": metadata,
            },
        )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agents SET status = 'completed', last_seen_at = ?",
            (old,),
        )

    preview = client.post(
        "/v1/agents/prune/preview",
        json={"preset": "operator-forks", "min_age_days": 30},
    )

    assert preview.status_code == 200
    assert {item["agent_id"] for item in preview.json()["candidates"]} == {
        "fork-no-pane"
    }
    skipped = {
        item["agent_id"]: item["guard_reasons"]
        for item in preview.json()["skipped"]
    }
    assert "tmux_pane_recorded" in skipped["fork-with-pane"]


def test_dismissed_agent_reappears_on_report_or_poll(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    for agent_id in ("report-agent", "poll-agent"):
        client.post(
            "/v1/agents/register",
            json={"agent_id": agent_id, "project": "demo"},
        )
        client.delete(f"/v1/agents/{agent_id}")

    assert client.get("/v1/agents").json() == []

    report = client.post(
        "/v1/agents/report-agent/reports",
        json={
            "project": "demo",
            "status": "running",
            "summary": "Back on PBX",
            "detail": "A dismissed agent that reports should reappear.",
        },
    )
    polled = client.get("/v1/agents/poll-agent/commands?wait_seconds=0")
    visible = {agent["agent_id"] for agent in client.get("/v1/agents").json()}

    assert report.status_code == 200
    assert polled.status_code == 200
    assert visible == {"report-agent", "poll-agent"}


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


def test_agent_files_list_and_preview_are_scoped_to_agent_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hello from repo\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "artifact.bin").write_text("ignored\n", encoding="utf-8")
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "capture.txt").write_text("ignored\n", encoding="utf-8")
    (repo / "coverage.xml").write_text("<coverage />\n", encoding="utf-8")
    src = repo / "src"
    src.mkdir()
    (src / "app.py").write_text("print('ok')\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (repo / "outside-link").symlink_to(outside)
    (repo / "pixel.gif").write_bytes(
        base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
    )
    (repo / "pixel.png").write_bytes(tiny_png_bytes())

    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "demo",
            "metadata": {"cwd": str(repo)},
        },
    )

    listing = client.get("/v1/agents/agent-1/files")
    text_preview = client.get(
        "/v1/agents/agent-1/files/preview",
        params={"path": "README.md"},
    )
    nested_listing = client.get(
        "/v1/agents/agent-1/files",
        params={"path": "src"},
    )
    gif_preview = client.get(
        "/v1/agents/agent-1/files/preview",
        params={"path": "pixel.gif"},
    )
    png_preview = client.get(
        "/v1/agents/agent-1/files/preview",
        params={"path": "pixel.png"},
    )
    traversal = client.get(
        "/v1/agents/agent-1/files/preview",
        params={"path": "../outside.txt"},
    )

    assert listing.status_code == 200
    entries = {entry["name"]: entry for entry in listing.json()["entries"]}
    assert ".git" not in entries
    assert "build" not in entries
    assert "artifacts" not in entries
    assert "coverage.xml" not in entries
    assert "outside-link" not in entries
    assert entries["src"]["kind"] == "directory"
    assert entries["README.md"]["is_text"] is True
    assert entries["pixel.gif"]["is_image"] is True
    assert entries["pixel.gif"]["is_gif"] is True
    assert entries["pixel.png"]["is_image"] is True
    assert text_preview.json()["text"] == "hello from repo\n"
    assert text_preview.json()["truncated"] is False
    assert nested_listing.json()["parent"] == "."
    assert nested_listing.json()["entries"][0]["name"] == "app.py"
    assert gif_preview.json()["is_image"] is True
    assert gif_preview.json()["is_gif"] is True
    assert gif_preview.json()["image_width"] == 1
    assert gif_preview.json()["image_height"] == 1
    assert gif_preview.json()["text"] is None
    assert gif_preview.json()["image_preview"]
    assert gif_preview.json()["image_preview_ansi"]
    assert gif_preview.json()["image_preview_format"] == "ansi-truecolor-halfblocks"
    assert png_preview.json()["is_image"] is True
    assert png_preview.json()["is_gif"] is False
    assert png_preview.json()["image_width"] == 2
    assert png_preview.json()["image_height"] == 2
    assert png_preview.json()["image_preview"]
    assert png_preview.json()["image_preview_ansi"]
    assert png_preview.json()["image_preview_format"] == "ansi-truecolor-halfblocks"
    assert traversal.status_code == 200
    assert traversal.json()["error"]["code"] == "PATH_OUTSIDE_CWD"


def test_operator_project_spawn_api_request_list_and_status(tmp_path: Path) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
    )
    client.post(
        "/v1/agents/register",
        json={
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
    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
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
    ).json()

    created = client.post(
        "/v1/operator/project-spawns",
        json={
            "operator_agent_id": "operator-0",
            "review_fork_id": fork["operator_fork_id"],
            "project_name": "Next Demo",
            "instructions": "Build the sibling project.",
            "mode": "empty",
        },
    )

    assert created.status_code == 200
    spawn = created.json()
    listed = client.get(
        "/v1/operator/project-spawns",
        params={"operator_agent_id": "operator-0", "status": "pending"},
    )
    assert listed.status_code == 200
    assert listed.json()["project_spawns"][0]["spawn_request_id"] == spawn["spawn_request_id"]
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "codex-Next-Demo",
            "project": "Next-Demo",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path / "Next-Demo")},
        },
    )
    updated = client.post(
        f"/v1/operator/project-spawns/{spawn['spawn_request_id']}/status",
        json={
            "status": "launched",
            "launched_agent_id": "codex-Next-Demo",
            "tmux_pane_id": "%42",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "launched"
    assert updated.json()["launched_agent_id"] == "codex-Next-Demo"


def test_operator_knowledge_link_api_propose_approve_and_close(
    tmp_path: Path,
) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
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
        client.post("/v1/agents/register", json=payload)
    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
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
    ).json()

    proposed = client.post(
        "/v1/operator/knowledge-links/proposals",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "source_agent_id": fork["fork_agent_id"],
            "target_agent_id": "operator-B",
            "source_operator_fork_id": fork["operator_fork_id"],
            "message": "Share enough domain context for operator-B to continue.",
            "summary": "Domain handoff",
        },
    )
    assert proposed.status_code == 200
    proposal = proposed.json()
    link = proposal["link"]
    turn = proposal["turn"]

    listed = client.get(
        "/v1/operator/knowledge-links",
        params={"operator_agent_id": "operator-0", "status": "proposed"},
    )
    context = client.get(
        f"/v1/operator/knowledge-links/{link['link_id']}/context",
        params={"operator_agent_id": "operator-0"},
    )
    delivered = client.post(
        f"/v1/operator/knowledge-links/{link['link_id']}/turns/{turn['turn_id']}/approve",
        json={"operator_agent_id": "operator-0", "delivery": "queue"},
    )

    assert listed.status_code == 200
    assert listed.json()["knowledge_links"][0]["link_id"] == link["link_id"]
    assert context.status_code == 200
    assert context.json()["turns"][0]["delivery_status"] == "pending_approval"
    assert delivered.status_code == 200
    delivered_payload = delivered.json()
    assert delivered_payload["link"]["status"] == "active"
    assert delivered_payload["turn"]["delivery_status"] == "queued"
    assert delivered_payload["command"]["agent_id"] == "operator-B"
    assert (
        delivered_payload["command"]["payload"]["source"]
        == "operator_knowledge_turn"
    )
    forks = client.get(
        "/v1/operator/forks",
        params={"operator_agent_id": "operator-0"},
    ).json()["forks"]
    assert [item["operator_fork_id"] for item in forks] == [fork["operator_fork_id"]]

    closed = client.post(
        f"/v1/operator/knowledge-links/{link['link_id']}/close",
        json={
            "operator_agent_id": "operator-0",
            "summary": "Knowledge share complete",
        },
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_operator_kb_api_propose_promote_export_and_import(
    tmp_path: Path,
) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
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
        client.post("/v1/agents/register", json=payload)
    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
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
    ).json()
    proposal = client.post(
        "/v1/operator/knowledge-links/proposals",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "source_agent_id": fork["fork_agent_id"],
            "target_agent_id": "operator-B",
            "source_operator_fork_id": fork["operator_fork_id"],
            "message": "Routing context: keep review forks read only.",
            "summary": "Routing handoff",
        },
    ).json()

    proposed = client.post(
        "/v1/operator/kb/from-link",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "link_id": proposal["link"]["link_id"],
            "title": "Review fork routing model",
            "summary": "Review forks escalate writes to the root operator.",
            "tags": ["routing", "review"],
        },
    )
    assert proposed.status_code == 200
    kb_entry = proposed.json()
    assert kb_entry["status"] == "proposed"
    assert kb_entry["redaction_status"] == "clean"
    assert kb_entry["created_by_operator_agent_id"] == "operator-0"
    assert kb_entry["created_by_agent_id"] == fork["fork_agent_id"]
    assert kb_entry["source_knowledge_link_id"] == proposal["link"]["link_id"]
    assert kb_entry["source_turn_ids"] == [proposal["turn"]["turn_id"]]

    updated = client.patch(
        f"/v1/operator/kb/{kb_entry['kb_id']}",
        json={
            "operator_agent_id": "operator-0",
            "summary": "Review forks escalate source writes to the root operator.",
            "body": "Review forks may propose KB entries but must not promote them.",
            "tags": ["routing", "review", "kb"],
        },
    )
    assert updated.status_code == 200
    kb_entry = updated.json()
    assert kb_entry["summary"] == "Review forks escalate source writes to the root operator."
    assert kb_entry["body"] == "Review forks may propose KB entries but must not promote them."
    assert kb_entry["tags"] == ["routing", "review", "kb"]

    active_for_target = client.get(
        "/v1/operator/kb",
        params={"operator_agent_id": "operator-B", "query": "routing"},
    )
    proposed_for_source = client.get(
        "/v1/operator/kb",
        params={"operator_agent_id": "operator-0", "status": "proposed"},
    )
    proposed_for_target = client.get(
        "/v1/operator/kb",
        params={"operator_agent_id": "operator-B", "status": "proposed"},
    )
    assert active_for_target.status_code == 200
    assert active_for_target.json()["kb_entries"] == []
    assert proposed_for_source.json()["kb_entries"][0]["kb_id"] == kb_entry["kb_id"]
    assert proposed_for_target.json()["kb_entries"] == []

    fork_promote = client.post(
        f"/v1/operator/kb/{kb_entry['kb_id']}/promote",
        json={"operator_agent_id": fork["fork_agent_id"]},
    )
    assert fork_promote.status_code == 400

    promoted = client.post(
        f"/v1/operator/kb/{kb_entry['kb_id']}/promote",
        json={"operator_agent_id": "operator-0"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "active"
    assert promoted.json()["promoted_at"] is not None

    active_for_target = client.get(
        "/v1/operator/kb",
        params={"operator_agent_id": "operator-B", "query": "routing"},
    )
    assert active_for_target.json()["kb_entries"][0]["kb_id"] == kb_entry["kb_id"]

    exported = client.get(
        "/v1/operator/kb/export",
        params={"operator_agent_id": "operator-0", "query": "routing"},
    )
    assert exported.status_code == 200
    bundle = exported.json()
    assert bundle["format"] == "agent-pbx-operator-kb-v1"
    assert [entry["kb_id"] for entry in bundle["entries"]] == [kb_entry["kb_id"]]

    imported = client.post(
        "/v1/operator/kb/import",
        json={
            "operator_agent_id": "operator-B",
            "bundle": bundle,
            "import_status": "proposed",
        },
    )
    assert imported.status_code == 200
    imported_payload = imported.json()
    assert imported_payload["imported_count"] == 1
    assert imported_payload["skipped_count"] == 0
    assert imported_payload["entries"][0]["status"] == "proposed"
    assert imported_payload["entries"][0]["created_by_operator_agent_id"] == "operator-B"

    reject_candidate = client.post(
        "/v1/operator/kb",
        json={
            "operator_agent_id": "operator-0",
            "scope": "project",
            "project": "demo",
            "title": "Discarded KB proposal",
            "summary": "This proposal should not be published.",
            "body": "Superseded by the promoted routing entry.",
        },
    )
    assert reject_candidate.status_code == 200
    rejected = client.post(
        f"/v1/operator/kb/{reject_candidate.json()['kb_id']}/reject",
        json={
            "operator_agent_id": "operator-0",
            "summary": "Superseded by another KB entry.",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_operator_kb_api_seed_run_proposal_and_completion(
    tmp_path: Path,
) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    for payload in [
        {
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
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
        client.post("/v1/agents/register", json=payload)
    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
            "operator_agent_id": "operator-0",
            "source_caller_agent_id": "caller-1",
            "fork_agent_id": "operator-0-fork-caller-1-review-1",
            "fork_track_id": "review-1",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "source_cwd": str(caller_cwd),
            "work_root": str(tmp_path / ".agent-pbx-review" / "review"),
            "status": "running",
            "metadata": {"pbx_mode": "nohup"},
        },
    ).json()

    seeded = client.post(
        "/v1/operator/kb/seed-runs",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "scope": "repo",
            "project": "demo",
            "repo_root": str(caller_cwd),
            "delivery": "queue",
        },
    )
    assert seeded.status_code == 200
    seeded_payload = seeded.json()
    seed_run = seeded_payload["seed_run"]
    command = seeded_payload["command"]
    seed_sync_key = seed_run["metadata"]["seed_sync_key"]
    assert seed_run["status"] == "queued"
    assert seed_run["logical_operator_agent_id"] == "operator-0"
    assert seed_run["source_operator_agent_id"] == fork["fork_agent_id"]
    assert command["agent_id"] == fork["fork_agent_id"]
    assert command["payload"]["source"] == "operator_kb_seed_run"
    assert command["payload"]["seed_run_id"] == seed_run["seed_run_id"]

    listed = client.get(
        "/v1/operator/kb/seed-runs",
        params={"operator_agent_id": "operator-0", "status": "queued"},
    )
    assert listed.status_code == 200
    assert listed.json()["seed_runs"][0]["seed_run_id"] == seed_run["seed_run_id"]

    proposed = client.post(
        "/v1/operator/kb",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "scope": "repo",
            "project": "demo",
            "repo_root": str(caller_cwd),
            "title": "Seeded review practice",
            "summary": "Review forks can seed durable operator guidance.",
            "body": "A KB seed run records provenance before root promotion.",
            "metadata": {
                "seed_run_id": seed_run["seed_run_id"],
                "seed_sync_key": seed_sync_key,
                "seed_type": seed_run["seed_type"],
                "extraction_version": "operator_kb_seed_v1",
            },
        },
    )
    assert proposed.status_code == 200
    kb_entry = proposed.json()
    assert kb_entry["created_by_operator_agent_id"] == "operator-0"
    assert kb_entry["created_by_agent_id"] == fork["fork_agent_id"]
    assert len(kb_entry["sources"]) == 1
    source = kb_entry["sources"][0]
    assert source["source_type"] == "kb_seed_run"
    assert source["source_id"] == seed_run["seed_run_id"]
    assert source["metadata"] == {
        "seed_type": seed_run["seed_type"],
        "seed_sync_key": seed_sync_key,
    }

    completed = client.patch(
        f"/v1/operator/kb/seed-runs/{seed_run['seed_run_id']}",
        json={
            "operator_agent_id": fork["fork_agent_id"],
            "status": "complete",
            "summary": "Created one KB proposal.",
            "metadata": {"proposal_count": 1},
        },
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert completed_payload["status"] == "complete"
    assert completed_payload["completed_at"] is not None
    assert completed_payload["metadata"]["proposal_count"] == 1


def test_operator_kb_report_compile_context_and_index_api(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
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
        client.post("/v1/agents/register", json=payload)

    report = client.post(
        "/v1/agents/operator-0/reports",
        json={
            "project": "agent-pbx-operator",
            "status": "working",
            "summary": "Compiled reusable KB candidate",
            "detail": "The operator identified durable harness routing guidance.",
            "metadata": {
                "operator_kb_candidates": [
                    {
                        "scope": "project",
                        "project": "demo",
                        "title": "Windows harness routing",
                        "summary": "Windows helper work should be handed off with TTL awareness.",
                        "body": (
                            "When validating Windows helpers, include expiry and "
                            "artifact expectations in the operator handoff."
                        ),
                        "tags": ["windows", "handoff"],
                    }
                ]
            },
        },
    )
    assert report.status_code == 200
    report_id = report.json()["report_id"]

    proposed = client.get(
        "/v1/operator/kb",
        params={
            "operator_agent_id": "operator-0",
            "status": "proposed",
            "query": "Windows TTL",
            "project": "demo",
        },
    )
    assert proposed.status_code == 200
    proposed_entries = proposed.json()["kb_entries"]
    assert len(proposed_entries) == 1
    kb_entry = proposed_entries[0]
    assert kb_entry["metadata"]["compiled_from_report_id"] == report_id
    assert kb_entry["metadata"]["content_hash"]
    assert kb_entry["sources"][0]["source_type"] == "report"
    assert kb_entry["sources"][0]["source_id"] == report_id

    duplicate = client.post(
        "/v1/operator/kb/compile-report",
        json={"operator_agent_id": "operator-0", "report_id": report_id},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["proposed_count"] == 0
    assert duplicate.json()["skipped"][0]["reason"] == "duplicate_content_hash"

    promoted = client.post(
        f"/v1/operator/kb/{kb_entry['kb_id']}/promote",
        json={"operator_agent_id": "operator-0"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "active"

    context = client.post(
        "/v1/operator/kb/context",
        json={
            "operator_agent_id": "operator-B",
            "query": "Windows helper TTL",
            "project": "demo",
        },
    )
    assert context.status_code == 200
    context_payload = context.json()
    assert context_payload["satisfied_by_kb"] is True
    assert context_payload["kb_entries"][0]["kb_id"] == kb_entry["kb_id"]

    handoff = client.post(
        "/v1/operator/handoffs",
        json={
            "operator_agent_id": "operator-0",
            "source_agent_id": "operator-0",
            "target_operator_agent_id": "operator-B",
            "message": "Use the Windows helper validation context.",
            "metadata": {
                "kb_query": "Windows helper TTL",
                "kb_project": "demo",
            },
        },
    )
    assert handoff.status_code == 200
    handoff_context = handoff.json()["metadata"]["kb_context"]
    assert handoff_context["satisfied_by_kb"] is True
    assert handoff_context["kb_entries"][0]["kb_id"] == kb_entry["kb_id"]

    rebuild = client.post(
        "/v1/operator/kb/index-jobs",
        json={"operation": "rebuild", "metadata": {"test": True}},
    )
    assert rebuild.status_code == 200
    assert rebuild.json()["status"] == "queued"
    ran = client.post("/v1/operator/kb/index-jobs/run", params={"limit": 1000})
    assert ran.status_code == 200
    assert ran.json()["failed_count"] == 0
    listed = client.get(
        "/v1/operator/kb/index-jobs",
        params={"status": "complete", "limit": 10},
    )
    assert listed.status_code == 200
    assert any(job["operation"] == "rebuild" for job in listed.json()["index_jobs"])


def test_operator_handoff_api_create_approve_ack_and_complete(
    tmp_path: Path,
) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
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
        client.post("/v1/agents/register", json=payload)

    handoff = client.post(
        "/v1/operator/handoffs",
        json={
            "operator_agent_id": "operator-0",
            "source_agent_id": "operator-0",
            "target_operator_agent_id": "operator-B",
            "target_caller_agent_id": "caller-1",
            "message": "Transfer enough context to validate the checkpoint.",
            "objective": "Validate checkpoint",
            "allowed_mutation_scope": "no live mutation",
            "required_artifacts": ["contract.json"],
            "artifact_bundle": [{"path": "helper.env", "redacted": True}],
            "expires_at": 4_000_000_000.0,
        },
    )
    assert handoff.status_code == 200
    handoff_payload = handoff.json()
    handoff_id = handoff_payload["handoff_id"]

    preflight = client.post(
        f"/v1/operator/handoffs/{handoff_id}/preflight",
        json={"operator_agent_id": "operator-0", "delivery": "queue"},
    )
    assert preflight.status_code == 200
    preflight_payload = preflight.json()
    assert preflight_payload["ok"] is False
    assert preflight_payload["status"] == "pending_launch"
    assert preflight_payload["target_fork"] is None

    listed = client.get(
        "/v1/operator/handoffs",
        params={"operator_agent_id": "operator-0"},
    )
    assert listed.status_code == 200
    assert listed.json()["handoffs"][0]["handoff_id"] == handoff_id

    pending = client.post(
        f"/v1/operator/handoffs/{handoff_id}/approve",
        json={"operator_agent_id": "operator-0", "delivery": "queue"},
    )
    assert pending.status_code == 200
    pending_payload = pending.json()
    assert pending_payload["command"] is None
    assert pending_payload["handoff"]["status"] == "pending_launch"

    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
            "operator_agent_id": "operator-B",
            "source_caller_agent_id": "caller-1",
            "fork_agent_id": "operator-B-fork-caller-1",
            "tmux_pane_id": "%44",
            "status": "running",
            "metadata": {"pbx_mode": "nohup", "tmux_pane_id": "%44"},
        },
    )
    assert fork.status_code == 200

    delivered = client.post(
        f"/v1/operator/handoffs/{handoff_id}/approve",
        json={"operator_agent_id": "operator-0", "delivery": "queue"},
    )
    assert delivered.status_code == 200
    delivered_payload = delivered.json()
    assert delivered_payload["handoff"]["status"] == "sent"
    assert delivered_payload["command"]["agent_id"] == "operator-B"
    assert delivered_payload["command"]["payload"]["source"] == "operator_handoff"

    acked = client.post(
        f"/v1/operator/handoffs/{handoff_id}/ack",
        json={
            "operator_agent_id": "operator-B",
            "status": "running",
            "summary": "Received and started.",
            "detail": "Inputs present.",
        },
    )
    assert acked.status_code == 200
    assert acked.json()["delivery_evidence"]["agent_started"] is True

    completed = client.post(
        f"/v1/operator/handoffs/{handoff_id}/status",
        json={
            "operator_agent_id": "operator-B",
            "status": "complete",
            "summary": "Knowledge transfer complete.",
            "artifact_bundle": [{"path": "result.md", "redacted": False}],
        },
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert completed_payload["status"] == "complete"
    assert completed_payload["completed_at"] is not None
    assert completed_payload["artifact_bundle"][0]["path"] == "result.md"


def test_operator_fork_rebind_source_session_api(tmp_path: Path) -> None:
    caller_cwd = tmp_path / "caller-1"
    caller_cwd.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "cwd": str(tmp_path)},
        },
    )
    client.post(
        "/v1/agents/register",
        json={
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
    fork = client.post(
        "/v1/operator/forks/ensure",
        json={
            "operator_agent_id": "operator-0",
            "source_caller_agent_id": "caller-1",
            "fork_agent_id": "operator-0-fork-caller-1-review-1",
            "fork_track_id": "review-1",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "source_cwd": str(caller_cwd),
            "work_root": str(tmp_path / ".agent-pbx-review" / "review"),
            "tmux_pane_id": "%42",
            "status": "running",
            "metadata": {"pbx_mode": "report"},
        },
    ).json()
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "caller-1",
            "project": "demo",
            "metadata": {
                "pbx_mode": "report",
                "cwd": str(caller_cwd),
                "codex_session_id": "session-caller-2",
                "codex_host_id": "local",
            },
        },
    )

    rebound = client.post(
        "/v1/operator/forks/rebind-source-session",
        json={
            "operator_agent_id": "operator-0",
            "operator_fork_id": fork["operator_fork_id"],
            "source_caller_agent_id": "caller-1",
            "old_source_codex_session_id": "session-caller-1",
            "new_source_codex_session_id": "session-caller-2",
            "source_cwd": str(caller_cwd),
            "codex_host_id": "local",
            "reason": "caller restarted",
        },
    )

    assert rebound.status_code == 200
    payload = rebound.json()
    assert payload["operator_fork_id"] == fork["operator_fork_id"]
    assert payload["fork_agent_id"] == fork["fork_agent_id"]
    assert payload["source_codex_session_id"] == "session-caller-2"
    assert payload["metadata"]["previous_source_codex_session_ids"] == [
        "session-caller-1"
    ]


def test_agent_files_reports_missing_cwd_as_structured_error(tmp_path: Path) -> None:
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )

    listing = client.get("/v1/agents/agent-1/files")
    preview = client.get(
        "/v1/agents/agent-1/files/preview",
        params={"path": "README.md"},
    )
    missing = client.get("/v1/agents/missing/files")

    assert listing.status_code == 200
    assert listing.json()["entries"] == []
    assert listing.json()["error"]["code"] == "AGENT_CWD_MISSING"
    assert preview.status_code == 200
    assert preview.json()["error"]["code"] == "AGENT_CWD_MISSING"
    assert missing.status_code == 404
