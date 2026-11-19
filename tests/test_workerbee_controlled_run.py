from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.controller_metadata import controller_metadata_patch
from agent_pbx.operator import OperatorService
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import SCHEMA_VERSION, Store
from agent_pbx.workerbee_controlled_run import (
    CONTROLLED_RUN_REQUIRED_TOOLS,
    WorkerBeeControlledRunError,
    WorkerBeeControlledRunService,
    controlled_run_posture,
    load_controlled_run_profile,
)
from agent_pbx.workerbee_controlled_run_executor import WorkerBeeControlledRunExecutor
from agent_pbx.workerbee_cutover import WorkerBeeCutoverError
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry


PROFILE_SHA = "1" * 64
WORKSPACE_SHA = "2" * 64
BASELINE = "a" * 40
CANDIDATE = "b" * 40


def _write_profile(root: Path, *, enabled: bool = True, approval: str = "campaign_lease") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / "isolated-runtime-validation.toml"
    path.write_text(
        "\n".join(
            [
                'profile_id = "isolated-runtime-validation"',
                "version = 1",
                'label = "Rosie Isolated Validation"',
                f"enabled = {str(enabled).lower()}",
                'backend = "workerbee-standard-workspace"',
                'required_authority = "trusted-workbench"',
                'allowed_workerbee_backends = ["platform_debug"]',
                'allowed_projects = ["k1s-oss-v1"]',
                'source_mode = "immutable-commit-export"',
                'network_mode = "private-loopback"',
                'external_egress = "deny"',
                'credentials = "none"',
                'recipe_id = "k1s-rc1-runtime-parity"',
                f'tool_manifest_sha256 = "{PROFILE_SHA}"',
                "max_seconds = 1800",
                "max_memory_mib = 4096",
                "max_output_bytes = 16777216",
                "max_concurrency = 1",
                f'approval_mode = "{approval}"',
                "lease_max_uses = 4",
                "lease_ttl_seconds = 7200",
                "retention_seconds = 604800",
                "cleanup_required = true",
                "allow_host_fallback = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _service(tmp_path: Path, *, mode: str = "root_operator"):
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    assert store.schema_version() == SCHEMA_VERSION == 46
    root_metadata = {
        "cwd": str(tmp_path),
        "operator_role": "root",
        "logical_operator_id": "operator-8",
        "operator_authority_activated_profile": "trusted-workbench",
        "operator_authority_activated_workspace_set_digest": WORKSPACE_SHA,
    }
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-8",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata=root_metadata,
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-k1s",
            project="k1s-oss-v1",
            metadata={"cwd": str(tmp_path), "codex_session_id": "caller-session"},
        )
    )
    fork = OperatorService(store).ensure_fork(
        operator_agent_id="operator-8",
        source_caller_agent_id="caller-k1s",
        fork_agent_id="operator-8-fork",
        fork_track_id="default",
        fork_purpose="edit",
        access_mode="edit",
        source_cwd=str(tmp_path),
        work_root=str(tmp_path),
        fork_codex_session_id="fork-session",
        status="running",
        metadata={
            "operator_authority_activated_profile": "trusted-workbench",
            "operator_authority_activated_workspace_set_digest": WORKSPACE_SHA,
            "workerbee_mcp_profile": "local-deploy",
            "workerbee_binding_state": "ready",
            "workerbee_workspace_binding_id": "wb-binding-8",
            "workerbee_backend_generation": 1,
            "workerbee_workspace_project": "k1s-oss-v1",
        },
    )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-8",
        title="K1s RC1 controlled validation",
        objective="Compare immutable baseline and candidate",
        criteria=["no external egress"],
        assignments=[
            {
                "target_agent_id": "caller-k1s",
                "title": "Run comparison",
                "prompt": "Run the exact recipe.",
                "criteria": [],
            }
        ],
    )
    profiles = tmp_path / "profiles"
    _write_profile(profiles)
    registry = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    selection = registry.observe(
        {
            "backend": "platform_debug",
            "endpoint": "http://workerbee.invalid/mcp",
            "server_name": "workerbee-platform-debug",
            "server_version": "0.1.7",
            "config_path": str(tmp_path / "debug.json"),
        }
    )
    assert selection.generation == 1
    service = WorkerBeeControlledRunService(
        store,
        registry,
        environ={
            "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MODE": mode,
            "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_PROFILE_ROOT": str(profiles),
            "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MAX_IN_FLIGHT": "2",
        },
    )
    return store, service, str(fork["fork_agent_id"]), str(campaign["campaign_id"])


def test_controlled_run_posture_is_guided_and_host_fallback_off_by_default() -> None:
    posture = controlled_run_posture({})
    assert posture.mode == "guided"
    assert posture.host_fallback == "disabled"
    assert posture.max_in_flight_per_root == 1
    assert posture.root_operator_enabled is False


def test_read_only_admin_control_blocks_new_requests_and_decisions(tmp_path: Path) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    service.environ["AGENT_PBX_WORKERBEE_CONTROLLED_RUN_CONTROL_MODE"] = "read_only"
    with pytest.raises(WorkerBeeControlledRunError) as error:
        service.request(
            root_operator_agent_id="operator-8",
            target_fork_agent_id=fork_id,
            campaign_id=campaign_id,
            profile_id="isolated-runtime-validation",
            baseline_commit=BASELINE,
            candidate_commit=CANDIDATE,
            requested_by="test",
        )
    assert error.value.code == "WORKERBEE_CONTROLLED_RUN_CONTROL_READ_ONLY"
    assert store.list_workerbee_controlled_run_requests() == []


def test_profile_is_private_fail_closed_and_digest_bound(tmp_path: Path) -> None:
    path = _write_profile(tmp_path)
    profile = load_controlled_run_profile(path)
    assert profile.external_egress == "deny"
    assert profile.credentials == "none"
    assert profile.network_mode == "private-loopback"
    assert profile.cleanup_required is True
    assert len(profile.sha256) == 64

    path.chmod(0o666)
    with pytest.raises(WorkerBeeControlledRunError) as error:
        load_controlled_run_profile(path)
    assert error.value.code == "WORKERBEE_CONTROLLED_RUN_PROFILE_PERMISSIONS"


def test_preflight_binds_exact_runtime_workspace_profile_and_commits(tmp_path: Path) -> None:
    _store, service, fork_id, campaign_id = _service(tmp_path)
    result = service.preflight(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
    )
    assert result["backend"] == "platform_debug"
    assert result["backend_generation"] == 1
    assert result["workspace_binding_id"] == "wb-binding-8"
    assert result["workspace_set_digest"] == WORKSPACE_SHA
    assert result["baseline_commit"] == BASELINE
    assert result["candidate_commit"] == CANDIDATE
    assert result["required_capabilities"] == list(CONTROLLED_RUN_REQUIRED_TOOLS)
    assert result["host_fallback_available"] is False


def test_request_lease_decision_and_receipt_are_exact_and_durable(tmp_path: Path) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test-controller",
        reason="RC1 parity",
    )
    request = requested["request"]
    assert requested["created"] is True
    assert request["status"] == "pending"
    reused = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test-controller",
    )
    assert reused["created"] is False
    assert reused["request"]["request_id"] == request["request_id"]

    lease = service.request_lease(request["request_id"], requested_by="test-controller")
    lease = store.decide_workerbee_controlled_run_lease(
        lease["lease_id"], approve=True, decided_by="test-controller"
    )
    assert lease["status"] == "approved"
    approved = service.decide(
        request["request_id"],
        approve=True,
        decided_by="test-controller",
        lease_id=lease["lease_id"],
    )
    assert approved["status"] == "approved"
    assert approved["lease_id"] == lease["lease_id"]
    assert store.get_workerbee_controlled_run_lease(lease["lease_id"])["use_count"] == 1

    claimed = store.claim_workerbee_controlled_run_request(
        request["request_id"], executor_id="daemon:test"
    )
    assert claimed["status"] == "claimed"
    states = [
        ("provisioning", ["claimed"]),
        ("canary", ["provisioning"]),
        ("running", ["canary"]),
        ("collecting", ["running"]),
        ("cleaning", ["collecting"]),
        ("succeeded", ["cleaning"]),
    ]
    for state, expected in states:
        claimed = store.transition_workerbee_controlled_run_request(
            request["request_id"],
            executor_id="daemon:test",
            status=state,
            expected_statuses=expected,
            workerbee_run_id="wb-run-1",
            workerbee_workspace_id="wb-workspace-1",
            result={"state": state},
        )
    assert claimed["status"] == "succeeded"
    completed = store.record_workerbee_controlled_run_receipt(
        request["request_id"],
        outcome="succeeded",
        backend_run_id="wb-run-1",
        workspace_id="wb-workspace-1",
        evidence={"canary": "passed"},
        cleanup={"complete": True, "residue": []},
    )
    assert completed["receipt"]["cleanup"] == {"complete": True, "residue": []}


def test_pending_controlled_run_is_exposed_in_root_operator_workflow_summary(
    tmp_path: Path,
) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    request = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    operator = next(
        item for item in store.list_agents() if item["agent_id"] == "operator-8"
    )
    assert operator["pending_workerbee_controlled_run_count"] == 1
    assert operator["oldest_workerbee_controlled_run_request_id"] == request["request_id"]
    assert operator["workflow_label"] == "! WB RUN"


def test_approved_controlled_run_blocks_backend_switch_and_binds_fingerprint(
    tmp_path: Path,
) -> None:
    _store, service, fork_id, campaign_id = _service(tmp_path)
    preflight = service.preflight(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
    )
    assert preflight["backend_fingerprint"] == preflight["runtime"]["fingerprint"]
    request = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    assert service.impact()["mutation_count"] == 0
    service.decide(request["request_id"], approve=True, decided_by="test")
    impact = service.impact()
    assert impact["mutation_count"] == 1
    assert impact["active_controlled_runs"][0]["request_id"] == request["request_id"]


def test_review_or_untrusted_fork_cannot_request_controlled_run(tmp_path: Path) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    fork = store.get_operator_fork_for_agent(fork_id)
    assert fork is not None
    store.update_operator_fork(
        str(fork["operator_fork_id"]),
        access_mode="review_readonly",
        fork_purpose="review",
    )
    with pytest.raises(WorkerBeeControlledRunError) as error:
        service.preflight(
            root_operator_agent_id="operator-8",
            target_fork_agent_id=fork_id,
            campaign_id=campaign_id,
            profile_id="isolated-runtime-validation",
            baseline_commit=BASELINE,
            candidate_commit=CANDIDATE,
        )
    assert error.value.code == "WORKERBEE_CONTROLLED_RUN_EDIT_FORK_REQUIRED"


def test_lease_cannot_approve_drifted_source_request(tmp_path: Path) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    first = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    lease = service.request_lease(first["request_id"], requested_by="test")
    store.decide_workerbee_controlled_run_lease(
        lease["lease_id"], approve=True, decided_by="test"
    )
    second = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit="c" * 40,
        requested_by="test",
    )["request"]
    with pytest.raises(ValueError, match="does not match"):
        service.decide(
            second["request_id"],
            approve=True,
            decided_by="test",
            lease_id=lease["lease_id"],
        )


def _api_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    profiles = tmp_path / "profiles"
    _write_profile(profiles)
    monkeypatch.setenv(
        "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_PROFILE_ROOT", str(profiles)
    )
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MODE", "root_operator")
    app = create_app(ServerConfig(db_path=tmp_path / "api.sqlite"))

    class Selector:
        def status(self):  # type: ignore[no-untyped-def]
            return {
                "active": {
                    "ready": True,
                    "backend": "platform_debug",
                    "endpoint": "http://workerbee.invalid/mcp",
                    "server_name": "WorkerBee Debug",
                    "server_version": "0.1.7",
                    "config_path": str(tmp_path / "debug.json"),
                }
            }

    app.state.workerbee_switcher = Selector()
    store = app.state.store
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-8",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "cwd": str(tmp_path),
                "operator_role": "root",
                "logical_operator_id": "operator-8",
                "operator_authority_activated_profile": "trusted-workbench",
                "operator_authority_activated_workspace_set_digest": WORKSPACE_SHA,
            },
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-k1s",
            project="k1s-oss-v1",
            metadata={"cwd": str(tmp_path), "codex_session_id": "caller-session"},
        )
    )
    fork = OperatorService(store).ensure_fork(
        operator_agent_id="operator-8",
        source_caller_agent_id="caller-k1s",
        fork_agent_id="operator-8-fork",
        fork_track_id="default",
        fork_purpose="edit",
        access_mode="edit",
        source_cwd=str(tmp_path),
        work_root=str(tmp_path),
        fork_codex_session_id="fork-session",
        status="running",
        metadata={
            "operator_authority_activated_profile": "trusted-workbench",
            "operator_authority_activated_workspace_set_digest": WORKSPACE_SHA,
            "workerbee_mcp_profile": "local-deploy",
            "workerbee_binding_state": "ready",
            "workerbee_workspace_binding_id": "wb-binding-8",
            "workerbee_backend_generation": 1,
            "workerbee_workspace_project": "k1s-oss-v1",
        },
    )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-8",
        title="API controlled run",
        objective="Test API",
        criteria=[],
        assignments=[
            {
                "target_agent_id": "caller-k1s",
                "title": "Test",
                "prompt": "Test",
                "criteria": [],
            }
        ],
    )
    return app, str(fork["fork_agent_id"]), str(campaign["campaign_id"])


def _api_payload(fork_id: str, campaign_id: str) -> dict[str, str]:
    return {
        "root_operator_agent_id": "operator-8",
        "target_fork_agent_id": fork_id,
        "campaign_id": campaign_id,
        "profile_id": "isolated-runtime-validation",
        "baseline_commit": BASELINE,
        "candidate_commit": CANDIDATE,
    }


def test_api_preflight_reports_missing_appliance_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)

    async def capability_call(  # type: ignore[no-untyped-def]
        _endpoint,
        tool_name,
        _arguments,
        **_kwargs,
    ):
        assert tool_name == "workerbee_v1_capabilities"
        return {"data": {"workerbee_version": "0.1.7", "mcp_tools": ["workerbee_v1_capabilities"]}}

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", capability_call)
    with TestClient(app) as client:
        preflight = client.post(
            "/v2/workerbee/controlled-runs/preflight",
            json=_api_payload(fork_id, campaign_id),
        )
        assert preflight.status_code == 200
        assert preflight.json()["ok"] is False
        assert preflight.json()["missing_capabilities"] == list(
            CONTROLLED_RUN_REQUIRED_TOOLS
        )
        create = client.post(
            "/v2/workerbee/controlled-runs",
            json=_api_payload(fork_id, campaign_id),
        )
        assert create.status_code == 409
        assert create.json()["detail"]["code"] == (
            "WORKERBEE_CONTROLLED_RUN_CAPABILITY_MISSING"
        )


def test_api_creates_request_only_after_observed_appliance_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)

    async def capability_call(_endpoint, tool_name, _arguments, **_kwargs):  # type: ignore[no-untyped-def]
        if tool_name == "workerbee_v1_runtime_policy_status":
            return {"ok": True, "data": {"profile": "linux-strict-workload"}}
        return {
            "data": {
                "workerbee_version": "0.1.8",
                "mcp_tools": [
                    "workerbee_v1_capabilities",
                    "workerbee_v1_runtime_policy_status",
                    *CONTROLLED_RUN_REQUIRED_TOOLS,
                ],
            }
        }

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", capability_call)
    with TestClient(app) as client:
        response = client.post(
            "/v2/workerbee/controlled-runs",
            json={**_api_payload(fork_id, campaign_id), "reason": "RC1 parity"},
        )
        assert response.status_code == 200
        request = response.json()["request"]
        assert request["status"] == "pending"
        assert request["backend"] == "platform_debug"
        listed = client.get(
            "/v2/workerbee/controlled-runs",
            params={"root_operator_agent_id": "operator-8"},
        )
        assert listed.status_code == 200
        assert listed.json()["requests"][0]["request_id"] == request["request_id"]


def test_agent_response_preserves_controlled_run_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)

    async def capability_call(_endpoint, tool_name, _arguments, **_kwargs):  # type: ignore[no-untyped-def]
        if tool_name == "workerbee_v1_runtime_policy_status":
            return {"ok": True, "data": {"profile": "linux-strict-workload"}}
        return {
            "data": {
                "workerbee_version": "0.1.8",
                "mcp_tools": [
                    "workerbee_v1_capabilities",
                    "workerbee_v1_runtime_policy_status",
                    *CONTROLLED_RUN_REQUIRED_TOOLS,
                ],
            }
        }

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", capability_call)
    with TestClient(app) as client:
        created = client.post(
            "/v2/workerbee/controlled-runs",
            json=_api_payload(fork_id, campaign_id),
        )
        assert created.status_code == 200
        request = created.json()["request"]

        agents = client.get("/v1/agents")
        assert agents.status_code == 200
        operator = next(
            item for item in agents.json() if item["agent_id"] == "operator-8"
        )

    assert operator["pending_workerbee_controlled_run_count"] == 1
    assert operator["active_workerbee_controlled_run_count"] == 0
    assert (
        operator["oldest_workerbee_controlled_run_created_at"]
        == request["created_at"]
    )
    assert operator["oldest_workerbee_controlled_run_request_id"] == request["request_id"]


def test_api_approval_schedules_daemon_executor_and_hides_internal_transitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)
    scheduled: list[str] = []

    class Executor:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def schedule(self, request_id: str) -> bool:
            scheduled.append(request_id)
            return True

    app.state.workerbee_controlled_run_executor = Executor()

    async def capability_call(_endpoint, tool_name, _arguments, **_kwargs):  # type: ignore[no-untyped-def]
        if tool_name == "workerbee_v1_runtime_policy_status":
            return {"ok": True, "data": {"profile": "linux-strict-workload"}}
        return {
            "data": {
                "workerbee_version": "0.1.8",
                "mcp_tools": [
                    "workerbee_v1_capabilities",
                    "workerbee_v1_runtime_policy_status",
                    *CONTROLLED_RUN_REQUIRED_TOOLS,
                ],
            }
        }

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", capability_call)
    with TestClient(app) as client:
        created = client.post(
            "/v2/workerbee/controlled-runs",
            json=_api_payload(fork_id, campaign_id),
        )
        request_id = created.json()["request"]["request_id"]
        decision = client.post(
            f"/v2/workerbee/controlled-runs/{request_id}/decision",
            json={"approve": True, "decided_by": "test-controller"},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"
        assert scheduled == [request_id]
        assert client.post(
            f"/v2/workerbee/controlled-runs/{request_id}/claim",
            json={"executor_id": "external"},
        ).status_code == 404


@pytest.mark.asyncio
async def test_executor_uses_only_narrow_contract_and_requires_cleanup_receipt(
    tmp_path: Path,
) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    request = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    request = service.decide(request["request_id"], approve=True, decided_by="test")
    calls: list[tuple[str, dict[str, object]]] = []
    observed = ["running", "collecting", "cleaning", "succeeded"]

    async def fake_call(_endpoint, tool_name, arguments, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append((tool_name, dict(arguments)))
        if tool_name == "workerbee_v1_controlled_run_preflight":
            return {
                "data": {
                    "ok": True,
                    "request_sha256": arguments["request_sha256"],
                    "profile_sha256": arguments["profile_sha256"],
                    "tool_manifest_sha256": arguments["tool_manifest_sha256"],
                }
            }
        if tool_name == "workerbee_v1_controlled_run_start":
            return {"data": {"run_id": "wb-run-1", "workspace_id": "wb-space-1"}}
        if tool_name == "workerbee_v1_controlled_run_status":
            return {"data": {"status": observed.pop(0)}}
        if tool_name == "workerbee_v1_controlled_run_receipt":
            return {
                "data": {
                    "workspace_id": "wb-space-1",
                    "evidence": {"baseline": BASELINE, "candidate": CANDIDATE},
                    "cleanup": {"complete": True, "residue": []},
                }
            }
        raise AssertionError(tool_name)

    executor = WorkerBeeControlledRunExecutor(
        store,
        service,
        service.runtime_registry,
        tool_caller=fake_call,
        environ={"AGENT_PBX_WORKERBEE_CONTROLLED_RUN_POLL_SECONDS": "0.01"},
        executor_id="test-executor",
    )
    await executor._run(str(request["request_id"]))
    completed = store.get_workerbee_controlled_run_request(str(request["request_id"]))
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert completed["receipt"]["cleanup"] == {"complete": True, "residue": []}
    assert {name for name, _ in calls} <= set(CONTROLLED_RUN_REQUIRED_TOOLS)
    start = next(arguments for name, arguments in calls if name.endswith("_start"))
    assert start["profile"]["project"] == "k1s-oss-v1"
    assert "argv" not in start
    assert "environment" not in start


@pytest.mark.asyncio
async def test_executor_fails_closed_when_cleanup_receipt_is_incomplete(
    tmp_path: Path,
) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    request = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    service.decide(request["request_id"], approve=True, decided_by="test")

    async def fake_call(_endpoint, tool_name, arguments, **_kwargs):  # type: ignore[no-untyped-def]
        if tool_name.endswith("_preflight"):
            return {
                "data": {
                    "ok": True,
                    "request_sha256": arguments["request_sha256"],
                    "profile_sha256": arguments["profile_sha256"],
                    "tool_manifest_sha256": arguments["tool_manifest_sha256"],
                }
            }
        if tool_name.endswith("_start"):
            return {"data": {"run_id": "wb-run-dirty", "workspace_id": "dirty"}}
        if tool_name.endswith("_status"):
            return {"data": {"status": "succeeded"}}
        if tool_name.endswith("_receipt"):
            return {"data": {"cleanup": {"complete": False, "residue": ["namespace"]}}}
        raise AssertionError(tool_name)

    executor = WorkerBeeControlledRunExecutor(
        store,
        service,
        service.runtime_registry,
        tool_caller=fake_call,
        environ={"AGENT_PBX_WORKERBEE_CONTROLLED_RUN_POLL_SECONDS": "0.01"},
        executor_id="test-executor",
    )
    await executor._run(str(request["request_id"]))
    failed = store.get_workerbee_controlled_run_request(str(request["request_id"]))
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error_code"] == "WORKERBEE_CONTROLLED_RUN_CLEANUP_INCOMPLETE"
    assert failed["receipt"]["cleanup"]["residue"] == ["namespace"]


@pytest.mark.asyncio
async def test_executor_recovers_existing_appliance_run_without_starting_it_again(
    tmp_path: Path,
) -> None:
    store, service, fork_id, campaign_id = _service(tmp_path)
    request = service.request(
        root_operator_agent_id="operator-8",
        target_fork_agent_id=fork_id,
        campaign_id=campaign_id,
        profile_id="isolated-runtime-validation",
        baseline_commit=BASELINE,
        candidate_commit=CANDIDATE,
        requested_by="test",
    )["request"]
    service.decide(request["request_id"], approve=True, decided_by="test")
    store.claim_workerbee_controlled_run_request(
        request["request_id"], executor_id="dead-daemon", lease_seconds=10
    )
    store.transition_workerbee_controlled_run_request(
        request["request_id"],
        executor_id="dead-daemon",
        status="provisioning",
        expected_statuses=["claimed"],
        workerbee_run_id="existing-run",
    )
    store.transition_workerbee_controlled_run_request(
        request["request_id"],
        executor_id="dead-daemon",
        status="running",
        expected_statuses=["provisioning"],
        workerbee_run_id="existing-run",
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE workerbee_controlled_run_requests SET claim_expires_at = 0 WHERE request_id = ?",
            (request["request_id"],),
        )
    calls: list[str] = []

    async def fake_call(_endpoint, tool_name, _arguments, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(tool_name)
        if tool_name.endswith("_status"):
            return {"data": {"status": "succeeded"}}
        if tool_name.endswith("_receipt"):
            return {"data": {"cleanup": {"complete": True, "residue": []}}}
        raise AssertionError(tool_name)

    executor = WorkerBeeControlledRunExecutor(
        store,
        service,
        service.runtime_registry,
        tool_caller=fake_call,
        executor_id="replacement-daemon",
    )
    await executor._run(str(request["request_id"]))
    assert "workerbee_v1_controlled_run_start" not in calls
    assert store.get_workerbee_controlled_run_request(request["request_id"])["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "plan_drift", "drain_timeout", "fast_reopen", "held", "generation_drift",
    "workspace_drift", "canceled", "lease_revoked", "exhausted_lease",
    "lease_revoked_during_preflight", "shutdown_and_startup_recovery",
])
async def test_approval_admission_race_uses_owned_task_and_exact_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)
    store = app.state.store
    executor = app.state.workerbee_controlled_run_executor
    cutover = app.state.workerbee_runtime.cutover
    schedule = executor.schedule
    observed_refusal = asyncio.Event()
    permit_wait = asyncio.Event()
    wait_for_reopen = cutover.wait_for_reopen
    calls: list[str] = []
    lease_id = None

    async def fake_call(_endpoint, name, arguments, **_kwargs):
        calls.append(name)
        if name.endswith("_preflight"):
            if case == "lease_revoked_during_preflight":
                store.revoke_workerbee_controlled_run_lease(lease_id, revoked_by="test")
            return {"data": {
                "ok": True,
                **{key: arguments[key] for key in (
                    "request_sha256", "profile_sha256", "tool_manifest_sha256",
                )},
            }}
        if name.endswith("_start"):
            return {"data": {"run_id": "fake-run", "workspace_id": "fake-workspace"}}
        if name.endswith("_status"):
            return {"data": {"status": "succeeded"}}
        if name.endswith("_receipt"):
            return {"data": {"cleanup": {"complete": True, "residue": []}}}
        raise AssertionError(name)

    async def note_refusal(**kwargs):
        observed_refusal.set()
        if case == "fast_reopen":
            await permit_wait.wait()
        return await wait_for_reopen(**kwargs)

    monkeypatch.setattr(executor, "tool_caller", fake_call)
    monkeypatch.setattr(cutover, "wait_for_reopen", note_refusal)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        await asyncio.to_thread(app.state.workerbee_application.refresh_runtime_selection)
        service = app.state.workerbee_controlled_runs
        row = service.request(
            **_api_payload(fork_id, campaign_id), requested_by="test",
        )["request"]
        request_id = row["request_id"]
        if case in {"lease_revoked", "exhausted_lease", "lease_revoked_during_preflight"}:
            lease = store.create_workerbee_controlled_run_lease(
                request=row, requested_by="test", max_uses=1 if case == "exhausted_lease" else 2,
                ttl_seconds=120,
            )
            lease_id = lease["lease_id"]
            store.decide_workerbee_controlled_run_lease(lease_id, approve=True, decided_by="root")

        selected = cutover.selected()
        record = {"source": selected.public_dict()}
        transaction = "approval-race"
        cutover.preview(transaction, "controller", record)

        def schedule_then_close(approved_id):
            assert store.get_workerbee_controlled_run_request(approved_id)["status"] == "approved"
            assert schedule(approved_id)
            cutover.claim(transaction, "controller", phase="previewed", selection=selected)
            return True

        monkeypatch.setattr(executor, "schedule", schedule_then_close)
        release_read = asyncio.Event()
        read_ready = asyncio.Event()

        async def old_read():
            with cutover.operation("old_gateway_read"):
                read_ready.set()
                await release_read.wait()

        read = asyncio.create_task(old_read()) if case == "drain_timeout" else None
        try:
            if read is not None:
                await asyncio.wait_for(read_ready.wait(), timeout=5)
            decision = await client.post(
                f"/v2/workerbee/controlled-runs/{request_id}/decision",
                json={"approve": True, "decided_by": "root", "lease_id": lease_id},
            )
            assert decision.status_code == 200, decision.text
            approval = decision.json()
            task = executor._tasks[request_id]
            await asyncio.wait_for(observed_refusal.wait(), timeout=5)
            assert not task.done() and not calls and not schedule(request_id)
            assert cutover.posture()["admitted"] == int(read is not None)
            assert cutover.drain(transaction, timeout=0) is (read is None)
            assert store.get_workerbee_controlled_run_request(request_id) == approval

            if case == "held":
                cutover.advance(transaction, "draining", "held", record, hold="test hold")
            elif case == "generation_drift":
                cutover.advance(
                    transaction, "draining", "published", record,
                    publish=replace(selected, generation=selected.generation + 1),
                    transport_identity="e" * 64,
                )
                cutover.advance(transaction, "published", "verified", record, reopen=True)
            else:
                if case == "canceled":
                    service.cancel(request_id, canceled_by="root", reason="fixture")
                elif case == "workspace_drift":
                    patch = controller_metadata_patch({"workerbee_workspace_binding_id": "other"})
                    store.merge_agent_metadata(fork_id, patch)
                    store.merge_operator_fork_metadata(fork_id, patch)
                    assert store.get_agent(fork_id)["metadata"]["workerbee_workspace_binding_id"] == "other"
                elif case == "lease_revoked":
                    store.revoke_workerbee_controlled_run_lease(lease_id, revoked_by="root")
                elif case == "shutdown_and_startup_recovery":
                    await executor.stop()
                    assert task.cancelled() and not executor._tasks
                phase = "drain_timeout" if case == "drain_timeout" else "plan_drift"
                cutover.advance(transaction, "draining", phase, record, reopen=True)
        finally:
            if read is not None:
                release_read.set()
                await read
        permit_wait.set()

        if case == "shutdown_and_startup_recovery":
            assert store.get_workerbee_controlled_run_request(request_id) == approval
            assert not calls
            monkeypatch.setattr(executor, "schedule", schedule)
            await executor.start()
            task = executor._tasks[request_id]
        await asyncio.wait_for(task, timeout=5)
        assert not executor._tasks
        final = store.get_workerbee_controlled_run_request(request_id)
        if case in {"plan_drift", "drain_timeout", "fast_reopen", "exhausted_lease",
                    "shutdown_and_startup_recovery"}:
            assert final["status"] == "succeeded"
            assert [name.rsplit("_", 1)[-1] for name in calls] == [
                "preflight", "start", "status", "receipt",
            ]
        elif case == "lease_revoked_during_preflight":
            assert final["error_code"] == "WORKERBEE_CONTROLLED_RUN_LEASE_STALE"
            assert calls == ["workerbee_v1_controlled_run_preflight"]
        else:
            assert final == approval or (case == "canceled" and final["status"] == "canceled")
            assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["drain", "recovery_hold", "ambiguous_start"])
async def test_already_admitted_run_handles_gate_closure_during_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    app, fork_id, campaign_id = _api_app(tmp_path, monkeypatch)
    store = app.state.store
    executor = app.state.workerbee_controlled_run_executor
    cutover = app.state.workerbee_runtime.cutover
    closed = asyncio.Event()
    allow_preflight_result = asyncio.Event()
    calls: list[str] = []
    transaction = "close-during-preflight"

    async def fake_call(_endpoint, name, arguments, **_kwargs):
        calls.append(name.rsplit("_", 1)[-1])
        if name.endswith("_preflight"):
            cutover.claim(transaction, "controller", phase="previewed", selection=selected)
            if case == "recovery_hold":
                cutover.hold("fixture recovery hold")
            closed.set()
            await allow_preflight_result.wait()
            return {"data": {"ok": True, **{
                key: arguments[key]
                for key in ("request_sha256", "profile_sha256", "tool_manifest_sha256")
            }}}
        if name.endswith("_start"):
            assert cutover.posture()["admission"] == "draining"
            if case == "ambiguous_start":
                raise TimeoutError("fixture lost result after dispatch")
            return {"data": {"run_id": "fake-run"}}
        if name.endswith("_status"):
            return {"data": {"status": "succeeded"}}
        if name.endswith("_receipt"):
            return {"data": {"cleanup": {"complete": True, "residue": []}}}
        raise AssertionError(name)

    monkeypatch.setattr(executor, "tool_caller", fake_call)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        await asyncio.to_thread(app.state.workerbee_application.refresh_runtime_selection)
        row = app.state.workerbee_controlled_runs.request(
            **_api_payload(fork_id, campaign_id), requested_by="test",
        )["request"]
        request_id = row["request_id"]
        selected = cutover.selected()
        record = {"source": selected.public_dict()}
        cutover.preview(transaction, "controller", record)
        decision = await client.post(
            f"/v2/workerbee/controlled-runs/{request_id}/decision",
            json={"approve": True, "decided_by": "root"},
        )
        assert decision.status_code == 200, decision.text
        task = executor._tasks[request_id]
        await asyncio.wait_for(closed.wait(), timeout=5)
        assert cutover.posture()["admitted"] == 1
        assert not cutover.drain(transaction, timeout=0)
        with pytest.raises(WorkerBeeCutoverError, match="admission is closed"):
            with cutover.operation("another_run", inherit=False):
                pytest.fail("The gate admitted a new run")
        allow_preflight_result.set()
        await asyncio.wait_for(task, timeout=5)
        assert not executor._tasks
        final = store.get_workerbee_controlled_run_request(request_id)
        if case == "drain":
            assert cutover.drain(transaction, timeout=0)
            cutover.advance(transaction, "draining", "plan_drift", record, reopen=True)
            assert cutover.posture()["admission"] == "open"
            assert final["status"] == "succeeded"
            assert calls == ["preflight", "start", "status", "receipt"]
        elif case == "recovery_hold":
            assert cutover.posture()["admission"] == "recovery_required"
            assert final["status"] == "failed" and final["workerbee_run_id"] is None
            assert final["error_code"] == "WORKERBEE_CUTOVER_DISPATCH_HELD"
            assert calls == ["preflight"]
        else:
            assert cutover.posture()["reason"] == "uncertain_operation"
            assert final["status"] == "recovery_required"
            assert final["workerbee_run_id"] is None and calls == ["preflight", "start"]
