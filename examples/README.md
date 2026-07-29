# Examples

The examples use the current application boundary:

- CLI runs go through `kairos run ...`.
- Python runs go through `TradingSystemLauncher`.
- Strategies live in `examples/strategies/` and are referenced from TOML with `module:callable`.
- Runtime internals are intentionally not assembled by example code.
- Relative paths in run configs are resolved from the workspace, normally the directory where the command is run.
- Direct run artifacts are written under `.kairos/runs/<mode>/<run_id>` in the current working directory unless a config explicitly sets `runs_root` or `state_path`.
- Daemon-managed runs use `.kairos/runs/<mode>/<run_id>` as a run group and write each launch under `instances/<run_instance_id>`.

## Quick Smoke Tests

Run a self-contained Python backtest:

```bash
uv run python examples/run_strategy_backtest.py
```

Run a strategy view example:

```bash
uv run python examples/view_runtime_usage.py
```

Run the top-level sample backtest config:

```bash
uv run kairos run backtest --config configs/runs/backtest.example.toml
```

Run the committed Hyperliquid replay smoke test. This uses `examples/events/hyperliquid_ticker.jsonl`, so it does not require network access:

```bash
uv run kairos run paper --config examples/configs/hyperliquid_paper_replay.toml
```

## Live Market Paper

Run Hyperliquid paper against the public websocket feed:

```bash
uv run kairos run paper --config examples/configs/hyperliquid_paper.toml
```

This is paper trading only: account state and fills are simulated locally, and no Hyperliquid API key is required.

Run OKX paper against the committed ticker event:

```bash
uv run kairos run paper --config examples/configs/okx_paper.toml
```

## Backtest Data

The `examples/configs/hyperliquid_backtest.toml` config reads the committed example data under `examples/.kairos/data`:

```bash
uv run kairos run backtest --config examples/configs/hyperliquid_backtest.toml
```

To download fresh Binance OHLCV data for `examples/configs/binance_backtest.toml`:

```bash
uv run kairos market download \
  --root examples/.kairos/data \
  --format jsonl \
  --exchange binance \
  --driver ccxt \
  --symbol BTC/USDT \
  --timeframe 1m \
  --limit 1000 \
  --mode replace
```

Then run:

```bash
uv run kairos run backtest --config examples/configs/binance_backtest.toml
```

## OKX Live

The OKX live examples are bounded smoke configs with `max_iterations = 1` and safety disabled by default:

```bash
export OKX_MAIN_API_KEY=...
export OKX_MAIN_SECRET=...
export OKX_MAIN_PASSWORD=...

uv run kairos run validate examples/configs/okx_live.toml
uv run kairos run live --config examples/configs/okx_live.toml
```

Set `live.safety.trading_enabled = true` only after checking account bootstrap, private streams, and order limits.
