from __future__ import annotations

from functools import wraps
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from ..auth import ClientPrincipal, require_controller, require_global_controller, require_token
from ..schemas import WorkerBeeSwitchApplyRequest, WorkerBeeSwitchPreviewRequest, WorkerBeeSwitchRollbackRequest
from ..workerbee_cutover import WorkerBeeCutoverError, wait_workerbee_thread


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


def register_workerbee_switch_routes(app: FastAPI) -> None:
    @app.exception_handler(WorkerBeeCutoverError)
    async def workerbee_cutover_error(_request: Request, exc: WorkerBeeCutoverError):
        return JSONResponse({"detail": exc.public_dict()}, status_code=409)

    @app.get(
        "/v1/workerbee/switch",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def workerbee_switch_status(request: Request) -> dict[str, Any]:
        return await wait_workerbee_thread(request.app.state.workerbee_switcher.status)

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
            return await wait_workerbee_thread(
                request.app.state.workerbee_switcher.preview,
                payload.target,
                controller=controller.token_id or controller.role,
            )
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
            return await wait_workerbee_thread(
                request.app.state.workerbee_switcher.apply,
                payload.transaction_id,
                preview_token=payload.preview_token,
                confirm_target=payload.confirm_target,
                confirm_privileged=payload.confirm_privileged,
                controller=controller.token_id or controller.role,
            )
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
            return await wait_workerbee_thread(
                request.app.state.workerbee_switcher.rollback,
                payload.transaction_id,
                confirm=payload.confirm,
                controller=controller.token_id or controller.role,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
