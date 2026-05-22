from __future__ import annotations

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
    assert "use Agent PBX nohup" in instructions
    assert "Report mode does not require" in instructions
    assert "alert pickup mechanism" in instructions
    assert "pbx_set_active(active=false)" in instructions
    assert "plan_options" in instructions
    assert "Selected plan option:" in instructions
    assert "Do not only write choices" in instructions


def test_runbook_payload_includes_command_guidance() -> None:
    payload = runbook_payload()

    assert payload["title"] == "Agent PBX Runbook"
    assert any("report mode" in item for item in payload["pbx_modes"])
    assert any("nohup" in item for item in payload["pbx_modes"])
    assert any("does not require pbx_poll_commands" in item for item in payload["pbx_modes"])
    assert any("pbx_poll_commands" in item for item in payload["active_loop"])
    assert any("max_wait_seconds=600" in item for item in payload["active_loop"])
    assert any("max_wait_seconds=600" in item for item in payload["post_reply_follow_up"])
    assert any("terminal" in item for item in payload["post_reply_follow_up"])
    assert any("max_wait_seconds=300" in item for item in payload["keepalive"])
    assert any("alert pickup mechanism" in item for item in payload["active_loop"])
    assert any("pong" in item for item in payload["keepalive"])
    assert any("usage estimates" in item for item in payload["usage_guardrails"])
    assert any("five minutes" in item for item in payload["long_running_work"])
    assert "ping" in payload["commands"]
    assert any("git state" in item for item in payload["done_reports"])
    assert any("pbx_set_active" in item for item in payload["session_stop"])
    assert "request_detail" in payload["commands"]
    assert any("plan_options" in item for item in payload["plan_options"])
    assert any("Selected plan option:" in item for item in payload["plan_options"])
    assert any("Do not only write choices" in item for item in payload["plan_options"])


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
