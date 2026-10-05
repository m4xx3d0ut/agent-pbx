from __future__ import annotations

import codecs
from dataclasses import dataclass

import pyte
from pyte.screens import Margins


class PbxHistoryScreen(pyte.HistoryScreen):
    """History screen tolerant of modern private terminal status queries."""

    def scroll_up(self, count: int | None = None) -> None:
        """Implement ECMA-48 SU within the active scrolling margins.

        Pyte 0.8.2 does not dispatch ``CSI S``. Current tmux uses that
        sequence to clear and redraw Codex composer overlays, so ignoring it
        leaves popup rows behind after Escape.
        """

        top, bottom = self.margins or Margins(0, self.lines - 1)
        amount = min(max(1, int(count or 1)), bottom - top + 1)
        self.dirty.update(range(top, bottom + 1))
        for row in range(top, bottom + 1):
            source = row + amount
            if source <= bottom and source in self.buffer:
                self.buffer[row] = self.buffer.pop(source)
            else:
                self.buffer.pop(row, None)

    def scroll_down(self, count: int | None = None) -> None:
        """Implement ECMA-48 SD within the active scrolling margins."""

        top, bottom = self.margins or Margins(0, self.lines - 1)
        amount = min(max(1, int(count or 1)), bottom - top + 1)
        self.dirty.update(range(top, bottom + 1))
        for row in range(bottom, top - 1, -1):
            source = row - amount
            if source >= top and source in self.buffer:
                self.buffer[row] = self.buffer.pop(source)
            else:
                self.buffer.pop(row, None)

    def report_device_status(self, mode: int, **kwargs: object) -> None:
        # Recent tmux versions emit private DSR queries such as CSI ? 996 n.
        # Pyte dispatches the ``private`` keyword but its Screen method does
        # not accept it. The embedded renderer has no response channel, so a
        # private query is safely ignored while standard DSR keeps pyte's
        # behavior.
        if kwargs.get("private"):
            return
        super().report_device_status(mode)


class PbxStream(pyte.Stream):
    """Pyte stream extended with sequences emitted by current tmux."""

    csi = {
        **pyte.Stream.csi,
        "S": "scroll_up",
        "T": "scroll_down",
    }


@dataclass(frozen=True)
class TerminalSnapshot:
    columns: int
    rows: int
    lines: tuple[str, ...]
    cursor_x: int
    cursor_y: int
    cursor_hidden: bool
    title: str


class VirtualTerminal:
    """Pyte-backed screen hidden behind a PBX-owned stable interface."""

    def __init__(self, columns: int = 80, rows: int = 24, *, history: int = 2000) -> None:
        self.columns = max(2, int(columns))
        self.rows = max(2, int(rows))
        self.history = max(0, int(history))
        self.screen = PbxHistoryScreen(self.columns, self.rows, history=self.history)
        self.stream = PbxStream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, data: bytes) -> None:
        if not data:
            return
        text = self.decoder.decode(data)
        if text:
            self.stream.feed(text)

    def resize(self, columns: int, rows: int) -> None:
        self.columns = max(2, int(columns))
        self.rows = max(2, int(rows))
        self.screen.resize(lines=self.rows, columns=self.columns)

    def snapshot(self) -> TerminalSnapshot:
        return TerminalSnapshot(
            columns=self.columns,
            rows=self.rows,
            lines=tuple(self.screen.display),
            cursor_x=self.screen.cursor.x,
            cursor_y=self.screen.cursor.y,
            cursor_hidden=bool(self.screen.cursor.hidden),
            title=str(getattr(self.screen, "title", "") or ""),
        )
