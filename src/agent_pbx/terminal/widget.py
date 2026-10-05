from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from rich.style import Style
from rich.text import Text
from textual import events
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
    ) -> None:
        super().__init__(id=id)
        self.history = max(0, int(history))
        self.poll_interval = max(0.01, float(poll_interval))
        self.terminal = VirtualTerminal(80, 24, history=self.history)
        self.process: PtyProcess | None = None
        self.target = ""
        self.read_only_client = False
        self.total_pty_bytes = 0
        self._poll_timer: Timer | None = None

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
        process = self.process
        if process is not None and (
            process.columns != resolved_columns or process.rows != resolved_rows
        ):
            process.resize(resolved_columns, resolved_rows)
            changed = True
        return changed

    async def _on_key(self, event: events.Key) -> None:
        normalized = event.key.strip().lower().replace("_", "+")
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
        if self._write_mouse(64, event.x, event.y, event, release=False):
            event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._write_mouse(65, event.x, event.y, event, release=False):
            event.stop()

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
        result = Text(no_wrap=True, overflow="crop")
        for row_index in range(self.terminal.rows):
            row = screen.buffer.get(row_index, {})
            for column in range(self.terminal.columns):
                char = row.get(column)
                value = str(getattr(char, "data", " ") or " ")
                style = self._rich_style(char)
                if (
                    not bool(screen.cursor.hidden)
                    and row_index == screen.cursor.y
                    and column == screen.cursor.x
                    and self.has_focus
                ):
                    style = style + Style(reverse=True)
                result.append(value, style=style)
            if row_index < self.terminal.rows - 1:
                result.append("\n")
        return result

    @classmethod
    def _rich_style(cls, char: object | None) -> Style:
        if char is None:
            return Style()
        fg = cls._rich_color(getattr(char, "fg", None))
        bg = cls._rich_color(getattr(char, "bg", None))
        return Style(
            color=fg,
            bgcolor=bg,
            bold=bool(getattr(char, "bold", False)),
            italic=bool(getattr(char, "italics", False)),
            underline=bool(getattr(char, "underscore", False)),
            reverse=bool(getattr(char, "reverse", False)),
            blink=bool(getattr(char, "blink", False)),
            strike=bool(getattr(char, "strikethrough", False)),
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
