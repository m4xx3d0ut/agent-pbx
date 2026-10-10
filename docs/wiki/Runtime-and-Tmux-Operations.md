# Runtime and tmux Operations

> **Applies to:** Agent PBX 2.1.x  
> **Last reviewed:** 2026-10-10

## Runtime server modes

| Mode | Behavior |
|---|---|
| `dedicated` | Uses a private PBX-managed tmux server and is the clean-user default. It works inside or outside an outer tmux session. |
| `outer_if_present` | Reuses the validated server identified by the launching TUI's tmux environment, then falls back to dedicated if absent. |
| `outer_required` | Requires that validated outer server and fails with a diagnostic when it is unavailable. |

Runtime mappings record the tmux executable, socket, server, session, window,
pane, process evidence, and origin client where applicable. Operations use the
recorded tmux binary and socket instead of the daemon's inherited shell tmux.
This is essential when the host shell and managed runtime use different tmux
versions.

## Embedded terminal

`Latest` runs a normal tmux client on a child PTY and renders its virtual
terminal grid inside Textual. The disposable embedded client holds a single
writer lease. Closing the TUI or disabling native mode detaches that client and
releases the lease; it does not stop Codex.

The runtime server owns scrollback. Managed Codex profiles use inline terminal
behavior so tmux can retain history. Page Up or `/tmux scrollback` enters copy
mode. This works for desktop terminals and Android/Termux over SSH without
turning swipe gestures into Codex prompt-history arrows.

## Pop out and pop in

In dedicated mode, pop-out attaches a separate terminal or temporarily
suspends the TUI and attaches the current terminal. In integrated outer mode,
Agent PBX resolves the exact invoking tmux client and targets only that client
with `switch-client`. Ambiguous or absent client evidence fails safely; Agent
PBX never switches an arbitrary connected client.

Recursive attachment to the session containing the PBX TUI is rejected. Only
one client has the writer lease; observers must not inject input or control
authoritative geometry.

## Access outside the TUI

Attach a local terminal to an entity's recorded runtime:

```bash
agent-pbx runtime attach --entity operator-0
```

Use `--read-only` for an observer attachment. For another host, generate an SSH
command rather than exposing a tmux socket on the network:

```bash
agent-pbx remote ssh-attach \
  --host user@pbx.example.com \
  --entity operator-0 \
  --read-only
```

## Failure boundaries

- A missing embedded frame does not prove that Codex exited.
- A stale pane ID must be reconciled against socket, session, process, and
  Codex evidence before repair.
- Changing runtime server mode does not move a live pane between servers.
- Cross-server migration resumes the Codex session in a new managed pane,
  verifies it, and retains rollback evidence.
- Extensive terminal replay is unnecessary; Codex resume history and current
  runtime readiness are the required recovery state.

