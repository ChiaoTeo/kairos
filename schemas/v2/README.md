# Kairos wire contract v2 semantic charter

Status: semantic baseline with compile-validated draft schemas. Market v2
bindings are generated for contract migration, but no v2 root is considered
published until it satisfies this document, enters the root registry as active,
and has a real publisher and consumer.

This document is authoritative for v2 wire semantics. It deliberately defines
meaning before field layout. Concrete schemas continue to live under the
owning business namespace, for example `schemas/v2/execution/`; this directory
does not introduce a `kairos.v2` business namespace.

V2 is the only active Kairos wire contract. It is designed from current business
ownership and real cross-process use cases, not as a compatibility adapter.

Concrete roots are indexed in [`registry.md`](./registry.md). A root is active
only when it has an admitted owner, publisher or caller, consumer, transport
profile, and Rust/Python mapping tests.
Control surfaces are owned by each module's Rust `#[conflux_rpc]` contract
trait and exposed over workspace Unix JSON-RPC. FlatBuffers roots remain event
payload contracts and may encode one entity value in the indexed current-view
store. Command is therefore a semantic operation, not a common FlatBuffers
wire shape. The target current-view storage contract is defined in
[`current-view-storage.md`](../../docs/architecture/current-view-storage.md).
Current views use the owner-scoped LMDB indexed-view contract described in
[`docs/architecture/current-view-storage.md`](../../docs/architecture/current-view-storage.md).

## 1. Goals and non-goals

V2 exists to provide:

- one unambiguous owner for every command, fact, and current-state view;
- wire types that cannot represent common invalid discriminator/payload pairs;
- explicit identity, time, ordering, idempotency, and delivery semantics;
- lossless mapping between the owning module's public contract model and wire;
- independent evolution of commands, events, queries, and current views;
- bounded contracts justified by an active publisher and consumer;
- the same business meaning in Rust, Python, live, paper, and replay modes.

V2 does not attempt to:

- serialize each module's complete domain or Actor state;
- make FlatBuffers generated types into application models;
- create a global event union, global schema version, or global state owner;
- replace Reference point-in-time queries or historical datasets with a current view;
- provide transparent event recovery through a current-state snapshot;
- preserve unused roots merely because generated code already exists.

## 2. Contract admission rule

A v2 root may be added only when all of the following are named:

1. the business owner;
2. the publisher or command caller;
3. the consumer;
4. the transport and delivery profile;
5. the logical key and cardinality;
6. the freshness or deadline rule;
7. the failure behavior;
8. the Rust and Python mapping tests.

An anticipated dashboard, future provider, possible strategy, or desire for a
uniform directory is not a current consumer. A schema without a real consumer
is a proposal, not a published contract, and must not enter the generation
script.

## 3. Business ownership

The owner defines validation, lifecycle, generation, event sequence, command
outcomes, and compatibility for its contract.

| Owner | Owns in v2 | Explicitly does not own |
| --- | --- | --- |
| Reference | Canonical assets, instruments, listings, markets, effective dating, lifecycle revision | Runtime data-source capability, execution routes, provider sessions, market observations, orders |
| Market | Observations, synchronized order books, subscription state, source freshness | Reference identity definitions, strategy indicators |
| Account | Balances, collateral, positions, equity/PnL, account freshness, account-side observed order facts | Exchange-facing order lifecycle, risk reservations |
| Risk | Policies, circuits, authorization decisions, allocations, reservations | Balances, positions, orders, provider facts |
| Execution | Accepted execution intents, plans, legs, exchange-facing orders, fills, reconciliation state, execution audit | Strategy model state, balances, risk policy state |
| System | Workspace/process lifecycle and operational health | Business truth from the other owners |

There is no independent v2 `IntentActor` or `intent` wire owner. A strategy
submission is a command to Execution. Once accepted, its intent lifecycle is
Execution-owned. Strategy retains the private decision and model state that
caused the submission.

Account-side observed open orders and Execution orders are different facts:

- an Account observation reports what an account provider currently exposes;
- an Execution order records the lifecycle of an order submitted or adopted by
  Execution;
- correlation uses stable identifiers when known;
- neither representation is embedded into the other owner's current view.

Reference current state remains the versioned, read-only, point-in-time SQLite
contract. V2 FlatBuffers may carry bounded Reference lifecycle notifications,
but not a second authoritative catalog snapshot.

## 4. Four distinct contract shapes

Every root is exactly one of the following. A type is not reused across shapes
merely because some fields currently look similar.

### 4.1 Command

A command requests an owner to perform a state transition. Its name is
imperative, its target owner is explicit, and it has one typed result contract.
A command is not an event and is never replayed as though it were a fact.

Mutating commands carry:

- a globally unique `command_id`;
- an owner-scoped `idempotency_key`;
- caller identity and required runtime scope;
- `requested_at_unix_nanos`;
- an optional explicit deadline represented with presence, never sentinel zero;
- the complete business input required for validation.

Commands are not transparently retried after they may have been sent. A caller
may repeat a command only through its documented idempotency contract.

A transport outcome and a business outcome are separate:

- transport outcome: not sent, sent with response, or delivery unknown;
- business outcome: accepted, rejected, or already applied.

Timeout after possible send is `delivery_unknown`, not business rejection.

### 4.2 Event

An event is an immutable, past-tense business fact emitted by exactly one
owner. It contains enough owner identity and correlation to be understood
without reading mutable Actor state.

Prefer one fact per root. A union is allowed only for a genuinely atomic batch
of a closed set of variants. String `kind` plus several optional payload tables
is forbidden.

Events do not carry current aggregate collections. They may carry the changed
entity state when that state is the fact, but they do not pretend to be a
snapshot or an event backlog.

### 4.3 Current view

A current view is an immutable, one-shot image published by the state owner. It
is optimized for a named read access pattern and has a declared logical key,
cardinality, and size bound.

A current view:

- contains current state, not event history;
- has one writer and any number of readers;
- may be incomplete only when metadata explicitly says so and the consumer's
  policy permits it;
- does not contain another owner's authoritative state;
- does not define an event cursor, replay position, or stream join point;
- does not expose mutable generated FlatBuffers objects to application code.

Separate roots are justified by different owners, keys, update cadence, size,
or consumers. They are not created merely to mirror every domain collection.

### 4.4 Query result

A query result is a bounded response to an explicit request. History, fills,
audit records, and large filtered collections normally belong here or in a
dataset contract rather than in current views.

Queries may use bounded safe retries. Pagination cursors are query-owned opaque
values and are unrelated to event sequences or snapshot generations.

## 5. Common event metadata

Every event root contains a required `EventMetadata` table with these
semantics. Exact FlatBuffers spelling is fixed when the first common v2 schema
is implemented.

| Field | Requirement | Meaning |
| --- | --- | --- |
| `event_id` | required | Globally unique identity of this immutable fact |
| `stream_id` | required | Canonical logical stream whose sequence rules apply |
| `sequence` | required, greater than zero | Contiguous owner-assigned position within `stream_id` |
| `producer_id` | required | Concrete producing process/Actor identity |
| `workspace_id` | required | Workspace isolation boundary |
| `launch_id` | present when launch-scoped | Launch identity; absence means genuinely project-scoped |
| `instance_id` | present when instance-scoped | Runtime instance identity |
| `correlation_id` | required for workflow-scoped facts | End-to-end operation or intent correlation identity; absent for independent source facts such as Market observations |
| `causation_id` | present when caused by a message | Immediate command or event that caused this fact |
| `occurred_at_unix_nanos` | required, greater than zero | Time the owning business transition or observation occurred |
| `published_at_unix_nanos` | required, greater than zero | Time the producer made the event available |

The file identifier and namespaced root type identify the wire contract. A
metadata `schema_major = 2` may be included for diagnostics, but it never
overrides the root/file identifier and is not a global version shared by
unrelated contracts.

`stream_id` determines the sequence scope. Consumers deduplicate by
`(stream_id, sequence)` and must reject a conflicting event at an already seen
sequence. Sequence values do not compare across streams.

The initial logical partitioning is owner-defined:

- Market: one declared observation stream per runtime Market publisher;
- Account: one stream per account scope;
- Risk: one stream per Risk Actor;
- Execution: one stream per Execution Actor;
- Reference: one lifecycle stream per catalog writer.

Changing partition rules is a contract migration, not a transport setting.

## 6. Common current-view metadata

Every owner-scoped LMDB environment contains a reserved metadata database. Metadata is committed in
the same write transaction as the affected entity families:

| Field | Requirement | Meaning |
| --- | --- | --- |
| `identity` | required | Exact workspace, launch, instance, owner, and publisher-resource identity |
| `format_version` | required | Platform indexed-view storage format |
| `schema_set` | required | Exact named-database key/value schemas and versions |
| `resource_epoch` | required, greater than zero | Incompatible environment replacement epoch |
| `producer_incarnation` | required, greater than zero | Writer-process incarnation used for fencing and diagnosis |
| `applied_event_sequence` | required | Highest owner state sequence reflected by this transaction; never an Aeron replay cursor |
| `committed_at_unix_nanos` | required | Publication commit time |
| `rebuild_state` | required | `building`, `ready`, or bounded `failed` diagnostic state |

There is no aggregate snapshot generation or per-value transport envelope. Contract version belongs
to each FlatBuffers value root/file identifier; business ordering belongs to owner event sequences and
entity-specific fields.

## 7. Time semantics

There is no generic interchangeable `event_time` in payloads.

- `occurred_at_unix_nanos` is the owner fact time in event metadata.
- `published_at_unix_nanos` is the local publication time.
- Provider/source clocks use explicit names such as
  `source_observed_at_unix_nanos`.
- Effective-dated facts use `effective_from_unix_nanos` and an explicitly
  optional `effective_to_unix_nanos`.
- Deadlines and expiry use their business names.

The same timestamp must not be duplicated in both envelope and payload. If two
timestamps exist, their distinct meanings must be documented.

Optional scalar values use a presence-bearing table/struct shape supported by
both generated languages. Zero never means absent. Required timestamps reject
zero at the contract boundary.

## 8. Identity semantics

Wire identifiers remain canonical strings for interoperability, but readers
and writers must parse them into owner-defined identifier types at the
contract boundary.

- `instrument_id`, `market_id`, `listing_id`, and `execution_route_id` are
  never interchangeable.
- Provider symbols do not substitute for canonical Reference identities.
- `market_id` is required for a market-scoped observation or order. If the
  producer cannot resolve it, the fact is not eligible for the canonical v2
  stream and must remain in Integration diagnostics/quarantine.
- `instrument_id` is required whenever the fact concerns a financial
  instrument.
- Empty strings are invalid even when FlatBuffers cannot express that rule.

Composite IDs are never reconstructed from arbitrary payload fields by a
consumer. Canonicalization and validation belong to the owner contract.

## 9. Numeric semantics

The first v2 implementation retains a checked fixed decimal value with signed
64-bit mantissa and an explicit scale in the range `0..=18`. Its value is
`mantissa * 10^-scale`.

Rules:

- every encode and decode is checked;
- overflow, invalid scale, NaN-like strings, and silent rounding are errors;
- each business field documents whether negative and zero values are valid;
- integer counts never use decimal;
- basis points are explicitly named integer basis-point fields;
- money, price, quantity, PnL, fee, rate, and ratio are not interchangeable
  merely because they share a wire representation.

If a real caller exceeds Decimal64 range, that field receives a deliberate v2
compatible extension or a new root. Publishers must not clamp, round, or fall
back to a string dynamically.

`current_pnl` and every other monetary amount use the decimal representation;
no monetary field uses a bare integer with an implicit scale.

## 10. Closed vocabulary and invalid states

Use enums for owner-defined closed vocabularies such as:

- side and order type;
- lifecycle status;
- risk metric and reservation status;
- completion/failure policy;
- freshness state and observation kind.

Every enum reserves zero as `UNSPECIFIED`, and contract validation rejects it
where a value is required. Adding an enum value is treated as a consumer
compatibility event and requires unknown-value behavior tests.

Use a string only for genuinely open external vocabulary. Such a field is
named to expose that fact, for example `provider_status_code`, and cannot drive
business lifecycle logic without an explicit normalizer.

Do not encode the same state twice. Examples forbidden in v2:

- `status` plus `active`;
- a vector plus a redundant equal `count`;
- `kind` plus optional payload tables;
- `version` ambiguously meaning schema version or generation;
- zero meaning either absent or a real value.

JSON blobs, arbitrary metadata maps, and provider payload escape hatches are
forbidden in business roots. Diagnostics may reference a separately stored raw
record by identifier.

## 11. Delivery and recovery profiles

Runtime Aeron publication has one profile: bounded, best-effort, non-replayable notification. A subscriber
may begin at any observed sequence. Within one `(stream_id, producer, producer_incarnation)`, sequence
supports duplicate suppression and loss diagnosis; a gap is observable but does not become a snapshot
recovery protocol. Incarnation change establishes a new observation position.

Queue overflow terminates and recreates that subscription while recording notification loss. Current views
remain authoritative and may be read explicitly by business key, but they never repair or replay missing
notifications.

An owner that admits durable replay, audit, or history defines a separate query, journal, or dataset contract
with explicit retention, cursor, unavailable-range, and ordering semantics. It must not label the live Aeron
subscription as that durable source.

## 12. Initial v2 semantic surface

Names below are semantic names represented by draft FlatBuffers roots and
reserved file identifiers. Draft presence does not make a root active or add
it to generated production bindings. Only this surface is admitted before
another real caller is demonstrated.

### Reference

- Reference entity events such as `InstrumentUpserted`, `InstrumentUpdated`,
  `ListingUpserted`, `ListingUpdated`, `MarketUpserted`, and `MarketUpdated`
  carry one entity fact and catalog revision; no embedded JSON record.
- Current/reference history remains a point-in-time SQLite query contract.

### Market

- `QuoteUpdated`
- `TradeOccurred`
- `BarCompleted`
- `GreeksUpdated`
- current Quote, completed Bar, and Greeks views only where the Strategy
  bootstrap reader requires them

Order-book roots are admitted as a complete snapshot/delta/resync slice:
`OrderBookSnapshotReceived`, `OrderBookDeltaReceived`, and
`OrderBookResyncRequired`. The keyed `MarketOrderBookCurrent` value is
usable only when its `synchronized` value is true. The same entity root carries
Market-owned freshness and the other admitted latest observation families.
Subscription lifecycle is represented by the synchronous Market JSON-RPC
control response; it is not a Market data event or current view.

Market observation roots require canonical `market_id`, `instrument_id`,
`source_id`, source observation time, and the values specific to that fact.
`BarCompleted` states that the interval is closed; an updating/incomplete bar
would be a different fact.

### Account

- Account change roots: one-fact `BalanceUpserted`/`BalanceRemoved`,
  `PositionUpserted`/`PositionRemoved`, `ValuationChanged`,
  `AccountStatusChanged`, and `ObservedOrderUpserted`/`ObservedOrderRemoved`;
  optional provider provenance explains the Binance or IBKR fact being
  normalized
- `AccountCurrentView`: balances, collateral, positions, valuation and account
  status/freshness
- `ObservedOrdersCurrentView`: provider-observed current orders, separate from
  the Execution order lifecycle

Binance and IBKR provider updates are often partial. Account merges those
updates before publishing a current view; a partial provider message is not a
partial `AccountCurrentView`. Optional Decimal fields distinguish null
(unreported/not applicable) from an explicitly reported zero.

Execution orders and fills never appear in the Account view. Account events
carry settlement correlation to Execution fill/order identifiers when the
change was caused by execution.

### Risk

- `AuthorizeAndReserve`, `ConsumeReservation`, and `ReleaseReservation`
  commands in the Risk JSON-RPC control contract; authorization returns one
  atomic decision/reservation result synchronously
- `RiskDecisionMade`, explicit reservation transition facts, and explicit
  circuit transition facts
- indexed Risk current values containing policy version, structured policy
  scopes, limits, allocations, reservations, and circuits

The wire representation preserves every allocation and scope. It never derives
an `owner_id` by choosing one field, never relabels `policy_id` as `budget_id`,
and never selects the first allocation as a reservation summary.

### Execution

- `SubmitExecutionIntent`, `CancelOrder`, `ReplaceOrder`, and
  `ReconcileExecution` commands; command results describe admission only
- commands use the Execution JSON-RPC control contract over the workspace Unix
  socket; FlatBuffers is reserved for events and active views
- the accepted intent supports current single-leg and multi-leg semantics,
  including intent type, legs, completion policy, failure policy, deadline,
  execution options, and optional hedge policy
- `IntentAccepted`, `IntentRejected`, `PlanCreated`, `OrderSubmitted`,
  `OrderAccepted`, `OrderRejected`, `OrderCanceled`, `OrderExpired`,
  and `FillRecorded` facts; reconciliation is expressed by the affected
  Intent/Order lifecycle rather than a second standalone event
- indexed LMDB entity families for operational Intents, AlgorithmRuns, Orders, commitments,
  reservations, and unresolved remote facts; there is no aggregate Execution snapshot path
- bounded durable Order audit queries use the Execution JSON-RPC contract rather than current storage

Execution facts preserve `intent_id`, `plan_id`, `leg_id`, `order_id`, and
`fill_id` correlation where applicable. The full durable audit remains the
history authority; current storage never retains a recent lifecycle window as a substitute for audit.

### System

System health and alerts remain operational contracts. They may reference
business identifiers but do not re-declare business state or become the source
of truth for an intent, order, account, or risk decision.

## 13. End-to-end correlation

The minimum closed loop has one correlation chain beginning when Strategy
creates an execution intent:

```text
Market fact(s) referenced as decision evidence
  -> Strategy private decision
  -> SubmitExecutionIntent command (starts correlation_id)
  -> Risk AuthorizeAndReserve command/result
  -> Execution order lifecycle
  -> FillRecorded
  -> Account settlement change
```

Independent Market facts do not invent workflow correlation. The submission
contains typed decision evidence such as source event IDs or a source snapshot
ID. From `SubmitExecutionIntent` onward, `correlation_id` remains stable across
the workflow. Each emitted event's `causation_id` identifies the immediately
preceding command or event. Payload-specific IDs remain typed business
identities and are not replaced by generic tracing fields.

Distributed tracing identifiers may be added as observability metadata, but
trace/span IDs do not replace business correlation, causation, or
idempotency.

## 14. Validation and decoding

Generated FlatBuffers verification is necessary but insufficient. Every v2
contract adapter validates:

- file identifier and exact expected root;
- required non-empty canonical identifiers;
- enum and union consistency;
- decimal scale/range and field-specific sign rules;
- required timestamp presence and ordering where meaningful;
- stream sequence greater than zero;
- runtime isolation fields;
- vector bounds, uniqueness, and referential integrity;
- lifecycle invariants owned by the contract.

Malformed or semantically invalid messages fail explicitly. Decoders never
guess a root from transport topic, fall through to another decoder, default an
unknown event to a known kind, or expose a partially decoded application
value.

Rust and Python adapters map immediately to owned contract/application values.
Generated table lifetimes and backing buffers do not cross the adapter.

## 15. Compatibility and evolution policy

V2 is the only active Kairos wire contract. Publishers and consumers do not
translate, dual-publish, or conditionally decode retired contract shapes.

For each published root:

- the namespace is the owning kairos.<business>.v2 namespace;
- the file identifier is unique repository-wide and never reused;
- field ordinals are append-only;
- existing field meaning, requiredness, defaults, units, and identity scope do
  not change;
- a semantic breaking change creates a new root and file identifier;
- malformed, unknown, or wrong-root messages fail closed.

Every active root requires Rust encode/decode coverage, Python decoding of
Rust-produced bytes, wrong-root rejection, semantic validation failures, and
live/replay mapping parity at the public application boundary.

v2 is a wire namespace, not a migration mode. Future breaking changes create
a new explicit contract version; they do not reintroduce a retired contract.
## 17. Root registry requirements

Before a draft `.fbs` root enters the production generation script, its
registry entry must contain:

```text
semantic name:
owner:
shape: command | command_result | event | current_view | query_result
publisher/caller:
consumer:
logical key:
cardinality/vector bounds:
transport:
delivery/recovery profile:
freshness/deadline policy:
file identifier:
Rust adapter:
Python adapter:
golden fixture:
superseded contract:
retirement condition:
```

The registry records delivered reality. Planned roots stay in design notes and
do not receive identifiers or generated code.

## 18. Decisions intentionally deferred

The following are selected per admitted root from measured caller needs, not
standardized speculatively:

- exact keyed-family cardinality and retention for future admitted views;
- retention and unavailable-range policy for separately admitted durable history or replay sources;
- whether a concrete view needs partial pagination or a dataset API;
- Decimal64 replacement for a demonstrated out-of-range field;
- transport choice beyond the semantic delivery profile.

Deferring these physical choices does not defer ownership, identity, time,
ordering, command, event, or compatibility semantics defined above.
