"""Settings modal presentation for the Agent PBX TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Select, Static

from ..preferences import (
    ACCESSIBLE_TUI_THEME,
    DEFAULT_TUI_THEME,
    EMBEDDED_TERMINAL_SCROLL_MODE_CHOICES,
    JOPLIN_COPY_MODE_CHOICES,
    LAYOUT_CHOICES,
    MINIMAL_TUI_THEME,
    SPLIT_PERCENT_STEP,
    TMUX_POPOUT_MODE_CHOICES,
    TMUX_RUNTIME_SERVER_MODE_CHOICES,
    normalize_embedded_scroll_mode,
    normalize_joplin_copy_mode,
    normalize_runtime_server_mode,
)


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
        joplin_copy_mode: str,
        tmux_direct_enabled: bool,
        tmux_features_available: bool,
        tmux_runtime_server_mode: str,
        embedded_terminal_enabled: bool,
        embedded_terminal_scroll_mode: str,
        tmux_popout_mode: str,
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
        self.joplin_copy_mode = normalize_joplin_copy_mode(joplin_copy_mode)
        self.tmux_direct_enabled = tmux_direct_enabled
        self.tmux_features_available = tmux_features_available
        self.tmux_runtime_server_mode = normalize_runtime_server_mode(
            tmux_runtime_server_mode
        ).value
        self.embedded_terminal_enabled = embedded_terminal_enabled
        self.embedded_terminal_scroll_mode = normalize_embedded_scroll_mode(
            embedded_terminal_scroll_mode
        )
        self.tmux_popout_mode = tmux_popout_mode
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
                yield Static("Joplin response copy", id="joplin-copy-mode-label")
                yield Select(
                    JOPLIN_COPY_MODE_CHOICES,
                    value=self.joplin_copy_mode,
                    allow_blank=False,
                    id="joplin-copy-mode",
                )
                tmux_direct = Checkbox(
                    "Tmux direct default",
                    value=self.tmux_direct_enabled,
                    id="tmux-direct",
                )
                tmux_direct.disabled = not self.tmux_features_available
                yield tmux_direct
                yield Static("Runtime tmux server", id="tmux-runtime-mode-label")
                yield Select(
                    TMUX_RUNTIME_SERVER_MODE_CHOICES,
                    value=self.tmux_runtime_server_mode,
                    allow_blank=False,
                    id="tmux-runtime-mode",
                )
                embedded_terminal = Checkbox(
                    "Embedded native tmux terminal (v2)",
                    value=self.embedded_terminal_enabled,
                    id="embedded-terminal-v2",
                )
                embedded_terminal.disabled = not self.tmux_features_available
                yield embedded_terminal
                yield Static(
                    "Embedded wheel/swipe behavior",
                    id="embedded-terminal-scroll-mode-label",
                )
                embedded_scroll_mode = Select(
                    EMBEDDED_TERMINAL_SCROLL_MODE_CHOICES,
                    value=self.embedded_terminal_scroll_mode,
                    allow_blank=False,
                    id="embedded-terminal-scroll-mode",
                )
                embedded_scroll_mode.disabled = not self.tmux_features_available
                yield embedded_scroll_mode
                yield Static("Tmux pop-out behavior", id="tmux-popout-mode-label")
                yield Select(
                    TMUX_POPOUT_MODE_CHOICES,
                    value=self.tmux_popout_mode,
                    allow_blank=False,
                    id="tmux-popout-mode",
                )
                yield Static("Theme", id="theme-label")
                yield Select(
                    [
                        ("Cyberpunk", DEFAULT_TUI_THEME),
                        ("Cyberpunk Accessible", ACCESSIBLE_TUI_THEME),
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
        elif event.select.id == "joplin-copy-mode":
            event.stop()
            self.app.set_joplin_copy_mode(str(event.value))  # type: ignore[attr-defined]
        elif event.select.id == "tmux-runtime-mode":
            event.stop()
            self.app.set_tmux_runtime_server_mode(str(event.value))  # type: ignore[attr-defined]
        elif event.select.id == "embedded-terminal-scroll-mode":
            event.stop()
            self.app.set_embedded_terminal_scroll_mode(str(event.value))  # type: ignore[attr-defined]
        elif event.select.id == "tmux-popout-mode":
            event.stop()
            self.app.set_tmux_popout_mode(str(event.value))  # type: ignore[attr-defined]


__all__ = ["SettingsScreen"]
