from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from agent_pbx.terminal.conformance import (
    run_dedicated_tmux_conformance,
    run_outer_tmux_conformance,
)
from agent_pbx.terminal.keys import FunctionKeyPassthroughMap, function_key_sequence
from agent_pbx.terminal.pty import PtyProcess
from agent_pbx.terminal.screen import VirtualTerminal


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
