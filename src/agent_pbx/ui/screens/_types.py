from __future__ import annotations

from typing import Protocol


class CodexModelPresetView(Protocol):
    label: str


class CustomSlashCommandView(Protocol):
    name: str
    description: str
    arg_label: str
    arg_placeholder: str
    arg_required: bool


class LinkedGitBrokerTuiContextView(Protocol):
    agent_id: str
    work_root: str
    authority_sha256: str


class OperatorSessionCandidateView(Protocol):
    session_id: str
    timestamp: float
    source: str
    path: str
    summary: str
