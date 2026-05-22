import inspect
import json
from pathlib import Path

import pytest
from rich.text import Text

from agent_pbx import tmux as tmux_support
from agent_pbx.tui import (
    AgentPBXTUI,
    CustomSlashCommand,
    PLAN_PBX_CONTEXT_PROMPT,
    TMUX_LIVENESS_IDLE_SECONDS,
    DEFAULT_SPLIT_PERCENT,
    env_custom_palette,
    env_flag,
    env_slash_commands_file,
    env_theme,
    follow_up_edit_control,
    git_diff_passthrough_command,
    is_local_server_url,
    is_follow_up_newline_key,
    load_custom_slash_commands,
    parse_custom_slash_commands,
    render_custom_slash_prompt,
    resolve_layout,
    tmux_features_available,
)
from textual.events import Click, Key
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea


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
        "AGENT_PBX_TUI_LAYOUT",
        "AGENT_PBX_TUI_SPLIT_PERCENT",
        "AGENT_PBX_TUI_TMUX",
        "AGENT_PBX_TUI_TMUX_SHOW",
        "AGENT_PBX_TUI_TMUX_CAPTURE_LINES",
        "AGENT_PBX_TUI_TMUX_REFRESH_SECONDS",
        "AGENT_PBX_TUI_COMMANDS_FILE",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1,0")
    monkeypatch.setattr("agent_pbx.tui.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv(
        "AGENT_PBX_TUI_SETTINGS_FILE",
        str(tmp_path / "agent-pbx" / "tui-settings.json"),
    )


def test_tui_constructs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")

    assert app.TITLE == "Agent PBX"
    assert ("s", "settings", "Settings") in app.BINDINGS
    assert ("ctrl+t", "toggle_tmux_direct", "Tmux") in app.BINDINGS
    assert any(
        getattr(binding, "key", None) == "d"
        and getattr(binding, "action", None) == "hide_agent"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+d"
        and getattr(binding, "action", None) == "purge_agent"
        for binding in app.BINDINGS
    )
    assert not any(
        str(getattr(binding, "key", "")).startswith("alt+")
        and "jump_agent" in str(getattr(binding, "action", ""))
        for binding in app.BINDINGS
    )
    assert app.server == "http://127.0.0.1:8765"
    assert app.token == "test"
    assert app.visual_flash_enabled is False
    assert app.terminal_bell_enabled is False
    assert app.agent_blink_enabled is True
    assert app.tmux_direct_enabled is False
    assert app.tmux_capture_lines == 0
    assert app.tmux_refresh_seconds == 1.5
    assert app.tmux_agent_targets == {}
    assert app.ui_theme == "cyberpunk"
    assert app.layout_mode == "adaptive"
    assert app.effective_layout_mode == "compact"
    assert app.split_percent == DEFAULT_SPLIT_PERCENT
    assert app.compact_view == "home"
    assert ("b", "back", "Back") in app.BINDINGS


def test_tui_reads_notification_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_BELL", "true")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.visual_flash_enabled is True
    assert app.terminal_bell_enabled is True


def test_tui_detects_tmux_features(monkeypatch) -> None:
    monkeypatch.setattr("agent_pbx.tui.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("AGENT_PBX_TUI_TMUX_SHOW", raising=False)

    assert tmux_features_available(tmux_direct_enabled=False) is False
    assert tmux_features_available(tmux_direct_enabled=True) is True
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_SHOW", "1")
    assert tmux_features_available(tmux_direct_enabled=False) is True
    monkeypatch.setattr("agent_pbx.tui.shutil.which", lambda name: None)
    assert tmux_features_available(tmux_direct_enabled=True) is False
    assert is_local_server_url("http://127.0.0.1:8767") is True
    assert is_local_server_url("http://192.168.1.20:8767") is False


def test_tui_reads_saved_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "visual_flash": True,
                "terminal_bell": True,
                "agent_blink": False,
                "theme": "1337",
                "layout": "compact",
                "split_percent": 61,
                "tmux_direct": True,
                "tmux_capture_lines": 250,
                "tmux_agent_targets": {"agent-1": "%1"},
                "export_dir": str(tmp_path / "exports"),
                "latest_viewed_at_by_agent": {"agent-1": 123.0},
                "last_seen_event_id": 42,
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
    assert app.layout_mode == "compact"
    assert app.split_percent == 61
    assert app.tmux_direct_enabled is True
    assert app.tmux_capture_lines == 250
    assert app.tmux_agent_targets == {"agent-1": "%1"}
    assert app.export_dir == tmp_path / "exports"
    assert app.latest_viewed_at_by_agent == {"agent-1": 123.0}
    assert app.last_seen_event_id == 42


def test_tui_env_overrides_saved_settings(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "visual_flash": True,
                "agent_blink": False,
                "tmux_direct": False,
                "theme": "1337",
                "layout": "split",
                "split_percent": 35,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_FLASH", "0")
    monkeypatch.setenv("AGENT_PBX_TUI_AGENT_BLINK", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_CAPTURE_LINES", "750")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_REFRESH_SECONDS", "2.75")
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "cyberpunk")
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "compact")
    monkeypatch.setenv("AGENT_PBX_TUI_SPLIT_PERCENT", "72")

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    assert app.visual_flash_enabled is False
    assert app.agent_blink_enabled is True
    assert app.tmux_direct_enabled is True
    assert app.tmux_capture_lines == 750
    assert app.tmux_refresh_seconds == 2.75
    assert app.ui_theme == "cyberpunk"
    assert app.layout_mode == "compact"
    assert app.split_percent == 72


def test_tui_env_allows_visible_tmux_capture(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_CAPTURE_LINES", "0")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.tmux_capture_lines == 0


def test_tui_saves_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    app.visual_flash_enabled = True
    app.terminal_bell_enabled = True
    app.agent_blink_enabled = False
    app.tmux_direct_enabled = True
    app.tmux_capture_lines = 333
    app.tmux_agent_targets = {"agent-1": "%2"}
    app.layout_mode = "compact"
    app.split_percent = 57
    app.latest_viewed_at_by_agent = {"agent-1": 123.0}
    app.last_seen_event_id = 42
    app.set_ui_theme("1337")

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["visual_flash"] is True
    assert saved["terminal_bell"] is True
    assert saved["agent_blink"] is False
    assert saved["tmux_direct"] is True
    assert saved["tmux_capture_lines"] == 333
    assert saved["tmux_agent_targets"] == {"agent-1": "%2"}
    assert saved["theme"] == "1337"
    assert saved["layout"] == "compact"
    assert saved["split_percent"] == 57
    assert saved["latest_viewed_at_by_agent"] == {"agent-1": 123.0}
    assert saved["last_seen_event_id"] == 42


def test_tui_reads_theme_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "1337")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.ui_theme == "1337"
    assert app.theme == "1337"


def test_tui_reads_minimal_theme_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "black-white")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.ui_theme == "minimal"
    assert app.theme == "minimal"


def test_tui_reads_layout_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "mobile")

    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.layout_mode == "compact"
    assert resolve_layout("single-pane") == "compact"
    assert resolve_layout("split") == "split"
    assert resolve_layout("auto") == "adaptive"
    assert resolve_layout("pocketchip") == "tiny"


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


def test_tui_flash_timer_tolerates_unmounted_attention() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.flash_generation = 1

    app.flash_for_event({"type": "report_created", "subject_id": "report-1"})
    app.clear_flash(1)


def test_tui_space_key_tolerates_unmounted_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    event = Key("space", " ")

    app.on_key(event)

    assert event._stop_propagation is False


def test_tui_background_render_skips_before_widgets_mount() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {"agent-1": {"agent_id": "agent-1"}}
    app.unseen_latest_agent_ids.add("agent-1")

    app.render_agents()
    app.focus_agent_row("agent-1")
    app.render_unseen_attention()

    assert app.agent_id_at_cursor() is None


async def test_tui_load_latest_report_skips_before_detail_mount() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    await app.load_latest_report("agent-1")


async def test_tui_mounts_latest_composer_and_settings_controls() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", visual_flash=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        agents = app.query_one("#agents", DataTable)
        thread = app.query_one("#thread", DataTable)
        hide_agent = app.query_one("#hide-agent", Button)
        purge_agent = app.query_one("#purge-agent", Button)
        request_detail = app.query_one("#request-detail", Button)
        ping = app.query_one("#ping-agent", Button)
        mark_canceled = app.query_one("#mark-canceled", Button)
        export_item = app.query_one("#export-item", Button)
        export_marked = app.query_one("#export-marked", Button)
        export_all = app.query_one("#export-all", Button)
        delete_queued = app.query_one("#delete-queued", Button)
        latest_send_plan = app.query_one("#latest-send-plan-choice", Button)
        latest_send_plan_tmux = app.query_one("#latest-send-plan-tmux", Button)
        send_plan = app.query_one("#send-plan-choice", Button)
        send_plan_tmux = app.query_one("#send-plan-tmux", Button)
        workerbee_detail = app.query_one("#workerbee-detail", TextArea)
        workerbee_refresh = app.query_one("#workerbee-refresh", Button)
        composer = app.query_one("#composer")
        agent_id = app.query_one("#agent-id", Input)
        message = app.query_one("#message", TextArea)
        actions = app.query_one("#composer-actions")
        hotkeys = app.query_one("#composer-hotkeys")
        buttons = app.query_one("#composer-buttons")

        assert agents.cursor_type == "row"
        assert agents.show_row_labels is False
        assert thread.cursor_type == "row"
        assert thread.show_row_labels is False
        assert hide_agent.label.plain == "Hide Agent (d)"
        assert purge_agent.label.plain == "Purge Agent (D)"
        assert request_detail.label.plain == "Request Detail"
        assert ping.label.plain == "Ping"
        assert mark_canceled.label.plain == "Mark Canceled"
        assert export_item.label.plain == "Export Item"
        assert export_marked.label.plain == "Export Marked"
        assert export_all.label.plain == "Export All"
        assert delete_queued.label.plain == "Delete Queued"
        assert latest_send_plan.label.plain == "Send Plan Choice"
        assert latest_send_plan_tmux.label.plain == "Send to Codex Pane"
        assert latest_send_plan.disabled is True
        assert latest_send_plan_tmux.disabled is True
        assert send_plan.label.plain == "Send Plan Choice"
        assert send_plan_tmux.label.plain == "Send to Codex Pane"
        assert send_plan.disabled is True
        assert send_plan_tmux.disabled is True
        assert workerbee_detail.read_only is True
        assert workerbee_refresh.label.plain == "Refresh WorkerBee"
        assert "#thread {\n        height: 7;" in app.CSS
        assert "#thread-detail {\n        height: 1fr;" in app.CSS
        assert "#latest-plan-choice-panel,\n    #plan-choice-panel {" in app.CSS
        assert "#latest-plan-notes,\n    #plan-notes {" in app.CSS
        assert "#workerbee-detail {\n        height: 1fr;" in app.CSS
        assert "#tmux-message {\n        height: 8;" in app.CSS
        assert "Notification Options" not in app.CSS
        assert message.soft_wrap is True
        assert "scrollbar-size: 0 1;" in app.CSS
        assert "#composer-button-inset {\n        width: 1;" in app.CSS
        assert "#composer-hotkeys {\n        height: 1;" in app.CSS
        assert "#composer-buttons Button {\n        width: 1fr;" in app.CSS
        assert "padding-top: 1;" in app.CSS
        assert composer.parent is app.query_one("#latest-tab")
        assert agent_id.region.height >= 8
        assert message.region.height >= 8
        assert composer.region.height >= 15
        assert message.region.bottom <= composer.region.bottom
        assert actions.region.y >= message.region.bottom
        assert buttons.region.x == message.region.x + 1
        assert buttons.region.y > message.region.bottom
        assert buttons.region.right <= message.region.right
        assert hotkeys.region.y >= buttons.region.bottom
        assert hotkeys.region.right <= composer.region.right
        hotkey_text = str(hotkeys.renderable)
        assert "Ctrl+J newline" in hotkey_text
        assert "Ctrl+W word" in hotkey_text
        assert "Ctrl+T tmux" in hotkey_text
        assert "Ctrl+A/E" not in hotkey_text
        assert "Ctrl+U" not in hotkey_text
        assert "start/end" not in hotkey_text
        assert "before/after" not in hotkey_text
        assert "Ctrl+D" not in hotkey_text
        assert "Ctrl+U/K" not in hotkey_text
        assert "Ctrl+B/F" not in hotkey_text
        assert "Ctrl+P/N" not in hotkey_text
        assert buttons.region.bottom <= composer.region.bottom
        assert ping.region.right <= message.region.right

        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        await pilot.pause()
        assert composer.region.height == 0
        tabs.active = "workerbee-tab"
        await pilot.pause()
        assert composer.region.height == 0
        tabs.active = "latest-tab"
        await pilot.pause()
        assert composer.region.height >= 15

        await pilot.press("s")
        await pilot.pause()

        visual = app.screen.query_one("#visual-flash", Checkbox)
        bell = app.screen.query_one("#terminal-bell", Checkbox)
        agent_blink = app.screen.query_one("#agent-blink", Checkbox)
        layout_mode = app.screen.query_one("#layout-mode", Select)
        split_label = app.screen.query_one("#split-percent-label", Static)
        split_narrow = app.screen.query_one("#split-narrow", Button)
        split_reset = app.screen.query_one("#split-reset", Button)
        split_widen = app.screen.query_one("#split-widen", Button)
        tmux_direct = app.screen.query_one("#tmux-direct", Checkbox)
        theme = app.screen.query_one("#theme-mode", Select)
        close = app.screen.query_one("#settings-close", Button)

        assert visual.value is True
        assert bell.value is False
        assert agent_blink.value is True
        assert layout_mode.value == "adaptive"
        assert str(split_label.renderable) == "Agents width: 42%"
        assert split_narrow.label.plain == "Narrow"
        assert split_reset.label.plain == "Reset"
        assert split_widen.label.plain == "Widen"
        assert tmux_direct.value is False
        assert theme.value == "cyberpunk"
        assert close.label.plain == "Close"


async def test_tui_disables_tmux_controls_when_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("agent_pbx.tui.shutil.which", lambda name: None)
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        hotkeys = app.query_one("#composer-hotkeys")
        app.action_settings()
        await pilot.pause()
        tmux_direct = app.screen.query_one("#tmux-direct", Checkbox)

        assert app.tmux_features_available is False
        assert tmux_direct.disabled is True
        assert "Ctrl+T tmux" not in str(hotkeys.renderable)


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

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
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


async def test_tui_tmux_direct_replaces_latest_and_sends_exact_input(
    monkeypatch,
) -> None:
    pane = tmux_support.TmuxPane(
        "agent-pbx",
        "0",
        "2",
        "%76",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        142,
        45,
        500,
    )
    sent: list[tuple[str, str]] = []
    threads: list[str] = []

    monkeypatch.setattr("agent_pbx.tui.tmux_support.list_panes", lambda: [pane])
    monkeypatch.setattr(
        "agent_pbx.tui.tmux_support.capture_pane",
        lambda target, *, lines=0: f"{target} captured {lines}",
    )
    monkeypatch.setattr(
        "agent_pbx.tui.tmux_support.send_text",
        lambda target, text: sent.append((target, text)),
    )

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        tmux_direct=True,
        tmux_capture_lines=25,
    )

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "status": "working",
                "last_seen_at": 123.0,
                "metadata": {"cwd": "/home/me/agent-pbx"},
            }
        }
        await app.select_agent("agent-1")
        await pilot.pause()

        assert app.screen.has_class("tmux-direct")
        assert app.query_one("#detail", TextArea).region.height == 0
        assert app.query_one("#composer").region.height == 0
        assert app.query_one("#tmux-panel").region.height > 0
        assert app.query_one("#tmux-message", TextArea).region.height >= 8
        assert "%76" in str(app.query_one("#tmux-status").renderable)
        assert app.query_one("#tmux-stream", TextArea).text == "%76 captured 25"
        assert threads == ["agent-1"]

        app.query_one("#tmux-message", TextArea).text = "/status"
        await app.send_input()
        await pilot.pause()

    assert sent == [("%76", "/status")]


async def test_tui_tiny_tmux_direct_keeps_stream_and_input_visible(
    monkeypatch,
) -> None:
    pane = tmux_support.TmuxPane(
        "agent-pbx",
        "0",
        "2",
        "%76",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        53,
        20,
        500,
    )

    monkeypatch.setattr("agent_pbx.tui.tmux_support.list_panes", lambda: [pane])
    monkeypatch.setattr(
        "agent_pbx.tui.tmux_support.capture_pane",
        lambda target, *, lines=0: "\n".join(f"line {index}" for index in range(20)),
    )

    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "status": "working",
                "last_seen_at": 123.0,
                "metadata": {"cwd": "/home/me/agent-pbx"},
            }
        }
        await app.select_agent("agent-1")
        await pilot.pause()

        stream = app.query_one("#tmux-stream", TextArea)
        message = app.query_one("#tmux-message", TextArea)
        hotkeys = app.query_one("#tmux-hotkeys", Static)

        assert app.screen.has_class("tiny-agent")
        assert app.screen.has_class("tmux-direct")
        assert app.query_one("#agent-title").region.height == 0
        assert app.query_one("#tmux-actions").region.height == 0
        assert stream.region.height >= 5
        assert 4 <= message.region.height <= 5
        assert message.region.bottom <= app.size.height
        assert hotkeys.region.bottom <= app.size.height
        assert "C-J nl" in str(hotkeys.renderable)
        assert app.focused is message


async def test_tui_tmux_update_skips_unchanged_capture() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        stream = app.query_one("#tmux-stream", TextArea)

        first = app.update_tmux_stream(stream, "same", cache_key="agent:%1")
        second = app.update_tmux_stream(stream, "same", cache_key="agent:%1")

    assert first is True
    assert second is False


async def test_tui_tmux_prepare_clears_stale_visible_stream() -> None:
    pane = tmux_support.TmuxPane(
        "agent-pbx",
        "0",
        "2",
        "%76",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        142,
        45,
        500,
    )
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        stream = app.query_one("#tmux-stream", TextArea)
        stream.text = "old agent capture"

        changed = app.prepare_tmux_stream_for_capture(
            stream,
            cache_key="agent-1:%76",
            pane=pane,
        )
        unchanged = app.prepare_tmux_stream_for_capture(
            stream,
            cache_key="agent-1:%76",
            pane=pane,
        )

    assert changed is True
    assert unchanged is False
    assert stream.text == "Loading tmux pane %76 (agent-pbx:0.2)..."


def test_tui_tmux_liveness_formats_activity(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)

    app.record_tmux_capture_liveness("agent-1", "%1", "first capture")
    assert app.format_tmux_liveness("agent-1") == "active"
    assert app.tmux_liveness_level("agent-1") == "active"

    monkeypatch.setattr(
        "agent_pbx.tui.time.time",
        lambda: 1000.0 + TMUX_LIVENESS_IDLE_SECONDS + 1,
    )
    assert app.format_tmux_liveness("agent-1") == "idle 1m"
    assert app.tmux_liveness_level("agent-1") == "idle"

    app.record_tmux_liveness_state("agent-1", "stale", pane_id="%1")
    assert app.format_tmux_liveness("agent-1") == "stale"
    assert app.tmux_liveness_level("agent-1") == "stale"

    app.record_tmux_liveness_state("agent-1", "auto")
    assert app.format_tmux_liveness("agent-1") == "-"
    assert app.tmux_liveness_level("agent-1") == "unknown"


def test_tui_styles_tmux_active_rows_without_changing_status(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_capture_liveness("agent-1", "%1", "changed")

    cells = app.style_agent_row(
        ["", "agent-1", "report", "", "done", "demo", "1000", "", "active", "-", "-"],
        {
            "agent_id": "agent-1",
            "status": "done",
            "queued_command_count": 0,
            "last_poll_at": None,
        },
    )

    assert all(isinstance(cell, Text) for cell in cells)
    assert {cell.style for cell in cells if isinstance(cell, Text)} == {"bold cyan"}
    assert app.format_agent_status({"status": "done"}) == "done"


def test_tui_infers_tmux_working_status_for_active_terminal_report(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_capture_liveness("agent-1", "%1", "changed")

    assert (
        app.format_agent_status({"agent_id": "agent-1", "status": "completed"})
        == "tmux-working"
    )
    assert (
        app.format_agent_status(
            {"agent_id": "agent-1", "effective_status": "done", "status": "completed"}
        )
        == "tmux-working"
    )


def test_tui_keeps_reported_status_for_idle_or_nonterminal_tmux(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_capture_liveness("agent-1", "%1", "changed")

    assert app.format_agent_status({"agent_id": "agent-1", "status": "working"}) == "working"

    monkeypatch.setattr(
        "agent_pbx.tui.time.time",
        lambda: 1000.0 + TMUX_LIVENESS_IDLE_SECONDS + 1,
    )

    assert (
        app.format_agent_status({"agent_id": "agent-1", "status": "completed"})
        == "completed"
    )


def test_tui_styles_tmux_idle_rows(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_capture_liveness("agent-1", "%1", "unchanged")
    monkeypatch.setattr(
        "agent_pbx.tui.time.time",
        lambda: 1000.0 + TMUX_LIVENESS_IDLE_SECONDS + 1,
    )

    cells = app.style_agent_row(
        ["", "agent-1", "report", "", "done", "demo", "1000", "", "idle 1m", "-", "-"],
        {
            "agent_id": "agent-1",
            "status": "done",
            "queued_command_count": 0,
            "last_poll_at": None,
        },
    )

    assert all(isinstance(cell, Text) for cell in cells)
    assert {cell.style for cell in cells if isinstance(cell, Text)} == {"dim"}


def test_tui_styles_tmux_stale_rows(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_liveness_state("agent-1", "stale", pane_id="%1")

    cells = app.style_agent_row(
        ["", "agent-1", "report", "", "done", "demo", "1000", "", "stale", "-", "-"],
        {
            "agent_id": "agent-1",
            "status": "done",
            "queued_command_count": 0,
            "last_poll_at": None,
        },
    )

    assert all(isinstance(cell, Text) for cell in cells)
    assert {cell.style for cell in cells if isinstance(cell, Text)} == {"bold yellow"}


def test_tui_pbx_never_overrides_tmux_active_rows(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)
    app.record_tmux_capture_liveness("agent-1", "%1", "changed")

    cells = app.style_agent_row(
        ["", "agent-1", "nohup", "", "working", "demo", "1000", "1", "active", "never", "-"],
        {
            "agent_id": "agent-1",
            "status": "working",
            "queued_command_count": 1,
            "last_poll_at": None,
            "metadata": {"pbx_mode": "nohup"},
        },
    )

    assert all(isinstance(cell, Text) for cell in cells)
    assert {cell.style for cell in cells if isinstance(cell, Text)} == {"bold red"}


def test_tui_tmux_crop_hides_codex_status_and_input_region() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    displayed = app.crop_tmux_capture_for_display(
        "done line\nnew output\nWorking 12s\n> buffered input\ncontext left"
    )
    assert displayed == "done line\nnew output"

    assert app.crop_tmux_capture_for_display("one\ntwo") == "one\ntwo"


async def test_tui_tmux_toggle_hotkey_only_from_latest() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    captures: list[str] = []
    reports: list[str] = []

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_load_latest_report(agent_id: str) -> None:
        reports.append(agent_id)

    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-1"
        app.active_agent_tab = "thread-tab"
        await app.action_toggle_tmux_direct()
        assert app.tmux_direct_enabled is False

        app.active_agent_tab = "latest-tab"
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.tmux_direct_enabled is True
        assert app.screen.has_class("tmux-direct")

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.tmux_direct_enabled is False

    assert captures == ["agent-1"]
    assert reports == ["agent-1"]


def test_tui_tmux_resolve_uses_manual_override_and_detach() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "project": "agent-pbx",
            "metadata": {"cwd": "/home/me/agent-pbx"},
        }
    }
    auto_pane = tmux_support.TmuxPane(
        "s",
        "0",
        "1",
        "%1",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        80,
        24,
        100,
    )
    manual_pane = tmux_support.TmuxPane(
        "s",
        "0",
        "2",
        "%2",
        False,
        "zsh",
        "shell",
        "/home/me",
        80,
        24,
        100,
    )

    app.tmux_agent_targets = {"agent-1": "%2"}
    pane, mode = app.resolve_tmux_pane("agent-1", [auto_pane, manual_pane])
    assert pane is None
    assert mode == "stale"

    app.tmux_manual_override_agent_ids.add("agent-1")
    pane, mode = app.resolve_tmux_pane("agent-1", [auto_pane, manual_pane])
    assert pane == manual_pane
    assert mode == "manual"

    app.tmux_detached_agent_ids.add("agent-1")
    pane, mode = app.resolve_tmux_pane("agent-1", [auto_pane, manual_pane])
    assert pane is None
    assert mode == "detached"


async def test_tui_compact_layout_opens_agent_view_and_back(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "compact")
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()

        assert app.screen.has_class("compact-home")
        assert app.query_one("#left").region.width >= 58

        tabs = app.query_one("#agent-tabs")
        tabs.active = "workerbee-tab"
        app.active_agent_tab = "workerbee-tab"
        await app.select_agent("agent-1")
        await pilot.pause()

        assert app.compact_view == "agent"
        assert app.screen.has_class("compact-agent")
        assert tabs.active == "latest-tab"
        assert app.active_agent_tab == "latest-tab"
        assert str(app.query_one("#agent-title").renderable) == "Agent: agent-1"
        assert app.query_one("#right").region.width >= 58
        message = app.query_one("#message", TextArea)
        buttons = app.query_one("#composer-buttons")
        assert buttons.region.x == message.region.x + 1
        assert buttons.region.y > message.region.bottom
        assert app.query_one("#mark-canceled", Button).region.right <= message.region.right

        app.action_back()
        await pilot.pause()

        assert app.compact_view == "home"
        assert app.screen.has_class("compact-home")

    assert loaded == ["agent-1"]
    assert threads == ["agent-1"]


async def test_tui_adaptive_layout_breakpoints_split_keys_and_tiny_events() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        assert app.layout_mode == "adaptive"
        assert app.effective_layout_mode == "split"
        assert app.screen.has_class("split-layout")

        await pilot.press("]")
        await pilot.pause()
        assert app.split_percent == DEFAULT_SPLIT_PERCENT + 5
        await pilot.press("[")
        await pilot.press("0")
        await pilot.pause()
        assert app.split_percent == DEFAULT_SPLIT_PERCENT

        app.query_one("#message", TextArea).focus()
        await pilot.press("]")
        await pilot.pause()
        assert app.split_percent == DEFAULT_SPLIT_PERCENT

        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert app.effective_layout_mode == "compact"
        assert app.screen.has_class("compact-home")

        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        assert app.effective_layout_mode == "tiny"
        assert app.screen.has_class("tiny-home")
        assert app.query_one("#events", DataTable).styles.display == "none"

        await pilot.press("e")
        await pilot.pause()
        assert app.tiny_show_events is True
        assert app.screen.has_class("tiny-events")
        assert app.query_one("#agents", DataTable).styles.display == "none"
        assert app.query_one("#events", DataTable).styles.display == "block"


async def test_tui_tiny_layout_opens_agent_view(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "tiny")
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()

        assert app.screen.has_class("tiny-home")
        await app.select_agent("agent-1")
        await pilot.pause()

        assert app.compact_view == "agent"
        assert app.screen.has_class("tiny-agent")
        assert app.query_one("#agent-id", Input).region.height == 0
        assert app.query_one("#send", Button).label.plain == "Send"
        assert app.query_one("#request-detail", Button).label.plain == "Detail"
        assert app.query_one("#mark-canceled", Button).label.plain == "Cancel"

    assert loaded == ["agent-1"]
    assert threads == ["agent-1"]


async def test_tui_compact_alert_opens_agent_latest(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "compact")
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
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
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

    assert app.selected_agent_id == "agent-2"
    assert app.compact_view == "agent"
    assert app.active_agent_tab == "latest-tab"
    assert app.unseen_latest_agent_ids == set()
    assert loaded == ["agent-2"]
    assert threads == ["agent-2"]
    assert click._stop_propagation is True


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

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
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

    app.set_ui_theme("minimal")
    assert app.ui_theme == "minimal"
    assert app.theme == "minimal"

    app.set_ui_theme("cyberpunk")
    assert app.ui_theme == "cyberpunk"
    assert app.theme == "cyberpunk"


async def test_tui_palette_includes_operator_commands() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.pause()
        titles = {command.title for command in app.get_system_commands(app.screen)}

    assert "/detail" in titles
    assert "/ping" in titles
    assert "/tmux" in titles
    assert "/workerbee" in titles
    assert "/plan" in titles
    assert "/plan latest" in titles
    assert "/plan thread" in titles
    assert "/theme minimal" in titles
    assert "/layout compact" in titles
    assert "/gitstatus" not in titles
    assert "/gitdiff" not in titles
    assert "/gitstageandcommit" not in titles


def test_tui_custom_slash_commands_env_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_PBX_TUI_COMMANDS_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert (
        env_slash_commands_file()
        == tmp_path / "config" / "agent-pbx" / "slash-commands.json"
    )

    monkeypatch.setenv("AGENT_PBX_TUI_COMMANDS_FILE", "~/commands.json")
    assert env_slash_commands_file() == Path("~/commands.json").expanduser()


def test_tui_custom_slash_commands_parse_and_validate() -> None:
    data = {
        "commands": [
            {
                "name": "/review",
                "description": "Review changes",
                "prompt": "review the current changes",
            },
            {
                "name": "/testfile",
                "prompt": "run focused tests for {arg}",
                "arg_label": "Target",
                "arg_placeholder": "tests/test_tui.py",
                "arg_required": True,
            },
            {"name": "bad", "prompt": "ignored"},
            {"name": "/detail", "prompt": "ignored duplicate"},
            {"name": "/badplaceholder", "prompt": "run {file}"},
        ]
    }

    commands, errors = parse_custom_slash_commands(
        data,
        built_in_names={"/detail"},
    )

    assert [command.name for command in commands] == ["/review", "/testfile"]
    assert commands[1].uses_arg is True
    assert commands[1].arg_required is True
    assert any("must start with '/'" in error for error in errors)
    assert any("duplicates" in error for error in errors)
    assert any("{file}" in error for error in errors)


def test_tui_load_custom_slash_commands_from_file(tmp_path: Path) -> None:
    path = tmp_path / "slash-commands.json"
    path.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "name": "/review",
                        "description": "Review changes",
                        "prompt": "review the current changes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    commands, errors = load_custom_slash_commands(path)

    assert errors == []
    assert commands == [
        CustomSlashCommand(
            name="/review",
            description="Review changes",
            prompt="review the current changes",
        )
    ]


def test_tui_render_custom_slash_prompt() -> None:
    command = CustomSlashCommand(
        name="/testfile",
        description="Run tests",
        prompt="run focused tests for {arg}",
    )

    assert render_custom_slash_prompt(command, " tests/test_tui.py ") == (
        "run focused tests for tests/test_tui.py"
    )


async def test_tui_palette_custom_commands_require_tmux_mode(
    monkeypatch, tmp_path: Path
) -> None:
    commands_file = tmp_path / "slash-commands.json"
    commands_file.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "name": "/review",
                        "description": "Review changes",
                        "prompt": "review the current changes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_COMMANDS_FILE", str(commands_file))

    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    tmux_app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.pause()
        titles = {command.title for command in app.get_system_commands(app.screen)}
    async with tmux_app.run_test() as pilot:
        await pilot.pause()
        tmux_titles = {
            command.title for command in tmux_app.get_system_commands(tmux_app.screen)
        }

    assert "/commands reload" in titles
    assert "/review" not in titles
    assert "/review" in tmux_titles


async def test_tui_palette_includes_git_commands_only_in_tmux_mode() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.pause()
        titles = {command.title for command in app.get_system_commands(app.screen)}

    assert "/gitstatus" in titles
    assert "/gitdiff" in titles
    assert "/gitstageandcommit" in titles


async def test_tui_palette_custom_static_command_sends_to_tmux(
    monkeypatch, tmp_path: Path
) -> None:
    commands_file = tmp_path / "slash-commands.json"
    commands_file.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "name": "/review",
                        "description": "Review changes",
                        "prompt": "review the current changes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_COMMANDS_FILE", str(commands_file))
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.palette_custom_slash_command(app.custom_slash_commands[0])
        await pilot.pause()

    assert sent == [("agent-1", "review the current changes")]


async def test_tui_palette_custom_arg_command_opens_modal_and_sends(
    monkeypatch, tmp_path: Path
) -> None:
    commands_file = tmp_path / "slash-commands.json"
    commands_file.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "name": "/testfile",
                        "description": "Run focused tests",
                        "prompt": "run focused tests for {arg}",
                        "arg_label": "Target",
                        "arg_placeholder": "tests/test_tui.py",
                        "arg_required": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_COMMANDS_FILE", str(commands_file))
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.palette_custom_slash_command(app.custom_slash_commands[0])
        await pilot.pause()
        target = app.screen.query_one("#palette-custom-command-arg", Input)
        target.value = "tests/test_tui.py"
        app.screen.submit()  # type: ignore[attr-defined]
        await pilot.pause()

    assert sent == [("agent-1", "run focused tests for tests/test_tui.py")]


async def test_tui_palette_reload_custom_commands(
    monkeypatch, tmp_path: Path
) -> None:
    commands_file = tmp_path / "slash-commands.json"
    commands_file.write_text(
        json.dumps({"commands": [{"name": "/one", "prompt": "one"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_PBX_TUI_COMMANDS_FILE", str(commands_file))
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert [command.name for command in app.custom_slash_commands] == ["/one"]
        commands_file.write_text(
            json.dumps({"commands": [{"name": "/two", "prompt": "two"}]}),
            encoding="utf-8",
        )
        app.palette_reload_custom_slash_commands()
        await pilot.pause()

    assert [command.name for command in app.custom_slash_commands] == ["/two"]


async def test_tui_palette_agent_commands_use_selected_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    async def fake_request_detail() -> None:
        calls.append(app.query_one("#agent-id", Input).value)

    app.request_detail = fake_request_detail  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.palette_request_detail()
        await pilot.pause()

    assert calls == ["agent-1"]


def test_tui_palette_theme_and_layout_commands() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    app.palette_set_theme("minimal")
    app.palette_set_layout("tiny")

    assert app.ui_theme == "minimal"
    assert app.layout_mode == "tiny"


async def test_tui_palette_gitstatus_sends_passthrough_to_tmux() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.palette_git_status()
        await pilot.pause()

    assert sent == [("agent-1", "!git status")]
    assert captures == ["agent-1"]


async def test_tui_palette_git_stage_and_commit_sends_prompt_to_tmux() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.palette_git_stage_and_commit()
        await pilot.pause()

    assert sent == [("agent-1", "stage and commit the changes")]


async def test_tui_palette_gitdiff_modal_sends_optional_target() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.palette_git_diff()
        await pilot.pause()
        target = app.screen.query_one("#palette-gitdiff-target", Input)
        target.value = "main:README.md"
        app.screen.submit()  # type: ignore[attr-defined]
        await pilot.pause()

    assert sent == [("agent-1", "!git diff main:README.md")]
    assert captures == ["agent-1"]


def test_tui_gitdiff_passthrough_command_formats_targets() -> None:
    assert git_diff_passthrough_command("") == "!git diff"
    assert git_diff_passthrough_command("README.md") == "!git diff -- README.md"
    assert git_diff_passthrough_command("main:README.md") == "!git diff main:README.md"
    assert (
        git_diff_passthrough_command("docs/My File.md")
        == "!git diff -- 'docs/My File.md'"
    )


async def test_tui_palette_plan_latest_modal_sends_choice_with_notes() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []
    refreshed_events = 0
    loaded_threads: list[str] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        nonlocal refreshed_events
        refreshed_events += 1

    async def fake_load_thread(agent_id: str) -> None:
        loaded_threads.append(agent_id)

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        refreshed_events = 0
        loaded_threads = []
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.latest_report_by_agent = {
            "agent-1": {
                "report_id": "report-1",
                "plan_options": ["A", "B"],
            }
        }
        app.selected_latest_plan_option_index = 1

        app.palette_plan_latest()
        await pilot.pause()

        assert app.screen.query_one("#palette-plan-title", Static).renderable == "Plan Choice: agent-1"
        option = app.screen.query_one("#palette-plan-option", Select)
        notes = app.screen.query_one("#palette-plan-notes", TextArea)
        send_tmux = app.screen.query_one("#palette-plan-send-tmux", Button)
        assert option.value == "1"
        assert send_tmux.disabled is True

        notes.text = "Prefer B."
        app.screen.submit(via_tmux=False)  # type: ignore[attr-defined]
        await pilot.pause()

    assert queued == [
        (
            "agent-1",
            "send_input",
            {"message": "Selected plan option: B\n\nOperator notes:\nPrefer B."},
        )
    ]
    assert refreshed_events == 1
    assert loaded_threads == ["agent-1"]


async def test_tui_palette_plan_primes_next_follow_up_prompt() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.palette_prime_plan_prompt()
        assert app.pending_slash_command_by_agent == {"agent-1": "/plan"}

        app.query_one("#message", TextArea).text = "Draft a path forward."
        await app.send_input()

    assert queued == [
        (
            "agent-1",
            "send_input",
            {"message": PLAN_PBX_CONTEXT_PROMPT},
        ),
        (
            "agent-1",
            "send_input",
            {"message": "/plan"},
        ),
        (
            "agent-1",
            "send_input",
            {"message": "Draft a path forward."},
        )
    ]
    assert app.pending_slash_command_by_agent == {}


async def test_tui_pending_plan_does_not_duplicate_manual_slash_prompt() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#agent-id", Input).value = "agent-1"
        app.pending_slash_command_by_agent["agent-1"] = "/plan"
        app.query_one("#message", TextArea).text = "/status"
        await app.send_input()

    assert queued == [("agent-1", "send_input", {"message": "/status"})]
    assert app.pending_slash_command_by_agent == {}


async def test_tui_palette_plan_primes_next_tmux_prompt() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.pending_slash_command_by_agent["agent-1"] = "/plan"
        app.query_one("#tmux-message", TextArea).text = "Investigate options."
        await app.send_input()

    assert sent == [
        ("agent-1", PLAN_PBX_CONTEXT_PROMPT),
        ("agent-1", "/plan"),
        ("agent-1", "Investigate options."),
    ]
    assert app.pending_slash_command_by_agent == {}


async def test_tui_palette_plan_thread_modal_can_send_to_tmux() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captured: list[str] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captured.append(agent_id)

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.thread_items = {
            "report:r1": {
                "item_id": "report:r1",
                "kind": "report",
                "metadata": {"plan_options": ["Proceed"]},
            }
        }
        app.selected_thread_item_id = "report:r1"

        app.palette_plan_thread()
        await pilot.pause()

        assert app.screen.query_one("#palette-plan-source", Static).renderable == "Selected thread item"
        assert app.screen.query_one("#palette-plan-send-tmux", Button).disabled is False
        app.screen.query_one("#palette-plan-notes", TextArea).text = "Go now."
        app.screen.submit(via_tmux=True)  # type: ignore[attr-defined]
        await pilot.pause()

    assert sent == [
        (
            "agent-1",
            "Selected plan option: Proceed\n\nOperator notes:\nGo now.",
        )
    ]
    assert captured == ["agent-1"]


async def test_tui_palette_dynamic_plan_option_commands_open_preselected_modal() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.latest_report_by_agent = {
            "agent-1": {
                "report_id": "report-1",
                "plan_options": ["A", "B"],
            }
        }
        app.thread_items = {
            "report:r1": {
                "item_id": "report:r1",
                "kind": "report",
                "metadata": {"plan_options": ["Thread option"]},
            }
        }
        app.selected_thread_item_id = "report:r1"
        commands = {command.title: command for command in app.get_system_commands(app.screen)}

        assert "/plan latest 2: B" in commands
        assert "/plan thread 1: Thread option" in commands

        commands["/plan latest 2: B"].callback()
        await pilot.pause()

        assert app.screen.query_one("#palette-plan-source", Static).renderable == "Latest report"
        assert app.screen.query_one("#palette-plan-option", Select).value == "1"


async def test_tui_palette_dynamic_plan_option_commands_require_options() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        titles = {command.title for command in app.get_system_commands(app.screen)}

    assert "/plan latest" in titles
    assert not any(title.startswith("/plan latest 1:") for title in titles)
    assert not any(title.startswith("/plan thread 1:") for title in titles)


def test_tui_palette_option_labels_are_compact() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.palette_option_label("  A\n\nB  ") == "A B"
    assert app.palette_option_label("x" * 100) == f"{'x' * 69}..."


def test_tui_layout_toggle_persists(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    app.set_layout_mode("compact")

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert app.layout_mode == "compact"
    assert saved["layout"] == "compact"


def test_tui_split_percent_persists_and_clamps(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    app.adjust_split_percent(100)
    assert app.split_percent == 75
    app.adjust_split_percent(-100)
    assert app.split_percent == 25
    app.reset_split_percent()

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert app.split_percent == DEFAULT_SPLIT_PERCENT
    assert saved["split_percent"] == DEFAULT_SPLIT_PERCENT


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
        "metadata": {"pbx_mode": "nohup"},
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
    assert app.format_poll_state({"queued_command_count": 1, "last_poll_at": None}) == "-"
    assert (
        app.format_poll_state(
            {
                "queued_command_count": 1,
                "last_poll_at": None,
                "metadata": {"pbx_mode": "nohup"},
            }
        )
        == "never"
    )
    assert app.agent_poll_level({"queued_command_count": 0, "last_poll_at": 980.0}) == "active"
    assert app.agent_poll_level(agent) == "stale"
    assert app.agent_poll_level({"queued_command_count": 1, "last_poll_at": None}) == "idle"
    assert (
        app.agent_poll_level(
            {
                "queued_command_count": 1,
                "last_poll_at": None,
                "metadata": {"pbx_mode": "nohup"},
            }
        )
        == "never"
    )
    assert app.agent_poll_level({"queued_command_count": 0, "last_poll_at": None}) == "idle"
    assert app.format_usage_state({}) == "-"
    assert app.format_pbx_active({"pbx_active": True}) == "report"
    assert app.format_pbx_active({"pbx_active": True, "metadata": {"pbx_mode": "nohup"}}) == "nohup"
    assert app.format_pbx_active({"pbx_active": True, "metadata": {"pbx_mode": "unknown"}}) == "report"
    assert app.format_pbx_active({"pbx_active": False}) == "off"
    assert app.format_pbx_active({}) == "report"
    app.agents = {
        "agent-1": {"agent_id": "agent-1", "metadata": {"pbx_mode": "report"}},
        "agent-2": {"agent_id": "agent-2", "metadata": {"pbx_mode": "nohup"}},
    }
    assert "requires Agent PBX nohup mode" in app.command_delivery_note("agent-1")
    assert "Waiting for the agent to poll" in app.command_delivery_note("agent-2")


def test_tui_styles_active_polling_agent_rows(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 1000.0)

    cells = app.style_agent_row(
        ["", "agent-1", "on", "working", "demo", "1000", "", "active", "-"],
        {"queued_command_count": 0, "last_poll_at": 990.0},
    )

    assert all(isinstance(cell, Text) for cell in cells)
    assert {cell.style for cell in cells if isinstance(cell, Text)} == {"bold green"}


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
        app.select_thread_item("report:r1")
        report_detail = app.query_one("#thread-detail", TextArea).text
        plan_options = app.query_one("#plan-options", DataTable)
        send_plan = app.query_one("#send-plan-choice", Button)
        send_plan_tmux = app.query_one("#send-plan-tmux", Button)
        table = app.query_one("#thread", DataTable)
        plan_row_count = plan_options.row_count
        plan_option_text = plan_options.get_row_at(0)[1]
        plan_marker = table.get_row_at(0)[3]
        send_plan_enabled = not send_plan.disabled
        send_plan_tmux_disabled = send_plan_tmux.disabled
        app.select_thread_item("command:c1")
        detail = app.query_one("#thread-detail", TextArea).text

    assert app.selected_thread_item_id == "command:c1"
    assert "Plan Options:" in report_detail
    assert plan_row_count == 1
    assert plan_option_text == "Next"
    assert plan_marker == "PLAN:1"
    assert send_plan_enabled is True
    assert send_plan_tmux_disabled is True
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
    assert table.get_row_at(0)[5] == "New report"
    assert table.get_row_at(1)[5] == "Middle command"
    assert table.get_row_at(2)[5] == "Old report"


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


def test_tui_queued_command_ids_for_delete_prefers_marked_items() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.selected_thread_item_id = "command:selected"
    app.thread_order = ["command:selected", "command:queued", "command:acked", "report:r1"]
    app.thread_items = {
        "command:selected": {
            "item_id": "command:selected",
            "kind": "command",
            "status": "queued",
            "metadata": {"command_id": "selected"},
        },
        "command:queued": {
            "item_id": "command:queued",
            "kind": "command",
            "status": "queued",
            "metadata": {"command_id": "queued"},
        },
        "command:acked": {
            "item_id": "command:acked",
            "kind": "command",
            "status": "acked",
            "metadata": {"command_id": "acked"},
        },
        "report:r1": {
            "item_id": "report:r1",
            "kind": "report",
            "status": "done",
            "metadata": {"report_id": "r1"},
        },
    }
    app.marked_thread_item_ids = {"command:queued", "command:acked", "report:r1"}

    assert app.queued_command_ids_for_delete() == ["queued"]


async def test_tui_delete_queued_thread_commands_refreshes_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[str] = []
    events_refreshed = 0
    agents_refreshed = 0
    threads: list[str] = []

    async def fake_delete_queued_command(command_id: str) -> dict[str, str]:
        deleted.append(command_id)
        return {"command_id": command_id}

    async def fake_refresh_events() -> None:
        nonlocal events_refreshed
        events_refreshed += 1

    async def fake_refresh_agents() -> None:
        nonlocal agents_refreshed
        agents_refreshed += 1

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.delete_queued_command = fake_delete_queued_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]
    app.selected_agent_id = "agent-1"
    app.selected_thread_item_id = "command:c1"
    app.thread_order = ["command:c1"]
    app.thread_items = {
        "command:c1": {
            "item_id": "command:c1",
            "kind": "command",
            "status": "queued",
            "metadata": {"command_id": "c1"},
        }
    }

    deleted_count = await app.delete_queued_thread_commands()

    assert deleted_count == 1
    assert deleted == ["c1"]
    assert events_refreshed == 1
    assert agents_refreshed == 1
    assert threads == ["agent-1"]


async def test_tui_dismiss_selected_agent_hides_and_retains_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, bool]] = []
    agents_refreshed = 0
    events_refreshed = 0

    async def fake_delete_agent(agent_id: str, *, delete_thread: bool = False) -> dict[str, str]:
        deleted.append((agent_id, delete_thread))
        return {"agent_id": agent_id}

    async def fake_refresh_agents() -> None:
        nonlocal agents_refreshed
        agents_refreshed += 1

    async def fake_refresh_events() -> None:
        nonlocal events_refreshed
        events_refreshed += 1

    app.delete_agent = fake_delete_agent  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.latest_report_by_agent = {"agent-1": {"summary": "done"}}
        app.unseen_latest_agent_ids = {"agent-1"}
        app.thread_items = {"report:r1": {"item_id": "report:r1"}}
        app.thread_order = ["report:r1"]
        app.render_agents()
        agents_refreshed = 0
        events_refreshed = 0
        await app.dismiss_selected_agent(delete_thread=False)
        detail = app.query_one("#detail", TextArea).text

    assert deleted == [("agent-1", False)]
    assert agents_refreshed == 1
    assert events_refreshed == 1
    assert app.selected_agent_id is None
    assert app.latest_report_by_agent == {}
    assert app.unseen_latest_agent_ids == set()
    assert app.thread_items == {}
    assert "Hidden agent-1." in detail
    assert "Thread data was retained" in detail


async def test_tui_purge_selected_agent_uses_cursor_and_deletes_thread() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, bool]] = []

    async def fake_delete_agent(agent_id: str, *, delete_thread: bool = False) -> dict[str, str]:
        deleted.append((agent_id, delete_thread))
        return {"agent_id": agent_id}

    async def fake_refresh() -> None:
        return None

    app.delete_agent = fake_delete_agent  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh  # type: ignore[method-assign]
    app.refresh_events = fake_refresh  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
        }
        app.selected_agent_id = "agent-1"
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        table.move_cursor(row=1, animate=False)
        await app.dismiss_selected_agent(delete_thread=True)
        detail = app.query_one("#detail", TextArea).text

    assert deleted == [("agent-2", True)]
    assert app.selected_agent_id == "agent-1"
    assert "Purged agent-2." in detail
    assert "Thread data was deleted" in detail


async def test_tui_agent_hide_and_purge_hotkeys_trigger_from_agents_table() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, bool]] = []

    async def fake_delete_agent(agent_id: str, *, delete_thread: bool = False) -> dict[str, str]:
        deleted.append((agent_id, delete_thread))
        return {"agent_id": agent_id}

    async def fake_refresh() -> None:
        return None

    app.delete_agent = fake_delete_agent  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh  # type: ignore[method-assign]
    app.refresh_events = fake_refresh  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        table.focus()
        table.move_cursor(row=0, animate=False)
        await pilot.press("d")
        await pilot.pause()

        table.move_cursor(row=1, animate=False)
        await pilot.press("shift+d")
        await pilot.pause()

    assert deleted == [("agent-1", False), ("agent-2", True)]


async def test_tui_agent_hide_hotkey_ignored_in_text_inputs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, bool]] = []

    async def fake_delete_agent(agent_id: str, *, delete_thread: bool = False) -> dict[str, str]:
        deleted.append((agent_id, delete_thread))
        return {"agent_id": agent_id}

    app.delete_agent = fake_delete_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        message = app.query_one("#message", TextArea)
        message.focus()
        await pilot.press("d")
        await pilot.press("shift+d")
        await pilot.pause()

    assert deleted == []


async def test_tui_agent_jump_sequence_opens_latest_by_visible_row() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []
    threads: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            f"agent-{index}": {
                "agent_id": f"agent-{index}",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 100.0 + index,
            }
            for index in range(1, 11)
        }
        app.render_agents()
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        await pilot.press("g")
        await pilot.press("3")
        await pilot.pause()

        assert app.selected_agent_id == "agent-3"
        assert app.active_agent_tab == "latest-tab"
        assert tabs.active == "latest-tab"
        assert app.agent_id_at_cursor() == "agent-3"

        await pilot.press("g")
        await pilot.press("0")
        await pilot.pause()

        assert app.selected_agent_id == "agent-10"
        assert app.agent_id_at_cursor() == "agent-10"

    assert loaded == ["agent-3", "agent-10"]
    assert threads == ["agent-3", "agent-10"]


async def test_tui_agent_jump_sequence_opens_compact_agent_view() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async def fake_load_latest_report(agent_id: str) -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        await pilot.press("g")
        await pilot.press("1")
        await pilot.pause()

        assert app.effective_layout_mode == "compact"
        assert app.compact_view == "agent"
        assert app.selected_agent_id == "agent-1"
        assert app.screen.has_class("compact-agent")


async def test_tui_agent_jump_sequence_ignored_in_text_inputs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        message = app.query_one("#message", TextArea)
        message.focus()
        await pilot.press("g")
        await pilot.press("1")
        await pilot.pause()

    assert loaded == []
    assert app.selected_agent_id is None


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


def test_tui_persisted_latest_seen_prevents_startup_alert() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.latest_viewed_at_by_agent = {"agent-1": 101.0}
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 101.0,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == set()


def test_tui_persisted_latest_seen_marks_new_offline_report() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.latest_viewed_at_by_agent = {"agent-1": 101.0}
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == {"agent-1"}


def test_tui_mark_latest_seen_persists_watermark(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 123.0,
        }
    }

    app.mark_latest_seen("agent-1")

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["latest_viewed_at_by_agent"] == {"agent-1": 123.0}


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

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
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

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
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


async def test_tui_agent_refresh_preserves_table_scroll() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1-with-a-long-display-name",
                "status": "running",
                "project": "agent-pbx-project-with-a-long-display-name",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2-with-a-long-display-name",
                "status": "running",
                "project": "agent-pbx-project-with-a-long-display-name",
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        table.scroll_x = 10
        table.scroll_target_x = 10
        app.agents["agent-1"]["status"] = "completed"
        app.render_agents()

    assert table.scroll_x == 10
    assert table.scroll_target_x == 10


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


async def test_tui_mark_agent_canceled_creates_report() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    reports: list[tuple[str, dict[str, object]]] = []
    refreshed_agents = 0
    refreshed_events = 0
    threads: list[str] = []

    async def fake_create_agent_report(
        agent_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        reports.append((agent_id, payload))
        return {"report_id": "report-1"}

    async def fake_refresh_agents() -> None:
        nonlocal refreshed_agents
        refreshed_agents += 1

    async def fake_refresh_events() -> None:
        nonlocal refreshed_events
        refreshed_events += 1

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.create_agent_report = fake_create_agent_report  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "demo",
                "status": "working",
                "effective_status": "stale-working",
            }
        }
        app.query_one("#agent-id", Input).value = "agent-1"
        refreshed_agents = 0
        refreshed_events = 0
        await app.mark_agent_canceled()
        detail = app.query_one("#detail", TextArea).text

    assert reports == [
        (
            "agent-1",
            {
                "project": "demo",
                "status": "canceled",
                "summary": "Session marked canceled by operator",
                "detail": (
                    "The operator marked this agent canceled from the TUI because "
                    "the CLI session was cancelled or is no longer active.\n\n"
                    "Previous status: working\n"
                    "Previous effective status: stale-working"
                ),
                "needs_input": False,
                "plan_options": [],
            },
        )
    ]
    assert refreshed_agents == 1
    assert refreshed_events == 1
    assert threads == ["agent-1"]
    assert "Marked agent-1 canceled." in detail
    assert "Report: report-1" in detail


async def test_tui_send_plan_choice_queues_follow_up_with_notes() -> None:
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

    async with app.run_test():
        item = {
            "item_id": "report:r1",
            "kind": "report",
            "agent_id": "agent-1",
            "created_at": 123.0,
            "status": "blocked",
            "title": "Choose",
            "body": "Pick a path",
            "metadata": {"plan_options": ["A", "B"]},
        }
        app.selected_agent_id = "agent-1"
        app.thread_items = {"report:r1": item}
        app.select_thread_item("report:r1")
        app.select_plan_option("1")
        app.query_one("#plan-notes", TextArea).text = "Prefer the safer path."
        await app.send_plan_choice()
        notes = app.query_one("#plan-notes", TextArea).text

    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": (
                    "Selected plan option: B\n\n"
                    "Operator notes:\nPrefer the safer path."
                )
            },
        )
    ]
    assert threads == ["agent-1"]
    assert notes == ""


async def test_tui_latest_plan_choice_queues_follow_up_with_notes() -> None:
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

    async with app.run_test():
        report = {
            "report_id": "r1",
            "agent_id": "agent-1",
            "project": "demo",
            "status": "needs_input",
            "summary": "Choose",
            "detail": "Pick a path",
            "needs_input": True,
            "plan_options": ["A", "B"],
            "created_at": 123.0,
        }
        app.selected_agent_id = "agent-1"
        app.latest_report_by_agent = {"agent-1": report}
        app.render_latest_plan_choice_panel(report)
        app.select_latest_plan_option("1")
        app.query_one("#latest-plan-notes", TextArea).text = "Prefer B."
        table = app.query_one("#latest-plan-options", DataTable)
        await app.send_latest_plan_choice()
        notes = app.query_one("#latest-plan-notes", TextArea).text

    assert table.row_count == 2
    assert queued == [
        (
            "agent-1",
            "send_input",
            {"message": "Selected plan option: B\n\nOperator notes:\nPrefer B."},
        )
    ]
    assert threads == ["agent-1"]
    assert notes == ""


async def test_tui_send_plan_choice_to_tmux_uses_direct_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        item = {
            "item_id": "report:r1",
            "kind": "report",
            "agent_id": "agent-1",
            "created_at": 123.0,
            "status": "blocked",
            "title": "Choose",
            "body": "Pick a path",
            "metadata": {"plan_options": ["Proceed"]},
        }
        app.selected_agent_id = "agent-1"
        app.thread_items = {"report:r1": item}
        app.select_thread_item("report:r1")
        app.query_one("#plan-notes", TextArea).text = "Go now."
        await app.send_plan_choice_to_tmux()

    assert sent == [
        (
            "agent-1",
            "Selected plan option: Proceed\n\nOperator notes:\nGo now.",
        )
    ]
    assert captures == ["agent-1"]


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
        await message._on_key(Key("shift_enter", None))
        await message._on_key(Key("ctrl+j", None))
        await message._on_key(Key("alt+enter", None))
        await message._on_key(Key("ctrl+enter", None))
        newline_text = message.text
        message.text = "\n".join(f"line {index}" for index in range(20))
        app.resize_message_input()
        composer_height = app.query_one("#composer").styles.height.value

    assert "line 1\n\n\n\n" in newline_text
    assert message.styles.height.value == 15
    assert composer_height == 22


def test_tui_follow_up_newline_key_detection_accepts_terminal_variants() -> None:
    assert is_follow_up_newline_key(Key("shift+enter", None)) is True
    assert is_follow_up_newline_key(Key("shift_enter", None)) is True
    assert is_follow_up_newline_key(Key("shift+return", None)) is True
    assert is_follow_up_newline_key(Key("alt+enter", None)) is True
    assert is_follow_up_newline_key(Key("ctrl+enter", None)) is True
    assert is_follow_up_newline_key(Key("ctrl+j", None)) is True
    assert is_follow_up_newline_key(Key("enter", None)) is False


def test_tui_follow_up_edit_key_detection_accepts_described_controls() -> None:
    assert follow_up_edit_control(Key("ctrl+w", None)) == "delete_word_left"
    assert follow_up_edit_control(Key("ctrl+a", None)) is None
    assert follow_up_edit_control(Key("ctrl+e", None)) is None
    assert follow_up_edit_control(Key("ctrl+u", None)) is None
    assert follow_up_edit_control(Key("ctrl+j", None)) is None
    assert follow_up_edit_control(Key("ctrl+d", None)) is None
    assert follow_up_edit_control(Key("ctrl+k", None)) is None
    assert follow_up_edit_control(Key("alt+u", None)) is None
    assert follow_up_edit_control(Key("alt+k", None)) is None
    assert follow_up_edit_control(Key("ctrl+b", None)) is None
    assert follow_up_edit_control(Key("ctrl+f", None)) is None
    assert follow_up_edit_control(Key("ctrl+p", None)) is None
    assert follow_up_edit_control(Key("ctrl+n", None)) is None


async def test_tui_follow_up_word_edit_control_works() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "abc def"
        message.move_cursor((0, 7))

        await message._on_key(Key("ctrl+w", None))
        after_word_back_text = message.text
        after_word_back_cursor = message.cursor_location

    assert after_word_back_text == "abc "
    assert after_word_back_cursor == (0, 4)
