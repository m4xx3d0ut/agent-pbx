# TUI and Keymap

> **Applies to:** Agent PBX 2.2.x
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

After F3 focuses the right-side tab row, Shift+F1 through Shift+F11 selects
Latest, Codex, Thread, Files, Editor, WorkerBee, PRs, Issues, Campaigns, KB, or
Joplin in that semantic order. The same focused row accepts the portable
fallback `1` through `9`, `0`, and `-`. These keys are scoped to the tab row;
inside Codex or a managed editor they retain their child-terminal meaning.

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

## Managed terminal editor

The Editor tab supports its Textual buffer and a managed native project
workspace. Files offers `Open Default`, `Open Text`, and `Open Native`.
`AGENT_PBX_EDITOR_DEFAULT_SURFACE=terminal` makes the Editor tab lazily start or
attach an empty project-rooted native workspace; the default remains `textual`
when this setting is absent. `Native` or `/editor terminal` opens the native
workspace without staging a file through Textual. `/editor workspace` follows
the configured default surface.

Installed Neovim, Vim, and Nano profiles are discovered by default;
`/editor terminal nvim`, `/editor terminal vim`, and `/editor terminal nano`
select one explicitly. Configured profiles may provide `workspace_argv` for
their fileless startup command. An active Neovim gets a private RPC socket.
`Open Native` and `/editor terminal open` route the selected approved file with
tab-drop behavior: the first file uses the empty tab, new files get tabs, and an
already-visible file is reused. Existing buffers and plugin state remain owned
by that Neovim process.

`Text` or `/editor text` switches to the guarded Textual buffer without stopping
Neovim. Clean buffers are refetched so native writes appear immediately; dirty
Textual content must be saved or reverted before native editing starts. `/editor
terminal close` stops only the managed editor runtime. Review-readonly sessions
require a registered cwd that exactly matches their writable
`metadata.work_root`.

Terminal editor input follows native terminal ownership. Plain function keys
remain PBX navigation, while modified function keys and ordinary editor input
reach the embedded editor. Each editor uses an editor-only tmux server and
cannot reuse or mutate a Codex runtime mapping. Agent PBX persists the editor's
tmux socket, pane, PID/start-time evidence, root, profile, and RPC identity.
Daemon or TUI restart adopts the runtime only when every identity check still
matches. A stale mapping cannot signal an arbitrary process; use
`/editor terminal forget` to remove the verified dead record. Daemon shutdown
detaches editor runtimes by default. Set
`AGENT_PBX_TERMINAL_EDITOR_SHUTDOWN_POLICY=stop` only when daemon shutdown
should explicitly end managed editors.

## WorkerBee backend transactions

When the host configures compatible WorkerBee status and switch helpers, the
WorkerBee tab exposes preview actions for OSS, platform-debug, and rollback.
Palette equivalents are `/workerbee switch oss`, `/workerbee switch debug`,
and `/workerbee switch rollback`. Every apply is bound to the displayed preview
and typed confirmation. A switch affects future sessions; running Codex
sessions retain their current MCP binding.

## Focus and refresh behavior

Async refreshes use generation checks. A completed background task may restore
focus only if the user has not moved since it began. Agent and Operator order
also remains stable during active list interaction, preventing cursor jumps.
The Joplin reader keeps per-note scroll positions and the native terminal avoids
painting unchanged frames.

Use `[` and `]` to change the split width and `0` to reset it. Adaptive,
split, compact, and tiny layouts are available; compact modes retain keyboard
access to the same right-side tabs.

