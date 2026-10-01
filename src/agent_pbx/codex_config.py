from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import shutil
import stat
import tempfile
import time
from typing import Any, Iterable, Mapping

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


SAFE_GLOBAL_FIELDS: dict[str, dict[str, str]] = {
    "model": {
        "label": "Default model",
        "description": "Default model slug for new Codex threads.",
    },
    "model_provider": {
        "label": "Model provider",
        "description": "Selected provider id. Provider definitions stay hidden/read-only.",
    },
    "model_reasoning_effort": {
        "label": "Reasoning effort",
        "description": "Reasoning effort advertised to the selected model.",
    },
    "model_verbosity": {
        "label": "Response verbosity",
        "description": "Requested response detail for models that support it.",
    },
    "model_reasoning_summary": {
        "label": "Reasoning summary",
        "description": "Reasoning summary detail mode.",
    },
    "service_tier": {
        "label": "Service tier",
        "description": "Default service tier for Codex requests.",
    },
    "approval_policy": {
        "label": "Approval policy",
        "description": "Default shell approval policy.",
    },
    "approvals_reviewer": {
        "label": "Approvals reviewer",
        "description": "Reviewer used when approvals are required.",
    },
    "sandbox_mode": {
        "label": "Sandbox mode",
        "description": "Default command sandbox mode.",
    },
    "default_permissions": {
        "label": "Default permissions",
        "description": "Default permission profile name.",
    },
    "web_search": {
        "label": "Web search",
        "description": "Default web search mode.",
    },
}

SENSITIVE_TOP_LEVEL_KEYS = {
    "auth_credentials",
    "chatgpt_base_url",
    "experimental_realtime_ws_base_url",
    "model_providers",
    "openai_base_url",
    "otel",
}

HIDDEN_ITEM_DEFINITIONS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "auth_credentials",
        "Auth credentials",
        "hidden",
        (
            "Codex login/session material and MCP credential sources are never returned. "
            "Prefer env-var or helper indirection for updates."
        ),
        ("auth_credentials",),
    ),
    (
        "provider_secrets",
        "Provider secrets",
        "hidden",
        "Provider authentication stays hidden. Manage provider credentials outside Agent PBX or through a future vault-backed flow.",
        ("model_providers",),
    ),
    (
        "model_providers",
        "model_providers",
        "read_only",
        "Provider tables can include base URLs, auth, and transport details; this UI only reports presence.",
        ("model_providers",),
    ),
    (
        "telemetry_keys",
        "Telemetry keys",
        "hidden",
        "Telemetry routing and headers stay hidden/read-only.",
        ("otel",),
    ),
    (
        "arbitrary_nested_tables",
        "Arbitrary nested tables",
        "read_only",
        "Unknown and advanced tables are preserved but not edited by the structured UI.",
        (),
    ),
    (
        "admin_enforced_requirements",
        "Admin-enforced requirements",
        "read_only",
        "requirements.toml and managed policy layers are reported but never overwritten.",
        (),
    ),
)

KNOWN_TOP_LEVEL_TABLES = {
    "features",
    "hooks",
    "mcp_servers",
    "model_providers",
    "models",
    "notify",
    "otel",
    "permissions",
    "plugins",
    "profiles",
    "projects",
    "tui",
}

REQUIREMENTS_FILES = (
    ("system_requirements", Path("/etc/codex/requirements.toml")),
    ("system_managed_defaults", Path("/etc/codex/managed_config.toml")),
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TABLE_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)]\s*(?:#.*)?$")
_TOP_LEVEL_ASSIGNMENT_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_-]+)\s*=")
_SECRET_PATH_RE = re.compile(
    r"^mcp_servers\.(?P<server>[A-Za-z0-9_-]+)\."
    r"(?P<map>env|env_http_headers|http_headers)\."
    r"(?P<name>[A-Za-z0-9_.:/@ -]+)$"
)


@dataclass(frozen=True)
class CodexConfigPatchResult:
    view: dict[str, Any]
    changed_paths: tuple[str, ...]
    backup_path: str | None


def codex_home_from_env(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    configured = str(source.get("CODEX_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def codex_config_path(env: Mapping[str, str] | None = None) -> Path:
    return codex_home_from_env(env) / "config.toml"


def load_codex_config_view(
    *,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    path = config_path or codex_config_path(env)
    text, read_error = _read_text(path)
    parsed, parse_error = _parse_toml(text) if text is not None else ({}, None)
    stat_result = _safe_stat(path)
    warnings: list[str] = []
    if read_error:
        warnings.append(read_error)
    if parse_error:
        warnings.append(parse_error)

    requirements = _requirements_status(path.parent)
    fields = _safe_field_status(parsed)
    hidden_items = _hidden_item_status(parsed, requirements)
    mcp_servers = _mcp_server_status(parsed)
    conflicts = _requirements_conflicts(fields, requirements)
    if conflicts:
        warnings.extend(conflicts)

    return {
        "path": str(path),
        "exists": path.exists(),
        "mtime": stat_result.st_mtime if stat_result is not None else None,
        "size_bytes": stat_result.st_size if stat_result is not None else 0,
        "parse_error": parse_error,
        "fields": fields,
        "mcp_servers": mcp_servers,
        "hidden_items": hidden_items,
        "requirements": requirements,
        "warnings": warnings,
        "editable_paths": sorted(SAFE_GLOBAL_FIELDS),
        "secret_update_paths": [
            "mcp_servers.<server>.env.<name>",
            "mcp_servers.<server>.env_http_headers.<header>",
            "mcp_servers.<server>.http_headers.<header>",
        ],
    }


def patch_codex_config(
    *,
    updates: Mapping[str, Any] | None = None,
    remove: Iterable[str] | None = None,
    agent_pbx_mcp: Mapping[str, Any] | None = None,
    secret_updates: Iterable[Mapping[str, Any]] | None = None,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CodexConfigPatchResult:
    path = config_path or codex_config_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.strip():
        parsed, parse_error = _parse_toml(text)
        if parse_error:
            raise ValueError(parse_error)
    else:
        parsed = {}

    _validate_patch_against_requirements(
        updates or {},
        agent_pbx_mcp=agent_pbx_mcp,
        codex_home=path.parent,
    )

    changed: list[str] = []
    new_text = text
    for key, value in (updates or {}).items():
        normalized = _require_safe_global_key(key)
        new_text = _set_top_level_key(new_text, normalized, value)
        changed.append(normalized)
    for key in remove or ():
        normalized = _require_safe_global_key(key)
        new_text = _set_top_level_key(new_text, normalized, None)
        changed.append(normalized)

    if agent_pbx_mcp:
        new_text, mcp_changes = _patch_agent_pbx_mcp(new_text, agent_pbx_mcp)
        changed.extend(mcp_changes)

    for patch in secret_updates or ():
        secret_path = str(patch.get("path") or "").strip()
        remove_secret = bool(patch.get("remove"))
        value = patch.get("value")
        if remove_secret:
            value = None
        elif not isinstance(value, str) or not value:
            raise ValueError(f"Secret update {secret_path!r} requires a non-empty value.")
        new_text = _set_secret_path(new_text, secret_path, value)
        changed.append(secret_path)

    backup_path = None
    if new_text != text:
        backup_path = _write_atomic_with_backup(path, new_text)
    view = load_codex_config_view(config_path=path, env=env)
    return CodexConfigPatchResult(
        view=view,
        changed_paths=tuple(dict.fromkeys(changed)),
        backup_path=backup_path,
    )


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return "", None
    except OSError as exc:
        return None, f"Unable to read {path}: {exc}"


def _parse_toml(text: str) -> tuple[dict[str, Any], str | None]:
    try:
        parsed = tomllib.loads(text or "")
    except Exception as exc:
        return {}, f"Unable to parse Codex config TOML: {exc}"
    return parsed if isinstance(parsed, dict) else {}, None


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _safe_field_status(parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for key, info in SAFE_GLOBAL_FIELDS.items():
        value = parsed.get(key)
        configured = key in parsed
        hidden = _is_sensitive_value(key, value)
        fields.append(
            {
                "key": key,
                "label": info["label"],
                "description": info["description"],
                "configured": configured,
                "value": None if hidden else _json_safe_scalar(value),
                "value_preview": "configured" if hidden else _display_value(value),
                "editable": True,
                "sensitive": hidden,
                "source": "user_global" if configured else "unset",
            }
        )
    return fields


def _hidden_item_status(
    parsed: Mapping[str, Any],
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    top_level_tables = {
        key for key, value in parsed.items() if isinstance(value, dict)
    }
    arbitrary_tables = sorted(
        key for key in top_level_tables if key not in KNOWN_TOP_LEVEL_TABLES
    )
    mcp_secret_count = _mcp_secret_count(parsed)
    items: list[dict[str, Any]] = []
    for key, label, policy, reason, markers in HIDDEN_ITEM_DEFINITIONS:
        configured = any(marker in parsed for marker in markers)
        detail: dict[str, Any] = {}
        if key == "auth_credentials":
            configured = configured or mcp_secret_count > 0
            detail["mcp_secret_source_count"] = mcp_secret_count
        elif key == "arbitrary_nested_tables":
            configured = bool(arbitrary_tables)
            detail["table_names"] = arbitrary_tables
        elif key == "admin_enforced_requirements":
            configured = any(bool(item.get("exists")) for item in requirements)
            detail["paths"] = [
                item.get("path") for item in requirements if item.get("exists")
            ]
        items.append(
            {
                "key": key,
                "label": label,
                "policy": policy,
                "configured": configured,
                "value_preview": "configured" if configured else "not configured",
                "editable": False,
                "reason": reason,
                "detail": detail,
            }
        )
    return items


def _mcp_secret_count(parsed: Mapping[str, Any]) -> int:
    count = 0
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict):
        return 0
    for config in servers.values():
        if not isinstance(config, dict):
            continue
        for key in (
            "bearer_token_env_var",
            "env",
            "env_http_headers",
            "http_headers",
            "http_headers_helper",
            "oauth",
        ):
            if key in config:
                count += 1
    return count


def _mcp_server_status(parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    servers = parsed.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    results: list[dict[str, Any]] = []
    for name, raw_config in sorted(servers.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_config, dict):
            continue
        env = raw_config.get("env") if isinstance(raw_config.get("env"), dict) else {}
        env_http_headers = (
            raw_config.get("env_http_headers")
            if isinstance(raw_config.get("env_http_headers"), dict)
            else {}
        )
        http_headers = (
            raw_config.get("http_headers")
            if isinstance(raw_config.get("http_headers"), dict)
            else {}
        )
        oauth = raw_config.get("oauth") if isinstance(raw_config.get("oauth"), dict) else {}
        results.append(
            {
                "name": str(name),
                "enabled": raw_config.get("enabled", True),
                "url": _string_or_none(raw_config.get("url")),
                "command": _string_or_none(raw_config.get("command")),
                "auth": _string_or_none(raw_config.get("auth")),
                "bearer_token_env_var": _string_or_none(
                    raw_config.get("bearer_token_env_var")
                ),
                "http_headers_helper_configured": bool(raw_config.get("http_headers_helper")),
                "env_keys": sorted(str(key) for key in env),
                "env_http_headers": {
                    str(key): "configured" for key in sorted(env_http_headers)
                },
                "http_headers": {
                    str(key): "configured" for key in sorted(http_headers)
                },
                "oauth_configured": bool(oauth),
                "oauth_fields": sorted(str(key) for key in oauth),
            }
        )
    return results


def _requirements_status(codex_home: Path) -> list[dict[str, Any]]:
    paths = [*REQUIREMENTS_FILES, ("user_managed_defaults", codex_home / "managed_config.toml")]
    results: list[dict[str, Any]] = []
    for kind, path in paths:
        text, read_error = _read_text(path)
        parsed: dict[str, Any] = {}
        parse_error: str | None = None
        if text:
            parsed, parse_error = _parse_toml(text)
        stat_result = _safe_stat(path)
        results.append(
            {
                "kind": kind,
                "path": str(path),
                "exists": path.exists(),
                "mtime": stat_result.st_mtime if stat_result is not None else None,
                "keys": sorted(parsed),
                "read_error": read_error,
                "parse_error": parse_error,
                "editable": False,
                "requirements": _requirements_projection(parsed),
            }
        )
    return results


def _requirements_projection(parsed: Mapping[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for key in (
        "allowed_approval_policies",
        "allowed_sandbox_modes",
        "allowed_web_search_modes",
        "allowed_permission_profiles",
        "model_provider",
        "model_providers",
        "mcp_servers",
    ):
        if key not in parsed:
            continue
        value = parsed.get(key)
        if isinstance(value, dict):
            projection[key] = sorted(str(item) for item in value)
        elif isinstance(value, list):
            projection[key] = [str(item) for item in value]
        else:
            projection[key] = str(value)
    return projection


def _requirements_conflicts(
    fields: list[dict[str, Any]],
    requirements: list[dict[str, Any]],
) -> list[str]:
    by_key = {field["key"]: field for field in fields if field.get("configured")}
    warnings: list[str] = []
    for item in requirements:
        projection = item.get("requirements") if isinstance(item.get("requirements"), dict) else {}
        source = item.get("path") or item.get("kind")
        _append_allowed_conflict(
            warnings,
            by_key,
            projection,
            source,
            field_key="approval_policy",
            requirement_key="allowed_approval_policies",
        )
        _append_allowed_conflict(
            warnings,
            by_key,
            projection,
            source,
            field_key="sandbox_mode",
            requirement_key="allowed_sandbox_modes",
        )
        _append_allowed_conflict(
            warnings,
            by_key,
            projection,
            source,
            field_key="web_search",
            requirement_key="allowed_web_search_modes",
        )
        _append_allowed_conflict(
            warnings,
            by_key,
            projection,
            source,
            field_key="default_permissions",
            requirement_key="allowed_permission_profiles",
        )
        enforced_provider = projection.get("model_provider")
        if enforced_provider and by_key.get("model_provider"):
            configured = str(by_key["model_provider"].get("value") or "")
            if configured and configured != str(enforced_provider):
                warnings.append(
                    f"model_provider={configured!r} conflicts with enforced {enforced_provider!r} from {source}."
                )
    return warnings


def _validate_patch_against_requirements(
    updates: Mapping[str, Any],
    *,
    agent_pbx_mcp: Mapping[str, Any] | None,
    codex_home: Path,
) -> None:
    requirements = _requirements_status(codex_home)
    fields = [
        {"key": key, "value": value, "configured": True}
        for key, value in updates.items()
    ]
    conflicts = _requirements_conflicts(fields, requirements)
    if conflicts:
        raise ValueError(conflicts[0])
    if agent_pbx_mcp:
        for item in requirements:
            projection = (
                item.get("requirements")
                if isinstance(item.get("requirements"), dict)
                else {}
            )
            mcp_servers = projection.get("mcp_servers")
            if isinstance(mcp_servers, list) and "agent-pbx" not in {
                str(name) for name in mcp_servers
            }:
                raise ValueError(
                    "Agent PBX MCP cannot be wired because managed requirements "
                    f"do not allow an agent-pbx MCP server in {item.get('path') or item.get('kind')}."
                )


def _append_allowed_conflict(
    warnings: list[str],
    by_key: Mapping[str, dict[str, Any]],
    projection: Mapping[str, Any],
    source: Any,
    *,
    field_key: str,
    requirement_key: str,
) -> None:
    allowed = projection.get(requirement_key)
    field = by_key.get(field_key)
    if not isinstance(allowed, list) or field is None:
        return
    configured = str(field.get("value") or "")
    if configured and configured not in {str(item) for item in allowed}:
        warnings.append(
            f"{field_key}={configured!r} is outside {requirement_key} from {source}."
        )


def _require_safe_global_key(key: str) -> str:
    normalized = str(key or "").strip()
    if normalized not in SAFE_GLOBAL_FIELDS:
        if normalized in SENSITIVE_TOP_LEVEL_KEYS or normalized.startswith("model_providers"):
            raise ValueError(f"{normalized!r} is hidden/read-only and cannot be edited.")
        raise ValueError(f"Unsupported Codex config key {normalized!r}.")
    return normalized


def _set_top_level_key(text: str, key: str, value: Any) -> str:
    value_literal = None if value is None else _toml_literal(value)
    lines = text.splitlines()
    first_table = len(lines)
    for index, line in enumerate(lines):
        if line.strip().startswith("["):
            first_table = index
            break
    for index in range(first_table):
        match = _TOP_LEVEL_ASSIGNMENT_RE.match(lines[index])
        if match and match.group("key") == key:
            if value_literal is None:
                del lines[index]
            else:
                comment = _trailing_comment(lines[index])
                lines[index] = f"{key} = {value_literal}{comment}"
            return _join_lines(lines, text)
    if value_literal is None:
        return text
    insert_at = first_table
    line = f"{key} = {value_literal}"
    if insert_at == 0 and lines:
        lines.insert(0, line)
        lines.insert(1, "")
    elif insert_at < len(lines):
        if insert_at > 0 and lines[insert_at - 1].strip():
            lines.insert(insert_at, "")
        lines.insert(insert_at, line)
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(line)
    return _join_lines(lines, text)


def _patch_agent_pbx_mcp(text: str, patch: Mapping[str, Any]) -> tuple[str, list[str]]:
    changes: list[str] = []
    server = "agent-pbx"
    simple_keys = {
        "url",
        "bearer_token_env_var",
        "default_tools_approval_mode",
        "enabled",
        "required",
        "startup_timeout_sec",
        "tool_timeout_sec",
    }
    for key, value in patch.items():
        if key not in simple_keys:
            raise ValueError(f"Unsupported Agent PBX MCP config key {key!r}.")
        text = _set_table_key(text, ("mcp_servers", server), key, value)
        changes.append(f"mcp_servers.{server}.{key}")
    return text, changes


def _set_secret_path(text: str, path: str, value: str | None) -> str:
    match = _SECRET_PATH_RE.match(path)
    if match is None:
        raise ValueError(
            "Secret updates are limited to mcp_servers.<server>.env.<name>, "
            "mcp_servers.<server>.env_http_headers.<header>, or "
            "mcp_servers.<server>.http_headers.<header>."
        )
    server = match.group("server")
    map_key = match.group("map")
    name = match.group("name").strip()
    if not name:
        raise ValueError("Secret update path must include a leaf name.")
    return _set_table_map_key(text, ("mcp_servers", server), map_key, name, value)


def _set_table_key(
    text: str,
    table_path: tuple[str, ...],
    key: str,
    value: Any,
) -> str:
    value_literal = None if value is None else _toml_literal(value)
    lines = text.splitlines()
    start, end = _find_table_bounds(lines, table_path)
    if start is None:
        if value_literal is None:
            return text
        return _append_table(text, table_path, {key: value})
    for index in range(start + 1, end):
        match = _TOP_LEVEL_ASSIGNMENT_RE.match(lines[index])
        if match and match.group("key") == key:
            if value_literal is None:
                del lines[index]
            else:
                comment = _trailing_comment(lines[index])
                lines[index] = f"{key} = {value_literal}{comment}"
            return _join_lines(lines, text)
    if value_literal is None:
        return text
    lines.insert(end, f"{key} = {value_literal}")
    return _join_lines(lines, text)


def _set_table_map_key(
    text: str,
    table_path: tuple[str, ...],
    map_key: str,
    leaf_key: str,
    value: str | None,
) -> str:
    parsed, parse_error = _parse_toml(text)
    if parse_error:
        raise ValueError(parse_error)
    current: Mapping[str, Any] = parsed
    for segment in table_path:
        next_value = current.get(segment) if isinstance(current, Mapping) else None
        if not isinstance(next_value, Mapping):
            next_value = {}
        current = next_value
    existing_map = current.get(map_key) if isinstance(current, Mapping) else None
    if isinstance(existing_map, Mapping):
        new_map = {str(key): str(item) for key, item in existing_map.items()}
    else:
        new_map = {}
    if value is None:
        new_map.pop(leaf_key, None)
    else:
        new_map[leaf_key] = value
    return _set_table_key(text, table_path, map_key, new_map)


def _find_table_bounds(lines: list[str], table_path: tuple[str, ...]) -> tuple[int | None, int]:
    target = ".".join(_toml_key(segment) for segment in table_path)
    start: int | None = None
    for index, line in enumerate(lines):
        match = _TABLE_RE.match(line)
        if match is None:
            continue
        table_name = match.group("name").strip()
        if table_name == target:
            start = index
            continue
        if start is not None:
            return start, index
    return start, len(lines)


def _append_table(text: str, table_path: tuple[str, ...], values: Mapping[str, Any]) -> str:
    lines = text.splitlines()
    if lines and lines[-1].strip():
        lines.append("")
    lines.append("[" + ".".join(_toml_key(segment) for segment in table_path) + "]")
    for key, value in values.items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_literal(value)}")
    return _join_lines(lines, text)


def _write_atomic_with_backup(path: Path, text: str) -> str | None:
    backup_path: Path | None = None
    existing_mode = 0o600
    if path.exists():
        try:
            existing_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            existing_mode = 0o600
        timestamp = time.strftime("%Y%m%d%H%M%S")
        backup_path = path.with_name(f"{path.name}.agent-pbx.{timestamp}.bak")
        shutil.copy2(path, backup_path)
        backup_path.chmod(existing_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
        tmp_path.chmod(existing_mode)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return str(backup_path) if backup_path is not None else None


def _toml_key(value: str) -> str:
    if _IDENTIFIER_RE.match(value):
        return value
    return json.dumps(value)


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            if item is None:
                continue
            parts.append(f"{_toml_key(str(key))} = {_toml_literal(item)}")
        return "{ " + ", ".join(parts) + " }"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    raise ValueError(f"Unsupported TOML value type for {value!r}.")


def _join_lines(lines: list[str], original_text: str) -> str:
    suffix = "\n" if original_text.endswith("\n") or lines else ""
    return "\n".join(lines) + suffix


def _trailing_comment(line: str) -> str:
    in_string = False
    escape = False
    for index, char in enumerate(line):
        if char == '"' and not escape:
            in_string = not in_string
        if char == "#" and not in_string:
            return "  " + line[index:].strip()
        escape = char == "\\" and not escape
        if char != "\\":
            escape = False
    return ""


def _json_safe_scalar(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list) and all(isinstance(item, str | int | float | bool) for item in value):
        return value
    return None


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "configured"
    return str(value)


def _is_sensitive_value(key: str, value: Any) -> bool:
    if key in SENSITIVE_TOP_LEVEL_KEYS:
        return True
    return isinstance(value, dict)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
