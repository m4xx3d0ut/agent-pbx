from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_gateway import (
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WorkerBeeGatewayAuthMiddleware,
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
                "binding_id": "wb-binding",
                "project": self.project,
                "backend": "platform_debug",
                "runtime_workspace_path": "/workspace/imports/wb-binding",
            }

    class Workspaces:
        def load(self, binding_id):  # type: ignore[no-untyped-def]
            assert binding_id == "wb-binding"
            return Binding()

        def verify_runtime(self, _binding, runtime):  # type: ignore[no-untyped-def]
            assert runtime.backend == "platform_debug"

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
    app.state.workerbee = HostCliMustNotRun()
    with TestClient(app) as client:
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": "debug-agent",
                "project": "demo",
                "metadata": {
                    "cwd": str(tmp_path),
                    "workerbee_workspace_binding_id": "wb-binding",
                },
            },
        ).status_code == 200

        response = client.get("/v1/agents/debug-agent/workerbee")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is True
        assert payload["status_source"] == "selected_mcp"
        assert payload["workspace_binding"]["runtime_workspace_path"] == (
            "/workspace/imports/wb-binding"
        )
        assert calls == [
            (
                "http://127.0.0.1:8765/mcp",
                "workerbee_v1_project_status",
                {"project": "demo"},
            )
        ]
