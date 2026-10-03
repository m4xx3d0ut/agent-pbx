from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JoplinDraft:
    note_id: str
    edit_id: str
    title: str
    body: str
    base_revision: str
    base_title: str
    base_body: str
    dirty: bool = False
    conflict_id: str | None = None
    merged_title: str | None = None
    merged_body: str | None = None


@dataclass
class JoplinPanelState:
    notes_by_scope: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    selected_by_scope: dict[str, str] = field(default_factory=dict)
    selected_note_id: str | None = None
    mode_by_scope: dict[str, str] = field(default_factory=dict)
    drafts_by_scope: dict[str, JoplinDraft] = field(default_factory=dict)

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

    def mode_for(self, scope: str) -> str:
        return self.mode_by_scope.get(scope, "read")

    def set_mode(self, scope: str, mode: str) -> None:
        if mode not in {"read", "edit", "preview", "conflict"}:
            raise ValueError(f"invalid Joplin panel mode: {mode}")
        self.mode_by_scope[scope] = mode

    def draft_for(self, scope: str) -> JoplinDraft | None:
        return self.drafts_by_scope.get(scope)

    def begin_edit(self, scope: str, *, edit: dict[str, Any], note: dict[str, Any]) -> JoplinDraft:
        draft = JoplinDraft(
            note_id=str(note.get("id") or ""),
            edit_id=str(edit.get("edit_id") or ""),
            title=str(edit.get("draft_title") or note.get("title") or ""),
            body=str(edit.get("draft_body") or note.get("body") or ""),
            base_revision=str(note.get("revision") or edit.get("base_revision") or ""),
            base_title=str(note.get("title") or ""),
            base_body=str(note.get("body") or ""),
            dirty=bool(edit.get("dirty")),
        )
        self.drafts_by_scope[scope] = draft
        self.mode_by_scope[scope] = "edit"
        return draft

    def update_draft(self, scope: str, body: str) -> JoplinDraft | None:
        draft = self.drafts_by_scope.get(scope)
        if draft is None:
            return None
        draft.body = body
        draft.dirty = body != draft.base_body or draft.title != draft.base_title
        return draft

    def finish_edit(self, scope: str) -> None:
        self.drafts_by_scope.pop(scope, None)
        self.mode_by_scope[scope] = "read"

    def has_dirty_draft(self, scope: str | None = None) -> bool:
        if scope is not None:
            draft = self.drafts_by_scope.get(scope)
            return bool(draft and draft.dirty)
        return any(draft.dirty for draft in self.drafts_by_scope.values())
