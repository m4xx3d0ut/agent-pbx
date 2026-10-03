from __future__ import annotations

from agent_pbx.compat import (
    POLLING_ENV,
    THREAD_ENV,
    TERMINAL_CAPTURE_ENV,
    compatibility_flag,
    compatibility_posture,
)


def test_compatibility_flags_are_retained_by_default() -> None:
    posture = compatibility_posture(environ={})

    assert posture.polling is True
    assert posture.thread_tab is True
    assert posture.terminal_capture is True
    assert posture.native_tmux_default is True
    assert posture.event_stream_default is True
    assert posture.retention == "retained_through_v2_0"


def test_compatibility_flags_accept_explicit_disable() -> None:
    environ = {
        POLLING_ENV: "0",
        THREAD_ENV: "disabled",
        TERMINAL_CAPTURE_ENV: "false",
    }
    posture = compatibility_posture(environ=environ)

    assert posture.polling is False
    assert posture.thread_tab is False
    assert posture.terminal_capture is False
    assert compatibility_flag(POLLING_ENV, environ={POLLING_ENV: "invalid"}) is True
