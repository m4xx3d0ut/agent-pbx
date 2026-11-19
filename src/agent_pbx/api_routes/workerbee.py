from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..auth import (
    ClientPrincipal,
    get_store,
    require_controller,
    require_global_controller,
)
from ..remote import principal_allows_agent
from ..schemas import (
    WorkerBeeAccessProfileRequest,
    WorkerBeeControlledRunCancelRequest,
    WorkerBeeControlledRunCreateRequest,
    WorkerBeeControlledRunDecisionRequest,
    WorkerBeeControlledRunLeaseCreateRequest,
    WorkerBeeControlledRunLeaseDecisionRequest,
    WorkerBeeControlledRunLeaseRevokeRequest,
    WorkerBeeControlledRunPreflightRequest,
    WorkerBeeGatewayLeaseRequest,
    WorkerBeeSnapshotPolicyRequest,
    WorkerBeeWorkspacePolicyRequest,
    WorkerBeeWorkspacePrepareRequest,
    WorkerBeeWorkspaceTransitionDecisionRequest,
    WorkerBeeWorkspaceTransitionRequest,
)
from ..workerbee import agent_cwd
from ..workerbee_application import WorkerBeeApplicationService, finalize_issued_workerbee_lease
from ..workerbee_access import WorkerBeeAccessPolicyError
from ..workerbee_controlled_run import WorkerBeeControlledRunError
from ..workerbee_gateway import WorkerBeeGatewayError
from ..workerbee_modes import (
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
)
from ..workerbee_policy import WorkerBeeWorkspacePolicyError
from ..workerbee_snapshot_policy import WorkerBeeSnapshotPolicyError
from ..workerbee_workspace import WorkerBeeWorkspaceError
from ._shared import require_agent
from .workerbee_cutover import (
    controlled_runs_readback, local_readback, register_workerbee_switch_routes, wait_workerbee_thread,
    workerbee_admission, workerbee_agent_readback,
)


def register_workerbee_routes(
    app: FastAPI,
    *,
    workerbee_application: WorkerBeeApplicationService,
    call_workerbee_mcp_tool: Any,
) -> None:
    register_workerbee_switch_routes(app, workerbee_application)
    admitted = workerbee_admission(workerbee_application)

    async def workerbee_controlled_run_preflight(
        payload: WorkerBeeControlledRunPreflightRequest,
        request: Request,
    ) -> dict[str, Any]:
        await wait_workerbee_thread(workerbee_application.refresh_runtime_selection)
        try:
            result = await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.preflight,
                root_operator_agent_id=payload.root_operator_agent_id,
                target_fork_agent_id=payload.target_fork_agent_id,
                campaign_id=payload.campaign_id,
                profile_id=payload.profile_id,
                baseline_commit=payload.baseline_commit,
                candidate_commit=payload.candidate_commit,
            )
        except (WorkerBeeControlledRunError, ValueError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        runtime = request.app.state.workerbee_runtime.selected()
        if runtime is None:
            raise HTTPException(
                status_code=409, detail="no verified WorkerBee backend is selected"
            )
        try:
            capabilities = await call_workerbee_mcp_tool(
                runtime.endpoint,
                "workerbee_v1_capabilities",
                {},
                timeout_seconds=10.0,
            )
        except Exception as exc:  # noqa: BLE001 - normalize backend diagnostics
            result.update(
                {
                    "ok": False,
                    "capability_ready": False,
                    "capability_error": (
                        f"WorkerBee capability probe failed ({type(exc).__name__})."
                    ),
                    "missing_capabilities": list(result["required_capabilities"]),
                    "runtime_policy": None,
                }
            )
            return result
        data = (
            capabilities.get("data")
            if isinstance(capabilities.get("data"), dict)
            else {}
        )
        advertised = {
            str(item) for item in data.get("mcp_tools", []) if isinstance(item, str)
        }
        missing = [
            item for item in result["required_capabilities"] if item not in advertised
        ]
        runtime_policy: dict[str, Any] | None = None
        if str(result["runtime_policy_tool"]) in advertised:
            try:
                runtime_policy = await call_workerbee_mcp_tool(
                    runtime.endpoint,
                    str(result["runtime_policy_tool"]),
                    {},
                    timeout_seconds=10.0,
                )
            except Exception as exc:  # noqa: BLE001
                runtime_policy = {
                    "ok": False,
                    "error": f"runtime policy probe failed ({type(exc).__name__})",
                }
        result.update(
            {
                "ok": not missing,
                "capability_ready": not missing,
                "workerbee_version": str(data.get("workerbee_version") or ""),
                "advertised_capabilities": sorted(advertised),
                "missing_capabilities": missing,
                "runtime_policy": runtime_policy,
            }
        )
        return result

    @app.get(
        "/v2/workerbee/workspace-policy",
        response_model=dict[str, Any],
    )
    async def get_workerbee_workspace_policy(
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        view = await wait_workerbee_thread(
            request.app.state.workerbee_workspace_policy.get_policy
        )
        view["access_posture"] = await wait_workerbee_thread(
            request.app.state.workerbee_access_policy.posture
        )
        return view

    @app.get(
        "/v2/agents/{agent_id}/workerbee/access-profile",
        response_model=dict[str, Any],
    )
    async def get_agent_workerbee_access_profile(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            result = await wait_workerbee_thread(workerbee_application.access_readback, agent_id)
            result["runtime_binding"] = await wait_workerbee_thread(workerbee_agent_readback, request, agent)
            return result
        except WorkerBeeAccessPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.put(
        "/v2/agents/{agent_id}/workerbee/access-profile",
        response_model=dict[str, Any],
    )
    @admitted("put_agent_workerbee_access_profile")
    async def put_agent_workerbee_access_profile(
        agent_id: str,
        payload: WorkerBeeAccessProfileRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            if (
                payload.profile is not None
                and payload.access_profile is not None
                and payload.profile != payload.access_profile
            ):
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_PROFILE_CONFLICT",
                    "profile and access_profile must match when both are supplied.",
                )
            result = await wait_workerbee_thread(
                request.app.state.workerbee_access_policy.set_policy,
                agent_id,
                access_profile=payload.access_profile or payload.profile,
                source_mode=payload.source_mode,
                execution_lane=payload.execution_lane,
                updated_by=controller.token_id or controller.client_id or "local",
            )
            readback = await wait_workerbee_thread(
                workerbee_application.access_readback,
                agent_id,
            )
            readback["changed"] = bool(result.get("changed"))
            return readback
        except WorkerBeeAccessPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/workerbee/controlled-runs/posture",
        response_model=dict[str, Any],
    )
    async def get_workerbee_controlled_run_posture(
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            posture = await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.posture
            )
        except WorkerBeeControlledRunError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        posture["runtime"] = await wait_workerbee_thread(local_readback, request.app.state.workerbee_runtime)
        return posture

    @app.post(
        "/v2/workerbee/controlled-runs/preflight",
        response_model=dict[str, Any],
    )
    @admitted("preflight_workerbee_controlled_run", discover=True)
    async def preflight_workerbee_controlled_run(
        payload: WorkerBeeControlledRunPreflightRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await workerbee_controlled_run_preflight(payload, request)

    @app.post(
        "/v2/workerbee/controlled-runs",
        response_model=dict[str, Any],
    )
    @admitted("create_workerbee_controlled_run", discover=True)
    async def create_workerbee_controlled_run(
        payload: WorkerBeeControlledRunCreateRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        preflight = await workerbee_controlled_run_preflight(payload, request)
        if not preflight.get("capability_ready"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORKERBEE_CONTROLLED_RUN_CAPABILITY_MISSING",
                    "message": "The selected WorkerBee appliance does not advertise the controlled-run contract.",
                    "missing_capabilities": preflight.get("missing_capabilities", []),
                },
            )
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.request,
                root_operator_agent_id=payload.root_operator_agent_id,
                target_fork_agent_id=payload.target_fork_agent_id,
                campaign_id=payload.campaign_id,
                profile_id=payload.profile_id,
                baseline_commit=payload.baseline_commit,
                candidate_commit=payload.candidate_commit,
                requested_by=payload.requested_by,
                reason=payload.reason,
            )
        except (WorkerBeeControlledRunError, ValueError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc

    @app.get(
        "/v2/workerbee/controlled-runs",
        response_model=dict[str, Any],
    )
    async def list_workerbee_controlled_runs(
        request: Request,
        root_operator_agent_id: str | None = Query(default=None, max_length=120),
        target_fork_agent_id: str | None = Query(default=None, max_length=240),
        status_filter: list[str] | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1, le=500),
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        rows = await wait_workerbee_thread(
            store.list_workerbee_controlled_run_requests,
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
            statuses=status_filter,
            limit=limit, expire=False,
        )
        return {"requests": await wait_workerbee_thread(controlled_runs_readback, request, rows)}

    @app.get(
        "/v2/workerbee/controlled-runs/{request_id}",
        response_model=dict[str, Any],
    )
    async def get_workerbee_controlled_run(
        request_id: str,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        result = await wait_workerbee_thread(
            store.get_workerbee_controlled_run_request,
            request_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="controlled run not found")
        return (await wait_workerbee_thread(controlled_runs_readback, request, [result]))[0]

    @app.post(
        "/v2/workerbee/controlled-runs/{request_id}/decision",
        response_model=dict[str, Any],
    )
    @admitted("decide_workerbee_controlled_run")
    async def decide_workerbee_controlled_run(
        request_id: str,
        payload: WorkerBeeControlledRunDecisionRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            result = await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.decide,
                request_id,
                approve=payload.approve,
                decided_by=payload.decided_by,
                lease_id=payload.lease_id,
            )
            if payload.approve:
                request.app.state.workerbee_controlled_run_executor.schedule(request_id)
            return result
        except (WorkerBeeControlledRunError, KeyError, ValueError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc

    @app.post(
        "/v2/workerbee/controlled-runs/{request_id}/cancel",
        response_model=dict[str, Any],
    )
    async def cancel_workerbee_controlled_run(
        request_id: str,
        payload: WorkerBeeControlledRunCancelRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await request.app.state.workerbee_controlled_run_executor.cancel(
                request_id,
                canceled_by=payload.canceled_by,
                reason=payload.reason,
            )
        except (WorkerBeeControlledRunError, KeyError, ValueError, RuntimeError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc

    @app.post(
        "/v2/workerbee/controlled-runs/{request_id}/leases",
        response_model=dict[str, Any],
    )
    @admitted("create_workerbee_controlled_run_lease")
    async def create_workerbee_controlled_run_lease(
        request_id: str,
        payload: WorkerBeeControlledRunLeaseCreateRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.request_lease,
                request_id,
                requested_by=payload.requested_by,
            )
        except (WorkerBeeControlledRunError, KeyError, ValueError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc

    @app.get(
        "/v2/workerbee/controlled-run-leases",
        response_model=dict[str, Any],
    )
    async def list_workerbee_controlled_run_leases(
        root_operator_agent_id: str | None = Query(default=None, max_length=120),
        status_filter: str | None = Query(default=None, alias="status", max_length=40),
        limit: int = Query(default=100, ge=1, le=500),
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        return {
            "leases": await wait_workerbee_thread(
                store.list_workerbee_controlled_run_leases,
                root_operator_agent_id=root_operator_agent_id,
                status=status_filter,
                limit=limit,
            )
        }

    @app.post(
        "/v2/workerbee/controlled-run-leases/{lease_id}/decision",
        response_model=dict[str, Any],
    )
    @admitted("decide_workerbee_controlled_run_lease")
    async def decide_workerbee_controlled_run_lease(
        lease_id: str,
        payload: WorkerBeeControlledRunLeaseDecisionRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_controlled_runs.decide_lease,
                lease_id,
                approve=payload.approve,
                decided_by=payload.decided_by,
            )
        except (WorkerBeeControlledRunError, KeyError, ValueError) as exc:
            detail = (
                exc.public_dict()
                if isinstance(exc, WorkerBeeControlledRunError)
                else str(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc

    @app.post(
        "/v2/workerbee/controlled-run-leases/{lease_id}/revoke",
        response_model=dict[str, Any],
    )
    @admitted("revoke_workerbee_controlled_run_lease")
    async def revoke_workerbee_controlled_run_lease(
        lease_id: str,
        payload: WorkerBeeControlledRunLeaseRevokeRequest,
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            return await wait_workerbee_thread(
                store.revoke_workerbee_controlled_run_lease,
                lease_id,
                revoked_by=payload.revoked_by,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/v2/workerbee/workspace-policy",
        response_model=dict[str, Any],
    )
    @admitted("put_workerbee_workspace_policy")
    async def put_workerbee_workspace_policy(
        payload: WorkerBeeWorkspacePolicyRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_workspace_policy.set_policy,
                agent_id=None,
                policy=payload.policy,
                request_mode=payload.request_mode,
                allowed_roots=payload.allowed_roots,
                managed_scratch_root=payload.managed_scratch_root,
                profile_label=payload.profile_label,
                updated_by=controller.token_id or "local",
            )
        except WorkerBeeWorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/agents/{agent_id}/workerbee/workspace-policy",
        response_model=dict[str, Any],
    )
    async def get_agent_workerbee_workspace_policy(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        return await wait_workerbee_thread(
            request.app.state.workerbee_workspace_policy.get_policy, agent_id
        )

    @app.put(
        "/v2/agents/{agent_id}/workerbee/workspace-policy",
        response_model=dict[str, Any],
    )
    @admitted("put_agent_workerbee_workspace_policy")
    async def put_agent_workerbee_workspace_policy(
        agent_id: str,
        payload: WorkerBeeWorkspacePolicyRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_workspace_policy.set_policy,
                agent_id=agent_id,
                policy=payload.policy,
                request_mode=payload.request_mode,
                allowed_roots=payload.allowed_roots,
                managed_scratch_root=payload.managed_scratch_root,
                profile_label=payload.profile_label,
                updated_by=controller.token_id or controller.client_id or "local",
            )
        except WorkerBeeWorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.delete(
        "/v2/agents/{agent_id}/workerbee/workspace-policy",
        response_model=dict[str, Any],
    )
    @admitted("delete_agent_workerbee_workspace_policy")
    async def delete_agent_workerbee_workspace_policy(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_workspace_policy.clear_policy,
                agent_id,
                updated_by=controller.token_id or controller.client_id or "local",
            )
        except WorkerBeeWorkspacePolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/workerbee/snapshot-policy",
        response_model=dict[str, Any],
    )
    async def get_workerbee_snapshot_policy(
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        runtime = request.app.state.workerbee_runtime.selected()
        return await wait_workerbee_thread(
            request.app.state.workerbee_snapshot_policy.get_policy,
            backend=(runtime.backend if runtime is not None else None),
        )

    @app.put(
        "/v2/workerbee/snapshot-policy",
        response_model=dict[str, Any],
    )
    @admitted("put_workerbee_snapshot_policy")
    async def put_workerbee_snapshot_policy(
        payload: WorkerBeeSnapshotPolicyRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_snapshot_policy.set_policy,
                agent_id=None,
                source_root=payload.source_root,
                policy=payload.policy,
                include_paths=payload.include_paths,
                max_bytes=payload.max_bytes,
                max_files=payload.max_files,
                max_file_bytes=payload.max_file_bytes,
                profile_label=payload.profile_label,
                updated_by=controller.token_id or "local",
            )
        except WorkerBeeSnapshotPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/agents/{agent_id}/workerbee/snapshot-policy",
        response_model=dict[str, Any],
    )
    async def get_agent_workerbee_snapshot_policy(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        runtime = request.app.state.workerbee_runtime.selected()
        return await wait_workerbee_thread(
            request.app.state.workerbee_snapshot_policy.get_policy,
            agent_id,
            source_root=agent_cwd(agent),
            backend=(runtime.backend if runtime is not None else None),
        )

    @app.put(
        "/v2/agents/{agent_id}/workerbee/snapshot-policy",
        response_model=dict[str, Any],
    )
    @admitted("put_agent_workerbee_snapshot_policy")
    async def put_agent_workerbee_snapshot_policy(
        agent_id: str,
        payload: WorkerBeeSnapshotPolicyRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        source_root = str(payload.source_root or agent_cwd(agent) or "").strip()
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_snapshot_policy.set_policy,
                agent_id=agent_id,
                source_root=source_root,
                policy=payload.policy,
                include_paths=payload.include_paths,
                max_bytes=payload.max_bytes,
                max_files=payload.max_files,
                max_file_bytes=payload.max_file_bytes,
                profile_label=payload.profile_label,
                updated_by=controller.token_id or controller.client_id or "local",
            )
        except WorkerBeeSnapshotPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.delete(
        "/v2/agents/{agent_id}/workerbee/snapshot-policy",
        response_model=dict[str, Any],
    )
    @admitted("delete_agent_workerbee_snapshot_policy")
    async def delete_agent_workerbee_snapshot_policy(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_snapshot_policy.clear_policy,
                agent_id,
                source_root=agent_cwd(agent) or "",
            )
        except WorkerBeeSnapshotPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/agents/{agent_id}/workerbee/snapshot-policy/preview",
        response_model=dict[str, Any],
    )
    @admitted("preview_agent_workerbee_snapshot_policy")
    async def preview_agent_workerbee_snapshot_policy(
        agent_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        runtime = request.app.state.workerbee_runtime.selected()
        if runtime is None:
            raise HTTPException(
                status_code=409, detail="no verified WorkerBee backend is selected"
            )
        try:
            return await wait_workerbee_thread(
                request.app.state.workerbee_snapshot_policy.preview,
                agent_id=agent_id,
                source_root=agent_cwd(agent) or "",
                backend=runtime.backend,
            )
        except WorkerBeeSnapshotPolicyError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/agents/{agent_id}/workerbee/workspace-transitions",
        response_model=list[dict[str, Any]],
    )
    async def list_workerbee_workspace_transitions(
        agent_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> list[dict[str, Any]]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        return await wait_workerbee_thread(
            request.app.state.workerbee_workspace_policy.list_transitions,
            agent_id,
            limit=limit,
        )

    @app.post(
        "/v2/agents/{agent_id}/workerbee/workspace-transitions/preview",
        response_model=dict[str, Any],
    )
    @admitted("preview_workerbee_workspace_transition", discover=True)
    async def preview_workerbee_workspace_transition(
        agent_id: str,
        payload: WorkerBeeWorkspaceTransitionRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        lease_id = str(metadata.get("workerbee_gateway_lease_id") or "").strip()
        if not lease_id:
            raise HTTPException(
                status_code=409, detail="Agent has no active WorkerBee gateway lease"
            )
        await wait_workerbee_thread(workerbee_application.refresh_runtime_selection)
        try:
            lease = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.load, lease_id
            )
            binding = await wait_workerbee_thread(
                request.app.state.workerbee_workspaces.load,
                lease.workspace_binding_id,
            )
            target = await wait_workerbee_thread(
                request.app.state.workerbee_workspace_policy.resolve_target,
                agent_id=agent_id,
                binding=binding,
                candidate_cwd=payload.cwd,
                requested_project=payload.project,
                current_working_subpath=lease.working_subpath,
                allow_disabled=True,
            )
        except (WorkerBeeGatewayError, WorkerBeeWorkspacePolicyError) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        return {
            "agent_id": agent_id,
            "lease_id": lease_id,
            "current_binding_id": lease.workspace_binding_id,
            "target": target.public_dict(),
            "reason": payload.reason,
        }

    @app.post(
        "/v2/agents/{agent_id}/workerbee/workspace-transitions",
        response_model=dict[str, Any],
    )
    @admitted("apply_workerbee_workspace_transition", discover=True)
    async def apply_workerbee_workspace_transition(
        agent_id: str,
        payload: WorkerBeeWorkspaceTransitionRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        lease_id = str(metadata.get("workerbee_gateway_lease_id") or "").strip()
        if not lease_id:
            raise HTTPException(
                status_code=409, detail="Agent has no active WorkerBee gateway lease"
            )
        await wait_workerbee_thread(workerbee_application.refresh_runtime_selection)
        try:
            lease, binding, changed = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.transition_workspace,
                await wait_workerbee_thread(
                    request.app.state.workerbee_gateway.load, lease_id
                ),
                {
                    "cwd": payload.cwd,
                    "project": payload.project,
                    "reason": payload.reason,
                },
                controller_approved=True,
                requested_by=controller.token_id or controller.client_id or "local",
            )
        except (WorkerBeeGatewayError, WorkerBeeWorkspacePolicyError) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        return {
            "changed": changed,
            "lease": lease.public_dict(),
            "workspace_binding": binding.public_dict(),
        }

    @app.post(
        "/v2/agents/{agent_id}/workerbee/workspace-transitions/{transition_id}/decision",
        response_model=dict[str, Any],
    )
    @admitted("decide_workerbee_workspace_transition")
    async def decide_workerbee_workspace_transition(
        agent_id: str,
        transition_id: str,
        payload: WorkerBeeWorkspaceTransitionDecisionRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            transition = request.app.state.workerbee_workspace_policy.get_transition(
                transition_id
            )
            if transition is None or transition.get("agent_id") != agent_id:
                raise HTTPException(
                    status_code=404, detail="Workspace transition not found"
                )
            if not payload.approve:
                if transition.get("status") != "pending":
                    raise HTTPException(
                        status_code=409,
                        detail="Only a pending workspace transition can be denied",
                    )
                denied = await wait_workerbee_thread(
                    request.app.state.workerbee_workspace_policy.update_transition,
                    transition_id,
                    status="denied",
                    reason="Denied by controller",
                )
                return {"changed": False, "transition": denied}
            lease, binding, changed = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.apply_transition,
                transition_id,
                approved_by=controller.token_id or controller.client_id or "local",
            )
        except (WorkerBeeGatewayError, WorkerBeeWorkspacePolicyError) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        return {
            "changed": changed,
            "lease": lease.public_dict(),
            "workspace_binding": binding.public_dict(),
        }

    @app.post(
        "/v2/agents/{agent_id}/workerbee/workspace-transitions/{transition_id}/revert",
        response_model=dict[str, Any],
    )
    @admitted("revert_workerbee_workspace_transition")
    async def revert_workerbee_workspace_transition(
        agent_id: str,
        transition_id: str,
        request: Request,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        if not principal_allows_agent(controller, agent_id):
            raise HTTPException(
                status_code=403, detail="controller is not scoped to this Agent"
            )
        try:
            transition = request.app.state.workerbee_workspace_policy.get_transition(
                transition_id
            )
            if transition is None or transition.get("agent_id") != agent_id:
                raise HTTPException(
                    status_code=404, detail="Workspace transition not found"
                )
            lease, binding = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.revert_transition,
                transition_id,
                reverted_by=controller.token_id or controller.client_id or "local",
            )
        except (WorkerBeeGatewayError, WorkerBeeWorkspacePolicyError) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        return {
            "lease": lease.public_dict(),
            "workspace_binding": binding.public_dict(),
        }

    @app.post(
        "/v2/agents/{agent_id}/workerbee/workspaces",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    @admitted("prepare_workerbee_workspace", discover=True)
    async def prepare_workerbee_workspace(
        agent_id: str,
        payload: WorkerBeeWorkspacePrepareRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = store.get_agent(agent_id)
        cwd = agent_cwd(agent) if agent is not None else payload.source_root
        if not cwd:
            raise HTTPException(
                status_code=409,
                detail="agent has no scoped cwd and no launch source root was supplied",
            )
        await wait_workerbee_thread(workerbee_application.refresh_runtime_selection)
        runtime = request.app.state.workerbee_runtime.selected()
        if runtime is None:
            raise HTTPException(
                status_code=409,
                detail="no verified WorkerBee backend is selected",
            )
        project = str(
            payload.project or (agent.get("project") if agent is not None else "") or ""
        ).strip()
        try:
            if agent is None:
                if payload.source_mode is None:
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_SOURCE_MODE_REQUIRED",
                        "Pre-registration workspace preparation requires an explicit source mode.",
                        "Select native-direct, snapshot-import, or managed-clone for the chosen backend.",
                    )
                source_mode = payload.source_mode
                if source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND:
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_TRUSTED_DIRECT_BIND_AGENT_REQUIRED",
                        "trusted-direct-bind requires a registered Agent with explicit host policy.",
                    )
            else:
                access = await wait_workerbee_thread(
                    request.app.state.workerbee_access_policy.get_profile,
                    agent_id,
                    selected_backend=runtime.backend,
                )
                if access.get("source_mode_constrained"):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_SOURCE_MODE_CEILING",
                        "The stored WorkerBee source mode exceeds the administrator ceiling.",
                        "Select an allowed source mode before preparing a workspace.",
                    )
                source_mode = str(access["source_mode"])
                if (
                    payload.source_mode is not None
                    and payload.source_mode != source_mode
                ):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_SOURCE_MODE_POLICY_MISMATCH",
                        "Requested source mode does not match the Agent's effective policy.",
                        "Update the source-mode policy first; Agent PBX will not fall back.",
                    )
            has_direct_receipt = bool(
                payload.trusted_direct_bind_receipt_id
                or payload.trusted_direct_bind_receipt_sha256
            )
            if source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND:
                if not (
                    payload.trusted_direct_bind_receipt_id
                    and payload.trusted_direct_bind_receipt_sha256
                ):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_REQUIRED",
                        "trusted-direct-bind requires an exact host-owned receipt ID and digest.",
                    )
            elif has_direct_receipt:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_UNEXPECTED",
                    "Trusted-direct receipt identity is valid only for trusted-direct-bind.",
                )
            include_paths = await wait_workerbee_thread(
                request.app.state.workerbee_snapshot_policy.resolve_include_paths,
                agent_id=agent_id,
                source_root=cwd,
                backend=runtime.backend,
            )
            if payload.include_paths and not set(payload.include_paths).issubset(
                set(include_paths)
            ):
                raise WorkerBeeSnapshotPolicyError(
                    "WORKERBEE_SNAPSHOT_POLICY_DENIED",
                    "Requested appliance include paths exceed the effective host policy.",
                    "Approve the paths through WorkerBee appliance-content policy first.",
                )
            path_policy = await wait_workerbee_thread(
                request.app.state.workerbee_workspace_policy.resolve_path_domain,
                agent_id=agent_id,
                source_root=cwd,
            )
            if agent is not None:
                agent_metadata = (
                    agent.get("metadata")
                    if isinstance(agent.get("metadata"), dict)
                    else {}
                )
                persisted_policy_digest = str(
                    agent_metadata.get("workerbee_workspace_policy_digest") or ""
                ).strip()
                if (
                    workerbee_application.durable_profile(agent_metadata)
                    == WORKERBEE_PROFILE_TRUSTED_WORKBENCH
                    and persisted_policy_digest
                    and persisted_policy_digest != path_policy.policy_digest
                ):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_WORKSPACE_POLICY_DIGEST_DRIFT",
                        "Durable WorkerBee profile authority no longer matches workspace policy.",
                        "Review and persist the current WorkerBee policy before "
                        "preparing a workspace.",
                    )
            binding = await wait_workerbee_thread(
                request.app.state.workerbee_workspaces.prepare,
                runtime=runtime,
                project=project,
                source_root=cwd,
                include_paths=include_paths,
                source_mode=source_mode,
                trusted_direct_bind_receipt_id=(payload.trusted_direct_bind_receipt_id),
                trusted_direct_bind_receipt_sha256=(
                    payload.trusted_direct_bind_receipt_sha256
                ),
                allowed_host_root=path_policy.allowed_host_root,
                workspace_policy_digest=path_policy.policy_digest,
            )
        except (
            WorkerBeeAccessPolicyError,
            WorkerBeeWorkspaceError,
            WorkerBeeWorkspacePolicyError,
            WorkerBeeSnapshotPolicyError,
        ) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        return binding.public_dict()

    @app.post(
        "/v2/agents/{agent_id}/workerbee/leases",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    @admitted("issue_workerbee_gateway_lease", discover=True)
    async def issue_workerbee_gateway_lease(
        agent_id: str,
        payload: WorkerBeeGatewayLeaseRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = store.get_agent(agent_id)
        await wait_workerbee_thread(workerbee_application.refresh_runtime_selection)
        try:
            runtime = request.app.state.workerbee_runtime.selected()
            if runtime is None:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_BACKEND_UNSELECTED",
                    "No verified WorkerBee backend is selected.",
                )
            if agent is None:
                if payload.profile not in {
                    WORKERBEE_PROFILE_OBSERVE,
                    WORKERBEE_PROFILE_REVIEW,
                }:
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_ACCESS_PROFILE_UNAPPROVED",
                        "Pre-registration launch candidates are limited to read-only WorkerBee profiles.",
                    )
            else:
                access = await wait_workerbee_thread(
                    request.app.state.workerbee_access_policy.get_profile,
                    agent_id,
                    selected_backend=runtime.backend,
                )
                effective_profile = str(access["effective_profile"])
                if payload.profile != effective_profile:
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_ACCESS_PROFILE_POLICY_MISMATCH",
                        "Requested lease profile does not match the Agent's effective access policy.",
                        f"Request {effective_profile!r} or update the policy within its administrator ceiling.",
                    )
                requested_binding = await wait_workerbee_thread(
                    request.app.state.workerbee_workspaces.load,
                    payload.workspace_binding_id,
                )
                path_policy = await wait_workerbee_thread(
                    request.app.state.workerbee_workspace_policy.resolve_path_domain,
                    agent_id=agent_id,
                    source_root=requested_binding.source_root,
                )
                if (
                    requested_binding.path_domain.workspace_policy_digest
                    != path_policy.policy_digest
                    or requested_binding.path_domain.allowed_host_root
                    != path_policy.allowed_host_root
                ):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_WORKSPACE_POLICY_DIGEST_DRIFT",
                        "Workspace binding was created under a different effective root policy.",
                        "Prepare a fresh workspace binding under the current policy.",
                    )
                agent_metadata = (
                    agent.get("metadata")
                    if isinstance(agent.get("metadata"), dict)
                    else {}
                )
                persisted_policy_digest = str(
                    agent_metadata.get("workerbee_workspace_policy_digest") or ""
                ).strip()
                if (
                    workerbee_application.durable_profile(agent_metadata)
                    == WORKERBEE_PROFILE_TRUSTED_WORKBENCH
                    and persisted_policy_digest
                    and persisted_policy_digest != path_policy.policy_digest
                ):
                    raise WorkerBeeAccessPolicyError(
                        "WORKERBEE_WORKSPACE_POLICY_DIGEST_DRIFT",
                        "Durable WorkerBee profile authority no longer matches workspace policy.",
                        "Review and persist the current WorkerBee policy before issuing a lease.",
                    )
            lease, token = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.issue,
                agent_id=agent_id,
                codex_session_id=payload.codex_session_id,
                profile=payload.profile,
                workspace_binding_id=payload.workspace_binding_id,
                ttl_seconds=payload.ttl_seconds,
            )
        except (
            WorkerBeeAccessPolicyError,
            WorkerBeeGatewayError,
            WorkerBeeWorkspaceError,
            WorkerBeeWorkspacePolicyError,
        ) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        binding = None
        if agent is not None:
            binding = await wait_workerbee_thread(
                request.app.state.workerbee_workspaces.load,
                lease.workspace_binding_id,
            )
        try:
            await wait_workerbee_thread(
                finalize_issued_workerbee_lease,
                store,
                request.app.state.workerbee_gateway,
                lease,
                binding,
            )
        except WorkerBeeGatewayError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
        gateway_url = (
            str(request.base_url).rstrip("/") + f"/workerbee-mcp/{lease.lease_id}"
        )
        return {
            "lease": lease.public_dict(),
            "token": token,
            "gateway_url": gateway_url,
            "bearer_token_env_var": "AGENT_PBX_WORKERBEE_GATEWAY_TOKEN",
        }

    @app.delete(
        "/v2/agents/{agent_id}/workerbee/leases/{lease_id}",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    @admitted("revoke_workerbee_gateway_lease")
    async def revoke_workerbee_gateway_lease(
        agent_id: str,
        lease_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        require_agent(store, agent_id)
        try:
            lease = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.load, lease_id
            )
            if lease.agent_id != agent_id:
                raise HTTPException(status_code=404, detail="WorkerBee lease not found")
            revoked = await wait_workerbee_thread(
                request.app.state.workerbee_gateway.revoke, lease_id
            )
        except WorkerBeeGatewayError as exc:
            raise HTTPException(status_code=404, detail=exc.public_dict()) from exc
        return revoked.public_dict()



__all__ = ("register_workerbee_routes",)
