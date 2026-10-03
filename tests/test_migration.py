from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from agent_pbx.cli import build_parser
from agent_pbx.mcp_daemon import MCPDaemonConfig
from agent_pbx.migration import (
    apply_migration,
    create_migration_backup,
    database_posture,
    load_backup_manifest,
    migration_dry_run,
    rollback_migration,
    verify_migration,
)
from agent_pbx.store import SCHEMA_VERSION, Store


def config_for(tmp_path: Path) -> MCPDaemonConfig:
    return MCPDaemonConfig(state_root=tmp_path / "state")


def test_migration_backup_is_consistent_private_and_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_for(tmp_path)
    store = Store(config.resolved_db_path)
    store.init()
    config_root = tmp_path / "config"
    codex_root = tmp_path / "codex"
    config_root.joinpath("agent-pbx").mkdir(parents=True)
    codex_root.mkdir()
    config_root.joinpath("agent-pbx/local.env").write_text(
        "AGENT_PBX_TOKEN=private\n", encoding="utf-8"
    )
    config_root.joinpath("agent-pbx/tui-settings.json").write_text(
        "{}\n", encoding="utf-8"
    )
    codex_root.joinpath("config.toml").write_text(
        'model = "gpt-5.6-sol"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("CODEX_HOME", str(codex_root))

    result = create_migration_backup(config, retention_days=9)
    backup_path = Path(result["backup_path"])
    manifest = load_backup_manifest(backup_path)

    assert result["ok"] is True
    assert manifest["source_schema"] == SCHEMA_VERSION
    assert manifest["source_integrity"] == "ok"
    assert manifest["retention_days"] == 9
    assert {item["name"] for item in manifest["artifacts"]} >= {
        "database",
        "local.env",
        "tui-settings.json",
        "codex-config.toml",
    }
    assert backup_path.stat().st_mode & 0o777 == 0o700
    for item in manifest["artifacts"]:
        assert (backup_path / item["backup_path"]).stat().st_mode & 0o077 == 0
        serialized = json.dumps(item)
        assert "AGENT_PBX_TOKEN" not in serialized
        assert "AGENT_PBX_TOKEN=private" not in serialized


def test_migration_dry_run_and_apply_reach_current_schema(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    Store(config.resolved_db_path).init()
    with sqlite3.connect(config.resolved_db_path) as conn:
        conn.execute(
            "UPDATE metadata SET value = '27' WHERE key = 'schema_version'"
        )

    dry_run = migration_dry_run(config)
    source_schema_after_dry_run, _ = database_posture(config.resolved_db_path)
    applied = apply_migration(config)

    assert dry_run["source_schema"] == 27
    assert dry_run["simulated_schema"] == SCHEMA_VERSION
    assert source_schema_after_dry_run == 27
    assert applied["ok"] is True
    assert applied["verification"]["schema"] == SCHEMA_VERSION
    assert Path(applied["backup"]["backup_path"]).is_dir()


def test_migration_rollback_restores_database_and_optionally_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_for(tmp_path)
    store = Store(config.resolved_db_path)
    store.init()
    store.append_event("before_backup", {"value": 1}, "fixture")
    config_root = tmp_path / "config"
    config_file = config_root / "agent-pbx" / "local.env"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("VALUE=before\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))

    backup = create_migration_backup(config)
    store.append_event("after_backup", {"value": 2}, "fixture")
    config_file.write_text("VALUE=after\n", encoding="utf-8")

    restored = rollback_migration(
        config,
        Path(backup["backup_path"]),
        restore_config=True,
    )
    events = Store(config.resolved_db_path).list_events(after_id=0, limit=100)

    assert restored["ok"] is True
    assert [item["type"] for item in events] == ["before_backup"]
    assert config_file.read_text(encoding="utf-8") == "VALUE=before\n"
    assert Path(restored["safety_backup"]["backup_path"]).is_dir()


def test_migration_rollback_rejects_tampered_artifact(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    Store(config.resolved_db_path).init()
    backup = create_migration_backup(config, include_config=False)
    backup_path = Path(backup["backup_path"])
    manifest = load_backup_manifest(backup_path)
    database = next(
        backup_path / item["backup_path"]
        for item in manifest["artifacts"]
        if item["name"] == "database"
    )
    database.write_bytes(database.read_bytes() + b"tamper")

    with pytest.raises(ValueError, match="checksum mismatch"):
        rollback_migration(config, backup_path)


def test_apply_refuses_while_daemon_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_for(tmp_path)
    Store(config.resolved_db_path).init()
    monkeypatch.setattr(
        "agent_pbx.migration.mcp_daemon_status",
        lambda _config: {"running": True},
    )

    with pytest.raises(ValueError, match="stop the Agent PBX daemon"):
        apply_migration(config)


def test_verify_migration_and_cli_contract(tmp_path: Path) -> None:
    config = config_for(tmp_path)
    Store(config.resolved_db_path).init()
    verified = verify_migration(config)
    args = build_parser().parse_args(
        [
            "migrate",
            "rollback",
            "--backup",
            str(tmp_path / "backup"),
            "--confirm",
            "RESTORE",
            "--restore-config",
            "--json",
        ]
    )

    assert verified["ok"] is True
    assert verified["schema"] == SCHEMA_VERSION
    assert args.migrate_command == "rollback"
    assert args.confirm == "RESTORE"
    assert args.restore_config is True
