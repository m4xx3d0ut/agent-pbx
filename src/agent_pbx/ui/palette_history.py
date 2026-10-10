from __future__ import annotations

from collections.abc import Callable, Iterable
import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import CommandInput, CommandList, CommandPalette
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, OptionList, Static


DEFAULT_PALETTE_HISTORY_LIMIT = 100

_SENSITIVE_COMMAND_PATTERN = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|bearer|"
    r"api[ _-]?key|private[ _-]?key)",
    re.IGNORECASE,
)
_HISTORY_CONTROL_PREFIX = "/palette history"


def safe_palette_command_name(value: object) -> str | None:
    """Return a history-safe slash command name, or ``None`` when private.

    Palette history records only the selected command label. It never accepts
    prompt bodies, multiline values, control characters, or labels that look
    like credential-management actions.
    """

    command = str(value or "").strip()
    if not command.startswith("/") or len(command) > 200:
        return None
    if any(ord(character) < 32 for character in command):
        return None
    if command.casefold().startswith(_HISTORY_CONTROL_PREFIX):
        return None
    if _SENSITIVE_COMMAND_PATTERN.search(command):
        return None
    return command


class PaletteCommandHistory:
    """Bounded, deduplicated command-name history with no prompt storage."""

    def __init__(
        self,
        entries: Iterable[object] = (),
        *,
        limit: int = DEFAULT_PALETTE_HISTORY_LIMIT,
    ) -> None:
        self.limit = max(1, int(limit))
        self._entries: list[str] = []
        for entry in entries:
            self.record(entry)

    @property
    def entries(self) -> tuple[str, ...]:
        """Return entries from oldest to newest."""

        return tuple(self._entries)

    def record(self, value: object) -> bool:
        command = safe_palette_command_name(value)
        if command is None:
            return False
        folded = command.casefold()
        self._entries = [
            entry for entry in self._entries if entry.casefold() != folded
        ]
        self._entries.append(command)
        del self._entries[: max(0, len(self._entries) - self.limit)]
        return True

    def clear(self) -> None:
        self._entries.clear()

    def navigator(self) -> PaletteHistoryNavigator:
        return PaletteHistoryNavigator(self)


class PaletteHistoryNavigator:
    """Per-palette traversal state that preserves the operator's draft."""

    def __init__(self, history: PaletteCommandHistory) -> None:
        self.history = history
        self.index: int | None = None
        self.draft = ""

    @property
    def active(self) -> bool:
        return self.index is not None

    def previous(self, current: str) -> str | None:
        entries = self.history.entries
        if not entries:
            return None
        if self.index is None:
            self.draft = current
            self.index = len(entries) - 1
        elif self.index > 0:
            self.index -= 1
        return entries[self.index]

    def next(self) -> str | None:
        entries = self.history.entries
        if self.index is None or not entries:
            return None
        if self.index < len(entries) - 1:
            self.index += 1
            return entries[self.index]
        self.index = None
        return self.draft


class PbxCommandPalette(CommandPalette):
    """Native Textual palette with shell-style command-name traversal."""

    BINDINGS = [
        Binding(
            "ctrl+end, shift+end",
            "command_list('last')",
            "Go to bottom",
            show=False,
        ),
        Binding(
            "ctrl+home, shift+home",
            "command_list('first')",
            "Go to top",
            show=False,
        ),
        Binding("down", "history_next", "Newer command", show=False),
        Binding("escape", "escape", "Exit the command palette"),
        Binding(
            "pagedown",
            "command_list('page_down')",
            "Next page",
            show=False,
        ),
        Binding(
            "pageup",
            "command_list('page_up')",
            "Previous page",
            show=False,
        ),
        Binding("up", "history_previous", "Older command", show=False),
    ]

    def __init__(
        self,
        history: PaletteCommandHistory,
        *,
        on_record: Callable[[], None] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._pbx_history = history
        self._pbx_navigator = history.navigator()
        self._pbx_on_record = on_record

    def _recall(self, value: str) -> None:
        command_input = self.query_one(CommandInput)
        command_input.value = value
        command_input.cursor_position = len(value)

    def action_history_previous(self) -> None:
        command_input = self.query_one(CommandInput)
        if self.focused is not command_input:
            self._action_command_list("cursor_up")
            return
        recalled = self._pbx_navigator.previous(command_input.value)
        if recalled is None:
            self._action_command_list("cursor_up")
            return
        self._recall(recalled)

    def action_history_next(self) -> None:
        command_input = self.query_one(CommandInput)
        if self.focused is command_input and self._pbx_navigator.active:
            recalled = self._pbx_navigator.next()
            if recalled is not None:
                self._recall(recalled)
                return
        self._action_cursor_down()

    @on(OptionList.OptionSelected)
    def _select_command(self, event: OptionList.OptionSelected) -> None:
        hit = getattr(event.option, "hit", None)
        command = getattr(hit, "text", None)
        if self._pbx_history.record(command) and self._pbx_on_record is not None:
            self._pbx_on_record()
        super()._select_command(event)


class PaletteHistoryScreen(ModalScreen[None]):
    """Read-only palette history inspector with an explicit clear action."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        history: PaletteCommandHistory,
        *,
        persistence_enabled: bool,
        on_clear: Callable[[], None],
    ) -> None:
        super().__init__()
        self.history = history
        self.persistence_enabled = persistence_enabled
        self.on_clear = on_clear

    def compose(self) -> ComposeResult:
        persistence = "on" if self.persistence_enabled else "off (memory only)"
        with Vertical(id="palette-history-panel"):
            yield Static("Command Palette History", id="palette-history-title")
            yield Static(
                f"Persistence: {persistence} · prompt bodies are never stored",
                id="palette-history-summary",
            )
            yield DataTable(
                id="palette-history-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="palette-history-actions"):
                yield Button("Clear", id="palette-history-clear", variant="warning")
                yield Button("Close", id="palette-history-close", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#palette-history-table", DataTable)
        table.add_columns("#", "Command")
        self.render_entries()

    def render_entries(self) -> None:
        table = self.query_one("#palette-history-table", DataTable)
        table.clear()
        for index, command in enumerate(reversed(self.history.entries), start=1):
            table.add_row(str(index), command)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-history-close":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-history-clear":
            event.stop()
            self.on_clear()
            self.render_entries()
