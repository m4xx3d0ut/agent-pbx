"""Shared WorkerBee TUI profile metadata and reviewed tool surfaces."""

from ..workerbee_modes import (
    WORKERBEE_PROFILE_REVIEW,
    tools_for_access_profile,
)


CALLER_WORKERBEE_PROFILE_METADATA_KEY = "workerbee_mcp_profile"

REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS = tools_for_access_profile(
    WORKERBEE_PROFILE_REVIEW
)


__all__ = [
    "CALLER_WORKERBEE_PROFILE_METADATA_KEY",
    "REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS",
]
