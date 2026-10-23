# WorkerBee Integration

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-23

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

## Choose a workflow posture

Agent PBX keeps four WorkerBee controls independent:

1. **Access** selects observe-only or bounded local build/deploy tools for one
   Agent or Operator. Review forks are always read-only.
2. **Workspace** controls whether the runtime may stay in its current checkout,
   move among worktrees, or bind another repository under approved roots.
3. **Content** controls which dirty, untracked, or ignored files may enter an
   appliance snapshot. It does not apply to the OSS direct-host backend.
4. **Backend** selects OSS, platform-debug, or a future managed user appliance.

The public default remains conservative: observe-only access, a pinned
workspace with controller approval, and tracked content. This is the normal
starting point for a new OSS or PaaS user. Appliance content policy does not
make OSS stricter because OSS reads the exact host checkout directly.

The setup assistant offers backend-neutral presets:

| Preset | Intended use | Workspace | Appliance content |
|---|---|---|---|
| `standard-oss` | New local user or direct-host OSS | Pinned, approval | Tracked |
| `personal-appliance` | Single-user appliance | Pinned, approval | Tracked |
| `trusted-platform-workbench` | Trusted engineering workstation | Scoped roots, automatic | Safe working tree |
| `high-assurance-platform` | Controlled or multiuser environment | Pinned, approval | Tracked; approved paths are the ceiling |

```bash
agent-pbx setup plan \
  --project-root "$HOME/git" \
  --workerbee-preset trusted-platform-workbench
agent-pbx setup apply \
  --project-root "$HOME/git" \
  --workerbee-preset trusted-platform-workbench
```

The preview must be reviewed before apply. A preset never switches the active
backend and never grants `local-deploy`. Use `Access` for explicit per-entity
authority and restart/resume that Codex session at a safe turn boundary after a
change. Administrators can make Access, Workspace, or Content read-only or hide
them while the daemon continues to enforce the configured ceilings.

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

Initialized Git submodules are flattened into the content-addressed snapshot
without copying `.git` metadata. Tracked submodule files are automatic. Dirty
or untracked submodule files require a private, host-owned allowlist bound to
the Agent ID and exact source checkout:

```bash
export AGENT_PBX_WORKERBEE_WORKSPACE_INCLUDES_JSON='{"agent-id":{"source_root":"/absolute/project","include_paths":["business-site/product-pages/agent-pbx"]}}'
```

Keep entries narrow. The controller rejects traversal, `.git`, malformed
selection, source-root drift, and more than 64 configured paths before it asks
WorkerBee to stage or replace a Codex process.

The staging helper records the source commit, dirty state, status digest,
content digest, backend generation, and appliance path. Reusing a binding is
allowed only when the appliance marker exactly matches that evidence. The
helper refuses the user-appliance backend until its separate image and volume
contract pass qualification.

### Refresh source without restarting Codex

A `local-deploy` session may update its source checkout after launch. Before a
build, manifest preparation, deployment, restart, or ingress mutation, repeat
`workerbee_v1_session_start`. Agent PBX compares the current checkout and its
host-approved includes with the lease's content-addressed binding. When content
changed, it stages and verifies a new platform-debug snapshot, then atomically
updates that same lease before forwarding `session_start` to WorkerBee.

The refresh preserves the Codex process and thread, gateway URL, bearer token,
backend selection, generation, project, and access profile. The prior binding
is retained for recovery. An unchanged checkout is a no-op. If staging or PBX
metadata reconciliation fails, the prior lease binding remains authoritative.

Other mutating tools never refresh implicitly. If the checkout changed since
the last binding, they return `WORKERBEE_WORKSPACE_REFRESH_REQUIRED` and direct
the caller to repeat `workerbee_v1_session_start`. This prevents a deployment
from silently using an older appliance snapshot. Observe and review profiles
remain fixed to their existing read-only snapshot, and OSS `direct_host`
sessions continue to use the exact host checkout without snapshot staging.

Source refresh does not authorize backend, generation, or access-profile
changes. Source-root and WorkerBee project changes are governed separately by
the daemon-owned workspace-mobility policy below.

## Appliance content policy

Appliance-backed sessions need a bounded rule for content that Git does not
carry in the tracked tree. `Content` in the WorkerBee tab and
`/workerbee content` expose that rule without exposing host secrets or changing
the selected backend.

| Policy | Snapshot behavior |
|---|---|
| `tracked` | Stage tracked repository and initialized submodule content only. |
| `approved-paths` | Also stage explicit host-approved relative paths. |
| `safe-working-tree` | Discover untracked and ignored files, exclude secret-sensitive names, links, special files, virtual environments, dependency caches, and oversized content, then stage the bounded remainder. |

`safe-working-tree` is intended for a trusted single-user engineering
workstation where generated site output, manifests, or other build inputs are
part of routine work. It remains bounded by file-count, total-byte, per-file,
source-root, and policy ceilings. High-assurance and multiuser installations
should retain `tracked` or explicit `approved-paths`.

The daemon owns the policy. Remote TUIs do not read local environment include
lists, and Agent-provided paths cannot expand the daemon-approved selection.
The legacy `AGENT_PBX_WORKERBEE_WORKSPACE_INCLUDES_JSON` allowlist remains a
compatibility input and is merged under the same limits.

```bash
export AGENT_PBX_WORKERBEE_SNAPSHOT_CONTROL_MODE="read_only" # editable|read_only|hidden
export AGENT_PBX_WORKERBEE_SNAPSHOT_POLICY_CEILING="approved-paths"
export AGENT_PBX_WORKERBEE_SNAPSHOT_ROOT_CEILING_JSON='["/srv/engineering"]'
export AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_BYTES="$((256 * 1024 * 1024))"
export AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_FILES="20000"
export AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_FILE_BYTES="$((64 * 1024 * 1024))"
```

The `user_appliance` backend remains unavailable until its managed-volume
contract is qualified. A configured platform-debug helper cannot accidentally
turn that deferred backend into a generic staging attempt.

## Workspace mobility and workbench policy

Agent PBX separates tool authority from workspace placement. Moving an Agent
or Operator to another approved checkout does not grant new WorkerBee tools.
The public default is `pinned` plus `approval`; it preserves the historical
single-checkout behavior until a controller deliberately changes policy.

| Policy | Meaning |
|---|---|
| `pinned` | The current canonical Git root; subdirectories are permitted. |
| `same-repository` | Any worktree whose Git common directory matches the current checkout. |
| `scoped-workbench` | Any canonical Git checkout beneath one of the daemon-owned allowed roots. |

Each policy has an independent request mode: `approval`, `automatic`, or
`disabled`. Under approval, the first `workerbee_v1_session_start(cwd=...)`
records a pending request and returns an actionable transition ID. A controller
can review and apply it from `Workspace` in the WorkerBee tab, after which the
Agent retries normally. Under automatic, an allowed request stages and binds
in the same tool call. Disabled mode refuses target changes.

Agent-supplied host paths never become appliance paths directly. The gateway
resolves the requested directory, discovers its canonical Git root and Git
common directory, calculates a root-relative `working_subpath`, applies owner,
symlink, policy, and administrator-ceiling checks, then sends only the selected
binding's runtime path to WorkerBee. A supplied project value is treated as a
scenario label and converted to a deterministic project namespace.

Every move records prior/new bindings, source/subpath, project namespace,
policy, request mode, actor, status, and timestamps. The previous receipt is
retained. Revert is allowed only when that transition still owns the active
binding and the old root remains permitted by current administrator policy.
Agent PBX never prunes these records as a side effect of binding.

The WorkerBee tab and `/workerbee workspace` or `/workerbee policy` expose
policy, preview, bind, pending approval, and revert controls. Environment
posture remains authoritative:

```bash
export AGENT_PBX_WORKERBEE_WORKSPACE_CONTROL_MODE="editable"
export AGENT_PBX_WORKERBEE_WORKSPACE_POLICY_CEILING="scoped-workbench"
export AGENT_PBX_WORKERBEE_WORKSPACE_ROOT_CEILING_JSON='["/srv/engineering"]'
export AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE="approval"
```

`CONTROL_MODE` may be `editable`, `read_only`, or `hidden`. A root ceiling is
a JSON list of existing directories. `REQUEST_MODE`, when set, overrides every
stored global and per-entity value. The daemon enforces these settings even if
a client hides or modifies its own TUI state.

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

Controllers can inspect or change this durable profile through `Access` in the
WorkerBee tab or `/workerbee access`. `Inspect` stores `observe-local`; `Build +
Deploy` stores `local-deploy`. The daemon can constrain or hide the control:

```bash
export AGENT_PBX_WORKERBEE_ACCESS_CONTROL_MODE="read_only" # editable|read_only|hidden
export AGENT_PBX_WORKERBEE_ACCESS_PROFILE_CEILING="observe-local" # or local-deploy
```

The new profile takes effect only after an Agent PBX restart/resume of that
entity. Agent PBX does not rewrite a running Codex process or mint broader tools
inside the current lease.

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
