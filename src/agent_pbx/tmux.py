from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence

from .tmux_binary import configured_tmux_binary


TMUX_PANE_FORMAT = "\t".join(
    [
        "#{session_name}",
        "#{window_index}",
        "#{pane_index}",
        "#{pane_id}",
        "#{pane_active}",
        "#{pane_current_command}",
        "#{pane_title}",
        "#{pane_current_path}",
        "#{pane_width}",
        "#{pane_height}",
        "#{history_size}",
        "#{window_name}",
        "#{alternate_on}",
        "#{session_attached}",
        "#{window_id}",
    ]
)
DEFAULT_SUBMIT_DELAY_SECONDS = 0.08
DEFAULT_QUIT_WAIT_SECONDS = 30.0
PANE_EXIT_POLL_SECONDS = 0.1
CODEX_FOREGROUND_COMMANDS = {"bun", "codex", "deno", "node", "nodejs"}
FALSE_ENV_VALUES = {"0", "false", "no", "off", "n", "disabled", ""}
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PANE_CLIPBOARD_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "XAUTHORITY",
)
AGENT_PATH_METADATA_KEYS = (
    "cwd",
    "repo",
    "work_repo",
    "current_repo",
    "live_workerbee_repo",
    "source_cwd",
    "work_root",
    "repos",
)


@dataclass(frozen=True)
class TmuxPane:
    session_name: str
    window_index: str
    pane_index: str
    pane_id: str
    active: bool
    current_command: str
    title: str
    cwd: str
    width: int
    height: int
    history_size: int
    window_name: str = ""
    alternate_on: bool = False
    session_attached: int = 0
    window_id: str = ""

    @property
    def target_label(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"


@dataclass(frozen=True)
class TmuxClipboardTransport:
    """Whether Codex can forward a copy through this pane's tmux client."""

    available: bool
    reason: str = ""
    client_name: str = ""


def int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def parse_pane_line(line: str) -> TmuxPane | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) not in {11, 12, 14, 15}:
        return None
    return TmuxPane(
        session_name=parts[0],
        window_index=parts[1],
        pane_index=parts[2],
        pane_id=parts[3],
        active=parts[4] == "1",
        current_command=parts[5],
        title=parts[6],
        cwd=parts[7],
        width=int_or_zero(parts[8]),
        height=int_or_zero(parts[9]),
        history_size=int_or_zero(parts[10]),
        window_name=parts[11] if len(parts) >= 12 else "",
        alternate_on=parts[12] == "1" if len(parts) >= 14 else False,
        session_attached=int_or_zero(parts[13]) if len(parts) >= 14 else 0,
        window_id=parts[14] if len(parts) >= 15 else "",
    )


def list_panes(
    tmux_bin: str = "tmux",
    *,
    socket_path: str | None = None,
) -> list[TmuxPane]:
    prefix = _runtime_tmux_command_prefix(
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    result = subprocess.run(
        [*prefix, "list-panes", "-a", "-F", TMUX_PANE_FORMAT],
        capture_output=True,
        check=True,
        text=True,
    )
    panes: list[TmuxPane] = []
    for line in result.stdout.splitlines():
        pane = parse_pane_line(line)
        if pane is not None:
            panes.append(pane)
    return panes


def session_exists(
    session_name: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> bool:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "has-session", "-t", session_name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def launch_pane(
    *,
    session_name: str,
    window_name: str,
    command: str,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    width: int | None = None,
    height: int | None = None,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> str:
    args: list[str]
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    existing_session = session_exists(
        session_name,
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    if existing_session:
        args = [
            *prefix,
            "new-window",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            session_name,
            "-n",
            window_name,
        ]
    else:
        args = [
            *prefix,
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            session_name,
            "-n",
            window_name,
        ]
        if width is not None and int(width) > 0:
            args.extend(["-x", str(int(width))])
        if height is not None and int(height) > 0:
            args.extend(["-y", str(int(height))])
    if cwd:
        args.extend(["-c", cwd])
    if env:
        for key, value in env.items():
            if not ENV_KEY_PATTERN.match(key):
                raise ValueError(f"invalid tmux environment key: {key!r}")
            args.extend(["-e", f"{key}={value}"])
    args.append(command)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux launch failed").strip()
        raise RuntimeError(message)
    pane_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not pane_id:
        raise RuntimeError("tmux did not return a launched pane id")
    if (width or height) and pane_session_attached(
        pane_id,
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    ) == 0:
        resize_window(
            pane_id,
            width=width,
            height=height,
            tmux_bin=tmux_bin,
            socket_path=socket_path,
        )
    return pane_id


def pane_session_attached(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> int:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{session_attached}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    return int_or_zero(result.stdout.strip())


def resize_window(
    target: str,
    *,
    width: int | None = None,
    height: int | None = None,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> None:
    if not ((width is not None and int(width) > 0) or (height is not None and int(height) > 0)):
        return
    args = [
        *_runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path),
        "resize-window",
        "-t",
        target,
    ]
    if width is not None and int(width) > 0:
        args.extend(["-x", str(int(width))])
    if height is not None and int(height) > 0:
        args.extend(["-y", str(int(height))])
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux resize failed").strip()
        raise RuntimeError(message)


def pane_root_pid(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> int | None:
    prefix = _runtime_tmux_command_prefix(
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_pid}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = int_or_zero(result.stdout.strip())
    return value or None


def process_tree_ids(root_pid: int, *, proc_root: str | Path = "/proc") -> set[int]:
    """Return a Linux process tree without depending on psutil or shell parsing."""
    root = Path(proc_root)
    children: dict[int, list[int]] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return {root_pid}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"(?m)^PPid:\s+(\d+)\s*$", status)
        if match is None:
            continue
        children.setdefault(int(match.group(1)), []).append(int(entry.name))
    found = {root_pid}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child in found:
                continue
            found.add(child)
            pending.append(child)
    return found


def pane_open_rollout_paths(
    target: str,
    *,
    codex_home: str | Path | None = None,
    tmux_bin: str = "tmux",
    proc_root: str | Path = "/proc",
) -> tuple[Path, ...]:
    """Find Codex rollout JSONL files held open by a pane's process tree."""
    pid = pane_root_pid(target, tmux_bin=tmux_bin)
    if pid is None:
        return ()
    home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    sessions_root = (home / "sessions").resolve(strict=False)
    found: dict[Path, float] = {}
    proc = Path(proc_root)
    for process_id in process_tree_ids(pid, proc_root=proc):
        fd_root = proc / str(process_id) / "fd"
        try:
            descriptors = list(fd_root.iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target_path = Path(os.readlink(descriptor))
            except OSError:
                continue
            if target_path.suffix != ".jsonl":
                continue
            resolved = target_path.resolve(strict=False)
            try:
                resolved.relative_to(sessions_root)
            except ValueError:
                continue
            try:
                found[resolved] = resolved.stat().st_mtime
            except OSError:
                continue
    return tuple(path for path, _ in sorted(found.items(), key=lambda item: item[1], reverse=True))


def respawn_pane(
    target: str,
    *,
    command: str,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> None:
    """Replace a pane command without changing its pane, window, or layout."""
    args = [
        *_runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path),
        "respawn-pane",
        "-k",
        "-t",
        target,
    ]
    if cwd:
        args.extend(["-c", cwd])
    if env:
        for key, value in env.items():
            if not ENV_KEY_PATTERN.match(key):
                raise ValueError(f"invalid tmux environment key: {key!r}")
            args.extend(["-e", f"{key}={value}"])
    args.append(command)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux respawn failed").strip()
        raise RuntimeError(message)


def kill_pane(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> None:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "kill-pane", "-t", target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux kill-pane failed").strip()
        raise RuntimeError(message)


def pane_exists(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> bool:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_id}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def pane_is_live(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> bool:
    """Return whether a pane still exists and is not a remain-on-exit pane."""
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_dead}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() != "1"


def pane_remain_on_exit(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> bool:
    """Return the effective ``remain-on-exit`` value for a pane."""
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{remain-on-exit}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux remain-on-exit lookup failed"
        ).strip()
        raise RuntimeError(message)
    return result.stdout.strip().lower() == "on"


def set_pane_remain_on_exit(
    target: str,
    enabled: bool,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> None:
    """Set a pane-local ``remain-on-exit`` override.

    A pane-local override keeps a failed replacement available for diagnostics
    and rollback without changing the setting for unrelated panes or windows.
    """
    result = subprocess.run(
        [
            *_runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path),
            "set-option",
            "-p",
            "-t",
            target,
            "remain-on-exit",
            "on" if enabled else "off",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux remain-on-exit update failed"
        ).strip()
        raise RuntimeError(message)


def pane_dead_status(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> int | None:
    """Return the retained pane's exit status, if tmux reports one."""
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_dead_status}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def pane_start_command(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> str:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_start_command}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux pane_start_command lookup failed"
        ).strip()
        raise RuntimeError(message)
    return result.stdout.strip()


def _runtime_tmux_command_prefix(
    *,
    tmux_bin: str,
    socket_path: str | None,
) -> list[str]:
    """Build an exact runtime-server command prefix from a stored mapping."""

    socket = str(socket_path or "").strip()
    if not socket:
        return [configured_tmux_binary(tmux_bin)]
    if not Path(socket).is_absolute():
        raise ValueError("tmux runtime socket path must be absolute")
    return [configured_tmux_binary(tmux_bin), "-S", socket]


def pane_in_copy_mode(
    target: str,
    *,
    socket_path: str | None = None,
    tmux_bin: str = "tmux",
) -> bool:
    """Return whether the exact runtime pane is currently in a tmux mode."""

    prefix = _runtime_tmux_command_prefix(
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    result = subprocess.run(
        [*prefix, "display-message", "-p", "-t", target, "#{pane_in_mode}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux pane mode lookup failed"
        ).strip()
        raise RuntimeError(message)
    return result.stdout.strip() == "1"


def enter_pane_copy_mode(
    target: str,
    *,
    socket_path: str | None = None,
    tmux_bin: str = "tmux",
) -> bool:
    """Enter tmux copy mode with exit-at-bottom behavior for one exact pane."""

    if pane_in_copy_mode(
        target,
        socket_path=socket_path,
        tmux_bin=tmux_bin,
    ):
        return False
    prefix = _runtime_tmux_command_prefix(
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    result = subprocess.run(
        [*prefix, "copy-mode", "-e", "-t", target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux copy mode activation failed"
        ).strip()
        raise RuntimeError(message)
    return True


def scroll_pane_copy_mode(
    target: str,
    direction: int,
    *,
    lines: int = 5,
    socket_path: str | None = None,
    tmux_bin: str = "tmux",
) -> bool:
    """Scroll an exact runtime pane without forwarding wheel input to Codex.

    Upward scrolling enters copy mode when needed. Downward scrolling is a
    no-op outside copy mode, so reaching live output can never turn a continued
    swipe into prompt-history navigation in the child application.
    """

    normalized_direction = -1 if int(direction) < 0 else 1
    amount = max(1, min(200, int(lines)))
    active = pane_in_copy_mode(
        target,
        socket_path=socket_path,
        tmux_bin=tmux_bin,
    )
    if normalized_direction < 0 and not active:
        enter_pane_copy_mode(
            target,
            socket_path=socket_path,
            tmux_bin=tmux_bin,
        )
        active = True
    if not active:
        return False
    prefix = _runtime_tmux_command_prefix(
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    command = "scroll-up" if normalized_direction < 0 else "scroll-down"
    result = subprocess.run(
        [
            *prefix,
            "send-keys",
            "-X",
            "-t",
            target,
            "-N",
            str(amount),
            command,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux copy mode scroll failed"
        ).strip()
        raise RuntimeError(message)
    return True


def _pane_pid(target: str, *, tmux_bin: str = "tmux") -> int | None:
    result = subprocess.run(
        [configured_tmux_binary(tmux_bin), "display-message", "-p", "-t", target, "#{pane_pid}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        value = int(result.stdout.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def pane_clipboard_environment(
    target: str,
    *,
    tmux_bin: str = "tmux",
) -> dict[str, str]:
    """Return only desktop variables needed to read this pane's clipboard.

    The pane environment can contain credentials and launch-only data.  This helper
    deliberately retains a small desktop-session allowlist and never exposes the
    rest of it to the TUI, settings, or logs.
    """
    pid = _pane_pid(target, tmux_bin=tmux_bin)
    if pid is None:
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    environment: dict[str, str] = {}
    for item in raw.split(b"\0"):
        try:
            key_bytes, value_bytes = item.split(b"=", 1)
            key = key_bytes.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            continue
        if key not in PANE_CLIPBOARD_ENV_KEYS:
            continue
        try:
            environment[key] = value_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return environment


def _tmux_command_prefix(
    target: str,
    *,
    tmux_bin: str,
) -> list[str]:
    """Use the pane's server socket when it is safely discoverable."""
    pid = _pane_pid(target, tmux_bin=tmux_bin)
    if pid is None:
        return [configured_tmux_binary(tmux_bin)]
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return [configured_tmux_binary(tmux_bin)]
    for item in raw.split(b"\0"):
        if not item.startswith(b"TMUX="):
            continue
        try:
            value = item[5:].decode("utf-8")
        except UnicodeDecodeError:
            break
        socket = value.split(",", 1)[0].strip()
        if socket.startswith("/"):
            return [configured_tmux_binary(tmux_bin), "-S", socket]
        break
    return [configured_tmux_binary(tmux_bin)]


def pane_tmux_buffer(
    target: str,
    *,
    tmux_bin: str = "tmux",
) -> str:
    """Read the default paste buffer from the tmux server that owns *target*."""
    result = subprocess.run(
        [*_tmux_command_prefix(target, tmux_bin=tmux_bin), "show-buffer"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux show-buffer failed").strip()
        raise RuntimeError(message)
    return result.stdout.rstrip("\n")


def tmux_clipboard_transport(
    target: str,
    *,
    tmux_bin: str = "tmux",
) -> TmuxClipboardTransport:
    """Mirror the eligibility checks used by Codex's tmux clipboard transport."""
    prefix = _tmux_command_prefix(target, tmux_bin=tmux_bin)

    def output(args: list[str]) -> str:
        result = subprocess.run(
            [*prefix, *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "tmux command failed").strip()
            raise RuntimeError(message)
        return result.stdout

    try:
        if output(["show-options", "-gv", "set-clipboard"]).strip() == "off":
            return TmuxClipboardTransport(False, "tmux clipboard forwarding is disabled")
        session_id = output(
            ["display-message", "-p", "-t", target, "#{session_id}"]
        ).strip()
        clients = output(
            [
                "list-clients",
                "-t",
                session_id,
                "-F",
                "#{client_activity} #{client_name}",
            ]
        )
    except RuntimeError as exc:
        return TmuxClipboardTransport(False, str(exc))

    candidates: list[tuple[int, str]] = []
    for line in clients.splitlines():
        activity, separator, client_name = line.partition(" ")
        if not separator or not client_name.strip():
            continue
        try:
            candidates.append((int(activity), client_name.strip()))
        except ValueError:
            continue
    if not candidates:
        return TmuxClipboardTransport(
            False,
            "tmux clipboard forwarding is unavailable: no attached client",
        )
    _activity, client_name = max(candidates)
    try:
        terminal_info = output(["show-messages", "-T", "-t", client_name])
    except RuntimeError as exc:
        return TmuxClipboardTransport(False, str(exc), client_name)
    has_ms = any(
        "Ms: (string) " in line and line.split("Ms: (string) ", 1)[1].strip()
        for line in terminal_info.splitlines()
    )
    if not has_ms:
        return TmuxClipboardTransport(
            False,
            "tmux clipboard forwarding is unavailable: missing Ms capability",
            client_name,
        )
    return TmuxClipboardTransport(True, client_name=client_name)


def wait_for_pane_exit(
    target: str,
    *,
    timeout_seconds: float = DEFAULT_QUIT_WAIT_SECONDS,
    interval_seconds: float = PANE_EXIT_POLL_SECONDS,
    tmux_bin: str = "tmux",
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if not pane_exists(target, tmux_bin=tmux_bin):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, interval_seconds))


def pane_current_command(
    target: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> str | None:
    """Return the pane's foreground command, or ``None`` once it is gone."""

    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    result = subprocess.run(
        [
            *prefix,
            "display-message",
            "-p",
            "-t",
            target,
            "#{pane_current_command}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def command_may_be_codex(command: str) -> bool:
    """Recognize the foreground launchers used by interactive Codex."""

    normalized = Path(str(command or "").strip()).name.casefold()
    return normalized in CODEX_FOREGROUND_COMMANDS or "codex" in normalized


def wait_for_codex_exit(
    target: str,
    *,
    timeout_seconds: float = DEFAULT_QUIT_WAIT_SECONDS,
    interval_seconds: float = PANE_EXIT_POLL_SECONDS,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> bool:
    """Wait until Codex exits, including when tmux keeps its parent shell alive."""

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        command = pane_current_command(
            target,
            tmux_bin=tmux_bin,
            socket_path=socket_path,
        )
        if command is None or not command_may_be_codex(command):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, interval_seconds))


def quit_pane(
    target: str,
    *,
    tmux_bin: str = "tmux",
    timeout_seconds: float = DEFAULT_QUIT_WAIT_SECONDS,
    socket_path: str | None = None,
) -> bool:
    current_command = pane_current_command(
        target,
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    if current_command is None or not command_may_be_codex(current_command):
        return True
    # Slash commands must be typed as literal keys. Bracketed paste deliberately
    # leaves them as prompt text in current Codex releases.
    send_literal_keys(
        target,
        "/quit",
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )
    return wait_for_codex_exit(
        target,
        timeout_seconds=timeout_seconds,
        tmux_bin=tmux_bin,
        socket_path=socket_path,
    )


def capture_start_arg(lines: int) -> str:
    return "0" if lines <= 0 else f"-{lines}"


def capture_pane(
    target: str,
    *,
    lines: int = 0,
    tmux_bin: str = "tmux",
    join_wrapped: bool = True,
    alternate_screen: bool = False,
    copy_mode: bool = False,
    preserve_trailing_spaces: bool = False,
    socket_path: str | None = None,
) -> str:
    args = [
        *_runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path),
        "capture-pane",
        "-p",
    ]
    if join_wrapped:
        args.append("-J")
    if alternate_screen:
        args.append("-a")
    if copy_mode:
        args.append("-M")
    if preserve_trailing_spaces:
        args.append("-N")
    args.extend(["-S", capture_start_arg(int(lines)), "-t", target])
    result = subprocess.run(
        args,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def bracketed_paste_enabled() -> bool:
    value = os.getenv("AGENT_PBX_TUI_TMUX_BRACKETED_PASTE")
    if value is None:
        return True
    return value.strip().lower() not in FALSE_ENV_VALUES


def send_text(
    target: str,
    text: str,
    *,
    tmux_bin: str = "tmux",
    submit_delay_seconds: float = DEFAULT_SUBMIT_DELAY_SECONDS,
    bracketed_paste: bool | None = None,
    submit: bool = True,
    socket_path: str | None = None,
) -> None:
    buffer_name = f"agent-pbx-{os.getpid()}"
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    subprocess.run(
        [*prefix, "load-buffer", "-b", buffer_name, "-"],
        input=text,
        check=True,
        text=True,
    )
    paste_cmd = [*prefix, "paste-buffer"]
    use_bracketed_paste = (
        bracketed_paste if bracketed_paste is not None else bracketed_paste_enabled()
    )
    if use_bracketed_paste:
        paste_cmd.append("-p")
    paste_cmd.extend(["-d", "-b", buffer_name, "-t", target])
    subprocess.run(
        paste_cmd,
        check=True,
        text=True,
    )
    if submit:
        if submit_delay_seconds > 0:
            time.sleep(submit_delay_seconds)
        subprocess.run(
            [*prefix, "send-keys", "-t", target, "C-m"],
            check=True,
            text=True,
        )


def send_literal_keys(
    target: str,
    text: str,
    *,
    tmux_bin: str = "tmux",
    submit_delay_seconds: float = DEFAULT_SUBMIT_DELAY_SECONDS,
    submit: bool = True,
    socket_path: str | None = None,
) -> None:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    subprocess.run(
        [*prefix, "send-keys", "-t", target, "-l", text],
        check=True,
        text=True,
    )
    if submit:
        if submit_delay_seconds > 0:
            time.sleep(submit_delay_seconds)
        subprocess.run(
            [*prefix, "send-keys", "-t", target, "C-m"],
            check=True,
            text=True,
        )


def send_key(
    target: str,
    key: str,
    *,
    tmux_bin: str = "tmux",
    socket_path: str | None = None,
) -> None:
    prefix = _runtime_tmux_command_prefix(tmux_bin=tmux_bin, socket_path=socket_path)
    subprocess.run(
        [*prefix, "send-keys", "-t", target, key],
        check=True,
        text=True,
    )


def iter_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        strings: list[str] = []
        for item in value:
            strings.extend(iter_string_values(item))
        return strings
    return []


def unique_strings(values: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def agent_path_hints(agent: Mapping[str, Any]) -> list[str]:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    hints: list[str] = []
    for key in AGENT_PATH_METADATA_KEYS:
        hints.extend(iter_string_values(metadata.get(key)))
    return [
        value
        for value in unique_strings(hints)
        if "/" in value or value.startswith("~")
    ]


def agent_match_tokens(agent: Mapping[str, Any], path_hints: Sequence[str]) -> list[str]:
    agent_id = str(agent.get("agent_id") or "").strip()
    tokens = [
        str(agent.get("project") or ""),
        str(agent.get("name") or ""),
        agent_id,
    ]
    if agent_id.startswith("codex-"):
        tokens.append(agent_id.removeprefix("codex-"))
    for path in path_hints:
        name = Path(path).name
        if name:
            tokens.append(name)
    return unique_strings(tokens)


def score_pane_path_hints(pane: TmuxPane, path_hints: Sequence[str]) -> int:
    pane_cwd = pane.cwd.rstrip("/")
    pane_cwd_lower = pane.cwd.lower()
    score = 0
    for index, path in enumerate(path_hints):
        hint = path.rstrip("/")
        if not hint:
            continue
        if pane_cwd == hint:
            exact_score = 80 if len(path_hints) == 1 else 70
            if index > 0:
                exact_score = 100
            score = max(score, exact_score)
            continue
        hint_name = Path(hint).name.lower()
        if hint_name and hint_name in pane_cwd_lower:
            score = max(score, 30)
    return score


def score_pane_for_agent(pane: TmuxPane, agent: Mapping[str, Any]) -> int:
    path_hints = agent_path_hints(agent)
    pane_text = (
        f"{pane.title} {pane.cwd} {pane.current_command} {pane.window_name}"
    ).lower()
    score = 0

    score += score_pane_path_hints(pane, path_hints)

    command = pane.current_command.lower()
    if command in {"codex", "node"} or "codex" in command:
        score += 30

    for token in agent_match_tokens(agent, path_hints):
        token = token.lower()
        if token and token in pane_text:
            score += 20

    return score


def pane_matches_agent(pane: TmuxPane, agent: Mapping[str, Any]) -> bool:
    path_hints = agent_path_hints(agent)
    project = str(agent.get("project") or "").strip().lower()
    tokens = [token.lower() for token in agent_match_tokens(agent, path_hints)]
    pane_text = f"{pane.title} {pane.cwd} {pane.window_name}".lower()
    if not path_hints and not project:
        return True
    if any(pane.cwd.rstrip("/") == path.rstrip("/") for path in path_hints):
        return True
    if project and project in pane_text:
        return True
    if any(token and token in pane_text for token in tokens):
        return True
    return False


def choose_pane_for_agent(
    panes: Sequence[TmuxPane],
    agent: Mapping[str, Any],
    *,
    min_score: int = 50,
) -> TmuxPane | None:
    scored = sorted(
        ((score_pane_for_agent(pane, agent), pane) for pane in panes),
        key=lambda item: (-item[0], item[1].target_label, item[1].pane_id),
    )
    if not scored or scored[0][0] < min_score:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def ranked_panes_for_agent(
    panes: Sequence[TmuxPane],
    agent: Mapping[str, Any],
) -> list[TmuxPane]:
    return [
        pane
        for _, pane in sorted(
            ((score_pane_for_agent(pane, agent), pane) for pane in panes),
            key=lambda item: (-item[0], item[1].target_label, item[1].pane_id),
        )
    ]
