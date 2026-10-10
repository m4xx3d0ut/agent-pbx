from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_pbx.cli import build_parser
from agent_pbx.setup_assistant import (
    apply_setup,
    build_setup_plan,
    resume_setup,
    rollback_setup,
    setup_state_path,
)


def available_command(name: str) -> str:
    return f"/usr/bin/{name}"


def test_setup_plan_uses_localhost_and_scoped_git_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    git_root = home / "git"
    git_root.mkdir(parents=True)
    config = home / ".config" / "agent-pbx" / "local.env"

    plan = build_setup_plan(
        config_path=config,
        environ={"HOME": str(home)},
        which=available_command,
        platform_name="Linux",
    )

    assert plan.ready is True
    assert plan.desired_values["AGENT_PBX_HOST"] == "127.0.0.1"
    assert plan.desired_values["AGENT_PBX_PROJECT_ROOTS"] == str(git_root)
    assert plan.desired_values["AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE"] == "dedicated"
    assert plan.dependencies["codex"] == "/usr/bin/codex"


def test_setup_plan_requires_existing_roots_and_tools(tmp_path: Path) -> None:
    plan = build_setup_plan(
        config_path=tmp_path / "local.env",
        project_roots=[tmp_path / "missing"],
        environ={"HOME": str(tmp_path)},
        which=lambda _name: None,
    )

    assert plan.ready is False
    assert len(plan.warnings) == 2
    with pytest.raises(ValueError, match="not ready"):
        apply_setup(plan)


def test_setup_apply_is_private_idempotent_and_preserves_unrelated_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "git"
    root.mkdir()
    config = tmp_path / "config" / "local.env"
    config.parent.mkdir()
    config.write_text(
        "# private settings\n"
        "export AGENT_PBX_TOKEN='keep-secret'\n"
        "export UNRELATED=value\n"
        "export AGENT_PBX_PROJECT_ROOTS='$HOME/git'\n"
        "export AGENT_PBX_TUI_THEME=minimal\n",
        encoding="utf-8",
    )
    plan = build_setup_plan(
        config_path=config,
        project_roots=[root],
        runtime_server_mode="outer_if_present",
        theme="cyberpunk",
        environ={"HOME": str(tmp_path)},
        which=available_command,
    )

    result = apply_setup(plan)
    content = config.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert "AGENT_PBX_TOKEN='keep-secret'" in content
    assert "export UNRELATED=value" in content
    assert "export AGENT_PBX_PROJECT_ROOTS='$HOME/git'" in content
    assert "export AGENT_PBX_TUI_THEME=cyberpunk" in content
    assert "export AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE=outer_if_present" in content
    assert config.stat().st_mode & 0o777 == 0o600
    assert setup_state_path(config).stat().st_mode & 0o777 == 0o600

    repeated = apply_setup(
        build_setup_plan(
            config_path=config,
            project_roots=[root],
            runtime_server_mode="outer_if_present",
            theme="cyberpunk",
            environ={"HOME": str(tmp_path)},
            which=available_command,
        )
    )
    assert repeated["status"] == "unchanged"
    assert repeated["changed"] is False


def test_setup_rollback_restores_exact_prior_config(tmp_path: Path) -> None:
    root = tmp_path / "git"
    root.mkdir()
    config = tmp_path / "local.env"
    original = b"# original\nexport PRIVATE_VALUE='untouched'\n"
    config.write_bytes(original)
    plan = build_setup_plan(
        config_path=config,
        project_roots=[root],
        which=available_command,
    )
    apply_setup(plan)

    result = rollback_setup(config)

    assert result["status"] == "rolled_back"
    assert config.read_bytes() == original


def test_setup_rollback_refuses_to_overwrite_post_apply_edits(tmp_path: Path) -> None:
    root = tmp_path / "git"
    root.mkdir()
    config = tmp_path / "local.env"
    plan = build_setup_plan(
        config_path=config,
        project_roots=[root],
        which=available_command,
    )
    apply_setup(plan)
    config.write_text(config.read_text(encoding="utf-8") + "USER_EDIT=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        rollback_setup(config)


def test_setup_resume_completes_interrupted_transaction(tmp_path: Path) -> None:
    root = tmp_path / "git"
    root.mkdir()
    config = tmp_path / "local.env"
    original = "ORIGINAL=value\n"
    config.write_text(original, encoding="utf-8")
    plan = build_setup_plan(
        config_path=config,
        project_roots=[root],
        which=available_command,
    )
    applied = apply_setup(plan)
    expected = config.read_text(encoding="utf-8")
    state_path = setup_state_path(config)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "applying"
    state.pop("completed_at", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(state_path, 0o600)
    config.write_text(original, encoding="utf-8")

    resumed = resume_setup(config)

    assert resumed["status"] == "complete"
    assert resumed["after_sha256"] == applied["after_sha256"]
    assert config.read_text(encoding="utf-8") == expected


def test_setup_cli_parser_exposes_transaction_commands() -> None:
    args = build_parser().parse_args(
        [
            "setup",
            "apply",
            "--project-root",
            "/srv/git",
            "--runtime-server-mode",
            "dedicated",
            "--json",
        ]
    )

    assert args.command == "setup"
    assert args.setup_command == "apply"
    assert args.project_root == [Path("/srv/git")]
    assert args.runtime_server_mode == "dedicated"
    assert args.json is True
