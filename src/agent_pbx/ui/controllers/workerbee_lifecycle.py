from __future__ import annotations

from typing import Any, Mapping

from ...workerbee_profile import (
    WorkerBeeProfileIntentError,
    resolve_workerbee_profile_intent,
)
from ._base import TUIController


class WorkerBeeLifecycleController(TUIController[Any]):
    def workerbee_profile_for_agent(self, agent: Mapping[str, Any]) -> str:
        metadata = {
            **self.agent_metadata(agent),
            "agent_type": self.agent_type(agent),
        }
        return resolve_workerbee_profile_intent(
            str(agent.get("agent_id") or agent.get("id") or "unknown"),
            metadata,
        ).profile

    async def workerbee_profile_for_lifecycle_agent(
        self,
        agent_id: str,
        agent: dict[str, Any],
        *,
        action: str,
    ) -> tuple[dict[str, Any], str] | None:
        """Resolve launch authority after one safe refresh of stale TUI state."""

        try:
            return agent, self.workerbee_profile_for_agent(agent)
        except WorkerBeeProfileIntentError:
            await self.refresh_agents()

        refreshed = self.agents.get(agent_id)
        if not isinstance(refreshed, dict):
            self.notify(
                f"Unable to {action} {agent_id}: refreshed Agent state is unavailable.",
                severity="error",
            )
            return None
        try:
            profile = self.workerbee_profile_for_agent(refreshed)
        except WorkerBeeProfileIntentError as exc:
            self.notify(
                f"Unable to {action} {agent_id}: WorkerBee authority still requires "
                f"policy reconciliation after refresh ({exc}).",
                severity="error",
            )
            return None
        return refreshed, profile
