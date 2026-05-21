# Repository Guidelines

## Project Structure & Module Organization

This repository is currently in the planning stage. `R&D_PLAN.md` defines the intended Agent PBX scope: a local/LAN MCP server plus a lightweight Python TUI for monitoring and interacting with multiple Codex agents. `.gitignore` already reserves common Python, test, local state, and generated artifact paths.

As implementation begins, keep source code under `src/agent_pbx/`, tests under `tests/`, and operational notes under `docs/` or `ops/` as appropriate. Treat `.codex/`, `.env`, `.venv/`, `state/`, `artifacts/`, and `runs/` as local-only or generated data.

## Build, Test, and Development Commands

No build system or package metadata is committed yet. Until tooling is added, use standard Python commands:

- `python -m venv .venv` creates a local virtual environment.
- `source .venv/bin/activate` activates it for development.
- `python -m pytest` should run the test suite once `tests/` exists.
- `python -m agent_pbx` is the preferred future local entry point if the package exposes one.

When adding project tooling, document commands in `README.md` and keep this file aligned.

## Coding Style & Naming Conventions

Use Python for the MCP server and TUI unless the project direction changes. Prefer 4-space indentation, type hints for public functions, small modules with clear responsibilities, and `snake_case` for modules, functions, variables, and CLI commands. Use `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants.

Prefer structured data formats and parsers over ad hoc string handling for callback payloads, spool records, and configuration.

## Testing Guidelines

Use `pytest` for unit and integration tests. Name test files `test_*.py` and mirror source modules where possible, for example `tests/test_spool.py` for `src/agent_pbx/spool.py`. Cover callback payload validation, SQLite spool behavior, MCP tool boundaries, and TUI interaction logic. Avoid relying on LAN services in default tests; gate those behind explicit integration markers.

## Commit & Pull Request Guidelines

This repository has no commit history yet, so no existing convention can be inferred. Use short, imperative commit subjects such as `Add sqlite spool schema` or `Implement agent status callback`. Keep commits focused.

Pull requests should include a concise summary, test results, configuration changes, and any security implications. Include screenshots or terminal captures for TUI changes.

## Security & Configuration Tips

The planned server may run on localhost or a LAN. Default to localhost-only binding, keep secrets out of Git, and do not expose the MCP server to the internet without a documented authentication and network security plan.

<!-- agent-pbx-agent-instructions:v1 start -->
## Agent PBX

When the operator asks you to use Agent PBX, register this session with
`pbx_register_agent` using a stable `agent_id`, the current project name, and
metadata containing the absolute `cwd` and task goal. Registration defaults
`pbx_active=true`; treat that as "Use Agent PBX" being on for this session until
the operator explicitly asks you to stop using PBX or starts a new session.

While `pbx_active=true`, report meaningful progress with `pbx_report_turn`: at
task start, before waiting for input, after important milestones, after test or
deploy results, and at turn completion. Keep summaries brief but actionable;
put detailed notes in `detail`. Do not claim to be using Agent PBX unless you
are also polling for queued commands.

During long-running work that is progressing normally, send a
`status="working"` `pbx_report_turn` check-in at least once every five minutes
until the work completes. Include what is still running and the last meaningful
progress signal, then poll for queued commands.

Every `status="done"` report for repository work must state the git state:
clean, committed, staged, or unstaged. Include the relevant `git status --short`
summary and the commit hash when changes were committed. If the workspace is not
a git repository, say so explicitly.

Poll for queued operator commands with `pbx_poll_commands` before starting
work, after each report, before finishing a turn, and periodically during
long-running work. Handle every returned command in order, then call
`pbx_ack_command` with the result. Do not poll as another agent ID.

Polling is the alert pickup mechanism. A single long-poll call only watches one
window; if the session remains live, keep starting bounded poll windows. After
sending a terminal reply (`status` of `done`, `complete`, `completed`, `failed`,
`canceled`, or `blocked`) while `pbx_active=true`, open one post-reply follow-up
window with
`pbx_poll_commands(wait_seconds=25, max_wait_seconds=600, interval_seconds=5)`.
This repeats long-poll cycles for up to ten minutes, allowing queued follow-ups
to be picked up after the reply. If the 600s window returns empty, stop polling
until the next explicit PBX action or new work.

If a `ping` command is received, treat it as a polling keepalive. Respond with a
`status="working"` `pbx_report_turn` whose summary starts with `Pong`, ack the
ping with `{"pong": true}`, then immediately start another bounded poll window
with `pbx_poll_commands(wait_seconds=25, max_wait_seconds=300, interval_seconds=5)`.

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
`pbx_set_active(active=false)`, then stop polling and reporting through PBX.

If PBX is temporarily unavailable, continue local work, mention the PBX failure
in your next response, and retry registration or polling when practical.
<!-- agent-pbx-agent-instructions:v1 end -->
