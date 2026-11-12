from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_pbx.cli import _daemon_config, build_parser, main
from agent_pbx.mcp_daemon import (
    EVIDENCE_EXPORTS_ENABLED_ENV,
    EVIDENCE_EXPORT_ROOT_ENV,
    OPERATOR_KB_BUNDLE_V2_ENABLED_ENV,
    DURABLE_HELPERS_ENABLED_ENV,
    DURABLE_HELPER_MEASUREMENTS_ENV,
    DURABLE_HELPER_PROPOSAL_ROOTS_ENV,
    DURABLE_HELPER_ROOT_ENV,
    DYNAMIC_RESOURCES_ENABLED_ENV,
    WORKERBEE_DEVELOPMENT_RESOURCES_ENABLED_ENV,
    MCPDaemonConfig,
    _child_env,
    config_from_args,
    daemon_database_preflight,
    mcp_daemon_status,
    restart_mcp_daemon,
    start_mcp_daemon,
    start_mcp_daemon_log_monitor_from_env,
    stop_mcp_daemon,
    rotate_mcp_daemon_log,
    rotate_mcp_daemon_log_live,
    _pid_matches_metadata,
)
from agent_pbx.config import (
    WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV,
    WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV,
    WORKERBEE_TRUSTED_DIRECT_HELPER_ENV,
    WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV,
    WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV,
)
from agent_pbx.paths import default_state_root
from agent_pbx.store import SCHEMA_VERSION, Store


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


def test_managed_daemon_replaces_trusted_direct_environment_from_typed_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for name in (
        WORKERBEE_TRUSTED_DIRECT_HELPER_ENV,
        WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV,
        WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV,
        WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV,
        WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV,
    ):
        monkeypatch.setenv(name, "untrusted-ambient-value")
    config = MCPDaemonConfig(
        state_root=tmp_path / "state",
        workerbee_trusted_direct_helper_bin=tmp_path / "helper",
        workerbee_trusted_direct_receipt_root=tmp_path / "receipts",
        workerbee_trusted_direct_runtime_prefix="/workspace/direct-bind",
        workerbee_trusted_direct_approved_roots=(tmp_path / "git",),
        workerbee_trusted_direct_administrator_ceiling=True,
    )

    child = _child_env(config)

    assert child[WORKERBEE_TRUSTED_DIRECT_HELPER_ENV] == str(tmp_path / "helper")
    assert child[WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV] == str(
        tmp_path / "receipts"
    )
    assert child[WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV] == (
        "/workspace/direct-bind"
    )
    assert json.loads(child[WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV]) == [
        str(tmp_path / "git")
    ]
    assert child[WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV] == "1"


def test_managed_daemon_projects_typed_evidence_export_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(EVIDENCE_EXPORTS_ENABLED_ENV, "untrusted-ambient-value")
    monkeypatch.setenv(EVIDENCE_EXPORT_ROOT_ENV, "/untrusted/ambient")
    root = tmp_path / "private-exports"
    config = MCPDaemonConfig(
        state_root=tmp_path / "state",
        evidence_exports_enabled=True,
        evidence_export_root=root,
    )

    child = _child_env(config)

    assert child[EVIDENCE_EXPORTS_ENABLED_ENV] == "1"
    assert child[EVIDENCE_EXPORT_ROOT_ENV] == str(root)


def test_daemon_config_loads_evidence_export_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "private-exports"
    monkeypatch.setenv(EVIDENCE_EXPORTS_ENABLED_ENV, "true")
    monkeypatch.setenv(EVIDENCE_EXPORT_ROOT_ENV, str(root))
    args = build_parser().parse_args(["mcp", "status"])

    config = _daemon_config(args)

    assert config.evidence_exports_enabled is True
    assert config.evidence_export_root == root


def test_managed_daemon_projects_operator_kb_bundle_v2_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(OPERATOR_KB_BUNDLE_V2_ENABLED_ENV, "ambient")
    config = MCPDaemonConfig(
        state_root=tmp_path / "state",
        operator_kb_bundle_v2_enabled=True,
    )

    child = _child_env(config)

    assert child[OPERATOR_KB_BUNDLE_V2_ENABLED_ENV] == "1"


def test_daemon_config_loads_operator_kb_bundle_v2_gate(
    monkeypatch,
) -> None:
    monkeypatch.setenv(OPERATOR_KB_BUNDLE_V2_ENABLED_ENV, "true")
    args = build_parser().parse_args(["mcp", "status"])

    config = _daemon_config(args)

    assert config.operator_kb_bundle_v2_enabled is True


def test_managed_daemon_projects_typed_durable_helper_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DURABLE_HELPERS_ENABLED_ENV, "ambient")
    monkeypatch.setenv(DURABLE_HELPER_ROOT_ENV, "/untrusted/root")
    monkeypatch.setenv(DURABLE_HELPER_PROPOSAL_ROOTS_ENV, "not-json")
    root = tmp_path / "helper-state"
    proposal = tmp_path / "proposals"
    config = MCPDaemonConfig(
        state_root=tmp_path / "state",
        durable_helpers_enabled=True,
        durable_helper_root=root,
        durable_helper_proposal_roots=(proposal,),
        durable_helper_measurements_enabled=True,
    )

    child = _child_env(config)

    assert child[DURABLE_HELPERS_ENABLED_ENV] == "1"
    assert child[DURABLE_HELPER_ROOT_ENV] == str(root)
    assert json.loads(child[DURABLE_HELPER_PROPOSAL_ROOTS_ENV]) == [str(proposal)]
    assert child[DURABLE_HELPER_MEASUREMENTS_ENV] == "1"


def test_daemon_config_loads_durable_helper_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "helper-state"
    proposal = tmp_path / "proposals"
    monkeypatch.setenv(DURABLE_HELPERS_ENABLED_ENV, "true")
    monkeypatch.setenv(DURABLE_HELPER_ROOT_ENV, str(root))
    monkeypatch.setenv(DURABLE_HELPER_PROPOSAL_ROOTS_ENV, json.dumps([str(proposal)]))
    monkeypatch.setenv(DURABLE_HELPER_MEASUREMENTS_ENV, "true")
    args = build_parser().parse_args(["mcp", "status"])

    config = _daemon_config(args)

    assert config.durable_helpers_enabled is True
    assert config.durable_helper_root == root
    assert config.durable_helper_proposal_roots == (proposal,)
    assert config.durable_helper_measurements_enabled is True


def test_managed_daemon_projects_dynamic_resource_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(DYNAMIC_RESOURCES_ENABLED_ENV, "ambient")
    monkeypatch.setenv(WORKERBEE_DEVELOPMENT_RESOURCES_ENABLED_ENV, "ambient")
    config = MCPDaemonConfig(
        state_root=tmp_path / "state",
        dynamic_resources_enabled=True,
        workerbee_development_resources_enabled=True,
    )

    child = _child_env(config)

    assert child[DYNAMIC_RESOURCES_ENABLED_ENV] == "1"
    assert child[WORKERBEE_DEVELOPMENT_RESOURCES_ENABLED_ENV] == "1"


def test_daemon_config_loads_dynamic_resource_gate(monkeypatch) -> None:
    monkeypatch.setenv(DYNAMIC_RESOURCES_ENABLED_ENV, "true")
    monkeypatch.setenv(WORKERBEE_DEVELOPMENT_RESOURCES_ENABLED_ENV, "true")
    args = build_parser().parse_args(["mcp", "status"])

    config = _daemon_config(args)

    assert config.dynamic_resources_enabled is True
    assert config.workerbee_development_resources_enabled is True


def test_daemon_config_loads_trusted_direct_environment_as_typed_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    helper = tmp_path / "helper"
    receipts = tmp_path / "receipts"
    approved = tmp_path / "git"
    monkeypatch.setenv(WORKERBEE_TRUSTED_DIRECT_HELPER_ENV, str(helper))
    monkeypatch.setenv(WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV, str(receipts))
    monkeypatch.setenv(
        WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV,
        "/workspace/direct-bind",
    )
    monkeypatch.setenv(
        WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV,
        json.dumps([str(approved)]),
    )
    monkeypatch.setenv(WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV, "true")
    args = build_parser().parse_args(
        ["mcp", "status", "--state-root", str(tmp_path / "state")]
    )

    config = _daemon_config(args)

    assert config.workerbee_trusted_direct_helper_bin == helper
    assert config.workerbee_trusted_direct_receipt_root == receipts
    assert config.workerbee_trusted_direct_runtime_prefix == "/workspace/direct-bind"
    assert config.workerbee_trusted_direct_approved_roots == (approved,)
    assert config.workerbee_trusted_direct_administrator_ceiling is True


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


def test_mcp_daemon_log_rotation_is_bounded(tmp_path: Path) -> None:
    log = tmp_path / "mcp-daemon.log"
    log.write_text("current", encoding="utf-8")
    (tmp_path / "mcp-daemon.log.1").write_text("prior", encoding="utf-8")
    (tmp_path / "mcp-daemon.log.2").write_text("oldest", encoding="utf-8")

    rotated = rotate_mcp_daemon_log(log, max_bytes=1, backups=2)

    assert rotated == [tmp_path / "mcp-daemon.log.1"]
    assert not log.exists()
    assert (tmp_path / "mcp-daemon.log.1").read_text(encoding="utf-8") == "current"
    assert (tmp_path / "mcp-daemon.log.2").read_text(encoding="utf-8") == "prior"


def test_live_mcp_daemon_log_rotation_preserves_open_path(tmp_path: Path) -> None:
    log = tmp_path / "mcp-daemon.log"
    log.write_text("current", encoding="utf-8")
    (tmp_path / "mcp-daemon.log.1").write_text("prior", encoding="utf-8")

    rotated = rotate_mcp_daemon_log_live(log, max_bytes=1, backups=2)

    assert rotated == [tmp_path / "mcp-daemon.log.1"]
    assert log.exists()
    assert log.read_text(encoding="utf-8") == ""
    assert (tmp_path / "mcp-daemon.log.1").read_text(encoding="utf-8") == "current"
    assert (tmp_path / "mcp-daemon.log.2").read_text(encoding="utf-8") == "prior"


def test_live_log_monitor_consumes_launch_only_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    started: list[bool] = []

    class FakeThread:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def start(self) -> None:
            started.append(True)

    monkeypatch.setenv("AGENT_PBX_DAEMON_LOG_FILE", str(tmp_path / "daemon.log"))
    monkeypatch.setenv("AGENT_PBX_DAEMON_LOG_MAX_BYTES", "2097152")
    monkeypatch.setenv("AGENT_PBX_DAEMON_LOG_BACKUPS", "2")
    monkeypatch.setenv("AGENT_PBX_DAEMON_LOG_CHECK_SECONDS", "7")
    monkeypatch.setattr("agent_pbx.mcp_daemon.threading.Thread", FakeThread)

    thread = start_mcp_daemon_log_monitor_from_env()

    assert isinstance(thread, FakeThread)
    assert started == [True]
    assert "AGENT_PBX_DAEMON_LOG_FILE" not in os.environ
    assert "AGENT_PBX_DAEMON_LOG_MAX_BYTES" not in os.environ
    assert "AGENT_PBX_DAEMON_LOG_BACKUPS" not in os.environ
    assert "AGENT_PBX_DAEMON_LOG_CHECK_SECONDS" not in os.environ


def test_mcp_status_accepts_token_for_command_symmetry() -> None:
    args = build_parser().parse_args(["mcp", "status", "--token", "secret"])

    assert args.mcp_command == "status"
    assert args.token == "secret"


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
        legacy_polling_enabled=False,
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
    assert calls["kwargs"]["env"]["AGENT_PBX_DAEMON_LOG_FILE"] == str(
        config.log_file
    )
    assert calls["kwargs"]["env"]["AGENT_PBX_DAEMON_LOG_MAX_BYTES"] == str(
        64 * 1024 * 1024
    )
    assert calls["kwargs"]["env"]["AGENT_PBX_DAEMON_LOG_BACKUPS"] == "3"
    assert calls["kwargs"]["env"]["AGENT_PBX_DAEMON_LOG_CHECK_SECONDS"] == "30"
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
    assert "--disable-legacy-polling" in calls["argv"]

    metadata = json.loads(config.metadata_file.read_text(encoding="utf-8"))
    assert metadata["pid"] == 4321
    assert metadata["token_configured"] is True
    assert metadata["legacy_polling_enabled"] is False
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


def test_start_mcp_daemon_blocks_old_schema_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    store = Store(config.resolved_db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )

    def fail_popen(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("daemon must not spawn before explicit migration")

    monkeypatch.setattr("agent_pbx.mcp_daemon.subprocess.Popen", fail_popen)

    result = start_mcp_daemon(config, timeout=1)

    assert result["ok"] is False
    assert result["started"] is False
    assert result["error"]["code"] == "MCP_SCHEMA_MIGRATION_REQUIRED"
    assert result["schema_preflight"]["current_schema"] == SCHEMA_VERSION - 1
    commands = result["error"]["details"]["commands"]
    assert commands["dry_run"].startswith("agent-pbx migrate dry-run")
    assert commands["apply"].startswith("agent-pbx migrate apply")
    assert commands["verify"].startswith("agent-pbx migrate verify")
    assert str(config.resolved_db_path) in commands["apply"]
    assert not config.metadata_file.exists()
    assert not config.log_file.exists()


def test_restart_mcp_daemon_checks_schema_before_stopping_old_daemon(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    store = Store(config.resolved_db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )

    def fail_stop(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("existing daemon must remain running when preflight blocks")

    monkeypatch.setattr("agent_pbx.mcp_daemon.stop_mcp_daemon", fail_stop)

    result = restart_mcp_daemon(config, timeout=1)

    assert result["ok"] is False
    assert result["stop"] is None
    assert result["start"]["error"]["code"] == "MCP_SCHEMA_MIGRATION_REQUIRED"


def test_daemon_schema_preflight_reports_new_current_newer_and_corrupt(
    tmp_path: Path,
) -> None:
    missing_config = MCPDaemonConfig(state_root=tmp_path / "missing")
    assert daemon_database_preflight(missing_config)["state"] == "new"

    current_config = MCPDaemonConfig(state_root=tmp_path / "current")
    current_store = Store(current_config.resolved_db_path)
    current_store.init()
    current = daemon_database_preflight(current_config)
    assert current["ok"] is True
    assert current["current_schema"] == SCHEMA_VERSION

    with current_store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
    newer = daemon_database_preflight(current_config)
    assert newer["ok"] is False
    assert newer["error"]["code"] == "MCP_SCHEMA_NEWER_THAN_RUNTIME"
    assert newer["error"]["retryable"] is False

    corrupt_config = MCPDaemonConfig(state_root=tmp_path / "corrupt")
    corrupt_config.resolved_db_path.parent.mkdir(parents=True)
    corrupt_config.resolved_db_path.write_bytes(b"not sqlite")
    corrupt = daemon_database_preflight(corrupt_config)
    assert corrupt["ok"] is False
    assert corrupt["error"]["code"] == "MCP_DATABASE_INSPECTION_FAILED"


def test_mcp_status_surfaces_schema_start_blocker(tmp_path: Path) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    store = Store(config.resolved_db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )

    status = mcp_daemon_status(config)

    assert status["running"] is False
    assert status["start_blocked"] is True
    assert status["schema_preflight"]["error"]["code"] == (
        "MCP_SCHEMA_MIGRATION_REQUIRED"
    )


def test_mcp_status_cli_prints_schema_start_blocker(tmp_path: Path, capsys) -> None:
    config = MCPDaemonConfig(state_root=tmp_path, port=9876)
    store = Store(config.resolved_db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )

    result = main(["mcp", "status", "--state-root", str(tmp_path), "--port", "9876"])
    output = capsys.readouterr().out

    assert result == 0
    assert "start_blocked: True" in output
    assert "schema_error: MCP_SCHEMA_MIGRATION_REQUIRED" in output
    assert "agent-pbx migrate dry-run" in output


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


def test_foreground_mcp_disables_access_log_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("AGENT_PBX_NO_CONFIG", "1")
    monkeypatch.delenv("AGENT_PBX_DAEMON_ACCESS_LOG", raising=False)
    monkeypatch.setattr(
        "agent_pbx.cli.uvicorn.run",
        lambda _app, **kwargs: calls.append(kwargs),
    )

    result = main(
        [
            "mcp",
            "serve",
            "--host",
            "127.0.0.1",
            "--state-root",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert calls[0]["access_log"] is False
