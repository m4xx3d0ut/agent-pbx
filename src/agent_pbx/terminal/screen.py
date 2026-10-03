from __future__ import annotations

import codecs
from dataclasses import dataclass

import pyte


class PbxHistoryScreen(pyte.HistoryScreen):
    """History screen tolerant of modern private terminal status queries."""

    def report_device_status(self, mode: int, **kwargs: object) -> None:
        # Recent tmux versions emit private DSR queries such as CSI ? 996 n.
        # Pyte dispatches the ``private`` keyword but its Screen method does
        # not accept it. The embedded renderer has no response channel, so a
        # private query is safely ignored while standard DSR keeps pyte's
        # behavior.
        if kwargs.get("private"):
            return
        super().report_device_status(mode)


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
        self.stream = pyte.Stream(self.screen)
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
