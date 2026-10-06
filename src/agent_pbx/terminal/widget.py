from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget

from .pty import PtyProcess
from .screen import VirtualTerminal


_KEY_BYTES: dict[str, bytes] = {
    "enter": b"\r",
    "return": b"\r",
    "escape": b"\x1b",
    "tab": b"\t",
    "shift+tab": b"\x1b[Z",
    "backspace": b"\x7f",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
}

_COLOR_NAMES = {
    "brown": "yellow",
    "brightblack": "bright_black",
    "brightred": "bright_red",
    "brightgreen": "bright_green",
    "brightbrown": "bright_yellow",
    "brightblue": "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan": "bright_cyan",
    "brightwhite": "bright_white",
}

EMBEDDED_SCROLL_MODE_TMUX = "tmux"
EMBEDDED_SCROLL_MODE_CHILD = "child"
EMBEDDED_SCROLL_MODES = frozenset(
    {EMBEDDED_SCROLL_MODE_TMUX, EMBEDDED_SCROLL_MODE_CHILD}
)

TerminalStyleKey = tuple[
    str | None,
    str | None,
    bool,
    bool,
    bool,
    bool,
    bool,
    bool,
]


@lru_cache(maxsize=512)
def _cached_rich_style(key: TerminalStyleKey) -> Style:
    return Style(
        color=key[0],
        bgcolor=key[1],
        bold=key[2],
        italic=key[3],
        underline=key[4],
        reverse=key[5],
        blink=key[6],
        strike=key[7],
    )


def normalize_embedded_scroll_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in EMBEDDED_SCROLL_MODES:
        return normalized
    return EMBEDDED_SCROLL_MODE_TMUX


class TerminalScrollRequested(Message):
    """Request tmux-owned history scrolling for an embedded terminal."""

    def __init__(
        self,
        surface: Widget,
        *,
        direction: int,
        ticks: int = 1,
    ) -> None:
        super().__init__()
        self.surface = surface
        self.direction = -1 if direction < 0 else 1
        self.ticks = max(1, int(ticks))

    @property
    def control(self) -> Widget:
        return self.surface


def terminal_key_bytes(key: str, character: str | None = None) -> bytes | None:
    normalized = key.strip().lower().replace("_", "+")
    if normalized in _KEY_BYTES:
        return _KEY_BYTES[normalized]
    ctrl = re.fullmatch(r"ctrl\+([a-z@\[\\\]\^_?])", normalized)
    if ctrl:
        value = ctrl.group(1)
        if value == "?":
            return b"\x7f"
        return bytes([(ord(value.upper()) - 64) & 0x7F])
    alt = re.fullmatch(r"(?:alt|meta)\+(.+)", normalized)
    if alt and len(alt.group(1)) == 1:
        return b"\x1b" + alt.group(1).encode("utf-8")
    if character:
        return character.encode("utf-8")
    return None


class PbxTerminalSurface(Widget):
    """Focusable Textual surface backed by a disposable normal tmux client."""

    can_focus = True

    def __init__(
        self,
        *,
        id: str | None = None,
        history: int = 4_000,
        poll_interval: float = 0.03,
        scroll_mode: str = EMBEDDED_SCROLL_MODE_TMUX,
    ) -> None:
        super().__init__(id=id)
        self.history = max(0, int(history))
        self.poll_interval = max(0.01, float(poll_interval))
        self.scroll_mode = normalize_embedded_scroll_mode(scroll_mode)
        self.terminal = VirtualTerminal(80, 24, history=self.history)
        self.process: PtyProcess | None = None
        self.target = ""
        self.read_only_client = False
        self.total_pty_bytes = 0
        self._poll_timer: Timer | None = None
        self._rendered_rows: dict[int, Text] = {}
        self._render_screen = self.terminal.screen
        self._render_geometry = (self.terminal.columns, self.terminal.rows)
        self._render_cursor: tuple[bool, bool, int, int] | None = None

    @property
    def attached(self) -> bool:
        return bool(self.process and self.process.alive and self.target)

    def on_mount(self) -> None:
        self._poll_timer = self.set_interval(
            self.poll_interval,
            self.poll_pty,
            pause=True,
        )

    def attach(
        self,
        argv: Sequence[str],
        *,
        target: str,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        read_only: bool = False,
    ) -> None:
        self.detach()
        columns = max(2, self.size.width or 80)
        rows = max(2, self.size.height or 24)
        process_env = dict(os.environ if env is None else env)
        process_env.pop("TMUX", None)
        process_env.pop("TMUX_PANE", None)
        # This PTY is rendered by PbxTerminalSurface rather than the outer
        # terminal. Advertising an inherited screen/tmux TERM makes a nested
        # client choose capabilities for the wrong renderer, which is
        # especially visible through SSH and Termux.
        process_env["TERM"] = "xterm-256color"
        self.terminal = VirtualTerminal(columns, rows, history=self.history)
        self._invalidate_render_cache()
        self.process = PtyProcess(
            argv,
            cwd=cwd,
            env=process_env,
            columns=columns,
            rows=rows,
        ).start()
        self.target = target
        self.read_only_client = read_only
        self.total_pty_bytes = 0
        if self._poll_timer is not None:
            self._poll_timer.resume()
        self.poll_pty()
        self.refresh()

    def detach(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.pause()
        process = self.process
        self.process = None
        if process is not None:
            process.close()
        self.target = ""
        self.read_only_client = False
        self.refresh()

    def write(self, data: bytes) -> bool:
        if not self.attached or self.read_only_client or self.process is None:
            return False
        self.process.write(data)
        return True

    def set_scroll_mode(self, mode: str) -> None:
        self.scroll_mode = normalize_embedded_scroll_mode(mode)

    def poll_pty(self) -> None:
        process = self.process
        if process is None:
            return
        self.sync_geometry()
        chunk = process.read_available(timeout=0.0, limit=262_144)
        if chunk:
            self.total_pty_bytes += len(chunk)
            self.terminal.feed(chunk)
            self.refresh()
        if not process.alive and self._poll_timer is not None:
            self._poll_timer.pause()

    def on_resize(self, event: events.Resize) -> None:
        if self.sync_geometry(event.size.width, event.size.height):
            self.refresh()

    def sync_geometry(
        self,
        columns: int | None = None,
        rows: int | None = None,
    ) -> bool:
        """Keep the virtual screen and child PTY aligned with the widget.

        Textual normally emits ``Resize`` for layout changes, but hidden-tab
        activation and rapid pane-ratio changes can coalesce those events. A
        cheap comparison from the PTY poll closes that gap without issuing a
        repeated ioctl when the geometry is already current.
        """

        requested_columns = int(columns if columns is not None else self.size.width)
        requested_rows = int(rows if rows is not None else self.size.height)
        if requested_columns <= 0 or requested_rows <= 0:
            # ``display: none`` gives the widget a zero-sized region. Retain
            # the last authoritative PTY geometry while a tab or selection is
            # hidden instead of collapsing a still-detaching tmux client to
            # the 80x24 construction fallback.
            return False
        resolved_columns = max(2, requested_columns)
        resolved_rows = max(2, requested_rows)
        changed = (
            self.terminal.columns != resolved_columns
            or self.terminal.rows != resolved_rows
        )
        if changed:
            self.terminal.resize(resolved_columns, resolved_rows)
            self._invalidate_render_cache()
        process = self.process
        if process is not None and (
            process.columns != resolved_columns or process.rows != resolved_rows
        ):
            process.resize(resolved_columns, resolved_rows)
            changed = True
        return changed

    async def _on_key(self, event: events.Key) -> None:
        normalized = event.key.strip().lower().replace("_", "+")
        if (
            normalized in {"shift+pageup", "shift+pagedown"}
            and self.scroll_mode == EMBEDDED_SCROLL_MODE_TMUX
            and self.attached
            and not self.read_only_client
        ):
            self.post_message(
                TerminalScrollRequested(
                    self,
                    direction=-1 if normalized == "shift+pageup" else 1,
                    ticks=max(1, self.terminal.rows // 5),
                )
            )
            event.stop()
            event.prevent_default()
            return
        if re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", normalized):
            await super()._on_key(event)
            return
        data = terminal_key_bytes(event.key, event.character)
        if data is None or not self.write(data):
            await super()._on_key(event)
            return
        event.stop()
        event.prevent_default()

    def on_paste(self, event: events.Paste) -> None:
        if self.write(b"\x1b[200~" + event.text.encode("utf-8") + b"\x1b[201~"):
            event.stop()
            event.prevent_default()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._write_mouse(max(0, event.button - 1), event.x, event.y, event, release=False):
            self.focus()
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._write_mouse(max(0, event.button - 1), event.x, event.y, event, release=True):
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if event.button and self._write_mouse(
            32 + max(0, event.button - 1), event.x, event.y, event, release=False
        ):
            event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._handle_mouse_scroll(event, direction=-1, raw_button=64)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._handle_mouse_scroll(event, direction=1, raw_button=65)

    def _handle_mouse_scroll(
        self,
        event: events.MouseEvent,
        *,
        direction: int,
        raw_button: int,
    ) -> None:
        if not self.attached or self.read_only_client:
            return
        # Shift is the explicit escape hatch for applications that need their
        # own wheel protocol. The default path asks the parent TUI to drive the
        # owning tmux pane's copy mode, keeping Codex prompt history untouched.
        if self.scroll_mode == EMBEDDED_SCROLL_MODE_CHILD or event.shift:
            if self._write_mouse(
                raw_button,
                event.x,
                event.y,
                event,
                release=False,
            ):
                event.stop()
                event.prevent_default()
            return
        delta = abs(int(getattr(event, "delta_y", 0) or 1))
        self.post_message(
            TerminalScrollRequested(self, direction=direction, ticks=delta)
        )
        event.stop()
        event.prevent_default()

    def _write_mouse(
        self,
        button: int,
        x: int,
        y: int,
        event: object,
        *,
        release: bool,
    ) -> bool:
        modifiers = 0
        if bool(getattr(event, "shift", False)):
            modifiers |= 4
        if bool(getattr(event, "meta", False)):
            modifiers |= 8
        if bool(getattr(event, "ctrl", False)):
            modifiers |= 16
        suffix = "m" if release else "M"
        return self.write(
            f"\x1b[<{button + modifiers};{max(1, x + 1)};{max(1, y + 1)}{suffix}".encode()
        )

    def render(self) -> Text:
        screen = self.terminal.screen
        geometry = (self.terminal.columns, self.terminal.rows)
        if screen is not self._render_screen or geometry != self._render_geometry:
            self._invalidate_render_cache()
        cursor = (
            self.has_focus,
            bool(screen.cursor.hidden),
            int(screen.cursor.y),
            int(screen.cursor.x),
        )
        dirty_rows = set(screen.dirty)
        if self._render_cursor != cursor:
            if self._render_cursor is not None:
                dirty_rows.add(self._render_cursor[2])
            dirty_rows.add(cursor[2])
        dirty_rows.update(
            row_index
            for row_index in range(self.terminal.rows)
            if row_index not in self._rendered_rows
        )
        for row_index in dirty_rows:
            if 0 <= row_index < self.terminal.rows:
                self._rendered_rows[row_index] = self._render_row(row_index, cursor)
        screen.dirty.clear()
        self._render_cursor = cursor

        result = Text(no_wrap=True, overflow="crop")
        for row_index in range(self.terminal.rows):
            result.append_text(self._rendered_rows[row_index])
            if row_index < self.terminal.rows - 1:
                result.append("\n")
        return result

    def _render_row(
        self,
        row_index: int,
        cursor: tuple[bool, bool, int, int],
    ) -> Text:
        row = self.terminal.screen.buffer.get(row_index, {})
        result = Text(no_wrap=True, overflow="crop")
        run_key: TerminalStyleKey | None = None
        run_chars: list[str] = []
        focused, cursor_hidden, cursor_y, cursor_x = cursor
        for column in range(self.terminal.columns):
            char = row.get(column)
            value = str(getattr(char, "data", " ") or " ")
            cursor_cell = (
                focused
                and not cursor_hidden
                and row_index == cursor_y
                and column == cursor_x
            )
            style_key = self._rich_style_key(char, cursor=cursor_cell)
            if run_key is not None and style_key != run_key:
                result.append("".join(run_chars), style=_cached_rich_style(run_key))
                run_chars = []
            run_key = style_key
            run_chars.append(value)
        if run_key is not None:
            result.append("".join(run_chars), style=_cached_rich_style(run_key))
        return result

    def _invalidate_render_cache(self) -> None:
        self._rendered_rows.clear()
        self._render_screen = self.terminal.screen
        self._render_geometry = (self.terminal.columns, self.terminal.rows)
        self._render_cursor = None

    @classmethod
    def _rich_style(cls, char: object | None) -> Style:
        return _cached_rich_style(cls._rich_style_key(char))

    @classmethod
    def _rich_style_key(
        cls,
        char: object | None,
        *,
        cursor: bool = False,
    ) -> TerminalStyleKey:
        fg = cls._rich_color(getattr(char, "fg", None))
        bg = cls._rich_color(getattr(char, "bg", None))
        return (
            fg,
            bg,
            bool(getattr(char, "bold", False)),
            bool(getattr(char, "italics", False)),
            bool(getattr(char, "underscore", False)),
            cursor or bool(getattr(char, "reverse", False)),
            bool(getattr(char, "blink", False)),
            bool(getattr(char, "strikethrough", False)),
        )

    @staticmethod
    def _rich_color(value: object) -> str | None:
        color = str(value or "").strip().lower()
        if not color or color == "default":
            return None
        color = _COLOR_NAMES.get(color, color)
        if re.fullmatch(r"[0-9a-f]{6}", color):
            return f"#{color}"
        if color.isdigit():
            return f"color({color})"
        return color

    def on_unmount(self) -> None:
        self.detach()
