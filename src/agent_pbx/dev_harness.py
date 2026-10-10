from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .mcp_daemon import (
    MCPDaemonConfig,
    mcp_daemon_status,
    start_mcp_daemon,
    stop_mcp_daemon,
)
from .paths import default_state_root
from .workerbee_harness import prepare_workerbee_harness_binding


HARNESS_API_VERSION = "agent-pbx.dev-harness/v1"
HARNESS_MANIFEST = "harness.json"
HARNESS_PROJECT_PREFIX = "agent-pbx-harness-"
HARNESS_ROOT_ENV = "AGENT_PBX_HARNESS_ROOT"
HARNESS_KEEP_ENV = "AGENT_PBX_HARNESS_KEEP"


def default_harness_root() -> Path:
    override = os.getenv(HARNESS_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(f"/tmp/agent-pbx-harness-{os.getuid()}").resolve()


def _safe_id(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    clean = clean.strip("-")
    if not clean or len(clean) > 80:
        raise ValueError("harness run id must contain 1-80 safe characters")
    return clean


def _source_revision(source_root: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=source_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    return commit, dirty


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


@dataclass(frozen=True, slots=True)
class HarnessPaths:
    run_root: Path
    home: Path
    state_root: Path
    config_home: Path
    data_home: Path
    cache_home: Path
    temp_dir: Path
    artifacts: Path
    bin_dir: Path
    db_path: Path
    tmux_socket: Path
    manifest: Path

    def public_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class HarnessPlan:
    run_id: str
    project: str
    harness_root: Path
    source_root: Path
    source_commit: str
    source_dirty: bool
    port: int
    token: str
    paths: HarnessPaths
    tmux_bin: str = "tmux"
    workerbee_backend: str = "fixture"

    @property
    def server_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def public_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        return {
            "api_version": HARNESS_API_VERSION,
            "run_id": self.run_id,
            "project": self.project,
            "harness_root": str(self.harness_root),
            "source_root": str(self.source_root),
            "source_commit": self.source_commit,
            "source_dirty": self.source_dirty,
            "host": "127.0.0.1",
            "port": self.port,
            "server_url": self.server_url,
            "token": self.token if include_secrets else "<redacted>",
            "paths": self.paths.public_dict(),
            "tmux_bin": self.tmux_bin,
            "workerbee_backend": self.workerbee_backend,
        }


@dataclass(slots=True)
class HarnessManifest:
    plan: HarnessPlan
    status: str = "planned"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    daemon: dict[str, Any] = field(default_factory=dict)
    tmux: dict[str, Any] = field(default_factory=dict)
    workerbee: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)

    def public_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        return {
            "api_version": HARNESS_API_VERSION,
            "plan": self.plan.public_dict(include_secrets=include_secrets),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "daemon": self.daemon,
            "tmux": self.tmux,
            "workerbee": self.workerbee,
            "runs": self.runs,
            "cleanup": self.cleanup,
        }


def build_harness_plan(
    *,
    source_root: Path,
    harness_root: Path | None = None,
    run_id: str | None = None,
    port: int | None = None,
    tmux_bin: str = "tmux",
    workerbee_backend: str = "fixture",
) -> HarnessPlan:
    source = source_root.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"source root does not exist: {source}")
    root = (harness_root or default_harness_root()).expanduser().resolve()
    production_state = default_state_root().expanduser().resolve()
    if root in {source, production_state, Path.home().resolve()}:
        raise ValueError("harness root collides with a protected production path")
    if _path_contains(root, production_state) or _path_contains(production_state, root):
        raise ValueError("harness root intersects the production Agent PBX state root")
    selected_id = _safe_id(run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    run_root = root / selected_id
    if run_root.exists():
        raise ValueError(f"harness run already exists: {selected_id}")
    selected_port = int(port or _free_loopback_port())
    if not 1024 <= selected_port <= 65535:
        raise ValueError("harness port must be between 1024 and 65535")
    if workerbee_backend not in {"fixture", "oss-local", "platform-debug-bound"}:
        raise ValueError("unsupported WorkerBee harness backend")
    commit, dirty = _source_revision(source)
    paths = HarnessPaths(
        run_root=run_root,
        home=run_root / "home",
        state_root=run_root / "state",
        config_home=run_root / "xdg" / "config",
        data_home=run_root / "xdg" / "data",
        cache_home=run_root / "xdg" / "cache",
        temp_dir=run_root / "tmp",
        artifacts=run_root / "artifacts",
        bin_dir=run_root / "bin",
        db_path=run_root / "state" / "agent-pbx.sqlite",
        tmux_socket=run_root / "runtime" / "tmux.sock",
        manifest=run_root / HARNESS_MANIFEST,
    )
    digest = hashlib.sha256(f"{commit}:{selected_id}".encode()).hexdigest()[:12]
    return HarnessPlan(
        run_id=selected_id,
        project=f"{HARNESS_PROJECT_PREFIX}{digest}",
        harness_root=root,
        source_root=source,
        source_commit=commit,
        source_dirty=dirty,
        port=selected_port,
        token=secrets.token_urlsafe(32),
        paths=paths,
        tmux_bin=tmux_bin,
        workerbee_backend=workerbee_backend,
    )


def _plan_from_dict(payload: dict[str, Any]) -> HarnessPlan:
    plan = payload["plan"] if "plan" in payload else payload
    paths = HarnessPaths(**{key: Path(value) for key, value in plan["paths"].items()})
    return HarnessPlan(
        run_id=str(plan["run_id"]),
        project=str(plan["project"]),
        harness_root=Path(plan.get("harness_root") or paths.run_root.parent),
        source_root=Path(plan["source_root"]),
        source_commit=str(plan["source_commit"]),
        source_dirty=bool(plan["source_dirty"]),
        port=int(plan["port"]),
        token=str(plan["token"]),
        paths=paths,
        tmux_bin=str(plan.get("tmux_bin") or "tmux"),
        workerbee_backend=str(plan.get("workerbee_backend") or "fixture"),
    )


def load_harness_manifest(path: Path) -> HarnessManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("api_version") != HARNESS_API_VERSION:
        raise ValueError("unsupported harness manifest version")
    plan = _plan_from_dict(payload)
    return HarnessManifest(
        plan=plan,
        status=str(payload.get("status") or "unknown"),
        created_at=float(payload.get("created_at") or 0),
        updated_at=float(payload.get("updated_at") or 0),
        daemon=dict(payload.get("daemon") or {}),
        tmux=dict(payload.get("tmux") or {}),
        workerbee=dict(payload.get("workerbee") or {}),
        runs=list(payload.get("runs") or []),
        cleanup=dict(payload.get("cleanup") or {}),
    )


def write_harness_manifest(manifest: HarnessManifest) -> None:
    manifest.updated_at = time.time()
    _write_private(
        manifest.plan.paths.manifest,
        json.dumps(manifest.public_dict(include_secrets=True), indent=2, sort_keys=True) + "\n",
    )


def harness_environment(plan: HarnessPlan) -> dict[str, str]:
    path = os.getenv("PATH", "/usr/bin:/bin")
    env = {
        "HOME": str(plan.paths.home),
        "XDG_CONFIG_HOME": str(plan.paths.config_home),
        "XDG_DATA_HOME": str(plan.paths.data_home),
        "XDG_CACHE_HOME": str(plan.paths.cache_home),
        "TMPDIR": str(plan.paths.temp_dir),
        "PATH": f"{plan.paths.bin_dir}{os.pathsep}{path}",
        "AGENT_PBX_HOME": str(plan.paths.state_root),
        "AGENT_PBX_DB": str(plan.paths.db_path),
        "AGENT_PBX_HOST": "127.0.0.1",
        "AGENT_PBX_PORT": str(plan.port),
        "AGENT_PBX_SERVER_URL": plan.server_url,
        "AGENT_PBX_TOKEN": plan.token,
        "AGENT_PBX_RUNTIME_SERVER_MODE": "dedicated",
        "AGENT_PBX_TMUX_SOCKET": str(plan.paths.tmux_socket),
        "AGENT_PBX_HARNESS": "1",
        "AGENT_PBX_HARNESS_RUN_ID": plan.run_id,
        "AGENT_PBX_HARNESS_PROJECT": plan.project,
        "CODEX_HOME": str(plan.paths.home / ".codex"),
        "PYTHONUNBUFFERED": "1",
        "TERM": os.getenv("TERM", "xterm-256color"),
        "LANG": os.getenv("LANG", "C.UTF-8"),
    }
    return env


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    original = os.environ.copy()
    os.environ.clear()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def _write_fixture_commands(plan: HarnessPlan) -> None:
    scripts = {
        "codex": "#!/bin/sh\nprintf '%s\\n' 'codex-cli 0.0.0-harness'\n",
        "joplin": "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"fixture\":\"joplin\"}'\n",
        "workerbee": "#!/bin/sh\nprintf '%s\\n' '{\"ok\":true,\"fixture\":\"workerbee\"}'\n",
        "wb-mcp-status": (
            "#!/bin/sh\n"
            "printf '%s\\n' '{\"config\":{\"api_version\":\"workerbee.mcp_switch_config/v1\","
            "\"path\":\"fixture\"},\"status\":{\"api_version\":"
            "\"workerbee.mcp_switch_status/v1\",\"backend\":\"fixture\","
            "\"mcp_endpoint\":\"fixture://workerbee\",\"mcp\":{\"status\":"
            "\"ready\",\"server_info\":{\"name\":\"WorkerBee fixture\","
            "\"version\":\"0.1.7\"}}}}'\n"
        ),
    }
    for name, body in scripts.items():
        path = plan.paths.bin_dir / name
        _write_private(path, body)
        path.chmod(0o700)


def materialize_harness(plan: HarnessPlan) -> HarnessManifest:
    plan.paths.run_root.mkdir(parents=True, mode=0o700)
    plan.paths.run_root.chmod(0o700)
    for path in (
        plan.paths.home,
        plan.paths.state_root,
        plan.paths.config_home,
        plan.paths.data_home,
        plan.paths.cache_home,
        plan.paths.temp_dir,
        plan.paths.artifacts,
        plan.paths.bin_dir,
        plan.paths.tmux_socket.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    if plan.workerbee_backend == "fixture":
        _write_fixture_commands(plan)
    _write_private(
        plan.paths.run_root / "harness.env",
        "\n".join(f"{key}={value}" for key, value in harness_environment(plan).items()) + "\n",
    )
    manifest = HarnessManifest(plan=plan)
    write_harness_manifest(manifest)
    return manifest


def _daemon_config(plan: HarnessPlan) -> MCPDaemonConfig:
    return MCPDaemonConfig(
        state_root=plan.paths.state_root,
        host="127.0.0.1",
        port=plan.port,
        db_path=plan.paths.db_path,
        token=plan.token,
        workerbee_bin=(plan.paths.bin_dir / "workerbee")
        if plan.workerbee_backend == "fixture"
        else None,
    )


def _process_start_ticks(pid: int | None) -> int | None:
    if not pid or pid <= 0:
        return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def start_harness(
    plan: HarnessPlan,
    *,
    start_tmux: bool = True,
    allow_platform_debug: bool = False,
    workerbee_workspace_path: Path | None = None,
    daemon_starter: Callable[..., dict[str, Any]] = start_mcp_daemon,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> HarnessManifest:
    manifest = materialize_harness(plan)
    env = harness_environment(plan)
    try:
        manifest.workerbee = prepare_workerbee_harness_binding(
            backend=plan.workerbee_backend,
            project=plan.project,
            source_root=plan.source_root,
            source_commit=plan.source_commit,
            source_dirty=plan.source_dirty,
            artifacts_root=plan.paths.artifacts,
            allow_platform_debug=allow_platform_debug,
            workerbee_workspace_path=workerbee_workspace_path,
        ).public_dict()
        write_harness_manifest(manifest)
        if start_tmux:
            if not shutil.which(plan.tmux_bin):
                raise RuntimeError(f"tmux executable is unavailable: {plan.tmux_bin}")
            command = [
                plan.tmux_bin,
                "-S",
                str(plan.paths.tmux_socket),
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "harness",
            ]
            result = runner(command, capture_output=True, text=True, env=env, timeout=10)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "isolated tmux failed to start")
            manifest.tmux = {
                "socket": str(plan.paths.tmux_socket),
                "session": "harness",
                "command": command,
                "running": True,
            }
        with _temporary_environment(env):
            daemon = daemon_starter(_daemon_config(plan), timeout=30.0)
        if daemon.get("ok") is False or not daemon.get("running"):
            raise RuntimeError(str(daemon.get("error") or "isolated daemon failed to start"))
        manifest.daemon = {
            key: daemon.get(key)
            for key in ("pid", "running", "health_url", "mcp_url", "metadata_file", "log_file")
        }
        manifest.daemon["process_start_ticks"] = _process_start_ticks(
            int(daemon["pid"]) if daemon.get("pid") else None
        )
        manifest.status = "running"
        write_harness_manifest(manifest)
        return manifest
    except Exception:
        manifest.status = "start_failed"
        write_harness_manifest(manifest)
        stop_harness(manifest, runner=runner)
        raise


def harness_status(
    manifest: HarnessManifest,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    plan = manifest.plan
    with _temporary_environment(harness_environment(plan)):
        daemon = mcp_daemon_status(_daemon_config(plan))
    tmux_running = False
    if manifest.tmux:
        result = runner(
            [plan.tmux_bin, "-S", str(plan.paths.tmux_socket), "has-session", "-t", "harness"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        tmux_running = result.returncode == 0
    return {
        "api_version": HARNESS_API_VERSION,
        "run_id": plan.run_id,
        "status": manifest.status,
        "daemon_running": bool(daemon.get("running")),
        "daemon_pid": daemon.get("pid"),
        "tmux_running": tmux_running,
        "server_url": plan.server_url,
        "manifest": str(plan.paths.manifest),
    }


def run_in_harness(
    manifest: HarnessManifest,
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not command:
        raise ValueError("harness run requires a command")
    started = time.time()
    result = runner(
        list(command),
        cwd=manifest.plan.source_root,
        env=harness_environment(manifest.plan),
        text=True,
    )
    record = {
        "argv": list(command),
        "started_at": started,
        "finished_at": time.time(),
        "returncode": int(result.returncode),
    }
    manifest.runs.append(record)
    write_harness_manifest(manifest)
    return record


def stop_harness(
    manifest: HarnessManifest,
    *,
    daemon_stopper: Callable[..., dict[str, Any]] = stop_mcp_daemon,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> HarnessManifest:
    plan = manifest.plan
    errors: list[str] = []
    try:
        with _temporary_environment(harness_environment(plan)):
            daemon_stopper(_daemon_config(plan), timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - cleanup records all failures
        errors.append(f"daemon: {type(exc).__name__}")
    if manifest.tmux:
        try:
            runner(
                [plan.tmux_bin, "-S", str(plan.paths.tmux_socket), "kill-server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tmux: {type(exc).__name__}")
    manifest.status = "cleanup_failed" if errors else "stopped"
    manifest.cleanup = {"at": time.time(), "errors": errors}
    write_harness_manifest(manifest)
    return manifest


def purge_harness(manifest: HarnessManifest, *, force: bool = False) -> Path:
    root = manifest.plan.paths.run_root.resolve()
    base = manifest.plan.harness_root.resolve()
    if not _path_contains(base, root) and not force:
        raise ValueError("refusing to purge a harness outside the configured harness root")
    if root.parent != base or root.name != manifest.plan.run_id:
        raise ValueError("harness manifest does not match its task-owned run directory")
    if manifest.status not in {"stopped", "start_failed", "cleanup_failed"}:
        raise ValueError("stop the harness before purging it")
    shutil.rmtree(root)
    return root


def resolve_harness_manifest(
    *, harness_root: Path | None,
    run_id: str,
) -> Path:
    return (harness_root or default_harness_root()).expanduser().resolve() / _safe_id(run_id) / HARNESS_MANIFEST


def render_harness(value: HarnessPlan | HarnessManifest | dict[str, Any]) -> str:
    if isinstance(value, HarnessPlan):
        payload = value.public_dict()
    elif isinstance(value, HarnessManifest):
        payload = value.public_dict()
    else:
        payload = value
    return json.dumps(payload, indent=2, sort_keys=True)
