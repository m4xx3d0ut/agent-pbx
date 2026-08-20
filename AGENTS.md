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
If the environment contains `AGENT_PBX_AGENT_ID`, use that value exactly; the
TUI uses it for operator and fork identities. Otherwise choose a project-specific
stable caller id such as `codex-k1s-workerbee-private`, not a generic id shared
across repositories.
Include `metadata.codex_session_id` when the current Codex session id is known;
operator fork creation blocks until callers expose that session id.
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
show the choices in Latest and Thread.

If the operator asks you to "use Agent PBX nohup", switch to nohup mode by
registering or updating metadata with `pbx_mode="nohup"` and
`pbx_nohup_explicit=true`. Nohup mode means reporting plus queued command
pickup: poll with `pbx_poll_commands`, handle commands, ack them with
`pbx_ack_command(agent_id=...)`, honor ping keepalives, and use the post-reply
follow-up window. Do not infer nohup mode from queued commands, pings, plan
requests, or the phrase "use Agent PBX"; it must be explicitly requested.

When operator choice is needed, send `pbx_report_turn(needs_input=true,
plan_options=[...])` with concise, mutually exclusive options. The TUI can queue
the selected option back as `send_input`; in tmux direct mode it can send the
same selection directly into the Codex pane. Treat a message beginning with
`Selected plan option:` as the operator's chosen path, then report what you will
do next and ack after handling. Do not only write choices in `summary` or
`detail`; without `needs_input=true` and `plan_options`, the TUI can show the
text but cannot attach stable choice metadata. Operators reply with `/plan:1
optional notes` or `/plan sel:1 optional notes`; the TUI converts that to the
`Selected plan option:` message. Plan options may be strings or objects with
`id`, `label`, and optional `description`.

During long-running work that is progressing normally, send a
`status="working"` `pbx_report_turn` check-in at least once every five minutes
until the work completes. Include what is still running and the last meaningful
progress signal. In nohup mode, poll for queued commands after each check-in.

Do not report control-plane, TUI, operator-fork, or routing diagnostics as
`status="blocked"` under a caller agent unless the caller's actual assigned work
is blocked. Report those diagnostics from the operator or fork session, or use
`status="working"` with detail when the caller itself remains usable.

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
then call `pbx_ack_command` with this session's `agent_id` and the result. Do
not poll as another agent ID.

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

Built-in TUI Joplin commands such as `/joplin`, `/joplin new`,
`/joplin rename`, `/joplin delete`, `/joplin copy`, `/joplin log start`, and
`/joplin sync` are local operator actions for the TUI. They open the Joplin
tab, call Agent PBX note APIs, edit scoped notes, or in tmux direct mode send
Codex `/copy` to capture the latest response; they are not instructions for an
agent unless the operator separately asks for Joplin note content in the chat.

Operator prompts may contain `@joplin:<note>` references from the TUI. Agent PBX
resolves these references at project scope under the Agent PBX Joplin notebook,
then expands them before delivery by appending a `Joplin Note References`
Markdown section with each referenced note body. Treat that section as
operator-supplied context for the current prompt; do not call Joplin tools again
unless the operator asks you to create, update, or inspect notes directly.

When an operator prompt contains `@caller:<agent>` references, following
`@joplin:<note>` references resolve in the nearest preceding caller's project
note scope until another caller reference appears. This allows one prompt to
attach notes from multiple caller projects.

Operator prompts may contain GitHub references from the TUI with `@pr:<number>`,
`@issue:<number>`, or natural text such as `PR #7` and `issue #12`. Agent PBX
resolves those references against the nearest preceding `@caller:` scope. If an
operator was started from a caller, that caller is implied when no explicit
caller appears, so prompts like `Review PR #7 and assess issue #12.` are scoped
to the source caller. If an unscoped operator prompt contains exactly one
`@caller:`, PR or issue references before that tag also use that caller. Agent
PBX appends a `GitHub PR and Issue References` Markdown section with read-only
PR and issue detail before delivery; treat it as operator-supplied context.

Built-in TUI pull request commands such as `/pr`, `/pr review`, `/pr validate`,
`/pr url`, and `/pr merge` are operator actions. `/pr review` and
`/pr validate` may queue a normal `send_input` prompt asking you to review a PR
or run validation. When asked for PR context, call `pbx_pr_context(agent_id,
pr_number)` and report findings through Agent PBX. Do not merge pull requests;
merge is an operator-only API/TUI action and is not exposed as an agent MCP
tool.

Built-in TUI issue commands such as `/issue`, `/issue mitigate`, `/issue url`,
and `/issue clear` are operator actions. `/issue mitigate` may queue a normal
`send_input` prompt asking you to investigate and mitigate a GitHub issue. When
asked for issue context, call `pbx_issue_context(agent_id, issue_number)` and
report findings through Agent PBX. Do not comment on or close issues; clear is
an operator-only API/TUI action and is not exposed as an agent MCP tool.

`pbx_queue_command` is exposed for the operator, TUI, tests, and control-plane
helpers to queue work for agents. Do not use it as normal agent-side behavior
or to self-queue work; in nohup mode, receive queued work through
`pbx_poll_commands` instead.

Agent PBX supports two agent types. The default `agent_type="caller"` is a
project-working agent that reports or polls according to PBX mode. An
`agent_type="operator"` session coordinates callers through tracked campaigns:
call `pbx_operator_runbook`, create campaigns with one assignment per caller,
inspect caller threads, send follow-ups, report assignment state, and finish the
campaign. Operator agents must not poll or ack commands for caller agent IDs.
Operators can use knowledge links plus executable handoffs for bounded transfers
between operators or review forks without changing fork ownership, campaign
assignment state, or source-session associations.

Operator forks have roles. The default/edit fork may coordinate caller-directed
edits. Review forks use `fork_purpose="review"` and
`access_mode="review_readonly"`; treat the caller source checkout as read-only
and write only under `metadata.work_root`. If review findings require source
edits, route them with `pbx_operator_route_review_escalation`. If review work
needs a new sibling project, request it with
`pbx_operator_request_project_spawn`; the TUI/root operator must approve and
launch that project as a normal caller agent. Do not directly create or attach
project repositories from a review fork outside that operator-mediated path.
If review work needs to transfer domain context, propose it with
`pbx_operator_propose_knowledge_handoff`; review forks may list and inspect
knowledge-link and handoff context, but the root operator/TUI must approve
delivery with `pbx_operator_approve_handoff` or `/operator handoff approve`.
Use `pbx_operator_preflight_handoff` or `/operator handoff preflight` before
delivery when pane readiness, queue/nohup routing, or attached KB context should
be checked without sending anything.
Receiving operators acknowledge with `pbx_operator_ack_handoff` and report
running, blocked, failed, or complete state with `pbx_operator_update_handoff`.
When a knowledge link or handoff produces reusable operating guidance, operators
and review forks may propose PBX-managed KB entries with
`pbx_operator_kb_propose` or `pbx_operator_kb_propose_from_link`. Active KB
publication, update, rejection, retirement, active imports, and export are
root-operator actions. Review forks may search/get active KB entries and
propose new entries, but they must not promote durable knowledge directly.

If the operator asks for a Joplin note or document, call `pbx_joplin_status`
first. If Joplin is available, use `pbx_joplin_create_document` to create
Markdown scoped under the Agent PBX notebook. Mermaid content should be fenced
as Joplin-safe `mermaid` blocks. Do not put secrets in note bodies, titles, or
asset metadata.

If a `ping` command is received in nohup mode, treat it as a polling keepalive.
Respond with a `status="working"` `pbx_report_turn` whose summary starts with
`Pong`, ack the ping with `{"pong": true}`, then immediately start another
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
- `send_key`: if `payload.key` is `escape`, treat it as an operator Escape key
  request. Send Escape to the local CLI when supported; otherwise report that
  key injection is unavailable in this session, then ack.
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
