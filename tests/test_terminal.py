from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest
from textual.app import App, ComposeResult

from agent_pbx.terminal.conformance import (
    run_dedicated_tmux_conformance,
    run_outer_tmux_conformance,
)
from agent_pbx.terminal.keys import FunctionKeyPassthroughMap, function_key_sequence
from agent_pbx.terminal.pty import PtyProcess
from agent_pbx.terminal.screen import VirtualTerminal
from agent_pbx.terminal.widget import PbxTerminalSurface, terminal_key_bytes
from agent_pbx.runtime_tmux import tmux_client_attach_command


@pytest.mark.parametrize("number", range(1, 13))
def test_shift_and_xterm_aliases_translate_to_unmodified_function_key(number: int) -> None:
    mapping = FunctionKeyPassthroughMap()
    semantic = mapping.translate([f"shift+f{number}"])
    alias = mapping.translate([f"f{number + 12}"])
    assert semantic is not None
    assert alias is not None
    assert semantic.child_key == f"f{number}"
    assert alias.sequence == semantic.sequence == function_key_sequence(number)


def test_plain_function_key_is_not_passthrough() -> None:
    assert FunctionKeyPassthroughMap().translate(["f2"]) is None


@pytest.mark.parametrize(
    ("key", "character", "expected"),
    [
        ("enter", None, b"\r"),
        ("escape", None, b"\x1b"),
        ("ctrl+c", None, b"\x03"),
        ("alt+x", None, b"\x1bx"),
        ("x", "λ", "λ".encode()),
    ],
)
def test_terminal_key_bytes_cover_control_and_unicode_input(
    key: str,
    character: str | None,
    expected: bytes,
) -> None:
    assert terminal_key_bytes(key, character) == expected


def test_pty_and_virtual_terminal_stream_and_resize() -> None:
    terminal = VirtualTerminal(40, 8)
    with PtyProcess(
        ["/bin/sh", "-c", "printf '\\033[31mhello\\033[0m\\nworld\\n'; sleep 0.1"],
        columns=40,
        rows=8,
    ) as process:
        terminal.feed(process.read_available(timeout=1.0))
        assert "hello" in "\n".join(terminal.snapshot().lines)
        process.resize(60, 12)
        terminal.resize(60, 12)
    assert terminal.snapshot().columns == 60
    assert terminal.snapshot().rows == 12


def test_pty_resize_notifies_child_process_group() -> None:
    with PtyProcess(
        [
            sys.executable,
            "-c",
            "import os, signal\n"
            "def resized(*_args):\n"
            "    size = os.get_terminal_size()\n"
            "    print(f'{size.lines} {size.columns}', flush=True)\n"
            "    raise SystemExit(0)\n"
            "signal.signal(signal.SIGWINCH, resized)\n"
            "print('READY', flush=True)\n"
            "signal.pause()\n",
        ],
        columns=40,
        rows=8,
    ) as process:
        assert b"READY" in process.read_available(timeout=1.0)
        process.resize(60, 12)
        output = process.read_available(timeout=1.0)
    assert b"12 60" in output


def test_virtual_terminal_ignores_private_device_status_queries() -> None:
    terminal = VirtualTerminal(40, 8)
    terminal.feed(b"before\x1b[?996nafter")
    assert "beforeafter" in "\n".join(terminal.snapshot().lines)


def test_virtual_terminal_scrolls_only_the_active_margin_region() -> None:
    terminal = VirtualTerminal(24, 6)
    terminal.feed(
        b"\x1b[1;1Hone"
        b"\x1b[2;1Htwo"
        b"\x1b[3;1H/status"
        b"\x1b[4;1H/statusline"
        b"\x1b[5;1H> /sta"
        b"\x1b[6;1Hfooter"
        b"\x1b[3;5r"
        b"\x1b[3S"
    )
    lines = terminal.snapshot().lines
    assert lines[0].startswith("one")
    assert lines[1].startswith("two")
    assert not lines[2].strip()
    assert not lines[3].strip()
    assert not lines[4].strip()
    assert lines[5].startswith("footer")


def test_virtual_terminal_scroll_down_preserves_rows_outside_margins() -> None:
    terminal = VirtualTerminal(24, 6)
    terminal.feed(
        b"\x1b[1;1Hone"
        b"\x1b[2;1Htwo"
        b"\x1b[3;1Hthree"
        b"\x1b[4;1Hfour"
        b"\x1b[5;1Hfive"
        b"\x1b[6;1Hfooter"
        b"\x1b[2;5r"
        b"\x1b[2T"
    )
    lines = terminal.snapshot().lines
    assert lines[0].startswith("one")
    assert not lines[1].strip()
    assert not lines[2].strip()
    assert lines[3].startswith("two")
    assert lines[4].startswith("three")
    assert lines[5].startswith("footer")


async def test_terminal_surface_streams_input_and_resizes() -> None:
    class TerminalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PbxTerminalSurface(id="terminal", poll_interval=0.01)

    app = TerminalApp()
    async with app.run_test() as pilot:
        surface = app.query_one("#terminal", PbxTerminalSurface)
        surface.attach(
            [
                "/bin/sh",
                "-c",
                "printf 'READY\\n'; IFS= read -r line; printf 'GOT:%s\\n' \"$line\"; sleep .1",
            ],
            target="test-shell",
        )
        await pilot.pause(0.1)
        assert "READY" in surface.render().plain
        assert surface.write(b"hello\r") is True
        await pilot.pause(0.15)
        assert "GOT:hello" in surface.render().plain
        await pilot.resize_terminal(100, 30)
        await pilot.pause()
        assert surface.terminal.columns == surface.size.width
        assert surface.terminal.rows == surface.size.height
        assert surface.process is not None
        surface.terminal.resize(20, 5)
        surface.process.resize(20, 5)
        surface.poll_pty()
        assert surface.terminal.columns == surface.size.width
        assert surface.terminal.rows == surface.size.height
        assert surface.process.columns == surface.size.width
        assert surface.process.rows == surface.size.height


async def test_terminal_surface_uses_renderer_term_instead_of_outer_term() -> None:
    class TerminalApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PbxTerminalSurface(id="terminal", poll_interval=0.01)

    app = TerminalApp()
    async with app.run_test() as pilot:
        surface = app.query_one("#terminal", PbxTerminalSurface)
        surface.attach(
            ["/bin/sh", "-c", "printf '%s' \"$TERM\"; sleep .1"],
            target="test-shell",
            env={**os.environ, "TERM": "screen-256color"},
        )
        await pilot.pause(0.1)
        assert "xterm-256color" in surface.render().plain


async def test_hidden_terminal_surface_retains_last_live_geometry() -> None:
    class TerminalApp(App[None]):
        CSS = """
        #terminal {
            display: block;
            width: 1fr;
            height: 1fr;
        }
        Screen.hidden #terminal {
            display: none;
        }
        """

        def compose(self) -> ComposeResult:
            yield PbxTerminalSurface(id="terminal", poll_interval=0.01)

    app = TerminalApp()
    async with app.run_test(size=(120, 32)) as pilot:
        surface = app.query_one("#terminal", PbxTerminalSurface)
        surface.attach(["/bin/sh", "-c", "sleep 30"], target="test-shell")
        await pilot.pause(0.1)
        assert surface.process is not None
        live_geometry = (surface.process.columns, surface.process.rows)
        assert live_geometry == (surface.size.width, surface.size.height)

        app.screen.add_class("hidden")
        app.refresh(layout=True)
        await pilot.pause(0.1)
        assert surface.size.width == 0
        assert surface.size.height == 0

        surface.poll_pty()
        assert (surface.process.columns, surface.process.rows) == live_geometry


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_normal_tmux_client_conformance_on_dedicated_server() -> None:
    result = run_dedicated_tmux_conformance()
    assert result.ok
    assert result.pty_bytes > 0


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_disposable_client_restart_keeps_runtime_session_alive() -> None:
    tmux = shutil.which("tmux") or "tmux"
    socket = f"agent-pbx-client-restart-{uuid.uuid4().hex[:10]}"
    prefix = [tmux, "-L", socket, "-f", "/dev/null"]
    subprocess.run(
        [*prefix, "new-session", "-d", "-s", "runtime"],
        check=True,
        capture_output=True,
    )
    first: PtyProcess | None = None
    second: PtyProcess | None = None
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env.setdefault("TERM", "xterm-256color")
    try:
        first = PtyProcess(
            [*prefix, "attach-session", "-t", "runtime"], env=env
        ).start()
        first.read_available(timeout=0.25)
        first.close()
        first = None
        assert subprocess.run(
            [*prefix, "has-session", "-t", "runtime"], capture_output=True
        ).returncode == 0
        second = PtyProcess(
            [*prefix, "attach-session", "-t", "runtime"], env=env
        ).start()
        output = second.read_available(timeout=0.25)
        assert second.alive, output.decode("utf-8", errors="replace")
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        subprocess.run([*prefix, "kill-server"], capture_output=True)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_mapped_tmux_client_attaches_to_exact_window_and_pane() -> None:
    tmux = shutil.which("tmux") or "tmux"
    socket_path = f"/tmp/agent-pbx-target-{uuid.uuid4().hex[:10]}.sock"
    prefix = [tmux, "-S", socket_path, "-f", "/dev/null"]
    process: PtyProcess | None = None
    try:
        subprocess.run(
            [*prefix, "new-session", "-d", "-s", "runtime", "sleep 30"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                *prefix,
                "new-window",
                "-d",
                "-t",
                "runtime:",
                "-n",
                "wanted",
                "sleep 30",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [*prefix, "split-window", "-d", "-t", "runtime:wanted", "sleep 30"],
            check=True,
            capture_output=True,
        )
        selected = subprocess.run(
            [
                *prefix,
                "list-panes",
                "-t",
                "runtime:wanted",
                "-F",
                "#{window_id}\t#{pane_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().splitlines()[-1].split("\t")
        window_id, pane_id = selected
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["TERM"] = "xterm-256color"
        process = PtyProcess(
            tmux_client_attach_command(
                {
                    "socket_path": socket_path,
                    "session_name": "runtime",
                    "window_id": window_id,
                    "pane_id": pane_id,
                }
            ),
            env=env,
            columns=111,
            rows=37,
        ).start()
        for _ in range(20):
            client = subprocess.run(
                [*prefix, "list-clients", "-F", "#{window_id}\t#{pane_id}"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if client:
                break
            time.sleep(0.05)
        assert process.alive
        assert client == f"{window_id}\t{pane_id}"
        process.resize(93, 29)
        resized = ""
        for _ in range(20):
            resized = subprocess.run(
                [
                    *prefix,
                    "list-clients",
                    "-F",
                    "#{client_width}x#{client_height}",
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if resized == "93x29":
                break
            time.sleep(0.05)
        assert resized == "93x29"
    finally:
        if process is not None:
            process.close()
        subprocess.run([*prefix, "kill-server"], capture_output=True)


@pytest.mark.skipif(
    not __import__("os").getenv("AGENT_PBX_TEST_OUTER_TMUX"),
    reason="outer tmux mutation requires explicit test opt-in",
)
def test_normal_tmux_client_conformance_on_outer_server() -> None:
    assert run_outer_tmux_conformance().ok
