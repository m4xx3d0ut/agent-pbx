# WorkerBee Integration

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

WorkerBee is an optional runtime-truth plane. Agent PBX remains the authority
for Agent/Operator identity, routing, lifecycle, policy, and audit. WorkerBee
adds project discovery, application and infrastructure status, simulation,
deployment evidence, ingress checks, and validation where configured.

## Enable status posture

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

Direct callers preserve the user's global WorkerBee configuration by default.
For bounded local deployment work, Agent PBX can assign the caller metadata
profile `workerbee_mcp_profile=local-deploy`. That explicit profile approves
project discovery, image builds, manifest preparation and validation, local
manifest deployment, ingress route operations and probes, status/log access,
and project security review. It excludes remote k1s deployment, CA
regeneration, generic execution, and unrelated destructive operations.

The profile is part of the durable caller record. Restarting or resuming that
caller through the TUI re-emits the same narrow WorkerBee policy together with
the caller's Agent PBX report policy, identity, token environment, Codex thread,
cwd, and model profile. Unknown profile names fail closed. Removing the
metadata value returns future restarts to the normal global-config behavior.

Backend switching remains a separate live operation. Inspecting provenance and
path visibility is safe. To enable the guarded switch controls, configure both
versioned helpers in private host configuration:

```bash
export AGENT_PBX_WORKERBEE_SWITCHER_STATUS_BIN="$HOME/.local/bin/wb-mcp-status"
export AGENT_PBX_WORKERBEE_SWITCHER_BIN="$HOME/.local/bin/wb-mcp-switch"
agent-pbx mcp restart
```

Use `/workerbee switch oss` or `/workerbee switch debug` to open a preview. The
preview runs the switch helper's real `--dry-run`, binds the target and active
backend fingerprint to a private expiring transaction, and shows the exact
confirmation requirement. Platform-debug requires an additional privileged
confirmation. Agent PBX verifies backend provenance and MCP capabilities after
apply and attempts to restore the prior backend when verification fails.

`/workerbee switch rollback` reviews and rolls back the last verified
transaction. Existing Codex sessions remain pinned to the MCP binding they
started with; a switch selects the backend for future sessions. Operators
should therefore review active work before applying a transaction even though
Agent PBX does not rewrite running sessions.

The status helper and switch helper are separate contracts. Do not substitute
a command named `precheck` unless its implementation is proven observational;
the Agent PBX preview path intentionally calls only the switcher's true
non-mutating `--dry-run` mode.
