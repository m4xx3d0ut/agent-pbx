from __future__ import annotations

from pathlib import Path

from agent_pbx.tmux_binary import configured_tmux_binary, resolve_tmux_binary


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
