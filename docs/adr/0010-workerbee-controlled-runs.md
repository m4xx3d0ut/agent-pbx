# ADR 0010: PBX-controlled WorkerBee runs

- Status: Accepted for implementation
- Date: 2026-11-05

## Context

Trusted workstation Operators sometimes need an isolated validation lane that is
broader than their interactive Codex sandbox while remaining narrower than an
unrestricted host shell. The motivating K1s RC1 comparison requires immutable
baseline and candidate sources, working loopback and Unix sockets, denied
external egress, no ambient credentials, pinned tools, bounded resources, and a
verifiable cleanup result.

The selected platform-debug WorkerBee appliance already owns the normal
workspace and project lifecycle. Agent PBX owns durable Agent and Operator
identity, authority profiles, approvals, campaign leases, routing, and audit.
Moving this work to an unrelated host executor would split those responsibilities
and make the debug appliance behave differently from an ordinary WorkerBee
appliance.

## Decision

Agent PBX will add a typed `workerbee_controlled_run` action. Agent PBX validates
and persists the request, approval or bounded campaign lease, exact source and
profile identity, and final receipt. The selected WorkerBee backend executes the
request through its standard local workspace lifecycle.

The initial run profile is fail closed:

- source is an immutable committed-tree export;
- writes are limited to disposable run scratch;
- loopback TCP and Unix sockets are available;
- external egress, ambient credentials, and host control sockets are denied;
- commands come from a versioned recipe and pinned tool manifest;
- resource, time, output, and concurrency limits are explicit;
- cleanup and a structured cleanup receipt are mandatory.

Normal `local-deploy` sessions do not receive unrestricted
`workerbee_v1_exec`. Review forks cannot request or approve controlled runs.
Only a root Operator with activated `trusted-workbench` authority may request a
run for its owned edit fork. Public defaults require manual approval. A local
trusted-workstation policy may issue a bounded campaign lease tied to exact
workspace, backend generation, recipe, source, and profile digests.

The platform-debug appliance is the primary backend. A host-isolated fallback is
disabled by default and may be offered only after a nonmutating capability probe
identifies a precise appliance gap and the controller separately approves the
backend change. Fallback is never silent.

## Provenance and drift rules

Each request binds all of the following:

- root Operator and owned edit-fork identities;
- campaign and workspace-set identity;
- selected WorkerBee backend, instance, and generation;
- workspace binding;
- controlled-run profile ID, version, and SHA-256 digest;
- baseline and candidate Git object IDs;
- recipe ID and pinned tool-manifest digest;
- approval or campaign-lease identity.

Changing any bound value creates a new request. Expired, exhausted, revoked, or
drifted leases fail closed.

## State model

Requests move through:

```text
pending -> approved -> claimed -> provisioning -> canary -> running
        -> collecting -> cleaning -> succeeded
```

Terminal alternatives are `rejected`, `canceled`, `failed`, and `expired`.
Cancellation still enters cleanup when WorkerBee allocated a workspace.

## Consequences

- WorkerBee remains the runtime truth and execution surface.
- Agent PBX can recover approval and run state after daemon or TUI restart.
- The interactive Codex sandbox does not need to be widened or restarted.
- Appliance capability gaps are explicit and attributable.
- WorkerBee needs a narrow controlled-run workspace contract; generic exec is
  not an acceptable substitute.
- Local trusted-workstation behavior can remove repetitive approvals without
  weakening committed defaults.

## Rollback

Disable controlled runs, revoke open leases, request WorkerBee workspace
release, and preserve receipts and artifacts. The feature uses additive database
state and does not modify project source or existing WorkerBee gateway leases.
