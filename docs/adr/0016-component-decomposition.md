# ADR 0016: Behavior-preserving component decomposition

- Status: Accepted for implementation
- Date: 2026-11-13
- Baseline: locally accepted post-v2.4 candidate
  (`56f46179863fed13f339ab3e55481a895b994921`)

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
inventory of logical atomic `Store` operations pin migration and explicit
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

Boundary D0 extends that one-way rule to the planned package owners:

- `ui.screens` owns typed modal presentation without I/O or authorization;
- `ui.controllers` owns TUI orchestration without API composition or direct
  persistence;
- `api_routes` owns HTTP translation without direct persistence or UI access;
  and
- `persistence.repositories` owns SQLite operations behind the public `Store`
  facade without transport or presentation dependencies.

The accepted `AgentPBXTUI` widget, tab, binding, action, and terminal-focus key
ownership is captured through deterministic static inspection before those
owners are extracted.

At D5-R0, `Store.connect()` remains a raw-connection compatibility API while a
shared factory supplies identical PRAGMAs and managed connection ownership to
repositories. All atomic operations use one `BEGIN IMMEDIATE` primitive. A
versioned manifest ties each logical Store operation either to that direct call
or to a named repository delegation, preventing source movement from silently
weakening writer serialization.

D5-3 applies that rule to model-elevation leases and tmux runtime writer leases.
Repository extraction stops at cross-domain transaction boundaries: managed
runtime stop, migration restore, managed Agent creation, and terminal-editor
state remain facade-owned until separately reviewed. This favors explicit
transaction cohesion over reducing `store.py` by moving partially owned SQL.

D5-4 moves terminal-editor records, Joplin response/edit/conflict state, and
durable Joplin sync jobs behind three repositories. Six Joplin operations join
the reviewed atomic inventory because they combine selection with replacement,
creation, coalescing, or claim. The repositories use the shared immediate-write
primitive, while the facade retains public signatures and cross-domain Agent
cleanup. Joplin transport, encryption, profile coordination, merge policy, and
terminal process lifecycle are not persistence responsibilities and remain in
their existing services.

## Compatibility contract

The owned route matrix and transaction rules are documented in
[`docs/component-decomposition.md`](../component-decomposition.md). The
checked fixtures are:

- `agent-pbx.component-decomposition-contract/v1`, containing AST ceilings,
  review targets, package ownership rules, and the schema/transaction
  fingerprint;
- `agent-pbx.tui-compatibility-contract/v1`, containing deterministic widget,
  tab, binding, action, and terminal-focus ownership; and
- the complete canonical OpenAPI document in `openapi_compat_v1.json`.

Changing any fixture is an explicit compatibility review, never a mechanical
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
