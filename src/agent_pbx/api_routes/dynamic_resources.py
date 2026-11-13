"""Typed dynamic-resource broker HTTP routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..api_services import ApiServices
from ..auth import ClientPrincipal, require_global_controller
from ..schemas import (
    DynamicResourceApprovalRequest,
    DynamicResourceCreateRequest,
    DynamicResourceOperatorRequest,
    DynamicResourceReleaseRequest,
    DynamicResourceUseRequest,
)


OperatorCall = Callable[..., Awaitable[Any]]


def register_dynamic_resource_routes(
    app: FastAPI,
    services: ApiServices,
    run_operator_call: OperatorCall,
) -> None:
    """Register dynamic-resource routes without moving broker authority."""

    def require_dynamic_resources_enabled() -> None:
        if not services.config.dynamic_resources_enabled:
            raise HTTPException(
                status_code=409,
                detail="dynamic resource broker is disabled",
            )

    @app.get("/v1/operator/resources")
    async def list_dynamic_resources(
        request: Request,
        operator_agent_id: str = Query(min_length=1, max_length=120),
        status: str | None = Query(default=None, max_length=40),
        campaign_id: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=200, ge=1, le=1000),
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> list[dict[str, Any]]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).list_dynamic_resources,
            operator_agent_id=operator_agent_id,
            status=status,
            campaign_id=campaign_id,
            limit=limit,
        )

    @app.post("/v1/operator/resources/request")
    async def request_dynamic_resource(
        payload: DynamicResourceCreateRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).request_dynamic_resource,
            **payload.model_dump(),
        )

    @app.post("/v1/operator/resources/{request_id}/approve")
    async def approve_dynamic_resource(
        request_id: str,
        payload: DynamicResourceApprovalRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).decide_dynamic_resource,
            request_id=request_id,
            **payload.model_dump(),
        )

    @app.post("/v1/operator/resources/{request_id}/acquire")
    async def acquire_dynamic_resource(
        request_id: str,
        payload: DynamicResourceOperatorRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).acquire_dynamic_resource,
            request_id=request_id,
            operator_agent_id=payload.operator_agent_id,
        )

    @app.post("/v1/operator/resources/{request_id}/use")
    async def use_dynamic_resource(
        request_id: str,
        payload: DynamicResourceUseRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).use_dynamic_resource,
            request_id=request_id,
            **payload.model_dump(),
        )

    @app.post("/v1/operator/resources/{request_id}/release")
    async def release_dynamic_resource(
        request_id: str,
        payload: DynamicResourceReleaseRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).release_dynamic_resource,
            request_id=request_id,
            **payload.model_dump(),
        )

    @app.post("/v1/operator/resources/{request_id}/recover")
    async def recover_dynamic_resource(
        request_id: str,
        payload: DynamicResourceOperatorRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_dynamic_resources_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).recover_dynamic_resource,
            request_id=request_id,
            operator_agent_id=payload.operator_agent_id,
        )
