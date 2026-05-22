# Agent PBX

Agent PBX is a local/LAN MCP service for collecting agent turn reports, storing
full response details, and queuing follow-up commands for agents to poll.

The name borrows from telephony. A PBX, traditionally a private branch exchange,
is a switchboard that routes calls between extensions and outside lines. Agent
PBX applies that pattern to agent sessions: agents report into one hub,
operators review the shared history, and follow-up commands are routed back to
the right agent.

## Install

### One-line release install

When release artifacts are published, install the latest wheelhouse with:

```bash
curl -fsSL https://github.com/m4xx3d0ut/agent-pbx/releases/latest/download/install-agent-pbx.sh | sh
agent-pbx --version
```

Set `AGENT_PBX_INSTALL_BASE_URL` when hosting the same artifacts somewhere else.

### Install from downloaded artifacts

Download both release assets into the same directory:

```text
install-agent-pbx.sh
agent-pbx-wheelhouse.tar.gz
```

Then install from that local artifact directory:

```bash
chmod +x install-agent-pbx.sh
AGENT_PBX_INSTALL_BASE_URL="file://$(pwd)" ./install-agent-pbx.sh
agent-pbx --version
```

The installer uses the active virtual environment when `VIRTUAL_ENV` is set.
Otherwise it creates a standalone venv under
`${XDG_DATA_HOME:-~/.local/share}/agent-pbx` and writes an `agent-pbx` wrapper
to `~/.local/bin`.

### Build and install from a local wheelhouse

```bash
scripts/build_wheelhouse.sh --out dist/agent-pbx-wheelhouse
python -m venv .venv
source .venv/bin/activate
python -m pip install --no-index --find-links dist/agent-pbx-wheelhouse agent-pbx
agent-pbx --version
```

The build script also writes `dist/agent-pbx-wheelhouse.tar.gz` and
`dist/install-agent-pbx.sh` for release upload.

Release bundles should include:

```text
install-agent-pbx.sh
agent-pbx-wheelhouse.tar.gz
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
agent-pbx mcp serve --host 127.0.0.1 --port 8765
```

The server defaults to localhost. LAN binding requires bearer-token authentication and explicit operator intent.

## Local Environment

Use `local.env.example` as the template for machine-specific MCP and client
defaults:

```bash
cp local.env.example local.env
$EDITOR local.env
source ./local.env
```

`local.env` is gitignored because it may contain local tokens and absolute
paths. After sourcing it, the CLI reads `AGENT_PBX_HOST`, `AGENT_PBX_PORT`,
`AGENT_PBX_TOKEN`, `AGENT_PBX_SERVER_URL`, `AGENT_PBX_DEBUG`, and
`AGENT_PBX_WORKERBEE_BIN` as defaults. Explicit host, port, server, and token
flags still take precedence.

For the common local workflow:

```bash
source .venv/bin/activate
source ./local.env
agent-pbx mcp restart
agent-pbx mcp status
codex mcp add agent-pbx --url "$AGENT_PBX_MCP_URL"
agent-pbx tui
```

## Background MCP Daemon

Start the shared local MCP daemon in the background:

```bash
agent-pbx mcp start
```

The daemon stores its SQLite DB, metadata, and logs under
`${AGENT_PBX_HOME:-${XDG_DATA_HOME:-~/.local/share}/agent-pbx}` by default. Use
`agent-pbx mcp status`, `agent-pbx mcp restart`, and `agent-pbx mcp stop` for
lifecycle management. Use `agent-pbx mcp serve` or the compatibility command
`agent-pbx serve` only when you want a foreground/debug process.

`agent-pbx mcp status` prints the MCP URL, health URL, DB path, log path,
metadata path, and a Codex connection command:

```bash
codex mcp add agent-pbx --url http://127.0.0.1:8765/mcp
```

If the requested port is already in use, `agent-pbx mcp start` fails fast. Stop
the owning process or choose another port with `--port`. After an editable
reinstall, restart the daemon so it serves the new code:

```bash
agent-pbx mcp restart
agent-pbx mcp status
```

## Agent Instructions

Agent PBX can print or install the AGENTS.md wording that tells Codex-style
agents how to register, report, poll, and handle queued operator commands.

```bash
agent-pbx agent instructions
agent-pbx agent install --check --target AGENTS.md
agent-pbx agent install --append --target AGENTS.md
agent-pbx agent install --append --allow-create --target AGENTS.md
```

For deeper behavior guidance, print the runbook:

```bash
agent-pbx agent runbook
```

Connected agents can also call `pbx_agent_runbook` over MCP. The runbook is
intended for project-specific agent docs and for agents that need a refresher on
session-long PBX behavior.

Agent PBX has two operator-facing modes:

- `use Agent PBX`: report mode. Agents register with
  `metadata.pbx_mode="report"` and send meaningful `pbx_report_turn` updates.
  They must not call `pbx_poll_commands` or claim queued command pickup.
- `use Agent PBX nohup`: nohup mode. Agents register or update metadata with
  `pbx_mode="nohup"` and `pbx_nohup_explicit=true`, report status, poll queued
  commands, ack handled commands, honor pings, and open post-reply follow-up
  windows.

Registration still defaults `pbx_active=true`, which means Agent PBX visibility
is on. `pbx_active=false` means PBX is off for either mode. The guidance
requires `status="done"` reports for repository work to include whether changes
are clean, committed, staged, or unstaged. It also requires normally progressing
long-running work to check in with `status="working"` at least once every five
minutes.

In nohup mode, after a terminal reply, agents should open one bounded
post-reply follow-up window:

```python
pbx_poll_commands(
    agent_id="codex-main",
    wait_seconds=25,
    max_wait_seconds=600,
    interval_seconds=5,
)
```

That repeats long-poll cycles for up to ten minutes after `done`, `complete`,
`completed`, `failed`, `canceled`, or `blocked` reports, so follow-ups queued
after the reply can still be picked up by a live nohup-mode agent. If the 600s
window returns empty, the agent should stop polling until the next explicit PBX
action or new work.

Agents should open that post-reply window only after the operator explicitly
asked for `use Agent PBX nohup`. In report mode, including local tmux-direct
workflows, agents should send the terminal report and stop without long polling.

Use `Ping` in the TUI to intentionally keep a live nohup-mode agent polling
longer. Agents handle `ping` as a keepalive: reply with a `status="working"`
pong report, ack with `{"pong": true}`, then start another 300s bounded poll
window. Each ping extends polling in five-minute increments. In report mode,
`Ping`, `Request Detail`, `Send Input`, and queued plan choices wait in the PBX
queue until the agent polls; use tmux direct mode for no-poll local interaction.

When an operator asks an agent to stop using PBX, the agent should send a final
report, call `pbx_set_active(active=false)`, and stop PBX reporting and any
polling until a new PBX session starts. The TUI `PBX` column shows `report`,
`nohup`, or `off`.

The TUI `Use` column shows a rough PBX-visible usage gauge per agent for the
last hour: estimated tokens plus poll, report, and ping counts. Estimates use
payload text size and fixed weights for polling overhead; they are not exact
Codex billing, but they identify noisy agents and verbose check-ins.

## Planned Local Validation

WorkerBee is used to rebuild and run the containerized MCP/API service with simulated agents and clients. Repository-owned WorkerBee manifests live under `ops/workerbee/`.

```bash
docker build -t agent-pbx:workerbee .
docker run --rm -p 8765:8765 -e AGENT_PBX_TOKEN=dev-token agent-pbx:workerbee
agent-pbx sim-agent --token dev-token --once
agent-pbx sim-client --token dev-token --agent-id sim-agent-1 --message "Proceed"
```

## WorkerBee TUI Status

Set `AGENT_PBX_WORKERBEE_BIN` before starting the Agent PBX daemon to enable
the read-only WorkerBee tab in the TUI. The daemon runs WorkerBee status
commands; the TUI only renders the API result.

```bash
export AGENT_PBX_WORKERBEE_BIN=/home/m4xx3d0ut/git/k1s-wt/k1s-workerbee/.venv/bin/workerbee
agent-pbx mcp restart --token dev-token
agent-pbx tui --token dev-token
```

Agents must register with `metadata.cwd` set to their project directory. When
the selected agent is in a WorkerBee project, the tab shows the WorkerBee
project name, mode, running state, dashboard URLs, app readiness, latest
deployment metadata, ingress URLs, workloads, and validation findings. The v1
tab does not start, stop, deploy, or mutate WorkerBee projects.

## Debug Runs

Use `--debug` on the foreground server or daemon for verbose PBX request and
MCP tool logs. Use `--transcript` on simulator commands to write JSONL CLI
transcripts:

```bash
agent-pbx mcp serve --debug --token dev-token
agent-pbx sim-agent --token dev-token --transcript runs/sim-agent.jsonl
agent-pbx sim-client --token dev-token --agent-id sim-agent-1 \
  --message "Proceed" --transcript runs/sim-client.jsonl
```

For a bounded TUI smoke feed, add `--debug-smoke` or set
`AGENT_PBX_DEBUG_SMOKE=1`. This registers `sun-tzu-smoke-1` through
`sun-tzu-smoke-3`, emits one or two short Sun Tzu quote reports immediately and
then every random 30-60 seconds, and stops after five minutes.

```bash
agent-pbx mcp start --debug --debug-smoke --token dev-token
```

## TUI Notifications

The TUI keeps visual flash and terminal bell notifications off by default for
the localhost workflow. Enable either at launch with environment variables:

```bash
AGENT_PBX_TUI_FLASH=1 agent-pbx tui --token dev-token
AGENT_PBX_TUI_BELL=1 agent-pbx tui --server http://192.168.1.25:8765 \
  --token dev-token
```

For a local container image, bake defaults in with build args:

```bash
docker build --build-arg AGENT_PBX_TUI_FLASH=1 \
  --build-arg AGENT_PBX_TUI_BELL=1 \
  --build-arg AGENT_PBX_TUI_AGENT_BLINK=1 \
  --build-arg AGENT_PBX_TUI_THEME=1337 \
  --build-arg AGENT_PBX_TUI_CUSTOM_THEME_NAME=1337 \
  -t agent-pbx:tui .
```

The same options are available from the TUI `Settings` screen with the `s`
hotkey. Alerts fire for new agent registrations, new reports, and command
acknowledgements. The Agents table also highlights unseen latest reports with
`NEW`, and the top alert bar blinks for unseen latest reports by default. Click
the blinking alert to jump directly to the first unseen agent's `Latest` tab.
Disable that with `AGENT_PBX_TUI_AGENT_BLINK=0` or the `Unseen blink` setting.

The TUI layout defaults to `adaptive`: wide terminals use a side-by-side
Agents/right-pane split, narrow terminals use compact full-width agent views,
and very small terminals use a tiny mode that shows only Agents or Events on
the home screen. Override with
`AGENT_PBX_TUI_LAYOUT=adaptive|split|compact|tiny` or the `Layout` setting.
Selecting an agent in compact or tiny mode opens a full-width view with
`Latest`, `Thread`, and `WorkerBee` tabs; press `b` to return to the agent
list. In tiny mode, press `e` on the home screen to toggle between Agents and
Events.

In split layout, adjust the Agents/right-pane width with `[` and `]`; press
`0` to reset to the default 42% Agents width. The same controls are available
from Settings, and `AGENT_PBX_TUI_SPLIT_PERCENT=25..75` can set the launch
default. Mouse-drag splitters are intentionally deferred because the current
Textual version does not provide a native splitter and keyboard controls work
better over SSH and mobile terminals.

Use `g` followed by `1` through `9` to jump directly to the first nine visible
agents' `Latest` tabs; `g` then `0` jumps to the tenth visible agent. The
sequence is ignored while typing in follow-up inputs, avoiding terminal
`Alt+number` tab-switching conflicts.

Press `Ctrl+P` to open the command palette. Agent PBX adds slash-style operator
commands such as `/detail`, `/ping`, `/tmux`, `/workerbee`, `/theme minimal`,
and `/layout compact`. Use `/plan` when no plan is active to submit Codex's
`/plan` slash command before the selected agent's next prompt. In tmux direct
mode this is sent as two terminal submissions: `/plan`, then your prompt. When
the selected agent has structured plan options, the palette also shows
`/plan latest`, `/plan thread`, and direct entries such as
`/plan latest 2: ...`; selecting one opens a plan-choice modal with optional
notes before sending through PBX or, in tmux direct mode, to the Codex pane.
When tmux direct mode is enabled, the palette also exposes git helpers:
`/gitstatus` sends `!git status`, `/gitdiff` opens an optional target prompt
and sends `!git diff`, and `/gitstageandcommit` asks Codex to stage and commit
the current changes.

Custom palette slash commands can be defined in
`${XDG_CONFIG_HOME:-~/.config}/agent-pbx/slash-commands.json`, or another file
set with `AGENT_PBX_TUI_COMMANDS_FILE=/path/to/slash-commands.json`. They are
local TUI shortcuts for tmux direct mode: Agent PBX sends the configured prompt
into the selected Codex pane, but does not create MCP tools or PBX queued
commands. Reload them without restarting the TUI with `/commands reload`.

```json
{
  "commands": [
    {
      "name": "/review",
      "description": "Ask Codex to review current changes",
      "prompt": "review the current changes"
    },
    {
      "name": "/testfile",
      "description": "Run focused tests for a target",
      "prompt": "run focused tests for {arg}",
      "arg_label": "Target",
      "arg_placeholder": "tests/test_tui.py",
      "arg_required": true
    },
    {
      "name": "/shell",
      "description": "Run a Codex passthrough command",
      "prompt": "!{arg}",
      "arg_label": "Command",
      "arg_required": true
    }
  ]
}
```

Each command needs a slash-prefixed `name` and a non-empty `prompt`. Built-in
palette names win over custom names, and duplicate custom names are skipped.
`{arg}` is the only supported placeholder; commands that use it open a small
input prompt before sending.

Experimental local-only tmux direct mode is available for workflows where the
TUI, MCP server, tmux, and Codex pane all run on the same host. Enable it with
`AGENT_PBX_TUI_TMUX=1` or the `Tmux direct` setting. When enabled, the selected
agent's `Latest` tab shows a captured tmux pane instead of the PBX latest
report and queue buttons. Agent PBX still records normal reports in `Thread`,
but follow-up input is pasted directly into the selected tmux pane, so slash
commands such as `/status` are sent unchanged. The TUI auto-matches panes by
agent cwd/project/title and provides `Auto`, `Select Pane`, and `Detach`
controls for manual correction. Tmux controls are disabled when `tmux` is not
available; set `AGENT_PBX_TUI_TMUX_SHOW=1` to show them on a tmux-capable host
outside an attached tmux client. Press `Ctrl+T` from `Latest` to toggle tmux
direct mode without opening settings. `AGENT_PBX_TUI_TMUX_CAPTURE_LINES=0`
captures only the visible pane by default; set a positive value to include
scrollback when you intentionally need older output. Set
`AGENT_PBX_TUI_TMUX_REFRESH_SECONDS=1.5` to tune the snapshot refresh cadence;
larger values reduce redraw pop at the cost of freshness. The tmux view crops
the Codex `Working` status/input area from captured output, so the main render
focuses on transcript changes instead of the live prompt buffer.
On tiny terminals, tmux direct mode hides the pane action button row, shortens
the editor hotkey text, and focuses the tmux input box so the transcript and
input both stay visible.

When tmux is available, the Agents table may show a local `Live` hint. `active`
means the captured pane text changed recently, `idle 1m` means the pane has not
visibly changed for the idle threshold, and `stale` means the selected pane
target is gone or invalid. When `Live` is `active` and the last PBX report is a
terminal status such as `done` or `completed`, the TUI displays
`tmux-working` in `Status` to make the local pane activity visible. This does
not rewrite the stored PBX report.

The tmux read path is snapshot-based: Agent PBX uses `capture-pane` to show the
rendered pane state a human would see. Tmux paste buffers are used for sending
text, not as a live read source. Follow-up text is loaded into a named tmux
buffer, pasted with bracketed paste, then submitted; set
`AGENT_PBX_TUI_TMUX_BRACKETED_PASTE=0` only if a target pane mishandles
bracketed paste. `pipe-pane` and tmux control mode are better fits for future
debug transcripts or event-driven refresh, but the default UI remains
`capture-pane` plus periodic resync for now.

For local tmux-direct testing, use report mode unless you specifically need PBX
queued follow-ups: tell the agent `use Agent PBX`, not `use Agent PBX nohup`.
The TUI will still show reports, plan alerts, and thread history, while
interaction goes directly through tmux and does not require poll or ping cycles.

TUI checkbox, layout, and theme selections persist between sessions in
`${XDG_CONFIG_HOME:-~/.config}/agent-pbx/tui-settings.json`. Set
`AGENT_PBX_TUI_SETTINGS_FILE=/path/to/tui-settings.json` to use a different
settings file. Explicit environment variables still override saved settings for
that launch.

The default TUI theme uses a cyberpunk palette with neon cyan, magenta, yellow,
and green over a dark terminal base. Use the `Theme` selector in `Settings` or
set `AGENT_PBX_TUI_THEME` to choose another built-in theme. `minimal` uses a
plain black/white terminal base while preserving semantic highlight colors for
alerts, status, and activity. `1337` keeps the black and bright-green terminal
look.

## Custom TUI Theme

The bundled custom theme defaults to the `1337` palette. Rename it with
`AGENT_PBX_TUI_CUSTOM_THEME_NAME`, then select it with `AGENT_PBX_TUI_THEME`:

```bash
AGENT_PBX_TUI_CUSTOM_THEME_NAME=matrix \
AGENT_PBX_TUI_THEME=matrix \
agent-pbx tui --token dev-token
```

Override individual colors with `AGENT_PBX_TUI_CUSTOM_<COLOR>` variables. Valid
keys are `PRIMARY`, `SECONDARY`, `WARNING`, `ERROR`, `SUCCESS`, `ACCENT`,
`FOREGROUND`, `BACKGROUND`, `SURFACE`, `PANEL`, and `BOOST`.

```bash
AGENT_PBX_TUI_THEME=1337 \
AGENT_PBX_TUI_CUSTOM_FOREGROUND="#00ff00" \
AGENT_PBX_TUI_CUSTOM_BACKGROUND="#000000" \
agent-pbx tui --token dev-token
```

## TUI History Thread

Select an agent and open the `Thread` tab to review that agent's historical
reports, follow-up inputs, detail requests, and command results. The thread is
compact by default; selecting a row shows the full report detail or command
payload/result in the thread detail pane.

Use `Hide Agent` or press `d` from the Agents list to remove a stale agent from
the view while keeping its thread history. Use `Purge Agent` or press `D` to
hide the agent and delete its reports, queued commands, command history, and
poll stats. Hidden agents reappear when they register again.

When a report includes `plan_options`, the Latest and Thread tabs show a
plan-choice panel. Agents must set `needs_input=true` and
`plan_options=[...]`; writing choices only in report text creates history, but
no interactive controls. In nohup mode, select an option, add optional notes,
then use `Send Plan Choice` to queue a normal `send_input` follow-up. In report
mode with tmux direct enabled, use `Send to Codex Pane` to paste the same choice
message directly into the local Codex pane without requiring the agent to poll.

Use `Space` on a Thread row to mark it, then export `Item`, `Marked`, or `All`
to Markdown under `artifacts/thread-exports/`. Set
`AGENT_PBX_TUI_EXPORT_DIR=/path/to/exports` to change the destination.

In the follow-up composer, `Enter` sends the input. The message field defaults
to 8 rows. Use `Shift+Enter` to add new lines for longer Markdown or code
snippets when your terminal reports that key distinctly. Use `Ctrl+J` or
`Alt+Enter` as terminal-safe newline fallbacks. Long lines soft-wrap, and the
input expands up to 15 rows with vertical scrolling when needed. The composer
hotkey strip lists editing controls such as `Ctrl+W` for delete previous word.

The thread is also available through the API:

```bash
curl -H "Authorization: Bearer $AGENT_PBX_TOKEN" \
  "http://127.0.0.1:${AGENT_PBX_PORT:-8765}/v1/agents/<agent-id>/thread"
```

Hide an agent from `/v1/agents` while retaining its thread:

```bash
curl -X DELETE -H "Authorization: Bearer $AGENT_PBX_TOKEN" \
  "http://127.0.0.1:${AGENT_PBX_PORT:-8765}/v1/agents/<agent-id>"
```

Add `?delete_thread=true` to purge that agent's thread data while hiding it.

## Agent PBX Session Semantics

When an agent is asked to use Agent PBX for a session, it should keep using PBX
until the operator explicitly asks it to stop or starts a new session. Default
`use Agent PBX` is report mode: the agent reports meaningful turn status with
`pbx_report_turn` and must not poll. `use Agent PBX nohup` is explicit queue
pickup mode: the agent reports, registers `pbx_nohup_explicit=true`, polls
queued commands with `pbx_poll_commands`, and acks handled commands with
`pbx_ack_command`.

`Request Detail` queues a `request_detail` command. A new detailed report appears
only after the target agent polls that command and responds with a new
`pbx_report_turn`, so it is intended for nohup-mode agents. For local
tmux-direct workflows, send the request directly to the Codex pane instead.

Agent status is last-reported state plus a derived effective state. If an agent
last reported `working` or `running` and has not checked in for ten minutes, the
API/TUI shows `stale-working` or `stale-running` while preserving the raw last
reported status. Use `Mark Canceled` in the Latest controls when an agent was
cancelled from its CLI session and can no longer report cleanup itself; this
writes a `status="canceled"` report to the thread and clears the working state.
