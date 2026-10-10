# Agent PBX wiki source

> **Applies to:** Agent PBX 2.2.x
> **Last reviewed:** 2026-10-10

This directory is the versioned source for the Agent PBX project wiki. The
pages are useful directly in the repository and can later be copied to the
GitHub wiki after release review. Repository history remains the authority;
publishing the files elsewhere must not introduce workstation configuration,
credentials, private personality overlays, or generated runtime state.

Start with [Home](Home.md). The recommended page order is:

1. [Getting Started](Getting-Started.md)
2. [Concepts and Architecture](Concepts-and-Architecture.md)
3. [TUI and Keymap](TUI-and-Keymap.md)
4. [Agents, Operators, and Runtimes](Agents-Operators-and-Runtimes.md)
5. [Runtime and tmux Operations](Runtime-and-Tmux-Operations.md)
6. [Models, Profiles, and Codex](Models-Profiles-and-Codex.md)
7. [WorkerBee Integration](WorkerBee-Integration.md)
8. [Joplin Notes](Joplin-Notes.md)
9. [Security and Remote Access](Security-and-Remote-Access.md)
10. [Troubleshooting and Recovery](Troubleshooting-and-Recovery.md)
11. [Packaging, Releases, and Upgrades](Packaging-Releases-and-Upgrades.md)

Before publication, run:

```bash
python scripts/check_docs.py
python -m pytest -q tests/test_docs.py tests/test_readme.py
agent-pbx wiki check --source docs/wiki
agent-pbx wiki stage --source docs/wiki --output artifacts/wiki-stage \
  --revision "$(git rev-parse HEAD)"
```

The checker validates repository-local links, page applicability/review
markers, public-source hygiene, and every `agent-pbx` command path shown in a
shell block by running its safe `--help` form.

Review `artifacts/wiki-stage/pages`, `_Sidebar.md`, `manifest.json`, and the
deterministic `wiki-pages.tar`. Publication is a separate explicit action. The
confirmation value must exactly match `stage_digest` from the reviewed
manifest:

```bash
agent-pbx wiki publish \
  --stage artifacts/wiki-stage \
  --remote git@github.com:m4xx3d0ut/agent-pbx.wiki.git \
  --confirm '<reviewed-stage-digest>'
```

The publisher updates only the staged page names and preserves unknown remote
wiki pages. It follows the remote wiki's advertised default branch unless an
explicit `--branch` override is supplied. It does not delete or mirror arbitrary
remote content.

