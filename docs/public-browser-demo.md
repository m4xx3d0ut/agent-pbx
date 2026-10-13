# Interactive browser demo

Agent PBX can expose its real Textual TUI as a deterministic product tour. The
repository owns the executable demo, fixture data, policy, lifecycle, and
versioned integration contract. A product site can later embed the service and
provide the surrounding presentation.

## Install and inspect

Install the optional delivery dependency from PyPI and run the nonmutating
doctor:

```bash
python -m pip install "agent-pbx[web-demo]"
agent-pbx demo doctor
agent-pbx demo contract
```

Source contributors can instead install the current checkout with
`python -m pip install -e ".[web-demo]"`.

The default command binds only to loopback:

```bash
agent-pbx demo web --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Each WebSocket connection starts one private
session and removes it on disconnect or after the default 15-minute limit.
Use `--max-sessions` and `--session-ttl-seconds` to lower the resource ceiling.

The local conformance path requires `git` and `tmux`. It does not use the
operator's daemon, database, projects, Codex credentials, integration secrets,
outer tmux server, or local Agent/Operator mappings.

## Public reverse-proxy mode

Agent PBX refuses a non-loopback bind until all publication settings are
explicit:

```bash
agent-pbx demo web \
  --host 0.0.0.0 \
  --port 8000 \
  --allow-public-bind \
  --public-url https://demo.example.com \
  --allowed-origin https://www.example.com \
  --max-sessions 8 \
  --session-ttl-seconds 600
```

`--public-url` is the externally visible Textual Serve endpoint. Agent PBX
automatically allows that service origin for its own WebSocket and uses each
additional `--allowed-origin` as an approved embedding origin and CSP
`frame-ancestors` source. The reverse proxy must terminate TLS and preserve
WebSocket upgrades for `/ws`.

The service exposes:

| Path | Purpose |
|---|---|
| `/` | Textual browser client |
| `/ws` | one isolated TUI session per WebSocket |
| `/healthz` | bounded service status without visitor data |
| `/demo-contract.json` | product-site integration contract |

Use the contract's iframe title, terminal sizing, keyboard map, policy flags,
and static-fallback metadata instead of duplicating them in deployment code.

## Container

Build the rootless image from the repository:

```bash
docker build -f ops/web-demo/Dockerfile -t agent-pbx-web-demo .
docker run --rm --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  -p 127.0.0.1:8000:8000 \
  -e AGENT_PBX_DEMO_PUBLIC_URL=https://demo.example.com \
  -e AGENT_PBX_DEMO_ALLOWED_ORIGINS=https://www.example.com \
  agent-pbx-web-demo
```

Publish the loopback port through a reviewed HTTPS reverse proxy. Apply CPU,
memory, process, file-descriptor, and connection limits at the container or
service manager. Deny outbound network access except infrastructure explicitly
required for health monitoring; all fixture integrations are local.

The image contains `git`, `tmux`, Agent PBX, and the optional web dependency. It
runs as an unprivileged user and stores every visitor runtime below temporary
directories. Do not mount a real home directory, Docker socket, SSH agent,
Kubernetes configuration, Codex home, Agent PBX state root, or integration
credential into this container.

## Development and qualification

The focused repository tests exercise policy, fixtures, the private daemon and
tmux runtime, HTTP metadata, exact-origin rejection, a real Textual WebSocket
frame, and disconnect cleanup:

```bash
python -m pytest -q \
  tests/test_public_demo_policy.py \
  tests/test_public_demo_fixtures.py \
  tests/test_public_demo_session.py \
  tests/test_public_demo_web.py
```

The media recorder continues to use the same package-owned Codex fixture via
`scripts/demo/demo_codex.py`, preventing drift between the README hero and the
interactive demo.
