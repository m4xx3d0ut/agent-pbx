from __future__ import annotations

import subprocess

from agent_pbx import tmux


def test_tmux_parse_pane_line() -> None:
    pane = tmux.parse_pane_line(
        "agent-pbx\t0\t2\t%76\t1\tnode\tagent-pbx\t/home/me/agent-pbx\t142\t45\t2640\toperator-0"
    )

    assert pane is not None
    assert pane.target_label == "agent-pbx:0.2"
    assert pane.pane_id == "%76"
    assert pane.active is True
    assert pane.current_command == "node"
    assert pane.cwd == "/home/me/agent-pbx"
    assert pane.width == 142
    assert pane.height == 45
    assert pane.history_size == 2640
    assert pane.window_name == "operator-0"


def test_tmux_parse_pane_line_keeps_legacy_output_compatible() -> None:
    pane = tmux.parse_pane_line(
        "agent-pbx\t0\t2\t%76\t1\tnode\tagent-pbx\t/home/me/agent-pbx\t142\t45\t2640"
    )

    assert pane is not None
    assert pane.window_name == ""


def test_tmux_choose_pane_for_agent_prefers_matching_codex_pane() -> None:
    agent = {
        "agent_id": "codex-main",
        "project": "agent-pbx",
        "metadata": {"cwd": "/home/me/agent-pbx"},
    }
    panes = [
        tmux.TmuxPane("s", "0", "0", "%1", False, "nvim", "nvim", "/home/me/agent-pbx", 80, 20, 100),
        tmux.TmuxPane("s", "0", "1", "%2", True, "node", "agent-pbx", "/home/me/agent-pbx", 80, 20, 100),
        tmux.TmuxPane("s", "1", "0", "%3", True, "node", "other", "/home/me/other", 80, 20, 100),
    ]

    assert tmux.choose_pane_for_agent(panes, agent) == panes[1]


def test_tmux_choose_pane_for_agent_returns_none_for_ties() -> None:
    agent = {
        "agent_id": "codex-main",
        "project": "agent-pbx",
        "metadata": {"cwd": "/home/me/agent-pbx"},
    }
    panes = [
        tmux.TmuxPane("s", "0", "1", "%1", True, "node", "agent-pbx", "/home/me/agent-pbx", 80, 20, 100),
        tmux.TmuxPane("s", "0", "2", "%2", False, "node", "agent-pbx", "/home/me/agent-pbx", 80, 20, 100),
    ]

    assert tmux.choose_pane_for_agent(panes, agent) is None


def test_tmux_pane_matches_agent_validates_cwd_or_project() -> None:
    agent = {
        "agent_id": "codex-main",
        "project": "agent-pbx",
        "metadata": {"cwd": "/home/me/agent-pbx"},
    }
    cwd_match = tmux.TmuxPane(
        "s", "0", "1", "%1", True, "node", "codex", "/home/me/agent-pbx", 80, 20, 100
    )
    project_match = tmux.TmuxPane(
        "s", "0", "2", "%2", True, "zsh", "agent-pbx", "/tmp", 80, 20, 100
    )
    stale = tmux.TmuxPane(
        "s", "0", "3", "%3", True, "zsh", "shell", "/home/me/other", 80, 20, 100
    )

    assert tmux.pane_matches_agent(cwd_match, agent) is True
    assert tmux.pane_matches_agent(project_match, agent) is True
    assert tmux.pane_matches_agent(stale, agent) is False


def test_tmux_list_and_capture_use_expected_commands(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "list-panes":
            return subprocess.CompletedProcess(
                args,
                0,
                "s\t0\t1\t%1\t0\tnode\tagent\t/tmp/project\t80\t24\t200\tagent\n",
                "",
            )
        if args[1] == "capture-pane":
            return subprocess.CompletedProcess(args, 0, "line 1\nline 2\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    panes = tmux.list_panes()
    captured = tmux.capture_pane("%1", lines=25)

    assert panes[0].pane_id == "%1"
    assert captured == "line 1\nline 2"
    assert calls[0][:3] == ["tmux", "list-panes", "-a"]
    assert calls[1] == ["tmux", "capture-pane", "-p", "-J", "-S", "-25", "-t", "%1"]


def test_tmux_capture_zero_lines_uses_visible_pane(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "visible\n", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    captured = tmux.capture_pane("%1", lines=0)

    assert captured == "visible"
    assert calls == [["tmux", "capture-pane", "-p", "-J", "-S", "0", "-t", "%1"]]


def test_tmux_capture_options_use_expected_flags(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "alternate\n", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    captured = tmux.capture_pane(
        "%1",
        lines=0,
        join_wrapped=False,
        alternate_screen=True,
        copy_mode=True,
        preserve_trailing_spaces=True,
    )

    assert captured == "alternate"
    assert calls == [
        ["tmux", "capture-pane", "-p", "-a", "-M", "-N", "-S", "0", "-t", "%1"]
    ]


def test_tmux_launch_pane_injects_environment(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, "%42\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    pane_id = tmux.launch_pane(
        session_name="operators",
        window_name="operator-0",
        command="codex",
        cwd="/tmp/project",
        env={"AGENT_PBX_TOKEN": "secret", "AGENT_PBX_MCP_URL": "http://pbx/mcp"},
    )

    assert pane_id == "%42"
    assert calls[1] == [
        "tmux",
        "new-session",
        "-d",
        "-P",
        "-F",
        "#{pane_id}",
        "-s",
        "operators",
        "-n",
        "operator-0",
        "-c",
        "/tmp/project",
        "-e",
        "AGENT_PBX_TOKEN=secret",
        "-e",
        "AGENT_PBX_MCP_URL=http://pbx/mcp",
        "codex",
    ]


def test_tmux_kill_pane_uses_target(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.kill_pane("%42")

    assert calls == [["tmux", "kill-pane", "-t", "%42"]]


def test_tmux_send_text_pastes_exact_text_and_enters(monkeypatch) -> None:
    calls: list[list[str]] = []
    loaded_text: list[str] = []
    sleeps: list[float] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "load-buffer":
            loaded_text.append(str(kwargs.get("input")))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux.time, "sleep", lambda seconds: sleeps.append(seconds))

    tmux.send_text("%1", "/status")

    assert loaded_text == ["/status"]
    assert calls[0][0:3] == ["tmux", "load-buffer", "-b"]
    assert calls[0][-1] == "-"
    assert calls[1][0:5] == ["tmux", "paste-buffer", "-p", "-d", "-b"]
    assert calls[1][-2:] == ["-t", "%1"]
    assert calls[2] == ["tmux", "send-keys", "-t", "%1", "C-m"]
    assert sleeps == [tmux.DEFAULT_SUBMIT_DELAY_SECONDS]


def test_tmux_send_text_can_disable_bracketed_paste(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_BRACKETED_PASTE", "0")

    tmux.send_text("%1", "hello")

    assert calls[1][0:4] == ["tmux", "paste-buffer", "-d", "-b"]


def test_tmux_send_literal_keys_types_and_submits(monkeypatch) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)
    monkeypatch.setattr(tmux.time, "sleep", lambda seconds: sleeps.append(seconds))

    tmux.send_literal_keys("%1", "/plan")

    assert calls == [
        ["tmux", "send-keys", "-t", "%1", "-l", "/plan"],
        ["tmux", "send-keys", "-t", "%1", "C-m"],
    ]
    assert sleeps == [tmux.DEFAULT_SUBMIT_DELAY_SECONDS]


def test_tmux_send_key_sends_named_key_without_submit(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.send_key("%1", "Escape")

    assert calls == [["tmux", "send-keys", "-t", "%1", "Escape"]]
