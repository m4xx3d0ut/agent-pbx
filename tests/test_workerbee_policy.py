from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_pbx.store import Store
from agent_pbx.workerbee_policy import (
    WORKERBEE_WORKSPACE_CONTROL_MODE_ENV,
    WORKERBEE_WORKSPACE_POLICY_CEILING_ENV,
    WORKERBEE_WORKSPACE_ROOT_CEILING_ENV,
    WorkerBeeWorkspacePolicyError,
    WorkerBeeWorkspacePolicyService,
)
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_workspace import WorkerBeeWorkspaceService


def git_repo(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True
    )
    (root / "README.md").write_text("workspace\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def binding_for(tmp_path: Path, source: Path):
    runtime = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    selected = runtime.observe(
        {
            "backend": "oss",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "K1S WorkerBee",
            "server_version": "1.30.0",
        }
    )
    return WorkerBeeWorkspaceService(tmp_path / "workspaces").prepare(
        runtime=selected,
        project="demo",
        source_root=source,
    )


def test_workspace_policy_defaults_to_pinned_approval(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    view = WorkerBeeWorkspacePolicyService(store, environ={}).get_policy("agent-1")

    assert view["effective"]["policy"] == "pinned"
    assert view["effective"]["request_mode"] == "approval"
    assert view["effective"]["profile_label"] == "Safe default"
    assert view["admin"]["control_mode"] == "editable"


def test_scoped_workbench_resolves_git_root_and_subpath(tmp_path: Path) -> None:
    first = tmp_path / "git" / "first"
    second = tmp_path / "git" / "second"
    git_repo(first)
    git_repo(second)
    nested = second / "src" / "feature"
    nested.mkdir(parents=True)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = WorkerBeeWorkspacePolicyService(store, environ={})
    service.set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="automatic",
        allowed_roots=[str(tmp_path / "git")],
        managed_scratch_root=str(tmp_path / "git" / ".agent-pbx-workbench"),
        profile_label="Rosie Workbench",
        updated_by="test",
    )

    target = service.resolve_target(
        agent_id="agent-1",
        binding=binding_for(tmp_path, first),
        candidate_cwd=str(nested),
        requested_project=None,
    )

    assert target.source_root == str(second.resolve())
    assert target.working_subpath == "src/feature"
    assert target.base_project == "second"
    assert target.runtime_project == "second"
    assert target.request_mode == "automatic"
    assert target.requires_transition is True


def test_same_repository_accepts_worktree_and_rejects_other_repo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    other = tmp_path / "other"
    worktree = tmp_path / "source-wt"
    git_repo(source)
    git_repo(other)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "feature", str(worktree)],
        cwd=source,
        check=True,
    )
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = WorkerBeeWorkspacePolicyService(store, environ={})
    service.set_policy(
        agent_id=None,
        policy="same-repository",
        request_mode="automatic",
        allowed_roots=[],
        managed_scratch_root=None,
        profile_label=None,
        updated_by="test",
    )
    binding = binding_for(tmp_path, source)

    assert service.resolve_target(
        agent_id="agent-1", binding=binding, candidate_cwd=str(worktree), requested_project=None
    ).source_root == str(worktree.resolve())
    with pytest.raises(WorkerBeeWorkspacePolicyError) as denied:
        service.resolve_target(
            agent_id="agent-1", binding=binding, candidate_cwd=str(other), requested_project=None
        )
    assert denied.value.code == "WORKERBEE_WORKSPACE_SCOPE_DENIED"


def test_admin_policy_and_root_ceilings_cannot_be_bypassed(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = WorkerBeeWorkspacePolicyService(
        store,
        environ={
            WORKERBEE_WORKSPACE_POLICY_CEILING_ENV: "same-repository",
            WORKERBEE_WORKSPACE_ROOT_CEILING_ENV: f'["{allowed}"]',
        },
    )

    with pytest.raises(WorkerBeeWorkspacePolicyError) as policy_error:
        service.set_policy(
            agent_id=None,
            policy="scoped-workbench",
            request_mode="automatic",
            allowed_roots=[str(allowed)],
            managed_scratch_root=None,
            profile_label=None,
            updated_by="test",
        )
    assert policy_error.value.code == "WORKERBEE_WORKSPACE_POLICY_CEILING"

    unrestricted = WorkerBeeWorkspacePolicyService(
        store,
        environ={WORKERBEE_WORKSPACE_ROOT_CEILING_ENV: f'["{allowed}"]'},
    )
    with pytest.raises(WorkerBeeWorkspacePolicyError) as root_error:
        unrestricted.set_policy(
            agent_id=None,
            policy="scoped-workbench",
            request_mode="automatic",
            allowed_roots=[str(outside)],
            managed_scratch_root=None,
            profile_label=None,
            updated_by="test",
        )
    assert root_error.value.code == "WORKERBEE_WORKSPACE_ROOT_CEILING"


def test_hidden_or_read_only_control_mode_blocks_policy_writes(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = WorkerBeeWorkspacePolicyService(
        store,
        environ={WORKERBEE_WORKSPACE_CONTROL_MODE_ENV: "hidden"},
    )
    with pytest.raises(WorkerBeeWorkspacePolicyError) as locked:
        service.set_policy(
            agent_id=None,
            policy="pinned",
            request_mode="approval",
            allowed_roots=[],
            managed_scratch_root=None,
            profile_label=None,
            updated_by="test",
        )
    assert locked.value.code == "WORKERBEE_WORKSPACE_POLICY_LOCKED"


def test_transition_audit_omits_host_source_path_from_event(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = WorkerBeeWorkspacePolicyService(store, environ={})
    binding = binding_for(tmp_path, source)
    target = service.resolve_target(
        agent_id="agent-1",
        binding=binding,
        candidate_cwd=str(source / "."),
        requested_project="test-scenario",
    )

    transition = service.create_transition(
        agent_id="agent-1",
        lease_id="lease-1",
        from_binding_id=binding.binding_id,
        target=target,
        status="pending",
        requested_by="agent-1",
    )

    assert transition["source_root"] == str(source.resolve())
    event = store.list_events(limit=1)[0]
    assert "source_root" not in event["payload"]
    assert event["payload"]["transition_id"] == transition["transition_id"]
