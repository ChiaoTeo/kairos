# KairosPy

KairosPy is a strategy runtime for trading systems. Strategy authors write against stable strategy events, views, and intents; KairosPy composes core trading domains with backtest, paper, and live modes plus external provider integrations.

The project is organized around one product axis:

- `kairospy.application.strategy`: strategy author API, stable strategy events, strategy context, controls, and strategy-facing views.
- `kairospy.application.runtime`: strategy event loop, runtime data pipeline, event lines, projection scheduling, runtime views, and run profiles.
- `kairospy.core`: stable trading domains.
- `kairospy.core.market`: provider-neutral market observations, quote/book/bar/trade/rate models, subscription specifications, and row encoders.
- `kairospy.core.execution`: intent-to-order-to-fill behavior, execution state, local ledger updates, and simulated/live execution adapters.
- `kairospy.core.account`: account identity, balances, positions, snapshots, account state derivation, and ledgers.
- `kairospy.core.reference`: provider-neutral asset, instrument, listing, market, lifecycle, participant, identity, resolver handle, product, catalog, and universe models.
- `kairospy.application.service.domain`: user-facing domain use cases for account, market data, reference data, and execution primitives.
- `kairospy.application.service.runtime`: runtime-facing service implementations for account, market data, execution, and reference ports.
- `kairospy.application.service.modes`: mode-specific runtime assembly for backtest, paper, and live runs.
- `kairospy.application.system`: operational services for accounts, run registries, daemon control, artifacts, and account journals.
- `kairospy.core.intent` and `kairospy.core.order`: strategy intent and order state models.
- `kairospy.core.views`: shared view schema, envelope, registry, and store primitives.
- `kairospy.infrastructure.data`: durable datasets, stores, queries, sinks, and stream feeds.
- `kairospy.infrastructure.integrations`: external systems and provider payload adapters such as ccxt, Binance, Hyperliquid, IBKR, and Massive.
- `kairospy.surface`: CLI and user-facing product APIs.

Surface packages are intentionally thin: external callers should reach run behavior through `kairospy.application.system` and mode configuration through `kairospy.application.service.modes` instead of composing runtime internals directly.

See [System Architecture Plan](docs/system-architecture-plan.md) for the planned migration toward system-owned trading runtime lifecycle management.

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
kairospy backtest run --config examples/configs/binance_backtest.toml
kairospy run paper --config examples/configs/hyperliquid_paper.toml
kairospy run daemon status
kairospy data download --symbol BTC/USDT
kairospy data read market.ohlcv.binance_spot_btc_usdt.1m
kairospy data replay market.trades.binance_spot_btc_usdt --speed 0
kairospy reference markets --active-only
kairospy streams print --kind ticker --symbol BTC/USDT --limit 1
```

Project defaults can be supplied with `kairos.toml`; see [kairospy/config.py](kairospy/config.py).
Reference catalogs and lifecycle events are persisted in SQLite at `.kairos/reference/reference.sqlite` by default.

## Python Runtime Shape

Strategies receive stable strategy events and emit intents; runtime does not submit orders. Execution adapters convert intents into simulated fills or live orders.

```python
from pathlib import Path

from kairospy.application.system import TradingSystemLauncher


result = TradingSystemLauncher().run_backtest_config(Path("examples/configs/binance_backtest.toml"))

print(result.runtime.strategy_id, result.final_equity)
```

Live runs require an explicit account payload adapter from an integration package, for example `CcxtAccountPayloadAdapter`. This keeps live orchestration provider-neutral while provider parsing and ingestion remain in `kairospy.infrastructure.integrations`.



Useful checks:

```bash
.venv/bin/python -m compileall -q kairospy
.venv/bin/pytest tests/test_architecture_boundaries_minimal.py -q
.venv/bin/pytest tests/test_*_minimal.py -q
```
