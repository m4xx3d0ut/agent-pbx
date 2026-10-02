from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Mapping

from ..contracts import (
    CapabilitySupport,
    CodexRuntimeState,
    RuntimeEvidence,
    RuntimeEvidenceSource,
    RuntimeIdentity,
)
from ..store import Store
from .adapter import CodexSessionAdapter
from .models import CodexChildRuntime, CodexProfile


RUNTIME_SNAPSHOT_API_VERSION = "agent-pbx.codex-runtime/v2"
RUNTIME_EVENT_TYPE = "codex_runtime_observed"
MAX_CHILDREN = 32

PROTOCOL_METHODS = {
    "thread/started",
    "thread/status/changed",
    "thread/tokenUsage/updated",
    "turn/started",
    "turn/completed",
    "turn/interrupted",
    "item/started",
    "item/completed",
    "server/error",
}

PBX_STATUS_STATES = {
    "registered": CodexRuntimeState.READY,
    "ready": CodexRuntimeState.READY,
    "idle": CodexRuntimeState.READY,
    "working": CodexRuntimeState.THINKING,
    "running": CodexRuntimeState.THINKING,
    "blocked": CodexRuntimeState.WAITING_USER,
    "waiting": CodexRuntimeState.WAITING_USER,
    "done": CodexRuntimeState.COMPLETE,
    "complete": CodexRuntimeState.COMPLETE,
    "completed": CodexRuntimeState.COMPLETE,
    "failed": CodexRuntimeState.ERROR,
    "error": CodexRuntimeState.ERROR,
    "canceled": CodexRuntimeState.INTERRUPTED,
    "cancelled": CodexRuntimeState.INTERRUPTED,
}

CHILD_STATUS_STATES = {
    "pendingInit": CodexRuntimeState.STARTING,
    "running": CodexRuntimeState.THINKING,
    "interrupted": CodexRuntimeState.INTERRUPTED,
    "completed": CodexRuntimeState.COMPLETE,
    "errored": CodexRuntimeState.ERROR,
    "shutdown": CodexRuntimeState.COMPLETE,
    "notFound": CodexRuntimeState.UNKNOWN,
}


@dataclass
class RuntimeRecord:
    adapter: CodexSessionAdapter
    profile: CodexProfile | None = None
    children: dict[str, CodexChildRuntime] = field(default_factory=dict)
    context: dict[str, int | float | None] = field(default_factory=dict)
    native_subagents_observed: bool = False
    hydrated: bool = False


class CodexRuntimeService:
    """Normalize and persist safe Codex runtime state for PBX consumers.

    The service stores a projection of app-server events. Prompt text, model
    reasoning, command content, tool arguments, and child status messages are
    intentionally excluded from both snapshots and the PBX event journal.
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        self._records: dict[str, RuntimeRecord] = {}
        self._lock = threading.RLock()
        self._branch_cache: dict[str, tuple[float, str | None]] = {}

    def observe_protocol(
        self,
        agent_id: str,
        *,
        thread_id: str,
        method: str,
        params: Mapping[str, Any] | None = None,
        observed_at: float | None = None,
    ) -> dict[str, Any]:
        if method not in PROTOCOL_METHODS:
            raise ValueError(f"unsupported Codex runtime method: {method}")
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        current = observed_at if observed_at is not None else time.time()
        raw_params = dict(params or {})
        with self._lock:
            record = self._record(agent)
            safe_params = safe_event_detail(method, raw_params)
            evidence = record.adapter.observe_app_server(
                thread_id=thread_id,
                method=method,
                params=safe_params,
                observed_at=current,
            )
            associated = record.adapter.association.app_server_verified
            if not associated:
                raise ValueError(
                    "Codex runtime event thread does not match the active PBX session"
                )
            changed = evidence is not None
            changed = self._apply_profile(record, method, raw_params) or changed
            changed = self._apply_context(record, method, raw_params) or changed
            changed = self._apply_children(record, method, raw_params, current) or changed
            if not changed:
                raise ValueError(
                    "Codex runtime event did not contain a supported state projection"
                )
            snapshot = self._snapshot(agent, record)
            self.store.append_event(
                RUNTIME_EVENT_TYPE,
                {
                    "agent_id": agent_id,
                    "session_id": record.adapter.identity.codex_session_id,
                    "method": method,
                    "observed_at": current,
                    "snapshot": snapshot,
                },
                agent_id,
            )
            return snapshot

    def observe_report(
        self,
        agent_id: str,
        *,
        status: str,
        observed_at: float | None = None,
    ) -> dict[str, Any] | None:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            return None
        state = PBX_STATUS_STATES.get(status.strip().lower())
        if state is None:
            return None
        with self._lock:
            record = self._record(agent)
            record.adapter.observe(
                state,
                source=RuntimeEvidenceSource.PBX_REPORT,
                confidence=1.0,
                detail={"status": status.strip().lower()},
                observed_at=observed_at,
            )
            return self._snapshot(agent, record)

    def snapshot(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        with self._lock:
            record = self._record(agent)
            return self._snapshot(agent, record)

    def _record(self, agent: dict[str, Any]) -> RuntimeRecord:
        agent_id = str(agent["agent_id"])
        identity = runtime_identity(self.store, agent)
        record = self._records.get(agent_id)
        if record is None or record.adapter.identity != identity:
            record = RuntimeRecord(
                adapter=CodexSessionAdapter(identity),
                profile=profile_from_agent(agent),
            )
            self._records[agent_id] = record
        if not record.hydrated:
            self._hydrate(agent_id, record)
            record.hydrated = True
        if record.profile is None:
            record.profile = profile_from_agent(agent)
        if record.adapter.state.current() is None:
            status = str(agent.get("effective_status") or agent.get("status") or "")
            state = PBX_STATUS_STATES.get(status.strip().lower())
            if state is not None:
                record.adapter.observe(
                    state,
                    source=RuntimeEvidenceSource.PBX_REPORT,
                    confidence=0.9,
                    detail={"status": status.strip().lower(), "fallback": True},
                    observed_at=float(agent.get("last_seen_at") or time.time()),
                )
        return record

    def _hydrate(self, agent_id: str, record: RuntimeRecord) -> None:
        events = self.store.list_subject_events(
            agent_id,
            event_type=RUNTIME_EVENT_TYPE,
            limit=1,
        )
        if not events:
            return
        payload = events[-1].get("payload")
        if not isinstance(payload, dict):
            return
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return
        if str(snapshot.get("session_id") or "") != str(
            record.adapter.identity.codex_session_id or ""
        ):
            return
        evidence = snapshot.get("evidence")
        if isinstance(evidence, dict):
            try:
                record.adapter.observe(
                    CodexRuntimeState(str(evidence["state"])),
                    source=RuntimeEvidenceSource(str(evidence["source"])),
                    confidence=float(evidence.get("confidence", 1.0)),
                    detail=dict(evidence.get("detail") or {}),
                    observed_at=float(evidence["observed_at"]),
                )
            except (KeyError, TypeError, ValueError):
                pass
        profile = snapshot.get("profile")
        if isinstance(profile, dict) and profile.get("model"):
            record.profile = CodexProfile(
                profile_id=str(profile.get("profile_id") or "observed"),
                model=str(profile["model"]),
                reasoning_effort=str(profile.get("reasoning_effort") or ""),
                verbosity=str(profile.get("verbosity") or ""),
                reasoning_summary=str(profile.get("reasoning_summary") or ""),
                service_tier=str(profile.get("service_tier") or "") or None,
            )
        context = snapshot.get("context")
        if isinstance(context, dict):
            record.context = {
                key: value
                for key, value in context.items()
                if key in {"used_tokens", "capacity_tokens", "remaining_percent"}
                and isinstance(value, (int, float, type(None)))
            }
        capabilities = snapshot.get("capabilities")
        if isinstance(capabilities, dict):
            record.native_subagents_observed = (
                capabilities.get("native_subagents") == CapabilitySupport.OBSERVED.value
            )
        children = snapshot.get("children")
        if record.native_subagents_observed and isinstance(children, list):
            for child in children[:MAX_CHILDREN]:
                parsed = child_from_snapshot(child)
                if parsed is not None:
                    record.children[parsed.child_id] = parsed

    def _apply_profile(
        self,
        record: RuntimeRecord,
        method: str,
        params: Mapping[str, Any],
    ) -> bool:
        if method != "thread/started":
            return False
        thread = params.get("thread")
        if not isinstance(thread, Mapping):
            return False
        model = clean_public_value(thread.get("model"), 160)
        if not model:
            return False
        effort = clean_public_value(thread.get("reasoningEffort"), 80) or ""
        prior = record.profile
        record.profile = CodexProfile(
            profile_id=prior.profile_id if prior else "observed",
            model=model,
            reasoning_effort=effort,
            verbosity=prior.verbosity if prior else "",
            reasoning_summary=prior.reasoning_summary if prior else "",
            service_tier=prior.service_tier if prior else None,
        )
        return True

    def _apply_context(
        self,
        record: RuntimeRecord,
        method: str,
        params: Mapping[str, Any],
    ) -> bool:
        if method != "thread/tokenUsage/updated":
            return False
        usage = params.get("tokenUsage")
        if not isinstance(usage, Mapping):
            return False
        last = usage.get("last")
        total = usage.get("total")
        source = last if isinstance(last, Mapping) else total
        if not isinstance(source, Mapping):
            return False
        used = safe_nonnegative_int(source.get("totalTokens"))
        capacity = safe_nonnegative_int(usage.get("modelContextWindow"))
        if used is None and capacity is None:
            return False
        remaining: float | None = None
        if used is not None and capacity:
            remaining = round(max(0.0, min(100.0, 100.0 * (capacity - used) / capacity)), 1)
        record.context = {
            "used_tokens": used,
            "capacity_tokens": capacity,
            "remaining_percent": remaining,
        }
        return True

    def _apply_children(
        self,
        record: RuntimeRecord,
        method: str,
        params: Mapping[str, Any],
        observed_at: float,
    ) -> bool:
        if method not in {"item/started", "item/completed"}:
            return False
        item = params.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "collabAgentToolCall":
            return False
        sender = clean_public_value(item.get("senderThreadId"), 240)
        if sender and sender != record.adapter.identity.codex_session_id:
            return False
        receivers = [
            value
            for raw in item.get("receiverThreadIds", [])
            if (value := clean_public_value(raw, 240))
        ][:MAX_CHILDREN]
        states = item.get("agentsStates")
        state_map = states if isinstance(states, Mapping) else {}
        model = clean_public_value(item.get("model"), 160)
        effort = clean_public_value(item.get("reasoningEffort"), 80)
        changed = False
        for child_id in receivers:
            child_state = state_map.get(child_id)
            child_status = ""
            if isinstance(child_state, Mapping):
                child_status = str(child_state.get("status") or "")
            state = CHILD_STATUS_STATES.get(child_status)
            if state is None:
                status = str(item.get("status") or "")
                if method == "item/completed" and status == "failed":
                    state = CodexRuntimeState.ERROR
                elif method == "item/completed":
                    state = CodexRuntimeState.COMPLETE
                else:
                    state = CodexRuntimeState.STARTING
            prior = record.children.get(child_id)
            record.children[child_id] = CodexChildRuntime(
                child_id=child_id,
                parent_id=record.adapter.identity.entity_id,
                state=state,
                role=prior.role if prior else None,
                task=None,
                model=model or (prior.model if prior else None),
                reasoning_effort=effort or (prior.reasoning_effort if prior else None),
                started_at=(prior.started_at if prior else observed_at),
                finished_at=(
                    observed_at
                    if state
                    in {
                        CodexRuntimeState.COMPLETE,
                        CodexRuntimeState.ERROR,
                        CodexRuntimeState.INTERRUPTED,
                    }
                    else None
                ),
            )
            changed = True
        if changed:
            record.native_subagents_observed = True
        return changed

    def _snapshot(
        self,
        agent: dict[str, Any],
        record: RuntimeRecord,
    ) -> dict[str, Any]:
        evidence = record.adapter.state.current()
        state = evidence.state if evidence else CodexRuntimeState.UNKNOWN
        identity = record.adapter.identity
        children = (
            sorted(record.children.values(), key=lambda item: (item.started_at or 0, item.child_id))
            if record.native_subagents_observed
            else []
        )
        return {
            "api_version": RUNTIME_SNAPSHOT_API_VERSION,
            "agent_id": identity.entity_id,
            "project": identity.project,
            "entity_type": identity.entity_type,
            "session_id": identity.codex_session_id,
            "state": state.value,
            "evidence": evidence_dict(evidence),
            "profile": profile_dict(record.profile),
            "context": dict(record.context),
            "branch": self._branch(identity.cwd),
            "capabilities": {
                "native_subagents": (
                    CapabilitySupport.OBSERVED.value
                    if record.native_subagents_observed
                    else CapabilitySupport.ADVERTISED.value
                ),
            },
            "children": [child_dict(item) for item in children],
            "updated_at": evidence.observed_at if evidence else None,
        }

    def _branch(self, cwd: str | None) -> str | None:
        if not cwd:
            return None
        now = time.monotonic()
        cached = self._branch_cache.get(cwd)
        if cached is not None and now - cached[0] <= 5.0:
            return cached[1]
        path = Path(cwd).expanduser()
        branch: str | None = None
        if path.is_dir():
            try:
                result = subprocess.run(
                    ["git", "-C", str(path), "branch", "--show-current"],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                value = result.stdout.strip()
                if result.returncode == 0 and value:
                    branch = value[:240]
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._branch_cache[cwd] = (now, branch)
        return branch


def runtime_identity(store: Store, agent: dict[str, Any]) -> RuntimeIdentity:
    agent_id = str(agent["agent_id"])
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    mapping = store.get_tmux_runtime_mapping(agent_id) or {}
    session_id = first_text(
        mapping.get("codex_session_id"),
        metadata.get("fork_codex_session_id"),
        metadata.get("codex_session_id"),
        metadata.get("codex_thread_id"),
        metadata.get("last_resume_codex_session_id"),
        metadata.get("source_codex_session_id"),
    )
    return RuntimeIdentity(
        entity_id=agent_id,
        project=str(agent.get("project") or ""),
        entity_type=str(agent.get("agent_type") or "caller"),
        codex_session_id=session_id,
        tmux_server=clean_public_value(mapping.get("server_id"), 4096),
        tmux_session=clean_public_value(mapping.get("session_name"), 240),
        tmux_window=clean_public_value(mapping.get("window_id"), 120),
        tmux_pane=clean_public_value(mapping.get("pane_id"), 120),
        process_id=safe_nonnegative_int(mapping.get("pane_pid")),
        cwd=first_text(mapping.get("cwd"), metadata.get("cwd")),
    )


def profile_from_agent(agent: Mapping[str, Any]) -> CodexProfile | None:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), Mapping) else {}
    model = first_text(metadata.get("codex_model"), metadata.get("model"))
    if not model:
        return None
    return CodexProfile(
        profile_id=first_text(metadata.get("codex_profile_id"), metadata.get("codex_model_preset"))
        or "registered",
        model=model,
        reasoning_effort=first_text(
            metadata.get("codex_model_reasoning_effort"),
            metadata.get("model_reasoning_effort"),
        )
        or "",
        verbosity=first_text(
            metadata.get("codex_model_verbosity"), metadata.get("model_verbosity")
        )
        or "",
        reasoning_summary=first_text(
            metadata.get("codex_model_reasoning_summary"),
            metadata.get("model_reasoning_summary"),
        )
        or "",
        service_tier=first_text(metadata.get("codex_service_tier")),
    )


def safe_event_detail(method: str, params: Mapping[str, Any]) -> dict[str, object]:
    detail: dict[str, object] = {"method": method}
    if method == "thread/status/changed":
        status = params.get("status")
        if isinstance(status, Mapping):
            detail["status"] = {
                "type": clean_public_value(status.get("type"), 80) or "",
                "activeFlags": [
                    value
                    for raw in status.get("activeFlags", [])
                    if (value := clean_public_value(raw, 80))
                ][:8],
            }
    elif method in {"item/started", "item/completed"}:
        item = params.get("item")
        if isinstance(item, Mapping):
            detail["item"] = {
                key: value
                for key in ("type", "status", "tool")
                if (value := clean_public_value(item.get(key), 80))
            }
    elif method == "turn/completed":
        turn = params.get("turn")
        if isinstance(turn, Mapping):
            detail["turn"] = {
                "status": clean_public_value(turn.get("status"), 80) or ""
            }
    return detail


def evidence_dict(evidence: RuntimeEvidence | None) -> dict[str, Any] | None:
    if evidence is None:
        return None
    return {
        "state": evidence.state.value,
        "source": evidence.source.value,
        "observed_at": evidence.observed_at,
        "confidence": evidence.confidence,
        "detail": safe_evidence_detail(evidence.detail),
    }


def safe_evidence_detail(detail: Mapping[str, Any]) -> dict[str, Any]:
    method = clean_public_value(detail.get("method"), 120)
    result: dict[str, Any] = {"method": method} if method else {}
    status = detail.get("status")
    if isinstance(status, str):
        result["status"] = status[:80]
    elif isinstance(status, Mapping):
        result["status"] = {
            "type": clean_public_value(status.get("type"), 80) or "",
            "activeFlags": [
                value
                for raw in status.get("activeFlags", [])
                if (value := clean_public_value(raw, 80))
            ][:8],
        }
    item = detail.get("item")
    if isinstance(item, Mapping):
        result["item"] = {
            key: value
            for key in ("type", "status", "tool")
            if (value := clean_public_value(item.get(key), 80))
        }
    if detail.get("fallback") is True:
        result["fallback"] = True
    return result


def profile_dict(profile: CodexProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "profile_id": profile.profile_id,
        "model": profile.model,
        "reasoning_effort": profile.reasoning_effort,
        "verbosity": profile.verbosity,
        "reasoning_summary": profile.reasoning_summary,
        "service_tier": profile.service_tier,
    }


def child_dict(child: CodexChildRuntime) -> dict[str, Any]:
    return {
        "child_id": child.child_id,
        "parent_id": child.parent_id,
        "state": child.state.value,
        "role": child.role,
        "model": child.model,
        "reasoning_effort": child.reasoning_effort,
        "started_at": child.started_at,
        "finished_at": child.finished_at,
    }


def child_from_snapshot(raw: object) -> CodexChildRuntime | None:
    if not isinstance(raw, Mapping):
        return None
    child_id = clean_public_value(raw.get("child_id"), 240)
    parent_id = clean_public_value(raw.get("parent_id"), 240)
    if not child_id or not parent_id:
        return None
    try:
        state = CodexRuntimeState(str(raw.get("state") or "unknown"))
    except ValueError:
        state = CodexRuntimeState.UNKNOWN
    return CodexChildRuntime(
        child_id=child_id,
        parent_id=parent_id,
        state=state,
        role=clean_public_value(raw.get("role"), 120),
        task=None,
        model=clean_public_value(raw.get("model"), 160),
        reasoning_effort=clean_public_value(raw.get("reasoning_effort"), 80),
        started_at=safe_float(raw.get("started_at")),
        finished_at=safe_float(raw.get("finished_at")),
    )


def first_text(*values: object) -> str | None:
    for raw in values:
        value = clean_public_value(raw, 4096)
        if value:
            return value
    return None


def clean_public_value(value: object, limit: int) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip().replace("\n", " ").replace("\r", " ")
    return text[:limit] if text else None


def safe_nonnegative_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
