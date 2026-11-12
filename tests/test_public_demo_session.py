from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from agent_pbx.demo import session as demo_session
from agent_pbx.demo.session import (
    DEMO_NATIVE_TERMINAL_ENTITY_IDS,
    DEMO_REPORT_ENTITY_IDS,
    DemoSession,
    DemoSessionConfig,
    DemoSessionError,
)


class _FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


def _tmux(session: DemoSession, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            session.config.tmux_bin,
            "-S",
            str(session.runtime_socket),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env=session.environment,
    )


def _capture_pane(session: DemoSession, pane_id: str) -> str:
    return _tmux(session, "capture-pane", "-p", "-t", pane_id).stdout


def _wait_for_pane_text(
    session: DemoSession,
    pane_id: str,
    expected: str,
    *,
    timeout: float = 5,
) -> str:
    deadline = time.monotonic() + timeout
    capture = ""
    while time.monotonic() < deadline:
        capture = _capture_pane(session, pane_id)
        if expected in capture:
            return capture
        time.sleep(0.05)
    raise AssertionError(f"pane {pane_id} did not render {expected!r}:\n{capture}")


def _process_exists(pid: int) -> bool:
    """Return process liveness without relying on Linux-only procfs."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_demo_session_prepare_uses_disposable_paths_and_fake_integrations() -> None:
    session = DemoSession(DemoSessionConfig(ttl_seconds=60))
    root = session.root
    try:
        session.prepare()
        assert session.environment["HOME"] == str(root / "home")
        assert session.environment["XDG_RUNTIME_DIR"] == str(root / "runtime")
        assert session.environment["AGENT_PBX_PROJECT_ROOTS"].startswith(str(root))
        assert session.environment["AGENT_PBX_PUBLIC_DEMO"] == "1"
        assert "TMUX" not in session.environment
        assert "SSH_AUTH_SOCK" not in session.environment
        assert session.fixtures is not None
        assert session.fixtures.codex_bin.is_file()
        tui_environment = session.tui_environment()
        assert tui_environment["TMUX"] == f"{session.runtime_socket},0,0"
        settings = json.loads(session.tui_settings_path.read_text(encoding="utf-8"))
        assert settings["tmux_direct"] is True
        assert settings["embedded_terminal_v2"] is True
        assert settings["legacy_terminal_capture_enabled"] is False
        assert settings["tmux_direct_agent_modes"] == {
            **{agent_id: True for agent_id in DEMO_NATIVE_TERMINAL_ENTITY_IDS},
            **{agent_id: False for agent_id in DEMO_REPORT_ENTITY_IDS},
        }
        assert session.tui_settings_path.stat().st_mode & 0o777 == 0o600
    finally:
        session.close()
    assert not root.exists()


def test_demo_session_rejects_unknown_scenario_and_unbounded_ttl() -> None:
    with pytest.raises(DemoSessionError, match="unsupported"):
        DemoSession(DemoSessionConfig(scenario="live-workstation"))
    with pytest.raises(DemoSessionError, match="TTL"):
        DemoSession(DemoSessionConfig(ttl_seconds=10))
    with pytest.raises(DemoSessionError, match="TTL"):
        DemoSession(DemoSessionConfig(ttl_seconds=3601))


def test_demo_daemon_start_retries_with_a_fresh_loopback_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = DemoSession(DemoSessionConfig(keep=True, ttl_seconds=60))
    session.token = "-leading-hyphen-demo-token"
    processes = [_FakeProcess(98), _FakeProcess(None)]
    ports = iter((41001, 41002))
    spawned: list[tuple[list[str], _FakeProcess]] = []

    def fake_spawn(argv: list[str], *, log_name: str) -> _FakeProcess:
        assert log_name == "agent-pbx.log"
        process = processes[len(spawned)]
        spawned.append((argv, process))
        return process

    def fake_wait_json(
        _url: str,
        *,
        timeout: float,
        process: _FakeProcess | None = None,
        log_name: str | None = None,
    ) -> object:
        assert timeout == demo_session.DEMO_DAEMON_START_TIMEOUT_SECONDS
        assert log_name == "agent-pbx.log"
        if process is processes[0]:
            raise DemoSessionError("transient daemon startup failure")
        return {"ok": True}

    monkeypatch.setattr(demo_session, "reserve_loopback_port", lambda: next(ports))
    monkeypatch.setattr(session, "_spawn", fake_spawn)
    monkeypatch.setattr(session, "_wait_json", fake_wait_json)
    try:
        session._start_daemon()
        assert session.daemon_process is processes[1]
        assert session.api_port == 41002
        assert session.server_url == "http://127.0.0.1:41002"
        assert [argv[argv.index("--port") + 1] for argv, _process in spawned] == [
            "41001",
            "41002",
        ]
        for argv, _process in spawned:
            assert f"--token={session.token}" in argv
            assert "--token" not in argv
    finally:
        session.daemon_process = None
        session.close()


def test_demo_readiness_reports_early_process_exit_and_redacts_token() -> None:
    session = DemoSession(DemoSessionConfig(keep=True, ttl_seconds=60))
    try:
        session.prepare()
        (session.logs_dir / "agent-pbx.log").write_text(
            f"bind failed token={session.token}\n",
            encoding="utf-8",
        )
        with pytest.raises(DemoSessionError, match="exited with code 71") as raised:
            session._wait_json(
                "http://127.0.0.1:1/healthz",
                timeout=0.5,
                process=_FakeProcess(71),
                log_name="agent-pbx.log",
            )
        message = str(raised.value)
        assert session.token not in message
        assert "<redacted-demo-token>" in message
        assert "bind failed" in message
    finally:
        session.close()


@pytest.mark.serial
@pytest.mark.skipif(
    shutil.which("tmux") is None or shutil.which("git") is None,
    reason="public demo integration requires tmux and git",
)
def test_demo_session_starts_real_isolated_server_and_runtime() -> None:
    session = DemoSession(DemoSessionConfig(keep=True, ttl_seconds=60))
    session.token = "-leading-hyphen-demo-token"
    root = session.root
    pane_pids: list[int] = []
    try:
        session.start()
        assert session.daemon_process is not None
        assert session.daemon_process.poll() is None
        assert session.joplin_server is not None
        assert session.joplin_thread is not None
        assert session.joplin_thread.is_alive()

        health = session.request("GET", "/healthz")
        assert health["ok"] is True
        agents = session.request("GET", "/v1/agents?include_hidden=true")
        agents_by_id = {agent["agent_id"]: agent for agent in agents}
        assert set(agents_by_id) >= {
            *DEMO_NATIVE_TERMINAL_ENTITY_IDS,
            *DEMO_REPORT_ENTITY_IDS,
        }
        assert agents_by_id["platform-agent"]["agent_type"] == "caller"
        assert agents_by_id["operator-0"]["agent_type"] == "operator"
        assert agents_by_id["operator-0"]["metadata"]["operator_role"] == "root"
        for fork_id in DEMO_NATIVE_TERMINAL_ENTITY_IDS[2:]:
            assert agents_by_id[fork_id]["agent_type"] == "operator"
            assert agents_by_id[fork_id]["metadata"]["operator_role"] == "fork"
        mappings = session.request("GET", "/v2/tmux/runtimes")
        mapping_text = json.dumps(mappings)
        assert "platform-agent" in mapping_text
        assert str(root) in mapping_text
        mapped_entities = {
            str(mapping["entity_id"])
            for mapping in mappings
            if isinstance(mapping, dict) and mapping.get("entity_id")
        }
        assert mapped_entities == set(DEMO_NATIVE_TERMINAL_ENTITY_IDS)
        mappings_by_id = {str(mapping["entity_id"]): mapping for mapping in mappings}
        assert len({mapping["pane_id"] for mapping in mappings}) == len(mappings)
        assert len({mapping["session_name"] for mapping in mappings}) == len(mappings)
        for entity_id, mapping in mappings_by_id.items():
            assert mapping["socket_path"] == str(session.runtime_socket.resolve())
            assert mapping["codex_session_id"] == f"demo-session-{entity_id}"
            assert mapping["state"] == "ready"
            assert mapping["metadata"]["managed_launch"] is True
            assert Path(mapping["cwd"]).is_relative_to(root)
            if mapping["pane_pid"]:
                pane_pids.append(int(mapping["pane_pid"]))
        assert mappings_by_id["operator-0"]["metadata"]["operator_role"] == "root"
        for fork_id in DEMO_NATIVE_TERMINAL_ENTITY_IDS[2:]:
            assert mappings_by_id[fork_id]["metadata"]["operator_role"] == "fork"

        for agent_id in DEMO_REPORT_ENTITY_IDS:
            reports = session.request("GET", f"/v1/agents/{agent_id}/reports?limit=1")
            assert isinstance(reports, list)
            assert reports

            assert agent_id not in mappings_by_id

        tmux = _tmux(
            session,
            "list-panes",
            "-a",
            "-F",
            "#{pane_id} #{pane_current_command}",
        )
        pane_commands = tmux.stdout.lower()
        assert "python" in pane_commands or "codex" in pane_commands

        platform_capture = _wait_for_pane_text(
            session,
            str(mappings_by_id["platform-agent"]["pane_id"]),
            "Agent · gpt-5.6-sol/high",
        )
        assert "platform-agent" in platform_capture
        root_capture = _wait_for_pane_text(
            session,
            str(mappings_by_id["operator-0"]["pane_id"]),
            "Root Operator · gpt-5.6-sol/xhigh",
        )
        assert "operator-0" in root_capture
        for fork_id in DEMO_NATIVE_TERMINAL_ENTITY_IDS[2:]:
            fork_capture = _wait_for_pane_text(
                session,
                str(mappings_by_id[fork_id]["pane_id"]),
                "Operator fork · gpt-5.6-sol/xhigh",
            )
            assert fork_id in fork_capture

        unique_input = "operator-zero-private-input"
        root_pane = str(mappings_by_id["operator-0"]["pane_id"])
        _tmux(session, "send-keys", "-t", root_pane, "-l", unique_input)
        _tmux(session, "send-keys", "-t", root_pane, "Enter")
        _wait_for_pane_text(session, root_pane, unique_input)
        for entity_id, mapping in mappings_by_id.items():
            if entity_id != "operator-0":
                assert unique_input not in _capture_pane(
                    session, str(mapping["pane_id"])
                )

        implicit_tmux = subprocess.run(
            [
                session.config.tmux_bin,
                "list-panes",
                "-a",
                "-F",
                "#{session_name} #{pane_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env=session.tui_environment(),
        )
        implicit_panes = implicit_tmux.stdout.strip().splitlines()
        assert len(implicit_panes) == len(DEMO_NATIVE_TERMINAL_ENTITY_IDS)
        implicit_sessions = {line.split()[0] for line in implicit_panes}
        assert implicit_sessions == {
            str(mapping["session_name"]) for mapping in mappings
        }
        assert all(_process_exists(pid) for pid in pane_pids)
    finally:
        session.close()
        shutil.rmtree(root, ignore_errors=True)

    assert session.daemon_process is None
    assert session.joplin_server is None
    assert session.joplin_thread is None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_process_exists(pid) for pid in pane_pids):
        time.sleep(0.05)
    assert not any(_process_exists(pid) for pid in pane_pids)
    assert not session.runtime_socket.exists()
