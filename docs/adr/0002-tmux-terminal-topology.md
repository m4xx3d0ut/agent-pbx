# 0002 — Tmux terminal topology

Status: accepted for Agent PBX v2

The v1 left/right composition and right-side tab bar remain. `Latest` gains a
focusable `PbxTerminalSurface` backed by a child PTY running a normal tmux
client. The selected runtime tmux server owns the real Codex pane, persistence,
and scrollback; Textual renders the client grid inside `Latest`.

The committed server mode is `dedicated`. `outer_if_present` may reuse a
validated outer tmux server and is the preferred local workstation override.
`outer_required` is available for managed environments. Same-server attachment
must reject recursive attachment to the PBX TUI and target client switches to a
single recorded client.

Plain configured function keys always invoke PBX navigation. The configured
passthrough modifier, initially Shift, sends the corresponding unmodified
function key to the child terminal. Thus F2 opens Events and Shift+F2 sends F2
to Codex warnings.

