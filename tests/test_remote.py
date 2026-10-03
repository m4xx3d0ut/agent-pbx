from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from agent_pbx.api import create_app
from agent_pbx.cli import build_parser
from agent_pbx.config import ServerConfig
from agent_pbx.events import EventClientRegistry
from agent_pbx.mcp_daemon import MCPDaemonConfig, lan_auth_guard
from agent_pbx.remote import ssh_attach_plan, validate_view_state
from agent_pbx.schemas import AgentRegisterRequest


RUNTIME_HEADERS = {"Authorization": "Bearer runtime-secret"}


def issue_token(
    client: TestClient,
    *,
    role: str,
    allowed_agent_ids: list[str] | None = None,
    client_id: str | None = None,
) -> dict[str, object]:
    response = client.post(
        "/v2/remote/tokens",
        headers=RUNTIME_HEADERS,
        json={
            "role": role,
            "label": f"{role}-fixture",
            "ttl_seconds": 3600,
            "allowed_agent_ids": allowed_agent_ids or [],
            "client_id": client_id,
        },
    )
    assert response.status_code == 200
    return response.json()


def register_agents(client: TestClient) -> None:
    for agent_id in ("agent-a", "agent-b"):
        response = client.post(
            "/v1/agents/register",
            headers=RUNTIME_HEADERS,
            json={
                "agent_id": agent_id,
                "project": "demo",
                "metadata": {
                    "cwd": "/tmp/demo",
                    "prompt": "must remain private",
                },
            },
        )
        assert response.status_code == 200


def test_remote_observer_is_scoped_redacted_and_read_only(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            token="runtime-secret",
            remote_audience="pbx-test",
        )
    )
    client = TestClient(app)
    register_agents(client)
    app.state.store.append_event(
        "private_fixture",
        {
            "agent_id": "agent-a",
            "prompt": "private prompt",
            "detail": "private detail",
            "summary": "safe summary",
        },
        "agent-a",
    )
    app.state.store.append_event(
        "other_fixture",
        {"agent_id": "agent-b", "summary": "other Agent"},
        "agent-b",
    )
    issued = issue_token(client, role="observer", allowed_agent_ids=["agent-a"])
    headers = {"Authorization": f"Bearer {issued['token']}"}

    identity = client.get("/v2/remote/me", headers=headers)
    snapshot = client.get("/v2/events/snapshot?limit=100", headers=headers)
    forbidden_read = client.get("/v1/agents", headers=headers)
    forbidden_write = client.post(
        "/v2/remote/tokens",
        headers=headers,
        json={"role": "observer", "label": "nested"},
    )
    forbidden_mcp = client.post("/mcp", headers=headers, json={})

    assert identity.status_code == 200
    assert identity.json()["role"] == "observer"
    assert identity.json()["audience"] == "pbx-test"
    assert identity.json()["allowed_agent_ids"] == ["agent-a"]
    assert snapshot.status_code == 200
    assert [item["agent_id"] for item in snapshot.json()["state"]["agents"]] == [
        "agent-a"
    ]
    fixture = next(
        item for item in snapshot.json()["events"] if item["type"] == "private_fixture"
    )
    assert fixture["payload"]["prompt"] == "[redacted]"
    assert fixture["payload"]["detail"] == "[redacted]"
    assert fixture["payload"]["summary"] == "safe summary"
    assert all(item["subject_id"] != "agent-b" for item in snapshot.json()["events"])
    assert forbidden_read.status_code == 403
    assert forbidden_write.status_code == 403
    assert forbidden_mcp.status_code == 403
    assert forbidden_mcp.json()["detail"] == "controller role required"


def test_remote_clients_keep_independent_view_state_and_cannot_cross_tokens(
    tmp_path: Path,
) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    first = issue_token(client, role="observer", client_id="thin-a")
    second = issue_token(client, role="observer", client_id="thin-b")
    first_headers = {"Authorization": f"Bearer {first['token']}"}
    second_headers = {"Authorization": f"Bearer {second['token']}"}

    saved_a = client.put(
        "/v2/remote/clients/thin-a/view-state",
        headers=first_headers,
        json={"view_state": {"tab": "latest", "scroll": 12}, "cursor": 10},
    )
    saved_b = client.put(
        "/v2/remote/clients/thin-b/view-state",
        headers=second_headers,
        json={"view_state": {"tab": "joplin", "scroll": 3}, "cursor": 20},
    )
    cross = client.get(
        "/v2/remote/clients/thin-a/view-state",
        headers=second_headers,
    )

    assert saved_a.status_code == 200
    assert saved_b.status_code == 200
    assert saved_a.json()["client"]["view_state"]["tab"] == "latest"
    assert saved_b.json()["client"]["view_state"]["tab"] == "joplin"
    assert cross.status_code == 403


def test_remote_token_revocation_is_observed_by_open_websocket(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    issued = issue_token(client, role="observer", client_id="remote-one")
    token_id = str(issued["record"]["token_id"])
    headers = {"Authorization": f"Bearer {issued['token']}"}

    with client.websocket_connect(
        "/v2/events/ws?client_id=remote-one",
        headers=headers,
    ) as websocket:
        assert websocket.receive_json()["client_id"] == "remote-one"
        revoked = client.post(
            f"/v2/remote/tokens/{token_id}/revoke",
            headers=RUNTIME_HEADERS,
        )
        assert revoked.status_code == 200
        with pytest.raises(WebSocketDisconnect) as raised:
            websocket.receive_json()
        assert raised.value.code == 4403

    rejected = client.get("/v2/remote/me", headers=headers)
    assert rejected.status_code == 403


def test_remote_websocket_client_identity_cannot_be_taken_over(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    first = issue_token(client, role="observer", client_id="stable-client")
    second = issue_token(client, role="observer", client_id="stable-client")

    with client.websocket_connect(
        "/v2/events/ws?client_id=stable-client",
        headers={"Authorization": f"Bearer {first['token']}"},
    ) as websocket:
        assert websocket.receive_json()["client_id"] == "stable-client"

    with pytest.raises(WebSocketDisconnect) as takeover:
        with client.websocket_connect(
            "/v2/events/ws?client_id=stable-client",
            headers={"Authorization": f"Bearer {second['token']}"},
        ):
            pass
    assert takeover.value.code == 4403


def test_remote_client_ids_reject_path_and_shell_syntax(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    invalid_issue = client.post(
        "/v2/remote/tokens",
        headers=RUNTIME_HEADERS,
        json={"role": "observer", "client_id": "../../other"},
    )
    issued = issue_token(client, role="observer")

    assert invalid_issue.status_code == 422
    with pytest.raises(WebSocketDisconnect) as invalid_socket:
        with client.websocket_connect(
            "/v2/events/ws?client_id=bad%2Fclient",
            headers={"Authorization": f"Bearer {issued['token']}"},
        ):
            pass
    assert invalid_socket.value.code == 4400


def test_remote_websocket_rejects_origin_audience_and_pressure(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            token="runtime-secret",
            remote_audience="server-a",
            remote_allowed_origins=("https://allowed.example",),
            remote_max_clients=1,
        )
    )
    client = TestClient(app)
    issued = issue_token(client, role="observer")
    headers = {
        "Authorization": f"Bearer {issued['token']}",
        "Origin": "https://denied.example",
    }
    with pytest.raises(WebSocketDisconnect) as denied_origin:
        with client.websocket_connect("/v2/events/ws", headers=headers):
            pass
    assert denied_origin.value.code == 4403

    wrong_token = "apbx_wrong_audience_fixture"
    app.state.store.add_token(
        wrong_token,
        kind="remote",
        role="observer",
        audience="server-b",
    )
    wrong = client.get(
        "/v2/remote/me",
        headers={"Authorization": f"Bearer {wrong_token}"},
    )
    assert wrong.status_code == 403

    allowed_headers = {
        "Authorization": f"Bearer {issued['token']}",
        "Origin": "https://allowed.example",
    }
    with client.websocket_connect("/v2/events/ws?client_id=one", headers=allowed_headers) as first:
        first.receive_json()
        with pytest.raises(WebSocketDisconnect) as pressure:
            with client.websocket_connect(
                "/v2/events/ws?client_id=two",
                headers=allowed_headers,
            ):
                pass
        assert pressure.value.code == 4429


def test_remote_lifecycle_control_is_preview_gated_and_audited(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    app.state.store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-a",
            project="demo",
            metadata={"cwd": str(workspace), "codex_session_id": "thread-a"},
        )
    )
    controller = issue_token(client, role="controller", allowed_agent_ids=["agent-a"])
    observer = issue_token(client, role="observer", allowed_agent_ids=["agent-a"])
    controller_headers = {"Authorization": f"Bearer {controller['token']}"}
    observer_headers = {"Authorization": f"Bearer {observer['token']}"}

    legacy = client.get("/v1/agents", headers=controller_headers)
    mint = client.post(
        "/v2/remote/tokens",
        headers=controller_headers,
        json={"role": "controller", "label": "privilege-escalation"},
    )
    mcp = client.post("/mcp", headers=controller_headers, json={})

    denied = client.post(
        "/v2/remote/control/agent-a/star",
        headers=observer_headers,
        json={},
    )
    cross_agent = client.post(
        "/v2/remote/control/agent-b/star",
        headers=controller_headers,
        json={},
    )
    preview = client.post(
        "/v2/remote/control/agent-a/star",
        headers=controller_headers,
        json={},
    )
    applied = client.post(
        "/v2/remote/control/agent-a/star",
        headers=controller_headers,
        json={"preview_token": preview.json()["result"]["preview_token"]},
    )
    audit = client.get("/v2/remote/audit", headers=RUNTIME_HEADERS)

    assert denied.status_code == 403
    assert cross_agent.status_code == 403
    assert legacy.status_code == 403
    assert legacy.json()["detail"] == "scoped controller must use Agent-scoped v2 endpoints"
    assert mint.status_code == 403
    assert mint.json()["detail"] == "global controller role required"
    assert mcp.status_code == 403
    assert mcp.json()["detail"] == "scoped controller cannot use the global MCP surface"
    assert preview.status_code == 200
    assert applied.status_code == 200
    assert applied.json()["result"]["after"]["agent"]["starred"] is True
    actions = [item["action"] for item in audit.json()["events"]]
    assert "lifecycle_star_preview" in actions
    assert "lifecycle_star_apply" in actions


def test_remote_terminal_snapshot_is_disabled_by_default(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    app.state.store.register_agent(AgentRegisterRequest(agent_id="agent-a", project="demo"))
    token = issue_token(client, role="observer", allowed_agent_ids=["agent-a"])

    response = client.get(
        "/v2/remote/terminal/agent-a/snapshot",
        headers={"Authorization": f"Bearer {token['token']}"},
    )

    assert response.status_code == 404
    assert "disabled" in response.json()["detail"]


def test_remote_terminal_snapshot_is_read_only_scoped_and_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            token="runtime-secret",
            remote_terminal_snapshots_enabled=True,
        )
    )
    client = TestClient(app)
    register_agents(client)
    app.state.store.upsert_tmux_runtime_mapping(
        entity_id="agent-a",
        server_mode="dedicated",
        server_id="server-a",
        socket_path="/tmp/pbx.sock",
        session_name="runtime-a",
        pane_id="%1",
    )
    observed: list[tuple[str, int]] = []

    def fake_capture(mapping: dict[str, object], *, lines: int) -> dict[str, object]:
        observed.append((str(mapping["entity_id"]), lines))
        return {
            "api_version": "agent-pbx.remote/v2",
            "entity_id": mapping["entity_id"],
            "pane_id": mapping["pane_id"],
            "captured_lines": 1,
            "max_lines": lines,
            "text": "read-only frame",
            "read_only": True,
        }

    monkeypatch.setattr("agent_pbx.api.capture_terminal_snapshot", fake_capture)
    token = issue_token(client, role="observer", allowed_agent_ids=["agent-a"])
    headers = {"Authorization": f"Bearer {token['token']}"}

    allowed = client.get(
        "/v2/remote/terminal/agent-a/snapshot?lines=42",
        headers=headers,
    )
    denied = client.get(
        "/v2/remote/terminal/agent-b/snapshot",
        headers=headers,
    )

    assert allowed.status_code == 200
    assert allowed.json()["read_only"] is True
    assert allowed.json()["text"] == "read-only frame"
    assert observed == [("agent-a", 42)]
    assert denied.status_code == 403
    assert app.state.store.list_remote_audit()[0]["action"] == "terminal_snapshot"


def test_view_state_rejects_sensitive_keys_and_excessive_payloads() -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        validate_view_state({"draft": {"api_token": "secret"}})
    with pytest.raises(ValueError, match="32 KiB"):
        validate_view_state({"selection": "x" * 40_000})


def test_ssh_attach_plan_and_cli_are_shell_safe() -> None:
    plan = ssh_attach_plan(
        "user@pbx-host",
        "operator-0-review-fork-3",
        read_only=True,
        state_root=Path("~/.local/share/agent-pbx"),
    )
    args = build_parser().parse_args(
        [
            "remote",
            "ssh-attach",
            "--host",
            "user@pbx-host",
            "--entity",
            "agent-a",
        ]
    )

    assert plan.command[:3] == ("ssh", "-t", "user@pbx-host")
    assert "agent-pbx runtime attach" in plan.public_dict()["shell"]
    assert "--read-only" in plan.command[-1]
    assert args.remote_command == "ssh-attach"
    with pytest.raises(ValueError, match="without shell syntax"):
        ssh_attach_plan("host;rm", "agent-a")


def test_lan_requires_token_and_tls_unless_explicitly_insecure(tmp_path: Path) -> None:
    no_token = lan_auth_guard(MCPDaemonConfig(state_root=tmp_path, host="0.0.0.0"))
    no_tls = lan_auth_guard(
        MCPDaemonConfig(state_root=tmp_path, host="0.0.0.0", token="secret")
    )
    secure = lan_auth_guard(
        MCPDaemonConfig(
            state_root=tmp_path,
            host="0.0.0.0",
            token="secret",
            tls_certfile=tmp_path / "cert.pem",
            tls_keyfile=tmp_path / "key.pem",
        )
    )
    insecure = lan_auth_guard(
        MCPDaemonConfig(
            state_root=tmp_path,
            host="0.0.0.0",
            allow_insecure_lan=True,
        )
    )

    assert no_token and no_token["code"] == "LAN_BIND_REQUIRES_TOKEN"
    assert no_tls and no_tls["code"] == "LAN_BIND_REQUIRES_TLS"
    assert secure is None
    assert secure_config_url(tmp_path).startswith("https://")
    assert insecure is None


def secure_config_url(tmp_path: Path) -> str:
    return MCPDaemonConfig(
        state_root=tmp_path,
        host="pbx.local",
        token="secret",
        tls_certfile=tmp_path / "cert.pem",
        tls_keyfile=tmp_path / "key.pem",
    ).mcp_url


def test_remote_schema_records_safe_token_and_audit_metadata(tmp_path: Path) -> None:
    app = create_app(
        ServerConfig(db_path=tmp_path / "pbx.sqlite", token="runtime-secret")
    )
    client = TestClient(app)
    issued = issue_token(client, role="observer", client_id="audit-client")
    token_record = issued["record"]
    listed = client.get("/v2/remote/tokens", headers=RUNTIME_HEADERS)

    assert app.state.store.schema_version() == 28
    assert "token_hash" not in json.dumps(token_record)
    assert "token" not in listed.json()["tokens"][0]
    assert listed.json()["tokens"][0]["client_id"] == "audit-client"


def test_event_registry_bounds_clients_and_incoming_messages() -> None:
    registry = EventClientRegistry(max_clients=1, message_rate_per_minute=1)
    registry.register(role="observer", cursor=0, client_id="one")
    with pytest.raises(RuntimeError, match="client limit"):
        registry.register(role="observer", cursor=0, client_id="two")
    assert registry.allow_message("one") is True
    assert registry.allow_message("one") is False
