from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FocusGenerationGuard:
    """Prevents asynchronous work from undoing later operator focus changes."""

    generation: int = 0

    def changed(self) -> int:
        self.generation += 1
        return self.generation

    def snapshot(self) -> int:
        return self.generation

    def current(self, snapshot: int) -> bool:
        return snapshot == self.generation

