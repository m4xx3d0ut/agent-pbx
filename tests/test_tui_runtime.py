from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import pytest

from agent_pbx.tui_runtime import (
    TUI_RUNTIME_ROOT_ENV,
    TuiRunRecorder,
    _process_start_ticks,
    run_tui_recorded,
)


def journal_events(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_tui_run_recorder_cleans_marker_and_redacts_stderr(tmp_path: Path) -> None:
    token = "super-secret-token"
    recorder = TuiRunRecorder.start(
        server=f"http://127.0.0.1:8765/?token={token}",
        token=token,
        root=tmp_path,
    )

    assert recorder.marker_path.exists()
    with recorder.capture_stderr():
        print(f"Authorization: Bearer {token}", file=sys.stderr)
    recorder.finish("clean")

    assert not recorder.marker_path.exists()
    events = journal_events(tmp_path)
    assert [event["event"] for event in events] == ["tui_started", "tui_exited"]
    assert events[-1]["status"] == "clean"
    assert token not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    stderr = recorder.stderr_path.read_text(encoding="utf-8")
    assert token not in stderr
    assert "Authorization: Bearer <redacted>" in stderr
    assert stat.S_IMODE(recorder.stderr_path.stat().st_mode) == 0o600


def test_run_tui_persists_redacted_exception_and_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "do-not-persist"

    class FakeApp:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self) -> None:
            print(f"token={token}", file=sys.stderr)
            raise RuntimeError(f"failed with bearer {token}")

    monkeypatch.setenv(TUI_RUNTIME_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr("agent_pbx.tui.AgentPBXTUI", FakeApp)

    with pytest.raises(RuntimeError, match="failed with bearer"):
        run_tui_recorded(server="http://127.0.0.1:8765", token=token)

    events_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert token not in events_text
    events = journal_events(tmp_path)
    assert events[-1]["event"] == "tui_exited"
    assert events[-1]["status"] == "crashed"
    assert events[-1]["exception_type"] == "RuntimeError"
    assert "<redacted>" in str(events[-1]["traceback"])
    stderr_files = list((tmp_path / "stderr").glob("*.log"))
    assert len(stderr_files) == 1
    assert token not in stderr_files[0].read_text(encoding="utf-8")
    assert not list((tmp_path / "active").glob("*.json"))


def test_recorded_tui_launch_uses_host_watchdog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = object()
    launched: list[object] = []

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            self.host_terminal_identity = kwargs["host_terminal_identity"]

    monkeypatch.setenv(TUI_RUNTIME_ROOT_ENV, str(tmp_path))
    monkeypatch.setattr("agent_pbx.tui.AgentPBXTUI", FakeApp)
    monkeypatch.setattr(
        "agent_pbx.ui.runtime.capture_interactive_tui_host_identity",
        lambda: identity,
    )
    monkeypatch.setattr(
        "agent_pbx.ui.runtime.run_tui_with_host_watchdog",
        launched.append,
    )

    run_tui_recorded(server="http://127.0.0.1:8765", token="secret")

    assert len(launched) == 1
    assert launched[0].host_terminal_identity is identity


def test_tui_run_recorder_preserves_stale_marker_as_unclean_evidence(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    active.mkdir(parents=True)
    stale = active / "999999-1-stale.json"
    stale.write_text(
        json.dumps(
            {
                "event": "tui_running",
                "run_id": "stale-run",
                "pid": 999999,
                "process_start_ticks": 1,
                "started_at": 1.0,
                "server_origin": "http://127.0.0.1:8765",
            }
        ),
        encoding="utf-8",
    )

    recorder = TuiRunRecorder.start(
        server="http://127.0.0.1:8765",
        token=None,
        root=tmp_path,
    )
    recorder.finish("clean")

    events = journal_events(tmp_path)
    unclean = [
        event for event in events if event["event"] == "tui_unclean_exit_detected"
    ]
    assert len(unclean) == 1
    assert unclean[0]["run_id"] == "stale-run"
    assert not stale.exists()
    assert (tmp_path / "unclean" / stale.name).exists()


def test_tui_run_recorder_does_not_reap_another_live_client(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir(parents=True)
    ticks = _process_start_ticks(os.getpid())
    live = active / f"{os.getpid()}-{ticks or 0}-live.json"
    live.write_text(
        json.dumps(
            {
                "event": "tui_running",
                "run_id": "live-run",
                "pid": os.getpid(),
                "process_start_ticks": ticks,
                "started_at": 1.0,
                "server_origin": "http://127.0.0.1:8765",
            }
        ),
        encoding="utf-8",
    )

    recorder = TuiRunRecorder.start(
        server="http://127.0.0.1:8765",
        token=None,
        root=tmp_path,
    )
    recorder.finish("clean")

    assert live.exists()
    assert not any(
        event["event"] == "tui_unclean_exit_detected"
        for event in journal_events(tmp_path)
    )
