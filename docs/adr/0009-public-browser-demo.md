# ADR 0009: Public browser demo boundary

- Status: accepted
- Scope: repository-owned interactive product demo runtime

## Decision

Agent PBX owns the executable half of its interactive browser demo. The demo
runs the real packaged Textual TUI through Textual Serve, but every browser
connection receives a new disposable Agent PBX system: private HOME and XDG
directories, SQLite database, loopback daemon, generated Git projects,
deterministic Codex/WorkerBee/GitHub/Joplin fixtures, and dedicated tmux server.

The product site consumes a versioned HTTP contract and embeds the terminal
surface. It does not reproduce TUI state, import Agent PBX implementation code,
or own fixture lifecycle. This keeps the demo synchronized with the released
TUI while allowing the site to own surrounding explanation, responsive framing,
analytics, consent, availability messaging, and the static fallback.

The browser delivery process passes a fixed argument vector to each child and
constructs its environment from an allowlist. It never forwards host Agent PBX,
Codex, OpenAI, Joplin, GitHub, cloud, SSH-agent, Kubernetes, WorkerBee, Docker,
or tmux values. Public binds require an explicit HTTPS public URL, an explicit
publication flag, and exact browser origins. The service caps concurrent
sessions, terminal geometry, input-message size, and session lifetime.

No live provider, repository, daemon, tmux server, MCP endpoint, credential, or
user home directory is part of the public scenario. Disconnect, timeout, startup
failure, or service shutdown ends the child TUI and removes its daemon, fixture
services, tmux server, database, projects, logs, and state root.

## Consequences

- Product demonstrations execute current Agent PBX behavior instead of a
  separately maintained visual imitation.
- A visitor can exercise navigation, reports, managed projects, native tmux,
  campaigns, files, WorkerBee, GitHub, and Joplin without reaching operator
  infrastructure.
- Textual Serve remains an optional install extra and does not enlarge the
  default server/TUI dependency set.
- Internet publication still requires an operator-owned TLS reverse proxy,
  resource limits, egress controls, monitoring, and a static fallback.
- The demo is intentionally synthetic and cannot be switched to live mode by a
  browser request or environment variable.

## Rollback

Stop the demo service and remove the optional `web-demo` installation. No
production database, Agent/Operator mapping, configuration, or tmux session is
migrated by this feature. The static hero image remains a complete fallback.
