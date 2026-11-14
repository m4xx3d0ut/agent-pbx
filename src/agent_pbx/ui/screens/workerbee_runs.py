from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from ._shared import format_timestamp


class WorkerBeeControlledRunConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        request: dict[str, Any],
        decision: str,
        approval_screen: "WorkerBeeControlledRunScreen",
    ) -> None:
        super().__init__()
        self.request = request
        self.decision = decision
        self.approval_screen = approval_screen

    def compose(self) -> ComposeResult:
        request_id = str(self.request.get("request_id") or "")
        profile = self.request.get("profile")
        label = (
            str(profile.get("label") or self.request.get("profile_id") or "-")
            if isinstance(profile, dict)
            else str(self.request.get("profile_id") or "-")
        )
        action = {
            "approve": "Approve this exact run once",
            "lease": "Trust this exact campaign/profile lease",
            "reject": "Reject this controlled run",
            "cancel": "Cancel this controlled run",
        }.get(self.decision, self.decision)
        with Vertical(id="workerbee-run-confirm-panel"):
            yield Static(
                "WorkerBee controlled-run confirmation", classes="dialog-title"
            )
            yield Static(
                f"{action}\n\n"
                f"Request: {request_id}\n"
                f"Root: {self.request.get('root_operator_agent_id') or '-'}\n"
                f"Fork: {self.request.get('target_fork_agent_id') or '-'}\n"
                f"Profile: {label}\n"
                f"Baseline: {self.request.get('baseline_commit') or '-'}\n"
                f"Candidate: {self.request.get('candidate_commit') or '-'}\n\n"
                "The appliance must deny external egress and credentials, use immutable "
                "sources, and return a cleanup-complete receipt.",
                id="workerbee-run-confirm-detail",
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Confirm", id="workerbee-run-confirm", variant="warning")
                yield Button("Back", id="workerbee-run-confirm-back")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "workerbee-run-confirm-back":
            self.dismiss()
            return
        if event.button.id == "workerbee-run-confirm":
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.decide_workerbee_controlled_run(  # type: ignore[attr-defined]
                    self.approval_screen,
                    self.request,
                    self.decision,
                ),
                name="workerbee-controlled-run-decision",
                exclusive=True,
            )


class WorkerBeeControlledRunScreen(ModalScreen[None]):
    """Specialized F8 review for root-owned WorkerBee controlled runs."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        requests: list[dict[str, Any]],
        statuses: tuple[str, ...] = ("pending",),
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.requests = requests
        self.statuses = statuses

    def compose(self) -> ComposeResult:
        with Vertical(id="workerbee-run-approval-panel"):
            yield Static(
                f"WorkerBee controlled runs · {self.operator_agent_id}",
                id="workerbee-run-approval-title",
            )
            yield Static(
                "Exact immutable sources, fixed appliance recipe, bounded authority.",
                id="workerbee-run-approval-summary",
            )
            yield DataTable(
                id="workerbee-run-approval-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="workerbee-run-approval-actions"):
                yield Button(
                    "Approve once", id="workerbee-run-approve", variant="warning"
                )
                yield Button(
                    "Trust campaign", id="workerbee-run-trust", variant="warning"
                )
                yield Button("Reject", id="workerbee-run-reject", variant="error")
                yield Button("Cancel", id="workerbee-run-cancel", variant="error")
                yield Button("Refresh", id="workerbee-run-refresh")
                yield Button("Close", id="workerbee-run-close")

    def on_mount(self) -> None:
        table = self.query_one("#workerbee-run-approval-table", DataTable)
        table.add_columns("Created", "Fork", "Profile", "Baseline", "Candidate", "ID")
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#workerbee-run-approval-table", DataTable)
        table.clear(columns=False)
        for request in self.requests:
            request_id = str(request.get("request_id") or "")
            if not request_id:
                continue
            table.add_row(
                format_timestamp(request.get("created_at")),
                str(request.get("target_fork_agent_id") or "-"),
                str(request.get("profile_id") or "-"),
                str(request.get("baseline_commit") or "-")[:10],
                str(request.get("candidate_commit") or "-")[:10],
                request_id,
                key=request_id,
            )
        self.query_one("#workerbee-run-approval-summary", Static).update(
            f"Showing {table.row_count} · every action remains bound to the displayed exact request."
        )

    def current_request(self) -> dict[str, Any] | None:
        table = self.query_one("#workerbee-run-approval-table", DataTable)
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

    def replace_requests(self, requests: list[dict[str, Any]]) -> None:
        self.requests = requests
        self.render_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "workerbee-run-close":
            self.dismiss()
            return
        if button_id == "workerbee-run-refresh":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.refresh_workerbee_controlled_run_screen(self),  # type: ignore[attr-defined]
                name="workerbee-controlled-run-refresh",
                exclusive=True,
            )
            return
        request = self.current_request()
        if request is None:
            self.app.notify("Select a controlled run.", severity="warning")  # type: ignore[attr-defined]
            return
        decision = {
            "workerbee-run-approve": "approve",
            "workerbee-run-trust": "lease",
            "workerbee-run-reject": "reject",
            "workerbee-run-cancel": "cancel",
        }.get(button_id)
        if decision:
            self.app.push_screen(  # type: ignore[attr-defined]
                WorkerBeeControlledRunConfirmScreen(
                    request=request,
                    decision=decision,
                    approval_screen=self,
                )
            )


class WorkerBeeControlledRunLeaseConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        lease: dict[str, Any],
        lease_screen: "WorkerBeeControlledRunLeaseScreen",
    ) -> None:
        super().__init__()
        self.lease = lease
        self.lease_screen = lease_screen

    def compose(self) -> ComposeResult:
        with Vertical(id="workerbee-run-confirm-panel"):
            yield Static("Revoke WorkerBee campaign trust", classes="dialog-title")
            yield Static(
                f"Lease: {self.lease.get('lease_id') or '-'}\n"
                f"Root: {self.lease.get('root_operator_agent_id') or '-'}\n"
                f"Fork: {self.lease.get('target_fork_agent_id') or '-'}\n"
                f"Campaign: {self.lease.get('campaign_id') or '-'}\n"
                f"Profile: {self.lease.get('profile_id') or '-'}\n"
                f"Uses remaining: {self.lease.get('uses_remaining', 0)}\n\n"
                "Revocation prevents this bounded lease from approving another exact run.",
                id="workerbee-run-confirm-detail",
            )
            with Horizontal(classes="dialog-actions"):
                yield Button(
                    "Revoke", id="workerbee-run-lease-confirm", variant="error"
                )
                yield Button("Back", id="workerbee-run-lease-confirm-back")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "workerbee-run-lease-confirm-back":
            self.dismiss()
            return
        if event.button.id == "workerbee-run-lease-confirm":
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.revoke_workerbee_controlled_run_lease(  # type: ignore[attr-defined]
                    self.lease_screen,
                    self.lease,
                ),
                name="workerbee-controlled-run-lease-revoke",
                exclusive=True,
            )


class WorkerBeeControlledRunLeaseScreen(ModalScreen[None]):
    """Review and revoke bounded WorkerBee campaign trust leases."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        leases: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.leases = leases

    def compose(self) -> ComposeResult:
        with Vertical(id="workerbee-run-approval-panel"):
            yield Static(
                f"WorkerBee campaign trust · {self.operator_agent_id}",
                id="workerbee-run-approval-title",
            )
            yield Static(
                "Approved leases remain bound to one root, fork, campaign, profile, "
                "backend generation, source pair, and request digest.",
                id="workerbee-run-approval-summary",
            )
            yield DataTable(
                id="workerbee-run-lease-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="workerbee-run-approval-actions"):
                yield Button("Revoke", id="workerbee-run-lease-revoke", variant="error")
                yield Button("Refresh", id="workerbee-run-lease-refresh")
                yield Button("Close", id="workerbee-run-lease-close")

    def on_mount(self) -> None:
        table = self.query_one("#workerbee-run-lease-table", DataTable)
        table.add_columns("Expires", "Fork", "Profile", "Uses", "Campaign", "ID")
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#workerbee-run-lease-table", DataTable)
        table.clear(columns=False)
        for lease in self.leases:
            lease_id = str(lease.get("lease_id") or "")
            if not lease_id:
                continue
            table.add_row(
                format_timestamp(lease.get("expires_at")),
                str(lease.get("target_fork_agent_id") or "-"),
                str(lease.get("profile_id") or "-"),
                str(lease.get("uses_remaining", 0)),
                str(lease.get("campaign_id") or "-")[:12],
                lease_id,
                key=lease_id,
            )
        self.query_one("#workerbee-run-approval-summary", Static).update(
            f"Showing {table.row_count} active bounded trust lease(s)."
        )

    def current_lease(self) -> dict[str, Any] | None:
        table = self.query_one("#workerbee-run-lease-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            lease_id = str(
                table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            )
        except Exception:
            return None
        return next(
            (item for item in self.leases if item.get("lease_id") == lease_id),
            None,
        )

    def replace_leases(self, leases: list[dict[str, Any]]) -> None:
        self.leases = leases
        self.render_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "workerbee-run-lease-close":
            self.dismiss()
            return
        if event.button.id == "workerbee-run-lease-refresh":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.refresh_workerbee_controlled_run_lease_screen(self),  # type: ignore[attr-defined]
                name="workerbee-controlled-run-lease-refresh",
                exclusive=True,
            )
            return
        if event.button.id == "workerbee-run-lease-revoke":
            lease = self.current_lease()
            if lease is None:
                self.app.notify("Select a campaign trust lease.", severity="warning")  # type: ignore[attr-defined]
                return
            self.app.push_screen(  # type: ignore[attr-defined]
                WorkerBeeControlledRunLeaseConfirmScreen(
                    lease=lease,
                    lease_screen=self,
                )
            )
