from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_pbx.cli import build_parser
from agent_pbx import dev_harness


def make_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "README.md").write_text("harness\n", encoding="utf-8")
    return source


def make_plan(monkeypatch, tmp_path: Path, *, run_id: str = "run-1"):
    source = make_source(tmp_path)
    production = tmp_path / "production-state"
    monkeypatch.setattr(dev_harness, "default_state_root", lambda: production)
    return dev_harness.build_harness_plan(
        source_root=source,
        harness_root=tmp_path / "harness",
        run_id=run_id,
        port=28765,
        tmux_bin=str(shutil.which("true") or "/usr/bin/true"),
    )


def test_harness_plan_is_private_scoped_and_redacts_token(monkeypatch, tmp_path) -> None:
    plan = make_plan(monkeypatch, tmp_path)

    assert plan.project.startswith(dev_harness.HARNESS_PROJECT_PREFIX)
    assert plan.paths.run_root == tmp_path / "harness" / "run-1"
    assert plan.server_url == "http://127.0.0.1:28765"
    assert plan.token not in dev_harness.render_harness(plan)

    manifest = dev_harness.materialize_harness(plan)
    assert manifest.plan.paths.manifest.stat().st_mode & 0o777 == 0o600
    assert manifest.plan.paths.run_root.stat().st_mode & 0o777 == 0o700
    payload = json.loads(manifest.plan.paths.manifest.read_text(encoding="utf-8"))
    assert payload["plan"]["token"] == plan.token
    assert Path(payload["plan"]["harness_root"]) == tmp_path / "harness"


def test_harness_rejects_production_state_collision(monkeypatch, tmp_path) -> None:
    source = make_source(tmp_path)
    production = tmp_path / "production"
    monkeypatch.setattr(dev_harness, "default_state_root", lambda: production)

    with pytest.raises(ValueError, match="production Agent PBX state"):
        dev_harness.build_harness_plan(
            source_root=source,
            harness_root=production / "nested",
            run_id="unsafe",
        )


def test_harness_start_stop_and_purge_use_only_owned_resources(
    monkeypatch, tmp_path
) -> None:
    plan = make_plan(monkeypatch, tmp_path)
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    manifest = dev_harness.start_harness(
        plan,
        runner=runner,
        daemon_starter=lambda _config, **_kwargs: {
            "ok": True,
            "running": True,
            "pid": 999999,
            "health_url": f"{plan.server_url}/healthz",
        },
    )
    assert manifest.status == "running"
    assert str(plan.paths.tmux_socket) in commands[0]
    assert manifest.daemon["process_start_ticks"] is None
    assert manifest.workerbee["status"] == "fixture_ready"

    stopped = dev_harness.stop_harness(
        manifest,
        runner=runner,
        daemon_stopper=lambda _config, **_kwargs: {"stopped": True},
    )
    assert stopped.status == "stopped"
    assert commands[-1][-1] == "kill-server"

    removed = dev_harness.purge_harness(stopped)
    assert removed == plan.paths.run_root
    assert not removed.exists()


def test_harness_environment_does_not_inherit_production_integration_paths(
    monkeypatch, tmp_path
) -> None:
    plan = make_plan(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_PBX_JOPLIN_PROFILE", "/production/joplin")
    monkeypatch.setenv("AGENT_PBX_SERVER_URL", "http://production:8765")

    env = dev_harness.harness_environment(plan)

    assert env["HOME"] == str(plan.paths.home)
    assert env["AGENT_PBX_SERVER_URL"] == plan.server_url
    assert "AGENT_PBX_JOPLIN_PROFILE" not in env
    assert env["AGENT_PBX_RUNTIME_SERVER_MODE"] == "dedicated"


def test_run_in_harness_records_exact_argv_and_isolated_environment(
    monkeypatch, tmp_path
) -> None:
    plan = make_plan(monkeypatch, tmp_path)
    manifest = dev_harness.materialize_harness(plan)
    observed: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    result = dev_harness.run_in_harness(
        manifest,
        ["python", "-m", "pytest", "tests/test_health.py"],
        runner=runner,
    )

    assert result["returncode"] == 0
    assert observed["command"] == ["python", "-m", "pytest", "tests/test_health.py"]
    assert observed["cwd"] == plan.source_root
    assert observed["env"]["AGENT_PBX_HARNESS_RUN_ID"] == "run-1"
    loaded = dev_harness.load_harness_manifest(plan.paths.manifest)
    assert loaded.runs[-1]["argv"][-1] == "tests/test_health.py"


def test_harness_cli_exposes_checkpoint_commands() -> None:
    parser = build_parser()

    start = parser.parse_args(
        [
            "harness",
            "start",
            "--run-id",
            "uat-1",
            "--source-root",
            "/tmp/source",
            "--no-tmux",
        ]
    )
    run = parser.parse_args(
        ["harness", "run", "--run-id", "uat-1", "--", "pytest", "-q"]
    )

    assert start.harness_command == "start"
    assert start.no_tmux is True
    assert run.argv == ["--", "pytest", "-q"]
