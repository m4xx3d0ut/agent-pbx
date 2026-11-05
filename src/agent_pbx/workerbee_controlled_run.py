from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]

from .security import now_ts
from .store import Store
from .workerbee_runtime import (
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WorkerBeeRuntimeRegistry,
)


CONTROLLED_RUN_MODE_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MODE"
CONTROLLED_RUN_CONTROL_MODE_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_CONTROL_MODE"
CONTROLLED_RUN_PROFILE_ROOT_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_PROFILE_ROOT"
CONTROLLED_RUN_HOST_FALLBACK_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_HOST_FALLBACK"
CONTROLLED_RUN_REQUEST_TTL_SECONDS_ENV = (
    "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_REQUEST_TTL_SECONDS"
)
CONTROLLED_RUN_MAX_IN_FLIGHT_ENV = "AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MAX_IN_FLIGHT"

CONTROLLED_RUN_MODES = ("guided", "root_operator", "disabled")
CONTROLLED_RUN_CONTROL_MODES = ("editable", "read_only", "hidden")
CONTROLLED_RUN_HOST_FALLBACK_MODES = ("disabled", "approval")
CONTROLLED_RUN_BACKEND = "workerbee-standard-workspace"
CONTROLLED_RUN_ACTION_CLASS = "workerbee_controlled_run"
CONTROLLED_RUN_REQUIRED_TOOLS = (
    "workerbee_v1_controlled_run_preflight",
    "workerbee_v1_controlled_run_start",
    "workerbee_v1_controlled_run_status",
    "workerbee_v1_controlled_run_cancel",
    "workerbee_v1_controlled_run_receipt",
)
CONTROLLED_RUN_RUNTIME_POLICY_TOOL = "workerbee_v1_runtime_policy_status"

DEFAULT_REQUEST_TTL_SECONDS = 1800
MIN_REQUEST_TTL_SECONDS = 60
MAX_REQUEST_TTL_SECONDS = 86400
DEFAULT_MAX_IN_FLIGHT = 1
MAX_IN_FLIGHT = 8
MAX_PROFILE_BYTES = 128 * 1024
MAX_PROFILE_FILES = 64

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkerBeeControlledRunError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeControlledRunPosture:
    mode: str
    control_mode: str
    profile_root: str
    host_fallback: str
    request_ttl_seconds: int
    max_in_flight_per_root: int

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    @property
    def root_operator_enabled(self) -> bool:
        return self.mode == "root_operator"

    @property
    def controls_hidden(self) -> bool:
        return self.control_mode == "hidden"

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "enabled": self.enabled,
            "root_operator_enabled": self.root_operator_enabled,
            "controls_hidden": self.controls_hidden,
            "primary_backend": CONTROLLED_RUN_BACKEND,
            "required_tools": list(CONTROLLED_RUN_REQUIRED_TOOLS),
            "runtime_policy_tool": CONTROLLED_RUN_RUNTIME_POLICY_TOOL,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeControlledRunProfile:
    profile_id: str
    version: int
    label: str
    enabled: bool
    backend: str
    required_authority: str
    allowed_workerbee_backends: tuple[str, ...]
    allowed_projects: tuple[str, ...]
    source_mode: str
    network_mode: str
    external_egress: str
    credentials: str
    recipe_id: str
    tool_manifest_sha256: str
    max_seconds: int
    max_memory_mib: int
    max_output_bytes: int
    max_concurrency: int
    approval_mode: str
    lease_max_uses: int
    lease_ttl_seconds: int
    retention_seconds: int
    cleanup_required: bool
    allow_host_fallback: bool
    path: str
    sha256: str

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "allowed_workerbee_backends": list(self.allowed_workerbee_backends),
            "allowed_projects": list(self.allowed_projects),
        }


def controlled_run_posture(
    environ: Mapping[str, str] | None = None,
) -> WorkerBeeControlledRunPosture:
    values = dict(os.environ if environ is None else environ)
    mode = str(values.get(CONTROLLED_RUN_MODE_ENV) or "guided").strip().lower()
    if mode not in CONTROLLED_RUN_MODES:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_CONFIG_INVALID",
            f"Unsupported controlled-run mode: {mode!r}.",
        )
    control_mode = str(
        values.get(CONTROLLED_RUN_CONTROL_MODE_ENV) or "editable"
    ).strip().lower()
    if control_mode not in CONTROLLED_RUN_CONTROL_MODES:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_CONFIG_INVALID",
            f"Unsupported controlled-run control mode: {control_mode!r}.",
        )
    fallback = str(
        values.get(CONTROLLED_RUN_HOST_FALLBACK_ENV) or "disabled"
    ).strip().lower()
    if fallback not in CONTROLLED_RUN_HOST_FALLBACK_MODES:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_CONFIG_INVALID",
            f"Unsupported controlled-run host fallback mode: {fallback!r}.",
        )
    profile_root = Path(
        values.get(CONTROLLED_RUN_PROFILE_ROOT_ENV)
        or "~/.config/agent-pbx/controlled-runs.d"
    ).expanduser()
    return WorkerBeeControlledRunPosture(
        mode=mode,
        control_mode=control_mode,
        profile_root=str(profile_root),
        host_fallback=fallback,
        request_ttl_seconds=_bounded_int(
            values.get(CONTROLLED_RUN_REQUEST_TTL_SECONDS_ENV),
            default=DEFAULT_REQUEST_TTL_SECONDS,
            minimum=MIN_REQUEST_TTL_SECONDS,
            maximum=MAX_REQUEST_TTL_SECONDS,
            name=CONTROLLED_RUN_REQUEST_TTL_SECONDS_ENV,
        ),
        max_in_flight_per_root=_bounded_int(
            values.get(CONTROLLED_RUN_MAX_IN_FLIGHT_ENV),
            default=DEFAULT_MAX_IN_FLIGHT,
            minimum=1,
            maximum=MAX_IN_FLIGHT,
            name=CONTROLLED_RUN_MAX_IN_FLIGHT_ENV,
        ),
    )


def load_controlled_run_profiles(
    profile_root: Path | str,
) -> dict[str, WorkerBeeControlledRunProfile]:
    root = Path(profile_root).expanduser()
    if not root.exists():
        return {}
    if not root.is_dir() or root.is_symlink():
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_ROOT_INVALID",
            "The controlled-run profile root must be a real directory.",
        )
    files = sorted(root.glob("*.toml"))
    if len(files) > MAX_PROFILE_FILES:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_LIMIT",
            f"The profile root contains more than {MAX_PROFILE_FILES} TOML files.",
        )
    profiles: dict[str, WorkerBeeControlledRunProfile] = {}
    for path in files:
        profile = load_controlled_run_profile(path)
        if profile.profile_id in profiles:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_PROFILE_DUPLICATE",
                f"Controlled-run profile {profile.profile_id!r} is duplicated.",
            )
        profiles[profile.profile_id] = profile
    return profiles


def load_controlled_run_profile(path: Path | str) -> WorkerBeeControlledRunProfile:
    profile_path = Path(path).expanduser()
    try:
        metadata = profile_path.lstat()
    except FileNotFoundError as exc:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_NOT_FOUND",
            "The controlled-run profile was not found.",
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or profile_path.is_symlink():
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_INVALID",
            "Controlled-run profiles must be regular files and cannot be symlinks.",
        )
    if metadata.st_size > MAX_PROFILE_BYTES:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_LIMIT",
            "The controlled-run profile exceeds the maximum size.",
        )
    if metadata.st_mode & 0o022:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_PERMISSIONS",
            "Controlled-run profiles cannot be writable by group or other users.",
        )
    raw = profile_path.read_bytes()
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_PROFILE_INVALID",
            f"Controlled-run profile {profile_path.name!r} is invalid TOML.",
        ) from exc
    profile_id = _identity_value(payload.get("profile_id"), "profile_id")
    version = _bounded_int(
        payload.get("version"), default=1, minimum=1, maximum=1_000_000, name="version"
    )
    label = str(payload.get("label") or profile_id).strip()
    if not label or len(label) > 120:
        raise _profile_error("label must contain 1 to 120 characters")
    backend = str(payload.get("backend") or CONTROLLED_RUN_BACKEND).strip()
    if backend != CONTROLLED_RUN_BACKEND:
        raise _profile_error(
            f"backend must be {CONTROLLED_RUN_BACKEND!r}"
        )
    required_authority = str(
        payload.get("required_authority") or "trusted-workbench"
    ).strip()
    if required_authority != "trusted-workbench":
        raise _profile_error("required_authority must be 'trusted-workbench'")
    source_mode = str(payload.get("source_mode") or "immutable-commit-export").strip()
    if source_mode != "immutable-commit-export":
        raise _profile_error("source_mode must be 'immutable-commit-export'")
    network_mode = str(payload.get("network_mode") or "private-loopback").strip()
    if network_mode != "private-loopback":
        raise _profile_error("network_mode must be 'private-loopback'")
    external_egress = str(payload.get("external_egress") or "deny").strip()
    if external_egress != "deny":
        raise _profile_error("external_egress must be 'deny'")
    credentials = str(payload.get("credentials") or "none").strip()
    if credentials != "none":
        raise _profile_error("credentials must be 'none'")
    recipe_id = _identity_value(payload.get("recipe_id"), "recipe_id")
    tool_manifest_sha256 = str(payload.get("tool_manifest_sha256") or "").lower()
    if not _SHA256.fullmatch(tool_manifest_sha256):
        raise _profile_error("tool_manifest_sha256 must be a lowercase SHA-256 digest")
    allowed_backends = _string_tuple(
        payload.get("allowed_workerbee_backends") or [WORKERBEE_BACKEND_PLATFORM_DEBUG],
        "allowed_workerbee_backends",
    )
    if not allowed_backends or any(
        item not in {"platform_debug", "oss", "user_appliance"}
        for item in allowed_backends
    ):
        raise _profile_error("allowed_workerbee_backends contains an unsupported backend")
    allowed_projects = _string_tuple(payload.get("allowed_projects") or (), "allowed_projects")
    approval_mode = str(payload.get("approval_mode") or "manual").strip()
    if approval_mode not in {"manual", "campaign_lease"}:
        raise _profile_error("approval_mode must be 'manual' or 'campaign_lease'")
    normalized = {
        "profile_id": profile_id,
        "version": version,
        "label": label,
        "enabled": bool(payload.get("enabled", False)),
        "backend": backend,
        "required_authority": required_authority,
        "allowed_workerbee_backends": list(allowed_backends),
        "allowed_projects": list(allowed_projects),
        "source_mode": source_mode,
        "network_mode": network_mode,
        "external_egress": external_egress,
        "credentials": credentials,
        "recipe_id": recipe_id,
        "tool_manifest_sha256": tool_manifest_sha256,
        "max_seconds": _bounded_int(payload.get("max_seconds"), default=1800, minimum=30, maximum=86400, name="max_seconds"),
        "max_memory_mib": _bounded_int(payload.get("max_memory_mib"), default=4096, minimum=128, maximum=262144, name="max_memory_mib"),
        "max_output_bytes": _bounded_int(payload.get("max_output_bytes"), default=16_777_216, minimum=1024, maximum=1_073_741_824, name="max_output_bytes"),
        "max_concurrency": _bounded_int(payload.get("max_concurrency"), default=1, minimum=1, maximum=8, name="max_concurrency"),
        "approval_mode": approval_mode,
        "lease_max_uses": _bounded_int(payload.get("lease_max_uses"), default=4, minimum=1, maximum=100, name="lease_max_uses"),
        "lease_ttl_seconds": _bounded_int(payload.get("lease_ttl_seconds"), default=7200, minimum=60, maximum=86400, name="lease_ttl_seconds"),
        "retention_seconds": _bounded_int(payload.get("retention_seconds"), default=604800, minimum=0, maximum=2_592_000, name="retention_seconds"),
        "cleanup_required": bool(payload.get("cleanup_required", True)),
        "allow_host_fallback": bool(payload.get("allow_host_fallback", False)),
    }
    if not normalized["cleanup_required"]:
        raise _profile_error("cleanup_required must remain true")
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    normalized["allowed_workerbee_backends"] = tuple(
        normalized["allowed_workerbee_backends"]
    )
    normalized["allowed_projects"] = tuple(normalized["allowed_projects"])
    return WorkerBeeControlledRunProfile(
        **normalized,
        path=str(profile_path.resolve()),
        sha256=digest,
    )


class WorkerBeeControlledRunService:
    """Validate root-owned run requests and persist their authority boundary."""

    def __init__(
        self,
        store: Store,
        runtime_registry: WorkerBeeRuntimeRegistry,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.runtime_registry = runtime_registry
        self.environ = dict(os.environ if environ is None else environ)

    def posture(self) -> dict[str, Any]:
        posture = controlled_run_posture(self.environ)
        profiles = load_controlled_run_profiles(posture.profile_root)
        return {
            "api_version": "agent-pbx.workerbee-controlled-run/v1",
            **posture.public_dict(),
            "profiles": [item.public_dict() for item in profiles.values()],
        }

    def profile(self, profile_id: str) -> WorkerBeeControlledRunProfile:
        posture = controlled_run_posture(self.environ)
        profiles = load_controlled_run_profiles(posture.profile_root)
        profile = profiles.get(str(profile_id or "").strip())
        if profile is None:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_PROFILE_NOT_FOUND",
                f"Controlled-run profile {profile_id!r} is not installed.",
            )
        if not profile.enabled:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_PROFILE_DISABLED",
                f"Controlled-run profile {profile_id!r} is disabled.",
            )
        return profile

    def preflight(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
        campaign_id: str,
        profile_id: str,
        baseline_commit: str,
        candidate_commit: str,
    ) -> dict[str, Any]:
        posture = controlled_run_posture(self.environ)
        if not posture.enabled:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_DISABLED",
                "WorkerBee controlled runs are disabled by local policy.",
            )
        root = self._require_root(root_operator_agent_id)
        fork = self._require_edit_fork(root_operator_agent_id, target_fork_agent_id)
        campaign = self.store.get_operator_campaign(campaign_id)
        if (
            campaign is None
            or str(campaign.get("operator_agent_id") or "") != root_operator_agent_id
            or str(campaign.get("status") or "").lower() != "running"
        ):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_CAMPAIGN_REQUIRED",
                "The request must belong to a running campaign owned by the root Operator.",
            )
        root_metadata = self._metadata(root)
        fork_metadata = self._metadata(fork)
        self._require_trusted_authority(root_metadata, "root Operator")
        self._require_trusted_authority(fork_metadata, "edit fork")
        workspace_digest = str(
            root_metadata.get("operator_authority_activated_workspace_set_digest") or ""
        ).lower()
        if (
            not _SHA256.fullmatch(workspace_digest)
            or workspace_digest
            != str(
                fork_metadata.get("operator_authority_activated_workspace_set_digest")
                or ""
            ).lower()
        ):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_WORKSPACE_MISMATCH",
                "Root and edit fork do not share one activated workspace-set digest.",
            )
        if str(fork_metadata.get("workerbee_mcp_profile") or "") != "local-deploy":
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_LOCAL_DEPLOY_REQUIRED",
                "The target edit fork does not have the local-deploy WorkerBee profile.",
            )
        binding_id = str(fork_metadata.get("workerbee_workspace_binding_id") or "").strip()
        if not binding_id or str(fork_metadata.get("workerbee_binding_state") or "") != "ready":
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_WORKSPACE_NOT_READY",
                "The target edit fork has no ready WorkerBee workspace binding.",
            )
        runtime = self.runtime_registry.selected()
        if runtime is None:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_BACKEND_UNSELECTED",
                "No verified WorkerBee backend is selected.",
            )
        if int(fork_metadata.get("workerbee_backend_generation") or 0) != runtime.generation:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_BACKEND_DRIFT",
                "The edit fork is bound to a different WorkerBee backend generation.",
                "Restart or resume the fork through Agent PBX before requesting a run.",
            )
        profile = self.profile(profile_id)
        if runtime.backend not in profile.allowed_workerbee_backends:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_BACKEND_DENIED",
                f"Profile {profile.profile_id!r} does not allow backend {runtime.backend!r}.",
            )
        project = str(fork.get("project") or fork_metadata.get("workerbee_workspace_project") or "")
        if profile.allowed_projects and project not in profile.allowed_projects:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_PROJECT_DENIED",
                f"Project {project!r} is not allowed by profile {profile.profile_id!r}.",
            )
        baseline = _git_oid(baseline_commit, "baseline_commit")
        candidate = _git_oid(candidate_commit, "candidate_commit")
        request_identity = {
            "action_class": CONTROLLED_RUN_ACTION_CLASS,
            "root_operator_agent_id": root_operator_agent_id,
            "target_fork_agent_id": target_fork_agent_id,
            "campaign_id": campaign_id,
            "workspace_set_digest": workspace_digest,
            "backend": runtime.backend,
            "backend_instance_id": runtime.instance_id,
            "backend_generation": runtime.generation,
            "workspace_binding_id": binding_id,
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "profile_sha256": profile.sha256,
            "baseline_commit": baseline,
            "candidate_commit": candidate,
            "recipe_id": profile.recipe_id,
            "tool_manifest_sha256": profile.tool_manifest_sha256,
        }
        request_sha256 = hashlib.sha256(
            json.dumps(request_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "ok": True,
            "checked_at": now_ts(),
            "posture": posture.public_dict(),
            "profile": profile.public_dict(),
            "runtime": runtime.public_dict(),
            "project": project,
            **request_identity,
            "request_sha256": request_sha256,
            "required_capabilities": list(CONTROLLED_RUN_REQUIRED_TOOLS),
            "runtime_policy_tool": CONTROLLED_RUN_RUNTIME_POLICY_TOOL,
            "host_fallback_available": bool(
                posture.host_fallback == "approval" and profile.allow_host_fallback
            ),
        }

    def request(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
        campaign_id: str,
        profile_id: str,
        baseline_commit: str,
        candidate_commit: str,
        requested_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        preflight = self.preflight(
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
            campaign_id=campaign_id,
            profile_id=profile_id,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
        )
        posture = controlled_run_posture(self.environ)
        request, created = self.store.create_workerbee_controlled_run_request(
            identity=preflight,
            requested_by=str(requested_by or "agent-pbx-tui"),
            reason=str(reason or ""),
            ttl_seconds=posture.request_ttl_seconds,
            max_in_flight_per_root=posture.max_in_flight_per_root,
        )
        return {"request": request, "created": created, "preflight": preflight}

    def decide(
        self,
        request_id: str,
        *,
        approve: bool,
        decided_by: str,
        lease_id: str | None = None,
    ) -> dict[str, Any]:
        return self.store.decide_workerbee_controlled_run_request(
            request_id,
            approve=approve,
            decided_by=decided_by,
            lease_id=lease_id,
        )

    def cancel(self, request_id: str, *, canceled_by: str, reason: str) -> dict[str, Any]:
        return self.store.cancel_workerbee_controlled_run_request(
            request_id,
            canceled_by=canceled_by,
            reason=reason,
        )

    def request_lease(
        self,
        request_id: str,
        *,
        requested_by: str,
    ) -> dict[str, Any]:
        posture = controlled_run_posture(self.environ)
        if not posture.root_operator_enabled:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_ROOT_MODE_REQUIRED",
                "Campaign leases require root_operator controlled-run mode.",
            )
        request = self.store.get_workerbee_controlled_run_request(request_id)
        if request is None:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_NOT_FOUND",
                "The controlled-run request was not found.",
            )
        profile = self.profile(str(request["profile_id"]))
        if profile.approval_mode != "campaign_lease":
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_LEASE_DENIED",
                "The selected profile requires approval for every run.",
            )
        return self.store.create_workerbee_controlled_run_lease(
            request=request,
            requested_by=requested_by,
            max_uses=profile.lease_max_uses,
            ttl_seconds=profile.lease_ttl_seconds,
        )

    @staticmethod
    def _metadata(agent: Mapping[str, Any]) -> dict[str, Any]:
        metadata = agent.get("metadata")
        return dict(metadata) if isinstance(metadata, Mapping) else {}

    def _require_root(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        metadata = self._metadata(agent or {})
        if (
            agent is None
            or agent.get("agent_type") != "operator"
            or not agent.get("pbx_active")
            or str(metadata.get("operator_role") or "root") != "root"
            or str(metadata.get("logical_operator_id") or agent_id) != agent_id
        ):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_ROOT_REQUIRED",
                "Only an active logical root Operator may request a controlled run.",
            )
        return agent

    def _require_edit_fork(self, root_id: str, fork_id: str) -> dict[str, Any]:
        fork = self.store.get_agent(fork_id)
        metadata = self._metadata(fork or {})
        fork_record = self.store.get_operator_fork_for_agent(fork_id)
        if (
            fork is None
            or fork.get("agent_type") != "operator"
            or fork_record is None
            or str(metadata.get("operator_role") or "") != "fork"
            or str(metadata.get("logical_operator_id") or "") != root_id
            or str(fork_record.get("logical_operator_agent_id") or "") != root_id
            or str(fork_record.get("fork_purpose") or "edit") != "edit"
            or str(fork_record.get("access_mode") or "edit") != "edit"
        ):
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_EDIT_FORK_REQUIRED",
                "The target must be an edit fork owned by the requesting root Operator.",
            )
        return fork

    @staticmethod
    def _require_trusted_authority(metadata: Mapping[str, Any], label: str) -> None:
        if str(metadata.get("operator_authority_activated_profile") or "") != "trusted-workbench":
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_AUTHORITY_REQUIRED",
                f"The {label} is not activated under trusted-workbench authority.",
            )
        if metadata.get("operator_authority_requires_restart") is True:
            raise WorkerBeeControlledRunError(
                "WORKERBEE_CONTROLLED_RUN_AUTHORITY_PENDING_RESTART",
                f"The {label} has an authority change awaiting restart.",
            )


def _profile_error(message: str) -> WorkerBeeControlledRunError:
    return WorkerBeeControlledRunError(
        "WORKERBEE_CONTROLLED_RUN_PROFILE_INVALID",
        f"Controlled-run profile is invalid: {message}.",
    )


def _identity_value(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTITY.fullmatch(normalized):
        raise _profile_error(f"{name} must be a safe 1 to 120 character identifier")
    return normalized


def _git_oid(value: object, name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _GIT_OID.fullmatch(normalized):
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_SOURCE_INVALID",
            f"{name} must be a full 40 to 64 character hexadecimal Git object ID.",
        )
    return normalized


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _profile_error(f"{name} must be an array")
    result: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if not normalized or len(normalized) > 240:
            raise _profile_error(f"{name} contains an invalid value")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    name: str,
) -> int:
    try:
        result = default if value is None or str(value).strip() == "" else int(value)
    except (TypeError, ValueError) as exc:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_CONFIG_INVALID",
            f"{name} must be an integer.",
        ) from exc
    if result < minimum or result > maximum:
        raise WorkerBeeControlledRunError(
            "WORKERBEE_CONTROLLED_RUN_CONFIG_INVALID",
            f"{name} must be between {minimum} and {maximum}.",
        )
    return result
