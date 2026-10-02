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

