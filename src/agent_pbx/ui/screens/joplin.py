from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..widgets import JoplinReferencePromptTextArea
from ._shared import slugify


class JoplinReferencePromptScreen(ModalScreen[None]):
    """Compose a PBX-expanded Joplin prompt without intercepting Codex input."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="joplin-reference-prompt-panel"):
            yield Static(
                f"Joplin Reference Prompt: {self.agent_id}",
                id="joplin-reference-prompt-title",
            )
            yield Static(
                "Type @joplin:<note> and press Tab to complete. Enter sends; "
                "Ctrl+J inserts a newline. Raw @ in the native Codex pane remains "
                "Codex-owned.",
                id="joplin-reference-prompt-help",
            )
            yield JoplinReferencePromptTextArea(
                agent_id=self.agent_id,
                id="joplin-reference-prompt-input",
                soft_wrap=True,
            )
            with Horizontal(id="joplin-reference-prompt-actions"):
                yield Button(
                    "Expand & Send",
                    id="joplin-reference-prompt-send",
                    variant="primary",
                )
                yield Button("Cancel", id="joplin-reference-prompt-cancel")

    def submit(self) -> None:
        text_area = self.query_one(
            "#joplin-reference-prompt-input",
            JoplinReferencePromptTextArea,
        )
        if not text_area.text.strip():
            self.notify("Enter a prompt containing @joplin:<note>.", severity="warning")
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self._send(text_area.text),
            name=f"joplin-reference-prompt-{slugify(self.agent_id)}",
            exclusive=True,
        )

    async def _send(self, message: str) -> None:
        sent = await self.app.send_joplin_reference_prompt(  # type: ignore[attr-defined]
            self.agent_id,
            message,
        )
        if sent:
            self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-reference-prompt-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-reference-prompt-send":
            event.stop()
            self.submit()


class JoplinNoteTitleScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        action: str,
        note_id: str | None = None,
        current_title: str = "",
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.action = action
        self.note_id = note_id
        self.current_title = current_title

    def compose(self) -> ComposeResult:
        label = "New Joplin Note" if self.action == "new" else "Rename Joplin Note"
        with Vertical(id="joplin-title-panel"):
            yield Static(f"{label}: {self.agent_id}", id="joplin-title-modal-title")
            yield Input(
                value=self.current_title,
                placeholder="Note title",
                id="joplin-title-input",
            )
            with Horizontal(id="joplin-title-actions"):
                yield Button(
                    "Create" if self.action == "new" else "Rename",
                    id="joplin-title-submit",
                    variant="primary",
                )
                yield Button("Cancel", id="joplin-title-cancel")

    def on_mount(self) -> None:
        title_input = self.query_one("#joplin-title-input", Input)
        title_input.focus()
        title_input.cursor_position = len(title_input.value)

    def submit(self) -> None:
        title = self.query_one("#joplin-title-input", Input).value.strip()
        if not title:
            self.notify("Joplin note title is required.", severity="warning")
            return
        app = self.app
        self.dismiss()
        app.run_worker(  # type: ignore[attr-defined]
            app.joplin_title_action_for_agent(  # type: ignore[attr-defined]
                self.agent_id,
                self.action,
                title,
                note_id=self.note_id,
            ),
            name=f"joplin-{self.action}-{slugify(self.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-title-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-title-submit":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "joplin-title-input":
            event.stop()
            self.submit()


class JoplinDeleteConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, note_id: str, title: str) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.note_id = note_id
        self.title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="joplin-delete-panel"):
            yield Static("Delete Joplin Note", id="joplin-delete-title")
            yield Static(self.title, id="joplin-delete-note-title")
            with Horizontal(id="joplin-delete-actions"):
                yield Button("Delete", id="joplin-delete-confirm", variant="error")
                yield Button("Cancel", id="joplin-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-delete-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-delete-confirm":
            event.stop()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.delete_joplin_note(self.agent_id, self.note_id),  # type: ignore[attr-defined]
                name=f"joplin-delete-{slugify(self.agent_id)}",
                exclusive=True,
            )
            self.dismiss()


class JoplinPasteConfirmScreen(ModalScreen[None]):
    """Confirm a large Notes paste without rendering clipboard content."""

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        agent_id: str,
        note_id: str,
        text: str,
        source: str,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.note_id = note_id
        self.text = text
        self.source = source

    def compose(self) -> ComposeResult:
        lines = self.text.count("\n") + 1
        with Vertical(id="joplin-paste-panel"):
            yield Static("Paste into Joplin Note", id="joplin-paste-title")
            yield Static(
                f"{len(self.text)} characters · {lines} lines · {self.source}\n"
                "Clipboard content is not shown or logged.",
                id="joplin-paste-detail",
            )
            with Horizontal(id="joplin-paste-actions"):
                yield Button("Paste", id="joplin-paste-confirm", variant="primary")
                yield Button("Cancel", id="joplin-paste-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-paste-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-paste-confirm":
            event.stop()
            self.dismiss()
            self.app.apply_joplin_clipboard_paste(  # type: ignore[attr-defined]
                self.agent_id,
                self.note_id,
                self.text,
            )
