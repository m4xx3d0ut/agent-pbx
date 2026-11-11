from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.lifecycle import LifecycleService
from agent_pbx.runtime_tmux import RuntimeTmuxPane
from agent_pbx.schemas import AgentRegisterRequest, ReportCreateRequest
from agent_pbx.store import Store


def register_agent(store: Store, root: Path, *, agent_id: str = "agent-1") -> None:
    store.register_agent(
        AgentRegisterRequest(
            agent_id=agent_id,
            project="demo",
            metadata={"cwd": str(root), "codex_session_id": "session-1"},
        )
    )


def register_operator_fork_runtime(
    store: Store,
    root: Path,
) -> tuple[dict[str, object], RuntimeTmuxPane]:
    register_agent(store, root, agent_id="caller-1")
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "launched_by": "agent-pbx-tui",
            },
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0-fork-caller-1",
            project="demo",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "fork_codex_session_id": "fork-session-1",
                "cwd": str(root),
                "tmux_pane_id": "%8",
                "launched_by": "agent-pbx-tui",
            },
        )
    )
    store.create_operator_fork(
        logical_operator_agent_id="operator-0",
        fork_agent_id="operator-0-fork-caller-1",
        source_caller_agent_id="caller-1",
        source_codex_session_id="session-1",
        fork_codex_session_id="fork-session-1",
        cwd=str(root),
        tmux_pane_id="%8",
        status="running",
        metadata={"tmux_pane_id": "%8"},
    )
    mapping = store.upsert_tmux_runtime_mapping(
        entity_id="operator-0-fork-caller-1",
        server_mode="dedicated",
        server_id="runtime-server",
        socket_path=str(root / "runtime.sock"),
        session_name="runtime-fork",
        window_id="@8",
        window_name="operator-0-fork-caller-1",
        pane_id="%8",
        pane_pid=800,
        process_start_ticks=80,
        codex_session_id="fork-session-1",
        cwd=str(root),
        state="ready",
        metadata={"managed_by": "agent-pbx-tui"},
    )
    pane = RuntimeTmuxPane(
        session_name="runtime-fork",
        window_id="@8",
        window_name="operator-0-fork-caller-1",
        pane_id="%8",
        pane_pid=800,
        cwd=str(root),
        current_command="codex",
        title="operator-0-fork-caller-1",
    )
    return mapping, pane


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
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", (candidate,)),
    )
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


def test_lifecycle_mapping_repair_supersedes_stale_managed_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    mapping, original_pane = register_operator_fork_runtime(store, workspace)
    replacement_pane = RuntimeTmuxPane(
        session_name=original_pane.session_name,
        window_id="@9",
        window_name=original_pane.window_name,
        pane_id="%9",
        pane_pid=900,
        cwd=original_pane.cwd,
        current_command="codex",
        title=original_pane.title,
    )
    panes: tuple[RuntimeTmuxPane, ...] = (original_pane,)
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", panes),
    )
    monkeypatch.setattr(
        "agent_pbx.lifecycle.process_start_ticks",
        lambda pid: {800: 80, 900: 90}.get(pid),
    )
    service = LifecycleService(store)

    prepared = service.prepare_managed_runtime_stop(
        "operator-0-fork-caller-1",
        expected_identity=store._managed_runtime_stop_identity(mapping),
        client_id="tui-1",
    )
    old_transaction_id = str(prepared["transaction_id"])
    panes = (replacement_pane,)

    preview = service.preview_mapping_repair("operator-0-fork-caller-1")
    repaired = service.apply_mapping_repair(
        "operator-0-fork-caller-1",
        preview_token=preview["preview_token"],
    )
    repaired_mapping = repaired["mapping"]

    assert repaired_mapping["state"] == "ready"
    assert repaired_mapping["pane_id"] == "%9"
    assert "managed_stop" not in repaired_mapping["metadata"]
    assert (
        repaired_mapping["metadata"]["last_superseded_managed_stop"][
            "transaction_id"
        ]
        == old_transaction_id
    )

    retried = service.prepare_managed_runtime_stop(
        "operator-0-fork-caller-1",
        expected_identity=store._managed_runtime_stop_identity(repaired_mapping),
        client_id="tui-1",
    )
    assert retried["transaction_id"] != old_transaction_id
    assert retried["mapping"]["metadata"]["managed_stop"]["identity"]["pane_id"] == "%9"


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


def test_managed_stop_finalizes_agent_runtime_and_fork_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    mapping, pane = register_operator_fork_runtime(store, workspace)
    panes: tuple[RuntimeTmuxPane, ...] = (pane,)
    ticks: int | None = 80
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", panes),
    )
    monkeypatch.setattr("agent_pbx.lifecycle.process_start_ticks", lambda _pid: ticks)
    service = LifecycleService(store)

    prepared = service.prepare_managed_runtime_stop(
        "operator-0-fork-caller-1",
        expected_identity=store._managed_runtime_stop_identity(mapping),
        client_id="tui-1",
        metadata={"test": True},
    )

    assert prepared["requires_pane_stop"] is True
    assert prepared["phase"] == "qualified"
    assert store.get_tmux_runtime_mapping("operator-0-fork-caller-1")["state"] == "stopping"  # type: ignore[index]
    assert service.inspect("operator-0-fork-caller-1")["lifecycle_state"] == "stopping"

    panes = ()
    with pytest.raises(ValueError, match="process identity is still alive"):
        service.finalize_managed_runtime_stop(
            "operator-0-fork-caller-1",
            transaction_id=str(prepared["transaction_id"]),
        )
    ticks = None
    finalized = service.finalize_managed_runtime_stop(
        "operator-0-fork-caller-1",
        transaction_id=str(prepared["transaction_id"]),
        metadata={"test": True},
    )

    assert finalized["idempotent"] is False
    assert finalized["runtime_mapping"] is None
    agent = store.get_agent("operator-0-fork-caller-1")
    fork = store.get_operator_fork_for_agent("operator-0-fork-caller-1")
    assert agent is not None
    assert agent["status"] == "canceled"
    assert agent["pbx_active"] is False
    assert "tmux_pane_id" not in agent["metadata"]
    assert agent["metadata"]["fork_codex_session_id"] == "fork-session-1"
    assert fork is not None
    assert fork["status"] == "canceled"
    assert fork["tmux_pane_id"] is None
    assert fork["completed_at"] is not None
    assert fork["fork_codex_session_id"] == "fork-session-1"
    assert finalized["after"]["lifecycle_state"] == "stopped"

    repeated = service.finalize_managed_runtime_stop(
        "operator-0-fork-caller-1",
        transaction_id=str(prepared["transaction_id"]),
    )
    assert repeated["idempotent"] is True
    assert repeated["report_id"] == finalized["report_id"]


def test_managed_stop_finalizes_exact_dead_pane_with_distinct_repair_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    mapping, original_pane = register_operator_fork_runtime(store, workspace)
    duplicate_pane = RuntimeTmuxPane(
        session_name=original_pane.session_name,
        window_id="@9",
        window_name=original_pane.window_name,
        pane_id="%9",
        pane_pid=900,
        cwd=original_pane.cwd,
        current_command="codex",
        title=original_pane.title,
    )
    panes: tuple[RuntimeTmuxPane, ...] = (original_pane,)
    process_ticks: dict[int, int] = {800: 80, 900: 90}
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", panes),
    )
    monkeypatch.setattr(
        "agent_pbx.lifecycle.process_start_ticks",
        lambda pid: process_ticks.get(int(pid)) if pid is not None else None,
    )
    service = LifecycleService(store)

    prepared = service.prepare_managed_runtime_stop(
        "operator-0-fork-caller-1",
        expected_identity=store._managed_runtime_stop_identity(mapping),
        client_id="tui-1",
    )
    panes = (duplicate_pane,)

    with pytest.raises(ValueError, match="process identity is still alive"):
        service.finalize_managed_runtime_stop(
            "operator-0-fork-caller-1",
            transaction_id=str(prepared["transaction_id"]),
        )

    process_ticks.pop(800)
    finalized = service.finalize_managed_runtime_stop(
        "operator-0-fork-caller-1",
        transaction_id=str(prepared["transaction_id"]),
    )

    assert finalized["runtime_mapping"] is None
    agent = store.get_agent("operator-0-fork-caller-1")
    fork = store.get_operator_fork_for_agent("operator-0-fork-caller-1")
    assert agent is not None
    assert agent["pbx_active"] is False
    assert fork is not None
    assert fork["status"] == "canceled"


def test_managed_stop_fails_closed_on_foreign_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    mapping, pane = register_operator_fork_runtime(store, workspace)
    store.acquire_tmux_writer_lease(
        "operator-0-fork-caller-1",
        client_id="other-tui",
        ttl_seconds=30,
    )
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", (pane,)),
    )
    monkeypatch.setattr("agent_pbx.lifecycle.process_start_ticks", lambda _pid: 80)
    service = LifecycleService(store)

    with pytest.raises(ValueError, match="other-tui"):
        service.prepare_managed_runtime_stop(
            "operator-0-fork-caller-1",
            expected_identity=store._managed_runtime_stop_identity(mapping),
            client_id="tui-1",
        )

    unchanged = store.get_tmux_runtime_mapping("operator-0-fork-caller-1")
    assert unchanged is not None
    assert unchanged["state"] == "ready"
    assert unchanged["writer_client_id"] == "other-tui"


def test_lifecycle_reports_agent_runtime_fork_split_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _mapping, pane = register_operator_fork_runtime(store, workspace)
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr("agent_pbx.lifecycle.process_start_ticks", lambda _pid: 80)
    service = LifecycleService(store)

    store.create_report(
        "operator-0-fork-caller-1",
        ReportCreateRequest(
            project="demo",
            status="canceled",
            summary="Canceled",
            detail="Canceled while runtime remained live.",
        ),
    )
    store.set_agent_pbx_active("operator-0-fork-caller-1", False)
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", (pane,)),
    )
    live_split = service.inspect("operator-0-fork-caller-1")
    assert live_split["lifecycle_state"] == "error"
    assert "canceled_agent_live_runtime" in live_split["consistency_issues"]
    assert live_split["actions"]["resume"]["available"] is False

    store.create_report(
        "operator-0-fork-caller-1",
        ReportCreateRequest(
            project="demo",
            status="working",
            summary="Working",
            detail="Fork row remains active after pane loss.",
        ),
    )
    store.set_agent_pbx_active("operator-0-fork-caller-1", True)
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", ()),
    )
    missing_split = service.inspect("operator-0-fork-caller-1")
    assert missing_split["lifecycle_state"] == "error"
    assert "missing_runtime_active_fork" in missing_split["consistency_issues"]


def test_managed_stop_api_blocks_new_writers_and_finalizes_after_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    store: Store = app.state.store
    workspace = tmp_path / "repo"
    workspace.mkdir()
    mapping, pane = register_operator_fork_runtime(store, workspace)
    panes: tuple[RuntimeTmuxPane, ...] = (pane,)
    ticks: int | None = 80
    monkeypatch.setattr("agent_pbx.lifecycle.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr(
        "agent_pbx.lifecycle.probe_runtime_panes",
        lambda _identity: (True, "", panes),
    )
    monkeypatch.setattr("agent_pbx.lifecycle.process_start_ticks", lambda _pid: ticks)
    client = TestClient(app)
    identity = store._managed_runtime_stop_identity(mapping)

    prepared = client.post(
        "/v2/lifecycle/operator-0-fork-caller-1/managed-stop/prepare",
        json={
            "client_id": "tui-1",
            "expected_server_id": identity["server_id"],
            "expected_socket_path": identity["socket_path"],
            "expected_session_name": identity["session_name"],
            "expected_window_id": identity["window_id"],
            "expected_pane_id": identity["pane_id"],
            "expected_pane_pid": identity["pane_pid"],
            "expected_process_start_ticks": identity["process_start_ticks"],
            "expected_codex_session_id": identity["codex_session_id"],
        },
    )
    assert prepared.status_code == 200
    transaction_id = prepared.json()["transaction_id"]
    blocked_writer = client.post(
        "/v2/tmux/runtimes/operator-0-fork-caller-1/writer/acquire",
        json={"client_id": "other-tui", "ttl_seconds": 30},
    )
    assert blocked_writer.status_code == 409
    assert "stopping" in blocked_writer.json()["detail"]

    panes = ()
    ticks = None
    finalized = client.post(
        "/v2/lifecycle/operator-0-fork-caller-1/managed-stop/finalize",
        json={"transaction_id": transaction_id},
    )
    assert finalized.status_code == 200
    assert finalized.json()["runtime_mapping"] is None


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
