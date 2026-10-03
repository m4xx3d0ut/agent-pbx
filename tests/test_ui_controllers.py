import pytest

from agent_pbx.contracts import ActionDefinition
from agent_pbx.ui.actions import ActionRegistry
from agent_pbx.ui.async_jobs import AsyncGenerationGate
from agent_pbx.ui.focus import FocusGenerationGuard
from agent_pbx.ui.panels.joplin import JoplinPanelState


def test_action_registry_rejects_duplicate_ids() -> None:
    registry = ActionRegistry()
    action = ActionDefinition("focus.events", "Events", default_sequence="f2")
    registry.register(action)
    assert registry.get("focus.events") is action
    with pytest.raises(ValueError, match="already registered"):
        registry.register(action)


def test_focus_guard_invalidates_async_restore_after_operator_move() -> None:
    guard = FocusGenerationGuard()
    before = guard.snapshot()
    guard.changed()
    assert not guard.current(before)
    assert guard.current(guard.snapshot())


def test_async_generation_gate_rejects_stale_result() -> None:
    gate = AsyncGenerationGate()
    first = gate.start("agents")
    second = gate.start("agents")
    assert not gate.current("agents", first)
    assert gate.current("agents", second)


def test_joplin_panel_selection_is_scoped_and_validated() -> None:
    state = JoplinPanelState(
        notes_by_scope={"agent-a": {"note-a": {"id": "note-a"}}}
    )
    state.select("agent-a", "note-a")
    assert state.selected_for("agent-a") == "note-a"
    state.select("agent-b", "note-b")
    assert state.selected_for("agent-a") == "note-a"
    assert state.selected_for("agent-b") == "note-b"


def test_joplin_panel_tracks_explicit_edit_preview_and_dirty_draft() -> None:
    state = JoplinPanelState()
    draft = state.begin_edit(
        "project:demo",
        edit={"edit_id": "edit-1", "dirty": False},
        note={
            "id": "note-1",
            "title": "Plan",
            "body": "base",
            "revision": "rev-1",
        },
    )

    assert state.mode_for("project:demo") == "edit"
    assert draft.dirty is False
    state.update_draft("project:demo", "changed")
    state.set_mode("project:demo", "preview")
    assert state.mode_for("project:demo") == "preview"
    assert state.has_dirty_draft("project:demo") is True
    state.finish_edit("project:demo")
    assert state.mode_for("project:demo") == "read"
    assert state.has_dirty_draft("project:demo") is False
