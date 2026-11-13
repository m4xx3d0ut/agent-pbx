"""Protected-provider metadata diagnostic HTTP route."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from ..api_services import ApiServices
from ..auth import ClientPrincipal, require_global_controller
from ..protected_provider import ProtectedProviderDiagnosticError
from ..schemas import ProtectedProviderDiagnosticRequest


def register_protected_provider_routes(
    app: FastAPI,
    services: ApiServices,
) -> None:
    """Register the read-only diagnostic without moving provider authority."""

    @app.post(
        "/v2/operator/protected-provider/diagnose",
        response_model=dict[str, Any],
    )
    async def diagnose_protected_provider_merge(
        payload: ProtectedProviderDiagnosticRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        """Reconcile approved metadata without invoking a provider or Git write."""

        try:
            return await asyncio.to_thread(
                services.protected_provider_diagnostics_for(request.app).diagnose,
                **payload.model_dump(),
            )
        except ProtectedProviderDiagnosticError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc
