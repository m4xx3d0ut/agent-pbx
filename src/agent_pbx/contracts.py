from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class CodexRuntimeState(str, Enum):
    """Stable PBX vocabulary for a Codex root or child runtime."""

    STARTING = "starting"
    READY = "ready"
    THINKING = "thinking"
    DELEGATING = "delegating"
    EXECUTING = "executing"
    WAITING_TOOL = "waiting_tool"
    WAITING_USER = "waiting_user"
    COMPACTING = "compacting"
    REVIEWING = "reviewing"
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    UNKNOWN = "unknown"


class RuntimeEvidenceSource(str, Enum):
    """Ordered sources used to explain a normalized runtime state."""

    APP_SERVER = "app_server"
    PBX_REPORT = "pbx_report"
    HOOK = "hook"
    TRANSCRIPT = "transcript"
    PROCESS = "process"
    TERMINAL = "terminal"
    TMUX_HEURISTIC = "tmux_heuristic"


RUNTIME_EVIDENCE_PRIORITY: dict[RuntimeEvidenceSource, int] = {
    RuntimeEvidenceSource.APP_SERVER: 700,
    RuntimeEvidenceSource.PBX_REPORT: 600,
    RuntimeEvidenceSource.HOOK: 500,
    RuntimeEvidenceSource.TRANSCRIPT: 400,
    RuntimeEvidenceSource.PROCESS: 300,
    RuntimeEvidenceSource.TERMINAL: 200,
    RuntimeEvidenceSource.TMUX_HEURISTIC: 100,
}


class CapabilitySupport(str, Enum):
    """Capability confidence; correctness-sensitive features require observed."""

    UNSUPPORTED = "unsupported"
    ADVERTISED = "advertised"
    OBSERVED = "observed"


class LifecycleState(str, Enum):
    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    MISSING = "missing"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class RuntimeEvidence:
    state: CodexRuntimeState
    source: RuntimeEvidenceSource
    observed_at: float
    confidence: float = 1.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return RUNTIME_EVIDENCE_PRIORITY[self.source]


@dataclass(frozen=True)
class RuntimeIdentity:
    """Durable relationship among PBX, Codex, tmux, process, and workspace."""

    entity_id: str
    project: str
    entity_type: str
    codex_session_id: str | None = None
    tmux_server: str | None = None
    tmux_session: str | None = None
    tmux_window: str | None = None
    tmux_pane: str | None = None
    process_id: int | None = None
    cwd: str | None = None


@dataclass(frozen=True)
class CapabilityRecord:
    name: str
    support: CapabilitySupport
    source: str
    observed_at: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionDefinition:
    """UI-neutral action metadata shared by keys, buttons, and the palette."""

    action_id: str
    label: str
    default_sequence: str | None = None
    focus_scope: str = "global"
    capability: str | None = None
    confirmation: str | None = None
    async_policy: str = "replace"
    audit_class: str = "routine"


class EngineeringFeatureGate(str, Enum):
    """Default-disabled post-v2.3 engineering control-plane features."""

    EXACT_GIT_REVIEW = "exact_git_review"
    EVIDENCE_EXPORT_CENTER = "evidence_export_center"
    KB_BUNDLE_V2 = "kb_bundle_v2"
    DURABLE_HELPER_REGISTRY = "durable_helper_registry"
    DYNAMIC_RESOURCE_BROKER = "dynamic_resource_broker"
    WORKERBEE_DEVELOPMENT_RESOURCES = "workerbee_development_resources"


@dataclass(frozen=True)
class VersionedEngineeringContract:
    """Stable schema and rollout identity for one additive subsystem."""

    schema: str
    version: int
    feature_gate: EngineeringFeatureGate
    migration: str
    rollback: str

    def __post_init__(self) -> None:
        if not self.schema.startswith("agent-pbx."):
            raise ValueError("engineering contract schema must use the agent-pbx namespace")
        if self.version < 1:
            raise ValueError("engineering contract version must be positive")
        if not self.migration.strip() or not self.rollback.strip():
            raise ValueError("engineering contract requires migration and rollback posture")


EVIDENCE_EXPORT_MANIFEST_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.evidence-export-manifest/v1",
    version=1,
    feature_gate=EngineeringFeatureGate.EVIDENCE_EXPORT_CENTER,
    migration="additive file manifest; no database rewrite",
    rollback="disable export gate and preserve immutable manifests",
)
GIT_REVIEW_ARTIFACT_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.git-review-artifact/v1",
    version=1,
    feature_gate=EngineeringFeatureGate.EXACT_GIT_REVIEW,
    migration="additive broker receipt field",
    rollback="disable review generation and reject receipts that require it",
)
KB_BUNDLE_V2_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.operator-kb-bundle/v2",
    version=2,
    feature_gate=EngineeringFeatureGate.KB_BUNDLE_V2,
    migration="preview-only v1 to v2 normalization before explicit import",
    rollback="retain local KB rows and stop bundle synchronization",
)
DURABLE_HELPER_BUNDLE_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.durable-helper-bundle/v1",
    version=1,
    feature_gate=EngineeringFeatureGate.DURABLE_HELPER_REGISTRY,
    migration="additive helper registry tables and private object root",
    rollback="disable helper activation and retain audit/provenance records",
)
DYNAMIC_RESOURCE_REQUEST_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.dynamic-resource-request/v1",
    version=1,
    feature_gate=EngineeringFeatureGate.DYNAMIC_RESOURCE_BROKER,
    migration="additive resource request, lease, and receipt tables",
    rollback="deny new requests, revoke leases, and preserve cleanup receipts",
)
WORKERBEE_DEVELOPMENT_RESOURCE_CONTRACT = VersionedEngineeringContract(
    schema="agent-pbx.workerbee-development-resource/v1",
    version=1,
    feature_gate=EngineeringFeatureGate.WORKERBEE_DEVELOPMENT_RESOURCES,
    migration="additive provider capability and receipt fields",
    rollback="disable capability gate and release provider-owned overlays",
)

ENGINEERING_CONTRACTS: tuple[VersionedEngineeringContract, ...] = (
    GIT_REVIEW_ARTIFACT_CONTRACT,
    EVIDENCE_EXPORT_MANIFEST_CONTRACT,
    KB_BUNDLE_V2_CONTRACT,
    DURABLE_HELPER_BUNDLE_CONTRACT,
    DYNAMIC_RESOURCE_REQUEST_CONTRACT,
    WORKERBEE_DEVELOPMENT_RESOURCE_CONTRACT,
)


@dataclass(frozen=True)
class EvidenceRedactionPolicy:
    """Closed metadata policy shared by engineering evidence exporters."""

    schema: str = "agent-pbx.evidence-redaction-policy/v1"
    excluded_fields: tuple[str, ...] = (
        "credential",
        "environment",
        "hidden_reasoning",
        "prompt_body",
        "secret",
        "terminal_history",
    )
    include_prompt_bodies: bool = False
    include_terminal_history: bool = False
    include_hidden_reasoning: bool = False

    def allows(self, field: str) -> bool:
        normalized = str(field or "").strip().lower()
        return bool(normalized and normalized not in self.excluded_fields)


DEFAULT_EVIDENCE_REDACTION_POLICY = EvidenceRedactionPolicy()


@dataclass(frozen=True)
class HelperReuseMeasurement:
    """Content-free, opt-in helper reuse/churn UAT counters."""

    enabled: bool = False
    proposals: int = 0
    activations: int = 0
    reuses: int = 0
    replacements: int = 0
    quarantines: int = 0

    def __post_init__(self) -> None:
        values = (
            self.proposals,
            self.activations,
            self.reuses,
            self.replacements,
            self.quarantines,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("helper measurement counters must be non-negative integers")
        if not self.enabled and any(values):
            raise ValueError("disabled helper measurement cannot retain counters")

    @property
    def reuse_ratio(self) -> float | None:
        if not self.enabled or self.activations == 0:
            return None
        return self.reuses / self.activations

    @property
    def churn_ratio(self) -> float | None:
        if not self.enabled or self.activations == 0:
            return None
        return (self.replacements + self.quarantines) / self.activations


@dataclass(frozen=True)
class WorkerBeeDevelopmentCapabilityBoundary:
    """Exact private-provider surface accepted by the public PBX controller."""

    schema: str = "agent-pbx.workerbee-development-capabilities/v1"
    trusted_direct_bind_read_only: bool = True
    trusted_direct_bind_rprivate: bool = True
    writable_source: bool = False
    resource_classes: tuple[str, ...] = (
        "build_scratch",
        "generated_output_staging",
        "managed_cache",
        "managed_venv",
        "service_state",
        "unix_socket_runtime",
    )

    def validate_provider_evidence(self, evidence: Mapping[str, Any]) -> None:
        if set(evidence) != {
            "schema",
            "provider",
            "provider_generation",
            "backend",
            "backend_instance",
            "backend_generation",
            "workspace_binding_id",
            "resource_classes",
            "mount_targets",
            "request_schema",
            "receipt_schema",
            "cleanup_schema",
            "trusted_direct_bind_read_only",
            "trusted_direct_bind_rprivate",
            "writable_source",
            "max_ttl_seconds",
            "max_quota_bytes",
            "issued_at",
            "expires_at",
            "capability_sha256",
        }:
            raise ValueError("WorkerBee capability evidence has unexpected fields")
        if evidence.get("schema") != self.schema:
            raise ValueError("WorkerBee capability schema mismatch")
        generation = evidence.get("backend_generation")
        if not isinstance(generation, str) or not generation.strip():
            raise ValueError("WorkerBee backend generation must be an identity")
        classes = evidence.get("resource_classes")
        if not isinstance(classes, list) or tuple(classes) != self.resource_classes:
            raise ValueError("WorkerBee development resource classes mismatch")
        if evidence.get("trusted_direct_bind_read_only") is not True:
            raise ValueError("trusted direct bind must remain read-only")
        if evidence.get("trusted_direct_bind_rprivate") is not True:
            raise ValueError("trusted direct bind must remain rprivate")
        if evidence.get("writable_source") is not False:
            raise ValueError("writable direct source is outside this capability")


WORKERBEE_DEVELOPMENT_CAPABILITY_BOUNDARY = WorkerBeeDevelopmentCapabilityBoundary()


def engineering_feature_defaults() -> dict[str, bool]:
    """Return committed public defaults without consulting ambient state."""

    return {gate.value: False for gate in EngineeringFeatureGate}

