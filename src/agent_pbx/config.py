from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


WORKERBEE_TRUSTED_DIRECT_HELPER_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN"
WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT"
)
WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX"
)
WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_JSON"
)
WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_ADMINISTRATOR_CEILING"
)


def env_optional_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute traversal-free path")
    return path


def env_workerbee_trusted_direct_roots() -> tuple[Path, ...]:
    raw = os.getenv(WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV, "").strip()
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV} must be valid JSON"
        ) from exc
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(
            f"{WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV} must be a JSON string list"
        )
    paths = tuple(Path(item).expanduser() for item in value)
    if any(not path.is_absolute() or ".." in path.parts for path in paths):
        raise ValueError(
            f"{WORKERBEE_TRUSTED_DIRECT_APPROVED_ROOTS_ENV} entries must be absolute traversal-free paths"
        )
    return tuple(dict.fromkeys(paths))


def env_workerbee_trusted_direct_runtime_prefix() -> str:
    raw = (
        os.getenv(
            WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV,
            "/workspace/direct-bind",
        ).strip()
        or "/workspace/direct-bind"
    )
    path = PurePosixPath(raw)
    if (
        not path.is_absolute()
        or "\\" in raw
        or "\x00" in raw
        or any(part in {"", ".", ".."} for part in raw.split("/")[1:])
    ):
        raise ValueError(
            f"{WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX_ENV} must be an absolute canonical POSIX path"
        )
    return path.as_posix()


def env_workerbee_trusted_direct_admin_ceiling() -> bool:
    raw = os.getenv(WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV, "").strip().lower()
    if not raw:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{WORKERBEE_TRUSTED_DIRECT_ADMIN_CEILING_ENV} must be a boolean"
    )


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("state/agent-pbx.sqlite")
    token: str | None = None
    allow_insecure_lan: bool = False
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    remote_audience: str = "agent-pbx"
    remote_allowed_origins: tuple[str, ...] = ()
    remote_max_clients: int = 16
    remote_message_rate_per_minute: int = 120
    remote_terminal_snapshots_enabled: bool = False
    legacy_polling_enabled: bool = True
    debug: bool = False
    debug_smoke: bool = False
    debug_smoke_duration_seconds: float = 300.0
    debug_smoke_min_interval_seconds: float = 30.0
    debug_smoke_max_interval_seconds: float = 60.0
    codex_event_bridge_enabled: bool = False
    codex_event_bridge_poll_seconds: float = 5.0
    codex_event_bridge_transport: str = "auto"
    codex_bin: str = "codex"
    evidence_exports_enabled: bool = False
    evidence_export_root: Path | None = None
    workerbee_bin: Path | None = None
    workerbee_timeout_seconds: float = 20.0
    workerbee_cache_seconds: float = 10.0
    workerbee_trusted_direct_helper_bin: Path | None = field(
        default_factory=lambda: env_optional_path(WORKERBEE_TRUSTED_DIRECT_HELPER_ENV)
    )
    workerbee_trusted_direct_receipt_root: Path | None = field(
        default_factory=lambda: env_optional_path(
            WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT_ENV
        )
    )
    workerbee_trusted_direct_runtime_prefix: str = field(
        default_factory=env_workerbee_trusted_direct_runtime_prefix
    )
    workerbee_trusted_direct_approved_roots: tuple[Path, ...] = field(
        default_factory=env_workerbee_trusted_direct_roots
    )
    workerbee_trusted_direct_administrator_ceiling: bool = field(
        default_factory=env_workerbee_trusted_direct_admin_ceiling
    )
    pull_requests_enabled: bool = False
    pull_request_merge_enabled: bool = False
    issues_enabled: bool = False
    issue_close_enabled: bool = False
    github_bin: str = "gh"
    pull_request_timeout_seconds: float = 20.0
    pull_request_allowed_repos: tuple[str, ...] = ()
    github_remote: str | None = None
    github_ssh_command: str | None = None
    github_ssh_command_overrides: dict[str, str] | None = None
    joplin_api_url: str | None = None
    joplin_token: str | None = None
    joplin_notebook: str = "Agent PBX"
    joplin_bin: Path | None = None
    joplin_profile: Path | None = None
    joplin_timeout_seconds: float = 15.0
    joplin_sync_on_write: bool = False
    joplin_profile_owner_mode: str = "external"
    joplin_webdav_url: str | None = None
    joplin_webdav_username: str | None = None
    joplin_webdav_password_configured: bool = False

    @property
    def lan_bound(self) -> bool:
        return self.host not in {"127.0.0.1", "localhost", "::1"}
