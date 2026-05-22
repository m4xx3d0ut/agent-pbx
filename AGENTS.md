# Repository Guidelines

## Project Structure & Module Organization

This repository implements Agent PBX: a local/LAN MCP server plus a Python Textual TUI for monitoring and interacting with multiple Codex agents. Core source lives in `src/agent_pbx/`; tests live in `tests/`; local runtime state and generated artifacts stay out of Git.

Operational assets live under `ops/` where needed. Treat `.codex/`, `.env`, `.venv/`, `state/`, `artifacts/`, and `runs/` as local-only or generated data.

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate` creates and enters a local development environment.
- `pip install -e '.[dev]'` installs Agent PBX and test tooling from `pyproject.toml`.
- `pytest` runs the full test suite.
- `agent-pbx mcp restart --port "$AGENT_PBX_PORT" --token "$AGENT_PBX_TOKEN" --debug` restarts the local daemon for manual testing.
- `agent-pbx tui --server "http://127.0.0.1:${AGENT_PBX_PORT}" --token "$AGENT_PBX_TOKEN"` opens the TUI.

## Coding Style & Naming Conventions

Use Python for the MCP server and TUI unless the project direction changes. Prefer 4-space indentation, type hints for public functions, small modules with clear responsibilities, and `snake_case` for modules, functions, variables, and CLI commands. Use `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants.

Prefer structured data formats and parsers over ad hoc string handling for callback payloads, spool records, and configuration.

## Testing Guidelines

Use `pytest` for unit and integration tests. Name test files `test_*.py` and mirror source modules where possible, for example `tests/test_spool.py` for `src/agent_pbx/spool.py`. Cover callback payload validation, SQLite spool behavior, MCP tool boundaries, and TUI interaction logic. Avoid relying on LAN services in default tests; gate those behind explicit integration markers.

## Commit & Pull Request Guidelines

Use short, imperative commit subjects such as `Add latest plan action controls` or `Split Agent PBX report and nohup modes`. Keep commits focused and include tests/docs with behavior changes.

Pull requests should include a concise summary, test results, configuration changes, and any security implications. Include screenshots or terminal captures for TUI changes.

## Security & Configuration Tips

The planned server may run on localhost or a LAN. Default to localhost-only binding, keep secrets out of Git, and do not expose the MCP server to the internet without a documented authentication and network security plan.

<!-- agent-pbx-agent-instructions:v1 start -->
## Agent PBX

When the operator asks you to use Agent PBX, register this session with
`pbx_register_agent` using a stable `agent_id`, the current project name, and
metadata containing the absolute `cwd`, task goal, and `pbx_mode="report"`.
Registration defaults `pbx_active=true`; treat that as "Use Agent PBX" being on
for this session until the operator explicitly asks you to stop using PBX or
starts a new session.

Default "use Agent PBX" is report mode: send meaningful `pbx_report_turn`
updates at task start, before waiting for input, after important milestones,
after test or deploy results, and at turn completion. Keep summaries brief but
actionable and put detailed notes in `detail`. Report mode does not require
polling queued commands.

If the operator asks you to "use Agent PBX nohup", switch to nohup mode by
registering or updating metadata with `pbx_mode="nohup"`. Nohup mode means
reporting plus queued command pickup: poll with `pbx_poll_commands`, handle
commands, ack them with `pbx_ack_command`, honor ping keepalives, and use the
post-reply follow-up window.

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

If a `ping` command is received in nohup mode, treat it as a polling keepalive.
Respond with a `status="working"` `pbx_report_turn` whose summary starts with
`Pong`, ack the ping with `{"pong": true}`, then immediately start another
bounded poll window with
`pbx_poll_commands(wait_seconds=25, max_wait_seconds=300, interval_seconds=5)`.

Keep routine check-ins and pong reports concise. Avoid repeating long logs,
diffs, or unchanged plan text in recurring `status="working"` reports; summarize
the latest signal and reference where details can be reviewed.

Command handling rules:

- `request_detail`: send a new detailed `pbx_report_turn`; do not only ack it.
- `send_input`: treat the message as operator follow-up and respond through a
  new `pbx_report_turn`.
- `start_task`: begin the requested task and report that it started.
- `cancel_task`: stop the current PBX-scoped task when safe and report what was
  stopped.
- `acknowledge`: ack after recording the instruction or status.
- `ping`: send a `status="working"` pong report, ack with `{"pong": true}`, and
  restart bounded polling.

If the operator asks you to stop using PBX, send a final report, call
`pbx_set_active(active=false)`, then stop reporting and any nohup polling
through PBX.

If PBX is temporarily unavailable, continue local work, mention the PBX failure
in your next response, and retry registration or polling when practical.
<!-- agent-pbx-agent-instructions:v1 end -->
