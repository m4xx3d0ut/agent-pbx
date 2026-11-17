from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx
from rich.text import Text

from ..contracts import CodexRuntimeState
from ..runtime_tmux import (
    process_start_ticks,
    runtime_server_id,
    tmux_socket_from_environment,
)
from .theme import PBX_PALETTE, runtime_color


STATE_LABELS: dict[CodexRuntimeState, str] = {
    CodexRuntimeState.STARTING: "STARTING",
    CodexRuntimeState.READY: "READY",
    CodexRuntimeState.THINKING: "THINKING",
    CodexRuntimeState.DELEGATING: "DELEGATING",
    CodexRuntimeState.EXECUTING: "EXECUTING",
    CodexRuntimeState.WAITING_TOOL: "TOOL",
    CodexRuntimeState.WAITING_USER: "INPUT",
    CodexRuntimeState.COMPACTING: "COMPACTING",
    CodexRuntimeState.REVIEWING: "REVIEWING",
    CodexRuntimeState.COMPLETE: "COMPLETE",
    CodexRuntimeState.INTERRUPTED: "INTERRUPTED",
    CodexRuntimeState.ERROR: "ERROR",
    CodexRuntimeState.UNKNOWN: "UNKNOWN",
}

STATE_FRAMES: dict[CodexRuntimeState, tuple[str, ...]] = {
    CodexRuntimeState.STARTING: ("◐", "◓", "◑", "◒"),
    CodexRuntimeState.READY: ("◆",),
    CodexRuntimeState.THINKING: ("◐", "◓", "◑", "◒"),
    CodexRuntimeState.DELEGATING: ("◇⇢◆", "◇─◆", "◆⇠◇", "◆─◇"),
    CodexRuntimeState.EXECUTING: ("▸", "▹"),
    CodexRuntimeState.WAITING_TOOL: ("▸", "▹"),
    CodexRuntimeState.WAITING_USER: ("◈",),
    CodexRuntimeState.COMPACTING: ("◒", "◐"),
    CodexRuntimeState.REVIEWING: ("◎", "◉"),
    CodexRuntimeState.COMPLETE: ("✓",),
    CodexRuntimeState.INTERRUPTED: ("■",),
    CodexRuntimeState.ERROR: ("✕",),
    CodexRuntimeState.UNKNOWN: ("?",),
}


@dataclass(frozen=True)
class RuntimeHeader:
    text: Text
    state: CodexRuntimeState
    animated: bool


@dataclass(frozen=True)
class TuiHostTerminalIdentity:
    """Ephemeral identity of the interactive terminal that launched one TUI."""

    terminal_fd: int
    terminal_path: str
    terminal_device: int
    terminal_inode: int
    terminal_rdevice: int
    parent_pid: int
    parent_start_ticks: int | None


@dataclass(frozen=True)
class TuiHostLiveness:
    live: bool
    reason_code: str


TUI_HOST_WATCHDOG_SECONDS = 1.0
TUI_HOST_TERMINATION_GRACE_SECONDS = 3.0
SUBAGENT_ACTIVE_FRESHNESS_SECONDS = 30.0
SUBAGENT_ACTIVE_STATES = frozenset(
    {
        CodexRuntimeState.STARTING,
        CodexRuntimeState.THINKING,
        CodexRuntimeState.DELEGATING,
        CodexRuntimeState.EXECUTING,
        CodexRuntimeState.WAITING_TOOL,
        CodexRuntimeState.COMPACTING,
        CodexRuntimeState.REVIEWING,
    }
)


def capture_interactive_tui_host_identity(
    *,
    stdin: Any = None,
    stdout: Any = None,
    getppid: Callable[[], int] = os.getppid,
    fstat: Callable[[int], os.stat_result] = os.fstat,
    ttyname: Callable[[int], str] = os.ttyname,
    tcgetpgrp: Callable[[int], int] = os.tcgetpgrp,
    process_start_reader: Callable[[int | None], int | None] = process_start_ticks,
) -> TuiHostTerminalIdentity | None:
    """Capture host identity only for a real interactive TUI launch."""

    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    try:
        if not input_stream.isatty() or not output_stream.isatty():
            return None
        terminal_fd = int(input_stream.fileno())
        terminal_stat = fstat(terminal_fd)
        terminal_path = os.path.realpath(ttyname(terminal_fd))
        parent_pid = int(getppid())
        if (
            terminal_fd < 0
            or not stat.S_ISCHR(terminal_stat.st_mode)
            or int(tcgetpgrp(terminal_fd)) <= 0
            or not terminal_path
            or parent_pid <= 1
        ):
            return None
        parent_ticks = process_start_reader(parent_pid)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return TuiHostTerminalIdentity(
        terminal_fd=terminal_fd,
        terminal_path=terminal_path,
        terminal_device=int(terminal_stat.st_dev),
        terminal_inode=int(terminal_stat.st_ino),
        terminal_rdevice=int(terminal_stat.st_rdev),
        parent_pid=parent_pid,
        parent_start_ticks=(int(parent_ticks) if parent_ticks is not None else None),
    )


def assess_tui_host_terminal_identity(
    identity: TuiHostTerminalIdentity,
    *,
    getppid: Callable[[], int] = os.getppid,
    fstat: Callable[[int], os.stat_result] = os.fstat,
    ttyname: Callable[[int], str] = os.ttyname,
    tcgetpgrp: Callable[[int], int] = os.tcgetpgrp,
    process_start_reader: Callable[[int | None], int | None] = process_start_ticks,
) -> TuiHostLiveness:
    """Prove that the exact launch parent and host terminal remain live."""

    try:
        if int(getppid()) != identity.parent_pid:
            return TuiHostLiveness(False, "parent_changed")
        observed_ticks = process_start_reader(identity.parent_pid)
        if (
            identity.parent_start_ticks is not None
            and observed_ticks != identity.parent_start_ticks
        ):
            return TuiHostLiveness(False, "parent_identity_changed")
        terminal_stat = fstat(identity.terminal_fd)
        terminal_path = os.path.realpath(ttyname(identity.terminal_fd))
        terminal_group = int(tcgetpgrp(identity.terminal_fd))
    except (OSError, TypeError, ValueError):
        return TuiHostLiveness(False, "host_terminal_unavailable")
    if (
        not stat.S_ISCHR(terminal_stat.st_mode)
        or int(terminal_stat.st_dev) != identity.terminal_device
        or int(terminal_stat.st_ino) != identity.terminal_inode
        or int(terminal_stat.st_rdev) != identity.terminal_rdevice
        or terminal_path != identity.terminal_path
        or terminal_group <= 0
    ):
        return TuiHostLiveness(False, "host_terminal_changed")
    return TuiHostLiveness(True, "live")


def runtime_process_identity_is_absent(
    pane_pid: int,
    expected_start_ticks: int,
    *,
    process_start_reader: Callable[[int | None], int | None] = process_start_ticks,
) -> bool:
    """Prove that an exact registered process identity no longer exists."""

    observed_ticks = process_start_reader(pane_pid)
    if observed_ticks is not None:
        return int(observed_ticks) != int(expected_start_ticks)
    try:
        os.stat(f"/proc/{pane_pid}")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def runtime_mapping_is_on_distinct_server(
    mapping: Mapping[str, Any],
    *,
    tmux_environment: str | None = None,
) -> bool:
    """Compare one mapping with the server owning unqualified pane rows."""

    current_socket = tmux_socket_from_environment(
        os.getenv("TMUX") if tmux_environment is None else tmux_environment
    )
    mapped_raw = str(mapping.get("socket_path") or "").strip()
    mapped_server_id = str(mapping.get("server_id") or "").strip()
    if current_socket is None or not mapped_raw or not mapped_server_id:
        return False
    mapped_socket = Path(mapped_raw).expanduser()
    if not mapped_socket.is_absolute() or runtime_server_id(mapped_socket) != mapped_server_id:
        return False
    return (
        runtime_server_id(current_socket) != mapped_server_id
        and current_socket.resolve(strict=False) != mapped_socket.resolve(strict=False)
    )


def in_place_restart_registration_payload(
    mapping: Mapping[str, Any],
    *,
    server_mode: str,
    socket_path: str,
    session_name: str,
    window_id: str | None,
    window_name: str | None,
    pane_id: str,
    pane_pid: int,
    process_start_ticks: int | None,
    codex_session_id: str | None,
    cwd: str,
    tmux_bin: str,
) -> dict[str, Any]:
    """Build a restart registration without stale transient validation."""

    metadata = dict(mapping.get("metadata") or {})
    metadata.pop("last_live_validation", None)
    metadata.pop("live_validated_at", None)
    metadata.update(mapping_source="in_place_restart", tmux_bin=tmux_bin)
    return {
        "server_mode": server_mode,
        "socket_path": socket_path,
        "session_name": session_name,
        "window_id": window_id,
        "window_name": window_name,
        "pane_id": pane_id,
        "pane_pid": pane_pid,
        "process_start_ticks": process_start_ticks,
        "codex_session_id": codex_session_id,
        "cwd": cwd,
        "origin_client_tty": mapping.get("origin_client_tty"),
        "origin_session_name": mapping.get("origin_session_name"),
        "metadata": metadata,
    }


def writer_lease_unavailable_diagnostic(response: Any) -> str:
    try:
        detail = str(response.json().get("detail") or "lease conflict")
    except (AttributeError, TypeError, ValueError):
        detail = "lease conflict"
    return (
        f"writer lease unavailable: {detail}; this TUI remains read-only "
        "and the managed runtime was not restarted"
    )


def orphan_tui_host_diagnostics(reason_code: str) -> tuple[str, str]:
    return (
        f"host terminal liveness lost ({reason_code}); writer reacquisition is "
        "disabled and the managed runtime was not restarted",
        "The interactive host terminal is no longer live; this orphan TUI released "
        "its writer lease and is exiting without changing the runtime.",
    )


def release_orphan_tui_writer_lease(
    app: Any,
    *,
    post: Callable[..., Any] = httpx.post,
) -> bool:
    """Release only the exact writer lease owned by an orphan TUI."""

    agent_id = str(getattr(app, "embedded_terminal_agent_id", "") or "").strip()
    client_id = str(getattr(app, "tmux_terminal_client_id", "") or "").strip()
    server = str(getattr(app, "server", "") or "").rstrip("/")
    if not agent_id or not client_id or not server:
        return False
    token = str(getattr(app, "token", "") or "")
    try:
        response = post(
            f"{server}/v2/tmux/runtimes/{quote(agent_id, safe='')}/writer/release",
            json={"client_id": client_id, "ttl_seconds": 30.0},
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=2.0,
        )
        return int(getattr(response, "status_code", 500)) in {200, 404, 409}
    except Exception:
        return False


def watch_tui_host_liveness(
    app: Any,
    identity: TuiHostTerminalIdentity,
    stop_event: threading.Event,
    *,
    assess: Callable[[TuiHostTerminalIdentity], TuiHostLiveness] = (
        assess_tui_host_terminal_identity
    ),
    post: Callable[..., Any] = httpx.post,
    terminate: Callable[[], None] = lambda: os.kill(os.getpid(), signal.SIGTERM),
    hard_exit: Callable[[int], None] = os._exit,
    check_seconds: float = TUI_HOST_WATCHDOG_SECONDS,
    termination_grace_seconds: float = TUI_HOST_TERMINATION_GRACE_SECONDS,
) -> None:
    """Fail closed when Textual cannot observe its vanished host terminal."""

    while not stop_event.wait(max(0.0, check_seconds)):
        liveness = assess(identity)
        if liveness.live:
            continue
        app.host_liveness_lost = True
        release_orphan_tui_writer_lease(app, post=post)
        terminate()
        if not stop_event.wait(max(0.0, termination_grace_seconds)):
            hard_exit(0)
        return


def finalize_tui_host_watchdog(
    app: Any,
    identity: TuiHostTerminalIdentity,
    *,
    assess: Callable[[TuiHostTerminalIdentity], TuiHostLiveness] = (
        assess_tui_host_terminal_identity
    ),
    post: Callable[..., Any] = httpx.post,
    hard_exit: Callable[[int], None] = os._exit,
) -> bool:
    """Prevent shutdown threads from preserving a TUI after host loss."""

    if assess(identity).live:
        return False
    app.host_liveness_lost = True
    release_orphan_tui_writer_lease(app, post=post)
    hard_exit(0)
    return True


def run_tui_with_host_watchdog(app: Any) -> None:
    """Run one TUI with a host watchdog independent of its event loop."""

    identity = getattr(app, "host_terminal_identity", None)
    if not isinstance(identity, TuiHostTerminalIdentity):
        app.run()
        return
    stop_event = threading.Event()
    watchdog = threading.Thread(
        target=watch_tui_host_liveness,
        args=(app, identity, stop_event),
        daemon=True,
        name="agent-pbx-tui-host-watchdog",
    )
    watchdog.start()
    try:
        app.run()
    finally:
        stop_event.set()
        watchdog.join(timeout=0.1)
        finalize_tui_host_watchdog(app, identity)


def runtime_state(snapshot: Mapping[str, Any] | None) -> CodexRuntimeState:
    raw = str((snapshot or {}).get("state") or "unknown")
    try:
        return CodexRuntimeState(raw)
    except ValueError:
        return CodexRuntimeState.UNKNOWN


def runtime_header(
    snapshot: Mapping[str, Any] | None,
    workerbee: Mapping[str, Any] | None = None,
    *,
    frame: int = 0,
    color_depth: int = 24,
) -> RuntimeHeader:
    snapshot = snapshot or {}
    state = runtime_state(snapshot)
    frames = STATE_FRAMES[state]
    symbol = frames[frame % len(frames)]
    text = Text()
    text.append(f"{symbol} {STATE_LABELS[state]}", style=f"bold {runtime_color(state, color_depth)}")
    parts: list[str] = []
    profile = snapshot.get("profile")
    if isinstance(profile, Mapping):
        model = short_model(str(profile.get("model") or ""))
        effort = str(profile.get("reasoning_effort") or "").upper()
        if model:
            parts.append(f"{model}{'/' + effort if effort else ''}")
    context = snapshot.get("context")
    if isinstance(context, Mapping):
        remaining = context.get("remaining_percent")
        if isinstance(remaining, (int, float)):
            parts.append(f"CTX {remaining:g}%")
    branch = single_line(snapshot.get("branch"), 48)
    if branch:
        parts.append(f"BR {branch}")
    parts.append(workerbee_label(workerbee))
    evidence = snapshot.get("evidence")
    if isinstance(evidence, Mapping):
        source = single_line(evidence.get("source"), 32)
        if source:
            parts.append(source.replace("_", " "))
    for part in parts:
        text.append(" │ ", style=PBX_PALETTE["muted"])
        text.append(part, style=PBX_PALETTE["foreground"])
    return RuntimeHeader(text, state, len(frames) > 1)


def runtime_topology(
    snapshot: Mapping[str, Any] | None,
    *,
    frame: int = 0,
    color_depth: int = 24,
    limit: int = 6,
    now: float | None = None,
) -> Text:
    snapshot = snapshot or {}
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, Mapping) or capabilities.get("native_subagents") != "observed":
        return Text()
    raw_children = snapshot.get("children")
    if not isinstance(raw_children, list) or not raw_children:
        return Text()
    all_children = [item for item in raw_children if isinstance(item, Mapping)]
    terminal_states = {"complete", "error", "interrupted"}
    current = time.time() if now is None else now
    done_count = sum(
        1 for child in all_children if str(child.get("state") or "") in terminal_states
    )
    active_count = sum(
        1 for child in all_children if child_is_confirmed_active(child, now=current)
    )
    stale_count = len(all_children) - active_count - done_count
    children = sorted(
        all_children,
        key=lambda child: (
            child_display_rank(child, now=current),
            -child_recent_timestamp(child),
            hierarchy_depth(child, all_children),
            str(child.get("child_id") or ""),
        ),
    )[: max(1, limit)]
    text = Text(
        (
            f"SUBAGENTS {active_count} active · {stale_count} stale · "
            f"{done_count} done"
        ),
        style=f"bold {PBX_PALETTE['muted']}",
    )
    text.append(
        " · inherited root authority (unverified)",
        style=PBX_PALETTE["warning"],
    )
    for index, child in enumerate(children):
        state = runtime_state(child)
        frames = STATE_FRAMES[state]
        symbol = frames[frame % len(frames)]
        depth = hierarchy_depth(child, all_children)
        connector = ("  " * depth) + (
            "└─" if index == len(children) - 1 else "├─"
        )
        child_id = short_session(single_line(child.get("child_id"), 240) or "child")
        nickname = single_line(child.get("nickname"), 28)
        role = single_line(child.get("role"), 28)
        identity = nickname or role
        label = f"{identity} · {child_id}" if identity else child_id
        model = short_model(single_line(child.get("model"), 160) or "")
        effort = single_line(child.get("reasoning_effort"), 40)
        suffix = f" · {model}{'/' + effort.upper() if effort else ''}" if model else ""
        state_label = child_display_state(child, now=current)
        text.append(f"\n{connector} ", style=PBX_PALETTE["muted"])
        text.append(symbol, style=f"bold {runtime_color(state, color_depth)}")
        text.append(f" {label}  {state_label}{suffix}", style=PBX_PALETTE["foreground"])
    extra = len(raw_children) - len(children)
    if extra > 0:
        text.append(f"\n└─ +{extra} more", style=PBX_PALETTE["muted"])
    return text


def child_is_confirmed_active(
    child: Mapping[str, Any],
    *,
    now: float,
) -> bool:
    if runtime_state(child) not in SUBAGENT_ACTIVE_STATES:
        return False
    availability = str(child.get("availability") or "").strip().casefold()
    if availability and availability != "active":
        return False
    source = str(child.get("transition_source") or "").strip().casefold()
    if not availability and source != "app_server":
        return False
    observed_at = child.get("availability_observed_at") or child.get(
        "last_transition_at"
    )
    try:
        age = max(0.0, now - float(observed_at))
    except (TypeError, ValueError):
        return False
    return age <= SUBAGENT_ACTIVE_FRESHNESS_SECONDS


def child_display_rank(child: Mapping[str, Any], *, now: float) -> int:
    if child_is_confirmed_active(child, now=now):
        return 0
    if runtime_state(child) in {
        CodexRuntimeState.COMPLETE,
        CodexRuntimeState.ERROR,
        CodexRuntimeState.INTERRUPTED,
    }:
        return 2
    return 1


def child_recent_timestamp(child: Mapping[str, Any]) -> float:
    for key in (
        "availability_observed_at",
        "last_transition_at",
        "finished_at",
        "started_at",
    ):
        try:
            return float(child.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def child_display_state(child: Mapping[str, Any], *, now: float) -> str:
    state = runtime_state(child)
    if state in {
        CodexRuntimeState.COMPLETE,
        CodexRuntimeState.ERROR,
        CodexRuntimeState.INTERRUPTED,
    }:
        return STATE_LABELS[state]
    if child_is_confirmed_active(child, now=now):
        return STATE_LABELS[state]
    availability = str(child.get("availability") or "").strip().casefold()
    if availability == "idle":
        return "IDLE"
    if availability == "unloaded":
        return f"UNLOADED · last {STATE_LABELS[state]}"
    if availability == "error":
        return "DISCOVERY ERROR"
    return f"STALE · last {STATE_LABELS[state]}"


def hierarchy_depth(
    child: Mapping[str, Any],
    children: list[Mapping[str, Any]],
) -> int:
    spawn_depth = child.get("spawn_depth")
    if isinstance(spawn_depth, int):
        return max(0, min(4, spawn_depth - 1))
    by_id = {
        str(item.get("child_id") or ""): item
        for item in children
        if item.get("child_id")
    }
    parent_id = str(child.get("parent_thread_id") or "")
    seen: set[str] = set()
    depth = 0
    while parent_id in by_id and parent_id not in seen and depth < 4:
        seen.add(parent_id)
        depth += 1
        parent_id = str(by_id[parent_id].get("parent_thread_id") or "")
    return depth


def workerbee_label(status: Mapping[str, Any] | None) -> str:
    if not status:
        return "WB ?"
    if status.get("error") or not status.get("available"):
        return "WB ▲"
    if status.get("running") is True:
        return "WB ●"
    return "WB ◇"


def short_model(model: str) -> str:
    normalized = model.strip()
    if normalized.startswith("gpt-"):
        normalized = normalized[4:]
    return normalized.upper()


def short_session(session_id: str) -> str:
    value = session_id.strip()
    return value if len(value) <= 12 else f"{value[:8]}…{value[-3:]}"


def single_line(value: object, limit: int) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()[:limit]
