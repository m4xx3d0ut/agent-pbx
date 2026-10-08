from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Callable, Mapping


TMUX_BIN_ENV = "AGENT_PBX_TMUX_BIN"


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
