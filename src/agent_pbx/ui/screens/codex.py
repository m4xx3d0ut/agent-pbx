from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ._shared import slugify
from ._types import CodexModelPresetView


class ModelElevationScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, preset: CodexModelPresetView) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.preset = preset
        self._submitted = False

    def compose(self) -> ComposeResult:
        with Vertical(id="model-elevation-panel"):
            yield Static("Approve Elevated Codex Model", id="model-elevation-title")
            yield Static(
                f"{self.agent_id} requests {self.preset.label}. "
                "Approval is bounded and audited. Enter the reason for this task.",
                id="model-elevation-help",
            )
            yield Input(
                placeholder="Justification (at least 8 characters)",
                id="model-elevation-justification",
            )
            with Horizontal(id="model-elevation-actions"):
                yield Button(
                    "Approve & Restart",
                    id="model-elevation-approve",
                    variant="warning",
                )
                yield Button("Cancel", id="model-elevation-cancel")

    def submit(self) -> None:
        if self._submitted:
            return
        justification = self.query_one(
            "#model-elevation-justification", Input
        ).value.strip()
        if len(justification) < 8:
            self.notify("A specific justification is required.", severity="warning")
            return
        self._submitted = True
        self.query_one("#model-elevation-justification", Input).disabled = True
        self.query_one("#model-elevation-approve", Button).disabled = True
        self.query_one("#model-elevation-cancel", Button).disabled = True
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.approve_and_restart_model_elevation(  # type: ignore[attr-defined]
                self.agent_id,
                self.preset,
                justification,
            ),
            name=f"model-elevation-{slugify(self.agent_id)}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "model-elevation-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "model-elevation-approve":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "model-elevation-justification":
            event.stop()
            self.submit()

    def on_unmount(self) -> None:
        modal_agents = getattr(self.app, "model_elevation_modal_agent_ids", None)
        if isinstance(modal_agents, set):
            modal_agents.discard(self.agent_id)
