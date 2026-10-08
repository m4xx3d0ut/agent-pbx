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

from .tmux_binary import configured_tmux_binary


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
    tmux_bin: str = ""

    @property
    def command_prefix(self) -> tuple[str, ...]:
        return (configured_tmux_binary(self.tmux_bin or None), "-S", self.socket_path)

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
class RuntimeTmuxClient:
    """Live tmux client identity used for one targeted pop transaction."""

    tty: str
    session_name: str
    flags: frozenset[str]
    client_pid: int | None

    @property
    def focused(self) -> bool:
        return "focused" in self.flags


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
RUNTIME_CLIENT_FORMAT = "\t".join(
    (
        "#{client_tty}",
        "#{session_name}",
        "#{client_flags}",
        "#{client_pid}",
    )
)

# Darwin's sockaddr_un.sun_path is shorter than Linux's. Keep generated paths
# below both limits, including room for the terminating NUL byte.
MAX_GENERATED_UNIX_SOCKET_PATH_BYTES = 100
RUNTIME_TMUX_SOCKET_ENV = "AGENT_PBX_TMUX_RUNTIME_SOCKET"


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


def ensure_dedicated_runtime_server(identity: TmuxServerIdentity) -> bool:
    """Start an empty dedicated server before session secrets are supplied.

    Tmux keeps the argv of the process that first becomes the server.  Starting
    it with ``new-session -e SECRET=...`` therefore leaves launch-only values
    visible in the long-lived server command line.  A secret-free
    ``start-server`` command avoids that exposure; ``exit-empty`` keeps the
    server alive until the first managed session is created.

    Return ``True`` when this call started the server and ``False`` when an
    existing server was already responsive.
    """

    if identity.effective_mode is not RuntimeServerMode.DEDICATED:
        return False
    socket_path = Path(identity.socket_path).expanduser()
    if not socket_path.is_absolute():
        raise ValueError("dedicated runtime tmux socket must be absolute")
    ensure_runtime_socket_parent(socket_path)
    responsive = subprocess.run(
        [*identity.command_prefix, "display-message", "-p", "#{pid}"],
        capture_output=True,
        text=True,
    )
    if responsive.returncode == 0:
        return False
    started = subprocess.run(
        [
            *identity.command_prefix,
            "start-server",
            ";",
            "set-option",
            "-g",
            "exit-empty",
            "off",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        message = (
            started.stderr
            or started.stdout
            or "dedicated runtime tmux server failed to start"
        ).strip()
        raise RuntimeError(message)
    ready, message = validate_tmux_socket(socket_path)
    if not ready:
        raise RuntimeError(message)
    return True


def restore_dedicated_runtime_exit_policy(identity: TmuxServerIdentity) -> None:
    """Restore tmux's normal exit-when-empty behavior after first launch."""

    if identity.effective_mode is not RuntimeServerMode.DEDICATED:
        return
    result = subprocess.run(
        [*identity.command_prefix, "set-option", "-g", "exit-empty", "on"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # An empty temporary server may exit as the option is applied.  That is
        # the intended cleanup result when new-session itself failed.
        message = (result.stderr or result.stdout or "").casefold()
        if "no server running" not in message and "connection refused" not in message:
            detail = result.stderr or result.stdout or "unable to restore tmux exit policy"
            raise RuntimeError(detail.strip())


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
    configured_socket = str(env.get(RUNTIME_TMUX_SOCKET_ENV) or "").strip()
    if configured_socket:
        dedicated_socket = Path(configured_socket).expanduser()
        if not dedicated_socket.is_absolute():
            return TmuxServerIdentity(
                requested,
                RuntimeServerMode.DEDICATED,
                runtime_server_id(dedicated_socket, uid=uid),
                str(dedicated_socket),
                False,
                False,
                f"{RUNTIME_TMUX_SOCKET_ENV} must be an absolute path",
            )
    else:
        base = runtime_dir or Path(
            env.get("XDG_RUNTIME_DIR") or f"/tmp/agent-pbx-{os.getuid()}"
        )
        dedicated_socket = base.expanduser() / "agent-pbx" / "runtime-tmux.sock"
    shortened = False
    if (
        not configured_socket
        and len(os.fsencode(str(dedicated_socket)))
        > MAX_GENERATED_UNIX_SOCKET_PATH_BYTES
    ):
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
    if configured_socket:
        fallback_message = (
            f"{fallback_message}; " if fallback_message else ""
        ) + "using configured dedicated runtime socket"
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


def _read_outer_tmux_pane_context(
    identity: TmuxServerIdentity,
    *,
    pane_id: str,
) -> OuterTmuxContext | None:
    if not identity.ready or not identity.outer_detected:
        return None
    if not pane_id:
        return None
    result = subprocess.run(
        [
            *identity.command_prefix,
            "display-message",
            "-p",
            "-t",
            pane_id,
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


def parse_runtime_client_line(line: str) -> RuntimeTmuxClient | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 4 or not parts[0] or not parts[1]:
        return None
    try:
        client_pid = int(parts[3]) or None
    except ValueError:
        client_pid = None
    return RuntimeTmuxClient(
        tty=parts[0],
        session_name=parts[1],
        flags=frozenset(item for item in parts[2].split(",") if item),
        client_pid=client_pid,
    )


def list_runtime_clients(identity: TmuxServerIdentity) -> tuple[RuntimeTmuxClient, ...]:
    if not identity.ready:
        return ()
    result = subprocess.run(
        [*identity.command_prefix, "list-clients", "-F", RUNTIME_CLIENT_FORMAT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ()
    return tuple(
        client
        for line in result.stdout.splitlines()
        if (client := parse_runtime_client_line(line)) is not None
    )


def resolve_invoking_outer_client(
    identity: TmuxServerIdentity,
    *,
    tui_pane_id: str,
    origin_session_name: str,
) -> RuntimeTmuxClient:
    """Resolve only the live outer client that can safely own a pop action.

    A pane belongs to a session rather than to one client. When several clients
    view the origin session, the client that invoked the TUI action is the sole
    focused client. If tmux cannot prove one candidate, fail instead of choosing
    an arbitrary terminal.
    """

    pane_id = str(tui_pane_id or "").strip()
    origin_session = str(origin_session_name or "").strip()
    if not pane_id:
        raise ValueError(
            "cannot resolve the invoking outer tmux client: the TUI TMUX_PANE is unavailable"
        )
    if not origin_session:
        raise ValueError(
            "cannot resolve the invoking outer tmux client: the origin session is unavailable"
        )
    outer = _read_outer_tmux_pane_context(identity, pane_id=pane_id)
    if outer is None:
        raise ValueError(
            f"cannot resolve the invoking outer tmux client: TUI pane {pane_id} is not live"
        )
    if outer.pane_id != pane_id or outer.session_name != origin_session:
        raise ValueError(
            "cannot resolve the invoking outer tmux client: "
            f"TUI pane {pane_id} belongs to session {outer.session_name!r}, "
            f"not recorded origin {origin_session!r}"
        )
    candidates = tuple(
        client
        for client in list_runtime_clients(identity)
        if client.session_name == origin_session
    )
    if len(candidates) == 1:
        return candidates[0]
    focused = tuple(client for client in candidates if client.focused)
    if len(focused) == 1:
        return focused[0]
    if not candidates:
        raise ValueError(
            "cannot resolve the invoking outer tmux client: "
            f"no live client is attached to origin session {origin_session!r}"
        )
    raise ValueError(
        "cannot resolve the invoking outer tmux client safely: "
        f"{len(candidates)} clients are attached to origin session "
        f"{origin_session!r} and no unique focused client exists"
    )


def read_outer_tmux_context(identity: TmuxServerIdentity) -> OuterTmuxContext | None:
    pane_id = str(os.environ.get("TMUX_PANE") or "").strip()
    outer = _read_outer_tmux_pane_context(identity, pane_id=pane_id)
    if outer is None:
        return None
    try:
        client = resolve_invoking_outer_client(
            identity,
            tui_pane_id=pane_id,
            origin_session_name=outer.session_name,
        )
    except ValueError:
        # Mapping registration may proceed without a client. Pop-out resolves
        # the current invoker again and refuses ambiguous or absent clients.
        return outer
    return OuterTmuxContext(
        outer.session_name,
        outer.window_id,
        outer.pane_id,
        client.tty,
    )


def runtime_mapping_server_identity(
    mapping: Mapping[str, Any],
) -> TmuxServerIdentity:
    mode = normalize_runtime_server_mode(mapping.get("server_mode"))
    socket_path = str(mapping.get("socket_path") or "").strip()
    if not socket_path:
        raise ValueError("runtime mapping does not identify a tmux socket")
    return TmuxServerIdentity(
        mode,
        mode,
        str(mapping.get("server_id") or runtime_server_id(Path(socket_path))),
        socket_path,
        True,
        mode is not RuntimeServerMode.DEDICATED,
        tmux_bin=runtime_mapping_tmux_binary(mapping),
    )


def runtime_mapping_tmux_binary(mapping: Mapping[str, Any]) -> str:
    metadata = mapping.get("metadata")
    configured = ""
    if isinstance(metadata, Mapping):
        configured = str(metadata.get("tmux_bin") or "").strip()
    return configured_tmux_binary(configured or None)


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
    window_id = str(mapping.get("window_id") or "").strip()
    pane_id = str(mapping.get("pane_id") or "").strip()
    target = session_name
    if window_id:
        target = f"{target}:{window_id}"
        if pane_id:
            target = f"{target}.{pane_id}"
    # The embedded terminal is always UTF-8 capable. ``-u`` prevents tmux
    # from inheriting a non-UTF-8 SSH/Termux locale and selecting a degraded
    # client encoding. Target the mapped window and pane as part of attach;
    # selecting a pane before a session-only attach does not change the
    # session's current window, so the new client can otherwise display an
    # unrelated Agent or Operator.
    command = [
        runtime_mapping_tmux_binary(mapping),
        "-S",
        socket_path,
        "-u",
        "attach-session",
    ]
    if read_only:
        command.append("-r")
    command.extend(("-t", target))
    return tuple(command)


def tmux_select_runtime_pane_command(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    socket_path = str(mapping.get("socket_path") or "").strip()
    pane_id = str(mapping.get("pane_id") or "").strip()
    if not socket_path or not pane_id:
        raise ValueError("runtime mapping does not identify a tmux socket and pane")
    return (
        runtime_mapping_tmux_binary(mapping),
        "-S",
        socket_path,
        "select-pane",
        "-t",
        pane_id,
    )


def runtime_pop_plan(
    mapping: Mapping[str, Any],
    *,
    direction: str,
    origin_client_tty: str | None = None,
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
    origin_client = str(
        origin_client_tty or mapping.get("origin_client_tty") or ""
    ).strip()
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
    if runtime_session == origin_session:
        raise ValueError("runtime target is the session containing the Agent PBX TUI")
    target_session = runtime_session if normalized == "out" else origin_session
    if not target_session:
        raise ValueError("integrated runtime has no recorded originating session")
    return RuntimePopPlan(
        f"switch_client_{normalized}",
        (
            runtime_mapping_tmux_binary(mapping),
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
    _ready, _message, panes = probe_runtime_panes(identity)
    return panes


def probe_runtime_panes(
    identity: TmuxServerIdentity,
) -> tuple[bool, str, tuple[RuntimeTmuxPane, ...]]:
    """Query panes while distinguishing an empty server from a dead socket."""

    if not identity.ready:
        return False, identity.message or "tmux server is unavailable", ()
    try:
        result = subprocess.run(
            [*identity.command_prefix, "list-panes", "-a", "-F", RUNTIME_PANE_FORMAT],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, f"tmux server query failed: {exc}", ()
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux server query failed").strip()
        return False, message, ()
    panes = tuple(
        pane
        for line in result.stdout.splitlines()
        if (pane := parse_runtime_pane_line(line)) is not None
    )
    return True, "", panes


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
