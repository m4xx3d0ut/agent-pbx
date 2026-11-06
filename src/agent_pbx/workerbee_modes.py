from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


WORKERBEE_MODES_API_VERSION = "agent-pbx.workerbee-modes/v1"

WORKERBEE_PROFILE_OBSERVE = "observe-local"
WORKERBEE_PROFILE_REVIEW = "review-readonly"
WORKERBEE_PROFILE_LOCAL_DEPLOY = "local-deploy"
WORKERBEE_PROFILE_TRUSTED_WORKBENCH = "trusted-workbench"
WORKERBEE_ACCESS_PROFILES = (
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
)
WORKERBEE_PROFILE_RANK = {
    WORKERBEE_PROFILE_REVIEW: 0,
    WORKERBEE_PROFILE_OBSERVE: 0,
    WORKERBEE_PROFILE_LOCAL_DEPLOY: 1,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH: 2,
}

WORKERBEE_FAMILY_RUNTIME_OBSERVE = "runtime-observe"
WORKERBEE_FAMILY_LOCAL_EXEC = "local-exec"
WORKERBEE_FAMILY_PROJECT_LIFECYCLE = "project-profile-lifecycle"
WORKERBEE_FAMILY_BUILD_DEPLOY = "build-deploy"
WORKERBEE_FAMILY_INGRESS = "ingress"
WORKERBEE_FAMILY_LOGS = "logs"
WORKERBEE_FAMILY_SECURITY = "security"
WORKERBEE_FAMILY_EXPORT = "export"

WORKERBEE_CAPABILITY_FAMILY_TOOLS: Mapping[str, tuple[str, ...]] = {
    WORKERBEE_FAMILY_RUNTIME_OBSERVE: (
        "workerbee_v1_capabilities",
        "workerbee_v1_runtime_policy_status",
        "workerbee_v1_project_runbook_status",
        "workerbee_v1_project_status",
        "workerbee_v1_secret_policy_status",
        "workerbee_v1_profile_workload_status",
        "workerbee_v1_edge_link_status",
    ),
    WORKERBEE_FAMILY_LOCAL_EXEC: ("workerbee_v1_exec",),
    WORKERBEE_FAMILY_PROJECT_LIFECYCLE: ("workerbee_v1_session_start",),
    WORKERBEE_FAMILY_BUILD_DEPLOY: (
        "workerbee_v1_image_build",
        "workerbee_v1_manifest_prepare",
        "workerbee_v1_manifest_validate",
        "workerbee_v1_manifest_deploy_local",
        "workerbee_v1_workload_restart",
    ),
    WORKERBEE_FAMILY_INGRESS: (
        "workerbee_v1_ingress_status",
        "workerbee_v1_ingress_route_apply",
        "workerbee_v1_ingress_probe",
    ),
    WORKERBEE_FAMILY_LOGS: ("workerbee_v1_logs",),
    WORKERBEE_FAMILY_SECURITY: ("workerbee_v1_security_review_project",),
    # WorkerBee private/appliance builds may expose this reviewed name.  OSS
    # installations that do not implement it report an explicit incompatibility;
    # the gateway never substitutes a wildcard or an unreviewed backend tool.
    WORKERBEE_FAMILY_EXPORT: ("workerbee_v1_project_export",),
}
WORKERBEE_CAPABILITY_FAMILIES = tuple(WORKERBEE_CAPABILITY_FAMILY_TOOLS)

WORKERBEE_PROFILE_FAMILIES: Mapping[str, tuple[str, ...]] = {
    WORKERBEE_PROFILE_REVIEW: (
        WORKERBEE_FAMILY_RUNTIME_OBSERVE,
        WORKERBEE_FAMILY_LOGS,
    ),
    WORKERBEE_PROFILE_OBSERVE: (
        WORKERBEE_FAMILY_RUNTIME_OBSERVE,
        WORKERBEE_FAMILY_PROJECT_LIFECYCLE,
        WORKERBEE_FAMILY_LOGS,
    ),
    WORKERBEE_PROFILE_LOCAL_DEPLOY: (
        WORKERBEE_FAMILY_RUNTIME_OBSERVE,
        WORKERBEE_FAMILY_PROJECT_LIFECYCLE,
        WORKERBEE_FAMILY_BUILD_DEPLOY,
        WORKERBEE_FAMILY_INGRESS,
        WORKERBEE_FAMILY_LOGS,
        WORKERBEE_FAMILY_SECURITY,
    ),
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH: (
        WORKERBEE_FAMILY_RUNTIME_OBSERVE,
        WORKERBEE_FAMILY_LOCAL_EXEC,
        WORKERBEE_FAMILY_PROJECT_LIFECYCLE,
        WORKERBEE_FAMILY_BUILD_DEPLOY,
        WORKERBEE_FAMILY_INGRESS,
        WORKERBEE_FAMILY_LOGS,
        WORKERBEE_FAMILY_SECURITY,
        WORKERBEE_FAMILY_EXPORT,
    ),
}

WORKERBEE_HIGH_RISK_FAMILY_TOOLS: Mapping[str, tuple[str, ...]] = {
    "remote-k1s": ("workerbee_v1_manifest_deploy_remote_k1s",),
    "provider-marketplace": (
        "workerbee_v1_provider_apply",
        "workerbee_v1_marketplace_install",
    ),
    "ca-trust": ("workerbee_v1_ingress_ca_regenerate",),
    "secrets": ("workerbee_v1_secret_write",),
    "destructive-cleanup": ("workerbee_v1_project_destroy",),
    "publication": ("workerbee_v1_publish",),
}
WORKERBEE_HIGH_RISK_FAMILIES = tuple(WORKERBEE_HIGH_RISK_FAMILY_TOOLS)
WORKERBEE_HIGH_RISK_TOOLS = frozenset(
    tool
    for tools in WORKERBEE_HIGH_RISK_FAMILY_TOOLS.values()
    for tool in tools
)

WORKERBEE_SOURCE_NATIVE_DIRECT = "native-direct"
WORKERBEE_SOURCE_SNAPSHOT_IMPORT = "snapshot-import"
WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND = "trusted-direct-bind"
WORKERBEE_SOURCE_MANAGED_CLONE = "managed-clone"
WORKERBEE_SOURCE_MODES = (
    WORKERBEE_SOURCE_NATIVE_DIRECT,
    WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
    WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
    WORKERBEE_SOURCE_MANAGED_CLONE,
)
WORKERBEE_SOURCE_MODE_VERSION = 1

WORKERBEE_EXECUTION_INTERACTIVE = "interactive-gateway"
WORKERBEE_EXECUTION_CONTROLLED_RUN = "controlled-run"
WORKERBEE_EXECUTION_LANES = (
    WORKERBEE_EXECUTION_INTERACTIVE,
    WORKERBEE_EXECUTION_CONTROLLED_RUN,
)

WORKERBEE_PUBLIC_DEFAULT_SOURCE_MODE = WORKERBEE_SOURCE_SNAPSHOT_IMPORT
WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE = WORKERBEE_PROFILE_LOCAL_DEPLOY
WORKERBEE_PUBLIC_DEFAULT_EXECUTION_LANE = WORKERBEE_EXECUTION_INTERACTIVE

_LEGACY_STRATEGY_TO_SOURCE_MODE = {
    "direct_host": WORKERBEE_SOURCE_NATIVE_DIRECT,
    "content_addressed_import": WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
    "managed_appliance_volume": WORKERBEE_SOURCE_MANAGED_CLONE,
}
_SOURCE_MODE_TO_LEGACY_STRATEGY = {
    value: key for key, value in _LEGACY_STRATEGY_TO_SOURCE_MODE.items()
}


class WorkerBeeModeError(ValueError):
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
class WorkerBeeModeSelection:
    selected_backend: str
    source_mode: str
    access_profile: str
    execution_lane: str

    def public_dict(self) -> dict[str, Any]:
        return {"api_version": WORKERBEE_MODES_API_VERSION, **asdict(self)}


@dataclass(frozen=True, slots=True)
class WorkerBeeToolSurface:
    access_profile: str
    requested_families: tuple[str, ...]
    requested_tools: tuple[str, ...]
    effective_families: tuple[str, ...]
    effective_tools: tuple[str, ...]
    missing_tools: tuple[str, ...]
    unknown_backend_tools: tuple[str, ...]
    denied_high_risk_tools: tuple[str, ...]

    @property
    def compatible(self) -> bool:
        return not self.missing_tools and not self.denied_high_risk_tools

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": "agent-pbx.workerbee-tool-surface/v1",
            **asdict(self),
            "compatible": self.compatible,
            "remediation": (
                "Use a profile supported by the selected WorkerBee backend or "
                "install a backend exposing every reviewed requested tool."
                if not self.compatible
                else ""
            ),
        }


def canonical_workerbee_access_profile(value: object) -> str:
    selected = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "observe": WORKERBEE_PROFILE_OBSERVE,
        "review": WORKERBEE_PROFILE_REVIEW,
        "deploy": WORKERBEE_PROFILE_LOCAL_DEPLOY,
        "rosie-workbench": WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    }
    selected = aliases.get(selected, selected)
    if selected not in WORKERBEE_ACCESS_PROFILES:
        raise WorkerBeeModeError(
            "WORKERBEE_ACCESS_PROFILE_INVALID",
            f"Unsupported WorkerBee access profile: {value!r}.",
        )
    return selected


def canonical_workerbee_source_mode(value: object) -> str:
    selected = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "direct-host": WORKERBEE_SOURCE_NATIVE_DIRECT,
        "content-addressed-import": WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
        "managed-appliance-volume": WORKERBEE_SOURCE_MANAGED_CLONE,
    }
    selected = aliases.get(selected, selected)
    if selected not in WORKERBEE_SOURCE_MODES:
        raise WorkerBeeModeError(
            "WORKERBEE_SOURCE_MODE_INVALID",
            f"Unsupported WorkerBee source mode: {value!r}.",
        )
    return selected


def canonical_workerbee_execution_lane(value: object) -> str:
    selected = str(value or "").strip().lower().replace("_", "-")
    if selected not in WORKERBEE_EXECUTION_LANES:
        raise WorkerBeeModeError(
            "WORKERBEE_EXECUTION_LANE_INVALID",
            f"Unsupported WorkerBee execution lane: {value!r}.",
        )
    return selected


def capability_families_for_profile(profile: object) -> tuple[str, ...]:
    selected = canonical_workerbee_access_profile(profile)
    return WORKERBEE_PROFILE_FAMILIES[selected]


def tools_for_capability_families(families: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_family in families:
        family = str(raw_family or "").strip().lower()
        tools = WORKERBEE_CAPABILITY_FAMILY_TOOLS.get(family)
        if tools is None:
            raise WorkerBeeModeError(
                "WORKERBEE_CAPABILITY_FAMILY_UNKNOWN",
                f"Unknown WorkerBee capability family: {raw_family!r}.",
                "Select only capability families in the reviewed Agent PBX registry.",
            )
        for tool in tools:
            if tool not in seen:
                result.append(tool)
                seen.add(tool)
    return tuple(result)


def tools_for_access_profile(profile: object) -> tuple[str, ...]:
    return tools_for_capability_families(capability_families_for_profile(profile))


def resolve_workerbee_tool_surface(
    profile: object,
    backend_tools: Iterable[object],
) -> WorkerBeeToolSurface:
    selected = canonical_workerbee_access_profile(profile)
    families = capability_families_for_profile(selected)
    requested = tools_for_capability_families(families)
    available = tuple(
        dict.fromkeys(
            str(item).strip()
            for item in backend_tools
            if isinstance(item, str) and str(item).strip()
        )
    )
    available_set = set(available)
    requested_set = set(requested)
    denied = tuple(sorted(available_set & WORKERBEE_HIGH_RISK_TOOLS))
    effective = tuple(tool for tool in requested if tool in available_set)
    missing = tuple(tool for tool in requested if tool not in available_set)
    effective_families = tuple(
        family
        for family in families
        if all(
            tool in available_set
            for tool in WORKERBEE_CAPABILITY_FAMILY_TOOLS[family]
        )
    )
    known = {
        tool
        for tools in WORKERBEE_CAPABILITY_FAMILY_TOOLS.values()
        for tool in tools
    } | set(WORKERBEE_HIGH_RISK_TOOLS)
    unknown = tuple(sorted(available_set - known))
    return WorkerBeeToolSurface(
        access_profile=selected,
        requested_families=families,
        requested_tools=requested,
        effective_families=effective_families,
        effective_tools=effective,
        missing_tools=missing,
        unknown_backend_tools=unknown,
        denied_high_risk_tools=denied,
    )


def source_mode_from_legacy_strategy(strategy: object) -> str:
    selected = str(strategy or "").strip().lower()
    source_mode = _LEGACY_STRATEGY_TO_SOURCE_MODE.get(selected)
    if source_mode is None:
        raise WorkerBeeModeError(
            "WORKERBEE_LEGACY_SOURCE_MODE_UNKNOWN",
            f"Unsupported legacy WorkerBee workspace strategy: {strategy!r}.",
        )
    return source_mode


def legacy_strategy_for_source_mode(source_mode: object) -> str:
    selected = canonical_workerbee_source_mode(source_mode)
    if selected == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND:
        return "trusted_direct_bind"
    return _SOURCE_MODE_TO_LEGACY_STRATEGY[selected]


def validate_independent_mode_selection(
    *,
    selected_backend: object,
    source_mode: object,
    access_profile: object,
    execution_lane: object,
) -> WorkerBeeModeSelection:
    backend = str(selected_backend or "").strip().lower().replace("-", "_")
    if not backend:
        raise WorkerBeeModeError(
            "WORKERBEE_BACKEND_UNSELECTED",
            "A WorkerBee backend must be selected explicitly.",
        )
    return WorkerBeeModeSelection(
        selected_backend=backend,
        source_mode=canonical_workerbee_source_mode(source_mode),
        access_profile=canonical_workerbee_access_profile(access_profile),
        execution_lane=canonical_workerbee_execution_lane(execution_lane),
    )
