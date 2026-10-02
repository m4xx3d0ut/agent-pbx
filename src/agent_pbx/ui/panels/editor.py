from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EditorPanelState:
    paths_by_agent: dict[str, str] = field(default_factory=dict)
    entries_by_agent: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    selected_path_by_agent: dict[str, str] = field(default_factory=dict)
    selected_line_by_agent: dict[str, int] = field(default_factory=dict)
    documents_by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)
    dirty_agent_ids: set[str] = field(default_factory=set)

