import inspect
import json
from pathlib import Path
import shlex
import subprocess
from types import SimpleNamespace

import pytest
from rich.text import Text

from agent_pbx import tmux as tmux_support
from agent_pbx.codex_cli import (
    CODEX_MODEL_ENV,
    CODEX_REASONING_EFFORT_ENV,
    CODEX_REASONING_SUMMARY_ENV,
    CODEX_SERVICE_TIER_ENV,
    CODEX_VERBOSITY_ENV,
    CodexCliPosture,
)
from agent_pbx.codex_sessions import CodexTranscriptBoundary, CodexTranscriptResult
from agent_pbx.tui import (
    ActiveCodexSession,
    AgentPBXTUI,
    ClipboardCandidate,
    ClipboardSnapshot,
    CodexCopyCapture,
    CodexSessionLeaseConflictError,
    CODEX_TERMINAL_MODE_DEFAULT,
    CODEX_TERMINAL_MODE_RAW,
    CODEX_TERMINAL_MODE_SCROLLBACK,
    CustomSlashCommand,
    EditorCloseConfirmScreen,
    ModelElevationScreen,
    ManagedProjectPickerScreen,
    OperatorHistoryScreen,
    OperatorSessionCandidate,
    ReadingMarkdown,
    PLAN_PBX_CONTEXT_PROMPT,
    PlanSelection,
    OPERATOR_MCP_APPROVAL_SERVERS_ENV,
    REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV,
    TMUX_LIVENESS_IDLE_SECONDS,
    DEFAULT_SPLIT_PERCENT,
    JOPLIN_COPY_MODE_COPY_FIRST,
    JOPLIN_COPY_MODE_TRANSCRIPT_FIRST,
    JOPLIN_COPY_MODE_TMUX_CAPTURE,
    agent_pbx_mcp_url,
    built_in_palette_command_names,
    caller_agent_config_overrides,
    clipboard_reader_environment,
    contains_codex_copy_picker,
    codex_copy_response_option,
    codex_terminal_config_overrides,
    codex_mcp_add_command,
    codex_model_preset_config_overrides,
    codex_model_preset_for,
    review_operator_config_overrides,
    review_operator_mcp_config_overrides,
    configure_codex_mcp,
    codex_native_plan_selector_indices,
    contains_codex_native_plan_selector,
    contains_codex_copy_selector,
    env_custom_palette,
    env_flag,
    env_slash_commands_file,
    env_theme,
    follow_up_edit_control,
    git_diff_passthrough_command,
    git_push_passthrough_command,
    is_local_server_url,
    is_follow_up_newline_key,
    load_codex_mcp_server_configs,
    load_custom_slash_commands,
    operator_agent_config_overrides,
    operator_panel_height,
    parse_git_push_slash_command,
    parse_custom_slash_commands,
    render_plan_prompt,
    render_custom_slash_prompt,
    response_text_digest,
    resolve_layout,
    run_tui,
    slash_completion_direction,
    tmux_features_available,
)
from agent_pbx.terminal import (
    EMBEDDED_SCROLL_MODE_CHILD,
    PbxTerminalSurface,
    TerminalScrollRequested,
    function_key_sequence,
)
from agent_pbx.runtime_tmux import (
    OuterTmuxContext,
    RuntimeServerMode,
    RuntimeTmuxClient,
    TmuxServerIdentity,
)
from textual.app import App, ComposeResult
from textual.events import Click, Key, MouseDown
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Markdown,
    RichLog,
    Select,
    Static,
    TabbedContent,
    Tabs,
    TextArea,
)


def rich_log_plain(log: RichLog) -> str:
    lines: list[str] = []
    for strip in log.lines:
        lines.append("".join(segment.text for segment in strip))
    for deferred in getattr(log, "_deferred_renders", []):
        content = getattr(deferred, "content", "")
        if isinstance(content, Text):
            lines.append(content.plain)
        else:
            lines.append(str(content))
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def isolate_tui_settings(monkeypatch, tmp_path: Path) -> None:
    for name in [
        "AGENT_PBX_TUI_SETTINGS_FILE",
        "AGENT_PBX_TUI_FLASH",
        "AGENT_PBX_TUI_VISUAL_FLASH",
        "AGENT_PBX_TUI_BELL",
        "AGENT_PBX_TUI_TERMINAL_BELL",
        "AGENT_PBX_TUI_AGENT_BLINK",
        "AGENT_PBX_TUI_LOW_POWER",
        "AGENT_PBX_TUI_WATCH_MODE",
        "AGENT_PBX_TUI_AGENT_REFRESH_SECONDS",
        "AGENT_PBX_TUI_ATTENTION_BLINK_SECONDS",
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
        "AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE",
        "AGENT_PBX_TUI_EMBEDDED_TERMINAL_V2",
        "AGENT_PBX_TUI_EVENT_STREAM_V2",
        "AGENT_PBX_TUI_COMPAT_THREAD",
        "AGENT_PBX_TUI_COMPAT_TERMINAL_CAPTURE",
        "AGENT_PBX_TUI_MOUSE_DEBUG",
        "AGENT_PBX_TUI_COMMANDS_FILE",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1,0")
    monkeypatch.setattr("agent_pbx.tui.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv(
        "AGENT_PBX_TUI_SETTINGS_FILE",
        str(tmp_path / "agent-pbx" / "tui-settings.json"),
    )


def mouse_down(widget, *, button: int = 1) -> MouseDown:
    return MouseDown(widget, 0, 0, 0, 0, button, False, False, False)


def copy_test_pane() -> tmux_support.TmuxPane:
    return tmux_support.TmuxPane(
        "agent-pbx",
        "0",
        "1",
        "%copy",
        True,
        "codex",
        "agent-1",
        str(Path.cwd()),
        100,
        30,
        200,
        window_name="agent-1",
    )


def test_tui_pane_clipboard_reader_environment_excludes_process_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("AGENT_PBX_TOKEN", "private-token")
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")

    environment = clipboard_reader_environment(
        {"DISPLAY": ":1", "XDG_RUNTIME_DIR": "/run/user/1000"}
    )

    assert environment is not None
    assert environment["PATH"] == "/usr/bin"
    assert environment["DISPLAY"] == ":1"
    assert environment["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "AGENT_PBX_TOKEN" not in environment
    assert "OPENAI_API_KEY" not in environment


def install_direct_copy_stubs(
    app: AgentPBXTUI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str,
    source: str = "tmux buffer",
    session_id: str = "session-1",
) -> list[tuple[str, str]]:
    """Install a pane-scoped native /copy transaction for TUI tests."""
    app.joplin_copy_mode = JOPLIN_COPY_MODE_COPY_FIRST
    pane = copy_test_pane()
    sent: list[tuple[str, str]] = []

    async def fake_resolve(
        agent_id: str,
        *,
        status: Static | None,
    ) -> tmux_support.TmuxPane:
        assert agent_id == "agent-1"
        return pane

    async def fake_dismiss(_: tmux_support.TmuxPane) -> bool:
        return True

    async def fake_snapshot(_: tmux_support.TmuxPane) -> ClipboardSnapshot:
        return ClipboardSnapshot(
            values={},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        )

    async def fake_await(
        observed_pane: tmux_support.TmuxPane,
        _: ClipboardSnapshot,
        *,
        expected_digest: str = "",
    ) -> tuple[ClipboardCandidate, str | None]:
        assert observed_pane == pane
        assert expected_digest == response_text_digest(text)
        return ClipboardCandidate(source, text), "idle Codex pane"

    async def fake_capture_raw(pane_id: str) -> str:
        assert pane_id == pane.pane_id
        return "idle Codex pane"

    async def fake_transcript(
        agent_id: str,
        *,
        after_boundary: CodexTranscriptBoundary | None = None,
    ) -> CodexTranscriptResult:
        assert agent_id == "agent-1"
        assert after_boundary is None
        return CodexTranscriptResult(
            text=text,
            session_id=session_id,
            path=Path("/tmp/rollout-session.jsonl"),
            phase="final_answer",
            line_index=7,
            mtime=1_790_000_000.0,
        )

    def fake_send_literal_keys(pane_id: str, command: str, **_: object) -> None:
        sent.append((pane_id, command))

    app.resolve_tmux_send_pane = fake_resolve  # type: ignore[method-assign]
    app.dismiss_codex_copy_selector = fake_dismiss  # type: ignore[method-assign]
    app.copy_clipboard_snapshot_for_pane = fake_snapshot  # type: ignore[method-assign]
    app.await_codex_copy_candidate = fake_await  # type: ignore[method-assign]
    app.copy_codex_transcript_response_text = fake_transcript  # type: ignore[method-assign]
    app.capture_tmux_raw_for_pane = fake_capture_raw  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "send_literal_keys", fake_send_literal_keys)
    return sent


def test_tui_constructs() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")

    assert app.TITLE == "Agent PBX"
    assert ("s", "settings", "Settings") in app.BINDINGS
    assert ("ctrl+t", "toggle_tmux_direct", "Tmux") in app.BINDINGS
    assert any(
        getattr(binding, "key", None) == "f8"
        and getattr(binding, "action", None) == "focus_next_alert"
        and getattr(binding, "priority", False) is True
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "f1"
        and getattr(binding, "action", None) == "focus_agents"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "f2"
        and getattr(binding, "action", None) == "focus_events"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "f3"
        and getattr(binding, "action", None) == "focus_right_pane"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "f4"
        and getattr(binding, "action", None) == "focus_latest_input"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "alt+t"
        and getattr(binding, "action", None) == "toggle_tmux_direct"
        and getattr(binding, "show", True) is False
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "p"
        and getattr(binding, "action", None) == "toggle_star_agent"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "d"
        and getattr(binding, "action", None) == "hide_agent"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "h"
        and getattr(binding, "action", None) == "toggle_hidden_agents"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+h"
        and getattr(binding, "action", None) == "unhide_agent"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+d"
        and getattr(binding, "action", None) == "purge_agent"
        for binding in app.BINDINGS
    )
    alt_number_keys = {f"alt+{value}" for value in "1234567890"}
    assert not any(
        str(getattr(binding, "key", "")) in alt_number_keys for binding in app.BINDINGS
    )
    assert app.server == "http://127.0.0.1:8765"
    assert app.token == "test"
    assert app.visual_flash_enabled is False
    assert app.terminal_bell_enabled is False
    assert app.agent_blink_enabled is True
    assert app.tmux_direct_enabled is False
    assert app.embedded_terminal_v2_enabled is False
    assert app.embedded_terminal_scroll_mode == "tmux"
    assert app.event_stream_v2_enabled is False
    assert app.legacy_thread_enabled is True
    assert app.legacy_terminal_capture_enabled is True
    assert app.tmux_capture_lines == 0
    assert app.tmux_refresh_seconds == 1.5
    assert app.tmux_agent_targets == {}
    assert app.ui_theme == "cyberpunk"
    assert app.layout_mode == "adaptive"
    assert app.effective_layout_mode == "compact"
    assert app.split_percent == DEFAULT_SPLIT_PERCENT
    assert app.compact_view == "home"
    assert ("b", "back", "Back") in app.BINDINGS


def test_run_tui_selects_native_default_only_for_local_server(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeApp:
        def __init__(self, **kwargs: object) -> None:
            calls.append(kwargs)

        def run(self) -> None:
            calls[-1]["ran"] = True

    monkeypatch.setattr("agent_pbx.tui.AgentPBXTUI", FakeApp)

    run_tui(server="http://127.0.0.1:8765", token="local")
    run_tui(server="https://pbx.example.test", token="remote")

    assert calls == [
        {
            "server": "http://127.0.0.1:8765",
            "token": "local",
            "v2_defaults": True,
            "ran": True,
        },
        {
            "server": "https://pbx.example.test",
            "token": "remote",
            "v2_defaults": True,
            "ran": True,
        },
    ]


def test_v2_launcher_defaults_enable_local_native_and_all_event_streams() -> None:
    local = AgentPBXTUI(server="http://127.0.0.1:8765", v2_defaults=True)
    remote = AgentPBXTUI(server="https://pbx.example.test", v2_defaults=True)

    assert local.tmux_direct_enabled is True
    assert local.embedded_terminal_v2_enabled is True
    assert local.event_stream_v2_enabled is True
    assert remote.tmux_direct_enabled is False
    assert remote.embedded_terminal_v2_enabled is False
    assert remote.event_stream_v2_enabled is True


def test_tui_v2_event_envelope_applies_snapshot_and_new_events() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")
    handled: list[int] = []
    saved: list[bool] = []
    app.render_agents = lambda **_: None  # type: ignore[method-assign]
    app.call_later = lambda callback, event: handled.append(  # type: ignore[method-assign]
        int(event["event_id"])
    )
    app.save_settings = lambda: saved.append(True)  # type: ignore[method-assign]
    app.apply_event_stream_v2_envelope(
        {
            "api_version": "agent-pbx.events/v2",
            "kind": "snapshot",
            "cursor": 4,
            "state": {
                "agents": [
                    {"agent_id": "agent-a", "project": "demo"},
                ]
            },
            "events": [
                {"event_id": 3, "type": "old"},
                {"event_id": 4, "type": "new"},
            ],
        }
    )
    assert set(app.agents) == {"agent-a"}
    assert app.last_seen_event_id == 4
    assert handled == [3, 4]
    assert saved == [True]

    app.apply_event_stream_v2_envelope(
        {
            "api_version": "agent-pbx.events/v2",
            "events": [{"event_id": 4, "type": "duplicate"}],
        }
    )
    assert handled == [3, 4]


def test_tui_formats_codex_posture_warning() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    posture = CodexCliPosture(
        shell_version="0.158.0",
        app_server_version="0.158.0",
        latest_stable_version="0.158.0",
        configured_model="codex-5.6",
        configured_model_source="AGENT_PBX_TUI_CODEX_MODEL",
        model_catalog_count=6,
        model_known=False,
        warnings=("Configured Codex model 'codex-5.6' is not in `codex debug models`.",),
    )

    rendered = app.format_codex_posture(posture)

    assert "CLI 0.158.0" in rendered
    assert "app-server 0.158.0" in rendered
    assert "latest 0.158.0" in rendered
    assert "model codex-5.6 !" in rendered
    assert "catalog 6" in rendered
    assert "Warning: Configured Codex model" in rendered


def test_tui_operator_bindings_and_mcp_command_helpers() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")

    assert any(
        getattr(binding, "key", None) == "f5"
        and getattr(binding, "action", None) == "focus_operators"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "f10"
        and getattr(binding, "action", None) == "launch_workspace"
        and getattr(binding, "priority", False)
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+o"
        and getattr(binding, "action", None) == "start_operator"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "u"
        and getattr(binding, "action", None) == "resume_operator"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "y"
        and getattr(binding, "action", None) == "operator_history"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+u"
        and getattr(binding, "action", None) == "restart_operator"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "x"
        and getattr(binding, "action", None) == "stop_operator"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+w"
        and getattr(binding, "action", None) == "start_review_operator_fork"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "upper_w"
        and getattr(binding, "action", None) == "start_review_operator_fork"
        and getattr(binding, "show", True) is False
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+r"
        and getattr(binding, "action", None) == "view_campaign_report"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+m"
        and getattr(binding, "action", None) == "monitor_campaign"
        for binding in app.BINDINGS
    )
    assert any(
        getattr(binding, "key", None) == "shift+c"
        and getattr(binding, "action", None) == "copy_campaign_to_joplin"
        for binding in app.BINDINGS
    )
    assert agent_pbx_mcp_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765/mcp"
    assert codex_mcp_add_command("codex --profile ops", "http://pbx/mcp") == [
        "codex",
        "--profile",
        "ops",
        "mcp",
        "add",
        "agent-pbx",
        "--url",
        "http://pbx/mcp",
        "--bearer-token-env-var",
        "AGENT_PBX_TOKEN",
    ]
    assert operator_panel_height(100) == 25
    assert operator_panel_height(20) == 5


def test_tui_configure_codex_mcp_readds_existing(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = [
        subprocess.CompletedProcess([], 1, "", "already exists"),
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess([], 0, "", ""),
    ]

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return results.pop(0)

    monkeypatch.setattr("agent_pbx.tui.subprocess.run", fake_run)

    configure_codex_mcp("codex", "http://127.0.0.1:8765/mcp", timeout=1)

    assert calls == [
        [
            "codex",
            "mcp",
            "add",
            "agent-pbx",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--bearer-token-env-var",
            "AGENT_PBX_TOKEN",
        ],
        ["codex", "mcp", "remove", "agent-pbx"],
        [
            "codex",
            "mcp",
            "add",
            "agent-pbx",
            "--url",
            "http://127.0.0.1:8765/mcp",
            "--bearer-token-env-var",
            "AGENT_PBX_TOKEN",
        ],
    ]


def test_tui_configure_codex_mcp_does_not_remove_on_generic_failure(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess([], 2, "", "unknown option")

    monkeypatch.setattr("agent_pbx.tui.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="unknown option"):
        configure_codex_mcp("codex", "http://127.0.0.1:8765/mcp", timeout=1)

    assert len(calls) == 1
    assert calls[0][:4] == ["codex", "mcp", "add", "agent-pbx"]


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
                "low_power": True,
                "theme": "1337",
                "layout": "compact",
                "split_percent": 61,
                "joplin_copy_mode": "transcript_first",
                "show_hidden_agents": True,
                "tmux_direct": True,
                "tmux_direct_agent_modes": {"agent-1": True, "agent-2": False},
                "tmux_capture_lines": 250,
                "tmux_runtime_server_mode": "outer_if_present",
                "embedded_terminal_v2": True,
                "embedded_terminal_scroll_mode": "child",
                "tmux_popout_mode": "switch_client",
                "tmux_agent_targets": {"agent-1": "%1"},
                "selected_operator_fork_target_by_operator": {
                    "operator-0": "operator-0-fork-agent-1:%7"
                },
                "tmux_manual_override_agent_ids": ["agent-1"],
                "tmux_detached_agent_ids": ["agent-2"],
                "starred_agent_ids": ["agent-1"],
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
    assert app.low_power_enabled is True
    assert app.agent_refresh_seconds == 15.0
    assert app.attention_blink_seconds == 3.0
    assert app.ui_theme == "1337"
    assert app.layout_mode == "compact"
    assert app.split_percent == 61
    assert app.joplin_copy_mode == JOPLIN_COPY_MODE_TRANSCRIPT_FIRST
    assert app.show_hidden_agents is True
    assert app.tmux_direct_enabled is True
    assert app.tmux_direct_agent_modes == {"agent-1": True, "agent-2": False}
    assert app.tmux_capture_lines == 250
    assert app.tmux_runtime_server_mode == "outer_if_present"
    assert app.embedded_terminal_v2_enabled is True
    assert app.embedded_terminal_scroll_mode == EMBEDDED_SCROLL_MODE_CHILD
    assert app.tmux_popout_mode == "switch_client"
    assert app.tmux_agent_targets == {"agent-1": "%1"}
    assert app.selected_operator_fork_target_by_operator == {
        "operator-0": "operator-0-fork-agent-1:%7"
    }
    assert app.tmux_manual_override_agent_ids == {"agent-1"}
    assert app.tmux_detached_agent_ids == {"agent-2"}
    assert app.starred_agent_ids == {"agent-1"}
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
                "low_power": False,
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
    monkeypatch.setenv("AGENT_PBX_TUI_LOW_POWER", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_AGENT_REFRESH_SECONDS", "22.5")
    monkeypatch.setenv("AGENT_PBX_TUI_ATTENTION_BLINK_SECONDS", "4.5")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_CAPTURE_LINES", "750")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_REFRESH_SECONDS", "2.75")
    monkeypatch.setenv("AGENT_PBX_TUI_TMUX_RUNTIME_SERVER_MODE", "outer_if_present")
    monkeypatch.setenv("AGENT_PBX_TUI_THEME", "cyberpunk")
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "compact")
    monkeypatch.setenv("AGENT_PBX_TUI_SPLIT_PERCENT", "72")
    monkeypatch.setenv("AGENT_PBX_TUI_EVENT_STREAM_V2", "1")
    monkeypatch.setenv("AGENT_PBX_TUI_REMOTE_CLIENT_ID", "thin-client-a")

    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )

    assert app.visual_flash_enabled is False
    assert app.agent_blink_enabled is True
    assert app.low_power_enabled is True
    assert app.agent_refresh_seconds == 22.5
    assert app.attention_blink_seconds == 4.5
    assert app.tmux_direct_enabled is True
    assert app.tmux_capture_lines == 750
    assert app.tmux_refresh_seconds == 2.75
    assert app.tmux_runtime_server_mode == "outer_if_present"
    assert app.ui_theme == "cyberpunk"
    assert app.layout_mode == "compact"
    assert app.split_percent == 72
    assert app.event_stream_v2_enabled is True
    assert app.remote_client_id == "thin-client-a"


async def test_tui_event_stream_worker_uses_isolated_group() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    worker_calls: list[dict[str, object]] = []

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    def fake_run_worker(work, *_args, **kwargs):  # type: ignore[no-untyped-def]
        worker_calls.append(kwargs)
        if inspect.iscoroutine(work):
            work.close()
        return None

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]

    async with app.run_test():
        pass

    event_workers = [
        call for call in worker_calls if call.get("name") == "events"
    ]
    assert event_workers == [
        {
            "name": "events",
            "group": "event-stream",
            "exclusive": True,
            "exit_on_error": False,
        }
    ]


def test_tui_event_stream_parser_ignores_keepalive_and_bad_data() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.event_from_sse_line(": keepalive") is None
    assert app.event_from_sse_line("data:") is None
    assert app.event_from_sse_line("data: not-json") is None
    assert app.event_from_sse_line('data: {"type": "report_created"}') is None
    assert app.event_from_sse_line(
        'data: {"event_id": "12", "type": "report_created"}'
    ) == {
        "event_id": 12,
        "type": "report_created",
    }


def test_tui_refresh_workers_use_non_default_groups() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    worker_calls: list[dict[str, object]] = []

    def fake_run_worker(work, *_args, **kwargs):  # type: ignore[no-untyped-def]
        worker_calls.append(kwargs)
        if inspect.iscoroutine(work):
            work.close()
        return None

    app.run_worker = fake_run_worker  # type: ignore[method-assign]

    app.schedule_refresh_agents()
    app.schedule_refresh_tmux_capture()

    assert worker_calls == [
        {
            "name": "agents-periodic-refresh",
            "group": "agents-refresh",
            "exclusive": True,
        },
        {
            "name": "tmux-periodic-refresh",
            "group": "tmux-refresh",
            "exclusive": True,
        },
    ]


async def test_tui_refresh_events_uses_tail_and_resets_stale_watermark(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )
    events = [
        {
            "event_id": 7,
            "type": "agent_registered",
            "subject_id": "agent-7",
            "payload": {"agent_id": "agent-7"},
            "created_at": 7.0,
        },
        {
            "event_id": 8,
            "type": "report_created",
            "subject_id": "report-8",
            "payload": {"agent_id": "agent-8"},
            "created_at": 8.0,
        },
    ]

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return events

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def get(self, path, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append({"path": path, **kwargs})
            return FakeResponse()

    fake_client = FakeClient()

    async def fake_refresh_agents() -> None:
        return None

    app.api_client = lambda: fake_client  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]

    async with app.run_test():
        fake_client.calls.clear()
        app.last_seen_event_id = 999
        await app.refresh_events()

    assert fake_client.calls[-1]["path"] == "/v1/events"
    assert fake_client.calls[-1]["params"] == {"tail": "true", "limit": 50}
    assert app.last_seen_event_id == 8
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["last_seen_event_id"] == 8


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
    app.low_power_enabled = True
    app.tmux_direct_enabled = True
    app.tmux_direct_agent_modes = {"agent-1": True, "agent-2": False}
    app.tmux_capture_lines = 333
    app.tmux_runtime_server_mode = "outer_if_present"
    app.embedded_terminal_scroll_mode = EMBEDDED_SCROLL_MODE_CHILD
    app.tmux_agent_targets = {"agent-1": "%2"}
    app.selected_operator_fork_target_by_operator = {
        "operator-0": "operator-0-fork-agent-1:%7"
    }
    app.tmux_manual_override_agent_ids = {"agent-1", "agent-3"}
    app.tmux_detached_agent_ids = {"agent-2"}
    app.starred_agent_ids = {"agent-2", "agent-1"}
    app.layout_mode = "compact"
    app.split_percent = 57
    app.joplin_copy_mode = JOPLIN_COPY_MODE_TMUX_CAPTURE
    app.show_hidden_agents = True
    app.latest_viewed_at_by_agent = {"agent-1": 123.0}
    app.last_seen_event_id = 42
    app.set_ui_theme("1337")

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["visual_flash"] is True
    assert saved["terminal_bell"] is True
    assert saved["agent_blink"] is False
    assert saved["low_power"] is True
    assert saved["tmux_direct"] is True
    assert saved["tmux_direct_agent_modes"] == {"agent-1": True, "agent-2": False}
    assert saved["tmux_capture_lines"] == 333
    assert saved["tmux_runtime_server_mode"] == "outer_if_present"
    assert saved["embedded_terminal_scroll_mode"] == EMBEDDED_SCROLL_MODE_CHILD
    assert saved["tmux_agent_targets"] == {"agent-1": "%2"}
    assert saved["selected_operator_fork_target_by_operator"] == {
        "operator-0": "operator-0-fork-agent-1:%7"
    }
    assert saved["tmux_manual_override_agent_ids"] == ["agent-1", "agent-3"]
    assert saved["tmux_detached_agent_ids"] == ["agent-2"]
    assert saved["starred_agent_ids"] == ["agent-1", "agent-2"]
    assert saved["theme"] == "1337"
    assert saved["layout"] == "compact"
    assert saved["split_percent"] == 57
    assert saved["joplin_copy_mode"] == JOPLIN_COPY_MODE_TMUX_CAPTURE
    assert saved["show_hidden_agents"] is True
    assert saved["latest_viewed_at_by_agent"] == {"agent-1": 123.0}
    assert saved["last_seen_event_id"] == 42
    assert saved["event_stream_v2"] is False
    assert saved["legacy_thread_enabled"] is True
    assert saved["legacy_terminal_capture_enabled"] is True
    assert saved["remote_client_id"] == app.remote_client_id


def test_remote_tui_does_not_enable_local_native_terminal_by_default() -> None:
    app = AgentPBXTUI(server="https://pbx.example.test")

    assert app.tmux_direct_enabled is False
    assert app.embedded_terminal_v2_enabled is False
    assert app.event_stream_v2_enabled is False


def test_tui_remote_view_state_excludes_prompt_and_document_drafts() -> None:
    app = AgentPBXTUI(server="https://pbx.example.test")
    app.selected_agent_id = "agent-a"
    app.active_agent_tab = "joplin-tab"
    app.active_agent_tab_by_agent = {"agent-a": "joplin-tab"}
    app.message_draft_by_agent = {"agent-a": "private prompt"}
    app.tmux_message_draft_by_agent = {"agent-a": "private terminal prompt"}
    app.editor_documents_by_agent = {
        "agent-a": {"secret.txt": {"text": "private document"}}
    }

    state = app.remote_view_state()

    assert state == {
        "selected_agent_id": "agent-a",
        "active_agent_tab": "joplin-tab",
        "active_agent_tab_by_agent": {"agent-a": "joplin-tab"},
        "show_hidden_agents": False,
        "layout": "adaptive",
        "compact_view": "home",
    }
    serialized = json.dumps(state)
    assert "private prompt" not in serialized
    assert "private document" not in serialized


async def test_remote_tui_does_not_reconcile_thin_client_tmux(monkeypatch) -> None:
    app = AgentPBXTUI(server="https://pbx.example.test", tmux_direct=True)
    app.tmux_features_available = True
    app.tmux_local_direct_context = False
    called = False

    def fail_list_panes():  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        raise AssertionError("remote TUI must not inspect thin-client tmux panes")

    monkeypatch.setattr(tmux_support, "list_panes", fail_list_panes)

    assert await app.reconcile_operator_tmux_targets() is False
    assert called is False


async def test_tui_remote_identity_uses_token_bound_client_and_persists_view() -> None:
    app = AgentPBXTUI(server="https://pbx.example.test")

    class FakeResponse:
        def __init__(self, payload):  # type: ignore[no-untyped-def]
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):  # type: ignore[no-untyped-def]
            return self.payload

    class FakeClient:
        def __init__(self) -> None:
            self.puts: list[dict[str, object]] = []

        async def get(self, path, **_kwargs):  # type: ignore[no-untyped-def]
            if path == "/v2/remote/me":
                return FakeResponse(
                    {"role": "observer", "client_id": "bound-thin-client"}
                )
            return FakeResponse(
                {
                    "client": {
                        "view_state": {
                            "selected_agent_id": "agent-a",
                            "active_agent_tab": "latest-tab",
                        }
                    }
                }
            )

        async def put(self, path, **kwargs):  # type: ignore[no-untyped-def]
            self.puts.append({"path": path, **kwargs})
            return FakeResponse({"client": {}})

    fake = FakeClient()
    app.api_client = lambda: fake  # type: ignore[method-assign]

    await app.initialize_remote_client_state()
    app.last_seen_event_id = 17
    await app.persist_remote_view_state()

    assert app.remote_client_role == "observer"
    assert app.remote_client_id == "bound-thin-client"
    assert app.pending_remote_view_state["selected_agent_id"] == "agent-a"
    assert fake.puts == [
        {
            "path": "/v2/remote/clients/bound-thin-client/view-state",
            "headers": {},
            "json": {
                "view_state": app.remote_view_state(),
                "cursor": 17,
            },
        }
    ]


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
    assert app.should_alert({"type": "report_identity_violation"}) is True
    assert app.should_alert({"type": "agent_registered"}) is True
    assert app.should_alert({"type": "command_acked"}) is True
    assert app.should_alert({"type": "operator_campaign_event"}) is True
    assert app.should_alert({"type": "command_queued"}) is False
    assert app.should_alert({"type": "command_delivered"}) is False
    assert (
        app.event_agent_id(
            {
                "type": "report_identity_violation",
                "payload": {
                    "agent_id": "caller-1",
                    "reporting_agent_id": "operator-0",
                },
            }
        )
        == "operator-0"
    )


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


async def test_tui_refresh_agents_can_include_hidden() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return [
                {
                    "agent_id": "agent-1",
                    "agent_type": "caller",
                    "project": "demo",
                    "name": None,
                    "status": "done",
                    "effective_status": "done",
                    "pbx_active": True,
                    "metadata": {},
                    "created_at": 1.0,
                    "last_seen_at": 2.0,
                    "dismissed_at": 3.0,
                }
            ]

    class Client:
        async def get(self, path: str, **kwargs: object) -> Response:
            calls.append({"path": path, **kwargs})
            return Response()

    async def fake_refresh_events() -> None:
        return None

    async def fake_refresh_joplin_status() -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.refresh_joplin_status = fake_refresh_joplin_status  # type: ignore[method-assign]

    async with app.run_test():
        app.show_hidden_agents = True
        await app.refresh_agents()
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert calls[-1]["path"] == "/v1/agents"
    assert calls[-1]["params"] == {"include_hidden": "true"}
    assert app.agents["agent-1"]["dismissed_at"] == 3.0
    assert row[-1].plain == "hidden"


async def test_tui_mounts_latest_composer_and_settings_controls() -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        visual_flash=True,
        tmux_direct=False,
    )

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        agents = app.query_one("#agents", DataTable)
        operators = app.query_one("#operators", DataTable)
        thread = app.query_one("#thread", DataTable)
        operator_start = app.query_one("#operator-start", Button)
        operator_history = app.query_one("#operator-history", Button)
        operator_resume = app.query_one("#operator-resume", Button)
        operator_restart = app.query_one("#operator-restart", Button)
        operator_stop = app.query_one("#operator-stop", Button)
        operator_review = app.query_one("#operator-fork-review", Button)
        star_agent = app.query_one("#star-agent", Button)
        toggle_hidden = app.query_one("#toggle-hidden-agents", Button)
        unhide_agent = app.query_one("#unhide-agent", Button)
        hide_agent = app.query_one("#hide-agent", Button)
        agents_prune = app.query_one("#agents-prune", Button)
        purge_agent = app.query_one("#purge-agent", Button)
        request_detail = app.query_one("#request-detail", Button)
        ping = app.query_one("#ping-agent", Button)
        mark_canceled = app.query_one("#mark-canceled", Button)
        export_item = app.query_one("#export-item", Button)
        export_marked = app.query_one("#export-marked", Button)
        export_all = app.query_one("#export-all", Button)
        delete_queued = app.query_one("#delete-queued", Button)
        files = app.query_one("#files", DataTable)
        files_search_query = app.query_one("#files-search-query", Input)
        file_search_results = app.query_one("#file-search-results", DataTable)
        file_search_status = app.query_one("#file-search-status", Static)
        file_preview = app.query_one("#file-preview", RichLog)
        files_refresh = app.query_one("#files-refresh", Button)
        files_up = app.query_one("#files-up", Button)
        files_copy_path = app.query_one("#files-copy-path", Button)
        files_copy_text = app.query_one("#files-copy-text", Button)
        files_open_editor = app.query_one("#files-open-editor", Button)
        files_create_file = app.query_one("#files-create-file", Button)
        files_create_directory = app.query_one("#files-create-directory", Button)
        files_move = app.query_one("#files-move", Button)
        files_delete = app.query_one("#files-delete", Button)
        files_trash = app.query_one("#files-trash", Button)
        editor = app.query_one("#editor", TextArea)
        editor_status = app.query_one("#editor-status", Static)
        editor_diagnostics = app.query_one("#editor-diagnostics", TextArea)
        editor_save = app.query_one("#editor-save", Button)
        editor_revert = app.query_one("#editor-revert", Button)
        editor_copy_path = app.query_one("#editor-copy-path", Button)
        editor_copy_text = app.query_one("#editor-copy-text", Button)
        editor_check = app.query_one("#editor-check", Button)
        editor_close = app.query_one("#editor-close", Button)
        editor_fullscreen = app.query_one("#editor-fullscreen", Button)
        editor_hotkeys = app.query_one("#editor-hotkeys", Static)
        latest_plan_hint = app.query_one("#latest-plan-hint", Static)
        plan_hint = app.query_one("#plan-hint", Static)
        workerbee_detail = app.query_one("#workerbee-detail", TextArea)
        workerbee_refresh = app.query_one("#workerbee-refresh", Button)
        joplin_status = app.query_one("#joplin-status", Static)
        joplin_notes = app.query_one("#joplin-notes", DataTable)
        joplin_body = app.query_one("#joplin-body", TextArea)
        joplin_reader = app.query_one("#joplin-reader", Markdown)
        joplin_new = app.query_one("#joplin-new", Button)
        joplin_rename = app.query_one("#joplin-rename", Button)
        joplin_delete = app.query_one("#joplin-delete", Button)
        joplin_refresh = app.query_one("#joplin-refresh", Button)
        joplin_copy = app.query_one("#joplin-copy-latest", Button)
        joplin_log_start = app.query_one("#joplin-log-start", Button)
        joplin_log_stop = app.query_one("#joplin-log-stop", Button)
        joplin_sync = app.query_one("#joplin-sync", Button)
        joplin_save = app.query_one("#joplin-save", Button)
        joplin_edit = app.query_one("#joplin-edit", Button)
        joplin_preview = app.query_one("#joplin-preview", Button)
        joplin_hotkeys = app.query_one("#joplin-hotkeys", Static)
        composer = app.query_one("#composer")
        agent_id = app.query_one("#agent-id", Input)
        message = app.query_one("#message", TextArea)
        actions = app.query_one("#composer-actions")
        hotkeys = app.query_one("#composer-hotkeys")
        buttons = app.query_one("#composer-buttons")

        assert agents.cursor_type == "row"
        assert agents.show_row_labels is False
        assert operators.cursor_type == "row"
        assert operators.show_row_labels is False
        assert thread.cursor_type == "row"
        assert thread.show_row_labels is False
        assert files.cursor_type == "row"
        assert files.show_row_labels is False
        assert file_search_results.cursor_type == "row"
        assert file_search_results.show_row_labels is False
        assert operator_start.label.plain == "Start O"
        assert operator_history.label.plain == "Hist y"
        assert operator_resume.label.plain == "Resume u"
        assert operator_restart.label.plain == "Restart U"
        assert operator_stop.label.plain == "Stop x"
        assert operator_review.label.plain == "Review W"
        assert star_agent.label.plain == "Star/Unstar (p)"
        assert toggle_hidden.label.plain == "Show Hidden (h)"
        assert unhide_agent.label.plain == "Unhide (H)"
        assert hide_agent.label.plain == "Hide Agent (d)"
        assert agents_prune.label.plain == "Prune"
        assert purge_agent.label.plain == "Purge Agent (D)"
        assert request_detail.label.plain == "Request Detail"
        assert ping.label.plain == "Ping"
        assert mark_canceled.label.plain == "Mark Canceled"
        assert export_item.label.plain == "Export Item"
        assert export_marked.label.plain == "Export Marked"
        assert export_all.label.plain == "Export All"
        assert delete_queued.label.plain == "Delete Queued"
        assert files_search_query.placeholder == "Search files"
        assert str(file_search_status.renderable) == "Search: -"
        assert file_preview.can_focus is True
        assert files_refresh.label.plain == "Refresh Files"
        assert files_up.label.plain == "Up"
        assert files_copy_path.label.plain == "Copy Path"
        assert files_copy_text.label.plain == "Copy Text"
        assert files_open_editor.label.plain == "Open Editor"
        assert files_create_file.label.plain == "New File"
        assert files_create_directory.label.plain == "New Dir"
        assert files_move.label.plain == "Move"
        assert files_delete.label.plain == "Delete"
        assert files_trash.label.plain == "Trash"
        assert editor.read_only is False
        assert str(editor_status.renderable) == "Editor: no file"
        assert editor_diagnostics.read_only is True
        assert editor_save.label.plain == "Save"
        assert editor_revert.label.plain == "Revert"
        assert editor_copy_path.label.plain == "Copy Path"
        assert editor_copy_text.label.plain == "Copy Text"
        assert editor_check.label.plain == "Check"
        assert editor_close.label.plain == "Close"
        assert editor_fullscreen.label.plain == "Full F9"
        assert "Close" in str(editor_hotkeys.renderable)
        assert "F9" in str(editor_hotkeys.renderable)
        assert latest_plan_hint.renderable == "Reply with /plan:1 optional notes."
        assert plan_hint.renderable == "Reply with /plan:1 optional notes."
        assert workerbee_detail.read_only is True
        assert workerbee_refresh.label.plain == "Refresh WorkerBee"
        assert str(joplin_status.renderable).startswith("Joplin:")
        assert joplin_notes.cursor_type == "row"
        assert joplin_body.read_only is False
        assert joplin_reader is not None
        assert joplin_new.label.plain == "New n"
        assert joplin_rename.label.plain == "Ren m"
        assert joplin_delete.label.plain == "Del d"
        assert joplin_refresh.label.plain == "Ref r"
        assert joplin_copy.label.plain == "Copy c"
        assert joplin_log_start.label.plain == "LOG+ l"
        assert joplin_log_stop.label.plain == "LOG- x"
        assert joplin_sync.label.plain == "Sync u"
        assert joplin_save.label.plain == "Save s"
        assert joplin_edit.label.plain == "Edit e"
        assert joplin_preview.label.plain == "Preview p"
        assert "Ctrl+G" in str(joplin_hotkeys.renderable)
        assert "#thread {\n        height: 7;" in app.CSS
        assert "#thread-detail {\n        height: 1fr;" in app.CSS
        assert "#files {\n        height: 8;" in app.CSS
        assert "#file-search-results {\n        height: 7;" in app.CSS
        assert "#file-preview {\n        height: 1fr;" in app.CSS
        assert "#file-actions {\n        height: 6;" in app.CSS
        assert "#editor {\n        height: 1fr;" in app.CSS
        assert "#latest-plan-choice-panel,\n    #plan-choice-panel {" in app.CSS
        assert "#latest-plan-hint,\n    #plan-hint {" in app.CSS
        assert "#workerbee-detail {\n        height: 1fr;" in app.CSS
        assert "#joplin-notes {\n        height: 8;" in app.CSS
        assert "#joplin-body {\n        height: 1fr;" in app.CSS
        assert "#joplin-actions {\n        height: 10;" in app.CSS
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
        assert "F1 Agents" in hotkey_text
        assert "F2 Events" in hotkey_text
        assert "F3 Tabs" in hotkey_text
        assert "F4 Input" in hotkey_text
        assert "Ctrl+J newline" in hotkey_text
        assert "Ctrl+W word" in hotkey_text
        assert "Ctrl+T tmux" in hotkey_text
        assert "F8 alert" in hotkey_text
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
        low_power = app.screen.query_one("#low-power", Checkbox)
        layout_mode = app.screen.query_one("#layout-mode", Select)
        split_label = app.screen.query_one("#split-percent-label", Static)
        split_narrow = app.screen.query_one("#split-narrow", Button)
        split_reset = app.screen.query_one("#split-reset", Button)
        split_widen = app.screen.query_one("#split-widen", Button)
        tmux_direct = app.screen.query_one("#tmux-direct", Checkbox)
        tmux_runtime_mode = app.screen.query_one("#tmux-runtime-mode", Select)
        theme = app.screen.query_one("#theme-mode", Select)
        close = app.screen.query_one("#settings-close", Button)

        assert visual.value is True
        assert bell.value is False
        assert agent_blink.value is True
        assert low_power.value is False
        assert layout_mode.value == "adaptive"
        assert str(split_label.renderable) == "Agents width: 42%"
        assert split_narrow.label.plain == "Narrow"
        assert split_reset.label.plain == "Reset"
        assert split_widen.label.plain == "Widen"
        assert tmux_direct.value is False
        assert tmux_runtime_mode.value == "dedicated"
        assert theme.value == "cyberpunk"
        assert close.label.plain == "Close"


def test_tui_joplin_sync_summary_uses_latest_sync_result() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    summary = app.format_joplin_sync_summary(
        {
            "enabled": True,
            "running": 0,
            "pending": 0,
            "latest": {"status": "succeeded", "error": None},
            "latest_success": {"status": "succeeded"},
            "latest_error": {
                "status": "failed",
                "error": "older failure",
            },
        }
    )

    assert summary == "Sync: ok"


def test_tui_joplin_sync_summary_reports_current_failure() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    summary = app.format_joplin_sync_summary(
        {
            "enabled": True,
            "running": 0,
            "pending": 0,
            "latest": {"status": "failed", "error": "current failure"},
            "latest_success": {"status": "succeeded"},
            "latest_error": {
                "status": "failed",
                "error": "current failure",
            },
        }
    )

    assert summary == "Sync: error"


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
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=False)
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


async def test_tui_tiny_layout_minimizes_latest_input(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "tiny")
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=False)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "status": "done",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        await app.open_agent_latest("agent-1")
        await pilot.pause()

        detail = app.query_one("#detail", TextArea)
        composer = app.query_one("#composer")
        composer_inputs = app.query_one("#composer-inputs")
        message = app.query_one("#message", TextArea)
        actions = app.query_one("#composer-actions")
        hotkeys = app.query_one("#composer-hotkeys", Static)

        message.text = "\n".join(f"line {index}" for index in range(8))
        app.resize_message_input()
        await pilot.pause()

        assert app.screen.has_class("tiny-agent")
        assert not app.screen.has_class("tmux-direct")
        assert detail.region.height >= 6
        assert composer.region.height == 6
        assert composer_inputs.styles.height.value == 1
        assert message.styles.height.value == 1
        assert message.region.height <= 2
        assert actions.region.y >= message.region.y + 1
        assert hotkeys.region.bottom <= composer.region.bottom


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


async def test_tui_tmux_stream_focus_preserves_manual_scroll() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        stream = app.query_one("#tmux-stream", TextArea)
        message = app.query_one("#tmux-message", TextArea)
        stream.text = "\n".join(f"line {index}" for index in range(40))
        stream.move_cursor((0, 0))
        stream.scroll_home(animate=False)
        message.focus()
        await pilot.pause()

        stream.focus()
        await pilot.pause()

    assert stream.cursor_location == (0, 0)


async def test_tui_tmux_prepare_starts_stream_at_bottom() -> None:
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

        app.prepare_tmux_stream_for_capture(
            stream,
            cache_key="agent-1:%76",
            pane=pane,
        )

    expected = "Loading tmux pane %76 (agent-pbx:0.2)..."
    assert stream.text == expected
    assert stream.cursor_location == (0, len(expected))
    assert stream.is_vertical_scroll_end


async def test_tui_tmux_update_preserves_manual_scroll_when_not_at_bottom() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(100, 20)
        await pilot.pause()
        stream = app.query_one("#tmux-stream", TextArea)
        stream.text = "\n".join(f"old {index}" for index in range(80))
        app.snap_tmux_stream_to_bottom(stream)
        await pilot.pause()

        stream.move_cursor((0, 0))
        stream.scroll_home(animate=False)
        scroll_y = stream.scroll_y
        scroll_target_y = stream.scroll_target_y

        changed = app.update_tmux_stream(
            stream,
            "\n".join(f"new {index}" for index in range(90)),
            cache_key="agent:%1",
        )

    assert changed is True
    assert stream.scroll_y == scroll_y
    assert stream.scroll_target_y == scroll_target_y
    assert stream.cursor_location == (0, 0)


def test_tui_tmux_stream_anchor_tracks_content_after_prefix_is_dropped() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    old_text = "\n".join(f"line {index}" for index in range(100))
    new_text = "\n".join(f"line {index}" for index in range(40, 120))

    anchor = app.tmux_stream_content_anchor(old_text, 50.0)
    anchored_y = app.tmux_stream_anchored_scroll_y(
        new_text,
        anchor,
        fallback=50.0,
    )

    assert anchored_y == 10.0


async def test_tui_resize_restores_tmux_stream_tail_when_at_bottom() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    callbacks: list[object] = []

    def fake_call_after_refresh(callback: object) -> None:
        callbacks.append(callback)

    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(100, 20)
        await pilot.pause()
        stream = app.query_one("#tmux-stream", TextArea)
        app.active_agent_tab = "latest-tab"
        app.tmux_visible_capture_key = "agent-1:%76"
        stream.text = "\n".join(f"line {index}" for index in range(80))
        app.snap_tmux_stream_to_bottom(stream)

        app.on_resize(SimpleNamespace(size=SimpleNamespace(width=120, height=32)))
        stream.scroll_home(animate=False)
        callbacks[0]()

    assert stream.is_vertical_scroll_end
    assert stream.cursor_location == (79, len("line 79"))


async def test_tui_resize_preserves_manual_tmux_scrollback() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    callbacks: list[object] = []

    def fake_call_after_refresh(callback: object) -> None:
        callbacks.append(callback)

    app.call_after_refresh = fake_call_after_refresh  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(100, 20)
        await pilot.pause()
        stream = app.query_one("#tmux-stream", TextArea)
        app.active_agent_tab = "latest-tab"
        app.tmux_visible_capture_key = "agent-1:%76"
        stream.text = "\n".join(f"line {index}" for index in range(80))
        app.snap_tmux_stream_to_bottom(stream)
        stream.move_cursor((0, 0))
        stream.scroll_home(animate=False)
        scroll_y = stream.scroll_y

        app.on_resize(SimpleNamespace(size=SimpleNamespace(width=120, height=32)))

    assert callbacks == []
    assert stream.scroll_y == scroll_y


async def test_tui_mouse_down_focuses_tapped_sections() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        agents = app.query_one("#agents", DataTable)
        events = app.query_one("#events", DataTable)
        tmux_panel = app.query_one("#tmux-panel")
        stream = app.query_one("#tmux-stream", TextArea)
        message = app.query_one("#tmux-message", TextArea)

        app.on_mouse_down(mouse_down(agents))
        await pilot.pause()
        assert app.focused is agents

        app.on_mouse_down(mouse_down(events))
        await pilot.pause()
        assert app.focused is events

        app.on_mouse_down(mouse_down(tmux_panel))
        await pilot.pause()
        assert app.focused is stream

        app.on_mouse_down(mouse_down(stream))
        await pilot.pause()
        assert app.focused is stream

        app.on_mouse_down(mouse_down(message))
        await pilot.pause()
        assert app.focused is message


async def test_tui_mouse_down_on_tmux_stream_preserves_manual_scroll() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        stream = app.query_one("#tmux-stream", TextArea)
        message = app.query_one("#tmux-message", TextArea)
        stream.text = "\n".join(f"line {index}" for index in range(40))
        stream.move_cursor((0, 0))
        stream.scroll_home(animate=False)

        message.focus()
        await pilot.pause()
        app.on_mouse_down(mouse_down(stream))
        await pilot.pause()
        first_tap_location = stream.cursor_location

        stream.move_cursor((0, 0))
        stream.scroll_home(animate=False)
        app.on_mouse_down(mouse_down(stream))
        await pilot.pause()
        second_tap_location = stream.cursor_location

    assert first_tap_location == (0, 0)
    assert second_tap_location == (0, 0)


async def test_tui_function_keys_focus_split_sections() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        agents = app.query_one("#agents", DataTable)
        events = app.query_one("#events", DataTable)
        tabs = app.query_one("#agent-tabs", TabbedContent).query_one(Tabs)
        message = app.query_one("#tmux-message", TextArea)

        await pilot.press("f1")
        await pilot.pause()
        assert app.focused is agents

        await pilot.press("f2")
        await pilot.pause()
        assert app.focused is events

        await pilot.press("f3")
        await pilot.pause()
        assert app.focused is tabs

        await pilot.press("right")
        await pilot.pause()
        assert app.active_agent_tab == "codex-tab"

        await pilot.press("f4")
        await pilot.pause()
        assert app.active_agent_tab == "latest-tab"
        assert app.focused is message


async def test_tui_shift_f2_alias_passes_unmodified_f2_to_focused_terminal() -> None:
    sent: list[tuple[str, str]] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async def fake_send_key(agent_id: str, key: str) -> bool:
        sent.append((agent_id, key))
        return True

    app.send_key_to_tmux = fake_send_key  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.selected_agent_id = "agent-a"
        stream = app.query_one("#tmux-stream", TextArea)
        stream.focus()
        await pilot.pause()
        app.on_key(Key("f14", None))
        await pilot.pause()

    assert sent == [("agent-a", "F2")]


async def test_tui_shift_f2_writes_directly_to_embedded_terminal() -> None:
    written: list[bytes] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs):  # type: ignore[no-untyped-def]
            return b""

        def write(self, data: bytes) -> None:
            written.append(data)

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        app.selected_agent_id = "agent-a"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        terminal = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        terminal.process = FakeProcess()  # type: ignore[assignment]
        terminal.target = "server:%7"
        app.apply_tmux_class()
        terminal.focus()
        await pilot.pause()

        app.on_key(Key("f14", None))
        await pilot.pause()
        app.on_key(Key("f15", None))
        await pilot.pause()
        assert written == [function_key_sequence(2), function_key_sequence(3)]

        await pilot.press("f2")
        await pilot.pause()
        assert app.focused is app.query_one("#events", DataTable)

    assert app.embedded_terminal_agent_id is None


async def test_tui_coalesces_embedded_wheel_into_exact_tmux_history_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scrolled: list[tuple[str, int, int, str | None]] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, _data: bytes) -> None:
            return None

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    def fake_scroll(
        pane_id: str,
        direction: int,
        *,
        lines: int,
        socket_path: str | None,
    ) -> bool:
        scrolled.append((pane_id, direction, lines, socket_path))
        return True

    monkeypatch.setattr(tmux_support, "scroll_pane_copy_mode", fake_scroll)

    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-a"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        app.tmux_runtime_mapping_by_agent["agent-a"] = {
            "state": "ready",
            "server_id": "server",
            "pane_id": "%7",
            "socket_path": "/tmp/pbx.sock",
        }
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        surface.process = FakeProcess()  # type: ignore[assignment]
        surface.target = "server:%7"
        surface.focus()
        app.on_terminal_scroll_requested(
            TerminalScrollRequested(surface, direction=-1, ticks=1)
        )
        app.on_terminal_scroll_requested(
            TerminalScrollRequested(surface, direction=-1, ticks=2)
        )
        await pilot.pause(0.15)

    assert scrolled == [("%7", -1, 15, "/tmp/pbx.sock")]


async def test_tui_embedded_scroll_request_is_discarded_after_selection_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scrolled: list[str] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, _data: bytes) -> None:
            return None

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    monkeypatch.setattr(
        tmux_support,
        "scroll_pane_copy_mode",
        lambda pane_id, *_args, **_kwargs: scrolled.append(pane_id) or True,
    )

    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-a"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        app.tmux_runtime_mapping_by_agent["agent-a"] = {
            "state": "ready",
            "server_id": "server",
            "pane_id": "%7",
            "socket_path": "/tmp/pbx.sock",
        }
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        surface.process = FakeProcess()  # type: ignore[assignment]
        surface.target = "server:%7"
        app.on_terminal_scroll_requested(
            TerminalScrollRequested(surface, direction=-1, ticks=1)
        )
        app.selected_agent_id = "agent-b"
        await pilot.pause(0.1)

    assert scrolled == []


async def test_tui_explicit_scrollback_targets_selected_embedded_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[tuple[str, str | None]] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, _data: bytes) -> None:
            return None

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    def fake_enter(
        pane_id: str,
        *,
        socket_path: str | None,
    ) -> bool:
        entered.append((pane_id, socket_path))
        return True

    monkeypatch.setattr(tmux_support, "enter_pane_copy_mode", fake_enter)

    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-a"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        app.tmux_runtime_mapping_by_agent["agent-a"] = {
            "state": "ready",
            "server_id": "server",
            "pane_id": "%7",
            "socket_path": "/tmp/pbx.sock",
        }
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        surface.process = FakeProcess()  # type: ignore[assignment]
        surface.target = "server:%7"
        app.apply_tmux_class()
        await pilot.pause()

        assert await app.enter_embedded_tmux_scrollback() is True
        await pilot.pause()
        assert app.focused is surface

    assert entered == [("%7", "/tmp/pbx.sock")]


async def test_tui_terminal_focus_bypasses_printable_and_control_priority_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[bytes] = []
    invoked: list[str] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            written.append(data)

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    action_names = (
        "toggle_star_agent",
        "toggle_hidden_agents",
        "hide_agent",
        "resume_operator",
        "stop_operator",
        "operator_history",
        "start_review_operator_fork",
        "launch_project_spawn",
        "unhide_agent",
        "restart_operator",
        "purge_agent",
        "monitor_campaign",
        "view_campaign_report",
        "copy_campaign_to_joplin",
    )
    for action_name in action_names:
        monkeypatch.setattr(
            app,
            f"action_{action_name}",
            lambda action_name=action_name: invoked.append(action_name),
        )

    keys = (
        ("p", "p", b"p"),
        ("h", "h", b"h"),
        ("d", "d", b"d"),
        ("u", "u", b"u"),
        ("x", "x", b"x"),
        ("y", "y", b"y"),
        ("shift+w", "W", b"W"),
        ("shift+p", "P", b"P"),
        ("shift+h", "H", b"H"),
        ("shift+u", "U", b"U"),
        ("shift+d", "D", b"D"),
        ("shift+m", "M", b"M"),
        ("shift+r", "R", b"R"),
        ("shift+c", "C", b"C"),
        ("ctrl+c", None, b"\x03"),
    )

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        app.selected_agent_id = "agent-a"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        terminal = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        terminal.process = FakeProcess()  # type: ignore[assignment]
        terminal.target = "server:%7"
        app.apply_tmux_class()
        terminal.focus()
        await pilot.pause()

        for key, character, _expected in keys:
            await app.on_event(Key(key, character))
        await pilot.pause()

    assert invoked == []
    assert written == [expected for _key, _character, expected in keys]


async def test_tui_ctrl_p_opens_palette_instead_of_writing_to_terminal() -> None:
    written: list[bytes] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            written.append(data)

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        app.selected_agent_id = "agent-a"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        terminal = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        terminal.process = FakeProcess()  # type: ignore[assignment]
        terminal.target = "server:%7"
        app.apply_tmux_class()
        terminal.focus()
        await pilot.pause()

        await pilot.press("ctrl+p")
        await pilot.pause()

        assert app.screen.id == "--command-palette"
        assert written == []


async def test_tui_tabs_into_embedded_terminal_and_arrows_reach_tmux_client() -> None:
    written: list[bytes] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            written.append(data)

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        app.selected_agent_id = "agent-a"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        terminal = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        terminal.process = FakeProcess()  # type: ignore[assignment]
        terminal.target = "server:%7"
        app.apply_tmux_class()
        await pilot.pause()

        await pilot.press("f3")
        await pilot.pause()
        assert app.focused is app.query_one("#agent-tabs", TabbedContent).query_one(
            Tabs
        )

        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is terminal

        await pilot.press("up", "down")
        await pilot.pause()

    assert written == [b"\x1b[A", b"\x1b[B"]


async def test_tui_joplin_reader_accepts_tab_focus_and_arrow_scrolling() -> None:
    class ReaderApp(App[None]):
        CSS = """
        #notes { height: 3; }
        #reader { height: 8; overflow-y: auto; }
        """

        def compose(self) -> ComposeResult:
            yield DataTable(id="notes")
            yield ReadingMarkdown(id="reader")

        def on_mount(self) -> None:
            self.query_one("#notes", DataTable).add_column("Title")

    app = ReaderApp()

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        reader = app.query_one("#reader", ReadingMarkdown)
        await reader.update("\n\n".join(f"## Section {index}\n\nBody" for index in range(80)))
        notes = app.query_one("#notes", DataTable)
        notes.focus()
        await pilot.pause()

        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is reader

        before = reader.scroll_y
        await pilot.press("down", "down", "down")
        await pilot.pause()
        assert reader.scroll_y > before

        await pilot.press("up")
        await pilot.pause()
        assert reader.scroll_y < before + 3


async def test_tui_priority_shortcuts_still_run_outside_terminal_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked: list[str] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    actions = (
        ("p", "p", "toggle_star_agent"),
        ("h", "h", "toggle_hidden_agents"),
        ("d", "d", "hide_agent"),
        ("u", "u", "resume_operator"),
        ("x", "x", "stop_operator"),
        ("y", "y", "operator_history"),
        ("shift+w", "W", "start_review_operator_fork"),
        ("shift+p", "P", "launch_project_spawn"),
        ("shift+d", "D", "purge_agent"),
        ("shift+m", "M", "monitor_campaign"),
        ("shift+r", "R", "view_campaign_report"),
        ("shift+c", "C", "copy_campaign_to_joplin"),
    )
    for _key, _character, action_name in actions:
        monkeypatch.setattr(
            app,
            f"action_{action_name}",
            lambda action_name=action_name: invoked.append(action_name),
        )

    async with app.run_test() as pilot:
        agents = app.query_one("#agents", DataTable)
        agents.focus()
        await pilot.pause()
        for key, character, _action_name in actions:
            await app.on_event(Key(key, character))

    assert invoked == [action_name for _key, _character, action_name in actions]


async def test_tui_discards_embedded_terminal_attach_after_selection_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    released: list[str] = []

    async def acquire(agent_id: str) -> dict[str, object]:
        assert agent_id == "operator-0"
        app.selected_agent_id = "agent-b"
        return {
            "state": "ready",
            "server_id": "server-a",
            "socket_path": "/tmp/pbx.sock",
            "session_name": "runtime-a",
            "window_id": "@12",
            "pane_id": "%7",
        }

    async def release(agent_id: str) -> None:
        released.append(agent_id)

    async with app.run_test():
        app.selected_agent_id = "operator-0"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        monkeypatch.setattr(app, "acquire_tmux_writer_lease", acquire)
        monkeypatch.setattr(app, "release_tmux_writer_lease", release)
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        attached: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            surface,
            "attach",
            lambda *args, **kwargs: attached.append((args, kwargs)),
        )

        assert (
            await app.attach_embedded_tmux_terminal(
                "operator-0",
                {
                    "state": "ready",
                    "server_id": "server-a",
                    "pane_id": "%7",
                },
            )
            is False
        )

    assert released == ["operator-0"]
    assert attached == []
    assert app.embedded_terminal_agent_id is None


async def test_tui_direct_entity_switch_keeps_native_surface_during_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    class_states: list[bool] = []
    rendered_during_attach: list[str] = []
    released: list[str] = []

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def close(self) -> None:
            self.alive = False

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    mapping = {
        "state": "ready",
        "server_id": "server-b",
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-b",
        "window_id": "@12",
        "pane_id": "%8",
        "cwd": "/tmp",
    }

    async def fetch(agent_id: str) -> dict[str, object]:
        assert agent_id == "agent-b"
        return mapping

    async def acquire(agent_id: str) -> dict[str, object]:
        assert agent_id == "agent-b"
        return mapping

    async def release(agent_id: str) -> None:
        released.append(agent_id)

    async def load_thread(_agent_id: str) -> None:
        return None

    async def load_runtime(_agent_id: str) -> None:
        return None

    async with app.run_test(size=(120, 32)) as pilot:
        app.agents = {
            "agent-a": {
                "agent_id": "agent-a",
                "project": "a",
                "status": "working",
                "last_seen_at": 100.0,
            },
            "agent-b": {
                "agent_id": "agent-b",
                "project": "b",
                "status": "working",
                "last_seen_at": 101.0,
            },
        }
        app.selected_agent_id = "agent-a"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server-a:%7"
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        old_process = FakeProcess()
        surface.process = old_process  # type: ignore[assignment]
        surface.target = "server-a:%7"
        surface.terminal.feed(b"stale agent-a frame")
        app.apply_tmux_class()
        await pilot.pause()

        original_apply = app.apply_tmux_class

        def tracked_apply() -> None:
            original_apply()
            try:
                screen = app.screen
            except Exception:
                return
            class_states.append(screen.has_class("embedded-terminal"))

        def attach(*_args: object, **kwargs: object) -> None:
            rendered_during_attach.append(surface.render().plain)
            surface.process = FakeProcess()  # type: ignore[assignment]
            surface.target = str(kwargs["target"])
            surface.end_transition()

        monkeypatch.setattr(app, "apply_tmux_class", tracked_apply)
        monkeypatch.setattr(app, "fetch_tmux_runtime_mapping", fetch)
        monkeypatch.setattr(app, "acquire_tmux_writer_lease", acquire)
        monkeypatch.setattr(app, "release_tmux_writer_lease", release)
        monkeypatch.setattr(app, "load_thread", load_thread)
        monkeypatch.setattr(app, "load_codex_runtime_status", load_runtime)
        monkeypatch.setattr(surface, "attach", attach)

        await app.select_agent("agent-b")
        await pilot.pause()

        assert app.selected_agent_id == "agent-b"
        assert app.embedded_terminal_agent_id == "agent-b"
        assert app.embedded_terminal_target == "server-b:%8"
        assert app.embedded_terminal_transition_agent_id is None
        assert surface.target == "server-b:%8"
        assert app.screen.has_class("embedded-terminal")
        assert released == ["agent-a"]
        assert class_states
        assert all(class_states)
        assert rendered_during_attach
        assert "Switching native terminal to agent-b" in rendered_during_attach[0]
        assert "stale agent-a frame" not in rendered_during_attach[0]
        monkeypatch.setattr(app, "apply_tmux_class", original_apply)


async def test_tui_waits_for_visible_terminal_layout_before_cold_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    attached_sizes: list[tuple[int, int]] = []

    class FakeProcess:
        alive = True

        def __init__(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

        def close(self) -> None:
            self.alive = False

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

    async def acquire(_agent_id: str) -> dict[str, object]:
        return {
            "state": "ready",
            "server_id": "server-a",
            "socket_path": "/tmp/pbx.sock",
            "session_name": "runtime-a",
            "window_id": "@12",
            "pane_id": "%7",
            "cwd": "/tmp",
        }

    async def release(_agent_id: str) -> None:
        return None

    async with app.run_test(size=(120, 32)) as pilot:
        app.selected_agent_id = "operator-0"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        monkeypatch.setattr(app, "acquire_tmux_writer_lease", acquire)
        monkeypatch.setattr(app, "release_tmux_writer_lease", release)
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        assert surface.size == (0, 0)

        def attach(*_args: object, **kwargs: object) -> None:
            attached_sizes.append((surface.size.width, surface.size.height))
            surface.process = FakeProcess(  # type: ignore[assignment]
                surface.size.width,
                surface.size.height,
            )
            surface.target = str(kwargs["target"])

        monkeypatch.setattr(surface, "attach", attach)
        assert (
            await app.attach_embedded_tmux_terminal(
                "operator-0",
                {
                    "state": "ready",
                    "server_id": "server-a",
                    "pane_id": "%7",
                },
            )
            is True
        )
        await pilot.pause()
        initial_size = attached_sizes[0]
        await pilot.resize_terminal(160, 32)
        await pilot.pause()
        # A compound Textual layout can emit an intermediate Resize before
        # borders and sibling widgets settle. The surface's regular PTY poll
        # closes that final geometry gap without app-level layout callbacks.
        surface.poll_pty()
        assert surface.process is not None
        assert (surface.process.columns, surface.process.rows) == (
            surface.size.width,
            surface.size.height,
        )
        assert (surface.size.width, surface.size.height) != initial_size

        # Hide the terminal, resize PBX while it has no live rectangle, and
        # attach again. The stale non-zero size from the prior visible layout
        # must not be accepted as the new PTY geometry.
        visible_size = (surface.size.width, surface.size.height)
        await app.detach_embedded_tmux_terminal(release_lease=True)
        await pilot.pause()
        await pilot.resize_terminal(200, 32)
        await pilot.pause()
        assert (
            await app.attach_embedded_tmux_terminal(
                "operator-0",
                {
                    "state": "ready",
                    "server_id": "server-a",
                    "pane_id": "%7",
                },
            )
            is True
        )
        await pilot.pause()
        # Focus and surrounding status/footer updates may settle one frame
        # after the disposable client starts. The regular PTY geometry sync is
        # authoritative; the initial attach still must use the resized width
        # rather than the previously hidden rectangle.
        surface.poll_pty()
        assert surface.process is not None
        assert (surface.process.columns, surface.process.rows) == (
            surface.size.width,
            surface.size.height,
        )
        assert attached_sizes[-1][0] > visible_size[0]

    assert attached_sizes
    assert attached_sizes[0][0] > 0
    assert attached_sizes[0][1] > 0
    assert attached_sizes[0] != (80, 24)


async def test_tui_managed_project_picker_loads_api_projects() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):  # type: ignore[no-untyped-def]
            return {
                "roots": ["/home/me/git"],
                "projects": [
                    {
                        "name": "demo",
                        "path": "/home/me/git/demo",
                        "branch": "dev",
                        "owned_by": [],
                        "available": True,
                    }
                ],
            }

    class Client:
        async def get(self, path, **_kwargs):  # type: ignore[no-untyped-def]
            assert path == "/v2/projects/discover"
            return Response()

        async def aclose(self) -> None:
            return None

    async with app.run_test() as pilot:
        if app.http_client is not None:
            await app.http_client.aclose()
            app.http_client = None
        app.api_client = lambda: Client()  # type: ignore[method-assign]
        await app.open_managed_project_picker()
        await pilot.pause()
        table = app.screen.query_one("#managed-projects", DataTable)
        assert table.row_count == 1
        assert app.screen.query_one("#managed-agent-profile", Select).value == "sol-high"


async def test_tui_runtime_migration_applies_only_eligible_candidates() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    restarted: list[str] = []
    posted: list[dict[str, object]] = []

    async def fake_restart(agent_id: str, **_kwargs) -> bool:  # type: ignore[no-untyped-def]
        restarted.append(agent_id)
        return True

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):  # type: ignore[no-untyped-def]
            return {"batch_id": "batch-1", "status": "complete"}

    class Client:
        async def post(self, path, **kwargs):  # type: ignore[no-untyped-def]
            posted.append({"path": path, **kwargs})
            return Response()

        async def aclose(self) -> None:
            return None

    app.restart_tmux_codex_session = fake_restart  # type: ignore[method-assign]
    batch = {
        "batch_id": "batch-1",
        "candidates": [
            {
                "agent_id": "agent-a",
                "eligible": True,
                "mapping": {"pane_id": "%1"},
                "blockers": [],
            },
            {
                "agent_id": "agent-b",
                "eligible": False,
                "mapping": None,
                "blockers": ["no mapping"],
            },
        ],
    }
    async with app.run_test():
        if app.http_client is not None:
            await app.http_client.aclose()
            app.http_client = None
        app.api_client = lambda: Client()  # type: ignore[method-assign]
        results = await app.apply_runtime_migration_batch(batch)

    assert restarted == ["agent-a"]
    assert results == [
        {"agent_id": "agent-a", "status": "complete"},
        {"agent_id": "agent-b", "status": "skipped", "reason": "no mapping"},
    ]
    assert posted[0]["path"] == "/v2/runtime-migrations/batch-1/results"


async def test_tui_layout_refresh_preserves_visible_events_focus() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(65, 20)
        await pilot.pause()
        app.action_focus_events()
        await pilot.pause()
        events = app.query_one("#events", DataTable)
        assert app.focused is events
        generation = app.focus_generation.snapshot()
        app.apply_layout_class()
        await pilot.pause()
        assert app.focused is events
        assert app.focus_generation.snapshot() >= generation


async def test_tui_plain_keys_focus_split_sections_for_tiny_terminals() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        agents = app.query_one("#agents", DataTable)
        events = app.query_one("#events", DataTable)
        tabs = app.query_one("#agent-tabs", TabbedContent).query_one(Tabs)
        message = app.query_one("#tmux-message", TextArea)

        await pilot.press("a")
        await pilot.pause()
        assert app.focused is agents

        await pilot.press("e")
        await pilot.pause()
        assert app.focused is events

        await pilot.press("v")
        await pilot.pause()
        assert app.focused is tabs

        await pilot.press("i")
        await pilot.pause()
        assert app.focused is message

        message.text = ""
        message.focus()
        await pilot.press("a")
        await pilot.pause()
        assert app.focused is message


async def test_tui_function_keys_switch_compact_views() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async def fake_refresh_selected_agent(agent_id: str) -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.refresh_selected_agent = fake_refresh_selected_agent  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

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
        app.render_agents()
        agents = app.query_one("#agents", DataTable)
        events = app.query_one("#events", DataTable)
        tabs = app.query_one("#agent-tabs", TabbedContent).query_one(Tabs)
        message = app.query_one("#tmux-message", TextArea)

        await pilot.press("f2")
        await pilot.pause()
        assert app.compact_view == "home"
        assert app.tiny_show_events is True
        assert app.focused is events

        await pilot.press("f3")
        await pilot.pause()
        assert app.compact_view == "agent"
        assert app.selected_agent_id == "agent-1"
        assert app.focused is tabs

        await pilot.press("f4")
        await pilot.pause()
        assert app.compact_view == "agent"
        assert app.active_agent_tab == "latest-tab"
        assert app.focused is message

        await pilot.press("f1")
        await pilot.pause()
        assert app.compact_view == "home"
        assert app.tiny_show_events is False
        assert app.focused is agents


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


def test_tui_shows_fork_ready_hint_for_stale_blocked_caller() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "status": "blocked",
            "metadata": {"codex_session_id": "session-1"},
        },
        "operator-0-fork-caller-1": {
            "agent_id": "operator-0-fork-caller-1",
            "agent_type": "operator",
            "status": "ready",
            "pbx_active": True,
            "metadata": {
                "operator_role": "fork",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "operator_fork_pending": False,
            },
        },
    }

    assert app.format_agent_status(app.agents["caller-1"]) == "blocked/fork-ready"


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
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=False)
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
        assert app.is_tmux_direct_enabled("agent-1") is False

        app.active_agent_tab = "latest-tab"
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.tmux_direct_enabled is False
        assert app.tmux_direct_agent_modes == {"agent-1": True}
        assert app.is_tmux_direct_enabled("agent-1") is True
        assert app.screen.has_class("tmux-direct")

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.tmux_direct_agent_modes == {"agent-1": False}
        assert app.is_tmux_direct_enabled("agent-1") is False

    assert captures == ["agent-1"]
    assert reports == ["agent-1"]


async def test_tui_disabling_direct_mode_detaches_and_cannot_reacquire_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    acquired: list[str] = []
    released: list[str] = []
    reports: list[str] = []

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, _data: bytes) -> None:
            return None

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    async def acquire(agent_id: str) -> dict[str, object]:
        acquired.append(agent_id)
        return {"state": "ready"}

    async def release(agent_id: str) -> None:
        released.append(agent_id)

    async def load_latest(agent_id: str) -> None:
        reports.append(agent_id)

    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-1"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-1"
        app.embedded_terminal_target = "server:%7"
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        process = FakeProcess()
        surface.process = process  # type: ignore[assignment]
        surface.target = "server:%7"
        app.apply_tmux_class()
        surface.focus()
        monkeypatch.setattr(app, "acquire_tmux_writer_lease", acquire)
        monkeypatch.setattr(app, "release_tmux_writer_lease", release)
        monkeypatch.setattr(app, "load_latest_report", load_latest)

        await app.action_toggle_tmux_direct()
        await pilot.pause()
        assert app.is_tmux_direct_enabled("agent-1") is False
        assert surface.attached is False
        assert process.alive is False
        assert app.embedded_terminal_agent_id is None
        assert app.embedded_terminal_target is None
        assert released == ["agent-1"]
        assert reports == ["agent-1"]

        await app.renew_embedded_tmux_writer_lease_if_active()
        assert acquired == []


@pytest.mark.parametrize("stale_state", ("direct", "tab", "selection", "process"))
async def test_tui_writer_renewal_releases_every_stale_embedded_state(
    stale_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    acquired: list[str] = []
    released: list[str] = []

    class FakeProcess:
        alive = True

        def close(self) -> None:
            self.alive = False

    async def acquire(agent_id: str) -> dict[str, object]:
        acquired.append(agent_id)
        return {"state": "ready"}

    async def release(agent_id: str) -> None:
        released.append(agent_id)

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.active_agent_tab = "latest-tab"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-1"
        app.embedded_terminal_target = "server:%7"
        surface = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        process = FakeProcess()
        surface.process = process  # type: ignore[assignment]
        surface.target = "server:%7"
        if stale_state == "direct":
            app.tmux_direct_agent_modes["agent-1"] = False
        elif stale_state == "tab":
            app.active_agent_tab = "thread-tab"
        elif stale_state == "selection":
            app.selected_agent_id = "agent-2"
        else:
            process.alive = False
        monkeypatch.setattr(app, "acquire_tmux_writer_lease", acquire)
        monkeypatch.setattr(app, "release_tmux_writer_lease", release)

        await app.renew_embedded_tmux_writer_lease_if_active()

        assert acquired == []
        assert released == ["agent-1"]
        assert app.embedded_terminal_agent_id is None
        assert app.embedded_terminal_target is None


async def test_tui_pop_transaction_reuses_only_resolved_live_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    mapping = {
        "server_mode": "outer_if_present",
        "server_id": "outer",
        "socket_path": "/tmp/pbx.sock",
        "session_name": "runtime-a",
        "pane_id": "%7",
        "origin_session_name": "agent-pbx",
        "origin_client_tty": None,
    }
    resolved = RuntimeTmuxClient(
        "/dev/pts/9",
        "agent-pbx",
        frozenset({"attached", "focused"}),
        90,
    )
    executed: list[tuple[str, ...]] = []

    async def fetch(_agent_id: str) -> dict[str, object]:
        return mapping

    async def detach(*, release_lease: bool) -> None:
        assert release_lease is True

    async def load(_agent_id: str) -> None:
        return None

    def execute(plan: object) -> SimpleNamespace:
        executed.append(plan.command)  # type: ignore[attr-defined]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("TMUX_PANE", "%2")
    monkeypatch.setattr(app, "fetch_tmux_runtime_mapping", fetch)
    monkeypatch.setattr(app, "detach_embedded_tmux_terminal", detach)
    monkeypatch.setattr(app, "load_tmux_capture", load)
    monkeypatch.setattr("agent_pbx.tui.resolve_invoking_outer_client", lambda *_args, **_kwargs: resolved)
    monkeypatch.setattr("agent_pbx.tui.execute_runtime_pop_plan", execute)
    app.selected_agent_id = "agent-1"

    assert await app.pop_runtime("out") is True
    assert executed[-1][-4:] == ("-c", "/dev/pts/9", "-t", "runtime-a")
    assert app.tmux_pop_client_by_agent["agent-1"] == resolved

    live = RuntimeTmuxClient(
        resolved.tty,
        "runtime-a",
        resolved.flags,
        resolved.client_pid,
    )
    monkeypatch.setattr("agent_pbx.tui.list_runtime_clients", lambda _identity: (live,))
    assert await app.pop_runtime("in") is True
    assert executed[-1][-4:] == ("-c", "/dev/pts/9", "-t", "agent-pbx")
    assert "agent-1" not in app.tmux_pop_client_by_agent


async def test_tui_tmux_runtime_mapping_uses_stable_window_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_local_direct_context = True
    app.tmux_runtime_server = TmuxServerIdentity(
        RuntimeServerMode.OUTER_IF_PRESENT,
        RuntimeServerMode.OUTER_IF_PRESENT,
        "outer-server",
        "/tmp/tmux-1000/default",
        True,
        True,
    )
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "metadata": {"codex_session_id": "session-1"},
        }
    }
    pane = tmux_support.TmuxPane(
        "agent-pbx-agents",
        "2",
        "0",
        "%9",
        True,
        "node",
        "agent-1",
        "/home/me/agent-pbx",
        100,
        30,
        0,
        "agent-1",
        True,
        0,
        "@42",
    )
    posted: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"state": "ready", "window_id": "@42", "pane_id": "%9"}

    class FakeClient:
        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeResponse:
            posted["url"] = url
            posted["json"] = json
            posted["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(
        "agent_pbx.tui.read_outer_tmux_context",
        lambda _identity: OuterTmuxContext("agent-pbx", "@1", "%1", "/dev/pts/1"),
    )
    monkeypatch.setattr(tmux_support, "pane_root_pid", lambda _pane_id: 1234)
    monkeypatch.setattr("agent_pbx.tui.runtime_process_start_ticks", lambda _pid: 5678)
    monkeypatch.setattr(app, "api_client", lambda: FakeClient())

    mapped = await app.ensure_tmux_runtime_mapping("agent-1", pane)

    assert mapped is True
    assert posted["url"] == "/v2/tmux/runtimes/agent-1"
    assert posted["json"]["window_id"] == "@42"  # type: ignore[index]
    assert app.tmux_runtime_mapping_signature_by_agent["agent-1"][2] == "@42"


async def test_tui_tmux_direct_is_tracked_per_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=False)
    captures: list[str] = []
    reports: list[str] = []
    threads: list[str] = []

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_load_latest_report(agent_id: str) -> None:
        reports.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "status": "done",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "project": "other",
                "status": "done",
                "last_seen_at": 124.0,
            },
        }
        await app.select_agent("agent-1")
        await pilot.pause()

        assert app.screen.has_class("tmux-direct") is False
        assert reports == ["agent-1"]

        await app.action_toggle_tmux_direct()
        await pilot.pause()

        assert app.tmux_direct_enabled is False
        assert app.tmux_direct_agent_modes == {"agent-1": True}
        assert app.screen.has_class("tmux-direct") is True
        assert captures == ["agent-1"]

        await app.select_agent("agent-2")
        await pilot.pause()

        assert app.screen.has_class("tmux-direct") is False
        assert app.is_tmux_direct_enabled("agent-2") is False
        assert reports[-1] == "agent-2"

        await app.select_agent("agent-1")
        await pilot.pause()

        assert app.screen.has_class("tmux-direct") is True
        assert captures[-1] == "agent-1"

    assert threads == ["agent-1", "agent-2", "agent-1"]


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
    assert pane == auto_pane
    assert mode == "auto"
    assert "agent-1" not in app.tmux_agent_targets

    app.tmux_agent_targets = {"agent-1": "%2"}
    app.tmux_manual_override_agent_ids.add("agent-1")
    pane, mode = app.resolve_tmux_pane("agent-1", [auto_pane, manual_pane])
    assert pane == manual_pane
    assert mode == "manual"

    app.tmux_detached_agent_ids.add("agent-1")
    pane, mode = app.resolve_tmux_pane("agent-1", [auto_pane, manual_pane])
    assert pane is None
    assert mode == "detached"


def test_tui_tmux_resolve_repairs_caller_target_bound_to_operator_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "project": "workerbee",
            "metadata": {"cwd": "/home/me/workerbee"},
        }
    }
    caller_pane = tmux_support.TmuxPane(
        "k1s",
        "0",
        "1",
        "%1",
        True,
        "node",
        "workerbee",
        "/home/me/workerbee",
        80,
        24,
        100,
        window_name="node",
    )
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%2",
        True,
        "node",
        "workerbee",
        "/home/me/workerbee",
        80,
        24,
        100,
        window_name="operator-0-fork-caller-1",
    )
    app.tmux_agent_targets = {"caller-1": "%2"}

    pane, mode = app.resolve_tmux_pane("caller-1", [fork_pane, caller_pane])

    assert pane == caller_pane
    assert mode == "auto"
    assert "caller-1" not in app.tmux_agent_targets


def test_tui_tmux_resolve_uses_repo_hints_when_caller_cwd_is_stale() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "codex-k1s-workerbee-private": {
            "agent_id": "codex-k1s-workerbee-private",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
            "metadata": {
                "cwd": "/home/me/git/agent-pbx",
                "repo": "/home/me/git/k1s-wt/k1s-workerbee-private",
                "work_repo": "/home/me/git/k1s-wt/k1s-workerbee-private",
                "repos": ["/home/me/git/k1s-wt/k1s-private"],
            },
        }
    }
    stale_cwd_pane = tmux_support.TmuxPane(
        "agent-pbx",
        "0",
        "0",
        "%10",
        False,
        "node",
        "agent-pbx",
        "/home/me/git/agent-pbx",
        80,
        24,
        100,
        window_name="node",
    )
    caller_pane = tmux_support.TmuxPane(
        "k1s",
        "1",
        "5",
        "%135",
        True,
        "node",
        "k1s-workerbee-private",
        "/home/me/git/k1s-wt/k1s-workerbee-private",
        80,
        24,
        100,
        window_name="node",
    )
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "14",
        "0",
        "%226",
        True,
        "node",
        "operator-0-codex-k1s-workerbee-private-review-4",
        (
            "/home/me/git/k1s-wt/.agent-pbx-review/"
            "operator-0-codex-k1s-workerbee-private-review-4"
        ),
        80,
        24,
        100,
        window_name="operator-0-fork-codex-k1s-workerbee-private-review-4",
    )

    pane, mode = app.resolve_tmux_pane(
        "codex-k1s-workerbee-private",
        [stale_cwd_pane, fork_pane, caller_pane],
    )

    assert pane == caller_pane
    assert mode == "auto"


def test_tui_tmux_resolve_recovers_root_operator_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": "/home/me/agent-pbx",
            },
        }
    }
    root_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "0",
        "0",
        "%1",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        80,
        24,
        100,
        window_name="operator-0",
    )
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%2",
        True,
        "node",
        "workerbee",
        "/home/me/workerbee",
        80,
        24,
        100,
        window_name="operator-0-fork-caller-1",
    )

    pane, mode = app.resolve_tmux_pane("operator-0", [fork_pane, root_pane])

    assert pane == root_pane
    assert mode == "auto"


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
        assert (
            str(app.query_one("#agent-title").renderable)
            == "Agent: agent-1 | View: pbx | Plan: off"
        )
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


async def test_tui_tiny_agents_columns_include_project(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "tiny")
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "effective_status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_created_at": 123.0,
            }
        }
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.desired_agent_columns()[:6] == (
        "*",
        "New",
        "Agent",
        "Status",
        "Project",
        "Queue",
    )
    assert row[3] == "running"
    assert row[4] == "agent-pbx"


async def test_tui_operator_split_filters_callers_and_operators() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "status": "running",
                "effective_status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "effective_status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        agents = app.query_one("#agents", DataTable)
        operators = app.query_one("#operators", DataTable)
        operators_title = app.query_one("#operators-title", Static)
        operator_actions = app.query_one("#operator-actions")

    assert [str(row.key.value) for row in agents.ordered_rows] == ["agent-1"]
    assert [str(row.key.value) for row in operators.ordered_rows] == ["operator-0"]
    assert operators_title.styles.display == "block"
    assert operators.styles.display == "block"
    assert operator_actions.styles.display == "block"
    assert operators.get_row("operator-0")[2].plain == "operator-0"


async def test_tui_operator_review_button_fits_wide_split() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(380, 63)
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "effective_status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        await pilot.pause()
        operator_actions = app.query_one("#operator-actions")
        review = app.query_one("#operator-fork-review", Button)

    assert operator_actions.styles.display == "block"
    assert review.label.plain == "Review W"
    assert review.region.right <= operator_actions.region.right


async def test_tui_operator_review_hotkey_uses_operator_table_cursor() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    reviewed: list[str | None] = []

    async def fake_start_review_operator_fork() -> None:
        reviewed.append(app.selected_operator_agent_id())

    app.start_review_operator_fork = fake_start_review_operator_fork  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "status": "working",
                "project": "demo",
                "last_seen_at": 123.0,
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "effective_status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        app.selected_agent_id = "caller-1"
        operators = app.query_one("#operators", DataTable)
        operators.focus()
        operators.move_cursor(row=0, animate=False, scroll=True)
        await pilot.pause()
        await pilot.press("shift+w")
        await pilot.pause()

    assert reviewed == ["operator-0"]


def test_tui_operator_review_source_infers_live_terminal_default_fork() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    fork_agent_id = "operator-0-fork-caller-1-abc12345"
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "status": "complete",
            "effective_status": "complete",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        fork_agent_id: {
            "agent_id": fork_agent_id,
            "agent_type": "operator",
            "status": "done",
            "effective_status": "done",
            "pbx_active": True,
            "project": "demo",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "fork_track_id": "default",
                "fork_purpose": "edit",
                "operator_fork_pending": False,
                "tmux_pane_id": "%151",
            },
        },
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "status": "complete",
            "project": "demo",
            "metadata": {"codex_session_id": "session-1"},
        },
    }
    app.tmux_agent_targets[fork_agent_id] = "%151"
    app.tmux_panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "1",
            "0",
            "%151",
            True,
            "node",
            fork_agent_id,
            "/repo",
            120,
            30,
            120,
            window_name=fork_agent_id,
        )
    ]

    assert app.source_caller_agent_id_for_review_fork("operator-0") == "caller-1"


async def test_tui_operator_split_shows_operator_forks() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                },
                "last_seen_at": 124.0,
            },
            "operator-0-fork-agent-1": {
                "agent_id": "operator-0-fork-agent-1",
                "agent_type": "operator",
                "status": "ready",
                "project": "agent-pbx",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "agent-1",
                    "source_codex_session_id": "session-1",
                    "cwd": "/home/me/agent-pbx",
                },
                "last_seen_at": 123.0,
            },
        }
        app.render_agents()
        operators = app.query_one("#operators", DataTable)

    assert [str(row.key.value) for row in operators.ordered_rows] == [
        "operator-0",
        "operator-0-fork-agent-1",
    ]


async def test_tui_cycles_operator_fork_panes(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    fork_agent_id = "operator-0-fork-agent-1"
    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "1",
            "0",
            "%1",
            True,
            "node",
            fork_agent_id,
            "/home/me/agent-pbx",
            120,
            32,
            100,
            window_name=fork_agent_id,
        ),
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "2",
            "0",
            "%2",
            True,
            "node",
            fork_agent_id,
            "/home/me/agent-pbx",
            120,
            32,
            100,
            window_name=fork_agent_id,
        ),
    ]
    refreshed: list[str] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return panes

    async def fake_refresh_selected_agent(agent_id: str) -> None:
        refreshed.append(agent_id)

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    app.refresh_selected_agent = fake_refresh_selected_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                },
                "last_seen_at": 124.0,
            },
            fork_agent_id: {
                "agent_id": fork_agent_id,
                "agent_type": "operator",
                "status": "ready",
                "project": "agent-pbx",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "agent-1",
                    "source_codex_session_id": "session-1",
                    "cwd": "/home/me/agent-pbx",
                },
                "last_seen_at": 123.0,
            },
        }
        app.selected_agent_id = "operator-0"
        app.render_agents()

        await app.cycle_selected_operator_fork(1)
        first_target = app.tmux_agent_targets[fork_agent_id]
        await app.cycle_selected_operator_fork(1)
        second_target = app.tmux_agent_targets[fork_agent_id]
        detail = app.query_one("#detail", TextArea).text

    assert first_target == "%1"
    assert second_target == "%2"
    assert refreshed == [fork_agent_id, fork_agent_id]
    assert app.selected_agent_id == fork_agent_id
    assert app.tmux_direct_agent_modes[fork_agent_id] is True
    assert fork_agent_id in app.tmux_manual_override_agent_ids
    assert app.selected_operator_fork_target_by_operator["operator-0"] == (
        f"{fork_agent_id}:%2"
    )
    assert "Viewing fork pane 2/2 for operator-0." in detail


def test_tui_operator_fork_targets_ignore_stale_caller_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    fork_agent_id = "operator-0-fork-caller-1"
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"operator_role": "root"},
        },
        fork_agent_id: {
            "agent_id": fork_agent_id,
            "agent_type": "operator",
            "project": "demo",
            "status": "running",
            "pbx_active": True,
            "metadata": {
                "operator_role": "root",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "tmux_pane_id": "%1",
            },
        },
    }
    caller_pane = tmux_support.TmuxPane(
        "k1s",
        "0",
        "1",
        "%1",
        True,
        "node",
        "caller-1",
        "/home/me/demo",
        100,
        30,
        100,
        window_name="zsh",
    )
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%2",
        True,
        "node",
        fork_agent_id,
        "/home/me/agent-pbx",
        100,
        30,
        100,
        window_name=fork_agent_id,
    )
    app.tmux_agent_targets[fork_agent_id] = "%1"

    targets = app.operator_fork_pane_targets("operator-0", [caller_pane, fork_pane])

    assert [(fork["agent_id"], pane.pane_id) for fork, pane in targets] == [
        (fork_agent_id, "%2")
    ]
    assert app.operator_role(app.agents[fork_agent_id]) == "fork"
    assert fork_agent_id not in app.tmux_agent_targets


def test_tui_reconciles_operator_fork_target_from_window_name() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    fork_agent_id = "operator-0-fork-caller-1"
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"operator_role": "root"},
        },
        fork_agent_id: {
            "agent_id": fork_agent_id,
            "agent_type": "operator",
            "project": "demo",
            "status": "running",
            "pbx_active": True,
            "metadata": {
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "tmux_pane_id": "%135",
            },
        },
    }
    caller_pane = tmux_support.TmuxPane(
        "k1s",
        "1",
        "5",
        "%135",
        True,
        "node",
        "caller-1",
        "/home/me/demo",
        100,
        30,
        100,
        window_name="zsh",
    )
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%151",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        100,
        30,
        100,
        window_name=fork_agent_id,
    )
    app.tmux_agent_targets[fork_agent_id] = "%135"
    app.tmux_manual_override_agent_ids.add(fork_agent_id)
    app.selected_operator_fork_target_by_operator["operator-0"] = f"{fork_agent_id}:%135"

    changed = app.reconcile_operator_tmux_targets_from_panes([caller_pane, fork_pane])

    assert changed is True
    assert app.tmux_agent_targets[fork_agent_id] == "%151"
    assert app.tmux_direct_agent_modes[fork_agent_id] is True
    assert app.agents[fork_agent_id]["metadata"]["tmux_pane_id"] == "%151"
    assert "operator-0" not in app.selected_operator_fork_target_by_operator


def test_tui_reconcile_preserves_saved_target_when_operator_panes_duplicate() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"operator_role": "root"},
        }
    }
    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "0",
            "0",
            "%167",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "8",
            "0",
            "%169",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
    ]
    app.tmux_agent_targets["operator-0"] = "%169"

    changed = app.reconcile_operator_tmux_targets_from_panes(panes)

    assert changed is True
    assert app.tmux_agent_targets["operator-0"] == "%169"
    assert app.agents["operator-0"]["metadata"]["tmux_pane_id"] == "%169"


def test_tui_reconcile_does_not_guess_when_operator_panes_duplicate() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"operator_role": "root"},
        }
    }
    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "0",
            "0",
            "%167",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "8",
            "0",
            "%169",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
    ]

    changed = app.reconcile_operator_tmux_targets_from_panes(panes)

    assert changed is False
    assert "operator-0" not in app.tmux_agent_targets


def test_tui_reconcile_clears_inactive_operator_fork_without_live_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    fork_agent_id = "operator-0-fork-caller-review-4"
    app.agents = {
        fork_agent_id: {
            "agent_id": fork_agent_id,
            "agent_type": "operator",
            "project": "demo",
            "status": "canceled",
            "effective_status": "canceled",
            "pbx_active": False,
            "metadata": {
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "tmux_pane_id": "%226",
            },
        }
    }
    app.tmux_agent_targets[fork_agent_id] = "%226"
    app.tmux_direct_agent_modes[fork_agent_id] = True
    app.tmux_manual_override_agent_ids.add(fork_agent_id)
    app.tmux_liveness_by_agent[fork_agent_id] = "live"
    app.tmux_plan_selector_agent_ids.add(fork_agent_id)
    app.tmux_plan_selector_pane_by_agent[fork_agent_id] = "%226"
    app.tmux_plan_selector_indices_by_agent[fork_agent_id] = {1}

    changed = app.reconcile_operator_tmux_targets_from_panes([])

    assert changed is True
    assert fork_agent_id not in app.tmux_agent_targets
    assert fork_agent_id not in app.tmux_direct_agent_modes
    assert fork_agent_id not in app.tmux_manual_override_agent_ids
    assert fork_agent_id not in app.tmux_liveness_by_agent
    assert fork_agent_id not in app.tmux_plan_selector_agent_ids
    assert fork_agent_id not in app.tmux_plan_selector_pane_by_agent
    assert fork_agent_id not in app.tmux_plan_selector_indices_by_agent


async def test_tui_live_operator_root_pane_rejects_duplicate_without_saved_target(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "0",
            "0",
            "%167",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "8",
            "0",
            "%169",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
    ]
    monkeypatch.setattr(tmux_support, "list_panes", lambda: panes)

    with pytest.raises(RuntimeError, match="multiple live tmux panes match operator-0"):
        await app.live_operator_root_pane_id("operator-0")


def test_tui_saved_operator_pane_survives_mutable_tmux_label_drift() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    agent = {
        "agent_id": "operator-0",
        "agent_type": "operator",
        "metadata": {"operator_role": "root", "tmux_pane_id": "%167"},
    }
    pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "0",
        "0",
        "%167",
        True,
        "node",
        "Codex changed this title",
        "/home/me/agent-pbx",
        100,
        30,
        100,
        window_name="node",
    )
    app.agents = {"operator-0": agent}
    app.tmux_agent_targets["operator-0"] = "%167"

    assert app.saved_tmux_target_pane_for_agent(
        "operator-0", agent, [pane]
    ) == pane


async def test_tui_existing_operator_root_is_reused_before_launch_metadata_changes(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    preserved_cwd = "/home/me/preserved-operator-project"
    posts: list[dict[str, object]] = []
    pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "0",
        "0",
        "%167",
        True,
        "node",
        "Codex title",
        preserved_cwd,
        100,
        30,
        100,
        window_name="node",
    )

    class Response:
        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            assert path == "/v1/agents/register"
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]
            assert isinstance(body, dict)
            return Response(
                {
                    **body,
                    "status": "working",
                    "effective_status": "working",
                    "created_at": 1.0,
                    "last_seen_at": 1.0,
                }
            )

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "operator_role": "root",
                "cwd": preserved_cwd,
                "tmux_pane_id": "%167",
                "task_goal": "retain this",
            },
        }
    }
    app.tmux_agent_targets["operator-0"] = "%167"
    monkeypatch.setattr(tmux_support, "list_panes", lambda: [pane])
    monkeypatch.setattr(
        tmux_support,
        "launch_pane",
        lambda **_kwargs: pytest.fail("existing root must not launch a new pane"),
    )

    agent, pane_id, launched = await app.ensure_operator_root_from_tui(
        agent_id="operator-0",
        cwd="/home/me/tui-launch-directory",
        codex_command="codex",
        mcp_url="http://127.0.0.1:8765/mcp",
        session_name="agent-pbx-operators",
    )

    assert launched is False
    assert pane_id == "%167"
    assert agent["metadata"]["cwd"] == preserved_cwd
    assert agent["metadata"]["task_goal"] == "retain this"
    assert posts[0]["json"]["metadata"]["tmux_pane_id"] == "%167"  # type: ignore[index]


async def test_tui_escape_fans_out_to_operator_root_and_running_forks(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    fork_agent_id = "operator-0-fork-caller-1"
    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "0",
            "0",
            "%10",
            True,
            "node",
            "operator-0",
            "/home/me/agent-pbx",
            100,
            30,
            100,
            window_name="operator-0",
        ),
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "1",
            "0",
            "%11",
            True,
            "node",
            fork_agent_id,
            "/home/me/demo",
            100,
            30,
            100,
            window_name=fork_agent_id,
        ),
    ]
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return panes

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "working",
                "metadata": {"operator_role": "root"},
            },
            fork_agent_id: {
                "agent_id": fork_agent_id,
                "agent_type": "operator",
                "project": "demo",
                "status": "running",
                "pbx_active": True,
                "metadata": {
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                    "source_codex_session_id": "session-1",
                    "operator_fork_pending": False,
                },
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        await app.send_escape_key()

    assert sent == [("%10", "Escape"), ("%11", "Escape")]
    assert captures == ["operator-0"]


async def test_tui_operator_split_hidden_without_operators() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        operators = app.query_one("#operators", DataTable)
        operators_title = app.query_one("#operators-title", Static)
        operator_actions = app.query_one("#operator-actions")

    assert operators.row_count == 0
    assert operators_title.styles.display == "none"
    assert operators.styles.display == "none"
    assert operator_actions.styles.display == "none"


async def test_tui_tiny_operator_focus_shows_operator_panel(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "tiny")
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 123.0,
            }
        }
        app.render_agents()
        await pilot.press("o")
        await pilot.pause()
        operators = app.query_one("#operators", DataTable)
        agents = app.query_one("#agents", DataTable)
        events = app.query_one("#events", DataTable)
        has_operator_class = app.screen.has_class("tiny-operators")

    assert app.tiny_home_panel == "operators"
    assert has_operator_class is True
    assert operators.styles.display == "block"
    assert agents.styles.display == "none"
    assert events.styles.display == "none"


async def test_tui_operator_button_focus_resolves_operator_cursor() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "status": "running",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 123.0,
            },
        }
        app.render_agents()
        app.query_one("#operator-hide", Button).focus()
        resolved = app.selected_operator_agent_id()

    assert resolved == "operator-0"


async def test_tui_starred_agents_sort_above_unstarred_by_freshness(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PBX_TUI_LAYOUT", "tiny")
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(53, 20)
        await pilot.pause()
        app.agents = {
            "old-star": {
                "agent_id": "old-star",
                "status": "done",
                "project": "demo",
                "last_seen_at": 100.0,
            },
            "new-unstarred": {
                "agent_id": "new-unstarred",
                "status": "working",
                "project": "demo",
                "last_seen_at": 300.0,
            },
            "new-star": {
                "agent_id": "new-star",
                "status": "working",
                "project": "demo",
                "last_seen_at": 200.0,
            },
        }
        app.starred_agent_ids = {"old-star", "new-star"}
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        ordered = [str(row.key.value) for row in table.ordered_rows]

    assert ordered == ["new-star", "old-star", "new-unstarred"]
    assert table.get_row("new-star")[0] == "*"
    assert table.get_row("new-unstarred")[0] == ""


async def test_tui_freezes_agent_and_operator_order_only_while_table_focused() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-old": {
                "agent_id": "agent-old",
                "agent_type": "caller",
                "status": "working",
                "project": "demo",
                "last_seen_at": 100.0,
            },
            "agent-new": {
                "agent_id": "agent-new",
                "agent_type": "caller",
                "status": "working",
                "project": "demo",
                "last_seen_at": 200.0,
            },
            "operator-old": {
                "agent_id": "operator-old",
                "agent_type": "operator",
                "status": "working",
                "project": "demo",
                "last_seen_at": 100.0,
                "metadata": {"operator_role": "root"},
            },
            "operator-new": {
                "agent_id": "operator-new",
                "agent_type": "operator",
                "status": "working",
                "project": "demo",
                "last_seen_at": 200.0,
                "metadata": {"operator_role": "root"},
            },
        }
        app.render_agents()
        agents = app.query_one("#agents", DataTable)
        operators = app.query_one("#operators", DataTable)
        events = app.query_one("#events", DataTable)

        agents.focus()
        await pilot.pause()
        app.agents["agent-old"]["last_seen_at"] = 300.0
        app.render_agents()
        assert [str(row.key.value) for row in agents.ordered_rows] == [
            "agent-new",
            "agent-old",
        ]

        operators.focus()
        await pilot.pause()
        assert [str(row.key.value) for row in agents.ordered_rows] == [
            "agent-old",
            "agent-new",
        ]
        app.agents["operator-old"]["last_seen_at"] = 300.0
        app.render_agents()
        assert [str(row.key.value) for row in operators.ordered_rows] == [
            "operator-new",
            "operator-old",
        ]

        events.focus()
        await pilot.pause()
        assert [str(row.key.value) for row in operators.ordered_rows] == [
            "operator-old",
            "operator-new",
        ]


async def test_tui_toggle_star_agent_persists_and_defers_reorder_while_focused(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )
    app.queue_agent_star_sync = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "demo",
                "last_seen_at": 100.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "working",
                "project": "demo",
                "last_seen_at": 200.0,
            },
        }
        app.render_agents()
        app.focus_agent_row("agent-1")
        app.toggle_selected_agent_star()
        table = app.query_one("#agents", DataTable)
        cursor_agent_id = app.agent_id_at_cursor()
        ordered_while_focused = [str(row.key.value) for row in table.ordered_rows]
        starred_cell = table.get_row("agent-1")[0]
        saved = json.loads(settings_file.read_text(encoding="utf-8"))
        app.query_one("#events", DataTable).focus()
        await pilot.pause()
        ordered_after_blur = [str(row.key.value) for row in table.ordered_rows]

    assert app.starred_agent_ids == {"agent-1"}
    assert saved["starred_agent_ids"] == ["agent-1"]
    assert cursor_agent_id == "agent-1"
    assert ordered_while_focused == ["agent-2", "agent-1"]
    assert ordered_after_blur == ["agent-1", "agent-2"]
    assert starred_cell == "*"


def test_tui_syncs_starred_agents_from_server_state() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.starred_agent_ids = {"local-stale"}
    app.local_starred_agent_ids = {"local-stale"}
    app.agents = {
        "local-stale": {
            "agent_id": "local-stale",
            "status": "done",
            "project": "demo",
            "last_seen_at": 100.0,
            "starred": False,
            "starred_at": None,
        },
        "remote-star": {
            "agent_id": "remote-star",
            "status": "done",
            "project": "demo",
            "last_seen_at": 200.0,
            "starred": True,
            "starred_at": 123.0,
        },
    }

    changed = app.sync_starred_from_agent_refresh()

    assert changed is True
    assert app.starred_agent_ids == {"remote-star"}


def test_tui_migrates_local_starred_agents_when_server_has_none() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, bool]] = []
    app.starred_agent_ids = {"agent-1"}
    app.local_starred_agent_ids = {"agent-1"}
    app.queue_agent_star_sync = (  # type: ignore[method-assign]
        lambda agent_id, starred: queued.append((agent_id, starred))
    )
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "demo",
            "last_seen_at": 100.0,
            "starred": False,
            "starred_at": None,
        },
        "agent-2": {
            "agent_id": "agent-2",
            "status": "done",
            "project": "demo",
            "last_seen_at": 200.0,
            "starred": False,
            "starred_at": None,
        },
    }

    changed = app.sync_starred_from_agent_refresh()

    assert changed is False
    assert app.starred_agent_ids == {"agent-1"}
    assert queued == [("agent-1", True)]


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


async def test_tui_workerbee_open_keeps_agent_selection_and_loads_status() -> None:
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
        await app.open_workerbee_for_agent("agent-1")

        assert tabs.active == "workerbee-tab"
        assert app.active_agent_tab == "workerbee-tab"
        assert loaded == ["agent-1"]


async def test_tui_pull_requests_open_keeps_agent_selection_and_loads() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[str] = []

    async def fake_load_latest_report(agent_id: str) -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    async def fake_load_pull_requests(agent_id: str) -> None:
        loaded.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]
    app.load_pull_requests = fake_load_pull_requests  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        tabs = app.query_one("#agent-tabs")
        await app.open_pull_requests_for_agent("agent-1")

        assert tabs.active == "pull-requests-tab"
        assert app.active_agent_tab == "pull-requests-tab"
        assert loaded == ["agent-1"]


async def test_tui_preserves_per_agent_tab_and_drafts() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async def fake_load_latest_report(agent_id: str) -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    async def fake_load_workerbee_status(agent_id: str) -> None:
        return None

    async def fake_load_pull_requests(agent_id: str) -> None:
        return None

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]
    app.load_workerbee_status = fake_load_workerbee_status  # type: ignore[method-assign]
    app.load_pull_requests = fake_load_pull_requests  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "one",
                "last_seen_at": 1.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "two",
                "last_seen_at": 2.0,
            },
        }

        await app.select_agent("agent-1")
        app.activate_agent_tab("pull-requests-tab")
        app.set_agent_draft_text(
            "agent-1",
            app.query_one("#message", TextArea),
            "draft for one",
        )
        app.set_agent_draft_text(
            "agent-1",
            app.query_one("#tmux-message", TextArea),
            "tmux draft for one",
        )

        await app.select_agent("agent-2")
        assert app.active_agent_tab == "latest-tab"
        assert app.query_one("#message", TextArea).text == ""
        app.activate_agent_tab("workerbee-tab")
        app.set_agent_draft_text(
            "agent-2",
            app.query_one("#message", TextArea),
            "draft for two",
        )

        await app.select_agent("agent-1")
        assert app.active_agent_tab == "pull-requests-tab"
        assert app.query_one("#message", TextArea).text == "draft for one"
        assert app.query_one("#tmux-message", TextArea).text == "tmux draft for one"

        await app.select_agent("agent-2")
        assert app.active_agent_tab == "workerbee-tab"
        assert app.query_one("#message", TextArea).text == "draft for two"


def test_tui_formats_pull_request_detail() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    text = app.format_pull_request_detail(
        {
            "number": 7,
            "title": "Improve PR support",
            "repo": "owner/repo",
            "url": "https://github.com/owner/repo/pull/7",
            "state": "OPEN",
            "is_draft": False,
            "author": "dev",
            "head_ref": "feature/prs",
            "base_ref": "dev",
            "checks": {"total": 1, "success": 1, "failed": 0, "pending": 0},
            "labels": ["enhancement"],
            "files": [{"path": "src/agent_pbx/pull_requests.py", "additions": 10}],
            "commits": [{"oid": "abc123"}],
            "body": "Adds PR workflow.",
        }
    )

    assert "PR #7 - Improve PR support" in text
    assert "Repo: owner/repo" in text
    assert "Checks: 1 ok/1" in text
    assert "src/agent_pbx/pull_requests.py" in text


def test_tui_formats_issue_detail() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    text = app.format_issue_detail(
        {
            "number": 9,
            "title": "Fix issue workflow",
            "repo": "owner/repo",
            "url": "https://github.com/owner/repo/issues/9",
            "state": "OPEN",
            "author": "reporter",
            "labels": ["bug"],
            "assignees": ["dev"],
            "milestone": "v1",
            "updated_at": "2026-06-10T12:00:00Z",
            "body": "Something needs mitigation.",
            "comments": [{"author": "reviewer", "body": "Confirmed."}],
        }
    )

    assert "Issue #9 - Fix issue workflow" in text
    assert "Repo: owner/repo" in text
    assert "Labels: bug" in text
    assert "Confirmed." in text


def test_tui_operator_source_scopes_joplin_and_repo_context() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    fork_agent_id = "operator-0-fork-caller-1"
    legacy_fork_agent_id = "operator-legacy-fork-caller-1"
    app.agents = {
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
            "metadata": {"cwd": "/home/me/k1s-workerbee-private"},
        },
        "caller-2": {
            "agent_id": "caller-2",
            "agent_type": "caller",
            "project": "k1s-private",
            "metadata": {"cwd": "/home/me/k1s-private"},
        },
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "default_source_caller_agent_id": "caller-1",
                "default_source_caller_project": "k1s-workerbee-private",
                "default_source_codex_session_id": "session-1",
            },
        },
        fork_agent_id: {
            "agent_id": fork_agent_id,
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_caller_project": "k1s-workerbee-private",
                "source_codex_session_id": "session-1",
            },
        },
        "operator-legacy": {
            "agent_id": "operator-legacy",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
            },
        },
        legacy_fork_agent_id: {
            "agent_id": legacy_fork_agent_id,
            "agent_type": "operator",
            "project": "k1s-workerbee-private",
            "status": "running",
            "pbx_active": True,
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-legacy",
                "source_caller_agent_id": "caller-1",
                "source_caller_project": "k1s-workerbee-private",
                "source_codex_session_id": "session-1",
                "operator_fork_pending": False,
            },
        },
        "operator-multi": {
            "agent_id": "operator-multi",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
            },
        },
        "operator-multi-fork-caller-1": {
            "agent_id": "operator-multi-fork-caller-1",
            "agent_type": "operator",
            "project": "k1s-workerbee-private",
            "status": "running",
            "pbx_active": True,
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-multi",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "operator_fork_pending": False,
            },
        },
        "operator-multi-fork-caller-2": {
            "agent_id": "operator-multi-fork-caller-2",
            "agent_type": "operator",
            "project": "k1s-private",
            "status": "running",
            "pbx_active": True,
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-multi",
                "source_caller_agent_id": "caller-2",
                "source_codex_session_id": "session-2",
                "operator_fork_pending": False,
            },
        },
    }
    app.joplin_notes_by_agent = {
        "caller-1": {"note-1": {"id": "note-1", "title": "Deploy Plan"}}
    }

    assert app.operator_role(app.agents[fork_agent_id]) == "fork"
    assert app.joplin_scope_agent_id(fork_agent_id) == "caller-1"
    assert app.joplin_project_for_agent(fork_agent_id) == "k1s-workerbee-private"
    assert app.current_joplin_note_title(fork_agent_id, "note-1") == "Deploy Plan"
    assert app.use_agent_joplin_note_scope("operator-0") is True
    assert (
        app.joplin_notes_url_for_agent("operator-0")
        == "/v1/agents/operator-0/joplin/notes"
    )
    assert (
        app.joplin_notes_url_for_agent("operator-0", "note-root")
        == "/v1/agents/operator-0/joplin/notes/note-root"
    )
    assert app.use_agent_joplin_note_scope(fork_agent_id) is False
    assert (
        app.joplin_notes_url_for_agent(fork_agent_id)
        == "/v1/projects/k1s-workerbee-private/joplin/notes"
    )
    assert app.repo_scope_agent_id("operator-0") == "caller-1"
    assert app.repo_scope_agent_id(fork_agent_id) == "caller-1"
    assert app.repo_scope_agent_id("caller-1") == "caller-1"
    assert app.repo_scope_agent_id("operator-legacy") == "caller-1"
    assert app.repo_scope_agent_id("operator-multi") == "operator-multi"


async def test_tui_pull_request_review_queues_with_delivery_note() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    posts: list[tuple[str, dict[str, object]]] = []
    threads: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"command": {"command_id": "cmd-1"}}

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "metadata": {"pbx_mode": "report"},
            }
        }
        app.pull_requests_by_agent = {"agent-1": {7: {"number": 7}}}
        app.selected_pull_request_number = 7
        await app.request_pull_request_review("agent-1")
        detail = app.query_one("#pull-request-detail", TextArea).text

    assert posts == [
        ("/v1/agents/agent-1/pull-requests/7/review-request", {"queue": True})
    ]
    assert threads == ["agent-1"]
    assert "Queued PR #7 review for agent-1." in detail
    assert "Command: cmd-1" in detail
    assert "requires Agent PBX nohup mode" in detail


async def test_tui_operator_pr_review_uses_source_repo_and_operator_tmux() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    posts: list[tuple[str, dict[str, object]]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []
    logged: list[tuple[str, str]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "prompt": "Review source repo PR #7.",
                "repo": "the-cm-collective/k1s-workerbee-private",
                "command": None,
            }

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_record_tmux_joplin_interaction(agent_id: str, message: str) -> None:
        logged.append((agent_id, message))

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]
    app.tmux_features_available = True
    app.tmux_direct_enabled = True

    async with app.run_test():
        app.tmux_features_available = True
        app.tmux_direct_enabled = True
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "k1s-workerbee-private",
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                    "default_source_caller_project": "k1s-workerbee-private",
                    "default_source_codex_session_id": "session-1",
                },
            },
        }
        app.pull_requests_by_agent = {"operator-0": {7: {"number": 7}}}
        app.selected_pull_request_number = 7
        await app.request_pull_request_review("operator-0")
        detail = app.query_one("#pull-request-detail", TextArea).text

    assert posts == [
        ("/v1/agents/caller-1/pull-requests/7/review-request", {"queue": False})
    ]
    assert sent == [("operator-0", "Review source repo PR #7.")]
    assert logged == sent
    assert captures == ["operator-0"]
    assert "using repo context caller-1" in detail


async def test_tui_issue_mitigation_queues_with_delivery_note() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    posts: list[tuple[str, dict[str, object]]] = []
    threads: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"command": {"command_id": "cmd-1"}}

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "metadata": {"pbx_mode": "report"},
            }
        }
        app.issues_by_agent = {"agent-1": {9: {"number": 9}}}
        app.selected_issue_number = 9
        await app.request_issue_mitigation("agent-1")
        detail = app.query_one("#issue-detail", TextArea).text

    assert posts == [
        ("/v1/agents/agent-1/issues/9/mitigation-request", {"queue": True})
    ]
    assert threads == ["agent-1"]
    assert "Queued issue #9 mitigation for agent-1." in detail
    assert "Command: cmd-1" in detail
    assert "requires Agent PBX nohup mode" in detail


async def test_tui_operator_issue_mitigation_uses_source_repo_and_operator_tmux() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    posts: list[tuple[str, dict[str, object]]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []
    logged: list[tuple[str, str]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "prompt": "Mitigate source repo issue #9.",
                "repo": "the-cm-collective/k1s-workerbee-private",
                "command": None,
            }

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_record_tmux_joplin_interaction(agent_id: str, message: str) -> None:
        logged.append((agent_id, message))

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]
    app.tmux_features_available = True
    app.tmux_direct_enabled = True

    async with app.run_test():
        app.tmux_features_available = True
        app.tmux_direct_enabled = True
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "k1s-workerbee-private",
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                    "default_source_caller_project": "k1s-workerbee-private",
                    "default_source_codex_session_id": "session-1",
                },
            },
        }
        app.issues_by_agent = {"operator-0": {9: {"number": 9}}}
        app.selected_issue_number = 9
        await app.request_issue_mitigation("operator-0")
        detail = app.query_one("#issue-detail", TextArea).text

    assert posts == [
        ("/v1/agents/caller-1/issues/9/mitigation-request", {"queue": False})
    ]
    assert sent == [("operator-0", "Mitigate source repo issue #9.")]
    assert logged == sent
    assert captures == ["operator-0"]
    assert "using repo context caller-1" in detail


async def test_tui_pull_request_validation_uses_tmux_direct_prompt() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    posts: list[tuple[str, dict[str, object]]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []
    logged: list[tuple[str, str]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "prompt": "Run appropriate WorkerBee validation for GitHub PR #7.",
                "command": None,
            }

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_record_tmux_joplin_interaction(agent_id: str, message: str) -> None:
        logged.append((agent_id, message))

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]
    app.tmux_features_available = True
    app.tmux_direct_enabled = True

    async with app.run_test():
        app.tmux_features_available = True
        app.tmux_direct_enabled = True
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.pull_requests_by_agent = {"agent-1": {7: {"number": 7}}}
        app.selected_pull_request_number = 7
        await app.request_pull_request_validation("agent-1")
        detail = app.query_one("#pull-request-detail", TextArea).text

    assert posts == [
        (
            "/v1/agents/agent-1/pull-requests/7/workerbee-validation-request",
            {"queue": False},
        )
    ]
    assert sent == [
        ("agent-1", "Run appropriate WorkerBee validation for GitHub PR #7.")
    ]
    assert logged == sent
    assert captures == ["agent-1"]
    assert "Sent PR #7 WorkerBee validation prompt to tmux" in detail


async def test_tui_issue_mitigation_uses_tmux_direct_prompt() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    posts: list[tuple[str, dict[str, object]]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []
    logged: list[tuple[str, str]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "prompt": "Mitigate GitHub Issue #9 in owner/repo.",
                "command": None,
            }

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append((path, dict(kwargs.get("json") or {})))
            return Response()

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_record_tmux_joplin_interaction(agent_id: str, message: str) -> None:
        logged.append((agent_id, message))

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]
    app.tmux_features_available = True
    app.tmux_direct_enabled = True

    async with app.run_test():
        app.tmux_features_available = True
        app.tmux_direct_enabled = True
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.issues_by_agent = {"agent-1": {9: {"number": 9}}}
        app.selected_issue_number = 9
        await app.request_issue_mitigation("agent-1")
        detail = app.query_one("#issue-detail", TextArea).text

    assert posts == [
        ("/v1/agents/agent-1/issues/9/mitigation-request", {"queue": False})
    ]
    assert sent == [("agent-1", "Mitigate GitHub Issue #9 in owner/repo.")]
    assert logged == sent
    assert captures == ["agent-1"]
    assert "Sent issue #9 mitigation prompt to tmux" in detail


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
        "dashboard_error": {
            "code": "WORKERBEE_TIMEOUT",
            "message": "WorkerBee command timed out after 20s",
            "retryable": True,
        },
    }

    rendered = app.format_workerbee_status(status)

    assert "Project" in rendered
    assert "Name: demo-dev-123" in rendered
    assert "No deployment recorded yet." in rendered
    assert "Global Dashboard" in rendered
    assert "Dashboard Warning" in rendered
    assert "Code: WORKERBEE_TIMEOUT" in rendered


def test_tui_workerbee_does_not_poll_while_active() -> None:
    source = inspect.getsource(AgentPBXTUI.on_mount)

    assert "refresh_workerbee_if_active" not in source
    assert "load_workerbee_status" not in source


async def test_tui_files_tab_loads_directory_and_preview() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> Response:
            params = params or {}
            calls.append((path, params))
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path.endswith("/files/preview"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params["path"],
                        "kind": "file",
                        "size": 12,
                        "mtime": 123.0,
                        "extension": ".py",
                        "mime_type": "text/x-python",
                        "is_text": True,
                        "is_image": False,
                        "is_gif": False,
                        "text": "print('ok')\n",
                        "truncated": False,
                        "error": None,
                    }
                )
            if params.get("path") == "src":
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": "src",
                        "parent": ".",
                        "entries": [
                            {
                                "name": "app.py",
                                "path": "src/app.py",
                                "kind": "file",
                                "size": 12,
                                "mtime": 123.0,
                                "extension": ".py",
                                "mime_type": "text/x-python",
                                "is_text": True,
                                "is_image": False,
                                "is_gif": False,
                            }
                        ],
                        "error": None,
                    }
                )
            return Response(
                {
                    "agent_id": "agent-1",
                    "cwd": "/repo",
                    "path": ".",
                    "parent": None,
                    "entries": [
                        {
                            "name": "src",
                            "path": "src",
                            "kind": "directory",
                            "size": None,
                            "mtime": 123.0,
                            "extension": "",
                            "mime_type": None,
                            "is_text": False,
                            "is_image": False,
                            "is_gif": False,
                        }
                    ],
                    "error": None,
                }
            )

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        await app.load_agent_files("agent-1")
        await app.select_file_entry("src")
        await app.select_file_entry("src/app.py")
        await pilot.pause()

        assert app.query_one("#file-path", Static).renderable == "Path: src"
        assert "print('ok')" in rich_log_plain(app.query_one("#file-preview", RichLog))
        assert "src" in app.file_directory_entries_by_agent["agent-1"]["."]
        assert "src/app.py" in app.file_directory_entries_by_agent["agent-1"]["src"]

    assert calls == [
        ("/v1/joplin/status", {}),
        ("/v1/codex/config", {}),
        ("/v1/agents", {}),
        ("/v1/events", {"tail": "true", "limit": 50}),
        ("/v1/agents/agent-1/files", {"path": "."}),
        ("/v1/agents/agent-1/files", {"path": "src"}),
        ("/v1/agents/agent-1/files/preview", {"path": "src/app.py"}),
    ]


async def test_tui_files_search_copy_and_open_editor(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, dict[str, str]]] = []
    copied: list[str] = []

    def fake_clipboard(text: str) -> str:
        copied.append(text)
        return "test clipboard"

    monkeypatch.setattr("agent_pbx.tui.write_clipboard_text", fake_clipboard)

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> Response:
            params = params or {}
            calls.append((path, params))
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path.endswith("/files/search"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": ".",
                        "query": params["query"],
                        "results": [
                            {
                                "path": "src/app.py",
                                "line": 3,
                                "column": 5,
                                "snippet": "def target():",
                            }
                        ],
                        "truncated": False,
                        "backend": "python",
                        "error": None,
                    }
                )
            if path.endswith("/files/preview"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params["path"],
                        "kind": "file",
                        "size": 25,
                        "mtime": 123.0,
                        "extension": ".py",
                        "mime_type": "text/x-python",
                        "is_text": True,
                        "is_image": False,
                        "is_gif": False,
                        "text": "def target():\n    return 1\n",
                        "truncated": False,
                        "error": None,
                    }
                )
            if path.endswith("/files/document"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params["path"],
                        "kind": "file",
                        "size": 25,
                        "mtime": 123.0,
                        "extension": ".py",
                        "mime_type": "text/x-python",
                        "is_text": True,
                        "text": "def target():\n    return 1\n",
                        "sha256": "old-hash",
                        "encoding": "utf-8",
                        "newline": "lf",
                        "language": "python",
                        "line_count": 2,
                        "read_only": False,
                        "saved": False,
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.file_path_by_agent["agent-1"] = "."
        search_input = app.query_one("#files-search-query", Input)
        search_input.value = "target"

        await app.search_agent_files("agent-1")
        await app.select_file_search_result("0:src/app.py:3:5")
        await app.copy_selected_file_path("agent-1")
        await app.copy_selected_file_text("agent-1")
        await app.open_selected_file_in_editor("agent-1")
        await pilot.pause()

        assert app.query_one("#file-search-results", DataTable).row_count == 1
        assert str(app.query_one("#file-search-status", Static).renderable) == (
            "Search: 1 match(es) via python"
        )
        assert "def target()" in rich_log_plain(app.query_one("#file-preview", RichLog))
        assert app.active_agent_tab == "editor-tab"
        assert app.query_one("#agent-tabs", TabbedContent).active == "editor-tab"
        assert app.query_one("#editor", TextArea).text == "def target():\n    return 1\n"
        assert "src/app.py" in str(app.query_one("#editor-status", Static).renderable)

    assert copied == ["src/app.py", "def target():\n    return 1\n"]
    assert (
        "/v1/agents/agent-1/files/search",
        {"query": "target", "path": "."},
    ) in calls


async def test_tui_file_crud_reconciles_editor_and_trash_prune_state() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    posts: list[tuple[str, dict[str, object]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> Response:
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/codex/config":
                return Response({"exists": False, "fields": [], "hidden_items": []})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path.endswith("/files/trash/prune-preview"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "cutoff": 100.0,
                        "count": 1,
                        "bytes": 10,
                        "entries": [{"trash_id": "trash-1"}],
                        "preview_token": "preview-prune",
                        "error": None,
                    }
                )
            if path.endswith("/files/preview"):
                file_path = str((params or {}).get("path") or "")
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": file_path,
                        "kind": "file",
                        "size": 0,
                        "mtime": 1.0,
                        "extension": ".py",
                        "mime_type": "text/x-python",
                        "is_text": True,
                        "is_image": False,
                        "is_gif": False,
                        "text": "",
                        "truncated": False,
                        "error": None,
                    }
                )
            if path.endswith("/files"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": str((params or {}).get("path") or "."),
                        "parent": ".",
                        "entries": [],
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

        async def post(
            self,
            path: str,
            *,
            json: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> Response:
            payload = json or {}
            posts.append((path, payload))
            if path.endswith("/files/create"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": payload["path"],
                        "kind": payload["kind"],
                        "created": True,
                        "error": None,
                    }
                )
            if path.endswith("/files/move"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": payload["destination"],
                        "source": payload["source"],
                        "destination": payload["destination"],
                        "kind": "directory",
                        "moved": True,
                        "error": None,
                    }
                )
            if path.endswith("/files/delete"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": payload["path"],
                        "deleted": True,
                        "trashed": True,
                        "trash_id": "trash-1",
                        "error": None,
                    }
                )
            if path.endswith("/files/trash/prune"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "batch_id": "batch-1",
                        "status": "applied",
                        "count": 1,
                        "trash_ids": ["trash-1"],
                        "error": None,
                    }
                )
            if path.endswith("/files/trash/prune-undo"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "batch_id": "batch-1",
                        "status": "undone",
                        "trash_ids": ["trash-1"],
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected POST {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.editor_documents_by_agent["agent-1"] = {
            "path": "src/pkg/app.py",
            "text": "print('ok')\n",
            "current_text": "print('ok')\n",
        }
        await app.apply_file_path_action(
            "agent-1",
            "move",
            "lib/pkg",
            source="src/pkg",
        )
        assert app.editor_documents_by_agent["agent-1"]["path"] == "lib/pkg/app.py"

        await app.apply_file_path_action("agent-1", "create_file", "lib/new.py")
        assert app.selected_file_path_by_agent["agent-1"] == "lib/new.py"

        app.editor_documents_by_agent["agent-1"] = {
            "path": "lib/pkg/app.py",
            "text": "print('ok')\n",
            "current_text": "print('ok')\n",
        }
        await app.apply_file_delete(
            "agent-1",
            {
                "path": "lib/pkg",
                "preview_token": "delete-preview",
                "delete_mode": "trash",
            },
        )
        assert "agent-1" not in app.editor_documents_by_agent

        await app.prune_file_trash("agent-1")
        assert app.latest_file_trash_prune_batch_by_agent["agent-1"] == "batch-1"
        await app.undo_file_trash_prune("agent-1")
        assert "agent-1" not in app.latest_file_trash_prune_batch_by_agent

    assert any(path.endswith("/files/move") for path, _payload in posts)
    assert any(path.endswith("/files/delete") for path, _payload in posts)
    assert any(path.endswith("/files/trash/prune") for path, _payload in posts)
    assert any(path.endswith("/files/trash/prune-undo") for path, _payload in posts)


async def test_tui_editor_persists_saves_and_toggles_fullscreen() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    saves: list[dict[str, object]] = []
    diagnostics_posts: list[dict[str, object]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> Response:
            params = params or {}
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path.endswith("/files/document"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params["path"],
                        "kind": "file",
                        "size": 10,
                        "mtime": 123.0,
                        "extension": ".py",
                        "mime_type": "text/x-python",
                        "is_text": True,
                        "text": "old = 1\n",
                        "sha256": "old-hash",
                        "encoding": "utf-8",
                        "newline": "lf",
                        "language": "python",
                        "line_count": 1,
                        "read_only": False,
                        "saved": False,
                        "error": None,
                    }
                )
            if path.endswith("/files"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params.get("path", "."),
                        "parent": None,
                        "entries": [],
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

        async def put(
            self,
            path: str,
            *,
            json: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> Response:
            assert path.endswith("/files/document")
            payload = json or {}
            saves.append(payload)
            return Response(
                {
                    "agent_id": "agent-1",
                    "cwd": "/repo",
                    "path": payload["path"],
                    "kind": "file",
                    "size": len(str(payload["text"]).encode()),
                    "mtime": 456.0,
                    "extension": ".py",
                    "mime_type": "text/x-python",
                    "is_text": True,
                    "text": payload["text"],
                    "sha256": "new-hash",
                    "encoding": "utf-8",
                    "newline": "lf",
                    "language": "python",
                    "line_count": 1,
                    "read_only": False,
                    "saved": True,
                    "error": None,
                }
            )

        async def post(
            self,
            path: str,
            *,
            json: dict[str, object] | None = None,
            **_kwargs: object,
        ) -> Response:
            assert path.endswith("/files/diagnostics")
            diagnostics_posts.append(json or {})
            return Response(
                {
                    "agent_id": "agent-1",
                    "cwd": "/repo",
                    "path": "src/app.py",
                    "tool": "auto",
                    "available_tools": ["ruff"],
                    "diagnostics": [
                        {
                            "path": "src/app.py",
                            "line": 1,
                            "column": 1,
                            "severity": "error",
                            "code": "F401",
                            "message": "unused import",
                            "source": "ruff",
                        }
                    ],
                    "backend": "ruff",
                    "error": None,
                }
            )

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        await app.open_editor_for_agent("agent-1", path="src/app.py")
        editor = app.query_one("#editor", TextArea)
        editor.load_text("")
        app.update_editor_dirty_state("agent-1")
        app.save_current_agent_pane_state("agent-1")
        app.selected_agent_id = "agent-2"
        app.editor_documents_by_agent["agent-2"] = {
            "path": "other.py",
            "text": "agent2 = True\n",
            "current_text": "agent2 = True\n",
        }
        app.save_current_agent_pane_state("agent-2")
        assert app.editor_documents_by_agent["agent-2"]["current_text"] == (
            "agent2 = True\n"
        )
        app.render_editor_for_agent("agent-2")
        assert editor.text == "agent2 = True\n"

        app.selected_agent_id = "agent-1"
        app.render_editor_for_agent("agent-1")
        assert editor.text == ""
        assert "agent-1" in app.editor_dirty_agent_ids

        await app.save_editor_document("agent-1")
        assert saves == [
            {
                "path": "src/app.py",
                "text": "",
                "previous_sha256": "old-hash",
                "previous_mtime": 123.0,
                "create": False,
            }
        ]
        assert "agent-1" not in app.editor_dirty_agent_ids
        assert app.editor_documents_by_agent["agent-1"]["sha256"] == "new-hash"

        await app.check_editor_document("agent-1")
        diagnostics_text = app.query_one("#editor-diagnostics", TextArea).text
        assert "Diagnostics: ruff" in diagnostics_text
        assert "src/app.py:1:1 error ruff [F401]: unused import" in diagnostics_text
        assert diagnostics_posts == [{"path": "src/app.py", "tool": "auto"}]

        app.activate_agent_tab("editor-tab")
        app.toggle_editor_fullscreen()
        await pilot.pause()
        assert app.editor_fullscreen is True
        assert app.query_one("#left").styles.display == "none"
        assert app.query_one("#right").styles.display == "block"

        app.toggle_editor_fullscreen()
        assert app.editor_fullscreen is False


async def test_tui_editor_close_clears_clean_buffer_and_fullscreen() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.editor_documents_by_agent["agent-1"] = {
            "path": "src/app.py",
            "text": "old = 1\n",
            "current_text": "old = 1\n",
            "language": "python",
            "line_count": 1,
        }
        app.activate_agent_tab("editor-tab")
        app.render_editor_for_agent("agent-1")
        app.toggle_editor_fullscreen()
        await pilot.pause()

        assert app.close_editor_document("agent-1") is True

        assert "agent-1" not in app.editor_documents_by_agent
        assert "agent-1" not in app.editor_dirty_agent_ids
        assert app.query_one("#editor", TextArea).text == ""
        assert str(app.query_one("#editor-status", Static).renderable) == "Editor: no file"
        assert app.query_one("#editor-diagnostics", TextArea).text == "Diagnostics: not run"
        assert app.active_agent_tab == "files-tab"
        assert app.editor_fullscreen is False


async def test_tui_editor_dirty_close_prompts_before_discarding() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.editor_documents_by_agent["agent-1"] = {
            "path": "src/app.py",
            "text": "old = 1\n",
            "current_text": "old = 1\n",
        }
        app.activate_agent_tab("editor-tab")
        app.render_editor_for_agent("agent-1")
        editor = app.query_one("#editor", TextArea)
        editor.load_text("new = 1\n")
        app.update_editor_dirty_state("agent-1")

        await app.request_close_editor_document("agent-1")
        await pilot.pause()

        assert isinstance(app.screen, EditorCloseConfirmScreen)
        app.screen.dismiss()
        await pilot.pause()
        assert "agent-1" in app.editor_documents_by_agent
        assert "agent-1" in app.editor_dirty_agent_ids

        assert app.close_editor_document("agent-1", discarded=True) is True
        assert "agent-1" not in app.editor_documents_by_agent
        assert "agent-1" not in app.editor_dirty_agent_ids


async def test_tui_editor_save_and_close_keeps_buffer_when_save_fails() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    saves: list[str | None] = []

    async def fake_save_editor_document(agent_id: str | None = None) -> bool:
        saves.append(agent_id)
        return False

    app.save_editor_document = fake_save_editor_document  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.editor_documents_by_agent["agent-1"] = {
            "path": "src/app.py",
            "text": "old = 1\n",
            "current_text": "new = 1\n",
        }

        await app.save_and_close_editor_document("agent-1")

        assert saves == ["agent-1"]
        assert "agent-1" in app.editor_documents_by_agent


async def test_tui_files_search_focus_helper() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    opened: list[str] = []

    async def fake_open_files_for_agent(agent_id: str) -> None:
        opened.append(agent_id)
        app.selected_agent_id = agent_id
        app.activate_agent_tab("files-tab")

    app.open_files_for_agent = fake_open_files_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()

        await app.open_files_search_for_agent("agent-1")
        await pilot.pause()

        assert opened == ["agent-1"]
        assert app.active_agent_tab == "files-tab"
        assert app.focused is app.query_one("#files-search-query", Input)


async def test_tui_editor_reveal_loads_files_and_preview() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> Response:
            params = params or {}
            calls.append((path, params))
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path.endswith("/files"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params.get("path", "."),
                        "parent": ".",
                        "entries": [],
                        "error": None,
                    }
                )
            if path.endswith("/files/preview"):
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": params["path"],
                        "kind": "file",
                        "is_text": True,
                        "text": "old = 1\n",
                        "truncated": False,
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.editor_documents_by_agent["agent-1"] = {
            "path": "src/app.py",
            "text": "old = 1\n",
            "current_text": "old = 1\n",
        }

        await app.reveal_editor_document_in_files("agent-1")

        assert app.active_agent_tab == "files-tab"
        assert app.file_path_by_agent["agent-1"] == "src"
        assert app.selected_file_path_by_agent["agent-1"] == "src/app.py"
        assert "old = 1" in rich_log_plain(app.query_one("#file-preview", RichLog))

    assert ("/v1/agents/agent-1/files", {"path": "src"}) in calls
    assert ("/v1/agents/agent-1/files/preview", {"path": "src/app.py"}) in calls


async def test_tui_joplin_unavailable_does_not_call_note_endpoints() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            calls.append(path)
            if path == "/v1/joplin/status":
                return Response(
                    {
                        "configured": True,
                        "available": False,
                        "notebook": "Agent PBX",
                        "error": {
                            "code": "JOPLIN_UNAVAILABLE",
                            "message": "[Errno 111] Connection refused",
                        },
                    }
                )
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.load_joplin_notes("agent-1")
        await pilot.pause()

        assert app.joplin_configured is True
        assert app.joplin_available is False
        reader = app.query_one("#joplin-reader", Markdown)
        assert "Connection refused" in str(getattr(reader, "source_markdown", ""))
        assert not any("/joplin/notes" in path for path in calls)


async def test_tui_file_completion_uses_cached_files() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.file_directory_entries_by_agent = {
            "agent-1": {
                ".": {
                    "README.md": {
                        "name": "README.md",
                        "path": "README.md",
                        "kind": "file",
                    },
                    "src": {
                        "name": "src",
                        "path": "src",
                        "kind": "directory",
                    },
                },
                "src": {
                    "src/app.py": {
                        "name": "app.py",
                        "path": "src/app.py",
                        "kind": "file",
                    },
                    "src/agent_pbx": {
                        "name": "agent_pbx",
                        "path": "src/agent_pbx",
                        "kind": "directory",
                    },
                },
            }
        }
        message = app.query_one("#message", TextArea)

        message.text = "review @RE"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "review @README.md"

        message.text = "open @sr"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "open @src/"

        message.text = "edit @src/ap"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "edit @src/app.py"


async def test_tui_file_completion_works_in_tmux_input() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.file_directory_entries_by_agent = {
            "agent-1": {
                ".": {
                    "docs": {
                        "name": "docs",
                        "path": "docs",
                        "kind": "directory",
                    },
                }
            }
        }
        message = app.query_one("#tmux-message", TextArea)

        message.text = "summarize @do"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "summarize @docs/"


async def test_tui_file_completion_lazy_loads_directory() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(
            self,
            path: str,
            *,
            params: dict[str, str] | None = None,
            **_kwargs: object,
        ) -> Response:
            calls.append((path, params or {}))
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/agents/agent-1/files":
                assert params == {"path": "."}
                return Response(
                    {
                        "agent_id": "agent-1",
                        "cwd": "/repo",
                        "path": ".",
                        "parent": None,
                        "entries": [
                            {
                                "name": "README.md",
                                "path": "README.md",
                                "kind": "file",
                                "size": 12,
                                "mtime": 123.0,
                                "extension": ".md",
                                "mime_type": "text/markdown",
                                "is_text": True,
                                "is_image": False,
                                "is_gif": False,
                            },
                            {
                                "name": "src",
                                "path": "src",
                                "kind": "directory",
                                "size": None,
                                "mtime": 123.0,
                                "extension": "",
                                "mime_type": None,
                                "is_text": False,
                                "is_image": False,
                                "is_gif": False,
                            },
                        ],
                        "error": None,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)

        message.text = "review @RE"
        message.move_cursor((0, len(message.text)))
        assert await app.complete_file_reference_async(message, direction=1) is True

    assert message.text == "review @README.md"
    assert app.file_directory_entries_by_agent["agent-1"]["."]["README.md"]["is_text"] is True
    assert ("/v1/agents/agent-1/files", {"path": "."}) in calls


async def test_tui_joplin_note_completion_uses_cached_notes() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        app.query_one("#agent-id", Input).value = "agent-1"
        app.joplin_notes_by_agent = {
            "agent-1": {
                "note-1": {
                    "id": "note-1",
                    "title": "Design Note",
                },
                "note-2": {
                    "id": "note-2",
                    "title": "Release Checklist",
                },
            }
        }
        message = app.query_one("#message", TextArea)

        message.text = "review @joplin:Des"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "review @joplin:Design-Note"


async def test_tui_joplin_note_completion_uses_preceding_caller_scope() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "operator-0"
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "operator-project",
                "metadata": {},
            },
            "caller-a": {
                "agent_id": "caller-a",
                "agent_type": "caller",
                "name": "Project A",
                "project": "alpha",
                "metadata": {},
            },
            "caller-b": {
                "agent_id": "caller-b",
                "agent_type": "caller",
                "name": "Project B",
                "project": "beta",
                "metadata": {},
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        app.joplin_notes_by_agent = {
            "operator-0": {
                "operator-note": {"id": "operator-note", "title": "Operator Runbook"}
            },
            "caller-a": {
                "alpha-note": {"id": "alpha-note", "title": "Alpha Runbook"}
            },
            "caller-b": {
                "beta-note": {"id": "beta-note", "title": "Beta Runbook"}
            },
        }
        message = app.query_one("#message", TextArea)

        message.text = "review @caller:Project-B @joplin:Beta"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True

    assert message.text == "review @caller:Project-B @joplin:Beta-Runbook"


async def test_tui_joplin_note_completion_disambiguates_duplicate_titles() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        app.query_one("#agent-id", Input).value = "agent-1"
        app.joplin_notes_by_agent = {
            "agent-1": {
                "abcdef123456": {
                    "id": "abcdef123456",
                    "title": "Meeting Notes",
                },
                "fedcba654321": {
                    "id": "fedcba654321",
                    "title": "Meeting Notes",
                },
            }
        }
        message = app.query_one("#message", TextArea)

        message.text = "review @joplin:Meeting"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "review @joplin:Meeting-Notes~abcdef12"
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "review @joplin:Meeting-Notes~fedcba65"


async def test_tui_joplin_note_completion_lazy_loads_notes() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            calls.append(path)
            if path == "/v1/joplin/status":
                return Response({"configured": True, "available": True})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/projects/demo/joplin/notes":
                return Response(
                    [
                        {"id": "note-1", "title": "Release Checklist"},
                        {"id": "note-2", "title": "Design Notes"},
                    ]
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)

        message.text = "review @joplin:Rel"
        message.move_cursor((0, len(message.text)))
        assert await app.complete_file_reference_async(message, direction=1) is True

    assert message.text == "review @joplin:Release-Checklist"
    assert "/v1/projects/demo/joplin/notes" in calls
    assert app.joplin_notes_by_agent["agent-1"]["note-1"]["title"] == "Release Checklist"


async def test_tui_joplin_note_completion_lazy_loads_in_tmux_input() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response({"configured": True, "available": True})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/projects/demo/joplin/notes":
                return Response([{"id": "note-1", "title": "Runbook"}])
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        message = app.query_one("#tmux-message", TextArea)

        message.text = "apply @joplin:Ru"
        message.move_cursor((0, len(message.text)))
        assert await app.complete_file_reference_async(message, direction=1) is True

    assert message.text == "apply @joplin:Runbook"


async def test_tui_github_reference_completion_lazy_loads_implied_source() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, dict[str, object]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **kwargs: object) -> Response:
            calls.append((path, dict(kwargs.get("params") or {})))
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/agents/caller-1/pull-requests":
                return Response(
                    {
                        "available": True,
                        "repo": "owner/private",
                        "pull_requests": [
                            {"number": 7, "title": "Add private flow"},
                            {"number": 9, "title": "Follow-up"},
                        ],
                    }
                )
            if path == "/v1/agents/caller-1/issues":
                assert kwargs.get("params") == {"state": "open", "limit": 30}
                return Response(
                    {
                        "available": True,
                        "repo": "owner/private",
                        "issues": [{"number": 12, "title": "Fix quota"}],
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test():
        app.selected_agent_id = "operator-0"
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "k1s-workerbee-private",
            },
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                },
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)

        message.text = "review @pr:"
        message.move_cursor((0, len(message.text)))
        assert await app.complete_file_reference_async(message, direction=1) is True
        assert message.text == "review @pr:7"

        message.text = "mitigate @issue:"
        message.move_cursor((0, len(message.text)))
        assert await app.complete_file_reference_async(message, direction=1) is True
        assert message.text == "mitigate @issue:12"

    assert ("/v1/agents/caller-1/pull-requests", {}) in calls
    assert ("/v1/agents/caller-1/issues", {"state": "open", "limit": 30}) in calls


async def test_tui_caller_agent_completion_uses_cached_callers() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.selected_agent_id = "operator-0"
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {},
            },
            "project-1": {
                "agent_id": "project-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {},
            },
            "project-2": {
                "agent_id": "project-2",
                "agent_type": "caller",
                "name": "Backend Worker",
                "project": "demo",
                "metadata": {},
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)

        message.text = "assign @caller:pro"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "assign @caller:project-1"

        message.text = "follow @caller:Back"
        message.move_cursor((0, len(message.text)))
        assert app.complete_file_reference(message, direction=1) is True
        assert message.text == "follow @caller:Backend-Worker"


def test_tui_file_completion_ignores_email_like_tokens() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    text_area = TextArea()
    text_area.text = "mail dev@example"
    text_area.move_cursor((0, len(text_area.text)))

    assert app.file_completion_context(text_area) is None


async def test_tui_send_input_expands_joplin_note_references() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response(
                    {
                        "configured": True,
                        "available": True,
                        "notebook": "Agent PBX",
                    }
                )
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/projects/demo/joplin/notes":
                return Response(
                    [
                        {
                            "id": "note-1",
                            "title": "weekly-update-052926-060826",
                            "updated_time": 123456.0,
                        }
                    ]
                )
            if path == "/v1/projects/demo/joplin/notes/note-1":
                return Response(
                    {
                        "id": "note-1",
                        "title": "weekly-update-052926-060826",
                        "body": "Decision log\n\n```text\nfenced\n```",
                        "updated_time": 123456.0,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "Please review @joplin:weekly-update-052926-060826"
        await app.send_input()
        await pilot.pause()

    assert len(queued) == 1
    sent_message = queued[0][2]["message"]
    assert "Please review @joplin:weekly-update-052926-060826" in sent_message
    assert "## Joplin Note References" in sent_message
    assert "### 1. weekly-update-052926-060826" in sent_message
    assert "- Ref: `@joplin:weekly-update-052926-060826`" in sent_message
    assert "- Note ID: `note-1`" in sent_message
    assert "Decision log" in sent_message
    assert "````markdown" in sent_message
    assert app.sent_message_history_by_agent["agent-1"] == [
        "Please review @joplin:weekly-update-052926-060826"
    ]


async def test_tui_send_input_expands_caller_agent_references() -> None:
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

    async def fake_ensure_operator_fork_from_tui(
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
    ) -> dict[str, str]:
        assert logical_operator_id == "operator-0"
        assert source_caller_agent_id == "project-1"
        app.agents["operator-0-fork-project-1"] = {
            "agent_id": "operator-0-fork-project-1",
            "agent_type": "operator",
            "project": "demo",
            "metadata": {
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "project-1",
                "source_codex_session_id": "session-project-1",
                "tmux_pane_id": "%42",
            },
        }
        return {
            "operator_fork_id": "fork-1",
            "fork_agent_id": "operator-0-fork-project-1",
            "source_codex_session_id": "session-project-1",
            "tmux_pane_id": "%42",
            "metadata": {
                "source_codex_session_id": "session-project-1",
                "tmux_pane_id": "%42",
            },
        }

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]
    app.ensure_operator_fork_from_tui = fake_ensure_operator_fork_from_tui  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "operator-0"
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {},
            },
            "project-1": {
                "agent_id": "project-1",
                "agent_type": "caller",
                "name": "Project One",
                "project": "demo",
                "effective_status": "working",
                "pbx_active": True,
                "active_campaign_count": 2,
                "metadata": {
                    "pbx_mode": "nohup",
                    "cwd": "/repo/project-1",
                    "codex_session_id": "session-project-1",
                },
            },
        }
        app.tmux_agent_targets["project-1"] = "%7"
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        message.text = "Coordinate @caller:Project-One on the release task."
        await app.send_input()
        await pilot.pause()

    assert len(queued) == 1
    assert queued[0][0] == "operator-0"
    assert queued[0][1] == "send_input"
    sent_message = queued[0][2]["message"]
    assert "Coordinate @caller:Project-One on the release task." in sent_message
    assert "## Caller Agent References" in sent_message
    assert "### 1. Project One" in sent_message
    assert "- Ref: `@caller:Project-One`" in sent_message
    assert "- Agent ID: `project-1`" in sent_message
    assert "- Project: `demo`" in sent_message
    assert "- Status: `working`" in sent_message
    assert "- PBX Mode: `nohup`" in sent_message
    assert "- Active Campaigns: `2`" in sent_message
    assert "- Tmux Pane: `%7`" in sent_message
    assert "- Active Operator Fork ID: `fork-1`" in sent_message
    assert "- Active Fork Agent ID: `operator-0-fork-project-1`" in sent_message
    assert "- Active Fork Source Session: `session-project-1`" in sent_message
    assert "- Active Fork Tmux Pane: `%42`" in sent_message
    assert app.sent_message_history_by_agent["operator-0"] == [
        "Coordinate @caller:Project-One on the release task."
    ]


async def test_tui_send_input_expands_caller_scoped_joplin_references() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response(
                    {
                        "configured": True,
                        "available": True,
                        "notebook": "Agent PBX",
                    }
                )
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/projects/alpha/joplin/notes":
                return Response(
                    [
                        {"id": "alpha-runbook", "title": "Runbook"},
                        {"id": "alpha-checklist", "title": "Checklist"},
                    ]
                )
            if path == "/v1/projects/alpha/joplin/notes/alpha-runbook":
                return Response(
                    {
                        "id": "alpha-runbook",
                        "title": "Runbook",
                        "body": "Alpha runbook body.",
                        "updated_time": 101.0,
                    }
                )
            if path == "/v1/projects/alpha/joplin/notes/alpha-checklist":
                return Response(
                    {
                        "id": "alpha-checklist",
                        "title": "Checklist",
                        "body": "Alpha checklist body.",
                        "updated_time": 102.0,
                    }
                )
            if path == "/v1/projects/beta/joplin/notes":
                return Response([{"id": "beta-runbook", "title": "Runbook"}])
            if path == "/v1/projects/beta/joplin/notes/beta-runbook":
                return Response(
                    {
                        "id": "beta-runbook",
                        "title": "Runbook",
                        "body": "Beta runbook body.",
                        "updated_time": 201.0,
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "operator-0"
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"operator_role": "fork"},
            },
            "caller-a": {
                "agent_id": "caller-a",
                "agent_type": "caller",
                "name": "Project A",
                "project": "alpha",
                "effective_status": "online",
                "pbx_active": True,
                "metadata": {"pbx_mode": "report"},
            },
            "caller-b": {
                "agent_id": "caller-b",
                "agent_type": "caller",
                "name": "Project B",
                "project": "beta",
                "effective_status": "working",
                "pbx_active": True,
                "metadata": {"pbx_mode": "nohup"},
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        prompt = (
            "Review @caller:Project-A @joplin:Runbook and @joplin:Checklist. "
            "Then compare @caller:Project-B @joplin:Runbook."
        )
        message.text = prompt
        await app.send_input()
        await pilot.pause()

    assert len(queued) == 1
    assert queued[0][0] == "operator-0"
    assert queued[0][1] == "send_input"
    sent_message = queued[0][2]["message"]
    assert "Review @caller:Project-A @joplin:Runbook" in sent_message
    assert "Then compare @caller:Project-B @joplin:Runbook." in sent_message
    assert "## Joplin Note References" in sent_message
    assert "### 1. Runbook" in sent_message
    assert "- Note ID: `alpha-runbook`" in sent_message
    assert "- Agent Scope: `caller-a`" in sent_message
    assert "- Project: `alpha`" in sent_message
    assert "- Caller Ref: `@caller:Project-A`" in sent_message
    assert "Alpha runbook body." in sent_message
    assert "### 2. Checklist" in sent_message
    assert "Alpha checklist body." in sent_message
    assert "- Note ID: `beta-runbook`" in sent_message
    assert "- Agent Scope: `caller-b`" in sent_message
    assert "- Project: `beta`" in sent_message
    assert "- Caller Ref: `@caller:Project-B`" in sent_message
    assert "Beta runbook body." in sent_message
    assert "## Caller Agent References" in sent_message
    assert "- Agent ID: `caller-a`" in sent_message
    assert "- Agent ID: `caller-b`" in sent_message
    assert app.sent_message_history_by_agent["operator-0"] == [prompt]


async def test_tui_expands_github_references_with_implied_operator_source() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    paths: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            paths.append(path)
            if path == "/v1/agents/caller-1/pull-requests/7":
                return Response(
                    {
                        "number": 7,
                        "title": "Add private flow",
                        "repo": "owner/private",
                        "url": "https://github.example/owner/private/pull/7",
                        "state": "OPEN",
                        "author": "dev",
                        "head_ref": "feature/private-flow",
                        "base_ref": "dev",
                        "checks": {"total": 1, "success": 1},
                        "files": [{"path": "src/app.py", "additions": 3}],
                        "commits": [{"oid": "abc123"}],
                        "body": "PR body.",
                    }
                )
            if path == "/v1/agents/caller-1/issues/12":
                return Response(
                    {
                        "number": 12,
                        "title": "Fix quota",
                        "repo": "owner/private",
                        "url": "https://github.example/owner/private/issues/12",
                        "state": "OPEN",
                        "author": "ops",
                        "labels": ["bug"],
                        "body": "Issue body.",
                        "comments": [{"author": "dev", "body": "Reproduced."}],
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.agents = {
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
        },
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "default_source_caller_agent_id": "caller-1",
                "default_source_caller_project": "k1s-workerbee-private",
            },
        },
    }

    expanded = await app.expand_prompt_references(
        "operator-0",
        "Review PR #7 and assess issue #12.",
    )

    assert paths == [
        "/v1/agents/caller-1/pull-requests/7",
        "/v1/agents/caller-1/issues/12",
    ]
    assert expanded is not None
    assert "Review PR #7 and assess issue #12." in expanded
    assert "## GitHub PR and Issue References" in expanded
    assert "### 1. PR #7 - Add private flow" in expanded
    assert "- Agent Scope: `caller-1`" in expanded
    assert "- Project: `k1s-workerbee-private`" in expanded
    assert "src/app.py" in expanded
    assert "### 2. Issue #12 - Fix quota" in expanded
    assert "Reproduced." in expanded


async def test_tui_expands_github_reference_before_single_caller_ref() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    paths: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            paths.append(path)
            if path == "/v1/agents/caller-1/pull-requests/7":
                return Response(
                    {
                        "number": 7,
                        "title": "Add private flow",
                        "repo": "owner/private",
                        "url": "https://github.example/owner/private/pull/7",
                        "state": "OPEN",
                        "author": "dev",
                        "body": "PR body.",
                    }
                )
            if path == "/v1/agents/caller-1/issues/12":
                return Response(
                    {
                        "number": 12,
                        "title": "Fix quota",
                        "repo": "owner/private",
                        "url": "https://github.example/owner/private/issues/12",
                        "state": "OPEN",
                        "author": "ops",
                        "body": "Issue body.",
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    async def fake_ensure_operator_fork_from_tui(
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
    ) -> dict[str, object]:
        assert logical_operator_id == "operator-0"
        assert source_caller_agent_id == "caller-1"
        return {
            "operator_fork_id": "fork-1",
            "fork_agent_id": "operator-0-fork-caller-1",
            "source_codex_session_id": "session-1",
            "tmux_pane_id": "%151",
            "metadata": {
                "source_codex_session_id": "session-1",
                "tmux_pane_id": "%151",
            },
        }

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_operator_fork_from_tui = fake_ensure_operator_fork_from_tui  # type: ignore[method-assign]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        "caller-1": {
            "agent_id": "caller-1",
            "agent_type": "caller",
            "name": "Private Repo",
            "project": "k1s-workerbee-private",
            "effective_status": "online",
            "pbx_active": True,
            "metadata": {"pbx_mode": "report", "codex_session_id": "session-1"},
        },
    }

    expanded = await app.expand_prompt_references(
        "operator-0",
        "Review PR #7 for @caller:Private-Repo and assess issue #12.",
    )

    assert paths == [
        "/v1/agents/caller-1/pull-requests/7",
        "/v1/agents/caller-1/issues/12",
    ]
    assert expanded is not None
    assert "- Caller Ref: `@caller:Private-Repo`" in expanded
    assert "## GitHub PR and Issue References" in expanded
    assert "## Caller Agent References" in expanded
    assert "- Active Operator Fork ID: `fork-1`" in expanded


async def test_tui_expands_multi_caller_github_references() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    paths: list[str] = []
    ensured: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            paths.append(path)
            if "/pull-requests/" in path:
                number = int(path.rsplit("/", 1)[1])
                repo = "api/repo" if "api-caller" in path else "web/repo"
                return Response(
                    {
                        "number": number,
                        "title": f"PR {number}",
                        "repo": repo,
                        "url": f"https://github.example/{repo}/pull/{number}",
                        "state": "OPEN",
                        "author": "dev",
                        "body": f"PR {number} body.",
                    }
                )
            if "/issues/" in path:
                number = int(path.rsplit("/", 1)[1])
                repo = "api/repo" if "api-caller" in path else "web/repo"
                return Response(
                    {
                        "number": number,
                        "title": f"Issue {number}",
                        "repo": repo,
                        "url": f"https://github.example/{repo}/issues/{number}",
                        "state": "OPEN",
                        "author": "ops",
                        "body": f"Issue {number} body.",
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    async def fake_ensure_operator_fork_from_tui(
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
    ) -> dict[str, object]:
        ensured.append(source_caller_agent_id)
        return {
            "operator_fork_id": f"fork-{source_caller_agent_id}",
            "fork_agent_id": f"operator-0-fork-{source_caller_agent_id}",
            "source_codex_session_id": f"session-{source_caller_agent_id}",
            "metadata": {"source_codex_session_id": f"session-{source_caller_agent_id}"},
        }

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_operator_fork_from_tui = fake_ensure_operator_fork_from_tui  # type: ignore[method-assign]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        "api-caller": {
            "agent_id": "api-caller",
            "agent_type": "caller",
            "name": "API",
            "project": "api",
            "effective_status": "online",
            "pbx_active": True,
            "metadata": {"pbx_mode": "report"},
        },
        "web-caller": {
            "agent_id": "web-caller",
            "agent_type": "caller",
            "name": "Web",
            "project": "web",
            "effective_status": "online",
            "pbx_active": True,
            "metadata": {"pbx_mode": "report"},
        },
    }

    expanded = await app.expand_prompt_references(
        "operator-0",
        "@caller:API @pr:7 @issue:8 then @caller:Web review PR #3 and issue #4.",
    )

    assert paths == [
        "/v1/agents/api-caller/pull-requests/7",
        "/v1/agents/api-caller/issues/8",
        "/v1/agents/web-caller/pull-requests/3",
        "/v1/agents/web-caller/issues/4",
    ]
    assert ensured == ["api-caller", "web-caller"]
    assert expanded is not None
    assert "- Caller Ref: `@caller:API`" in expanded
    assert "- Caller Ref: `@caller:Web`" in expanded
    assert "- Repo: `api/repo`" in expanded
    assert "- Repo: `web/repo`" in expanded


async def test_tui_send_input_keeps_caller_reference_when_fork_metadata_missing() -> None:
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
        app.selected_agent_id = "operator-0"
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {},
            },
            "codex-k1s-workerbee-private": {
                "agent_id": "codex-k1s-workerbee-private",
                "agent_type": "caller",
                "name": "Codex k1s-workerbee-private",
                "project": "k1s-workerbee-private",
                "effective_status": "online",
                "pbx_active": True,
                "metadata": {
                    "pbx_mode": "report",
                    "cwd": "/home/m4xx3d0ut/git/k1s-wt/k1s-workerbee-private",
                },
            },
        }
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        message.text = "Coordinate @caller:codex-k1s-workerbee-private now."
        await app.send_input()
        await pilot.pause()

    assert len(queued) == 1
    assert queued[0][0] == "operator-0"
    assert queued[0][1] == "send_input"
    sent_message = queued[0][2]["message"]
    assert "Coordinate @caller:codex-k1s-workerbee-private now." in sent_message
    assert "## Caller Agent References" in sent_message
    assert "- Ref: `@caller:codex-k1s-workerbee-private`" in sent_message
    assert "- Agent ID: `codex-k1s-workerbee-private`" in sent_message
    assert "- Project: `k1s-workerbee-private`" in sent_message
    assert "- Status: `online`" in sent_message


async def test_tui_campaigns_loads_and_views_generated_reports() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="secret")
    calls: list[str] = []

    campaign = {
        "campaign_id": "campaign-1",
        "operator_agent_id": "operator-0",
        "title": "Release check",
        "objective": "Verify release state.",
        "criteria": ["All assignments reported"],
        "status": "complete",
        "summary": "Ready",
        "created_at": 1.0,
        "updated_at": 2.0,
        "completed_at": 3.0,
        "assignments": [
            {
                "assignment_id": "assignment-1",
                "campaign_id": "campaign-1",
                "target_agent_id": "caller-1",
                "operator_fork_id": "fork-1",
                "title": "Caller release check",
                "prompt": "Check release",
                "criteria": [],
                "state": "complete",
                "last_report_id": "report-1",
                "last_command_id": "cmd-1",
                "created_at": 1.0,
                "updated_at": 2.0,
                "completed_at": 3.0,
            }
        ],
        "events": [
            {
                "event_id": 7,
                "campaign_id": "campaign-1",
                "assignment_id": "assignment-1",
                "operator_agent_id": "operator-0",
                "target_agent_id": "caller-1",
                "event_type": "assignment_reported",
                "summary": "Caller release check complete",
                "detail": {"state": "complete"},
                "report_id": "report-1",
                "command_id": None,
                "created_at": 2.0,
            }
        ],
    }
    report = {
        "report_id": "report-1",
        "agent_id": "operator-0",
        "project": "agent-pbx-operator",
        "status": "complete",
        "summary": "Caller release check complete",
        "detail": "Detailed campaign report body.",
        "needs_input": False,
        "plan_options": [],
        "metadata": {
            "source": "operator_campaign",
            "campaign_id": "campaign-1",
        },
        "created_at": 2.0,
    }

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            calls.append(path)
            if path == "/v2/operator/campaigns":
                summary = {**campaign, "events": []}
                return Response(
                    {
                        "campaigns": [summary],
                        "cursor": 0,
                        "next_cursor": None,
                        "has_more": False,
                    }
                )
            if path == "/v2/operator/campaigns/campaign-1":
                return Response(campaign)
            if path == "/v1/reports/report-1":
                return Response(report)
            raise AssertionError(path)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        assert app.query_one("#campaign-monitor", Button) is not None
        assert app.query_one("#campaign-report", Button) is not None
        assert app.query_one("#campaign-copy-joplin", Button) is not None
        await app.load_operator_campaigns("operator-0")
        await pilot.pause()
        detail = app.query_one("#campaign-detail", TextArea).text

        assert "Reports:" in detail
        assert "Detailed campaign report body." in detail
        assert "report: report-1 (complete Caller release check complete)" in detail
        assert "fork: fork-1" in detail
        assert app.selected_campaign_report_id_by_operator["operator-0"] == "report-1"

        await app.view_selected_campaign_report()
        await pilot.pause()
        report_detail = app.query_one("#campaign-detail", TextArea).text

    relevant_calls = [
        path
        for path in calls
        if path
        in {
            "/v2/operator/campaigns",
            "/v2/operator/campaigns/campaign-1",
            "/v1/reports/report-1",
        }
    ]
    assert relevant_calls == [
        "/v2/operator/campaigns",
        "/v2/operator/campaigns/campaign-1",
        "/v1/reports/report-1",
    ]
    assert "Campaign: Release check" in report_detail
    assert "Report: report-1" in report_detail
    assert "Agent: operator-0" in report_detail
    assert "Detailed campaign report body." in report_detail


async def test_tui_campaign_copy_creates_joplin_note_from_selected_campaign() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="secret")
    copied: list[dict[str, str]] = []
    campaign = {
        "campaign_id": "campaign-1",
        "operator_agent_id": "operator-0",
        "title": "Release check",
        "objective": "Verify release state.",
        "criteria": ["All assignments reported"],
        "status": "complete",
        "assignments": [
            {
                "target_agent_id": "caller-1",
                "title": "Caller release check",
                "state": "complete",
                "last_report_id": "report-1",
            }
        ],
        "events": [
            {
                "event_type": "assignment_reported",
                "summary": "Caller release check complete",
                "report_id": "report-1",
            }
        ],
        "_reports_by_id": {
            "report-1": {
                "report_id": "report-1",
                "agent_id": "operator-0",
                "status": "complete",
                "summary": "Caller release check complete",
                "detail": "Detailed campaign report body.",
            }
        },
    }

    async def fake_create_manual_joplin_copy(
        agent_id: str,
        *,
        title: str,
        body: str,
        success_message: str,
    ) -> None:
        copied.append(
            {
                "agent_id": agent_id,
                "title": title,
                "body": body,
                "success_message": success_message,
            }
        )

    app.create_manual_joplin_copy = fake_create_manual_joplin_copy  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.campaigns_by_operator = {"operator-0": {"campaign-1": campaign}}
        app.selected_campaign_id_by_operator = {"operator-0": "campaign-1"}
        await app.copy_selected_campaign_to_joplin()
        await pilot.pause()

    assert len(copied) == 1
    assert copied[0]["agent_id"] == "operator-0"
    assert copied[0]["title"] == "Campaign - Release check"
    assert copied[0]["success_message"] == "Copied campaign to a new Joplin note."
    assert "# Campaign - Release check" in copied[0]["body"]
    assert "- Campaign ID: `campaign-1`" in copied[0]["body"]
    assert "- Operator Agent: `operator-0`" in copied[0]["body"]
    assert "Reports:" in copied[0]["body"]
    assert "### 1. report-1" in copied[0]["body"]
    assert "Detailed campaign report body." in copied[0]["body"]


async def test_tui_campaign_monitor_sends_prompt_to_operator_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="secret")
    sent: list[tuple[str, str]] = []
    pane = tmux_support.TmuxPane(
        session_name="agent-pbx-operators",
        window_index="1",
        pane_index="0",
        pane_id="%9",
        active=True,
        current_command="codex",
        title="operator-0",
        cwd="/home/me/agent-pbx",
        width=120,
        height=32,
        history_size=100,
        window_name="operator-0",
    )
    campaign = {
        "campaign_id": "campaign-1",
        "operator_agent_id": "operator-0",
        "title": "WorkerBee private check",
        "objective": "Verify the private repo.",
        "criteria": ["Fork reports complete"],
        "status": "running",
        "assignments": [
            {
                "assignment_id": "assign-1",
                "target_agent_id": "codex-k1s-workerbee-private",
                "title": "Run validation",
                "state": "sent",
                "operator_fork_id": "operator-0-fork-codex-k1s-workerbee-private",
                "last_command_id": "cmd-1",
            }
        ],
    }

    def fake_send_text(target: str, text: str) -> None:
        sent.append((target, text))

    monkeypatch.setattr(tmux_support, "list_panes", lambda: [pane])
    monkeypatch.setattr(tmux_support, "send_text", fake_send_text)
    app.save_settings = lambda: None  # type: ignore[method-assign]

    async def fake_refresh_events() -> None:
        return None

    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "tmux_pane_id": "%9",
                    "cwd": "/home/me/agent-pbx",
                },
            }
        }
        app.selected_agent_id = "operator-0"
        app.campaigns_by_operator = {"operator-0": {"campaign-1": campaign}}
        app.selected_campaign_id_by_operator = {"operator-0": "campaign-1"}

        await app.monitor_selected_campaign()
        await pilot.pause()
        detail = app.query_one("#campaign-detail", TextArea).text

    assert sent
    assert sent[0][0] == "%9"
    assert "Monitor operator campaign `campaign-1`" in sent[0][1]
    assert "assignment_id=assign-1" in sent[0][1]
    assert "pbx_operator_campaign_status" in sent[0][1]
    assert "pbx_operator_send_followup" in sent[0][1]
    assert "Do not dispatch to regular caller tmux panes" in sent[0][1]
    assert app.tmux_agent_targets["operator-0"] == "%9"
    assert app.tmux_direct_agent_modes["operator-0"] is True
    assert "Monitor prompt sent to operator-0" in detail


async def test_tui_follow_up_exact_campaign_monitor_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_campaign_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.campaign_action_for_agent = fake_campaign_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        message.text = "/campaign monitor"
        await app.send_input()
        await pilot.pause()

    assert calls == [("operator-0", "monitor")]
    assert message.text == ""


async def test_tui_follow_up_exact_campaign_report_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_campaign_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.campaign_action_for_agent = fake_campaign_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        message.text = "/campaign report"
        await app.send_input()
        await pilot.pause()

    assert calls == [("operator-0", "report")]
    assert message.text == ""


async def test_tui_follow_up_exact_campaign_copy_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_campaign_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.campaign_action_for_agent = fake_campaign_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        message = app.query_one("#message", TextArea)
        message.text = "/campaign copy"
        await app.send_input()
        await pilot.pause()

    assert calls == [("operator-0", "copy")]
    assert message.text == ""


async def test_tui_tmux_input_expands_joplin_note_references() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    logged: list[tuple[str, str]] = []
    captured: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response(
                    {
                        "configured": True,
                        "available": True,
                        "notebook": "Agent PBX",
                    }
                )
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/projects/demo/joplin/notes":
                return Response([{"id": "note-1", "title": "Runbook"}])
            if path == "/v1/projects/demo/joplin/notes/note-1":
                return Response(
                    {
                        "id": "note-1",
                        "title": "Runbook",
                        "body": "Use the staged workflow.",
                    }
                )
            raise AssertionError(f"unexpected GET {path}")

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captured.append(agent_id)

    async def fake_record_tmux_joplin_interaction(
        agent_id: str,
        message: str,
    ) -> None:
        logged.append((agent_id, message))

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.tmux_features_available = True
        app.tmux_direct_enabled = True
        app.selected_agent_id = "agent-1"
        app.agents = {"agent-1": {"agent_id": "agent-1", "project": "demo"}}
        message = app.query_one("#tmux-message", TextArea)
        message.text = "Apply @joplin:Runbook"
        await app.send_input()
        await pilot.pause()

    assert len(sent) == 1
    assert sent[0][0] == "agent-1"
    assert "Apply @joplin:Runbook" in sent[0][1]
    assert "## Joplin Note References" in sent[0][1]
    assert "Use the staged workflow." in sent[0][1]
    assert logged == sent
    assert captured == ["agent-1"]
    assert app.sent_message_history_by_agent["agent-1"] == ["Apply @joplin:Runbook"]


def test_tui_formats_image_file_preview_as_rendered_text() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    rendered = app.format_file_preview(
        {
            "path": "docs/assets/demo.gif",
            "kind": "file",
            "size": 128,
            "mtime": 123.0,
            "mime_type": "image/gif",
            "is_text": False,
            "is_image": True,
            "is_gif": True,
            "image_width": 2,
            "image_height": 3,
            "image_preview": "@@\n::",
            "image_preview_ansi": None,
            "image_preview_format": "grayscale-ascii",
            "text": None,
            "truncated": False,
            "error": None,
        }
    )

    assert "Image: GIF" in rendered
    assert "Dimensions: 2x3" in rendered
    assert "GIF Preview (first frame)" in rendered
    assert "@@\n::" in rendered


def test_tui_formats_color_image_file_preview_as_rich_text() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    renderables = app.format_file_preview_renderables(
        {
            "path": "docs/assets/demo.png",
            "kind": "file",
            "size": 128,
            "mtime": 123.0,
            "mime_type": "image/png",
            "is_text": False,
            "is_image": True,
            "is_gif": False,
            "image_width": 2,
            "image_height": 2,
            "image_preview": "@@",
            "image_preview_ansi": "\x1b[38;2;255;0;0m\x1b[48;2;0;0;255m▀\x1b[0m",
            "image_preview_format": "ansi-truecolor-halfblocks",
            "text": None,
            "truncated": False,
            "error": None,
        }
    )

    assert len(renderables) == 1
    assert isinstance(renderables[0], Text)
    assert "Image Preview (color)" in renderables[0].plain
    assert "▀" in renderables[0].plain


def test_tui_formats_image_file_preview_without_rendered_text() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    rendered = app.format_file_preview(
        {
            "path": "docs/assets/photo.jpg",
            "kind": "file",
            "size": 128,
            "mtime": 123.0,
            "mime_type": "image/jpeg",
            "is_text": False,
            "is_image": True,
            "is_gif": False,
            "image_width": 2,
            "image_height": 3,
            "image_preview": None,
            "image_preview_ansi": None,
            "image_preview_format": None,
            "text": None,
            "truncated": False,
            "error": None,
        }
    )

    assert "Image: yes" in rendered
    assert "Image preview is metadata-only." in rendered


def test_tui_extracts_event_agent_id() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.event_agent_id({"payload": {"agent_id": "agent-1"}}) == "agent-1"
    assert (
        app.event_agent_id(
            {
                "type": "operator_campaign_event",
                "payload": {
                    "operator_agent_id": "operator-0",
                    "fork_agent_id": "operator-0-fork-caller-1",
                },
            }
        )
        == "operator-0-fork-caller-1"
    )
    assert (
        app.event_agent_id(
            {
                "type": "operator_campaign_event",
                "payload": {"operator_agent_id": "operator-0"},
            }
        )
        == "operator-0"
    )
    assert (
        app.operator_campaign_event_operator_id(
            {
                "type": "operator_campaign_event",
                "payload": {
                    "operator_agent_id": "operator-0",
                    "fork_agent_id": "operator-0-fork-caller-1",
                },
            }
        )
        == "operator-0"
    )
    assert app.event_agent_id({"payload": {}}) is None
    assert app.event_agent_id({"payload": None}) is None


def test_tui_palette_keeps_explicit_operator_selection(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.selected_agent_id = "operator-0"
    monkeypatch.setattr(app, "focused_agent_table_id", lambda: None)
    monkeypatch.setattr(app, "agent_id_at_cursor", lambda: "caller-at-stale-cursor")
    monkeypatch.setattr(app, "operator_id_at_cursor", lambda: "operator-0")

    assert app.selected_or_cursor_agent_id() == "operator-0"

    monkeypatch.setattr(app, "focused_agent_table_id", lambda: "caller-in-focus")
    assert app.selected_or_cursor_agent_id() == "caller-in-focus"


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
    assert "/unblock" in titles
    assert "/working" in titles
    assert "/esc" in titles
    assert "/ctrlc" in titles
    assert "/restart" in titles
    assert "/codex restart" in titles
    assert "/codex" in titles
    assert "/codex config" in titles
    assert "/codex config refresh" in titles
    assert "/codex config save" in titles
    assert "/pbx keymap" in titles
    assert "/codex keymap" in titles
    assert "/codex keymap portable" in titles
    assert "/codex keymap reset" in titles
    assert "/codex mcp wire" in titles
    assert "/codex update" in titles
    assert "/codex model" in titles
    assert "/codex model plan" in titles
    assert "/codex model cleanup plan" in titles
    assert "/codex model terra-max" in titles
    assert "/codex model default terra-max" in titles
    assert "/codex model sol-max" in titles
    assert "/codex model sol-xhigh" in titles
    assert "/codex model default sol-max" in titles
    assert "/codex model default sol-xhigh" in titles
    assert "/workspace launch" in titles
    assert "/agent launch" in titles
    assert "/tmux" in titles
    assert "/latest" in titles
    assert "/thread" in titles
    assert "/files" in titles
    assert "/files refresh" in titles
    assert "/files up" in titles
    assert "/files search" in titles
    assert "/files search clear" in titles
    assert "/files copy path" in titles
    assert "/files copy text" in titles
    assert "/files open editor" in titles
    assert "/editor" in titles
    assert "/editor open" in titles
    assert "/editor save" in titles
    assert "/editor revert" in titles
    assert "/editor check" in titles
    assert "/editor copy path" in titles
    assert "/editor copy text" in titles
    assert "/editor fullscreen" in titles
    assert "/editor close" in titles
    assert "/editor focus" in titles
    assert "/editor reveal" in titles
    assert "/workerbee" in titles
    assert "/campaigns" in titles
    assert "/campaign monitor" in titles
    assert "/campaign report" in titles
    assert "/campaign copy" in titles
    assert "/plan" in titles
    assert "/plan latest" in titles
    assert "/plan thread" in titles
    assert "/operator history" in titles
    assert "/operator resume" in titles
    assert "/operator restart" in titles
    assert "/operator fork next" in titles
    assert "/operator fork prev" in titles
    assert "/operator fork review" in titles
    assert "/operator handoffs" in titles
    assert "/operator handoff preflight" in titles
    assert "/operator handoff approve" in titles
    assert "/operator handoff launch" in titles
    assert "/operator knowledge links" in titles
    assert "/operator knowledge send" in titles
    assert "/operator kb" in titles
    assert "/operator kb detail" in titles
    assert "/operator kb proposed" in titles
    assert "/operator kb proposed detail" in titles
    assert "/operator kb search" in titles
    assert "/operator kb history" in titles
    assert "/operator kb misses" in titles
    assert "/operator kb seed" in titles
    assert "/operator kb promote" in titles
    assert "/operator kb reject" in titles
    assert "/operator kb retire" in titles
    assert "/operator project spawn" in titles
    assert "/theme minimal" in titles
    assert "/layout compact" in titles
    assert "/gitstatus" not in titles
    assert "/gitdiff" not in titles
    assert "/gitpush" not in titles
    assert "/gitstageandcommit" not in titles


async def test_tui_managed_workspace_reuses_caller_and_starts_operator() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    selected: list[str] = []
    started: list[str] = []
    app.agents = {
        "codex-demo": {
            "agent_id": "codex-demo",
            "agent_type": "caller",
            "project": "demo",
            "status": "waiting",
            "last_seen_at": 1.0,
            "metadata": {
                "cwd": "/tmp/demo",
                "codex_session_id": "session-demo",
            },
        }
    }

    async def fake_select(agent_id: str) -> None:
        selected.append(agent_id)
        app.selected_agent_id = agent_id

    async def fake_start_operator(
        agent_id: str | None = None,
        *,
        source_caller_agent_id: str | None = None,
    ) -> None:
        assert agent_id is None
        started.append(str(source_caller_agent_id))

    app.select_agent = fake_select  # type: ignore[method-assign]
    app.start_operator_agent = fake_start_operator  # type: ignore[method-assign]
    app.notify = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    launched = await app.launch_managed_workspace_from_picker(
        project_path="/tmp/demo",
        agent_id="",
        profile_id="sol-high",
        runtime_server_mode="dedicated",
        launch_mode="agent_operator",
        existing_owner_ids=["codex-demo"],
    )

    assert launched is True
    assert selected == ["codex-demo"]
    assert started == ["codex-demo"]


async def test_tui_managed_workspace_bootstraps_new_caller_before_operator() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    sent: list[tuple[str, str]] = []
    started: list[str] = []

    async def fake_launch(**_kwargs: object) -> bool:
        app.selected_agent_id = "codex-demo"
        app.agents["codex-demo"] = {
            "agent_id": "codex-demo",
            "agent_type": "caller",
            "project": "demo",
            "status": "online",
            "last_seen_at": 1.0,
            "metadata": {"cwd": "/tmp/demo"},
        }
        return True

    async def fake_send(agent_id: str, prompt: str) -> bool:
        sent.append((agent_id, prompt))
        return True

    async def fake_wait(agent_id: str, **_kwargs: object) -> bool:
        app.agents[agent_id]["metadata"]["codex_session_id"] = "session-demo"
        return True

    async def fake_start_operator(
        agent_id: str | None = None,
        *,
        source_caller_agent_id: str | None = None,
    ) -> None:
        assert agent_id is None
        started.append(str(source_caller_agent_id))

    app.launch_managed_agent_from_picker = fake_launch  # type: ignore[method-assign]
    app.send_text_to_tmux = fake_send  # type: ignore[method-assign]
    app.wait_for_managed_caller_registration = fake_wait  # type: ignore[method-assign]
    app.start_operator_agent = fake_start_operator  # type: ignore[method-assign]
    app.notify = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    launched = await app.launch_managed_workspace_from_picker(
        project_path="/tmp/demo",
        agent_id="codex-demo",
        profile_id="sol-high",
        runtime_server_mode="dedicated",
        launch_mode="agent_operator",
    )

    assert launched is True
    assert sent[0][0] == "codex-demo"
    assert "startup registration only" in sent[0][1]
    assert started == ["codex-demo"]


async def test_tui_f10_launches_workspace_under_terminal_focus() -> None:
    opened: list[bool] = []
    written: list[bytes] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    class FakeProcess:
        alive = True
        columns = 80
        rows = 24

        def read_available(self, **_kwargs: object) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            written.append(data)

        def close(self) -> None:
            self.alive = False

        def resize(self, columns: int, rows: int) -> None:
            self.columns = columns
            self.rows = rows

    async def fake_open() -> None:
        opened.append(True)

    app.open_managed_project_picker = fake_open  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        app.selected_agent_id = "agent-a"
        app.embedded_terminal_v2_enabled = True
        app.embedded_terminal_agent_id = "agent-a"
        app.embedded_terminal_target = "server:%7"
        terminal = app.query_one("#pbx-terminal-surface", PbxTerminalSurface)
        terminal.process = FakeProcess()  # type: ignore[assignment]
        terminal.target = "server:%7"
        app.apply_tmux_class()
        terminal.focus()
        await pilot.pause()

        await pilot.press("f10")
        await pilot.pause()
        app.on_key(Key("f22", None))
        await pilot.pause()

    assert opened == [True]
    assert written == [function_key_sequence(10)]


async def test_managed_project_picker_dismisses_before_selecting_launched_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    launched: list[dict[str, object]] = []

    async def fake_launch(**request: object) -> bool:
        assert not isinstance(app.screen, ManagedProjectPickerScreen)
        assert app.query_one("#agent-id", Input) is not None
        launched.append(request)
        return True

    app.launch_managed_workspace_from_picker = fake_launch  # type: ignore[method-assign]
    project = {
        "path": "/tmp/demo",
        "name": "demo",
        "branch": "dev",
        "available": True,
        "owned_by": [],
    }

    async with app.run_test() as pilot:
        app.push_screen(ManagedProjectPickerScreen([project], ["/tmp"]))
        await pilot.pause()
        app.screen.query_one("#managed-project-launch", Button).press()
        await pilot.pause()

    assert launched == [
        {
            "project_path": "/tmp/demo",
            "agent_id": "",
            "profile_id": "sol-high",
            "runtime_server_mode": "dedicated",
            "launch_mode": "agent",
            "existing_owner_ids": [],
        }
    ]


async def test_tui_codex_model_selector_displays_sol_postures() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    sol_xhigh = codex_model_preset_for("sol-xhigh")
    sol_max = codex_model_preset_for("sol-max")

    assert sol_xhigh is not None
    assert sol_max is not None

    async with app.run_test() as pilot:
        await pilot.pause()
        selector = app.query_one("#codex-model-preset", Select)
        status = app.query_one("#codex-model-status", Static)

        app.set_codex_model_preset_selector(sol_xhigh)
        assert selector.value == "sol-5.6-xhigh"
        assert (
            "Model preset: sol-5.6/xhigh -> gpt-5.6-sol/xhigh, verbosity high, "
            "reasoning summary detailed."
        ) in str(status.renderable)

        app.set_codex_model_preset_selector(sol_max)
        assert selector.value == "sol-5.6-max"
        assert (
            "Model preset: sol-5.6/max -> gpt-5.6-sol/max, verbosity high, "
            "reasoning summary detailed."
        ) in str(status.renderable)


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


def test_tui_joplin_commands_are_reserved_builtin_names() -> None:
    names = built_in_palette_command_names()

    assert {
        "/campaigns",
        "/campaign monitor",
        "/campaign report",
        "/campaign copy",
        "/unblock",
        "/working",
        "/operator fork next",
        "/operator fork prev",
        "/operator fork review",
        "/operator handoffs",
        "/operator handoff preflight",
        "/operator handoff approve",
        "/operator handoff launch",
        "/operator knowledge links",
        "/operator knowledge send",
        "/operator kb",
        "/operator kb detail",
        "/operator kb proposed",
        "/operator kb proposed detail",
        "/operator kb search",
        "/operator kb history",
        "/operator kb misses",
        "/operator kb seed",
        "/operator kb promote",
        "/operator kb reject",
        "/operator kb retire",
        "/operator project spawn",
        "/joplin",
        "/joplin refresh",
        "/joplin new",
        "/joplin rename",
        "/joplin delete",
        "/joplin copy",
        "/joplin copy force",
        "/joplin copy report",
        "/joplin copy mode copy-first",
        "/joplin copy mode transcript-first",
        "/joplin copy mode tmux-capture",
        "/joplin log start",
        "/joplin log stop",
        "/joplin save",
        "/joplin sync",
        "/codex",
        "/codex config",
        "/codex config refresh",
        "/codex config save",
        "/codex mcp wire",
        "/codex update",
        "/codex model",
        "/codex model terra-max",
        "/codex model default terra-max",
        "/codex model sol-max",
        "/codex model sol-xhigh",
        "/codex model default sol-max",
        "/codex model default sol-xhigh",
    } <= names
    assert {
        "/agents prune",
        "/agents prune apply",
        "/agents prune stale",
        "/agents prune forks",
        "/agents prune undo",
    } <= names
    assert {"/show hidden agents", "/unhide agent"} <= names


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


def test_tui_plan_prompt_prefix_requests_structured_pbx_planning() -> None:
    prompt = PLAN_PBX_CONTEXT_PROMPT

    assert "pbx_report_turn" in prompt
    assert "structured plan_options" in prompt
    assert "Do not start nohup polling" in prompt
    assert render_plan_prompt("Draft a path forward.").endswith("Draft a path forward.")


def test_tui_detects_codex_native_plan_selector() -> None:
    capture = """
    Plan ready.

      1 Start coding
      2 Clear context & start
      3 Stay in plan mode
    """

    assert contains_codex_native_plan_selector(capture) is True
    assert codex_native_plan_selector_indices(capture) == (1, 2, 3)
    assert contains_codex_native_plan_selector("1 Start coding only") is False
    assert (
        contains_codex_native_plan_selector(
            "The final plan:2 option is to clear context and start; "
            "plan:3 stays in plan mode and plan:1 starts coding."
        )
        is False
    )


def test_tui_detects_codex_pre_plan_question_selector() -> None:
    capture = """
    Pick the approach you want before I draft the implementation plan.

    › 1. Keep the current API shape
      2. Add a compatibility shim
      3. Split the migration into phases
      4. Stop and inspect the affected tests first
    """

    assert contains_codex_native_plan_selector(capture) is True
    assert codex_native_plan_selector_indices(capture) == (1, 2, 3, 4)


def test_tui_detects_current_codex_plan_selector_text() -> None:
    capture = """
    Plan updated. Choose how to proceed.

    › 1. Yes, implement this plan          Switch to Default and start coding.
      2. Yes, clear context and implement  Fresh thread. Context: 26% used.
      3. No, keep planning                 Stay in planning mode.
    """

    assert contains_codex_native_plan_selector(capture) is True
    assert codex_native_plan_selector_indices(capture) == (1, 2, 3)


def test_tui_rejects_marked_menu_without_plan_context() -> None:
    capture = """
    Choose a file to open.

    › 1. README.md
      2. pyproject.toml
    """

    assert contains_codex_native_plan_selector(capture) is False


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
    assert "/gitpush" in titles
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


async def test_tui_model_actions_bind_selected_operator_not_stale_caller_cursor() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    restarts: list[tuple[str | None, str | None]] = []

    async def fake_restart(
        agent_id: str | None = None,
        *,
        model_preset=None,
    ) -> None:
        restarts.append(
            (
                agent_id,
                model_preset.key if model_preset is not None else None,
            )
        )

    app.restart_tmux_codex_session = fake_restart  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "codex-agent-pbx": {
                "agent_id": "codex-agent-pbx",
                "agent_type": "caller",
                "status": "working",
                "project": "agent-pbx",
                "last_seen_at": 125.0,
            },
            "operator-5": {
                "agent_id": "operator-5",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 124.0,
            },
        }
        app.render_agents()
        callers = app.query_one("#agents", DataTable)
        operators = app.query_one("#operators", DataTable)
        callers.move_cursor(row=0, animate=False, scroll=False)
        operators.move_cursor(row=0, animate=False, scroll=False)
        app.selected_agent_id = "operator-5"
        app.query_one("#agent-id", Input).value = "codex-agent-pbx"

        assert app.codex_model_target_agent_id() == "operator-5"
        app.palette_codex_model_restart("terra-max")
        await pilot.pause()
        await app.restart_selected_with_codex_model_preset()

        app.selected_agent_id = None
        assert app.codex_model_target_agent_id() is None

    assert restarts == [
        ("operator-5", "terra-5.6-max"),
        ("operator-5", "terra-5.6-max"),
    ]


async def test_tui_sol_max_requires_explicit_justification_modal() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    restarts: list[str] = []

    async def fake_restart(*_args: object, **_kwargs: object) -> bool:
        restarts.append("restarted")
        return True

    app.restart_tmux_codex_session = fake_restart  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-5": {
                "agent_id": "operator-5",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator"},
                "last_seen_at": 124.0,
            }
        }
        app.selected_agent_id = "operator-5"
        app.palette_codex_model_restart("sol-max")
        await pilot.pause()

        assert isinstance(app.screen, ModelElevationScreen)
        assert restarts == []


async def test_tui_palette_escape_uses_selected_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    async def fake_send_escape_key() -> None:
        calls.append(app.query_one("#agent-id", Input).value)

    app.send_escape_key = fake_send_escape_key  # type: ignore[method-assign]

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
        app.palette_escape()
        await pilot.pause()

    assert calls == ["agent-1"]


async def test_tui_palette_ctrl_c_uses_selected_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    async def fake_send_ctrl_c_key() -> None:
        calls.append(app.query_one("#agent-id", Input).value)

    app.send_ctrl_c_key = fake_send_ctrl_c_key  # type: ignore[method-assign]

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
        app.palette_ctrl_c()
        await pilot.pause()

    assert calls == ["agent-1"]


async def test_tui_follow_up_tab_completes_slash_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "/e"
        message.move_cursor((0, 2))
        await message._on_key(Key("tab", "\t"))

    assert message.text == "/esc"
    assert message.cursor_location == (0, len("/esc"))


async def test_tui_follow_up_tab_completes_operator_review_slash_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "/operator fork r"
        message.move_cursor((0, len("/operator fork r")))
        await message._on_key(Key("tab", "\t"))

    assert message.text == "/operator fork review"
    assert message.cursor_location == (0, len("/operator fork review"))


async def test_tui_follow_up_tab_cycles_slash_command_matches() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "/plan"
        message.move_cursor((0, len("/plan")))
        await message._on_key(Key("tab", "\t"))
        first_completion = message.text
        await message._on_key(Key("tab", "\t"))
        second_completion = message.text

    assert first_completion == "/plan latest"
    assert second_completion == "/plan thread"


async def test_tui_tmux_follow_up_tab_completes_tmux_only_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        message = app.query_one("#tmux-message", TextArea)
        message.text = "/gitst"
        message.move_cursor((0, len("/gitst")))
        await message._on_key(Key("tab", "\t"))

    assert message.text == "/gitstatus"


async def test_tui_follow_up_tab_completes_ctrl_c_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "/ctr"
        message.move_cursor((0, len("/ctr")))
        await message._on_key(Key("tab", "\t"))

    assert message.text == "/ctrlc"


async def test_tui_follow_up_tab_completes_joplin_action_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.joplin_configured = True
        message = app.query_one("#message", TextArea)
        message.text = "/joplin c"
        message.move_cursor((0, len("/joplin c")))
        await message._on_key(Key("tab", "\t"))

    assert message.text == "/joplin copy"


async def test_tui_follow_up_exact_slash_command_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[str] = []

    async def fake_send_escape_key() -> None:
        calls.append(app.query_one("#agent-id", Input).value)

    app.send_escape_key = fake_send_escape_key  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "/esc"
        await app.send_input()
        await pilot.pause()

    assert calls == ["agent-1"]
    assert message.text == ""


async def test_tui_follow_up_exact_joplin_action_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "/joplin copy"
        await app.send_input()
        await pilot.pause()

    assert calls == [("agent-1", "copy")]
    assert message.text == ""


async def test_tui_follow_up_exact_joplin_copy_report_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "/joplin copy report"
        await app.send_input()
        await pilot.pause()

    assert calls == [("agent-1", "copy-report")]
    assert message.text == ""


async def test_tui_follow_up_exact_joplin_copy_force_executes_locally() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "/joplin copy force"
        await app.send_input()
        await pilot.pause()

    assert calls == [("agent-1", "copy-force")]
    assert message.text == ""


async def test_tui_follow_up_joplin_action_uses_input_agent_not_cursor() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            },
        }
        app.render_agents()
        app.move_agent_cursor("agent-2", focus=False)
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "/joplin copy"
        await app.send_input()
        await pilot.pause()

    assert calls == [("agent-1", "copy")]
    assert message.text == ""


async def test_tui_joplin_target_uses_input_agent_unless_table_focused() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "alpha",
                "last_seen_at": 124.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "beta",
                "last_seen_at": 123.0,
            },
        }
        app.render_agents()
        app.move_agent_cursor("agent-2", focus=False)
        app.selected_agent_id = "agent-1"
        agent_input = app.query_one("#agent-id", Input)
        agent_input.value = "agent-1"
        agent_input.focus()
        await pilot.pause()

        assert app.joplin_target_agent_id() == "agent-1"

        agents_table = app.query_one("#agents", DataTable)
        agents_table.focus()
        await pilot.pause()

        assert app.joplin_target_agent_id() == "agent-2"


async def test_tui_joplin_leader_uses_input_agent_not_stale_cursor() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.joplin_available = True
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "alpha",
                "last_seen_at": 124.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "beta",
                "last_seen_at": 123.0,
            },
        }
        app.render_agents()
        app.move_agent_cursor("agent-2", focus=False)
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        tabs = app.query_one("#agent-tabs")
        tabs.active = "joplin-tab"
        app.active_agent_tab = "joplin-tab"
        body = app.query_one("#joplin-body", TextArea)
        body.focus()
        await body._on_key(Key("ctrl+g", None))
        await body._on_key(Key("s", "s"))
        await pilot.pause()

    assert calls == [("agent-1", "save")]


async def test_tui_joplin_rename_modal_submits_explicit_note_id() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str, str, str | None]] = []

    async def fake_joplin_title_action_for_agent(
        agent_id: str,
        action: str,
        title: str,
        *,
        note_id: str | None = None,
    ) -> None:
        calls.append((agent_id, action, title, note_id))

    app.joplin_title_action_for_agent = fake_joplin_title_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "alpha",
                "last_seen_at": 124.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.joplin_notes_by_agent = {
            "agent-1": {
                "note-1": {"id": "note-1", "title": "Old title"},
                "note-2": {"id": "note-2", "title": "Other title"},
            }
        }
        app.set_selected_joplin_note_for_agent("agent-1", "note-1")
        app.open_joplin_title_modal("agent-1", action="rename")
        await pilot.pause()
        title_input = app.query_one("#joplin-title-input", Input)
        assert title_input.value == "Old title"
        assert app.focused is title_input

        app.set_selected_joplin_note_for_agent("agent-1", "note-2")
        title_input.value = "Renamed title"
        submit = getattr(app.screen, "submit")
        submit()
        await pilot.pause()

    assert calls == [("agent-1", "rename", "Renamed title", "note-1")]


async def test_tui_joplin_rename_and_save_use_scoped_note_selection() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    puts: list[tuple[str, dict[str, object]]] = []
    loaded: list[str] = []
    edit_starts: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response({"configured": True, "available": True})
            raise AssertionError(f"unexpected GET {path}")

        async def post(self, path: str, **_kwargs: object) -> Response:
            edit_starts.append(path)
            index = len(edit_starts)
            title = "Old alpha" if index == 1 else "Renamed alpha"
            return Response(
                {
                    "edit": {"edit_id": f"edit-{index}", "base_revision": f"rev-{index}"},
                    "note": {
                        "id": "note-alpha",
                        "title": title,
                        "body": "Base body",
                        "revision": f"rev-{index}",
                    },
                    "conflict": None,
                }
            )

        async def put(self, path: str, **kwargs: object) -> Response:
            puts.append((path, dict(kwargs.get("json") or {})))
            return Response({})

    async def fake_load_joplin_notes(agent_id: str) -> None:
        loaded.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.load_joplin_notes = fake_load_joplin_notes  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {"agent_id": "agent-1", "project": "alpha"},
            "agent-2": {"agent_id": "agent-2", "project": "beta"},
        }
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.set_selected_joplin_note_for_agent("agent-1", "note-alpha")
        app.set_selected_joplin_note_for_agent("agent-2", "note-beta")
        app.selected_joplin_note_id = "note-beta"

        await app.rename_joplin_note("agent-1", title="Renamed alpha")
        await app.begin_joplin_edit("agent-1")
        app.query_one("#joplin-body", TextArea).text = "Updated body"
        await app.save_joplin_note("agent-1")

    note_puts = [item for item in puts if "/joplin/notes/" in item[0]]
    assert note_puts == [
        (
            "/v1/projects/alpha/joplin/notes/note-alpha",
            {
                "title": "Renamed alpha",
                "base_revision": "rev-1",
                "base_title": "Old alpha",
                "base_body": "Base body",
                "edit_id": "edit-1",
            },
        ),
        (
            "/v1/projects/alpha/joplin/notes/note-alpha",
            {
                "title": "Renamed alpha",
                "body": "Updated body",
                "base_revision": "rev-2",
                "base_title": "Renamed alpha",
                "base_body": "Base body",
                "edit_id": "edit-2",
            },
        ),
    ]
    assert edit_starts == [
        "/v1/projects/alpha/joplin/notes/note-alpha/edit",
        "/v1/projects/alpha/joplin/notes/note-alpha/edit",
    ]
    assert loaded == ["agent-1", "agent-1"]


async def test_tui_root_operator_joplin_save_uses_agent_note_scope() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    puts: list[tuple[str, dict[str, object]]] = []
    loaded: list[str] = []
    edit_starts: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_kwargs: object) -> Response:
            if path == "/v1/joplin/status":
                return Response({"configured": True, "available": True})
            raise AssertionError(f"unexpected GET {path}")

        async def post(self, path: str, **_kwargs: object) -> Response:
            edit_starts.append(path)
            index = len(edit_starts)
            title = "Old operator" if index == 1 else "Operator note"
            return Response(
                {
                    "edit": {"edit_id": f"root-edit-{index}", "base_revision": f"root-rev-{index}"},
                    "note": {
                        "id": "note-root",
                        "title": title,
                        "body": "Operator base",
                        "revision": f"root-rev-{index}",
                    },
                    "conflict": None,
                }
            )

        async def put(self, path: str, **kwargs: object) -> Response:
            puts.append((path, dict(kwargs.get("json") or {})))
            return Response({})

    async def fake_load_joplin_notes(agent_id: str) -> None:
        loaded.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.load_joplin_notes = fake_load_joplin_notes  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "k1s-workerbee-private",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                },
            }
        }
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        app.set_selected_joplin_note_for_agent("operator-0", "note-root")

        await app.rename_joplin_note("operator-0", title="Operator note")
        await app.begin_joplin_edit("operator-0")
        app.query_one("#joplin-body", TextArea).text = "Operator body"
        await app.save_joplin_note("operator-0")

    note_puts = [item for item in puts if "/joplin/notes/" in item[0]]
    assert note_puts == [
        (
            "/v1/agents/operator-0/joplin/notes/note-root",
            {
                "title": "Operator note",
                "base_revision": "root-rev-1",
                "base_title": "Old operator",
                "base_body": "Operator base",
                "edit_id": "root-edit-1",
            },
        ),
        (
            "/v1/agents/operator-0/joplin/notes/note-root",
            {
                "title": "Operator note",
                "body": "Operator body",
                "base_revision": "root-rev-2",
                "base_title": "Operator note",
                "base_body": "Operator base",
                "edit_id": "root-edit-2",
            },
        ),
    ]
    assert edit_starts == [
        "/v1/agents/operator-0/joplin/notes/note-root/edit",
        "/v1/agents/operator-0/joplin/notes/note-root/edit",
    ]
    assert loaded == ["operator-0", "operator-0"]


async def test_tui_joplin_leader_shortcut_runs_action() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.joplin_available = True
        app.selected_agent_id = "agent-1"
        tabs = app.query_one("#agent-tabs")
        tabs.active = "joplin-tab"
        app.active_agent_tab = "joplin-tab"
        notes = app.query_one("#joplin-notes", DataTable)
        notes.focus()
        assert app.handle_joplin_shortcut_key(Key("j", "j"), focused=notes) is True
        assert app.handle_joplin_shortcut_key(Key("c", "c"), focused=notes) is True
        await pilot.pause()

    assert calls == [("agent-1", "copy")]


async def test_tui_joplin_ctrl_g_shortcut_works_from_note_body() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, str]] = []

    async def fake_joplin_action_for_agent(agent_id: str, action: str) -> None:
        calls.append((agent_id, action))

    app.joplin_action_for_agent = fake_joplin_action_for_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.joplin_configured = True
        app.joplin_available = True
        app.selected_agent_id = "agent-1"
        tabs = app.query_one("#agent-tabs")
        tabs.active = "joplin-tab"
        app.active_agent_tab = "joplin-tab"
        body = app.query_one("#joplin-body", TextArea)
        body.focus()
        await body._on_key(Key("ctrl+g", None))
        await body._on_key(Key("s", "s"))
        await pilot.pause()

    assert calls == [("agent-1", "save")]


async def test_tui_joplin_copy_tmux_response_uses_codex_copy_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    app.tmux_direct_enabled = True
    posts: list[dict[str, object]] = []
    captures: list[str] = []
    loaded: list[str] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            return Response({"id": "note-1"})

    async def fake_ensure_joplin_available() -> bool:
        return True

    async def fake_load_joplin_notes(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_joplin_available = fake_ensure_joplin_available  # type: ignore[method-assign]
    app.load_joplin_notes = fake_load_joplin_notes  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    sent = install_direct_copy_stubs(
        app,
        monkeypatch,
        text="Codex response markdown",
    )

    async with app.run_test():
        app.joplin_configured = True
        app.sent_message_history_by_agent["agent-1"] = [
            "first prompt",
            "last useful prompt",
            "/joplin copy",
        ]
        await app.copy_latest_to_joplin("agent-1")

    assert sent == [("%copy", "/copy")]
    assert captures == ["agent-1"]
    assert loaded == ["agent-1"]
    assert posts[0]["path"] == "/v1/agents/agent-1/joplin/copy"
    payload = posts[0]["json"]
    assert isinstance(payload, dict)
    assert payload["title"] == "Codex Response - last useful prompt"
    assert "## Prompt" in str(payload["body"])
    assert "last useful prompt" in str(payload["body"])
    assert "## Capture" in str(payload["body"])
    assert "Codex /copy via tmux buffer" in str(payload["body"])
    assert "## Response" in str(payload["body"])
    assert "Codex response markdown" in str(payload["body"])


async def test_tui_joplin_copy_tmux_response_prefers_live_tmux_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    app.tmux_direct_enabled = True
    posts: list[dict[str, object]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            return Response({"id": "note-1"})

    async def fake_ensure_joplin_available() -> bool:
        return True

    async def fake_load_joplin_notes(agent_id: str) -> None:
        return None

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_joplin_available = fake_ensure_joplin_available  # type: ignore[method-assign]
    app.load_joplin_notes = fake_load_joplin_notes  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    sent = install_direct_copy_stubs(
        app,
        monkeypatch,
        text="Live tmux response markdown",
        source="pane desktop clipboard: wl-paste",
    )

    async with app.run_test():
        app.joplin_configured = True
        app.agents["agent-1"] = {
            "agent_id": "agent-1",
            "project": "demo",
            "status": "online",
            "metadata": {"codex_session_id": "session-1"},
        }
        app.sent_message_history_by_agent["agent-1"] = ["last useful prompt"]
        await app.copy_latest_to_joplin("agent-1")

    assert sent == [("%copy", "/copy")]
    assert posts[0]["path"] == "/v1/agents/agent-1/joplin/copy"
    payload = posts[0]["json"]
    assert isinstance(payload, dict)
    assert "Live tmux response markdown" in str(payload["body"])


async def test_tui_joplin_copy_replaces_stale_direct_buffer_with_transcript(
    tmp_path: Path,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"codex_session_id": "session-1"},
    }
    notices: list[str] = []
    transcript = CodexTranscriptResult(
        text="Current transcript response",
        session_id="session-1",
        path=tmp_path / "rollout-session-1.jsonl",
        phase="final_answer",
        line_index=9,
        mtime=1_790_000_000.0,
    )

    async def fake_transcript(
        agent_id: str,
        *,
        after_boundary: CodexTranscriptBoundary | None = None,
    ) -> CodexTranscriptResult:
        assert agent_id == "agent-1"
        assert after_boundary is None
        return transcript

    app.copy_codex_transcript_response_text = fake_transcript  # type: ignore[method-assign]
    app.notify = lambda message, **_: notices.append(str(message))  # type: ignore[method-assign]

    capture = await app.validate_direct_codex_copy(
        "agent-1",
        ClipboardCandidate("tmux buffer", "Stale prior response"),
        "idle Codex pane",
    )

    assert capture.text == "Current transcript response"
    assert "replaced unmatched /copy output" in capture.source
    assert any("did not match" in notice for notice in notices)


async def test_tui_joplin_copy_duplicate_guard_and_force_create_intentional_note() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    capture = CodexCopyCapture(
        text="Latest response",
        source="Codex /copy via tmux buffer",
        session_id="session-1",
    )
    app.joplin_copy_fingerprints_by_agent["agent-1"] = {
        "digest": capture.digest,
        "session_id": "session-1",
        "note_id": "prior-note",
    }
    posts: list[dict[str, object]] = []
    notices: list[str] = []
    loads: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "forced-note"}

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            return Response()

    async def fake_ensure() -> bool:
        return True

    async def fake_copy(agent_id: str) -> tuple[str, str]:
        assert agent_id == "agent-1"
        app.last_codex_copy_capture_by_agent[agent_id] = capture
        return capture.text, capture.source

    async def fake_load(agent_id: str) -> None:
        loads.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_joplin_available = fake_ensure  # type: ignore[method-assign]
    app.copy_tmux_response_text = fake_copy  # type: ignore[method-assign]
    app.load_joplin_notes = fake_load  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load  # type: ignore[method-assign]
    app.notify = lambda message, **_: notices.append(str(message))  # type: ignore[method-assign]

    await app.copy_tmux_response_to_joplin("agent-1")

    assert posts == []
    assert app.selected_joplin_note_for_agent("agent-1") == "prior-note"
    assert any("copy force" in notice for notice in notices)

    await app.copy_tmux_response_to_joplin("agent-1", force=True)

    assert posts[0]["path"] == "/v1/agents/agent-1/joplin/copy"
    assert app.selected_joplin_note_for_agent("agent-1") == "forced-note"
    assert app.joplin_copy_fingerprints_by_agent["agent-1"]["note_id"] == "forced-note"
    assert loads == ["agent-1", "agent-1", "agent-1"]


async def test_tui_joplin_copy_title_prefers_prompt_associated_with_transcript() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    capture = CodexCopyCapture(
        text="Latest response",
        source="Codex transcript rollout.jsonl",
        session_id="session-1",
        prompt="prompt typed directly in the embedded Codex terminal",
    )
    created: list[tuple[str, str, str]] = []

    async def fake_ensure() -> bool:
        return True

    async def fake_copy(agent_id: str) -> tuple[str, str]:
        app.last_codex_copy_capture_by_agent[agent_id] = capture
        return capture.text, capture.source

    async def fake_create(
        agent_id: str,
        *,
        title: str,
        body: str,
        success_message: str,
    ) -> dict[str, str]:
        created.append((agent_id, title, body))
        return {"id": "note-1"}

    async def fake_load(_agent_id: str) -> None:
        return None

    app.ensure_joplin_available = fake_ensure  # type: ignore[method-assign]
    app.copy_tmux_response_text = fake_copy  # type: ignore[method-assign]
    app.create_manual_joplin_copy = fake_create  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load  # type: ignore[method-assign]
    app.sent_message_history_by_agent["agent-1"] = ["stale PBX-side prompt"]

    await app.copy_tmux_response_to_joplin("agent-1")

    assert created[0][1] == (
        "Codex Response - prompt typed directly in the embedded Codex terminal"
    )
    assert "prompt typed directly in the embedded Codex terminal" in created[0][2]


async def test_tui_joplin_copy_falls_back_to_transcript_before_tmux_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.joplin_copy_mode = JOPLIN_COPY_MODE_TRANSCRIPT_FIRST
    sent: list[tuple[str, str]] = []
    captured: list[str] = []
    transcript_result = CodexTranscriptResult(
        text="Transcript response markdown",
        session_id="fork-session-123",
        path=tmp_path / "rollout-fork-session.jsonl",
        phase="final_answer",
        line_index=12,
        mtime=1_790_000_000.0,
    )

    async def fake_send_keys_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_read_copied_tmux_response(
        previous_clipboard: str,
    ) -> tuple[str, str]:
        raise RuntimeError("clipboard unavailable")

    async def fake_copy_codex_transcript_response_text(
        agent_id: str,
        *,
        after_boundary: CodexTranscriptBoundary | None = None,
    ) -> CodexTranscriptResult | None:
        assert agent_id == "agent-1"
        assert after_boundary is None
        return transcript_result

    async def fake_capture_tmux_display_for_agent(agent_id: str) -> str | None:
        captured.append(agent_id)
        return "tmux fallback text"

    monkeypatch.setattr(
        "agent_pbx.tui.read_clipboard_text",
        lambda: ("old clipboard", "fake-clipboard"),
    )
    app.send_keys_to_tmux = fake_send_keys_to_tmux  # type: ignore[method-assign]
    app.read_copied_tmux_response = fake_read_copied_tmux_response  # type: ignore[method-assign]
    app.copy_codex_transcript_response_text = fake_copy_codex_transcript_response_text  # type: ignore[method-assign]
    app.capture_tmux_display_for_agent = fake_capture_tmux_display_for_agent  # type: ignore[method-assign]

    copied = await app.copy_tmux_response_text("agent-1")

    assert copied is not None
    assert copied[0] == "Transcript response markdown"
    assert "session fork-ses..." in copied[1]
    assert "phase final_answer" in copied[1]
    assert "line 12" in copied[1]
    assert sent == []
    assert captured == []


async def test_tui_joplin_copy_default_skips_unobservable_copy_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    assert app.joplin_copy_mode == JOPLIN_COPY_MODE_COPY_FIRST
    sent: list[tuple[str, str]] = []
    captured: list[str] = []
    pane = copy_test_pane()
    transcript_result = CodexTranscriptResult(
        text="Transcript response markdown",
        session_id="session-1",
        path=tmp_path / "rollout-session-1.jsonl",
        phase="final_answer",
        line_index=7,
        mtime=1_790_000_000.0,
    )

    async def fake_capture_tmux_display_for_agent(agent_id: str) -> str | None:
        captured.append(agent_id)
        return "visible fallback"

    async def fake_copy_codex_transcript_response_text(
        agent_id: str,
        *,
        after_boundary: CodexTranscriptBoundary | None = None,
    ) -> CodexTranscriptResult | None:
        return transcript_result

    async def fake_resolve(
        agent_id: str,
        *,
        status: Static | None,
    ) -> tmux_support.TmuxPane:
        assert agent_id == "agent-1"
        return pane

    async def fake_dismiss(_: tmux_support.TmuxPane) -> bool:
        return True

    async def fake_snapshot(_: tmux_support.TmuxPane) -> ClipboardSnapshot:
        return ClipboardSnapshot(
            values={},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(False),
        )

    async def fake_await(
        _: tmux_support.TmuxPane,
        __: ClipboardSnapshot,
    ) -> tuple[ClipboardCandidate, str | None]:
        raise RuntimeError("clipboard unavailable")

    def fake_send_literal_keys(pane_id: str, command: str, **_: object) -> None:
        sent.append((pane_id, command))

    app.resolve_tmux_send_pane = fake_resolve  # type: ignore[method-assign]
    app.dismiss_codex_copy_selector = fake_dismiss  # type: ignore[method-assign]
    app.copy_clipboard_snapshot_for_pane = fake_snapshot  # type: ignore[method-assign]
    app.await_codex_copy_candidate = fake_await  # type: ignore[method-assign]
    app.capture_tmux_display_for_agent = fake_capture_tmux_display_for_agent  # type: ignore[method-assign]
    app.copy_codex_transcript_response_text = fake_copy_codex_transcript_response_text  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "send_literal_keys", fake_send_literal_keys)

    copied = await app.copy_tmux_response_text("agent-1")

    assert copied is not None
    assert copied[0] == "Transcript response markdown"
    assert sent == []
    assert captured == []


def test_tui_detects_codex_copy_selector() -> None:
    assert contains_codex_copy_selector(
        "Copy to clipboard\n\n"
        "› 1. Whole response  Here is the command:\n"
        "  2. sh code         echo current\n\n"
        "  enter select · esc back"
    )
    assert contains_codex_copy_selector(
        "Choose what to copy for /copy:\n"
        "  1. Latest response\n"
        "  2. Current conversation\n"
    )
    assert contains_codex_copy_selector(
        "Select content to copy\n\n"
        "› 1. Entire answer\n"
        "  2. Code block\n\n"
        "↑↓ navigate · enter to select · esc to cancel"
    )
    assert contains_codex_copy_selector(
        "Copy response\n\n"
        "  1) Response\n"
        "  2) Reasoning summary\n\n"
        "arrow keys to select · enter to select"
    )
    assert not contains_codex_copy_selector("1. unrelated\n2. still unrelated")
    assert not contains_codex_copy_selector(
        "Please choose one:\n1. Restart the worker\n2. Keep investigating\n"
    )
    whole_prompt_picker = (
        "Copy to clipboard\n\n"
        "› 1. Whole prompt\n"
        "  2. Latest response\n\n"
        "↑↓ navigate · enter to select · esc to cancel"
    )
    assert contains_codex_copy_picker(whole_prompt_picker)
    assert contains_codex_copy_selector(whole_prompt_picker)
    response_option = codex_copy_response_option(whole_prompt_picker)
    assert response_option is not None
    assert response_option.index == 2


async def test_tui_joplin_copy_selects_whole_response_from_interactive_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    keys: list[tuple[str, str]] = []
    raw_captures = iter([
        "Copy to clipboard\n\n"
        "› 1. Whole response  Response preview\n"
        "  2. sh code         echo current\n\n"
        "  enter select · esc back",
        "Codex response after the picker closed",
    ])

    async def fake_capture_tmux_raw_for_pane(pane_id: str) -> str:
        assert pane_id == "%copy"
        return next(raw_captures)

    async def fake_candidates(
        _: tmux_support.TmuxPane,
        *,
        pane_environment: object,
        tmux_transport: object,
    ) -> tuple[ClipboardCandidate, ...]:
        return (ClipboardCandidate("tmux buffer", "Whole response markdown"),)

    async def fake_send_key_to_tmux_pane(pane_id: str, key: str) -> bool:
        keys.append((pane_id, key))
        return True

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.0)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.copy_clipboard_candidates_for_pane = fake_candidates  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    candidate, raw = await app.await_codex_copy_candidate(
        pane,
        ClipboardSnapshot(
            values={"tmux buffer": "old response"},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        ),
    )

    assert candidate == ClipboardCandidate("tmux buffer", "Whole response markdown")
    assert raw == "Codex response after the picker closed"
    assert keys == [("%copy", "1")]


async def test_tui_joplin_copy_selects_latest_response_when_whole_prompt_is_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    keys: list[tuple[str, str]] = []
    raw_captures = iter(
        [
            "Copy to clipboard\n\n"
            "› 1. Whole prompt\n"
            "  2. Latest response\n\n"
            "↑↓ navigate · enter to select · esc to cancel",
            "normal Codex output after selection",
        ]
    )

    async def fake_capture_tmux_raw_for_pane(pane_id: str) -> str:
        assert pane_id == "%copy"
        return next(raw_captures)

    async def fake_send_key_to_tmux_pane(pane_id: str, key: str) -> bool:
        keys.append((pane_id, key))
        return True

    async def fake_candidates(
        _: tmux_support.TmuxPane,
        *,
        pane_environment: object,
        tmux_transport: object,
    ) -> tuple[ClipboardCandidate, ...]:
        return (ClipboardCandidate("tmux buffer", "Latest response markdown"),)

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.0)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.copy_clipboard_candidates_for_pane = fake_candidates  # type: ignore[method-assign]

    candidate, raw = await app.await_codex_copy_candidate(
        pane,
        ClipboardSnapshot(
            values={},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        ),
    )

    assert candidate == ClipboardCandidate("tmux buffer", "Latest response markdown")
    assert raw == "normal Codex output after selection"
    assert keys == [("%copy", "2")]


def test_tui_codex_copy_response_option_ignores_prompt_and_code_choices() -> None:
    option = codex_copy_response_option(
        "Copy to clipboard\n\n"
        "› 1. Whole prompt\n"
        "  2. Reasoning summary\n"
        "  3. Latest response\n"
        "  4. Code block\n\n"
        "↑↓ navigate · enter to select · esc to cancel"
    )

    assert option is not None
    assert option.index == 3
    assert option.label == "Latest response"


async def test_tui_joplin_copy_dismisses_picker_without_response_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    keys: list[tuple[str, str]] = []
    raw_captures = iter(
        [
            "Copy to clipboard\n\n"
            "› 1. Whole prompt\n"
            "  2. Reasoning summary\n\n"
            "↑↓ navigate · enter to select · esc to cancel",
            "Copy to clipboard\n\n"
            "› 1. Whole prompt\n"
            "  2. Reasoning summary\n\n"
            "↑↓ navigate · enter to select · esc to cancel",
            "normal Codex output after cancellation",
        ]
    )

    async def fake_capture_tmux_raw_for_pane(pane_id: str) -> str:
        assert pane_id == "%copy"
        return next(raw_captures)

    async def fake_send_key_to_tmux_pane(pane_id: str, key: str) -> bool:
        keys.append((pane_id, key))
        return True

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.0)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="did not offer a response-safe option"):
        await app.await_codex_copy_candidate(
            pane,
            ClipboardSnapshot(
                values={},
                pane_environment={},
                tmux_transport=tmux_support.TmuxClipboardTransport(False),
            ),
        )

    assert keys == [("%copy", "Escape")]


async def test_tui_joplin_copy_selects_picker_while_clipboard_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    keys: list[tuple[str, str]] = []
    candidate_reads = 0
    raw_captures = iter(
        [
            "Copy to clipboard\n\n"
            "› 1. Whole response  Response preview\n"
            "  2. sh code         echo current\n\n"
            "  enter select · esc back",
            "normal Codex output",
        ]
    )

    async def fake_capture_tmux_raw_for_pane(pane_id: str) -> str:
        assert pane_id == "%copy"
        return next(raw_captures)

    async def fake_candidates(
        _: tmux_support.TmuxPane,
        *,
        pane_environment: object,
        tmux_transport: object,
    ) -> tuple[ClipboardCandidate, ...]:
        nonlocal candidate_reads
        candidate_reads += 1
        return (
            ClipboardCandidate("host clipboard: wl-paste", "stale response"),
            ClipboardCandidate("tmux buffer", "Whole response markdown"),
        )

    async def fake_send_key_to_tmux_pane(pane_id: str, key: str) -> bool:
        keys.append((pane_id, key))
        return True

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.0)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.copy_clipboard_candidates_for_pane = fake_candidates  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    candidate, _raw = await app.await_codex_copy_candidate(
        pane,
        ClipboardSnapshot(
            values={
                "host clipboard: wl-paste": "stale response",
                "tmux buffer": "old response",
            },
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        ),
    )

    assert candidate == ClipboardCandidate("tmux buffer", "Whole response markdown")
    assert keys == [("%copy", "1")]
    assert candidate_reads == 1


async def test_tui_joplin_copy_accepts_matching_unchanged_buffer_after_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    response = "Current complete response"
    raw_captures = iter(
        [
            "Copy to clipboard\n\n"
            "› 1. Whole response\n"
            "  2. Whole prompt\n\n"
            "enter to select · esc to cancel",
            "normal Codex output",
        ]
    )

    async def fake_capture_tmux_raw_for_pane(_pane_id: str) -> str:
        return next(raw_captures)

    async def fake_candidates(
        _: tmux_support.TmuxPane,
        *,
        pane_environment: object,
        tmux_transport: object,
    ) -> tuple[ClipboardCandidate, ...]:
        return (ClipboardCandidate("tmux buffer", response),)

    async def fake_send_key_to_tmux_pane(_pane_id: str, _key: str) -> bool:
        return True

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.0)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.copy_clipboard_candidates_for_pane = fake_candidates  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    candidate, _raw = await app.await_codex_copy_candidate(
        pane,
        ClipboardSnapshot(
            values={"tmux buffer": response},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        ),
        expected_digest=response_text_digest(response),
    )

    assert candidate == ClipboardCandidate("tmux buffer", response)


async def test_tui_joplin_copy_waits_for_late_native_picker_after_clipboard_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = copy_test_pane()
    keys: list[tuple[str, str]] = []
    picker_seen = False
    raw_captures = iter(
        [
            "normal Codex output before the /copy picker",
            (
                "Copy to clipboard\n\n"
                "› 1. Whole response  Response preview\n"
                "  2. sh code         echo current\n\n"
                "  enter select · esc back"
            ),
            "normal Codex output after selection",
        ]
    )

    async def fake_capture_tmux_raw_for_pane(pane_id: str) -> str:
        nonlocal picker_seen
        assert pane_id == "%copy"
        value = next(raw_captures)
        picker_seen = picker_seen or contains_codex_copy_selector(value)
        return value

    async def fake_candidates(
        _: tmux_support.TmuxPane,
        *,
        pane_environment: object,
        tmux_transport: object,
    ) -> tuple[ClipboardCandidate, ...]:
        assert picker_seen, "clipboard candidates must wait for the picker grace window"
        return (ClipboardCandidate("tmux buffer", "Whole response markdown"),)

    async def fake_send_key_to_tmux_pane(pane_id: str, key: str) -> bool:
        keys.append((pane_id, key))
        return True

    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_WAIT_SECONDS", 0.1)
    monkeypatch.setattr("agent_pbx.tui.CODEX_COPY_SELECTOR_SETTLE_SECONDS", 0.02)
    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_POLL_SECONDS", 0.001)
    app.capture_tmux_raw_for_pane = fake_capture_tmux_raw_for_pane  # type: ignore[method-assign]
    app.copy_clipboard_candidates_for_pane = fake_candidates  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    candidate, _raw = await app.await_codex_copy_candidate(
        pane,
        ClipboardSnapshot(
            values={"tmux buffer": "old response"},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(True),
        ),
    )

    assert candidate == ClipboardCandidate("tmux buffer", "Whole response markdown")
    assert keys == [("%copy", "1")]


async def test_tui_joplin_transcript_read_error_falls_back_to_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.joplin_copy_mode = JOPLIN_COPY_MODE_TRANSCRIPT_FIRST
    pane = copy_test_pane()
    sent: list[tuple[str, str]] = []

    async def fake_resolve(
        agent_id: str,
        *,
        status: Static | None,
    ) -> tmux_support.TmuxPane:
        assert agent_id == "agent-1"
        return pane

    async def fake_dismiss(_: tmux_support.TmuxPane) -> bool:
        return True

    async def fake_snapshot(_: tmux_support.TmuxPane) -> ClipboardSnapshot:
        return ClipboardSnapshot(
            values={},
            pane_environment={},
            tmux_transport=tmux_support.TmuxClipboardTransport(False),
        )

    async def fake_await(
        _: tmux_support.TmuxPane,
        __: ClipboardSnapshot,
    ) -> tuple[ClipboardCandidate, str | None]:
        raise RuntimeError("clipboard unavailable")

    async def fake_capture_tmux_display_for_agent(agent_id: str) -> str | None:
        return "visible tmux fallback"

    def fake_send_literal_keys(pane_id: str, command: str, **_: object) -> None:
        sent.append((pane_id, command))

    def raise_decode_error(*args: object, **kwargs: object) -> CodexTranscriptResult:
        raise UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid byte")

    monkeypatch.setattr(
        "agent_pbx.tui.latest_assistant_transcript_for_session",
        raise_decode_error,
    )
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"codex_session_id": "session-1"},
    }
    app.resolve_tmux_send_pane = fake_resolve  # type: ignore[method-assign]
    app.dismiss_codex_copy_selector = fake_dismiss  # type: ignore[method-assign]
    app.copy_clipboard_snapshot_for_pane = fake_snapshot  # type: ignore[method-assign]
    app.await_codex_copy_candidate = fake_await  # type: ignore[method-assign]
    app.capture_tmux_display_for_agent = fake_capture_tmux_display_for_agent  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "send_literal_keys", fake_send_literal_keys)

    copied = await app.copy_tmux_response_text("agent-1")

    assert copied == ("visible tmux fallback", "tmux capture fallback")
    assert sent == [("%copy", "/copy")]


async def test_tui_tmux_copy_rejects_unchanged_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    monkeypatch.setattr("agent_pbx.tui.CLIPBOARD_COPY_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(
        "agent_pbx.tui.read_clipboard_text",
        lambda: ("old clipboard", "fake-clipboard"),
    )

    with pytest.raises(RuntimeError, match="did not produce readable clipboard"):
        await app.read_copied_tmux_response("old clipboard")


def test_tui_codex_session_ids_prefer_fork_and_current_before_source() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {
            "source_codex_session_id": "source-session",
            "codex_session_id": "current-session",
            "fork_codex_session_id": "fork-session",
            "last_resume_codex_session_id": "resume-session",
            "codex_thread_id": "current-session",
            "default_source_codex_session_id": "default-source",
        },
    }

    assert app.codex_session_ids_for_agent("agent-1") == (
        "fork-session",
        "resume-session",
        "current-session",
        "source-session",
        "default-source",
    )


async def test_tui_active_codex_session_prefers_rollout_open_in_target_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    pane = copy_test_pane()
    rollout = tmp_path / "sessions" / "2026" / "10" / "02" / "rollout-live.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "session-live"}})
        + "\n",
        encoding="utf-8",
    )
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "metadata": {"codex_session_id": "session-stale"},
    }
    app.codex_home_dir = lambda: tmp_path  # type: ignore[method-assign]
    monkeypatch.setattr(
        tmux_support,
        "pane_open_rollout_paths",
        lambda *args, **kwargs: (rollout,),
    )

    active = await app.active_codex_session_for_agent("agent-1", pane=pane)

    assert active is not None
    assert active.session_id == "session-live"
    assert active.path == rollout
    assert active.source == "pane-open-rollout"
    assert active.confidence == "high"


async def test_tui_fork_transcript_fallback_excludes_source_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    pane = copy_test_pane()
    sessions = tmp_path / "sessions" / "2026" / "10" / "02"
    sessions.mkdir(parents=True)

    def write_rollout(session_id: str, response: str) -> Path:
        path = sessions / f"rollout-{session_id}.jsonl"
        records = [
            {"type": "session_meta", "payload": {"id": session_id}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": response}],
                },
            },
        ]
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    write_rollout("source-session", "source response")
    current_path = write_rollout("fork-session", "fork response")
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "metadata": {
            "operator_role": "fork",
            "codex_session_id": "fork-session",
            "fork_codex_session_id": "fork-session",
            "source_codex_session_id": "source-session",
        },
    }
    app.codex_home_dir = lambda: tmp_path  # type: ignore[method-assign]
    monkeypatch.setattr(
        tmux_support,
        "pane_open_rollout_paths",
        lambda *args, **kwargs: (),
    )

    assert app.codex_transcript_fallback_ids_for_agent("agent-1") == (
        "fork-session",
    )
    active = await app.active_codex_session_for_agent("agent-1", pane=pane)

    assert active is not None
    assert active.session_id == "fork-session"
    assert active.path == current_path


async def test_tui_repairs_tui_owned_fork_session_without_overwriting_source(
    tmp_path: Path,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    recorded: list[dict[str, object]] = []
    app.agents["fork-1"] = {
        "agent_id": "fork-1",
        "project": "demo",
        "metadata": {
            "launched_by": "agent-pbx-tui",
            "operator_role": "fork",
            "logical_operator_id": "operator-0",
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": "source-session",
            "codex_session_id": "stale-session",
            "fork_codex_session_id": "stale-session",
        },
    }

    async def fake_record_operator_fork(**kwargs: object) -> None:
        recorded.append(kwargs)

    app.record_operator_fork = fake_record_operator_fork  # type: ignore[method-assign]
    resolved = ActiveCodexSession(
        agent_id="fork-1",
        pane_id="%42",
        session_id="live-session",
        path=tmp_path / "rollout-live.jsonl",
        source="pane-open-rollout",
        confidence="high",
    )

    await app.repair_active_codex_session_metadata(resolved)

    assert len(recorded) == 1
    assert recorded[0]["fork_codex_session_id"] == "live-session"
    metadata = recorded[0]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["codex_session_id"] == "live-session"
    assert metadata["fork_codex_session_id"] == "live-session"
    assert metadata["source_codex_session_id"] == "source-session"
    assert metadata["previous_codex_session_id"] == "stale-session"


async def test_tui_tmux_joplin_log_response_appends_copied_response() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    calls: list[tuple[str, str, str]] = []
    copy_kwargs: list[dict[str, object]] = []
    boundary = CodexTranscriptBoundary(
        session_id="session-1",
        path=Path("/tmp/rollout-session-1.jsonl"),
        line_index=9,
        mtime=1.0,
    )

    async def fake_wait_for_tmux_log_idle(agent_id: str) -> bool:
        return agent_id == "agent-1"

    async def fake_copy_tmux_response_text(
        agent_id: str,
        **kwargs: object,
    ) -> tuple[str, str] | None:
        assert agent_id == "agent-1"
        copy_kwargs.append(kwargs)
        return "Codex response markdown", "fake-clipboard"

    async def fake_append_joplin_log_section(
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> bool:
        calls.append((agent_id, title, body))
        return True

    app.wait_for_tmux_log_idle = fake_wait_for_tmux_log_idle  # type: ignore[method-assign]
    app.copy_tmux_response_text = fake_copy_tmux_response_text  # type: ignore[method-assign]
    app.append_joplin_log_section = fake_append_joplin_log_section  # type: ignore[method-assign]
    app.joplin_log_transcript_boundary_by_agent["agent-1"] = boundary

    await app.capture_tmux_joplin_log_response("agent-1")

    assert copy_kwargs == [
        {"transcript_boundary": boundary, "allow_transcript": True}
    ]
    assert "agent-1" not in app.joplin_log_transcript_boundary_by_agent
    assert calls == [
        (
            "agent-1",
            "Agent Response",
            "Clipboard: fake-clipboard\n\nCodex response markdown",
        )
    ]


async def test_tui_tmux_joplin_log_records_transcript_boundary() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    boundary = CodexTranscriptBoundary(
        session_id="session-1",
        path=Path("/tmp/rollout-session-1.jsonl"),
        line_index=4,
        mtime=1.0,
    )
    workers: list[str] = []

    async def fake_append_joplin_log_section(
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> bool:
        assert (agent_id, title, body) == ("agent-1", "Operator Prompt", "prompt")
        return True

    async def fake_codex_transcript_boundary_for_agent(
        agent_id: str,
    ) -> CodexTranscriptBoundary | None:
        assert agent_id == "agent-1"
        return boundary

    def fake_run_worker(*args: object, **kwargs: object) -> object:
        workers.append(str(kwargs.get("name")))
        coroutine = args[0]
        if inspect.iscoroutine(coroutine):
            coroutine.close()
        return object()

    app.append_joplin_log_section = fake_append_joplin_log_section  # type: ignore[method-assign]
    app.codex_transcript_boundary_for_agent = fake_codex_transcript_boundary_for_agent  # type: ignore[method-assign]
    app.run_worker = fake_run_worker  # type: ignore[method-assign]

    await app.record_tmux_joplin_interaction("agent-1", "prompt")

    assert app.joplin_log_transcript_boundary_by_agent["agent-1"] == boundary
    assert workers == ["joplin-tmux-log-agent-1"]


async def test_tui_follow_up_exact_theme_command_executes_without_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        message = app.query_one("#message", TextArea)
        message.text = "/theme minimal"
        await app.send_input()

    assert app.ui_theme == "minimal"
    assert message.text == ""


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


async def test_tui_palette_gitpush_modal_sends_optional_branch() -> None:
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
        app.palette_git_push()
        await pilot.pause()
        branch = app.screen.query_one("#palette-gitpush-branch", Input)
        branch.value = "dev"
        app.screen.submit()  # type: ignore[attr-defined]
        await pilot.pause()

    assert sent == [("agent-1", "!git push origin dev")]
    assert captures == ["agent-1"]


async def test_tui_gitpush_slash_input_defaults_to_current_branch() -> None:
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
        message = app.query_one("#tmux-message", TextArea)
        message.text = "/gitpush"
        await app.send_tmux_input()
        message.text = "/gitpush release/v1"
        await app.send_tmux_input()

    assert sent == [
        ("agent-1", "!git push origin HEAD"),
        ("agent-1", "!git push origin release/v1"),
    ]


def test_tui_gitdiff_passthrough_command_formats_targets() -> None:
    assert git_diff_passthrough_command("") == "!git diff"
    assert git_diff_passthrough_command("README.md") == "!git diff -- README.md"
    assert git_diff_passthrough_command("main:README.md") == "!git diff main:README.md"
    assert (
        git_diff_passthrough_command("docs/My File.md")
        == "!git diff -- 'docs/My File.md'"
    )


def test_tui_gitpush_passthrough_command_formats_branch() -> None:
    assert git_push_passthrough_command("") == "!git push origin HEAD"
    assert git_push_passthrough_command("dev") == "!git push origin dev"
    assert git_push_passthrough_command("feature/mobile-ui") == (
        "!git push origin feature/mobile-ui"
    )
    assert git_push_passthrough_command("release candidate") == (
        "!git push origin 'release candidate'"
    )
    assert parse_git_push_slash_command("/gitpush") == ""
    assert parse_git_push_slash_command("/gitpush dev") == "dev"
    assert parse_git_push_slash_command("/gitpush-dev") is None


async def test_tui_plan_selection_command_sends_latest_choice_with_notes() -> None:
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
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:2 Prefer B."
        await app.send_input()
        await pilot.pause()

    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": "Selected plan option: B\n\nOperator notes:\nPrefer B.",
                "plan_choice": {"label": "B", "option": "B"},
            },
        )
    ]
    assert refreshed_events == 1
    assert loaded_threads == ["agent-1"]


async def test_tui_plan_selection_uses_explicit_target_agent_latest_options() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []
    loaded_threads: list[str] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        loaded_threads.append(agent_id)

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "one",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "done",
                "project": "two",
                "last_seen_at": 124.0,
            },
        }
        app.selected_agent_id = "agent-2"
        app.latest_report_by_agent = {
            "agent-1": {
                "report_id": "report-1",
                "agent_id": "agent-1",
                "plan_options": ["Stay in plan", "Start coding"],
            }
        }
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:2 Use the approved path."
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": (
                    "Selected plan option: Start coding\n\n"
                    "Operator notes:\nUse the approved path."
                ),
                "plan_choice": {"label": "Start coding", "option": "Start coding"},
            },
        )
    ]
    assert loaded_threads == ["agent-1"]
    assert message_text == ""


async def test_tui_palette_plan_toggles_mode_immediately() -> None:
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
        await pilot.pause()

    assert queued == [("agent-1", "send_input", {"message": "/plan"})]
    assert app.plan_mode_active_agent_ids == {"agent-1"}
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
    typed: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_send_keys_to_tmux(agent_id: str, message: str) -> bool:
        typed.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.send_keys_to_tmux = fake_send_keys_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_selected_agent(agent_id: str) -> None:
        return None

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_selected_agent = fake_refresh_selected_agent  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-1"
        app.pending_slash_command_by_agent["agent-1"] = "/plan"
        app.query_one("#tmux-message", TextArea).text = "Investigate options."
        await app.send_input()
        assert app.pending_slash_command_by_agent == {}
        await pilot.pause()

    assert typed == [("agent-1", "/plan")]
    assert sent == [("agent-1", render_plan_prompt("Investigate options."))]


async def test_tui_plan_selection_command_can_send_thread_choice_to_tmux() -> None:
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

        app.active_agent_tab = "thread-tab"
        app.query_one("#tmux-message", TextArea).text = "/plan sel:1 Go now."
        await app.send_tmux_input()
        await pilot.pause()

    assert sent == [
        (
            "agent-1",
            "Selected plan option: Proceed\n\nOperator notes:\nGo now.",
        )
    ]
    assert captured == ["agent-1"]


async def test_tui_palette_dynamic_plan_option_commands_prepare_reply() -> None:
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

        assert app.query_one("#message", TextArea).text == "/plan:2 "


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
        plan_hint = app.query_one("#plan-hint", Static)
        table = app.query_one("#thread", DataTable)
        plan_row_count = plan_options.row_count
        plan_option_text = plan_options.get_row_at(0)[1]
        plan_marker = table.get_row_at(0)[3]
        app.select_thread_item("command:c1")
        detail = app.query_one("#thread-detail", TextArea).text

    assert app.selected_thread_item_id == "command:c1"
    assert "Plan Options:" in report_detail
    assert plan_row_count == 1
    assert plan_option_text == "Next"
    assert plan_marker == "PLAN:1"
    assert plan_hint.renderable == "Reply with /plan:1 optional notes."
    assert "Command: c1" in detail
    assert "Proceed" in detail
    assert '"ok": true' in detail


async def test_tui_structured_plan_options_render_and_queue_metadata() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, object]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, object]
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

    async with app.run_test():
        item = {
            "item_id": "report:r1",
            "kind": "report",
            "agent_id": "agent-1",
            "created_at": 123.0,
            "status": "needs_input",
            "title": "Choose",
            "body": "Pick a path",
            "metadata": {
                "plan_options": [
                    {
                        "id": "incremental",
                        "label": "Incremental hardening",
                        "description": "Fix lifecycle first.",
                    }
                ]
            },
        }
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.thread_items = {"report:r1": item}
        app.select_thread_item("report:r1")
        table = app.query_one("#plan-options", DataTable)
        app.query_one("#message", TextArea).text = "/plan:1 Ship this."
        await app.send_input()

    assert table.get_row_at(0)[1] == "Incremental hardening: Fix lifecycle first."
    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": (
                    "Selected plan option: Incremental hardening\n\n"
                    "Operator notes:\nShip this."
                ),
                "plan_choice": {
                    "id": "incremental",
                    "label": "Incremental hardening",
                    "description": "Fix lifecycle first.",
                    "option": {
                        "id": "incremental",
                        "label": "Incremental hardening",
                        "description": "Fix lifecycle first.",
                    },
                },
            },
        )
    ]


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


async def test_tui_hide_cursor_falls_back_to_nearest_remaining_row() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            f"agent-{index}": {
                "agent_id": f"agent-{index}",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": float(100 - index),
                "metadata": {},
            }
            for index in range(1, 4)
        }
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        table.move_cursor(row=1, animate=False, scroll=False)

        app.remember_cursor_after_agent_removal("agent-2")
        app.agents.pop("agent-2")
        app.render_agents()

        assert app.agent_id_at_cursor() == "agent-3"


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

    assert deleted == [("agent-1", True)]
    assert app.selected_agent_id is None
    assert "Purged agent-1." in detail
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

    assert deleted == [("agent-2", False), ("agent-1", True)]


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


async def test_tui_unhide_selected_agent_restores_hidden_row() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    restored: list[str] = []
    agents_refreshed = 0
    events_refreshed = 0

    async def fake_unhide_agent(agent_id: str) -> dict[str, object]:
        restored.append(agent_id)
        return {
            "agent_id": agent_id,
            "agent_type": "caller",
            "project": "demo",
            "name": None,
            "status": "done",
            "effective_status": "done",
            "pbx_active": True,
            "metadata": {},
            "created_at": 1.0,
            "last_seen_at": 2.0,
            "dismissed_at": None,
        }

    async def fake_refresh_agents() -> None:
        nonlocal agents_refreshed
        agents_refreshed += 1

    async def fake_refresh_events() -> None:
        nonlocal events_refreshed
        events_refreshed += 1

    app.unhide_agent = fake_unhide_agent  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.stop_refresh_timers()
        agents_refreshed = 0
        events_refreshed = 0
        app.show_hidden_agents = True
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "status": "done",
                "project": "demo",
                "last_seen_at": 2.0,
                "dismissed_at": 3.0,
            }
        }
        app.render_agents()
        app.query_one("#agents", DataTable).focus()
        await app.unhide_selected_agent()
        detail = app.query_one("#detail", TextArea).text

    assert restored == ["agent-1"]
    assert agents_refreshed == 1
    assert events_refreshed == 1
    assert app.agents["agent-1"]["dismissed_at"] is None
    assert "Unhid agent-1." in detail


async def test_tui_start_operator_configures_mcp_and_launch_env(monkeypatch) -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    configured: list[tuple[str, str]] = []
    launches: list[dict[str, object]] = []
    sent: list[tuple[str, str]] = []
    posts: list[dict[str, object]] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response({"ok": True})

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            return Response(
                {
                    "agent_id": "operator-0",
                    "agent_type": "operator",
                    "project": "agent-pbx-operator",
                    "name": "operator-0",
                    "status": "registered",
                    "effective_status": "registered",
                    "pbx_active": True,
                    "metadata": kwargs["json"]["metadata"],  # type: ignore[index]
                    "created_at": 1.0,
                    "last_seen_at": 1.0,
                }
            )

    async def fake_configure_operator_codex_mcp(
        *,
        codex_command: str,
        mcp_url: str,
    ) -> None:
        configured.append((codex_command, mcp_url))

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%42"

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, message))
        return True

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    async def fake_refresh_joplin_status() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        return True

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.refresh_joplin_status = fake_refresh_joplin_status  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)

    async with app.run_test():
        await app.start_operator_agent()

    assert configured == [("codex", "http://127.0.0.1:8765/mcp")]
    assert posts[0]["path"] == "/v1/agents/register"
    metadata = posts[0]["json"]["metadata"]  # type: ignore[index]
    assert metadata["mcp_url"] == "http://127.0.0.1:8765/mcp"
    assert metadata["token_env"] == "AGENT_PBX_TOKEN"
    assert "secret" not in json.dumps(metadata)
    assert posts[1]["path"] == "/v1/agents/register"
    assert posts[1]["json"]["metadata"]["tmux_pane_id"] == "%42"  # type: ignore[index]
    assert launches[0]["env"] == {
            "AGENT_PBX_SERVER_URL": "http://127.0.0.1:8765",
            "AGENT_PBX_MCP_URL": "http://127.0.0.1:8765/mcp",
            "AGENT_PBX_AGENT_ID": "operator-0",
            "AGENT_PBX_REPORTING_AGENT_ID": "operator-0",
            "AGENT_PBX_AGENT_TYPE": "operator",
        "AGENT_PBX_AGENT_PROJECT": "agent-pbx-operator",
        "AGENT_PBX_PBX_MODE": "report",
        "AGENT_PBX_OPERATOR_ID": "operator-0",
        "AGENT_PBX_OPERATOR_ROLE": "root",
        "AGENT_PBX_LOGICAL_OPERATOR_ID": "operator-0",
        "AGENT_PBX_OPERATOR_CWD": str(Path.cwd()),
        "AGENT_PBX_CWD": str(Path.cwd()),
        "AGENT_PBX_CODEX_MODEL_PRESET": "sol-5.6-xhigh",
        "AGENT_PBX_CODEX_MODEL": "gpt-5.6-sol",
        "AGENT_PBX_CODEX_REASONING_EFFORT": "xhigh",
        "AGENT_PBX_CODEX_REASONING_SUMMARY": "detailed",
        "AGENT_PBX_CODEX_VERBOSITY": "high",
        "AGENT_PBX_TOKEN": "secret",
    }
    assert app.tmux_agent_targets["operator-0"] == "%42"
    assert sent[0][0] == "%42"
    assert "operator-0" in sent[0][1]
    assert "Keep the root turn active while campaign assignments are running" in sent[0][1]


async def test_tui_start_operator_does_not_bind_stale_caller_when_operator_selected(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response({"ok": True})

        async def post(self, path: str, **kwargs: object) -> Response:
            assert path == "/v1/agents/register"
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            return Response(
                {
                    "agent_id": body["agent_id"],  # type: ignore[index]
                    "agent_type": "operator",
                    "project": "agent-pbx-operator",
                    "name": body["agent_id"],  # type: ignore[index]
                    "status": "registered",
                    "effective_status": "registered",
                    "pbx_active": True,
                    "metadata": body["metadata"],  # type: ignore[index]
                    "created_at": 1.0,
                    "last_seen_at": 1.0,
                }
            )

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%42"

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        _ = pane_id, message, status
        return True

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        _ = agent_id
        return True

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.selected_caller_agent_id_for_fork = lambda: "caller-1"  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "status": "working",
                "metadata": {
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "session-caller-1",
                },
            },
        }
        app.selected_agent_id = "operator-0"
        await app.start_operator_agent()

    assert len(launches) == 1
    assert len(posts) == 2
    metadata = posts[0]["json"]["metadata"]  # type: ignore[index]
    assert metadata["default_source_caller_agent_id"] == ""
    assert metadata["default_source_codex_session_id"] == ""


async def test_tui_resume_operator_uses_previous_session_when_live_pane_exists(
    monkeypatch,
) -> None:
    preserved_cwd = "/home/me/preserved-operator-project"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    sent: list[tuple[str, str]] = []
    posts: list[dict[str, object]] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            return Response(
                {
                    "agent_id": body["agent_id"],  # type: ignore[index]
                    "agent_type": "operator",
                    "project": body["project"],  # type: ignore[index]
                    "name": body.get("name"),  # type: ignore[union-attr]
                    "status": "online",
                    "effective_status": "online",
                    "pbx_active": body.get("pbx_active", True),  # type: ignore[union-attr]
                    "metadata": body["metadata"],  # type: ignore[index]
                    "created_at": 1.0,
                    "last_seen_at": 1.0,
                }
            )

    async def fake_auth_ready() -> bool:
        return True

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, message))
        return True

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        return True

    def fake_respawn_pane(pane_id: str, **kwargs: object) -> None:
        respawns.append({"pane_id": pane_id, **kwargs})

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx-operators",
                "0",
                "0",
                "%152",
                True,
                "node",
                "operator-0",
                preserved_cwd,
                100,
                30,
                200,
                window_name="operator-0",
            )
        ]

    def fake_operator_session_candidates(agent_id: str) -> list[OperatorSessionCandidate]:
        assert agent_id == "operator-0"
        return [
            OperatorSessionCandidate(
                session_id="new-session",
                timestamp=20.0,
                source="codex.sessions",
            ),
            OperatorSessionCandidate(
                session_id="old-session",
                timestamp=10.0,
                source="codex.sessions",
            ),
        ]

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_operator_auth_ready = fake_auth_ready  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.operator_session_candidates = fake_operator_session_candidates  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(
        tmux_support,
        "pane_start_command",
        lambda _target: "codex resume current-session",
    )
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(tmux_support, "capture_pane", lambda *_args, **_kwargs: "")

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "name": "operator-0",
                "pbx_active": True,
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "launched_by": "agent-pbx-tui",
                    "cwd": preserved_cwd,
                },
            }
        }
        app.tmux_agent_targets["operator-0"] = "%152"
        app.selected_agent_id = "operator-0"
        await app.resume_selected_operator()

    assert respawns[0]["pane_id"] == "%152"
    launch_argv = shlex.split(str(respawns[0]["command"]))
    assert launch_argv[:4] == ["codex", "resume", "--cd", preserved_cwd]
    assert launch_argv[-1] == "old-session"
    assert "-c" in launch_argv
    assert any(item.startswith("mcp_servers.agent-pbx=") for item in launch_argv)
    launch_env = respawns[0]["env"]
    assert isinstance(launch_env, dict)
    assert launch_env["AGENT_PBX_RESUME_CODEX_SESSION_ID"] == "old-session"
    assert launch_env["AGENT_PBX_REPORTING_AGENT_ID"] == "operator-0"
    assert sent[0][0] == "%152"
    assert "agent_id: operator-0" in sent[0][1]
    assert app.tmux_agent_targets["operator-0"] == "%152"
    register_body = posts[-1]["json"]
    assert isinstance(register_body, dict)
    register_metadata = register_body["metadata"]
    assert isinstance(register_metadata, dict)
    assert register_metadata["last_resume_codex_session_id"] == "old-session"


async def test_tui_operator_history_opens_selectable_modal() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    def fake_operator_session_candidates(agent_id: str) -> list[OperatorSessionCandidate]:
        assert agent_id == "operator-0"
        return [
            OperatorSessionCandidate(
                session_id="new-session",
                timestamp=20.0,
                source="codex.sessions",
                summary="new prompt",
            ),
            OperatorSessionCandidate(
                session_id="old-session",
                timestamp=10.0,
                source="metadata.operator_session_history",
                summary="old prompt",
            ),
        ]

    app.operator_session_candidates = fake_operator_session_candidates  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            }
        }
        app.tmux_agent_targets["operator-0"] = "%152"
        app.selected_agent_id = "operator-0"
        await app.show_selected_operator_history()
        await pilot.pause()

        assert isinstance(app.screen, OperatorHistoryScreen)
        table = app.screen.query_one("#operator-history-table", DataTable)
        summary = app.screen.query_one("#operator-history-summary", Static)

    assert table.row_count == 2
    assert table.get_row("old-session")[3] == "old-session"
    assert "Default resume target: old-session" in str(summary.renderable)


async def test_tui_operator_history_modal_resumes_highlighted_candidate() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    resumed: list[tuple[str | None, str | None]] = []
    candidates = [
        OperatorSessionCandidate(
            session_id="new-session",
            timestamp=20.0,
            source="codex.sessions",
        ),
        OperatorSessionCandidate(
            session_id="old-session",
            timestamp=10.0,
            source="codex.sessions",
        ),
    ]

    async def fake_resume_selected_operator(
        *,
        agent_id: str | None = None,
        resume_candidate: OperatorSessionCandidate | None = None,
    ) -> None:
        resumed.append(
            (
                agent_id,
                resume_candidate.session_id if resume_candidate is not None else None,
            )
        )

    app.resume_selected_operator = fake_resume_selected_operator  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await app.push_screen(
            OperatorHistoryScreen(
                agent_id="operator-0",
                candidates=candidates,
                resume_target=candidates[1],
            )
        )
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, OperatorHistoryScreen)
        table = screen.query_one("#operator-history-table", DataTable)
        table.move_cursor(row=1, animate=False)
        screen.resume_candidate()
        await pilot.pause()

    assert resumed == [("operator-0", "old-session")]


async def test_tui_restart_tmux_caller_resumes_known_session(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    captures: list[str] = []

    class Response:
        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            return Response(
                {
                    "agent_id": body["agent_id"],  # type: ignore[index]
                    "agent_type": body["agent_type"],  # type: ignore[index]
                    "project": body["project"],  # type: ignore[index]
                    "name": body["name"],  # type: ignore[index]
                    "pbx_active": body["pbx_active"],  # type: ignore[index]
                    "metadata": body["metadata"],  # type: ignore[index]
                }
            )

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx",
                "0",
                "0",
                "%10",
                True,
                "node",
                "agent-pbx",
                str(Path.cwd()),
                100,
                30,
                200,
                window_name="agent-1",
            )
        ]

    def fake_respawn_pane(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda target: target == "%10")
    monkeypatch.setattr(
        tmux_support,
        "pane_start_command",
        lambda target: "codex --search resume session-1",
    )

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "project": "demo",
                "name": "Agent 1",
                "pbx_active": True,
                "metadata": {
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "session-1",
                },
            }
        }
        app.tmux_agent_targets["agent-1"] = "%10"
        app.tmux_manual_override_agent_ids.add("agent-1")
        app.tmux_direct_agent_modes["agent-1"] = True
        await app.restart_tmux_codex_session("agent-1")

    assert respawns[0]["target"] == "%10"
    launch_argv = shlex.split(str(respawns[0]["command"]))
    assert launch_argv[:5] == [
        "codex",
        "--search",
        "resume",
        "--cd",
        str(Path.cwd()),
    ]
    assert launch_argv[-1] == "session-1"
    config_values = [
        launch_argv[index + 1]
        for index, value in enumerate(launch_argv[:-1])
        if value == "-c"
    ]
    assert 'model="gpt-5.6-sol"' in config_values
    assert 'model_reasoning_effort="high"' in config_values
    assert 'model_reasoning_summary="detailed"' in config_values
    assert 'model_verbosity="high"' in config_values
    assert app.tmux_agent_targets["agent-1"] == "%10"
    assert posts[0]["path"] == "/v1/agents/register"
    metadata = posts[0]["json"]["metadata"]  # type: ignore[index]
    assert metadata["tmux_pane_id"] == "%10"
    assert metadata["codex_command"] == "codex --search"
    assert metadata["last_tmux_restart_mode"] == "in_place"
    assert captures == ["agent-1"]


async def test_tui_launch_restart_pane_retries_dead_replacement(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    launches: list[str] = []

    def fake_launch_pane(**_: object) -> str:
        pane_id = "%dead" if not launches else "%live"
        launches.append(pane_id)
        return pane_id

    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target == "%live")

    pane_id = await app.launch_restart_pane(
        session_name="agent-pbx",
        window_name="agent-1",
        command="codex resume session-1",
        label="agent-1",
    )

    assert pane_id == "%live"
    assert launches == ["%dead", "%live"]


async def test_tui_respawn_restart_pane_rolls_back_in_same_pane_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    respawns: list[dict[str, object]] = []

    def fake_respawn(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_LAUNCH_ATTEMPTS", 2)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda _target: True)
    monkeypatch.setattr(tmux_support, "pane_remain_on_exit", lambda _target: False)
    remain_on_exit: list[bool] = []
    monkeypatch.setattr(
        tmux_support,
        "set_pane_remain_on_exit",
        lambda _target, enabled: remain_on_exit.append(enabled),
    )
    monkeypatch.setattr(tmux_support, "pane_dead_status", lambda _target: 1)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: len(respawns) >= 3)

    with pytest.raises(RuntimeError, match="restored the prior command"):
        await app.respawn_restart_pane(
            pane_id="%10",
            command="codex resume new-session",
            label="agent-1",
            cwd="/tmp/project",
            env={"AGENT_PBX_RESUME_CODEX_SESSION_ID": "new-session"},
            rollback_command="codex resume prior-session",
            rollback_cwd="/tmp/project",
            rollback_env={"AGENT_PBX_RESUME_CODEX_SESSION_ID": "prior-session"},
        )

    assert [record["target"] for record in respawns] == ["%10", "%10", "%10"]
    assert [record["command"] for record in respawns] == [
        "codex resume new-session",
        "codex resume new-session",
        "codex resume prior-session",
    ]
    assert remain_on_exit == [True, False]


async def test_tui_respawn_restart_pane_retains_dead_pane_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    respawns: list[str] = []

    def fake_respawn(_target: str, **kwargs: object) -> None:
        respawns.append(str(kwargs["command"]))

    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_LAUNCH_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda _target: True)
    monkeypatch.setattr(tmux_support, "pane_remain_on_exit", lambda _target: False)
    remain_on_exit: list[bool] = []
    monkeypatch.setattr(
        tmux_support,
        "set_pane_remain_on_exit",
        lambda _target, enabled: remain_on_exit.append(enabled),
    )
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: False)
    monkeypatch.setattr(tmux_support, "pane_dead_status", lambda _target: 17)

    with pytest.raises(RuntimeError, match="exited with status 17"):
        await app.respawn_restart_pane(
            pane_id="%10",
            command="codex resume new-session",
            label="agent-1",
            rollback_command="codex resume prior-session",
        )

    assert respawns == [
        "codex resume new-session",
        "codex resume prior-session",
    ]
    assert remain_on_exit == [True]


def test_tui_identifies_real_codex_resume_session_commands() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert app.codex_resume_session_id_from_command("codex resume session-1") == "session-1"
    assert (
        app.codex_resume_session_id_from_command(
            "codex --search resume --cd /tmp/project session-2"
        )
        == "session-2"
    )
    assert (
        app.codex_resume_session_id_from_command(
            "node /opt/codex/bin/codex resume session-3"
        )
        == "session-3"
    )
    assert (
        app.codex_resume_session_id_from_command(
            "codex fork source-session 'please resume after checking the diff'"
        )
        is None
    )
    assert app.codex_resume_session_id_from_command("zsh -lc 'echo resume session-4'") is None


async def test_tui_respawn_restart_pane_rolls_back_on_codex_lease_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    respawns: list[dict[str, object]] = []

    def fake_respawn(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_LAUNCH_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda _target: True)
    monkeypatch.setattr(tmux_support, "pane_remain_on_exit", lambda _target: False)
    remain_on_exit: list[bool] = []
    monkeypatch.setattr(
        tmux_support,
        "set_pane_remain_on_exit",
        lambda _target, enabled: remain_on_exit.append(enabled),
    )
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(
        tmux_support,
        "capture_pane",
        lambda *_args, **_kwargs: (
            "This conversation is open in another app.\n"
            "Close it there and press r to continue here."
        ),
    )

    with pytest.raises(CodexSessionLeaseConflictError, match="lease dialog"):
        await app.respawn_restart_pane(
            pane_id="%10",
            command="codex resume new-session",
            label="agent-1",
            rollback_command="codex resume prior-session",
        )

    assert [record["command"] for record in respawns] == [
        "codex resume new-session",
        "codex resume prior-session",
    ]
    assert remain_on_exit == [True, False]


async def test_tui_restart_tmux_caller_uses_current_shell_for_unknown_launch_command(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    captures: list[str] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx",
                "0",
                "0",
                "%10",
                True,
                "zsh",
                "agent-pbx",
                str(Path.cwd()),
                100,
                30,
                200,
                window_name="agent-1",
            )
        ]

    def fake_respawn_pane(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda target: target == "%10")
    monkeypatch.setattr(tmux_support, "pane_start_command", lambda target: "zsh")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "session-1",
                },
            }
        }
        app.tmux_agent_targets["agent-1"] = "%10"
        app.tmux_manual_override_agent_ids.add("agent-1")
        app.tmux_direct_agent_modes["agent-1"] = True
        await app.restart_tmux_codex_session("agent-1")

    assert respawns[0]["target"] == "%10"
    launch_argv = shlex.split(str(respawns[0]["command"]))
    assert launch_argv[:4] == [
        "codex",
        "resume",
        "--cd",
        str(Path.cwd()),
    ]
    assert launch_argv[-1] == "session-1"
    config_values = [
        launch_argv[index + 1]
        for index, value in enumerate(launch_argv[:-1])
        if value == "-c"
    ]
    assert 'model="gpt-5.6-sol"' in config_values
    assert 'model_reasoning_effort="high"' in config_values
    assert captures in ([], ["agent-1"])


async def test_tui_restart_tmux_requires_tmux_direct(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=False)
    app.tmux_features_available = True

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        raise AssertionError("restart should not inspect panes when tmux is disabled")

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "session-1",
                },
            }
        }
        await app.restart_tmux_codex_session("agent-1")

    assert "agent-1" not in app.tmux_agent_targets


async def test_tui_restart_operator_root_resumes_current_session(monkeypatch) -> None:
    monkeypatch.setenv(OPERATOR_MCP_APPROVAL_SERVERS_ENV, "agent-pbx,workerbee")
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response({"ok": True})

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            return Response(
                {
                    "agent_id": body["agent_id"],  # type: ignore[index]
                    "agent_type": "operator",
                    "project": body["project"],  # type: ignore[index]
                    "name": body.get("name"),  # type: ignore[union-attr]
                    "pbx_active": body.get("pbx_active", True),  # type: ignore[union-attr]
                    "metadata": body["metadata"],  # type: ignore[index]
                }
            )

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx-operators",
                "0",
                "0",
                "%30",
                True,
                "node",
                "operator-0",
                str(Path.cwd()),
                100,
                30,
                200,
                window_name="operator-0",
            )
        ]

    def fake_respawn_pane(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    def fake_operator_session_candidates(agent_id: str) -> list[OperatorSessionCandidate]:
        assert agent_id == "operator-0"
        return [
            OperatorSessionCandidate(
                session_id="current-session",
                timestamp=20.0,
                source="metadata.codex_session_id",
            ),
            OperatorSessionCandidate(
                session_id="old-session",
                timestamp=10.0,
                source="codex.sessions",
            ),
        ]

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, message))
        return True

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_ensure_codex_model_preset_valid(*_: object) -> bool:
        return True

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.operator_session_candidates = fake_operator_session_candidates  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.ensure_codex_model_preset_valid = (  # type: ignore[method-assign]
        fake_ensure_codex_model_preset_valid
    )
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda target: target == "%30")
    monkeypatch.setattr(tmux_support, "pane_start_command", lambda target: "codex resume old")

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "name": "operator-0",
                "pbx_active": True,
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "current-session",
                    "launched_by": "agent-pbx-tui",
                },
            }
        }
        app.selected_agent_id = "operator-0"
        await app.restart_tmux_codex_session(
            "operator-0",
            model_preset=codex_model_preset_for("terra-max"),
        )

    assert respawns[0]["target"] == "%30"
    launch_argv = shlex.split(str(respawns[0]["command"]))
    assert launch_argv[:4] == ["codex", "resume", "--cd", str(Path.cwd())]
    assert launch_argv[-1] == "current-session"
    assert "-c" in launch_argv
    assert any(item.startswith("mcp_servers.agent-pbx=") for item in launch_argv)
    assert any(item.startswith("mcp_servers.workerbee=") for item in launch_argv)
    assert 'model="gpt-5.6-terra"' in launch_argv
    assert 'model_reasoning_effort="max"' in launch_argv
    assert 'model_reasoning_summary="detailed"' in launch_argv
    assert 'model_verbosity="high"' in launch_argv
    assert respawns[0]["env"]["AGENT_PBX_RESUME_CODEX_SESSION_ID"] == "current-session"
    assert respawns[0]["env"]["AGENT_PBX_REPORTING_AGENT_ID"] == "operator-0"
    assert respawns[0]["env"]["AGENT_PBX_CODEX_MODEL"] == "gpt-5.6-terra"
    assert respawns[0]["env"]["AGENT_PBX_CODEX_REASONING_EFFORT"] == "max"
    assert respawns[0]["env"]["AGENT_PBX_CODEX_REASONING_SUMMARY"] == "detailed"
    assert respawns[0]["env"]["AGENT_PBX_CODEX_VERBOSITY"] == "high"
    assert posts[-1]["path"] == "/v1/agents/register"
    metadata = posts[-1]["json"]["metadata"]  # type: ignore[index]
    assert metadata["last_resume_codex_session_id"] == "current-session"
    assert metadata["last_tmux_restart_mode"] == "in_place"
    assert metadata["codex_model_preset"] == "terra-5.6-max"
    assert metadata["codex_model_reasoning_summary"] == "detailed"
    assert metadata["codex_model_verbosity"] == "high"
    assert sent[0][0] == "%30"
    assert "agent_id: operator-0" in sent[0][1]
    assert app.tmux_agent_targets["operator-0"] == "%30"
    assert captures
    assert set(captures) == {"operator-0"}


async def test_tui_refuses_sol_restart_when_another_pane_owns_the_resume_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    notifications: list[str] = []
    respawns: list[dict[str, object]] = []
    root_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "0",
        "0",
        "%30",
        True,
        "node",
        "operator-0",
        str(Path.cwd()),
        100,
        30,
        200,
        window_name="operator-0",
    )
    stale_owner_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%31",
        True,
        "node",
        "stale-fork",
        str(Path.cwd()),
        100,
        30,
        200,
        window_name="stale-fork",
    )

    async def fake_auth_ready() -> bool:
        return True

    async def fail_configure(**_: object) -> None:
        raise AssertionError("a conflicting resume must not alter Codex configuration")

    def fake_respawn(_target: str, **kwargs: object) -> None:
        respawns.append(kwargs)

    app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
    app.ensure_operator_auth_ready = fake_auth_ready  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fail_configure  # type: ignore[method-assign]
    app.operator_session_candidates = lambda _agent_id: [  # type: ignore[method-assign]
        OperatorSessionCandidate(
            session_id="current-session",
            timestamp=1.0,
            source="metadata.codex_session_id",
        )
    ]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "launched_by": "agent-pbx-tui",
                "cwd": str(Path.cwd()),
                "codex_session_id": "current-session",
                "codex_model_preset": "terra-5.6-max",
            },
        }
    }
    app.tmux_agent_targets["operator-0"] = "%30"
    monkeypatch.setattr(tmux_support, "list_panes", lambda: [root_pane, stale_owner_pane])
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(
        tmux_support,
        "pane_start_command",
        lambda _target: "codex resume current-session",
    )
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)

    restarted = await app.relaunch_operator_root_codex(
        "operator-0",
        root_pane,
        model_preset=codex_model_preset_for("sol-xhigh"),
    )

    assert restarted is False
    assert respawns == []
    assert app.agents["operator-0"]["metadata"]["codex_model_preset"] == "terra-5.6-max"
    assert any("stale-fork" in message for message in notifications)
    assert any("will not fork automatically" in message for message in notifications)


async def test_tui_sol_restart_rolls_back_when_codex_reports_external_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    notifications: list[str] = []
    respawns: list[dict[str, object]] = []
    configured: list[dict[str, object]] = []
    root_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "0",
        "0",
        "%30",
        True,
        "node",
        "operator-0",
        str(Path.cwd()),
        100,
        30,
        200,
        window_name="operator-0",
    )

    async def fake_auth_ready() -> bool:
        return True

    async def fake_configure(**kwargs: object) -> None:
        configured.append(kwargs)

    def fake_respawn(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
    app.ensure_operator_auth_ready = fake_auth_ready  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fake_configure  # type: ignore[method-assign]
    app.operator_session_candidates = lambda _agent_id: [  # type: ignore[method-assign]
        OperatorSessionCandidate(
            session_id="current-session",
            timestamp=1.0,
            source="metadata.codex_session_id",
        )
    ]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "launched_by": "agent-pbx-tui",
                "cwd": str(Path.cwd()),
                "codex_session_id": "current-session",
                "codex_model_preset": "terra-5.6-max",
            },
        }
    }
    app.tmux_agent_targets["operator-0"] = "%30"
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_LAUNCH_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", lambda: [root_pane])
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(
        tmux_support,
        "pane_start_command",
        lambda _target: "codex resume current-session",
    )
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)
    monkeypatch.setattr(
        tmux_support,
        "capture_pane",
        lambda *_args, **_kwargs: "This conversation is open in another app.",
    )

    restarted = await app.relaunch_operator_root_codex(
        "operator-0",
        root_pane,
        model_preset=codex_model_preset_for("sol-xhigh"),
    )

    assert restarted is False
    assert len(configured) == 1
    assert len(respawns) == 2
    assert respawns[1]["command"] == "codex resume current-session"
    assert 'model="gpt-5.6-sol"' in shlex.split(str(respawns[0]["command"]))
    assert app.agents["operator-0"]["metadata"]["codex_model_preset"] == "terra-5.6-max"
    assert any("lease dialog" in message for message in notifications)


async def test_tui_fork_restart_replaces_stale_source_resume_with_codex_fork(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "fork"
    work_root.mkdir()
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    respawns: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    fork_pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%20",
        True,
        "node",
        "operator-0-fork-caller-1",
        str(work_root),
        100,
        30,
        200,
        window_name="operator-0-fork-caller-1",
    )

    async def fake_auth_ready() -> bool:
        return True

    async def fake_configure(**_: object) -> None:
        return None

    async def fake_record_operator_fork(**kwargs: object) -> dict[str, object]:
        records.append(dict(kwargs))
        return {
            "operator_fork_id": "fork-1",
            "logical_operator_agent_id": "operator-0",
            "fork_agent_id": kwargs["fork_agent_id"],
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": "source-session",
            "fork_track_id": "default",
            "fork_purpose": "edit",
            "access_mode": "edit",
            "tmux_pane_id": kwargs["tmux_pane_id"],
            "fork_codex_session_id": None,
            "metadata": kwargs["metadata"],
        }

    def fake_respawn(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    app.notify = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    app.ensure_operator_auth_ready = fake_auth_ready  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fake_configure  # type: ignore[method-assign]
    app.record_operator_fork = fake_record_operator_fork  # type: ignore[method-assign]
    app.operator_session_candidates = lambda _agent_id: [  # type: ignore[method-assign]
        OperatorSessionCandidate(
            session_id="source-session",
            timestamp=1.0,
            source="metadata.fork_codex_session_id",
        )
    ]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    app.agents = {
        "operator-0-fork-caller-1": {
            "agent_id": "operator-0-fork-caller-1",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "launched_by": "agent-pbx-tui",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "source-session",
                "fork_codex_session_id": "source-session",
                "codex_session_id": "source-session",
                "codex_thread_id": "source-session",
                "last_resume_codex_session_id": "source-session",
                "fork_track_id": "default",
                "fork_purpose": "edit",
                "access_mode": "edit",
                "source_cwd": str(work_root),
                "work_root": str(work_root),
                "cwd": str(work_root),
            },
        }
    }
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(tmux_support, "pane_start_command", lambda _target: "codex resume old")
    monkeypatch.setattr(tmux_support, "capture_pane", lambda *_args, **_kwargs: "")

    restarted = await app.relaunch_operator_fork_codex(
        "operator-0-fork-caller-1",
        fork_pane,
        model_preset=codex_model_preset_for("sol-xhigh"),
    )

    assert restarted is True
    argv = shlex.split(str(respawns[0]["command"]))
    assert argv[0] == "codex"
    assert "fork" in argv
    assert "resume" not in argv
    assert 'model="gpt-5.6-sol"' in argv
    assert records[0]["fork_codex_session_id"] is None
    assert records[0]["clear_fork_codex_session_id"] is True
    record_metadata = records[0]["metadata"]
    assert isinstance(record_metadata, dict)
    for key in (
        "fork_codex_session_id",
        "codex_session_id",
        "codex_thread_id",
        "last_resume_codex_session_id",
    ):
        assert key not in record_metadata
    assert record_metadata["source_codex_session_id"] == "source-session"
    assert record_metadata["fork_resume_disabled_reason"] == "source_session_collision"


async def test_tui_resume_leaves_external_lease_dialog_unregistered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.tmux_features_available = True
    notifications: list[str] = []
    sent: list[tuple[str, str]] = []

    async def fake_auth_ready() -> bool:
        return True

    async def fake_configure(**_: object) -> None:
        return None

    async def fake_send(pane_id: str, message: str, **_: object) -> bool:
        sent.append((pane_id, message))
        return True

    def fake_launch(**_: object) -> str:
        return "%153"

    app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
    app.ensure_operator_auth_ready = fake_auth_ready  # type: ignore[method-assign]
    app.configure_operator_codex_mcp = fake_configure  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send  # type: ignore[method-assign]
    app.operator_session_candidates = lambda _agent_id: [  # type: ignore[method-assign]
        OperatorSessionCandidate(
            session_id="old-session",
            timestamp=1.0,
            source="codex.sessions",
        )
    ]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "launched_by": "agent-pbx-tui",
                "cwd": str(Path.cwd()),
            },
        }
    }
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", lambda: [])
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda _target: True)
    monkeypatch.setattr(
        tmux_support,
        "capture_pane",
        lambda *_args, **_kwargs: "This conversation is open in another app.",
    )

    await app.resume_selected_operator(agent_id="operator-0")

    assert app.tmux_agent_targets["operator-0"] == "%153"
    assert sent == []
    assert any("left unregistered" in message for message in notifications)


async def test_tui_restart_review_fork_resumes_with_approval_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "review"
    source_cwd = tmp_path / "caller"
    work_root.mkdir()
    source_cwd.mkdir()
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response({"ok": True})

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            if path == "/v1/operator/forks/ensure":
                body = kwargs["json"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-1",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "source-session",
                        "fork_track_id": body["fork_track_id"],  # type: ignore[index]
                        "fork_purpose": body["fork_purpose"],  # type: ignore[index]
                        "access_mode": body["access_mode"],  # type: ignore[index]
                        "work_root": body["work_root"],  # type: ignore[index]
                        "tmux_pane_id": body["tmux_pane_id"],  # type: ignore[index]
                        "status": "running",
                        "summary": "Fork relaunched.",
                        "metadata": body["metadata"],  # type: ignore[index]
                    }
                )
            raise AssertionError(path)

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx-operators",
                "1",
                "0",
                "%20",
                True,
                "node",
                "operator-0-fork-caller-review-1",
                str(work_root),
                100,
                30,
                200,
                window_name="operator-0-fork-caller-review-1",
            )
        ]

    def fake_respawn_pane(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.operator_session_candidates = lambda agent_id: [  # type: ignore[assignment,method-assign]
        OperatorSessionCandidate(
            session_id="fork-session",
            timestamp=10.0,
            source="metadata.fork_codex_session_id",
        )
    ]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda target: target == "%20")
    monkeypatch.setattr(tmux_support, "pane_start_command", lambda target: "codex resume old")

    async with app.run_test():
        app.agents = {
            "operator-0-fork-caller-review-1": {
                "agent_id": "operator-0-fork-caller-review-1",
                "agent_type": "operator",
                "project": "demo",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                    "source_codex_session_id": "source-session",
                    "fork_codex_session_id": "fork-session",
                    "fork_track_id": "review-1",
                    "fork_purpose": "review",
                    "access_mode": "review_readonly",
                    "source_cwd": str(source_cwd),
                    "work_root": str(work_root),
                    "cwd": str(work_root),
                    "tmux_pane_id": "%20",
                    "launched_by": "agent-pbx-tui",
                    "review_mcp_approval_servers": ["agent-pbx", "workerbee"],
                },
            }
        }
        app.tmux_agent_targets["operator-0-fork-caller-review-1"] = "%20"
        app.tmux_manual_override_agent_ids.add("operator-0-fork-caller-review-1")
        app.tmux_direct_agent_modes["operator-0-fork-caller-review-1"] = True
        await app.restart_tmux_codex_session("operator-0-fork-caller-review-1")

    assert respawns[0]["target"] == "%20"
    argv = shlex.split(str(respawns[0]["command"]))
    assert argv[:2] == ["codex", "resume"]
    assert "--sandbox" in argv
    assert "workspace-write" in argv
    assert "--cd" in argv
    assert str(work_root) in argv
    config_overrides = [
        argv[index + 1]
        for index, item in enumerate(argv)
        if item == "-c"
    ]
    agent_pbx_override = next(
        item
        for item in config_overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    workerbee_override = next(
        item
        for item in config_overrides
        if item.startswith("mcp_servers.workerbee=")
    )
    assert 'url = "http://127.0.0.1:8765/mcp"' in agent_pbx_override
    assert 'bearer_token_env_var = "AGENT_PBX_TOKEN"' in agent_pbx_override
    assert 'default_tools_approval_mode = "approve"' in agent_pbx_override
    assert "url = " in workerbee_override
    assert 'default_tools_approval_mode = "approve"' in workerbee_override
    assert (
        f"projects={{{json.dumps(str(work_root.resolve()))} = "
        '{trust_level = "trusted"}}'
        in config_overrides
    )
    assert argv[-1] == "fork-session"
    assert respawns[0]["env"]["AGENT_PBX_OPERATOR_ROLE"] == "fork"
    assert respawns[0]["env"]["AGENT_PBX_RESUME_CODEX_SESSION_ID"] == "fork-session"
    assert posts[0]["path"] == "/v1/operator/forks/ensure"
    assert posts[0]["json"]["fork_codex_session_id"] == "fork-session"  # type: ignore[index]
    assert sent[0][0] == "%20"
    assert "agent_id: operator-0-fork-caller-review-1" in sent[0][1]
    assert captures == ["operator-0-fork-caller-review-1"]


async def test_tui_restart_review_fork_falls_back_when_resume_encrypted_content_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    work_root = tmp_path / "review"
    source_cwd = tmp_path / "caller"
    work_root.mkdir()
    source_cwd.mkdir()
    broken_session = tmp_path / "broken-session.jsonl"
    broken_session.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "message": {
                        "error": {
                            "code": "invalid_encrypted_content",
                            "message": (
                                "Encrypted content item_id did not match the target "
                                "item id."
                            ),
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    respawns: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response({"ok": True})

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            if path == "/v1/operator/forks/ensure":
                body = kwargs["json"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-1",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "source-session",
                        "fork_track_id": body["fork_track_id"],  # type: ignore[index]
                        "fork_purpose": body["fork_purpose"],  # type: ignore[index]
                        "access_mode": body["access_mode"],  # type: ignore[index]
                        "work_root": body["work_root"],  # type: ignore[index]
                        "tmux_pane_id": body["tmux_pane_id"],  # type: ignore[index]
                        "status": "running",
                        "summary": "Fork relaunched fresh.",
                        "metadata": body["metadata"],  # type: ignore[index]
                    }
                )
            raise AssertionError(path)

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [
            tmux_support.TmuxPane(
                "agent-pbx-operators",
                "1",
                "0",
                "%20",
                True,
                "node",
                "operator-0-fork-caller-review-1",
                str(work_root),
                100,
                30,
                200,
                window_name="operator-0-fork-caller-review-1",
            )
        ]

    def fake_respawn_pane(target: str, **kwargs: object) -> None:
        respawns.append({"target": target, **kwargs})

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent.append((pane_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.operator_session_candidates = lambda agent_id: [  # type: ignore[assignment,method-assign]
        OperatorSessionCandidate(
            session_id="fork-session",
            timestamp=10.0,
            source="codex.sessions",
            path=str(broken_session),
        )
    ]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "respawn_pane", fake_respawn_pane)
    monkeypatch.setattr(tmux_support, "pane_is_live", lambda target: target == "%20")
    monkeypatch.setattr(tmux_support, "pane_start_command", lambda target: "codex resume old")
    monkeypatch.setattr(tmux_support, "capture_pane", lambda *args, **kwargs: "")

    async with app.run_test():
        app.agents = {
            "operator-0-fork-caller-review-1": {
                "agent_id": "operator-0-fork-caller-review-1",
                "agent_type": "operator",
                "project": "demo",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                    "source_codex_session_id": "source-session",
                    "fork_codex_session_id": "fork-session",
                    "fork_track_id": "review-1",
                    "fork_purpose": "review",
                    "access_mode": "review_readonly",
                    "source_cwd": str(source_cwd),
                    "work_root": str(work_root),
                    "cwd": str(work_root),
                    "tmux_pane_id": "%20",
                    "launched_by": "agent-pbx-tui",
                    "review_mcp_approval_servers": ["agent-pbx", "workerbee"],
                },
            }
        }
        app.tmux_agent_targets["operator-0-fork-caller-review-1"] = "%20"
        app.tmux_manual_override_agent_ids.add("operator-0-fork-caller-review-1")
        app.tmux_direct_agent_modes["operator-0-fork-caller-review-1"] = True
        await app.restart_tmux_codex_session("operator-0-fork-caller-review-1")

    assert respawns[0]["target"] == "%20"
    argv = shlex.split(str(respawns[0]["command"]))
    assert argv[0] == "codex"
    assert "resume" not in argv
    assert "fork" not in argv
    assert "--sandbox" in argv
    assert "workspace-write" in argv
    assert str(work_root) in argv
    bootstrap_prompt = argv[-1]
    assert "agent_id: operator-0-fork-caller-review-1" in bootstrap_prompt
    assert "fresh Codex session" in bootstrap_prompt
    assert respawns[0]["env"]["AGENT_PBX_OPERATOR_FORK_LAUNCH_MODE"] == "fresh_context"
    assert "AGENT_PBX_RESUME_CODEX_SESSION_ID" not in respawns[0]["env"]
    metadata = posts[0]["json"]["metadata"]  # type: ignore[index]
    assert metadata["review_launch_mode"] == "fresh_context"
    assert metadata["source_continuation_disabled_reason"] == "invalid_encrypted_content"
    assert "fork_codex_session_id" not in metadata
    assert sent == []
    assert captures == ["operator-0-fork-caller-review-1"]


def test_tui_operator_resume_target_prefers_known_current_session() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "last_resume_codex_session_id": "current-session",
            },
        }
    }
    app.tmux_agent_targets["operator-0"] = "%153"
    candidates = [
        OperatorSessionCandidate(
            session_id="current-session",
            timestamp=20.0,
            source="codex.sessions",
        ),
        OperatorSessionCandidate(
            session_id="reset-session",
            timestamp=10.0,
            source="codex.sessions",
        ),
    ]

    target = app.operator_resume_target("operator-0", candidates)

    assert target == candidates[0]


def test_tui_operator_resume_target_skips_mismatched_report_identity(
    tmp_path: Path,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    bad_session = tmp_path / "bad-session.jsonl"
    good_session = tmp_path / "good-session.jsonl"
    bad_session.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "pbx_report_turn",
                    "arguments": json.dumps(
                        {
                            "agent_id": "caller-1",
                            "project": "demo",
                            "summary": "Wrong",
                            "detail": "Wrong identity.",
                        }
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    good_session.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "pbx_report_turn",
                    "arguments": json.dumps(
                        {
                            "agent_id": "operator-0",
                            "project": "agent-pbx-operator",
                            "summary": "Right",
                            "detail": "Right identity.",
                        }
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "last_resume_codex_session_id": "bad-session",
            },
        }
    }
    candidates = [
        OperatorSessionCandidate(
            session_id="bad-session",
            timestamp=20.0,
            source="codex.sessions",
            path=str(bad_session),
        ),
        OperatorSessionCandidate(
            session_id="good-session",
            timestamp=10.0,
            source="codex.sessions",
            path=str(good_session),
        ),
    ]

    target = app.operator_resume_target("operator-0", candidates)

    assert target == candidates[1]


def test_tui_operator_resume_target_rejects_all_mismatched_report_identities(
    tmp_path: Path,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    session_path = tmp_path / "bad-session.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "mcp_tool_call_end",
                    "invocation": {
                        "tool": "pbx_report_turn",
                        "arguments": {
                            "agent_id": "caller-1",
                            "project": "demo",
                            "summary": "Wrong",
                            "detail": "Wrong identity.",
                        },
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    target = app.operator_resume_target(
        "operator-0",
        [
            OperatorSessionCandidate(
                session_id="bad-session",
                timestamp=20.0,
                source="codex.sessions",
                path=str(session_path),
            )
        ],
    )

    assert target is None


def test_tui_operator_resume_target_skips_invalid_encrypted_content(
    tmp_path: Path,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    bad_session = tmp_path / "bad-session.jsonl"
    good_session = tmp_path / "good-session.jsonl"
    bad_session.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "message": {
                        "error": {
                            "code": "invalid_encrypted_content",
                            "message": (
                                "Encrypted content item_id did not match the target "
                                "item id."
                            ),
                        }
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    good_session.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "message": "clean",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    target = app.operator_resume_target(
        "operator-0",
        [
            OperatorSessionCandidate(
                session_id="bad-session",
                timestamp=20.0,
                source="codex.sessions",
                path=str(bad_session),
            ),
            OperatorSessionCandidate(
                session_id="good-session",
                timestamp=10.0,
                source="codex.sessions",
                path=str(good_session),
            ),
        ],
    )

    assert target is not None
    assert target.session_id == "good-session"


def test_tui_operator_session_candidates_scan_codex_session_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions" / "2026" / "06" / "22"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "rollout-2026-06-22T15-00-00-session-old.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "session-old",
                            "cwd": str(tmp_path),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "\n".join(
                                [
                                    "Use Agent PBX as an operator agent.",
                                    "Register this session with:",
                                    "- agent_id: operator-0",
                                    "- agent_type: operator",
                                ]
                            ),
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    false_positive_file = session_dir / "rollout-2026-06-22T16-00-00-session-dev.jsonl"
    false_positive_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "session-dev",
                            "cwd": str(tmp_path),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "output": (
                                '"name":"pbx_register_agent" '
                                '"agent_id":"operator-0" '
                                '"project":"agent-pbx-operator"'
                            ),
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (codex_home / "history.jsonl").write_text(
        json.dumps(
            {
                "session_id": "session-old",
                "ts": 123.0,
                "text": "continue prior operator work",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path),
            },
        }
    }

    candidates = app.operator_session_candidates("operator-0")

    assert [candidate.session_id for candidate in candidates] == ["session-old"]
    assert candidates[0].summary == "continue prior operator work"


async def test_tui_start_operator_from_caller_launches_codex_fork(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    gets: list[dict[str, object]] = []
    configured: list[tuple[str, str]] = []

    class Response:
        status_code = 200

        def __init__(self, data: object) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.data

    class Client:
        async def get(self, path: str, **kwargs: object) -> Response:
            gets.append({"path": path, **kwargs})
            if path == "/v1/auth/check":
                return Response({"ok": True})
            if path == "/v1/operator/forks":
                return Response({"forks": []})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            if path == "/v1/agents/register":
                body = kwargs["json"]  # type: ignore[index]
                return Response(
                    {
                        "agent_id": body["agent_id"],  # type: ignore[index]
                        "agent_type": "operator",
                        "project": body["project"],  # type: ignore[index]
                        "name": body.get("name"),  # type: ignore[union-attr]
                        "status": "online",
                        "effective_status": "online",
                        "pbx_active": body.get("pbx_active", True),  # type: ignore[union-attr]
                        "metadata": body["metadata"],  # type: ignore[index]
                        "created_at": 1.0,
                        "last_seen_at": 1.0,
                    }
                )
            if path == "/v1/operator/forks/ensure":
                body = kwargs["json"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-1",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "session-caller-1",
                        "campaign_id": None,
                        "cwd": str(Path.cwd()),
                        "codex_home": None,
                        "codex_host_id": "local",
                        "tmux_pane_id": "%43",
                        "status": "running",
                        "summary": "Fork launched.",
                        "metadata": body["metadata"],  # type: ignore[index]
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "last_used_at": 1.0,
                        "completed_at": None,
                        "edges": [],
                    }
                )
            raise AssertionError(path)

    async def fake_configure_operator_codex_mcp(
        *,
        codex_command: str,
        mcp_url: str,
    ) -> None:
        configured.append((codex_command, mcp_url))

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%42" if len(launches) == 1 else "%43"

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        assert pane_id == "%42"
        assert "agent_id: operator-0" in message
        return True

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        return True

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target == "%43")

    async with app.run_test():
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "status": "working",
                "last_seen_at": 1.0,
                "metadata": {
                    "cwd": str(Path.cwd()),
                    "codex_session_id": "session-caller-1",
                    "codex_host_id": "local",
                },
            }
        }
        app.selected_agent_id = "caller-1"
        await app.start_operator_agent()

    assert configured
    assert posts[0]["path"] == "/v1/agents/register"
    assert posts[0]["json"]["metadata"]["operator_role"] == "root"  # type: ignore[index]
    assert posts[0]["json"]["metadata"]["default_source_caller_agent_id"] == "caller-1"  # type: ignore[index]
    assert posts[0]["json"]["metadata"]["default_source_caller_project"] == "demo"  # type: ignore[index]
    assert posts[0]["json"]["metadata"]["default_source_codex_session_id"] == "session-caller-1"  # type: ignore[index]
    assert posts[1]["json"]["metadata"]["operator_role"] == "root"  # type: ignore[index]
    assert posts[1]["json"]["metadata"]["tmux_pane_id"] == "%42"  # type: ignore[index]
    assert posts[1]["json"]["metadata"]["default_source_caller_agent_id"] == "caller-1"  # type: ignore[index]
    assert posts[1]["json"]["metadata"]["default_source_caller_project"] == "demo"  # type: ignore[index]
    assert posts[1]["json"]["metadata"]["default_source_codex_session_id"] == "session-caller-1"  # type: ignore[index]
    assert posts[2]["path"] == "/v1/operator/forks/ensure"
    assert posts[2]["json"]["metadata"]["operator_role"] == "fork"  # type: ignore[index]
    assert launches[0]["window_name"] == "operator-0"
    root_argv = shlex.split(str(launches[0]["command"]))
    assert root_argv[0] == "codex"
    assert "--cd" in root_argv
    assert "-c" in root_argv
    assert any(item.startswith("mcp_servers.agent-pbx=") for item in root_argv)
    assert launches[0]["env"]["AGENT_PBX_OPERATOR_ROLE"] == "root"
    assert launches[1]["cwd"] == str(Path.cwd())
    fork_argv = shlex.split(str(launches[1]["command"]))
    assert fork_argv[:2] == ["codex", "fork"]
    assert "session-caller-1" in fork_argv
    assert "-c" in fork_argv
    assert any(item.startswith("mcp_servers.agent-pbx=") for item in fork_argv)
    assert launches[1]["env"]["AGENT_PBX_OPERATOR_ID"] == posts[2]["json"]["fork_agent_id"]  # type: ignore[index]
    assert launches[1]["env"]["AGENT_PBX_AGENT_ID"] == posts[2]["json"]["fork_agent_id"]  # type: ignore[index]
    assert launches[1]["env"]["AGENT_PBX_OPERATOR_ROLE"] == "fork"
    assert launches[1]["env"]["AGENT_PBX_LOGICAL_OPERATOR_ID"] == "operator-0"
    assert launches[1]["env"]["AGENT_PBX_SOURCE_CALLER_AGENT_ID"] == "caller-1"
    assert launches[1]["env"]["AGENT_PBX_SOURCE_CODEX_SESSION_ID"] == "session-caller-1"


def test_tui_operator_prompts_require_visible_pbx_forks() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    root_prompt = app.operator_bootstrap_prompt("operator-0", str(Path.cwd()))
    fork_prompt = app.operator_bootstrap_prompt(
        "operator-0-fork-caller-1",
        str(Path.cwd()),
        logical_operator_id="operator-0",
        source_caller_agent_id="caller-1",
        source_codex_session_id="session-caller-1",
    )
    monitor_prompt = app.campaign_monitor_prompt(
        "operator-0",
        {
            "campaign_id": "campaign-1",
            "title": "Campaign",
            "status": "running",
            "objective": "Do the work.",
            "criteria": ["tests pass"],
            "assignments": [
                {
                    "assignment_id": "assignment-1",
                    "target_agent_id": "caller-1",
                    "state": "waiting",
                    "operator_fork_id": "fork-1",
                }
            ],
        },
    )

    for prompt in (root_prompt, fork_prompt, monitor_prompt):
        assert "Agent PBX" in prompt
        assert "multi_agent_v1" in prompt
        assert "Native Codex children may perform bounded" in prompt
        assert "may not" in prompt
    assert "must not implement caller repo changes directly" in root_prompt
    assert "metadata.reporting_agent_id: operator-0" in root_prompt
    assert "`agent_id=operator-0`" in root_prompt
    assert "Never report under a caller agent ID" in root_prompt
    assert "metadata.reporting_agent_id: operator-0-fork-caller-1" in fork_prompt
    assert "`reporting_agent_id=operator-0-fork-caller-1`" in fork_prompt
    assert "Do not dispatch to regular caller tmux panes" in monitor_prompt


async def test_tui_start_operator_from_caller_missing_session_does_not_launch(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []

    class Client:
        async def get(self, path: str, **_: object) -> object:
            raise AssertionError(f"unexpected API request: {path}")

        async def post(self, path: str, **_: object) -> object:
            raise AssertionError(f"unexpected API request: {path}")

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        raise AssertionError("Codex MCP should not be configured")

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%42"

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)

    async with app.run_test():
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "status": "working",
                "metadata": {
                    "cwd": str(Path.cwd()),
                },
            }
        }
        app.selected_agent_id = "caller-1"
        await app.start_operator_agent()

    assert launches == []
    assert app.tmux_agent_targets == {}


async def test_tui_start_operator_refuses_missing_required_token() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token=None)
    app.tmux_features_available = True
    configured = False

    class Response:
        status_code = 401

        def raise_for_status(self) -> None:
            raise AssertionError("raise_for_status should not be called")

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            assert path == "/v1/auth/check"
            return Response()

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        nonlocal configured
        configured = True

    async def fake_refresh() -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh  # type: ignore[method-assign]
    app.refresh_events = fake_refresh  # type: ignore[method-assign]
    app.refresh_joplin_status = fake_refresh  # type: ignore[method-assign]

    async with app.run_test():
        await app.start_operator_agent()

    assert configured is False
    assert app.tmux_agent_targets == {}


async def test_tui_operator_purge_confirmed_kills_owned_pane(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, bool]] = []
    killed: list[str] = []

    async def fake_delete_agent(agent_id: str, *, delete_thread: bool = False) -> dict[str, str]:
        deleted.append((agent_id, delete_thread))
        return {"agent_id": agent_id}

    async def fake_refresh() -> None:
        return None

    def fake_kill_pane(pane_id: str) -> None:
        killed.append(pane_id)

    app.delete_agent = fake_delete_agent  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh  # type: ignore[method-assign]
    app.refresh_events = fake_refresh  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "kill_pane", fake_kill_pane)

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "launched_by": "agent-pbx-tui",
                },
                "last_seen_at": 123.0,
            }
        }
        app.tmux_agent_targets["operator-0"] = "%42"
        app.tmux_manual_override_agent_ids.add("operator-0")
        await app.dismiss_selected_agent(
            agent_id="operator-0",
            delete_thread=True,
            confirmed_operator_kill=True,
        )

    assert killed == ["%42"]
    assert deleted == [("operator-0", True)]
    assert "operator-0" not in app.tmux_agent_targets


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

        assert app.selected_agent_id == "agent-8"
        assert app.active_agent_tab == "latest-tab"
        assert tabs.active == "latest-tab"
        assert app.agent_id_at_cursor() == "agent-8"

        await pilot.press("g")
        await pilot.press("0")
        await pilot.pause()

        assert app.selected_agent_id == "agent-1"
        assert app.agent_id_at_cursor() == "agent-1"

    assert loaded == ["agent-8", "agent-1"]
    assert threads == ["agent-8", "agent-1"]


async def test_tui_operator_fork_record_agent_renders_before_agent_refresh() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    fork_agent = app.operator_fork_record_agent(
        {
            "operator_fork_id": "fork-1",
            "logical_operator_agent_id": "operator-0",
            "fork_agent_id": "operator-0-fork-caller-review-2",
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": "source-session",
            "fork_track_id": "review-2",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "cwd": "/tmp/review",
            "work_root": "/tmp/review",
            "tmux_pane_id": "%77",
            "status": "running",
            "created_at": 100.0,
            "updated_at": 101.0,
            "last_used_at": 102.0,
            "metadata": {
                "operator_role": "fork",
                "source_caller_project": "agent-pbx",
            },
        }
    )

    assert fork_agent["last_seen_at"] == 102.0
    assert fork_agent["created_at"] == 100.0
    assert fork_agent["pbx_active"] is True

    async with app.run_test() as pilot:
        await pilot.resize_terminal(120, 32)
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "online",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
                "last_seen_at": 123.0,
            },
            "operator-0-fork-caller-review-2": fork_agent,
        }
        app.render_agents()
        operator_table = app.query_one("#operators", DataTable)
        rendered = [
            cell.plain if isinstance(cell, Text) else str(cell)
            for cell in operator_table.get_row("operator-0-fork-caller-review-2")
        ]

    assert "operator-0-fork-caller-review-2" in rendered
    assert "102" in rendered


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


async def test_tui_f8_focuses_and_clears_one_alert_at_a_time() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    focused: list[str] = []

    async def fake_open_agent_latest(agent_id: str) -> bool:
        focused.append(agent_id)
        app.selected_agent_id = agent_id
        app.unseen_latest_agent_ids.discard(agent_id)
        app.render_unseen_attention()
        return True

    app.open_agent_latest = fake_open_agent_latest  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-done": {
                "agent_id": "agent-done",
                "agent_type": "caller",
                "status": "done",
                "project": "demo",
                "last_seen_at": 101.0,
            },
            "operator-failed": {
                "agent_id": "operator-failed",
                "agent_type": "operator",
                "status": "failed",
                "project": "demo-operator",
                "last_seen_at": 102.0,
                "metadata": {"operator_role": "root"},
            },
        }
        app.unseen_latest_agent_ids = {"agent-done", "operator-failed"}
        app.render_agents()
        app.render_unseen_attention()

        await pilot.press("f8")
        await pilot.pause()
        assert focused == ["operator-failed"]
        assert app.unseen_latest_agent_ids == {"agent-done"}
        assert "agent-done" in str(app.query_one("#attention", Static).renderable)

        await pilot.press("f8")
        await pilot.pause()

    assert focused == ["operator-failed", "agent-done"]
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


def test_tui_shared_latest_seen_prevents_startup_alert() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 101.0,
            "latest_report_seen_at": 101.0,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent == {"agent-1": 101.0}


def test_tui_startup_marks_unviewed_latest_report_new() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 101.0,
            "latest_report_seen_at": None,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == {"agent-1"}


def test_tui_startup_suppresses_synthetic_latest_report_alert() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 101.0,
            "latest_report_seen_at": None,
            "latest_report_status": "done",
            "latest_report_suppress_tui_alerts": True,
        }
    }

    changed = app.update_unseen_from_agent_refresh({})

    assert changed is True
    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent == {"agent-1": 101.0}
    assert app.latest_report_alert_label(app.agents["agent-1"]) == ""


def test_tui_agent_without_latest_report_does_not_mark_new() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "online",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": None,
            "latest_report_seen_at": None,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == set()


def test_tui_shared_latest_seen_does_not_clear_newer_report() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.latest_viewed_at_by_agent = {"agent-1": 100.0}
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 102.0,
            "latest_report_seen_at": 101.0,
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == {"agent-1"}


def test_tui_shared_latest_seen_refresh_clears_existing_marker() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.latest_viewed_at_by_agent = {"agent-1": 100.0}
    app.unseen_latest_agent_ids = {"agent-1"}
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 102.0,
            "latest_report_seen_at": 102.0,
        }
    }

    app.update_unseen_from_agent_refresh({"agent-1": 102.0})

    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent == {"agent-1": 102.0}


def test_tui_unseen_latest_transitions_working_done_seen() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "working",
            "project": "agent-pbx",
            "last_seen_at": 101.0,
            "latest_report_created_at": 101.0,
            "latest_report_seen_at": None,
            "latest_report_status": "working",
        }
    }

    app.update_unseen_from_agent_refresh({})

    assert app.unseen_latest_agent_ids == {"agent-1"}
    assert app.format_unseen_latest_alert("agent-1") == "NEW"

    previous = app.agent_last_seen_at.copy()
    app.agents["agent-1"] = {
        "agent_id": "agent-1",
        "status": "done",
        "project": "agent-pbx",
        "last_seen_at": 102.0,
        "latest_report_created_at": 102.0,
        "latest_report_seen_at": None,
        "latest_report_status": "done",
    }

    app.update_unseen_from_agent_refresh(previous)

    assert app.unseen_latest_agent_ids == {"agent-1"}
    assert app.format_unseen_latest_alert("agent-1") == "DONE"

    previous = app.agent_last_seen_at.copy()
    app.agents["agent-1"]["latest_report_seen_at"] = 102.0

    app.update_unseen_from_agent_refresh(previous)

    assert app.unseen_latest_agent_ids == set()
    assert app.format_unseen_latest_alert("agent-1") == ""


def test_tui_shared_latest_seen_refresh_persists_watermark(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        settings_file=settings_file,
    )
    app.latest_viewed_at_by_agent = {"agent-1": 100.0}
    app.unseen_latest_agent_ids = {"agent-1"}
    app.agents = {
        "agent-1": {
            "agent_id": "agent-1",
            "status": "done",
            "project": "agent-pbx",
            "last_seen_at": 102.0,
            "latest_report_created_at": 102.0,
            "latest_report_seen_at": 102.0,
        }
    }

    changed = app.update_unseen_from_agent_refresh({"agent-1": 102.0})

    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert changed is True
    assert app.unseen_latest_agent_ids == set()
    assert saved["latest_viewed_at_by_agent"] == {"agent-1": 102.0}


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


async def test_tui_mark_latest_seen_syncs_when_server_seen_is_missing() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    worker_work: list[object] = []

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_created_at": 123.0,
                "latest_report_seen_at": None,
            }
        }
        app.latest_viewed_at_by_agent = {"agent-1": 123.0}

        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_work.append(work)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        app.mark_latest_seen("agent-1")

    assert len(worker_work) == 1
    assert inspect.iscoroutinefunction(worker_work[0])


async def test_tui_latest_seen_event_clears_remote_unseen_marker() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 102.0,
                "latest_report_created_at": 102.0,
                "latest_report_seen_at": None,
            }
        }
        app.unseen_latest_agent_ids = {"agent-1"}
        app.render_agents()
        app.render_unseen_attention()

        app.handle_event(
            {
                "event_id": 10,
                "type": "latest_seen",
                "subject_id": "agent-1",
                "payload": {
                    "agent_id": "agent-1",
                    "latest_report_seen_at": 102.0,
                },
            }
        )
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent["agent-1"] == 102.0
    assert app.agents["agent-1"]["latest_report_seen_at"] == 102.0
    assert row[1] == ""


async def test_tui_root_seen_response_clears_linked_edit_fork_alerts() -> None:
    requested: list[str] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="secret")

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "root_operator_id": "operator-0",
                "cutoff": 102.0,
                "acknowledged": [
                    {
                        "agent_id": "operator-0",
                        "latest_report_seen_at": 102.0,
                        "reason": "root",
                        "changed": True,
                    },
                    {
                        "agent_id": "operator-0-fork-caller-1",
                        "latest_report_seen_at": 101.0,
                        "reason": "linked_edit_fork",
                        "changed": True,
                    },
                ],
                "preserved": [],
            }

    class Client:
        async def post(self, path: str, **_: object) -> Response:
            requested.append(path)
            return Response()

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "working",
                "metadata": {"operator_role": "root"},
                "latest_report_created_at": 102.0,
                "last_seen_at": 102.0,
            },
            "operator-0-fork-caller-1": {
                "agent_id": "operator-0-fork-caller-1",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "working",
                "metadata": {
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "fork_purpose": "edit",
                },
                "latest_report_created_at": 101.0,
                "last_seen_at": 101.0,
            },
        }
        app.unseen_latest_agent_ids = {
            "operator-0",
            "operator-0-fork-caller-1",
        }

        await app.mark_latest_seen_remote("operator-0")

    assert requested == ["/v2/operators/operator-0/alerts/acknowledge"]
    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent == {
        "operator-0": 102.0,
        "operator-0-fork-caller-1": 101.0,
    }


async def test_tui_agent_starred_event_updates_visible_row() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async def fake_refresh_agents() -> None:
        return None

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 100.0,
                "starred": False,
                "starred_at": None,
            }
        }
        app.render_agents()
        app.handle_event(
            {
                "event_id": 10,
                "type": "agent_starred_changed",
                "subject_id": "agent-1",
                "payload": {
                    "agent_id": "agent-1",
                    "starred": True,
                    "starred_at": 123.0,
                },
            }
        )
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.starred_agent_ids == {"agent-1"}
    assert app.agents["agent-1"]["starred"] is True
    assert app.agents["agent-1"]["starred_at"] == 123.0
    assert row[0] == "*"


async def test_tui_replayed_report_event_respects_shared_seen_marker() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "effective_status": "done",
                "project": "agent-pbx",
                "last_seen_at": 200.0,
                "latest_report_created_at": 200.0,
                "latest_report_seen_at": 200.0,
            }
        }
        app.render_agents()

        event = {
            "event_id": 11,
            "type": "report_created",
            "subject_id": "report-1",
            "created_at": 200.0,
            "payload": {
                "agent_id": "agent-1",
                "status": "done",
                "summary": "Already seen",
                "needs_input": False,
                "created_at": 200.0,
            },
        }
        app.apply_report_created_event("agent-1", event)
        app.mark_latest_unseen("agent-1")
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.unseen_latest_agent_ids == set()
    assert app.latest_viewed_at_by_agent == {"agent-1": 200.0}
    assert row[1] == ""


async def test_tui_suppressed_report_event_updates_state_without_new_marker() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "running",
                "effective_status": "running",
                "project": "agent-pbx",
                "last_seen_at": 100.0,
            }
        }
        app.render_agents()
        event = {
            "event_id": 12,
            "type": "report_created",
            "subject_id": "report-1",
            "created_at": 201.0,
            "payload": {
                "agent_id": "agent-1",
                "status": "done",
                "summary": "Synthetic UAT report",
                "needs_input": False,
                "created_at": 201.0,
                "suppress_tui_alerts": True,
            },
        }
        app.apply_report_created_event("agent-1", event)
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")

    assert app.agents["agent-1"]["status"] == "done"
    assert app.agents["agent-1"]["latest_report_suppress_tui_alerts"] is True
    assert app.unseen_latest_agent_ids == set()
    assert row[1] == ""


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
        screen_flash_active = "attention-flash" in app.screen.classes

    assert steady_text == "NEW latest: agent-1"
    assert blink_text == "NEW! latest: agent-1"
    assert attention.has_class("unseen-active")
    assert screen_flash_active is False


async def test_tui_unseen_latest_uses_latest_report_status_labels() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-blocked": {
                "agent_id": "agent-blocked",
                "status": "blocked",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_status": "blocked",
            },
            "agent-canceled": {
                "agent_id": "agent-canceled",
                "status": "canceled",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
                "latest_report_status": "canceled",
            },
            "agent-done": {
                "agent_id": "agent-done",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 125.0,
                "latest_report_status": "done",
            },
            "agent-failed": {
                "agent_id": "agent-failed",
                "status": "failed",
                "project": "agent-pbx",
                "last_seen_at": 126.0,
                "latest_report_status": "failed",
            },
            "agent-working": {
                "agent_id": "agent-working",
                "status": "working",
                "project": "agent-pbx",
                "last_seen_at": 127.0,
                "latest_report_status": "working",
            },
        }
        app.unseen_latest_agent_ids = set(app.agents)
        app.render_agents()
        table = app.query_one("#agents", DataTable)
        rows = {
            agent_id: table.get_row(agent_id)[1]
            for agent_id in app.agents
        }

    assert rows == {
        "agent-blocked": "BLOCK",
        "agent-canceled": "CANC",
        "agent-done": "DONE",
        "agent-failed": "FAIL",
        "agent-working": "NEW",
    }


async def test_tui_unseen_latest_attention_groups_by_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.agents = {
            "agent-done": {
                "agent_id": "agent-done",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_status": "done",
            },
            "agent-failed": {
                "agent_id": "agent-failed",
                "status": "failed",
                "project": "agent-pbx",
                "last_seen_at": 124.0,
                "latest_report_status": "failed",
            },
            "agent-working": {
                "agent_id": "agent-working",
                "status": "working",
                "project": "agent-pbx",
                "last_seen_at": 125.0,
                "latest_report_status": "working",
            },
        }
        app.unseen_latest_agent_ids = set(app.agents)
        app.attention_blink_phase = False
        app.render_unseen_attention()
        attention = app.query_one("#attention")
        steady_text = str(attention.renderable)
        target_agent_id = app.attention_agent_id
        app.attention_blink_phase = True
        app.render_unseen_attention()
        blink_text = str(attention.renderable)

    assert steady_text == (
        "FAIL latest: agent-failed | DONE latest: agent-done | "
        "NEW latest: agent-working"
    )
    assert blink_text == (
        "FAIL! latest: agent-failed | DONE! latest: agent-done | "
        "NEW! latest: agent-working"
    )
    assert target_agent_id == "agent-failed"


async def test_tui_unseen_latest_attention_stays_visible_when_blink_disabled() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", agent_blink=False)

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_status": "done",
            }
        }
        app.unseen_latest_agent_ids.add("agent-1")
        app.attention_blink_phase = True
        app.render_unseen_attention()
        attention = app.query_one("#attention")
        has_flash_class = "attention-flash" in app.screen.classes

    assert str(attention.renderable) == "DONE latest: agent-1"
    assert attention.has_class("unseen-active")
    assert has_flash_class is False


async def test_tui_unseen_latest_does_not_clear_active_visual_flash() -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        visual_flash=True,
        agent_blink=False,
    )

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
                "latest_report_status": "done",
            }
        }
        app.unseen_latest_agent_ids.add("agent-1")
        app.flash_for_event(
            {
                "type": "report_created",
                "subject_id": "report-1",
                "payload": {"agent_id": "agent-1"},
            }
        )
        attention = app.query_one("#attention")
        app.render_unseen_attention()
        flash_text = str(attention.renderable)
        flash_active = attention.has_class("attention-active")
        flash_class_active = "attention-flash" in app.screen.classes
        app.clear_flash(app.flash_generation)
        restored_text = str(attention.renderable)
        restored_active = attention.has_class("attention-active")
        restored_flash_class_active = "attention-flash" in app.screen.classes

    assert flash_text == "New report created: report-1"
    assert flash_active is True
    assert flash_class_active is True
    assert restored_text == "DONE latest: agent-1"
    assert restored_active is False
    assert restored_flash_class_active is False


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
    assert cursor_row == 0
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
    assert cursor_row == 0
    assert cursor_agent_id == "agent-2"
    assert focused is agents
    assert loaded == ["agent-2"]
    assert threads == ["agent-2"]
    assert click._stop_propagation is True


async def test_tui_operator_campaign_flash_targets_fork_agent() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", visual_flash=True)
    loaded: list[str] = []
    threads: list[str] = []
    fork_agent_id = "operator-0-fork-caller-1"

    async def fake_load_latest_report(agent_id: str) -> None:
        loaded.append(agent_id)

    async def fake_load_thread(agent_id: str) -> None:
        threads.append(agent_id)

    app.load_latest_report = fake_load_latest_report  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "last_seen_at": 123.0,
                "metadata": {"operator_role": "root"},
            },
            fork_agent_id: {
                "agent_id": fork_agent_id,
                "agent_type": "operator",
                "status": "working",
                "project": "agent-pbx-operator",
                "last_seen_at": 124.0,
                "metadata": {
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                },
            },
        }
        app.render_agents()
        tabs = app.query_one("#agent-tabs")
        tabs.active = "thread-tab"
        app.active_agent_tab = "thread-tab"
        app.selected_agent_id = "operator-0"
        app.query_one("#agent-id", Input).value = "operator-0"
        app.flash_for_event(
            {
                "type": "operator_campaign_event",
                "subject_id": "campaign-1",
                "payload": {
                    "operator_agent_id": "operator-0",
                    "fork_agent_id": fork_agent_id,
                    "event_type": "assignment_reported",
                },
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

    assert app.attention_agent_id == fork_agent_id
    assert app.selected_agent_id == fork_agent_id
    assert app.active_agent_tab == "latest-tab"
    assert tabs.active == "latest-tab"
    assert loaded == [fork_agent_id]
    assert threads == [fork_agent_id]
    assert click._stop_propagation is True


async def test_tui_selected_report_event_keeps_thread_unseen() -> None:
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
        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(work() if callable(work) else work)
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
                    "status": "done",
                    "summary": "Done",
                },
            }
        )
        for coro in worker_coros:
            await coro

    assert app.active_agent_tab == "thread-tab"
    assert tabs.active == "thread-tab"
    assert app.unseen_latest_agent_ids == {"agent-1"}
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
        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(work() if callable(work) else work)
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


async def test_tui_operator_campaign_event_refreshes_selected_campaigns() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    refreshed_agents = 0
    refreshed_campaigns: list[str] = []
    worker_coros = []

    async def fake_refresh_agents() -> None:
        nonlocal refreshed_agents
        refreshed_agents += 1

    async def fake_load_operator_campaigns(agent_id: str) -> None:
        refreshed_campaigns.append(agent_id)

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.load_operator_campaigns = fake_load_operator_campaigns  # type: ignore[method-assign]

    async with app.run_test():
        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(work() if callable(work) else work)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        refreshed_agents = 0
        worker_coros.clear()
        app.selected_agent_id = "operator-0"
        app.active_agent_tab = "campaigns-tab"
        app.handle_event(
            {
                "event_id": 1,
                "type": "operator_campaign_event",
                "subject_id": "campaign-1",
                "payload": {
                    "operator_agent_id": "operator-0",
                    "fork_agent_id": "operator-0-fork-caller-1",
                    "campaign_id": "campaign-1",
                    "event_type": "assignment_reported",
                },
            }
        )
        for coro in worker_coros:
            await coro

    assert refreshed_agents == 1
    assert refreshed_campaigns == ["operator-0"]


async def test_tui_operator_kb_tab_loads_entries() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async def fake_fetch_operator_kb_entries(
        logical_operator_id: str,
        *,
        status: str | None = "active",
        query: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        assert logical_operator_id == "operator-0"
        assert status == "proposed"
        assert query is None
        assert limit == 100
        return [
            {
                "kb_id": "kb-1",
                "status": "proposed",
                "redaction_status": "clean",
                "scope": "project",
                "project": "agent-pbx",
                "title": "Review handoff model",
                "summary": "Operators can promote reviewed handoff context.",
                "body": "Use the KB tab to inspect proposed durable knowledge.",
                "tags": ["handoff", "kb"],
                "created_at": 100.0,
                "updated_at": 120.0,
                "sources": [],
                "metadata": {},
            }
        ]

    app.fetch_operator_kb_entries = fake_fetch_operator_kb_entries  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.activate_operator_kb_tab()

        await app.load_operator_kb("operator-0", status="proposed")

        table = app.query_one("#operator-kb", DataTable)
        detail = app.query_one("#operator-kb-detail", TextArea)
        status = app.query_one("#operator-kb-status", Static)

    assert app.active_agent_tab == "operator-kb-tab"
    assert table.row_count == 1
    assert "KB ID: kb-1" in detail.text
    assert "Use the KB tab to inspect proposed durable knowledge." in detail.text
    assert "1 proposed entry for operator-0" in str(status.renderable)


async def test_tui_operator_kb_search_history_and_feedback() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    fetched: list[dict[str, object]] = []
    feedback_calls: list[dict[str, object]] = []

    async def fake_fetch_operator_kb_entries(
        logical_operator_id: str,
        *,
        status: str | None = "active",
        query: str | None = None,
        semantic: bool = False,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        fetched.append(
            {
                "logical_operator_id": logical_operator_id,
                "status": status,
                "query": query,
                "semantic": semantic,
                "limit": limit,
            }
        )
        return [
            {
                "kb_id": "kb-1",
                "status": "active",
                "redaction_status": "clean",
                "scope": "project",
                "project": "agent-pbx",
                "title": "Review handoff model",
                "summary": "Operators can promote reviewed handoff context.",
                "body": "Use retrieval feedback for tuning.",
                "tags": ["handoff", "kb"],
                "created_at": 100.0,
                "updated_at": 120.0,
                "sources": [],
                "metadata": {
                    "retrieval": {
                        "query_id": "query-1",
                        "match_id": "match-1",
                        "mode": "semantic",
                        "sources": ["semantic"],
                        "score": 0.77,
                        "semantic_score": 0.31,
                        "semantic_chunks": [
                            {"chunk_index": 0, "score": 0.31, "token_count": 20}
                        ],
                    }
                },
            }
        ]

    async def fake_fetch_operator_kb_queries(
        logical_operator_id: str,
        *,
        misses: bool = False,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        assert logical_operator_id == "operator-0"
        assert misses is False
        assert limit == 100
        return [
            {
                "query_id": "query-1",
                "logical_operator_agent_id": "operator-0",
                "operator_agent_id": "operator-0",
                "query": "handoff routing",
                "project": "agent-pbx",
                "retrieval_mode": "hybrid",
                "retrieval_provider": "sqlite",
                "retrieval_model": "hashed-sparse-v1",
                "index_version": "sqlite-hybrid-v1",
                "source": "search",
                "match_count": 1,
                "semantic_match_count": 1,
                "created_at": 130.0,
                "feedback_summary": {"accepted": 1},
                "matches": [
                    {
                        "match_id": "match-1",
                        "query_id": "query-1",
                        "kb_id": "kb-1",
                        "rank": 1,
                        "retrieval_mode": "semantic",
                        "score": 0.77,
                        "semantic_score": 0.31,
                        "semantic_chunks": [
                            {"chunk_index": 0, "score": 0.31, "token_count": 20}
                        ],
                    }
                ],
            }
        ]

    async def fake_submit_operator_kb_feedback(
        logical_operator_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        feedback_calls.append({"logical_operator_id": logical_operator_id, **kwargs})
        return {"feedback_id": "feedback-1", "feedback": kwargs["feedback"]}

    app.fetch_operator_kb_entries = fake_fetch_operator_kb_entries  # type: ignore[method-assign]
    app.fetch_operator_kb_queries = fake_fetch_operator_kb_queries  # type: ignore[method-assign]
    app.submit_operator_kb_feedback = fake_submit_operator_kb_feedback  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.activate_operator_kb_tab()
        query_input = app.query_one("#operator-kb-query", Input)
        query_input.value = "handoff routing"

        await app.search_operator_kb()
        await app.feedback_selected_operator_kb("accepted")
        await app.load_operator_kb_queries("operator-0")

        table = app.query_one("#operator-kb", DataTable)
        detail = app.query_one("#operator-kb-detail", TextArea)

    assert {
        "logical_operator_id": "operator-0",
        "status": "active",
        "query": "handoff routing",
        "semantic": True,
        "limit": 50,
    } in fetched
    assert {
        "logical_operator_id": "operator-0",
        "status": "active",
        "query": None,
        "semantic": False,
        "limit": 100,
    } in fetched
    assert feedback_calls[0]["query_id"] == "query-1"
    assert feedback_calls[0]["match_id"] == "match-1"
    assert feedback_calls[0]["kb_id"] == "kb-1"
    assert feedback_calls[0]["feedback"] == "accepted"
    assert table.row_count == 1
    assert "Operator KB query: operator-0" in detail.text
    assert "Feedback: {\"accepted\": 1}" in detail.text


async def test_tui_operator_kb_promote_uses_selected_proposal() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    promoted: list[tuple[str, str]] = []
    loaded: list[tuple[str, str | None]] = []
    refreshed_events = 0

    async def fake_promote_operator_kb_entry(
        logical_operator_id: str,
        kb_id: str,
    ) -> dict[str, object]:
        promoted.append((logical_operator_id, kb_id))
        return {"kb_id": kb_id, "status": "active"}

    async def fake_load_operator_kb(
        agent_id: str,
        *,
        status: str | None = None,
    ) -> None:
        loaded.append((agent_id, status))

    async def fake_refresh_events() -> None:
        nonlocal refreshed_events
        refreshed_events += 1

    app.promote_operator_kb_entry = fake_promote_operator_kb_entry  # type: ignore[method-assign]
    app.load_operator_kb = fake_load_operator_kb  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.active_agent_tab = "operator-kb-tab"
        app.operator_kb_entries_by_operator["operator-0"] = {
            "kb-2": {
                "kb_id": "kb-2",
                "status": "proposed",
                "redaction_status": "clean",
            }
        }
        app.selected_operator_kb_id_by_operator["operator-0"] = "kb-2"
        refreshed_events = 0

        await app.promote_oldest_operator_kb_proposal()

    assert promoted == [("operator-0", "kb-2")]
    assert loaded == [("operator-0", "active")]
    assert refreshed_events == 1


async def test_tui_operator_kb_promote_requires_selected_proposal() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    promoted: list[tuple[str, str]] = []
    loaded: list[tuple[str, str | None]] = []

    async def fake_promote_operator_kb_entry(
        logical_operator_id: str,
        kb_id: str,
    ) -> dict[str, object]:
        promoted.append((logical_operator_id, kb_id))
        return {"kb_id": kb_id, "status": "active"}

    async def fake_load_operator_kb(
        agent_id: str,
        *,
        status: str | None = None,
    ) -> None:
        loaded.append((agent_id, status))

    app.promote_operator_kb_entry = fake_promote_operator_kb_entry  # type: ignore[method-assign]
    app.load_operator_kb = fake_load_operator_kb  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {"operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        app.active_agent_tab = "operator-kb-tab"
        app.operator_kb_entries_by_operator["operator-0"] = {
            "kb-2": {
                "kb_id": "kb-2",
                "status": "active",
                "redaction_status": "clean",
            }
        }
        app.selected_operator_kb_id_by_operator["operator-0"] = "kb-2"

        await app.promote_oldest_operator_kb_proposal()

    assert promoted == []
    assert loaded == []


async def test_tui_operator_kb_seed_uses_selected_fork_context(tmp_path: Path) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    started: list[tuple[str, dict[str, object]]] = []
    loaded: list[tuple[str, str | None]] = []
    refreshed_events = 0

    async def fake_start_operator_kb_seed_run(
        operator_agent_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        started.append((operator_agent_id, kwargs))
        return {
            "seed_run": {
                "seed_run_id": "seed-1",
                "logical_operator_agent_id": "operator-0",
                "source_operator_agent_id": operator_agent_id,
                "seed_type": "operator_self_seed",
                "scope": kwargs["scope"],
                "project": kwargs["project"],
                "repo_root": kwargs["repo_root"],
                "git_remote": None,
                "branch": None,
                "status": "queued",
                "delivery_status": "queued",
                "command_id": "cmd-1",
                "tmux_pane_id": None,
                "metadata": {"seed_sync_key": "sync-1"},
            },
            "command": {
                "command_id": "cmd-1",
                "status": "queued",
                "agent_id": operator_agent_id,
            },
        }

    async def fake_open_operator_kb_for_agent(
        agent_id: str,
        *,
        status: str = "active",
    ) -> None:
        loaded.append((agent_id, status))

    async def fake_refresh_events() -> None:
        nonlocal refreshed_events
        refreshed_events += 1

    app.start_operator_kb_seed_run = fake_start_operator_kb_seed_run  # type: ignore[method-assign]
    app.open_operator_kb_for_agent = fake_open_operator_kb_for_agent  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0-fork-review": {
                "agent_id": "operator-0-fork-review",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                    "source_cwd": str(source_cwd),
                },
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {"cwd": str(source_cwd)},
            },
        }
        app.selected_agent_id = "operator-0-fork-review"
        refreshed_events = 0

        await app.seed_selected_operator_kb()

    assert started == [
        (
            "operator-0-fork-review",
            {
                "scope": "repo",
                "project": "demo",
                "repo_root": str(source_cwd),
                "git_remote": None,
                "branch": None,
                "metadata": {
                    "triggered_by": "agent-pbx-tui",
                    "selected_operator_agent_id": "operator-0-fork-review",
                    "selected_logical_operator_id": "operator-0",
                    "source_caller_agent_id": "caller-1",
                },
            },
        )
    ]
    assert loaded == [("operator-0-fork-review", "proposed")]
    assert refreshed_events == 1


async def test_tui_operator_kb_event_refreshes_selected_tab() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    refreshed_kb: list[str] = []
    worker_coros = []

    async def fake_load_operator_kb(agent_id: str, *, status: str | None = None) -> None:
        assert status is None
        refreshed_kb.append(agent_id)

    app.load_operator_kb = fake_load_operator_kb  # type: ignore[method-assign]

    async with app.run_test():
        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(work() if callable(work) else work)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        app.agents = {
            "operator-0-fork-review": {
                "agent_id": "operator-0-fork-review",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "running",
                "last_seen_at": 123.0,
                "metadata": {
                    "operator_role": "fork",
                    "logical_operator_id": "operator-0",
                },
            }
        }
        app.selected_agent_id = "operator-0-fork-review"
        app.active_agent_tab = "operator-kb-tab"
        app.handle_event(
            {
                "event_id": 1,
                "type": "operator_kb_proposed",
                "subject_id": "kb-1",
                "payload": {
                    "kb_id": "kb-1",
                    "operator_agent_id": "operator-0-fork-review",
                    "created_by_operator_agent_id": "operator-0",
                    "status": "proposed",
                },
            }
        )
        for coro in worker_coros:
            await coro

    assert refreshed_kb == ["operator-0-fork-review"]


async def test_tui_report_event_patches_visible_agent_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    worker_coros = []

    async def fake_refresh_agents() -> None:
        return None

    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]

    async with app.run_test():
        def fake_run_worker(work, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            worker_coros.append(work() if callable(work) else work)
            return None

        app.run_worker = fake_run_worker  # type: ignore[method-assign]
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "done",
                "effective_status": "done",
                "project": "agent-pbx",
                "last_seen_at": 100.0,
                "latest_report_created_at": 100.0,
            }
        }
        app.render_agents()

        app.handle_event(
            {
                "event_id": 1,
                "type": "report_created",
                "subject_id": "report-1",
                "created_at": 125.0,
                "payload": {
                    "agent_id": "agent-1",
                    "status": "working",
                    "summary": "Working",
                    "needs_input": False,
                    "created_at": 125.0,
                },
            }
        )
        table = app.query_one("#agents", DataTable)
        row = table.get_row("agent-1")
        for coro in worker_coros:
            await coro

    assert app.agents["agent-1"]["status"] == "working"
    assert app.agents["agent-1"]["effective_status"] == "working"
    assert app.agents["agent-1"]["last_seen_at"] == 125.0
    assert app.agents["agent-1"]["latest_report_created_at"] == 125.0
    assert "working" in [str(value) for value in row]


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
    assert row[1] == "DONE"


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
    assert row[1] == ""


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
        original_cursor_agent_id = app.agent_id_at_cursor()
        app.agents["agent-1"]["status"] = "completed"
        app.render_agents()
        cursor_agent_id = app.agent_id_at_cursor()

    assert table.cursor_row == 1
    assert original_cursor_agent_id == "agent-1"
    assert cursor_agent_id == original_cursor_agent_id


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


async def test_tui_escape_queues_send_key_command() -> None:
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
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.send_escape_key()
        detail = app.query_one("#detail", TextArea).text

    assert queued == [
        (
            "agent-1",
            "send_key",
            {
                "key": "escape",
                "request": (
                    "Send an Escape key event to the agent session if supported; "
                    "otherwise report that key injection is unavailable."
                ),
            },
        )
    ]
    assert threads == ["agent-1"]
    assert "Escape key request queued for agent-1." in detail
    assert "Command: cmd-1" in detail


async def test_tui_escape_sends_tmux_escape_key() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux(agent_id: str, key: str) -> bool:
        sent.append((agent_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux = fake_send_key_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.send_escape_key()

    assert sent == [("agent-1", "Escape")]
    assert captures == ["agent-1"]


async def test_tui_ctrl_c_sends_tmux_interrupt_key() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux(agent_id: str, key: str) -> bool:
        sent.append((agent_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux = fake_send_key_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.send_ctrl_c_key()

    assert sent == [("agent-1", "C-c")]
    assert captures == ["agent-1"]


async def test_tui_ctrl_c_requires_tmux_direct() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    sent: list[tuple[str, str]] = []
    notifications: list[tuple[str, str | None]] = []

    async def fake_send_key_to_tmux(agent_id: str, key: str) -> bool:
        sent.append((agent_id, key))
        return True

    app.send_key_to_tmux = fake_send_key_to_tmux  # type: ignore[method-assign]
    app.notify = lambda message, **kwargs: notifications.append(  # type: ignore[method-assign]
        (str(message), kwargs.get("severity"))
    )

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        await app.send_ctrl_c_key()

    assert sent == []
    assert (
        "Enable tmux direct mode for agent-1 before sending Ctrl+C.",
        "warning",
    ) in notifications


async def test_tui_hard_stop_verification_warns_when_pane_keeps_changing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    captures = iter(["working", "still working"])
    loaded: list[str] = []
    notifications: list[tuple[str, str | None]] = []

    async def fake_capture_tmux_display_for_agent(agent_id: str) -> str | None:
        assert agent_id == "agent-1"
        return next(captures)

    async def fake_load_tmux_capture(agent_id: str) -> None:
        loaded.append(agent_id)

    monkeypatch.setattr("agent_pbx.tui.TMUX_HARD_STOP_VERIFY_DELAY_SECONDS", 0)
    app.capture_tmux_display_for_agent = fake_capture_tmux_display_for_agent  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]
    app.notify = lambda message, **kwargs: notifications.append(  # type: ignore[method-assign]
        (str(message), kwargs.get("severity"))
    )

    await app.verify_tmux_hard_stop("agent-1", "Ctrl+C")

    assert loaded == ["agent-1"]
    assert (
        "Sent Ctrl+C; tmux pane was still changing after verification.",
        "warning",
    ) in notifications


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


async def test_tui_mark_agent_working_creates_audited_report() -> None:
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
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "status": "blocked",
                "effective_status": "blocked",
                "latest_report_id": "blocked-report",
                "metadata": {"codex_session_id": "session-1"},
            },
            "operator-0-fork-caller-1": {
                "agent_id": "operator-0-fork-caller-1",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "status": "ready",
                "pbx_active": True,
                "metadata": {
                    "operator_role": "fork",
                    "source_caller_agent_id": "caller-1",
                    "source_codex_session_id": "session-1",
                    "operator_fork_pending": False,
                    "tmux_pane_id": "%42",
                },
            },
        }
        app.query_one("#agent-id", Input).value = "caller-1"
        refreshed_agents = 0
        refreshed_events = 0
        await app.mark_agent_working()
        detail = app.query_one("#detail", TextArea).text

    assert len(reports) == 1
    agent_id, payload = reports[0]
    assert agent_id == "caller-1"
    assert payload["project"] == "demo"
    assert payload["status"] == "working"
    assert payload["summary"] == "Agent marked working by operator"
    assert payload["needs_input"] is False
    assert payload["plan_options"] == []
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["manual_unblock"] is True
    assert metadata["resolved_report_id"] == "blocked-report"
    assert metadata["active_fork_agent_id"] == "operator-0-fork-caller-1"
    assert metadata["active_source_codex_session_id"] == "session-1"
    assert metadata["active_fork_tmux_pane_id"] == "%42"
    assert "Previous status: blocked" in str(payload["detail"])
    assert "Active fork agent: operator-0-fork-caller-1" in str(payload["detail"])
    assert refreshed_agents == 1
    assert refreshed_events == 1
    assert threads == ["caller-1"]
    assert "Marked caller-1 working." in detail
    assert "Report: report-1" in detail


async def test_tui_plan_selection_command_queues_thread_choice_with_notes() -> None:
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
        app.query_one("#agent-id", Input).value = "agent-1"
        app.thread_items = {"report:r1": item}
        app.select_thread_item("report:r1")
        app.active_agent_tab = "thread-tab"
        app.query_one("#message", TextArea).text = "/plan:2 Prefer the safer path."
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": (
                    "Selected plan option: B\n\n"
                    "Operator notes:\nPrefer the safer path."
                ),
                "plan_choice": {"label": "B", "option": "B"},
            },
        )
    ]
    assert threads == ["agent-1"]
    assert message_text == ""


async def test_tui_plan_selection_does_not_use_other_agent_thread_choice() -> None:
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

    async with app.run_test():
        stale_item = {
            "item_id": "report:agent-2",
            "kind": "report",
            "agent_id": "agent-2",
            "created_at": 123.0,
            "status": "blocked",
            "title": "Other agent choice",
            "body": "Do not use this for agent-1",
            "metadata": {"plan_options": ["Other agent option"]},
        }
        app.selected_agent_id = "agent-1"
        app.active_agent_tab = "thread-tab"
        app.thread_items = {"report:agent-2": stale_item}
        app.selected_thread_item_id = "report:agent-2"
        app.latest_report_by_agent = {
            "agent-1": {
                "report_id": "report-agent-1",
                "agent_id": "agent-1",
                "plan_options": ["Agent one option"],
            }
        }
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:1"
        await app.send_input()

    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": "Selected plan option: Agent one option",
                "plan_choice": {
                    "label": "Agent one option",
                    "option": "Agent one option",
                },
            },
        )
    ]


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
        app.query_one("#agent-id", Input).value = "agent-1"
        app.render_latest_plan_choice_panel(report)
        app.query_one("#message", TextArea).text = "/plan:2 Prefer B."
        table = app.query_one("#latest-plan-options", DataTable)
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert table.row_count == 2
    assert queued == [
        (
            "agent-1",
            "send_input",
            {
                "message": "Selected plan option: B\n\nOperator notes:\nPrefer B.",
                "plan_choice": {"label": "B", "option": "B"},
            },
        )
    ]
    assert threads == ["agent-1"]
    assert message_text == ""


async def test_tui_plan_selection_command_to_tmux_uses_direct_pane() -> None:
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
        app.active_agent_tab = "thread-tab"
        app.query_one("#tmux-message", TextArea).text = "/plan:1 Go now."
        await app.send_tmux_input()

    assert sent == [
        (
            "agent-1",
            "Selected plan option: Proceed\n\nOperator notes:\nGo now.",
        )
    ]
    assert captures == ["agent-1"]


async def test_tui_plan_selection_to_tmux_presses_native_selector_key() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_text: list[tuple[str, str]] = []
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent_text.append((agent_id, message))
        return True

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_plan_selector_agent_ids.add("agent-1")
        app.tmux_plan_selector_pane_by_agent["agent-1"] = "%9"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"
        )
        app.query_one("#tmux-message", TextArea).text = "/plan:2"
        await app.send_tmux_input()

    assert sent_keys == [("%9", "2")]
    assert sent_text == []
    assert captures == ["agent-1"]
    assert "agent-1" not in app.tmux_plan_selector_agent_ids


async def test_tui_plan_selection_from_latest_input_presses_pending_tmux_selector() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"
        )
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:1"
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert sent_keys == [("%9", "1")]
    assert queued == []
    assert captures == ["agent-1"]
    assert message_text == ""


async def test_tui_tmux_direct_can_send_plan_selection_from_latest_input() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    message_text = ""
    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"
        )
        app.query_one("#tmux-message", TextArea).text = ""
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:2"
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert sent_keys == [("%9", "2")]
    assert captures == ["agent-1"]
    assert message_text == ""


async def test_tui_tmux_direct_can_send_pre_plan_question_selection() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "› 1. Keep the current API shape\n"
            "  2. Add a compatibility shim\n"
            "  3. Split the migration into phases\n"
            "  4. Stop and inspect the affected tests first"
        )
        app.query_one("#tmux-message", TextArea).text = ""
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:4"
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert sent_keys == [("%9", "4")]
    assert captures == ["agent-1"]
    assert message_text == ""


async def test_tui_tmux_direct_allows_unparsed_native_plan_option() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"
        )
        sent = await app.send_plan_selection(
            "agent-1",
            PlanSelection(index=4),
            via_tmux=True,
        )

    assert sent is True
    assert sent_keys == [("%9", "4")]
    assert captures == ["agent-1"]


async def test_tui_tmux_plan_selection_notes_send_follow_up_comment() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    sent_notes: list[tuple[str, str]] = []
    captures: list[str] = []

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_send_text_to_tmux_pane(
        pane_id: str,
        message: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_notes.append((pane_id, message))
        return True

    async def fake_record_tmux_joplin_interaction(agent_id: str, message: str) -> None:
        return None

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fake_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.record_tmux_joplin_interaction = fake_record_tmux_joplin_interaction  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"
        )
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:3 keep this staged"
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert sent_keys == [("%9", "3")]
    assert sent_notes == [
        ("%9", "Plan selection note for option 3:\nkeep this staged")
    ]
    assert captures == ["agent-1"]
    assert message_text == ""


async def test_tui_tmux_plan_selection_falls_back_to_selected_pane(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    pane = tmux_support.TmuxPane(
        "s",
        "0",
        "1",
        "%9",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        80,
        24,
        100,
    )
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [pane]

    def fake_capture_pane(target: str, **_: object) -> str:
        return "Codex is asking a plan-mode question, but this text is not parsed."

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "metadata": {"cwd": "/home/me/agent-pbx"},
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_agent_targets["agent-1"] = "%9"
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = (
            "Codex is asking a plan-mode question, but this text is not parsed."
        )
        sent = await app.send_plan_selection(
            "agent-1",
            PlanSelection(index=1),
            via_tmux=True,
        )

    assert sent is True
    assert sent_keys == [("%9", "1")]
    assert captures == ["agent-1"]


async def test_tui_plan_selection_uses_recorded_native_selector_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []
    pane = tmux_support.TmuxPane(
        "s",
        "0",
        "1",
        "%9",
        True,
        "node",
        "agent-pbx",
        "/home/me/agent-pbx",
        80,
        24,
        100,
    )

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [pane]

    def fake_capture_pane(target: str, **_: object) -> str:
        return "1 Start coding\n2 Clear context & start\n3 Stay in plan mode"

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    try:
        async with app.run_test():
            app.agents = {
                "agent-1": {
                    "agent_id": "agent-1",
                    "status": "plan",
                    "project": "agent-pbx",
                    "last_seen_at": 123.0,
                }
            }
            app.selected_agent_id = "agent-1"
            app.tmux_plan_selector_agent_ids.add("agent-1")
            app.tmux_plan_selector_pane_by_agent["agent-1"] = "%9"
            sent = await app.send_native_plan_selection(
                "agent-1",
                PlanSelection(index=3),
            )
    finally:
        monkeypatch.undo()

    assert sent is True
    assert sent_keys == [("%9", "3")]
    assert captures == ["agent-1"]
    assert "agent-1" not in app.tmux_plan_selector_agent_ids
    assert "agent-1" not in app.tmux_plan_selector_pane_by_agent


async def test_tui_plan_selection_uses_visible_native_selector_pane() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []
    selector_text = (
        "› 1. Yes, implement this plan          Switch to Default and start coding.\n"
        "  2. Yes, clear context and implement  Fresh thread. Context: 26% used.\n"
        "  3. No, stay in Plan mode             Continue planning with the model."
    )

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "one",
                "last_seen_at": 123.0,
            },
            "agent-2": {
                "agent_id": "agent-2",
                "status": "plan",
                "project": "two",
                "last_seen_at": 124.0,
            },
        }
        app.selected_agent_id = "agent-2"
        app.latest_report_by_agent["agent-2"] = {"plan_options": ["Structured option"]}
        app.tmux_visible_capture_key = "agent-1:%9"
        app.query_one("#tmux-stream", TextArea).text = selector_text
        sent = await app.send_plan_selection(
            "agent-2",
            PlanSelection(index=1),
            via_tmux=True,
        )

    assert sent is True
    assert sent_keys == [("%9", "1")]
    assert captures == ["agent-2"]


async def test_tui_plan_selection_uses_unique_global_native_selector_pane(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    agent_pane = tmux_support.TmuxPane(
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
    selector_pane = tmux_support.TmuxPane(
        "s",
        "0",
        "2",
        "%2",
        False,
        "node",
        "other",
        "/home/me/other",
        80,
        24,
        100,
    )
    selector_text = (
        "  1. Yes, implement this plan          Switch to Default and start coding.\n"
        "  2. Yes, clear context and implement  Fresh thread. Context: 26% used.\n"
        "  3. No, stay in Plan mode             Continue planning with the model."
    )
    sent_keys: list[tuple[str, str]] = []
    captures: list[str] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return [agent_pane, selector_pane]

    def fake_capture_pane(target: str, **_: object) -> str:
        return selector_text if target == "%2" else "No plan selector here."

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        captures.append(agent_id)

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "metadata": {"cwd": "/home/me/agent-pbx"},
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        sent = await app.send_plan_selection(
            "agent-1",
            PlanSelection(index=2),
            via_tmux=True,
        )

    assert sent is True
    assert sent_keys == [("%2", "2")]
    assert captures == ["agent-1"]
    assert "agent-1" not in app.tmux_plan_selector_pane_by_agent


async def test_tui_plan_selection_refuses_multiple_global_native_selectors(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    panes = [
        tmux_support.TmuxPane(
            "s",
            "0",
            str(index),
            f"%{index}",
            False,
            "node",
            f"other-{index}",
            f"/home/me/other-{index}",
            80,
            24,
            100,
        )
        for index in (1, 2)
    ]
    selector_text = (
        "  1. Yes, implement this plan          Switch to Default and start coding.\n"
        "  2. Yes, clear context and implement  Fresh thread. Context: 26% used.\n"
        "  3. No, stay in Plan mode             Continue planning with the model."
    )
    sent_keys: list[tuple[str, str]] = []

    def fake_list_panes() -> list[tmux_support.TmuxPane]:
        return panes

    def fake_capture_pane(target: str, **_: object) -> str:
        return selector_text

    async def fake_send_key_to_tmux_pane(
        pane_id: str,
        key: str,
        *,
        status: Static | None = None,
    ) -> bool:
        sent_keys.append((pane_id, key))
        return True

    monkeypatch.setattr(tmux_support, "list_panes", fake_list_panes)
    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)
    app.send_key_to_tmux_pane = fake_send_key_to_tmux_pane  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "project": "agent-pbx",
                "metadata": {"cwd": "/home/me/agent-pbx"},
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        sent = await app.send_plan_selection(
            "agent-1",
            PlanSelection(index=1),
            via_tmux=True,
        )

    assert sent is False
    assert sent_keys == []


async def test_tui_native_plan_selector_sets_attention_banner() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        changed = app.update_tmux_plan_selector_state(
            "agent-1",
            "1 Start coding\n2 Clear\n3 Stay in plan mode",
        )
        attention = app.query_one("#attention", Static)

    assert changed is True
    assert "agent-1" in app.tmux_plan_selector_agent_ids
    assert "PLAN selection pending: agent-1" in str(attention.renderable)
    assert app.attention_target_agent_id() == "agent-1"


async def test_tui_native_plan_selector_revalidates_stale_alert() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.tmux_visible_capture_key = "agent-1:%1"
        app.tmux_plan_selector_agent_ids.add("agent-1")
        app.query_one("#tmux-stream", TextArea).text = "No active plan prompt here."
        pending = app.tmux_native_plan_selector_pending("agent-1")

    assert pending is False
    assert "agent-1" not in app.tmux_plan_selector_agent_ids


async def test_tui_tmux_plan_selector_fallback_resolves_ambiguous_pane(
    monkeypatch,
) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    plain_pane = tmux_support.TmuxPane(
        "s",
        "0",
        "1",
        "%1",
        True,
        "node",
        "workerbee",
        "/home/me/workerbee",
        80,
        24,
        100,
    )
    selector_pane = tmux_support.TmuxPane(
        "s",
        "0",
        "2",
        "%2",
        False,
        "node",
        "workerbee",
        "/home/me/workerbee",
        80,
        24,
        100,
    )
    selector_text = (
        "› 1. Yes, implement this plan          Switch to Default and start coding.\n"
        "  2. Yes, clear context and implement  Fresh thread. Context: 26% used.\n"
        "  3. No, stay in Plan mode             Continue planning with the model."
    )

    def fake_capture_pane(target: str, **_: object) -> str:
        return selector_text if target == "%2" else "Working on a normal prompt."

    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "workerbee",
                "metadata": {"cwd": "/home/me/workerbee"},
                "last_seen_at": 123.0,
            }
        }
        pane, mode = app.resolve_tmux_pane("agent-1", [plain_pane, selector_pane])
        resolved = await app.resolve_tmux_plan_selector_pane(
            "agent-1",
            [plain_pane, selector_pane],
        )
        attention = app.query_one("#attention", Static)

    assert pane is None
    assert mode == "auto"
    assert resolved == selector_pane
    assert app.tmux_plan_selector_pane_by_agent["agent-1"] == "%2"
    assert "agent-1" in app.tmux_plan_selector_agent_ids
    assert "PLAN selection pending: agent-1" in str(attention.renderable)


async def test_tui_plan_selection_without_options_does_not_fallback() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": "cmd-1"}

    app.queue_command = fake_queue_command  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "agent-1": {
                "agent_id": "agent-1",
                "status": "plan",
                "project": "agent-pbx",
                "last_seen_at": 123.0,
            }
        }
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        app.query_one("#message", TextArea).text = "/plan:2"
        await app.send_input()
        message_text = app.query_one("#message", TextArea).text

    assert queued == []
    assert message_text == "/plan:2"


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


async def test_tui_follow_up_up_recalls_prior_sent_agent_messages() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    queued: list[tuple[str, str, dict[str, str]]] = []

    async def fake_queue_command(
        agent_id: str, command_type: str, payload: dict[str, str]
    ) -> dict[str, str]:
        queued.append((agent_id, command_type, payload))
        return {"command_id": f"cmd-{len(queued)}"}

    async def fake_refresh_events() -> None:
        return None

    async def fake_load_thread(agent_id: str) -> None:
        return None

    app.queue_command = fake_queue_command  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#message", TextArea)
        message.text = "first prompt"
        await app.send_input()
        message.text = "second prompt"
        await app.send_input()

        await message._on_key(Key("up", None))
        recalled_latest = message.text
        await message._on_key(Key("up", None))
        recalled_previous = message.text
        await message._on_key(Key("down", None))
        recalled_next = message.text

    assert [item[2]["message"] for item in queued] == ["first prompt", "second prompt"]
    assert recalled_latest == "second prompt"
    assert recalled_previous == "first prompt"
    assert recalled_next == "second prompt"


async def test_tui_follow_up_up_does_not_replace_existing_draft() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    async with app.run_test():
        app.query_one("#agent-id", Input).value = "agent-1"
        app.record_sent_message("agent-1", "sent prompt")
        message = app.query_one("#message", TextArea)
        message.text = "draft"
        await message._on_key(Key("up", None))

    assert message.text == "draft"


async def test_tui_tmux_follow_up_up_recalls_prior_sent_messages() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    sent: list[tuple[str, str]] = []

    async def fake_send_text_to_tmux(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    async def fake_load_tmux_capture(agent_id: str) -> None:
        return None

    app.send_text_to_tmux = fake_send_text_to_tmux  # type: ignore[method-assign]
    app.load_tmux_capture = fake_load_tmux_capture  # type: ignore[method-assign]

    async with app.run_test():
        app.selected_agent_id = "agent-1"
        app.query_one("#agent-id", Input).value = "agent-1"
        message = app.query_one("#tmux-message", TextArea)
        message.text = "tmux first"
        await app.send_tmux_input()
        message.text = "tmux second"
        await app.send_tmux_input()

        await message._on_key(Key("up", None))
        recalled_latest = message.text
        await message._on_key(Key("up", None))
        recalled_previous = message.text

    assert sent == [("agent-1", "tmux first"), ("agent-1", "tmux second")]
    assert recalled_latest == "tmux second"
    assert recalled_previous == "tmux first"


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


def test_tui_slash_completion_key_detection_accepts_terminal_variants() -> None:
    assert slash_completion_direction(Key("tab", "\t")) == 1
    assert slash_completion_direction(Key("shift+tab", None)) == -1
    assert slash_completion_direction(Key("shift_tab", None)) == -1
    assert slash_completion_direction(Key("backtab", None)) == -1
    assert slash_completion_direction(Key("enter", None)) is None


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


def test_tui_operator_fork_ids_preserve_default_and_track_review() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    default_id = app.operator_fork_agent_id(
        "operator-0",
        "caller-1",
        "session-caller-1",
    )
    explicit_default_id = app.operator_fork_agent_id(
        "operator-0",
        "caller-1",
        "session-caller-1",
        fork_track_id="default",
    )
    review_id = app.operator_fork_agent_id(
        "operator-0",
        "caller-1",
        "session-caller-1",
        fork_track_id="review-1",
    )

    assert default_id == "operator-0-fork-caller-1-86e61603"
    assert explicit_default_id == default_id
    assert review_id != default_id
    assert "review-1" in review_id


def test_tui_review_fork_source_prefers_root_default_over_stale_caller() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "default_source_caller_agent_id": "codex-k1s-workerbee-private",
            },
        },
        "codex-k1s-workerbee-private": {
            "agent_id": "codex-k1s-workerbee-private",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
            "metadata": {"codex_session_id": "workerbee-session"},
        },
        "codex-micropc-debian": {
            "agent_id": "codex-micropc-debian",
            "agent_type": "caller",
            "project": "micropc-debian",
            "metadata": {"codex_session_id": "micropc-session"},
        },
    }
    app.selected_agent_id = "codex-micropc-debian"

    assert (
        app.source_caller_agent_id_for_review_fork("operator-0")
        == "codex-k1s-workerbee-private"
    )


def test_tui_review_fork_source_prefers_root_default_over_focused_caller() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "root",
                "default_source_caller_agent_id": "codex-k1s-workerbee-private",
            },
        },
        "codex-k1s-workerbee-private": {
            "agent_id": "codex-k1s-workerbee-private",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
            "metadata": {"codex_session_id": "workerbee-session"},
        },
        "codex-micropc-debian": {
            "agent_id": "codex-micropc-debian",
            "agent_type": "caller",
            "project": "micropc-debian",
            "metadata": {"codex_session_id": "micropc-session"},
        },
    }
    app.selected_agent_id = "codex-micropc-debian"
    app.focused_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]

    assert (
        app.source_caller_agent_id_for_review_fork("operator-0")
        == "codex-k1s-workerbee-private"
    )


def test_tui_review_fork_source_prefers_active_default_fork_over_agents_state() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        "operator-0-fork-codex-k1s-workerbee-private": {
            "agent_id": "operator-0-fork-codex-k1s-workerbee-private",
            "agent_type": "operator",
            "status": "running",
            "effective_status": "running",
            "project": "k1s-workerbee-private",
            "metadata": {
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "codex-k1s-workerbee-private",
                "source_codex_session_id": "workerbee-session",
                "fork_track_id": "default",
                "fork_purpose": "edit",
                "operator_fork_pending": False,
            },
        },
        "codex-k1s-workerbee-private": {
            "agent_id": "codex-k1s-workerbee-private",
            "agent_type": "caller",
            "project": "k1s-workerbee-private",
            "metadata": {"codex_session_id": "workerbee-session"},
        },
        "codex-micropc-debian": {
            "agent_id": "codex-micropc-debian",
            "agent_type": "caller",
            "project": "micropc-debian",
            "metadata": {"codex_session_id": "micropc-session"},
        },
    }
    app.selected_agent_id = "codex-micropc-debian"
    app.focused_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]
    app.selected_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]

    assert (
        app.source_caller_agent_id_for_review_fork("operator-0")
        == "codex-k1s-workerbee-private"
    )


def test_tui_review_fork_source_has_no_agents_fallback_for_root_operator() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        "codex-micropc-debian": {
            "agent_id": "codex-micropc-debian",
            "agent_type": "caller",
            "project": "micropc-debian",
            "metadata": {"codex_session_id": "micropc-session"},
        },
    }
    app.selected_agent_id = "codex-micropc-debian"
    app.focused_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]
    app.selected_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]

    assert app.source_caller_agent_id_for_review_fork("operator-0") is None


async def test_tui_review_fork_source_resolves_hidden_default_fork_from_registry() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    gets: list[dict[str, object]] = []

    class Response:
        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **kwargs: object) -> Response:
            gets.append({"path": path, **kwargs})
            if path == "/v1/operator/forks":
                return Response(
                    {
                        "forks": [
                            {
                                "fork_agent_id": (
                                    "operator-0-fork-codex-k1s-workerbee-private"
                                ),
                                "source_caller_agent_id": (
                                    "codex-k1s-workerbee-private"
                                ),
                                "source_codex_session_id": "workerbee-session",
                                "fork_track_id": "default",
                                "fork_purpose": "edit",
                                "status": "done",
                                "metadata": {
                                    "operator_role": "fork",
                                    "logical_operator_id": "operator-0",
                                    "source_caller_agent_id": (
                                        "codex-k1s-workerbee-private"
                                    ),
                                    "fork_track_id": "default",
                                    "fork_purpose": "edit",
                                    "operator_fork_pending": False,
                                },
                            },
                            {
                                "fork_agent_id": (
                                    "operator-0-fork-codex-micropc-debian-review-1"
                                ),
                                "source_caller_agent_id": "codex-micropc-debian",
                                "fork_track_id": "review-1",
                                "fork_purpose": "review",
                                "status": "running",
                                "metadata": {
                                    "source_caller_agent_id": "codex-micropc-debian",
                                    "fork_track_id": "review-1",
                                    "fork_purpose": "review",
                                    "operator_fork_pending": False,
                                },
                            },
                        ]
                    }
                )
            raise AssertionError(path)

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.agents = {
        "operator-0": {
            "agent_id": "operator-0",
            "agent_type": "operator",
            "project": "agent-pbx-operator",
            "metadata": {"agent_type": "operator", "operator_role": "root"},
        },
        "codex-micropc-debian": {
            "agent_id": "codex-micropc-debian",
            "agent_type": "caller",
            "project": "micropc-debian",
            "metadata": {"codex_session_id": "micropc-session"},
        },
    }
    app.selected_agent_id = "codex-micropc-debian"
    app.focused_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]
    app.selected_caller_agent_id_for_fork = lambda: "codex-micropc-debian"  # type: ignore[method-assign]

    assert (
        await app.resolve_source_caller_agent_id_for_review_fork("operator-0")
        == "codex-k1s-workerbee-private"
    )
    assert gets[0]["path"] == "/v1/operator/forks"
    assert gets[0]["params"] == {  # type: ignore[index]
        "operator_agent_id": "operator-0",
        "fork_track_id": "default",
        "limit": 500,
    }


def test_tui_default_operator_fork_source_records_refuse_ambiguity() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert (
        app.single_default_operator_fork_source_agent_id_from_records(
            [
                {
                    "source_caller_agent_id": "caller-1",
                    "fork_track_id": "default",
                    "fork_purpose": "edit",
                    "status": "running",
                    "metadata": {"operator_fork_pending": False},
                },
                {
                    "source_caller_agent_id": "caller-2",
                    "fork_track_id": "default",
                    "fork_purpose": "edit",
                    "status": "done",
                    "metadata": {"operator_fork_pending": False},
                },
            ]
        )
        is None
    )


def test_tui_operator_root_metadata_omits_source_keys_without_binding() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    metadata = app.operator_root_metadata(
        "operator-0",
        cwd="/tmp/agent-pbx",
        codex_command="codex",
        mcp_url="http://127.0.0.1:8765/mcp",
        session_name="agent-pbx-operators",
    )

    assert "default_source_caller_agent_id" not in metadata
    assert "default_source_caller_project" not in metadata
    assert "default_source_codex_session_id" not in metadata


def test_tui_codex_model_preset_metadata_and_overrides() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    preset = codex_model_preset_for("terra-max")
    terra_xhigh = codex_model_preset_for("terra-xhigh")
    sol_xhigh = codex_model_preset_for("sol-xhigh")
    sol_max = codex_model_preset_for("sol-max")

    assert preset is not None
    assert terra_xhigh is not None
    assert sol_xhigh is not None
    assert sol_max is not None
    assert terra_xhigh.reasoning_summary == "detailed"
    assert sol_xhigh.reasoning_summary == "detailed"
    assert sol_max.reasoning_summary == "detailed"
    command = app.operator_resume_command(
        "codex",
        "session-1",
        config_overrides=[
            'model="gpt-5.6-terra"',
            'model_reasoning_effort="max"',
            'model_reasoning_summary="detailed"',
            'model_verbosity="high"',
        ],
    )
    metadata = app.operator_root_metadata(
        "operator-5",
        cwd="/tmp/agent-pbx",
        codex_command="codex",
        mcp_url="http://127.0.0.1:8765/mcp",
        session_name="agent-pbx-operators",
        model_preset=preset,
    )

    argv = shlex.split(command)
    assert 'model="gpt-5.6-terra"' in argv
    assert 'model_reasoning_effort="max"' in argv
    assert 'model_reasoning_summary="detailed"' in argv
    assert 'model_verbosity="high"' in argv
    assert metadata["codex_model_preset"] == "terra-5.6-max"
    assert metadata["codex_model"] == "gpt-5.6-terra"
    assert metadata["codex_model_reasoning_effort"] == "max"
    assert metadata["codex_model_reasoning_summary"] == "detailed"
    assert metadata["codex_model_verbosity"] == "high"

    for sol in (sol_xhigh, sol_max):
        assert codex_model_preset_config_overrides(sol) == [
            'model="gpt-5.6-sol"',
            f'model_reasoning_effort="{sol.reasoning_effort}"',
            'model_reasoning_summary="detailed"',
            'model_verbosity="high"',
        ]
        sol_metadata = app.operator_root_metadata(
            "operator-5",
            cwd="/tmp/agent-pbx",
            codex_command="codex",
            mcp_url="http://127.0.0.1:8765/mcp",
            session_name="agent-pbx-operators",
            model_preset=sol,
        )
        sol_env = app.operator_launch_env(
            agent_id="operator-5",
            cwd="/tmp/agent-pbx",
            mcp_url="http://127.0.0.1:8765/mcp",
            model_preset=sol,
        )
        assert sol_metadata["codex_model_preset"] == sol.key
        assert sol_metadata["codex_model"] == "gpt-5.6-sol"
        assert sol_metadata["codex_model_reasoning_effort"] == sol.reasoning_effort
        assert sol_metadata["codex_model_reasoning_summary"] == "detailed"
        assert sol_metadata["codex_model_verbosity"] == "high"
        assert sol_env["AGENT_PBX_CODEX_MODEL_PRESET"] == sol.key
        assert sol_env["AGENT_PBX_CODEX_MODEL"] == "gpt-5.6-sol"
        assert sol_env["AGENT_PBX_CODEX_REASONING_EFFORT"] == sol.reasoning_effort
        assert sol_env["AGENT_PBX_CODEX_REASONING_SUMMARY"] == "detailed"
        assert sol_env["AGENT_PBX_CODEX_VERBOSITY"] == "high"


async def test_tui_saving_model_default_preserves_summary_and_refuses_sol_max() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    requests: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"config": {"fields": []}}

    class Client:
        async def patch(self, path: str, **kwargs: object) -> Response:
            requests.append({"path": path, **kwargs})
            return Response()

    async def fake_validate(_: object) -> bool:
        return True

    async def fake_refresh() -> None:
        return None

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.ensure_codex_model_preset_valid = fake_validate  # type: ignore[method-assign]
    app.render_codex_config = lambda _config: None  # type: ignore[method-assign]
    app.refresh_codex_posture = fake_refresh  # type: ignore[method-assign]
    app.notify = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    terra = codex_model_preset_for("terra-max")
    sol_xhigh = codex_model_preset_for("sol-xhigh")
    sol_max = codex_model_preset_for("sol-max")
    assert terra is not None
    assert sol_xhigh is not None
    assert sol_max is not None

    await app.save_codex_model_preset_as_default(terra)
    await app.save_codex_model_preset_as_default(sol_xhigh)
    await app.save_codex_model_preset_as_default(sol_max)

    assert requests[0] == {
        "path": "/v1/codex/config",
        "json": {
            "updates": {
                "model": "gpt-5.6-terra",
                "model_reasoning_effort": "max",
                "model_verbosity": "high",
                "model_reasoning_summary": "detailed",
            }
        },
        "headers": {},
    }
    assert requests[1] == {
        "path": "/v1/codex/config",
        "json": {
            "updates": {
                "model": "gpt-5.6-sol",
                "model_reasoning_effort": "xhigh",
                "model_verbosity": "high",
                "model_reasoning_summary": "detailed",
            }
        },
        "headers": {},
    }
    assert len(requests) == 2


def test_tui_operator_fork_command_supports_cd_and_sandbox() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    command = app.operator_fork_command(
        "codex",
        "source-session",
        "review prompt",
        cd="/tmp/review root",
        sandbox="workspace-write",
    )

    assert command == (
        "codex fork --cd '/tmp/review root' --sandbox workspace-write "
        "source-session 'review prompt'"
    )


def test_tui_operator_fork_command_supports_config_overrides() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    command = app.operator_fork_command(
        "codex",
        "source-session",
        "review prompt",
        config_overrides=[
            'mcp_servers.agent-pbx.default_tools_approval_mode="approve"',
        ],
    )

    argv = shlex.split(command)
    assert argv[:2] == ["codex", "fork"]
    assert argv[-2:] == ["source-session", "review prompt"]
    assert "-c" in argv
    assert (
        'mcp_servers.agent-pbx.default_tools_approval_mode="approve"'
        in argv
    )


def test_tui_codex_start_command_supports_project_spawn_options() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    command = app.codex_start_command(
        "codex --search",
        "bootstrap prompt",
        cd="/tmp/new project",
        sandbox="workspace-write",
        config_overrides=[
            'projects={"/tmp/new project" = {trust_level = "trusted"}}',
        ],
    )

    argv = shlex.split(command)
    assert argv[:2] == ["codex", "--search"]
    assert argv[2:5] == ["--cd", "/tmp/new project", "--sandbox"]
    assert "workspace-write" in argv
    assert "-c" in argv
    assert argv[-1] == "bootstrap prompt"


def test_tui_codex_interactive_command_supports_operator_options() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    command = app.codex_interactive_command(
        "codex --search",
        cd="/tmp/operator root",
        config_overrides=[
            'mcp_servers.agent-pbx.default_tools_approval_mode="approve"',
        ],
    )

    argv = shlex.split(command)
    assert argv[:2] == ["codex", "--search"]
    assert argv[2:4] == ["--cd", "/tmp/operator root"]
    assert "-c" in argv
    assert (
        'mcp_servers.agent-pbx.default_tools_approval_mode="approve"'
        in argv
    )


def test_tui_operator_resume_command_supports_restart_options() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    command = app.operator_resume_command(
        "codex --search",
        "session-1",
        cd="/tmp/work",
        sandbox="workspace-write",
        config_overrides=[
            'mcp_servers.agent-pbx.default_tools_approval_mode="approve"',
        ],
    )

    argv = shlex.split(command)
    assert argv[:2] == ["codex", "--search"]
    assert argv[2:5] == ["resume", "--cd", "/tmp/work"]
    assert "--sandbox" in argv
    assert "workspace-write" in argv
    assert "-c" in argv
    assert argv[-1] == "session-1"


def test_tui_codex_command_from_start_command_preserves_flags() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    assert (
        app.codex_command_from_start_command(
            '"codex --search --yolo resume session-1"'
        )
        == "codex --search --yolo"
    )
    assert (
        app.codex_command_from_start_command(
            "codex --search fork session-1 prompt"
        )
        == "codex --search"
    )
    assert app.codex_command_from_start_command("python app.py") == ""


def test_tui_review_operator_mcp_config_overrides_allowlist_known_tools() -> None:
    overrides = review_operator_mcp_config_overrides(
        ("agent-pbx", "workerbee"),
        mcp_url="http://127.0.0.1:8767/mcp",
        server_configs={"workerbee": {"url": "http://127.0.0.1:8765/mcp"}},
    )

    agent_pbx_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    workerbee_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.workerbee=")
    )
    assert 'url = "http://127.0.0.1:8767/mcp"' in agent_pbx_config
    assert 'bearer_token_env_var = "AGENT_PBX_TOKEN"' in agent_pbx_config
    assert 'default_tools_approval_mode = "approve"' in agent_pbx_config
    assert "pbx_register_agent" in agent_pbx_config
    assert "pbx_report_turn" in agent_pbx_config
    assert "pbx_operator_request_project_spawn" in agent_pbx_config
    assert "pbx_operator_propose_knowledge_handoff" in agent_pbx_config
    assert "pbx_operator_list_knowledge_links" in agent_pbx_config
    assert "pbx_operator_get_knowledge_context" in agent_pbx_config
    assert "pbx_operator_list_handoffs" in agent_pbx_config
    assert "pbx_operator_get_handoff" in agent_pbx_config
    assert "pbx_operator_preflight_handoff" in agent_pbx_config
    assert "pbx_operator_kb_search" in agent_pbx_config
    assert "pbx_operator_kb_context" in agent_pbx_config
    assert "pbx_operator_kb_get" in agent_pbx_config
    assert "pbx_operator_kb_list_queries" in agent_pbx_config
    assert "pbx_operator_kb_get_query" in agent_pbx_config
    assert "pbx_operator_kb_feedback" in agent_pbx_config
    assert "pbx_operator_kb_propose" in agent_pbx_config
    assert "pbx_operator_kb_propose_from_link" in agent_pbx_config
    assert "pbx_operator_kb_compile_report" in agent_pbx_config
    assert "pbx_operator_kb_list_seed_runs" in agent_pbx_config
    assert "pbx_operator_kb_get_seed_run" in agent_pbx_config
    assert "pbx_operator_kb_update_seed_run" in agent_pbx_config
    assert '"pbx_operator_kb_seed"' not in agent_pbx_config
    assert '"pbx_operator_approve_handoff"' not in agent_pbx_config
    assert '"pbx_operator_update_handoff"' not in agent_pbx_config
    assert '"pbx_operator_send_knowledge_turn"' not in agent_pbx_config
    assert '"pbx_operator_approve_knowledge_turn"' not in agent_pbx_config
    assert '"pbx_operator_create_knowledge_link"' not in agent_pbx_config
    assert '"pbx_operator_close_knowledge_link"' not in agent_pbx_config
    assert '"pbx_operator_kb_promote"' not in agent_pbx_config
    assert '"pbx_operator_kb_update"' not in agent_pbx_config
    assert '"pbx_operator_kb_reject"' not in agent_pbx_config
    assert '"pbx_operator_kb_retire"' not in agent_pbx_config
    assert '"pbx_operator_kb_import"' not in agent_pbx_config
    assert '"pbx_operator_kb_export"' not in agent_pbx_config
    assert '"pbx_operator_kb_run_index_jobs"' not in agent_pbx_config
    assert '"pbx_operator_kb_rebuild_index"' not in agent_pbx_config
    assert "pbx_queue_command" not in agent_pbx_config
    assert 'url = "http://127.0.0.1:8765/mcp"' in workerbee_config
    assert 'default_tools_approval_mode = "approve"' in workerbee_config
    assert "workerbee_v1_capabilities" in workerbee_config
    assert "workerbee_v1_session_start" in workerbee_config
    assert "workerbee_v1_project_status" in workerbee_config
    assert "workerbee_v1_logs" in workerbee_config
    assert "workerbee_v1_workload_restart" not in workerbee_config
    assert "workerbee_v1_exec" not in workerbee_config
    assert not any(".enabled_tools=" in item for item in overrides)


def test_tui_codex_terminal_mode_overrides_are_explicit_and_bounded() -> None:
    portable = [
        'tui.keymap.composer.submit=["enter"]',
        'tui.keymap.editor.insert_newline=["ctrl-j","shift-enter","alt-enter","ctrl-enter"]',
    ]
    assert codex_terminal_config_overrides(CODEX_TERMINAL_MODE_DEFAULT) == portable
    assert codex_terminal_config_overrides(CODEX_TERMINAL_MODE_SCROLLBACK) == [
        *portable,
        'tui.alternate_screen="never"'
    ]
    assert codex_terminal_config_overrides(CODEX_TERMINAL_MODE_RAW) == [
        *portable,
        'tui.alternate_screen="never"',
        "tui.raw_output_mode=true",
    ]


def test_tui_managed_launch_config_enables_scrollback_mode() -> None:
    overrides = review_operator_config_overrides(
        ("agent-pbx",),
        mcp_url="http://127.0.0.1:8767/mcp",
        terminal_mode=CODEX_TERMINAL_MODE_SCROLLBACK,
    )

    assert 'tui.alternate_screen="never"' in overrides
    assert "tui.raw_output_mode=true" not in overrides


def test_tui_workerbee_bootstrap_policy_covers_all_operator_roles() -> None:
    kwargs = {
        "mcp_url": "http://127.0.0.1:8767/mcp",
        "server_configs": {
            "workerbee": {"url": "http://127.0.0.1:8765/mcp"},
        },
    }
    role_overrides = {
        "root": operator_agent_config_overrides(
            ("workerbee",),
            include_campaign_lifecycle=True,
            **kwargs,
        ),
        "edit-fork": operator_agent_config_overrides(
            ("workerbee",),
            **kwargs,
        ),
        "review-fork": review_operator_config_overrides(
            ("workerbee",),
            **kwargs,
        ),
    }

    for role, overrides in role_overrides.items():
        workerbee_config = next(
            item for item in overrides if item.startswith("mcp_servers.workerbee=")
        )
        assert "workerbee_v1_capabilities" in workerbee_config, role
        assert "workerbee_v1_session_start" in workerbee_config, role
        assert "workerbee_v1_workload_restart" not in workerbee_config, role
        assert "workerbee_v1_exec" not in workerbee_config, role


def test_tui_direct_caller_launch_preserves_global_workerbee_config() -> None:
    overrides = caller_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
    )

    assert not any(item.startswith("mcp_servers.workerbee=") for item in overrides)


def test_tui_role_launches_use_distinct_sol_reasoning_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        CODEX_MODEL_ENV,
        CODEX_REASONING_EFFORT_ENV,
        CODEX_REASONING_SUMMARY_ENV,
        CODEX_VERBOSITY_ENV,
        CODEX_SERVICE_TIER_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    caller = caller_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
    )
    operator = operator_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
    )

    assert 'model="gpt-5.6-sol"' in caller
    assert 'model_reasoning_effort="high"' in caller
    assert 'model_reasoning_summary="detailed"' in caller
    assert 'model_verbosity="high"' in caller
    assert 'model="gpt-5.6-sol"' in operator
    assert 'model_reasoning_effort="xhigh"' in operator
    assert 'model_reasoning_summary="detailed"' in operator
    assert 'model_verbosity="high"' in operator


def test_tui_restart_model_defaults_follow_entity_role() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")

    caller = app.restart_model_preset({}, None)
    operator = app.restart_model_preset({}, None, operator=True)

    assert caller is not None and caller.key == "sol-5.6-high"
    assert operator is not None and operator.key == "sol-5.6-xhigh"


def test_tui_review_operator_config_overrides_trusts_scratch_work_root() -> None:
    overrides = review_operator_config_overrides(
        ("agent-pbx",),
        mcp_url="http://127.0.0.1:8767/mcp",
        work_root="/tmp/review root",
    )

    trusted = str(Path("/tmp/review root").resolve())
    assert f'projects={{"{trusted}" = {{trust_level = "trusted"}}}}' in overrides
    assert any(item.startswith("mcp_servers.agent-pbx=") for item in overrides)


def test_tui_caller_agent_config_overrides_allowlist_report_tools() -> None:
    overrides = caller_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
        work_root="/tmp/new project",
    )

    agent_pbx_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    assert 'url = "http://127.0.0.1:8767/mcp"' in agent_pbx_config
    assert 'default_tools_approval_mode = "approve"' in agent_pbx_config
    assert "pbx_register_agent" in agent_pbx_config
    assert "pbx_report_turn" in agent_pbx_config
    assert "pbx_operator_request_project_spawn" not in agent_pbx_config
    assert "pbx_operator_propose_knowledge_handoff" not in agent_pbx_config
    assert "pbx_operator_list_handoffs" not in agent_pbx_config
    assert "pbx_operator_send_knowledge_turn" not in agent_pbx_config
    trusted = str(Path("/tmp/new project").resolve())
    assert f'projects={{"{trusted}" = {{trust_level = "trusted"}}}}' in overrides


def test_tui_operator_agent_config_overrides_follow_operator_server_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPERATOR_MCP_APPROVAL_SERVERS_ENV, "agent-pbx,workerbee")
    monkeypatch.setenv(REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV, "agent-pbx")

    overrides = operator_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
        work_root="/tmp/operator root",
        server_configs={"workerbee": {"url": "http://127.0.0.1:8765/mcp"}},
    )

    agent_pbx_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    workerbee_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.workerbee=")
    )
    assert 'default_tools_approval_mode = "approve"' in agent_pbx_config
    assert "pbx_register_agent" in agent_pbx_config
    assert "pbx_operator_runbook" in agent_pbx_config
    assert "pbx_operator_kb_search" in agent_pbx_config
    assert "pbx_queue_command" not in agent_pbx_config
    assert "pbx_operator_start_campaign" not in agent_pbx_config
    assert "pbx_operator_send_followup" not in agent_pbx_config
    assert "pbx_operator_finish_campaign" not in agent_pbx_config
    assert 'default_tools_approval_mode = "approve"' in workerbee_config
    assert "workerbee_v1_project_status" in workerbee_config
    assert "workerbee_v1_exec" not in workerbee_config
    trusted = str(Path("/tmp/operator root").resolve())
    assert f'projects={{"{trusted}" = {{trust_level = "trusted"}}}}' in overrides


def test_tui_root_operator_config_includes_campaign_lifecycle_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPERATOR_MCP_APPROVAL_SERVERS_ENV, "agent-pbx")

    overrides = operator_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
        include_campaign_lifecycle=True,
    )

    agent_pbx_config = next(
        item
        for item in overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    assert "pbx_operator_start_campaign" in agent_pbx_config
    assert "pbx_operator_send_followup" in agent_pbx_config
    assert "pbx_operator_finish_campaign" in agent_pbx_config


def test_tui_operator_agent_config_overrides_fallback_to_review_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPERATOR_MCP_APPROVAL_SERVERS_ENV, raising=False)
    monkeypatch.setenv(REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV, "agent-pbx,workerbee")

    overrides = operator_agent_config_overrides(
        mcp_url="http://127.0.0.1:8767/mcp",
        server_configs={"workerbee": {"url": "http://127.0.0.1:8765/mcp"}},
    )

    assert any(item.startswith("mcp_servers.agent-pbx=") for item in overrides)
    assert any(item.startswith("mcp_servers.workerbee=") for item in overrides)


def test_tui_agent_config_overrides_include_codex_model_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CODEX_MODEL_ENV, "gpt-6-sol")
    monkeypatch.setenv(CODEX_REASONING_EFFORT_ENV, "high")
    monkeypatch.setenv(CODEX_REASONING_SUMMARY_ENV, "detailed")
    monkeypatch.setenv(CODEX_VERBOSITY_ENV, "high")
    monkeypatch.setenv(CODEX_SERVICE_TIER_ENV, "priority")

    overrides = operator_agent_config_overrides(mcp_url="http://127.0.0.1:8767/mcp")

    assert 'model="gpt-6-sol"' in overrides
    assert 'model_reasoning_effort="high"' in overrides
    assert 'model_reasoning_summary="detailed"' in overrides
    assert 'model_verbosity="high"' in overrides
    assert 'service_tier="priority"' in overrides


def test_tui_loads_codex_mcp_server_transport_config(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.workerbee]",
                'url = "http://127.0.0.1:18765/mcp"',
                "",
                '[mcp_servers."agent-pbx"]',
                'url = "http://127.0.0.1:18767/mcp"',
                'bearer_token_env_var = "AGENT_PBX_TOKEN"',
                "",
                "[other]",
                'url = "http://127.0.0.1:9999/mcp"',
            ]
        ),
        encoding="utf-8",
    )

    configs = load_codex_mcp_server_configs(codex_home, ("agent-pbx", "workerbee"))

    assert configs == {
        "agent-pbx": {
            "url": "http://127.0.0.1:18767/mcp",
            "bearer_token_env_var": "AGENT_PBX_TOKEN",
        },
        "workerbee": {"url": "http://127.0.0.1:18765/mcp"},
    }


async def test_tui_start_review_operator_fork_uses_scratch_work_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller"
    source_cwd.mkdir()
    monkeypatch.setenv(
        REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV,
        "agent-pbx,workerbee",
    )
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    opened: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/auth/check":
                return Response({"ok": True})
            if path == "/v1/operator/forks":
                return Response({"forks": []})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            if path == "/v1/agents/register":
                return Response(
                    {
                        "agent_id": body["agent_id"],  # type: ignore[index]
                        "agent_type": "operator",
                        "project": body["project"],  # type: ignore[index]
                        "name": body.get("name"),  # type: ignore[union-attr]
                        "status": "online",
                        "effective_status": "online",
                        "pbx_active": True,
                        "metadata": body["metadata"],  # type: ignore[index]
                        "created_at": 1.0,
                        "last_seen_at": 1.0,
                    }
                )
            if path == "/v1/operator/forks/ensure":
                metadata = body["metadata"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-review-1",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "source-session",
                        "fork_track_id": body["fork_track_id"],  # type: ignore[index]
                        "fork_purpose": body["fork_purpose"],  # type: ignore[index]
                        "access_mode": body["access_mode"],  # type: ignore[index]
                        "source_cwd": body["source_cwd"],  # type: ignore[index]
                        "work_root": body["work_root"],  # type: ignore[index]
                        "cwd": body["work_root"],  # type: ignore[index]
                        "codex_home": None,
                        "codex_host_id": None,
                        "tmux_pane_id": body["tmux_pane_id"],  # type: ignore[index]
                        "status": "running",
                        "summary": "Fork launched from Agent PBX TUI.",
                        "metadata": metadata,
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "last_used_at": 1.0,
                        "completed_at": None,
                        "edges": [],
                    }
                )
            raise AssertionError(path)

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        opened.append(agent_id)
        return True

    async def fail_send_text_to_tmux_pane(*_: object, **__: object) -> bool:
        raise AssertionError("review fork creation should not paste a task after launch")

    def fail_agents_pane_fallback() -> str | None:
        raise AssertionError("Review W from an operator must not inspect Agents pane state")

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%dead" if len(launches) == 1 else "%77"

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.send_text_to_tmux_pane = fail_send_text_to_tmux_pane  # type: ignore[method-assign]
    app.focused_caller_agent_id_for_fork = fail_agents_pane_fallback  # type: ignore[method-assign]
    app.selected_caller_agent_id_for_fork = fail_agents_pane_fallback  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_ATTEMPTS", 1)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", lambda *args, **kwargs: [])
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target == "%77")

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                },
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "source-session",
                    "codex_host_id": "local",
                },
            },
            "codex-micropc-debian": {
                "agent_id": "codex-micropc-debian",
                "agent_type": "caller",
                "project": "stale",
                "metadata": {
                    "cwd": str(tmp_path / "stale"),
                    "codex_session_id": "stale-session",
                    "codex_host_id": "local",
                },
            },
        }
        app.selected_agent_id = "operator-0"
        await app.start_review_operator_fork()

    assert len(launches) == 2
    launch = launches[-1]
    work_root = Path(str(launch["cwd"]))
    assert work_root.name == "operator-0-caller-1-review-1"
    assert not work_root.is_relative_to(source_cwd)
    argv = shlex.split(str(launch["command"]))
    assert "--cd" in argv
    assert "--sandbox" in argv
    assert "workspace-write" in argv
    assert argv[1] == "fork"
    assert argv[-2] == "source-session"
    bootstrap_prompt = argv[-1]
    assert "Register this session with:" in bootstrap_prompt
    assert "wait at the Codex prompt for operator instructions" in bootstrap_prompt
    assert "Do not begin review or caller work until the operator sends a task" in bootstrap_prompt
    assert "Startup readiness:" in bootstrap_prompt
    assert 'status="waiting"' in bootstrap_prompt
    assert "Initial instructions:" not in bootstrap_prompt
    assert "stale-session" not in argv
    config_overrides = [
        argv[index + 1]
        for index, item in enumerate(argv)
        if item == "-c"
    ]
    agent_pbx_override = next(
        item
        for item in config_overrides
        if item.startswith("mcp_servers.agent-pbx=")
    )
    workerbee_override = next(
        item
        for item in config_overrides
        if item.startswith("mcp_servers.workerbee=")
    )
    assert 'url = "http://127.0.0.1:8765/mcp"' in agent_pbx_override
    assert 'bearer_token_env_var = "AGENT_PBX_TOKEN"' in agent_pbx_override
    assert 'default_tools_approval_mode = "approve"' in agent_pbx_override
    assert "url = " in workerbee_override
    assert 'default_tools_approval_mode = "approve"' in workerbee_override
    assert (
        f"projects={{{json.dumps(str(work_root.resolve()))} = "
        '{trust_level = "trusted"}}'
        in config_overrides
    )
    env = launch["env"]
    assert isinstance(env, dict)
    assert env["AGENT_PBX_OPERATOR_FORK_TRACK_ID"] == "review-1"
    assert env["AGENT_PBX_OPERATOR_FORK_PURPOSE"] == "review"
    assert env["AGENT_PBX_OPERATOR_ACCESS_MODE"] == "review_readonly"
    assert env["AGENT_PBX_SOURCE_CWD"] == str(source_cwd)
    assert env["AGENT_PBX_OPERATOR_WORK_ROOT"] == str(work_root)
    assert [post["path"] for post in posts] == ["/v1/operator/forks/ensure"]
    ensure_body = posts[-1]["json"]
    assert ensure_body["source_caller_agent_id"] == "caller-1"  # type: ignore[index]
    assert ensure_body["fork_track_id"] == "review-1"  # type: ignore[index]
    assert ensure_body["fork_purpose"] == "review"  # type: ignore[index]
    assert ensure_body["access_mode"] == "review_readonly"  # type: ignore[index]
    assert ensure_body["work_root"] == str(work_root)  # type: ignore[index]
    assert ensure_body["metadata"]["review_mcp_approval_servers"] == [  # type: ignore[index]
        "agent-pbx",
        "workerbee",
    ]
    assert opened and "review-1" in opened[0]
    assert app.tmux_agent_targets[str(ensure_body["fork_agent_id"])] == "%77"  # type: ignore[index]
    assert app.tmux_direct_agent_modes[str(ensure_body["fork_agent_id"])] is True  # type: ignore[index]


async def test_tui_start_review_operator_fork_falls_back_to_fresh_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller"
    source_cwd.mkdir()
    monkeypatch.setenv(
        REVIEW_OPERATOR_MCP_APPROVAL_SERVERS_ENV,
        "agent-pbx,workerbee",
    )
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    quit_calls: list[str] = []
    opened: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/auth/check":
                return Response({"ok": True})
            if path == "/v1/operator/forks":
                return Response({"forks": []})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            if path == "/v1/operator/forks/ensure":
                metadata = body["metadata"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-review-1",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "source-session",
                        "fork_track_id": body["fork_track_id"],  # type: ignore[index]
                        "fork_purpose": body["fork_purpose"],  # type: ignore[index]
                        "access_mode": body["access_mode"],  # type: ignore[index]
                        "source_cwd": body["source_cwd"],  # type: ignore[index]
                        "work_root": body["work_root"],  # type: ignore[index]
                        "cwd": body["work_root"],  # type: ignore[index]
                        "codex_home": None,
                        "codex_host_id": None,
                        "tmux_pane_id": body["tmux_pane_id"],  # type: ignore[index]
                        "status": "running",
                        "summary": "Fork launched from Agent PBX TUI.",
                        "metadata": metadata,
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "last_used_at": 1.0,
                        "completed_at": None,
                        "edges": [],
                    }
                )
            raise AssertionError(path)

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        opened.append(agent_id)
        return True

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%bad" if len(launches) == 1 else "%fresh"

    def fake_quit_pane(target: str, **_: object) -> bool:
        quit_calls.append(target)
        return True

    capture_counts: dict[str, int] = {}

    def fake_capture_pane(target: str, **_: object) -> str:
        capture_counts[target] = capture_counts.get(target, 0) + 1
        if target == "%bad":
            if capture_counts[target] == 1:
                return ""
            return (
                '{"code":"invalid_encrypted_content","message":"Encrypted '
                'content item_id did not match the target item id."}'
            )
        return ""

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_ATTEMPTS", 2)
    monkeypatch.setattr("agent_pbx.tui.REVIEW_FORK_HEALTH_CHECK_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", lambda *args, **kwargs: [])
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target in {"%bad", "%fresh"})
    monkeypatch.setattr(tmux_support, "quit_pane", fake_quit_pane)
    monkeypatch.setattr(tmux_support, "capture_pane", fake_capture_pane)

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                },
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "source-session",
                    "codex_host_id": "local",
                },
            },
        }
        app.selected_agent_id = "operator-0"
        await app.start_review_operator_fork()

    assert len(launches) == 2
    assert capture_counts["%bad"] == 2
    assert quit_calls == ["%bad"]
    first_argv = shlex.split(str(launches[0]["command"]))
    second_argv = shlex.split(str(launches[1]["command"]))
    assert first_argv[1] == "fork"
    assert first_argv[-2] == "source-session"
    assert "fork" not in second_argv
    assert "source-session" not in second_argv
    assert second_argv[0] == "codex"
    assert second_argv[-1].startswith("Use Agent PBX as an operator agent.")
    assert "fresh Codex session" in second_argv[-1]
    assert "Startup readiness:" in second_argv[-1]
    assert 'status="waiting"' in second_argv[-1]
    assert launches[1]["env"]["AGENT_PBX_OPERATOR_FORK_LAUNCH_MODE"] == "fresh_context"
    assert posts and posts[0]["path"] == "/v1/operator/forks/ensure"
    ensure_body = posts[0]["json"]
    metadata = ensure_body["metadata"]  # type: ignore[index]
    assert metadata["review_launch_mode"] == "fresh_context"
    assert metadata["source_continuation_disabled_reason"] == "invalid_encrypted_content"
    assert ensure_body["tmux_pane_id"] == "%fresh"  # type: ignore[index]
    assert opened and "review-1" in opened[0]


async def test_tui_start_review_operator_fork_uses_prior_failed_source_disposition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller"
    source_cwd.mkdir()
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    opened: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    prior_failed_fork = {
        "operator_fork_id": "fork-review-1",
        "logical_operator_agent_id": "operator-0",
        "fork_agent_id": "operator-0-fork-caller-1-review-1-old",
        "source_caller_agent_id": "caller-1",
        "source_codex_session_id": "source-session",
        "fork_track_id": "review-1",
        "fork_purpose": "review",
        "access_mode": "review_readonly",
        "source_cwd": str(source_cwd),
        "work_root": str(tmp_path / ".agent-pbx-review" / "operator-0-caller-1-review-1"),
        "tmux_pane_id": "%old",
        "status": "completed",
        "metadata": {
            "agent_type": "operator",
            "operator_role": "fork",
            "logical_operator_id": "operator-0",
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": "source-session",
            "fork_track_id": "review-1",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "review_launch_mode": "fresh_context",
            "source_continuation_disabled_reason": "invalid_encrypted_content",
        },
    }

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/auth/check":
                return Response({"ok": True})
            if path == "/v1/operator/forks":
                return Response({"forks": [prior_failed_fork]})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            if path == "/v1/operator/forks/ensure":
                metadata = body["metadata"]  # type: ignore[index]
                return Response(
                    {
                        "operator_fork_id": "fork-review-2",
                        "logical_operator_agent_id": body["operator_agent_id"],  # type: ignore[index]
                        "fork_agent_id": body["fork_agent_id"],  # type: ignore[index]
                        "source_caller_agent_id": body["source_caller_agent_id"],  # type: ignore[index]
                        "source_codex_session_id": "source-session",
                        "fork_track_id": body["fork_track_id"],  # type: ignore[index]
                        "fork_purpose": body["fork_purpose"],  # type: ignore[index]
                        "access_mode": body["access_mode"],  # type: ignore[index]
                        "source_cwd": body["source_cwd"],  # type: ignore[index]
                        "work_root": body["work_root"],  # type: ignore[index]
                        "cwd": body["work_root"],  # type: ignore[index]
                        "tmux_pane_id": body["tmux_pane_id"],  # type: ignore[index]
                        "status": "running",
                        "summary": "Fork launched from Agent PBX TUI.",
                        "metadata": metadata,
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "last_used_at": 1.0,
                        "completed_at": None,
                        "edges": [],
                    }
                )
            raise AssertionError(path)

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_refresh_agents() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> bool:
        opened.append(agent_id)
        return True

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%fresh"

    def fail_capture_pane(*_: object, **__: object) -> str:
        raise AssertionError("fresh-context preflight should skip continuation health checks")

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_RETRY_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "list_panes", lambda *args, **kwargs: [])
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target == "%fresh")
    monkeypatch.setattr(tmux_support, "capture_pane", fail_capture_pane)

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "root",
                    "default_source_caller_agent_id": "caller-1",
                },
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "source-session",
                    "codex_host_id": "local",
                },
            },
        }
        app.selected_agent_id = "operator-0"
        await app.start_review_operator_fork()

    assert len(launches) == 1
    argv = shlex.split(str(launches[0]["command"]))
    assert argv[0] == "codex"
    assert "fork" not in argv
    assert "source-session" not in argv
    assert "fresh Codex session" in argv[-1]
    assert launches[0]["env"]["AGENT_PBX_OPERATOR_FORK_LAUNCH_MODE"] == "fresh_context"
    assert posts and posts[0]["path"] == "/v1/operator/forks/ensure"
    ensure_body = posts[0]["json"]
    assert ensure_body["fork_track_id"] == "review-2"  # type: ignore[index]
    metadata = ensure_body["metadata"]  # type: ignore[index]
    assert metadata["review_launch_mode"] == "fresh_context"
    assert metadata["source_continuation_disabled_reason"] == "invalid_encrypted_content"
    assert opened and "review-2" in opened[0]


@pytest.mark.asyncio
async def test_tui_rebinds_stale_live_operator_fork_without_launching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    work_root = tmp_path / ".agent-pbx-review" / "operator-0-caller-1-review-1"
    work_root.mkdir(parents=True)
    posts: list[dict[str, object]] = []
    fork_agent_id = "operator-0-fork-caller-1-review-1-oldhash"
    stale_fork = {
        "operator_fork_id": "fork-review-1",
        "logical_operator_agent_id": "operator-0",
        "fork_agent_id": fork_agent_id,
        "source_caller_agent_id": "caller-1",
        "source_codex_session_id": "session-caller-1",
        "fork_track_id": "review-1",
        "fork_purpose": "review",
        "access_mode": "review_readonly",
        "source_cwd": str(source_cwd),
        "work_root": str(work_root),
        "cwd": str(work_root),
        "codex_home": None,
        "codex_host_id": "local",
        "tmux_pane_id": "%42",
        "status": "running",
        "summary": None,
        "metadata": {
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": "session-caller-1",
            "fork_track_id": "review-1",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "source_cwd": str(source_cwd),
            "work_root": str(work_root),
            "tmux_pane_id": "%42",
        },
        "created_at": 1.0,
        "updated_at": 1.0,
        "last_used_at": 1.0,
        "completed_at": None,
        "edges": [],
    }

    class Response:
        def __init__(self, data: dict[str, object]) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/operator/forks":
                return Response({"forks": [stale_fork]})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            if path != "/v1/operator/forks/rebind-source-session":
                raise AssertionError(path)
            body = kwargs["json"]  # type: ignore[index]
            metadata = dict(stale_fork["metadata"])  # type: ignore[arg-type]
            metadata.update(body["metadata"])  # type: ignore[index]
            metadata["previous_source_codex_session_ids"] = ["session-caller-1"]
            rebound = {
                **stale_fork,
                "source_codex_session_id": body["new_source_codex_session_id"],  # type: ignore[index]
                "metadata": metadata,
            }
            return Response(rebound)

    async def fail_configure_operator_codex_mcp(**_: object) -> None:
        raise AssertionError("new fork launch should not configure MCP")

    async def fail_launch_restart_pane(**_: object) -> str:
        raise AssertionError("new fork launch should not create a pane")

    pane = tmux_support.TmuxPane(
        "agent-pbx-operators",
        "1",
        "0",
        "%42",
        True,
        "node",
        fork_agent_id,
        str(work_root),
        120,
        30,
        120,
        window_name=fork_agent_id,
    )
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fail_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.launch_restart_pane = fail_launch_restart_pane  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "list_panes", lambda *args, **kwargs: [pane])

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "session-caller-2",
                    "codex_host_id": "local",
                },
            },
        }
        fork = await app.ensure_operator_fork_from_tui(
            logical_operator_id="operator-0",
            source_caller_agent_id="caller-1",
            fork_track_id="review-1",
            fork_purpose="review",
            access_mode="review_readonly",
            work_root=str(work_root),
        )

    assert fork is not None
    assert fork["operator_fork_id"] == "fork-review-1"
    assert fork["fork_agent_id"] == fork_agent_id
    assert fork["source_codex_session_id"] == "session-caller-2"
    assert app.tmux_agent_targets[fork_agent_id] == "%42"
    assert app.tmux_direct_agent_modes[fork_agent_id] is True
    assert app.agents[fork_agent_id]["metadata"]["source_codex_session_id"] == (
        "session-caller-2"
    )
    assert [post["path"] for post in posts] == [
        "/v1/operator/forks/rebind-source-session"
    ]
    body = posts[0]["json"]
    assert body["old_source_codex_session_id"] == "session-caller-1"  # type: ignore[index]
    assert body["new_source_codex_session_id"] == "session-caller-2"  # type: ignore[index]
    assert body["operator_fork_id"] == "fork-review-1"  # type: ignore[index]


@pytest.mark.asyncio
async def test_tui_blocks_ambiguous_stale_operator_fork_rebind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    work_root = tmp_path / ".agent-pbx-review" / "operator-0-caller-1-review-1"
    work_root.mkdir(parents=True)
    fork_agent_ids = [
        "operator-0-fork-caller-1-review-1-oldhash-a",
        "operator-0-fork-caller-1-review-1-oldhash-b",
    ]
    stale_forks = [
        {
            "operator_fork_id": f"fork-review-{index}",
            "logical_operator_agent_id": "operator-0",
            "fork_agent_id": fork_agent_id,
            "source_caller_agent_id": "caller-1",
            "source_codex_session_id": f"session-caller-old-{index}",
            "fork_track_id": "review-1",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
            "source_cwd": str(source_cwd),
            "work_root": str(work_root),
            "cwd": str(work_root),
            "codex_home": None,
            "codex_host_id": "local",
            "tmux_pane_id": f"%4{index}",
            "status": "running",
            "summary": None,
            "metadata": {
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": f"session-caller-old-{index}",
                "fork_track_id": "review-1",
                "source_cwd": str(source_cwd),
                "work_root": str(work_root),
                "tmux_pane_id": f"%4{index}",
            },
            "created_at": 1.0,
            "updated_at": 1.0,
            "last_used_at": 1.0,
            "completed_at": None,
            "edges": [],
        }
        for index, fork_agent_id in enumerate(fork_agent_ids, start=1)
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"forks": stale_forks}

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/operator/forks":
                return Response()
            raise AssertionError(path)

        async def post(self, path: str, **_: object) -> Response:
            raise AssertionError(path)

    async def fail_configure_operator_codex_mcp(**_: object) -> None:
        raise AssertionError("ambiguous stale forks should block before launch")

    async def fail_launch_restart_pane(**_: object) -> str:
        raise AssertionError("ambiguous stale forks should block before launch")

    panes = [
        tmux_support.TmuxPane(
            "agent-pbx-operators",
            "1",
            str(index),
            f"%4{index}",
            index == 1,
            "node",
            fork_agent_id,
            str(work_root),
            120,
            30,
            120,
            window_name=fork_agent_id,
        )
        for index, fork_agent_id in enumerate(fork_agent_ids, start=1)
    ]
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fail_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.launch_restart_pane = fail_launch_restart_pane  # type: ignore[method-assign]
    monkeypatch.setattr(tmux_support, "list_panes", lambda *args, **kwargs: panes)

    async with app.run_test():
        app.agents = {
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {
                    "cwd": str(source_cwd),
                    "codex_session_id": "session-caller-2",
                    "codex_host_id": "local",
                },
            },
        }
        with pytest.raises(RuntimeError, match="multiple stale review-1 forks"):
            await app.ensure_operator_fork_from_tui(
                logical_operator_id="operator-0",
                source_caller_agent_id="caller-1",
                fork_track_id="review-1",
                fork_purpose="review",
                access_mode="review_readonly",
                work_root=str(work_root),
            )


async def test_tui_launch_pending_project_spawn_starts_registered_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_cwd = tmp_path / "caller-1"
    source_cwd.mkdir()
    target_path = tmp_path / "next-demo"
    request = {
        "spawn_request_id": "spawn-1",
        "logical_operator_agent_id": "operator-0",
        "operator_agent_id": "operator-0",
        "review_fork_id": "fork-review-1",
        "review_fork_agent_id": "operator-0-fork-caller-1-review-1",
        "source_caller_agent_id": "caller-1",
        "source_cwd": str(source_cwd),
        "target_parent": str(tmp_path),
        "target_slug": "next-demo",
        "target_path": str(target_path),
        "project_name": "Next Demo",
        "mode": "empty",
        "instructions": "Build a small sibling project.",
        "status": "pending",
        "metadata": {},
        "created_at": 1.0,
        "updated_at": 1.0,
        "completed_at": None,
    }
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    app.tmux_features_available = True
    launches: list[dict[str, object]] = []
    posts: list[dict[str, object]] = []
    opened: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, data: object) -> None:
            self.data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.data

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/auth/check":
                return Response({"ok": True})
            if path == "/v1/operator/project-spawns":
                return Response({"project_spawns": [dict(request)]})
            raise AssertionError(path)

        async def post(self, path: str, **kwargs: object) -> Response:
            posts.append({"path": path, **kwargs})
            body = kwargs["json"]  # type: ignore[index]
            if path == "/v1/agents/register":
                return Response(
                    {
                        "agent_id": body["agent_id"],  # type: ignore[index]
                        "agent_type": "caller",
                        "project": body["project"],  # type: ignore[index]
                        "name": body.get("name"),  # type: ignore[union-attr]
                        "status": "online",
                        "effective_status": "online",
                        "pbx_active": True,
                        "metadata": body["metadata"],  # type: ignore[index]
                        "created_at": 1.0,
                        "last_seen_at": 1.0,
                    }
                )
            if path == "/v1/operator/project-spawns/spawn-1/status":
                updated = dict(request)
                updated.update(body)  # type: ignore[arg-type]
                if body["status"] == "launched":  # type: ignore[index]
                    updated["completed_at"] = 2.0
                return Response(updated)
            raise AssertionError(path)

    async def fake_configure_operator_codex_mcp(**_: object) -> None:
        return None

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    async def fake_open_latest_for_agent(agent_id: str) -> None:
        opened.append(agent_id)

    def fake_launch_pane(**kwargs: object) -> str:
        launches.append(kwargs)
        return "%77"

    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]
    app.configure_operator_codex_mcp = fake_configure_operator_codex_mcp  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.open_latest_for_agent = fake_open_latest_for_agent  # type: ignore[method-assign]
    app.save_settings = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr("agent_pbx.tui.CODEX_RESTART_STABILIZE_SECONDS", 0.0)
    monkeypatch.setattr(tmux_support, "launch_pane", fake_launch_pane)
    monkeypatch.setattr(tmux_support, "pane_exists", lambda target: target == "%77")

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        await app.launch_pending_project_spawn_request()

    assert (target_path / ".git").is_dir()
    assert len(launches) == 1
    launch = launches[0]
    assert launch["session_name"] == app.caller_tmux_session_name()
    assert launch["window_name"] == "codex-next-demo"
    assert launch["cwd"] == str(target_path)
    argv = shlex.split(str(launch["command"]))
    assert argv[0] == "codex"
    assert "--cd" in argv
    assert str(target_path) in argv
    assert "-c" in argv
    assert "Build a small sibling project." in argv[-1]
    env = launch["env"]
    assert isinstance(env, dict)
    assert env["AGENT_PBX_AGENT_ID"] == "codex-next-demo"
    assert env["AGENT_PBX_AGENT_TYPE"] == "caller"
    assert env["AGENT_PBX_AGENT_PROJECT"] == "next-demo"
    assert app.tmux_agent_targets["codex-next-demo"] == "%77"
    assert app.tmux_direct_agent_modes["codex-next-demo"] is True
    assert opened == ["codex-next-demo"]
    assert [post["path"] for post in posts] == [
        "/v1/operator/project-spawns/spawn-1/status",
        "/v1/agents/register",
        "/v1/operator/project-spawns/spawn-1/status",
    ]
    assert posts[0]["json"]["status"] == "launching"  # type: ignore[index]
    assert posts[-1]["json"]["status"] == "launched"  # type: ignore[index]
    assert posts[-1]["json"]["launched_agent_id"] == "codex-next-demo"  # type: ignore[index]


async def test_tui_launch_pending_operator_handoff_fork_retries_approval() -> None:
    app = AgentPBXTUI(
        server="http://127.0.0.1:8765",
        token="secret",
        tmux_direct=True,
    )
    handoff = {
        "handoff_id": "handoff-1",
        "logical_operator_agent_id": "operator-0",
        "target_operator_agent_id": "operator-B",
        "target_caller_agent_id": "caller-1",
        "status": "pending_launch",
        "updated_at": 2.0,
    }
    launches: list[tuple[str, str]] = []
    approvals: list[tuple[str, str]] = []

    async def fake_fetch_operator_handoffs(
        logical_operator_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        assert logical_operator_id == "operator-0"
        assert status == "pending_launch"
        assert limit == 50
        return [handoff]

    async def fake_ensure_operator_fork_from_tui(
        *,
        logical_operator_id: str,
        source_caller_agent_id: str,
        fork_track_id: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        work_root: str | None = None,
    ) -> dict[str, object]:
        _ = (fork_track_id, fork_purpose, access_mode, work_root)
        launches.append((logical_operator_id, source_caller_agent_id))
        return {
            "operator_fork_id": "fork-1",
            "fork_agent_id": "operator-B-fork-caller-1",
        }

    async def fake_approve_operator_handoff(
        logical_operator_id: str,
        handoff_id: str,
        *,
        delivery: str = "auto",
    ) -> dict[str, object]:
        _ = delivery
        approvals.append((logical_operator_id, handoff_id))
        return {
            "handoff": {
                "handoff_id": handoff_id,
                "status": "sent",
                "command_id": "cmd-1",
            },
            "command": {"command_id": "cmd-1"},
        }

    async def fake_refresh_agents() -> None:
        return None

    async def fake_refresh_events() -> None:
        return None

    app.fetch_operator_handoffs = fake_fetch_operator_handoffs  # type: ignore[method-assign]
    app.ensure_operator_fork_from_tui = fake_ensure_operator_fork_from_tui  # type: ignore[method-assign]
    app.approve_operator_handoff = fake_approve_operator_handoff  # type: ignore[method-assign]
    app.refresh_agents = fake_refresh_agents  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            },
            "caller-1": {
                "agent_id": "caller-1",
                "agent_type": "caller",
                "project": "demo",
                "metadata": {"cwd": "/tmp/demo", "codex_session_id": "session-1"},
            },
        }
        app.selected_agent_id = "operator-0"
        await app.launch_pending_operator_handoff_fork()

    assert launches == [("operator-B", "caller-1")]
    assert approvals == [("operator-0", "handoff-1")]


async def test_tui_preflight_pending_operator_handoff_formats_result() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    fetched: list[tuple[str, str | None]] = []
    preflights: list[tuple[str, str, str]] = []

    async def fake_fetch_operator_handoffs(
        logical_operator_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        _ = limit
        fetched.append((logical_operator_id, status))
        return [
            {
                "handoff_id": "handoff-1",
                "logical_operator_agent_id": "operator-0",
                "target_operator_agent_id": "operator-B",
                "status": "proposed",
            }
        ]

    async def fake_preflight_operator_handoff(
        logical_operator_id: str,
        handoff_id: str,
        delivery: str = "auto",
    ) -> dict[str, object]:
        preflights.append((logical_operator_id, handoff_id, delivery))
        return {
            "handoff_id": handoff_id,
            "status": "ready",
            "ok": True,
            "delivery_possible": True,
            "requested_delivery": "auto",
            "resolved_delivery": "queue",
            "checked_at": 123.0,
            "target": {"agent_id": "operator-B", "pbx_mode": "nohup"},
            "target_fork": None,
            "pane": None,
            "warnings": [],
            "kb_context": {"satisfied_by_kb": True, "match_count": 2},
        }

    app.fetch_operator_handoffs = fake_fetch_operator_handoffs  # type: ignore[method-assign]
    app.preflight_operator_handoff = fake_preflight_operator_handoff  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        await app.preflight_pending_operator_handoff()
        detail = app.query_one("#detail", TextArea)

    assert fetched == [("operator-0", None)]
    assert preflights == [("operator-0", "handoff-1", "auto")]
    assert "Handoff: handoff-1" in detail.text
    assert "Resolved delivery: queue" in detail.text
    assert "Checked: 123.0" in detail.text
    assert "matches: 2" in detail.text


@pytest.mark.asyncio
async def test_tui_approve_pending_operator_handoff_preflights_first() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    order: list[str] = []
    loaded_threads: list[str] = []

    async def fake_fetch_operator_handoffs(
        logical_operator_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        _ = (status, limit)
        assert logical_operator_id == "operator-0"
        order.append("fetch")
        return [
            {
                "handoff_id": "handoff-1",
                "logical_operator_agent_id": "operator-0",
                "target_operator_agent_id": "operator-B",
                "status": "proposed",
            }
        ]

    async def fake_preflight_operator_handoff(
        logical_operator_id: str,
        handoff_id: str,
        delivery: str = "auto",
    ) -> dict[str, object]:
        assert (logical_operator_id, handoff_id, delivery) == (
            "operator-0",
            "handoff-1",
            "auto",
        )
        order.append("preflight")
        return {
            "handoff_id": handoff_id,
            "status": "ready",
            "ok": True,
            "delivery_possible": True,
            "requested_delivery": "auto",
            "resolved_delivery": "queue",
            "checked_at": 123.0,
            "target": {"agent_id": "operator-B", "pbx_mode": "nohup"},
            "target_fork": None,
            "pane": None,
            "warnings": [],
        }

    async def fake_approve_operator_handoff(
        logical_operator_id: str,
        handoff_id: str,
        *,
        delivery: str = "auto",
    ) -> dict[str, object]:
        assert order[-1] == "preflight"
        assert (logical_operator_id, handoff_id, delivery) == (
            "operator-0",
            "handoff-1",
            "auto",
        )
        order.append("approve")
        return {
            "handoff": {
                "handoff_id": handoff_id,
                "status": "sent",
                "target_operator_agent_id": "operator-B",
                "target_caller_agent_id": "-",
                "target_operator_fork_id": "-",
                "delivery_status": "sent",
            },
            "command": {"command_id": "cmd-1"},
        }

    async def fake_refresh_events() -> None:
        order.append("refresh")

    async def fake_load_thread(agent_id: str) -> None:
        loaded_threads.append(agent_id)

    app.fetch_operator_handoffs = fake_fetch_operator_handoffs  # type: ignore[method-assign]
    app.preflight_operator_handoff = fake_preflight_operator_handoff  # type: ignore[method-assign]
    app.approve_operator_handoff = fake_approve_operator_handoff  # type: ignore[method-assign]
    app.refresh_events = fake_refresh_events  # type: ignore[method-assign]
    app.load_thread = fake_load_thread  # type: ignore[method-assign]

    async with app.run_test():
        app.agents = {
            "operator-0": {
                "agent_id": "operator-0",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {"agent_type": "operator", "operator_role": "root"},
            }
        }
        app.selected_agent_id = "operator-0"
        order.clear()
        await app.approve_pending_operator_handoff()
        detail = app.query_one("#detail", TextArea)

    assert order == ["fetch", "preflight", "approve", "refresh"]
    assert loaded_threads == ["operator-B"]
    assert "Preflight: ready queue" in detail.text
    assert "Preflight warnings: 0" in detail.text


def test_tui_operator_handoff_monitor_renders_pending_ack(monkeypatch) -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    monkeypatch.setattr("agent_pbx.tui.time.time", lambda: 170.0)

    text = app.format_operator_handoffs(
        "operator-0",
        [
            {
                "handoff_id": "handoff-1",
                "status": "sent",
                "safe_to_start": True,
                "source_agent_id": "operator-0",
                "target_operator_agent_id": "operator-B",
                "needs_ack": True,
                "updated_at": 100.0,
                "metadata": {
                    "delivery_preflight": {
                        "status": "ready",
                        "resolved_delivery": "queue",
                        "checked_at": 120.0,
                        "warnings": ["target has no tmux pane"],
                    }
                },
                "delivery_evidence": {
                    "agent_acknowledged": False,
                    "agent_started": False,
                },
            }
        ],
    )

    assert "preflight: ready/queue age=50s ago warnings=1" in text
    assert "monitor: late; awaiting ack 70s" in text


def codex_config_tui_payload() -> dict[str, object]:
    return {
        "path": "/home/test/.codex/config.toml",
        "exists": True,
        "mtime": 123.0,
        "size_bytes": 42,
        "parse_error": None,
        "fields": [
            {
                "key": "model",
                "label": "Default model",
                "description": "Default model slug.",
                "configured": True,
                "value": "gpt-5.6-sol",
                "value_preview": "gpt-5.6-sol",
                "editable": True,
                "sensitive": False,
            },
            {
                "key": "approval_policy",
                "label": "Approval policy",
                "description": "Default shell approval policy.",
                "configured": False,
                "value": None,
                "value_preview": "",
                "editable": True,
                "sensitive": False,
            },
        ],
        "mcp_servers": [
            {
                "name": "agent-pbx",
                "url": "http://127.0.0.1:8767/mcp",
                "bearer_token_env_var": "AGENT_PBX_TOKEN",
                "http_headers": {},
                "env_keys": [],
            }
        ],
        "hidden_items": [
            {
                "key": "model_providers",
                "label": "model_providers",
                "policy": "read_only",
                "configured": True,
                "value_preview": "configured",
                "reason": "Provider tables stay hidden.",
            }
        ],
        "requirements": [],
        "warnings": ["Configured Codex model is unknown."],
        "editable_paths": ["approval_policy", "model"],
        "secret_update_paths": ["mcp_servers.<server>.http_headers.<header>"],
    }


async def test_tui_loads_and_renders_codex_config(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/codex/config":
                return Response(codex_config_tui_payload())
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            return Response({})

    monkeypatch.setattr(
        "agent_pbx.tui.inspect_codex_posture",
        lambda: CodexCliPosture("", "", "", "", "", 0, None, ()),
    )
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")
    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#codex-config-fields", DataTable)
        assert table.row_count >= 3
        app.select_codex_config_field("model")
        assert app.query_one("#codex-config-key", Input).value == "model"
        assert app.query_one("#codex-config-value", Input).value == "gpt-5.6-sol"
        detail = app.query_one("#codex-config-detail", TextArea).text
        assert "Default model" in detail
        assert "Provider tables stay hidden" not in detail


async def test_tui_codex_config_save_wire_and_secret_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.payload

    class Client:
        async def get(self, path: str, **_: object) -> Response:
            if path == "/v1/codex/config":
                return Response(codex_config_tui_payload())
            if path == "/v1/agents":
                return Response([])
            if path == "/v1/events":
                return Response([])
            if path == "/v1/joplin/status":
                return Response({"configured": False, "available": False})
            return Response({})

        async def patch(self, path: str, **kwargs: object) -> Response:
            calls.append({"path": path, **kwargs})
            return Response(
                {
                    "ok": True,
                    "changed_paths": ["model"],
                    "backup_path": None,
                    "config": codex_config_tui_payload(),
                }
            )

    monkeypatch.setattr(
        "agent_pbx.tui.inspect_codex_posture",
        lambda: CodexCliPosture("", "", "", "", "", 0, None, ()),
    )
    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")
    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#codex-config-key", Input).value = "model"
        app.query_one("#codex-config-value", Input).value = "gpt-5.6-sol"
        await app.save_codex_config_field()
        await app.wire_agent_pbx_mcp_config()
        app.query_one("#codex-secret-path", Input).value = (
            "mcp_servers.workerbee.http_headers.Authorization"
        )
        secret_input = app.query_one("#codex-secret-value", Input)
        secret_input.value = "Bearer hidden"
        await app.replace_codex_secret(remove=False)
        assert secret_input.value == ""

    assert calls[0]["path"] == "/v1/codex/config"
    assert calls[0]["json"] == {"updates": {"model": "gpt-5.6-sol"}}
    assert calls[1]["json"] == {
        "agent_pbx_mcp": {
            "url": "http://127.0.0.1:8765/mcp",
            "bearer_token_env_var": "AGENT_PBX_TOKEN",
            "default_tools_approval_mode": "approve",
        }
    }
    assert calls[2]["json"] == {
        "secret_updates": [
            {
                "path": "mcp_servers.workerbee.http_headers.Authorization",
                "value": "Bearer hidden",
            }
        ]
    }


async def test_tui_applies_portable_codex_keymap_preset() -> None:
    calls: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "config": {
                    "fields": [],
                    "hidden_items": [],
                    "mcp_servers": [],
                    "requirements": [],
                    "warnings": [],
                    "keymap": {"preset": "portable"},
                },
            }

    class Client:
        async def patch(self, path: str, **kwargs: object) -> Response:
            calls.append({"path": path, **kwargs})
            return Response()

    app = AgentPBXTUI(server="http://127.0.0.1:8765", token="test")
    app.api_client = lambda: Client()  # type: ignore[assignment,method-assign]

    async with app.run_test():
        applied = await app.apply_codex_keymap_preset("portable")

    assert applied is True
    assert calls[0]["path"] == "/v1/codex/config"
    assert calls[0]["json"] == {"keymap_preset": "portable"}


async def test_tui_opens_native_codex_keymap_in_selected_tmux_session() -> None:
    sent: list[tuple[str, str]] = []
    app = AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    app.selected_agent_id = "operator-0"
    app.tmux_direct_agent_modes["operator-0"] = True

    async def fake_send(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    app.send_text_to_tmux = fake_send  # type: ignore[method-assign]

    opened = await app.open_native_codex_keymap()

    assert opened is True
    assert sent == [("operator-0", "/keymap")]
