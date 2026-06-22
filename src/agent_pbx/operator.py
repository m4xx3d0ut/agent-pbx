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
TERMINAL_ASSIGNMENT_STATES = {"complete", "completed", "blocked", "failed", "canceled"}
TERMINAL_CAMPAIGN_STATUSES = {"complete", "completed", "blocked", "failed", "canceled"}
NOHUP_MODE = "nohup"
FORK_READY_STATUSES = {"starting", "running", "ready"}
ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
FORK_LAUNCH_REQUIRED_REASON = (
    "operator fork has not been launched; use the TUI to create the first fork for this caller"
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
            "Inspect caller reports and threads with pbx_operator_get_thread.",
            "Mark each assignment complete, blocked, or needing follow-up with pbx_operator_report_assignment.",
            "Finish the campaign only after every assignment is complete or explicitly blocked.",
        ],
        "delivery": [
            "Operator work is routed to the per-caller forked operator session.",
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
        source_cwd = str(caller_metadata.get("cwd") or "").strip()
        source_codex_home = str(caller_metadata.get("codex_home") or "").strip() or None
        source_host_id = str(caller_metadata.get("codex_host_id") or "").strip() or None
        local_host_id = self.local_codex_host_id()
        blocked_reason = self._fork_blocked_reason(
            source_session_id=source_session_id,
            source_cwd=source_cwd,
            source_host_id=source_host_id,
            local_host_id=local_host_id,
        )
        if source_session_id:
            existing = self.store.get_operator_fork_for_source(
                logical_operator_agent_id=logical_operator_id,
                source_caller_agent_id=source_caller_agent_id,
                source_codex_session_id=source_session_id,
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
                }
                if tmux_pane_id:
                    fork_metadata["tmux_pane_id"] = tmux_pane_id
                if fork_codex_session_id:
                    fork_metadata["fork_codex_session_id"] = fork_codex_session_id
                merged_metadata = {**fork_metadata, **(metadata or {})}
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
        )
        fork_metadata = {
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": "fork",
            "logical_operator_id": logical_operator_id,
            "source_caller_agent_id": source_caller_agent_id,
            "source_codex_session_id": source_session_id,
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
            cwd=source_cwd or str(caller.get("project") or ""),
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
        campaign_id: str,
        target_agent_id: str,
        message: str,
        assignment_id: str | None = None,
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
            },
            command_id=command["command_id"],
        )
        _ = campaign
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
        complete = state in TERMINAL_ASSIGNMENT_STATES
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
        complete = status in TERMINAL_CAMPAIGN_STATUSES
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
        return {
            "target_agent_id": target_agent_id,
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
    ) -> dict[str, Any]:
        fork = self.ensure_fork(
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=target_agent_id,
            campaign_id=campaign_id,
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

    def _agent_with_fork_delivery_metadata(
        self,
        agent: dict[str, Any],
        fork: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        fork_metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        merged_metadata = dict(metadata)
        for key in (
            "source_caller_agent_id",
            "source_codex_session_id",
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
        return str(metadata.get("operator_role") or "").strip().lower() == "fork"

    def _pane_matches_operator_fork_agent(
        self,
        pane: tmux_support.TmuxPane,
        agent: dict[str, Any],
    ) -> bool:
        agent_id = str(agent.get("agent_id") or "").strip()
        if not agent_id:
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
        return str(operator["agent_id"])

    def local_codex_host_id(self) -> str:
        return os.getenv("AGENT_PBX_CODEX_HOST_ID", "").strip() or socket.gethostname()

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
    def default_fork_agent_id(
        logical_operator_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
    ) -> str:
        base = ID_SAFE.sub("-", f"{logical_operator_agent_id}-fork-{source_caller_agent_id}")
        base = base.strip("-") or "operator-fork"
        digest = hashlib.sha256(
            f"{logical_operator_agent_id}:{source_caller_agent_id}:{source_codex_session_id}".encode(
                "utf-8"
            )
        ).hexdigest()[:8]
        return f"{base[:96]}-{digest}"
