# 0003 — Runtime evidence and protocol use

Status: accepted for Agent PBX v2

Agent PBX consumes versioned internal runtime types. Codex app-server payloads
remain behind an adapter because the protocol is experimental. Structured
events may control state only after PBX proves their relationship to the mapped
Codex session.

Evidence precedence is app-server, PBX report, authenticated hook, transcript,
process/terminal metadata, then rendered tmux heuristics. Every normalized state
records source, observation time, and confidence. A lower-priority heuristic
cannot silently replace newer structured evidence.

Capabilities use `unsupported`, `advertised`, and `observed`. Features that
affect correctness require observed support before becoming a default.

Tmux runtime absence is also provenance-bound. The TUI may discard one local
cache entry for a canceled, inactive edit fork only when a fresh daemon probe
reports `missing`, the durable mapping agrees exactly with the Agent's recorded
server mode/ID, session, pane, and Codex session, no writer or embedded client
exists, a fresh local/outer pane listing has no target-owned pane, and the
mapped process identity is conclusively gone. Failure to parse
process-start evidence is not proof of death: an existing, unreadable, or
malformed `/proc/<pid>` remains ambiguous and fails closed. Bare pane numbers
are never global identities; a collision on a different socket/server cannot
resurrect or protect a historical mapping, while missing or inconsistent
socket/server/session evidence prevents cleanup.

## Manually approved host-process identity attestation

Agent PBX may expose a read-only MCP attestation executed by the daemon in its
host process namespace. This capability is not a process-discovery, inspection,
or lifecycle API. It accepts a bounded, explicitly approved candidate PID list
and compares only four identity fields for each candidate:

- the `/proc/<pid>/exe` link;
- only the first NUL-delimited field of `/proc/<pid>/cmdline`;
- the `/proc/<pid>/ns/user` link; and
- the `/proc/<pid>/ns/mnt` link.

The request is authorized only through the authenticated global-controller MCP
surface. The requesting Operator must be active under the exact activated
workspace-set digest. Its campaign must be running, and its handoff must be
acknowledged or running, target that Operator, retain root-Operator delegation,
and contain a documented TUI F8 approval. Campaign ID, handoff ID, workspace
digest, approval ID, candidate PIDs, executable, argv0, and both namespace
predicates must exactly match the durable approval scope. Missing, stale, or
partially matching authority fails closed.

Results are fixed-size metadata: match count, a host PID only when exactly one
candidate matches, four equality flags, observation timestamp, and a
fail-closed reason. Audit events retain only those result fields and authority
identifiers. Raw cmdline bytes, later argv fields, environment, cwd, file
descriptors, memory, and arbitrary proc content are neither returned nor
retained. The reader opens `cmdline` without following symlinks and consumes
one byte at a time only through its first NUL. It reads every permitted identity
twice and rejects changed, deleted, unreadable, malformed, duplicate,
ambiguous, unbounded, or non-host evidence.

The capability has no namespace-entry, signal, stop, restart, mutation,
credential, provider, or external-action parameter. It does not infer approval
from authentication alone and does not weaken handoff delegation rules.

The source implementation is inert until a reviewed package is installed and
the Agent PBX daemon is restarted under a separately approved activation. A TUI
restart is not required merely to load the daemon MCP tool. Existing campaigns
and handoffs do not gain authority retroactively: a new or updated handoff must
carry the complete bound approval scope before the capability can return a
match.

