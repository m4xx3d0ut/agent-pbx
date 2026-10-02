# ADR 0008: Runtime presentation and child topology

- Status: accepted
- Checkpoint: v2 checkpoint 10

## Decision

Agent PBX presents Codex runtime state from a daemon-side normalized projection.
The projection accepts only events whose thread ID matches the active PBX/Codex
session association. It records the normalized state, evidence provenance,
public model profile, token-window counters, and bounded child lifecycle data.

Prompt text, tool arguments and results, commands, child status messages, and
reasoning content are excluded from the projection and event journal.

The TUI renders a one-line state/model/context/branch/WorkerBee surface outside
the embedded terminal. It may animate the PBX state symbol, but it never writes
animation bytes into the Codex PTY. Child topology is hidden until an associated
app-server `collabAgentToolCall` proves native subagent behavior for that
session.

One semantic palette supplies Textual colors, declarative tmux options, and a
generated Codex TextMate syntax theme. Every state also has a distinct symbol
and text label, with explicit 16-color, 256-color, and true-color mappings.

## Consequences

- TUI wording and Codex ANSI output are fallback display material rather than
  the source of runtime truth.
- An unrelated app-server cannot update an Agent or Operator runtime surface.
- Unknown protocol fields and unsupported events do not crash the TUI.
- A daemon restart can restore the last safe projection from the PBX event
  journal.
- Child prompts and hidden reasoning cannot appear in PBX runtime telemetry.
- Older daemons and sessions without structured events continue to show a PBX
  report-derived status and no child tree.

## Rollback

Disable runtime event producers and revert the checkpoint commit. Existing
`codex_runtime_observed` journal rows are inert to earlier versions and contain
no schema migration. The captured Latest/tmux terminal and v1 status paths
remain available.
