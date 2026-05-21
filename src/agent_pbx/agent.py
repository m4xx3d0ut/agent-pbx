from __future__ import annotations

from pathlib import Path
from typing import Any


AGENT_INSTRUCTIONS_START = "<!-- agent-pbx-agent-instructions:v1 start -->"
AGENT_INSTRUCTIONS_END = "<!-- agent-pbx-agent-instructions:v1 end -->"


def agent_instructions_markdown() -> str:
    return f"""\
{AGENT_INSTRUCTIONS_START}
## Agent PBX

When the operator asks you to use Agent PBX, register this session with
`pbx_register_agent` using a stable `agent_id`, the current project name, and
metadata containing the absolute `cwd` and task goal. Treat Agent PBX as active
until the operator explicitly asks you to stop using PBX or starts a new
session.

While PBX is active, report meaningful progress with `pbx_report_turn`: at task
start, before waiting for input, after important milestones, after test or
deploy results, and at turn completion. Keep summaries brief but actionable;
put detailed notes in `detail`.

Every `status="done"` report for repository work must state the git state:
clean, committed, staged, or unstaged. Include the relevant `git status --short`
summary and the commit hash when changes were committed. If the workspace is not
a git repository, say so explicitly.

Poll for queued operator commands with `pbx_poll_commands` before starting
work, after each report, before finishing a turn, and periodically during
long-running work. Handle every returned command in order, then call
`pbx_ack_command` with the result. Do not poll as another agent ID.

Command handling rules:

- `request_detail`: send a new detailed `pbx_report_turn`; do not only ack it.
- `send_input`: treat the message as operator follow-up and respond through a
  new `pbx_report_turn`.
- `start_task`: begin the requested task and report that it started.
- `cancel_task`: stop the current PBX-scoped task when safe and report what was
  stopped.
- `acknowledge`: ack after recording the instruction or status.

If PBX is temporarily unavailable, continue local work, mention the PBX failure
in your next response, and retry registration or polling when practical.
{AGENT_INSTRUCTIONS_END}
"""


def runbook_markdown() -> str:
    return """\
# Agent PBX Runbook

Agent PBX is a local/LAN coordination bus for agent sessions. The agent still
does normal work in its shell and code workspace; PBX adds shared status,
operator follow-up queues, detailed report history, and TUI visibility.

## Session Start

1. Choose a stable `agent_id`, for example `codex-agent-pbx-main`.
2. Call `pbx_register_agent` with the current project and metadata such as
   `cwd`, `task`, and relevant environment notes.
3. Immediately call `pbx_report_turn` with `status="working"` so the operator
   can see that the session is connected.

## Active Loop

1. Poll with `pbx_poll_commands` before starting work, after each report,
   before ending the turn, and periodically during long-running work.
2. Report milestones, blockers, test results, deployment results, and final
   outcomes with `pbx_report_turn`.
3. Keep `summary` short enough for the TUI. Put complete notes, logs, and
   reasoning in `detail`.
4. Handle commands in the order received and only call `pbx_ack_command` after
   the command has been handled.
5. Never poll or acknowledge commands for a different `agent_id`.

## Done Reports

Before sending `status="done"` for repository work, inspect the workspace and
include its git state in `detail`: clean, committed, staged, or unstaged.
Include `git status --short` output when there are staged or unstaged changes,
and include the commit hash when the changes were committed. If the workspace is
not a git repository, state that explicitly.

## Command Handling

- `request_detail`: send a new detailed `pbx_report_turn`, then ack the command.
- `send_input`: treat `payload.message` as operator follow-up, act on it, report
  the result, then ack.
- `start_task`: start the requested work and report that it began.
- `cancel_task`: stop the PBX-scoped task when safe, report what stopped, then
  ack.
- `acknowledge`: record the instruction or status and ack.

## Failure Modes

If PBX is unavailable, continue local work when possible and mention the PBX
failure in your next operator-facing response. If a command fails after being
claimed, report the failure through `pbx_report_turn` and ack with an error
result so the queue does not stall.
"""


def runbook_payload() -> dict[str, Any]:
    return {
        "title": "Agent PBX Runbook",
        "summary": (
            "Use Agent PBX as a session-long coordination bus: register once, "
            "report meaningful progress, poll commands regularly, and ack only "
            "after handling each command."
        ),
        "session_start": [
            "Choose a stable agent_id such as codex-agent-pbx-main.",
            "Call pbx_register_agent with project, name, cwd, task, and metadata.",
            "Send an initial pbx_report_turn with status='working'.",
        ],
        "active_loop": [
            "Call pbx_poll_commands before work, after each report, before turn end, and periodically during long work.",
            "Use pbx_report_turn for milestones, blockers, test results, deployment results, and final outcomes.",
            "Keep summary concise and put complete notes in detail.",
            "Handle commands in order and call pbx_ack_command only after handling.",
            "Never poll or ack commands for another agent_id.",
        ],
        "done_reports": [
            "Before status='done' for repository work, inspect git state.",
            "State whether changes are clean, committed, staged, or unstaged.",
            "Include git status --short when there are staged or unstaged changes.",
            "Include the commit hash when changes were committed.",
            "If the workspace is not a git repository, say so explicitly.",
        ],
        "commands": {
            "request_detail": "Send a new detailed pbx_report_turn, then ack.",
            "send_input": "Treat payload.message as operator follow-up, act, report, then ack.",
            "start_task": "Start the requested task and report that it began.",
            "cancel_task": "Stop the PBX-scoped task when safe, report what stopped, then ack.",
            "acknowledge": "Record the instruction or status and ack.",
        },
        "failure_modes": [
            "If PBX is unavailable, continue local work when possible and retry later.",
            "If a claimed command fails, report the failure and ack with an error result.",
        ],
    }


def install_agent_instructions(
    target: Path,
    *,
    check: bool = False,
    append: bool = False,
    allow_create: bool = False,
) -> dict[str, Any]:
    target = target.expanduser()
    exists = target.exists()
    text = target.read_text(encoding="utf-8") if exists else ""
    has_start = AGENT_INSTRUCTIONS_START in text
    has_end = AGENT_INSTRUCTIONS_END in text
    installed = has_start and has_end
    result = {
        "ok": True,
        "path": str(target),
        "exists": exists,
        "installed": installed,
        "changed": False,
        "would_create": not exists,
        "would_append": exists and not installed,
    }

    if has_start != has_end:
        raise ValueError(f"{target} contains partial Agent PBX instruction markers")
    if check or installed:
        return result
    if not exists and not allow_create:
        raise ValueError(f"{target} does not exist; use --allow-create to create it")
    if exists and not append:
        raise ValueError(f"{target} has no Agent PBX block; use --append to add it")

    block = agent_instructions_markdown().rstrip()
    if text and not text.endswith("\n"):
        text += "\n"
    if text.strip():
        text = f"{text}\n{block}\n"
    else:
        text = f"# Repository Guidelines\n\n{block}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    result.update({"exists": True, "installed": True, "changed": True})
    return result
