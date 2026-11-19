from __future__ import annotations

import time
from functools import wraps
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from ..auth import ClientPrincipal, get_store, principal_from_record, require_controller, require_global_controller, require_token
from ..schemas import WorkerBeeStatusResponse, WorkerBeeSwitchApplyRequest, WorkerBeeSwitchPreviewRequest, WorkerBeeSwitchRollbackRequest
from ..workerbee_cutover import WorkerBeeCutoverError, wait_workerbee_thread
from ..workerbee_readback import agent_binding_readback, daemon_readback, local_readback, run_readback, runtime_identity, switch_record


def workerbee_admission(application):
    def admitted(kind: str, *, discover: bool = False):
        def decorate(func):
            @wraps(func)
            async def run(*args, **kwargs):
                registry = kwargs["request"].app.state.workerbee_runtime
                if discover and registry.selected() is None:
                    await wait_workerbee_thread(application.refresh_runtime_selection)
                with registry.admission(kind, require_runtime=discover):
                    return await func(*args, **kwargs)
            return run
        return decorate

    return admitted


def workerbee_agent_readback(request: Request, agent: dict[str, Any]) -> dict[str, Any]:
    view = local_readback(request.app.state.workerbee_runtime)
    requests = request.app.state.store.list_workerbee_controlled_run_requests(
        target_fork_agent_id=agent["agent_id"],
        statuses=("approved", "claimed", "provisioning", "canary", "running", "collecting", "cleaning", "recovery_required"),
        limit=20, expire=False,
    )
    return {
        "selected": view["selected"], "admission": view["admission"],
        "gateway": agent_binding_readback(request.app.state, agent, view["selected"]),
        "controlled_runs": [
            {"request_id": row["request_id"], "status": row["status"], **run_readback(row, view["selected"])}
            for row in requests
        ],
    }


def controlled_runs_readback(request: Request, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = local_readback(request.app.state.workerbee_runtime)["selected"]
    return [{**row, "runtime_binding": run_readback(row, selected)} for row in records]


def _controller_key(record) -> str | None:
    if record is None:
        return "local"
    principal = principal_from_record(record)
    return principal.token_id if principal.role == "controller" and not principal.allowed_agent_ids else None


def _runtime_view(request: Request, record, api_version: str) -> dict[str, Any]:
    state = request.app.state
    view = daemon_readback(state.workerbee_runtime, state.workerbee_switcher, controller=_controller_key(record))
    return {**view, "readback_schema": view["api_version"], "api_version": api_version}


def register_workerbee_switch_routes(app: FastAPI, application) -> None:
    @app.exception_handler(WorkerBeeCutoverError)
    async def workerbee_cutover_error(_request: Request, exc: WorkerBeeCutoverError):
        return JSONResponse({"detail": exc.public_dict()}, status_code=409)

    @app.get("/v2/workerbee/runtime", response_model=dict[str, Any])
    async def workerbee_runtime_status(
        request: Request, record=Depends(require_token),
    ) -> dict[str, Any]:
        return await wait_workerbee_thread(_runtime_view, request, record, "agent-pbx.workerbee-runtime/v1")

    @app.get("/v1/agents/{agent_id}/workerbee", response_model=WorkerBeeStatusResponse)
    async def get_agent_workerbee_status(
        agent_id: str, request: Request, store: Any = Depends(get_store),
        _record=Depends(require_token),
    ) -> dict[str, Any]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        state = request.app.state
        registry = state.workerbee_runtime
        view = await wait_workerbee_thread(daemon_readback, registry, state.workerbee_switcher)
        bound = await wait_workerbee_thread(agent_binding_readback, state, agent, view["selected"])
        gate = view["admission"]
        status = None
        can_read = (
            gate["admission"] in {"open", "legacy"} and not bound["rebind_required"]
            and (view["selected"] is not None or (not view["observed"] and not bound["bound"]))
        )
        if can_read:
            try:
                with registry.admission("agent_readback", require_runtime=False):
                    pinned = registry.selected()
                    pinned_view = runtime_identity(pinned.public_dict()) if pinned else None
                    if pinned_view == view["selected"]:
                        status = await application.selected_status_for_agent(agent)
            except WorkerBeeCutoverError:
                pass
            if status is None:
                view = local_readback(registry)
                gate = view["admission"]
                bound = await wait_workerbee_thread(agent_binding_readback, state, agent, view["selected"])
        if status is None:
            code = (
                "WORKERBEE_CUTOVER_CLOSED" if gate["admission"] not in {"open", "legacy"}
                else "WORKERBEE_BACKEND_REBIND_REQUIRED" if bound["rebind_required"]
                else "WORKERBEE_SELECTOR_CHANGED" if view["selected"] else "WORKERBEE_RUNTIME_UNSELECTED"
            )
            status = {
                "agent_id": agent_id, "configured": True, "available": False,
                "checked_at": time.time(), "status_source": "local_selector",
                "error": {"code": code, "message": f"WorkerBee runtime status is held ({code})."},
            }
        if view["selected"] or view["observed"] or status["status_source"] == "local_selector":
            status["selected_runtime"] = {
                **(view["selected"] or {}),
                "admission": gate, "observed": view["observed"],
                "observation_state": view["observation_state"], "gateway": bound,
            }
        return status

    @app.get("/v1/workerbee/switch", response_model=dict[str, Any])
    async def workerbee_switch_status(
        request: Request, record=Depends(require_token),
    ) -> dict[str, Any]:
        return await wait_workerbee_thread(_runtime_view, request, record, "agent-pbx.workerbee-switch/v1")

    @app.post(
        "/v1/workerbee/switch/preview",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def preview_workerbee_switch(
        payload: WorkerBeeSwitchPreviewRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            result = await wait_workerbee_thread(
                request.app.state.workerbee_switcher.preview,
                payload.target,
                controller=controller.token_id or controller.role,
            )
            return switch_record(result, include_preview=True) or {}
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/workerbee/switch/apply",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def apply_workerbee_switch(
        payload: WorkerBeeSwitchApplyRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            result = await wait_workerbee_thread(
                request.app.state.workerbee_switcher.apply,
                payload.transaction_id,
                preview_token=payload.preview_token,
                confirm_target=payload.confirm_target,
                confirm_privileged=payload.confirm_privileged,
                controller=controller.token_id or controller.role,
            )
            return switch_record(result) or {}
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/workerbee/switch/rollback",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def rollback_workerbee_switch(
        payload: WorkerBeeSwitchRollbackRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            result = await wait_workerbee_thread(
                request.app.state.workerbee_switcher.rollback,
                payload.transaction_id,
                confirm=payload.confirm,
                controller=controller.token_id or controller.role,
            )
            return switch_record(result) or {}
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
