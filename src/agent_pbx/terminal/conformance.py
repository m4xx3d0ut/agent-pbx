from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

from .pty import PtyProcess
from .screen import VirtualTerminal


@dataclass(frozen=True)
class TmuxConformanceResult:
    client_alive: bool
    initial_frame_seen: bool
    pane_update_seen: bool
    pty_bytes: int
    rendered_lines: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.client_alive and self.initial_frame_seen and self.pane_update_seen


def run_dedicated_tmux_conformance(*, timeout: float = 5.0) -> TmuxConformanceResult:
    tmux = shutil.which("tmux")
    if not tmux:
        raise RuntimeError("tmux is unavailable")
    socket_name = f"agent-pbx-conformance-{uuid.uuid4().hex[:12]}"
    session = "runtime"
    marker_a = f"PBX_INITIAL_{uuid.uuid4().hex[:8]}"
    marker_b = f"PBX_UPDATE_{uuid.uuid4().hex[:8]}"
    config_fd, config_name = tempfile.mkstemp(prefix="agent-pbx-tmux-", suffix=".conf")
    os.close(config_fd)
    config = Path(config_name)
    process: PtyProcess | None = None
    try:
        subprocess.run(
            [tmux, "-L", socket_name, "-f", str(config), "new-session", "-d", "-s", session],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        subprocess.run(
            [tmux, "-L", socket_name, "send-keys", "-t", session, f"printf '{marker_a}\\n'", "Enter"],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        env=dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        process=PtyProcess(
            [tmux, "-L", socket_name, "-f", str(config), "attach-session", "-t", session],
            env=env,
            columns=100,
            rows=32,
        ).start()
        terminal=VirtualTerminal(100,32)
        deadline=time.monotonic()+timeout
        raw=b""
        while time.monotonic()<deadline and marker_a not in "\n".join(terminal.snapshot().lines):
            chunk=process.read_available(timeout=0.1)
            raw+=chunk; terminal.feed(chunk)
        initial=marker_a in "\n".join(terminal.snapshot().lines)
        subprocess.run(
            [tmux, "-L", socket_name, "send-keys", "-t", session, f"printf '{marker_b}\\n'", "Enter"],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        while time.monotonic()<deadline and marker_b not in "\n".join(terminal.snapshot().lines):
            chunk=process.read_available(timeout=0.1)
            raw+=chunk; terminal.feed(chunk)
        snapshot=terminal.snapshot()
        return TmuxConformanceResult(
            client_alive=process.alive,
            initial_frame_seen=initial,
            pane_update_seen=marker_b in "\n".join(snapshot.lines),
            pty_bytes=len(raw),
            rendered_lines=snapshot.lines,
        )
    finally:
        if process is not None:
            process.close()
        subprocess.run([tmux, "-L", socket_name, "kill-server"],capture_output=True,timeout=timeout)
        config.unlink(missing_ok=True)


def run_outer_tmux_conformance(*, timeout: float = 5.0) -> TmuxConformanceResult:
    """Exercise a disposable session on the server named by the local TMUX value."""

    tmux = shutil.which("tmux")
    tmux_env = os.getenv("TMUX", "").strip()
    if not tmux:
        raise RuntimeError("tmux is unavailable")
    if not tmux_env:
        raise RuntimeError("no outer TMUX server is detected")
    socket_path = tmux_env.split(",", 1)[0]
    if not socket_path:
        raise RuntimeError("outer TMUX socket path is empty")
    session = f"agent-pbx-conformance-{uuid.uuid4().hex[:12]}"
    marker_a = f"PBX_OUTER_INITIAL_{uuid.uuid4().hex[:8]}"
    marker_b = f"PBX_OUTER_UPDATE_{uuid.uuid4().hex[:8]}"
    process: PtyProcess | None = None
    prefix = [tmux, "-S", socket_path]
    try:
        subprocess.run(
            [*prefix, "new-session", "-d", "-s", session],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        subprocess.run(
            [*prefix, "send-keys", "-t", session, f"printf '{marker_a}\\n'", "Enter"],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env.setdefault("TERM", "xterm-256color")
        process = PtyProcess(
            [*prefix, "attach-session", "-t", session],
            env=env,
            columns=100,
            rows=32,
        ).start()
        terminal = VirtualTerminal(100, 32)
        raw = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and marker_a not in "\n".join(
            terminal.snapshot().lines
        ):
            chunk = process.read_available(timeout=0.1)
            raw += chunk
            terminal.feed(chunk)
        initial = marker_a in "\n".join(terminal.snapshot().lines)
        subprocess.run(
            [*prefix, "send-keys", "-t", session, f"printf '{marker_b}\\n'", "Enter"],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and marker_b not in "\n".join(
            terminal.snapshot().lines
        ):
            chunk = process.read_available(timeout=0.1)
            raw += chunk
            terminal.feed(chunk)
        snapshot = terminal.snapshot()
        return TmuxConformanceResult(
            client_alive=process.alive,
            initial_frame_seen=initial,
            pane_update_seen=marker_b in "\n".join(snapshot.lines),
            pty_bytes=len(raw),
            rendered_lines=snapshot.lines,
        )
    finally:
        if process is not None:
            process.close()
        subprocess.run(
            [*prefix, "kill-session", "-t", session],
            capture_output=True,
            timeout=timeout,
        )
