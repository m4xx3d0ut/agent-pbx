from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import time
from typing import Any, Callable, Mapping
import uuid

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
from .store import Store


TERMINAL_EDITOR_API_VERSION = "agent-pbx.terminal-editor/v1"
TERMINAL_EDITOR_PROFILES_ENV = "AGENT_PBX_TERMINAL_EDITOR_PROFILES"
TERMINAL_EDITOR_DEFAULT_ENV = "AGENT_PBX_TERMINAL_EDITOR_DEFAULT"
TERMINAL_EDITOR_SHUTDOWN_POLICY_ENV = "AGENT_PBX_TERMINAL_EDITOR_SHUTDOWN_POLICY"
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
TERMINAL_EDITOR_SHUTDOWN_POLICIES = {"detach", "stop"}


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

    @property
    def supports_rpc(self) -> bool:
        return self.profile_id == "nvim" or Path(self.argv[0]).name == "nvim"


@dataclass(frozen=True, slots=True)
class TerminalEditorRuntime:
    agent_id: str
    runtime_id: str
    project: str
    access_mode: str
    profile_id: str
    path: str
    root: str
    socket_path: str
    rpc_socket_path: str | None
    rpc_enabled: bool
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
    """Own durable, project-rooted editor runtimes outside Codex topology."""

    def __init__(
        self,
        files: AgentFileService,
        *,
        runtime_root: Path,
        store: Store | None = None,
        tmux_bin: str | None = None,
        environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.files = files
        self.store = store
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
        self.shutdown_policy = str(
            self.environ.get(TERMINAL_EDITOR_SHUTDOWN_POLICY_ENV) or "detach"
        ).strip().lower()
        if self.shutdown_policy not in TERMINAL_EDITOR_SHUTDOWN_POLICIES:
            raise ValueError(
                "terminal editor shutdown policy must be detach or stop"
            )
        self.runtimes: dict[str, TerminalEditorRuntime] = {}
        self._load_persisted_runtimes()

    def profile_view(self) -> dict[str, Any]:
        selected = self.default_profile
        if selected not in self.profiles:
            selected = next(iter(self.profiles), "")
        return {
            "api_version": TERMINAL_EDITOR_API_VERSION,
            "default_profile": selected or None,
            "profiles": [profile.public_dict() for profile in self.profiles.values()],
            "native_editor_default": True,
            "shutdown_policy": self.shutdown_policy,
            "durable_runtime": self.store is not None,
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
        metadata = agent.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        return {
            "api_version": TERMINAL_EDITOR_API_VERSION,
            "ready": True,
            "agent_id": str(agent.get("agent_id") or ""),
            "project": str(agent.get("project") or ""),
            "access_mode": str(
                metadata.get("access_mode")
                or agent.get("access_mode")
                or "edit"
            ),
            "profile": profile.public_dict(),
            "root": str(root),
            "path": str(target),
            "line": max(1, line or 1),
            "command": list(profile.command(path=target, line=line)),
            "rpc_enabled": profile.supports_rpc,
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
            if existing.get("root") != preview["root"]:
                raise ValueError(
                    "the managed editor belongs to a different project root"
                )
            if existing.get("profile_id") != preview["profile"]["profile_id"]:
                raise ValueError(
                    "stop the active managed editor before changing profiles"
                )
            if existing.get("path") == preview["path"]:
                return {**existing, "started": False, "opened": False}
            runtime = self.runtimes[agent_id]
            if not runtime.rpc_enabled:
                raise ValueError(
                    "this editor profile cannot open another file in the active runtime"
                )
            self._route_file(
                runtime,
                path=Path(str(preview["path"])),
                line=max(1, line or 1),
            )
            runtime = replace(runtime, path=str(preview["path"]), state="ready")
            self.runtimes[agent_id] = runtime
            self._persist_runtime(runtime, verified=True)
            return {
                **runtime.public_dict(),
                "running": True,
                "started": False,
                "opened": True,
            }
        if existing.get("state") == "stale":
            raise ValueError(
                "the persisted editor mapping is stale; inspect or forget it before starting another runtime"
            )
        if agent_id in self.runtimes:
            self.runtimes.pop(agent_id, None)
            if self.store is not None:
                self.store.delete_terminal_editor_runtime(agent_id)

        digest = hashlib.sha256(agent_id.encode()).hexdigest()[:16]
        runtime_dir = self.runtime_root / digest
        runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime_dir.chmod(0o700)
        socket_path = runtime_dir / "tmux.sock"
        profile = self._profile(str(preview["profile"]["profile_id"]))
        rpc_socket_path = runtime_dir / "nvim.sock" if profile.supports_rpc else None
        if rpc_socket_path is not None:
            rpc_socket_path.unlink(missing_ok=True)
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
        editor_command = tuple(str(item) for item in preview["command"])
        if rpc_socket_path is not None:
            editor_command = (
                editor_command[0],
                "--listen",
                str(rpc_socket_path),
                *editor_command[1:],
            )
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
            shlex.join(["exec", *editor_command]),
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
        if rpc_socket_path is not None and not self._wait_for_rpc_socket(rpc_socket_path):
            self._kill_session(identity, session_name)
            raise RuntimeError("Neovim did not create its managed RPC socket")
        runtime = TerminalEditorRuntime(
            agent_id=agent_id,
            runtime_id=f"editor-{uuid.uuid4().hex}",
            project=str(preview.get("project") or ""),
            access_mode=str(preview.get("access_mode") or "edit"),
            profile_id=str(preview["profile"]["profile_id"]),
            path=str(preview["path"]),
            root=str(preview["root"]),
            socket_path=str(socket_path),
            rpc_socket_path=str(rpc_socket_path) if rpc_socket_path else None,
            rpc_enabled=rpc_socket_path is not None,
            server_id=identity.server_id,
            session_name=session_name,
            window_id=pane.window_id,
            pane_id=pane.pane_id,
            pane_pid=pane.pane_pid,
            process_start_ticks=process_start_ticks(pane.pane_pid),
            tmux_bin=self.tmux_bin,
        )
        self.runtimes[agent_id] = runtime
        self._persist_runtime(runtime, verified=True)
        return {
            **runtime.public_dict(),
            "running": True,
            "started": True,
            "opened": True,
        }

    def status(self, agent_id: str) -> dict[str, Any]:
        runtime = self._runtime(agent_id)
        if runtime is None:
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "agent_id": agent_id,
                "running": False,
            }
        pane, state, reason = self._validate_runtime(runtime)
        if pane is None:
            runtime = replace(runtime, state=state)
            self.runtimes[agent_id] = runtime
            self._persist_runtime(runtime, last_error=reason)
            return {
                **runtime.public_dict(),
                "running": False,
                "state": state,
                "error": reason,
                "recoverable": state == "stale",
            }
        adopted = runtime.state in {"detached", "starting"}
        runtime = replace(
            runtime,
            state="ready",
            window_id=pane.window_id,
            pane_pid=pane.pane_pid,
        )
        self.runtimes[agent_id] = runtime
        self._persist_runtime(runtime, verified=True)
        return {
            **runtime.public_dict(),
            "running": True,
            "adopted": adopted,
            "recoverable": False,
        }

    def stop(self, agent_id: str) -> dict[str, Any]:
        runtime = self._runtime(agent_id)
        if runtime is None:
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "agent_id": agent_id,
                "running": False,
                "stopped": False,
            }
        pane, state, reason = self._validate_runtime(runtime)
        if pane is None and state == "stale":
            self._persist_runtime(runtime, last_error=reason)
            return {
                **runtime.public_dict(),
                "running": False,
                "stopped": False,
                "state": "stale",
                "error": reason,
                "safe_to_forget": True,
            }
        if pane is not None:
            self._kill_session(self._identity(runtime), runtime.session_name)
        self.runtimes.pop(agent_id, None)
        if self.store is not None:
            self.store.delete_terminal_editor_runtime(agent_id)
        return {
            **runtime.public_dict(),
            "running": False,
            "state": "stopped",
            "stopped": True,
        }

    def forget(self, agent_id: str) -> dict[str, Any]:
        runtime = self._runtime(agent_id)
        if runtime is None:
            return {
                "api_version": TERMINAL_EDITOR_API_VERSION,
                "agent_id": agent_id,
                "forgotten": False,
            }
        pane, state, reason = self._validate_runtime(runtime)
        if pane is not None:
            raise ValueError("cannot forget a live managed editor; stop it first")
        if state != "stale" and state != "exited":
            raise ValueError(reason or "managed editor cannot be forgotten safely")
        self.runtimes.pop(agent_id, None)
        if self.store is not None:
            self.store.delete_terminal_editor_runtime(agent_id)
        return {
            **runtime.public_dict(),
            "running": False,
            "forgotten": True,
        }

    def detach_all(self) -> list[dict[str, Any]]:
        detached: list[dict[str, Any]] = []
        for agent_id, runtime in tuple(self.runtimes.items()):
            pane, state, reason = self._validate_runtime(runtime)
            next_state = "detached" if pane is not None else state
            next_runtime = replace(runtime, state=next_state)
            self.runtimes[agent_id] = next_runtime
            self._persist_runtime(next_runtime, last_error=reason)
            detached.append(
                {
                    **next_runtime.public_dict(),
                    "running": pane is not None,
                    "detached": pane is not None,
                }
            )
        return detached

    def shutdown(self) -> list[dict[str, Any]]:
        if self.shutdown_policy == "stop":
            return self.stop_all()
        return self.detach_all()

    def stop_all(self) -> list[dict[str, Any]]:
        return [self.stop(agent_id) for agent_id in tuple(self.runtimes)]

    def _runtime(self, agent_id: str) -> TerminalEditorRuntime | None:
        runtime = self.runtimes.get(agent_id)
        if runtime is not None or self.store is None:
            return runtime
        record = self.store.get_terminal_editor_runtime(agent_id)
        if record is None:
            return None
        try:
            runtime = self._runtime_from_record(record)
        except (KeyError, TypeError, ValueError):
            return None
        self.runtimes[agent_id] = runtime
        return runtime

    def _load_persisted_runtimes(self) -> None:
        if self.store is None:
            return
        for record in self.store.list_terminal_editor_runtimes():
            try:
                runtime = self._runtime_from_record(record)
            except (KeyError, TypeError, ValueError):
                continue
            self.runtimes[runtime.agent_id] = runtime

    @staticmethod
    def _runtime_from_record(record: Mapping[str, Any]) -> TerminalEditorRuntime:
        return TerminalEditorRuntime(
            agent_id=str(record["agent_id"]),
            runtime_id=str(record["runtime_id"]),
            project=str(record.get("project") or ""),
            access_mode=str(record.get("access_mode") or "edit"),
            profile_id=str(record["profile_id"]),
            path=str(record["path"]),
            root=str(record["root"]),
            socket_path=str(record["socket_path"]),
            rpc_socket_path=(
                str(record["rpc_socket_path"])
                if record.get("rpc_socket_path")
                else None
            ),
            rpc_enabled=bool(record.get("rpc_enabled")),
            server_id=str(record["server_id"]),
            session_name=str(record["session_name"]),
            window_id=str(record.get("window_id") or ""),
            pane_id=str(record["pane_id"]),
            pane_pid=(int(record["pane_pid"]) if record.get("pane_pid") else None),
            process_start_ticks=(
                int(record["process_start_ticks"])
                if record.get("process_start_ticks") is not None
                else None
            ),
            tmux_bin=str(record["tmux_bin"]),
            state=str(record.get("state") or "ready"),
        )

    def _persist_runtime(
        self,
        runtime: TerminalEditorRuntime,
        *,
        last_error: str | None = None,
        verified: bool = False,
    ) -> None:
        if self.store is None:
            return
        self.store.upsert_terminal_editor_runtime(
            runtime.public_dict(),
            last_error=last_error,
            metadata={"owner": "agent-pbx", "kind": "managed-editor"},
            verified=verified,
        )

    def _validate_runtime(
        self,
        runtime: TerminalEditorRuntime,
    ) -> tuple[Any | None, str, str | None]:
        try:
            socket_path = Path(runtime.socket_path)
            socket_path.resolve(strict=False).relative_to(self.runtime_root)
        except (OSError, ValueError):
            return None, "stale", "editor tmux socket is outside the managed runtime root"
        try:
            panes = list_runtime_panes(self._identity(runtime))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return None, "exited", f"editor tmux server is unavailable: {exc}"
        pane = next((item for item in panes if item.pane_id == runtime.pane_id), None)
        if pane is None:
            return None, "exited", "managed editor pane is no longer present"
        if pane.session_name != runtime.session_name:
            return None, "stale", "editor pane session identity no longer matches"
        if runtime.window_id and pane.window_id != runtime.window_id:
            return None, "stale", "editor pane window identity no longer matches"
        if runtime.pane_pid is not None and pane.pane_pid != runtime.pane_pid:
            return None, "stale", "editor pane process id no longer matches"
        if runtime.process_start_ticks is not None and pane.pane_pid is not None:
            current_ticks = process_start_ticks(pane.pane_pid)
            if current_ticks != runtime.process_start_ticks:
                return None, "stale", "editor process start identity no longer matches"
        try:
            pane_root = Path(pane.cwd).resolve(strict=True)
            runtime_root = Path(runtime.root).resolve(strict=True)
        except OSError:
            return None, "stale", "editor project root is unavailable"
        if pane_root != runtime_root:
            return None, "stale", "editor pane cwd no longer matches its project root"
        if runtime.rpc_enabled:
            rpc_path = Path(str(runtime.rpc_socket_path or ""))
            if not self._rpc_socket_is_safe(rpc_path):
                return None, "stale", "managed Neovim RPC socket is unavailable or unsafe"
        return pane, "ready", None

    def _route_file(
        self,
        runtime: TerminalEditorRuntime,
        *,
        path: Path,
        line: int,
    ) -> None:
        profile = self._profile(runtime.profile_id)
        if not runtime.rpc_enabled or not runtime.rpc_socket_path:
            raise ValueError("the active editor does not support managed file routing")
        if not self._rpc_socket_is_safe(Path(runtime.rpc_socket_path)):
            raise RuntimeError("the managed Neovim RPC socket is unavailable or unsafe")
        command = [
            profile.argv[0],
            "--server",
            runtime.rpc_socket_path,
            "--remote",
            f"+{max(1, line)}",
            str(path),
        ]
        result = self.runner(command, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout or "Neovim remote-open failed").strip()
            )

    def _wait_for_rpc_socket(self, path: Path) -> bool:
        for _ in range(40):
            if self._rpc_socket_is_safe(path):
                return True
            time.sleep(0.05)
        return False

    def _rpc_socket_is_safe(self, path: Path) -> bool:
        if not path.is_absolute():
            return False
        try:
            path.resolve(strict=False).relative_to(self.runtime_root)
            details = path.lstat()
        except (OSError, ValueError):
            return False
        return (
            not path.is_symlink()
            and stat.S_ISSOCK(details.st_mode)
            and details.st_uid == os.getuid()
        )

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
