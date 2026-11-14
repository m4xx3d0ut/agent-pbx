from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from ._shared import format_timestamp


class DurableHelperRegistryScreen(ModalScreen[None]):
    """Review root-controlled durable helper lifecycle state."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        helpers: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.helpers = helpers
        self.helper_ids: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-approval-panel"):
            yield Static(
                f"Durable helpers · {self.operator_agent_id}",
                id="operator-completion-title",
            )
            yield Static(
                "Validation compiles the private object without executing helper "
                "code. Promote materializes only a reviewed helper; invocation "
                "remains an explicit separate action.",
                id="operator-completion-summary",
            )
            yield DataTable(
                id="durable-helper-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="operator-handoff-approval-actions"):
                yield Button("Review", id="durable-helper-review")
                yield Button(
                    "Promote", id="durable-helper-promote", variant="success"
                )
                yield Button("Reject", id="durable-helper-reject", variant="error")
                yield Button("Retire", id="durable-helper-retire")
                yield Button(
                    "Quarantine",
                    id="durable-helper-quarantine",
                    variant="warning",
                )
                yield Button("Refresh", id="durable-helper-refresh")
                yield Button("Close", id="durable-helper-close")

    def on_mount(self) -> None:
        table = self.query_one("#durable-helper-table", DataTable)
        table.add_columns("Name", "Status", "Digest", "Scopes", "Helper ID")
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#durable-helper-table", DataTable)
        table.clear(columns=False)
        self.helper_ids = []
        for helper in self.helpers:
            helper_id = str(helper.get("helper_id") or "").strip()
            if not helper_id:
                continue
            self.helper_ids.append(helper_id)
            scopes = helper.get("scopes")
            table.add_row(
                str(helper.get("name") or "-"),
                str(helper.get("status") or "-"),
                str(helper.get("object_sha256") or "-")[:12],
                ", ".join(str(item) for item in scopes)
                if isinstance(scopes, list)
                else "-",
                helper_id,
            )

    def selected_helper_id(self) -> str | None:
        table = self.query_one("#durable-helper-table", DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(self.helper_ids):
            return None
        return self.helper_ids[table.cursor_row]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = str(event.button.id or "")
        if button_id == "durable-helper-close":
            self.dismiss()
            return
        app = self.app
        if button_id == "durable-helper-refresh":
            app.run_worker(  # type: ignore[attr-defined]
                app.refresh_durable_helper_screen(self),  # type: ignore[attr-defined]
                name="durable-helper-refresh",
                exclusive=True,
                exit_on_error=False,
            )
            return
        actions = {
            "durable-helper-review": "review",
            "durable-helper-promote": "active",
            "durable-helper-reject": "rejected",
            "durable-helper-retire": "retired",
            "durable-helper-quarantine": "quarantined",
        }
        action = actions.get(button_id)
        helper_id = self.selected_helper_id()
        if action is None or helper_id is None:
            app.notify("Select one durable helper first", severity="warning")  # type: ignore[attr-defined]
            return
        app.run_worker(  # type: ignore[attr-defined]
            app.apply_durable_helper_action(  # type: ignore[attr-defined]
                self,
                helper_id=helper_id,
                action=action,
            ),
            name=f"durable-helper-{action}",
            exclusive=True,
            exit_on_error=False,
        )


class DynamicResourceApprovalScreen(ModalScreen[None]):
    """Review exact typed resource requests; only modal-local F8 approves."""

    BINDINGS = [
        Binding("f8", "approve_selected", "Approve exact request", priority=True),
        Binding("escape", "dismiss", "Close", priority=True),
    ]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        requests: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.requests = requests

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-approval-panel"):
            yield Static(
                f"Typed dynamic resources · {self.operator_agent_id}",
                id="operator-completion-title",
            )
            yield Static(
                "F8 approves only the highlighted receipt-bound request. "
                "A running sandbox remains restart-required; approval never remaps it.",
                id="dynamic-resource-summary",
            )
            yield DataTable(
                id="dynamic-resource-table",
                cursor_type="row",
                show_row_labels=False,
            )
            yield Static(
                "Select a request to review its exact F8 phrase.",
                id="dynamic-resource-detail",
            )
            with Horizontal(id="operator-handoff-approval-actions"):
                yield Button("Refresh", id="dynamic-resource-refresh")
                yield Button("Close", id="dynamic-resource-close")

    def on_mount(self) -> None:
        table = self.query_one("#dynamic-resource-table", DataTable)
        table.add_columns(
            "Class",
            "Requester",
            "Campaign",
            "Restart",
            "Expires",
            "Request ID",
        )
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#dynamic-resource-table", DataTable)
        table.clear(columns=False)
        for request in self.requests:
            request_id = str(request.get("request_id") or "")
            if not request_id:
                continue
            table.add_row(
                str(request.get("resource_class") or "-"),
                str(request.get("requester_agent_id") or "-"),
                str(request.get("campaign_id") or request.get("handoff_id") or "-")[
                    :12
                ],
                "yes" if request.get("restart_required") is True else "no",
                format_timestamp(request.get("expires_at")),
                request_id,
                key=request_id,
            )
        self._render_selected_detail()

    def current_request(self) -> dict[str, Any] | None:
        table = self.query_one("#dynamic-resource-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            request_id = str(
                table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            )
        except Exception:
            return None
        return next(
            (item for item in self.requests if item.get("request_id") == request_id),
            None,
        )

    def _render_selected_detail(self) -> None:
        request = self.current_request()
        detail = self.query_one("#dynamic-resource-detail", Static)
        if request is None:
            detail.update("No pending manual resource request.")
            return
        detail.update(
            "Request: "
            f"{request.get('request_id')}\n"
            f"Digest: {request.get('request_sha256')}\n"
            f"Workspace: {request.get('workspace_set_digest')}\n"
            f"Spec: {json.dumps(request.get('spec') or {}, sort_keys=True)}\n"
            f"F8 phrase: {request.get('approval_confirmation') or '-'}"
        )

    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._render_selected_detail()

    def action_approve_selected(self) -> None:
        request = self.current_request()
        if request is None:
            self.app.notify("Select one pending resource request", severity="warning")  # type: ignore[attr-defined]
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.approve_dynamic_resource_request(self, request),  # type: ignore[attr-defined]
            name="dynamic-resource-f8-approve",
            exclusive=True,
            exit_on_error=False,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "dynamic-resource-close":
            self.dismiss()
            return
        if event.button.id == "dynamic-resource-refresh":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.refresh_dynamic_resource_screen(self),  # type: ignore[attr-defined]
                name="dynamic-resource-refresh",
                exclusive=True,
                exit_on_error=False,
            )
