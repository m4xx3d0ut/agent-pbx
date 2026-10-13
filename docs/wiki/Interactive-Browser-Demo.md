# Interactive Browser Demo

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-12

The optional browser demo runs the real packaged Textual TUI while keeping the
operator workstation outside the trust boundary. Every WebSocket connection
receives private temporary HOME and XDG directories, a SQLite database, a
loopback daemon, generated Git projects, deterministic Codex, WorkerBee,
GitHub, and Joplin fixtures, and a dedicated tmux server.

The demo cannot attach to live Agents, Operators, credentials, repositories,
Codex state, PBX state, or host tmux sockets.

## Local qualification

Install the optional delivery dependency and run the nonmutating checks:

```bash
python -m pip install "agent-pbx[web-demo]"
agent-pbx demo doctor
agent-pbx demo contract
agent-pbx demo web --host 127.0.0.1 --port 8000
```

Source contributors can use `python -m pip install -e ".[web-demo]"` from a
checkout. The local conformance path also requires Git and tmux.

## Product-site boundary

Agent PBX owns the executable demo, fixtures, policy, visitor lifecycle, and
the versioned `agent-pbx.public-demo/v1` contract. A product site owns its
surrounding explanation, responsive frame, analytics and consent, availability
message, and static fallback. It should read `/demo-contract.json` rather than
copy terminal dimensions, keyboard help, or policy text into site code.

The service exposes:

| Path | Purpose |
|---|---|
| `/` | Textual browser client |
| `/ws` | isolated visitor session |
| `/healthz` | bounded service health without visitor data |
| `/demo-contract.json` | product-site integration contract |

## Public deployment requirements

A non-loopback bind requires an explicit HTTPS public URL, publication flag,
and exact embedding origins. The reverse proxy must terminate TLS and preserve
WebSocket upgrades for `/ws`. Run the container as an unprivileged user with a
read-only root filesystem, temporary writable storage, resource and connection
limits, bounded concurrency, and a short session lifetime.

Do not mount a real home directory, Docker socket, SSH agent, Kubernetes
configuration, Codex home, Agent PBX state, provider credential, or host tmux
socket. Deny outbound network access except explicitly reviewed monitoring.
Keep a complete static product page available when the demo is unhealthy.

The full container, reverse-proxy, origin, test, and rollback procedure is in
the repository's [browser demo guide](../public-browser-demo.md) and
[architecture decision](../adr/0009-public-browser-demo.md).
