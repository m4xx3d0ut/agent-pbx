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

Public policy is manual. A Trusted Workbench policy may auto-approve a reviewed
workspace-local low-risk class. High-risk classes stay manual. If an existing
Codex sandbox cannot safely receive a resource, the receipt says restart or
resume is required; Agent PBX never silently remaps a live runtime.

## Migration and feature gate

The broker is default-disabled and adds request, lease, event, and cleanup
receipt tables. Existing WorkerBee leases and F8 approvals are referenced by
identity and are not rewritten. Recovery classifies interrupted operations as
`recovery_required` rather than guessing completion.

## Rollback

Deny new requests, revoke leases, release only exact provider-owned resources,
and preserve cleanup or recovery receipts. Ambiguous provider state remains
manual recovery; rollback never broadens access to make cleanup easier.

## Security consequences

F8 single-flight approval, bounded campaign leases, audit, expiry, revocation,
quota, and idempotent release apply to every class. Unknown fields, provider
generation drift, authority drift, or cleanup ambiguity fail closed.
