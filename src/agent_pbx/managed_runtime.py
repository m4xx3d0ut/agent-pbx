from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
from .codex_launch import (
    MANAGED_CALLER_BOOTSTRAP_VERSION,
    managed_caller_bootstrap_id,
    managed_caller_bootstrap_prompt,
    managed_caller_codex_overrides,
)
from .mcp_inventory import (
    MCP_TOOL_MANIFEST_ENV,
    MCP_TOOL_MANIFEST_METADATA_KEY,
    MCP_TOOL_MANIFEST_STORE_KEY,
    manifest_sha256,
)
from .runtime_tmux import (
    exact_tmux_session_target,
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
from .tmux_binary import configured_tmux_binary, managed_tmux_launch_environment
from .workerbee_modes import WORKERBEE_PROFILE_LOCAL_DEPLOY
from .workerbee_profile import (
    WORKERBEE_PROFILE_CEILING_METADATA_KEY,
    WORKERBEE_PROFILE_INTENT_METADATA_KEY,
    WORKERBEE_PROFILE_METADATA_KEY,
    build_workerbee_profile_intent,
    resolve_workerbee_profile_intent,
)


PROJECT_ROOTS_ENV = "AGENT_PBX_PROJECT_ROOTS"
DEFAULT_MIGRATION_RETENTION_DAYS = 7.0
AGENT_ID_PATTERN = re.compile(r"[^a-z0-9_.-]+")
ACTIVE_MIGRATION_STATUSES = {"online", "working", "waiting", "blocked", "needs_input"}


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
        state_root: Path | None = None,
        catalog_loader: Callable[[str], tuple[CodexModelOption, ...]] | None = None,
    ) -> None:
        self.store = store
        self.project_roots = tuple(
            path.expanduser().resolve(strict=False)
            for path in (project_roots or default_managed_project_roots())
        )
        self.codex_bin = codex_bin or shutil.which("codex") or "codex"
        self.tmux_bin = configured_tmux_binary(tmux_bin)
        self.state_root = (state_root or self.store.path.parent).expanduser().resolve(
            strict=False
        )
        self.catalog_loader = catalog_loader or (
            lambda command: inspect_codex_model_catalog(command)
        )

    def public_roots(self) -> list[str]:
        return [str(path) for path in self.project_roots]

    def _mcp_tool_manifest_sha256(self) -> str:
        return manifest_sha256(
            self.store.get_system_metadata_json(MCP_TOOL_MANIFEST_STORE_KEY)
        )

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
        server_url: str = "http://127.0.0.1:8765",
        source_codex_session_id: str | None = None,
        workerbee_profile: str = WORKERBEE_PROFILE_LOCAL_DEPLOY,
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
        if workerbee_profile != WORKERBEE_PROFILE_LOCAL_DEPLOY:
            raise ValueError(
                "managed caller launches are fixed to the local-deploy WorkerBee profile"
            )
        catalog = self.catalog_loader(self.codex_bin)
        validation = validate_managed_profile(profile, catalog)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        identity = resolve_runtime_tmux_server(runtime_server_mode)
        if not identity.ready:
            raise ValueError(identity.message or "runtime tmux server is unavailable")
        session_name = self._runtime_session_name(resolved_agent_id)
        bootstrap_prompt = managed_caller_bootstrap_prompt(
            agent_id=resolved_agent_id,
            project=project_name,
            cwd=path,
            source_codex_session_id=source_codex_session_id,
            workerbee_profile=workerbee_profile,
        )
        bootstrap_id = managed_caller_bootstrap_id(
            agent_id=resolved_agent_id,
            project_path=path,
            profile_id=profile_id,
            runtime_server_mode=runtime_server_mode,
            source_codex_session_id=source_codex_session_id,
            workerbee_profile=workerbee_profile,
        )
        mcp_manifest_digest = self._mcp_tool_manifest_sha256()
        return {
            "agent_id": resolved_agent_id,
            "project": project_name,
            "project_path": str(path),
            "profile_id": profile.profile_id,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "verbosity": profile.verbosity,
            "reasoning_summary": profile.reasoning_summary,
            "service_tier": profile.service_tier,
            "runtime_server": identity.public_dict(),
            "session_name": session_name,
            "command": [
                self.codex_bin,
                *self._profile_argv(profile.overrides()),
                *self._profile_argv(managed_caller_codex_overrides(server_url)),
                bootstrap_prompt,
            ],
            "bootstrap_id": bootstrap_id,
            "bootstrap_version": MANAGED_CALLER_BOOTSTRAP_VERSION,
            "bootstrap_prompt_sha256": hashlib.sha256(
                bootstrap_prompt.encode("utf-8")
            ).hexdigest(),
            "source_codex_session_id": str(source_codex_session_id or "").strip()
            or None,
            "workerbee_profile": workerbee_profile,
            "mcp_tool_manifest_sha256": mcp_manifest_digest or None,
            "warnings": list(validation.warnings),
        }

    def preview_rehydrate(
        self,
        *,
        agent_id: str,
        mode: str = "fresh",
        profile_id: str = DEFAULT_CALLER_PROFILE_ID,
        runtime_server_mode: str = "dedicated",
        server_url: str = "http://127.0.0.1:8765",
        source_codex_session_id: str | None = None,
        workerbee_profile: str = WORKERBEE_PROFILE_LOCAL_DEPLOY,
    ) -> dict[str, Any]:
        """Preview a new independent managed runtime for an existing caller."""

        normalized_mode = str(mode or "fresh").strip().lower()
        if normalized_mode not in {"fresh", "fork_source"}:
            raise ValueError("rehydration mode must be fresh or fork_source")
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"agent {agent_id!r} does not exist")
        if str(agent.get("agent_type") or "caller") != "caller":
            raise ValueError("managed rehydration supports caller agents only")
        metadata = (
            dict(agent.get("metadata") or {})
            if isinstance(agent.get("metadata"), dict)
            else {}
        )
        project_path = str(
            metadata.get("work_root")
            or metadata.get("cwd")
            or metadata.get("source_cwd")
            or ""
        ).strip()
        if not project_path:
            raise ValueError("caller has no persisted project working directory")
        path = self._validated_project_path(project_path)
        profile = MANAGED_CODEX_PROFILES.get(profile_id)
        if profile is None:
            raise ValueError(f"unknown managed Codex profile: {profile_id}")
        if profile.elevated:
            raise ValueError("elevated profiles require an approved model lease")
        if profile.migration_only:
            raise ValueError("migration-only profiles cannot rehydrate callers")
        if workerbee_profile != WORKERBEE_PROFILE_LOCAL_DEPLOY:
            raise ValueError(
                "managed caller rehydration is fixed to the local-deploy WorkerBee profile"
            )
        validation = validate_managed_profile(profile, self.catalog_loader(self.codex_bin))
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        identity = resolve_runtime_tmux_server(runtime_server_mode)
        if not identity.ready:
            raise ValueError(identity.message or "runtime tmux server is unavailable")

        blockers: list[str] = []
        existing_mapping = self.store.get_tmux_runtime_mapping(agent_id)
        if existing_mapping is not None:
            blockers.append("caller already has a runtime mapping")
        session_name = self._runtime_session_name(agent_id)
        try:
            if any(
                pane.session_name == session_name
                for pane in list_runtime_panes(identity)
            ):
                blockers.append(
                    f"managed tmux session {session_name!r} already exists"
                )
        except Exception as exc:  # noqa: BLE001 - preview records a safe blocker.
            blockers.append(f"unable to inspect runtime tmux server: {exc}")

        historical_session_id = str(
            source_codex_session_id
            or metadata.get("last_resume_codex_session_id")
            or metadata.get("codex_session_id")
            or metadata.get("codex_thread_id")
            or ""
        ).strip()
        session_owner_ids = self._codex_session_owner_ids(historical_session_id)
        if normalized_mode == "fork_source":
            if not historical_session_id:
                blockers.append("fork_source requires a source Codex session id")
            blockers.append(
                "fork_source is not enabled until the installed Codex fork capability is observed"
            )

        owners = tuple(
            owner
            for owner in self._cwd_ownership().get(path, ())
            if owner != agent_id
        )
        bootstrap_source = historical_session_id if normalized_mode == "fork_source" else None
        bootstrap_prompt = managed_caller_bootstrap_prompt(
            agent_id=agent_id,
            project=str(agent.get("project") or path.name),
            cwd=path,
            source_codex_session_id=bootstrap_source,
            workerbee_profile=workerbee_profile,
        )
        bootstrap_id = managed_caller_bootstrap_id(
            agent_id=agent_id,
            project_path=path,
            profile_id=profile_id,
            runtime_server_mode=runtime_server_mode,
            source_codex_session_id=bootstrap_source,
            workerbee_profile=workerbee_profile,
        )
        token_payload = {
            "agent_id": agent_id,
            "agent_type": agent.get("agent_type"),
            "project": agent.get("project"),
            "project_path": str(path),
            "metadata_sha256": hashlib.sha256(
                json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "mode": normalized_mode,
            "profile_id": profile_id,
            "runtime_server_mode": runtime_server_mode,
            "runtime_server_id": identity.server_id,
            "source_codex_session_id": historical_session_id or None,
            "workerbee_profile": workerbee_profile,
            "existing_mapping": existing_mapping,
            "mcp_tool_manifest_sha256": self._mcp_tool_manifest_sha256() or None,
        }
        preview_token = hashlib.sha256(
            json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        warnings = list(validation.warnings)
        if owners:
            warnings.append(
                "workspace is also referenced by active entities: " + ", ".join(owners)
            )
        if historical_session_id and session_owner_ids:
            warnings.append(
                "historical Codex session is associated with: "
                + ", ".join(session_owner_ids)
                + "; fresh mode will not resume it"
            )
        return {
            "agent_id": agent_id,
            "project": str(agent.get("project") or path.name),
            "project_path": str(path),
            "mode": normalized_mode,
            "eligible": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "co_owners": list(owners),
            "historical_codex_session_id": historical_session_id or None,
            "historical_session_owner_ids": list(session_owner_ids),
            "profile_id": profile.profile_id,
            "model": profile.model,
            "reasoning_effort": profile.reasoning_effort,
            "verbosity": profile.verbosity,
            "reasoning_summary": profile.reasoning_summary,
            "service_tier": profile.service_tier,
            "runtime_server": identity.public_dict(),
            "session_name": session_name,
            "command": [
                self.codex_bin,
                *self._profile_argv(profile.overrides()),
                *self._profile_argv(managed_caller_codex_overrides(server_url)),
                bootstrap_prompt,
            ],
            "bootstrap_id": bootstrap_id,
            "bootstrap_version": MANAGED_CALLER_BOOTSTRAP_VERSION,
            "bootstrap_prompt_sha256": hashlib.sha256(
                bootstrap_prompt.encode("utf-8")
            ).hexdigest(),
            "source_codex_session_id": bootstrap_source,
            "workerbee_profile": workerbee_profile,
            "mcp_tool_manifest_sha256": token_payload[
                "mcp_tool_manifest_sha256"
            ],
            "preview_token": preview_token,
        }

    def rehydrate(
        self,
        *,
        agent_id: str,
        preview_token: str,
        mode: str = "fresh",
        profile_id: str = DEFAULT_CALLER_PROFILE_ID,
        runtime_server_mode: str = "dedicated",
        server_url: str = "http://127.0.0.1:8765",
        token: str | None = None,
        source_codex_session_id: str | None = None,
        workerbee_profile: str = WORKERBEE_PROFILE_LOCAL_DEPLOY,
    ) -> dict[str, Any]:
        return self.launch(
            project_path="",
            agent_id=agent_id,
            profile_id=profile_id,
            runtime_server_mode=runtime_server_mode,
            server_url=server_url,
            token=token,
            source_codex_session_id=source_codex_session_id,
            workerbee_profile=workerbee_profile,
            _rehydrate_existing=True,
            _rehydration_mode=mode,
            _preview_token=preview_token,
        )

    def launch(
        self,
        *,
        project_path: str,
        agent_id: str = "",
        profile_id: str = DEFAULT_CALLER_PROFILE_ID,
        runtime_server_mode: str = "dedicated",
        server_url: str = "http://127.0.0.1:8765",
        token: str | None = None,
        source_codex_session_id: str | None = None,
        workerbee_profile: str = WORKERBEE_PROFILE_LOCAL_DEPLOY,
        _rehydrate_existing: bool = False,
        _rehydration_mode: str = "fresh",
        _preview_token: str | None = None,
    ) -> dict[str, Any]:
        if _rehydrate_existing:
            preview = self.preview_rehydrate(
                agent_id=agent_id,
                mode=_rehydration_mode,
                profile_id=profile_id,
                runtime_server_mode=runtime_server_mode,
                server_url=server_url,
                source_codex_session_id=source_codex_session_id,
                workerbee_profile=workerbee_profile,
            )
            if not _preview_token or _preview_token != preview["preview_token"]:
                raise ValueError("rehydration preview token is stale or invalid")
            if not preview["eligible"]:
                raise ValueError("; ".join(preview["blockers"]))
        else:
            existing = self._idempotent_launch_result(
                project_path=project_path,
                agent_id=agent_id,
                profile_id=profile_id,
                runtime_server_mode=runtime_server_mode,
                source_codex_session_id=source_codex_session_id,
                workerbee_profile=workerbee_profile,
            )
            if existing is not None:
                return existing
            preview = self.preview_launch(
                project_path=project_path,
                agent_id=agent_id,
                profile_id=profile_id,
                runtime_server_mode=runtime_server_mode,
                server_url=server_url,
                source_codex_session_id=source_codex_session_id,
                workerbee_profile=workerbee_profile,
            )
        profile = MANAGED_CODEX_PROFILES[str(preview["profile_id"])]
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
            "AGENT_PBX_REPORTING_AGENT_ID": str(preview["agent_id"]),
            "AGENT_PBX_AGENT_TYPE": "caller",
            "AGENT_PBX_AGENT_PROJECT": str(preview["project"]),
            "AGENT_PBX_CWD": str(preview["project_path"]),
            "AGENT_PBX_PBX_MODE": "report",
            "AGENT_PBX_CODEX_MODEL": str(preview["model"]),
            "AGENT_PBX_CODEX_REASONING_EFFORT": str(preview["reasoning_effort"]),
            "AGENT_PBX_CODEX_REASONING_SUMMARY": str(preview["reasoning_summary"]),
            "AGENT_PBX_CODEX_VERBOSITY": str(preview["verbosity"]),
            "AGENT_PBX_SERVER_URL": server_url.rstrip("/"),
            "AGENT_PBX_MODE": "report",
            "AGENT_PBX_MANAGED_BOOTSTRAP_ID": str(preview["bootstrap_id"]),
            "CODEX_NON_INTERACTIVE": "1",
            "AGENT_PBX_WORKERBEE_MCP_PROFILE": str(preview["workerbee_profile"]),
        }
        if preview.get("source_codex_session_id"):
            env["AGENT_PBX_SOURCE_CODEX_SESSION_ID"] = str(
                preview["source_codex_session_id"]
            )
        if token:
            env["AGENT_PBX_TOKEN"] = token
        if preview.get("mcp_tool_manifest_sha256"):
            env[MCP_TOOL_MANIFEST_ENV] = str(
                preview["mcp_tool_manifest_sha256"]
            )
        env = managed_tmux_launch_environment(
            env,
            identity.tmux_bin or self.tmux_bin,
            state_root=self.state_root,
        )
        command = [
            *identity.command_prefix,
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
        command.append(
            shlex.join(["exec", *(str(item) for item in preview["command"])])
        )
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
            profile_metadata = {
                "agent_type": "caller",
                WORKERBEE_PROFILE_METADATA_KEY: str(preview["workerbee_profile"]),
                WORKERBEE_PROFILE_CEILING_METADATA_KEY: (
                    WORKERBEE_PROFILE_LOCAL_DEPLOY
                ),
            }
            profile_intent = build_workerbee_profile_intent(
                str(preview["agent_id"]),
                profile_metadata,
                profile=str(preview["workerbee_profile"]),
            )
            metadata = {
                "cwd": str(preview["project_path"]),
                "launched_by": "agent-pbx-managed-runtime",
                "tmux_pane_id": pane_id,
                "tmux_session": str(preview["session_name"]),
                "runtime_server_id": identity.server_id,
                "runtime_server_mode": identity.effective_mode.value,
                "codex_command": self.codex_bin,
                "managed_profile_id": profile.profile_id,
                "model": preview["model"],
                "model_reasoning_effort": preview["reasoning_effort"],
                "model_verbosity": preview["verbosity"],
                "model_reasoning_summary": preview["reasoning_summary"],
                "model_service_tier": preview["service_tier"],
                "codex_model_preset": profile.profile_id,
                "codex_model": preview["model"],
                "codex_model_reasoning_effort": preview["reasoning_effort"],
                "codex_model_verbosity": preview["verbosity"],
                "codex_model_reasoning_summary": preview["reasoning_summary"],
                "codex_model_service_tier": preview["service_tier"],
                "managed_bootstrap_id": preview["bootstrap_id"],
                "managed_bootstrap_version": preview["bootstrap_version"],
                "managed_bootstrap_prompt_sha256": preview[
                    "bootstrap_prompt_sha256"
                ],
                "managed_bootstrap_state": "delivered",
                "source_codex_session_id": preview.get("source_codex_session_id"),
                WORKERBEE_PROFILE_METADATA_KEY: str(preview["workerbee_profile"]),
                WORKERBEE_PROFILE_CEILING_METADATA_KEY: (
                    WORKERBEE_PROFILE_LOCAL_DEPLOY
                ),
                WORKERBEE_PROFILE_INTENT_METADATA_KEY: profile_intent.public_dict(),
            }
            if preview.get("mcp_tool_manifest_sha256"):
                metadata[MCP_TOOL_MANIFEST_METADATA_KEY] = str(
                    preview["mcp_tool_manifest_sha256"]
                )
            runtime_mapping = {
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
                        "tmux_bin": identity.tmux_bin or self.tmux_bin,
                        "managed_bootstrap_id": preview["bootstrap_id"],
                        "managed_bootstrap_version": preview["bootstrap_version"],
                        "source_codex_session_id": preview.get(
                            "source_codex_session_id"
                        ),
                        WORKERBEE_PROFILE_METADATA_KEY: str(
                            preview["workerbee_profile"]
                        ),
                        WORKERBEE_PROFILE_CEILING_METADATA_KEY: (
                            WORKERBEE_PROFILE_LOCAL_DEPLOY
                        ),
                        WORKERBEE_PROFILE_INTENT_METADATA_KEY: (
                            profile_intent.public_dict()
                        ),
                    },
                }
            if preview.get("mcp_tool_manifest_sha256"):
                runtime_mapping["metadata"][MCP_TOOL_MANIFEST_METADATA_KEY] = str(
                    preview["mcp_tool_manifest_sha256"]
                )
            if _rehydrate_existing:
                metadata.update(
                    {
                        "rehydration_mode": str(preview["mode"]),
                        "rehydration_preview_token": str(preview["preview_token"]),
                        "rehydration_previous_codex_session_id": preview.get(
                            "historical_codex_session_id"
                        ),
                        "rehydrated_at": time.time(),
                    }
                )
                runtime_mapping["metadata"].update(
                    {
                        "managed_rehydration": True,
                        "rehydration_mode": str(preview["mode"]),
                        "rehydration_preview_token": str(preview["preview_token"]),
                    }
                )
                agent, mapping = self.store.rehydrate_managed_caller_runtime(
                    str(preview["agent_id"]),
                    metadata=metadata,
                    clear_metadata_keys=(
                        "codex_session_id",
                        "codex_thread_id",
                        "last_resume_codex_session_id",
                        "fork_codex_session_id",
                    ),
                    runtime_mapping=runtime_mapping,
                )
            else:
                agent, mapping = self.store.create_managed_agent_runtime(
                    AgentRegisterRequest(
                        agent_id=str(preview["agent_id"]),
                        project=str(preview["project"]),
                        name=str(preview["project"]),
                        metadata=metadata,
                    ),
                    runtime_mapping=runtime_mapping,
                )
        except Exception:
            self._kill_session(identity, str(preview["session_name"]))
            raise
        self.store.append_event(
            (
                "managed_caller_rehydrated"
                if _rehydrate_existing
                else "managed_agent_launched"
            ),
            {
                "agent_id": preview["agent_id"],
                "project": preview["project"],
                "pane_id": pane_id,
                "profile_id": profile_id,
                "server_id": identity.server_id,
                "bootstrap_id": preview["bootstrap_id"],
                "source_codex_session_id": preview.get("source_codex_session_id"),
                "rehydration_mode": preview.get("mode"),
            },
            str(preview["agent_id"]),
        )
        return {
            **preview,
            "agent": agent,
            "runtime_mapping": mapping,
            "launched": True,
            "rehydrated": _rehydrate_existing,
        }

    def _idempotent_launch_result(
        self,
        *,
        project_path: str,
        agent_id: str,
        profile_id: str,
        runtime_server_mode: str,
        source_codex_session_id: str | None,
        workerbee_profile: str,
    ) -> dict[str, Any] | None:
        path = self._validated_project_path(project_path)
        resolved_agent_id = normalize_managed_agent_id(agent_id, project_name=path.name)
        existing = self.store.get_agent(resolved_agent_id)
        if existing is None:
            return None
        bootstrap_id = managed_caller_bootstrap_id(
            agent_id=resolved_agent_id,
            project_path=path,
            profile_id=profile_id,
            runtime_server_mode=runtime_server_mode,
            source_codex_session_id=source_codex_session_id,
            workerbee_profile=workerbee_profile,
        )
        metadata = (
            dict(existing.get("metadata") or {})
            if isinstance(existing.get("metadata"), dict)
            else {}
        )
        intent = resolve_workerbee_profile_intent(resolved_agent_id, metadata)
        if intent.profile != workerbee_profile:
            raise ValueError(
                "managed launch WorkerBee profile differs from the persisted intent"
            )
        mapping = self.store.get_tmux_runtime_mapping(resolved_agent_id)
        if (
            metadata.get("managed_bootstrap_id") != bootstrap_id
            or not isinstance(mapping, dict)
            or str(mapping.get("state") or "") != "ready"
            or Path(str(metadata.get("cwd") or "")).resolve(strict=False) != path
        ):
            return None
        return {
            "agent_id": resolved_agent_id,
            "project": path.name,
            "project_path": str(path),
            "profile_id": profile_id,
            "model": metadata.get("model"),
            "reasoning_effort": metadata.get("model_reasoning_effort"),
            "verbosity": metadata.get("model_verbosity"),
            "reasoning_summary": metadata.get("model_reasoning_summary"),
            "service_tier": metadata.get("model_service_tier"),
            "runtime_server": {
                "effective_mode": mapping.get("server_mode"),
                "server_id": mapping.get("server_id"),
                "socket_path": mapping.get("socket_path"),
            },
            "session_name": mapping.get("session_name"),
            "bootstrap_id": bootstrap_id,
            "bootstrap_version": metadata.get("managed_bootstrap_version"),
            "bootstrap_prompt_sha256": metadata.get(
                "managed_bootstrap_prompt_sha256"
            ),
            "source_codex_session_id": metadata.get("source_codex_session_id"),
            "warnings": [],
            "agent": existing,
            "runtime_mapping": mapping,
            "launched": False,
            "idempotent": True,
        }

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
            if not session_id:
                blockers.append("no Codex resume session id")
            cwd = str(
                metadata.get("work_root")
                or metadata.get("cwd")
                or metadata.get("source_cwd")
                or ""
            ).strip()
            if not cwd or not Path(cwd).is_dir():
                blockers.append("working directory is unavailable")
            status = str(agent.get("status") or "").strip().casefold()
            if not bool(agent.get("pbx_active", True)) or status not in ACTIVE_MIGRATION_STATUSES:
                blockers.append("retained identity is not active; preserve without relaunch")
            if mapping is not None and bool(mapping.get("writer_lease_active")):
                blockers.append("runtime writer lease is active")
            selected.append(
                {
                    "agent_id": agent_id,
                    "agent_type": agent.get("agent_type"),
                    "project": agent.get("project"),
                    "starred": bool(agent.get("starred")),
                    "session_id": session_id or None,
                    "cwd": cwd or None,
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
        by_session: dict[str, list[dict[str, Any]]] = {}
        for item in selected:
            session_id = str(item.get("session_id") or "")
            if session_id:
                by_session.setdefault(session_id, []).append(item)
        for session_id, candidates in by_session.items():
            if len(candidates) < 2:
                continue
            owner = sorted(
                candidates,
                key=lambda item: (
                    str((item.get("mapping") or {}).get("state") or "") != "ready",
                    item.get("agent_type") != "operator",
                    str(item.get("agent_id") or ""),
                ),
            )[0]
            owner_id = str(owner.get("agent_id") or "")
            for item in candidates:
                if item is owner:
                    continue
                blockers = item.get("blockers")
                if isinstance(blockers, list):
                    blockers.append(
                        f"Codex session {session_id} is owned by migration candidate {owner_id}"
                    )
                    item["eligible"] = False
            owner["eligible"] = not bool(owner.get("blockers"))
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

    def _codex_session_owner_ids(self, session_id: str) -> tuple[str, ...]:
        normalized = str(session_id or "").strip()
        if not normalized:
            return ()
        owners: set[str] = set()
        session_keys = (
            "fork_codex_session_id",
            "last_resume_codex_session_id",
            "codex_session_id",
            "codex_thread_id",
        )
        for agent in self.store.list_agents(include_hidden=True):
            metadata = (
                agent.get("metadata")
                if isinstance(agent.get("metadata"), dict)
                else {}
            )
            if any(str(metadata.get(key) or "").strip() == normalized for key in session_keys):
                owners.add(str(agent["agent_id"]))
        for mapping in self.store.list_tmux_runtime_mappings():
            if str(mapping.get("codex_session_id") or "").strip() == normalized:
                owners.add(str(mapping["entity_id"]))
        return tuple(sorted(owners))

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
            [
                *identity.command_prefix,
                "kill-session",
                "-t",
                exact_tmux_session_target(session_name),
            ],
            capture_output=True,
            text=True,
        )
