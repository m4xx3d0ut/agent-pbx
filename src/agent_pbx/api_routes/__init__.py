"""Low-coupling HTTP route registrars for the Agent PBX composition root."""

from ..api_services import ApiServices
from ..evidence_export import EvidenceExportService
from ._shared import (
    append_joplin_command_log,
    require_agent,
    run_issue_call,
    run_pull_request_call,
)
from .agent_lifecycle import register_agent_lifecycle_routes
from .commands import register_command_routes
from .durable_helpers import register_durable_helper_routes
from .dynamic_resources import register_dynamic_resource_routes
from .evidence import register_evidence_routes
from .file_editor import register_file_editor_routes
from .github import register_github_routes
from .joplin import register_joplin_routes
from .operator_kb_v2 import register_operator_kb_v2_routes
from .operator import register_operator_routes, run_operator_call
from .operator_authority import register_operator_authority_routes
from .protected_provider import register_protected_provider_routes
from .runtime import register_runtime_routes
from .remote_events import register_remote_event_routes
from .workerbee import register_workerbee_routes


__all__ = (
    "ApiServices",
    "EvidenceExportService",
    "append_joplin_command_log",
    "require_agent",
    "register_agent_lifecycle_routes",
    "register_command_routes",
    "register_durable_helper_routes",
    "register_dynamic_resource_routes",
    "register_evidence_routes",
    "register_file_editor_routes",
    "register_github_routes",
    "register_joplin_routes",
    "register_operator_kb_v2_routes",
    "register_operator_routes",
    "register_operator_authority_routes",
    "register_protected_provider_routes",
    "register_runtime_routes",
    "register_remote_event_routes",
    "register_workerbee_routes",
    "run_issue_call",
    "run_operator_call",
    "run_pull_request_call",
)
