from __future__ import annotations

import hashlib
import os
import re
import socket
from typing import Any

from . import tmux as tmux_support
from .schemas import (
    AgentRegisterRequest,
    CommandCreateRequest,
    OperatorAssignmentCreate,
    ReportCreateRequest,
)
from .store import Store


CALLER_AGENT_TYPE = "caller"
OPERATOR_AGENT_TYPE = "operator"
TERMINAL_OPERATOR_BASE_STATES = {
    "complete",
    "completed",
    "blocked",
    "failed",
    "canceled",
    "cancelled",
    "superseded",
}
TERMINAL_OPERATOR_STATE_PREFIXES = (
    "complete_",
    "complete-",
    "completed_",
    "completed-",
    "blocked_",
    "blocked-",
    "failed_",
    "failed-",
    "canceled_",
    "canceled-",
    "cancelled_",
    "cancelled-",
)
NOHUP_MODE = "nohup"
FORK_READY_STATUSES = {"starting", "running", "ready"}
DEFAULT_FORK_TRACK_ID = "default"
DEFAULT_FORK_PURPOSE = "edit"
DEFAULT_FORK_ACCESS_MODE = "edit"
REVIEW_FORK_PURPOSE = "review"
REVIEW_FORK_ACCESS_MODE = "review_readonly"
REVIEW_ESCALATION_ROUTES = {
    "primary_idle_edit_fork",
    "root_operator",
    "new_write_operator",
}
DEFAULT_OPERATOR_TMUX_SESSION = "agent-pbx-operators"
OPERATOR_TMUX_SESSION_ENV = "AGENT_PBX_TUI_OPERATOR_TMUX_SESSION"
ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
FORK_LAUNCH_REQUIRED_REASON = (
    "operator fork has not been launched; use the TUI to create the first fork for this caller"
)


def operator_state_is_terminal(state: str) -> bool:
    normalized = str(state or "").strip().lower()
    return bool(
        normalized
        and (
            normalized in TERMINAL_OPERATOR_BASE_STATES
            or normalized.startswith(TERMINAL_OPERATOR_STATE_PREFIXES)
        )
    )


def operator_runbook_payload() -> dict[str, Any]:
    return {
        "title": "Agent PBX Operator Runbook",
        "summary": (
            "Operator agents coordinate caller agents through tracked campaigns. "
            "Campaign tables are the source of truth; caller reports and commands "
            "remain the audit trail."
        ),
        "loop": [
            "Start a campaign with title, objective, shared criteria, and one assignment per caller.",
            "Dispatch or follow up through pbx_operator_start_campaign and pbx_operator_send_followup.",
            "Keep the root operator turn active while assignments are running; periodically recheck campaign state.",
            "Inspect caller reports and threads with pbx_operator_get_thread.",
            "Mark each assignment complete, blocked, or needing follow-up with pbx_operator_report_assignment.",
            "Finish the campaign only after every assignment is complete or explicitly blocked.",
        ],
        "delivery": [
            "Operator work is routed only to the per-caller Agent PBX forked operator session.",
            "A PBX fork is the visible tmux/Codex pane registered as operator-<n>-fork-<caller>-<hash>.",
            "Each caller session has a default/edit fork; review forks use separate fork_track_id values such as review-1.",
            "Read-only review work should use review forks and write only to the configured work_root outside the caller project directory.",
            "When review work finds an edit task, escalate it to the root operator or to the idle default/edit fork; do not let review forks edit the source project.",
            "The root operator coordinates campaigns and reviews evidence; it must not implement caller repo changes directly.",
            "Do not spawn or use Codex internal subagents for caller work; do not call multi_agent_v1.",
            "delivery='queue' creates normal PBX send_input commands for nohup fork sessions.",
            "delivery='tmux' sends directly to a uniquely matched local tmux Codex pane for the fork.",
            "delivery='auto' queues nohup forks and uses tmux for report-mode forks.",
            "Callers must register metadata.codex_session_id before Agent PBX can create a fork.",
        ],
        "guardrails": [
            "Do not poll or ack commands for caller agents.",
            "Treat campaign criteria as completion criteria, not loose guidance.",
            "Use explicit blocked states when a caller cannot complete its assignment.",
            "Keep operator reports concise and put full review notes in detail.",
        ],
    }


class OperatorService:
    def __init__(self, store: Store, *, tmux_bin: str = "tmux") -> None:
        self.store = store
        self.tmux_bin = tmux_bin

    def list_agents(self, *, agent_type: str = "caller") -> list[dict[str, Any]]:
        requested = str(agent_type or "caller").strip().lower()
        agents = self.store.list_agents()
        if requested == "all":
            return agents
        if requested not in {CALLER_AGENT_TYPE, OPERATOR_AGENT_TYPE}:
            raise ValueError("agent_type must be caller, operator, or all")
        return [agent for agent in agents if agent.get("agent_type") == requested]

    def get_thread(self, agent_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        self._require_agent(agent_id)
        return self.store.list_thread(agent_id, limit=limit)

    def list_forks(
        self,
        *,
        operator_agent_id: str | None = None,
        source_caller_agent_id: str | None = None,
        fork_track_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        logical_operator_id = None
        if operator_agent_id:
            logical_operator_id = self._logical_operator_agent_id(
                self._require_operator(operator_agent_id)
            )
        return self.store.list_operator_forks(
            logical_operator_agent_id=logical_operator_id,
            source_caller_agent_id=source_caller_agent_id,
            fork_track_id=(
                self.normalize_fork_track_id(fork_track_id)
                if fork_track_id
                else None
            ),
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )

    def ensure_fork(
        self,
        *,
        operator_agent_id: str,
        source_caller_agent_id: str,
        fork_agent_id: str | None = None,
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        source_cwd: str | None = None,
        work_root: str | None = None,
        campaign_id: str | None = None,
        tmux_pane_id: str | None = None,
        fork_codex_session_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        caller = self._require_caller(source_caller_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        caller_metadata = caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        source_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        caller_cwd = str(caller_metadata.get("cwd") or "").strip()
        resolved_source_cwd = str(source_cwd or caller_cwd).strip()
        resolved_work_root = str(work_root or resolved_source_cwd).strip()
        resolved_cwd = resolved_work_root or resolved_source_cwd or str(caller.get("project") or "")
        resolved_track_id = self.normalize_fork_track_id(fork_track_id)
        resolved_purpose = self.normalize_fork_label(
            fork_purpose,
            default=(
                DEFAULT_FORK_PURPOSE
                if resolved_track_id == DEFAULT_FORK_TRACK_ID
                else REVIEW_FORK_PURPOSE
            ),
        )
        resolved_access_mode = self.normalize_fork_label(
            access_mode,
            default=(
                DEFAULT_FORK_ACCESS_MODE
                if resolved_purpose == DEFAULT_FORK_PURPOSE
                else REVIEW_FORK_ACCESS_MODE
            ),
        )
        source_codex_home = str(caller_metadata.get("codex_home") or "").strip() or None
        source_host_id = str(caller_metadata.get("codex_host_id") or "").strip() or None
        local_host_id = self.local_codex_host_id()
        blocked_reason = self._fork_blocked_reason(
            source_session_id=source_session_id,
            source_cwd=resolved_source_cwd,
            source_host_id=source_host_id,
            local_host_id=local_host_id,
        )
        if source_session_id:
            existing = self.store.get_operator_fork_for_source(
                logical_operator_agent_id=logical_operator_id,
                source_caller_agent_id=source_caller_agent_id,
                source_codex_session_id=source_session_id,
                fork_track_id=resolved_track_id,
            )
            if existing is not None:
                resolved_fork_agent_id = fork_agent_id or str(existing["fork_agent_id"])
                ready_status = str(status or "").strip().lower() in FORK_READY_STATUSES
                launching = bool(tmux_pane_id or fork_codex_session_id or ready_status)
                existing_metadata = (
                    existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                )
                fork_metadata = {
                    **existing_metadata,
                    "agent_type": OPERATOR_AGENT_TYPE,
                    "operator_role": "fork",
                    "logical_operator_id": logical_operator_id,
                    "source_caller_agent_id": source_caller_agent_id,
                    "source_codex_session_id": source_session_id,
                    "fork_track_id": resolved_track_id,
                    "fork_purpose": resolved_purpose,
                    "access_mode": resolved_access_mode,
                    "source_cwd": resolved_source_cwd,
                    "work_root": resolved_work_root,
                    "cwd": resolved_cwd,
                }
                if tmux_pane_id:
                    fork_metadata["tmux_pane_id"] = tmux_pane_id
                if fork_codex_session_id:
                    fork_metadata["fork_codex_session_id"] = fork_codex_session_id
                merged_metadata = {**fork_metadata, **(metadata or {})}
                merged_metadata.update(
                    {
                        "agent_type": OPERATOR_AGENT_TYPE,
                        "operator_role": "fork",
                        "logical_operator_id": logical_operator_id,
                        "source_caller_agent_id": source_caller_agent_id,
                        "source_codex_session_id": source_session_id,
                        "fork_track_id": resolved_track_id,
                        "fork_purpose": resolved_purpose,
                        "access_mode": resolved_access_mode,
                        "source_cwd": resolved_source_cwd,
                        "work_root": resolved_work_root,
                        "cwd": resolved_cwd,
                    }
                )
                if tmux_pane_id:
                    merged_metadata["tmux_pane_id"] = tmux_pane_id
                if fork_codex_session_id:
                    merged_metadata["fork_codex_session_id"] = fork_codex_session_id
                if launching and not blocked_reason:
                    fork_metadata["operator_fork_pending"] = False
                    merged_metadata["operator_fork_pending"] = False
                    fork_agent = self.store.get_agent(resolved_fork_agent_id) or {}
                    self.store.register_agent(
                        AgentRegisterRequest(
                            agent_id=resolved_fork_agent_id,
                            project=str(
                                fork_agent.get("project")
                                or operator.get("project")
                                or "agent-pbx-operator"
                            ),
                            name=(
                                str(fork_agent["name"])
                                if fork_agent.get("name")
                                else resolved_fork_agent_id
                            ),
                            agent_type=OPERATOR_AGENT_TYPE,
                            metadata=merged_metadata,
                            pbx_active=True,
                        )
                    )
                updated = self.store.update_operator_fork(
                    existing["operator_fork_id"],
                    fork_agent_id=resolved_fork_agent_id,
                    campaign_id=campaign_id,
                    tmux_pane_id=tmux_pane_id,
                    fork_codex_session_id=fork_codex_session_id,
                    status=status,
                    summary=summary,
                    cwd=resolved_cwd,
                    fork_purpose=resolved_purpose,
                    access_mode=resolved_access_mode,
                    source_cwd=resolved_source_cwd,
                    work_root=resolved_work_root,
                    metadata=merged_metadata,
                    touch=True,
                ) or existing
                if launching and not blocked_reason:
                    self.store.append_event(
                        "operator_fork_ensured",
                        {
                            "operator_fork_id": updated["operator_fork_id"],
                            "logical_operator_agent_id": logical_operator_id,
                            "source_caller_agent_id": source_caller_agent_id,
                            "fork_agent_id": resolved_fork_agent_id,
                            "fork_track_id": resolved_track_id,
                        },
                        updated["operator_fork_id"],
                    )
                return updated
        if not blocked_reason and not tmux_pane_id and fork_agent_id is None:
            blocked_reason = FORK_LAUNCH_REQUIRED_REASON
        unlaunchable_block = (
            bool(blocked_reason) and blocked_reason != FORK_LAUNCH_REQUIRED_REASON
        )
        resolved_session_id = source_session_id or "missing"
        resolved_fork_agent_id = fork_agent_id or self.default_fork_agent_id(
            logical_operator_id,
            source_caller_agent_id,
            resolved_session_id,
            fork_track_id=resolved_track_id,
        )
        fork_metadata = {
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": "fork",
            "logical_operator_id": logical_operator_id,
            "source_caller_agent_id": source_caller_agent_id,
            "source_codex_session_id": source_session_id,
            "fork_track_id": resolved_track_id,
            "fork_purpose": resolved_purpose,
            "access_mode": resolved_access_mode,
            "source_cwd": resolved_source_cwd,
            "work_root": resolved_work_root,
            "cwd": resolved_cwd,
            "operator_fork_pending": bool(blocked_reason),
        }
        if tmux_pane_id:
            fork_metadata["tmux_pane_id"] = tmux_pane_id
        if fork_codex_session_id:
            fork_metadata["fork_codex_session_id"] = fork_codex_session_id
        if blocked_reason:
            fork_metadata["operator_fork_blocked_reason"] = blocked_reason
        if unlaunchable_block:
            fork_metadata["operator_fork_launchable"] = False
        fork_metadata.update(metadata or {})
        fork_metadata.update(
            {
                "agent_type": OPERATOR_AGENT_TYPE,
                "operator_role": "fork",
                "logical_operator_id": logical_operator_id,
                "source_caller_agent_id": source_caller_agent_id,
                "source_codex_session_id": source_session_id,
                "fork_track_id": resolved_track_id,
                "fork_purpose": resolved_purpose,
                "access_mode": resolved_access_mode,
                "source_cwd": resolved_source_cwd,
                "work_root": resolved_work_root,
                "cwd": resolved_cwd,
            }
        )
        if tmux_pane_id:
            fork_metadata["tmux_pane_id"] = tmux_pane_id
        if fork_codex_session_id:
            fork_metadata["fork_codex_session_id"] = fork_codex_session_id
        self.store.register_agent(
            AgentRegisterRequest(
                agent_id=resolved_fork_agent_id,
                project=str(operator.get("project") or "agent-pbx-operator"),
                name=resolved_fork_agent_id,
                agent_type=OPERATOR_AGENT_TYPE,
                metadata=fork_metadata,
                pbx_active=not bool(blocked_reason),
            )
        )
        if unlaunchable_block:
            self.store.dismiss_agent(resolved_fork_agent_id)
        resolved_status = (
            "blocked"
            if unlaunchable_block
            else status or ("blocked" if blocked_reason else "starting")
        )
        resolved_summary = blocked_reason if unlaunchable_block else summary or blocked_reason
        fork = self.store.create_operator_fork(
            logical_operator_agent_id=logical_operator_id,
            fork_agent_id=resolved_fork_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=resolved_session_id,
            fork_codex_session_id=fork_codex_session_id,
            campaign_id=campaign_id,
            cwd=resolved_cwd,
            fork_track_id=resolved_track_id,
            fork_purpose=resolved_purpose,
            access_mode=resolved_access_mode,
            source_cwd=resolved_source_cwd,
            work_root=resolved_work_root,
            codex_home=source_codex_home,
            codex_host_id=source_host_id or local_host_id,
            tmux_pane_id=tmux_pane_id,
            status=resolved_status,
            summary=resolved_summary,
            metadata={
                **fork_metadata,
                "source_caller_project": caller.get("project"),
                "local_codex_host_id": local_host_id,
            },
        )
        if blocked_reason:
            self.store.append_event(
                "operator_fork_blocked",
                {
                    "operator_fork_id": fork["operator_fork_id"],
                    "logical_operator_agent_id": logical_operator_id,
                    "source_caller_agent_id": source_caller_agent_id,
                    "fork_track_id": resolved_track_id,
                    "reason": blocked_reason,
                },
                fork["operator_fork_id"],
            )
        else:
            self.store.append_event(
                "operator_fork_ensured",
                {
                    "operator_fork_id": fork["operator_fork_id"],
                    "logical_operator_agent_id": logical_operator_id,
                    "source_caller_agent_id": source_caller_agent_id,
                    "fork_agent_id": resolved_fork_agent_id,
                    "fork_track_id": resolved_track_id,
                },
                fork["operator_fork_id"],
            )
        return fork

    def link_forks(
        self,
        *,
        from_fork_id: str,
        to_fork_id: str,
        edge_type: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.store.get_operator_fork(from_fork_id) is None:
            raise ValueError("from_fork_id not found")
        if self.store.get_operator_fork(to_fork_id) is None:
            raise ValueError("to_fork_id not found")
        return self.store.create_operator_fork_edge(
            from_fork_id=from_fork_id,
            to_fork_id=to_fork_id,
            edge_type=edge_type,
            summary=summary,
            metadata=metadata or {},
        )

    def start_campaign(
        self,
        *,
        operator_agent_id: str,
        title: str,
        objective: str,
        criteria: list[str],
        assignments: list[OperatorAssignmentCreate | dict[str, Any]],
        delivery: str = "auto",
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        assignment_payloads = [
            self._assignment_payload(assignment) for assignment in assignments
        ]
        if not assignment_payloads:
            raise ValueError("campaign requires at least one assignment")
        for assignment in assignment_payloads:
            self._require_caller(str(assignment["target_agent_id"]))

        campaign = self.store.create_operator_campaign(
            operator_agent_id=operator_agent_id,
            title=title,
            objective=objective,
            criteria=criteria,
            assignments=assignment_payloads,
        )
        for assignment in campaign["assignments"]:
            message = self._initial_assignment_message(
                campaign=campaign,
                assignment=assignment,
            )
            try:
                command = self._deliver(
                    operator_agent_id=operator_agent_id,
                    campaign_id=campaign["campaign_id"],
                    target_agent_id=assignment["target_agent_id"],
                    message=message,
                    assignment_id=assignment["assignment_id"],
                    delivery=delivery,
                    source="operator_campaign_start",
                    operator_fork_id=assignment.get("operator_fork_id"),
                    fork_track_id=assignment.get("fork_track_id"),
                )
            except Exception as exc:  # noqa: BLE001 - delivery failure becomes state.
                self.store.update_operator_assignment(
                    assignment["assignment_id"],
                    state="blocked",
                    complete=True,
                )
                self.store.add_operator_campaign_event(
                    campaign_id=campaign["campaign_id"],
                    assignment_id=assignment["assignment_id"],
                    operator_agent_id=operator_agent_id,
                    target_agent_id=assignment["target_agent_id"],
                    event_type="dispatch_failed",
                    summary=f"Dispatch failed for {assignment['target_agent_id']}",
                    detail={"error": str(exc), "delivery": delivery},
                )
                continue
            state = "sent" if command["status"] == "sent" else "waiting"
            self._append_command_delivery_event(
                command,
                campaign_id=campaign["campaign_id"],
                assignment_id=assignment["assignment_id"],
            )
            self.store.update_operator_assignment(
                assignment["assignment_id"],
                state=state,
                last_command_id=command["command_id"],
                operator_fork_id=command["payload"].get("operator_fork_id"),
                fork_track_id=command["payload"].get("fork_track_id"),
            )
            fork_agent_id = command["payload"].get("fork_agent_id")
            dispatch_target = (
                f"{assignment['target_agent_id']} via {fork_agent_id}"
                if fork_agent_id
                else str(assignment["target_agent_id"])
            )
            self.store.add_operator_campaign_event(
                campaign_id=campaign["campaign_id"],
                assignment_id=assignment["assignment_id"],
                operator_agent_id=operator_agent_id,
                target_agent_id=assignment["target_agent_id"],
                event_type="assignment_dispatched",
                summary=f"Assignment dispatched to {dispatch_target}",
                detail={
                    "delivery_status": command["status"],
                    "delivery": delivery,
                    "operator_fork_id": command["payload"].get("operator_fork_id"),
                    "fork_agent_id": fork_agent_id,
                    "fork_track_id": command["payload"].get("fork_track_id"),
                },
                command_id=command["command_id"],
            )
        self.store.append_event(
            "operator_campaign_started",
            {
                "campaign_id": campaign["campaign_id"],
                "operator_agent_id": operator_agent_id,
                "assignment_count": len(assignment_payloads),
            },
            campaign["campaign_id"],
        )
        _ = operator
        return self.campaign_status(campaign_id=campaign["campaign_id"])[0]

    def send_followup(
        self,
        *,
        operator_agent_id: str,
        campaign_id: str | None,
        target_agent_id: str,
        message: str,
        assignment_id: str | None = None,
        operator_fork_id: str | None = None,
        fork_track_id: str | None = None,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        self._require_operator(operator_agent_id)
        campaign = self._require_campaign(campaign_id)
        assignment = self._resolve_assignment(
            campaign_id,
            target_agent_id=target_agent_id,
            assignment_id=assignment_id,
        )
        command = self._deliver(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            target_agent_id=target_agent_id,
            message=message,
            assignment_id=assignment["assignment_id"],
            delivery=delivery,
            source="operator_campaign_followup",
            operator_fork_id=operator_fork_id or assignment.get("operator_fork_id"),
            fork_track_id=fork_track_id or assignment.get("fork_track_id"),
        )
        state = "sent" if command["status"] == "sent" else "waiting"
        self._append_command_delivery_event(
            command,
            campaign_id=campaign_id,
            assignment_id=assignment["assignment_id"],
        )
        self.store.update_operator_assignment(
            assignment["assignment_id"],
            state=state,
            last_command_id=command["command_id"],
            operator_fork_id=command["payload"].get("operator_fork_id"),
            fork_track_id=command["payload"].get("fork_track_id"),
        )
        fork_agent_id = command["payload"].get("fork_agent_id")
        dispatch_target = (
            f"{target_agent_id} via {fork_agent_id}" if fork_agent_id else target_agent_id
        )
        self.store.add_operator_campaign_event(
            campaign_id=campaign_id,
            assignment_id=assignment["assignment_id"],
            operator_agent_id=operator_agent_id,
            target_agent_id=target_agent_id,
            event_type="followup_sent",
            summary=f"Follow-up sent to {dispatch_target}",
            detail={
                "delivery_status": command["status"],
                "delivery": delivery,
                "operator_fork_id": command["payload"].get("operator_fork_id"),
                "fork_agent_id": fork_agent_id,
                "fork_track_id": command["payload"].get("fork_track_id"),
            },
            command_id=command["command_id"],
        )
        _ = campaign
        return command

    def route_review_escalation(
        self,
        *,
        operator_agent_id: str,
        review_fork_id: str,
        message: str,
        route: str = "primary_idle_edit_fork",
        campaign_id: str | None = None,
        assignment_id: str | None = None,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        review_fork = (
            self.store.get_operator_fork(review_fork_id)
            or self.store.get_operator_fork_for_agent(review_fork_id)
        )
        if review_fork is None:
            raise ValueError("review_fork_id not found")
        if str(review_fork.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("review_fork_id belongs to a different operator")
        target_agent_id = str(review_fork.get("source_caller_agent_id") or "").strip()
        source_session_id = str(review_fork.get("source_codex_session_id") or "").strip()
        if not target_agent_id or not source_session_id:
            raise ValueError("review fork is missing source caller/session metadata")
        resolved_route = str(route or "primary_idle_edit_fork").strip().lower()
        if resolved_route not in REVIEW_ESCALATION_ROUTES:
            raise ValueError(
                "route must be primary_idle_edit_fork, root_operator, or new_write_operator"
            )
        if campaign_id:
            self._require_campaign(campaign_id)
        routed_message = self._review_escalation_message(
            review_fork=review_fork,
            message=message,
            route=resolved_route,
        )
        if resolved_route == "primary_idle_edit_fork":
            edit_fork = self.store.get_operator_fork_for_source(
                logical_operator_agent_id=logical_operator_id,
                source_caller_agent_id=target_agent_id,
                source_codex_session_id=source_session_id,
                fork_track_id=DEFAULT_FORK_TRACK_ID,
            )
            if edit_fork is None:
                raise ValueError("primary edit fork is not available")
            if self._operator_fork_has_active_assignment(edit_fork):
                raise ValueError(
                    "primary edit fork is not idle; route to root_operator or new_write_operator"
                )
            command = self._deliver(
                operator_agent_id=operator_agent_id,
                campaign_id=campaign_id,
                target_agent_id=target_agent_id,
                message=routed_message,
                assignment_id=assignment_id,
                delivery=delivery,
                source="operator_review_escalation",
                operator_fork_id=str(edit_fork["operator_fork_id"]),
                fork_track_id=DEFAULT_FORK_TRACK_ID,
            )
        else:
            root_message = routed_message
            if resolved_route == "new_write_operator":
                root_message = (
                    routed_message
                    + "\n\nRequested route: create a separate write-capable root "
                    "operator/fork for this work if parallel source edits are required."
                )
            command = self._deliver_root_operator_message(
                operator_agent_id=operator_agent_id,
                logical_operator_id=logical_operator_id,
                message=root_message,
                delivery=delivery,
                payload={
                    "source": "operator_review_escalation",
                    "campaign_id": campaign_id,
                    "assignment_id": assignment_id,
                    "operator_agent_id": operator_agent_id,
                    "review_fork_id": review_fork["operator_fork_id"],
                    "review_fork_agent_id": review_fork["fork_agent_id"],
                    "target_agent_id": target_agent_id,
                    "route": resolved_route,
                },
            )
        self._record_review_escalation_event(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            target_agent_id=target_agent_id,
            review_fork=review_fork,
            route=resolved_route,
            command=command,
        )
        return command

    def report_assignment(
        self,
        *,
        operator_agent_id: str,
        campaign_id: str,
        assignment_id: str,
        state: str,
        summary: str,
        detail: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        assignment = self.store.get_operator_campaign_assignment(assignment_id)
        if assignment is None or assignment["campaign_id"] != campaign_id:
            raise ValueError("assignment not found for campaign")
        complete = operator_state_is_terminal(state)
        report = self.store.create_report(
            operator_agent_id,
            ReportCreateRequest(
                project=str(operator["project"]),
                status=state,
                summary=summary,
                detail=detail,
                metadata={
                    "source": "operator_campaign",
                    "campaign_id": campaign_id,
                    "assignment_id": assignment_id,
                    "operator_agent_id": operator_agent_id,
                    "target_agent_id": assignment["target_agent_id"],
                    "completion_state": state,
                },
            ),
        )
        updated = self.store.update_operator_assignment(
            assignment_id,
            state=state,
            last_report_id=report["report_id"],
            complete=complete,
        )
        self.store.add_operator_campaign_event(
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            operator_agent_id=operator_agent_id,
            target_agent_id=assignment["target_agent_id"],
            event_type="assignment_reported",
            summary=summary,
            detail={"state": state},
            report_id=report["report_id"],
        )
        return updated or assignment

    def finish_campaign(
        self,
        *,
        operator_agent_id: str,
        campaign_id: str,
        status: str,
        summary: str,
        detail: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        self._require_campaign(campaign_id)
        complete = operator_state_is_terminal(status)
        report = self.store.create_report(
            operator_agent_id,
            ReportCreateRequest(
                project=str(operator["project"]),
                status=status,
                summary=summary,
                detail=detail,
                metadata={
                    "source": "operator_campaign",
                    "campaign_id": campaign_id,
                    "operator_agent_id": operator_agent_id,
                    "completion_state": status,
                },
            ),
        )
        updated = self.store.update_operator_campaign(
            campaign_id,
            status=status,
            summary=summary,
            complete=complete,
        )
        self.store.add_operator_campaign_event(
            campaign_id=campaign_id,
            operator_agent_id=operator_agent_id,
            event_type="campaign_finished",
            summary=summary,
            detail={"status": status},
            report_id=report["report_id"],
        )
        return updated or self._require_campaign(campaign_id)

    def campaign_status(
        self,
        *,
        operator_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if operator_agent_id:
            self._require_operator(operator_agent_id)
        return self.store.list_operator_campaigns(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )

    def _assignment_payload(
        self, assignment: OperatorAssignmentCreate | dict[str, Any]
    ) -> dict[str, Any]:
        if isinstance(assignment, OperatorAssignmentCreate):
            data = assignment.model_dump()
        else:
            data = dict(assignment)
        target_agent_id = str(data.get("target_agent_id") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        if not target_agent_id:
            raise ValueError("assignment target_agent_id is required")
        if not prompt:
            raise ValueError("assignment prompt is required")
        criteria = data.get("criteria") or []
        if not isinstance(criteria, list):
            raise ValueError("assignment criteria must be a list")
        operator_fork_id = str(data.get("operator_fork_id") or "").strip() or None
        fork_track_id = str(data.get("fork_track_id") or "").strip() or None
        return {
            "target_agent_id": target_agent_id,
            "operator_fork_id": operator_fork_id,
            "fork_track_id": (
                self.normalize_fork_track_id(fork_track_id)
                if fork_track_id is not None
                else None
            ),
            "title": str(data.get("title") or f"Assignment for {target_agent_id}"),
            "prompt": prompt,
            "criteria": [str(item) for item in criteria],
        }

    def _deliver(
        self,
        *,
        operator_agent_id: str,
        campaign_id: str,
        target_agent_id: str,
        message: str,
        assignment_id: str | None,
        delivery: str,
        source: str,
        operator_fork_id: str | None = None,
        fork_track_id: str | None = None,
    ) -> dict[str, Any]:
        fork = self._resolve_delivery_fork(
            operator_agent_id=operator_agent_id,
            target_agent_id=target_agent_id,
            campaign_id=campaign_id,
            operator_fork_id=operator_fork_id,
            fork_track_id=fork_track_id,
        )
        if str(fork.get("status") or "").lower() not in FORK_READY_STATUSES:
            raise ValueError(f"operator fork unavailable: {fork.get('summary') or fork.get('status')}")
        target = self._agent_with_fork_delivery_metadata(
            self._require_agent(str(fork["fork_agent_id"])),
            fork,
        )
        mode = str((target.get("metadata") or {}).get("pbx_mode") or "report").lower()
        resolved = str(delivery or "auto").strip().lower()
        if resolved == "auto":
            resolved = "queue" if mode == NOHUP_MODE else "tmux"
        payload = {
            "message": message,
            "source": source,
            "campaign_id": campaign_id,
            "assignment_id": assignment_id,
            "operator_agent_id": operator_agent_id,
            "target_agent_id": target_agent_id,
            "operator_fork_id": fork["operator_fork_id"],
            "fork_agent_id": fork["fork_agent_id"],
            "fork_track_id": fork.get("fork_track_id") or DEFAULT_FORK_TRACK_ID,
            "fork_purpose": fork.get("fork_purpose") or DEFAULT_FORK_PURPOSE,
            "access_mode": fork.get("access_mode") or DEFAULT_FORK_ACCESS_MODE,
            "source_cwd": fork.get("source_cwd"),
            "work_root": fork.get("work_root"),
        }
        if resolved == "queue":
            return self.store.create_command(
                CommandCreateRequest(
                    agent_id=str(fork["fork_agent_id"]),
                    type="send_input",
                    payload=payload,
                )
            )
        if resolved != "tmux":
            raise ValueError("delivery must be auto, queue, or tmux")
        pane = self._resolve_tmux_pane(target)
        tmux_support.send_text(pane.pane_id, message, tmux_bin=self.tmux_bin)
        payload["tmux_pane_id"] = pane.pane_id
        payload["tmux_target"] = pane.target_label
        return self.store.create_command(
            CommandCreateRequest(
                agent_id=str(fork["fork_agent_id"]),
                type="send_input",
                payload=payload,
            ),
            status="sent",
        )

    def _deliver_root_operator_message(
        self,
        *,
        operator_agent_id: str,
        logical_operator_id: str,
        message: str,
        delivery: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        target = self._require_agent(logical_operator_id)
        if target.get("agent_type") != OPERATOR_AGENT_TYPE:
            raise ValueError("logical root operator is not registered")
        mode = str((target.get("metadata") or {}).get("pbx_mode") or "report").lower()
        resolved = str(delivery or "auto").strip().lower()
        if resolved == "auto":
            resolved = "queue" if mode == NOHUP_MODE else "tmux"
        command_payload = {
            "message": message,
            **payload,
            "logical_operator_id": logical_operator_id,
        }
        if resolved == "queue":
            return self.store.create_command(
                CommandCreateRequest(
                    agent_id=logical_operator_id,
                    type="send_input",
                    payload=command_payload,
                )
            )
        if resolved != "tmux":
            raise ValueError("delivery must be auto, queue, or tmux")
        pane = self._resolve_tmux_pane(target)
        tmux_support.send_text(pane.pane_id, message, tmux_bin=self.tmux_bin)
        command_payload["tmux_pane_id"] = pane.pane_id
        command_payload["tmux_target"] = pane.target_label
        return self.store.create_command(
            CommandCreateRequest(
                agent_id=logical_operator_id,
                type="send_input",
                payload=command_payload,
            ),
            status="sent",
        )

    def _resolve_delivery_fork(
        self,
        *,
        operator_agent_id: str,
        target_agent_id: str,
        campaign_id: str | None,
        operator_fork_id: str | None,
        fork_track_id: str | None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        resolved_operator_fork_id = str(operator_fork_id or "").strip()
        if resolved_operator_fork_id:
            fork = self.store.get_operator_fork(resolved_operator_fork_id)
            if fork is None:
                fork = self.store.get_operator_fork_for_agent(resolved_operator_fork_id)
            if fork is None:
                raise ValueError("operator_fork_id not found")
            if str(fork.get("logical_operator_agent_id") or "") != logical_operator_id:
                raise ValueError("operator_fork_id belongs to a different operator")
            if str(fork.get("source_caller_agent_id") or "") != target_agent_id:
                raise ValueError("operator_fork_id source caller does not match target_agent_id")
            return self.store.update_operator_fork(
                str(fork["operator_fork_id"]),
                campaign_id=campaign_id,
                touch=True,
            ) or fork
        return self.ensure_fork(
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=target_agent_id,
            campaign_id=campaign_id,
            fork_track_id=fork_track_id,
        )

    def _operator_fork_has_active_assignment(self, fork: dict[str, Any]) -> bool:
        operator_fork_id = str(fork.get("operator_fork_id") or "").strip()
        target_agent_id = str(fork.get("source_caller_agent_id") or "").strip()
        if not operator_fork_id or not target_agent_id:
            return False
        assignments = self.store.list_operator_campaign_assignments(
            target_agent_id=target_agent_id,
            limit=500,
        )
        for assignment in assignments:
            if str(assignment.get("operator_fork_id") or "") != operator_fork_id:
                continue
            if assignment.get("completed_at") is not None:
                continue
            if operator_state_is_terminal(str(assignment.get("state") or "")):
                continue
            return True
        return False

    def _review_escalation_message(
        self,
        *,
        review_fork: dict[str, Any],
        message: str,
        route: str,
    ) -> str:
        return "\n".join(
            [
                "Operator review escalation",
                "",
                f"Review fork: {review_fork.get('fork_agent_id')}",
                f"Review fork ID: {review_fork.get('operator_fork_id')}",
                f"Source caller: {review_fork.get('source_caller_agent_id')}",
                f"Source session: {review_fork.get('source_codex_session_id')}",
                f"Requested route: {route}",
                "",
                message,
                "",
                "Keep campaign state and final git state visible through Agent PBX.",
            ]
        )

    def _record_review_escalation_event(
        self,
        *,
        operator_agent_id: str,
        campaign_id: str | None,
        assignment_id: str | None,
        target_agent_id: str,
        review_fork: dict[str, Any],
        route: str,
        command: dict[str, Any],
    ) -> None:
        payload = {
            "operator_agent_id": operator_agent_id,
            "target_agent_id": target_agent_id,
            "review_fork_id": review_fork.get("operator_fork_id"),
            "review_fork_agent_id": review_fork.get("fork_agent_id"),
            "route": route,
            "command_id": command.get("command_id"),
            "command_agent_id": command.get("agent_id"),
            "command_status": command.get("status"),
            "campaign_id": campaign_id,
            "assignment_id": assignment_id,
        }
        self.store.append_event(
            "operator_review_escalation",
            payload,
            str(review_fork.get("operator_fork_id") or ""),
        )
        if campaign_id:
            self.store.add_operator_campaign_event(
                campaign_id=campaign_id,
                assignment_id=assignment_id,
                operator_agent_id=operator_agent_id,
                target_agent_id=target_agent_id,
                event_type="review_escalation_routed",
                summary=f"Review escalation routed via {route}",
                detail=payload,
                command_id=str(command.get("command_id") or "") or None,
            )

    def _agent_with_fork_delivery_metadata(
        self,
        agent: dict[str, Any],
        fork: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        fork_metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        merged_metadata = dict(metadata)
        merged_metadata.update(
            {
                "agent_type": OPERATOR_AGENT_TYPE,
                "operator_role": "fork",
                "operator_fork_id": str(fork.get("operator_fork_id") or ""),
                "logical_operator_id": str(
                    fork.get("logical_operator_agent_id") or ""
                ),
                "source_caller_agent_id": str(
                    fork.get("source_caller_agent_id") or ""
                ),
                "source_codex_session_id": str(
                    fork.get("source_codex_session_id") or ""
                ),
                "fork_track_id": str(
                    fork.get("fork_track_id") or DEFAULT_FORK_TRACK_ID
                ),
                "fork_purpose": str(
                    fork.get("fork_purpose") or DEFAULT_FORK_PURPOSE
                ),
                "access_mode": str(
                    fork.get("access_mode") or DEFAULT_FORK_ACCESS_MODE
                ),
            }
        )
        for key in (
            "source_caller_agent_id",
            "source_codex_session_id",
            "fork_track_id",
            "fork_purpose",
            "access_mode",
            "source_cwd",
            "work_root",
            "fork_codex_session_id",
            "cwd",
            "codex_home",
            "codex_host_id",
            "tmux_pane_id",
        ):
            value = fork_metadata.get(key)
            if isinstance(value, str) and value.strip():
                merged_metadata[key] = value.strip()
        for key in (
            "source_caller_agent_id",
            "source_codex_session_id",
            "fork_track_id",
            "fork_purpose",
            "access_mode",
            "source_cwd",
            "work_root",
            "fork_codex_session_id",
            "cwd",
            "codex_home",
            "codex_host_id",
            "tmux_pane_id",
        ):
            value = fork.get(key)
            if isinstance(value, str) and value.strip():
                merged_metadata[key] = value.strip()
        return {**agent, "metadata": merged_metadata}

    def _append_command_delivery_event(
        self,
        command: dict[str, Any],
        *,
        campaign_id: str,
        assignment_id: str | None,
    ) -> None:
        event_type = "command_sent" if command["status"] == "sent" else "command_queued"
        self.store.append_event(
            event_type,
            {
                "command_id": command["command_id"],
                "agent_id": command["agent_id"],
                "type": command["type"],
                "campaign_id": campaign_id,
                "assignment_id": assignment_id,
            },
            command["command_id"],
        )

    def _resolve_tmux_pane(self, agent: dict[str, Any]) -> tmux_support.TmuxPane:
        try:
            panes = tmux_support.list_panes(self.tmux_bin)
        except Exception as exc:  # noqa: BLE001 - surface actionable delivery error.
            raise ValueError(f"tmux is unavailable: {exc}") from exc
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        tmux_pane_id = str(metadata.get("tmux_pane_id") or "").strip()
        if tmux_pane_id:
            matches = [
                pane
                for pane in panes
                if pane.pane_id == tmux_pane_id or pane.target_label == tmux_pane_id
            ]
            if len(matches) == 1:
                pane = matches[0]
                if self._explicit_tmux_pane_matches_agent(pane, agent):
                    return pane
        if self._is_operator_fork_agent(agent):
            fork_panes = [
                pane
                for pane in panes
                if self._pane_matches_operator_fork_agent(pane, agent)
            ]
            if len(fork_panes) == 1:
                return fork_panes[0]
            if not fork_panes:
                raise ValueError(
                    f"no local tmux pane/window matched operator fork {agent['agent_id']}; "
                    "use the TUI to view or relaunch the fork before dispatch"
                )
            raise ValueError(
                f"multiple local tmux panes matched operator fork {agent['agent_id']}; "
                "select the intended fork pane in the TUI before dispatch"
            )
        pane = tmux_support.choose_pane_for_agent(panes, agent)
        if pane is None:
            raise ValueError(
                f"no unique local tmux pane matched {agent['agent_id']}; "
                "enable tmux direct mode or use delivery='queue' with nohup polling"
            )
        return pane

    def _explicit_tmux_pane_matches_agent(
        self,
        pane: tmux_support.TmuxPane,
        agent: dict[str, Any],
    ) -> bool:
        if self._is_operator_fork_agent(agent):
            return self._pane_matches_operator_fork_agent(pane, agent)
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        has_match_hint = bool(
            str(metadata.get("cwd") or "").strip()
            or str(agent.get("project") or "").strip()
        )
        if not has_match_hint:
            return True
        return tmux_support.pane_matches_agent(pane, agent)

    def _is_operator_fork_agent(self, agent: dict[str, Any]) -> bool:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        if str(metadata.get("operator_role") or "").strip().lower() == "fork":
            return True
        agent_id = str(agent.get("agent_id") or "").strip()
        return bool(agent_id and self.store.get_operator_fork_for_agent(agent_id))

    def _pane_matches_operator_fork_agent(
        self,
        pane: tmux_support.TmuxPane,
        agent: dict[str, Any],
    ) -> bool:
        agent_id = str(agent.get("agent_id") or "").strip()
        if not agent_id:
            return False
        if pane.session_name != self.operator_tmux_session_name():
            return False
        return pane.window_name == agent_id or pane.title == agent_id

    def _initial_assignment_message(
        self,
        *,
        campaign: dict[str, Any],
        assignment: dict[str, Any],
    ) -> str:
        criteria = assignment.get("criteria") or campaign.get("criteria") or []
        criteria_text = "\n".join(f"- {item}" for item in criteria) or "- Report clear completion evidence."
        return "\n".join(
            [
                f"Operator campaign: {campaign['title']}",
                "",
                f"Objective: {campaign['objective']}",
                "",
                f"Assignment: {assignment['title']}",
                "",
                assignment["prompt"],
                "",
                "Completion criteria:",
                criteria_text,
                "",
                "Use Agent PBX for this work. Report progress, blockers, validation, "
                "and final git state through PBX so the operator can review completion.",
            ]
        )

    def _resolve_assignment(
        self,
        campaign_id: str,
        *,
        target_agent_id: str,
        assignment_id: str | None,
    ) -> dict[str, Any]:
        if assignment_id:
            assignment = self.store.get_operator_campaign_assignment(assignment_id)
            if assignment is None or assignment["campaign_id"] != campaign_id:
                raise ValueError("assignment not found for campaign")
            if assignment["target_agent_id"] != target_agent_id:
                raise ValueError("assignment target does not match target_agent_id")
            return assignment
        matches = [
            assignment
            for assignment in self.store.list_operator_campaign_assignments(
                campaign_id=campaign_id,
                target_agent_id=target_agent_id,
            )
        ]
        if len(matches) != 1:
            raise ValueError("assignment_id is required when target has multiple assignments")
        return matches[0]

    def _require_campaign(self, campaign_id: str) -> dict[str, Any]:
        campaign = self.store.get_operator_campaign(campaign_id)
        if campaign is None:
            raise ValueError("campaign not found")
        return campaign

    def _require_agent(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError("agent not registered")
        return agent

    def _require_operator(self, agent_id: str) -> dict[str, Any]:
        agent = self._require_agent(agent_id)
        if agent.get("agent_type") != OPERATOR_AGENT_TYPE:
            raise ValueError("agent is not an operator")
        return agent

    def _require_caller(self, agent_id: str) -> dict[str, Any]:
        agent = self._require_agent(agent_id)
        if agent.get("agent_type") == OPERATOR_AGENT_TYPE:
            raise ValueError("target agent must be a caller")
        return agent

    def _logical_operator_agent_id(self, operator: dict[str, Any]) -> str:
        metadata = operator.get("metadata") if isinstance(operator.get("metadata"), dict) else {}
        if str(metadata.get("operator_role") or "").strip().lower() == "fork":
            logical = str(metadata.get("logical_operator_id") or "").strip()
            if logical:
                return logical
        fork = self.store.get_operator_fork_for_agent(str(operator.get("agent_id") or ""))
        if fork is not None:
            logical = str(fork.get("logical_operator_agent_id") or "").strip()
            if logical:
                return logical
        return str(operator["agent_id"])

    def local_codex_host_id(self) -> str:
        return os.getenv("AGENT_PBX_CODEX_HOST_ID", "").strip() or socket.gethostname()

    def operator_tmux_session_name(self) -> str:
        return (
            os.getenv(OPERATOR_TMUX_SESSION_ENV, DEFAULT_OPERATOR_TMUX_SESSION).strip()
            or DEFAULT_OPERATOR_TMUX_SESSION
        )

    def _fork_blocked_reason(
        self,
        *,
        source_session_id: str,
        source_cwd: str,
        source_host_id: str | None,
        local_host_id: str,
    ) -> str | None:
        if not source_session_id:
            return "caller metadata.codex_session_id is required before creating an operator fork"
        if not source_cwd:
            return "caller metadata.cwd is required before creating an operator fork"
        if source_host_id and source_host_id not in {local_host_id, "local", "localhost"}:
            return (
                "caller Codex session is not on the TUI/server host; "
                f"caller host={source_host_id}, local host={local_host_id}"
            )
        return None

    @staticmethod
    def normalize_fork_track_id(fork_track_id: str | None) -> str:
        normalized = ID_SAFE.sub("-", str(fork_track_id or DEFAULT_FORK_TRACK_ID))
        normalized = normalized.strip("-._").lower()
        return normalized[:80] or DEFAULT_FORK_TRACK_ID

    @staticmethod
    def normalize_fork_label(value: str | None, *, default: str) -> str:
        normalized = ID_SAFE.sub("-", str(value or default))
        normalized = normalized.strip("-._").lower()
        return normalized[:80] or default

    @staticmethod
    def default_fork_agent_id(
        logical_operator_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
        *,
        fork_track_id: str | None = None,
    ) -> str:
        resolved_track_id = OperatorService.normalize_fork_track_id(fork_track_id)
        track_suffix = (
            ""
            if resolved_track_id == DEFAULT_FORK_TRACK_ID
            else f"-{resolved_track_id}"
        )
        base = ID_SAFE.sub(
            "-",
            f"{logical_operator_agent_id}-fork-{source_caller_agent_id}{track_suffix}",
        )
        base = base.strip("-") or "operator-fork"
        digest_key = (
            f"{logical_operator_agent_id}:{source_caller_agent_id}:"
            f"{source_codex_session_id}"
        )
        if resolved_track_id != DEFAULT_FORK_TRACK_ID:
            digest_key = f"{digest_key}:{resolved_track_id}"
        digest = hashlib.sha256(
            digest_key.encode("utf-8")
        ).hexdigest()[:8]
        return f"{base[:96]}-{digest}"
