from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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

