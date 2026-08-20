from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_pbx import uat


def test_resolve_tmux_session_auto_prefers_agent_pbx(monkeypatch) -> None:
    def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
        assert argv[:2] == ["tmux", "list-sessions"]
        return SimpleNamespace(
            returncode=0,
            stdout="other\nagent-pbx\nagent-pbx-operators\n",
            stderr="",
        )

    monkeypatch.setattr(uat.subprocess, "run", fake_run)

    session, sessions, warnings = uat.resolve_tmux_session("auto")

    assert session == "agent-pbx"
    assert sessions == ["other", "agent-pbx", "agent-pbx-operators"]
    assert warnings == []


def test_resolve_tmux_session_auto_warns_when_no_agent_pbx_session(monkeypatch) -> None:
    def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
        assert argv[:2] == ["tmux-test", "list-sessions"]
        return SimpleNamespace(returncode=0, stdout="demo\n", stderr="")

    monkeypatch.setattr(uat.subprocess, "run", fake_run)

    session, sessions, warnings = uat.resolve_tmux_session("auto", tmux_bin="tmux-test")

    assert session == "agent-pbx"
    assert sessions == ["demo"]
    assert warnings == ["auto tmux session found no existing Agent PBX session"]


def test_resolve_tmux_session_auto_prefers_tui_operator_env(monkeypatch) -> None:
    def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
        assert argv[:2] == ["tmux", "list-sessions"]
        return SimpleNamespace(returncode=0, stdout="custom-ops\nagent-pbx\n", stderr="")

    monkeypatch.setenv("AGENT_PBX_TUI_OPERATOR_TMUX_SESSION", "custom-ops")
    monkeypatch.setattr(uat.subprocess, "run", fake_run)

    session, sessions, warnings = uat.resolve_tmux_session("auto")

    assert session == "custom-ops"
    assert sessions == ["custom-ops", "agent-pbx"]
    assert warnings == []


def test_uat_manifest_round_trips_private_file(tmp_path: Path) -> None:
    payload = {
        "format": uat.UAT_MANIFEST_FORMAT,
        "run": "kb-sim-1",
        "status": "running",
        "base": "http://127.0.0.1:8765",
    }

    path = uat.write_uat_manifest(payload, tmp_path)
    loaded = uat.read_uat_manifest("kb-sim-1", tmp_path)

    assert path == tmp_path / "uat" / "kb-sim-1.json"
    assert loaded == payload
    assert path.stat().st_mode & 0o777 == 0o600


def test_selected_uat_stages_include_required_setup() -> None:
    assert uat.selected_uat_stages(stage="6") == ("0", "1", "6")
    assert uat.selected_uat_stages(from_stage="6") == ("0", "1", "2", "6", "7")
    assert uat.selected_uat_stages(stage="5") == ("0", "1", "2", "5")


def test_operator_kb_flow_manifest_only_lists_created_agents(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        uat,
        "resolve_tmux_session",
        lambda *_args, **_kwargs: ("agent-pbx", ["agent-pbx"], []),
    )

    harness = uat.OperatorKbFlowUAT(
        server="http://pbx.test",
        token=None,
        state_root=tmp_path,
    )
    payload = harness.manifest_payload("starting")

    assert payload["synthetic_agents"] == [harness.sim_a, harness.sim_b, harness.sim_fork]
    assert harness.sim_tmux not in payload["synthetic_agents"]


def test_operator_kb_flow_cleanup_resource_states(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        uat,
        "resolve_tmux_session",
        lambda *_args, **_kwargs: ("agent-pbx", ["agent-pbx"], []),
    )
    harness = uat.OperatorKbFlowUAT(
        server="http://pbx.test",
        token=None,
        state_root=tmp_path,
    )
    harness.cleanup = [
        {"kind": "kb_retired", "kb_id": "kb-1", "status": "retired"},
        {"kind": "agent_dismissed", "agent_id": "agent-1"},
        {"kind": "tmux_pane_already_absent", "tmux_pane_id": "%1", "returncode": 0},
    ]

    assert harness.cleanup_resource_states() == [
        {
            "resource_type": "kb",
            "resource_id": "kb-1",
            "state": "retired",
            "terminal": True,
        },
        {
            "resource_type": "agent",
            "resource_id": "agent-1",
            "state": "dismissed",
            "terminal": True,
        },
        {
            "resource_type": "tmux_pane",
            "resource_id": "%1",
            "state": "tmux_pane_already_absent",
            "terminal": True,
        },
    ]


def test_operator_kb_flow_cleanup_failure_count_tracks_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        uat,
        "resolve_tmux_session",
        lambda *_args, **_kwargs: ("agent-pbx", ["agent-pbx"], []),
    )
    harness = uat.OperatorKbFlowUAT(
        server="http://pbx.test",
        token=None,
        state_root=tmp_path,
    )
    harness.cleanup = [
        {"kind": "agent_dismissed"},
        {"kind": "agent_dismiss_failed"},
        {"kind": "tmux_pane_killed", "returncode": 1},
    ]

    assert harness.cleanup_failure_count() == 2


def test_cleanup_operator_kb_flow_uat_uses_manifest_base_and_updates_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest = {
        "format": uat.UAT_MANIFEST_FORMAT,
        "run": "kb-sim-1",
        "status": "failed",
        "base": "http://pbx.test",
        "cleanup_operator_agent_id": "operator-a",
        "active_kb_ids": ["kb-active"],
        "secret_kb_ids": ["kb-secret"],
        "synthetic_agents": ["operator-a", "operator-b", "operator-a"],
        "tmux_pane_id": "%42",
    }
    uat.write_uat_manifest(manifest, tmp_path)
    requests: list[tuple[str, str, Any | None]] = []
    tmux_calls: list[list[str]] = []

    class FakeClient:
        def __init__(self, *, server: str, token: str | None, timeout: float) -> None:
            self.server = server.rstrip("/")
            self.token = token
            self.timeout = timeout

        def request(
            self,
            method: str,
            path: str,
            body: Any | None = None,
            query: dict[str, Any] | None = None,
            expect: tuple[int, ...] = (200,),
        ) -> dict[str, Any]:
            _ = (query, expect)
            requests.append((method, path, body))
            if path.endswith("/retire"):
                return {"status": "retired"}
            if path.endswith("/reject"):
                return {"status": "rejected"}
            if method == "DELETE":
                return {"dismissed_at": 123.0}
            raise AssertionError(path)

    def fake_run(argv: list[str], **_: object) -> SimpleNamespace:
        tmux_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(uat, "UATClient", FakeClient)
    monkeypatch.setattr(uat.subprocess, "run", fake_run)

    result = uat.cleanup_operator_kb_flow_uat(
        server=None,
        token="secret",
        run="kb-sim-1",
        state_root=tmp_path,
        tmux_bin="tmux-test",
    )

    assert result["base"] == "http://pbx.test"
    assert result["failed_count"] == 0
    assert tmux_calls == [["tmux-test", "kill-pane", "-t", "%42"]]
    assert [request[:2] for request in requests] == [
        ("POST", "/v1/operator/kb/kb-active/retire"),
        ("POST", "/v1/operator/kb/kb-secret/reject"),
        ("DELETE", "/v1/agents/operator-a"),
        ("DELETE", "/v1/agents/operator-b"),
    ]
    updated = json.loads((tmp_path / "uat" / "kb-sim-1.json").read_text())
    assert updated["status"] == "cleanup_retry_complete"
    assert updated["last_cleanup_retry"]["run"] == "kb-sim-1"


def test_cleanup_operator_kb_flow_uat_counts_tmux_cleanup_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    uat.write_uat_manifest(
        {
            "format": uat.UAT_MANIFEST_FORMAT,
            "run": "kb-sim-2",
            "status": "failed",
            "base": "http://pbx.test",
            "cleanup_operator_agent_id": "operator-a",
            "tmux_pane_id": "%43",
        },
        tmp_path,
    )

    class FakeClient:
        def __init__(self, *, server: str, token: str | None, timeout: float) -> None:
            self.server = server
            _ = (token, timeout)

        def request(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            raise AssertionError("no API cleanup expected")

    monkeypatch.setattr(uat, "UATClient", FakeClient)
    monkeypatch.setattr(
        uat.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="permission denied",
        ),
    )

    result = uat.cleanup_operator_kb_flow_uat(
        server=None,
        token=None,
        run="kb-sim-2",
        state_root=tmp_path,
    )

    assert result["failed_count"] == 1
    assert result["cleanup"][0]["kind"] == "tmux_pane_kill_failed"


def test_cleanup_operator_kb_flow_uat_treats_missing_tmux_pane_as_clean(
    monkeypatch,
    tmp_path: Path,
) -> None:
    uat.write_uat_manifest(
        {
            "format": uat.UAT_MANIFEST_FORMAT,
            "run": "kb-sim-3",
            "status": "complete",
            "base": "http://pbx.test",
            "cleanup_operator_agent_id": "operator-a",
            "tmux_pane_id": "%44",
        },
        tmp_path,
    )

    class FakeClient:
        def __init__(self, *, server: str, token: str | None, timeout: float) -> None:
            self.server = server
            _ = (token, timeout)

        def request(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            raise AssertionError("no API cleanup expected")

    monkeypatch.setattr(uat, "UATClient", FakeClient)
    monkeypatch.setattr(
        uat.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="can't find pane: %44",
        ),
    )

    result = uat.cleanup_operator_kb_flow_uat(
        server=None,
        token=None,
        run="kb-sim-3",
        state_root=tmp_path,
    )

    assert result["failed_count"] == 0
    assert result["cleanup"][0]["kind"] == "tmux_pane_already_absent"


def test_tmux_pane_liveness_uses_list_panes_membership(monkeypatch) -> None:
    monkeypatch.setattr(
        uat.subprocess,
        "run",
        lambda argv, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="%1\n%2\n",
            stderr="",
            argv=argv,
        ),
    )

    assert uat.tmux_pane_liveness("tmux-test", "%2") == {
        "tmux_pane_id": "%2",
        "live": True,
        "returncode": 0,
        "stderr": "",
    }
    assert uat.tmux_pane_liveness("tmux-test", "%3")["live"] is False


def test_compare_operator_kb_flow_uat_runs_reports_deltas(tmp_path: Path) -> None:
    base = {
        "format": uat.UAT_MANIFEST_FORMAT,
        "status": "complete",
        "base": "http://pbx.test",
        "project": "demo",
    }
    uat.write_uat_manifest(
        {
            **base,
            "run": "run-a",
            "results": [{"stage": "1", "ok": True, "evidence": {}}],
            "failures": [],
            "cleanup": [{"kind": "agent_dismissed"}],
            "cleanup_failed_count": 0,
            "non_sim_report_change_count": 0,
            "duration_seconds": 2.0,
            "stage_timings": {"1": {"duration_seconds": 1.0}},
        },
        tmp_path,
    )
    uat.write_uat_manifest(
        {
            **base,
            "run": "run-b",
            "results": [
                {"stage": "1", "ok": True, "evidence": {}},
                {"stage": "2", "ok": False, "evidence": {"warnings": ["warn"]}},
            ],
            "failures": ["2: failed"],
            "cleanup": [{"kind": "agent_dismissed"}, {"kind": "tmux_pane_killed"}],
            "cleanup_failed_count": 0,
            "non_sim_report_change_count": 1,
            "duration_seconds": 5.5,
            "stage_timings": {
                "1": {"duration_seconds": 1.5},
                "2": {"duration_seconds": 2.0},
            },
        },
        tmp_path,
    )

    result = uat.compare_operator_kb_flow_uat_runs(
        run_a="run-a",
        run_b="run-b",
        state_root=tmp_path,
    )

    assert result["deltas"]["check_count"] == 1
    assert result["deltas"]["failure_count"] == 1
    assert result["deltas"]["warning_count"] == 1
    assert result["deltas"]["non_sim_report_change_count"] == 1
    assert result["deltas"]["duration_seconds"] == 3.5
    assert result["deltas"]["stage_durations"]["1"] == 0.5
    assert result["deltas"]["stage_durations"]["2"] == 2.0
