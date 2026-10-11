from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
from typing import Callable, Mapping
import uuid

from .paths import default_state_root


TMUX_BIN_ENV = "AGENT_PBX_TMUX_BIN"
MANAGED_TMUX_BIN_DIRECTORY = "runtime-bin"
MANAGED_TMUX_COMMAND = "tmux"


def configured_tmux_binary(
    explicit: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Select one tmux command for Agent PBX daemon and TUI operations.

    Explicit non-default commands remain authoritative for tests and CLI
    callers. The environment override replaces only the conventional ``tmux``
    default, allowing a qualified user-scoped binary without replacing the
    host package.
    """

    environ = os.environ if env is None else env
    requested = os.fspath(explicit).strip() if explicit is not None else ""
    configured = str(environ.get(TMUX_BIN_ENV) or "").strip()
    if configured and requested in {"", "tmux"}:
        return str(Path(configured).expanduser()) if "/" in configured else configured
    return requested or "tmux"


def resolve_tmux_binary(
    explicit: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    """Resolve the selected command to an executable path when available."""

    command = configured_tmux_binary(explicit, env=env)
    if "/" in command:
        path = Path(command).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve(strict=False))
        return None
    return (which or shutil.which)(command)


def managed_tmux_shim_path(*, state_root: Path | None = None) -> Path:
    root = (state_root or default_state_root()).expanduser().resolve(strict=False)
    return root / MANAGED_TMUX_BIN_DIRECTORY / MANAGED_TMUX_COMMAND


def ensure_managed_tmux_shim(
    tmux_bin: str | os.PathLike[str] | None = None,
    *,
    state_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> Path:
    """Install a private ``tmux`` command that pins managed panes to one binary."""

    resolved = resolve_tmux_binary(tmux_bin, env=env, which=which)
    if not resolved:
        requested = configured_tmux_binary(tmux_bin, env=env)
        raise RuntimeError(f"configured tmux executable is unavailable: {requested}")
    target = Path(resolved).expanduser().resolve(strict=True)
    target_stat = target.stat()
    if not stat.S_ISREG(target_stat.st_mode) or not os.access(target, os.X_OK):
        raise RuntimeError(f"configured tmux target is not executable: {target}")
    allowed_owners = {0}
    if hasattr(os, "getuid"):
        allowed_owners.add(os.getuid())
    if target_stat.st_uid not in allowed_owners:
        raise RuntimeError(f"configured tmux target has an untrusted owner: {target}")
    if target_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError(f"configured tmux target is group/world writable: {target}")

    shim = managed_tmux_shim_path(state_root=state_root)
    directory = shim.parent
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise RuntimeError(f"managed tmux bin path is not a private directory: {directory}")
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory_stat = directory.stat()
    if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
        raise RuntimeError(f"managed tmux bin directory has a different owner: {directory}")
    if directory_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        directory.chmod(0o700)

    if shim.is_symlink() and shim.resolve(strict=False) == target:
        return shim
    if shim.exists() and not shim.is_symlink():
        raise RuntimeError(f"managed tmux shim is not a symlink: {shim}")
    temporary = directory / f".{MANAGED_TMUX_COMMAND}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.symlink_to(target)
        os.replace(temporary, shim)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return shim


def managed_tmux_shim_status(
    tmux_bin: str | os.PathLike[str] | None = None,
    *,
    state_root: Path | None = None,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, str | bool | None]:
    """Inspect the managed command without creating or replacing it."""

    shim = managed_tmux_shim_path(state_root=state_root)
    target = resolve_tmux_binary(tmux_bin, env=env, which=which)
    if target is None:
        return {
            "ready": False,
            "state": "target_unavailable",
            "path": str(shim),
            "target": None,
        }
    resolved_target = str(Path(target).expanduser().resolve(strict=False))
    if not shim.is_symlink():
        return {
            "ready": False,
            "state": "missing" if not shim.exists() else "invalid",
            "path": str(shim),
            "target": resolved_target,
        }
    shim_target = str(shim.resolve(strict=False))
    return {
        "ready": shim_target == resolved_target and os.access(shim, os.X_OK),
        "state": "ready" if shim_target == resolved_target else "stale",
        "path": str(shim),
        "target": resolved_target,
        "shim_target": shim_target,
    }


def managed_tmux_launch_environment(
    launch_env: Mapping[str, str],
    tmux_bin: str | os.PathLike[str] | None = None,
    *,
    state_root: Path | None = None,
    inherited_env: Mapping[str, str] | None = None,
    install: bool = True,
) -> dict[str, str]:
    """Pin plain ``tmux`` commands inside a PBX-managed runtime pane."""

    inherited = os.environ if inherited_env is None else inherited_env
    target = resolve_tmux_binary(tmux_bin, env=inherited)
    if not target:
        requested = configured_tmux_binary(tmux_bin, env=inherited)
        raise RuntimeError(f"configured tmux executable is unavailable: {requested}")
    shim = managed_tmux_shim_path(state_root=state_root)
    if install:
        shim = ensure_managed_tmux_shim(
            tmux_bin,
            state_root=state_root,
            env=inherited,
        )
    else:
        status = managed_tmux_shim_status(
            tmux_bin,
            state_root=state_root,
            env=inherited,
        )
        if not bool(status["ready"]):
            return {**launch_env, TMUX_BIN_ENV: str(Path(target).resolve(strict=False))}

    result = {str(key): str(value) for key, value in launch_env.items()}
    current_path = result.get("PATH") or str(inherited.get("PATH") or os.defpath)
    path_entries = [item for item in current_path.split(os.pathsep) if item]
    private_bin = str(shim.parent)
    result["PATH"] = os.pathsep.join(
        [private_bin, *(item for item in path_entries if item != private_bin)]
    )
    result[TMUX_BIN_ENV] = str(Path(target).resolve(strict=False))
    return result
