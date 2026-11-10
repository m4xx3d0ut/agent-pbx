from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Callable, Mapping

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .security import constant_time_equal, generate_token, hash_secret
from .workerbee_runtime import (
    WORKERBEE_WORKSPACE_DIRECT_HOST,
    WorkerBeeRuntimeRegistry,
    WorkerBeeRuntimeSelection,
)
from .workerbee_policy import (
    WORKERBEE_WORKSPACE_REQUEST_APPROVAL,
    WorkerBeeWorkspacePolicyError,
    WorkerBeeWorkspacePolicyService,
    WorkerBeeWorkspaceTarget,
)
from .workerbee_snapshot_policy import (
    WorkerBeeSnapshotPolicyError,
    WorkerBeeSnapshotPolicyService,
)
from .workerbee_workspace import (
    WorkerBeeWorkspaceBinding,
    WorkerBeeWorkspaceError,
    WorkerBeeWorkspaceIncludePolicyError,
    WorkerBeeWorkspaceService,
    configured_workerbee_workspace_include_paths,
)
from .workerbee_modes import (
    WORKERBEE_EXECUTION_INTERACTIVE,
    WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATIONS,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
    WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
    WorkerBeeModeError,
    capability_families_for_profile,
    canonical_workerbee_execution_lane,
    reviewed_tools_for_access_profile,
    resolve_workerbee_tool_surface,
    tools_for_access_profile,
)

WORKERBEE_GATEWAY_API_VERSION = "agent-pbx.workerbee-gateway/v2"
WORKERBEE_GATEWAY_LEGACY_API_VERSION = "agent-pbx.workerbee-gateway/v1"
WORKERBEE_GATEWAY_TOKEN_ENV = "AGENT_PBX_WORKERBEE_GATEWAY_TOKEN"
WORKERBEE_OBSERVE_TOOLS = tools_for_access_profile(WORKERBEE_PROFILE_OBSERVE)
WORKERBEE_LOCAL_DEPLOY_TOOLS = tools_for_access_profile(
    WORKERBEE_PROFILE_LOCAL_DEPLOY
)
WORKERBEE_PROFILE_TOOLS = {
    profile: reviewed_tools_for_access_profile(profile, "platform_debug")
    for profile in (
        WORKERBEE_PROFILE_OBSERVE,
        WORKERBEE_PROFILE_REVIEW,
        WORKERBEE_PROFILE_LOCAL_DEPLOY,
        WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    )
}
WORKERBEE_MUTATING_TOOLS = frozenset(
    {
        "workerbee_v1_session_start",
        "workerbee_v1_project_mode_set",
        "workerbee_v1_project_start",
        "workerbee_v1_project_stop",
        "workerbee_v1_project_runbook_update",
        "workerbee_v1_project_runbook_import",
        "workerbee_v1_profile_start",
        "workerbee_v1_profile_stop",
        "workerbee_v1_profile_validate",
        "workerbee_v1_profile_workload_validate",
        "workerbee_v1_edge_link_start",
        "workerbee_v1_edge_link_stop",
        "workerbee_v1_edge_link_validate",
        "workerbee_v1_image_build",
        "workerbee_v1_manifest_prepare",
        "workerbee_v1_manifest_deploy_local",
        "workerbee_v1_workload_restart",
        "workerbee_v1_ingress_route_apply",
        "workerbee_v1_exec",
        "workerbee_v1_project_runbook_export",
        "workerbee_v1_bundle_export",
    }
)
WORKERBEE_PROJECT_SCOPED_TOOLS = frozenset(
    tool
    for tools in WORKERBEE_PROFILE_TOOLS.values()
    for tool in tools
    if tool
    not in {
        "workerbee_v1_capabilities",
        "workerbee_v1_ingress_status",
        WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
    }
)
_LEASE_ID = re.compile(r"^[a-f0-9]{32}$")
_CURRENT_LEASE_ID: ContextVar[str | None] = ContextVar(
    "agent_pbx_workerbee_gateway_lease", default=None
)


class WorkerBeeGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeGatewayLease:
    lease_id: str
    token_hash: str
    agent_id: str
    codex_session_id: str
    profile: str
    capability_families: tuple[str, ...]
    requested_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    source_mode: str
    execution_lane: str
    backend: str
    backend_instance_id: str
    backend_generation: int
    backend_fingerprint: str
    workspace_binding_id: str
    project: str
    status: str
    created_at: float
    expires_at: float | None
    revoked_at: float | None = None
    source_project: str = ""
    working_subpath: str = "."
    scenario_label: str | None = None

    def private_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_GATEWAY_API_VERSION,
            **asdict(self),
            "capability_families": list(self.capability_families),
            "requested_tools": list(self.requested_tools),
            "allowed_tools": list(self.allowed_tools),
        }

    def public_dict(self) -> dict[str, Any]:
        result = self.private_dict()
        result.pop("token_hash", None)
        return result


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class WorkerBeeGatewayService:
    """Bind one Codex session to one selected backend generation and workspace."""

    def __init__(
        self,
        state_root: Path,
        runtime_registry: WorkerBeeRuntimeRegistry,
        workspace_service: WorkerBeeWorkspaceService,
        *,
        clock: Callable[[], float] = time.time,
        binding_refreshed: Callable[
            [WorkerBeeGatewayLease, WorkerBeeGatewayLease, WorkerBeeWorkspaceBinding],
            None,
        ]
        | None = None,
        workspace_policy: WorkerBeeWorkspacePolicyService | None = None,
        snapshot_policy: WorkerBeeSnapshotPolicyService | None = None,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.runtime_registry = runtime_registry
        self.workspace_service = workspace_service
        self.clock = clock
        self.binding_refreshed = binding_refreshed
        self.workspace_policy = workspace_policy
        self.snapshot_policy = snapshot_policy
        self._mutation_lock = threading.Lock()
        self._active_mutations: dict[str, int] = {}

    def issue(
        self,
        *,
        agent_id: str,
        codex_session_id: str,
        profile: str,
        workspace_binding_id: str,
        execution_lane: str = WORKERBEE_EXECUTION_INTERACTIVE,
        ttl_seconds: float | None = None,
    ) -> tuple[WorkerBeeGatewayLease, str]:
        selected_profile = str(profile or "").strip().lower()
        try:
            requested_tools = tools_for_access_profile(selected_profile)
            families = capability_families_for_profile(selected_profile)
            selected_lane = canonical_workerbee_execution_lane(execution_lane)
        except WorkerBeeModeError as exc:
            raise WorkerBeeGatewayError(
                exc.code, exc.message, exc.remediation
            ) from exc
        if selected_lane != WORKERBEE_EXECUTION_INTERACTIVE:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_EXECUTION_LANE_DENIED",
                "Gateway leases use only the interactive-gateway lane.",
                "Request controlled-run through its root-owned immutable workflow.",
            )
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED",
                "No verified WorkerBee runtime is selected.",
                "Refresh WorkerBee runtime posture before issuing a session binding.",
            )
        allowed_tools = reviewed_tools_for_access_profile(
            selected_profile, runtime.backend
        )
        binding = self.workspace_service.load(workspace_binding_id)
        try:
            self.workspace_service.verify_runtime(binding, runtime)
        except Exception as exc:
            code = str(getattr(exc, "code", "WORKERBEE_WORKSPACE_BINDING_INVALID"))
            raise WorkerBeeGatewayError(code, str(exc)) from exc
        lease_id = secrets.token_hex(16)
        token = generate_token()
        now = self.clock()
        lease = WorkerBeeGatewayLease(
            lease_id=lease_id,
            token_hash=hash_secret(token),
            agent_id=self._identity(agent_id, "agent_id"),
            codex_session_id=str(codex_session_id or "").strip(),
            profile=selected_profile,
            capability_families=tuple(families),
            requested_tools=tuple(requested_tools),
            allowed_tools=tuple(allowed_tools),
            source_mode=binding.source_mode,
            execution_lane=selected_lane,
            backend=runtime.backend,
            backend_instance_id=runtime.instance_id,
            backend_generation=runtime.generation,
            backend_fingerprint=runtime.fingerprint,
            workspace_binding_id=binding.binding_id,
            project=binding.project,
            status="active",
            created_at=now,
            expires_at=(now + max(30.0, ttl_seconds) if ttl_seconds else None),
            source_project=binding.project,
            working_subpath=".",
        )
        _atomic_private_json(self._lease_path(lease_id), lease.private_dict())
        return lease, token

    def load(self, lease_id: str) -> WorkerBeeGatewayLease:
        path = self._lease_path(lease_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_NOT_FOUND",
                "WorkerBee gateway lease was not found.",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_INVALID",
                "WorkerBee gateway lease is unreadable.",
            ) from exc
        api_version = payload.pop("api_version", "")
        if api_version == WORKERBEE_GATEWAY_LEGACY_API_VERSION:
            profile = str(payload.get("profile") or "")
            try:
                payload["capability_families"] = capability_families_for_profile(
                    profile
                )
                payload["requested_tools"] = tuple(payload.get("allowed_tools") or ())
            except WorkerBeeModeError as exc:
                raise WorkerBeeGatewayError(
                    "WORKERBEE_GATEWAY_LEASE_INVALID",
                    "Legacy WorkerBee gateway lease has an unknown profile.",
                ) from exc
            try:
                legacy_binding = self.workspace_service.load(
                    str(payload.get("workspace_binding_id") or "")
                )
                payload["source_mode"] = legacy_binding.source_mode
            except WorkerBeeWorkspaceError as exc:
                raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
            payload["execution_lane"] = WORKERBEE_EXECUTION_INTERACTIVE
        elif api_version != WORKERBEE_GATEWAY_API_VERSION:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_VERSION_UNSUPPORTED",
                "WorkerBee gateway lease has an unsupported version.",
            )
        payload["capability_families"] = tuple(
            payload.get("capability_families") or ()
        )
        payload["requested_tools"] = tuple(payload.get("requested_tools") or ())
        payload["allowed_tools"] = tuple(payload.get("allowed_tools") or ())
        payload.setdefault("source_project", str(payload.get("project") or ""))
        payload.setdefault("working_subpath", ".")
        payload.setdefault("scenario_label", None)
        try:
            return WorkerBeeGatewayLease(**payload)
        except (TypeError, ValueError) as exc:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_INVALID",
                "WorkerBee gateway lease has invalid fields.",
            ) from exc

    def validate(self, lease_id: str, token: str) -> WorkerBeeGatewayLease:
        lease = self.load(lease_id)
        if not token or not constant_time_equal(hash_secret(token), lease.token_hash):
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_AUTH_FAILED",
                "WorkerBee gateway lease token is invalid.",
            )
        if lease.status != "active" or lease.revoked_at is not None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_REVOKED",
                "WorkerBee gateway lease is no longer active.",
            )
        if lease.expires_at is not None and self.clock() >= lease.expires_at:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_EXPIRED",
                "WorkerBee gateway lease expired.",
            )
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED",
                "No verified WorkerBee runtime is selected.",
            )
        if (
            lease.backend != runtime.backend
            or lease.backend_instance_id != runtime.instance_id
            or lease.backend_generation != runtime.generation
            or lease.backend_fingerprint != runtime.fingerprint
        ):
            raise WorkerBeeGatewayError(
                "WORKERBEE_BACKEND_REBIND_REQUIRED",
                (
                    f"This session is bound to {lease.backend} generation "
                    f"{lease.backend_generation}; the selected runtime is "
                    f"{runtime.backend} generation {runtime.generation}."
                ),
                "Restart or resume the session through Agent PBX after reviewing its workspace binding.",
            )
        binding = self.workspace_service.load(lease.workspace_binding_id)
        if binding.source_mode != lease.source_mode:
            raise WorkerBeeGatewayError(
                "WORKERBEE_SOURCE_MODE_DRIFT",
                "Gateway lease and workspace binding source modes differ.",
            )
        try:
            self.workspace_service.verify_runtime(binding, runtime)
        except Exception as exc:
            raise WorkerBeeGatewayError(
                str(getattr(exc, "code", "WORKERBEE_WORKSPACE_BINDING_INVALID")),
                str(exc),
            ) from exc
        return lease

    def revoke(self, lease_id: str) -> WorkerBeeGatewayLease:
        lease = self.load(lease_id)
        if lease.revoked_at is not None:
            return lease
        updated = WorkerBeeGatewayLease(
            **{
                **asdict(lease),
                "status": "revoked",
                "revoked_at": self.clock(),
            }
        )
        _atomic_private_json(self._lease_path(lease_id), updated.private_dict())
        return updated

    def impact(self) -> dict[str, Any]:
        leases: list[dict[str, Any]] = []
        root = self.state_root / "leases"
        if root.is_dir():
            for path in sorted(root.glob("*.json")):
                try:
                    lease = self.load(path.stem)
                except WorkerBeeGatewayError:
                    continue
                if lease.status == "active" and lease.revoked_at is None:
                    leases.append(lease.public_dict())
        with self._mutation_lock:
            mutations = dict(self._active_mutations)
        return {
            "api_version": WORKERBEE_GATEWAY_API_VERSION,
            "active_leases": leases,
            "active_mutations": mutations,
            "mutation_count": sum(mutations.values()),
        }

    def prepare_arguments(
        self,
        lease: WorkerBeeGatewayLease,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if tool_name not in lease.allowed_tools:
            raise WorkerBeeGatewayError(
                "WORKERBEE_TOOL_NOT_ALLOWED",
                f"WorkerBee tool {tool_name!r} is not allowed by profile {lease.profile!r}.",
            )
        binding = self.workspace_service.load(lease.workspace_binding_id)
        if (
            binding.source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            and (
                tool_name == "workerbee_v1_session_start"
                or tool_name in WORKERBEE_PROJECT_SCOPED_TOOLS
            )
        ):
            runtime = self.runtime_registry.selected()
            if runtime is None:
                raise WorkerBeeGatewayError(
                    "WORKERBEE_RUNTIME_UNSELECTED",
                    "No verified WorkerBee runtime is selected.",
                )
            try:
                # The workspace service owns both the approved roots and the
                # administrator ceiling.  Callers cannot substitute either;
                # every operation-time check derives them from service state.
                self.workspace_service.revalidate_trusted_direct(binding, runtime)
            except WorkerBeeWorkspaceError as exc:
                raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        supplied = dict(arguments or {})
        if tool_name == WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE:
            allowed_arguments = {"operation", "profile", "component"}
            unexpected = sorted(set(supplied) - allowed_arguments)
            operation = supplied.get("operation")
            if unexpected:
                raise WorkerBeeGatewayError(
                    "WORKERBEE_K1S_PROFILE_EVIDENCE_ARGUMENT_INVALID",
                    "WorkerBee K1s profile evidence received unsupported arguments: "
                    + ", ".join(unexpected),
                )
            if (
                not isinstance(operation, str)
                or operation not in WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATIONS
            ):
                raise WorkerBeeGatewayError(
                    "WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATION_INVALID",
                    "WorkerBee K1s profile evidence operation must be one of: "
                    + ", ".join(WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATIONS),
                )
            for optional in ("profile", "component"):
                value = supplied.get(optional)
                if value is not None and not isinstance(value, str):
                    raise WorkerBeeGatewayError(
                        "WORKERBEE_K1S_PROFILE_EVIDENCE_ARGUMENT_INVALID",
                        f"WorkerBee K1s profile evidence {optional} must be a string or null.",
                    )
            return supplied
        supplied_project = str(supplied.get("project") or "").strip()
        if (
            tool_name != "workerbee_v1_session_start"
            and supplied_project
            and supplied_project != lease.project
        ):
            raise WorkerBeeGatewayError(
                "WORKERBEE_PROJECT_SCOPE_MISMATCH",
                "WorkerBee tool call targets a project outside this session binding.",
            )
        if tool_name == "workerbee_v1_session_start":
            goal = str(supplied.get("goal") or "Agent PBX managed session").strip()
            prepared = binding.session_start_args(goal=goal)
            working_subpath = str(lease.working_subpath or ".").strip() or "."
            if working_subpath != ".":
                prepared["cwd"] = str(
                    PurePosixPath(binding.runtime_workspace_path) / working_subpath
                )
            prepared["project"] = lease.project
            return prepared
        if tool_name in WORKERBEE_PROJECT_SCOPED_TOOLS:
            supplied.setdefault("project", lease.project)
        return supplied

    def resolve_tool_surface(
        self,
        lease: WorkerBeeGatewayLease,
        backend_tools: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        surface = resolve_workerbee_tool_surface(
            lease.profile,
            backend_tools,
            backend=lease.backend,
        )
        if tuple(surface.requested_families) != tuple(lease.capability_families):
            raise WorkerBeeGatewayError(
                "WORKERBEE_CAPABILITY_FAMILY_DRIFT",
                "Gateway lease capability families drifted from the reviewed profile registry.",
            )
        if tuple(surface.requested_tools) != tuple(lease.requested_tools):
            raise WorkerBeeGatewayError(
                "WORKERBEE_TOOL_REGISTRY_DRIFT",
                "Gateway lease tool registry drifted from the reviewed profile registry.",
            )
        if not surface.compatible:
            detail = surface.public_dict()
            raise WorkerBeeGatewayError(
                "WORKERBEE_TOOL_SURFACE_INCOMPATIBLE",
                (
                    "Selected WorkerBee backend tool surface is incompatible: "
                    f"missing={detail['missing_tools']}; "
                    f"high_risk_allowed={sorted(set(detail['effective_tools']) & set(detail['denied_high_risk_tools']))}."
                ),
                str(detail["remediation"]),
            )
        return tuple(surface.effective_tools)

    def transition_workspace(
        self,
        lease: WorkerBeeGatewayLease,
        arguments: Mapping[str, Any] | None,
        *,
        controller_approved: bool = False,
        requested_by: str | None = None,
        transition_id: str | None = None,
    ) -> tuple[WorkerBeeGatewayLease, WorkerBeeWorkspaceBinding, bool]:
        """Move one lease to an allowed Git checkout without changing its identity."""

        current = self.load(lease.lease_id)
        runtime, binding = self._refresh_context(current)
        if self.workspace_policy is None:
            return self.refresh_workspace(current)
        supplied = dict(arguments or {})
        try:
            target = self.workspace_policy.resolve_target(
                agent_id=current.agent_id,
                binding=binding,
                candidate_cwd=str(supplied.get("cwd") or "").strip() or None,
                requested_project=str(supplied.get("project") or "").strip() or None,
                current_working_subpath=current.working_subpath,
                allow_disabled=controller_approved,
            )
        except WorkerBeeWorkspacePolicyError as exc:
            raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        if not target.requires_transition:
            return self.refresh_workspace(current)

        transition: dict[str, Any] | None = None
        if transition_id:
            transition = self.workspace_policy.get_transition(transition_id)
            if (
                transition is None
                or transition.get("agent_id") != current.agent_id
                or transition.get("lease_id") != current.lease_id
            ):
                raise WorkerBeeGatewayError(
                    "WORKERBEE_WORKSPACE_TRANSITION_NOT_FOUND",
                    "Workspace transition does not belong to this session lease.",
                )
        if target.request_mode == WORKERBEE_WORKSPACE_REQUEST_APPROVAL and not controller_approved:
            transition = transition or self.workspace_policy.find_open_transition(
                agent_id=current.agent_id,
                lease_id=current.lease_id,
                target=target,
            )
            if transition is None:
                transition = self.workspace_policy.create_transition(
                    agent_id=current.agent_id,
                    lease_id=current.lease_id,
                    from_binding_id=current.workspace_binding_id,
                    target=target,
                    status="pending",
                    requested_by=requested_by or current.agent_id,
                    reason=str(supplied.get("reason") or "").strip() or None,
                    metadata=self._transition_metadata(current),
                )
                raise WorkerBeeGatewayError(
                    "WORKERBEE_WORKSPACE_APPROVAL_REQUIRED",
                    "WorkerBee workspace transition requires controller approval.",
                    f"Approve transition {transition['transition_id']} in the WorkerBee tab, then retry.",
                )
            if transition.get("status") != "approved":
                raise WorkerBeeGatewayError(
                    "WORKERBEE_WORKSPACE_APPROVAL_REQUIRED",
                    "WorkerBee workspace transition is awaiting controller approval.",
                    f"Approve transition {transition['transition_id']} in the WorkerBee tab, then retry.",
                )
        elif transition is None:
            transition = self.workspace_policy.create_transition(
                agent_id=current.agent_id,
                lease_id=current.lease_id,
                from_binding_id=current.workspace_binding_id,
                target=target,
                status="approved",
                requested_by=requested_by or current.agent_id,
                approved_by=(requested_by or "policy:auto"),
                reason=str(supplied.get("reason") or "").strip() or None,
                metadata=self._transition_metadata(current),
            )
        elif transition.get("status") == "pending" and controller_approved:
            transition = self.workspace_policy.approve_transition(
                str(transition["transition_id"]), requested_by or "controller"
            )

        try:
            include_paths = self._target_include_paths(current, target)
            next_binding = self.workspace_service.prepare(
                runtime=runtime,
                project=target.runtime_project,
                source_root=target.source_root,
                include_paths=include_paths,
            )
            self.workspace_service.verify_runtime(next_binding, runtime)
        except (WorkerBeeWorkspaceError, WorkerBeeGatewayError) as exc:
            self.workspace_policy.update_transition(
                str(transition["transition_id"]),
                status="failed",
                reason=str(getattr(exc, "message", exc)),
            )
            if isinstance(exc, WorkerBeeGatewayError):
                raise
            raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        updated = WorkerBeeGatewayLease(
            **{
                **asdict(current),
                "workspace_binding_id": next_binding.binding_id,
                "project": target.runtime_project,
                "source_project": target.base_project,
                "working_subpath": target.working_subpath,
                "scenario_label": target.scenario_label,
            }
        )
        lease_path = self._lease_path(current.lease_id)
        _atomic_private_json(lease_path, updated.private_dict())
        try:
            if self.binding_refreshed is not None:
                self.binding_refreshed(current, updated, next_binding)
        except Exception as exc:  # noqa: BLE001
            _atomic_private_json(lease_path, current.private_dict())
            self.workspace_policy.update_transition(
                str(transition["transition_id"]),
                status="failed",
                reason="Agent PBX mapping reconciliation failed",
            )
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_TRANSITION_RECONCILE_FAILED",
                "WorkerBee workspace changed but Agent PBX could not reconcile its session mapping.",
                "The prior lease binding was restored; review daemon events before retrying.",
            ) from exc
        self.workspace_policy.update_transition(
            str(transition["transition_id"]),
            status="applied",
            to_binding_id=next_binding.binding_id,
        )
        return updated, next_binding, True

    def apply_transition(
        self,
        transition_id: str,
        *,
        approved_by: str,
    ) -> tuple[WorkerBeeGatewayLease, WorkerBeeWorkspaceBinding, bool]:
        if self.workspace_policy is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_POLICY_UNAVAILABLE",
                "WorkerBee workspace policy service is unavailable.",
            )
        transition = self.workspace_policy.get_transition(transition_id)
        if transition is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_TRANSITION_NOT_FOUND",
                "Workspace transition was not found.",
            )
        if transition.get("status") not in {"pending", "approved"}:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_TRANSITION_NOT_PENDING",
                "Only a pending or approved workspace transition can be applied.",
            )
        if transition["status"] == "pending":
            transition = self.workspace_policy.approve_transition(
                transition_id, approved_by
            )
        target_args = {
            "cwd": str(
                Path(str(transition["source_root"]))
                / str(transition["working_subpath"])
            ),
            "project": transition.get("scenario_label")
            or transition["runtime_project"],
        }
        return self.transition_workspace(
            self.load(str(transition["lease_id"])),
            target_args,
            controller_approved=True,
            requested_by=approved_by,
            transition_id=transition_id,
        )

    def revert_transition(
        self,
        transition_id: str,
        *,
        reverted_by: str,
    ) -> tuple[WorkerBeeGatewayLease, WorkerBeeWorkspaceBinding]:
        if self.workspace_policy is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_POLICY_UNAVAILABLE",
                "WorkerBee workspace policy service is unavailable.",
            )
        transition = self.workspace_policy.get_transition(transition_id)
        if transition is None or transition.get("status") != "applied":
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_TRANSITION_NOT_APPLIED",
                "Only the current applied workspace transition can be reverted.",
            )
        current = self.load(str(transition["lease_id"]))
        if current.workspace_binding_id != transition.get("to_binding_id"):
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_REVERT_STALE",
                "A newer workspace binding is active on this lease.",
                "Revert the newest applicable transition instead.",
            )
        runtime, _binding = self._refresh_context(current)
        previous = self.workspace_service.load(str(transition["from_binding_id"]))
        try:
            self.workspace_service.verify_runtime(previous, runtime)
            # Re-apply current policy and administrator ceilings before restoring.
            self.workspace_policy.resolve_target(
                agent_id=current.agent_id,
                binding=self.workspace_service.load(current.workspace_binding_id),
                candidate_cwd=previous.source_root,
                requested_project=None,
                current_working_subpath=current.working_subpath,
                allow_disabled=True,
            )
        except (WorkerBeeWorkspaceError, WorkerBeeWorkspacePolicyError) as exc:
            raise WorkerBeeGatewayError(
                str(getattr(exc, "code", "WORKERBEE_WORKSPACE_REVERT_DENIED")),
                str(getattr(exc, "message", exc)),
                str(getattr(exc, "remediation", "")),
            ) from exc
        metadata = transition.get("metadata") if isinstance(transition.get("metadata"), dict) else {}
        updated = WorkerBeeGatewayLease(
            **{
                **asdict(current),
                "workspace_binding_id": previous.binding_id,
                "project": str(metadata.get("from_project") or previous.project),
                "source_project": str(
                    metadata.get("from_source_project") or previous.project
                ),
                "working_subpath": str(
                    metadata.get("from_working_subpath") or "."
                ),
                "scenario_label": metadata.get("from_scenario_label"),
            }
        )
        lease_path = self._lease_path(current.lease_id)
        _atomic_private_json(lease_path, updated.private_dict())
        try:
            if self.binding_refreshed is not None:
                self.binding_refreshed(current, updated, previous)
        except Exception as exc:  # noqa: BLE001
            _atomic_private_json(lease_path, current.private_dict())
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_REVERT_RECONCILE_FAILED",
                "The prior binding was restored but Agent PBX mapping reconciliation failed.",
                "The newer lease binding remains active.",
            ) from exc
        self.workspace_policy.update_transition(
            transition_id,
            status="reverted",
            reason=f"Reverted by {reverted_by}",
        )
        return updated, previous

    def refresh_workspace(
        self,
        lease: WorkerBeeGatewayLease,
    ) -> tuple[WorkerBeeGatewayLease, WorkerBeeWorkspaceBinding, bool]:
        """Refresh a local-deploy snapshot without replacing its session lease."""

        current = self.load(lease.lease_id)
        runtime, binding = self._refresh_context(current)
        if (
            current.profile
            not in {WORKERBEE_PROFILE_LOCAL_DEPLOY, WORKERBEE_PROFILE_TRUSTED_WORKBENCH}
            or binding.source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            or binding.workspace_strategy == WORKERBEE_WORKSPACE_DIRECT_HOST
        ):
            return current, binding, False
        include_paths = self._workspace_include_paths(current, binding)
        expected_binding_id = self.workspace_service.expected_binding_id(
            runtime=runtime,
            project=binding.project,
            source_root=binding.source_root,
            include_paths=include_paths,
            source_mode=binding.source_mode,
            source_mode_version=binding.source_mode_version,
        )
        if expected_binding_id == binding.binding_id:
            return current, binding, False
        try:
            refreshed_binding = self.workspace_service.prepare(
                runtime=runtime,
                project=binding.project,
                source_root=binding.source_root,
                include_paths=include_paths,
                source_mode=binding.source_mode,
            )
            self.workspace_service.verify_runtime(refreshed_binding, runtime)
            verified_binding_id = self.workspace_service.expected_binding_id(
                runtime=runtime,
                project=binding.project,
                source_root=binding.source_root,
                include_paths=include_paths,
                source_mode=binding.source_mode,
                source_mode_version=binding.source_mode_version,
            )
        except WorkerBeeWorkspaceError as exc:
            raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        if (
            refreshed_binding.binding_id != expected_binding_id
            or refreshed_binding.binding_id != verified_binding_id
            or refreshed_binding.project != current.project
            or refreshed_binding.source_root != binding.source_root
        ):
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_REFRESH_INVALID",
                "WorkerBee workspace refresh returned different session scope.",
                "Retain the current binding and review the materializer receipt.",
            )
        refreshed_lease = WorkerBeeGatewayLease(
            **{
                **asdict(current),
                "workspace_binding_id": refreshed_binding.binding_id,
            }
        )
        lease_path = self._lease_path(current.lease_id)
        _atomic_private_json(lease_path, refreshed_lease.private_dict())
        try:
            if self.binding_refreshed is not None:
                self.binding_refreshed(current, refreshed_lease, refreshed_binding)
        except Exception as exc:  # noqa: BLE001 - preserve the known-good lease
            _atomic_private_json(lease_path, current.private_dict())
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_REFRESH_RECONCILE_FAILED",
                "WorkerBee workspace refreshed but Agent PBX could not reconcile its session mapping.",
                "The prior lease binding was restored; review daemon events before retrying.",
            ) from exc
        return refreshed_lease, refreshed_binding, True

    def require_current_workspace(self, lease: WorkerBeeGatewayLease) -> None:
        """Reject appliance mutations when the host checkout changed after binding."""

        current = self.load(lease.lease_id)
        runtime, binding = self._refresh_context(current)
        if (
            current.profile
            not in {WORKERBEE_PROFILE_LOCAL_DEPLOY, WORKERBEE_PROFILE_TRUSTED_WORKBENCH}
            or binding.source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            or binding.workspace_strategy == WORKERBEE_WORKSPACE_DIRECT_HOST
        ):
            return
        include_paths = self._workspace_include_paths(current, binding)
        try:
            expected_binding_id = self.workspace_service.expected_binding_id(
                runtime=runtime,
                project=binding.project,
                source_root=binding.source_root,
                include_paths=include_paths,
                source_mode=binding.source_mode,
                source_mode_version=binding.source_mode_version,
            )
        except WorkerBeeWorkspaceError as exc:
            raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        if expected_binding_id != binding.binding_id:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_REFRESH_REQUIRED",
                "The host checkout changed after this WorkerBee workspace snapshot was bound.",
                (
                    "Call workerbee_v1_session_start again to refresh the same session "
                    "lease before running a mutating WorkerBee tool."
                ),
            )

    def _refresh_context(
        self,
        lease: WorkerBeeGatewayLease,
    ) -> tuple[WorkerBeeRuntimeSelection, WorkerBeeWorkspaceBinding]:
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED",
                "No verified WorkerBee runtime is selected.",
            )
        if (
            lease.backend != runtime.backend
            or lease.backend_instance_id != runtime.instance_id
            or lease.backend_generation != runtime.generation
            or lease.backend_fingerprint != runtime.fingerprint
        ):
            raise WorkerBeeGatewayError(
                "WORKERBEE_BACKEND_REBIND_REQUIRED",
                "The selected WorkerBee backend no longer matches this session lease.",
                "Restart or resume the session through Agent PBX after reviewing the backend change.",
            )
        binding = self.workspace_service.load(lease.workspace_binding_id)
        try:
            self.workspace_service.verify_runtime(binding, runtime)
        except WorkerBeeWorkspaceError as exc:
            raise WorkerBeeGatewayError(exc.code, exc.message, exc.remediation) from exc
        if binding.project != lease.project:
            raise WorkerBeeGatewayError(
                "WORKERBEE_PROJECT_SCOPE_MISMATCH",
                "WorkerBee lease and workspace binding target different projects.",
            )
        return runtime, binding

    def _workspace_include_paths(
        self,
        lease: WorkerBeeGatewayLease,
        binding: WorkerBeeWorkspaceBinding,
    ) -> tuple[str, ...]:
        if self.snapshot_policy is not None:
            try:
                return self.snapshot_policy.resolve_include_paths(
                    agent_id=lease.agent_id,
                    source_root=binding.source_root,
                    backend=lease.backend,
                )
            except WorkerBeeSnapshotPolicyError as exc:
                raise WorkerBeeGatewayError(
                    exc.code, exc.message, exc.remediation
                ) from exc
        try:
            return configured_workerbee_workspace_include_paths(
                agent_id=lease.agent_id,
                source_root=binding.source_root,
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_INCLUDE_INVALID",
                f"WorkerBee workspace include policy {exc}.",
                "Correct the host-owned include policy before refreshing this session.",
            ) from exc

    def _target_include_paths(
        self,
        lease: WorkerBeeGatewayLease,
        target: WorkerBeeWorkspaceTarget,
    ) -> tuple[str, ...]:
        if self.snapshot_policy is not None:
            try:
                return self.snapshot_policy.resolve_include_paths(
                    agent_id=lease.agent_id,
                    source_root=target.source_root,
                    backend=lease.backend,
                )
            except WorkerBeeSnapshotPolicyError as exc:
                raise WorkerBeeGatewayError(
                    exc.code, exc.message, exc.remediation
                ) from exc
        try:
            return configured_workerbee_workspace_include_paths(
                agent_id=lease.agent_id,
                source_root=target.source_root,
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeGatewayError(
                "WORKERBEE_WORKSPACE_INCLUDE_INVALID",
                f"WorkerBee workspace include policy {exc}.",
                "Correct the host-owned include policy for the requested checkout.",
            ) from exc

    @staticmethod
    def _transition_metadata(lease: WorkerBeeGatewayLease) -> dict[str, Any]:
        return {
            "from_project": lease.project,
            "from_source_project": lease.source_project,
            "from_working_subpath": lease.working_subpath,
            "from_scenario_label": lease.scenario_label,
        }

    @asynccontextmanager
    async def mutation(self, lease: WorkerBeeGatewayLease, tool_name: str) -> AsyncIterator[None]:
        mutating = tool_name in WORKERBEE_MUTATING_TOOLS
        if mutating:
            with self._mutation_lock:
                self._active_mutations[lease.lease_id] = (
                    self._active_mutations.get(lease.lease_id, 0) + 1
                )
        try:
            yield
        finally:
            if mutating:
                with self._mutation_lock:
                    remaining = self._active_mutations.get(lease.lease_id, 0) - 1
                    if remaining > 0:
                        self._active_mutations[lease.lease_id] = remaining
                    else:
                        self._active_mutations.pop(lease.lease_id, None)

    def _lease_path(self, lease_id: str) -> Path:
        if not _LEASE_ID.fullmatch(str(lease_id or "")):
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_ID_INVALID",
                "WorkerBee gateway lease ID is invalid.",
            )
        return self.state_root / "leases" / f"{lease_id}.json"

    @staticmethod
    def _identity(value: str, label: str) -> str:
        selected = str(value or "").strip()
        if not selected or len(selected) > 255:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_IDENTITY_INVALID",
                f"WorkerBee gateway {label} is missing or invalid.",
            )
        return selected


class WorkerBeeGatewayProxy:
    def __init__(self, service: WorkerBeeGatewayService) -> None:
        self.service = service
        self._lease_locks: dict[str, asyncio.Lock] = {}

    async def list_tools(self, lease: WorkerBeeGatewayLease) -> list[types.Tool]:
        runtime = self.service.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED", "No WorkerBee runtime is selected."
            )
        async with streamablehttp_client(runtime.endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
        effective = set(
            self.service.resolve_tool_surface(
                lease,
                [tool.name for tool in result.tools],
            )
        )
        return [tool for tool in result.tools if tool.name in effective]

    async def call_tool(
        self,
        lease: WorkerBeeGatewayLease,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
    ) -> types.CallToolResult:
        lock = self._lease_locks.setdefault(lease.lease_id, asyncio.Lock())
        async with lock:
            current = self.service.load(lease.lease_id)
            # Validate tool and project scope before doing any staging work.
            self.service.prepare_arguments(current, tool_name, arguments)
            async with self.service.mutation(current, tool_name):
                if tool_name == "workerbee_v1_session_start":
                    current, _binding, _changed = await asyncio.to_thread(
                        self.service.transition_workspace,
                        current,
                        arguments,
                    )
                elif tool_name in WORKERBEE_MUTATING_TOOLS:
                    await asyncio.to_thread(
                        self.service.require_current_workspace,
                        current,
                    )
                prepared = self.service.prepare_arguments(
                    current,
                    tool_name,
                    arguments,
                )
                runtime = self.service.runtime_registry.selected()
                if runtime is None:
                    raise WorkerBeeGatewayError(
                        "WORKERBEE_RUNTIME_UNSELECTED",
                        "No WorkerBee runtime is selected.",
                    )
                async with streamablehttp_client(runtime.endpoint) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        return await session.call_tool(
                            tool_name,
                            prepared,
                            read_timeout_seconds=timedelta(seconds=300),
                        )


class WorkerBeeGatewayAuthMiddleware:
    def __init__(self, app: ASGIApp, service: WorkerBeeGatewayService) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        lease_id = self._route_lease_id(scope)
        token = self._bearer_token(scope)
        try:
            self.service.validate(lease_id, token or "")
        except WorkerBeeGatewayError as exc:
            status = 409 if exc.code == "WORKERBEE_BACKEND_REBIND_REQUIRED" else 403
            await JSONResponse(exc.public_dict(), status_code=status)(scope, receive, send)
            return
        rewritten = dict(scope)
        root_path = str(scope.get("root_path") or "").rstrip("/")
        rewritten_path = f"{root_path}/" if root_path else "/"
        rewritten["path"] = rewritten_path
        rewritten["raw_path"] = rewritten_path.encode("utf-8")
        context_token = _CURRENT_LEASE_ID.set(lease_id)
        try:
            await self.app(rewritten, receive, send)
        finally:
            _CURRENT_LEASE_ID.reset(context_token)

    @staticmethod
    def _route_lease_id(scope: Scope) -> str:
        """Extract one lease segment from mounted or standalone ASGI scopes."""

        path = str(scope.get("path") or "")
        root_path = str(scope.get("root_path") or "").rstrip("/")
        if root_path and (path == root_path or path.startswith(f"{root_path}/")):
            path = path[len(root_path) :]
        segments = [segment for segment in path.split("/") if segment]
        return segments[0] if len(segments) == 1 else ""

    @staticmethod
    def _bearer_token(scope: Scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key.lower() != b"authorization":
                continue
            text = value.decode("latin1")
            if text.lower().startswith("bearer "):
                return text[7:].strip()
        return None


@dataclass(slots=True)
class WorkerBeeGatewayApp:
    app: ASGIApp
    server: Server[Any]
    session_manager: StreamableHTTPSessionManager


def create_workerbee_gateway_app(
    service: WorkerBeeGatewayService,
    *,
    proxy: WorkerBeeGatewayProxy | None = None,
) -> WorkerBeeGatewayApp:
    selected_proxy = proxy or WorkerBeeGatewayProxy(service)
    server: Server[Any] = Server(
        "Agent PBX WorkerBee Gateway",
        version="1",
        instructions="WorkerBee tools scoped to one Agent PBX session and backend generation.",
    )

    def current_lease() -> WorkerBeeGatewayLease:
        lease_id = _CURRENT_LEASE_ID.get()
        if not lease_id:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_CONTEXT_MISSING",
                "WorkerBee gateway request has no session lease context.",
            )
        return service.load(lease_id)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await selected_proxy.list_tools(current_lease())

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return await selected_proxy.call_tool(current_lease(), name, arguments)

    manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
    )
    transport = StreamableHTTPASGIApp(manager)
    return WorkerBeeGatewayApp(
        app=WorkerBeeGatewayAuthMiddleware(transport, service),
        server=server,
        session_manager=manager,
    )
