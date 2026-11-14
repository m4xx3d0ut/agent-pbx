"""Shared WorkerBee TUI profile metadata and reviewed tool surfaces."""

from ..workerbee_modes import WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE


CALLER_WORKERBEE_PROFILE_METADATA_KEY = "workerbee_mcp_profile"

REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS = (
    "workerbee_v1_capabilities",
    WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
    "workerbee_v1_session_start",
    "workerbee_v1_project_runbook_status",
    "workerbee_v1_project_status",
    "workerbee_v1_secret_policy_status",
    "workerbee_v1_profile_workload_status",
    "workerbee_v1_edge_link_status",
    "workerbee_v1_logs",
)


__all__ = [
    "CALLER_WORKERBEE_PROFILE_METADATA_KEY",
    "REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS",
]
