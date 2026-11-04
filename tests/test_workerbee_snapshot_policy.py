from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agent_pbx.store import Store
from agent_pbx.workerbee_snapshot_policy import (
    WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
    WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
    WORKERBEE_SNAPSHOT_POLICY_TRACKED,
    WORKERBEE_WORKFLOW_TRUSTED_PLATFORM,
    WorkerBeeSnapshotPolicyError,
    WorkerBeeSnapshotPolicyService,
    workerbee_workflow_preset_values,
)


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path


def service(tmp_path: Path, *, environ: dict[str, str] | None = None) -> WorkerBeeSnapshotPolicyService:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    return WorkerBeeSnapshotPolicyService(store, environ=environ or {})


def test_snapshot_policy_defaults_to_tracked_and_is_not_applicable_to_oss(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repo")
    policy = service(tmp_path)

    view = policy.get_policy("agent-1", source_root=repo, backend="oss")
    selection = policy.resolve_selection(
        agent_id="agent-1", source_root=repo, backend="oss"
    )

    assert view["effective"]["policy"] == WORKERBEE_SNAPSHOT_POLICY_TRACKED
    assert view["applicable"] is False
    assert selection.policy == "direct-host"
    assert selection.include_paths == ()


def test_snapshot_policy_stores_exact_agent_root_and_respects_admin_ceiling(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "git" / "repo")
    constrained = service(
        tmp_path,
        environ={
            "AGENT_PBX_WORKERBEE_SNAPSHOT_POLICY_CEILING": "approved-paths",
            "AGENT_PBX_WORKERBEE_SNAPSHOT_ROOT_CEILING_JSON": json.dumps(
                [str(tmp_path / "git")]
            ),
        },
    )

    with pytest.raises(WorkerBeeSnapshotPolicyError) as error:
        constrained.set_policy(
            agent_id="agent-1",
            source_root=repo,
            policy=WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
            include_paths=[],
            max_bytes=None,
            max_files=None,
            max_file_bytes=None,
            profile_label="Too broad",
            updated_by="test",
        )
    assert error.value.code == "WORKERBEE_SNAPSHOT_POLICY_CEILING"

    view = constrained.set_policy(
        agent_id="agent-1",
        source_root=repo,
        policy=WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
        include_paths=["site-output"],
        max_bytes=1024,
        max_files=10,
        max_file_bytes=512,
        profile_label="Project output",
        updated_by="test",
    )
    assert view["effective"]["include_paths"] == ["site-output"]
    assert view["effective"]["source_root"] == str(repo.resolve())


def test_safe_working_tree_selects_generated_root_and_excludes_sensitive_bulk(
    tmp_path: Path,
) -> None:
    repo = git_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ignore local"], check=True)
    (repo / "site-output").mkdir()
    (repo / "site-output" / "index.html").write_text("hello\n", encoding="utf-8")
    (repo / "site-output" / "app.css").write_text("body{}\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "index.js").write_text("x\n", encoding="utf-8")

    policy = service(tmp_path)
    policy.set_policy(
        agent_id="agent-1",
        source_root=repo,
        policy=WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
        include_paths=[],
        max_bytes=None,
        max_files=None,
        max_file_bytes=None,
        profile_label="Trusted platform",
        updated_by="test",
    )

    selection = policy.resolve_selection(
        agent_id="agent-1", source_root=repo, backend="platform_debug"
    )

    assert selection.include_paths == ("site-output",)
    assert selection.file_count == 2
    assert any(".env (sensitive-name)" == item for item in selection.excluded_paths)
    assert any("node_modules" in item and "bulk-cache" in item for item in selection.excluded_paths)


def test_legacy_include_policy_remains_effective_during_migration(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    generated = repo / "generated"
    generated.mkdir()
    (generated / "manifest.yaml").write_text("kind: List\n", encoding="utf-8")
    policy = service(
        tmp_path,
        environ={
            "AGENT_PBX_WORKERBEE_WORKSPACE_INCLUDES_JSON": json.dumps(
                {
                    "agent-1": {
                        "source_root": str(repo),
                        "include_paths": ["generated"],
                    }
                }
            )
        },
    )

    selection = policy.resolve_selection(
        agent_id="agent-1", source_root=repo, backend="platform_debug"
    )

    assert selection.include_paths == ("generated",)
    assert selection.compatibility_include_count == 1


def test_tracked_policy_does_not_retain_stored_approved_paths(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    generated = repo / "generated"
    generated.mkdir()
    (generated / "manifest.yaml").write_text("kind: List\n", encoding="utf-8")
    policy = service(tmp_path)
    policy.set_policy(
        agent_id="agent-1",
        source_root=repo,
        policy=WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
        include_paths=["generated"],
        max_bytes=None,
        max_files=None,
        max_file_bytes=None,
        profile_label="Generated content",
        updated_by="test",
    )
    policy.set_policy(
        agent_id="agent-1",
        source_root=repo,
        policy=WORKERBEE_SNAPSHOT_POLICY_TRACKED,
        include_paths=["generated"],
        max_bytes=None,
        max_files=None,
        max_file_bytes=None,
        profile_label="Tracked only",
        updated_by="test",
    )

    selection = policy.resolve_selection(
        agent_id="agent-1", source_root=repo, backend="platform_debug"
    )

    assert selection.include_paths == ()
    assert selection.file_count == 0


def test_trusted_platform_preset_is_backend_neutral_and_scoped(tmp_path: Path) -> None:
    root = tmp_path / "git"
    root.mkdir()

    values = workerbee_workflow_preset_values(
        WORKERBEE_WORKFLOW_TRUSTED_PLATFORM,
        project_roots=[root],
    )

    assert values["AGENT_PBX_WORKERBEE_WORKSPACE_DEFAULT_POLICY"] == "scoped-workbench"
    assert values["AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE"] == "automatic"
    assert values["AGENT_PBX_WORKERBEE_SNAPSHOT_DEFAULT_POLICY"] == "safe-working-tree"
    assert values["AGENT_PBX_OPERATOR_AUTHORITY_DEFAULT_PROFILE"] == "scoped-edit"
    assert values["AGENT_PBX_OPERATOR_AUTHORITY_PROFILE_CEILING"] == "trusted-workbench"
    assert values["AGENT_PBX_OPERATOR_AUTHORITY_ROOT_CEILING_JSON"] == f'["{root}"]'
    assert "AGENT_PBX_WORKERBEE_BACKEND" not in values
    assert "local-deploy" not in values.values()
