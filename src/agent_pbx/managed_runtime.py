from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping
import uuid

from .codex.profiles import (
    DEFAULT_CALLER_PROFILE_ID,
    MANAGED_CODEX_PROFILES,
    validate_managed_profile,
)
from .codex_cli import CodexModelOption, inspect_codex_model_catalog
from .runtime_tmux import (
    ensure_dedicated_runtime_server,
    ensure_runtime_socket_parent,
    RuntimeServerMode,
    TmuxServerIdentity,
    list_runtime_panes,
    process_start_ticks,
    resolve_runtime_tmux_server,
    restore_dedicated_runtime_exit_policy,
    validate_tmux_socket,
)
from .schemas import AgentRegisterRequest
from .store import Store
from .tmux_binary import configured_tmux_binary


PROJECT_ROOTS_ENV = "AGENT_PBX_PROJECT_ROOTS"
DEFAULT_MIGRATION_RETENTION_DAYS = 7.0
AGENT_ID_PATTERN = re.compile(r"[^a-z0-9_.-]+")


@dataclass(frozen=True)
class ManagedProject:
    name: str
    path: str
    git_common_dir: str
    branch: str
    owned_by: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "git_common_dir": self.git_common_dir,
            "branch": self.branch,
            "owned_by": list(self.owned_by),
            "available": not self.owned_by,
        }


def default_managed_project_roots(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[Path, ...]:
    env = os.environ if environ is None else environ
    configured = str(env.get(PROJECT_ROOTS_ENV) or "").strip()
    if configured:
        candidates = [Path(item).expanduser() for item in configured.split(os.pathsep)]
    else:
        current = (cwd or Path.cwd()).expanduser().resolve(strict=False)
        candidates = [current.parent if (current / ".git").exists() else current]
    roots: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def normalize_managed_agent_id(value: str, *, project_name: str) -> str:
    raw = str(value or "").strip().casefold() or f"codex-{project_name.casefold()}"
    normalized = AGENT_ID_PATTERN.sub("-", raw).strip(".-")
    if not normalized:
        raise ValueError("agent id is empty after normalization")
    return normalized[:120].rstrip(".-")


class ManagedRuntimeService:
    """Transactional project discovery, caller launch, and migration snapshots."""

    def __init__(
        self,
        store: Store,
        *,
        project_roots: Iterable[Path] | None = None,
        codex_bin: str | None = None,
        tmux_bin: str | None = None,
        catalog_loader: Callable[[str], tuple[CodexModelOption, ...]] | None = None,
    ) -> None:
        self.store = store
        self.project_roots = tuple(
            path.expanduser().resolve(strict=False)
            for path in (project_roots or default_managed_project_roots())
        )
        self.codex_bin = codex_bin or shutil.which("codex") or "codex"
        self.tmux_bin = configured_tmux_binary(tmux_bin)
        self.catalog_loader = catalog_loader or (
            lambda command: inspect_codex_model_catalog(command)
        )

    def public_roots(self) -> list[str]:
        return [str(path) for path in self.project_roots]

    def discover_projects(self, *, root: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        roots = self._selected_roots(root)
        discovered: dict[Path, ManagedProject] = {}
        ownership = self._cwd_ownership()
        for allowed_root in roots:
            candidates = [allowed_root]
            try:
                candidates.extend(path for path in allowed_root.iterdir() if path.is_dir())
            except OSError:
                continue
            for candidate in candidates:
                project = self._inspect_git_project(candidate, ownership=ownership)
                if project is not None:
                    discovered[Path(project.path)] = project
                if len(discovered) >= max(1, min(1000, int(limit))):
                    break
        return [
            project.public_dict()
            for project in sorted(discovered.values(), key=lambda item: (item.name, item.path))
        ]

    def preview_launch(
        self,
        *,
        project_path: str,
        agent_id: str = "",
        profile_id: str = DEFAULT_CALLER_PROFILE_ID,
        runtime_server_mode: str = "dedicated",
    ) -> dict[str, Any]:
        path = self._validated_project_path(project_path)
        project_name = path.name
        resolved_agent_id = normalize_managed_agent_id(agent_id, project_name=project_name)
        if self.store.get_agent(resolved_agent_id) is not None:
            raise ValueError(f"agent id {resolved_agent_id!r} already exists")
        owners = self._cwd_ownership().get(path, ())
        if owners:
            raise ValueError(
                f"project is already owned by active Agent PBX entity: {', '.join(owners)}"
            )
        profile = MANAGED_CODEX_PROFILES.get(profile_id)
        if profile is None:
            raise ValueError(f"unknown managed Codex profile: {profile_id}")
        if profile.elevated:
            raise ValueError("elevated profiles require an approved model lease")
        if profile.migration_only:
            raise ValueError("migration-only profiles cannot launch new agents")
        catalog = self.catalog_loader(self.codex_bin)
        validation = validate_managed_profile(profile, catalog)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        identity = resolve_runtime_tmux_server(runtime_server_mode)
        if not identity.ready:
            raise ValueError(identity.message or "runtime tmux server is unavailable")
        session_name = self._runtime_session_name(resolved_agent_id)
        return {
            "agent_id": resolved_agent_id,
            "project": project_name,
            "project_path": str(path),
            "profile_id": profile.profile_id,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "verbosity": profile.verbosity,
            "reasoning_summary": profile.reasoning_summary,
            "runtime_server": identity.public_dict(),
            "session_name": session_name,
            "command": [self.codex_bin, *self._profile_argv(profile.overrides())],
            "warnings": list(validation.warnings),
        }

    def launch(
        self,
        *,
        project_path: str,
        agent_id: str = "",
        profile_id: str = DEFAULT_CALLER_PROFILE_ID,
        runtime_server_mode: str = "dedicated",
        server_url: str = "http://127.0.0.1:8765",
        token: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview_launch(
            project_path=project_path,
            agent_id=agent_id,
            profile_id=profile_id,
            runtime_server_mode=runtime_server_mode,
        )
        identity_data = preview["runtime_server"]
        identity = TmuxServerIdentity(
            RuntimeServerMode(identity_data["requested_mode"]),
            RuntimeServerMode(identity_data["effective_mode"]),
            str(identity_data["server_id"]),
            str(identity_data["socket_path"]),
            bool(identity_data["ready"]),
            bool(identity_data["outer_detected"]),
            str(identity_data.get("message") or ""),
            str(identity_data.get("tmux_bin") or self.tmux_bin),
        )
        socket_path = Path(identity.socket_path)
        dedicated_server_started = False
        if identity.effective_mode is RuntimeServerMode.DEDICATED:
            ensure_runtime_socket_parent(socket_path)
            dedicated_server_started = ensure_dedicated_runtime_server(identity)
        env = {
            "AGENT_PBX_AGENT_ID": str(preview["agent_id"]),
            "AGENT_PBX_SERVER_URL": server_url.rstrip("/"),
            "AGENT_PBX_MODE": "report",
        }
        if token:
            env["AGENT_PBX_TOKEN"] = token
        command = [
            self.tmux_bin,
            "-S",
            identity.socket_path,
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            str(preview["session_name"]),
            "-n",
            "codex",
            "-c",
            str(preview["project_path"]),
        ]
        for key, value in env.items():
            command.extend(("-e", f"{key}={value}"))
        command.append(shlex.join(str(item) for item in preview["command"]))
        try:
            launched = subprocess.run(command, capture_output=True, text=True)
            if dedicated_server_started:
                restore_dedicated_runtime_exit_policy(identity)
        except Exception:
            if dedicated_server_started:
                subprocess.run(
                    [*identity.command_prefix, "kill-server"],
                    capture_output=True,
                    text=True,
                )
            raise
        if launched.returncode != 0:
            raise RuntimeError(
                (launched.stderr or launched.stdout or "tmux launch failed").strip()
            )
        pane_id = launched.stdout.strip().splitlines()[-1] if launched.stdout.strip() else ""
        if not pane_id:
            self._kill_session(identity, str(preview["session_name"]))
            raise RuntimeError("tmux did not return a managed pane id")
        try:
            ready, message = validate_tmux_socket(socket_path)
            if not ready:
                raise RuntimeError(message)
            pane = next(
                (item for item in list_runtime_panes(identity) if item.pane_id == pane_id),
                None,
            )
            if pane is None:
                raise RuntimeError("launched pane was not visible on the runtime server")
            metadata = {
                "cwd": str(preview["project_path"]),
                "launched_by": "agent-pbx-managed-runtime",
                "tmux_pane_id": pane_id,
                "tmux_session": str(preview["session_name"]),
                "runtime_server_id": identity.server_id,
                "runtime_server_mode": identity.effective_mode.value,
                "codex_command": self.codex_bin,
                "managed_profile_id": profile_id,
                "model": preview["model"],
                "model_reasoning_effort": preview["reasoning_effort"],
                "model_verbosity": preview["verbosity"],
                "model_reasoning_summary": preview["reasoning_summary"],
                "codex_model_preset": {
                    "sol-high": "sol-5.6-high",
                    "sol-xhigh": "sol-5.6-xhigh",
                    "sol-max": "sol-5.6-max",
                    "terra-xhigh": "terra-5.6-xhigh",
                    "terra-max": "terra-5.6-max",
                    "codex-5.5-xhigh": "legacy-5.5-xhigh",
                }.get(profile_id, profile_id),
                "codex_model": preview["model"],
                "codex_model_reasoning_effort": preview["reasoning_effort"],
                "codex_model_verbosity": preview["verbosity"],
                "codex_model_reasoning_summary": preview["reasoning_summary"],
            }
            agent, mapping = self.store.create_managed_agent_runtime(
                AgentRegisterRequest(
                    agent_id=str(preview["agent_id"]),
                    project=str(preview["project"]),
                    name=str(preview["project"]),
                    metadata=metadata,
                ),
                runtime_mapping={
                    "server_mode": identity.effective_mode.value,
                    "server_id": identity.server_id,
                    "socket_path": identity.socket_path,
                    "session_name": pane.session_name,
                    "window_id": pane.window_id,
                    "window_name": pane.window_name,
                    "pane_id": pane.pane_id,
                    "pane_pid": pane.pane_pid,
                    "process_start_ticks": process_start_ticks(pane.pane_pid),
                    "cwd": pane.cwd,
                    "state": "ready",
                    "metadata": {
                        "managed_launch": True,
                        "profile_id": profile_id,
                        "tmux_bin": self.tmux_bin,
                    },
                },
            )
        except Exception:
            self._kill_session(identity, str(preview["session_name"]))
            raise
        self.store.append_event(
            "managed_agent_launched",
            {
                "agent_id": preview["agent_id"],
                "project": preview["project"],
                "pane_id": pane_id,
                "profile_id": profile_id,
                "server_id": identity.server_id,
            },
            str(preview["agent_id"]),
        )
        return {**preview, "agent": agent, "runtime_mapping": mapping, "launched": True}

    def preview_migration(
        self,
        *,
        agent_ids: Iterable[str] = (),
        include_starred: bool = True,
        include_operators: bool = True,
        target_cli_version: str = "",
        retention_days: float = DEFAULT_MIGRATION_RETENTION_DAYS,
    ) -> dict[str, Any]:
        requested = {str(item).strip() for item in agent_ids if str(item).strip()}
        agents = self.store.list_agents(include_hidden=True)
        selected: list[dict[str, Any]] = []
        for agent in agents:
            agent_id = str(agent["agent_id"])
            if requested and agent_id not in requested:
                continue
            if not requested and not (
                (include_starred and bool(agent.get("starred")))
                or (include_operators and agent.get("agent_type") == "operator")
            ):
                continue
            mapping = self.store.get_tmux_runtime_mapping(agent_id)
            metadata = dict(agent.get("metadata") or {})
            session_id = str(
                metadata.get("fork_codex_session_id")
                or metadata.get("last_resume_codex_session_id")
                or metadata.get("codex_session_id")
                or metadata.get("codex_thread_id")
                or ""
            ).strip()
            blockers: list[str] = []
            if mapping is None:
                blockers.append("no runtime mapping")
            elif str(mapping.get("state") or "") != "ready":
                blockers.append(f"runtime mapping is {mapping.get('state')}")
            if not session_id:
                blockers.append("no Codex resume session id")
            selected.append(
                {
                    "agent_id": agent_id,
                    "agent_type": agent.get("agent_type"),
                    "project": agent.get("project"),
                    "starred": bool(agent.get("starred")),
                    "session_id": session_id or None,
                    "mapping": mapping,
                    "blockers": blockers,
                    "eligible": not blockers,
                    "snapshot": {
                        "agent": {
                            "agent_id": agent_id,
                            "project": agent.get("project"),
                            "name": agent.get("name"),
                            "agent_type": agent.get("agent_type"),
                            "pbx_active": bool(agent.get("pbx_active", True)),
                            "metadata": metadata,
                        },
                        "runtime_mapping": mapping,
                    },
                }
            )
        missing = sorted(requested - {str(item["agent_id"]) for item in selected})
        if missing:
            raise ValueError(f"unknown migration agent ids: {', '.join(missing)}")
        snapshot = {
            "target_cli_version": target_cli_version,
            "candidates": selected,
        }
        batch = self.store.create_runtime_migration_batch(
            target_cli_version=target_cli_version,
            retention_until=time.time() + max(1.0, retention_days) * 86400,
            snapshot=snapshot,
        )
        return {
            **batch,
            "candidate_count": len(selected),
            "eligible_count": sum(bool(item["eligible"]) for item in selected),
            "blocked_count": sum(not bool(item["eligible"]) for item in selected),
            "candidates": selected,
        }

    def record_migration_results(
        self,
        batch_id: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        batch = self.store.get_runtime_migration_batch(batch_id)
        if batch is None:
            raise ValueError("runtime migration batch not found")
        expected = {
            str(item.get("agent_id") or "")
            for item in dict(batch.get("snapshot") or {}).get("candidates", [])
            if isinstance(item, dict)
        }
        actual = {str(item.get("agent_id") or "") for item in results}
        if not actual <= expected:
            raise ValueError("migration results contain an agent outside the preview")
        statuses = {str(item.get("status") or "") for item in results}
        status = "complete" if results and statuses <= {"complete", "skipped"} else "partial"
        updated = self.store.update_runtime_migration_batch(
            batch_id,
            status=status,
            results=results,
            applied=True,
        )
        self.store.append_event(
            "runtime_migration_results_recorded",
            {"batch_id": batch_id, "status": status, "results": results},
            batch_id,
        )
        return updated

    def rollback_migration(self, batch_id: str) -> dict[str, Any]:
        restored = self.store.restore_runtime_migration_batch(batch_id)
        self.store.append_event(
            "runtime_migration_rolled_back",
            {"batch_id": batch_id, "restored": restored},
            batch_id,
        )
        return restored

    def _selected_roots(self, root: str | None) -> tuple[Path, ...]:
        if not root:
            return self.project_roots
        requested = Path(root).expanduser().resolve(strict=False)
        if requested not in self.project_roots:
            raise ValueError("requested discovery root is not an approved project root")
        return (requested,)

    def _validated_project_path(self, value: str) -> Path:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("project path is not a directory")
        if not any(path == root or path.is_relative_to(root) for root in self.project_roots):
            raise ValueError("project path is outside approved roots")
        inspected = self._inspect_git_project(path, ownership={})
        if inspected is None or Path(inspected.path) != path:
            raise ValueError("project path must be a Git repository or worktree root")
        return path

    def _inspect_git_project(
        self,
        path: Path,
        *,
        ownership: Mapping[Path, tuple[str, ...]],
    ) -> ManagedProject | None:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.splitlines()
        if len(lines) < 2:
            return None
        root = Path(lines[0]).resolve(strict=False)
        if root != path.resolve(strict=False):
            return None
        common = Path(lines[1])
        if not common.is_absolute():
            common = (path / common).resolve(strict=False)
        branch_result = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
        )
        return ManagedProject(
            path.name,
            str(path),
            str(common),
            branch_result.stdout.strip() if branch_result.returncode == 0 else "",
            ownership.get(path.resolve(strict=False), ()),
        )

    def _cwd_ownership(self) -> dict[Path, tuple[str, ...]]:
        owners: dict[Path, list[str]] = {}
        for agent in self.store.list_agents(include_hidden=True):
            if not bool(agent.get("pbx_active", True)):
                continue
            metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            cwd = str(metadata.get("work_root") or metadata.get("cwd") or "").strip()
            if not cwd:
                continue
            owners.setdefault(Path(cwd).expanduser().resolve(strict=False), []).append(
                str(agent["agent_id"])
            )
        return {path: tuple(sorted(agent_ids)) for path, agent_ids in owners.items()}

    @staticmethod
    def _profile_argv(overrides: Iterable[str]) -> list[str]:
        argv: list[str] = []
        for override in overrides:
            argv.extend(("-c", str(override)))
        return argv

    @staticmethod
    def _runtime_session_name(agent_id: str) -> str:
        suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"agent-pbx:{agent_id}").hex[:8]
        return f"agent-pbx-{agent_id[:80]}-{suffix}"

    def _kill_session(self, identity: TmuxServerIdentity, session_name: str) -> None:
        subprocess.run(
            [self.tmux_bin, "-S", identity.socket_path, "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
        )
