from types import SimpleNamespace

import pytest

from agent_pbx import tui as tui_module
from agent_pbx.contracts import ActionDefinition
from agent_pbx.tui import AgentPBXTUI
from agent_pbx.ui.actions import ActionRegistry
from agent_pbx.ui.async_jobs import AsyncGenerationGate
from agent_pbx.ui.controllers import TUIController, controller_method
from agent_pbx.ui.controllers.github import GitHubController
from agent_pbx.ui.controllers.joplin import JoplinController
from agent_pbx.ui.controllers.operator_handoffs import OperatorHandoffController
from agent_pbx.ui.controllers.references import PromptReferenceController
from agent_pbx.ui.controllers.workerbee import WorkerBeeController
from agent_pbx.ui.controllers.workerbee_projection import (
    WorkerBeeProjectionController,
)
from agent_pbx.ui.focus import FocusGenerationGuard
from agent_pbx.ui.github import github_ref_number
from agent_pbx.ui.panels.joplin import JoplinPanelState
from agent_pbx.ui import references as ui_references
from agent_pbx.workerbee_mcp import WorkerBeeMcpPreflight


class _ExampleController(TUIController[SimpleNamespace]):
    def increment(self, amount: int = 1) -> int:
        self.value += amount
        return self.value


class _ExampleHost(SimpleNamespace):
    increment = controller_method("controller", "increment")

    def __init__(self) -> None:
        super().__init__(value=1)
        self.controller = _ExampleController(self)


def test_controller_method_preserves_host_state_and_legacy_call_shape() -> None:
    host = _ExampleHost()

    assert host.increment(2) == 3
    assert host.value == 3
    assert host.controller.value == 3


def test_workerbee_projection_controller_preserves_nested_status_rendering() -> None:
    controller = WorkerBeeProjectionController(SimpleNamespace())
    rendered = controller.format_workerbee_status(
        {
            "agent_id": "operator-8",
            "cwd": "/workspace",
            "workerbee_bin": "/usr/bin/workerbee",
            "available": True,
            "project": "workerbee",
            "mode": "local",
            "status_kind": "ready",
            "running": True,
            "dashboard_url": "https://dashboard.workerbee.home.arpa:19443/",
            "state_dir": "/state",
            "description": "Debug appliance",
            "app_status": {
                "state": "ready",
                "ready": True,
                "message": "green",
                "declared_workload_count": 1,
                "ready_workload_count": 1,
                "degraded_workload_count": 0,
                "orphaned_workload_count": 0,
                "declared_workloads": [
                    {"kind": "Deployment", "namespace": "demo", "name": "api"}
                ],
            },
        }
    )

    assert "Agent: operator-8" in rendered
    assert "Status: ready" in rendered
    assert "Workloads: 1 declared, 1 ready, 0 degraded, 0 orphaned" in rendered
    assert "- Deployment demo/api" in rendered


def test_workerbee_projection_controller_redacts_preflight_query() -> None:
    controller = WorkerBeeProjectionController(SimpleNamespace())

    rendered = controller.format_workerbee_mcp_preflight(
        WorkerBeeMcpPreflight(
            code="WORKERBEE_READY",
            ready=True,
            message="ready",
            remediation="",
            endpoint="https://workerbee.example/mcp?token=secret",
            workerbee_version="1.0.0",
        )
    )

    assert "Endpoint: https://workerbee.example/mcp" in rendered
    assert "secret" not in rendered


def test_workerbee_projection_controller_keeps_legacy_tui_entry_points() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert isinstance(
        app._workerbee_projection_controller,
        WorkerBeeProjectionController,
    )
    assert app.format_workerbee_workload(
        {"kind": "Service", "namespace": "demo", "name": "web"}
    ) == "Service demo/web"


def test_github_controller_preserves_reference_numbers_and_tui_entry_points() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert github_ref_number("12") == 12
    assert github_ref_number(0) is None
    assert github_ref_number("invalid") is None
    assert isinstance(app._github_controller, GitHubController)
    assert app.format_pull_request_checks(
        {"total": 3, "success": 2, "pending": 1}
    ) == "1 pending/3"
    assert "State: open" in app.format_issue_detail(
        {
            "number": 7,
            "title": "Controller extraction",
            "state": "open",
            "body": "Preserve behavior.",
        }
    )


def test_prompt_reference_controller_preserves_public_aliases_and_formatting() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    reference = ui_references.CallerAgentReference(
        token="@caller:builder",
        agent_id="builder-1",
        name="Builder",
        project="demo",
        status="working",
        pbx_mode="report",
        pbx_active=True,
        active_campaign_count=1,
    )

    assert isinstance(app._prompt_reference_controller, PromptReferenceController)
    assert tui_module.CallerAgentReference is ui_references.CallerAgentReference
    assert tui_module.JoplinNoteReference is ui_references.JoplinNoteReference
    assert tui_module.CALLER_AGENT_REF_PATTERN is ui_references.CALLER_AGENT_REF_PATTERN
    assert tui_module.format_caller_agent_references_for_prompt(
        "Inspect the caller.",
        [reference],
    ).startswith("Inspect the caller.\n\n---\n\n## Caller Agent References")
    assert ui_references.joplin_note_ref_completion_tokens(
        [{"id": "note-1", "title": "Release Plan"}]
    ) == ("@joplin:Release-Plan",)


def test_joplin_controller_preserves_legacy_status_formatters() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert isinstance(app._joplin_controller, JoplinController)
    assert (
        app.format_joplin_sync_summary(
            {
                "enabled": True,
                "write_sync": {"running": 0, "pending": 1},
            }
        )
        == "Writes: queued 1"
    )
    assert (
        app.format_joplin_profile_summary(
            {
                "profile_validation": {
                    "running": 0,
                    "pending": 0,
                    "current_status": "succeeded",
                }
            },
            {"pending_total": 0},
        )
        == "Profile: ok"
    )
    assert app.format_joplin_time(0.0) == "1970-01-01"


def test_controller_honors_instance_level_host_overrides() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    replacement = object()
    app.ensure_joplin_available = replacement  # type: ignore[method-assign]

    assert app._joplin_controller.ensure_joplin_available is replacement


def test_operator_handoff_controller_preserves_legacy_tui_entry_points() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert isinstance(app._operator_handoff_controller, OperatorHandoffController)
    assert app.pending_source_handoffs(
        "operator-0",
        [
            {
                "logical_operator_agent_id": "operator-0",
                "status": "approved",
                "handoff_id": "handoff-1",
            }
        ],
    )[0]["handoff_id"] == "handoff-1"


def test_workerbee_controller_preserves_legacy_tui_entry_points() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert isinstance(app._workerbee_controller, WorkerBeeController)
    assert app.load_workerbee_status.__self__ is app._workerbee_controller


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


def test_joplin_panel_reader_position_requires_same_revision() -> None:
    state = JoplinPanelState()
    state.remember_reader_position(
        "project:demo",
        "note-1",
        revision="rev-1",
        scroll_y=24,
        scroll_target_y=25,
    )

    position = state.reader_position(
        "project:demo",
        "note-1",
        revision="rev-1",
    )

    assert position is not None
    assert position.scroll_y == 24
    assert position.scroll_target_y == 25
    assert (
        state.reader_position("project:demo", "note-1", revision="rev-2")
        is None
    )
