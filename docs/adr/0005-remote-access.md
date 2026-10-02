# 0005 — Remote access boundary

Status: accepted for Agent PBX v2

Local execution and SSH attachment provide the native terminal path. A remote
TUI uses an authenticated Agent PBX event/control service with independent view
state, bounded replay, and explicit observer/controller roles.

Agent PBX does not expose tmux sockets or raw Codex app-server endpoints over the
LAN. Non-loopback event transport requires TLS, short-lived scoped credentials,
revocation, rate limits, and audit. Full remote terminal streaming remains a
separately threat-modeled future service.

