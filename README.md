# KairosPy

KairosPy is a strategy runtime for trading systems. Strategy authors write against stable strategy events, views, and intents; KairosPy composes core trading domains with backtest, paper, and live modes plus external provider integrations.

The project is organized around one product axis:

- `kairospy.strategy`: strategy author API, stable strategy events, strategy context, controls, and strategy-facing views.
- `kairospy.runtime`: strategy event loop, runtime data pipeline, event lines, component scheduling, and run profiles.
- `kairospy.core`: stable trading domains.
- `kairospy.core.market`: provider-neutral market observations, quote/book/bar/trade/rate models, subscription specifications, and row encoders.
- `kairospy.core.execution`: intent-to-order-to-fill behavior, execution state, local ledger updates, and simulated/live execution adapters.
- `kairospy.core.account`: account identity, balances, positions, snapshots, projections, reservations, ledgers, and provider-neutral account bootstrap.
- `kairospy.core.reference`: provider-neutral instrument, listing, market, lifecycle, resolver, store, and universe models.
- `kairospy.core.intent` and `kairospy.core.order`: strategy intent and order state models.
- `kairospy.core.views`: shared view schema, envelope, registry, and store primitives.
- `kairospy.modes`: run modes that compose strategy, runtime, core, data, and integrations.
- `kairospy.modes.backtest`: historical simulation entry points, simulated account configuration, results, and metrics.
- `kairospy.modes.paper`: non-production runtime entry points built from the same runtime and execution primitives as backtest.
- `kairospy.modes.live`: live gateway protocols, account reconciliation, private stream collection, and live engine orchestration.
- `kairospy.data`: durable datasets, stores, queries, sinks, and stream feeds.
- `kairospy.integrations`: external systems and provider payload adapters such as ccxt, Binance, Hyperliquid, IBKR, and Massive.
- `kairospy.surface`: CLI and user-facing product APIs.

Old top-level domain and mode packages are intentionally not part of the layout; use `kairospy.core.*` and `kairospy.modes.*`.

## Install For Development

```bash
uv sync
.venv/bin/pytest tests/test_*_minimal.py -q
```

The current minimal suite covers runtime, data, integrations, account/execution interaction, backtest, paper, live context, strategy events/views, market observations, reference catalog, and architecture boundaries.

## CLI

The CLI currently exposes focused product surfaces:

```bash
kairospy --help
kairospy data download --symbol BTC/USDT --dataset market.ohlcv.binance.btc_usdt.1m
kairospy data read market.ohlcv.binance.btc_usdt.1m
kairospy data replay market.trades.binance.btc_usdt --speed 0
kairospy reference markets --active-only
kairospy streams print --kind ticker --symbol BTC/USDT --limit 1
```

Project defaults can be supplied with `kairos.toml`; see [kairospy/config.py](kairospy/config.py).
Reference catalogs and lifecycle events are persisted in SQLite at `.kairos/reference/reference.sqlite` by default.

## Python Runtime Shape

Strategies receive stable strategy events and emit intents; runtime does not submit orders. Execution adapters convert intents into simulated fills or live orders.

```python
from decimal import Decimal
from tempfile import TemporaryDirectory

from kairospy.modes.backtest import BacktestEngine, SimulatedAccount
from kairospy.context import DataContext
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.runtime import IterableEventSource
from kairospy.strategy import StrategyBase, StrategyContext


class TargetBtc(StrategyBase):
    strategy_id = "target-btc"

    def on_market(self, context: StrategyContext, event):
        context.target_position("BTC/USDT", Decimal("1"), intent_id="enter")
        return ()


source = IterableEventSource(
    "market.ohlcv.example",
    [{"time": "2026-01-01T00:00:00+00:00", "kind": "bar", "market_id": "market:simulated:spot:btc_usdt", "instrument_id": "instrument:spot:btc:usdt", "market_key": "simulated_spot_btc_usdt", "close": "100"}],
)

with TemporaryDirectory() as temporary:
    market_resolver = MarketResolver(default_venue="simulated", default_market="spot")
    result = BacktestEngine(
        TargetBtc(),
        DataContext(DataStore(temporary, storage_format="jsonl")),
        SimulatedAccount("strategy-a", Decimal("1000"), cash_currency="USDT"),
        market_resolver=market_resolver,
    ).run(source)
```

Live runs require an explicit account payload adapter from an integration package, for example `CcxtAccountPayloadAdapter`. This keeps live orchestration provider-neutral while provider parsing and ingestion remain in `kairospy.integrations`.

## Architecture

See [docs/architecture.md](docs/architecture.md) for the package boundary rules and runtime flow.
See [docs/migration_audit.md](docs/migration_audit.md) for the legacy-domain deletion and replacement map.

Useful checks:

```bash
.venv/bin/python -m compileall -q kairospy
.venv/bin/pytest tests/test_architecture_boundaries_minimal.py -q
.venv/bin/pytest tests/test_*_minimal.py -q
```
