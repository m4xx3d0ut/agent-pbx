"""Typed references owned by the Agent PBX API composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .config import ServerConfig
from .durable_helpers import DurableHelperRegistry
from .dynamic_resources import DynamicResourceBroker
from .evidence_export import EvidenceExportService
from .operator import OperatorService
from .protected_provider import ProtectedProviderDiagnosticService


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Services constructed once by :func:`agent_pbx.api.create_app`.

    The legacy ``app.state`` names remain the runtime compatibility surface.
    Accessors intentionally prefer those names so an established test or local
    extension that replaces one service continues to affect the same routes.
    """

    config: ServerConfig
    evidence_exports: EvidenceExportService
    operator_service: OperatorService
    durable_helpers: DurableHelperRegistry
    dynamic_resources: DynamicResourceBroker
    protected_provider_diagnostics: ProtectedProviderDiagnosticService

    @staticmethod
    def _current(app: Any, name: str, fallback: Any) -> Any:
        return getattr(app.state, name, fallback)

    def evidence_exports_for(self, app: Any) -> EvidenceExportService:
        return cast(
            EvidenceExportService,
            self._current(app, "evidence_exports", self.evidence_exports),
        )

    def operator_service_for(self, app: Any) -> OperatorService:
        return cast(
            OperatorService,
            self._current(app, "operator_service", self.operator_service),
        )

    def protected_provider_diagnostics_for(
        self,
        app: Any,
    ) -> ProtectedProviderDiagnosticService:
        return cast(
            ProtectedProviderDiagnosticService,
            self._current(
                app,
                "protected_provider_diagnostics",
                self.protected_provider_diagnostics,
            ),
        )
