# TUI and Keymap

> **Applies to:** Agent PBX 2.1.x  
> **Last reviewed:** 2026-10-10

The v2 TUI retains the v1 left/right composition. Agents, Operators, and Events
occupy the left side. The right side retains its top tab row, including Latest,
Thread, Files, Editor, Codex, WorkerBee, PRs, Issues, Campaigns, KB, and Joplin
when their capabilities are enabled. `Latest` embeds the selected runtime's
normal tmux client and Codex renderer.

## Global navigation

Plain function keys remain PBX-owned even while the terminal has focus:

| Key | Agent PBX action |
|---|---|
| F1 | Focus Agents |
| F2 | Focus Events |
| F3 | Focus the right-side tab row |
| F4 | Focus Latest input/composer |
| F5 | Focus Operators |
| F6 / F7 | Previous / next Operator fork |
| F8 | Focus and acknowledge the next alert |
| F9 | Toggle Editor full-screen |
| F10 | Launch a workspace from approved project roots |
| F11 / F12 | Navigate backward / forward through Agent, Operator, and right-tab destinations |

Shift+F1 through Shift+F12 sends the corresponding unmodified function key to
Codex. Terminals that encode those combinations as F13 through F24 are also
recognized. Therefore F2 always means PBX Events and Shift+F2 opens Codex
warnings.

Navigation history is per TUI client and is not persisted. It records stable
entity and tab identities rather than table row numbers. Hidden, deleted, or
unavailable destinations are skipped safely. `/navigation back` and
`/navigation forward` provide the palette equivalents.

## Terminal keys

While the embedded terminal has focus:

- ordinary printable input, Escape, and Ctrl+C go to Codex;
- Ctrl+P opens the Agent PBX command palette;
- Enter submits and Ctrl+J inserts a newline under the portable keymap;
- Shift+Enter, Alt+Enter, and Ctrl+Enter translate to Ctrl+J;
- Page Up enters runtime tmux copy mode; arrows and Page Up/Page Down navigate;
- `/tmux scrollback` is the explicit copy-mode fallback;
- Ctrl+T toggles the selected entity's native terminal surface.

Use `/pbx keymap` for the effective ownership map and `/codex keymap` for
Codex's native viewer. `/codex keymap portable` writes the portable newline
mapping; running Codex sessions need a safe restart/resume before a changed
Codex keymap takes effect.

## Command palette history

Ctrl+P opens the palette. Up and Down traverse only command names previously
chosen from the palette; prompt bodies and command arguments are never stored.
History is bounded, deduplicated, and memory-only by default.

- `/palette history` inspects the current entries.
- `/palette history clear` removes them.
- `/palette history persistence on` opts into private local persistence.
- `/palette history persistence off` removes persisted entries and returns to
  memory-only behavior.

Sensitive command classes are excluded even when persistence is enabled.

## Focus and refresh behavior

Async refreshes use generation checks. A completed background task may restore
focus only if the user has not moved since it began. Agent and Operator order
also remains stable during active list interaction, preventing cursor jumps.
The Joplin reader keeps per-note scroll positions and the native terminal avoids
painting unchanged frames.

Use `[` and `]` to change the split width and `0` to reset it. Adaptive,
split, compact, and tiny layouts are available; compact modes retain keyboard
access to the same right-side tabs.

