# Operator Authority and Workspace Sets

> **Applies to:** Agent PBX 2.3.x
> **Last reviewed:** 2026-11-04

Agent PBX treats Operator role and execution authority as separate controls.
An Operator may coordinate campaigns without receiving broad filesystem,
network, Git, or WorkerBee mutation authority. The daemon owns the durable
assignment and enforces administrator ceilings; the TUI is a controller, not
the policy boundary.

## Profiles

| Profile | Filesystem and network | WorkerBee | Inheritance |
|---|---|---|---|
| `scoped-edit` | Current checkout under the normal Codex workspace profile | `observe-local`, same-repository moves require approval | Public default |
| `trusted-workbench` | Exact members of one versioned workspace set; LAN network and local binding enabled | `local-deploy`, automatic moves only among exact set members | Root Operator to edit fork |
| `full-access` | Codex `danger-full-access` | `local-deploy` | Never a committed default; local ceiling must allow it |
| `review-readonly` | Review work root only | `review-readonly` | Fixed for review forks; cannot be elevated |

Use `/operator authority` for the selected root Operator or edit fork. Saving a
profile changes durable policy. It does not mutate a running Codex process.
Restart/resume at a safe turn boundary; Agent PBX keeps the Codex thread ID,
verifies the launch, and then records the assignment as active.

## Exact workspace sets

A workspace set is an immutable versioned snapshot of canonical Git
toplevels. Each member records:

- checkout path;
- Git common directory;
- branch and HEAD at qualification time;
- linked-worktree status;
- exact object, index, branch-ref, and reflog administration paths needed by a
  linked edit worktree.

The digest changes whenever membership or qualified topology changes.
Assignments pin one set version and digest so drift remains visible.

Collection roots are discovery helpers. For example, `~/git/k1s-wt` can be
entered as a collection and Agent PBX will discover its immediate Git
checkouts while excluding review scratch roots, trash, temporary directories,
virtual environments, and dependency caches. The user reviews and saves the
resulting explicit members. New repositories created under the collection do
not silently enter an existing set.

This design supports both ordinary sibling repositories under `~/git` and the
older nested worktree collection layout under `~/git/k1s-wt` without granting
write access to the whole top-level directory.

## Git safety

Linked worktrees are qualified with the same checks used by the bounded Git
broker: canonical paths, current-user ownership, trusted `.git` indirection,
matching backpointers and common directories, attached namespaced branches,
no config includes or object alternates, and exact ref/reflog namespaces.

Two selected worktrees cannot share the same branch in one Git common
directory. Shared object directories are deduplicated. The generated Codex
Trusted Workbench permission profile receives only selected workspace roots and
each already-qualified canonical common Git directory. A common Git directory
remains an explicit writable root when nested beneath a selected checkout,
because a more-specific read-only sandbox mount can override the writable
parent. Exact canonical duplicates are collapsed. Narrow per-worktree, object,
ref, and reflog administration paths remain qualification and broker evidence;
they are not added as launch roots. Unrelated sibling repositories and parent
directories remain outside the write set.

## Root-supervised exact Git checkpoints

The committed default is `guided`: a user reviews and runs one guarded stop,
broker, resume, or validation action at a time. Dedicated single-user hosts may
choose `root_operator`, which lets a root Operator ask the local TUI to execute
the stop → exact commit → same-session resume sequence for its own edit fork.
This is orchestration authority, not direct Git authority for the model.

The daemon accepts a request only when all of these identities agree:

- active root Operator and owned edit fork;
- running campaign and exact executable handoff;
- activated Trusted Workbench profile and workspace-set digest;
- exact paths, expected Git parent or reviewed ordered parent list, commit
  subject, exact target tree for merge checkpoints, and scope digest.

The request ID is deterministic for the exact handoff, campaign assignment,
workspace, and scope. It is persisted before F8 emits an idempotent
`awaiting_broker_approval` needs-input report, then leased to one loopback TUI
executor. The TUI uses the
existing private-journal Git broker, verifies the resulting HEAD, ordered
parents, and empty index, resumes the same Codex session in a managed tmux pane,
and writes a root-readable receipt. For a merge checkpoint, the first parent
must remain the stopped fork's exact HEAD and all additional parents are pinned
complete commit IDs. The broker rebuilds the reviewed target tree from only the
pinned raw-byte paths and rejects ambient merge, cherry-pick, revert, rebase,
bisect, or sequencer state. A crash after commit can recover old/new HEAD evidence
from the durable action receipts. Validation remains with the root Operator,
which must validate the exact terminal transaction and acknowledge the handoff
completion receipt before assignment or campaign completion. Actionable
handoffs, open or unvalidated requests, incomplete transactions, and
unacknowledged completion receipts all block completion. Recovery discovered
after terminal history is handled by one immutable campaign successor rather
than reopening or rewriting the predecessor.
Edit and review forks are not given the request tools, remote observers cannot
control the workflow, and executor endpoints are loopback-only. This does not
reduce the authority of the normal controller bearer: keep that administrator
credential out of untrusted processes.

```bash
export AGENT_PBX_OPERATOR_BROKER_MODE="guided" # guided|root_operator|disabled
export AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE="editable" # editable|read_only|hidden
export AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS="900"
export AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT="1"
```

`read_only` keeps posture visible but prevents local control changes; `hidden`
removes the control from the TUI. The mode remains daemon-enforced in either
case. Use `agent-pbx doctor` to verify policy and the permissions of the local
`apbx` launcher before enabling `root_operator`.

## WorkerBee coupling

Applying Trusted Workbench also assigns:

- WorkerBee access profile `local-deploy`;
- workspace policy `scoped-workbench`;
- request mode `automatic`;
- allowed roots equal to the set's exact Git members.

WorkerBee retains one active binding per session. Automatic movement can change
that binding among selected members, but it cannot bind to the collection root,
an unselected repository, or a path outside the administrator ceiling. Review
forks retain `review-readonly` through every transition.

Trusted Workbench also permits a root Operator to *request* a PBX-controlled
WorkerBee validation run for its owned edit fork. It does not authorize the run
by itself. Public defaults still require F8 approval for the exact source,
backend generation, profile, recipe, and tool-manifest digests. A private
workstation profile may allow a bounded campaign lease with a use count and
expiry; source or backend drift creates a new approval boundary.

## Administrator posture

```bash
export AGENT_PBX_OPERATOR_AUTHORITY_CONTROL_MODE="editable" # editable|read_only|hidden
export AGENT_PBX_OPERATOR_AUTHORITY_PROFILE_CEILING="trusted-workbench"
export AGENT_PBX_OPERATOR_AUTHORITY_DEFAULT_PROFILE="scoped-edit"
export AGENT_PBX_OPERATOR_AUTHORITY_ROOT_CEILING_JSON='["/srv/engineering"]'
export AGENT_PBX_OPERATOR_AUTHORITY_WORKFLOW_LABEL="Trusted Workbench"
```

The clean-user default is `scoped-edit`; the default ceiling permits a user to
choose Trusted Workbench explicitly. Set the ceiling to `scoped-edit` for
managed multiuser hosts. `full-access` is accepted only when a local
administrator explicitly raises the ceiling. `read_only` keeps current
assignments visible and prevents changes. `hidden` also removes the TUI
control while preserving server enforcement.

On a dedicated personal workstation a suitable local overlay is:

```bash
export AGENT_PBX_OPERATOR_AUTHORITY_PROFILE_CEILING="trusted-workbench"
export AGENT_PBX_OPERATOR_AUTHORITY_DEFAULT_PROFILE="trusted-workbench"
export AGENT_PBX_OPERATOR_AUTHORITY_ROOT_CEILING_JSON="[\"$HOME/git\"]"
export AGENT_PBX_OPERATOR_AUTHORITY_WORKFLOW_LABEL="Rosie Workbench"
```

The JSON must contain the expanded absolute path. The workstation label is
local presentation state and does not change the public profile semantics.

## Rollback

Select `scoped-edit` in `/operator authority`, save it, and restart/resume at a
safe turn boundary. Agent PBX returns WorkerBee to `observe-local` with
same-repository mobility requiring approval. The previous workspace-set
versions remain audit records and can be selected again later. No set change
deletes repositories, worktrees, Codex transcripts, or WorkerBee workspaces.

For a Trusted Workbench linked-worktree launch update, first verify that
readback contains the exact qualified common Git directory. Only a new or
guarded-resumed process receives the corrected mount; no daemon restart is
required for the TUI-side launch projection. If qualification or mount readback
fails, leave the fork stopped, restore the previous reviewed TUI/package, and
do not alter Git refs, indexes, permissions, or workspace-set records.
