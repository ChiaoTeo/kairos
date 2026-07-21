# Kairos Examples

Examples now follow the consolidated product boundary:

```text
configure provider -> prepare data -> bind a workspace -> run user code
```

Examples use one Workspace for data bindings. Strategy code is ordinary Python selected by `kairospy run start --workspace ... --entrypoint module:callable`.

## Data

```bash
./pyenv/bin/python examples/data/discover_datasets.py
./pyenv/bin/python examples/data/load_local_dataset.py
./pyenv/bin/python examples/data/point_in_time_replay.py
```

## Connectors

```bash
./pyenv/bin/python examples/connectors/reference_connector/verify_contract.py
```

## Runtime And Operations

```bash
./pyenv/bin/python examples/realtime/binance_order_book.py --symbol BTCUSDT --messages 1000 --depth 100
./pyenv/bin/python examples/operations/manual_order.py
```

## Workspace And Run

Create and bind a workspace from the CLI, then run user-owned strategy code:

```bash
kairospy init
kairospy workspace create alpha
kairospy workspace bind-data alpha --name bars --dataset market.ohlcv.crypto.binance.btc-usdt.1d
kairospy run start --workspace alpha --mode backtest --entrypoint my_strategies.sma_cross:build
```

Run artifacts are written under `.kairos/run/{run_id}`. Workspace data bindings live under `.kairos/workspace/{name}`.
