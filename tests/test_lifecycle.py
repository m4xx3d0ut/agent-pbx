from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.lifecycle import LifecycleService
from agent_pbx.runtime_tmux import RuntimeTmuxPane
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def register_agent(store: Store, root: Path, *, agent_id: str = "agent-1") -> None:
    store.register_agent(
        AgentRegisterRequest(
            agent_id=agent_id,
            project="demo",
            metadata={"cwd": str(root), "codex_session_id": "session-1"},
        )
    )


def test_lifecycle_actions_require_fresh_previews_and_preserve_guards(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    register_agent(store, workspace)
    service = LifecycleService(store)

    inspection = service.inspect("agent-1")
    star_preview = service.preview_action("agent-1", "star")
    starred = service.apply_action(
        "agent-1",
        "star",
        preview_token=star_preview["preview_token"],
    )
    guarded_archive = service.preview_action("agent-1", "archive")

    assert inspection["entity_kind"] == "agent"
    assert inspection["codex"]["session_id"] == "session-1"
    assert inspection["workspace"]["available"] is True
    assert inspection["runtime"]["state"] == "unregistered"
    assert starred["after"]["agent"]["starred"] is True
    assert guarded_archive["safe"] is False
    assert "starred" in guarded_archive["reason"]

    with pytest.raises(ValueError, match="stale"):
        service.apply_action("agent-1", "unstar", preview_token="wrong")

    unstar_preview = service.preview_action("agent-1", "unstar")
    service.apply_action(
        "agent-1",
        "unstar",
        preview_token=unstar_preview["preview_token"],
    )
    canceled_preview = service.preview_action("agent-1", "mark_canceled")
    canceled = service.apply_action(
        "agent-1",
        "mark_canceled",
        preview_token=canceled_preview["preview_token"],
        metadata={"test": True},
    )
    archive_preview = service.preview_action("agent-1", "archive")
    archived = service.apply_action(
        "agent-1",
        "archive",
        preview_token=archive_preview["preview_token"],
    )
    purge_preview = service.preview_action("agent-1", "purge")
    purged = service.apply_action(
        "agent-1",
        "purge",
        preview_token=purge_preview["preview_token"],
    )

    assert canceled["after"]["agent"]["pbx_active"] is False
    assert archived["after"]["lifecycle_state"] == "archived"
    assert purge_preview["safe"] is True
    assert purged["applied"] is True
    assert store.list_reports("agent-1") == []


def test_lifecycle_mapping_repair_requires_unique_matching_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    register_agent(store, workspace)
    socket_path = tmp_path / "tmux.sock"
    store.upsert_tmux_runtime_mapping(
        entity_id="agent-1",
        server_mode="dedicated",
        server_id="server-1",
        socket_path=str(socket_path),
        session_name="runtime",
        window_id="@1",
        window_name="agent-1",
        pane_id="%1",
        pane_pid=100,
        process_start_ticks=10,
        codex_session_id="session-1",
        cwd=str(workspace),
        origin_client_tty=None,
        origin_session_name=None,
        state="ready",
        metadata={},
    )
    candidate = RuntimeTmuxPane(
        session_name="runtime",
        window_id="@2",
        window_name="agent-1",
        pane_id="%2",
        pane_pid=200,
        cwd=str(workspace),
        current_command="codex",
        title="agent-1",
    )
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr("agent_pbx.lifecycle.list_runtime_panes", lambda _identity: (candidate,))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.process_start_ticks",
        lambda pid: {100: 10, 200: 20}.get(pid),
    )
    service = LifecycleService(store)

    inspection = service.inspect("agent-1")
    preview = service.preview_mapping_repair("agent-1")
    result = service.apply_mapping_repair(
        "agent-1",
        preview_token=preview["preview_token"],
        metadata={"test": True},
    )

    assert inspection["lifecycle_state"] == "stale"
    assert inspection["runtime"]["state"] == "moved"
    assert preview["safe"] is True
    assert preview["before"]["pane_id"] == "%1"
    assert preview["candidate"]["pane_id"] == "%2"
    assert result["mapping"]["pane_id"] == "%2"
    assert result["mapping"]["process_start_ticks"] == 20
    assert result["after"]["runtime"]["state"] == "ready"


def test_lifecycle_api_exposes_preview_before_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "demo",
            "metadata": {"cwd": str(workspace), "codex_session_id": "session-1"},
        },
    )

    inspection = client.get("/v2/lifecycle/agent-1")
    preview = client.post("/v2/lifecycle/agent-1/actions/star/preview")
    applied = client.post(
        "/v2/lifecycle/agent-1/actions/star/apply",
        json={"preview_token": preview.json()["preview_token"], "metadata": {"test": True}},
    )
    stale = client.post(
        "/v2/lifecycle/agent-1/actions/unstar/apply",
        json={"preview_token": preview.json()["preview_token"]},
    )

    assert inspection.status_code == 200
    assert inspection.json()["actions"]["resume"]["available"] is True
    assert preview.json()["safe"] is True
    assert applied.json()["after"]["agent"]["starred"] is True
    assert stale.status_code == 409


def test_campaign_api_pages_summaries_and_loads_events_on_detail(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    store: Store = app.state.store
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"cwd": str(tmp_path)},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    created = [
        store.create_operator_campaign(
            operator_agent_id="operator-0",
            title=f"Campaign {index}",
            objective="Exercise paged summaries.",
            criteria=[],
            assignments=[
                {
                    "target_agent_id": "caller-1",
                    "prompt": f"Run assignment {index}.",
                }
            ],
        )
        for index in range(3)
    ]
    client = TestClient(app)

    first = client.get(
        "/v2/operator/campaigns",
        params={"operator_agent_id": "operator-0", "limit": 2, "cursor": 0},
    )
    second = client.get(
        "/v2/operator/campaigns",
        params={"operator_agent_id": "operator-0", "limit": 2, "cursor": 2},
    )
    detail = client.get(
        f"/v2/operator/campaigns/{created[0]['campaign_id']}",
        params={"operator_agent_id": "operator-0"},
    )

    assert first.status_code == 200
    assert len(first.json()["campaigns"]) == 2
    assert first.json()["has_more"] is True
    assert first.json()["next_cursor"] == 2
    assert all(item["events"] == [] for item in first.json()["campaigns"])
    assert second.status_code == 200
    assert len(second.json()["campaigns"]) == 1
    assert second.json()["has_more"] is False
    assert second.json()["next_cursor"] is None
    assert {
        item["campaign_id"]
        for item in first.json()["campaigns"] + second.json()["campaigns"]
    } == {item["campaign_id"] for item in created}
    assert detail.status_code == 200
    assert detail.json()["events"][0]["event_type"] == "campaign_started"
