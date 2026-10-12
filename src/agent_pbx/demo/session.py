from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

from agent_pbx.tui import run_tui

from .contract import DEMO_SCENARIO_ID
from .fixtures import DemoFixturePaths, prepare_demo_fixtures
from .policy import (
    build_public_demo_environment,
    ensure_private_directory,
    sensitive_environment_names,
)


class DemoSessionError(RuntimeError):
    """Raised when an isolated demo session cannot reach a safe ready state."""


@dataclass(frozen=True)
class DemoSessionConfig:
    root: Path | None = None
    keep: bool = False
    scenario: str = DEMO_SCENARIO_ID
    ttl_seconds: int = 900
    tmux_bin: str = "tmux"
    theme: str = "cyberpunk"
    layout: str = "adaptive"


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _terminate_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=3)


class DemoSession:
    """One visitor's private Agent PBX daemon, fixtures, tmux, and TUI state."""

    def __init__(self, config: DemoSessionConfig | None = None) -> None:
        self.config = config or DemoSessionConfig()
        if self.config.scenario != DEMO_SCENARIO_ID:
            raise DemoSessionError(f"unsupported public demo scenario: {self.config.scenario}")
        if self.config.ttl_seconds < 30 or self.config.ttl_seconds > 3600:
            raise DemoSessionError("demo session TTL must be between 30 and 3600 seconds")
        self.session_id = uuid.uuid4().hex
        self.root = Path(
            self.config.root
            or tempfile.mkdtemp(prefix=f"agent-pbx-demo-{self.session_id[:8]}-")
        ).expanduser().resolve(strict=False)
        if self.config.root is not None and self.root.exists() and any(self.root.iterdir()):
            raise DemoSessionError(f"explicit demo root is not empty: {self.root}")
        self._owns_root = True
        self.home = self.root / "home"
        self.runtime_dir = self.root / "runtime"
        self.state_root = self.root / "state"
        self.db_path = self.state_root / "agent-pbx.sqlite"
        self.logs_dir = self.root / "logs"
        self.api_port = reserve_loopback_port()
        self.joplin_port = reserve_loopback_port()
        self.server_url = f"http://127.0.0.1:{self.api_port}"
        self.joplin_url = f"http://127.0.0.1:{self.joplin_port}"
        self.token = secrets.token_urlsafe(32)
        self.fixtures: DemoFixturePaths | None = None
        self.environment: dict[str, str] = {}
        self.daemon_process: subprocess.Popen[bytes] | None = None
        self.joplin_process: subprocess.Popen[bytes] | None = None
        self._log_handles: list[Any] = []
        self._started = False
        self._stop_event = threading.Event()

    @property
    def runtime_socket(self) -> Path:
        return self.runtime_dir / "agent-pbx" / "runtime-tmux.sock"

    def prepare(self) -> None:
        if len(os.fsencode(self.runtime_socket)) >= 104:
            raise DemoSessionError(
                "demo runtime socket path is too long; use a shorter --root path"
            )
        ensure_private_directory(self.root)
        for path in (
            self.home,
            self.runtime_dir,
            self.state_root,
            self.logs_dir,
        ):
            ensure_private_directory(path)
        self.fixtures = prepare_demo_fixtures(self.root / "fixtures")
        self.environment = build_public_demo_environment(
            os.environ,
            home=self.home,
            runtime_dir=self.runtime_dir,
            path_prefix=self.fixtures.bin_dir,
        )
        self.environment.update(
            {
                "AGENT_PBX_PROJECT_ROOTS": str(self.fixtures.project_root),
                "AGENT_PBX_TMUX_BIN": self.config.tmux_bin,
                "AGENT_PBX_WORKERBEE_BIN": str(self.fixtures.workerbee_bin),
                "AGENT_PBX_GH_BIN": str(self.fixtures.github_bin),
                "AGENT_PBX_PR_ENABLED": "1",
                "AGENT_PBX_ISSUES_ENABLED": "1",
                "AGENT_PBX_PR_ALLOWED_REPOS": "agent-pbx/demo",
                "AGENT_PBX_JOPLIN_API_URL": self.joplin_url,
                "AGENT_PBX_JOPLIN_TOKEN": "public-demo-joplin-token",
                "AGENT_PBX_JOPLIN_NOTEBOOK": "Agent PBX",
                "AGENT_PBX_DAEMON_ACCESS_LOG": "0",
            }
        )

    def _open_log(self, name: str) -> Any:
        handle = (self.logs_dir / name).open("ab", buffering=0)
        self._log_handles.append(handle)
        return handle

    def _spawn(self, argv: list[str], *, log_name: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=self._open_log(log_name),
            stderr=subprocess.STDOUT,
            env=self.environment,
            cwd=self.root,
            start_new_session=True,
        )

    def start(self) -> None:
        if self._started:
            return
        self.prepare()
        assert self.fixtures is not None
        self.joplin_process = self._spawn(
            [
                sys.executable,
                "-m",
                "agent_pbx.demo.fake_joplin",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.joplin_port),
            ],
            log_name="joplin.log",
        )
        self._wait_json(f"{self.joplin_url}/folders", timeout=10)
        self.daemon_process = self._spawn(
            [
                sys.executable,
                "-m",
                "agent_pbx",
                "mcp",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
                "--state-root",
                str(self.state_root),
                "--db",
                str(self.db_path),
                "--token",
                self.token,
                "--log-level",
                "warning",
            ],
            log_name="agent-pbx.log",
        )
        self._wait_json(f"{self.server_url}/healthz", timeout=20)
        self.seed()
        self._started = True

    def _wait_json(self, url: str, *, timeout: float) -> object:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (OSError, json.JSONDecodeError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(0.1)
        raise DemoSessionError(f"demo service did not become ready at {url}: {last_error}")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        timeout: float = 10,
    ) -> object:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(
            f"{self.server_url}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise DemoSessionError(
                f"demo request {method} {path} failed with {exc.code}: {body}"
            ) from exc

    def _report(
        self,
        agent_id: str,
        project: str,
        *,
        status: str,
        summary: str,
        detail: str,
        needs_input: bool = False,
        plan_options: list[dict[str, str]] | None = None,
    ) -> None:
        self.request(
            "POST",
            f"/v1/agents/{agent_id}/reports",
            {
                "project": project,
                "status": status,
                "summary": summary,
                "detail": detail,
                "needs_input": needs_input,
                "plan_options": plan_options or [],
                "reporting_agent_id": agent_id,
                "metadata": {
                    "demo": True,
                    "reporting_agent_id": agent_id,
                    "suppress_tui_alerts": not needs_input,
                },
            },
        )

    def seed(self) -> None:
        assert self.fixtures is not None
        self.request(
            "POST",
            "/v2/agents/managed-launch",
            {
                "project_path": str(self.fixtures.platform_project),
                "agent_id": "platform-agent",
                "profile_id": "sol-high",
                "runtime_server_mode": "dedicated",
            },
            timeout=20,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            agents = self.request("GET", "/v1/agents?include_hidden=true")
            platform = next(
                (
                    item
                    for item in agents
                    if isinstance(item, dict) and item.get("agent_id") == "platform-agent"
                ),
                {},
            )
            metadata = platform.get("metadata") if isinstance(platform, dict) else {}
            if isinstance(metadata, dict) and metadata.get("codex_session_id"):
                break
            time.sleep(0.1)
        else:
            raise DemoSessionError("managed demo Codex runtime did not register")

        self.request(
            "POST",
            "/v1/agents/register",
            {
                "agent_id": "release-review",
                "project": "release-evidence",
                "name": "Release Review",
                "metadata": {
                    "cwd": str(self.fixtures.release_project),
                    "pbx_mode": "report",
                    "demo": True,
                    "codex_session_id": "demo-session-release-review",
                },
            },
        )
        self.request(
            "POST",
            "/v1/agents/register",
            {
                "agent_id": "operator-0",
                "project": "agent-pbx-operator",
                "name": "Architecture Operator",
                "agent_type": "operator",
                "metadata": {
                    "cwd": str(self.fixtures.platform_project),
                    "pbx_mode": "report",
                    "demo": True,
                    "agent_type": "operator",
                    "operator_role": "root",
                    "logical_operator_id": "operator-0",
                    "codex_session_id": "demo-session-operator-0",
                },
            },
        )
        self._report(
            "platform-agent",
            "platform-console",
            status="working",
            summary="Native runtime is ready for the interactive product tour.",
            detail=(
                "The managed Agent is attached through a normal tmux client rendered "
                "inside Latest. Tmux owns the durable pane and scrollback."
            ),
        )
        self._report(
            "release-review",
            "release-evidence",
            status="plan",
            summary="Review the public demo and release evidence.",
            detail=(
                "This bounded review demonstrates durable report state and explicit "
                "operator choices beside the native runtime."
            ),
            needs_input=True,
            plan_options=[
                {
                    "id": "accept",
                    "label": "Accept evidence",
                    "description": "Record the release review as complete.",
                },
                {
                    "id": "follow-up",
                    "label": "Request follow-up",
                    "description": "Route one bounded correction to the Agent.",
                },
            ],
        )
        self._report(
            "operator-0",
            "agent-pbx-operator",
            status="working",
            summary="Coordinating runtime validation and release review.",
            detail=(
                "Operator 0 owns durable campaign state while bounded forks organize "
                "the project-specific work."
            ),
        )
        for source, fork_id, summary in (
            (
                "platform-agent",
                "operator-0-fork-platform-agent-demo",
                "Native runtime validation fork is ready.",
            ),
            (
                "release-review",
                "operator-0-fork-release-review-demo",
                "Release review fork is ready.",
            ),
        ):
            self.request(
                "POST",
                "/v1/operator/forks/ensure",
                {
                    "operator_agent_id": "operator-0",
                    "source_caller_agent_id": source,
                    "fork_agent_id": fork_id,
                    "fork_track_id": "default",
                    "fork_purpose": "edit",
                    "access_mode": "edit",
                    "status": "running",
                    "summary": summary,
                    "metadata": {"demo": True},
                },
            )
        self.request(
            "POST",
            "/v1/operator/campaigns",
            {
                "operator_agent_id": "operator-0",
                "title": "Interactive Agent PBX product tour",
                "objective": (
                    "Coordinate native runtime validation, documentation review, and "
                    "release evidence through durable Agent PBX identities."
                ),
                "criteria": [
                    "Managed Agent remains attached through the native terminal.",
                    "Operator and fork topology remain explicit.",
                    "Runtime and release evidence are independently reviewed.",
                ],
                "assignments": [
                    {
                        "target_agent_id": "platform-agent",
                        "title": "Validate native runtime",
                        "prompt": "Verify terminal attachment, resize, and persistence.",
                        "criteria": ["Native tmux runtime remains ready."],
                    },
                    {
                        "target_agent_id": "release-review",
                        "title": "Review release evidence",
                        "prompt": "Review architecture and acceptance evidence.",
                        "criteria": ["Control-plane terminology matches behavior."],
                    },
                ],
                "delivery": "queue",
            },
        )
        self._report(
            "platform-agent",
            "platform-console",
            status="working",
            summary="Selected native runtime is ready for engineering operator input.",
            detail="Use F3 for right-side tabs, F5 for Operators, and F10 to launch a workspace.",
        )
        subprocess.run(
            [
                self.config.tmux_bin,
                "-S",
                str(self.runtime_socket),
                "set-option",
                "-g",
                "status",
                "off",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

    def tui_environment(self) -> dict[str, str]:
        assert self.fixtures is not None
        result = dict(self.environment)
        result.update(
            {
                "AGENT_PBX_SERVER_URL": self.server_url,
                "AGENT_PBX_TOKEN": self.token,
                "AGENT_PBX_TUI_SETTINGS_FILE": str(self.root / "tui-settings.json"),
                "AGENT_PBX_TUI_THEME": self.config.theme,
                "AGENT_PBX_TUI_LAYOUT": self.config.layout,
                "AGENT_PBX_TUI_FLASH": "1",
                "AGENT_PBX_TUI_AGENT_BLINK": "1",
                "AGENT_PBX_TUI_EVENT_STREAM_V2": "1",
                "AGENT_PBX_TUI_TMUX": "1",
                "AGENT_PBX_TUI_EMBEDDED_TERMINAL_V2": "1",
                "AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE": "dedicated",
                "AGENT_PBX_TUI_COMPAT_TERMINAL_CAPTURE": "0",
                "AGENT_PBX_TUI_CODEX_BIN": str(self.fixtures.codex_bin),
            }
        )
        result.pop("NO_COLOR", None)
        return result

    def public_status(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "scenario": self.config.scenario,
            "root": str(self.root),
            "server_url": self.server_url,
            "runtime_socket": str(self.runtime_socket),
            "started": self._started,
            "ttl_seconds": self.config.ttl_seconds,
            "sensitive_parent_variables_removed": list(
                sensitive_environment_names(os.environ)
            ),
        }

    def run_tui(self) -> None:
        self.start()
        environment = self.tui_environment()
        original = dict(os.environ)
        expired = threading.Event()

        def expire() -> None:
            if not self._stop_event.wait(self.config.ttl_seconds):
                expired.set()
                os.kill(os.getpid(), signal.SIGTERM)

        def stop_from_signal(_signum: int, _frame: object) -> None:
            raise SystemExit(0)

        previous_term = signal.signal(signal.SIGTERM, stop_from_signal)
        watchdog = threading.Thread(target=expire, daemon=True, name="demo-session-ttl")
        watchdog.start()
        os.environ.clear()
        os.environ.update(environment)
        try:
            run_tui(server=self.server_url, token=self.token)
        finally:
            self._stop_event.set()
            signal.signal(signal.SIGTERM, previous_term)
            os.environ.clear()
            os.environ.update(original)
            if expired.is_set():
                print("Agent PBX public demo session reached its time limit.", file=sys.stderr)

    def close(self) -> None:
        self._stop_event.set()
        if self.runtime_socket.exists():
            with suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    [self.config.tmux_bin, "-S", str(self.runtime_socket), "kill-server"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
        _terminate_process(self.daemon_process)
        _terminate_process(self.joplin_process)
        self.daemon_process = None
        self.joplin_process = None
        for handle in self._log_handles:
            with suppress(OSError):
                handle.close()
        self._log_handles.clear()
        self._started = False
        if not self.config.keep and self._owns_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "DemoSession":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def run_demo_tui_session(config: DemoSessionConfig | None = None) -> int:
    session = DemoSession(config)
    try:
        session.run_tui()
        return 0
    finally:
        session.close()
