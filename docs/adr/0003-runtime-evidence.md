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

