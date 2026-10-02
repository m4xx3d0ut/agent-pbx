from __future__ import annotations

import shutil
import subprocess
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
    try:
        first = PtyProcess([*prefix, "attach-session", "-t", "runtime"]).start()
        first.read_available(timeout=0.25)
        first.close()
        first = None
        assert subprocess.run(
            [*prefix, "has-session", "-t", "runtime"], capture_output=True
        ).returncode == 0
        second = PtyProcess([*prefix, "attach-session", "-t", "runtime"]).start()
        second.read_available(timeout=0.25)
        assert second.alive
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        subprocess.run([*prefix, "kill-server"], capture_output=True)


@pytest.mark.skipif(
    not __import__("os").getenv("AGENT_PBX_TEST_OUTER_TMUX"),
    reason="outer tmux mutation requires explicit test opt-in",
)
def test_normal_tmux_client_conformance_on_outer_server() -> None:
    assert run_outer_tmux_conformance().ok
