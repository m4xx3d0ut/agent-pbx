import inspect
import json
from pathlib import Path

import pytest

from agent_pbx.tui import (
    AgentPBXTUI,
    env_custom_palette,
    env_flag,
    env_theme,
)
from textual.events import Click, Key
from textual.widgets import Button, Checkbox, DataTable, Input, TextArea


@pytest.fixture(autouse=True)
def isolate_tui_settings(monkeypatch, tmp_path: Path) -> None:
    for name in [
        "AGENT_PBX_TUI_SETTINGS_FILE",
        "AGENT_PBX_TUI_FLASH",
        "AGENT_PBX_TUI_VISUAL_FLASH",
        "AGENT_PBX_TUI_BELL",
        "AGENT_PBX_TUI_TERMINAL_BELL",
        "AGENT_PBX_TUI_AGENT_BLINK",
        "AGENT_PBX_TUI_THEME",
        "AGENT_PBX_TUI_1337",
        "AGENT_PBX_TUI_CUSTOM_THEME_NAME",
        "AGENT_PBX_TUI_CUSTOM_FOREGROUND",
        "AGENT_PBX_TUI_CUSTOM_BACKGROUND",
        "AGENT_PBX_TUI_EXPORT_DIR",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "AGENT_PBX_TUI_SETTINGS_FILE",
        str(tmp_path / "agent-pbx" / "tui-settings.json"),
    )


def test_tui_constructs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")

    assert app.TITLE == "Agent PBX"
    assert app.server == "http://127.0.0.1:8765"
    assert app.token == "test"
    assert app.visual_flash_enabled is False
    assert app.terminal_bell_enabled is False
    assert app.agent_blink_enabled is True
    assert app.ui_theme == "cyberpunk"


def test_tui_reads_notification_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_BELL", "true")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.visual_flash_enabled is True
    assert app.terminal_bell_enabled is True


def test_tui_reads_saved_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "visual_flash": True,
                "terminal_bell": True,
                "agent_blink": False,
                "theme": "1337",
                "export_dir": str(tmp_path / "exports"),
            }
        ),
        encoding="utf-8",
    )

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    assert app.visual_flash_enabled is True
    assert app.terminal_bell_enabled is True
    assert app.agent_blink_enabled is False
    assert app.ui_theme == "1337"
    assert app.export_dir == tmp_path / "exports"


def test_tui_env_overrides_saved_settings(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"visual_flash": True, "agent_blink": False, "theme": "1337"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "0")
    monkeypatch.setenv("AGENT_PBX_TUI_AGENT_BLINK", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "cyberpunk")

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    assert app.visual_flash_enabled is False
    assert app.agent_blink_enabled is True
    assert app.ui_theme == "cyberpunk"


def test_tui_saves_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    app.visual_flash_enabled = True
    app.terminal_bell_enabled = True
    app.agent_blink_enabled = False
    app.set_ui_theme("1337")

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["visual_flash"] is True
    assert saved["terminal_bell"] is True
    assert saved["agent_blink"] is False
    assert saved["theme"] == "1337"


def test_tui_reads_theme_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "1337")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.ui_theme == "1337"
    assert app.theme == "1337"


def test_tui_maps_legacy_github_theme_to_default(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "github-dark")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.ui_theme == "cyberpunk"
    assert app.theme == "cyberpunk"


def test_tui_reads_custom_theme_name_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_CUSTOM_THEME_NAME", "matrix")
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "matrix")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.custom_theme_name == "matrix"
    assert app.ui_theme == "matrix"
    assert app.theme == "matrix"


def test_tui_reads_custom_palette_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_CUSTOM_FOREGROUND", "#11ff11")
    monkeypatch.setenv("AGENT_PBX_TUI_CUSTOM_BACKGROUND", "not-a-color")

    palette = env_custom_palette()

    assert palette["foreground"] == "#11ff11"
    assert palette["background"] == "#000000"


def test_tui_reads_legacy_1337_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_1337", "1")

    assert env_theme() == "1337"


def test_tui_agent_blink_defaults_on_and_can_be_disabled(monkeypatch) -> None:
    assert AgentPBXTUI(server="http://127.0.0.1:8765").agent_blink_enabled is True

    monkeypatch.setenv("AGENT_PBX_TUI_AGENT_BLINK", "off")

    assert AgentPBXTUI(server="http://127.0.0.1:8765").agent_blink_enabled is False


def test_tui_constructor_overrides_notification_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_BELL", "1")

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        visual_flash=False,
        terminal_bell=False,
        theme_name="cyberpunk",
    )

    assert app.visual_flash_enabled is False
    assert app.terminal_bell_enabled is False


def test_env_flag_accepts_false_values(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "off")

    assert env_flag("AGENT_PBX_TUI_FLASH", default=True) is False


def test_tui_alerts_only_for_attention_events() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.should_alert({"type": "report_created"}) is True
    assert app.should_alert({"type": "agent_registered"}) is True
    assert app.should_alert({"type": "command_acked"}) is True
    assert app.should_alert({"type": "command_queued"}) is False
    assert app.should_alert({"type": "command_delivered"}) is False


async def test_tui_mounts_notification_controls() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", visual_flash=True)

    async with app.run_test():
        visual = app.query_one("#visual-flash", Checkbox)
        bell = app.query_one("#terminal-bell", Checkbox)
        agent_blink = app.query_one("#agent-blink", Checkbox)
        theme = app.query_one("#theme-1337", Checkbox)
        agents = app.query_one("#agents", DataTable)
        thread = app.query_one("#thread", DataTable)
        request_detail = app.query_one("#request-detail", Button)
        ping = app.query_one("#ping-agent", Button)
        export_item = app.query_one("#export-item", Button)
        export_marked = app.query_one("#export-marked", Button)
        export_all = app.query_one("#export-all", Button)
        workerbee_detail = app.query_one("#workerbee-detail", TextArea)
        workerbee_refresh = app.query_one("#workerbee-refresh", Button)
        composer = app.query_one("#composer")
        message = app.query_one("#message", TextArea)
        buttons = app.query_one("#composer-buttons")

        assert visual.value is True
        assert bell.value is False
        assert agent_blink.value is True
        assert theme.value is False
        assert agents.cursor_type == "row"
        assert agents.show_row_labels is False
        assert thread.cursor_type == "row"
        assert thread.show_row_labels is False
        assert request_detail.label.plain == "Request Detail"
        assert ping.label.plain == "Ping"
        assert export_item.label.plain == "Export Item"
        assert export_marked.label.plain == "Export Marked"
        assert export_all.label.plain == "Export All"
        assert workerbee_detail.read_only is True
        assert workerbee_refresh.label.plain == "Refresh WorkerBee"
        assert "#thread {\n        height: 7;" in app.CSS
        assert "#thread-detail {\n        height: 1fr;" in app.CSS
        assert "#workerbee-detail {\n        height: 1fr;" in app.CSS
        assert message.region.bottom <= composer.region.bottom
        assert buttons.region.bottom <= composer.region.bottom


async def test_tui_select_agent_updates_composer_and_loads_report() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        await app.select_agent("agent-1")

        assert app.selected_agent_id == "agent-1"
        assert app.query_one("#agent-id", Input).value == "agent-1"
        assert tabs.active == "latest-tab"
        assert app.active_agent_tab == "latest-tab"
        assert loaded == ["agent-1"]
        assert threads == ["agent-1"]


async def test_tui_workerbee_tab_keeps_agent_selection_and_loads_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    async def fake_load_workerbee_status(agent_id: str) -> None:
        loaded.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]
    app.load_workerbee_status = fake_load_workerbee_status  # type: ignore[method-assign]

    async with app.run_test():
        tabs = app.query_one("#agent-tabs")
        tabs.active = "workerbee-tab"
        app.active_agent_tab = "workerbee-tab"
        await app.select_agent("agent-1")

        assert tabs.active == "workerbee-tab"
        assert app.active_agent_tab == "workerbee-tab"
        assert loaded == ["agent-1"]


def test_tui_formats_workerbee_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    status = {
        "available": True,
        "agent_id": "agent-1",
        "cwd": "/tmp/repo",
        "workerbee_bin": "/tmp/workerbee",
        "project": "demo-dev-123",
        "mode": "lazy",
        "running": False,
        "status_kind": "stopped",
        "dashboard_url": "https://dashboard.local/",
        "state_dir": "/tmp/state",
        "description": "no app workload deployed yet",
        "app_status": {
            "state": "no_workload_deployed",
            "ready": False,
            "message": "no app workload deployed yet",
            "declared_workload_count": 0,
            "ready_workload_count": 0,
            "degraded_workload_count": 0,
            "orphaned_workload_count": 0,
        },
        "latest_deployment": None,
        "project_card": {"status_kind": "stopped", "exposed_route_summary": "none"},
        "global_dashboard": {"dashboard_url": "https://dashboard.local/", "running": True},
    }

    rendered = app.format_workerbee_status(status)

    assert "Project" in rendered
    assert "Name: demo-dev-123" in rendered
    assert "No deployment recorded yet." in rendered
    assert "Global Dashboard" in rendered


def test_tui_workerbee_does_not_poll_while_active() -> None:
    source = inspect.getsource(AgentPBXTUI.on_mount)

    assert "refresh_workerbee_if_active" not in source
    assert "load_workerbee_status" not in source


def test_tui_extracts_event_agent_id() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.event_agent_id({"payload": {"agent_id": "agent-1"}}) == "agent-1"
    assert app.event_agent_id({"payload": {}}) is None
    assert app.event_agent_id({"payload": None}) is None


def test_tui_theme_toggle_updates_app_theme() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    app.set_ui_theme("1337")
    assert app.ui_theme == "1337"
    assert app.theme == "1337"

    app.set_ui_theme("cyberpunk")
    assert app.ui_theme == "cyberpunk"
    assert app.theme == "cyberpunk"


def test_tui_custom_theme_css_has_readable_text_area_highlights() -> None:
    assert "Screen.custom-theme TextArea .text-area--selection" in AgentPBXTUI.CSS
    assert "color: $background;" in AgentPBXTUI.CSS


def test_tui_formats_queue_and_poll_state(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    agent = {
        "queued_command_count": 2,
        "oldest_queued_command_age_seconds": 125.0,
        "last_poll_at": 700.0,
        "estimated_visible_tokens_per_hour": 12500,
        "polls_per_hour": 30,
        "reports_per_hour": 12,
        "pings_per_hour": 3,
        "usage_warning": "high estimated token use",
    }

    assert app.format_queue_state(agent) == "!2 2m"
    assert app.format_poll_state(agent) == "stale 5m ago"
    assert app.format_usage_state(agent) == "!12kt p30 r12 g3"
    assert app.format_poll_state({"queued_command_count": 0, "last_poll_at": 980.0}) == "active"
    assert app.format_poll_state({"queued_command_count": 0, "last_poll_at": None}) == "-"
    assert app.format_poll_state({"queued_command_count": 1, "last_poll_at": None}) == "never"
    assert app.format_usage_state({}) == "-"


async def test_tui_thread_selection_renders_detail() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        thread = [
            {
                "item_id": "report:r1",
                "kind": "report",
                "agent_id": "agent-1",
                "created_at": 123.0,
                "status": "done",
                "title": "Report summary",
                "body": "Full report detail",
                "metadata": {"plan_options": ["Next"]},
            },
            {
                "item_id": "command:c1",
                "kind": "command",
                "agent_id": "agent-1",
                "created_at": 124.0,
                "status": "acked",
                "title": "Follow-up input",
                "body": "Proceed",
                "metadata": {
                    "command_id": "c1",
                    "type": "send_input",
                    "acked_at": 125.0,
                    "result": {"ok": True},
                },
            },
        ]
        app.thread_items = {item["item_id"]: item for item in thread}
        app.render_thread(thread)
        app.select_thread_item("command:c1")
        detail = app.query_one("#thread-detail", TextArea).text

    assert app.selected_thread_item_id == "command:c1"
    assert "Command: c1" in detail
    assert "Proceed" in detail
    assert '"ok": true' in detail


async def test_tui_thread_table_renders_newest_first() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        thread = [
            {
                "item_id": "report:old",
                "kind": "report",
                "agent_id": "agent-1",
                "created_at": 100.0,
                "status": "done",
                "title": "Old report",
                "body": "old",
                "metadata": {},
            },
            {
                "item_id": "command:middle",
                "kind": "command",
                "agent_id": "agent-1",
                "created_at": 101.0,
                "status": "acked",
                "title": "Middle command",
                "body": "middle",
                "metadata": {},
            },
            {
                "item_id": "report:new",
                "kind": "report",
                "agent_id": "agent-1",
                "created_at": 102.0,
                "status": "working",
                "title": "New report",
                "body": "new",
                "metadata": {},
            },
        ]
        ordered = app.order_thread_for_display(thread)
        app.thread_items = {item["item_id"]: item for item in ordered}
        app.thread_order = [item["item_id"] for item in ordered]
        app.render_thread(ordered)
        table = app.query_one("#thread", DataTable)

    assert app.thread_order == ["report:new", "command:middle", "report:old"]
    assert table.get_row_at(0)[4] == "New report"
    assert table.get_row_at(1)[4] == "Middle command"
    assert table.get_row_at(2)[4] == "Old report"


async def test_tui_thread_marking_tracks_rows() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        thread = [
            {
                "item_id": "report:r1",
                "kind": "report",
                "agent_id": "agent-1",
                "created_at": 123.0,
                "status": "done",
                "title": "Report summary",
                "body": "Full report detail",
                "metadata": {},
            },
            {
                "item_id": "command:c1",
                "kind": "command",
                "agent_id": "agent-1",
                "created_at": 124.0,
                "status": "acked",
                "title": "Follow-up input",
                "body": "Proceed",
                "metadata": {},
            },
        ]
        app.thread_items = {item["item_id"]: item for item in thread}
        app.thread_order = [item["item_id"] for item in thread]
        app.render_thread(thread)
        app.toggle_current_thread_mark()
        table = app.query_one("#thread", DataTable)
        table.move_cursor(row=1, animate=False)
        app.toggle_current_thread_mark()

    assert app.marked_thread_item_ids == {"report:r1", "command:c1"}


def test_tui_thread_export_writes_markdown(tmp_path: Path) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", export_dir=tmp_path)
    app.selected_agent_id = "agent-1"
    app.thread_items = {
        "report:r1": {
            "item_id": "report:r1",
            "kind": "report",
            "agent_id": "agent-1",
            "created_at": 123.0,
            "status": "done",
            "title": "Report summary",
            "body": "Full report detail",
            "metadata": {"report_id": "r1"},
        }
    }
    app.thread_order = ["report:r1"]

    path = app.write_thread_export("all", ["report:r1"])

    assert path.parent == tmp_path
    assert path.name.endswith("-all.md")
    content = path.read_text(encoding="utf-8")
    assert "# Agent PBX Thread Export: agent-1" in content
    assert "Full report detail" in content
    assert '"report_id": "r1"' in content


async def test_tui_unseen_latest_tracking_clears_when_seen() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        app.active_agent_tab = "thread-tab"
        app.mark_latest_unseen("agent-1")
        assert app.unseen_latest_agent_ids == {"agent-1"}
        app.mark_latest_seen("agent-1")

    assert app.unseen_latest_agent_ids == set()


async def test_tui_unseen_latest_blinks_attention_bar() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.unseen_latest_agent_ids.add("agent-1")
        app.attention_blink_phase = False
        app.render_agents()
        app.render_unseen_attention()
        attention = app.query_one("#attention")
        steady_text = str(attention.renderable)
        app.attention_blink_phase = True
        app.render_unseen_attention()
        blink_text = str(attention.renderable)

    assert steady_text == "NEW unseen latest report: agent-1"
    assert blink_text == "!!! unseen latest report: agent-1"
    assert attention.has_class("unseen-active")


async def test_tui_clicking_unseen_alert_opens_first_latest() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            }
        }
        app.render_agents()
        agents = app.query_one("#agents", DataTable)
        agents.move_cursor(row=0, animate=False)
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        app.unseen_latest_agent_ids.add("agent-2")
        app.render_unseen_attention()
        click = Click(
            app.query_one("#attention"),
            0,
            0,
            0,
            0,
            1,
            False,
            False,
            False,
        )

        await app.on_click(click)
        cursor_row = agents.cursor_row
        cursor_agent_id = app.agent_id_at_cursor()
        focused = app.focused

    assert app.selected_agent_id == "agent-2"
    assert app.active_agent_tab == "latest-tab"
    assert tabs.active == "latest-tab"
    assert app.unseen_latest_agent_ids == set()
    assert cursor_row == 1
    assert cursor_agent_id == "agent-2"
    assert focused is agents
    assert loaded == ["agent-2"]
    assert threads == ["agent-2"]
    assert click._stop_propagation is True


async def test_tui_clicking_flash_alert_opens_event_agent_latest_from_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", visual_flash=True)
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        agents = app.query_one("#agents", DataTable)
        agents.move_cursor(row=0, animate=False)
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.flash_for_event(
            {
                "type": "command_acked",
                "subject_id": "cmd-1",
                "payload": {"agent_id": "agent-2"},
            }
        )
        click = Click(
            app.query_one("#attention"),
            0,
            0,
            0,
            0,
            1,
            False,
            False,
            False,
        )

        await app.on_click(click)
        cursor_row = agents.cursor_row
        cursor_agent_id = app.agent_id_at_cursor()
        focused = app.focused

    assert app.selected_agent_id == "agent-2"
    assert app.active_agent_tab == "latest-tab"
    assert tabs.active == "latest-tab"
    assert app.unseen_latest_agent_ids == set()
    assert cursor_row == 1
    assert cursor_agent_id == "agent-2"
    assert focused is agents
    assert loaded == ["agent-2"]
    assert threads == ["agent-2"]
    assert click._stop_propagation is True


async def test_tui_selected_report_event_opens_latest_from_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    refreshed_agents = 0
    refreshed_selected: list[str] = []
    worker_coros = []

    async def fake_refresh_agents() -> None:
        nonlocal refreshed_agents
        refreshed_agents += 1

    async def fake_refresh_selected_agent(agent_id: str) -> None:
        refreshed_selected.append(agent_id)

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_selected_agent = fake_refresh_selected_agent  # type: ignore[method-assign]

    async with app.run_test():
        def fake_run_worker(coro, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(coro)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        refreshed_agents = 0

        app.handle_event(
            {
                "event_id": 1,
                "type": "report_created",
                "subject_id": "report-1",
                "payload": {
                    "agent_id": "agent-1",
                    "status": "working",
                    "summary": "Working",
                },
            }
        )
        for coro in worker_coros:
            await coro

    assert app.active_agent_tab == "latest-tab"
    assert tabs.active == "latest-tab"
    assert app.unseen_latest_agent_ids == set()
    assert refreshed_agents == 1
    assert refreshed_selected == ["agent-1"]


async def test_tui_command_events_refresh_agent_queue_state() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    refreshed_agents = 0
    worker_coros = []

    async def fake_refresh_agents() -> None:
        nonlocal refreshed_agents
        refreshed_agents += 1

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]

    async with app.run_test():
        def fake_run_worker(coro, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(coro)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        refreshed_agents = 0
        app.handle_event(
            {
                "event_id": 1,
                "type": "command_queued",
                "subject_id": "cmd-1",
                "payload": {"agent_id": "agent-1"},
            }
        )
        for coro in worker_coros:
            await coro

    assert refreshed_agents == 1


async def test_tui_agent_status_refresh_marks_unseen_latest() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 100.0,
            }
        }
        app.update_unseen_from_agent_refresh({})
        app.render_agents()
        previous = app.agent_last_seen_at.copy()
        app.agents["agent-1"] = {
            "agent_id": "agent-1",
            "status": "completed",
            "project": "agent-pbx",
            "last_seen_at": 101.0,
        }
        app.update_unseen_from_agent_refresh(previous)
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.unseen_latest_agent_ids == {"agent-1"}
    assert row[0] == "NEW"


async def test_tui_agent_status_refresh_does_not_mark_engaged_latest() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.active_agent_tab = "latest-tab"
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 100.0,
            }
        }
        app.update_unseen_from_agent_refresh({})
        previous = app.agent_last_seen_at.copy()
        app.agents["agent-1"] = {
            "agent_id": "agent-1",
            "status": "completed",
            "project": "agent-pbx",
            "last_seen_at": 101.0,
        }
        app.update_unseen_from_agent_refresh(previous)
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent["agent-1"] == 101.0
    assert row[0] == ""


async def test_tui_agent_refresh_preserves_cursor_position() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        table.move_cursor(row=1, animate=False)
        app.agents["agent-1"]["status"] = "completed"
        app.render_agents()
        cursor_agent_id = app.agent_id_at_cursor()

    assert table.cursor_row == 1
    assert cursor_agent_id == "agent-2"


async def test_tui_request_detail_queues_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.request_detail()
        detail = app.query_one("#detail", TextArea).text

    assert queued == [
        (
            "agent-1",
            "request_detail",
            {"request": "Please provide the detailed response for operator review."},
        )
    ]
    assert "Detail request queued for agent-1." in detail
    assert "Command: cmd-1" in detail


async def test_tui_ping_queues_keepalive_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, object]]] = []
    threads: list[str] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, object]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.ping_agent()
        detail = app.query_one("#detail", TextArea).text

    assert queued[0][0] == "agent-1"
    assert queued[0][1] == "ping"
    assert queued[0][2]["restart_poll"] is True
    assert queued[0][2]["recommended_poll"] == {
        "wait_seconds": 25,
        "max_wait_seconds": 300,
        "interval_seconds": 5,
    }
    assert threads == ["agent-1"]
    assert "Ping queued for agent-1." in detail
    assert "Command: cmd-1" in detail


async def test_tui_follow_up_enter_sends_input() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []
    threads: list[str] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "please review"
        await message._on_key(Key("enter", None))
        await pilot.pause()

    assert queued == [
        ("agent-1", "send_input", {"message": "please review"}),
    ]
    assert threads == ["agent-1"]
    assert message.text == ""


async def test_tui_follow_up_shift_enter_inserts_newline_and_resizes() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "line 1"
        message.move_cursor((0, len("line 1")))
        await message._on_key(Key("shift+enter", None))
        message.text = "\n".join(f"line {index}" for index in range(20))
        app.resize_message_input()

    assert "line 1\n" in message.text
    assert message.styles.height.value == 15
