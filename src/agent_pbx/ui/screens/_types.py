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
