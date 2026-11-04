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
Git directory writable. It qualifies the canonical worktree, forward `.git`
pointer, reverse admin `gitdir` pointer, common directory, critical admin files,
ownership, object posture, and an isolated `pbx-operator-N/...` branch
ref/reflog namespace. The four qualified administration paths are the active
per-worktree admin directory, common object database, dedicated branch-ref
namespace, and matching reflog namespace. Common configuration, hooks, sibling
worktree administration, unrelated refs, and the common `.git` root remain
outside the grant.

For a direct-compatible base profile, those four paths are permission-profile
filesystem entries only. Agent PBX never passes them as workspace roots or
`--add-dir` inputs. Codex 0.166 can create protected `.git`, `.agents`,
`.codex`, and `.aws` mount targets below external writable roots when a profile
also needs split secret/read/deny rules. A provider-free app-server canary also
rejects the legacy `sandbox_workspace_write.writable_roots` alternative:
config readback sees the roots, but retaining trusted profile provenance leaves
the linked index read-only. Agent PBX therefore keeps direct mode for compatible
profiles and selects a separate local-TUI commit broker only for the proved
split-policy incompatibility. No policy is silently weakened.

The broker is root/TUI-confirmed, never caller-callable, and is not exposed by
HTTP, API, MCP, or server routes. It requires one exact aligned Operators row
whose edit fork has canonical `canceled` status, is inactive, and has no live
tmux mapping. Qualification is recomputed from the canonical work root before
every review, confirmation, execution, and recovery. An exact historical
four-path metadata record may be treated as inert only for a stopped legacy
fork when every topology/path value matches the fresh plan; partial or
conflicting metadata fails closed.

The operator must enumerate exact leaf paths. The receipt displays and binds
each approved leaf's state, mode, raw SHA-256/blob OID, and prior index mode/OID
alongside branch, HEAD/tree, index, upstream, ref, reflog, authority, and
transaction identities. The broker commits raw bytes with no Git attributes or
clean filters. It does not run hooks, inherit caller environment, expose a
shell/message surface, perform implicit staging, enumerate repository status,
or open unrelated protected dirt. Qualification rejects local/worktree Git
include directives before repository-aware Git commands, rejects object
alternates and partial-clone/promisor configuration, disables replacement
objects and lazy fetch, and uses an absolute Git executable with a sanitized
environment. Linux qualification includes a strace gate proving that candidate
review and receipt preparation do not open/stat/readlink sentinel protected
paths or unapproved `.gitattributes`, and that an external config include target
is never opened.

Transactions prepare approved blobs/tree/commit under a private `0700` journal
object quarantine and durably record the exact manifest before any shared
object installation. Git's index lock and dedicated-ref compare-and-swap guard
the visible transaction. Recovery accepts only the closed, typed journal state
machine and exact ref/index/reflog identities; corrupt, foreign, ambiguous, or
drifted state performs no recovery mutation. Logical repository rollback is
exact when those identities match. A failed transaction may leave only the
journaled immutable objects derived from approved content unreachable in the
shared object database. Agent PBX never deletes shared objects during rollback;
normal Git garbage collection may reclaim them safely later.

Review/read-only forks, direct-compatible edit forks, running/paused/unknown
forks, shared branch namespaces, staged/unmerged/sparse/split indexes,
bare/detached repositories, malformed pointers, sibling-admin collisions,
symlink races, unsupported file types, and root/ownership ambiguity cannot use
the broker.

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

Operator execution authority is enforced separately from role and WorkerBee
placement. `scoped-edit` is the public default. `trusted-workbench` uses a
versioned list of exact Git toplevels and qualified linked-worktree
administration paths; a parent collection such as `~/git` or `~/git/k1s-wt`
does not become a writable wildcard. `full-access` is unavailable unless the
host explicitly raises `AGENT_PBX_OPERATOR_AUTHORITY_PROFILE_CEILING`.
Review forks are fixed to `review-readonly` regardless of root Operator or
workstation posture. See [Operator Authority and Workspace Sets](Operator-Authority-and-Workspace-Sets.md).

## Audit-sensitive operations

Model elevation, runtime repair, prompt injection, file writes, Joplin writes,
tmux movement, remote controls, merge/issue mutation, lifecycle deletion, and
credential changes require typed actions and audit evidence. Destructive
operations use preview and exact confirmation where the change cannot be
reversed directly.
