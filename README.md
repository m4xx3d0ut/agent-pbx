# Agent PBX

Agent PBX is a local/LAN development service for collecting agent turn reports, storing full response details, and queuing follow-up commands for agents to poll.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
agent-pbx serve --host 127.0.0.1 --port 8765
```

The server defaults to localhost. LAN binding requires bearer-token authentication and explicit operator intent.

## Planned Local Validation

WorkerBee is used to rebuild and run the containerized MCP/API service with simulated agents and clients. Repository-owned WorkerBee manifests live under `ops/workerbee/` once deployment manifests are introduced.
