from __future__ import annotations

import codecs
from dataclasses import dataclass

import pyte


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
        self.screen = pyte.HistoryScreen(self.columns, self.rows, history=self.history)
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

