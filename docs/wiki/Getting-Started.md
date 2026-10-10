# Getting Started

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

## Host requirements

A local Agent PBX workstation needs Python 3.10 or newer, Git, OpenSSH client
tools, tmux 3.2 or newer, and a supported Codex CLI. Joplin, GitHub CLI,
clipboard helpers, and WorkerBee are optional integrations. Agent PBX reports
missing host tools but does not silently install operating-system packages.

## Install

Use a virtual environment for a package-index installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install agent-pbx
agent-pbx --version
```

Source contributors can install the checkout with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Guided configuration

The setup assistant previews every known configuration change before writing
the private user configuration. Repeat `--project-root` to approve more than
one parent directory for the F10 workspace launcher.

```bash
agent-pbx setup plan --project-root "$HOME/git"
agent-pbx setup apply --project-root "$HOME/git"
agent-pbx doctor
```

The clean-user default is a dedicated PBX runtime tmux server. It works whether
or not the TUI itself is inside tmux. Use `outer_if_present` only when the
workstation should deliberately share a validated outer server:

```bash
agent-pbx setup plan \
  --project-root "$HOME/git" \
  --runtime-server-mode outer_if_present
```

Setup modifies only known keys, writes private files with mode `0600`, and
records a checksum-guarded backup. It does not start services, install host
packages, edit shell startup files, or write credentials. Interrupted setup can
be resumed; rollback refuses to overwrite unrelated later edits:

```bash
agent-pbx setup resume
agent-pbx setup rollback
```

## Start the control plane

```bash
agent-pbx mcp start
agent-pbx mcp status
agent-pbx tui
```

The installed `agent-pbx-tui` entry point is convenient for a TUI-only client
because it reads its private TUI environment file. `apbx` is not a separate
repository command; workstations may define it as a local alias for that
entry point:

```bash
alias apbx='agent-pbx-tui'
```

Keep that alias in private shell configuration. Documentation and automation
should use the shipped `agent-pbx` and `agent-pbx-tui` commands.

## First checks

1. Confirm `doctor` reports the intended daemon, Codex, tmux, model catalog,
   managed theme, and optional integration posture.
2. Press F10 or use `/workspace launch` to start a caller in an approved Git
   repository.
3. Select the Agent and confirm `Latest` shows the native managed terminal.
4. Press F5 to reach Operators and use the Start Operator action when a caller
   should receive a coordinating root Operator.
5. Press F2 for Events and F3 for the right-side tab row.

Continue with [TUI and Keymap](TUI-and-Keymap.md) and
[Runtime and tmux Operations](Runtime-and-Tmux-Operations.md). The full host,
wheelhouse, migration, and rollback procedure is in
[the v2 installation guide](../install-v2.md).

