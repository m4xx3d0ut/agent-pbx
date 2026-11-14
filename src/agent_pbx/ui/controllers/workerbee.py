from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from textual.widgets import Button, TextArea

from ...client import auth_headers
from ...workerbee_mcp import (
    WorkerBeeMcpPreflight,
    WorkerBeeProjectVisibilityDiagnostic,
    inspect_workerbee_mcp,
    inspect_workerbee_project_visibility,
    inspect_workerbee_switcher,
)
from ..screens import (
    WorkerBeeAccessScreen,
    WorkerBeeControlledRunLeaseScreen,
    WorkerBeeControlledRunScreen,
    WorkerBeeSnapshotScreen,
    WorkerBeeSwitchConfirmScreen,
    WorkerBeeWorkspaceScreen,
)
from ..workerbee_config import (
    CALLER_WORKERBEE_PROFILE_METADATA_KEY,
    REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS,
)
from ._base import TUIController


class WorkerBeeController(TUIController[Any]):
    async def fetch_workerbee_controlled_runs(
        self,
        operator_agent_id: str,
        *,
        statuses: tuple[str, ...] = ("pending",),
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("root_operator_agent_id", operator_agent_id),
            ("limit", str(limit)),
        ]
        params.extend(("status", status) for status in statuses)
        response = await self.api_client().get(
            "/v2/workerbee/controlled-runs",
            params=params,
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        requests = payload.get("requests") if isinstance(payload, dict) else None
        return [dict(item) for item in requests or [] if isinstance(item, dict)]

    async def open_workerbee_controlled_run_center(
        self,
        operator_agent_id: str,
        *,
        statuses: tuple[str, ...] = ("pending",),
    ) -> bool:
        if self.workerbee_controlled_run_controls_hidden:
            self.notify(
                "WorkerBee controlled-run controls are hidden by policy.",
                severity="warning",
            )
            return False
        active = self.active_workerbee_controlled_run_screen()
        if active is not None:
            self.notify(
                f"WorkerBee run approvals for {active.operator_agent_id} are already open."
            )
            return False
        if self.workerbee_run_opening_operator_id is not None:
            self.notify("WorkerBee run approvals are already opening.")
            return False
        self.workerbee_run_opening_operator_id = operator_agent_id
        try:
            try:
                requests = await self.fetch_workerbee_controlled_runs(
                    operator_agent_id,
                    statuses=statuses,
                )
            except Exception as exc:
                self.notify(f"Unable to fetch WorkerBee runs: {exc}", severity="error")
                return False
            if not requests:
                self.notify(
                    f"No pending WorkerBee controlled runs for {operator_agent_id}."
                )
                await self.refresh_agents()
                return False
            self.reviewed_workerbee_run_signatures[operator_agent_id] = (
                self.workerbee_controlled_run_signature(operator_agent_id)
            )
            await self.push_screen(
                WorkerBeeControlledRunScreen(
                    operator_agent_id=operator_agent_id,
                    requests=requests,
                    statuses=statuses,
                )
            )
            return True
        finally:
            self.workerbee_run_opening_operator_id = None

    async def refresh_workerbee_controlled_run_screen(
        self,
        screen: WorkerBeeControlledRunScreen,
    ) -> None:
        try:
            requests = await self.fetch_workerbee_controlled_runs(
                screen.operator_agent_id,
                statuses=screen.statuses,
            )
        except Exception as exc:
            self.notify(f"Unable to refresh WorkerBee runs: {exc}", severity="error")
            return
        screen.replace_requests(requests)
        await self.refresh_agents()
        if not requests:
            screen.dismiss()

    async def decide_workerbee_controlled_run(
        self,
        screen: WorkerBeeControlledRunScreen,
        request: dict[str, Any],
        decision: str,
    ) -> None:
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            self.notify("Controlled run has no durable request ID.", severity="error")
            return
        try:
            if decision == "cancel":
                response = await self.api_client().post(
                    f"/v2/workerbee/controlled-runs/{request_id}/cancel",
                    json={
                        "canceled_by": "local-tui-user",
                        "reason": "Canceled from the Agent PBX controlled-run review.",
                    },
                    headers=auth_headers(self.token),
                )
                response.raise_for_status()
                await self.refresh_events()
                await self.refresh_workerbee_controlled_run_screen(screen)
                self.notify(f"WorkerBee controlled run {request_id} canceled.")
                return
            lease_id: str | None = None
            if decision == "lease":
                lease_response = await self.api_client().post(
                    f"/v2/workerbee/controlled-runs/{request_id}/leases",
                    json={"requested_by": "agent-pbx-tui"},
                    headers=auth_headers(self.token),
                )
                lease_response.raise_for_status()
                lease = lease_response.json()
                lease_id = str(lease.get("lease_id") or "")
                if not lease_id:
                    raise RuntimeError("controlled-run lease response omitted its ID")
                lease_decision = await self.api_client().post(
                    f"/v2/workerbee/controlled-run-leases/{lease_id}/decision",
                    json={"approve": True, "decided_by": "local-tui-user"},
                    headers=auth_headers(self.token),
                )
                lease_decision.raise_for_status()
            approve = decision in {"approve", "lease"}
            response = await self.api_client().post(
                f"/v2/workerbee/controlled-runs/{request_id}/decision",
                json={
                    "approve": approve,
                    "decided_by": "local-tui-user",
                    "lease_id": lease_id,
                },
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Unable to decide WorkerBee run: {exc}", severity="error")
            return
        await self.refresh_events()
        await self.refresh_workerbee_controlled_run_screen(screen)
        verb = (
            "approved"
            if decision == "approve"
            else (
                "trusted for the bounded campaign"
                if decision == "lease"
                else "rejected"
            )
        )
        self.notify(f"WorkerBee controlled run {request_id} {verb}.")

    async def show_workerbee_controlled_run_status(
        self, operator_agent_id: str
    ) -> None:
        try:
            requests = await self.fetch_workerbee_controlled_runs(
                operator_agent_id,
                statuses=(
                    "pending",
                    "approved",
                    "claimed",
                    "provisioning",
                    "canary",
                    "running",
                    "collecting",
                    "cleaning",
                    "succeeded",
                    "failed",
                    "recovery_required",
                    "canceled",
                    "rejected",
                    "expired",
                ),
                limit=20,
            )
        except Exception as exc:
            self.notify(
                f"Unable to fetch WorkerBee run status: {exc}", severity="error"
            )
            return
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            lines = [f"WorkerBee controlled runs · {operator_agent_id}", ""]
            lines.extend(
                f"{item.get('status', '-'):>12}  {str(item.get('request_id') or '')[:12]}  "
                f"{str(item.get('baseline_commit') or '')[:8]}→"
                f"{str(item.get('candidate_commit') or '')[:8]}  "
                f"{item.get('profile_id') or '-'}"
                for item in requests
            )
            if not requests:
                lines.append("No controlled runs recorded.")
            detail.text = "\n".join(lines)
        self.notify(f"Loaded {len(requests)} WorkerBee controlled run(s).")

    async def fetch_workerbee_controlled_run_leases(
        self,
        operator_agent_id: str,
        *,
        status: str = "approved",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        response = await self.api_client().get(
            "/v2/workerbee/controlled-run-leases",
            params={
                "root_operator_agent_id": operator_agent_id,
                "status": status,
                "limit": str(limit),
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        leases = payload.get("leases") if isinstance(payload, dict) else None
        return [dict(item) for item in leases or [] if isinstance(item, dict)]

    async def open_workerbee_controlled_run_lease_center(
        self,
        operator_agent_id: str,
    ) -> bool:
        if self.workerbee_controlled_run_controls_hidden:
            self.notify(
                "WorkerBee controlled-run controls are hidden by policy.",
                severity="warning",
            )
            return False
        active = self.active_workerbee_controlled_run_lease_screen()
        if active is not None:
            self.notify(
                f"WorkerBee campaign trust for {active.operator_agent_id} is already open."
            )
            return False
        try:
            leases = await self.fetch_workerbee_controlled_run_leases(operator_agent_id)
        except Exception as exc:
            self.notify(
                f"Unable to fetch WorkerBee campaign trust: {exc}", severity="error"
            )
            return False
        if not leases:
            self.notify(f"No active WorkerBee campaign trust for {operator_agent_id}.")
            return False
        await self.push_screen(
            WorkerBeeControlledRunLeaseScreen(
                operator_agent_id=operator_agent_id,
                leases=leases,
            )
        )
        return True

    async def refresh_workerbee_controlled_run_lease_screen(
        self,
        screen: WorkerBeeControlledRunLeaseScreen,
    ) -> None:
        try:
            leases = await self.fetch_workerbee_controlled_run_leases(
                screen.operator_agent_id
            )
        except Exception as exc:
            self.notify(
                f"Unable to refresh WorkerBee campaign trust: {exc}", severity="error"
            )
            return
        screen.replace_leases(leases)
        if not leases:
            await screen.dismiss()

    async def revoke_workerbee_controlled_run_lease(
        self,
        screen: WorkerBeeControlledRunLeaseScreen,
        lease: dict[str, Any],
    ) -> None:
        lease_id = str(lease.get("lease_id") or "").strip()
        if not lease_id:
            self.notify("Campaign trust has no durable lease ID.", severity="error")
            return
        try:
            response = await self.api_client().post(
                f"/v2/workerbee/controlled-run-leases/{lease_id}/revoke",
                json={"revoked_by": "local-tui-user"},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(
                f"Unable to revoke WorkerBee campaign trust: {exc}", severity="error"
            )
            return
        await self.refresh_events()
        await self.refresh_workerbee_controlled_run_lease_screen(screen)
        self.notify(f"Revoked WorkerBee campaign trust {lease_id}.")

    async def load_workerbee_status(
        self,
        agent_id: str,
        *,
        render_detail: bool = True,
        selection_generation: int | None = None,
    ) -> None:
        detail = self.query_one_or_none("#workerbee-detail", TextArea)
        if render_detail and detail is not None:
            detail.text = f"Loading WorkerBee status for {agent_id}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/workerbee",
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
            status = response.json()
        except Exception as exc:
            if render_detail and detail is not None:
                detail.text = f"Unable to load WorkerBee status for {agent_id}: {exc}"
            return
        if selection_generation is not None and not self.agent_selection_is_current(
            agent_id,
            selection_generation,
        ):
            return
        self.workerbee_status_by_agent[agent_id] = status
        workspace_policy = (
            status.get("workspace_policy")
            if isinstance(status.get("workspace_policy"), dict)
            else {}
        )
        if workspace_policy.get("control_mode") == "hidden":
            self.workerbee_workspace_controls_hidden = True
        workspace_button = self.query_one_or_none("#workerbee-workspace", Button)
        if workspace_button is not None:
            workspace_button.display = not self.workerbee_workspace_controls_hidden
        access_summary = (
            workspace_policy.get("access")
            if isinstance(workspace_policy.get("access"), dict)
            else {}
        )
        if all(
            str(access_summary.get(key) or "") == "hidden"
            for key in (
                "control_mode",
                "source_mode_control",
                "execution_lane_control",
            )
        ):
            self.workerbee_access_controls_hidden = True
        access_button = self.query_one_or_none("#workerbee-access", Button)
        if access_button is not None:
            access_button.display = not self.workerbee_access_controls_hidden
        snapshot_summary = (
            workspace_policy.get("snapshot")
            if isinstance(workspace_policy.get("snapshot"), dict)
            else {}
        )
        if snapshot_summary.get("control_mode") == "hidden":
            self.workerbee_snapshot_controls_hidden = True
        selected_runtime = (
            status.get("selected_runtime")
            if isinstance(status.get("selected_runtime"), dict)
            else {}
        )
        snapshot_applicable = str(selected_runtime.get("backend") or "oss") != "oss"
        snapshot_button = self.query_one_or_none("#workerbee-snapshot", Button)
        if snapshot_button is not None:
            snapshot_button.display = (
                not self.workerbee_snapshot_controls_hidden and snapshot_applicable
            )
        if render_detail and detail is not None:
            rendered = self.format_workerbee_status(status)
            if self.workerbee_backend_provenance is not None:
                rendered = (
                    f"{rendered}\n\n"
                    f"{self.format_workerbee_backend_provenance(self.workerbee_backend_provenance)}"
                )
            if self.workerbee_mcp_preflight is not None:
                rendered = f"{rendered}\n\n{self.format_workerbee_mcp_preflight(self.workerbee_mcp_preflight)}"
            if self.workerbee_project_visibility is not None:
                rendered = (
                    f"{rendered}\n\n"
                    f"{self.format_workerbee_project_visibility(self.workerbee_project_visibility)}"
                )
            detail.text = rendered
        if self.selected_agent_id == agent_id:
            self.render_codex_runtime_surface()

    async def load_workerbee_workspace_policy(
        self,
        agent_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        encoded = quote(agent_id, safe="")
        policy_response, transitions_response = await asyncio.gather(
            self.api_client().get(
                f"/v2/agents/{encoded}/workerbee/workspace-policy",
                headers=auth_headers(self.token),
                timeout=15,
            ),
            self.api_client().get(
                f"/v2/agents/{encoded}/workerbee/workspace-transitions",
                headers=auth_headers(self.token),
                timeout=15,
            ),
        )
        policy_response.raise_for_status()
        transitions_response.raise_for_status()
        policy = dict(policy_response.json())
        transitions = [
            dict(item) for item in transitions_response.json() if isinstance(item, dict)
        ]
        self.workerbee_workspace_policy_by_agent[agent_id] = policy
        self.workerbee_workspace_transitions_by_agent[agent_id] = transitions
        admin = policy.get("admin") if isinstance(policy.get("admin"), dict) else {}
        if admin.get("control_mode") == "hidden":
            self.workerbee_workspace_controls_hidden = True
        workspace_button = self.query_one_or_none("#workerbee-workspace", Button)
        if workspace_button is not None:
            workspace_button.display = not self.workerbee_workspace_controls_hidden
        return policy, transitions

    async def load_workerbee_access_profile(self, agent_id: str) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v2/agents/{quote(agent_id, safe='')}/workerbee/access-profile",
            headers=auth_headers(self.token),
            timeout=15,
        )
        response.raise_for_status()
        view = dict(response.json())
        if all(
            str(view.get(key) or "") == "hidden"
            for key in (
                "control_mode",
                "source_mode_control",
                "execution_lane_control",
            )
        ):
            self.workerbee_access_controls_hidden = True
        access_button = self.query_one_or_none("#workerbee-access", Button)
        if access_button is not None:
            access_button.display = not self.workerbee_access_controls_hidden
        return view

    async def open_workerbee_access_manager(self, agent_id: str) -> None:
        try:
            view = await self.load_workerbee_access_profile(agent_id)
        except Exception as exc:
            self.notify(f"WorkerBee access policy failed: {exc}", severity="error")
            return
        if all(
            str(view.get(key) or "") == "hidden"
            for key in (
                "control_mode",
                "source_mode_control",
                "execution_lane_control",
            )
        ):
            self.notify(
                "WorkerBee runtime policy controls are hidden by administrator policy.",
                severity="warning",
            )
            return
        self.push_screen(WorkerBeeAccessScreen(agent_id=agent_id, access_view=view))

    async def save_workerbee_access_profile(
        self,
        screen: WorkerBeeAccessScreen,
    ) -> None:
        try:
            response = await self.api_client().put(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/access-profile",
                json=screen.policy_payload(),
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
            result = dict(response.json())
        except Exception as exc:
            self.notify(f"WorkerBee access update failed: {exc}", severity="error")
            return
        cached_agent = self.agents.get(screen.agent_id)
        requested_profile = str(result.get("requested_profile") or "").strip()
        source_mode = str(result.get("source_mode_preference") or "").strip()
        execution_lane = str(result.get("execution_lane") or "").strip()
        if isinstance(cached_agent, dict):
            cached_metadata = self.agent_metadata(cached_agent)
            if requested_profile:
                cached_metadata[CALLER_WORKERBEE_PROFILE_METADATA_KEY] = (
                    requested_profile
                )
            if source_mode:
                cached_metadata["workerbee_source_mode"] = source_mode
            if execution_lane:
                cached_metadata["workerbee_execution_lane"] = execution_lane
            cached_agent["metadata"] = cached_metadata
        screen.dismiss()
        if result.get("changed"):
            self.notify(
                "WorkerBee access saved. Restart/resume the session at a safe turn boundary to apply it.",
                severity="warning",
            )
        else:
            self.notify("WorkerBee access is unchanged.")

    async def open_workerbee_workspace_manager(self, agent_id: str) -> None:
        try:
            policy, transitions = await self.load_workerbee_workspace_policy(agent_id)
        except Exception as exc:
            self.notify(f"WorkerBee workspace policy failed: {exc}", severity="error")
            return
        admin = policy.get("admin") if isinstance(policy.get("admin"), dict) else {}
        if admin.get("control_mode") == "hidden":
            self.notify(
                "WorkerBee workspace controls are hidden by administrator policy.",
                severity="warning",
            )
            return
        self.push_screen(
            WorkerBeeWorkspaceScreen(
                agent_id=agent_id,
                policy_view=policy,
                transitions=transitions,
            )
        )

    async def save_workerbee_workspace_policy(
        self,
        screen: WorkerBeeWorkspaceScreen,
        *,
        global_policy: bool = False,
    ) -> None:
        path = (
            "/v2/workerbee/workspace-policy"
            if global_policy
            else f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-policy"
        )
        try:
            response = await self.api_client().put(
                path,
                json=screen.policy_payload(),
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"WorkerBee policy update failed: {exc}", severity="error")
            return
        self.notify(
            "Global WorkerBee workspace policy saved."
            if global_policy
            else "Agent WorkerBee workspace policy saved."
        )
        screen.dismiss()
        await self.open_workerbee_workspace_manager(screen.agent_id)

    async def inherit_workerbee_workspace_policy(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        try:
            response = await self.api_client().delete(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-policy",
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"WorkerBee policy inheritance failed: {exc}", severity="error")
            return
        self.notify("WorkerBee workspace policy now inherits the global policy.")
        screen.dismiss()
        await self.open_workerbee_workspace_manager(screen.agent_id)

    async def load_workerbee_snapshot_policy(
        self,
        agent_id: str,
    ) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v2/agents/{quote(agent_id, safe='')}/workerbee/snapshot-policy",
            headers=auth_headers(self.token),
            timeout=30,
        )
        response.raise_for_status()
        policy = dict(response.json())
        self.workerbee_snapshot_policy_by_agent[agent_id] = policy
        admin = policy.get("admin") if isinstance(policy.get("admin"), dict) else {}
        if admin.get("control_mode") == "hidden":
            self.workerbee_snapshot_controls_hidden = True
        snapshot_button = self.query_one_or_none("#workerbee-snapshot", Button)
        if snapshot_button is not None:
            snapshot_button.display = (
                not self.workerbee_snapshot_controls_hidden
                and bool(policy.get("applicable", True))
            )
        return policy

    async def open_workerbee_snapshot_manager(self, agent_id: str) -> None:
        try:
            policy = await self.load_workerbee_snapshot_policy(agent_id)
        except Exception as exc:
            self.notify(
                f"WorkerBee appliance content policy failed: {exc}", severity="error"
            )
            return
        admin = policy.get("admin") if isinstance(policy.get("admin"), dict) else {}
        if admin.get("control_mode") == "hidden":
            self.notify(
                "WorkerBee appliance content controls are hidden by administrator policy.",
                severity="warning",
            )
            return
        self.push_screen(WorkerBeeSnapshotScreen(agent_id=agent_id, policy_view=policy))

    async def save_workerbee_snapshot_policy(
        self,
        screen: WorkerBeeSnapshotScreen,
        *,
        global_policy: bool = False,
    ) -> None:
        path = (
            "/v2/workerbee/snapshot-policy"
            if global_policy
            else f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/snapshot-policy"
        )
        try:
            response = await self.api_client().put(
                path,
                json=screen.policy_payload(),
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(
                f"WorkerBee appliance content update failed: {exc}", severity="error"
            )
            return
        self.notify(
            "Global WorkerBee appliance content policy saved."
            if global_policy
            else "Agent WorkerBee appliance content policy saved."
        )
        screen.dismiss()
        await self.open_workerbee_snapshot_manager(screen.agent_id)

    async def inherit_workerbee_snapshot_policy(
        self,
        screen: WorkerBeeSnapshotScreen,
    ) -> None:
        try:
            response = await self.api_client().delete(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/snapshot-policy",
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(
                f"WorkerBee appliance content inheritance failed: {exc}",
                severity="error",
            )
            return
        self.notify("WorkerBee appliance content now inherits its root/global policy.")
        screen.dismiss()
        await self.open_workerbee_snapshot_manager(screen.agent_id)

    async def preview_workerbee_snapshot_policy(
        self,
        screen: WorkerBeeSnapshotScreen,
    ) -> None:
        try:
            response = await self.api_client().get(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/snapshot-policy/preview",
                headers=auth_headers(self.token),
                timeout=60,
            )
            response.raise_for_status()
            payload = dict(response.json())
        except Exception as exc:
            self.notify(
                f"WorkerBee appliance content preview failed: {exc}", severity="error"
            )
            return
        screen.show_preview(payload)

    async def preview_workerbee_workspace_target(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        payload = screen.target_payload()
        if not payload.get("cwd"):
            self.notify(
                "Enter a Git checkout or subdirectory to preview.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-transitions/preview",
                json=payload,
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
            preview = dict(response.json())
        except Exception as exc:
            self.notify(f"WorkerBee workspace preview failed: {exc}", severity="error")
            return
        screen.show_preview(preview)

    async def apply_workerbee_workspace_target(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        current = screen.target_payload()
        if (
            not screen.preview_payload
            or screen.preview_payload.get("request") != current
        ):
            screen.preview_payload = None
            screen.query_one("#workerbee-workspace-bind", Button).disabled = True
            self.notify(
                "Preview the current workspace target before binding.",
                severity="warning",
            )
            return
        try:
            response = await self.api_client().post(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-transitions",
                json=current,
                headers=auth_headers(self.token),
                timeout=240,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            self.notify(f"WorkerBee workspace bind failed: {exc}", severity="error")
            return
        self.workerbee_status_by_agent.pop(screen.agent_id, None)
        self.notify(
            "WorkerBee workspace binding updated."
            if result.get("changed")
            else "WorkerBee workspace binding is already current."
        )
        screen.dismiss()
        await self.load_workerbee_status(screen.agent_id)

    async def approve_workerbee_workspace_transition(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        pending = next(
            (item for item in screen.transitions if item.get("status") == "pending"),
            None,
        )
        if pending is None:
            self.notify(
                "No pending WorkerBee workspace transition.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-transitions/"
                f"{quote(str(pending.get('transition_id') or ''), safe='')}/decision",
                json={"approve": True},
                headers=auth_headers(self.token),
                timeout=240,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(
                f"WorkerBee transition approval failed: {exc}", severity="error"
            )
            return
        self.notify("WorkerBee workspace transition approved and applied.")
        screen.dismiss()
        await self.load_workerbee_status(screen.agent_id)

    async def deny_workerbee_workspace_transition(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        pending = next(
            (item for item in screen.transitions if item.get("status") == "pending"),
            None,
        )
        if pending is None:
            self.notify(
                "No pending WorkerBee workspace transition.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-transitions/"
                f"{quote(str(pending.get('transition_id') or ''), safe='')}/decision",
                json={"approve": False},
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"WorkerBee transition denial failed: {exc}", severity="error")
            return
        self.notify("WorkerBee workspace transition denied.")
        screen.dismiss()

    async def revert_workerbee_workspace_transition(
        self,
        screen: WorkerBeeWorkspaceScreen,
    ) -> None:
        applied = next(
            (item for item in screen.transitions if item.get("status") == "applied"),
            None,
        )
        if applied is None:
            self.notify(
                "No current applied WorkerBee workspace transition.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                f"/v2/agents/{quote(screen.agent_id, safe='')}/workerbee/workspace-transitions/"
                f"{quote(str(applied.get('transition_id') or ''), safe='')}/revert",
                headers=auth_headers(self.token),
                timeout=240,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"WorkerBee workspace revert failed: {exc}", severity="error")
            return
        self.notify("WorkerBee workspace binding reverted.")
        screen.dismiss()
        await self.load_workerbee_status(screen.agent_id)

    async def preview_workerbee_switch(self, target: str) -> None:
        try:
            response = await self.api_client().post(
                "/v1/workerbee/switch/preview",
                json={"target": target},
                headers=auth_headers(self.token),
                timeout=60,
            )
            response.raise_for_status()
            transaction = response.json()
        except Exception as exc:
            self.notify(f"WorkerBee switch preview failed: {exc}", severity="error")
            return
        self.workerbee_switch_transaction = dict(transaction)
        self.push_screen(WorkerBeeSwitchConfirmScreen(transaction=transaction))

    async def apply_workerbee_switch(
        self,
        transaction: dict[str, Any],
        *,
        confirmation: str,
    ) -> None:
        target = str(transaction.get("target") or "")
        if confirmation.strip() != f"switch {target}":
            self.notify(
                "WorkerBee switch confirmation did not match.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                "/v1/workerbee/switch/apply",
                json={
                    "transaction_id": transaction.get("transaction_id"),
                    "preview_token": transaction.get("preview_token"),
                    "confirm_target": target,
                    "confirm_privileged": target == "platform_debug",
                },
                headers=auth_headers(self.token),
                timeout=240,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            self.notify(f"WorkerBee switch failed: {exc}", severity="error")
            return
        self.workerbee_switch_transaction = dict(result)
        self.workerbee_status_by_agent.clear()
        self.workerbee_project_visibility = None
        phase = str(result.get("phase") or "unknown")
        severity = "information" if phase in {"verified", "verified_noop"} else "error"
        self.notify(f"WorkerBee switch result: {phase}.", severity=severity)
        await self.refresh_workerbee_mcp_preflight()
        if self.selected_agent_id:
            await self.load_workerbee_status(self.selected_agent_id)

    async def preview_workerbee_switch_rollback(self) -> None:
        transaction = self.workerbee_switch_transaction
        status: dict[str, Any] = {}
        if not transaction or transaction.get("phase") not in {
            "verified",
            "verified_noop",
        }:
            try:
                response = await self.api_client().get(
                    "/v1/workerbee/switch",
                    headers=auth_headers(self.token),
                    timeout=15,
                )
                response.raise_for_status()
                status = response.json()
            except Exception as exc:
                self.notify(
                    f"WorkerBee rollback status failed: {exc}", severity="error"
                )
                return
            transaction = status.get("last_transaction")
        if not isinstance(transaction, dict) or transaction.get("phase") not in {
            "verified",
            "verified_noop",
        }:
            self.notify(
                "No verified WorkerBee switch is available to roll back.",
                severity="warning",
            )
            return
        display = dict(transaction)
        active = status.get("active") if isinstance(status.get("active"), dict) else {}
        display.setdefault("before", active)
        display["target"] = str(
            display.get("rollback_backend")
            or display.get("before_backend")
            or (display.get("before") or {}).get("backend")
            or "prior backend"
        )
        self.push_screen(
            WorkerBeeSwitchConfirmScreen(transaction=display, action="rollback")
        )

    async def rollback_workerbee_switch(
        self,
        transaction: dict[str, Any],
        *,
        confirmation: str,
    ) -> None:
        transaction_id = str(transaction.get("transaction_id") or "")
        if confirmation.strip() != f"rollback {transaction_id}":
            self.notify(
                "WorkerBee rollback confirmation did not match.", severity="warning"
            )
            return
        try:
            response = await self.api_client().post(
                "/v1/workerbee/switch/rollback",
                json={"transaction_id": transaction_id, "confirm": True},
                headers=auth_headers(self.token),
                timeout=240,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            self.notify(f"WorkerBee rollback failed: {exc}", severity="error")
            return
        self.workerbee_switch_transaction = dict(result)
        self.workerbee_status_by_agent.clear()
        self.workerbee_project_visibility = None
        phase = str(result.get("phase") or "unknown")
        self.notify(
            f"WorkerBee rollback result: {phase}.",
            severity="information" if phase == "rolled_back" else "error",
        )
        await self.refresh_workerbee_mcp_preflight()
        if self.selected_agent_id:
            await self.load_workerbee_status(self.selected_agent_id)

    async def refresh_workerbee_mcp_preflight(self) -> WorkerBeeMcpPreflight:
        endpoint = self.workerbee_mcp_endpoint()
        prior_provenance = self.workerbee_backend_provenance
        provenance = await asyncio.to_thread(
            inspect_workerbee_switcher,
            expected_endpoint=endpoint,
        )
        self.workerbee_backend_provenance = provenance
        if (
            prior_provenance is not None
            and prior_provenance.fingerprint != provenance.fingerprint
        ):
            self.workerbee_status_by_agent.clear()
            self.workerbee_project_visibility = None
            self.notify(
                "WorkerBee backend changed; cached capability and project evidence was cleared.",
                severity="warning",
            )
        result = await inspect_workerbee_mcp(
            endpoint,
            enabled_tools=REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS,
        )
        self.workerbee_mcp_preflight = result
        visibility: WorkerBeeProjectVisibilityDiagnostic | None = None
        agent = self.agents.get(self.selected_agent_id or "")
        metadata = (
            agent.get("metadata")
            if isinstance(agent, dict) and isinstance(agent.get("metadata"), dict)
            else {}
        )
        cwd = str(
            metadata.get("work_root")
            or metadata.get("cwd")
            or metadata.get("source_cwd")
            or ""
        ).strip()
        selected_status = self.workerbee_status_by_agent.get(
            self.selected_agent_id or ""
        )
        selected_binding = (
            selected_status.get("workspace_binding")
            if isinstance(selected_status, dict)
            and isinstance(selected_status.get("workspace_binding"), dict)
            else None
        )
        if result.ready and cwd and selected_binding:
            visibility = WorkerBeeProjectVisibilityDiagnostic(
                code="WORKERBEE_WORKSPACE_BOUND",
                ready=True,
                host_cwd=cwd,
                host_git_root=cwd,
                workerbee_git_root=str(
                    selected_binding.get("runtime_workspace_path") or ""
                )
                or None,
                workerbee_repo_exists=True,
                message="Agent PBX bound this session to a verified backend workspace.",
                remediation="",
            )
        elif result.ready and cwd:
            visibility = await inspect_workerbee_project_visibility(endpoint, cwd)
        self.workerbee_project_visibility = visibility
        detail = self.query_one_or_none("#workerbee-detail", TextArea)
        if detail is not None:
            agent_id = self.selected_agent_id
            status = self.workerbee_status_by_agent.get(agent_id or "")
            base = (
                self.format_workerbee_status(status) if isinstance(status, dict) else ""
            )
            detail.text = "\n\n".join(
                item
                for item in (
                    base,
                    self.format_workerbee_backend_provenance(provenance),
                    self.format_workerbee_mcp_preflight(result),
                    (
                        self.format_workerbee_project_visibility(visibility)
                        if visibility is not None
                        else ""
                    ),
                )
                if item
            )
        self.notify(
            f"WorkerBee MCP preflight: {result.code}.",
            severity="information" if result.ready else "warning",
        )
        return result
