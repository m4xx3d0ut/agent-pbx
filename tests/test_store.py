from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from agent_pbx.schemas import AgentRegisterRequest, ReportCreateRequest
from agent_pbx.store import SCHEMA_VERSION, STALE_WORKING_SECONDS, Store


def test_agent_summary_keeps_pending_handoff_approval_durable(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id, agent_type in (
        ("operator-0", "operator"),
        ("operator-7", "operator"),
    ):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="demo",
                agent_type=agent_type,
                metadata={"cwd": str(tmp_path / agent_id)},
            )
        )
    handoff = store.create_operator_handoff(
        logical_operator_agent_id="operator-0",
        source_operator_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        source_agent_id="operator-0",
        objective="Review the exact checkpoint",
        message="Review the exact checkpoint",
    )

    by_id = {agent["agent_id"]: agent for agent in store.list_agents()}
    assert by_id["operator-0"]["pending_handoff_approval_count"] == 1
    assert by_id["operator-0"]["oldest_pending_handoff_created_at"] is not None
    assert by_id["operator-7"]["pending_handoff_approval_count"] == 0

    store.update_operator_handoff(handoff["handoff_id"], status="sent")
    by_id = {agent["agent_id"]: agent for agent in store.list_agents()}
    assert by_id["operator-0"]["pending_handoff_approval_count"] == 0


@pytest.mark.parametrize("report_status", ["working", "waiting"])
def test_operator_stale_status_precedes_report_state_but_not_durable_work(
    tmp_path: Path,
    report_status: str,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id in ("operator-0", "operator-7"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={"cwd": str(tmp_path / agent_id)},
            )
        )
    store.create_report(
        "operator-0",
        ReportCreateRequest(
            project="agent-pbx-operator",
            status=report_status,
            summary=f"Operator is {report_status}",
            detail="The latest durable report state is retained for explanation.",
        ),
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE agents SET status = 'working', last_seen_at = ? WHERE agent_id = ?",
            (0.0, "operator-0"),
        )

    stale = {agent["agent_id"]: agent for agent in store.list_agents()}[
        "operator-0"
    ]
    assert stale["status_age_seconds"] >= STALE_WORKING_SECONDS
    assert stale["workflow_state"] == "stale"
    assert stale["workflow_label"] == "△ STALE"
    assert stale["workflow_reason"].endswith(
        f"latest report remains {report_status}"
    )
    assert stale["workflow_source"] == "agent_status"

    store.create_operator_handoff(
        logical_operator_agent_id="operator-0",
        source_operator_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        source_agent_id="operator-0",
        objective="Review durable work",
        message="Review the exact durable state.",
    )
    durable = {agent["agent_id"]: agent for agent in store.list_agents()}[
        "operator-0"
    ]
    assert durable["status_stale"] is True
    assert durable["workflow_state"] == "approval"
    assert durable["workflow_source"] == "handoff_approval"


def test_cancel_operator_handoff_is_atomic_and_clears_pending_summary(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id in ("operator-0", "operator-7"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="demo",
                agent_type="operator",
                metadata={"cwd": str(tmp_path / agent_id)},
            )
        )
    handoff = store.create_operator_handoff(
        logical_operator_agent_id="operator-0",
        source_operator_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        source_agent_id="operator-0",
        objective="Replace the legacy request",
        message="Replace the legacy request",
        metadata={"preserved": True},
    )
    cancellation = {
        "reason": "no_longer_needed",
        "previous_status": "proposed",
        "canceled_by": "operator-0",
    }

    canceled = store.cancel_operator_handoff(
        handoff["handoff_id"],
        logical_operator_agent_id="operator-0",
        expected_status="proposed",
        summary="Canceled before delivery: no longer needed.",
        cancellation=cancellation,
        metadata={"requested_by": "test"},
    )

    assert canceled is not None
    assert canceled["status"] == "canceled"
    assert canceled["completed_at"] is not None
    assert canceled["metadata"]["preserved"] is True
    assert canceled["metadata"]["requested_by"] == "test"
    assert canceled["metadata"]["cancellation"] == cancellation
    by_id = {agent["agent_id"]: agent for agent in store.list_agents()}
    assert by_id["operator-0"]["pending_handoff_approval_count"] == 0

    raced = store.create_operator_handoff(
        logical_operator_agent_id="operator-0",
        source_operator_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        source_agent_id="operator-0",
        objective="Race cancellation",
        message="Race cancellation",
    )
    store.update_operator_handoff(raced["handoff_id"], status="sent")

    assert (
        store.cancel_operator_handoff(
            raced["handoff_id"],
            logical_operator_agent_id="operator-0",
            expected_status="proposed",
            summary="Must not overwrite sent.",
            cancellation=cancellation,
        )
        is None
    )
    assert store.get_operator_handoff(raced["handoff_id"])["status"] == "sent"


def test_model_elevation_request_is_idempotent_under_concurrency(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    barrier = threading.Barrier(2)

    def request() -> tuple[str, bool]:
        barrier.wait(timeout=5)
        lease, created = store.request_or_reuse_model_elevation(
            agent_id="agent-1",
            requested_profile="sol-max",
            prior_profile="sol-5.6-xhigh",
            justification="Difficult bounded architecture synthesis",
            scope="one_task",
        )
        return str(lease["lease_id"]), created

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(request), pool.submit(request))]

    assert len({lease_id for lease_id, _created in results}) == 1
    assert sorted(created for _lease_id, created in results) == [False, True]
    assert len(store.list_model_elevations(agent_id="agent-1")) == 1


def test_store_installs_agent_summary_indexes(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    with store.connect() as conn:
        for name in (
            "idx_reports_agent_created",
            "idx_commands_status_agent_created",
            "idx_commands_agent_created",
            "idx_poll_events_agent_created",
        ):
            conn.execute(f"DROP INDEX {name}")
        conn.execute(
            "UPDATE metadata SET value = '28' WHERE key = 'schema_version'"
        )

    store.init()
    with store.connect() as conn:
        indexes = {
            str(row["name"])
            for table in ("reports", "commands", "poll_events")
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        }

    assert store.schema_version() == SCHEMA_VERSION
    assert {
        "idx_reports_agent_created",
        "idx_commands_status_agent_created",
        "idx_commands_agent_created",
        "idx_poll_events_agent_created",
    } <= indexes


def test_register_agent_merges_metadata_without_dropping_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            name="Agent One",
            metadata={"cwd": str(tmp_path), "task": "initial"},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"pbx_mode": "report", "task": "updated"},
        )
    )

    assert agent["name"] == "Agent One"
    assert agent["metadata"] == {
        "cwd": str(tmp_path),
        "task": "updated",
        "pbx_mode": "report",
    }


def test_merge_agent_metadata_journals_controller_update_atomically(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path), "existing": "value"},
        )
    )

    agent = store.merge_agent_metadata(
        "agent-1",
        {"workerbee_workspace_binding_id": "wb-next"},
        event_type="workerbee_workspace_refreshed",
        event_payload={"previous_binding_id": "wb-old", "workspace_binding_id": "wb-next"},
    )

    assert agent is not None
    assert agent["metadata"]["existing"] == "value"
    assert agent["metadata"]["workerbee_workspace_binding_id"] == "wb-next"
    event = store.list_events(limit=1)[0]
    assert event["type"] == "workerbee_workspace_refreshed"
    assert event["subject_id"] == "agent-1"
    assert event["payload"]["workspace_binding_id"] == "wb-next"


def test_terminal_editor_runtime_records_are_durable_and_agent_scoped(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    runtime = {
        "runtime_id": "editor-1",
        "agent_id": "agent-1",
        "project": "demo",
        "access_mode": "edit",
        "profile_id": "nvim",
        "path": str(tmp_path / "app.py"),
        "root": str(tmp_path),
        "socket_path": str(tmp_path / "tmux.sock"),
        "rpc_socket_path": str(tmp_path / "nvim.sock"),
        "rpc_enabled": True,
        "server_id": "server-1",
        "session_name": "pbx-editor-one",
        "window_id": "@1",
        "pane_id": "%1",
        "pane_pid": 123,
        "process_start_ticks": 456,
        "tmux_bin": "/usr/bin/tmux",
        "state": "ready",
    }

    stored = store.upsert_terminal_editor_runtime(
        runtime,
        metadata={"owner": "agent-pbx"},
        verified=True,
    )
    detached = store.update_terminal_editor_runtime_state(
        "agent-1",
        state="detached",
    )

    assert stored["rpc_enabled"] is True
    assert stored["metadata"] == {"owner": "agent-pbx"}
    assert stored["last_verified_at"] is not None
    assert detached is not None and detached["state"] == "detached"
    assert store.list_terminal_editor_runtimes()[0]["runtime_id"] == "editor-1"
    assert store.delete_terminal_editor_runtime("agent-1") is True
    assert store.get_terminal_editor_runtime("agent-1") is None


def test_register_agent_empty_cwd_does_not_clear_existing_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": "", "pbx_mode": "nohup"},
        )
    )

    assert agent["metadata"]["cwd"] == str(tmp_path)
    assert agent["metadata"]["pbx_mode"] == "nohup"


def test_register_agent_backfills_cwd_from_absolute_repo_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"repo": str(repo), "task": "review"},
        )
    )

    assert agent["metadata"]["cwd"] == str(repo)


def test_register_agent_preserves_logical_project_when_refresh_uses_cwd(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "1337-b04rd"
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="board-agent",
            project="1337-board",
            metadata={"cwd": str(repo)},
        )
    )

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="board-agent",
            project=str(repo),
            metadata={"cwd": str(repo), "pbx_mode": "report"},
        )
    )

    assert agent["project"] == "1337-board"
    assert agent["metadata"]["cwd"] == str(repo)
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_makes_first_path_project_route_safe(tmp_path: Path) -> None:
    repo = tmp_path / "1337-b04rd"
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="board-agent",
            project=str(repo),
            metadata={"cwd": str(repo)},
        )
    )

    assert agent["project"] == "1337-b04rd"


def test_register_agent_can_replace_path_project_with_logical_project(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "1337-b04rd"
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="board-agent",
            project=str(repo),
            metadata={"cwd": str(repo)},
        )
    )

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="board-agent",
            project="1337-board",
            metadata={"cwd": str(repo)},
        )
    )

    assert agent["project"] == "1337-board"


def test_register_agent_preserves_operator_identity_on_default_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
            },
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s",
            name="Codex k1s Stage 1 fork",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path / "k1s")},
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "agent-pbx-operator"
    assert agent["name"] == "operator-0"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "root"
    assert agent["metadata"]["cwd"] == str(tmp_path / "agent-pbx")
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_preserves_root_operator_default_source_on_blank_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
                "default_source_caller_agent_id": "caller-1",
                "default_source_caller_project": "demo",
                "default_source_codex_session_id": "session-1",
            },
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "default_source_caller_agent_id": "",
                "default_source_caller_project": "",
                "default_source_codex_session_id": "",
                "pbx_mode": "report",
            },
        )
    )

    assert agent["metadata"]["default_source_caller_agent_id"] == "caller-1"
    assert agent["metadata"]["default_source_caller_project"] == "demo"
    assert agent["metadata"]["default_source_codex_session_id"] == "session-1"
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_canonicalizes_root_operator_project_on_bad_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
                "tmux_pane_id": "%1",
                "launched_by": "agent-pbx-tui",
            },
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s-workerbee-private",
            name="Codex k1s Stage 1 fork",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "k1s-workerbee-private"),
                "tmux_pane_id": "%99",
                "pbx_mode": "report",
            },
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "agent-pbx-operator"
    assert agent["name"] == "operator-0"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "root"
    assert agent["metadata"]["cwd"] == str(tmp_path / "agent-pbx")
    assert agent["metadata"]["tmux_pane_id"] == "%1"
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_allows_explicit_operator_demote(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={"agent_type": "operator", "operator_role": "root"},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s",
            name="Codex k1s Stage 1 fork",
            metadata={"agent_type": "caller", "cwd": str(tmp_path / "k1s")},
        )
    )

    assert agent["agent_type"] == "caller"
    assert agent["project"] == "k1s"
    assert agent["name"] == "Codex k1s Stage 1 fork"
    assert agent["metadata"]["agent_type"] == "caller"


def test_register_agent_preserves_operator_fork_identity_on_bad_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"agent_type": "operator", "operator_role": "root"},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"cwd": str(tmp_path / "demo"), "codex_session_id": "session-1"},
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
                "cwd": str(tmp_path / "demo"),
                "tmux_pane_id": "%42",
            },
        )
    )
    store.create_operator_fork(
        logical_operator_agent_id="operator-0",
        fork_agent_id="operator-0-fork-caller-1",
        source_caller_agent_id="caller-1",
        source_codex_session_id="session-1",
        cwd=str(tmp_path / "demo"),
        tmux_pane_id="%42",
        status="running",
        metadata={"operator_fork_pending": False},
    )

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0-fork-caller-1",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
                "tmux_pane_id": "%1",
                "pbx_mode": "report",
            },
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "demo"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "fork"
    assert agent["metadata"]["logical_operator_id"] == "operator-0"
    assert agent["metadata"]["source_caller_agent_id"] == "caller-1"
    assert agent["metadata"]["source_codex_session_id"] == "session-1"
    assert agent["metadata"]["cwd"] == str(tmp_path / "demo")
    assert agent["metadata"]["tmux_pane_id"] == "%42"
    assert agent["metadata"]["pbx_mode"] == "report"
