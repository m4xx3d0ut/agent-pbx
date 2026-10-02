from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


WORKERBEE_REQUIRED_BOOTSTRAP_TOOLS = (
    "workerbee_v1_capabilities",
    "workerbee_v1_session_start",
)
MINIMUM_WORKERBEE_BOOTSTRAP_VERSION = "0.1.7"


@dataclass(frozen=True)
class WorkerBeeMcpPreflight:
    code: str
    ready: bool
    message: str
    remediation: str
    endpoint: str = ""
    workerbee_version: str = ""
    advertised_tools: tuple[str, ...] = ()
    missing_tools: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerBeeProjectVisibilityDiagnostic:
    code: str
    ready: bool
    host_cwd: str
    host_git_root: str | None
    workerbee_git_root: str | None
    workerbee_repo_exists: bool | None
    message: str
    remediation: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def diagnose_workerbee_project_visibility(
    cwd: str | Path,
    session_payload: dict[str, Any],
) -> WorkerBeeProjectVisibilityDiagnostic:
    host_cwd = str(Path(cwd).expanduser().resolve())
    result = subprocess.run(
        ["git", "-C", host_cwd, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    host_git_root = result.stdout.strip() if result.returncode == 0 else None
    data = (
        session_payload.get("data")
        if isinstance(session_payload.get("data"), dict)
        else session_payload
    )
    workerbee_git_root = str(data.get("git_root") or "").strip() or None
    runbook = data.get("project_runbook")
    repo_exists = (
        runbook.get("repo_exists")
        if isinstance(runbook, dict) and isinstance(runbook.get("repo_exists"), bool)
        else None
    )
    if host_git_root and (not workerbee_git_root or repo_exists is False):
        return WorkerBeeProjectVisibilityDiagnostic(
            code="WORKERBEE_PATH_VISIBILITY_MISMATCH",
            ready=False,
            host_cwd=host_cwd,
            host_git_root=host_git_root,
            workerbee_git_root=workerbee_git_root,
            workerbee_repo_exists=repo_exists,
            message="The host sees a Git checkout that WorkerBee did not resolve.",
            remediation=(
                "Compare the PBX host path with WorkerBee container/mount path mapping; "
                "keep project injection manual until both roots agree."
            ),
        )
    if host_git_root and workerbee_git_root:
        same_root = Path(host_git_root).resolve() == Path(workerbee_git_root).resolve()
        return WorkerBeeProjectVisibilityDiagnostic(
            code="WORKERBEE_PATH_VISIBLE" if same_root else "WORKERBEE_PATH_TRANSLATED",
            ready=True,
            host_cwd=host_cwd,
            host_git_root=host_git_root,
            workerbee_git_root=workerbee_git_root,
            workerbee_repo_exists=repo_exists,
            message=(
                "WorkerBee resolves the host repository."
                if same_root
                else "WorkerBee resolves the repository through a translated path."
            ),
            remediation="",
        )
    return WorkerBeeProjectVisibilityDiagnostic(
        code="WORKERBEE_HOST_NOT_GIT",
        ready=False,
        host_cwd=host_cwd,
        host_git_root=host_git_root,
        workerbee_git_root=workerbee_git_root,
        workerbee_repo_exists=repo_exists,
        message="The supplied host path is not a visible Git checkout.",
        remediation="Select a repository checkout and retry WorkerBee session discovery.",
    )


def workerbee_policy_preflight(
    enabled_tools: Iterable[str],
    *,
    endpoint: str = "",
) -> WorkerBeeMcpPreflight:
    enabled = {str(item).strip() for item in enabled_tools if str(item).strip()}
    missing = tuple(tool for tool in WORKERBEE_REQUIRED_BOOTSTRAP_TOOLS if tool not in enabled)
    if missing:
        return WorkerBeeMcpPreflight(
            code="WORKERBEE_POLICY_OMISSION",
            ready=False,
            message="Agent PBX launch policy omits required WorkerBee bootstrap tools.",
            remediation="Add the missing tools to the role's generated enabled_tools allowlist.",
            endpoint=endpoint,
            missing_tools=missing,
        )
    return WorkerBeeMcpPreflight(
        code="WORKERBEE_POLICY_READY",
        ready=True,
        message="Agent PBX launch policy includes WorkerBee bootstrap tools.",
        remediation="",
        endpoint=endpoint,
    )


def workerbee_inventory_preflight(
    listed_tools: Iterable[str],
    *,
    endpoint: str,
    workerbee_version: str = "",
    advertised_tools: Iterable[str] = (),
) -> WorkerBeeMcpPreflight:
    listed = {str(item).strip() for item in listed_tools if str(item).strip()}
    advertised = {
        str(item).strip() for item in advertised_tools if str(item).strip()
    }
    available = listed & advertised if advertised else listed
    missing = tuple(tool for tool in WORKERBEE_REQUIRED_BOOTSTRAP_TOOLS if tool not in available)
    if missing:
        return WorkerBeeMcpPreflight(
            code="WORKERBEE_CAPABILITY_MISSING",
            ready=False,
            message="WorkerBee is reachable but does not advertise the required bootstrap contract.",
            remediation="Upgrade WorkerBee or use a server that exposes the WorkerBee v1 bootstrap tools.",
            endpoint=endpoint,
            workerbee_version=workerbee_version,
            advertised_tools=tuple(sorted(available)),
            missing_tools=missing,
        )
    if workerbee_version and _version_tuple(workerbee_version) < _version_tuple(
        MINIMUM_WORKERBEE_BOOTSTRAP_VERSION
    ):
        return WorkerBeeMcpPreflight(
            code="WORKERBEE_VERSION_UNSUPPORTED",
            ready=False,
            message=(
                f"WorkerBee {workerbee_version} is older than the supported bootstrap "
                f"baseline {MINIMUM_WORKERBEE_BOOTSTRAP_VERSION}."
            ),
            remediation="Upgrade WorkerBee and rerun the MCP preflight.",
            endpoint=endpoint,
            workerbee_version=workerbee_version,
            advertised_tools=tuple(sorted(available)),
        )
    return WorkerBeeMcpPreflight(
        code="WORKERBEE_READY",
        ready=True,
        message="WorkerBee MCP bootstrap tools are available.",
        remediation="",
        endpoint=endpoint,
        workerbee_version=workerbee_version,
        advertised_tools=tuple(sorted(available)),
    )


async def inspect_workerbee_mcp(
    endpoint: str,
    *,
    enabled_tools: Iterable[str] | None = None,
    timeout_seconds: float = 5.0,
) -> WorkerBeeMcpPreflight:
    if enabled_tools is not None:
        policy = workerbee_policy_preflight(enabled_tools, endpoint=endpoint)
        if not policy.ready:
            return policy

    async def inspect() -> WorkerBeeMcpPreflight:
        async with streamablehttp_client(endpoint) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                inventory = await session.list_tools()
                listed = {tool.name for tool in inventory.tools}
                if "workerbee_v1_capabilities" not in listed:
                    return workerbee_inventory_preflight(
                        listed,
                        endpoint=endpoint,
                    )
                result = await session.call_tool("workerbee_v1_capabilities", {})
                payload = _tool_result_payload(result)
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                version = str(data.get("workerbee_version") or "").strip()
                advertised = data.get("mcp_tools")
                return workerbee_inventory_preflight(
                    listed,
                    endpoint=endpoint,
                    workerbee_version=version,
                    advertised_tools=(advertised if isinstance(advertised, list) else ()),
                )

    try:
        return await asyncio.wait_for(inspect(), timeout=max(0.1, timeout_seconds))
    except Exception as exc:  # noqa: BLE001 - diagnostics must normalize transports
        return WorkerBeeMcpPreflight(
            code="WORKERBEE_ENDPOINT_UNAVAILABLE",
            ready=False,
            message=f"WorkerBee MCP endpoint is unavailable ({type(exc).__name__}).",
            remediation="Check the configured endpoint and WorkerBee daemon, then retry.",
            endpoint=endpoint,
        )


def _tool_result_payload(result: object) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if not isinstance(structured, dict):
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in numbers[:4]) or (0,)
