# Agent PBX v2.0 acceptance record

This record maps the locked `AGENT-PBX-CYCLE-1026` scope to implementation and
verification evidence. A pass means the behavior is covered by automated tests
or an isolated live exercise. Host-specific steps are called out separately so
they cannot be mistaken for portable test evidence.

## Release identity

- Baseline: `70978b2`, tag `next-cycle-baseline-2026-10-02`
- Alpha 1: `e600cdc`, tag `v2.0.0a1`
- Alpha 2: `05ed799`, tag `v2.0.0a2`
- Beta 1: `b63b9c3`, tag `v2.0.0b1`
- Beta 2: `c964ec9`, tag `v2.0.0b2`
- Release candidate 1: `18771a2`, tag `v2.0.0rc1`
- Release candidate 2: recorded on tag `v2.0.0rc2`
- Final: recorded on tag `v2.0.0`

## Checkpoint evidence

| Checkpoint | Result | Evidence |
|---|---|---|
| 1 — contracts and characterization | Pass | ADRs 0001–0006; typed contracts and baseline fixtures; `b9ba1dd` |
| 2 — TUI decomposition | Pass | panel/action/focus/async boundaries; `2372cdd` |
| 2A — embedded terminal qualification | Pass | Pyte/PTTY conformance, F-key translation, dedicated and outer-server fixtures; ADR 0007; `f43a181` |
| 3 — actions, focus, async | Pass | typed registry, focus generations, cancellable workers, F1–F24 matrix; `dab8844` |
| 4 — event stream | Pass | snapshots, sequence cursors, replay/resync, WebSocket auth; `26645c5` |
| 5 — Codex adapter | Pass | capability cache, state provenance, session association, copy/transcript/tmux fallbacks; `6df9b63` |
| 6 — profiles, skills, model policy | Pass | Roses profile, private overlay metadata, managed skills, live catalog checks, bounded elevation; `091a021` |
| 7 — tmux registry | Pass | dedicated/outer modes, identity reconciliation, writer lease, recursive-attach guard; `170bbb3` |
| 8 — embedded `Latest` and pop | Pass | PTY-backed normal tmux client, F2/Shift+F2 ownership, native input/resize, fallbacks; `1c2dbe1` |
| 9 — launch and migration | Pass | Git-scoped project launch and transactional legacy migration; `11be332` |
| 10 — runtime UX | Pass | provenance-aware status and observed child topology; accessible theme tokens; `8489d39` |
| 11 — Joplin consistency | Pass | revisions, conflicts, durable sync jobs, Markdown read/edit modes; `ca1e07a` |
| 12 — Editor CRUD and trash | Pass | safe CRUD, manifests, restore, retention, tracked/untracked policy; `a2d23ba` |
| 13 — lifecycle and campaigns | Pass | unified lifecycle, repair previews, guarded prune, lazy nested panels; `dd126cf` |
| 14 — secure remote clients | Pass | role/scoped credentials, TLS/WSS, replay/resync, SSH attach, audit; `6d37d0f` |
| 15 — release and migration | Pass | doctor, backups, dry-run/apply/verify/rollback, SBOM, checksums, offline wheelhouse; `c913c37`, `34ae50e`, `f9c0ae3` |
| v2 compatibility/default | Pass | native local tmux/event defaults; retained polling/Thread/capture gates and posture; `254390c` |

## Automated validation

### Repository suite

The release gate requires:

```text
python -m compileall -q src tests
python -m pytest -q
git diff --check
```

The RC local gate passed all **790 tests with one expected skip**. It was split
only to preserve progress while qualifying the launcher-default correction:

```text
tests/test_tui.py:                 406 passed in 330.88s
all tests except tests/test_tui.py: 384 passed, 1 skipped in 285.65s
```

CI executes the complete suite on Linux and macOS with Python 3.10 and 3.12. A
separate Linux/Python 3.10 job builds and verifies the checksummed offline
wheelhouse. GitHub Actions run URLs are added after the RC push completes.

RC1 exposed a release-workflow invocation defect: the manifest verifier's
global `--wheelhouse` option was placed after the subcommand. RC2 corrects the
argument order and updates official checkout/setup actions to their Node 24
generations; the release code and local acceptance result were unchanged.

### Isolated live control-plane exercise

On 2026-10-03 an isolated loopback daemon was created in a temporary state
root without touching the workstation daemon. The following passed:

- `/healthz` returned the expected v2 version and machine-readable compatibility posture;
- the v2 WebSocket delivered a snapshot envelope;
- the complete CI-safe Operator KB/handoff UAT ran stages 0–9 plus audit with 49 passing assertions, zero failures, and zero cleanup failures;
- migration verification reported schema 28, integrity `ok`, no foreign-key violations, and no missing tables;
- the Codex Operator matrix generated eight spawn/fork/review/resume cases for Codex 0.159.3 and `gpt-5.6-sol`;
- stopping the isolated daemon left no running test service.

### Packaging

- A Python 3.10 wheelhouse was built with package hashes, dependency manifest,
  SPDX 2.3 SBOM, archive checksum, and safe extractor checks.
- A clean virtual environment installed exclusively from that wheelhouse.
- The installed commands reported the packaged version and executed doctor.
- The installer rejects traversal, links, devices, and checksum mismatch.

## Capability and integration findings

- Codex CLI on the qualification host is 0.159.3. The local app-server may
  advance independently; doctor reports version skew instead of hiding it.
- WorkerBee 0.1.7 reports `workerbee_v1_capabilities` and
  `workerbee_v1_session_start`. Session startup remained lazy and launched no
  workload.
- WorkerBee still reports `git_root=null`, `git_branch=null`, and
  `repo_exists=false` for this valid checkout. Agent PBX keeps this as a
  distinct path-visibility diagnostic and does not infer a missing MCP method
  or patch the WorkerBee server.
- The workstation Joplin Data API accepted optimistic ledger updates. Joplin
  conflict, restart, and offline paths remain covered by deterministic tests.

## Host qualification and migration posture

The current workstation daemon was deliberately left running during the RC
build. It is a LAN listener that predates the v2 TLS enforcement and uses an
older database schema. Restarting only the executable would create avoidable
version, transport, and schema skew.

Before switching that daemon to v2:

1. retain the existing seven-day backup;
2. configure the LAN certificate and key, or move the service to loopback plus SSH tunneling;
3. stop the daemon;
4. run `agent-pbx migrate dry-run`, `agent-pbx migrate apply`, and `agent-pbx migrate verify`;
5. start the v2 daemon and verify doctor, health, WebSocket, Agents, Operators, forks, Joplin, and WorkerBee;
6. restart the TUI so the private `outer_if_present` setting is loaded;
7. retain the pre-v2 backup for at least seven days.

The private TUI overlay is already prepared with native terminal and event
stream enabled, `outer_if_present`, and all three v2.0 compatibility paths
retained.

## Security acceptance

- Non-loopback service rejects startup without token and TLS unless the
  explicit lab-only insecure override is supplied.
- Observer and controller roles are separated; agent-scoped controllers cannot
  reach global legacy, credential, audit, or MCP surfaces.
- Remote native terminal access uses SSH; tmux sockets and raw Codex app-server
  endpoints are not exposed over the LAN.
- Secrets, provider credentials, arbitrary hidden configuration, private
  personality text, terminal history, and hidden reasoning are excluded from
  shared event records.
- Review-fork and child-agent policy tests retain read-only tool boundaries.

## Compatibility contract

Native tmux and the v2 event stream are the local defaults in v2.0. Polling,
Thread, and capture remain enabled by default, independently gated, reported
through health/doctor/status, and retained for the full v2.0 line. Their later
removal requires measured parity, migration evidence, published criteria, and
a release after v2.0.
