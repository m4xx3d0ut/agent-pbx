from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any, Callable, Mapping

from .files import AgentFileService, FileBrowserError
from .runtime_tmux import (
    RuntimeServerMode,
    TmuxServerIdentity,
    ensure_dedicated_runtime_server,
    exact_tmux_session_target,
    list_runtime_panes,
    process_start_ticks,
    restore_dedicated_runtime_exit_policy,
    runtime_server_id,
)
from .tmux_binary import configured_tmux_binary


TERMINAL_EDITOR_API_VERSION = "agent-pbx.terminal-editor/v1"
TERMINAL_EDITOR_PROFILES_ENV = "AGENT_PBX_TERMINAL_EDITOR_PROFILES"
TERMINAL_EDITOR_DEFAULT_ENV = "AGENT_PBX_TERMINAL_EDITOR_DEFAULT"
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class TerminalEditorProfile:
    profile_id: str
    label: str
    argv: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return {**asdict(self), "argv": list(self.argv)}

    def command(self, *, path: Path, line: int | None = None) -> tuple[str, ...]:
        rendered = tuple(
            item.replace("{path}", str(path)).replace("{line}", str(max(1, line or 1)))
            for item in self.argv
        )
        if not any("{path}" in item for item in self.argv):
            rendered = (*rendered, str(path))
        return rendered


@dataclass(frozen=True, slots=True)
class TerminalEditorRuntime:
    agent_id: str
    profile_id: str
    path: str
    root: str
    socket_path: str
    server_id: str
    session_name: str
    window_id: str
    pane_id: str
    pane_pid: int | None
    process_start_ticks: int | None
    tmux_bin: str
    state: str = "ready"

    def public_dict(self) -> dict[str, Any]:
        return {"api_version": TERMINAL_EDITOR_API_VERSION, **asdict(self)}


def terminal_editor_profiles(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[TerminalEditorProfile, ...]:
    env = os.environ if environ is None else environ
    configured = str(env.get(TERMINAL_EDITOR_PROFILES_ENV) or "").strip()
    profiles: list[TerminalEditorProfile] = []
    if configured:
        try:
            payload = json.loads(configured)
        except json.JSONDecodeError as exc:
            raise ValueError("terminal editor profiles must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("terminal editor profiles must be a JSON object")
        for profile_id, item in payload.items():
            if not PROFILE_ID_PATTERN.fullmatch(str(profile_id)):
                raise ValueError(f"invalid terminal editor profile id: {profile_id}")
            if not isinstance(item, dict) or not isinstance(item.get("argv"), list):
                raise ValueError(f"terminal editor profile {profile_id} requires argv")
            argv = tuple(str(part) for part in item["argv"])
            if not argv or len(argv) > 32 or any(not part or "\x00" in part for part in argv):
                raise ValueError(f"terminal editor profile {profile_id} has invalid argv")
            executable = argv[0]
            if Path(executable).is_absolute():
                executable_path = Path(executable).expanduser().resolve()
                resolved = (
                    str(executable_path)
                    if executable_path.is_file()
                    and os.access(executable_path, os.X_OK)
                    else ""
                )
            else:
                resolved = str(which(executable) or "")
            if not resolved:
                continue
            profiles.append(
                TerminalEditorProfile(
                    str(profile_id),
                    str(item.get("label") or profile_id),
                    (resolved, *argv[1:]),
                )
            )
    else:
        for profile_id, label, extra in (
            ("nvim", "Neovim", ("+{line}", "--", "{path}")),
            ("vim", "Vim", ("+{line}", "--", "{path}")),
            ("nano", "Nano", ("+{line}", "--", "{path}")),
        ):
            executable = which(profile_id)
            if executable:
                profiles.append(
                    TerminalEditorProfile(profile_id, label, (executable, *extra))
                )
    return tuple(profiles)


class ManagedTerminalEditorService:
    """Own disposable editor runtimes without sharing Codex process topology."""

    def __init__(
        self,
        files: AgentFileService,
        *,
        runtime_root: Path,
        tmux_bin: str | None = None,
        environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.files = files
        self.runtime_root = runtime_root.expanduser().resolve()
        self.tmux_bin = configured_tmux_binary(tmux_bin)
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner
        self.profiles = {
            profile.profile_id: profile
            for profile in terminal_editor_profiles(environ=self.environ)
        }
        self.default_profile = str(
            self.environ.get(TERMINAL_EDITOR_DEFAULT_ENV) or ""
        ).strip()
        self.runtimes: dict[str, TerminalEditorRuntime] = {}

    def profile_view(self) -> dict[str, Any]:
        selected = self.default_profile
        if selected not in self.profiles:
            selected = next(iter(self.profiles), "")
        return {
            "api_version": TERMINAL_EDITOR_API_VERSION,
            "default_profile": selected or None,
            "profiles": [profile.public_dict() for profile in self.profiles.values()],
            "native_editor_default": True,
        }

    def preview(
        self,
        agent: dict[str, Any],
        *,
        path: str,
        profile_id: str | None = None,
        line: int | None = None,
    ) -> dict[str, Any]:
        profile = self._profile(profile_id)
        resolved = self.files.resolve_terminal_editor_target(agent, path=path)
        if isinstance(resolved, FileBrowserError):
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "ready": False,
                "error": resolved.as_dict(),
            }
        root, target = resolved
        return {
            "api_version": TERMINAL_EDITOR_API_VERSION,
            "ready": True,
            "agent_id": str(agent.get("agent_id") or ""),
            "profile": profile.public_dict(),
            "root": str(root),
            "path": str(target),
            "line": max(1, line or 1),
            "command": list(profile.command(path=target, line=line)),
            "native_editor_default": True,
        }

    def start(
        self,
        agent: dict[str, Any],
        *,
        path: str,
        profile_id: str | None = None,
        line: int | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(agent, path=path, profile_id=profile_id, line=line)
        if not preview.get("ready"):
            return preview
        agent_id = str(preview["agent_id"])
        existing = self.status(agent_id)
        if existing.get("running"):
            if (
                existing.get("path") == preview["path"]
                and existing.get("profile_id") == preview["profile"]["profile_id"]
            ):
                return {**existing, "started": False}
            raise ValueError("a managed terminal editor is already running for this agent")

        digest = hashlib.sha256(agent_id.encode()).hexdigest()[:16]
        runtime_dir = self.runtime_root / digest
        runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime_dir.chmod(0o700)
        socket_path = runtime_dir / "tmux.sock"
        session_name = f"pbx-editor-{digest}"
        identity = TmuxServerIdentity(
            RuntimeServerMode.DEDICATED,
            RuntimeServerMode.DEDICATED,
            runtime_server_id(socket_path),
            str(socket_path),
            True,
            False,
            "managed terminal editor",
            self.tmux_bin,
        )
        server_started = ensure_dedicated_runtime_server(identity)
        command = [
            self.tmux_bin,
            "-S",
            str(socket_path),
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            session_name,
            "-n",
            "editor",
            "-c",
            str(preview["root"]),
            shlex.join(["exec", *[str(item) for item in preview["command"]]]),
        ]
        try:
            result = self.runner(command, capture_output=True, text=True, timeout=15)
            if server_started:
                restore_dedicated_runtime_exit_policy(identity)
        except Exception:
            if server_started:
                self.runner(
                    [*identity.command_prefix, "kill-server"],
                    capture_output=True,
                    text=True,
                )
            raise
        if result.returncode != 0:
            if server_started:
                self.runner(
                    [*identity.command_prefix, "kill-server"],
                    capture_output=True,
                    text=True,
                )
            raise RuntimeError(
                (result.stderr or result.stdout or "terminal editor launch failed").strip()
            )
        pane_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        pane = next(
            (item for item in list_runtime_panes(identity) if item.pane_id == pane_id),
            None,
        )
        if pane is None:
            self._kill_session(identity, session_name)
            raise RuntimeError("terminal editor pane did not appear on its dedicated server")
        runtime = TerminalEditorRuntime(
            agent_id=agent_id,
            profile_id=str(preview["profile"]["profile_id"]),
            path=str(preview["path"]),
            root=str(preview["root"]),
            socket_path=str(socket_path),
            server_id=identity.server_id,
            session_name=session_name,
            window_id=pane.window_id,
            pane_id=pane.pane_id,
            pane_pid=pane.pane_pid,
            process_start_ticks=process_start_ticks(pane.pane_pid),
            tmux_bin=self.tmux_bin,
        )
        self.runtimes[agent_id] = runtime
        return {**runtime.public_dict(), "running": True, "started": True}

    def status(self, agent_id: str) -> dict[str, Any]:
        runtime = self.runtimes.get(agent_id)
        if runtime is None:
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "agent_id": agent_id,
                "running": False,
            }
        identity = self._identity(runtime)
        pane = next(
            (item for item in list_runtime_panes(identity) if item.pane_id == runtime.pane_id),
            None,
        )
        if pane is None:
            self.runtimes.pop(agent_id, None)
            return {
                **runtime.public_dict(),
                "running": False,
                "state": "exited",
            }
        return {**runtime.public_dict(), "running": True}

    def stop(self, agent_id: str) -> dict[str, Any]:
        runtime = self.runtimes.pop(agent_id, None)
        if runtime is None:
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "agent_id": agent_id,
                "running": False,
                "stopped": False,
            }
        identity = self._identity(runtime)
        self._kill_session(identity, runtime.session_name)
        return {
            **runtime.public_dict(),
            "running": False,
            "state": "stopped",
            "stopped": True,
        }

    def stop_all(self) -> list[dict[str, Any]]:
        return [self.stop(agent_id) for agent_id in tuple(self.runtimes)]

    def _profile(self, profile_id: str | None) -> TerminalEditorProfile:
        selected = str(profile_id or self.default_profile or "").strip()
        if not selected:
            selected = next(iter(self.profiles), "")
        profile = self.profiles.get(selected)
        if profile is None:
            available = ", ".join(self.profiles) or "none"
            raise ValueError(f"terminal editor profile is unavailable ({available})")
        return profile

    def _identity(self, runtime: TerminalEditorRuntime) -> TmuxServerIdentity:
        return TmuxServerIdentity(
            RuntimeServerMode.DEDICATED,
            RuntimeServerMode.DEDICATED,
            runtime.server_id,
            runtime.socket_path,
            True,
            False,
            "managed terminal editor",
            runtime.tmux_bin,
        )

    def _kill_session(self, identity: TmuxServerIdentity, session_name: str) -> None:
        self.runner(
            [
                *identity.command_prefix,
                "kill-session",
                "-t",
                exact_tmux_session_target(session_name),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
