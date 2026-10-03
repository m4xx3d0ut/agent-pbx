# V2 Checkpoint 14: Secure remote clients and SSH-native attach

Checkpoint 14 advances the Agent PBX SQLite schema from 27 to 28. It adds
role-scoped remote credentials, independent remote-client state, an audit
journal, authenticated v2 WebSocket replay, optional read-only terminal
snapshots, and an SSH-native path to PBX-managed tmux runtimes.

## Security boundary

The daemon keeps the existing loopback workflow. A non-loopback bind now
requires both a bearer token and a TLS certificate/key pair unless the operator
explicitly sets the existing insecure-LAN override. Remote credentials are
hashed at rest and carry:

- a stable token ID, observer or controller role, audience, expiry, and
  revocation timestamp;
- an optional client-ID binding;
- optional Agent/Operator identity bounds and declared scopes;
- no recoverable copy of the bearer secret after issuance.

Observers may read their identity, scoped/redacted v2 event snapshots and
streams, their own view state, and an explicitly enabled terminal snapshot.
They cannot use legacy APIs or MCP. Controllers can perform audited lifecycle
actions through the remote-control endpoint; destructive operations preserve
the lifecycle preview/apply token gate. Token revocation is checked on every
HTTP request and during an open WebSocket session.

The event stream has bounded clients, bounded inbound messages, cursor replay,
overflow resynchronization, Agent filtering, and field redaction. Client IDs
accept only letters, numbers, dot, underscore, colon, and hyphen. A durable
client ID cannot be claimed by a different token.

## Client behavior

Enable the v2 stream in a TUI client with:

```bash
export AGENT_PBX_TUI_EVENT_STREAM_V2=1
```

The TUI resolves a token-bound client identity when present and otherwise
persists a generated identity in its local settings. It stores navigation state
per client. Prompt input, terminal input, editor buffers, Joplin drafts, auth
material, and reasoning are excluded from remote view-state storage.

Observer-mode startup uses the scoped v2 snapshot and does not call privileged
Agent or Event list endpoints. A remote host does not run local Codex posture or
tmux inspection against the thin client.

For full native terminal fidelity, attach over SSH:

```bash
agent-pbx remote ssh-attach \
  --host user@pbx-host \
  --entity operator-0-review-fork-3 \
  --read-only
```

The generated command runs `agent-pbx runtime attach` on the PBX host. The tmux
socket remains host-local. The raw Codex app-server endpoint and tmux socket are
never exposed by the remote API.

Read-only terminal snapshots remain disabled unless
`AGENT_PBX_REMOTE_TERMINAL_SNAPSHOTS=1` is set. Snapshots are line/byte bounded,
Agent scoped, secret-pattern redacted, and audited. They are an observation
fallback, not terminal streaming.

## Upgrade verification

1. Back up the SQLite database and record its schema version and integrity.
2. Start the Checkpoint 14 daemon. `Store.init()` atomically adds token metadata,
   `remote_client_sessions`, and `remote_audit_events`, then records schema 28.
3. Verify `PRAGMA integrity_check` returns `ok`.
4. Issue separate observer and controller credentials with short expiries and a
   server-specific audience. Save the raw bearer secret once; it cannot be read
   back later.
5. Connect two clients and verify their selected tabs and cursors remain
   independent.
6. Revoke one credential while its WebSocket is open and verify immediate
   closure with no data from the other client's Agent scope.
7. Verify observers receive HTTP 403 from legacy API and MCP mutation surfaces.
8. Exercise SSH read-only attach before enabling terminal snapshots.

The implementation gate qualified copied databases on both supported paths:
schema 22 to 28 and schema 27 to 28. Both retained `PRAGMA integrity_check=ok`,
all legacy token rows received unique non-secret IDs, and both remote tables and
all 13 token metadata columns were present.

## Rollback

Stop the daemon before rollback. Schema 28 is additive, but Checkpoint 13 code
does not understand the new remote records. Restore the pre-checkpoint database
backup before checking out the Checkpoint 13 commit. Revoke or destroy any
remote credentials issued during the attempted upgrade, remove non-loopback
listeners, and retain the remote audit export with the operational record.

Do not expose the daemon without TLS while rolling back. SSH-native attachment
continues to work independently of the remote WebSocket service.
