from __future__ import annotations

from dataclasses import dataclass


DEFAULT_NAVIGATION_HISTORY_LIMIT = 64


@dataclass(frozen=True)
class NavigationDestination:
    """A stable PBX destination independent of table order and widget focus."""

    entity_id: str
    tab_id: str

    def __post_init__(self) -> None:
        if not self.entity_id.strip() or not self.tab_id.strip():
            raise ValueError("navigation destinations require entity and tab ids")


class NavigationHistory:
    """Bounded browser-style history for one TUI client."""

    def __init__(self, *, limit: int = DEFAULT_NAVIGATION_HISTORY_LIMIT) -> None:
        self.limit = max(2, int(limit))
        self._entries: list[NavigationDestination] = []
        self._index = -1

    @property
    def entries(self) -> tuple[NavigationDestination, ...]:
        return tuple(self._entries)

    @property
    def index(self) -> int:
        return self._index

    @property
    def current(self) -> NavigationDestination | None:
        if 0 <= self._index < len(self._entries):
            return self._entries[self._index]
        return None

    def record(self, destination: NavigationDestination | None) -> bool:
        if destination is None:
            return False
        if self.current == destination:
            return False
        if self._index < len(self._entries) - 1:
            del self._entries[self._index + 1 :]
        self._entries.append(destination)
        if len(self._entries) > self.limit:
            overflow = len(self._entries) - self.limit
            del self._entries[:overflow]
        self._index = len(self._entries) - 1
        return True

    def back(
        self,
        current: NavigationDestination | None = None,
    ) -> NavigationDestination | None:
        self.record(current)
        if self._index <= 0:
            return None
        self._index -= 1
        return self._entries[self._index]

    def forward(
        self,
        current: NavigationDestination | None = None,
    ) -> NavigationDestination | None:
        self.record(current)
        if self._index < 0 or self._index >= len(self._entries) - 1:
            return None
        self._index += 1
        return self._entries[self._index]

    def restore_index(self, index: int) -> None:
        """Restore a traversal cursor after every candidate was unavailable."""

        if not self._entries:
            self._index = -1
            return
        self._index = min(max(0, int(index)), len(self._entries) - 1)
