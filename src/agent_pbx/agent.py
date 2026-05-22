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
metadata containing the absolute `cwd`, task goal, and `pbx_mode="report"`.
Registration defaults `pbx_active=true`; treat that as "Use Agent PBX" being on
for this session until the operator explicitly asks you to stop using PBX or
starts a new session.

If you need a refresher on the Agent PBX contract, call `pbx_agent_runbook`.
It is the MCP-exposed source for current mode, polling, command, plan, and done
report guidance.

Default "use Agent PBX" is report mode: send meaningful `pbx_report_turn`
updates at task start, before waiting for input, after important milestones,
after test or deploy results, and at turn completion. Keep summaries brief but
actionable and put detailed notes in `detail`. Report mode does not require
polling queued commands. In report mode, never call `pbx_poll_commands` or
claim PBX queue pickup.

If the operator says "use agent pbx for planning", treat it as report mode with
extra emphasis on structured plan reporting. Before presenting plan choices,
send `pbx_report_turn(needs_input=true, plan_options=[...])` so the TUI can
open its plan-selection modal.

If the operator asks you to "use Agent PBX nohup", switch to nohup mode by
registering or updating metadata with `pbx_mode="nohup"` and
`pbx_nohup_explicit=true`. Nohup mode means reporting plus queued command
pickup: poll with `pbx_poll_commands`, handle commands, ack them with
`pbx_ack_command`, honor ping keepalives, and use the post-reply follow-up
window. Do not infer nohup mode from queued commands, pings, plan requests, or
the phrase "use Agent PBX"; it must be explicitly requested.

When operator choice is needed, send `pbx_report_turn(needs_input=true,
plan_options=[...])` with concise, mutually exclusive options. The TUI can queue
the selected option back as `send_input`; treat a message beginning with
`Selected plan option:` as the operator's chosen path, then report what you will
do next and ack after handling. Do not only write choices in `summary` or
`detail`; without `needs_input=true` and `plan_options`, the TUI can show the
text but cannot provide plan-selection controls.

During long-running work that is progressing normally, send a
`status="working"` `pbx_report_turn` check-in at least once every five minutes
until the work completes. Include what is still running and the last meaningful
progress signal. In nohup mode, poll for queued commands after each check-in.

If you observe cancellation before the session exits, send a final
`status="canceled"` `pbx_report_turn` that states the operator cancelled the CLI
session. If the process exits before you can report, the operator can use
`Mark Canceled` in the TUI to record that state.

Every `status="done"` report for repository work must state the git state:
clean, committed, staged, or unstaged. Include the relevant `git status --short`
summary and the commit hash when changes were committed. If the workspace is not
a git repository, say so explicitly.

In nohup mode, poll for queued operator commands with `pbx_poll_commands`
before starting work, after each report, before finishing a turn, and
periodically during long-running work. Handle every returned command in order,
then call `pbx_ack_command` with the result. Do not poll as another agent ID.

Polling is the alert pickup mechanism for PBX-queued follow-ups in nohup mode. A
single long-poll call only watches one window; if the session remains live, keep
starting bounded poll windows. After sending a terminal reply (`status` of
`done`, `complete`, `completed`, `failed`, `canceled`, or `blocked`) in nohup
mode, open one post-reply follow-up window with
`pbx_poll_commands(wait_seconds=25, max_wait_seconds=600, interval_seconds=5)`.
This repeats long-poll cycles for up to ten minutes, allowing queued follow-ups
to be picked up after the reply. If the 600s window returns empty, stop polling
until the next explicit PBX action or new work.

Before opening any post-reply long-poll window, confirm this session is in
explicit nohup mode. If this is report mode, or if local tmux direct interaction
is being used for follow-up, close out normally after the final report and do
not poll.

Custom slash commands configured in the Agent PBX TUI are local operator
shortcuts for tmux direct mode. They paste configured prompts into the selected
Codex pane; they are not MCP tools, PBX queued commands, or a reason to enter
nohup mode. Treat them like normal operator prompts only after they reach your
session.

`pbx_queue_command` is exposed for the operator, TUI, tests, and control-plane
helpers to queue work for agents. Do not use it as normal agent-side behavior
or to self-queue work; in nohup mode, receive queued work through
`pbx_poll_commands` instead.

If a `ping` command is received in nohup mode, treat it as a polling keepalive.
Respond with a `status="working"` `pbx_report_turn` whose summary starts with
`Pong`, ack the ping with `{{"pong": true}}`, then immediately start another
bounded poll window with
`pbx_poll_commands(wait_seconds=25, max_wait_seconds=300, interval_seconds=5)`.

Keep routine check-ins and pong reports concise. Avoid repeating long logs,
diffs, or unchanged plan text in recurring `status="working"` reports; summarize
the latest signal and reference where details can be reviewed.

Command handling rules:

These rules apply after `pbx_poll_commands` delivers a queued command in
explicit nohup mode:

- `request_detail`: send a new detailed `pbx_report_turn`; do not only ack it.
- `send_input`: treat the message as operator follow-up and respond through a
  new `pbx_report_turn`.
- `start_task`: begin the requested task and report that it started.
- `cancel_task`: stop the current PBX-scoped task when safe and report what was
  stopped.
- `acknowledge`: ack after recording the instruction or status.
- `ping`: send a `status="working"` pong report, ack with `{{"pong": true}}`, and
  restart bounded polling.

If the operator asks you to stop using PBX, send a final report, call
`pbx_set_active(active=false)`, then stop reporting and any nohup polling
through PBX.

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
   `cwd`, `task`, `pbx_mode`, and relevant environment notes. The default
   `pbx_active=true` means Agent PBX visibility is on.
3. Immediately call `pbx_report_turn` with `status="working"` so the operator
   can see that the session is connected.
4. If the local AGENTS.md guidance is unclear or stale, call
   `pbx_agent_runbook` for the current MCP-exposed Agent PBX contract.

## MCP Tool Roles

- `pbx_agent_runbook`: fetch current Agent PBX agent guidance.
- `pbx_register_agent`: register or refresh this session's identity,
  metadata, and PBX active state.
- `pbx_set_active`: turn PBX visibility off or back on for this session.
- `pbx_report_turn`: report status, details, needs-input state, and structured
  plan options.
- `pbx_poll_commands`: in explicit nohup mode only, receive queued operator
  commands for this agent.
- `pbx_ack_command`: in explicit nohup mode only, acknowledge a delivered
  command after handling it.
- `pbx_queue_command`: operator, TUI, test, and control-plane helper for
  queuing commands. Do not use it as normal agent-side behavior or to self-queue
  work.

## PBX Modes

Default "use Agent PBX" means report mode. Register with
`metadata.pbx_mode="report"` and send meaningful `pbx_report_turn` updates for
progress, blockers, plan options, test results, and final outcomes. Report mode
never calls `pbx_poll_commands`; it is the right fit when the operator will
interact through tmux direct mode or the normal Codex session.

"Use agent pbx for planning" is also report mode. It means plan choices should
be sent through structured `pbx_report_turn(needs_input=true,
plan_options=[...])` before or while Codex plan mode is active, so the Agent PBX
TUI can render a modal for choosing among options.

"Use Agent PBX nohup" means nohup mode. Register or update metadata with
`pbx_mode="nohup"` and `pbx_nohup_explicit=true`. Nohup mode includes report
mode plus queued command pickup: poll commands, handle them in order, ack them
after handling, honor pings, and open post-reply follow-up poll windows. Do not
infer nohup mode from queued commands, pings, plan requests, or ordinary "use
Agent PBX" wording.

If the operator asks you to stop using PBX, send a final report, call
`pbx_set_active(active=false)`, then stop reporting and any nohup polling. Do
not call PBX tools again unless the operator starts a new PBX session.

## Report Mode Loop

1. Report milestones, blockers, test results, deployment results, plan options,
   and final outcomes with `pbx_report_turn`.
2. Keep `summary` short enough for the TUI. Put complete notes, logs, and
   reasoning in `detail`.
3. For normal long-running progress, send a `status="working"` check-in at
   least once every five minutes.
4. Do not claim queue pickup in report mode; PBX queue buttons require nohup
   mode, while tmux direct mode bypasses the PBX queue.

## Local Tmux Direct Mode

When the Codex session is running in tmux and the Agent PBX MCP server plus TUI
are on the same physical host, prefer report mode plus tmux direct interaction.
The TUI can paste follow-ups directly into the local Codex pane, so long-polling
is unnecessary and can create confusing perceived activity. Only use nohup
polling in this local tmux workflow when the operator explicitly asks for
`use Agent PBX nohup`.

## Custom TUI Slash Commands

The Agent PBX TUI can define local custom slash commands for tmux direct mode.
These commands paste configured text into the selected Codex pane. They are
operator shortcuts only: they are not MCP tools, not PBX queued commands, and
not a signal to start nohup polling. Respond to the resulting prompt normally
when it appears in your session.

`pbx_queue_command` is the MCP equivalent of those operator/control-plane
queue actions. Agents should receive queued work through `pbx_poll_commands` in
explicit nohup mode rather than queueing work for themselves.

## Nohup Mode Loop

1. Poll with `pbx_poll_commands` before starting work, after each report,
   before ending the turn, and periodically during long-running work.
2. Handle commands in the order received and only call `pbx_ack_command` after
   the command has been handled.
3. Never poll or acknowledge commands for a different `agent_id`.

Polling is what lets Agent PBX alerts and TUI follow-ups reach a live agent.
In nohup mode, do not rely on one long-poll call and then go idle.
After sending a terminal reply (`done`, `complete`, `completed`, `failed`,
`canceled`, or `blocked`), open one post-reply follow-up window with
`pbx_poll_commands(wait_seconds=25, max_wait_seconds=600, interval_seconds=5)`.
The server repeats long-poll cycles until a command arrives or the ten-minute
window expires. If the 600s window returns empty, stop polling until the next
explicit PBX action or new work.

Before opening this post-reply window, confirm the session is explicitly in
nohup mode. In report mode or local tmux direct workflows, send the final report
and stop; do not long poll.

If a `ping` command arrives, send a `status="working"` pong report, ack the
command with `{"pong": true}`, and immediately begin another bounded poll window.
Use `pbx_poll_commands(wait_seconds=25, max_wait_seconds=300, interval_seconds=5)`
for that ping window. This lets an operator intentionally extend the agent's
polling period in five-minute increments while the agent is still live.

Keep recurring check-ins concise. PBX estimates visible usage from poll counts,
report text, command payloads, and ack payloads, so repeated verbose reports can
create avoidable account impact.

## Long-Running Work

If a command, test, build, deploy, or other process is still progressing
normally, send a `status="working"` `pbx_report_turn` check-in at least once
every five minutes until it completes. Include what is running, the latest
progress signal, and whether operator input is needed. In nohup mode, poll for
queued commands after each check-in.

## Cancellation And Stale Status

If cancellation is observed before the session exits, send a final
`status="canceled"` report explaining that the operator cancelled the CLI
session. If a session exits before it can report, Agent PBX keeps the raw last
reported status but exposes an effective stale status after ten minutes for
`working` and `running` agents. Operators can use `Mark Canceled` in the TUI to
write a historical canceled report and clear the working state.

## Plan Options

When you need the operator to choose between implementation paths, include
`needs_input=true` and `plan_options=[...]` in `pbx_report_turn`. Keep each
option short, actionable, and mutually exclusive. Do not only write plan choices
in `summary` or `detail`; the TUI needs these structured fields to render
selection controls. If the TUI sends back a `send_input` message beginning with
`Selected plan option:`, follow that choice and use any `Operator notes:` text
as additional constraints.

## Done Reports

Before sending `status="done"` for repository work, inspect the workspace and
include its git state in `detail`: clean, committed, staged, or unstaged.
Include `git status --short` output when there are staged or unstaged changes,
and include the commit hash when the changes were committed. If the workspace is
not a git repository, state that explicitly.

## Command Handling

These rules apply to commands delivered by `pbx_poll_commands` in explicit
nohup mode.

- `request_detail`: send a new detailed `pbx_report_turn`, then ack the command.
- `send_input`: treat `payload.message` as operator follow-up, act on it, report
  the result, then ack.
- `start_task`: start the requested work and report that it began.
- `cancel_task`: stop the PBX-scoped task when safe, report what stopped, then
  ack.
- `acknowledge`: record the instruction or status and ack.
- `ping`: send a `status="working"` pong report, ack with `{"pong": true}`, and
  restart bounded polling.

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
            "Use Agent PBX as a session-long coordination bus: report mode "
            "records status and history without polling, while explicitly "
            "requested nohup mode adds queued follow-ups, ping keepalives, "
            "long polling, and command acking."
        ),
        "session_start": [
            "Choose a stable agent_id such as codex-agent-pbx-main.",
            "Call pbx_register_agent with project, name, cwd, task, and metadata.pbx_mode; pbx_active defaults true.",
            "Send an initial pbx_report_turn with status='working'.",
            "Call pbx_agent_runbook if local AGENTS.md guidance is unclear or stale.",
        ],
        "tool_roles": [
            "pbx_agent_runbook returns current Agent PBX agent guidance.",
            "pbx_register_agent registers or refreshes this session identity, metadata, and PBX active state.",
            "pbx_set_active turns PBX visibility off or back on for this session.",
            "pbx_report_turn reports status, detail, needs-input state, and structured plan_options.",
            "pbx_poll_commands receives queued operator commands in explicit nohup mode only.",
            "pbx_ack_command acknowledges a delivered command after handling it in explicit nohup mode.",
            "pbx_queue_command is for operators, the TUI, tests, and control-plane helpers; agents should not self-queue work.",
        ],
        "pbx_modes": [
            "Default 'use Agent PBX' means report mode: register with metadata.pbx_mode='report' and send pbx_report_turn updates.",
            "'Use agent pbx for planning' also means report mode, with structured plan_options sent through pbx_report_turn(needs_input=true, plan_options=[...]).",
            "Report mode never calls pbx_poll_commands and never claims queued command pickup.",
            "Use tmux direct or normal Codex interaction for follow-up in report mode.",
            "'Use Agent PBX nohup' means register or update metadata.pbx_mode='nohup' and metadata.pbx_nohup_explicit=true.",
            "Nohup mode adds pbx_poll_commands, queued command handling, pbx_ack_command, ping keepalive, and post-reply follow-up windows.",
            "Do not infer nohup mode from queued commands, pings, plan requests, or ordinary 'use Agent PBX' wording.",
            "pbx_active=false means Agent PBX is off in either mode.",
        ],
        "tmux_direct_mode": [
            "When Codex, Agent PBX MCP, the TUI, and tmux are on the same physical host, prefer report mode plus tmux direct interaction.",
            "Tmux direct follow-up goes directly to the local Codex pane and does not require PBX queue polling.",
            "Only use nohup long polling in local tmux workflows when the operator explicitly asks for 'use Agent PBX nohup'.",
        ],
        "custom_slash_commands": [
            "Custom TUI slash commands are local operator shortcuts for tmux direct mode.",
            "They paste configured prompts into the selected Codex pane; they are not MCP tools or PBX queued commands.",
            "Do not enter nohup mode because a custom slash command was used.",
            "Treat the resulting text like a normal operator prompt after it reaches the Codex session.",
            "pbx_queue_command is the MCP queueing equivalent for operator/control-plane actions, not normal agent-side behavior.",
        ],
        "active_loop": [
            "Use pbx_report_turn for milestones, blockers, test results, deployment results, and final outcomes.",
            "Keep summary concise and put complete notes in detail.",
            "In report mode, do not call pbx_poll_commands and do not claim queued command pickup.",
            "In nohup mode, call pbx_poll_commands before work, after each report, before turn end, and periodically during long work.",
            "Polling is the alert pickup mechanism for PBX-queued follow-up in nohup mode.",
            "In nohup mode, handle commands in order and call pbx_ack_command only after handling.",
            "Never poll or ack commands for another agent_id.",
            "After terminal replies, use pbx_poll_commands(wait_seconds=25, max_wait_seconds=600, interval_seconds=5).",
            "If that 600s post-reply window returns empty, stop polling until the next explicit PBX action or new work.",
        ],
        "post_reply_follow_up": [
            "Terminal statuses are done, complete, completed, failed, canceled, and blocked.",
            "After sending a terminal report in nohup mode, open one 600s follow-up poll window.",
            "Before opening a post-reply window, confirm metadata.pbx_mode='nohup' and metadata.pbx_nohup_explicit=true.",
            "In report mode or local tmux direct workflows, send the terminal report and do not long poll.",
            "Use pbx_poll_commands(wait_seconds=25, max_wait_seconds=600, interval_seconds=5).",
            "If a command arrives, handle it, ack it, report, then apply this terminal follow-up rule again if the new response is terminal.",
            "If the 600s window returns empty, stop polling until the next explicit PBX action or new work.",
        ],
        "keepalive": [
            "A ping command is a nohup-mode polling keepalive, not task input.",
            "On ping, send a status='working' pong report.",
            'Ack the ping with {"pong": true}.',
            "Immediately start another bounded poll window with pbx_poll_commands(wait_seconds=25, max_wait_seconds=300, interval_seconds=5).",
            "Ping extends polling in five-minute increments.",
            "Keep pong reports concise.",
        ],
        "usage_guardrails": [
            "Use five-minute working check-ins for normal long-running progress.",
            "Keep recurring working and pong reports concise.",
            "Avoid repeating long logs, diffs, or unchanged plans in recurring reports.",
            "Watch TUI usage estimates for high polling, report, ping, or token patterns.",
        ],
        "plan_options": [
            "Use pbx_report_turn(needs_input=true, plan_options=[...]) when operator choice is required.",
            "Keep plan options concise, actionable, and mutually exclusive.",
            "Do not only write choices in summary or detail; structured plan_options are what the TUI turns into selection controls.",
            "Treat send_input messages beginning with 'Selected plan option:' as the operator's chosen path.",
            "Use Operator notes in that message as additional constraints.",
        ],
        "long_running_work": [
            "For normally progressing long-running work, send status='working' at least once every five minutes.",
            "Include what is still running and the latest meaningful progress signal.",
            "State whether operator input is needed.",
            "In nohup mode, call pbx_poll_commands after each long-running check-in.",
        ],
        "cancellation": [
            "If cancellation is observed before exit, send a final status='canceled' pbx_report_turn.",
            "If a session exits before it can report, Agent PBX preserves the raw last status and derives stale-working or stale-running after ten minutes.",
            "Operators can use Mark Canceled in the TUI to write a canceled report and clear working state.",
        ],
        "done_reports": [
            "Before status='done' for repository work, inspect git state.",
            "State whether changes are clean, committed, staged, or unstaged.",
            "Include git status --short when there are staged or unstaged changes.",
            "Include the commit hash when changes were committed.",
            "If the workspace is not a git repository, say so explicitly.",
        ],
        "commands": {
            "request_detail": "In nohup mode, send a new detailed pbx_report_turn, then ack.",
            "send_input": "In nohup mode, treat payload.message as operator follow-up, act, report, then ack.",
            "start_task": "In nohup mode, start the requested task and report that it began.",
            "cancel_task": "In nohup mode, stop the PBX-scoped task when safe, report what stopped, then ack.",
            "acknowledge": "In nohup mode, record the instruction or status and ack.",
            "ping": 'In nohup mode, send a status=\'working\' pong report, ack with {"pong": true}, then restart bounded polling.',
        },
        "session_stop": [
            "If the operator asks you to stop using PBX, send a final report.",
            "Call pbx_set_active(active=false) so the TUI shows Use Agent PBX is off.",
            "Stop reporting and any nohup polling through PBX until the operator starts a new PBX session.",
        ],
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
