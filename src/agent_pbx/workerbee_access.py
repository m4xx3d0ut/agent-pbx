from __future__ import annotations

import os
from typing import Any, Mapping

from .store import Store
from .workerbee_gateway import (
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
)


WORKERBEE_ACCESS_CONTROL_MODE_ENV = "AGENT_PBX_WORKERBEE_ACCESS_CONTROL_MODE"
WORKERBEE_ACCESS_PROFILE_CEILING_ENV = "AGENT_PBX_WORKERBEE_ACCESS_PROFILE_CEILING"
WORKERBEE_ACCESS_CONTROL_MODES = ("editable", "read_only", "hidden")
WORKERBEE_ACCESS_PROFILE_CEILINGS = (
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
)


class WorkerBeeAccessPolicyError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


class WorkerBeeAccessPolicyService:
    """Persist explicit per-entity WorkerBee authority without self-elevation."""

    def __init__(
        self,
        store: Store,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)

    def posture(self) -> dict[str, str]:
        control = str(
            self.environ.get(WORKERBEE_ACCESS_CONTROL_MODE_ENV) or "editable"
        ).strip().lower()
        if control not in WORKERBEE_ACCESS_CONTROL_MODES:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                f"Unsupported WorkerBee access control mode: {control!r}.",
            )
        ceiling = str(
            self.environ.get(WORKERBEE_ACCESS_PROFILE_CEILING_ENV)
            or WORKERBEE_PROFILE_LOCAL_DEPLOY
        ).strip().lower()
        if ceiling not in WORKERBEE_ACCESS_PROFILE_CEILINGS:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                f"Unsupported WorkerBee access profile ceiling: {ceiling!r}.",
            )
        return {"control_mode": control, "profile_ceiling": ceiling}

    def get_profile(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_AGENT_NOT_FOUND",
                "Agent is not registered.",
            )
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        review = str(metadata.get("access_mode") or "").strip() == "review_readonly"
        requested = str(metadata.get("workerbee_mcp_profile") or "").strip()
        if review:
            effective = WORKERBEE_PROFILE_REVIEW
        elif requested == WORKERBEE_PROFILE_LOCAL_DEPLOY:
            effective = WORKERBEE_PROFILE_LOCAL_DEPLOY
        else:
            effective = WORKERBEE_PROFILE_OBSERVE
        posture = self.posture()
        constrained = False
        if (
            effective == WORKERBEE_PROFILE_LOCAL_DEPLOY
            and posture["profile_ceiling"] == WORKERBEE_PROFILE_OBSERVE
        ):
            effective = WORKERBEE_PROFILE_OBSERVE
            constrained = True
        return {
            "api_version": "agent-pbx.workerbee-access-policy/v1",
            "agent_id": agent_id,
            "requested_profile": requested or WORKERBEE_PROFILE_OBSERVE,
            "effective_profile": effective,
            "review_readonly": review,
            "control_mode": posture["control_mode"],
            "profile_ceiling": posture["profile_ceiling"],
            "constrained": constrained,
            "requires_restart_on_change": True,
        }

    def set_profile(
        self,
        agent_id: str,
        *,
        profile: str,
        updated_by: str,
    ) -> dict[str, Any]:
        posture = self.posture()
        if posture["control_mode"] != "editable":
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_POLICY_LOCKED",
                "WorkerBee access authority is administrator-managed.",
            )
        selected = str(profile or "").strip().lower()
        if selected not in {WORKERBEE_PROFILE_OBSERVE, WORKERBEE_PROFILE_LOCAL_DEPLOY}:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_PROFILE_INVALID",
                f"Unsupported WorkerBee access profile: {profile!r}.",
            )
        current = self.get_profile(agent_id)
        if current["review_readonly"] and selected != WORKERBEE_PROFILE_OBSERVE:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_REVIEW_READONLY",
                "Review forks cannot receive WorkerBee deployment authority.",
            )
        if (
            selected == WORKERBEE_PROFILE_LOCAL_DEPLOY
            and posture["profile_ceiling"] == WORKERBEE_PROFILE_OBSERVE
        ):
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_PROFILE_CEILING",
                "local-deploy exceeds the administrator access ceiling.",
            )
        updated = self.store.merge_agent_metadata(
            agent_id,
            {
                "workerbee_mcp_profile": selected,
                "workerbee_mcp_profile_updated_by": str(updated_by or "local"),
            },
            event_type="workerbee_access_profile_updated",
            event_payload={
                "agent_id": agent_id,
                "previous_profile": current["requested_profile"],
                "profile": selected,
                "updated_by": str(updated_by or "local"),
            },
        )
        if updated is None:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_AGENT_NOT_FOUND",
                "Agent is no longer registered.",
            )
        result = self.get_profile(agent_id)
        result["changed"] = current["requested_profile"] != selected
        return result
