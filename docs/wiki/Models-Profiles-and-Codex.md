# Models, Profiles, and Codex

> **Applies to:** Agent PBX 2.6.x
> **Last reviewed:** 2026-11-18

Agent PBX treats the live Codex model catalog as the availability authority and
its managed profiles as the qualification, policy, approval, and rollback
layer. The native Codex `/model` picker remains available, including account or
provider models that are newer than the Agent PBX profile registry.

## Committed defaults

The public defaults are intentionally portable and moderate:

| Role or task | Managed profile | Purpose |
| --- | --- | --- |
| Caller Agent | `sol-6.2-medium` | Everyday coding and engineering work |
| Root Operator | `sol-6.2-high` | Cross-project coordination and review |
| Edit fork | `sol-6.2-high` | Bounded implementation under its root Operator |
| Review fork | `sol-6.2-high` | Read-only review with deeper synthesis |
| Efficient task | `luna-6-high` | Focused, repeatable, or high-volume work |
| Visual task | `terra-6.1-high` | Visual understanding and computer-use work |

Sol 6.2 max and ultra plus Astra 6.2 xhigh, max, and ultra are committed
elevation choices. Max and ultra require a bounded approval lease. Ultra also
requires observed Codex subagent capability because it uses delegated
execution.

Compatibility profiles preserve exact Sol 5.6, Terra 5.6, and Codex 5.5
metadata for old session resume, audit, and rollback. They are migration-only
and cannot become a new Agent, Operator, or shared default. Starred state is a
lifecycle and retention attribute. Starring an entity never changes its model.

## Workstation-local policy

An owner-only local policy can raise or specialize role defaults without
changing the repository defaults. The normal path is
`~/.config/agent-pbx/model-policy.toml`, or the path named by
`AGENT_PBX_CODEX_MODEL_POLICY_FILE`.

```toml
control_mode = "guard_elevated"

[defaults]
caller = "sol-6.2-high"
operator = "sol-6.2-xhigh"
edit_fork = "sol-6.2-xhigh"
review_fork = "sol-6.2-xhigh"

[tasks]
efficient = "luna-6-high"
visual = "terra-6.1-high"

[elevations]
allowed = [
  "sol-6.2-max",
  "sol-6.2-ultra",
  "astra-6.2-xhigh",
  "astra-6.2-max",
  "astra-6.2-ultra",
]
```

Keep this file mode `0600`. Environment overrides are also supported for the
control mode, role defaults, task profiles, and comma-separated elevation
profile list:

- `AGENT_PBX_CODEX_MODEL_POLICY_CONTROL_MODE`
- `AGENT_PBX_CODEX_MODEL_CALLER_PROFILE`
- `AGENT_PBX_CODEX_MODEL_OPERATOR_PROFILE`
- `AGENT_PBX_CODEX_MODEL_EDIT_FORK_PROFILE`
- `AGENT_PBX_CODEX_MODEL_REVIEW_FORK_PROFILE`
- `AGENT_PBX_CODEX_MODEL_EFFICIENT_PROFILE`
- `AGENT_PBX_CODEX_MODEL_VISUAL_PROFILE`
- `AGENT_PBX_CODEX_MODEL_ELEVATION_PROFILES`

An invalid local value falls back to its committed default and produces a
warning. Strict validation is available to diagnostics and tests. This keeps a
stale override from making the daemon or TUI unstartable after a Codex catalog
change.

## Native `/model` integration

Use `/codex model native` from the Agent PBX palette to open Codex's native
picker in the selected managed terminal. Agent PBX observes the resulting
`thread/settings/updated` event and may hydrate the current selection with a
read-only `thread/read` query. The Codex tab reports the exact model, reasoning
effort, provider, observation source, policy result, and one of these classes:

| Classification | Meaning |
| --- | --- |
| `managed_qualified` | Exact model and effort match a versioned PBX profile |
| `catalog_observed` | Present in the live Codex catalog but not yet a PBX profile |
| `custom_provider_observed` | Reported by a configured non-OpenAI provider |
| `catalog_stale` | Catalog evidence is missing or too old for qualification |
| `unknown` | Selection cannot be reconciled with current evidence |

`/codex model inspect` refreshes this view. `/codex model adopt` stores a safe
managed or live-catalog choice in the selected entity's resume metadata. It
does not change repository defaults, workstation role defaults, or the global
Codex configuration. `/codex model return` restarts and resumes the entity at
its current PBX role default.

Direct max or ultra selections remain visible but cannot be adopted as the
entity resume profile without an approved elevation lease. Custom-provider,
stale-catalog, and unknown selections remain observational until explicitly
qualified.

## Control modes

| Mode | Behavior |
| --- | --- |
| `observe` | Record direct choices without PBX policy gating |
| `guard_elevated` | Permit live catalog choices while requiring leases for elevated effort |
| `enforced` | Require a managed qualified profile or an approved elevation lease |

The committed and trusted-workstation defaults use `guard_elevated`. This
allows normal Codex catalog use while retaining a human review boundary around
max and ultra.

## Elevation leases

An elevated profile requires a human-approved justification and a bounded
lease. The requesting Agent or Operator cannot self-approve. Palette and Codex
tab requests converge on one idempotent lease, so repeated input cannot open a
second approval or restart an already elevated runtime. The Codex status
surface shows active and pending-return leases.

One-task completion and lease expiry move an active lease to
`revert_pending`. Agent PBX waits for a high-confidence app-server, hook, or
transcript boundary, restarts and resumes the same Codex session under the
prior profile, and closes the lease only after the effective model and
reasoning effort are verified. Ambiguous runtime evidence leaves an actionable
pending-return state. Launch and verification failures retain an audit record.

## Catalog and migration diagnostics

Agent PBX validates managed choices against `codex debug models`. A configured
slug or effort missing from the current CLI produces a warning and is never
silently replaced. Capability evidence is cached by executable, version,
profile, and host, then invalidated when the Codex executable changes.

The Codex row in `agent-pbx doctor` shows the host's configured model default
and the size of the host catalog. For the effective model and reasoning effort
of a particular running session, select that entity in the TUI and use
`/codex model inspect`. The session can be using a role profile, an
entity-local override, or a selection made in Codex's native `/model` picker.

Existing entities keep their explicit model metadata until an operator chooses
a managed restart, adopts a direct catalog choice, or returns the entity to its
role default. A role-policy update does not interrupt or silently rewrite a
running Codex session.

## Managed global configuration

The Codex tab and `/codex config` edit a reviewed set of supported scalar
settings. Agent PBX uses atomic backup/restore and preserves unrelated values.
Authentication credentials, provider secrets, model provider tables, telemetry
keys, arbitrary nested tables, and administrator-enforced requirements remain
hidden or read-only.

Opaque secret fields can be replaced without revealing the stored value. The
UI displays state such as present, absent, or managed and never reads a secret
back into the editor after save.

## Themes and terminal mode

Install or inspect the generated Codex syntax theme with:

```bash
agent-pbx codex theme status
agent-pbx codex theme install
```

The PBX theme controls the outer semantic state; the Codex theme controls code
and diff syntax supported by the installed CLI. Managed profiles keep tmux as
scrollback owner. `/codex terminal scrollback`, `/codex terminal raw`, and
`/codex terminal default` expose the supported posture from the TUI.

## Skills and personality

The committed personality is a generic Roses Architect profile: direct,
professional, technically precise, and architect-first. Host-specific identity
or relationship text belongs in a private local overlay. The TUI may show that
an overlay is active and its hash/version, but shared diagnostics and project
files must not expose its text.

Managed skill packs use manifests, hashes, preview/apply/rollback, drift
detection, and context-budget checks. Project-authored files outside managed
markers are not overwritten.
