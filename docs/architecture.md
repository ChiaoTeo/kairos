# KairosPy Architecture

KairosPy is organized around explicit runtime, execution, account, integration, and mode boundaries.

## Package Boundaries

- `kairospy.runtime`
  Owns the strategy event loop, runtime events, event lines, runtime-owned market/account/system projections, runtime-owned views, and run profiles. Runtime code does not submit orders or parse venue payloads.

- `kairospy.execution`
  Owns the execution domain after a strategy emits an intent: order planning, order state transitions, fills, local ledger updates, reservations, and simulated execution models.

- `kairospy.accounts`
  Owns account identity, balances, positions, margin snapshots, account projections, reservations, ledgers, and provider-neutral account bootstrap orchestration.

- `kairospy.integrations`
  Owns external system adapters. Provider-specific parsing and private stream ingestion belong here, such as `kairospy.integrations.ccxt`.

- `kairospy.reference`
  Owns provider-neutral asset, instrument, listing, market, lifecycle, store, universe, and market resolution models. Reference models are the canonical source for market and instrument identity. `MarketResolver` converts user-facing symbols and aliases into runtime `MarketRef` handles derived from reference market definitions or explicit ephemeral defaults for local simulation.

- `kairospy.context`
  Owns strategy context data bindings and strategy-facing data access. It consumes reference `MarketResolver` instances instead of owning market identity.

- `kairospy.schema`
  Owns provider-neutral market record/value schemas. Schema code should not own instrument reference models or symbol registries.

- `kairospy.strategy`
  Owns strategy protocols, strategy context, controls, and strategy-facing views.

- `kairospy.backtest`
  Owns historical simulation entry points, simulated account configuration, backtest results, and metrics. Shared simulated execution models live in `kairospy.execution`.

- `kairospy.live`
  Owns live runtime binding: gateway protocols, account reconciliation, private account stream collection, and live engine orchestration. Provider payload parsing is injected through adapters from `kairospy.integrations`.

- `kairospy.paper`
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

## Identity Model

Reference identity is canonical:

- `instrument_id` identifies the economic instrument, such as a spot pair, equity, perpetual, future, or option.
- `market_id` identifies a venue/listing/market where that instrument is traded or observed.
- `market_key` is a filesystem- and stream-safe runtime key derived from venue, market type, and source symbol. It is allowed in dataset names, stream names, subscriptions, and compatibility lookup tables, but it is not the canonical instrument identity.

Standard market records must carry explicit `market_id`, `instrument_id`, and `market_key` fields. They must not emit legacy camelCase identity fields such as `instrumentId`.

Strategies may refer to symbols through `MarketResolver`; runtime intents and account positions use canonical `instrument_id`, while live broker routing uses `market_id` when available. Provider-specific raw fields are normalized inside `kairospy.integrations` payload adapters before reaching runtime views.

## Runtime Flow

```text
Data/EventSource
  -> RuntimeLine
  -> MarketResolver/MarketRef
  -> StrategyRuntime
  -> Runtime projections
  -> ViewStore
  -> TradeIntent
  -> ExecutionCoordinator or simulated execution model
  -> AccountSnapshot/AccountProjection
  -> Runtime views
```

Backtest, paper, and live modes share `StrategyRuntime` and `ModeRunner`; they differ only in event sources, account sources, payload adapters, and execution adapters.
