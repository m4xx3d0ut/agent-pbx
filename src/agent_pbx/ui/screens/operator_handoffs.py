from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any, Protocol

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from ...operator_delegation import delegation_lease_ineligibility_reason
from ..presentation_options import OPERATOR_HANDOFF_CANCEL_REASON_CHOICES


class OperatorHandoffApprovalScreenProtocol(Protocol):
    app: Any


def operator_ready_action_control(action: Mapping[str, Any]) -> str:
    """Describe the explicit TUI control that advances one durable action."""

    return {
        "stop": "/operator stop",
        "broker_review": "/operator git commit",
        "broker_commit": (
            "finish the open broker confirmation "
            "(/operator git recover after an interrupted transaction)"
        ),
        "resume": "/operator resume",
        "validate": "/operator action validate",
    }.get(str(action.get("next_action") or ""), "manual review")


class OperatorReadyActionScreen(ModalScreen[None]):
    """Review one approved target-side action without executing it automatically."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, action: dict[str, Any]) -> None:
        super().__init__()
        self.action = action

    def compose(self) -> ComposeResult:
        scope = (
            self.action.get("execution_scope")
            if isinstance(self.action.get("execution_scope"), dict)
            else {}
        )
        paths = scope.get("exact_paths") if isinstance(scope.get("exact_paths"), list) else []
        guarded_control = operator_ready_action_control(self.action)
        with Vertical(id="operator-handoff-confirm-panel"):
            yield Static("Operator ready action", id="operator-handoff-confirm-title")
            yield Static(
                "\n".join(
                    [
                        f"State: {self.action.get('state') or '-'}",
                        f"Next: {self.action.get('next_action') or 'manual review'}",
                        f"Guarded control: {guarded_control}",
                        f"Handoff: {self.action.get('handoff_id') or '-'}",
                        f"Campaign: {self.action.get('campaign_id') or '-'}",
                        f"Target: {self.action.get('target_agent_id') or '-'}",
                        f"Transaction: {self.action.get('transaction_id') or 'not started'}",
                        f"Scope: {str(self.action.get('scope_sha256') or '-')[:16]}",
                        "Exact paths:",
                        *(f"- {path}" for path in paths),
                        "",
                        str(self.action.get("reason") or "Use the guarded palette action shown by Next. Steps are never chained automatically."),
                        "This alert remains active until the guarded control records its receipt.",
                    ]
                ),
                id="operator-handoff-confirm-summary",
            )
            with Horizontal(id="operator-handoff-confirm-actions"):
                if self.action.get("next_action"):
                    yield Button(
                        f"Run next · {guarded_control}",
                        id="operator-ready-action-run",
                        variant="warning",
                    )
                yield Button("Focus target", id="operator-ready-action-focus", variant="primary")
                yield Button("Close", id="operator-ready-action-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "operator-ready-action-close":
            self.dismiss()
            return
        if event.button.id == "operator-ready-action-focus":
            work = self.app.focus_operator_ready_action(self.action)  # type: ignore[attr-defined]
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                work,
                name="operator-ready-action-focus",
                exclusive=True,
            )
            return
        if event.button.id == "operator-ready-action-run":
            work = self.app.advance_operator_ready_action(self.action)  # type: ignore[attr-defined]
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                work,
                name="operator-ready-action-run",
                exclusive=True,
                exit_on_error=False,
            )


class OperatorHandoffApprovalConfirmScreen(ModalScreen[None]):
    """Confirm delivery of an explicit, immutable set of handoff ids."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
        approval_screen: "OperatorHandoffApprovalScreenProtocol",
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.handoff_ids = handoff_ids
        self.approval_screen = approval_screen

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-confirm-panel"):
            yield Static("Approve Operator handoffs", id="operator-handoff-confirm-title")
            yield Static(
                "The following exact handoff IDs will be preflighted again and approved:\n\n"
                + "\n".join(self.handoff_ids),
                id="operator-handoff-confirm-summary",
            )
            with Horizontal(id="operator-handoff-confirm-actions"):
                yield Button("Approve exact IDs", id="operator-handoff-confirm", variant="warning")
                yield Button("Cancel", id="operator-handoff-confirm-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "operator-handoff-confirm-cancel":
            self.dismiss()
            return
        if event.button.id == "operator-handoff-confirm":
            work = self.app.approve_operator_handoff_ids(  # type: ignore[attr-defined]
                self.operator_agent_id,
                self.handoff_ids,
                approval_screen=self.approval_screen,
            )
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                work,
                name="operator-handoff-approve-exact",
                exclusive=True,
            )


class OperatorDelegationLeaseConfirmScreen(ModalScreen[None]):
    """Approve one bounded Trusted Workbench campaign delegation lease."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        handoff_ids: tuple[str, ...],
        approval_screen: "OperatorHandoffApprovalScreenProtocol",
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.handoff_ids = handoff_ids
        self.approval_screen = approval_screen
        self.submitted = False

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-confirm-panel"):
            yield Static("Approve bounded campaign delegation", id="operator-handoff-confirm-title")
            yield Static(
                "This local Trusted Workbench lease is limited to the selected "
                "handoffs' campaign, targets, workspace digest, action classes, "
                "roots, and exact paths. It lasts one hour, permits at most eight "
                "matching uses, and keeps credentials, provider actions, deploy, "
                "publication, push/tag/release, workspace expansion, authority "
                "changes, and destructive work manual.\n\n"
                + "\n".join(self.handoff_ids),
                id="operator-handoff-confirm-summary",
            )
            with Horizontal(id="operator-handoff-confirm-actions"):
                yield Button("Approve campaign lease", id="operator-delegation-confirm", variant="warning")
                yield Button("Cancel", id="operator-delegation-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "operator-delegation-cancel":
            self.dismiss()
            return
        if event.button.id == "operator-delegation-confirm":
            if self.submitted:
                return
            self.submitted = True
            app = self.app
            self.query_one("#operator-delegation-confirm", Button).disabled = True
            self.query_one("#operator-delegation-cancel", Button).disabled = True
            work = app.create_operator_delegation_lease_from_handoffs(  # type: ignore[attr-defined]
                self.operator_agent_id,
                self.handoff_ids,
                approval_screen=self.approval_screen,
            )
            self.dismiss()
            app.run_worker(  # type: ignore[attr-defined]
                work,
                name="operator-delegation-lease-create",
                exclusive=True,
                exit_on_error=False,
            )


class OperatorHandoffCancelConfirmScreen(ModalScreen[None]):
    """Confirm audited withdrawal of pre-delivery Operator handoffs."""

    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        handoffs: tuple[dict[str, Any], ...],
        approval_screen: "OperatorHandoffApprovalScreen",
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.handoffs = handoffs
        self.handoff_ids = tuple(
            str(handoff.get("handoff_id") or "") for handoff in handoffs
        )
        self.approval_screen = approval_screen

    def compose(self) -> ComposeResult:
        lines = []
        for handoff in self.handoffs:
            handoff_id = str(handoff.get("handoff_id") or "-")
            target = str(handoff.get("target_operator_agent_id") or "-")
            objective = OperatorHandoffApprovalScreen.truncate(
                str(handoff.get("objective") or "-"),
                72,
            )
            lines.append(f"{handoff_id} · {target}\n  {objective}")
        with Vertical(id="operator-handoff-confirm-panel"):
            yield Static("Cancel Operator handoffs", id="operator-handoff-confirm-title")
            yield Static(
                "Withdraw these exact requests before delivery. Their records and "
                "audit evidence will be retained.\n\n" + "\n".join(lines),
                id="operator-handoff-confirm-summary",
            )
            yield Select(
                OPERATOR_HANDOFF_CANCEL_REASON_CHOICES,
                value="superseded",
                allow_blank=False,
                id="operator-handoff-cancel-reason",
            )
            yield Input(
                placeholder="Required detail for Other; optional otherwise",
                id="operator-handoff-cancel-detail",
            )
            yield Input(
                placeholder="Replacement handoff IDs, separated by spaces or commas",
                id="operator-handoff-cancel-replacements",
            )
            with Horizontal(id="operator-handoff-confirm-actions"):
                yield Button(
                    "Cancel exact IDs",
                    id="operator-handoff-cancel-confirm",
                    variant="error",
                )
                yield Button("Back", id="operator-handoff-cancel-back")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "operator-handoff-cancel-back":
            self.dismiss()
            return
        if event.button.id != "operator-handoff-cancel-confirm":
            return
        reason = str(
            self.query_one("#operator-handoff-cancel-reason", Select).value or ""
        ).strip()
        detail = self.query_one("#operator-handoff-cancel-detail", Input).value.strip()
        replacements_text = self.query_one(
            "#operator-handoff-cancel-replacements", Input
        ).value
        replacement_ids = tuple(
            dict.fromkeys(
                value
                for value in re.split(r"[\s,]+", replacements_text.strip())
                if value
            )
        )
        if reason == "other" and not detail:
            self.app.notify(  # type: ignore[attr-defined]
                "Cancellation detail is required when the reason is Other.",
                severity="warning",
            )
            return
        if reason == "superseded" and not replacement_ids:
            self.app.notify(  # type: ignore[attr-defined]
                "Superseded cancellations require at least one replacement handoff ID.",
                severity="warning",
            )
            return
        if set(self.handoff_ids).intersection(replacement_ids):
            self.app.notify(  # type: ignore[attr-defined]
                "A canceled handoff cannot be its own replacement.",
                severity="warning",
            )
            return
        work = self.app.cancel_operator_handoff_ids(  # type: ignore[attr-defined]
            self.operator_agent_id,
            self.handoff_ids,
            reason=reason,
            detail=detail,
            superseded_by_handoff_ids=replacement_ids,
            approval_screen=self.approval_screen,
        )
        self.dismiss()
        self.app.run_worker(  # type: ignore[attr-defined]
            work,
            name="operator-handoff-cancel-exact",
            exclusive=True,
        )


class OperatorHandoffCompletionScreen(ModalScreen[None]):
    """Review and acknowledge source-addressed handoff completions."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        recipient_agent_id: str,
        deliveries: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.recipient_agent_id = recipient_agent_id
        self.deliveries = deliveries

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-approval-panel"):
            yield Static(
                f"Operator completion receipts · {self.recipient_agent_id}",
                id="operator-completion-title",
            )
            yield Static(
                "Review the authoritative terminal result before acknowledging it.",
                id="operator-completion-summary",
            )
            yield DataTable(
                id="operator-completion-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="operator-handoff-approval-actions"):
                yield Button("Focus sender", id="operator-completion-focus")
                yield Button(
                    "Reviewed",
                    id="operator-completion-ack",
                    variant="success",
                )
                yield Button("Retry delivery", id="operator-completion-retry")
                yield Button("Refresh", id="operator-completion-refresh")
                yield Button("Close", id="operator-completion-close")

    def on_mount(self) -> None:
        table = self.query_one("#operator-completion-table", DataTable)
        table.add_columns("Created", "From", "Result", "Delivery", "Summary", "ID")
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#operator-completion-table", DataTable)
        table.clear(columns=False)
        for delivery in self.deliveries:
            delivery_id = str(delivery.get("completion_delivery_id") or "").strip()
            if not delivery_id:
                continue
            table.add_row(
                OperatorHandoffApprovalScreen.format_timestamp(
                    delivery.get("created_at")
                ),
                str(delivery.get("target_operator_agent_id") or "-"),
                str(delivery.get("terminal_status") or "-"),
                str(delivery.get("delivery_status") or "-"),
                OperatorHandoffApprovalScreen.truncate(
                    str(delivery.get("summary") or "-"),
                    56,
                ),
                delivery_id,
                key=delivery_id,
            )
        self.query_one("#operator-completion-summary", Static).update(
            f"Unreviewed {len(self.deliveries)} · acknowledgement stops retries "
            "without changing the terminal handoff result."
        )

    def replace_deliveries(self, deliveries: list[dict[str, Any]]) -> None:
        self.deliveries = deliveries
        self.render_rows()

    def current_delivery(self) -> dict[str, Any] | None:
        table = self.query_one("#operator-completion-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            delivery_id = str(
                table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            )
        except Exception:
            return None
        return next(
            (
                item
                for item in self.deliveries
                if str(item.get("completion_delivery_id") or "") == delivery_id
            ),
            None,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "operator-completion-close":
            self.dismiss()
            return
        delivery = self.current_delivery()
        if delivery is None:
            self.app.notify("Select a completion receipt first.", severity="warning")
            return
        if button_id == "operator-completion-focus":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.focus_operator_completion_receipt(self, delivery),  # type: ignore[attr-defined]
                name="operator-completion-focus",
                exclusive=True,
                exit_on_error=False,
            )
        elif button_id == "operator-completion-ack":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.acknowledge_operator_completion_receipt(self, delivery),  # type: ignore[attr-defined]
                name="operator-completion-ack",
                exclusive=True,
                exit_on_error=False,
            )
        elif button_id == "operator-completion-retry":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.retry_operator_completion_receipt(self, delivery),  # type: ignore[attr-defined]
                name="operator-completion-retry",
                exclusive=True,
                exit_on_error=False,
            )
        elif button_id == "operator-completion-refresh":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.refresh_operator_completion_screen(self),  # type: ignore[attr-defined]
                name="operator-completion-refresh",
                exclusive=True,
                exit_on_error=False,
            )


class OperatorHandoffApprovalScreen(ModalScreen[None]):
    """Durable action center for exact-ID Operator handoff approvals."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("space", "toggle_selected", "Mark"),
    ]

    def __init__(
        self,
        *,
        operator_agent_id: str,
        handoffs: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.operator_agent_id = operator_agent_id
        self.handoffs = handoffs
        self.marked_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-handoff-approval-panel"):
            yield Static(
                f"Operator handoff approvals · {self.operator_agent_id}",
                id="operator-handoff-approval-title",
            )
            yield Static(
                "Select a row, or press Space to mark several exact IDs.",
                id="operator-handoff-approval-summary",
            )
            yield DataTable(
                id="operator-handoff-approval-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="operator-handoff-approval-actions"):
                yield Button("Preflight", id="operator-handoff-preflight")
                yield Button("Approve selected", id="operator-handoff-approve", variant="warning")
                yield Button(
                    "Trust & approve",
                    id="operator-handoff-trust-campaign",
                    variant="warning",
                )
                yield Button("Cancel selected", id="operator-handoff-cancel", variant="error")
                yield Button("Refresh", id="operator-handoff-refresh")
                yield Button("Close", id="operator-handoff-close")

    def on_mount(self) -> None:
        table = self.query_one("#operator-handoff-approval-table", DataTable)
        table.add_columns("Mark", "Created", "Target", "Class", "Status", "Objective", "ID")
        self.render_rows()

    def render_rows(self) -> None:
        table = self.query_one("#operator-handoff-approval-table", DataTable)
        table.clear(columns=False)
        valid_ids: set[str] = set()
        for handoff in self.handoffs:
            handoff_id = str(handoff.get("handoff_id") or "").strip()
            if not handoff_id:
                continue
            valid_ids.add(handoff_id)
            scope = handoff.get("execution_scope")
            action_class = (
                str(scope.get("action_class") or "legacy")
                if isinstance(scope, dict)
                else "legacy"
            )
            table.add_row(
                "●" if handoff_id in self.marked_ids else "○",
                self.format_timestamp(handoff.get("created_at")),
                str(handoff.get("target_operator_agent_id") or "-"),
                action_class,
                str(handoff.get("status") or "-"),
                self.truncate(str(handoff.get("objective") or "-"), 48),
                handoff_id,
                key=handoff_id,
            )
        self.marked_ids.intersection_update(valid_ids)
        self.query_one("#operator-handoff-approval-summary", Static).update(
            f"Pending {len(valid_ids)} · marked {len(self.marked_ids)} · "
            "approval always targets the displayed immutable IDs."
        )

    @staticmethod
    def format_timestamp(value: Any) -> str:
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).strftime(
                "%Y-%m-%d %H:%M"
            )
        except (TypeError, ValueError, OSError):
            return "-"

    @staticmethod
    def truncate(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"

    def current_handoff_id(self) -> str | None:
        table = self.query_one("#operator-handoff-approval-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return None

    def selected_handoff_ids(self) -> tuple[str, ...]:
        if self.marked_ids:
            return tuple(
                handoff_id
                for handoff_id in self.handoff_order()
                if handoff_id in self.marked_ids
            )
        current = self.current_handoff_id()
        return (current,) if current else ()

    def selected_handoffs(self) -> tuple[dict[str, Any], ...]:
        selected_ids = set(self.selected_handoff_ids())
        return tuple(
            handoff
            for handoff in self.handoffs
            if str(handoff.get("handoff_id") or "") in selected_ids
        )

    def handoff_order(self) -> tuple[str, ...]:
        return tuple(
            str(item.get("handoff_id") or "")
            for item in self.handoffs
            if str(item.get("handoff_id") or "").strip()
        )

    def action_toggle_selected(self) -> None:
        handoff_id = self.current_handoff_id()
        if handoff_id is None:
            return
        if handoff_id in self.marked_ids:
            self.marked_ids.remove(handoff_id)
        else:
            self.marked_ids.add(handoff_id)
        self.render_rows()

    def replace_handoffs(self, handoffs: list[dict[str, Any]]) -> None:
        self.handoffs = handoffs
        self.render_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "operator-handoff-close":
            self.dismiss()
            return
        if button_id == "operator-handoff-refresh":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.refresh_operator_handoff_approval_screen(self),  # type: ignore[attr-defined]
                name="operator-handoff-approval-refresh",
                exclusive=True,
            )
            return
        handoff_ids = self.selected_handoff_ids()
        if not handoff_ids:
            self.app.notify("Select at least one handoff.", severity="warning")  # type: ignore[attr-defined]
            return
        if button_id == "operator-handoff-preflight":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.preflight_operator_handoff_ids(  # type: ignore[attr-defined]
                    self.operator_agent_id,
                    handoff_ids,
                ),
                name="operator-handoff-preflight-exact",
                exclusive=True,
            )
        elif button_id == "operator-handoff-approve":
            self.app.push_screen(  # type: ignore[attr-defined]
                OperatorHandoffApprovalConfirmScreen(
                    operator_agent_id=self.operator_agent_id,
                    handoff_ids=handoff_ids,
                    approval_screen=self,
                )
            )
        elif button_id == "operator-handoff-trust-campaign":
            selected = self.selected_handoffs()
            reason = delegation_lease_ineligibility_reason(selected)
            if reason is not None:
                self.app.notify(reason, severity="warning")  # type: ignore[attr-defined]
                return
            self.app.push_screen(  # type: ignore[attr-defined]
                OperatorDelegationLeaseConfirmScreen(
                    operator_agent_id=self.operator_agent_id,
                    handoff_ids=handoff_ids,
                    approval_screen=self,
                )
            )
        elif button_id == "operator-handoff-cancel":
            selected = self.selected_handoffs()
            if len(selected) != len(handoff_ids):
                self.app.notify(  # type: ignore[attr-defined]
                    "One or more selected handoffs changed; refresh and retry.",
                    severity="error",
                )
                return
            self.app.push_screen(  # type: ignore[attr-defined]
                OperatorHandoffCancelConfirmScreen(
                    operator_agent_id=self.operator_agent_id,
                    handoffs=selected,
                    approval_screen=self,
                )
            )
