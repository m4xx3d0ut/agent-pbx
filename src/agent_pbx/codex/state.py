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
        state = APP_SERVER_STATE_EVENTS.get(method)
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

