from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import __version__
from .auth import (
    require_token,
)
from .config import OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV, ServerConfig
from .compat import compatibility_posture
from .codex_cli import inspect_codex_model_catalog, update_codex_cli_package
from .codex_config import load_codex_config_view, patch_codex_config
from .codex.capabilities import CodexCapabilityProbe
from .codex.event_bridge import CodexAppServerEventBridge
from .codex.skills import ManagedSkillPackService
from .debug_smoke import DebugSmokeConfig, run_debug_smoke_reports
from .files import AgentFileService
from .terminal_editor import ManagedTerminalEditorService
from .workerbee import WorkerBeeStatusService, agent_cwd
from .workerbee_gateway import (
    WorkerBeeGatewayError,
    WorkerBeeGatewayLease,
    WorkerBeeGatewayService,
    create_workerbee_gateway_app,
)
from .workerbee_access import (
    WorkerBeeAccessPolicyError,
    WorkerBeeAccessPolicyService,
)
from .workerbee_modes import (
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
)
from .workerbee_runtime import WorkerBeeRuntimeRegistry
from .workerbee_workspace import (
    WorkerBeeWorkspaceBinding,
    WorkerBeeWorkspaceError,
    WorkerBeeWorkspaceService,
)
from .workerbee_policy import (
    WorkerBeeWorkspacePolicyService,
)
from .workerbee_snapshot_policy import (
    WorkerBeeSnapshotPolicyService,
)
from .workerbee_mcp import call_workerbee_mcp_tool
from .workerbee_controlled_run import (
    WorkerBeeControlledRunService,
)
from .workerbee_controlled_run_executor import WorkerBeeControlledRunExecutor
from .workerbee_switcher import WorkerBeeSwitchService
from .events import EventClientRegistry, EventStreamService
from .api_routes import (
    ApiServices,
    EvidenceExportService,
    register_agent_lifecycle_routes,
    register_command_routes,
    register_durable_helper_routes,
    register_dynamic_resource_routes,
    register_evidence_routes,
    register_file_editor_routes,
    register_github_routes,
    register_joplin_routes,
    register_operator_kb_v2_routes,
    register_operator_routes,
    register_operator_authority_routes,
    register_protected_provider_routes,
    register_runtime_routes,
    register_remote_event_routes,
    register_workerbee_routes,
    run_operator_call,
)
from .durable_helpers import DurableHelperRegistry
from .dynamic_resources import DynamicResourceBroker
from .workerbee_development_resources import (
    WorkerBeeDevelopmentAdapter,
    WorkerBeeDevelopmentProvider,
)
from .codex.runtime import CodexRuntimeService
from .joplin import (
    JoplinConfig,
    JoplinGateway,
    JoplinService,
)
from .lifecycle import LifecycleService
from .issues import (
    IssueConfig,
    IssueService,
)
from .mcp_tools import create_mcp_asgi_app
from .managed_runtime import ManagedRuntimeService
from .agent_adoption import AgentPaneAdoptionService
from .operator import (
    OperatorService,
)
from .operator_broker import OperatorBrokerService
from .operator_authority import OperatorAuthorityService
from .protected_provider import ProtectedProviderDiagnosticService
from .pairing import PairRequest, PairResponse, issue_pairing_token
from .pull_requests import (
    PullRequestConfig,
    PullRequestService,
)
from .runtime_tmux import (
    list_runtime_panes,
    RuntimeServerMode,
    TmuxServerIdentity,
    assess_runtime_mapping,
    normalize_runtime_server_mode,
    process_start_ticks,
    probe_runtime_panes,
    runtime_mapping_tmux_binary,
    runtime_server_id,
    validate_tmux_socket,
)
from .remote import (
    capture_terminal_snapshot,
)
from .security import generate_pairing_code
from .store import Store


logger = logging.getLogger("agent_pbx.api")


async def inspect_tmux_runtime_mapping_live(
    mapping: Mapping[str, Any],
) -> dict[str, object]:
    """Assess one runtime against its live socket, pane, and process evidence."""

    socket_path = Path(str(mapping.get("socket_path") or ""))
    ready, message = await asyncio.to_thread(validate_tmux_socket, socket_path)
    if not ready:
        return {
            "state": "server_lost",
            "safe": False,
            "message": message,
            "repair_pane_id": None,
        }
    mode = normalize_runtime_server_mode(mapping.get("server_mode"))
    identity = TmuxServerIdentity(
        mode,
        mode,
        str(mapping.get("server_id") or runtime_server_id(socket_path)),
        str(socket_path),
        True,
        mode is not RuntimeServerMode.DEDICATED,
        tmux_bin=runtime_mapping_tmux_binary(mapping),
    )
    responsive, response_message, panes = await asyncio.to_thread(
        probe_runtime_panes,
        identity,
    )
    if not responsive:
        return {
            "state": "server_lost",
            "safe": False,
            "message": response_message,
            "repair_pane_id": None,
        }
    selected = next(
        (pane for pane in panes if pane.pane_id == mapping.get("pane_id")),
        None,
    )
    ticks = await asyncio.to_thread(
        process_start_ticks,
        selected.pane_pid if selected is not None else None,
    )
    return assess_runtime_mapping(
        mapping,
        panes,
        observed_start_ticks=ticks,
    ).public_dict()


def refresh_workerbee_runtime_selection(app: FastAPI) -> dict[str, Any]:
    """Observe the configured selector and return one authoritative runtime posture."""

    switch_status = app.state.workerbee_switcher.status()
    active = (
        switch_status.get("active")
        if isinstance(switch_status.get("active"), dict)
        else {}
    )
    if (
        active.get("ready")
        and str(active.get("backend") or "").strip()
        and str(active.get("endpoint") or "").strip()
    ):
        app.state.workerbee_runtime.observe(active, reason="selector_status")
    posture = app.state.workerbee_runtime.posture()
    posture["selector"] = switch_status
    posture["gateway_impact"] = app.state.workerbee_gateway.impact()
    return posture


def durable_workerbee_profile(metadata: Mapping[str, Any]) -> str:
    intent = (
        metadata.get("workerbee_profile_intent")
        if isinstance(metadata.get("workerbee_profile_intent"), dict)
        else {}
    )
    return str(
        intent.get("profile") or metadata.get("workerbee_mcp_profile") or ""
    ).strip()


def reconcile_workerbee_agent_binding(
    store: Store,
    lease: WorkerBeeGatewayLease,
    binding: WorkerBeeWorkspaceBinding,
    *,
    event_type: str,
    extra_metadata: Mapping[str, Any] | None = None,
    extra_event_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically project one fully validated active lease into entity metadata."""

    agent = store.get_agent(lease.agent_id)
    if agent is None:
        raise WorkerBeeGatewayError(
            "WORKERBEE_BINDING_AGENT_MISSING",
            "The active WorkerBee lease Agent is no longer registered.",
        )
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    recorded_lease_id = str(metadata.get("workerbee_gateway_lease_id") or "").strip()
    if recorded_lease_id != lease.lease_id:
        raise WorkerBeeGatewayError(
            "WORKERBEE_BINDING_LEASE_DRIFT",
            "Durable Agent metadata names a different WorkerBee gateway lease.",
            "Reconcile the current launch intent and lease before using WorkerBee.",
        )
    receipt = binding.path_domain
    recorded_policy_digest = str(
        metadata.get("workerbee_workspace_policy_digest") or ""
    ).strip()
    durable_profile = durable_workerbee_profile(metadata)
    if (
        durable_profile == WORKERBEE_PROFILE_TRUSTED_WORKBENCH
        and recorded_policy_digest
        and recorded_policy_digest != receipt.workspace_policy_digest
    ):
        raise WorkerBeeGatewayError(
            "WORKERBEE_WORKSPACE_POLICY_DIGEST_DRIFT",
            "Durable WorkerBee workspace-policy authority differs from the active binding.",
            "Issue a fresh binding and lease under the current workspace policy.",
        )
    projected = {
        "workerbee_workspace_binding_id": binding.binding_id,
        "workerbee_backend": lease.backend,
        "workerbee_backend_generation": lease.backend_generation,
        "workerbee_path_domain_receipt_sha256": receipt.receipt_sha256,
        "workerbee_workspace_policy_digest": receipt.workspace_policy_digest,
        "workerbee_host_source_root": receipt.host_source_root,
        "workerbee_allowed_host_root": receipt.allowed_host_root,
        "workerbee_container_target_root": receipt.container_target_root,
        "workerbee_runtime_workspace_path": receipt.runtime_workspace_path,
    }
    projected.update(dict(extra_metadata or {}))
    if all(metadata.get(key) == value for key, value in projected.items()):
        return agent
    reconciled = store.merge_agent_metadata(
        lease.agent_id,
        projected,
        event_type=event_type,
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
    return reconciled


async def selected_workerbee_status_for_agent(
    app: FastAPI,
    agent: dict[str, Any],
) -> dict[str, Any]:
    """Read status from the selected backend without mixing appliance and host state."""

    posture = await asyncio.to_thread(refresh_workerbee_runtime_selection, app)
    selected = posture.get("selected") if isinstance(posture.get("selected"), dict) else None
    if selected is None or selected.get("backend") == "oss":
        result = await asyncio.to_thread(app.state.workerbee.status_for_agent, agent)
        result["status_source"] = "selected_oss_cli" if selected else "host_cli"
        result["selected_runtime"] = selected
        result["workspace_binding"] = None
        result["workspace_policy"] = workerbee_policy_status(app, str(agent.get("agent_id") or ""))
        return result

    agent_id = str(agent.get("agent_id") or "")
    project = str(agent.get("project") or "")
    cwd = agent_cwd(agent)
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    binding_id = str(metadata.get("workerbee_workspace_binding_id") or "").strip()
    lease_id = str(metadata.get("workerbee_gateway_lease_id") or "").strip()
    selected_binding: WorkerBeeWorkspaceBinding | None = None
    lease_error: WorkerBeeGatewayError | None = None
    if lease_id:
        try:
            lease, selected_binding = await asyncio.to_thread(
                app.state.workerbee_gateway.current_binding,
                lease_id,
            )
        except WorkerBeeGatewayError as exc:
            lease = None
            lease_error = exc
        if (
            lease is not None
            and lease.agent_id == agent_id
        ):
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
        "workspace_policy": workerbee_policy_status(app, agent_id),
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
        binding = selected_binding or await asyncio.to_thread(
            app.state.workerbee_workspaces.load, binding_id
        )
        runtime = app.state.workerbee_runtime.selected()
        if runtime is None:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_RUNTIME_UNSELECTED",
                "No verified WorkerBee runtime is selected.",
            )
        await asyncio.to_thread(
            app.state.workerbee_workspaces.verify_runtime,
            binding,
            runtime,
        )
        base["workspace_binding"] = binding.public_dict()
        base["project"] = binding.project
        payload = await call_workerbee_mcp_tool(
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
            status_kind=(data.get("status_kind") or project_status.get("status_kind")),
            dashboard_url=data.get("dashboard_url"),
            state_dir=project_status.get("state_dir"),
            description=(
                app_status.get("message") if isinstance(app_status, dict) else None
            ),
            project_status=project_status,
            app_status=app_status if isinstance(app_status, dict) else None,
            latest_deployment=project_status.get("latest_deployment"),
        )
    except (WorkerBeeWorkspaceError, OSError, RuntimeError, ValueError) as exc:
        base["error"] = {
            "code": str(getattr(exc, "code", "WORKERBEE_SELECTED_STATUS_FAILED")),
            "message": str(exc),
            "remediation": str(getattr(exc, "remediation", "Retry after verifying the selected backend and binding.")),
        }
    return base


def workerbee_policy_status(app: FastAPI, agent_id: str) -> dict[str, Any]:
    """Return a root-redacted policy summary for ordinary status consumers."""

    view = app.state.workerbee_workspace_policy.get_policy(agent_id)
    effective = view.get("effective") if isinstance(view.get("effective"), dict) else {}
    admin = view.get("admin") if isinstance(view.get("admin"), dict) else {}
    agent = app.state.store.get_agent(agent_id)
    runtime = app.state.workerbee_runtime.selected()
    snapshot_view = app.state.workerbee_snapshot_policy.get_policy(
        agent_id,
        source_root=(agent_cwd(agent) if agent is not None else None),
        backend=(runtime.backend if runtime is not None else None),
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
        access = app.state.workerbee_access_policy.get_profile(agent_id)
    except WorkerBeeAccessPolicyError:
        access = {}
    return {
        "profile_label": effective.get("profile_label"),
        "policy": effective.get("policy"),
        "request_mode": effective.get("request_mode"),
        "allowed_root_count": len(effective.get("allowed_roots") or ()),
        "managed_scratch_configured": bool(effective.get("managed_scratch_root")),
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
            "trusted_direct_bind": workerbee_trusted_direct_config_readback(app),
        },
    }


def workerbee_trusted_direct_config_readback(app: FastAPI) -> dict[str, Any]:
    service = app.state.workerbee_workspaces
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
    approved_root_count = len(approved_roots)
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
            helper
            and receipt_store
            and roots_valid
            and administrator_ceiling
        ),
        "helper_configured": helper,
        "receipt_store_configured": receipt_store,
        "approved_root_count": approved_root_count,
        "administrator_ceiling": administrator_ceiling,
    }


def workerbee_access_readback(app: FastAPI, agent_id: str) -> dict[str, Any]:
    runtime = app.state.workerbee_runtime.selected()
    readback = app.state.workerbee_access_policy.get_profile(
        agent_id,
        selected_backend=(runtime.backend if runtime is not None else None),
    )
    readback["trusted_direct_bind"] = workerbee_trusted_direct_config_readback(app)
    return readback


def create_app(
    config: ServerConfig | None = None,
    *,
    workerbee_development_adapter: WorkerBeeDevelopmentAdapter | None = None,
) -> FastAPI:
    resolved_config = config or ServerConfig()
    store = Store(resolved_config.db_path)
    store.init()
    joplin_config = JoplinConfig(
        api_url=resolved_config.joplin_api_url,
        token=resolved_config.joplin_token,
        notebook=resolved_config.joplin_notebook,
        joplin_bin=resolved_config.joplin_bin,
        profile=resolved_config.joplin_profile,
        timeout_seconds=resolved_config.joplin_timeout_seconds,
        sync_on_write=resolved_config.joplin_sync_on_write,
        profile_owner_mode=resolved_config.joplin_profile_owner_mode,
        webdav_url=resolved_config.joplin_webdav_url,
        webdav_username=resolved_config.joplin_webdav_username,
        webdav_password_configured=(
            resolved_config.joplin_webdav_password_configured
        ),
    )
    joplin = JoplinGateway(
        store,
        JoplinService(replace(joplin_config, sync_on_write=False)),
        sync_on_write=joplin_config.sync_on_write,
    )
    pull_requests = PullRequestService(
        PullRequestConfig(
            enabled=resolved_config.pull_requests_enabled,
            merge_enabled=resolved_config.pull_request_merge_enabled,
            gh_bin=resolved_config.github_bin,
            timeout_seconds=resolved_config.pull_request_timeout_seconds,
            allowed_repos=resolved_config.pull_request_allowed_repos,
            github_remote=resolved_config.github_remote,
            github_ssh_command=resolved_config.github_ssh_command,
            github_ssh_command_overrides=(
                resolved_config.github_ssh_command_overrides or {}
            ),
        )
    )
    issues = IssueService(
        IssueConfig(
            enabled=resolved_config.issues_enabled,
            close_enabled=resolved_config.issue_close_enabled,
            gh_bin=resolved_config.github_bin,
            timeout_seconds=resolved_config.pull_request_timeout_seconds,
            allowed_repos=resolved_config.pull_request_allowed_repos,
            github_remote=resolved_config.github_remote,
            github_ssh_command=resolved_config.github_ssh_command,
            github_ssh_command_overrides=(
                resolved_config.github_ssh_command_overrides or {}
            ),
        )
    )
    mcp_asgi_app, mcp_server = create_mcp_asgi_app(
        store,
        resolved_config,
        joplin,
        pull_requests=pull_requests,
        issues=issues,
    )
    workerbee_state_root = resolved_config.db_path.parent / "workerbee-runtime"
    workerbee = WorkerBeeStatusService(
        workerbee_bin=resolved_config.workerbee_bin,
        timeout_seconds=resolved_config.workerbee_timeout_seconds,
        cache_seconds=resolved_config.workerbee_cache_seconds,
    )
    workerbee_runtime = WorkerBeeRuntimeRegistry(workerbee_state_root)
    workerbee_workspaces = WorkerBeeWorkspaceService(
        workerbee_state_root / "workspaces",
        helper_bin=resolved_config.workerbee_trusted_direct_helper_bin,
        trusted_direct_receipt_root=(
            resolved_config.workerbee_trusted_direct_receipt_root
        ),
        trusted_direct_runtime_prefix=(
            resolved_config.workerbee_trusted_direct_runtime_prefix
        ),
        trusted_direct_approved_roots=(
            resolved_config.workerbee_trusted_direct_approved_roots
        ),
        trusted_direct_administrator_ceiling=(
            resolved_config.workerbee_trusted_direct_administrator_ceiling
        ),
    )
    workerbee_workspace_policy = WorkerBeeWorkspacePolicyService(store)
    workerbee_snapshot_policy = WorkerBeeSnapshotPolicyService(store)
    workerbee_access_environ = dict(os.environ)
    if (
        resolved_config.operator_workerbee_default_profile != "local-deploy"
        or OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV in workerbee_access_environ
    ):
        workerbee_access_environ[OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV] = (
            resolved_config.operator_workerbee_default_profile
        )
    workerbee_access_policy = WorkerBeeAccessPolicyService(
        store,
        environ=workerbee_access_environ,
    )
    workerbee_controlled_runs = WorkerBeeControlledRunService(
        store,
        workerbee_runtime,
    )
    workerbee_controlled_run_executor = WorkerBeeControlledRunExecutor(
        store,
        workerbee_controlled_runs,
        workerbee_runtime,
    )
    operator_authority = OperatorAuthorityService(
        store,
        workerbee_access=workerbee_access_policy,
        workerbee_workspace=workerbee_workspace_policy,
    )

    def record_workerbee_workspace_refresh(
        previous_lease: WorkerBeeGatewayLease,
        refreshed_lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
    ) -> None:
        refreshed_at = time.time()
        event_payload = {
            "previous_binding_id": previous_lease.workspace_binding_id,
            "project": binding.project,
            "source_project": refreshed_lease.source_project,
            "working_subpath": refreshed_lease.working_subpath,
            "scenario_label": refreshed_lease.scenario_label,
            "source_commit": binding.source_commit,
            "source_dirty": binding.source_dirty,
            "refreshed_at": refreshed_at,
        }
        reconcile_workerbee_agent_binding(
            store,
            refreshed_lease,
            binding,
            event_type="workerbee_workspace_refreshed",
            extra_metadata={
                "workerbee_workspace_previous_binding_id": (
                    previous_lease.workspace_binding_id
                ),
                "workerbee_workspace_refreshed_at": refreshed_at,
                "workerbee_workspace_source_root": binding.source_root,
                "workerbee_workspace_working_subpath": refreshed_lease.working_subpath,
                "workerbee_workspace_project": refreshed_lease.project,
                "workerbee_workspace_scenario": refreshed_lease.scenario_label,
            },
            extra_event_payload=event_payload,
        )

    def record_workerbee_binding_validation(
        lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
    ) -> None:
        reconcile_workerbee_agent_binding(
            store,
            lease,
            binding,
            event_type="workerbee_binding_reconciled",
        )

    workerbee_gateway = WorkerBeeGatewayService(
        workerbee_state_root / "gateway",
        workerbee_runtime,
        workerbee_workspaces,
        binding_refreshed=record_workerbee_workspace_refresh,
        binding_validated=record_workerbee_binding_validation,
        workspace_policy=workerbee_workspace_policy,
        snapshot_policy=workerbee_snapshot_policy,
    )
    workerbee_gateway_app = create_workerbee_gateway_app(workerbee_gateway)

    def workerbee_switch_impact() -> dict[str, Any]:
        gateway_impact = workerbee_gateway.impact()
        controlled_impact = workerbee_controlled_runs.impact()
        return {
            "api_version": "agent-pbx.workerbee-switch-impact/v1",
            "active_leases": list(gateway_impact.get("active_leases") or []),
            "active_mutations": dict(gateway_impact.get("active_mutations") or {}),
            "active_controlled_runs": list(
                controlled_impact.get("active_controlled_runs") or []
            ),
            "mutation_count": int(gateway_impact.get("mutation_count") or 0)
            + int(controlled_impact.get("mutation_count") or 0),
        }

    workerbee_switcher = WorkerBeeSwitchService(
        state_root=resolved_config.db_path.parent / "workerbee-switch",
        cache_invalidator=workerbee.clear_cache,
        impact_provider=workerbee_switch_impact,
        selection_recorder=workerbee_runtime.observe,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_server.session_manager.run())
            await stack.enter_async_context(workerbee_gateway_app.session_manager.run())
            smoke_task: asyncio.Task[None] | None = None
            joplin_sync_task: asyncio.Task[None] | None = None
            if resolved_config.debug_smoke:
                smoke_config = DebugSmokeConfig(
                    duration_seconds=resolved_config.debug_smoke_duration_seconds,
                    min_interval_seconds=(
                        resolved_config.debug_smoke_min_interval_seconds
                    ),
                    max_interval_seconds=(
                        resolved_config.debug_smoke_max_interval_seconds
                    ),
                )
                smoke_task = asyncio.create_task(
                    run_debug_smoke_reports(store, smoke_config),
                    name="agent-pbx-debug-smoke",
                )
            if joplin.config.configured:
                await bootstrap_joplin_server(store, joplin)
                joplin_sync_task = asyncio.create_task(
                    run_joplin_sync_worker(store, joplin),
                    name="agent-pbx-joplin-sync",
                )
            await app.state.codex_event_bridge.start()
            await app.state.workerbee_controlled_run_executor.start()
            try:
                yield
            finally:
                await app.state.workerbee_controlled_run_executor.stop()
                await app.state.codex_event_bridge.stop()
                with suppress(Exception):
                    shutdown_editor = getattr(
                        app.state.terminal_editor,
                        "shutdown",
                        app.state.terminal_editor.stop_all,
                    )
                    await asyncio.to_thread(shutdown_editor)
                if joplin_sync_task is not None:
                    joplin_sync_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await joplin_sync_task
                if smoke_task is not None:
                    smoke_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await smoke_task

    app = FastAPI(
        title="Agent PBX",
        version=__version__,
        lifespan=lifespan,
        debug=resolved_config.debug,
    )
    app.state.config = resolved_config
    app.state.store = store
    app.state.evidence_exports = EvidenceExportService(
        store,
        root=(
            resolved_config.evidence_export_root
            or resolved_config.db_path.parent / "evidence-exports"
        ),
        enabled=resolved_config.evidence_exports_enabled,
    )
    app.state.event_stream = EventStreamService(store)
    app.state.event_clients = EventClientRegistry(
        max_clients=max(1, resolved_config.remote_max_clients),
        message_rate_per_minute=max(
            1,
            resolved_config.remote_message_rate_per_minute,
        ),
    )
    app.state.codex_capabilities = CodexCapabilityProbe(
        cache_path=resolved_config.db_path.parent / "codex-capabilities-v2.json"
    )
    app.state.codex_runtime = CodexRuntimeService(store)
    app.state.codex_event_bridge = CodexAppServerEventBridge(
        store,
        app.state.codex_runtime,
        codex_bin=resolved_config.codex_bin,
        enabled=resolved_config.codex_event_bridge_enabled,
        poll_seconds=resolved_config.codex_event_bridge_poll_seconds,
        transport=resolved_config.codex_event_bridge_transport,
    )
    app.state.files = AgentFileService()
    app.state.terminal_editor = ManagedTerminalEditorService(
        app.state.files,
        runtime_root=resolved_config.db_path.parent / "editor-runtimes",
        store=store,
    )
    app.state.joplin = joplin
    app.state.workerbee = workerbee
    app.state.workerbee_runtime = workerbee_runtime
    app.state.workerbee_workspaces = workerbee_workspaces
    app.state.workerbee_workspace_policy = workerbee_workspace_policy
    app.state.workerbee_snapshot_policy = workerbee_snapshot_policy
    app.state.workerbee_access_policy = workerbee_access_policy
    app.state.workerbee_controlled_runs = workerbee_controlled_runs
    app.state.workerbee_controlled_run_executor = workerbee_controlled_run_executor
    app.state.operator_authority = operator_authority
    app.state.workerbee_gateway = workerbee_gateway
    app.state.workerbee_switcher = workerbee_switcher
    app.state.pull_requests = pull_requests
    app.state.issues = issues
    app.state.durable_helpers = DurableHelperRegistry(
        store,
        root=(
            resolved_config.durable_helper_root
            or resolved_config.db_path.parent / "durable-helpers"
        ),
        approved_source_roots=resolved_config.durable_helper_proposal_roots,
        enabled=resolved_config.durable_helpers_enabled,
        measurements_enabled=(
            resolved_config.durable_helper_measurements_enabled
        ),
    )
    dynamic_resource_providers = {}
    if (
        resolved_config.workerbee_development_resources_enabled
        and workerbee_development_adapter is not None
    ):
        workerbee_development_provider = WorkerBeeDevelopmentProvider(
            workerbee_development_adapter
        )
        dynamic_resource_providers[
            workerbee_development_provider.provider_id
        ] = workerbee_development_provider
    app.state.dynamic_resources = DynamicResourceBroker(
        store,
        enabled=resolved_config.dynamic_resources_enabled,
        providers=dynamic_resource_providers,
    )
    app.state.workerbee_development_resources = {
        "enabled": resolved_config.workerbee_development_resources_enabled,
        "provider_configured": workerbee_development_adapter is not None,
    }
    app.state.operator_service = OperatorService(
        store,
        durable_helpers=app.state.durable_helpers,
        dynamic_resources=app.state.dynamic_resources,
        codex_runtime=app.state.codex_runtime,
    )
    app.state.operator_broker_service = OperatorBrokerService(store)
    app.state.protected_provider_diagnostics = ProtectedProviderDiagnosticService(
        store
    )
    app.state.api_services = ApiServices(
        config=resolved_config,
        evidence_exports=app.state.evidence_exports,
        operator_service=app.state.operator_service,
        durable_helpers=app.state.durable_helpers,
        dynamic_resources=app.state.dynamic_resources,
        protected_provider_diagnostics=app.state.protected_provider_diagnostics,
    )
    app.state.managed_runtime = ManagedRuntimeService(
        store,
        codex_bin=resolved_config.codex_bin,
    )
    app.state.agent_pane_adoption = AgentPaneAdoptionService(store)
    app.state.lifecycle = LifecycleService(store)
    if resolved_config.debug:
        app.add_middleware(DebugRequestLogMiddleware)
    app.mount("/mcp", mcp_asgi_app)
    app.mount("/workerbee-mcp", workerbee_gateway_app.app)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {
            "ok": True,
            "service": "agent-pbx",
            "version": __version__,
            "compatibility": compatibility_posture(
                polling=resolved_config.legacy_polling_enabled
            ).public_dict(),
            "codex_event_bridge": app.state.codex_event_bridge.public_status(),
        }

    @app.get("/v1/auth/check", dependencies=[Depends(require_token)])
    async def auth_check() -> dict[str, object]:
        return {"ok": True}

    register_runtime_routes(
        app,
        inspect_tmux_runtime_mapping_live=inspect_tmux_runtime_mapping_live,
        inspect_codex_model_catalog=inspect_codex_model_catalog,
        list_runtime_panes=list_runtime_panes,
        load_codex_config_view=load_codex_config_view,
        patch_codex_config=patch_codex_config,
        process_start_ticks=process_start_ticks,
        update_codex_cli_package=update_codex_cli_package,
        _managed_skill_service=_managed_skill_service,
    )

    register_agent_lifecycle_routes(
        app,
        resolved_config=resolved_config,
    )

    register_operator_routes(app)

    register_evidence_routes(app, app.state.api_services)

    register_workerbee_routes(
        app,
        refresh_workerbee_runtime_selection=refresh_workerbee_runtime_selection,
        selected_workerbee_status_for_agent=selected_workerbee_status_for_agent,
        workerbee_access_readback=workerbee_access_readback,
        durable_workerbee_profile=durable_workerbee_profile,
        call_workerbee_mcp_tool=lambda *args, **kwargs: call_workerbee_mcp_tool(
            *args, **kwargs
        ),
    )

    register_operator_authority_routes(app)

    register_github_routes(app)

    register_joplin_routes(app)

    register_file_editor_routes(app)

    register_operator_kb_v2_routes(
        app,
        app.state.api_services,
        run_operator_call,
    )

    register_durable_helper_routes(
        app,
        app.state.api_services,
        run_operator_call,
    )

    register_dynamic_resource_routes(
        app,
        app.state.api_services,
        run_operator_call,
    )

    register_protected_provider_routes(app, app.state.api_services)

    register_command_routes(
        app,
        resolved_config=resolved_config,
    )

    register_remote_event_routes(
        app,
        resolved_config=resolved_config,
        store=store,
        capture_terminal_snapshot=lambda *args, **kwargs: capture_terminal_snapshot(
            *args, **kwargs
        ),
    )

    return app


def _managed_skill_service(
    store: Store,
    agent_id: str,
    pack_names: list[str] | None = None,
) -> ManagedSkillPackService:
    agent = store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    metadata = agent.get("metadata")
    cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
    if not isinstance(cwd, str) or not cwd.strip():
        raise HTTPException(status_code=409, detail="agent metadata.cwd is unavailable")
    path = Path(cwd).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=409, detail="agent metadata.cwd must be absolute")
    try:
        root = path.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="agent project root is unavailable") from exc
    if not root.is_dir():
        raise HTTPException(status_code=409, detail="agent project root is not a directory")
    try:
        if pack_names:
            return ManagedSkillPackService(root, pack_names=pack_names)
        defaults = ["agent-pbx-base", "roses-architect"]
        if str(agent.get("agent_type") or "caller") == "operator":
            defaults.append("operator-role")
            metadata = agent.get("metadata")
            if isinstance(metadata, dict) and (
                metadata.get("fork_purpose") == "review"
                or metadata.get("access_mode") == "review_readonly"
            ):
                defaults.append("review-role")
        else:
            defaults.append("agent-role")
        return ManagedSkillPackService(root, pack_names=defaults)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def run_joplin_sync_worker(
    store: Store,
    joplin: JoplinGateway,
    *,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        job = store.claim_next_joplin_sync_job()
        if job is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.to_thread(joplin.run_sync_job, job)
        except Exception as exc:  # noqa: BLE001 - sync status should retain cause
            completed = store.complete_joplin_sync_job(
                str(job["sync_id"]),
                success=False,
                error=str(exc),
                error_code=str(getattr(exc, "code", "JOPLIN_SYNC_ERROR")),
            )
            store.append_event(
                "joplin_sync_failed",
                {
                    "sync_id": job["sync_id"],
                    "reason": job["reason"],
                    "agent_id": job.get("agent_id"),
                    "note_id": job.get("note_id"),
                    "message": str(exc),
                    "error_code": str(
                        getattr(exc, "code", "JOPLIN_SYNC_ERROR")
                    ),
                },
                str(job["sync_id"]),
            )
            logger.warning("Joplin sync job failed: %s", completed or job)
            continue
        completed = store.complete_joplin_sync_job(
            str(job["sync_id"]),
            success=True,
        )
        store.append_event(
            "joplin_sync_succeeded",
            {
                "sync_id": job["sync_id"],
                "reason": job["reason"],
                "agent_id": job.get("agent_id"),
                "note_id": job.get("note_id"),
            },
            str(job["sync_id"]),
        )
        logger.debug("Joplin sync job succeeded: %s", completed or job)


async def bootstrap_joplin_server(
    store: Store,
    joplin: JoplinGateway,
) -> dict[str, Any] | None:
    """Restore a configured managed Joplin server without blocking PBX startup."""

    try:
        result = await asyncio.to_thread(joplin.bootstrap_server)
    except Exception as exc:  # noqa: BLE001 - PBX remains usable without Joplin
        payload = {
            "state": "failed",
            "started": False,
            "error_code": str(
                getattr(exc, "code", "JOPLIN_PROFILE_BOOTSTRAP_FAILED")
            ),
            "message": str(exc),
        }
        store.append_event("joplin_server_bootstrap_failed", payload, "joplin")
        logger.warning("Joplin managed server bootstrap failed: %s", exc)
        return None

    event_type = (
        "joplin_server_bootstrap_started"
        if result.get("started")
        else "joplin_server_bootstrap_checked"
    )
    store.append_event(event_type, result, "joplin")
    return result


class DebugRequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        logger.debug(
            "pbx.request.start method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "-",
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "pbx.request.error method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                (time.perf_counter() - start) * 1000,
            )
            raise
        logger.debug(
            "pbx.request.finish method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response


def create_token_helper_app(
    store: Store,
    *,
    pairing_code: str | None = None,
    ttl_seconds: int = 120,
    on_issued: Callable[[], None] | None = None,
) -> tuple[FastAPI, str]:
    store.init()
    code = pairing_code or generate_pairing_code()
    store.create_pairing_code(code, ttl_seconds=ttl_seconds)
    app = FastAPI(title="Agent PBX Token Helper", version=__version__)
    app.state.store = store

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx-token-helper"}

    @app.post("/pair", response_model=PairResponse)
    async def pair(
        request: PairRequest, background_tasks: BackgroundTasks
    ) -> PairResponse:
        response = issue_pairing_token(store, request)
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid or expired pairing code",
            )
        if on_issued is not None:
            background_tasks.add_task(on_issued)
        return response

    return app, code
