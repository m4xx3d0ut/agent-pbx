# Agents, Operators, and Runtimes

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

## Agent lifecycle

An Agent binds a stable PBX identity to one project/workspace and, when
available, a Codex session and managed tmux runtime. Agents can register from an
existing Codex session or be launched from the F10 project picker. Approved
project roots constrain what the picker can open.

Starred Agents are retention signals. Hiding removes an entity from the normal
list without deleting its thread or runtime evidence. Prune is preview-first
and hide-only: active, starred, queued, campaign-linked, handoff-linked,
knowledge-linked, and Operator-related entities remain guarded.

## Operator topology

A root Operator coordinates one or more callers through campaigns. Its edit and
review forks remain children of the root:

- edit forks can perform bounded coordination and approved project changes;
- review forks use a read-only source checkout and a separate writable review
  root;
- nested Codex children inherit the owning fork's restrictions;
- reusable context moves through reviewed knowledge links or handoffs;
- new sibling projects require an approved PBX project-spawn request.

Acknowledging a root alert also clears the related non-review fork alert when
both describe the same update. F8 moves to and clears one alert at a time so a
queue of alerts remains reviewable.

## Report and nohup modes

Report mode is the normal local workflow. Agents send milestone and completion
reports, while interactive follow-up uses the native terminal. Report mode does
not poll the command queue.

Explicit nohup mode adds bounded queue polling for detached workflows. An Agent
must be deliberately registered with nohup enabled; queued commands, pings, and
plan choices never imply it. Compatibility polling can be disabled only after
the local native/event-stream path is qualified.

## Restart, resume, and model transition

Model and reasoning settings are treated as session-static. A requested profile
change waits for a safe turn boundary, preserves the Codex session identifier,
restarts/resumes in the managed pane, verifies the effective profile, and keeps
rollback evidence. If no healthy v2 mapping exists, an explicit restart may
create a fresh managed pane and resume the known session. It fails closed when
the association cannot be proved.

Lifecycle repair and native adoption are preview-first actions. They must never
retarget a reused pane ID or retire a healthy source before the replacement is
verified.

## Codex children

The runtime tree appears above `Latest` only after the daemon's read-only Codex
event bridge associates native child activity with the exact attached session.
It shows lifecycle state, non-sensitive identifiers, and explicit structured
role labels; it does not expose prompts, hidden reasoning, child filesystem
paths, tool content, or roles inferred from paths. See the
[subagent qualification record](../acceptance/v2.1.2-subagent-activity.md).
