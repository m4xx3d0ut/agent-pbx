from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from agent_pbx.workerbee_gateway import (
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WorkerBeeGatewayError,
    WorkerBeeGatewayService,
)
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_workspace import WorkerBeeWorkspaceService


def git_repo(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    (root / "README.md").write_text("gateway\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def services(tmp_path: Path):
    runtime = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    selected = runtime.observe(
        {
            "backend": "oss",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "K1S WorkerBee",
            "server_version": "1.30.0",
        }
    )
    workspace = WorkerBeeWorkspaceService(tmp_path / "workspaces")
    source = tmp_path / "repo"
    git_repo(source)
    binding = workspace.prepare(runtime=selected, project="demo", source_root=source)
    gateway = WorkerBeeGatewayService(tmp_path / "gateway", runtime, workspace)
    return runtime, workspace, binding, gateway


def test_gateway_lease_is_private_and_scoped_to_binding(tmp_path: Path) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    assert token.startswith("apbx_")
    assert "token_hash" not in lease.public_dict()
    assert gateway.validate(lease.lease_id, token) == lease
    assert (tmp_path / "gateway" / "leases" / f"{lease.lease_id}.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(WorkerBeeGatewayError) as wrong_token:
        gateway.validate(lease.lease_id, "wrong")
    assert wrong_token.value.code == "WORKERBEE_GATEWAY_AUTH_FAILED"


def test_gateway_injects_exact_session_binding_and_project_scope(tmp_path: Path) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    arguments = gateway.prepare_arguments(
        lease,
        "workerbee_v1_session_start",
        {"cwd": "/wrong", "project": "demo", "goal": "deploy"},
    )
    assert arguments["cwd"] == binding.runtime_workspace_path
    assert arguments["project"] == "demo"

    status_args = gateway.prepare_arguments(lease, "workerbee_v1_project_status", {})
    assert status_args["project"] == "demo"
    with pytest.raises(WorkerBeeGatewayError) as scope_error:
        gateway.prepare_arguments(
            lease, "workerbee_v1_project_status", {"project": "other"}
        )
    assert scope_error.value.code == "WORKERBEE_PROJECT_SCOPE_MISMATCH"


def test_gateway_rejects_tools_outside_profile(tmp_path: Path) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    with pytest.raises(WorkerBeeGatewayError) as denied:
        gateway.prepare_arguments(
            lease, "workerbee_v1_manifest_deploy_remote_k1s", {}
        )
    assert denied.value.code == "WORKERBEE_TOOL_NOT_ALLOWED"


def test_gateway_generation_change_requires_rebind(tmp_path: Path) -> None:
    runtime, _workspace, binding, gateway = services(tmp_path)
    lease, token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    runtime.observe(
        {
            "backend": "oss",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "K1S WorkerBee",
            "server_version": "1.30.0",
        },
        force_generation=True,
    )

    with pytest.raises(WorkerBeeGatewayError) as stale:
        gateway.validate(lease.lease_id, token)
    assert stale.value.code == "WORKERBEE_BACKEND_REBIND_REQUIRED"


@pytest.mark.asyncio
async def test_gateway_tracks_active_mutations(tmp_path: Path) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    async with gateway.mutation(lease, "workerbee_v1_manifest_deploy_local"):
        assert gateway.impact()["mutation_count"] == 1
    assert gateway.impact()["mutation_count"] == 0
