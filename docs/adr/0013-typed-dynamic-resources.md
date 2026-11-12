# ADR 0013: Typed dynamic resources

- Status: Accepted for implementation
- Date: 2026-11-12
- Baseline: Agent PBX v2.3.0 (`dcb7d632e66cd22ef4b53718d92871d5f31282e9`)

## Context

Some bounded development workflows need a Unix socket, loopback service,
managed cache, existing WorkerBee service, or reserved port. Generic command or
host-resource access would erase Agent PBX authority and cleanup boundaries.

## Decision

Agent PBX will broker only typed `agent-pbx.dynamic-resource-request/v1`
requests. Every request binds requester, campaign or handoff, activated
workspace-set digest, exact resource specification, provider identity,
WorkerBee binding/backend generation when applicable, TTL, quota, use count,
approval or lease, and cleanup receipt.

Initial low-risk classes are workspace Unix socket, loopback service, managed
cache, existing WorkerBee service, and port reservation. Network endpoints,
host sockets, devices, and credential references are modeled but disabled and
always manual until separately qualified. No request accepts a shell command,
arbitrary executable, opaque environment, or generic host path.

The requester and root Operator must be registered, the supplied workspace
digest must equal the Operator's activated authority digest, and a referenced
campaign or handoff must be durably active for that exact Operator. Stale or
invented identifiers fail before a request row is created.

Public policy is manual. A Trusted Workbench policy may auto-approve a reviewed
workspace-local low-risk class. High-risk classes stay manual. If an existing
Codex sandbox cannot safely receive a resource, the receipt says restart or
resume is required; Agent PBX never silently remaps a live runtime.

## Migration and feature gate

The broker is default-disabled by `AGENT_PBX_DYNAMIC_RESOURCES_ENABLED` and adds
schema-43 request and event tables. Existing WorkerBee leases and F8 approvals
are referenced by identity and are not rewritten. Recovery classifies
interrupted operations as `recovery_required` rather than guessing completion.

The root Operator API is global-controller authenticated. Manual requests are
single-flight and can be approved only through the exact request/digest phrase
and the TUI-local F8 method. Campaign-lease requests consume an existing,
unexpired delegation lease only after campaign, workspace digest, target
Operator, action class, denial set, and use quota all match. Trusted Rosie auto
approval is limited by code to workspace sockets and managed caches; host port
reservations and loopback services remain manual. A caller-supplied class
cannot broaden that fixed set.

Providers are injected behind a closed interface and return an exact receipt
that rebinds request digest, class, workspace set, WorkerBee binding/generation,
spec digest, quota, expiry, and restart posture. Agent PBX ships with no active
provider while the gate is disabled. Missing providers fail before lease
state changes; ambiguous acquisition or cleanup is durably
`recovery_required`.

## Rollback

Deny new requests, revoke leases, release only exact provider-owned resources,
and preserve cleanup or recovery receipts. Ambiguous provider state remains
manual recovery; rollback never broadens access to make cleanup easier.

## Security consequences

F8 single-flight approval, bounded campaign leases, audit, expiry, revocation,
quota, and narrowly idempotent terminal release apply to every class. Unknown
fields, provider generation drift, authority drift, or cleanup ambiguity fail
closed. A resource prepared for a running sandbox is marked
`restart_required`; the broker does not inject, restart, or remap that runtime.
