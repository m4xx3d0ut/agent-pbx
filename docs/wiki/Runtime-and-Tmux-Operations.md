# Runtime and tmux Operations

> **Applies to:** Agent PBX 2.3.x
> **Last reviewed:** 2026-10-21

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

Lifecycle actions additionally re-list the recorded server and require one
exact session/window/pane match before stop, purge, restart, or resume. Missing
socket/server provenance, a reused pane ID, or a live identity mismatch performs
zero mutation. The implementation never retries a destructive action against
the process-default tmux server.

When `AGENT_PBX_TMUX_BIN` selects a qualified absolute binary, daemon startup
creates a private `runtime-bin/tmux` shim under the Agent PBX state root and
prepends that directory to managed-runtime `PATH`. This also makes an ordinary
`tmux` command launched from Codex resolve to the qualified binary. Agent PBX
validates ownership, executable mode, and write permissions before replacing
the shim. `agent-pbx doctor` reports the result as
`managed-tmux-command`. Existing runtime processes acquire the updated
environment at their next safe restart/resume; they are never restarted solely
to refresh the shim.

Linked-worktree Git-admin permissions follow the same activation rule. A
package update alone does not change a running Codex process. A qualified edit
fork receives its launch-local permission profile only on a reviewed fresh
launch, in-place restart/resume, or managed cross-server resume. The preflight
runs before pane creation or replacement; failure leaves the existing pane and
working tree untouched. The four Git-admin paths are sandbox filesystem grants,
not workspace or `--add-dir` roots. Review forks retain their existing read-only
sandbox. On Codex 0.166, a base profile with split secret/read/deny rules is not
flattened or weakened. Instead of placing Git-admin paths in that runtime, a
stopped and inactive edit fork uses the local-only `/operator git commit`
broker for an exact root-approved leaf subset. `/operator git recover` resolves
one exact pending transaction after a TUI/process interruption. Neither action
restarts Codex, and the fork remains stopped.

## Embedded terminal

`Latest` runs a normal tmux client on a child PTY and renders its virtual
terminal grid inside Textual. The disposable embedded client holds a single
writer lease. Closing the TUI or disabling native mode detaches that client and
releases the lease; it does not stop Codex.

The terminal drains and parses every available PTY byte, but coalesces Textual
paint requests during sustained output. The default maximum paint rate is 10
frames per second. `AGENT_PBX_TERMINAL_MAX_PAINT_FPS` may set a qualified local
value from 5 through 60; raising it trades more CPU for intermediate-frame
smoothness. When tmux owns scrollback, the renderer keeps only a small 200-line
mirror rather than duplicating the full history. `Latest` shows bounded runtime
telemetry for event-loop lag, RSS, PTY throughput, paint rate, and frame
coalescing without storing prompt contents.

Managed daemon logs rotate while the daemon remains active. The default limit
is 64 MiB with three backups; `AGENT_PBX_DAEMON_LOG_MAX_BYTES`,
`AGENT_PBX_DAEMON_LOG_BACKUPS`, and
`AGENT_PBX_DAEMON_LOG_CHECK_SECONDS` tune those bounds for a local deployment.

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
- A blocked linked edit fork should keep all working-tree changes in place.
  Install the reviewed Agent PBX package, then restart or reopen only the TUI so
  it loads the new validator; this source-only change does not require a daemon
  restart. Record and verify the fork's current Codex session, cwd, branch, and
  diff, then invoke guarded in-place restart/resume for only that edit fork at a
  safe turn boundary. Qualification runs inside that action before pane
  replacement; failure leaves the pane and working tree untouched. After a
  successful resume, verify the same session, cwd, branch, and diff before
  staging. Do not relaunch callers or restart any other pane. If the branch
  namespace is shared or top-level, stop: branch migration is a separately
  approved repository mutation and is never part of repair activation. If
  qualification reports a split filesystem policy on Codex 0.166, do not switch
  to legacy writable roots or remove secret-deny rules. Stop the exact edit fork
  through the guarded TUI lifecycle action, align its Operators row with Enter,
  and use `/operator git commit` only after reviewing the exact leaf identities
  and receipt-bound confirmation. A direct-compatible fork continues to use its
  qualified runtime profile and is deliberately ineligible for the broker.
- The broker requires an initially empty staged set and a dedicated
  `pbx-operator-N/...` branch. It never discovers or stages all changes. An
  interrupted transaction is recovered only through `/operator git recover`
  when one exact journal receipt still matches the stopped fork and recomputed
  topology. Multiple, corrupt, foreign, or drifted journals require manual
  review and cause zero recovery mutation.
- Rollback never resets, cleans, stashes, edits, or opens unapproved worktree
  paths. Exact ref/index/reflog state is restored when its recorded identities
  still match. Journaled approved-content Git objects may remain unreachable
  after a failed compare-and-swap and are intentionally left for normal Git GC.
