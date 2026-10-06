# V2 schema 29: Agent refresh indexes

Schema 29 adds four SQLite indexes used by the Agent and Operator list summary
queries:

- `idx_reports_agent_created`
- `idx_commands_status_agent_created`
- `idx_commands_agent_created`
- `idx_poll_events_agent_created`

The migration is additive. It does not rewrite Agent or Operator records,
starred state, Codex session identities, runtime tmux mappings, reports,
commands, or poll history. Existing schema-28 databases receive the indexes
when the daemon starts and initializes the store.

The indexes remove repeated full-table scans from `Store.list_agents()`. On the
workstation qualification snapshot, the visible-Agent path fell from about
1.26 seconds to 9 milliseconds and the include-hidden path fell from about
3.27 seconds to 20 milliseconds. This lowers daemon contention during the
TUI's periodic refresh and leaves more CPU time for the embedded terminal
renderer.

Before activation, create the normal Agent PBX migration backup. Apply the
migration by restarting the daemon with the updated package, then verify schema
29 with `agent-pbx doctor`. A TUI restart is recommended after the daemon is
healthy so the client reconnects against the migrated service. Rollback uses
the normal stopped-daemon database restore; no tmux or Codex runtime migration
is involved.
