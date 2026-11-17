# Troubleshooting and Recovery

> **Applies to:** Agent PBX 2.3.x and later
> **Last reviewed:** 2026-11-17

Start with read-only posture and preserve runtime evidence:

```bash
agent-pbx mcp status
agent-pbx doctor
agent-pbx migrate verify
```

Do not restart or move a healthy pane merely because the embedded surface is
blank. Check the entity's mapping, tmux server/socket, process evidence, Codex
session, and writer lease first.

## TUI unavailable

Attach directly to the recorded local runtime:

```bash
agent-pbx runtime attach --entity codex-agent-pbx
```

Use `--read-only` for diagnosis. Detaching this client leaves the managed
runtime running. On another host, generate an SSH attachment with
`agent-pbx remote ssh-attach`.

## TUI exits or becomes unresponsive

Do not kill or recreate the managed runtime merely because the disposable TUI
client stopped. Inspect the bounded local run evidence first:

```bash
tail -n 100 ~/.local/share/agent-pbx/tui-runtime/events.jsonl
ls -lt ~/.local/share/agent-pbx/tui-runtime/stderr/
ls -lt ~/.local/share/agent-pbx/tui-runtime/unclean/
```

Every launch creates a PID/process-start-qualified marker in `active/`. A
normal quit, handled interrupt, or Python exception writes a terminal event and
removes the marker. A hard kill cannot execute cleanup; the next TUI launch
recognizes the dead process, records `tui_unclean_exit_detected`, and moves the
marker to `unclean/` without touching managed tmux or Codex state. Live markers
from another TUI client are not reaped.

Per-run stderr and exception tracebacks are stored with user-only permissions,
bounded retention, and credential redaction. The configured PBX token and
common bearer/query token forms are removed, but operators should still review
diagnostic evidence before sharing it. Set `AGENT_PBX_TUI_RUNTIME_ROOT` to a
private alternate directory when the standard XDG state location is unsuitable.

After preserving the evidence, relaunch with the normal local wrapper (for
example `apbx local`) and verify the selected entity's mapping and writer lease.
The TUI client should reattach to the existing native runtime; it should not
respawn a healthy managed Codex pane.

## Native terminal falls back to capture

Check:

1. native tmux and embedded-terminal settings are enabled;
2. the selected entity has a verified v2 mapping;
3. its recorded tmux binary and socket are available;
4. no stale client owns the writer lease;
5. the current right tab is Latest;
6. terminal engine diagnostics do not report an unsupported sequence.

Disabling native mode must detach the embedded PTY, clear selected terminal
state, and release the lease. If that evidence is stale, repair through a
previewed lifecycle action rather than deleting the mapping manually.

## Wrong geometry or scrollback

The recorded runtime tmux executable must be used for resize and copy-mode
operations. Page Up should enter history; `/tmux scrollback` is the explicit
fallback. Confirm the managed Codex terminal profile uses tmux-owned scrollback
and that a read-only observer is not controlling authoritative size.

## Key does the wrong thing

Run `/pbx keymap` and use the interactive key probe. Plain F keys belong to PBX;
Shift+F keys belong to Codex. Ctrl+P opens the PBX palette. Printable Agent and
Operator shortcuts are disabled while a writable embedded terminal owns focus,
preventing typed characters from triggering lifecycle actions.

## Joplin note is locked or empty

Encrypted content must appear locked. Check the dedicated profile, Data API,
profile-owner mode, sync job, and E2EE status. Unlock/decrypt with a supported
Joplin client; do not paste a master password into Agent PBX configuration or a
note. A verified plaintext target can remain successful while unrelated locked
items are reported separately.

## WorkerBee project is absent

Compare the PBX host cwd, WorkerBee-visible path, repository root, backend
provenance, and mount translation. Distinguish path mismatch from an omitted
tool allowlist or an unavailable/older endpoint.

If `workerbee_v1_session_start` reports
`WORKERBEE_WORKSPACE_APPROVAL_REQUIRED`, open the selected entity's WorkerBee
tab, choose `Workspace`, review the pending canonical root/subpath and apply it,
then retry the original call. `WORKERBEE_WORKSPACE_SCOPE_DENIED` means the
target exceeds the effective `pinned`, same-repository, allowed-root, or
administrator ceiling. Do not bypass that decision by sending an appliance
path: the gateway intentionally maps only verified host Git roots into the
selected runtime.

## Migration rollback

Rollback requires a reviewed backup and a stopped managed daemon:

```bash
agent-pbx mcp stop
agent-pbx migrate rollback \
  --backup /path/to/reviewed-backup \
  --confirm RESTORE
agent-pbx mcp start
agent-pbx doctor
```

Add `--restore-config` only when the matching configuration must also be
restored. Rollback creates a safety backup first and does not delete later
project work, transcripts, Joplin notes, or trash entries.
