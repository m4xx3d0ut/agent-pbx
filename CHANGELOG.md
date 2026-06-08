# Changelog

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
