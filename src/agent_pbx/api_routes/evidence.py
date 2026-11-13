"""Evidence-export HTTP routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from ..api_services import ApiServices
from ..auth import ClientPrincipal, require_global_controller
from ..evidence_export import EvidenceExportError
from ..schemas import (
    EvidenceExportRequest,
    EvidenceRetentionPreviewRequest,
    EvidenceVerifyRequest,
)


def register_evidence_routes(app: FastAPI, services: ApiServices) -> None:
    """Register the unchanged evidence-export controller contract."""

    async def evidence_call(method: str, **kwargs: Any) -> dict[str, Any]:
        service = services.evidence_exports_for(app)
        try:
            return await asyncio.to_thread(getattr(service, method), **kwargs)
        except EvidenceExportError as exc:
            detail = str(exc)
            code = 409 if "disabled" in detail or "drift" in detail else 422
            raise HTTPException(status_code=code, detail=detail) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence export not found") from exc

    @app.post("/v1/evidence/preview")
    async def preview_evidence_export(
        payload: EvidenceExportRequest,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await evidence_call("preview", **payload.model_dump())

    @app.post("/v1/evidence/exports")
    async def create_evidence_export(
        payload: EvidenceExportRequest,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await evidence_call("export", **payload.model_dump())

    @app.post("/v1/evidence/verify")
    async def verify_evidence_export(
        payload: EvidenceVerifyRequest,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await evidence_call("verify", export_id=payload.export_id)

    @app.post("/v1/evidence/retention/preview")
    async def preview_evidence_retention(
        payload: EvidenceRetentionPreviewRequest,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await evidence_call("retention_preview", older_than=payload.older_than)

    @app.post("/v1/evidence/{export_id}/joplin-render")
    async def render_evidence_for_joplin(
        export_id: str,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        return await evidence_call("render_joplin", export_id=export_id)
