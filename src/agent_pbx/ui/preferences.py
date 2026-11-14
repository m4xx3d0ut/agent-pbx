"""Cycle-free presentation preferences shared by the TUI and settings screen."""

from __future__ import annotations

from ..runtime_tmux import RuntimeServerMode, normalize_runtime_server_mode
from ..terminal import (
    EMBEDDED_SCROLL_MODE_CHILD,
    EMBEDDED_SCROLL_MODE_TMUX,
    normalize_embedded_scroll_mode,
)


DEFAULT_TUI_THEME = "cyberpunk"
ACCESSIBLE_TUI_THEME = "cyberpunk-accessible"
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

DEFAULT_SPLIT_PERCENT = 42
MIN_SPLIT_PERCENT = 25
MAX_SPLIT_PERCENT = 75
SPLIT_PERCENT_STEP = 5

TMUX_POPOUT_MODE_CHOICES = (
    ("Automatic for runtime server", "auto"),
    ("Switch recorded outer client", "switch_client"),
    ("Suspend TUI and attach", "suspend_attach"),
)
TMUX_RUNTIME_SERVER_MODE_CHOICES = (
    ("Dedicated PBX server", RuntimeServerMode.DEDICATED.value),
    ("Outer server when present", RuntimeServerMode.OUTER_IF_PRESENT.value),
    ("Require outer server", RuntimeServerMode.OUTER_REQUIRED.value),
)
EMBEDDED_TERMINAL_SCROLL_MODE_CHOICES = (
    ("Tmux history (wheel/swipe)", EMBEDDED_SCROLL_MODE_TMUX),
    ("Child application mouse input", EMBEDDED_SCROLL_MODE_CHILD),
)

JOPLIN_COPY_MODE_COPY_FIRST = "copy_first"
JOPLIN_COPY_MODE_TRANSCRIPT_FIRST = "transcript_first"
JOPLIN_COPY_MODE_TMUX_CAPTURE = "tmux_capture"
DEFAULT_JOPLIN_COPY_MODE = JOPLIN_COPY_MODE_COPY_FIRST
JOPLIN_COPY_MODE_CHOICES = (
    ("Fast: /copy → transcript → tmux", JOPLIN_COPY_MODE_COPY_FIRST),
    ("Clean: transcript first", JOPLIN_COPY_MODE_TRANSCRIPT_FIRST),
    ("Visible tmux capture only", JOPLIN_COPY_MODE_TMUX_CAPTURE),
)


def normalize_joplin_copy_mode(value: object) -> str:
    mode = str(value or "").strip().casefold().replace("-", "_")
    if mode in {
        JOPLIN_COPY_MODE_COPY_FIRST,
        "copy",
        "clipboard",
        "clipboard_first",
        "legacy",
        "legacy_clipboard",
        "fast",
    }:
        return JOPLIN_COPY_MODE_COPY_FIRST
    if mode in {
        JOPLIN_COPY_MODE_TRANSCRIPT_FIRST,
        "transcript",
        "export",
        "clean",
    }:
        return JOPLIN_COPY_MODE_TRANSCRIPT_FIRST
    if mode in {
        JOPLIN_COPY_MODE_TMUX_CAPTURE,
        "tmux",
        "capture",
        "visible",
    }:
        return JOPLIN_COPY_MODE_TMUX_CAPTURE
    return DEFAULT_JOPLIN_COPY_MODE


__all__ = [
    "ACCESSIBLE_TUI_THEME",
    "ADAPTIVE_TUI_LAYOUT",
    "COMPACT_TUI_LAYOUT",
    "DEFAULT_CUSTOM_THEME_NAME",
    "DEFAULT_JOPLIN_COPY_MODE",
    "DEFAULT_SPLIT_PERCENT",
    "DEFAULT_TUI_LAYOUT",
    "DEFAULT_TUI_THEME",
    "EMBEDDED_TERMINAL_SCROLL_MODE_CHOICES",
    "JOPLIN_COPY_MODE_CHOICES",
    "JOPLIN_COPY_MODE_COPY_FIRST",
    "JOPLIN_COPY_MODE_TMUX_CAPTURE",
    "JOPLIN_COPY_MODE_TRANSCRIPT_FIRST",
    "LAYOUT_CHOICES",
    "MAX_SPLIT_PERCENT",
    "MINIMAL_TUI_THEME",
    "MIN_SPLIT_PERCENT",
    "SPLIT_PERCENT_STEP",
    "SPLIT_TUI_LAYOUT",
    "THEME_1337_NAME",
    "TINY_TUI_LAYOUT",
    "TMUX_POPOUT_MODE_CHOICES",
    "TMUX_RUNTIME_SERVER_MODE_CHOICES",
    "normalize_embedded_scroll_mode",
    "normalize_joplin_copy_mode",
    "normalize_runtime_server_mode",
]
