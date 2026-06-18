# Changelog

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
