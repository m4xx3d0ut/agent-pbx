from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import httpx
from rich.color import Color, ColorParseError
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Horizontal, Vertical
from textual.events import Click, Key
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .client import auth_headers


TRUE_ENV_VALUES = {"1", "true", "yes", "on", "y", "enabled"}
FALSE_ENV_VALUES = {"0", "false", "no", "off", "n", "disabled", ""}
ATTENTION_EVENT_TYPES = {"agent_registered", "report_created", "command_acked"}
DEFAULT_TUI_THEME = "cyberpunk"
DEFAULT_CUSTOM_THEME_NAME = "1337"
THEME_1337_NAME = DEFAULT_CUSTOM_THEME_NAME
DEFAULT_EXPORT_DIR = Path("artifacts/thread-exports")
DEFAULT_SETTINGS_FILE = Path("agent-pbx/tui-settings.json")
FOLLOW_UP_MIN_HEIGHT = 3
FOLLOW_UP_MAX_HEIGHT = 15
SHIFT_ENTER_KEYS = {"shift+enter", "shift_enter", "shift+return"}
STALE_POLL_SECONDS = 120
QUEUED_COMMAND_WARN_SECONDS = 60
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


def env_export_dir(default: Path = DEFAULT_EXPORT_DIR) -> Path:
    return env_export_dir_value() or default


def env_export_dir_value() -> Path | None:
    value = os.getenv("AGENT_PBX_TUI_EXPORT_DIR", "").strip()
    return Path(value).expanduser() if value else None


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
        if event.key in SHIFT_ENTER_KEYS:
            event.stop()
            event.prevent_default()
            self.insert("\n")
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

    #detail {
        height: 1fr;
        min-height: 8;
    }

    #thread {
        height: 7;
        min-height: 5;
    }

    #thread-detail {
        height: 1fr;
        min-height: 12;
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

    #notification-options {
        height: 3;
    }

    #notification-options Checkbox {
        width: 1fr;
    }

    #events {
        height: 12;
    }

    #composer {
        height: auto;
        min-height: 6;
        max-height: 18;
    }

    #composer-inputs {
        height: auto;
        max-height: 15;
    }

    #agent-id {
        width: 35%;
        height: 3;
    }

    #message {
        width: 65%;
        height: 3;
        min-height: 3;
        max-height: 15;
        scrollbar-size: 1 1;
        scrollbar-color: $accent;
        scrollbar-color-hover: $warning;
        scrollbar-background: $surface;
    }

    #composer-buttons {
        height: 3;
    }

    #composer-buttons Button {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("r", "refresh", "Refresh"),
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
        self.agents: dict[str, dict[str, Any]] = {}
        self.selected_agent_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.thread_items: dict[str, dict[str, Any]] = {}
        self.thread_order: list[str] = []
        self.selected_thread_item_id: str | None = None
        self.marked_thread_item_ids: set[str] = set()
        self.active_agent_tab = "latest-tab"
        self.unseen_latest_agent_ids: set[str] = set()
        self.agent_last_seen_at: dict[str, float] = {}
        self.latest_viewed_at_by_agent: dict[str, float] = {}
        self.workerbee_status_by_agent: dict[str, dict[str, Any]] = {}
        self.attention_blink_phase = False
        self.attention_agent_id: str | None = None
        self.event_stream_disconnected = False
        self.last_seen_event_id = 0
        self.flash_generation = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="attention")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("Agents")
                yield DataTable(
                    id="agents",
                    cursor_type="row",
                    show_row_labels=False,
                )
                yield Static("Events")
                yield DataTable(
                    id="events",
                    cursor_type="row",
                    show_row_labels=False,
                )
            with Vertical(id="right"):
                with TabbedContent(initial="latest-tab", id="agent-tabs"):
                    with TabPane("Latest", id="latest-tab"):
                        yield TextArea(id="detail", read_only=True)
                    with TabPane("Thread", id="thread-tab"):
                        yield DataTable(
                            id="thread",
                            cursor_type="row",
                            show_row_labels=False,
                        )
                        yield TextArea(id="thread-detail", read_only=True)
                        with Horizontal(id="thread-actions"):
                            yield Button("Export Item", id="export-item")
                            yield Button("Export Marked", id="export-marked")
                            yield Button("Export All", id="export-all")
                            yield Button("Clear Marks", id="clear-marks")
                    with TabPane("WorkerBee", id="workerbee-tab"):
                        yield TextArea(id="workerbee-detail", read_only=True)
                        with Horizontal(id="workerbee-actions"):
                            yield Button("Refresh WorkerBee", id="workerbee-refresh")
                yield Static("Notification Options")
                with Horizontal(id="notification-options"):
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
                        f"{self.custom_theme_name} theme",
                        value=self.ui_theme == self.custom_theme_name,
                        id="theme-1337",
                    )
                with Vertical(id="composer"):
                    with Horizontal(id="composer-inputs"):
                        yield Input(placeholder="Agent id", id="agent-id")
                        yield FollowUpTextArea(id="message", soft_wrap=True)
                    with Horizontal(id="composer-buttons"):
                        yield Button("Send Input", id="send", variant="primary")
                        yield Button("Request Detail", id="request-detail")
                        yield Button("Ping", id="ping-agent")
        yield Footer()

    async def on_mount(self) -> None:
        self.screen.set_class(self.ui_theme == self.custom_theme_name, "custom-theme")
        agents = self.query_one("#agents", DataTable)
        agents.add_columns("New", "Agent", "Status", "Project", "Last Seen", "Queue", "Poll", "Use")
        events = self.query_one("#events", DataTable)
        events.add_columns("ID", "Type", "Subject")
        thread = self.query_one("#thread", DataTable)
        thread.add_columns("M", "Time", "Kind", "Status", "Summary")
        await self.refresh_agents()
        await self.refresh_events()
        self.set_interval(2.0, self.refresh_agents)
        self.set_interval(0.8, self.toggle_unseen_attention)
        self.run_worker(self.stream_events(), name="events", exclusive=True)

    async def action_refresh(self) -> None:
        await self.refresh_agents()
        await self.refresh_events()
        if self.selected_agent_id:
            await self.refresh_selected_agent(self.selected_agent_id)
            if self.active_agent_tab == "workerbee-tab":
                await self.load_workerbee_status(self.selected_agent_id)

    async def refresh_agents(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get("/v1/agents", headers=auth_headers(self.token))
                response.raise_for_status()
                agents = response.json()
        except Exception as exc:
            self.query_one("#detail", TextArea).text = f"Unable to refresh agents: {exc}"
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
            if previous is not None and current_last_seen > previous:
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
        return agent_id == self.selected_agent_id and self.active_agent_tab == "latest-tab"

    def render_agents(self) -> None:
        table = self.query_one("#agents", DataTable)
        cursor_agent_id = self.agent_id_at_cursor()
        table.clear()
        for agent in self.agents.values():
            agent_id = str(agent["agent_id"])
            unseen = agent_id in self.unseen_latest_agent_ids
            marker = "NEW" if unseen else ""
            cells = [
                marker,
                agent_id,
                str(agent["status"]),
                str(agent["project"]),
                f"{agent['last_seen_at']:.0f}",
                self.format_queue_state(agent),
                self.format_poll_state(agent),
                self.format_usage_state(agent),
            ]
            table.add_row(*cells, key=agent_id)
        restore_agent_id = cursor_agent_id if cursor_agent_id in self.agents else None
        if restore_agent_id is not None:
            table.move_cursor(
                row=table.get_row_index(restore_agent_id),
                animate=False,
                scroll=False,
            )

    def agent_id_at_cursor(self) -> str | None:
        table = self.query_one("#agents", DataTable)
        if table.row_count == 0 or not table.is_valid_row_index(table.cursor_row):
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def focus_agent_row(self, agent_id: str) -> None:
        if agent_id not in self.agents:
            return
        table = self.query_one("#agents", DataTable)
        table.move_cursor(
            row=table.get_row_index(agent_id),
            animate=False,
            scroll=True,
        )
        table.focus()

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
            return "never" if queued_count else "-"
        age = max(0.0, time.time() - last_poll_at)
        if age <= 60:
            return "active"
        label = f"{format_duration(age)} ago"
        if queued_count and age >= STALE_POLL_SECONDS:
            return f"stale {label}"
        return label

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
        attention = self.query_one("#attention", Static)
        if not self.agent_blink_enabled or not self.unseen_latest_agent_ids:
            if attention.has_class("unseen-active"):
                attention.update("")
                attention.remove_class("unseen-active")
                if not attention.has_class("attention-active"):
                    self.attention_agent_id = None
            self.screen.remove_class("attention-flash")
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
        self.screen.set_class(self.attention_blink_phase, "attention-flash")

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
        await self.select_agent(agent_id)
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
        self.last_seen_event_id = max(
            [self.last_seen_event_id, *[int(event["event_id"]) for event in self.events]]
        )
        self.render_events()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "agents":
            await self.select_agent(str(event.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.row_key.value))

    async def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        if event.data_table.id == "agents":
            await self.select_agent(str(event.cell_key.row_key.value))
            return
        if event.data_table.id == "thread":
            self.select_thread_item(str(event.cell_key.row_key.value))

    def on_key(self, event: Key) -> None:
        if event.key == "space" and self.focused is self.query_one("#thread", DataTable):
            event.stop()
            self.toggle_current_thread_mark()

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
        if self.active_agent_tab == "workerbee-tab" and self.selected_agent_id:
            self.run_worker(
                self.load_workerbee_status(self.selected_agent_id),
                name="workerbee-status",
                exclusive=True,
            )

    async def select_agent(self, agent_id: str) -> None:
        if agent_id != self.selected_agent_id:
            self.selected_thread_item_id = None
        self.selected_agent_id = agent_id
        self.query_one("#agent-id", Input).value = self.selected_agent_id
        if self.active_agent_tab in {"latest-tab", "thread-tab"}:
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
        await self.load_latest_report(agent_id)
        await self.load_thread(agent_id)

    def mark_latest_seen(self, agent_id: str) -> None:
        last_seen = self.agent_last_seen(agent_id)
        if last_seen is not None:
            self.latest_viewed_at_by_agent[agent_id] = last_seen
        if agent_id not in self.unseen_latest_agent_ids:
            return
        self.unseen_latest_agent_ids.remove(agent_id)
        self.render_agents()
        self.render_unseen_attention()

    async def load_latest_report(self, agent_id: str) -> None:
        detail = self.query_one("#detail", TextArea)
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
            return
        if not reports:
            detail.text = f"No reports for {agent_id}."
            return
        report = reports[0]
        detail.text = (
            f"Agent: {report['agent_id']}\n"
            f"Status: {report['status']}\n"
            f"Summary: {report['summary']}\n\n"
            f"{report['detail']}"
        )

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
        if event.button.id == "request-detail":
            await self.request_detail()
            return
        if event.button.id == "ping-agent":
            await self.ping_agent()
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
        if event.button.id == "workerbee-refresh":
            if self.selected_agent_id:
                await self.load_workerbee_status(self.selected_agent_id)

    async def send_input(self) -> None:
        agent_id = self.query_one("#agent-id", Input).value.strip()
        message_input = self.query_one("#message", TextArea)
        message = message_input.text.strip()
        if not agent_id or not message:
            return
        await self.queue_command(
            agent_id,
            "send_input",
            {"message": message},
        )
        message_input.text = ""
        self.resize_message_input()
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
        self.query_one("#composer").styles.height = height + 3

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
            "Waiting for the agent to poll this command and publish a new report."
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
            "Waiting for the agent to reply with a pong report and restart polling."
        )
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
        elif event.checkbox.id == "theme-1337":
            self.set_ui_theme(self.custom_theme_name if event.value else DEFAULT_TUI_THEME)

    def set_ui_theme(self, theme_name: str) -> None:
        self.ui_theme = self.resolve_theme(theme_name)
        self.theme = self.ui_theme
        self.save_settings()
        try:
            self.screen.set_class(self.ui_theme == self.custom_theme_name, "custom-theme")
        except ScreenStackError:
            return

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
            "export_dir": str(self.export_dir),
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
            elif event_type in {"command_queued", "command_delivered", "command_acked"}:
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
        attention = self.query_one("#attention", Static)
        attention.update(message)
        attention.add_class("attention-active")
        self.screen.add_class("attention-flash")
        self.set_timer(3.0, lambda: self.clear_flash(generation))

    def clear_flash(self, generation: int) -> None:
        if generation != self.flash_generation:
            return
        attention = self.query_one("#attention", Static)
        if not self.unseen_latest_agent_ids:
            attention.update("")
            attention.remove_class("attention-active")
            self.attention_agent_id = None
        else:
            attention.remove_class("attention-active")
            self.render_unseen_attention()
        self.screen.remove_class("attention-flash")


def run_tui(*, server: str, token: str | None = None) -> None:
    AgentPBXTUI(server=server, token=token).run()
