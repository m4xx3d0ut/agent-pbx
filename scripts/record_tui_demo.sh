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
DEMO_PROJECT_ROOT="${AGENT_PBX_DEMO_PROJECT_ROOT:-/tmp/agent-pbx-demo-projects}"
DEMO_PROJECT_DIR="${AGENT_PBX_DEMO_PROJECT_DIR:-${DEMO_PROJECT_ROOT}/agent-pbx-v2-demo}"
DEMO_EXISTING_PROJECT_DIR="${AGENT_PBX_DEMO_EXISTING_PROJECT_DIR:-${DEMO_PROJECT_ROOT}/platform-console}"
DEMO_LAUNCHED_AGENT_ID="${AGENT_PBX_DEMO_LAUNCHED_AGENT_ID:-codex-agent-pbx-v2-demo}"
DEMO_RUNTIME_DIR="${AGENT_PBX_DEMO_RUNTIME_DIR:-${OUT_DIR}/runtime}"
DEMO_RUNTIME_SOCKET="${DEMO_RUNTIME_DIR}/agent-pbx/runtime-tmux.sock"
COLS="${AGENT_PBX_DEMO_COLS:-180}"
ROWS="${AGENT_PBX_DEMO_ROWS:-54}"
TITLE="${AGENT_PBX_DEMO_TITLE:-Agent PBX TUI demo}"
THEME="${AGENT_PBX_DEMO_THEME:-cyberpunk}"
LAYOUT="${AGENT_PBX_DEMO_LAYOUT:-split}"
MAX_RECORD_SECONDS="${AGENT_PBX_DEMO_MAX_RECORD_SECONDS:-120}"
CAST_PATH="${AGENT_PBX_DEMO_CAST:-${OUT_DIR}/agent-pbx-tui-demo.cast}"
GIF_PATH="${AGENT_PBX_DEMO_GIF:-${ROOT_DIR}/docs/assets/agent-pbx-tui.gif}"
if [[ -z "${AGENT_PBX_BIN:-}" && -x "${ROOT_DIR}/.venv/bin/agent-pbx" ]]; then
  AGENT_PBX_BIN="${ROOT_DIR}/.venv/bin/agent-pbx"
elif [[ -z "${AGENT_PBX_BIN:-}" ]]; then
  AGENT_PBX_BIN="agent-pbx"
fi
ASCIINEMA_BIN="${ASCIINEMA_BIN:-asciinema}"
AGG_BIN="${AGG_BIN:-agg}"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
RENDER_FONT_SIZE="${AGENT_PBX_DEMO_RENDER_FONT_SIZE:-17}"
RENDER_LINE_HEIGHT="${AGENT_PBX_DEMO_RENDER_LINE_HEIGHT:-1.12}"
RENDER_WIDTH="${AGENT_PBX_DEMO_RENDER_WIDTH:-1920}"
RENDER_HEIGHT="${AGENT_PBX_DEMO_RENDER_HEIGHT:-1080}"
RENDER_FPS="${AGENT_PBX_DEMO_RENDER_FPS:-30}"
RENDER_SELECT="${AGENT_PBX_DEMO_RENDER_SELECT:-0..67}"
RENDER_EXACT_SIZE="${AGENT_PBX_DEMO_RENDER_EXACT_SIZE:-0}"
WORKERBEE_BIN="${AGENT_PBX_WORKERBEE_BIN:-}"
DEMO_FIXTURES="${AGENT_PBX_DEMO_FIXTURES:-1}"
DEMO_BIN_DIR="${OUT_DIR}/bin"
DEMO_GH_BIN="${DEMO_BIN_DIR}/gh"
DEMO_WORKERBEE_BIN="${DEMO_BIN_DIR}/workerbee"
DEMO_CODEX_BIN="${DEMO_BIN_DIR}/codex"
LIVE_CODEX="${AGENT_PBX_DEMO_LIVE_CODEX:-0}"
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
  ffmpeg     exact-size GIF rendering when --exact-size is used
  workerbee  expose WorkerBee status in the TUI when AGENT_PBX_WORKERBEE_BIN is set

Options:
  --out-dir PATH       Artifact directory. Default: artifacts/tui-demo
  --port PORT          Demo MCP port. Default: 8771
  --token TOKEN        Demo bearer token. Default: demo-token
  --cols N             Recording terminal width. Default: 180
  --rows N             Recording terminal height. Default: 54
  --theme NAME         TUI theme. Default: cyberpunk
  --layout NAME        TUI layout. Default: split
  --max-seconds N      Scripted recording timeout. Default: 120
  --render-size WxH    Exact GIF output size. Default: 1920x1080
  --render-select SEL  agg frame selector. Default: 0..67
  --exact-size         Resize/re-encode to --render-size with ffmpeg.
  --live-codex         Use the installed Codex CLI in the isolated demo runtime.
  --manual             Record without scripted key presses; quit the TUI to stop.
  --skip-mcp           Use an already running MCP/API server.
  --keep-mcp           Leave the demo MCP daemon running after recording.
  --no-render          Create only the asciinema .cast file.
  --check              Verify local recording prerequisites, then exit.
  -h, --help           Show this help.

Example:
  AGENT_PBX_WORKERBEE_BIN=/path/to/workerbee scripts/record_tui_demo.sh

The default uses deterministic local fixtures, including a clearly labeled demo
Codex runtime running in real PBX-managed tmux panes. Use --live-codex only for
a reviewed release capture from the isolated demo projects. Set
AGENT_PBX_DEMO_FIXTURES=0 to use live integrations instead of deterministic
WorkerBee, GitHub, and Joplin fixtures.
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
      DEMO_CODEX_BIN="${DEMO_BIN_DIR}/codex"
      DEMO_RUNTIME_DIR="${OUT_DIR}/runtime"
      DEMO_RUNTIME_SOCKET="${DEMO_RUNTIME_DIR}/agent-pbx/runtime-tmux.sock"
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
    --render-size)
      if [[ "$2" != *x* ]]; then
        fail "--render-size must be WIDTHxHEIGHT"
      fi
      RENDER_WIDTH="${2%x*}"
      RENDER_HEIGHT="${2#*x}"
      RENDER_EXACT_SIZE=1
      shift 2
      ;;
    --render-select)
      RENDER_SELECT="$2"
      shift 2
      ;;
    --exact-size)
      RENDER_EXACT_SIZE=1
      shift
      ;;
    --live-codex)
      LIVE_CODEX=1
      shift
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
RECORDER_PID=""
RECORDING_WATCHDOG_PID=""

cleanup() {
  set +e
  if [[ "$CHECK_ONLY" == "1" ]]; then
    return
  fi
  if [[ -n "$DRIVER_PID" ]]; then
    kill "$DRIVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$RECORDING_WATCHDOG_PID" ]]; then
    kill "$RECORDING_WATCHDOG_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$RECORDER_PID" ]]; then
    kill -INT "$RECORDER_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$JOPLIN_PID" ]]; then
    kill "$JOPLIN_PID" >/dev/null 2>&1 || true
  fi
  # The recording server is private to this run and may also contain the
  # demo Operator session. Stop the whole disposable server so no fixture
  # runtime survives a completed or interrupted capture.
  tmux_demo kill-server >/dev/null 2>&1 || true
  tmux -S "$DEMO_RUNTIME_SOCKET" kill-server >/dev/null 2>&1 || true
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
  need_cmd git
  need_cmd python3
  need_cmd tmux
  need_cmd "$ASCIINEMA_BIN"
  if [[ "$RENDER" == "1" ]]; then
    need_cmd "$AGG_BIN"
    if [[ "$RENDER_EXACT_SIZE" == "1" ]]; then
      need_cmd "$FFMPEG_BIN"
    fi
  fi
  if [[ -n "$WORKERBEE_BIN" && ! -x "$WORKERBEE_BIN" ]]; then
    fail "AGENT_PBX_WORKERBEE_BIN is set but not executable: $WORKERBEE_BIN"
  fi
  if [[ "$LIVE_CODEX" == "1" ]]; then
    need_cmd codex
  fi
}

prepare_demo_project() {
  if [[ "$DEMO_FIXTURES" != "1" ]]; then
    return
  fi
  rm -rf "$DEMO_PROJECT_ROOT"
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

  mkdir -p "$DEMO_EXISTING_PROJECT_DIR/src" "$DEMO_EXISTING_PROJECT_DIR/ops"
  cat >"$DEMO_EXISTING_PROJECT_DIR/README.md" <<'EOF'
# Platform Console

Synthetic project used by the Agent PBX v2 native-runtime recording.
EOF
  cat >"$DEMO_EXISTING_PROJECT_DIR/src/runtime.py" <<'PY'
def runtime_status() -> str:
    return "ready"
PY
  printf '%s\n' 'profile: demo' 'runtime: healthy' \
    >"$DEMO_EXISTING_PROJECT_DIR/ops/runtime.yaml"

  for repo in "$DEMO_PROJECT_DIR" "$DEMO_EXISTING_PROJECT_DIR"; do
    git -C "$repo" init -q -b dev
    git -C "$repo" add .
    git -C "$repo" \
      -c user.name="Agent PBX Demo" \
      -c user.email="demo@agent-pbx.local" \
      commit -q -m "Create isolated Agent PBX demo project"
  done
}

write_demo_integrations() {
  mkdir -p "$DEMO_BIN_DIR"
  if [[ "$LIVE_CODEX" != "1" ]]; then
    cp "$ROOT_DIR/scripts/demo/demo_codex.py" "$DEMO_CODEX_BIN"
    chmod +x "$DEMO_CODEX_BIN"
  fi
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
    {"id": "project", "parent_id": "root", "title": "agent-pbx-v2-demo"},
    {"id": "agent", "parent_id": "project", "title": "codex-agent-pbx-v2-demo"},
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
        "body": "The demo walks through native Latest, Files, WorkerBee, PRs, Issues, campaigns, and Joplin.",
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
  mkdir -p "$OUT_DIR" "$STATE_ROOT" "$DEMO_RUNTIME_DIR"
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
  local demo_path="$PATH"
  if [[ "$LIVE_CODEX" != "1" ]]; then
    demo_path="${DEMO_BIN_DIR}:${demo_path}"
  fi
  # The release recorder may itself run inside the operator's workstation
  # tmux. Keep that server out of daemon-side outer-server discovery so demo
  # launches can only use the disposable XDG runtime socket below.
  env -u TMUX -u TMUX_PANE \
    PATH="$demo_path" \
    XDG_RUNTIME_DIR="$DEMO_RUNTIME_DIR" \
    AGENT_PBX_PROJECT_ROOTS="$DEMO_PROJECT_ROOT" \
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
  log "Seeding deterministic v2 Agent and native runtime"
  local demo_cwd="$ROOT_DIR"
  local existing_cwd="$ROOT_DIR"
  if [[ "$DEMO_FIXTURES" == "1" ]]; then
    demo_cwd="$DEMO_PROJECT_DIR"
    existing_cwd="$DEMO_EXISTING_PROJECT_DIR"
  fi
  AGENT_PBX_DEMO_SERVER="$SERVER" \
  AGENT_PBX_DEMO_TOKEN="$TOKEN" \
  AGENT_PBX_DEMO_CWD="$demo_cwd" \
  AGENT_PBX_DEMO_EXISTING_CWD="$existing_cwd" \
  python3 - <<'PY'
from __future__ import annotations

import json
import os
import time
import urllib.request

server = os.environ["AGENT_PBX_DEMO_SERVER"].rstrip("/")
token = os.environ["AGENT_PBX_DEMO_TOKEN"]
cwd = os.environ["AGENT_PBX_DEMO_CWD"]
existing_cwd = os.environ["AGENT_PBX_DEMO_EXISTING_CWD"]
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}


def request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> object:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{server}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


request(
    "POST",
    "/v2/agents/managed-launch",
    {
        "project_path": existing_cwd,
        "agent_id": "platform-agent",
        "profile_id": "sol-high",
        "runtime_server_mode": "dedicated",
    },
)

deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    agents = request("GET", "/v1/agents?include_hidden=true")
    platform = next(
        (
            item
            for item in agents
            if isinstance(item, dict) and item.get("agent_id") == "platform-agent"
        ),
        {},
    )
    metadata = platform.get("metadata") if isinstance(platform, dict) else {}
    if isinstance(metadata, dict) and metadata.get("codex_session_id"):
        break
    time.sleep(0.2)
else:
    raise SystemExit("platform-agent demo runtime did not register its session")

request(
    "POST",
    "/v1/agents/register",
    {
        "agent_id": "release-review",
        "project": "agent-pbx",
        "name": "Release Review",
        "metadata": {
            "cwd": os.path.dirname(cwd),
            "pbx_mode": "report",
            "demo": True,
            "codex_session_id": "demo-session-release-review",
            "codex_host_id": "local",
        },
    },
)
request(
    "POST",
    "/v1/agents/platform-agent/reports",
    {
        "project": "platform-console",
        "status": "working",
        "summary": "Native runtime is ready for the v2 interface review.",
        "detail": (
            "The managed Agent is attached through a normal tmux client rendered "
            "inside Latest. Tmux owns the durable pane and scrollback."
        ),
        "needs_input": False,
        "plan_options": [],
        "reporting_agent_id": "platform-agent",
        "metadata": {"demo": True, "suppress_tui_alerts": True},
    },
)
request(
    "POST",
    "/v1/agents/release-review/reports",
    {
        "project": "agent-pbx",
        "status": "plan",
        "summary": "Review the v2 documentation and release evidence.",
        "detail": (
            "This bounded review Agent demonstrates durable report state beside "
            "the native managed runtime."
        ),
        "needs_input": True,
        "plan_options": [
            {
                "id": "accept",
                "label": "Accept evidence",
                "description": "Record the release review as complete.",
            },
            {
                "id": "follow-up",
                "label": "Request follow-up",
                "description": "Route one bounded correction to the Agent.",
            },
        ],
        "reporting_agent_id": "release-review",
        "metadata": {"demo": True},
    },
)
request(
    "POST",
    "/v1/agents/platform-agent/reports",
    {
        "project": "platform-console",
        "status": "working",
        "summary": "Selected native runtime is ready for operator input.",
        "detail": (
            "The active Agent is intentionally the newest demo entity so the "
            "recording opens on its managed tmux terminal."
        ),
        "needs_input": False,
        "plan_options": [],
        "reporting_agent_id": "platform-agent",
        "metadata": {"demo": True, "suppress_tui_alerts": True},
    },
)
PY
  # Keep public release captures independent of the workstation's tmux status
  # configuration. The server remains real and owns the Codex pane/history;
  # only its decorative status row is disabled for the embedded surface.
  tmux -S "$DEMO_RUNTIME_SOCKET" set-option -g status off >/dev/null 2>&1 || true
}

wait_for_demo_agent() {
  local agent_id="$1"
  local require_session="${2:-0}"
  AGENT_PBX_DEMO_SERVER="$SERVER" \
  AGENT_PBX_DEMO_TOKEN="$TOKEN" \
  AGENT_PBX_DEMO_AGENT_ID="$agent_id" \
  AGENT_PBX_DEMO_REQUIRE_SESSION="$require_session" \
  python3 - <<'PY'
from __future__ import annotations

import json
import os
import time
import urllib.request

server = os.environ["AGENT_PBX_DEMO_SERVER"].rstrip("/")
token = os.environ["AGENT_PBX_DEMO_TOKEN"]
agent_id = os.environ["AGENT_PBX_DEMO_AGENT_ID"]
require_session = os.environ["AGENT_PBX_DEMO_REQUIRE_SESSION"] == "1"
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    request = urllib.request.Request(
        f"{server}/v1/agents?include_hidden=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            agents = json.loads(response.read().decode())
    except OSError:
        time.sleep(0.25)
        continue
    agent = next(
        (
            item
            for item in agents
            if isinstance(item, dict) and item.get("agent_id") == agent_id
        ),
        None,
    )
    if isinstance(agent, dict):
        metadata = agent.get("metadata")
        if not require_session or (
            isinstance(metadata, dict) and metadata.get("codex_session_id")
        ):
            raise SystemExit(0)
    time.sleep(0.25)
raise SystemExit(f"timed out waiting for demo Agent PBX entity {agent_id}")
PY
}

seed_operator_workflow() {
  log "Seeding deterministic Operator campaign after UI launch"
  AGENT_PBX_DEMO_SERVER="$SERVER" \
  AGENT_PBX_DEMO_TOKEN="$TOKEN" \
  AGENT_PBX_DEMO_LAUNCHED_AGENT_ID="$DEMO_LAUNCHED_AGENT_ID" \
  python3 - <<'PY'
from __future__ import annotations

import json
import os
import urllib.request

server = os.environ["AGENT_PBX_DEMO_SERVER"].rstrip("/")
token = os.environ["AGENT_PBX_DEMO_TOKEN"]
launched_agent_id = os.environ["AGENT_PBX_DEMO_LAUNCHED_AGENT_ID"]
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}


def request(method: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    req = urllib.request.Request(
        f"{server}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


# The deterministic runtime does not register through MCP, so promote the
# isolated fork records to the state that a real Codex launch reports. Campaign
# delivery then exercises the normal queue path and renders healthy topology.
request(
    "POST",
    "/v1/operator/forks/ensure",
    {
        "operator_agent_id": "operator-0",
        "source_caller_agent_id": launched_agent_id,
        "fork_agent_id": "operator-0-fork-codex-agent-pbx-v2-demo-demo",
        "fork_track_id": "default",
        "fork_purpose": "edit",
        "access_mode": "edit",
        "status": "running",
        "summary": "Deterministic demo edit fork is ready.",
        "metadata": {"demo": True},
    },
)
request(
    "POST",
    "/v1/operator/forks/ensure",
    {
        "operator_agent_id": "operator-0",
        "source_caller_agent_id": "release-review",
        "fork_agent_id": "operator-0-fork-release-review-demo",
        "fork_track_id": "default",
        "fork_purpose": "edit",
        "access_mode": "edit",
        "status": "running",
        "summary": "Deterministic demo release-review fork is ready.",
        "metadata": {"demo": True},
    },
)


request(
    "POST",
    "/v1/agents/operator-0/reports",
    {
        "project": "agent-pbx-operator",
        "status": "working",
        "summary": "Coordinating the native Agent and release review campaign.",
        "detail": (
            "Operator 0 owns durable campaign state while its Codex root and "
            "bounded fork execute inside PBX-managed tmux runtimes."
        ),
        "needs_input": False,
        "plan_options": [],
        "reporting_agent_id": "operator-0",
        "metadata": {
            "demo": True,
            "reporting_agent_id": "operator-0",
            "suppress_tui_alerts": True,
        },
    },
)
request(
    "POST",
    "/v1/operator/campaigns",
    {
        "operator_agent_id": "operator-0",
        "title": "Agent PBX v2 documentation release",
        "objective": (
            "Coordinate native-runtime validation, documentation review, and "
            "release evidence through durable Agent PBX identities."
        ),
        "criteria": [
            "Managed Agent remains attached through the native terminal.",
            "Operator and fork topology remain explicit.",
            "Documentation and release evidence are independently reviewed.",
        ],
        "assignments": [
            {
                "target_agent_id": launched_agent_id,
                "title": "Validate native runtime",
                "prompt": "Verify terminal attachment, resize, input, and persistence.",
                "criteria": ["Native tmux runtime remains ready."],
            },
            {
                "target_agent_id": "release-review",
                "title": "Review release evidence",
                "prompt": "Review README, architecture, and v2 acceptance evidence.",
                "criteria": ["Control-plane terminology matches implementation."],
            },
        ],
        "delivery": "queue",
    },
)
PY
}

start_tui_session() {
  local tui_cmd
  rm -f "${OUT_DIR}/tui-settings.json"
  local codex_bin="codex"
  local demo_path="$PATH"
  if [[ "$LIVE_CODEX" != "1" ]]; then
    codex_bin="$DEMO_CODEX_BIN"
    demo_path="${DEMO_BIN_DIR}:${demo_path}"
  fi
  printf -v tui_cmd \
    'env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor PATH=%q XDG_RUNTIME_DIR=%q AGENT_PBX_TUI_SETTINGS_FILE=%q AGENT_PBX_TUI_THEME=%q AGENT_PBX_TUI_LAYOUT=%q AGENT_PBX_TUI_FLASH=1 AGENT_PBX_TUI_AGENT_BLINK=1 AGENT_PBX_TUI_EVENT_STREAM_V2=1 AGENT_PBX_TUI_TMUX=1 AGENT_PBX_TUI_EMBEDDED_TERMINAL_V2=1 AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE=outer_if_present AGENT_PBX_TUI_COMPAT_TERMINAL_CAPTURE=0 AGENT_PBX_TUI_CODEX_BIN=%q %q tui --server %q --token %q' \
    "$demo_path" \
    "$DEMO_RUNTIME_DIR" \
    "${OUT_DIR}/tui-settings.json" \
    "$THEME" \
    "$LAYOUT" \
    "$codex_bin" \
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
  wait_for_screen_text() {
    local expected="$1"
    local attempts="${2:-80}"
    local output
    for ((index = 0; index < attempts; index++)); do
      output="$(tmux_demo capture-pane -p -J -t "$SESSION" 2>/dev/null || true)"
      if grep -Fq -- "$expected" <<<"$output"; then
        return 0
      fi
      sleep 0.25
    done
    log "Timed out waiting for TUI text: $expected"
    return 1
  }

  send_palette() {
    local command="$1"
    # Ctrl+P belongs to Codex while the embedded terminal owns focus. F3 is a
    # global PBX binding that deliberately returns focus to the right tab row.
    tmux_demo send-keys -t "$SESSION" F3
    sleep 0.35
    tmux_demo send-keys -t "$SESSION" C-p
    sleep 0.4
    tmux_demo send-keys -t "$SESSION" -l "$command"
    # Textual gathers command providers asynchronously. Explicitly move to the
    # first result after it has settled so Enter selects and runs the intended
    # command instead of merely highlighting it on a loaded system.
    sleep 1.25
    tmux_demo send-keys -t "$SESSION" Down Enter
  }

  # The release-review fixture intentionally has a newer alert. Select the
  # first managed Agent explicitly so the hero opens on the native runtime.
  sleep 2
  tmux_demo send-keys -t "$SESSION" F1 Home Enter
  wait_for_screen_text "DEMO CODEX RUNTIME" 120
  log "Demo scene: embedded native Agent runtime"
  sleep 3

  # F10 managed Agent launch: project -> optional Agent id -> default Agent
  # mode, Sol/high profile, dedicated runtime -> Launch. Leave the optional
  # ID blank and use the API's deterministic project-derived identifier. This
  # avoids depending on terminal-specific Tab focus behavior for the capture.
  tmux_demo send-keys -t "$SESSION" F10
  wait_for_screen_text "Launch Project Workspace" 40
  log "Demo scene: F10 managed workspace launch"
  sleep 4
  tmux_demo send-keys -t "$SESSION" Tab Tab Tab Tab Tab Enter
  wait_for_demo_agent "$DEMO_LAUNCHED_AGENT_ID" 1
  wait_for_screen_text "$DEMO_LAUNCHED_AGENT_ID" 120
  wait_for_screen_text "DEMO CODEX RUNTIME" 120
  log "Demo scene: newly launched managed Agent"
  sleep 4

  # Select the new caller, then launch a real root Operator plus its default
  # edit fork through the PBX palette. This demonstrates the discoverable
  # command surface while exercising the production launch action.
  tmux_demo send-keys -t "$SESSION" F1 Home Enter
  sleep 0.5
  log "Demo scene: Operator launch palette"
  send_palette "/operator start"
  wait_for_demo_agent "operator-0" 0
  wait_for_screen_text "operator-0" 160
  # Continue directly into the Operator-owned campaign. A newly registered
  # root can briefly lack its native session mapping while the fork starts;
  # that transitional fallback is diagnostic state, not a release-demo scene.
  sleep 0.5
  seed_operator_workflow

  # Campaigns are scoped to an Operator identity. Select the newly launched
  # root Operator before opening the campaign panel.
  tmux_demo send-keys -t "$SESSION" F5
  sleep 0.5
  tmux_demo send-keys -t "$SESSION" Home
  sleep 0.25
  tmux_demo send-keys -t "$SESSION" Enter
  wait_for_screen_text "5.6-SOL/XHIGH" 80
  send_palette "/campaigns"
  wait_for_screen_text "Agent PBX v2 documentation release" 80
  log "Demo scene: Operator campaign"
  sleep 4

  send_palette "/codex"
  log "Demo scene: Codex posture and configuration"
  sleep 3
  send_palette "/workerbee"
  log "Demo scene: WorkerBee runtime truth"
  sleep 3

  # Return to the managed caller before showing project-scoped engineering
  # surfaces. The palette then retains this explicit selection across tabs.
  tmux_demo send-keys -t "$SESSION" F1
  sleep 0.5
  tmux_demo send-keys -t "$SESSION" Home
  sleep 0.25
  tmux_demo send-keys -t "$SESSION" Enter
  wait_for_screen_text "5.6-SOL/HIGH" 80

  send_palette "/files"
  log "Demo scene: scoped files"
  sleep 2.5
  send_palette "/editor"
  log "Demo scene: guarded editor"
  sleep 2.5
  send_palette "/pr"
  log "Demo scene: pull requests"
  sleep 2.5
  send_palette "/issue"
  log "Demo scene: issues"
  sleep 2.5
  send_palette "/joplin"
  log "Demo scene: Joplin knowledge surface"
  sleep 4

  # Close on the newly launched Agent's native runtime.
  send_palette "/latest"
  wait_for_screen_text "5.6-SOL/HIGH" 80
  wait_for_screen_text "Agent · gpt-5.6-sol/high" 120
  log "Demo scene: closing native Agent runtime"
  sleep 3
  # Detach the recorder while the TUI session still exists. Quitting the TUI
  # first lets tmux switch the attached client to the demo Operator session,
  # which keeps asciinema alive until its timeout and can lose the cast.
  tmux_demo detach-client -s "$SESSION"
  if [[ -n "$RECORDER_PID" ]]; then
    kill -INT "$RECORDER_PID" >/dev/null 2>&1 || true
  fi
  log "Demo recording complete"
}

record_cast() {
  local attach_cmd
  printf -v attach_cmd 'tmux -f /dev/null -S %q attach-session -t %q' "$TMUX_SOCKET" "$SESSION"
  log "Recording ${CAST_PATH}"
  if [[ "$MANUAL" == "1" ]]; then
    log "Manual mode: interact with the TUI, then quit it to stop recording."
    "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" --cols "$COLS" --rows "$ROWS" -c "$attach_cmd" "$CAST_PATH"
    return
  fi

  # Run the recorder directly so the scene driver can stop it with SIGINT,
  # which makes asciinema flush the cast cleanly. Terminating a timeout wrapper
  # with SIGTERM can strand its writer process and leave a zero-byte artifact.
  "$ASCIINEMA_BIN" rec --overwrite --title "$TITLE" --cols "$COLS" --rows "$ROWS" -c "$attach_cmd" "$CAST_PATH" &
  RECORDER_PID="$!"
  sleep 0.5
  (
    set +e
    (
      set -e
      drive_demo
    )
    driver_status="$?"
    # A failed scene must also stop asciinema so cleanup does not wait for the
    # watchdog and an incomplete cast can never be rendered as the hero.
    if kill -0 "$RECORDER_PID" >/dev/null 2>&1; then
      kill -INT "$RECORDER_PID" >/dev/null 2>&1 || true
    fi
    exit "$driver_status"
  ) &
  DRIVER_PID="$!"
  (
    sleep "$MAX_RECORD_SECONDS"
    if kill -0 "$RECORDER_PID" >/dev/null 2>&1; then
      log "Recording reached ${MAX_RECORD_SECONDS}s timeout; stopping cleanly."
      kill -INT "$RECORDER_PID" >/dev/null 2>&1 || true
    fi
  ) &
  RECORDING_WATCHDOG_PID="$!"

  set +e
  wait "$RECORDER_PID"
  local recorder_status="$?"
  wait "$DRIVER_PID"
  local driver_status="$?"
  set -e
  RECORDER_PID=""
  kill "$RECORDING_WATCHDOG_PID" >/dev/null 2>&1 || true
  RECORDING_WATCHDOG_PID=""
  DRIVER_PID=""
  if [[ "$driver_status" != "0" ]]; then
    fail "scripted scene driver failed with status ${driver_status}; refusing to render an incomplete capture"
  fi
  if [[ ! -s "$CAST_PATH" ]]; then
    fail "asciinema did not produce a non-empty cast (status ${recorder_status})"
  fi
  if [[ "$recorder_status" != "0" && "$recorder_status" != "130" ]]; then
    log "asciinema exited with status ${recorder_status}; using the flushed cast."
  fi
}

render_gif() {
  if [[ "$RENDER" != "1" ]]; then
    return
  fi
  log "Rendering ${GIF_PATH}"
  local raw_gif="${OUT_DIR}/agent-pbx-tui-demo.raw.gif"
  local palette="${OUT_DIR}/agent-pbx-tui-demo.palette.png"
  local agg_output="$GIF_PATH"
  if [[ "$RENDER_EXACT_SIZE" == "1" ]]; then
    agg_output="$raw_gif"
  fi
  "$AGG_BIN" \
    --quiet \
    --font-size "$RENDER_FONT_SIZE" \
    --line-height "$RENDER_LINE_HEIGHT" \
    --cols "$COLS" \
    --rows "$ROWS" \
    --select "$RENDER_SELECT" \
    "$CAST_PATH" \
    "$agg_output"
  if [[ "$RENDER_EXACT_SIZE" != "1" ]]; then
    return
  fi
  "$FFMPEG_BIN" \
    -y \
    -v warning \
    -i "$raw_gif" \
    -vf "fps=${RENDER_FPS},scale=${RENDER_WIDTH}:${RENDER_HEIGHT}:flags=lanczos,palettegen=stats_mode=diff:max_colors=256" \
    "$palette"
  "$FFMPEG_BIN" \
    -y \
    -v warning \
    -i "$raw_gif" \
    -i "$palette" \
    -lavfi "fps=${RENDER_FPS},scale=${RENDER_WIDTH}:${RENDER_HEIGHT}:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
    "$GIF_PATH"
}

check_requirements
mkdir -p "$OUT_DIR" "$STATE_ROOT"

if [[ "$CHECK_ONLY" == "1" ]]; then
  if [[ "$LIVE_CODEX" != "1" ]]; then
    "$ROOT_DIR/scripts/demo/demo_codex.py" --version >/dev/null
  fi
  log "Recording prerequisites are available."
  exit 0
fi

prepare_demo_project

if [[ "$DEMO_FIXTURES" == "1" && "$LIVE_CODEX" != "1" ]]; then
  log "Using deterministic demo fixtures for Codex, WorkerBee, GitHub, and Joplin."
elif [[ "$DEMO_FIXTURES" == "1" ]]; then
  log "Using live Codex with deterministic WorkerBee, GitHub, and Joplin fixtures."
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
