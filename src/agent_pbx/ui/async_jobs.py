from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AsyncGenerationGate:
    """Tracks the newest request for independently refreshed UI resources."""

    _generations: dict[str, int] = field(default_factory=dict)

    def start(self, resource: str) -> int:
        generation = self._generations.get(resource, 0) + 1
        self._generations[resource] = generation
        return generation

    def current(self, resource: str, generation: int) -> bool:
        return self._generations.get(resource, 0) == generation

    def invalidate(self, resource: str) -> int:
        return self.start(resource)

