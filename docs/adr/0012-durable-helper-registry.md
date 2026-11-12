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

Reuse/churn UAT is opt-in and records only counters: proposals, activations,
reuses, replacements, and quarantines. It records no helper input, output, KB
body, command body, or credential data.

## Migration and feature gate

The registry is default-disabled and uses additive tables plus a private object
root. KB v1 references remain plain text and acquire no executable meaning.
Migration can attach helper IDs to KB entries only after an explicit review.

## Rollback

Disable activation and invocation, retire materializations, preserve bundles,
validation receipts, and audit records, and remove only verified registry-owned
materialized views. Shared or unknown files are never deleted.

## Security consequences

Promotion is root-controlled. Unknown schemas, scopes, provenance, validation
receipts, or object digests fail closed. A KB publication cannot activate a
helper, and helper activation cannot publish a KB entry.
