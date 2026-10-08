from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .contracts import LifecycleState
from .runtime_tmux import (
    RuntimeTmuxPane,
    assess_runtime_mapping,
    probe_runtime_panes,
    process_start_ticks,
    runtime_mapping_server_identity,
    validate_tmux_socket,
)
from .schemas import ReportCreateRequest
from .store import Store


CLIENT_LIFECYCLE_ACTIONS = {
    "resume",
    "restart_resume",
    "pop_in",
    "pop_out",
}
SERVER_LIFECYCLE_ACTIONS = {
    "archive",
    "mark_canceled",
    "purge",
    "repair_mapping",
    "star",
    "unarchive",
    "unstar",
}
LIFECYCLE_ACTIONS = CLIENT_LIFECYCLE_ACTIONS | SERVER_LIFECYCLE_ACTIONS | {"prune"}
CODEX_SESSION_KEYS = (
    "fork_codex_session_id",
    "codex_session_id",
    "codex_thread_id",
    "last_resume_codex_session_id",
)


class LifecycleService:
    """Reconciles PBX identity with workspace, Codex, tmux, and references."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def inspect(self, entity_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(entity_id)
        if agent is None:
            raise ValueError("agent not found")
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        fork = self.store.get_operator_fork_for_agent(entity_id)
        mapping = self.store.get_tmux_runtime_mapping(entity_id)
        runtime = self._assess_mapping(mapping)
        workspace_path = str(
            (fork or {}).get("work_root")
            or (fork or {}).get("cwd")
            or metadata.get("work_root")
            or metadata.get("cwd")
            or ""
        ).strip()
        workspace = self._workspace_status(workspace_path, fork=fork)
        codex_session_id = self._codex_session_id(agent, fork=fork, mapping=mapping)
        reference_guards = self.store.agent_prune_protection_reasons().get(entity_id, [])
        guards = sorted(
            {
                *reference_guards,
                *(["starred"] if agent.get("starred") else []),
                *(
                    ["queued_commands"]
                    if int(agent.get("queued_command_count") or 0) > 0
                    else []
                ),
                *(
                    ["active_campaign"]
                    if int(agent.get("active_campaign_count") or 0) > 0
                    else []
                ),
            }
        )
        entity_kind = self._entity_kind(agent, fork)
        state = self._lifecycle_state(agent, runtime)
        result = {
            "entity_id": entity_id,
            "entity_kind": entity_kind,
            "lifecycle_state": state.value,
            "agent": {
                "agent_id": entity_id,
                "agent_type": agent.get("agent_type"),
                "project": agent.get("project"),
                "status": agent.get("status"),
                "effective_status": agent.get("effective_status"),
                "pbx_active": bool(agent.get("pbx_active", True)),
                "starred": bool(agent.get("starred")),
                "archived": agent.get("dismissed_at") is not None,
                "queued_command_count": int(agent.get("queued_command_count") or 0),
                "active_campaign_count": int(agent.get("active_campaign_count") or 0),
                "last_seen_at": agent.get("last_seen_at"),
            },
            "codex": {
                "session_id": codex_session_id,
                "session_known": bool(codex_session_id),
            },
            "workspace": workspace,
            "runtime": runtime,
            "fork": self._safe_fork(fork),
            "guard_reasons": guards,
            "actions": {},
            "observed_at": time.time(),
        }
        result["actions"] = self._action_matrix(result)
        return result

    def preview_action(self, entity_id: str, action: str) -> dict[str, Any]:
        normalized = self._normalize_action(action)
        if normalized == "repair_mapping":
            return self.preview_mapping_repair(entity_id)
        inspection = self.inspect(entity_id)
        action_state = inspection["actions"][normalized]
        preview = {
            "entity_id": entity_id,
            "action": normalized,
            "safe": bool(action_state["available"]),
            "reason": str(action_state.get("reason") or ""),
            "requires_client": normalized in CLIENT_LIFECYCLE_ACTIONS,
            "guard_reasons": inspection["guard_reasons"],
            "before": {
                "lifecycle_state": inspection["lifecycle_state"],
                "starred": inspection["agent"]["starred"],
                "archived": inspection["agent"]["archived"],
                "pbx_active": inspection["agent"]["pbx_active"],
                "runtime_state": inspection["runtime"]["state"],
                "codex_session_id": inspection["codex"]["session_id"],
            },
        }
        preview["preview_token"] = self._preview_token(preview)
        return preview

    def apply_action(
        self,
        entity_id: str,
        action: str,
        *,
        preview_token: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_action(action)
        if normalized == "repair_mapping":
            return self.apply_mapping_repair(
                entity_id,
                preview_token=preview_token,
                metadata=metadata,
            )
        preview = self.preview_action(entity_id, normalized)
        if not preview_token or preview_token != preview["preview_token"]:
            raise ValueError("lifecycle preview is stale")
        if not preview["safe"]:
            raise ValueError(preview["reason"] or "lifecycle action is guarded")
        if normalized in CLIENT_LIFECYCLE_ACTIONS:
            return {
                **preview,
                "applied": False,
                "requires_client": True,
                "message": "The controlling TUI must execute this local tmux/Codex action.",
            }
        if normalized == "star":
            updated = self.store.set_agent_starred(entity_id, starred=True)
        elif normalized == "unstar":
            updated = self.store.set_agent_starred(entity_id, starred=False)
        elif normalized == "archive":
            updated = self.store.dismiss_agent(entity_id, delete_thread=False)
        elif normalized == "unarchive":
            updated = self.store.unhide_agent(entity_id)
        elif normalized == "purge":
            updated = self.store.dismiss_agent(entity_id, delete_thread=True)
        elif normalized == "mark_canceled":
            updated = self._mark_canceled(entity_id, metadata=metadata)
        elif normalized == "prune":
            return {
                **preview,
                "applied": False,
                "message": "Use the existing prune preview/apply/undo batch API.",
            }
        else:  # pragma: no cover - normalized action exhausts server actions.
            raise ValueError("unsupported lifecycle action")
        if updated is None:
            raise ValueError("agent not found")
        event = {
            "entity_id": entity_id,
            "action": normalized,
            "metadata": metadata or {},
            "before": preview["before"],
        }
        self.store.append_event("lifecycle_action_applied", event, entity_id)
        return {
            **preview,
            "applied": True,
            "requires_client": False,
            "entity": updated,
            "after": self.inspect(entity_id),
        }

    def preview_mapping_repair(self, entity_id: str) -> dict[str, Any]:
        inspection = self.inspect(entity_id)
        mapping = self.store.get_tmux_runtime_mapping(entity_id)
        runtime = inspection["runtime"]
        repair = runtime.get("repair_candidate")
        safe = bool(
            mapping
            and isinstance(repair, dict)
            and runtime.get("state") == "moved"
            and not mapping.get("writer_lease_active")
        )
        if mapping is None:
            reason = "runtime mapping is not registered"
        elif mapping.get("writer_lease_active"):
            reason = "release the active runtime writer lease before repair"
        elif runtime.get("state") == "ready":
            reason = "runtime mapping already matches its pane"
        elif not isinstance(repair, dict):
            reason = str(runtime.get("message") or "no unique repair candidate")
        else:
            reason = "unique session/window/cwd candidate is ready for review"
        preview = {
            "entity_id": entity_id,
            "action": "repair_mapping",
            "safe": safe,
            "reason": reason,
            "requires_client": False,
            "before": self._mapping_identity(mapping),
            "candidate": repair,
        }
        preview["preview_token"] = self._preview_token(preview)
        return preview

    def apply_mapping_repair(
        self,
        entity_id: str,
        *,
        preview_token: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        preview = self.preview_mapping_repair(entity_id)
        if not preview_token or preview_token != preview["preview_token"]:
            raise ValueError("runtime repair preview is stale")
        if not preview["safe"]:
            raise ValueError(preview["reason"] or "runtime mapping cannot be repaired")
        mapping = self.store.get_tmux_runtime_mapping(entity_id)
        candidate = preview.get("candidate")
        if mapping is None or not isinstance(candidate, dict):
            raise ValueError("runtime mapping repair candidate disappeared")
        merged_metadata = dict(mapping.get("metadata") or {})
        merged_metadata.update(metadata or {})
        merged_metadata["last_repair"] = {
            "from_pane_id": mapping.get("pane_id"),
            "to_pane_id": candidate.get("pane_id"),
            "applied_at": time.time(),
        }
        updated = self.store.upsert_tmux_runtime_mapping(
            entity_id=entity_id,
            server_mode=str(mapping["server_mode"]),
            server_id=str(mapping["server_id"]),
            socket_path=str(mapping["socket_path"]),
            session_name=str(candidate["session_name"]),
            window_id=str(candidate["window_id"]),
            window_name=str(candidate["window_name"]),
            pane_id=str(candidate["pane_id"]),
            pane_pid=candidate.get("pane_pid"),
            process_start_ticks=candidate.get("process_start_ticks"),
            codex_session_id=mapping.get("codex_session_id"),
            cwd=str(candidate.get("cwd") or mapping.get("cwd") or ""),
            origin_client_tty=mapping.get("origin_client_tty"),
            origin_session_name=mapping.get("origin_session_name"),
            state="ready",
            metadata=merged_metadata,
        )
        self.store.append_event(
            "lifecycle_runtime_mapping_repaired",
            {
                "entity_id": entity_id,
                "before": preview["before"],
                "after": self._mapping_identity(updated),
            },
            entity_id,
        )
        return {
            **preview,
            "applied": True,
            "mapping": updated,
            "after": self.inspect(entity_id),
        }

    def _assess_mapping(self, mapping: dict[str, Any] | None) -> dict[str, Any]:
        if mapping is None:
            return {
                "registered": False,
                "state": "unregistered",
                "safe": False,
                "message": "runtime mapping is not registered",
                "repair_candidate": None,
            }
        socket_path = Path(str(mapping.get("socket_path") or ""))
        ready, message = validate_tmux_socket(socket_path)
        if not ready:
            return {
                "registered": True,
                "state": "server_lost",
                "safe": False,
                "message": message,
                "mapping": self._mapping_identity(mapping),
                "repair_candidate": None,
            }
        identity = runtime_mapping_server_identity(mapping)
        responsive, response_message, panes = probe_runtime_panes(identity)
        if not responsive:
            return {
                "registered": True,
                "state": "server_lost",
                "safe": False,
                "message": response_message,
                "mapping": self._mapping_identity(mapping),
                "repair_candidate": None,
            }
        selected = next(
            (pane for pane in panes if pane.pane_id == mapping.get("pane_id")),
            None,
        )
        ticks = process_start_ticks(selected.pane_pid if selected else None)
        assessment = assess_runtime_mapping(
            mapping,
            panes,
            observed_start_ticks=ticks,
        )
        candidate = next(
            (pane for pane in panes if pane.pane_id == assessment.repair_pane_id),
            None,
        )
        return {
            "registered": True,
            "state": assessment.state,
            "safe": assessment.safe,
            "message": assessment.message,
            "mapping": self._mapping_identity(mapping),
            "repair_candidate": self._pane_identity(candidate),
        }

    def _action_matrix(self, inspection: dict[str, Any]) -> dict[str, dict[str, Any]]:
        agent = inspection["agent"]
        runtime = inspection["runtime"]
        guards = inspection["guard_reasons"]
        archived = bool(agent["archived"])
        starred = bool(agent["starred"])
        active_runtime = runtime.get("state") == "ready"
        session_known = bool(inspection["codex"]["session_known"])
        workspace_ready = bool(inspection["workspace"]["available"])

        def option(available: bool, reason: str = "") -> dict[str, Any]:
            return {"available": available, "reason": "" if available else reason}

        archive_reasons = [*guards]
        if active_runtime:
            archive_reasons.append("runtime_ready")
        purge_reasons = [*guards]
        if runtime.get("registered"):
            purge_reasons.append("runtime_mapping_registered")
        if agent["pbx_active"]:
            purge_reasons.append("pbx_active")
        repair_ready = bool(runtime.get("repair_candidate")) and runtime.get("state") == "moved"
        return {
            "inspect": option(True),
            "star": option(not starred, "entity is already starred"),
            "unstar": option(starred, "entity is not starred"),
            "archive": option(
                not archived and not archive_reasons,
                ", ".join(sorted(set(archive_reasons))) or "entity is already archived",
            ),
            "unarchive": option(archived, "entity is not archived"),
            "mark_canceled": option(not archived, "entity is archived"),
            "resume": option(
                session_known and workspace_ready,
                "Codex session or workspace is unavailable",
            ),
            "restart_resume": option(
                session_known and workspace_ready,
                "Codex session or workspace is unavailable",
            ),
            "pop_in": option(active_runtime, "runtime mapping is not ready"),
            "pop_out": option(active_runtime, "runtime mapping is not ready"),
            "repair_mapping": option(repair_ready, runtime.get("message") or "no candidate"),
            "prune": option(
                archived and not guards,
                ", ".join(guards) or "archive the entity before pruning",
            ),
            "purge": option(
                archived and not purge_reasons,
                ", ".join(sorted(set(purge_reasons))) or "entity must be archived first",
            ),
        }

    def _mark_canceled(
        self,
        entity_id: str,
        *,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        agent = self.store.get_agent(entity_id)
        if agent is None:
            return None
        self.store.create_report(
            entity_id,
            ReportCreateRequest(
                project=str(agent.get("project") or ""),
                status="canceled",
                summary="Session marked canceled by operator",
                detail="The Agent PBX lifecycle service recorded this session as canceled.",
                metadata={"source": "lifecycle_v2", **(metadata or {})},
            ),
        )
        return self.store.set_agent_pbx_active(entity_id, False)

    @staticmethod
    def _workspace_status(path: str, *, fork: dict[str, Any] | None) -> dict[str, Any]:
        target = Path(path).expanduser() if path else None
        available = bool(target and target.is_dir())
        return {
            "path": str(target) if target else "",
            "available": available,
            "access_mode": str((fork or {}).get("access_mode") or "project"),
            "fork_purpose": (fork or {}).get("fork_purpose"),
        }

    @staticmethod
    def _codex_session_id(
        agent: dict[str, Any],
        *,
        fork: dict[str, Any] | None,
        mapping: dict[str, Any] | None,
    ) -> str | None:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        candidates: list[Any] = [
            (fork or {}).get("fork_codex_session_id"),
            (mapping or {}).get("codex_session_id"),
        ]
        candidates.extend(metadata.get(key) for key in CODEX_SESSION_KEYS)
        candidates.append((fork or {}).get("source_codex_session_id"))
        return next((str(value).strip() for value in candidates if str(value or "").strip()), None)

    @staticmethod
    def _entity_kind(agent: dict[str, Any], fork: dict[str, Any] | None) -> str:
        if fork:
            return "review_fork" if fork.get("fork_purpose") == "review" else "edit_fork"
        return "operator" if agent.get("agent_type") == "operator" else "agent"

    @staticmethod
    def _lifecycle_state(
        agent: dict[str, Any],
        runtime: dict[str, Any],
    ) -> LifecycleState:
        if agent.get("dismissed_at") is not None:
            return LifecycleState.ARCHIVED
        runtime_state = runtime.get("state")
        if runtime_state in {"missing", "server_lost"}:
            return LifecycleState.MISSING
        if runtime_state in {"moved", "foreign", "reused"}:
            return LifecycleState.STALE
        status = str(agent.get("effective_status") or agent.get("status") or "").lower()
        if status in {"running", "working", "in_progress", "in-progress"}:
            return LifecycleState.RUNNING
        if status in {"complete", "completed", "done", "ready", "canceled", "cancelled"}:
            return LifecycleState.STOPPED
        return LifecycleState.REGISTERED

    @staticmethod
    def _safe_fork(fork: dict[str, Any] | None) -> dict[str, Any] | None:
        if fork is None:
            return None
        return {
            key: fork.get(key)
            for key in (
                "operator_fork_id",
                "logical_operator_agent_id",
                "fork_agent_id",
                "source_caller_agent_id",
                "fork_track_id",
                "fork_purpose",
                "access_mode",
                "status",
                "campaign_id",
                "updated_at",
                "completed_at",
            )
        }

    @staticmethod
    def _mapping_identity(mapping: dict[str, Any] | None) -> dict[str, Any] | None:
        if mapping is None:
            return None
        return {
            key: mapping.get(key)
            for key in (
                "server_mode",
                "server_id",
                "socket_path",
                "session_name",
                "window_id",
                "window_name",
                "pane_id",
                "pane_pid",
                "process_start_ticks",
                "codex_session_id",
                "cwd",
                "state",
            )
        }

    @staticmethod
    def _pane_identity(pane: RuntimeTmuxPane | None) -> dict[str, Any] | None:
        if pane is None:
            return None
        return {
            "session_name": pane.session_name,
            "window_id": pane.window_id,
            "window_name": pane.window_name,
            "pane_id": pane.pane_id,
            "pane_pid": pane.pane_pid,
            "process_start_ticks": process_start_ticks(pane.pane_pid),
            "cwd": pane.cwd,
            "current_command": pane.current_command,
        }

    @staticmethod
    def _preview_token(payload: dict[str, Any]) -> str:
        stable = {key: value for key, value in payload.items() if key != "preview_token"}
        return hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _normalize_action(action: str) -> str:
        normalized = str(action or "").strip().lower().replace("-", "_")
        if normalized not in LIFECYCLE_ACTIONS:
            raise ValueError("unsupported lifecycle action")
        return normalized
