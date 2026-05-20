# Repository Guidelines

## Project Structure & Module Organization

This repository is currently in the planning stage. `R&D_PLAN.md` defines the intended Agent PBX scope: a local/LAN MCP server plus a lightweight Python TUI for monitoring and interacting with multiple Codex agents. `.gitignore` already reserves common Python, test, local state, and generated artifact paths.

As implementation begins, keep source code under `src/agent_pbx/`, tests under `tests/`, and operational notes under `docs/` or `ops/` as appropriate. Treat `.codex/`, `.env`, `.venv/`, `state/`, `artifacts/`, and `runs/` as local-only or generated data.

## Build, Test, and Development Commands

No build system or package metadata is committed yet. Until tooling is added, use standard Python commands:

- `python -m venv .venv` creates a local virtual environment.
- `source .venv/bin/activate` activates it for development.
- `python -m pytest` should run the test suite once `tests/` exists.
- `python -m agent_pbx` is the preferred future local entry point if the package exposes one.

When adding project tooling, document commands in `README.md` and keep this file aligned.

## Coding Style & Naming Conventions

Use Python for the MCP server and TUI unless the project direction changes. Prefer 4-space indentation, type hints for public functions, small modules with clear responsibilities, and `snake_case` for modules, functions, variables, and CLI commands. Use `PascalCase` for classes and `UPPER_SNAKE_CASE` for constants.

Prefer structured data formats and parsers over ad hoc string handling for callback payloads, spool records, and configuration.

## Testing Guidelines

Use `pytest` for unit and integration tests. Name test files `test_*.py` and mirror source modules where possible, for example `tests/test_spool.py` for `src/agent_pbx/spool.py`. Cover callback payload validation, SQLite spool behavior, MCP tool boundaries, and TUI interaction logic. Avoid relying on LAN services in default tests; gate those behind explicit integration markers.

## Commit & Pull Request Guidelines

This repository has no commit history yet, so no existing convention can be inferred. Use short, imperative commit subjects such as `Add sqlite spool schema` or `Implement agent status callback`. Keep commits focused.

Pull requests should include a concise summary, test results, configuration changes, and any security implications. Include screenshots or terminal captures for TUI changes.

## Security & Configuration Tips

The planned server may run on localhost or a LAN. Default to localhost-only binding, keep secrets out of Git, and do not expose the MCP server to the internet without a documented authentication and network security plan.

## Agent PBX Session Behavior

When an operator asks an agent to start using Agent PBX, treat PBX as active for
the rest of that session. Continue reporting turn status, polling queued
commands, and acknowledging handled commands until the operator explicitly asks
you to stop using PBX or starts a new session. A `request_detail` command is not
self-resolving; after polling it, respond with a new detailed `pbx_report_turn`.
