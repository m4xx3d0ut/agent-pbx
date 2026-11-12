from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.operator import OperatorService
from agent_pbx.operator_kb_sync import (
    KB_BUNDLE_V2_SCHEMA,
    OperatorKbBundleV2Service,
    OperatorKbSyncError,
    canonical_json,
    logical_project_identity,
    sha256_bytes,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def registered_store(path: Path) -> Store:
    store = Store(path)
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


def active_entry(store: Store, *, body: str = "Reviewed guidance.") -> dict[str, object]:
    return store.create_operator_kb_entry(
        scope="project",
        project="demo",
        title="Durable guidance",
        summary="Use deterministic evidence.",
        body=body,
        tags=["evidence", "durable"],
        status="active",
        redaction_status="clean",
        created_by_operator_agent_id="operator-0",
        created_by_agent_id="operator-0",
        metadata={"promoted_by_operator_agent_id": "operator-0"},
    )


def test_bundle_v2_is_deterministic_and_strict(tmp_path: Path) -> None:
    store = registered_store(tmp_path / "source.sqlite")
    entry = active_entry(store)
    service = OperatorKbBundleV2Service(store)
    project = logical_project_identity(project="demo")

    first = service.export_bundle(entries=[entry], logical_project=project)
    second = service.export_bundle(entries=[entry], logical_project=project)

    assert first == second
    assert first["schema"] == KB_BUNDLE_V2_SCHEMA
    assert first["entries"][0]["revision"] == 1
    assert first["entries"][0]["origin_kb_id"] == entry["kb_id"]
    assert service.validate_bundle(first) == first

    tampered = json.loads(json.dumps(first))
    tampered["entries"][0]["entry"]["body"] = "changed"
    with pytest.raises(OperatorKbSyncError, match="content digest"):
        service.validate_bundle(tampered)

    extra = json.loads(json.dumps(first))
    extra["unexpected"] = True
    with pytest.raises(OperatorKbSyncError, match="fields"):
        service.validate_bundle(extra)


def test_bundle_v2_excludes_unclean_entry_content(tmp_path: Path) -> None:
    store = registered_store(tmp_path / "source.sqlite")
    clean = active_entry(store)
    excluded = store.create_operator_kb_entry(
        scope="project",
        project="demo",
        title="Unreviewed guidance",
        summary="Must stay local.",
        body="SECRET-SENTINEL-MUST-NOT-LEAVE",
        tags=["unreviewed"],
        status="proposed",
        redaction_status="unreviewed",
        created_by_operator_agent_id="operator-0",
        created_by_agent_id="operator-0",
    )
    operator = OperatorService(store)

    bundle = operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
    )
    encoded = canonical_json(bundle)

    assert len(bundle["entries"]) == 1
    assert bundle["entries"][0]["origin_kb_id"] == clean["kb_id"]
    assert bundle["redaction_exclusions"] == [
        {
            "kb_id": excluded["kb_id"],
            "reason": "entry redaction status is not clean",
        }
    ]
    assert b"SECRET-SENTINEL-MUST-NOT-LEAVE" not in encoded

    tree = operator.prepare_kb_bundle_v2_git_tree(
        operator_agent_id="operator-0",
        bundle=bundle,
    )
    tree_bytes = canonical_json(tree)
    assert b"SECRET-SENTINEL-MUST-NOT-LEAVE" not in tree_bytes
    manifest = json.loads(tree["files"]["kb/manifest.json"])
    assert manifest["redaction_exclusions"] == bundle["redaction_exclusions"]

    duplicate = json.loads(json.dumps(bundle))
    duplicate["redaction_exclusions"].append(
        dict(duplicate["redaction_exclusions"][0])
    )
    unsigned = {
        key: value for key, value in duplicate.items() if key != "bundle_sha256"
    }
    duplicate["bundle_sha256"] = sha256_bytes(canonical_json(unsigned))
    with pytest.raises(OperatorKbSyncError, match="not unique"):
        OperatorKbBundleV2Service.validate_bundle(duplicate)


def test_import_is_proposed_idempotent_and_linear(tmp_path: Path) -> None:
    source = registered_store(tmp_path / "source.sqlite")
    source_entry = active_entry(source)
    source_operator = OperatorService(source)
    first = source_operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
    )

    source_preview = source_operator.preview_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    assert source_preview["counts"]["adopt"] == 1
    adopted = source_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    assert adopted["applied_count"] == 1
    assert source.get_operator_kb_entry(str(source_entry["kb_id"]))["status"] == "active"

    target = registered_store(tmp_path / "target.sqlite")
    target_operator = OperatorService(target)
    preview = target_operator.preview_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    assert preview["counts"]["create"] == 1
    imported = target_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    assert imported["applied_count"] == 1
    sync = target.list_operator_kb_sync_records(
        logical_project_id=first["logical_project"]["logical_project_id"]
    )[0]
    target_entry = target.get_operator_kb_entry(sync["local_kb_id"])
    assert target_entry is not None
    assert target_entry["status"] == "proposed"

    repeated = target_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    assert repeated["applied_count"] == 0
    assert repeated["unchanged_count"] == 1

    source.update_operator_kb_entry(
        str(source_entry["kb_id"]),
        summary="Use deterministic evidence and revisions.",
        event_type="test_update",
        operator_agent_id="operator-0",
        event_summary="Update",
    )
    changed_entry = source.get_operator_kb_entry(str(source_entry["kb_id"]))
    second = source_operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
        parent_bundle_sha256=first["bundle_sha256"],
    )
    assert changed_entry is not None
    assert second["entries"][0]["revision"] == 2
    assert second["entries"][0]["parent_content_hash"] == first["entries"][0][
        "content_hash"
    ]
    updated = target_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=second,
    )
    assert updated["applied"][0]["action"] == "update"
    target_entry = target.get_operator_kb_entry(sync["local_kb_id"])
    assert target_entry is not None
    assert target_entry["status"] == "proposed"
    assert target_entry["summary"] == "Use deterministic evidence and revisions."


def test_conflict_and_tombstone_fail_closed(tmp_path: Path) -> None:
    source = registered_store(tmp_path / "source.sqlite")
    entry = active_entry(source)
    source_operator = OperatorService(source)
    first = source_operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
    )
    source_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )
    target = registered_store(tmp_path / "target.sqlite")
    target_operator = OperatorService(target)
    target_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=first,
    )

    conflict = json.loads(json.dumps(first))
    conflict["entries"][0]["revision"] = 2
    conflict["entries"][0]["parent_content_hash"] = "f" * 64
    unsigned = {key: value for key, value in conflict.items() if key != "bundle_sha256"}
    conflict["bundle_sha256"] = sha256_bytes(canonical_json(unsigned))
    preview = target_operator.preview_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=conflict,
    )
    assert preview["counts"]["conflict"] == 1
    with pytest.raises(OperatorKbSyncError, match="unresolved conflicts"):
        target_operator.import_kb_bundle_v2(
            operator_agent_id="operator-0",
            bundle=conflict,
        )

    source.update_operator_kb_entry(
        str(entry["kb_id"]),
        status="retired",
        event_type="test_retire",
        operator_agent_id="operator-0",
        event_summary="Retire",
    )
    tombstone = source_operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
        parent_bundle_sha256=first["bundle_sha256"],
    )
    assert tombstone["entries"][0]["tombstone"] is True
    result = target_operator.import_kb_bundle_v2(
        operator_agent_id="operator-0",
        bundle=tombstone,
    )
    assert result["applied"][0]["action"] == "tombstone"


def test_git_tree_is_deterministic_and_credential_free(tmp_path: Path) -> None:
    store = registered_store(tmp_path / "source.sqlite")
    operator = OperatorService(store)
    active_entry(store)
    bundle = operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
    )

    first = operator.prepare_kb_bundle_v2_git_tree(
        operator_agent_id="operator-0",
        bundle=bundle,
    )
    second = operator.prepare_kb_bundle_v2_git_tree(
        operator_agent_id="operator-0",
        bundle=bundle,
    )

    assert first == second
    assert first["branch_namespace"].startswith("refs/heads/pbx-kb-sync/")
    assert first["ambient_credentials_allowed"] is False
    assert first["daemon_git_execution"] is False
    assert sorted(first["files"])[0] == "kb/entries/" + bundle["entries"][0][
        "stable_id"
    ] + ".json"


def test_offline_validator_reports_metadata_only(tmp_path: Path) -> None:
    store = registered_store(tmp_path / "source.sqlite")
    operator = OperatorService(store)
    active_entry(store)
    bundle = operator.export_kb_bundle_v2(
        operator_agent_id="operator-0",
        project="demo",
    )
    path = tmp_path / "bundle.json"
    path.write_bytes(canonical_json(bundle))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_operator_kb_bundle.py",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": "/usr/bin:/bin",
        },
    )
    output = json.loads(result.stdout)
    assert output == {
        "valid": True,
        "schema": KB_BUNDLE_V2_SCHEMA,
        "bundle_sha256": bundle["bundle_sha256"],
        "logical_project_id": bundle["logical_project"]["logical_project_id"],
        "entry_count": 1,
    }
    assert "Reviewed guidance" not in result.stdout


def test_bundle_v2_api_is_default_disabled_and_root_controlled(tmp_path: Path) -> None:
    disabled = TestClient(create_app(ServerConfig(db_path=tmp_path / "disabled.sqlite")))
    assert disabled.post(
        "/v1/operator/kb/bundle-v2/export",
        json={"operator_agent_id": "operator-0", "project": "demo"},
    ).status_code == 409

    client = TestClient(
        create_app(
            ServerConfig(
                db_path=tmp_path / "enabled.sqlite",
                operator_kb_bundle_v2_enabled=True,
            )
        )
    )
    registered = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "operator_role": "root"},
        },
    )
    assert registered.status_code == 200
    proposed = client.post(
        "/v1/operator/kb",
        json={
            "operator_agent_id": "operator-0",
            "scope": "project",
            "project": "demo",
            "title": "Guidance",
            "summary": "Summary",
            "body": "Body",
        },
    )
    assert proposed.status_code == 200
    promoted = client.post(
        f"/v1/operator/kb/{proposed.json()['kb_id']}/promote",
        json={"operator_agent_id": "operator-0"},
    )
    assert promoted.status_code == 200
    exported = client.post(
        "/v1/operator/kb/bundle-v2/export",
        json={"operator_agent_id": "operator-0", "project": "demo"},
    )
    assert exported.status_code == 200
    bundle = exported.json()
    preview = client.post(
        "/v1/operator/kb/bundle-v2/preview",
        json={"operator_agent_id": "operator-0", "bundle": bundle},
    )
    assert preview.status_code == 200
    assert preview.json()["counts"]["adopt"] == 1
    imported = client.post(
        "/v1/operator/kb/bundle-v2/import",
        json={"operator_agent_id": "operator-0", "bundle": bundle},
    )
    assert imported.status_code == 200
    tree = client.post(
        "/v1/operator/kb/bundle-v2/git-tree",
        json={"operator_agent_id": "operator-0", "bundle": bundle},
    )
    assert tree.status_code == 200
    assert tree.json()["daemon_git_execution"] is False
