# Changelog

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
