# 0001 — Control-plane ownership

Status: accepted for Agent PBX v2

Agent PBX owns durable Agent and Operator identity, topology, workspaces,
permissions, lifecycle, routing, approvals, audit, and cross-session handoffs.
Codex owns the reasoning and execution loop within each attached root session.
Codex subagents are task-scoped children of one root and never become PBX peers,
campaign assignees, or queue consumers implicitly.

Durable work crossing a project, owner, or root-session boundary must use a PBX
assignment or handoff. A useful child result may be packaged into a new PBX task;
the live child process itself is not promoted.

