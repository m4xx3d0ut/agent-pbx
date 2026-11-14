from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TextArea

from ._shared import slugify


class PullRequestMergeConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        number: int,
        title: str,
        method: str = "squash",
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.number = number
        self.title = title
        self.method = method

    def compose(self) -> ComposeResult:
        expected = f"merge PR #{self.number}"
        with Vertical(id="pr-merge-panel"):
            yield Static("Merge Pull Request", id="pr-merge-title")
            yield Static(f"#{self.number} {self.title}", id="pr-merge-summary")
            yield Static(
                f'Type "{expected}" to confirm {self.method} merge.',
                id="pr-merge-help",
            )
            yield Input(placeholder=expected, id="pr-merge-confirm")
            with Horizontal(id="pr-merge-actions"):
                yield Button("Merge", id="pr-merge-run", variant="error")
                yield Button("Cancel", id="pr-merge-cancel")

    def submit(self) -> None:
        confirm = self.query_one("#pr-merge-confirm", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.merge_pull_request(  # type: ignore[attr-defined]
                self.agent_id,
                self.number,
                method=self.method,
                confirm=confirm,
            ),
            name=f"pr-merge-{slugify(self.agent_id)}-{self.number}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pr-merge-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "pr-merge-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pr-merge-confirm":
            event.stop()
            self.submit()


class IssueClearConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        number: int,
        title: str,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.number = number
        self.title = title

    def compose(self) -> ComposeResult:
        expected = f"clear issue #{self.number}"
        with Vertical(id="issue-clear-panel"):
            yield Static("Clear GitHub Issue", id="issue-clear-title")
            yield Static(f"#{self.number} {self.title}", id="issue-clear-summary")
            yield TextArea(
                "",
                id="issue-clear-comment",
                language="markdown",
                soft_wrap=True,
            )
            yield Static(
                f'Type "{expected}" to comment and close.',
                id="issue-clear-help",
            )
            yield Input(placeholder=expected, id="issue-clear-confirm")
            with Horizontal(id="issue-clear-actions"):
                yield Button("Clear", id="issue-clear-run", variant="error")
                yield Button("Cancel", id="issue-clear-cancel")

    def submit(self) -> None:
        comment = self.query_one("#issue-clear-comment", TextArea).text
        confirm = self.query_one("#issue-clear-confirm", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.clear_issue(  # type: ignore[attr-defined]
                self.agent_id,
                self.number,
                comment=comment,
                confirm=confirm,
            ),
            name=f"issue-clear-{slugify(self.agent_id)}-{self.number}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "issue-clear-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "issue-clear-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "issue-clear-confirm":
            event.stop()
            self.submit()
