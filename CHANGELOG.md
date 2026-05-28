# Changelog

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
