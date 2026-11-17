from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable

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
    active_count = sum(
        1
        for child in all_children
        if str(child.get("state") or "") not in terminal_states
    )
    done_count = len(all_children) - active_count
    children = sorted(
        all_children,
        key=lambda child: (
            hierarchy_depth(child, all_children),
            float(child.get("started_at") or 0.0),
            str(child.get("child_id") or ""),
        ),
    )[: max(1, limit)]
    text = Text(
        f"SUBAGENTS {active_count} active · {done_count} done",
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
        label = single_line(child.get("role"), 28) or short_session(
            single_line(child.get("child_id"), 240) or "child"
        )
        model = short_model(single_line(child.get("model"), 160) or "")
        effort = single_line(child.get("reasoning_effort"), 40)
        suffix = f" · {model}{'/' + effort.upper() if effort else ''}" if model else ""
        text.append(f"\n{connector} ", style=PBX_PALETTE["muted"])
        text.append(symbol, style=f"bold {runtime_color(state, color_depth)}")
        text.append(f" {label}  {STATE_LABELS[state]}{suffix}", style=PBX_PALETTE["foreground"])
    extra = len(raw_children) - len(children)
    if extra > 0:
        text.append(f"\n└─ +{extra} more", style=PBX_PALETTE["muted"])
    return text


def hierarchy_depth(
    child: Mapping[str, Any],
    children: list[Mapping[str, Any]],
) -> int:
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
