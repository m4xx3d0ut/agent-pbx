from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import math
import subprocess
import time
from typing import Any

from textual.widgets import TextArea

from ...client import auth_headers
from ...operator_delegation import delegation_lease_ineligibility_reason
from ..screens import (
    LinkedGitCommitPathsScreen,
    LinkedGitRecoveryConfirmScreen,
    OperatorHandoffApprovalScreen,
    OperatorHandoffCompletionScreen,
    OperatorReadyActionScreen,
    operator_ready_action_control,
)
from ._base import TUIController


HANDOFF_PREFLIGHT_STALE_SECONDS = 300.0
HANDOFF_ACK_WARN_SECONDS = 60.0


def float_value(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def int_value(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class OperatorHandoffController(TUIController[Any]):
    async def fetch_operator_knowledge_links(
        self,
        logical_operator_id: str,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "operator_agent_id": logical_operator_id,
            "limit": limit,
        }
        if status:
            params["status"] = status
        response = await self.api_client().get(
            "/v1/operator/knowledge-links",
            params=params,
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        links = payload.get("knowledge_links")
        if not isinstance(links, list):
            return []
        return [item for item in links if isinstance(item, dict)]

    async def fetch_operator_knowledge_context(
        self,
        logical_operator_id: str,
        link_id: str,
    ) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v1/operator/knowledge-links/{link_id}/context",
            params={"operator_agent_id": logical_operator_id},
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("knowledge context response was not an object")

    async def approve_operator_knowledge_turn(
        self,
        logical_operator_id: str,
        link_id: str,
        turn_id: str,
        *,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/knowledge-links/{link_id}/turns/{turn_id}/approve",
            json={
                "operator_agent_id": logical_operator_id,
                "delivery": delivery,
                "metadata": {"approved_by": "agent-pbx-tui"},
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("knowledge approval response was not an object")

    async def fetch_operator_handoffs(
        self,
        logical_operator_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async def fetch(params: dict[str, Any]) -> list[dict[str, Any]]:
            response = await self.api_client().get(
                "/v1/operator/handoffs",
                params=params,
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            handoffs = payload.get("handoffs")
            if not isinstance(handoffs, list):
                return []
            return [item for item in handoffs if isinstance(item, dict)]

        base: dict[str, Any] = {"limit": limit}
        if status:
            base["status"] = status
        source_params = {**base, "operator_agent_id": logical_operator_id}
        target_params = {**base, "target_operator_agent_id": logical_operator_id}
        combined: dict[str, dict[str, Any]] = {}
        for item in await fetch(source_params):
            handoff_id = str(item.get("handoff_id") or "").strip()
            if handoff_id:
                combined[handoff_id] = item
        for item in await fetch(target_params):
            handoff_id = str(item.get("handoff_id") or "").strip()
            if handoff_id:
                combined.setdefault(handoff_id, item)
        return sorted(
            combined.values(),
            key=lambda item: float_value(item.get("updated_at")) or 0.0,
            reverse=True,
        )

    async def fetch_operator_completion_deliveries(
        self,
        recipient_agent_id: str,
        *,
        unacknowledged_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        response = await self.api_client().get(
            "/v1/operator/handoff-completions",
            params={
                "recipient_agent_id": recipient_agent_id,
                "unacknowledged_only": unacknowledged_only,
                "limit": limit,
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("completion_deliveries") if isinstance(payload, dict) else []
        return [item for item in raw if isinstance(item, dict)]

    async def acknowledge_operator_completion_delivery(
        self,
        recipient_agent_id: str,
        completion_delivery_id: str,
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoff-completions/{completion_delivery_id}/ack",
            json={"operator_agent_id": recipient_agent_id},
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def retry_operator_completion_delivery(
        self,
        operator_agent_id: str,
        completion_delivery_id: str,
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoff-completions/{completion_delivery_id}/retry",
            json={"operator_agent_id": operator_agent_id},
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def fetch_operator_handoff_actions(
        self,
        *,
        target_agent_id: str | None = None,
        include_complete: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "limit": 100,
            "include_complete": include_complete,
        }
        if target_agent_id:
            params["target_agent_id"] = target_agent_id
        response = await self.api_client().get(
            "/v1/operator/handoff-actions",
            params=params,
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("actions") if isinstance(payload, dict) else []
        return [item for item in raw if isinstance(item, dict)]

    async def record_operator_handoff_action_receipt(
        self,
        *,
        handoff_id: str,
        operator_agent_id: str,
        step: str,
        outcome: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoffs/{handoff_id}/action-receipts",
            json={
                "operator_agent_id": operator_agent_id,
                "step": step,
                "outcome": outcome,
                "actor": "agent-pbx-tui",
                "evidence": evidence or {},
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def record_ready_action_for_target(
        self,
        target_agent_id: str,
        *,
        step: str,
        outcome: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Link a guarded TUI result to one unambiguous approved handoff."""

        actions = await self.fetch_operator_handoff_actions(
            target_agent_id=target_agent_id
        )
        if not actions:
            return None
        if len(actions) != 1:
            raise RuntimeError(
                f"{len(actions)} ready handoff actions target {target_agent_id}; "
                "select an exact handoff before recording execution"
            )
        action = actions[0]
        return await self.record_operator_handoff_action_receipt(
            handoff_id=str(action.get("handoff_id") or ""),
            operator_agent_id=str(action.get("target_operator_agent_id") or ""),
            step=step,
            outcome=outcome,
            evidence=evidence,
        )

    def ready_action_signal_for_target(self, target_agent_id: str) -> bool:
        target = self.agents.get(target_agent_id) or {}
        metadata = (
            target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
        )
        logical_operator_id = str(
            metadata.get("logical_operator_agent_id")
            or metadata.get("operator_agent_id")
            or metadata.get("logical_operator_id")
            or ""
        ).strip()
        root = self.agents.get(logical_operator_id) or {}
        return int(root.get("ready_handoff_action_count") or 0) > 0

    async def maybe_record_ready_action_for_target(
        self,
        target_agent_id: str,
        *,
        step: str,
        outcome: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        if not self.ready_action_signal_for_target(target_agent_id):
            return
        try:
            await self.record_ready_action_for_target(
                target_agent_id,
                step=step,
                outcome=outcome,
                evidence=evidence,
            )
        except Exception as exc:
            self.notify(
                f"Guarded action completed, but its handoff receipt was not recorded: {exc}",
                severity="error",
            )

    async def validate_operator_ready_action(self, target_agent_id: str) -> None:
        try:
            result = await self.record_ready_action_for_target(
                target_agent_id,
                step="validate",
                outcome="succeeded",
                evidence={"confirmed_by": "agent-pbx-tui"},
            )
        except Exception as exc:
            self.notify(f"Unable to complete ready action: {exc}", severity="error")
            return
        if result is None:
            self.notify(
                f"No approved validation action targets {target_agent_id}.",
                severity="warning",
            )
            return
        self.notify(f"Completed the approved validation action for {target_agent_id}.")
        await self.refresh_agents()
        await self.refresh_events()

    async def open_operator_ready_action(self, logical_operator_id: str) -> None:
        active_screen = self.active_ready_action_screen()
        if active_screen is not None:
            self.notify("An Operator ready-action review is already open.")
            return
        if self.handoff_action_opening_operator_id is not None:
            self.notify("Operator ready actions are already opening.")
            return
        self.handoff_action_opening_operator_id = logical_operator_id
        try:
            actions = await self.fetch_operator_handoff_actions()
            matching = [
                action
                for action in actions
                if str(action.get("target_operator_agent_id") or "")
                == logical_operator_id
            ]
            oldest = min(
                (
                    float_value(action.get("updated_at")) or math.inf
                    for action in matching
                ),
                default=None,
            )
            self.reviewed_handoff_action_signatures[logical_operator_id] = (
                len(matching),
                oldest,
            )
            if not matching:
                self.notify(f"No ready actions for {logical_operator_id}.")
                return
            current = max(
                matching,
                key=lambda action: (
                    float_value(action.get("updated_at")) or 0.0,
                    str(action.get("handoff_id") or ""),
                ),
            )
            self.push_screen(OperatorReadyActionScreen(current))
        except Exception as exc:
            self.notify(
                f"Unable to open Operator ready actions: {exc}", severity="error"
            )
        finally:
            self.handoff_action_opening_operator_id = None

    async def focus_operator_ready_action(self, action: dict[str, Any]) -> None:
        target_agent_id = str(action.get("target_agent_id") or "").strip()
        if not target_agent_id or target_agent_id not in self.agents:
            self.notify("Ready-action target is no longer visible.", severity="warning")
            return
        await self.open_agent_latest(target_agent_id)
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None and not self.is_tmux_direct_enabled(target_agent_id):
            detail.text = self.format_operator_ready_action(action)
        self.notify(
            f"Focused {target_agent_id}; run the guarded control: "
            f"{operator_ready_action_control(action)}. The action alert remains "
            "until that control succeeds."
        )

    async def advance_operator_ready_action(self, action: dict[str, Any]) -> None:
        """Run only the immutable action's currently displayed guarded step."""

        target_agent_id = str(action.get("target_agent_id") or "").strip()
        handoff_id = str(action.get("handoff_id") or "").strip()
        scope_sha256 = str(action.get("scope_sha256") or "").strip()
        expected_state = str(action.get("state") or "").strip()
        if not target_agent_id or not handoff_id or not scope_sha256:
            self.notify(
                "Ready action is incomplete; refresh and retry.", severity="error"
            )
            return
        try:
            current = await self.operator_broker_action(
                target_agent_id=target_agent_id,
                handoff_id=handoff_id,
            )
        except Exception as exc:
            self.notify(f"Unable to refresh ready action: {exc}", severity="error")
            return
        if (
            str(current.get("scope_sha256") or "") != scope_sha256
            or str(current.get("state") or "") != expected_state
        ):
            self.notify(
                "Ready action changed after review; refresh F8 before continuing.",
                severity="warning",
            )
            return
        next_action = str(current.get("next_action") or "")
        await self.open_agent_latest(target_agent_id)
        self.move_operator_cursor(target_agent_id, focus=True)
        if next_action == "stop":
            await self.stop_selected_operator(agent_id=target_agent_id)
        elif next_action == "broker_review":
            if self.operator_broker_posture.root_operator_enabled:
                root_operator_id = str(
                    current.get("target_operator_agent_id")
                    or current.get("logical_operator_agent_id")
                    or ""
                ).strip()
                if not root_operator_id:
                    self.notify(
                        "Broker action has no supervising root Operator.",
                        severity="error",
                    )
                    return
                await self.request_operator_broker_checkpoint(
                    root_operator_agent_id=root_operator_id,
                    target_fork_agent_id=target_agent_id,
                    handoff_id=handoff_id,
                )
            else:
                context = self.linked_git_broker_tui_context(target_agent_id)
                if context is None:
                    return
                pending, safe = self.pending_linked_git_broker_transaction(
                    context,
                    require_pending=False,
                )
                if not safe:
                    return
                if pending is not None:
                    self.notify(
                        "A pending exact broker transaction must be recovered "
                        "before a new approval can be prepared.",
                        severity="warning",
                    )
                    self.push_screen(
                        LinkedGitRecoveryConfirmScreen(
                            context=context,
                            pending=pending,
                        )
                    )
                    return
                scope = current.get("execution_scope")
                scope = scope if isinstance(scope, Mapping) else {}
                suggested_paths = tuple(
                    str(path)
                    for path in scope.get("exact_paths") or ()
                    if isinstance(path, str) and path.strip()
                )
                scoped_summary = await asyncio.to_thread(
                    self.linked_git_scoped_change_summary,
                    context.work_root,
                    suggested_paths,
                )
                self.push_screen(
                    LinkedGitCommitPathsScreen(
                        context=context,
                        suggested_paths=suggested_paths,
                        scoped_change_summary=scoped_summary,
                    )
                )
        elif next_action == "resume":
            await self.resume_selected_operator(agent_id=target_agent_id)
        elif next_action == "validate":
            await self.validate_operator_ready_action(target_agent_id)
        elif next_action == "broker_commit":
            self.notify(
                "Return to the open exact broker confirmation, or use "
                "/operator git recover after an interrupted transaction.",
                severity="warning",
            )
        else:
            self.notify("The ready action requires manual review.", severity="warning")

    async def request_operator_broker_checkpoint(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
        handoff_id: str,
    ) -> dict[str, Any] | None:
        """Queue one root-supervised checkpoint from an already reviewed action."""

        response = await self.api_client().post(
            "/v1/operator/broker-checkpoints",
            json={
                "root_operator_agent_id": root_operator_agent_id,
                "target_fork_agent_id": target_fork_agent_id,
                "handoff_id": handoff_id,
            },
            headers=auth_headers(self.token),
        )
        if response.status_code in {400, 409}:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            detail_value = (
                error_payload.get("detail") if isinstance(error_payload, dict) else None
            )
            if isinstance(detail_value, dict):
                detail = str(
                    detail_value.get("message")
                    or detail_value.get("code")
                    or detail_value
                )
            else:
                detail = str(detail_value or response.text or response.status_code)
            self.notify(
                f"Root-supervised broker request was refused: {detail}",
                severity="error",
            )
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("broker request response was not an object")
        request = payload.get("request")
        request = request if isinstance(request, dict) else {}
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            raise RuntimeError("broker request response omitted the request id")
        self.notify(
            f"Queued root-supervised checkpoint {request_id[:12]} for "
            f"{target_fork_agent_id}."
        )
        self.schedule_operator_broker_requests()
        return payload

    @staticmethod
    def linked_git_scoped_change_summary(
        work_root: str,
        exact_paths: tuple[str, ...],
    ) -> str:
        """Describe only the reviewed exact paths for the guided broker screen."""

        if not work_root or not exact_paths:
            return "No reviewed exact paths were supplied by this handoff."

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", work_root, *args, "--", *exact_paths],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )

        status = run("status", "--short", "--untracked-files=all")
        unstaged = run("diff", "--stat")
        staged = run("diff", "--cached", "--stat")
        if any(result.returncode != 0 for result in (status, unstaged, staged)):
            return (
                "Scoped Git summary unavailable. No paths were staged; review the "
                "exact handoff subset before continuing."
            )
        status_text = status.stdout.strip() or "(no scoped changes)"
        unstaged_text = unstaged.stdout.strip() or "(none)"
        staged_text = staged.stdout.strip() or "(none)"
        return "\n".join(
            (
                "Reviewed-scope Git status:",
                status_text,
                "",
                "Unstaged diff summary:",
                unstaged_text,
                "",
                "Staged diff summary:",
                staged_text,
            )
        )

    @staticmethod
    def format_operator_ready_action(action: Mapping[str, Any]) -> str:
        scope = action.get("execution_scope")
        scope = scope if isinstance(scope, Mapping) else {}
        paths = (
            scope.get("exact_paths")
            if isinstance(scope.get("exact_paths"), list)
            else []
        )
        return "\n".join(
            [
                "Operator ready action",
                "",
                f"State: {action.get('state') or '-'}",
                f"Next: {action.get('next_action') or '-'}",
                f"Guarded control: {operator_ready_action_control(action)}",
                f"Handoff: {action.get('handoff_id') or '-'}",
                f"Campaign: {action.get('campaign_id') or '-'}",
                f"Target: {action.get('target_agent_id') or '-'}",
                f"Transaction: {action.get('transaction_id') or 'not started'}",
                f"Scope SHA-256: {action.get('scope_sha256') or '-'}",
                "",
                "Exact paths:",
                *(f"- {path}" for path in paths),
                "",
                "Use the guarded control shown above. Agent PBX will not chain lifecycle or Git actions automatically.",
                "The action alert remains active until that control records its receipt.",
            ]
        )

    async def approve_operator_handoff(
        self,
        logical_operator_id: str,
        handoff_id: str,
        *,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoffs/{handoff_id}/approve",
            json={
                "operator_agent_id": logical_operator_id,
                "delivery": delivery,
                "metadata": {"approved_by": "agent-pbx-tui"},
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("handoff approval response was not an object")

    async def cancel_operator_handoff(
        self,
        logical_operator_id: str,
        handoff_id: str,
        *,
        reason: str,
        detail: str = "",
        superseded_by_handoff_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoffs/{handoff_id}/cancel",
            json={
                "operator_agent_id": logical_operator_id,
                "reason": reason,
                "detail": detail or None,
                "superseded_by_handoff_ids": list(superseded_by_handoff_ids),
                "metadata": {"requested_by": "agent-pbx-tui"},
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("handoff cancellation response was not an object")

    async def preflight_operator_handoff(
        self,
        logical_operator_id: str,
        handoff_id: str,
        *,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/operator/handoffs/{handoff_id}/preflight",
            json={
                "operator_agent_id": logical_operator_id,
                "delivery": delivery,
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("handoff preflight response was not an object")

    async def show_selected_operator_handoffs(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            handoffs = await self.fetch_operator_handoffs(
                logical_operator_id,
                limit=50,
            )
        except Exception as exc:
            self.notify(f"Unable to fetch operator handoffs: {exc}", severity="error")
            return
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = self.format_operator_handoffs(
                logical_operator_id,
                handoffs,
            )
        self.activate_latest_tab()

    @staticmethod
    def pending_source_handoffs(
        logical_operator_id: str,
        handoffs: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        pending = [
            handoff
            for handoff in handoffs
            if str(handoff.get("logical_operator_agent_id") or "")
            == logical_operator_id
            and str(handoff.get("status") or "")
            in {"proposed", "approved", "pending_launch"}
            and not bool(handoff.get("expired"))
        ]
        return sorted(
            pending,
            key=lambda item: (
                float_value(item.get("created_at")) or 0.0,
                str(item.get("handoff_id") or ""),
            ),
        )

    async def open_selected_operator_handoff_approval_center(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        await self.open_operator_handoff_approval_center(
            self.logical_operator_id_for_agent(operator_agent)
        )

    async def open_operator_completion_center(
        self,
        recipient_agent_id: str,
    ) -> bool:
        active = self.active_completion_receipt_screen()
        if active is not None:
            self.notify(
                f"Completion receipts for {active.recipient_agent_id} are already open.",
            )
            return False
        if self.completion_receipt_opening_operator_id is not None:
            self.notify("Operator completion receipts are already opening.")
            return False
        self.completion_receipt_opening_operator_id = recipient_agent_id
        try:
            try:
                deliveries = await self.fetch_operator_completion_deliveries(
                    recipient_agent_id,
                    unacknowledged_only=True,
                    limit=100,
                )
            except Exception as exc:
                self.notify(
                    f"Unable to fetch completion receipts: {exc}",
                    severity="error",
                )
                return False
            if not deliveries:
                self.notify(
                    f"No unreviewed completion receipts for {recipient_agent_id}."
                )
                await self.refresh_agents()
                return False
            self.reviewed_completion_receipt_signatures[recipient_agent_id] = (
                self.completion_receipt_signature(recipient_agent_id)
            )
            await self.push_screen(
                OperatorHandoffCompletionScreen(
                    recipient_agent_id=recipient_agent_id,
                    deliveries=deliveries,
                )
            )
            return True
        finally:
            self.completion_receipt_opening_operator_id = None

    async def refresh_operator_completion_screen(
        self,
        screen: OperatorHandoffCompletionScreen,
    ) -> None:
        try:
            deliveries = await self.fetch_operator_completion_deliveries(
                screen.recipient_agent_id,
                unacknowledged_only=True,
                limit=100,
            )
        except Exception as exc:
            self.notify(
                f"Unable to refresh completion receipts: {exc}", severity="error"
            )
            return
        screen.replace_deliveries(deliveries)
        await self.refresh_agents()
        if not deliveries:
            await screen.dismiss()

    async def focus_operator_completion_receipt(
        self,
        screen: OperatorHandoffCompletionScreen,
        delivery: dict[str, Any],
    ) -> None:
        target_agent_id = str(
            delivery.get("target_operator_agent_id")
            or delivery.get("source_operator_agent_id")
            or ""
        ).strip()
        if not target_agent_id or target_agent_id not in self.agents:
            self.notify(
                "The Operator that produced this completion is no longer visible.",
                severity="warning",
            )
            return
        await screen.dismiss()
        if not await self.open_agent_latest(target_agent_id):
            self.notify(
                f"Unable to focus completion source {target_agent_id}.",
                severity="warning",
            )

    async def acknowledge_operator_completion_receipt(
        self,
        screen: OperatorHandoffCompletionScreen,
        delivery: dict[str, Any],
    ) -> None:
        delivery_id = str(delivery.get("completion_delivery_id") or "").strip()
        if not delivery_id:
            self.notify("Completion receipt has no durable ID.", severity="error")
            return
        try:
            await self.acknowledge_operator_completion_delivery(
                screen.recipient_agent_id,
                delivery_id,
            )
        except Exception as exc:
            self.notify(
                f"Unable to acknowledge completion receipt: {exc}", severity="error"
            )
            return
        await self.refresh_events()
        await self.refresh_operator_completion_screen(screen)
        self.notify(f"Reviewed completion receipt {delivery_id}.")

    async def open_operator_handoff_approval_center(
        self,
        logical_operator_id: str,
    ) -> bool:
        active = self.active_handoff_approval_screen()
        if active is not None:
            self.notify(
                f"Operator handoff approvals for {active.operator_agent_id} are already open.",
            )
            return False
        if self.handoff_approval_opening_operator_id is not None:
            self.notify("Operator handoff approvals are already opening.")
            return False
        self.handoff_approval_opening_operator_id = logical_operator_id
        try:
            try:
                handoffs = await self.fetch_operator_handoffs(
                    logical_operator_id,
                    limit=100,
                )
            except Exception as exc:
                self.notify(
                    f"Unable to fetch operator handoffs: {exc}", severity="error"
                )
                return False
            pending = self.pending_source_handoffs(logical_operator_id, handoffs)
            if not pending:
                self.notify(f"No pending operator handoffs for {logical_operator_id}.")
                await self.refresh_agents()
                return False
            self.reviewed_handoff_approval_signatures[logical_operator_id] = (
                self.handoff_approval_signature(logical_operator_id)
            )
            await self.push_screen(
                OperatorHandoffApprovalScreen(
                    operator_agent_id=logical_operator_id,
                    handoffs=pending,
                )
            )
            return True
        finally:
            self.handoff_approval_opening_operator_id = None

    async def refresh_operator_handoff_approval_screen(
        self,
        screen: OperatorHandoffApprovalScreen,
    ) -> None:
        try:
            handoffs = await self.fetch_operator_handoffs(
                screen.operator_agent_id,
                limit=100,
            )
        except Exception as exc:
            self.notify(f"Unable to refresh operator handoffs: {exc}", severity="error")
            return
        pending = self.pending_source_handoffs(screen.operator_agent_id, handoffs)
        screen.replace_handoffs(pending)
        await self.refresh_agents()
        if not pending:
            screen.dismiss()

    async def preflight_operator_handoff_ids(
        self,
        logical_operator_id: str,
        handoff_ids: tuple[str, ...],
    ) -> None:
        results: list[dict[str, Any]] = []
        for handoff_id in handoff_ids:
            try:
                results.append(
                    await self.preflight_operator_handoff(
                        logical_operator_id,
                        handoff_id,
                    )
                )
            except Exception as exc:
                self.notify(
                    f"Unable to preflight handoff {handoff_id}: {exc}",
                    severity="error",
                )
                return
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = "\n\n".join(
                self.format_operator_handoff_preflight(result) for result in results
            )
        failures = [result for result in results if not bool(result.get("ok"))]
        self.notify(
            f"Preflighted {len(results)} exact handoff ID(s); {len(failures)} need attention.",
            severity="warning" if failures else "information",
        )

    async def approve_operator_handoff_ids(
        self,
        logical_operator_id: str,
        handoff_ids: tuple[str, ...],
        *,
        approval_screen: OperatorHandoffApprovalScreen | None = None,
    ) -> None:
        approved: list[str] = []
        for handoff_id in handoff_ids:
            try:
                preflight = await self.preflight_operator_handoff(
                    logical_operator_id,
                    handoff_id,
                )
                if (
                    not bool(preflight.get("ok"))
                    and str(preflight.get("status") or "") != "pending_launch"
                ):
                    reason = str(preflight.get("reason") or "preflight failed")
                    raise RuntimeError(reason)
                await self.approve_operator_handoff(
                    logical_operator_id,
                    handoff_id,
                )
            except Exception as exc:
                self.notify(
                    f"Stopped after {len(approved)} approval(s); {handoff_id}: {exc}",
                    severity="error",
                )
                break
            approved.append(handoff_id)
        await self.refresh_events()
        if approval_screen is not None:
            await self.refresh_operator_handoff_approval_screen(approval_screen)
        else:
            await self.refresh_agents()
        if approved:
            self.notify(f"Approved {len(approved)} exact Operator handoff ID(s).")

    async def cancel_operator_handoff_ids(
        self,
        logical_operator_id: str,
        handoff_ids: tuple[str, ...],
        *,
        reason: str,
        detail: str,
        superseded_by_handoff_ids: tuple[str, ...],
        approval_screen: OperatorHandoffApprovalScreen | None = None,
    ) -> None:
        canceled: list[str] = []
        for handoff_id in handoff_ids:
            try:
                await self.cancel_operator_handoff(
                    logical_operator_id,
                    handoff_id,
                    reason=reason,
                    detail=detail,
                    superseded_by_handoff_ids=superseded_by_handoff_ids,
                )
            except Exception as exc:
                self.notify(
                    f"Canceled {len(canceled)} handoff(s); stopped at {handoff_id}: {exc}",
                    severity="error",
                )
                break
            canceled.append(handoff_id)
        await self.refresh_events()
        await self.refresh_agents()
        if approval_screen is not None:
            await self.refresh_operator_handoff_approval_screen(approval_screen)
        if canceled:
            self.notify(f"Canceled {len(canceled)} exact Operator handoff ID(s).")

    async def create_operator_delegation_lease_from_handoffs(
        self,
        logical_operator_id: str,
        handoff_ids: tuple[str, ...],
        *,
        approval_screen: OperatorHandoffApprovalScreen,
    ) -> None:
        by_id = {
            str(item.get("handoff_id") or ""): item for item in approval_screen.handoffs
        }
        selected = [
            by_id[handoff_id] for handoff_id in handoff_ids if handoff_id in by_id
        ]
        if len(selected) != len(handoff_ids):
            self.notify(
                "One or more selected handoffs changed; refresh and retry.",
                severity="error",
            )
            return
        ineligibility = delegation_lease_ineligibility_reason(selected)
        if ineligibility is not None:
            self.notify(ineligibility, severity="warning")
            return
        if len(selected) > 8:
            self.notify(
                "A local campaign lease can cover at most eight selected handoffs.",
                severity="warning",
            )
            return
        scopes = [
            item.get("execution_scope")
            for item in selected
            if isinstance(item.get("execution_scope"), dict)
        ]
        if len(scopes) != len(selected) or any(not scope for scope in scopes):
            self.notify(
                "Delegation requires a structured execution scope on every selected handoff.",
                severity="warning",
            )
            return
        campaigns = sorted({str(scope.get("campaign_id") or "") for scope in scopes})
        digests = {str(scope.get("workspace_set_digest") or "") for scope in scopes}
        if "" in campaigns or "" in digests or len(digests) != 1:
            self.notify(
                "Selected handoffs need campaign IDs and one shared workspace-set digest.",
                severity="warning",
            )
            return
        payload = {
            "source_operator_agent_id": logical_operator_id,
            "target_operator_agent_ids": sorted(
                {str(item.get("target_operator_agent_id") or "") for item in selected}
            ),
            "campaign_ids": campaigns,
            "workspace_set_digest": next(iter(digests)),
            "allowed_action_classes": sorted(
                {str(scope.get("action_class") or "") for scope in scopes}
            ),
            "allowed_roots": sorted(
                {
                    str(root)
                    for scope in scopes
                    for root in scope.get("allowed_roots") or ()
                }
            ),
            "exact_paths": sorted(
                {
                    str(path)
                    for scope in scopes
                    for path in scope.get("exact_paths") or ()
                }
            ),
            "denied_actions": [],
            "max_uses": 8,
            "ttl_seconds": 3600,
            "requested_by": "agent-pbx-tui",
            "metadata": {
                "profile_label": "Rosie Workbench",
                "approved_handoff_ids": list(handoff_ids),
            },
        }
        lease_id = ""
        lease_status = ""
        applied = 0
        try:
            for handoff_id in handoff_ids:
                preflight = await self.preflight_operator_handoff(
                    logical_operator_id,
                    handoff_id,
                )
                if not bool(preflight.get("ok")):
                    raise RuntimeError(
                        f"{handoff_id}: {preflight.get('reason') or 'preflight failed'}"
                    )
            response = await self.api_client().post(
                "/v1/operator/delegation-leases",
                json=payload,
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            lease = response.json()
            if not isinstance(lease, dict):
                raise RuntimeError("delegation lease response was not an object")
            lease_id = str(lease.get("lease_id") or "")
            if not lease_id:
                raise RuntimeError("delegation lease response omitted the lease ID")
            lease_status = "requested"
            decision = await self.api_client().post(
                f"/v1/operator/delegation-leases/{lease_id}/decision",
                json={"approve": True, "decided_by": "local-tui-user"},
                headers=auth_headers(self.token),
            )
            decision.raise_for_status()
            lease_status = "approved"
            for handoff_id in handoff_ids:
                result = await self.api_client().post(
                    f"/v1/operator/delegation-leases/{lease_id}/apply/{handoff_id}",
                    headers=auth_headers(self.token),
                )
                result.raise_for_status()
                applied += 1
        except Exception as exc:
            cleanup = ""
            if lease_id and lease_status:
                try:
                    if lease_status == "approved":
                        response = await self.api_client().post(
                            f"/v1/operator/delegation-leases/{lease_id}/revoke",
                            json={"revoked_by": "agent-pbx-tui-failed-activation"},
                            headers=auth_headers(self.token),
                        )
                        response.raise_for_status()
                        cleanup = "; the incomplete lease was revoked"
                    elif lease_status == "requested":
                        response = await self.api_client().post(
                            f"/v1/operator/delegation-leases/{lease_id}/decision",
                            json={
                                "approve": False,
                                "decided_by": "agent-pbx-tui-failed-activation",
                            },
                            headers=auth_headers(self.token),
                        )
                        response.raise_for_status()
                        cleanup = "; the incomplete lease was rejected"
                except Exception as cleanup_exc:
                    cleanup = f"; lease cleanup also failed: {cleanup_exc}"
            self.notify(
                f"Unable to activate campaign delegation after {applied} "
                f"handoff(s): {exc}{cleanup}",
                severity="error",
            )
            return
        try:
            await self.refresh_events()
            await self.refresh_agents()
            await self.refresh_operator_handoff_approval_screen(approval_screen)
        except Exception as exc:
            self.notify(
                "Campaign lease was activated, but the approval view could not "
                f"refresh: {exc}",
                severity="warning",
            )
        self.notify(
            f"Approved one-hour campaign lease {lease_id[:8]} and applied {applied} handoff(s)."
        )

    async def preflight_pending_operator_handoff(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            handoffs = await self.fetch_operator_handoffs(
                logical_operator_id,
                limit=50,
            )
        except Exception as exc:
            self.notify(f"Unable to fetch operator handoffs: {exc}", severity="error")
            return
        candidates = self.pending_source_handoffs(logical_operator_id, handoffs)
        if not candidates:
            self.notify(f"No pending operator handoffs for {logical_operator_id}.")
            return
        handoff = candidates[0]
        source_operator_id = str(
            handoff.get("logical_operator_agent_id") or logical_operator_id
        )
        handoff_id = str(handoff.get("handoff_id") or "").strip()
        if not handoff_id:
            self.notify("Pending handoff is missing an id.", severity="error")
            return
        try:
            preflight = await self.preflight_operator_handoff(
                source_operator_id,
                handoff_id,
            )
        except Exception as exc:
            self.notify(
                f"Unable to preflight operator handoff: {exc}", severity="error"
            )
            return
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = self.format_operator_handoff_preflight(preflight)
        self.activate_latest_tab()
        severity = "information" if bool(preflight.get("ok")) else "warning"
        self.notify(
            f"Handoff preflight {preflight.get('status') or 'checked'}.",
            severity=severity,
        )

    async def approve_pending_operator_handoff(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            handoffs = await self.fetch_operator_handoffs(
                logical_operator_id,
                limit=50,
            )
        except Exception as exc:
            self.notify(f"Unable to fetch operator handoffs: {exc}", severity="error")
            return
        candidates = self.pending_source_handoffs(logical_operator_id, handoffs)
        if not candidates:
            self.notify(f"No pending operator handoffs for {logical_operator_id}.")
            return
        handoff = candidates[0]
        source_operator_id = str(
            handoff.get("logical_operator_agent_id") or logical_operator_id
        )
        handoff_id = str(handoff.get("handoff_id") or "").strip()
        if not handoff_id:
            self.notify("Pending handoff is missing an id.", severity="error")
            return
        preflight: dict[str, Any] | None = None
        preflight_warning = ""
        try:
            preflight = await self.preflight_operator_handoff(
                source_operator_id,
                handoff_id,
            )
            preflight_warning = self.operator_handoff_preflight_warning(preflight)
            result = await self.approve_operator_handoff(
                source_operator_id,
                handoff_id,
            )
        except Exception as exc:
            self.notify(f"Unable to approve operator handoff: {exc}", severity="error")
            return
        updated = (
            result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
        )
        command = (
            result.get("command") if isinstance(result.get("command"), dict) else {}
        )
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            preflight_lines = ""
            if preflight is not None:
                preflight_lines = (
                    "\n"
                    f"Preflight: {preflight.get('status') or '-'} "
                    f"{preflight.get('resolved_delivery') or '-'}\n"
                    f"Preflight reason: {preflight.get('reason') or '-'}\n"
                    f"Preflight warnings: {len(preflight.get('warnings') or [])}"
                )
            detail.text = (
                "Operator handoff approval result.\n\n"
                f"Handoff: {handoff_id}\n"
                f"Status: {updated.get('status') or '-'}\n"
                f"Target operator: {updated.get('target_operator_agent_id') or '-'}\n"
                f"Target caller: {updated.get('target_caller_agent_id') or '-'}\n"
                f"Target fork: {updated.get('target_operator_fork_id') or '-'}\n"
                f"Delivery: {updated.get('delivery_status') or '-'}\n"
                f"Command: {command.get('command_id') or '-'}"
                f"{preflight_lines}"
            )
        await self.refresh_events()
        target = str(updated.get("target_operator_agent_id") or "")
        if target:
            await self.load_thread(target)
        status = str(updated.get("status") or "")
        if status == "pending_launch":
            self.notify("Handoff is pending required fork launch.", severity="warning")
        elif preflight_warning:
            self.notify(
                f"Operator handoff approved with preflight warning: {preflight_warning}",
                severity="warning",
            )
        else:
            self.notify(f"Operator handoff {handoff_id} approved.")

    async def launch_pending_operator_handoff_fork(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            handoffs = await self.fetch_operator_handoffs(
                logical_operator_id,
                status="pending_launch",
                limit=50,
            )
        except Exception as exc:
            self.notify(f"Unable to fetch pending handoffs: {exc}", severity="error")
            return
        handoff = next(
            (
                item
                for item in handoffs
                if str(item.get("target_operator_agent_id") or "").strip()
                and str(item.get("target_caller_agent_id") or "").strip()
            ),
            None,
        )
        if handoff is None:
            self.notify(f"No launchable pending handoffs for {logical_operator_id}.")
            return
        target_operator_id = str(handoff.get("target_operator_agent_id") or "").strip()
        target_caller_id = str(handoff.get("target_caller_agent_id") or "").strip()
        if target_caller_id not in self.agents:
            await self.refresh_agents()
        if target_caller_id not in self.agents:
            self.notify(
                f"Target caller {target_caller_id} is not loaded.",
                severity="error",
            )
            return
        try:
            fork = await self.ensure_operator_fork_from_tui(
                logical_operator_id=target_operator_id,
                source_caller_agent_id=target_caller_id,
            )
        except Exception as exc:
            self.notify(f"Unable to launch handoff fork: {exc}", severity="error")
            return
        if fork is None:
            return
        handoff_id = str(handoff.get("handoff_id") or "").strip()
        source_operator_id = str(handoff.get("logical_operator_agent_id") or "").strip()
        try:
            result = await self.approve_operator_handoff(
                source_operator_id,
                handoff_id,
            )
        except Exception as exc:
            self.notify(
                f"Launched fork, but handoff retry failed: {exc}",
                severity="error",
            )
            return
        updated = (
            result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
        )
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = (
                "Required handoff fork launched.\n\n"
                f"Handoff: {handoff_id}\n"
                f"Fork: {fork.get('fork_agent_id') or '-'}\n"
                f"Fork ID: {fork.get('operator_fork_id') or '-'}\n"
                f"Handoff status: {updated.get('status') or '-'}\n"
                f"Command: {updated.get('command_id') or '-'}"
            )
        await self.refresh_agents()
        await self.refresh_events()
        self.notify(f"Launched required fork and retried handoff {handoff_id}.")

    def format_operator_handoffs(
        self,
        logical_operator_id: str,
        handoffs: list[dict[str, Any]],
    ) -> str:
        lines = [
            f"Operator handoffs: {logical_operator_id}",
            "",
        ]
        if not handoffs:
            lines.append("No operator handoffs.")
            return "\n".join(lines)
        for handoff in handoffs:
            remaining = handoff.get("time_remaining_seconds")
            remaining_text = "-"
            if isinstance(remaining, (int, float)):
                remaining_text = f"{remaining:.0f}s"
            evidence = (
                handoff.get("delivery_evidence")
                if isinstance(handoff.get("delivery_evidence"), dict)
                else {}
            )
            artifacts = handoff.get("artifact_bundle")
            artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
            preflight_state = self.operator_handoff_preflight_state(handoff)
            monitor_state = self.operator_handoff_monitor_state(handoff, evidence)
            lines.extend(
                [
                    f"- {handoff.get('handoff_id')}",
                    f"  status: {handoff.get('status')}  safe_to_start: {handoff.get('safe_to_start')}",
                    f"  source: {handoff.get('source_agent_id')}  target: {handoff.get('target_operator_agent_id')}",
                    f"  target caller: {handoff.get('target_caller_agent_id') or '-'}",
                    f"  target fork: {handoff.get('target_operator_fork_id') or '-'}",
                    f"  expires in: {remaining_text}",
                    f"  delivery: {handoff.get('delivery_status') or '-'}  command: {handoff.get('command_id') or '-'}",
                    f"  pane: {evidence.get('tmux_pane_id') or '-'}  target command: {evidence.get('target_command') or '-'}",
                    f"  codex-like: {evidence.get('codex_like_target')}  submitted: {evidence.get('submitted_to_codex')}",
                    f"  ack: {evidence.get('agent_acknowledged')}  started: {evidence.get('agent_started')}",
                    f"  preflight: {preflight_state}",
                    f"  monitor: {monitor_state}",
                    f"  artifacts: {artifact_count}",
                    f"  summary: {handoff.get('summary') or '-'}",
                ]
            )
        return "\n".join(lines)

    def operator_handoff_preflight_state(self, handoff: dict[str, Any]) -> str:
        metadata = (
            handoff.get("metadata") if isinstance(handoff.get("metadata"), dict) else {}
        )
        preflight = (
            metadata.get("delivery_preflight")
            if isinstance(metadata.get("delivery_preflight"), dict)
            else None
        )
        if preflight is None:
            status = str(handoff.get("status") or "").strip().lower()
            return "not recorded" if status in {"proposed", "approved"} else "-"
        checked_at = float_value(preflight.get("checked_at"))
        age_text = "-"
        stale_text = ""
        if checked_at is not None:
            age = max(0.0, time.time() - checked_at)
            age_text = f"{age:.0f}s ago"
            if age > HANDOFF_PREFLIGHT_STALE_SECONDS:
                stale_text = " stale"
        warnings = preflight.get("warnings")
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        reason = str(preflight.get("reason") or "").strip()
        reason_text = f" reason={reason}" if reason else ""
        return (
            f"{preflight.get('status') or '-'}"
            f"/{preflight.get('resolved_delivery') or '-'}"
            f" age={age_text}{stale_text}"
            f" warnings={warning_count}{reason_text}"
        )

    def operator_handoff_monitor_state(
        self,
        handoff: dict[str, Any],
        evidence: dict[str, Any],
    ) -> str:
        status = str(handoff.get("status") or "").strip().lower()
        if status in {
            "complete",
            "completed",
            "failed",
            "blocked",
            "expired",
            "canceled",
            "cancelled",
        }:
            return "terminal"
        if bool(evidence.get("agent_started")) or handoff.get("started_at"):
            return "running"
        if bool(evidence.get("agent_acknowledged")) or handoff.get("acknowledged_at"):
            return "acknowledged; waiting start"
        if status == "sent" and bool(handoff.get("needs_ack")):
            sent_at = float_value(handoff.get("updated_at")) or float_value(
                handoff.get("created_at")
            )
            age = max(0.0, time.time() - sent_at) if sent_at is not None else None
            if age is None:
                return "sent; awaiting ack"
            level = "late" if age >= HANDOFF_ACK_WARN_SECONDS else "pending"
            return f"{level}; awaiting ack {age:.0f}s"
        if status == "pending_launch":
            return "waiting target fork launch"
        return "-"

    def operator_handoff_preflight_warning(
        self, preflight: dict[str, Any] | None
    ) -> str:
        if preflight is None:
            return "handoff was approved without a preflight result"
        checked_at = float_value(preflight.get("checked_at"))
        if (
            checked_at is not None
            and time.time() - checked_at > HANDOFF_PREFLIGHT_STALE_SECONDS
        ):
            return "handoff preflight is stale"
        warnings = preflight.get("warnings")
        if isinstance(warnings, list) and warnings:
            return "; ".join(str(item) for item in warnings if str(item).strip())
        if not bool(preflight.get("ok")):
            return str(
                preflight.get("reason") or preflight.get("status") or "preflight failed"
            )
        return ""

    def format_operator_handoff_preflight(self, preflight: dict[str, Any]) -> str:
        pane = preflight.get("pane") if isinstance(preflight.get("pane"), dict) else {}
        target = (
            preflight.get("target") if isinstance(preflight.get("target"), dict) else {}
        )
        target_fork = (
            preflight.get("target_fork")
            if isinstance(preflight.get("target_fork"), dict)
            else {}
        )
        kb_context = (
            preflight.get("kb_context")
            if isinstance(preflight.get("kb_context"), dict)
            else {}
        )
        warnings = preflight.get("warnings")
        warning_lines = (
            [f"- {item}" for item in warnings if isinstance(item, str)]
            if isinstance(warnings, list)
            else []
        )
        lines = [
            "Operator handoff delivery preflight.",
            "",
            f"Handoff: {preflight.get('handoff_id') or '-'}",
            f"Status: {preflight.get('status') or '-'}",
            f"OK: {preflight.get('ok')}  possible: {preflight.get('delivery_possible')}",
            f"Requested delivery: {preflight.get('requested_delivery') or '-'}",
            f"Resolved delivery: {preflight.get('resolved_delivery') or '-'}",
            f"Checked: {preflight.get('checked_at') or '-'}",
            f"Reason: {preflight.get('reason') or '-'}",
            "",
            "Target:",
            f"- operator: {target.get('agent_id') or preflight.get('target_operator_agent_id') or '-'}",
            f"- pbx mode: {target.get('pbx_mode') or '-'}",
            f"- cwd: {target.get('cwd') or '-'}",
            f"- tmux pane: {target.get('tmux_pane_id') or '-'}",
            "",
            "Target fork:",
            f"- fork: {target_fork.get('fork_agent_id') or '-'}",
            f"- status: {target_fork.get('status') or '-'}",
            f"- pending: {target_fork.get('operator_fork_pending')}",
            "",
            "Pane:",
            f"- pane: {pane.get('pane_id') or '-'}",
            f"- target: {pane.get('target_label') or '-'}",
            f"- command: {pane.get('current_command') or '-'}",
            f"- cwd: {pane.get('cwd') or '-'}",
            f"- codex-like: {pane.get('codex_like_target')}",
            "",
            "KB context:",
            f"- satisfied: {kb_context.get('satisfied_by_kb')}",
            f"- matches: {kb_context.get('match_count') if kb_context else 0}",
            "",
            "Warnings:",
            *(warning_lines or ["- none"]),
        ]
        return "\n".join(lines)

    async def show_selected_operator_knowledge_links(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            links = await self.fetch_operator_knowledge_links(
                logical_operator_id,
                limit=50,
            )
        except Exception as exc:
            self.notify(f"Unable to fetch knowledge links: {exc}", severity="error")
            return
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = self.format_operator_knowledge_links(
                logical_operator_id,
                links,
            )
        self.activate_latest_tab()

    async def send_pending_operator_knowledge_handoff(self) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            links = await self.fetch_operator_knowledge_links(
                logical_operator_id,
                status="proposed",
                limit=20,
            )
        except Exception as exc:
            self.notify(
                f"Unable to fetch pending knowledge links: {exc}", severity="error"
            )
            return
        pending_links = [
            link for link in links if int_value(link.get("pending_turn_count")) or 0
        ]
        if not pending_links:
            self.notify(f"No pending knowledge handoffs for {logical_operator_id}.")
            return
        link = pending_links[0]
        link_id = str(link.get("link_id") or "").strip()
        if not link_id:
            self.notify("Pending knowledge link is missing an id.", severity="error")
            return
        try:
            context = await self.fetch_operator_knowledge_context(
                logical_operator_id,
                link_id,
            )
            turns = (
                context.get("turns") if isinstance(context.get("turns"), list) else []
            )
            pending_turn = next(
                (
                    turn
                    for turn in turns
                    if isinstance(turn, dict)
                    and str(turn.get("delivery_status") or "") == "pending_approval"
                ),
                None,
            )
            if pending_turn is None:
                raise RuntimeError("pending knowledge turn not found")
            result = await self.approve_operator_knowledge_turn(
                logical_operator_id,
                link_id,
                str(pending_turn.get("turn_id") or ""),
            )
        except Exception as exc:
            self.notify(f"Unable to send knowledge handoff: {exc}", severity="error")
            return
        delivered_turn = (
            result.get("turn") if isinstance(result.get("turn"), dict) else {}
        )
        command = (
            result.get("command") if isinstance(result.get("command"), dict) else {}
        )
        recipient = str(
            delivered_turn.get("recipient_agent_id")
            or link.get("target_agent_id")
            or ""
        )
        status = str(delivered_turn.get("delivery_status") or "")
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = (
                "Knowledge handoff sent.\n\n"
                f"Link: {link_id}\n"
                f"Turn: {delivered_turn.get('turn_id') or '-'}\n"
                f"Recipient: {recipient or '-'}\n"
                f"Delivery: {status or '-'}\n"
                f"Command: {command.get('command_id') or '-'}"
            )
        self.notify(f"Knowledge handoff sent to {recipient or 'target'}.")
        await self.refresh_events()
        if recipient:
            await self.load_thread(recipient)

    def format_operator_knowledge_links(
        self,
        logical_operator_id: str,
        links: list[dict[str, Any]],
    ) -> str:
        lines = [
            f"Operator knowledge links: {logical_operator_id}",
            "",
        ]
        if not links:
            lines.append("No knowledge links.")
            return "\n".join(lines)
        for link in links:
            pending = int_value(link.get("pending_turn_count")) or 0
            latest = float_value(link.get("latest_turn_at"))
            latest_text = self.format_short_time(latest) if latest is not None else "-"
            lines.extend(
                [
                    f"- {link.get('link_id')}",
                    f"  status: {link.get('status')}  type: {link.get('link_type')}",
                    f"  source: {link.get('source_agent_id')}",
                    f"  target: {link.get('target_agent_id')}",
                    f"  pending turns: {pending}  latest turn: {latest_text}",
                    f"  summary: {link.get('summary') or '-'}",
                ]
            )
        return "\n".join(lines)
