from __future__ import annotations

import json
from pathlib import Path

from agent_pbx.agent import (
    AGENT_INSTRUCTIONS_END,
    AGENT_INSTRUCTIONS_START,
    agent_instructions_markdown,
    install_agent_instructions,
    runbook_payload,
)
from agent_pbx.cli import main


def test_agent_instructions_include_pbx_loop() -> None:
    instructions = agent_instructions_markdown()

    assert AGENT_INSTRUCTIONS_START in instructions
    assert AGENT_INSTRUCTIONS_END in instructions
    assert "pbx_register_agent" in instructions
    assert "pbx_agent_runbook" in instructions
    assert "pbx_queue_command" in instructions
    assert "pbx_pr_context" in instructions
    assert "pbx_issue_context" in instructions
    assert "pbx_poll_commands" in instructions
    assert "request_detail" in instructions
    assert "pbx_ack_command" in instructions
    assert "status=\"done\"" in instructions
    assert "git status --short" in instructions
    assert "status=\"working\"" in instructions
    assert "five minutes" in instructions
    assert "terminal reply" in instructions
    assert "max_wait_seconds=600" in instructions
    assert "ten minutes" in instructions
    assert "max_wait_seconds=300" in instructions
    assert "`ping`" in instructions
    assert '{"pong": true}' in instructions
    assert "Keep routine check-ins and pong reports concise" in instructions
    assert "pbx_active=true" in instructions
    assert "pbx_mode=\"report\"" in instructions
    assert "AGENT_PBX_AGENT_ID" in instructions
    assert "AGENT_PBX_REPORTING_AGENT_ID" in instructions
    assert "reporting_agent_id" in instructions
    assert "codex-k1s-workerbee-private" in instructions
    assert "not a generic id shared" in instructions
    assert "metadata.codex_session_id" in instructions
    assert "use agent pbx for planning" in instructions
    assert "/plan:1" in instructions
    assert "show the choices in Latest and Thread" in instructions
    assert "use Agent PBX nohup" in instructions
    assert "pbx_nohup_explicit=true" in instructions
    assert "never call `pbx_poll_commands`" in instructions
    assert "Do not infer nohup mode" in instructions
    assert "local tmux direct interaction" in instructions
    assert "Report mode does not require" in instructions
    assert "alert pickup mechanism" in instructions
    assert "pbx_set_active(active=false)" in instructions
    assert "status=\"canceled\"" in instructions
    assert "Mark Canceled" in instructions
    assert "plan_options" in instructions
    assert "Selected plan option:" in instructions
    assert "Do not only write choices" in instructions
    assert "Custom slash commands" in instructions
    assert "not MCP tools" in instructions
    assert "or a reason to enter" in instructions
    assert "self-queue work" in instructions
    assert "explicit nohup mode" in instructions
    assert "fork_purpose=\"review\"" in instructions
    assert "access_mode=\"review_readonly\"" in instructions
    assert "metadata.work_root" in instructions
    assert "pbx_operator_route_review_escalation" in instructions
    assert "pbx_operator_request_project_spawn" in instructions
    assert "pbx_operator_ack_handoff" in instructions
    assert "pbx_operator_update_handoff" in instructions
    assert "pbx_operator_preflight_handoff" in instructions
    assert "/operator handoff preflight" in instructions
    assert "pbx_operator_kb_propose" in instructions
    assert "pbx_operator_kb_propose_from_link" in instructions
    assert "pbx_operator_kb_context" in instructions
    assert "operator_kb_candidates" in instructions
    assert "pbx_operator_kb_seed" in instructions
    assert "pbx_operator_kb_update_seed_run" in instructions
    assert "seed_run_id" in instructions
    assert "rejection, retirement" in instructions
    assert "must not promote durable knowledge directly" in instructions
    assert "operator-mediated path" in instructions


def test_runbook_payload_includes_command_guidance() -> None:
    payload = runbook_payload()

    assert payload["title"] == "Agent PBX Runbook"
    assert any("report mode" in item for item in payload["pbx_modes"])
    assert any("pbx_agent_runbook" in item for item in payload["session_start"])
    assert any("AGENT_PBX_AGENT_ID" in item for item in payload["session_start"])
    assert any("AGENT_PBX_REPORTING_AGENT_ID" in item for item in payload["session_start"])
    assert any("reporting_agent_id" in item for item in payload["tool_roles"])
    assert any("codex-k1s-workerbee-private" in item for item in payload["session_start"])
    assert any("not a shared generic id" in item for item in payload["session_start"])
    assert any("pbx_queue_command" in item for item in payload["tool_roles"])
    assert any("pbx_report_turn" in item for item in payload["tool_roles"])
    assert any("pbx_pr_context" in item for item in payload["tool_roles"])
    assert any("pbx_issue_context" in item for item in payload["tool_roles"])
    assert any("project-spawn guidance" in item for item in payload["tool_roles"])
    assert any(
        "pbx_operator_request_project_spawn" in item
        for item in payload["tool_roles"]
    )
    assert any("for planning" in item for item in payload["pbx_modes"])
    assert any("nohup" in item for item in payload["pbx_modes"])
    assert any("never calls pbx_poll_commands" in item for item in payload["pbx_modes"])
    assert any("pbx_nohup_explicit=true" in item for item in payload["pbx_modes"])
    assert any("Do not infer nohup mode" in item for item in payload["pbx_modes"])
    assert any("tmux direct" in item for item in payload["tmux_direct_mode"])
    assert any("Custom TUI slash commands" in item for item in payload["custom_slash_commands"])
    assert any("not MCP tools" in item for item in payload["custom_slash_commands"])
    assert any("not normal agent-side behavior" in item for item in payload["custom_slash_commands"])
    assert any("pbx_poll_commands" in item for item in payload["active_loop"])
    assert any("do not call pbx_poll_commands" in item for item in payload["active_loop"])
    assert any("max_wait_seconds=600" in item for item in payload["active_loop"])
    assert any("max_wait_seconds=600" in item for item in payload["post_reply_follow_up"])
    assert any("pbx_nohup_explicit=true" in item for item in payload["post_reply_follow_up"])
    assert any("do not long poll" in item for item in payload["post_reply_follow_up"])
    assert any("terminal" in item for item in payload["post_reply_follow_up"])
    assert any("max_wait_seconds=300" in item for item in payload["keepalive"])
    assert any("alert pickup mechanism" in item for item in payload["active_loop"])
    assert any("pong" in item for item in payload["keepalive"])
    assert any("usage estimates" in item for item in payload["usage_guardrails"])
    assert any("five minutes" in item for item in payload["long_running_work"])
    assert any("status='canceled'" in item for item in payload["cancellation"])
    assert any("stale-working" in item for item in payload["cancellation"])
    assert "ping" in payload["commands"]
    assert any("git state" in item for item in payload["done_reports"])
    assert any("pbx_set_active" in item for item in payload["session_stop"])
    assert any("nohup polling" in item for item in payload["session_stop"])
    assert "request_detail" in payload["commands"]
    assert "nohup mode" in payload["commands"]["request_detail"]
    assert any("plan_options" in item for item in payload["plan_options"])
    assert any("Selected plan option:" in item for item in payload["plan_options"])
    assert any("Do not only write choices" in item for item in payload["plan_options"])
    assert any("pbx_issue_context" in item for item in payload["issue_mitigation"])
    assert any("operator-only" in item for item in payload["issue_mitigation"])
    assert any(
        "access_mode='review_readonly'" in item
        for item in payload["agent_types"]
    )
    assert any(
        "pbx_operator_route_review_escalation" in item
        for item in payload["agent_types"]
    )
    assert any(
        "pbx_operator_request_project_spawn" in item
        for item in payload["agent_types"]
    )
    assert any("pbx_operator_kb_propose" in item for item in payload["agent_types"])
    assert any("pbx_operator_kb_context" in item for item in payload["agent_types"])
    assert any(
        "pbx_operator_kb_compile_report" in item
        for item in payload["agent_types"]
    )
    assert any(
        "pbx_operator_kb_update_seed_run" in item
        for item in payload["agent_types"]
    )
    assert any(
        "update, promote, reject, retire, export, import, and rebuild indexes"
        in item
        for item in payload["agent_types"]
    )
    assert any("pbx_operator_ack_handoff" in item for item in payload["agent_types"])
    assert any("pbx_operator_update_handoff" in item for item in payload["agent_types"])
    assert any("pbx_operator_preflight_handoff" in item for item in payload["agent_types"])


def test_install_agent_instructions_check_missing_target(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"

    result = install_agent_instructions(target, check=True)

    assert result["ok"] is True
    assert result["exists"] is False
    assert result["installed"] is False
    assert result["would_create"] is True


def test_install_agent_instructions_creates_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "AGENTS.md"

    created = install_agent_instructions(target, append=True, allow_create=True)
    checked = install_agent_instructions(target, check=True)
    unchanged = install_agent_instructions(target, append=True, allow_create=True)

    text = target.read_text(encoding="utf-8")
    assert created["changed"] is True
    assert checked["installed"] is True
    assert unchanged["changed"] is False
    assert text.count(AGENT_INSTRUCTIONS_START) == 1


def test_install_agent_instructions_refreshes_stale_block(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text(
        "# Repository Guidelines\n\n"
        f"{AGENT_INSTRUCTIONS_START}\n"
        "## Agent PBX\n\nOld instructions.\n"
        f"{AGENT_INSTRUCTIONS_END}\n",
        encoding="utf-8",
    )

    result = install_agent_instructions(target, append=True)
    text = target.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert "AGENT_PBX_AGENT_ID" in text
    assert "Old instructions" not in text
    assert text.count(AGENT_INSTRUCTIONS_START) == 1


def test_install_agent_instructions_appends_to_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("# Existing\n\nKeep this.\n", encoding="utf-8")

    result = install_agent_instructions(target, append=True)

    text = target.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert text.startswith("# Existing\n\nKeep this.\n")
    assert AGENT_INSTRUCTIONS_START in text


def test_agent_cli_prints_instructions(capsys) -> None:
    result = main(["agent", "instructions"])

    out = capsys.readouterr().out
    assert result == 0
    assert AGENT_INSTRUCTIONS_START in out
    assert "pbx_poll_commands" in out


def test_agent_cli_prints_runbook(capsys) -> None:
    result = main(["agent", "runbook"])

    out = capsys.readouterr().out
    assert result == 0
    assert "Agent PBX Runbook" in out
    assert "pbx_report_turn" in out


def test_agent_cli_runs_operator_kb_flow_uat(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_operator_kb_flow_uat(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "run": "uat-1",
            "base": kwargs["server"],
            "project": kwargs["project"],
            "results": [],
            "failures": [],
            "cleanup": [],
        }

    monkeypatch.setattr(
        "agent_pbx.cli.run_operator_kb_flow_uat",
        fake_run_operator_kb_flow_uat,
    )

    result = main(
        [
            "uat",
            "operator-kb-flow",
            "--server",
            "http://pbx.test",
            "--token",
            "secret",
            "--project",
            "demo",
            "--tmux-sink",
            "--tmux-session",
            "agent-pbx",
            "--json",
        ]
    )

    out = capsys.readouterr().out
    assert result == 0
    assert '"run": "uat-1"' in out
    assert calls == [
        {
            "server": "http://pbx.test",
            "token": "secret",
            "project": "demo",
            "tmux_sink": True,
            "tmux_session": "agent-pbx",
            "controlled_cwd": None,
            "cleanup": True,
            "timeout": 20.0,
            "state_root": None,
            "tmux_bin": "tmux",
            "stage": None,
            "from_stage": None,
            "skip_tmux": False,
        }
    ]


def test_agent_cli_operator_kb_flow_uat_ci_profile_and_json_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []
    output = tmp_path / "runs" / "uat-result.json"

    def fake_run_operator_kb_flow_uat(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "run": "uat-ci-1",
            "base": kwargs["server"],
            "project": kwargs["project"],
            "results": [],
            "failures": [],
            "cleanup": [],
        }

    monkeypatch.setattr(
        "agent_pbx.cli.run_operator_kb_flow_uat",
        fake_run_operator_kb_flow_uat,
    )

    result = main(
        [
            "uat",
            "operator-kb-flow",
            "--server",
            "http://pbx.test",
            "--token",
            "ci-secret",
            "--tmux-sink",
            "--no-cleanup",
            "--ci",
            "--state-root",
            str(tmp_path / "state"),
            "--tmux-bin",
            "tmux-test",
            "--from-stage",
            "6",
            "--skip-tmux",
            "--output",
            str(output),
        ]
    )

    out = capsys.readouterr().out
    assert result == 0
    assert "# Operator KB Flow UAT uat-ci-1" in out
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["run"] == "uat-ci-1"
    assert calls == [
        {
            "server": "http://pbx.test",
            "token": "ci-secret",
            "project": "agent-pbx-kb-sim",
            "tmux_sink": False,
            "tmux_session": None,
            "controlled_cwd": None,
            "cleanup": True,
            "timeout": 20.0,
            "state_root": tmp_path / "state",
            "tmux_bin": "tmux-test",
            "stage": None,
            "from_stage": "6",
            "skip_tmux": True,
        }
    ]


def test_agent_cli_operator_kb_flow_compare(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_compare_operator_kb_flow_uat_runs(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "run_a": kwargs["run_a"],
            "run_b": kwargs["run_b"],
            "summary_a": {"pass_count": 1, "check_count": 1},
            "summary_b": {"pass_count": 2, "check_count": 2},
            "deltas": {
                "failure_count": 0,
                "warning_count": 1,
                "cleanup_failed_count": 0,
                "non_sim_report_change_count": 0,
                "duration_seconds": 1.25,
                "stage_durations": {"1": 1.25},
            },
        }

    monkeypatch.setattr(
        "agent_pbx.cli.compare_operator_kb_flow_uat_runs",
        fake_compare_operator_kb_flow_uat_runs,
    )

    result = main(
        [
            "uat",
            "compare",
            "--run",
            "run-a",
            "--run",
            "run-b",
            "--state-root",
            str(tmp_path),
        ]
    )

    out = capsys.readouterr().out
    assert result == 0
    assert "# Operator KB Flow UAT Compare run-a -> run-b" in out
    assert calls == [{"run_a": "run-a", "run_b": "run-b", "state_root": tmp_path}]


def test_agent_cli_operator_kb_flow_compare_requires_two_runs(capsys) -> None:
    result = main(["uat", "compare", "--run", "run-a"])

    err = capsys.readouterr().err
    assert result == 2
    assert "exactly two --run values" in err


def test_agent_cli_operator_kb_flow_cleanup_uses_manifest(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_cleanup_operator_kb_flow_uat(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "run": "uat-1",
            "base": "http://pbx.test",
            "cleanup": [{"kind": "agent_dismissed", "agent_id": "sim"}],
            "failed_count": 0,
        }

    monkeypatch.setattr(
        "agent_pbx.cli.cleanup_operator_kb_flow_uat",
        fake_cleanup_operator_kb_flow_uat,
    )

    result = main(["uat", "cleanup", "--run", "uat-1", "--token", "secret"])

    out = capsys.readouterr().out
    assert result == 0
    assert "# Operator KB Flow UAT uat-1" in out
    assert calls == [
        {
            "server": None,
            "token": "secret",
            "run": "uat-1",
            "state_root": None,
            "timeout": 20.0,
            "tmux_bin": "tmux",
        }
    ]


def test_agent_cli_installs_instructions(tmp_path: Path, capsys) -> None:
    target = tmp_path / "AGENTS.md"

    result = main(
        [
            "agent",
            "install",
            "--target",
            str(target),
            "--append",
            "--allow-create",
        ]
    )

    out = capsys.readouterr().out
    assert result == 0
    assert '"changed": true' in out
    assert AGENT_INSTRUCTIONS_START in target.read_text(encoding="utf-8")
