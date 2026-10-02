from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from ..contracts import CodexRuntimeState, RuntimeEvidence, RuntimeEvidenceSource, RuntimeIdentity
from .capabilities import CodexCapabilitySnapshot
from .sessions import CodexSessionAssociation
from .state import RuntimeStateReducer


@dataclass(frozen=True)
class RuntimeCapture:
    text: str
    source: str
    session_id: str | None = None
    path: str | None = None
    phase: str | None = None
    line_index: int | None = None
    mtime: float | None = None


class CodexSessionAdapter:
    def __init__(
        self,
        identity: RuntimeIdentity,
        *,
        capabilities: CodexCapabilitySnapshot | None = None,
    ) -> None:
        self.identity = identity
        self.capabilities = capabilities
        self.association = CodexSessionAssociation(identity)
        self.state = RuntimeStateReducer()

    def observe(
        self,
        state: CodexRuntimeState,
        *,
        source: RuntimeEvidenceSource,
        confidence: float = 1.0,
        detail: dict[str, object] | None = None,
        observed_at: float | None = None,
    ) -> RuntimeEvidence:
        return self.state.add(
            RuntimeEvidence(
                state=state,
                source=source,
                observed_at=observed_at if observed_at is not None else time.time(),
                confidence=max(0.0, min(1.0, confidence)),
                detail=detail or {},
            )
        )

    def observe_app_server(
        self,
        *,
        thread_id: str,
        method: str,
        params: dict[str, object] | None = None,
        observed_at: float | None = None,
    ) -> RuntimeEvidence | None:
        if not self.association.bind_app_server(thread_id, source="protocol_event"):
            return None
        return self.state.add_app_server_event(
            method,
            observed_at=observed_at if observed_at is not None else time.time(),
            detail=params,
        )

    def capture_latest(
        self,
        *,
        copy_capture: Callable[[], RuntimeCapture | None],
        transcript_capture: Callable[[], RuntimeCapture | None],
        tmux_capture: Callable[[], RuntimeCapture | None],
    ) -> RuntimeCapture | None:
        """Use the locked `/copy`, transcript, then tmux fallback order."""

        for capture in (copy_capture, transcript_capture, tmux_capture):
            result = capture()
            if result is None:
                continue
            text = result.text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if text:
                return RuntimeCapture(
                    text=text,
                    source=result.source,
                    session_id=result.session_id,
                    path=result.path,
                    phase=result.phase,
                    line_index=result.line_index,
                    mtime=result.mtime,
                )
        return None
