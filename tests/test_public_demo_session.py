from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from agent_pbx.demo.session import DemoSession, DemoSessionConfig, DemoSessionError


def test_demo_session_prepare_uses_disposable_paths_and_fake_integrations() -> None:
    root = Path(tempfile.mkdtemp(prefix="apbx-demo-prepare-"))
    root.rmdir()
    session = DemoSession(DemoSessionConfig(root=root, ttl_seconds=60))
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


@pytest.mark.serial
@pytest.mark.skipif(
    shutil.which("tmux") is None or shutil.which("git") is None,
    reason="public demo integration requires tmux and git",
)
def test_demo_session_starts_real_isolated_server_and_runtime() -> None:
    root = Path(tempfile.mkdtemp(prefix="apbx-demo-test-"))
    session = DemoSession(DemoSessionConfig(root=root, keep=True, ttl_seconds=60))
    try:
        session.start()
        assert session.daemon_process is not None
        assert session.daemon_process.poll() is None
        assert session.joplin_process is not None
        assert session.joplin_process.poll() is None

        health = session.request("GET", "/healthz")
        assert health["ok"] is True
        agents = session.request("GET", "/v1/agents?include_hidden=true")
        assert {agent["agent_id"] for agent in agents} >= {
            "platform-agent",
            "release-review",
            "operator-0",
        }
        assert next(
            agent for agent in agents if agent["agent_id"] == "operator-0"
        )["agent_type"] == "operator"
        mappings = session.request("GET", "/v2/tmux/runtimes")
        mapping_text = json.dumps(mappings)
        assert "platform-agent" in mapping_text
        assert str(root) in mapping_text

        tmux = subprocess.run(
            [
                session.config.tmux_bin,
                "-S",
                str(session.runtime_socket),
                "list-panes",
                "-a",
                "-F",
                "#{pane_id} #{pane_current_command}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert "python" in tmux.stdout or "codex" in tmux.stdout
    finally:
        session.close()
        shutil.rmtree(root, ignore_errors=True)

    assert session.daemon_process is None
    assert session.joplin_process is None
