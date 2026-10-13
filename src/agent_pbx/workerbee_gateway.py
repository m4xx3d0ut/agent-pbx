from __future__ import annotations

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
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .security import constant_time_equal, generate_token, hash_secret
from .workerbee_runtime import WorkerBeeRuntimeRegistry
from .workerbee_workspace import WorkerBeeWorkspaceService

WORKERBEE_GATEWAY_API_VERSION = "agent-pbx.workerbee-gateway/v1"
WORKERBEE_GATEWAY_TOKEN_ENV = "AGENT_PBX_WORKERBEE_GATEWAY_TOKEN"
WORKERBEE_PROFILE_OBSERVE = "observe-local"
WORKERBEE_PROFILE_REVIEW = "review-readonly"
WORKERBEE_PROFILE_LOCAL_DEPLOY = "local-deploy"

WORKERBEE_OBSERVE_TOOLS = (
    "workerbee_v1_capabilities",
    "workerbee_v1_session_start",
    "workerbee_v1_project_runbook_status",
    "workerbee_v1_project_status",
    "workerbee_v1_secret_policy_status",
    "workerbee_v1_profile_workload_status",
    "workerbee_v1_edge_link_status",
    "workerbee_v1_logs",
)
WORKERBEE_LOCAL_DEPLOY_TOOLS = (
    *WORKERBEE_OBSERVE_TOOLS,
    "workerbee_v1_image_build",
    "workerbee_v1_manifest_prepare",
    "workerbee_v1_manifest_validate",
    "workerbee_v1_manifest_deploy_local",
    "workerbee_v1_ingress_status",
    "workerbee_v1_ingress_route_apply",
    "workerbee_v1_ingress_probe",
    "workerbee_v1_security_review_project",
)
WORKERBEE_PROFILE_TOOLS = {
    WORKERBEE_PROFILE_OBSERVE: WORKERBEE_OBSERVE_TOOLS,
    WORKERBEE_PROFILE_REVIEW: WORKERBEE_OBSERVE_TOOLS,
    WORKERBEE_PROFILE_LOCAL_DEPLOY: WORKERBEE_LOCAL_DEPLOY_TOOLS,
}
WORKERBEE_MUTATING_TOOLS = frozenset(
    {
        "workerbee_v1_session_start",
        "workerbee_v1_image_build",
        "workerbee_v1_manifest_prepare",
        "workerbee_v1_manifest_deploy_local",
        "workerbee_v1_ingress_route_apply",
    }
)
WORKERBEE_PROJECT_SCOPED_TOOLS = frozenset(
    tool
    for tool in WORKERBEE_LOCAL_DEPLOY_TOOLS
    if tool
    not in {
        "workerbee_v1_capabilities",
        "workerbee_v1_ingress_status",
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
    allowed_tools: tuple[str, ...]
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

    def private_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_GATEWAY_API_VERSION,
            **asdict(self),
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
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.runtime_registry = runtime_registry
        self.workspace_service = workspace_service
        self.clock = clock
        self._mutation_lock = threading.Lock()
        self._active_mutations: dict[str, int] = {}

    def issue(
        self,
        *,
        agent_id: str,
        codex_session_id: str,
        profile: str,
        workspace_binding_id: str,
        ttl_seconds: float | None = None,
    ) -> tuple[WorkerBeeGatewayLease, str]:
        selected_profile = str(profile or "").strip().lower()
        allowed_tools = WORKERBEE_PROFILE_TOOLS.get(selected_profile)
        if allowed_tools is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_PROFILE_UNSUPPORTED",
                f"Unsupported WorkerBee access profile: {profile or '-'}",
            )
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED",
                "No verified WorkerBee runtime is selected.",
                "Refresh WorkerBee runtime posture before issuing a session binding.",
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
            allowed_tools=tuple(allowed_tools),
            backend=runtime.backend,
            backend_instance_id=runtime.instance_id,
            backend_generation=runtime.generation,
            backend_fingerprint=runtime.fingerprint,
            workspace_binding_id=binding.binding_id,
            project=binding.project,
            status="active",
            created_at=now,
            expires_at=(now + max(30.0, ttl_seconds) if ttl_seconds else None),
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
        if payload.pop("api_version", "") != WORKERBEE_GATEWAY_API_VERSION:
            raise WorkerBeeGatewayError(
                "WORKERBEE_GATEWAY_LEASE_VERSION_UNSUPPORTED",
                "WorkerBee gateway lease has an unsupported version.",
            )
        payload["allowed_tools"] = tuple(payload.get("allowed_tools") or ())
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
        supplied = dict(arguments or {})
        supplied_project = str(supplied.get("project") or "").strip()
        if supplied_project and supplied_project != binding.project:
            raise WorkerBeeGatewayError(
                "WORKERBEE_PROJECT_SCOPE_MISMATCH",
                "WorkerBee tool call targets a project outside this session binding.",
            )
        if tool_name == "workerbee_v1_session_start":
            goal = str(supplied.get("goal") or "Agent PBX managed session").strip()
            return binding.session_start_args(goal=goal)
        if tool_name in WORKERBEE_PROJECT_SCOPED_TOOLS:
            supplied.setdefault("project", binding.project)
        return supplied

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
        allowed = set(lease.allowed_tools)
        return [tool for tool in result.tools if tool.name in allowed]

    async def call_tool(
        self,
        lease: WorkerBeeGatewayLease,
        tool_name: str,
        arguments: Mapping[str, Any] | None,
    ) -> types.CallToolResult:
        prepared = self.service.prepare_arguments(lease, tool_name, arguments)
        runtime = self.service.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeGatewayError(
                "WORKERBEE_RUNTIME_UNSELECTED", "No WorkerBee runtime is selected."
            )
        async with self.service.mutation(lease, tool_name):
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

