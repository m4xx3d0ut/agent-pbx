from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from ._shared import slugify
from ._types import OperatorSessionCandidateView


class OperatorKillConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, action: str, delete_thread: bool) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.action = action
        self.delete_thread = delete_thread

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-kill-panel"):
            yield Static("Kill Operator Pane", id="operator-kill-title")
            yield Static(
                f"{self.action.title()} {self.agent_id} and kill its tmux pane?",
                id="operator-kill-message",
            )
            with Horizontal(id="operator-kill-actions"):
                yield Button(
                    self.action.title(), id="operator-kill-confirm", variant="error"
                )
                yield Button("Cancel", id="operator-kill-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "operator-kill-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "operator-kill-confirm":
            event.stop()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.dismiss_selected_agent(  # type: ignore[attr-defined]
                    agent_id=self.agent_id,
                    delete_thread=self.delete_thread,
                    confirmed_operator_kill=True,
                ),
                name=f"operator-{self.action}-{slugify(self.agent_id)}",
                exclusive=True,
            )
            self.dismiss()


class OperatorProjectSpawnCancelScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, request: Mapping[str, Any]) -> None:
        super().__init__()
        self.request = dict(request)

    def compose(self) -> ComposeResult:
        request_id = str(self.request.get("spawn_request_id") or "")
        with Vertical(id="operator-kill-panel"):
            yield Static("Cancel Project Spawn", id="operator-kill-title")
            yield Static(
                "Cancel this pending request without creating or changing its target?\n\n"
                f"Request: {request_id}\n"
                f"Project: {self.request.get('project_name') or '-'}\n"
                f"Target: {self.request.get('target_path') or '-'}",
                id="operator-kill-message",
            )
            with Horizontal(id="operator-kill-actions"):
                yield Button(
                    "Cancel Request",
                    id="project-spawn-cancel-confirm",
                    variant="error",
                )
                yield Button("Keep Pending", id="project-spawn-cancel-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-spawn-cancel-close":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "project-spawn-cancel-confirm":
            event.stop()
            request_id = str(self.request.get("spawn_request_id") or "")
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.cancel_project_spawn_request(  # type: ignore[attr-defined]
                    request_id
                ),
                name=f"project-spawn-cancel-{slugify(request_id)}",
                exclusive=True,
            )
            self.dismiss()


class OperatorHistoryScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("enter", "resume_selected", "Resume"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        *,
        agent_id: str,
        candidates: list[OperatorSessionCandidateView],
        resume_target: OperatorSessionCandidateView | None = None,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.candidates = candidates
        self.resume_target = resume_target

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-history-panel"):
            yield Static(
                f"Operator History: {self.agent_id}", id="operator-history-title"
            )
            yield Static(self.summary_text(), id="operator-history-summary")
            yield DataTable(
                id="operator-history-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="operator-history-actions"):
                yield Button("Resume", id="operator-history-resume", variant="primary")
                yield Button("Refresh", id="operator-history-refresh")
                yield Button("Close", id="operator-history-close")

    def on_mount(self) -> None:
        table = self.query_one("#operator-history-table", DataTable)
        table.add_columns("Pick", "Updated", "Source", "Session", "Latest Prompt")
        self.render_candidates()

    def summary_text(self) -> str:
        if not self.candidates:
            return "No local Codex sessions found for this operator."
        if self.resume_target is None:
            return f"{len(self.candidates)} session candidate(s)."
        return f"Default resume target: {self.resume_target.session_id}"

    def candidate_key(self, candidate: OperatorSessionCandidateView) -> str:
        return candidate.session_id

    def format_timestamp(self, value: float) -> str:
        if not value:
            return "-"
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M")

    def render_candidates(self) -> None:
        summary = self.query_one("#operator-history-summary", Static)
        summary.update(self.summary_text())
        table = self.query_one("#operator-history-table", DataTable)
        table.clear()
        for index, candidate in enumerate(self.candidates, start=1):
            marker = "*" if candidate == self.resume_target else str(index)
            table.add_row(
                marker,
                self.format_timestamp(candidate.timestamp),
                candidate.source,
                candidate.session_id,
                candidate.summary,
                key=self.candidate_key(candidate),
            )
        if table.row_count:
            row = 0
            if self.resume_target is not None:
                try:
                    row = table.get_row_index(self.candidate_key(self.resume_target))
                except Exception:
                    row = 0
            table.move_cursor(row=row, animate=False, scroll=False)

    def selected_candidate(self) -> OperatorSessionCandidateView | None:
        table = self.query_one("#operator-history-table", DataTable)
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        row_key = str(table.ordered_rows[table.cursor_row].key.value)
        for candidate in self.candidates:
            if self.candidate_key(candidate) == row_key:
                return candidate
        return None

    def resume_candidate(self) -> None:
        candidate = self.selected_candidate()
        if candidate is None:
            self.notify("Select an operator session first.", severity="warning")
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.resume_selected_operator(  # type: ignore[attr-defined]
                agent_id=self.agent_id,
                resume_candidate=candidate,
            ),
            name=f"operator-resume-{slugify(self.agent_id)}",
            exclusive=True,
        )
        self.dismiss()

    def refresh_candidates(self) -> None:
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.refresh_operator_history_screen(self),  # type: ignore[attr-defined]
            name=f"operator-history-refresh-{slugify(self.agent_id)}",
            exclusive=True,
        )

    def update_candidates(
        self,
        candidates: list[OperatorSessionCandidateView],
        resume_target: OperatorSessionCandidateView | None,
    ) -> None:
        self.candidates = candidates
        self.resume_target = resume_target
        self.render_candidates()

    def action_resume_selected(self) -> None:
        self.resume_candidate()

    def action_refresh(self) -> None:
        self.refresh_candidates()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "operator-history-table":
            return
        event.stop()
        self.resume_candidate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "operator-history-close":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "operator-history-refresh":
            event.stop()
            self.refresh_candidates()
            return
        if event.button.id == "operator-history-resume":
            event.stop()
            self.resume_candidate()


class OperatorAuthorityScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        authority_view: dict[str, Any],
        workspace_sets: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.authority_view = authority_view
        self.workspace_sets = workspace_sets

    def compose(self) -> ComposeResult:
        admin = self.authority_view.get("admin") or {}
        workspace_set = self.authority_view.get("workspace_set") or {}
        read_only = str(admin.get("control_mode") or "editable") != "editable"
        set_options = [("No workspace set", "")]
        set_options.extend(
            (
                f"{item.get('label') or item.get('set_id')} · v{item.get('version')}",
                str(item.get("set_id") or ""),
            )
            for item in self.workspace_sets
        )
        selected_set = str(workspace_set.get("set_id") or "")
        members = ", ".join(
            str(item.get("path") or "")
            for item in workspace_set.get("members") or ()
            if str(item.get("path") or "").strip()
        )
        collections = ", ".join(
            str(item) for item in workspace_set.get("collections") or ()
        )
        with Vertical(id="operator-authority-panel"):
            yield Static("Operator execution authority", id="operator-authority-title")
            yield Static(
                f"{self.agent_id} · control={admin.get('control_mode') or '-'} · "
                f"ceiling={admin.get('profile_ceiling') or '-'}",
                id="operator-authority-posture",
            )
            yield Select(
                [
                    ("Scoped edit", "scoped-edit"),
                    ("Trusted Workbench", "trusted-workbench"),
                    ("Full access (exceptional)", "full-access"),
                    ("Review read-only", "review-readonly"),
                ],
                value=str(
                    self.authority_view.get("requested_profile") or "scoped-edit"
                ),
                id="operator-authority-profile",
                disabled=read_only or bool(self.authority_view.get("review_readonly")),
            )
            yield Select(
                set_options,
                value=selected_set,
                id="operator-authority-set",
                disabled=read_only,
            )
            yield Input(
                value=str(
                    self.authority_view.get("profile_label") or "Rosie Workbench"
                ),
                placeholder="Profile label",
                id="operator-authority-label",
                disabled=read_only,
            )
            yield Input(
                value=str(workspace_set.get("set_id") or "rosie-stackops"),
                placeholder="Workspace set id",
                id="operator-workspace-set-id",
                disabled=read_only,
            )
            yield Input(
                value=str(workspace_set.get("label") or "Rosie StackOps"),
                placeholder="Workspace set label",
                id="operator-workspace-set-label",
                disabled=read_only,
            )
            yield Input(
                value=members,
                placeholder="Explicit Git toplevels, comma-separated",
                id="operator-workspace-set-members",
                disabled=read_only,
            )
            yield Input(
                value=collections,
                placeholder="Collection roots, comma-separated (reviewed snapshot)",
                id="operator-workspace-set-collections",
                disabled=read_only,
            )
            yield Static(
                self.summary(),
                id="operator-authority-summary",
            )
            with Horizontal(id="operator-authority-actions"):
                yield Button(
                    "Preview Set", id="operator-authority-preview", disabled=read_only
                )
                yield Button(
                    "Save Set", id="operator-authority-save-set", disabled=read_only
                )
                yield Button(
                    "Apply",
                    id="operator-authority-apply",
                    variant="primary",
                    disabled=read_only,
                )
                yield Button("Close", id="operator-authority-close")

    def summary(self) -> str:
        workspace_set = self.authority_view.get("workspace_set") or {}
        return (
            f"Effective: {self.authority_view.get('effective_profile') or '-'} · "
            f"set={workspace_set.get('set_id') or '-'} · "
            f"members={workspace_set.get('member_count') or 0} · "
            + (
                "pending safe restart/resume"
                if self.authority_view.get("pending_activation")
                else "active or default policy"
            )
        )

    def assignment_payload(self) -> dict[str, Any]:
        profile = str(self.query_one("#operator-authority-profile", Select).value)
        selected = self.query_one("#operator-authority-set", Select).value
        return {
            "profile": profile,
            "workspace_set_id": (
                (str(selected).strip() or None)
                if profile == "trusted-workbench"
                else None
            ),
            "profile_label": self.query_one(
                "#operator-authority-label", Input
            ).value.strip()
            or None,
        }

    def workspace_set_payload(self) -> tuple[str, dict[str, Any]]:
        set_id = self.query_one("#operator-workspace-set-id", Input).value.strip()
        members = self.query_one("#operator-workspace-set-members", Input).value
        collections = self.query_one("#operator-workspace-set-collections", Input).value
        return set_id, {
            "label": self.query_one(
                "#operator-workspace-set-label", Input
            ).value.strip(),
            "members": [part.strip() for part in members.split(",") if part.strip()],
            "collections": [
                part.strip() for part in collections.split(",") if part.strip()
            ],
            "collection_depth": 1,
        }

    def show_preview(self, payload: dict[str, Any]) -> None:
        self.query_one("#operator-authority-summary", Static).update(
            f"Preview: {payload.get('label') or '-'} · "
            f"members={payload.get('member_count') or 0} · "
            f"digest={str(payload.get('member_digest') or '')[:12]}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "operator-authority-close":
            self.dismiss()
        elif button_id == "operator-authority-preview":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.preview_operator_workspace_set(self),  # type: ignore[attr-defined]
                name="operator-authority-preview",
                exclusive=True,
            )
        elif button_id == "operator-authority-save-set":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_operator_workspace_set(self),  # type: ignore[attr-defined]
                name="operator-authority-save-set",
                exclusive=True,
            )
        elif button_id == "operator-authority-apply":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.apply_operator_authority(self),  # type: ignore[attr-defined]
                name="operator-authority-apply",
                exclusive=True,
            )
