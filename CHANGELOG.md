# Changelog

## Unreleased

## v2.6.0rc2 - 2026-11-18

### Fixed

- Require trusted provider provenance and current live-catalog evidence before
  a familiar model slug can be classified as a managed qualified profile or
  adopted into entity-local resume metadata.
- Reclassify managed-looking choices from custom providers, stale or empty
  catalogs, and unsupported live catalog entries as observational evidence
  only. Even an active elevation lease cannot make those choices adoptable.
- Revalidate provider and catalog evidence at adoption time so an observation
  recorded before catalog invalidation cannot cross the managed policy
  boundary later.
- Canonicalize legacy Sol 5.6 elevation aliases when leases are requested and
  when retained pre-v2.6 leases are matched to an observed runtime profile.

### Qualification

- Add focused provider-spoofing, catalog-invalidation, unsupported-selection,
  elevated-adoption, and retained-lease compatibility coverage in response to
  the independent RC1 policy review.
- Keep the v2.6 database schema, role defaults, direct Codex `/model` picker,
  WorkerBee policy, tmux topology, and migration behavior unchanged.

## v2.6.0rc1 - 2026-11-18

### Added

- Add exact, versioned managed profiles for Sol 6.2, Astra 6.2, Luna 6, and
  Terra 6.1 with live-catalog validation, capability requirements, role hints,
  and explicit migration fallbacks.
- Add a layered model policy with safe committed defaults and an owner-only
  workstation override for caller, root Operator, edit-fork, review-fork,
  efficient-task, visual-task, and bounded-elevation profiles.
- Integrate Codex's native `/model` picker with structured
  `thread/settings/updated` observation, read-only thread hydration, selection
  classification, entity-local adoption, and return-to-role-default actions.
- Add managed Sol 6.2 max/ultra and Astra 6.2 xhigh/max/ultra elevation choices.
  Ultra profiles carry explicit delegated-execution and subagent capability
  requirements.

### Changed

- Set the committed caller default to Sol 6.2/medium and the committed root
  Operator, edit-fork, and review-fork defaults to Sol 6.2/high.
- Use Luna 6/high for efficient repeatable work and Terra 6.1/high for visual
  or computer-use work. Terra is no longer treated as the low-cost task lane.
- Update the isolated public demo and Codex Operator UAT defaults to the Codex
  0.169.0 and Sol 6.2 profile generation.
- Derive built-in palette collision protection from the reviewed command
  catalog and extract model command actions behind a dedicated UI module.

### Security

- Keep direct catalog selections observable without silently changing shared
  defaults. Entity-local adoption accepts only managed or current live-catalog
  choices; custom-provider, stale-catalog, and unknown choices remain
  observational.
- Require an exact, unexpired, matching approved or active lease before an
  elevated direct choice can be adopted. Unmatched, expired, and arbitrary
  elevated selections fail closed.
- Keep Sol 5.6, Terra 5.6, and Codex 5.5 profiles migration-only so retained
  sessions remain resumable without allowing those profiles to become new
  launch or shared-default choices.
- Keep starred state independent from model policy. Starring or unstarring an
  Agent or Operator never changes its model.

## v2.5.0 - 2026-11-18

### Highlights

- Promote the complete v2.5 runtime-reconciliation and component-boundary
  cycle after RC4 passed local qualification, Linux and macOS on Python 3.10
  and 3.12, TestPyPI Trusted Publishing, registry-hash verification, and clean
  installation from the public test index.
- Add independently governed WorkerBee Operator profiles, path-domain evidence,
  exact readiness-bound terminal delivery, caller rehydration, explicit direct
  caller campaigns, explainable Operator workflow projection, and MCP tool
  inventory freshness.
- Preserve the native managed-tmux experience while hardening disposable TUI
  host liveness, writer leases, process identity refresh, recursive-launch
  rejection, and cross-platform run-marker recovery.

### Changed

- Decompose API, Textual presentation, persistence, WorkerBee projection, and
  restart preparation behind typed ownership boundaries with executable
  dependency, growth, OpenAPI, route, Store, schema, and TUI compatibility
  contracts.
- Advance the additive database contract from schema 45 to schema 46 for
  explicit campaign-delivery topology. Existing assignments retain the prior
  `operator_fork` behavior.
- Correlate observed Codex children with native `/subagents` identifiers while
  keeping runtime availability separate from durable completion evidence.

### Security

- Keep terminal delivery exact-target, input-ready, writer-lease-bound, and
  independent from durable report history. Prompt bodies and terminal content
  remain excluded from delivery audit records.
- Keep WorkerBee backend, access profile, gateway lease, filesystem, Git,
  provider, deployment, publication, and credential authority independently
  governed; discovery and reconciliation cannot raise a configured ceiling.
- Keep caller rehydration, direct-caller campaigns, runtime restart, and TUI
  ownership fail closed across stale identities, ambiguous sessions, invalid
  roots, recursive attachment, and mismatched capability evidence.

Stable 2.5.0 is derived from the qualified RC4 product tree by release identity
and documentation only. No semantic production change was added after RC4.

## v2.5.0rc4 - 2026-11-18

### Added

- Add preview-token-bound rehydration for an existing caller identity that has
  no managed runtime mapping. The initial safe mode starts a fresh independent
  Codex session in the caller's persisted project while preserving stars,
  project ownership, durable history, and PBX identity; historical shared
  sessions are evidence only and are never resumed implicitly.
- Add an explicit `direct_caller` campaign-delivery topology for root Operators
  that intentionally target an existing caller without creating an Operator
  fork. The existing `operator_fork` topology remains the default and topology
  cannot be switched by a follow-up.
- Publish a deterministic manifest of the exact FastMCP tool contract and stamp
  its digest into new managed/TUI Codex launches and durable runtime evidence.
  Agent summaries now distinguish `current`, `stale`, `unknown`, `no_runtime`,
  and `unavailable` inventories.

### Changed

- Advance the additive database contract from schema 45 to schema 46 for the
  persisted campaign delivery topology. Existing assignments migrate to the
  unchanged `operator_fork` behavior.
- Project Operator `State`, `Flow`, and `Camp` from explicit primary and
  secondary evidence, timestamps, subject IDs, and precedence reasons. A newer
  active campaign can supersede an unrelated older terminal report without
  deleting either durable record.
- Reconcile campaigns after dispatch so all-terminal assignments close the
  campaign instead of leaving a stale running projection; unresolved completion
  receipts and other durable review boundaries continue to take precedence.
- Mark a proven MCP manifest mismatch as restart-required. Pre-RC4 sessions
  without launch provenance are reported as unknown with a restart
  recommendation, never as a proven mismatch.
- Derive the WorkerBee launch inventory from the effective access profile and
  reconcile platform-debug appliance discovery into stable profile/tool
  projections. Platform cloud tools are classified for all appliance-capable
  profiles, while `workerbee_v1_exec` remains restricted to the trusted
  workbench profile.
- Reconcile a session's durable WorkerBee profile intent, active gateway lease,
  backend generation, and live launch projection before restart. Stale leases
  are revoked and replaced without broadening the configured profile ceiling.
- Extract WorkerBee controller metadata, projection, lease reconciliation, and
  restart preparation behind dedicated service boundaries while retaining the
  existing API and persistence contracts.

### Security

- Keep caller rehydration fail closed across existing runtime mappings,
  conflicting tmux sessions, stale preview tokens, invalid project roots,
  unsupported profiles, and unobserved Codex fork capability. Persistence
  failure removes only the disposable newly created session.
- Keep direct-caller campaigns root-Operator-only and retain exact-target,
  runtime-readiness, writer-lease, workspace, and terminal-delivery checks.
- Treat MCP inventory state as provenance rather than authority: it does not
  grant tools, restart sessions automatically, mutate live mappings, or infer
  that legacy sessions are stale without a recorded launch digest.
- Reject recursive TUI launches when the parent terminal cannot remain alive to
  own the disposable client, while allowing the explicitly isolated managed
  web-demo stdio boundary.
- Preserve profile ceilings during appliance-tool discovery and lease refresh;
  runtime reconciliation cannot promote a caller or Operator beyond its
  configured WorkerBee authority.

### Fixed

- Preserve active TUI run markers on macOS and other hosts without Linux
  process-start ticks by falling back to a non-signalling PID liveness probe;
  stale markers are still reaped when the process is absent.
- Normalize version-specific empty Python AST fields in the component-boundary
  checker so one TUI source tree retains one compatibility identity across the
  supported Python 3.10 and 3.12 release matrix.
- Deliver direct-caller campaign prompts through the same exact managed-runtime,
  process, session, workspace, input-readiness, and writer-lease contract used
  by Operator targets.
- Refresh PID and process-start identity after a managed pane restart so the
  replacement runtime is not evaluated against stale process evidence.
- Refresh WorkerBee authority immediately before caller restart so the resumed
  Codex process receives the current backend generation, gateway lease, access
  profile, and allowed tool inventory instead of a stale adoption-time launch
  surface.

## v2.5.0rc3 - 2026-11-17

### Fixed

- Run the host-terminal watchdog from the production recorded TUI entrypoint,
  not only the direct compatibility entrypoint. An orphan interactive client
  now releases only its exact writer lease and terminates without stopping,
  signaling, remapping, or restarting its managed Codex runtime.
- Separate current Codex child availability from retained lifecycle history.
  A child reported as `notLoaded`, idle, unavailable, or no longer refreshed
  is rendered as stale instead of remaining in the active count because its
  last known lifecycle state was thinking or executing.
- Preserve Codex child nicknames and spawn depth in the bounded PBX runtime
  projection so the right-pane topology can be correlated with Codex's native
  `/subagents` surface by nickname plus abbreviated thread ID.
- Refresh unchanged child availability in memory without appending redundant
  runtime-journal events. If app-server observation stops, the freshness
  window expires and the child fails closed to stale.

### Security

- Keep subagent display state observational: RC3 adds no child steering,
  prompt-body capture, hidden-reasoning exposure, authority inference, custom
  profile claim, model override, concurrency change, or automatic repair.
- Preserve exact TUI host identity, writer-lease ownership, recursive-attach
  protections, managed runtime identity, and all Git, WorkerBee, provider,
  credential, publication, review-fork, and remote-client authority boundaries.

## v2.5.0rc2 - 2026-11-17

### Fixed

- Separate immutable campaign/report projection from current Codex interaction
  readiness. A terminal or `needs_input` report no longer vetoes delivery when
  the exact managed runtime, process, workspace, idle composer, and writer lease
  all remain valid.
- Bind terminal-delivery receipts to the runtime mapping generation and include
  bounded metadata-only readiness provenance. Current non-report interruption,
  stale generation/session/process/workspace/authority evidence, overlays, and
  writer conflicts continue to fail closed with stable reason codes.
- Add isolated Operator-0 and Operator-6 regression fixtures for late reports,
  restart replay, duplicate suppression, and terminal-history preservation
  without using live managed panes or sockets.
- Bind an interactive TUI writer lease to the exact host terminal and parent
  process that launched it. If either identity disappears or changes, only the
  orphan embedded client is detached, its writer lease is released, and that
  TUI exits without stopping, signaling, restarting, or remapping the managed
  runtime. Noninteractive pilot and test launches remain unaffected.
- Clear stale transient live-validation metadata when a successful in-place
  restart registers fresh PID/start evidence, while preserving the durable
  reconciliation and registration event history. Writer-lease conflicts now
  identify the read-only fallback without implying a runtime restart.

### Security

- Keep report-mode delivery exact-target-only with no queue fallback, automatic
  restart/remap, prompt suppression, review-fork broadening, or change to Git
  broker, WorkerBee, provider, credential, publication, or remote-client
  authority.
- Keep prompt bodies and terminal captures out of delivery receipts and audit
  records; readiness evidence contains only bounded state, source, timestamp,
  reason, and target-identity metadata.
- Prevent a detached or replaced host terminal from renewing or reacquiring a
  writer lease. The liveness correction never signals or inspects a live
  managed Codex PID and does not change multi-client read-only semantics.

## v2.5.0rc1 - 2026-11-17

### Highlights

- Complete the first behavior-preserving component-decomposition boundary for
  the API, TUI, and persistence layers while retaining the public route,
  OpenAPI, Store, schema, transaction, authorization, and migration contracts.
- Add independently governed WorkerBee Operator profiles and exact path-domain
  lease evidence without coupling WorkerBee access to Codex, filesystem, Git,
  broker, provider, deployment, publication, or credential authority.
- Add typed, restart-safe terminal delivery that proves exact managed-runtime,
  process, workspace, idle-input, and writer-lease evidence before injection.

### Added

- Add a manual, exact-tag recovery workflow that verifies already-published
  PyPI files and restores prebuilt GitHub Release assets without receiving
  OIDC publication authority.
- Add receipt-bearing, idempotent Operator terminal delivery with typed
  readiness outcomes and exact managed-runtime identity evidence.
- Add bounded, user-private TUI run evidence with start/exit events,
  PID/start-time-qualified active markers, stale-run recovery, and redacted
  per-run stderr and exception traces. The disposable TUI journal remains
  independent from managed tmux and Codex runtime ownership.

### Changed

- Split FastAPI route families, Textual screens/controllers, and SQLite
  repositories behind typed composition boundaries with executable no-growth,
  import-direction, OpenAPI, TUI, Store, schema, and transaction guards.
- Disable Codex startup update checks explicitly for managed caller, root
  Operator, edit-fork, review-fork, restart, and resume launch policies.
- Resolve new root Operators through a daemon-owned WorkerBee profile default
  and independent access ceiling. The committed default remains
  `local-deploy`; trusted-workstation overlays may select `trusted-workbench`
  only under an equal or broader ceiling. Caller Agents remain `local-deploy`,
  edit forks inherit the root Operator's effective profile, and review forks
  remain read-only.

### Fixed

- Retry clean package-index installation while a newly published release is
  propagating from PyPI metadata to the Simple API.
- Link the stable wheelhouse, checksum, and installer directly from the v2.4.0
  release notes so they remain discoverable when GitHub collapses its Assets
  section.
- Separate Git modal-routing tests from pane-refresh scheduling so background
  terminal refreshes cannot create nondeterministic release-gate failures.
- Refuse report-mode delivery unless the exact Codex session, managed tmux
  target, process start identity, workspace set, runtime readiness, idle input
  prompt, and writer lease all remain valid through injection. Explicit nohup
  targets retain queue fallback; startup, model, update, and thinking overlays
  return typed non-success outcomes.
- Preserve the exact Textual WebDriver across both hardened public-demo
  environment sanitization boundaries without accepting arbitrary driver
  import paths.
- Cover selected native root-Operator restart, explicit embedded-client
  detach, Agents-pane refocus, and disposable tmux-client replacement without
  changing or terminating the managed Codex runtime identity.

### Security

- Keep public WorkerBee access at `local-deploy`, force review forks to
  `review-readonly`, and require daemon-resolved ceiling plus exact
  workspace-policy evidence before a root Operator or edit fork can use
  `trusted-workbench`.
- Keep report-mode terminal delivery fail closed across stale session, pane,
  process, workspace, authority, readiness, overlay, and writer-lease evidence;
  queue fallback remains limited to explicit nohup targets.
- Preserve exact Git broker approval and receipt authority independently from
  WorkerBee access, terminal delivery, component ownership, and release
  workflows.

## v2.4.0 - 2026-11-13

### Highlights

- Promote the complete v2.4 durable engineering control-plane boundary after
  the immutable RC2 tree passed Linux and macOS qualification on Python 3.10
  and 3.12, TestPyPI Trusted Publishing, registry-hash verification, clean
  installation, and managed workstation activation.
- Add exact Git review artifacts, private evidence export, Operator KB bundle
  v2 synchronization, content-addressed durable helpers, typed dynamic
  resources, and capability-gated WorkerBee development-resource contracts.
- Keep all new high-authority engineering subsystems additive,
  default-disabled, receipt-bound, and independently constrained from Agent,
  Operator, WorkerBee backend, provider, deployment, publication, and
  credential authority.

### Changed

- Advance the additive database contract from schema 40 to schema 45 while
  preserving existing Agents, Operators, forks, stars, campaigns, handoffs,
  Codex sessions, managed tmux mappings, and compatibility surfaces.
- Define Operator `State`, `Flow`, `Camp`, and `Live` as separate durable
  workflow and runtime projections, with event-driven refresh and explicit
  reason, source, and subject evidence.

### Security

- Retain positive-field evidence export, private output roots, exact-path Git
  review, content-addressed helper validation, bounded resource classes,
  capability and generation binding, immutable receipts, and zero-residue
  cleanup.
- Permit a shared-write Python executable as the durable-helper validator only
  when it resolves to the interpreter already running Agent PBX. Separately
  configured interpreters retain the strict no-group-or-world-write rule.

Stable 2.4.0 is derived from the qualified RC2 product tree by release identity
and documentation only. No semantic production change was added after RC2.

## v2.4.0rc2 - 2026-11-13

### Fixed

- Permit the already-running Agent PBX Python interpreter to serve as the
  isolated durable-helper validator when a supported distribution installs
  that interpreter with a group-writable tool-cache mode. Separately
  configured validator interpreters retain the strict no-shared-write check.
- Restore Linux/Python 3.12 release qualification without weakening exact
  validator-path resolution, regular-file and owner-execute checks, or the
  before/after executable-identity comparison.

## v2.4.0rc1 - 2026-11-13

### Highlights

- Add a durable engineering control-plane layer for exact Git review,
  redacted evidence export, Operator knowledge synchronization, reusable
  helper bundles, typed development resources, and bounded WorkerBee
  development overlays.
- Keep the new subsystems additive and default-disabled, with root-Operator
  ownership, explicit approval, immutable receipts, bounded leases, and
  feature-local rollback.
- Make Operator workflow state explainable and event-driven without coupling
  `State`, `Flow`, or `Camp` to raw Codex terminal activity.

### Added

- Add exact-leaf Git review artifacts, daemon-owned evidence exports, Operator
  KB bundle-v2 synchronization, a content-addressed durable helper registry,
  a typed dynamic-resource broker, and a capability-gated WorkerBee
  platform-debug development-resource contract.
- Add isolated restart/recovery qualification across receipt-bound approval,
  WorkerBee resource truth, redacted evidence export, and zero-residue cleanup.
- Add metadata-only protected-provider preflight diagnostics that bind exact
  Git, workspace, campaign, approval, and opaque provider-generation evidence
  without granting provider, credential, publication, or mutation authority.

### Changed

- Advance the additive database contract from schema 40 to schema 45 while
  preserving existing Agent, Operator, campaign, handoff, runtime, and policy
  records through migration and rollback.
- Keep WorkerBee development-resource support behind a checked capability
  contract and a separately qualified private adapter; trusted-direct source
  remains read-only and writable overlays remain receipt-bound.
- Define Operator columns consistently: `State` is the projected PBX workflow,
  `Flow` is its highest-priority durable route or action, `Camp` is the active
  nonterminal campaign count, and `Live` is tmux/runtime liveness.

### Fixed

- Refresh Operator workflow projections for all durable workflow event
  families, display stale state before an underlying running/waiting report,
  and expose the selected workflow reason, source, and subject identity.
- Preserve authority-qualified per-worktree Git administration as an exact
  filesystem exception for Trusted Workbench edit launches without projecting
  a nested workspace root or exposing sibling/common Git administration.
- Match durable event `subject_id` during filtered evidence-case export so
  real lifecycle and resource events remain visible without exporting payloads.
- Persist exact broker approval lifecycle and terminalize failed broker
  actions so reconciled successor campaigns can close without rewriting their
  immutable predecessor history.

### Security

- Retain positive-field evidence exports, private output roots, bounded diff
  rendering, exact-path review, content-addressed helpers, fail-closed
  capability and generation checks, and zero-residue resource cleanup.
- Keep high-risk network, host-socket, device, credential, provider,
  publication, deployment, and destructive-operation authority outside the
  automatic resource path.

## v2.3.0 - 2026-11-12

### Highlights

- Add durable, source-addressed Operator handoff completion receipts and
  clearer workflow, live-runtime, and alert state in the TUI.
- Add versioned Operator execution authority, exact workspace sets, bounded
  delegation leases, and Trusted Workbench edit-fork inheritance while
  retaining conservative public defaults and immutable review forks.
- Add a local-only, root-supervised exact Git checkpoint broker for qualified
  stopped edit forks whose split policy cannot safely receive direct Git
  administration grants.
- Separate WorkerBee backend, source mode, access profile, execution lane,
  workspace mobility, and snapshot-content policy into explicit,
  independently constrained controls.
- Advance the additive database contract from schema 30 to schema 40 with
  backup, dry-run, verification, and rollback qualification that preserves
  Agents, Operators, forks, stars, campaigns, sessions, and runtime mappings.

### Changed

- Make managed stop, purge, restart, resume, and reconciliation require the
  complete tmux executable, socket, server, session, window, pane, process,
  and Codex-session identity before any mutation.
- Project immutable terminal reports into campaign assignment completion and
  close campaigns only when every bound assignment is terminal.
- Keep Agent PBX, Codex, tmux, and WorkerBee responsibilities explicit:
  Agent PBX owns durable control-plane state, Codex owns reasoning and
  execution, tmux owns terminal persistence, and WorkerBee remains an
  optional runtime-truth plane.

### Fixed

- Pass the public-demo bearer token to the isolated daemon as one
  `--token=<value>` argument, so a cryptographically generated token beginning
  with `-` cannot be parsed as another command-line option.
- Preserve the last valid native terminal frame across Agent, Operator,
  Joplin, and F4 transitions; reject stale asynchronous attach results; and
  use compatibility capture only after a genuine attach failure.
- Keep Joplin targeted write/readback health independent from strict
  whole-profile E2EE validation and reuse an existing target retry instead of
  duplicating a write job.
- Preserve authority-qualified linked-worktree Git roots without promoting
  them to broader Codex workspaces, and preserve exact ordered Git parents and
  reviewed trees through broker preflight, execution, receipt, and recovery.
- Reconcile missing inactive runtimes and complete exact managed stops without
  adopting, signaling, or deleting a distinct replacement pane.
- Close the final cross-platform RC fixture gaps without changing production
  trusted-direct admission, F8 alert, or approval behavior.

Stable 2.3.0 contains the reviewed RC4 product tree plus the isolated
public-demo token argument repair found by the first stable branch matrix.
The repaired RC5 tree passed the exact-tag GitHub matrix, TestPyPI Trusted
Publishing, registry hash verification, and a clean install before this
stable identity was derived. No stable tag, GitHub Release, or PyPI artifact
was created from the failed gate. See the RC1 through RC4 entries below for
the complete implementation history.

## v2.3.0rc4 - 2026-11-12

### Fixed

- Make trusted-direct-bind test identity deterministic across Linux and macOS
  without relaxing the production private-primary-group requirement.
- Complete the F8 single-flight alert fixture with the required Agent API
  fields so background rendering cannot race against a partial test record.

## v2.3.0rc3 - 2026-11-12

### Added

- Add idempotent, source-addressed Operator handoff completion receipts. A
  terminal update now returns to the exact source root or source fork, remains
  independent from the authoritative handoff result, and requires explicit
  review acknowledgement. The TUI exposes durable `RECEIPTS` through F8 and
  separates Operator workflow `State`/`Flow` from tmux `Live`; report-mode
  delivery refuses guessed panes when no exact runtime evidence exists.

- Add server-enforced Operator execution authority independent from Operator
  role. The public `scoped-edit` default, immutable `review-readonly`, explicit
  Trusted Workbench, and ceiling-gated local `full-access` profiles use
  versioned exact Git workspace sets, linked-worktree qualification, edit-fork
  inheritance, bounded WorkerBee `local-deploy`, and safe restart/resume
  activation through `/operator authority`.
- Add a local-only, root-confirmed linked-worktree commit broker for stopped
  edit forks whose split secret/read/deny profile cannot safely receive direct
  Codex Git-administration grants. It commits only an explicit reviewed leaf
  subset, uses raw-byte/no-filter identities, leaves unrelated dirt
  unobserved, journals a closed crash-recovery state machine, and exposes no
  API, MCP, shell, implicit-stage, or arbitrary-message surface.
- Add an optional root-supervised exact Git checkpoint workflow for dedicated
  workstations. A root Operator can create one durable request for its own
  Trusted Workbench edit fork; the loopback TUI revalidates the campaign and
  immutable scope, executes guarded stop → exact broker commit → same-session
  resume, and returns readable receipts while leaving root validation explicit.
  Public installs remain in guided mode, and edit/review forks cannot invoke
  the request path.

### Fixed

- Preserve every authority-qualified common Git directory as an explicit
  Trusted Workbench launch root, including when it is nested beneath an
  approved checkout. Exact canonical duplicates are collapsed, while the
  narrower per-worktree, object, ref, and reflog paths remain qualification
  evidence rather than launch roots; review and non-Trusted profiles are
  unchanged.

- Preserve explicitly approved ordered Git parents in root-supervised broker
  checkpoints. Exact two-parent integration commits now bind the first parent
  to the stopped fork's HEAD, validate every complete parent object before
  mutation, reconstruct the reviewed target tree from approved raw-byte paths,
  create the commit with the reviewed parent order, and verify the tree and
  order from quarantine through post-commit recovery. The broker rejects
  ambient merge, cherry-pick, revert, rebase, bisect, and sequencer state.
  Existing single-parent requests and journals retain their prior identity and
  behavior.
- Preserve ordered Git parents and the reviewed tree in broker checkpoint API
  responses. The local TUI now receives the same multi-parent contract that
  passed daemon preflight instead of silently falling back to a one-parent
  request during serialization.

- Make native Latest attachment and runtime-header results selection-generation
  aware. Joplin-to-Latest, F4, and Agent/Operator switches now preserve the
  last valid native frame or bounded loading state, reject stale A→B→A async
  results, and retain the compatibility fallback only for a genuine attach
  failure.
- Keep linked-Git broker completion and recovery bound to the immutable target,
  transaction, and authority receipt after the initiating row selection. A
  harmless later TUI selection change no longer vetoes a valid completion,
  while target posture, branch, path, or authority drift still fails before a
  broker call; durable action receipts remain replayable after interruption.
- Project an assigned caller or bound Operator fork's immutable terminal report
  into campaign assignment state, `last_report_id`, and report-time completion,
  then close the campaign when all assignments are terminal. Projection is
  reporter- and scope-bound, terminal state is immutable, and filtered or
  unfiltered campaign readback safely replays persisted reports after restart.
- Preserve the existing split between targeted Joplin write/readback health and
  strict whole-profile E2EE validation across restart. A verified new note is
  still a successful write when unrelated historical items remain locked, and
  repeated write retry reuses the one active target job instead of duplicating
  work.
- Keep long linked-Git broker receipts inside a bounded scroll region while
  leaving the receipt-bound confirmation input and actions visible. The input
  now owns initial focus, and app-level priority shortcuts yield only while
  that exact modal input owns focus; empty or wrong confirmations still stop
  before broker execution.
- Reconcile a canceled, inactive edit fork's definitively missing tmux runtime
  without treating a same-number pane on another tmux server as the fork. The
  TUI now requires a fresh daemon probe, exact Agent/mapping server, socket,
  session, pane, and Codex-session provenance, no writer lease, and conclusive
  recorded-process absence plus a fresh local/outer pane listing with no
  target-owned pane before discarding only that local cache entry.
  Missing, stale, unreadable, active, leased, live, or ambiguous evidence still
  fails closed.
- Finalize an exact managed stop after its frozen pane process is conclusively
  absent even when reconciliation observes a distinct replacement candidate.
  The replacement remains preserved as audit evidence and is never adopted,
  stopped, or deleted as part of the original transaction.
- Bind Operator stop, purge, restart, and resume operations to the complete
  managed tmux identity: executable, socket, server, session, window, and pane.
  Missing or mismatched provenance now fails before mutation, so a pane ID that
  collides on the outer/default tmux server can never be used as a fallback.
- Keep linked-worktree Git-administration grants out of Codex workspace and
  `--add-dir` roots. Direct-compatible profiles emit only four qualified
  sandbox filesystem writes. Split secret-deny profiles preserve all denials
  and use the stopped-fork TUI broker instead; the legacy
  `sandbox_workspace_write.writable_roots` representation remains rejected
  because it cannot simultaneously retain profile provenance and make the
  linked-worktree index writable.

## v2.3.0rc1 - 2026-11-02

### Added

- Add deterministic ultrawide TUI still capture for documentation and article
  workflows. The isolated media harness records native Latest, Operator
  campaign, WorkerBee, and managed Neovim scenes with per-frame marker checks,
  forbidden-text checks, hashes, geometry, and source revision evidence.

- Add tiered WorkerBee workflow policy without changing the conservative public
  default. Access authority, workspace mobility, appliance snapshot content,
  and backend selection are now independent controls. The WorkerBee tab and
  palette expose per-entity Access and Content screens; review forks remain
  read-only, policy/admin ceilings are daemon-enforced, and authority changes
  require a safe restart/resume before taking effect.
- Add backend-neutral setup presets for standard OSS, personal appliance,
  trusted platform workbench, and high-assurance platform workflows. The
  trusted preset scopes automatic workspace binding to approved roots and uses
  bounded safe-working-tree appliance snapshots, while no preset switches a
  backend or grants `local-deploy`.
- Add daemon-owned appliance snapshot policy with tracked, approved-paths, and
  safe-working-tree modes, source/size/file ceilings, sensitive and bulk-cache
  exclusions, previews, drift-safe persistence, and compatibility with the
  existing private include allowlist. OSS remains direct-host, and the managed
  PaaS/user appliance remains unavailable pending volume qualification.

- Add daemon-owned WorkerBee workspace mobility with `pinned`,
  `same-repository`, and `scoped-workbench` policies. Session-start requests
  resolve to canonical Git roots and working subpaths, preserve the existing
  gateway lease and access profile, journal approval/application/revert state,
  and enforce administrator policy/root ceilings. The WorkerBee tab and
  palette expose controller preview, policy, approval, bind, and rollback
  actions; committed defaults remain pinned and approval-gated.

- Add `/joplin retry write` and an Agent-scoped API action to requeue the
  selected Agent or Operator's latest failed targeted write without creating a
  duplicate note or invoking strict whole-profile E2EE validation.
- Add an Agent PBX styled first-frame loading surface to the public browser
  demo. It reports staged private-session startup progress, yields when Textual
  paints the first terminal frame, honors reduced-motion preferences, and
  offers a safe retry when the disposable session closes before startup.
- Expand each isolated public-demo visitor topology with distinct native tmux
  runtimes for the root Operator and both Operator forks while retaining Release
  Review as a report-mode Agent. Each fake-Codex identity has an independent
  pane and runtime mapping on the visitor-private socket, preserving the public
  demo's no-credentials, no-host-tmux security boundary.
- Add host-owned, Agent-and-source-bound workspace include policies for
  explicitly selected dirty or untracked Git-submodule content. Materialized
  binding identities now incorporate dirty submodule diffs and approved include
  bytes so changed snapshots cannot reuse a stale appliance import path.
- Add generation-bound WorkerBee workspace and gateway lease APIs, scoped
  `observe-local`, `review-readonly`, and `local-deploy` tool profiles, active
  mutation guards for backend switching, and doctor diagnostics for runtime
  generation and materializer readiness.

### Fixed

- Harden isolated public-demo daemon startup against transient loopback-port
  races and slow CI cold starts. Each launch now chooses a fresh port at spawn
  time, detects early child exit with a redacted bounded log tail, and retries
  once without touching host Agent PBX state.
- Make public-demo process liveness assertions portable across Linux and macOS
  instead of assuming a Linux `/proc` filesystem during release qualification.
- Pin a managed Agent process to its launched Agent ID, project, cwd, PBX mode,
  model, reasoning, summary, and verbosity environment instead of allowing
  daemon-launcher environment values to leak into the new runtime. The media
  harness also pins its private runtime socket and uses the matching managed
  tmux binary during cleanup, so a capture cannot adopt or strand a host
  runtime.
- Make bounded Sol-max elevation idempotent across the Codex tab, palette,
  repeated submissions, and concurrent API clients. Agent PBX now reuses one
  nonterminal lease per runtime, refuses lease/profile mismatches, associates
  the approved lease with the resumed Codex runtime, and changes an expired or
  completed one-task lease to `revert_pending` until a structured safe turn
  boundary permits restart/resume on the verified prior profile.
- Wait for the managed Joplin Clipper listener itself to release its socket
  before running profile sync and starting the replacement Data API process.
  This closes a shutdown race where the recorded PID exited first, the new
  server failed with `EADDRINUSE`, and note copy remained locally and remotely
  saved while subsequent Joplin requests received connection refusals.
- Bound long-running native-terminal load by lowering the default embedded
  paint ceiling to 10 FPS, retaining only a small renderer-side history when
  tmux owns scrollback, skipping redundant capture polling for an attached
  client, and exposing event-loop lag, RSS, PTY throughput, paint rate, and
  frame-coalescing telemetry in `Latest`. Agent reconciliation and remote view
  persistence now coalesce without canceling in-flight HTTP requests, use a
  bounded keepalive pool, and reduce polling while the event stream is healthy.
- Rotate managed daemon logs while the daemon is running instead of waiting for
  its next restart. The live copy-and-truncate monitor honors
  `AGENT_PBX_DAEMON_LOG_MAX_BYTES` and `AGENT_PBX_DAEMON_LOG_BACKUPS` while
  preserving the daemon's open stdout/stderr descriptor.
- Keep managed Joplin sync transitions responsive and visible. Status now reads
  durable queued/running state before probing the deliberately stopped Data API,
  the TUI preserves known configuration and cached notes through transient
  restart failures, locally completed `jc` copies render immediately, and write
  jobs from different Agents no longer coalesce under one owner.
- Recognize a live Codex descendant beneath a managed tmux shell launcher when
  routing Operator campaign assignments and follow-ups. Delivery remains bound
  to the exact recorded pane PID, process start evidence, session, window, and
  workspace, while command audit metadata records whether Codex was identified
  from tmux's foreground command or the pane process tree.
- Exit an active tmux copy mode before audited prompt delivery so the submit
  key reaches Codex instead of being consumed by the pane mode.
- Restore the configured Joplin Data API server when Agent PBX starts in
  `managed_cli` profile ownership mode. Bootstrap uses the existing exclusive
  profile lock and validates PID, owner, executable, command, and profile before
  accepting a live process, while unverified listeners fail closed without
  preventing the rest of Agent PBX from starting.
- Resynchronize the public web demo with its browser terminal after the
  terminal element, web font, iframe, or visual viewport settles. Textual
  startup now waits for the initial font and layout frames, preventing the
  provisional browser geometry from opening the application. The server also
  waits briefly for Textual Serve's authoritative post-open resize envelope
  before launching the Textual process, buffering any earlier input and safely
  falling back to sanitized URL dimensions. Those bounded dimensions now pass
  through the disposable session's environment allowlist so Textual's web
  driver receives them before its first render. Embedded and dedicated sessions
  therefore start from one framebuffer at their full viewport without a manual
  resize while the upstream Textual Serve bundle remains untouched.
- Refresh a platform-debug content-addressed workspace when an approved
  `local-deploy` session repeats `workerbee_v1_session_start`. The existing
  Codex thread, gateway lease, token, and backend generation remain in place;
  subsequent mutating tools fail closed if source changes again before refresh.
- Reapply responsive TUI geometry after the browser demo's first painted frame
  so the Agents, Operators, and Events stack no longer requires a manual window
  resize to adopt its intended initial proportions.
- Bind platform-debug switching to an explicit loopback or native-LAN posture.
  Preview, no-op detection, verification, rollback, and status now include that
  posture so a backend recreation cannot silently replace qualified LAN DNS and
  HTTPS with a loopback-only appliance.
- Preserve structured WorkerBee workspace and gateway error codes during Codex
  launch preflight. The TUI now identifies the failed binding phase, presents
  remediation, and states explicitly that the existing Codex process was not
  started or replaced instead of reducing the response to an HTTP conflict.
- Resolve gateway lease identities from Starlette's mounted ASGI `root_path`
  before authenticating the request, then rewrite the authenticated route to
  the Streamable HTTP transport root. Live gateway URLs no longer fail as an
  invalid lease before WorkerBee receives the MCP handshake.
- Bind every Agent, Operator, edit fork, and review fork launch or resume to the
  one selected WorkerBee backend generation and an exact workspace receipt.
  Appliance sessions now use an Agent PBX policy gateway instead of inheriting
  host OSS paths, and backend changes fail closed until affected sessions are
  rebound.
- Keep OSS, platform-debug, and future user-appliance workspace domains
  separate. Platform-debug uses content-addressed imports in its own appliance
  volume; the unqualified user-appliance materializer remains unavailable.
- Read WorkerBee status from the selected appliance and its bound workspace
  without blending in the host OSS CLI registry.
- Preserve an explicitly assigned, task-scoped WorkerBee caller policy across
  Codex restart/resume operations. The new `local-deploy` profile permits only
  the bounded local build, manifest validation/deployment, ingress, status,
  logging, and security-review tools; ordinary caller sessions continue to
  inherit global WorkerBee configuration without a PBX override.
- Restore caller Agent PBX identity and bearer-token environment when Codex is
  respawned in an existing managed tmux pane.

## v2.2.0 - 2026-10-13

### Highlights

- Adds an isolated development harness with private runtime state, deterministic
  cleanup, and WorkerBee workspace-binding receipts so integration work can be
  qualified without interrupting production Agents or Operators.
- Adds allowlisted native terminal editors inside the Editor tab, each on an
  editor-only tmux runtime that never shares Codex pane topology.
- Adds preview-bound WorkerBee backend switching with typed confirmation,
  capability verification, automatic recovery, and explicit rollback for
  future sessions.
- Adds deterministic repository-wiki validation, staging, checksums, and a
  digest-confirmed publisher that preserves unrelated wiki pages.
- Adds a repository-owned interactive browser demo that runs the real Textual
  TUI in a disposable, isolated Agent PBX environment for each visitor.
- Adds a daemon-owned, read-only Codex app-server bridge for exact-mapping
  subagent activity without making structured observation a runtime dependency.
- Completes setup, navigation and palette history, Codex child activity,
  responsive layouts, and Joplin clipboard actions for the 2.2 cycle.

### Added

- Report versioned WorkerBee backend provenance, endpoint alignment, server
  identity, and host-to-WorkerBee project visibility through doctor and the
  TUI without changing the selected backend.
- Add a guided local setup transaction that previews dependencies and scoped
  project roots, merges private localhost-first configuration atomically, and
  supports idempotent resume and checksum-guarded rollback.
- Recognize current Codex `subAgentActivity` events in the capability-gated
  nested runtime tree while keeping child paths, prompts, and inferred roles
  out of PBX telemetry.
- Add bounded, deduplicated command palette history with input-focused Up/Down
  traversal, an inspector and clear action, sensitive-name filtering, and
  opt-in persistence that never stores prompt bodies.
- Add per-client Agent, Operator, and right-tab navigation history on F11/F12,
  with Shift-function-key passthrough preserved and safe skipping for stale or
  hidden logical destinations.
- Add a versioned repository wiki for setup, control-plane concepts, keymaps,
  managed tmux operations, Codex profiles, WorkerBee, Joplin, security,
  recovery, and releases, plus automated link, public-source, and documented
  command checks.
- Add an isolated development harness with loopback-only services, private
  HOME/XDG/state roots, dedicated tmux sockets, atomic manifests, guarded
  cleanup, and reproducible fixture integrations.
- Add versioned WorkerBee harness-binding receipts that preserve source
  revision and content-digest evidence, reject workspace drift, and keep local
  snapshots unbound until the active endpoint proves an allowed import path.
- Add managed terminal editor profiles using structured executable arguments,
  shared project file policy, dedicated editor-only tmux runtimes, API/TUI
  lifecycle controls, and native Editor-tab rendering.
- Add transactional WorkerBee backend switching with dry-run previews,
  expiring private tokens, active-backend drift checks, typed privileged
  confirmation, MCP capability verification, and automatic/manual rollback.
- Add guarded wiki `check`, `stage`, and `publish` commands with revision-pinned
  repository links, generated navigation, per-page checksums, deterministic
  archives, exact digest confirmation, and preservation of unknown pages.
- Add an optional interactive browser demo with per-visitor daemon, database,
  Git fixtures, integrations, tmux runtime, bounded lifecycle, exact origin
  policy, rootless container packaging, and a versioned product-site contract.
- Add a daemon-owned, read-only Codex app-server bridge that associates native
  subagent threads and lifecycle items with exact PBX session mappings and
  drives the existing nested runtime tree when structured evidence is observed.
- Add a combined Codex/editor runtime-bundle status view with explicit editor
  stop and stale-record forget controls.
- Add focus-scoped right-tab shortcuts: F3 followed by Shift+F1–F11, with a
  portable `1..9`, `0`, `-` fallback that never captures terminal input.

### Changed

- Treat WorkerBee backend selection as a future-session boundary. Existing
  Codex sessions retain their current MCP binding and are never rewritten by a
  backend switch transaction.
- Keep managed terminal editors separate from Codex runtime mappings and apply
  the same root, sensitive-path, traversal, symlink, and regular-file checks as
  the Textual editor.
- Coalesce embedded-terminal paints under sustained output while continuing to
  read and parse every PTY byte, expose bounded performance counters, and avoid
  rebuilding rows whose final cell presentation is unchanged.
- Persist managed editor runtime identity across daemon/TUI restarts, adopt
  verified editor panes safely, route approved files into active Neovim tabs
  through its private RPC socket, and preserve editor processes by default on
  daemon shutdown.
- Follow each Git wiki remote's advertised default branch during publication so
  Gitea `main` and GitHub `master` remain visible without host-specific defaults.
- Update the versioned project wiki, public README, environment template, and
  release guidance for Agent PBX 2.2.

### Fixed

- Preserve a caller's route-safe logical project identity when later Agent
  registration or report metadata supplies an absolute working directory, and
  keep Joplin list/get requests read-only so missing scopes cannot create
  duplicate path-derived notebooks.
- Identify Joplin-native conflict copies as local-only, immutable recovery
  records; reject false-positive targeted sync completion and provide a
  revision-guarded promotion action that creates a uniquely titled normal note
  while preserving both the readable conflict and its encrypted original.
- Separate targeted Joplin write health from strict profile E2EE validation,
  skip whole-profile decrypt attempts for targeted jobs, and label manual sync
  as profile validation while retaining `/joplin sync` as an alias.
- Render the public hero and architecture visuals on PyPI by using absolute
  HTTPS asset URLs, while retaining the architecture's maintained Mermaid
  source in the repository.
- Preserve cached Joplin note bodies and list geometry across unchanged sync
  refreshes, invalidate them on note revision changes, and suppress redundant
  Markdown renders that caused visible selection and reading-view jumps.
- Keep a revision-scoped reading position for each Joplin note and preview so
  keyboard, mouse, and Termux readers return to the same offset without
  restoring stale positions after content changes.
- Add explicit Joplin note clipboard copy and edit-only paste actions with
  stale-target revalidation and metadata-only confirmation for multiline or
  large clipboard payloads.
- Run Joplin's explicit E2EE decrypt pass while the managed profile is the
  sole owner, restore the validated Data API server after decrypt failures,
  and report password-required and native crypto-worker failures separately.
- Preserve targeted Joplin write success when its verified note is plaintext
  even if an unrelated encrypted profile item cannot yet be decrypted.
- Force a capable terminal type for managed editor runtimes when an inherited
  noninteractive environment reports `TERM=dumb`.
- Pin `tmux` command resolution inside managed runtimes through a private,
  validated state-root shim so Codex follow-up tools use the same qualified
  binary as Agent PBX even when the parent shell resolves another tmux version.
- Refuse daemon start or restart before stopping a healthy older daemon when
  the database schema is older, newer, corrupt, or unreadable, and report the
  exact dry-run/apply/verify remediation commands through status.
- Close repeated WebSocket failure and TUI unmount paths cleanly; isolated
  stress qualification found bounded descriptors and no growing transport leak.
- Keep disposable public-demo and test UNIX sockets below macOS path limits,
  resolve portable host utility paths, and host each demo's isolated in-memory
  Joplin API on a dedicated thread without a macOS subprocess startup race.

## v2.1.1 - 2026-10-09

### Added

- Add exact-tag TestPyPI and production PyPI Trusted Publishing workflows with
  protected GitHub environments, short-lived OIDC credentials, immutable build
  artifacts, package attestations, post-publish hash verification, and clean
  index-install smoke tests.
- Add deterministic release identity and archive validators covering tag,
  commit, package version, changelog, release notes, duplicate index versions,
  private/generated package content, metadata, and distribution hashes.

### Changed

- Pin GitHub Actions used by release gates to reviewed commit SHAs and attach
  the existing checksummed Linux wheelhouse to successful GitHub Releases
  separately from the wheel and source distribution published to PyPI.

- Serialize same-profile Joplin Data API and CLI sync access through an
  explicit external or managed profile-ownership policy.
- Validate targeted Joplin write jobs against their declared note IDs while
  keeping manual sync as a strict profile-wide E2EE consistency check.
- Add Codex session, transcript, turn-phase, line, mtime, and response-hash
  provenance to copied-response notes.

### Fixed

- Create a fresh v2 managed tmux runtime mapping and resume the preserved Codex
  session when an explicit Agent or Operator restart finds no ready mapping;
  fail closed when mapping state cannot be verified.
- Install and select the generated `agent-pbx-1337` Codex syntax theme through
  a drift-safe command, report its posture through doctor, and reconcile stale
  `NO_COLOR` inherited by persistent dedicated tmux servers while preserving
  an explicit user color-suppression preference.
- Route Operator campaign follow-ups, root messages, KB seed prompts, and
  knowledge turns through the target runtime mapping's recorded tmux binary,
  socket, session, window, and pane instead of probing the daemon's inherited
  host tmux context.
- Restore the original `cyberpunk` TUI RGB contract, retain the newer
  deuteranopia-friendly palette as a visibly distinct deep-teal
  `cyberpunk-accessible` option, and keep true-color rendering stable when the
  outer workspace uses tmux 3.2.
- Use each runtime mapping's recorded tmux executable for embedded wheel,
  Page Up/Down, and explicit copy-mode actions so managed tmux 3.7 sessions do
  not fall back to an incompatible host tmux 3.2 client.
- Resolve the rollout held open by a managed pane through that mapping's exact
  tmux executable and socket, preventing reused pane IDs on the outer 3.2
  server from selecting the wrong Codex session history.
- Type the supported Codex `/quit` command as literal key input, allow its
  bounded shutdown time, and recognize exit when a shell-rooted pane remains.
- Render encrypted Joplin items as locked instead of empty and block note
  mutation until decryption completes.
- Restore the exact validated Joplin Data API process after managed CLI sync
  without exposing E2EE credentials or ciphertext through Agent PBX.
- Keep unrelated encrypted profile items visible as health warnings without
  falsely failing a successfully synchronized response note.
- Preserve the current Joplin note list, reading view, and dirty draft while
  managed sync restarts the Data API, then refresh after recovery.

## v2.1.0 - 2026-10-06

### Highlights

- Adds reversible adoption of existing Agents into PBX-managed native tmux
  runtimes while preserving Codex session identity, process state, pane
  topology, and rollback evidence.
- Adds guided `F10` and palette project launch from configured filesystem
  roots.
- Hardens the embedded Codex terminal across keyboard ownership, modified
  function-key passthrough, scrollback, resizing, redraw, pop lifecycle,
  writer leases, and Android/Termux navigation.
- Improves Agent and Operator lifecycle UX, alert handling, live list
  stability, Joplin response export, and terminal transition performance.
- Reframes and documents Agent PBX as a runtime-grounded Agentic Engineering
  Control Plane for Agentic Application Stack Operations.

### Added

- Add reversible native Agent pane adoption with migration preview, rollback
  evidence, current-rollout selection, and declared runtime aliases.
- Add the `F10` scoped project launcher and equivalent palette actions.
- Add configurable project-root discovery for managed Agent launch.
- Add `F3` navigation to the right-pane tab row.
- Add `PgUp` activation of runtime tmux scrollback from the embedded Codex
  pane.
- Add terminal reading-view keyboard navigation for Linux, SSH, and
  Android/Termux clients.
- Add configurable newline input for embedded Codex terminal sessions.
- Add `F8` alert navigation and acknowledgement behavior.
- Add deterministic v2 interface and hero-capture fixtures.
- Add bounded parallel pytest lanes and serial-test isolation.
- Add schema 29 refresh indexes for Agent-list summary queries.

### Changed

- Keep plain PBX function-key navigation active under terminal focus while
  translating configured modified function keys to the child Codex terminal.
- Preserve existing Codex panes when restart or migration verification fails.
- Prefer the current Codex rollout when resolving session adoption candidates.
- Preserve known origin-client evidence while refreshing runtime mappings.
- Cascade root Operator alert acknowledgement to the related non-review fork.
- Keep Agent and Operator ordering stable while the user is interacting with
  their lists.
- Use the configured prompt summary in Joplin response-note titles.
- Reduce redundant embedded-terminal painting and input latency.
- Smooth transitions between captured and native terminal surfaces.
- Refresh the README, architecture documentation, and public release media
  around the Agentic Engineering Control Plane product model.

### Fixed

- Fix printable PBX shortcut bindings consuming characters intended for the
  focused Codex terminal.
- Fix `Ctrl+C` and `Ctrl+P` ownership under embedded-terminal focus.
- Fix stale or missing outer-tmux client identity during targeted pop-out.
- Fix writer-lease renewal after native mode is disabled or the disposable
  embedded client has exited.
- Fix native tmux target selection, resize propagation, and pane geometry.
- Fix embedded Codex popup and selector redraw artifacts after Escape.
- Fix failed restart paths that could disconnect or retire a healthy source
  pane prematurely.
- Fix palette and modal actions targeting the previously selected entity.
- Fix transient right-pane fallback flashes while switching between native
  Agent and Operator terminals.
- Fix managed-launch cleanup and selection behavior after launch dialogs.
- Fix diagnostics isolation under parallel pytest execution.

### Performance

- Add SQLite indexes for common Agent refresh and summary queries.
- Coalesce embedded-terminal paints and avoid unchanged-frame rendering.
- Reduce terminal transition work while selecting Agents and Operators.
- Add qualified parallel test lanes for faster full-suite execution.

### Compatibility

- Existing Agent, Operator, fork, Codex-session, tmux-pane, campaign, Joplin,
  and project mappings remain supported.
- Schema 29 is an additive index migration from the v2.0 schema 28 baseline.
- Polling/nohup mode, Thread, HTTP refresh, and captured-terminal fallback
  remain supported in Agent PBX 2.1.0.
- Legacy-path removal still requires measured parity, a supported migration
  path, and published notice.
- Native terminal mode retains capture and pop-out recovery paths.

### Upgrade notes

- Back up the Agent PBX database, configuration, and runtime mappings before
  upgrading.
- Install Agent PBX 2.1.0, restart the daemon, and then restart each connected
  TUI client.
- Existing Codex Agents and Operators do not require a Codex restart solely
  because Agent PBX was upgraded.
- Verify retained entities, runtime mappings, native terminal attachment, and
  schema 29 after the daemon restart.
- Keep the pre-upgrade backup for at least seven days.
- See `docs/releases/v2.1.0.md` and `docs/acceptance/v2.1-uat.md`.

### Verification

- Qualified local parallel suite: 874 passed.
- Isolated serial lane: one expected skip with no failure.
- Existing starred Agents, root Operators, edit forks, and review forks were
  retained through migration and restart testing.
- Native terminal typing, resizing, scrollback, function-key ownership,
  pop-out, pop-in, TUI restart, daemon restart, and session reconciliation were
  exercised against the workstation tmux topology.
- Android/Termux-over-SSH scrollback and reading navigation were exercised.
- Managed `F10` launch, alert cycling, Joplin response export, and deterministic
  release capture were exercised.
- `python -m compileall -q src tests`
- `scripts/test_full.sh`
- `python -m pytest -q tests/test_release_packaging.py`
- `git diff --check`

## v2.0.0 - 2026-10-03

### Highlights

- Promotes Agent PBX into a durable Codex runtime control plane while retaining
  PBX ownership of Agent/Operator identity, lifecycle, policy, routing,
  campaigns, approvals, and audit.
- Embeds a PTY-backed normal tmux client in the existing `Latest` tab, keeps the
  v1 left/right layout and right-side tabs, supports dedicated and validated
  outer tmux servers, and adds safe pop in/out with one writer lease.
- Keeps plain F keys as global PBX navigation and translates Shift+F1–F12 into
  child F1–F12; F2 opens Events and Shift+F2 reaches Codex warnings.
- Adds a versioned Codex adapter, catalog-driven profiles, provenance-aware
  runtime state, structured transcript results, safe restart/resume model
  transitions, managed skill packs, and approved bounded model elevation.
- Adds sequenced event snapshots, replay/resync WebSockets, role-scoped remote
  clients, SSH native attach, runtime and child-agent status surfaces, and a
  unified accessible theme.
- Adds durable Operator knowledge links and executable handoffs, a managed KB,
  read-only review-fork proposals, right-pane KB controls, and guarded batch
  pruning with preview/apply/undo.
- Adds optimistic Joplin revisions, durable sync jobs, Markdown read/edit modes,
  safe Editor CRUD and recoverable project trash, unified lifecycle repair and
  pruning, and hardened campaign/fork transitions.
- Adds platform doctor, guarded database/config backup and migration commands,
  verified rollback, safe offline wheelhouses, hashes, dependency manifests,
  and an SPDX 2.3 SBOM.

### Compatibility

- Native tmux and v2 event streaming are the defaults for local clients.
- Polling/nohup mode, Thread, HTTP refresh, and terminal capture remain enabled
  and supported throughout the v2.0 line.
- Legacy removal requires measured parity, a migration path, published
  criteria, and a later release.

### Upgrade notes

- Back up and dry-run before applying schema 28.
- LAN listeners require bearer authentication and TLS unless an explicit
  controlled-lab override is supplied.
- Restart the daemon after migration, then restart the TUI. Existing Codex
  sessions remain resumable through their recorded mappings.
- See `docs/releases/v2.0.0.md` and `docs/acceptance/v2-uat.md`.

### Verification

- Full Linux and macOS CI matrix on Python 3.10 and 3.12.
- Portable RC gate: 793 passed and one expected skip on Ubuntu and macOS with
  Python 3.10 and 3.12.
- Isolated daemon, WebSocket, schema 28, Operator KB/handoff, Codex flow-matrix,
  and cleanup UAT.
- Checksummed Python 3.10 offline wheelhouse install and command smoke test.
- `python -m compileall -q src tests`
- `python -m pytest -q`
- `git diff --check`

## v2.0.0a1 - 2026-10-02

### Highlights

- Locks the v2 control-plane, tmux topology, runtime evidence, profile, remote
  access, compatibility, and embedded-terminal engine decisions in repository
  ADRs.
- Introduces stable runtime, capability, lifecycle, and action contracts plus
  Textual-independent panel, focus, and asynchronous-generation controllers.
- Adds a Pyte-backed PTY/virtual-terminal adapter and conformance checks for
  normal tmux clients on dedicated and validated outer servers.
- Keeps Agent PBX function-key navigation global while translating
  Shift+F1–F12 and xterm F13–F24 aliases into child F1–F12; F2 remains Events
  and Shift+F2 reaches Codex warnings.
- Adds focus-generation and stale-result guards to prevent layout refresh and
  delayed Joplin operations from snapping or replacing newer UI state.
- Adds versioned event snapshots, bounded replay, authenticated WebSocket
  clients, cursor/resync state, and an opt-in v2 TUI event consumer while
  retaining v1 HTTP/SSE behavior.
- Adds a versioned Codex runtime adapter with capability/model/feature/schema
  probing, binary/profile-aware caching, state provenance, verified app-server
  association, and the locked `/copy → transcript → tmux` capture contract.

### Compatibility

- The captured `Latest` terminal and v1 SSE path remain defaults in alpha 1.
- `event_stream_v2` is opt-in in local TUI settings.
- No database schema or user Codex configuration changes are included in this
  alpha.

### Verification

- Dedicated and detected-outer tmux PTY conformance.
- Full Python test suite.
- `python -m compileall -q src/agent_pbx`
- `git diff --check`


## v0.11.0 - 2026-08-13

### Highlights

- Adds tmux-backed Codex restart controls for caller agents, root operators,
  and operator forks, including `/restart`, `/codex restart`, session resume,
  and retry handling when a replacement pane exits before stabilizing.
- Expands operator review workflows with multiple read-only review forks per
  logical operator/caller session, review-specific scratch work roots, fork
  cycling, safer pane validation, and preapproved Agent PBX/optional WorkerBee
  MCP access for happy-path review prompts.
- Adds operator-mediated project spawn requests: read-only review forks can
  request a new sibling project, and the TUI can approve, create, clone or
  initialize it, launch a normal tmux caller agent, and attach it to the Agents
  pane.
- Adds guarded operator fork source-session rebinding so one stale live fork can
  be associated with a restarted caller session when operator, caller, cwd,
  host, and fork track all match.
- Improves operator-scoped prompts with caller-scoped Joplin, PR, and issue
  references, plus stronger routing recovery for forked operator panes.
- Advances the SQLite schema to version 14 for project-spawn persistence and
  fork relationship state.

### Upgrade Notes

- Restart the Agent PBX daemon after installing this release so the API, MCP
  tools, and schema migration are loaded.
- Restart the TUI after installing this release, especially when using the
  repo-local venv launcher, so operator project-spawn and fork-rebind controls
  are available.
- Restart long-lived tmux Codex caller/operator sessions with `/restart` or
  `/codex restart` after updating the global Codex CLI package.

### Verification

- `python -m pytest`
- `git diff --check`
- Release wheelhouse build with `scripts/build_wheelhouse.sh`
- Local no-index wheelhouse install smoke test
- Daemon restart and `/healthz` check

## v0.10.0 - 2026-06-18

### Highlights

- Adds first-class operator agents with TUI lifecycle controls, a dedicated
  Operators split, tmux-backed operator launch paths, and fork-pane cycling for
  monitoring operator-owned caller forks.
- Adds operator campaign persistence with dedicated SQLite tables for campaigns,
  assignments, events, forks, and fork DAG links so campaign queries do not rely
  on report metadata scans.
- Adds operator campaign routing through caller forks, including `@caller:`
  reference expansion, fork regeneration after caller restarts, stale tmux
  target repair, and safer campaign dispatch checks.
- Adds the TUI `Campaigns` tab for viewing operator campaign state and generated
  campaign reports, plus `View Report (R)` / `/campaign report`.
- Adds campaign-to-Joplin capture with `Copy Note (C)`, `Shift+C`, and
  `/campaign copy`, creating a new scoped Joplin note with campaign detail and
  loaded generated reports.
- Improves tmux plan-selection routing for native Codex plan selectors and
  pre-plan choice handling.
- Refreshes the README TUI demo GIF for the new operator, campaign, and Joplin
  workflows while reducing the release asset size.
- Adds configurable GitHub remote/SSH settings for PR and issue workflows.

### Verification

- `python -m pytest`
- `git diff --check`
- Release wheelhouse build with `scripts/build_wheelhouse.sh`
- Local no-index wheelhouse install smoke test

## v0.8.1 - 2026-06-09

### Highlights

- Fixes stale Joplin sync status in the TUI so an older sync failure no longer
  keeps showing `Sync: error` after a newer successful sync.
- Adds tmux/Termux-safe Joplin action shortcuts with `Ctrl+G` as the leader,
  plus `j` as an alternate leader when focus is outside the editable note body.
- Makes Joplin tab button shortcuts visible directly in the action labels:
  `New n`, `Ren m`, `Del d`, `Save s`, `Ref r`, `Copy c`, `LOG+ l`,
  `LOG- x`, and `Sync u`.
- Fixes `/joplin copy` from the Latest input so it targets the input-selected
  agent instead of the Agents table cursor.
- Updates README guidance for the Joplin shortcut workflow.

### Verification

- `python -m pytest`
- `git diff --check`
- Release wheelhouse build with `scripts/build_wheelhouse.sh`

## v0.8.0 - 2026-06-08

### Highlights

- Adds a queued Joplin sync gateway with durable sync jobs, sync status
  reporting, and a `/v1/joplin/sync` API for manual or write-triggered sync.
- Adds project-scoped Joplin note APIs so notes created directly under an Agent
  PBX project notebook can be listed, read, edited, deleted, and referenced.
- Expands the TUI Joplin tab to show project-level notes while keeping COPY and
  LOG captures attributed under agent/session note folders.
- Adds `@joplin:` references in the Latest input. Tab completion uses cached
  project note titles, and send-time expansion fetches fresh note bodies into a
  Markdown `Joplin Note References` section for Codex review.
- Updates README and AGENTS/runbook guidance for project-scoped Joplin
  references, sync-on-write, and operator prompt expansion behavior.

### Verification

- `python -m pytest`
- `git diff --check`
- Release wheelhouse build with `scripts/build_wheelhouse.sh`

## v0.7.0 - 2026-06-06

### Highlights

- Adds optional Joplin notes integration with scoped notebooks under
  `project > agent`, MCP document export, and server-side configuration for
  local REST API plus CLI/WebDAV sync.
- Adds the TUI `Joplin` tab for listing, viewing, creating, renaming, deleting,
  saving, COPY notes, and growing LOG notes within the Agent PBX notebook scope.
- Adds tmux-direct Joplin COPY/LOG capture using Codex `/copy` and local
  clipboard helpers, so prompts and copied responses can be written to notes.
- Adds sync-on-write support that runs `joplin --profile <profile> sync` after
  note writes, and surfaces Joplin zero-exit E2EE errors such as unloaded master
  keys.
- Improves tmux plan-selection routing and alerts, including relaxed scope for
  visible native Codex plan selectors.
- Syncs starred agents across TUI clients so workstation and watch-screen TUIs
  share pinned agent ordering.
- Expands user configuration defaults, Joplin availability guards, README
  guidance, and AGENTS/runbook notes for Joplin and tmux-direct workflows.

### Verification

- `python -m pytest`
- `git diff --check`
- Joplin CLI sync diagnostics against the dedicated local profile

## v0.6.0 - 2026-06-02

### Highlights

- Adds `agent-pbx-tui`, a TUI-only launcher for lightweight LAN clients with
  dotenv-style config at `~/.config/agent-pbx/tui.env`.
- Adds low-power TUI watch mode, configurable refresh intervals, and updated
  tiny/mobile focus shortcuts for small terminals such as PocketCHIP.
- Improves remote TUI refresh by tailing current events and isolating the SSE
  event worker from other Textual refresh workers.
- Adds shared latest-report seen state so clearing `NEW` in one TUI clears it
  in other connected TUIs.
- Fixes no-report agents incorrectly appearing as `NEW`, and makes the tiny
  Agents view show `Project` directly after `Status`.
- Adds `/v1/events?tail=true&limit=N` for efficient latest-event reads and
  advances the database schema to version 7 with latest-report seen backfill.

### Verification

- `python -m pytest`
- `git diff --check`
- PocketCHIP remote TUI probes for event refresh and shared `NEW` clearing

## v0.4.0 - 2026-05-28

### Highlights

- Adds the TUI project file browser for viewing agent workspace files without
  leaving Agent PBX.
- Improves tmux-backed workflows with safer toggles, better mobile key
  navigation, and focus controls for small terminals and SSH/Termux use.
- Expands command palette and slash-command support, including Git helpers and
  custom user command configuration.
- Adds README hero/demo assets and a scripted TUI demo capture flow.
- Aligns AGENTS.md and runbook guidance around report mode, nohup polling mode,
  planning, and completion git-state reporting.
- Fixes WorkerBee project status selection when multiple WorkerBee records share
  the same project cwd, so explicit running projects are preferred.

### Verification

- `python -m pytest`
- `git diff --check`
- Release wheelhouse build and local wheelhouse install smoke test
