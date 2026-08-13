# Kairos wire contract v2 semantic charter

Status: semantic baseline with compile-validated draft schemas. No v2
FlatBuffers root is considered published until it satisfies this document,
enters the root registry as active, and has a real publisher and consumer.

This document is authoritative for v2 wire semantics. It deliberately defines
meaning before field layout. Concrete schemas continue to live under the
owning business namespace, for example `schemas/execution/v2/`; this directory
does not introduce a `kairos.v2` business namespace.

V2 is not a field-for-field cleanup of v1. It is a new set of contracts derived
from current business ownership and real cross-process use cases. V1 remains a
migration boundary and must not be silently reinterpreted to have v2 meaning.

Concrete draft roots are indexed in
[`registry.md`](./registry.md). Every v1 root has an explicit replace, split,
query/dataset, defer, or retire decision in
[`v1-disposition.md`](./v1-disposition.md).
The per-owner mmap files, discovery manifest, sharding, capacity, publication,
and file-epoch rules are defined in
[`mmap-contract.md`](./mmap-contract.md), with a machine-readable discovery
contract in [`mmap-manifest.schema.json`](./mmap-manifest.schema.json).

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
- replace Reference point-in-time queries or historical datasets with mmap;
- provide transparent event recovery through a current-state snapshot;
- preserve unused v1 roots merely because generated code already exists.

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
| Reference | Canonical assets, instruments, listings, markets, execution-access definitions, effective dating, lifecycle revision | Provider sessions, market observations, orders |
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
dataset contract rather than in mmap current views.

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

Every current-view root contains required `ViewMetadata`:

| Field | Requirement | Meaning |
| --- | --- | --- |
| `snapshot_id` | required | Unique identity for this publication |
| `resource_id` | required | Stable logical mmap resource identity from the resource manifest |
| `resource_epoch` | required, greater than zero | Immutable file/capacity/schema epoch for this resource |
| `view_key` | required | Canonical key naming the access pattern and scope |
| `owner_id` | required | Business Actor that owns the state |
| `workspace_id` | required | Workspace isolation boundary |
| `launch_id` | present when launch-scoped | Launch isolation |
| `instance_id` | present when instance-scoped | Instance isolation |
| `generation` | required, greater than zero | Monotonic publication generation for this `view_key` |
| `as_of_unix_nanos` | required, greater than zero | Business cut represented by the image |
| `published_at_unix_nanos` | required, greater than zero | Publication time |
| `completeness` | required enum, not `UNSPECIFIED` | Whether the declared view population is complete or intentionally partial |

There is no generic `version` field. Contract version belongs to the root/file
identifier; publication ordering belongs to `generation`.

An optional owner-specific `applied_revision` may record audit evidence such as
a catalog revision or journal checkpoint. It does not become an event cursor
unless a separate retained event contract explicitly defines that mapping.

Counts are omitted when they merely repeat a vector length. A total count is
allowed only when the payload is intentionally partial or paginated and the
difference has business meaning.

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

- `instrument_id`, `market_id`, `listing_id`, and `execution_access_id` are
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

Every event stream declares exactly one profile in its contract registry:

### Retained

The owner provides bounded retention and replay by `(stream_id, sequence)`.
The contract defines retention limits, unavailable-cursor behavior, ordering,
and resync. Account, Risk, Execution, and Reference lifecycle streams should
converge on this profile because they represent durable business transitions.

### Ephemeral fail-closed

The owner provides ordered live delivery but no replay promise. A gap is a
terminal continuity error for that subscription; the caller restarts through a
documented application workflow. Initial high-rate Market streams may use this
profile.

Snapshots do not repair either profile. A dedicated owner-defined resync
operation may return a new current view and a separately defined retained
cursor, but the relationship must be part of that operation's contract.

Backpressure behavior is part of the stream contract. Silent drop followed by
continued delivery is forbidden; a dropped sequence must become an observable
gap or explicit subscriber termination.

## 12. Initial v2 semantic surface

Names below are semantic names represented by draft FlatBuffers roots and
reserved file identifiers. Draft presence does not make a root active or add
it to generated production bindings. Only this surface is admitted before
another real caller is demonstrated.

### Reference

- `ReferenceChanged`: structured lifecycle notification carrying catalog
  revision and affected canonical identities; no embedded JSON record.
- Current/reference history remains a point-in-time SQLite query contract.

### Market

- `QuoteObserved`
- `TradeObserved`
- `BarCompleted`
- `GreeksObserved`
- current Quote, completed Bar, and Greeks views only where the Strategy
  bootstrap reader requires them

Order-book roots migrate when their existing transport consumer is promoted to
a public application caller and included in v2 parity tests. Rate, ticker,
mark/index price, funding, open interest, instrument status, warm-up history,
and subscription views are not automatically admitted by their v1 existence.

Market observation roots require canonical `market_id`, `instrument_id`,
`source_id`, source observation time, and the values specific to that fact.
`BarCompleted` states that the interval is closed; an updating/incomplete bar
would be a different fact.

### Account

- `AccountChanged`: an atomic union batch of typed balance, position,
  valuation, freshness, status, and account-observed-order variants
- `AccountCurrentView`: balances, collateral, positions, valuation and account
  status/freshness
- account-observed orders use a separately named view or typed section only
  when a current consumer requires them

Execution orders and fills never appear in the Account view. Account events
carry settlement correlation to Execution fill/order identifiers when the
change was caused by execution.

### Risk

- `AuthorizeAndReserve` command with one atomic decision/reservation result
- idempotent `ConsumeReservation` and `ReleaseReservation` cleanup commands
- `RiskDecisionMade`, `ReservationChanged`, and `CircuitChanged` facts
- `RiskCurrentView` containing policy version, structured policy scopes,
  limits, allocations, reservations, and circuits

The wire representation preserves every allocation and scope. It never derives
an `owner_id` by choosing one field, never relabels `policy_id` as `budget_id`,
and never selects the first allocation as a reservation summary.

### Execution

- `SubmitExecutionIntent` command and typed accepted/rejected result
- the accepted intent supports current single-leg and multi-leg semantics,
  including intent type, legs, completion policy, failure policy, deadline,
  execution options, and optional hedge policy
- `IntentLifecycleChanged`, `PlanCreated`, `OrderLifecycleChanged`,
  `FillRecorded`, and `ReconciliationRequired` facts
- separate bounded current views for active intents/plans and active orders;
  terminal history is queried rather than retained in mmap

Execution facts preserve `intent_id`, `plan_id`, `leg_id`, `order_id`, and
`fill_id` correlation where applicable. A fill ledger is a query/history
contract until a demonstrated mmap consumer requires a current view.

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

## 15. Compatibility policy

For each published v2 root:

- namespace is the owning `kairos.<business>.v2` namespace;
- file identifier is unique repository-wide and never reused;
- field ordinals are append-only;
- existing field meaning, requiredness, default, units, and identity scope do
  not change;
- fields are deprecated before removal and are not repurposed;
- a semantic breaking change creates a new root/file identifier, even if
  FlatBuffers could technically decode it;
- v1 and v2 may run side by side through explicit translators;
- a publisher emits one semantic version per publication path; it does not
  conditionally reinterpret the same bytes for different consumers.

The repository must retain a machine-readable baseline of every published v2
schema and run FlatBuffers conformance checks or an equivalent ordinal/default
compatibility check in CI.

Every active v2 root requires:

1. Rust encode/decode round trip;
2. Rust-produced golden bytes decoded by Python;
3. file-identifier and wrong-root rejection;
4. semantic validation failure fixtures;
5. old-writer/new-reader additive compatibility fixture;
6. stream continuity or mmap stable-read tests as applicable;
7. live/replay mapping parity at the public application boundary.

## 16. Migration policy

Migration is one vertical business slice at a time. A slice is complete only
when the v2 path is active, observed, and the equivalent v1 path is removed.

The initial disposition of the most important v1 contracts is:

| V1 contract | V2 disposition |
| --- | --- |
| `MessageHeader` | Replaced by shape-specific command/event metadata; no generic timestamp duplication |
| `SnapshotHeader` | Replaced by `ViewMetadata`; removes ambiguous `version` and any cursor interpretation |
| Market per-kind event roots | Migrated individually, beginning with Quote; unsupported kinds are not carried forward automatically |
| `PMC1` omnibus Market current view | Split only along demonstrated bootstrap access patterns |
| `PMH1` Market history mmap | Replaced by bounded query/dataset semantics unless a measured mmap caller is demonstrated |
| `ACE1` Account event | Replaced by typed variants; no string `kind`/optional-payload combinations |
| `AAC1` Account current view | Retains Account-owned state; Execution lifecycle is excluded and account-observed orders are explicitly named |
| `OIR1` single-order intent | Replaced by `SubmitExecutionIntent`, which represents the current intent/leg/policy model losslessly |
| `EXE1` Execution event | Split into typed lifecycle facts with intent/plan/leg/order/fill correlation |
| `PIJ1` intent projection | Moves under Execution ownership; there is no v2 Intent owner |
| `RAR3` authorization plus `RRD1` decision | Replaced by one authoritative authorize-and-reserve command/result semantic operation |
| `RKE1` Risk event | Split into typed decision, reservation, and circuit facts |
| `PRK1` Risk snapshot | Replaced by a lossless structured-scope Risk current view |
| Reference catalog FlatBuffers types | Not used as a second current-state catalog; Reference SQLite remains authoritative |
| System projections | Reintroduced only for concrete operational consumers and never as business truth |

### Phase 0: freeze semantics

- Treat this document as the admission gate.
- Add a registry entry before adding a concrete v2 root.
- Do not add fields or roots to v1 to imitate v2, except a critical compatible
  production fix.

### Phase 1: prove the common boundary

- Implement v2 common decimal, event metadata, and view metadata.
- Migrate one Market fact (`QuoteObserved`) end to end.
- Prove Rust/Python/live/replay parity and fail-closed gap behavior.

### Phase 2: prove the authoritative command chain

- Migrate `SubmitExecutionIntent` and `AuthorizeAndReserve`.
- Prove idempotency and delivery-unknown handling.
- Migrate order/fill/account settlement facts with correlation and causation.

### Phase 3: migrate current views

- Add only the bootstrap/current views used by the migrated slice.
- Record view key, upper size bound, freshness policy, and reader.
- Keep event recovery independent from snapshot reads.

### Phase 4: retire each v1 slice

- Stop dual publication for that slice.
- Remove its v1 decoder, generated bindings, mapper, and tests.
- Retain only compatibility fixtures required for supported persisted data.

Dual publication is temporary and occurs at the owner contract adapter, not by
making application/domain code understand both schema versions.

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
v1 replacement:
retirement condition:
```

The registry records delivered reality. Planned roots stay in design notes and
do not receive identifiers or generated code.

## 18. Decisions intentionally deferred

The following are selected per admitted root from measured caller needs, not
standardized speculatively:

- one mmap file per logical key versus a bounded keyed collection;
- Market retained replay versus ephemeral fail-closed delivery;
- whether a concrete view needs partial pagination or a dataset API;
- Decimal64 replacement for a demonstrated out-of-range field;
- transport choice beyond the semantic delivery profile.

Deferring these physical choices does not defer ownership, identity, time,
ordering, command, event, or compatibility semantics defined above.
