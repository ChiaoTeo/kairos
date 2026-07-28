# Backtest Product Plan

KairosPy already has the core pieces needed to run an initial backtest:

- `kairospy.modes.backtest` provides `BacktestEngine`, simulated accounts, fills, equity, trades, and metrics.
- `kairospy.data` provides durable historical datasets and event replay inputs.
- `kairospy.strategy` and `kairospy.context` give strategy authors a stable API for reading runtime state and emitting intents.
- `kairospy.surface.products.run` exposes a configured `run backtest` command.

The next step is not to rebuild a large research platform. The next step is to make the existing backtest path usable as a product: easy to configure, easy to run, easy to inspect, and extensible enough for strategy-local factors first.

## Non-Goals

Do not introduce a data manifest system or a data quality/promotion workflow.

Those concepts are useful in larger governed data platforms, but they are too heavy for the current product stage. Backtest data should remain simple:

- Users name a dataset or an events file directly.
- The runner performs lightweight input validation before execution.
- Missing or malformed fields fail fast with actionable errors.
- More advanced data governance can stay out of scope until users actually need it.

Do not reintroduce old broad packages such as `analytics`, `research`, `portfolio`, `risk`, or `governance` as top-level domains. New work should stay behind the current package boundaries described in `docs/architecture.md`.

## Product Goal

A user should be able to run a backtest with one configuration file and get a durable run directory containing:

- Normalized run configuration
- Strategy identity and parameters
- Data input identity
- Account and execution assumptions
- Equity curve
- Fills
- Orders or intent states
- Closed trades
- Metrics summary
- Human-readable report

The first product target is a single-strategy, single-run workflow. Parameter sweeps and multi-strategy research come after this path is solid.

## User Workflow

The intended workflow:

```bash
kairospy data download \
  --exchange binance \
  --symbol BTC/USDT \
  --timeframe 1m \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00

kairospy backtest run --config backtest.toml

kairospy backtest show <run-id>
kairospy backtest report <run-id>
```

The existing `kairospy run backtest --config ...` command can remain for compatibility, but the product-facing command should be explicit: `kairospy backtest ...`.

## Backtest Configuration

Use a lightweight run config instead of a data manifest.

Example:

```toml
[run]
id = "btc-sma-2026-01"
mode = "backtest"
strategy = "examples.strategies.sma:SmaStrategy"

[strategy.params]
symbol = "BTC/USDT"
fast_window = 20
slow_window = 50
target_quantity = "1"
venue = "binance"
market = "spot"
timeframe = "1m"

[backtest]
start = "2026-01-01T00:00:00+00:00"
end = "2026-02-01T00:00:00+00:00"
venue = "binance"
market = "spot"
price_field = "close"

[account]
cash = "100000"
currency = "USDT"
fee_rate = "0.001"

[execution]
slippage_bps = "1"
volume_participation = "1.0"
```

The runner should support two input modes:

- `backtest.dataset`: read historical rows from `DataStore`.
- `backtest.events`: read an explicit JSONL event file.

If both are present, `events` should win because it is the most explicit replay artifact.

## Data Handling

Keep data handling practical:

- Store historical bars/trades in `DataStore`.
- Resolve datasets with existing aliases.
- Apply `start`, `end`, `columns`, and `limit` through existing `DataStore.read_rows`.
- Convert rows into `RuntimeDataEnvelope` through existing event source logic.
- Validate only the fields required by the selected execution and strategy path.

For OHLCV backtests, required fields should be:

- `time`
- `market_id` or enough information for `MarketResolver`
- `instrument_id` or enough information for `MarketResolver`
- configured `price_field`, defaulting to `close`

Optional fields:

- `open`
- `high`
- `low`
- `close`
- `volume`
- `timeframe`
- `market_key`

This gives users direct control without adding a separate data planning layer.

## Strategy And Factors

Initial strategy support can keep factors strategy-local.

That means a strategy may maintain rolling windows, pandas-derived series, or custom state internally:

```python
class SmaStrategy(StrategyBase):
    strategy_id = "sma"

    def __init__(self, fast_window: int = 20, slow_window: int = 50):
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.closes = []

    def on_start(self, context):
        context.subscribe_market_fields(
            "BTC/USDT",
            venue="binance",
            market="spot",
            fields=(MarketDataField("bar.close", interval="1m"),),
        )
        return ()

    def on_market(self, context, signal):
        bar = context.latest_data(domain="market", kind="bar")
        if bar is None:
            return ()
        close = Decimal(str(bar.fields["bar.close"]))
        self.closes.append(close)
        if len(self.closes) < self.slow_window:
            return ()
        fast = sum(self.closes[-self.fast_window:]) / Decimal(self.fast_window)
        slow = sum(self.closes[-self.slow_window:]) / Decimal(self.slow_window)
        context.target_position("BTC/USDT", Decimal("1") if fast > slow else Decimal("0"))
```

After the configured single-run path is stable, introduce a narrow factor module. Prefer `kairospy.core.factor` only if factors become runtime/domain-owned primitives; otherwise prefer a product-facing `kairospy.strategy.factor` helper layer.

Minimum factor model:

- `FactorSpec`: identity, version, inputs, parameters, warmup, outputs.
- `FactorSnapshot`: values, as-of time, readiness, optional state hash.
- `FactorRuntime`: `update(event)`, `snapshot()`, `dump_state()`, `restore()`.

This can borrow the useful parts of `kairospy.bak/analytics/features/runtime.py` without restoring the old package structure.

## Execution Assumptions

The current simulated execution path is a good base. Product configuration should expose:

- Initial cash and currency
- Fee rate or commission model
- Slippage in basis points
- Fill price field
- Optional volume participation
- Limit order crossing behavior

Keep execution models explicit and deterministic. A backtest report must show the assumptions that produced the result.

## Results And Run Artifacts

Every backtest should write a run directory, for example:

```text
.kairos/runs/backtest/btc-sma-2026-01/
  config.normalized.toml
  summary.json
  metrics.json
  equity.parquet
  fills.parquet
  trades.parquet
  intent_states.parquet
  report.md
```

The first report can be Markdown. HTML charts can come later.

Minimum metrics:

- Initial equity
- Final equity
- Net profit
- Total return
- Max drawdown
- Max drawdown percent
- Sharpe
- Trade count
- Win rate
- Gross profit
- Gross loss
- Fees

Useful next metrics:

- Exposure time
- Average trade return
- Profit factor
- Best/worst trade
- Long/short split
- Monthly returns
- Benchmark return

## CLI Surface

Add a dedicated backtest product surface:

```text
kairospy backtest run --config backtest.toml
kairospy backtest show <run-id>
kairospy backtest metrics <run-id>
kairospy backtest equity <run-id>
kairospy backtest fills <run-id>
kairospy backtest trades <run-id>
kairospy backtest report <run-id>
```

The existing `run account` commands can continue to serve daemon-backed runs. The `backtest` product should be optimized for completed historical runs and durable artifacts.

## Implementation Plan

1. Add `kairospy.surface.products.backtest`.
2. Add a small `BacktestConfig` parser or extend `RunConfig` with backtest-specific fields.
3. Load strategy factories with optional `[strategy.params]`.
4. Support dataset-backed event source in addition to explicit events files.
5. Write run artifacts from `BacktestResult`.
6. Add `show`, `metrics`, `fills`, `trades`, and `report` commands.
7. Add one realistic example strategy and config.
8. Add tests for config parsing, dataset input, artifact writing, and CLI output.

## Design Rules

- Keep the backtest runner thin. Domain behavior belongs in `kairospy.modes.backtest`, `kairospy.core.execution`, and `kairospy.runtime`.
- Keep data preparation explicit but lightweight. Do not add data manifests, promotion statuses, or quality gates.
- Keep factor support optional until users need reusable factor artifacts.
- Persist enough metadata to reproduce a run.
- Prefer deterministic failures over silent assumptions.
- Avoid importing provider-specific code into backtest mode. Provider-specific parsing stays in `kairospy.integrations`.

## Near-Term Definition Of Done

The backtest product is usable when this works end to end:

```bash
kairospy data download --symbol BTC/USDT
kairospy backtest run --config examples/btc_sma_backtest.toml
kairospy backtest show btc-sma-2026-01
kairospy backtest trades btc-sma-2026-01
kairospy backtest report btc-sma-2026-01
```

The output should let a user answer:

- What strategy ran?
- What data did it use?
- What account and execution assumptions were applied?
- How much did it make or lose?
- What trades produced the result?
- Can the run be reproduced?
