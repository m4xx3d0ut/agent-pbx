from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_gateway import (
    WORKERBEE_MUTATING_TOOLS,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WorkerBeeGatewayAuthMiddleware,
    WorkerBeeGatewayError,
    WorkerBeeGatewayProxy,
    WorkerBeeGatewayService,
)
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_policy import WorkerBeeWorkspacePolicyService
from agent_pbx.workerbee_workspace import WorkerBeeWorkspaceService
from agent_pbx.store import Store


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


def platform_services(tmp_path: Path, *, binding_refreshed=None, failing_stage=False):
    runtime = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    selected = runtime.observe(
        {
            "backend": "platform_debug",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "K1S WorkerBee Debug",
            "server_version": "1.30.0",
        }
    )
    source = tmp_path / "repo"
    git_repo(source)

    def runner(command, **_kwargs):  # type: ignore[no-untyped-def]
        if failing_stage and (source / "FAIL_STAGE").exists():
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="stage failed")
        binding_id = command[command.index("--binding-id") + 1]
        commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(source), "status", "--porcelain=v1", "-z"],
                check=True,
                capture_output=True,
            ).stdout
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "backend": "platform_debug",
                    "runtime_workspace_path": f"/workspace/imports/{binding_id}",
                    "content_sha256": hashlib.sha256(binding_id.encode()).hexdigest(),
                    "source_commit": commit,
                    "source_dirty": dirty,
                    "receipt_path": f"/private/{binding_id}.json",
                }
            ),
            stderr="",
        )

    workspace = WorkerBeeWorkspaceService(
        tmp_path / "workspaces",
        helper_bin="/helper",
        runner=runner,
    )
    binding = workspace.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
    )
    gateway = WorkerBeeGatewayService(
        tmp_path / "gateway",
        runtime,
        workspace,
        binding_refreshed=binding_refreshed,
    )
    return runtime, workspace, source, binding, gateway


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


def test_gateway_session_start_refreshes_platform_snapshot_on_same_lease(
    tmp_path: Path,
) -> None:
    refreshes: list[tuple[str, str]] = []

    def record(previous, refreshed, _binding):  # type: ignore[no-untyped-def]
        refreshes.append(
            (previous.workspace_binding_id, refreshed.workspace_binding_id)
        )

    _runtime, _workspace, source, binding, gateway = platform_services(
        tmp_path,
        binding_refreshed=record,
    )
    lease, token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    (source / "README.md").write_text("refreshed\n", encoding="utf-8")

    refreshed, refreshed_binding, changed = gateway.refresh_workspace(lease)

    assert changed is True
    assert refreshed.lease_id == lease.lease_id
    assert refreshed.token_hash == lease.token_hash
    assert refreshed.workspace_binding_id != binding.binding_id
    assert refreshed_binding.binding_id == refreshed.workspace_binding_id
    assert gateway.validate(lease.lease_id, token) == refreshed
    assert gateway.prepare_arguments(
        refreshed,
        "workerbee_v1_session_start",
        {"goal": "deploy r3"},
    )["workspace_path"] == refreshed_binding.runtime_workspace_path
    assert refreshes == [(binding.binding_id, refreshed_binding.binding_id)]


def test_gateway_workspace_refresh_is_noop_when_source_is_unchanged(
    tmp_path: Path,
) -> None:
    _runtime, _workspace, _source, binding, gateway = platform_services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    refreshed, refreshed_binding, changed = gateway.refresh_workspace(lease)

    assert changed is False
    assert refreshed == lease
    assert refreshed_binding == binding


def test_gateway_rejects_stale_platform_mutation_until_session_refresh(
    tmp_path: Path,
) -> None:
    _runtime, _workspace, source, binding, gateway = platform_services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    (source / "README.md").write_text("pending deployment\n", encoding="utf-8")

    with pytest.raises(WorkerBeeGatewayError) as stale:
        gateway.require_current_workspace(lease)

    assert stale.value.code == "WORKERBEE_WORKSPACE_REFRESH_REQUIRED"
    assert "workerbee_v1_session_start" in stale.value.remediation


def test_gateway_failed_platform_refresh_preserves_prior_lease(
    tmp_path: Path,
) -> None:
    _runtime, _workspace, source, binding, gateway = platform_services(
        tmp_path,
        failing_stage=True,
    )
    lease, token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    (source / "README.md").write_text("pending deployment\n", encoding="utf-8")
    (source / "FAIL_STAGE").write_text("1\n", encoding="utf-8")

    with pytest.raises(WorkerBeeGatewayError) as failure:
        gateway.refresh_workspace(lease)

    assert failure.value.code == "WORKERBEE_WORKSPACE_STAGE_FAILED"
    assert gateway.validate(lease.lease_id, token) == lease


@pytest.mark.parametrize(
    "profile",
    [WORKERBEE_PROFILE_OBSERVE, WORKERBEE_PROFILE_REVIEW],
)
def test_gateway_read_only_profiles_do_not_refresh_platform_snapshot(
    tmp_path: Path,
    profile: str,
) -> None:
    _runtime, _workspace, source, binding, gateway = platform_services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=profile,
        workspace_binding_id=binding.binding_id,
    )
    (source / "README.md").write_text("changed\n", encoding="utf-8")

    refreshed, refreshed_binding, changed = gateway.refresh_workspace(lease)

    assert changed is False
    assert refreshed == lease
    assert refreshed_binding == binding


@pytest.mark.asyncio
async def test_gateway_proxy_session_start_uses_refreshed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, _workspace, source, binding, gateway = platform_services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    (source / "README.md").write_text("r3\n", encoding="utf-8")
    calls: list[tuple[str, dict[str, object]]] = []

    @asynccontextmanager
    async def fake_transport(endpoint):  # type: ignore[no-untyped-def]
        assert endpoint == "http://127.0.0.1:8765/mcp"
        yield object(), object(), None

    class FakeSession:
        def __init__(self, _read, _write):  # type: ignore[no-untyped-def]
            pass

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        async def initialize(self) -> None:
            return None

        async def call_tool(self, name, arguments, **_kwargs):  # type: ignore[no-untyped-def]
            calls.append((name, arguments))
            return object()

    monkeypatch.setattr(
        "agent_pbx.workerbee_gateway.streamablehttp_client",
        fake_transport,
    )
    monkeypatch.setattr(
        "agent_pbx.workerbee_gateway.ClientSession",
        FakeSession,
    )

    await WorkerBeeGatewayProxy(gateway).call_tool(
        lease,
        "workerbee_v1_session_start",
        {"goal": "deploy r3"},
    )

    refreshed = gateway.load(lease.lease_id)
    assert refreshed.workspace_binding_id != binding.binding_id
    refreshed_binding = gateway.workspace_service.load(
        refreshed.workspace_binding_id
    )
    assert calls == [
        (
            "workerbee_v1_session_start",
            refreshed_binding.session_start_args(goal="deploy r3"),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "root_path", "expected_rewritten"),
    [
        ("/abc123", "", "/"),
        ("/workerbee-mcp/abc123", "/workerbee-mcp", "/workerbee-mcp/"),
    ],
)
async def test_gateway_auth_resolves_mounted_lease_path_and_rewrites_transport_root(
    path: str,
    root_path: str,
    expected_rewritten: str,
) -> None:
    validated: list[tuple[str, str]] = []
    downstream_scopes: list[dict[str, object]] = []

    class Service:
        @staticmethod
        def validate(lease_id: str, token: str) -> None:
            validated.append((lease_id, token))

    async def downstream(scope, _receive, _send):
        downstream_scopes.append(dict(scope))

    middleware = WorkerBeeGatewayAuthMiddleware(downstream, Service())  # type: ignore[arg-type]
    scope = {
        "type": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "headers": [(b"authorization", b"Bearer test-token")],
    }

    await middleware(scope, None, None)  # type: ignore[arg-type]

    assert validated == [("abc123", "test-token")]
    assert downstream_scopes[0]["path"] == expected_rewritten
    assert downstream_scopes[0]["raw_path"] == expected_rewritten.encode()


def test_gateway_auth_rejects_missing_or_extra_route_segments() -> None:
    assert WorkerBeeGatewayAuthMiddleware._route_lease_id(  # noqa: SLF001
        {"path": "/workerbee-mcp", "root_path": "/workerbee-mcp"}  # type: ignore[arg-type]
    ) == ""
    assert WorkerBeeGatewayAuthMiddleware._route_lease_id(  # noqa: SLF001
        {
            "path": "/workerbee-mcp/abc123/extra",
            "root_path": "/workerbee-mcp",
        }  # type: ignore[arg-type]
    ) == ""


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


def test_gateway_automatic_scoped_workbench_transition_keeps_lease(
    tmp_path: Path,
) -> None:
    runtime, workspace, binding, _gateway = services(tmp_path)
    target_repo = tmp_path / "git" / "other"
    target_repo.parent.mkdir()
    git_repo(target_repo)
    nested = target_repo / "service"
    nested.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    policy = WorkerBeeWorkspacePolicyService(store, environ={})
    policy.set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="automatic",
        allowed_roots=[str(tmp_path)],
        managed_scratch_root=None,
        profile_label="Workbench",
        updated_by="test",
    )
    gateway = WorkerBeeGatewayService(
        tmp_path / "gateway-policy",
        runtime,
        workspace,
        workspace_policy=policy,
    )
    lease, token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    moved, moved_binding, changed = gateway.transition_workspace(
        lease,
        {"cwd": str(nested)},
    )

    assert changed is True
    assert moved.lease_id == lease.lease_id
    assert moved.token_hash == lease.token_hash
    assert moved.workspace_binding_id == moved_binding.binding_id
    assert moved_binding.source_root == str(target_repo.resolve())
    assert moved.project == "other"
    assert moved.working_subpath == "service"
    assert gateway.validate(lease.lease_id, token) == moved
    prepared = gateway.prepare_arguments(
        moved, "workerbee_v1_session_start", {"goal": "test other"}
    )
    assert prepared["cwd"] == str(target_repo.resolve() / "service")
    assert prepared["project"] == "other"


def test_gateway_approval_transition_is_pending_until_controller_applies(
    tmp_path: Path,
) -> None:
    runtime, workspace, binding, _gateway = services(tmp_path)
    target_repo = tmp_path / "other"
    git_repo(target_repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    policy = WorkerBeeWorkspacePolicyService(store, environ={})
    policy.set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="approval",
        allowed_roots=[str(tmp_path)],
        managed_scratch_root=None,
        profile_label="Safe",
        updated_by="test",
    )
    gateway = WorkerBeeGatewayService(
        tmp_path / "gateway-policy",
        runtime,
        workspace,
        workspace_policy=policy,
    )
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_OBSERVE,
        workspace_binding_id=binding.binding_id,
    )

    with pytest.raises(WorkerBeeGatewayError) as approval:
        gateway.transition_workspace(lease, {"cwd": str(target_repo)})
    assert approval.value.code == "WORKERBEE_WORKSPACE_APPROVAL_REQUIRED"
    pending = policy.list_transitions("demo-agent")
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert gateway.load(lease.lease_id).workspace_binding_id == binding.binding_id
    with pytest.raises(WorkerBeeGatewayError) as repeated:
        gateway.transition_workspace(lease, {"cwd": str(target_repo)})
    assert repeated.value.code == "WORKERBEE_WORKSPACE_APPROVAL_REQUIRED"
    assert len(policy.list_transitions("demo-agent")) == 1

    moved, moved_binding, changed = gateway.apply_transition(
        pending[0]["transition_id"], approved_by="controller"
    )
    assert changed is True
    assert moved.workspace_binding_id == moved_binding.binding_id
    assert policy.get_transition(pending[0]["transition_id"])["status"] == "applied"
    # Workspace mobility does not broaden the observe-only tool profile.
    assert "workerbee_v1_manifest_deploy_local" not in moved.allowed_tools


def test_gateway_disabled_agent_request_still_allows_explicit_controller_bind(
    tmp_path: Path,
) -> None:
    runtime, workspace, binding, _gateway = services(tmp_path)
    target_repo = tmp_path / "other"
    git_repo(target_repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    policy = WorkerBeeWorkspacePolicyService(store, environ={})
    policy.set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="disabled",
        allowed_roots=[str(tmp_path)],
        managed_scratch_root=None,
        profile_label="Controller only",
        updated_by="test",
    )
    gateway = WorkerBeeGatewayService(
        tmp_path / "gateway-policy",
        runtime,
        workspace,
        workspace_policy=policy,
    )
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_OBSERVE,
        workspace_binding_id=binding.binding_id,
    )

    with pytest.raises(WorkerBeeGatewayError) as disabled:
        gateway.transition_workspace(lease, {"cwd": str(target_repo)})
    assert disabled.value.code == "WORKERBEE_WORKSPACE_REQUEST_DISABLED"

    moved, _binding, changed = gateway.transition_workspace(
        lease,
        {"cwd": str(target_repo)},
        controller_approved=True,
        requested_by="controller",
    )
    assert changed is True
    assert moved.project == "other"


def test_gateway_transition_revert_restores_prior_binding(tmp_path: Path) -> None:
    runtime, workspace, binding, _gateway = services(tmp_path)
    target_repo = tmp_path / "other"
    git_repo(target_repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    policy = WorkerBeeWorkspacePolicyService(store, environ={})
    policy.set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="automatic",
        allowed_roots=[str(tmp_path)],
        managed_scratch_root=None,
        profile_label="Workbench",
        updated_by="test",
    )
    gateway = WorkerBeeGatewayService(
        tmp_path / "gateway-policy",
        runtime,
        workspace,
        workspace_policy=policy,
    )
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_REVIEW,
        workspace_binding_id=binding.binding_id,
    )
    moved, _moved_binding, _changed = gateway.transition_workspace(
        lease, {"cwd": str(target_repo)}
    )
    transition = policy.list_transitions("demo-agent")[0]

    restored, restored_binding = gateway.revert_transition(
        transition["transition_id"], reverted_by="controller"
    )

    assert restored.workspace_binding_id == binding.binding_id
    assert restored_binding == binding
    assert restored.profile == WORKERBEE_PROFILE_REVIEW
    assert policy.get_transition(transition["transition_id"])["status"] == "reverted"


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


def test_gateway_tool_surface_rejects_missing_and_quarantines_unknown_without_silent_drop(
    tmp_path: Path,
) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    with pytest.raises(WorkerBeeGatewayError) as missing:
        gateway.resolve_tool_surface(lease, list(lease.requested_tools[:-1]))
    assert missing.value.code == "WORKERBEE_TOOL_SURFACE_INCOMPATIBLE"
    assert "missing=" in missing.value.message

    effective = gateway.resolve_tool_surface(
        lease,
        [*lease.requested_tools, "workerbee_v1_backend_extension"],
    )
    assert "workerbee_v1_backend_extension" not in effective
    assert set(effective) == set(lease.requested_tools)
    with pytest.raises(WorkerBeeGatewayError) as denied:
        gateway.prepare_arguments(lease, "workerbee_v1_backend_extension", {})
    assert denied.value.code == "WORKERBEE_TOOL_NOT_ALLOWED"


def test_gateway_revalidates_trusted_direct_bind_before_source_consuming_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, workspace, binding, gateway = services(tmp_path)
    direct_binding = replace(
        binding,
        source_mode="trusted-direct-bind",
        workspace_strategy="trusted_direct_bind",
    )
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    direct_lease = replace(lease, source_mode="trusted-direct-bind")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(workspace, "load", lambda _binding_id: direct_binding)
    monkeypatch.setattr(
        workspace,
        "revalidate_trusted_direct",
        lambda selected, runtime: calls.append((selected.binding_id, runtime.backend)),
    )

    gateway.prepare_arguments(
        direct_lease,
        "workerbee_v1_project_status",
        {},
    )
    gateway.prepare_arguments(
        direct_lease,
        "workerbee_v1_capabilities",
        {},
    )

    assert calls == [(binding.binding_id, "oss")]


def test_gateway_local_deploy_allows_scoped_restart_only_for_mutating_profile(
    tmp_path: Path,
) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    local_lease, _token = gateway.issue(
        agent_id="deploy-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )
    observe_lease, _token = gateway.issue(
        agent_id="observe-agent",
        codex_session_id="thread-2",
        profile=WORKERBEE_PROFILE_OBSERVE,
        workspace_binding_id=binding.binding_id,
    )

    restart_args = gateway.prepare_arguments(
        local_lease,
        "workerbee_v1_workload_restart",
        {"app": "web"},
    )
    assert restart_args == {"app": "web", "project": "demo"}
    assert "workerbee_v1_workload_restart" in WORKERBEE_MUTATING_TOOLS

    with pytest.raises(WorkerBeeGatewayError) as denied:
        gateway.prepare_arguments(
            observe_lease,
            "workerbee_v1_workload_restart",
            {"app": "web"},
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


@pytest.mark.asyncio
async def test_gateway_tracks_workload_restart_as_mutation(tmp_path: Path) -> None:
    _runtime, _workspace, binding, gateway = services(tmp_path)
    lease, _token = gateway.issue(
        agent_id="demo-agent",
        codex_session_id="thread-1",
        profile=WORKERBEE_PROFILE_LOCAL_DEPLOY,
        workspace_binding_id=binding.binding_id,
    )

    async with gateway.mutation(lease, "workerbee_v1_workload_restart"):
        assert gateway.impact()["mutation_count"] == 1
    assert gateway.impact()["mutation_count"] == 0


def test_gateway_api_prepares_workspace_and_issues_one_time_token(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    git_repo(repo)
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "oss",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee",
                    "server_version": "1.30.0",
                    "config_path": "/tmp/selector.env",
                }
            }

    app.state.workerbee_switcher = Selector()
    with TestClient(app) as client:
        registered = client.post(
            "/v1/agents/register",
            json={
                "agent_id": "demo-agent",
                "project": "demo",
                "metadata": {
                    "cwd": str(repo),
                    "workerbee_mcp_profile": "local-deploy",
                },
            },
        )
        assert registered.status_code == 200

        prepared = client.post(
            "/v2/agents/demo-agent/workerbee/workspaces",
            json={},
        )
        assert prepared.status_code == 200
        binding = prepared.json()
        assert binding["backend"] == "oss"
        assert binding["runtime_workspace_path"] == str(repo)

        issued = client.post(
            "/v2/agents/demo-agent/workerbee/leases",
            json={
                "codex_session_id": "thread-1",
                "profile": "local-deploy",
                "workspace_binding_id": binding["binding_id"],
            },
        )
        assert issued.status_code == 200
        lease_payload = issued.json()
        assert lease_payload["token"].startswith("apbx_")
        assert lease_payload["gateway_url"].endswith(
            f"/workerbee-mcp/{lease_payload['lease']['lease_id']}"
        )
        receipt = (
            tmp_path
            / "workerbee-runtime"
            / "gateway"
            / "leases"
            / f"{lease_payload['lease']['lease_id']}.json"
        ).read_text(encoding="utf-8")
        assert lease_payload["token"] not in receipt

        revoked = client.delete(
            f"/v2/agents/demo-agent/workerbee/leases/{lease_payload['lease']['lease_id']}"
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"


def test_gateway_api_manages_workspace_policy_and_controller_transition(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "git" / "first"
    target = tmp_path / "git" / "second"
    repo.parent.mkdir()
    git_repo(repo)
    git_repo(target)
    nested = target / "service"
    nested.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "oss",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee",
                    "server_version": "1.30.0",
                }
            }

    app.state.workerbee_switcher = Selector()
    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "demo-agent",
                "project": "demo",
                "metadata": {
                    "cwd": str(repo),
                    "workerbee_mcp_profile": "local-deploy",
                },
            },
        ).status_code == 200
        binding = client.post(
            "/v2/agents/demo-agent/workerbee/workspaces", json={}
        ).json()
        issued = client.post(
            "/v2/agents/demo-agent/workerbee/leases",
            json={
                "codex_session_id": "thread-1",
                "profile": "local-deploy",
                "workspace_binding_id": binding["binding_id"],
            },
        )
        assert issued.status_code == 200

        saved = client.put(
            "/v2/agents/demo-agent/workerbee/workspace-policy",
            json={
                "policy": "scoped-workbench",
                "request_mode": "automatic",
                "allowed_roots": [str(tmp_path / "git")],
                "managed_scratch_root": str(tmp_path / "git" / ".scratch"),
                "profile_label": "Test Workbench",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["effective"]["profile_label"] == "Test Workbench"

        preview = client.post(
            "/v2/agents/demo-agent/workerbee/workspace-transitions/preview",
            json={"cwd": str(nested)},
        )
        assert preview.status_code == 200
        assert preview.json()["target"]["working_subpath"] == "service"
        applied = client.post(
            "/v2/agents/demo-agent/workerbee/workspace-transitions",
            json={"cwd": str(nested)},
        )
        assert applied.status_code == 200
        assert applied.json()["changed"] is True
        assert applied.json()["lease"]["project"] == "second"
        transitions = client.get(
            "/v2/agents/demo-agent/workerbee/workspace-transitions"
        )
        assert transitions.status_code == 200
        assert transitions.json()[0]["status"] == "applied"

        reverted = client.post(
            "/v2/agents/demo-agent/workerbee/workspace-transitions/"
            f"{transitions.json()[0]['transition_id']}/revert"
        )
        assert reverted.status_code == 200
        assert reverted.json()["lease"]["workspace_binding_id"] == binding["binding_id"]


def test_gateway_api_manages_backend_aware_snapshot_policy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git_repo(repo)
    (repo / "site-output").mkdir()
    (repo / "site-output" / "index.html").write_text("demo\n", encoding="utf-8")
    (repo / "site-output" / "app.css").write_text("body{}\n", encoding="utf-8")
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "platform_debug",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee Debug",
                    "server_version": "1.30.0",
                }
            }

    app.state.workerbee_switcher = Selector()
    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "demo-agent",
                "project": "demo",
                "metadata": {"cwd": str(repo)},
            },
        ).status_code == 200

        initial = client.get(
            "/v2/agents/demo-agent/workerbee/snapshot-policy"
        )
        assert initial.status_code == 200
        assert initial.json()["applicable"] is True
        assert initial.json()["effective"]["policy"] == "tracked"

        saved = client.put(
            "/v2/agents/demo-agent/workerbee/snapshot-policy",
            json={
                "source_root": str(repo),
                "policy": "safe-working-tree",
                "include_paths": [],
                "profile_label": "Trusted test",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["effective"]["policy"] == "safe-working-tree"

        preview = client.get(
            "/v2/agents/demo-agent/workerbee/snapshot-policy/preview"
        )
        assert preview.status_code == 200
        assert preview.json()["selection"]["include_paths"] == ["site-output"]

        inherited = client.delete(
            "/v2/agents/demo-agent/workerbee/snapshot-policy"
        )
        assert inherited.status_code == 200
        assert inherited.json()["effective"]["policy"] == "tracked"


def test_gateway_api_requires_explicit_access_profile_and_restart(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git_repo(repo)
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.workerbee_runtime.observe(
        {
            "backend": "oss",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "WorkerBee OSS",
            "server_version": "2.3.0rc2",
        }
    )
    catalog_fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "workerbee_tool_catalogs_rc2.json").read_text(
            encoding="utf-8"
        )
    )

    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "demo-agent",
                "project": "demo",
                "metadata": {"cwd": str(repo)},
            },
        ).status_code == 200

        initial = client.get(
            "/v2/agents/demo-agent/workerbee/access-profile"
        )
        assert initial.status_code == 200
        assert initial.json()["effective_profile"] == "local-deploy"
        assert initial.json()["source_mode"] == "snapshot-import"
        assert initial.json()["selected_backend"] == "oss"
        contract = initial.json()["tool_surface_contract"]
        assert set(contract["required_tools"]).issubset(catalog_fixture["oss_tools"])
        assert contract["reviewed_optional_tools"] == []
        assert contract["unknown_backend_tool_policy"] == "quarantine-deny-and-warn"

        saved = client.put(
            "/v2/agents/demo-agent/workerbee/access-profile",
            json={"profile": "local-deploy"},
        )
        assert saved.status_code == 200
        assert saved.json()["effective_profile"] == "local-deploy"
        assert saved.json()["requires_restart_on_change"] is True

        agent = app.state.store.get_agent("demo-agent")
        assert agent is not None
        assert agent["metadata"]["workerbee_mcp_profile"] == "local-deploy"

        app.state.workerbee_runtime.observe(
            {
                "backend": "platform_debug",
                "endpoint": "http://127.0.0.1:9876/mcp",
                "server_name": "WorkerBee Platform Debug",
                "server_version": "2.3.0rc2",
            }
        )
        platform_readback = client.get(
            "/v2/agents/demo-agent/workerbee/access-profile"
        )
        assert platform_readback.status_code == 200
        assert platform_readback.json()["selected_backend"] == "platform_debug"
        assert platform_readback.json()["tool_surface_contract"][
            "reviewed_optional_tools"
        ] == catalog_fixture["platform_debug_reviewed_optional_tools"]


@pytest.mark.parametrize(
    "profile",
    [WORKERBEE_PROFILE_OBSERVE, WORKERBEE_PROFILE_REVIEW],
)
def test_gateway_api_allows_pre_registration_read_only_launch_candidates(
    tmp_path: Path,
    profile: str,
) -> None:
    repo = tmp_path / "repo"
    git_repo(repo)
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "oss",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee",
                    "server_version": "1.30.0",
                    "config_path": "/tmp/selector.env",
                }
            }

    app.state.workerbee_switcher = Selector()
    with TestClient(app) as client:
        prepared = client.post(
            "/v2/agents/new-launch/workerbee/workspaces",
            json={"project": "demo", "source_root": str(repo)},
        )
        assert prepared.status_code == 200

        issued = client.post(
            "/v2/agents/new-launch/workerbee/leases",
            json={
                "profile": profile,
                "workspace_binding_id": prepared.json()["binding_id"],
            },
        )
        assert issued.status_code == 200
        assert issued.json()["lease"]["profile"] == profile


def test_gateway_api_rejects_pre_registration_local_deploy_candidate(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    git_repo(repo)
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "oss",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee",
                    "server_version": "1.30.0",
                }
            }

    app.state.workerbee_switcher = Selector()
    with TestClient(app) as client:
        prepared = client.post(
            "/v2/agents/new-launch/workerbee/workspaces",
            json={"project": "demo", "source_root": str(repo)},
        )
        assert prepared.status_code == 200

        issued = client.post(
            "/v2/agents/new-launch/workerbee/leases",
            json={
                "profile": WORKERBEE_PROFILE_LOCAL_DEPLOY,
                "workspace_binding_id": prepared.json()["binding_id"],
            },
        )
        assert issued.status_code == 409
        assert "not approved" in issued.json()["detail"]


def test_selected_appliance_status_requires_binding_without_host_cli_fallback(
    tmp_path: Path,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "platform_debug",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee Debug",
                    "server_version": "1.30.0",
                }
            }

    class HostCliMustNotRun:
        def status_for_agent(self, _agent):  # type: ignore[no-untyped-def]
            raise AssertionError("host OSS CLI status must not be merged into appliance status")

    app.state.workerbee_switcher = Selector()
    app.state.workerbee = HostCliMustNotRun()
    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "debug-agent",
                "project": "demo",
                "metadata": {"cwd": str(tmp_path)},
            },
        ).status_code == 200

        response = client.get("/v1/agents/debug-agent/workerbee")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status_source"] == "selected_mcp"
        assert payload["selected_runtime"]["backend"] == "platform_debug"
        assert payload["error"]["code"] == "WORKERBEE_WORKSPACE_BINDING_REQUIRED"


def test_selected_appliance_status_uses_bound_selected_mcp(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    calls: list[tuple[str, str, dict[str, object]]] = []

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "platform_debug",
                    "endpoint": "http://127.0.0.1:8765/mcp",
                    "server_name": "K1S WorkerBee Debug",
                    "server_version": "1.30.0",
                }
            }

    class Binding:
        project = "demo"

        def public_dict(self):  # type: ignore[no-untyped-def]
            return {
                "binding_id": "wb-binding-refreshed",
                "project": self.project,
                "backend": "platform_debug",
                "runtime_workspace_path": "/workspace/imports/wb-binding-refreshed",
            }

    class Workspaces:
        def load(self, binding_id):  # type: ignore[no-untyped-def]
            assert binding_id == "wb-binding-refreshed"
            return Binding()

        def verify_runtime(self, _binding, runtime):  # type: ignore[no-untyped-def]
            assert runtime.backend == "platform_debug"

    class Gateway:
        def impact(self):  # type: ignore[no-untyped-def]
            return {"active_leases": [], "active_mutations": {}, "mutation_count": 0}

        def load(self, lease_id):  # type: ignore[no-untyped-def]
            assert lease_id == "lease-1"

            class Lease:
                agent_id = "debug-agent"
                status = "active"
                revoked_at = None
                workspace_binding_id = "wb-binding-refreshed"

            return Lease()

    class HostCliMustNotRun:
        def status_for_agent(self, _agent):  # type: ignore[no-untyped-def]
            raise AssertionError("host OSS CLI status must not be merged into appliance status")

    async def selected_call(endpoint, tool, arguments):  # type: ignore[no-untyped-def]
        calls.append((endpoint, tool, arguments))
        return {
            "data": {
                "mode": "lazy",
                "project_status": {
                    "running": False,
                    "status_kind": "ready",
                    "state_dir": "/var/lib/workerbee/demo",
                },
            }
        }

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", selected_call)
    app.state.workerbee_switcher = Selector()
    app.state.workerbee_workspaces = Workspaces()
    app.state.workerbee_gateway = Gateway()
    app.state.workerbee = HostCliMustNotRun()
    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "debug-agent",
                "project": "demo",
                "metadata": {
                    "cwd": str(tmp_path),
                    "workerbee_workspace_binding_id": "wb-binding-old",
                    "workerbee_gateway_lease_id": "lease-1",
                },
            },
        ).status_code == 200

        response = client.get("/v1/agents/debug-agent/workerbee")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is True
        assert payload["status_source"] == "selected_mcp"
        assert payload["workspace_binding"]["runtime_workspace_path"] == (
            "/workspace/imports/wb-binding-refreshed"
        )
        assert calls == [
            (
                "http://127.0.0.1:8765/mcp",
                "workerbee_v1_project_status",
                {"project": "demo"},
            )
        ]
