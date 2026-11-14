from __future__ import annotations

from typing import Any, Protocol

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class OperatorHandoffApprovalScreenProtocol(Protocol):
    app: Any


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
