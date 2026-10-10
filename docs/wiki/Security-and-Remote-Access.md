# Security and Remote Access

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

## Default boundary

Agent PBX binds to loopback by default. A non-loopback listener requires bearer
authentication and TLS unless an explicit insecure-LAN override is chosen for a
controlled lab. Provider secrets, Joplin tokens, remote credentials, private
profile overlays, and administrator-enforced Codex settings stay outside Git.

Run posture checks before exposing any client surface:

```bash
agent-pbx doctor --strict
```

## Client choices

- Local TUI: full native interaction on the PBX host.
- SSH attach: preferred full-fidelity remote terminal access.
- Remote TUI: authenticated WSS state, independent client selections/drafts,
  and observer/controller roles.
- HTTP/WSS/MCP: typed integration surfaces governed by PBX policy.

Never expose a raw tmux socket or raw Codex app-server endpoint to the LAN. SSH
keeps terminal transport inside a mature authenticated channel; Agent PBX can
generate the exact runtime attach command.

## Remote credentials

Credentials are short-lived, role-scoped, audience-bound, revocable, and
optionally client- or Agent-bound. Observers cannot mutate state. Agent-scoped
controllers cannot use global credential administration, audit, legacy global
API, or unrestricted MCP surfaces. Revocation and expiry apply immediately.

Prompt bodies and terminal snapshots are excluded from remote events by
default. Optional snapshots are read-only and must be enabled deliberately
because pane text can contain private project data.

## Review and filesystem policy

Review forks write only in their assigned work root and treat caller source as
read-only. Any Codex children inherit that boundary. The Editor enforces
approved roots, symlink/path traversal checks, sensitive-path policy, hash/mtime
conflicts, and recoverable trash for untracked data. Tracked deletion remains
visible to Git.

## Audit-sensitive operations

Model elevation, runtime repair, prompt injection, file writes, Joplin writes,
tmux movement, remote controls, merge/issue mutation, lifecycle deletion, and
credential changes require typed actions and audit evidence. Destructive
operations use preview and exact confirmation where the change cannot be
reversed directly.

