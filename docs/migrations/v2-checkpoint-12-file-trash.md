# V2 Checkpoint 12: Editor CRUD and project trash

Checkpoint 12 advances the Agent PBX database schema from 26 to 27. The migration adds durable records for recoverable project trash entries and reversible trash-prune batches. Existing file, Agent, Operator, and Joplin records remain unchanged.

## Behavior

The Files tab now exposes create-file, create-directory, move, delete, and project-trash actions. All paths remain relative to the selected Agent's approved project root. Mutations reject traversal, absolute paths, symlink traversal, special devices, sensitive files, Git metadata, Agent PBX trash metadata, missing parents, and destination collisions.

Deletion follows the repository state:

- Git-tracked paths become ordinary working-tree deletions after an explicit preview and confirmation. Git remains the recovery system.
- Untracked, ignored, and non-Git paths move atomically to `.agent-pbx-trash/<trash-id>/content` with a manifest. Git repositories receive `/.agent-pbx-trash/` in `.git/info/exclude`; committed `.gitignore` files are not changed.
- A delete preview binds path kind, size, item count, mtime, content/tree digest, Git state, and mode. Changed content invalidates the preview.
- Restore refuses to replace an existing destination.
- Prune uses preview/apply/undo. Apply moves expired entries into a hidden per-batch quarantine under `.agent-pbx-trash/.prune/`, preserving an immediate rollback path. Undo restores the complete batch. A later maintenance policy may remove old, already-applied quarantine batches after their separate rollback-retention window.

The daemon journals create, move, tracked delete, trash, restore, prune, and undo events. SQLite records preserve actor, original path, root, digest, size, expiry, and lifecycle state without storing file contents.

## Upgrade

1. Stop writes to the daemon or take a consistent SQLite backup.
2. Start the Checkpoint 12 build. `Store.init()` creates the two additive tables and records schema version 27.
3. Run `PRAGMA integrity_check` and confirm the result is `ok`.
4. Open Files for a test Agent and verify create, move, trash, restore, and tracked-delete preview behavior.

Project trash directories remain local project data and are excluded from Agent PBX file browsing and search.

## Rollback

Stop the daemon and restore the pre-Checkpoint-12 SQLite backup. Code from schema 26 ignores `.agent-pbx-trash`, but it does not understand durable trash records or prune batches. Restore or retain any project trash content before manually deleting it. Rolling back the database never deletes project files, trash content, or Git-visible deletions.

The Checkpoint 12 development backup is recorded outside Git in the execution ledger. Production operators should use their own timestamped backup and retention policy.
