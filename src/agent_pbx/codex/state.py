from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import CodexRuntimeState, RuntimeEvidence, RuntimeEvidenceSource


APP_SERVER_STATE_EVENTS: dict[str, CodexRuntimeState] = {
    "thread/started": CodexRuntimeState.READY,
    "turn/started": CodexRuntimeState.THINKING,
    "turn/completed": CodexRuntimeState.COMPLETE,
    "turn/interrupted": CodexRuntimeState.INTERRUPTED,
    "item/commandExecution/started": CodexRuntimeState.EXECUTING,
    "item/commandExecution/completed": CodexRuntimeState.THINKING,
    "item/agentMessage/delta": CodexRuntimeState.THINKING,
    "item/toolCall/started": CodexRuntimeState.WAITING_TOOL,
    "item/toolCall/completed": CodexRuntimeState.THINKING,
    "server/error": CodexRuntimeState.ERROR,
}

THREAD_STATUS_STATES: dict[str, CodexRuntimeState] = {
    "notLoaded": CodexRuntimeState.STARTING,
    "idle": CodexRuntimeState.READY,
    "systemError": CodexRuntimeState.ERROR,
    "active": CodexRuntimeState.THINKING,
}

TOOL_ITEM_TYPES = {
    "dynamicToolCall",
    "imageGeneration",
    "mcpToolCall",
    "webSearch",
}

EXECUTION_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
}


@dataclass
class RuntimeStateReducer:
    evidence: list[RuntimeEvidence] = field(default_factory=list)
    max_evidence: int = 100

    def add(self, item: RuntimeEvidence) -> RuntimeEvidence:
        self.evidence.append(item)
        self.evidence = self.evidence[-max(1, self.max_evidence) :]
        return item

    def current(self) -> RuntimeEvidence | None:
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda item: (item.priority, item.observed_at))

    def add_app_server_event(
        self,
        method: str,
        *,
        observed_at: float,
        detail: dict[str, object] | None = None,
    ) -> RuntimeEvidence | None:
        state = app_server_event_state(method, detail or {})
        if state is None:
            return None
        return self.add(
            RuntimeEvidence(
                state=state,
                source=RuntimeEvidenceSource.APP_SERVER,
                observed_at=observed_at,
                confidence=1.0,
                detail={"method": method, **(detail or {})},
            )
        )


def app_server_event_state(
    method: str,
    params: dict[str, object],
) -> CodexRuntimeState | None:
    """Normalize current and compatibility app-server event shapes.

    The app-server protocol intentionally remains behind capability probing.
    Keep protocol spelling in this module so the UI only consumes PBX states.
    """

    if method == "thread/status/changed":
        status = params.get("status")
        if not isinstance(status, dict):
            return None
        status_type = str(status.get("type") or "")
        active_flags = {
            str(value) for value in status.get("activeFlags", []) if value
        }
        if active_flags & {"waitingOnApproval", "waitingOnUserInput"}:
            return CodexRuntimeState.WAITING_USER
        return THREAD_STATUS_STATES.get(status_type)

    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        item_type = str(item.get("type") or "")
        if item_type == "collabAgentToolCall":
            return (
                CodexRuntimeState.DELEGATING
                if method == "item/started"
                else CodexRuntimeState.THINKING
            )
        if item_type in EXECUTION_ITEM_TYPES:
            if method == "item/completed" and str(item.get("status") or "") in {
                "failed",
                "declined",
            }:
                return CodexRuntimeState.ERROR
            return (
                CodexRuntimeState.EXECUTING
                if method == "item/started"
                else CodexRuntimeState.THINKING
            )
        if item_type in TOOL_ITEM_TYPES:
            if method == "item/completed" and str(item.get("status") or "") == "failed":
                return CodexRuntimeState.ERROR
            return (
                CodexRuntimeState.WAITING_TOOL
                if method == "item/started"
                else CodexRuntimeState.THINKING
            )
        if item_type == "agentMessage":
            return CodexRuntimeState.THINKING

    return APP_SERVER_STATE_EVENTS.get(method)
