from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OperatorPanelState:
    campaigns_by_operator: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    selected_campaign_by_operator: dict[str, str] = field(default_factory=dict)
    selected_report_by_operator: dict[str, str] = field(default_factory=dict)
    kb_entries_by_operator: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    kb_queries_by_operator: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )

