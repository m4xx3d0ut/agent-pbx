from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_pbx.cli import _daemon_config, build_parser, main
from agent_pbx.mcp_daemon import (
    MCPDaemonConfig,
    config_from_args,
    mcp_daemon_status,
    restart_mcp_daemon,
    start_mcp_daemon,
    stop_mcp_daemon,
    _pid_matches_metadata,
)
from agent_pbx.paths import default_state_root


REMOTE_BIND_HOST = "0.0.0.0"  # noqa: S104 - explicit LAN bind fixture


def test_default_state_root_uses_xdg_data_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_PBX_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    config = config_from_args()

    assert default_state_root() == tmp_path / "agent-pbx"
    assert config.resolved_state_root == tmp_path / "agent-pbx"
    assert config.resolved_db_path == tmp_path / "agent-pbx" / "agent-pbx.sqlite"


def test_mcp_daemon_status_reports_stale_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    config.global_dir.mkdir(parents=True)
    config.metadata_file.write_text(
        json.dumps({"pid": 99999, "state_root": str(tmp_path.resolve())}),
        encoding="utf-8",
    )
    monkeypatch.setattr("agent_pbx.mcp_daemon._pid_alive", lambda _pid: False)

    status = mcp_daemon_status(config)

    assert status["running"] is False
    assert status["stale"] is True
    assert status["mcp_url"] == "http://127.0.0.1:9876/mcp"


def test_pid_match_rejects_unreadable_proc_cmdline_on_linux(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)

    def fail_read_bytes(_path: Path) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr("agent_pbx.mcp_daemon.Path.read_bytes", fail_read_bytes)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon.Path.exists",
        lambda path: str(path) == "/proc",
    )

    assert (
        _pid_matches_metadata(
            4321,
            config,
            {"state_root": str(config.resolved_state_root)},
        )
        is False
    )


def test_start_mcp_daemon_writes_detached_agent_pbx_argv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, Any] = {}

    class FakePopen:
        pid = 4321

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            calls["argv"] = argv
            calls["kwargs"] = kwargs

    config = MCPDaemonConfig(
        state_root=tmp_path,
        port=9876,
        token="secret",
        debug=True,
        debug_smoke=True,
        pull_requests_enabled=True,
        pull_request_merge_enabled=True,
        issues_enabled=True,
        issue_close_enabled=True,
        github_bin="/usr/bin/gh",
        pull_request_timeout_seconds=12.0,
        pull_request_allowed_repos=("owner/repo",),
        github_remote="github",
        github_ssh_command="ssh -i ~/.ssh/github-m",
        github_ssh_command_overrides={"owner/repo": "ssh -i ~/.ssh/repo"},
    )
    monkeypatch.setattr("agent_pbx.mcp_daemon.subprocess.Popen", FakePopen)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._mcp_port_available",
        lambda _config: {"ok": True},
    )
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._wait_ready",
        lambda _config, *, timeout: {"ready_at": 123.0},  # noqa: ARG005
    )
    monkeypatch.setattr("agent_pbx.mcp_daemon._pid_alive", lambda pid: pid == 4321)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._pid_matches_metadata",
        lambda *_args: True,
    )

    result = start_mcp_daemon(config, timeout=1)

    assert result["ok"] is True
    assert result["started"] is True
    assert calls["kwargs"]["start_new_session"] is True
    assert calls["kwargs"]["env"]["AGENT_PBX_TOKEN"] == "secret"
    assert calls["kwargs"]["env"]["AGENT_PBX_PR_ENABLED"] == "1"
    assert calls["kwargs"]["env"]["AGENT_PBX_PR_MERGE_ENABLED"] == "1"
    assert calls["kwargs"]["env"]["AGENT_PBX_ISSUES_ENABLED"] == "1"
    assert calls["kwargs"]["env"]["AGENT_PBX_ISSUES_CLOSE_ENABLED"] == "1"
    assert calls["kwargs"]["env"]["AGENT_PBX_GH_BIN"] == "/usr/bin/gh"
    assert calls["kwargs"]["env"]["AGENT_PBX_PR_TIMEOUT_SECONDS"] == "12.0"
    assert calls["kwargs"]["env"]["AGENT_PBX_PR_ALLOWED_REPOS"] == "owner/repo"
    assert calls["kwargs"]["env"]["AGENT_PBX_GITHUB_REMOTE"] == "github"
    assert (
        calls["kwargs"]["env"]["AGENT_PBX_GITHUB_SSH_COMMAND"]
        == "ssh -i ~/.ssh/github-m"
    )
    assert json.loads(
        calls["kwargs"]["env"]["AGENT_PBX_GITHUB_SSH_COMMAND_OVERRIDES_JSON"]
    ) == {"owner/repo": "ssh -i ~/.ssh/repo"}
    assert calls["argv"][:3] == [calls["argv"][0], "-m", "agent_pbx"]
    assert "mcp" in calls["argv"]
    assert "serve" in calls["argv"]
    assert "--token" not in calls["argv"]
    assert calls["argv"][calls["argv"].index("--port") + 1] == "9876"
    assert "--debug" in calls["argv"]
    assert "--debug-smoke" in calls["argv"]

    metadata = json.loads(config.metadata_file.read_text(encoding="utf-8"))
    assert metadata["pid"] == 4321
    assert metadata["token_configured"] is True
    assert metadata["ready_at"] == 123.0
    assert metadata["mcp_url"] == "http://127.0.0.1:9876/mcp"
    assert metadata["pull_requests_enabled"] is True
    assert metadata["pull_request_merge_enabled"] is True
    assert metadata["issues_enabled"] is True
    assert metadata["issue_close_enabled"] is True
    assert metadata["github_bin"] == "/usr/bin/gh"
    assert metadata["pull_request_timeout_seconds"] == 12.0
    assert metadata["pull_request_allowed_repos"] == ["owner/repo"]
    assert metadata["github_remote"] == "github"
    assert metadata["github_ssh_command_configured"] is True
    assert metadata["github_ssh_command_override_count"] == 1
    assert "github_ssh_command" not in metadata


def test_start_mcp_daemon_does_not_export_unset_github_remote(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, Any] = {}

    class FakePopen:
        pid = 4321

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            calls["argv"] = argv
            calls["kwargs"] = kwargs

    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    monkeypatch.setattr("agent_pbx.mcp_daemon.subprocess.Popen", FakePopen)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._mcp_port_available",
        lambda _config: {"ok": True},
    )
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._wait_ready",
        lambda _config, *, timeout: {"ready_at": 123.0},  # noqa: ARG005
    )
    monkeypatch.setattr("agent_pbx.mcp_daemon._pid_alive", lambda pid: pid == 4321)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._pid_matches_metadata",
        lambda *_args: True,
    )

    result = start_mcp_daemon(config, timeout=1)

    assert result["ok"] is True
    assert "AGENT_PBX_GITHUB_REMOTE" not in calls["kwargs"]["env"]
    metadata = json.loads(config.metadata_file.read_text(encoding="utf-8"))
    assert metadata["github_remote"] is None


def test_start_mcp_daemon_fails_fast_when_port_is_in_use(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._mcp_port_available",
        lambda _config: {
            "ok": False,
            "error": {"code": "MCP_PORT_IN_USE", "message": "port busy"},
        },
    )

    def fail_popen(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("daemon should not spawn when the requested port is busy")

    monkeypatch.setattr("agent_pbx.mcp_daemon.subprocess.Popen", fail_popen)

    result = start_mcp_daemon(config, timeout=1)

    assert result["ok"] is False
    assert result["started"] is False
    assert result["error"]["code"] == "MCP_PORT_IN_USE"


def test_start_mcp_daemon_refuses_lan_without_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, host=REMOTE_BIND_HOST, port=9876)

    def fail_popen(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("daemon should not spawn for LAN bind without auth")

    monkeypatch.setattr("agent_pbx.mcp_daemon.subprocess.Popen", fail_popen)

    result = start_mcp_daemon(config, timeout=1)

    assert result["ok"] is False
    assert result["started"] is False
    assert result["error"]["code"] == "LAN_BIND_REQUIRES_TOKEN"
    assert not config.resolved_db_path.exists()


def test_stop_mcp_daemon_removes_metadata_after_termination(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    config.global_dir.mkdir(parents=True)
    config.metadata_file.write_text(
        json.dumps({"pid": 4321, "state_root": str(tmp_path.resolve())}),
        encoding="utf-8",
    )
    alive = {"value": True}
    monkeypatch.setattr("agent_pbx.mcp_daemon._pid_alive", lambda _pid: alive["value"])
    monkeypatch.setattr("agent_pbx.mcp_daemon._pid_matches_metadata", lambda *_args: True)
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._terminate_process_group",
        lambda _pid, *, timeout: alive.update({"value": False}),  # noqa: ARG005
    )

    result = stop_mcp_daemon(config, timeout=1)

    assert result["stopped"] is True
    assert result["running"] is False
    assert not config.metadata_file.exists()


def test_restart_mcp_daemon_waits_for_port_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    starts: list[float] = []
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon.stop_mcp_daemon",
        lambda _config, *, timeout: {"stopped": True, "timeout": timeout},
    )
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon._wait_for_port_release",
        lambda _config, *, timeout: {"ok": True, "timeout": timeout},
    )
    monkeypatch.setattr(
        "agent_pbx.mcp_daemon.start_mcp_daemon",
        lambda _config, *, timeout: starts.append(timeout)
        or {"ok": True, "started": True},
    )

    result = restart_mcp_daemon(config, timeout=3)

    assert result["ok"] is True
    assert result["port_release"]["ok"] is True
    assert starts == [3]


def test_mcp_cli_parser_accepts_lifecycle_commands() -> None:
    parser = build_parser()

    start = parser.parse_args(["mcp", "start", "--port", "9876"])
    status = parser.parse_args(["mcp", "status"])
    serve = parser.parse_args(["mcp", "serve", "--debug-smoke"])

    assert start.command == "mcp"
    assert start.mcp_command == "start"
    assert start.port == 9876
    assert status.mcp_command == "status"
    assert serve.mcp_command == "serve"
    assert serve.debug_smoke is True


def test_mcp_cli_parser_uses_local_env_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_HOST", "127.0.0.2")
    monkeypatch.setenv("AGENT_PBX_PORT", "9876")
    monkeypatch.setenv("AGENT_PBX_TOKEN", "secret")
    monkeypatch.setenv("AGENT_PBX_SERVER_URL", "http://127.0.0.2:9876")
    monkeypatch.setenv("AGENT_PBX_DEBUG", "1")

    parser = build_parser()
    restart = parser.parse_args(["mcp", "restart"])
    tui = parser.parse_args(["tui"])
    sim_agent = parser.parse_args(["sim-agent"])
    override = parser.parse_args(["tui", "--server", "http://localhost:9999"])

    assert restart.host == "127.0.0.2"
    assert restart.port == 9876
    assert _daemon_config(restart).debug is True
    assert tui.server == "http://127.0.0.2:9876"
    assert tui.token == "secret"
    assert sim_agent.server == "http://127.0.0.2:9876"
    assert sim_agent.token == "secret"
    assert override.server == "http://localhost:9999"


def test_cli_loads_user_env_config_defaults(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / "config" / "agent-pbx" / "local.env"
    config.parent.mkdir(parents=True)
    config.write_text(
        "\n".join(
            [
                "AGENT_PBX_HOST=127.0.0.2",
                "AGENT_PBX_PORT=9876",
                "AGENT_PBX_SERVER_URL=http://${AGENT_PBX_HOST}:${AGENT_PBX_PORT}",
                "AGENT_PBX_TOKEN=config-token",
                "",
            ]
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("AGENT_PBX_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_PBX_HOST", raising=False)
    monkeypatch.delenv("AGENT_PBX_PORT", raising=False)
    monkeypatch.delenv("AGENT_PBX_SERVER_URL", raising=False)
    monkeypatch.delenv("AGENT_PBX_TOKEN", raising=False)
    monkeypatch.setattr(
        "agent_pbx.cli.run_tui",
        lambda *, server, token: calls.append((server, token)),
    )

    result = main(["tui"])

    assert result == 0
    assert calls == [("http://127.0.0.2:9876", "config-token")]


def test_cli_environment_overrides_user_env_config(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "config" / "agent-pbx" / "local.env"
    config.parent.mkdir(parents=True)
    config.write_text(
        "AGENT_PBX_SERVER_URL=http://config:8767\n"
        "AGENT_PBX_TOKEN=config-token\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("AGENT_PBX_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_PBX_SERVER_URL", "http://env:8767")
    monkeypatch.setenv("AGENT_PBX_TOKEN", "env-token")
    monkeypatch.setattr(
        "agent_pbx.cli.run_tui",
        lambda *, server, token: calls.append((server, token)),
    )

    result = main(["tui"])

    assert result == 0
    assert calls == [("http://env:8767", "env-token")]


def test_mcp_restart_preserves_previous_bind_without_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENT_PBX_HOST", raising=False)
    monkeypatch.delenv("AGENT_PBX_PORT", raising=False)
    previous = config_from_args(state_root=tmp_path)
    previous.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    previous.metadata_file.write_text(
        json.dumps({"host": REMOTE_BIND_HOST, "port": 8767}),
        encoding="utf-8",
    )

    parser = build_parser()
    restart = parser.parse_args(["mcp", "restart", "--state-root", str(tmp_path)])
    config = _daemon_config(restart)

    assert restart.host is None
    assert restart.port is None
    assert config.host == REMOTE_BIND_HOST
    assert config.port == 8767


def test_foreground_mcp_serve_refuses_lan_without_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_run(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "foreground server should not start for unauthenticated LAN bind"
        )

    monkeypatch.setenv("AGENT_PBX_NO_CONFIG", "1")
    monkeypatch.delenv("AGENT_PBX_TOKEN", raising=False)
    monkeypatch.setattr("agent_pbx.cli.uvicorn.run", fail_run)

    result = main(
        [
            "mcp",
            "serve",
            "--host",
            REMOTE_BIND_HOST,
            "--state-root",
            str(tmp_path),
        ]
    )

    assert result == 1
    assert not (tmp_path / "agent-pbx.sqlite").exists()
