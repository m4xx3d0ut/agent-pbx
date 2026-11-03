from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


MANAGED_CALLER_BOOTSTRAP_VERSION = "agent-pbx-managed-caller-v1"
MANAGED_CALLER_PBX_TOOLS = (
    "pbx_agent_runbook",
    "pbx_register_agent",
    "pbx_report_turn",
    "pbx_set_active",
)


def _toml_key(value: str) -> str:
    return value if re.match(r"^[A-Za-z0-9_-]+$", value) else json.dumps(value)


def _toml_literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(
            f"{_toml_key(str(key))} = {_toml_literal(item)}"
            for key, item in value.items()
        ) + "}"
    if isinstance(value, Iterable):
        return "[" + ", ".join(_toml_literal(item) for item in value) + "]"
    return json.dumps(value)


def codex_config_override(key: str, value: object) -> str:
    return f"{key}={_toml_literal(value)}"


def managed_caller_codex_overrides(server_url: str) -> tuple[str, ...]:
    """Return the narrow, noninteractive Codex launch policy for PBX bootstrap."""

    mcp_url = f"{server_url.rstrip('/')}/mcp"
    approval_by_tool = {
        tool: {"approval_mode": "approve"}
        for tool in MANAGED_CALLER_PBX_TOOLS
    }
    return (
        codex_config_override("check_for_update_on_startup", False),
        codex_config_override(
            "mcp_servers.agent-pbx",
            {
                "url": mcp_url,
                "bearer_token_env_var": "AGENT_PBX_TOKEN",
                "enabled_tools": list(MANAGED_CALLER_PBX_TOOLS),
                "default_tools_approval_mode": "approve",
                "tools": approval_by_tool,
            },
        ),
    )


def managed_caller_bootstrap_prompt(
    *,
    agent_id: str,
    project: str,
    cwd: str | Path,
    source_codex_session_id: str | None = None,
) -> str:
    source_session = str(source_codex_session_id or "").strip()
    lines = [
        "Use Agent PBX as a caller agent.",
        "This is an audited managed-runtime bootstrap; perform only startup registration.",
        "Register this session with pbx_register_agent using:",
        f"- agent_id: {agent_id}",
        f"- project: {project}",
        "- agent_type: caller",
        f"- metadata.cwd: {Path(cwd).expanduser().resolve(strict=False)}",
        '- metadata.pbx_mode: "report"',
        '- metadata.launched_by: "agent-pbx-managed-runtime"',
        f"- metadata.reporting_agent_id: {agent_id}",
        "- metadata.codex_session_id: the current Codex session id",
    ]
    if source_session:
        lines.append(f"- metadata.source_codex_session_id: {source_session}")
    lines.extend(
        [
            "",
            "Then call pbx_agent_runbook once and send pbx_report_turn with "
            'status="waiting" and needs_input=true.',
            f"Every report must use agent_id={agent_id}, reporting_agent_id={agent_id}, "
            f"and metadata.reporting_agent_id={agent_id}.",
            "Stop at the Codex prompt after the waiting report. Do not begin project work "
            "until the operator sends a task.",
        ]
    )
    return "\n".join(lines)


def managed_caller_bootstrap_id(
    *,
    agent_id: str,
    project_path: str | Path,
    profile_id: str,
    runtime_server_mode: str,
    source_codex_session_id: str | None = None,
) -> str:
    payload = {
        "version": MANAGED_CALLER_BOOTSTRAP_VERSION,
        "agent_id": agent_id,
        "project_path": str(Path(project_path).expanduser().resolve(strict=False)),
        "profile_id": profile_id,
        "runtime_server_mode": runtime_server_mode,
        "source_codex_session_id": str(source_codex_session_id or "").strip(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
