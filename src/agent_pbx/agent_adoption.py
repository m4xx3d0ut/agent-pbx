from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping

from .runtime_tmux import (
    RuntimeServerMode,
    TmuxServerIdentity,
    list_runtime_panes,
    process_start_ticks,
    resolve_runtime_tmux_server,
)
from .schemas import AgentRegisterRequest
from .store import Store
from .tmux_binary import configured_tmux_binary


DEFAULT_AGENT_RUNTIME_SESSION = "agent-pbx-agents"
DEFAULT_ADOPTION_RETENTION_DAYS = 7.0
SESSION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
WINDOW_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class PanePlacement:
    session_name: str
    window_id: str
    window_index: str
    window_name: str
    pane_id: str
    pane_index: str
    pane_pid: int
    pane_start_ticks: int | None
    pane_command: str
    pane_start_command: str
    cwd: str
    width: int
    height: int
    pane_active: bool
    window_zoomed: bool
    window_layout: str
    sibling_pane_ids: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "window_id": self.window_id,
            "window_index": self.window_index,
            "window_name": self.window_name,
            "pane_id": self.pane_id,
            "pane_index": self.pane_index,
            "pane_pid": self.pane_pid,
            "pane_start_ticks": self.pane_start_ticks,
            "pane_command": self.pane_command,
            "pane_start_command": self.pane_start_command,
            "cwd": self.cwd,
            "width": self.width,
            "height": self.height,
            "pane_active": self.pane_active,
            "window_zoomed": self.window_zoomed,
            "window_layout": self.window_layout,
            "sibling_pane_ids": list(self.sibling_pane_ids),
        }


PANE_PLACEMENT_FORMAT = "\t".join(
    (
        "#{session_name}",
        "#{window_id}",
        "#{window_index}",
        "#{window_name}",
        "#{pane_id}",
        "#{pane_index}",
        "#{pane_pid}",
        "#{pane_current_command}",
        "#{pane_start_command}",
        "#{pane_current_path}",
        "#{pane_width}",
        "#{pane_height}",
        "#{pane_active}",
        "#{window_zoomed_flag}",
        "#{window_layout}",
    )
)


def agent_codex_session_id(agent: Mapping[str, Any]) -> str:
    metadata = agent.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return str(
        metadata.get("fork_codex_session_id")
        or metadata.get("last_resume_codex_session_id")
        or metadata.get("codex_session_id")
        or metadata.get("codex_thread_id")
        or ""
    ).strip()


def agent_runtime_alias_target(agent: Mapping[str, Any]) -> str:
    metadata = agent.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    return str(metadata.get("runtime_alias_of") or "").strip()


def managed_agent_window_name(agent_id: str) -> str:
    normalized = WINDOW_NAME_PATTERN.sub("-", str(agent_id).strip()).strip(".-")
    return (normalized or "agent")[:120]


def _process_tree(root_pid: int, *, proc_root: Path = Path("/proc")) -> tuple[int, ...]:
    pending = [root_pid]
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            children = (
                proc_root / str(pid) / "task" / str(pid) / "children"
            ).read_text().split()
        except OSError:
            continue
        for child in children:
            try:
                pending.append(int(child))
            except ValueError:
                continue
    return tuple(sorted(seen))


def codex_session_ids_for_process_tree(
    root_pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[str, ...]:
    """Return session IDs proven by argv or open rollout files.

    Older interactive Codex launches often keep only ``resume`` in argv. Their
    active rollout JSONL remains open in a descendant process, so both evidence
    sources are required for migration identity checks.
    """

    session_ids: set[str] = set()
    for pid in _process_tree(root_pid, proc_root=proc_root):
        try:
            argv = [
                value.decode(errors="replace")
                for value in (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
                if value
            ]
        except OSError:
            argv = []
        session_ids.update(value for value in argv if SESSION_ID_PATTERN.fullmatch(value))
        fd_root = proc_root / str(pid) / "fd"
        try:
            descriptors = tuple(fd_root.iterdir())
        except OSError:
            descriptors = ()
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if "/.codex/sessions/" not in target or not target.endswith(".jsonl"):
                continue
            matches = re.findall(
                r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                Path(target).name,
                flags=re.IGNORECASE,
            )
            session_ids.update(matches)
    return tuple(sorted(session_ids))


def current_codex_session_ids_for_process_tree(
    root_pid: int,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[str, ...]:
    """Return the current rollout IDs, excluding fork source argv when possible.

    ``codex fork <source-id>`` and related continuation commands retain the
    source ID in argv while writing a distinct rollout for the new session.
    A writable rollout descriptor is therefore stronger current-session
    evidence than argv.  This keeps source sessions from looking duplicated by
    every live fork that descended from them.
    """

    argv_ids: set[str] = set()
    rollout_ids: set[str] = set()
    writable_rollout_ids: set[str] = set()
    for pid in _process_tree(root_pid, proc_root=proc_root):
        try:
            argv = [
                value.decode(errors="replace")
                for value in (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
                if value
            ]
        except OSError:
            argv = []
        argv_ids.update(value for value in argv if SESSION_ID_PATTERN.fullmatch(value))
        fd_root = proc_root / str(pid) / "fd"
        try:
            descriptors = tuple(fd_root.iterdir())
        except OSError:
            descriptors = ()
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if "/.codex/sessions/" not in target or not target.endswith(".jsonl"):
                continue
            matches = re.findall(
                r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                Path(target).name,
                flags=re.IGNORECASE,
            )
            rollout_ids.update(matches)
            try:
                flags_line = next(
                    line
                    for line in (
                        proc_root / str(pid) / "fdinfo" / descriptor.name
                    ).read_text().splitlines()
                    if line.startswith("flags:")
                )
                flags = int(flags_line.split()[1], 8)
            except (OSError, StopIteration, ValueError):
                continue
            if flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}:
                writable_rollout_ids.update(matches)
    if writable_rollout_ids:
        return tuple(sorted(writable_rollout_ids))
    if rollout_ids:
        return tuple(sorted(rollout_ids))
    return tuple(sorted(argv_ids))


class AgentPaneAdoptionService:
    """Move existing caller panes into one-pane managed runtime windows."""

    def __init__(self, store: Store, *, tmux_bin: str = "tmux") -> None:
        self.store = store
        self.tmux_bin = configured_tmux_binary(tmux_bin)

    def preview(
        self,
        *,
        agent_id: str,
        pane_id: str = "",
        runtime_server_mode: str = "outer_if_present",
        destination_session: str = DEFAULT_AGENT_RUNTIME_SESSION,
        origin_session_name: str | None = None,
        origin_client_tty: str | None = None,
        retention_days: float = DEFAULT_ADOPTION_RETENTION_DAYS,
    ) -> dict[str, Any]:
        plan = self._build_plan(
            agent_id=agent_id,
            pane_id=pane_id,
            runtime_server_mode=runtime_server_mode,
            destination_session=destination_session,
            origin_session_name=origin_session_name,
            origin_client_tty=origin_client_tty,
        )
        batch = self.store.create_runtime_migration_batch(
            target_cli_version="pane-preserving-adoption",
            retention_until=time.time() + max(1.0, retention_days) * 86400,
            snapshot={"kind": "agent_pane_adoption", "plan": plan},
        )
        return {**batch, "plan": plan}

    def apply(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch(batch_id, expected_status="previewed")
        plan = self._plan(batch)
        if plan.get("blockers"):
            raise ValueError("; ".join(str(item) for item in plan["blockers"]))
        current = self._build_plan(
            agent_id=str(plan["agent_id"]),
            pane_id=str(plan["pane_id"]),
            runtime_server_mode=str(plan["runtime_server_mode"]),
            destination_session=str(plan["destination_session"]),
            origin_session_name=plan.get("origin_session_name"),
            origin_client_tty=plan.get("origin_client_tty"),
        )
        if current.get("blockers"):
            raise ValueError("; ".join(str(item) for item in current["blockers"]))
        if current["source_signature"] != plan["source_signature"]:
            raise ValueError("source pane identity or placement changed after preview")
        if current.get("already_adopted"):
            mapping = self._register_mapping(current)
            result = {
                "agent_id": current["agent_id"],
                "status": "complete",
                "already_adopted": True,
                "mapping": mapping,
            }
            updated = self.store.update_runtime_migration_batch(
                batch_id,
                status="complete",
                results=[result],
                applied=True,
            )
            return {"batch": updated, "result": result}

        identity = self._identity(current)
        created_session = self._ensure_destination_session(
            identity,
            str(current["destination_session"]),
        )
        destination = self._break_pane(identity, current)
        try:
            self._verify_destination(current, destination)
            mapping = self._register_mapping({**current, "destination": destination})
            self._mark_agent_adopted(current, mapping)
        except Exception:
            self._restore_placement(identity, current, destination)
            raise
        if created_session:
            self._remove_control_window(identity, str(current["destination_session"]))
        result = {
            "agent_id": current["agent_id"],
            "status": "complete",
            "pane_id": destination["pane_id"],
            "pane_pid": destination["pane_pid"],
            "session_name": destination["session_name"],
            "window_id": destination["window_id"],
            "window_name": destination["window_name"],
            "codex_session_id": current["codex_session_id"],
            "mapping": mapping,
        }
        updated = self.store.update_runtime_migration_batch(
            batch_id,
            status="complete",
            results=[result],
            applied=True,
        )
        self.store.append_event("agent_pane_adopted", result, str(current["agent_id"]))
        return {"batch": updated, "result": result}

    def rollback(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch(batch_id)
        if batch.get("rolled_back_at") is not None:
            raise ValueError("Agent pane adoption was already rolled back")
        if float(batch.get("retention_until") or 0) < time.time():
            raise ValueError("Agent pane adoption rollback retention has expired")
        plan = self._plan(batch)
        identity = self._identity(plan)
        placement = self._pane_placement(identity, str(plan["pane_id"]))
        if placement.pane_pid != int(plan["source"]["pane_pid"]):
            raise ValueError("pane PID changed; refusing placement rollback")
        expected_ticks = plan["source"].get("pane_start_ticks")
        if expected_ticks and placement.pane_start_ticks != int(expected_ticks):
            raise ValueError("pane process start evidence changed; refusing rollback")
        destination = placement.public_dict()
        if placement.session_name != str(plan["destination_session"]):
            raise ValueError("pane is no longer in the managed Agent session")
        self._restore_placement(identity, plan, destination)
        self._restore_record(plan)
        result = {
            "agent_id": plan["agent_id"],
            "status": "restored",
            "pane_id": plan["pane_id"],
            "session_name": plan["source"]["session_name"],
            "window_id": plan["source"]["window_id"],
        }
        updated = self.store.update_runtime_migration_batch(
            batch_id,
            status="rolled_back",
            results=[result],
            rolled_back=True,
        )
        self.store.append_event(
            "agent_pane_adoption_rolled_back", result, str(plan["agent_id"])
        )
        return {"batch": updated, "result": result}

    def _build_plan(
        self,
        *,
        agent_id: str,
        pane_id: str,
        runtime_server_mode: str,
        destination_session: str,
        origin_session_name: str | None,
        origin_client_tty: str | None,
    ) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError("Agent not found")
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        mapping = self.store.get_tmux_runtime_mapping(agent_id)
        selected_pane_id = str(
            pane_id
            or metadata.get("tmux_pane_id")
            or (mapping or {}).get("pane_id")
            or ""
        ).strip()
        identity = resolve_runtime_tmux_server(runtime_server_mode)
        blockers: list[str] = []
        warnings: list[str] = []
        if not identity.ready:
            blockers.append(identity.message or "runtime tmux server is unavailable")
        if str(agent.get("agent_type") or "caller") != "caller":
            blockers.append("pane adoption supports caller Agents only")
        alias_target = agent_runtime_alias_target(agent)
        if alias_target:
            blockers.append(
                f"Agent is a runtime alias of {alias_target!r}; adopt the canonical identity"
            )
        session_id = agent_codex_session_id(agent)
        if not session_id:
            blockers.append("Agent has no Codex session ID")
        if not selected_pane_id:
            blockers.append("Agent has no selected tmux pane")
        panes = list_runtime_panes(identity) if identity.ready else ()
        pane = next((item for item in panes if item.pane_id == selected_pane_id), None)
        source: dict[str, Any] | None = None
        observed_session_ids: tuple[str, ...] = ()
        if pane is None and selected_pane_id:
            blockers.append("selected tmux pane is absent from the runtime server")
        elif pane is not None:
            placement = self._pane_placement(identity, pane.pane_id)
            source = placement.public_dict()
            observed_session_ids = current_codex_session_ids_for_process_tree(
                placement.pane_pid
            )
            if observed_session_ids and session_id not in observed_session_ids:
                blockers.append(
                    "pane session evidence does not match the Agent Codex session ID"
                )
            duplicate_panes = []
            if session_id:
                for candidate in panes:
                    candidate_ids = current_codex_session_ids_for_process_tree(
                        int(candidate.pane_pid or 0)
                    )
                    if session_id in candidate_ids and candidate.pane_id != pane.pane_id:
                        duplicate_panes.append(candidate.pane_id)
            if duplicate_panes:
                blockers.append(
                    "Codex session is live in multiple panes: "
                    + ", ".join([pane.pane_id, *sorted(duplicate_panes)])
                )
        duplicate_records = [
            item
            for item in self.store.list_agents(include_hidden=True)
            if str(item.get("agent_id") or "") != agent_id
            and session_id
            and agent_codex_session_id(item) == session_id
        ]
        declared_aliases = sorted(
            str(item.get("agent_id") or "")
            for item in duplicate_records
            if agent_runtime_alias_target(item) == agent_id
        )
        duplicate_agents = sorted(
            str(item.get("agent_id") or "")
            for item in duplicate_records
            if agent_runtime_alias_target(item) != agent_id
        )
        if declared_aliases:
            warnings.append(
                "declared runtime aliases share this session: "
                + ", ".join(declared_aliases)
            )
        if duplicate_agents:
            blockers.append(
                "Codex session is claimed by other PBX identities: "
                + ", ".join(duplicate_agents)
            )
        conflicting_mappings = sorted(
            str(item.get("entity_id") or "")
            for item in self.store.list_tmux_runtime_mappings()
            if str(item.get("entity_id") or "") != agent_id
            and (
                (selected_pane_id and item.get("pane_id") == selected_pane_id)
                or (session_id and item.get("codex_session_id") == session_id)
            )
        )
        if conflicting_mappings:
            blockers.append(
                "runtime is owned by other PBX entities: "
                + ", ".join(conflicting_mappings)
            )
        if mapping and mapping.get("writer_lease_active"):
            blockers.append(
                f"runtime writer is leased to {mapping.get('writer_client_id')!r}"
            )
        destination_session = str(destination_session or "").strip()
        if not destination_session:
            blockers.append("destination tmux session is empty")
        resolved_origin_session = str(
            origin_session_name or (mapping or {}).get("origin_session_name") or ""
        ).strip()
        resolved_origin_tty = str(
            origin_client_tty or (mapping or {}).get("origin_client_tty") or ""
        ).strip()
        if destination_session and destination_session == resolved_origin_session:
            blockers.append("destination is the session containing the Agent PBX TUI")
        already_adopted = bool(
            source and source["session_name"] == destination_session
        )
        if already_adopted and len(source.get("sibling_pane_ids", [])) > 0:
            blockers.append("managed Agent window contains more than one pane")
        if source and len(source.get("sibling_pane_ids", [])) == 0 and not already_adopted:
            blockers.append(
                "source window has no sibling pane; a placement rollback anchor is required"
            )
        source_signature = {
            key: source.get(key) if source else None
            for key in (
                "session_name",
                "window_id",
                "window_name",
                "pane_id",
                "pane_pid",
                "pane_start_ticks",
                "cwd",
            )
        }
        return {
            "kind": "agent_pane_adoption",
            "agent_id": agent_id,
            "pane_id": selected_pane_id,
            "codex_session_id": session_id,
            "observed_session_ids": list(observed_session_ids),
            "runtime_server_mode": identity.effective_mode.value,
            "runtime_server": identity.public_dict(),
            "destination_session": destination_session,
            "destination_window_name": managed_agent_window_name(agent_id),
            "origin_session_name": resolved_origin_session or None,
            "origin_client_tty": resolved_origin_tty or None,
            "already_adopted": already_adopted,
            "eligible": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "declared_runtime_aliases": declared_aliases,
            "source_signature": source_signature,
            "source": source,
            "snapshot": {
                "agent": agent,
                "runtime_mapping": mapping,
            },
        }

    def _identity(self, plan: Mapping[str, Any]) -> TmuxServerIdentity:
        data = plan.get("runtime_server")
        if not isinstance(data, Mapping):
            raise ValueError("migration plan has no runtime server identity")
        return TmuxServerIdentity(
            RuntimeServerMode(str(data["requested_mode"])),
            RuntimeServerMode(str(data["effective_mode"])),
            str(data["server_id"]),
            str(data["socket_path"]),
            bool(data["ready"]),
            bool(data["outer_detected"]),
            str(data.get("message") or ""),
        )

    def _pane_placement(
        self,
        identity: TmuxServerIdentity,
        pane_id: str,
    ) -> PanePlacement:
        result = self._run(
            identity,
            "display-message",
            "-p",
            "-t",
            pane_id,
            PANE_PLACEMENT_FORMAT,
        )
        parts = result.stdout.rstrip("\n").split("\t")
        if len(parts) != 15:
            raise ValueError("unable to inspect tmux pane placement")
        sibling_result = self._run(
            identity,
            "list-panes",
            "-t",
            parts[1],
            "-F",
            "#{pane_id}",
        )
        siblings = tuple(
            value
            for value in sibling_result.stdout.splitlines()
            if value and value != parts[4]
        )
        return PanePlacement(
            session_name=parts[0],
            window_id=parts[1],
            window_index=parts[2],
            window_name=parts[3],
            pane_id=parts[4],
            pane_index=parts[5],
            pane_pid=int(parts[6]),
            pane_start_ticks=process_start_ticks(int(parts[6])),
            pane_command=parts[7],
            pane_start_command=parts[8],
            cwd=parts[9],
            width=int(parts[10]),
            height=int(parts[11]),
            pane_active=parts[12] == "1",
            window_zoomed=parts[13] == "1",
            window_layout=parts[14],
            sibling_pane_ids=siblings,
        )

    def _ensure_destination_session(
        self,
        identity: TmuxServerIdentity,
        session_name: str,
    ) -> bool:
        present = subprocess.run(
            [*identity.command_prefix, "has-session", "-t", session_name],
            capture_output=True,
            text=True,
        )
        if present.returncode == 0:
            return False
        self._run(
            identity,
            "new-session",
            "-d",
            "-s",
            session_name,
            "-n",
            "__control",
            "sleep 2147483647",
        )
        return True

    def _break_pane(
        self,
        identity: TmuxServerIdentity,
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = self._run(
            identity,
            "break-pane",
            "-d",
            "-s",
            str(plan["pane_id"]),
            "-t",
            f"{plan['destination_session']}:",
            "-n",
            str(plan["destination_window_name"]),
            "-P",
            "-F",
            "#{session_name}\t#{window_id}\t#{window_name}\t#{pane_id}\t#{pane_pid}",
        )
        parts = result.stdout.rstrip("\n").split("\t")
        if len(parts) != 5:
            raise RuntimeError("tmux did not report the adopted pane placement")
        placement = self._pane_placement(identity, parts[3]).public_dict()
        return placement

    def _verify_destination(
        self,
        plan: Mapping[str, Any],
        destination: Mapping[str, Any],
    ) -> None:
        source = plan["source"]
        for key in ("pane_id", "pane_pid", "pane_start_ticks", "cwd"):
            if destination.get(key) != source.get(key):
                raise RuntimeError(f"pane adoption changed {key}")
        if destination.get("session_name") != plan.get("destination_session"):
            raise RuntimeError("pane adoption selected the wrong tmux session")
        if destination.get("sibling_pane_ids"):
            raise RuntimeError("adopted Agent window is not a single-pane window")
        observed = current_codex_session_ids_for_process_tree(
            int(destination["pane_pid"])
        )
        expected = str(plan.get("codex_session_id") or "")
        if observed and expected not in observed:
            raise RuntimeError("Codex session evidence changed during pane adoption")

    def _register_mapping(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        destination = plan.get("destination") or plan.get("source")
        if not isinstance(destination, Mapping):
            raise ValueError("pane adoption has no destination placement")
        identity = self._identity(plan)
        return self.store.upsert_tmux_runtime_mapping(
            entity_id=str(plan["agent_id"]),
            server_mode=identity.effective_mode.value,
            server_id=identity.server_id,
            socket_path=identity.socket_path,
            session_name=str(destination["session_name"]),
            window_id=str(destination["window_id"]),
            window_name=str(destination["window_name"]),
            pane_id=str(destination["pane_id"]),
            pane_pid=int(destination["pane_pid"]),
            process_start_ticks=destination.get("pane_start_ticks"),
            codex_session_id=str(plan.get("codex_session_id") or "") or None,
            cwd=str(destination.get("cwd") or "") or None,
            origin_client_tty=plan.get("origin_client_tty"),
            origin_session_name=plan.get("origin_session_name"),
            state="ready",
            metadata={
                "managed_by": "agent-pbx",
                "mapping_source": "pane_adoption",
                "popped_in": True,
            },
        )

    def _mark_agent_adopted(
        self,
        plan: Mapping[str, Any],
        mapping: Mapping[str, Any],
    ) -> None:
        agent = self.store.get_agent(str(plan["agent_id"]))
        if agent is None:
            raise ValueError("Agent disappeared during pane adoption")
        metadata = dict(agent.get("metadata") or {})
        metadata.update(
            {
                "tmux_pane_id": mapping["pane_id"],
                "tmux_session": mapping["session_name"],
                "tmux_window_id": mapping.get("window_id"),
                "tmux_window_name": mapping.get("window_name"),
                "native_tmux_state": "popped_in",
                "native_tmux_adopted_at": time.time(),
            }
        )
        self.store.register_agent(
            AgentRegisterRequest(
                agent_id=str(agent["agent_id"]),
                project=str(agent.get("project") or "agent-pbx"),
                name=agent.get("name"),
                agent_type="caller",
                pbx_active=bool(agent.get("pbx_active", True)),
                metadata=metadata,
            )
        )

    def _restore_placement(
        self,
        identity: TmuxServerIdentity,
        plan: Mapping[str, Any],
        destination: Mapping[str, Any],
    ) -> None:
        source = plan.get("source")
        if not isinstance(source, Mapping):
            raise RuntimeError("adoption snapshot has no source placement")
        siblings = [str(item) for item in source.get("sibling_pane_ids", []) if item]
        anchor = next(
            (
                pane_id
                for pane_id in siblings
                if subprocess.run(
                    [*identity.command_prefix, "display-message", "-p", "-t", pane_id, "#{pane_id}"],
                    capture_output=True,
                    text=True,
                ).returncode
                == 0
            ),
            None,
        )
        if anchor is None:
            raise RuntimeError("source window has no surviving anchor pane")
        self._run(
            identity,
            "join-pane",
            "-d",
            "-s",
            str(destination["pane_id"]),
            "-t",
            anchor,
        )
        self._run(
            identity,
            "select-layout",
            "-t",
            anchor,
            str(source["window_layout"]),
        )
        if source.get("pane_active"):
            self._run(identity, "select-pane", "-t", str(source["pane_id"]))
        if source.get("window_zoomed"):
            self._run(identity, "resize-pane", "-Z", "-t", str(source["pane_id"]))

    def _restore_record(self, plan: Mapping[str, Any]) -> None:
        snapshot = plan.get("snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("pane adoption has no PBX snapshot")
        agent = snapshot.get("agent")
        if not isinstance(agent, Mapping):
            raise ValueError("pane adoption has no Agent snapshot")
        self.store.register_agent(
            AgentRegisterRequest(
                agent_id=str(agent["agent_id"]),
                project=str(agent.get("project") or "agent-pbx"),
                name=agent.get("name"),
                agent_type=str(agent.get("agent_type") or "caller"),
                pbx_active=bool(agent.get("pbx_active", True)),
                metadata=dict(agent.get("metadata") or {}),
            )
        )
        mapping = snapshot.get("runtime_mapping")
        if not isinstance(mapping, Mapping):
            self.store.delete_tmux_runtime_mapping(str(agent["agent_id"]))
            return
        self.store.upsert_tmux_runtime_mapping(
            entity_id=str(mapping["entity_id"]),
            server_mode=str(mapping["server_mode"]),
            server_id=str(mapping["server_id"]),
            socket_path=str(mapping["socket_path"]),
            session_name=str(mapping["session_name"]),
            window_id=mapping.get("window_id"),
            window_name=mapping.get("window_name"),
            pane_id=str(mapping["pane_id"]),
            pane_pid=mapping.get("pane_pid"),
            process_start_ticks=mapping.get("process_start_ticks"),
            codex_session_id=mapping.get("codex_session_id"),
            cwd=mapping.get("cwd"),
            origin_client_tty=mapping.get("origin_client_tty"),
            origin_session_name=mapping.get("origin_session_name"),
            state=str(mapping.get("state") or "ready"),
            metadata=dict(mapping.get("metadata") or {}),
        )

    def _remove_control_window(
        self,
        identity: TmuxServerIdentity,
        session_name: str,
    ) -> None:
        subprocess.run(
            [*identity.command_prefix, "kill-window", "-t", f"{session_name}:__control"],
            capture_output=True,
            text=True,
        )

    def _batch(
        self,
        batch_id: str,
        *,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        batch = self.store.get_runtime_migration_batch(batch_id)
        if batch is None:
            raise ValueError("Agent pane adoption batch not found")
        if expected_status and batch.get("status") != expected_status:
            raise ValueError(
                f"Agent pane adoption is {batch.get('status')}, expected {expected_status}"
            )
        snapshot = batch.get("snapshot")
        if not isinstance(snapshot, Mapping) or snapshot.get("kind") != "agent_pane_adoption":
            raise ValueError("runtime migration batch is not an Agent pane adoption")
        return batch

    @staticmethod
    def _plan(batch: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = batch.get("snapshot")
        plan = snapshot.get("plan") if isinstance(snapshot, Mapping) else None
        if not isinstance(plan, dict):
            raise ValueError("Agent pane adoption plan is missing")
        return plan

    def _run(
        self,
        identity: TmuxServerIdentity,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.tmux_bin, "-S", identity.socket_path, *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "tmux command failed"
            raise RuntimeError(detail)
        return result
