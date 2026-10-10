# Concepts and Architecture

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

## Durable ownership

Agent PBX separates macro orchestration from execution:

| Layer | Responsibility |
|---|---|
| Agent PBX | Durable identity, project/workspace ownership, policy, topology, routing, lifecycle, approvals, audit, campaigns, and handoffs |
| Codex root | Reasoning, tool use, implementation, tests, review, and local decomposition |
| Codex child | Bounded work under one root; never an implicit PBX peer |
| tmux | Process terminal, pane/session identity, geometry, persistence, attachment, and scrollback |
| WorkerBee | Optional application, platform, infrastructure, simulation, deployment, and runtime evidence |
| TUI/SSH/API/MCP | Human and integration surfaces over the same durable state |

The TUI is disposable. Closing it detaches one client and does not erase a PBX
identity or terminate a managed Codex runtime. Agent PBX reconciles entity,
workspace, Codex session evidence, tmux server/session/window/pane, process,
and active profile before declaring a runtime healthy.

## Agents and Operators

A PBX **Agent** is a durable project-working identity. A PBX **Operator** is a
durable coordination identity with campaigns, caller assignments, forks,
handoffs, and policy. An edit fork can coordinate bounded changes. A review
fork remains read-only against the caller checkout and routes requested changes
back through the owning control plane.

Codex children are micro-workers inside one Agent or Operator session. They may
inspect, implement, test, or review bounded work, but they do not become campaign
assignees, independently routable PBX identities, or durable owners.

## Runtime evidence

The internal state bridge prefers:

1. structured events associated with the exact session;
2. authenticated PBX reports and hooks;
3. transcript records with session, path, phase, line, mtime, and turn boundary;
4. process, terminal activity, and tmux metadata;
5. rendered terminal heuristics.

Lower-confidence screen text must not overwrite higher-confidence lifecycle
evidence. Capability states distinguish unsupported, advertised, and observed
features so a Codex update cannot silently enable an unqualified path.

## Compatibility

Native tmux and sequenced events are the preferred local v2 paths. Explicit
nohup polling, Thread, HTTP refresh, and captured-terminal fallback remain
compatibility features in 2.2.x. Removal requires measured parity, migration
evidence, and published notice.

See the maintained [architecture overview](../architecture.md) and
[architecture decision records](../adr/README.md) for formal boundaries.

