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
from agent_pbx.schemas import AgentRegisterRequest
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
            "UPDATE metadata SET value = '28' WHERE key = 'schema_version'"
        )

    dry_run = migration_dry_run(config)
    source_schema_after_dry_run, _ = database_posture(config.resolved_db_path)
    applied = apply_migration(config)

    assert dry_run["source_schema"] == 28
    assert dry_run["simulated_schema"] == SCHEMA_VERSION
    assert source_schema_after_dry_run == 28
    assert applied["ok"] is True
    assert applied["verification"]["schema"] == SCHEMA_VERSION
    assert Path(applied["backup"]["backup_path"]).is_dir()


def test_schema_43_adds_workerbee_capability_link_without_rewriting_requests(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    with store.connect() as conn:
        conn.execute(
            "ALTER TABLE dynamic_resource_requests "
            "DROP COLUMN provider_capability_sha256"
        )
        conn.execute(
            "UPDATE metadata SET value = '43' WHERE key = 'schema_version'"
        )

    store.init()

    with store.connect() as conn:
        columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(dynamic_resource_requests)"
            ).fetchall()
        }
    assert store.schema_version() == SCHEMA_VERSION == 45
    assert "provider_capability_sha256" in columns


def test_schema_30_fixture_migrates_with_retained_entities_and_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualify the v2.2 stable schema-30 boundary through current schema 40."""

    config = config_for(tmp_path)
    store = Store(config.resolved_db_path)
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="retained-agent",
            project="fixture-project",
            name="Retained Agent",
            metadata={"cwd": str(tmp_path / "project")},
        )
    )
    store.set_agent_starred("retained-agent", starred=True)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="retained-operator",
            project="fixture-operator",
            name="Retained Operator",
            agent_type="operator",
            metadata={"agent_type": "operator", "operator_role": "root"},
        )
    )
    store.append_event(
        "fixture_retained",
        {"agent_id": "retained-agent"},
        "retained-agent",
    )
    post_v22_tables = (
        "operator_action_receipts",
        "operator_action_transactions",
        "operator_authority_assignments",
        "operator_broker_requests",
        "operator_delegation_leases",
        "dynamic_resource_events",
        "dynamic_resource_requests",
        "operator_handoff_completion_deliveries",
        "operator_workspace_set_versions",
        "workerbee_controlled_run_leases",
        "workerbee_controlled_run_receipts",
        "workerbee_controlled_run_requests",
        "workerbee_workspace_policies",
        "workerbee_snapshot_policies",
        "workerbee_workspace_transitions",
    )
    with store.connect() as conn:
        for table in post_v22_tables:
            conn.execute(f"DROP TABLE {table}")
        conn.execute(
            "ALTER TABLE operator_handoffs DROP COLUMN execution_scope_json"
        )
        conn.execute(
            "UPDATE metadata SET value = '30' WHERE key = 'schema_version'"
        )

    # Keep migration config discovery inside this test's private fixture tree.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    dry_run = migration_dry_run(config)
    source_schema_after_dry_run, source_integrity = database_posture(
        config.resolved_db_path
    )
    with store.connect() as conn:
        source_tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        source_handoff_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(operator_handoffs)").fetchall()
        }

    assert dry_run["source_schema"] == 30
    assert dry_run["simulated_schema"] == SCHEMA_VERSION
    assert dry_run["simulated_integrity"] == "ok"
    assert source_schema_after_dry_run == 30
    assert source_integrity == "ok"
    assert not set(post_v22_tables) & source_tables
    assert "execution_scope_json" not in source_handoff_columns

    applied = apply_migration(config)
    migrated = Store(config.resolved_db_path)
    migrated_agents = {
        item["agent_id"]: item for item in migrated.list_agents(include_hidden=True)
    }
    migrated_events = migrated.list_events(after_id=0, limit=100)
    with migrated.connect() as conn:
        migrated_tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        migrated_handoff_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(operator_handoffs)").fetchall()
        }

    assert applied["ok"] is True
    assert applied["verification"]["schema"] == SCHEMA_VERSION
    assert set(post_v22_tables) <= migrated_tables
    assert "execution_scope_json" in migrated_handoff_columns
    assert set(migrated_agents) >= {"retained-agent", "retained-operator"}
    assert migrated_agents["retained-agent"]["name"] == "Retained Agent"
    assert migrated_agents["retained-agent"]["starred_at"] is not None
    assert migrated_agents["retained-operator"]["agent_type"] == "operator"
    assert [item["type"] for item in migrated_events] == ["fixture_retained"]

    restored = rollback_migration(
        config,
        Path(applied["backup"]["backup_path"]),
        restore_config=False,
    )
    rolled_back_schema, rolled_back_integrity = database_posture(
        config.resolved_db_path
    )
    with sqlite3.connect(config.resolved_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rolled_back_agents = {
            str(row["agent_id"]): dict(row)
            for row in conn.execute("SELECT * FROM agents").fetchall()
        }
        rolled_back_tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        rolled_back_handoff_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(operator_handoffs)").fetchall()
        }

    assert restored["ok"] is True
    assert rolled_back_schema == 30
    assert rolled_back_integrity == "ok"
    assert not set(post_v22_tables) & rolled_back_tables
    assert "execution_scope_json" not in rolled_back_handoff_columns
    assert set(rolled_back_agents) >= {"retained-agent", "retained-operator"}
    assert rolled_back_agents["retained-agent"]["starred_at"] is not None


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


def test_schema_repairs_pre_activation_controlled_run_fingerprint_columns(
    tmp_path: Path,
) -> None:
    config = config_for(tmp_path)
    store = Store(config.resolved_db_path)
    store.init()
    with store.connect() as conn:
        conn.execute(
            "ALTER TABLE workerbee_controlled_run_requests "
            "DROP COLUMN backend_fingerprint"
        )
        conn.execute(
            "ALTER TABLE workerbee_controlled_run_leases "
            "DROP COLUMN backend_fingerprint"
        )
        conn.execute(
            "UPDATE metadata SET value = '39' WHERE key = 'schema_version'"
        )

    store.init()
    with store.connect() as conn:
        request_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(workerbee_controlled_run_requests)"
            ).fetchall()
        }
        lease_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(workerbee_controlled_run_leases)"
            ).fetchall()
        }
    assert "backend_fingerprint" in request_columns
    assert "backend_fingerprint" in lease_columns
