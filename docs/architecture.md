# KairosPy Architecture

KairosPy is organized around a strategy-runtime product axis: strategy API, runtime orchestration, core domains, run modes, integrations, data, and surface.

## Package Boundaries

- `kairospy.runtime`
  Owns the run-scoped data pipeline, strategy event loop, runtime events, event lines, component scheduling, system projections, and run profiles. Runtime code does not submit orders, parse venue payloads, or own market/account/execution domain views. External integrations, execution adapters, and account collectors feed runtime through one data pipeline boundary.

- `kairospy.core.execution`
  Owns the execution domain after a strategy emits an intent: order planning, order state transitions, fills, local ledger updates, reservations, and simulated execution models.

- `kairospy.core.account`
  Owns account identity, balances, positions, margin snapshots, account projections, reservations, ledgers, and provider-neutral account bootstrap orchestration.

- `kairospy.integrations`
  Owns external system adapters. Provider-specific parsing and private stream ingestion belong here, such as `kairospy.integrations.ccxt`. Integration adapters normalize provider payloads into stable core-domain update models such as `MarketUpdate` before domain packages apply them.

- `kairospy.core.market`
  Owns provider-neutral market observations, quote/book/bar/trade/rate models, market subscription specifications, market row encoders, and market-specific data binding helpers. Market code can depend on reference market handles, but it does not own durable storage, runtime event loops, or provider I/O.

- `kairospy.core.reference`
  Owns provider-neutral asset, instrument, listing, market, lifecycle, store, universe, and market resolution models. Reference models are the canonical source for market and instrument identity. `MarketResolver` converts user-facing symbols and aliases into runtime `MarketRef` handles derived from reference market definitions or explicit ephemeral defaults for local simulation.

- `kairospy.core.views`
  Owns shared view primitives: `ViewSchema`, `ViewFieldSchema`, `ViewEnvelope`, `ViewRegistry`, `ViewStore`, and view hashes. Strategy can re-export these for author ergonomics, but core domains must import them from `core.views`.

- `kairospy.context`
  Owns the strategy-side runtime context: named data bindings, controlled runtime data reads, view access, account access, market subscriptions, runtime request handles, control requests, and intent factories. It coordinates strategy reads and side-channel requests but must not parse provider payloads, run execution, project account state, orchestrate runtime loops, or own market identity.

- `kairospy.data`
  Owns durable dataset identities, stores, queries, sinks, and stream feeds. Data code should not own market domain models, market row schemas, instrument reference models, or runtime subscriptions.

- `kairospy.strategy`
  Owns the strategy author protocol and re-exports: stable `StrategySignal` trigger notifications, hook contracts, control request types, and strategy-facing view helpers. It does not own runtime context/control implementation and does not import runtime implementation modules.

- `kairospy.modes.backtest`
  Owns historical simulation entry points, simulated account configuration, backtest results, and metrics. Shared simulated execution models live in `kairospy.core.execution`.

- `kairospy.modes.live`
  Owns live runtime binding: gateway protocols, account reconciliation, private account stream collection, and live engine orchestration. Provider payload parsing is injected through adapters from `kairospy.integrations`.

- `kairospy.modes.paper`
  Owns non-production paper runtime entry points. It composes the same runtime and execution primitives instead of inheriting from backtest internals.

- `kairospy.surface`
  Owns user-facing CLI and product APIs.

## Naming Rules

- Use `Runtime` for strategy event-loop concerns.
- Use `RunProfile` for mode declarations such as backtest, paper, and live.
- Use `Execution` for intent-to-order-to-fill behavior.
- Use `Account` for balances, positions, snapshots, projections, and account bootstrap.
- Use `Integration` for external systems and provider-specific payloads.
- Use `Gateway` for external I/O protocols.
- Use `PayloadAdapter` for provider payload normalization and ingestion.
- Use `Projection` for state derived from events.
- Use `View` for strategy-readable state.
- Use `MarketRef` for lightweight runtime market handles.
- Use `MarketResolver` for symbol or alias resolution into `MarketRef`.
- Use `Engine` only for thin user-facing orchestration entry points.

Deprecated broad package names such as `trading` should not be reintroduced.
Deprecated ambiguous package names such as `schema` should not be reintroduced for domain models, records, or views. Use `core.market` for market domain models and row encoders, `data` for storage/query/stream infrastructure, and `core.views` for shared view schemas.

## Identity Model

Reference identity is canonical:

- `instrument_id` identifies the economic instrument, such as a spot pair, equity, perpetual, future, or option.
- `market_id` identifies a venue/listing/market where that instrument is traded or observed.
- `market_key` is a filesystem- and stream-safe runtime key derived from venue, market type, and source symbol. It is allowed in dataset names, stream names, subscriptions, and compatibility lookup tables, but it is not the canonical instrument identity.

Standard market records must carry explicit `market_id`, `instrument_id`, and `market_key` fields. They must not emit legacy camelCase identity fields such as `instrumentId`.

Strategies may refer to symbols through `MarketResolver`; runtime intents and account positions use canonical `instrument_id`, while live broker routing uses `market_id` when available. Provider-specific raw fields are normalized inside `kairospy.integrations` payload adapters before reaching runtime views.

Non-instrument market data, such as interest rates, funding rates, curve points, or index values, must not be forced into `instrument_id`. Market observations carry an explicit subject:

- `subject_type` identifies whether the observation is about an `instrument`, `market`, `rate`, `curve`, or `index`.
- `subject_id` identifies the referenced subject inside the relevant reference namespace.
- `kind` identifies the observation shape, such as `quote`, `orderbook`, `bar`, `funding_rate`, `interest_rate`, `curve_point`, or `index_value`.

Market subscriptions use a subject/field model. Strategies request concrete facts such as `quote.bid`, `book.bid1`, `bar.open` with an interval, or `funding_rate.rate`; `kairospy.core.market` turns those logical field requirements into provider-neutral stream plans. Provider-specific event shapes stay inside `kairospy.integrations`; integrations emit stable core-domain update models, and each domain applies those updates into its own projections. Runtime publishes subscription state, a field-level `market.fields` projection for strategy-facing panels, a generic `market.observations` projection for arbitrary subject/kind data, and typed market projections such as `market.quotes`, `market.books`, `market.bars`, `market.trades`, and `market.rates`.

Views are domain-owned, not a single global object. Shared view primitives live in `core.views`; strategy re-exports them through `strategy.views` for author ergonomics. Market/account/execution views live with their core domains and are scheduled by runtime components, while provider event types and domain-internal state machines stay behind their package boundaries.

## Runtime Flow

```text
Data/EventSource/Integration/Execution/Account
  -> RuntimeDataEnvelope
  -> RuntimeDataPipeline
  -> Runtime projectors
  -> RuntimeLine
  -> MarketResolver/MarketRef
  -> StrategySignal trigger
  -> StrategyRuntime
  -> Domain projections and market subscriptions
  -> ViewStore
  -> TradeIntent
  -> ExecutionCoordinator or simulated execution model
  -> RuntimeDataEnvelope
  -> AccountSnapshot/AccountProjection
  -> Runtime views
```

Backtest, paper, and live modes share `StrategyRuntime` and `ModeRunner`; they differ only in event sources, account sources, payload adapters, and execution adapters.

Runtime owns one data plane: `RuntimeDataEnvelope`. Sources, integrations, execution adapters, and account adapters emit envelopes; runtime ingests them into `RuntimeDataPipeline`, applies domain projectors, updates views, and dispatches strategy-facing `StrategySignal` triggers. `StrategySignal` does not carry business payload; strategies must read data through context accessors and runtime-owned views. Account state flows through `AccountSnapshot`, `AccountProjection`, explicit equity/source fields, and non-domain metadata inside `AccountRuntimePayload`; order state flows through `ExecutionRuntimePayload` with `OrderEvent` or `OrderState`.

Runtime sources emit `RuntimeDataEnvelope` directly. Market projectors consume envelope payloads such as `MarketUpdate` and `MarketObservation`; account and execution projectors consume their typed runtime payloads. There is no secondary runtime event hierarchy.

`kairospy.context` is the strategy-side access layer over the runtime data pipeline and views. It exposes controlled reads such as `context.latest_data()`, view access, market access, account access, subscriptions, and intent factories. It must not own provider parsing, execution, account projection, daemon control, or durable storage semantics.

Execution adapters and backtest accounting must not read callback signal payloads; callback signals have no payload. They should consume market facts through runtime-owned market projections such as `market.fields` as the canonical source for prices, volume, and other execution inputs.
