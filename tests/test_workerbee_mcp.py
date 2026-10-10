from __future__ import annotations

import json
import subprocess

from agent_pbx import workerbee_mcp


REQUIRED = workerbee_mcp.WORKERBEE_REQUIRED_BOOTSTRAP_TOOLS


def test_workerbee_policy_preflight_reports_allowlist_omission() -> None:
    result = workerbee_mcp.workerbee_policy_preflight([REQUIRED[0]])

    assert result.ready is False
    assert result.code == "WORKERBEE_POLICY_OMISSION"
    assert result.missing_tools == (REQUIRED[1],)


def test_workerbee_inventory_preflight_distinguishes_missing_capability() -> None:
    result = workerbee_mcp.workerbee_inventory_preflight(
        [REQUIRED[0]],
        endpoint="http://127.0.0.1:8765/mcp",
        workerbee_version="0.1.7",
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_CAPABILITY_MISSING"
    assert result.missing_tools == (REQUIRED[1],)


def test_workerbee_inventory_preflight_rejects_old_server() -> None:
    result = workerbee_mcp.workerbee_inventory_preflight(
        REQUIRED,
        endpoint="http://127.0.0.1:8765/mcp",
        workerbee_version="0.1.6",
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_VERSION_UNSUPPORTED"


def test_workerbee_inventory_preflight_accepts_supported_server() -> None:
    result = workerbee_mcp.workerbee_inventory_preflight(
        REQUIRED,
        endpoint="http://127.0.0.1:8765/mcp",
        workerbee_version="0.1.7",
    )

    assert result.ready is True
    assert result.code == "WORKERBEE_READY"


async def test_workerbee_live_preflight_normalizes_unavailable_endpoint(monkeypatch) -> None:
    def unavailable(_endpoint: str):
        raise ConnectionError("offline")

    monkeypatch.setattr(workerbee_mcp, "streamablehttp_client", unavailable)

    result = await workerbee_mcp.inspect_workerbee_mcp(
        "http://127.0.0.1:1/mcp",
        enabled_tools=REQUIRED,
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_ENDPOINT_UNAVAILABLE"


def test_workerbee_visibility_diagnostic_reports_mount_mismatch(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    result = workerbee_mcp.diagnose_workerbee_project_visibility(
        repo,
        {"data": {"git_root": None, "project_runbook": {"repo_exists": False}}},
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_PATH_VISIBILITY_MISMATCH"
    assert result.host_git_root == str(repo)


def switcher_payload(
    *,
    backend: str = "platform_debug",
    endpoint: str = "http://127.0.0.1:8765/mcp",
) -> str:
    return json.dumps(
        {
            "config": {
                "api_version": "workerbee.mcp_switch_config/v1",
                "path": "/home/test/.config/workerbee/wb-mcp-switch.env",
            },
            "status": {
                "api_version": "workerbee.mcp_switch_status/v1",
                "backend": backend,
                "mcp_endpoint": endpoint,
                "mcp": {
                    "status": "ready",
                    "server_info": {
                        "name": "K1S WorkerBee",
                        "version": "1.30.0",
                    },
                },
            },
        }
    )


def test_workerbee_switcher_reports_backend_provenance() -> None:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, switcher_payload(), "")

    result = workerbee_mcp.inspect_workerbee_switcher(
        status_bin="/opt/bin/wb-mcp-status",
        expected_endpoint="http://127.0.0.1:8765/mcp",
        runner=run,
    )

    assert result.ready is True
    assert result.code == "WORKERBEE_SWITCHER_READY"
    assert result.backend == "platform_debug"
    assert result.server_name == "K1S WorkerBee"
    assert result.server_version == "1.30.0"


def test_workerbee_switcher_rejects_endpoint_mismatch() -> None:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, switcher_payload(), "")

    result = workerbee_mcp.inspect_workerbee_switcher(
        status_bin="/opt/bin/wb-mcp-status",
        expected_endpoint="http://127.0.0.1:18765/mcp",
        runner=run,
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_SWITCHER_ENDPOINT_MISMATCH"
    assert "Align" in result.remediation


def test_workerbee_switcher_absence_keeps_direct_configuration_supported() -> None:
    result = workerbee_mcp.inspect_workerbee_switcher(which=lambda _name: None)

    assert result.configured is False
    assert result.ready is True
    assert result.code == "WORKERBEE_SWITCHER_NOT_CONFIGURED"
    assert workerbee_mcp.WORKERBEE_PRODUCT_URL in result.remediation


def test_workerbee_switcher_invalid_json_is_actionable() -> None:
    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    result = workerbee_mcp.inspect_workerbee_switcher(
        status_bin="/opt/bin/wb-mcp-status",
        runner=run,
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_SWITCHER_INVALID_JSON"


def test_workerbee_switcher_rejects_unknown_contract_version() -> None:
    payload = json.loads(switcher_payload())
    payload["status"]["api_version"] = "workerbee.mcp_switch_status/v2"

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    result = workerbee_mcp.inspect_workerbee_switcher(
        status_bin="/opt/bin/wb-mcp-status",
        runner=run,
    )

    assert result.ready is False
    assert result.code == "WORKERBEE_SWITCHER_UNSUPPORTED_CONTRACT"
    assert workerbee_mcp.WORKERBEE_SWITCHER_STATUS_API_VERSION in result.remediation
