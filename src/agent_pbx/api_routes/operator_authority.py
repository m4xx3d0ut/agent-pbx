from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from ..auth import ClientPrincipal, get_store, require_controller
from ..operator_authority import OperatorAuthorityError
from ..remote import principal_allows_agent
from ..schemas import OperatorAuthorityAssignmentRequest
from ..workerbee_access import WorkerBeeAccessPolicyError
from ..workerbee_policy import WorkerBeeWorkspacePolicyError
from ._shared import require_agent


def register_operator_authority_routes(app: FastAPI) -> None:
    @app.get(
        "/v2/agents/{agent_id}/operator-authority",
        response_model=dict[str, Any],
    )
    async def get_agent_operator_authority(
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
            return await asyncio.to_thread(
                request.app.state.operator_authority.get_assignment,
                agent_id,
            )
        except OperatorAuthorityError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.put(
        "/v2/agents/{agent_id}/operator-authority",
        response_model=dict[str, Any],
    )
    async def put_agent_operator_authority(
        agent_id: str,
        payload: OperatorAuthorityAssignmentRequest,
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
            return await asyncio.to_thread(
                request.app.state.operator_authority.set_assignment,
                agent_id,
                profile=payload.profile,
                workspace_set_id=payload.workspace_set_id,
                profile_label=payload.profile_label,
                updated_by=controller.token_id or controller.client_id or "local",
            )
        except (
            OperatorAuthorityError,
            WorkerBeeAccessPolicyError,
            WorkerBeeWorkspacePolicyError,
        ) as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.post(
        "/v2/agents/{agent_id}/operator-authority/activated",
        response_model=dict[str, Any],
    )
    async def activate_agent_operator_authority(
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
            return await asyncio.to_thread(
                request.app.state.operator_authority.mark_activated,
                agent_id,
            )
        except OperatorAuthorityError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc


__all__ = ("register_operator_authority_routes",)
