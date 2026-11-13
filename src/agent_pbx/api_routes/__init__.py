"""Low-coupling HTTP route registrars for the Agent PBX composition root."""

from ..api_services import ApiServices
from ..evidence_export import EvidenceExportService
from .durable_helpers import register_durable_helper_routes
from .dynamic_resources import register_dynamic_resource_routes
from .evidence import register_evidence_routes
from .operator_kb_v2 import register_operator_kb_v2_routes


__all__ = (
    "ApiServices",
    "EvidenceExportService",
    "register_durable_helper_routes",
    "register_dynamic_resource_routes",
    "register_evidence_routes",
    "register_operator_kb_v2_routes",
)
