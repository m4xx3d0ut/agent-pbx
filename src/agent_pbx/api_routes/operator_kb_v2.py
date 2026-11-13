"""Operator KB bundle-v2 synchronization HTTP routes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from ..api_services import ApiServices
from ..auth import ClientPrincipal, require_global_controller
from ..schemas import OperatorKbBundleV2ExportRequest, OperatorKbBundleV2Request


OperatorCall = Callable[..., Awaitable[Any]]


def register_operator_kb_v2_routes(
    app: FastAPI,
    services: ApiServices,
    run_operator_call: OperatorCall,
) -> None:
    """Register KB-v2 routes with their existing root-owned service boundary."""

    def require_kb_bundle_v2_enabled() -> None:
        if not services.config.operator_kb_bundle_v2_enabled:
            raise HTTPException(
                status_code=409,
                detail="Operator KB bundle v2 synchronization is disabled",
            )

    @app.post("/v1/operator/kb/bundle-v2/export")
    async def export_operator_kb_bundle_v2(
        payload: OperatorKbBundleV2ExportRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_kb_bundle_v2_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).export_kb_bundle_v2,
            operator_agent_id=payload.operator_agent_id,
            project=payload.project,
            repo_root=payload.repo_root,
            git_remote=payload.git_remote,
            parent_bundle_sha256=payload.parent_bundle_sha256,
            limit=payload.limit,
        )

    @app.post("/v1/operator/kb/bundle-v2/preview")
    async def preview_operator_kb_bundle_v2(
        payload: OperatorKbBundleV2Request,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_kb_bundle_v2_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).preview_kb_bundle_v2,
            operator_agent_id=payload.operator_agent_id,
            bundle=payload.bundle,
        )

    @app.post("/v1/operator/kb/bundle-v2/import")
    async def import_operator_kb_bundle_v2(
        payload: OperatorKbBundleV2Request,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_kb_bundle_v2_enabled()
        return await run_operator_call(
            services.operator_service_for(request.app).import_kb_bundle_v2,
            operator_agent_id=payload.operator_agent_id,
            bundle=payload.bundle,
        )

    @app.post("/v1/operator/kb/bundle-v2/git-tree")
    async def prepare_operator_kb_bundle_v2_git_tree(
        payload: OperatorKbBundleV2Request,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        require_kb_bundle_v2_enabled()
        return await run_operator_call(
            services.operator_service_for(
                request.app
            ).prepare_kb_bundle_v2_git_tree,
            operator_agent_id=payload.operator_agent_id,
            bundle=payload.bundle,
        )
