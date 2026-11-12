# ADR 0014: WorkerBee direct-bind development volumes

- Status: Accepted for implementation
- Date: 2026-11-12
- Baseline: Agent PBX v2.3.0 (`dcb7d632e66cd22ef4b53718d92871d5f31282e9`)

## Context

Platform-debug development needs writable virtual environments, caches,
build/test scratch, service state, Unix sockets, and generated-output staging.
The existing trusted direct bind deliberately exposes source read-only with
`rprivate` propagation and protected-name masks. Reinterpreting that source
mount as writable would violate its reviewed contract.

## Decision

WorkerBee remains the runtime provider and truth surface. Agent PBX accepts only
the exact `agent-pbx.workerbee-development-capabilities/v1` evidence and issues
`agent-pbx.workerbee-development-resource/v1` requests through the typed
resource broker. Supported classes are `managed_venv`, `managed_cache`,
`build_scratch`, `service_state`, `unix_socket_runtime`, and
`generated_output_staging`.

Capability evidence binds backend generation, provider identity, resource
classes, helper/receipt schema, and the invariants
`trusted_direct_bind_read_only=true`, `trusted_direct_bind_rprivate=true`, and
`writable_source=false`. Requests and cleanup receipts bind exact mount targets,
ownership, mode, quota, TTL, and provider resource IDs. Source and overlay
identities remain separate.

The provider contract is closed and metadata-only. It advertises the exact six
classes and fixed runtime targets under `/workspace/.agent-pbx-dev`, a bounded
TTL and quota ceiling, the workspace binding and backend generation, and a
canonical capability digest. Requests carry only an approved byte quota; they
cannot supply paths, commands, executables, arguments, environment variables,
or source-write policy. Provider receipts bind the exact target, provider
resource identity, owner UID/GID, mode `0700`, quota, expiry, capability digest,
and the unchanged read-only/rprivate source invariants. Cleanup succeeds only
with an exact receipt proving `cleanup_complete=true` and zero residue.

The public package defines an injected adapter protocol and a static
conformance fixture. It does not ship or discover a private implementation.
The `workerbee_development_resources_enabled` gate defaults off, and even an
enabled gate remains unusable unless a reviewed adapter is explicitly injected.
These resources remain manual or bounded-campaign-lease operations; the Rosie
low-risk auto policy does not apply. A running Codex sandbox is marked as
requiring restart rather than being remapped in place.

The private WorkerBee implementation is outside the public Agent PBX package.
It must implement the versioned capability and receipt contract; Agent PBX does
not hard-code a private repository or infer private behavior. Writable direct
source remains deferred unless a separately reviewed acceptance case proves
read-only source plus overlays insufficient.

Activation is blocked until the private implementation passes the byte-level
fixture contract for capability, acquire, status, and zero-residue release
responses against an isolated platform-debug runtime. That qualification is a
separate handoff and must prove backend-generation drift, quota/expiry denial,
cleanup after timeout, and absence of any writable source mount.

## Migration and feature gate

The capability is default-disabled and additive. Existing trusted-direct-bind
receipts retain their meaning and gain no writable authority. Existing backend,
workspace source mode, access profile, and execution lane remain independent.

## Rollback

Disable the WorkerBee development gate while leaving the general typed-resource
broker policy unchanged, reject new resources, ask WorkerBee to release exact
provider-owned overlays, and preserve request, validation, and cleanup receipts.
If provider state is ambiguous, mark recovery required and do not detach or
delete an unverified path.

## Security consequences

No Codex or daemon-wide host capability is added. Provider-generation drift,
unknown classes, writable source, non-rprivate mounts, unbound targets, or
missing cleanup evidence fail closed. Private implementation work requires a
separate decision-complete handoff.
