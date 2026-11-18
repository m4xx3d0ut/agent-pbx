from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .controller_metadata import controller_metadata_patch
from .store import Store
from .workerbee_gateway import (
    WorkerBeeGatewayError,
    WorkerBeeGatewayLease,
    WorkerBeeGatewayService,
)
from .workerbee_modes import WORKERBEE_PROFILE_TRUSTED_WORKBENCH
from .workerbee_workspace import WorkerBeeWorkspaceBinding


WORKERBEE_BINDING_TRANSITION_METADATA_KEYS = (
    "workerbee_workspace_previous_binding_id",
    "workerbee_workspace_refreshed_at",
    "workerbee_workspace_scenario",
)


def workerbee_binding_projection(
    lease: WorkerBeeGatewayLease,
    binding: WorkerBeeWorkspaceBinding,
) -> dict[str, Any]:
    """Return the canonical durable metadata for one verified lease binding."""

    receipt = binding.path_domain
    return {
        "workerbee_gateway_lease_id": lease.lease_id,
        "workerbee_workspace_binding_id": binding.binding_id,
        "workerbee_workspace_project": lease.project,
        "workerbee_workspace_working_subpath": lease.working_subpath,
        "workerbee_workspace_source_root": binding.source_root,
        "workerbee_bound_backend": lease.backend,
        "workerbee_backend": lease.backend,
        "workerbee_backend_generation": lease.backend_generation,
        "workerbee_binding_state": binding.state,
        "workerbee_path_domain_receipt_sha256": receipt.receipt_sha256,
        "workerbee_workspace_policy_digest": receipt.workspace_policy_digest,
        "workerbee_host_source_root": receipt.host_source_root,
        "workerbee_allowed_host_root": receipt.allowed_host_root,
        "workerbee_container_target_root": receipt.container_target_root,
        "workerbee_runtime_workspace_path": receipt.runtime_workspace_path,
    }


def durable_workerbee_profile(metadata: Mapping[str, Any]) -> str:
    intent = (
        metadata.get("workerbee_profile_intent")
        if isinstance(metadata.get("workerbee_profile_intent"), dict)
        else {}
    )
    return str(
        intent.get("profile") or metadata.get("workerbee_mcp_profile") or ""
    ).strip()


class WorkerBeeBindingProjectionService:
    """Project a verified gateway binding into durable Agent metadata."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def reconcile(
        self,
        lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
        *,
        event_type: str,
        extra_metadata: Mapping[str, Any] | None = None,
        extra_event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent = self.store.get_agent(lease.agent_id)
        if agent is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_BINDING_AGENT_MISSING",
                "The active WorkerBee lease Agent is no longer registered.",
            )
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        if str(metadata.get("workerbee_gateway_lease_id") or "").strip() != lease.lease_id:
            raise WorkerBeeGatewayError(
                "WORKERBEE_BINDING_LEASE_DRIFT",
                "Durable Agent metadata names a different WorkerBee gateway lease.",
                "Reconcile the current launch intent and lease before using WorkerBee.",
            )
        receipt = binding.path_domain
        recorded_policy_digest = str(
            metadata.get("workerbee_workspace_policy_digest") or ""
        ).strip()
        if (
            durable_workerbee_profile(metadata) == WORKERBEE_PROFILE_TRUSTED_WORKBENCH
            and recorded_policy_digest
            and recorded_policy_digest != receipt.workspace_policy_digest
        ):
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_POLICY_DIGEST_DRIFT",
                "Durable WorkerBee workspace-policy authority differs from the active binding.",
                "Issue a fresh binding and lease under the current workspace policy.",
            )
        projected = workerbee_binding_projection(lease, binding)
        supplemental = dict(extra_metadata or {})
        clear_keys = tuple(key for key, value in supplemental.items() if value is None)
        projected.update(
            {key: value for key, value in supplemental.items() if value is not None}
        )
        changed = bool(clear_keys) or not all(
            metadata.get(key) == value for key, value in projected.items()
        )
        patch = controller_metadata_patch(projected, clear_keys=clear_keys)
        reconciled = self.store.merge_agent_metadata(
            lease.agent_id,
            patch,
            event_type=event_type if changed else None,
            event_payload={
                "agent_id": lease.agent_id,
                "lease_id": lease.lease_id,
                "workspace_binding_id": binding.binding_id,
                "backend": lease.backend,
                "backend_generation": lease.backend_generation,
                "path_domain_receipt_sha256": receipt.receipt_sha256,
                "workspace_policy_digest": receipt.workspace_policy_digest,
                **dict(extra_event_payload or {}),
            },
        )
        if reconciled is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_BINDING_AGENT_MISSING",
                "The active WorkerBee lease Agent disappeared during reconciliation.",
            )
        self.store.merge_operator_fork_metadata(lease.agent_id, patch)
        return self.store.get_agent(lease.agent_id) or reconciled

    def record_workspace_refresh(
        self,
        previous_lease: WorkerBeeGatewayLease,
        refreshed_lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
    ) -> None:
        refreshed_at = time.time()
        self.reconcile(
            refreshed_lease,
            binding,
            event_type="workerbee_workspace_refreshed",
            extra_metadata={
                "workerbee_workspace_previous_binding_id": previous_lease.workspace_binding_id,
                "workerbee_workspace_refreshed_at": refreshed_at,
                "workerbee_workspace_source_root": binding.source_root,
                "workerbee_workspace_working_subpath": refreshed_lease.working_subpath,
                "workerbee_workspace_project": refreshed_lease.project,
                "workerbee_workspace_scenario": refreshed_lease.scenario_label,
            },
            extra_event_payload={
                "previous_binding_id": previous_lease.workspace_binding_id,
                "project": binding.project,
                "source_project": refreshed_lease.source_project,
                "working_subpath": refreshed_lease.working_subpath,
                "scenario_label": refreshed_lease.scenario_label,
                "source_commit": binding.source_commit,
                "source_dirty": binding.source_dirty,
                "refreshed_at": refreshed_at,
            },
        )

    def record_binding_validation(
        self,
        lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
    ) -> None:
        self.reconcile(lease, binding, event_type="workerbee_binding_reconciled")


def reconcile_workerbee_gateway_startup(
    store: Store,
    gateway: WorkerBeeGatewayService,
) -> dict[str, Any]:
    """Revoke active leases that no durable Agent record still references."""

    reconcile = getattr(gateway, "reconcile_active_references", None)
    if not callable(reconcile):
        return {"supported": False, "kept_count": 0, "revoked_count": 0}
    references = {
        str(agent.get("agent_id") or "").strip(): str(
            (
                agent.get("metadata")
                if isinstance(agent.get("metadata"), dict)
                else {}
            ).get("workerbee_gateway_lease_id")
            or ""
        ).strip()
        for agent in store.list_agents(include_hidden=True)
    }
    result = reconcile(references)
    if int(result.get("revoked_count") or 0):
        store.append_event(
            "workerbee_gateway_startup_reconciled",
            result,
            "workerbee-gateway",
        )
    return result


def finalize_issued_workerbee_lease(
    store: Store,
    gateway: WorkerBeeGatewayService,
    lease: WorkerBeeGatewayLease,
    binding: WorkerBeeWorkspaceBinding | None,
) -> dict[str, Any] | None:
    """Persist one issued lease, then revoke only its durable predecessors."""

    updated = None
    if binding is not None:
        projection = workerbee_binding_projection(lease, binding)
        projection["workerbee_mcp_profile"] = lease.profile
        patch = controller_metadata_patch(
            projection,
            clear_keys=WORKERBEE_BINDING_TRANSITION_METADATA_KEYS,
        )
        updated = store.merge_agent_metadata(
            lease.agent_id,
            patch,
            event_type="workerbee_gateway_lease_issued",
            event_payload={
                "agent_id": lease.agent_id,
                "lease_id": lease.lease_id,
                "workspace_binding_id": lease.workspace_binding_id,
                "backend": lease.backend,
                "backend_generation": lease.backend_generation,
                "profile": lease.profile,
                "path_domain_receipt_sha256": binding.path_domain.receipt_sha256,
                "workspace_policy_digest": binding.path_domain.workspace_policy_digest,
            },
        )
        if updated is None:
            gateway.revoke(lease.lease_id)
            raise WorkerBeeGatewayError(
                "WORKERBEE_ACCESS_AGENT_NOT_FOUND",
                "Agent disappeared while persisting its WorkerBee lease.",
                "Register the Agent and prepare a fresh lease.",
            )
        store.merge_operator_fork_metadata(lease.agent_id, patch)
    revoked = gateway.revoke_superseded_for_agent(
        lease.agent_id,
        keep_lease_id=lease.lease_id,
    )
    if revoked:
        store.append_event(
            "workerbee_gateway_predecessors_revoked",
            {
                "agent_id": lease.agent_id,
                "current_lease_id": lease.lease_id,
                "revoked_lease_ids": [item.lease_id for item in revoked],
                "revoked_count": len(revoked),
            },
            lease.agent_id,
        )
    return updated
