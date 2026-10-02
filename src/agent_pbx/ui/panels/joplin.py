from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JoplinPanelState:
    notes_by_scope: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    selected_by_scope: dict[str, str] = field(default_factory=dict)
    selected_note_id: str | None = None

    def selected_for(self, scope: str) -> str | None:
        selected = self.selected_by_scope.get(scope)
        if selected:
            return selected
        if not self.selected_note_id:
            return None
        notes = self.notes_by_scope.get(scope)
        if notes and self.selected_note_id not in notes:
            return None
        return self.selected_note_id

    def select(self, scope: str, note_id: str | None) -> None:
        if note_id:
            self.selected_by_scope[scope] = note_id
        else:
            self.selected_by_scope.pop(scope, None)
        self.selected_note_id = note_id

