"""Durable-helper registry HTTP routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..api_services import ApiServices
from ..auth import ClientPrincipal, require_global_controller
from ..schemas import (
    DurableHelperImportRequest,
    DurableHelperInvocationRequest,
    DurableHelperOperatorRequest,
    DurableHelperProposalRequest,
    DurableHelperTransitionRequest,
)


OperatorCall = Callable[..., Awaitable[Any]]


def register_durable_helper_routes(
    app: FastAPI,
    services: ApiServices,
    run_operator_call: OperatorCall,
) -> None:
    """Register durable-helper routes with their existing gates and executor."""

    def require_durable_helpers_enabled() -> None:
        if not services.config.durable_helpers_enabled:
            raise HTTPException(
                status_code=409,
                detail="durable helper registry is disabled",
            )

    @app.get("/v1/operator/helpers")
    async def list_durable_helpers(
        request: Request,
        operator_agent_id: str = Query(min_length=1, max_length=120),
        status: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=200, ge=1, le=1000),
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> list[dict[str, Any]]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).list_durable_helpers,
            operator_agent_id=operator_agent_id,
            status=status,
            limit=limit,
        )

    @app.post("/v1/operator/helpers/propose")
    async def propose_durable_helper(
        payload: DurableHelperProposalRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).propose_durable_helper,
            operator_agent_id=payload.operator_agent_id,
            source_path=payload.source_path,
            name=payload.name,
            scopes=payload.scopes,
            kb_refs=payload.kb_refs,
        )

    @app.post("/v1/operator/helpers/{helper_id}/validate")
    async def validate_durable_helper(
        helper_id: str,
        payload: DurableHelperOperatorRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).validate_durable_helper,
            operator_agent_id=payload.operator_agent_id,
            helper_id=helper_id,
        )

    @app.post("/v1/operator/helpers/{helper_id}/transition")
    async def transition_durable_helper(
        helper_id: str,
        payload: DurableHelperTransitionRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).transition_durable_helper,
            operator_agent_id=payload.operator_agent_id,
            helper_id=helper_id,
            target_status=payload.target_status,
        )

    @app.post("/v1/operator/helpers/{helper_id}/export")
    async def export_durable_helper(
        helper_id: str,
        payload: DurableHelperOperatorRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).export_durable_helper,
            operator_agent_id=payload.operator_agent_id,
            helper_id=helper_id,
        )

    @app.post("/v1/operator/helpers/import")
    async def import_durable_helper(
        payload: DurableHelperImportRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).import_durable_helper,
            operator_agent_id=payload.operator_agent_id,
            bundle=payload.bundle,
        )

    @app.post("/v1/operator/helpers/{helper_id}/invocation")
    async def prepare_durable_helper_invocation(
        helper_id: str,
        payload: DurableHelperInvocationRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(
                request.app
            ).prepare_durable_helper_invocation,
            operator_agent_id=payload.operator_agent_id,
            helper_id=helper_id,
            scope=payload.scope,
        )

    @app.get("/v1/operator/helpers/measurements")
    async def durable_helper_measurements(
        request: Request,
        operator_agent_id: str = Query(min_length=1, max_length=120),
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_durable_helpers_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).durable_helper_measurements,
            operator_agent_id=operator_agent_id,
        )
