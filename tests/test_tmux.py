from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

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


def test_tmux_parse_pane_line_includes_terminal_and_attachment_state() -> None:
    pane = tmux.parse_pane_line(
        "operators\t2\t0\t%88\t1\tcodex\toperator-5\t/tmp/project\t160\t48\t900\toperator-5\t1\t0"
    )

    assert pane is not None
    assert pane.window_name == "operator-5"
    assert pane.alternate_on is True
    assert pane.session_attached == 0


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


def test_tmux_choose_pane_uses_repo_hints_when_cwd_is_stale() -> None:
    agent = {
        "agent_id": "codex-k1s-workerbee-private",
        "project": "k1s-workerbee-private",
        "metadata": {
            "cwd": "/home/me/git/agent-pbx",
            "repo": "/home/me/git/k1s-wt/k1s-workerbee-private",
            "repos": ["/home/me/git/k1s-wt/k1s-private"],
        },
    }
    stale_cwd_pane = tmux.TmuxPane(
        "agent-pbx",
        "0",
        "0",
        "%1",
        False,
        "node",
        "agent-pbx",
        "/home/me/git/agent-pbx",
        80,
        20,
        100,
    )
    caller_pane = tmux.TmuxPane(
        "k1s",
        "1",
        "5",
        "%135",
        True,
        "node",
        "k1s-workerbee-private",
        "/home/me/git/k1s-wt/k1s-workerbee-private",
        80,
        20,
        100,
    )

    assert (
        tmux.choose_pane_for_agent([stale_cwd_pane, caller_pane], agent)
        == caller_pane
    )
    assert tmux.pane_matches_agent(caller_pane, agent) is True


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


def test_tmux_launch_pane_sets_initial_detached_geometry(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[1] == "new-session":
            return subprocess.CompletedProcess(args, 0, "%42\n", "")
        if args[1] == "display-message":
            return subprocess.CompletedProcess(args, 0, "0\n", "")
        if args[1] == "resize-window":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.launch_pane(
        session_name="operators",
        window_name="operator-5",
        command="codex",
        width=160,
        height=48,
    ) == "%42"
    assert calls[1][-5:] == ["-x", "160", "-y", "48", "codex"]
    assert calls[-1] == [
        "tmux",
        "resize-window",
        "-t",
        "%42",
        "-x",
        "160",
        "-y",
        "48",
    ]


def test_tmux_launch_pane_resizes_existing_detached_session(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1] == "new-window":
            return subprocess.CompletedProcess(args, 0, "%43\n", "")
        if args[1] == "display-message":
            return subprocess.CompletedProcess(args, 0, "0\n", "")
        if args[1] == "resize-window":
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.launch_pane(
        session_name="operators",
        window_name="operator-6",
        command="codex",
        width=160,
        height=48,
    ) == "%43"
    assert calls[-1] == [
        "tmux",
        "resize-window",
        "-t",
        "%43",
        "-x",
        "160",
        "-y",
        "48",
    ]


def test_tmux_launch_pane_does_not_resize_attached_session(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "has-session":
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1] == "new-window":
            return subprocess.CompletedProcess(args, 0, "%44\n", "")
        if args[1] == "display-message":
            return subprocess.CompletedProcess(args, 0, "1\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.launch_pane(
        session_name="operators",
        window_name="operator-7",
        command="codex",
        width=160,
        height=48,
    ) == "%44"
    assert all(call[1] != "resize-window" for call in calls)


def test_tmux_pane_open_rollout_paths_follows_process_tree(tmp_path, monkeypatch) -> None:
    proc_root = tmp_path / "proc"
    codex_home = tmp_path / "codex"
    rollout = codex_home / "sessions" / "2026" / "10" / "02" / "rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}\n", encoding="utf-8")
    unrelated = tmp_path / "other.jsonl"
    unrelated.write_text("{}\n", encoding="utf-8")
    for pid, parent in ((100, 1), (101, 100), (102, 9)):
        root = proc_root / str(pid)
        (root / "fd").mkdir(parents=True)
        (root / "status").write_text(f"Name:\ttest\nPPid:\t{parent}\n", encoding="utf-8")
    (proc_root / "101" / "fd" / "3").symlink_to(rollout)
    (proc_root / "101" / "fd" / "4").symlink_to(unrelated)
    monkeypatch.setattr(tmux, "pane_root_pid", lambda *args, **kwargs: 100)

    assert tmux.pane_open_rollout_paths(
        "%42",
        codex_home=codex_home,
        proc_root=proc_root,
    ) == (Path(rollout),)


def test_tmux_kill_pane_uses_target(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.kill_pane("%42")

    assert calls == [["tmux", "kill-pane", "-t", "%42"]]


def test_tmux_respawn_pane_preserves_target_and_injects_environment(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.respawn_pane(
        "%42",
        command="codex resume session-1",
        cwd="/tmp/project",
        env={"AGENT_PBX_TOKEN": "secret"},
    )

    assert calls == [
        [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "%42",
            "-c",
            "/tmp/project",
            "-e",
            "AGENT_PBX_TOKEN=secret",
            "codex resume session-1",
        ]
    ]


def test_tmux_respawn_pane_rejects_invalid_environment_key(monkeypatch) -> None:
    monkeypatch.setattr(tmux.subprocess, "run", lambda *args, **kwargs: None)

    try:
        tmux.respawn_pane("%42", command="codex", env={"bad-key": "value"})
    except ValueError as exc:
        assert "invalid tmux environment key" in str(exc)
    else:
        raise AssertionError("invalid tmux environment key was accepted")


def test_tmux_pane_is_live_checks_remain_on_exit_state(monkeypatch) -> None:
    outputs = iter(["0\n", "1\n"])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, next(outputs), "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.pane_is_live("%42") is True
    assert tmux.pane_is_live("%42") is False


def test_tmux_pane_remain_on_exit_reads_effective_value(monkeypatch) -> None:
    outputs = iter(["off\n", "on\n"])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, next(outputs), "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.pane_remain_on_exit("%42") is False
    assert tmux.pane_remain_on_exit("%42") is True


def test_tmux_set_pane_remain_on_exit_is_pane_local(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    tmux.set_pane_remain_on_exit("%42", True)
    tmux.set_pane_remain_on_exit("%42", False)

    assert calls == [
        ["tmux", "set-option", "-p", "-t", "%42", "remain-on-exit", "on"],
        ["tmux", "set-option", "-p", "-t", "%42", "remain-on-exit", "off"],
    ]


def test_tmux_pane_dead_status_parses_retained_exit_status(monkeypatch) -> None:
    outputs = iter(["7\n", "\n"])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, next(outputs), "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.pane_dead_status("%42") == 7
    assert tmux.pane_dead_status("%42") is None


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_tmux_retains_failed_respawn_for_same_pane_rollback(tmp_path: Path) -> None:
    tmux_executable = shutil.which("tmux") or "tmux"
    socket_path = tmp_path / "transaction.sock"
    wrapper = tmp_path / "tmux-transaction"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(tmux_executable)} -S {shlex.quote(str(socket_path))} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    tmux_bin = str(wrapper)
    pane_id = ""
    try:
        launched = subprocess.run(
            [
                tmux_bin,
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                "transaction",
                "sleep 30",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        pane_id = launched.stdout.strip()
        assert pane_id

        assert tmux.pane_remain_on_exit(pane_id, tmux_bin=tmux_bin) is False
        tmux.set_pane_remain_on_exit(pane_id, True, tmux_bin=tmux_bin)
        tmux.respawn_pane(pane_id, command="exit 7", tmux_bin=tmux_bin)
        for _ in range(20):
            if not tmux.pane_is_live(pane_id, tmux_bin=tmux_bin):
                break
            time.sleep(0.05)

        assert tmux.pane_exists(pane_id, tmux_bin=tmux_bin) is True
        assert tmux.pane_is_live(pane_id, tmux_bin=tmux_bin) is False
        dead_status = None
        for _ in range(20):
            dead_status = tmux.pane_dead_status(pane_id, tmux_bin=tmux_bin)
            if dead_status is not None:
                break
            time.sleep(0.05)
        assert dead_status == 7

        tmux.respawn_pane(pane_id, command="sleep 30", tmux_bin=tmux_bin)

        assert tmux.pane_exists(pane_id, tmux_bin=tmux_bin) is True
        assert tmux.pane_is_live(pane_id, tmux_bin=tmux_bin) is True
        assert (
            subprocess.run(
                [tmux_bin, "display-message", "-p", "-t", pane_id, "#{pane_id}"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
            == pane_id
        )
    finally:
        subprocess.run(
            [tmux_bin, "kill-server"],
            capture_output=True,
            text=True,
        )


def test_tmux_pane_clipboard_environment_only_returns_desktop_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(tmux, "_pane_pid", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        tmux.Path,
        "read_bytes",
        lambda _path: (
            b"DISPLAY=:1\0XDG_RUNTIME_DIR=/run/user/1000\0"
            b"AGENT_PBX_TOKEN=secret\0CODEX_HOME=/private\0"
            b"WAYLAND_DISPLAY=wayland-1\0"
        ),
    )

    assert tmux.pane_clipboard_environment("%42") == {
        "DISPLAY": ":1",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "WAYLAND_DISPLAY": "wayland-1",
    }


def test_tmux_clipboard_transport_requires_an_attached_ms_client(monkeypatch) -> None:
    monkeypatch.setattr(tmux, "_tmux_command_prefix", lambda *args, **kwargs: ["tmux"])
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1:] == ["show-options", "-gv", "set-clipboard"]:
            return subprocess.CompletedProcess(args, 0, "on\n", "")
        if args[1:] == ["display-message", "-p", "-t", "%42", "#{session_id}"]:
            return subprocess.CompletedProcess(args, 0, "$1\n", "")
        if args[1:] == ["list-clients", "-t", "$1", "-F", "#{client_activity} #{client_name}"]:
            return subprocess.CompletedProcess(args, 0, "12 /dev/pts/5\n", "")
        if args[1:] == ["show-messages", "-T", "-t", "/dev/pts/5"]:
            return subprocess.CompletedProcess(
                args,
                0,
                "terminal features:\nMs: (string) \\033]52;%p1%s;%p2%s\\007\n",
                "",
            )
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    transport = tmux.tmux_clipboard_transport("%42")

    assert transport.available is True
    assert transport.client_name == "/dev/pts/5"
    assert calls[-1][1:] == ["show-messages", "-T", "-t", "/dev/pts/5"]


def test_tmux_clipboard_transport_explains_missing_attached_client(monkeypatch) -> None:
    monkeypatch.setattr(tmux, "_tmux_command_prefix", lambda *args, **kwargs: ["tmux"])

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[1:] == ["show-options", "-gv", "set-clipboard"]:
            return subprocess.CompletedProcess(args, 0, "on\n", "")
        if args[1:] == ["display-message", "-p", "-t", "%42", "#{session_id}"]:
            return subprocess.CompletedProcess(args, 0, "$1\n", "")
        if args[1:] == ["list-clients", "-t", "$1", "-F", "#{client_activity} #{client_name}"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    transport = tmux.tmux_clipboard_transport("%42")

    assert transport.available is False
    assert "no attached client" in transport.reason


def test_tmux_pane_exists_checks_display_message(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "%42\n", "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.pane_exists("%42") is True
    assert calls == [
        ["tmux", "display-message", "-p", "-t", "%42", "#{pane_id}"]
    ]


def test_tmux_pane_start_command_reads_format(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '"codex resume session-1"\n', "")

    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    assert tmux.pane_start_command("%42") == '"codex resume session-1"'
    assert calls == [
        ["tmux", "display-message", "-p", "-t", "%42", "#{pane_start_command}"]
    ]


def test_tmux_quit_pane_sends_q_and_waits(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    waited: list[tuple[str, float]] = []

    def fake_send_literal_keys(
        target: str,
        text: str,
        **kwargs: object,
    ) -> None:
        sent.append((target, text))

    def fake_wait_for_pane_exit(
        target: str,
        *,
        timeout_seconds: float = 0,
        **kwargs: object,
    ) -> bool:
        waited.append((target, timeout_seconds))
        return True

    monkeypatch.setattr(tmux, "send_literal_keys", fake_send_literal_keys)
    monkeypatch.setattr(tmux, "wait_for_pane_exit", fake_wait_for_pane_exit)

    assert tmux.quit_pane("%42", timeout_seconds=1.5) is True
    assert sent == [("%42", "/q")]
    assert waited == [("%42", 1.5)]


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
