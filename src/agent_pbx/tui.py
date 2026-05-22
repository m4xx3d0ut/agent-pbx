from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from rich.color import Color, ColorParseError
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.events import Click, Key, Resize
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
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
DEFAULT_TUI_THEME = "cyberpunk"
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
DEFAULT_TMUX_CAPTURE_LINES = 0
DEFAULT_TMUX_REFRESH_SECONDS = 1.5
MIN_TMUX_REFRESH_SECONDS = 0.25
FOLLOW_UP_MIN_HEIGHT = 8
FOLLOW_UP_MAX_HEIGHT = 15
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
STALE_POLL_SECONDS = 120
QUEUED_COMMAND_WARN_SECONDS = 60
ACTIVE_POLL_SECONDS = 60
TMUX_LIVENESS_IDLE_SECONDS = 60
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
WidgetType = TypeVar("WidgetType")


@dataclass
class TmuxLiveness:
    pane_id: str | None = None
    capture_hash: str | None = None
    last_capture_at: float | None = None
    last_changed_at: float | None = None
    state: str = "unknown"


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
    if theme in {
        "",
        "default",
        DEFAULT_TUI_THEME,
        "github",
        "github dark",
        "github-dark",
        "github_dark",
    }:
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


def bool_setting(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key)
    return value if isinstance(value, bool) else default


def str_setting(settings: dict[str, Any], key: str, default: str) -> str:
    value = settings.get(key)
    return value if isinstance(value, str) and value.strip() else default


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "agent"


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


def list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


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


class SettingsScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        visual_flash_enabled: bool,
        terminal_bell_enabled: bool,
        agent_blink_enabled: bool,
        layout_mode: str,
        split_percent: int,
        tmux_direct_enabled: bool,
        tmux_features_available: bool,
        custom_theme_name: str,
        custom_theme_enabled: bool,
    ) -> None:
        super().__init__()
        self.visual_flash_enabled = visual_flash_enabled
        self.terminal_bell_enabled = terminal_bell_enabled
        self.agent_blink_enabled = agent_blink_enabled
        self.layout_mode = layout_mode
        self.split_percent = split_percent
        self.tmux_direct_enabled = tmux_direct_enabled
        self.tmux_features_available = tmux_features_available
        self.custom_theme_name = custom_theme_name
        self.custom_theme_enabled = custom_theme_enabled

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
                    "Tmux direct",
                    value=self.tmux_direct_enabled,
                    id="tmux-direct",
                )
                tmux_direct.disabled = not self.tmux_features_available
                yield tmux_direct
                yield Checkbox(
                    f"{self.custom_theme_name} theme",
                    value=self.custom_theme_enabled,
                    id="theme-1337",
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
    Screen.tiny-home #events {
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

    #layout-mode-label {
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
        height: 13;
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

    #latest-plan-notes,
    #plan-notes {
        height: 4;
        min-height: 3;
        max-height: 8;
        border: tall $accent;
        background: $surface;
        scrollbar-size: 0 1;
    }

    #latest-plan-actions,
    #plan-actions {
        height: 3;
    }

    #latest-plan-actions Button,
    #plan-actions Button {
        width: 1fr;
    }

    #thread-actions {
        height: 3;
    }

    #thread-actions Button {
        width: 1fr;
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
    Screen.tiny-agent #workerbee-detail {
        min-height: 4;
    }

    Screen.tiny-agent #thread {
        height: 5;
        min-height: 4;
    }

    Screen.tiny-agent #thread-detail {
        min-height: 5;
    }

    Screen.tiny-agent #composer {
        min-height: 11;
        max-height: 14;
        padding: 0;
    }

    Screen.tiny-agent #composer-inputs {
        height: 8;
        min-height: 8;
    }

    Screen.tiny-agent #agent-id,
    Screen.tiny-agent #composer-button-spacer,
    Screen.tiny-agent #composer-button-inset {
        display: none;
    }

    Screen.tiny-agent #message {
        width: 100%;
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
        ("ctrl+t", "toggle_tmux_direct", "Tmux"),
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
        self.register_theme(self.custom_theme)
        self.server = server.rstrip("/")
        self.token = token
        self.settings_file = (
            Path(settings_file).expanduser()
            if settings_file is not None
            else env_settings_file()
        )
        self.settings = load_tui_settings(self.settings_file)
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
        tmux_direct_setting = env_flag_value("AGENT_PBX_TUI_TMUX")
        self.tmux_direct_enabled = (
            bool_setting(self.settings, "tmux_direct", False)
            if tmux_direct is None
            else tmux_direct
        )
        if tmux_direct is None and tmux_direct_setting is not None:
            self.tmux_direct_enabled = tmux_direct_setting
        self.tmux_features_available = tmux_features_available(
            tmux_direct_enabled=self.tmux_direct_enabled
        )
        self.tmux_local_direct_context = (
            self.tmux_features_available and is_local_server_url(self.server)
        )
        if self.tmux_direct_enabled and not self.tmux_features_available:
            self.tmux_direct_enabled = False
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
        self.agent_last_seen_at: dict[str, float] = {}
        self.latest_viewed_at_by_agent = float_map_setting(
            self.settings,
            "latest_viewed_at_by_agent",
        )
        self.workerbee_status_by_agent: dict[str, dict[str, Any]] = {}
        self.tmux_agent_targets = str_map_setting(self.settings, "tmux_agent_targets")
        self.tmux_manual_override_agent_ids: set[str] = set()
        self.tmux_detached_agent_ids: set[str] = set()
        self.tmux_last_capture_by_pane: dict[str, str] = {}
        self.tmux_last_status_by_agent: dict[str, str] = {}
        self.tmux_visible_capture_key: str | None = None
        self.tmux_liveness_by_agent: dict[str, TmuxLiveness] = {}
        self.tmux_panes: list[tmux_support.TmuxPane] = []
        self.tmux_refreshing = False
        self.attention_blink_phase = False
        self.attention_agent_id: str | None = None
        self.event_stream_disconnected = False
        self.last_seen_event_id = int_setting(self.settings, "last_seen_event_id", 0)
        self.flash_generation = 0

    def composer_hotkeys_text(self) -> str:
        text = "Enter send | Ctrl+J newline | Ctrl+W word"
        if self.tmux_features_available:
            text += " | Ctrl+T tmux"
        return text

    def tmux_hotkeys_text(self) -> str:
        if self.is_tiny_layout():
            return "Enter send | C-J nl | C-W word | C-T PBX"
        return "Enter send | Ctrl+J newline | Ctrl+W word | Ctrl+T PBX"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="attention")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("Agents", id="agents-title")
                yield DataTable(
                    id="agents",
                    cursor_type="row",
                    show_row_labels=False,
                )
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
                        yield TextArea(id="detail", read_only=True)
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
                            yield TextArea(
                                id="latest-plan-notes",
                                soft_wrap=True,
                            )
                            with Horizontal(id="latest-plan-actions"):
                                yield Button(
                                    "Send Plan Choice",
                                    id="latest-send-plan-choice",
                                    variant="primary",
                                )
                                yield Button(
                                    "Send to Codex Pane",
                                    id="latest-send-plan-tmux",
                                )
                        with Vertical(id="tmux-panel"):
                            yield Static("Tmux: -", id="tmux-status")
                            yield TextArea(id="tmux-stream", read_only=True)
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
                        yield TextArea(id="thread-detail", read_only=True)
                        with Vertical(id="plan-choice-panel"):
                            yield Static("Plan Options", id="plan-choice-title")
                            yield DataTable(
                                id="plan-options",
                                cursor_type="row",
                                show_row_labels=False,
                            )
                            yield TextArea(
                                id="plan-notes",
                                soft_wrap=True,
                            )
                            with Horizontal(id="plan-actions"):
                                yield Button(
                                    "Send Plan Choice",
                                    id="send-plan-choice",
                                    variant="primary",
                                )
                                yield Button(
                                    "Send to Codex Pane",
                                    id="send-plan-tmux",
                                )
                        with Horizontal(id="thread-actions"):
                            yield Button("Export Item", id="export-item")
                            yield Button("Export Marked", id="export-marked")
                            yield Button("Export All", id="export-all")
                            yield Button("Clear Marks", id="clear-marks")
                            yield Button("Delete Queued", id="delete-queued")
                    with TabPane("WorkerBee", id="workerbee-tab"):
                        yield TextArea(id="workerbee-detail", read_only=True)
                        with Horizontal(id="workerbee-actions"):
                            yield Button("Refresh WorkerBee", id="workerbee-refresh")
        yield Footer()

    async def on_mount(self) -> None:
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
        latest_plan_options = self.query_one("#latest-plan-options", DataTable)
        latest_plan_options.add_columns("#", "Option")
        plan_options = self.query_one("#plan-options", DataTable)
        plan_options.add_columns("#", "Option")
        self.render_latest_plan_choice_panel(None)
        self.render_plan_choice_panel(None)
        await self.refresh_agents()
        await self.refresh_events()
        self.set_interval(2.0, self.refresh_agents)
        self.set_interval(self.tmux_refresh_seconds, self.refresh_tmux_capture_if_active)
        self.set_interval(0.8, self.toggle_unseen_attention)
        self.run_worker(self.stream_events(), name="events", exclusive=True)

    async def action_refresh(self) -> None:
        await self.refresh_agents()
        await self.refresh_events()
        if self.selected_agent_id:
            await self.refresh_selected_agent(self.selected_agent_id)
            if self.active_agent_tab == "workerbee-tab":
                await self.load_workerbee_status(self.selected_agent_id)

    def action_settings(self) -> None:
        self.push_screen(
            SettingsScreen(
                visual_flash_enabled=self.visual_flash_enabled,
                terminal_bell_enabled=self.terminal_bell_enabled,
                agent_blink_enabled=self.agent_blink_enabled,
                layout_mode=self.layout_mode,
                split_percent=self.split_percent,
                tmux_direct_enabled=self.tmux_direct_enabled,
                tmux_features_available=self.tmux_features_available,
                custom_theme_name=self.custom_theme_name,
                custom_theme_enabled=self.ui_theme == self.custom_theme_name,
            )
        )

    def action_back(self) -> None:
        if self.is_compact_layout() and self.compact_view == "agent":
            self.show_compact_home()

    async def action_toggle_tmux_direct(self) -> None:
        if self.active_agent_tab != "latest-tab":
            return
        enabled = self.set_tmux_direct_enabled(not self.tmux_direct_enabled)
        if not self.selected_agent_id:
            return
        if enabled:
            await self.load_tmux_capture(self.selected_agent_id)
        else:
            await self.load_latest_report(self.selected_agent_id)

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
            self.tmux_detached_agent_ids.clear()
        self.apply_tmux_class()
        self.update_plan_tmux_button_state()
        self.save_settings()
        return enabled

    def update_plan_tmux_button_state(self) -> None:
        send_tmux = self.query_one_or_none("#send-plan-tmux", Button)
        if send_tmux is not None:
            send_tmux.disabled = (
                not self.tmux_direct_enabled or self.selected_plan_option() is None
            )
        latest_send_tmux = self.query_one_or_none("#latest-send-plan-tmux", Button)
        if latest_send_tmux is not None:
            latest_send_tmux.disabled = (
                not self.tmux_direct_enabled
                or self.selected_latest_plan_option() is None
            )

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
            return ("New", "Agent", "Status", "Queue", *live)
        if self.effective_layout_mode == COMPACT_TUI_LAYOUT:
            return ("New", "Agent", "Plan", "Status", "Queue", *live, "Poll")
        return (
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
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get("/v1/agents", headers=auth_headers(self.token))
                response.raise_for_status()
                agents = response.json()
        except Exception as exc:
            detail = self.query_one_or_none("#detail", TextArea)
            if detail is not None:
                detail.text = f"Unable to refresh agents: {exc}"
            return

        previous_last_seen = self.agent_last_seen_at.copy()
        self.agents = {str(agent["agent_id"]): agent for agent in agents}
        self.update_unseen_from_agent_refresh(previous_last_seen)
        self.render_agents()
        self.render_unseen_attention()

    def update_unseen_from_agent_refresh(
        self, previous_last_seen: dict[str, float]
    ) -> None:
        self.unseen_latest_agent_ids.intersection_update(self.agents)
        for agent_id, agent in self.agents.items():
            current_last_seen = self.agent_last_seen(agent_id)
            if current_last_seen is None:
                continue
            previous = previous_last_seen.get(agent_id)
            latest_viewed_at = self.latest_viewed_at_by_agent.get(agent_id)
            if latest_viewed_at is not None and current_last_seen <= latest_viewed_at:
                self.unseen_latest_agent_ids.discard(agent_id)
            elif previous is None:
                if latest_viewed_at is not None and current_last_seen > latest_viewed_at:
                    if self.is_latest_engaged(agent_id):
                        self.latest_viewed_at_by_agent[agent_id] = current_last_seen
                        self.unseen_latest_agent_ids.discard(agent_id)
                    else:
                        self.unseen_latest_agent_ids.add(agent_id)
            elif current_last_seen > previous:
                if self.is_latest_engaged(agent_id):
                    self.latest_viewed_at_by_agent[agent_id] = current_last_seen
                    self.unseen_latest_agent_ids.discard(agent_id)
                else:
                    self.unseen_latest_agent_ids.add(agent_id)
            self.agent_last_seen_at[agent_id] = current_last_seen

    def agent_last_seen(self, agent_id: str) -> float | None:
        agent = self.agents.get(agent_id)
        if agent is None:
            return None
        try:
            return float(agent["last_seen_at"])
        except (KeyError, TypeError, ValueError):
            return None

    def is_latest_engaged(self, agent_id: str) -> bool:
        if self.is_compact_layout() and self.compact_view != "agent":
            return False
        return agent_id == self.selected_agent_id and self.active_agent_tab == "latest-tab"

    def render_agents(self) -> None:
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
        for agent in self.agents.values():
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
        return str(agent.get("effective_status") or agent.get("status") or "")

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
        entry = self.tmux_liveness_by_agent.get(agent_id)
        if entry is None:
            return "-"
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
            if style is None and self.tmux_features_available:
                tmux_level = self.tmux_liveness_level(str(agent.get("agent_id") or ""))
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
        if not self.agent_blink_enabled or not self.unseen_latest_agent_ids:
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
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get("/v1/events", headers=auth_headers(self.token))
                response.raise_for_status()
                self.events = response.json()[-50:]
        except Exception:
            return
        previous_last_seen_event_id = self.last_seen_event_id
        self.last_seen_event_id = max(
            [self.last_seen_event_id, *[int(event["event_id"]) for event in self.events]]
        )
        if self.last_seen_event_id != previous_last_seen_event_id:
            self.save_settings()
        self.render_events()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "agents":
            await self.select_agent(str(event.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.row_key.value))
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
        if isinstance(focused, (Input, TextArea)):
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
        if event.key == "e" or event.character == "e":
            event.stop()
            self.toggle_tiny_events()

    async def on_click(self, event: Click) -> None:
        if getattr(event.widget, "id", None) == "attention":
            opened = await self.open_attention_latest()
            if opened:
                event.stop()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "message":
            self.resize_message_input()

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if event.tabbed_content.id != "agent-tabs":
            return
        self.active_agent_tab = str(event.pane.id)
        if self.active_agent_tab == "latest-tab" and self.selected_agent_id:
            self.mark_latest_seen(self.selected_agent_id)
            if self.tmux_direct_enabled:
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

    async def select_agent(self, agent_id: str) -> None:
        if agent_id != self.selected_agent_id:
            self.selected_thread_item_id = None
        was_compact_home = self.is_compact_layout() and self.compact_view == "home"
        self.selected_agent_id = agent_id
        self.query_one("#agent-id", Input).value = self.selected_agent_id
        agent_title = self.query_one_or_none("#agent-title", Static)
        if agent_title is not None:
            agent_title.update(f"Agent: {agent_id}")
        if self.is_compact_layout():
            self.show_compact_agent()
        if was_compact_home or self.active_agent_tab in {"latest-tab", "thread-tab"}:
            self.activate_latest_tab()
        await self.refresh_selected_agent(self.selected_agent_id)
        if self.active_agent_tab == "latest-tab":
            self.mark_latest_seen(agent_id)
        elif self.active_agent_tab == "workerbee-tab":
            await self.load_workerbee_status(agent_id)

    def activate_latest_tab(self) -> None:
        tabs = self.query_one("#agent-tabs", TabbedContent)
        tabs.active = "latest-tab"
        self.active_agent_tab = "latest-tab"

    async def refresh_selected_agent(self, agent_id: str) -> None:
        if self.tmux_direct_enabled:
            await self.load_tmux_capture(agent_id)
        else:
            await self.load_latest_report(agent_id)
        await self.load_thread(agent_id)

    def mark_latest_seen(self, agent_id: str) -> None:
        last_seen = self.agent_last_seen(agent_id)
        changed = False
        if last_seen is not None:
            changed = self.latest_viewed_at_by_agent.get(agent_id) != last_seen
            self.latest_viewed_at_by_agent[agent_id] = last_seen
        if agent_id not in self.unseen_latest_agent_ids:
            if changed:
                self.save_settings()
            return
        self.unseen_latest_agent_ids.remove(agent_id)
        self.render_agents()
        self.render_unseen_attention()
        self.save_settings()

    async def load_latest_report(self, agent_id: str) -> None:
        detail = self.query_one_or_none("#detail", TextArea)
        if detail is None:
            return
        try:
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get(
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
        plan_options = [
            str(option)
            for option in list_value(report.get("plan_options"))
            if str(option).strip()
        ]
        lines = [
            f"Agent: {report['agent_id']}",
            f"Status: {report['status']}",
            f"Needs Input: {'yes' if report.get('needs_input') else 'no'}",
            f"Summary: {report['summary']}",
            "",
            str(report["detail"]),
        ]
        if plan_options:
            lines.extend(["", "Plan Options:", *[f"- {option}" for option in plan_options]])
        detail.text = "\n".join(lines)
        self.render_latest_plan_choice_panel(report)

    async def refresh_tmux_capture_if_active(self) -> None:
        if (
            not self.tmux_direct_enabled
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
        if pane is None:
            self.tmux_visible_capture_key = None
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
            self.record_tmux_liveness_state(agent_id, "unavailable", pane_id=pane.pane_id)
            stream.text = f"Unable to capture tmux pane {pane.pane_id}: {exc}"
            self.render_agents()
            return
        displayed = self.crop_tmux_capture_for_display(captured or "(empty tmux pane)")
        self.record_tmux_capture_liveness(agent_id, pane.pane_id, displayed)
        self.update_tmux_stream(
            stream,
            displayed,
            cache_key=cache_key,
        )
        self.update_tmux_status(
            status,
            (
                "Tmux: "
                f"{pane.pane_id} {pane.target_label} {mode} "
                f"{self.tmux_capture_mode_label()} "
                "cropped "
                f"{pane.current_command} {pane.width}x{pane.height}"
            ),
            cache_key=agent_id,
        )
        self.render_agents()

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
        stream.scroll_home(animate=False)
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
        at_bottom = bool(getattr(stream, "is_vertical_scroll_end", True))
        scroll_y = stream.scroll_y
        scroll_target_y = stream.scroll_target_y
        stream.text = captured
        if at_bottom:
            stream.scroll_end(animate=False)
        else:
            stream.scroll_y = scroll_y
            stream.scroll_target_y = scroll_target_y
        return True

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
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get(
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
        if event.button.id == "send-plan-choice":
            await self.send_plan_choice()
            return
        if event.button.id == "send-plan-tmux":
            await self.send_plan_choice_to_tmux()
            return
        if event.button.id == "latest-send-plan-choice":
            await self.send_latest_plan_choice()
            return
        if event.button.id == "latest-send-plan-tmux":
            await self.send_latest_plan_choice_to_tmux()
            return
        if event.button.id == "workerbee-refresh":
            if self.selected_agent_id:
                await self.load_workerbee_status(self.selected_agent_id)

    async def send_input(self) -> None:
        if self.tmux_direct_enabled:
            await self.send_tmux_input()
            return
        agent_id = self.query_one("#agent-id", Input).value.strip()
        message_input = self.query_one("#message", TextArea)
        message = message_input.text.strip()
        if not agent_id or not message:
            return
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": message},
        )
        message_input.text = ""
        self.resize_message_input()
        self.notify_queued_command(agent_id, "Input", command)
        await self.refresh_events()
        await self.load_thread(agent_id)

    def resize_message_input(self) -> None:
        message_input = self.query_one("#message", TextArea)
        line_count = max(1, message_input.text.count("\n") + 1)
        height = min(
            FOLLOW_UP_MAX_HEIGHT,
            max(FOLLOW_UP_MIN_HEIGHT, line_count + 2),
        )
        message_input.styles.height = height
        self.query_one("#composer-inputs").styles.height = height
        self.query_one("#composer").styles.height = height + 7

    async def send_tmux_input(self) -> None:
        agent_id = self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        message_input = self.query_one("#tmux-message", TextArea)
        message = message_input.text
        if not agent_id or not message.strip():
            return
        sent = await self.send_text_to_tmux(agent_id, message)
        if not sent:
            return
        message_input.text = ""
        await self.load_tmux_capture(agent_id)

    async def send_text_to_tmux(self, agent_id: str, message: str) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        try:
            panes = await asyncio.to_thread(tmux_support.list_panes)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: unavailable ({exc})")
            return False
        self.tmux_panes = panes
        pane, mode = self.resolve_tmux_pane(agent_id, panes)
        if pane is None:
            if status is not None:
                if mode == "stale":
                    status.update(f"Tmux: stale target for {agent_id}")
                else:
                    status.update(f"Tmux: no pane for {agent_id}")
            return False
        try:
            await asyncio.to_thread(tmux_support.send_text, pane.pane_id, message)
        except Exception as exc:
            if status is not None:
                status.update(f"Tmux: send failed ({exc})")
            return False
        return True

    def selected_plan_option(self) -> str | None:
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

    def selected_latest_plan_option(self) -> str | None:
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

    def plan_options_for_item(self, item: dict[str, Any] | None) -> list[str]:
        if not item or item.get("kind") != "report":
            return []
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        return [
            str(option)
            for option in list_value(metadata.get("plan_options"))
            if str(option).strip()
        ]

    def plan_options_for_report(self, report: dict[str, Any] | None) -> list[str]:
        if not report:
            return []
        return [
            str(option)
            for option in list_value(report.get("plan_options"))
            if str(option).strip()
        ]

    def plan_choice_message(self, option: str, notes: str = "") -> str:
        message = f"Selected plan option: {option.strip()}"
        clean_notes = notes.strip()
        if clean_notes:
            message = f"{message}\n\nOperator notes:\n{clean_notes}"
        return message

    async def send_plan_choice(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        option = self.selected_plan_option()
        if not agent_id or option is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        notes_input = self.query_one("#plan-notes", TextArea)
        message = self.plan_choice_message(option, notes_input.text)
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": message},
        )
        notes_input.text = ""
        self.notify_queued_command(agent_id, "Plan choice", command)
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def send_plan_choice_to_tmux(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        option = self.selected_plan_option()
        if not self.tmux_direct_enabled:
            self.notify(
                "Enable tmux direct mode before sending to Codex pane.",
                severity="warning",
            )
            return
        if not agent_id or option is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        notes_input = self.query_one("#plan-notes", TextArea)
        message = self.plan_choice_message(option, notes_input.text)
        sent = await self.send_text_to_tmux(agent_id, message)
        if not sent:
            return
        notes_input.text = ""
        self.notify(f"Sent plan choice to Codex pane for {agent_id}.")
        await self.load_tmux_capture(agent_id)

    async def send_latest_plan_choice(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        option = self.selected_latest_plan_option()
        if not agent_id or option is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        notes_input = self.query_one("#latest-plan-notes", TextArea)
        message = self.plan_choice_message(option, notes_input.text)
        command = await self.queue_command(
            agent_id,
            "send_input",
            {"message": message},
        )
        notes_input.text = ""
        self.notify_queued_command(agent_id, "Plan choice", command)
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def send_latest_plan_choice_to_tmux(self) -> None:
        agent_id = (
            self.selected_agent_id or self.query_one("#agent-id", Input).value.strip()
        )
        option = self.selected_latest_plan_option()
        if not self.tmux_direct_enabled:
            self.notify(
                "Enable tmux direct mode before sending to Codex pane.",
                severity="warning",
            )
            return
        if not agent_id or option is None:
            self.notify("Select a plan option first.", severity="warning")
            return
        notes_input = self.query_one("#latest-plan-notes", TextArea)
        message = self.plan_choice_message(option, notes_input.text)
        sent = await self.send_text_to_tmux(agent_id, message)
        if not sent:
            return
        notes_input.text = ""
        self.notify(f"Sent plan choice to Codex pane for {agent_id}.")
        await self.load_tmux_capture(agent_id)

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

    async def queue_command(
        self, agent_id: str, command_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
            response = await client.post(
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

    async def create_agent_report(
        self, agent_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
            response = await client.post(
                f"/v1/agents/{agent_id}/reports",
                json=payload,
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            return response.json()

    async def delete_queued_command(self, command_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
            response = await client.delete(
                f"/v1/commands/{command_id}",
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            return response.json()

    async def load_workerbee_status(self, agent_id: str) -> None:
        detail = self.query_one("#workerbee-detail", TextArea)
        detail.text = f"Loading WorkerBee status for {agent_id}..."
        try:
            async with httpx.AsyncClient(base_url=self.server, timeout=15) as client:
                response = await client.get(
                    f"/v1/agents/{agent_id}/workerbee",
                    headers=auth_headers(self.token),
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
        elif event.checkbox.id == "tmux-direct":
            enabled = self.set_tmux_direct_enabled(event.value)
            if self.selected_agent_id:
                worker = (
                    self.load_tmux_capture(self.selected_agent_id)
                    if enabled
                    else self.load_latest_report(self.selected_agent_id)
                )
                self.run_worker(
                    worker,
                    name="tmux-toggle",
                    exclusive=True,
                )
        elif event.checkbox.id == "theme-1337":
            self.set_ui_theme(self.custom_theme_name if event.value else DEFAULT_TUI_THEME)

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
        widgets = [agents_title, agents, events_title, events]
        if any(widget is None for widget in widgets):
            return
        assert agents_title is not None
        assert agents is not None
        assert events_title is not None
        assert events is not None
        tiny_home = self.is_tiny_layout() and self.compact_view == "home"
        show_events = tiny_home and self.tiny_show_events
        show_agents = not tiny_home or not show_events
        agents_title.styles.display = "block" if show_agents else "none"
        agents.styles.display = "block" if show_agents else "none"
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
            if self.tmux_direct_enabled and self.active_agent_tab == "latest-tab":
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
            screen.set_class(self.tmux_direct_enabled, "tmux-direct")

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
        return DEFAULT_TUI_THEME

    def save_settings(self) -> None:
        self.settings = {
            "visual_flash": self.visual_flash_enabled,
            "terminal_bell": self.terminal_bell_enabled,
            "agent_blink": self.agent_blink_enabled,
            "theme": self.ui_theme,
            "layout": self.layout_mode,
            "split_percent": self.split_percent,
            "export_dir": str(self.export_dir),
            "tmux_direct": self.tmux_direct_enabled,
            "tmux_capture_lines": self.tmux_capture_lines,
            "tmux_agent_targets": self.tmux_agent_targets,
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
                            self.call_later(self.notify_stream_reconnected)
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
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
            except Exception:
                if not self.event_stream_disconnected:
                    self.event_stream_disconnected = True
                    self.call_later(self.notify_stream_disconnected)
                await asyncio.sleep(2)

    def notify_stream_disconnected(self) -> None:
        self.notify("Event stream disconnected; retrying.", severity="warning")

    def notify_stream_reconnected(self) -> None:
        self.notify("Event stream reconnected.")

    def handle_event(self, event: dict[str, Any]) -> None:
        self.render_events()
        event_type = str(event.get("type"))
        agent_id = self.event_agent_id(event)
        selected_agent_id = self.selected_agent_id
        if event_type == "report_created" and agent_id == selected_agent_id:
            self.activate_latest_tab()
        if event_type in {
            "agent_registered",
            "report_created",
            "command_queued",
            "command_delivered",
            "command_acked",
            "command_deleted",
            "agent_pbx_active_changed",
        }:
            self.run_worker(
                self.refresh_agents(),
                name="agents-refresh",
                exclusive=True,
            )
        if event_type == "report_created" and agent_id:
            self.mark_latest_unseen(agent_id)
        if selected_agent_id and agent_id == selected_agent_id:
            if event_type == "report_created":
                self.run_worker(
                    self.refresh_selected_agent(selected_agent_id),
                    name="selected-agent-report",
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
                    exclusive=True,
                )
        if self.should_alert(event):
            self.alert_for_event(event)

    def mark_latest_unseen(self, agent_id: str) -> None:
        if self.is_latest_engaged(agent_id):
            self.mark_latest_seen(agent_id)
            return
        self.unseen_latest_agent_ids.add(agent_id)
        self.render_agents()
        self.render_unseen_attention()

    def render_events(self) -> None:
        table = self.query_one("#events", DataTable)
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
        notes = self.query_one_or_none("#latest-plan-notes", TextArea)
        send = self.query_one_or_none("#latest-send-plan-choice", Button)
        send_tmux = self.query_one_or_none("#latest-send-plan-tmux", Button)
        if panel is None or table is None or notes is None:
            return
        options = self.plan_options_for_report(report)
        table.clear()
        self.selected_latest_plan_option_index = None
        if not options:
            panel.styles.display = "none"
            notes.text = ""
            if send is not None:
                send.disabled = True
            if send_tmux is not None:
                send_tmux.disabled = True
            return
        panel.styles.display = "block"
        for index, option in enumerate(options):
            table.add_row(str(index + 1), option, key=str(index))
        self.selected_latest_plan_option_index = 0
        table.move_cursor(row=0, animate=False, scroll=False)
        if send is not None:
            send.disabled = False
        if send_tmux is not None:
            send_tmux.disabled = not self.tmux_direct_enabled

    def render_plan_choice_panel(self, item: dict[str, Any] | None) -> None:
        panel = self.query_one_or_none("#plan-choice-panel", Vertical)
        table = self.query_one_or_none("#plan-options", DataTable)
        notes = self.query_one_or_none("#plan-notes", TextArea)
        send = self.query_one_or_none("#send-plan-choice", Button)
        send_tmux = self.query_one_or_none("#send-plan-tmux", Button)
        if panel is None or table is None or notes is None:
            return
        options = self.plan_options_for_item(item)
        table.clear()
        self.selected_plan_option_index = None
        if not options:
            panel.styles.display = "none"
            notes.text = ""
            if send is not None:
                send.disabled = True
            if send_tmux is not None:
                send_tmux.disabled = True
            return
        panel.styles.display = "block"
        for index, option in enumerate(options):
            table.add_row(str(index + 1), option, key=str(index))
        self.selected_plan_option_index = 0
        table.move_cursor(row=0, animate=False, scroll=False)
        if send is not None:
            send.disabled = False
        if send_tmux is not None:
            send_tmux.disabled = not self.tmux_direct_enabled

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
        self.update_plan_tmux_button_state()

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
        self.update_plan_tmux_button_state()

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
            plan_options = metadata.get("plan_options") or []
            if plan_options:
                lines.extend(["", "Plan Options:", *[f"- {option}" for option in plan_options]])
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
        if not self.unseen_latest_agent_ids:
            attention.update("")
            attention.remove_class("attention-active")
            self.attention_agent_id = None
        else:
            attention.remove_class("attention-active")
            self.render_unseen_attention()
        self.set_attention_flash_class(False)


def run_tui(*, server: str, token: str | None = None) -> None:
    AgentPBXTUI(server=server, token=token).run()
