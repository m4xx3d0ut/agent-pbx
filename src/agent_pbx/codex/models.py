from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts import CapabilityRecord, CodexRuntimeState, RuntimeEvidence, RuntimeIdentity


@dataclass(frozen=True)
class CodexProfile:
    profile_id: str
    model: str
    reasoning_effort: str
    verbosity: str
    reasoning_summary: str
    service_tier: str | None = None


@dataclass(frozen=True)
class CodexChildRuntime:
    child_id: str
    parent_id: str
    state: CodexRuntimeState
    role: str | None = None
    task: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(frozen=True)
class CodexRuntimeSnapshot:
    identity: RuntimeIdentity
    state: CodexRuntimeState
    evidence: RuntimeEvidence | None
    profile: CodexProfile | None = None
    capabilities: tuple[CapabilityRecord, ...] = ()
    children: tuple[CodexChildRuntime, ...] = ()
    context: dict[str, Any] = field(default_factory=dict)

