# KairosPy Architecture

KairosPy is organized around a strategy-runtime product axis: strategy API, runtime orchestration, core domains, service orchestration, run modes, integrations, data storage, and surface.

See [runtime_layers.md](runtime_layers.md) for the detailed target layering inside `kairospy.application.runtime`.

## Package Boundaries

- `kairospy.application.runtime`
  Owns the run-scoped data pipeline, strategy event loop, runtime events, event lines, runtime-facing service scheduling, component scheduling, runtime projections, runtime read models, and run profiles. Runtime can hold stable services and handlers supplied by mode/service code, but it does not submit orders through concrete brokers, parse venue payloads, own market/account/execution domain state machines, or instantiate provider-specific service implementations. External integrations, execution adapters, and account collectors feed runtime through one data pipeline boundary.

- `kairospy.core.execution`
  Owns the execution domain after a strategy emits an intent: order planning, order state transitions, fills, local ledger updates, reservations, buying-power checks, broker gateway protocols, and execution state snapshots. Runtime-facing views such as `execution.current` live in `kairospy.application.runtime.projection.execution`. Runtime-mode adapters, persistence, and simulation policy selection live above core.

- `kairospy.core.account`
  Owns account identity, balances, positions, margin snapshots, ledgers, and provider-neutral account state derivation. `AccountState` is a core account state object, not a runtime read model and not an order journal. Runtime-facing views such as `account.current` live in `kairospy.application.runtime.projection.account`. Reservations, pending orders, buying-power checks, gateway protocols, and reconciliation orchestration live outside `core.account`.

- `kairospy.infrastructure.integrations`
  Owns external system adapters. Exchange connectors represent venues that own order matching; broker adapters are account/order gateways used for market access and execution; provider adapters supply market/reference data without order routing. Provider-specific parsing, lifecycle row mapping, and private stream ingestion belong under provider/connector payload modules such as `kairospy.infrastructure.integrations.payloads.ccxt_market`, `kairospy.infrastructure.integrations.payloads.ccxt_execution`, `kairospy.infrastructure.integrations.connectors.binance.reference`, and `kairospy.infrastructure.integrations.providers.massive_reference`. Integration adapters normalize provider payloads into `core.market` standard objects wrapped in `MarketEvent` before runtime projections apply them.

- `kairospy.core.market`
  Owns provider-neutral market semantics: standard market objects such as `Quote`, `OrderBookSnapshot`, `Bar`, `TradePrint`, and `RateObservation`; `MarketSelector` for selecting fields from those objects; `MarketEvent` as the normalized observation envelope; and order book state-machine helpers for applying snapshots and deltas. It does not own subscription registries, stream planning, dataset/stream identity, durable storage, runtime event loops, runtime view components, data-context binding, or provider I/O.

- `kairospy.application.service.domains.execution`
  Owns execution use-case adapters and persistence around core execution state: live intent execution against broker gateways, simulated execution policies for backtest/paper runs, and JSON execution state storage.

- `kairospy.application.service.domains.market`
  Owns user-facing market data requests and orchestration: `MarketDataSpec`, market data resolution, data-context binding helpers, derived dataset/stream names, historical download/read coordination, live persistence, replay timing, subscription registries, and subscription-to-stream planning from `MarketSelector` requests. It composes `core.market`, `core.reference`, and `data`; durable storage remains in `kairospy.infrastructure.data`, and provider payload parsing remains in `kairospy.infrastructure.integrations`.

- `kairospy.application.service.domains.account`
  Owns account startup, reconciliation, account difference calculation, snapshot gateway implementations, and live private stream collection use cases: fetching venue balances/open orders through injected gateways, applying provider-neutral parsers, importing open orders into execution state, classifying account stream balance deltas, and returning bootstrap/reconciliation results. Account models, ledgers, and account state derivation remain in `kairospy.core.account`.

- `kairospy.application.service.domains.reference`
  Owns reference catalog use cases: loading stored catalogs, serializing stored reference rows, building snapshots from provider rows, applying provider-neutral snapshots, selecting universes, applying corporate-action commands, saving catalogs, and appending lifecycle events. Reference identity, product identity helpers, lifecycle event definitions, and the in-memory catalog state machine remain in `kairospy.core.reference`.

- `kairospy.application.service.modes.backtest`
  Owns backtest mode configuration assembly: loading run config, constructing data context, resolving event or dataset sources, discovering startup subscriptions, loading strategies, and returning a configured backtest run. Backtest simulation behavior remains in `kairospy.application.mode.backtest`.

- `kairospy.application.service.modes.live`
  Owns live mode configuration assembly: loading live run config, resolving the configured account and market, constructing the live engine daemon target, binding venue brokers, account payload adapters, runtime state, private stream limits, and live trading safety policy. Live runtime behavior remains in `kairospy.application.mode.live`; provider-specific exchange behavior remains in `kairospy.infrastructure.integrations`.

- `kairospy.application.service.operations.run`
  Owns operational run configuration across modes: event-file run assembly, compatibility exports for configured daemon targets, and streaming paper daemon source binding. It sits above mode services because it coordinates run control and daemon execution.

- `kairospy.core.reference`
  Owns provider-neutral asset, instrument, listing, market, lifecycle, participant, universe query/result, market reference models, product identity helpers, and the in-memory catalog state machine. Reference models are the canonical source for market and instrument identity. `MarketResolver` converts user-facing symbols and aliases into runtime `MarketRef` handles derived from reference market definitions or explicit ephemeral defaults for local simulation; higher-level reference refresh, universe selection, and corporate-action command handling live in `kairospy.application.service.domains.reference`.

- `kairospy.core.views`
  Owns only shared view primitives: `ViewSchema`, `ViewFieldSchema`, `ViewEnvelope`, `ViewRegistry`, `ViewStore`, default schema registration, and view hashes. Strategy can re-export these for author ergonomics. Runtime projections use these primitives to publish read models; concrete runtime view payload dataclasses live under `kairospy.application.runtime.projection.*`.

- `kairospy.application.runtime.projection`
  Owns strategy-readable runtime read models by domain: account, market, execution, intent, order, reference, risk, and system. Runtime projections consume `RuntimeDataEnvelope` or injected core domain state and publish `ViewStore` entries. They may adapt core domain objects, but they do not own core state transitions or service orchestration. The intent journal projection currently publishes the compatibility key `system.intents`; independent order/reference read models should live in their own projection packages when their runtime data sources are introduced.

- `kairospy.application.context`
  Owns the strategy-side runtime context: named data bindings, controlled runtime data reads, view access, account access, market subscriptions, runtime request handles, control requests, and intent factories. It coordinates strategy reads and side-channel requests but must not parse provider payloads, run execution, project account state, orchestrate runtime loops, or own market identity.

- `kairospy.infrastructure.data`
  Owns durable dataset identities, stores, queries, sinks, and stream feeds. Data code should not own market domain models, market row schemas, instrument reference models, or runtime subscriptions.

- `kairospy.application.strategy`
  Owns the strategy author protocol and re-exports: stable `StrategySignal` trigger notifications, hook contracts, control request types, and strategy-facing view helpers. It does not own runtime context/control implementation and does not import runtime implementation modules.

- `kairospy.application.mode.backtest`
  Owns historical simulation entry points, simulated account configuration, backtest results, and metrics. Shared simulated execution policies live in `kairospy.application.service.domains.execution`.

- `kairospy.application.mode.live`
  Owns live runtime binding: gateway protocols, account reconciliation, private account stream collection, and live engine orchestration. Provider payload parsing is injected through adapters from `kairospy.infrastructure.integrations`.

- `kairospy.application.mode.paper`
  Owns non-production paper runtime entry points. It composes the same runtime and execution primitives instead of inheriting from backtest internals.

- `kairospy.surface`
  Owns user-facing CLI and product APIs.

## Naming Rules

- Use `Runtime` for strategy event-loop concerns.
- Use `RunProfile` for mode declarations such as backtest, paper, and live.
- Use `Execution` for intent-to-order-to-fill behavior.
- Use `Account` for balances, positions, snapshots, and projections.
- Use `Integration` for external systems and provider-specific payloads.
- Use `Exchange` for a venue that owns matching and market identity.
- Use `Broker` for a market/account/order gateway used to fetch account state and place/cancel orders.
- Use `Provider` for market/reference data sources that do not route orders.
- Use `Gateway` for external I/O protocols.
- Use `PayloadAdapter` for provider payload normalization and ingestion.
- Use `Projection` for runtime `ViewStore` read-model publishers. Use `State` or `Snapshot` for core domain state.
- Use `View` for strategy-readable runtime state.
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

Strategies may refer to symbols through `MarketResolver`; runtime intents and account positions use canonical `instrument_id`, while live broker routing uses `market_id` when available. Provider-specific raw fields are normalized inside `kairospy.infrastructure.integrations` payload adapters before reaching runtime views.

Non-instrument market data, such as interest rates, funding rates, curve points, or index values, must not be forced into `instrument_id`. Market observations carry an explicit subject:

- `subject_type` identifies whether the observation is about an `instrument`, `market`, `rate`, `curve`, or `index`.
- `subject_id` identifies the referenced subject inside the relevant reference namespace.
- `kind` identifies the observation shape, such as `quote`, `orderbook`, `bar`, `funding_rate`, `interest_rate`, `curve_point`, or `index_value`.

Market subscriptions use standard object selectors. Strategies request fields from explicit objects, such as `Bar.select("close", interval="1m")` or `Quote.select("bid", basis="ticker")`. Service-domain market code turns those selector requirements into provider-neutral stream plans. Provider-specific event shapes stay inside `kairospy.infrastructure.integrations`; integrations emit `MarketEvent(value=standard_object)` events, and runtime market projections apply those events into typed views. Runtime publishes subscription state, a field-level `market.fields` projection for strategy-facing panels, a generic `market.observations` projection for arbitrary subject/kind data, and typed market projections such as `market.quotes`, `market.books`, `market.bars`, `market.trades`, and `market.rates`.

Views are runtime read models, not core domain ownership. Shared view primitives live in `core.views`; strategy re-exports them through `strategy.views` for author ergonomics. Market/account/execution/intent/order/reference/risk/system views live under `runtime.projection` and are scheduled by runtime components, while provider event types and domain-internal state machines stay behind their package boundaries.

## Runtime Flow

```text
Data/EventSource/Integration/Execution/Account
  -> RuntimeDataEnvelope
  -> RuntimeDataPipeline
  -> Runtime projectors
  -> RuntimeLine
  -> MarketResolver/MarketRef
  -> StrategySignal trigger
  -> RuntimeKernel
  -> Domain projections and market subscriptions
  -> ViewStore
  -> TradeIntent
  -> ExecutionCoordinator or simulated execution model
  -> RuntimeDataEnvelope
  -> AccountSnapshot/AccountState
  -> Runtime views
```

Backtest, paper, and live modes share `RuntimeKernel` and `RuntimeRunner`; they differ only in event sources, account sources, payload adapters, and execution adapters.

Runtime owns one data plane: `RuntimeDataEnvelope`. Sources, integrations, execution adapters, and account adapters emit envelopes; runtime ingests them into `RuntimeDataPipeline`, applies domain projectors, updates views, and dispatches strategy-facing `StrategySignal` triggers. `StrategySignal` does not carry business payload; strategies must read data through context accessors and runtime-owned views. Account state flows through `AccountSnapshot`, `AccountState`, explicit equity/source fields, and non-domain metadata inside `AccountRuntimePayload`; order state flows through `ExecutionRuntimePayload` with `OrderEvent` or `OrderState`.

Runtime sources emit `RuntimeDataEnvelope` directly. Market projectors consume envelope payloads such as `MarketEvent` and `MarketObservation`; account and execution projectors consume their typed runtime payloads. There is no secondary runtime event hierarchy.

`kairospy.application.context` is the strategy-side access layer over the runtime data pipeline and views. It exposes controlled reads such as `context.latest_data()`, view access, market access, account access, subscriptions, and intent factories. It must not own provider parsing, execution, account projection, daemon control, or durable storage semantics.

Execution adapters and backtest accounting must not read callback signal payloads; callback signals have no payload. They should consume market facts through runtime-owned market projections such as `market.fields` as the canonical source for prices, volume, and other execution inputs.
