from __future__ import annotations

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
