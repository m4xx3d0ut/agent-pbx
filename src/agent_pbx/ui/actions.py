from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..contracts import ActionDefinition


@dataclass
class ActionRegistry:
    """Central metadata index for actions exposed through multiple UI surfaces."""

    _actions: dict[str, ActionDefinition] = field(default_factory=dict)

    def register(self, action: ActionDefinition) -> None:
        if action.action_id in self._actions:
            raise ValueError(f"action already registered: {action.action_id}")
        self._actions[action.action_id] = action

    def extend(self, actions: Iterable[ActionDefinition]) -> None:
        for action in actions:
            self.register(action)

    def get(self, action_id: str) -> ActionDefinition | None:
        return self._actions.get(action_id)

    def all(self) -> tuple[ActionDefinition, ...]:
        return tuple(self._actions.values())

