#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${AGENT_PBX_DEMO_OUT_DIR:-${ROOT_DIR}/artifacts/tui-demo}"
STATE_ROOT="${AGENT_PBX_DEMO_STATE_ROOT:-${OUT_DIR}/state}"
DB_PATH="${AGENT_PBX_DEMO_DB:-${STATE_ROOT}/agent-pbx-demo.sqlite}"
PORT="${AGENT_PBX_DEMO_PORT:-8771}"
JOPLIN_PORT="${AGENT_PBX_DEMO_JOPLIN_PORT:-8772}"
HOST="${AGENT_PBX_DEMO_HOST:-127.0.0.1}"
TOKEN="${AGENT_PBX_DEMO_TOKEN:-demo-token}"
SERVER="${AGENT_PBX_DEMO_SERVER:-http://${HOST}:${PORT}}"
JOPLIN_SERVER="${AGENT_PBX_DEMO_JOPLIN_SERVER:-http://${HOST}:${JOPLIN_PORT}}"
SESSION="${AGENT_PBX_DEMO_SESSION:-agent-pbx-demo-$$}"
TMUX_SOCKET="${AGENT_PBX_DEMO_TMUX_SOCKET:-${OUT_DIR}/tmux.sock}"
DEMO_PROJECT_DIR="${AGENT_PBX_DEMO_PROJECT_DIR:-/tmp/agent-pbx-demo-project}"
COLS="${AGENT_PBX_DEMO_COLS:-120}"
ROWS="${AGENT_PBX_DEMO_ROWS:-36}"
TITLE="${AGENT_PBX_DEMO_TITLE:-Agent PBX TUI demo}"
THEME="${AGENT_PBX_DEMO_THEME:-cyberpunk}"
LAYOUT="${AGENT_PBX_DEMO_LAYOUT:-split}"
MAX_RECORD_SECONDS="${AGENT_PBX_DEMO_MAX_RECORD_SECONDS:-60}"
CAST_PATH="${AGENT_PBX_DEMO_CAST:-${OUT_DIR}/agent-pbx-tui-demo.cast}"
GIF_PATH="${AGENT_PBX_DEMO_GIF:-${ROOT_DIR}/docs/assets/agent-pbx-tui.gif}"
if [[ -z "${AGENT_PBX_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/agent-pbx" ]]; then
  AGENT_PBX_BIN="${ROOT_DIR}/.venv/bin/agent-pbx"
elif [[ -z "${AGENT_PBX_BIN:-}" ]]; then
  AGENT_PBX_BIN="agent-pbx"
fi
ASCIINEMA_BIN="${ASCIINEMA_BIN:-asciinema}"
AGG_BIN="${AGG_BIN:-agg}"
WORKERBEE_BIN="${AGENT_PBX_WORKERBEE_BIN:-}"
DEMO_FIXTURES="${AGENT_PBX_DEMO_FIXTURES:-1}"
DEMO_BIN_DIR="${OUT_DIR}/bin"
DEMO_GH_BIN="${DEMO_BIN_DIR}/gh"
DEMO_WORKERBEE_BIN="${DEMO_BIN_DIR}/workerbee"
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
  --max-seconds N      Scripted recording timeout. Default: 60
  --manual             Record without scripted key presses; quit the TUI to stop.
  --skip-mcp           Use an already running MCP/API server.
  --keep-mcp           Leave the demo MCP daemon running after recording.
  --no-render          Create only the asciinema .cast file.
  --check              Verify local recording prerequisites, then exit.
  -h, --help           Show this help.

Example:
  AGENT_PBX_WORKERBEE_BIN=/path/to/workerbee scripts/record_tui_demo.sh

Set AGENT_PBX_DEMO_FIXTURES=0 to use live integrations instead of deterministic
local demo fixtures.
EOF
}

log() {
  printf '%s\n' "$*"
}

tmux_demo() {
  tmux -f /dev/null -S "$TMUX_SOCKET" "$@"
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
      TMUX_SOCKET="${OUT_DIR}/tmux.sock"
      DEMO_BIN_DIR="${OUT_DIR}/bin"
      DEMO_GH_BIN="${DEMO_BIN_DIR}/gh"
      DEMO_WORKERBEE_BIN="${DEMO_BIN_DIR}/workerbee"
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
JOPLIN_PID=""

cleanup() {
  set +e
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return
  fi
  if [[ -n "$DRIVER_PID" ]]; then
    kill "$DRIVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$JOPLIN_PID" ]]; then
    kill "$JOPLIN_PID" >/dev/null 2>&1 || true
  fi
  tmux_demo kill-session -t "$SESSION" >/dev/null 2>&1 || true
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

prepare_demo_project() {
  if [[ "$DEMO_FIXTURES" != "1" ]]; then
    return
  fi
  rm -rf "$DEMO_PROJECT_DIR"
  mkdir -p \
    "$DEMO_PROJECT_DIR/docs" \
    "$DEMO_PROJECT_DIR/ops" \
    "$DEMO_PROJECT_DIR/scripts" \
    "$DEMO_PROJECT_DIR/src/agent_pbx" \
    "$DEMO_PROJECT_DIR/tests"
  cp "$ROOT_DIR/README.md" "$DEMO_PROJECT_DIR/README.md"
  cp "$ROOT_DIR/AGENTS.md" "$DEMO_PROJECT_DIR/AGENTS.md"
  cp "$ROOT_DIR/CHANGELOG.md" "$DEMO_PROJECT_DIR/CHANGELOG.md"
  cp "$ROOT_DIR/src/agent_pbx/tui.py" "$DEMO_PROJECT_DIR/src/agent_pbx/tui.py"
  cp "$ROOT_DIR/tests/test_tui.py" "$DEMO_PROJECT_DIR/tests/test_tui.py"
  printf '%s\n' '# Demo operator notes' '' 'This directory is generated for the Agent PBX TUI recording.' \
    >"$DEMO_PROJECT_DIR/docs/operator-notes.md"
  printf '%s\n' '#!/usr/bin/env bash' 'echo "demo smoke test"' \
    >"$DEMO_PROJECT_DIR/scripts/smoke.sh"
  chmod +x "$DEMO_PROJECT_DIR/scripts/smoke.sh"
}

write_demo_integrations() {
  mkdir -p "$DEMO_BIN_DIR"
  cat >"$DEMO_WORKERBEE_BIN" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

args = sys.argv[1:]
if "project" in args and "status" in args:
    print(json.dumps({
        "project": "agent-pbx",
        "mode": "local",
        "running": True,
        "project_status": {
            "project": "agent-pbx",
            "running": True,
            "app_status": {
                "ready": True,
                "workloads": [
                    {"name": "agent-pbx-api", "ready": "1/1", "status": "running"},
                    {"name": "agent-pbx-tui-demo", "ready": "1/1", "status": "running"},
                ],
                "ingress_urls": ["https://agent-pbx.demo.local"],
            },
            "latest_deployment": {
                "name": "agent-pbx-demo",
                "namespace": "agent-pbx",
                "image": "agent-pbx:demo",
                "created_at": "2026-06-10T16:00:00Z",
            },
        },
    }))
elif "projects" in args:
    print(json.dumps({
        "projects": [
            {
                "project": "agent-pbx",
                "cwd_hint": ".",
                "running": True,
                "dashboard_url": "https://agent-pbx.demo.local",
                "state_dir": "/tmp/agent-pbx-demo",
            }
        ],
        "global_dashboard": {
            "ready": True,
            "url": "https://dashboard.demo.local",
        },
    }))
else:
    print("{}")
PY
  chmod +x "$DEMO_WORKERBEE_BIN"

  cat >"$DEMO_GH_BIN" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

args = sys.argv[1:]
repo = {
    "nameWithOwner": "m4xx3d0ut/agent-pbx",
    "url": "https://github.com/m4xx3d0ut/agent-pbx",
}
prs = [
    {
        "number": 12,
        "title": "Add operator workflow tabs",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "demo-agent"},
        "headRefName": "feature/operator-tabs",
        "baseRefName": "dev",
        "updatedAt": "2026-06-10T16:00:00Z",
        "createdAt": "2026-06-10T15:30:00Z",
        "url": "https://github.com/m4xx3d0ut/agent-pbx/pull/12",
        "body": "Adds PR and issue review surfaces to the Agent PBX TUI.",
        "labels": [{"name": "tui"}, {"name": "workflow"}],
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [
            {"name": "pytest", "conclusion": "SUCCESS"},
            {"name": "workerbee-smoke", "status": "IN_PROGRESS"},
        ],
        "files": [
            {"path": "src/agent_pbx/tui.py", "additions": 120, "deletions": 12},
            {"path": "tests/test_tui.py", "additions": 80, "deletions": 3},
        ],
        "commits": [{"oid": "abc123", "messageHeadline": "Add workflow tabs"}],
    },
    {
        "number": 13,
        "title": "Refresh public documentation",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "demo-docs"},
        "headRefName": "docs/refresh",
        "baseRefName": "dev",
        "updatedAt": "2026-06-10T15:45:00Z",
        "createdAt": "2026-06-10T15:00:00Z",
        "url": "https://github.com/m4xx3d0ut/agent-pbx/pull/13",
        "body": "Updates public operator docs and release notes.",
        "labels": [{"name": "docs"}],
        "reviewDecision": "APPROVED",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"name": "docs", "conclusion": "SUCCESS"}],
        "files": [{"path": "README.md", "additions": 24, "deletions": 4}],
        "commits": [{"oid": "def456", "messageHeadline": "Refresh docs"}],
    },
]
issues = [
    {
        "number": 42,
        "title": "Show README.md in Files completion",
        "state": "OPEN",
        "author": {"login": "demo-operator"},
        "labels": [{"name": "tui"}, {"name": "files"}],
        "assignees": [{"login": "demo-agent"}],
        "milestone": {"title": "next"},
        "updatedAt": "2026-06-10T16:10:00Z",
        "createdAt": "2026-06-10T15:10:00Z",
        "url": "https://github.com/m4xx3d0ut/agent-pbx/issues/42",
        "closed": False,
        "closedAt": None,
        "body": "The Files tab and @ completion should surface README.md clearly.",
        "comments": [
            {
                "author": {"login": "demo-agent"},
                "body": "Confirmed with a deterministic demo fixture.",
                "createdAt": "2026-06-10T16:15:00Z",
                "updatedAt": "2026-06-10T16:15:00Z",
                "url": "https://github.com/m4xx3d0ut/agent-pbx/issues/42#issuecomment-1",
            }
        ],
    },
    {
        "number": 43,
        "title": "Capture each TUI tab for the README hero",
        "state": "OPEN",
        "author": {"login": "demo-docs"},
        "labels": [{"name": "docs"}],
        "assignees": [],
        "milestone": None,
        "updatedAt": "2026-06-10T16:05:00Z",
        "createdAt": "2026-06-10T15:05:00Z",
        "url": "https://github.com/m4xx3d0ut/agent-pbx/issues/43",
        "closed": False,
        "closedAt": None,
        "body": "The README animation should show each right-pane tab with example data.",
        "comments": [],
    },
]

def write(value: object) -> None:
    print(json.dumps(value))

if args[:2] == ["repo", "view"]:
    write(repo)
elif args[:2] == ["pr", "list"]:
    write(prs)
elif len(args) >= 3 and args[:2] == ["pr", "view"]:
    number = int(args[2])
    write(next((item for item in prs if item["number"] == number), prs[0]))
elif args[:2] == ["issue", "list"]:
    write(issues)
elif len(args) >= 3 and args[:2] == ["issue", "view"]:
    number = int(args[2])
    write(next((item for item in issues if item["number"] == number), issues[0]))
elif args[:2] == ["pr", "merge"]:
    write({"ok": True})
elif args[:2] in (["issue", "comment"], ["issue", "close"]):
    write({"ok": True})
else:
    write({})
PY
  chmod +x "$DEMO_GH_BIN"
}

start_demo_joplin() {
  local server_py="${OUT_DIR}/fake_joplin_api.py"
  cat >"$server_py" <<'PY'
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


folders = [
    {"id": "root", "parent_id": "", "title": "Agent PBX"},
    {"id": "project", "parent_id": "root", "title": "agent-pbx"},
    {"id": "agent", "parent_id": "project", "title": "codex-main"},
]
notes = {
    "release-checklist": {
        "id": "release-checklist",
        "parent_id": "project",
        "title": "Release Checklist",
        "body": "## Release Checklist\n\n- Run tests\n- Review README\n- Build artifacts",
        "created_time": 1781100000000,
        "updated_time": 1781100900000,
    },
    "tui-demo-notes": {
        "id": "tui-demo-notes",
        "parent_id": "project",
        "title": "TUI Demo Notes",
        "body": "The demo walks through Latest, Thread, Files, WorkerBee, PRs, Issues, and Joplin.",
        "created_time": 1781100000000,
        "updated_time": 1781100600000,
    },
}


def payload(value: object) -> bytes:
    return json.dumps(value).encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def send_json(self, value: object, status: int = 200) -> None:
        data = payload(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/ping":
            self.send_json({"status": "ok"})
            return
        if path == "/folders":
            self.send_json({"items": folders, "has_more": False})
            return
        if path == "/folders/project/notes":
            self.send_json({"items": list(notes.values()), "has_more": False})
            return
        if path == "/folders/agent/notes":
            self.send_json({"items": [], "has_more": False})
            return
        if path.startswith("/notes/"):
            note_id = path.rsplit("/", 1)[-1]
            note = notes.get(note_id)
            self.send_json(note or {"error": "not found"}, status=200 if note else 404)
            return
        self.send_json({"items": [], "has_more": False})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/folders":
            self.send_json(folders[0])
            return
        if path == "/notes":
            self.send_json(notes["tui-demo-notes"])
            return
        self.send_json({})

    def do_PUT(self) -> None:
        self.send_json({})

    def do_DELETE(self) -> None:
        self.send_json({})


host = sys.argv[1]
port = int(sys.argv[2])
ThreadingHTTPServer((host, port), Handler).serve_forever()
PY
  python3 "$server_py" "$HOST" "$JOPLIN_PORT" &
  JOPLIN_PID="$!"
  python3 - <<PY
from __future__ import annotations

import json
import time
import urllib.request

url = "${JOPLIN_SERVER}/folders?token=demo-joplin-token"
deadline = time.time() + 8
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            json.loads(response.read().decode())
        raise SystemExit(0)
    except Exception:
        time.sleep(0.2)
raise SystemExit("fake Joplin API did not start")
PY
}

start_demo_mcp() {
  mkdir -p "$OUT_DIR" "$STATE_ROOT"
  rm -f "$DB_PATH"
  log "Starting demo MCP/API on ${SERVER}"
  local workerbee_bin="$WORKERBEE_BIN"
  local gh_bin="${AGENT_PBX_GH_BIN:-gh}"
  local pr_enabled="${AGENT_PBX_PR_ENABLED:-0}"
  local issues_enabled="${AGENT_PBX_ISSUES_ENABLED:-0}"
  local joplin_api_url="${AGENT_PBX_JOPLIN_API_URL:-}"
  local joplin_token="${AGENT_PBX_JOPLIN_TOKEN:-}"
  if [[ "$DEMO_FIXTURES" == "1" ]]; then
    write_demo_integrations
    start_demo_joplin
    workerbee_bin="$DEMO_WORKERBEE_BIN"
    gh_bin="$DEMO_GH_BIN"
    pr_enabled="1"
    issues_enabled="1"
    joplin_api_url="$JOPLIN_SERVER"
    joplin_token="demo-joplin-token"
  fi
  AGENT_PBX_WORKERBEE_BIN="$workerbee_bin" \
  AGENT_PBX_GH_BIN="$gh_bin" \
  AGENT_PBX_PR_ENABLED="$pr_enabled" \
  AGENT_PBX_ISSUES_ENABLED="$issues_enabled" \
  AGENT_PBX_PR_ALLOWED_REPOS="m4xx3d0ut/agent-pbx" \
  AGENT_PBX_JOPLIN_API_URL="$joplin_api_url" \
  AGENT_PBX_JOPLIN_TOKEN="$joplin_token" \
  "$AGENT_PBX_BIN" mcp restart \
    --host "$HOST" \
    --port "$PORT" \
    --state-root "$STATE_ROOT" \
    --db "$DB_PATH" \
    --token "$TOKEN" \
    --debug \
    >"${OUT_DIR}/mcp-restart.json"
}

seed_demo_data() {
  log "Seeding deterministic demo agents"
  local demo_cwd="$ROOT_DIR"
  if [[ "$DEMO_FIXTURES" == "1" ]]; then
    demo_cwd="$DEMO_PROJECT_DIR"
  fi
  AGENT_PBX_DEMO_SERVER="$SERVER" \
  AGENT_PBX_DEMO_TOKEN="$TOKEN" \
  AGENT_PBX_DEMO_CWD="$demo_cwd" \
  python3 - <<'PY'
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
        "agent_id": "sun-tzu-smoke-1",
        "project": "agent-pbx",
        "name": "Sun Tzu Smoke 1",
        "metadata": {"cwd": cwd, "pbx_mode": "report", "demo": True},
    },
    {
        "agent_id": "sun-tzu-smoke-3",
        "project": "agent-pbx",
        "name": "Sun Tzu Smoke 3",
        "metadata": {"cwd": cwd, "pbx_mode": "report", "demo": True},
    },
    {
        "agent_id": "sun-tzu-smoke-2",
        "project": "agent-pbx",
        "name": "Sun Tzu Smoke 2",
        "metadata": {"cwd": cwd, "pbx_mode": "nohup", "demo": True},
    },
]

for agent in agents:
    request("POST", "/v1/agents/register", agent)

request(
    "POST",
    "/v1/agents/sun-tzu-smoke-1/reports",
    {
        "project": "agent-pbx",
        "status": "working",
        "summary": "Attack where he is unprepared.",
        "detail": (
            "Attack where he is unprepared.\n\n"
            "Debug smoke report from sun-tzu-smoke-1. Use this to verify TUI "
            "refresh, unseen markers, and alerts."
        ),
        "needs_input": False,
        "plan_options": [],
    },
)
request(
    "POST",
    "/v1/agents/sun-tzu-smoke-3/reports",
    {
        "project": "agent-pbx",
        "status": "plan",
        "summary": "Know yourself and you will win all battles.",
        "detail": (
            "Know yourself and you will win all battles.\n\n"
            "This report demonstrates structured plan options and Thread history."
        ),
        "needs_input": True,
        "plan_options": [
            {
                "id": "observe",
                "label": "Observe",
                "description": "Review the field before committing the next move.",
            },
            {
                "id": "advance",
                "label": "Advance",
                "description": "Act while the opening is visible.",
            },
        ],
    },
)
request(
    "POST",
    "/v1/agents/sun-tzu-smoke-2/reports",
    {
        "project": "agent-pbx",
        "status": "done",
        "summary": "Opportunities multiply as they are seized.",
        "detail": (
            "Opportunities multiply as they are seized.\n\n"
            "Debug smoke report from sun-tzu-smoke-2. This agent demonstrates "
            "queued commands, pings, and post-reply follow-up handling."
        ),
        "needs_input": False,
        "plan_options": [],
    },
)
request(
    "POST",
    "/v1/commands",
    {
        "agent_id": "sun-tzu-smoke-2",
        "type": "request_detail",
        "payload": {"request": "Show the detailed smoke quote response."},
    },
)
PY
}

start_tui_session() {
  local tui_cmd
  rm -f "${OUT_DIR}/tui-settings.json"
  printf -v tui_cmd \
    'env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor AGENT_PBX_TUI_SETTINGS_FILE=%q AGENT_PBX_TUI_THEME=%q AGENT_PBX_TUI_LAYOUT=%q AGENT_PBX_TUI_FLASH=1 AGENT_PBX_TUI_AGENT_BLINK=1 %q tui --server %q --token %q' \
    "${OUT_DIR}/tui-settings.json" \
    "$THEME" \
    "$LAYOUT" \
    "$AGENT_PBX_BIN" \
    "$SERVER" \
    "$TOKEN"
  tmux_demo kill-session -t "$SESSION" >/dev/null 2>&1 || true
  tmux_demo set-option -g default-terminal "tmux-256color" >/dev/null 2>&1 || true
  tmux_demo set-option -ga terminal-overrides ",xterm-256color:RGB" >/dev/null 2>&1 || true
  tmux_demo new-session -d -x "$COLS" -y "$ROWS" -s "$SESSION" "$tui_cmd"
  tmux_demo set-option -t "$SESSION" status off >/dev/null 2>&1 || true
}

drive_demo() {
  send_palette() {
    local command="$1"
    tmux_demo send-keys -t "$SESSION" C-p
    sleep 0.5
    tmux_demo send-keys -t "$SESSION" -l "$command"
    tmux_demo send-keys -t "$SESSION" Enter
  }

  sleep 2
  tmux_demo send-keys -t "$SESSION" Enter
  sleep 3
  send_palette "/latest"
  sleep 5
  send_palette "/thread"
  sleep 5
  send_palette "/files"
  sleep 5
  send_palette "/workerbee"
  sleep 5
  send_palette "/pr"
  sleep 5
  send_palette "/issue"
  sleep 5
  send_palette "/joplin"
  sleep 5
  send_palette "/latest"
  sleep 2
  tmux_demo send-keys -t "$SESSION" C-c
}

record_cast() {
  local attach_cmd
  printf -v attach_cmd 'tmux -f /dev/null -S %q attach-session -t %q' "$TMUX_SOCKET" "$SESSION"
  log "Recording ${CAST_PATH}"
  if [[ "$MANUAL" == "0" ]]; then
    drive_demo &
    DRIVER_PID="$!"
  else
    log "Manual mode: interact with the TUI, then quit it to stop recording."
  fi
  if [[ "$MANUAL" == "0" && "$(command -v timeout || true)" != "" ]]; then
    timeout "${MAX_RECORD_SECONDS}s" \
      "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" --cols "$COLS" --rows "$ROWS" -c "$attach_cmd" "$CAST_PATH" \
      || {
        status="$?"
        if [[ "$status" == "124" ]]; then
          log "Recording reached ${MAX_RECORD_SECONDS}s timeout; continuing with captured cast."
        else
          return "$status"
        fi
      }
  else
    "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" --cols "$COLS" --rows "$ROWS" -c "$attach_cmd" "$CAST_PATH"
  fi
}

render_gif() {
  if [[ "$RENDER" != "1" ]]; then
    return
  fi
  log "Rendering ${GIF_PATH}"
  "$AGG_BIN" --cols "$COLS" --rows "$ROWS" "$CAST_PATH" "$GIF_PATH"
}

check_requirements
mkdir -p "$OUT_DIR" "$STATE_ROOT"
prepare_demo_project

if [[ "$CHECK_ONLY" == "1" ]]; then
  log "Recording prerequisites are available."
  exit 0
fi

if [[ "$DEMO_FIXTURES" == "1" ]]; then
  log "Using deterministic demo fixtures for WorkerBee, GitHub, and Joplin tabs."
elif [[ -n "$WORKERBEE_BIN" ]]; then
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
