from __future__ import annotations

from pathlib import Path

import pytest

from agent_pbx.tmux_binary import (
    TMUX_BIN_ENV,
    configured_tmux_binary,
    ensure_managed_tmux_shim,
    managed_tmux_launch_environment,
    managed_tmux_shim_status,
    resolve_tmux_binary,
)


def test_tmux_binary_environment_replaces_only_default() -> None:
    env = {"AGENT_PBX_TMUX_BIN": "/opt/pbx/tmux"}

    assert configured_tmux_binary(env=env) == "/opt/pbx/tmux"
    assert configured_tmux_binary("tmux", env=env) == "/opt/pbx/tmux"
    assert configured_tmux_binary("tmux-test", env=env) == "tmux-test"


def test_tmux_binary_expands_executable_path(tmp_path: Path) -> None:
    executable = tmp_path / "tmux"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)

    assert configured_tmux_binary(env={"AGENT_PBX_TMUX_BIN": "~/bin/tmux"}) == str(
        Path.home() / "bin/tmux"
    )
    assert resolve_tmux_binary(env={"AGENT_PBX_TMUX_BIN": str(executable)}) == str(
        executable.resolve()
    )


def test_tmux_binary_named_command_uses_injected_lookup() -> None:
    assert resolve_tmux_binary(
        env={"AGENT_PBX_TMUX_BIN": "qualified-tmux"},
        which=lambda command: f"/resolved/{command}",
    ) == "/resolved/qualified-tmux"


def test_managed_tmux_shim_is_private_atomic_and_prepended(tmp_path: Path) -> None:
    target = tmp_path / "tmux-3.7c"
    target.write_text("#!/bin/sh\necho tmux 3.7c\n", encoding="utf-8")
    target.chmod(0o755)
    state_root = tmp_path / "state"
    inherited = {"PATH": "/usr/bin:/bin", TMUX_BIN_ENV: str(target)}

    shim = ensure_managed_tmux_shim(state_root=state_root, env=inherited)
    launch = managed_tmux_launch_environment(
        {"PBX": "1"},
        state_root=state_root,
        inherited_env=inherited,
    )
    status = managed_tmux_shim_status(state_root=state_root, env=inherited)

    assert shim.is_symlink()
    assert shim.resolve() == target.resolve()
    assert shim.parent.stat().st_mode & 0o777 == 0o700
    assert launch["PATH"].split(":", 1)[0] == str(shim.parent)
    assert launch[TMUX_BIN_ENV] == str(target.resolve())
    assert launch["PBX"] == "1"
    assert status["ready"] is True


def test_managed_tmux_shim_rejects_writable_target(tmp_path: Path) -> None:
    target = tmp_path / "tmux-unsafe"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o777)

    try:
        with pytest.raises(RuntimeError, match="group/world writable"):
            ensure_managed_tmux_shim(
                state_root=tmp_path / "state",
                env={TMUX_BIN_ENV: str(target)},
            )
    finally:
        target.chmod(0o700)
