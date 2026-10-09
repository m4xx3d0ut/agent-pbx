from __future__ import annotations

import os
from typing import Mapping

from ..contracts import CodexRuntimeState


# Preserve the original TUI palette under the long-standing ``cyberpunk``
# selector. Existing workstation and SSH users rely on these exact RGB values,
# so changing them in place is a compatibility break even when the replacement
# palette is more accessible.
CYBERPUNK_PALETTE: dict[str, str] = {
    "primary": "#00e5ff",
    "secondary": "#9b5cff",
    "warning": "#fcee09",
    "error": "#ff2e88",
    "success": "#38ff9c",
    "accent": "#ff3df2",
    "foreground": "#f2f7ff",
    "muted": "#697386",
    "background": "#070b16",
    "surface": "#101826",
    "panel": "#1a102a",
    "boost": "#2b174b",
    "executing": "#3b82f6",
}

# Accessible semantic source for PBX runtime status, tmux surfaces, generated
# Codex syntax themes, and the opt-in ``cyberpunk-accessible`` TUI theme. States
# always retain a symbol and text label so color is never the only signal. This
# palette avoids red/green adjacency and remains legible under common
# deuteranopia simulations.
PBX_PALETTE: dict[str, str] = {
    "primary": "#00d7ff",
    "secondary": "#af87ff",
    "warning": "#ffd75f",
    "error": "#ff5f87",
    "success": "#5fffff",
    "accent": "#ff5fd7",
    "foreground": "#e6edf7",
    "muted": "#8492a6",
    "background": "#070b16",
    "surface": "#101826",
    "panel": "#1a102a",
    "boost": "#2b174b",
    "executing": "#5fafff",
}

STATE_COLOR_KEYS: dict[CodexRuntimeState, str] = {
    CodexRuntimeState.STARTING: "secondary",
    CodexRuntimeState.READY: "primary",
    CodexRuntimeState.THINKING: "secondary",
    CodexRuntimeState.DELEGATING: "accent",
    CodexRuntimeState.EXECUTING: "executing",
    CodexRuntimeState.WAITING_TOOL: "executing",
    CodexRuntimeState.WAITING_USER: "warning",
    CodexRuntimeState.COMPACTING: "secondary",
    CodexRuntimeState.REVIEWING: "accent",
    CodexRuntimeState.COMPLETE: "success",
    CodexRuntimeState.INTERRUPTED: "warning",
    CodexRuntimeState.ERROR: "error",
    CodexRuntimeState.UNKNOWN: "muted",
}

ANSI16_BY_KEY = {
    "primary": "bright_cyan",
    "secondary": "bright_magenta",
    "warning": "bright_yellow",
    "error": "bright_red",
    "success": "cyan",
    "accent": "magenta",
    "foreground": "bright_white",
    "muted": "bright_black",
    "executing": "bright_blue",
}

ANSI256_BY_KEY = {
    "primary": "color(45)",
    "secondary": "color(141)",
    "warning": "color(221)",
    "error": "color(204)",
    "success": "color(87)",
    "accent": "color(206)",
    "foreground": "color(255)",
    "muted": "color(103)",
    "executing": "color(75)",
}


def runtime_color(state: CodexRuntimeState, color_depth: int = 24) -> str:
    key = STATE_COLOR_KEYS.get(state, "muted")
    if color_depth <= 4:
        return ANSI16_BY_KEY[key]
    if color_depth <= 8:
        return ANSI256_BY_KEY[key]
    return PBX_PALETTE[key]


def terminal_color_depth(environment: Mapping[str, str] | None = None) -> int:
    env = environment or os.environ
    color_term = str(env.get("COLORTERM") or "").lower()
    term = str(env.get("TERM") or "").lower()
    if "truecolor" in color_term or "24bit" in color_term:
        return 24
    if "256color" in term:
        return 8
    return 4


def textual_palette(
    palette: Mapping[str, str] = CYBERPUNK_PALETTE,
) -> dict[str, str]:
    return {
        key: palette[key]
        for key in (
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
    }


def tmux_theme_options(palette: Mapping[str, str] = PBX_PALETTE) -> tuple[str, ...]:
    """Return declarative tmux options; callers decide when to apply them."""

    return (
        f"status-style fg={palette['foreground']},bg={palette['background']}",
        f"pane-border-style fg={palette['muted']}",
        f"pane-active-border-style fg={palette['primary']}",
        f"message-style fg={palette['background']},bg={palette['warning']}",
        f"mode-style fg={palette['background']},bg={palette['primary']}",
    )


def codex_syntax_theme_xml(palette: Mapping[str, str] = PBX_PALETTE) -> str:
    """Generate a compact TextMate theme accepted by Codex syntax themes."""

    settings = (
        ("Comment", "comment", palette["muted"]),
        ("String", "string", palette["success"]),
        ("Number", "constant.numeric", palette["warning"]),
        ("Keyword", "keyword", palette["secondary"]),
        ("Function", "entity.name.function", palette["executing"]),
        ("Type", "entity.name.type, support.type", palette["primary"]),
        ("Constant", "constant, variable.other.constant", palette["accent"]),
        ("Invalid", "invalid", palette["error"]),
    )
    items = "\n".join(
        """    <dict>
      <key>name</key><string>{name}</string>
      <key>scope</key><string>{scope}</string>
      <key>settings</key><dict><key>foreground</key><string>{color}</string></dict>
    </dict>""".format(name=name, scope=scope, color=color)
        for name, scope, color in settings
    )
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict>
  <key>name</key><string>Agent PBX 1337 Accessible</string>
  <key>settings</key><array>
    <dict><key>settings</key><dict>
      <key>background</key><string>{palette['background']}</string>
      <key>foreground</key><string>{palette['foreground']}</string>
    </dict></dict>
{items}
  </array>
</dict></plist>
"""
