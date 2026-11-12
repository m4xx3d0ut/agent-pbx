# Durable engineering control-plane acceptance

This acceptance plan qualifies the integrated post-2.3 engineering control
plane without selecting a release version or activating any feature on a live
Agent PBX daemon.

## Qualification boundary

- Exact Git review is restricted to the stopped fork's approved broker leaves;
  artifact digests are reverified before commit and no unrelated dirt enters a
  compact or full review surface.
- Evidence exports are daemon-owned, private-rooted, manifest-hashed, bounded,
  and positive-field-selected. Hidden reasoning, prompt bodies, terminal
  history, credentials, and event payloads remain excluded by default.
- KB bundle v2 extends the existing Operator KB with stable revisions,
  reconciliation, conflicts, tombstones, reviewer evidence, and deterministic
  private-Git preparation. It carries no ambient Git credentials.
- Durable helpers remain content-addressed executable bundles separate from KB
  prose. Exact-path proposal, isolated validation, lifecycle review, read-only
  materialization, and explicit invocation are mandatory.
- Dynamic resources are closed typed requests bound to Operator authority,
  campaign or handoff, workspace digest, TTL, quota, approval, lease, provider
  receipts, and cleanup. There is no generic command, executable, environment,
  host-path, device, or credential surface.
- WorkerBee development overlays bind the exact workspace binding, backend
  generation, capability digest, fixed mount target, owner/mode, quota, expiry,
  and zero-residue cleanup. Trusted direct source remains read-only and
  `rprivate`; writable direct source is not implemented.

## Provider-free test matrix

| Case | Expected evidence |
| --- | --- |
| Public resource policy | Manual approval only; auto approval denied |
| Team/campaign delegation | Exact action class, campaign, workspace digest, target Operator, use count, and expiry |
| Trusted Rosie low risk | Only the reviewed local cache/socket subset can auto-approve |
| WorkerBee development | Manual or bounded campaign lease only, including under Trusted Rosie |
| Cross-client approval | Request and F8 approval may use distinct clients while retaining one request/authority digest |
| Daemon recreation | Schema-44 request and provider receipts reload; provider status must revalidate exact identity |
| Backend drift | Capability or generation mismatch fails closed and enters `recovery_required` where provider work may exist |
| Evidence export | Durable `subject_id` events appear; payloads, provider receipts, and secret probes do not |
| Cleanup | Exact provider release proves `cleanup_complete=true` and zero residue |
| Retention | Preview is non-mutating; pruning remains separately authorized |

The final suite must use owner-private temporary roots and sockets, disable
repository bytecode/cache writes, preserve the protected dotenv deny set, and
classify any AF_UNIX, tmux, ptrace, or read-only-fixture failures separately
from candidate-owned failures.

## Migration and rollback

Schema 44 additively adds `provider_capability_sha256` to dynamic-resource
requests. Migration dry-run, backup, apply, verify, and rollback must retain all
pre-existing rows and restore the earlier database/config snapshot exactly.

Rollback is feature-local: disable new gates, deny new requests, release only
receipt-bound provider resources, retain immutable evidence, KB, helper,
resource, and cleanup records, and revert the code checkpoint only after all
leased resources are terminal. Ambiguous provider state remains
`recovery_required`; rollback must never guess a path or delete shared state.

## External integration gate

Agent PBX qualification does not implement or activate the private WorkerBee
adapter. Before activation, a separate authorized WorkerBee handoff must prove
byte-compatible capability/acquire/status/release responses against
`tests/fixtures/workerbee_development_resources_v1.json`, backend-generation
drift denial, quota/expiry bounds, timeout cleanup, fixed target visibility,
and absence of any writable source mount. Failure leaves the feature disabled.
