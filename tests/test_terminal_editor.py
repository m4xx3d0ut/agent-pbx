from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.files import AgentFileService, FileBrowserError
from agent_pbx.runtime_tmux import RuntimeTmuxPane
from agent_pbx.terminal_editor import (
    ManagedTerminalEditorService,
    terminal_editor_profiles,
)


def agent(root: Path) -> dict[str, object]:
    return {"agent_id": "agent-1", "project": "demo", "metadata": {"cwd": str(root)}}


def test_terminal_editor_profiles_use_structured_executable_argv(tmp_path: Path) -> None:
    editor = tmp_path / "editor with spaces"
    editor.write_text("#!/bin/sh\n", encoding="utf-8")
    editor.chmod(0o700)
    payload = json.dumps(
        {
            "safe": {
                "label": "Safe editor",
                "argv": [str(editor), "--line", "{line}", "--", "{path}"],
            },
            "missing": {"argv": [str(tmp_path / "missing"), "{path}"]},
        }
    )

    profiles = terminal_editor_profiles(
        environ={"AGENT_PBX_TERMINAL_EDITOR_PROFILES": payload}
    )

    assert [profile.profile_id for profile in profiles] == ["safe"]
    command = profiles[0].command(path=tmp_path / "file name.py", line=7)
    assert command == (
        str(editor),
        "--line",
        "7",
        "--",
        str(tmp_path / "file name.py"),
    )


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"Bad ID": {"argv": ["vim"]}}', '{"vim": {"argv": []}}'],
)
def test_terminal_editor_profiles_reject_invalid_configuration(payload: str) -> None:
    with pytest.raises(ValueError):
        terminal_editor_profiles(
            environ={"AGENT_PBX_TERMINAL_EDITOR_PROFILES": payload}
        )


def test_terminal_editor_target_uses_file_service_policy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "linked.py").symlink_to(outside)
    files = AgentFileService()

    resolved = files.resolve_terminal_editor_target(agent(root), path="app.py")
    secret = files.resolve_terminal_editor_target(agent(root), path=".env")
    linked = files.resolve_terminal_editor_target(agent(root), path="linked.py")
    traversal = files.resolve_terminal_editor_target(agent(root), path="../outside.py")

    assert resolved == (root, root / "app.py")
    assert isinstance(secret, FileBrowserError) and secret.code == "SENSITIVE_FILE"
    assert isinstance(linked, FileBrowserError) and linked.code == "PATH_OUTSIDE_CWD"
    assert isinstance(traversal, FileBrowserError) and traversal.code == "PATH_OUTSIDE_CWD"


def test_managed_terminal_editor_lifecycle_isolated_from_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_text("print('ok')\n", encoding="utf-8")
    editor = tmp_path / "fake-editor"
    editor.write_text("#!/bin/sh\n", encoding="utf-8")
    editor.chmod(0o700)
    commands: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(argv))
        if "new-session" in argv:
            return subprocess.CompletedProcess(argv, 0, "%41\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    pane = RuntimeTmuxPane(
        session_name="pbx-editor-placeholder",
        window_id="@8",
        window_name="editor",
        pane_id="%41",
        pane_pid=4241,
        cwd=str(root),
        current_command="fake-editor",
        title="editor",
    )
    monkeypatch.setattr(
        "agent_pbx.terminal_editor.ensure_dedicated_runtime_server",
        lambda _identity: True,
    )
    monkeypatch.setattr(
        "agent_pbx.terminal_editor.restore_dedicated_runtime_exit_policy",
        lambda _identity: None,
    )
    live = [pane]
    monkeypatch.setattr(
        "agent_pbx.terminal_editor.list_runtime_panes", lambda _identity: tuple(live)
    )
    monkeypatch.setattr(
        "agent_pbx.terminal_editor.process_start_ticks", lambda _pid: 77
    )
    service = ManagedTerminalEditorService(
        AgentFileService(),
        runtime_root=tmp_path / "runtimes",
        tmux_bin="tmux-test",
        environ={
            "AGENT_PBX_TERMINAL_EDITOR_PROFILES": json.dumps(
                {"test": {"argv": [str(editor), "+{line}", "--", "{path}"]}}
            ),
            "AGENT_PBX_TERMINAL_EDITOR_DEFAULT": "test",
        },
        runner=runner,
    )

    preview = service.preview(agent(root), path="app.py", line=3)
    started = service.start(agent(root), path="app.py", line=3)
    duplicate = service.start(agent(root), path="app.py", line=3)

    assert preview["ready"] is True
    assert preview["command"] == [str(editor), "+3", "--", str(target)]
    assert started["running"] is True and started["pane_id"] == "%41"
    assert started["server_id"] and started["socket_path"].endswith("tmux.sock")
    assert duplicate["started"] is False
    launch = next(command for command in commands if "new-session" in command)
    assert "exec" in launch[-1] and str(target) in launch[-1]
    assert "codex" not in " ".join(launch)

    with pytest.raises(ValueError, match="already running"):
        other = root / "other.py"
        other.write_text("pass\n", encoding="utf-8")
        service.start(agent(root), path="other.py")

    stopped = service.stop("agent-1")
    assert stopped["stopped"] is True
    assert any("kill-session" in command for command in commands)
    assert service.status("agent-1")["running"] is False


def test_terminal_editor_api_preserves_controller_boundary(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("pass\n", encoding="utf-8")
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    class FakeTerminalEditor:
        def profile_view(self) -> dict[str, object]:
            return {"api_version": "test/v1", "profiles": []}

        def preview(self, record, **kwargs):  # type: ignore[no-untyped-def]
            return {"ready": True, "agent_id": record["agent_id"], **kwargs}

        def start(self, record, **kwargs):  # type: ignore[no-untyped-def]
            return {"running": True, "agent_id": record["agent_id"], **kwargs}

        def status(self, agent_id: str) -> dict[str, object]:
            return {"running": True, "agent_id": agent_id}

        def stop(self, agent_id: str) -> dict[str, object]:
            return {"stopped": True, "agent_id": agent_id}

        def stop_all(self) -> list[object]:
            return []

    app.state.terminal_editor = FakeTerminalEditor()
    with TestClient(app) as client:
        client.post(
            "/v1/agents/register",
            json={
                "agent_id": "agent-1",
                "project": "demo",
                "metadata": {"cwd": str(root)},
            },
        )
        preview = client.post(
            "/v1/agents/agent-1/terminal-editor/preview",
            json={"path": "app.py", "profile_id": "vim", "line": 4},
        )
        started = client.post(
            "/v1/agents/agent-1/terminal-editor",
            json={"path": "app.py"},
        )
        status = client.get("/v1/agents/agent-1/terminal-editor")
        stopped = client.delete("/v1/agents/agent-1/terminal-editor")

    assert preview.status_code == 200 and preview.json()["profile_id"] == "vim"
    assert started.status_code == 200 and started.json()["running"] is True
    assert status.status_code == 200 and status.json()["agent_id"] == "agent-1"
    assert stopped.status_code == 200 and stopped.json()["stopped"] is True
