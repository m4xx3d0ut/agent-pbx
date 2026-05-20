from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TextArea,
)

from .client import auth_headers


TRUE_ENV_VALUES = {"1", "true", "yes", "on", "y", "enabled"}
FALSE_ENV_VALUES = {"0", "false", "no", "off", "n", "disabled", ""}
ATTENTION_EVENT_TYPES = {"agent_registered", "report_created", "command_acked"}


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


class AgentPBXTUI(App[None]):
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

    Screen.attention-flash #left,
    Screen.attention-flash #right {
        border: solid $warning;
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

    #detail {
        height: 1fr;
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
        height: 5;
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
    ) -> None:
        super().__init__()
        self.server = server.rstrip("/")
        self.token = token
        self.visual_flash_enabled = (
            env_flag("AGENT_PBX_TUI_VISUAL_FLASH", "AGENT_PBX_TUI_FLASH")
            if visual_flash is None
            else visual_flash
        )
        self.terminal_bell_enabled = (
            env_flag("AGENT_PBX_TUI_TERMINAL_BELL", "AGENT_PBX_TUI_BELL")
            if terminal_bell is None
            else terminal_bell
        )
        self.agents: dict[str, dict[str, Any]] = {}
        self.selected_agent_id: str | None = None
        self.events: list[dict[str, Any]] = []
        self.last_seen_event_id = 0
        self.flash_generation = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="attention")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static("Agents")
                yield DataTable(id="agents")
                yield Static("Events")
                yield DataTable(id="events")
            with Vertical(id="right"):
                yield Static("Latest Report")
                yield TextArea(id="detail", read_only=True)
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
                with Vertical(id="composer"):
                    yield Input(placeholder="Agent id", id="agent-id")
                    yield Input(placeholder="Follow-up input", id="message")
                    yield Button("Send Input", id="send", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        agents = self.query_one("#agents", DataTable)
        agents.add_columns("Agent", "Status", "Project", "Last Seen")
        events = self.query_one("#events", DataTable)
        events.add_columns("ID", "Type", "Subject")
        await self.refresh_agents()
        await self.refresh_events()
        self.set_interval(2.0, self.refresh_agents)
        self.run_worker(self.stream_events(), name="events", exclusive=True)

    async def action_refresh(self) -> None:
        await self.refresh_agents()
        await self.refresh_events()

    async def refresh_agents(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
                response = await client.get("/v1/agents", headers=auth_headers(self.token))
                response.raise_for_status()
                agents = response.json()
        except Exception as exc:
            self.query_one("#detail", TextArea).text = f"Unable to refresh agents: {exc}"
            return

        self.agents = {agent["agent_id"]: agent for agent in agents}
        table = self.query_one("#agents", DataTable)
        table.clear()
        for agent in agents:
            table.add_row(
                agent["agent_id"],
                agent["status"],
                agent["project"],
                f"{agent['last_seen_at']:.0f}",
                key=agent["agent_id"],
            )

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
        if event.data_table.id != "agents":
            return
        self.selected_agent_id = str(event.row_key.value)
        self.query_one("#agent-id", Input).value = self.selected_agent_id
        await self.load_latest_report(self.selected_agent_id)

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

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "send":
            return
        agent_id = self.query_one("#agent-id", Input).value.strip()
        message = self.query_one("#message", Input).value.strip()
        if not agent_id or not message:
            return
        async with httpx.AsyncClient(base_url=self.server, timeout=10) as client:
            response = await client.post(
                "/v1/commands",
                json={
                    "agent_id": agent_id,
                    "type": "send_input",
                    "payload": {"message": message},
                },
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
        self.query_one("#message", Input).value = ""
        await self.refresh_events()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "visual-flash":
            self.visual_flash_enabled = event.value
        elif event.checkbox.id == "terminal-bell":
            self.terminal_bell_enabled = event.value

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
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                event = json.loads(line[6:])
                                event_id = int(event["event_id"])
                                if event_id <= self.last_seen_event_id:
                                    continue
                                self.last_seen_event_id = event_id
                                self.events.append(event)
                                self.events = self.events[-50:]
                                self.call_later(self.render_events)
                                if self.should_alert(event):
                                    self.call_later(self.alert_for_event, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)

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

    def should_alert(self, event: dict[str, Any]) -> bool:
        return str(event.get("type")) in ATTENTION_EVENT_TYPES

    def alert_for_event(self, event: dict[str, Any]) -> None:
        if self.terminal_bell_enabled:
            self.bell()
        if self.visual_flash_enabled:
            self.flash_for_event(event)

    def flash_for_event(self, event: dict[str, Any]) -> None:
        self.flash_generation += 1
        generation = self.flash_generation
        event_type = str(event.get("type", "event")).replace("_", " ")
        subject = event.get("subject_id") or ""
        message = f"New {event_type}"
        if subject:
            message = f"{message}: {subject}"
        attention = self.query_one("#attention", Static)
        attention.update(message)
        attention.add_class("attention-active")
        self.screen.add_class("attention-flash")
        self.set_timer(0.7, lambda: self.clear_flash(generation))

    def clear_flash(self, generation: int) -> None:
        if generation != self.flash_generation:
            return
        attention = self.query_one("#attention", Static)
        attention.update("")
        attention.remove_class("attention-active")
        self.screen.remove_class("attention-flash")


def run_tui(*, server: str, token: str | None = None) -> None:
    AgentPBXTUI(server=server, token=token).run()
