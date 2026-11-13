# Component decomposition contract

This contract governs the post-v2.4 extraction of large Agent PBX modules. It
describes ownership and dependency direction; it does not authorize a public
API, database, TUI, runtime, or policy change.

## Dependency direction

| Layer | May depend on | Must not own |
|---|---|---|
| `api.py` composition root | configuration, typed service container, route registrars, existing lifecycle wiring | domain mutation semantics or new authorization |
| `api_routes.*` | `api_services`, schemas, auth dependencies, HTTP error translation | `Store`, TUI, executor construction, migrations |
| `api_services` | typed references to already-constructed services | route decorators, hidden service creation, feature-gate widening |
| domain services | `Store`, domain contracts, provider interfaces | FastAPI or Textual presentation |
| `store.py` | SQLite and persistence-safe schemas/utilities | FastAPI, route modules, TUI, provider execution |
| `tui.py` | HTTP/WebSocket clients and presentation helpers | API construction, route registration, direct `Store` access |

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

Unlisted API operations remain in the composition module during Checkpoint 1.
Moving a route does not authorize renaming it, changing its status behavior,
changing its dependency list, or broadening its accepted model.

## Transaction and migration invariants

- `SCHEMA_VERSION` remains 45 throughout Checkpoints 0 and 1.
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

Run `python scripts/check_component_boundaries.py` plus the owning route-family
tests after every extraction. A failed OpenAPI, dependency, schema, transaction,
or no-growth check blocks the checkpoint. Rollback reverts only the route move;
because schema and migrations do not change, no data rollback is required.

### Post-v2.4 baseline alignment

The reviewed decomposition chain is aligned on dev commit
`9d62240f7c4370763d442d65549f7e950111028b`. That base added the reviewed
WorkerBee gateway launch allowlist to `tui.py`, moving only its ceilings from
43,874 to 43,928 lines, 1,758,561 to 1,760,563 bytes, 238,230 to 238,478 AST
nodes, and 1,670 to 1,671 functions. Class and import ceilings are unchanged.
The API, OpenAPI, Store schema, transaction inventory, and migration ceilings
remain the original Checkpoint 0 contract; this alignment does not authorize
additional growth in any of them.
