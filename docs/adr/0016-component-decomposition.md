# ADR 0016: Behavior-preserving component decomposition

- Status: Accepted for implementation
- Date: 2026-11-13
- Baseline: Agent PBX v2.4.0 (`acbc24b6a95cff33b3381d6d178702b9ebcb20f0`)

## Context

`api.py`, `store.py`, and `tui.py` accumulated multiple independently governed
subsystems while Agent PBX preserved a stable HTTP, authorization, migration,
and local-operator compatibility boundary. Splitting those modules without an
executable baseline would make route movement indistinguishable from behavior
change and could accidentally move authorization, feature gates, transactions,
or execution authority into presentation code.

## Decision

Decomposition begins with a versioned compatibility boundary. A canonical
OpenAPI snapshot pins every public method, path, request and response schema,
status declaration, and operation identifier. An AST inventory sets no-growth
ceilings for `api.py`, `store.py`, and `tui.py`; extraction may reduce those
modules but may not make them larger. A schema-45 SQLite fingerprint and the
inventory of `Store` methods using `BEGIN IMMEDIATE` pin migration and explicit
write-serialization posture.

Dependency direction is one way:

1. transport adapters and route registrars depend on typed service handles,
   request/response schemas, and authorization dependencies;
2. application services depend on domain services and `Store` contracts;
3. domain services may depend on `Store`, but never on FastAPI or Textual;
4. `Store` depends only on persistence-safe schemas and utilities;
5. the TUI remains an API client and never imports API construction, routes, or
   persistence implementation.

The first extraction introduces a typed API service container while retaining
all legacy `app.state` names. Route families are registered from small modules,
but controllers continue to delegate to the same service methods. Route modules
must not import `Store` or the TUI, instantiate executors, perform migrations,
or acquire new mutation authority.

## Compatibility contract

The owned route matrix and transaction rules are documented in
[`docs/component-decomposition.md`](../component-decomposition.md). The
checked fixtures are:

- `agent-pbx.component-decomposition-contract/v1`, containing AST ceilings and
  the schema/transaction fingerprint; and
- the complete canonical OpenAPI document in `openapi_compat_v1.json`.

Changing either fixture is an explicit compatibility review, never a mechanical
side effect of moving code. The checker runs with a private temporary database
and does not start the daemon, TUI, MCP session managers, or any runtime.

## Migration and rollback

This checkpoint changes no database schema, durable value, route, default,
feature gate, or runtime. Existing construction through `create_app` remains
the public entry point. Rollback is a normal commit revert: route registration
returns to `api.py`, legacy `app.state` service handles remain valid, and no
data migration or cleanup is needed.

## Security consequences

Global-controller, token, root-Operator, manual-approval, and feature-gate
checks stay attached to their existing route operations. Controller modules may
validate and translate transport input but do not become executors. The guard
fails closed on public OpenAPI drift, Store schema or transaction drift,
forbidden dependency edges, or growth of the three decomposition targets.
