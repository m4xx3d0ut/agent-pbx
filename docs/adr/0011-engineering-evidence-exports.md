# ADR 0011: Engineering evidence exports

- Status: Accepted for implementation
- Date: 2026-11-12
- Baseline: Agent PBX v2.3.0 (`dcb7d632e66cd22ef4b53718d92871d5f31282e9`)

## Context

Agent PBX already persists reports, campaigns, handoffs, approvals, audit events,
and exact Git broker receipts. Operators need portable evidence without scraping
transient TUI widgets or exposing unrelated worktree dirt, prompts, credentials,
or terminal history.

## Decision

The daemon will own an additive evidence-export service. Every export has a
versioned `agent-pbx.evidence-export-manifest/v1` manifest, canonical content
hashes, bounded record counts, source identities, redaction policy identity,
and deterministic JSON, Markdown, or NDJSON rendering. Thread, campaign, and
filtered evidence-case exports use this service. The private export root is the
default; an Operator previews exact paths and retained records before pruning.

Exact Git review uses a separate `agent-pbx.git-review-artifact/v1` receipt. It
may inspect only the broker-approved leaf set after the fork is stopped and
frozen. The artifact records path status, object identities, bounded text hunks,
binary summaries, line counts, truncation evidence, and a SHA-256 digest that is
reverified before commit. Two-parent commits are represented explicitly and
never collapsed to an ambient worktree diff.

The broker obtains prior content only from each approved stage-0 index object;
it never runs a whole-worktree status or diff. Source capture is capped at 1
MiB per side for rendering, individual unified diffs are capped at 32 KiB, and
the receipt's aggregate rendered diff is capped at 256 KiB. Larger inputs keep
their exact mode, object ID, and byte count and carry explicit truncation
evidence. Binary inputs expose metadata only. The compact confirmation binds
the artifact digest and totals; a separate read-only viewer renders the bounded
full artifact. Execution reparses the artifact and recaptures the complete
receipt before the first repository object write.

Trusted Workbench edit forks created after workspace-set activation still run
the linked-worktree topology qualifier at launch. The qualifier's exact active
per-worktree Git directory may be merged into the permission profile as an
absolute filesystem write exception. It is not a workspace root: making it a
nested workspace root can trigger recursive bubblewrap remounts beneath the
already-mounted source or common Git root. The exception requires the same
activated workspace-set digest, approved common Git directory, canonical
owned non-symlink topology, and edit-fork posture. Objects, shared refs,
reflogs, sibling worktrees, and the broad common directory are never inferred
from the exception.

The default redaction policy excludes hidden reasoning, secrets, credentials,
prompt bodies, unrestricted terminal history, and environment data. Exporters
use positive field selection; unknown record classes fail closed. Joplin output
is rendering of the same manifest, not a second evidence source.

The export center is a global-controller daemon capability. It accepts only the
typed `thread`, `campaign`, and filtered `evidence_case` sources and the fixed
JSON, Markdown, and NDJSON renderings. Clients never select a filesystem path.
The daemon writes owner-private `0700` export directories and `0600` files,
records per-file and canonical bundle SHA-256 identities, and verifies those
identities before reuse. Retention is preview-first: this checkpoint lists the
exact immutable export IDs eligible for a later prune but exposes no delete
operation.

Thread export is routed through the daemon service rather than writing from the
TUI. Campaign evidence positively selects campaign, assignment, event, report,
handoff/completion, delegation-lease, and KB-reference metadata; assignment
prompts, handoff bodies, event payloads, and KB bodies are excluded. Evidence
cases select reviewed journal/audit/runtime/broker/WorkerBee categories and are
filtered to the requested Agent identity. Joplin rendering reads the verified
Markdown member of the same export and introduces no separate collection path.

## Migration and feature gate

Both features are default-disabled and additive. When the evidence gate is
disabled, daemon preview, export, verification, retention preview, and Joplin
rendering all fail closed. Enabling it moves Thread export to the daemon without
rewriting historical records. Existing broker receipts without an
artifact remain valid only for legacy flows that do not require diff review.
They cannot start a new broker execution; only an already-journaled legacy
transaction may enter its existing recovery flow. No database rewrite occurs
in this checkpoint.

## Rollback

Disable the gates, preserve immutable manifests and broker receipts, and return
Thread export to its legacy renderer. Rollback never deletes exported evidence
or weakens a receipt that already requires a review-artifact digest.

## Security consequences

The service does not gain credential, provider, terminal-control, or arbitrary
filesystem authority. Export roots are private, manifests are metadata-bounded,
and exact-leaf Git review never enumerates unapproved dirt.
