from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


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
