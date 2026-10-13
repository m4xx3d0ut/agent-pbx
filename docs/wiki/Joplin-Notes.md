# Joplin Notes

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

Agent PBX provides project-scoped Joplin reading, editing, response capture,
campaign export, and durable sync coordination. Joplin credentials remain on
the daemon host; remote TUI clients receive scoped note data through Agent PBX.

## Scope and modes

Notes live beneath the configured Agent PBX notebook and project folder. The
tab opens in rendered Markdown reading mode. Explicit Edit switches to source
mode; Preview returns to rendered content without publishing. Dirty drafts are
protected across selection, tab, disconnect, and exit transitions.

Project folders use the caller's route-safe logical Agent PBX project name.
The absolute working directory remains separate registration metadata and does
not replace that identity during later reports or refreshes. Listing or opening
a missing project or Agent scope returns an empty/not-found result without
creating Joplin folders; only an explicit create action may initialize them.

The reading view is keyboard-focusable. Arrow keys and Page Up/Page Down work
over desktop, SSH, and Termux clients. Each note keeps a revision-scoped scroll
position, so an unchanged refresh does not jump to the top while a changed
revision cannot restore a stale offset.

Copy and paste actions are explicit. Copy can read selected Markdown or the
whole note. Paste is edit-only and revalidates the note/revision before writing;
large or multiline payloads receive metadata-only confirmation so clipboard
contents are not echoed into dialogs or logs.

## Response capture

`/joplin copy` records the latest response with a prompt-derived title and
session provenance. Capture methods are configurable. The reliable sequence is
the chosen primary method followed by structured transcript and visible tmux
capture fallbacks. Transcript results identify session, path, phase, line,
mtime, response hash, and turn boundary so an old copy buffer cannot be treated
as the current response.

Interactive Codex copy confirmations are detected and dismissed after capture.
Failure of one method should continue to the next and should not leave the
Codex pane blocked.

## Sync and conflict safety

Writes return after the local Joplin API verifies the target note. Optional
WebDAV sync uses durable jobs and a profile ownership policy. Two-way edits use
base revisions and hashes; when Joplin changed after the draft began, Agent PBX
offers review/merge, keep-Joplin, conflict-copy, or explicit overwrite paths.
There is no silent last-writer-wins path.

Joplin-native conflict copies are labeled `⚠ LOCAL` and remain read-only.
They do not have a remote sync item, so Agent PBX rejects targeted sync jobs
that name them instead of reporting false success. `Promote Local` or
`/joplin promote conflict` requires a second action within five seconds, checks
the current note revision and original-note identity, and creates a uniquely
titled normal note with the same Markdown body. The readable conflict and its
encrypted original remain intact for rollback and later E2EE recovery.

Encrypted items appear as locked rather than empty. Mutation remains disabled
until the dedicated Joplin profile decrypts the item. A targeted write may be
successful when its verified note is plaintext even if an unrelated encrypted
item remains, and targeted jobs do not invoke whole-profile decryption. The TUI
reports write health separately from profile E2EE posture. `Validate` or
`/joplin validate profile` runs the strict profile-wide check; `/joplin sync`
remains a compatibility alias and still reports unresolved E2EE items.

## Credential boundary

Configure WebDAV and E2EE inside the dedicated Joplin profile. Agent PBX does
not store or display the E2EE master password. API tokens and any external
credential helper files must be private and excluded from Git. Diagnostics
report states such as password required, decrypt failure, profile locked, and
API recovery without printing secret values or ciphertext.

See the repository [Joplin migration note](../migrations/v2-checkpoint-11-joplin.md)
and the Joplin section of the [README](../../README.md) for configuration keys
and conflict semantics.
