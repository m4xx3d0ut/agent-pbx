# ADR 0012: Durable helper registry

- Status: Accepted for implementation
- Date: 2026-11-12
- Baseline: Agent PBX v2.3.0 (`dcb7d632e66cd22ef4b53718d92871d5f31282e9`)

## Context

Operator KB entries are reviewed text. Executable helpers need stronger
provenance, validation, lifecycle, materialization, and invocation controls and
must not be smuggled into the KB or rediscovered by crawling temporary files.

## Decision

Agent PBX will add private content-addressed helper bundles using
`agent-pbx.durable-helper-bundle/v1`. A bundle binds an exact source path,
content digest, manifest, provenance, validation receipt, declared scopes,
reviewer evidence, and optional KB references. Lifecycle states are `draft`,
`proposed`, `reviewed`, `active`, `deprecated`, `retired`, `rejected`, and
`quarantined`.

Only exact-path proposals are accepted. Validation runs in an isolated,
bounded harness. Active content is materialized read-only; output is a separate
writable directory, and invocation always names the helper and reviewed scope.
The registry never crawls or auto-ingests temporary directories.

The first implementation accepts only bounded, single-link Python source files
under explicitly configured proposal roots. Every lexical component is
no-follow checked before canonicalization; source ownership, mode, identity,
and bytes are rechecked across one bounded read. Validation invokes a fixed
Python syntax compiler in an owner-private harness with `-I -S`, closed file
descriptors, a minimal environment, no helper execution, and a five-second
timeout. Promotion requires that exact digest-bound validation receipt.

Objects are private and content addressed. Activation materializes `helper.py`
mode 0444 with a mode-0400 manifest and a separate mode-0700 output directory.
The API returns an exact, shell-free invocation plan but does not execute it;
execution remains an explicit downstream action. Imports always return to
`proposed` regardless of source lifecycle state. The root-only TUI review
center exposes validate+review, promote, reject, retire, and quarantine.

Reuse/churn UAT is opt-in and records only counters: proposals, activations,
reuses, replacements, and quarantines. It records no helper input, output, KB
body, command body, or credential data.

## Migration and feature gate

The registry is default-disabled via `AGENT_PBX_DURABLE_HELPERS_ENABLED` and
uses additive schema-42 tables plus a private object root configured by
`AGENT_PBX_DURABLE_HELPER_ROOT`. Proposal roots are an explicit JSON list in
`AGENT_PBX_DURABLE_HELPER_PROPOSAL_ROOTS`. Content-free reuse/churn metrics are
separately opt in with `AGENT_PBX_DURABLE_HELPER_MEASUREMENTS_ENABLED`. KB v1
references remain plain text and acquire no executable meaning. Migration can
attach helper IDs to KB entries only after explicit helper review.

## Rollback

Disable activation and invocation, retire materializations, preserve bundles,
validation receipts, and audit records, and remove only verified registry-owned
materialized views. Shared or unknown files are never deleted.

## Security consequences

Promotion is root-controlled. Unknown schemas, scopes, provenance, validation
receipts, or object digests fail closed. A KB publication cannot activate a
helper, and helper activation cannot publish a KB entry.
