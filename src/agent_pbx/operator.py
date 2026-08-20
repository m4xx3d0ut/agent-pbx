from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import socket
import uuid
from typing import Any

from . import tmux as tmux_support
from .project_spawn import PROJECT_SPAWN_MODES, resolve_sibling_project_paths
from .schemas import (
    AgentRegisterRequest,
    CommandCreateRequest,
    OperatorAssignmentCreate,
    ReportCreateRequest,
)
from .security import now_ts
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
KNOWLEDGE_LINK_TYPES = {
    "handoff",
    "consult",
    "domain_context",
    "review_context",
}
KNOWLEDGE_LINK_STATUSES = {
    "proposed",
    "active",
    "closed",
    "canceled",
    "cancelled",
}
KNOWLEDGE_TURN_TYPES = {
    "handoff",
    "question",
    "answer",
    "note",
}
KNOWLEDGE_TERMINAL_STATUSES = {"closed", "canceled", "cancelled"}
HANDOFF_STATUSES = {
    "proposed",
    "approved",
    "pending_launch",
    "sent",
    "acknowledged",
    "running",
    "complete",
    "completed",
    "blocked",
    "failed",
    "expired",
    "canceled",
    "cancelled",
}
HANDOFF_TERMINAL_STATUSES = {
    "complete",
    "completed",
    "blocked",
    "failed",
    "expired",
    "canceled",
    "cancelled",
}
HANDOFF_PRESTART_STATUSES = {
    "proposed",
    "approved",
    "pending_launch",
    "sent",
    "acknowledged",
}
OPERATOR_KB_STATUSES = {
    "proposed",
    "active",
    "retired",
    "rejected",
}
OPERATOR_KB_SCOPES = {"global", "project", "repo", "operator", "caller"}
OPERATOR_KB_REDACTION_STATUSES = {
    "unreviewed",
    "clean",
    "needs_review",
    "blocked",
}
OPERATOR_KB_SEED_RUN_STATUSES = {
    "requested",
    "queued",
    "sent",
    "failed",
    "complete",
    "completed",
    "canceled",
    "cancelled",
}
OPERATOR_KB_SEED_TERMINAL_STATUSES = {
    "failed",
    "complete",
    "completed",
    "canceled",
    "cancelled",
}
OPERATOR_KB_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|token|password|secret|credential|private[_-]?key)"
        r"\s*[:=]\s*['\"]?[^\s'\"`]+"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
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
            "Use operator handoffs for executable domain transfers between operators or review forks without changing fork ownership.",
            "Use manual KB seed runs to ask an operator or fork to propose durable knowledge from its current context.",
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
            "When review work needs a new sibling project, request it with pbx_operator_request_project_spawn; the TUI must approve and launch the new caller agent.",
            "When review work needs to transfer domain context to another operator, propose a knowledge handoff; the TUI/root operator approves the executable handoff delivery.",
            "Operator handoffs track required target fork launch, delivery evidence, receiver acknowledgement, running state, TTL expiry, artifact summaries, and terminal state.",
            "Manual KB seed runs deliver a seed prompt to the selected operator or fork; that session proposes KB entries and updates seed-run status when finished.",
            "Promote durable operator knowledge into the PBX-managed KB only from the root operator; forks may propose entries, update their seed-run status, and read active entries.",
            "Knowledge links and handoffs do not create fork edges, campaign assignments, or source-session ownership.",
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

    def rebind_fork_source_session(
        self,
        *,
        operator_agent_id: str,
        operator_fork_id: str,
        source_caller_agent_id: str,
        old_source_codex_session_id: str,
        new_source_codex_session_id: str,
        source_cwd: str | None = None,
        codex_host_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        caller = self._require_caller(source_caller_agent_id)
        caller_metadata = (
            caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        )
        caller_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        caller_cwd = str(caller_metadata.get("cwd") or "").strip()
        caller_host_id = str(caller_metadata.get("codex_host_id") or "").strip()
        old_session_id = str(old_source_codex_session_id or "").strip()
        new_session_id = str(new_source_codex_session_id or "").strip()
        if not old_session_id or not new_session_id:
            raise ValueError("old and new source Codex session ids are required")
        if old_session_id == new_session_id:
            raise ValueError("old and new source Codex session ids must differ")
        if not caller_session_id:
            raise ValueError("source caller metadata.codex_session_id is required")
        if caller_session_id != new_session_id:
            raise ValueError("new source Codex session id does not match caller metadata")

        fork = self.store.get_operator_fork(operator_fork_id)
        if fork is None:
            raise ValueError("operator_fork_id not found")
        if str(fork.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("operator fork belongs to a different operator")
        if str(fork.get("source_caller_agent_id") or "") != source_caller_agent_id:
            raise ValueError("operator fork belongs to a different source caller")
        if str(fork.get("source_codex_session_id") or "") != old_session_id:
            raise ValueError("operator fork is not associated with the old source session")

        fork_metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        fork_source_cwd = str(
            fork.get("source_cwd") or fork_metadata.get("source_cwd") or ""
        ).strip()
        requested_source_cwd = str(source_cwd or caller_cwd or "").strip()
        if (
            requested_source_cwd
            and fork_source_cwd
            and requested_source_cwd != fork_source_cwd
        ):
            raise ValueError("source cwd does not match operator fork association")
        resolved_source_cwd = requested_source_cwd or fork_source_cwd or None

        fork_host_id = str(
            fork.get("codex_host_id") or fork_metadata.get("codex_host_id") or ""
        ).strip()
        requested_host_id = str(codex_host_id or caller_host_id or "").strip()
        if (
            requested_host_id
            and fork_host_id
            and requested_host_id != fork_host_id
        ):
            raise ValueError("Codex host id does not match operator fork association")
        resolved_host_id = requested_host_id or fork_host_id or None

        fork_track_id = self.normalize_fork_track_id(
            str(fork.get("fork_track_id") or DEFAULT_FORK_TRACK_ID)
        )
        collision = self.store.get_operator_fork_for_source(
            logical_operator_agent_id=logical_operator_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=new_session_id,
            fork_track_id=fork_track_id,
        )
        if (
            collision is not None
            and collision["operator_fork_id"] != operator_fork_id
        ):
            raise ValueError("current source session already has this fork track")

        previous_sessions = fork_metadata.get("previous_source_codex_session_ids")
        if not isinstance(previous_sessions, list):
            previous_sessions = []
        previous_session_values = [
            str(item)
            for item in previous_sessions
            if isinstance(item, str) and item.strip()
        ]
        if old_session_id not in previous_session_values:
            previous_session_values.append(old_session_id)
        rebind_metadata: dict[str, Any] = {
            **(metadata or {}),
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": "fork",
            "logical_operator_id": logical_operator_id,
            "source_caller_agent_id": source_caller_agent_id,
            "source_codex_session_id": new_session_id,
            "fork_track_id": fork_track_id,
            "fork_purpose": str(fork.get("fork_purpose") or DEFAULT_FORK_PURPOSE),
            "access_mode": str(fork.get("access_mode") or DEFAULT_FORK_ACCESS_MODE),
            "source_cwd": resolved_source_cwd or "",
            "previous_source_codex_session_ids": previous_session_values,
            "rebound_from_source_codex_session_id": old_session_id,
            "source_session_rebound_at": now_ts(),
        }
        if resolved_host_id:
            rebind_metadata["codex_host_id"] = resolved_host_id
        try:
            updated = self.store.update_operator_fork_source_session(
                operator_fork_id,
                old_source_codex_session_id=old_session_id,
                new_source_codex_session_id=new_session_id,
                source_cwd=resolved_source_cwd,
                codex_host_id=resolved_host_id,
                metadata=rebind_metadata,
                touch=True,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "current source session already has this fork track"
            ) from exc
        if updated is None:
            raise RuntimeError("operator fork source session rebind failed")

        fork_agent_id = str(updated.get("fork_agent_id") or "")
        existing_agent = self.store.get_agent(fork_agent_id)
        if existing_agent is not None:
            existing_metadata = (
                existing_agent.get("metadata")
                if isinstance(existing_agent.get("metadata"), dict)
                else {}
            )
            self.store.register_agent(
                AgentRegisterRequest(
                    agent_id=fork_agent_id,
                    project=str(
                        existing_agent.get("project")
                        or operator.get("project")
                        or "agent-pbx-operator"
                    ),
                    name=str(existing_agent.get("name") or fork_agent_id),
                    agent_type=OPERATOR_AGENT_TYPE,
                    metadata={**existing_metadata, **rebind_metadata},
                    pbx_active=bool(existing_agent.get("pbx_active", True)),
                )
            )

        self.store.append_event(
            "operator_fork_source_session_rebound",
            {
                "operator_fork_id": operator_fork_id,
                "logical_operator_agent_id": logical_operator_id,
                "source_caller_agent_id": source_caller_agent_id,
                "fork_agent_id": fork_agent_id,
                "fork_track_id": fork_track_id,
                "old_source_codex_session_id": old_session_id,
                "new_source_codex_session_id": new_session_id,
                "reason": str(reason or "").strip(),
            },
            operator_fork_id,
        )
        return updated

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

    def create_knowledge_link(
        self,
        *,
        operator_agent_id: str,
        source_agent_id: str,
        target_agent_id: str,
        link_type: str = "domain_context",
        status: str = "active",
        source_operator_fork_id: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        self._require_agent(source_agent_id)
        self._require_agent(target_agent_id)
        normalized_type = self._normalize_knowledge_link_type(link_type)
        normalized_status = self._normalize_knowledge_link_status(status)
        fork_id = self._validated_knowledge_source_fork_id(
            logical_operator_id=logical_operator_id,
            source_agent_id=source_agent_id,
            source_operator_fork_id=source_operator_fork_id,
        )
        link = self.store.create_operator_knowledge_link(
            logical_operator_agent_id=logical_operator_id,
            operator_agent_id=operator_agent_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            source_operator_fork_id=fork_id,
            link_type=normalized_type,
            status=normalized_status,
            summary=summary,
            metadata=metadata or {},
        )
        self.store.append_event(
            "operator_knowledge_link_created",
            {
                "link_id": link["link_id"],
                "operator_agent_id": operator_agent_id,
                "logical_operator_agent_id": logical_operator_id,
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
                "source_operator_fork_id": fork_id,
                "link_type": normalized_type,
                "status": normalized_status,
            },
            link["link_id"],
        )
        return link

    def propose_knowledge_handoff(
        self,
        *,
        operator_agent_id: str,
        source_agent_id: str,
        target_agent_id: str,
        message: str,
        link_type: str = "domain_context",
        source_operator_fork_id: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        link = self.create_knowledge_link(
            operator_agent_id=operator_agent_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            source_operator_fork_id=source_operator_fork_id,
            link_type=link_type,
            status="proposed",
            summary=summary or "Knowledge handoff proposed",
            metadata={**(metadata or {}), "requires_operator_delivery": True},
        )
        turn = self.store.create_operator_knowledge_turn(
            link_id=link["link_id"],
            sender_agent_id=source_agent_id,
            recipient_agent_id=target_agent_id,
            turn_type="handoff",
            message=self._knowledge_turn_message(
                link=link,
                message=message,
                turn_type="handoff",
            ),
            delivery_status="pending_approval",
            metadata={
                **(metadata or {}),
                "proposed_by_operator_agent_id": operator_agent_id,
                "requires_operator_delivery": True,
            },
        )
        self.store.append_event(
            "operator_knowledge_handoff_proposed",
            {
                "link_id": link["link_id"],
                "turn_id": turn["turn_id"],
                "operator_agent_id": operator_agent_id,
                "source_agent_id": source_agent_id,
                "target_agent_id": target_agent_id,
                "source_operator_fork_id": link.get("source_operator_fork_id"),
            },
            link["link_id"],
        )
        handoff = self.create_handoff(
            operator_agent_id=operator_agent_id,
            source_agent_id=source_agent_id,
            target_operator_agent_id=target_agent_id,
            message=message,
            objective=str((metadata or {}).get("objective") or summary or message).strip(),
            source_operator_fork_id=link.get("source_operator_fork_id"),
            target_caller_agent_id=str(
                (metadata or {}).get("target_caller_agent_id") or ""
            ).strip()
            or None,
            target_operator_fork_id=str(
                (metadata or {}).get("target_operator_fork_id") or ""
            ).strip()
            or None,
            knowledge_link_id=link["link_id"],
            knowledge_turn_id=turn["turn_id"],
            allowed_mutation_scope=(
                str((metadata or {}).get("allowed_mutation_scope") or "").strip()
                or None
            ),
            required_artifacts=(
                (metadata or {}).get("required_artifacts")
                if isinstance((metadata or {}).get("required_artifacts"), list)
                else []
            ),
            artifact_bundle=(
                (metadata or {}).get("artifact_bundle")
                if isinstance((metadata or {}).get("artifact_bundle"), list)
                else []
            ),
            expires_at=self._float_or_none((metadata or {}).get("expires_at")),
            needs_ack=bool((metadata or {}).get("needs_ack", True)),
            summary=summary or "Knowledge handoff proposed",
            metadata={**(metadata or {}), "created_from_knowledge_proposal": True},
        )
        refreshed = self.store.get_operator_knowledge_link(link["link_id"]) or link
        return {"link": refreshed, "turn": turn, "handoff": handoff}

    def list_knowledge_links(
        self,
        *,
        operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        logical_operator_id = None
        if operator_agent_id:
            logical_operator_id = self._logical_operator_agent_id(
                self._require_operator(operator_agent_id)
            )
        return self.store.list_operator_knowledge_links(
            logical_operator_agent_id=logical_operator_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            status=status,
            limit=limit,
        )

    def knowledge_context(
        self,
        *,
        link_id: str,
        operator_agent_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        link = self._require_knowledge_link(link_id)
        if operator_agent_id:
            operator = self._require_operator(operator_agent_id)
            logical_operator_id = self._logical_operator_agent_id(operator)
            if str(link.get("logical_operator_agent_id") or "") != logical_operator_id:
                raise ValueError("knowledge link belongs to a different operator")
        turns = self.store.list_operator_knowledge_turns(
            link_id=link_id,
            limit=limit,
        )
        return {"link": link, "turns": turns}

    def send_knowledge_turn(
        self,
        *,
        operator_agent_id: str,
        link_id: str,
        sender_agent_id: str,
        recipient_agent_id: str,
        message: str,
        turn_type: str = "note",
        delivery: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        link = self._require_knowledge_link_for_operator(link_id, logical_operator_id)
        if str(link.get("status") or "") in KNOWLEDGE_TERMINAL_STATUSES:
            raise ValueError("knowledge link is closed")
        self._require_agent(sender_agent_id)
        self._require_agent(recipient_agent_id)
        self._validate_knowledge_participant(link, sender_agent_id)
        self._validate_knowledge_participant(link, recipient_agent_id)
        normalized_type = self._normalize_knowledge_turn_type(turn_type)
        if str(link.get("status") or "") == "proposed":
            link = self.store.update_operator_knowledge_link(link_id, status="active") or link
        turn = self.store.create_operator_knowledge_turn(
            link_id=link_id,
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            turn_type=normalized_type,
            message=self._knowledge_turn_message(
                link=link,
                message=message,
                turn_type=normalized_type,
            ),
            delivery_status="recorded",
            metadata=metadata or {},
        )
        delivered = self._deliver_knowledge_turn(
            turn,
            operator_agent_id=operator_agent_id,
            link=link,
            delivery=delivery,
            metadata=metadata or {},
        )
        refreshed_link = self.store.get_operator_knowledge_link(link_id) or link
        return {
            "link": refreshed_link,
            "turn": delivered["turn"],
            "command": delivered.get("command"),
        }

    def approve_knowledge_turn(
        self,
        *,
        operator_agent_id: str,
        link_id: str,
        turn_id: str,
        delivery: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        link = self._require_knowledge_link_for_operator(link_id, logical_operator_id)
        if str(link.get("status") or "") in KNOWLEDGE_TERMINAL_STATUSES:
            raise ValueError("knowledge link is closed")
        turn = self.store.get_operator_knowledge_turn(turn_id)
        if turn is None or str(turn.get("link_id") or "") != link_id:
            raise ValueError("knowledge turn not found for link")
        if str(turn.get("delivery_status") or "") != "pending_approval":
            raise ValueError("knowledge turn is not pending approval")
        link = self.store.update_operator_knowledge_link(link_id, status="active") or link
        delivered = self._deliver_knowledge_turn(
            turn,
            operator_agent_id=operator_agent_id,
            link=link,
            delivery=delivery,
            metadata={
                **(metadata or {}),
                "approved_by_operator_agent_id": operator_agent_id,
            },
        )
        refreshed_link = self.store.get_operator_knowledge_link(link_id) or link
        return {
            "link": refreshed_link,
            "turn": delivered["turn"],
            "command": delivered.get("command"),
        }

    def close_knowledge_link(
        self,
        *,
        operator_agent_id: str,
        link_id: str,
        status: str = "closed",
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        self._require_knowledge_link_for_operator(link_id, logical_operator_id)
        normalized_status = self._normalize_knowledge_link_status(status)
        if normalized_status not in KNOWLEDGE_TERMINAL_STATUSES:
            raise ValueError("knowledge link close status must be closed, canceled, or cancelled")
        link = self.store.update_operator_knowledge_link(
            link_id,
            status=normalized_status,
            summary=summary,
            metadata=metadata or {},
        )
        if link is None:
            raise RuntimeError("knowledge link close failed")
        self.store.append_event(
            "operator_knowledge_link_closed",
            {
                "link_id": link_id,
                "operator_agent_id": operator_agent_id,
                "logical_operator_agent_id": logical_operator_id,
                "status": normalized_status,
                "summary": summary,
            },
            link_id,
        )
        return link

    def propose_kb_entry(
        self,
        *,
        operator_agent_id: str,
        title: str,
        summary: str,
        body: str,
        scope: str = "project",
        project: str | None = None,
        repo_root: str | None = None,
        git_remote: str | None = None,
        branch: str | None = None,
        tags: list[str] | None = None,
        source_knowledge_link_id: str | None = None,
        source_handoff_id: str | None = None,
        source_turn_ids: list[str] | None = None,
        stale_after: float | None = None,
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        metadata_payload = metadata or {}
        normalized_turn_ids = self._require_operator_kb_source_context(
            logical_operator_id=logical_operator_id,
            source_knowledge_link_id=source_knowledge_link_id,
            source_handoff_id=source_handoff_id,
            source_turn_ids=source_turn_ids or [],
        )
        seed_sources: list[dict[str, Any]] = []
        seed_run_id = str(metadata_payload.get("seed_run_id") or "").strip()
        if seed_run_id:
            seed_run = self._require_operator_kb_seed_run_usable(
                seed_run_id,
                logical_operator_id,
                operator_agent_id,
            )
            seed_sources.append(
                {
                    "source_type": "kb_seed_run",
                    "source_id": seed_run_id,
                    "metadata": {
                        "seed_type": seed_run.get("seed_type"),
                        "seed_sync_key": metadata_payload.get("seed_sync_key"),
                    },
                }
            )
        redaction_status = self._operator_kb_redaction_status(
            title=title,
            summary=summary,
            body=body,
            metadata=metadata_payload,
        )
        entry = self.store.create_operator_kb_entry(
            scope=scope,
            project=project,
            repo_root=repo_root,
            git_remote=git_remote,
            branch=branch,
            title=title,
            summary=summary,
            body=body,
            tags=tags or [],
            status="proposed",
            redaction_status=redaction_status,
            created_by_operator_agent_id=logical_operator_id,
            created_by_agent_id=operator_agent_id,
            source_knowledge_link_id=source_knowledge_link_id,
            source_handoff_id=source_handoff_id,
            source_turn_ids=normalized_turn_ids,
            stale_after=stale_after,
            expires_at=expires_at,
            metadata={
                **metadata_payload,
                "proposed_by_operator_agent_id": operator_agent_id,
            },
            sources=seed_sources,
        )
        self._record_kb_event(
            "operator_kb_proposed",
            entry,
            operator_agent_id=operator_agent_id,
            summary=summary,
        )
        return entry

    def propose_kb_from_link(
        self,
        *,
        operator_agent_id: str,
        link_id: str,
        title: str,
        summary: str | None = None,
        scope: str = "project",
        project: str | None = None,
        repo_root: str | None = None,
        git_remote: str | None = None,
        branch: str | None = None,
        tags: list[str] | None = None,
        include_turn_ids: list[str] | None = None,
        stale_after: float | None = None,
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        link = self._require_knowledge_link_visible_to_operator(
            link_id,
            logical_operator_id,
        )
        turns = self.store.list_operator_knowledge_turns(link_id=link_id, limit=500)
        requested_turn_ids = {
            str(turn_id or "").strip()
            for turn_id in (include_turn_ids or [])
            if str(turn_id or "").strip()
        }
        if requested_turn_ids:
            available_turn_ids = {str(turn.get("turn_id") or "") for turn in turns}
            missing_turn_ids = sorted(requested_turn_ids - available_turn_ids)
            if missing_turn_ids:
                raise ValueError(
                    "knowledge turn not found for link: "
                    + ", ".join(missing_turn_ids)
                )
            turns = [
                turn
                for turn in turns
                if str(turn.get("turn_id") or "") in requested_turn_ids
            ]
        source_agent = self.store.get_agent(str(link.get("source_agent_id") or ""))
        default_project = (
            str(source_agent.get("project") or "").strip()
            if source_agent is not None
            else ""
        )
        return self.propose_kb_entry(
            operator_agent_id=operator_agent_id,
            scope=scope,
            project=project or default_project or None,
            repo_root=repo_root,
            git_remote=git_remote,
            branch=branch,
            title=title,
            summary=summary or str(link.get("summary") or title),
            body=self._operator_kb_body_from_link(link, turns),
            tags=tags or [],
            source_knowledge_link_id=link_id,
            source_turn_ids=[
                str(turn.get("turn_id") or "")
                for turn in turns
                if str(turn.get("turn_id") or "").strip()
            ],
            stale_after=stale_after,
            expires_at=expires_at,
            metadata={
                **(metadata or {}),
                "created_from_knowledge_link": True,
            },
        )

    def search_kb_entries(
        self,
        *,
        operator_agent_id: str,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: list[str] | None = None,
        include_expired: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        normalized_status = (
            self._normalize_operator_kb_status(status)
            if status is not None
            else None
        )
        entries = self.store.search_operator_kb_entries(
            query=query,
            scope=scope,
            project=project,
            repo_root=repo_root,
            status=normalized_status,
            tags=tags or [],
            include_expired=include_expired,
            limit=limit,
        )
        return [
            entry
            for entry in entries
            if self._operator_can_read_kb_entry(entry, logical_operator_id)
        ]

    def get_kb_entry(
        self,
        *,
        operator_agent_id: str,
        kb_id: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        entry = self._require_operator_kb_entry(kb_id)
        if not self._operator_can_read_kb_entry(entry, logical_operator_id):
            raise ValueError("KB entry belongs to a different operator")
        return entry

    def update_kb_entry(
        self,
        *,
        operator_agent_id: str,
        kb_id: str,
        updates: dict[str, Any],
        redaction_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        entry = self._require_operator_kb_entry(kb_id)
        self._require_operator_kb_owner(entry, logical_operator_id)
        allowed_update_keys = {
            "scope",
            "project",
            "repo_root",
            "git_remote",
            "branch",
            "title",
            "summary",
            "body",
            "tags",
            "stale_after",
            "expires_at",
        }
        normalized_updates = {
            key: value
            for key, value in updates.items()
            if key in allowed_update_keys
        }
        if not normalized_updates and redaction_status is None and not metadata:
            raise ValueError("KB update has no fields to change")
        merged_metadata = {
            **(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
            **(metadata or {}),
        }
        scanned_redaction = self._operator_kb_redaction_status(
            title=str(normalized_updates.get("title", entry.get("title") or "")),
            summary=str(
                normalized_updates.get("summary", entry.get("summary") or "")
            ),
            body=str(normalized_updates.get("body", entry.get("body") or "")),
            metadata=merged_metadata,
        )
        requested_redaction = (
            self._normalize_operator_kb_redaction_status(redaction_status)
            if redaction_status is not None
            else scanned_redaction
        )
        if scanned_redaction != "clean" and requested_redaction == "clean" and not bool(
            (metadata or {}).get("redaction_override")
        ):
            requested_redaction = "needs_review"
        if str(entry.get("status") or "") == "active" and requested_redaction != "clean":
            raise ValueError(
                "active KB updates must be clean or manually redacted before saving"
            )
        updated = self.store.update_operator_kb_entry(
            kb_id,
            updates=normalized_updates,
            redaction_status=requested_redaction,
            metadata={
                **(metadata or {}),
                "updated_by_operator_agent_id": operator_agent_id,
            },
            event_type="operator_kb_updated",
            operator_agent_id=operator_agent_id,
            event_summary="KB entry updated",
        )
        if updated is None:
            raise RuntimeError("KB entry update failed")
        self._record_kb_event(
            "operator_kb_updated",
            updated,
            operator_agent_id=operator_agent_id,
            summary="KB entry updated",
        )
        return updated

    def promote_kb_entry(
        self,
        *,
        operator_agent_id: str,
        kb_id: str,
        redaction_status: str = "clean",
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        entry = self._require_operator_kb_entry(kb_id)
        self._require_operator_kb_owner(entry, logical_operator_id)
        normalized_redaction = self._normalize_operator_kb_redaction_status(
            redaction_status
        )
        if normalized_redaction != "clean":
            raise ValueError("KB entry must have clean redaction status before promotion")
        scanned_redaction = self._operator_kb_redaction_status(
            title=str(entry.get("title") or ""),
            summary=summary or str(entry.get("summary") or ""),
            body=str(entry.get("body") or ""),
            metadata={
                **(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
                **(metadata or {}),
            },
        )
        if scanned_redaction != "clean" and not bool(
            (metadata or {}).get("redaction_override")
        ):
            raise ValueError(
                "KB entry appears to contain secrets; review/redact before promotion "
                "or set metadata.redaction_override after manual review"
            )
        updated = self.store.update_operator_kb_entry(
            kb_id,
            status="active",
            redaction_status=normalized_redaction,
            summary=summary,
            metadata={
                **(metadata or {}),
                "promoted_by_operator_agent_id": operator_agent_id,
            },
            event_type="operator_kb_promoted",
            operator_agent_id=operator_agent_id,
            event_summary=summary or "KB entry promoted",
        )
        if updated is None:
            raise RuntimeError("KB entry promotion failed")
        self._record_kb_event(
            "operator_kb_promoted",
            updated,
            operator_agent_id=operator_agent_id,
            summary=summary or "KB entry promoted",
        )
        return updated

    def reject_kb_entry(
        self,
        *,
        operator_agent_id: str,
        kb_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        entry = self._require_operator_kb_entry(kb_id)
        self._require_operator_kb_owner(entry, logical_operator_id)
        if str(entry.get("status") or "") == "active":
            raise ValueError("active KB entries must be retired, not rejected")
        updated = self.store.update_operator_kb_entry(
            kb_id,
            status="rejected",
            summary=summary,
            metadata={
                **(metadata or {}),
                "rejected_by_operator_agent_id": operator_agent_id,
            },
            event_type="operator_kb_rejected",
            operator_agent_id=operator_agent_id,
            event_summary=summary,
        )
        if updated is None:
            raise RuntimeError("KB entry rejection failed")
        self._record_kb_event(
            "operator_kb_rejected",
            updated,
            operator_agent_id=operator_agent_id,
            summary=summary,
        )
        return updated

    def retire_kb_entry(
        self,
        *,
        operator_agent_id: str,
        kb_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        entry = self._require_operator_kb_entry(kb_id)
        self._require_operator_kb_owner(entry, logical_operator_id)
        updated = self.store.update_operator_kb_entry(
            kb_id,
            status="retired",
            summary=summary,
            metadata={
                **(metadata or {}),
                "retired_by_operator_agent_id": operator_agent_id,
            },
            event_type="operator_kb_retired",
            operator_agent_id=operator_agent_id,
            event_summary=summary,
        )
        if updated is None:
            raise RuntimeError("KB entry retirement failed")
        self._record_kb_event(
            "operator_kb_retired",
            updated,
            operator_agent_id=operator_agent_id,
            summary=summary,
        )
        return updated

    def export_kb_entries(
        self,
        *,
        operator_agent_id: str,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: list[str] | None = None,
        include_expired: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        normalized_status = (
            self._normalize_operator_kb_status(status)
            if status is not None
            else None
        )
        bundle = self.store.export_operator_kb_entries(
            query=query,
            scope=scope,
            project=project,
            repo_root=repo_root,
            status=normalized_status,
            tags=tags or [],
            include_expired=include_expired,
            limit=limit,
        )
        entries = [
            entry
            for entry in bundle.get("entries", [])
            if isinstance(entry, dict)
            and self._operator_can_read_kb_entry(entry, logical_operator_id)
        ]
        bundle["entries"] = entries
        criteria = bundle.get("criteria") if isinstance(bundle.get("criteria"), dict) else {}
        criteria["operator_agent_id"] = operator_agent_id
        bundle["criteria"] = criteria
        return bundle

    def import_kb_entries(
        self,
        *,
        operator_agent_id: str,
        bundle: dict[str, Any],
        import_status: str = "proposed",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_root_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        normalized_status = self._normalize_operator_kb_status(import_status)
        prepared_bundle = dict(bundle)
        security_skipped: list[dict[str, Any]] = []
        entries = prepared_bundle.get("entries")
        if isinstance(entries, list):
            prepared_entries: list[Any] = []
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    prepared_entries.append(item)
                    continue
                item_redaction = self._operator_kb_redaction_status(
                    title=str(item.get("title") or ""),
                    summary=str(item.get("summary") or ""),
                    body=str(item.get("body") or ""),
                    metadata={
                        **(
                            item.get("metadata")
                            if isinstance(item.get("metadata"), dict)
                            else {}
                        ),
                        **(metadata or {}),
                    },
                )
                if normalized_status == "active" and item_redaction != "clean" and not bool(
                    (metadata or {}).get("redaction_override")
                ):
                    security_skipped.append(
                        {
                            "index": index,
                            "reason": "entry appears to contain secrets",
                        }
                    )
                    continue
                prepared_item = dict(item)
                prepared_item["redaction_status"] = (
                    "clean" if normalized_status == "active" else item_redaction
                )
                prepared_entries.append(prepared_item)
            prepared_bundle["entries"] = prepared_entries
        result = self.store.import_operator_kb_entries(
            bundle=prepared_bundle,
            operator_agent_id=logical_operator_id,
            import_status=normalized_status,
            metadata={
                **(metadata or {}),
                "imported_by_operator_agent_id": operator_agent_id,
            },
        )
        if security_skipped:
            skipped = [*security_skipped, *(result.get("skipped") or [])]
            result["skipped"] = skipped
            result["skipped_count"] = len(skipped)
        self.store.append_event(
            "operator_kb_imported_bundle",
            {
                "operator_agent_id": operator_agent_id,
                "logical_operator_agent_id": logical_operator_id,
                "import_status": normalized_status,
                "imported_count": result.get("imported_count"),
                "skipped_count": result.get("skipped_count"),
            },
            operator_agent_id,
        )
        return result

    def start_kb_seed_run(
        self,
        *,
        operator_agent_id: str,
        seed_type: str = "operator_self_seed",
        scope: str = "project",
        project: str | None = None,
        repo_root: str | None = None,
        git_remote: str | None = None,
        branch: str | None = None,
        prompt: str | None = None,
        delivery: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        defaults = self._operator_kb_seed_defaults(operator)
        resolved_seed_type = str(seed_type or "operator_self_seed").strip().lower()
        resolved_scope = self._normalize_operator_kb_scope(scope)
        resolved_project = project or defaults.get("project")
        resolved_repo_root = repo_root or defaults.get("repo_root")
        resolved_git_remote = git_remote or defaults.get("git_remote")
        resolved_branch = branch or defaults.get("branch")
        seed_run_id = str(uuid.uuid4())
        base_metadata = {
            **defaults,
            **(metadata or {}),
            "seed_run_id": seed_run_id,
            "seed_type": resolved_seed_type,
            "seed_scope": resolved_scope,
            "source_operator_agent_id": operator_agent_id,
            "logical_operator_id": logical_operator_id,
            "extraction_version": "operator_kb_seed_v1",
        }
        sync_key = self._operator_kb_seed_sync_key(
            logical_operator_id=logical_operator_id,
            seed_type=resolved_seed_type,
            scope=resolved_scope,
            project=resolved_project,
            repo_root=resolved_repo_root,
            git_remote=resolved_git_remote,
            branch=resolved_branch,
        )
        base_metadata["seed_sync_key"] = sync_key
        rendered_prompt = str(prompt or "").strip() or self.render_kb_seed_prompt(
            operator_agent_id=operator_agent_id,
            logical_operator_id=logical_operator_id,
            seed_run_id=seed_run_id,
            seed_type=resolved_seed_type,
            scope=resolved_scope,
            project=resolved_project,
            repo_root=resolved_repo_root,
            git_remote=resolved_git_remote,
            branch=resolved_branch,
            seed_sync_key=sync_key,
        )
        base_metadata["content_fingerprint"] = hashlib.sha256(
            rendered_prompt.encode("utf-8")
        ).hexdigest()
        seed_run = self.store.create_operator_kb_seed_run(
            seed_run_id=seed_run_id,
            logical_operator_agent_id=logical_operator_id,
            source_operator_agent_id=operator_agent_id,
            seed_type=resolved_seed_type,
            scope=resolved_scope,
            project=resolved_project,
            repo_root=resolved_repo_root,
            git_remote=resolved_git_remote,
            branch=resolved_branch,
            prompt=rendered_prompt,
            metadata=base_metadata,
        )
        self._record_kb_seed_event(
            "operator_kb_seed_requested",
            seed_run,
            summary="Operator KB seed run requested",
        )
        try:
            command = self._deliver_operator_seed_run(
                seed_run=seed_run,
                delivery=delivery,
            )
        except Exception as exc:
            updated = self.store.update_operator_kb_seed_run(
                seed_run_id,
                status="failed",
                error=str(exc),
                metadata={"delivery": delivery},
            )
            if updated is None:
                raise RuntimeError("KB seed run failure update failed") from exc
            self._record_kb_seed_event(
                "operator_kb_seed_failed",
                updated,
                summary="Operator KB seed delivery failed",
            )
            return {"seed_run": updated, "command": None}
        evidence = self._seed_delivery_evidence(command)
        updated = self.store.update_operator_kb_seed_run(
            seed_run_id,
            status=str(command.get("status") or "queued"),
            command_id=str(command.get("command_id") or ""),
            tmux_pane_id=str(evidence.get("tmux_pane_id") or "") or None,
            delivery_status=str(command.get("status") or ""),
            metadata={"delivery": delivery, "delivery_evidence": evidence},
        )
        if updated is None:
            raise RuntimeError("KB seed run delivery update failed")
        self._record_kb_seed_event(
            "operator_kb_seed_sent"
            if str(command.get("status") or "") == "sent"
            else "operator_kb_seed_queued",
            updated,
            summary="Operator KB seed prompt delivered",
        )
        return {"seed_run": updated, "command": command}

    def list_kb_seed_runs(
        self,
        *,
        operator_agent_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        normalized_status = (
            self._normalize_operator_kb_seed_run_status(status)
            if status is not None
            else None
        )
        return self.store.list_operator_kb_seed_runs(
            logical_operator_agent_id=logical_operator_id,
            status=normalized_status,
            limit=limit,
        )

    def get_kb_seed_run(
        self,
        *,
        operator_agent_id: str,
        seed_run_id: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        return self._require_operator_kb_seed_run_visible(
            seed_run_id,
            logical_operator_id,
        )

    def update_kb_seed_run(
        self,
        *,
        operator_agent_id: str,
        seed_run_id: str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        self._require_operator_kb_seed_run_usable(
            seed_run_id,
            logical_operator_id,
            operator_agent_id,
        )
        normalized_status = self._normalize_operator_kb_seed_run_status(status)
        updated = self.store.update_operator_kb_seed_run(
            seed_run_id,
            status=normalized_status,
            error=error,
            metadata={
                **(metadata or {}),
                "updated_by_operator_agent_id": operator_agent_id,
                "summary": summary,
            },
        )
        if updated is None:
            raise RuntimeError("KB seed run update failed")
        self._record_kb_seed_event(
            f"operator_kb_seed_{normalized_status}",
            updated,
            summary=summary or f"Operator KB seed run {normalized_status}",
        )
        return updated

    def create_handoff(
        self,
        *,
        operator_agent_id: str,
        source_agent_id: str,
        target_operator_agent_id: str,
        message: str,
        objective: str | None = None,
        source_operator_fork_id: str | None = None,
        target_caller_agent_id: str | None = None,
        target_operator_fork_id: str | None = None,
        knowledge_link_id: str | None = None,
        knowledge_turn_id: str | None = None,
        allowed_mutation_scope: str | None = None,
        required_artifacts: list[Any] | None = None,
        artifact_bundle: list[Any] | None = None,
        expires_at: float | None = None,
        needs_ack: bool = True,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        self._require_agent(source_agent_id)
        target_operator = self._require_operator(target_operator_agent_id)
        target_logical_operator_id = self._logical_operator_agent_id(target_operator)
        target_caller = (
            self._require_caller(target_caller_agent_id)
            if target_caller_agent_id
            else None
        )
        source_fork_id = self._validated_knowledge_source_fork_id(
            logical_operator_id=logical_operator_id,
            source_agent_id=source_agent_id,
            source_operator_fork_id=source_operator_fork_id,
        )
        target_fork_id = self._validated_handoff_target_fork_id(
            target_logical_operator_id=target_logical_operator_id,
            target_caller_agent_id=target_caller_agent_id,
            target_operator_fork_id=target_operator_fork_id,
        )
        if knowledge_link_id and self.store.get_operator_knowledge_link(knowledge_link_id) is None:
            raise ValueError("knowledge_link_id not found")
        if knowledge_turn_id and self.store.get_operator_knowledge_turn(knowledge_turn_id) is None:
            raise ValueError("knowledge_turn_id not found")
        normalized_message = str(message or "").strip()
        if not normalized_message:
            raise ValueError("handoff message is required")
        normalized_objective = str(objective or summary or normalized_message).strip()
        handoff = self.store.create_operator_handoff(
            logical_operator_agent_id=logical_operator_id,
            source_operator_agent_id=operator_agent_id,
            target_operator_agent_id=target_logical_operator_id,
            source_agent_id=source_agent_id,
            source_operator_fork_id=source_fork_id,
            target_caller_agent_id=(
                str(target_caller["agent_id"]) if target_caller is not None else None
            ),
            target_operator_fork_id=target_fork_id,
            knowledge_link_id=knowledge_link_id,
            knowledge_turn_id=knowledge_turn_id,
            objective=normalized_objective,
            message=normalized_message,
            allowed_mutation_scope=allowed_mutation_scope,
            required_artifacts=required_artifacts or [],
            artifact_bundle=artifact_bundle or [],
            status="proposed",
            needs_ack=needs_ack,
            expires_at=expires_at,
            summary=summary or normalized_objective,
            metadata=metadata or {},
        )
        self._record_handoff_event(
            "operator_handoff_created",
            handoff,
            summary=handoff.get("summary") or "Operator handoff created",
        )
        return handoff

    def list_handoffs(
        self,
        *,
        operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_operator_agent_id: str | None = None,
        target_caller_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        logical_operator_id = None
        if operator_agent_id:
            logical_operator_id = self._logical_operator_agent_id(
                self._require_operator(operator_agent_id)
            )
        target_logical_operator_id = None
        if target_operator_agent_id:
            target_logical_operator_id = self._logical_operator_agent_id(
                self._require_operator(target_operator_agent_id)
            )
        handoffs = self.store.list_operator_handoffs(
            logical_operator_agent_id=logical_operator_id,
            source_agent_id=source_agent_id,
            target_operator_agent_id=target_logical_operator_id,
            target_caller_agent_id=target_caller_agent_id,
            status=status,
            limit=limit,
        )
        return [self._expire_handoff_if_due(handoff) for handoff in handoffs]

    def get_handoff(
        self,
        *,
        handoff_id: str,
        operator_agent_id: str | None = None,
    ) -> dict[str, Any]:
        handoff = self._require_handoff(handoff_id)
        if operator_agent_id:
            operator = self._require_operator(operator_agent_id)
            logical_operator_id = self._logical_operator_agent_id(operator)
            if logical_operator_id not in {
                str(handoff.get("logical_operator_agent_id") or ""),
                str(handoff.get("target_operator_agent_id") or ""),
            }:
                raise ValueError("handoff belongs to a different operator")
        return self._expire_handoff_if_due(handoff)

    def approve_handoff(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
        delivery: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handoff = self._require_handoff_for_source_operator(
            operator_agent_id=operator_agent_id,
            handoff_id=handoff_id,
        )
        handoff = self._expire_handoff_if_due(handoff)
        if str(handoff.get("status") or "") == "expired":
            raise ValueError("handoff has expired")
        if str(handoff.get("status") or "") in HANDOFF_TERMINAL_STATUSES:
            raise ValueError("handoff is already terminal")
        target_fork = self._handoff_required_target_fork(handoff)
        if target_fork is not None and not self._handoff_target_fork_ready(target_fork):
            updated = self.store.update_operator_handoff(
                str(handoff["handoff_id"]),
                status="pending_launch",
                target_operator_fork_id=str(target_fork.get("operator_fork_id") or ""),
                summary="Required target operator fork is not launched.",
                metadata={
                    **(metadata or {}),
                    "pending_launch_reason": target_fork.get("summary")
                    or target_fork.get("status"),
                    "target_fork_agent_id": target_fork.get("fork_agent_id"),
                },
            )
            if updated is None:
                raise RuntimeError("handoff pending-launch update failed")
            self._record_handoff_event(
                "operator_handoff_pending_launch",
                updated,
                summary="Required target operator fork is not launched.",
            )
            return {"handoff": updated, "command": None}
        resolved_delivery = str(delivery or "auto").strip().lower()
        if resolved_delivery == "record_only":
            updated = self.store.update_operator_handoff(
                str(handoff["handoff_id"]),
                status="approved",
                delivery_status="recorded",
                metadata=metadata or {},
            )
            if updated is None:
                raise RuntimeError("handoff approval update failed")
            self._mark_handoff_knowledge_turn_delivered(
                updated,
                delivery_status="recorded",
                command=None,
            )
            self._record_handoff_event(
                "operator_handoff_approved",
                updated,
                summary="Operator handoff approved without delivery.",
            )
            return {"handoff": updated, "command": None}
        command = self._deliver_handoff(
            handoff=handoff,
            target_fork=target_fork,
            operator_agent_id=operator_agent_id,
            delivery=resolved_delivery,
        )
        evidence = self._handoff_delivery_evidence(command)
        updated = self.store.update_operator_handoff(
            str(handoff["handoff_id"]),
            status="sent",
            command_id=str(command.get("command_id") or ""),
            tmux_pane_id=str(evidence.get("tmux_pane_id") or "") or None,
            delivery_status=str(command.get("status") or ""),
            delivery_evidence=evidence,
            metadata={
                **(metadata or {}),
                "approved_by_operator_agent_id": operator_agent_id,
            },
        )
        if updated is None:
            raise RuntimeError("handoff delivery update failed")
        self._mark_handoff_knowledge_turn_delivered(
            updated,
            delivery_status=str(command.get("status") or "sent"),
            command=command,
        )
        self._record_handoff_event(
            "operator_handoff_sent",
            updated,
            summary="Operator handoff sent to target operator.",
            command=command,
        )
        return {"handoff": updated, "command": command}

    def ack_handoff(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
        status: str = "acknowledged",
        summary: str,
        detail: str | None = None,
        artifact_bundle: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handoff = self._require_handoff_for_participant(
            operator_agent_id=operator_agent_id,
            handoff_id=handoff_id,
        )
        normalized_status = self._normalize_handoff_status(status)
        if normalized_status not in {
            "acknowledged",
            "running",
            "blocked",
            "failed",
            "complete",
            "completed",
            "expired",
            "canceled",
            "cancelled",
        }:
            raise ValueError("ack status must be acknowledged, running, or terminal")
        evidence = dict(handoff.get("delivery_evidence") or {})
        evidence["agent_acknowledged"] = True
        if normalized_status == "running":
            evidence["agent_started"] = True
        updated = self.store.update_operator_handoff(
            str(handoff["handoff_id"]),
            status=normalized_status,
            summary=summary,
            artifact_bundle=artifact_bundle if artifact_bundle is not None else None,
            delivery_evidence=evidence,
            metadata={
                **(metadata or {}),
                "acknowledged_by_operator_agent_id": operator_agent_id,
                "ack_detail": detail or "",
            },
            acknowledge=True,
            start=normalized_status == "running",
            complete=normalized_status in HANDOFF_TERMINAL_STATUSES,
        )
        if updated is None:
            raise RuntimeError("handoff ack update failed")
        self._record_handoff_event(
            "operator_handoff_acknowledged",
            updated,
            summary=summary,
        )
        return updated

    def update_handoff(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
        status: str,
        summary: str,
        detail: str | None = None,
        artifact_bundle: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handoff = self._require_handoff_for_participant(
            operator_agent_id=operator_agent_id,
            handoff_id=handoff_id,
        )
        normalized_status = self._normalize_handoff_status(status)
        evidence = dict(handoff.get("delivery_evidence") or {})
        if normalized_status in {"acknowledged", "running"}:
            evidence["agent_acknowledged"] = True
        if normalized_status == "running":
            evidence["agent_started"] = True
        updated = self.store.update_operator_handoff(
            str(handoff["handoff_id"]),
            status=normalized_status,
            summary=summary,
            artifact_bundle=artifact_bundle if artifact_bundle is not None else None,
            delivery_evidence=evidence,
            metadata={
                **(metadata or {}),
                "updated_by_operator_agent_id": operator_agent_id,
                "update_detail": detail or "",
            },
            acknowledge=normalized_status in {"acknowledged", "running"},
            start=normalized_status == "running",
            complete=normalized_status in HANDOFF_TERMINAL_STATUSES,
        )
        if updated is None:
            raise RuntimeError("handoff status update failed")
        self._record_handoff_event(
            f"operator_handoff_{normalized_status}",
            updated,
            summary=summary,
        )
        return updated

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

    def request_project_spawn(
        self,
        *,
        operator_agent_id: str,
        review_fork_id: str,
        project_name: str,
        instructions: str,
        mode: str = "empty",
        target_slug: str | None = None,
        campaign_id: str | None = None,
        assignment_id: str | None = None,
        metadata: dict[str, Any] | None = None,
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
        if str(review_fork.get("fork_purpose") or "").strip().lower() != REVIEW_FORK_PURPOSE:
            raise ValueError("project spawn requests must come from a review fork")
        if str(review_fork.get("access_mode") or "").strip().lower() != REVIEW_FORK_ACCESS_MODE:
            raise ValueError("project spawn requests require review_readonly access")
        source_caller_agent_id = str(review_fork.get("source_caller_agent_id") or "").strip()
        source_cwd = str(
            review_fork.get("source_cwd")
            or (review_fork.get("metadata") or {}).get("source_cwd")
            or ""
        ).strip()
        if not source_caller_agent_id or not source_cwd:
            raise ValueError("review fork is missing source caller/cwd metadata")
        resolved_mode = str(mode or "empty").strip().lower()
        if resolved_mode not in PROJECT_SPAWN_MODES:
            raise ValueError("mode must be empty or clone_source")
        if campaign_id:
            self._require_campaign(campaign_id)
        source_path, target_parent, target_path, resolved_slug = resolve_sibling_project_paths(
            source_cwd,
            project_name,
            target_slug=target_slug,
        )
        request = self.store.create_operator_project_spawn_request(
            logical_operator_agent_id=logical_operator_id,
            operator_agent_id=operator_agent_id,
            review_fork_id=str(review_fork["operator_fork_id"]),
            review_fork_agent_id=str(review_fork["fork_agent_id"]),
            source_caller_agent_id=source_caller_agent_id,
            source_cwd=str(source_path),
            target_parent=str(target_parent),
            target_slug=resolved_slug,
            target_path=str(target_path),
            project_name=project_name,
            mode=resolved_mode,
            instructions=instructions,
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            metadata={
                **(metadata or {}),
                "requested_by": str(review_fork["fork_agent_id"]),
                "review_fork_id": str(review_fork["operator_fork_id"]),
            },
        )
        self._record_project_spawn_event(
            "operator_project_spawn_requested",
            request,
            summary=(
                f"Review fork requested {resolved_mode} project "
                f"{resolved_slug}."
            ),
        )
        return request

    def list_project_spawn_requests(
        self,
        *,
        operator_agent_id: str | None = None,
        review_fork_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        logical_operator_id = None
        if operator_agent_id:
            logical_operator_id = self._logical_operator_agent_id(
                self._require_operator(operator_agent_id)
            )
        return self.store.list_operator_project_spawn_requests(
            logical_operator_agent_id=logical_operator_id,
            review_fork_id=review_fork_id,
            status=str(status).strip().lower() if status else None,
            limit=limit,
        )

    def update_project_spawn_request(
        self,
        *,
        spawn_request_id: str,
        status: str,
        launched_agent_id: str | None = None,
        tmux_pane_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {
            "pending",
            "launching",
            "launched",
            "failed",
            "canceled",
            "cancelled",
        }:
            raise ValueError("status must be pending, launching, launched, failed, or canceled")
        request = self.store.update_operator_project_spawn_request(
            spawn_request_id,
            status=normalized_status,
            launched_agent_id=launched_agent_id,
            tmux_pane_id=tmux_pane_id,
            error=error,
            metadata=metadata,
        )
        if request is None:
            raise ValueError("project spawn request not found")
        self._record_project_spawn_event(
            f"operator_project_spawn_{normalized_status}",
            request,
            summary=f"Project spawn request {normalized_status}.",
        )
        return request

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

    def _deliver_operator_seed_run(
        self,
        *,
        seed_run: dict[str, Any],
        delivery: str,
    ) -> dict[str, Any]:
        source_operator_agent_id = str(
            seed_run.get("source_operator_agent_id") or ""
        ).strip()
        target = self._require_agent(source_operator_agent_id)
        if target.get("agent_type") != OPERATOR_AGENT_TYPE:
            raise ValueError("KB seed target is not an operator")
        resolved = str(delivery or "auto").strip().lower()
        mode = str((target.get("metadata") or {}).get("pbx_mode") or "report").lower()
        if resolved == "auto":
            resolved = "queue" if mode == NOHUP_MODE else "tmux"
        payload = {
            "message": seed_run["prompt"],
            "source": "operator_kb_seed_run",
            "seed_run_id": seed_run["seed_run_id"],
            "operator_agent_id": source_operator_agent_id,
            "logical_operator_id": seed_run["logical_operator_agent_id"],
            "seed_type": seed_run["seed_type"],
            "scope": seed_run["scope"],
            "project": seed_run.get("project"),
            "repo_root": seed_run.get("repo_root"),
            "git_remote": seed_run.get("git_remote"),
            "branch": seed_run.get("branch"),
        }
        if resolved == "queue":
            return self.store.create_command(
                CommandCreateRequest(
                    agent_id=source_operator_agent_id,
                    type="send_input",
                    payload=payload,
                )
            )
        if resolved != "tmux":
            raise ValueError("delivery must be auto, queue, or tmux")
        pane = self._resolve_tmux_pane(target)
        tmux_support.send_text(pane.pane_id, seed_run["prompt"], tmux_bin=self.tmux_bin)
        payload["tmux_pane_id"] = pane.pane_id
        payload["tmux_target"] = pane.target_label
        return self.store.create_command(
            CommandCreateRequest(
                agent_id=source_operator_agent_id,
                type="send_input",
                payload=payload,
            ),
            status="sent",
        )

    def _deliver_knowledge_turn(
        self,
        turn: dict[str, Any],
        *,
        operator_agent_id: str,
        link: dict[str, Any],
        delivery: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        recipient_agent_id = str(turn.get("recipient_agent_id") or "").strip()
        target = self._require_agent(recipient_agent_id)
        resolved = str(delivery or "auto").strip().lower()
        command: dict[str, Any] | None = None
        tmux_pane_id: str | None = None
        if resolved == "record_only":
            updated_turn = self.store.update_operator_knowledge_turn_delivery(
                str(turn["turn_id"]),
                delivery_status="recorded",
                metadata=metadata,
            ) or turn
            self._record_knowledge_turn_event(
                link=link,
                turn=updated_turn,
                operator_agent_id=operator_agent_id,
                command=None,
            )
            return {"turn": updated_turn, "command": None}
        mode = str((target.get("metadata") or {}).get("pbx_mode") or "report").lower()
        if resolved == "auto":
            resolved = "queue" if mode == NOHUP_MODE else "tmux"
        payload = {
            "message": turn["message"],
            "source": "operator_knowledge_turn",
            "knowledge_link_id": link["link_id"],
            "knowledge_turn_id": turn["turn_id"],
            "operator_agent_id": operator_agent_id,
            "logical_operator_agent_id": link["logical_operator_agent_id"],
            "sender_agent_id": turn["sender_agent_id"],
            "recipient_agent_id": recipient_agent_id,
            "turn_type": turn["turn_type"],
            "link_type": link["link_type"],
        }
        if resolved == "queue":
            command = self.store.create_command(
                CommandCreateRequest(
                    agent_id=recipient_agent_id,
                    type="send_input",
                    payload=payload,
                )
            )
            delivery_status = "queued"
        elif resolved == "tmux":
            pane = self._resolve_tmux_pane(target)
            tmux_support.send_text(pane.pane_id, turn["message"], tmux_bin=self.tmux_bin)
            tmux_pane_id = pane.pane_id
            payload["tmux_pane_id"] = pane.pane_id
            payload["tmux_target"] = pane.target_label
            command = self.store.create_command(
                CommandCreateRequest(
                    agent_id=recipient_agent_id,
                    type="send_input",
                    payload=payload,
                ),
                status="sent",
            )
            delivery_status = "sent"
        else:
            raise ValueError("delivery must be auto, queue, tmux, or record_only")
        updated_turn = self.store.update_operator_knowledge_turn_delivery(
            str(turn["turn_id"]),
            delivery_status=delivery_status,
            command_id=str(command.get("command_id") or "") if command else None,
            tmux_pane_id=tmux_pane_id,
            metadata=metadata,
        ) or turn
        self._record_knowledge_turn_event(
            link=link,
            turn=updated_turn,
            operator_agent_id=operator_agent_id,
            command=command,
        )
        return {"turn": updated_turn, "command": command}

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

    def _record_project_spawn_event(
        self,
        event_type: str,
        request: dict[str, Any],
        *,
        summary: str,
    ) -> None:
        payload = {
            "spawn_request_id": request.get("spawn_request_id"),
            "operator_agent_id": request.get("operator_agent_id"),
            "logical_operator_agent_id": request.get("logical_operator_agent_id"),
            "review_fork_id": request.get("review_fork_id"),
            "review_fork_agent_id": request.get("review_fork_agent_id"),
            "source_caller_agent_id": request.get("source_caller_agent_id"),
            "source_cwd": request.get("source_cwd"),
            "target_path": request.get("target_path"),
            "target_slug": request.get("target_slug"),
            "project_name": request.get("project_name"),
            "mode": request.get("mode"),
            "status": request.get("status"),
            "launched_agent_id": request.get("launched_agent_id"),
            "tmux_pane_id": request.get("tmux_pane_id"),
            "error": request.get("error"),
            "campaign_id": request.get("campaign_id"),
            "assignment_id": request.get("assignment_id"),
        }
        self.store.append_event(
            event_type,
            payload,
            str(request.get("spawn_request_id") or ""),
        )
        campaign_id = str(request.get("campaign_id") or "").strip()
        if campaign_id:
            self.store.add_operator_campaign_event(
                campaign_id=campaign_id,
                assignment_id=str(request.get("assignment_id") or "").strip() or None,
                operator_agent_id=str(request.get("operator_agent_id") or ""),
                target_agent_id=str(request.get("source_caller_agent_id") or "") or None,
                event_type=event_type,
                summary=summary,
                detail=payload,
            )

    def _require_handoff(self, handoff_id: str) -> dict[str, Any]:
        handoff = self.store.get_operator_handoff(handoff_id)
        if handoff is None:
            raise ValueError("handoff not found")
        return handoff

    def _require_handoff_for_source_operator(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        handoff = self._require_handoff(handoff_id)
        if str(handoff.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("handoff belongs to a different operator")
        return handoff

    def _require_handoff_for_participant(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
    ) -> dict[str, Any]:
        operator = self._require_operator(operator_agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        handoff = self._require_handoff(handoff_id)
        allowed = {
            str(handoff.get("logical_operator_agent_id") or ""),
            str(handoff.get("target_operator_agent_id") or ""),
        }
        if logical_operator_id not in allowed:
            raise ValueError("handoff belongs to different operators")
        return handoff

    def _expire_handoff_if_due(self, handoff: dict[str, Any]) -> dict[str, Any]:
        if not bool(handoff.get("expired")):
            return handoff
        if str(handoff.get("status") or "") not in HANDOFF_PRESTART_STATUSES:
            return handoff
        updated = self.store.update_operator_handoff(
            str(handoff["handoff_id"]),
            status="expired",
            summary="Handoff expired before the receiving operator started.",
            error="expired before start",
            complete=True,
        )
        if updated is None:
            return handoff
        self._record_handoff_event(
            "operator_handoff_expired",
            updated,
            summary="Handoff expired before the receiving operator started.",
        )
        return updated

    def _handoff_required_target_fork(
        self,
        handoff: dict[str, Any],
    ) -> dict[str, Any] | None:
        target_caller_agent_id = str(handoff.get("target_caller_agent_id") or "").strip()
        target_operator_fork_id = str(
            handoff.get("target_operator_fork_id") or ""
        ).strip()
        if not target_caller_agent_id and not target_operator_fork_id:
            return None
        if target_operator_fork_id:
            fork = (
                self.store.get_operator_fork(target_operator_fork_id)
                or self.store.get_operator_fork_for_agent(target_operator_fork_id)
            )
            if fork is None:
                raise ValueError("target operator fork not found")
            return fork
        target_operator_agent_id = str(
            handoff.get("target_operator_agent_id") or ""
        ).strip()
        if not target_operator_agent_id:
            return None
        return self.ensure_fork(
            operator_agent_id=target_operator_agent_id,
            source_caller_agent_id=target_caller_agent_id,
            fork_track_id=DEFAULT_FORK_TRACK_ID,
            metadata={
                "created_for_handoff_id": handoff.get("handoff_id"),
                "handoff_requires_launch": True,
            },
        )

    @staticmethod
    def _handoff_target_fork_ready(fork: dict[str, Any]) -> bool:
        status = str(fork.get("status") or "").strip().lower()
        pane_id = str(fork.get("tmux_pane_id") or "").strip()
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        pending = bool(metadata.get("operator_fork_pending"))
        return status in FORK_READY_STATUSES and bool(pane_id) and not pending

    def _deliver_handoff(
        self,
        *,
        handoff: dict[str, Any],
        target_fork: dict[str, Any] | None,
        operator_agent_id: str,
        delivery: str,
    ) -> dict[str, Any]:
        target_operator_agent_id = str(
            handoff.get("target_operator_agent_id") or ""
        ).strip()
        message = self._handoff_message(handoff=handoff, target_fork=target_fork)
        return self._deliver_root_operator_message(
            operator_agent_id=operator_agent_id,
            logical_operator_id=target_operator_agent_id,
            message=message,
            delivery=delivery,
            payload={
                "source": "operator_handoff",
                "handoff_id": handoff.get("handoff_id"),
                "source_operator_agent_id": handoff.get("source_operator_agent_id"),
                "target_operator_agent_id": target_operator_agent_id,
                "source_agent_id": handoff.get("source_agent_id"),
                "source_operator_fork_id": handoff.get("source_operator_fork_id"),
                "target_caller_agent_id": handoff.get("target_caller_agent_id"),
                "target_operator_fork_id": (
                    target_fork.get("operator_fork_id") if target_fork else None
                ),
                "knowledge_link_id": handoff.get("knowledge_link_id"),
                "knowledge_turn_id": handoff.get("knowledge_turn_id"),
            },
        )

    def _handoff_message(
        self,
        *,
        handoff: dict[str, Any],
        target_fork: dict[str, Any] | None,
    ) -> str:
        artifact_lines = self._handoff_artifact_lines(
            "Required artifacts",
            handoff.get("required_artifacts") or [],
        )
        bundle_lines = self._handoff_artifact_lines(
            "Artifact bundle",
            handoff.get("artifact_bundle") or [],
        )
        expires_at = handoff.get("expires_at")
        time_remaining = handoff.get("time_remaining_seconds")
        expiry_line = "Expires: -"
        if expires_at is not None:
            expiry_line = f"Expires: {expires_at}"
            if time_remaining is not None:
                expiry_line += f" ({time_remaining:.0f}s remaining)"
        target_fork_lines = ["Target fork: -"]
        if target_fork is not None:
            target_fork_lines = [
                f"Target fork: {target_fork.get('fork_agent_id')}",
                f"Target fork ID: {target_fork.get('operator_fork_id')}",
                f"Target fork status: {target_fork.get('status')}",
                f"Target fork pane: {target_fork.get('tmux_pane_id') or '-'}",
            ]
        return "\n".join(
            [
                "Operator handoff",
                "",
                f"Handoff ID: {handoff.get('handoff_id')}",
                f"Source operator: {handoff.get('logical_operator_agent_id')}",
                f"Source agent: {handoff.get('source_agent_id')}",
                f"Target operator: {handoff.get('target_operator_agent_id')}",
                f"Target caller: {handoff.get('target_caller_agent_id') or '-'}",
                *target_fork_lines,
                f"Knowledge link: {handoff.get('knowledge_link_id') or '-'}",
                f"Knowledge turn: {handoff.get('knowledge_turn_id') or '-'}",
                expiry_line,
                f"Allowed mutation scope: {handoff.get('allowed_mutation_scope') or '-'}",
                "",
                "Objective:",
                str(handoff.get("objective") or ""),
                "",
                "Message:",
                str(handoff.get("message") or ""),
                "",
                *artifact_lines,
                "",
                *bundle_lines,
                "",
                "Receiver actions:",
                "- Reply with pbx_operator_ack_handoff after reading the handoff.",
                "- Use status='acknowledged' when context is understood.",
                "- Use status='running' only after the target workflow actually starts.",
                "- Use pbx_operator_update_handoff for blocked, failed, or complete terminal states.",
                "- Do not change fork ownership or source-session associations for this handoff.",
            ]
        )

    @staticmethod
    def _handoff_artifact_lines(title: str, artifacts: list[Any]) -> list[str]:
        lines = [f"{title}:"]
        if not artifacts:
            lines.append("- none")
            return lines
        for item in artifacts:
            if isinstance(item, dict):
                visible = {
                    key: value
                    for key, value in item.items()
                    if "secret" not in str(key).lower()
                    and "token" not in str(key).lower()
                    and "password" not in str(key).lower()
                }
                lines.append(f"- {visible}")
            else:
                lines.append(f"- {item}")
        return lines

    @staticmethod
    def _handoff_delivery_evidence(command: dict[str, Any]) -> dict[str, Any]:
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        return {
            "command_id": command.get("command_id"),
            "command_status": command.get("status"),
            "delivery_status": command.get("status"),
            "delivered_to_pane": bool(payload.get("tmux_pane_id")),
            "pasted_to_input": bool(payload.get("tmux_pane_id")),
            "submitted_to_codex": bool(payload.get("tmux_pane_id")),
            "agent_acknowledged": False,
            "agent_started": False,
            "tmux_pane_id": payload.get("tmux_pane_id"),
            "tmux_target": payload.get("tmux_target"),
        }

    @staticmethod
    def _seed_delivery_evidence(command: dict[str, Any]) -> dict[str, Any]:
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        return {
            "command_id": command.get("command_id"),
            "command_status": command.get("status"),
            "delivery_status": command.get("status"),
            "delivered_to_pane": bool(payload.get("tmux_pane_id")),
            "submitted_to_codex": bool(payload.get("tmux_pane_id")),
            "tmux_pane_id": payload.get("tmux_pane_id"),
            "tmux_target": payload.get("tmux_target"),
        }

    def _mark_handoff_knowledge_turn_delivered(
        self,
        handoff: dict[str, Any],
        *,
        delivery_status: str,
        command: dict[str, Any] | None,
    ) -> None:
        link_id = str(handoff.get("knowledge_link_id") or "").strip()
        turn_id = str(handoff.get("knowledge_turn_id") or "").strip()
        if link_id:
            self.store.update_operator_knowledge_link(link_id, status="active")
        if not turn_id:
            return
        command_payload = (
            command.get("payload")
            if command is not None and isinstance(command.get("payload"), dict)
            else {}
        )
        tmux_pane_id = str(command_payload.get("tmux_pane_id") or "").strip() or None
        self.store.update_operator_knowledge_turn_delivery(
            turn_id,
            delivery_status=delivery_status,
            command_id=str(command.get("command_id") or "") if command else None,
            tmux_pane_id=tmux_pane_id,
            metadata={
                "delivered_by_handoff_id": handoff.get("handoff_id"),
                "handoff_delivery_status": delivery_status,
            },
        )

    def _record_handoff_event(
        self,
        event_type: str,
        handoff: dict[str, Any],
        *,
        summary: str,
        command: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "handoff_id": handoff.get("handoff_id"),
            "logical_operator_agent_id": handoff.get("logical_operator_agent_id"),
            "source_operator_agent_id": handoff.get("source_operator_agent_id"),
            "target_operator_agent_id": handoff.get("target_operator_agent_id"),
            "source_agent_id": handoff.get("source_agent_id"),
            "source_operator_fork_id": handoff.get("source_operator_fork_id"),
            "target_caller_agent_id": handoff.get("target_caller_agent_id"),
            "target_operator_fork_id": handoff.get("target_operator_fork_id"),
            "knowledge_link_id": handoff.get("knowledge_link_id"),
            "knowledge_turn_id": handoff.get("knowledge_turn_id"),
            "status": handoff.get("status"),
            "expires_at": handoff.get("expires_at"),
            "command_id": command.get("command_id") if command else handoff.get("command_id"),
        }
        self.store.append_event(
            event_type,
            payload,
            str(handoff.get("handoff_id") or ""),
        )

    def _record_kb_event(
        self,
        event_type: str,
        entry: dict[str, Any],
        *,
        operator_agent_id: str,
        summary: str,
    ) -> None:
        self.store.append_event(
            event_type,
            {
                "kb_id": entry.get("kb_id"),
                "operator_agent_id": operator_agent_id,
                "created_by_operator_agent_id": entry.get(
                    "created_by_operator_agent_id"
                ),
                "created_by_agent_id": entry.get("created_by_agent_id"),
                "scope": entry.get("scope"),
                "project": entry.get("project"),
                "repo_root": entry.get("repo_root"),
                "status": entry.get("status"),
                "redaction_status": entry.get("redaction_status"),
                "summary": summary,
                "source_knowledge_link_id": entry.get("source_knowledge_link_id"),
                "source_handoff_id": entry.get("source_handoff_id"),
            },
            str(entry.get("kb_id") or ""),
        )

    def _record_kb_seed_event(
        self,
        event_type: str,
        seed_run: dict[str, Any],
        *,
        summary: str,
    ) -> None:
        self.store.append_event(
            event_type,
            {
                "seed_run_id": seed_run.get("seed_run_id"),
                "operator_agent_id": seed_run.get("source_operator_agent_id"),
                "logical_operator_agent_id": seed_run.get(
                    "logical_operator_agent_id"
                ),
                "source_operator_agent_id": seed_run.get(
                    "source_operator_agent_id"
                ),
                "seed_type": seed_run.get("seed_type"),
                "scope": seed_run.get("scope"),
                "project": seed_run.get("project"),
                "repo_root": seed_run.get("repo_root"),
                "status": seed_run.get("status"),
                "delivery_status": seed_run.get("delivery_status"),
                "command_id": seed_run.get("command_id"),
                "summary": summary,
            },
            str(seed_run.get("seed_run_id") or ""),
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

    def _require_knowledge_link(self, link_id: str) -> dict[str, Any]:
        link = self.store.get_operator_knowledge_link(link_id)
        if link is None:
            raise ValueError("knowledge link not found")
        return link

    def _require_knowledge_link_for_operator(
        self,
        link_id: str,
        logical_operator_id: str,
    ) -> dict[str, Any]:
        link = self._require_knowledge_link(link_id)
        if str(link.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("knowledge link belongs to a different operator")
        return link

    def _require_knowledge_link_visible_to_operator(
        self,
        link_id: str,
        logical_operator_id: str,
    ) -> dict[str, Any]:
        link = self._require_knowledge_link(link_id)
        if not self._knowledge_link_visible_to_operator(link, logical_operator_id):
            raise ValueError("knowledge link belongs to a different operator")
        return link

    def _knowledge_link_visible_to_operator(
        self,
        link: dict[str, Any],
        logical_operator_id: str,
    ) -> bool:
        if str(link.get("logical_operator_agent_id") or "") == logical_operator_id:
            return True
        return any(
            self._agent_resolves_to_logical_operator(
                str(link.get(field) or ""),
                logical_operator_id,
            )
            for field in ("source_agent_id", "target_agent_id", "operator_agent_id")
        )

    def _require_handoff_visible_to_operator(
        self,
        handoff_id: str,
        logical_operator_id: str,
    ) -> dict[str, Any]:
        handoff = self._require_handoff(handoff_id)
        allowed = {
            str(handoff.get("logical_operator_agent_id") or ""),
            str(handoff.get("target_operator_agent_id") or ""),
        }
        if logical_operator_id not in allowed:
            raise ValueError("handoff belongs to different operators")
        return handoff

    def _require_operator_kb_source_context(
        self,
        *,
        logical_operator_id: str,
        source_knowledge_link_id: str | None,
        source_handoff_id: str | None,
        source_turn_ids: list[str],
    ) -> list[str]:
        link_id = str(source_knowledge_link_id or "").strip()
        handoff_id = str(source_handoff_id or "").strip()
        if link_id:
            self._require_knowledge_link_visible_to_operator(
                link_id,
                logical_operator_id,
            )
        if handoff_id:
            self._require_handoff_visible_to_operator(
                handoff_id,
                logical_operator_id,
            )
        normalized_turn_ids: list[str] = []
        seen_turn_ids: set[str] = set()
        for turn_id_value in source_turn_ids:
            turn_id = str(turn_id_value or "").strip()
            if not turn_id or turn_id in seen_turn_ids:
                continue
            turn = self.store.get_operator_knowledge_turn(turn_id)
            if turn is None:
                raise ValueError("knowledge turn not found")
            turn_link_id = str(turn.get("link_id") or "").strip()
            if link_id and turn_link_id != link_id:
                raise ValueError("knowledge turn belongs to a different link")
            self._require_knowledge_link_visible_to_operator(
                turn_link_id,
                logical_operator_id,
            )
            seen_turn_ids.add(turn_id)
            normalized_turn_ids.append(turn_id)
        return normalized_turn_ids

    def _operator_kb_seed_defaults(self, operator: dict[str, Any]) -> dict[str, Any]:
        metadata = (
            operator.get("metadata")
            if isinstance(operator.get("metadata"), dict)
            else {}
        )
        source_caller_id = str(metadata.get("source_caller_agent_id") or "").strip()
        source_agent = self.store.get_agent(source_caller_id) if source_caller_id else None
        source_metadata = (
            source_agent.get("metadata")
            if isinstance(source_agent, dict)
            and isinstance(source_agent.get("metadata"), dict)
            else {}
        )
        project = (
            str(source_agent.get("project") or "").strip()
            if isinstance(source_agent, dict)
            else ""
        ) or str(operator.get("project") or "").strip()
        repo_root = (
            str(metadata.get("source_cwd") or "").strip()
            or str(source_metadata.get("cwd") or "").strip()
            or str(metadata.get("cwd") or "").strip()
        )
        return {
            "project": project or None,
            "repo_root": repo_root or None,
            "git_remote": str(metadata.get("git_remote") or "").strip() or None,
            "branch": str(metadata.get("branch") or "").strip() or None,
            "source_caller_agent_id": source_caller_id or None,
            "source_codex_session_id": (
                str(metadata.get("source_codex_session_id") or "").strip() or None
            ),
            "source_operator_codex_session_id": (
                str(
                    metadata.get("fork_codex_session_id")
                    or metadata.get("codex_session_id")
                    or metadata.get("codex_thread_id")
                    or ""
                ).strip()
                or None
            ),
        }

    @staticmethod
    def _operator_kb_seed_sync_key(
        *,
        logical_operator_id: str,
        seed_type: str,
        scope: str,
        project: str | None,
        repo_root: str | None,
        git_remote: str | None,
        branch: str | None,
    ) -> str:
        text = "\0".join(
            [
                str(logical_operator_id or ""),
                str(seed_type or ""),
                str(scope or ""),
                str(project or ""),
                str(repo_root or ""),
                str(git_remote or ""),
                str(branch or ""),
            ]
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def render_kb_seed_prompt(
        *,
        operator_agent_id: str,
        logical_operator_id: str,
        seed_run_id: str,
        seed_type: str,
        scope: str,
        project: str | None,
        repo_root: str | None,
        git_remote: str | None,
        branch: str | None,
        seed_sync_key: str,
    ) -> str:
        return "\n".join(
            [
                "Seed the Agent PBX operator KB with durable domain knowledge.",
                "",
                f"Seed run: {seed_run_id}",
                f"Operator agent: {operator_agent_id}",
                f"Logical operator: {logical_operator_id}",
                f"Seed type: {seed_type}",
                f"Scope: {scope}",
                f"Project: {project or '-'}",
                f"Repo root: {repo_root or '-'}",
                f"Git remote: {git_remote or '-'}",
                f"Branch: {branch or '-'}",
                f"Sync key: {seed_sync_key}",
                "",
                "Task:",
                "- Review your current durable domain knowledge for this operator context.",
                "- Search existing active and proposed KB entries before proposing new ones.",
                "- Propose small atomic KB entries only for reusable operating guidance.",
                "- Exclude secrets, credentials, personal data, proprietary raw dumps, transient status, and speculation.",
                "- Prefer stable procedures, constraints, architecture notes, validation rules, and known pitfalls.",
                "- If nothing durable should be saved, do not create KB entries.",
                "",
                "For each durable item, call pbx_operator_kb_propose with:",
                f"- operator_agent_id={operator_agent_id!r}",
                f"- scope={scope!r}",
                f"- project={project!r}",
                f"- repo_root={repo_root!r}",
                f"- git_remote={git_remote!r}",
                f"- branch={branch!r}",
                "- concise title, summary, body, and tags",
                "- metadata containing:",
                f"  seed_run_id={seed_run_id!r}",
                f"  seed_type={seed_type!r}",
                f"  seed_sync_key={seed_sync_key!r}",
                "  extraction_version='operator_kb_seed_v1'",
                f"  source_operator_agent_id={operator_agent_id!r}",
                f"  logical_operator_id={logical_operator_id!r}",
                "",
                "When finished, call pbx_operator_kb_update_seed_run with:",
                f"- operator_agent_id={operator_agent_id!r}",
                f"- seed_run_id={seed_run_id!r}",
                "- status='complete' if done, or status='failed' with error if blocked",
                "- summary describing how many KB proposals were created or why none were created",
                "",
                "Do not promote KB entries. Promotion stays with root-operator review.",
            ]
        )

    def _require_operator_kb_seed_run_visible(
        self,
        seed_run_id: str,
        logical_operator_id: str,
    ) -> dict[str, Any]:
        seed_run = self.store.get_operator_kb_seed_run(seed_run_id)
        if seed_run is None:
            raise ValueError("KB seed run not found")
        if str(seed_run.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("KB seed run belongs to a different operator")
        return seed_run

    def _require_operator_kb_seed_run_usable(
        self,
        seed_run_id: str,
        logical_operator_id: str,
        operator_agent_id: str,
    ) -> dict[str, Any]:
        seed_run = self._require_operator_kb_seed_run_visible(
            seed_run_id,
            logical_operator_id,
        )
        source_operator_id = str(seed_run.get("source_operator_agent_id") or "").strip()
        if operator_agent_id not in {logical_operator_id, source_operator_id}:
            raise ValueError("KB seed run belongs to a different source operator")
        return seed_run

    def _require_operator_kb_entry(self, kb_id: str) -> dict[str, Any]:
        entry = self.store.get_operator_kb_entry(kb_id)
        if entry is None:
            raise ValueError("KB entry not found")
        return entry

    @staticmethod
    def _operator_can_read_kb_entry(
        entry: dict[str, Any],
        logical_operator_id: str,
    ) -> bool:
        if str(entry.get("status") or "") == "active":
            return True
        return str(entry.get("created_by_operator_agent_id") or "") == logical_operator_id

    @staticmethod
    def _require_operator_kb_owner(
        entry: dict[str, Any],
        logical_operator_id: str,
    ) -> None:
        if str(entry.get("created_by_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("KB entry belongs to a different operator")

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

    def _require_root_operator(self, agent_id: str) -> dict[str, Any]:
        operator = self._require_operator(agent_id)
        logical_operator_id = self._logical_operator_agent_id(operator)
        if logical_operator_id != agent_id or self._is_operator_fork_agent(operator):
            raise ValueError("operation requires the root operator session")
        return operator

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

    def _agent_resolves_to_logical_operator(
        self,
        agent_id: str,
        logical_operator_id: str,
    ) -> bool:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            return False
        if normalized_agent_id == logical_operator_id:
            return True
        agent = self.store.get_agent(normalized_agent_id)
        if agent is None or agent.get("agent_type") != OPERATOR_AGENT_TYPE:
            return False
        return self._logical_operator_agent_id(agent) == logical_operator_id

    def local_codex_host_id(self) -> str:
        return os.getenv("AGENT_PBX_CODEX_HOST_ID", "").strip() or socket.gethostname()

    def operator_tmux_session_name(self) -> str:
        return (
            os.getenv(OPERATOR_TMUX_SESSION_ENV, DEFAULT_OPERATOR_TMUX_SESSION).strip()
            or DEFAULT_OPERATOR_TMUX_SESSION
        )

    def _validated_knowledge_source_fork_id(
        self,
        *,
        logical_operator_id: str,
        source_agent_id: str,
        source_operator_fork_id: str | None,
    ) -> str | None:
        fork_id = str(source_operator_fork_id or "").strip()
        if not fork_id:
            return None
        fork = self.store.get_operator_fork(fork_id) or self.store.get_operator_fork_for_agent(fork_id)
        if fork is None:
            raise ValueError("source_operator_fork_id not found")
        if str(fork.get("logical_operator_agent_id") or "") != logical_operator_id:
            raise ValueError("source_operator_fork_id belongs to a different operator")
        if str(fork.get("fork_agent_id") or "") != source_agent_id:
            raise ValueError("source_operator_fork_id does not match source_agent_id")
        return str(fork["operator_fork_id"])

    def _validated_handoff_target_fork_id(
        self,
        *,
        target_logical_operator_id: str,
        target_caller_agent_id: str | None,
        target_operator_fork_id: str | None,
    ) -> str | None:
        fork_id = str(target_operator_fork_id or "").strip()
        if not fork_id:
            return None
        fork = self.store.get_operator_fork(fork_id) or self.store.get_operator_fork_for_agent(fork_id)
        if fork is None:
            raise ValueError("target_operator_fork_id not found")
        if str(fork.get("logical_operator_agent_id") or "") != target_logical_operator_id:
            raise ValueError("target_operator_fork_id belongs to a different operator")
        if target_caller_agent_id and str(fork.get("source_caller_agent_id") or "") != target_caller_agent_id:
            raise ValueError("target_operator_fork_id source caller does not match target_caller_agent_id")
        return str(fork["operator_fork_id"])

    def _validate_knowledge_participant(
        self,
        link: dict[str, Any],
        agent_id: str,
    ) -> None:
        participants = {
            str(link.get("source_agent_id") or ""),
            str(link.get("target_agent_id") or ""),
            str(link.get("logical_operator_agent_id") or ""),
            str(link.get("operator_agent_id") or ""),
        }
        if agent_id not in participants:
            raise ValueError("knowledge turn participant is not part of the link")

    @staticmethod
    def _normalize_knowledge_link_type(link_type: str) -> str:
        normalized = str(link_type or "domain_context").strip().lower()
        if normalized not in KNOWLEDGE_LINK_TYPES:
            raise ValueError("link_type must be handoff, consult, domain_context, or review_context")
        return normalized

    @staticmethod
    def _normalize_knowledge_link_status(status: str) -> str:
        normalized = str(status or "active").strip().lower()
        if normalized not in KNOWLEDGE_LINK_STATUSES:
            raise ValueError("status must be proposed, active, closed, canceled, or cancelled")
        return normalized

    @staticmethod
    def _normalize_knowledge_turn_type(turn_type: str) -> str:
        normalized = str(turn_type or "note").strip().lower()
        if normalized not in KNOWLEDGE_TURN_TYPES:
            raise ValueError("turn_type must be handoff, question, answer, or note")
        return normalized

    @staticmethod
    def _normalize_handoff_status(status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized not in HANDOFF_STATUSES:
            raise ValueError("handoff status is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_status(status: str | None) -> str:
        normalized = str(status or "proposed").strip().lower()
        if normalized not in OPERATOR_KB_STATUSES:
            raise ValueError("KB status is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_scope(scope: str | None) -> str:
        normalized = str(scope or "project").strip().lower()
        if normalized not in OPERATOR_KB_SCOPES:
            raise ValueError("KB scope is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_redaction_status(status: str | None) -> str:
        normalized = str(status or "unreviewed").strip().lower()
        if normalized not in OPERATOR_KB_REDACTION_STATUSES:
            raise ValueError("KB redaction status is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_seed_run_status(status: str | None) -> str:
        normalized = str(status or "requested").strip().lower()
        if normalized not in OPERATOR_KB_SEED_RUN_STATUSES:
            raise ValueError("KB seed run status is invalid")
        return normalized

    @staticmethod
    def _operator_kb_redaction_status(
        *,
        title: str,
        summary: str,
        body: str,
        metadata: dict[str, Any],
    ) -> str:
        text = "\n".join(
            [
                str(title or ""),
                str(summary or ""),
                str(body or ""),
                str(metadata or {}),
            ]
        )
        for pattern in OPERATOR_KB_SECRET_PATTERNS:
            if pattern.search(text):
                return "needs_review"
        return "clean"

    @staticmethod
    def _operator_kb_body_from_link(
        link: dict[str, Any],
        turns: list[dict[str, Any]],
    ) -> str:
        lines = [
            "# Operator Knowledge Link Snapshot",
            "",
            f"Knowledge link: {link.get('link_id')}",
            f"Link type: {link.get('link_type')}",
            f"Source agent: {link.get('source_agent_id')}",
            f"Target agent: {link.get('target_agent_id')}",
            f"Status: {link.get('status')}",
            "",
            "## Summary",
            "",
            str(link.get("summary") or "No summary recorded."),
            "",
            "## Turns",
            "",
        ]
        if not turns:
            lines.append("No turns selected.")
            return "\n".join(lines)
        sorted_turns = sorted(
            turns,
            key=lambda turn: (
                float(turn.get("created_at") or 0.0),
                str(turn.get("turn_id") or ""),
            ),
        )
        for turn in sorted_turns:
            lines.extend(
                [
                    f"### {turn.get('turn_type') or 'turn'} {turn.get('turn_id')}",
                    "",
                    f"Sender: {turn.get('sender_agent_id')}",
                    f"Recipient: {turn.get('recipient_agent_id')}",
                    f"Delivery: {turn.get('delivery_status')}",
                    "",
                    str(turn.get("message") or ""),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _knowledge_turn_message(
        *,
        link: dict[str, Any],
        message: str,
        turn_type: str,
    ) -> str:
        return "\n".join(
            [
                "Operator knowledge transfer",
                "",
                f"Knowledge link: {link.get('link_id')}",
                f"Link type: {link.get('link_type')}",
                f"Turn type: {turn_type}",
                f"Source agent: {link.get('source_agent_id')}",
                f"Target agent: {link.get('target_agent_id')}",
                "",
                message,
                "",
                "Use Agent PBX reports for questions, answers, and completion evidence. "
                "This knowledge link does not change operator fork ownership or source permissions.",
            ]
        )

    def _record_knowledge_turn_event(
        self,
        *,
        link: dict[str, Any],
        turn: dict[str, Any],
        operator_agent_id: str,
        command: dict[str, Any] | None,
    ) -> None:
        payload = {
            "link_id": link.get("link_id"),
            "turn_id": turn.get("turn_id"),
            "operator_agent_id": operator_agent_id,
            "logical_operator_agent_id": link.get("logical_operator_agent_id"),
            "source_agent_id": link.get("source_agent_id"),
            "target_agent_id": link.get("target_agent_id"),
            "sender_agent_id": turn.get("sender_agent_id"),
            "recipient_agent_id": turn.get("recipient_agent_id"),
            "turn_type": turn.get("turn_type"),
            "delivery_status": turn.get("delivery_status"),
            "command_id": command.get("command_id") if command else None,
            "command_status": command.get("status") if command else None,
        }
        self.store.append_event(
            "operator_knowledge_turn_delivered",
            payload,
            str(link.get("link_id") or ""),
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
