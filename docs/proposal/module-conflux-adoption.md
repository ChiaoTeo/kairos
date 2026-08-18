# Business Module Adoption of Conflux

## 0. Status and authority

- Status: accepted migration direction; implementation proceeds in bounded
  business slices.
- Scope: Account, Market, Execution, Risk, and Reference module preparation
  and their eventual process-runtime cutover to Conflux.
- This proposal complements
  `integration-session-and-operation-design.md` and
  `conflux-actor-runtime-framework.md`. It does not redefine Integration
  connection semantics or Conflux runtime internals.
- Module preparation may proceed while Integration and Conflux are changing.
  Binding a production module to an obsolete or exploratory Conflux API is not
  permitted merely to make the migration appear started.
- Temporary workspace compilation failures caused by the intentional removal
  of old Integration APIs remain acceptable. New module work must still have
  a locally checkable boundary whenever the required lower-level API exists.

## 1. Decision

Module migration and Integration migration can run in parallel, but they have
different commit points:

```text
Integration migration ── concrete connections + capability facts ──┐
                                                                   ├─ final module cutover
Conflux C1-C5 ─────── typed routes + catalogs + managed tasks ─────┤
                                                                   │
Module M0-M2 ─────── ownership cleanup + typed adapters ───────────┘
```

The parallel module track prepares business code for Conflux without depending
on provider constructors or the current transitional `ConfluxSystem` fields.
The final cutover begins only after the relevant Conflux capability exists.

This is a directional migration, not a compatibility exercise. Each migrated
slice deletes its replaced process loop, source runner, listener plumbing, or
publication lifecycle after the Conflux path is usable. No second long-lived
state owner, dual ingestion path, or permanent legacy facade is retained.

## 2. Stable boundaries modules may target now

The following boundaries are stable enough for parallel work:

- the module's `application/` facade remains the public business boundary;
- the existing private module Actor remains the only mutable business state
  owner;
- owning and dependency Contract crates continue to own typed control, event,
  view, publisher, and client vocabulary;
- Integration owns normalized provider facts and provider-native connections;
- composition owns concrete source selection, named resource keys, revision,
  and required/optional readiness policy;
- every asynchronous input becomes an owned, bounded message before reaching
  the state owner;
- business freshness, snapshot/event barriers, deduplication, and resync stay
  in the owning module rather than moving into Conflux.

The following transitional surfaces must not be adopted by modules:

- provider facade/principal collections that hide concrete physical links;
- a second event entrance beside the global closed `ConfluxEvent` handled by
  the Actor's one `handle` method;
- direct module access to another module's `services/`;
- provider facade, principal, capability registry, or raw vendor payload types;
- a second module-local task registry, connection registry, or event bus beside
  Conflux.

## 3. Parallel migration stages

### M0: inventory and ownership freeze

For one module at a time, record:

1. every control listener and request queue;
2. every Contract client, stream, publisher, and view;
3. every Integration query, command, and stream source;
4. every timer, worker, retry loop, health contribution, and shutdown path;
5. the exact mutable state owner and every route by which it is mutated;
6. boundedness, overflow policy, readiness, ordering, recovery, and final-drop
   behavior for each source.

During M0, new runtime plumbing is not added to the binary. Missing semantics
are made explicit in application or composition types, not hidden in another
generic runtime abstraction.

### M1: isolate business dispatch from process mechanics

Move reusable business invocation behind application-owned methods and typed
module/Contract inputs. Keep the existing process operational where possible,
but reduce it to input adaptation, composition, and invocation.

M1 may include:

- replacing stringly internal messages with module- or Contract-owned types;
- moving business freshness, barriers, and resync decisions out of listener or
  source tasks and into Application/Actor;
- making publisher/view effects explicit and bounded;
- separating provider fact mapping from provider connection construction;
- consolidating all mutations through the existing Actor/Application entrance;
- adding behavior tests that do not depend on the old server loop.

M1 must not introduce an application-owned port or protocol trait merely to
stand in for Conflux, Integration, or a Contract implementation.

### M2: prepare composition recipes

Composition declares the resources the future Conflux process will create:

- stable purpose-oriented resource key;
- concrete Contract client/publisher/view or Integration connection type;
- configuration revision and replacement policy;
- required or optional readiness contribution;
- selected generated Actor route for an inbound source;
- queue capacity, overflow behavior, and recovery policy.

Until Conflux exposes the matching typed API, these recipes remain ordinary
module composition data or design fixtures. They must not be implemented by
mirroring the old `ConfluxSystem` universe inside the module.

### M3: Conflux runtime cutover

M3 starts per capability, not per whole repository, after the corresponding C5
runtime criterion is satisfied:

| Module need | Required Conflux capability |
| --- | --- |
| command/query control | generated typed HTTP/UDS routes and fixed hosts |
| dependency events | typed Contract client catalog and managed source task |
| provider events | managed concrete Integration connection/source task |
| periodic work | bounded timer route with shutdown semantics |
| event publication | typed Contract publisher catalog |
| latest views | typed Contract view catalog |

At cutover, Conflux owns scheduling and technical resource lifecycles. The
module Actor/Application still owns business state and decisions. The old loop
for the migrated slice is deleted in the same slice; temporary non-compilation
is acceptable only when a named lower-level migration dependency is missing.

### M4: delete compatibility paths and prove the boundary

For the migrated slice:

- delete the old listener/source/task registry and duplicate cancellation;
- delete duplicate ingestion and publication paths;
- prove one state owner and one bounded ingress;
- verify readiness, overload, replacement, recovery, and shutdown behavior;
- run module and Conflux focused tests, then the repository checks that are not
  blocked by an intentionally incomplete Integration slice.

## 4. Recommended module order

The order is based on architectural learning value, not current compile ease.

1. **Risk control/timer/publication slice.** It has no direct Integration
   dependency and is the smallest place to validate typed control, timer,
   publisher/view, readiness, and shutdown without conflating provider work.
2. **Reference control/refresh/publication slice.** It validates a long-running
   authoritative store, fan-in refresh, and publication while preserving
   SQLite query ownership.
3. **Market one-provider live source slice.** It is the first Integration
   proving slice for managed market stream, subscription recovery, freshness,
   and bounded event ingress. Start with one provider and one feed set.
4. **Account one-principal live snapshot/event slice.** It validates the single
   authoritative Integration ingress, snapshot/event barrier, segment
   completeness, and resync. Simulation commands remain explicitly separate.
5. **Execution one-route order command/query/event slice.** It follows only
   after delivery certainty and execution stream semantics are stable. Risk,
   Account, and Market dependencies enter through their Contract application
   APIs, never private services.

This order does not require finishing one entire module before preparing the
next. It requires finishing and deleting the old path for one business slice
before claiming that slice migrated.

## 5. Module-specific ownership constraints

### Risk

- `RiskApplication` and its private Actor remain the state owner.
- Reservation expiry is a typed timer-driven application command; Conflux owns
  timer scheduling, not expiry policy or replay-clock semantics.
- Event publishers and views use Risk Contract types directly. Transport-only
  traits currently owned by `application/process.rs` are removed when typed
  Contract resources replace them.

### Reference

- `ReferenceApplication` owns refresh/catalog behavior and SQLite remains the
  authoritative query model where intended.
- Provider catalog facts are mapped in composition before application entry.
- Fan-in completion, stale-provider policy, and last-known-good behavior remain
  module business behavior rather than managed-task policy.

### Market

- The Market Actor owns subscriptions, observations, books, and freshness.
- Integration owns socket/channel recovery and emits normalized owned facts.
- Conflux supervises the selected concrete connection and bounded source task;
  it does not choose feeds or implement REST/WS fallback.

### Account

- Live Account facts have exactly one Integration-owned authoritative ingress.
- Execution must not push duplicate live order/fill observations into Account.
- Snapshot/event barriers, idempotence, segment completeness, and business
  resync remain Account-owned.
- Simulation mutation uses explicitly named commands and is rejected by live
  processes.

### Execution

- Execution owns exchange-facing order lifecycle and audit.
- Order commands preserve Integration delivery certainty and are not retried by
  Conflux after a possibly-sent result.
- Risk, Account, Market, and Reference collaboration uses their application or
  Contract boundaries; no generic dependency gateway is added for uniformity.

## 6. Temporary non-compilation policy

Non-compilation is a migration signal, not a substitute for boundary design.
Every intentional break must identify:

- the deleted or not-yet-available lower-level symbol;
- the provider/capability/module slice that owns the repair;
- the target type or semantic boundary, if already decided;
- the focused checks that still run;
- the exit condition that removes the break.

Do not add aliases, compatibility traits, placeholder provider facades, or
`todo!()` production paths solely to restore a green workspace. Conversely, do
not break an unrelated module when an isolated preparatory refactor can remain
compilable without adopting an obsolete API.

## 7. Verification and migration ledger

Each module slice adds a short ledger entry to this document or a linked design
note containing:

| Field | Required content |
| --- | --- |
| slice | module and exact business capability |
| old owner/path | listener, loop, worker, registry, or facade being replaced |
| state owner | the single Actor/Application mutation owner |
| typed inputs | control, Contract, Integration, timer, and local inputs |
| runtime resources | clients, connections, publishers, views, and tasks |
| semantics | capacity, overflow, ordering, readiness, recovery, shutdown |
| blocker | missing Conflux/Integration capability, if any |
| deletion | obsolete concepts removed at M3/M4 |
| evidence | focused tests and static searches |

Baseline searches:

```text
rg -n "tokio::select!|mpsc::channel|axum::serve|\\.abort\\(\\)" crates/modules
rg -n "ConnectionSpec|IntegrationCapability|dyn Connection" crates/modules
rg -n "crates::modules|::services::" crates/modules -g '*.rs'
rg -n "serde_json::(to_value|from_value)" crates/modules
rg -n "trait .*Publisher|trait .*Client|trait .*Gateway|mod ports|mod capabilities" crates/modules
```

Matches are inventory inputs and must be classified; they are not all defects.
The verification gate for a cut-over slice includes focused module and Conflux
tests, formatting, `git diff --check`, crate-layout validation, and a search
showing the replaced loop or compatibility path is gone.

## 8. Migration ledger

### Risk control/timer/publication

| Field | Current state |
| --- | --- |
| slice | Risk control commands, maintenance tick, event publication, latest view, health, shutdown |
| old owner/path | removed: the former `application/process.rs` request queue, manual timer select, and publisher traits |
| state owner | `RiskApplication` wrapping the private `RiskActor` |
| typed inputs | Risk Contract control request, expiry timer, shutdown/system input |
| runtime resources | fixed UDS host, Risk Contract event publisher and view, timer task |
| semantics | bounded control admission; replay clock suppresses wall-clock expiry; publication retains pending events on failure; graceful socket cleanup |
| completed preparation | full process cutover: `RiskApplication` implements the closed Contract Actor, UDS is a thin `ConfluxHandle` host, maintenance is a Conflux timer, and typed event/view resources publish and acknowledge in Actor turns |
| blocker | `PolicyActivated` 仍需等 Risk Contract event schema 真正拥有该业务事实后再加入；Conflux absolute shutdown deadline 已实现 |
| deletion | module-owned Axum server loop, request queue, publisher traits, manual timer select, server abort, duplicate health lifecycle |
| evidence | `cargo test -p kairos-risk --lib` and `cargo test -p kairos-conflux` pass; Conflux tests cover single-handle serialization, revision/epoch replacement, and managed mmap reader/writer resources; final Risk cutover still requires host/output/shutdown tests |

### Reference refresh/publication

| Field | Current state |
| --- | --- |
| slice | provider refresh, durable outbox publication, read model, control, health, shutdown |
| old owner/path | removed: binary control queue, refresh/publication select loop, shared read-model mirror, and publication worker thread |
| state owner | `ReferenceApplication` wrapping `ReferenceActor`; SQLite remains the authoritative recovery/query store |
| typed inputs | refresh-all, refresh-source, catalog mutations, publication retry timer, control and shutdown inputs |
| runtime resources | fixed UDS host, provider resources, Reference Contract publisher, refresh/publication timers |
| semantics | partial provider scans are not failures; stale/last-known-good behavior remains Reference-owned; outbox rows are acknowledged only after successful publish |
| completed preparation | `ReferenceRestRequest`/`ReferenceRestResponse` are closed; `ReferenceApplication` implements `Contract + ConfluxActor`; UDS is a thin Contract adapter; refresh scheduling and durable outbox Aeron publication run in Actor turns; OKX and Hyperliquid use product-specific catalog queries |
| blocker | none for the current provider inventory; future provider slices must add their exact concrete collection before entering the Actor |
| deletion | binary publication runtime, manual refresh/publication `select!`, Axum listener, shared read/health mirrors, server abort, production `Box<dyn ReferenceSource>`, and direct production provider constructors |
| evidence | `cargo check -p kairos-reference --all-targets` passes; exact-collection architecture checks cover every configured provider family; final `cargo test --workspace` passes |

### Market freshness and one-provider live source

| Field | Current state |
| --- | --- |
| slice | freshness timer first, then one named provider market stream and its publication path |
| old owner/path | removed: `MarketProcess`, `MarketActorTask`, the generic control transport/parser, UDS event fanout, event publication queue, and `MarketChangePublisher` |
| state owner | `MarketApplication` and private Market Actor own subscriptions, observations, books and freshness |
| typed inputs | typed control commands, `MarketDataStream` facts, reference-universe updates, explicit freshness timer timestamp, shutdown |
| runtime resources | fixed UDS host, closed Market REST pair, named Aeron publisher, named typed Market view publishers, source receivers, Reference universe receiver, freshness timer, history queue |
| semantics | Integration owns socket epoch/reconnect/subscription ACK; Market owns feed choice, freshness, source recovery policy and business fallback |
| completed preparation | `MarketApplication` implements `Contract + ConfluxActor`; the one `handle` entrance owns typed REST, source facts, Reference universe updates, timers and system events; command idempotency moved into the Actor; mmap codecs moved under Market services while all concrete view writers and the Aeron publisher are owned by `ConfluxSystem`; the server is a thin fixed Conflux UDS host; old process/control/fanout/publication compatibility paths are deleted |
| blocker | none for the current provider inventory; new feeds still require an explicit typed query/command/stream capability and exact collection |
| deletion | process/control/publication paths, transitional provider composition facades, source activator, and duplicate provider task ownership are removed |
| evidence | `cargo check -p kairos-market --all-targets` and the focused Market architecture tests pass; final `cargo test --workspace` passes |

### Account simulation clock and live fact ingress

| Field | Current state |
| --- | --- |
| slice | application mode/time ownership now; one-principal snapshot/event ingress after Integration stabilizes |
| old owner/path | removed: `AccountProcess`, its select loop, provider consumer tasks, refresh scheduler, publisher closures, health mirror and Axum lifecycle |
| state owner | `AccountApplication` and private Account Actor own canonical balances, positions, equity, account-side order facts and business events |
| typed inputs | live `AccountQuery` baseline, `AccountStream` facts, explicit simulation settlement/mark/time commands, refresh/reconcile timer, shutdown |
| runtime resources | fixed control host, concrete private REST/stream connections, Account Contract event publisher/view, timers/tasks |
| semantics | live facts have one Account-owned Integration ingress; simulation commands are rejected in live mode; snapshot/event barriers, segment completeness, dedup and resync remain Account-owned |
| completed preparation | full server cutover: `AccountApplication` implements `Contract + ConfluxActor`; concrete Binance Spot/Funding/Margin/USD-M/COIN-M/Options, OKX private, and IBKR REST/stream connections transfer into exact named `ConfluxSystem` collections; the Actor owns snapshot/event barriers, deduplication, gap buffering, resync and freshness; the UDS host only decodes the closed REST pair; generic mmap/Aeron resources are Conflux-managed and Account-owned codecs publish/acknowledge inside Actor turns |
| blocker | remove the remaining CLI-only transitional async snapshot holder and convert its direct refresh command to the Contract client; add immediate resync scheduling after a source failure instead of waiting for the periodic refresh tick |
| deletion | `AccountProcess`, process-owned mode/time, provider consumer tasks, async refresh task, listener/publisher closures, manual timer select, duplicate health lifecycle |
| evidence | `cargo check -p kairos-conflux -p kairos-account --all-targets` passes; Account architecture tests pass against the Conflux Actor/server boundary; final `cargo test --workspace` passes |

### Execution replay clock and one-route lifecycle

| Field | Current state |
| --- | --- |
| slice | replay clock ownership now; one named order command/query/execution stream route after Integration stabilizes |
| old owner/path | removed: `ExecutionProcess`, its polling/select loop, Integration stream tasks, control queues, publisher facades, readiness mirror and shutdown ownership |
| state owner | `ExecutionApplication` and private Execution Actor own exchange order lifecycle, intents, fills and audit facts |
| typed inputs | typed Contract control, `OrderCommand`, `OrderQuery`, `ExecutionStream` facts, business-time/reconciliation timers, dependency Contract facts, shutdown |
| runtime resources | fixed control host, dependency Contract clients, concrete order command/query/stream connections, publishers/views, timers/tasks |
| semantics | possibly-sent commands are never transparently retried; reconciliation/recovery barriers remain Execution-owned; dependencies enter through Contract APIs |
| completed preparation | `ExecutionApplication` implements `Contract + ConfluxActor`; exact Binance Spot/Margin/USD-M/COIN-M/Options/Stocks, OKX private and IBKR connections enter `ConfluxSystem`; concrete execution streams are supervised into the one global `handle`; reconciliation is timer-driven; durable audit, views, Aeron events and simulation settlement publish in Actor turns; `ExecutionHost` is lifecycle-only |
| blocker | command/query calls still bridge the synchronous application core to async concrete connections through bounded queued gateways; removing that bridge requires making the corresponding Execution use cases async, not another compatibility facade |
| deletion | generic old async connection wrappers, `ExecutionProcess`, blocking execution-stream pull path, process stream/reconciliation loops, simulator-owned authoritative clock check, control queues and publication lifecycle |
| evidence | `cargo check -p kairos-execution --all-targets` passes, obsolete stream/process names are absent, and final `cargo test --workspace` passes |
