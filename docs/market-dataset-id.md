# Market Dataset IDs

Market data is identified by a canonical dataset ID:

```text
market.{kind}.{venue}.{market}.{symbol}.{timeframe?}
```

Examples:

```text
market.ohlcv.binance.spot.btc_usdt.1m
market.trades.binance.spot.btc_usdt
market.orderbook.binance.spot.btc_usdt
market.ticker.binance.spot.btc_usdt
```

The dataset ID is the stable boundary for market data. Historical download,
local persistence, replay, backtest reads, live monitoring, and strategy
subscriptions should all resolve through the same dataset ID.

Canonical IDs use separate path-safe segments for venue, market, and symbol.
The old compressed form such as `market.ohlcv.binance_spot_btc_usdt.1m` is not
supported.

Strategies may subscribe directly by dataset ID:

```python
context.subscribe("market.ohlcv.binance.spot.btc_usdt.1m")
```

CLI commands also accept dataset IDs:

```bash
kairospy market read market.ohlcv.binance.spot.btc_usdt.1m
kairospy market replay market.ohlcv.binance.spot.btc_usdt.1m
kairospy market watch market.trades.binance.spot.btc_usdt --limit 100
kairospy market persist market.trades.binance.spot.btc_usdt --limit 100
```

Physical storage layout, including time partitioning, is a DataStore concern.
Callers should not depend on local file paths.
