from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .store import Store
from .workerbee import WorkerBeeStatusService, agent_cwd
from .workerbee_access import (
    WorkerBeeAccessPolicyError,
    WorkerBeeAccessPolicyService,
)
from .workerbee_cutover import wait_workerbee_thread
from .workerbee_gateway import (
    WorkerBeeGatewayError,
    WorkerBeeGatewayService,
)
from .workerbee_policy import WorkerBeeWorkspacePolicyService
from .workerbee_projection import (
    WorkerBeeBindingProjectionService,
    durable_workerbee_profile,
    finalize_issued_workerbee_lease as finalize_issued_workerbee_lease,
    reconcile_workerbee_gateway_startup,
)
from .workerbee_runtime import WorkerBeeRuntimeRegistry
from .workerbee_snapshot_policy import WorkerBeeSnapshotPolicyService
from .workerbee_switcher import WorkerBeeSwitchService
from .workerbee_workspace import (
    WorkerBeeWorkspaceBinding,
    WorkerBeeWorkspaceError,
    WorkerBeeWorkspaceService,
)


WorkerBeeMcpCall = Callable[
    [str, str, dict[str, Any]],
    Awaitable[dict[str, Any]],
]


class WorkerBeeApplicationService:
    """Application-level WorkerBee selection, status, and policy facade."""

    def __init__(
        self,
        store: Store,
        *,
        status_service: Callable[[], WorkerBeeStatusService],
        runtime_registry: Callable[[], WorkerBeeRuntimeRegistry],
        workspace_service: Callable[[], WorkerBeeWorkspaceService],
        workspace_policy: Callable[[], WorkerBeeWorkspacePolicyService],
        snapshot_policy: Callable[[], WorkerBeeSnapshotPolicyService],
        access_policy: Callable[[], WorkerBeeAccessPolicyService],
        gateway: Callable[[], WorkerBeeGatewayService],
        switcher: Callable[[], WorkerBeeSwitchService],
        call_mcp_tool: WorkerBeeMcpCall,
    ) -> None:
        self.store = store
        self._status_service = status_service
        self._runtime_registry = runtime_registry
        self._workspace_service = workspace_service
        self._workspace_policy = workspace_policy
        self._snapshot_policy = snapshot_policy
        self._access_policy = access_policy
        self._gateway = gateway
        self._switcher = switcher
        self._call_mcp_tool = call_mcp_tool

    durable_profile = staticmethod(durable_workerbee_profile)

    def reconcile_gateway_startup(self) -> dict[str, Any]:
        return reconcile_workerbee_gateway_startup(self.store, self._gateway())

    def refresh_runtime_selection(self) -> dict[str, Any]:
        runtime = self._runtime_registry()
        switch_status = runtime.observe_status(self._switcher().status)
        posture = runtime.posture()
        posture["selector"] = switch_status
        posture["gateway_impact"] = self._gateway().impact()
        return posture

    def trusted_direct_config_readback(self) -> dict[str, Any]:
        service = self._workspace_service()
        raw_helper = str(getattr(service, "helper_bin", "") or "")
        helper = bool(raw_helper and Path(raw_helper).is_absolute())
        verifier = getattr(service, "trusted_direct_verifier", None)
        receipt_store = bool(
            verifier is not None
            and Path(getattr(verifier, "receipt_root", "")).is_absolute()
        )
        approved_roots = tuple(
            getattr(service, "trusted_direct_approved_roots", ()) or ()
        )
        roots_valid = bool(approved_roots) and all(
            Path(root).is_absolute() and ".." not in Path(root).parts
            for root in approved_roots
        )
        administrator_ceiling = bool(
            getattr(service, "trusted_direct_administrator_ceiling", False)
        )
        return {
            "configured": helper and receipt_store and roots_valid,
            "enabled": (
                helper and receipt_store and roots_valid and administrator_ceiling
            ),
            "helper_configured": helper,
            "receipt_store_configured": receipt_store,
            "approved_root_count": len(approved_roots),
            "administrator_ceiling": administrator_ceiling,
        }

    def policy_status(self, agent_id: str) -> dict[str, Any]:
        view = self._workspace_policy().get_policy(agent_id)
        effective = view.get("effective") if isinstance(view.get("effective"), dict) else {}
        admin = view.get("admin") if isinstance(view.get("admin"), dict) else {}
        agent = self.store.get_agent(agent_id)
        selected = self._runtime_registry().selected()
        snapshot_view = self._snapshot_policy().get_policy(
            agent_id,
            source_root=(agent_cwd(agent) if agent is not None else None),
            backend=(selected.backend if selected is not None else None),
        )
        snapshot = (
            snapshot_view.get("effective")
            if isinstance(snapshot_view.get("effective"), dict)
            else {}
        )
        snapshot_admin = (
            snapshot_view.get("admin")
            if isinstance(snapshot_view.get("admin"), dict)
            else {}
        )
        try:
            access = self._access_policy().get_profile(agent_id)
        except WorkerBeeAccessPolicyError:
            access = {}
        return {
            "profile_label": effective.get("profile_label"),
            "policy": effective.get("policy"),
            "request_mode": effective.get("request_mode"),
            "allowed_root_count": len(effective.get("allowed_roots") or ()),
            "managed_scratch_configured": bool(
                effective.get("managed_scratch_root")
            ),
            "control_mode": admin.get("control_mode"),
            "constrained": bool(view.get("constrained")),
            "snapshot": {
                "applicable": bool(snapshot_view.get("applicable")),
                "policy": snapshot.get("policy"),
                "approved_path_count": len(snapshot.get("include_paths") or ()),
                "control_mode": snapshot_admin.get("control_mode"),
                "constrained": bool(snapshot_view.get("constrained")),
                "workflow_label": snapshot_view.get("workflow_label"),
            },
            "access": {
                "profile": access.get("effective_profile"),
                "control_mode": access.get("control_mode"),
                "constrained": bool(access.get("constrained")),
                "trusted_direct_bind": self.trusted_direct_config_readback(),
            },
        }

    def access_readback(self, agent_id: str) -> dict[str, Any]:
        selected = self._runtime_registry().selected()
        readback = self._access_policy().get_profile(
            agent_id,
            selected_backend=(selected.backend if selected is not None else None),
        )
        readback["trusted_direct_bind"] = self.trusted_direct_config_readback()
        return readback

    async def selected_status_for_agent(
        self,
        agent: dict[str, Any],
    ) -> dict[str, Any]:
        await wait_workerbee_thread(self.refresh_runtime_selection)
        with self._runtime_registry().admission("selected_status", require_runtime=False):
            runtime = self._runtime_registry().selected()
            return await self._selected_status(agent, runtime.public_dict() if runtime else None)

    async def _selected_status(
        self, agent: dict[str, Any], selected: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if selected is None or selected.get("backend") == "oss":
            result = await wait_workerbee_thread(
                self._status_service().status_for_agent,
                agent,
            )
            result["status_source"] = "selected_oss_cli" if selected else "host_cli"
            result["selected_runtime"] = selected
            result["workspace_binding"] = None
            result["workspace_policy"] = self.policy_status(
                str(agent.get("agent_id") or "")
            )
            return result

        agent_id = str(agent.get("agent_id") or "")
        project = str(agent.get("project") or "")
        cwd = agent_cwd(agent)
        metadata = (
            agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        )
        binding_id = str(
            metadata.get("workerbee_workspace_binding_id") or ""
        ).strip()
        lease_id = str(metadata.get("workerbee_gateway_lease_id") or "").strip()
        selected_binding: WorkerBeeWorkspaceBinding | None = None
        lease_error: WorkerBeeGatewayError | None = None
        if lease_id:
            try:
                lease, selected_binding = await wait_workerbee_thread(
                    self._gateway().current_binding,
                    lease_id,
                )
            except WorkerBeeGatewayError as exc:
                lease = None
                lease_error = exc
            if lease is not None and lease.agent_id == agent_id:
                binding_id = lease.workspace_binding_id
            elif lease is not None:
                lease_error = WorkerBeeGatewayError(
                    "WORKERBEE_BINDING_AGENT_MISMATCH",
                    "WorkerBee gateway lease belongs to a different Agent.",
                )
        base: dict[str, Any] = {
            "configured": True,
            "available": False,
            "agent_id": agent_id,
            "agent_project": project or None,
            "cwd": cwd,
            "workerbee_bin": None,
            "checked_at": time.time(),
            "project": project or None,
            "mode": None,
            "running": None,
            "status_kind": None,
            "dashboard_url": None,
            "state_dir": None,
            "description": None,
            "error": None,
            "project_status": None,
            "project_card": None,
            "global_dashboard": None,
            "dashboard_error": None,
            "app_status": None,
            "latest_deployment": None,
            "status_source": "selected_mcp",
            "selected_runtime": selected,
            "workspace_binding": None,
            "workspace_policy": self.policy_status(agent_id),
        }
        if lease_error is not None:
            base["error"] = lease_error.public_dict()
            return base
        if not binding_id:
            base["error"] = {
                "code": "WORKERBEE_WORKSPACE_BINDING_REQUIRED",
                "message": "This session has not been bound to the selected WorkerBee appliance workspace.",
                "remediation": "Restart or resume the session through Agent PBX to prepare a verified workspace binding.",
            }
            return base
        try:
            binding = selected_binding or await wait_workerbee_thread(
                self._workspace_service().load,
                binding_id,
            )
            runtime = self._runtime_registry().selected()
            if runtime is None:
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_RUNTIME_UNSELECTED",
                    "No verified WorkerBee runtime is selected.",
                )
            await wait_workerbee_thread(
                self._workspace_service().verify_runtime,
                binding,
                runtime,
            )
            base["workspace_binding"] = binding.public_dict()
            base["project"] = binding.project
            payload = await self._call_mcp_tool(
                runtime.endpoint,
                "workerbee_v1_project_status",
                {"project": binding.project},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            project_status = (
                data.get("project_status")
                if isinstance(data.get("project_status"), dict)
                else data
            )
            app_status = (
                project_status.get("app_status")
                if isinstance(project_status.get("app_status"), dict)
                else data.get("app_status")
            )
            base.update(
                available=True,
                mode=data.get("mode"),
                running=project_status.get("running"),
                status_kind=(
                    data.get("status_kind") or project_status.get("status_kind")
                ),
                dashboard_url=data.get("dashboard_url"),
                state_dir=project_status.get("state_dir"),
                description=(
                    app_status.get("message")
                    if isinstance(app_status, dict)
                    else None
                ),
                project_status=project_status,
                app_status=app_status if isinstance(app_status, dict) else None,
                latest_deployment=project_status.get("latest_deployment"),
            )
        except (WorkerBeeWorkspaceError, OSError, RuntimeError, ValueError) as exc:
            base["error"] = {
                "code": str(
                    getattr(exc, "code", "WORKERBEE_SELECTED_STATUS_FAILED")
                ),
                "message": str(exc),
                "remediation": str(
                    getattr(
                        exc,
                        "remediation",
                        "Retry after verifying the selected backend and binding.",
                    )
                ),
            }
        return base


__all__ = (
    "WorkerBeeApplicationService",
    "WorkerBeeBindingProjectionService",
    "durable_workerbee_profile",
)
