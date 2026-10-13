# WorkerBee Integration

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-13

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

## Runtime domains and session binding

Agent PBX separates three concepts that must not be conflated:

1. **Access profile:** `observe-local`, `review-readonly`, or `local-deploy`.
2. **Selected backend:** `oss`, `platform_debug`, or the deferred
   `user_appliance` contract.
3. **Workspace binding:** the exact source revision and path visible to that
   backend.

Only one backend endpoint is selected at a time. Dormant state may remain in
each backend's private domain, but Agent PBX does not run a second backend as a
fallback or merge its project registry into the active backend.

| Backend | Workspace strategy | Availability |
|---|---|---|
| `oss` | Direct, exact host Git root | Qualified |
| `platform_debug` | Content-addressed snapshot in `/workspace/imports/<binding>` by default | Qualified |
| `user_appliance` | Separate managed appliance volume | Reserved and unavailable until its image is qualified |

At launch or resume, Agent PBX prepares a workspace receipt, issues a private
gateway lease, and configures the Codex process to use that lease endpoint. The
lease records backend instance, generation, fingerprint, project, profile, and
workspace binding. The raw bearer token exists only in the child process
environment. Entity metadata stores nonsecret IDs and provenance.

A completed backend switch advances the generation. Calls from an older lease
then fail with `WORKERBEE_BACKEND_REBIND_REQUIRED`; Agent PBX does not silently
retarget the session. Restart or resume the affected Agent or Operator through
the TUI at a safe turn boundary to create a fresh workspace and lease.

For platform-debug, configure the materializer in private host configuration:

```bash
export AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN="$(command -v agent-pbx-workerbee-workspace)"
export AGENT_PBX_WORKERBEE_DEBUG_STAGE_PYTHON="/path/to/k1s-workerbee-private/.venv/bin/python"
export AGENT_PBX_WORKERBEE_DEBUG_STAGE_SCRIPT="/path/to/k1s-workerbee-private/scripts/dev/stage_platform_debug_workspace.py"
export AGENT_PBX_WORKERBEE_DEBUG_CONTAINER="workerbee-mcp-debug"
export AGENT_PBX_WORKERBEE_DEBUG_IMPORT_ROOT="/workspace/imports"
```

The staging helper records the source commit, dirty state, status digest,
content digest, backend generation, and appliance path. Reusing a binding is
allowed only when the appliance marker exactly matches that evidence. The
helper refuses the user-appliance backend until its separate image and volume
contract pass qualification.

## Tool policy

Direct Agents, root Operators, edit forks, and review forks can receive
`workerbee_v1_capabilities` and `workerbee_v1_session_start` when available.
Review forks retain read-only restrictions and do not receive mutation tools.
WorkerBee planner guidance should be used for runtime, manifest, ingress,
database, queue, security, deployment, and validation work.

Without a configured backend selector, direct callers preserve the user's
global WorkerBee configuration. With a selected runtime, Agent PBX injects the
generation-bound gateway configuration. For bounded local deployment work,
Agent PBX can assign the caller metadata profile
`workerbee_mcp_profile=local-deploy`. That explicit profile approves
project discovery, image builds, manifest preparation and validation, local
manifest deployment, ingress route operations and probes, status/log access,
and project security review. It excludes remote k1s deployment, CA
regeneration, generic execution, and unrelated destructive operations.

The profile is part of the durable caller record. Restarting or resuming that
caller through the TUI re-emits the same narrow WorkerBee policy together with
the caller's Agent PBX report policy, identity, token environment, Codex thread,
cwd, and model profile. Unknown profile names fail closed. Removing the
metadata value returns future restarts to the normal global-config behavior.

With a selected runtime, ordinary Agents and root/edit Operators receive
`observe-local`, review forks receive `review-readonly`, and only entities with
the durable `workerbee_mcp_profile=local-deploy` policy receive the bounded
deployment profile. Pre-registration launch candidates may obtain observe or
review access; they cannot obtain deployment authority. The gateway excludes
remote k1s deployment, CA regeneration, generic execution, and tools outside
the selected profile.

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
transaction. A switch is rejected while any gateway mutation is active. It
does not rewrite a running Codex process, but the generation change invalidates
its old lease so the process cannot accidentally mutate the newly selected
backend. Operators should restart or resume affected sessions through Agent PBX
after reviewing active work.

Run `agent-pbx doctor` after configuration or a switch. The
`workerbee-runtime-binding` check reports the selected backend generation,
instance, workspace strategy, and platform-debug materializer prerequisites.

The status helper and switch helper are separate contracts. Do not substitute
a command named `precheck` unless its implementation is proven observational;
the Agent PBX preview path intentionally calls only the switcher's true
non-mutating `--dry-run` mode.
