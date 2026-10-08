from __future__ import annotations

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
from .compat import compatibility_posture
from .mcp_daemon import MCPDaemonConfig, lan_auth_guard, mcp_daemon_status
from .store import SCHEMA_VERSION
from .tmux_binary import configured_tmux_binary, resolve_tmux_binary
from .ui.theme import terminal_color_depth


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
    checks.append(_terminal_check())
    checks.append(_database_check(config.resolved_db_path))
    checks.append(_daemon_check(config))
    checks.append(_security_check(config))
    checks.append(_compatibility_check(config))
    checks.append(_codex_check(codex_command, timeout))
    checks.append(_workerbee_check(config, which, timeout))
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
    outer = bool(os.getenv("TMUX"))
    return DoctorCheck(
        "tmux",
        "pass" if result.returncode == 0 else "fail",
        version or path,
        detail=(
            f"configured command: {configured}; resolved path: {path}; "
            f"outer tmux detected: {'yes' if outer else 'no'}"
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
