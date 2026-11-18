from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


WORKERBEE_CONTROLLER_METADATA_KEYS = (
    "workerbee_mcp_profile",
    "workerbee_mcp_profile_updated_by",
    "workerbee_profile_intent",
    "workerbee_policy_updated_by",
    "workerbee_access_profile_ceiling",
    "workerbee_source_mode",
    "workerbee_execution_lane",
    "workerbee_gateway_lease_id",
    "workerbee_workspace_binding_id",
    "workerbee_workspace_project",
    "workerbee_workspace_working_subpath",
    "workerbee_workspace_source_root",
    "workerbee_workspace_previous_binding_id",
    "workerbee_workspace_refreshed_at",
    "workerbee_workspace_scenario",
    "workerbee_bound_backend",
    "workerbee_backend",
    "workerbee_backend_generation",
    "workerbee_binding_state",
    "workerbee_path_domain_receipt_sha256",
    "workerbee_workspace_policy_digest",
    "workerbee_host_source_root",
    "workerbee_allowed_host_root",
    "workerbee_container_target_root",
    "workerbee_runtime_workspace_path",
)
WORKERBEE_CONTROLLER_AUTHORITY_MARKERS = (
    "workerbee_gateway_lease_id",
    "workerbee_profile_intent",
    "workerbee_mcp_profile_updated_by",
    "workerbee_policy_updated_by",
    "workerbee_path_domain_receipt_sha256",
)


class ControllerMetadataPatch(dict[str, Any]):
    """Metadata values plus controller-authorized key removals."""

    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        clear_keys: Iterable[str] = (),
    ) -> None:
        super().__init__(values)
        self.clear_keys = tuple(str(key) for key in clear_keys)


def controller_metadata_patch(
    values: Mapping[str, Any],
    *,
    clear_keys: Iterable[str] = (),
) -> ControllerMetadataPatch:
    return ControllerMetadataPatch(values, clear_keys=clear_keys)


def merge_controller_metadata(
    existing: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge non-null values and apply explicit controller removals."""

    merged = dict(existing)
    for key, value in patch.items():
        if value is None:
            continue
        if key == "cwd" and not str(value or "").strip() and str(
            merged.get("cwd") or ""
        ).strip():
            continue
        merged[key] = value
    for key in getattr(patch, "clear_keys", ()):
        merged.pop(str(key), None)
    return merged


def protect_workerbee_registration_metadata(
    existing: Mapping[str, Any],
    requested: dict[str, Any],
) -> None:
    """Prevent launch observations from replacing controller authority."""

    if not any(existing.get(key) for key in WORKERBEE_CONTROLLER_AUTHORITY_MARKERS):
        return
    for key in WORKERBEE_CONTROLLER_METADATA_KEYS:
        requested.pop(key, None)
