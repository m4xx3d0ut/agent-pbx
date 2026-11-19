"""TUI actions and readback for the daemon-owned WorkerBee selector."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from textual.widgets import TextArea

from ...client import auth_headers
from ...workerbee_mcp import WorkerBeeMcpPreflight
from ..screens import WorkerBeeSwitchConfirmScreen
from ._base import TUIController


class WorkerBeeLaunchPreflightError(RuntimeError):
    """WorkerBee rejected launch preparation before Codex was changed."""

    def __init__(
        self,
        *,
        agent_id: str,
        phase: str,
        code: str,
        message: str,
        remediation: str = "",
        status_code: int | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.phase = phase
        self.code = code
        self.message = message
        self.remediation = remediation
        self.status_code = status_code
        detail = f"{code}: {message}" if code else message
        if remediation:
            detail = f"{detail} Remediation: {remediation}"
        super().__init__(
            f"WorkerBee {phase} preflight failed for {agent_id}: {detail} "
            "No Codex process was started or replaced."
        )

    @classmethod
    def from_response(
        cls,
        response: httpx.Response,
        *,
        agent_id: str,
        phase: str,
    ) -> WorkerBeeLaunchPreflightError:
        status_code = int(response.status_code)
        code = f"WORKERBEE_HTTP_{status_code}"
        message = f"WorkerBee returned HTTP {status_code}."
        remediation = ""
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            code = str(detail.get("code") or code).strip() or code
            message = str(detail.get("message") or message).strip() or message
            remediation = str(detail.get("remediation") or "").strip()
        elif isinstance(detail, str) and detail.strip():
            message = detail.strip()
        return cls(
            agent_id=agent_id,
            phase=phase,
            code=code,
            message=message,
            remediation=remediation,
            status_code=status_code,
        )


class WorkerBeeSelectorController(TUIController[Any]):
    async def _read_selector(self) -> dict[str, Any]:
        response = await self.api_client().get(
            "/v1/workerbee/switch", headers=auth_headers(self.token), timeout=15,
        )
        response.raise_for_status()
        return response.json()

    async def _governed_workerbee_preflight(self) -> WorkerBeeMcpPreflight | None:
        try:
            view = await self._read_selector()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            view = {}
        except Exception:
            view = {}
        if view.get("admission", {}).get("admission") == "legacy":
            return None
        selected = view.get("selected") or {}
        gate = view.get("admission") or {}
        key = tuple(selected.get(item) for item in ("backend", "generation", "instance_id", "fingerprint"))
        if key != getattr(self, "_workerbee_selector_identity", None) or gate.get("admission") != "open":
            self.workerbee_status_by_agent.clear()
            self.workerbee_project_visibility = None
        self._workerbee_selector_identity = key
        result = WorkerBeeMcpPreflight(
            code="WORKERBEE_DAEMON_OWNED" if view else "WORKERBEE_SELECTOR_UNAVAILABLE",
            ready=False,
            message="The daemon owns backend probes." if view else "The daemon selector could not be read.",
            remediation="Review daemon selector posture and the selected Agent's gateway evidence.",
        )
        self.workerbee_mcp_preflight = result
        self.workerbee_backend_provenance = None
        detail = self.query_one_or_none("#workerbee-detail", TextArea)
        if detail is not None:
            detail.text = "\n".join((
                f"Selected: {selected.get('backend', '-')} g{selected.get('generation', '-')} "
                f"instance {selected.get('instance_id', '-')}",
                f"Observation: {view.get('observation_state', 'unavailable')}",
                f"Admission: {gate.get('admission', 'unavailable')} {gate.get('reason', '')}".rstrip(),
                result.message,
            ))
        return result

    async def prepare_workerbee_gateway_launch(
        self,
        *,
        agent_id: str,
        project: str,
        codex_session_id: str,
        profile: str,
        source_root: str | None = None,
    ) -> dict[str, Any] | None:
        """Prepare one exact backend workspace and a one-time launch lease."""

        try:
            runtime_response = await self.api_client().get(
                "/v2/workerbee/runtime",
                headers=auth_headers(self.token),
                timeout=30,
            )
            if runtime_response.status_code == 404:
                return None
            if runtime_response.status_code >= 400:
                raise WorkerBeeLaunchPreflightError.from_response(
                    runtime_response,
                    agent_id=agent_id,
                    phase="runtime discovery",
                )
            runtime_response.raise_for_status()
            runtime_posture = runtime_response.json()
            if not isinstance(runtime_posture, dict):
                return None
            gate = runtime_posture.get("admission") or {}
            if gate.get("admission") not in (None, "open", "legacy"):
                raise WorkerBeeLaunchPreflightError(
                    agent_id=agent_id, phase="runtime discovery",
                    code="WORKERBEE_CUTOVER_CLOSED",
                    message="The WorkerBee selector is held.",
                    remediation="Review the daemon selector gate before preparing a workspace.",
                )
            if not (isinstance(runtime_posture.get("selected"), dict)
                    or runtime_posture.get("bootstrap_available") is True):
                return None
            workspace_payload: dict[str, object] = {
                "project": project,
                "source_root": source_root,
            }
            workspace_response = await self.api_client().post(
                f"/v2/agents/{quote(agent_id, safe='')}/workerbee/workspaces",
                json=workspace_payload,
                headers=auth_headers(self.token),
                timeout=330,
            )
            if workspace_response.status_code == 404:
                return None
            if workspace_response.status_code >= 400:
                raise WorkerBeeLaunchPreflightError.from_response(
                    workspace_response,
                    agent_id=agent_id,
                    phase="workspace staging",
                )
            workspace_response.raise_for_status()
            workspace = workspace_response.json()
            lease_response = await self.api_client().post(
                f"/v2/agents/{quote(agent_id, safe='')}/workerbee/leases",
                json={
                    "codex_session_id": codex_session_id,
                    "profile": profile,
                    "workspace_binding_id": workspace.get("binding_id"),
                },
                headers=auth_headers(self.token),
                timeout=30,
            )
            if lease_response.status_code >= 400:
                raise WorkerBeeLaunchPreflightError.from_response(
                    lease_response,
                    agent_id=agent_id,
                    phase="gateway lease",
                )
            lease_response.raise_for_status()
            binding = lease_response.json()
        except WorkerBeeLaunchPreflightError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"WorkerBee launch preflight failed for {agent_id}: {exc}. "
                "No Codex process was started or replaced."
            ) from exc
        if not isinstance(binding, dict) or not isinstance(workspace, dict):
            raise RuntimeError("WorkerBee runtime binding returned invalid data")
        lease = binding.get("lease") if isinstance(binding.get("lease"), dict) else {}
        if not (
            str(workspace.get("binding_id") or "").strip()
            and str(binding.get("gateway_url") or "").strip()
            and str(binding.get("token") or "").strip()
            and str(lease.get("lease_id") or "").strip()
        ):
            raise RuntimeError("WorkerBee runtime binding response is incomplete")
        binding["workspace_binding"] = workspace
        return binding

    async def preview_workerbee_switch(self, target: str) -> None:
        try:
            view = await self._read_selector()
            if target not in view.get("targets", []):
                self.notify("The daemon has not offered this WorkerBee cutover target.", severity="warning")
                return
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
            status = await self._read_selector()
            reviewed = status.get("last_transaction") or {}
            if (
                status.get("admission", {}).get("admission") != "open"
                or reviewed.get("phase") != "previewed"
                or any(reviewed.get(key) != transaction.get(key) for key in (
                    "transaction_id", "source", "target_runtime", "target_qualification",
                ))
                or status.get("selected") != transaction.get("source")
                or not transaction.get("preview_token")
            ):
                self.notify("WorkerBee preview is stale. Review a fresh preview.", severity="warning")
                return
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
        try:
            status = await self._read_selector()
        except Exception as exc:
            self.notify(f"WorkerBee rollback status failed: {exc}", severity="error")
            return
        transaction = status.get("rollback_transaction")
        if not isinstance(transaction, dict) or transaction.get("phase") != "verified":
            self.notify("No current verified WorkerBee switch is available to roll back.", severity="warning")
            return
        current = transaction["after"]
        restore = {**transaction["source"], "generation": current["generation"] + 1}
        display = {**transaction, "before": current, "target": restore["backend"], "target_runtime": restore}
        self.push_screen(WorkerBeeSwitchConfirmScreen(transaction=display, action="rollback"))

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
            status = await self._read_selector()
            reviewed = status.get("rollback_transaction") or {}
            if (
                reviewed.get("transaction_id") != transaction_id
                or reviewed.get("after") != transaction.get("before")
                or reviewed.get("source") != transaction.get("source")
                or status.get("selected") != reviewed.get("after")
            ):
                self.notify("WorkerBee rollback evidence changed. Review the current transaction.", severity="warning")
                return
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
