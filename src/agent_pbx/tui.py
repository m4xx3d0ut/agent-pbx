from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, TypeVar
from urllib.parse import quote, urlparse

import httpx
from rich.color import Color, ColorParseError
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Click, Focus, Key, MouseDown, Resize
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .client import auth_headers
from . import tmux as tmux_support


TRUE_ENV_VALUES = {"1", "true", "yes", "on", "y", "enabled"}
FALSE_ENV_VALUES = {"0", "false", "no", "off", "n", "disabled", ""}
ATTENTION_EVENT_TYPES = {
    "agent_registered",
    "report_created",
    "command_acked",
    "operator_campaign_event",
}
STARRED_AGENT_COLUMN = "*"
DEFAULT_TUI_THEME = "cyberpunk"
MINIMAL_TUI_THEME = "minimal"
DEFAULT_CUSTOM_THEME_NAME = "1337"
THEME_1337_NAME = DEFAULT_CUSTOM_THEME_NAME
ADAPTIVE_TUI_LAYOUT = "adaptive"
DEFAULT_TUI_LAYOUT = ADAPTIVE_TUI_LAYOUT
SPLIT_TUI_LAYOUT = "split"
COMPACT_TUI_LAYOUT = "compact"
TINY_TUI_LAYOUT = "tiny"
LAYOUT_CHOICES = (
    (ADAPTIVE_TUI_LAYOUT.title(), ADAPTIVE_TUI_LAYOUT),
    (SPLIT_TUI_LAYOUT.title(), SPLIT_TUI_LAYOUT),
    (COMPACT_TUI_LAYOUT.title(), COMPACT_TUI_LAYOUT),
    (TINY_TUI_LAYOUT.title(), TINY_TUI_LAYOUT),
)
SPLIT_MIN_WIDTH = 100
SPLIT_MIN_HEIGHT = 24
COMPACT_MIN_WIDTH = 70
COMPACT_MIN_HEIGHT = 22
DEFAULT_SPLIT_PERCENT = 42
MIN_SPLIT_PERCENT = 25
MAX_SPLIT_PERCENT = 75
SPLIT_PERCENT_STEP = 5
PBX_REPORT_MODE = "report"
PBX_NOHUP_MODE = "nohup"
CALLER_AGENT_TYPE = "caller"
OPERATOR_AGENT_TYPE = "operator"
OPERATOR_ROLE_ROOT = "root"
OPERATOR_ROLE_FORK = "fork"
DEFAULT_OPERATOR_FORK_TRACK_ID = "default"
DEFAULT_OPERATOR_FORK_PURPOSE = "edit"
DEFAULT_OPERATOR_FORK_ACCESS_MODE = "edit"
REVIEW_OPERATOR_FORK_PURPOSE = "review"
REVIEW_OPERATOR_FORK_ACCESS_MODE = "review_readonly"
DEFAULT_OPERATOR_TMUX_SESSION = "agent-pbx-operators"
REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV = (
    "AGENT_PBX_TUI_REVIEW_MCP_APPROVAL_SERVERS"
)
DEFAULT_REVIEW_OPERATOR_MCP_APPROVAL_SERVERS = ("agent-pbx",)
REVIEW_OPERATOR_AGENT_PBX_APPROVED_TOOLS = (
    "pbx_register_agent",
    "pbx_report_turn",
    "pbx_operator_runbook",
    "pbx_operator_get_thread",
    "pbx_operator_campaign_status",
    "pbx_operator_report_assignment",
    "pbx_operator_route_review_escalation",
    "pbx_pr_context",
    "pbx_issue_context",
    "pbx_joplin_status",
    "pbx_joplin_create_document",
)
REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS = (
    "workerbee_v1_project_runbook_status",
    "workerbee_v1_project_status",
    "workerbee_v1_secret_policy_status",
    "workerbee_v1_profile_workload_status",
    "workerbee_v1_edge_link_status",
    "workerbee_v1_logs",
)
REVIEW_OPERATOR_MCP_APPROVED_TOOLS = {
    "agent-pbx": REVIEW_OPERATOR_AGENT_PBX_APPROVED_TOOLS,
    "workerbee": REVIEW_OPERATOR_WORKERBEE_APPROVED_TOOLS,
}
AGENT_PBX_TOKEN_ENV = "AGENT_PBX_TOKEN"
AGENT_PBX_SERVER_URL_ENV = "AGENT_PBX_SERVER_URL"
AGENT_PBX_MCP_URL_ENV = "AGENT_PBX_MCP_URL"
TINY_HOME_AGENTS = "agents"
TINY_HOME_OPERATORS = "operators"
TINY_HOME_EVENTS = "events"
OPERATOR_PANEL_MIN_RATIO = 0.10
OPERATOR_PANEL_DEFAULT_RATIO = 0.20
OPERATOR_PANEL_MAX_RATIO = 0.20
OPERATOR_PANEL_MIN_HEIGHT = 5
DEFAULT_EXPORT_DIR = Path("artifacts/thread-exports")
DEFAULT_SETTINGS_FILE = Path("agent-pbx/tui-settings.json")
DEFAULT_SLASH_COMMANDS_FILE = Path("agent-pbx/slash-commands.json")
DEFAULT_TMUX_CAPTURE_LINES = 0
DEFAULT_TMUX_REFRESH_SECONDS = 1.5
MIN_TMUX_REFRESH_SECONDS = 0.25
DEFAULT_AGENT_REFRESH_SECONDS = 2.0
LOW_POWER_AGENT_REFRESH_SECONDS = 15.0
MIN_AGENT_REFRESH_SECONDS = 1.0
DEFAULT_ATTENTION_BLINK_SECONDS = 0.8
LOW_POWER_ATTENTION_BLINK_SECONDS = 3.0
MIN_ATTENTION_BLINK_SECONDS = 0.5
CLIPBOARD_COPY_WAIT_SECONDS = 3.0
CLIPBOARD_COPY_POLL_SECONDS = 0.2
JOPLIN_TMUX_LOG_MIN_WAIT_SECONDS = 2.0
JOPLIN_TMUX_LOG_IDLE_SECONDS = 4.0
JOPLIN_TMUX_LOG_TIMEOUT_SECONDS = 90.0
CLIPBOARD_READ_COMMANDS = (
    ("wl-paste", ("wl-paste", "--no-newline")),
    ("xclip", ("xclip", "-selection", "clipboard", "-out")),
    ("xsel", ("xsel", "--clipboard", "--output")),
    ("pbpaste", ("pbpaste",)),
    ("termux-clipboard-get", ("termux-clipboard-get",)),
    ("tmux buffer", ("tmux", "show-buffer")),
)
FOLLOW_UP_MIN_HEIGHT = 8
FOLLOW_UP_MAX_HEIGHT = 15
SENT_MESSAGE_HISTORY_LIMIT = 100
FOLLOW_UP_NEWLINE_KEYS = {
    "shift+enter",
    "shift+return",
    "alt+enter",
    "ctrl+enter",
    "ctrl+j",
    "newline",
}
FOLLOW_UP_EDIT_KEYS = {
    "ctrl+w": "delete_word_left",
}
SLASH_COMPLETION_FORWARD_KEYS = {"tab"}
SLASH_COMPLETION_BACKWARD_KEYS = {"shift+tab", "shift_tab", "backtab"}
STALE_POLL_SECONDS = 120
QUEUED_COMMAND_WARN_SECONDS = 60
ACTIVE_POLL_SECONDS = 60
TMUX_LIVENESS_IDLE_SECONDS = 60
SLASH_COMMAND_FOLLOWUP_DELAY_SECONDS = 0.2
PLAN_MODE_FOLLOWUP_DELAY_SECONDS = 1.0
PLAN_PBX_CONTEXT_PROMPT = (
    "Use Agent PBX for planning. Register or update this session in Agent PBX "
    "report mode with pbx_mode=\"report\". Do not start nohup polling. When "
    "operator choice is needed, send pbx_report_turn with needs_input=true and "
    "structured plan_options so the TUI can present selectable choices."
)
PLAN_SLASH_COMMAND = "/plan"
PLAN_SELECTION_PATTERN = re.compile(
    r"^/plan(?:\s+sel(?:ect)?)?\s*:?\s*(?P<index>[1-9][0-9]*)"
    r"(?:\s+(?P<notes>.*))?$",
    re.IGNORECASE | re.DOTALL,
)
CODEX_NATIVE_PLAN_SELECTOR_CHOICES = {
    1: "start coding",
    2: "clear context & start",
    3: "stay in plan mode",
}
CODEX_NATIVE_SELECTOR_LINE_PATTERN = re.compile(
    r"^\s*(?P<marker>[›❯])?\s*(?P<index>[1-9][0-9]*)[.)]?\s+(?P<label>\S.*)$"
)
AGENT_JUMP_KEYS = {
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
    "8": 7,
    "9": 8,
    "0": 9,
}
MOUSE_FOCUS_TARGET_IDS = {
    "agents",
    "operators",
    "events",
    "detail",
    "tmux-stream",
    "tmux-message",
    "thread",
    "thread-detail",
    "files",
    "file-preview",
    "workerbee-detail",
    "pull-requests",
    "pull-request-detail",
    "issues",
    "issue-detail",
    "joplin-notes",
    "joplin-body",
    "campaigns",
    "campaign-detail",
    "message",
    "agent-id",
    "plan-options",
    "latest-plan-options",
}
MOUSE_FOCUS_CONTAINER_TARGETS = {
    "left": "#agents",
    "agent-actions": "#agents",
    "operator-actions": "#operators",
    "latest-tab": "#detail",
    "latest-plan-choice-panel": "#latest-plan-options",
    "tmux-panel": "#tmux-stream",
    "tmux-actions": "#tmux-message",
    "composer": "#message",
    "composer-inputs": "#message",
    "composer-actions": "#message",
    "thread-tab": "#thread",
    "thread-actions": "#thread",
    "files-tab": "#files",
    "files-actions": "#files",
    "workerbee-tab": "#workerbee-detail",
    "pull-requests-tab": "#pull-requests",
    "pull-request-actions": "#pull-request-detail",
    "issues-tab": "#issues",
    "issue-actions": "#issue-detail",
    "campaigns-tab": "#campaigns",
    "campaign-actions": "#campaign-detail",
    "joplin-tab": "#joplin-notes",
    "joplin-actions": "#joplin-body",
}
TMUX_WORKING_INFERABLE_STATUSES = {
    "blocked",
    "canceled",
    "cancelled",
    "complete",
    "completed",
    "done",
    "failed",
}
THEME_KEYS = (
    "primary",
    "secondary",
    "warning",
    "error",
    "success",
    "accent",
    "foreground",
    "background",
    "surface",
    "panel",
    "boost",
)
CODEX_STATUS_LINE_PATTERN = re.compile(
    r"\b(Working|Thinking|Reading|Editing|Running|Waiting)\b"
)
JOPLIN_NOTE_REF_PREFIX = "@joplin:"
JOPLIN_NOTE_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@joplin:[A-Za-z0-9_.~-]+)"
)
CALLER_AGENT_REF_PREFIX = "@caller:"
CALLER_AGENT_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@caller:[A-Za-z0-9_.~-]+)"
)
PULL_REQUEST_REF_PREFIX = "@pr:"
PULL_REQUEST_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@pr:(\d+))\b",
    re.IGNORECASE,
)
ISSUE_REF_PREFIX = "@issue:"
ISSUE_REF_PATTERN = re.compile(
    r"(?:^|(?<=[\s(\[{'\"`]))(@issue:(\d+))\b",
    re.IGNORECASE,
)
PULL_REQUEST_NATURAL_REF_PATTERN = re.compile(
    r"\b((?:PR|pull request)\s*#?\s*(\d+))\b",
    re.IGNORECASE,
)
ISSUE_NATURAL_REF_PATTERN = re.compile(
    r"\b((?:issues?)\s*#?\s*(\d+))\b",
    re.IGNORECASE,
)
CALLER_AGENT_REF_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")
BUILT_IN_PALETTE_COMMAND_NAMES = {
    "/refresh",
    "/detail",
    "/ping",
    "/unblock",
    "/working",
    "/cancel",
    "/esc",
    "/ctrlc",
    "/tmux",
    "/latest",
    "/thread",
    "/files",
    "/workerbee",
    "/campaigns",
    "/campaign monitor",
    "/campaign report",
    "/campaign copy",
    "/pr",
    "/pr refresh",
    "/pr review",
    "/pr validate",
    "/pr url",
    "/pr merge",
    "/issue",
    "/issue refresh",
    "/issue mitigate",
    "/issue url",
    "/issue clear",
    "/joplin",
    "/joplin refresh",
    "/joplin new",
    "/joplin rename",
    "/joplin delete",
    "/joplin copy",
    "/joplin copy report",
    "/joplin log start",
    "/joplin log stop",
    "/joplin save",
    "/joplin sync",
    "/plan",
    "/plan latest",
    "/plan thread",
    "/plan select 1",
    "/plan select 2",
    "/plan select 3",
    "/operator start",
    "/operator focus",
    "/operator history",
    "/operator resume",
    "/operator restart",
    "/operator stop",
    "/operator hide",
    "/operator purge",
    "/operator fork next",
    "/operator fork prev",
    "/operator fork review",
    "/gitstatus",
    "/gitdiff",
    "/gitpush",
    "/gitstageandcommit",
    "/commands reload",
    "/hide agent",
    "/show hidden agents",
    "/unhide agent",
    "/purge agent",
    "/theme cyberpunk",
    "/theme minimal",
    "/layout adaptive",
    "/layout split",
    "/layout compact",
    "/layout tiny",
}
PULL_REQUEST_SLASH_ACTIONS = {
    "/pr": "open",
    "/pr refresh": "refresh",
    "/pr review": "review",
    "/pr validate": "validate",
    "/pr url": "url",
    "/pr merge": "merge",
}
ISSUE_SLASH_ACTIONS = {
    "/issue": "open",
    "/issue refresh": "refresh",
    "/issue mitigate": "mitigate",
    "/issue url": "url",
    "/issue clear": "clear",
}
CAMPAIGN_SLASH_ACTIONS = {
    "/campaign monitor": "monitor",
    "/campaign report": "report",
    "/campaign copy": "copy",
}
JOPLIN_SLASH_ACTIONS = {
    "/joplin": "open",
    "/joplin refresh": "refresh",
    "/joplin new": "new",
    "/joplin rename": "rename",
    "/joplin delete": "delete",
    "/joplin copy": "copy",
    "/joplin copy report": "copy-report",
    "/joplin log start": "log-start",
    "/joplin log stop": "log-stop",
    "/joplin save": "save",
    "/joplin sync": "sync",
}
JOPLIN_SHORTCUT_ACTIONS = {
    "n": ("new", "new"),
    "m": ("rename", "rename"),
    "d": ("delete", "delete"),
    "s": ("save", "save"),
    "r": ("refresh", "refresh"),
    "c": ("copy", "copy"),
    "l": ("log-start", "log on"),
    "x": ("log-stop", "log off"),
    "u": ("sync", "sync"),
}
JOPLIN_SHORTCUT_HINT = (
    "Joplin keys: Ctrl+G, or j outside note body, then the button key"
)
WidgetType = TypeVar("WidgetType")


@dataclass
class TmuxLiveness:
    pane_id: str | None = None
    capture_hash: str | None = None
    last_capture_at: float | None = None
    last_changed_at: float | None = None
    state: str = "unknown"


@dataclass(frozen=True)
class CustomSlashCommand:
    name: str
    description: str
    prompt: str
    arg_label: str = "Argument"
    arg_placeholder: str = ""
    arg_required: bool = False

    @property
    def uses_arg(self) -> bool:
        return "{arg}" in self.prompt


@dataclass(frozen=True)
class PlanChoice:
    label: str
    id: str | None = None
    description: str | None = None
    raw: Any = None

    @property
    def display_label(self) -> str:
        if self.description:
            return f"{self.label}: {self.description}"
        return self.label

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {"label": self.label}
        if self.id:
            data["id"] = self.id
        if self.description:
            data["description"] = self.description
        if self.raw is not None:
            data["option"] = self.raw
        return data


@dataclass(frozen=True)
class OperatorSessionCandidate:
    session_id: str
    timestamp: float
    source: str
    path: str = ""
    summary: str = ""


@dataclass(frozen=True)
class SlashCompletionContext:
    input_id: str
    line: int
    start_col: int
    end_col: int
    prefix: str


@dataclass(frozen=True)
class SlashCompletionState:
    input_id: str
    line: int
    start_col: int
    original_prefix: str
    matches: tuple[str, ...]
    index: int


@dataclass(frozen=True)
class FileCompletionContext:
    input_id: str
    line: int
    start_col: int
    end_col: int
    prefix: str
    directory: str
    name_prefix: str


@dataclass(frozen=True)
class FileCompletionState:
    input_id: str
    line: int
    start_col: int
    original_prefix: str
    matches: tuple[str, ...]
    index: int


@dataclass(frozen=True)
class JoplinNoteReference:
    token: str
    note_id: str
    title: str
    body: str
    updated_time: int | float | None = None
    scope_agent_id: str | None = None
    scope_project: str | None = None
    caller_token: str | None = None


@dataclass(frozen=True)
class GitHubReferenceScope:
    kind: str
    token: str
    number: int
    scope_agent_id: str
    caller_token: str | None = None


@dataclass(frozen=True)
class GitHubPromptReference:
    kind: str
    token: str
    number: int
    scope_agent_id: str
    scope_project: str
    payload: dict[str, Any]
    caller_token: str | None = None


@dataclass(frozen=True)
class CallerAgentReference:
    token: str
    agent_id: str
    name: str | None
    project: str
    status: str
    pbx_mode: str
    pbx_active: bool
    active_campaign_count: int
    tmux_pane: str | None = None
    active_operator_fork_id: str | None = None
    active_fork_agent_id: str | None = None
    active_fork_track_id: str | None = None
    active_fork_purpose: str | None = None
    active_fork_source_session_id: str | None = None
    active_fork_tmux_pane: str | None = None


@dataclass(frozen=True)
class PlanSelection:
    index: int
    notes: str = ""


CYBERPUNK_PALETTE = {
    "primary": "#00e5ff",
    "secondary": "#9b5cff",
    "warning": "#fcee09",
    "error": "#ff2e88",
    "success": "#38ff9c",
    "accent": "#ff3df2",
    "foreground": "#f2f7ff",
    "background": "#070b16",
    "surface": "#101826",
    "panel": "#1a102a",
    "boost": "#2b174b",
}
MINIMAL_PALETTE = {
    "primary": "#ffffff",
    "secondary": "#c0c0c0",
    "warning": "#ffff00",
    "error": "#ff0000",
    "success": "#00ff00",
    "accent": "#ffffff",
    "foreground": "#ffffff",
    "background": "#000000",
    "surface": "#000000",
    "panel": "#000000",
    "boost": "#ffffff",
}
DEFAULT_CUSTOM_PALETTE = {
    "primary": "#00ff00",
    "secondary": "#00ff00",
    "warning": "#00ff00",
    "error": "#00ff00",
    "success": "#00ff00",
    "accent": "#00ff00",
    "foreground": "#00ff00",
    "background": "#000000",
    "surface": "#000000",
    "panel": "#000000",
    "boost": "#00ff00",
}


def build_theme(name: str, palette: dict[str, str]) -> Theme:
    return Theme(
        name=name,
        primary=palette["primary"],
        secondary=palette["secondary"],
        warning=palette["warning"],
        error=palette["error"],
        success=palette["success"],
        accent=palette["accent"],
        foreground=palette["foreground"],
        background=palette["background"],
        surface=palette["surface"],
        panel=palette["panel"],
        boost=palette["boost"],
        dark=True,
    )


THEME_CYBERPUNK = build_theme(DEFAULT_TUI_THEME, CYBERPUNK_PALETTE)
THEME_MINIMAL = build_theme(MINIMAL_TUI_THEME, MINIMAL_PALETTE)


def env_flag(*names: str, default: bool = False) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        parsed = parse_env_bool(value)
        if parsed is not None:
            return parsed
    return default


def env_flag_value(*names: str) -> bool | None:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        parsed = parse_env_bool(value)
        if parsed is not None:
            return parsed
    return None


def parse_env_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in TRUE_ENV_VALUES:
        return True
    if normalized in FALSE_ENV_VALUES:
        return False
    return None


def env_custom_theme_name(default: str = DEFAULT_CUSTOM_THEME_NAME) -> str:
    name = os.getenv("AGENT_PBX_TUI_CUSTOM_THEME_NAME", default).strip()
    return name or default


def env_color(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        Color.parse(value)
    except ColorParseError:
        return default
    return value


def env_custom_palette() -> dict[str, str]:
    return {
        key: env_color(
            f"AGENT_PBX_TUI_CUSTOM_{key.upper()}",
            DEFAULT_CUSTOM_PALETTE[key],
        )
        for key in THEME_KEYS
    }


def is_custom_theme_selector(theme: str, custom_name: str) -> bool:
    normalized = theme.strip().lower()
    return normalized in {
        custom_name.strip().lower(),
        DEFAULT_CUSTOM_THEME_NAME,
        "leet",
    }


def is_minimal_theme_selector(theme: str) -> bool:
    normalized = theme.strip().lower().replace("_", "-")
    return normalized in {
        MINIMAL_TUI_THEME,
        "basic",
        "black-white",
        "blackwhite",
        "bw",
        "compat",
        "compatible",
        "mono",
        "monochrome",
    }


def is_default_theme_selector(theme: str) -> bool:
    normalized = theme.strip().lower().replace("_", "-")
    return normalized in {
        "",
        "default",
        DEFAULT_TUI_THEME,
        "github",
        "github dark",
        "github-dark",
    }


def env_theme(
    default: str = DEFAULT_TUI_THEME, custom_name: str | None = None
) -> str:
    override = env_theme_value(custom_name=custom_name)
    return override or default


def env_theme_value(custom_name: str | None = None) -> str | None:
    resolved_custom_name = custom_name or env_custom_theme_name()
    if env_flag_value("AGENT_PBX_TUI_1337"):
        return resolved_custom_name
    if "AGENT_PBX_TUI_THEME" not in os.environ:
        return None
    theme = os.getenv("AGENT_PBX_TUI_THEME", "").strip().lower()
    if is_custom_theme_selector(theme, resolved_custom_name):
        return resolved_custom_name
    if is_minimal_theme_selector(theme):
        return MINIMAL_TUI_THEME
    if is_default_theme_selector(theme):
        return DEFAULT_TUI_THEME
    return None


def resolve_layout(layout_name: str) -> str:
    normalized = layout_name.strip().lower().replace("_", "-")
    if normalized in {"", "default", "auto", "adaptive", "responsive"}:
        return ADAPTIVE_TUI_LAYOUT
    if normalized in {"split", "wide", "desktop"}:
        return SPLIT_TUI_LAYOUT
    if normalized in {"compact", "mobile", "small", "single", "single-pane"}:
        return COMPACT_TUI_LAYOUT
    if normalized in {"tiny", "pocket", "pocketchip", "mini"}:
        return TINY_TUI_LAYOUT
    return DEFAULT_TUI_LAYOUT


def env_layout_value() -> str | None:
    if "AGENT_PBX_TUI_LAYOUT" not in os.environ:
        return None
    return resolve_layout(os.getenv("AGENT_PBX_TUI_LAYOUT", ""))


def clamp_split_percent(value: int) -> int:
    return max(MIN_SPLIT_PERCENT, min(MAX_SPLIT_PERCENT, value))


def operator_panel_height(left_height: int) -> int:
    if left_height <= 0:
        return OPERATOR_PANEL_MIN_HEIGHT
    minimum = max(OPERATOR_PANEL_MIN_HEIGHT, round(left_height * OPERATOR_PANEL_MIN_RATIO))
    maximum = max(minimum, round(left_height * OPERATOR_PANEL_MAX_RATIO))
    desired = round(left_height * OPERATOR_PANEL_DEFAULT_RATIO)
    return max(minimum, min(maximum, desired))


def agent_pbx_mcp_url(server: str) -> str:
    return f"{server.rstrip('/')}/mcp"


def codex_command_argv(codex_command: str) -> list[str]:
    argv = shlex.split(codex_command.strip())
    return argv or ["codex"]


def codex_mcp_add_command(codex_command: str, mcp_url: str) -> list[str]:
    return [
        *codex_command_argv(codex_command),
        "mcp",
        "add",
        "agent-pbx",
        "--url",
        mcp_url,
        "--bearer-token-env-var",
        AGENT_PBX_TOKEN_ENV,
    ]


def codex_mcp_remove_command(codex_command: str) -> list[str]:
    return [*codex_command_argv(codex_command), "mcp", "remove", "agent-pbx"]


def codex_config_mcp_key(server_name: str, setting: str) -> str:
    return f"mcp_servers.{json.dumps(server_name)}.{setting}"


def codex_config_override(key: str, value: object) -> str:
    return f"{key}={json.dumps(value)}"


def review_operator_mcp_approval_server_names() -> tuple[str, ...]:
    configured = os.getenv(REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV, "").strip()
    if configured:
        names = tuple(
            name.strip()
            for name in configured.split(",")
            if name.strip()
        )
    else:
        names = DEFAULT_REVIEW_OPERATOR_MCP_APPROVAL_SERVERS
    if "agent-pbx" in names:
        return names
    return ("agent-pbx", *names)


def review_operator_mcp_config_overrides(
    server_names: Iterable[str] | None = None,
) -> list[str]:
    overrides: list[str] = []
    names = server_names or review_operator_mcp_approval_server_names()
    for server_name in names:
        approved_tools = REVIEW_OPERATOR_MCP_APPROVED_TOOLS.get(server_name)
        if not approved_tools:
            continue
        overrides.extend(
            [
                codex_config_override(
                    codex_config_mcp_key(server_name, "enabled_tools"),
                    list(approved_tools),
                ),
                codex_config_override(
                    codex_config_mcp_key(
                        server_name,
                        "default_tools_approval_mode",
                    ),
                    "approve",
                ),
            ]
        )
    return overrides


def process_error_summary(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        return detail
    return f"exit code {result.returncode}"


def configure_codex_mcp(
    codex_command: str,
    mcp_url: str,
    *,
    timeout: float = 15.0,
) -> None:
    add_command = codex_mcp_add_command(codex_command, mcp_url)
    add = subprocess.run(
        add_command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if add.returncode == 0:
        return
    add_failure = process_error_summary(add).lower()
    if "exist" not in add_failure and "already" not in add_failure:
        raise RuntimeError(f"add failed: {process_error_summary(add)}")

    remove = subprocess.run(
        codex_mcp_remove_command(codex_command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    retry = subprocess.run(
        add_command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if retry.returncode == 0:
        return

    details = [f"add failed: {process_error_summary(add)}"]
    if remove.returncode != 0:
        details.append(f"remove failed: {process_error_summary(remove)}")
    details.append(f"retry failed: {process_error_summary(retry)}")
    raise RuntimeError("; ".join(details))


def env_split_percent_value() -> int | None:
    value = os.getenv("AGENT_PBX_TUI_SPLIT_PERCENT", "").strip()
    if not value:
        return None
    parsed = int_value(value)
    return clamp_split_percent(parsed) if parsed is not None else None


def env_export_dir(default: Path = DEFAULT_EXPORT_DIR) -> Path:
    return env_export_dir_value() or default


def env_export_dir_value() -> Path | None:
    value = os.getenv("AGENT_PBX_TUI_EXPORT_DIR", "").strip()
    return Path(value).expanduser() if value else None


def env_tmux_capture_lines_value() -> int | None:
    value = os.getenv("AGENT_PBX_TUI_TMUX_CAPTURE_LINES", "").strip()
    if not value:
        return None
    parsed = int_value(value)
    return parsed if parsed is not None and parsed >= 0 else None


def env_tmux_refresh_seconds_value() -> float | None:
    value = os.getenv("AGENT_PBX_TUI_TMUX_REFRESH_SECONDS", "").strip()
    if not value:
        return None
    parsed = float_value(value)
    if parsed is None or parsed <= 0:
        return None
    return max(MIN_TMUX_REFRESH_SECONDS, parsed)


def env_agent_refresh_seconds_value() -> float | None:
    value = os.getenv("AGENT_PBX_TUI_AGENT_REFRESH_SECONDS", "").strip()
    if not value:
        return None
    parsed = float_value(value)
    if parsed is None or parsed <= 0:
        return None
    return max(MIN_AGENT_REFRESH_SECONDS, parsed)


def env_attention_blink_seconds_value() -> float | None:
    value = os.getenv("AGENT_PBX_TUI_ATTENTION_BLINK_SECONDS", "").strip()
    if not value:
        return None
    parsed = float_value(value)
    if parsed is None or parsed <= 0:
        return None
    return max(MIN_ATTENTION_BLINK_SECONDS, parsed)


def is_local_server_url(server: str) -> bool:
    parsed = urlparse(server)
    host = (parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def tmux_features_available(
    *,
    tmux_direct_enabled: bool = False,
    tmux_bin: str = "tmux",
) -> bool:
    if shutil.which(tmux_bin) is None:
        return False
    return bool(os.getenv("TMUX")) or tmux_direct_enabled or env_flag(
        "AGENT_PBX_TUI_TMUX_SHOW"
    )


def env_settings_file() -> Path:
    value = os.getenv("AGENT_PBX_TUI_SETTINGS_FILE", "").strip()
    if value:
        return Path(value).expanduser()
    config_home = os.getenv("XDG_CONFIG_HOME", "").strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / DEFAULT_SETTINGS_FILE


def load_tui_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def env_slash_commands_file() -> Path:
    value = os.getenv("AGENT_PBX_TUI_COMMANDS_FILE", "").strip()
    if value:
        return Path(value).expanduser()
    config_home = os.getenv("XDG_CONFIG_HOME", "").strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / DEFAULT_SLASH_COMMANDS_FILE


def parse_custom_slash_commands(
    data: object,
    *,
    built_in_names: set[str] | None = None,
) -> tuple[list[CustomSlashCommand], list[str]]:
    if not isinstance(data, dict):
        return [], ["custom slash command file must contain a JSON object"]
    raw_commands = data.get("commands")
    if raw_commands is None:
        return [], []
    if not isinstance(raw_commands, list):
        return [], ["custom slash command field 'commands' must be a list"]

    reserved_names = {name.strip() for name in built_in_names or set()}
    seen_names = set(reserved_names)
    commands: list[CustomSlashCommand] = []
    errors: list[str] = []
    for index, item in enumerate(raw_commands, start=1):
        prefix = f"commands[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = item.get("name")
        prompt = item.get("prompt")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix}.name is required")
            continue
        normalized_name = name.strip()
        if not normalized_name.startswith("/"):
            errors.append(f"{prefix}.name must start with '/'")
            continue
        if normalized_name in seen_names:
            errors.append(f"{prefix}.name duplicates a built-in or earlier command")
            continue
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{prefix}.prompt is required")
            continue
        clean_prompt = prompt.strip()
        placeholders = set(re.findall(r"{([^{}]+)}", clean_prompt))
        unsupported = sorted(placeholders - {"arg"})
        if unsupported:
            errors.append(
                f"{prefix}.prompt uses unsupported placeholder(s): "
                + ", ".join(f"{{{value}}}" for value in unsupported)
            )
            continue
        description = item.get("description")
        arg_label = item.get("arg_label")
        arg_placeholder = item.get("arg_placeholder")
        arg_required = item.get("arg_required")
        commands.append(
            CustomSlashCommand(
                name=normalized_name,
                description=(
                    description.strip()
                    if isinstance(description, str) and description.strip()
                    else "Custom slash command"
                ),
                prompt=clean_prompt,
                arg_label=(
                    arg_label.strip()
                    if isinstance(arg_label, str) and arg_label.strip()
                    else "Argument"
                ),
                arg_placeholder=(
                    arg_placeholder.strip()
                    if isinstance(arg_placeholder, str)
                    and arg_placeholder.strip()
                    else ""
                ),
                arg_required=arg_required if isinstance(arg_required, bool) else False,
            )
        )
        seen_names.add(normalized_name)
    return commands, errors


def load_custom_slash_commands(
    path: Path,
    *,
    built_in_names: set[str] | None = None,
) -> tuple[list[CustomSlashCommand], list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"unable to read {path}: {exc}"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"]
    return parse_custom_slash_commands(data, built_in_names=built_in_names)


def render_custom_slash_prompt(command: CustomSlashCommand, arg: str = "") -> str:
    return command.prompt.replace("{arg}", arg.strip())


def render_plan_prompt(message: str) -> str:
    return f"{PLAN_PBX_CONTEXT_PROMPT}\n\n{message.strip()}"


def codex_native_plan_selector_indices(text: str) -> tuple[int, ...]:
    normalized_lines = [
        " ".join(line.casefold().split()) for line in text.splitlines()
    ]
    if not normalized_lines:
        return ()
    start_terms = (
        "start coding",
        "start implementation",
        "begin coding",
        "start work",
    )
    stay_terms = (
        "stay in plan",
        "stay in planning",
        "keep planning",
        "continue planning",
        "remain in plan",
    )
    indexed_labels: dict[int, str] = {}
    marked_indices: set[int] = set()
    for line in normalized_lines[-40:]:
        match = CODEX_NATIVE_SELECTOR_LINE_PATTERN.match(line)
        if match is None:
            continue
        label = match.group("label").strip()
        if len(label) > 160:
            continue
        index = int(match.group("index"))
        indexed_labels[index] = label
        if match.group("marker"):
            marked_indices.add(index)

    def choice_line(index: int, terms: tuple[str, ...]) -> bool:
        label = indexed_labels.get(index, "")
        return any(term in label for term in terms)

    if all(
        (
            choice_line(1, start_terms),
            choice_line(2, ("clear", "discard")),
            choice_line(3, stay_terms),
        )
    ):
        return tuple(sorted(indexed_labels))
    if marked_indices and len(indexed_labels) >= 2:
        return tuple(sorted(indexed_labels))
    return ()


def contains_codex_native_plan_selector(text: str) -> bool:
    return bool(codex_native_plan_selector_indices(text))


def built_in_palette_command_names(custom_theme_name: str = DEFAULT_CUSTOM_THEME_NAME) -> set[str]:
    names = set(BUILT_IN_PALETTE_COMMAND_NAMES)
    names.add(f"/theme {custom_theme_name}")
    return names


def read_clipboard_text() -> tuple[str, str]:
    errors: list[str] = []
    for label, command in CLIPBOARD_READ_COMMANDS:
        executable = command[0]
        if shutil.which(executable) is None:
            continue
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{label}: {exc}")
            continue
        if result.returncode == 0:
            return result.stdout.rstrip("\n"), label
        message = (result.stderr or result.stdout).strip()
        if message:
            errors.append(f"{label}: {message}")
    if errors:
        raise RuntimeError("; ".join(errors))
    raise RuntimeError(
        "no clipboard reader found; install wl-paste, xclip, xsel, pbpaste, "
        "termux-clipboard-get, or run inside tmux with a readable tmux buffer"
    )


def latest_nonlocal_sent_prompt(
    history: list[str],
    *,
    is_local_command: Callable[[str], bool],
) -> str:
    for message in reversed(history):
        clean = message.strip()
        if not clean:
            continue
        if is_local_command(clean):
            continue
        return clean
    return ""


def joplin_tmux_copy_title(prompt: str) -> str:
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "")
    if not first_line:
        return "Codex Response"
    if len(first_line) > 96:
        first_line = f"{first_line[:93].rstrip()}..."
    return f"Codex Response - {first_line}"


def format_joplin_tmux_response_copy_body(prompt: str, response: str) -> str:
    clean_prompt = prompt.strip()
    clean_response = response.strip()
    prompt_body = clean_prompt or "_No prompt was recorded by the Agent PBX TUI._"
    response_body = clean_response or "_No copied response text was available._"
    return "\n".join(
        [
            "## Prompt",
            "",
            "```text",
            prompt_body,
            "```",
            "",
            "## Response",
            "",
            response_body,
        ]
    )


def bool_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def str_setting(settings: dict[str, Any], key: str, default: str) -> str:
    value = settings.get(key)
    return value if isinstance(value, str) and value.strip() else default


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "agent"


def short_stable_hash(value: str, length: int = 8) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def normalize_project_path(path: str) -> str | None:
    clean = path.strip()
    if clean.startswith("/"):
        return None
    parts: list[str] = []
    for part in clean.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or "."


def split_file_completion_prefix(prefix: str) -> tuple[str, str] | None:
    if "/" not in prefix:
        return ".", prefix
    directory, _, name_prefix = prefix.rpartition("/")
    normalized = normalize_project_path(directory or ".")
    if normalized is None:
        return None
    return normalized, name_prefix


def joplin_note_ref_slug(note: dict[str, Any]) -> str:
    title = str(note.get("title") or "").strip()
    note_id = str(note.get("id") or "").strip()
    return slugify(title or note_id[:12] or "note")


def joplin_note_ref_tokens(
    notes: Iterable[dict[str, Any]],
) -> dict[str, str]:
    note_list = [note for note in notes if note.get("id")]
    slug_counts: dict[str, int] = {}
    short_id_counts: dict[str, int] = {}
    for note in note_list:
        slug = joplin_note_ref_slug(note).lower()
        short_id = str(note.get("id") or "")[:8].lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        if short_id:
            short_id_counts[short_id] = short_id_counts.get(short_id, 0) + 1
    tokens: dict[str, str] = {}
    for note in note_list:
        note_id = str(note.get("id") or "")
        slug = joplin_note_ref_slug(note)
        slug_key = slug.lower()
        if slug_counts.get(slug_key, 0) == 1:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{slug}"] = note_id
        else:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{slug}~{note_id[:8]}"] = note_id
        tokens[f"{JOPLIN_NOTE_REF_PREFIX}{note_id}"] = note_id
        if note_id[:8] and short_id_counts.get(note_id[:8].lower(), 0) == 1:
            tokens[f"{JOPLIN_NOTE_REF_PREFIX}{note_id[:8]}"] = note_id
    return tokens


def joplin_note_ref_completion_tokens(
    notes: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    note_list = [note for note in notes if note.get("id")]
    slug_counts: dict[str, int] = {}
    for note in note_list:
        slug = joplin_note_ref_slug(note).lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
    tokens: list[str] = []
    seen: set[str] = set()
    for note in note_list:
        note_id = str(note.get("id") or "")
        slug = joplin_note_ref_slug(note)
        token = (
            f"{JOPLIN_NOTE_REF_PREFIX}{slug}"
            if slug_counts.get(slug.lower(), 0) == 1
            else f"{JOPLIN_NOTE_REF_PREFIX}{slug}~{note_id[:8]}"
        )
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        tokens.append(token)
    return tuple(sorted(tokens, key=str.lower))


def joplin_note_ref_tokens_in_message(message: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in JOPLIN_NOTE_REF_PATTERN.finditer(message):
        token = match.group(1)
        key = token.lower()
        if key not in seen:
            seen.add(key)
            tokens.append(token)
    return tuple(tokens)


def caller_agent_ref_slug(agent: dict[str, Any]) -> str:
    name = str(agent.get("name") or "").strip()
    agent_id = str(agent.get("agent_id") or "").strip()
    return slugify(name or agent_id)


def caller_agent_ref_token_entries(
    agents: Iterable[dict[str, Any]],
    *,
    primary_only: bool = False,
) -> list[tuple[str, str]]:
    agent_list = [agent for agent in agents if agent.get("agent_id")]
    slug_counts: dict[str, int] = {}
    for agent in agent_list:
        slug = caller_agent_ref_slug(agent).lower()
        slug_counts[slug] = slug_counts.get(slug, 0) + 1

    entries: list[tuple[str, str]] = []
    for agent in agent_list:
        agent_id = str(agent.get("agent_id") or "")
        slug = caller_agent_ref_slug(agent)
        slug_key = slug.lower()
        suffix = (
            ""
            if slug_counts.get(slug_key, 0) == 1
            else f"~{short_stable_hash(agent_id)}"
        )
        entries.append((f"{CALLER_AGENT_REF_PREFIX}{slug}{suffix}", agent_id))
        if primary_only or not CALLER_AGENT_REF_SAFE_VALUE.match(agent_id):
            continue
        entries.append((f"{CALLER_AGENT_REF_PREFIX}{agent_id}", agent_id))
    return entries


def unique_reference_token_map(entries: Iterable[tuple[str, str]]) -> dict[str, str]:
    by_lower: dict[str, set[str]] = {}
    original_token: dict[str, str] = {}
    for token, value in entries:
        lower = token.lower()
        by_lower.setdefault(lower, set()).add(value)
        original_token.setdefault(lower, token)
    return {
        original_token[lower]: next(iter(values))
        for lower, values in by_lower.items()
        if len(values) == 1
    }


def caller_agent_ref_tokens(
    agents: Iterable[dict[str, Any]],
) -> dict[str, str]:
    return unique_reference_token_map(caller_agent_ref_token_entries(agents))


def caller_agent_ref_completion_tokens(
    agents: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    token_map = unique_reference_token_map(
        caller_agent_ref_token_entries(agents, primary_only=True)
    )
    return tuple(sorted(token_map, key=str.lower))


def caller_agent_ref_tokens_in_message(message: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in CALLER_AGENT_REF_PATTERN.finditer(message):
        token = match.group(1)
        key = token.lower()
        if key not in seen:
            seen.add(key)
            tokens.append(token)
    return tuple(tokens)


def github_ref_number(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def pull_request_ref_completion_tokens(
    pulls: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    numbers = {
        number
        for item in pulls
        if (number := github_ref_number(item.get("number"))) is not None
    }
    return tuple(f"{PULL_REQUEST_REF_PREFIX}{number}" for number in sorted(numbers))


def issue_ref_completion_tokens(
    issues: Iterable[dict[str, Any]],
) -> tuple[str, ...]:
    numbers = {
        number
        for item in issues
        if (number := github_ref_number(item.get("number"))) is not None
    }
    return tuple(f"{ISSUE_REF_PREFIX}{number}" for number in sorted(numbers))


def markdown_fence_for(body: str) -> str:
    fence = "```"
    while fence in body:
        fence += "`"
    return fence


def format_joplin_note_references_for_prompt(
    message: str,
    references: list[JoplinNoteReference],
) -> str:
    if not references:
        return message
    lines = [
        message.rstrip(),
        "",
        "---",
        "",
        "## Joplin Note References",
        "",
    ]
    for index, reference in enumerate(references, start=1):
        body = reference.body.rstrip() or "(empty note)"
        fence = markdown_fence_for(body)
        lines.extend(
            [
                f"### {index}. {reference.title or reference.note_id}",
                "",
                f"- Ref: `{reference.token}`",
                f"- Note ID: `{reference.note_id}`",
            ]
        )
        if reference.scope_agent_id:
            lines.append(f"- Agent Scope: `{reference.scope_agent_id}`")
        if reference.scope_project:
            lines.append(f"- Project: `{reference.scope_project}`")
        if reference.caller_token:
            lines.append(f"- Caller Ref: `{reference.caller_token}`")
        if reference.updated_time is not None:
            lines.append(f"- Updated: `{reference.updated_time}`")
        lines.extend(["", f"{fence}markdown", body, fence, ""])
    return "\n".join(lines).rstrip() + "\n"


def format_caller_agent_references_for_prompt(
    message: str,
    references: list[CallerAgentReference],
) -> str:
    if not references:
        return message
    lines = [
        message.rstrip(),
        "",
        "---",
        "",
        "## Caller Agent References",
        "",
    ]
    for index, reference in enumerate(references, start=1):
        title = reference.name or reference.agent_id
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- Ref: `{reference.token}`",
                f"- Agent ID: `{reference.agent_id}`",
                f"- Project: `{reference.project}`",
                f"- Status: `{reference.status}`",
                f"- PBX Mode: `{reference.pbx_mode}`",
                f"- PBX Active: `{str(reference.pbx_active).lower()}`",
                f"- Active Campaigns: `{reference.active_campaign_count}`",
            ]
        )
        if reference.tmux_pane:
            lines.append(f"- Tmux Pane: `{reference.tmux_pane}`")
        if reference.active_operator_fork_id:
            lines.append(f"- Active Operator Fork ID: `{reference.active_operator_fork_id}`")
        if reference.active_fork_agent_id:
            lines.append(f"- Active Fork Agent ID: `{reference.active_fork_agent_id}`")
        if reference.active_fork_track_id:
            lines.append(f"- Active Fork Track: `{reference.active_fork_track_id}`")
        if reference.active_fork_purpose:
            lines.append(f"- Active Fork Purpose: `{reference.active_fork_purpose}`")
        if reference.active_fork_source_session_id:
            lines.append(
                f"- Active Fork Source Session: `{reference.active_fork_source_session_id}`"
            )
        if reference.active_fork_tmux_pane:
            lines.append(f"- Active Fork Tmux Pane: `{reference.active_fork_tmux_pane}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def float_value(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def int_value(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    value = int_value(settings.get(key))
    return value if value is not None else default


def float_map_setting(settings: dict[str, Any], key: str) -> dict[str, float]:
    value = settings.get(key)
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for item_key, item_value in value.items():
        parsed = float_value(item_value)
        if isinstance(item_key, str) and parsed is not None:
            result[item_key] = parsed
    return result


def str_map_setting(settings: dict[str, Any], key: str) -> dict[str, str]:
    value = settings.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        item_key: item_value
        for item_key, item_value in value.items()
        if isinstance(item_key, str) and isinstance(item_value, str)
    }


def bool_map_setting(settings: dict[str, Any], key: str) -> dict[str, bool]:
    value = settings.get(key)
    if not isinstance(value, dict):
        return {}
    result: dict[str, bool] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_key, str):
            continue
        if isinstance(item_value, bool):
            result[item_key] = item_value
            continue
        if isinstance(item_value, str):
            parsed = parse_env_bool(item_value)
            if parsed is not None:
                result[item_key] = parsed
    return result


def str_set_setting(settings: dict[str, Any], key: str) -> set[str]:
    value = settings.get(key)
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item.strip()}


def list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def plan_choice_from_value(value: Any) -> PlanChoice | None:
    if isinstance(value, str):
        label = value.strip()
        return PlanChoice(label=label, raw=value) if label else None
    if isinstance(value, dict):
        raw_id = value.get("id")
        option_id = str(raw_id).strip() if raw_id is not None else None
        raw_label = value.get("label")
        label = str(raw_label).strip() if raw_label is not None else ""
        if not label and option_id:
            label = option_id
        raw_description = value.get("description")
        description = (
            str(raw_description).strip() if raw_description is not None else None
        )
        if description == "":
            description = None
        return (
            PlanChoice(
                label=label,
                id=option_id or None,
                description=description,
                raw=value,
            )
            if label
            else None
        )
    label = str(value).strip()
    return PlanChoice(label=label, raw=value) if label else None


def plan_choices_from_value(value: object) -> list[PlanChoice]:
    choices: list[PlanChoice] = []
    for option in list_value(value):
        choice = plan_choice_from_value(option)
        if choice is not None:
            choices.append(choice)
    return choices


def parse_plan_selection_command(message: str) -> PlanSelection | None:
    match = PLAN_SELECTION_PATTERN.match(message.strip())
    if match is None:
        return None
    index = int(match.group("index"))
    notes = (match.group("notes") or "").strip()
    return PlanSelection(index=index, notes=notes)


def is_plan_toggle_message(message: str) -> bool:
    return message.strip().lower() == PLAN_SLASH_COMMAND


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def format_count(value: int) -> str:
    if value < 1000:
        return str(value)
    return f"{value // 1000}k"


class FollowUpTextArea(TextArea):
    async def _on_key(self, event: Key) -> None:
        completion_direction = slash_completion_direction(event)
        if completion_direction is not None:
            complete_file_async = getattr(self.app, "complete_file_reference_async", None)
            if complete_file_async is not None and await complete_file_async(
                self,
                direction=completion_direction,
            ):
                event.stop()
                event.prevent_default()
                return
            complete_file = getattr(self.app, "complete_file_reference", None)
            if complete_file is not None and complete_file(
                self,
                direction=completion_direction,
            ):
                event.stop()
                event.prevent_default()
                return
            complete = getattr(self.app, "complete_slash_command", None)
            if complete is not None and complete(self, direction=completion_direction):
                event.stop()
                event.prevent_default()
                return
        if event.key == "up":
            recall = getattr(self.app, "recall_sent_message", None)
            if recall is not None and recall(self, direction=-1):
                event.stop()
                event.prevent_default()
                return
        if event.key == "down":
            recall = getattr(self.app, "recall_sent_message", None)
            if recall is not None and recall(self, direction=1):
                event.stop()
                event.prevent_default()
                return
        if is_follow_up_newline_key(event):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        edit = follow_up_edit_control(event)
        if edit is not None:
            event.stop()
            event.prevent_default()
            if edit == "delete_word_left":
                self.action_delete_word_left()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            send_input = getattr(self.app, "send_input", None)
            if send_input is not None:
                self.app.run_worker(
                    send_input(),
                    name="send-input",
                    exclusive=True,
                )
            return
        await super()._on_key(event)


class NavigationTextArea(TextArea):
    async def _on_key(self, event: Key) -> None:
        handle_joplin_shortcut = getattr(self.app, "handle_joplin_shortcut_key", None)
        if handle_joplin_shortcut is not None and handle_joplin_shortcut(
            event,
            focused=self,
        ):
            return
        if self.read_only:
            handle_shortcut = getattr(self.app, "handle_focus_shortcut_key", None)
            if handle_shortcut is not None and handle_shortcut(event):
                return
        await super()._on_key(event)


class TmuxStreamTextArea(NavigationTextArea):
    def _on_focus(self, event: Focus) -> None:
        super()._on_focus(event)
        snap = getattr(self.app, "snap_tmux_stream_to_bottom", None)
        if snap is not None:
            snap(self)


class GitDiffScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-gitdiff-panel"):
            yield Static(f"Git Diff: {self.agent_id}", id="palette-gitdiff-title")
            yield Static(
                "Optional file or branch:file target",
                id="palette-gitdiff-help",
            )
            yield Input(
                placeholder="README.md or main:README.md",
                id="palette-gitdiff-target",
            )
            with Horizontal(id="palette-gitdiff-actions"):
                yield Button("Run Diff", id="palette-gitdiff-run", variant="primary")
                yield Button("Cancel", id="palette-gitdiff-cancel")

    def submit(self) -> None:
        target = self.query_one("#palette-gitdiff-target", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_git_diff_target(self.agent_id, target),  # type: ignore[attr-defined]
            name="palette-gitdiff",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-gitdiff-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-gitdiff-run":
            event.stop()
            self.submit()


class GitPushScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-gitdiff-panel"):
            yield Static(f"Git Push: {self.agent_id}", id="palette-gitdiff-title")
            yield Static(
                "Optional branch/ref. Leave blank to push the current branch with HEAD.",
                id="palette-gitdiff-help",
            )
            yield Input(
                placeholder="current branch",
                id="palette-gitpush-branch",
            )
            with Horizontal(id="palette-gitdiff-actions"):
                yield Button("Push", id="palette-gitpush-run", variant="primary")
                yield Button("Cancel", id="palette-gitpush-cancel")

    def submit(self) -> None:
        branch = self.query_one("#palette-gitpush-branch", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_git_push_target(self.agent_id, branch),  # type: ignore[attr-defined]
            name="palette-gitpush",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-gitpush-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-gitpush-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-gitpush-branch":
            event.stop()
            self.submit()


class CustomSlashCommandArgScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, command: CustomSlashCommand) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.command = command

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-custom-command-panel"):
            yield Static(
                f"{self.command.name}: {self.agent_id}",
                id="palette-custom-command-title",
            )
            yield Static(self.command.description, id="palette-custom-command-help")
            yield Input(
                placeholder=self.command.arg_placeholder,
                id="palette-custom-command-arg",
            )
            with Horizontal(id="palette-custom-command-actions"):
                yield Button("Send", id="palette-custom-command-send", variant="primary")
                yield Button("Cancel", id="palette-custom-command-cancel")

    def submit(self) -> None:
        arg = self.query_one("#palette-custom-command-arg", Input).value
        if self.command.arg_required and not arg.strip():
            self.notify(f"{self.command.arg_label} is required.", severity="warning")
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.palette_custom_slash_command_arg(  # type: ignore[attr-defined]
                self.agent_id,
                self.command,
                arg,
            ),
            name=f"palette-custom-{slugify(self.command.name)}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "palette-custom-command-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "palette-custom-command-send":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-custom-command-arg":
            event.stop()
            self.submit()


class JoplinNoteTitleScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        action: str,
        note_id: str | None = None,
        current_title: str = "",
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.action = action
        self.note_id = note_id
        self.current_title = current_title

    def compose(self) -> ComposeResult:
        label = "New Joplin Note" if self.action == "new" else "Rename Joplin Note"
        with Vertical(id="joplin-title-panel"):
            yield Static(f"{label}: {self.agent_id}", id="joplin-title-modal-title")
            yield Input(
                value=self.current_title,
                placeholder="Note title",
                id="joplin-title-input",
            )
            with Horizontal(id="joplin-title-actions"):
                yield Button(
                    "Create" if self.action == "new" else "Rename",
                    id="joplin-title-submit",
                    variant="primary",
                )
                yield Button("Cancel", id="joplin-title-cancel")

    def on_mount(self) -> None:
        title_input = self.query_one("#joplin-title-input", Input)
        title_input.focus()
        title_input.cursor_position = len(title_input.value)

    def submit(self) -> None:
        title = self.query_one("#joplin-title-input", Input).value.strip()
        if not title:
            self.notify("Joplin note title is required.", severity="warning")
            return
        app = self.app
        self.dismiss()
        app.run_worker(  # type: ignore[attr-defined]
            app.joplin_title_action_for_agent(  # type: ignore[attr-defined]
                self.agent_id,
                self.action,
                title,
                note_id=self.note_id,
            ),
            name=f"joplin-{self.action}-{slugify(self.agent_id)}",
            exclusive=True,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-title-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-title-submit":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "joplin-title-input":
            event.stop()
            self.submit()


class JoplinDeleteConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, note_id: str, title: str) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.note_id = note_id
        self.title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="joplin-delete-panel"):
            yield Static("Delete Joplin Note", id="joplin-delete-title")
            yield Static(self.title, id="joplin-delete-note-title")
            with Horizontal(id="joplin-delete-actions"):
                yield Button("Delete", id="joplin-delete-confirm", variant="error")
                yield Button("Cancel", id="joplin-delete-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "joplin-delete-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "joplin-delete-confirm":
            event.stop()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.delete_joplin_note(self.agent_id, self.note_id),  # type: ignore[attr-defined]
                name=f"joplin-delete-{slugify(self.agent_id)}",
                exclusive=True,
            )
            self.dismiss()


class OperatorKillConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, action: str, delete_thread: bool) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.action = action
        self.delete_thread = delete_thread

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-kill-panel"):
            yield Static("Kill Operator Pane", id="operator-kill-title")
            yield Static(
                f"{self.action.title()} {self.agent_id} and kill its tmux pane?",
                id="operator-kill-message",
            )
            with Horizontal(id="operator-kill-actions"):
                yield Button(self.action.title(), id="operator-kill-confirm", variant="error")
                yield Button("Cancel", id="operator-kill-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "operator-kill-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "operator-kill-confirm":
            event.stop()
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.dismiss_selected_agent(  # type: ignore[attr-defined]
                    agent_id=self.agent_id,
                    delete_thread=self.delete_thread,
                    confirmed_operator_kill=True,
                ),
                name=f"operator-{self.action}-{slugify(self.agent_id)}",
                exclusive=True,
            )
            self.dismiss()


class OperatorHistoryScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("enter", "resume_selected", "Resume"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        *,
        agent_id: str,
        candidates: list[OperatorSessionCandidate],
        resume_target: OperatorSessionCandidate | None = None,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.candidates = candidates
        self.resume_target = resume_target

    def compose(self) -> ComposeResult:
        with Vertical(id="operator-history-panel"):
            yield Static(f"Operator History: {self.agent_id}", id="operator-history-title")
            yield Static(self.summary_text(), id="operator-history-summary")
            yield DataTable(
                id="operator-history-table",
                cursor_type="row",
                show_row_labels=False,
            )
            with Horizontal(id="operator-history-actions"):
                yield Button("Resume", id="operator-history-resume", variant="primary")
                yield Button("Refresh", id="operator-history-refresh")
                yield Button("Close", id="operator-history-close")

    def on_mount(self) -> None:
        table = self.query_one("#operator-history-table", DataTable)
        table.add_columns("Pick", "Updated", "Source", "Session", "Latest Prompt")
        self.render_candidates()

    def summary_text(self) -> str:
        if not self.candidates:
            return "No local Codex sessions found for this operator."
        if self.resume_target is None:
            return f"{len(self.candidates)} session candidate(s)."
        return f"Default resume target: {self.resume_target.session_id}"

    def candidate_key(self, candidate: OperatorSessionCandidate) -> str:
        return candidate.session_id

    def format_timestamp(self, value: float) -> str:
        if not value:
            return "-"
        return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M")

    def render_candidates(self) -> None:
        summary = self.query_one("#operator-history-summary", Static)
        summary.update(self.summary_text())
        table = self.query_one("#operator-history-table", DataTable)
        table.clear()
        for index, candidate in enumerate(self.candidates, start=1):
            marker = "*" if candidate == self.resume_target else str(index)
            table.add_row(
                marker,
                self.format_timestamp(candidate.timestamp),
                candidate.source,
                candidate.session_id,
                candidate.summary,
                key=self.candidate_key(candidate),
            )
        if table.row_count:
            row = 0
            if self.resume_target is not None:
                try:
                    row = table.get_row_index(self.candidate_key(self.resume_target))
                except Exception:
                    row = 0
            table.move_cursor(row=row, animate=False, scroll=False)

    def selected_candidate(self) -> OperatorSessionCandidate | None:
        table = self.query_one("#operator-history-table", DataTable)
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        row_key = str(table.ordered_rows[table.cursor_row].key.value)
        for candidate in self.candidates:
            if self.candidate_key(candidate) == row_key:
                return candidate
        return None

    def resume_candidate(self) -> None:
        candidate = self.selected_candidate()
        if candidate is None:
            self.notify("Select an operator session first.", severity="warning")
            return
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.resume_selected_operator(  # type: ignore[attr-defined]
                agent_id=self.agent_id,
                resume_candidate=candidate,
            ),
            name=f"operator-resume-{slugify(self.agent_id)}",
            exclusive=True,
        )
        self.dismiss()

    def refresh_candidates(self) -> None:
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.refresh_operator_history_screen(self),  # type: ignore[attr-defined]
            name=f"operator-history-refresh-{slugify(self.agent_id)}",
            exclusive=True,
        )

    def update_candidates(
        self,
        candidates: list[OperatorSessionCandidate],
        resume_target: OperatorSessionCandidate | None,
    ) -> None:
        self.candidates = candidates
        self.resume_target = resume_target
        self.render_candidates()

    def action_resume_selected(self) -> None:
        self.resume_candidate()

    def action_refresh(self) -> None:
        self.refresh_candidates()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "operator-history-table":
            return
        event.stop()
        self.resume_candidate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "operator-history-close":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "operator-history-refresh":
            event.stop()
            self.refresh_candidates()
            return
        if event.button.id == "operator-history-resume":
            event.stop()
            self.resume_candidate()


class PullRequestMergeConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        number: int,
        title: str,
        method: str = "squash",
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.number = number
        self.title = title
        self.method = method

    def compose(self) -> ComposeResult:
        expected = f"merge PR #{self.number}"
        with Vertical(id="pr-merge-panel"):
            yield Static("Merge Pull Request", id="pr-merge-title")
            yield Static(f"#{self.number} {self.title}", id="pr-merge-summary")
            yield Static(
                f'Type "{expected}" to confirm {self.method} merge.',
                id="pr-merge-help",
            )
            yield Input(placeholder=expected, id="pr-merge-confirm")
            with Horizontal(id="pr-merge-actions"):
                yield Button("Merge", id="pr-merge-run", variant="error")
                yield Button("Cancel", id="pr-merge-cancel")

    def submit(self) -> None:
        confirm = self.query_one("#pr-merge-confirm", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.merge_pull_request(  # type: ignore[attr-defined]
                self.agent_id,
                self.number,
                method=self.method,
                confirm=confirm,
            ),
            name=f"pr-merge-{slugify(self.agent_id)}-{self.number}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pr-merge-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "pr-merge-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pr-merge-confirm":
            event.stop()
            self.submit()


class IssueClearConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        number: int,
        title: str,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.number = number
        self.title = title

    def compose(self) -> ComposeResult:
        expected = f"clear issue #{self.number}"
        with Vertical(id="issue-clear-panel"):
            yield Static("Clear GitHub Issue", id="issue-clear-title")
            yield Static(f"#{self.number} {self.title}", id="issue-clear-summary")
            yield TextArea(
                "",
                id="issue-clear-comment",
                language="markdown",
                soft_wrap=True,
            )
            yield Static(
                f'Type "{expected}" to comment and close.',
                id="issue-clear-help",
            )
            yield Input(placeholder=expected, id="issue-clear-confirm")
            with Horizontal(id="issue-clear-actions"):
                yield Button("Clear", id="issue-clear-run", variant="error")
                yield Button("Cancel", id="issue-clear-cancel")

    def submit(self) -> None:
        comment = self.query_one("#issue-clear-comment", TextArea).text
        confirm = self.query_one("#issue-clear-confirm", Input).value
        self.app.run_worker(  # type: ignore[attr-defined]
            self.app.clear_issue(  # type: ignore[attr-defined]
                self.agent_id,
                self.number,
                comment=comment,
                confirm=confirm,
            ),
            name=f"issue-clear-{slugify(self.agent_id)}-{self.number}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "issue-clear-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "issue-clear-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "issue-clear-confirm":
            event.stop()
            self.submit()


def normalized_key_names(event: Key) -> set[str]:
    values = {event.key, getattr(event, "name", "")}
    values.update(getattr(event, "aliases", []) or [])
    return {value.strip().lower().replace("_", "+") for value in values if value}


def is_follow_up_newline_key(event: Key) -> bool:
    names = normalized_key_names(event)
    return bool(names & FOLLOW_UP_NEWLINE_KEYS) or any(
        ("shift" in name.split("+") or "alt" in name.split("+"))
        and ("enter" in name.split("+") or "return" in name.split("+"))
        for name in names
    )


def follow_up_edit_control(event: Key) -> str | None:
    for name in normalized_key_names(event):
        if edit := FOLLOW_UP_EDIT_KEYS.get(name):
            return edit
    return None


def slash_completion_direction(event: Key) -> int | None:
    names = normalized_key_names(event)
    if names & SLASH_COMPLETION_FORWARD_KEYS:
        return 1
    if names & SLASH_COMPLETION_BACKWARD_KEYS:
        return -1
    return None


def git_diff_passthrough_command(target: str = "") -> str:
    clean_target = target.strip()
    if not clean_target:
        return "!git diff"
    quoted = shlex.quote(clean_target)
    if ":" in clean_target and not clean_target.startswith((".", "/")):
        return f"!git diff {quoted}"
    return f"!git diff -- {quoted}"


def git_push_passthrough_command(branch: str = "") -> str:
    clean_branch = branch.strip()
    if not clean_branch:
        return "!git push origin HEAD"
    return f"!git push origin {shlex.quote(clean_branch)}"


def parse_git_push_slash_command(message: str) -> str | None:
    stripped = message.strip()
    if "\n" in stripped:
        return None
    if stripped.lower() == "/gitpush":
        return ""
    command, separator, branch = stripped.partition(" ")
    if not separator or command.lower() != "/gitpush":
        return None
    return branch.strip()


class SettingsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        visual_flash_enabled: bool,
        terminal_bell_enabled: bool,
        agent_blink_enabled: bool,
        low_power_enabled: bool,
        layout_mode: str,
        split_percent: int,
        tmux_direct_enabled: bool,
        tmux_features_available: bool,
        custom_theme_name: str,
        theme_name: str,
    ) -> None:
        super().__init__()
        self.visual_flash_enabled = visual_flash_enabled
        self.terminal_bell_enabled = terminal_bell_enabled
        self.agent_blink_enabled = agent_blink_enabled
        self.low_power_enabled = low_power_enabled
        self.layout_mode = layout_mode
        self.split_percent = split_percent
        self.tmux_direct_enabled = tmux_direct_enabled
        self.tmux_features_available = tmux_features_available
        self.custom_theme_name = custom_theme_name
        self.theme_name = theme_name

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-panel"):
            yield Static("Settings", id="settings-title")
            with Vertical(id="settings-options"):
                yield Checkbox(
                    "Visual flash",
                    value=self.visual_flash_enabled,
                    id="visual-flash",
                )
                yield Checkbox(
                    "Terminal bell",
                    value=self.terminal_bell_enabled,
                    id="terminal-bell",
                )
                yield Checkbox(
                    "Unseen blink",
                    value=self.agent_blink_enabled,
                    id="agent-blink",
                )
                yield Checkbox(
                    "Low-power watch mode",
                    value=self.low_power_enabled,
                    id="low-power",
                )
                yield Static("Layout", id="layout-mode-label")
                yield Select(
                    LAYOUT_CHOICES,
                    value=self.layout_mode,
                    allow_blank=False,
                    id="layout-mode",
                )
                yield Static(
                    f"Agents width: {self.split_percent}%",
                    id="split-percent-label",
                )
                with Horizontal(id="split-percent-actions"):
                    yield Button("Narrow", id="split-narrow")
                    yield Button("Reset", id="split-reset")
                    yield Button("Widen", id="split-widen")
                tmux_direct = Checkbox(
                    "Tmux direct default",
                    value=self.tmux_direct_enabled,
                    id="tmux-direct",
                )
                tmux_direct.disabled = not self.tmux_features_available
                yield tmux_direct
                yield Static("Theme", id="theme-label")
                yield Select(
                    [
                        ("Cyberpunk", DEFAULT_TUI_THEME),
                        ("Minimal", MINIMAL_TUI_THEME),
                        (self.custom_theme_name, self.custom_theme_name),
                    ],
                    value=self.theme_name,
                    allow_blank=False,
                    id="theme-mode",
                )
            with Horizontal(id="settings-actions"):
                yield Button("Close", id="settings-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-close":
            event.stop()
            self.dismiss()
            return
        if event.button.id in {"split-narrow", "split-reset", "split-widen"}:
            event.stop()
            app = self.app
            if event.button.id == "split-narrow":
                app.adjust_split_percent(-SPLIT_PERCENT_STEP)  # type: ignore[attr-defined]
            elif event.button.id == "split-widen":
                app.adjust_split_percent(SPLIT_PERCENT_STEP)  # type: ignore[attr-defined]
            else:
                app.reset_split_percent()  # type: ignore[attr-defined]
            self.split_percent = app.split_percent  # type: ignore[attr-defined]
            self.query_one("#split-percent-label", Static).update(
                f"Agents width: {self.split_percent}%"
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "layout-mode":
            event.stop()
            self.app.set_layout_mode(str(event.value))  # type: ignore[attr-defined]
        elif event.select.id == "theme-mode":
            event.stop()
            self.app.set_ui_theme(str(event.value))  # type: ignore[attr-defined]


class AgentPBXTUI(App[None]):
    TITLE = "Agent PBX"
    CSS = """
    Screen {
        layout: vertical;
    }

    #attention {
        height: 1;
        content-align: center middle;
    }

    #attention.attention-active {
        background: $warning;
        color: $background;
        text-style: bold;
    }

    #attention.unseen-active {
        background: $warning;
        color: $background;
        text-style: bold;
    }

    Screen.attention-flash #left,
    Screen.attention-flash #right {
        border: solid $warning;
    }

    Screen.compact-home #left,
    Screen.tiny-home #left {
        width: 100%;
    }

    Screen.compact-home #right,
    Screen.tiny-home #right {
        display: none;
    }

    Screen.compact-agent #left,
    Screen.tiny-agent #left {
        display: none;
    }

    Screen.compact-agent #right,
    Screen.tiny-agent #right {
        width: 100%;
    }

    Screen.tiny-home #events-title,
    Screen.tiny-home #events,
    Screen.tiny-home #operators-title,
    Screen.tiny-home #operators,
    Screen.tiny-home #operator-actions,
    Screen.tiny-home #agent-actions {
        display: none;
    }

    Screen.tiny-home.tiny-operators #agents-title,
    Screen.tiny-home.tiny-operators #agents,
    Screen.tiny-home.tiny-operators #events-title,
    Screen.tiny-home.tiny-operators #events {
        display: none;
    }

    Screen.tiny-home.tiny-operators #operators-title,
    Screen.tiny-home.tiny-operators #operators {
        display: block;
    }

    Screen.tiny-home.tiny-events #agents-title,
    Screen.tiny-home.tiny-events #agents,
    Screen.tiny-home.tiny-events #operators-title,
    Screen.tiny-home.tiny-events #operators {
        display: none;
    }

    Screen.tiny-home.tiny-events #events-title,
    Screen.tiny-home.tiny-events #events {
        display: block;
    }

    Screen.custom-theme TextArea {
        background: $background;
        color: $foreground;
    }

    Screen.custom-theme TextArea .text-area--cursor-line {
        background: $surface;
    }

    Screen.custom-theme TextArea .text-area--selection {
        background: $foreground;
        color: $background;
        text-style: bold;
    }

    Screen.custom-theme TextArea .text-area--cursor {
        background: $foreground;
        color: $background;
    }

    Screen.custom-theme TextArea.-read-only .text-area--cursor {
        background: $warning;
        color: $background;
    }

    GitDiffScreen {
        align: center middle;
    }

    #palette-gitdiff-panel {
        width: 72;
        max-width: 92%;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #palette-gitdiff-title {
        height: 1;
        content-align: center middle;
        text-style: bold;
    }

    #palette-gitdiff-help {
        height: 1;
        color: $secondary;
        content-align: center middle;
    }

    #palette-gitdiff-target {
        height: 3;
    }

    #palette-gitdiff-actions {
        height: 3;
    }

    #palette-gitdiff-actions Button {
        width: 1fr;
    }

    CustomSlashCommandArgScreen {
        align: center middle;
    }

    #palette-custom-command-panel {
        width: 72;
        max-width: 92%;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #palette-custom-command-title {
        height: 1;
        content-align: center middle;
        text-style: bold;
    }

    #palette-custom-command-help {
        height: 1;
        color: $secondary;
        content-align: center middle;
    }

    #palette-custom-command-arg {
        height: 3;
    }

    #palette-custom-command-actions {
        height: 3;
    }

    #palette-custom-command-actions Button {
        width: 1fr;
    }

    JoplinNoteTitleScreen,
    JoplinDeleteConfirmScreen,
    OperatorKillConfirmScreen,
    OperatorHistoryScreen,
    PullRequestMergeConfirmScreen,
    IssueClearConfirmScreen {
        align: center middle;
    }

    #joplin-title-panel,
    #joplin-delete-panel,
    #operator-kill-panel,
    #operator-history-panel,
    #pr-merge-panel,
    #issue-clear-panel {
        width: 64;
        max-width: 90%;
        height: auto;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }

    #operator-history-panel {
        width: 128;
        max-width: 96%;
        height: 26;
        max-height: 90%;
    }

    #joplin-title-modal-title,
    #joplin-delete-title,
    #operator-kill-title,
    #operator-history-title,
    #pr-merge-title,
    #issue-clear-title {
        height: 1;
        text-style: bold;
        color: $primary;
        content-align: center middle;
    }

    #operator-history-summary {
        height: 1;
        color: $secondary;
        content-align: center middle;
    }

    #operator-history-table {
        height: 1fr;
        margin-top: 1;
    }

    #joplin-title-input,
    #joplin-delete-note-title,
    #operator-kill-message,
    #pr-merge-confirm,
    #issue-clear-confirm {
        height: 3;
        margin-top: 1;
    }

    #pr-merge-summary,
    #pr-merge-help,
    #issue-clear-summary,
    #issue-clear-help {
        height: auto;
        margin-top: 1;
        color: $warning;
    }

    #issue-clear-comment {
        height: 8;
        min-height: 4;
        margin-top: 1;
        border: tall $accent;
    }

    #joplin-delete-note-title {
        color: $warning;
        content-align: center middle;
    }

    #operator-kill-message {
        color: $warning;
        content-align: center middle;
    }

    #joplin-title-actions,
    #joplin-delete-actions,
    #operator-kill-actions,
    #operator-history-actions,
    #pr-merge-actions,
    #issue-clear-actions {
        height: 3;
        margin-top: 1;
    }

    #joplin-title-actions Button,
    #joplin-delete-actions Button,
    #operator-kill-actions Button,
    #operator-history-actions Button,
    #pr-merge-actions Button,
    #issue-clear-actions Button {
        width: 1fr;
    }

    SettingsScreen {
        align: center middle;
    }

    #settings-panel {
        width: 72;
        max-width: 90%;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #settings-title {
        height: 1;
        content-align: center middle;
        text-style: bold;
    }

    #settings-options {
        height: auto;
    }

    #settings-options Checkbox {
        height: 3;
    }

    #layout-mode {
        height: 3;
    }

    #theme-mode {
        height: 3;
    }

    #layout-mode-label,
    #theme-label {
        height: 1;
        color: $secondary;
        content-align: center middle;
    }

    #split-percent-label {
        height: 1;
        color: $secondary;
        content-align: center middle;
    }

    #split-percent-actions {
        height: 3;
    }

    #split-percent-actions Button {
        width: 1fr;
    }

    #settings-actions {
        height: 3;
    }

    #settings-actions Button {
        width: 1fr;
    }

    #main {
        height: 1fr;
    }

    #left {
        width: 42%;
        border: solid $accent;
    }

    #right {
        width: 58%;
        border: solid $accent;
    }

    #agents {
        height: 1fr;
    }

    #operators {
        height: 1fr;
    }

    #agent-actions {
        height: 3;
    }

    #operator-actions {
        height: 3;
    }

    #agent-actions Button {
        width: 1fr;
    }

    #operator-actions Button {
        width: 12;
        min-width: 7;
    }

    #agent-tabs {
        height: 1fr;
    }

    #agent-title {
        display: none;
        height: 1;
        content-align: left middle;
        text-style: bold;
    }

    Screen.compact-agent #agent-title,
    Screen.tiny-agent #agent-title {
        display: block;
    }

    Screen.tiny-agent.tmux-direct #agent-title {
        display: none;
    }

    #detail {
        height: 1fr;
        min-height: 8;
    }

    #tmux-panel {
        display: none;
        height: 1fr;
    }

    Screen.tmux-direct #detail,
    Screen.tmux-direct #latest-plan-choice-panel,
    Screen.tmux-direct #composer {
        display: none;
    }

    Screen.tmux-direct #tmux-panel {
        display: block;
    }

    #tmux-status {
        height: 1;
        content-align: left middle;
        color: $secondary;
    }

    #tmux-stream {
        height: 1fr;
        min-height: 8;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #tmux-actions {
        height: 3;
    }

    #tmux-actions Button {
        width: 1fr;
    }

    #tmux-message {
        height: 8;
        min-height: 8;
        max-height: 15;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #tmux-hotkeys {
        height: 1;
        content-align: center middle;
        color: $secondary;
        background: $surface;
    }

    #thread {
        height: 7;
        min-height: 5;
    }

    #thread-detail {
        height: 1fr;
        min-height: 12;
    }

    #latest-plan-choice-panel,
    #plan-choice-panel {
        display: none;
        height: 8;
        min-height: 0;
        border: tall $secondary;
        padding: 0 1;
    }

    #latest-plan-choice-title,
    #plan-choice-title {
        height: 1;
        color: $secondary;
        content-align: left middle;
    }

    #latest-plan-options,
    #plan-options {
        height: 4;
    }

    #latest-plan-hint,
    #plan-hint {
        height: 1;
        color: $secondary;
    }

    #thread-actions {
        height: 3;
    }

    #thread-actions Button {
        width: 1fr;
    }

    #file-path {
        height: 1;
        color: $secondary;
        content-align: left middle;
    }

    #files {
        height: 10;
        min-height: 5;
    }

    #file-preview {
        height: 1fr;
        min-height: 12;
    }

    #file-actions {
        height: 3;
    }

    #file-actions Button {
        width: 1fr;
        min-width: 1;
    }

    #workerbee-detail {
        height: 1fr;
        min-height: 12;
    }

    #workerbee-actions {
        height: 3;
    }

    #workerbee-actions Button {
        width: 1fr;
    }

    #pull-request-status,
    #issue-status {
        height: 1;
        color: $secondary;
        content-align: left middle;
    }

    #pull-requests,
    #issues,
    #campaigns {
        height: 8;
        min-height: 4;
    }

    #pull-request-detail,
    #issue-detail,
    #campaign-detail {
        height: 1fr;
        min-height: 12;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #pull-request-actions,
    #issue-actions,
    #campaign-actions {
        height: 3;
    }

    #pull-request-actions Button,
    #issue-actions Button,
    #campaign-actions Button {
        width: 1fr;
        min-width: 1;
    }

    #joplin-status {
        height: 1;
        color: $secondary;
        content-align: left middle;
    }

    #joplin-notes {
        height: 8;
        min-height: 4;
    }

    #joplin-body {
        height: 1fr;
        min-height: 12;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #joplin-actions {
        height: 7;
    }

    #joplin-crud-actions,
    #joplin-log-actions {
        height: 3;
    }

    #joplin-crud-actions Button,
    #joplin-log-actions Button {
        width: 1fr;
        min-width: 1;
    }

    #joplin-hotkeys {
        height: 1;
        color: $secondary;
        text-style: dim;
        content-align: left middle;
    }

    #events {
        height: 12;
    }

    Screen.tiny-home #events {
        height: 1fr;
    }

    #composer {
        height: auto;
        min-height: 15;
        max-height: 22;
        border: tall $accent;
        padding: 0 1;
    }

    #composer-inputs {
        height: 8;
        min-height: 8;
        max-height: 15;
    }

    #agent-id {
        width: 20%;
        height: 8;
        border: tall $accent;
        background: $surface;
    }

    #message {
        width: 80%;
        height: 8;
        min-height: 8;
        max-height: 15;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #composer-actions {
        height: 4;
        padding-top: 1;
    }

    #composer-button-spacer {
        width: 20%;
        height: 3;
    }

    #composer-button-inset {
        width: 1;
        height: 3;
    }

    #composer-buttons {
        width: 1fr;
        height: 3;
        align: center middle;
    }

    #composer-buttons Button {
        width: 1fr;
        min-width: 1;
    }

    #composer-hotkeys {
        height: 1;
        content-align: center middle;
        color: $secondary;
        background: $surface;
    }

    Screen.tiny-agent #detail,
    Screen.tiny-agent #tmux-stream,
    Screen.tiny-agent #file-preview,
    Screen.tiny-agent #workerbee-detail,
    Screen.tiny-agent #pull-request-detail,
    Screen.tiny-agent #issue-detail,
    Screen.tiny-agent #joplin-body {
        min-height: 4;
    }

    Screen.tiny-agent #thread,
    Screen.tiny-agent #files,
    Screen.tiny-agent #pull-requests,
    Screen.tiny-agent #issues,
    Screen.tiny-agent #joplin-notes {
        height: 5;
        min-height: 4;
    }

    Screen.tiny-agent #thread-detail {
        min-height: 5;
    }

    Screen.tiny-agent #composer {
        min-height: 6;
        max-height: 6;
        padding: 0;
    }

    Screen.tiny-agent #composer-inputs {
        height: 1;
        min-height: 1;
        max-height: 1;
    }

    Screen.tiny-agent #agent-id,
    Screen.tiny-agent #composer-button-spacer,
    Screen.tiny-agent #composer-button-inset {
        display: none;
    }

    Screen.tiny-agent #message {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 1;
    }

    Screen.tiny-agent #composer-actions {
        height: 3;
        padding-top: 0;
    }

    Screen.tiny-agent.tmux-direct #tmux-actions {
        display: none;
    }

    Screen.tiny-agent.tmux-direct #tmux-stream {
        min-height: 5;
        border: none;
    }

    Screen.tiny-agent.tmux-direct #tmux-message {
        height: 5;
        min-height: 4;
        max-height: 5;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("s", "settings", "Settings"),
        Binding("f1", "focus_agents", "Agents", key_display="F1", priority=True),
        Binding("f2", "focus_events", "Events", key_display="F2", priority=True),
        Binding("f3", "focus_right_pane", "View", key_display="F3", priority=True),
        Binding("f4", "focus_latest_input", "Input", key_display="F4", priority=True),
        Binding("f5", "focus_operators", "Operators", key_display="F5", priority=True),
        Binding("f6", "prev_operator_fork", "Prev Fork", key_display="F6", priority=True),
        Binding("f7", "next_operator_fork", "Next Fork", key_display="F7", priority=True),
        Binding("shift+o", "start_operator", "Start Operator", key_display="O"),
        Binding("y", "operator_history", "Operator History", priority=True),
        Binding("u", "resume_operator", "Resume Operator", priority=True),
        Binding("shift+u", "restart_operator", "Restart Operator", key_display="U"),
        Binding("x", "stop_operator", "Stop Operator", priority=True),
        Binding(
            "shift+w",
            "start_review_operator_fork",
            "Review Fork",
            key_display="W",
            priority=True,
        ),
        Binding(
            "upper_w",
            "start_review_operator_fork",
            "Review Fork",
            show=False,
            priority=True,
        ),
        Binding(
            "W",
            "start_review_operator_fork",
            "Review Fork",
            show=False,
            priority=True,
        ),
        ("ctrl+t", "toggle_tmux_direct", "Tmux"),
        Binding("f8", "toggle_tmux_direct", "Tmux", key_display="F8"),
        Binding("alt+t", "toggle_tmux_direct", "Tmux", key_display="Alt+T", show=False),
        Binding("p", "toggle_star_agent", "Star Agent", priority=True),
        Binding("h", "toggle_hidden_agents", "Hidden", priority=True),
        Binding("shift+h", "unhide_agent", "Unhide Agent", key_display="H", priority=True),
        Binding("d", "hide_agent", "Hide Agent", priority=True),
        Binding(
            "shift+m",
            "monitor_campaign",
            "Campaign Monitor",
            key_display="M",
            priority=True,
        ),
        Binding(
            "shift+r",
            "view_campaign_report",
            "Campaign Report",
            key_display="R",
            priority=True,
        ),
        Binding(
            "shift+c",
            "copy_campaign_to_joplin",
            "Campaign Note",
            key_display="C",
            priority=True,
        ),
        Binding(
            "shift+d",
            "purge_agent",
            "Purge Agent",
            key_display="D",
            priority=True,
        ),
        ("b", "back", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        server: str,
        token: str | None = None,
        visual_flash: bool | None = None,
        terminal_bell: bool | None = None,
        theme_name: str | None = None,
        agent_blink: bool | None = None,
        tmux_direct: bool | None = None,
        tmux_capture_lines: int | None = None,
        export_dir: Path | str | None = None,
        settings_file: Path | str | None = None,
    ) -> None:
        super().__init__()
        self.custom_theme_name = env_custom_theme_name()
        self.custom_palette = env_custom_palette()
        self.custom_theme = build_theme(self.custom_theme_name, self.custom_palette)
        self.register_theme(THEME_CYBERPUNK)
        self.register_theme(THEME_MINIMAL)
        self.register_theme(self.custom_theme)
        self.server = server.rstrip("/")
        self.token = token
        self.settings_file = (
            Path(settings_file).expanduser()
            if settings_file is not None
            else env_settings_file()
        )
        self.settings = load_tui_settings(self.settings_file)
        self.slash_commands_file = env_slash_commands_file()
        (
            self.custom_slash_commands,
            self.custom_slash_command_errors,
        ) = load_custom_slash_commands(
            self.slash_commands_file,
            built_in_names=built_in_palette_command_names(self.custom_theme_name),
        )
        visual_flash_setting = env_flag_value(
            "AGENT_PBX_TUI_VISUAL_FLASH", "AGENT_PBX_TUI_FLASH"
        )
        self.visual_flash_enabled = (
            bool_setting(self.settings, "visual_flash", False)
            if visual_flash is None
            else visual_flash
        )
        if visual_flash is None and visual_flash_setting is not None:
            self.visual_flash_enabled = visual_flash_setting
        terminal_bell_setting = env_flag_value(
            "AGENT_PBX_TUI_TERMINAL_BELL", "AGENT_PBX_TUI_BELL"
        )
        self.terminal_bell_enabled = (
            bool_setting(self.settings, "terminal_bell", False)
            if terminal_bell is None
            else terminal_bell
        )
        if terminal_bell is None and terminal_bell_setting is not None:
            self.terminal_bell_enabled = terminal_bell_setting
        agent_blink_setting = env_flag_value("AGENT_PBX_TUI_AGENT_BLINK")
        self.agent_blink_enabled = (
            bool_setting(self.settings, "agent_blink", True)
            if agent_blink is None
            else agent_blink
        )
        if agent_blink is None and agent_blink_setting is not None:
            self.agent_blink_enabled = agent_blink_setting
        low_power_setting = env_flag_value(
            "AGENT_PBX_TUI_LOW_POWER", "AGENT_PBX_TUI_WATCH_MODE"
        )
        self.low_power_enabled = bool_setting(self.settings, "low_power", False)
        if low_power_setting is not None:
            self.low_power_enabled = low_power_setting
        tmux_direct_setting = env_flag_value("AGENT_PBX_TUI_TMUX")
        self.tmux_direct_enabled = (
            bool_setting(self.settings, "tmux_direct", False)
            if tmux_direct is None
            else tmux_direct
        )
        if tmux_direct is None and tmux_direct_setting is not None:
            self.tmux_direct_enabled = tmux_direct_setting
        self.tmux_direct_agent_modes = bool_map_setting(
            self.settings,
            "tmux_direct_agent_modes",
        )
        self.tmux_features_available = tmux_features_available(
            tmux_direct_enabled=self.tmux_direct_enabled
        )
        self.tmux_local_direct_context = (
            self.tmux_features_available and is_local_server_url(self.server)
        )
        if self.tmux_direct_enabled and not self.tmux_features_available:
            self.tmux_direct_enabled = False
        if not self.tmux_features_available:
            self.tmux_direct_agent_modes = {}
        capture_lines_setting = int_setting(
            self.settings,
            "tmux_capture_lines",
            DEFAULT_TMUX_CAPTURE_LINES,
        )
        if capture_lines_setting < 0:
            capture_lines_setting = DEFAULT_TMUX_CAPTURE_LINES
        env_capture_lines = env_tmux_capture_lines_value()
        if tmux_capture_lines is not None and tmux_capture_lines >= 0:
            self.tmux_capture_lines = tmux_capture_lines
        elif env_capture_lines is not None:
            self.tmux_capture_lines = env_capture_lines
        else:
            self.tmux_capture_lines = capture_lines_setting
        self.tmux_refresh_seconds = (
            env_tmux_refresh_seconds_value() or DEFAULT_TMUX_REFRESH_SECONDS
        )
        self.agent_refresh_seconds = self.resolve_agent_refresh_seconds()
        self.attention_blink_seconds = self.resolve_attention_blink_seconds()
        export_dir_setting = str_setting(
            self.settings,
            "export_dir",
            str(DEFAULT_EXPORT_DIR),
        )
        self.export_dir = (
            Path(export_dir).expanduser()
            if export_dir is not None
            else env_export_dir_value() or Path(export_dir_setting).expanduser()
        )
        theme_setting = str_setting(self.settings, "theme", DEFAULT_TUI_THEME)
        self.ui_theme = self.resolve_theme(
            theme_name or env_theme_value(custom_name=self.custom_theme_name) or theme_setting
        )
        self.theme = self.ui_theme
        layout_setting = str_setting(self.settings, "layout", DEFAULT_TUI_LAYOUT)
        self.layout_mode = resolve_layout(env_layout_value() or layout_setting)
        self.effective_layout_mode = self.compute_effective_layout()
        self.compact_view = "home"
        self.tiny_home_panel = TINY_HOME_AGENTS
        self.tiny_show_events = False
        self.show_hidden_agents = bool_setting(
            self.settings,
            "show_hidden_agents",
            False,
        )
        split_percent_setting = int_setting(
            self.settings,
            "split_percent",
            DEFAULT_SPLIT_PERCENT,
        )
        split_percent_env = env_split_percent_value()
        self.split_percent = (
            split_percent_env
            if split_percent_env is not None
            else clamp_split_percent(split_percent_setting)
        )
        self.rendered_agent_columns: tuple[str, ...] = ()
        self.rendered_operator_columns: tuple[str, ...] = ()
        self.rendered_agents_signature: tuple[Any, ...] | None = None
        self.agents: dict[str, dict[str, Any]] = {}
        self.selected_agent_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.latest_report_by_agent: dict[str, dict[str, Any]] = {}
        self.thread_items: dict[str, dict[str, Any]] = {}
        self.thread_order: list[str] = []
        self.selected_thread_item_id: str | None = None
        self.selected_thread_item_id_by_agent: dict[str, str] = {}
        self.selected_plan_option_index: int | None = None
        self.selected_latest_plan_option_index: int | None = None
        self.joplin_shortcut_pending = False
        self.marked_thread_item_ids: set[str] = set()
        self.active_agent_tab = "latest-tab"
        self.unseen_latest_agent_ids: set[str] = set()
        self.local_starred_agent_ids = str_set_setting(
            self.settings,
            "starred_agent_ids",
        )
        self.starred_agent_ids = set(self.local_starred_agent_ids)
        self.remote_star_state_seen = False
        self.agent_last_seen_at: dict[str, float] = {}
        self.latest_viewed_at_by_agent = float_map_setting(
            self.settings,
            "latest_viewed_at_by_agent",
        )
        self.workerbee_status_by_agent: dict[str, dict[str, Any]] = {}
        self.pull_request_status_by_agent: dict[str, dict[str, Any]] = {}
        self.pull_requests_by_agent: dict[str, dict[int, dict[str, Any]]] = {}
        self.selected_pull_request_number: int | None = None
        self.selected_pull_request_number_by_agent: dict[str, int] = {}
        self.issue_status_by_agent: dict[str, dict[str, Any]] = {}
        self.issues_by_agent: dict[str, dict[int, dict[str, Any]]] = {}
        self.selected_issue_number: int | None = None
        self.selected_issue_number_by_agent: dict[str, int] = {}
        self.campaigns_by_operator: dict[str, dict[str, dict[str, Any]]] = {}
        self.selected_campaign_id: str | None = None
        self.selected_campaign_id_by_operator: dict[str, str] = {}
        self.selected_campaign_report_id_by_operator: dict[str, str] = {}
        self.joplin_configured = False
        self.joplin_available = False
        self.joplin_status: dict[str, Any] = {}
        self.joplin_notes_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
        self.selected_joplin_note_id: str | None = None
        self.selected_joplin_note_id_by_agent: dict[str, str] = {}
        self.file_path_by_agent: dict[str, str] = {}
        self.file_entries_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
        self.file_directory_entries_by_agent: dict[
            str,
            dict[str, dict[str, dict[str, Any]]],
        ] = {}
        self.active_agent_tab_by_agent: dict[str, str] = {}
        self.message_draft_by_agent: dict[str, str] = {}
        self.tmux_message_draft_by_agent: dict[str, str] = {}
        self.tmux_agent_targets = str_map_setting(self.settings, "tmux_agent_targets")
        self.selected_operator_fork_target_by_operator = str_map_setting(
            self.settings,
            "selected_operator_fork_target_by_operator",
        )
        self.tmux_manual_override_agent_ids = str_set_setting(
            self.settings,
            "tmux_manual_override_agent_ids",
        )
        self.tmux_detached_agent_ids = str_set_setting(
            self.settings,
            "tmux_detached_agent_ids",
        )
        if not self.tmux_features_available:
            self.tmux_agent_targets = {}
            self.selected_operator_fork_target_by_operator = {}
            self.tmux_manual_override_agent_ids = set()
            self.tmux_detached_agent_ids = set()
        self.tmux_last_capture_by_pane: dict[str, str] = {}
        self.tmux_last_status_by_agent: dict[str, str] = {}
        self.tmux_visible_capture_key: str | None = None
        self.tmux_liveness_by_agent: dict[str, TmuxLiveness] = {}
        self.tmux_panes: list[tmux_support.TmuxPane] = []
        self.tmux_refreshing = False
        self.tmux_plan_selector_pane_by_agent: dict[str, str] = {}
        self.tmux_plan_selector_indices_by_agent: dict[str, set[int]] = {}
        self.mouse_debug_enabled = env_flag("AGENT_PBX_TUI_MOUSE_DEBUG")
        self.attention_blink_phase = False
        self.attention_agent_id: str | None = None
        self.pending_slash_command_by_agent: dict[str, str] = {}
        self.plan_mode_active_agent_ids: set[str] = set()
        self.tmux_plan_selector_agent_ids: set[str] = set()
        self.sent_message_history_by_agent: dict[str, list[str]] = {}
        self.sent_message_history_cursor: dict[tuple[str, str], int] = {}
        self.slash_completion_state: dict[str, SlashCompletionState] = {}
        self.file_completion_state: dict[str, FileCompletionState] = {}
        self.event_stream_disconnected = False
        self.event_stream_error_count = 0
        self.event_stream_last_error = ""
        self.last_seen_event_id = int_setting(self.settings, "last_seen_event_id", 0)
        self.flash_generation = 0
        self.agent_jump_prefix_pending = False
        self.agent_jump_prefix_generation = 0
        self.http_client: httpx.AsyncClient | None = None
        self.agent_refresh_timer: Timer | None = None
        self.tmux_refresh_timer: Timer | None = None
        self.attention_blink_timer: Timer | None = None

    def resolve_agent_refresh_seconds(self) -> float:
        default = (
            LOW_POWER_AGENT_REFRESH_SECONDS
            if self.low_power_enabled
            else DEFAULT_AGENT_REFRESH_SECONDS
        )
        return env_agent_refresh_seconds_value() or default

    def resolve_attention_blink_seconds(self) -> float:
        default = (
            LOW_POWER_ATTENTION_BLINK_SECONDS
            if self.low_power_enabled
            else DEFAULT_ATTENTION_BLINK_SECONDS
        )
        return env_attention_blink_seconds_value() or default

    def api_client(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(base_url=self.server, timeout=10)
        return self.http_client

    def composer_hotkeys_text(self) -> str:
        nav = (
            "a Agents | e Events | v View | i Input"
            if self.is_tiny_layout() or self.low_power_enabled
            else "F1 Agents | F2 Events | F3 View | F4 Input"
        )
        text = f"{nav} | Enter send | Ctrl+J newline | Ctrl+W word"
        if self.tmux_features_available:
            text += " | Ctrl+T/F8 tmux"
        return text

    def tmux_hotkeys_text(self) -> str:
        if self.is_tiny_layout() or self.low_power_enabled:
            return "a Agt | e Evt | v View | i In | Enter send | C-J nl | C-W word | C-T/F8 PBX"
        return (
            "F1 Agents | F2 Events | F3 View | F4 Input | Enter send | "
            "Ctrl+J newline | Ctrl+W word | Ctrl+T/F8 PBX"
        )

    def joplin_hotkeys_text(self) -> str:
        return JOPLIN_SHORTCUT_HINT

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="attention")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("Agents (g 1-0)", id="agents-title")
                yield DataTable(
                    id="agents",
                    cursor_type="row",
                    show_row_labels=False,
                )
                with Horizontal(id="agent-actions"):
                    yield Button("Start Operator", id="start-operator")
                    yield Button("Star/Unstar (p)", id="star-agent")
                    yield Button("Show Hidden (h)", id="toggle-hidden-agents")
                    yield Button("Unhide (H)", id="unhide-agent")
                    yield Button("Hide Agent (d)", id="hide-agent")
                    yield Button("Purge Agent (D)", id="purge-agent")
                yield Static("Operators (F5/o)", id="operators-title")
                yield DataTable(
                    id="operators",
                    cursor_type="row",
                    show_row_labels=False,
                )
                with Horizontal(id="operator-actions"):
                    yield Button("Start O", id="operator-start")
                    yield Button("Hist y", id="operator-history")
                    yield Button("Resume u", id="operator-resume")
                    yield Button("Restart U", id="operator-restart")
                    yield Button("Stop x", id="operator-stop")
                    yield Button("Review W", id="operator-fork-review")
                    yield Button("Prev F6", id="operator-fork-prev")
                    yield Button("Next F7", id="operator-fork-next")
                    yield Button("Hide d", id="operator-hide")
                    yield Button("Purge D", id="operator-purge")
                yield Static("Events", id="events-title")
                yield DataTable(
                    id="events",
                    cursor_type="row",
                    show_row_labels=False,
                )
            with Vertical(id="right"):
                yield Static("Agent: -", id="agent-title")
                with TabbedContent(initial="latest-tab", id="agent-tabs"):
                    with TabPane("Latest", id="latest-tab"):
                        yield NavigationTextArea(id="detail", read_only=True)
                        with Vertical(id="latest-plan-choice-panel"):
                            yield Static(
                                "Plan Options",
                                id="latest-plan-choice-title",
                            )
                            yield DataTable(
                                id="latest-plan-options",
                                cursor_type="row",
                                show_row_labels=False,
                            )
                            yield Static(
                                "Reply with /plan:1 optional notes.",
                                id="latest-plan-hint",
                            )
                        with Vertical(id="tmux-panel"):
                            yield Static("Tmux: -", id="tmux-status")
                            yield TmuxStreamTextArea(id="tmux-stream", read_only=True)
                            with Horizontal(id="tmux-actions"):
                                yield Button("Auto", id="tmux-auto")
                                yield Button("Select Pane", id="tmux-select")
                                yield Button("Detach", id="tmux-detach")
                                yield Button("Send", id="tmux-send", variant="primary")
                            yield FollowUpTextArea(id="tmux-message", soft_wrap=True)
                            yield Static(
                                self.tmux_hotkeys_text(),
                                id="tmux-hotkeys",
                            )
                        with Vertical(id="composer"):
                            with Horizontal(id="composer-inputs"):
                                yield Input(placeholder="Agent id", id="agent-id")
                                yield FollowUpTextArea(id="message", soft_wrap=True)
                            with Horizontal(id="composer-actions"):
                                yield Static("", id="composer-button-spacer")
                                yield Static("", id="composer-button-inset")
                                with Horizontal(id="composer-buttons"):
                                    yield Button(
                                        "Send Input",
                                        id="send",
                                        variant="primary",
                                    )
                                    yield Button(
                                        "Request Detail",
                                        id="request-detail",
                                    )
                                    yield Button("Ping", id="ping-agent")
                                    yield Button("Mark Working", id="mark-working")
                                    yield Button("Mark Canceled", id="mark-canceled")
                            yield Static(
                                self.composer_hotkeys_text(),
                                id="composer-hotkeys",
                            )
                    with TabPane("Thread", id="thread-tab"):
                        yield DataTable(
                            id="thread",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="thread-detail", read_only=True)
                        with Vertical(id="plan-choice-panel"):
                            yield Static("Plan Options", id="plan-choice-title")
                            yield DataTable(
                                id="plan-options",
                                cursor_type="row",
                                show_row_labels=False,
                            )
                            yield Static(
                                "Reply with /plan:1 optional notes.",
                                id="plan-hint",
                            )
                        with Horizontal(id="thread-actions"):
                            yield Button("Export Item", id="export-item")
                            yield Button("Export Marked", id="export-marked")
                            yield Button("Export All", id="export-all")
                            yield Button("Clear Marks", id="clear-marks")
                            yield Button("Delete Queued", id="delete-queued")
                    with TabPane("Files", id="files-tab"):
                        yield Static("Path: .", id="file-path")
                        yield DataTable(
                            id="files",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield RichLog(
                            id="file-preview",
                            wrap=False,
                            highlight=False,
                            markup=False,
                            auto_scroll=False,
                        )
                        with Horizontal(id="file-actions"):
                            yield Button("Refresh Files", id="files-refresh")
                            yield Button("Up", id="files-up")
                    with TabPane("WorkerBee", id="workerbee-tab"):
                        yield NavigationTextArea(id="workerbee-detail", read_only=True)
                        with Horizontal(id="workerbee-actions"):
                            yield Button("Refresh WorkerBee", id="workerbee-refresh")
                    with TabPane("PRs", id="pull-requests-tab"):
                        yield Static("Pull Requests: checking...", id="pull-request-status")
                        yield DataTable(
                            id="pull-requests",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="pull-request-detail", read_only=True)
                        with Horizontal(id="pull-request-actions"):
                            yield Button("Refresh", id="pr-refresh")
                            yield Button("Review", id="pr-review")
                            yield Button("Validate", id="pr-validate")
                            yield Button("URL", id="pr-url")
                            yield Button("Merge", id="pr-merge", variant="error")
                    with TabPane("Issues", id="issues-tab"):
                        yield Static("Issues: checking...", id="issue-status")
                        yield DataTable(
                            id="issues",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="issue-detail", read_only=True)
                        with Horizontal(id="issue-actions"):
                            yield Button("Refresh", id="issue-refresh")
                            yield Button("Mitigate", id="issue-mitigate")
                            yield Button("URL", id="issue-url")
                            yield Button("Clear", id="issue-clear", variant="error")
                    with TabPane("Campaigns", id="campaigns-tab"):
                        yield Static("Campaigns: select an operator", id="campaign-status")
                        yield DataTable(
                            id="campaigns",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="campaign-detail", read_only=True)
                        with Horizontal(id="campaign-actions"):
                            yield Button("Refresh", id="campaign-refresh")
                            yield Button("Monitor (M)", id="campaign-monitor")
                            yield Button("View Report (R)", id="campaign-report")
                            yield Button("Copy Note (C)", id="campaign-copy-joplin")
                    with TabPane("Joplin", id="joplin-tab"):
                        yield Static("Joplin: checking...", id="joplin-status")
                        yield DataTable(
                            id="joplin-notes",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="joplin-body")
                        with Vertical(id="joplin-actions"):
                            with Horizontal(id="joplin-crud-actions"):
                                yield Button("New n", id="joplin-new")
                                yield Button("Ren m", id="joplin-rename")
                                yield Button("Del d", id="joplin-delete")
                                yield Button("Save s", id="joplin-save", variant="primary")
                                yield Button("Ref r", id="joplin-refresh")
                            with Horizontal(id="joplin-log-actions"):
                                yield Button("Copy c", id="joplin-copy-latest")
                                yield Button("LOG+ l", id="joplin-log-start")
                                yield Button("LOG- x", id="joplin-log-stop")
                                yield Button("Sync u", id="joplin-sync")
                            yield Static(self.joplin_hotkeys_text(), id="joplin-hotkeys")
        yield Footer()

    async def on_mount(self) -> None:
        self.api_client()
        self.apply_theme_class()
        self.update_effective_layout()
        self.apply_layout_class()
        self.apply_tmux_class()
        agents = self.query_one("#agents", DataTable)
        self.render_agent_columns(agents)
        operators = self.query_one("#operators", DataTable)
        self.render_operator_columns(operators)
        events = self.query_one("#events", DataTable)
        events.add_columns("ID", "Type", "Subject")
        thread = self.query_one("#thread", DataTable)
        thread.add_columns("M", "Time", "Kind", "Plan", "Status", "Summary")
        files = self.query_one("#files", DataTable)
        files.add_columns("Type", "Name", "Size", "Modified")
        pull_requests = self.query_one("#pull-requests", DataTable)
        pull_requests.add_columns("#", "State", "Checks", "Title")
        issues = self.query_one("#issues", DataTable)
        issues.add_columns("#", "State", "Labels", "Title", "Updated")
        campaigns = self.query_one("#campaigns", DataTable)
        campaigns.add_columns("Status", "Assignments", "Title", "Updated")
        joplin_notes = self.query_one("#joplin-notes", DataTable)
        joplin_notes.add_columns("Updated", "Title")
        latest_plan_options = self.query_one("#latest-plan-options", DataTable)
        latest_plan_options.add_columns("#", "Option")
        plan_options = self.query_one("#plan-options", DataTable)
        plan_options.add_columns("#", "Option")
        self.update_hidden_agent_button()
        self.render_latest_plan_choice_panel(None)
        self.render_plan_choice_panel(None)
        await self.refresh_joplin_status()
        await self.refresh_agents()
        await self.refresh_events()
        self.notify_custom_slash_command_errors()
        self.restart_refresh_timers()
        self.run_worker(
            self.stream_events(),
            name="events",
            group="event-stream",
            exclusive=True,
            exit_on_error=False,
        )

    async def on_unmount(self) -> None:
        self.stop_refresh_timers()
        if self.http_client is not None:
            await self.http_client.aclose()
            self.http_client = None

    def stop_refresh_timers(self) -> None:
        for timer in (
            self.agent_refresh_timer,
            self.tmux_refresh_timer,
            self.attention_blink_timer,
        ):
            if timer is not None:
                timer.stop()
        self.agent_refresh_timer = None
        self.tmux_refresh_timer = None
        self.attention_blink_timer = None

    def restart_refresh_timers(self) -> None:
        self.stop_refresh_timers()
        self.agent_refresh_seconds = self.resolve_agent_refresh_seconds()
        self.attention_blink_seconds = self.resolve_attention_blink_seconds()
        self.agent_refresh_timer = self.set_interval(
            self.agent_refresh_seconds,
            self.schedule_refresh_agents,
        )
        self.tmux_refresh_timer = self.set_interval(
            self.tmux_refresh_seconds,
            self.schedule_refresh_tmux_capture,
        )
        self.attention_blink_timer = self.set_interval(
            self.attention_blink_seconds,
            self.toggle_unseen_attention,
        )

    def schedule_refresh_agents(self) -> None:
        self.run_worker(
            self.refresh_agents,
            name="agents-periodic-refresh",
            group="agents-refresh",
            exclusive=True,
        )

    def schedule_refresh_tmux_capture(self) -> None:
        self.run_worker(
            self.refresh_tmux_capture_if_active,
            name="tmux-periodic-refresh",
            group="tmux-refresh",
            exclusive=True,
        )

    def get_system_commands(self, screen: Any) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("/refresh", "Refresh agents, events, and selected agent", self.palette_refresh)
        yield SystemCommand("/detail", "Request detail for the selected agent", self.palette_request_detail)
        yield SystemCommand("/ping", "Ping the selected nohup-mode agent", self.palette_ping)
        yield SystemCommand("/unblock", "Mark the selected agent working", self.palette_mark_working)
        yield SystemCommand("/working", "Mark the selected agent working", self.palette_mark_working)
        yield SystemCommand("/cancel", "Mark the selected agent canceled", self.palette_mark_canceled)
        yield SystemCommand("/esc", "Send Escape to the selected agent", self.palette_escape)
        yield SystemCommand("/ctrlc", "Send Ctrl+C to the selected tmux pane", self.palette_ctrl_c)
        yield SystemCommand("/tmux", "Toggle tmux direct mode", self.palette_toggle_tmux)
        yield SystemCommand("/latest", "Open the Latest tab", self.palette_latest)
        yield SystemCommand("/thread", "Open the Thread tab", self.palette_thread)
        yield SystemCommand("/files", "Open and refresh the Files tab", self.palette_files)
        yield SystemCommand("/workerbee", "Open and refresh the WorkerBee tab", self.palette_workerbee)
        yield SystemCommand("/campaigns", "Open and refresh operator campaigns", self.palette_campaigns)
        yield SystemCommand("/campaign monitor", "Ask the root operator to monitor selected campaign", self.palette_campaign_monitor)
        yield SystemCommand("/campaign report", "View the selected campaign report", self.palette_campaign_report)
        yield SystemCommand("/campaign copy", "Copy selected campaign to a new Joplin note", self.palette_campaign_copy)
        yield SystemCommand("/pr", "Open and refresh pull requests", self.palette_pull_requests)
        yield SystemCommand("/pr refresh", "Refresh pull requests", self.palette_pull_requests_refresh)
        yield SystemCommand("/pr review", "Ask selected agent to review the selected PR", self.palette_pull_request_review)
        yield SystemCommand("/pr validate", "Ask selected agent to run WorkerBee validation for the selected PR", self.palette_pull_request_validate)
        yield SystemCommand("/pr url", "Show the selected PR URL", self.palette_pull_request_url)
        yield SystemCommand("/pr merge", "Merge selected PR when enabled", self.palette_pull_request_merge)
        yield SystemCommand("/issue", "Open and refresh GitHub issues", self.palette_issues)
        yield SystemCommand("/issue refresh", "Refresh GitHub issues", self.palette_issues_refresh)
        yield SystemCommand("/issue mitigate", "Ask selected agent to mitigate the selected issue", self.palette_issue_mitigate)
        yield SystemCommand("/issue url", "Show the selected issue URL", self.palette_issue_url)
        yield SystemCommand("/issue clear", "Comment and close selected issue when enabled", self.palette_issue_clear)
        if self.joplin_configured:
            yield SystemCommand("/joplin", "Open and refresh the Joplin tab", self.palette_joplin)
            yield SystemCommand("/joplin refresh", "Refresh scoped Joplin notes", self.palette_joplin_refresh)
            yield SystemCommand("/joplin new", "Create a scoped Joplin note", self.palette_joplin_new)
            yield SystemCommand("/joplin rename", "Rename the selected Joplin note", self.palette_joplin_rename)
            yield SystemCommand("/joplin delete", "Delete the selected Joplin note", self.palette_joplin_delete)
            yield SystemCommand("/joplin copy", "Copy latest tmux response or report to Joplin", self.palette_joplin_copy)
            yield SystemCommand("/joplin copy report", "Copy latest PBX report to Joplin", self.palette_joplin_copy_report)
            yield SystemCommand("/joplin log start", "Start Joplin LOG for the selected agent", self.palette_joplin_log_start)
            yield SystemCommand("/joplin log stop", "Stop Joplin LOG for the selected agent", self.palette_joplin_log_stop)
            yield SystemCommand("/joplin save", "Save the selected Joplin note body", self.palette_joplin_save)
            yield SystemCommand("/joplin sync", "Queue a Joplin sync job", self.palette_joplin_sync)
        yield SystemCommand("/plan", "Toggle plan mode for the selected agent", self.palette_toggle_plan_mode)
        yield SystemCommand("/plan latest", "Show latest report plan options", self.palette_plan_latest)
        yield SystemCommand("/plan thread", "Show selected thread plan options", self.palette_plan_thread)
        yield SystemCommand("/operator start", "Start a new operator agent", self.palette_operator_start)
        yield SystemCommand("/operator focus", "Focus the Operators table", self.palette_operator_focus)
        yield SystemCommand(
            "/operator history",
            "Show selected operator session history",
            self.palette_operator_history,
        )
        yield SystemCommand(
            "/operator resume",
            "Resume the selected operator from local Codex history",
            self.palette_operator_resume,
        )
        yield SystemCommand("/operator restart", "Restart the selected operator", self.palette_operator_restart)
        yield SystemCommand("/operator stop", "Stop the selected operator", self.palette_operator_stop)
        yield SystemCommand("/operator hide", "Hide the selected operator", self.palette_operator_hide)
        yield SystemCommand("/operator purge", "Purge the selected operator", self.palette_operator_purge)
        yield SystemCommand("/operator fork next", "View the next fork pane for the selected operator", self.palette_operator_fork_next)
        yield SystemCommand("/operator fork prev", "View the previous fork pane for the selected operator", self.palette_operator_fork_prev)
        yield SystemCommand("/operator fork review", "Start a read-only review fork for the selected operator/caller", self.palette_operator_fork_review)
        yield SystemCommand("/commands reload", "Reload custom slash commands", self.palette_reload_custom_slash_commands)
        yield from self.palette_native_plan_selector_commands()
        yield from self.palette_dynamic_plan_commands()
        if self.is_tmux_direct_enabled():
            yield SystemCommand("/gitstatus", "Run !git status in the selected tmux pane", self.palette_git_status)
            yield SystemCommand("/gitdiff", "Run !git diff with an optional target in tmux", self.palette_git_diff)
            yield SystemCommand("/gitpush", "Run !git push origin with an optional branch in tmux", self.palette_git_push)
            yield SystemCommand("/gitstageandcommit", "Ask Codex to stage and commit changes", self.palette_git_stage_and_commit)
            yield from self.palette_custom_slash_commands()
        yield SystemCommand("/hide agent", "Hide the selected agent from the Agents view", self.palette_hide_agent)
        yield SystemCommand("/show hidden agents", "Toggle hidden agents in the Agents view", self.palette_toggle_hidden_agents)
        yield SystemCommand("/unhide agent", "Unhide the selected hidden agent", self.palette_unhide_agent)
        yield SystemCommand("/purge agent", "Hide selected agent and delete its thread data", self.palette_purge_agent)
        yield SystemCommand("/theme cyberpunk", "Use the Cyberpunk theme", lambda: self.palette_set_theme(DEFAULT_TUI_THEME))
        yield SystemCommand("/theme minimal", "Use the high-compatibility Minimal theme", lambda: self.palette_set_theme(MINIMAL_TUI_THEME))
        yield SystemCommand(f"/theme {self.custom_theme_name}", "Use the custom TUI theme", lambda: self.palette_set_theme(self.custom_theme_name))
        yield SystemCommand("/layout adaptive", "Use adaptive layout mode", lambda: self.palette_set_layout(ADAPTIVE_TUI_LAYOUT))
        yield SystemCommand("/layout split", "Use split layout mode", lambda: self.palette_set_layout(SPLIT_TUI_LAYOUT))
        yield SystemCommand("/layout compact", "Use compact layout mode", lambda: self.palette_set_layout(COMPACT_TUI_LAYOUT))
        yield SystemCommand("/layout tiny", "Use tiny layout mode", lambda: self.palette_set_layout(TINY_TUI_LAYOUT))

    def palette_refresh(self) -> None:
        self.run_worker(self.action_refresh(), name="palette-refresh", exclusive=True)

    def palette_agent_id(self) -> str | None:
        agent_id = self.selected_or_cursor_agent_id()
        if not agent_id:
            self.notify("Select an agent first.", severity="warning")
            return None
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        return agent_id

    def palette_request_detail(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.request_detail(), name="palette-request-detail", exclusive=True)

    def palette_ping(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.ping_agent(), name="palette-ping", exclusive=True)

    def palette_mark_working(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.mark_agent_working(), name="palette-working", exclusive=True)

    def palette_mark_canceled(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.mark_agent_canceled(), name="palette-cancel", exclusive=True)

    def palette_escape(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.send_escape_key(), name="palette-esc", exclusive=True)

    def palette_ctrl_c(self) -> None:
        if self.palette_agent_id() is None:
            return
        self.run_worker(self.send_ctrl_c_key(), name="palette-ctrlc", exclusive=True)

    def palette_toggle_tmux(self) -> None:
        self.run_worker(self.action_toggle_tmux_direct(), name="palette-tmux", exclusive=True)

    def palette_latest(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_latest_for_agent(agent_id),
            name="palette-latest",
            exclusive=True,
        )

    def palette_thread(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_thread_for_agent(agent_id),
            name="palette-thread",
            exclusive=True,
        )

    def palette_files(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_files_for_agent(agent_id),
            name="palette-files",
            exclusive=True,
        )

    def palette_workerbee(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_workerbee_for_agent(agent_id),
            name="palette-workerbee",
            exclusive=True,
        )

    def palette_campaigns(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_campaigns_for_agent(agent_id),
            name="palette-campaigns",
            exclusive=True,
        )

    def palette_campaign_report(self) -> None:
        self.run_worker(
            self.view_selected_campaign_report(),
            name="palette-campaign-report",
            exclusive=True,
        )

    def palette_campaign_monitor(self) -> None:
        self.run_worker(
            self.monitor_selected_campaign(),
            name="palette-campaign-monitor",
            exclusive=True,
        )

    def palette_campaign_copy(self) -> None:
        self.run_worker(
            self.copy_selected_campaign_to_joplin(),
            name="palette-campaign-copy",
            exclusive=True,
        )

    def palette_pull_requests(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_pull_requests_for_agent(agent_id),
            name="palette-pr",
            exclusive=True,
        )

    def palette_pull_requests_refresh(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.pull_request_action_for_agent(agent_id, "refresh"),
            name="palette-pr-refresh",
            exclusive=True,
        )

    def palette_pull_request_review(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.pull_request_action_for_agent(agent_id, "review"),
            name="palette-pr-review",
            exclusive=True,
        )

    def palette_pull_request_validate(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.pull_request_action_for_agent(agent_id, "validate"),
            name="palette-pr-validate",
            exclusive=True,
        )

    def palette_pull_request_url(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.pull_request_action_for_agent(agent_id, "url"),
            name="palette-pr-url",
            exclusive=True,
        )

    def palette_pull_request_merge(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.pull_request_action_for_agent(agent_id, "merge"),
            name="palette-pr-merge",
            exclusive=True,
        )

    def palette_issues(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_issues_for_agent(agent_id),
            name="palette-issues",
            exclusive=True,
        )

    def palette_issues_refresh(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.issue_action_for_agent(agent_id, "refresh"),
            name="palette-issue-refresh",
            exclusive=True,
        )

    def palette_issue_mitigate(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.issue_action_for_agent(agent_id, "mitigate"),
            name="palette-issue-mitigate",
            exclusive=True,
        )

    def palette_issue_url(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.issue_action_for_agent(agent_id, "url"),
            name="palette-issue-url",
            exclusive=True,
        )

    def palette_issue_clear(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.issue_action_for_agent(agent_id, "clear"),
            name="palette-issue-clear",
            exclusive=True,
        )

    def palette_operator_start(self) -> None:
        self.run_worker(
            self.start_operator_agent(),
            name="palette-operator-start",
            exclusive=True,
        )

    def palette_operator_focus(self) -> None:
        self.action_focus_operators()

    def palette_operator_history(self) -> None:
        self.run_worker(
            self.show_selected_operator_history(),
            name="palette-operator-history",
            exclusive=True,
        )

    def palette_operator_resume(self) -> None:
        self.run_worker(
            self.resume_selected_operator(),
            name="palette-operator-resume",
            exclusive=True,
        )

    def palette_operator_restart(self) -> None:
        self.run_worker(
            self.restart_selected_operator(),
            name="palette-operator-restart",
            exclusive=True,
        )

    def palette_operator_stop(self) -> None:
        self.run_worker(
            self.stop_selected_operator(),
            name="palette-operator-stop",
            exclusive=True,
        )

    def palette_operator_hide(self) -> None:
        agent_id = self.selected_operator_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.dismiss_selected_agent(agent_id=agent_id, delete_thread=False),
            name="palette-operator-hide",
            exclusive=True,
        )

    def palette_operator_purge(self) -> None:
        agent_id = self.selected_operator_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.dismiss_selected_agent(agent_id=agent_id, delete_thread=True),
            name="palette-operator-purge",
            exclusive=True,
        )

    def palette_operator_fork_next(self) -> None:
        self.run_worker(
            self.cycle_selected_operator_fork(1),
            name="palette-operator-fork-next",
            exclusive=True,
        )

    def palette_operator_fork_prev(self) -> None:
        self.run_worker(
            self.cycle_selected_operator_fork(-1),
            name="palette-operator-fork-prev",
            exclusive=True,
        )

    def palette_operator_fork_review(self) -> None:
        self.run_worker(
            self.start_review_operator_fork(),
            name="palette-operator-fork-review",
            exclusive=True,
        )

    def palette_joplin(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_joplin_for_agent(agent_id),
            name="palette-joplin",
            exclusive=True,
        )

    def palette_joplin_refresh(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_joplin_for_agent(agent_id),
            name="palette-joplin-refresh",
            exclusive=True,
        )

    def palette_joplin_new(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.open_joplin_title_modal(agent_id, action="new")

    def palette_joplin_rename(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.open_joplin_title_modal(agent_id, action="rename")

    def palette_joplin_delete(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.confirm_delete_joplin_note(agent_id)

    def palette_joplin_copy(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "copy"),
            name="palette-joplin-copy",
            exclusive=True,
        )

    def palette_joplin_copy_report(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "copy-report"),
            name="palette-joplin-copy-report",
            exclusive=True,
        )

    def palette_joplin_log_start(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "log-start"),
            name="palette-joplin-log-start",
            exclusive=True,
        )

    def palette_joplin_log_stop(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "log-stop"),
            name="palette-joplin-log-stop",
            exclusive=True,
        )

    def palette_joplin_save(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "save"),
            name="palette-joplin-save",
            exclusive=True,
        )

    def palette_joplin_sync(self) -> None:
        agent_id = self.palette_joplin_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.joplin_action_for_agent(agent_id, "sync"),
            name="palette-joplin-sync",
            exclusive=True,
        )

    def palette_reload_custom_slash_commands(self) -> None:
        self.reload_custom_slash_commands(notify=True)

    def palette_custom_slash_commands(self) -> Iterable[SystemCommand]:
        for command in self.custom_slash_commands:
            yield SystemCommand(
                command.name,
                command.description,
                lambda command=command: self.palette_custom_slash_command(command),
            )

    def palette_custom_slash_command(self, command: CustomSlashCommand) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        if command.uses_arg:
            self.push_screen(
                CustomSlashCommandArgScreen(agent_id=agent_id, command=command)
            )
            return
        self.run_worker(
            self.palette_send_tmux_prompt(agent_id, command.prompt, command.name),
            name=f"palette-custom-{slugify(command.name)}",
            exclusive=True,
        )

    async def palette_custom_slash_command_arg(
        self,
        agent_id: str,
        command: CustomSlashCommand,
        arg: str,
    ) -> None:
        await self.palette_send_tmux_prompt(
            agent_id,
            render_custom_slash_prompt(command, arg),
            command.name,
        )

    def reload_custom_slash_commands(self, *, notify: bool = False) -> None:
        (
            self.custom_slash_commands,
            self.custom_slash_command_errors,
        ) = load_custom_slash_commands(
            self.slash_commands_file,
            built_in_names=built_in_palette_command_names(self.custom_theme_name),
        )
        if notify:
            if self.custom_slash_command_errors:
                self.notify_custom_slash_command_errors()
                return
            count = len(self.custom_slash_commands)
            self.notify(
                f"Loaded {count} custom slash command{'s' if count != 1 else ''}."
            )

    def notify_custom_slash_command_errors(self) -> None:
        if not self.custom_slash_command_errors:
            return
        first = self.custom_slash_command_errors[0]
        extra = len(self.custom_slash_command_errors) - 1
        suffix = f" (+{extra} more)" if extra else ""
        self.notify(f"Custom slash commands: {first}{suffix}", severity="warning")

    def palette_tmux_agent_id(self) -> str | None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return None
        if not self.is_tmux_direct_enabled(agent_id):
            self.notify(
                f"Enable tmux direct mode for {agent_id} before using this command.",
                severity="warning",
            )
            return None
        return agent_id

    def palette_git_status(self) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.palette_send_tmux_prompt(agent_id, "!git status", "Git status"),
            name="palette-gitstatus",
            exclusive=True,
        )

    def palette_git_stage_and_commit(self) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.palette_send_tmux_prompt(
                agent_id,
                "stage and commit the changes",
                "Stage and commit",
            ),
            name="palette-gitstageandcommit",
            exclusive=True,
        )

    def palette_git_diff(self) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        self.push_screen(GitDiffScreen(agent_id=agent_id))

    async def palette_git_diff_target(self, agent_id: str, target: str = "") -> None:
        await self.palette_send_tmux_prompt(
            agent_id,
            git_diff_passthrough_command(target),
            "Git diff",
        )

    def palette_git_push(self) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        self.push_screen(GitPushScreen(agent_id=agent_id))

    async def palette_git_push_target(self, agent_id: str, branch: str = "") -> None:
        await self.palette_send_tmux_prompt(
            agent_id,
            git_push_passthrough_command(branch),
            "Git push",
        )

    async def palette_send_tmux_prompt(
        self, agent_id: str, prompt: str, label: str
    ) -> None:
        if not self.is_tmux_direct_enabled(agent_id):
            self.notify(
                f"Enable tmux direct mode for {agent_id} before using this command.",
                severity="warning",
            )
            return
        sent = await self.send_text_to_tmux(agent_id, prompt)
        if not sent:
            return
        await self.record_tmux_joplin_interaction(agent_id, prompt)
        self.notify(f"{label} sent to tmux for {agent_id}.")
        await self.load_tmux_capture(agent_id)

    def palette_toggle_plan_mode(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        state = self.plan_mode_state(agent_id)
        if state == "pending":
            self.pending_slash_command_by_agent.pop(agent_id, None)
            self.update_agent_title()
            self.notify(f"Plan mode canceled for {agent_id}; /plan was not sent.")
            return
        self.run_worker(
            self.toggle_sent_plan_mode(agent_id),
            name=f"plan-toggle-{slugify(agent_id)}",
            exclusive=True,
        )

    def palette_prime_plan_prompt(self) -> None:
        self.palette_toggle_plan_mode()

    def palette_plan_latest(self) -> None:
        context = self.palette_latest_plan_context()
        if context is None:
            self.notify("No latest plan options for the selected agent.", severity="warning")
            return
        agent_id, options = context
        self.show_plan_options_for_reply(agent_id, options, source="latest report")

    def palette_plan_thread(self) -> None:
        context = self.palette_thread_plan_context()
        if context is None:
            self.notify("No selected thread plan options.", severity="warning")
            return
        agent_id, options = context
        self.show_plan_options_for_reply(agent_id, options, source="thread item")

    def palette_native_plan_selector_commands(self) -> Iterable[SystemCommand]:
        agent_id = self.palette_context_agent_id()
        if (
            agent_id is None
            or not self.is_tmux_direct_enabled(agent_id)
            or not (
                self.tmux_native_plan_selector_pending(agent_id)
                or self.tmux_visible_native_plan_selector_pending()
            )
        ):
            return
        indices = self.available_native_plan_selector_indices(agent_id)
        if not indices:
            indices = set(CODEX_NATIVE_PLAN_SELECTOR_CHOICES)
        for index in sorted(indices):
            label = CODEX_NATIVE_PLAN_SELECTOR_CHOICES.get(index, f"option {index}")
            yield SystemCommand(
                f"/plan select {index}: {label}",
                f"Press {index} in the Codex plan selector for {agent_id}",
                lambda index=index: self.palette_send_native_plan_selection(index),
            )

    def palette_send_native_plan_selection(self, index: int) -> None:
        agent_id = self.palette_tmux_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.send_native_plan_selection(
                agent_id,
                PlanSelection(index=index),
            ),
            name=f"palette-plan-select-{slugify(agent_id)}-{index}",
            exclusive=True,
        )

    def palette_latest_plan_context(self) -> tuple[str, list[PlanChoice]] | None:
        agent_id = self.palette_context_agent_id()
        if agent_id is None:
            return None
        options = self.plan_options_for_report(self.latest_report_by_agent.get(agent_id))
        if not options:
            return None
        return agent_id, options

    def palette_thread_plan_context(self) -> tuple[str, list[PlanChoice]] | None:
        agent_id = self.palette_context_agent_id()
        if agent_id is None:
            return None
        item_id = self.selected_thread_item_id
        if item_id is None:
            thread = self.query_one_or_none("#thread", DataTable)
            if (
                thread is not None
                and thread.row_count > 0
                and thread.is_valid_row_index(thread.cursor_row)
            ):
                item_id = str(
                    thread.coordinate_to_cell_key(thread.cursor_coordinate).row_key.value
                )
        options = self.plan_options_for_item(self.thread_items.get(item_id or ""))
        if not options:
            return None
        return agent_id, options

    def palette_context_agent_id(self) -> str | None:
        if self.selected_agent_id:
            return self.selected_agent_id
        focused_agent_id = self.focused_agent_table_id()
        if focused_agent_id:
            return focused_agent_id
        cursor_agent_id = self.agent_id_at_cursor()
        if cursor_agent_id:
            return cursor_agent_id
        cursor_operator_id = self.operator_id_at_cursor()
        if cursor_operator_id:
            return cursor_operator_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None and agent_input.value.strip():
            return agent_input.value.strip()
        return None

    def palette_dynamic_plan_commands(self) -> Iterable[SystemCommand]:
        latest = self.palette_latest_plan_context()
        if latest is not None:
            agent_id, options = latest
            for index, option in enumerate(options):
                yield SystemCommand(
                    f"/plan latest {index + 1}: {self.palette_option_label(option)}",
                    f"Prepare /plan:{index + 1} reply for {agent_id}",
                    lambda index=index: self.palette_prepare_latest_plan_option(index),
                )
        thread = self.palette_thread_plan_context()
        if thread is not None:
            agent_id, options = thread
            for index, option in enumerate(options):
                yield SystemCommand(
                    f"/plan thread {index + 1}: {self.palette_option_label(option)}",
                    f"Prepare /plan:{index + 1} reply for {agent_id}",
                    lambda index=index: self.palette_prepare_thread_plan_option(index),
                )

    def palette_option_label(self, option: PlanChoice | str) -> str:
        choice = option if isinstance(option, PlanChoice) else plan_choice_from_value(option)
        label = " ".join((choice.display_label if choice else "").strip().split())
        if len(label) > 72:
            return f"{label[:69]}..."
        return label

    def palette_prepare_latest_plan_option(self, selected_index: int) -> None:
        context = self.palette_latest_plan_context()
        if context is None:
            self.notify("No latest plan options for the selected agent.", severity="warning")
            return
        agent_id, options = context
        self.prepare_plan_selection_reply(agent_id, selected_index, options)

    def palette_prepare_thread_plan_option(self, selected_index: int) -> None:
        context = self.palette_thread_plan_context()
        if context is None:
            self.notify("No selected thread plan options.", severity="warning")
            return
        agent_id, options = context
        self.prepare_plan_selection_reply(agent_id, selected_index, options)

    def show_plan_options_for_reply(
        self,
        agent_id: str,
        options: list[PlanChoice],
        *,
        source: str,
    ) -> None:
        self.activate_latest_tab()
        self.notify(
            f"{len(options)} plan option(s) in {source}; reply with /plan:1 optional notes."
        )

    def prepare_plan_selection_reply(
        self,
        agent_id: str,
        selected_index: int,
        options: list[PlanChoice],
    ) -> None:
        if selected_index < 0 or selected_index >= len(options):
            self.notify("Plan option is no longer available.", severity="warning")
            return
        self.selected_agent_id = agent_id
        self.query_one("#agent-id", Input).value = agent_id
        command = f"/plan:{selected_index + 1} "
        if self.is_tmux_direct_enabled(agent_id):
            target = self.query_one_or_none("#tmux-message", TextArea)
            if target is None:
                return
            target.text = command
            target.focus()
        else:
            target = self.query_one_or_none("#message", TextArea)
            if target is None:
                return
            target.text = command
            target.focus()
            self.resize_message_input()
        self.update_agent_title()
        self.notify(f"Prepared {command.strip()} for {agent_id}; add notes and press Enter.")

    def palette_hide_agent(self) -> None:
        self.run_worker(
            self.dismiss_selected_agent(delete_thread=False),
            name="palette-hide-agent",
            exclusive=True,
        )

    def palette_toggle_hidden_agents(self) -> None:
        self.action_toggle_hidden_agents()

    def palette_unhide_agent(self) -> None:
        self.run_worker(
            self.unhide_selected_agent(),
            name="palette-unhide-agent",
            exclusive=True,
        )

    def palette_purge_agent(self) -> None:
        self.run_worker(
            self.dismiss_selected_agent(delete_thread=True),
            name="palette-purge-agent",
            exclusive=True,
        )

    def palette_set_theme(self, theme_name: str) -> None:
        self.set_ui_theme(theme_name)
        self.notify(f"Theme set to {self.ui_theme}.")

    def palette_set_layout(self, layout_name: str) -> None:
        self.set_layout_mode(layout_name)
        self.notify(f"Layout set to {self.layout_mode}.")

    async def open_latest_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_latest_tab()
        await self.load_latest_report(agent_id)
        await self.load_thread(agent_id)

    async def open_thread_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("thread-tab")
        await self.load_thread(agent_id)

    async def open_files_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("files-tab")
        await self.load_agent_files(agent_id)

    async def open_workerbee_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("workerbee-tab")
        await self.load_workerbee_status(agent_id)

    async def open_campaigns_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("campaigns-tab")
        await self.load_operator_campaigns(agent_id)

    def activate_campaigns_tab(self) -> None:
        self.activate_agent_tab("campaigns-tab")

    async def campaign_action_for_agent(
        self,
        agent_id: str,
        action: str,
    ) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_campaigns_tab()
        self.update_agent_title()
        if action == "report":
            if agent_id not in self.campaigns_by_operator:
                await self.load_operator_campaigns(agent_id)
            await self.view_selected_campaign_report()
        elif action == "monitor":
            if agent_id not in self.campaigns_by_operator:
                await self.load_operator_campaigns(agent_id)
            await self.monitor_selected_campaign()
        elif action == "copy":
            if agent_id not in self.campaigns_by_operator:
                await self.load_operator_campaigns(agent_id)
            await self.copy_selected_campaign_to_joplin()
        elif action in {"open", "refresh"}:
            await self.load_operator_campaigns(agent_id)
        else:
            self.notify(f"Unknown campaign action: {action}", severity="warning")

    async def open_pull_requests_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("pull-requests-tab")
        await self.load_pull_requests(agent_id)

    async def open_issues_for_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_agent_tab("issues-tab")
        await self.load_issues(agent_id)

    async def open_joplin_for_agent(self, agent_id: str) -> None:
        await self.refresh_joplin_status()
        if not self.joplin_configured:
            self.notify("Joplin is not configured on this Agent PBX server.", severity="warning")
            return
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            self.selected_agent_id = agent_id
        self.activate_joplin_tab()
        await self.load_joplin_notes(agent_id)

    def activate_joplin_tab(self) -> None:
        self.activate_agent_tab("joplin-tab")

    async def joplin_action_for_agent(self, agent_id: str, action: str) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_joplin_tab()
        self.update_agent_title()
        if action == "open":
            await self.load_joplin_notes(agent_id)
        elif action == "refresh":
            await self.load_joplin_notes(agent_id)
        elif action == "new":
            self.open_joplin_title_modal(agent_id, action="new")
        elif action == "rename":
            self.open_joplin_title_modal(agent_id, action="rename")
        elif action == "delete":
            self.confirm_delete_joplin_note(agent_id)
        elif action == "copy":
            await self.copy_latest_to_joplin(agent_id)
        elif action == "copy-report":
            await self.copy_latest_report_to_joplin(agent_id)
        elif action == "log-start":
            await self.start_joplin_log(agent_id)
        elif action == "log-stop":
            await self.stop_joplin_log(agent_id)
        elif action == "save":
            await self.save_joplin_note(agent_id)
        elif action == "sync":
            await self.sync_joplin_now(agent_id)
        else:
            self.notify(f"Unknown Joplin action: {action}", severity="warning")

    async def action_refresh(self) -> None:
        await self.refresh_agents()
        await self.refresh_events()
        await self.refresh_joplin_status()
        if self.selected_agent_id:
            await self.refresh_selected_agent(self.selected_agent_id)
            if self.active_agent_tab == "workerbee-tab":
                await self.load_workerbee_status(self.selected_agent_id)
            elif self.active_agent_tab == "pull-requests-tab":
                await self.load_pull_requests(self.selected_agent_id)
            elif self.active_agent_tab == "issues-tab":
                await self.load_issues(self.selected_agent_id)
            elif self.active_agent_tab == "joplin-tab":
                await self.load_joplin_notes(self.selected_agent_id)

    def action_settings(self) -> None:
        self.push_screen(
            SettingsScreen(
                visual_flash_enabled=self.visual_flash_enabled,
                terminal_bell_enabled=self.terminal_bell_enabled,
                agent_blink_enabled=self.agent_blink_enabled,
                low_power_enabled=self.low_power_enabled,
                layout_mode=self.layout_mode,
                split_percent=self.split_percent,
                tmux_direct_enabled=self.tmux_direct_enabled,
                tmux_features_available=self.tmux_features_available,
                custom_theme_name=self.custom_theme_name,
                theme_name=self.ui_theme,
            )
        )

    def action_focus_agents(self) -> None:
        if self.is_collapsed_layout():
            self.compact_view = "home"
            self.set_tiny_home_panel(TINY_HOME_AGENTS, apply=False)
            self.apply_layout_class()
        if self.selected_agent_id in self.agents:
            agent = self.agents[self.selected_agent_id]
            if self.agent_type(agent) == CALLER_AGENT_TYPE:
                self.move_agent_cursor(self.selected_agent_id, focus=True)
                return
        agents = self.query_one_or_none("#agents", DataTable)
        if agents is not None:
            agents.focus()

    def action_focus_operators(self) -> None:
        operators = self.query_one_or_none("#operators", DataTable)
        if operators is None:
            return
        if not self.operator_table_agents():
            self.notify("No operators are registered.", severity="warning")
            return
        if self.is_collapsed_layout():
            self.compact_view = "home"
            self.set_tiny_home_panel(TINY_HOME_OPERATORS, apply=False)
            self.apply_layout_class()
        if self.selected_agent_id in self.agents:
            agent = self.agents[self.selected_agent_id]
            if self.agent_type(agent) == OPERATOR_AGENT_TYPE:
                self.move_operator_cursor(self.selected_agent_id, focus=True)
                return
        operators.focus()

    def action_focus_events(self) -> None:
        if self.is_collapsed_layout():
            self.compact_view = "home"
            if self.is_tiny_layout():
                self.set_tiny_home_panel(TINY_HOME_EVENTS, apply=False)
            self.apply_layout_class()
            self.render_events()
        events = self.query_one_or_none("#events", DataTable)
        if events is not None:
            events.focus()

    async def action_focus_right_pane(self) -> None:
        if not await self.ensure_agent_pane_visible():
            return
        self.focus_right_pane_content()

    async def action_focus_latest_input(self) -> None:
        if not await self.ensure_agent_pane_visible():
            return
        self.activate_latest_tab()
        if self.is_tmux_direct_enabled():
            target = self.query_one_or_none("#tmux-message", TextArea)
        else:
            target = self.query_one_or_none("#message", TextArea)
        if target is not None:
            target.focus()

    async def ensure_agent_pane_visible(self) -> bool:
        agent_id = self.selected_agent_id or self.agent_id_at_cursor()
        if self.is_collapsed_layout():
            if not agent_id:
                self.notify(
                    "Select an agent before focusing the agent pane.",
                    severity="warning",
                )
                return False
            if agent_id != self.selected_agent_id:
                await self.select_agent(agent_id)
            else:
                self.show_compact_agent()
        elif agent_id and agent_id != self.selected_agent_id:
            await self.select_agent(agent_id)
        return True

    def focus_right_pane_content(self) -> None:
        target: Widget | None = None
        if self.active_agent_tab == "latest-tab":
            if self.is_tmux_direct_enabled():
                target = self.query_one_or_none("#tmux-stream", TextArea)
            else:
                target = self.query_one_or_none("#detail", TextArea)
        elif self.active_agent_tab == "thread-tab":
            target = self.query_one_or_none("#thread-detail", TextArea)
            if target is None:
                target = self.query_one_or_none("#thread", DataTable)
        elif self.active_agent_tab == "files-tab":
            target = self.query_one_or_none("#file-preview", RichLog)
            if target is None:
                target = self.query_one_or_none("#files", DataTable)
        elif self.active_agent_tab == "workerbee-tab":
            target = self.query_one_or_none("#workerbee-detail", TextArea)
        elif self.active_agent_tab == "pull-requests-tab":
            target = self.query_one_or_none("#pull-request-detail", TextArea)
            if target is None:
                target = self.query_one_or_none("#pull-requests", DataTable)
        elif self.active_agent_tab == "issues-tab":
            target = self.query_one_or_none("#issue-detail", TextArea)
            if target is None:
                target = self.query_one_or_none("#issues", DataTable)
        elif self.active_agent_tab == "campaigns-tab":
            target = self.query_one_or_none("#campaign-detail", TextArea)
            if target is None:
                target = self.query_one_or_none("#campaigns", DataTable)
        elif self.active_agent_tab == "joplin-tab":
            target = self.query_one_or_none("#joplin-body", TextArea)
            if target is None:
                target = self.query_one_or_none("#joplin-notes", DataTable)
        if target is None:
            target = self.query_one_or_none("#detail", TextArea)
        if target is not None:
            target.focus()

    def action_back(self) -> None:
        if self.is_compact_layout() and self.compact_view == "agent":
            self.show_compact_home()

    def focused_editable_text_input(self) -> bool:
        return isinstance(self.focused, (Input, TextArea)) and not bool(
            getattr(self.focused, "read_only", False)
        )

    def action_start_operator(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.start_operator_agent(),
            name="start-operator",
            exclusive=True,
        )

    def action_restart_operator(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.restart_selected_operator(),
            name="restart-operator",
            exclusive=True,
        )

    def action_operator_history(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.show_selected_operator_history(),
            name="operator-history",
            exclusive=True,
        )

    def action_resume_operator(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.resume_selected_operator(),
            name="resume-operator",
            exclusive=True,
        )

    def action_stop_operator(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.stop_selected_operator(),
            name="stop-operator",
            exclusive=True,
        )

    def action_next_operator_fork(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.cycle_selected_operator_fork(1),
            name="next-operator-fork",
            exclusive=True,
        )

    def action_prev_operator_fork(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.cycle_selected_operator_fork(-1),
            name="prev-operator-fork",
            exclusive=True,
        )

    def action_start_review_operator_fork(self) -> None:
        if self.focused_editable_text_input():
            return
        self.run_worker(
            self.start_review_operator_fork(),
            name="start-review-operator-fork",
            exclusive=True,
        )

    def action_monitor_campaign(self) -> None:
        if self.active_agent_tab != "campaigns-tab":
            return
        if isinstance(self.focused, (Input, TextArea)) and getattr(
            self.focused, "id", None
        ) != "campaign-detail":
            return
        self.run_worker(
            self.monitor_selected_campaign(),
            name="monitor-campaign",
            exclusive=True,
        )

    def action_view_campaign_report(self) -> None:
        if self.active_agent_tab != "campaigns-tab":
            return
        if isinstance(self.focused, (Input, TextArea)) and getattr(
            self.focused, "id", None
        ) != "campaign-detail":
            return
        self.run_worker(
            self.view_selected_campaign_report(),
            name="view-campaign-report",
            exclusive=True,
        )

    def action_copy_campaign_to_joplin(self) -> None:
        if self.active_agent_tab != "campaigns-tab":
            return
        if isinstance(self.focused, (Input, TextArea)) and getattr(
            self.focused, "id", None
        ) != "campaign-detail":
            return
        self.run_worker(
            self.copy_selected_campaign_to_joplin(),
            name="copy-campaign-to-joplin",
            exclusive=True,
        )

    def action_hide_agent(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.run_worker(
            self.dismiss_selected_agent(delete_thread=False),
            name="dismiss-agent",
            exclusive=True,
        )

    def action_toggle_hidden_agents(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.show_hidden_agents = not self.show_hidden_agents
        self.update_hidden_agent_button()
        self.save_settings()
        self.run_worker(
            self.refresh_agents(),
            name="toggle-hidden-agents",
            exclusive=True,
        )
        state = "showing" if self.show_hidden_agents else "hiding"
        self.notify(f"Agents view is now {state} hidden agents.")

    def action_unhide_agent(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.run_worker(
            self.unhide_selected_agent(),
            name="unhide-agent",
            exclusive=True,
        )

    def action_purge_agent(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.run_worker(
            self.dismiss_selected_agent(delete_thread=True),
            name="purge-agent",
            exclusive=True,
        )

    def action_toggle_star_agent(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.toggle_selected_agent_star()

    async def action_toggle_tmux_direct(self) -> None:
        if self.active_agent_tab != "latest-tab":
            return
        if not self.selected_agent_id:
            enabled = self.set_tmux_direct_enabled(not self.tmux_direct_enabled)
            if enabled:
                self.notify("Tmux direct default enabled for agents without overrides.")
            else:
                self.notify("Tmux direct default disabled for agents without overrides.")
            return
        enabled = self.set_agent_tmux_direct_enabled(
            self.selected_agent_id,
            not self.is_tmux_direct_enabled(self.selected_agent_id),
        )
        if enabled:
            await self.load_tmux_capture(self.selected_agent_id)
        else:
            await self.load_latest_report(self.selected_agent_id)

    def is_tmux_direct_enabled(self, agent_id: str | None = None) -> bool:
        if not self.tmux_features_available:
            return False
        resolved_agent_id = agent_id or self.selected_agent_id
        if resolved_agent_id and resolved_agent_id in self.tmux_direct_agent_modes:
            return self.tmux_direct_agent_modes[resolved_agent_id]
        return self.tmux_direct_enabled

    def set_agent_tmux_direct_enabled(self, agent_id: str, enabled: bool) -> bool:
        if enabled and not self.tmux_features_available:
            self.notify(
                "Tmux direct is unavailable in this terminal. Start inside tmux "
                "or set AGENT_PBX_TUI_TMUX_SHOW=1 on a host with tmux.",
                severity="warning",
            )
            enabled = False
        self.tmux_direct_agent_modes[agent_id] = enabled
        if enabled:
            self.tmux_detached_agent_ids.discard(agent_id)
        self.apply_tmux_class()
        self.update_agent_title()
        self.render_agents()
        self.save_settings()
        self.notify(
            f"Tmux direct {'enabled' if enabled else 'disabled'} for {agent_id}."
        )
        return enabled

    def set_tmux_direct_enabled(self, enabled: bool) -> bool:
        if enabled and not self.tmux_features_available:
            self.notify(
                "Tmux direct is unavailable in this terminal. Start inside tmux "
                "or set AGENT_PBX_TUI_TMUX_SHOW=1 on a host with tmux.",
                severity="warning",
            )
            enabled = False
        self.tmux_direct_enabled = enabled
        if enabled:
            self.tmux_detached_agent_ids = {
                agent_id
                for agent_id in self.tmux_detached_agent_ids
                if self.tmux_direct_agent_modes.get(agent_id) is False
            }
        self.apply_tmux_class()
        self.update_agent_title()
        self.save_settings()
        return enabled

    def query_one_or_none(
        self, selector: str, widget_type: type[WidgetType]
    ) -> WidgetType | None:
        try:
            return self.query_one(selector, widget_type)
        except NoMatches:
            return None

    def compute_effective_layout(
        self, width: int | None = None, height: int | None = None
    ) -> str:
        if width is None or height is None:
            try:
                width = self.size.width
                height = self.size.height
            except Exception:
                width = SPLIT_MIN_WIDTH
                height = SPLIT_MIN_HEIGHT
        if self.layout_mode == SPLIT_TUI_LAYOUT:
            return SPLIT_TUI_LAYOUT
        if self.layout_mode == TINY_TUI_LAYOUT:
            return TINY_TUI_LAYOUT
        if width < COMPACT_MIN_WIDTH or height < COMPACT_MIN_HEIGHT:
            return TINY_TUI_LAYOUT
        if self.layout_mode == COMPACT_TUI_LAYOUT:
            return COMPACT_TUI_LAYOUT
        if width >= SPLIT_MIN_WIDTH and height >= SPLIT_MIN_HEIGHT:
            return SPLIT_TUI_LAYOUT
        return COMPACT_TUI_LAYOUT

    def update_effective_layout(
        self, width: int | None = None, height: int | None = None
    ) -> bool:
        previous = self.effective_layout_mode
        self.effective_layout_mode = self.compute_effective_layout(width, height)
        if self.effective_layout_mode == SPLIT_TUI_LAYOUT:
            self.compact_view = "home"
            self.set_tiny_home_panel(TINY_HOME_AGENTS, apply=False)
        changed = previous != self.effective_layout_mode
        return changed

    def is_collapsed_layout(self) -> bool:
        return self.effective_layout_mode in {COMPACT_TUI_LAYOUT, TINY_TUI_LAYOUT}

    def is_tiny_layout(self) -> bool:
        return self.effective_layout_mode == TINY_TUI_LAYOUT

    def desired_agent_columns(self) -> tuple[str, ...]:
        live = ("Live",) if self.tmux_features_available else ()
        if self.effective_layout_mode == TINY_TUI_LAYOUT:
            return (
                STARRED_AGENT_COLUMN,
                "New",
                "Agent",
                "Status",
                "Project",
                "Queue",
                "Camp",
                *live,
                "Hidden",
            )
        if self.effective_layout_mode == COMPACT_TUI_LAYOUT:
            return (
                STARRED_AGENT_COLUMN,
                "New",
                "Agent",
                "Plan",
                "Status",
                "Queue",
                "Camp",
                *live,
                "Poll",
                "Hidden",
            )
        return (
            STARRED_AGENT_COLUMN,
            "New",
            "Agent",
            "Type",
            "PBX",
            "Plan",
            "Status",
            "Project",
            "Camp",
            "Last Seen",
            "Queue",
            *live,
            "Poll",
            "Use",
            "Hidden",
        )

    def desired_operator_columns(self) -> tuple[str, ...]:
        live = ("Live",) if self.tmux_features_available else ()
        if self.effective_layout_mode == TINY_TUI_LAYOUT:
            return (
                STARRED_AGENT_COLUMN,
                "New",
                "Operator",
                "Status",
                "Camp",
                *live,
                "Hidden",
            )
        if self.effective_layout_mode == COMPACT_TUI_LAYOUT:
            return (
                STARRED_AGENT_COLUMN,
                "New",
                "Operator",
                "Status",
                "Camp",
                *live,
                "PBX",
                "Hidden",
            )
        return (
            STARRED_AGENT_COLUMN,
            "New",
            "Operator",
            "PBX",
            "Status",
            "Camp",
            *live,
            "Last Seen",
            "Hidden",
        )

    def render_agent_columns(self, table: DataTable | None = None) -> None:
        table = table or self.query_one_or_none("#agents", DataTable)
        if table is None:
            return
        columns = self.desired_agent_columns()
        if columns == self.rendered_agent_columns:
            return
        table.clear(columns=True)
        table.add_columns(*columns)
        self.rendered_agent_columns = columns

    def render_operator_columns(self, table: DataTable | None = None) -> None:
        table = table or self.query_one_or_none("#operators", DataTable)
        if table is None:
            return
        columns = self.desired_operator_columns()
        if columns == self.rendered_operator_columns:
            return
        table.clear(columns=True)
        table.add_columns(*columns)
        self.rendered_operator_columns = columns

    def agent_row_values(
        self,
        agent: dict[str, Any],
        columns: tuple[str, ...] | None = None,
    ) -> list[str]:
        agent_id = str(agent["agent_id"])
        selected_columns = columns or self.rendered_agent_columns
        values = {
            STARRED_AGENT_COLUMN: "*" if agent_id in self.starred_agent_ids else "",
            "New": "NEW" if agent_id in self.unseen_latest_agent_ids else "",
            "Agent": agent_id,
            "Operator": agent_id,
            "Type": self.format_agent_type(agent),
            "PBX": self.format_pbx_active(agent),
            "Plan": self.format_plan_state(agent),
            "Status": self.format_agent_status(agent),
            "Project": str(agent["project"]),
            "Camp": self.format_campaign_count(agent),
            "Last Seen": f"{agent['last_seen_at']:.0f}",
            "Queue": self.format_queue_state(agent),
            "Live": self.format_tmux_liveness(agent_id),
            "Poll": self.format_poll_state(agent),
            "Use": self.format_usage_state(agent),
            "Hidden": self.format_hidden_state(agent),
        }
        return [values[column] for column in selected_columns]

    def on_resize(self, event: Resize) -> None:
        restore_tmux_tail = self.should_restore_tmux_stream_tail_after_resize()
        if self.update_effective_layout(event.size.width, event.size.height):
            self.apply_layout_class()
            self.render_agents()
        else:
            self.apply_layout_dimensions()
        if restore_tmux_tail:
            self.call_after_refresh(self.snap_tmux_stream_to_bottom)

    def set_layout_mode(self, layout_name: str) -> None:
        self.layout_mode = resolve_layout(layout_name)
        self.compact_view = "home"
        self.set_tiny_home_panel(TINY_HOME_AGENTS, apply=False)
        self.update_effective_layout()
        self.apply_layout_class()
        self.render_agents()
        self.save_settings()

    def adjust_split_percent(self, delta: int) -> None:
        self.split_percent = clamp_split_percent(self.split_percent + delta)
        self.apply_layout_dimensions()
        self.save_settings()

    def reset_split_percent(self) -> None:
        self.split_percent = DEFAULT_SPLIT_PERCENT
        self.apply_layout_dimensions()
        self.save_settings()

    def toggle_tiny_events(self) -> None:
        if not (self.is_tiny_layout() and self.compact_view == "home"):
            return
        panel = TINY_HOME_AGENTS if self.tiny_show_events else TINY_HOME_EVENTS
        self.set_tiny_home_panel(panel, apply=False)
        self.apply_layout_class()

    async def refresh_agents(self) -> None:
        try:
            response = await self.api_client().get(
                "/v1/agents",
                params={"include_hidden": "true"} if self.show_hidden_agents else None,
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            agents = response.json()
        except Exception as exc:
            detail = self.query_one_or_none("#detail", TextArea)
            if detail is not None:
                detail.text = f"Unable to refresh agents: {exc}"
            return

        previous_last_seen = self.agent_last_seen_at.copy()
        self.agents = {str(agent["agent_id"]): agent for agent in agents}
        tmux_state_changed = await self.reconcile_operator_tmux_targets()
        unseen_state_changed = self.update_unseen_from_agent_refresh(previous_last_seen)
        star_state_changed = self.sync_starred_from_agent_refresh()
        signature = self.agents_render_signature()
        if (
            tmux_state_changed
            or unseen_state_changed
            or star_state_changed
            or signature != self.rendered_agents_signature
        ):
            self.render_agents(signature=signature)
        self.render_unseen_attention()

    def update_unseen_from_agent_refresh(
        self, previous_last_seen: dict[str, float]
    ) -> bool:
        previous_unseen = set(self.unseen_latest_agent_ids)
        viewed_changed = False
        self.unseen_latest_agent_ids.intersection_update(self.agents)
        for agent_id, agent in self.agents.items():
            current_report_at = self.latest_report_timestamp(agent_id)
            if current_report_at is None:
                continue
            previous = previous_last_seen.get(agent_id)
            latest_viewed_at = self.latest_viewed_at_by_agent.get(agent_id)
            shared_seen_at = self.shared_latest_seen_at(agent_id)
            if shared_seen_at is not None and current_report_at <= shared_seen_at:
                if (
                    latest_viewed_at is None
                    or latest_viewed_at < shared_seen_at
                ):
                    self.latest_viewed_at_by_agent[agent_id] = shared_seen_at
                    viewed_changed = True
                self.unseen_latest_agent_ids.discard(agent_id)
            elif latest_viewed_at is not None and current_report_at <= latest_viewed_at:
                self.unseen_latest_agent_ids.discard(agent_id)
            elif previous is None:
                if self.is_latest_engaged(agent_id):
                    self.latest_viewed_at_by_agent[agent_id] = current_report_at
                    viewed_changed = viewed_changed or latest_viewed_at != current_report_at
                    self.unseen_latest_agent_ids.discard(agent_id)
                elif latest_viewed_at is None or current_report_at > latest_viewed_at:
                    self.unseen_latest_agent_ids.add(agent_id)
            elif current_report_at > previous:
                if self.is_latest_engaged(agent_id):
                    self.latest_viewed_at_by_agent[agent_id] = current_report_at
                    viewed_changed = viewed_changed or latest_viewed_at != current_report_at
                    self.unseen_latest_agent_ids.discard(agent_id)
                else:
                    self.unseen_latest_agent_ids.add(agent_id)
            self.agent_last_seen_at[agent_id] = current_report_at
        if viewed_changed:
            self.save_settings()
        return previous_unseen != self.unseen_latest_agent_ids or viewed_changed

    def agent_last_seen(self, agent_id: str) -> float | None:
        agent = self.agents.get(agent_id)
        if agent is None:
            return None
        try:
            return float(agent["last_seen_at"])
        except (KeyError, TypeError, ValueError):
            return None

    def latest_report_timestamp(self, agent_id: str) -> float | None:
        agent = self.agents.get(agent_id)
        if agent is None:
            return None
        if "latest_report_created_at" in agent:
            try:
                value = agent.get("latest_report_created_at")
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        # Older Agent PBX servers did not include latest_report_created_at.
        # Fall back to last_seen_at only for those legacy responses.
        for key in ("latest_report_created_at", "last_seen_at"):
            try:
                value = agent.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def shared_latest_seen_at(self, agent_id: str) -> float | None:
        agent = self.agents.get(agent_id)
        if agent is None:
            return None
        try:
            value = agent.get("latest_report_seen_at")
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def sync_starred_from_agent_refresh(self) -> bool:
        if not self.agents:
            return False
        if not all(
            "starred" in agent or "starred_at" in agent
            for agent in self.agents.values()
        ):
            return False
        previous = set(self.starred_agent_ids)
        remote_starred = {
            str(agent_id)
            for agent_id, agent in self.agents.items()
            if bool(agent.get("starred")) or float_value(agent.get("starred_at")) is not None
        }
        seed_starred: set[str] = set()
        if not self.remote_star_state_seen:
            self.remote_star_state_seen = True
            if not remote_starred:
                seed_starred = {
                    agent_id
                    for agent_id in self.local_starred_agent_ids
                    if agent_id in self.agents
                }
                for agent_id in sorted(seed_starred):
                    self.queue_agent_star_sync(agent_id, True)
        self.starred_agent_ids = remote_starred | seed_starred
        if self.starred_agent_ids != previous:
            self.save_settings()
            return True
        return False

    def is_latest_engaged(self, agent_id: str) -> bool:
        if self.is_compact_layout() and self.compact_view != "agent":
            return False
        return agent_id == self.selected_agent_id and self.active_agent_tab == "latest-tab"

    def ordered_agents(self, agent_type: str | None = None) -> list[dict[str, Any]]:
        agents = sorted(
            self.agents.values(),
            key=lambda agent: (
                str(agent.get("agent_id") or "") not in self.starred_agent_ids,
                -(float_value(agent.get("last_seen_at")) or 0.0),
                str(agent.get("agent_id") or ""),
            ),
        )
        if agent_type is None:
            return agents
        return [agent for agent in agents if self.agent_type(agent) == agent_type]

    def caller_agents(self) -> list[dict[str, Any]]:
        return self.ordered_agents(CALLER_AGENT_TYPE)

    def operator_agents(self) -> list[dict[str, Any]]:
        return [
            agent
            for agent in self.ordered_agents(OPERATOR_AGENT_TYPE)
            if self.operator_role(agent) != OPERATOR_ROLE_FORK
        ]

    def operator_fork_agents(self) -> list[dict[str, Any]]:
        return [
            agent
            for agent in self.ordered_agents(OPERATOR_AGENT_TYPE)
            if self.operator_role(agent) == OPERATOR_ROLE_FORK
        ]

    def logical_operator_id_for_agent(self, agent: dict[str, Any]) -> str:
        agent_id = str(agent.get("agent_id") or "").strip()
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        logical_operator_id = str(metadata.get("logical_operator_id") or "").strip()
        if logical_operator_id:
            return logical_operator_id
        if self.operator_role(agent) == OPERATOR_ROLE_FORK and "-fork-" in agent_id:
            logical_operator_id, _, _ = agent_id.partition("-fork-")
            if logical_operator_id:
                return logical_operator_id
        return agent_id

    def operator_fork_sort_key(self, fork: dict[str, Any]) -> tuple[Any, ...]:
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        track_id = str(
            metadata.get("fork_track_id") or DEFAULT_OPERATOR_FORK_TRACK_ID
        ).strip()
        purpose = str(
            metadata.get("fork_purpose") or DEFAULT_OPERATOR_FORK_PURPOSE
        ).strip()
        return (
            str(metadata.get("source_caller_agent_id") or ""),
            str(metadata.get("source_codex_session_id") or ""),
            0 if track_id == DEFAULT_OPERATOR_FORK_TRACK_ID else 1,
            purpose,
            track_id,
            -(float_value(fork.get("last_seen_at")) or 0.0),
            str(fork.get("agent_id") or ""),
        )

    def operator_forks_for_logical_operator(
        self, logical_operator_id: str
    ) -> list[dict[str, Any]]:
        return sorted(
            [
                fork
                for fork in self.operator_fork_agents()
                if self.logical_operator_id_for_agent(fork) == logical_operator_id
            ],
            key=self.operator_fork_sort_key,
        )

    def operator_table_agents(self) -> list[dict[str, Any]]:
        roots = self.operator_agents()
        forks_by_operator: dict[str, list[dict[str, Any]]] = {}
        for fork in self.operator_fork_agents():
            forks_by_operator.setdefault(
                self.logical_operator_id_for_agent(fork),
                [],
            ).append(fork)
        rows: list[dict[str, Any]] = []
        for root in roots:
            logical_operator_id = self.logical_operator_id_for_agent(root)
            rows.append(root)
            rows.extend(
                sorted(
                    forks_by_operator.pop(logical_operator_id, []),
                    key=self.operator_fork_sort_key,
                )
            )
        for logical_operator_id in sorted(forks_by_operator):
            rows.extend(
                sorted(
                    forks_by_operator[logical_operator_id],
                    key=self.operator_fork_sort_key,
                )
            )
        return rows

    def preferred_edit_operator_fork(
        self,
        forks: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not forks:
            return None

        def score(fork: dict[str, Any]) -> tuple[int, int, float, str]:
            metadata = (
                fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            )
            track_id = str(
                metadata.get("fork_track_id") or DEFAULT_OPERATOR_FORK_TRACK_ID
            ).strip()
            purpose = str(
                metadata.get("fork_purpose") or DEFAULT_OPERATOR_FORK_PURPOSE
            ).strip()
            return (
                0 if track_id == DEFAULT_OPERATOR_FORK_TRACK_ID else 1,
                0 if purpose == DEFAULT_OPERATOR_FORK_PURPOSE else 1,
                -(float_value(fork.get("last_seen_at")) or 0.0),
                str(fork.get("agent_id") or ""),
            )

        return sorted(forks, key=score)[0]

    def operator_fork_has_live_tmux_target(self, fork: dict[str, Any]) -> bool:
        fork_agent_id = str(
            fork.get("agent_id") or fork.get("fork_agent_id") or ""
        ).strip()
        if not fork_agent_id:
            return False
        if self.tmux_liveness_level(fork_agent_id) in {"active", "idle"}:
            return True
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        pane_id = str(
            self.tmux_agent_targets.get(fork_agent_id)
            or fork.get("tmux_pane_id")
            or metadata.get("tmux_pane_id")
            or ""
        ).strip()
        if not pane_id:
            return False
        for pane in self.tmux_panes:
            if pane.pane_id == pane_id or pane.target_label == pane_id:
                return self.tmux_pane_allowed_for_agent(fork, pane)
        return fork_agent_id in self.tmux_agent_targets

    def operator_fork_is_ready_for_source_inference(
        self,
        fork: dict[str, Any],
    ) -> bool:
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        pending = str(metadata.get("operator_fork_pending") or "").strip().lower()
        if pending in {"1", "true", "yes"}:
            return False
        if not bool(fork.get("pbx_active", True)):
            return False
        status = (
            str(fork.get("effective_status") or fork.get("status") or "")
            .strip()
            .lower()
        )
        if status in {"active", "online", "ready", "running", "starting", "working"}:
            return True
        return self.operator_fork_has_live_tmux_target(fork)

    def active_operator_fork_for_caller(
        self,
        caller: dict[str, Any],
        *,
        logical_operator_id: str | None = None,
    ) -> dict[str, Any] | None:
        caller_agent_id = str(caller.get("agent_id") or "").strip()
        caller_metadata = (
            caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        )
        source_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        if not caller_agent_id or not source_session_id:
            return None
        candidates: list[dict[str, Any]] = []
        for fork in self.operator_fork_agents():
            if (
                logical_operator_id
                and self.logical_operator_id_for_agent(fork) != logical_operator_id
            ):
                continue
            metadata = (
                fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            )
            if str(metadata.get("source_caller_agent_id") or "").strip() != caller_agent_id:
                continue
            if str(metadata.get("source_codex_session_id") or "").strip() != source_session_id:
                continue
            if self.operator_fork_is_ready_for_source_inference(fork):
                candidates.append(fork)
        return self.preferred_edit_operator_fork(candidates)

    def single_active_operator_fork_source_agent_id(
        self,
        logical_operator_id: str,
    ) -> str | None:
        source_agent_ids: set[str] = set()
        for fork in self.operator_forks_for_logical_operator(logical_operator_id):
            metadata = (
                fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            )
            if (
                str(metadata.get("fork_track_id") or DEFAULT_OPERATOR_FORK_TRACK_ID)
                != DEFAULT_OPERATOR_FORK_TRACK_ID
            ):
                continue
            if (
                str(metadata.get("fork_purpose") or DEFAULT_OPERATOR_FORK_PURPOSE)
                != DEFAULT_OPERATOR_FORK_PURPOSE
            ):
                continue
            if not self.operator_fork_is_ready_for_source_inference(fork):
                continue
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            if source_agent_id and source_agent_id in self.agents:
                source_agent_ids.add(source_agent_id)
        if len(source_agent_ids) == 1:
            return next(iter(source_agent_ids))
        return None

    def agents_render_signature(self) -> tuple[Any, ...]:
        return (
            self.desired_agent_columns(),
            self.desired_operator_columns(),
            tuple(sorted(self.starred_agent_ids)),
            tuple(
                (
                    str(agent["agent_id"]),
                    tuple(
                        str(value)
                        for value in self.agent_row_values(
                            agent,
                            self.desired_agent_columns(),
                        )
                    ),
                )
                for agent in self.caller_agents()
            ),
            tuple(
                (
                    str(agent["agent_id"]),
                    tuple(
                        str(value)
                        for value in self.agent_row_values(
                            agent,
                            self.desired_operator_columns(),
                        )
                    ),
                )
                for agent in self.operator_table_agents()
            ),
        )

    def render_agents(self, signature: tuple[Any, ...] | None = None) -> None:
        table = self.query_one_or_none("#agents", DataTable)
        operator_table = self.query_one_or_none("#operators", DataTable)
        if table is None or operator_table is None:
            return
        cursor_agent_id = self.agent_id_at_cursor()
        cursor_operator_id = self.operator_id_at_cursor()
        scroll_x = table.scroll_x
        scroll_target_x = table.scroll_target_x
        scroll_y = table.scroll_y
        scroll_target_y = table.scroll_target_y
        operator_scroll_x = operator_table.scroll_x
        operator_scroll_target_x = operator_table.scroll_target_x
        operator_scroll_y = operator_table.scroll_y
        operator_scroll_target_y = operator_table.scroll_target_y

        self.render_agent_columns(table)
        table.clear()
        caller_agent_ids = {str(agent["agent_id"]) for agent in self.caller_agents()}
        for agent in self.caller_agents():
            agent_id = str(agent["agent_id"])
            row = self.agent_row_values(agent, self.rendered_agent_columns)
            cells = self.style_agent_row(
                row,
                agent,
            )
            table.add_row(*cells, key=agent_id)
        restore_agent_id = cursor_agent_id if cursor_agent_id in caller_agent_ids else None
        if restore_agent_id is not None:
            table.move_cursor(
                row=table.get_row_index(restore_agent_id),
                animate=False,
                scroll=False,
            )
        table.scroll_x = scroll_x
        table.scroll_target_x = scroll_target_x
        table.scroll_y = scroll_y
        table.scroll_target_y = scroll_target_y

        self.render_operator_columns(operator_table)
        operator_table.clear()
        operator_agent_ids = {
            str(agent["agent_id"]) for agent in self.operator_table_agents()
        }
        for agent in self.operator_table_agents():
            agent_id = str(agent["agent_id"])
            row = self.agent_row_values(agent, self.rendered_operator_columns)
            cells = self.style_agent_row(row, agent)
            operator_table.add_row(*cells, key=agent_id)
        restore_operator_id = (
            cursor_operator_id if cursor_operator_id in operator_agent_ids else None
        )
        if restore_operator_id is not None:
            operator_table.move_cursor(
                row=operator_table.get_row_index(restore_operator_id),
                animate=False,
                scroll=False,
            )
        operator_table.scroll_x = operator_scroll_x
        operator_table.scroll_target_x = operator_scroll_target_x
        operator_table.scroll_y = operator_scroll_y
        operator_table.scroll_target_y = operator_scroll_target_y
        self.apply_tiny_events_visibility()
        self.rendered_agents_signature = signature or self.agents_render_signature()

    def agent_id_at_cursor(self) -> str | None:
        table = self.query_one_or_none("#agents", DataTable)
        if table is None:
            return None
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def operator_id_at_cursor(self) -> str | None:
        table = self.query_one_or_none("#operators", DataTable)
        if table is None:
            return None
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def focus_agent_row(self, agent_id: str) -> None:
        self.move_agent_cursor(agent_id, focus=True)

    def move_agent_cursor(self, agent_id: str, *, focus: bool) -> None:
        if agent_id not in self.agents:
            return
        if self.agent_type(self.agents[agent_id]) == OPERATOR_AGENT_TYPE:
            self.move_operator_cursor(agent_id, focus=focus)
            return
        table = self.query_one_or_none("#agents", DataTable)
        if table is None:
            return
        if agent_id not in {str(agent["agent_id"]) for agent in self.caller_agents()}:
            return
        table.move_cursor(
            row=table.get_row_index(agent_id),
            animate=False,
            scroll=True,
        )
        if focus:
            table.focus()

    def move_operator_cursor(self, agent_id: str, *, focus: bool) -> None:
        if agent_id not in self.agents:
            return
        table = self.query_one_or_none("#operators", DataTable)
        if table is None:
            return
        if agent_id not in {str(agent["agent_id"]) for agent in self.operator_table_agents()}:
            return
        table.move_cursor(
            row=table.get_row_index(agent_id),
            animate=False,
            scroll=True,
        )
        if focus:
            table.focus()

    def agent_id_at_row_index(self, row_index: int) -> str | None:
        table = self.query_one_or_none("#agents", DataTable)
        if table is None or not table.is_valid_row_index(row_index):
            return None
        return str(table.ordered_rows[row_index].key.value)

    async def jump_to_agent_row(self, row_index: int) -> bool:
        agent_id = self.agent_id_at_row_index(row_index)
        if agent_id is None:
            self.notify(
                f"No agent at shortcut slot {row_index + 1}.",
                severity="warning",
            )
            return False
        return await self.open_agent_latest(agent_id)

    def begin_agent_jump_prefix(self) -> None:
        self.agent_jump_prefix_generation += 1
        generation = self.agent_jump_prefix_generation
        self.agent_jump_prefix_pending = True
        self.notify("Agent jump: press 1-0.")
        self.set_timer(3.0, lambda: self.clear_agent_jump_prefix(generation))

    def clear_agent_jump_prefix(self, generation: int | None = None) -> None:
        if generation is not None and generation != self.agent_jump_prefix_generation:
            return
        self.agent_jump_prefix_pending = False

    def agent_jump_index_from_key(self, event: Key) -> int | None:
        key = str(event.character or event.key or "").lower()
        return AGENT_JUMP_KEYS.get(key)

    def handle_agent_jump_key(self, event: Key) -> bool:
        if self.agent_jump_prefix_pending:
            row_index = self.agent_jump_index_from_key(event)
            self.clear_agent_jump_prefix()
            if row_index is None:
                if event.key == "escape":
                    event.stop()
                    return True
                return False
            event.stop()
            self.run_worker(
                self.jump_to_agent_row(row_index),
                name="jump-agent",
                exclusive=True,
            )
            return True
        if event.character == "g" or event.key == "g":
            event.stop()
            self.begin_agent_jump_prefix()
            return True
        return False

    def agent_pbx_mode(self, agent: dict[str, Any]) -> str:
        if not bool(agent.get("pbx_active", True)):
            return "off"
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        mode = str(metadata.get("pbx_mode") or PBX_REPORT_MODE).strip().lower()
        if mode == PBX_NOHUP_MODE:
            return PBX_NOHUP_MODE
        return PBX_REPORT_MODE

    def agent_requires_polling(self, agent: dict[str, Any]) -> bool:
        return self.agent_pbx_mode(agent) == PBX_NOHUP_MODE

    def format_pbx_active(self, agent: dict[str, Any]) -> str:
        return self.agent_pbx_mode(agent)

    def agent_type(self, agent: dict[str, Any]) -> str:
        value = str(agent.get("agent_type") or "").strip().lower()
        if not value:
            metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            value = str(metadata.get("agent_type") or "").strip().lower()
        return OPERATOR_AGENT_TYPE if value == OPERATOR_AGENT_TYPE else CALLER_AGENT_TYPE

    def format_agent_type(self, agent: dict[str, Any]) -> str:
        return "op" if self.agent_type(agent) == OPERATOR_AGENT_TYPE else "call"

    def operator_role(self, agent: dict[str, Any]) -> str:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        role = str(metadata.get("operator_role") or "").strip().lower()
        if role == OPERATOR_ROLE_FORK:
            return OPERATOR_ROLE_FORK
        agent_id = str(agent.get("agent_id") or "").strip()
        has_fork_identity = bool(
            str(metadata.get("logical_operator_id") or "").strip()
            and str(metadata.get("source_caller_agent_id") or "").strip()
            and str(metadata.get("source_codex_session_id") or "").strip()
        )
        if self.agent_type(agent) == OPERATOR_AGENT_TYPE and (
            has_fork_identity or "-fork-" in agent_id
        ):
            return OPERATOR_ROLE_FORK
        return OPERATOR_ROLE_ROOT

    def format_campaign_count(self, agent: dict[str, Any]) -> str:
        count = int_value(agent.get("active_campaign_count")) or 0
        return str(count) if count else ""

    def is_hidden_agent(self, agent: dict[str, Any]) -> bool:
        return float_value(agent.get("dismissed_at")) is not None

    def format_hidden_state(self, agent: dict[str, Any]) -> str:
        return "hidden" if self.is_hidden_agent(agent) else ""

    def format_agent_status(self, agent: dict[str, Any]) -> str:
        status = str(agent.get("effective_status") or agent.get("status") or "")
        if (
            self.agent_type(agent) == CALLER_AGENT_TYPE
            and status.strip().lower() == "blocked"
            and self.active_operator_fork_for_caller(agent) is not None
        ):
            return "blocked/fork-ready"
        if self.infer_tmux_working_status(agent, status):
            return "tmux-working"
        return status

    def infer_tmux_working_status(self, agent: dict[str, Any], status: str) -> bool:
        agent_id = str(agent.get("agent_id") or "")
        if not agent_id or not self.tmux_features_available:
            return False
        if not self.is_tmux_direct_enabled(agent_id):
            return False
        if status.strip().lower() not in TMUX_WORKING_INFERABLE_STATUSES:
            return False
        return self.tmux_liveness_level(agent_id) == "active"

    def command_delivery_note(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if agent is not None and not self.agent_requires_polling(agent):
            return (
                "This PBX queue action requires Agent PBX nohup mode. "
                "Report-mode agents will not pick it up; use tmux direct mode "
                "to interact locally without polling, or explicitly restart "
                "the agent with Agent PBX nohup."
            )
        return "Waiting for the agent to poll this command and publish a new report."

    def command_delivery_requires_nohup(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return agent is not None and not self.agent_requires_polling(agent)

    def record_tmux_liveness_state(
        self,
        agent_id: str,
        state: str,
        *,
        pane_id: str | None = None,
    ) -> None:
        entry = self.tmux_liveness_by_agent.setdefault(agent_id, TmuxLiveness())
        entry.state = state
        entry.pane_id = pane_id
        entry.last_capture_at = time.time()

    def record_tmux_capture_liveness(
        self,
        agent_id: str,
        pane_id: str,
        captured: str,
    ) -> None:
        now = time.time()
        capture_hash = hashlib.sha256(
            captured.encode("utf-8", errors="replace")
        ).hexdigest()
        entry = self.tmux_liveness_by_agent.setdefault(agent_id, TmuxLiveness())
        if entry.pane_id != pane_id or entry.capture_hash != capture_hash:
            entry.last_changed_at = now
        entry.pane_id = pane_id
        entry.capture_hash = capture_hash
        entry.last_capture_at = now
        entry.state = "captured"

    def tmux_liveness_level(self, agent_id: str) -> str:
        entry = self.tmux_liveness_by_agent.get(agent_id)
        if entry is None:
            return "unknown"
        if entry.state == "stale":
            return "stale"
        if entry.state != "captured":
            return "unknown"
        if entry.last_changed_at is None:
            return "unknown"
        age = max(0.0, time.time() - entry.last_changed_at)
        if age < TMUX_LIVENESS_IDLE_SECONDS:
            return "active"
        return "idle"

    def format_tmux_liveness(self, agent_id: str) -> str:
        if not self.is_tmux_direct_enabled(agent_id):
            return "pbx"
        entry = self.tmux_liveness_by_agent.get(agent_id)
        if entry is None:
            return "tmux"
        if entry.state == "stale":
            return "stale"
        if entry.state != "captured":
            return "-"
        if entry.last_changed_at is None:
            return "-"
        age = max(0.0, time.time() - entry.last_changed_at)
        if age < TMUX_LIVENESS_IDLE_SECONDS:
            return "active"
        return f"idle {format_duration(age)}"

    def notify_queued_command(
        self, agent_id: str, label: str, command: dict[str, Any]
    ) -> None:
        suffix = (
            " Requires nohup polling."
            if self.command_delivery_requires_nohup(agent_id)
            else ""
        )
        severity = "warning" if suffix else "information"
        self.notify(
            f"{label} queued for {agent_id}: {command['command_id']}.{suffix}",
            severity=severity,
        )

    def format_plan_state(self, agent: dict[str, Any]) -> str:
        agent_id = str(agent.get("agent_id") or "")
        if agent_id in self.tmux_plan_selector_agent_ids:
            return "SELECT"
        option_count = int_value(agent.get("latest_report_plan_option_count")) or 0
        if option_count > 0:
            return f"PLAN:{option_count}"
        if bool(agent.get("latest_report_needs_input")):
            return "INPUT"
        latest_status = str(agent.get("latest_report_status") or "").strip().lower()
        if latest_status in {"plan", "planning", "needs_input", "waiting"}:
            return latest_status.upper()
        return ""

    def format_queue_state(self, agent: dict[str, Any]) -> str:
        try:
            queued_count = int(agent.get("queued_command_count") or 0)
        except (TypeError, ValueError):
            queued_count = 0
        if queued_count <= 0:
            return ""
        age = float_value(agent.get("oldest_queued_command_age_seconds"))
        if age is None:
            return str(queued_count)
        prefix = "!" if age >= QUEUED_COMMAND_WARN_SECONDS else ""
        return f"{prefix}{queued_count} {format_duration(age)}"

    def format_poll_state(self, agent: dict[str, Any]) -> str:
        last_poll_at = float_value(agent.get("last_poll_at"))
        try:
            queued_count = int(agent.get("queued_command_count") or 0)
        except (TypeError, ValueError):
            queued_count = 0
        if last_poll_at is None:
            if self.agent_requires_polling(agent):
                return "never" if queued_count else "-"
            return "-"
        age = max(0.0, time.time() - last_poll_at)
        if age <= ACTIVE_POLL_SECONDS:
            return "active"
        label = f"{format_duration(age)} ago"
        if self.agent_requires_polling(agent) and queued_count and age >= STALE_POLL_SECONDS:
            return f"stale {label}"
        return label

    def agent_poll_level(self, agent: dict[str, Any]) -> str:
        last_poll_at = float_value(agent.get("last_poll_at"))
        try:
            queued_count = int(agent.get("queued_command_count") or 0)
        except (TypeError, ValueError):
            queued_count = 0
        if last_poll_at is None:
            if self.agent_requires_polling(agent):
                return "never" if queued_count else "idle"
            return "idle"
        age = max(0.0, time.time() - last_poll_at)
        if age <= ACTIVE_POLL_SECONDS:
            return "active"
        if self.agent_requires_polling(agent) and queued_count and age >= STALE_POLL_SECONDS:
            return "stale"
        return "idle"

    def style_agent_row(
        self, cells: list[str], agent: dict[str, Any]
    ) -> list[str | Text]:
        if self.is_hidden_agent(agent):
            style = "dim yellow"
        elif bool(agent.get("status_stale")):
            style = "bold yellow"
        else:
            level = self.agent_poll_level(agent)
            style = {
                "stale": "bold yellow",
                "never": "bold red",
            }.get(level)
            agent_id = str(agent.get("agent_id") or "")
            if (
                style is None
                and self.tmux_features_available
                and self.is_tmux_direct_enabled(agent_id)
            ):
                tmux_level = self.tmux_liveness_level(agent_id)
                style = {
                    "active": "bold cyan",
                    "idle": "dim",
                    "stale": "bold yellow",
                }.get(tmux_level)
            if style is None and level == "active":
                style = "bold green"
            if style is None and self.agent_type(agent) == OPERATOR_AGENT_TYPE:
                style = "bold magenta"
        if style is None:
            return cells
        return [Text(cell, style=style) for cell in cells]

    def format_usage_state(self, agent: dict[str, Any]) -> str:
        tokens = int_value(agent.get("estimated_visible_tokens_per_hour")) or 0
        polls = int_value(agent.get("polls_per_hour")) or 0
        reports = int_value(agent.get("reports_per_hour")) or 0
        pings = int_value(agent.get("pings_per_hour")) or 0
        if not any([tokens, polls, reports, pings]):
            return "-"
        prefix = "!" if agent.get("usage_warning") else ""
        return f"{prefix}{format_count(tokens)}t p{polls} r{reports} g{pings}"

    def toggle_unseen_attention(self) -> None:
        has_attention_target = bool(
            self.unseen_latest_agent_ids or self.tmux_plan_selector_agent_ids
        )
        if not self.agent_blink_enabled or not has_attention_target:
            if self.attention_blink_phase:
                self.attention_blink_phase = False
                self.render_unseen_attention()
            return
        self.attention_blink_phase = not self.attention_blink_phase
        self.render_unseen_attention()

    def render_unseen_attention(self) -> None:
        attention = self.query_one_or_none("#attention", Static)
        if attention is None:
            return
        if self.tmux_plan_selector_agent_ids:
            agent_ids = sorted(self.tmux_plan_selector_agent_ids)
            self.attention_agent_id = agent_ids[0]
            agents = ", ".join(agent_ids[:3])
            extra = len(agent_ids) - 3
            if extra > 0:
                agents = f"{agents}, +{extra}"
            marker = "PLAN!" if self.attention_blink_phase else "PLAN"
            attention.update(
                f"{marker} selection pending: {agents} "
                "(/plan:1 start, /plan:2 clear context & start, /plan:3 stay)"
            )
            attention.add_class("unseen-active")
            self.set_attention_flash_class(
                self.agent_blink_enabled and self.attention_blink_phase
            )
            return
        if not self.agent_blink_enabled or not self.unseen_latest_agent_ids:
            if attention.has_class("unseen-active"):
                attention.update("")
                attention.remove_class("unseen-active")
                if not attention.has_class("attention-active"):
                    self.attention_agent_id = None
            self.set_attention_flash_class(False)
            return
        unseen_agent_ids = sorted(self.unseen_latest_agent_ids)
        self.attention_agent_id = unseen_agent_ids[0]
        agents = ", ".join(unseen_agent_ids[:3])
        extra = len(self.unseen_latest_agent_ids) - 3
        if extra > 0:
            agents = f"{agents}, +{extra}"
        marker = "!!!" if self.attention_blink_phase else "NEW"
        attention.update(f"{marker} unseen latest report: {agents}")
        attention.add_class("unseen-active")
        self.set_attention_flash_class(self.attention_blink_phase)

    def set_attention_flash_class(self, enabled: bool) -> None:
        try:
            self.screen.set_class(enabled, "attention-flash")
        except ScreenStackError:
            return

    def attention_target_agent_id(self) -> str | None:
        for agent_id in sorted(self.tmux_plan_selector_agent_ids):
            if agent_id in self.agents:
                return agent_id
        if self.attention_agent_id in self.agents:
            return self.attention_agent_id
        for agent_id in sorted(self.unseen_latest_agent_ids):
            if agent_id in self.agents:
                return agent_id
        if self.selected_agent_id in self.agents:
            return self.selected_agent_id
        return None

    async def open_attention_latest(self) -> bool:
        agent_id = self.attention_target_agent_id()
        if agent_id is None:
            return False
        return await self.open_agent_latest(agent_id)

    async def open_agent_latest(self, agent_id: str) -> bool:
        if agent_id not in self.agents:
            return False
        self.activate_latest_tab()
        if self.is_compact_layout():
            self.move_agent_cursor(agent_id, focus=False)
        await self.select_agent(agent_id)
        selected_tab = self.active_agent_tab
        self.activate_latest_tab()
        if selected_tab != "latest-tab":
            if self.is_tmux_direct_enabled(agent_id):
                await self.load_tmux_capture(agent_id)
            else:
                await self.load_latest_report(agent_id)
        self.mark_latest_seen(agent_id)
        if self.is_compact_layout():
            detail = self.query_one_or_none("#detail", TextArea)
            if detail is not None:
                detail.focus()
        else:
            self.focus_agent_row(agent_id)
        return True

    async def refresh_events(self) -> None:
        try:
            response = await self.api_client().get(
                "/v1/events",
                params={"tail": "true", "limit": 50},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            self.events = response.json()[-50:]
        except Exception:
            return
        previous_last_seen_event_id = self.last_seen_event_id
        if self.events:
            self.last_seen_event_id = max(int(event["event_id"]) for event in self.events)
        if self.last_seen_event_id != previous_last_seen_event_id:
            self.save_settings()
        self.render_events()

    def selected_agent_detail_visible(self) -> bool:
        if not self.selected_agent_id:
            return False
        return not (self.is_collapsed_layout() and self.compact_view != "agent")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id in {"agents", "operators"}:
            await self.select_agent(str(event.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.row_key.value))
            return
        if event.data_table.id == "files":
            await self.select_file_entry(str(event.row_key.value))
            return
        if event.data_table.id == "pull-requests":
            await self.select_pull_request(str(event.row_key.value))
            return
        if event.data_table.id == "issues":
            await self.select_issue(str(event.row_key.value))
            return
        if event.data_table.id == "campaigns":
            self.select_campaign(str(event.row_key.value))
            return
        if event.data_table.id == "joplin-notes":
            await self.select_joplin_note(str(event.row_key.value))
            return
        if event.data_table.id == "plan-options":
            self.select_plan_option(str(event.row_key.value))
            return
        if event.data_table.id == "latest-plan-options":
            self.select_latest_plan_option(str(event.row_key.value))
            return

    async def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id in {"agents", "operators"}:
            await self.select_agent(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "files":
            await self.select_file_entry(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "pull-requests":
            await self.select_pull_request(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "issues":
            await self.select_issue(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "campaigns":
            self.select_campaign(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "joplin-notes":
            await self.select_joplin_note(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "plan-options":
            self.select_plan_option(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "latest-plan-options":
            self.select_latest_plan_option(str(event.cell_key.row_key.value))

    def on_key(self, event: Key) -> None:
        thread = self.query_one_or_none("#thread", DataTable)
        try:
            focused = self.focused
        except ScreenStackError:
            focused = None
        if self.handle_joplin_shortcut_key(event, focused=focused):
            return
        if event.key == "space" and thread is not None and focused is thread:
            event.stop()
            self.toggle_current_thread_mark()
            return
        if isinstance(focused, (Input, FollowUpTextArea)):
            self.clear_agent_jump_prefix()
            return
        if self.handle_agent_jump_key(event):
            return
        if event.character == "[":
            event.stop()
            self.adjust_split_percent(-SPLIT_PERCENT_STEP)
            return
        if event.character == "]":
            event.stop()
            self.adjust_split_percent(SPLIT_PERCENT_STEP)
            return
        if event.character == "0":
            event.stop()
            self.reset_split_percent()
            return
        if self.handle_focus_shortcut_key(event):
            return

    def handle_joplin_shortcut_key(
        self,
        event: Key,
        *,
        focused: Widget | None = None,
    ) -> bool:
        if self.active_agent_tab != "joplin-tab":
            self.joplin_shortcut_pending = False
            return False
        key_names = normalized_key_names(event)
        key = str(event.character or event.key or "").lower()
        editable_focus = isinstance(focused, (Input, TextArea)) and not bool(
            getattr(focused, "read_only", False)
        )
        starts_prefix = "ctrl+g" in key_names or "c-g" in key_names
        if not starts_prefix and key == "j" and not editable_focus:
            starts_prefix = True
        if starts_prefix:
            event.stop()
            event.prevent_default()
            self.joplin_shortcut_pending = True
            self.notify(JOPLIN_SHORTCUT_HINT)
            return True
        if not self.joplin_shortcut_pending:
            return False
        event.stop()
        event.prevent_default()
        self.joplin_shortcut_pending = False
        if event.key == "escape":
            self.notify("Joplin shortcut canceled.")
            return True
        action = JOPLIN_SHORTCUT_ACTIONS.get(key)
        if action is None:
            self.notify(f"Unknown Joplin shortcut {key!r}.", severity="warning")
            return True
        agent_id = self.joplin_target_agent_id(focused=focused)
        if not agent_id:
            self.notify("Select an agent first.", severity="warning")
            return True
        action_name, label = action
        self.run_worker(
            self.joplin_action_for_agent(agent_id, action_name),
            name=f"joplin-shortcut-{slugify(action_name)}",
            exclusive=True,
        )
        self.notify(f"Joplin {label} requested for {agent_id}.")
        return True

    def handle_focus_shortcut_key(self, event: Key) -> bool:
        key = str(event.character or event.key or "").lower()
        if key == "a":
            event.stop()
            event.prevent_default()
            self.action_focus_agents()
            return True
        if key == "o":
            event.stop()
            event.prevent_default()
            self.action_focus_operators()
            return True
        if key == "e":
            event.stop()
            event.prevent_default()
            self.action_focus_events()
            return True
        if key == "v":
            event.stop()
            event.prevent_default()
            self.run_worker(
                self.action_focus_right_pane(),
                name="key-focus-right-pane",
                exclusive=True,
            )
            return True
        if key == "i":
            event.stop()
            event.prevent_default()
            self.run_worker(
                self.action_focus_latest_input(),
                name="key-focus-latest-input",
                exclusive=True,
            )
            return True
        return False

    def on_mouse_down(self, event: MouseDown) -> None:
        target = self.mouse_focus_target(event)
        self.log_mouse_event("mouse_down", event, target)
        if target is None or event.button not in {0, 1}:
            return
        try:
            target.focus()
        except Exception:
            return

    async def on_click(self, event: Click) -> None:
        self.log_mouse_event("click", event, self.mouse_focus_target(event))
        if getattr(event.widget, "id", None) == "attention":
            opened = await self.open_attention_latest()
            if opened:
                event.stop()

    def mouse_focus_target(self, event: MouseDown | Click) -> Widget | None:
        widget = event.widget
        if widget is None:
            return None
        for candidate in self.widget_ancestry(widget):
            candidate_id = getattr(candidate, "id", None)
            if isinstance(
                candidate,
                (Button, Checkbox, DataTable, Input, RichLog, Select, TextArea),
            ):
                return candidate
            if candidate_id in MOUSE_FOCUS_TARGET_IDS and getattr(
                candidate, "can_focus", False
            ):
                return candidate
            selector = self.mouse_focus_container_selector(candidate_id)
            if selector is not None:
                target = self.query_one_or_none(selector, Widget)
                if target is not None:
                    return target
        return None

    def widget_ancestry(self, widget: Widget) -> Iterable[Widget]:
        current: Any = widget
        while isinstance(current, Widget):
            yield current
            current = getattr(current, "parent", None)

    def mouse_focus_container_selector(self, widget_id: str | None) -> str | None:
        if widget_id == "latest-tab" and self.is_tmux_direct_enabled():
            return "#tmux-stream"
        return MOUSE_FOCUS_CONTAINER_TARGETS.get(widget_id or "")

    def log_mouse_event(
        self,
        event_name: str,
        event: MouseDown | Click,
        target: Widget | None,
    ) -> None:
        if not self.mouse_debug_enabled:
            return
        widget_id = getattr(event.widget, "id", None) or type(event.widget).__name__
        target_id = getattr(target, "id", None) if target is not None else None
        self.log(
            f"{event_name} widget={widget_id} target={target_id} "
            f"button={event.button} x={event.x} y={event.y} "
            f"screen_x={event.screen_x} screen_y={event.screen_y}"
        )

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self.selected_agent_id and event.text_area.id == "message":
            self.message_draft_by_agent[self.selected_agent_id] = event.text_area.text
        if event.text_area.id == "message":
            self.resize_message_input()
        if self.selected_agent_id and event.text_area.id == "tmux-message":
            self.tmux_message_draft_by_agent[self.selected_agent_id] = (
                event.text_area.text
            )

    def set_agent_draft_text(
        self,
        agent_id: str,
        text_area: TextArea,
        text: str,
    ) -> None:
        text_area.text = text
        if text_area.id == "message":
            self.message_draft_by_agent[agent_id] = text
            self.resize_message_input()
        elif text_area.id == "tmux-message":
            self.tmux_message_draft_by_agent[agent_id] = text

    def sent_history_agent_id(self, text_area: TextArea) -> str | None:
        if text_area.id == "tmux-message":
            agent_id = self.selected_agent_id
            if agent_id:
                return agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None and agent_input.value.strip():
            return agent_input.value.strip()
        return self.selected_agent_id

    def record_sent_message(self, agent_id: str, message: str) -> None:
        if not message.strip():
            return
        history = self.sent_message_history_by_agent.setdefault(agent_id, [])
        if not history or history[-1] != message:
            history.append(message)
        if len(history) > SENT_MESSAGE_HISTORY_LIMIT:
            del history[: len(history) - SENT_MESSAGE_HISTORY_LIMIT]
        self.sent_message_history_cursor = {
            key: value
            for key, value in self.sent_message_history_cursor.items()
            if key[0] != agent_id
        }

    def recall_sent_message(self, text_area: TextArea, *, direction: int) -> bool:
        agent_id = self.sent_history_agent_id(text_area)
        if not agent_id:
            return False
        history = self.sent_message_history_by_agent.get(agent_id) or []
        if not history:
            return False
        input_id = text_area.id or "message"
        key = (agent_id, input_id)
        current = self.sent_message_history_cursor.get(key)
        current_text = text_area.text
        if current is None:
            if current_text.strip():
                return False
            next_index = len(history) - 1
        elif 0 <= current < len(history) and current_text == history[current]:
            next_index = current + direction
            if next_index >= len(history):
                self.set_agent_draft_text(agent_id, text_area, "")
                self.sent_message_history_cursor.pop(key, None)
                return True
            next_index = max(0, next_index)
        elif current_text.strip():
            return False
        else:
            next_index = len(history) - 1
        text_area.text = history[next_index]
        last_line = text_area.text.split("\n")[-1]
        text_area.move_cursor((text_area.text.count("\n"), len(last_line)))
        self.sent_message_history_cursor[key] = next_index
        if text_area.id == "message":
            self.resize_message_input()
        return True

    def slash_command_entries(self) -> list[SystemCommand]:
        entries: list[SystemCommand] = []
        seen: set[str] = set()
        for command in self.get_system_commands(self.screen):
            title = command.title.strip()
            key = title.lower()
            if not title.startswith("/") or key in seen:
                continue
            seen.add(key)
            entries.append(command)
        return entries

    def slash_command_titles(self) -> list[str]:
        return [command.title for command in self.slash_command_entries()]

    def slash_command_for_text(self, text: str) -> SystemCommand | None:
        stripped = text.strip()
        if not stripped or "\n" in stripped:
            return None
        for command in self.slash_command_entries():
            if command.title.lower() == stripped.lower():
                return command
        return None

    def file_completion_context(
        self,
        text_area: TextArea,
    ) -> FileCompletionContext | None:
        input_id = text_area.id or "message"
        row, column = text_area.cursor_location
        lines = text_area.text.split("\n")
        if row < 0 or row >= len(lines):
            return None
        line = lines[row]
        column = min(max(0, column), len(line))
        start_col = line.rfind("@", 0, column)
        if start_col < 0:
            return None
        before = line[start_col:column]
        if any(char.isspace() for char in before):
            return None
        if start_col > 0 and not line[start_col - 1].isspace():
            if line[start_col - 1] not in "([{'\"`":
                return None
        end_col = column
        while end_col < len(line) and not line[end_col].isspace():
            end_col += 1
        prefix = line[start_col:column]
        split = split_file_completion_prefix(prefix[1:])
        if split is None:
            return None
        directory, name_prefix = split
        return FileCompletionContext(
            input_id=input_id,
            line=row,
            start_col=start_col,
            end_col=end_col,
            prefix=prefix,
            directory=directory,
            name_prefix=name_prefix,
        )

    def file_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        entries = self.file_directory_entries_by_agent.get(agent_id, {}).get(
            context.directory
        )
        if not entries:
            return ()
        prefix_key = context.name_prefix.lower()
        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            if not name or name == ".." or not name.lower().startswith(prefix_key):
                continue
            path = normalize_project_path(str(entry.get("path") or name))
            if path is None:
                continue
            is_directory = entry.get("kind") == "directory"
            completion = f"@{path}{'/' if is_directory else ''}"
            if completion in seen:
                continue
            seen.add(completion)
            candidates.append((0 if is_directory else 1, completion))
        candidates.sort(key=lambda item: (item[0], item[1].lower()))
        return tuple(completion for _kind, completion in candidates)

    def joplin_note_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        notes = self.joplin_notes_by_agent.get(cache_agent_id, {})
        if not notes:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in joplin_note_ref_completion_tokens(notes.values())
            if token.lower().startswith(prefix_key)
        )

    def caller_agent_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        if not self.is_operator_agent_id(agent_id):
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in caller_agent_ref_completion_tokens(self.caller_agents())
            if token.lower().startswith(prefix_key)
        )

    def pull_request_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        pulls = self.pull_requests_by_agent.get(repo_agent_id) or self.pull_requests_by_agent.get(
            agent_id,
            {},
        )
        if not pulls:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in pull_request_ref_completion_tokens(pulls.values())
            if token.lower().startswith(prefix_key)
        )

    def issue_completion_matches(
        self,
        context: FileCompletionContext,
        agent_id: str,
    ) -> tuple[str, ...]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        issues = self.issues_by_agent.get(repo_agent_id) or self.issues_by_agent.get(
            agent_id,
            {},
        )
        if not issues:
            return ()
        prefix_key = context.prefix.lower()
        return tuple(
            token
            for token in issue_ref_completion_tokens(issues.values())
            if token.lower().startswith(prefix_key)
        )

    def project_for_agent(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if isinstance(agent, dict) and agent.get("project"):
            return str(agent["project"])
        return agent_id

    def joplin_scope_agent_id(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if (
            isinstance(agent, dict)
            and self.agent_type(agent) == OPERATOR_AGENT_TYPE
            and self.operator_role(agent) == OPERATOR_ROLE_FORK
        ):
            metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            if source_agent_id:
                return source_agent_id
        return agent_id

    def joplin_note_cache_agent_id(self, agent_id: str) -> str:
        return self.joplin_scope_agent_id(agent_id)

    def joplin_project_for_agent(self, agent_id: str) -> str:
        return self.project_for_agent(self.joplin_scope_agent_id(agent_id))

    def repo_scope_agent_id(self, agent_id: str) -> str:
        agent = self.agents.get(agent_id)
        if not isinstance(agent, dict) or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        if self.operator_role(agent) == OPERATOR_ROLE_FORK:
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            if source_agent_id:
                return source_agent_id
        source_agent_id = str(
            metadata.get("default_source_caller_agent_id")
            or metadata.get("source_caller_agent_id")
            or ""
        ).strip()
        if source_agent_id:
            return source_agent_id
        inferred_source_agent_id = self.single_active_operator_fork_source_agent_id(
            self.logical_operator_id_for_agent(agent)
        )
        if inferred_source_agent_id:
            return inferred_source_agent_id
        return agent_id

    def default_github_reference_scope_agent_id(self, agent_id: str) -> str | None:
        agent = self.agents.get(agent_id)
        if not isinstance(agent, dict) or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        if repo_agent_id and repo_agent_id != agent_id:
            return repo_agent_id
        return None

    def project_joplin_notes_url(self, project: str, note_id: str | None = None) -> str:
        base = f"/v1/projects/{quote(project, safe='')}/joplin/notes"
        if note_id is None:
            return base
        return f"{base}/{quote(note_id, safe='')}"

    def text_before_completion_context(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
    ) -> str:
        lines = text_area.text.split("\n")
        if context.line < 0 or context.line >= len(lines):
            return ""
        prefix_lines = lines[: context.line]
        prefix_lines.append(lines[context.line][: context.start_col])
        return "\n".join(prefix_lines)

    def caller_scope_agent_id_before_context(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
    ) -> str | None:
        token = None
        before = self.text_before_completion_context(text_area, context)
        for match in CALLER_AGENT_REF_PATTERN.finditer(before):
            token = match.group(1)
        if token is None:
            return None
        return self.resolve_caller_agent_ref_token(token)

    def joplin_completion_agent_id(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
        default_agent_id: str,
    ) -> str:
        if not self.is_operator_agent_id(default_agent_id):
            return default_agent_id
        return (
            self.caller_scope_agent_id_before_context(text_area, context)
            or default_agent_id
        )

    def github_completion_agent_id(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
        default_agent_id: str,
    ) -> str:
        if not self.is_operator_agent_id(default_agent_id):
            return default_agent_id
        return (
            self.caller_scope_agent_id_before_context(text_area, context)
            or self.default_github_reference_scope_agent_id(default_agent_id)
            or default_agent_id
        )

    def file_completion_index(
        self,
        context: FileCompletionContext,
        matches: tuple[str, ...],
        *,
        direction: int,
    ) -> int:
        state = self.file_completion_state.get(context.input_id)
        if (
            state is not None
            and state.line == context.line
            and state.start_col == context.start_col
            and state.matches == matches
            and 0 <= state.index < len(matches)
            and context.prefix.lower() == matches[state.index].lower()
        ):
            return (state.index + direction) % len(matches)
        exact_index = next(
            (
                index
                for index, title in enumerate(matches)
                if title.lower() == context.prefix.lower()
            ),
            None,
        )
        if exact_index is not None:
            if len(matches) == 1:
                return exact_index
            return (exact_index + direction) % len(matches)
        return 0 if direction > 0 else len(matches) - 1

    def apply_file_completion(
        self,
        text_area: TextArea,
        context: FileCompletionContext,
        completion: str,
    ) -> None:
        lines = text_area.text.split("\n")
        line = lines[context.line]
        lines[context.line] = (
            line[: context.start_col] + completion + line[context.end_col :]
        )
        text_area.text = "\n".join(lines)
        text_area.move_cursor((context.line, context.start_col + len(completion)))
        if text_area.id == "message":
            self.resize_message_input()

    def complete_file_reference(self, text_area: TextArea, *, direction: int) -> bool:
        context = self.file_completion_context(text_area)
        if context is None:
            return False
        agent_id = self.sent_history_agent_id(text_area)
        if not agent_id:
            self.notify("Select an agent before completing @file references.", severity="warning")
            return True
        is_joplin_ref = context.prefix.lower().startswith(JOPLIN_NOTE_REF_PREFIX)
        is_caller_ref = context.prefix.lower().startswith(CALLER_AGENT_REF_PREFIX)
        is_pull_request_ref = context.prefix.lower().startswith(PULL_REQUEST_REF_PREFIX)
        is_issue_ref = context.prefix.lower().startswith(ISSUE_REF_PREFIX)
        if is_caller_ref and not self.is_operator_agent_id(agent_id):
            self.notify(
                "Select an operator before completing @caller references.",
                severity="warning",
            )
            return True
        joplin_agent_id = (
            self.joplin_completion_agent_id(text_area, context, agent_id)
            if is_joplin_ref
            else agent_id
        )
        github_agent_id = (
            self.github_completion_agent_id(text_area, context, agent_id)
            if is_pull_request_ref or is_issue_ref
            else agent_id
        )
        state = self.file_completion_state.get(context.input_id)
        if (
            state is not None
            and state.line == context.line
            and state.start_col == context.start_col
            and 0 <= state.index < len(state.matches)
            and context.prefix.lower() == state.matches[state.index].lower()
        ):
            matches = state.matches
        else:
            if is_caller_ref:
                matches = self.caller_agent_completion_matches(context, agent_id)
            elif is_joplin_ref:
                matches = self.joplin_note_completion_matches(context, joplin_agent_id)
            elif is_pull_request_ref:
                matches = self.pull_request_completion_matches(context, github_agent_id)
            elif is_issue_ref:
                matches = self.issue_completion_matches(context, github_agent_id)
            else:
                matches = self.file_completion_matches(context, agent_id)
        if not matches:
            self.file_completion_state.pop(context.input_id, None)
            if is_caller_ref:
                self.notify(
                    f"No cached caller matches {context.prefix!r}; refresh agents first.",
                    severity="warning",
                )
            elif is_joplin_ref:
                self.notify(
                    f"No cached Joplin note matches {context.prefix!r}; open or refresh the Joplin tab.",
                    severity="warning",
                )
            elif is_pull_request_ref:
                self.notify(
                    f"No cached pull request matches {context.prefix!r}; open or refresh the PRs tab.",
                    severity="warning",
                )
            elif is_issue_ref:
                self.notify(
                    f"No cached issue matches {context.prefix!r}; open or refresh the Issues tab.",
                    severity="warning",
                )
            else:
                self.notify(
                    f"No cached file matches {context.prefix!r}; open or refresh Files for {context.directory}.",
                    severity="warning",
                )
            return True
        index = self.file_completion_index(context, matches, direction=direction)
        completion = matches[index]
        self.apply_file_completion(text_area, context, completion)
        self.file_completion_state[context.input_id] = FileCompletionState(
            input_id=context.input_id,
            line=context.line,
            start_col=context.start_col,
            original_prefix=context.prefix,
            matches=matches,
            index=index,
        )
        return True

    async def complete_file_reference_async(
        self,
        text_area: TextArea,
        *,
        direction: int,
    ) -> bool:
        context = self.file_completion_context(text_area)
        if context is None:
            return False
        agent_id = self.sent_history_agent_id(text_area)
        is_joplin_ref = context.prefix.lower().startswith(JOPLIN_NOTE_REF_PREFIX)
        is_caller_ref = context.prefix.lower().startswith(CALLER_AGENT_REF_PREFIX)
        is_pull_request_ref = context.prefix.lower().startswith(PULL_REQUEST_REF_PREFIX)
        is_issue_ref = context.prefix.lower().startswith(ISSUE_REF_PREFIX)
        if not agent_id:
            if is_caller_ref:
                target = "@caller"
            elif is_joplin_ref:
                target = "@joplin note"
            elif is_pull_request_ref:
                target = "@pr reference"
            elif is_issue_ref:
                target = "@issue reference"
            else:
                target = "@file"
            self.notify(f"Select an agent before completing {target} references.", severity="warning")
            return True
        if is_caller_ref and not self.is_operator_agent_id(agent_id):
            self.notify(
                "Select an operator before completing @caller references.",
                severity="warning",
            )
            return True
        joplin_agent_id = (
            self.joplin_completion_agent_id(text_area, context, agent_id)
            if is_joplin_ref
            else agent_id
        )
        github_agent_id = (
            self.github_completion_agent_id(text_area, context, agent_id)
            if is_pull_request_ref or is_issue_ref
            else agent_id
        )

        state = self.file_completion_state.get(context.input_id)
        if (
            state is not None
            and state.line == context.line
            and state.start_col == context.start_col
            and 0 <= state.index < len(state.matches)
            and context.prefix.lower() == state.matches[state.index].lower()
        ):
            matches = state.matches
        else:
            if is_caller_ref:
                matches = self.caller_agent_completion_matches(context, agent_id)
            elif is_joplin_ref:
                matches = self.joplin_note_completion_matches(context, joplin_agent_id)
            elif is_pull_request_ref:
                matches = self.pull_request_completion_matches(context, github_agent_id)
            elif is_issue_ref:
                matches = self.issue_completion_matches(context, github_agent_id)
            else:
                matches = self.file_completion_matches(context, agent_id)
            if not matches:
                try:
                    if is_caller_ref:
                        await self.refresh_agents()
                    elif is_joplin_ref:
                        await self.fetch_joplin_note_summaries(joplin_agent_id)
                    elif is_pull_request_ref:
                        await self.fetch_pull_request_summaries(github_agent_id)
                    elif is_issue_ref:
                        await self.fetch_issue_summaries(github_agent_id)
                    else:
                        payload = await self.fetch_agent_file_listing(
                            agent_id,
                            context.directory,
                        )
                        self.cache_file_listing(payload)
                except Exception as exc:
                    self.file_completion_state.pop(context.input_id, None)
                    if is_caller_ref:
                        label = "Caller"
                    elif is_joplin_ref:
                        label = "Joplin note"
                    elif is_pull_request_ref:
                        label = "Pull request"
                    elif is_issue_ref:
                        label = "Issue"
                    else:
                        label = "File"
                    self.notify(
                        f"{label} completion failed: {exc}",
                        severity="error",
                    )
                    return True
                if is_caller_ref:
                    matches = self.caller_agent_completion_matches(context, agent_id)
                elif is_joplin_ref:
                    matches = self.joplin_note_completion_matches(context, joplin_agent_id)
                elif is_pull_request_ref:
                    matches = self.pull_request_completion_matches(context, github_agent_id)
                elif is_issue_ref:
                    matches = self.issue_completion_matches(context, github_agent_id)
                else:
                    matches = self.file_completion_matches(context, agent_id)

        if not matches:
            self.file_completion_state.pop(context.input_id, None)
            if is_caller_ref:
                label = "caller agent"
            elif is_joplin_ref:
                label = "scoped Joplin note"
            elif is_pull_request_ref:
                label = "pull request"
            elif is_issue_ref:
                label = "issue"
            else:
                label = "project file"
            self.notify(
                f"No {label} matches {context.prefix!r}.",
                severity="warning",
            )
            return True

        index = self.file_completion_index(context, matches, direction=direction)
        completion = matches[index]
        self.apply_file_completion(text_area, context, completion)
        self.file_completion_state[context.input_id] = FileCompletionState(
            input_id=context.input_id,
            line=context.line,
            start_col=context.start_col,
            original_prefix=context.prefix,
            matches=matches,
            index=index,
        )
        return True

    def slash_completion_context(
        self, text_area: TextArea
    ) -> SlashCompletionContext | None:
        input_id = text_area.id or "message"
        row, column = text_area.cursor_location
        lines = text_area.text.split("\n")
        if row < 0 or row >= len(lines):
            return None
        line = lines[row]
        column = min(max(0, column), len(line))
        if not line.startswith("/") or column == 0:
            return None
        suffix = line[column:]
        if suffix.strip():
            return None
        prefix = line[:column]
        if not prefix.startswith("/"):
            return None
        return SlashCompletionContext(
            input_id=input_id,
            line=row,
            start_col=0,
            end_col=column,
            prefix=prefix,
        )

    def slash_completion_matches(self, prefix: str) -> tuple[str, ...]:
        prefix_key = prefix.lower()
        return tuple(
            title
            for title in self.slash_command_titles()
            if title.lower().startswith(prefix_key)
        )

    def slash_completion_index(
        self,
        context: SlashCompletionContext,
        matches: tuple[str, ...],
        *,
        direction: int,
    ) -> int:
        state = self.slash_completion_state.get(context.input_id)
        if (
            state is not None
            and state.line == context.line
            and state.start_col == context.start_col
            and state.matches == matches
            and 0 <= state.index < len(matches)
            and context.prefix.lower() == matches[state.index].lower()
        ):
            return (state.index + direction) % len(matches)
        exact_index = next(
            (
                index
                for index, title in enumerate(matches)
                if title.lower() == context.prefix.lower()
            ),
            None,
        )
        if exact_index is not None:
            if len(matches) == 1:
                return exact_index
            return (exact_index + direction) % len(matches)
        return 0 if direction > 0 else len(matches) - 1

    def apply_slash_completion(
        self,
        text_area: TextArea,
        context: SlashCompletionContext,
        completion: str,
    ) -> None:
        lines = text_area.text.split("\n")
        line = lines[context.line]
        lines[context.line] = (
            line[: context.start_col] + completion + line[context.end_col :]
        )
        text_area.text = "\n".join(lines)
        text_area.move_cursor((context.line, context.start_col + len(completion)))
        if text_area.id == "message":
            self.resize_message_input()

    def complete_slash_command(self, text_area: TextArea, *, direction: int) -> bool:
        context = self.slash_completion_context(text_area)
        if context is None:
            return False
        state = self.slash_completion_state.get(context.input_id)
        if (
            state is not None
            and state.line == context.line
            and state.start_col == context.start_col
            and 0 <= state.index < len(state.matches)
            and context.prefix.lower() == state.matches[state.index].lower()
        ):
            matches = state.matches
        else:
            matches = self.slash_completion_matches(context.prefix)
        if not matches:
            self.slash_completion_state.pop(context.input_id, None)
            self.notify(
                f"No slash command matches {context.prefix!r}.",
                severity="warning",
            )
            return True
        index = self.slash_completion_index(context, matches, direction=direction)
        completion = matches[index]
        self.apply_slash_completion(text_area, context, completion)
        self.slash_completion_state[context.input_id] = SlashCompletionState(
            input_id=context.input_id,
            line=context.line,
            start_col=context.start_col,
            original_prefix=context.prefix,
            matches=matches,
            index=index,
        )
        return True

    async def execute_local_slash_command_from_input(
        self,
        text_area: TextArea,
        message: str,
    ) -> bool:
        git_push_branch = parse_git_push_slash_command(message)
        if git_push_branch is not None:
            agent_id = self.sent_history_agent_id(text_area)
            if agent_id:
                self.selected_agent_id = agent_id
                agent_input = self.query_one_or_none("#agent-id", Input)
                if agent_input is not None:
                    agent_input.value = agent_id
            if not agent_id:
                self.notify("Select an agent first.", severity="warning")
                return True
            if not self.is_tmux_direct_enabled(agent_id):
                self.notify(
                    f"Enable tmux direct mode for {agent_id} before using this command.",
                    severity="warning",
                )
                return True
            await self.palette_git_push_target(agent_id, git_push_branch)
            self.record_sent_message(agent_id, message)
            self.set_agent_draft_text(agent_id, text_area, "")
            return True
        command = self.slash_command_for_text(message)
        if command is None:
            return False
        agent_id = self.sent_history_agent_id(text_area)
        if agent_id:
            self.selected_agent_id = agent_id
            agent_input = self.query_one_or_none("#agent-id", Input)
            if agent_input is not None:
                agent_input.value = agent_id
        joplin_action = JOPLIN_SLASH_ACTIONS.get(command.title.lower())
        if joplin_action is not None:
            if not agent_id:
                self.notify("Select an agent first.", severity="warning")
                return True
            await self.joplin_action_for_agent(agent_id, joplin_action)
            self.record_sent_message(agent_id, message)
            self.set_agent_draft_text(agent_id, text_area, "")
            return True
        pr_action = PULL_REQUEST_SLASH_ACTIONS.get(command.title.lower())
        if pr_action is not None:
            if not agent_id:
                self.notify("Select an agent first.", severity="warning")
                return True
            await self.pull_request_action_for_agent(agent_id, pr_action)
            self.record_sent_message(agent_id, message)
            self.set_agent_draft_text(agent_id, text_area, "")
            return True
        issue_action = ISSUE_SLASH_ACTIONS.get(command.title.lower())
        if issue_action is not None:
            if not agent_id:
                self.notify("Select an agent first.", severity="warning")
                return True
            await self.issue_action_for_agent(agent_id, issue_action)
            self.record_sent_message(agent_id, message)
            self.set_agent_draft_text(agent_id, text_area, "")
            return True
        campaign_action = CAMPAIGN_SLASH_ACTIONS.get(command.title.lower())
        if campaign_action is not None:
            if not agent_id:
                self.notify("Select an agent first.", severity="warning")
                return True
            await self.campaign_action_for_agent(agent_id, campaign_action)
            self.record_sent_message(agent_id, message)
            self.set_agent_draft_text(agent_id, text_area, "")
            return True
        original_text = text_area.text
        result = command.callback()
        if inspect.isawaitable(result):
            await result
        if agent_id:
            self.record_sent_message(agent_id, message)
        if text_area.text == original_text:
            if agent_id:
                self.set_agent_draft_text(agent_id, text_area, "")
            else:
                text_area.text = ""
                if text_area.id == "message":
                    self.resize_message_input()
        return True

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if event.tabbed_content.id != "agent-tabs":
            return
        self.active_agent_tab = str(event.pane.id)
        agent_id = self.selected_agent_id
        if agent_id:
            self.active_agent_tab_by_agent[agent_id] = self.active_agent_tab
        if self.active_agent_tab == "latest-tab" and agent_id:
            self.mark_latest_seen(agent_id)
            self.apply_tmux_class()
            if self.is_tmux_direct_enabled(agent_id):
                self.run_async_worker(
                    lambda agent_id=agent_id: self.load_tmux_capture(agent_id),
                    name="tmux-capture",
                    exclusive=True,
                )
        if self.active_agent_tab == "workerbee-tab" and agent_id:
            self.run_async_worker(
                lambda agent_id=agent_id: self.load_workerbee_status(agent_id),
                name="workerbee-status",
                exclusive=True,
            )
        if self.active_agent_tab == "pull-requests-tab" and agent_id:
            self.run_async_worker(
                lambda agent_id=agent_id: self.load_pull_requests(agent_id),
                name="pull-requests",
                exclusive=True,
            )
        if self.active_agent_tab == "issues-tab" and agent_id:
            self.run_async_worker(
                lambda agent_id=agent_id: self.load_issues(agent_id),
                name="issues",
                exclusive=True,
            )
        if self.active_agent_tab == "files-tab" and agent_id:
            self.run_async_worker(
                lambda agent_id=agent_id: self.load_agent_files(agent_id),
                name="agent-files",
                exclusive=True,
            )
        if self.active_agent_tab == "joplin-tab" and agent_id:
            self.run_async_worker(
                lambda agent_id=agent_id: self.load_joplin_notes(agent_id),
                name="agent-joplin",
                exclusive=True,
            )

    def save_current_agent_pane_state(self, agent_id: str | None = None) -> None:
        agent_id = agent_id or self.selected_agent_id
        if not agent_id:
            return
        self.active_agent_tab_by_agent[agent_id] = self.active_agent_tab
        if self.selected_thread_item_id:
            self.selected_thread_item_id_by_agent[agent_id] = self.selected_thread_item_id
        else:
            self.selected_thread_item_id_by_agent.pop(agent_id, None)
        if self.selected_pull_request_number is not None:
            self.selected_pull_request_number_by_agent[agent_id] = (
                self.selected_pull_request_number
            )
        else:
            self.selected_pull_request_number_by_agent.pop(agent_id, None)
        if self.selected_issue_number is not None:
            self.selected_issue_number_by_agent[agent_id] = self.selected_issue_number
        else:
            self.selected_issue_number_by_agent.pop(agent_id, None)
        message_input = self.query_one_or_none("#message", TextArea)
        if message_input is not None:
            self.message_draft_by_agent[agent_id] = message_input.text
        tmux_message = self.query_one_or_none("#tmux-message", TextArea)
        if tmux_message is not None:
            self.tmux_message_draft_by_agent[agent_id] = tmux_message.text

    def restore_agent_pane_state(self, agent_id: str) -> None:
        self.selected_thread_item_id = self.selected_thread_item_id_by_agent.get(
            agent_id
        )
        self.selected_pull_request_number = (
            self.selected_pull_request_number_by_agent.get(agent_id)
        )
        self.selected_issue_number = self.selected_issue_number_by_agent.get(agent_id)
        self.restore_agent_drafts(agent_id)
        preferred_tab = self.active_agent_tab_by_agent.get(agent_id, "latest-tab")
        self.activate_agent_tab(preferred_tab)

    def restore_agent_drafts(self, agent_id: str) -> None:
        message_input = self.query_one_or_none("#message", TextArea)
        if message_input is not None:
            message_input.text = self.message_draft_by_agent.get(agent_id, "")
            self.resize_message_input()
        tmux_message = self.query_one_or_none("#tmux-message", TextArea)
        if tmux_message is not None:
            tmux_message.text = self.tmux_message_draft_by_agent.get(agent_id, "")

    def activate_agent_tab(self, tab_id: str) -> str:
        if tab_id == "joplin-tab" and not self.joplin_configured:
            tab_id = "latest-tab"
        tabs = self.query_one_or_none("#agent-tabs", TabbedContent)
        if tabs is not None:
            try:
                tabs.active = tab_id
            except Exception:
                tab_id = "latest-tab"
                tabs.active = tab_id
        self.active_agent_tab = tab_id
        if self.selected_agent_id:
            self.active_agent_tab_by_agent[self.selected_agent_id] = tab_id
        return tab_id

    async def select_agent(self, agent_id: str) -> None:
        previous_agent_id = self.selected_agent_id
        if agent_id != previous_agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        was_compact_home = self.is_compact_layout() and self.compact_view == "home"
        self.selected_agent_id = agent_id
        self.query_one("#agent-id", Input).value = self.selected_agent_id
        if agent_id != previous_agent_id:
            self.restore_agent_pane_state(agent_id)
        self.update_agent_title()
        self.apply_tmux_class()
        if self.is_compact_layout():
            self.show_compact_agent()
        if was_compact_home:
            self.activate_agent_tab(
                self.active_agent_tab_by_agent.get(agent_id, "latest-tab")
            )
        await self.refresh_selected_agent(self.selected_agent_id)
        if self.active_agent_tab == "latest-tab":
            self.mark_latest_seen(agent_id)

    def plan_mode_state(self, agent_id: str | None) -> str:
        if not agent_id:
            return "off"
        if agent_id in self.tmux_plan_selector_agent_ids:
            return "select"
        if self.pending_slash_command_by_agent.get(agent_id) == PLAN_SLASH_COMMAND:
            return "pending"
        if agent_id in self.plan_mode_active_agent_ids:
            return "on"
        return "off"

    def update_agent_title(self) -> None:
        title = self.query_one_or_none("#agent-title", Static)
        if title is None:
            return
        agent_id = self.selected_agent_id
        if not agent_id:
            title.update("Agent: -")
            return
        state = self.plan_mode_state(agent_id)
        view = "tmux" if self.is_tmux_direct_enabled(agent_id) else "pbx"
        title.update(f"Agent: {agent_id} | View: {view} | Plan: {state}")

    def activate_latest_tab(self) -> None:
        self.activate_agent_tab("latest-tab")

    def run_async_worker(
        self,
        work_factory: Callable[[], Any],
        *,
        name: str | None = "",
        group: str = "default",
        description: str = "",
        exit_on_error: bool = True,
        start: bool = True,
        exclusive: bool = False,
    ) -> Any:
        async def runner() -> Any:
            result = work_factory()
            if inspect.isawaitable(result):
                return await result
            return result

        return self.run_worker(
            runner,
            name=name,
            group=group,
            description=description,
            exit_on_error=exit_on_error,
            start=start,
            exclusive=exclusive,
        )

    async def refresh_selected_agent(self, agent_id: str) -> None:
        if self.active_agent_tab == "files-tab":
            await self.load_agent_files(agent_id)
        elif self.active_agent_tab == "workerbee-tab":
            await self.load_workerbee_status(agent_id)
        elif self.active_agent_tab == "pull-requests-tab":
            await self.load_pull_requests(agent_id)
        elif self.active_agent_tab == "issues-tab":
            await self.load_issues(agent_id)
        elif self.active_agent_tab == "campaigns-tab":
            await self.load_operator_campaigns(agent_id)
        elif self.active_agent_tab == "joplin-tab":
            await self.load_joplin_notes(agent_id)
        elif self.is_tmux_direct_enabled(agent_id):
            await self.load_tmux_capture(agent_id)
        else:
            await self.load_latest_report(agent_id)
        await self.load_thread(agent_id)

    def mark_latest_seen(self, agent_id: str) -> None:
        last_seen = self.latest_report_timestamp(agent_id)
        changed = False
        should_sync_remote = False
        if last_seen is not None:
            changed = self.latest_viewed_at_by_agent.get(agent_id) != last_seen
            self.latest_viewed_at_by_agent[agent_id] = last_seen
            shared_seen_at = self.shared_latest_seen_at(agent_id)
            should_sync_remote = shared_seen_at is None or shared_seen_at < last_seen
        if (changed or should_sync_remote) and self.is_running:
            self.run_async_worker(
                lambda agent_id=agent_id: self.mark_latest_seen_remote(agent_id),
                name=f"latest-seen-{agent_id}",
                exclusive=True,
            )
        if agent_id not in self.unseen_latest_agent_ids:
            if changed:
                self.save_settings()
            return
        self.unseen_latest_agent_ids.remove(agent_id)
        self.render_agents()
        self.render_unseen_attention()
        self.save_settings()

    async def mark_latest_seen_remote(self, agent_id: str) -> None:
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/latest/seen",
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            agent = response.json()
        except Exception:
            return
        if isinstance(agent, dict):
            existing = self.agents.get(agent_id)
            if existing is not None:
                existing["latest_report_seen_at"] = agent.get("latest_report_seen_at")
            else:
                self.agents[agent_id] = agent
            seen_at = self.shared_latest_seen_at(agent_id)
            if seen_at is not None:
                self.latest_viewed_at_by_agent[agent_id] = max(
                    self.latest_viewed_at_by_agent.get(agent_id, 0.0),
                    seen_at,
                )
                self.save_settings()

    async def load_latest_report(self, agent_id: str) -> None:
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is None:
            return
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/reports",
                params={"limit": 1},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            reports = response.json()
        except Exception as exc:
            detail.text = f"Unable to load report for {agent_id}: {exc}"
            self.render_latest_plan_choice_panel(None)
            return
        if not reports:
            detail.text = f"No reports for {agent_id}."
            self.latest_report_by_agent.pop(agent_id, None)
            self.render_latest_plan_choice_panel(None)
            return
        report = reports[0]
        self.latest_report_by_agent[agent_id] = report
        plan_options = self.plan_options_for_report(report)
        lines = [
            f"Agent: {report['agent_id']}",
            f"Status: {report['status']}",
            f"Needs Input: {'yes' if report.get('needs_input') else 'no'}",
            f"Summary: {report['summary']}",
            "",
            str(report["detail"]),
        ]
        if plan_options:
            lines.extend(
                [
                    "",
                    "Plan Options:",
                    *[f"- {option.display_label}" for option in plan_options],
                ]
            )
        detail.text = "\n".join(lines)
        self.render_latest_plan_choice_panel(report)

    async def refresh_tmux_capture_if_active(self) -> None:
        if (
            not self.is_tmux_direct_enabled(self.selected_agent_id)
            or self.active_agent_tab != "latest-tab"
            or not self.selected_agent_id
            or self.tmux_refreshing
        ):
            return
        self.tmux_refreshing = True
        try:
            await self.load_tmux_capture(self.selected_agent_id)
        finally:
            self.tmux_refreshing = False

    async def load_tmux_capture(self, agent_id: str) -> None:
        status = self.query_one_or_none("#tmux-status", Static)
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if status is None or stream is None:
            return
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            self.tmux_visible_capture_key = None
            self.record_tmux_liveness_state(agent_id, "unavailable")
            self.update_tmux_status(
                status,
                "Tmux: unavailable",
                cache_key=agent_id,
            )
            stream.text = f"Unable to list tmux panes: {exc}"
            self.render_agents()
            return
        self.tmux_panes = panes
        pane, mode = self.resolve_tmux_pane(agent_id, panes)
        if pane is None and mode == "auto":
            selector_pane = await self.resolve_tmux_plan_selector_pane(agent_id, panes)
            if selector_pane is not None:
                pane = selector_pane
                mode = "selector"
        if pane is None:
            self.tmux_visible_capture_key = None
            self.update_tmux_plan_selector_state(agent_id, "")
            self.record_tmux_liveness_state(
                agent_id,
                "stale" if mode == "stale" else mode,
            )
            if mode == "stale":
                self.update_tmux_status(
                    status,
                    f"Tmux: stale target for {agent_id}",
                    cache_key=agent_id,
                )
                stream.text = (
                    "The saved tmux pane target no longer matches this agent.\n\n"
                    "Use Auto to rediscover the pane or Select Pane to choose one."
                )
            else:
                self.update_tmux_status(
                    status,
                    f"Tmux: no pane for {agent_id}",
                    cache_key=agent_id,
                )
                stream.text = (
                    "No tmux pane is attached for this agent.\n\n"
                    "Use Auto to retry discovery or Select Pane to choose a pane."
                )
            self.render_agents()
            return
        cache_key = f"{agent_id}:{pane.pane_id}"
        self.prepare_tmux_stream_for_capture(stream, cache_key=cache_key, pane=pane)
        try:
            captured = await asyncio.to_thread(
                tmux_support.capture_pane,
                pane.pane_id,
                lines=self.tmux_capture_lines,
            )
        except Exception as exc:
            self.update_tmux_status(
                status,
                f"Tmux: {pane.pane_id} capture failed",
                cache_key=agent_id,
            )
            self.update_tmux_plan_selector_state(agent_id, "")
            self.record_tmux_liveness_state(agent_id, "unavailable", pane_id=pane.pane_id)
            stream.text = f"Unable to capture tmux pane {pane.pane_id}: {exc}"
            self.render_agents()
            return
        displayed = self.crop_tmux_capture_for_display(captured or "(empty tmux pane)")
        self.update_tmux_plan_selector_state(
            agent_id,
            displayed,
            pane_id=pane.pane_id,
        )
        self.record_tmux_capture_liveness(agent_id, pane.pane_id, displayed)
        self.update_tmux_stream(
            stream,
            displayed,
            cache_key=cache_key,
        )
        selector_note = (
            " selector pending: /plan:1 start /plan:2 clear context & start /plan:3 stay"
            if agent_id in self.tmux_plan_selector_agent_ids
            else ""
        )
        self.update_tmux_status(
            status,
            (
                "Tmux: "
                f"{pane.pane_id} {pane.target_label} {mode} "
                f"{self.tmux_capture_mode_label()} "
                "cropped "
                f"{pane.current_command} {pane.width}x{pane.height}"
                f"{selector_note}"
            ),
            cache_key=agent_id,
        )
        self.render_agents()

    async def resolve_tmux_plan_selector_pane(
        self,
        agent_id: str,
        panes: list[tmux_support.TmuxPane],
    ) -> tmux_support.TmuxPane | None:
        if agent_id in self.tmux_detached_agent_ids:
            return None
        agent = self.agents.get(agent_id, {"agent_id": agent_id})
        candidates = [
            pane
            for pane in tmux_support.ranked_panes_for_agent(
                self.tmux_candidate_panes_for_agent(agent, panes),
                agent,
            )
            if tmux_support.pane_matches_agent(pane, agent)
        ]
        selector_matches = await self.tmux_plan_selector_matches(candidates)
        if len(selector_matches) != 1:
            self.update_tmux_plan_selector_state(agent_id, "")
            return None
        pane, displayed = selector_matches[0]
        self.update_tmux_plan_selector_state(
            agent_id,
            displayed,
            pane_id=pane.pane_id,
        )
        return pane

    async def tmux_plan_selector_matches(
        self,
        panes: Iterable[tmux_support.TmuxPane],
    ) -> list[tuple[tmux_support.TmuxPane, str]]:
        matches: list[tuple[tmux_support.TmuxPane, str]] = []
        seen_pane_ids: set[str] = set()
        for pane in panes:
            if pane.pane_id in seen_pane_ids:
                continue
            seen_pane_ids.add(pane.pane_id)
            try:
                captured = await asyncio.to_thread(
                    tmux_support.capture_pane,
                    pane.pane_id,
                    lines=self.tmux_capture_lines,
                )
            except Exception:
                continue
            displayed = self.crop_tmux_capture_for_display(captured or "")
            if contains_codex_native_plan_selector(displayed):
                matches.append((pane, displayed))
        return matches

    def update_tmux_plan_selector_state(
        self,
        agent_id: str,
        captured: str,
        *,
        pane_id: str | None = None,
    ) -> bool:
        was_pending = agent_id in self.tmux_plan_selector_agent_ids
        indices = set(codex_native_plan_selector_indices(captured))
        is_pending = bool(indices)
        if is_pending:
            self.tmux_plan_selector_agent_ids.add(agent_id)
            self.tmux_plan_selector_indices_by_agent[agent_id] = indices
            if pane_id:
                self.tmux_plan_selector_pane_by_agent[agent_id] = pane_id
        else:
            self.tmux_plan_selector_agent_ids.discard(agent_id)
            self.tmux_plan_selector_pane_by_agent.pop(agent_id, None)
            self.tmux_plan_selector_indices_by_agent.pop(agent_id, None)
        if was_pending == is_pending:
            return False
        self.update_agent_title()
        self.render_unseen_attention()
        return True

    def native_plan_selector_indices_for_pane(
        self,
        agent_id: str,
        pane_id: str,
    ) -> set[int]:
        indices = set(self.tmux_plan_selector_indices_by_agent.get(agent_id, set()))
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        visible_pane_id = self.tmux_pane_id_from_capture_key(self.tmux_visible_capture_key)
        if stream is not None and visible_pane_id == pane_id:
            visible_indices = set(codex_native_plan_selector_indices(stream.text))
            indices.update(visible_indices)
            visible_agent_id = self.tmux_agent_id_from_capture_key(
                self.tmux_visible_capture_key
            )
            if visible_agent_id:
                self.update_tmux_plan_selector_state(
                    visible_agent_id,
                    stream.text,
                    pane_id=pane_id,
                )
        return indices

    def available_native_plan_selector_indices(self, agent_id: str) -> set[int]:
        pane_id = self.tmux_plan_selector_pane_by_agent.get(agent_id)
        if pane_id:
            return self.native_plan_selector_indices_for_pane(agent_id, pane_id)
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None:
            return set(self.tmux_plan_selector_indices_by_agent.get(agent_id, set()))
        visible_pane_id = self.tmux_visible_plan_selector_pane_id()
        if visible_pane_id is None:
            return set(self.tmux_plan_selector_indices_by_agent.get(agent_id, set()))
        return self.native_plan_selector_indices_for_pane(agent_id, visible_pane_id)

    def tmux_visible_native_plan_selector_pending(self) -> bool:
        return self.tmux_visible_plan_selector_pane_id() is not None

    def tmux_native_plan_selector_pending(self, agent_id: str) -> bool:
        if agent_id in self.tmux_plan_selector_agent_ids:
            if (
                agent_id == self.selected_agent_id
                and str(self.tmux_visible_capture_key or "").startswith(f"{agent_id}:")
            ):
                stream = self.query_one_or_none("#tmux-stream", TextArea)
                if stream is not None and not contains_codex_native_plan_selector(stream.text):
                    self.update_tmux_plan_selector_state(agent_id, stream.text)
                    return False
                if stream is not None:
                    self.update_tmux_plan_selector_state(
                        agent_id,
                        stream.text,
                        pane_id=self.tmux_visible_pane_id_for_agent(agent_id),
                    )
            return True
        if agent_id != self.selected_agent_id:
            return False
        if not str(self.tmux_visible_capture_key or "").startswith(f"{agent_id}:"):
            return False
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None or not contains_codex_native_plan_selector(stream.text):
            return False
        pane_id = self.tmux_visible_pane_id_for_agent(agent_id)
        self.update_tmux_plan_selector_state(
            agent_id,
            stream.text,
            pane_id=pane_id,
        )
        self.update_agent_title()
        self.render_unseen_attention()
        return True

    def tmux_agent_id_from_capture_key(self, cache_key: str | None) -> str | None:
        value = str(cache_key or "")
        if ":" not in value:
            return None
        agent_id, pane_id = value.rsplit(":", 1)
        if not agent_id or not pane_id:
            return None
        return agent_id

    def tmux_pane_id_from_capture_key(self, cache_key: str | None) -> str | None:
        value = str(cache_key or "")
        if ":" not in value:
            return None
        _, pane_id = value.rsplit(":", 1)
        return pane_id or None

    def tmux_visible_plan_selector_pane_id(self) -> str | None:
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None or not contains_codex_native_plan_selector(stream.text):
            return None
        pane_id = self.tmux_pane_id_from_capture_key(self.tmux_visible_capture_key)
        agent_id = self.tmux_agent_id_from_capture_key(self.tmux_visible_capture_key)
        if agent_id and pane_id:
            self.update_tmux_plan_selector_state(
                agent_id,
                stream.text,
                pane_id=pane_id,
            )
        return pane_id

    def tmux_visible_pane_id_for_agent(self, agent_id: str) -> str | None:
        prefix = f"{agent_id}:"
        cache_key = str(self.tmux_visible_capture_key or "")
        if not cache_key.startswith(prefix):
            return None
        return self.tmux_pane_id_from_capture_key(cache_key)

    def prepare_tmux_stream_for_capture(
        self,
        stream: TextArea,
        *,
        cache_key: str,
        pane: tmux_support.TmuxPane,
    ) -> bool:
        if self.tmux_visible_capture_key == cache_key:
            return False
        self.tmux_visible_capture_key = cache_key
        stream.text = f"Loading tmux pane {pane.pane_id} ({pane.target_label})..."
        self.snap_tmux_stream_to_bottom(stream)
        return True

    def update_tmux_status(
        self,
        status: Static,
        text: str,
        *,
        cache_key: str,
    ) -> bool:
        if self.tmux_last_status_by_agent.get(cache_key) == text:
            return False
        self.tmux_last_status_by_agent[cache_key] = text
        status.update(text)
        return True

    def tmux_capture_mode_label(self) -> str:
        if self.tmux_capture_lines <= 0:
            return "visible"
        return f"scrollback {self.tmux_capture_lines}"

    def crop_tmux_capture_for_display(self, captured: str) -> str:
        lines = captured.splitlines()
        if not lines:
            return captured
        search_start = max(0, len(lines) - 12)
        for index in range(len(lines) - 1, search_start - 1, -1):
            if CODEX_STATUS_LINE_PATTERN.search(lines[index]):
                body = "\n".join(lines[:index]).rstrip("\n")
                return body or "(empty tmux pane)"
        return captured

    def update_tmux_stream(
        self,
        stream: TextArea,
        captured: str,
        *,
        cache_key: str,
    ) -> bool:
        previous = self.tmux_last_capture_by_pane.get(cache_key)
        self.tmux_last_capture_by_pane[cache_key] = captured
        if previous == captured and stream.text == captured:
            return False
        if stream.text == captured:
            return False
        at_bottom = self.tmux_stream_is_at_bottom(stream)
        scroll_y = stream.scroll_y
        scroll_target_y = stream.scroll_target_y
        stream.text = captured
        if at_bottom:
            self.snap_tmux_stream_to_bottom(stream)
        else:
            stream.scroll_y = scroll_y
            stream.scroll_target_y = scroll_target_y
        return True

    def tmux_stream_is_at_bottom(self, stream: TextArea) -> bool:
        max_scroll_y = float(getattr(stream, "max_scroll_y", 0) or 0)
        if max_scroll_y <= 0:
            return True
        scroll_y = float(getattr(stream, "scroll_y", 0) or 0)
        scroll_target_y = float(getattr(stream, "scroll_target_y", scroll_y) or 0)
        return max(scroll_y, scroll_target_y) >= max_scroll_y - 1

    def should_restore_tmux_stream_tail_after_resize(self) -> bool:
        if self.active_agent_tab != "latest-tab" or not self.tmux_visible_capture_key:
            return False
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None or not stream.text:
            return False
        return self.tmux_stream_is_at_bottom(stream)

    def snap_tmux_stream_to_bottom(self, stream: TextArea | None = None) -> None:
        if stream is None:
            stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None:
            return
        lines = stream.text.split("\n")
        row = max(0, len(lines) - 1)
        column = len(lines[-1]) if lines else 0
        location = (row, column)
        try:
            stream.move_cursor(location, center=False)
        except Exception:
            stream.selection = (location, location)
        stream.scroll_end(animate=False)
        stream.scroll_x = 0
        stream.scroll_target_x = 0

    def is_operator_tmux_pane(self, pane: tmux_support.TmuxPane) -> bool:
        return pane.session_name == self.operator_tmux_session_name()

    async def reconcile_operator_tmux_targets(self) -> bool:
        if not self.tmux_features_available:
            return False
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception:
            return False
        self.tmux_panes = panes
        changed = self.reconcile_operator_tmux_targets_from_panes(panes)
        if changed:
            self.save_settings()
        return changed

    def reconcile_operator_tmux_targets_from_panes(
        self,
        panes: list[tmux_support.TmuxPane],
    ) -> bool:
        changed = False
        for agent in self.ordered_agents(OPERATOR_AGENT_TYPE):
            agent_id = str(agent.get("agent_id") or "").strip()
            if not agent_id or agent_id in self.tmux_detached_agent_ids:
                continue
            matches = [
                pane
                for pane in panes
                if self.tmux_pane_allowed_for_agent(agent, pane)
            ]
            saved_pane = self.saved_tmux_target_pane_for_agent(
                agent_id,
                agent,
                matches,
            )
            if saved_pane is not None:
                if self.tmux_direct_agent_modes.get(agent_id) is not True:
                    self.tmux_direct_agent_modes[agent_id] = True
                    changed = True
                changed = self.update_local_agent_tmux_pane(agent_id, saved_pane) or changed
                continue
            if self.tmux_agent_targets.get(agent_id):
                self.clear_saved_tmux_target(agent_id)
                changed = True
            if len(matches) != 1:
                continue
            pane = matches[0]
            if self.tmux_agent_targets.get(agent_id) != pane.pane_id:
                self.tmux_agent_targets[agent_id] = pane.pane_id
                changed = True
            if self.tmux_direct_agent_modes.get(agent_id) is not True:
                self.tmux_direct_agent_modes[agent_id] = True
                changed = True
            self.tmux_detached_agent_ids.discard(agent_id)
            changed = self.update_local_agent_tmux_pane(agent_id, pane) or changed
        changed = self.prune_selected_operator_fork_targets() or changed
        return changed

    def saved_tmux_target_pane_for_agent(
        self,
        agent_id: str,
        agent: dict[str, Any],
        panes: Iterable[tmux_support.TmuxPane],
    ) -> tmux_support.TmuxPane | None:
        saved = str(self.tmux_agent_targets.get(agent_id) or "").strip()
        if not saved:
            return None
        for pane in panes:
            if pane.pane_id != saved and pane.target_label != saved:
                continue
            if self.tmux_pane_allowed_for_agent(agent, pane):
                return pane
        return None

    def update_local_agent_tmux_pane(
        self,
        agent_id: str,
        pane: tmux_support.TmuxPane,
    ) -> bool:
        agent = self.agents.get(agent_id)
        if agent is None:
            return False
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        if metadata.get("tmux_pane_id") == pane.pane_id:
            return False
        agent["metadata"] = {**metadata, "tmux_pane_id": pane.pane_id}
        return True

    def prune_selected_operator_fork_targets(self) -> bool:
        changed = False
        for logical_operator_id, target_key in list(
            self.selected_operator_fork_target_by_operator.items()
        ):
            if ":" not in target_key:
                self.selected_operator_fork_target_by_operator.pop(logical_operator_id, None)
                changed = True
                continue
            fork_agent_id, pane_id = target_key.rsplit(":", 1)
            fork = self.agents.get(fork_agent_id)
            if (
                fork is None
                or self.operator_role(fork) != OPERATOR_ROLE_FORK
                or self.logical_operator_id_for_agent(fork) != logical_operator_id
                or self.tmux_agent_targets.get(fork_agent_id) != pane_id
            ):
                self.selected_operator_fork_target_by_operator.pop(logical_operator_id, None)
                changed = True
        return changed

    def tmux_pane_matches_operator_agent(
        self,
        agent: dict[str, Any],
        pane: tmux_support.TmuxPane,
    ) -> bool:
        agent_id = str(agent.get("agent_id") or "").strip()
        if not agent_id:
            return False
        return pane.window_name == agent_id or pane.title == agent_id

    def tmux_pane_allowed_for_agent(
        self,
        agent: dict[str, Any],
        pane: tmux_support.TmuxPane,
    ) -> bool:
        agent_type = self.agent_type(agent)
        if agent_type == CALLER_AGENT_TYPE:
            return not self.is_operator_tmux_pane(pane)
        if agent_type == OPERATOR_AGENT_TYPE:
            return (
                self.is_operator_tmux_pane(pane)
                and self.tmux_pane_matches_operator_agent(agent, pane)
            )
        return True

    def tmux_candidate_panes_for_agent(
        self,
        agent: dict[str, Any],
        panes: list[tmux_support.TmuxPane],
    ) -> list[tmux_support.TmuxPane]:
        return [
            pane for pane in panes if self.tmux_pane_allowed_for_agent(agent, pane)
        ]

    def clear_saved_tmux_target(self, agent_id: str) -> None:
        self.tmux_agent_targets.pop(agent_id, None)
        self.tmux_manual_override_agent_ids.discard(agent_id)

    def resolve_tmux_pane(
        self,
        agent_id: str,
        panes: list[tmux_support.TmuxPane],
    ) -> tuple[tmux_support.TmuxPane | None, str]:
        if agent_id in self.tmux_detached_agent_ids:
            return None, "detached"
        agent = self.agents.get(agent_id, {"agent_id": agent_id})
        manual_target = self.tmux_agent_targets.get(agent_id)
        if manual_target:
            for pane in panes:
                if pane.pane_id == manual_target or pane.target_label == manual_target:
                    if not self.tmux_pane_allowed_for_agent(agent, pane):
                        self.clear_saved_tmux_target(agent_id)
                        self.save_settings()
                        break
                    if (
                        agent_id not in self.tmux_manual_override_agent_ids
                        and not tmux_support.pane_matches_agent(pane, agent)
                    ):
                        self.clear_saved_tmux_target(agent_id)
                        self.save_settings()
                        break
                    return pane, "manual"
            else:
                self.clear_saved_tmux_target(agent_id)
                self.save_settings()
        candidate_panes = self.tmux_candidate_panes_for_agent(agent, panes)
        if self.agent_type(agent) == OPERATOR_AGENT_TYPE and len(candidate_panes) == 1:
            return candidate_panes[0], "auto"
        pane = tmux_support.choose_pane_for_agent(candidate_panes, agent)
        if pane is None:
            return None, "auto"
        return pane, "auto"

    async def select_next_tmux_pane(self) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            stream = self.query_one_or_none("#tmux-stream", TextArea)
            if stream is not None:
                stream.text = f"Unable to list tmux panes: {exc}"
            return
        self.tmux_panes = panes
        if not panes:
            stream = self.query_one_or_none("#tmux-stream", TextArea)
            if stream is not None:
                stream.text = "No tmux panes are available."
            return
        agent = self.agents.get(agent_id, {"agent_id": agent_id})
        ranked = tmux_support.ranked_panes_for_agent(
            self.tmux_candidate_panes_for_agent(agent, panes),
            agent,
        )
        if not ranked:
            stream = self.query_one_or_none("#tmux-stream", TextArea)
            if stream is not None:
                stream.text = "No tmux panes match this agent type."
            return
        current = self.tmux_agent_targets.get(agent_id)
        current_index = next(
            (
                index
                for index, pane in enumerate(ranked)
                if pane.pane_id == current or pane.target_label == current
            ),
            -1,
        )
        pane = ranked[(current_index + 1) % len(ranked)]
        self.tmux_agent_targets[agent_id] = pane.pane_id
        self.tmux_manual_override_agent_ids.add(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        self.save_settings()
        await self.load_tmux_capture(agent_id)

    async def set_tmux_auto(self) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        self.tmux_agent_targets.pop(agent_id, None)
        self.tmux_manual_override_agent_ids.discard(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        self.save_settings()
        await self.load_tmux_capture(agent_id)

    async def detach_tmux_pane(self) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        self.tmux_agent_targets.pop(agent_id, None)
        self.tmux_manual_override_agent_ids.discard(agent_id)
        self.tmux_detached_agent_ids.add(agent_id)
        self.tmux_visible_capture_key = None
        self.save_settings()
        status = self.query_one_or_none("#tmux-status", Static)
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if status is not None:
            status.update(f"Tmux: detached from {agent_id}")
        if stream is not None:
            stream.text = "Detached. Use Auto or Select Pane to reconnect."

    async def load_thread(self, agent_id: str) -> None:
        thread_detail = self.query_one("#thread-detail", TextArea)
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/thread",
                params={"limit": 100},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            thread = response.json()
        except Exception as exc:
            self.thread_items = {}
            self.thread_order = []
            self.marked_thread_item_ids.clear()
            self.render_thread([])
            self.render_plan_choice_panel(None)
            thread_detail.text = f"Unable to load thread for {agent_id}: {exc}"
            return
        thread = self.order_thread_for_display(thread)
        self.thread_items = {item["item_id"]: item for item in thread}
        self.thread_order = [item["item_id"] for item in thread]
        self.marked_thread_item_ids.intersection_update(self.thread_items)
        self.render_thread(thread)
        if thread:
            item_id = (
                self.selected_thread_item_id
                if self.selected_thread_item_id in self.thread_items
                else thread[0]["item_id"]
            )
            self.select_thread_item(item_id)
        else:
            self.selected_thread_item_id = None
            self.selected_thread_item_id_by_agent.pop(agent_id, None)
            self.thread_order = []
            self.render_plan_choice_panel(None)
            thread_detail.text = f"No thread history for {agent_id}."

    def order_thread_for_display(
        self, thread: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return sorted(
            thread,
            key=lambda item: (float(item["created_at"]), str(item["item_id"])),
            reverse=True,
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            await self.send_input()
            return
        if event.button.id == "tmux-send":
            await self.send_tmux_input()
            return
        if event.button.id == "tmux-auto":
            await self.set_tmux_auto()
            return
        if event.button.id == "tmux-select":
            await self.select_next_tmux_pane()
            return
        if event.button.id == "tmux-detach":
            await self.detach_tmux_pane()
            return
        if event.button.id == "request-detail":
            await self.request_detail()
            return
        if event.button.id == "ping-agent":
            await self.ping_agent()
            return
        if event.button.id == "mark-working":
            await self.mark_agent_working()
            return
        if event.button.id == "mark-canceled":
            await self.mark_agent_canceled()
            return
        if event.button.id == "export-item":
            self.export_thread_scope("item")
            return
        if event.button.id == "export-marked":
            self.export_thread_scope("marked")
            return
        if event.button.id == "export-all":
            self.export_thread_scope("all")
            return
        if event.button.id == "clear-marks":
            self.clear_thread_marks()
            return
        if event.button.id == "delete-queued":
            await self.delete_queued_thread_commands()
            return
        if event.button.id == "files-refresh":
            if self.selected_agent_id:
                await self.load_agent_files(self.selected_agent_id)
            return
        if event.button.id == "files-up":
            if self.selected_agent_id:
                await self.load_parent_agent_files(self.selected_agent_id)
            return
        if event.button.id == "workerbee-refresh":
            if self.selected_agent_id:
                await self.load_workerbee_status(self.selected_agent_id)
            return
        if event.button.id == "pr-refresh":
            if self.selected_agent_id:
                await self.load_pull_requests(self.selected_agent_id)
            return
        if event.button.id == "pr-review":
            if self.selected_agent_id:
                await self.request_pull_request_review(self.selected_agent_id)
            return
        if event.button.id == "pr-validate":
            if self.selected_agent_id:
                await self.request_pull_request_validation(self.selected_agent_id)
            return
        if event.button.id == "pr-url":
            if self.selected_agent_id:
                self.show_selected_pull_request_url(self.selected_agent_id)
            return
        if event.button.id == "pr-merge":
            if self.selected_agent_id:
                self.confirm_merge_pull_request(self.selected_agent_id)
            return
        if event.button.id == "issue-refresh":
            if self.selected_agent_id:
                await self.load_issues(self.selected_agent_id)
            return
        if event.button.id == "issue-mitigate":
            if self.selected_agent_id:
                await self.request_issue_mitigation(self.selected_agent_id)
            return
        if event.button.id == "issue-url":
            if self.selected_agent_id:
                self.show_selected_issue_url(self.selected_agent_id)
            return
        if event.button.id == "issue-clear":
            if self.selected_agent_id:
                self.confirm_clear_issue(self.selected_agent_id)
            return
        joplin_button_actions = {
            "joplin-new": "new",
            "joplin-rename": "rename",
            "joplin-delete": "delete",
            "joplin-refresh": "refresh",
            "joplin-copy-latest": "copy",
            "joplin-log-start": "log-start",
            "joplin-log-stop": "log-stop",
            "joplin-sync": "sync",
            "joplin-save": "save",
        }
        joplin_action = joplin_button_actions.get(str(event.button.id or ""))
        if joplin_action is not None:
            agent_id = self.joplin_target_agent_id()
            if agent_id:
                await self.joplin_action_for_agent(agent_id, joplin_action)
            return
        if event.button.id == "campaign-refresh":
            if self.selected_agent_id:
                await self.load_operator_campaigns(self.selected_agent_id)
            return
        if event.button.id == "campaign-monitor":
            await self.monitor_selected_campaign()
            return
        if event.button.id == "campaign-report":
            await self.view_selected_campaign_report()
            return
        if event.button.id == "campaign-copy-joplin":
            await self.copy_selected_campaign_to_joplin()
            return
        if event.button.id == "start-operator":
            await self.start_operator_agent()
            return
        if event.button.id == "operator-start":
            await self.start_operator_agent()
            return
        if event.button.id == "operator-history":
            await self.show_selected_operator_history()
            return
        if event.button.id == "operator-resume":
            await self.resume_selected_operator()
            return
        if event.button.id == "operator-restart":
            await self.restart_selected_operator()
            return
        if event.button.id == "operator-stop":
            await self.stop_selected_operator()
            return
        if event.button.id == "operator-fork-prev":
            await self.cycle_selected_operator_fork(-1)
            return
        if event.button.id == "operator-fork-next":
            await self.cycle_selected_operator_fork(1)
            return
        if event.button.id == "operator-fork-review":
            await self.start_review_operator_fork()
            return
        if event.button.id == "operator-hide":
            agent_id = self.selected_operator_agent_id()
            if agent_id:
                await self.dismiss_selected_agent(agent_id=agent_id, delete_thread=False)
            return
        if event.button.id == "operator-purge":
            agent_id = self.selected_operator_agent_id()
            if agent_id:
                await self.dismiss_selected_agent(agent_id=agent_id, delete_thread=True)
            return
        if event.button.id == "star-agent":
            self.toggle_selected_agent_star()
            return
        if event.button.id == "toggle-hidden-agents":
            self.action_toggle_hidden_agents()
            return
        if event.button.id == "unhide-agent":
            await self.unhide_selected_agent()
            return
        if event.button.id == "hide-agent":
            await self.dismiss_selected_agent(delete_thread=False)
            return
        if event.button.id == "purge-agent":
            await self.dismiss_selected_agent(delete_thread=True)
            return

    async def send_input(self) -> None:
        if self.is_tmux_direct_enabled():
            focused_input = (
                self.focused
                if isinstance(self.focused, TextArea)
                and self.focused.id in {"message", "tmux-message"}
                else None
            )
            tmux_input = self.query_one_or_none("#tmux-message", TextArea)
            message_input = self.query_one_or_none("#message", TextArea)
            if focused_input is not None and focused_input.text.strip():
                await self.send_tmux_text_area_input(focused_input)
                return
            if tmux_input is not None and tmux_input.text.strip():
                await self.send_tmux_text_area_input(tmux_input)
                return
            if message_input is not None and message_input.text.strip():
                await self.send_tmux_text_area_input(message_input)
                return
            if tmux_input is not None:
                await self.send_tmux_text_area_input(tmux_input)
            return
        message_input = self.query_one("#message", TextArea)
        agent_id = self.sent_history_agent_id(message_input) or ""
        message = message_input.text.strip()
        if not message:
            return
        if not agent_id:
            if await self.execute_local_slash_command_from_input(message_input, message):
                return
            return
        if is_plan_toggle_message(message):
            toggled = await self.toggle_plan_mode_from_input(agent_id, via_tmux=False)
            if toggled:
                self.record_sent_message(agent_id, message)
                self.set_agent_draft_text(agent_id, message_input, "")
            return
        selection = parse_plan_selection_command(message)
        if selection is not None:
            sent_selection = await self.send_plan_selection(
                agent_id,
                selection,
                via_tmux=False,
            )
            if sent_selection:
                self.record_sent_message(agent_id, message)
                self.set_agent_draft_text(agent_id, message_input, "")
            return
        if await self.execute_local_slash_command_from_input(message_input, message):
            return
        routed_agent_id = await self.route_operator_prompt_to_fork(agent_id, message)
        if routed_agent_id is None:
            return
        agent_id = routed_agent_id
        if self.should_send_plan_prompt(agent_id, message):
            expanded_message = await self.expand_prompt_references(
                agent_id,
                message,
            )
            if expanded_message is None:
                return
            sent_plan = await self.send_plan_prompt(
                agent_id,
                expanded_message,
                via_tmux=False,
            )
            if sent_plan:
                self.record_sent_message(agent_id, message)
                self.clear_pending_slash_command(agent_id)
                self.set_agent_draft_text(agent_id, message_input, "")
            return
        pending_slash_commands = self.pending_slash_command_sequence_for_message(
            agent_id, message
        )
        expanded_message = await self.expand_prompt_references(agent_id, message)
        if expanded_message is None:
            return
        for pending_slash in pending_slash_commands:
            await self.queue_command(
                agent_id,
                "send_input",
                {"message": pending_slash},
            )
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": expanded_message},
        )
        self.clear_pending_slash_command(agent_id)
        self.record_sent_message(agent_id, message)
        self.set_agent_draft_text(agent_id, message_input, "")
        self.notify_queued_command(agent_id, "Input", command)
        await self.refresh_events()
        await self.load_thread(agent_id)

    def resize_message_input(self) -> None:
        message_input = self.query_one("#message", TextArea)
        if self.is_tiny_layout():
            message_input.styles.height = 1
            message_input.styles.min_height = 1
            message_input.styles.max_height = 1
            composer_inputs = self.query_one("#composer-inputs")
            composer_inputs.styles.height = 1
            composer_inputs.styles.min_height = 1
            composer_inputs.styles.max_height = 1
            composer = self.query_one("#composer")
            composer.styles.height = 6
            composer.styles.min_height = 6
            composer.styles.max_height = 6
            return
        line_count = max(1, message_input.text.count("\n") + 1)
        height = min(
            FOLLOW_UP_MAX_HEIGHT,
            max(FOLLOW_UP_MIN_HEIGHT, line_count + 2),
        )
        message_input.styles.height = height
        self.query_one("#composer-inputs").styles.height = height
        self.query_one("#composer").styles.height = height + 7

    async def send_tmux_input(self) -> None:
        message_input = self.query_one("#tmux-message", TextArea)
        await self.send_tmux_text_area_input(message_input)

    async def send_tmux_text_area_input(self, message_input: TextArea) -> None:
        agent_id = self.sent_history_agent_id(message_input) or ""
        message = message_input.text
        if not message.strip():
            return
        if not agent_id:
            if await self.execute_local_slash_command_from_input(
                message_input, message.strip()
            ):
                return
            return
        if is_plan_toggle_message(message):
            toggled = await self.toggle_plan_mode_from_input(agent_id, via_tmux=True)
            if toggled:
                self.record_sent_message(agent_id, message)
                self.set_agent_draft_text(agent_id, message_input, "")
                await self.load_tmux_capture(agent_id)
            return
        selection = parse_plan_selection_command(message)
        if selection is not None:
            sent_selection = await self.send_plan_selection(
                agent_id,
                selection,
                via_tmux=True,
            )
            if sent_selection:
                self.record_sent_message(agent_id, message)
                self.set_agent_draft_text(agent_id, message_input, "")
            return
        if await self.execute_local_slash_command_from_input(
            message_input, message.strip()
        ):
            return
        routed_agent_id = await self.route_operator_prompt_to_fork(agent_id, message)
        if routed_agent_id is None:
            return
        agent_id = routed_agent_id
        if self.should_send_plan_prompt(agent_id, message):
            expanded_message = await self.expand_prompt_references(
                agent_id,
                message,
            )
            if expanded_message is None:
                return
            sent_plan = await self.send_plan_prompt(
                agent_id,
                expanded_message,
                via_tmux=True,
            )
            if sent_plan:
                self.record_sent_message(agent_id, message)
                self.clear_pending_slash_command(agent_id)
                self.set_agent_draft_text(agent_id, message_input, "")
                await self.load_tmux_capture(agent_id)
            return
        pending_slash_commands = self.pending_slash_command_sequence_for_message(
            agent_id, message
        )
        expanded_message = await self.expand_prompt_references(agent_id, message)
        if expanded_message is None:
            return
        for pending_slash in pending_slash_commands:
            sent_slash = await self.send_text_to_tmux(agent_id, pending_slash)
            if not sent_slash:
                return
            await asyncio.sleep(SLASH_COMMAND_FOLLOWUP_DELAY_SECONDS)
        sent = await self.send_text_to_tmux(agent_id, expanded_message)
        if not sent:
            return
        self.clear_pending_slash_command(agent_id)
        self.record_sent_message(agent_id, message)
        await self.record_tmux_joplin_interaction(agent_id, expanded_message)
        self.set_agent_draft_text(agent_id, message_input, "")
        await self.load_tmux_capture(agent_id)

    def pending_slash_command_sequence_for_message(
        self, agent_id: str, message: str
    ) -> list[str]:
        command = self.pending_slash_command_by_agent.get(agent_id)
        body = message.strip()
        if not command or not body:
            return []
        if body.startswith("/"):
            return []
        if command == PLAN_SLASH_COMMAND:
            return [PLAN_SLASH_COMMAND]
        return [command]

    def clear_pending_slash_command(self, agent_id: str) -> None:
        self.pending_slash_command_by_agent.pop(agent_id, None)
        self.update_agent_title()

    def should_send_plan_prompt(self, agent_id: str, message: str) -> bool:
        command = self.pending_slash_command_by_agent.get(agent_id)
        return (
            command == PLAN_SLASH_COMMAND
            and bool(message.strip())
            and not message.strip().startswith("/")
        )

    async def toggle_plan_mode_from_input(
        self,
        agent_id: str,
        *,
        via_tmux: bool,
    ) -> bool:
        if self.plan_mode_state(agent_id) == "pending":
            self.pending_slash_command_by_agent.pop(agent_id, None)
            self.update_agent_title()
            self.notify(f"Plan mode canceled for {agent_id}; /plan was not sent.")
            return True
        return await self.toggle_sent_plan_mode(agent_id, via_tmux=via_tmux)

    async def toggle_sent_plan_mode(
        self,
        agent_id: str,
        *,
        via_tmux: bool | None = None,
    ) -> bool:
        use_tmux = (
            self.is_tmux_direct_enabled(agent_id)
            if via_tmux is None
            else via_tmux
        )
        if use_tmux:
            sent = await self.send_keys_to_tmux(agent_id, PLAN_SLASH_COMMAND)
            if not sent:
                return False
            await self.load_tmux_capture(agent_id)
        else:
            command = await self.queue_command(
                agent_id,
                "send_input",
                {"message": PLAN_SLASH_COMMAND},
            )
            self.notify_queued_command(agent_id, "Plan toggle", command)
            await self.refresh_events()
            await self.load_thread(agent_id)
        if agent_id in self.plan_mode_active_agent_ids:
            self.plan_mode_active_agent_ids.discard(agent_id)
            self.notify(f"Plan mode toggled off for {agent_id}.")
        else:
            self.plan_mode_active_agent_ids.add(agent_id)
            self.notify(f"Plan mode toggled on for {agent_id}.")
        self.pending_slash_command_by_agent.pop(agent_id, None)
        self.update_agent_title()
        self.render_agents()
        return True

    async def send_plan_prompt(
        self,
        agent_id: str,
        message: str,
        *,
        via_tmux: bool,
    ) -> bool:
        plan_prompt = render_plan_prompt(message)
        if via_tmux:
            sent_slash = await self.send_keys_to_tmux(agent_id, PLAN_SLASH_COMMAND)
            if not sent_slash:
                return False
            await asyncio.sleep(PLAN_MODE_FOLLOWUP_DELAY_SECONDS)
            sent_prompt = await self.send_text_to_tmux(agent_id, plan_prompt)
            if not sent_prompt:
                return False
            await self.record_tmux_joplin_interaction(agent_id, plan_prompt)
            self.notify(f"Plan prompt sent to tmux for {agent_id}.")
            self.plan_mode_active_agent_ids.add(agent_id)
            self.update_agent_title()
            self.render_agents()
            return True
        await self.queue_command(
            agent_id,
            "send_input",
            {"message": PLAN_SLASH_COMMAND},
        )
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": plan_prompt},
        )
        self.notify_queued_command(agent_id, "Plan prompt", command)
        await self.refresh_events()
        await self.load_thread(agent_id)
        self.plan_mode_active_agent_ids.add(agent_id)
        self.update_agent_title()
        self.render_agents()
        return True

    async def send_text_to_tmux(self, agent_id: str, message: str) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        pane = await self.resolve_tmux_send_pane(agent_id, status=status)
        if pane is None:
            return False
        try:
            await asyncio.to_thread(tmux_support.send_text, pane.pane_id, message)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: send failed ({exc})")
            return False
        return True

    async def send_keys_to_tmux(self, agent_id: str, message: str) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        pane = await self.resolve_tmux_send_pane(agent_id, status=status)
        if pane is None:
            return False
        try:
            await asyncio.to_thread(
                tmux_support.send_literal_keys,
                pane.pane_id,
                message,
            )
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: send failed ({exc})")
            return False
        return True

    async def send_key_to_tmux(self, agent_id: str, key: str) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        pane = await self.resolve_tmux_send_pane(agent_id, status=status)
        if pane is None:
            return False
        return await self.send_key_to_tmux_pane(pane.pane_id, key, status=status)

    async def send_key_to_tmux_pane(
        self,
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        try:
            await asyncio.to_thread(tmux_support.send_key, pane_id, key)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: send failed ({exc})")
            return False
        return True

    async def send_escape_to_operator_scope(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        if (
            agent is None
            or self.agent_type(agent) != OPERATOR_AGENT_TYPE
            or self.operator_role(agent) != OPERATOR_ROLE_ROOT
        ):
            return False
        status = self.query_one_or_none("#tmux-status", Static)
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: unavailable ({exc})")
            return False
        self.tmux_panes = panes
        logical_operator_id = self.logical_operator_id_for_agent(agent)
        targets: list[tuple[str, tmux_support.TmuxPane]] = []
        root_pane, _mode = self.resolve_tmux_pane(agent_id, panes)
        if root_pane is not None:
            targets.append((agent_id, root_pane))
        for fork, pane in self.operator_fork_pane_targets(logical_operator_id, panes):
            if not self.operator_fork_is_running(fork):
                continue
            fork_agent_id = str(fork.get("agent_id") or "").strip()
            if fork_agent_id:
                targets.append((fork_agent_id, pane))
        deduped: list[tuple[str, tmux_support.TmuxPane]] = []
        seen_panes: set[str] = set()
        for target_agent_id, pane in targets:
            if pane.pane_id in seen_panes:
                continue
            seen_panes.add(pane.pane_id)
            deduped.append((target_agent_id, pane))
        if not deduped:
            if status is not None:
                status.update(f"Tmux: no operator panes for {agent_id}")
            return False
        sent: list[str] = []
        failed: list[str] = []
        for _target_agent_id, pane in deduped:
            if await self.send_key_to_tmux_pane(pane.pane_id, "Escape", status=status):
                sent.append(pane.pane_id)
            else:
                failed.append(pane.pane_id)
        if sent:
            suffix = f"; failed {', '.join(failed)}" if failed else ""
            self.notify(
                f"Sent Escape to {len(sent)} operator pane(s) for {agent_id}{suffix}."
            )
            await self.load_tmux_capture(agent_id)
            return True
        return False

    async def send_text_to_tmux_pane(
        self,
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        try:
            await asyncio.to_thread(tmux_support.send_text, pane_id, message)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: send failed ({exc})")
            return False
        return True

    async def resolve_tmux_send_pane(
        self,
        agent_id: str,
        *,
        status: Static | None,
    ) -> tmux_support.TmuxPane | None:
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: unavailable ({exc})")
            return None
        self.tmux_panes = panes
        pane, mode = self.resolve_tmux_pane(agent_id, panes)
        if pane is None:
            if status is not None:
                if mode == "stale":
                    status.update(f"Tmux: stale target for {agent_id}")
                else:
                    status.update(f"Tmux: no pane for {agent_id}")
            return None
        return pane

    def selected_plan_option(self) -> PlanChoice | None:
        item = (
            self.thread_items.get(self.selected_thread_item_id)
            if self.selected_thread_item_id
            else None
        )
        options = self.plan_options_for_item(item)
        if self.selected_plan_option_index is None:
            return None
        if (
            self.selected_plan_option_index < 0
            or self.selected_plan_option_index >= len(options)
        ):
            return None
        return options[self.selected_plan_option_index]

    def selected_latest_plan_option(self) -> PlanChoice | None:
        report = (
            self.latest_report_by_agent.get(self.selected_agent_id)
            if self.selected_agent_id
            else None
        )
        options = self.plan_options_for_report(report)
        if self.selected_latest_plan_option_index is None:
            return None
        if (
            self.selected_latest_plan_option_index < 0
            or self.selected_latest_plan_option_index >= len(options)
        ):
            return None
        return options[self.selected_latest_plan_option_index]

    def plan_options_for_item(self, item: dict[str, Any] | None) -> list[PlanChoice]:
        if not item or item.get("kind") != "report":
            return []
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return plan_choices_from_value(metadata.get("plan_options"))

    def plan_options_for_report(self, report: dict[str, Any] | None) -> list[PlanChoice]:
        if not report:
            return []
        return plan_choices_from_value(report.get("plan_options"))

    def plan_choice_message(self, option: PlanChoice, notes: str = "") -> str:
        message = f"Selected plan option: {option.label.strip()}"
        clean_notes = notes.strip()
        if clean_notes:
            message = f"{message}\n\nOperator notes:\n{clean_notes}"
        return message

    def plan_choice_payload(self, option: PlanChoice, notes: str = "") -> dict[str, Any]:
        return {
            "message": self.plan_choice_message(option, notes),
            "plan_choice": option.payload(),
        }

    def current_thread_plan_options_for_agent(self, agent_id: str) -> list[PlanChoice]:
        if not self.selected_thread_item_id:
            return []
        item = self.thread_items.get(self.selected_thread_item_id)
        if not item:
            return []
        item_agent_id = str(item.get("agent_id") or "")
        if item_agent_id:
            if item_agent_id != agent_id:
                return []
        elif agent_id != self.selected_agent_id:
            return []
        thread = self.plan_options_for_item(item)
        return thread

    def current_plan_options_for_agent(self, agent_id: str) -> list[PlanChoice]:
        latest = self.plan_options_for_report(self.latest_report_by_agent.get(agent_id))
        thread = self.current_thread_plan_options_for_agent(agent_id)
        if self.active_agent_tab == "thread-tab" and thread:
            return thread
        return latest or thread

    def plan_option_for_selection(
        self,
        agent_id: str,
        selection: PlanSelection,
    ) -> PlanChoice | None:
        options = self.current_plan_options_for_agent(agent_id)
        selected_index = selection.index - 1
        if selected_index < 0 or selected_index >= len(options):
            return None
        return options[selected_index]

    async def send_native_plan_selection(
        self,
        agent_id: str,
        selection: PlanSelection,
        *,
        notify_missing: bool = True,
        allow_tmux_pane_fallback: bool = False,
    ) -> bool:
        label = CODEX_NATIVE_PLAN_SELECTOR_CHOICES.get(
            selection.index,
            f"option {selection.index}",
        )
        status = self.query_one_or_none("#tmux-status", Static)
        pane_id = await self.resolve_native_plan_selection_pane_id(
            agent_id,
            status=status,
            notify_missing=notify_missing and not allow_tmux_pane_fallback,
        )
        if pane_id is None:
            if not allow_tmux_pane_fallback:
                return False
            pane = await self.resolve_tmux_send_pane(agent_id, status=status)
            if pane is None:
                return False
            pane_id = pane.pane_id
        sent = await self.send_key_to_tmux_pane(
            pane_id,
            str(selection.index),
            status=status,
        )
        if not sent:
            return False
        self.tmux_plan_selector_agent_ids.discard(agent_id)
        self.tmux_plan_selector_pane_by_agent.pop(agent_id, None)
        self.tmux_plan_selector_indices_by_agent.pop(agent_id, None)
        visible_agent_id = self.tmux_agent_id_from_capture_key(self.tmux_visible_capture_key)
        if visible_agent_id and visible_agent_id != agent_id:
            self.tmux_plan_selector_agent_ids.discard(visible_agent_id)
            self.tmux_plan_selector_pane_by_agent.pop(visible_agent_id, None)
            self.tmux_plan_selector_indices_by_agent.pop(visible_agent_id, None)
        self.update_agent_title()
        self.render_agents()
        self.render_unseen_attention()
        if selection.notes:
            note_message = (
                f"Plan selection note for option {selection.index}:\n"
                f"{selection.notes}"
            )
            await asyncio.sleep(PLAN_MODE_FOLLOWUP_DELAY_SECONDS)
            note_sent = await self.send_text_to_tmux_pane(
                pane_id,
                note_message,
                status=status,
            )
            if note_sent:
                await self.record_tmux_joplin_interaction(agent_id, note_message)
            else:
                self.notify(
                    "Plan option was selected, but the trailing note could not be sent.",
                    severity="warning",
                )
        self.notify(f"Pressed {selection.index} ({label}) in Codex plan selector.")
        await asyncio.sleep(SLASH_COMMAND_FOLLOWUP_DELAY_SECONDS)
        await self.load_tmux_capture(agent_id)
        return True

    async def resolve_native_plan_selection_pane_id(
        self,
        agent_id: str,
        *,
        status: Static | None = None,
        notify_missing: bool = True,
    ) -> str | None:
        visible_pane_id = self.tmux_visible_plan_selector_pane_id()
        if visible_pane_id:
            return visible_pane_id
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: unavailable ({exc})")
            return None
        self.tmux_panes = panes
        stored_pane_id = self.tmux_plan_selector_pane_by_agent.get(agent_id)
        if stored_pane_id:
            stored_panes = [pane for pane in panes if pane.pane_id == stored_pane_id]
            stored_matches = await self.tmux_plan_selector_matches(stored_panes)
            if stored_matches:
                pane, displayed = stored_matches[0]
                self.update_tmux_plan_selector_state(
                    agent_id,
                    displayed,
                    pane_id=pane.pane_id,
                )
                return pane.pane_id
            self.update_tmux_plan_selector_state(agent_id, "")
        pane = await self.resolve_tmux_plan_selector_pane(agent_id, panes)
        if pane is not None:
            return pane.pane_id
        selector_matches = await self.tmux_plan_selector_matches(panes)
        if len(selector_matches) == 1:
            pane, displayed = selector_matches[0]
            self.update_tmux_plan_selector_state(
                agent_id,
                displayed,
                pane_id=pane.pane_id,
            )
            return pane.pane_id
        if len(selector_matches) > 1:
            labels = ", ".join(
                f"{pane.pane_id} {pane.target_label}" for pane, _ in selector_matches[:4]
            )
            extra = len(selector_matches) - 4
            if extra > 0:
                labels = f"{labels}, +{extra}"
            self.notify(
                f"Multiple Codex plan selectors are visible in tmux: {labels}. "
                "Select the target pane first.",
                severity="warning",
            )
            return None
        if notify_missing:
            self.notify(
                "No Codex native plan selector is visible in tmux for /plan selection.",
                severity="warning",
            )
        return None

    async def send_plan_selection(
        self,
        agent_id: str,
        selection: PlanSelection,
        *,
        via_tmux: bool,
    ) -> bool:
        options = self.current_plan_options_for_agent(agent_id)
        native_pending = self.tmux_native_plan_selector_pending(agent_id)
        if via_tmux or native_pending:
            sent_native = await self.send_native_plan_selection(
                agent_id,
                selection,
                notify_missing=not options,
                allow_tmux_pane_fallback=via_tmux,
            )
            if sent_native:
                return True
            if not options or native_pending:
                return False
        option = self.plan_option_for_selection(agent_id, selection)
        if option is None:
            self.notify(
                f"Plan option {selection.index} is not available for {agent_id}.",
                severity="warning",
            )
            return False
        message = self.plan_choice_message(option, selection.notes)
        if via_tmux:
            sent = await self.send_text_to_tmux(agent_id, message)
            if not sent:
                return False
            await self.record_tmux_joplin_interaction(agent_id, message)
            self.notify(f"Sent /plan:{selection.index} reply to Codex pane for {agent_id}.")
            await self.load_tmux_capture(agent_id)
            return True
        command = await self.queue_command(
            agent_id,
            "send_input",
            self.plan_choice_payload(option, selection.notes),
        )
        self.notify_queued_command(agent_id, "Plan choice", command)
        await self.refresh_events()
        await self.load_thread(agent_id)
        return True

    async def send_plan_choice(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        if not agent_id or self.selected_plan_option_index is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        await self.send_plan_selection(
            agent_id,
            PlanSelection(index=self.selected_plan_option_index + 1),
            via_tmux=False,
        )

    async def send_plan_choice_to_tmux(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        if not self.is_tmux_direct_enabled(agent_id):
            self.notify(
                f"Enable tmux direct mode for {agent_id} before sending to Codex pane.",
                severity="warning",
            )
            return
        if not agent_id or self.selected_plan_option_index is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        await self.send_plan_selection(
            agent_id,
            PlanSelection(index=self.selected_plan_option_index + 1),
            via_tmux=True,
        )

    async def send_latest_plan_choice(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        if not agent_id or self.selected_latest_plan_option_index is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        await self.send_plan_selection(
            agent_id,
            PlanSelection(index=self.selected_latest_plan_option_index + 1),
            via_tmux=False,
        )

    async def send_latest_plan_choice_to_tmux(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        if not self.is_tmux_direct_enabled(agent_id):
            self.notify(
                f"Enable tmux direct mode for {agent_id} before sending to Codex pane.",
                severity="warning",
            )
            return
        if not agent_id or self.selected_latest_plan_option_index is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        await self.send_plan_selection(
            agent_id,
            PlanSelection(index=self.selected_latest_plan_option_index + 1),
            via_tmux=True,
        )

    async def request_detail(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        command = await self.queue_command(
            agent_id,
            "request_detail",
            {"request": "Please provide the detailed response for operator review."},
        )
        self.query_one("#detail", TextArea).text = (
            f"Detail request queued for {agent_id}.\n"
            f"Command: {command['command_id']}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def send_escape_key(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        if self.is_tmux_direct_enabled(agent_id):
            sent_scope = await self.send_escape_to_operator_scope(agent_id)
            if sent_scope:
                return
            sent = await self.send_key_to_tmux(agent_id, "Escape")
            if not sent:
                return
            self.notify(f"Sent Escape to Codex pane for {agent_id}.")
            await self.load_tmux_capture(agent_id)
            return
        command = await self.queue_command(
            agent_id,
            "send_key",
            {
                "key": "escape",
                "request": (
                    "Send an Escape key event to the agent session if supported; "
                    "otherwise report that key injection is unavailable."
                ),
            },
        )
        self.query_one("#detail", TextArea).text = (
            f"Escape key request queued for {agent_id}.\n"
            f"Command: {command['command_id']}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify_queued_command(agent_id, "Escape", command)
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def send_ctrl_c_key(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        if not self.is_tmux_direct_enabled(agent_id):
            self.notify(
                f"Enable tmux direct mode for {agent_id} before sending Ctrl+C.",
                severity="warning",
            )
            return
        sent = await self.send_key_to_tmux(agent_id, "C-c")
        if not sent:
            return
        self.notify(f"Sent Ctrl+C to Codex pane for {agent_id}.")
        await self.load_tmux_capture(agent_id)

    async def ping_agent(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        command = await self.queue_command(
            agent_id,
            "ping",
            {
                "request": (
                    "Reply with a status='working' pong report, acknowledge this "
                    "ping, then start another bounded poll window."
                ),
                "restart_poll": True,
                "recommended_poll": {
                    "wait_seconds": 25,
                    "max_wait_seconds": 300,
                    "interval_seconds": 5,
                },
            },
        )
        self.query_one("#detail", TextArea).text = (
            f"Ping queued for {agent_id}.\n"
            f"Command: {command['command_id']}\n\n"
            "Ping only extends polling for agents using Agent PBX nohup mode.\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def mark_agent_working(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        agent = self.agents.get(agent_id)
        project = str((agent or {}).get("project") or "").strip()
        if not project:
            self.notify("Select a known agent before marking working.", severity="warning")
            return
        previous_status = str((agent or {}).get("status") or "-")
        previous_effective = str((agent or {}).get("effective_status") or previous_status)
        latest_report_id = str((agent or {}).get("latest_report_id") or "").strip()
        active_fork = self.active_operator_fork_for_caller(agent or {})
        metadata: dict[str, Any] = {"manual_unblock": True}
        detail_lines = [
            "The operator marked this agent working from the TUI to clear a stale "
            "terminal/problem status after verifying the session is no longer blocked.",
            "",
            f"Previous status: {previous_status}",
            f"Previous effective status: {previous_effective}",
        ]
        if latest_report_id:
            metadata["resolved_report_id"] = latest_report_id
            detail_lines.append(f"Resolved report: {latest_report_id}")
        if active_fork is not None:
            fork_metadata = (
                active_fork.get("metadata")
                if isinstance(active_fork.get("metadata"), dict)
                else {}
            )
            fork_agent_id = str(active_fork.get("agent_id") or "").strip()
            source_session_id = str(fork_metadata.get("source_codex_session_id") or "").strip()
            tmux_pane_id = str(fork_metadata.get("tmux_pane_id") or "").strip()
            if fork_agent_id:
                metadata["active_fork_agent_id"] = fork_agent_id
                detail_lines.append(f"Active fork agent: {fork_agent_id}")
            if source_session_id:
                metadata["active_source_codex_session_id"] = source_session_id
                detail_lines.append(f"Active fork source session: {source_session_id}")
            if tmux_pane_id:
                metadata["active_fork_tmux_pane_id"] = tmux_pane_id
                detail_lines.append(f"Active fork tmux pane: {tmux_pane_id}")
        detail = "\n".join(detail_lines)
        report = await self.create_agent_report(
            agent_id,
            {
                "project": project,
                "status": "working",
                "summary": "Agent marked working by operator",
                "detail": detail,
                "needs_input": False,
                "plan_options": [],
                "metadata": metadata,
            },
        )
        self.query_one("#detail", TextArea).text = (
            f"Marked {agent_id} working.\n"
            f"Report: {report['report_id']}\n\n"
            f"{detail}"
        )
        self.notify(f"Marked {agent_id} working.")
        await self.refresh_agents()
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def mark_agent_canceled(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        if not agent_id:
            return
        agent = self.agents.get(agent_id)
        project = str((agent or {}).get("project") or "").strip()
        if not project:
            self.notify("Select a known agent before marking canceled.", severity="warning")
            return
        previous_status = str((agent or {}).get("status") or "-")
        previous_effective = str((agent or {}).get("effective_status") or previous_status)
        detail = (
            "The operator marked this agent canceled from the TUI because the "
            "CLI session was cancelled or is no longer active.\n\n"
            f"Previous status: {previous_status}\n"
            f"Previous effective status: {previous_effective}"
        )
        report = await self.create_agent_report(
            agent_id,
            {
                "project": project,
                "status": "canceled",
                "summary": "Session marked canceled by operator",
                "detail": detail,
                "needs_input": False,
                "plan_options": [],
            },
        )
        self.query_one("#detail", TextArea).text = (
            f"Marked {agent_id} canceled.\n"
            f"Report: {report['report_id']}\n\n"
            f"{detail}"
        )
        self.notify(f"Marked {agent_id} canceled.")
        await self.refresh_agents()
        await self.refresh_events()
        await self.load_thread(agent_id)

    def focused_agent_table_id(self) -> str | None:
        try:
            focused = self.focused
        except ScreenStackError:
            focused = None
        if not isinstance(focused, DataTable):
            return None
        if focused.id == "agents":
            return self.agent_id_at_cursor()
        if focused.id == "operators":
            return self.operator_id_at_cursor()
        return None

    def selected_or_cursor_agent_id(self) -> str | None:
        focused_agent_id = self.focused_agent_table_id()
        if focused_agent_id:
            return focused_agent_id
        cursor_agent_id = self.agent_id_at_cursor()
        if cursor_agent_id:
            return cursor_agent_id
        cursor_operator_id = self.operator_id_at_cursor()
        if cursor_operator_id:
            return cursor_operator_id
        if self.selected_agent_id:
            return self.selected_agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            value = agent_input.value.strip()
            if value:
                return value
        return None

    def joplin_target_agent_id(
        self,
        *,
        focused: Widget | None = None,
    ) -> str | None:
        if focused is not None and isinstance(focused, DataTable):
            if focused.id == "agents":
                return self.agent_id_at_cursor()
            if focused.id == "operators":
                return self.operator_id_at_cursor()
        if focused is None:
            focused_agent_id = self.focused_agent_table_id()
            if focused_agent_id:
                return focused_agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            value = agent_input.value.strip()
            if value:
                return value
        if self.selected_agent_id:
            return self.selected_agent_id
        return None

    def palette_joplin_agent_id(self) -> str | None:
        agent_id = self.joplin_target_agent_id()
        if not agent_id:
            self.notify("Select an agent first.", severity="warning")
            return None
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        self.selected_agent_id = agent_id
        return agent_id

    def toggle_selected_agent_star(self) -> None:
        agent_id = self.selected_or_cursor_agent_id()
        if not agent_id:
            self.notify("Select an agent before starring it.", severity="warning")
            return
        starred = agent_id not in self.starred_agent_ids
        self.update_agent_star_state(agent_id, starred=starred, focus=True)
        self.queue_agent_star_sync(agent_id, starred)
        self.notify(f"{'Starred' if starred else 'Unstarred'} {agent_id}.")

    def update_agent_star_state(
        self,
        agent_id: str,
        *,
        starred: bool,
        starred_at: float | None = None,
        focus: bool = False,
    ) -> None:
        if starred:
            self.starred_agent_ids.add(agent_id)
        else:
            self.starred_agent_ids.discard(agent_id)
        agent = self.agents.get(agent_id)
        if agent is not None:
            agent["starred"] = starred
            agent["starred_at"] = starred_at if starred else None
        self.save_settings()
        self.render_agents()
        if focus:
            self.focus_agent_row(agent_id)

    def queue_agent_star_sync(self, agent_id: str, starred: bool) -> None:
        if not self.is_running:
            return
        self.run_worker(
            self.set_agent_starred_remote(agent_id, starred=starred),
            name=f"agent-star-{agent_id}",
            group=f"agent-star-{agent_id}",
            exclusive=True,
        )

    async def set_agent_starred_remote(self, agent_id: str, *, starred: bool) -> None:
        try:
            method = self.api_client().post if starred else self.api_client().delete
            response = await method(
                f"/v1/agents/{agent_id}/star",
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            agent = response.json()
        except Exception:
            return
        if isinstance(agent, dict):
            if agent_id not in self.agents:
                self.agents[agent_id] = agent
            self.update_agent_star_state(
                agent_id,
                starred=bool(agent.get("starred")),
                starred_at=float_value(agent.get("starred_at")),
            )

    async def unhide_selected_agent(self) -> None:
        agent_id = self.selected_or_cursor_agent_id()
        if not agent_id:
            self.notify("Select a hidden agent before unhiding it.", severity="warning")
            return
        agent = self.agents.get(agent_id)
        if agent is not None and not self.is_hidden_agent(agent):
            self.notify(f"{agent_id} is not hidden.", severity="warning")
            return
        try:
            restored = await self.unhide_agent(agent_id)
        except Exception as exc:
            self.notify(f"Unable to unhide {agent_id}: {exc}", severity="error")
            return
        if isinstance(restored, dict):
            self.agents[agent_id] = restored
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = f"Unhid {agent_id}.\n\nThe agent is visible in Agents again."
        self.notify(f"Unhid {agent_id}.")
        await self.refresh_agents()
        await self.refresh_events()

    async def dismiss_selected_agent(
        self,
        *,
        delete_thread: bool,
        agent_id: str | None = None,
        confirmed_operator_kill: bool = False,
    ) -> None:
        agent_id = agent_id or self.selected_or_cursor_agent_id()
        if not agent_id:
            self.notify("Select an agent before hiding it.", severity="warning")
            return
        pane_id = self.tui_owned_operator_pane_id(agent_id)
        if pane_id and not confirmed_operator_kill:
            self.push_screen(
                OperatorKillConfirmScreen(
                    agent_id=agent_id,
                    action="purge" if delete_thread else "hide",
                    delete_thread=delete_thread,
                )
            )
            return
        if pane_id and not await self.kill_tui_owned_operator_pane(agent_id):
            return
        try:
            await self.delete_agent(agent_id, delete_thread=delete_thread)
        except Exception as exc:
            self.notify(f"Unable to hide {agent_id}: {exc}", severity="error")
            return

        self.unseen_latest_agent_ids.discard(agent_id)
        self.latest_report_by_agent.pop(agent_id, None)
        self.workerbee_status_by_agent.pop(agent_id, None)
        self.pull_request_status_by_agent.pop(agent_id, None)
        self.pull_requests_by_agent.pop(agent_id, None)
        self.selected_pull_request_number_by_agent.pop(agent_id, None)
        self.issue_status_by_agent.pop(agent_id, None)
        self.issues_by_agent.pop(agent_id, None)
        self.selected_issue_number_by_agent.pop(agent_id, None)
        self.campaigns_by_operator.pop(agent_id, None)
        self.selected_campaign_id_by_operator.pop(agent_id, None)
        self.selected_campaign_report_id_by_operator.pop(agent_id, None)
        self.joplin_notes_by_agent.pop(agent_id, None)
        self.file_directory_entries_by_agent.pop(agent_id, None)
        self.active_agent_tab_by_agent.pop(agent_id, None)
        self.message_draft_by_agent.pop(agent_id, None)
        self.tmux_message_draft_by_agent.pop(agent_id, None)
        self.selected_thread_item_id_by_agent.pop(agent_id, None)
        self.tmux_liveness_by_agent.pop(agent_id, None)
        self.tmux_agent_targets.pop(agent_id, None)
        self.tmux_direct_agent_modes.pop(agent_id, None)
        self.tmux_manual_override_agent_ids.discard(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        if self.selected_agent_id == agent_id:
            self.selected_agent_id = None
            agent_input = self.query_one_or_none("#agent-id", Input)
            if agent_input is not None:
                agent_input.value = ""
            self.selected_thread_item_id = None
            self.selected_pull_request_number = None
            self.selected_issue_number = None
            self.thread_items = {}
            self.thread_order = []
            self.marked_thread_item_ids.clear()
            thread = self.query_one_or_none("#thread", DataTable)
            if thread is not None:
                thread.clear()
            thread_detail = self.query_one_or_none("#thread-detail", TextArea)
            if thread_detail is not None:
                thread_detail.text = ""
            if self.is_collapsed_layout():
                self.show_compact_home()

        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = (
                f"{'Purged' if delete_thread else 'Hidden'} {agent_id}.\n\n"
                + (
                    "Thread data was deleted. The agent will reappear if it "
                    "registers again."
                    if delete_thread
                    else "Thread data was retained. The agent will reappear "
                    "with its history if it registers again."
                )
            )
        self.notify(
            f"{'Purged' if delete_thread else 'Hidden'} {agent_id} from Agents."
        )
        await self.refresh_agents()
        await self.refresh_events()

    def next_operator_agent_id(self) -> str:
        index = 0
        while f"operator-{index}" in self.agents:
            index += 1
        return f"operator-{index}"

    def operator_fork_agent_id(
        self,
        logical_operator_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
        *,
        fork_track_id: str | None = None,
    ) -> str:
        resolved_track_id = self.normalize_operator_fork_track_id(fork_track_id)
        track_suffix = (
            ""
            if resolved_track_id == DEFAULT_OPERATOR_FORK_TRACK_ID
            else f"-{resolved_track_id}"
        )
        base = slugify(
            f"{logical_operator_id}-fork-{source_caller_agent_id}{track_suffix}"
        )[:96]
        digest_key = (
            f"{logical_operator_id}:{source_caller_agent_id}:"
            f"{source_codex_session_id}"
        )
        if resolved_track_id != DEFAULT_OPERATOR_FORK_TRACK_ID:
            digest_key = f"{digest_key}:{resolved_track_id}"
        digest = short_stable_hash(digest_key)
        return f"{base}-{digest}"

    def normalize_operator_fork_track_id(self, fork_track_id: str | None) -> str:
        normalized = slugify(fork_track_id or DEFAULT_OPERATOR_FORK_TRACK_ID).lower()
        return normalized[:80] or DEFAULT_OPERATOR_FORK_TRACK_ID

    def normalize_operator_fork_label(self, value: str | None, *, default: str) -> str:
        normalized = slugify(value or default).lower()
        return normalized[:80] or default

    def selected_caller_agent_id_for_fork(self) -> str | None:
        focused_agent_id = self.focused_agent_table_id()
        if focused_agent_id and focused_agent_id in self.agents:
            if self.agent_type(self.agents[focused_agent_id]) == CALLER_AGENT_TYPE:
                return focused_agent_id
        cursor_agent_id = self.agent_id_at_cursor()
        if cursor_agent_id and cursor_agent_id in self.agents:
            if self.agent_type(self.agents[cursor_agent_id]) == CALLER_AGENT_TYPE:
                return cursor_agent_id
        if self.selected_agent_id and self.selected_agent_id in self.agents:
            if self.agent_type(self.agents[self.selected_agent_id]) == CALLER_AGENT_TYPE:
                return self.selected_agent_id
        return None

    def operator_fork_start_blocker(self, source_caller_agent_id: str) -> str | None:
        caller = self.agents.get(source_caller_agent_id)
        if caller is None:
            return f"Caller {source_caller_agent_id} is not loaded."
        caller_metadata = (
            caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        )
        source_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        caller_cwd = str(caller_metadata.get("cwd") or "").strip()
        if not source_session_id:
            return f"{source_caller_agent_id} is missing metadata.codex_session_id."
        if not caller_cwd:
            return f"{source_caller_agent_id} is missing metadata.cwd."
        return None

    def operator_bootstrap_prompt(
        self,
        agent_id: str,
        cwd: str,
        *,
        logical_operator_id: str | None = None,
        source_caller_agent_id: str | None = None,
        source_codex_session_id: str | None = None,
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        source_cwd: str | None = None,
        work_root: str | None = None,
    ) -> str:
        role = OPERATOR_ROLE_FORK if source_caller_agent_id else OPERATOR_ROLE_ROOT
        logical_id = logical_operator_id or agent_id
        resolved_track_id = self.normalize_operator_fork_track_id(fork_track_id)
        resolved_purpose = self.normalize_operator_fork_label(
            fork_purpose,
            default=(
                DEFAULT_OPERATOR_FORK_PURPOSE
                if resolved_track_id == DEFAULT_OPERATOR_FORK_TRACK_ID
                else REVIEW_OPERATOR_FORK_PURPOSE
            ),
        )
        resolved_access_mode = self.normalize_operator_fork_label(
            access_mode,
            default=(
                DEFAULT_OPERATOR_FORK_ACCESS_MODE
                if resolved_purpose == DEFAULT_OPERATOR_FORK_PURPOSE
                else REVIEW_OPERATOR_FORK_ACCESS_MODE
            ),
        )
        extra = []
        if source_caller_agent_id:
            extra.extend(
                [
                    f"- metadata.operator_role: \"{OPERATOR_ROLE_FORK}\"",
                    f"- metadata.logical_operator_id: {logical_id}",
                    f"- metadata.source_caller_agent_id: {source_caller_agent_id}",
                    f"- metadata.source_codex_session_id: {source_codex_session_id or ''}",
                    f"- metadata.fork_track_id: {resolved_track_id}",
                    f"- metadata.fork_purpose: {resolved_purpose}",
                    f"- metadata.access_mode: {resolved_access_mode}",
                    f"- metadata.source_cwd: {source_cwd or cwd}",
                    f"- metadata.work_root: {work_root or cwd}",
                    "",
                    "This session is a fork of the caller's Codex session. "
                    "Keep work for this caller isolated in this fork and report "
                    "through Agent PBX for the logical operator to review. "
                    "This visible Agent PBX tmux pane is the fork; do not spawn "
                    "or use Codex internal subagents such as multi_agent_v1 for "
                    "caller work.",
                ]
            )
            if resolved_purpose == REVIEW_OPERATOR_FORK_PURPOSE:
                extra.extend(
                    [
                        "",
                        "This is a review fork. Treat the caller source directory "
                        "as read-only. Put any generated notes, patches, logs, or "
                        "scratch files under metadata.work_root only. If review "
                        "findings require source edits, escalate them through "
                        "pbx_operator_route_review_escalation instead of editing "
                        "the caller project directly.",
                    ]
                )
        else:
            extra.extend(
                [
                    f"- metadata.operator_role: \"{OPERATOR_ROLE_ROOT}\"",
                    "",
                    "This root operator session is the persistent coordination "
                    "pane for the logical operator. Keep campaign state, planning, "
                    "and human follow-up handling here. Caller-specific execution "
                    "runs only in visible Agent PBX fork sessions under this "
                    "operator; use @caller references and operator campaign tools "
                    "to target one or more forks without abandoning this root "
                    "session. The root operator must not implement caller repo "
                    "changes directly. Do not spawn or use Codex internal subagents such as "
                    "multi_agent_v1 for caller work; if PBX fork delivery is "
                    "unavailable, mark the assignment blocked instead. Keep the root "
                    "turn active while campaign assignments are running and "
                    "periodically recheck campaign state until terminal.",
                ]
            )
        return "\n".join(
            [
                "Use Agent PBX as an operator agent.",
                "The TUI launched this Codex session with Agent PBX MCP "
                "configured and AGENT_PBX_TOKEN in the process environment.",
                "These launch-specific operator identity instructions override "
                "repository AGENTS.md guidance about caller agent registration.",
                "",
                "Register this session with:",
                f"- agent_id: {agent_id}",
                "- project: agent-pbx-operator",
                "- agent_type: operator",
                f"- metadata.cwd: {cwd}",
                '- metadata.pbx_mode: "report"',
                '- metadata.agent_type: "operator"',
                *extra,
                "",
                "Call pbx_operator_runbook before starting campaign work. "
                "Use operator campaign tools to dispatch caller assignments, "
                "review caller threads, follow up until criteria are met or "
                "blocked, report each assignment state, and finish campaigns.",
            ]
        )

    def operator_cwd(self) -> str:
        return os.getenv("AGENT_PBX_TUI_OPERATOR_CWD", os.getcwd()).strip() or os.getcwd()

    def operator_codex_command(self) -> str:
        return os.getenv("AGENT_PBX_TUI_CODEX_BIN", "codex").strip() or "codex"

    def operator_tmux_session_name(self) -> str:
        return (
            os.getenv("AGENT_PBX_TUI_OPERATOR_TMUX_SESSION", DEFAULT_OPERATOR_TMUX_SESSION)
            .strip()
            or DEFAULT_OPERATOR_TMUX_SESSION
        )

    def operator_launch_env(
        self,
        *,
        agent_id: str,
        cwd: str,
        mcp_url: str,
        operator_role: str = OPERATOR_ROLE_ROOT,
        logical_operator_id: str | None = None,
        source_caller_agent_id: str | None = None,
        source_codex_session_id: str | None = None,
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        source_cwd: str | None = None,
        work_root: str | None = None,
    ) -> dict[str, str]:
        logical_id = logical_operator_id or agent_id
        resolved_track_id = self.normalize_operator_fork_track_id(fork_track_id)
        resolved_purpose = self.normalize_operator_fork_label(
            fork_purpose,
            default=(
                DEFAULT_OPERATOR_FORK_PURPOSE
                if resolved_track_id == DEFAULT_OPERATOR_FORK_TRACK_ID
                else REVIEW_OPERATOR_FORK_PURPOSE
            ),
        )
        resolved_access_mode = self.normalize_operator_fork_label(
            access_mode,
            default=(
                DEFAULT_OPERATOR_FORK_ACCESS_MODE
                if resolved_purpose == DEFAULT_OPERATOR_FORK_PURPOSE
                else REVIEW_OPERATOR_FORK_ACCESS_MODE
            ),
        )
        env = {
            AGENT_PBX_SERVER_URL_ENV: self.server,
            AGENT_PBX_MCP_URL_ENV: mcp_url,
            "AGENT_PBX_AGENT_ID": agent_id,
            "AGENT_PBX_AGENT_TYPE": OPERATOR_AGENT_TYPE,
            "AGENT_PBX_AGENT_PROJECT": "agent-pbx-operator",
            "AGENT_PBX_PBX_MODE": PBX_REPORT_MODE,
            "AGENT_PBX_OPERATOR_ID": agent_id,
            "AGENT_PBX_OPERATOR_ROLE": operator_role,
            "AGENT_PBX_LOGICAL_OPERATOR_ID": logical_id,
            "AGENT_PBX_OPERATOR_CWD": cwd,
            "AGENT_PBX_CWD": cwd,
        }
        if operator_role == OPERATOR_ROLE_FORK:
            env["AGENT_PBX_OPERATOR_FORK_TRACK_ID"] = resolved_track_id
            env["AGENT_PBX_OPERATOR_FORK_PURPOSE"] = resolved_purpose
            env["AGENT_PBX_OPERATOR_ACCESS_MODE"] = resolved_access_mode
        if source_caller_agent_id:
            env["AGENT_PBX_SOURCE_CALLER_AGENT_ID"] = source_caller_agent_id
        if source_codex_session_id:
            env["AGENT_PBX_SOURCE_CODEX_SESSION_ID"] = source_codex_session_id
        if source_cwd:
            env["AGENT_PBX_SOURCE_CWD"] = source_cwd
        if work_root:
            env["AGENT_PBX_OPERATOR_WORK_ROOT"] = work_root
        if self.token:
            env[AGENT_PBX_TOKEN_ENV] = self.token
        return env

    async def ensure_operator_auth_ready(self) -> bool:
        try:
            response = await self.api_client().get(
                "/v1/auth/check",
                headers=auth_headers(self.token),
            )
        except Exception as exc:
            self.notify(f"Unable to verify Agent PBX auth: {exc}", severity="error")
            return False
        if response.status_code in {401, 403}:
            if not self.token:
                self.notify(
                    "Agent PBX token is required to start an operator.",
                    severity="error",
                )
            else:
                self.notify("Agent PBX token was rejected.", severity="error")
            return False
        try:
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Unable to verify Agent PBX auth: {exc}", severity="error")
            return False
        return True

    async def configure_operator_codex_mcp(
        self,
        *,
        codex_command: str,
        mcp_url: str,
    ) -> None:
        await asyncio.to_thread(configure_codex_mcp, codex_command, mcp_url)

    def codex_home_dir(self) -> Path:
        configured = os.getenv("CODEX_HOME", "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path.home() / ".codex"

    def operator_resume_command(self, codex_command: str, session_id: str) -> str:
        command_parts = shlex.split(codex_command) if codex_command.strip() else ["codex"]
        return shlex.join([*command_parts, "resume", session_id])

    def operator_fork_command(
        self,
        codex_command: str,
        source_codex_session_id: str,
        prompt: str,
        *,
        cd: str | None = None,
        sandbox: str | None = None,
        config_overrides: Iterable[str] = (),
    ) -> str:
        command_parts = shlex.split(codex_command) if codex_command.strip() else ["codex"]
        fork_parts = [*command_parts, "fork"]
        if cd:
            fork_parts.extend(["--cd", cd])
        if sandbox:
            fork_parts.extend(["--sandbox", sandbox])
        for override in config_overrides:
            fork_parts.extend(["-c", override])
        fork_parts.extend([source_codex_session_id, prompt])
        return shlex.join(fork_parts)

    def operator_root_metadata(
        self,
        agent_id: str,
        *,
        cwd: str,
        codex_command: str,
        mcp_url: str,
        session_name: str,
        tmux_pane_id: str | None = None,
        resumed_codex_session_id: str | None = None,
        operator_session_history: list[dict[str, Any]] | None = None,
        default_source_caller_agent_id: str | None = None,
        default_source_caller_project: str | None = None,
        default_source_codex_session_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": OPERATOR_ROLE_ROOT,
            "pbx_mode": PBX_REPORT_MODE,
            "cwd": cwd,
            "logical_only": False,
            "launched_by": "agent-pbx-tui",
            "server_url": self.server,
            "mcp_url": mcp_url,
            "token_env": AGENT_PBX_TOKEN_ENV,
            "tmux_session": session_name,
            "codex_command": codex_command,
            "default_source_caller_agent_id": default_source_caller_agent_id or "",
            "default_source_caller_project": default_source_caller_project or "",
            "default_source_codex_session_id": default_source_codex_session_id or "",
        }
        if tmux_pane_id:
            metadata["tmux_pane_id"] = tmux_pane_id
        if resumed_codex_session_id:
            metadata["last_resume_codex_session_id"] = resumed_codex_session_id
        if operator_session_history is not None:
            metadata["operator_session_history"] = operator_session_history
        return metadata

    async def register_operator_root(
        self,
        agent_id: str,
        *,
        cwd: str,
        codex_command: str,
        mcp_url: str,
        session_name: str,
        tmux_pane_id: str | None = None,
        resumed_codex_session_id: str | None = None,
        operator_session_history: list[dict[str, Any]] | None = None,
        default_source_caller_agent_id: str | None = None,
        default_source_caller_project: str | None = None,
        default_source_codex_session_id: str | None = None,
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            "/v1/agents/register",
            json={
                "agent_id": agent_id,
                "project": "agent-pbx-operator",
                "name": agent_id,
                "agent_type": OPERATOR_AGENT_TYPE,
                "pbx_active": True,
                "metadata": self.operator_root_metadata(
                    agent_id,
                    cwd=cwd,
                    codex_command=codex_command,
                    mcp_url=mcp_url,
                    session_name=session_name,
                    tmux_pane_id=tmux_pane_id,
                    resumed_codex_session_id=resumed_codex_session_id,
                    operator_session_history=operator_session_history,
                    default_source_caller_agent_id=default_source_caller_agent_id,
                    default_source_caller_project=default_source_caller_project,
                    default_source_codex_session_id=default_source_codex_session_id,
                ),
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        agent = response.json()
        if isinstance(agent, dict):
            self.agents[agent_id] = agent
            return agent
        raise RuntimeError("operator root registration returned a non-object")

    def operator_metadata_for(self, agent_id: str) -> dict[str, Any]:
        agent = self.agents.get(agent_id) or {}
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        return dict(metadata)

    def operator_session_history_entries(self, agent_id: str) -> list[dict[str, Any]]:
        metadata = self.operator_metadata_for(agent_id)
        entries: list[dict[str, Any]] = []
        for key in (
            "codex_session_id",
            "codex_thread_id",
            "last_resume_codex_session_id",
        ):
            session_id = str(metadata.get(key) or "").strip()
            if session_id:
                entries.append(
                    {
                        "session_id": session_id,
                        "timestamp": float_value(metadata.get("last_seen_at")) or 0.0,
                        "source": f"metadata.{key}",
                    }
                )
        raw_history = metadata.get("operator_session_history")
        if isinstance(raw_history, list):
            for item in raw_history:
                if isinstance(item, str):
                    session_id = item.strip()
                    if session_id:
                        entries.append(
                            {
                                "session_id": session_id,
                                "timestamp": 0.0,
                                "source": "metadata.operator_session_history",
                            }
                        )
                    continue
                if not isinstance(item, dict):
                    continue
                session_id = str(item.get("session_id") or "").strip()
                if not session_id:
                    continue
                entries.append(
                    {
                        "session_id": session_id,
                        "timestamp": float_value(item.get("timestamp")) or 0.0,
                        "source": str(item.get("source") or "metadata.operator_session_history"),
                        "path": str(item.get("path") or ""),
                        "summary": str(item.get("summary") or ""),
                    }
                )
        return self.dedupe_operator_session_entries(entries)

    def dedupe_operator_session_entries(
        self,
        entries: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_session: dict[str, dict[str, Any]] = {}
        for entry in entries:
            session_id = str(entry.get("session_id") or "").strip()
            if not session_id:
                continue
            timestamp = float_value(entry.get("timestamp")) or 0.0
            existing = by_session.get(session_id)
            if existing is None or timestamp >= (float_value(existing.get("timestamp")) or 0.0):
                by_session[session_id] = {**entry, "session_id": session_id, "timestamp": timestamp}
        return sorted(
            by_session.values(),
            key=lambda item: (
                float_value(item.get("timestamp")) or 0.0,
                str(item.get("session_id") or ""),
            ),
            reverse=True,
        )

    def current_operator_session_ids(self, agent_id: str) -> set[str]:
        metadata = self.operator_metadata_for(agent_id)
        session_ids: set[str] = set()
        for key in (
            "codex_session_id",
            "codex_thread_id",
            "last_resume_codex_session_id",
        ):
            value = str(metadata.get(key) or "").strip()
            if value:
                session_ids.add(value)
        return session_ids

    def codex_history_summaries(self, codex_home: Path) -> dict[str, tuple[float, str]]:
        path = codex_home / "history.jsonl"
        if not path.is_file():
            return {}
        summaries: dict[str, tuple[float, str]] = {}
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    session_id = str(payload.get("session_id") or "").strip()
                    if not session_id:
                        continue
                    timestamp = float_value(payload.get("ts")) or 0.0
                    text = str(payload.get("text") or "").strip()
                    summary = text.splitlines()[0][:180] if text else ""
                    existing = summaries.get(session_id)
                    if existing is None or timestamp >= existing[0]:
                        summaries[session_id] = (timestamp, summary)
        except OSError:
            return summaries
        return summaries

    def codex_session_file_id(self, path: Path) -> str:
        match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", path.name)
        return match.group(1) if match else ""

    def read_codex_session_meta(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                line = handle.readline()
        except OSError:
            return {}
        if not line:
            return {}
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict) or payload.get("type") != "session_meta":
            return {}
        session = payload.get("payload")
        return session if isinstance(session, dict) else {}

    def operator_session_file_matches(self, path: Path, agent_id: str) -> bool:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    payload = item.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if self.codex_record_registers_operator(payload, agent_id):
                        return True
                    if self.codex_record_contains_operator_bootstrap(payload, agent_id):
                        return True
        except OSError:
            return False
        return False

    def codex_record_registers_operator(
        self,
        payload: dict[str, Any],
        agent_id: str,
    ) -> bool:
        tool_name = str(payload.get("name") or "").strip()
        arguments: Any = payload.get("arguments")
        if payload.get("type") == "mcp_tool_call_end":
            invocation = payload.get("invocation")
            if not isinstance(invocation, dict):
                return False
            tool_name = str(invocation.get("tool") or "").strip()
            arguments = invocation.get("arguments")
        if tool_name != "pbx_register_agent":
            return False
        if isinstance(arguments, str):
            try:
                decoded = json.loads(arguments)
            except json.JSONDecodeError:
                decoded = {}
            arguments = decoded
        if not isinstance(arguments, dict):
            return False
        return (
            str(arguments.get("agent_id") or "").strip() == agent_id
            and str(arguments.get("project") or "").strip() == "agent-pbx-operator"
            and str(arguments.get("agent_type") or "").strip() == OPERATOR_AGENT_TYPE
        )

    def codex_record_contains_operator_bootstrap(
        self,
        payload: dict[str, Any],
        agent_id: str,
    ) -> bool:
        messages: list[str] = []
        if payload.get("type") == "user_message":
            messages.append(str(payload.get("message") or ""))
        elif payload.get("type") == "message" and payload.get("role") == "user":
            content = payload.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        messages.append(str(item.get("text") or item.get("input_text") or ""))
        for message in messages:
            if (
                "Use Agent PBX as an operator agent." in message
                and "Register this session with:" in message
                and f"- agent_id: {agent_id}" in message
                and "- agent_type: operator" in message
            ):
                return True
        return False

    def local_operator_codex_session_entries(
        self,
        agent_id: str,
        *,
        max_files: int = 500,
    ) -> list[dict[str, Any]]:
        codex_home = self.codex_home_dir()
        sessions_dir = codex_home / "sessions"
        if not sessions_dir.is_dir():
            return []
        metadata = self.operator_metadata_for(agent_id)
        operator_cwd = str(metadata.get("cwd") or self.operator_cwd()).strip()
        normalized_operator_cwd = (
            str(Path(operator_cwd).expanduser().resolve(strict=False))
            if operator_cwd
            else ""
        )
        summaries = self.codex_history_summaries(codex_home)
        candidates: list[tuple[float, Path]] = []
        for path in sessions_dir.rglob("*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
        entries: list[dict[str, Any]] = []
        for mtime, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
            session_meta = self.read_codex_session_meta(path)
            session_id = str(session_meta.get("id") or self.codex_session_file_id(path)).strip()
            if not session_id:
                continue
            session_cwd = str(session_meta.get("cwd") or "").strip()
            if normalized_operator_cwd and session_cwd:
                normalized_session_cwd = str(
                    Path(session_cwd).expanduser().resolve(strict=False)
                )
                if normalized_session_cwd != normalized_operator_cwd:
                    continue
            if not self.operator_session_file_matches(path, agent_id):
                continue
            history_timestamp, summary = summaries.get(session_id, (0.0, ""))
            entries.append(
                {
                    "session_id": session_id,
                    "timestamp": history_timestamp or mtime,
                    "source": "codex.sessions",
                    "path": str(path),
                    "summary": summary,
                }
            )
        return self.dedupe_operator_session_entries(entries)

    def operator_session_candidates(self, agent_id: str) -> list[OperatorSessionCandidate]:
        raw_entries = [
            *self.operator_session_history_entries(agent_id),
            *self.local_operator_codex_session_entries(agent_id),
        ]
        return [
            OperatorSessionCandidate(
                session_id=str(entry.get("session_id") or ""),
                timestamp=float_value(entry.get("timestamp")) or 0.0,
                source=str(entry.get("source") or "unknown"),
                path=str(entry.get("path") or ""),
                summary=str(entry.get("summary") or ""),
            )
            for entry in self.dedupe_operator_session_entries(raw_entries)
        ]

    def operator_resume_target(
        self,
        agent_id: str,
        candidates: list[OperatorSessionCandidate],
    ) -> OperatorSessionCandidate | None:
        if not candidates:
            return None
        current_session_ids = self.current_operator_session_ids(agent_id)
        for candidate in candidates:
            if candidate.session_id in current_session_ids:
                return candidate
        if (
            not current_session_ids
            and self.tmux_agent_targets.get(agent_id)
            and len(candidates) > 1
        ):
            return candidates[1]
        for candidate in candidates:
            if candidate.session_id not in current_session_ids:
                return candidate
        if self.tmux_agent_targets.get(agent_id) and len(candidates) > 1:
            return candidates[1]
        return candidates[0]

    def operator_session_history_metadata(
        self,
        agent_id: str,
        *,
        include: Iterable[OperatorSessionCandidate | dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        entries = self.operator_session_history_entries(agent_id)
        for item in include:
            if isinstance(item, OperatorSessionCandidate):
                entries.append(
                    {
                        "session_id": item.session_id,
                        "timestamp": item.timestamp,
                        "source": item.source,
                        "path": item.path,
                        "summary": item.summary,
                    }
                )
            elif isinstance(item, dict):
                entries.append(dict(item))
        trimmed = self.dedupe_operator_session_entries(entries)[:20]
        return [
            {
                key: value
                for key, value in entry.items()
                if key in {"session_id", "timestamp", "source", "path", "summary"} and value
            }
            for entry in trimmed
        ]

    def format_operator_session_history(
        self,
        agent_id: str,
        candidates: list[OperatorSessionCandidate],
    ) -> str:
        resume_target = self.operator_resume_target(agent_id, candidates)
        current_session_ids = self.current_operator_session_ids(agent_id)
        lines = [
            f"Operator Session History: {agent_id}",
            "",
        ]
        if not candidates:
            lines.append("No local Codex sessions were found for this operator.")
            return "\n".join(lines)
        if resume_target is not None:
            lines.extend(
                [
                    f"Resume target: `{resume_target.session_id}`",
                    "",
                ]
            )
        for index, candidate in enumerate(candidates, start=1):
            marker = " current" if candidate.session_id in current_session_ids else ""
            created = (
                datetime.fromtimestamp(candidate.timestamp, timezone.utc).isoformat()
                if candidate.timestamp
                else "unknown time"
            )
            lines.append(f"{index}. `{candidate.session_id}`{marker}")
            lines.append(f"   source: {candidate.source}; updated: {created}")
            if candidate.summary:
                lines.append(f"   latest prompt: {candidate.summary}")
            if candidate.path:
                lines.append(f"   file: {candidate.path}")
        return "\n".join(lines)

    async def show_selected_operator_history(self) -> None:
        agent_id = self.selected_operator_agent_id()
        if not agent_id:
            return
        candidates = await asyncio.to_thread(self.operator_session_candidates, agent_id)
        resume_target = self.operator_resume_target(agent_id, candidates)
        self.push_screen(
            OperatorHistoryScreen(
                agent_id=agent_id,
                candidates=candidates,
                resume_target=resume_target,
            )
        )
        self.notify(f"Found {len(candidates)} operator session candidate(s) for {agent_id}.")

    async def refresh_operator_history_screen(
        self,
        screen: OperatorHistoryScreen,
    ) -> None:
        candidates = await asyncio.to_thread(
            self.operator_session_candidates,
            screen.agent_id,
        )
        screen.update_candidates(
            candidates,
            self.operator_resume_target(screen.agent_id, candidates),
        )
        self.notify(
            f"Refreshed {len(candidates)} operator session candidate(s) for "
            f"{screen.agent_id}."
        )

    async def record_operator_session_history(
        self,
        agent_id: str,
        *,
        include: Iterable[OperatorSessionCandidate | dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        history = self.operator_session_history_metadata(agent_id, include=include)
        agent = self.agents.get(agent_id) or {"agent_id": agent_id}
        metadata = self.operator_metadata_for(agent_id)
        payload_metadata = {**metadata, "operator_session_history": history}
        response = await self.api_client().post(
            "/v1/agents/register",
            json={
                "agent_id": agent_id,
                "project": str(agent.get("project") or "agent-pbx-operator"),
                "name": str(agent.get("name") or agent_id),
                "agent_type": OPERATOR_AGENT_TYPE,
                "pbx_active": bool(agent.get("pbx_active", True)),
                "metadata": payload_metadata,
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        updated = response.json()
        if isinstance(updated, dict):
            self.agents[agent_id] = updated
        return history

    async def live_operator_root_pane_id(self, agent_id: str) -> str | None:
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception:
            return None
        agent = self.agents.get(
            agent_id,
            {"agent_id": agent_id, "agent_type": OPERATOR_AGENT_TYPE},
        )
        matches = [
            pane
            for pane in panes
            if self.tmux_pane_allowed_for_agent(agent, pane)
        ]
        saved = self.saved_tmux_target_pane_for_agent(agent_id, agent, matches)
        if saved is not None:
            return saved.pane_id
        if len(matches) == 1:
            return matches[0].pane_id
        if len(matches) > 1:
            pane_list = ", ".join(
                f"{pane.pane_id} ({pane.target_label})" for pane in matches
            )
            raise RuntimeError(
                f"multiple live tmux panes match {agent_id}: {pane_list}; "
                "select the intended pane or prune stale operator panes before reuse"
            )
        return None

    async def ensure_operator_root_from_tui(
        self,
        *,
        agent_id: str,
        cwd: str,
        codex_command: str,
        mcp_url: str,
        session_name: str,
        source_caller_agent_id: str | None = None,
    ) -> tuple[dict[str, Any], str, bool]:
        source_caller = self.agents.get(source_caller_agent_id or "")
        source_metadata = (
            source_caller.get("metadata")
            if isinstance(source_caller, dict)
            and isinstance(source_caller.get("metadata"), dict)
            else {}
        )
        agent = await self.register_operator_root(
            agent_id,
            cwd=cwd,
            codex_command=codex_command,
            mcp_url=mcp_url,
            session_name=session_name,
            default_source_caller_agent_id=source_caller_agent_id,
            default_source_caller_project=(
                str(source_caller.get("project") or "")
                if isinstance(source_caller, dict)
                else None
            ),
            default_source_codex_session_id=str(
                source_metadata.get("codex_session_id") or ""
            ),
        )
        pane_id = await self.live_operator_root_pane_id(agent_id)
        if pane_id:
            self.tmux_agent_targets[agent_id] = pane_id
            self.tmux_detached_agent_ids.discard(agent_id)
            self.tmux_direct_agent_modes[agent_id] = True
            metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            if metadata.get("tmux_pane_id") != pane_id:
                agent = await self.register_operator_root(
                    agent_id,
                    cwd=cwd,
                    codex_command=codex_command,
                    mcp_url=mcp_url,
                    session_name=session_name,
                    tmux_pane_id=pane_id,
                    default_source_caller_agent_id=source_caller_agent_id,
                    default_source_caller_project=(
                        str(source_caller.get("project") or "")
                        if isinstance(source_caller, dict)
                        else None
                    ),
                    default_source_codex_session_id=str(
                        source_metadata.get("codex_session_id") or ""
                    ),
                )
            return agent, pane_id, False

        pane_id = await asyncio.to_thread(
            tmux_support.launch_pane,
            session_name=session_name,
            window_name=agent_id,
            command=codex_command,
            cwd=cwd,
            env=self.operator_launch_env(
                agent_id=agent_id,
                cwd=cwd,
                mcp_url=mcp_url,
            ),
        )
        self.tmux_agent_targets[agent_id] = pane_id
        self.tmux_manual_override_agent_ids.add(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        self.tmux_direct_agent_modes[agent_id] = True
        agent = await self.register_operator_root(
            agent_id,
            cwd=cwd,
            codex_command=codex_command,
            mcp_url=mcp_url,
            session_name=session_name,
            tmux_pane_id=pane_id,
            default_source_caller_agent_id=source_caller_agent_id,
            default_source_caller_project=(
                str(source_caller.get("project") or "")
                if isinstance(source_caller, dict)
                else None
            ),
            default_source_codex_session_id=str(
                source_metadata.get("codex_session_id") or ""
            ),
        )
        await asyncio.sleep(1.0)
        sent = await self.send_text_to_tmux_pane(
            pane_id,
            self.operator_bootstrap_prompt(agent_id, cwd),
        )
        if not sent:
            self.notify(f"Started {agent_id}, but bootstrap paste failed.", severity="warning")
        return agent, pane_id, True

    async def record_operator_fork(
        self,
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
        fork_agent_id: str,
        tmux_pane_id: str,
        metadata: dict[str, Any],
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        source_cwd: str | None = None,
        work_root: str | None = None,
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            "/v1/operator/forks/ensure",
            json={
                "operator_agent_id": logical_operator_id,
                "source_caller_agent_id": source_caller_agent_id,
                "fork_agent_id": fork_agent_id,
                "fork_track_id": fork_track_id,
                "fork_purpose": fork_purpose,
                "access_mode": access_mode,
                "source_cwd": source_cwd,
                "work_root": work_root,
                "tmux_pane_id": tmux_pane_id,
                "status": "running",
                "summary": "Fork launched from Agent PBX TUI.",
                "metadata": metadata,
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        fork = response.json()
        if isinstance(fork, dict):
            return fork
        raise RuntimeError("operator fork response was not an object")

    async def ensure_operator_fork_from_tui(
        self,
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        work_root: str | None = None,
    ) -> dict[str, Any] | None:
        caller = self.agents.get(source_caller_agent_id)
        blocker = self.operator_fork_start_blocker(source_caller_agent_id)
        if blocker:
            self.notify(blocker, severity="error")
            return None
        if caller is None:
            return None
        caller_metadata = caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        source_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        caller_cwd = str(caller_metadata.get("cwd") or "").strip()
        resolved_track_id = self.normalize_operator_fork_track_id(fork_track_id)
        resolved_purpose = self.normalize_operator_fork_label(
            fork_purpose,
            default=(
                DEFAULT_OPERATOR_FORK_PURPOSE
                if resolved_track_id == DEFAULT_OPERATOR_FORK_TRACK_ID
                else REVIEW_OPERATOR_FORK_PURPOSE
            ),
        )
        resolved_access_mode = self.normalize_operator_fork_label(
            access_mode,
            default=(
                DEFAULT_OPERATOR_FORK_ACCESS_MODE
                if resolved_purpose == DEFAULT_OPERATOR_FORK_PURPOSE
                else REVIEW_OPERATOR_FORK_ACCESS_MODE
            ),
        )
        launch_cwd = str(work_root or caller_cwd).strip()
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception:
            panes = []
        self.tmux_panes = panes
        existing_response = await self.api_client().get(
            "/v1/operator/forks",
            params={
                "operator_agent_id": logical_operator_id,
                "source_caller_agent_id": source_caller_agent_id,
                "limit": 20,
            },
            headers=auth_headers(self.token),
        )
        existing_response.raise_for_status()
        existing_payload = existing_response.json()
        existing_forks = (
            existing_payload.get("forks", [])
            if isinstance(existing_payload, dict)
            else []
        )
        for fork in existing_forks:
            if not isinstance(fork, dict):
                continue
            if fork.get("source_codex_session_id") != source_session_id:
                continue
            fork_metadata = (
                fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            )
            fork_track = str(
                fork.get("fork_track_id")
                or fork_metadata.get("fork_track_id")
                or DEFAULT_OPERATOR_FORK_TRACK_ID
            ).strip()
            if fork_track != resolved_track_id:
                continue
            if str(fork.get("status") or "").lower() in {"starting", "running", "ready"}:
                fork_agent_id = str(fork.get("fork_agent_id") or "")
                tmux_pane_id = str(fork.get("tmux_pane_id") or "").strip()
                if not fork_agent_id:
                    continue
                fork_agent = self.operator_fork_record_agent(fork)
                self.agents[fork_agent_id] = fork_agent
                pane = self.validated_operator_fork_pane(
                    fork_agent_id,
                    panes,
                    tmux_pane_id=tmux_pane_id,
                )
                if pane is None:
                    pane = self.validated_operator_fork_pane(fork_agent_id, panes)
                if pane is None:
                    self.clear_saved_tmux_target(fork_agent_id)
                    continue
                if pane.pane_id != tmux_pane_id:
                    metadata = fork_metadata
                    repaired = await self.record_operator_fork(
                        logical_operator_id=logical_operator_id,
                        source_caller_agent_id=source_caller_agent_id,
                        fork_agent_id=fork_agent_id,
                        tmux_pane_id=pane.pane_id,
                        fork_track_id=resolved_track_id,
                        fork_purpose=resolved_purpose,
                        access_mode=resolved_access_mode,
                        source_cwd=caller_cwd,
                        work_root=launch_cwd,
                        metadata={
                            **metadata,
                            "agent_type": OPERATOR_AGENT_TYPE,
                            "operator_role": OPERATOR_ROLE_FORK,
                            "logical_operator_id": logical_operator_id,
                            "source_caller_agent_id": source_caller_agent_id,
                            "source_codex_session_id": source_session_id,
                            "fork_track_id": resolved_track_id,
                            "fork_purpose": resolved_purpose,
                            "access_mode": resolved_access_mode,
                            "source_cwd": caller_cwd,
                            "work_root": launch_cwd,
                            "cwd": launch_cwd,
                            "tmux_pane_id": pane.pane_id,
                            "operator_fork_pending": False,
                        },
                    )
                    if isinstance(repaired, dict):
                        fork = repaired
                self.tmux_agent_targets[fork_agent_id] = pane.pane_id
                self.tmux_direct_agent_modes[fork_agent_id] = True
                local_agent = self.agents.get(fork_agent_id)
                if local_agent is not None:
                    metadata = (
                        local_agent.get("metadata")
                        if isinstance(local_agent.get("metadata"), dict)
                        else {}
                    )
                    local_agent["metadata"] = {
                        **metadata,
                        "agent_type": OPERATOR_AGENT_TYPE,
                        "operator_role": OPERATOR_ROLE_FORK,
                        "logical_operator_id": logical_operator_id,
                        "source_caller_agent_id": source_caller_agent_id,
                        "source_codex_session_id": source_session_id,
                        "fork_track_id": resolved_track_id,
                        "fork_purpose": resolved_purpose,
                        "access_mode": resolved_access_mode,
                        "source_cwd": caller_cwd,
                        "work_root": launch_cwd,
                        "cwd": launch_cwd,
                        "tmux_pane_id": pane.pane_id,
                    }
                return fork

        codex_command = self.operator_codex_command()
        mcp_url = agent_pbx_mcp_url(self.server)
        session_name = self.operator_tmux_session_name()
        await self.configure_operator_codex_mcp(
            codex_command=codex_command,
            mcp_url=mcp_url,
        )
        fork_agent_id = self.operator_fork_agent_id(
            logical_operator_id,
            source_caller_agent_id,
            source_session_id,
            fork_track_id=resolved_track_id,
        )
        fork_metadata = {
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": OPERATOR_ROLE_FORK,
            "logical_operator_id": logical_operator_id,
            "source_caller_agent_id": source_caller_agent_id,
            "source_codex_session_id": source_session_id,
            "fork_track_id": resolved_track_id,
            "fork_purpose": resolved_purpose,
            "access_mode": resolved_access_mode,
            "source_cwd": caller_cwd,
            "work_root": launch_cwd,
            "pbx_mode": PBX_REPORT_MODE,
            "cwd": launch_cwd,
            "launched_by": "agent-pbx-tui",
            "server_url": self.server,
            "mcp_url": mcp_url,
            "token_env": AGENT_PBX_TOKEN_ENV,
            "codex_command": codex_command,
        }
        review_mcp_approval_servers: tuple[str, ...] = ()
        if resolved_purpose == REVIEW_OPERATOR_FORK_PURPOSE:
            review_mcp_approval_servers = review_operator_mcp_approval_server_names()
            fork_metadata["review_mcp_approval_servers"] = list(
                review_mcp_approval_servers
            )
        register_response = await self.api_client().post(
            "/v1/agents/register",
            json={
                "agent_id": fork_agent_id,
                "project": str(caller.get("project") or "agent-pbx-operator"),
                "name": fork_agent_id,
                "agent_type": OPERATOR_AGENT_TYPE,
                "metadata": fork_metadata,
            },
            headers=auth_headers(self.token),
        )
        register_response.raise_for_status()
        fork_agent = register_response.json()
        if isinstance(fork_agent, dict):
            self.agents[fork_agent_id] = fork_agent
        bootstrap = self.operator_bootstrap_prompt(
            fork_agent_id,
            launch_cwd,
            logical_operator_id=logical_operator_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=source_session_id,
            fork_track_id=resolved_track_id,
            fork_purpose=resolved_purpose,
            access_mode=resolved_access_mode,
            source_cwd=caller_cwd,
            work_root=launch_cwd,
        )
        command = self.operator_fork_command(
            codex_command,
            source_session_id,
            bootstrap,
            cd=launch_cwd if launch_cwd and launch_cwd != caller_cwd else None,
            sandbox=(
                "workspace-write"
                if resolved_purpose == REVIEW_OPERATOR_FORK_PURPOSE
                else None
            ),
            config_overrides=(
                review_operator_mcp_config_overrides(review_mcp_approval_servers)
                if resolved_purpose == REVIEW_OPERATOR_FORK_PURPOSE
                else ()
            ),
        )
        pane_id = await asyncio.to_thread(
            tmux_support.launch_pane,
            session_name=session_name,
            window_name=fork_agent_id,
            command=command,
            cwd=launch_cwd,
            env=self.operator_launch_env(
                agent_id=fork_agent_id,
                cwd=launch_cwd,
                mcp_url=mcp_url,
                operator_role=OPERATOR_ROLE_FORK,
                logical_operator_id=logical_operator_id,
                source_caller_agent_id=source_caller_agent_id,
                source_codex_session_id=source_session_id,
                fork_track_id=resolved_track_id,
                fork_purpose=resolved_purpose,
                access_mode=resolved_access_mode,
                source_cwd=caller_cwd,
                work_root=launch_cwd,
            ),
        )
        self.tmux_agent_targets[fork_agent_id] = pane_id
        self.tmux_manual_override_agent_ids.add(fork_agent_id)
        self.tmux_detached_agent_ids.discard(fork_agent_id)
        self.tmux_direct_agent_modes[fork_agent_id] = True
        fork_metadata["tmux_pane_id"] = pane_id
        local_agent = self.agents.get(fork_agent_id)
        if local_agent is not None:
            metadata = (
                local_agent.get("metadata")
                if isinstance(local_agent.get("metadata"), dict)
                else {}
            )
            local_agent["metadata"] = {**metadata, "tmux_pane_id": pane_id}
        fork = await self.record_operator_fork(
            logical_operator_id=logical_operator_id,
            source_caller_agent_id=source_caller_agent_id,
            fork_agent_id=fork_agent_id,
            tmux_pane_id=pane_id,
            metadata=fork_metadata,
            fork_track_id=resolved_track_id,
            fork_purpose=resolved_purpose,
            access_mode=resolved_access_mode,
            source_cwd=caller_cwd,
            work_root=launch_cwd,
        )
        self.notify(
            f"Started {resolved_purpose} fork {fork_agent_id} for {source_caller_agent_id}."
        )
        return fork

    def source_caller_agent_id_for_review_fork(
        self,
        operator_agent_id: str,
    ) -> str | None:
        operator_agent = self.agents.get(operator_agent_id)
        if not isinstance(operator_agent, dict):
            return None
        metadata = (
            operator_agent.get("metadata")
            if isinstance(operator_agent.get("metadata"), dict)
            else {}
        )
        if self.operator_role(operator_agent) == OPERATOR_ROLE_FORK:
            source_agent_id = str(metadata.get("source_caller_agent_id") or "").strip()
            return source_agent_id or None
        selected_caller = self.selected_caller_agent_id_for_fork()
        if selected_caller:
            return selected_caller
        default_source = str(metadata.get("default_source_caller_agent_id") or "").strip()
        if default_source:
            return default_source
        return self.single_active_operator_fork_source_agent_id(
            self.logical_operator_id_for_agent(operator_agent)
        )

    def operator_review_work_root(
        self,
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
        source_cwd: str,
        fork_track_id: str,
    ) -> str:
        source_root = Path(source_cwd).expanduser().resolve()
        configured = os.getenv("AGENT_PBX_TUI_OPERATOR_REVIEW_ROOT", "").strip()
        if configured:
            base = Path(configured).expanduser()
        else:
            base = source_root.parent / ".agent-pbx-review"
        work_root = (
            base
            / slugify(
                f"{logical_operator_id}-{source_caller_agent_id}-{fork_track_id}"
            )[:140]
        ).resolve()
        if work_root == source_root or work_root.is_relative_to(source_root):
            raise RuntimeError(
                "review work_root resolves inside the caller project; set "
                "AGENT_PBX_TUI_OPERATOR_REVIEW_ROOT outside the repo"
            )
        work_root.mkdir(parents=True, exist_ok=True)
        return str(work_root)

    async def next_operator_review_fork_track_id(
        self,
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
    ) -> str:
        forks: list[dict[str, Any]] = []
        try:
            response = await self.api_client().get(
                "/v1/operator/forks",
                params={
                    "operator_agent_id": logical_operator_id,
                    "source_caller_agent_id": source_caller_agent_id,
                    "limit": 100,
                },
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("forks"), list):
                forks.extend(item for item in payload["forks"] if isinstance(item, dict))
        except Exception:
            pass
        for fork in self.operator_fork_agents():
            if self.logical_operator_id_for_agent(fork) == logical_operator_id:
                forks.append(fork)
        used: set[int] = set()
        for fork in forks:
            metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            if (
                str(
                    fork.get("source_caller_agent_id")
                    or metadata.get("source_caller_agent_id")
                    or ""
                ).strip()
                != source_caller_agent_id
            ):
                continue
            if (
                str(
                    fork.get("source_codex_session_id")
                    or metadata.get("source_codex_session_id")
                    or ""
                ).strip()
                != source_codex_session_id
            ):
                continue
            track_id = str(
                fork.get("fork_track_id")
                or metadata.get("fork_track_id")
                or ""
            ).strip()
            match = re.fullmatch(r"review-(\d+)", track_id)
            if match:
                used.add(int(match.group(1)))
        index = 1
        while index in used:
            index += 1
        return f"review-{index}"

    async def start_review_operator_fork(self) -> None:
        if not self.tmux_features_available:
            self.notify(
                "Tmux is required to start an operator review fork from the TUI.",
                severity="warning",
            )
            return
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if not isinstance(operator_agent, dict):
            return
        await self.reconcile_operator_tmux_targets()
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        source_caller_agent_id = self.source_caller_agent_id_for_review_fork(
            operator_agent_id
        )
        if not source_caller_agent_id:
            self.notify(
                "Select a caller or an operator with an active/default caller first.",
                severity="warning",
            )
            return
        blocker = self.operator_fork_start_blocker(source_caller_agent_id)
        if blocker:
            self.notify(blocker, severity="error")
            return
        caller = self.agents.get(source_caller_agent_id)
        if not isinstance(caller, dict):
            self.notify(f"Caller {source_caller_agent_id} is not loaded.", severity="error")
            return
        caller_metadata = (
            caller.get("metadata") if isinstance(caller.get("metadata"), dict) else {}
        )
        source_session_id = str(caller_metadata.get("codex_session_id") or "").strip()
        source_cwd = str(caller_metadata.get("cwd") or "").strip()
        if not await self.ensure_operator_auth_ready():
            return
        fork_track_id = await self.next_operator_review_fork_track_id(
            logical_operator_id=logical_operator_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=source_session_id,
        )
        try:
            work_root = self.operator_review_work_root(
                logical_operator_id=logical_operator_id,
                source_caller_agent_id=source_caller_agent_id,
                source_cwd=source_cwd,
                fork_track_id=fork_track_id,
            )
            fork = await self.ensure_operator_fork_from_tui(
                logical_operator_id=logical_operator_id,
                source_caller_agent_id=source_caller_agent_id,
                fork_track_id=fork_track_id,
                fork_purpose=REVIEW_OPERATOR_FORK_PURPOSE,
                access_mode=REVIEW_OPERATOR_FORK_ACCESS_MODE,
                work_root=work_root,
            )
        except Exception as exc:
            self.notify(f"Unable to start review fork: {exc}", severity="error")
            return
        if fork is None:
            return
        self.save_settings()
        await self.refresh_agents()
        fork_agent_id = str(fork.get("fork_agent_id") or "").strip()
        if fork_agent_id:
            await self.open_latest_for_agent(fork_agent_id)
        self.notify(
            f"Started review fork {fork_agent_id or fork_track_id} for "
            f"{source_caller_agent_id} in {work_root}."
        )

    async def start_operator_agent(
        self,
        agent_id: str | None = None,
        *,
        source_caller_agent_id: str | None = None,
    ) -> None:
        if not self.tmux_features_available:
            self.notify(
                "Tmux is required to start an operator from the TUI.",
                severity="warning",
            )
            return
        source_caller_agent_id = source_caller_agent_id or (
            self.selected_caller_agent_id_for_fork() if agent_id is None else None
        )
        if source_caller_agent_id:
            blocker = self.operator_fork_start_blocker(source_caller_agent_id)
            if blocker:
                self.notify(blocker, severity="error")
                return
        if not await self.ensure_operator_auth_ready():
            return
        agent_id = agent_id or self.next_operator_agent_id()
        cwd = self.operator_cwd()
        codex_command = self.operator_codex_command()
        session_name = self.operator_tmux_session_name()
        mcp_url = agent_pbx_mcp_url(self.server)
        try:
            await self.configure_operator_codex_mcp(
                codex_command=codex_command,
                mcp_url=mcp_url,
            )
        except Exception as exc:
            self.notify(f"Unable to configure Codex MCP: {exc}", severity="error")
            return
        try:
            root_agent, root_pane_id, root_launched = await self.ensure_operator_root_from_tui(
                agent_id=agent_id,
                cwd=cwd,
                codex_command=codex_command,
                mcp_url=mcp_url,
                session_name=session_name,
                source_caller_agent_id=source_caller_agent_id,
            )
        except Exception as exc:
            self.notify(f"Unable to start operator root: {exc}", severity="error")
            return
        if source_caller_agent_id:
            try:
                fork = await self.ensure_operator_fork_from_tui(
                    logical_operator_id=agent_id,
                    source_caller_agent_id=source_caller_agent_id,
                )
            except Exception as exc:
                self.notify(f"Unable to start operator fork: {exc}", severity="error")
                return
            if fork is None:
                return
            self.save_settings()
            await self.refresh_agents()
            await self.open_latest_for_agent(agent_id)
            if root_launched:
                self.notify(
                    f"Started operator {agent_id} with fork {fork['fork_agent_id']}."
                )
            else:
                self.notify(
                    f"Reused operator {agent_id} and started fork {fork['fork_agent_id']}."
                )
            return
        self.save_settings()
        if root_launched:
            self.notify(f"Started operator {agent_id}.")
        else:
            self.notify(f"Reused operator {agent_id}.")
        await self.refresh_agents()
        await self.open_latest_for_agent(agent_id)
        _ = root_agent, root_pane_id

    def is_operator_agent_id(self, agent_id: str) -> bool:
        agent = self.agents.get(agent_id)
        return agent is not None and self.agent_type(agent) == OPERATOR_AGENT_TYPE

    def operator_fork_record_agent(self, fork: dict[str, Any]) -> dict[str, Any]:
        fork_agent_id = str(fork.get("fork_agent_id") or fork.get("agent_id") or "").strip()
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        hydrated_metadata = dict(metadata)
        identity = {
            "agent_type": OPERATOR_AGENT_TYPE,
            "operator_role": OPERATOR_ROLE_FORK,
            "operator_fork_id": str(fork.get("operator_fork_id") or ""),
            "logical_operator_id": str(fork.get("logical_operator_agent_id") or ""),
            "source_caller_agent_id": str(fork.get("source_caller_agent_id") or ""),
            "source_codex_session_id": str(fork.get("source_codex_session_id") or ""),
            "fork_track_id": str(
                fork.get("fork_track_id") or DEFAULT_OPERATOR_FORK_TRACK_ID
            ),
            "fork_purpose": str(
                fork.get("fork_purpose") or DEFAULT_OPERATOR_FORK_PURPOSE
            ),
            "access_mode": str(
                fork.get("access_mode") or DEFAULT_OPERATOR_FORK_ACCESS_MODE
            ),
        }
        hydrated_metadata.update({key: value for key, value in identity.items() if value})
        for key in (
            "cwd",
            "source_cwd",
            "work_root",
            "fork_codex_session_id",
            "tmux_pane_id",
        ):
            value = str(fork.get(key) or "").strip()
            if value:
                hydrated_metadata[key] = value
        local_agent = self.agents.get(fork_agent_id)
        if isinstance(local_agent, dict):
            agent = dict(local_agent)
            agent_metadata = (
                agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            )
            agent["metadata"] = {**agent_metadata, **hydrated_metadata}
            agent["agent_type"] = OPERATOR_AGENT_TYPE
            source_project = str(
                hydrated_metadata.get("source_caller_project") or ""
            ).strip()
            if source_project:
                agent["project"] = source_project
            return agent
        source_project = str(hydrated_metadata.get("source_caller_project") or "").strip()
        return {
            "agent_id": fork_agent_id,
            "agent_type": OPERATOR_AGENT_TYPE,
            "project": source_project or str(fork.get("cwd") or "agent-pbx-operator"),
            "status": str(fork.get("status") or "unknown"),
            "metadata": hydrated_metadata,
        }

    def validated_operator_fork_pane(
        self,
        fork_agent_id: str,
        panes: list[tmux_support.TmuxPane],
        *,
        tmux_pane_id: str | None = None,
    ) -> tmux_support.TmuxPane | None:
        fork_agent = self.agents.get(fork_agent_id) or {
            "agent_id": fork_agent_id,
            "agent_type": OPERATOR_AGENT_TYPE,
            "metadata": {"operator_role": OPERATOR_ROLE_FORK},
        }
        candidates = panes
        target = str(tmux_pane_id or "").strip()
        if target:
            candidates = [
                pane
                for pane in panes
                if pane.pane_id == target or pane.target_label == target
            ]
        matches = [
            pane
            for pane in candidates
            if self.tmux_pane_allowed_for_agent(fork_agent, pane)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def operator_fork_is_running(self, fork: dict[str, Any]) -> bool:
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        pending = str(metadata.get("operator_fork_pending") or "").strip().lower()
        if pending in {"1", "true", "yes", "on"}:
            return False
        if not bool(fork.get("pbx_active", True)):
            return False
        status = (
            str(fork.get("effective_status") or fork.get("status") or "")
            .strip()
            .lower()
        )
        return status in {
            "active",
            "online",
            "ready",
            "running",
            "starting",
            "working",
            "in_progress",
        }

    def operator_fork_pane_targets(
        self,
        logical_operator_id: str,
        panes: list[tmux_support.TmuxPane],
    ) -> list[tuple[dict[str, Any], tmux_support.TmuxPane]]:
        targets: list[tuple[dict[str, Any], tmux_support.TmuxPane]] = []
        seen: set[tuple[str, str]] = set()
        operator_session_name = self.operator_tmux_session_name()

        def add_target(fork: dict[str, Any], pane: tmux_support.TmuxPane) -> None:
            fork_agent_id = str(fork.get("agent_id") or "").strip()
            if not fork_agent_id:
                return
            key = (fork_agent_id, pane.pane_id)
            if key in seen:
                return
            seen.add(key)
            targets.append((fork, pane))

        for fork in self.operator_forks_for_logical_operator(logical_operator_id):
            fork_agent_id = str(fork.get("agent_id") or "").strip()
            if not fork_agent_id:
                continue
            metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
            explicit_targets = {
                str(self.tmux_agent_targets.get(fork_agent_id) or "").strip(),
                str(metadata.get("tmux_pane_id") or "").strip(),
            }
            explicit_targets.discard("")
            for pane in panes:
                if pane.pane_id in explicit_targets or pane.target_label in explicit_targets:
                    if self.tmux_pane_allowed_for_agent(fork, pane):
                        add_target(fork, pane)
                    elif self.tmux_agent_targets.get(fork_agent_id) in {
                        pane.pane_id,
                        pane.target_label,
                    }:
                        self.clear_saved_tmux_target(fork_agent_id)

            for pane in panes:
                if pane.session_name != operator_session_name:
                    continue
                if not self.tmux_pane_allowed_for_agent(fork, pane):
                    continue
                add_target(fork, pane)

        return targets

    def current_operator_fork_target_key(self, logical_operator_id: str) -> str | None:
        selected_agent_id = self.selected_agent_id
        if selected_agent_id and selected_agent_id in self.agents:
            selected_agent = self.agents[selected_agent_id]
            if (
                self.operator_role(selected_agent) == OPERATOR_ROLE_FORK
                and self.logical_operator_id_for_agent(selected_agent) == logical_operator_id
            ):
                pane_id = (
                    self.tmux_visible_pane_id_for_agent(selected_agent_id)
                    or self.tmux_agent_targets.get(selected_agent_id)
                )
                if pane_id:
                    return f"{selected_agent_id}:{pane_id}"
        return self.selected_operator_fork_target_by_operator.get(logical_operator_id)

    async def view_operator_fork_pane(
        self,
        *,
        logical_operator_id: str,
        fork: dict[str, Any],
        pane: tmux_support.TmuxPane,
        index: int,
        total: int,
    ) -> None:
        fork_agent_id = str(fork.get("agent_id") or "").strip()
        if not fork_agent_id:
            return
        self.tmux_agent_targets[fork_agent_id] = pane.pane_id
        self.tmux_manual_override_agent_ids.add(fork_agent_id)
        self.tmux_detached_agent_ids.discard(fork_agent_id)
        self.tmux_direct_agent_modes[fork_agent_id] = True
        self.selected_operator_fork_target_by_operator[logical_operator_id] = (
            f"{fork_agent_id}:{pane.pane_id}"
        )
        self.active_agent_tab_by_agent[fork_agent_id] = "latest-tab"
        self.save_settings()
        await self.select_agent(fork_agent_id)
        self.move_operator_cursor(fork_agent_id, focus=True)
        source_caller = ""
        track_id = ""
        purpose = ""
        metadata = fork.get("metadata") if isinstance(fork.get("metadata"), dict) else {}
        if metadata:
            source_caller = str(metadata.get("source_caller_agent_id") or "").strip()
            track_id = str(
                metadata.get("fork_track_id") or DEFAULT_OPERATOR_FORK_TRACK_ID
            ).strip()
            purpose = str(
                metadata.get("fork_purpose") or DEFAULT_OPERATOR_FORK_PURPOSE
            ).strip()
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is not None:
            detail.text = (
                f"Viewing fork pane {index + 1}/{total} for {logical_operator_id}.\n"
                f"Fork: {fork_agent_id}\n"
                f"Track: {track_id or '-'}\n"
                f"Purpose: {purpose or '-'}\n"
                f"Pane: {pane.pane_id} ({pane.target_label})\n"
                f"Source caller: {source_caller or '-'}"
            )
        self.notify(
            f"Viewing fork {index + 1}/{total}: {fork_agent_id} "
            f"{track_id or DEFAULT_OPERATOR_FORK_TRACK_ID} {pane.pane_id}."
        )

    async def cycle_selected_operator_fork(self, direction: int) -> None:
        operator_agent_id = self.selected_operator_agent_id()
        if not operator_agent_id:
            return
        operator_agent = self.agents.get(operator_agent_id)
        if operator_agent is None:
            self.notify("Select a known operator first.", severity="warning")
            return
        logical_operator_id = self.logical_operator_id_for_agent(operator_agent)
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            self.notify(f"Unable to list tmux panes: {exc}", severity="error")
            return
        self.tmux_panes = panes
        targets = self.operator_fork_pane_targets(logical_operator_id, panes)
        if not targets:
            self.notify(
                f"No live fork panes found for {logical_operator_id}.",
                severity="warning",
            )
            return
        current_key = self.current_operator_fork_target_key(logical_operator_id)
        current_index = next(
            (
                index
                for index, (fork, pane) in enumerate(targets)
                if f"{fork.get('agent_id')}:{pane.pane_id}" == current_key
            ),
            -1,
        )
        if current_index < 0:
            next_index = 0 if direction >= 0 else len(targets) - 1
        else:
            next_index = (current_index + direction) % len(targets)
        fork, pane = targets[next_index]
        await self.view_operator_fork_pane(
            logical_operator_id=logical_operator_id,
            fork=fork,
            pane=pane,
            index=next_index,
            total=len(targets),
        )

    def selected_operator_agent_id(self) -> str | None:
        focused_agent_id = self.focused_agent_table_id()
        if focused_agent_id and self.is_operator_agent_id(focused_agent_id):
            return focused_agent_id
        cursor_operator_id = self.operator_id_at_cursor()
        if cursor_operator_id and self.is_operator_agent_id(cursor_operator_id):
            return cursor_operator_id
        if self.selected_agent_id and self.is_operator_agent_id(self.selected_agent_id):
            return self.selected_agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            value = agent_input.value.strip()
            if value and self.is_operator_agent_id(value):
                return value
        self.notify("Select an operator first.", severity="warning")
        return None

    def tui_owned_operator_pane_id(self, agent_id: str) -> str | None:
        if not self.is_operator_agent_id(agent_id):
            return None
        agent = self.agents.get(agent_id, {})
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        if metadata.get("launched_by") != "agent-pbx-tui":
            return None
        return self.tmux_agent_targets.get(agent_id)

    def clear_operator_tmux_state(self, agent_id: str) -> None:
        self.tmux_agent_targets.pop(agent_id, None)
        self.tmux_direct_agent_modes.pop(agent_id, None)
        self.tmux_manual_override_agent_ids.discard(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        self.tmux_liveness_by_agent.pop(agent_id, None)
        self.tmux_plan_selector_agent_ids.discard(agent_id)
        self.tmux_plan_selector_pane_by_agent.pop(agent_id, None)
        self.tmux_plan_selector_indices_by_agent.pop(agent_id, None)

    async def kill_tui_owned_operator_pane(self, agent_id: str) -> bool:
        pane_id = self.tui_owned_operator_pane_id(agent_id)
        if not pane_id:
            return True
        try:
            await asyncio.to_thread(tmux_support.kill_pane, pane_id)
        except Exception as exc:
            self.notify(f"Unable to kill operator pane {pane_id}: {exc}", severity="error")
            return False
        self.clear_operator_tmux_state(agent_id)
        self.save_settings()
        return True

    async def restart_selected_operator(self) -> None:
        agent_id = self.selected_operator_agent_id()
        if not agent_id:
            return
        pane_id = self.tmux_agent_targets.get(agent_id)
        if pane_id and self.tui_owned_operator_pane_id(agent_id) != pane_id:
            self.notify(
                f"{agent_id} has a non-TUI-owned tmux pane; detach it before restart.",
                severity="warning",
            )
            return
        try:
            candidates = await asyncio.to_thread(self.operator_session_candidates, agent_id)
            await self.record_operator_session_history(agent_id, include=candidates[:1])
        except Exception as exc:
            self.notify(
                f"Unable to record operator session history before restart: {exc}",
                severity="warning",
            )
        if not await self.kill_tui_owned_operator_pane(agent_id):
            return
        await self.start_operator_agent(agent_id=agent_id)

    async def resume_selected_operator(
        self,
        *,
        agent_id: str | None = None,
        resume_candidate: OperatorSessionCandidate | None = None,
    ) -> None:
        agent_id = agent_id or self.selected_operator_agent_id()
        if not agent_id:
            return
        if not self.is_operator_agent_id(agent_id):
            self.notify(f"{agent_id} is not an operator.", severity="warning")
            return
        if not self.tmux_features_available:
            self.notify(
                "Tmux is required to resume an operator from the TUI.",
                severity="warning",
            )
            return
        candidates = (
            [resume_candidate]
            if resume_candidate is not None
            else await asyncio.to_thread(self.operator_session_candidates, agent_id)
        )
        target = resume_candidate or self.operator_resume_target(agent_id, candidates)
        if target is None:
            self.notify(f"No resumable Codex session found for {agent_id}.", severity="warning")
            await self.show_selected_operator_history()
            return
        pane_id = self.tmux_agent_targets.get(agent_id)
        if pane_id and self.tui_owned_operator_pane_id(agent_id) != pane_id:
            self.notify(
                f"{agent_id} has a non-TUI-owned tmux pane; detach it before resume.",
                severity="warning",
            )
            return
        if not await self.ensure_operator_auth_ready():
            return
        cwd = self.operator_cwd()
        codex_command = self.operator_codex_command()
        session_name = self.operator_tmux_session_name()
        mcp_url = agent_pbx_mcp_url(self.server)
        try:
            await self.configure_operator_codex_mcp(
                codex_command=codex_command,
                mcp_url=mcp_url,
            )
        except Exception as exc:
            self.notify(f"Unable to configure Codex MCP: {exc}", severity="error")
            return
        try:
            history = await self.record_operator_session_history(
                agent_id,
                include=[*candidates[:3], target],
            )
        except Exception as exc:
            self.notify(
                f"Unable to record operator session history before resume: {exc}",
                severity="warning",
            )
            history = self.operator_session_history_metadata(
                agent_id,
                include=[*candidates[:3], target],
            )
        if not await self.kill_tui_owned_operator_pane(agent_id):
            return
        command = self.operator_resume_command(codex_command, target.session_id)
        env = self.operator_launch_env(
            agent_id=agent_id,
            cwd=cwd,
            mcp_url=mcp_url,
        )
        env["AGENT_PBX_RESUME_CODEX_SESSION_ID"] = target.session_id
        try:
            resumed_pane_id = await asyncio.to_thread(
                tmux_support.launch_pane,
                session_name=session_name,
                window_name=agent_id,
                command=command,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            self.notify(f"Unable to launch resumed operator pane: {exc}", severity="error")
            return
        self.tmux_agent_targets[agent_id] = resumed_pane_id
        self.tmux_manual_override_agent_ids.add(agent_id)
        self.tmux_detached_agent_ids.discard(agent_id)
        self.tmux_direct_agent_modes[agent_id] = True
        try:
            await self.register_operator_root(
                agent_id,
                cwd=cwd,
                codex_command=codex_command,
                mcp_url=mcp_url,
                session_name=session_name,
                tmux_pane_id=resumed_pane_id,
                resumed_codex_session_id=target.session_id,
                operator_session_history=history,
            )
        except Exception as exc:
            self.notify(
                f"Resumed pane launched, but PBX registration failed: {exc}",
                severity="warning",
            )
        await asyncio.sleep(1.0)
        sent = await self.send_text_to_tmux_pane(
            resumed_pane_id,
            self.operator_bootstrap_prompt(agent_id, cwd),
        )
        if not sent:
            self.notify(
                f"Resumed {agent_id}, but bootstrap paste failed.",
                severity="warning",
            )
        else:
            self.notify(f"Resumed {agent_id} from {target.session_id}.")
        self.save_settings()
        await self.refresh_agents()
        await self.open_latest_for_agent(agent_id)

    async def stop_selected_operator(self) -> None:
        agent_id = self.selected_operator_agent_id()
        if not agent_id:
            return
        pane_id = self.tmux_agent_targets.get(agent_id)
        if pane_id and self.tui_owned_operator_pane_id(agent_id) != pane_id:
            self.notify(
                f"{agent_id} has a non-TUI-owned tmux pane; detach it before stop.",
                severity="warning",
            )
            return
        if not await self.kill_tui_owned_operator_pane(agent_id):
            return
        agent = self.agents.get(agent_id) or {}
        project = str(agent.get("project") or "agent-pbx-operator")
        detail = (
            "The operator pane was stopped from the TUI. The pane was killed "
            "and Agent PBX reporting was marked inactive."
        )
        try:
            await self.create_agent_report(
                agent_id,
                {
                    "project": project,
                    "status": "canceled",
                    "summary": "Operator stopped from TUI",
                    "detail": detail,
                    "needs_input": False,
                    "plan_options": [],
                },
            )
        except Exception as exc:
            self.notify(f"Unable to record stop report for {agent_id}: {exc}", severity="warning")
        try:
            updated = await self.set_agent_pbx_active(agent_id, active=False)
            if isinstance(updated, dict):
                self.agents[agent_id] = updated
        except Exception as exc:
            self.notify(f"Unable to mark {agent_id} inactive: {exc}", severity="error")
            return
        self.notify(f"Stopped operator {agent_id}.")
        await self.refresh_agents()
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def load_operator_campaigns(self, agent_id: str) -> None:
        status_label = self.query_one_or_none("#campaign-status", Static)
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        agent = self.agents.get(agent_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            self.render_campaigns(agent_id, [])
            if status_label is not None:
                status_label.update("Campaigns: select an operator agent")
            if detail is not None:
                detail.text = "Campaigns are shown for operator agents only."
            return
        if status_label is not None:
            status_label.update(f"Campaigns: loading for {agent_id}...")
        try:
            response = await self.api_client().get(
                "/v1/operator/campaigns",
                params={"operator_agent_id": agent_id, "limit": 50},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            payload = response.json()
            campaigns = payload.get("campaigns", []) if isinstance(payload, dict) else []
            if not isinstance(campaigns, list):
                campaigns = []
            await self.attach_campaign_reports(campaigns)
        except Exception as exc:
            self.render_campaigns(agent_id, [])
            if status_label is not None:
                status_label.update(f"Campaigns: unable to load ({exc})")
            if detail is not None:
                detail.text = f"Unable to load campaigns for {agent_id}: {exc}"
            return
        self.campaigns_by_operator[agent_id] = {
            str(campaign.get("campaign_id")): campaign
            for campaign in campaigns
            if campaign.get("campaign_id")
        }
        self.render_campaigns(agent_id, campaigns)
        if status_label is not None:
            status_label.update(f"Campaigns: {len(campaigns)} for {agent_id}")
        selected = self.selected_campaign_id_by_operator.get(agent_id)
        if selected in self.campaigns_by_operator.get(agent_id, {}):
            self.select_campaign(selected)
        elif campaigns:
            self.select_campaign(str(campaigns[0]["campaign_id"]))
        elif detail is not None:
            detail.text = f"No operator campaigns for {agent_id}."

    def campaign_report_ids(self, campaign: dict[str, Any]) -> list[str]:
        report_ids: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            report_id = str(value or "").strip()
            if report_id and report_id not in seen:
                seen.add(report_id)
                report_ids.append(report_id)

        assignments = campaign.get("assignments") or []
        if isinstance(assignments, list):
            for assignment in assignments:
                if isinstance(assignment, dict):
                    add(assignment.get("last_report_id"))
        events = campaign.get("events") or []
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict):
                    add(event.get("report_id"))
        return report_ids

    async def fetch_report_by_id(self, report_id: str) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v1/reports/{report_id}",
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        report = response.json()
        if not isinstance(report, dict):
            raise RuntimeError(f"report {report_id} response was not an object")
        return report

    async def attach_campaign_reports(
        self,
        campaigns: list[dict[str, Any]],
    ) -> None:
        report_ids: list[str] = []
        seen: set[str] = set()
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                continue
            for report_id in self.campaign_report_ids(campaign):
                if report_id not in seen:
                    seen.add(report_id)
                    report_ids.append(report_id)
        reports_by_id: dict[str, dict[str, Any]] = {}
        for report_id in report_ids:
            try:
                reports_by_id[report_id] = await self.fetch_report_by_id(report_id)
            except Exception as exc:
                reports_by_id[report_id] = {
                    "report_id": report_id,
                    "error": str(exc),
                }
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                continue
            campaign["_reports_by_id"] = {
                report_id: reports_by_id[report_id]
                for report_id in self.campaign_report_ids(campaign)
                if report_id in reports_by_id
            }

    def render_campaigns(
        self,
        agent_id: str,
        campaigns: list[dict[str, Any]],
    ) -> None:
        table = self.query_one_or_none("#campaigns", DataTable)
        if table is None:
            return
        table.clear()
        for campaign in campaigns:
            campaign_id = str(campaign.get("campaign_id") or "")
            if not campaign_id:
                continue
            assignments = campaign.get("assignments") or []
            states: dict[str, int] = {}
            if isinstance(assignments, list):
                for assignment in assignments:
                    if isinstance(assignment, dict):
                        state = str(assignment.get("state") or "pending")
                        states[state] = states.get(state, 0) + 1
            assignment_text = ", ".join(
                f"{state}:{count}" for state, count in sorted(states.items())
            ) or "-"
            updated = float_value(campaign.get("updated_at"))
            updated_text = f"{updated:.0f}" if updated is not None else "-"
            table.add_row(
                str(campaign.get("status") or "-"),
                assignment_text,
                str(campaign.get("title") or campaign_id),
                updated_text,
                key=campaign_id,
            )
        selected = self.selected_campaign_id_by_operator.get(agent_id)
        if selected and selected in self.campaigns_by_operator.get(agent_id, {}):
            try:
                table.move_cursor(
                    row=table.get_row_index(selected),
                    animate=False,
                    scroll=True,
                )
            except Exception:
                pass

    def select_campaign(self, campaign_id: str) -> None:
        operator_id = self.selected_agent_id
        if not operator_id:
            return
        campaign = self.campaigns_by_operator.get(operator_id, {}).get(campaign_id)
        if campaign is None:
            return
        self.selected_campaign_id = campaign_id
        self.selected_campaign_id_by_operator[operator_id] = campaign_id
        report_ids = self.campaign_report_ids(campaign)
        selected_report_id = self.selected_campaign_report_id_by_operator.get(operator_id)
        if selected_report_id not in report_ids:
            selected_report_id = report_ids[0] if report_ids else None
        if selected_report_id:
            self.selected_campaign_report_id_by_operator[operator_id] = selected_report_id
        else:
            self.selected_campaign_report_id_by_operator.pop(operator_id, None)
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if detail is not None:
            detail.text = self.format_campaign_detail(campaign)

    def report_summary_line(self, report: dict[str, Any]) -> str:
        if report.get("error"):
            return f"unavailable: {report.get('error')}"
        status = str(report.get("status") or "-")
        summary = str(report.get("summary") or "").strip()
        return f"{status} {summary}".strip()

    def format_report_block(self, report: dict[str, Any]) -> list[str]:
        report_id = str(report.get("report_id") or "-")
        if report.get("error"):
            return [
                f"Report: {report_id}",
                f"Error: {report.get('error')}",
            ]
        lines = [
            f"Report: {report_id}",
            f"Agent: {report.get('agent_id')}",
            f"Status: {report.get('status')}",
            f"Summary: {report.get('summary')}",
            "",
            str(report.get("detail") or ""),
        ]
        return lines

    def selected_campaign_for_operator(
        self,
        operator_id: str | None,
    ) -> dict[str, Any] | None:
        if not operator_id:
            return None
        campaign_id = self.selected_campaign_id_by_operator.get(operator_id)
        if not campaign_id:
            return None
        return self.campaigns_by_operator.get(operator_id, {}).get(campaign_id)

    def format_campaign_detail(self, campaign: dict[str, Any]) -> str:
        lines = [
            f"Campaign: {campaign.get('title')}",
            f"ID: {campaign.get('campaign_id')}",
            f"Status: {campaign.get('status')}",
            "",
            "Objective:",
            str(campaign.get("objective") or ""),
        ]
        criteria = campaign.get("criteria") or []
        if criteria:
            lines.extend(["", "Criteria:", *[f"- {item}" for item in criteria]])
        reports_by_id = (
            campaign.get("_reports_by_id")
            if isinstance(campaign.get("_reports_by_id"), dict)
            else {}
        )
        assignments = campaign.get("assignments") or []
        if assignments:
            lines.extend(["", "Assignments:"])
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue
                lines.append(
                    "- "
                    f"{assignment.get('target_agent_id')} "
                    f"[{assignment.get('state')}] "
                    f"{assignment.get('title')}"
                )
                if assignment.get("last_command_id"):
                    lines.append(f"  command: {assignment.get('last_command_id')}")
                if assignment.get("operator_fork_id"):
                    lines.append(f"  fork: {assignment.get('operator_fork_id')}")
                if assignment.get("last_report_id"):
                    report_id = str(assignment.get("last_report_id"))
                    report = reports_by_id.get(report_id)
                    if isinstance(report, dict):
                        lines.append(
                            f"  report: {report_id} ({self.report_summary_line(report)})"
                        )
                    else:
                        lines.append(f"  report: {report_id}")
        events = campaign.get("events") or []
        if events:
            lines.extend(["", "Recent Events:"])
            for event in events[:10]:
                if not isinstance(event, dict):
                    continue
                lines.append(
                    "- "
                    f"{event.get('event_type')} "
                    f"{event.get('summary')}"
                )
                if event.get("report_id"):
                    lines.append(f"  report: {event.get('report_id')}")
        report_ids = self.campaign_report_ids(campaign)
        if report_ids:
            lines.extend(["", "Reports:"])
            for index, report_id in enumerate(report_ids, start=1):
                report = reports_by_id.get(report_id)
                if not isinstance(report, dict):
                    lines.append(f"### {index}. {report_id}")
                    lines.append("(not loaded)")
                    continue
                lines.append(f"### {index}. {report_id}")
                block = self.format_report_block(report)
                for block_line in block[1:]:
                    lines.append(block_line)
                lines.append("")
        return "\n".join(lines)

    def campaign_joplin_copy_title(self, campaign: dict[str, Any]) -> str:
        title = str(campaign.get("title") or campaign.get("campaign_id") or "").strip()
        if not title:
            title = "Campaign"
        return f"Campaign - {title}"

    def format_campaign_joplin_copy_body(
        self,
        operator_id: str,
        campaign: dict[str, Any],
    ) -> str:
        title = str(campaign.get("title") or campaign.get("campaign_id") or "Campaign")
        campaign_id = str(campaign.get("campaign_id") or "")
        status = str(campaign.get("status") or "")
        copied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return "\n".join(
            [
                f"# Campaign - {title}",
                "",
                f"- Campaign ID: `{campaign_id}`",
                f"- Operator Agent: `{operator_id}`",
                f"- Status: `{status}`",
                f"- Copied: `{copied_at}`",
                "",
                "## Campaign Detail",
                "",
                "```text",
                self.format_campaign_detail(campaign).strip(),
                "```",
            ]
        )

    async def copy_selected_campaign_to_joplin(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = "Select a campaign before copying to Joplin."
            self.notify("Select a campaign before copying to Joplin.", severity="warning")
            return
        report_ids = self.campaign_report_ids(campaign)
        reports_by_id = (
            campaign.get("_reports_by_id")
            if isinstance(campaign.get("_reports_by_id"), dict)
            else {}
        )
        if any(report_id not in reports_by_id for report_id in report_ids):
            await self.attach_campaign_reports([campaign])
        await self.create_manual_joplin_copy(
            operator_id,
            title=self.campaign_joplin_copy_title(campaign),
            body=self.format_campaign_joplin_copy_body(operator_id, campaign),
            success_message="Copied campaign to a new Joplin note.",
        )

    def campaign_monitor_prompt(
        self,
        operator_id: str,
        campaign: dict[str, Any],
    ) -> str:
        campaign_id = str(campaign.get("campaign_id") or "").strip()
        title = str(campaign.get("title") or campaign_id or "Campaign").strip()
        status = str(campaign.get("status") or "unknown").strip()
        objective = str(campaign.get("objective") or "").strip()
        criteria = campaign.get("criteria") or []
        criteria_lines = [
            f"- {str(item).strip()}"
            for item in criteria
            if str(item).strip()
        ]
        if not criteria_lines:
            criteria_lines = ["- Use the campaign criteria already stored in PBX."]

        assignment_lines: list[str] = []
        assignments = campaign.get("assignments") or []
        if isinstance(assignments, list):
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue
                assignment_id = str(assignment.get("assignment_id") or "-")
                target_agent_id = str(assignment.get("target_agent_id") or "-")
                state = str(assignment.get("state") or "-")
                fork_agent_id = str(
                    assignment.get("operator_fork_id")
                    or assignment.get("fork_agent_id")
                    or "-"
                )
                last_command_id = str(assignment.get("last_command_id") or "-")
                last_report_id = str(assignment.get("last_report_id") or "-")
                assignment_title = str(assignment.get("title") or "").strip()
                line = (
                    f"- assignment_id={assignment_id} "
                    f"target={target_agent_id} "
                    f"state={state} "
                    f"fork={fork_agent_id} "
                    f"last_command={last_command_id} "
                    f"last_report={last_report_id}"
                )
                if assignment_title:
                    line = f"{line} title={assignment_title}"
                assignment_lines.append(line)
        if not assignment_lines:
            assignment_lines = ["- no assignments loaded"]

        return "\n".join(
            [
                f"Monitor operator campaign `{campaign_id}` for `{operator_id}`.",
                "",
                f"Title: {title}",
                f"Current status: {status}",
                "",
                "Objective:",
                objective or "-",
                "",
                "Completion criteria:",
                *criteria_lines,
                "",
                "Assignments:",
                *assignment_lines,
                "",
                "Instructions:",
                "- Keep this root operator turn active until every campaign assignment is complete, blocked, failed, or canceled.",
                "- Do not return to idle after the initial dispatch/status check unless the campaign is terminal or human input is required.",
                "- Recheck with `pbx_operator_campaign_status` at natural milestones and about every 60 seconds while work is active.",
                "- Inspect assignment reports/thread state before deciding whether follow-up is needed.",
                "- Use `pbx_operator_send_followup` only for the campaign/fork targets in this campaign. Do not dispatch to regular caller tmux panes.",
                "- Do not spawn or use Codex internal subagents such as `multi_agent_v1`; caller work must stay in visible Agent PBX fork panes.",
                "- Report assignment state changes with `pbx_operator_report_assignment`.",
                "- When all assignments are terminal, use `pbx_operator_finish_campaign` and report the final campaign result.",
            ]
        )

    async def send_operator_monitor_prompt(
        self,
        operator_id: str,
        prompt: str,
    ) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        agent = self.agents.get(operator_id)
        metadata = agent.get("metadata") if isinstance(agent, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        tmux_pane_id = str(metadata.get("tmux_pane_id") or "").strip()
        if tmux_pane_id:
            try:
                panes = await asyncio.to_thread(tmux_support.list_panes)
            except Exception as exc:
                if status is not None:
                    status.update(f"Tmux: unavailable ({exc})")
            else:
                self.tmux_panes = panes
                for pane in panes:
                    if pane.pane_id != tmux_pane_id and pane.target_label != tmux_pane_id:
                        continue
                    if isinstance(agent, dict) and not self.tmux_pane_allowed_for_agent(
                        agent,
                        pane,
                    ):
                        break
                    self.tmux_agent_targets[operator_id] = pane.pane_id
                    self.tmux_direct_agent_modes[operator_id] = True
                    self.tmux_detached_agent_ids.discard(operator_id)
                    self.save_settings()
                    return await self.send_text_to_tmux_pane(
                        pane.pane_id,
                        prompt,
                        status=status,
                    )
        return await self.send_text_to_tmux(operator_id, prompt)

    async def monitor_selected_campaign(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        agent = self.agents.get(operator_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            if detail is not None:
                detail.text = "Campaign monitoring is available for operator agents only."
            self.notify("Select an operator agent before monitoring a campaign.", severity="warning")
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = "Select a campaign before asking the operator to monitor it."
            self.notify("Select a campaign before monitoring.", severity="warning")
            return
        prompt = self.campaign_monitor_prompt(operator_id, campaign)
        sent = await self.send_operator_monitor_prompt(operator_id, prompt)
        campaign_id = str(campaign.get("campaign_id") or "")
        if not sent:
            if detail is not None:
                detail.text = (
                    f"Unable to send monitor prompt for campaign {campaign_id}.\n\n"
                    f"{self.format_campaign_detail(campaign)}"
                )
            self.notify("Unable to send monitor prompt to the operator pane.", severity="error")
            return
        if detail is not None:
            detail.text = (
                f"Monitor prompt sent to {operator_id} for campaign {campaign_id}.\n\n"
                f"{self.format_campaign_detail(campaign)}"
            )
        self.notify(f"Monitor prompt sent to {operator_id}.")
        await self.refresh_events()

    async def view_selected_campaign_report(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = "Select a campaign before viewing a report."
            self.notify("Select a campaign before viewing a report.", severity="warning")
            return
        report_ids = self.campaign_report_ids(campaign)
        if not report_ids:
            if detail is not None:
                detail.text = "Selected campaign has no generated reports yet."
            self.notify("Selected campaign has no generated reports yet.", severity="warning")
            return
        report_id = self.selected_campaign_report_id_by_operator.get(operator_id)
        if report_id not in report_ids:
            report_id = report_ids[0]
            self.selected_campaign_report_id_by_operator[operator_id] = report_id
        reports_by_id = campaign.get("_reports_by_id")
        if not isinstance(reports_by_id, dict):
            reports_by_id = {}
            campaign["_reports_by_id"] = reports_by_id
        report = reports_by_id.get(report_id)
        if not isinstance(report, dict):
            try:
                report = await self.fetch_report_by_id(report_id)
            except Exception as exc:
                report = {"report_id": report_id, "error": str(exc)}
            reports_by_id[report_id] = report
        if detail is not None:
            lines = [
                f"Campaign: {campaign.get('title')}",
                f"Campaign ID: {campaign.get('campaign_id')}",
                "",
                *self.format_report_block(report),
            ]
            detail.text = "\n".join(lines)

    async def queue_command(
        self, agent_id: str, command_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            "/v1/commands",
            json={
                "agent_id": agent_id,
                "type": command_type,
                "payload": payload,
            },
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def delete_agent(
        self, agent_id: str, *, delete_thread: bool = False
    ) -> dict[str, Any]:
        response = await self.api_client().delete(
            f"/v1/agents/{agent_id}",
            params={"delete_thread": delete_thread},
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def unhide_agent(self, agent_id: str) -> dict[str, Any]:
        response = await self.api_client().put(
            f"/v1/agents/{agent_id}/unhide",
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def set_agent_pbx_active(
        self,
        agent_id: str,
        *,
        active: bool,
    ) -> dict[str, Any]:
        response = await self.api_client().put(
            f"/v1/agents/{agent_id}/pbx-active",
            json={"active": active},
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def create_agent_report(
        self, agent_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self.api_client().post(
            f"/v1/agents/{agent_id}/reports",
            json=payload,
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def delete_queued_command(self, command_id: str) -> dict[str, Any]:
        response = await self.api_client().delete(
            f"/v1/commands/{command_id}",
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        return response.json()

    async def fetch_agent_file_listing(
        self,
        agent_id: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v1/agents/{agent_id}/files",
            params={"path": path or "."},
            headers=auth_headers(self.token),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Files response was not an object")
        return payload

    async def load_agent_files(self, agent_id: str, path: str | None = None) -> None:
        current_path = path if path is not None else self.file_path_by_agent.get(agent_id, ".")
        path_label = self.query_one("#file-path", Static)
        path_label.update(f"Path: {current_path or '.'}")
        self.set_file_preview_text(f"Loading files for {agent_id}...")
        try:
            payload = await self.fetch_agent_file_listing(agent_id, current_path or ".")
        except Exception as exc:
            self.render_file_error(agent_id, f"Unable to load files for {agent_id}: {exc}")
            return
        self.render_file_list(payload)

    async def load_parent_agent_files(self, agent_id: str) -> None:
        current = self.file_path_by_agent.get(agent_id, ".")
        parent = "."
        entries = self.file_entries_by_agent.get(agent_id, {})
        current_entry = entries.get(current)
        if isinstance(current_entry, dict):
            parent = str(current_entry.get("parent") or ".")
        else:
            parts = [part for part in current.split("/") if part and part != "."]
            parent = "/".join(parts[:-1]) if len(parts) > 1 else "."
        await self.load_agent_files(agent_id, parent)

    async def select_file_entry(self, row_key: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        entry = self.file_entries_by_agent.get(agent_id, {}).get(row_key)
        if not entry:
            return
        if entry.get("kind") == "directory":
            await self.load_agent_files(agent_id, str(entry.get("path") or "."))
            return
        await self.load_file_preview(agent_id, str(entry.get("path") or row_key))

    async def load_file_preview(self, agent_id: str, path: str) -> None:
        self.set_file_preview_text(f"Loading {path}...")
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/files/preview",
                params={"path": path},
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.set_file_preview_text(f"Unable to preview {path}: {exc}")
            return
        self.set_file_preview_payload(payload)

    def render_file_error(self, agent_id: str, message: str) -> None:
        table = self.query_one("#files", DataTable)
        table.clear()
        self.file_entries_by_agent[agent_id] = {}
        self.set_file_preview_text(message)

    def cache_file_listing(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        agent_id = str(payload.get("agent_id") or self.selected_agent_id or "")
        path = str(payload.get("path") or ".")
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        parent = payload.get("parent")
        entry_map: dict[str, dict[str, Any]] = {}
        if parent:
            entry_map[".."] = {
                "name": "..",
                "path": str(parent),
                "kind": "directory",
                "parent": parent,
            }
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_path = str(entry.get("path") or entry.get("name") or "")
            if entry_path:
                entry_map[entry_path] = entry
        if agent_id:
            self.file_path_by_agent[agent_id] = path
            self.file_entries_by_agent[agent_id] = entry_map
            self.file_directory_entries_by_agent.setdefault(agent_id, {})[path] = entry_map
        return entry_map

    def render_file_list(self, payload: dict[str, Any]) -> None:
        agent_id = str(payload.get("agent_id") or self.selected_agent_id or "")
        path = str(payload.get("path") or ".")
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        entry_map = self.cache_file_listing(payload)
        table = self.query_one("#files", DataTable)
        table.clear()
        self.query_one("#file-path", Static).update(f"Path: {path}")
        if ".." in entry_map:
            table.add_row("DIR", "..", "", "", key="..")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_path = str(entry.get("path") or entry.get("name") or "")
            if not entry_path:
                continue
            entry_map[entry_path] = entry
            table.add_row(
                self.file_entry_type(entry),
                str(entry.get("name") or entry_path),
                self.format_file_size(entry.get("size")),
                self.format_file_mtime(entry.get("mtime")),
                key=entry_path,
            )
        error = payload.get("error") if isinstance(payload.get("error"), dict) else None
        if error:
            self.set_file_preview_text(self.format_file_error(error))
        else:
            self.set_file_preview_text(
                f"Agent: {agent_id}\n"
                f"Cwd: {payload.get('cwd') or '-'}\n"
                f"Path: {path}\n"
                f"Entries: {len(entries)}\n\n"
                "Select a directory to browse it or a file to preview it."
            )

    def set_file_preview_text(self, text: str) -> None:
        preview = self.query_one("#file-preview", RichLog)
        preview.clear()
        preview.write(text)
        preview.scroll_home(animate=False, immediate=True)

    def set_file_preview_payload(self, payload: dict[str, Any]) -> None:
        preview = self.query_one("#file-preview", RichLog)
        preview.clear()
        for renderable in self.format_file_preview_renderables(payload):
            preview.write(renderable)
        preview.scroll_home(animate=False, immediate=True)

    def file_entry_type(self, entry: dict[str, Any]) -> str:
        if entry.get("kind") == "directory":
            return "DIR"
        if entry.get("is_gif"):
            return "GIF"
        if entry.get("is_image"):
            return "IMG"
        if entry.get("is_text"):
            return "TXT"
        return "BIN" if entry.get("kind") == "file" else "OTHER"

    def format_file_preview_renderables(
        self,
        payload: dict[str, Any],
    ) -> list[str | Text]:
        ansi_preview = str(payload.get("image_preview_ansi") or "").rstrip()
        if not payload.get("is_image") or not ansi_preview:
            return [self.format_file_preview(payload)]
        lines = self.format_file_preview_header(payload)
        lines.extend(
            [
                f"Image: {'GIF' if payload.get('is_gif') else 'yes'}",
                f"Dimensions: {self.format_image_dimensions(payload)}",
                "",
                self.image_preview_label(payload, color=True),
            ]
        )
        text = Text("\n".join(lines) + "\n")
        text.append(Text.from_ansi(ansi_preview))
        if payload.get("truncated"):
            text.append("\n\n[Preview truncated]")
        return [text]

    def format_file_preview(self, payload: dict[str, Any]) -> str:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else None
        if error:
            return self.format_file_error(error)
        lines = self.format_file_preview_header(payload)
        if payload.get("is_image"):
            dimensions = self.format_image_dimensions(payload)
            lines.extend(
                [
                    f"Image: {'GIF' if payload.get('is_gif') else 'yes'}",
                    f"Dimensions: {dimensions}",
                ]
            )
            preview = str(payload.get("image_preview") or "").rstrip()
            if preview:
                lines.extend(["", self.image_preview_label(payload), preview])
                if payload.get("truncated"):
                    lines.extend(["", "[Preview truncated]"])
                return "\n".join(lines)
            lines.extend(["", "Image preview is metadata-only."])
            return "\n".join(lines)
        text = payload.get("text")
        if text is not None:
            lines.extend(["", str(text)])
            if payload.get("truncated"):
                lines.extend(["", "[Preview truncated]"])
            return "\n".join(lines)
        lines.extend(["", "Binary preview is not available."])
        return "\n".join(lines)

    def format_file_preview_header(self, payload: dict[str, Any]) -> list[str]:
        return [
            f"Path: {payload.get('path') or '-'}",
            f"Type: {self.file_entry_type(payload)}",
            f"Size: {self.format_file_size(payload.get('size'))}",
            f"Modified: {self.format_file_mtime(payload.get('mtime'))}",
            f"MIME: {payload.get('mime_type') or '-'}",
        ]

    def image_preview_label(self, payload: dict[str, Any], *, color: bool = False) -> str:
        base = "GIF Preview (first frame)" if payload.get("is_gif") else "Image Preview"
        return f"{base} (color)" if color else base

    def format_file_error(self, error: dict[str, Any]) -> str:
        lines = [
            "Files unavailable",
            f"Code: {error.get('code', 'FILE_ERROR')}",
            f"Message: {error.get('message', '')}",
        ]
        remediation = error.get("remediation")
        if remediation:
            lines.append(f"Remediation: {remediation}")
        return "\n".join(lines)

    def format_image_dimensions(self, payload: dict[str, Any]) -> str:
        width = payload.get("image_width")
        height = payload.get("image_height")
        if width and height:
            return f"{width}x{height}"
        return "-"

    def format_file_size(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            size = int(value)
        except (TypeError, ValueError):
            return ""
        units = ["B", "KiB", "MiB", "GiB"]
        amount = float(size)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if unit != "B" else f"{size} B"
            amount /= 1024
        return f"{size} B"

    def format_file_mtime(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return ""
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M")

    async def refresh_joplin_status(self) -> None:
        try:
            response = await self.api_client().get(
                "/v1/joplin/status",
                headers=auth_headers(self.token),
                timeout=10,
            )
            response.raise_for_status()
            status = response.json()
            if not isinstance(status, dict):
                raise ValueError("Joplin status response was not an object")
        except Exception as exc:
            status = {
                "configured": False,
                "available": False,
                "notebook": "Agent PBX",
                "error": {
                    "code": "JOPLIN_STATUS_UNAVAILABLE",
                    "message": str(exc),
                },
            }
        self.joplin_status = status
        self.joplin_configured = bool(status.get("configured"))
        self.joplin_available = bool(status.get("available"))
        self.apply_joplin_tab_visibility()
        label = self.query_one_or_none("#joplin-status", Static)
        if label is not None:
            label.update(self.format_joplin_status_line(status))

    def apply_joplin_tab_visibility(self) -> None:
        tabs = self.query_one_or_none("#agent-tabs", TabbedContent)
        if tabs is None:
            return
        try:
            if self.joplin_configured:
                tabs.show_tab("joplin-tab")
            else:
                if tabs.active == "joplin-tab":
                    tabs.active = "latest-tab"
                    self.active_agent_tab = "latest-tab"
                tabs.hide_tab("joplin-tab")
        except Exception:
            return

    def format_joplin_status_line(self, status: dict[str, Any]) -> str:
        if status.get("available"):
            sync = status.get("sync") if isinstance(status.get("sync"), dict) else None
            sync_text = f" | {self.format_joplin_sync_summary(sync)}" if sync else ""
            return f"Joplin: {status.get('notebook') or 'Agent PBX'}{sync_text}"
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = error.get("code") or "JOPLIN_UNAVAILABLE"
        return f"Joplin: {code}"

    def format_joplin_sync_summary(self, sync: dict[str, Any] | None) -> str:
        if not sync or not sync.get("enabled"):
            return "Sync: off"
        running = int(sync.get("running") or 0)
        pending = int(sync.get("pending") or 0)
        if running:
            return f"Sync: running {running}"
        if pending:
            return f"Sync: queued {pending}"
        latest = sync.get("latest")
        if isinstance(latest, dict):
            latest_status = str(latest.get("status") or "").lower()
            if latest_status == "failed" and latest.get("error"):
                return "Sync: error"
            if latest_status == "succeeded":
                return "Sync: ok"
        latest_error = sync.get("latest_error")
        if isinstance(latest_error, dict) and latest_error.get("error"):
            return "Sync: error"
        if sync.get("latest_success"):
            return "Sync: ok"
        return "Sync: ready"

    async def fetch_joplin_note_summaries(
        self,
        agent_id: str,
    ) -> dict[str, dict[str, Any]]:
        await self.refresh_joplin_status()
        if not (self.joplin_configured and self.joplin_available):
            raise RuntimeError(self.format_joplin_unavailable_summary(self.joplin_status))
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        project = self.joplin_project_for_agent(agent_id)
        response = await self.api_client().get(
            self.project_joplin_notes_url(project),
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        notes = response.json()
        if not isinstance(notes, list):
            raise ValueError("Joplin notes response was not a list")
        existing = self.joplin_notes_by_agent.get(cache_agent_id, {})
        note_map: dict[str, dict[str, Any]] = {}
        for note in notes:
            if not isinstance(note, dict):
                continue
            note_id = str(note.get("id") or "")
            if not note_id:
                continue
            merged = dict(existing.get(note_id, {}))
            merged.update(note)
            note_map[note_id] = merged
        self.joplin_notes_by_agent[cache_agent_id] = note_map
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent[agent_id] = note_map
        return note_map

    def resolve_joplin_note_ref_token(
        self,
        token: str,
        notes: dict[str, dict[str, Any]],
    ) -> str | None:
        token_map = joplin_note_ref_tokens(notes.values())
        lower_map = {candidate.lower(): note_id for candidate, note_id in token_map.items()}
        note_id = lower_map.get(token.lower())
        if note_id is not None:
            return note_id
        stripped = token.rstrip(".")
        if stripped != token:
            return lower_map.get(stripped.lower())
        return None

    def resolve_caller_agent_ref_token(self, token: str) -> str | None:
        token_map = caller_agent_ref_tokens(self.caller_agents())
        lower_map = {
            candidate.lower(): agent_id
            for candidate, agent_id in token_map.items()
        }
        agent_id = lower_map.get(token.lower())
        if agent_id is not None:
            return agent_id
        stripped = token.rstrip(".")
        if stripped != token:
            return lower_map.get(stripped.lower())
        return None

    def caller_agent_reference_from_agent(
        self,
        token: str,
        agent: dict[str, Any],
        *,
        logical_operator_id: str | None = None,
        active_fork: dict[str, Any] | None = None,
    ) -> CallerAgentReference:
        agent_id = str(agent.get("agent_id") or "")
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        tmux_pane = self.tmux_agent_targets.get(agent_id)
        if not tmux_pane:
            for key in ("tmux_pane_id", "tmux_pane", "pane_id"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    tmux_pane = value.strip()
                    break
        active_fork = active_fork or self.active_operator_fork_for_caller(
            agent,
            logical_operator_id=logical_operator_id,
        )
        active_operator_fork_id = None
        active_fork_agent_id = None
        active_fork_track_id = None
        active_fork_purpose = None
        active_fork_source_session_id = None
        active_fork_tmux_pane = None
        if active_fork is not None:
            fork_metadata = (
                active_fork.get("metadata")
                if isinstance(active_fork.get("metadata"), dict)
                else {}
            )
            active_operator_fork_id = str(active_fork.get("operator_fork_id") or "").strip() or None
            active_fork_agent_id = (
                str(active_fork.get("fork_agent_id") or active_fork.get("agent_id") or "").strip()
                or None
            )
            active_fork_track_id = (
                str(
                    active_fork.get("fork_track_id")
                    or fork_metadata.get("fork_track_id")
                    or DEFAULT_OPERATOR_FORK_TRACK_ID
                ).strip()
                or None
            )
            active_fork_purpose = (
                str(
                    active_fork.get("fork_purpose")
                    or fork_metadata.get("fork_purpose")
                    or DEFAULT_OPERATOR_FORK_PURPOSE
                ).strip()
                or None
            )
            active_fork_source_session_id = (
                str(
                    active_fork.get("source_codex_session_id")
                    or fork_metadata.get("source_codex_session_id")
                    or ""
                ).strip()
                or None
            )
            active_fork_tmux_pane = (
                str(active_fork.get("tmux_pane_id") or fork_metadata.get("tmux_pane_id") or "").strip()
                or None
            )
        return CallerAgentReference(
            token=token,
            agent_id=agent_id,
            name=str(agent["name"]) if agent.get("name") else None,
            project=str(agent.get("project") or ""),
            status=self.format_agent_status(agent),
            pbx_mode=self.agent_pbx_mode(agent),
            pbx_active=bool(agent.get("pbx_active", True)),
            active_campaign_count=int_value(agent.get("active_campaign_count")) or 0,
            tmux_pane=tmux_pane,
            active_operator_fork_id=active_operator_fork_id,
            active_fork_agent_id=active_fork_agent_id,
            active_fork_track_id=active_fork_track_id,
            active_fork_purpose=active_fork_purpose,
            active_fork_source_session_id=active_fork_source_session_id,
            active_fork_tmux_pane=active_fork_tmux_pane,
        )

    async def fetch_joplin_note_for_reference(
        self,
        agent_id: str,
        note_id: str,
    ) -> dict[str, Any]:
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        project = self.joplin_project_for_agent(agent_id)
        response = await self.api_client().get(
            self.project_joplin_notes_url(project, note_id),
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        note = response.json()
        if not isinstance(note, dict):
            raise ValueError(f"Joplin note {note_id} response was not an object")
        self.joplin_notes_by_agent.setdefault(cache_agent_id, {})[note_id] = note
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent.setdefault(agent_id, {})[note_id] = note
        return note

    async def joplin_note_reference_scopes(
        self,
        agent_id: str,
        message: str,
    ) -> list[tuple[str, str, str | None]] | None:
        events: list[tuple[int, int, str, str]] = []
        for match in CALLER_AGENT_REF_PATTERN.finditer(message):
            events.append((match.start(1), 0, "caller", match.group(1)))
        for match in JOPLIN_NOTE_REF_PATTERN.finditer(message):
            events.append((match.start(1), 1, "joplin", match.group(1)))
        if not any(kind == "joplin" for _start, _order, kind, _token in events):
            return []
        if not agent_id:
            self.notify(
                "Select an agent before sending @joplin note references.",
                severity="warning",
            )
            return None
        if any(kind == "caller" for _start, _order, kind, _token in events) and not (
            self.is_operator_agent_id(agent_id)
        ):
            self.notify(
                "Select an operator before sending caller-scoped @joplin references.",
                severity="warning",
            )
            return None

        events.sort(key=lambda event: (event[0], event[1]))
        current_scope_agent_id = agent_id
        current_caller_token: str | None = None
        refreshed_agents = False
        seen: set[tuple[str, str]] = set()
        scopes: list[tuple[str, str, str | None]] = []
        for _start, _order, kind, token in events:
            if kind == "caller":
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None and not refreshed_agents:
                    await self.refresh_agents()
                    refreshed_agents = True
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                current_scope_agent_id = target_agent_id
                current_caller_token = token
                continue
            key = (current_scope_agent_id, token.lower())
            if key in seen:
                continue
            seen.add(key)
            scopes.append((token, current_scope_agent_id, current_caller_token))
        return scopes

    def github_reference_events(
        self,
        message: str,
    ) -> list[tuple[int, int, str, str, int | None]]:
        events: list[tuple[int, int, str, str, int | None]] = []
        for match in CALLER_AGENT_REF_PATTERN.finditer(message):
            events.append((match.start(1), 0, "caller", match.group(1), None))
        for match in PULL_REQUEST_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    1,
                    "pull_request",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in ISSUE_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    1,
                    "issue",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in PULL_REQUEST_NATURAL_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    2,
                    "pull_request",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        for match in ISSUE_NATURAL_REF_PATTERN.finditer(message):
            events.append(
                (
                    match.start(1),
                    2,
                    "issue",
                    match.group(1),
                    github_ref_number(match.group(2)),
                )
            )
        events.sort(key=lambda event: (event[0], event[1]))
        return events

    async def github_reference_scopes(
        self,
        agent_id: str,
        message: str,
    ) -> list[GitHubReferenceScope] | None:
        events = self.github_reference_events(message)
        if not any(kind in {"pull_request", "issue"} for _s, _o, kind, _t, _n in events):
            return []
        if not agent_id:
            self.notify(
                "Select an agent before sending GitHub PR or issue references.",
                severity="warning",
            )
            return None
        if any(kind == "caller" for _s, _o, kind, _t, _n in events) and not (
            self.is_operator_agent_id(agent_id)
        ):
            self.notify(
                "Select an operator before sending caller-scoped GitHub references.",
                severity="warning",
            )
            return None

        default_scope_agent_id = self.default_github_reference_scope_agent_id(agent_id)
        current_scope_agent_id = default_scope_agent_id or agent_id
        current_caller_token: str | None = None
        operator_without_default_scope = (
            self.is_operator_agent_id(agent_id) and default_scope_agent_id is None
        )
        refreshed_agents = False
        caller_tokens = [
            token
            for _start, _order, kind, token, _number in events
            if kind == "caller"
        ]
        unique_caller_tokens = list(dict.fromkeys(caller_tokens))
        if operator_without_default_scope and len(unique_caller_tokens) == 1:
            only_caller_token = unique_caller_tokens[0]
            target_agent_id = self.resolve_caller_agent_ref_token(only_caller_token)
            if target_agent_id is None:
                await self.refresh_agents()
                refreshed_agents = True
                target_agent_id = self.resolve_caller_agent_ref_token(only_caller_token)
            if target_agent_id is not None:
                current_scope_agent_id = target_agent_id
                current_caller_token = only_caller_token
                operator_without_default_scope = False
        seen: set[tuple[str, str, int]] = set()
        scopes: list[GitHubReferenceScope] = []
        for _start, _order, kind, token, number in events:
            if kind == "caller":
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None and not refreshed_agents:
                    await self.refresh_agents()
                    refreshed_agents = True
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                current_scope_agent_id = target_agent_id
                current_caller_token = token
                continue
            if number is None:
                continue
            if operator_without_default_scope and current_caller_token is None:
                raise ValueError(
                    f"{token} needs a caller scope; add @caller:<agent> "
                    "or start the operator from a caller."
                )
            key = (kind, current_scope_agent_id, number)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(
                GitHubReferenceScope(
                    kind=kind,
                    token=token,
                    number=number,
                    scope_agent_id=current_scope_agent_id,
                    caller_token=current_caller_token,
                )
            )
        return scopes

    async def expand_github_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        try:
            scopes = await self.github_reference_scopes(
                agent_id,
                reference_text or message,
            )
        except Exception as exc:
            self.notify(f"GitHub reference failed: {exc}", severity="error")
            return None
        if scopes is None:
            return None
        if not scopes:
            return message
        try:
            references: list[GitHubPromptReference] = []
            for scope in scopes:
                if scope.kind == "pull_request":
                    payload = await self.fetch_pull_request_for_reference(
                        scope.scope_agent_id,
                        scope.number,
                    )
                elif scope.kind == "issue":
                    payload = await self.fetch_issue_for_reference(
                        scope.scope_agent_id,
                        scope.number,
                    )
                else:
                    continue
                references.append(
                    GitHubPromptReference(
                        kind=scope.kind,
                        token=scope.token,
                        number=scope.number,
                        scope_agent_id=scope.scope_agent_id,
                        scope_project=self.project_for_agent(scope.scope_agent_id),
                        payload=payload,
                        caller_token=scope.caller_token,
                    )
                )
        except Exception as exc:
            self.notify(f"GitHub reference failed: {exc}", severity="error")
            return None
        return self.format_github_references_for_prompt(message, references)

    def format_github_references_for_prompt(
        self,
        message: str,
        references: list[GitHubPromptReference],
    ) -> str:
        if not references:
            return message
        lines = [
            message.rstrip(),
            "",
            "---",
            "",
            "## GitHub PR and Issue References",
            "",
        ]
        for index, reference in enumerate(references, start=1):
            if reference.kind == "pull_request":
                label = "Pull Request"
                title = str(reference.payload.get("title") or "")
                heading = f"PR #{reference.number}"
                detail = self.format_pull_request_detail(reference.payload)
            else:
                label = "Issue"
                title = str(reference.payload.get("title") or "")
                heading = f"Issue #{reference.number}"
                detail = self.format_issue_detail(reference.payload)
            if title:
                heading = f"{heading} - {title}"
            fence = markdown_fence_for(detail)
            lines.extend(
                [
                    f"### {index}. {heading}",
                    "",
                    f"- Ref: `{reference.token}`",
                    f"- Type: `{label}`",
                    f"- Number: `{reference.number}`",
                    f"- Agent Scope: `{reference.scope_agent_id}`",
                    f"- Project: `{reference.scope_project}`",
                ]
            )
            if reference.caller_token:
                lines.append(f"- Caller Ref: `{reference.caller_token}`")
            if reference.payload.get("repo"):
                lines.append(f"- Repo: `{reference.payload.get('repo')}`")
            if reference.payload.get("url"):
                lines.append(f"- URL: `{reference.payload.get('url')}`")
            lines.extend(["", f"{fence}text", detail, fence, ""])
        return "\n".join(lines).rstrip() + "\n"

    async def expand_joplin_note_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        try:
            scopes = await self.joplin_note_reference_scopes(
                agent_id,
                reference_text or message,
            )
        except Exception as exc:
            self.notify(f"Joplin note reference failed: {exc}", severity="error")
            return None
        if scopes is None:
            return None
        if not scopes:
            return message
        try:
            notes_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
            references: list[JoplinNoteReference] = []
            for token, scope_agent_id, caller_token in scopes:
                notes = notes_by_agent.get(scope_agent_id)
                if notes is None:
                    notes = await self.fetch_joplin_note_summaries(scope_agent_id)
                    notes_by_agent[scope_agent_id] = notes
                note_id = self.resolve_joplin_note_ref_token(token, notes)
                if note_id is None:
                    project = self.joplin_project_for_agent(scope_agent_id)
                    raise ValueError(
                        f"No scoped Joplin note matches {token} in {project}"
                    )
                note = await self.fetch_joplin_note_for_reference(scope_agent_id, note_id)
                scoped_by_caller = caller_token is not None
                references.append(
                    JoplinNoteReference(
                        token=token,
                        note_id=note_id,
                        title=str(note.get("title") or note_id),
                        body=str(note.get("body") or ""),
                        updated_time=note.get("updated_time"),
                        scope_agent_id=scope_agent_id if scoped_by_caller else None,
                        scope_project=(
                            self.joplin_project_for_agent(scope_agent_id)
                            if scoped_by_caller
                            else None
                        ),
                        caller_token=caller_token,
                    )
                )
        except Exception as exc:
            self.notify(f"Joplin note reference failed: {exc}", severity="error")
            return None
        return format_joplin_note_references_for_prompt(message, references)

    async def expand_caller_agent_references(
        self,
        agent_id: str,
        message: str,
        *,
        reference_text: str | None = None,
    ) -> str | None:
        tokens = caller_agent_ref_tokens_in_message(reference_text or message)
        if not tokens:
            return message
        if not agent_id or not self.is_operator_agent_id(agent_id):
            self.notify(
                "Select an operator before sending @caller references.",
                severity="warning",
            )
            return None
        operator_agent = self.agents.get(agent_id)
        logical_operator_id = (
            self.logical_operator_id_for_agent(operator_agent)
            if operator_agent is not None
            else agent_id
        )
        ensure_forks = (
            operator_agent is not None
            and self.agent_type(operator_agent) == OPERATOR_AGENT_TYPE
            and self.operator_role(operator_agent) == OPERATOR_ROLE_ROOT
        )
        try:
            references: list[CallerAgentReference] = []
            seen_agent_ids: set[str] = set()
            ensured_forks_by_caller: dict[str, dict[str, Any]] = {}
            for token in tokens:
                target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    await self.refresh_agents()
                    target_agent_id = self.resolve_caller_agent_ref_token(token)
                if target_agent_id is None:
                    raise ValueError(f"No caller agent matches {token}")
                if target_agent_id in seen_agent_ids:
                    continue
                target_agent = self.agents.get(target_agent_id)
                if target_agent is None:
                    raise ValueError(f"Caller agent {target_agent_id} is not loaded")
                active_fork: dict[str, Any] | None = None
                if ensure_forks:
                    active_fork = ensured_forks_by_caller.get(target_agent_id)
                    if active_fork is None:
                        active_fork = await self.ensure_operator_fork_from_tui(
                            logical_operator_id=logical_operator_id,
                            source_caller_agent_id=target_agent_id,
                        )
                        if active_fork is not None:
                            ensured_forks_by_caller[target_agent_id] = active_fork
                        else:
                            self.notify(
                                (
                                    "Operator fork unavailable for "
                                    f"{target_agent_id}; sending caller reference "
                                    f"to {agent_id}."
                                ),
                                severity="warning",
                            )
                references.append(
                    self.caller_agent_reference_from_agent(
                        token,
                        target_agent,
                        logical_operator_id=logical_operator_id,
                        active_fork=active_fork,
                    )
                )
                seen_agent_ids.add(target_agent_id)
        except Exception as exc:
            self.notify(f"Caller agent reference failed: {exc}", severity="error")
            return None
        return format_caller_agent_references_for_prompt(message, references)

    async def expand_prompt_references(
        self,
        agent_id: str,
        message: str,
    ) -> str | None:
        reference_text = message
        expanded_message = await self.expand_github_references(
            agent_id,
            message,
            reference_text=reference_text,
        )
        if expanded_message is None:
            return None
        expanded_message = await self.expand_joplin_note_references(
            agent_id,
            expanded_message,
            reference_text=reference_text,
        )
        if expanded_message is None:
            return None
        return await self.expand_caller_agent_references(
            agent_id,
            expanded_message,
            reference_text=reference_text,
        )

    async def route_operator_prompt_to_fork(
        self,
        agent_id: str,
        message: str,
    ) -> str | None:
        agent = self.agents.get(agent_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            return agent_id
        _ = message
        return agent_id

    async def load_joplin_notes(self, agent_id: str) -> None:
        await self.refresh_joplin_status()
        table = self.query_one_or_none("#joplin-notes", DataTable)
        body = self.query_one_or_none("#joplin-body", TextArea)
        if table is None or body is None:
            return
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        project = self.joplin_project_for_agent(agent_id)
        if not self.joplin_configured:
            table.clear()
            body.text = self.format_joplin_unavailable(self.joplin_status)
            return
        if not self.joplin_available:
            table.clear()
            body.text = self.format_joplin_unavailable(self.joplin_status)
            return
        body.text = f"Loading project Joplin notes for {project}..."
        try:
            response = await self.api_client().get(
                self.project_joplin_notes_url(project),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            notes = response.json()
        except Exception as exc:
            table.clear()
            body.text = f"Unable to load Joplin notes for {project}: {exc}"
            return
        self.render_joplin_notes(agent_id, notes)
        if notes:
            selected_note_id = self.selected_joplin_note_for_agent(agent_id)
            note_id = (
                selected_note_id
                if selected_note_id
                in self.joplin_notes_by_agent.get(cache_agent_id, {})
                else str(notes[0]["id"])
            )
            await self.select_joplin_note(note_id, agent_id=agent_id)
        else:
            self.set_selected_joplin_note_for_agent(agent_id, None)
            body.text = "No project-scoped Joplin notes yet."

    def render_joplin_notes(
        self,
        agent_id: str,
        notes: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#joplin-notes", DataTable)
        table.clear()
        note_map: dict[str, dict[str, Any]] = {}
        for note in notes:
            note_id = str(note.get("id") or "")
            if not note_id:
                continue
            note_map[note_id] = note
            table.add_row(
                self.format_joplin_time(note.get("updated_time")),
                str(note.get("title") or note_id),
                key=note_id,
            )
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        self.joplin_notes_by_agent[cache_agent_id] = note_map
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent[agent_id] = note_map

    def joplin_selection_key(self, agent_id: str) -> str:
        return self.joplin_note_cache_agent_id(agent_id)

    def selected_joplin_note_for_agent(self, agent_id: str) -> str | None:
        key = self.joplin_selection_key(agent_id)
        note_id = self.selected_joplin_note_id_by_agent.get(key)
        if note_id:
            return note_id
        if not self.selected_joplin_note_id:
            return None
        notes = self.joplin_notes_by_agent.get(key)
        if notes and self.selected_joplin_note_id not in notes:
            return None
        return self.selected_joplin_note_id

    def set_selected_joplin_note_for_agent(
        self,
        agent_id: str,
        note_id: str | None,
    ) -> None:
        key = self.joplin_selection_key(agent_id)
        if note_id:
            self.selected_joplin_note_id_by_agent[key] = note_id
        else:
            self.selected_joplin_note_id_by_agent.pop(key, None)
        self.selected_joplin_note_id = note_id

    async def select_joplin_note(
        self,
        note_id: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        agent_id = agent_id or self.selected_agent_id
        if not agent_id:
            return
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        project = self.joplin_project_for_agent(agent_id)
        body = self.query_one("#joplin-body", TextArea)
        body.text = f"Loading Joplin note {note_id}..."
        try:
            response = await self.api_client().get(
                self.project_joplin_notes_url(project, note_id),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            body.text = f"Unable to load Joplin note {note_id}: {exc}"
            return
        self.set_selected_joplin_note_for_agent(agent_id, note_id)
        self.joplin_notes_by_agent.setdefault(cache_agent_id, {})[note_id] = note
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent.setdefault(agent_id, {})[note_id] = note
        body.text = str(note.get("body") or "")
        table = self.query_one_or_none("#joplin-notes", DataTable)
        if table is not None and note_id in self.joplin_notes_by_agent.get(cache_agent_id, {}):
            try:
                table.move_cursor(
                    row=table.get_row_index(note_id),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def current_joplin_note_title(self, agent_id: str, note_id: str | None) -> str:
        if not note_id:
            return ""
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        note = self.joplin_notes_by_agent.get(cache_agent_id, {}).get(note_id, {})
        return str(note.get("title") or note_id)

    def open_joplin_title_modal(self, agent_id: str, *, action: str) -> None:
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if action == "rename" and not note_id:
            self.notify("Select a Joplin note before renaming.", severity="warning")
            return
        current_title = (
            self.current_joplin_note_title(agent_id, note_id)
            if action == "rename"
            else ""
        )
        self.push_screen(
            JoplinNoteTitleScreen(
                agent_id=agent_id,
                action=action,
                note_id=note_id if action == "rename" else None,
                current_title=current_title,
            )
        )

    async def joplin_title_action_for_agent(
        self,
        agent_id: str,
        action: str,
        title: str,
        *,
        note_id: str | None = None,
    ) -> None:
        if action == "new":
            await self.create_joplin_note(agent_id, title=title)
            return
        if action == "rename":
            await self.rename_joplin_note(agent_id, title=title, note_id=note_id)
            return
        self.notify(f"Unknown Joplin action: {action}", severity="warning")

    async def create_joplin_note(self, agent_id: str, *, title: str) -> None:
        if not await self.ensure_joplin_available():
            return
        project = self.joplin_project_for_agent(agent_id)
        body = f"# {title.strip()}\n\n"
        try:
            response = await self.api_client().post(
                self.project_joplin_notes_url(project),
                json={"title": title.strip(), "body": body},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            self.notify(f"Joplin note create failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(
            agent_id,
            str(note.get("id") or "").strip() or None,
        )
        self.notify("Joplin note created.")
        await self.load_joplin_notes(agent_id)

    async def rename_joplin_note(
        self,
        agent_id: str,
        *,
        title: str,
        note_id: str | None = None,
    ) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = note_id or self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before renaming.", severity="warning")
            return
        project = self.joplin_project_for_agent(agent_id)
        try:
            response = await self.api_client().put(
                self.project_joplin_notes_url(project, note_id),
                json={"title": title.strip()},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Joplin rename failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(agent_id, note_id)
        self.notify("Joplin note renamed.")
        await self.load_joplin_notes(agent_id)

    def confirm_delete_joplin_note(self, agent_id: str) -> None:
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before deleting.", severity="warning")
            return
        self.push_screen(
            JoplinDeleteConfirmScreen(
                agent_id=agent_id,
                note_id=note_id,
                title=self.current_joplin_note_title(agent_id, note_id),
            )
        )

    async def delete_joplin_note(self, agent_id: str, note_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        project = self.joplin_project_for_agent(agent_id)
        try:
            response = await self.api_client().delete(
                self.project_joplin_notes_url(project, note_id),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Joplin delete failed: {exc}", severity="error")
            return
        if self.selected_joplin_note_for_agent(agent_id) == note_id:
            self.set_selected_joplin_note_for_agent(agent_id, None)
        self.notify("Joplin note deleted.")
        await self.load_joplin_notes(agent_id)

    async def save_joplin_note(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before saving.", severity="warning")
            return
        project = self.joplin_project_for_agent(agent_id)
        body = self.query_one("#joplin-body", TextArea)
        try:
            response = await self.api_client().put(
                self.project_joplin_notes_url(project, note_id),
                json={"body": body.text},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Joplin save failed: {exc}", severity="error")
            return
        self.notify("Joplin note saved.")
        await self.load_joplin_notes(agent_id)

    async def copy_latest_to_joplin(self, agent_id: str) -> None:
        if self.is_tmux_direct_enabled(agent_id):
            await self.copy_tmux_response_to_joplin(agent_id)
            return
        await self.copy_latest_report_to_joplin(agent_id)

    async def copy_tmux_response_text(self, agent_id: str) -> tuple[str, str] | None:
        try:
            previous_clipboard, _previous_source = await asyncio.to_thread(
                read_clipboard_text
            )
        except Exception:
            previous_clipboard = ""
        sent = await self.send_keys_to_tmux(agent_id, "/copy")
        if not sent:
            return None
        try:
            return await self.read_copied_tmux_response(
                previous_clipboard
            )
        except Exception as exc:
            self.notify(f"Joplin copy failed: {exc}", severity="error")
            return

    async def copy_tmux_response_to_joplin(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        copied = await self.copy_tmux_response_text(agent_id)
        if copied is None:
            return
        response_text, clipboard_source = copied
        prompt = self.latest_joplin_prompt_for_agent(agent_id)
        body = format_joplin_tmux_response_copy_body(prompt, response_text)
        title = joplin_tmux_copy_title(prompt)
        await self.create_manual_joplin_copy(
            agent_id,
            title=title,
            body=body,
            success_message=f"Copied Codex response to Joplin via {clipboard_source}.",
        )
        await self.load_tmux_capture(agent_id)

    async def append_joplin_log_section(
        self,
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> bool:
        if not self.joplin_configured or not self.joplin_available:
            return False
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/joplin/log/append",
                json={"title": title, "body": body},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            log = response.json()
        except Exception as exc:
            self.notify(f"Joplin LOG append failed: {exc}", severity="error")
            return False
        return bool(log)

    def should_auto_log_tmux_message(self, message: str) -> bool:
        clean = message.strip()
        if not clean:
            return False
        if clean.lower() in {PLAN_SLASH_COMMAND, "/copy"}:
            return False
        return True

    async def record_tmux_joplin_interaction(
        self,
        agent_id: str,
        message: str,
    ) -> None:
        if not self.should_auto_log_tmux_message(message):
            return
        active = await self.append_joplin_log_section(
            agent_id,
            title="Operator Prompt",
            body=message,
        )
        if not active:
            return
        self.run_worker(
            self.capture_tmux_joplin_log_response(agent_id),
            name=f"joplin-tmux-log-{slugify(agent_id)}",
            group=f"joplin-tmux-log-{slugify(agent_id)}",
            exclusive=True,
            exit_on_error=False,
        )

    async def capture_tmux_display_for_agent(self, agent_id: str) -> str | None:
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception:
            return None
        self.tmux_panes = panes
        pane, mode = self.resolve_tmux_pane(agent_id, panes)
        if pane is None and mode == "auto":
            pane = await self.resolve_tmux_plan_selector_pane(agent_id, panes)
        if pane is None:
            return None
        try:
            captured = await asyncio.to_thread(
                tmux_support.capture_pane,
                pane.pane_id,
                lines=self.tmux_capture_lines,
            )
        except Exception:
            return None
        displayed = self.crop_tmux_capture_for_display(captured or "(empty tmux pane)")
        self.record_tmux_capture_liveness(agent_id, pane.pane_id, displayed)
        return displayed

    async def wait_for_tmux_log_idle(self, agent_id: str) -> bool:
        await asyncio.sleep(JOPLIN_TMUX_LOG_MIN_WAIT_SECONDS)
        deadline = time.monotonic() + JOPLIN_TMUX_LOG_TIMEOUT_SECONDS
        last_hash: str | None = None
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            captured = await self.capture_tmux_display_for_agent(agent_id)
            if captured is None:
                await asyncio.sleep(1.0)
                continue
            capture_hash = hashlib.sha256(
                captured.encode("utf-8", errors="replace")
            ).hexdigest()
            now = time.monotonic()
            if capture_hash != last_hash:
                last_hash = capture_hash
                stable_since = now
            elif now - stable_since >= JOPLIN_TMUX_LOG_IDLE_SECONDS:
                return True
            await asyncio.sleep(1.0)
        return False

    async def capture_tmux_joplin_log_response(self, agent_id: str) -> None:
        if not await self.wait_for_tmux_log_idle(agent_id):
            self.notify(
                f"Joplin LOG response capture timed out for {agent_id}.",
                severity="warning",
            )
            return
        copied = await self.copy_tmux_response_text(agent_id)
        if copied is None:
            return
        response_text, clipboard_source = copied
        body = f"Clipboard: {clipboard_source}\n\n{response_text.strip()}"
        appended = await self.append_joplin_log_section(
            agent_id,
            title="Agent Response",
            body=body,
        )
        if appended and agent_id == self.selected_agent_id:
            await self.load_joplin_notes(agent_id)
            if self.active_agent_tab == "latest-tab":
                await self.load_tmux_capture(agent_id)

    async def read_copied_tmux_response(
        self,
        previous_clipboard: str,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + CLIPBOARD_COPY_WAIT_SECONDS
        while True:
            text, source = await asyncio.to_thread(read_clipboard_text)
            if text.strip() and text != previous_clipboard:
                return text, source
            if time.monotonic() >= deadline:
                if text.strip():
                    return text, source
                break
            await asyncio.sleep(CLIPBOARD_COPY_POLL_SECONDS)
        raise RuntimeError(
            "Codex /copy did not produce readable clipboard text. "
            "Install a clipboard reader or verify terminal clipboard integration."
        )

    def latest_joplin_prompt_for_agent(self, agent_id: str) -> str:
        history = self.sent_message_history_by_agent.get(agent_id) or []
        return latest_nonlocal_sent_prompt(
            history,
            is_local_command=lambda message: self.slash_command_for_text(message)
            is not None,
        )

    async def create_manual_joplin_copy(
        self,
        agent_id: str,
        *,
        title: str,
        body: str,
        success_message: str,
    ) -> None:
        if not await self.ensure_joplin_available():
            return
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/joplin/copy",
                json={"title": title, "body": body},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            self.notify(f"Joplin copy failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(
            agent_id,
            str(note.get("id") or "").strip() or None,
        )
        self.notify(success_message)
        await self.load_joplin_notes(agent_id)

    async def copy_latest_report_to_joplin(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/joplin/copy",
                json={},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            self.notify(f"Joplin copy failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(
            agent_id,
            str(note.get("id") or "").strip() or None,
        )
        self.notify("Copied latest PBX report to Joplin.")
        await self.load_joplin_notes(agent_id)

    async def start_joplin_log(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/joplin/log/start",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            log = response.json()
        except Exception as exc:
            self.notify(f"Joplin LOG start failed: {exc}", severity="error")
            return
        state = "already active" if log.get("already_active") else "started"
        self.notify(f"Joplin LOG {state}.")
        await self.load_joplin_notes(agent_id)

    async def stop_joplin_log(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        try:
            response = await self.api_client().post(
                f"/v1/agents/{agent_id}/joplin/log/stop",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            log = response.json()
        except Exception as exc:
            self.notify(f"Joplin LOG stop failed: {exc}", severity="error")
            return
        if log:
            self.notify("Joplin LOG stopped.")
        else:
            self.notify("No active Joplin LOG for this agent.", severity="warning")
        await self.load_joplin_notes(agent_id)

    async def sync_joplin_now(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        try:
            response = await self.api_client().post(
                "/v1/joplin/sync",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            job = response.json()
        except Exception as exc:
            self.notify(f"Joplin sync failed to queue: {exc}", severity="error")
            return
        sync_id = str(job.get("sync_id") or "")
        self.notify(f"Joplin sync queued {sync_id[:8]}.")
        await self.refresh_joplin_status()

    async def ensure_joplin_available(self) -> bool:
        await self.refresh_joplin_status()
        if self.joplin_configured and self.joplin_available:
            return True
        self.notify(
            self.format_joplin_unavailable_summary(self.joplin_status),
            severity="warning",
        )
        return False

    def format_joplin_unavailable_summary(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = str(error.get("code") or "JOPLIN_UNAVAILABLE")
        message = str(error.get("message") or "")
        if message:
            return f"{code}: {message}"
        return code

    def format_joplin_unavailable(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        lines = [
            "Joplin unavailable",
            f"Code: {error.get('code', 'JOPLIN_UNAVAILABLE')}",
            f"Message: {error.get('message', '')}",
        ]
        remediation = error.get("remediation")
        if remediation:
            lines.append(f"Remediation: {remediation}")
        return "\n".join(lines)

    def format_joplin_time(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return ""
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")

    def activate_pull_requests_tab(self) -> None:
        self.activate_agent_tab("pull-requests-tab")

    async def fetch_pull_request_summaries(
        self,
        agent_id: str,
    ) -> dict[int, dict[str, Any]]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/pull-requests",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("pull request list response was not an object")
        self.pull_request_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "pull_requests"
        }
        if repo_agent_id != agent_id:
            self.pull_request_status_by_agent[repo_agent_id] = self.pull_request_status_by_agent[
                agent_id
            ]
        pulls = payload.get("pull_requests") if isinstance(payload.get("pull_requests"), list) else []
        pull_map: dict[int, dict[str, Any]] = {}
        for item in pulls:
            if not isinstance(item, dict):
                continue
            number = github_ref_number(item.get("number"))
            if number is None:
                continue
            pull_map[number] = item
        self.pull_requests_by_agent[agent_id] = pull_map
        if repo_agent_id != agent_id:
            self.pull_requests_by_agent[repo_agent_id] = pull_map
        return pull_map

    async def fetch_pull_request_for_reference(
        self,
        agent_id: str,
        number: int,
    ) -> dict[str, Any]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/pull-requests/{number}",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        pull = response.json()
        if not isinstance(pull, dict):
            raise ValueError(f"pull request #{number} response was not an object")
        self.pull_requests_by_agent.setdefault(agent_id, {})[number] = pull
        if repo_agent_id != agent_id:
            self.pull_requests_by_agent.setdefault(repo_agent_id, {})[number] = pull
        return pull

    async def pull_request_action_for_agent(
        self,
        agent_id: str,
        action: str,
    ) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_pull_requests_tab()
        self.update_agent_title()
        if action in {"open", "refresh"}:
            await self.load_pull_requests(agent_id)
        elif action == "review":
            await self.request_pull_request_review(agent_id)
        elif action == "validate":
            await self.request_pull_request_validation(agent_id)
        elif action == "url":
            self.show_selected_pull_request_url(agent_id)
        elif action == "merge":
            self.confirm_merge_pull_request(agent_id)
        else:
            self.notify(f"Unknown PR action: {action}", severity="warning")

    async def load_pull_requests(self, agent_id: str) -> None:
        table = self.query_one_or_none("#pull-requests", DataTable)
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        status_label = self.query_one_or_none("#pull-request-status", Static)
        if table is None or detail is None:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        scope_note = (
            f" via repo context {repo_agent_id}" if repo_agent_id != agent_id else ""
        )
        detail.text = f"Loading pull requests for {agent_id}{scope_note}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/pull-requests",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            detail.text = f"Unable to load pull requests for {agent_id}{scope_note}: {exc}"
            if status_label is not None:
                status_label.update("Pull Requests: unavailable")
            return
        if status_label is not None:
            status_label.update(self.format_pull_request_status_line(payload))
        self.pull_request_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "pull_requests"
        }
        if repo_agent_id != agent_id:
            self.pull_request_status_by_agent[repo_agent_id] = self.pull_request_status_by_agent[
                agent_id
            ]
        pulls = payload.get("pull_requests") if isinstance(payload, dict) else []
        if not isinstance(pulls, list):
            pulls = []
        self.render_pull_requests(agent_id, pulls)
        if not payload.get("available"):
            detail.text = self.format_pull_request_unavailable(payload)
            return
        if not pulls:
            self.selected_pull_request_number = None
            detail.text = "No open pull requests for this agent repository."
            return
        number = self.selected_pull_request_number
        numbers = {
            int(item.get("number") or 0)
            for item in pulls
            if isinstance(item, dict)
        }
        if number not in numbers:
            number = int(pulls[0].get("number") or 0)
        if number:
            await self.select_pull_request(str(number))

    def render_pull_requests(
        self,
        agent_id: str,
        pulls: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#pull-requests", DataTable)
        table.clear()
        pull_map: dict[int, dict[str, Any]] = {}
        for item in pulls:
            number = int(item.get("number") or 0)
            if number <= 0:
                continue
            pull_map[number] = item
            state = "draft" if item.get("is_draft") else str(item.get("state") or "-")
            table.add_row(
                f"#{number}",
                state,
                self.format_pull_request_checks(item.get("checks")),
                str(item.get("title") or ""),
                key=str(number),
            )
        self.pull_requests_by_agent[agent_id] = pull_map

    async def select_pull_request(self, number_text: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            number = int(number_text)
        except ValueError:
            return
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is None:
            return
        detail.text = f"Loading PR #{number}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            pull = response.json()
        except Exception as exc:
            detail.text = f"Unable to load PR #{number}: {exc}"
            return
        self.selected_pull_request_number = number
        self.selected_pull_request_number_by_agent[agent_id] = number
        self.pull_requests_by_agent.setdefault(agent_id, {})[number] = pull
        if repo_agent_id != agent_id:
            self.selected_pull_request_number_by_agent[repo_agent_id] = number
            self.pull_requests_by_agent.setdefault(repo_agent_id, {})[number] = pull
        detail.text = self.format_pull_request_detail(pull)
        table = self.query_one_or_none("#pull-requests", DataTable)
        if table is not None:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(number)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def selected_pull_request_number_for_agent(self, agent_id: str) -> int | None:
        pulls = self.pull_requests_by_agent.get(agent_id, {})
        if self.selected_pull_request_number in pulls:
            return self.selected_pull_request_number
        table = self.query_one_or_none("#pull-requests", DataTable)
        if (
            table is not None
            and table.row_count
            and table.is_valid_row_index(table.cursor_row)
        ):
            try:
                return int(
                    table.coordinate_to_cell_key(
                        table.cursor_coordinate
                    ).row_key.value
                )
            except Exception:
                return None
        if pulls:
            return next(iter(pulls))
        return None

    def current_pull_request(self, agent_id: str) -> dict[str, Any] | None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            return None
        return self.pull_requests_by_agent.get(agent_id, {}).get(number)

    async def request_pull_request_review(self, agent_id: str) -> None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/review-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"PR review request failed: {exc}", severity="error")
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR review prompt was empty.", severity="error")
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "pull_request_review",
                    "pr_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_pull_request_action_detail(
                f"Queued PR #{number} review for {agent_id} using repo context "
                f"{repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued PR #{number} review for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR review prompt was empty.", severity="error")
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/pr review #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_pull_request_action_detail(
                f"Sent PR #{number} review prompt to tmux for {agent_id}"
                f"{scope_line}.\n\n"
                "Watch the tmux stream for Codex output.",
            )
            self.notify(f"Sent PR #{number} review to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_pull_request_action_detail(
            f"Queued PR #{number} review for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued PR #{number} review for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def request_pull_request_validation(self, agent_id: str) -> None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/workerbee-validation-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(
                f"PR WorkerBee validation request failed: {exc}",
                severity="error",
            )
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR WorkerBee validation prompt was empty.", severity="error")
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "pull_request_workerbee_validation",
                    "pr_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_pull_request_action_detail(
                f"Queued PR #{number} WorkerBee validation for {agent_id} using "
                f"repo context {repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued PR #{number} validation for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR WorkerBee validation prompt was empty.", severity="error")
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/pr validate #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_pull_request_action_detail(
                f"Sent PR #{number} WorkerBee validation prompt to tmux for "
                f"{agent_id}{scope_line}.\n\nWatch the tmux stream for Codex output."
            )
            self.notify(f"Sent PR #{number} validation to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_pull_request_action_detail(
            f"Queued PR #{number} WorkerBee validation for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued PR #{number} WorkerBee validation for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    def update_pull_request_action_detail(self, message: str) -> None:
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is not None:
            detail.text = message

    def activate_issues_tab(self) -> None:
        self.activate_agent_tab("issues-tab")

    async def fetch_issue_summaries(
        self,
        agent_id: str,
    ) -> dict[int, dict[str, Any]]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/issues",
            params={"state": "open", "limit": 30},
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("issue list response was not an object")
        self.issue_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "issues"
        }
        if repo_agent_id != agent_id:
            self.issue_status_by_agent[repo_agent_id] = self.issue_status_by_agent[
                agent_id
            ]
        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        issue_map: dict[int, dict[str, Any]] = {}
        for item in issues:
            if not isinstance(item, dict):
                continue
            number = github_ref_number(item.get("number"))
            if number is None:
                continue
            issue_map[number] = item
        self.issues_by_agent[agent_id] = issue_map
        if repo_agent_id != agent_id:
            self.issues_by_agent[repo_agent_id] = issue_map
        return issue_map

    async def fetch_issue_for_reference(
        self,
        agent_id: str,
        number: int,
    ) -> dict[str, Any]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/issues/{number}",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        issue = response.json()
        if not isinstance(issue, dict):
            raise ValueError(f"issue #{number} response was not an object")
        self.issues_by_agent.setdefault(agent_id, {})[number] = issue
        if repo_agent_id != agent_id:
            self.issues_by_agent.setdefault(repo_agent_id, {})[number] = issue
        return issue

    async def issue_action_for_agent(
        self,
        agent_id: str,
        action: str,
    ) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_issues_tab()
        self.update_agent_title()
        if action in {"open", "refresh"}:
            await self.load_issues(agent_id)
        elif action == "mitigate":
            await self.request_issue_mitigation(agent_id)
        elif action == "url":
            self.show_selected_issue_url(agent_id)
        elif action == "clear":
            self.confirm_clear_issue(agent_id)
        else:
            self.notify(f"Unknown issue action: {action}", severity="warning")

    async def load_issues(self, agent_id: str) -> None:
        table = self.query_one_or_none("#issues", DataTable)
        detail = self.query_one_or_none("#issue-detail", TextArea)
        status_label = self.query_one_or_none("#issue-status", Static)
        if table is None or detail is None:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        scope_note = (
            f" via repo context {repo_agent_id}" if repo_agent_id != agent_id else ""
        )
        detail.text = f"Loading GitHub issues for {agent_id}{scope_note}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/issues",
                params={"state": "open", "limit": 30},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            detail.text = f"Unable to load GitHub issues for {agent_id}{scope_note}: {exc}"
            if status_label is not None:
                status_label.update("Issues: unavailable")
            return
        if status_label is not None:
            status_label.update(self.format_issue_status_line(payload))
        self.issue_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "issues"
        }
        if repo_agent_id != agent_id:
            self.issue_status_by_agent[repo_agent_id] = self.issue_status_by_agent[
                agent_id
            ]
        issues = payload.get("issues") if isinstance(payload, dict) else []
        if not isinstance(issues, list):
            issues = []
        self.render_issues(agent_id, issues)
        if not payload.get("available"):
            detail.text = self.format_issue_unavailable(payload)
            return
        if not issues:
            self.selected_issue_number = None
            self.selected_issue_number_by_agent.pop(agent_id, None)
            detail.text = "No open GitHub issues for this agent repository."
            return
        number = self.selected_issue_number
        numbers = {
            int(item.get("number") or 0)
            for item in issues
            if isinstance(item, dict)
        }
        if number not in numbers:
            number = next(iter(numbers))
        if number:
            await self.select_issue(str(number))

    def render_issues(
        self,
        agent_id: str,
        issues: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#issues", DataTable)
        table.clear()
        issue_map: dict[int, dict[str, Any]] = {}
        for item in issues:
            number = int(item.get("number") or 0)
            if number <= 0:
                continue
            issue_map[number] = item
            labels = ", ".join(item.get("labels") or [])
            table.add_row(
                f"#{number}",
                str(item.get("state") or "-"),
                labels or "-",
                str(item.get("title") or ""),
                self.format_issue_time(item.get("updated_at")),
                key=str(number),
            )
        self.issues_by_agent[agent_id] = issue_map

    async def select_issue(self, number_text: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            number = int(number_text)
        except ValueError:
            return
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is None:
            return
        detail.text = f"Loading issue #{number}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/issues/{number}",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            issue = response.json()
        except Exception as exc:
            detail.text = f"Unable to load issue #{number}: {exc}"
            return
        self.selected_issue_number = number
        self.selected_issue_number_by_agent[agent_id] = number
        self.issues_by_agent.setdefault(agent_id, {})[number] = issue
        if repo_agent_id != agent_id:
            self.selected_issue_number_by_agent[repo_agent_id] = number
            self.issues_by_agent.setdefault(repo_agent_id, {})[number] = issue
        detail.text = self.format_issue_detail(issue)
        table = self.query_one_or_none("#issues", DataTable)
        if table is not None:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(number)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def selected_issue_number_for_agent(self, agent_id: str) -> int | None:
        issues = self.issues_by_agent.get(agent_id, {})
        if self.selected_issue_number in issues:
            return self.selected_issue_number
        table = self.query_one_or_none("#issues", DataTable)
        if (
            table is not None
            and table.row_count
            and table.is_valid_row_index(table.cursor_row)
        ):
            try:
                return int(
                    table.coordinate_to_cell_key(
                        table.cursor_coordinate
                    ).row_key.value
                )
            except Exception:
                return None
        if issues:
            return next(iter(issues))
        return None

    def current_issue(self, agent_id: str) -> dict[str, Any] | None:
        number = self.selected_issue_number_for_agent(agent_id)
        if number is None:
            return None
        return self.issues_by_agent.get(agent_id, {}).get(number)

    async def request_issue_mitigation(self, agent_id: str) -> None:
        number = self.selected_issue_number_for_agent(agent_id)
        if number is None:
            self.notify("Select an issue first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/issues/{number}/mitigation-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"Issue mitigation request failed: {exc}", severity="error")
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("Issue mitigation prompt was empty.", severity="error")
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "issue_mitigation",
                    "issue_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_issue_action_detail(
                f"Queued issue #{number} mitigation for {agent_id} using repo "
                f"context {repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued issue #{number} mitigation for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("Issue mitigation prompt was empty.", severity="error")
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/issue mitigate #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_issue_action_detail(
                f"Sent issue #{number} mitigation prompt to tmux for {agent_id}"
                f"{scope_line}.\n\n"
                "Watch the tmux stream for Codex output."
            )
            self.notify(f"Sent issue #{number} mitigation to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_issue_action_detail(
            f"Queued issue #{number} mitigation for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued issue #{number} mitigation for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    def update_issue_action_detail(self, message: str) -> None:
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is not None:
            detail.text = message

    def show_selected_issue_url(self, agent_id: str) -> None:
        issue = self.current_issue(agent_id)
        if issue is None:
            self.notify("Select an issue first.", severity="warning")
            return
        url = str(issue.get("url") or "")
        if not url:
            self.notify("Selected issue has no URL.", severity="warning")
            return
        self.notify(url)
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is not None:
            detail.text = self.format_issue_detail(issue)

    def confirm_clear_issue(self, agent_id: str) -> None:
        issue = self.current_issue(agent_id)
        if issue is None:
            self.notify("Select an issue first.", severity="warning")
            return
        number = int(issue.get("number") or 0)
        if number <= 0:
            self.notify("Selected issue number is invalid.", severity="warning")
            return
        self.push_screen(
            IssueClearConfirmScreen(
                agent_id=agent_id,
                number=number,
                title=str(issue.get("title") or ""),
            )
        )

    async def clear_issue(
        self,
        agent_id: str,
        number: int,
        *,
        comment: str,
        confirm: str,
    ) -> None:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/issues/{number}/clear",
                json={"comment": comment, "confirm": confirm},
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Issue clear failed: {exc}", severity="error")
            self.update_issue_action_detail(f"Issue #{number} clear failed:\n{exc}")
            return
        self.notify(f"Cleared issue #{number}.")
        await self.load_issues(agent_id)

    def format_issue_status_line(self, status: dict[str, Any]) -> str:
        if not status.get("configured"):
            return "Issues: disabled"
        if not status.get("available"):
            return "Issues: unavailable"
        repo = status.get("repo") or "-"
        close = "close on" if status.get("close_enabled") else "close off"
        return f"Issues: {repo} ({close})"

    def format_issue_unavailable(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        lines = [
            "GitHub Issues unavailable",
            f"Code: {error.get('code', 'ISSUES_UNAVAILABLE')}",
            f"Message: {error.get('message', '')}",
        ]
        details = error.get("details")
        if isinstance(details, dict) and details.get("repo"):
            lines.append(f"Repo: {details.get('repo')}")
        return "\n".join(lines)

    def format_issue_detail(self, issue: dict[str, Any]) -> str:
        labels = ", ".join(issue.get("labels") or []) or "-"
        assignees = ", ".join(issue.get("assignees") or []) or "-"
        comments = issue.get("comments") if isinstance(issue.get("comments"), list) else []
        lines = [
            f"Issue #{issue.get('number')} - {issue.get('title')}",
            f"Repo: {issue.get('repo') or '-'}",
            f"State: {issue.get('state') or '-'}",
            f"Author: {issue.get('author') or '-'}",
            f"Labels: {labels}",
            f"Assignees: {assignees}",
            f"Milestone: {issue.get('milestone') or '-'}",
            f"Updated: {self.format_issue_time(issue.get('updated_at')) or '-'}",
            f"URL: {issue.get('url') or '-'}",
            "",
            str(issue.get("body") or "").strip() or "(No issue body.)",
        ]
        if comments:
            lines.extend(["", "Comments:"])
            for comment in comments[-5:]:
                if not isinstance(comment, dict):
                    continue
                body = str(comment.get("body") or "").strip()
                if len(body) > 600:
                    body = f"{body[:600]}..."
                lines.extend(
                    [
                        "",
                        f"- {comment.get('author') or '-'} at {self.format_issue_time(comment.get('created_at')) or '-'}",
                        body or "(empty comment)",
                    ]
                )
        return "\n".join(lines)

    def format_issue_time(self, value: Any) -> str:
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value[:10]
            return parsed.strftime("%Y-%m-%d")
        return self.format_joplin_time(value)

    def show_selected_pull_request_url(self, agent_id: str) -> None:
        pull = self.current_pull_request(agent_id)
        if pull is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        url = str(pull.get("url") or "")
        if not url:
            self.notify("Selected PR has no URL.", severity="warning")
            return
        self.notify(url)
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is not None:
            detail.text = self.format_pull_request_detail(pull)

    def confirm_merge_pull_request(self, agent_id: str) -> None:
        pull = self.current_pull_request(agent_id)
        if pull is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        number = int(pull.get("number") or 0)
        if number <= 0:
            self.notify("Selected PR number is invalid.", severity="warning")
            return
        self.push_screen(
            PullRequestMergeConfirmScreen(
                agent_id=agent_id,
                number=number,
                title=str(pull.get("title") or ""),
            )
        )

    async def merge_pull_request(
        self,
        agent_id: str,
        number: int,
        *,
        method: str,
        confirm: str,
    ) -> None:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/merge",
                json={"method": method, "confirm": confirm},
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            self.notify(f"PR merge failed: {exc}", severity="error")
            return
        self.notify(f"Merged PR #{number} with {result.get('method') or method}.")
        await self.load_pull_requests(agent_id)

    def format_pull_request_status_line(self, status: dict[str, Any]) -> str:
        if status.get("available"):
            repo = status.get("repo") or "-"
            merge = "on" if status.get("merge_enabled") else "off"
            return f"Pull Requests: {repo} | Merge: {merge}"
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = error.get("code") or "PR_UNAVAILABLE"
        return f"Pull Requests: {code}"

    def format_pull_request_unavailable(self, status: dict[str, Any]) -> str:
        lines = ["Pull request integration is unavailable."]
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        if error:
            lines.extend(
                [
                    "",
                    f"Code: {error.get('code', 'PR_UNAVAILABLE')}",
                    f"Message: {error.get('message', '')}",
                ]
            )
        lines.extend(
            [
                "",
                f"GitHub CLI: {status.get('gh_bin') or '-'}",
                f"Cwd: {status.get('cwd') or '-'}",
            ]
        )
        return "\n".join(lines)

    def format_pull_request_detail(self, pull: dict[str, Any]) -> str:
        files = pull.get("files") if isinstance(pull.get("files"), list) else []
        file_lines = []
        for item in files[:40]:
            if isinstance(item, dict):
                additions = item.get("additions")
                deletions = item.get("deletions")
                changed = ""
                if additions is not None or deletions is not None:
                    changed = f" (+{additions or 0}/-{deletions or 0})"
                file_lines.append(
                    f"- {item.get('path') or item.get('filename')}{changed}"
                )
        commits = pull.get("commits") if isinstance(pull.get("commits"), list) else []
        draft = " (draft)" if pull.get("is_draft") else ""
        mergeable = pull.get("mergeable") if pull.get("mergeable") is not None else "-"
        lines = [
            f"PR #{pull.get('number')} - {pull.get('title')}",
            f"Repo: {pull.get('repo') or '-'}",
            f"URL: {pull.get('url') or '-'}",
            f"State: {pull.get('state') or '-'}{draft}",
            f"Author: {pull.get('author') or '-'}",
            f"Branch: {pull.get('head_ref') or '-'} -> {pull.get('base_ref') or '-'}",
            f"Updated: {pull.get('updated_at') or '-'}",
            f"Review: {pull.get('review_decision') or '-'}",
            f"Mergeable: {mergeable}",
            f"Merge State: {pull.get('merge_state_status') or '-'}",
            f"Checks: {self.format_pull_request_checks(pull.get('checks'))}",
            f"Labels: {', '.join(pull.get('labels') or []) or '-'}",
            "",
            "Files",
            "\n".join(file_lines) if file_lines else "-",
            "",
            f"Commits: {len(commits)}",
            "",
            "Body",
            str(pull.get("body") or "-"),
        ]
        return "\n".join(lines)

    def format_pull_request_checks(self, checks: object) -> str:
        if not isinstance(checks, dict):
            return "-"
        total = int(checks.get("total") or 0)
        if total <= 0:
            return "-"
        failed = int(checks.get("failed") or 0)
        pending = int(checks.get("pending") or 0)
        success = int(checks.get("success") or 0)
        unknown = int(checks.get("unknown") or 0)
        if failed:
            return f"{failed} fail/{total}"
        if pending:
            return f"{pending} pending/{total}"
        if unknown:
            return f"{success} ok/{unknown} unk/{total}"
        return f"{success} ok/{total}"

    async def load_workerbee_status(self, agent_id: str) -> None:
        detail = self.query_one("#workerbee-detail", TextArea)
        detail.text = f"Loading WorkerBee status for {agent_id}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/workerbee",
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
            status = response.json()
        except Exception as exc:
            detail.text = f"Unable to load WorkerBee status for {agent_id}: {exc}"
            return
        self.workerbee_status_by_agent[agent_id] = status
        detail.text = self.format_workerbee_status(status)

    def format_workerbee_status(self, status: dict[str, Any]) -> str:
        agent_id = str(status.get("agent_id") or "-")
        cwd = str(status.get("cwd") or "-")
        workerbee_bin = str(status.get("workerbee_bin") or "-")
        lines = [
            f"Agent: {agent_id}",
            f"Cwd: {cwd}",
            f"WorkerBee: {workerbee_bin}",
        ]
        error = status.get("error") if isinstance(status.get("error"), dict) else None
        if error:
            lines.extend(
                [
                    "",
                    "Status: unavailable",
                    f"Code: {error.get('code', 'WORKERBEE_ERROR')}",
                    f"Message: {error.get('message', '')}",
                ]
            )
            remediation = error.get("remediation")
            if remediation:
                lines.append(f"Remediation: {remediation}")
            return "\n".join(lines)

        if not status.get("available"):
            lines.extend(["", "Status: unavailable"])
            return "\n".join(lines)

        project = str(status.get("project") or "-")
        mode = str(status.get("mode") or "-")
        status_kind = str(status.get("status_kind") or "-")
        running = status.get("running")
        dashboard_url = str(status.get("dashboard_url") or "-")
        state_dir = str(status.get("state_dir") or "-")
        description = str(status.get("description") or "-")
        app_status = status.get("app_status")
        latest_deployment = status.get("latest_deployment")
        project_card = status.get("project_card")
        global_dashboard = status.get("global_dashboard")
        dashboard_error = (
            status.get("dashboard_error")
            if isinstance(status.get("dashboard_error"), dict)
            else None
        )
        lines.extend(
            [
                "",
                "Project",
                f"Name: {project}",
                f"Mode: {mode}",
                f"Running: {running if running is not None else '-'}",
                f"Status: {status_kind}",
                f"Dashboard: {dashboard_url}",
                f"State Dir: {state_dir}",
                f"Description: {description}",
            ]
        )
        lines.extend(self.format_workerbee_app_status(app_status))
        lines.extend(self.format_workerbee_deployment(latest_deployment))
        lines.extend(self.format_workerbee_project_card(project_card))
        lines.extend(self.format_workerbee_global_dashboard(global_dashboard))
        lines.extend(self.format_workerbee_dashboard_error(dashboard_error))
        return "\n".join(lines)

    def format_workerbee_app_status(self, app_status: object) -> list[str]:
        if not isinstance(app_status, dict):
            return ["", "App", "No app status reported."]
        lines = [
            "",
            "App",
            f"State: {app_status.get('state') or '-'}",
            f"Ready: {app_status.get('ready')}",
            f"Message: {app_status.get('message') or '-'}",
            (
                "Workloads: "
                f"{app_status.get('declared_workload_count', 0)} declared, "
                f"{app_status.get('ready_workload_count', 0)} ready, "
                f"{app_status.get('degraded_workload_count', 0)} degraded, "
                f"{app_status.get('orphaned_workload_count', 0)} orphaned"
            ),
        ]
        urls = list_value(app_status.get("ingress_urls"))
        if urls:
            lines.append("Ingress URLs:")
            lines.extend(f"- {url}" for url in urls)
        workloads = list_value(app_status.get("declared_workloads"))
        if workloads:
            lines.append("Declared Workloads:")
            lines.extend(f"- {self.format_workerbee_workload(workload)}" for workload in workloads)
        return lines

    def format_workerbee_deployment(self, deployment: object) -> list[str]:
        lines = ["", "Latest Deployment"]
        if not isinstance(deployment, dict):
            lines.append("No deployment recorded yet.")
            return lines
        lines.extend(
            [
                f"ID: {deployment.get('id') or deployment.get('deployment_id') or '-'}",
                f"Target: {deployment.get('target') or '-'}",
                f"Namespace: {deployment.get('namespace') or '-'}",
                f"Stage: {deployment.get('stage') or deployment.get('stage_dir') or '-'}",
                f"Created: {deployment.get('created_at') or '-'}",
                f"Updated: {deployment.get('updated_at') or '-'}",
            ]
        )
        urls = list_value(deployment.get("ingress_urls"))
        if urls:
            lines.append("Ingress URLs:")
            lines.extend(f"- {url}" for url in urls)
        validation = deployment.get("validation")
        if isinstance(validation, dict):
            findings = list_value(validation.get("findings"))
            if findings:
                lines.append("Validation Findings:")
                lines.extend(
                    f"- {finding.get('level', 'info')} {finding.get('code', '')}: "
                    f"{finding.get('message', '')}"
                    for finding in findings
                    if isinstance(finding, dict)
                )
        return lines

    def format_workerbee_project_card(self, project_card: object) -> list[str]:
        if not isinstance(project_card, dict):
            return []
        lines = ["", "Dashboard Project"]
        for label, key in (
            ("Kind", "status_kind"),
            ("Stack", "stack_running"),
            ("Profile", "profile_running"),
            ("Ingress", "ingress_status"),
            ("Exposed Routes", "exposed_route_summary"),
        ):
            value = project_card.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        return lines

    def format_workerbee_global_dashboard(self, dashboard: object) -> list[str]:
        if not isinstance(dashboard, dict):
            return []
        lines = ["", "Global Dashboard"]
        for label, key in (
            ("URL", "dashboard_url"),
            ("Running", "running"),
            ("Exposure", "exposure"),
            ("Runtime", "runtime"),
            ("CA Ready", "ca_ready"),
        ):
            value = dashboard.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        return lines

    def format_workerbee_dashboard_error(
        self,
        error: dict[str, Any] | None,
    ) -> list[str]:
        if not error:
            return []
        lines = [
            "",
            "Dashboard Warning",
            f"Code: {error.get('code', 'WORKERBEE_DASHBOARD_ERROR')}",
            f"Message: {error.get('message', '')}",
        ]
        if "retryable" in error:
            lines.append(f"Retryable: {error.get('retryable')}")
        return lines

    def format_workerbee_workload(self, workload: object) -> str:
        if not isinstance(workload, dict):
            return str(workload)
        kind = workload.get("kind") or workload.get("input_kind") or "workload"
        name = workload.get("name") or "-"
        namespace = workload.get("namespace") or "-"
        return f"{kind} {namespace}/{name}"

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "visual-flash":
            self.visual_flash_enabled = event.value
            self.save_settings()
        elif event.checkbox.id == "terminal-bell":
            self.terminal_bell_enabled = event.value
            self.save_settings()
        elif event.checkbox.id == "agent-blink":
            self.agent_blink_enabled = event.value
            if not event.value:
                self.attention_blink_phase = False
                self.render_unseen_attention()
            self.render_agents()
            self.save_settings()
        elif event.checkbox.id == "low-power":
            self.low_power_enabled = event.value
            self.restart_refresh_timers()
            self.update_hotkey_labels()
            self.save_settings()
            state = "enabled" if event.value else "disabled"
            self.notify(
                f"Low-power watch mode {state}; agent refresh every "
                f"{self.agent_refresh_seconds:g}s."
            )
        elif event.checkbox.id == "tmux-direct":
            self.set_tmux_direct_enabled(event.value)
            agent_id = self.selected_agent_id
            if agent_id:
                if self.is_tmux_direct_enabled(agent_id):
                    work_factory = (
                        lambda agent_id=agent_id: self.load_tmux_capture(agent_id)
                    )
                else:
                    work_factory = (
                        lambda agent_id=agent_id: self.load_latest_report(agent_id)
                    )
                self.run_async_worker(
                    work_factory,
                    name="tmux-toggle",
                    exclusive=True,
                )
            self.render_agents()

    def set_ui_theme(self, theme_name: str) -> None:
        self.ui_theme = self.resolve_theme(theme_name)
        self.theme = self.ui_theme
        self.save_settings()
        self.apply_theme_class()

    def apply_theme_class(self) -> None:
        use_custom_theme = self.ui_theme == self.custom_theme_name
        try:
            screens = list(self.screen_stack)
        except ScreenStackError:
            return
        for screen in screens:
            screen.set_class(use_custom_theme, "custom-theme")

    def is_compact_layout(self) -> bool:
        return self.is_collapsed_layout()

    def apply_layout_class(self) -> None:
        split_layout = self.effective_layout_mode == SPLIT_TUI_LAYOUT
        compact_layout = self.effective_layout_mode == COMPACT_TUI_LAYOUT
        tiny_layout = self.effective_layout_mode == TINY_TUI_LAYOUT
        compact_home = compact_layout and self.compact_view == "home"
        compact_agent = compact_layout and self.compact_view == "agent"
        tiny_home = tiny_layout and self.compact_view == "home"
        tiny_agent = tiny_layout and self.compact_view == "agent"
        try:
            screens = list(self.screen_stack)
        except ScreenStackError:
            return
        for screen in screens:
            screen.set_class(split_layout, "split-layout")
            screen.set_class(compact_home, "compact-home")
            screen.set_class(compact_agent, "compact-agent")
            screen.set_class(tiny_home, "tiny-home")
            screen.set_class(tiny_agent, "tiny-agent")
            screen.set_class(
                tiny_home and self.tiny_home_panel == TINY_HOME_OPERATORS,
                "tiny-operators",
            )
            screen.set_class(
                tiny_home and self.tiny_home_panel == TINY_HOME_EVENTS,
                "tiny-events",
            )
        self.apply_layout_dimensions()
        self.render_agent_columns()
        self.render_operator_columns()
        self.update_tiny_button_labels()
        self.update_hotkey_labels()
        self.restore_layout_focus()

    def apply_layout_dimensions(self) -> None:
        left = self.query_one_or_none("#left", Vertical)
        right = self.query_one_or_none("#right", Vertical)
        if left is None or right is None:
            return
        if self.effective_layout_mode == SPLIT_TUI_LAYOUT:
            left.styles.display = "block"
            right.styles.display = "block"
            left.styles.width = f"{self.split_percent}%"
            right.styles.width = f"{100 - self.split_percent}%"
        elif self.compact_view == "home":
            left.styles.display = "block"
            right.styles.display = "none"
            left.styles.width = "100%"
            right.styles.width = "100%"
        else:
            left.styles.display = "none"
            right.styles.display = "block"
            left.styles.width = "100%"
            right.styles.width = "100%"
        self.apply_tiny_events_visibility()

    def apply_tiny_events_visibility(self) -> None:
        agents_title = self.query_one_or_none("#agents-title", Static)
        agents = self.query_one_or_none("#agents", DataTable)
        operators_title = self.query_one_or_none("#operators-title", Static)
        operators = self.query_one_or_none("#operators", DataTable)
        operator_actions = self.query_one_or_none("#operator-actions", Horizontal)
        events_title = self.query_one_or_none("#events-title", Static)
        events = self.query_one_or_none("#events", DataTable)
        agent_actions = self.query_one_or_none("#agent-actions", Horizontal)
        widgets = [
            agents_title,
            agents,
            operators_title,
            operators,
            operator_actions,
            events_title,
            events,
            agent_actions,
        ]
        if any(widget is None for widget in widgets):
            return
        assert agents_title is not None
        assert agents is not None
        assert operators_title is not None
        assert operators is not None
        assert operator_actions is not None
        assert events_title is not None
        assert events is not None
        assert agent_actions is not None
        tiny_home = self.is_tiny_layout() and self.compact_view == "home"
        operators_available = bool(self.operator_table_agents())
        if self.tiny_home_panel == TINY_HOME_OPERATORS and not operators_available:
            self.set_tiny_home_panel(TINY_HOME_AGENTS, apply=False)
        show_events = tiny_home and self.tiny_home_panel == TINY_HOME_EVENTS
        show_operators = (
            operators_available
            and (
                not tiny_home
                or self.tiny_home_panel == TINY_HOME_OPERATORS
            )
        )
        show_agents = not tiny_home or self.tiny_home_panel == TINY_HOME_AGENTS
        agents_title.styles.display = "block" if show_agents else "none"
        agents.styles.display = "block" if show_agents else "none"
        agent_actions.styles.display = "none" if tiny_home else "block"
        operators_title.styles.display = "block" if show_operators else "none"
        operators.styles.display = "block" if show_operators else "none"
        operator_actions.styles.display = (
            "block" if show_operators and not tiny_home else "none"
        )
        events_title.styles.display = "block" if show_events or not tiny_home else "none"
        events.styles.display = "block" if show_events or not tiny_home else "none"
        events.styles.height = "1fr" if show_events else 12
        if show_operators and tiny_home:
            operators.styles.height = "1fr"
        elif show_operators:
            left = self.query_one_or_none("#left", Vertical)
            left_height = left.region.height if left is not None else 0
            total_height = operator_panel_height(left_height)
            operators.styles.height = max(1, total_height - 4)

    def update_tiny_button_labels(self) -> None:
        tiny = self.is_tiny_layout()
        labels = {
            "send": "Send" if tiny else "Send Input",
            "request-detail": "Detail" if tiny else "Request Detail",
            "ping-agent": "Ping",
            "mark-canceled": "Cancel" if tiny else "Mark Canceled",
        }
        for button_id, label in labels.items():
            button = self.query_one_or_none(f"#{button_id}", Button)
            if button is not None:
                button.label = Text(label)
        self.update_hidden_agent_button()

    def update_hidden_agent_button(self) -> None:
        button = self.query_one_or_none("#toggle-hidden-agents", Button)
        if button is not None:
            label = "Hide Hidden (h)" if self.show_hidden_agents else "Show Hidden (h)"
            button.label = Text(label)

    def update_hotkey_labels(self) -> None:
        composer_hotkeys = self.query_one_or_none("#composer-hotkeys", Static)
        if composer_hotkeys is not None:
            composer_hotkeys.update(self.composer_hotkeys_text())
        tmux_hotkeys = self.query_one_or_none("#tmux-hotkeys", Static)
        if tmux_hotkeys is not None:
            tmux_hotkeys.update(self.tmux_hotkeys_text())

    def restore_layout_focus(self) -> None:
        if not self.is_collapsed_layout():
            return
        if self.compact_view == "home":
            agents = self.query_one_or_none("#agents", DataTable)
            if agents is not None:
                agents.focus()
        else:
            if isinstance(self.focused, (Input, TextArea)):
                return
            if self.is_tmux_direct_enabled() and self.active_agent_tab == "latest-tab":
                tmux_message = self.query_one_or_none("#tmux-message", TextArea)
                if tmux_message is not None:
                    tmux_message.focus()
                    return
            detail = self.query_one_or_none("#detail", TextArea)
            if detail is not None:
                detail.focus()

    def apply_tmux_class(self) -> None:
        try:
            screens = list(self.screen_stack)
        except ScreenStackError:
            return
        for screen in screens:
            screen.set_class(self.is_tmux_direct_enabled(), "tmux-direct")

    def show_compact_home(self) -> None:
        if not self.is_collapsed_layout():
            return
        self.compact_view = "home"
        self.set_tiny_home_panel(TINY_HOME_AGENTS, apply=False)
        self.apply_layout_class()
        if self.selected_agent_id:
            self.move_agent_cursor(self.selected_agent_id, focus=True)

    def show_compact_agent(self) -> None:
        if not self.is_collapsed_layout():
            return
        self.compact_view = "agent"
        self.apply_layout_class()

    def set_tiny_home_panel(self, panel: str, *, apply: bool = True) -> None:
        if panel not in {TINY_HOME_AGENTS, TINY_HOME_OPERATORS, TINY_HOME_EVENTS}:
            panel = TINY_HOME_AGENTS
        if panel == TINY_HOME_OPERATORS and not self.operator_table_agents():
            panel = TINY_HOME_AGENTS
        self.tiny_home_panel = panel
        self.tiny_show_events = panel == TINY_HOME_EVENTS
        if apply:
            self.apply_layout_class()

    def resolve_theme(self, theme_name: str) -> str:
        if is_custom_theme_selector(theme_name, self.custom_theme_name):
            return self.custom_theme_name
        if is_minimal_theme_selector(theme_name):
            return MINIMAL_TUI_THEME
        if is_default_theme_selector(theme_name):
            return DEFAULT_TUI_THEME
        return DEFAULT_TUI_THEME

    def save_settings(self) -> None:
        self.settings = {
            "visual_flash": self.visual_flash_enabled,
            "terminal_bell": self.terminal_bell_enabled,
            "agent_blink": self.agent_blink_enabled,
            "low_power": self.low_power_enabled,
            "theme": self.ui_theme,
            "layout": self.layout_mode,
            "split_percent": self.split_percent,
            "show_hidden_agents": self.show_hidden_agents,
            "export_dir": str(self.export_dir),
            "tmux_direct": self.tmux_direct_enabled,
            "tmux_direct_agent_modes": self.tmux_direct_agent_modes,
            "tmux_capture_lines": self.tmux_capture_lines,
            "tmux_agent_targets": self.tmux_agent_targets,
            "selected_operator_fork_target_by_operator": (
                self.selected_operator_fork_target_by_operator
            ),
            "tmux_manual_override_agent_ids": sorted(self.tmux_manual_override_agent_ids),
            "tmux_detached_agent_ids": sorted(self.tmux_detached_agent_ids),
            "starred_agent_ids": sorted(self.starred_agent_ids),
            "latest_viewed_at_by_agent": self.latest_viewed_at_by_agent,
            "last_seen_event_id": self.last_seen_event_id,
        }
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(
                json.dumps(self.settings, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    async def stream_events(self) -> None:
        while True:
            try:
                async with httpx.AsyncClient(base_url=self.server, timeout=None) as client:
                    headers = auth_headers(self.token)
                    if self.last_seen_event_id:
                        headers["Last-Event-ID"] = str(self.last_seen_event_id)
                    async with client.stream(
                        "GET",
                        "/v1/events/stream",
                        headers=headers,
                    ) as response:
                        response.raise_for_status()
                        if self.event_stream_disconnected:
                            self.event_stream_disconnected = False
                            if not self.low_power_enabled:
                                self.call_later(self.notify_stream_reconnected)
                        self.event_stream_error_count = 0
                        self.event_stream_last_error = ""
                        async for line in response.aiter_lines():
                            event = self.event_from_sse_line(line)
                            if event is None:
                                continue
                            event_id = int(event["event_id"])
                            if event_id <= self.last_seen_event_id:
                                continue
                            self.last_seen_event_id = event_id
                            self.save_settings()
                            self.events.append(event)
                            self.events = self.events[-50:]
                            self.call_later(self.handle_event, event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.event_stream_error_count += 1
                self.event_stream_last_error = f"{type(exc).__name__}: {exc}"
                if not self.event_stream_disconnected:
                    self.event_stream_disconnected = True
                    if not self.low_power_enabled:
                        self.call_later(self.notify_stream_disconnected)
                await asyncio.sleep(2)

    def event_from_sse_line(self, line: str) -> dict[str, Any] | None:
        if not line.startswith("data:"):
            return None
        raw_event = line.partition(":")[2].strip()
        if not raw_event:
            return None
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            return None
        try:
            event["event_id"] = int(event["event_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return event

    def notify_stream_disconnected(self) -> None:
        detail = (
            f" ({self.event_stream_last_error})"
            if self.event_stream_last_error
            else ""
        )
        self.notify(
            f"Event stream disconnected; polling fallback active{detail}.",
            severity="warning",
        )

    def notify_stream_reconnected(self) -> None:
        self.notify("Event stream reconnected.")

    def handle_event(self, event: dict[str, Any]) -> None:
        self.render_events()
        event_type = str(event.get("type"))
        agent_id = self.event_agent_id(event)
        campaign_operator_id = self.operator_campaign_event_operator_id(event)
        selected_agent_id = self.selected_agent_id
        selected_detail_visible = (
            agent_id == selected_agent_id and self.selected_agent_detail_visible()
        )
        if event_type == "report_created" and selected_detail_visible:
            self.activate_latest_tab()
        if event_type == "report_created" and agent_id:
            self.apply_report_created_event(agent_id, event)
        if event_type == "agent_starred_changed" and agent_id:
            self.apply_agent_starred_event(agent_id, event)
        if event_type in {
            "agent_registered",
            "report_created",
            "command_queued",
            "command_delivered",
            "command_acked",
            "command_deleted",
            "agent_pbx_active_changed",
            "agent_dismissed",
            "agent_unhidden",
            "latest_seen",
            "agent_starred_changed",
            "operator_campaign_event",
        }:
            self.run_worker(
                self.refresh_agents,
                name="agents-refresh",
                group="agents-refresh",
                exclusive=True,
            )
        if event_type == "report_created" and agent_id:
            self.mark_latest_unseen(agent_id)
        if event_type == "latest_seen" and agent_id:
            self.apply_latest_seen_event(agent_id, event)
        if event_type == "operator_campaign_event" and campaign_operator_id:
            self.handle_operator_campaign_event(campaign_operator_id)
        if selected_agent_id and selected_detail_visible:
            if event_type == "report_created":
                self.run_worker(
                    self.refresh_selected_agent(selected_agent_id),
                    name="selected-agent-report",
                    group="selected-agent-refresh",
                    exclusive=True,
                )
            elif event_type in {
                "command_queued",
                "command_delivered",
                "command_acked",
                "command_deleted",
            }:
                self.run_worker(
                    self.load_thread(selected_agent_id),
                    name="selected-agent-thread",
                    group="selected-agent-refresh",
                    exclusive=True,
                )
        if self.should_alert(event):
            self.alert_for_event(event)

    def handle_operator_campaign_event(self, operator_id: str) -> None:
        if (
            self.active_agent_tab != "campaigns-tab"
            or self.selected_agent_id != operator_id
        ):
            return
        self.run_worker(
            self.load_operator_campaigns(operator_id),
            name="campaigns-refresh",
            group="campaigns-refresh",
            exclusive=True,
        )

    def apply_agent_starred_event(self, agent_id: str, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        starred = bool(payload.get("starred"))
        self.update_agent_star_state(
            agent_id,
            starred=starred,
            starred_at=float_value(payload.get("starred_at")),
        )

    def mark_latest_unseen(self, agent_id: str) -> None:
        current_report_at = self.latest_report_timestamp(agent_id)
        latest_viewed_at = self.latest_viewed_at_by_agent.get(agent_id)
        shared_seen_at = self.shared_latest_seen_at(agent_id)
        if current_report_at is not None:
            if shared_seen_at is not None and current_report_at <= shared_seen_at:
                if latest_viewed_at is None or latest_viewed_at < shared_seen_at:
                    self.latest_viewed_at_by_agent[agent_id] = shared_seen_at
                    self.save_settings()
                if agent_id in self.unseen_latest_agent_ids:
                    self.unseen_latest_agent_ids.discard(agent_id)
                    self.render_agents()
                    self.render_unseen_attention()
                return
            if latest_viewed_at is not None and current_report_at <= latest_viewed_at:
                if agent_id in self.unseen_latest_agent_ids:
                    self.unseen_latest_agent_ids.discard(agent_id)
                    self.render_agents()
                    self.render_unseen_attention()
                return
        if self.is_latest_engaged(agent_id):
            self.mark_latest_seen(agent_id)
            return
        self.unseen_latest_agent_ids.add(agent_id)
        self.render_agents()
        self.render_unseen_attention()

    def apply_latest_seen_event(self, agent_id: str, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        seen_at = float_value(payload.get("latest_report_seen_at"))
        if seen_at is None:
            return
        current_report_at = self.latest_report_timestamp(agent_id)
        agent = self.agents.get(agent_id)
        if agent is not None:
            existing_seen_at = self.shared_latest_seen_at(agent_id) or 0.0
            agent["latest_report_seen_at"] = max(existing_seen_at, seen_at)
        self.latest_viewed_at_by_agent[agent_id] = max(
            self.latest_viewed_at_by_agent.get(agent_id, 0.0),
            seen_at,
        )
        if current_report_at is None or current_report_at <= seen_at:
            self.unseen_latest_agent_ids.discard(agent_id)
        self.render_agents()
        self.render_unseen_attention()
        self.save_settings()

    def apply_report_created_event(self, agent_id: str, event: dict[str, Any]) -> None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        agent = self.agents.get(agent_id)
        if agent is None:
            return
        status = str(payload.get("status") or "").strip()
        if not status:
            return
        event_at = float_value(payload.get("created_at"))
        if event_at is None:
            event_at = float_value(event.get("created_at"))
        agent["status"] = status
        agent["effective_status"] = status
        agent["status_stale"] = False
        agent["status_age_seconds"] = 0.0
        agent["latest_report_status"] = status
        agent["latest_report_needs_input"] = bool(payload.get("needs_input", False))
        agent["latest_report_action_required"] = bool(payload.get("needs_input", False))
        if event_at is not None:
            agent["last_seen_at"] = max(
                self.agent_last_seen(agent_id) or 0.0,
                event_at,
            )
            agent["latest_report_created_at"] = max(
                self.latest_report_timestamp(agent_id) or 0.0,
                event_at,
            )
        self.render_agents()

    def render_events(self) -> None:
        if (
            self.low_power_enabled
            and self.is_tiny_layout()
            and self.compact_view == "home"
            and not self.tiny_show_events
        ):
            return
        table = self.query_one_or_none("#events", DataTable)
        if table is None:
            return
        table.clear()
        for event in self.events[-50:]:
            table.add_row(
                str(event["event_id"]),
                event["type"],
                event.get("subject_id") or "",
                key=str(event["event_id"]),
            )

    def render_thread(self, thread: list[dict[str, Any]]) -> None:
        table = self.query_one("#thread", DataTable)
        table.clear()
        for item in thread:
            table.add_row(
                "*" if item["item_id"] in self.marked_thread_item_ids else "",
                f"{item['created_at']:.0f}",
                item["kind"],
                self.format_thread_plan_state(item),
                item["status"],
                item["title"],
                key=item["item_id"],
            )
        if self.selected_thread_item_id in self.thread_items:
            table.move_cursor(
                row=table.get_row_index(self.selected_thread_item_id),
                animate=False,
                scroll=False,
            )

    def select_thread_item(self, item_id: str) -> None:
        item = self.thread_items.get(item_id)
        if item is None:
            return
        self.selected_thread_item_id = item_id
        if self.selected_agent_id:
            self.selected_thread_item_id_by_agent[self.selected_agent_id] = item_id
        self.query_one("#thread-detail", TextArea).text = self.format_thread_item(item)
        self.render_plan_choice_panel(item)

    def format_thread_plan_state(self, item: dict[str, Any]) -> str:
        if item.get("kind") != "report":
            return ""
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        option_count = len(self.plan_options_for_item(item))
        if option_count > 0:
            return f"PLAN:{option_count}"
        if bool(metadata.get("needs_input")):
            return "INPUT"
        status = str(item.get("status") or "").strip().lower()
        if status in {"plan", "planning", "needs_input", "waiting"}:
            return status.upper()
        return ""

    def render_latest_plan_choice_panel(self, report: dict[str, Any] | None) -> None:
        panel = self.query_one_or_none("#latest-plan-choice-panel", Vertical)
        table = self.query_one_or_none("#latest-plan-options", DataTable)
        hint = self.query_one_or_none("#latest-plan-hint", Static)
        if panel is None or table is None:
            return
        options = self.plan_options_for_report(report)
        table.clear()
        self.selected_latest_plan_option_index = None
        if not options:
            panel.styles.display = "none"
            return
        panel.styles.display = "block"
        for index, option in enumerate(options):
            table.add_row(str(index + 1), option.display_label, key=str(index))
        self.selected_latest_plan_option_index = 0
        table.move_cursor(row=0, animate=False, scroll=False)
        if hint is not None:
            hint.update("Reply with /plan:1 optional notes.")

    def render_plan_choice_panel(self, item: dict[str, Any] | None) -> None:
        panel = self.query_one_or_none("#plan-choice-panel", Vertical)
        table = self.query_one_or_none("#plan-options", DataTable)
        hint = self.query_one_or_none("#plan-hint", Static)
        if panel is None or table is None:
            return
        options = self.plan_options_for_item(item)
        table.clear()
        self.selected_plan_option_index = None
        if not options:
            panel.styles.display = "none"
            return
        panel.styles.display = "block"
        for index, option in enumerate(options):
            table.add_row(str(index + 1), option.display_label, key=str(index))
        self.selected_plan_option_index = 0
        table.move_cursor(row=0, animate=False, scroll=False)
        if hint is not None:
            hint.update("Reply with /plan:1 optional notes.")

    def select_plan_option(self, row_key: str) -> None:
        parsed = int_value(row_key)
        if parsed is None:
            return
        item = (
            self.thread_items.get(self.selected_thread_item_id)
            if self.selected_thread_item_id
            else None
        )
        if parsed < 0 or parsed >= len(self.plan_options_for_item(item)):
            return
        self.selected_plan_option_index = parsed

    def select_latest_plan_option(self, row_key: str) -> None:
        parsed = int_value(row_key)
        if parsed is None:
            return
        report = (
            self.latest_report_by_agent.get(self.selected_agent_id)
            if self.selected_agent_id
            else None
        )
        if parsed < 0 or parsed >= len(self.plan_options_for_report(report)):
            return
        self.selected_latest_plan_option_index = parsed

    def current_thread_item_id(self) -> str | None:
        table = self.query_one("#thread", DataTable)
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def toggle_current_thread_mark(self) -> None:
        item_id = self.current_thread_item_id()
        if item_id is None:
            return
        if item_id in self.marked_thread_item_ids:
            self.marked_thread_item_ids.remove(item_id)
        else:
            self.marked_thread_item_ids.add(item_id)
        self.render_thread([self.thread_items[item_id] for item_id in self.thread_order])

    def clear_thread_marks(self) -> None:
        if not self.marked_thread_item_ids:
            return
        self.marked_thread_item_ids.clear()
        self.render_thread([self.thread_items[item_id] for item_id in self.thread_order])

    async def delete_queued_thread_commands(self) -> int:
        agent_id = self.selected_agent_id
        command_ids = self.queued_command_ids_for_delete()
        if not command_ids:
            self.notify("No queued commands selected for deletion.", severity="warning")
            return 0
        deleted = 0
        failures: list[str] = []
        for command_id in command_ids:
            try:
                await self.delete_queued_command(command_id)
            except Exception as exc:
                failures.append(f"{command_id}: {exc}")
            else:
                deleted += 1
        if deleted:
            self.notify(f"Deleted {deleted} queued command(s).")
        if failures:
            self.notify(
                f"Failed to delete {len(failures)} command(s).",
                severity="error",
            )
        await self.refresh_events()
        await self.refresh_agents()
        if agent_id:
            await self.load_thread(agent_id)
        return deleted

    def queued_command_ids_for_delete(self) -> list[str]:
        item_ids = self.thread_item_ids_for_scope(
            "marked" if self.marked_thread_item_ids else "item"
        )
        command_ids: list[str] = []
        for item_id in item_ids:
            item = self.thread_items.get(item_id)
            if (
                item is None
                or item.get("kind") != "command"
                or item.get("status") != "queued"
            ):
                continue
            metadata = item.get("metadata")
            command_id = metadata.get("command_id") if isinstance(metadata, dict) else None
            if command_id:
                command_ids.append(str(command_id))
        return command_ids

    def export_thread_scope(self, scope: str) -> Path | None:
        item_ids = self.thread_item_ids_for_scope(scope)
        if not item_ids:
            self.notify(f"No thread items to export for {scope}.", severity="warning")
            return None
        try:
            path = self.write_thread_export(scope, item_ids)
        except OSError as exc:
            self.notify(f"Thread export failed: {exc}", severity="error")
            return None
        self.notify(f"Exported thread {scope} to {path}")
        return path

    def thread_item_ids_for_scope(self, scope: str) -> list[str]:
        if scope == "item":
            return [self.selected_thread_item_id] if self.selected_thread_item_id else []
        if scope == "marked":
            return [
                item_id
                for item_id in self.thread_order
                if item_id in self.marked_thread_item_ids
            ]
        if scope == "all":
            return list(self.thread_order)
        raise ValueError(f"unknown export scope: {scope}")

    def write_thread_export(self, scope: str, item_ids: list[str]) -> Path:
        agent_id = self.selected_agent_id or "agent"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.export_dir / f"{slugify(agent_id)}-{timestamp}-{scope}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.format_thread_export(scope, item_ids),
            encoding="utf-8",
        )
        return path

    def format_thread_export(self, scope: str, item_ids: list[str]) -> str:
        agent_id = self.selected_agent_id or "unknown"
        exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        items = [self.thread_items[item_id] for item_id in item_ids if item_id in self.thread_items]
        lines = [
            f"# Agent PBX Thread Export: {agent_id}",
            "",
            f"- Exported: {exported_at}",
            f"- Scope: {scope}",
            f"- Items: {len(items)}",
            "",
        ]
        for index, item in enumerate(items, start=1):
            lines.extend(
                [
                    f"## {index}. {item['kind'].title()}: {item['title']}",
                    "",
                    f"- Item: {item['item_id']}",
                    f"- Status: {item['status']}",
                    f"- Created: {item['created_at']:.0f}",
                    "",
                    str(item.get("body") or ""),
                    "",
                ]
            )
            metadata = item.get("metadata")
            if metadata:
                lines.extend(
                    [
                        "```json",
                        json.dumps(metadata, indent=2, sort_keys=True),
                        "```",
                        "",
                    ]
                )
        return "\n".join(lines).rstrip() + "\n"

    def format_thread_item(self, item: dict[str, Any]) -> str:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        lines = [
            f"{item['kind'].title()}: {item['title']}",
            f"Status: {item['status']}",
            f"Created: {item['created_at']:.0f}",
            "",
            str(item.get("body") or ""),
        ]
        if item["kind"] == "command":
            lines.extend(
                [
                    "",
                    f"Command: {metadata.get('command_id', '')}",
                    f"Type: {metadata.get('type', '')}",
                    f"Acked: {metadata.get('acked_at') or '-'}",
                ]
            )
            result = metadata.get("result")
            if result is not None:
                lines.extend(["", "Result:", json.dumps(result, indent=2, sort_keys=True)])
        elif item["kind"] == "report":
            plan_options = plan_choices_from_value(metadata.get("plan_options"))
            if plan_options:
                lines.extend(
                    [
                        "",
                        "Plan Options:",
                        *[f"- {option.display_label}" for option in plan_options],
                    ]
                )
        return "\n".join(lines)

    def should_alert(self, event: dict[str, Any]) -> bool:
        return str(event.get("type")) in ATTENTION_EVENT_TYPES

    def event_agent_id(self, event: dict[str, Any]) -> str | None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        if str(event.get("type")) == "operator_campaign_event":
            agent_id = (
                payload.get("fork_agent_id")
                or payload.get("agent_id")
                or payload.get("operator_agent_id")
            )
            return str(agent_id) if agent_id else None
        agent_id = payload.get("agent_id")
        return str(agent_id) if agent_id else None

    def operator_campaign_event_operator_id(
        self,
        event: dict[str, Any],
    ) -> str | None:
        if str(event.get("type")) != "operator_campaign_event":
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        agent_id = payload.get("operator_agent_id")
        return str(agent_id) if agent_id else None

    def alert_for_event(self, event: dict[str, Any]) -> None:
        if self.terminal_bell_enabled:
            self.bell()
        if self.visual_flash_enabled:
            self.flash_for_event(event)

    def flash_for_event(self, event: dict[str, Any]) -> None:
        self.flash_generation += 1
        generation = self.flash_generation
        self.attention_agent_id = self.event_agent_id(event)
        event_type = str(event.get("type", "event")).replace("_", " ")
        subject = event.get("subject_id") or ""
        message = f"New {event_type}"
        if subject:
            message = f"{message}: {subject}"
        attention = self.query_one_or_none("#attention", Static)
        if attention is None:
            return
        attention.update(message)
        attention.add_class("attention-active")
        self.set_attention_flash_class(True)
        self.set_timer(3.0, lambda: self.clear_flash(generation))

    def clear_flash(self, generation: int) -> None:
        if generation != self.flash_generation:
            return
        attention = self.query_one_or_none("#attention", Static)
        if attention is None:
            self.set_attention_flash_class(False)
            return
        if not self.unseen_latest_agent_ids and not self.tmux_plan_selector_agent_ids:
            attention.update("")
            attention.remove_class("attention-active")
            self.attention_agent_id = None
        else:
            attention.remove_class("attention-active")
            self.render_unseen_attention()
        self.set_attention_flash_class(False)


def run_tui(*, server: str, token: str | None = None) -> None:
    AgentPBXTUI(server=server, token=token).run()
