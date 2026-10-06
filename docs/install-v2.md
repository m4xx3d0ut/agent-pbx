# Agent PBX v2 installation and upgrade

Agent PBX is a runtime-grounded Agentic Engineering Control Plane. Read the
[architecture overview](architecture.md) for the ownership boundary among Agent
PBX, Codex, tmux, WorkerBee, and the local or remote operator surfaces.

Agent PBX v2 supports three operating shapes. They use the same hash-verified
Python release bundle and differ in the host tools and services enabled.

## Server/API host

Required:

- Python 3.10 or newer;
- Git and OpenSSH client tools;
- a user-writable Agent PBX state directory;
- TLS certificate/key plus bearer authentication for every non-loopback bind.

Tmux and Codex are optional only when this host serves API/event data without
owning Agent runtimes. Joplin and WorkerBee remain optional integrations.

## Local workstation

Install the server requirements plus:

- tmux 3.2 or newer;
- Codex CLI through its supported package method;
- a terminal with 256-color or true-color and distinguishable F13–F24 sequences;
- one platform clipboard helper when direct `/copy` capture is desired.

Linux examples:

```bash
# Debian/Ubuntu
sudo apt install python3 python3-venv git openssh-client tmux

# Fedora
sudo dnf install python3 git openssh-clients tmux
```

macOS example:

```bash
xcode-select --install
brew install python git tmux
```

Agent PBX reports missing packages and commands but does not install operating
system software. Run `agent-pbx doctor` after host preparation.

## Remote TUI client

Install the Agent PBX wheelhouse on the thin client and configure an HTTPS/WSS
server URL plus a short-lived observer or controller credential. Native Codex
terminal interaction should use the generated SSH attach command. The remote
API does not expose a tmux socket or raw Codex app-server endpoint.

```bash
export AGENT_PBX_SERVER_URL=https://pbx.example.internal:8767
export AGENT_PBX_TOKEN='<one-time-issued remote credential>'
export AGENT_PBX_TUI_EVENT_STREAM_V2=1
agent-pbx-tui

agent-pbx remote ssh-attach \
  --host user@pbx.example.internal \
  --entity operator-0 \
  --read-only
```

## Verified wheelhouse install

Release assets contain:

```text
install-agent-pbx.sh
agent-pbx-wheelhouse.tar.gz
agent-pbx-wheelhouse.tar.gz.sha256
```

The archive contains wheel checksums, a dependency manifest, and an SPDX 2.3
SBOM. The installer verifies the HTTPS-delivered archive checksum before safe
extraction and verifies every wheel before installation. Checksums detect
corruption and mismatch; they do not replace release provenance or transport
authentication. Set `AGENT_PBX_OFFLINE=1` to prohibit package-index fallback if
the wheelhouse does not contain a compatible native wheel.

```bash
curl -fsSL https://github.com/m4xx3d0ut/agent-pbx/releases/latest/download/install-agent-pbx.sh | sh
agent-pbx --version
agent-pbx doctor
```

For an air-gapped installation, place all three assets in one directory:

```bash
AGENT_PBX_INSTALL_BASE_URL="file://$(pwd)" \
AGENT_PBX_OFFLINE=1 \
./install-agent-pbx.sh
```

Wheelhouses containing native wheels are platform and Python specific. Build
and test separate release artifacts on each supported Linux/macOS architecture
and Python ABI. A Linux wheelhouse must not be represented as a macOS bundle.

## Safe upgrade

Run these commands from the newly installed code while the old daemon still
runs:

```bash
agent-pbx doctor
agent-pbx migrate dry-run
agent-pbx migrate backup --retention-days 7
agent-pbx mcp stop
agent-pbx migrate apply --retention-days 7
agent-pbx migrate verify
agent-pbx mcp start
agent-pbx doctor
```

`migrate backup` uses SQLite online backup and may run while the old daemon is
serving. `migrate apply` and `migrate rollback` refuse to run while the managed
daemon is active. Apply creates another backup before any schema change.

The backup directory is mode `0700`; copied database and configuration files
are mode `0600`. It may contain the local Agent PBX environment and Codex
configuration, including credentials. Keep it on trusted encrypted storage and
remove it only after its reviewed retention period.

## Rollback

Keep the printed backup path. To restore the database and runtime mappings:

```bash
agent-pbx mcp stop
agent-pbx migrate rollback \
  --backup /path/to/v2-pre-migration-... \
  --confirm RESTORE
agent-pbx mcp start
```

Add `--restore-config` only when the matching local Agent PBX/Codex configuration
must also be restored. Rollback makes a pre-rollback safety backup first. It
does not delete Codex transcripts, project files, Git worktrees, Joplin notes,
or project trash created after the selected backup.

## Compatibility posture

V2 keeps nohup polling, the Thread tab, and the capture-based terminal fallback
through the entire v2.0 line. Native tmux and the v2 event stream are the
preferred local path. Legacy removal requires measured usage, parity, a tested
migration path, and a later release with published notice; calendar age alone
does not trigger removal.
