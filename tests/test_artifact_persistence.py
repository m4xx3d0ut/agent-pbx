from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def artifact_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "operator_role": "root"},
        )
    )
    return store


def trash_result(
    trash_id: str,
    *,
    path: str,
    created_at: float,
) -> dict[str, object]:
    return {
        "trash_id": trash_id,
        "path": path,
        "trash_path": f"/repo/.agent-pbx-trash/{trash_id}/payload",
        "kind": "file",
        "git_state": "untracked",
        "sha256": f"sha256-{trash_id}",
        "size": 17,
        "item_count": 1,
        "actor": "operator-0",
        "created_at": created_at,
        "expires_at": created_at + 86400,
        "manifest": {
            "original_path": path,
            "kind": "file",
            "git_state": "untracked",
            "actor": "operator-0",
        },
    }


def helper_bundle(store: Store, helper_id: str = "helper-1") -> dict[str, object]:
    return store.create_durable_helper_bundle(
        helper_id=helper_id,
        name="release-audit",
        object_sha256=f"sha256-{helper_id}",
        object_bytes=128,
        source_path="/repo/tools/release_audit.py",
        manifest={"runtime": "python3-isolated", "entrypoint": "main"},
        provenance={"project": "agent-pbx", "commit": "abc123"},
        scopes=["workspace.read", "evidence.render"],
        kb_refs=["kb-release-process"],
        status="proposed",
        created_by_operator_agent_id="operator-0",
    )


def test_file_trash_record_restore_prune_and_undo_contract(tmp_path: Path) -> None:
    store = artifact_store(tmp_path)
    first = store.record_file_trash_entry(
        agent_id="operator-0",
        project="agent-pbx",
        root_path="/repo",
        result=trash_result("trash-1", path="notes/one.md", created_at=100.0),
    )
    second = store.record_file_trash_entry(
        agent_id="operator-0",
        project="agent-pbx",
        root_path="/repo",
        result=trash_result("trash-2", path="notes/two.md", created_at=200.0),
    )

    assert first["manifest"]["original_path"] == "notes/one.md"
    assert second["status"] == "trashed"
    assert [
        entry["trash_id"] for entry in store.list_file_trash_entries("operator-0")
    ] == ["trash-2", "trash-1"]

    restored = store.mark_file_trash_restored("trash-1")
    assert restored is not None
    assert restored["status"] == "restored"
    assert restored["restored_at"] is not None
    assert store.mark_file_trash_restored("trash-1") is None

    batch = store.record_file_trash_prune_batch(
        agent_id="operator-0",
        preview_token="preview-1",
        result={
            "batch_id": "batch-1",
            "trash_ids": ["trash-2"],
            "eligible": 1,
        },
    )
    assert batch["status"] == "applied"
    assert batch["snapshot"]["trash_ids"] == ["trash-2"]
    assert store.get_file_trash_entry("trash-2")["status"] == "pruned"

    undone = store.mark_file_trash_prune_undone("batch-1")
    assert undone is not None
    assert undone["status"] == "undone"
    assert store.get_file_trash_entry("trash-2")["status"] == "trashed"
    assert store.mark_file_trash_prune_undone("batch-1") is None


def test_file_trash_prune_batch_rolls_back_when_entry_update_fails(
    tmp_path: Path,
) -> None:
    store = artifact_store(tmp_path)
    store.record_file_trash_entry(
        agent_id="operator-0",
        project="agent-pbx",
        root_path="/repo",
        result=trash_result("trash-1", path="notes/one.md", created_at=100.0),
    )
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_file_trash_prune
            BEFORE UPDATE OF status ON file_trash_entries
            WHEN NEW.status = 'pruned'
            BEGIN
                SELECT RAISE(ABORT, 'forced file trash prune failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced file trash prune failure"):
        store.record_file_trash_prune_batch(
            agent_id="operator-0",
            preview_token="preview-1",
            result={"batch_id": "batch-1", "trash_ids": ["trash-1"]},
        )

    assert store.get_file_trash_prune_batch("batch-1") is None
    assert store.get_file_trash_entry("trash-1")["status"] == "trashed"


def test_durable_helper_create_update_query_and_event_contract(tmp_path: Path) -> None:
    store = artifact_store(tmp_path)
    created = helper_bundle(store)

    assert created["manifest"]["runtime"] == "python3-isolated"
    assert created["provenance"]["commit"] == "abc123"
    assert created["scopes"] == ["workspace.read", "evidence.render"]
    assert created["kb_refs"] == ["kb-release-process"]
    assert store.find_durable_helper_bundle(
        name="release-audit",
        object_sha256="sha256-helper-1",
    )["helper_id"] == "helper-1"
    assert [item["helper_id"] for item in store.list_durable_helper_bundles()] == [
        "helper-1"
    ]

    updated = store.update_durable_helper_bundle(
        "helper-1",
        status="reviewed",
        validation_receipt={"passed": True, "executed_helper_code": False},
        reviewed_by_operator_agent_id="operator-0",
        event_type="reviewed",
        operator_agent_id="operator-0",
        event_metadata={"review": "manual"},
    )
    assert updated is not None
    assert updated["status"] == "reviewed"
    assert updated["validation_receipt"]["passed"] is True
    assert updated["reviewed_by_operator_agent_id"] == "operator-0"
    assert [
        event["event_type"]
        for event in store.list_durable_helper_events(helper_id="helper-1")
    ] == ["proposed", "reviewed"]
    assert store.list_durable_helper_events(helper_id="helper-1")[-1]["metadata"] == {
        "review": "manual"
    }
    assert store.update_durable_helper_bundle(
        "missing",
        status="reviewed",
        event_type="reviewed",
        operator_agent_id="operator-0",
    ) is None


def test_durable_helper_bundle_and_event_writes_roll_back_together(
    tmp_path: Path,
) -> None:
    store = artifact_store(tmp_path)
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_durable_helper_event
            BEFORE INSERT ON durable_helper_events
            BEGIN
                SELECT RAISE(ABORT, 'forced durable helper event failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced durable helper event failure"):
        helper_bundle(store)
    assert store.get_durable_helper_bundle("helper-1") is None

    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_durable_helper_event")
    helper_bundle(store)
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_durable_helper_review_event
            BEFORE INSERT ON durable_helper_events
            WHEN NEW.event_type = 'reviewed'
            BEGIN
                SELECT RAISE(ABORT, 'forced durable helper review failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced durable helper review failure"):
        store.update_durable_helper_bundle(
            "helper-1",
            status="reviewed",
            validation_receipt={"passed": True},
            reviewed_by_operator_agent_id="operator-0",
            event_type="reviewed",
            operator_agent_id="operator-0",
        )

    persisted = store.get_durable_helper_bundle("helper-1")
    assert persisted is not None
    assert persisted["status"] == "proposed"
    assert persisted["validation_receipt"] is None
    assert [
        event["event_type"]
        for event in store.list_durable_helper_events(helper_id="helper-1")
    ] == ["proposed"]
