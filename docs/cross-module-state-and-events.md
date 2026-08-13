# Cross-module state, snapshot, and event architecture

Reference is the deliberate exception to the generic service-snapshot profile:
its large catalog uses the read-only SQLite contract defined in
[`reference-sqlite-read-model-design.md`](./reference-sqlite-read-model-design.md),
plus lifecycle notifications and consumer-owned bounded projections. References
below to generic mmap snapshots apply to Account, Market, Risk, and Execution,
not to the Reference catalog.

This document defines how business modules read one another's current state
and consume changes. It is an implementation contract for the current
workspace; it does not introduce a new business layer or a global state owner.

## Decision summary

Each business module has one independent contract crate for its complete
cross-process boundary. The crate defines both the read contract and the write
contract; read and write are directions of the same module boundary:

```text
crates/
  transport/                     generic KSS1/mmap and byte transport
  protocol/                      FlatBuffers schemas and generated bindings
  business/
    reference/
      contract/                  package: kairos-reference-contract
      service/                   package: kairos-reference-service
    market/
      contract/                  package: kairos-market-contract
      service/                   package: kairos-market-service
    account/
      contract/                  package: kairos-account-contract
      service/                   package: kairos-account-service
    risk/
      contract/                  package: kairos-risk-contract
      service/                   package: kairos-risk-service
    execution/
      contract/                  package: kairos-execution-contract
      service/                   package: kairos-execution-service
```

The directory is grouped by business module, while Cargo package names remain
globally explicit. The duplicated module name is intentionally avoided in the
filesystem path (`crates/business/market/contract`, not
`crates/business/market/market-contract`); the package name still carries the complete
identity required by Cargo and dependency declarations.

This extra `business/` directory is intentional. It is a repository grouping
boundary, not another runtime or business layer: all ownership still belongs to
the module below it, and the dependency graph remains `contract -> service`
within each module. It gives the workspace one obvious place for business
modules and keeps generic crates such as transport, protocol, integration, and
workspace separate from business code.

Each business module has exactly two initial public crate boundaries:

```text
business/{module}/contract     stable cross-process read/write contract
business/{module}/service      business implementation and process runtime
```

The contract crate is independently compilable and is the only cross-module
entry point. The service crate is independently compilable and owns the
implementation. A contract crate may depend on `kairos-transport` and
`kairos-protocol`, but must not depend on its service crate or another
business module's service, private files, or server implementation.

The service dependency direction is one-way:

```text
kairos-{module}-service
    -> kairos-{module}-contract
    -> kairos-transport / kairos-protocol
    -> optional kairos-integration capability
```

Another module may depend on `kairos-{module}-contract`, but never on
`kairos-{module}-service`. This rule prevents a module from bypassing another
module's public read/write boundary.

## Composition and external dependencies

Composition is primarily responsible for assembling the current module, but
it is not limited to purely internal objects. It is the only place that
selects and wires concrete implementations of external contracts.

The responsibilities are deliberately split:

```text
contract
  defines what the module exposes and accepts
  commands, queries, events, snapshots, schemas, and errors

composition
  selects which contract implementation is used and how it connects
  mmap paths, event streams, UDS addresses, providers, and runtime modes

application
  orchestrates business use cases through contract/application boundaries
```

For example, Market composition may construct a
`ReferenceSqliteReader`, a `ReferenceEventSubscriber`, and a
`MarketSnapshotPublisher`, then inject them into `MarketApplication`. It must
not open a write-capable Reference database, send raw UDS JSON, construct event
payloads by hand, or import Reference `service` internals. Those operations
must go through `kairos-reference-contract`.

The resulting construction flow is:

```text
external module service
        -> external module contract
        -> current module composition
        -> current module application
        -> current module services/domain
```

In short, contract defines the external boundary, composition chooses and
assembles its concrete implementation, and application performs business
orchestration. Composition owns wiring, not cross-module business semantics.

Python contract facades live in `kairospy` and implement the same wire
contract:

```text
kairospy/infrastructure/contracts/
  reference.py
  market.py
  account.py
  risk.py
  execution.py
```

Account and Risk facades expose their low-frequency query/command clients as
`AccountContractClient` and `RiskContractClient`; all five modules also expose
their snapshot readers. Python remains a caller-side facade and does not own
business state.

The service crate keeps the standard business-module layout required by the
workspace architecture:

```text
crates/business/market/service/src/
  bin/             server and CLI entry points
  composition/     concrete stores, publishers, clients, and mode setup
  application/     public use cases and optional process facade
  services/        private actors, persistence, adapters, and publishers
  domain/          entities, value objects, and business invariants
```

`contract` and `service` are package boundaries, not additional business
layers. The service's internal `services/` directory remains private and must
not be confused with the outer `service` crate.

The cross-module planes are deliberately separate:

```text
UDS HTTP/JSON       control, health, CLI, diagnostics, low-frequency commands
mmap snapshots      current-state reads and bootstrap
event streams       ordered changes, facts, notifications, and recovery hints
application APIs    business commands, queries, and results
composition/System  cross-module assembly and dependency checks
```

UDS JSON is not the internal high-throughput data plane and is not an event
bus. The `/v1/snapshot` endpoint is diagnostic only.

Low-frequency commands and diagnostics may use the UDS HTTP/JSON endpoint,
but the socket client and JSON wire types must live in the target module's
contract crate. Service code must not contain a second UDS client or
deserialize another module's JSON directly. Current-state reads use the
contract snapshot reader; UDS JSON is limited to control, health, and
diagnostics.

## Ownership and dependency rules

The module that owns mutable state also owns its snapshot schema, event
meaning, command/query semantics, generation, and event sequence. A contract
crate describes that module's public boundary; it does not own or mutate the
state.

Cross-module dependencies are assembled in composition or System application:

```text
Reference SQLite/events   -> Market reference projection

Account snapshot
Market snapshot
Reference SQLite query   -> Execution preflight context
Risk snapshot

Risk reservation command  -> authoritative execution admission
Account authorization     -> authoritative account-side admission
```

Snapshots are advisory for validation and audit. A snapshot does not replace a
command that owns a concurrent state transition. In particular, a sufficient
balance in an Account snapshot does not reserve that balance; Risk reservation
remains an authoritative command.

Domain code must not import contract crates, transport, FlatBuffers, or
another business module. Application code consumes another module only
through that module's contract or application boundary. Concrete readers,
writers, and event connections are selected in composition.

## Contract crate boundary

The contract crate is the only supported cross-process entry point for a
module. It defines the complete public data contract:

```text
crates/business/account/contract/
  src/
    command.rs       write commands and command results
    query.rs         read queries and query results
    event.rs         published facts and change events
    snapshot.rs      snapshot read model and watermark
    transport.rs     mmap/stream/command adapters, when kept here
    version.rs       schema and compatibility identifiers
```

Each `*-contract` crate provides public read-side and write-side types and
adapters:

```rust
SnapshotReader::open(path)
SnapshotReader::read() -> Result<Snapshot, ReadError>
EventDecoder::decode(bytes) -> Result<Event, ReadError>
CommandClient::submit(command) -> Result<Response, WriteError>
QueryClient::execute(query) -> Result<Response, QueryError>
```

The exact names may vary by module, but every reader must expose:

- a typed snapshot read model;
- snapshot metadata and watermark;
- a typed decoder for the module's declared event stream, when that stream has
  a consumer;
- typed commands, command results, and write errors;
- typed queries, query results, and read errors;
- validation of file identifier, schema version, payload completeness, and
  stable double-slot reads.

Read models and write messages are cross-process contracts, not aliases for
server-side domain entities. They must not expose SDK payloads, persistence
records, service instances, or mutable references into a server actor.

A module must not directly read or write another module's mmap files, event
sockets, UDS JSON, database, actor state, persistence records, or private
services. It must use the target module's contract. Callers must not construct
transport payloads by hand to bypass the contract.

The contract crate does not contain domain state, actors, persistence, vendor
SDK types, or business implementation. It may contain the typed adapters that
implement the contract over mmap, streams, and low-frequency command/query
transport. If those adapters later become large, they may move to a separate
internal module within the contract crate without changing the contract types
or the module boundary. They do not move back into the service crate.

Cross-process publishers belong to the same contract boundary. For example,
Reference's snapshot and event publication adapters ultimately move from the
service crate into its contract crate:

```text
crates/business/reference/contract/src/
  snapshot.rs       public snapshot types and metadata
  event.rs          public event types and envelopes
  encoding.rs       FlatBuffers contract encoding
  transport/
    mmap.rs         contract snapshot publisher/reader
    aeron.rs        contract event publisher/subscriber
```

These adapters may depend on `kairos-transport` and
`kairos-protocol`, but must not depend on `reference-service::domain`,
`reference-service::services`, or service-owned errors. They accept and return
contract-owned types and errors.

The service remains responsible for translating its private domain state into
contract data and for selecting runtime configuration:

```text
ReferenceCatalog / LifecycleEvent
        -> ReferenceSnapshot / ReferenceChanged
        -> reference-contract encoding and transport
```

The mapping, actor identity, event stream identity, file paths, provider
selection, and publisher injection remain in
`reference-service/src/composition`. This rule applies to every module:
contract owns the cross-process read/write implementation, while service
composition owns domain-to-contract mapping and runtime assembly.

The intended ownership path is:

```text
caller -> target contract -> target application -> target services/domain
target services/domain -> target contract publisher -> caller contract
```

The common transport implementation belongs in `kairos-transport`. It owns
only KSS1, mmap slot consistency, and byte-level transport behavior. Business
schemas and contract types remain owned by their business modules and
`kairos-protocol`.

Python contract facades must expose the same semantic fields, command results,
errors, and watermark behavior as the Rust contract. They may use the
generated Python FlatBuffers bindings and
must not require PyO3 merely to read a snapshot.

## Snapshot metadata and consistency

Every published snapshot must carry the common metadata already defined by the
service runtime contract:

```text
snapshot_id
view_key
producer_id
event_stream_id
generation
event_sequence
published_at_unix_nanos
```

`generation` identifies the state image. `event_sequence` is the event
watermark represented by that image. They are not interchangeable.

There is no distributed transaction across independent module snapshots. A
consumer reads a stable snapshot from each module and records the individual
watermarks. Execution must retain these dependency watermarks in its intent,
order, or audit record:

```text
account:   generation/event_sequence
market:    generation/event_sequence
reference: generation/event_sequence
risk:      generation/event_sequence
```

Consumers apply freshness policy per dependency. A stale snapshot may be
acceptable for display or simulation, but a live order must reject or enter a
degraded state when a required dependency is unavailable, too old, or has an
incompatible watermark.

## Event streams

The system uses one ordered stream per producing module rather than one global
business event enum:

```text
reference.events
market.events
account.events
risk.events
execution.events
```

An optional thin EventBus facade may route and subscribe to these streams, but
it must not own business state, coordinate use cases, or merge all events into
one stream. The underlying contract remains per-stream:

```text
stream_id
sequence
schema_version
producer_id
event_time_unix_nanos
payload
```

Event consumers must be idempotent by `(stream_id, sequence)`. A consumer
starts from a snapshot watermark, applies later events, and re-reads the
snapshot if it sees a gap, an older watermark, or an invalid event. The event
stream is therefore a change/fact plane, not durable current-state storage.

Event streams are optional for a module. A module only publishes a stream when
another component has a real consumer. Internal actor pending-event queues are
not considered a cross-process event mechanism until they have a publisher,
stable stream identity, sequence semantics, and recovery behavior.

## Module-specific integration

### Reference to Market

Reference publishes the current markets projection and `reference.events`.
Market bootstraps a local Reference projection from the Reference snapshot.
When a Reference change event arrives, Market refreshes that projection and
calls its existing `MarketApplication::reconcile_reference` boundary.

The event may contain affected identifiers and a new watermark; it need not
carry the complete catalog. If Market misses an event, it re-reads the
Reference snapshot before reconciling. Dynamic subscriptions use the local
projection and must not poll Reference JSON on every tick.

The existing `ReferenceChanged`, `AeronReferenceChangeSource`, and Market
reference reconciliation code are wired through the Market process event
source. Enabling the source performs an initial Reference projection
bootstrap, marks received changes for reconciliation, and performs a full
contract query recovery when the stream watermark has a gap. There is no
query fallback path; failure to recover remains an explicit projection error.

### Execution dependencies

Execution composition creates contract-owned readers/clients for Account,
Market, Reference, and Risk and assembles the preflight context. Market quotes
are decoded from its mmap contract; Reference queries and dependency health are
decoded by the Reference contract; Account/Risk queries and commands use their
target contract clients. The context records business
read results and dependency watermarks, not cross-module service objects.

The long-running Execution process does not perform these reads on its state
owner thread. Composition starts independent Account, Market, Reference, and
Risk projection workers, each with its own refresh loop and bounded freshness
window. The state owner reads only the local typed projection and rejects a
missing or stale dependency. These workers do not form a shared EventBus and
do not merge the four streams into a generic event enum.

Execution validates the context, then invokes authoritative commands for Risk
reservation and account-side authorization as required. It records the
dependency watermarks with the intent/order/audit result. UDS JSON is not a
fallback data path; any administrative operation must be an explicit control
command executed by a composition-owned preflight worker.

The provider's private order stream is a fifth, execution-owned input pipe. It
publishes normalized `RemoteOrderEvent` facts into Execution and is never
represented as an application command. Order entry and remote order queries
also have separate provider gateway workers; their concrete SDK connections
never enter the state owner.

Execution's high-frequency path is explicitly bounded and batched. The state
owner consumes at most 64 exchange facts before yielding one control request, then
flushes the durable outbox and publishes the newest snapshot once per batch.
The exchange, command, and query mailboxes are bounded; queue depth, batch size,
applied event count, and last state-operation latency are exposed in health.
SQLite commits a state checkpoint and its outbox record in one transaction,
while audit publication and outbox acknowledgement use batch transactions.
Provider/preflight queues have a finite enqueue deadline, and repeated
dependency transport failures open a short circuit; cleanup commands bypass the
circuit so reservations can still be released or consumed.
Account and Reference projections short-circuit full reads when their health
watermark is unchanged; Market first reads only its snapshot watermark and
decodes quotes only after a publication change.

### Account and Risk

Account and Risk publish snapshots for contract clients. Risk must add a
binary snapshot publisher using the existing shared snapshot transport.
Account and Risk event streams should be added only when a concrete consumer
requires incremental updates; an internal `drain_events()` method alone is not
a cross-process contract.

## Implementation order

1. Add the five independent `*-contract` crates and matching Python contract
   modules. Start with shared metadata validation, one snapshot view, and the
   module's existing command/query boundary per module.
2. Add the Risk binary snapshot publisher and verify all five modules publish
   stable, schema-identified snapshots.
3. Wire Reference snapshot bootstrap and `reference.events` consumption into
   Market. Add watermark tracking, gap detection, and snapshot recovery.
4. Use the four typed contract clients for Execution preflight reads and
   writes. Keep reads in independent projection workers, writes in the
   preflight command worker, and keep the UDS implementation out of the state
   owner; it is not the mmap/event data plane.
5. Record dependency watermarks in Execution intent/order/audit data and expose
   dependency freshness in health/readiness. Execution now persists the
   collected Account, Market, Reference, and Risk watermarks in `IntentState`;
   freshness policy remains a follow-up validation rule per deployment mode.
6. Add Account, Risk, and Execution event streams only when their first real
   consumer is implemented. Reuse the same per-stream envelope and recovery
   rules.

## Verification

Each contract crate must test:

- stable reads from both mmap slots;
- generation and event-sequence validation;
- schema/file-identifier rejection;
- Python/Rust decoding of the same fixture;
- event idempotency and gap recovery.

The repository checks remain:

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```
