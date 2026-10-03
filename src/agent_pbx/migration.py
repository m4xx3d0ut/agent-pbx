from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any
import uuid

from . import __version__
from .mcp_daemon import MCPDaemonConfig, mcp_daemon_status
from .store import SCHEMA_VERSION, Store


MIGRATION_API_VERSION = "agent-pbx.migration/v2"
BACKUP_MANIFEST = "manifest.json"


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    name: str
    source_path: str
    backup_path: str
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    backup_id: str
    created_at: float
    created_by_version: str
    source_schema: int | None
    source_integrity: str | None
    state_root: str
    retention_days: int
    retain_until: float
    artifacts: tuple[BackupArtifact, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": MIGRATION_API_VERSION,
            **asdict(self),
            "artifacts": [asdict(item) for item in self.artifacts],
        }


def create_migration_backup(
    config: MCPDaemonConfig,
    *,
    retention_days: int = 7,
    label: str = "pre-migration",
    include_config: bool = True,
) -> dict[str, Any]:
    safe_retention = max(1, min(365, int(retention_days)))
    backup_id = _backup_id(label)
    backup_root = config.resolved_state_root / "backups" / backup_id
    backup_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup_root.chmod(0o700)

    artifacts: list[BackupArtifact] = []
    source_schema: int | None = None
    source_integrity: str | None = None
    if config.resolved_db_path.exists():
        destination = backup_root / "database" / "agent-pbx.sqlite"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _sqlite_backup(config.resolved_db_path, destination)
        source_schema, source_integrity = database_posture(destination)
        artifacts.append(_artifact("database", config.resolved_db_path, destination, backup_root))

    if include_config:
        for name, source in migration_config_paths(config):
            if not source.exists() or not source.is_file():
                continue
            destination = backup_root / "config" / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(source, destination)
            destination.chmod(0o600)
            artifacts.append(_artifact(name, source, destination, backup_root))

    created_at = time.time()
    manifest = BackupManifest(
        backup_id=backup_id,
        created_at=created_at,
        created_by_version=__version__,
        source_schema=source_schema,
        source_integrity=source_integrity,
        state_root=str(config.resolved_state_root),
        retention_days=safe_retention,
        retain_until=created_at + safe_retention * 86400,
        artifacts=tuple(artifacts),
    )
    manifest_path = backup_root / BACKUP_MANIFEST
    _atomic_write(manifest_path, json.dumps(manifest.public_dict(), indent=2, sort_keys=True) + "\n")
    manifest_path.chmod(0o600)
    return {
        "api_version": MIGRATION_API_VERSION,
        "operation": "backup",
        "ok": source_integrity in {None, "ok"},
        "backup_path": str(backup_root),
        "manifest": manifest.public_dict(),
    }


def migration_dry_run(config: MCPDaemonConfig) -> dict[str, Any]:
    source = config.resolved_db_path
    daemon = mcp_daemon_status(config)
    if not source.exists():
        return {
            "api_version": MIGRATION_API_VERSION,
            "operation": "dry-run",
            "ok": True,
            "source_exists": False,
            "source_schema": None,
            "target_schema": SCHEMA_VERSION,
            "source_integrity": None,
            "simulated_schema": SCHEMA_VERSION,
            "simulated_integrity": "ok",
            "daemon_running": bool(daemon.get("running")),
            "changes_required": True,
            "message": "A new database will be created at the current schema.",
        }
    source_schema, source_integrity = database_posture(source)
    with tempfile.TemporaryDirectory(prefix="agent-pbx-migrate-") as temp:
        simulated = Path(temp) / "agent-pbx.sqlite"
        _sqlite_backup(source, simulated)
        Store(simulated).init()
        simulated_schema, simulated_integrity = database_posture(simulated)
    return {
        "api_version": MIGRATION_API_VERSION,
        "operation": "dry-run",
        "ok": source_integrity == "ok" and simulated_integrity == "ok",
        "source_exists": True,
        "source_schema": source_schema,
        "target_schema": SCHEMA_VERSION,
        "source_integrity": source_integrity,
        "simulated_schema": simulated_schema,
        "simulated_integrity": simulated_integrity,
        "daemon_running": bool(daemon.get("running")),
        "changes_required": source_schema != SCHEMA_VERSION,
        "message": (
            "Migration simulation completed without modifying the source database."
            if simulated_schema == SCHEMA_VERSION and simulated_integrity == "ok"
            else "Migration simulation did not reach the expected schema."
        ),
    }


def apply_migration(
    config: MCPDaemonConfig,
    *,
    retention_days: int = 7,
) -> dict[str, Any]:
    daemon = mcp_daemon_status(config)
    if daemon.get("running"):
        raise ValueError("stop the Agent PBX daemon before applying a database migration")
    dry_run = migration_dry_run(config)
    if not dry_run["ok"]:
        raise ValueError("migration dry-run failed; source database was not modified")
    backup = create_migration_backup(
        config,
        retention_days=retention_days,
        label="pre-migration",
    )
    config.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    Store(config.resolved_db_path).init()
    verification = verify_migration(config)
    if not verification["ok"]:
        raise RuntimeError(
            f"migration verification failed; restore {backup['backup_path']} before restart"
        )
    return {
        "api_version": MIGRATION_API_VERSION,
        "operation": "apply",
        "ok": True,
        "dry_run": dry_run,
        "backup": backup,
        "verification": verification,
    }


def verify_migration(config: MCPDaemonConfig) -> dict[str, Any]:
    path = config.resolved_db_path
    if not path.exists():
        return {
            "api_version": MIGRATION_API_VERSION,
            "operation": "verify",
            "ok": False,
            "schema": None,
            "integrity": None,
            "error": "database does not exist",
        }
    schema, integrity = database_posture(path)
    missing_tables: list[str] = []
    required_tables = {
        "metadata",
        "agents",
        "reports",
        "events",
        "tmux_runtime_mappings",
        "joplin_sync_jobs",
        "file_trash_entries",
        "remote_client_sessions",
        "remote_audit_events",
    }
    with sqlite3.connect(path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing_tables = sorted(required_tables - tables)
        foreign_keys = list(conn.execute("PRAGMA foreign_key_check"))
    return {
        "api_version": MIGRATION_API_VERSION,
        "operation": "verify",
        "ok": schema == SCHEMA_VERSION and integrity == "ok" and not missing_tables and not foreign_keys,
        "schema": schema,
        "target_schema": SCHEMA_VERSION,
        "integrity": integrity,
        "missing_tables": missing_tables,
        "foreign_key_violations": len(foreign_keys),
        "database_path": str(path),
    }


def rollback_migration(
    config: MCPDaemonConfig,
    backup_path: Path,
    *,
    restore_config: bool = False,
    retention_days: int = 7,
) -> dict[str, Any]:
    daemon = mcp_daemon_status(config)
    if daemon.get("running"):
        raise ValueError("stop the Agent PBX daemon before rollback")
    backup_root = backup_path.expanduser().resolve()
    manifest = load_backup_manifest(backup_root)
    database_artifact = next(
        (item for item in manifest["artifacts"] if item.get("name") == "database"),
        None,
    )
    if database_artifact is None:
        raise ValueError("selected backup does not contain an Agent PBX database")
    source = backup_root / str(database_artifact["backup_path"])
    _verify_artifact(source, database_artifact)
    schema, integrity = database_posture(source)
    if integrity != "ok":
        raise ValueError("backup database failed integrity verification")

    safety_backup = create_migration_backup(
        config,
        retention_days=retention_days,
        label="pre-rollback",
    )
    destination = config.resolved_db_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.restore-{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, temp)
    temp.chmod(0o600)
    # Store connections use WAL mode. A stopped daemon can still leave WAL and
    # shared-memory sidecars beside the database; if those survive replacement,
    # SQLite may replay post-backup transactions into the restored main file.
    # The daemon-running guard above makes it safe to retire those stale files
    # before atomically installing the verified backup.
    _remove_sqlite_sidecars(destination)
    os.replace(temp, destination)

    restored_config: list[str] = []
    if restore_config:
        for item in manifest["artifacts"]:
            if item.get("name") == "database":
                continue
            source_item = backup_root / str(item["backup_path"])
            _verify_artifact(source_item, item)
            destination_item = Path(str(item["source_path"])).expanduser()
            destination_item.parent.mkdir(parents=True, exist_ok=True)
            temp_item = destination_item.with_name(
                f".{destination_item.name}.restore-{uuid.uuid4().hex}.tmp"
            )
            shutil.copy2(source_item, temp_item)
            temp_item.chmod(int(item.get("mode") or 0o600))
            os.replace(temp_item, destination_item)
            restored_config.append(str(destination_item))

    restored_schema, restored_integrity = database_posture(destination)
    return {
        "api_version": MIGRATION_API_VERSION,
        "operation": "rollback",
        "ok": restored_integrity == "ok",
        "restored_backup": str(backup_root),
        "restored_schema": restored_schema,
        "restored_integrity": restored_integrity,
        "backup_schema": schema,
        "restored_config": restored_config,
        "safety_backup": safety_backup,
    }


def load_backup_manifest(backup_root: Path) -> dict[str, Any]:
    manifest_path = backup_root / BACKUP_MANIFEST
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is missing or invalid") from exc
    if payload.get("api_version") != MIGRATION_API_VERSION:
        raise ValueError("backup manifest API version is unsupported")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("backup manifest artifacts are invalid")
    return payload


def database_posture(path: Path) -> tuple[int, str]:
    uri = f"file:{path.expanduser().resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        schema = int(row[0]) if row else 0
    return schema, integrity


def migration_config_paths(config: MCPDaemonConfig) -> tuple[tuple[str, Path], ...]:
    xdg_config = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    codex_root = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    env_path = Path(
        os.getenv("AGENT_PBX_CONFIG") or xdg_config / "agent-pbx" / "local.env"
    )
    return (
        ("daemon-metadata.json", config.metadata_file),
        ("local.env", env_path),
        ("tui-settings.json", xdg_config / "agent-pbx" / "tui-settings.json"),
        ("codex-config.toml", codex_root / "config.toml"),
        ("codex-managed-skills.json", codex_root / "agent-pbx-managed-skills.json"),
    )


def render_migration_result(result: dict[str, Any]) -> str:
    operation = str(result.get("operation") or "backup")
    lines = [f"Agent PBX migration {operation}: {'PASS' if result.get('ok', True) else 'FAIL'}"]
    for key in (
        "source_schema",
        "target_schema",
        "source_integrity",
        "simulated_schema",
        "simulated_integrity",
        "schema",
        "integrity",
        "database_path",
        "backup_path",
        "restored_backup",
        "restored_schema",
        "restored_integrity",
        "message",
        "error",
    ):
        if key in result and result[key] is not None:
            lines.append(f"{key}: {result[key]}")
    backup = result.get("backup")
    if isinstance(backup, dict) and backup.get("backup_path"):
        lines.append(f"backup_path: {backup['backup_path']}")
    safety = result.get("safety_backup")
    if isinstance(safety, dict) and safety.get("backup_path"):
        lines.append(f"safety_backup: {safety['backup_path']}")
    return "\n".join(lines)


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as target_conn:
        source_conn.backup(target_conn)
    destination.chmod(0o600)


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            Path(f"{path}{suffix}").unlink()
        except FileNotFoundError:
            pass


def _artifact(
    name: str,
    source: Path,
    destination: Path,
    backup_root: Path,
) -> BackupArtifact:
    mode = source.stat().st_mode & 0o777
    return BackupArtifact(
        name=name,
        source_path=str(source.expanduser().resolve()),
        backup_path=str(destination.relative_to(backup_root)),
        sha256=_sha256(destination),
        size=destination.stat().st_size,
        mode=mode,
    )


def _verify_artifact(path: Path, item: dict[str, Any]) -> None:
    if not path.is_file():
        raise ValueError(f"backup artifact is missing: {path.name}")
    if _sha256(path) != str(item.get("sha256") or ""):
        raise ValueError(f"backup artifact checksum mismatch: {path.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_id(label: str) -> str:
    safe_label = "".join(character if character.isalnum() or character == "-" else "-" for character in label)
    safe_label = "-".join(part for part in safe_label.split("-") if part) or "backup"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"v2-{safe_label}-{stamp}-{uuid.uuid4().hex[:8]}"


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)
