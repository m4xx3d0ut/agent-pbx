from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
from typing import Any, Callable

import httpx

from . import __version__
from .codex_cli import inspect_codex_posture
from .codex_theme import MANAGED_CODEX_THEME_NAME, managed_codex_theme_status
from .compat import compatibility_posture
from .mcp_daemon import MCPDaemonConfig, lan_auth_guard, mcp_daemon_status
from .operator_broker import OperatorBrokerPolicyError, operator_broker_posture
from .store import SCHEMA_VERSION, Store
from .tmux_binary import (
    configured_tmux_binary,
    managed_tmux_shim_status,
    resolve_tmux_binary,
)
from .runtime_tmux import RUNTIME_TMUX_SOCKET_ENV, tmux_socket_from_environment
from .ui.theme import terminal_color_depth
from .workerbee_mcp import inspect_workerbee_mcp, inspect_workerbee_switcher
from .workerbee_runtime import (
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    WorkerBeeRuntimeRegistry,
    canonical_workerbee_backend,
)
from .workerbee_workspace import WORKERBEE_WORKSPACE_HELPER_ENV
from .workerbee_policy import WorkerBeeWorkspacePolicyService
from .workerbee_snapshot_policy import WorkerBeeSnapshotPolicyService
from .workerbee_materializer import (
    DEBUG_STAGE_PYTHON_ENV,
    DEBUG_STAGE_SCRIPT_ENV,
)
from .workerbee_controlled_run import (
    WorkerBeeControlledRunError,
    controlled_run_posture,
    load_controlled_run_profiles,
)
from .workerbee_modes import (
    WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE,
    resolve_workerbee_tool_surface,
)


DOCTOR_API_VERSION = "agent-pbx.doctor/v2"
SUPPORTED_SYSTEMS = {"Linux", "Darwin"}
REQUIRED_EXECUTABLES = ("git", "ssh")
OPTIONAL_CLIPBOARD_EXECUTABLES = (
    "wl-paste",
    "xclip",
    "xsel",
    "pbpaste",
    "termux-clipboard-get",
)


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    check_id: str
    status: str
    summary: str
    detail: str = ""
    remediation: str = ""

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]
    platform: str
    architecture: str
    python_version: str
    agent_pbx_version: str

    @property
    def ok(self) -> bool:
        return all(item.status != "fail" for item in self.checks)

    @property
    def warning_count(self) -> int:
        return sum(item.status == "warn" for item in self.checks)

    @property
    def failure_count(self) -> int:
        return sum(item.status == "fail" for item in self.checks)

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": DOCTOR_API_VERSION,
            "ok": self.ok,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "platform": self.platform,
            "architecture": self.architecture,
            "python_version": self.python_version,
            "agent_pbx_version": self.agent_pbx_version,
            "checks": [item.public_dict() for item in self.checks],
        }


def run_platform_doctor(
    config: MCPDaemonConfig,
    *,
    codex_command: str = "codex",
    timeout: float = 8.0,
    probe_services: bool = True,
    which: Callable[[str], str | None] = shutil.which,
) -> DoctorReport:
    checks: list[DoctorCheck] = []
    system = platform.system() or "unknown"
    architecture = platform.machine() or "unknown"
    python_version = platform.python_version()

    checks.append(
        DoctorCheck(
            "platform",
            "pass" if system in SUPPORTED_SYSTEMS else "warn",
            f"{system} {architecture}",
            remediation=(
                "Use a supported Linux or macOS host, or qualify this platform before production use."
                if system not in SUPPORTED_SYSTEMS
                else ""
            ),
        )
    )
    python_ok = sys.version_info >= (3, 10)
    checks.append(
        DoctorCheck(
            "python",
            "pass" if python_ok else "fail",
            f"Python {python_version}",
            remediation="Install Python 3.10 or newer." if not python_ok else "",
        )
    )
    checks.append(_dependency_check())
    checks.extend(_executable_checks(which))
    checks.append(_tmux_check(which, timeout))
    checks.append(_managed_tmux_command_check(config.resolved_state_root, which))
    checks.append(_launcher_permission_check())
    checks.append(_operator_broker_policy_check())
    checks.append(_terminal_check())
    checks.append(_database_check(config.resolved_db_path))
    checks.append(_daemon_check(config))
    checks.append(_security_check(config))
    checks.append(_compatibility_check(config))
    checks.append(_codex_check(codex_command, timeout))
    checks.append(_codex_theme_check())
    checks.append(_workerbee_check(config, which, timeout))
    checks.append(_workerbee_switcher_check(which, timeout))
    checks.append(_workerbee_runtime_binding_check(config, which, timeout))
    checks.append(_workerbee_tool_surface_runtime_check(config, timeout))
    checks.append(_workerbee_workspace_policy_check(config))
    checks.append(_workerbee_controlled_run_check())
    checks.append(_joplin_check(config, timeout, probe_services=probe_services))
    checks.append(_clipboard_check(which))
    return DoctorReport(
        checks=tuple(checks),
        platform=system,
        architecture=architecture,
        python_version=python_version,
        agent_pbx_version=__version__,
    )


def doctor_markdown(report: DoctorReport) -> str:
    lines = [
        f"Agent PBX {report.agent_pbx_version} doctor",
        f"Host: {report.platform} {report.architecture} · Python {report.python_version}",
        "",
    ]
    symbols = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    for item in report.checks:
        lines.append(f"[{symbols.get(item.status, item.status.upper())}] {item.check_id}: {item.summary}")
        if item.detail:
            lines.append(f"       {item.detail}")
        if item.remediation:
            lines.append(f"       Fix: {item.remediation}")
    lines.extend(
        (
            "",
            f"Result: {report.failure_count} failure(s), {report.warning_count} warning(s).",
        )
    )
    return "\n".join(lines)


def _dependency_check() -> DoctorCheck:
    required = ("fastapi", "httpx", "mcp", "pydantic", "textual", "uvicorn", "websockets")
    versions: list[str] = []
    missing: list[str] = []
    for name in required:
        try:
            versions.append(f"{name}={importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return DoctorCheck(
        "python-dependencies",
        "fail" if missing else "pass",
        "required packages available" if not missing else f"missing: {', '.join(missing)}",
        detail=", ".join(versions),
        remediation="Reinstall Agent PBX from the release wheelhouse." if missing else "",
    )


def _launcher_permission_check(
    launcher: Path | None = None,
) -> DoctorCheck:
    path = (
        launcher
        or Path(os.getenv("AGENT_PBX_LAUNCHER") or "~/.local/bin/apbx")
    ).expanduser()
    if not path.exists():
        return DoctorCheck(
            "local-launcher-permissions",
            "pass",
            f"optional launcher not installed: {path}",
        )
    try:
        stat = path.stat()
    except OSError as exc:
        return DoctorCheck(
            "local-launcher-permissions",
            "warn",
            f"unable to inspect {path}",
            detail=str(exc),
            remediation=f"Inspect ownership and permissions for {path}.",
        )
    mode = stat.st_mode & 0o777
    owner_ok = not hasattr(os, "getuid") or stat.st_uid == os.getuid()
    private = mode & 0o077 == 0
    executable = bool(mode & 0o100)
    ok = owner_ok and private and executable
    return DoctorCheck(
        "local-launcher-permissions",
        "pass" if ok else "warn",
        f"{path} mode {mode:04o}",
        detail=(
            "launcher is private and user-owned"
            if ok
            else f"owner_ok={owner_ok}; private={private}; executable={executable}"
        ),
        remediation=(
            "Set the launcher owner to the Agent PBX user and run "
            f"`chmod 700 {path}`."
            if not ok
            else ""
        ),
    )


def _operator_broker_policy_check() -> DoctorCheck:
    try:
        posture = operator_broker_posture()
    except OperatorBrokerPolicyError as exc:
        return DoctorCheck(
            "operator-broker-policy",
            "fail",
            exc.message,
            remediation="Correct the AGENT_PBX_OPERATOR_BROKER_* environment values.",
        )
    summary = (
        f"mode={posture.mode}; controls={posture.control_mode}; "
        f"ttl={posture.request_ttl_seconds}s; "
        f"max_in_flight={posture.max_in_flight_per_root}"
    )
    return DoctorCheck(
        "operator-broker-policy",
        "pass",
        summary,
        detail=(
            "Root-supervised execution is active; exact handoff, campaign, fork, "
            "workspace, parent, paths, and subject checks still apply."
            if posture.root_operator_enabled
            else "Guided mode keeps each stop, broker, resume, and validation step explicit."
        ),
    )


def _executable_checks(which: Callable[[str], str | None]) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for executable in REQUIRED_EXECUTABLES:
        path = which(executable)
        checks.append(
            DoctorCheck(
                executable,
                "pass" if path else "fail",
                path or f"{executable} not found",
                remediation=f"Install {executable} and ensure it is on PATH." if not path else "",
            )
        )
    return checks


def _tmux_check(which: Callable[[str], str | None], timeout: float) -> DoctorCheck:
    configured = configured_tmux_binary()
    path = resolve_tmux_binary(which=which)
    if not path:
        return DoctorCheck(
            "tmux",
            "fail",
            "tmux not found",
            remediation="Install tmux 3.2 or newer for native runtime sessions.",
        )
    try:
        result = subprocess.run(
            [path, "-V"], capture_output=True, text=True, timeout=max(1.0, timeout)
        )
        version = (result.stdout or result.stderr).strip()
    except Exception as exc:  # noqa: BLE001 - doctor normalizes host failures
        return DoctorCheck("tmux", "fail", f"tmux probe failed ({type(exc).__name__})")
    outer_socket = tmux_socket_from_environment(os.getenv("TMUX"))
    outer = outer_socket is not None
    details = [
        f"configured command: {configured}; resolved path: {path}; "
        f"outer tmux detected: {'yes' if outer else 'no'}"
    ]
    warnings: list[str] = []
    client_version = version.removeprefix("tmux ").strip()
    if outer_socket is not None:
        server = _tmux_server_diagnostic(path, outer_socket, timeout)
        if server:
            details.append(f"outer server: {server['description']}")
            if server.get("version") and server["version"] != client_version:
                warnings.append(
                    f"outer client/server version skew ({client_version} vs {server['version']})"
                )
    runtime_socket_raw = str(os.getenv(RUNTIME_TMUX_SOCKET_ENV) or "").strip()
    if runtime_socket_raw:
        runtime_socket = Path(runtime_socket_raw).expanduser()
        runtime_bin = configured_tmux_binary()
        runtime = _tmux_server_diagnostic(runtime_bin, runtime_socket, timeout)
        if runtime:
            details.append(f"runtime server: {runtime['description']}")
            if runtime.get("plugin_contaminated"):
                warnings.append("dedicated runtime inherited resurrect/continuum options")
            if runtime.get("no_color"):
                warnings.append("dedicated runtime inherited NO_COLOR")
        else:
            details.append(f"runtime server: unavailable at {runtime_socket}")
    status = "fail" if result.returncode != 0 else "warn" if warnings else "pass"
    return DoctorCheck(
        "tmux",
        status,
        (version or path) + (f"; {'; '.join(warnings)}" if warnings else ""),
        detail="; ".join(details),
        remediation=(
            "Start or reconcile dedicated runtimes through Agent PBX so tmux "
            "options, color environment, and client/server versions are aligned."
            if warnings
            else ""
        ),
    )


def _tmux_server_diagnostic(
    tmux_bin: str,
    socket_path: Path,
    timeout: float,
) -> dict[str, Any] | None:
    """Return live server identity without trusting the invoking client version."""

    try:
        identity = subprocess.run(
            [
                tmux_bin,
                "-S",
                str(socket_path),
                "display-message",
                "-p",
                "#{version}\t#{pid}",
            ],
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
        )
    except Exception:  # noqa: BLE001 - doctor converts host failures to evidence
        return None
    if identity.returncode != 0:
        return None
    fields = identity.stdout.strip().split("\t")
    if len(fields) != 2 or not fields[0]:
        return None
    version, pid_text = fields
    try:
        pid = int(pid_text)
    except ValueError:
        pid = 0
    executable = ""
    if pid > 0 and sys.platform.startswith("linux"):
        try:
            executable = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            executable = ""
    options = subprocess.run(
        [tmux_bin, "-S", str(socket_path), "show-options", "-g"],
        capture_output=True,
        text=True,
        timeout=max(1.0, timeout),
    )
    option_text = options.stdout.casefold() if options.returncode == 0 else ""
    contaminated = "continuum" in option_text or "resurrect" in option_text
    environment = subprocess.run(
        [tmux_bin, "-S", str(socket_path), "show-environment", "-g"],
        capture_output=True,
        text=True,
        timeout=max(1.0, timeout),
    )
    server_environment: dict[str, str] = {}
    if environment.returncode == 0:
        for line in environment.stdout.splitlines():
            if not line or line.startswith("-") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            server_environment[key] = value
    no_color = str(server_environment.get("NO_COLOR") or "").strip()
    color_term = str(server_environment.get("COLORTERM") or "").strip()
    description = f"{version} pid={pid or 'unknown'} socket={socket_path}"
    if executable:
        description += f" exe={executable}"
    if contaminated:
        description += " plugins=resurrect/continuum"
    if no_color:
        description += f" NO_COLOR={no_color}"
    if color_term:
        description += f" COLORTERM={color_term}"
    return {
        "version": version,
        "pid": pid or None,
        "executable": executable or None,
        "plugin_contaminated": contaminated,
        "no_color": no_color or None,
        "color_term": color_term or None,
        "description": description,
    }


def _managed_tmux_command_check(
    state_root: Path,
    which: Callable[[str], str | None],
) -> DoctorCheck:
    status = managed_tmux_shim_status(state_root=state_root, which=which)
    ready = bool(status.get("ready"))
    state = str(status.get("state") or "unknown")
    return DoctorCheck(
        "managed-tmux-command",
        "pass" if ready else "warn",
        "managed panes resolve the configured tmux binary"
        if ready
        else f"managed tmux command is {state}",
        detail=(
            f"shim={status.get('path')}; target={status.get('target') or '-'}; "
            f"shim_target={status.get('shim_target') or '-'}"
        ),
        remediation=(
            "Restart the Agent PBX daemon to install the private runtime tmux shim; "
            "then restart/resume affected managed sessions at a safe turn boundary."
            if not ready
            else ""
        ),
    )


def _terminal_check() -> DoctorCheck:
    term = os.getenv("TERM", "") or "unset"
    depth = terminal_color_depth()
    return DoctorCheck(
        "terminal",
        "warn" if term in {"unset", "dumb"} else "pass",
        f"TERM={term}; color depth={depth}",
        detail="Function-key passthrough should be qualified with the TUI key probe over SSH/Termux.",
    )


def _database_check(path: Path) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            "database",
            "warn",
            f"database does not exist yet: {path}",
            detail=f"It will be created at schema {SCHEMA_VERSION} on first daemon start.",
        )
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            schema = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001 - doctor must report corrupt/legacy DBs
        return DoctorCheck(
            "database",
            "fail",
            f"database inspection failed ({type(exc).__name__})",
            remediation="Stop the daemon and restore a verified Agent PBX backup.",
        )
    status = "pass" if integrity == "ok" and schema == SCHEMA_VERSION else "warn"
    remediation = "Run `agent-pbx migrate dry-run` and back up before applying migration."
    return DoctorCheck(
        "database",
        status,
        f"schema {schema}; integrity={integrity}",
        detail=str(path),
        remediation="" if status == "pass" else remediation,
    )


def _daemon_check(config: MCPDaemonConfig) -> DoctorCheck:
    status = mcp_daemon_status(config)
    running = bool(status.get("running"))
    daemon_version = str(status.get("daemon_agent_pbx_version") or "").strip()
    skew = bool(daemon_version and daemon_version != __version__)
    version_unknown = bool(running and not daemon_version)
    state = "warn" if not running or skew or version_unknown else "pass"
    summary = "running" if running else "not running"
    if daemon_version:
        summary += f"; daemon={daemon_version}; shell={__version__}"
    return DoctorCheck(
        "daemon",
        state,
        summary,
        detail=str(config.metadata_file),
        remediation=(
            "Restart the daemon after upgrade so daemon and shell versions match."
            if skew
            else "Restart the daemon once so it records its running Agent PBX version."
            if version_unknown
            else "Start the daemon for live API/TUI validation."
            if not running
            else ""
        ),
    )


def _security_check(config: MCPDaemonConfig) -> DoctorCheck:
    guard = lan_auth_guard(config)
    if guard is not None:
        return DoctorCheck(
            "security",
            "fail",
            str(guard.get("message") or guard.get("code") or "unsafe listener"),
            remediation=str(guard.get("remediation") or "Bind to loopback."),
        )
    if config.lan_bound:
        return DoctorCheck(
            "security",
            "pass" if not config.allow_insecure_lan else "warn",
            "LAN listener uses token and TLS"
            if not config.allow_insecure_lan
            else "insecure LAN override enabled",
            remediation=(
                "Remove the insecure override and configure token plus TLS."
                if config.allow_insecure_lan
                else ""
            ),
        )
    return DoctorCheck("security", "pass", "loopback-only listener")


def _compatibility_check(config: MCPDaemonConfig) -> DoctorCheck:
    posture = compatibility_posture(polling=config.legacy_polling_enabled)
    enabled = [
        name
        for name, value in (
            ("polling", posture.polling),
            ("Thread", posture.thread_tab),
            ("terminal capture", posture.terminal_capture),
        )
        if value
    ]
    disabled = [
        name
        for name, value in (
            ("polling", posture.polling),
            ("Thread", posture.thread_tab),
            ("terminal capture", posture.terminal_capture),
        )
        if not value
    ]
    return DoctorCheck(
        "v2-compatibility",
        "warn" if disabled else "pass",
        f"retained paths enabled: {', '.join(enabled) or 'none'}",
        detail=json.dumps(posture.public_dict(), sort_keys=True),
        remediation=(
            "Re-enable retained v2.0 paths until measured parity and migration "
            "criteria are satisfied: " + ", ".join(disabled) + "."
            if disabled
            else ""
        ),
    )


def _codex_check(command: str, timeout: float) -> DoctorCheck:
    if shutil.which(command) is None and not Path(command).expanduser().exists():
        return DoctorCheck(
            "codex",
            "fail",
            f"Codex executable not found: {command}",
            remediation="Install @openai/codex and rerun doctor.",
        )
    posture = inspect_codex_posture(command, timeout=max(1.0, timeout))
    if not posture.shell_version:
        return DoctorCheck(
            "codex",
            "fail",
            "Codex version probe failed",
            remediation="Run `codex --version` and `codex doctor` directly.",
        )
    status = "warn" if posture.warnings else "pass"
    versions = (
        f"shell={posture.shell_version}; app-server={posture.app_server_version or 'unknown'}; "
        f"latest={posture.latest_stable_version or 'unknown'}"
    )
    model = posture.configured_model or "unset"
    return DoctorCheck(
        "codex",
        status,
        versions,
        detail=f"model={model}; catalog={posture.model_catalog_count}",
        remediation=" ".join(posture.warnings),
    )


def _codex_theme_check() -> DoctorCheck:
    theme = managed_codex_theme_status()
    if theme.ready:
        return DoctorCheck(
            "codex-theme",
            "pass",
            f"{MANAGED_CODEX_THEME_NAME} installed and selected",
            detail=f"{theme.path}; sha256={theme.expected_sha256}",
        )
    reasons: list[str] = []
    if theme.state != "matched":
        reasons.append(f"asset={theme.state}")
    if not theme.selected:
        reasons.append(f"selected={theme.configured_theme or 'unset'}")
    return DoctorCheck(
        "codex-theme",
        "warn",
        "; ".join(reasons) or "managed theme is not ready",
        detail=theme.path,
        remediation="Run `agent-pbx codex theme install` and restart/resume Codex sessions at a safe turn boundary.",
    )


def _workerbee_check(
    config: MCPDaemonConfig,
    which: Callable[[str], str | None],
    timeout: float,
) -> DoctorCheck:
    candidate = str(config.workerbee_bin) if config.workerbee_bin else which("workerbee")
    if not candidate:
        return DoctorCheck(
            "workerbee",
            "warn",
            "WorkerBee executable is not configured",
            remediation="Configure AGENT_PBX_WORKERBEE_BIN to enable WorkerBee panels and probes.",
        )
    path = Path(candidate).expanduser()
    if not path.exists() and which(candidate) is None:
        return DoctorCheck(
            "workerbee",
            "warn",
            f"WorkerBee executable is unavailable: {candidate}",
            remediation="Correct AGENT_PBX_WORKERBEE_BIN and rerun doctor.",
        )
    try:
        result = subprocess.run(
            [candidate, "--version"],
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
        )
        version = (result.stdout or result.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("workerbee", "warn", f"version probe failed ({type(exc).__name__})")
    return DoctorCheck(
        "workerbee",
        "pass" if result.returncode == 0 else "warn",
        version or candidate,
        detail="Run the MCP path-visibility diagnostic before automatic project skill injection.",
    )


def _workerbee_switcher_check(
    which: Callable[[str], str | None],
    timeout: float,
) -> DoctorCheck:
    provenance = inspect_workerbee_switcher(
        timeout_seconds=timeout,
        which=which,
    )
    if not provenance.configured:
        return DoctorCheck(
            "workerbee-mcp-switcher",
            "pass",
            "not configured; direct WorkerBee MCP configuration is supported",
            remediation=provenance.remediation,
        )
    detail = " · ".join(
        item
        for item in (
            provenance.endpoint,
            (
                f"{provenance.server_name} {provenance.server_version}".strip()
                if provenance.server_name or provenance.server_version
                else ""
            ),
            provenance.config_path,
        )
        if item
    )
    return DoctorCheck(
        "workerbee-mcp-switcher",
        "pass" if provenance.ready else "warn",
        (
            f"{provenance.backend or 'unknown'} ({provenance.code})"
        ),
        detail=detail,
        remediation=provenance.remediation,
    )


def _workerbee_runtime_binding_check(
    config: MCPDaemonConfig,
    which: Callable[[str], str | None],
    timeout: float,
) -> DoctorCheck:
    """Report the selected backend generation and appliance materializer posture."""

    provenance = inspect_workerbee_switcher(
        timeout_seconds=timeout,
        which=which,
    )
    if not provenance.configured:
        return DoctorCheck(
            "workerbee-runtime-binding",
            "pass",
            "direct WorkerBee configuration; runtime generation binding is inactive",
        )
    if not provenance.ready:
        return DoctorCheck(
            "workerbee-runtime-binding",
            "warn",
            "selected WorkerBee runtime is unavailable",
            remediation=provenance.remediation,
        )
    registry = WorkerBeeRuntimeRegistry(
        config.resolved_db_path.parent / "workerbee-runtime"
    )
    selected = registry.selected()
    if selected is None:
        return DoctorCheck(
            "workerbee-runtime-binding",
            "warn",
            f"{provenance.backend or 'unknown'} selected but no runtime generation is recorded",
            detail=provenance.endpoint,
            remediation="Start or restart the Agent PBX daemon to record the selected WorkerBee runtime generation.",
        )
    backend = canonical_workerbee_backend(provenance.backend)
    if backend != selected.backend or provenance.endpoint.rstrip("/") != selected.endpoint.rstrip("/"):
        return DoctorCheck(
            "workerbee-runtime-binding",
            "warn",
            "selected WorkerBee runtime differs from the recorded session-binding generation",
            detail=(
                f"selector={backend} {provenance.endpoint}; "
                f"recorded={selected.backend} {selected.endpoint} generation={selected.generation}"
            ),
            remediation="Restart the Agent PBX daemon, then restart or resume affected Codex sessions through Agent PBX.",
        )
    if selected.backend == WORKERBEE_BACKEND_USER_APPLIANCE:
        return DoctorCheck(
            "workerbee-runtime-binding",
            "warn",
            f"user_appliance generation {selected.generation} is selected but not qualified",
            detail=selected.endpoint,
            remediation="Use OSS or platform-debug until the user-appliance workspace image is qualified.",
        )
    details = [
        selected.endpoint,
        f"strategy={selected.workspace_strategy}",
        f"instance={selected.instance_id}",
    ]
    if selected.backend == WORKERBEE_BACKEND_PLATFORM_DEBUG:
        missing: list[str] = []
        helper = _resolve_executable_setting(
            os.getenv(WORKERBEE_WORKSPACE_HELPER_ENV, ""),
            "agent-pbx-workerbee-workspace",
            which,
        )
        stage_python = _resolve_executable_setting(
            os.getenv(DEBUG_STAGE_PYTHON_ENV, ""),
            "",
            which,
        )
        stage_script = Path(os.getenv(DEBUG_STAGE_SCRIPT_ENV, "")).expanduser()
        if not helper:
            missing.append(WORKERBEE_WORKSPACE_HELPER_ENV)
        if not stage_python:
            missing.append(DEBUG_STAGE_PYTHON_ENV)
        if not stage_script.is_file():
            missing.append(DEBUG_STAGE_SCRIPT_ENV)
        if missing:
            return DoctorCheck(
                "workerbee-runtime-binding",
                "warn",
                f"platform_debug generation {selected.generation} lacks workspace materializer prerequisites",
                detail="missing: " + ", ".join(missing),
                remediation="Configure the platform-debug staging helper, Python environment, and staging script in the private host environment.",
            )
        details.extend(
            (
                f"helper={helper}",
                f"stage_python={stage_python}",
                f"stage_script={stage_script}",
            )
        )
    return DoctorCheck(
        "workerbee-runtime-binding",
        "pass",
        f"{selected.backend} generation {selected.generation} ready",
        detail=" · ".join(details),
    )


def _workerbee_tool_surface_check(
    *,
    profile: str,
    backend: str,
    advertised_tools: tuple[str, ...] | list[str],
) -> DoctorCheck:
    """Render requested/effective/quarantined WorkerBee catalog posture."""

    surface = resolve_workerbee_tool_surface(
        profile,
        advertised_tools,
        backend=backend,
    )
    detail = (
        f"requested={len(surface.requested_tools)}; "
        f"effective={len(surface.effective_tools)}; "
        f"missing={list(surface.missing_tools)}; "
        f"quarantined={list(surface.quarantined_backend_tools)}"
    )
    if not surface.compatible:
        return DoctorCheck(
            "workerbee-tool-surface",
            "warn",
            "selected WorkerBee backend is missing required profile tools",
            detail=detail,
            remediation=(
                "Select a compatible access profile/backend or install a WorkerBee "
                "build exposing every required reviewed tool."
            ),
        )
    if not surface.catalog_fully_reviewed:
        return DoctorCheck(
            "workerbee-tool-surface",
            "warn",
            "unreviewed WorkerBee backend tools are quarantined",
            detail=detail,
            remediation=(
                "Keep the extensions quarantined or add them to a reviewed, "
                "backend-specific optional capability registry before use."
            ),
        )
    return DoctorCheck(
        "workerbee-tool-surface",
        "pass",
        "WorkerBee requested and effective tool surfaces are compatible",
        detail=detail,
    )


def _workerbee_tool_surface_runtime_check(
    config: MCPDaemonConfig,
    timeout: float,
) -> DoctorCheck:
    """Probe the selected backend catalog and expose effective/quarantine posture."""

    selected = WorkerBeeRuntimeRegistry(
        config.resolved_db_path.parent / "workerbee-runtime"
    ).selected()
    if selected is None:
        return DoctorCheck(
            "workerbee-tool-surface",
            "warn",
            "no selected WorkerBee runtime is available for tool-surface diagnostics",
            remediation="Select and record a WorkerBee backend, then rerun doctor.",
        )
    try:
        preflight = asyncio.run(
            inspect_workerbee_mcp(
                selected.endpoint,
                timeout_seconds=max(0.1, timeout),
            )
        )
    except Exception as exc:  # noqa: BLE001 - doctor normalizes diagnostics
        return DoctorCheck(
            "workerbee-tool-surface",
            "warn",
            f"WorkerBee tool catalog probe failed ({type(exc).__name__})",
            remediation="Verify the selected WorkerBee endpoint and rerun doctor.",
        )
    if not preflight.ready:
        return DoctorCheck(
            "workerbee-tool-surface",
            "warn",
            preflight.message,
            detail=f"backend={selected.backend}; endpoint={selected.endpoint}",
            remediation=preflight.remediation,
        )
    return _workerbee_tool_surface_check(
        profile=WORKERBEE_PUBLIC_DEFAULT_ACCESS_PROFILE,
        backend=selected.backend,
        advertised_tools=list(preflight.advertised_tools),
    )


def _workerbee_workspace_policy_check(config: MCPDaemonConfig) -> DoctorCheck:
    if not config.resolved_db_path.is_file():
        return DoctorCheck(
            "workerbee-workspace-policy",
            "warn",
            "workspace policy will use safe defaults after database initialization",
        )
    try:
        service = WorkerBeeWorkspacePolicyService(Store(config.resolved_db_path))
        view = service.get_policy()
        snapshot_view = WorkerBeeSnapshotPolicyService(
            Store(config.resolved_db_path)
        ).get_policy()
    except Exception as exc:  # noqa: BLE001 - doctor must report malformed state
        return DoctorCheck(
            "workerbee-workspace-policy",
            "warn",
            "WorkerBee workspace policy is unreadable",
            detail=f"{type(exc).__name__}: {exc}",
            remediation="Run the Agent PBX migration, then review the host workspace policy.",
        )
    effective = view["effective"]
    admin = view["admin"]
    policy = str(effective.get("policy") or "pinned")
    roots = effective.get("allowed_roots") or []
    valid = policy != "scoped-workbench" or bool(roots)
    label = str(effective.get("profile_label") or "Safe default")
    snapshot = snapshot_view["effective"]
    snapshot_admin = snapshot_view["admin"]
    return DoctorCheck(
        "workerbee-workspace-policy",
        "pass" if valid else "warn",
        f"{label}: {policy} with {effective.get('request_mode') or 'approval'} requests",
        detail=(
            f"control={admin.get('control_mode') or 'editable'} · "
            f"ceiling={admin.get('policy_ceiling') or 'scoped-workbench'} · "
            f"allowed_roots={len(roots)} · "
            f"managed_scratch={bool(effective.get('managed_scratch_root'))}"
            f" · snapshot={snapshot.get('policy') or 'tracked'}"
            f" · snapshot_control={snapshot_admin.get('control_mode') or 'editable'}"
        ),
        remediation=(
            "Configure at least one existing allowed root for scoped-workbench policy."
            if not valid
            else ""
        ),
    )


def _workerbee_controlled_run_check() -> DoctorCheck:
    try:
        posture = controlled_run_posture()
        profiles = load_controlled_run_profiles(posture.profile_root)
    except WorkerBeeControlledRunError as exc:
        return DoctorCheck(
            "workerbee-controlled-runs",
            "warn",
            exc.message,
            remediation=exc.remediation or "Correct the private controlled-run profile directory.",
        )
    enabled = [profile for profile in profiles.values() if profile.enabled]
    if not posture.enabled:
        return DoctorCheck(
            "workerbee-controlled-runs",
            "pass",
            "disabled by host policy",
            detail=f"control={posture.control_mode}; host_fallback={posture.host_fallback}",
        )
    if not enabled:
        return DoctorCheck(
            "workerbee-controlled-runs",
            "warn" if posture.root_operator_enabled else "pass",
            f"{posture.mode} mode with no enabled private profiles",
            detail=f"profile_root={posture.profile_root}; control={posture.control_mode}",
            remediation=(
                "Install a private, owner-writable-only controlled-run profile and verify the selected WorkerBee appliance contract."
                if posture.root_operator_enabled
                else ""
            ),
        )
    return DoctorCheck(
        "workerbee-controlled-runs",
        "pass",
        f"{posture.mode} mode with {len(enabled)} enabled profile(s)",
        detail=(
            f"profile_root={posture.profile_root}; control={posture.control_mode}; "
            f"host_fallback={posture.host_fallback}; max_in_flight={posture.max_in_flight_per_root}"
        ),
    )


def _resolve_executable_setting(
    configured: str,
    fallback: str,
    which: Callable[[str], str | None],
) -> str:
    candidate = str(configured or "").strip()
    if candidate:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        return str(which(candidate) or "")
    return str(which(fallback) or "") if fallback else ""


def _joplin_check(
    config: MCPDaemonConfig,
    timeout: float,
    *,
    probe_services: bool,
) -> DoctorCheck:
    if not config.joplin_api_url or not config.joplin_token:
        return DoctorCheck("joplin", "warn", "Joplin Data API is not configured")
    if not probe_services:
        return DoctorCheck("joplin", "pass", "Joplin Data API is configured; probe skipped")
    try:
        response = httpx.get(
            f"{config.joplin_api_url.rstrip('/')}/ping",
            params={"token": config.joplin_token},
            timeout=max(1.0, timeout),
        )
        ready = response.status_code == 200
    except Exception:
        ready = False
    return DoctorCheck(
        "joplin",
        "pass" if ready else "warn",
        "Joplin Data API reachable" if ready else "Joplin Data API probe failed",
        remediation="Start the configured Joplin profile and verify API URL/token." if not ready else "",
    )


def _clipboard_check(which: Callable[[str], str | None]) -> DoctorCheck:
    available = [name for name in OPTIONAL_CLIPBOARD_EXECUTABLES if which(name)]
    if os.getenv("TMUX") and which("tmux"):
        available.append("tmux buffer")
    return DoctorCheck(
        "clipboard",
        "pass" if available else "warn",
        ", ".join(available) if available else "no clipboard helper found",
        remediation=(
            "Install a platform clipboard helper or use transcript/tmux capture fallback."
            if not available
            else ""
        ),
    )


def doctor_json(report: DoctorReport) -> str:
    return json.dumps(report.public_dict(), indent=2, sort_keys=True)
