# Account / Execution / Market / Risk transport contract audit

Status: Gate 0 baseline audit, 2026-08-13.

This document audits the business data plane used by Strategy, Backtest, and
Research before the first end-to-end trading slice is declared complete. It
does not make Aeron or mmap a public Strategy/Research API. `kairospy`
applications remain the public boundary and hide transport details.

## Decision summary

The current transport direction is sound but the consumer contract is not yet
complete enough for the planned SPY option slice.

- Aeron carries incremental business facts. Every Aeron business payload must
  be a schema-identified FlatBuffers root.
- KSS1/mmap carries one current-state image. Every slot payload must be a
  schema-identified FlatBuffers root.
- JSON remains valid for UDS control/diagnostics, configuration, manifests,
  internal persistence, and immutable dataset metadata. JSON is not a business
  event or mmap payload format.
- Service/application models must map explicitly into contract models. A
  `serde_json::to_value/from_value` round trip is not an acceptable typed
  mapping even when the final bytes are FlatBuffers.
- Research consumes Dataset/History APIs and point-in-time tables. It does not
  consume live Aeron or mmap directly. Backtest Replay must map the same owner
  facts into the same public application event types used by live Strategy.
- A mmap snapshot is not an Aeron cursor or event backlog. A gap is recovered
  by event retention/a dedicated resync contract, or fails closed. Diffing a
  current snapshot must not pretend to reconstruct missed facts.

## Real consumer demand

| Consumer | Required input | Required behavior |
| --- | --- | --- |
| Research | Historical Quote, Greeks, Reference point-in-time facts, manifests and quality | Columnar/history access, deterministic composition; no direct IPC dependency |
| Backtest Strategy | Replay Quote/Bar/Greeks plus Account/Risk/Execution results | Same public event and command semantics as Paper/Live, deterministic ordering |
| Paper/Live Strategy | Current bootstrap queries plus incremental Market, Account, Risk, and Execution facts | Instance isolation, explicit continuity behavior, bounded backpressure, fail-closed gaps |
| Execution preflight | Current Account, Market, Reference, and Risk facts | Typed reads with freshness/generation evidence; authoritative commands for reservations |
| Reporting/operations | Account equity/positions, Execution orders/fills, Risk decisions/reservations | Queryable correlation chain; diagnostics may use JSON |

The first option strategy additionally requires Market Quote and Greeks,
Reference option contracts, multi-leg Intent, Risk decision/reservation,
Execution order/fill correlation, and Account multiplier/fee/PnL settlement.

## Current publication inventory

All physical stream ids are centralized in `kairos-transport` and use the
workspace default Aeron channel.

| Owner | Aeron physical/logical stream | Active event root | Active mmap root(s) | Current Strategy surface |
| --- | --- | --- | --- | --- |
| Market | `1301` / `market.events` | Per-kind roots: Quote, Trade, Bar, OrderBook, Greeks, Rate, Ticker24h, MarkPrice, IndexPrice, FundingRate, OpenInterest, InstrumentStatus | `PMC1` current plus qualified views; `PMB1` order book | Quote, Trade, Bar, Greeks |
| Account | `1401` / `account.events:{account_id}` | `ACE1` AccountEvent | `AAC1` current | Balance, Position, Equity, Status |
| Execution | `1501` / `execution.events` | `EXE1` ExecutionEventMessage | `PEO1` orders and `PIJ1` intents | Intent, Order, Fill |
| Risk | `1601` / `risk.events` | `RKE1` RiskEventMessage | `PRK1` budgets/reservations/circuits | Decision, Reservation, Circuit; policy activation is intentionally not dispatched |

Schemas such as Account equity history (`AAE1`), Execution fills (`PEF1`),
Market history (`PMH1`) and subscriptions (`PMS1`) exist but schema existence
does not mean a runtime publisher and a real consumer are complete. They must
not be counted as delivered until wired and tested.

## Findings

### P0: one workspace must mean one explicit Aeron Media Driver

The System runtime creates a workspace-owned media directory at
`run/aeron/media`. Publishers and Strategy bridge processes previously relied
on an implicit/default directory, so they could attach to a different driver.

The runtime now exports the workspace `AERON_DIR`, and Strategy-facing Market,
Account, Execution, and Risk composition passes that same directory explicitly
to each bridge. This is required for project isolation and for a real Paper or
Live loop to receive any event.

### P0: Market publication breadth must not leak into Strategy callbacks

Rust Market publishes twelve observation families on the same physical
stream. Python publicly maps Quote, Trade, Bar, and Greeks. OrderBook, Rate,
Ticker24h, MarkPrice, IndexPrice, FundingRate, OpenInterest, and
InstrumentStatus are transport-recognized continuity records but are not
public Strategy events.

Before the minimal option loop, define the actual v1 Strategy Market set. The
smallest justified set is Quote, Bar, and Greeks; this set is now public and
Trade remains supported. Non-Strategy observations now advance the global
Market cursor without entering user callbacks. Live dispatch now additionally
matches the Strategy instance's requested market id and selector (including a
Bar timeframe); filtered records still advance the physical cursor. Replay
remains dataset-scoped and therefore does not require a live subscription.

Unknown Market file identifiers now fail explicitly instead of falling
through to the Quote decoder.

### P0: event recovery is fail-closed, not resynchronizing

Aeron publication is best effort and deliberately drops notifications when no
subscriber is connected. Python sources join from the first observed event,
then require contiguous logical sequences. A later gap fails the owning
application and therefore the Strategy runtime. There is no event retention
or dedicated resync contract for these four Strategy streams.

This is acceptable for a first Paper/Live slice only if it is stated as a
fail-closed limitation and covered by lifecycle tests. It is not acceptable to
claim transparent recovery from mmap. Durable resume requires an owner event
log/retention cursor or a dedicated owner-defined resync operation.

Account, Execution, Market, and Risk application tests reject gaps without
reading mmap. Strategy lifecycle tests prove a gap/failure from each of the
four module sources transitions the instance to `FAILED`, emits an
`event_source_failed` system fact, and does not attempt snapshot recovery.

### Complete: publishers map typed models directly

Account, Market, Risk, and Execution now map application/domain state directly
into contract-owned models before FlatBuffers encoding. No active Aeron event
or mmap snapshot publisher uses `serde_json::to_value/from_value`,
`serde_json::Value`, or an equivalent serialization round trip as a model
adapter.

JSON remains allowed at explicitly JSON control, configuration, diagnostic,
persistence, and dataset-metadata boundaries. `AGENTS.md` and
`tests/test_business_publisher_architecture.py` enforce the distinction.
Focused Rust library tests cover the four publishers' owning services.

### P0: snapshot and trace metadata are not yet a consistent contract

`SnapshotHeader.version` was used as a schema version by Account, Execution,
and Risk but as a generation by Market. Market now publishes `version = 1` and
keeps generation in `generation`.

The implemented KSS1 contract and `shared-memory.md` say that a snapshot does
not define a cursor or join point. `cross-module-state-and-events.md`
previously listed `event_stream_id/event_sequence` as mandatory snapshot
metadata and has now been aligned with the implemented contract. The rule is:

- snapshot generation/as-of describe the current image;
- any incorporated-event watermark is audit evidence only unless the owner
  also provides a retained cursor/resume contract;
- Strategy must never infer missed events by diffing snapshots.

All active snapshot encoders now use `version = 1`, keep state generation in
`generation`, set `generated_at_unix_nanos` to publication time, and derive
`as_of_unix_nanos` from the latest business observation represented in the
snapshot (zero means an empty/unknown image). The common event header still
lacks an explicit schema version and
causation/correlation fields even though the Python public metadata currently
defaults `schema_version` to 1. Full Signal -> Intent -> Decision -> Order ->
Fill -> Account traceability therefore depends on payload-specific ids and is
not uniform yet. That is a future schema-evolution item, not a snapshot
consistency blocker for Gate 0.

### Complete: shared active-root compatibility fixtures

`tests/fixtures/active_wire/` contains deterministic hexadecimal FlatBuffers
payloads for all 21 currently published Account, Execution, Market, and Risk
event/snapshot roots. Rust validates the roots and identifiers through the
generated bindings; Python decodes the same bytes through its generated
bindings and public event decoders. The generator is deterministic and its
output is compared with the checked-in manifests.

### P1: several schemas are weakly discriminated

Account, Execution, and Risk event envelopes use string `kind` fields plus
optional payload tables rather than FlatBuffers enums/unions. They are binary
and schema-identified, but the schema itself does not prevent invalid
kind/payload combinations. Current decoders must continue strict validation.
A v2 enum/union migration is justified when the first contract evolution is
needed; it is not required merely to make the directory layout uniform.

### P1: declared projections exceed current delivery

Separate Account equity history, Execution fill projection, Market warm-up
history, and Market subscription projections are sensible only where read
cadence and consumers justify them. For the minimum loop:

- Account current must retain quantity, average/mark price, realized and
  unrealized PnL, equity, multiplier-sensitive settlement results, and fees;
- Execution current needs intent/order lookup; fills may remain an event/query
  ledger until a concrete mmap consumer needs `PEF1`;
- Market current needs latest Quote/Bar/Greeks; full order-book depth stays a
  separate root;
- Risk current needs budgets, reservations, circuits, generation, and
  scope-specific availability.

Do not publish every existing schema merely because it was generated.

## Required contract tests

Each active root must have:

1. Rust encode -> Rust decode validation and file-identifier rejection;
2. the same Rust fixture decoded by Python;
3. required-field, enum/discriminator, decimal, timestamp, and identity tests;
4. KSS1 inactive-slot publication and stable double-read tests for mmap;
5. logical stream id, positive sequence, duplicate, regression, and gap tests;
6. launch/instance/account/strategy isolation tests;
7. bounded queue/backpressure and bridge termination behavior tests;
8. Replay event -> public application event parity with the live decoder.

Gate 0 executable evidence is mapped as follows:

- `crates/kairos-protocol/tests/active_wire_fixtures.rs` and
  `crates/business/market/contract/tests/active_wire_fixtures.rs` decode all
  21 active roots in Rust, validate each declared identifier, and prove a
  corrupted identifier is rejected by the generated identifier check;
- `tests/test_active_wire_fixtures.py` decodes the same checked bytes with the
  Python generated bindings and verifies the deterministic fixture generator;
- the four business contract `encoding.rs` test modules decode actual
  production snapshot-encoder output and verify version, generation,
  publication time, business as-of time, and completeness;
- `kairos-transport` verifies KSS1 inactive-slot alternation, retention of the
  previous slot, and stable repeated reads;
- the four `test_*_event_flow.py` suites cover stream identity, positive
  sequence, duplicate/stale delivery and fail-closed gaps; `test_strategy_host.py`
  proves every domain gap moves Strategy to `FAILED` without mmap recovery;
- event-flow identity cases plus project/launch tests cover account, strategy,
  launch, instance, and workspace isolation;
- `test_strategy_ingress.py`, `test_aeron_event_sources.py`, and the four Rust
  `event_bridge_readiness.rs` suites cover bounded queues, source failure,
  bridge termination, readiness, and workspace-owned Aeron directories;
- `test_replay_and_live_decoder_expose_the_same_public_market_event` proves
  replay and live decoding expose the same public Strategy event contract.

## Exit criteria for Gate 0

Gate 0 is complete when:

- the active Strategy Market event set is explicitly chosen and fully typed
  end to end;
- every active Aeron and mmap payload is FlatBuffers with a validated file
  identifier;
- no active publisher maps Rust models through serde JSON;
- all processes use the same workspace-owned Aeron directory;
- event gap behavior is documented and tested as fail-closed or backed by a
  real owner resync contract;
- snapshot version/generation/as-of semantics are consistent;
- a fixture proves Rust/Python parity for every active root;
- unused projection roots are labelled planned rather than delivered.

## Minimum closed-loop slice after Gate 0

Use one deterministic, single-leg slice first to validate plumbing, while
keeping the later SPY spread requirements visible:

```text
Market Quote or completed Bar
-> Strategy signal
-> Execution target-position Intent
-> Risk decision and reservation
-> simulated Execution Order and Fill
-> Account position, equity and PnL
-> correlated report
```

The fixture must prove that the same public Strategy contract can consume a
Replay event and a decoded live event, and that the final Account result is
repeatable for the same fixture/configuration/seed. Once this plumbing slice
passes, extend the same chain to Quote + Greeks, multi-leg Intent, conservative
package execution, multiplier, fees, and spread settlement.
