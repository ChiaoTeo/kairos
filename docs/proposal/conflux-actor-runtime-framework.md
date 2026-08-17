# Conflux Actor Runtime Framework

## 0. Status

- Status: accepted direction, revised to a Contract-first, macro-generated
  Actor dispatch model.
- Current implementation scope: `crates/platform/conflux`, its standalone
  example, tests, benchmarks, and this proposal.
- C1 dispatch kernel implemented: the impl macro, inferred Actor error,
  generated typed routes, heterogeneous bounded Handle, serialized Actor loop,
  minimal exclusive Context, and lifecycle/shutdown dispatch are executable.
- The automatic Contract client catalog, its first private typed source
  runner, concrete Integration connection ownership, and the first generic
  managed-task slice are implemented. Fixed control hosting, outward Contract
  resources, task failure/readiness reporting, and asynchronous readiness
  remain subsequent framework stages.
- Business-module adoption is deliberately deferred until the framework
  package satisfies the readiness criteria in section 22.
- The current exploratory API is not the target API. In particular,
  `ConfluxActor::Input`, `ConfluxActor::Response`, `context.http()`,
  `context.aeron()`, `context.views()`, and transport-specific resource
  registration must be replaced rather than preserved as compatibility APIs.
  Manual Actor route registration is also not a target public API.

## 1. Summary

Conflux is the process runtime around one serialized module Actor. It provides
one external Handle, heterogeneous typed messages, exclusive mutable Context
access, framework-owned resource lifecycles, bounded scheduling, readiness,
and graceful shutdown.

Conflux is Contract-first rather than transport-first:

```text
fixed HTTP-over-TCP / HTTP-over-UDS
        -> generated extractor produces Json<T> / Query<T> / Path<T>
        -> ConfluxHandle<Actor>::request(typed route input)
        -> annotated Actor method

dynamic Module Contract clients / business-owned Integration work
        -> bounded owned Contract/provider frame
        -> ConfluxHandle<Actor>::notify(frame message)
        -> annotated Actor method performs decoding/mapping

Conflux scheduler
        -> macro-generated typed route handler
        -> &mut Context<Actor>
             ├─ contracts().clients::<C>()
             ├─ contracts().publishers::<P>()
             ├─ contracts().views::<V>()
             ├─ integrations()
             ├─ timers()
             └─ shutdown()
```

The module Contract owns request/response types, event frames, codecs, views,
clients, and its current transport implementation. Aeron and mmap may remain
the transport used by a particular Contract, but they are not Conflux core
capabilities. A Contract may later replace Aeron with WebSocket or mmap with a
different snapshot implementation without changing the Actor runtime model.

The four required runtime capability groups are:

| Direction | Capability | Lifecycle |
|---|---|---|
| outward service | fixed HTTP-over-TCP control | process lifetime |
| outward service | fixed HTTP-over-UDS control | process lifetime |
| outward service | Contract event publishers, currently commonly Aeron | dynamic, multiple |
| outward service | Contract views, currently commonly mmap | dynamic, multiple |
| dependency | Module Contract clients | dynamic, multiple |
| dependency | provider-native Integration connections | dynamic, multiple |

## 2. Why this framework exists

Long-running modules repeatedly need the same process behavior:

- fixed command/query admission over HTTP and UDS;
- dynamic connections to Account, Market, Execution, Risk, and Reference
  Contract endpoints;
- dynamic provider Integration connections;
- multiple event streams entering one mutable state owner;
- multiple event publications and materialized views;
- bounded queues, overload policy, readiness, resource feedback, and shutdown.

Without one runtime boundary, each module grows its own listeners, loops,
registries, cancellation paths, and shutdown ordering. The resulting code
duplicates process mechanics and makes it difficult to prove that mutable
business state still has one owner.

Conflux solves that process problem. It does not become a second application
facade and does not define module business vocabulary.

### 2.1 Abstraction justification

1. **Concrete problem now:** modules already duplicate multi-ingress dispatch,
   resource ownership, bounded delivery, and graceful shutdown.
2. **Current callers:** long-running module processes and standalone process
   examples.
3. **Insufficient existing boundary:** module Contract crates own their public
   process contracts, `kairos-integration` owns provider behavior,
   `kairos-transport` owns transport mechanics, and `kairos-workspace` owns
   paths and launch resources. None owns the serialized process lifecycle.
4. **Smallest boundary:** heterogeneous messages, one Handle, one Actor,
   exclusive Context, a type-indexed Contract catalog, managed streams and
   Integration connections, and one shutdown protocol.
5. **Concepts removed after adoption:** per-module process loops, listener/task
   registries, transport-specific Conflux APIs, duplicated cancellation code,
   single `Input`/`Response` enums, and manual handler registries created only
   for runtime plumbing.
6. **Evidence:** behavior, lifecycle, fault, stress, fixed-control, dynamic
   Contract, Integration, and benchmark tests described below.

## 3. Goals and non-goals

### 3.1 Goals

- Give every external producer the same bounded Handle entrance.
- Allow each annotated route to have its own input and response types.
- Generate uniform dispatch glue from Actor methods with a procedural macro.
- Serialize Actor mutation while providing `&mut Context<Actor>` to every
  lifecycle hook and annotated route method.
- Own the Actor's type-indexed Contract catalog outside the Actor; its set of
  live resources may be empty and requires no Actor-level catalog declaration.
- Host fixed HTTP and UDS control services using module-owned Contract routes.
- Support multiple dynamic outward Contract publishers and views.
- Support multiple dynamic Module Contract clients.
- Own provider-native Integration connection lifecycles without owning their
  command/query/stream semantics.
- Keep queues, collections, effect batches, and background work bounded.
- Make revision, epoch, readiness, failure, and shutdown behavior observable.
- Prove the framework independently before migrating business modules.

### 3.2 Non-goals

- Conflux does not define Account, Market, Execution, Risk, or Reference
  request/event/view models.
- Conflux does not directly depend on those business Contract crates.
- Conflux does not expose a universal provider adapter, session API, or
  `execute(operation, payload)` facade.
- Conflux does not decode vendor payloads or module wire formats.
- Conflux does not place raw HTTP request objects, borrowed transport buffers,
  socket objects, or vendor SDK callbacks in the Actor queue.
- Conflux is not a business event bus, cache, persistence layer, workflow
  engine, or replacement for Axum, Tokio, Contract crates, or Integration.
- Conflux does not require every module to publish events, expose views, or
  connect to every other module.
- This proposal does not authorize a bulk migration of business modules.

## 4. Ownership and dependency direction

```text
module main crate / composition
        |
        ├─ depends on kairos-conflux
        ├─ depends on its own and required module Contract crates
        └─ depends on provider Integration capabilities when required

kairos-conflux
        ├─ owns runtime scheduling and lifecycle
        ├─ owns the concrete Actor instance
        ├─ owns the Actor's type-indexed Contract catalog
        ├─ owns managed stream/task lifecycles
        └─ owns provider-native Integration connection values

module Contract crate
        ├─ owns control request/response types
        ├─ owns event frames and encode/decode
        ├─ owns view keys, frames, readers, and publishers
        ├─ owns its unified Client/Endpoint facade
        └─ may use UDS, HTTP, Aeron, mmap, WebSocket, or another transport
```

`kairos-conflux` must not depend on `kairos-account-contract`,
`kairos-market-contract`, or another business Contract. Its catalog stores
framework-managed resources behind private type erasure while its public API
remains typed. Concrete Contract types enter only when Actor code creates or
uses a resource. An Actor with no Contract resources therefore has an empty
catalog without declaring a special empty type.

Ownership map:

| Concern | Owner |
|---|---|
| mutable business state and invariants | module Actor/Application |
| process scheduling and lifecycle | Conflux |
| concrete Contract catalog values | Conflux, selected by typed Context calls |
| Contract request/event/view vocabulary | owning module Contract crate |
| fixed control framing and response transport mapping | module Contract host/composition |
| Contract payload decoding and business mapping | annotated Actor method using the Contract crate |
| Contract client protocol and transport | module Contract crate |
| provider authentication and normalized external capabilities | Integration |
| provider-native connection object lifetime | Conflux |
| source/route selection | module composition and Actor |
| low-level byte transport | Contract implementation / `kairos-transport` |

## 5. Macro-driven ConfluxActor

The Actor declares only the HTTP commands/queries, Module Contract events, and
Integration events it actually consumes. It does not define one universal
input/response pair and does not manually implement a generic dispatch trait.

Conflux follows the useful part of Rocket's design: a procedural macro turns
ergonomic, strongly typed user functions into uniform runtime handlers and
static route metadata. Conflux differs from Rocket because generated handlers
must enqueue through one Handle and execute serially with `&mut Actor` and
`&mut Context`.

Target user surface:

```rust,ignore
#[conflux::actor]
impl StrategyActor {
    #[started]
    async fn started(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ActorError> {
        Ok(())
    }

    #[handle(post = "/v1/subscribe")]
    async fn subscribe(
        &mut self,
        Json(request): Json<SubscribeRequest>,
        context: &mut Context<'_, Self>,
    ) -> Result<Json<SubscribeResponse>, ActorError> {
        // business use case and desired resource changes
    }

    #[handle]
    async fn account_event(
        &mut self,
        event: Event<AccountEventFrame>,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ActorError> {
        // decode the Account Contract frame and apply the needed fact
    }

    #[handle]
    async fn market_event(
        &mut self,
        event: Event<MarketEventFrame>,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ActorError> {
        // this Actor uses more than one Contract type
    }

    #[handle]
    async fn binance_order_event(
        &mut self,
        event: Event<BinanceOrderEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ActorError> {
        // provider-native event mapping
    }

    #[stopping]
    async fn stopping(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<(), ActorError> {
        Ok(())
    }
}
```

The macro generates the `ConfluxActor` implementation, lifecycle glue, typed
route tokens, fixed HTTP route metadata, type-erased dispatch functions, and
compile-time bounds. The conceptual runtime trait remains small:

```rust,ignore
pub trait ConfluxActor: Send + Sized + 'static {
    type Error: Error + Send + Sync + 'static;

    fn routes() -> &'static ActorRouteTable<Self>;
}
```

The macro infers `Actor::Error` from the annotated methods and requires every
handler and lifecycle hook to use the same error type. An annotated `impl`
must contain at least one handler or lifecycle hook from which the error type
can be determined.

There is deliberately no `contracts = StrategyContracts` argument and no
`Actor::Contracts` associated type. The catalog is the full framework catalog:
it can hold every concrete Contract resource type requested by this Actor at
runtime, and contains zero entries when none are requested. Handler signatures
generate routes; dynamic resource creation supplies concrete client,
publisher, and view types. Maintaining a second handwritten catalog struct
would duplicate those facts and allow configuration to drift from use.

Lifecycle invocation is part of the generated route/lifecycle descriptor; it
does not require users to write a second parallel trait implementation.

### 5.1 Why this is an `impl` attribute, not a derive

`#[derive(ConfluxActor)]` on `StrategyActor` can inspect only the struct
declaration. It cannot inspect a separate inherent `impl`, so it cannot
discover or consume `#[started]`, `#[stopping]`, and `#[handle]` methods.

`#[conflux::actor]` is therefore an attribute macro on the complete inherent
`impl`. It receives all methods and their helper attributes in one token tree,
then emits:

- the cleaned inherent `impl` with framework helper attributes removed;
- the generated `ConfluxActor` implementation;
- lifecycle descriptors, route tokens, and private dispatch thunks.

This makes `#[started]` meaningful without requiring a handwritten trait
implementation or a second derive declaration. A derive should be introduced
only if Conflux later needs metadata that belongs exclusively to Actor struct
fields; it would not replace the `impl` macro that builds method dispatch.

### 5.2 One handler attribute

Every ordinary Actor entry method uses `#[handle]`. Infrastructure categories
do not appear in the method attribute:

```rust,ignore
#[handle(post = "/v1/orders")]
async fn submit_order(
    &mut self,
    Json(request): Json<SubmitOrderRequest>,
    context: &mut Context<'_, Self>,
) -> Result<Json<SubmitOrderResponse>, ActorError>;

#[handle]
async fn account_event(
    &mut self,
    event: Event<AccountEventFrame>,
    context: &mut Context<'_, Self>,
) -> Result<(), ActorError>;

#[handle]
async fn provider_event(
    &mut self,
    event: Event<BinanceOrderEvent>,
    context: &mut Context<'_, Self>,
) -> Result<(), ActorError>;
```

HTTP handlers carry method/path metadata because fixed listeners need route
matching and extraction. Event handlers are selected by a generated typed
RouteToken when a dynamic source is subscribed. The Actor method does not need
to know whether its event came from a Contract client, an Integration
connection, a timer, or another framework-owned producer unless its logic uses
the supplied source metadata.

Framework system inputs such as resource status, timer firing, and owned task
completion may also target `#[handle]` methods. They are not another dispatch
mechanism.

### 5.3 Static handlers versus dynamic instances

The design deliberately separates compile-time handler topology from runtime
resource instances:

| Static and macro-generated | Dynamic and framework-owned |
|---|---|
| HTTP method/path/body/response handler | individual HTTP requests |
| event payload type and generated RouteToken | clients, connections, subscriptions, reconnect epochs |
| handler names and input/output type checks | publisher/view types, instances, and revisions requested through the catalog |

A dynamic Account subscription does not register a new handler. At creation it
selects an already-generated `Event<AccountEventFrame>` RouteToken. Likewise,
a business-owned Binance event task selects an existing
`Event<BinanceOrderEvent>` token. Neither operation modifies the route table.

The handler set is enumerable for one Actor even though the number and timing
of resource instances are dynamic.

The macro scans the entire annotated `impl`; the user never repeats that set
in `contracts = ...`, `events = ...`, or another allowlist. All declared
handlers participate. If the Actor declares no Contract-backed handler and
creates no Contract resource, both its Contract event-route count and its live
catalog count are zero.

### 5.4 Macro responsibilities and limits

The macro must generate:

- one immutable Actor route table;
- inference and consistency validation of the Actor error type;
- typed HTTP extractor/responder glue;
- one dispatch thunk per annotated method;
- compile-time validation of handler signatures and `Send + 'static` payloads;
- a typed RouteToken accessor for every annotated method;
- compile-time input/output matching between RouteToken and Handle calls;
- typed request reply channels;
- private type erasure needed by the heterogeneous queue;
- startup validation for duplicate HTTP routes and invalid route descriptors.

The macro does not decide which client or connection uses an event route.
Dynamic subscription code selects that token explicitly. The macro must not
generate or statically register client instances, subscriptions, Integration
connections, publishers, or views. It must not generate a universal provider
operation enum or expose its private dispatch enum/trait as module API.

Implementation requires a separate proc-macro package,
`crates/platform/conflux-macros`, re-exported by `kairos-conflux`. Compile-pass
and compile-fail macro behavior is verified with UI tests.

### 5.5 Rocket precedent and Conflux difference

Rocket route attributes generate a uniform `Handler`, static route metadata,
typed guard/data extraction, and responder conversion around an ergonomic user
function. See the official
[route attribute semantics](https://api.rocket.rs/v0.5/rocket/attr.put) and
[Handler API](https://api.rocket.rs/v0.5/rocket/route/trait.Handler).

Conflux adopts that compile-time glue pattern, not Rocket's concurrent request
execution or ranked route forwarding. Once a generated Conflux proxy has
selected and extracted an HTTP route—or a Contract runner/business-owned task
has selected its exact typed event route—it submits to the single Actor Handle.
There is no attempt to fall through to another Actor handler after admission.

## 6. The single external Handle

`ConfluxHandle<A>` is Clone, Send, and Sync. Generated HTTP proxies, private
Contract source runners, generic managed tasks, timers, system composition,
and tests all submit through it.

```rust,ignore
let route = StrategyActor::routes().subscribe();
let response = handle
    .request(route, Json(SubscribeRequest { symbol }))
    .await?;

let route = StrategyActor::routes().account_event();
handle.notify(route, contract_event).await?;
```

Target surface:

```rust,ignore
impl<A: ConfluxActor> ConfluxHandle<A> {
    async fn notify<I>(
        &self,
        route: RouteToken<A, I, ()>,
        input: I,
    ) -> Result<SubmitOutcome, NotifyError<I>>;

    async fn request<I, O>(
        &self,
        route: RouteToken<A, I, O>,
        input: I,
    ) -> Result<O, RequestError<I>>;

    fn shutdown(&self, mode: ShutdownMode);
    fn status(&self) -> ProcessStatus;
}
```

Route tokens are generated by the actor macro and cannot target a handler with
the wrong input or response type. Generated HTTP proxies and private source
runners normally hold these tokens; application code rarely calls them
directly.

Both methods accept ingress metadata/options for source identity, class,
sequence, correlation, and deadline. `notify` may backpressure, reject, or use
an explicitly configured drop policy. `request` never silently drops.

The framework must not expose a public second path that invokes the Actor or
mutates Context directly.

## 7. The exclusive mutable Context

Only Conflux constructs Context. It is valid for one lifecycle hook or one
message dispatch and cannot be cloned or retained.

```rust,ignore
pub struct Context<'runtime, A: ConfluxActor> {
    contracts: &'runtime mut ContractCatalog<A>,
    effects: EffectBatch<A>,
    runtime: &'runtime mut RuntimeState<A>,
}
```

The target public surface is:

```text
context.handle()
context.routes()
context.contracts()
context.integrations()
context.tasks()
context.timers()
context.resources()
context.readiness()
context.shutdown()
```

`context.routes()` only returns immutable macro-generated tokens for binding a
dynamic source. It is not an Actor invocation path; delivery still occurs
exclusively through the one Handle owned by the source runner.

There is intentionally no `context.aeron()`, `context.views()`, or
`context.http()`. Those names expose current transport choices instead of the
Contract boundary.

### 7.1 Full, type-indexed Contract catalog

Conflux constructs one initially empty `ContractCatalog<Actor>` automatically:

```rust,ignore
let process = Conflux::new(actor);
```

The catalog does not have a handwritten field for every possible dependency.
It exposes typed projections over privately erased storage:

```rust,ignore
let accounts = context.contracts().clients::<AccountClient>();
let account = accounts.get_mut(account_id)?;
let health = account.control().health().await?;

let events = context.contracts().publishers::<StrategyEventPublisher>();
let views = context.contracts().views::<StrategyView>();
```

The type parameter and a stable typed resource ID recover the concrete value;
`TypeId` may validate private downcasts but is never the resource identity.
Collections are created lazily and contain zero members until requested. This
is what “full catalog” means: every legal Contract type is available without
pre-registration, not that Conflux eagerly creates every business dependency.
Both the total number of live resource families and each family's member count
remain bounded by runtime configuration.

The catalog belongs to Conflux, not to the Actor. The Actor may retain stable
IDs and desired business configuration, but must not become a second owner of
live clients. Modules may add extension methods such as `accounts()` for local
ergonomics, but those are typed projections over the same catalog rather than
a second catalog definition required by the framework.

The collection stores the concrete Contract client directly:

```rust,ignore
ClientEntry<AccountContractClient> {
    value: AccountContractClient,
    revision,
    epoch,
    subscriptions,
}
```

Conflux does not require a universal `Client` trait and does not proxy control,
query, or view operations. `get` and `get_mut` return the concrete type, so
business code calls the Contract client's own API. Type erasure exists only
inside catalog storage to support an initially empty, open set of concrete
types; `TypeId` selects a private collection and never identifies an instance.

### 7.2 Staged runtime effects

Operations that create tasks, streams, publishers, views, listeners, timers,
or Integration connections are staged for the current dispatch. Before
commit, Conflux validates keys, revisions, capacity, dependencies, and
conflicts. A failed batch must not partially create runtime resources.

Direct calls on an already-established Contract client are ordinary Contract
operations and retain that Contract's command/query/delivery semantics. They
are not rewritten into a universal Conflux operation API.

Commit order is:

1. validate the whole batch;
2. create or replace dependencies required by later effects;
3. start Contract sources and generic managed tasks;
4. admit Contract event/view publications;
5. apply retirements and epoch fences;
6. apply readiness changes;
7. apply shutdown requests;
8. complete a pending request response.

## 8. Contract-first transport boundary

Conflux controls the runtime envelope and generated extraction/dispatch glue;
modules control payload meaning and business mapping.

Framework-owned metadata includes:

- source identity and ingress class;
- receive time and optional deadline;
- correlation and causation identity;
- source sequence when available;
- resource key, revision, and epoch;
- typed request reply continuation.

Module/Contract-owned behavior includes:

- declaring HTTP request/response DTOs and their serialization rules;
- FlatBuffers or other event frame decoding;
- mapping a decoded Contract event into a consuming module message;
- encoding outbound Contract events and views;
- mapping Actor errors/responses into Contract responses.

Transport framing, body limits, authentication, and ownership conversion run
before the Actor. For `#[handle(post = "...")]`, the macro-generated proxy
applies extractors such as `Json<T>`, `Query<T>`, and `Path<T>` and only
enqueues their owned typed values. For an event `#[handle]`, an owned Contract
frame or provider event enters through the Handle; Contract-specific frame
decoding occurs inside the Actor method. Raw `axum::Request`, socket objects,
borrowed Aeron buffers, and vendor SDK callbacks never enter the Actor.

One framework event envelope retains source identity without forcing all
payloads into one public enum:

```rust,ignore
pub struct Event<E> {
    pub source: EventSource,
    pub subscription: SubscriptionId,
    pub revision: Revision,
    pub epoch: Epoch,
    pub sequence: Option<u64>,
    pub payload: E,
}

pub enum EventSource {
    Contract { client: ResourceId },
    Integration { connection: ResourceId },
    Timer { timer: ResourceId },
    Resource { resource: ResourceId },
}
```

`EventSource` describes the concrete origin instance; it does not select the
handler. The generated typed route token and private dispatch thunk connect an
`Event<E>` to the same Handle dispatch path. A source runner receives that
token when its subscription is created and attaches the current source ID,
revision, epoch, and sequence to every event. The Actor never writes an
infrastructure wrapper enum match or implements erased dispatch manually.

HTTP extractor failures are answered before Actor dispatch. Contract frame
decode failures identify their client/resource inside the annotated event
method and do not become business state changes.

## 9. Outward service: fixed HTTP and UDS

Every process may expose one fixed control Contract over HTTP-over-TCP,
HTTP-over-UDS, or both. These listeners are process-lifetime infrastructure,
not dynamic Actor resources.

Composition supplies:

- fixed TCP address and/or UDS path;
- admission, body, concurrency, and request timeout limits.

The Actor macro supplies the fixed HTTP route table and generated extractor /
Handle proxy for every `#[handle(post = "...")]` method (and the equivalent
HTTP verb forms). Request/response DTOs still belong to the module Contract.

Illustrative construction:

```rust,ignore
let process = Conflux::new(actor)
    .http(http_address)
    .uds(control_socket);
```

The final builder spelling may differ, but the invariants are fixed:

- request/response DTOs belong to the module Contract and route declarations
  belong to the Actor methods that need them;
- the macro generates both listener proxies from one immutable route table;
- Conflux owns listener tasks, admission, cancellation, and UDS cleanup;
- HTTP and UDS routes submit typed messages through the same Handle;
- listeners become externally accepting only after required startup readiness;
- routes cannot borrow the Actor or Context directly;
- shutdown rejects new requests before draining accepted messages.

This fixed control configuration is not the rejected static registration of
dynamic event publications, views, clients, or Integration connections.

## 10. Outward service: dynamic Contract publishers and views

The full Contract catalog exposes typed dynamic collections such as:

```rust,ignore
context.contracts().publishers::<StrategyEventPublisher>()
context.contracts().views::<StrategyView>()
```

The concrete publisher or view type belongs to the module Contract. For
example, a current implementation may contain an Aeron publisher or mmap
replacement snapshot, but Conflux does not name those transports in its core
API.

Illustrative use:

```rust,ignore
let events = context
    .contracts()
    .publishers::<StrategyEventPublisher>()
    .ensure(event_key, revision, event_endpoint)?;

context
    .contracts()
    .publishers::<StrategyEventPublisher>()
    .publish(events, ContractEvent::from(domain_event))?;

let view = context
    .contracts()
    .views::<StrategyView>()
    .ensure(view_key, revision, view_endpoint)?;

context
    .contracts()
    .views::<StrategyView>()
    .replace(view, ContractView::from(snapshot))?;
```

Required publisher semantics:

- multiple dynamic publishers;
- bounded pending work and explicit overload outcomes;
- Contract-owned encoding before transport publication;
- revisioned replacement and per-incarnation epoch;
- no-subscriber or unavailable-peer outcome according to the Contract;
- shutdown flush bounded by the process deadline;
- old-epoch completion fencing.

Required view semantics:

- multiple dynamic views;
- stable logical key and independently changing revision/epoch;
- Contract-owned view type and encoder;
- replacement publication only after a complete payload is available;
- size validation before replacing a readable snapshot;
- final accepted view remains readable after retirement when the Contract
  declares persistent snapshot semantics;
- restart rebuilds views from authoritative Actor/Application state.

Conflux may provide reusable bounded collection and lifecycle primitives to
Contract hosts. It must not make every Contract implement a universal
transport adapter.

## 11. Dependency service: dynamic Module Contract clients

Dependencies on other Kairos modules are represented by independently
dependable Contract clients in the same type-indexed catalog:

```rust,ignore
context.contracts().clients::<AccountClient>()
context.contracts().clients::<MarketClient>()
context.contracts().clients::<ExecutionClient>()
context.contracts().clients::<ReferenceClient>()
```

Collections are typed and bounded. Each member has a stable key, desired
revision, concrete incarnation epoch, and typed ID. Replacement fences the old
client and all streams derived from it.

For one Contract client type, the event frame type is fixed by that Contract
binding—for example, `AccountClient -> AccountEventFrame`. Client instances,
subscription instances, endpoints, revisions, and epochs remain dynamic. An
Actor that only uses a client's control/view capability may explicitly omit
its event binding; the macro must not require handlers the Actor does not need.

Illustrative dynamic creation:

```rust,ignore
let account_id = context.contracts().clients::<AccountClient>()?.ensure_with(
    key,
    revision,
    || AccountClient::from_endpoint(endpoint),
)?.id;

let target = context.routes().account_event();
let mut clients = context.contracts().clients::<AccountClient>()?;
clients.subscribe(
    account_id,
    event_stream_key,
    revision,
    IngressClass::CriticalEvent,
    target,
    |account| account.events(event_request),
)?;
```

`subscribe` does not create a second Actor entrance. It invokes the concrete
client only to obtain its owned stream; its private source runner owns a clone
of the one `ConfluxHandle<Actor>`. Every received frame is wrapped with the
client ID, revision, epoch, subscription identity, and source metadata and
submitted through that Handle. Replacement or retirement cancels the old
runner, while queued old-epoch frames are fenced immediately before dispatch.

The Actor performs Contract deserialization at its sole macro-generated
handler boundary:

```rust,ignore
#[handle]
async fn account_event(
    &mut self,
    input: Event<AccountEventFrame>,
    context: &mut Context<'_, Self>,
) -> Result<(), ActorError> {
    let event = input.payload.decode()?;
    self.apply_account_event(input.source, event, context).await
}
```

The client type is fixed at each typed catalog call, while instances are not.
Multiple `AccountClient` values can coexist under different resource IDs,
revisions, and epochs. Their subscriptions may select the same
`account_event()` token, or select different `Event<AccountEventFrame>` tokens
when the Actor intentionally needs different business handling. The payload
type alone never chooses between those handlers.

Conflux owns:

- concrete client values and their removal/replacement;
- collection capacity and stable identity;
- streams/tasks registered from a client;
- cancellation and epoch fencing;
- technical readiness/failure feedback;
- shutdown ordering and final drop/close.

The Contract client owns:

- control command/query APIs;
- event and view capabilities;
- wire encoding/decoding;
- its concrete transport and delivery semantics.

The framework must not mirror `AccountClient`, `MarketClient`, or another
Contract API behind a second generic command/query facade.

## 12. Dependency service: dynamic Integration connections

Integration connections are different from Module Contract clients.

- A Module Contract client communicates with another Kairos business module
  through that module's stable public contract.
- An Integration connection is provider-native and exposes provider-specific
  command, query, stream, authentication, and recovery semantics.

Conflux owns concrete Integration connection storage, identity, replacement,
retirement, and final drop. Business/Integration code owns connection
construction inputs, authentication, subscribe/unsubscribe, commands, queries,
streams, reconnect policy, and every other provider interaction.

For one Actor, accepted event handlers and their typed route tokens are
statically enumerable from `#[handle]` methods. Concrete connections,
principals and connection epochs are dynamic. A connection type is not baked
into the handler attribute. Enumerating event dispatch does not authorize
Conflux to know or unify provider command/query/stream semantics.

```rust,ignore
let connected = BinanceConnection::connect(config).await?;
let mut integrations = context.integrations();
let mut connections = integrations.connections::<BinanceConnection>()?;
let connection = connections
    .ensure_with(connection_key, revision, || connected)?
    .id;

// Direct concrete provider interaction; Conflux has no subscribe method.
let stream = connections
    .get(connection)?
    .order_events(subscription)
    .await?;
```

If the stream must outlive the current handler, business code registers its
already-created future/stream as generic Conflux-owned background work and
captures the one Handle plus its chosen RouteToken. Generic task ownership may
provide cancellation, bounds, completion, and shutdown; it must not expose an
Integration-specific `subscribe`, invent provider subscription identity, or
retry provider operations.

```rust,ignore
#[handle]
async fn binance_order_event(
    &mut self,
    input: Event<BinanceOrderEvent>,
    context: &mut Context<'_, Self>,
) -> Result<(), ActorError> {
    self.apply_provider_fact(input.source, input.payload, context)
        .await
}
```

### 12.1 Multiple connections of the same concrete type

Multiple instances of one concrete connection type are first-class. For
example, an Actor may hold two Binance connections for different principals:

```rust,ignore
let mut integrations = context.integrations();
let mut connections = integrations.connections::<BinanceConnection>()?;
let primary = connections
    .ensure_with(connection_key("binance-primary"), revision, || primary)?
    .id;
let hedge = connections
    .ensure_with(connection_key("binance-hedge"), revision, || hedge)?
    .id;

let primary_stream = connections.get(primary)?.order_events(primary_request)?;
let hedge_stream = connections.get(hedge)?.order_events(hedge_request)?;
```

Business code may send both streams to one handler or choose two handlers with
the same `Event<BinanceOrderEvent>` input. The chosen RouteToken is part of
business task setup, not connection management. This is intentional and is
not an ambiguous type-based dispatch.

A managed Integration connection is identified by:

```text
(ConnectionKey, ConnectionRevision, ConnectionEpoch)
```

`TypeId<BinanceConnection>` is only a private storage/downcast check. It must
never be used as the connection identity or as an automatic routing rule.

The concrete connection type remains visible at the Context method boundary.
Private storage may erase it and must recover the exact type before returning
`&C` or `&mut C`. Conflux must not define a universal connection trait. Drop is
the framework's universal ownership terminal; provider-specific asynchronous
close/logout remains an explicit business/Integration operation, normally
performed from stopping logic before the value is dropped.

Required semantics:

- provider command/query/stream semantics remain explicit;
- commands are not transparently retried after they may have been sent;
- queries may use bounded safe retries owned by Integration;
- business-owned streams define ordering, reconnect, backpressure, resync,
  and how their facts are wrapped and routed;
- generic managed tasks provide bounded ownership and shutdown without
  interpreting provider subscription semantics;
- uncertain completion remains visible as a typed outcome.

## 13. One Handle ingress and private source runners

There is exactly one dispatch entrance: `ConfluxHandle<Actor>`. Contract
collections, fixed control listeners, timers, and generic managed tasks may
create private producers, but no public Integration subscription manager or
second callback dispatcher exists.

The remaining public lifecycle primitives are:

```text
context.tasks()     cancellable owned async work and typed fact production
context.timers()    one-shot and interval message production
```

Contract subscriptions are created through the Contract catalog's typed
source facility. Integration subscribe/unsubscribe remains a direct operation
on the concrete provider connection; any resulting long-lived work may be
registered through generic task lifecycle machinery. Both forms eventually
submit through the same bounded Handle, but Conflux has no
Integration-specific subscription API.

All private source runners submit either an owned HTTP handler input or an
`Event<E>` through the same `ConfluxHandle<A>`. Revision/epoch metadata is
attached and fenced by the runner immediately before Handle submission. No
independent queue, callback dispatcher, or direct Actor executor is exposed.
The task-scoped producer is a restricted facade over the same Handle queues;
background producers never borrow Actor or Context and never invoke business
callbacks concurrently.

### 13.1 First managed-task slice

The current caller is the standalone `market_runtime` example. A concrete
provider connection returns its provider-native receiver after business code
calls `subscribe_order_book`; neither the Integration catalog nor the task
facility knows that this operation is a subscription.

`context.tasks().spawn(key, revision, run)` solves the lifecycle gap that the
Actor and connection catalog cannot solve alone: bounded task identity,
same-revision idempotency, revision replacement, cancellation, shutdown join,
and fencing of already queued inputs from an obsolete task epoch. The task
body receives a cancellation signal and a task-scoped typed producer backed
by the same Handle queues. It chooses an Actor-generated `RouteToken` itself.

This is intentionally the smallest current abstraction:

- no provider or Integration trait;
- no subscribe/unsubscribe command in Conflux;
- no stream item type imposed by Conflux;
- no retry, reconnect, resync, or normalization policy in the task registry;
- no second Actor invocation path.

Task failure reporting, readiness contribution, shutdown deadlines, and
staged task mutations remain later work. They must be driven by concrete
failure and control examples before expanding this surface.

## 14. Resource identity and feedback

Every dynamic technical resource has:

```text
ResourceKey       stable logical identity
Revision          desired specification revision
Epoch             concrete runtime incarnation
OperationId       unique ensure/replace/retire/status operation
```

Idempotency rules:

- same key, same revision, equivalent visible specification: idempotent;
- same key, same revision, different visible specification: conflict;
- lower revision: stale revision;
- higher revision: replacement and a new epoch;
- late input/completion from an old epoch: reject and measure.

Opaque executable values such as route handlers, factories, and decoder
closures cannot be compared. Reusing a revision declares those opaque values
equivalent; changing their behavior requires advancing the revision.

Technical resource changes are framework-owned events and may target an
ordinary generated handler:

```rust,ignore
let target = context.routes().resource_changed();
context
    .resources()
    .subscribe(target, IngressClass::Completion)?;

#[handle]
async fn resource_changed(
    &mut self,
    event: Event<ResourceEvent>,
    context: &mut Context<'_, Self>,
) -> Result<(), ActorError>;
```

`ResourceEvent` includes family, key, revision, epoch, operation ID, state, and
error. The Actor decides whether a technical state affects business admission,
source freshness, or only diagnostics.

Resource feedback uses the same bounded Handle path. It does not mutate the
Application directly.

## 15. Bounded scheduling

The internal queue stores heterogeneous erased dispatch envelopes, divided
into bounded lanes:

```text
Lifecycle
Command
Query
CriticalEvent
Observation
Completion
```

Default overload intent:

- lifecycle: reserved bounded capacity;
- command/query: bounded wait or explicit rejection;
- critical event/completion: bounded backpressure, never silent drop;
- observation: explicit drop/coalesce/reject policy selected by the caller.

Dispatch is weighted and fair among non-empty lanes. FIFO is guaranteed within
one lane. There is no invented global order among independent sources.

The queue and metrics retain source identity, receive time, sequence,
correlation, deadline, resource revision, and epoch independently from the
concrete message type.

## 16. Readiness

Process health, technical resource readiness, Contract readiness, and business
readiness are distinct.

During startup the Actor may require tickets for dynamic initial resources.
Unmarked tickets are optional. Conflux begins external control admission only
after:

- fixed HTTP/UDS listeners are bound but still gated;
- `actor.started()` succeeds;
- required Contract clients, streams, publishers, views, and Integration
  connections reach their declared technical ready state;
- the heterogeneous scheduler and Actor loop are live.

Optional dependency failure produces resource feedback and may make process
health Degraded without automatically blocking all business commands.

## 17. Graceful shutdown

Conflux owns one absolute shutdown deadline and observable phases:

```text
Running
  -> Quiescing
  -> StoppingSources
  -> DrainingMessages
  -> FlushingContracts
  -> ClosingDependencies
  -> Stopped
```

### 17.1 Quiescing

1. close Handle admission for new external requests;
2. stop accepting new HTTP/UDS requests;
3. preserve bounded delivery of already-accepted resource completions;
4. invoke `actor.stopping(&mut Context)` exactly once.

### 17.2 Stopping sources

1. cancel timers and generic managed streams;
2. stop private Module Contract client source runners;
3. cancel generic tasks that business code uses for provider streams;
4. allow already-framed critical events and completions into the drain queue;
5. fence every input produced after its resource terminal epoch.

### 17.3 Draining messages

The Actor handles messages accepted before admission closed and allowed
terminal resource messages. Weighted fairness remains active. No new external
business request is admitted.

### 17.4 Flushing and closing

1. flush accepted outward Contract publication work;
2. publish the last accepted replacement for dirty Contract views;
3. finish explicit persistence/audit completion tasks;
4. complete or fail every pending typed request;
5. close Integration connections;
6. close/drop Module Contract clients;
7. remove framework-owned UDS socket files.

### 17.5 Deadline and immediate shutdown

Every phase consumes the same deadline. On expiry, or for Immediate shutdown,
Conflux aborts remaining owned tasks, fails pending requests, discards queued
messages with metrics, closes resources best-effort, and reports `Forced`.
It must never report a clean Drain after silently discarding accepted work.

### 17.6 Restart invariants

- the Actor/Application remains the authoritative business state owner;
- Contract views rebuild from authoritative state;
- dynamic desired resources are recreated from startup/business state;
- resource epochs change across incarnations;
- stale Contract/Integration observations remain distinguishable and fenced;
- fixed UDS ownership is reacquired without deleting unrelated files.

## 18. Failure semantics

Required failure categories include:

```text
MessageRejected / MessageDropped
RequestTimeout / DeadlineExceeded
HandlerFailed / HandlerDidNotReply
DecodeFailed / EncodeFailed
ContractUnavailable / ContractUncertain
IntegrationUnavailable / IntegrationUncertain
ResourceConflict / StaleRevision / CapacityExceeded
Backpressure / NoSubscriber / ResyncRequired
Shutdown / ShutdownTimeout / Forced
```

Rules:

- HTTP extraction failures are answered before Actor dispatch; Contract frame
  decode failures arise in the annotated event method, identify their source,
  and do not become business state changes;
- handler or effect-commit failure fails the request and forces cleanup when
  process consistency is uncertain;
- a disconnected HTTP client does not undo an already-committed mutation;
- Integration commands preserve delivery certainty and are not blindly
  retried;
- publisher/view encode failure preserves the last valid visible state;
- resource task panics become failed resource events and cannot masquerade as
  clean retirement;
- every pending request finishes with a typed response or explicit error.

## 19. Observability

Minimum process metrics:

- queue depth, capacity, admitted, rejected, dropped, and dispatched by lane;
- handler latency and failure count by generated route;
- fixed HTTP/UDS active requests and admission state;
- Contract collection count, revision, epoch, and state by family;
- Contract source reconnect, gap, resync, and backpressure counts;
- Integration connection lifecycle plus business-reported provider stream and
  uncertain-operation outcomes;
- publisher outcomes and view publication generations;
- current shutdown phase, elapsed duration, forced aborts, and discarded work.

Logs include process identity, source identity, Contract/resource key, revision,
epoch, operation ID, correlation, and error class where applicable.

## 20. Tests and performance evidence

### 20.1 Actor macro and heterogeneous route tests

- one Actor declares HTTP and multiple `Event<E>` payload types in one
  annotated impl using only `#[handle]`;
- an Actor with no Contract event handler compiles with zero Contract routes
  and starts with an empty catalog;
- two handlers may accept the same `Event<E>` type and remain unambiguous
  because subscriptions select exact route tokens;
- each HTTP route may have a different input and response type;
- notify and request share one Handle and one serialized mutation boundary;
- concurrent producers never execute two generated handlers concurrently;
- request completion occurs after effect admission;
- missing reply, handler failure, deadline, queue-full, and caller-drop behavior
  is explicit;
- compile-fail tests reject invalid signatures, duplicate HTTP routes,
  inconsistent handler error types, non-Send payloads, and route-token
  input/output mismatches;
- macro expansion never registers concrete clients, subscriptions, or
  Integration connections statically.

### 20.2 Fixed control tests

- the same module Contract router works over HTTP/TCP and HTTP/UDS;
- transport framing, guards, and HTTP `Json<T>` extraction occur before
  dispatch, while Contract event frame decoding occurs in the annotated Actor
  method;
- both endpoints submit typed messages through the same Handle;
- admission waits for startup readiness;
- shutdown rejects new requests and removes only owned UDS paths;
- listener error/panic becomes resource/process failure.

### 20.3 Contract catalog tests

- one initially empty catalog lazily holds multiple concrete client,
  publisher, and view collections without Actor-level registration;
- configured global family and per-family member bounds are enforced;
- direct typed access such as
  `contracts().clients::<AccountClient>().get_mut(id)` works;
- multiple instances of the same client type may share one event route or
  intentionally bind to different compatible routes;
- create, replace, retire, capacity, stale revision, and conflict behavior is
  covered;
- replacement fences streams derived from the old client epoch;
- clients remain owned by Conflux and close/drop during shutdown.

### 20.4 Dynamic outward Contract tests

- create, publish/replace, and retire multiple publishers and views;
- same-revision idempotency and specification conflicts;
- bounded publication overload outcomes;
- encoder panic/error becomes resource failure;
- final accepted view remains readable where declared by the Contract;
- restart rebuilds dynamic views from Actor state;
- a test Contract can replace one transport implementation with another
  without changing annotated Actor methods.

Transport-specific Aeron and mmap correctness belongs primarily in the owning
Contract/transport tests. A Conflux example may use them as one concrete proof,
but Conflux core tests must not encode them as the framework abstraction.

### 20.5 Integration tests

- heterogeneous provider-native connection types;
- multiple connections of the same concrete type have distinct logical IDs,
  revisions, and epochs;
- business code receives `&C`/`&mut C` and calls concrete provider operations
  directly;
- create, ready, replace, retire, and stale revision;
- concrete connection values drop on replacement, retirement, and shutdown;
- no Integration-specific subscribe/unsubscribe API or universal connection
  trait exists in Conflux;
- uncertain command is not transparently retried;
- generic managed tasks are cancelled and awaited during graceful shutdown;
- a shared shutdown deadline and ignored-cancellation policy remain pending.

### 20.6 Graceful shutdown and fault tests

- clean idle Drain;
- Drain under message and publication backlog;
- failed fixed listener;
- failed Contract client/stream;
- failed Integration connection ownership operation or business-managed task;
- task panic;
- ignored cancellation and shutdown timeout;
- Immediate shutdown reports discarded messages and `Forced`.

### 20.7 Demo

The current `examples/market_runtime.rs` slice is executable in two modes. Its
deterministic mode launches a local Binance USD-M protocol fixture; its live
mode uses `KAIROS_CONFLUX_BINANCE_WS=wss://fstream.binance.com/ws`. Both use
the real Integration connection and direct business-owned
`connect_channel`/`subscribe` calls, then route normalized facts through a
managed polling task and one Actor Handle.

The example also runs fixed HTTP/TCP and HTTP-over-UDS listeners against the
same request route, an embedded Aeron Media Driver with a real subscriber, a
concrete output Contract pinned to a publisher thread, a real double-slot mmap
writer/reader, and clean Drain. The Aeron thread boundary is required because
the current rusteron C resource is not `Send`; Conflux does not use an unsafe
marker to move it into the Actor. The same encoded example frame is published
to Aeron and mmap and decoded by both readers; in live mode the mmap reader may
naturally observe a newer sequence than the first queued Aeron frame. Likewise,
sequential TCP and UDS control reads compare identity and monotonic sequence,
not whole-snapshot equality, because the Actor continues consuming live facts
between the two requests.

This proves the end-to-end composition but does not yet make Axum, Aeron, or
mmap a Conflux core API, and it does not import or migrate the real Market
module. Generated HTTP extraction and first-class outward Contract
collections remain framework work.

The later full-system standalone example must additionally demonstrate:

Without importing a business module:

- a test Contract catalog owned by Conflux;
- one fixed HTTP/TCP and one fixed HTTP/UDS control service;
- multiple typed request/response and notification messages;
- dynamic creation of at least two outward publishers and two views;
- dynamic creation of at least two Module Contract clients;
- a provider-native Integration connection and managed stream;
- resource feedback through the Handle;
- clean Drain and forced Immediate scenarios.

### 20.8 Stress and benchmark evidence

- many concurrent typed request/notify producers;
- observation flood does not starve commands, lifecycle, or shutdown;
- bounded overload behavior for streams and publishers;
- large dynamic Contract collections within configured capacity;
- handler dispatch throughput and latency;
- Contract stream-to-Actor throughput;
- publisher/view effect cost;
- graceful drain latency under backlog.

Benchmarks establish a reproducible baseline, not an unsupported release
threshold. Performance abstractions require profiling evidence.

## 21. Target package structure

```text
crates/platform/conflux/
  src/
    lib.rs
    actor.rs           generated Actor trait and lifecycle descriptors
    route.rs           typed route tokens and immutable route table
    dispatch.rs        private erased dispatch envelope
    handle.rs          route-token notify/request entrance
    context.rs         exclusive per-dispatch Context
    contracts.rs       catalog/collection lifecycle primitives
    control.rs         fixed HTTP/TCP and HTTP/UDS hosting
    sources.rs         private Contract source runners
    integrations.rs    concrete provider connection ownership
    tasks.rs           cancellable work and typed completion
    timer.rs           managed timers
    effects.rs         bounded staged runtime changes
    lifecycle.rs       readiness and shutdown phases
    metrics.rs
  examples/
    multi_contract.rs
  tests/
    actor_routes.rs
    fixed_control.rs
    contract_catalog.rs
    dynamic_contract_outputs.rs
    integration_connections.rs
    graceful_shutdown.rs
    stress.rs
  benches/
    throughput.rs

crates/platform/conflux-macros/
  src/
    lib.rs             `#[conflux::actor]` implementation
    parse.rs           actor and handler attribute parsing
    expand.rs          route/lifecycle/dispatch code generation
  tests/
    ui.rs
    ui/
      pass/
      fail/
```

There is no core `aeron.rs`, `views.rs`, or dynamic `http.rs` capability. Any
concrete transport support used by the example is a Contract implementation,
not an Actor runtime primitive.

The macro package contains no runtime state. `kairos-conflux` re-exports its
attributes so users need one dependency. The public API must not expose an
`adapters` catch-all, macro-internal erased dispatch types, a universal
provider operation facade, raw transport objects, or a second application
facade.

## 22. Migration plan and readiness

### C0: revise and clean the exploratory design

- accept this Contract-first proposal;
- remove the single Actor `Input`/`Response` model;
- remove manual route/handler registration from the user-facing model;
- remove transport-specific Context capabilities;
- retain only low-level bounded scheduling/lifecycle pieces that fit the new
  ownership model.

Exit: no exploratory transport-first API is documented as final.

### C1: Actor macro, typed routes, and Context

- implement `conflux-macros` and re-export `#[conflux::actor]`;
- implement the single `#[handle]` method annotation plus `#[started]` and
  `#[stopping]` lifecycle annotations;
- infer one Actor error type from annotated methods and reject inconsistent or
  non-inferable declarations;
- generate typed route tokens, an immutable route table, and private erased
  dispatch;
- implement route-token `notify/request` on one Handle;
- implement the minimal `Context<Actor>` authority for Handle access, route
  selection, ingress metadata, and shutdown;
- preserve bounded fair scheduling and staged effects;
- add heterogeneous response, serialization, and macro UI tests.

Exit: a transport-free test proves HTTP and event `#[handle]` methods execute
serially with exclusive Context access, dynamically selected routes deliver
to the intended handler, and invalid signatures fail at compile time.

### C2: fixed Contract control service

- host one Contract router over fixed HTTP/TCP and HTTP/UDS;
- gate admission on readiness;
- map listener failures and own UDS cleanup;
- add request/decode/shutdown tests.

Exit: both endpoints invoke the same macro-generated typed route through the
same Handle.

### C3: Contract catalog and dynamic outward service

- automatically construct one initially empty, type-indexed
  `ContractCatalog<Actor>` and expose it through Context;
- implement typed bounded Contract collections;
- implement stable IDs, revision, epoch, replacement, and retirement;
- support module service Contract publishers and views without naming their
  transports in Conflux core;
- implement private transport-neutral source runners that submit only through
  the Handle;
- add replacement, restart, overload, and transport-substitution tests.

Exit: the standalone demo dynamically manages multiple clients, publishers,
views, and streams through its Contract catalog.

### C4: Integration connections

- preserve provider-native concrete connection types;
- own concrete value identity, replacement, retirement, and shutdown drop;
- leave construction details and every provider interaction, including
  subscribe/unsubscribe, in business/Integration code;
- preserve command/query/stream semantics and delivery certainty;
- add failure, panic, uncertainty, epoch, and backpressure tests.

Exit: multiple concrete provider connections are dynamically owned and used
directly without a universal trait or Integration-specific subscription API;
business-owned background work can later route typed events through the
generic Handle/task facilities.

### C5: framework package readiness

The Conflux package is ready for business adoption only when evidence proves:

1. there is no Actor-wide `Input` or `Response` associated type;
2. the Actor declares only needed HTTP and event methods with one `#[handle]`
   vocabulary in one `#[conflux::actor]` impl;
3. macro-generated typed route tokens feed one Handle with per-route response
   types;
4. every generated handler receives exclusive mutable Context access;
5. Conflux automatically owns a full type-indexed Contract catalog, with no
   handwritten Actor catalog declaration and zero entries when unused;
6. fixed HTTP/TCP and HTTP/UDS control endpoints use module Contract routes;
7. dynamic outward publishers and views are Contract capabilities, not core
   Aeron/mmap APIs;
8. dynamic typed Module Contract clients work with stable identity and epoch;
9. every framework-managed Contract subscription and business-managed event
   task explicitly selects a compatible static generated route token and
   never mutates the Actor route table;
10. provider-native Integration connections work without a universal provider
   facade;
11. all background streams/tasks re-enter through the Handle;
12. all queues, collections, effects, and resource counts are bounded;
13. graceful shutdown passes clean, overloaded, failed-resource, timeout, and
    Immediate scenarios;
14. the standalone demo proves the complete lifecycle;
15. macro UI, stress, and benchmark tests cover the claimed type and
    concurrency model;
16. focused verification succeeds and unrelated workspace failures are
    precisely documented;
17. searches find no documented compatibility API preserving the rejected
    transport-first, single-message, or manual-dispatch model.

### C6: deferred business adoption

After C5, select one business slice that currently duplicates control,
Contract-client streams, outward publications/views, readiness, and shutdown.
Migrate only that slice, delete the replaced process-loop concepts, and retain
one mutable business state owner. Market is a possible slice, not part of the
current framework completion scope.

Further module adoption occurs only where Conflux removes real duplication.
Legitimate differences, such as Reference's authoritative SQLite query model
or a module without outward views, remain intact.
