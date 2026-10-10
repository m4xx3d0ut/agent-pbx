# Troubleshooting and Recovery

> **Applies to:** Agent PBX 2.1.x  
> **Last reviewed:** 2026-10-10

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

