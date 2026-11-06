from __future__ import annotations

import os
import json
from typing import Any, Mapping

from .store import Store
from .workerbee_modes import (
    WORKERBEE_ACCESS_PROFILES,
    WORKERBEE_EXECUTION_CONTROLLED_RUN,
    WORKERBEE_EXECUTION_INTERACTIVE,
    WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE,
    WORKERBEE_PUBLIC_DEFAULT_EXECUTION_LANE,
    WORKERBEE_PUBLIC_DEFAULT_SOURCE_MODE,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_RANK,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    WORKERBEE_SOURCE_MODES,
    WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
    WorkerBeeModeError,
    canonical_workerbee_access_profile,
    canonical_workerbee_execution_lane,
    canonical_workerbee_source_mode,
    optional_tools_for_access_profile,
    capability_families_for_profile,
    tools_for_access_profile,
)


WORKERBEE_ACCESS_CONTROL_MODE_ENV = "AGENT_PBX_WORKERBEE_ACCESS_CONTROL_MODE"
WORKERBEE_ACCESS_PROFILE_CEILING_ENV = "AGENT_PBX_WORKERBEE_ACCESS_PROFILE_CEILING"
WORKERBEE_SOURCE_MODE_CONTROL_ENV = "AGENT_PBX_WORKERBEE_SOURCE_MODE_CONTROL_MODE"
WORKERBEE_SOURCE_MODE_CEILING_ENV = "AGENT_PBX_WORKERBEE_SOURCE_MODE_CEILING_JSON"
WORKERBEE_EXECUTION_LANE_CONTROL_ENV = (
    "AGENT_PBX_WORKERBEE_EXECUTION_LANE_CONTROL_MODE"
)
WORKERBEE_TRUSTED_WORKBENCH_LABEL_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_WORKBENCH_LABEL"
)
WORKERBEE_ACCESS_CONTROL_MODES = ("editable", "read_only", "hidden")
WORKERBEE_ACCESS_PROFILE_CEILINGS = (
    WORKERBEE_PROFILE_OBSERVE,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
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

    def posture(self) -> dict[str, Any]:
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
        source_control = str(
            self.environ.get(WORKERBEE_SOURCE_MODE_CONTROL_ENV) or "editable"
        ).strip().lower()
        lane_control = str(
            self.environ.get(WORKERBEE_EXECUTION_LANE_CONTROL_ENV) or "read_only"
        ).strip().lower()
        if source_control not in WORKERBEE_ACCESS_CONTROL_MODES:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                f"Unsupported WorkerBee source-mode control: {source_control!r}.",
            )
        if lane_control not in WORKERBEE_ACCESS_CONTROL_MODES:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                f"Unsupported WorkerBee execution-lane control: {lane_control!r}.",
            )
        raw_source_ceiling = str(
            self.environ.get(WORKERBEE_SOURCE_MODE_CEILING_ENV) or ""
        ).strip()
        if raw_source_ceiling:
            try:
                parsed = json.loads(raw_source_ceiling)
            except json.JSONDecodeError as exc:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_CONFIG_INVALID",
                    f"{WORKERBEE_SOURCE_MODE_CEILING_ENV} must be valid JSON.",
                ) from exc
            if not isinstance(parsed, list):
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_CONFIG_INVALID",
                    f"{WORKERBEE_SOURCE_MODE_CEILING_ENV} must be a JSON list.",
                )
            try:
                source_ceiling = tuple(
                    dict.fromkeys(canonical_workerbee_source_mode(item) for item in parsed)
                )
            except WorkerBeeModeError as exc:
                raise WorkerBeeAccessPolicyError(exc.code, exc.message) from exc
        else:
            source_ceiling = tuple(
                mode
                for mode in WORKERBEE_SOURCE_MODES
                if mode != WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            )
        if not source_ceiling:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                "WorkerBee source-mode ceiling cannot be empty.",
            )
        label = str(
            self.environ.get(WORKERBEE_TRUSTED_WORKBENCH_LABEL_ENV)
            or "Trusted Workbench"
        ).strip()
        if not label or len(label) > 80:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_CONFIG_INVALID",
                "WorkerBee trusted-workbench label must contain 1-80 characters.",
            )
        return {
            "control_mode": control,
            "profile_ceiling": ceiling,
            "source_mode_control": source_control,
            "source_mode_ceiling": list(source_ceiling),
            "execution_lane_control": lane_control,
            "execution_lane_ceiling": [WORKERBEE_EXECUTION_INTERACTIVE],
            "trusted_workbench_label": label,
        }

    def get_profile(
        self,
        agent_id: str,
        *,
        selected_backend: str | None = None,
    ) -> dict[str, Any]:
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
        else:
            try:
                effective = canonical_workerbee_access_profile(
                    requested or WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE
                )
            except WorkerBeeModeError as exc:
                raise WorkerBeeAccessPolicyError(exc.code, exc.message) from exc
        posture = self.posture()
        constrained = False
        if WORKERBEE_PROFILE_RANK[effective] > WORKERBEE_PROFILE_RANK[
            posture["profile_ceiling"]
        ]:
            effective = str(posture["profile_ceiling"])
            constrained = True
        source_mode = str(
            metadata.get("workerbee_source_mode")
            or WORKERBEE_PUBLIC_DEFAULT_SOURCE_MODE
        ).strip()
        execution_lane = str(
            metadata.get("workerbee_execution_lane")
            or WORKERBEE_PUBLIC_DEFAULT_EXECUTION_LANE
        ).strip()
        try:
            source_mode = canonical_workerbee_source_mode(source_mode)
            execution_lane = canonical_workerbee_execution_lane(execution_lane)
        except WorkerBeeModeError as exc:
            raise WorkerBeeAccessPolicyError(exc.code, exc.message) from exc
        source_constrained = source_mode not in posture["source_mode_ceiling"]
        required_tools = tools_for_access_profile(effective)
        reviewed_optional_tools = (
            optional_tools_for_access_profile(effective, selected_backend)
            if selected_backend
            else ()
        )
        return {
            "api_version": "agent-pbx.workerbee-access-policy/v2",
            "agent_id": agent_id,
            "requested_profile": requested or WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE,
            "effective_profile": effective,
            "access_profile": effective,
            "access_profile_label": (
                posture["trusted_workbench_label"]
                if effective == WORKERBEE_PROFILE_TRUSTED_WORKBENCH
                else effective
            ),
            "requested_capability_families": list(
                capability_families_for_profile(effective)
            ),
            "requested_tools": list(required_tools),
            "selected_backend": str(selected_backend or ""),
            "tool_surface_contract": {
                "api_version": "agent-pbx.workerbee-tool-contract/v1",
                "required_tools": list(required_tools),
                "reviewed_optional_tools": list(reviewed_optional_tools),
                "unknown_backend_tool_policy": "quarantine-deny-and-warn",
                "catalog_observation": "doctor-runtime-probe",
            },
            "source_mode_preference": source_mode,
            "source_mode": source_mode,
            "source_mode_resolved": True,
            "source_mode_constrained": source_constrained,
            "execution_lane": execution_lane,
            "controlled_run_authority": "separate-root-owned-action",
            "review_readonly": review,
            **posture,
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
        try:
            selected = canonical_workerbee_access_profile(profile)
        except WorkerBeeModeError as exc:
            raise WorkerBeeAccessPolicyError(exc.code, exc.message) from exc
        if selected == WORKERBEE_PROFILE_REVIEW:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_PROFILE_INVALID",
                "review-readonly is derived from the fork access mode.",
            )
        current = self.get_profile(agent_id)
        if current["review_readonly"] and selected != WORKERBEE_PROFILE_OBSERVE:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_REVIEW_READONLY",
                "Review forks cannot receive WorkerBee deployment authority.",
            )
        if WORKERBEE_PROFILE_RANK[selected] > WORKERBEE_PROFILE_RANK[
            posture["profile_ceiling"]
        ]:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_PROFILE_CEILING",
                f"{selected} exceeds the administrator access ceiling.",
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

    def set_policy(
        self,
        agent_id: str,
        *,
        access_profile: str | None = None,
        source_mode: str | None = None,
        execution_lane: str | None = None,
        updated_by: str,
    ) -> dict[str, Any]:
        current = self.get_profile(agent_id)
        posture = self.posture()
        if current["review_readonly"] and any(
            value is not None
            for value in (access_profile, source_mode, execution_lane)
        ):
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_REVIEW_READONLY",
                "Review forks cannot change WorkerBee runtime policy axes.",
            )
        changes: dict[str, Any] = {}
        if access_profile is not None:
            try:
                selected_profile = canonical_workerbee_access_profile(access_profile)
            except WorkerBeeModeError as exc:
                raise WorkerBeeAccessPolicyError(
                    exc.code, exc.message, exc.remediation
                ) from exc
            if selected_profile == WORKERBEE_PROFILE_REVIEW:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_PROFILE_INVALID",
                    "review-readonly is derived from the fork access mode.",
                )
            if posture["control_mode"] != "editable":
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_POLICY_LOCKED",
                    "WorkerBee access authority is administrator-managed.",
                )
            if WORKERBEE_PROFILE_RANK[selected_profile] > WORKERBEE_PROFILE_RANK[
                posture["profile_ceiling"]
            ]:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_ACCESS_PROFILE_CEILING",
                    f"{selected_profile} exceeds the administrator access ceiling.",
                )
            changes["workerbee_mcp_profile"] = selected_profile
        if source_mode is not None:
            if posture["source_mode_control"] != "editable":
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_SOURCE_MODE_LOCKED",
                    "WorkerBee source mode is administrator-managed.",
                )
            try:
                selected_source = canonical_workerbee_source_mode(source_mode)
            except WorkerBeeModeError as exc:
                raise WorkerBeeAccessPolicyError(
                    exc.code, exc.message, exc.remediation
                ) from exc
            if selected_source not in posture["source_mode_ceiling"]:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_SOURCE_MODE_CEILING",
                    f"{selected_source} exceeds the administrator source-mode ceiling.",
                )
            changes["workerbee_source_mode"] = selected_source
        if execution_lane is not None:
            try:
                selected_lane = canonical_workerbee_execution_lane(execution_lane)
            except WorkerBeeModeError as exc:
                raise WorkerBeeAccessPolicyError(
                    exc.code, exc.message, exc.remediation
                ) from exc
            if selected_lane == WORKERBEE_EXECUTION_CONTROLLED_RUN:
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_CONTROLLED_RUN_AUTHORITY_SEPARATE",
                    "controlled-run is a separate root-owned immutable action lane.",
                )
            if posture["execution_lane_control"] != "editable":
                raise WorkerBeeAccessPolicyError(
                    "WORKERBEE_EXECUTION_LANE_LOCKED",
                    "WorkerBee execution lane is administrator-managed.",
                )
            changes["workerbee_execution_lane"] = selected_lane
        if not changes:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_POLICY_EMPTY_UPDATE",
                "At least one WorkerBee policy axis must be supplied.",
            )
        changes["workerbee_policy_updated_by"] = str(updated_by or "local")
        updated = self.store.merge_agent_metadata(
            agent_id,
            changes,
            event_type="workerbee_mode_policy_updated",
            event_payload={
                "agent_id": agent_id,
                "updated_by": str(updated_by or "local"),
                "changed_axes": sorted(key for key in changes if key != "workerbee_policy_updated_by"),
            },
        )
        if updated is None:
            raise WorkerBeeAccessPolicyError(
                "WORKERBEE_ACCESS_AGENT_NOT_FOUND",
                "Agent is no longer registered.",
            )
        result = self.get_profile(agent_id)
        result["changed"] = any(
            (
                key == "workerbee_mcp_profile"
                and value != current["requested_profile"]
            )
            or (key == "workerbee_source_mode" and value != current["source_mode"])
            or (
                key == "workerbee_execution_lane"
                and value != current["execution_lane"]
            )
            for key, value in changes.items()
            if key != "workerbee_policy_updated_by"
        )
        return result
