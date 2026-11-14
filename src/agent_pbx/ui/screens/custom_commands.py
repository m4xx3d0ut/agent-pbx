from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ._shared import slugify
from ._types import CustomSlashCommandView


class CustomSlashCommandArgScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, command: CustomSlashCommandView) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.command = command

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-custom-command-panel"):
            yield Static(
                f"{self.command.name}: {self.agent_id}",
                id="palette-custom-command-title",
            )
            yield Static(self.command.description, id="palette-custom-command-help")
            yield Input(
                placeholder=self.command.arg_placeholder,
                id="palette-custom-command-arg",
            )
            with Horizontal(id="palette-custom-command-actions"):
                yield Button("Send", id="palette-custom-command-send", variant="primary")
                yield Button("Cancel", id="palette-custom-command-cancel")

    def submit(self) -> None:
        arg = self.query_one("#palette-custom-command-arg", Input).value
        if self.command.arg_required and not arg.strip():
            self.notify(f"{self.command.arg_label} is required.", severity="warning")
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_custom_slash_command_arg(  # type: ignore[attr-defined]
                self.agent_id,
                self.command,
                arg,
            ),
            name=f"palette-custom-{slugify(self.command.name)}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-custom-command-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-custom-command-send":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-custom-command-arg":
            event.stop()
            self.submit()
