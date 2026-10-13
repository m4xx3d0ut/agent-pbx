from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
from typing import Any, Callable, Iterable, Mapping
import uuid

from .envfile import default_user_env_path, parse_env_file, parse_env_line


SETUP_API_VERSION = "agent-pbx.setup/v1"
SETUP_STATE_SUFFIX = ".setup-state.json"
LOCAL_WORKSTATION_COMMANDS = ("git", "tmux", "codex")


@dataclass(frozen=True)
class SetupPlan:
    config_path: str
    platform: str
    ready: bool
    desired_values: dict[str, str]
    changes: dict[str, dict[str, str | None]]
    dependencies: dict[str, str | None]
    warnings: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {"api_version": SETUP_API_VERSION, **asdict(self)}


def build_setup_plan(
    *,
    config_path: Path | None = None,
    project_roots: Iterable[Path] | None = None,
    runtime_server_mode: str | None = None,
    theme: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    platform_name: str | None = None,
) -> SetupPlan:
    env = dict(os.environ if environ is None else environ)
    path = (config_path or default_user_env_path()).expanduser().resolve(strict=False)
    existing = parse_env_file(path, base_env=env)
    warnings: list[str] = []

    configured_roots = tuple(project_roots or ())
    if configured_roots:
        roots = tuple(root.expanduser().resolve(strict=False) for root in configured_roots)
    elif str(existing.get("AGENT_PBX_PROJECT_ROOTS") or "").strip():
        roots = tuple(
            Path(item).expanduser().resolve(strict=False)
            for item in existing["AGENT_PBX_PROJECT_ROOTS"].split(os.pathsep)
            if item.strip()
        )
    else:
        default_root = Path(env.get("HOME") or Path.home()).expanduser() / "git"
        roots = (default_root.resolve(strict=False),)
    missing_roots = [str(root) for root in roots if not root.is_dir()]
    if missing_roots:
        warnings.append(
            "Project roots must exist before setup is applied: " + ", ".join(missing_roots)
        )

    host = str(existing.get("AGENT_PBX_HOST") or "127.0.0.1")
    port = str(existing.get("AGENT_PBX_PORT") or "8765")
    server_url = str(
        existing.get("AGENT_PBX_SERVER_URL") or f"http://{host}:{port}"
    ).rstrip("/")
    desired = {
        "AGENT_PBX_HOST": host,
        "AGENT_PBX_PORT": port,
        "AGENT_PBX_SERVER_URL": server_url,
        "AGENT_PBX_MCP_URL": str(
            existing.get("AGENT_PBX_MCP_URL") or f"{server_url}/mcp"
        ),
        "AGENT_PBX_PROJECT_ROOTS": os.pathsep.join(str(root) for root in roots),
        "AGENT_PBX_TUI_EVENT_STREAM_V2": str(
            existing.get("AGENT_PBX_TUI_EVENT_STREAM_V2") or "1"
        ),
        "AGENT_PBX_TUI_TMUX": str(existing.get("AGENT_PBX_TUI_TMUX") or "1"),
        "AGENT_PBX_TUI_EMBEDDED_TERMINAL_V2": str(
            existing.get("AGENT_PBX_TUI_EMBEDDED_TERMINAL_V2") or "1"
        ),
        "AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE": (
            runtime_server_mode
            or str(existing.get("AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE") or "dedicated")
        ),
        "AGENT_PBX_TUI_THEME": theme or str(existing.get("AGENT_PBX_TUI_THEME") or "cyberpunk"),
    }
    dependencies = {
        command: str(found) if (found := which(command)) else None
        for command in LOCAL_WORKSTATION_COMMANDS
    }
    missing_commands = [name for name, value in dependencies.items() if value is None]
    if missing_commands:
        warnings.append(
            "Install missing local-workstation commands before apply: "
            + ", ".join(missing_commands)
        )
    changes = {
        key: {"before": existing.get(key), "after": value}
        for key, value in desired.items()
        if existing.get(key) != value
    }
    return SetupPlan(
        config_path=str(path),
        platform=platform_name or platform.system(),
        ready=not warnings,
        desired_values=desired,
        changes=changes,
        dependencies=dependencies,
        warnings=tuple(warnings),
    )


def render_setup_plan(plan: SetupPlan) -> str:
    lines = [
        "# Agent PBX local setup",
        "",
        f"- Config: `{plan.config_path}`",
        f"- Platform: `{plan.platform}`",
        f"- Ready: `{'yes' if plan.ready else 'no'}`",
        "",
        "## Dependencies",
        "",
    ]
    lines.extend(
        f"- `{name}`: `{value or 'missing'}`"
        for name, value in plan.dependencies.items()
    )
    lines.extend(["", "## Planned configuration", ""])
    if plan.changes:
        lines.extend(
            f"- `{key}`: `{change['before'] if change['before'] is not None else '<unset>'}` → "
            f"`{change['after']}`"
            for key, change in plan.changes.items()
        )
    else:
        lines.append("- No changes; configuration is already aligned.")
    if plan.warnings:
        lines.extend(["", "## Required action", ""])
        lines.extend(f"- {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def apply_setup(plan: SetupPlan) -> dict[str, Any]:
    if not plan.ready:
        raise ValueError("setup plan is not ready; resolve its required actions first")
    config_path = Path(plan.config_path)
    before_exists = config_path.exists()
    before = config_path.read_bytes() if before_exists else b""
    changed_values = {
        key: plan.desired_values[key]
        for key in plan.changes
    }
    after_text = merge_env_text(
        before.decode("utf-8") if before_exists else "",
        changed_values,
    )
    after = after_text.encode("utf-8")
    before_hash = _sha256(before)
    after_hash = _sha256(after)
    if before_exists and before_hash == after_hash:
        return {
            "api_version": SETUP_API_VERSION,
            "status": "unchanged",
            "changed": False,
            "config_path": str(config_path),
            "state_path": str(setup_state_path(config_path)),
            "after_sha256": after_hash,
        }

    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir = config_path.parent / "setup-backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{stamp}-{uuid.uuid4().hex[:8]}-{config_path.name}"
    if before_exists:
        _atomic_write_bytes(backup_path, before)
    state = {
        "api_version": SETUP_API_VERSION,
        "status": "applying",
        "config_path": str(config_path),
        "before_exists": before_exists,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "backup_path": str(backup_path) if before_exists else None,
        "desired_values": changed_values,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_setup_state(config_path, state)
    _atomic_write_bytes(config_path, after)
    state["status"] = "complete"
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_setup_state(config_path, state)
    return {**state, "changed": True, "state_path": str(setup_state_path(config_path))}


def resume_setup(config_path: Path | None = None) -> dict[str, Any]:
    path = (config_path or default_user_env_path()).expanduser().resolve(strict=False)
    state = _read_setup_state(path)
    if state.get("status") == "complete":
        return {**state, "changed": False, "state_path": str(setup_state_path(path))}
    if state.get("status") != "applying":
        raise ValueError("no interrupted setup transaction is available to resume")
    current_exists = path.exists()
    current = path.read_bytes() if current_exists else b""
    current_hash = _sha256(current)
    before_hash = str(state.get("before_sha256") or "")
    after_hash = str(state.get("after_sha256") or "")
    if current_hash == after_hash:
        state["status"] = "complete"
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        _write_setup_state(path, state)
        return {**state, "changed": False, "state_path": str(setup_state_path(path))}
    if current_hash != before_hash:
        raise ValueError("setup target changed after interruption; review it before resuming")
    desired = state.get("desired_values")
    if not isinstance(desired, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in desired.items()
    ):
        raise ValueError("setup transaction has invalid desired configuration")
    after = merge_env_text(current.decode("utf-8"), desired).encode("utf-8")
    if _sha256(after) != after_hash:
        raise ValueError("setup resume output differs from the recorded transaction")
    _atomic_write_bytes(path, after)
    state["status"] = "complete"
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_setup_state(path, state)
    return {**state, "changed": True, "state_path": str(setup_state_path(path))}


def rollback_setup(config_path: Path | None = None) -> dict[str, Any]:
    path = (config_path or default_user_env_path()).expanduser().resolve(strict=False)
    state = _read_setup_state(path)
    if state.get("status") != "complete":
        raise ValueError("only a completed setup transaction can be rolled back")
    current = path.read_bytes() if path.exists() else b""
    if _sha256(current) != str(state.get("after_sha256") or ""):
        raise ValueError("setup target changed after apply; refusing to overwrite user edits")
    if bool(state.get("before_exists")):
        backup_value = state.get("backup_path")
        backup_path = Path(str(backup_value)) if backup_value else None
        if backup_path is None or not backup_path.is_file():
            raise ValueError("setup backup is missing")
        before = backup_path.read_bytes()
        if _sha256(before) != str(state.get("before_sha256") or ""):
            raise ValueError("setup backup checksum does not match the transaction")
        _atomic_write_bytes(path, before)
    else:
        path.unlink(missing_ok=True)
    state["status"] = "rolled_back"
    state["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _write_setup_state(path, state)
    return {**state, "changed": True, "state_path": str(setup_state_path(path))}


def merge_env_text(source: str, desired_values: Mapping[str, str]) -> str:
    remaining = dict(desired_values)
    rendered: list[str] = []
    emitted: set[str] = set()
    for line in source.splitlines():
        parsed = parse_env_line(line)
        key = parsed[0] if parsed is not None else None
        if key not in desired_values:
            rendered.append(line)
            continue
        if key in emitted:
            continue
        rendered.append(f"export {key}={shlex.quote(remaining.pop(key))}")
        emitted.add(key)
    if remaining:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append("# Managed by agent-pbx setup; reruns update only these known keys.")
        rendered.extend(
            f"export {key}={shlex.quote(remaining[key])}" for key in sorted(remaining)
        )
    return "\n".join(rendered).rstrip() + "\n"


def setup_state_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.name + SETUP_STATE_SUFFIX)


def _read_setup_state(config_path: Path) -> dict[str, Any]:
    path = setup_state_path(config_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("setup transaction state does not exist") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("setup transaction state is invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("api_version") != SETUP_API_VERSION:
        raise ValueError("setup transaction state has an unsupported format")
    if str(payload.get("config_path") or "") != str(config_path):
        raise ValueError("setup transaction targets a different config path")
    return payload


def _write_setup_state(config_path: Path, state: Mapping[str, Any]) -> None:
    _atomic_write_bytes(
        setup_state_path(config_path),
        (json.dumps(dict(state), indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
