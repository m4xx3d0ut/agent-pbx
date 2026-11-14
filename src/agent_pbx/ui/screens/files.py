from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from ._shared import slugify


class FilePathActionScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        *,
        agent_id: str,
        action: str,
        source: str | None = None,
        initial_path: str = "",
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.action = action
        self.source = source
        self.initial_path = initial_path

    def compose(self) -> ComposeResult:
        title = {
            "create_file": "Create File",
            "create_directory": "Create Directory",
            "move": "Move or Rename Path",
        }.get(self.action, "File Action")
        with Vertical(id="file-path-action-panel"):
            yield Static(title, id="file-path-action-title")
            if self.source:
                yield Static(f"Source: {self.source}", id="file-path-action-source")
            yield Input(
                value=self.initial_path,
                placeholder="project-relative path",
                id="file-path-action-input",
            )
            with Horizontal(id="file-path-action-actions"):
                yield Button("Apply", id="file-path-action-apply", variant="primary")
                yield Button("Cancel", id="file-path-action-cancel")

    def on_mount(self) -> None:
        target = self.query_one("#file-path-action-input", Input)
        target.focus()
        target.cursor_position = len(target.value)

    def submit(self) -> None:
        path = self.query_one("#file-path-action-input", Input).value.strip()
        if not path:
            self.notify("A project-relative path is required.", severity="warning")
            return
        self.dismiss()
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.apply_file_path_action(  # type: ignore[attr-defined]
                self.agent_id,
                self.action,
                path,
                source=self.source,
            ),
            name=f"file-{self.action}-{slugify(self.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "file-path-action-cancel":
            self.dismiss()
            return
        if event.button.id == "file-path-action-apply":
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "file-path-action-input":
            self.submit()


class FileDeleteConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, *, agent_id: str, preview: dict[str, Any]) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.preview = preview

    def compose(self) -> ComposeResult:
        tracked = self.preview.get("delete_mode") == "git_delete"
        mode = "Git-visible deletion" if tracked else "Recoverable project trash"
        detail = (
            f"{self.preview.get('path')}\n"
            f"{self.preview.get('kind')} · {self.preview.get('item_count')} item(s) · "
            f"{self.preview.get('size')} bytes\n{mode}"
        )
        with Vertical(id="file-delete-panel"):
            yield Static("Delete Path", id="file-delete-title")
            yield Static(detail, id="file-delete-detail")
            with Horizontal(id="file-delete-actions"):
                yield Button("Delete", id="file-delete-confirm", variant="error")
                yield Button("Cancel", id="file-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "file-delete-cancel":
            self.dismiss()
            return
        if event.button.id == "file-delete-confirm":
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.apply_file_delete(self.agent_id, self.preview),  # type: ignore[attr-defined]
                name=f"file-delete-{slugify(self.agent_id)}",
                exclusive=True,
            )


class FileTrashScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, entries: list[dict[str, Any]]) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.entries = entries
        self.selected_trash_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="file-trash-panel"):
            yield Static("Project Trash", id="file-trash-title")
            yield DataTable(
                id="file-trash-entries",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="file-trash-actions"):
                yield Button("Restore", id="file-trash-restore", variant="primary")
                yield Button("Prune Expired", id="file-trash-prune")
                yield Button("Undo Prune", id="file-trash-undo")
                yield Button("Close", id="file-trash-close")

    def on_mount(self) -> None:
        table = self.query_one("#file-trash-entries", DataTable)
        table.add_columns("Created", "Path", "State")
        for entry in self.entries:
            trash_id = str(entry.get("trash_id") or "")
            if not trash_id:
                continue
            table.add_row(
                (
                    datetime.fromtimestamp(
                        float(entry.get("created_at") or 0),
                        tz=timezone.utc,
                    ).strftime("%Y-%m-%d %H:%M")
                    if entry.get("created_at")
                    else "-"
                ),
                str(entry.get("original_path") or ""),
                str(entry.get("git_state") or ""),
                key=trash_id,
            )
        if self.entries:
            self.selected_trash_id = str(self.entries[0].get("trash_id") or "") or None
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "file-trash-entries":
            self.selected_trash_id = str(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = str(event.button.id or "")
        if action == "file-trash-close":
            self.dismiss()
            return
        if action == "file-trash-restore":
            if not self.selected_trash_id:
                self.notify("Select a trash entry first.", severity="warning")
                return
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.restore_file_trash(self.agent_id, self.selected_trash_id),  # type: ignore[attr-defined]
                name=f"file-trash-restore-{slugify(self.agent_id)}",
                exclusive=True,
            )
            return
        if action == "file-trash-prune":
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.prune_file_trash(self.agent_id),  # type: ignore[attr-defined]
                name=f"file-trash-prune-{slugify(self.agent_id)}",
                exclusive=True,
            )
            return
        if action == "file-trash-undo":
            self.dismiss()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.undo_file_trash_prune(self.agent_id),  # type: ignore[attr-defined]
                name=f"file-trash-undo-{slugify(self.agent_id)}",
                exclusive=True,
            )


class EditorCloseConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, *, agent_id: str, path: str) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="editor-close-panel"):
            yield Static("Close Editor File", id="editor-close-title")
            yield Static(self.path, id="editor-close-path")
            yield Static(
                "This editor buffer has unsaved changes.",
                id="editor-close-message",
            )
            with Horizontal(id="editor-close-actions"):
                yield Button(
                    "Save and Close",
                    id="editor-close-save",
                    variant="primary",
                )
                yield Button(
                    "Discard",
                    id="editor-close-discard",
                    variant="error",
                )
                yield Button("Cancel", id="editor-close-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "editor-close-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "editor-close-save":
            event.stop()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_and_close_editor_document(self.agent_id),  # type: ignore[attr-defined]
                name=f"editor-save-close-{slugify(self.agent_id)}",
                exclusive=True,
            )
            self.dismiss()
            return
        if event.button.id == "editor-close-discard":
            event.stop()
            self.app.close_editor_document(  # type: ignore[attr-defined]
                self.agent_id,
                notify=True,
                discarded=True,
            )
            self.dismiss()
