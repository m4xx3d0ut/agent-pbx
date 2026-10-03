# V2 Checkpoint 13: Lifecycle reconciliation and campaign hardening

Checkpoint 13 keeps the Agent PBX SQLite schema at version 27. It adds a service boundary that reconciles durable Agent and Operator records with Codex session identity, project/worktree availability, runtime tmux mappings, fork ownership, and prune guards. No database migration is required.

## Lifecycle behavior

`LifecycleService` returns one redacted inspection for callers, root Operators, edit forks, and review forks. It reports the PBX lifecycle state, preferred current/fork Codex session ID, workspace availability, runtime-mapping assessment, reference guards, and the availability of each lifecycle operation.

Mutating lifecycle operations require a preview token computed from current state. If the entity changes before apply, the token is rejected. Server-side actions cover star, unstar, archive, unarchive, mark-canceled, guarded purge, and unique tmux-mapping repair. Resume, restart/resume, and pop actions return a typed client-required response because the controlling TUI owns the local tmux and Codex transition. Existing prune preview/apply/undo remains the authoritative batch-pruning path.

Runtime repair is deliberately narrow:

- the recorded tmux server socket must be valid and user-local;
- the recorded pane must assess as moved rather than ready, missing, reused, or foreign;
- exactly one session/window/CWD candidate must match;
- no active writer lease may exist;
- apply re-runs assessment and rejects a stale preview;
- the repair preserves Codex session, server, origin-client, and prior metadata while journaling old and new pane identity.

Secrets, prompt bodies, provider configuration, and hidden reasoning do not enter lifecycle inspection or audit events.

## Campaign behavior

Campaign lists use cursor/limit pagination and omit event bodies. Selecting a campaign loads its event and report detail lazily. The TUI uses generation guards so an older list or detail response cannot replace a newer selection, and report bodies are fetched concurrently.

Campaign transitions now enforce these invariants:

- a campaign can be mutated only by its logical root Operator or one of its registered forks;
- an assignment that reached a terminal state cannot transition to a different state;
- an archived assignment target accepts only a terminal closing report;
- campaign finish requires a terminal status and every assignment to be terminal;
- a completed campaign cannot be completed or updated again.

## Upgrade and verification

1. Confirm the existing database reports schema version 27 and `PRAGMA integrity_check` returns `ok`.
2. Start the Checkpoint 13 daemon; no schema mutation occurs.
3. Inspect a caller, root Operator, edit fork, and review fork through `/v2/lifecycle/{entity_id}`.
4. Preview every intended mutation before apply. Preserve the existing batch prune workflow for bulk cleanup.
5. Exercise campaign list pagination, selected-detail loading, terminal transitions, archived targets, and logical Operator ownership.

## Rollback

Stop the Checkpoint 13 daemon and check out the Checkpoint 12 commit. Because the schema remains at 27, the Checkpoint 12 daemon can reopen the same database. Lifecycle audit events and valid mapping repairs remain ordinary event and runtime-mapping records; reverting code does not undo them. If a repair must be reverted, restore the recorded prior pane identity only after re-validating process and Codex session evidence.
