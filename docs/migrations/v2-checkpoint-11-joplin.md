# V2 checkpoint 11: Joplin consistency migration

Checkpoint 11 advances the Agent PBX SQLite schema from 25 to 26. Startup is
additive and creates durable Joplin edit sessions and conflict records while
extending sync jobs with profile, generation, progress, affected-note, and
structured-error fields.

Before applying this checkpoint to a live daemon:

1. Stop write traffic or use SQLite's online backup API.
2. Copy the database to a timestamped `v2-cp11-pre-schema26-*.sqlite` backup.
3. Run `PRAGMA integrity_check` against the backup.
4. Initialize a copy with the new `Store` and verify schema 26 before restarting
   the daemon against the production database.

The migration was qualified against a schema 22 live-state backup as well as
fresh databases. Running sync jobs are returned to `queued` during startup, so
an interrupted daemon can resume them. Redundant queued jobs with the same
profile and reason are coalesced; manual sync remains a separate job.

Rollback requires stopping the v2 daemon and restoring the pre-migration
database backup. Restore the matching daemon configuration at the same time.
Do not copy schema-26 tables into an older database. Joplin note bodies remain
in Joplin and are not removed by database rollback.

The new write contract is optimistic:

- the editor records the note revision, hashes, and original title/body;
- save re-reads Joplin and returns HTTP 409 when that revision changed;
- conflict actions preserve both versions through merge review, keep-current,
  conflict-copy, or an explicitly confirmed audited overwrite;
- read mode renders Markdown, while edit and preview are explicit modes;
- dirty drafts are persisted in the daemon and protected across note selection,
  tab changes, and application exit.

