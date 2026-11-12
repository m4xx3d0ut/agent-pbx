# Post-v2.3 durable engineering control plane

This document records the local review candidate built from Agent PBX v2.3.0.
It deliberately does not assign the eventual minor-release version and is not
an activation, publication, or deployment record.

## Checkpoint contents

1. Versioned contracts, default-off gates, redaction, migration, rollback, and
   helper reuse/churn measurements.
2. Receipt-bound exact-leaf Git review artifacts with bounded text/binary
   evidence and drift rejection.
3. Hashed JSON, Markdown, and NDJSON evidence exports for threads, campaigns,
   and filtered evidence cases, including Joplin rendering and retention
   preview.
4. Deterministic Operator KB bundle v2 synchronization with conflict,
   tombstone, reviewer, and private-Git validation evidence.
5. Content-addressed durable helper bundles with closed lifecycle, exact-path
   proposal, validation receipts, read-only materialization, and reuse metrics.
6. Typed resource requests for low-risk local resources plus explicit disabled
   models for network endpoints, host sockets, devices, and credentials.
7. A default-disabled WorkerBee platform-debug development-overlay adapter
   contract for managed virtual environments, caches, build/test scratch,
   service state, Unix sockets/runtime, and generated-output staging.

## Broker and campaign durability follow-up

Root-supervised Git checkpoints now persist one deterministic request and one
`awaiting_broker_approval` report before F8 returns. Executor success remains
nonterminal for campaign purposes until the root validates the exact durable
action transaction and acknowledges the handoff completion receipt. Campaign
and immutable-report projection reject completion while actionable handoffs,
broker requests, transactions, or completion receipts remain outstanding.
Recovery discovered after terminal history uses one idempotent successor
campaign; it never reopens or rewrites the predecessor or its reports.

## Compatibility and security posture

Existing public behavior remains unchanged until each gate is explicitly
enabled. Backend, workspace source mode, access profile, and execution lane
remain independent. Review forks remain non-mutating. Trusted-direct source
stays read-only and `rprivate`; overlay authority never implies source-write or
controlled-run authority.

Public policy is manual by default. Team use requires a bounded delegation
lease. Trusted Rosie may auto-approve only its reviewed workspace-local
low-risk subset. WorkerBee development resources always require manual or
bounded campaign approval. High-risk network, host, device, credential,
provider, publication, and destructive actions remain outside this release
candidate.

## Activation requirements

Activation requires a separate root-reviewed handoff that identifies the exact
commit, schema backup, enabled gates, private roots, rollback owner, and
post-start readback. WorkerBee development resources additionally require a
separately reviewed private adapter that passes the checked-in conformance
fixture and isolated zero-residue UAT. No daemon or TUI should be restarted
until those prerequisites are complete.

## Rollback

Disable the affected gate first, refuse new work, release only exact
receipt-bound resources, retain immutable records for review, verify database
integrity, and restore the pre-activation configuration/database backup if the
schema rollback is required. Never delete shared objects, helper bundles,
provider paths, or KB state by inference.
