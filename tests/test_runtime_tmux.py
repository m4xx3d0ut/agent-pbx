from __future__ import annotations

from pathlib import Path
import os
import socket
import subprocess
import tempfile

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.runtime_tmux import (
    MAX_GENERATED_UNIX_SOCKET_PATH_BYTES,
    OuterTmuxContext,
    RuntimeServerMode,
    RuntimeTmuxClient,
    RuntimeTmuxPane,
    TmuxServerIdentity,
    assess_runtime_mapping,
    ensure_runtime_socket_parent,
    probe_runtime_panes,
    recursive_attachment_reason,
    resolve_invoking_outer_client,
    resolve_runtime_tmux_server,
    runtime_mapping_server_identity,
    runtime_pop_plan,
    tmux_client_attach_command,
    tmux_select_runtime_pane_command,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def bind_socket(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    return server


def short_socket_path() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    directory = tempfile.TemporaryDirectory(prefix="apbx-", dir="/tmp")
    return directory, Path(directory.name) / "tmux.sock"


def pane(*, pane_id: str = "%7", pane_pid: int = 100) -> RuntimeTmuxPane:
    return RuntimeTmuxPane(
        session_name="agent-pbx-runtime-agent-a",
        window_id="@4",
        window_name="agent-a",
        pane_id=pane_id,
        pane_pid=pane_pid,
        cwd="/tmp/demo",
        current_command="codex",
        title="agent-a",
    )


def test_runtime_server_outer_if_present_uses_owned_socket(tmp_path: Path) -> None:
    directory, socket_path = short_socket_path()
    server = bind_socket(socket_path)
    try:
        identity = resolve_runtime_tmux_server(
            "outer_if_present",
            environ={"TMUX": f"{socket_path},123,0"},
        )
    finally:
        server.close()
        directory.cleanup()
    assert identity.ready is True
    assert identity.outer_detected is True
    assert identity.effective_mode is RuntimeServerMode.OUTER_IF_PRESENT
    assert identity.socket_path == str(socket_path.resolve())


def test_runtime_server_outer_if_present_falls_back_without_outer(tmp_path: Path) -> None:
    identity = resolve_runtime_tmux_server(
        "outer_if_present",
        environ={},
        runtime_dir=tmp_path,
    )
    assert identity.ready is True
    assert identity.effective_mode is RuntimeServerMode.DEDICATED
    assert len(os.fsencode(identity.socket_path)) <= MAX_GENERATED_UNIX_SOCKET_PATH_BYTES


def test_runtime_server_uses_configured_dedicated_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "pbx-3.7c.sock"

    identity = resolve_runtime_tmux_server(
        "dedicated",
        environ={"AGENT_PBX_TMUX_RUNTIME_SOCKET": str(socket_path)},
    )

    assert identity.ready is True
    assert identity.effective_mode is RuntimeServerMode.DEDICATED
    assert identity.socket_path == str(socket_path)
    assert "configured dedicated runtime socket" in identity.message


def test_runtime_server_rejects_relative_configured_socket() -> None:
    identity = resolve_runtime_tmux_server(
        "dedicated",
        environ={"AGENT_PBX_TMUX_RUNTIME_SOCKET": "relative/tmux.sock"},
    )

    assert identity.ready is False
    assert "absolute path" in identity.message


def test_runtime_pane_probe_distinguishes_dead_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = TmuxServerIdentity(
        RuntimeServerMode.DEDICATED,
        RuntimeServerMode.DEDICATED,
        "server-a",
        "/tmp/pbx.sock",
        True,
        False,
    )
    monkeypatch.setattr(
        "agent_pbx.runtime_tmux.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            "no server running on /tmp/pbx.sock",
        ),
    )

    ready, message, panes = probe_runtime_panes(identity)

    assert ready is False
    assert "no server running" in message
    assert panes == ()


def test_generated_dedicated_socket_shortens_long_runtime_root(tmp_path: Path) -> None:
    long_root = tmp_path / ("runtime-segment-" * 12)
    identity = resolve_runtime_tmux_server(
        "dedicated",
        environ={},
        runtime_dir=long_root,
    )

    assert len(os.fsencode(identity.socket_path)) <= MAX_GENERATED_UNIX_SOCKET_PATH_BYTES
    assert identity.socket_path.startswith("/tmp/agent-pbx-")
    assert "short user-local path" in identity.message


def test_runtime_socket_parent_is_private_and_rejects_symlink(tmp_path: Path) -> None:
    parent = tmp_path / "runtime"
    ensure_runtime_socket_parent(parent / "tmux.sock")
    assert parent.stat().st_mode & 0o777 == 0o700

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "runtime-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PermissionError, match="real directory"):
        ensure_runtime_socket_parent(link / "tmux.sock")


def test_outer_required_reports_missing_tmux() -> None:
    identity = resolve_runtime_tmux_server("outer_required", environ={})
    assert identity.ready is False
    assert "TMUX" in identity.message


def test_recursive_attachment_rejects_outer_tui_session() -> None:
    outer = OuterTmuxContext("workspace", "@1", "%2", "/dev/pts/3")
    assert recursive_attachment_reason(
        target_session="workspace", target_pane_id="%8", outer=outer
    )
    assert (
        recursive_attachment_reason(
            target_session="agent-pbx-runtime", target_pane_id="%8", outer=outer
        )
        is None
    )


def test_mapping_assessment_detects_ready_moved_and_reused() -> None:
    mapping = {
        "session_name": "agent-pbx-runtime-agent-a",
        "window_name": "agent-a",
        "pane_id": "%7",
        "pane_pid": 100,
        "process_start_ticks": 200,
        "cwd": "/tmp/demo",
    }
    assert assess_runtime_mapping(mapping, (pane(),), observed_start_ticks=200).state == "ready"
    moved = assess_runtime_mapping(mapping, (pane(pane_id="%9"),))
    assert moved.state == "moved"
    assert moved.repair_pane_id == "%9"
    assert assess_runtime_mapping(mapping, (pane(pane_pid=101),)).state == "reused"


def test_mapping_change_revokes_existing_writer_lease(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(AgentRegisterRequest(agent_id="agent-a", project="demo"))
    base = {
        "entity_id": "agent-a",
        "server_mode": "dedicated",
        "server_id": "server-a",
        "socket_path": "/tmp/server-a.sock",
        "session_name": "runtime-a",
        "pane_id": "%1",
    }
    store.upsert_tmux_runtime_mapping(**base)
    store.acquire_tmux_writer_lease("agent-a", client_id="tui-a", ttl_seconds=30)
    changed = store.upsert_tmux_runtime_mapping(**{**base, "pane_id": "%2"})
    assert changed["writer_client_id"] is None
    assert changed["writer_lease_active"] is False


def test_tmux_client_commands_are_mapping_scoped() -> None:
    mapping = {
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "window_id": "@12",
        "pane_id": "%7",
    }
    assert tmux_client_attach_command(mapping) == (
        "tmux",
        "-S",
        "/tmp/pbx.sock",
        "-u",
        "attach-session",
        "-t",
        "runtime-a:@12.%7",
    )
    assert "-r" in tmux_client_attach_command(mapping, read_only=True)
    assert tmux_select_runtime_pane_command(mapping)[-2:] == ("-t", "%7")


def test_tmux_client_commands_use_configured_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TMUX_BIN", "/opt/agent-pbx/tmux")
    mapping = {
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "window_id": "@12",
        "pane_id": "%7",
    }

    assert tmux_client_attach_command(mapping)[0] == "/opt/agent-pbx/tmux"
    assert tmux_select_runtime_pane_command(mapping)[0] == "/opt/agent-pbx/tmux"


def test_runtime_mapping_preserves_legacy_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TMUX_BIN", "/opt/agent-pbx/tmux")
    mapping = {
        "server_mode": "dedicated",
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "pane_id": "%7",
        "metadata": {"tmux_bin": "/usr/bin/tmux"},
    }

    assert tmux_client_attach_command(mapping)[0] == "/usr/bin/tmux"
    assert runtime_mapping_server_identity(mapping).command_prefix[0] == "/usr/bin/tmux"


def test_tmux_client_attach_falls_back_to_session_for_legacy_mapping() -> None:
    assert tmux_client_attach_command(
        {
            "socket_path": "/tmp/pbx.sock",
            "session_name": "runtime-a",
            "pane_id": "%7",
        }
    )[-2:] == ("-t", "runtime-a")


def test_integrated_pop_targets_only_recorded_client_and_sessions() -> None:
    mapping = {
        "server_mode": "outer_if_present",
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "origin_session_name": "agent-pbx",
        "origin_client_tty": "/dev/pts/9",
    }
    pop_out = runtime_pop_plan(mapping, direction="out")
    pop_in = runtime_pop_plan(mapping, direction="in")
    assert pop_out.action == "switch_client_out"
    assert pop_out.command[-4:] == ("-c", "/dev/pts/9", "-t", "runtime-a")
    assert pop_in.command[-4:] == ("-c", "/dev/pts/9", "-t", "agent-pbx")


def test_integrated_pop_rejects_recursive_origin_session() -> None:
    with pytest.raises(ValueError, match="session containing the Agent PBX TUI"):
        runtime_pop_plan(
            {
                "server_mode": "outer_if_present",
                "socket_path": "/tmp/pbx.sock",
                "session_name": "agent-pbx",
                "origin_session_name": "agent-pbx",
                "origin_client_tty": "/dev/pts/9",
            },
            direction="out",
        )


def test_integrated_pop_resolves_unique_live_invoking_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = TmuxServerIdentity(
        RuntimeServerMode.OUTER_IF_PRESENT,
        RuntimeServerMode.OUTER_IF_PRESENT,
        "outer",
        "/tmp/pbx.sock",
        True,
        True,
    )

    def run(command: list[str], **_kwargs: object) -> object:
        if "display-message" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "agent-pbx\t@1\t%2\t\n",
                "",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            "/dev/pts/8\tother\tattached\t80\n"
            "/dev/pts/9\tagent-pbx\tattached\t90\n",
            "",
        )

    monkeypatch.setattr("agent_pbx.runtime_tmux.subprocess.run", run)
    client = resolve_invoking_outer_client(
        identity,
        tui_pane_id="%2",
        origin_session_name="agent-pbx",
    )
    assert client == RuntimeTmuxClient(
        "/dev/pts/9",
        "agent-pbx",
        frozenset({"attached"}),
        90,
    )

    mapping = {
        "server_mode": "outer_if_present",
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "origin_session_name": "agent-pbx",
        "origin_client_tty": None,
    }
    plan = runtime_pop_plan(
        mapping,
        direction="out",
        origin_client_tty=client.tty,
    )
    assert plan.target_client == "/dev/pts/9"
    assert plan.command[-4:] == ("-c", "/dev/pts/9", "-t", "runtime-a")


def test_integrated_pop_uses_only_unique_focused_invoking_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = TmuxServerIdentity(
        RuntimeServerMode.OUTER_IF_PRESENT,
        RuntimeServerMode.OUTER_IF_PRESENT,
        "outer",
        "/tmp/pbx.sock",
        True,
        True,
    )

    def run(command: list[str], **_kwargs: object) -> object:
        if "display-message" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "agent-pbx\t@1\t%2\t\n",
                "",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            "/dev/pts/1\tagent-pbx\tattached\t10\n"
            "/dev/pts/2\tagent-pbx\tattached,focused\t20\n",
            "",
        )

    monkeypatch.setattr("agent_pbx.runtime_tmux.subprocess.run", run)
    client = resolve_invoking_outer_client(
        identity,
        tui_pane_id="%2",
        origin_session_name="agent-pbx",
    )
    plan = runtime_pop_plan(
        {
            "server_mode": "outer_if_present",
            "socket_path": "/tmp/pbx.sock",
            "session_name": "runtime-a",
            "origin_session_name": "agent-pbx",
        },
        direction="out",
        origin_client_tty=client.tty,
    )
    assert client.tty == "/dev/pts/2"
    assert "/dev/pts/1" not in plan.command
    assert plan.command[-4:] == ("-c", "/dev/pts/2", "-t", "runtime-a")


@pytest.mark.parametrize(
    ("clients", "message"),
    [
        ("/dev/pts/8\tother\tattached\t80\n", "no live client"),
        (
            "/dev/pts/1\tagent-pbx\tattached\t10\n"
            "/dev/pts/2\tagent-pbx\tattached\t20\n",
            "no unique focused client",
        ),
    ],
)
def test_integrated_pop_refuses_absent_or_ambiguous_clients(
    clients: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = TmuxServerIdentity(
        RuntimeServerMode.OUTER_IF_PRESENT,
        RuntimeServerMode.OUTER_IF_PRESENT,
        "outer",
        "/tmp/pbx.sock",
        True,
        True,
    )

    def run(command: list[str], **_kwargs: object) -> object:
        output = "agent-pbx\t@1\t%2\t\n" if "display-message" in command else clients
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr("agent_pbx.runtime_tmux.subprocess.run", run)
    with pytest.raises(ValueError, match=message):
        resolve_invoking_outer_client(
            identity,
            tui_pane_id="%2",
            origin_session_name="agent-pbx",
        )


def test_mapping_refresh_preserves_non_null_origin_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(AgentRegisterRequest(agent_id="agent-a", project="demo"))
    base = {
        "entity_id": "agent-a",
        "server_mode": "outer_if_present",
        "server_id": "server-a",
        "socket_path": "/tmp/server-a.sock",
        "session_name": "runtime-a",
        "pane_id": "%1",
    }
    store.upsert_tmux_runtime_mapping(
        **base,
        origin_client_tty="/dev/pts/9",
        origin_session_name="agent-pbx",
    )
    refreshed = store.upsert_tmux_runtime_mapping(
        **base,
        origin_client_tty=None,
        origin_session_name=None,
    )
    assert refreshed["origin_client_tty"] == "/dev/pts/9"
    assert refreshed["origin_session_name"] == "agent-pbx"


def test_dedicated_pop_uses_foreground_attach() -> None:
    plan = runtime_pop_plan(
        {
            "server_mode": "dedicated",
            "socket_path": "/tmp/pbx.sock",
            "session_name": "runtime-a",
        },
        direction="out",
    )
    assert plan.action == "suspend_attach"
    assert plan.command[-2:] == ("-t", "runtime-a")


def test_tmux_runtime_api_registers_reconciles_and_leases_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    directory, socket_path = short_socket_path()
    server = bind_socket(socket_path)
    observed = pane()
    monkeypatch.setattr("agent_pbx.api.list_runtime_panes", lambda _identity: (observed,))
    monkeypatch.setattr("agent_pbx.api.process_start_ticks", lambda _pid: 900)

    async def inspect_live(_mapping: object) -> dict[str, object]:
        return {
            "state": "ready",
            "safe": True,
            "message": "runtime matches",
            "repair_pane_id": None,
        }

    monkeypatch.setattr("agent_pbx.api.inspect_tmux_runtime_mapping_live", inspect_live)
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    try:
        registered = client.post(
            "/v1/agents/register",
            headers=headers,
            json={
                "agent_id": "agent-a",
                "project": "demo",
                "metadata": {"cwd": "/tmp/demo", "codex_session_id": "thread-a"},
            },
        )
        assert registered.status_code == 200
        mapping = client.post(
            "/v2/tmux/runtimes/agent-a",
            headers=headers,
            json={
                "server_mode": "outer_if_present",
                "socket_path": str(socket_path),
                "session_name": observed.session_name,
                "window_name": observed.window_name,
                "pane_id": observed.pane_id,
                "pane_pid": observed.pane_pid,
                "codex_session_id": "thread-a",
                "cwd": observed.cwd,
                "origin_session_name": "workspace",
                "origin_client_tty": "/dev/pts/3",
            },
        )
        assert mapping.status_code == 200
        assert mapping.json()["process_start_ticks"] == 900
        first = client.post(
            "/v2/tmux/runtimes/agent-a/writer/acquire",
            headers=headers,
            json={"client_id": "tui-a", "ttl_seconds": 30},
        )
        assert first.status_code == 200
        renewed = client.post(
            "/v2/tmux/runtimes/agent-a/writer/acquire",
            headers=headers,
            json={"client_id": "tui-a", "ttl_seconds": 30},
        )
        assert renewed.status_code == 200
        events = client.get(
            "/v1/events",
            headers=headers,
            params={"limit": 100},
        ).json()
        assert sum(
            event["type"] == "tmux_runtime_writer_acquired" for event in events
        ) == 1
        collision = client.post(
            "/v2/tmux/runtimes/agent-a/writer/acquire",
            headers=headers,
            json={"client_id": "tui-b", "ttl_seconds": 30},
        )
        assert collision.status_code == 409
        released = client.post(
            "/v2/tmux/runtimes/agent-a/writer/release",
            headers=headers,
            json={"client_id": "tui-a"},
        )
        assert released.status_code == 200
        reconciled = client.post("/v2/tmux/reconcile", headers=headers)
        assert reconciled.status_code == 200
        assert reconciled.json()["results"][0]["assessment"]["state"] == "ready"
    finally:
        server.close()
        directory.cleanup()


def test_tmux_runtime_api_rejects_recursive_session(tmp_path: Path, monkeypatch) -> None:
    directory, socket_path = short_socket_path()
    server = bind_socket(socket_path)
    observed = pane()
    monkeypatch.setattr("agent_pbx.api.list_runtime_panes", lambda _identity: (observed,))
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    try:
        client.post(
            "/v1/agents/register",
            headers=headers,
            json={"agent_id": "agent-a", "project": "demo", "metadata": {}},
        )
        response = client.post(
            "/v2/tmux/runtimes/agent-a",
            headers=headers,
            json={
                "server_mode": "outer_if_present",
                "socket_path": str(socket_path),
                "session_name": observed.session_name,
                "pane_id": observed.pane_id,
                "origin_session_name": observed.session_name,
            },
        )
    finally:
        server.close()
        directory.cleanup()
    assert response.status_code == 409
    assert "Agent PBX TUI" in response.json()["detail"]


def test_tmux_runtime_live_validation_clears_stale_writer(
    tmp_path: Path,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    client.post(
        "/v1/agents/register",
        headers=headers,
        json={"agent_id": "agent-a", "project": "demo", "metadata": {}},
    )
    app.state.store.upsert_tmux_runtime_mapping(
        entity_id="agent-a",
        server_mode="dedicated",
        server_id="missing-server",
        socket_path=str(tmp_path / "missing.sock"),
        session_name="runtime-a",
        pane_id="%7",
        state="ready",
    )
    app.state.store.acquire_tmux_writer_lease(
        "agent-a",
        client_id="stale-tui",
        ttl_seconds=30,
    )

    fetched = client.get("/v2/tmux/runtimes/agent-a", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["state"] == "server_lost"
    assert fetched.json()["writer_client_id"] is None
    assert fetched.json()["writer_lease_active"] is False

    acquired = client.post(
        "/v2/tmux/runtimes/agent-a/writer/acquire",
        headers=headers,
        json={"client_id": "new-tui", "ttl_seconds": 30},
    )
    assert acquired.status_code == 409
    assert "server_lost" in acquired.json()["detail"]
