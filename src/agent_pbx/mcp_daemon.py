from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .paths import default_state_root
from .store import Store
from .workerbee import WORKERBEE_BIN_ENV, WORKERBEE_CACHE_ENV, WORKERBEE_TIMEOUT_ENV


MCP_DAEMON_FILE = "mcp-daemon.json"
MCP_DAEMON_LOG = "mcp-daemon.log"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True, slots=True)
class MCPDaemonConfig:
    state_root: Path
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path | None = None
    token: str | None = None
    allow_insecure_lan: bool = False
    debug: bool = False
    debug_smoke: bool = False
    log_level: str | None = None
    workerbee_bin: Path | None = None
    workerbee_timeout_seconds: float = 20.0
    workerbee_cache_seconds: float = 10.0

    @property
    def resolved_state_root(self) -> Path:
        return self.state_root.expanduser().resolve()

    @property
    def global_dir(self) -> Path:
        return self.resolved_state_root / "global"

    @property
    def metadata_file(self) -> Path:
        return self.global_dir / MCP_DAEMON_FILE

    @property
    def log_file(self) -> Path:
        return self.global_dir / MCP_DAEMON_LOG

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return self.db_path.expanduser().resolve()
        return self.resolved_state_root / "agent-pbx.sqlite"

    @property
    def mcp_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/healthz"

    @property
    def codex_command(self) -> str:
        return f"codex mcp add agent-pbx --url {self.mcp_url}"

    @property
    def lan_bound(self) -> bool:
        return self.host not in LOOPBACK_HOSTS


def config_from_args(
    *,
    state_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Path | None = None,
    token: str | None = None,
    allow_insecure_lan: bool = False,
    debug: bool = False,
    debug_smoke: bool = False,
    log_level: str | None = None,
    workerbee_bin: Path | None = None,
    workerbee_timeout_seconds: float = 20.0,
    workerbee_cache_seconds: float = 10.0,
) -> MCPDaemonConfig:
    return MCPDaemonConfig(
        state_root=(state_root or default_state_root()).expanduser().resolve(),
        host=host,
        port=port,
        db_path=db_path,
        token=token,
        allow_insecure_lan=allow_insecure_lan,
        debug=debug,
        debug_smoke=debug_smoke,
        log_level=log_level,
        workerbee_bin=workerbee_bin.expanduser() if workerbee_bin else None,
        workerbee_timeout_seconds=workerbee_timeout_seconds,
        workerbee_cache_seconds=workerbee_cache_seconds,
    )


def start_mcp_daemon(config: MCPDaemonConfig, *, timeout: float = 30.0) -> dict[str, Any]:
    auth_guard = lan_auth_guard(config)
    if auth_guard is not None:
        return {**_base_status(config), "ok": False, "started": False, "error": auth_guard}

    status = mcp_daemon_status(config)
    if status["running"]:
        return {**status, "ok": True, "started": False}
    if status.get("stale"):
        _cleanup_stale_metadata(config)

    port_check = _mcp_port_available(config)
    if not port_check["ok"]:
        return {
            **_base_status(config),
            "ok": False,
            "started": False,
            "running": False,
            "error": port_check["error"],
        }

    config.global_dir.mkdir(parents=True, exist_ok=True)
    config.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    if config.token:
        child_env["AGENT_PBX_TOKEN"] = config.token
    if config.workerbee_bin:
        child_env[WORKERBEE_BIN_ENV] = str(config.workerbee_bin.expanduser())
    child_env[WORKERBEE_TIMEOUT_ENV] = str(config.workerbee_timeout_seconds)
    child_env[WORKERBEE_CACHE_ENV] = str(config.workerbee_cache_seconds)

    argv = _serve_argv(config)
    log = open(config.log_file, "ab")  # noqa: SIM115 - passed to detached child
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=child_env,
        )
    except Exception as exc:  # noqa: BLE001
        return _start_error(config, exc, code="MCP_SPAWN_FAILED")
    finally:
        log.close()

    metadata = {
        "pid": int(proc.pid),
        "argv": argv,
        "state_root": str(config.resolved_state_root),
        "host": config.host,
        "port": config.port,
        "db_path": str(config.resolved_db_path),
        "token_configured": bool(config.token),
        "allow_insecure_lan": config.allow_insecure_lan,
        "debug": config.debug,
        "debug_smoke": config.debug_smoke,
        "workerbee_bin": str(config.workerbee_bin) if config.workerbee_bin else None,
        "workerbee_timeout_seconds": config.workerbee_timeout_seconds,
        "workerbee_cache_seconds": config.workerbee_cache_seconds,
        "mcp_url": config.mcp_url,
        "health_url": config.health_url,
        "log_file": str(config.log_file),
        "metadata_file": str(config.metadata_file),
        "codex_command": config.codex_command,
        "started_at": time.time(),
    }
    _write_metadata(config.metadata_file, metadata)
    try:
        ready = _wait_ready(config, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        _cleanup_failed_start(config)
        return _start_error(config, exc, code="MCP_NOT_READY")
    metadata.update(ready)
    _write_metadata(config.metadata_file, metadata)
    return {**mcp_daemon_status(config), "ok": True, "started": True}


def stop_mcp_daemon(config: MCPDaemonConfig, *, timeout: float = 10.0) -> dict[str, Any]:
    metadata = _read_metadata(config.metadata_file)
    if not metadata:
        return {**_base_status(config), "running": False, "stopped": False, "stale": False}
    pid = _metadata_pid(metadata)
    if pid is None or not _pid_alive(pid):
        _cleanup_stale_metadata(config)
        return {
            **_base_status(config),
            "running": False,
            "stopped": False,
            "stale": True,
            "pid": pid,
        }
    if not _pid_matches_metadata(pid, config, metadata):
        return {
            **_base_status(config),
            "running": False,
            "stopped": False,
            "pid": pid,
            "error": "PID does not look like the Agent PBX MCP daemon for this state root",
        }

    _terminate_process_group(pid, timeout=timeout)
    stopped = not _pid_alive(pid)
    if stopped:
        with suppress(OSError):
            config.metadata_file.unlink()
    return {
        **_base_status(config),
        "running": not stopped,
        "stopped": stopped,
        "pid": pid,
    }


def restart_mcp_daemon(config: MCPDaemonConfig, *, timeout: float = 30.0) -> dict[str, Any]:
    auth_guard = lan_auth_guard(config)
    if auth_guard is not None:
        start = {**_base_status(config), "ok": False, "started": False, "error": auth_guard}
        return {"ok": False, "stop": None, "start": start, "port_release": None}
    stop = stop_mcp_daemon(config, timeout=min(max(timeout, 1.0), 10.0))
    port_release = _wait_for_port_release(config, timeout=min(max(timeout, 1.0), 10.0))
    if not port_release["ok"]:
        start = {
            **_base_status(config),
            "ok": False,
            "started": False,
            "running": False,
            "error": port_release["error"],
        }
        return {"ok": False, "stop": stop, "start": start, "port_release": port_release}
    start = start_mcp_daemon(config, timeout=timeout)
    return {"ok": bool(start.get("ok")), "stop": stop, "start": start, "port_release": port_release}


def mcp_daemon_status(config: MCPDaemonConfig) -> dict[str, Any]:
    metadata = _read_metadata(config.metadata_file)
    status = _base_status(config)
    if not metadata:
        return {**status, "running": False, "stale": False}
    pid = _metadata_pid(metadata)
    running = bool(pid and _pid_alive(pid) and _pid_matches_metadata(pid, config, metadata))
    return {
        **status,
        **metadata,
        "pid": pid,
        "running": running,
        "stale": bool(pid and not running),
    }


def _base_status(config: MCPDaemonConfig) -> dict[str, Any]:
    return {
        "host": config.host,
        "port": config.port,
        "mcp_url": config.mcp_url,
        "health_url": config.health_url,
        "db_path": str(config.resolved_db_path),
        "state_root": str(config.resolved_state_root),
        "log_file": str(config.log_file),
        "metadata_file": str(config.metadata_file),
        "codex_command": config.codex_command,
        "workerbee_bin": str(config.workerbee_bin) if config.workerbee_bin else None,
        "workerbee_timeout_seconds": config.workerbee_timeout_seconds,
        "workerbee_cache_seconds": config.workerbee_cache_seconds,
    }


def _serve_argv(config: MCPDaemonConfig) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "agent_pbx",
        "mcp",
        "serve",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--db",
        str(config.resolved_db_path),
    ]
    if config.allow_insecure_lan:
        argv.append("--allow-insecure-lan")
    if config.debug:
        argv.append("--debug")
    if config.debug_smoke:
        argv.append("--debug-smoke")
    if config.log_level:
        argv.extend(["--log-level", config.log_level])
    return argv


def lan_auth_guard(config: MCPDaemonConfig) -> dict[str, Any] | None:
    if not config.lan_bound or config.allow_insecure_lan:
        return None
    if config.token:
        return None
    if config.resolved_db_path.exists():
        store = Store(config.resolved_db_path)
        try:
            if store.has_tokens():
                return None
        except OSError:
            pass
    return {
        "code": "LAN_BIND_REQUIRES_TOKEN",
        "message": "Agent PBX LAN daemon binds require a bearer token",
        "details": {"host": config.host, "port": config.port},
        "retryable": True,
        "remediation": (
            "Set AGENT_PBX_TOKEN, pass --token, or explicitly pass "
            "--allow-insecure-lan for a controlled lab-only run."
        ),
    }


def _mcp_port_available(config: MCPDaemonConfig) -> dict[str, Any]:
    family = socket.AF_INET6 if ":" in config.host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((config.host, int(config.port)))
    except OSError as exc:
        return {
            "ok": False,
            "error": {
                "code": "MCP_PORT_IN_USE",
                "message": f"Agent PBX MCP port {config.host}:{config.port} is already in use",
                "details": {
                    "host": config.host,
                    "port": config.port,
                    "mcp_url": config.mcp_url,
                    "error": str(exc),
                },
                "retryable": True,
                "remediation": (
                    "Stop the process using this port or run "
                    "`agent-pbx mcp start --port <port>`."
                ),
            },
        }
    return {"ok": True}


def _wait_for_port_release(config: MCPDaemonConfig, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout, 0.0)
    last = _mcp_port_available(config)
    while not last["ok"] and time.monotonic() < deadline:
        time.sleep(0.2)
        last = _mcp_port_available(config)
    if last["ok"]:
        return {"ok": True, "host": config.host, "port": config.port}
    error = dict(last.get("error") or {})
    error["message"] = (
        "Agent PBX MCP port did not become available after stopping the previous daemon"
    )
    return {"ok": False, "host": config.host, "port": config.port, "error": error}


def _wait_ready(config: MCPDaemonConfig, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        _raise_if_dead(config)
        if _tcp_ready(config.host, config.port) and _health_ready(config.health_url):
            _raise_if_dead(config)
            return {"ready_at": time.time()}
        time.sleep(0.25)
    raise TimeoutError(f"Agent PBX MCP did not become ready at {config.mcp_url}")


def _raise_if_dead(config: MCPDaemonConfig) -> None:
    metadata = _read_metadata(config.metadata_file)
    pid = _metadata_pid(metadata)
    if pid is not None and not _pid_alive(pid):
        raise RuntimeError(f"Agent PBX MCP daemon exited early; inspect {config.log_file}")


def _tcp_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _health_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.0) as response:  # noqa: S310 - local dev URL
            return int(response.status) == 200
    except (OSError, URLError):
        return False


def _cleanup_failed_start(config: MCPDaemonConfig) -> None:
    metadata = _read_metadata(config.metadata_file)
    pid = _metadata_pid(metadata)
    if pid is not None and _pid_alive(pid):
        _terminate_process_group(pid, timeout=5.0)
    _cleanup_stale_metadata(config)


def _cleanup_stale_metadata(config: MCPDaemonConfig) -> None:
    with suppress(OSError):
        config.metadata_file.unlink()


def _start_error(
    config: MCPDaemonConfig,
    exc: Exception,
    *,
    code: str,
) -> dict[str, Any]:
    return {
        **_base_status(config),
        "ok": False,
        "started": False,
        "running": False,
        "error": {
            "code": code,
            "message": str(exc),
            "retryable": True,
        },
    }


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _metadata_pid(metadata: dict[str, Any]) -> int | None:
    try:
        pid = int(metadata.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_matches_metadata(
    pid: int, config: MCPDaemonConfig, metadata: dict[str, Any]
) -> bool:
    if str(metadata.get("state_root") or "") != str(config.resolved_state_root):
        return False
    cmdline_path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = cmdline_path.read_bytes()
    except OSError:
        return not Path("/proc").exists()
    cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    return "agent_pbx" in cmdline and "mcp" in cmdline and "serve" in cmdline


def _terminate_process_group(pid: int, *, timeout: float) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(timeout, 0.0)
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(pid):
        with suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
