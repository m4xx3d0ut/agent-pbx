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
| `src/agent_pbx/api.py` | 1,171 | 1,100 | application composition and router registration |
| `src/agent_pbx/store.py` | 12,961 | 12,000 | stable persistence facade over cohesive repositories |
| `src/agent_pbx/tui.py` | 33,745 | 33,000 | Textual composition root and event routing |

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
- The 32 logical atomic Store operations remain the complete explicit
  write-serialization inventory. They invoke the single reviewed
  `begin_immediate` primitive directly or through an explicit repository
  delegation recorded in the compatibility fixture. Removing, adding, or
  redirecting one requires a separate persistence review and fixture version.
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
explicit atomic-operation inventory remain unchanged. Provenance alignment and
the new ownership fixtures do not authorize a product, policy, route, schema,
migration, version, runtime, or configuration change.

### D5-R0 persistence checkpoint

The first persistence checkpoint introduces a single configured connection
factory, an explicitly managed repository connection lifecycle, and the one
reviewed immediate-transaction primitive. `Store.connect()` remains the raw
connection compatibility API. Event persistence and base Agent rows now belong
to `persistence.repositories`; `Store` continues to own Agent identity policy,
Operator/fork hydration, cross-domain projections, and cross-table
transactions.

The Store contract additionally freezes all 363 method declarations and their
signatures/decorators, connection PRAGMAs and row factory, the schema-45 object
hash, and explicit repository delegation for logical atomic operations. D5-R0
ratchets the Store ceiling to 14,488 lines and 60,556 AST nodes.

### D5-3 runtime persistence checkpoint

Model-elevation lease state, tmux runtime mapping CRUD, writer leases, and
runtime-migration batch CRUD/pruning now belong to cohesive persistence
repositories behind the unchanged `Store` signatures. Five of the 26 logical
atomic Store operations have explicit repository delegates; the other 21 still
invoke the shared primitive directly. The checker requires both sets to equal
the same reviewed manifest.

Cross-domain lifecycle work deliberately remains in `Store`. Managed runtime
stop preparation/finalization spans mappings, Agents, Operator forks, reports,
and events. Runtime-migration restore spans Agent records and mappings. Managed
Agent creation atomically creates both an Agent and its mapping. Terminal editor
state is a separate persistence domain. Those boundaries require their own
review rather than being moved because they are adjacent in the source file.

D5-3 ratchets the Store ceiling to 14,026 lines, 563,451 bytes, and 58,472 AST
nodes while preserving all 363 Store declarations, schema version 45, the
150-object schema fingerprint, and public API/TUI compatibility. Higher-risk
Joplin, resource/KB/WorkerBee, and Operator campaign persistence remains out of
scope.

### D5-4 Joplin and editor persistence checkpoint

Terminal-editor runtime identity/state, Joplin response logs, local edit and
conflict state, and durable Joplin sync scheduling now belong to three cohesive
repositories. `Store` preserves Agent-existence validation and all 363 public
declarations. Joplin HTTP, E2EE, profile ownership, merge policy, terminal
process management, authorization, and TUI behavior remain outside persistence.

The atomic inventory grows from 26 to 32 reviewed operations. Active-log
replacement, active-log stop, edit begin/reuse, conflict recording, sync
enqueue/coalescing, and FIFO sync claim use the shared immediate-transaction
primitive through 6 new repository delegates. Together with the 5 D5-3
delegates, 11 operations delegate and 21 continue invoking the primitive in the
facade. Concurrent equivalent edit starts now reuse one edit, equivalent sync
enqueues coalesce into one job, and concurrent claims select distinct queued
jobs.

Cross-domain `dismiss_agent(delete_thread=True)` cleanup remains in `Store`
because it deletes reports, commands, poll events, Joplin logs, and Joplin sync
jobs in one Agent-lifecycle transaction. Moving only its Joplin statements
would split ownership and weaken rollback behavior.

D5-4 ratchets the Store ceiling to 13,384 lines, 536,375 bytes, and 56,049 AST
nodes. Schema version 45, all 150 SQLite objects, the schema fingerprint, and
public API/MCP/TUI compatibility remain unchanged. Resource, KB, WorkerBee, and
Operator campaign persistence remain separately reviewed future boundaries.

### D5-5 bounded artifact persistence checkpoint

Recoverable file-trash entries/prune batches and durable-helper bundle/event
records now belong to two cohesive persistence repositories. `Store` retains
all public signatures as compatibility delegates. File mutation, trash-policy
decisions, helper object storage, approved-root checks, helper validation and
invocation, Operator authorization, feature gates, and KB policy remain in
their existing service layers.

The file-trash prune/apply and undo writes continue to use one managed SQLite
transaction. Durable-helper bundle/event creation and lifecycle update/event
append also remain single managed transactions. Failure-injection
characterization proves that a failed second write rolls back the first. These
operations retain their existing implicit SQLite transaction behavior and do
not alter the 32-operation explicit immediate-transaction inventory or its 11
repository delegates.

D5-5 ratchets the Store ceiling to 13,135 lines, 526,401 bytes, and 54,752 AST
nodes. All 363 Store declarations, schema version 45, the 150-object schema
fingerprint, OpenAPI inventory, and TUI compatibility remain unchanged.
Dynamic-resource leases, remote audit, WorkerBee controlled runs, Operator
campaigns/handoffs/broker workflows, and Operator KB indexing remain separate
future review boundaries.

### D5-6 dynamic-resource persistence checkpoint

Typed dynamic-resource request rows and resource-local lifecycle events now
belong to a cohesive persistence repository. The repository owns digest-bound
single-flight creation, expected-state compare-and-swap transitions,
requester/expiry/quota checks within use-count transactions, event listing,
and receipt/spec row conversion. `Store` retains all public signatures and the
post-commit projection into the broader PBX event journal.

Dynamic-resource feature gates, class/spec validation, Operator/campaign and
handoff authority, F8 approval, delegation leases, Trusted Rosie policy,
WorkerBee binding/backend generation and capability evidence, provider calls,
receipt validation, cleanup, and recovery remain in `DynamicResourceBroker`
and its injected providers. Persistence has no dependency on WorkerBee or a
provider implementation.

Failure-injection and concurrency characterization prove request/event,
transition/event, and consumption/event rollback; one-winner state
transitions; digest single-flight creation; and non-oversubscribed use quotas.
The two immediate operations now delegate through the repository, increasing
the reviewed delegate count from 11 to 13 while retaining all 32 operations.
Resource-local state/event transactions still commit before the separate PBX
audit projection; making that projection crash-atomic remains a separately
reviewed outbox/event architecture concern.

D5-6 ratchets the Store ceiling to 12,961 lines, 519,055 bytes, and 53,867 AST
nodes. All 363 Store declarations, schema version 45, the 150-object schema
fingerprint, OpenAPI inventory, TUI compatibility, provider contracts, and
runtime behavior remain unchanged. Remote audit, WorkerBee controlled runs,
Operator campaigns/handoffs/broker workflows, and Operator KB indexing remain
separate future review boundaries.
