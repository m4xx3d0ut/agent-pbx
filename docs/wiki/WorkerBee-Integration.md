# WorkerBee Integration

> **Applies to:** Agent PBX 2.1.x  
> **Last reviewed:** 2026-10-10

WorkerBee is an optional runtime-truth plane. Agent PBX remains the authority
for Agent/Operator identity, routing, lifecycle, policy, and audit. WorkerBee
adds project discovery, application and infrastructure status, simulation,
deployment evidence, ingress checks, and validation where configured.

## Enable read-only posture

Set the WorkerBee executable in private host configuration, restart the daemon,
and run doctor. The TUI never receives host credentials; the daemon invokes the
configured executable and returns normalized status.

```bash
agent-pbx doctor
agent-pbx mcp restart
```

The WorkerBee tab shows the selected Agent's project, mode, readiness, URLs,
workloads, latest deployment information, and validation findings. Refreshes
are serialized per Agent and cached briefly to avoid project-lock collisions.

## Backend and path diagnostics

Agent PBX reports:

- the configured WorkerBee executable and backend provenance;
- a versioned backend/switcher status when available;
- endpoint and server-identity agreement;
- the PBX host path, WorkerBee-visible workspace path, discovered repository
  root, and any mount/path translation;
- distinct states for an omitted tool allowlist, unavailable endpoint, and an
  older server.

A project that exists for Agent PBX but appears absent to WorkerBee is a path
visibility mismatch. Do not patch the WorkerBee server merely because a launch
allowlist omitted `workerbee_v1_session_start`. Correct the policy or path
mapping first.

## Tool policy

Direct Agents, root Operators, edit forks, and review forks can receive
`workerbee_v1_capabilities` and `workerbee_v1_session_start` when available.
Review forks retain read-only restrictions and do not receive mutation tools.
WorkerBee planner guidance should be used for runtime, manifest, ingress,
database, queue, security, deployment, and validation work.

Backend switching remains a separate live operation. Inspecting provenance and
path visibility is safe; changing the active MCP backend can affect running
Operators and requires explicit operational review.

