#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${AGENT_PBX_DEMO_OUT_DIR:-${ROOT_DIR}/artifacts/tui-demo}"
STATE_ROOT="${AGENT_PBX_DEMO_STATE_ROOT:-${OUT_DIR}/state}"
DB_PATH="${AGENT_PBX_DEMO_DB:-${STATE_ROOT}/agent-pbx-demo.sqlite}"
PORT="${AGENT_PBX_DEMO_PORT:-8771}"
HOST="${AGENT_PBX_DEMO_HOST:-127.0.0.1}"
TOKEN="${AGENT_PBX_DEMO_TOKEN:-demo-token}"
SERVER="${AGENT_PBX_DEMO_SERVER:-http://${HOST}:${PORT}}"
SESSION="${AGENT_PBX_DEMO_SESSION:-agent-pbx-demo-$$}"
COLS="${AGENT_PBX_DEMO_COLS:-120}"
ROWS="${AGENT_PBX_DEMO_ROWS:-36}"
TITLE="${AGENT_PBX_DEMO_TITLE:-Agent PBX TUI demo}"
THEME="${AGENT_PBX_DEMO_THEME:-cyberpunk}"
LAYOUT="${AGENT_PBX_DEMO_LAYOUT:-split}"
MAX_RECORD_SECONDS="${AGENT_PBX_DEMO_MAX_RECORD_SECONDS:-35}"
CAST_PATH="${AGENT_PBX_DEMO_CAST:-${OUT_DIR}/agent-pbx-tui-demo.cast}"
GIF_PATH="${AGENT_PBX_DEMO_GIF:-${OUT_DIR}/agent-pbx-tui-demo.gif}"
if [[ -z "${AGENT_PBX_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/agent-pbx" ]]; then
  AGENT_PBX_BIN="${ROOT_DIR}/.venv/bin/agent-pbx"
elif [[ -z "${AGENT_PBX_BIN:-}" ]]; then
  AGENT_PBX_BIN="agent-pbx"
fi
ASCIINEMA_BIN="${ASCIINEMA_BIN:-asciinema}"
AGG_BIN="${AGG_BIN:-agg}"
WORKERBEE_BIN="${AGENT_PBX_WORKERBEE_BIN:-}"
RENDER=1
START_MCP=1
KEEP_MCP="${AGENT_PBX_DEMO_KEEP_MCP:-0}"
MANUAL=0
CHECK_ONLY=0

usage() {
  cat <<'EOF'
usage: scripts/record_tui_demo.sh [options]

Record a deterministic Agent PBX TUI demo cast and render it to GIF.

Required tools:
  agent-pbx, tmux, asciinema

Optional tools:
  agg        render the .cast file to GIF
  workerbee  expose WorkerBee status in the TUI when AGENT_PBX_WORKERBEE_BIN is set

Options:
  --out-dir PATH       Artifact directory. Default: artifacts/tui-demo
  --port PORT          Demo MCP port. Default: 8771
  --token TOKEN        Demo bearer token. Default: demo-token
  --cols N             Recording terminal width. Default: 120
  --rows N             Recording terminal height. Default: 36
  --theme NAME         TUI theme. Default: cyberpunk
  --layout NAME        TUI layout. Default: split
  --max-seconds N      Scripted recording timeout. Default: 35
  --manual             Record without scripted key presses; quit the TUI to stop.
  --skip-mcp           Use an already running MCP/API server.
  --keep-mcp           Leave the demo MCP daemon running after recording.
  --no-render          Create only the asciinema .cast file.
  --check              Verify local recording prerequisites, then exit.
  -h, --help           Show this help.

Example:
  AGENT_PBX_WORKERBEE_BIN=/path/to/workerbee scripts/record_tui_demo.sh
EOF
}

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'record_tui_demo: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      STATE_ROOT="${OUT_DIR}/state"
      DB_PATH="${STATE_ROOT}/agent-pbx-demo.sqlite"
      CAST_PATH="${OUT_DIR}/agent-pbx-tui-demo.cast"
      GIF_PATH="${OUT_DIR}/agent-pbx-tui-demo.gif"
      shift 2
      ;;
    --port)
      PORT="$2"
      SERVER="http://${HOST}:${PORT}"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --cols)
      COLS="$2"
      shift 2
      ;;
    --rows)
      ROWS="$2"
      shift 2
      ;;
    --theme)
      THEME="$2"
      shift 2
      ;;
    --layout)
      LAYOUT="$2"
      shift 2
      ;;
    --max-seconds)
      MAX_RECORD_SECONDS="$2"
      shift 2
      ;;
    --manual)
      MANUAL=1
      shift
      ;;
    --skip-mcp)
      START_MCP=0
      shift
      ;;
    --keep-mcp)
      KEEP_MCP=1
      shift
      ;;
    --no-render)
      RENDER=0
      shift
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

DRIVER_PID=""

cleanup() {
  set +e
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return
  fi
  if [[ -n "$DRIVER_PID" ]]; then
    kill "$DRIVER_PID" >/dev/null 2>&1 || true
  fi
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
  if [[ "$START_MCP" == "1" && "$KEEP_MCP" != "1" ]]; then
    mkdir -p "$OUT_DIR" >/dev/null 2>&1 || true
    "$AGENT_PBX_BIN" mcp stop \
      --host "$HOST" \
      --port "$PORT" \
      --state-root "$STATE_ROOT" \
      --db "$DB_PATH" \
      >"${OUT_DIR}/mcp-stop.json" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

check_requirements() {
  need_cmd "$AGENT_PBX_BIN"
  need_cmd tmux
  need_cmd "$ASCIINEMA_BIN"
  if [[ "$RENDER" == "1" ]]; then
    need_cmd "$AGG_BIN"
  fi
  if [[ -n "$WORKERBEE_BIN" && ! -x "$WORKERBEE_BIN" ]]; then
    fail "AGENT_PBX_WORKERBEE_BIN is set but not executable: $WORKERBEE_BIN"
  fi
}

start_demo_mcp() {
  mkdir -p "$OUT_DIR" "$STATE_ROOT"
  rm -f "$DB_PATH"
  log "Starting demo MCP/API on ${SERVER}"
  AGENT_PBX_WORKERBEE_BIN="$WORKERBEE_BIN" "$AGENT_PBX_BIN" mcp restart \
    --host "$HOST" \
    --port "$PORT" \
    --state-root "$STATE_ROOT" \
    --db "$DB_PATH" \
    --token "$TOKEN" \
    --debug \
    --debug-smoke \
    >"${OUT_DIR}/mcp-restart.json"
}

seed_demo_data() {
  log "Seeding deterministic demo agents"
  AGENT_PBX_DEMO_SERVER="$SERVER" \
  AGENT_PBX_DEMO_TOKEN="$TOKEN" \
  AGENT_PBX_DEMO_CWD="$ROOT_DIR" \
  python - <<'PY'
from __future__ import annotations

import json
import os
import urllib.request

server = os.environ["AGENT_PBX_DEMO_SERVER"].rstrip("/")
token = os.environ["AGENT_PBX_DEMO_TOKEN"]
cwd = os.environ["AGENT_PBX_DEMO_CWD"]
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}


def request(method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{server}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.loads(response.read().decode())


agents = [
    {
        "agent_id": "demo-workerbee",
        "project": "agent-pbx",
        "name": "WorkerBee Demo",
        "metadata": {"cwd": cwd, "pbx_mode": "report", "demo": True},
    },
    {
        "agent_id": "demo-planner",
        "project": "agent-pbx",
        "name": "Planning Demo",
        "metadata": {"cwd": cwd, "pbx_mode": "report", "demo": True},
    },
    {
        "agent_id": "demo-nohup",
        "project": "agent-pbx",
        "name": "Nohup Demo",
        "metadata": {"cwd": cwd, "pbx_mode": "nohup", "demo": True},
    },
]

for agent in agents:
    request("POST", "/v1/agents/register", agent)

request(
    "POST",
    "/v1/agents/demo-workerbee/reports",
    {
        "project": "agent-pbx",
        "status": "working",
        "summary": "WorkerBee project status is ready for review",
        "detail": (
            "Demo agent registered with metadata.cwd pointing at this repository. "
            "Open the WorkerBee tab to show project status, workload readiness, "
            "and dashboard links when AGENT_PBX_WORKERBEE_BIN is configured."
        ),
        "needs_input": False,
        "plan_options": [],
    },
)
request(
    "POST",
    "/v1/agents/demo-planner/reports",
    {
        "project": "agent-pbx",
        "status": "plan",
        "summary": "Choose a TUI demo path",
        "detail": "This report demonstrates structured plan options and Thread history.",
        "needs_input": True,
        "plan_options": [
            {
                "id": "tour",
                "label": "Tour the TUI",
                "description": "Show Latest, Thread, WorkerBee, and palette commands.",
            },
            {
                "id": "release",
                "label": "Prepare release notes",
                "description": "Summarize tests and artifact status.",
            },
        ],
    },
)
request(
    "POST",
    "/v1/agents/demo-nohup/reports",
    {
        "project": "agent-pbx",
        "status": "done",
        "summary": "Nohup follow-up window is available",
        "detail": (
            "This agent demonstrates queued commands, pings, and post-reply "
            "follow-up handling for local LAN workflows."
        ),
        "needs_input": False,
        "plan_options": [],
    },
)
request(
    "POST",
    "/v1/commands",
    {
        "agent_id": "demo-nohup",
        "type": "request_detail",
        "payload": {"request": "Show the detailed demo response."},
    },
)
PY
}

start_tui_session() {
  local tui_cmd
  printf -v tui_cmd \
    'env TERM=xterm-256color AGENT_PBX_TUI_SETTINGS_FILE=%q AGENT_PBX_TUI_THEME=%q AGENT_PBX_TUI_LAYOUT=%q AGENT_PBX_TUI_FLASH=1 AGENT_PBX_TUI_AGENT_BLINK=1 %q tui --server %q --token %q' \
    "${OUT_DIR}/tui-settings.json" \
    "$THEME" \
    "$LAYOUT" \
    "$AGENT_PBX_BIN" \
    "$SERVER" \
    "$TOKEN"
  tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
  tmux new-session -d -x "$COLS" -y "$ROWS" -s "$SESSION" "$tui_cmd"
}

drive_demo() {
  sleep 2
  tmux send-keys -t "$SESSION" Enter
  sleep 2
  tmux send-keys -t "$SESSION" C-p
  sleep 0.5
  tmux send-keys -t "$SESSION" -l "/workerbee"
  tmux send-keys -t "$SESSION" Enter
  sleep 4
  tmux send-keys -t "$SESSION" C-p
  sleep 0.5
  tmux send-keys -t "$SESSION" -l "/theme minimal"
  tmux send-keys -t "$SESSION" Enter
  sleep 2
  tmux send-keys -t "$SESSION" C-p
  sleep 0.5
  tmux send-keys -t "$SESSION" -l "/theme cyberpunk"
  tmux send-keys -t "$SESSION" Enter
  sleep 2
  tmux send-keys -t "$SESSION" C-p
  sleep 0.5
  tmux send-keys -t "$SESSION" -l "/plan latest"
  tmux send-keys -t "$SESSION" Enter
  sleep 3
  tmux send-keys -t "$SESSION" C-p
  sleep 0.5
  tmux send-keys -t "$SESSION" -l "/esc"
  tmux send-keys -t "$SESSION" Enter
  sleep 2
  tmux send-keys -t "$SESSION" C-c
}

record_cast() {
  local attach_cmd
  printf -v attach_cmd 'tmux attach-session -t %q' "$SESSION"
  log "Recording ${CAST_PATH}"
  if [[ "$MANUAL" == "0" ]]; then
    drive_demo &
    DRIVER_PID="$!"
  else
    log "Manual mode: interact with the TUI, then quit it to stop recording."
  fi
  if [[ "$MANUAL" == "0" && "$(command -v timeout || true)" != "" ]]; then
    timeout "${MAX_RECORD_SECONDS}s" \
      "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" -c "$attach_cmd" "$CAST_PATH" \
      || {
        status="$?"
        if [[ "$status" == "124" ]]; then
          log "Recording reached ${MAX_RECORD_SECONDS}s timeout; continuing with captured cast."
        else
          return "$status"
        fi
      }
  else
    "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" -c "$attach_cmd" "$CAST_PATH"
  fi
}

render_gif() {
  if [[ "$RENDER" != "1" ]]; then
    return
  fi
  log "Rendering ${GIF_PATH}"
  "$AGG_BIN" "$CAST_PATH" "$GIF_PATH"
}

check_requirements
mkdir -p "$OUT_DIR" "$STATE_ROOT"

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Recording prerequisites are available."
  exit 0
fi

if [[ -n "$WORKERBEE_BIN" ]]; then
  log "WorkerBee status enabled with AGENT_PBX_WORKERBEE_BIN=${WORKERBEE_BIN}"
else
  log "WorkerBee status not configured; set AGENT_PBX_WORKERBEE_BIN to populate the WorkerBee tab."
fi

if [[ "$START_MCP" == "1" ]]; then
  start_demo_mcp
fi
seed_demo_data
start_tui_session
record_cast
render_gif

log ""
log "TUI demo artifacts:"
log "  cast: ${CAST_PATH}"
if [[ "$RENDER" == "1" ]]; then
  log "  gif:  ${GIF_PATH}"
fi
