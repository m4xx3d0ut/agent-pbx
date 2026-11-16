from __future__ import annotations

from pathlib import Path

import pytest

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def _store(tmp_path: Path) -> Store:
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


def _editor_runtime(tmp_path: Path, *, path: str = "app.py") -> dict[str, object]:
    return {
        "runtime_id": "editor-1",
        "agent_id": "agent-1",
        "project": "demo",
        "access_mode": "edit",
        "profile_id": "nvim",
        "path": str(tmp_path / path),
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


def _note(*, revision: str = "rev-1", body: str = "Body") -> dict[str, object]:
    return {
        "id": "note-1",
        "revision": revision,
        "updated_time": 1_700_000_000_001,
        "user_updated_time": 1_700_000_000_002,
        "title_hash": f"title-{revision}",
        "body_hash": f"body-{revision}",
        "title": "Title",
        "body": body,
    }


def test_terminal_editor_runtime_preserves_verification_and_path_contract(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    initial = store.upsert_terminal_editor_runtime(
        _editor_runtime(tmp_path),
        metadata={"owner": "agent-pbx", "generation": 1},
        verified=True,
    )
    assert initial["last_verified_at"] is not None

    refreshed = store.upsert_terminal_editor_runtime(
        _editor_runtime(tmp_path, path="other.py"),
        metadata={"owner": "agent-pbx", "generation": 2},
        verified=False,
    )
    assert refreshed["last_verified_at"] == initial["last_verified_at"]
    assert refreshed["metadata"] == {"owner": "agent-pbx", "generation": 2}

    detached = store.update_terminal_editor_runtime_state(
        "agent-1",
        state="detached",
        last_error="client closed",
        path=str(tmp_path / "final.py"),
    )
    assert detached is not None
    assert detached["state"] == "detached"
    assert detached["last_error"] == "client closed"
    assert detached["path"] == str(tmp_path / "final.py")

    reopened = Store(tmp_path / "pbx.sqlite")
    reopened.init()
    assert reopened.get_terminal_editor_runtime("agent-1") == detached


def test_terminal_editor_runtime_requires_registered_agent(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    with pytest.raises(ValueError, match="is not registered"):
        store.upsert_terminal_editor_runtime(_editor_runtime(tmp_path))


def test_joplin_log_replaces_active_log_and_survives_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.start_joplin_log(
        agent_id="agent-1",
        project="demo",
        session_id="session-1",
        note_id="note-1",
        title="First",
    )
    second = store.start_joplin_log(
        agent_id="agent-1",
        project="demo",
        session_id="session-2",
        note_id="note-2",
        title="Second",
    )

    assert first["active"] is True
    assert store.get_joplin_log(str(first["log_id"]))["active"] is False
    assert store.get_active_joplin_log("agent-1") == second

    stopped = store.stop_active_joplin_log("agent-1")
    assert stopped is not None
    assert stopped["active"] is False
    assert stopped["stopped_at"] is not None
    assert store.stop_active_joplin_log("agent-1") is None

    reopened = Store(tmp_path / "pbx.sqlite")
    reopened.init()
    assert reopened.get_joplin_log(str(second["log_id"])) == stopped


def test_joplin_edit_reuse_conflict_and_rebase_contract(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.begin_joplin_edit(
        client_id="client-a",
        scope_key="project:demo",
        note=_note(),
        agent_id="agent-1",
        project="demo",
    )
    reused = store.begin_joplin_edit(
        client_id="client-a",
        scope_key="project:demo",
        note=_note(),
        agent_id="agent-1",
        project="demo",
    )
    other_client = store.begin_joplin_edit(
        client_id="client-b",
        scope_key="project:demo",
        note=_note(),
        agent_id="agent-1",
        project="demo",
    )
    assert reused["edit_id"] == first["edit_id"]
    assert other_client["edit_id"] != first["edit_id"]

    draft = store.update_joplin_edit_draft(
        str(first["edit_id"]),
        title="Local title",
        body="Local body",
    )
    assert draft is not None and draft["dirty"] is True

    conflict = store.record_joplin_conflict(
        edit_id=str(first["edit_id"]),
        current_note=_note(revision="rev-2", body="Remote body"),
        local_title="Local title",
        local_body="Local body",
        merged_title="Merged title",
        merged_body="Merged body",
    )
    assert conflict["status"] == "open"
    assert store.get_open_joplin_conflict_for_edit(str(first["edit_id"])) == conflict
    assert store.get_joplin_edit(str(first["edit_id"]))["status"] == "conflict"

    resolved = store.resolve_joplin_conflict(
        str(conflict["conflict_id"]),
        resolution="review_merge",
    )
    assert resolved is not None and resolved["status"] == "resolved"
    rebased = store.rebase_joplin_edit(
        str(first["edit_id"]),
        current_note=_note(revision="rev-2", body="Remote body"),
        draft_title="Merged title",
        draft_body="Merged body",
    )
    assert rebased is not None
    assert rebased["status"] == "editing"
    assert rebased["base_revision"] == "rev-2"
    assert rebased["draft_body"] == "Merged body"


def test_joplin_sync_coalescing_and_lane_status_survive_restart(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.enqueue_joplin_sync(
        reason="note_update",
        agent_id="agent-1",
        note_id="note-1",
        profile_key="profile-a",
    )
    coalesced = store.enqueue_joplin_sync(
        reason="note_update",
        agent_id="agent-1",
        note_id="note-2",
        profile_key="profile-a",
    )
    profile_validation = store.enqueue_joplin_sync(
        reason="manual",
        profile_key="profile-a",
    )
    assert coalesced["sync_id"] == first["sync_id"]
    assert coalesced["coalesced"] is True
    assert coalesced["generation"] == 2
    assert coalesced["note_ids"] == ["note-1", "note-2"]

    claimed_write = store.claim_next_joplin_sync_job()
    assert claimed_write is not None
    assert claimed_write["sync_id"] == first["sync_id"]
    store.complete_joplin_sync_job(str(first["sync_id"]), success=True)
    claimed_profile = store.claim_next_joplin_sync_job()
    assert claimed_profile is not None
    assert claimed_profile["sync_id"] == profile_validation["sync_id"]
    store.complete_joplin_sync_job(
        str(profile_validation["sync_id"]),
        success=False,
        error="profile locked",
        error_code="JOPLIN_E2EE_LOCKED",
    )

    status = store.joplin_sync_status()
    assert status["write_sync"]["current_status"] == "succeeded"
    assert status["profile_validation"]["current_status"] == "failed"

    reopened = Store(tmp_path / "pbx.sqlite")
    reopened.init()
    assert reopened.joplin_sync_status() == status


def test_joplin_sync_hydration_tolerates_legacy_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.enqueue_joplin_sync(reason="manual", profile_key="profile-a")
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE joplin_sync_jobs
            SET note_ids_json = 'not-json', reasons_json = '{}',
                progress_json = '[]'
            WHERE sync_id = ?
            """,
            (job["sync_id"],),
        )

    hydrated = store.get_joplin_sync_job(str(job["sync_id"]))
    assert hydrated is not None
    assert hydrated["note_ids"] == []
    assert hydrated["reasons"] == []
    assert hydrated["progress"] == {}
