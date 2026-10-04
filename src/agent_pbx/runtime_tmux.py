from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import socket
import stat
import subprocess
from typing import Any, Mapping


class RuntimeServerMode(str, Enum):
    DEDICATED = "dedicated"
    OUTER_IF_PRESENT = "outer_if_present"
    OUTER_REQUIRED = "outer_required"


@dataclass(frozen=True)
class TmuxServerIdentity:
    requested_mode: RuntimeServerMode
    effective_mode: RuntimeServerMode
    server_id: str
    socket_path: str
    ready: bool
    outer_detected: bool
    message: str = ""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        return ("tmux", "-S", self.socket_path)

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["requested_mode"] = self.requested_mode.value
        result["effective_mode"] = self.effective_mode.value
        result["command_prefix"] = list(self.command_prefix)
        return result


@dataclass(frozen=True)
class OuterTmuxContext:
    session_name: str
    window_id: str
    pane_id: str
    client_tty: str


@dataclass(frozen=True)
class RuntimeTmuxPane:
    session_name: str
    window_id: str
    window_name: str
    pane_id: str
    pane_pid: int | None
    cwd: str
    current_command: str
    title: str


@dataclass(frozen=True)
class RuntimeMappingAssessment:
    state: str
    safe: bool
    message: str
    repair_pane_id: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimePopPlan:
    """A reviewable tmux client transition that never moves the runtime pane."""

    action: str
    command: tuple[str, ...]
    target_session: str
    target_client: str | None = None

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["command"] = list(self.command)
        return result


RUNTIME_PANE_FORMAT = "\t".join(
    (
        "#{session_name}",
        "#{window_id}",
        "#{window_name}",
        "#{pane_id}",
        "#{pane_pid}",
        "#{pane_current_path}",
        "#{pane_current_command}",
        "#{pane_title}",
    )
)

# Darwin's sockaddr_un.sun_path is shorter than Linux's. Keep generated paths
# below both limits, including room for the terminating NUL byte.
MAX_GENERATED_UNIX_SOCKET_PATH_BYTES = 100


def normalize_runtime_server_mode(value: object) -> RuntimeServerMode:
    cleaned = str(value or "").strip().casefold().replace("-", "_")
    try:
        return RuntimeServerMode(cleaned or RuntimeServerMode.DEDICATED.value)
    except ValueError:
        return RuntimeServerMode.DEDICATED


def tmux_socket_from_environment(value: str | None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    socket_value = raw.split(",", 1)[0].strip()
    path = Path(socket_value).expanduser()
    if not path.is_absolute():
        return None
    return path


def validate_tmux_socket(path: Path, *, uid: int | None = None) -> tuple[bool, str]:
    expected_uid = os.getuid() if uid is None else int(uid)
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        return False, f"tmux socket is unavailable: {exc}"
    if info.st_uid != expected_uid:
        return False, "tmux socket is not owned by the current user"
    if not stat.S_ISSOCK(info.st_mode):
        return False, "tmux path is not a Unix socket"
    return True, ""


def runtime_server_id(path: Path, *, uid: int | None = None) -> str:
    owner = os.getuid() if uid is None else int(uid)
    canonical = str(path.resolve(strict=False))
    return hashlib.sha256(f"{owner}:{canonical}".encode()).hexdigest()[:24]


def resolve_runtime_tmux_server(
    mode: RuntimeServerMode | str,
    *,
    environ: Mapping[str, str] | None = None,
    runtime_dir: Path | None = None,
    uid: int | None = None,
) -> TmuxServerIdentity:
    requested = normalize_runtime_server_mode(mode)
    env = os.environ if environ is None else environ
    outer_socket = tmux_socket_from_environment(env.get("TMUX"))
    if requested in {RuntimeServerMode.OUTER_IF_PRESENT, RuntimeServerMode.OUTER_REQUIRED}:
        if outer_socket is not None:
            ready, message = validate_tmux_socket(outer_socket, uid=uid)
            if ready:
                return TmuxServerIdentity(
                    requested,
                    requested,
                    runtime_server_id(outer_socket, uid=uid),
                    str(outer_socket.resolve(strict=True)),
                    True,
                    True,
                )
            if requested is RuntimeServerMode.OUTER_REQUIRED:
                return TmuxServerIdentity(
                    requested,
                    requested,
                    runtime_server_id(outer_socket, uid=uid),
                    str(outer_socket),
                    False,
                    True,
                    message,
                )
        elif requested is RuntimeServerMode.OUTER_REQUIRED:
            missing = Path("/nonexistent/agent-pbx-outer-tmux")
            return TmuxServerIdentity(
                requested,
                requested,
                runtime_server_id(missing, uid=uid),
                "",
                False,
                False,
                "TMUX does not identify an outer server",
            )
    base = runtime_dir or Path(
        env.get("XDG_RUNTIME_DIR") or f"/tmp/agent-pbx-{os.getuid()}"
    )
    dedicated_socket = base.expanduser() / "agent-pbx" / "runtime-tmux.sock"
    shortened = False
    if len(os.fsencode(str(dedicated_socket))) > MAX_GENERATED_UNIX_SOCKET_PATH_BYTES:
        owner = os.getuid() if uid is None else int(uid)
        digest = hashlib.sha256(str(dedicated_socket).encode()).hexdigest()[:16]
        dedicated_socket = Path("/tmp") / f"agent-pbx-{owner}" / f"rt-{digest}.sock"
        shortened = True
    fallback_message = (
        "outer tmux unavailable; using the dedicated runtime server"
        if requested is RuntimeServerMode.OUTER_IF_PRESENT
        else ""
    )
    if shortened:
        fallback_message = (
            f"{fallback_message}; " if fallback_message else ""
        ) + "dedicated socket moved to a short user-local path"
    return TmuxServerIdentity(
        requested,
        RuntimeServerMode.DEDICATED,
        runtime_server_id(dedicated_socket, uid=uid),
        str(dedicated_socket),
        True,
        False,
        fallback_message,
    )


def ensure_runtime_socket_parent(path: Path) -> None:
    """Create a user-private directory for a generated dedicated socket."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PermissionError("runtime tmux socket parent is not a real directory")
    if info.st_uid != os.getuid():
        raise PermissionError("runtime tmux socket directory is not user-owned")
    path.parent.chmod(0o700)


def read_outer_tmux_context(identity: TmuxServerIdentity) -> OuterTmuxContext | None:
    if not identity.ready or not identity.outer_detected:
        return None
    result = subprocess.run(
        [
            *identity.command_prefix,
            "display-message",
            "-p",
            "#{session_name}\t#{window_id}\t#{pane_id}\t#{client_tty}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.rstrip("\n").split("\t")
    if len(parts) != 4 or not parts[0]:
        return None
    return OuterTmuxContext(*parts)


def recursive_attachment_reason(
    *,
    target_session: str,
    target_pane_id: str | None,
    outer: OuterTmuxContext | None,
) -> str | None:
    if outer is None:
        return None
    if target_session == outer.session_name:
        return "runtime target is the session containing the Agent PBX TUI"
    if target_pane_id and target_pane_id == outer.pane_id:
        return "runtime target is the Agent PBX TUI pane"
    return None


def tmux_client_attach_command(
    mapping: Mapping[str, Any],
    *,
    read_only: bool = False,
) -> tuple[str, ...]:
    socket_path = str(mapping.get("socket_path") or "").strip()
    session_name = str(mapping.get("session_name") or "").strip()
    if not socket_path or not session_name:
        raise ValueError("runtime mapping does not identify a tmux socket and session")
    # The embedded terminal is always UTF-8 capable. ``-u`` prevents tmux
    # from inheriting a non-UTF-8 SSH/Termux locale and selecting a degraded
    # client encoding.
    command = ["tmux", "-S", socket_path, "-u", "attach-session"]
    if read_only:
        command.append("-r")
    command.extend(("-t", session_name))
    return tuple(command)


def tmux_select_runtime_pane_command(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    socket_path = str(mapping.get("socket_path") or "").strip()
    pane_id = str(mapping.get("pane_id") or "").strip()
    if not socket_path or not pane_id:
        raise ValueError("runtime mapping does not identify a tmux socket and pane")
    return ("tmux", "-S", socket_path, "select-pane", "-t", pane_id)


def runtime_pop_plan(
    mapping: Mapping[str, Any],
    *,
    direction: str,
) -> RuntimePopPlan:
    """Build a targeted pop transition for one originating tmux client.

    Integrated mode switches exactly the recorded client. Dedicated mode returns
    a foreground attach command suitable for Textual's suspend context.
    """

    normalized = direction.strip().casefold().replace("_", "-")
    if normalized not in {"out", "in"}:
        raise ValueError("direction must be 'out' or 'in'")
    socket_path = str(mapping.get("socket_path") or "").strip()
    runtime_session = str(mapping.get("session_name") or "").strip()
    origin_session = str(mapping.get("origin_session_name") or "").strip()
    origin_client = str(mapping.get("origin_client_tty") or "").strip()
    mode = normalize_runtime_server_mode(mapping.get("server_mode"))
    if not socket_path or not runtime_session:
        raise ValueError("runtime mapping is incomplete")
    if mode is RuntimeServerMode.DEDICATED:
        if normalized == "in":
            raise ValueError("dedicated runtime returns when its attached client detaches")
        return RuntimePopPlan(
            "suspend_attach",
            tmux_client_attach_command(mapping),
            runtime_session,
        )
    if not origin_client:
        raise ValueError("integrated runtime has no recorded originating client")
    target_session = runtime_session if normalized == "out" else origin_session
    if not target_session:
        raise ValueError("integrated runtime has no recorded originating session")
    return RuntimePopPlan(
        f"switch_client_{normalized}",
        (
            "tmux",
            "-S",
            socket_path,
            "switch-client",
            "-c",
            origin_client,
            "-t",
            target_session,
        ),
        target_session,
        origin_client,
    )


def execute_runtime_pop_plan(plan: RuntimePopPlan) -> subprocess.CompletedProcess[str]:
    return subprocess.run(plan.command, capture_output=True, text=True)


def parse_runtime_pane_line(line: str) -> RuntimeTmuxPane | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 8:
        return None
    try:
        pane_pid = int(parts[4]) or None
    except ValueError:
        pane_pid = None
    return RuntimeTmuxPane(
        session_name=parts[0],
        window_id=parts[1],
        window_name=parts[2],
        pane_id=parts[3],
        pane_pid=pane_pid,
        cwd=parts[5],
        current_command=parts[6],
        title=parts[7],
    )


def list_runtime_panes(identity: TmuxServerIdentity) -> tuple[RuntimeTmuxPane, ...]:
    if not identity.ready:
        return ()
    result = subprocess.run(
        [*identity.command_prefix, "list-panes", "-a", "-F", RUNTIME_PANE_FORMAT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ()
    return tuple(
        pane
        for line in result.stdout.splitlines()
        if (pane := parse_runtime_pane_line(line)) is not None
    )


def process_start_ticks(pid: int | None, *, proc_root: Path = Path("/proc")) -> int | None:
    if not pid:
        return None
    try:
        fields = (proc_root / str(pid) / "stat").read_text().split()
        return int(fields[21])
    except (OSError, IndexError, ValueError):
        return None


def assess_runtime_mapping(
    mapping: Mapping[str, Any],
    panes: tuple[RuntimeTmuxPane, ...],
    *,
    observed_start_ticks: int | None = None,
) -> RuntimeMappingAssessment:
    pane_id = str(mapping.get("pane_id") or "")
    selected = next((pane for pane in panes if pane.pane_id == pane_id), None)
    if selected is None:
        candidates = [
            pane
            for pane in panes
            if pane.session_name == mapping.get("session_name")
            and pane.window_name == mapping.get("window_name")
            and (not mapping.get("cwd") or pane.cwd == mapping.get("cwd"))
        ]
        if len(candidates) == 1:
            return RuntimeMappingAssessment(
                "moved",
                False,
                "The recorded pane is missing; one repair candidate matches session, window, and cwd.",
                candidates[0].pane_id,
            )
        return RuntimeMappingAssessment(
            "missing",
            False,
            "The recorded tmux pane is absent from its runtime server.",
        )
    if selected.session_name != mapping.get("session_name"):
        return RuntimeMappingAssessment("foreign", False, "Pane id now belongs to another session.")
    expected_pid = mapping.get("pane_pid")
    if expected_pid and selected.pane_pid and int(expected_pid) != selected.pane_pid:
        return RuntimeMappingAssessment("reused", False, "Pane id was reused by another process.")
    expected_ticks = mapping.get("process_start_ticks")
    if expected_ticks and observed_start_ticks and int(expected_ticks) != observed_start_ticks:
        return RuntimeMappingAssessment("reused", False, "Pane process start evidence changed.")
    if mapping.get("cwd") and selected.cwd != mapping.get("cwd"):
        return RuntimeMappingAssessment("foreign", False, "Pane cwd no longer matches the registered runtime.")
    return RuntimeMappingAssessment("ready", True, "Runtime mapping matches tmux and process evidence.")


def create_probe_socket(path: Path) -> socket.socket:
    """Create a Unix socket for tests and diagnostic probes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.bind(str(path))
    return probe
