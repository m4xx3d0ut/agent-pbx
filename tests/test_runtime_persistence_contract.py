from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def registered_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    return store


def test_model_elevation_contract_preserves_idempotency_and_transition_merge(
    tmp_path: Path,
) -> None:
    store = registered_store(tmp_path)
    lease, created = store.request_or_reuse_model_elevation(
        agent_id="agent-1",
        requested_profile="sol-max",
        prior_profile="sol-xhigh",
        justification="bounded architecture review",
        scope="one_task",
    )
    repeated, repeated_created = store.request_or_reuse_model_elevation(
        agent_id="agent-1",
        requested_profile="sol-max",
        prior_profile="sol-xhigh",
        justification="duplicate dispatch",
        scope="one_task",
    )

    assert created is True
    assert repeated_created is False
    assert repeated["lease_id"] == lease["lease_id"]
    with pytest.raises(ValueError, match="already has an open model elevation"):
        store.request_or_reuse_model_elevation(
            agent_id="agent-1",
            requested_profile="terra-max",
            justification="conflicting request",
            scope="one_task",
        )

    approved = store.decide_model_elevation(
        lease["lease_id"],
        approved=True,
        approved_by="architect",
        duration_seconds=300,
    )
    active = store.activate_model_elevation(
        lease["lease_id"],
        transition={"resume_session_id": "session-1"},
    )
    updated = store.update_model_elevation_transition(
        lease["lease_id"],
        transition={"verified_model": "sol-max"},
    )
    pending = store.mark_model_elevation_revert_pending(
        lease["lease_id"],
        reason="scope_complete",
        transition={"turn_complete": True},
    )

    assert approved["status"] == "approved"
    assert active["status"] == "active"
    assert updated["transition"] == {
        "resume_session_id": "session-1",
        "verified_model": "sol-max",
    }
    assert pending["status"] == "revert_pending"
    assert pending["transition"]["revert_reason"] == "scope_complete"
    assert pending["transition"]["turn_complete"] is True
    assert pending["expired"] is False

    reverted = store.finish_model_elevation(
        lease["lease_id"],
        status="reverted",
        transition={"restored_profile": "sol-xhigh"},
    )
    assert reverted["transition"]["verified_model"] == "sol-max"
    assert reverted["transition"]["restored_profile"] == "sol-xhigh"
    assert store.list_model_elevations(agent_id="agent-1", limit=1) == [reverted]


def test_model_elevation_expiry_contract_distinguishes_approved_and_active(
    tmp_path: Path,
) -> None:
    store = registered_store(tmp_path)
    approved = store.request_model_elevation(
        agent_id="agent-1",
        requested_profile="sol-max",
        prior_profile="sol-xhigh",
        justification="approved expiry",
        scope="one_task",
    )
    approved = store.decide_model_elevation(
        approved["lease_id"],
        approved=True,
        approved_by="architect",
        duration_seconds=300,
    )
    assert store.expire_model_elevations(
        current_time=float(approved["expires_at"]) + 1,
    ) == 1
    assert store.get_model_elevation(approved["lease_id"])["status"] == "expired"  # type: ignore[index]

    active = store.request_model_elevation(
        agent_id="agent-1",
        requested_profile="sol-max",
        prior_profile="sol-xhigh",
        justification="active expiry",
        scope="one_task",
    )
    active = store.decide_model_elevation(
        active["lease_id"],
        approved=True,
        approved_by="architect",
        duration_seconds=300,
    )
    active = store.activate_model_elevation(active["lease_id"])
    assert store.expire_model_elevations(
        current_time=float(active["expires_at"]) + 1,
    ) == 1
    expired_active = store.get_model_elevation(active["lease_id"])
    assert expired_active is not None
    assert expired_active["status"] == "revert_pending"
    assert expired_active["transition"]["revert_reason"] == "lease_expired"


def test_tmux_mapping_contract_preserves_origin_and_revokes_changed_pane_lease(
    tmp_path: Path,
) -> None:
    store = registered_store(tmp_path)
    mapping = store.upsert_tmux_runtime_mapping(
        entity_id="agent-1",
        server_mode="outer_if_present",
        server_id="server-1",
        socket_path="/tmp/pbx.sock",
        session_name="runtime-agent-1",
        window_id="@1",
        pane_id="%1",
        origin_client_tty="/dev/pts/7",
        origin_session_name="agent-pbx",
        metadata={"generation": 1},
    )
    leased = store.acquire_tmux_writer_lease(
        "agent-1",
        client_id="tui-1",
        ttl_seconds=1,
    )
    same_pane = store.upsert_tmux_runtime_mapping(
        entity_id="agent-1",
        server_mode="outer_if_present",
        server_id="server-1",
        socket_path="/tmp/pbx.sock",
        session_name="runtime-agent-1",
        window_id="@1",
        pane_id="%1",
        origin_client_tty=None,
        origin_session_name=None,
        metadata={"generation": 2},
    )

    assert mapping["origin_client_tty"] == "/dev/pts/7"
    assert leased["writer_client_id"] == "tui-1"
    assert leased["writer_lease_expires_at"] - leased["updated_at"] >= 5
    assert same_pane["origin_client_tty"] == "/dev/pts/7"
    assert same_pane["origin_session_name"] == "agent-pbx"
    assert same_pane["writer_client_id"] == "tui-1"
    assert same_pane["metadata"] == {"generation": 2}

    with pytest.raises(ValueError, match="leased to 'tui-1'"):
        store.acquire_tmux_writer_lease(
            "agent-1",
            client_id="tui-2",
            ttl_seconds=30,
        )

    moved = store.upsert_tmux_runtime_mapping(
        entity_id="agent-1",
        server_mode="outer_if_present",
        server_id="server-1",
        socket_path="/tmp/pbx.sock",
        session_name="runtime-agent-1",
        window_id="@1",
        pane_id="%2",
        metadata={"generation": 3},
    )
    assert moved["writer_client_id"] is None
    assert moved["writer_lease_expires_at"] is None
    assert moved["writer_lease_active"] is False


def test_tmux_mapping_state_and_runtime_migration_retention_contract(
    tmp_path: Path,
) -> None:
    store = registered_store(tmp_path)
    store.upsert_tmux_runtime_mapping(
        entity_id="agent-1",
        server_mode="dedicated",
        server_id="server-1",
        socket_path="/tmp/pbx.sock",
        session_name="runtime-agent-1",
        pane_id="%1",
        metadata={"existing": True},
    )
    store.acquire_tmux_writer_lease(
        "agent-1",
        client_id="tui-1",
        ttl_seconds=30,
    )
    stopped = store.set_tmux_runtime_state(
        "agent-1",
        state="server_lost",
        metadata={"reason": "fixture"},
        clear_writer_lease=True,
    )
    assert stopped["metadata"] == {"existing": True, "reason": "fixture"}
    assert stopped["writer_client_id"] is None
    assert [item["entity_id"] for item in store.list_tmux_runtime_mappings()] == [
        "agent-1"
    ]

    retention_until = time.time() + 3600
    batch = store.create_runtime_migration_batch(
        target_cli_version="0.200.0",
        retention_until=retention_until,
        snapshot={"candidates": []},
    )
    updated = store.update_runtime_migration_batch(
        batch["batch_id"],
        status="applied",
        results=[{"agent_id": "agent-1", "status": "ready"}],
        applied=True,
    )
    assert batch["retained"] is True
    assert updated["applied_at"] is not None
    assert updated["results"] == [{"agent_id": "agent-1", "status": "ready"}]
    assert store.list_runtime_migration_batches(limit=1) == [updated]
    assert store.prune_expired_runtime_migration_batches(
        now=retention_until + 1,
    ) == 1
    assert store.get_runtime_migration_batch(batch["batch_id"]) is None
