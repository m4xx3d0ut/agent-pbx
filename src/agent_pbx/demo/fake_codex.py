#!/usr/bin/env python3
"""Deterministic Codex-shaped terminal used by the Agent PBX media harness.

The process intentionally identifies itself as a demo runtime. It implements
the small CLI posture surface needed by managed launch and then runs as a real
interactive process in a real PBX-managed tmux pane. This lets the README demo
exercise PTY attachment, resize, input, Operator launch, and runtime mappings
without using personal Codex authentication or network access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import sys
import termios
import threading
import time
import tty
import urllib.error
import urllib.request


DEMO_VERSION = "0.159.3"
MODEL_CATALOG = {
    "models": [
        {
            "slug": "gpt-5.6-sol",
            "displayName": "Sol 5.6",
            "defaultReasoningLevel": "high",
            "supportedReasoningLevels": ["high", "xhigh", "max"],
            "serviceTiers": ["default"],
            "defaultServiceTier": "default",
            "visibility": "list",
            "shellType": "responses",
            "supportedInApi": True,
            "supportVerbosity": True,
            "defaultVerbosity": "high",
        },
        {
            "slug": "gpt-5.6-terra",
            "displayName": "Terra 5.6",
            "defaultReasoningLevel": "xhigh",
            "supportedReasoningLevels": ["xhigh", "max"],
            "serviceTiers": ["default"],
            "defaultServiceTier": "default",
            "visibility": "list",
            "shellType": "responses",
            "supportedInApi": True,
            "supportVerbosity": True,
            "defaultVerbosity": "high",
        },
    ]
}


def _command_mode(argv: list[str]) -> int | None:
    if "--version" in argv:
        print(f"codex-cli {DEMO_VERSION}")
        return 0
    if "--help" in argv or "-h" in argv:
        print(
            "Codex CLI demo fixture\n\n"
            "Commands:\n"
            "  resume      Resume a session\n"
            "  fork        Fork a session\n"
            "  mcp         Manage MCP servers\n"
            "  doctor      Show runtime posture\n"
            "  debug       Debug commands\n\n"
            "Options:\n"
            "  -m, --model MODEL\n"
            "  -c, --config KEY=VALUE\n"
        )
        return 0
    if len(argv) >= 2 and argv[:2] == ["debug", "models"]:
        print(json.dumps(MODEL_CATALOG))
        return 0
    if argv and argv[0] == "doctor":
        print(
            "Model  gpt-5.6-sol · high\n"
            f"App-server version  {DEMO_VERSION}\n"
            f"Latest version  {DEMO_VERSION}\n"
            "Install method  Agent PBX demo fixture\n"
            "Managed by  npm: no\n"
        )
        return 0
    if argv and argv[0] == "mcp":
        action = argv[1] if len(argv) > 1 else "list"
        if action == "list":
            print("agent-pbx  enabled  demo")
        else:
            print(f"Agent PBX demo MCP {action}: ok")
        return 0
    return None


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> object:
    server = os.getenv("AGENT_PBX_SERVER_URL", "").rstrip("/")
    if not server:
        raise RuntimeError("Agent PBX server URL is unavailable")
    headers = {"Content-Type": "application/json"}
    if token := os.getenv("AGENT_PBX_TOKEN", ""):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{server}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode())


def _register_when_ready() -> None:
    agent_id = os.getenv("AGENT_PBX_AGENT_ID", "").strip()
    if not agent_id or not os.getenv("AGENT_PBX_SERVER_URL", "").strip():
        return
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            agents = _request("GET", "/v1/agents?include_hidden=true")
            if isinstance(agents, list) and any(
                isinstance(item, dict) and item.get("agent_id") == agent_id
                for item in agents
            ):
                break
        except (OSError, RuntimeError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    else:
        return

    cwd = os.getenv("AGENT_PBX_CWD", "").strip() or str(Path.cwd())
    agent_type = os.getenv("AGENT_PBX_AGENT_TYPE", "caller").strip() or "caller"
    project = os.getenv("AGENT_PBX_AGENT_PROJECT", "").strip()
    if not project:
        project = "agent-pbx-operator" if agent_type == "operator" else Path(cwd).name
    role = os.getenv("AGENT_PBX_OPERATOR_ROLE", "").strip()
    session_id = f"demo-session-{agent_id}"
    metadata: dict[str, object] = {
        "cwd": cwd,
        "pbx_mode": "report",
        "demo": True,
        "reporting_agent_id": agent_id,
        "codex_session_id": session_id,
        "codex_thread_id": session_id,
    }
    if agent_type == "operator":
        metadata.update(
            {
                "agent_type": "operator",
                "operator_role": role or "root",
                "logical_operator_id": os.getenv(
                    "AGENT_PBX_LOGICAL_OPERATOR_ID", agent_id
                ),
            }
        )
    try:
        _request(
            "POST",
            "/v1/agents/register",
            {
                "agent_id": agent_id,
                "project": project,
                "name": agent_id,
                "agent_type": agent_type,
                "metadata": metadata,
            },
        )
        _request(
            "POST",
            f"/v1/agents/{agent_id}/reports",
            {
                "project": project,
                "status": "working",
                "summary": (
                    "Coordinating the v2 demo workflow."
                    if agent_type == "operator"
                    else "Managed native runtime is ready."
                ),
                "detail": (
                    "This isolated demo session exercises the Agent PBX v2 "
                    "runtime mapping, native tmux terminal, and durable identity path."
                ),
                "needs_input": False,
                "plan_options": [],
                "reporting_agent_id": agent_id,
                "metadata": {
                    "demo": True,
                    "reporting_agent_id": agent_id,
                    "suppress_tui_alerts": True,
                },
            },
        )
    except (OSError, RuntimeError, urllib.error.URLError):
        return


class DemoTerminal:
    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.agent_id = os.getenv("AGENT_PBX_AGENT_ID", "agent-pbx-demo")
        self.agent_type = os.getenv("AGENT_PBX_AGENT_TYPE", "caller")
        self.operator_role = os.getenv("AGENT_PBX_OPERATOR_ROLE", "")
        self.model = os.getenv("AGENT_PBX_CODEX_MODEL", "gpt-5.6-sol")
        self.effort = os.getenv(
            "AGENT_PBX_CODEX_REASONING_EFFORT",
            "xhigh" if self.agent_type == "operator" else "high",
        )
        self.input = ""
        self.last_prompt = "Review the v2 runtime and report readiness."
        self.running = True
        self.frame = 0
        self.old_termios: list[object] | None = None

    def _role_label(self) -> str:
        if self.agent_type != "operator":
            return "Agent"
        return "Operator fork" if self.operator_role == "fork" else "Root Operator"

    def _size(self) -> tuple[int, int]:
        size = os.get_terminal_size(sys.stdout.fileno())
        return max(50, size.columns), max(16, size.lines)

    @staticmethod
    def _fit(text: str, width: int) -> str:
        return text if len(text) <= width else text[: max(0, width - 1)] + "…"

    def draw(self) -> None:
        cols, rows = self._size()
        width = max(36, cols - 4)
        spinner = "◐◓◑◒"[self.frame % 4]
        self.frame += 1
        lines = [
            f"╭─ Agent PBX v2 · DEMO CODEX RUNTIME {'─' * max(1, width - 38)}╮",
            f"│ {self._fit(self.agent_id, width - 2):<{width - 2}} │",
            f"│ {self._fit(f'{self._role_label()} · {self.model}/{self.effort} · report mode', width - 2):<{width - 2}} │",
            f"╰{'─' * width}╯",
            "",
            "• Connected to the Agent PBX control plane",
            "• Runtime identity bound to a PBX-managed tmux pane",
            "• Project, model profile, and session evidence verified",
            "",
            "✓ Native terminal input, resize, and scrollback are active",
            "✓ Closing the TUI leaves this runtime running",
            "",
            f"{spinner} Ready for engineering operator input",
            "",
            f"  You  {self._fit(self.last_prompt, width - 7)}",
            "",
            f"  › {self.input}",
        ]
        if self.input.startswith("/"):
            lines.extend(
                [
                    "",
                    "    /status    Show runtime posture",
                    "    /model     Select managed model profile",
                    "    /review    Start a bounded review",
                ]
            )
        footer = f"{self.model} · {self.effort} · high output · demo fixture"
        while len(lines) < rows - 1:
            lines.append("")
        lines = lines[: max(1, rows - 1)]
        lines.append(self._fit(footer, cols - 1))
        sys.stdout.write("\x1b]0;Agent PBX demo runtime\x07\x1b[H\x1b[J")
        sys.stdout.write("\r\n".join(self._fit(line, cols - 1) for line in lines))
        sys.stdout.flush()

    def _read_key(self) -> bytes:
        readable, _, _ = select.select([sys.stdin], [], [], 0.25)
        return os.read(sys.stdin.fileno(), 32) if readable else b""

    def run(self) -> int:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Agent PBX demo runtime requires a TTY", file=sys.stderr)
            return 2
        self.old_termios = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        signal.signal(signal.SIGWINCH, lambda *_: self.draw())
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        try:
            self.draw()
            while self.running:
                data = self._read_key()
                if not data:
                    self.draw()
                    continue
                for value in data:
                    if value == 3:
                        self.input = ""
                    elif value in {10, 13}:
                        if self.input.strip():
                            self.last_prompt = self.input.strip()
                        self.input = ""
                    elif value == 27:
                        self.input = ""
                    elif value in {8, 127}:
                        self.input = self.input[:-1]
                    elif 32 <= value < 127:
                        self.input += chr(value)
                self.draw()
        finally:
            if self.old_termios is not None:
                termios.tcsetattr(
                    sys.stdin.fileno(), termios.TCSADRAIN, self.old_termios
                )
            sys.stdout.write("\x1b[0m\r\n")
            sys.stdout.flush()
        return 0


def main() -> int:
    argv = sys.argv[1:]
    if (result := _command_mode(argv)) is not None:
        return result
    threading.Thread(target=_register_when_ready, daemon=True).start()
    return DemoTerminal(argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
