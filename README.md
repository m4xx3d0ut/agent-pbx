# Agent PBX

Agent PBX is a local/LAN development service for collecting agent turn reports, storing full response details, and queuing follow-up commands for agents to poll.

## Install

### One-line release install

When release artifacts are published, install the latest wheelhouse with:

```bash
curl -fsSL https://github.com/the-cm-collective/agent-pbx/releases/latest/download/install-agent-pbx.sh | sh
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
agent-pbx serve --host 127.0.0.1 --port 8765
```

The server defaults to localhost. LAN binding requires bearer-token authentication and explicit operator intent.

## Planned Local Validation

WorkerBee is used to rebuild and run the containerized MCP/API service with simulated agents and clients. Repository-owned WorkerBee manifests live under `ops/workerbee/`.

```bash
docker build -t agent-pbx:workerbee .
docker run --rm -p 8765:8765 -e AGENT_PBX_TOKEN=dev-token agent-pbx:workerbee
agent-pbx sim-agent --token dev-token --once
agent-pbx sim-client --token dev-token --agent-id sim-agent-1 --message "Proceed"
```

## Debug Runs

Use `--debug` on the server for verbose PBX request and MCP tool logs. Use
`--transcript` on simulator commands to write JSONL CLI transcripts:

```bash
agent-pbx serve --debug --token dev-token
agent-pbx sim-agent --token dev-token --transcript runs/sim-agent.jsonl
agent-pbx sim-client --token dev-token --agent-id sim-agent-1 \
  --message "Proceed" --transcript runs/sim-client.jsonl
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

The same options are available as `Visual flash` and `Terminal bell` checkboxes
inside the TUI. Alerts fire for new agent registrations, new reports, and
command acknowledgements. The Agents table also highlights unseen latest reports
with `NEW`, and the top alert bar blinks for unseen latest reports by default.
Disable that with `AGENT_PBX_TUI_AGENT_BLINK=0` or the `Unseen blink` checkbox.

TUI checkbox and theme selections persist between sessions in
`${XDG_CONFIG_HOME:-~/.config}/agent-pbx/tui-settings.json`. Set
`AGENT_PBX_TUI_SETTINGS_FILE=/path/to/tui-settings.json` to use a different
settings file. Explicit environment variables still override saved settings for
that launch.

The default TUI theme uses GitHub Dark-style colors. Use
`AGENT_PBX_TUI_THEME=1337` or the `1337 theme` checkbox for a black and
bright-green terminal look.

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

Use `Space` on a Thread row to mark it, then export `Item`, `Marked`, or `All`
to Markdown under `artifacts/thread-exports/`. Set
`AGENT_PBX_TUI_EXPORT_DIR=/path/to/exports` to change the destination.

In the follow-up composer, `Enter` sends the input. Use `Shift+Enter` to add
new lines for longer Markdown or code snippets; the input expands up to 15 rows
and scrolls when needed.

The thread is also available through the API:

```bash
curl -H "Authorization: Bearer $AGENT_PBX_TOKEN" \
  "http://127.0.0.1:${AGENT_PBX_PORT:-8765}/v1/agents/<agent-id>/thread"
```

## Agent PBX Session Semantics

When an agent is asked to use Agent PBX for a session, it should keep using PBX
until the operator explicitly asks it to stop or starts a new session. During
that active PBX session, the agent should report meaningful turn status with
`pbx_report_turn`, poll queued commands with `pbx_poll_commands`, and ack handled
commands with `pbx_ack_command`.

`Request Detail` queues a `request_detail` command. A new detailed report appears
only after the target agent polls that command and responds with a new
`pbx_report_turn`.
