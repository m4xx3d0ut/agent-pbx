# Security and Remote Access

> **Applies to:** Agent PBX 2.3.x
> **Last reviewed:** 2026-10-12

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

The optional public browser demo is a separate synthetic runtime, not a remote
view into the operator control plane. Each visitor gets private temporary
state, deterministic fixtures, a loopback daemon, and a dedicated tmux server.
See [Interactive Browser Demo](Interactive-Browser-Demo.md) for its publication
and isolation requirements.

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

For trusted edit forks in linked worktrees, Agent PBX does not make the common
Git directory writable. It emits a launch-local Codex permission profile only
after proving the canonical worktree, the forward `.git` pointer, the reverse
admin `gitdir` pointer, the common directory, critical admin files, ownership,
and an isolated branch ref/reflog namespace. The minimal additional writable
set is the active per-worktree admin directory, common object database, and the
active branch's dedicated ref/reflog directories. Existing Codex permission
profiles must ultimately inherit `:workspace` and must not already grant Git
administration writes. Legacy `sandbox_mode`, administrator-profile conflicts,
bare/detached repositories, malformed pointers, sibling-admin collisions, and
symlink or root ambiguity all fail closed.

WorkerBee workspace placement is enforced by the daemon, independently of its
tool profile. The public posture pins each lease to its checkout and requires
controller approval. Broader workstation profiles may allow same-repository or
allowed-root mobility, but canonical Git discovery, current-user ownership,
symlink resolution, policy ceilings, and root ceilings still apply. Review
forks retain `review-readonly` tools across every transition. Ordinary status
events report policy/project state without publishing allowed host paths.

Administrators can set workspace controls to `read_only` or `hidden`, cap the
maximum policy, force request mode, and provide a JSON root ceiling through the
`AGENT_PBX_WORKERBEE_WORKSPACE_*` environment variables documented in the
WorkerBee guide. These checks occur server-side and cannot be relaxed by a TUI
configuration file or remote controller scoped to one Agent.

## Audit-sensitive operations

Model elevation, runtime repair, prompt injection, file writes, Joplin writes,
tmux movement, remote controls, merge/issue mutation, lifecycle deletion, and
credential changes require typed actions and audit evidence. Destructive
operations use preview and exact confirmation where the change cannot be
reversed directly.
