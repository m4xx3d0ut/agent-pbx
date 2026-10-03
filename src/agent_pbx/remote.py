from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Mapping

from .auth import ClientPrincipal, principal_from_record
from .config import ServerConfig
from .runtime_tmux import tmux_client_attach_command, validate_tmux_socket
from .security import constant_time_equal
from .store import Store, TokenRecord


REMOTE_API_VERSION = "agent-pbx.remote/v2"
REMOTE_ROLES = {"observer", "controller"}
MAX_VIEW_STATE_BYTES = 32_768
MAX_TERMINAL_SNAPSHOT_LINES = 200
MAX_TERMINAL_SNAPSHOT_BYTES = 256_000
_SSH_TARGET = re.compile(r"[A-Za-z0-9_.@:-]+")
_REMOTE_CLIENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,120}")
_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|credential|password|prompt|reasoning|secret|token)"
)
_SECRET_TEXT = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)\S+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class RemoteAuthError(ValueError):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SSHAttachPlan:
    host: str
    entity_id: str
    read_only: bool
    command: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["command"] = list(self.command)
        result["shell"] = shlex.join(self.command)
        return result


def authenticate_websocket(
    *,
    authorization: str,
    origin: str | None,
    config: ServerConfig,
    store: Store,
) -> tuple[ClientPrincipal, str | None]:
    if origin and config.remote_allowed_origins and origin not in config.remote_allowed_origins:
        raise RemoteAuthError(4403, "websocket origin is not allowed")
    scheme, _, raw_token = authorization.partition(" ")
    auth_required = bool(
        (config.lan_bound and not config.allow_insecure_lan)
        or config.token
        or store.has_tokens()
    )
    if not auth_required:
        return ClientPrincipal(None, None, "local", None, None, (), ()), None
    if scheme.lower() != "bearer" or not raw_token:
        raise RemoteAuthError(4401, "bearer token required")
    if config.token and constant_time_equal(raw_token, config.token):
        record = TokenRecord(
            token_hash="runtime-token",
            token_id="runtime",
            kind="runtime",
            role="controller",
            label="runtime",
            audience=config.remote_audience,
            client_id=None,
            scopes=("*",),
            allowed_agent_ids=(),
            created_at=0.0,
            expires_at=None,
            revoked_at=None,
            last_used_at=None,
        )
        return principal_from_record(record), raw_token
    record = store.verify_token(raw_token)
    if record is None:
        raise RemoteAuthError(4401, "bearer token is invalid, expired, or revoked")
    if record.audience and record.audience != config.remote_audience:
        raise RemoteAuthError(4403, "bearer token audience does not match this server")
    return principal_from_record(record), raw_token


def websocket_token_is_current(
    principal: ClientPrincipal,
    raw_token: str | None,
    *,
    config: ServerConfig,
    store: Store,
) -> bool:
    if principal.role == "local":
        return True
    if principal.token_id == "runtime":
        return bool(config.token and raw_token and constant_time_equal(raw_token, config.token))
    if not raw_token:
        return False
    record = store.verify_token(raw_token)
    return bool(
        record
        and record.token_id == principal.token_id
        and (not record.audience or record.audience == config.remote_audience)
    )


def principal_allows_agent(principal: ClientPrincipal, entity_id: str) -> bool:
    return not principal.allowed_agent_ids or entity_id in principal.allowed_agent_ids


def normalize_remote_client_id(value: str) -> str:
    client_id = value.strip()
    if not _REMOTE_CLIENT_ID.fullmatch(client_id):
        raise ValueError(
            "remote client id must contain only letters, numbers, dot, underscore, colon, or hyphen"
        )
    return client_id


def validate_view_state(value: Mapping[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_VIEW_STATE_BYTES:
        raise ValueError("remote view state exceeds 32 KiB")

    def inspect(item: Any, path: str = "") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if _SENSITIVE_KEY.search(str(key)):
                    raise ValueError(f"remote view state contains sensitive key at {path}{key}")
                inspect(child, f"{path}{key}.")
        elif isinstance(item, list):
            for index, child in enumerate(item):
                inspect(child, f"{path}{index}.")

    inspect(value)
    return json.loads(serialized)


def capture_terminal_snapshot(
    mapping: Mapping[str, Any],
    *,
    lines: int = 80,
    timeout: float = 3.0,
) -> dict[str, Any]:
    safe_lines = min(MAX_TERMINAL_SNAPSHOT_LINES, max(1, int(lines)))
    socket_path = Path(str(mapping.get("socket_path") or "")).expanduser()
    pane_id = str(mapping.get("pane_id") or "").strip()
    if not socket_path.is_absolute() or not pane_id:
        raise ValueError("runtime mapping does not identify a tmux socket and pane")
    ready, message = validate_tmux_socket(socket_path)
    if not ready:
        raise ValueError(message)
    result = subprocess.run(
        [
            "tmux",
            "-S",
            str(socket_path),
            "capture-pane",
            "-p",
            "-J",
            "-S",
            f"-{safe_lines}",
            "-t",
            pane_id,
        ],
        capture_output=True,
        text=True,
        timeout=max(0.2, float(timeout)),
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "tmux terminal snapshot failed")
    text = result.stdout[-MAX_TERMINAL_SNAPSHOT_BYTES:]
    for pattern in _SECRET_TEXT:
        text = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[redacted]", text)
    return {
        "api_version": REMOTE_API_VERSION,
        "entity_id": mapping.get("entity_id"),
        "pane_id": pane_id,
        "captured_lines": len(text.splitlines()),
        "max_lines": safe_lines,
        "text": text,
        "read_only": True,
    }


def local_runtime_attach_command(
    store: Store,
    entity_id: str,
    *,
    read_only: bool = False,
) -> tuple[str, ...]:
    mapping = store.get_tmux_runtime_mapping(entity_id)
    if mapping is None:
        raise ValueError("runtime mapping not found")
    ready, message = validate_tmux_socket(Path(str(mapping.get("socket_path") or "")))
    if not ready:
        raise ValueError(message)
    return tmux_client_attach_command(mapping, read_only=read_only)


def ssh_attach_plan(
    host: str,
    entity_id: str,
    *,
    read_only: bool = False,
    state_root: Path | None = None,
) -> SSHAttachPlan:
    normalized_host = host.strip()
    normalized_entity = entity_id.strip()
    if not normalized_host or normalized_host.startswith("-") or not _SSH_TARGET.fullmatch(normalized_host):
        raise ValueError("SSH host must be a user@host or host name without shell syntax")
    if not normalized_entity or normalized_entity.startswith("-"):
        raise ValueError("entity id is required")
    remote = ["agent-pbx", "runtime", "attach", "--entity", normalized_entity]
    if read_only:
        remote.append("--read-only")
    if state_root is not None:
        remote.extend(("--state-root", str(state_root.expanduser())))
    command = ("ssh", "-t", normalized_host, shlex.join(remote))
    return SSHAttachPlan(normalized_host, normalized_entity, read_only, command)


def exec_runtime_attach(command: tuple[str, ...]) -> None:
    os.execvp(command[0], list(command))
