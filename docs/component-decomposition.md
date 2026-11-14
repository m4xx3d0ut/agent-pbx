# Component decomposition contract

This contract governs the post-v2.4 extraction of large Agent PBX modules. It
describes ownership and dependency direction; it does not authorize a public
API, database, TUI, runtime, or policy change.

## Dependency direction

| Layer | May depend on | Must not own |
|---|---|---|
| `api.py` composition root | configuration, typed service container, route registrars, existing lifecycle wiring | domain mutation semantics or new authorization |
| `api_routes.*` | `api_services`, schemas, auth dependencies, HTTP error translation | `Store`, persistence repositories, TUI/UI code, executor construction, migrations |
| `api_services` | typed references to already-constructed services | route decorators, hidden service creation, feature-gate widening |
| domain services | `Store`, domain contracts, provider interfaces | FastAPI or Textual presentation |
| `store.py` | SQLite, future persistence repositories, and persistence-safe schemas/utilities | FastAPI, route modules, TUI, provider execution |
| `persistence.repositories.*` | SQLite and persistence-safe domain records behind the `Store` facade | FastAPI, HTTP clients, Textual, API/TUI composition, authorization policy |
| `tui.py` | HTTP/WebSocket clients, `ui.controllers`, `ui.screens`, panels, and presentation helpers | API construction, route registration, direct `Store` or repository access |
| `ui.controllers.*` | typed clients, view models, domain contracts, cancellation/generation helpers | FastAPI composition, route registrars, `Store`, persistence repositories |
| `ui.screens.*` | Textual widgets, typed screen inputs/results, presentation helpers | HTTP clients, subprocesses, SQLite, API composition, persistence, business authorization |

The composition root remains responsible for constructing services exactly
once. The typed container records those objects and route registrars consume
them. Existing `app.state` names remain available so extensions and tests do not
silently receive a different service instance.

## Route ownership matrix

| Family | Methods and paths | Authentication | Feature/manual boundary | Controller | Executor |
|---|---|---|---|---|---|
| Evidence and audit export | `POST /v1/evidence/preview`, `/exports`, `/verify`, `/retention/preview`, `/{export_id}/joplin-render` | global controller | evidence-export service remains default-disabled | evidence route registrar | `EvidenceExportService` |
| Operator KB bundle v2 | four `POST /v1/operator/kb/bundle-v2/*` operations | global controller and root Operator in service | `operator_kb_bundle_v2_enabled` | KB-v2 route registrar | `OperatorService` |
| Durable helpers | `GET /v1/operator/helpers`, measurements, and six helper `POST` operations | global controller and root Operator in service | `durable_helpers_enabled`; reviewed lifecycle remains service-owned | durable-helper route registrar | `OperatorService` / `DurableHelperRegistry` |
| Dynamic resources | `GET /v1/operator/resources` and request/approve/acquire/use/release/recover | global controller and root Operator in service | `dynamic_resources_enabled`; F8 approval and leases remain broker-owned | dynamic-resource route registrar | `OperatorService` / `DynamicResourceBroker` |
| Protected-provider diagnostics | `POST /v2/operator/protected-provider/diagnose` | global controller | exact manual approval and read-only envelope remain service-owned | protected-provider route registrar | `ProtectedProviderDiagnosticService` |

Unlisted API operations remain in the composition module until their reviewed
Boundary D route-family checkpoint.
Moving a route does not authorize renaming it, changing its status behavior,
changing its dependency list, or broadening its accepted model.

## Component ownership rules

The ownership rules above are executable. The decomposition contract declares
recursive import restrictions for the current `api_routes` package and the
future `ui/screens`, `ui/controllers`, and `persistence/repositories` packages.
The checker resolves both absolute and relative imports before comparing them
with the forbidden dependency prefixes.

The intended responsibilities are:

- `ui/screens` owns modal layout, validation presentation, and typed dismiss
  values. A screen does not call HTTP, start a process, query SQLite, or decide
  whether an operation is authorized.
- `ui/controllers` owns asynchronous TUI orchestration, cancellation,
  generation checks, and view-model preparation. It calls typed clients and
  services but does not reach through the server composition root or directly
  into persistence.
- `api_routes` owns authentication dependency attachment, request/response
  translation, and HTTP error translation. Mutation and authorization remain
  in the same application/domain services used before extraction.
- `persistence/repositories` owns cohesive SQLite reads and transactions. The
  public `Store` class remains the compatibility facade, and repositories do
  not acquire HTTP, Textual, provider, or policy authority.

Adding an exception requires a reviewed contract change. Creating a package
does not by itself authorize moving a responsibility into it.

## TUI compatibility ownership

`tests/fixtures/tui_compat_v1.json` is a deterministic AST-derived inventory of
the accepted TUI composition. It pins:

- all 242 statically identified widget IDs and constructors owned by
  `AgentPBXTUI.compose`;
- the ordered 11-tab ID/label surface;
- all 35 app bindings and their key, action, label, priority, visibility, and
  display-key properties;
- the 32 bound actions currently implemented by `AgentPBXTUI`; and
- the terminal-focus key ownership policy and priority-binding gate.

Extraction may move an owner only through an explicit fixture review. It must
preserve the normalized inventory and behavior unless a separately approved
product change says otherwise. Formatting and source line numbers are not part
of this fixture.

## Review targets

The no-growth ceilings are hard gates. These lower line targets are the D7
architecture review goals, not permission to expand another component:

| Composition module | Accepted ceiling | D7 line target | Intended end state |
|---|---:|---:|---|
| `src/agent_pbx/api.py` | 7,897 | 6,400 | application composition and router registration |
| `src/agent_pbx/store.py` | 14,652 | 12,000 | stable persistence facade over cohesive repositories |
| `src/agent_pbx/tui.py` | 44,244 | 34,000 | Textual composition root and event routing |

The checker requires every target to remain below its accepted ceiling. D7 may
accept a smaller reduction when cohesion and compatibility evidence justify it;
it must not meet a number by creating circular or weakly owned modules.

## Transaction and migration invariants

- `SCHEMA_VERSION` remains 45 throughout Boundary D unless a separately
  approved persistence migration changes the scope.
- No new migration branch, table, index, trigger, or durable metadata key is
  introduced by route extraction.
- A route invokes the same application-service method before and after the
  move. It does not split one service transaction across controller code.
- Existing `BEGIN IMMEDIATE` methods remain the complete explicit
  write-serialization inventory. Removing or adding one requires a separate
  persistence review and fixture version.
- Feature checks run before their service mutation, and authorization
  dependencies stay on the same operation.
- The full canonical OpenAPI document must remain byte-equivalent after sorted
  compact JSON normalization.

## Validation and rollback

Run `python scripts/check_component_boundaries.py` plus the owning component
tests after every extraction. A failed OpenAPI, dependency, ownership, TUI,
schema, transaction, or no-growth check blocks the checkpoint. Rollback reverts
only the extraction commit; because schema and migrations do not change, no
data rollback is required.

### Post-v2.4 baseline alignment

Boundary D0 is aligned to the locally accepted and qualified commit
`56f46179863fed13f339ab3e55481a895b994921`. Its exact inventory lowers the
previous `api.py` ceiling to 7,897 lines, 297,062 bytes, 36,706 AST nodes, and
290 functions. It also lowers the `tui.py` byte ceiling by 29 and AST-node
ceiling by two while retaining its 44,244-line ceiling. `store.py` remains at
its prior exact ceiling. No ceiling increased.

The OpenAPI fixture, schema-45 fingerprint, SQLite object inventory, and
explicit `BEGIN IMMEDIATE` method inventory remain unchanged. Provenance
alignment and the new ownership fixtures do not authorize a product, policy,
route, schema, migration, version, runtime, or configuration change.
