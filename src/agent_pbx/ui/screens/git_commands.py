from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class GitDiffScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-gitdiff-panel"):
            yield Static(f"Git Diff: {self.agent_id}", id="palette-gitdiff-title")
            yield Static(
                "Optional file or branch:file target",
                id="palette-gitdiff-help",
            )
            yield Input(
                placeholder="README.md or main:README.md",
                id="palette-gitdiff-target",
            )
            with Horizontal(id="palette-gitdiff-actions"):
                yield Button("Run Diff", id="palette-gitdiff-run", variant="primary")
                yield Button("Cancel", id="palette-gitdiff-cancel")

    def submit(self) -> None:
        target = self.query_one("#palette-gitdiff-target", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_git_diff_target(self.agent_id, target),  # type: ignore[attr-defined]
            name="palette-gitdiff",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-gitdiff-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-gitdiff-run":
            event.stop()
            self.submit()


class GitPushScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-gitdiff-panel"):
            yield Static(f"Git Push: {self.agent_id}", id="palette-gitdiff-title")
            yield Static(
                "Optional branch/ref. Leave blank to push the current branch with HEAD.",
                id="palette-gitdiff-help",
            )
            yield Input(
                placeholder="current branch",
                id="palette-gitpush-branch",
            )
            with Horizontal(id="palette-gitdiff-actions"):
                yield Button("Push", id="palette-gitpush-run", variant="primary")
                yield Button("Cancel", id="palette-gitpush-cancel")

    def submit(self) -> None:
        branch = self.query_one("#palette-gitpush-branch", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_git_push_target(self.agent_id, branch),  # type: ignore[attr-defined]
            name="palette-gitpush",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-gitpush-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-gitpush-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-gitpush-branch":
            event.stop()
            self.submit()
