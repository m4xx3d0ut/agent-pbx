# ADR 0015: Operator KB repository synchronization

- Status: Accepted for implementation
- Date: 2026-11-12
- Baseline: Agent PBX v2.3.0 (`dcb7d632e66cd22ef4b53718d92871d5f31282e9`)

## Context

The Operator KB already owns reviewed text, source evidence, root-only promotion,
redaction checks, retrieval, and feedback. Teams need deterministic private-Git
synchronization without creating a second KB or giving the daemon ambient Git
credentials.

## Decision

The existing KB gains the default-disabled
`agent-pbx.operator-kb-bundle/v2` synchronization contract. A bundle binds a
canonical logical-project identity, stable entry IDs, monotonic revisions,
parent and content hashes, clean reviewer/promotion evidence, conflicts, and
tombstones. Canonical compact sorted JSON determines every content and bundle
SHA-256. Unknown fields, duplicate or unsorted stable IDs, malformed hashes,
nonlinear revisions, oversized bodies, and project drift fail closed.
Live entries whose redaction status is not clean are omitted entirely; the
manifest records only their local KB ID and a fixed exclusion reason. Terminal
tombstones remain content-free and may be synchronized for convergence.

Import is preview/reconcile first and idempotent. A revision is a linear update
only when its parent hash equals the current synchronized content and its
revision increments by one. Identical content is a no-op; divergent ancestry is
a conflict with zero import. Fresh imported live entries are always proposed.
An active status is retained only when the exact content is already represented
by a destination-local entry whose promotion evidence resolves to a registered
root Operator. A remote bundle cannot self-assert trusted review. Tombstones
retire an existing local entry or persist as sync state without fabricating KB
text.

Private-Git preparation is metadata-only. Agent PBX renders a deterministic
file map and tree digest under the dedicated
`refs/heads/pbx-kb-sync/<logical-project-prefix>` namespace. The daemon neither
runs Git nor reads credentials. `scripts/validate_operator_kb_bundle.py` is the
offline CI validator and reports only schema, digest, logical project, and entry
count. Root-controlled publication remains a separate operation outside this
checkpoint.

## Migration and rollback

Schema 41 adds only `operator_kb_sync_records`; existing KB entry and v1 bundle
rows are unchanged. Existing entries acquire v2 identities deterministically at
export. The first same-repository import adopts matching origin entries into
sync state. Rollback disables the v2 gate and preserves sync records for audit;
the v1 KB, retrieval, redaction, promotion, and retirement paths continue
unchanged. No rollback deletes KB text or private-Git history.

## Security consequences

The endpoints require a global controller and a root Operator. Bundle v2 never
grants execution, helper, credential, provider, network, or publication
authority. Unreviewed live text is not exported. Imported content passes the
existing secret-redaction scanner and defaults proposed. Git preparation
returns a closed branch namespace and exact file receipts with
`ambient_credentials_allowed=false` and `daemon_git_execution=false`.
