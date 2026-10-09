# Changelog

## Unreleased

### Changed

- Serialize same-profile Joplin Data API and CLI sync access through an
  explicit external or managed profile-ownership policy.
- Validate targeted Joplin write jobs against their declared note IDs while
  keeping manual sync as a strict profile-wide E2EE consistency check.
- Add Codex session, transcript, turn-phase, line, mtime, and response-hash
  provenance to copied-response notes.

### Fixed

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
