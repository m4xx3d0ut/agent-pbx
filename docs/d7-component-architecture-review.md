# D7 component architecture review

- Status: Complete; accepted for integration review
- Date: 2026-11-16
- Review baseline: D5-C at
  `77377b19e925037a498bb89126044e558e2657d6`
- Runtime impact: None

## 1. Decision

The post-v2.4 decomposition has materially improved ownership and is safe to
advance to integration review. D5 is closed. The current `api.py`, `store.py`,
and `tui.py` no-growth ceilings remain hard gates, while their lower D7 values
remain architecture goals rather than release blockers.

D7 does not authorize another behavior-bearing extraction. It identifies three
cohesive future boundaries that may be proposed independently:

1. a WorkerBee application-runtime facade outside the FastAPI composition
   root;
2. a schema bootstrap/migration owner behind `Store.init()`; and
3. typed TUI command-catalog and button-routing owners.

The schema boundary is the highest-value persistence follow-up. The command
catalog/button router is the lowest-risk TUI follow-up. High-risk Operator,
WorkerBee-control, remote-audit, and destructive lifecycle persistence remains
deferred until its authority and transaction state machines are reviewed.

## 2. Review method

The review used deterministic source and compatibility evidence:

- AST inventories for the three composition roots;
- top-level and method source-span analysis;
- package-level internal-import graph analysis;
- the canonical OpenAPI and route inventory;
- the schema-45/150-object SQLite fingerprint;
- the 363-declaration Store facade contract;
- the 32-operation immediate-transaction inventory;
- the TUI widget, tab, binding, action, and terminal-focus fixture;
- the D5-6 complete-suite result of 1,968 passed and 3 skipped; and
- the D5-C component-boundary and whitespace checks.

The method-name domain groupings in this document are review aids, not new
compatibility contracts. A method may participate in more than one domain even
when its name gives it one primary classification.

## 3. Overall result

| Composition root | Post-v2.4 baseline | D7 reviewed | Reduction | D7 goal | Difference |
|---|---:|---:|---:|---:|---:|
| `api.py` | 7,897 | 1,171 | 6,726 / 85.17% | 1,100 | +71 |
| `store.py` | 14,652 | 12,961 | 1,691 / 11.54% | 12,000 | +961 |
| `tui.py` | 44,244 | 33,745 | 10,499 / 23.73% | 33,000 | +745 |
| **Total** | **66,793** | **47,877** | **18,916 / 28.32%** | **46,100** | **+1,777** |

The extracted ownership packages now contain approximately:

| Ownership package | Lines | Role |
|---|---:|---|
| API services and route registrars | 7,806 | HTTP translation over services |
| TUI controllers, screens, and panels | 11,608 | presentation and orchestration owners |
| Persistence connection, transactions, and repositories | 2,882 | SQLite mechanics behind `Store` |

The line movement is not treated as proof by itself. The internal Agent PBX
Python import graph contains no cycles, the boundary checker reports no
forbidden dependency, and no extracted persistence repository is imported
directly outside the persistence aggregate and `Store` facade.

## 4. FastAPI composition root

### 4.1 Current ownership

`api.py` is now predominantly application composition:

- `create_app()` constructs services, binds lifecycle tasks, assigns stable
  `app.state` compatibility handles, mounts MCP applications, and registers
  route families;
- token-helper application composition remains local;
- Joplin startup/background-worker ownership remains explicit; and
- debug request logging remains middleware composition.

The remaining non-composition concentration is the selected WorkerBee runtime
status and durable-binding projection path:

| Function family | Approximate span |
|---|---:|
| selected WorkerBee status | 134 lines |
| durable binding reconciliation | 75 lines |
| policy/access readback | 91 lines |
| runtime selection and profile helpers | 28 lines |

This logic is application orchestration rather than HTTP translation, but it is
also used to construct and reconcile the same services built in `create_app()`.
Moving it without an explicit service contract would only hide composition
state in another utility module.

### 4.2 D7 disposition

The current 1,171-line ceiling is accepted for integration and remains a hard
no-growth limit. The 71-line D7 difference is not a release blocker.

A future API boundary should introduce a typed WorkerBee runtime application
facade that receives the Store, runtime registry, gateway, workspace service,
and policy services explicitly. The facade may own status selection, binding
reconciliation, and redacted policy readback. Route registrars and
`create_app()` would consume that facade rather than importing persistence or
rebuilding service state.

Required gate before that move:

- characterize OSS, platform-debug, missing-binding, stale-lease, policy-drift,
  unavailable-runtime, and successful reconciliation outcomes;
- preserve every public error code and remediation string relied upon by the
  TUI;
- keep all route authorization and OpenAPI output unchanged; and
- keep `create_app()` as the sole application composition entry point.

## 5. Store facade

### 5.1 Current ownership

The Store facade retains 363 declarations. The largest remaining cohesive
mechanical owner is not an Operator domain: it is schema initialization.

| Store area | Methods | Approximate method lines |
|---|---:|---:|
| schema bootstrap, migration repair, and backfill | 8 | 1,747 |
| Operator KB | 65 | 2,115 |
| Agents, reports, commands, and pruning | 51 | 1,807 |
| Operator broker/action receipts | 15 | 847 |
| Operator fork and project-spawn state | 21 | 707 |
| WorkerBee controlled runs | 18 | 695 |
| Operator handoffs | 15 | 689 |
| Operator campaigns | 16 | 657 |
| remote token/client/audit state | 19 | 405 |

`Store.init()` alone spans 1,633 lines. It owns table/index creation, additive
column repair, legacy normalization, schema-version updates, and calls to the
existing FTS/semantic/backfill helpers. That is a coherent persistence
bootstrap responsibility and is separable without moving a business
transaction or authorization decision.

The other large areas remain intentionally coupled to cross-domain state:

- terminal report reconciliation updates reports, campaign assignments,
  campaigns, forks, alerts, and events;
- managed runtime stop spans mappings, Agent state, reports, forks, and events;
- campaign succession spans campaigns, assignments, and campaign events;
- broker projection spans requests, approvals, reports, assignments, and
  events;
- Agent pruning is destructive and preserves preview/apply/undo protection;
  and
- Operator KB combines publication state, sources, indexing, search, feedback,
  sync records, and audit events.

### 5.2 D7 disposition

The current 12,961-line ceiling is accepted for integration. The 961-line D7
difference is not a release blocker, but D7 identifies a strong future schema
boundary rather than accepting the facade as its final shape.

The proposed owner is `persistence/schema.py`, exposed through a typed
`SchemaManager` or equivalent. `Store.init()` remains the compatibility method
and delegates to that owner. `SCHEMA_VERSION` remains import-compatible from
`agent_pbx.store` even if its canonical definition moves.

Required gate before that move:

1. freeze the complete initialization SQL and schema fingerprint;
2. characterize initialization from empty databases and representative legacy
   schema versions, including versions 6, 19, 20, 28, 30, 39, 43, 44, and 45;
3. preserve interrupted Joplin-job recovery and controlled-run fingerprint
   repair;
4. preserve FTS and semantic-chunk rebuild behavior;
5. preserve connection PRAGMAs, transaction behavior, and `Store.init()`/
   `Store.schema_version()` signatures;
6. run migration backup, dry-run, apply, verification, rollback, daemon
   preflight, schema-fingerprint, Store, and full-suite tests; and
7. ratchet both the Store and new schema-owner ceilings after extraction.

This extraction should reduce `store.py` below 12,000 lines naturally. Remote
audit is not recommended as a preliminary extraction: its net reduction is
small and its security sensitivity is high.

## 6. Textual composition root

### 6.1 Current ownership

`AgentPBXTUI` contains 1,062 methods and spans 30,624 lines. A name-based review
shows the principal concentrations:

| Primary method family | Methods | Approximate method lines |
|---|---:|---:|
| tmux, runtime, terminal, and Codex | 265 | 7,652 |
| Operator, campaign, handoff, and fork | 237 | 7,086 |
| file and editor | 94 | 1,746 |
| GitHub and Git broker | 40 | 1,376 |
| alerts, reports, and events | 55 | 1,290 |
| navigation, focus, and tabs | 46 | 1,152 |
| rendering and refresh | 37 | 1,129 |
| palette, slash commands, and clipboard | 69 | 1,126 |

Two mechanically cohesive dispatch surfaces remain in the composition root:

- `get_system_commands()` spans 415 lines of command metadata and callbacks;
  and
- `on_button_pressed()` spans 393 lines of widget-ID dispatch.

They are good candidates because the TUI compatibility fixture already pins
the visible bindings/actions and extensive tests inspect the system-command
surface. They must not become untyped dictionaries that bypass action
preconditions or terminal-focus ownership.

The much larger tmux and Operator groups are not approved for wholesale
movement. Their long methods combine focus generation, asynchronous workers,
screen interaction, runtime evidence, writer leases, launch policy, and
recovery. They require callback/port boundaries and characterization one flow
at a time.

### 6.2 D7 disposition

The current 33,745-line ceiling is accepted for integration. The 745-line D7
difference is not a release blocker.

A future TUI boundary should:

1. define immutable command descriptors in `ui/command_catalog.py` and bind
   callbacks from the composition root;
2. route button IDs through typed action descriptors in
   `ui/button_router.py` or extend the existing `ActionRegistry`;
3. retain capability checks, hidden-control rules, focus-generation guards,
   terminal key ownership, destructive confirmations, and audit classes;
4. fail closed for duplicate or unknown button/action IDs; and
5. preserve the ordered command titles, aliases, widget IDs, bindings, and
   Textual behavior snapshots.

The command and button boundaries together are large enough to reach the D7
goal without moving tmux or Operator lifecycle authority prematurely.

## 7. Guardrail findings

The current boundary checker is effective for the three composition roots and
four extracted ownership packages. D7 found two worthwhile guardrail
extensions for a future checkpoint:

1. fail on internal Agent PBX import cycles, not only named forbidden edges;
2. record no-growth ceilings for large extracted route/controller files so
   decomposition cannot merely move a monolith out of a tracked root.

Current extracted files that merit explicit no-growth review include:

- `api_routes/operator.py` at 1,843 lines;
- `api_routes/workerbee.py` at 1,346 lines;
- `ui/controllers/operator_handoffs.py` at 1,731 lines;
- `ui/controllers/joplin.py` at 1,305 lines;
- `ui/controllers/workerbee.py` at 1,091 lines; and
- `ui/controllers/github.py` at 1,027 lines.

These sizes do not prove faulty ownership, but allowing unbounded growth would
undermine the purpose of the decomposition.

## 8. Ordered follow-up boundaries

The following sequence is recommended for later approval. It is not authorized
by D7 itself.

### D7-G — Extended structural guardrails — implemented

- add an internal-import cycle check;
- add reviewed per-file ceilings for extracted route/controller owners;
- retain the canonical OpenAPI, Store, schema, and TUI fixtures;
- no product behavior change.

The implemented guard covers all internal Python modules and pins the six
largest extracted route/controller owners identified above.

### D8-A — WorkerBee API application facade — implemented

- characterize selected-runtime and binding outcomes;
- extract the typed facade;
- preserve route and error contracts;
- ratchet `api.py` below its D7 goal when naturally achieved.

The WorkerBee selected-runtime, binding projection, access readback, policy
summary, and selected status orchestration now live in
`workerbee_application.py`. The FastAPI composition root constructs that typed
service and WorkerBee routes translate HTTP requests through it. Provider
lookups remain late-bound through `app.state`, preserving the existing test and
integration replacement seam. `api.py` is ratcheted from 1,171 to 792 lines,
and the new application service has its own exact no-growth ceiling.

### D8-S — Schema bootstrap and migration owner — implemented

- characterize empty and legacy initialization;
- extract schema mechanics behind `Store.init()`;
- retain all compatibility imports and migration behavior;
- ratchet `store.py` and the new schema module.

Schema DDL and upgrade orchestration now live in
`persistence/schema.py` behind the unchanged `Store.init()` compatibility
method. All 363 reviewed Store declarations remain present. Empty bootstrap,
idempotent re-initialization, and legacy markers 6, 19, 20, 28, 30, 39, 43,
44, and 45 are characterized. `store.py` is ratcheted from 12,961 to 11,331
lines, and the schema owner has an exact no-growth ceiling.

### D8-T — TUI command catalog and button router

- freeze command metadata and button dispatch;
- introduce typed descriptors and duplicate/unknown-ID checks;
- preserve focus, key ownership, confirmation, and authorization behavior;
- ratchet `tui.py` below its D7 goal when naturally achieved.

### Later domain reviews

Operator campaigns, handoffs, forks, broker actions, KB, controlled WorkerBee
runs, remote audit, and destructive prune operations require separate state,
authority, and transaction diagrams. They are not line-reduction candidates.

## 9. Integration and rollback posture

D7 changes documentation only. It introduces no schema, route, migration,
feature-gate, runtime, configuration, or durable-data change. The live daemon,
TUI, tmux servers, Agents, Operators, and WorkerBee backends do not require
activation or restart.

The D5 branch is fit for integration review after the D7 documentation commit
passes the component-boundary checker and Git whitespace validation. Rollback
is an ordinary documentation commit revert; the D5 extraction commits remain
independently revertible because they introduced no schema migration.
