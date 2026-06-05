from __future__ import annotations

import asyncio
from collections.abc import Iterable
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
import time
from typing import Any, TypeVar
from urllib.parse import urlparse

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
ATTENTION_EVENT_TYPES = {"agent_registered", "report_created", "command_acked"}
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
    "events",
    "detail",
    "tmux-stream",
    "tmux-message",
    "thread",
    "thread-detail",
    "files",
    "file-preview",
    "workerbee-detail",
    "joplin-notes",
    "joplin-body",
    "message",
    "agent-id",
    "plan-options",
    "latest-plan-options",
}
MOUSE_FOCUS_CONTAINER_TARGETS = {
    "left": "#agents",
    "agent-actions": "#agents",
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
BUILT_IN_PALETTE_COMMAND_NAMES = {
    "/refresh",
    "/detail",
    "/ping",
    "/cancel",
    "/esc",
    "/ctrlc",
    "/tmux",
    "/workerbee",
    "/joplin",
    "/plan",
    "/plan latest",
    "/plan thread",
    "/plan select 1",
    "/plan select 2",
    "/plan select 3",
    "/gitstatus",
    "/gitdiff",
    "/gitpush",
    "/gitstageandcommit",
    "/commands reload",
    "/hide agent",
    "/purge agent",
    "/theme cyberpunk",
    "/theme minimal",
    "/layout adaptive",
    "/layout split",
    "/layout compact",
    "/layout tiny",
}
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
        normalized = value.strip().lower()
        if normalized in TRUE_ENV_VALUES:
            return True
        if normalized in FALSE_ENV_VALUES:
            return False
    return default


def env_flag_value(*names: str) -> bool | None:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
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


def contains_codex_native_plan_selector(text: str) -> bool:
    lines = [" ".join(line.casefold().split()) for line in text.splitlines()]
    if not lines:
        return False
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

    def choice_line(index: int, terms: tuple[str, ...]) -> bool:
        pattern = re.compile(rf"^[^\w/]*{index}[.)]?\s+(?P<label>.+)$")
        for line in lines[-40:]:
            match = pattern.match(line)
            if match is None:
                continue
            label = match.group("label")
            if len(label) > 120:
                continue
            if any(term in label for term in terms):
                return True
        return False

    return all(
        (
            choice_line(1, start_terms),
            choice_line(2, ("clear", "discard")),
            choice_line(3, stay_terms),
        )
    )


def built_in_palette_command_names(custom_theme_name: str = DEFAULT_CUSTOM_THEME_NAME) -> set[str]:
    names = set(BUILT_IN_PALETTE_COMMAND_NAMES)
    names.add(f"/theme {custom_theme_name}")
    return names


def bool_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def str_setting(settings: dict[str, Any], key: str, default: str) -> str:
    value = settings.get(key)
    return value if isinstance(value, str) and value.strip() else default


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "agent"


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
    Screen.tiny-home #agent-actions {
        display: none;
    }

    Screen.tiny-home.tiny-events #agents-title,
    Screen.tiny-home.tiny-events #agents {
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

    #agent-actions {
        height: 3;
    }

    #agent-actions Button {
        width: 1fr;
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
        height: 3;
    }

    #joplin-actions Button {
        width: 1fr;
        min-width: 1;
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
    Screen.tiny-agent #joplin-body {
        min-height: 4;
    }

    Screen.tiny-agent #thread,
    Screen.tiny-agent #files,
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
        ("ctrl+t", "toggle_tmux_direct", "Tmux"),
        Binding("f8", "toggle_tmux_direct", "Tmux", key_display="F8"),
        Binding("alt+t", "toggle_tmux_direct", "Tmux", key_display="Alt+T", show=False),
        Binding("p", "toggle_star_agent", "Star Agent", priority=True),
        Binding("d", "hide_agent", "Hide Agent", priority=True),
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
        self.tiny_show_events = False
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
        self.rendered_agents_signature: tuple[Any, ...] | None = None
        self.agents: dict[str, dict[str, Any]] = {}
        self.selected_agent_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.latest_report_by_agent: dict[str, dict[str, Any]] = {}
        self.thread_items: dict[str, dict[str, Any]] = {}
        self.thread_order: list[str] = []
        self.selected_thread_item_id: str | None = None
        self.selected_plan_option_index: int | None = None
        self.selected_latest_plan_option_index: int | None = None
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
        self.joplin_configured = False
        self.joplin_available = False
        self.joplin_status: dict[str, Any] = {}
        self.joplin_notes_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
        self.selected_joplin_note_id: str | None = None
        self.file_path_by_agent: dict[str, str] = {}
        self.file_entries_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
        self.file_directory_entries_by_agent: dict[
            str,
            dict[str, dict[str, dict[str, Any]]],
        ] = {}
        self.tmux_agent_targets = str_map_setting(self.settings, "tmux_agent_targets")
        self.tmux_manual_override_agent_ids: set[str] = set()
        self.tmux_detached_agent_ids: set[str] = set()
        self.tmux_last_capture_by_pane: dict[str, str] = {}
        self.tmux_last_status_by_agent: dict[str, str] = {}
        self.tmux_visible_capture_key: str | None = None
        self.tmux_liveness_by_agent: dict[str, TmuxLiveness] = {}
        self.tmux_panes: list[tmux_support.TmuxPane] = []
        self.tmux_refreshing = False
        self.tmux_plan_selector_pane_by_agent: dict[str, str] = {}
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
                    yield Button("Star/Unstar (p)", id="star-agent")
                    yield Button("Hide Agent (d)", id="hide-agent")
                    yield Button("Purge Agent (D)", id="purge-agent")
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
                    with TabPane("Joplin", id="joplin-tab"):
                        yield Static("Joplin: checking...", id="joplin-status")
                        yield DataTable(
                            id="joplin-notes",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield NavigationTextArea(id="joplin-body")
                        with Horizontal(id="joplin-actions"):
                            yield Button("Refresh", id="joplin-refresh")
                            yield Button("Copy Latest", id="joplin-copy-latest")
                            yield Button("Start LOG", id="joplin-log-start")
                            yield Button("Stop LOG", id="joplin-log-stop")
                            yield Button("Save", id="joplin-save", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self.api_client()
        self.apply_theme_class()
        self.update_effective_layout()
        self.apply_layout_class()
        self.apply_tmux_class()
        agents = self.query_one("#agents", DataTable)
        self.render_agent_columns(agents)
        events = self.query_one("#events", DataTable)
        events.add_columns("ID", "Type", "Subject")
        thread = self.query_one("#thread", DataTable)
        thread.add_columns("M", "Time", "Kind", "Plan", "Status", "Summary")
        files = self.query_one("#files", DataTable)
        files.add_columns("Type", "Name", "Size", "Modified")
        joplin_notes = self.query_one("#joplin-notes", DataTable)
        joplin_notes.add_columns("Updated", "Title")
        latest_plan_options = self.query_one("#latest-plan-options", DataTable)
        latest_plan_options.add_columns("#", "Option")
        plan_options = self.query_one("#plan-options", DataTable)
        plan_options.add_columns("#", "Option")
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
        yield SystemCommand("/cancel", "Mark the selected agent canceled", self.palette_mark_canceled)
        yield SystemCommand("/esc", "Send Escape to the selected agent", self.palette_escape)
        yield SystemCommand("/ctrlc", "Send Ctrl+C to the selected tmux pane", self.palette_ctrl_c)
        yield SystemCommand("/tmux", "Toggle tmux direct mode", self.palette_toggle_tmux)
        yield SystemCommand("/workerbee", "Open and refresh the WorkerBee tab", self.palette_workerbee)
        if self.joplin_configured:
            yield SystemCommand("/joplin", "Open and refresh the Joplin tab", self.palette_joplin)
        yield SystemCommand("/plan", "Toggle plan mode for the selected agent", self.palette_toggle_plan_mode)
        yield SystemCommand("/plan latest", "Show latest report plan options", self.palette_plan_latest)
        yield SystemCommand("/plan thread", "Show selected thread plan options", self.palette_plan_thread)
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

    def palette_workerbee(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_workerbee_for_agent(agent_id),
            name="palette-workerbee",
            exclusive=True,
        )

    def palette_joplin(self) -> None:
        agent_id = self.palette_agent_id()
        if agent_id is None:
            return
        self.run_worker(
            self.open_joplin_for_agent(agent_id),
            name="palette-joplin",
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
        for index, label in CODEX_NATIVE_PLAN_SELECTOR_CHOICES.items():
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
        cursor_agent_id = self.agent_id_at_cursor()
        if cursor_agent_id:
            return cursor_agent_id
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

    async def open_workerbee_for_agent(self, agent_id: str) -> None:
        tabs = self.query_one_or_none("#agent-tabs", TabbedContent)
        if tabs is not None:
            tabs.active = "workerbee-tab"
        self.active_agent_tab = "workerbee-tab"
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            await self.load_workerbee_status(agent_id)

    async def open_joplin_for_agent(self, agent_id: str) -> None:
        await self.refresh_joplin_status()
        if not self.joplin_configured:
            self.notify("Joplin is not configured on this Agent PBX server.", severity="warning")
            return
        tabs = self.query_one_or_none("#agent-tabs", TabbedContent)
        if tabs is not None:
            tabs.active = "joplin-tab"
        self.active_agent_tab = "joplin-tab"
        if agent_id in self.agents:
            await self.select_agent(agent_id)
        else:
            await self.load_joplin_notes(agent_id)

    async def action_refresh(self) -> None:
        await self.refresh_agents()
        await self.refresh_events()
        await self.refresh_joplin_status()
        if self.selected_agent_id:
            await self.refresh_selected_agent(self.selected_agent_id)
            if self.active_agent_tab == "workerbee-tab":
                await self.load_workerbee_status(self.selected_agent_id)
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
            self.tiny_show_events = False
            self.apply_layout_class()
        if self.selected_agent_id in self.agents:
            self.move_agent_cursor(self.selected_agent_id, focus=True)
            return
        agents = self.query_one_or_none("#agents", DataTable)
        if agents is not None:
            agents.focus()

    def action_focus_events(self) -> None:
        if self.is_collapsed_layout():
            self.compact_view = "home"
            if self.is_tiny_layout():
                self.tiny_show_events = True
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

    def action_hide_agent(self) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        self.run_worker(
            self.dismiss_selected_agent(delete_thread=False),
            name="dismiss-agent",
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
            self.tiny_show_events = False
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
                *live,
            )
        if self.effective_layout_mode == COMPACT_TUI_LAYOUT:
            return (
                STARRED_AGENT_COLUMN,
                "New",
                "Agent",
                "Plan",
                "Status",
                "Queue",
                *live,
                "Poll",
            )
        return (
            STARRED_AGENT_COLUMN,
            "New",
            "Agent",
            "PBX",
            "Plan",
            "Status",
            "Project",
            "Last Seen",
            "Queue",
            *live,
            "Poll",
            "Use",
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

    def agent_row_values(self, agent: dict[str, Any]) -> list[str]:
        agent_id = str(agent["agent_id"])
        values = {
            STARRED_AGENT_COLUMN: "*" if agent_id in self.starred_agent_ids else "",
            "New": "NEW" if agent_id in self.unseen_latest_agent_ids else "",
            "Agent": agent_id,
            "PBX": self.format_pbx_active(agent),
            "Plan": self.format_plan_state(agent),
            "Status": self.format_agent_status(agent),
            "Project": str(agent["project"]),
            "Last Seen": f"{agent['last_seen_at']:.0f}",
            "Queue": self.format_queue_state(agent),
            "Live": self.format_tmux_liveness(agent_id),
            "Poll": self.format_poll_state(agent),
            "Use": self.format_usage_state(agent),
        }
        return [values[column] for column in self.rendered_agent_columns]

    def on_resize(self, event: Resize) -> None:
        if self.update_effective_layout(event.size.width, event.size.height):
            self.apply_layout_class()
            self.render_agents()
        else:
            self.apply_layout_dimensions()

    def set_layout_mode(self, layout_name: str) -> None:
        self.layout_mode = resolve_layout(layout_name)
        self.compact_view = "home"
        self.tiny_show_events = False
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
        self.tiny_show_events = not self.tiny_show_events
        self.apply_layout_class()

    async def refresh_agents(self) -> None:
        try:
            response = await self.api_client().get(
                "/v1/agents", headers=auth_headers(self.token)
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
        unseen_state_changed = self.update_unseen_from_agent_refresh(previous_last_seen)
        star_state_changed = self.sync_starred_from_agent_refresh()
        signature = self.agents_render_signature()
        if (
            unseen_state_changed
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

    def ordered_agents(self) -> list[dict[str, Any]]:
        return sorted(
            self.agents.values(),
            key=lambda agent: (
                str(agent.get("agent_id") or "") not in self.starred_agent_ids,
                -(float_value(agent.get("last_seen_at")) or 0.0),
                str(agent.get("agent_id") or ""),
            ),
        )

    def agents_render_signature(self) -> tuple[Any, ...]:
        return (
            self.desired_agent_columns(),
            tuple(sorted(self.starred_agent_ids)),
            tuple(
                (
                    str(agent["agent_id"]),
                    tuple(str(value) for value in self.agent_row_values(agent)),
                )
                for agent in self.ordered_agents()
            ),
        )

    def render_agents(self, signature: tuple[Any, ...] | None = None) -> None:
        table = self.query_one_or_none("#agents", DataTable)
        if table is None:
            return
        scroll_x = table.scroll_x
        scroll_target_x = table.scroll_target_x
        scroll_y = table.scroll_y
        scroll_target_y = table.scroll_target_y
        cursor_agent_id = self.agent_id_at_cursor()
        self.render_agent_columns(table)
        table.clear()
        for agent in self.ordered_agents():
            agent_id = str(agent["agent_id"])
            row = self.agent_row_values(agent)
            cells = self.style_agent_row(
                row,
                agent,
            )
            table.add_row(*cells, key=agent_id)
        restore_agent_id = cursor_agent_id if cursor_agent_id in self.agents else None
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
        self.rendered_agents_signature = signature or self.agents_render_signature()

    def agent_id_at_cursor(self) -> str | None:
        table = self.query_one_or_none("#agents", DataTable)
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
        table = self.query_one_or_none("#agents", DataTable)
        if table is None:
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

    def format_agent_status(self, agent: dict[str, Any]) -> str:
        status = str(agent.get("effective_status") or agent.get("status") or "")
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
        if bool(agent.get("status_stale")):
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
        self.activate_latest_tab()
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
        if event.data_table.id == "agents":
            await self.select_agent(str(event.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.row_key.value))
            return
        if event.data_table.id == "files":
            await self.select_file_entry(str(event.row_key.value))
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
        if event.data_table.id == "agents":
            await self.select_agent(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "files":
            await self.select_file_entry(str(event.cell_key.row_key.value))
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

    def handle_focus_shortcut_key(self, event: Key) -> bool:
        key = str(event.character or event.key or "").lower()
        if key == "a":
            event.stop()
            event.prevent_default()
            self.action_focus_agents()
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
        if event.text_area.id == "message":
            self.resize_message_input()

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
                text_area.text = ""
                self.sent_message_history_cursor.pop(key, None)
                if text_area.id == "message":
                    self.resize_message_input()
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
            matches = self.file_completion_matches(context, agent_id)
        if not matches:
            self.file_completion_state.pop(context.input_id, None)
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
            text_area.text = ""
            if text_area.id == "message":
                self.resize_message_input()
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
        original_text = text_area.text
        result = command.callback()
        if inspect.isawaitable(result):
            await result
        if agent_id:
            self.record_sent_message(agent_id, message)
        if text_area.text == original_text:
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
        if self.active_agent_tab == "latest-tab" and self.selected_agent_id:
            self.mark_latest_seen(self.selected_agent_id)
            self.apply_tmux_class()
            if self.is_tmux_direct_enabled(self.selected_agent_id):
                self.run_worker(
                    self.load_tmux_capture(self.selected_agent_id),
                    name="tmux-capture",
                    exclusive=True,
                )
        if self.active_agent_tab == "workerbee-tab" and self.selected_agent_id:
            self.run_worker(
                self.load_workerbee_status(self.selected_agent_id),
                name="workerbee-status",
                exclusive=True,
            )
        if self.active_agent_tab == "files-tab" and self.selected_agent_id:
            self.run_worker(
                self.load_agent_files(self.selected_agent_id),
                name="agent-files",
                exclusive=True,
            )
        if self.active_agent_tab == "joplin-tab" and self.selected_agent_id:
            self.run_worker(
                self.load_joplin_notes(self.selected_agent_id),
                name="agent-joplin",
                exclusive=True,
            )

    async def select_agent(self, agent_id: str) -> None:
        if agent_id != self.selected_agent_id:
            self.selected_thread_item_id = None
        was_compact_home = self.is_compact_layout() and self.compact_view == "home"
        self.selected_agent_id = agent_id
        self.query_one("#agent-id", Input).value = self.selected_agent_id
        self.update_agent_title()
        self.apply_tmux_class()
        if self.is_compact_layout():
            self.show_compact_agent()
        if was_compact_home or self.active_agent_tab in {"latest-tab", "thread-tab"}:
            self.activate_latest_tab()
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
        tabs = self.query_one("#agent-tabs", TabbedContent)
        tabs.active = "latest-tab"
        self.active_agent_tab = "latest-tab"

    async def refresh_selected_agent(self, agent_id: str) -> None:
        if self.active_agent_tab == "files-tab":
            await self.load_agent_files(agent_id)
        elif self.active_agent_tab == "workerbee-tab":
            await self.load_workerbee_status(agent_id)
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
            self.run_worker(
                self.mark_latest_seen_remote(agent_id),
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
            for pane in tmux_support.ranked_panes_for_agent(panes, agent)
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
        is_pending = contains_codex_native_plan_selector(captured)
        if is_pending:
            self.tmux_plan_selector_agent_ids.add(agent_id)
            if pane_id:
                self.tmux_plan_selector_pane_by_agent[agent_id] = pane_id
        else:
            self.tmux_plan_selector_agent_ids.discard(agent_id)
            self.tmux_plan_selector_pane_by_agent.pop(agent_id, None)
        if was_pending == is_pending:
            return False
        self.update_agent_title()
        self.render_unseen_attention()
        return True

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
            return True
        if agent_id != self.selected_agent_id:
            return False
        if not str(self.tmux_visible_capture_key or "").startswith(f"{agent_id}:"):
            return False
        stream = self.query_one_or_none("#tmux-stream", TextArea)
        if stream is None or not contains_codex_native_plan_selector(stream.text):
            return False
        self.tmux_plan_selector_agent_ids.add(agent_id)
        pane_id = self.tmux_visible_pane_id_for_agent(agent_id)
        if pane_id:
            self.tmux_plan_selector_pane_by_agent[agent_id] = pane_id
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
        return self.tmux_pane_id_from_capture_key(self.tmux_visible_capture_key)

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
                    if (
                        agent_id not in self.tmux_manual_override_agent_ids
                        and not tmux_support.pane_matches_agent(pane, agent)
                    ):
                        return None, "stale"
                    return pane, "manual"
            return None, "stale"
        pane = tmux_support.choose_pane_for_agent(panes, agent)
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
        ranked = tmux_support.ranked_panes_for_agent(panes, agent)
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
        if event.button.id == "joplin-refresh":
            if self.selected_agent_id:
                await self.load_joplin_notes(self.selected_agent_id)
            return
        if event.button.id == "joplin-copy-latest":
            if self.selected_agent_id:
                await self.copy_latest_to_joplin(self.selected_agent_id)
            return
        if event.button.id == "joplin-log-start":
            if self.selected_agent_id:
                await self.start_joplin_log(self.selected_agent_id)
            return
        if event.button.id == "joplin-log-stop":
            if self.selected_agent_id:
                await self.stop_joplin_log(self.selected_agent_id)
            return
        if event.button.id == "joplin-save":
            if self.selected_agent_id:
                await self.save_joplin_note(self.selected_agent_id)
            return
        if event.button.id == "star-agent":
            self.toggle_selected_agent_star()
            return
        if event.button.id == "hide-agent":
            await self.dismiss_selected_agent(delete_thread=False)
            return
        if event.button.id == "purge-agent":
            await self.dismiss_selected_agent(delete_thread=True)
            return

    async def send_input(self) -> None:
        if self.is_tmux_direct_enabled():
            await self.send_tmux_input()
            return
        message_input = self.query_one("#message", TextArea)
        agent_id = self.sent_history_agent_id(message_input) or ""
        message = message_input.text.strip()
        if not message:
            return
        if await self.execute_local_slash_command_from_input(message_input, message):
            return
        if not agent_id:
            return
        if is_plan_toggle_message(message):
            toggled = await self.toggle_plan_mode_from_input(agent_id, via_tmux=False)
            if toggled:
                self.record_sent_message(agent_id, message)
                message_input.text = ""
                self.resize_message_input()
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
                message_input.text = ""
                self.resize_message_input()
            return
        if self.should_send_plan_prompt(agent_id, message):
            sent_plan = await self.send_plan_prompt(
                agent_id,
                message,
                via_tmux=False,
            )
            if sent_plan:
                self.record_sent_message(agent_id, message)
                self.clear_pending_slash_command(agent_id)
                message_input.text = ""
                self.resize_message_input()
            return
        pending_slash_commands = self.pending_slash_command_sequence_for_message(
            agent_id, message
        )
        for pending_slash in pending_slash_commands:
            await self.queue_command(
                agent_id,
                "send_input",
                {"message": pending_slash},
            )
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": message},
        )
        self.clear_pending_slash_command(agent_id)
        self.record_sent_message(agent_id, message)
        message_input.text = ""
        self.resize_message_input()
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
        agent_id = self.sent_history_agent_id(message_input) or ""
        message = message_input.text
        if not message.strip():
            return
        if await self.execute_local_slash_command_from_input(
            message_input, message.strip()
        ):
            return
        if not agent_id:
            return
        if is_plan_toggle_message(message):
            toggled = await self.toggle_plan_mode_from_input(agent_id, via_tmux=True)
            if toggled:
                self.record_sent_message(agent_id, message)
                message_input.text = ""
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
                message_input.text = ""
            return
        if self.should_send_plan_prompt(agent_id, message):
            sent_plan = await self.send_plan_prompt(
                agent_id,
                message,
                via_tmux=True,
            )
            if sent_plan:
                self.record_sent_message(agent_id, message)
                self.clear_pending_slash_command(agent_id)
                message_input.text = ""
                await self.load_tmux_capture(agent_id)
            return
        pending_slash_commands = self.pending_slash_command_sequence_for_message(
            agent_id, message
        )
        for pending_slash in pending_slash_commands:
            sent_slash = await self.send_text_to_tmux(agent_id, pending_slash)
            if not sent_slash:
                return
            await asyncio.sleep(SLASH_COMMAND_FOLLOWUP_DELAY_SECONDS)
        sent = await self.send_text_to_tmux(agent_id, message)
        if not sent:
            return
        self.clear_pending_slash_command(agent_id)
        self.record_sent_message(agent_id, message)
        message_input.text = ""
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

    def current_plan_options_for_agent(self, agent_id: str) -> list[PlanChoice]:
        if agent_id != self.selected_agent_id:
            return []
        latest = self.plan_options_for_report(self.latest_report_by_agent.get(agent_id))
        item = (
            self.thread_items.get(self.selected_thread_item_id)
            if self.selected_thread_item_id
            else None
        )
        thread = self.plan_options_for_item(item)
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
    ) -> bool:
        label = CODEX_NATIVE_PLAN_SELECTOR_CHOICES.get(selection.index)
        if label is None:
            self.notify(
                f"Codex plan selector option {selection.index} is not available.",
                severity="warning",
            )
            return False
        status = self.query_one_or_none("#tmux-status", Static)
        pane_id = await self.resolve_native_plan_selection_pane_id(
            agent_id,
            status=status,
            notify_missing=notify_missing,
        )
        if pane_id is None:
            return False
        sent = await self.send_key_to_tmux_pane(
            pane_id,
            str(selection.index),
            status=status,
        )
        if not sent:
            return False
        self.tmux_plan_selector_agent_ids.discard(agent_id)
        self.tmux_plan_selector_pane_by_agent.pop(agent_id, None)
        visible_agent_id = self.tmux_agent_id_from_capture_key(self.tmux_visible_capture_key)
        if visible_agent_id and visible_agent_id != agent_id:
            self.tmux_plan_selector_agent_ids.discard(visible_agent_id)
            self.tmux_plan_selector_pane_by_agent.pop(visible_agent_id, None)
        self.update_agent_title()
        self.render_agents()
        self.render_unseen_attention()
        if selection.notes:
            self.notify(
                "Notes are ignored for Codex native plan selector choices.",
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
        if via_tmux and selection.index in CODEX_NATIVE_PLAN_SELECTOR_CHOICES:
            sent_native = await self.send_native_plan_selection(
                agent_id,
                selection,
                notify_missing=not options,
            )
            if sent_native:
                return True
            if not options:
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

    def selected_or_cursor_agent_id(self) -> str | None:
        cursor_agent_id = self.agent_id_at_cursor()
        if cursor_agent_id:
            return cursor_agent_id
        if self.selected_agent_id:
            return self.selected_agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            value = agent_input.value.strip()
            if value:
                return value
        return None

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

    async def dismiss_selected_agent(self, *, delete_thread: bool) -> None:
        agent_id = self.selected_or_cursor_agent_id()
        if not agent_id:
            self.notify("Select an agent before hiding it.", severity="warning")
            return
        try:
            await self.delete_agent(agent_id, delete_thread=delete_thread)
        except Exception as exc:
            self.notify(f"Unable to hide {agent_id}: {exc}", severity="error")
            return

        self.unseen_latest_agent_ids.discard(agent_id)
        self.latest_report_by_agent.pop(agent_id, None)
        self.workerbee_status_by_agent.pop(agent_id, None)
        self.joplin_notes_by_agent.pop(agent_id, None)
        self.file_directory_entries_by_agent.pop(agent_id, None)
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

    async def load_agent_files(self, agent_id: str, path: str | None = None) -> None:
        current_path = path if path is not None else self.file_path_by_agent.get(agent_id, ".")
        path_label = self.query_one("#file-path", Static)
        path_label.update(f"Path: {current_path or '.'}")
        self.set_file_preview_text(f"Loading files for {agent_id}...")
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/files",
                params={"path": current_path or "."},
                headers=auth_headers(self.token),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
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

    def render_file_list(self, payload: dict[str, Any]) -> None:
        agent_id = str(payload.get("agent_id") or self.selected_agent_id or "")
        path = str(payload.get("path") or ".")
        self.file_path_by_agent[agent_id] = path
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        parent = payload.get("parent")
        entry_map: dict[str, dict[str, Any]] = {}
        table = self.query_one("#files", DataTable)
        table.clear()
        self.query_one("#file-path", Static).update(f"Path: {path}")
        if parent:
            parent_entry = {
                "name": "..",
                "path": str(parent),
                "kind": "directory",
                "parent": parent,
            }
            entry_map[".."] = parent_entry
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
        self.file_entries_by_agent[agent_id] = entry_map
        self.file_directory_entries_by_agent.setdefault(agent_id, {})[path] = entry_map
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
            return f"Joplin: {status.get('notebook') or 'Agent PBX'}"
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = error.get("code") or "JOPLIN_UNAVAILABLE"
        return f"Joplin: {code}"

    async def load_joplin_notes(self, agent_id: str) -> None:
        await self.refresh_joplin_status()
        table = self.query_one_or_none("#joplin-notes", DataTable)
        body = self.query_one_or_none("#joplin-body", TextArea)
        if table is None or body is None:
            return
        if not self.joplin_configured:
            table.clear()
            body.text = self.format_joplin_unavailable(self.joplin_status)
            return
        if not self.joplin_available:
            table.clear()
            body.text = self.format_joplin_unavailable(self.joplin_status)
            return
        body.text = f"Loading Joplin notes for {agent_id}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/joplin/notes",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            notes = response.json()
        except Exception as exc:
            table.clear()
            body.text = f"Unable to load Joplin notes for {agent_id}: {exc}"
            return
        self.render_joplin_notes(agent_id, notes)
        if notes:
            note_id = (
                self.selected_joplin_note_id
                if self.selected_joplin_note_id in self.joplin_notes_by_agent.get(agent_id, {})
                else str(notes[0]["id"])
            )
            await self.select_joplin_note(note_id)
        else:
            self.selected_joplin_note_id = None
            body.text = "No Agent PBX Joplin notes for this agent yet."

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
        self.joplin_notes_by_agent[agent_id] = note_map

    async def select_joplin_note(self, note_id: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        body = self.query_one("#joplin-body", TextArea)
        body.text = f"Loading Joplin note {note_id}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{agent_id}/joplin/notes/{note_id}",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            body.text = f"Unable to load Joplin note {note_id}: {exc}"
            return
        self.selected_joplin_note_id = note_id
        self.joplin_notes_by_agent.setdefault(agent_id, {})[note_id] = note
        body.text = str(note.get("body") or "")
        table = self.query_one_or_none("#joplin-notes", DataTable)
        if table is not None and note_id in self.joplin_notes_by_agent.get(agent_id, {}):
            try:
                table.move_cursor(
                    row=table.get_row_index(note_id),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    async def save_joplin_note(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = self.selected_joplin_note_id
        if not note_id:
            self.notify("Select a Joplin note before saving.", severity="warning")
            return
        body = self.query_one("#joplin-body", TextArea)
        try:
            response = await self.api_client().put(
                f"/v1/agents/{agent_id}/joplin/notes/{note_id}",
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
        self.selected_joplin_note_id = str(note.get("id") or "")
        self.notify("Copied latest report to Joplin.")
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
            enabled = self.set_tmux_direct_enabled(event.value)
            if self.selected_agent_id:
                worker = (
                    self.load_tmux_capture(self.selected_agent_id)
                    if self.is_tmux_direct_enabled(self.selected_agent_id)
                    else self.load_latest_report(self.selected_agent_id)
                )
                self.run_worker(
                    worker,
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
            screen.set_class(tiny_home and self.tiny_show_events, "tiny-events")
        self.apply_layout_dimensions()
        self.render_agent_columns()
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
        events_title = self.query_one_or_none("#events-title", Static)
        events = self.query_one_or_none("#events", DataTable)
        agent_actions = self.query_one_or_none("#agent-actions", Horizontal)
        widgets = [agents_title, agents, events_title, events, agent_actions]
        if any(widget is None for widget in widgets):
            return
        assert agents_title is not None
        assert agents is not None
        assert events_title is not None
        assert events is not None
        assert agent_actions is not None
        tiny_home = self.is_tiny_layout() and self.compact_view == "home"
        show_events = tiny_home and self.tiny_show_events
        show_agents = not tiny_home or not show_events
        agents_title.styles.display = "block" if show_agents else "none"
        agents.styles.display = "block" if show_agents else "none"
        agent_actions.styles.display = "none" if tiny_home else "block"
        events_title.styles.display = "block" if show_events or not tiny_home else "none"
        events.styles.display = "block" if show_events or not tiny_home else "none"
        events.styles.height = "1fr" if show_events else 12

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
        self.tiny_show_events = False
        self.apply_layout_class()
        if self.selected_agent_id:
            self.move_agent_cursor(self.selected_agent_id, focus=True)

    def show_compact_agent(self) -> None:
        if not self.is_collapsed_layout():
            return
        self.compact_view = "agent"
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
            "export_dir": str(self.export_dir),
            "tmux_direct": self.tmux_direct_enabled,
            "tmux_direct_agent_modes": self.tmux_direct_agent_modes,
            "tmux_capture_lines": self.tmux_capture_lines,
            "tmux_agent_targets": self.tmux_agent_targets,
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
            "latest_seen",
            "agent_starred_changed",
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
        agent_id = payload.get("agent_id")
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
