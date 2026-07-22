# Data 产品使用文档

日期：2026-07-22

Data 只回答两个问题：

1. 数据有哪些。
2. 怎么读。

Dataset 是数据地址，不是 release、workspace、backtest 或 production 配置。读取链路不依赖 `schema.json`、`lineage.json`、`coverage.json`、`quality.json`、`manifest.json`、hash 或 audit 文件。

## 1. 用户心智

| 概念 | 含义 |
|---|---|
| Dataset ID | 稳定的数据地址，例如 `market.orderbook.crypto.binance.spot.btc-usdt` |
| Alias | 用户短名，例如 `btc_book`，只指向一个 Dataset ID |
| Historical | `datasets/<id>/data/` 下的历史文件 |
| Live | `datasets/<id>/live/` 下的实时状态或捕获 |
| Built-in product | 内置供应商产品，例如 `binance.orderbook`、`massive.trade`、`hyperliquid.perpetual.ohlcv.1h` |

## 2. 读取数据

Python：

```python
from kairospy.surface.product import Data

data = Data(".kairos/data")

rows = data.read(
    "market.ohlcv.crypto.hyperliquid.perpetual.1h",
    start="2026-01-01T00:00:00+00:00",
    end="2026-02-01T00:00:00+00:00",
    output="rows",
)

book = data.live("market.orderbook.crypto.binance.spot.btc-usdt")
```

CLI：

```bash
kairospy data query market.ohlcv.crypto.hyperliquid.perpetual.1h --limit 10
kairospy data metadata market.ohlcv.crypto.hyperliquid.perpetual.1h
kairospy data list
```

Reader 会递归扫描 Dataset 的 `data/` 目录。产品可以自由决定分区层级，例如 `event_day=2026-07-22/` 或 `event_day=2026-07-22/event_hour=13/`；读到内存后仍是一张逻辑大表。

## 3. 内置产品

内置产品不允许用户自定义 Dataset 名字。Dataset ID 由 product key 和 selector 自动生成。需要短名时使用 alias。

### Binance 实时

```python
data.connect(
    "binance.orderbook",
    instruments=["BTCUSDT"],
    market="spot",
    levels=20,
)

data.alias("market.orderbook.crypto.binance.spot.btc-usdt", "btc_book")
book = data.live("btc_book")
```

```bash
kairospy data connect binance.orderbook \
  --instrument BTCUSDT \
  --market spot \
  --levels 20
```

### Massive 实时

```python
data.connect("massive.trade", instruments=["AAPL"])
data.connect("massive.quote", instruments=["AAPL"])
data.connect("massive.aggregate", instruments=["AAPL"], interval="1m")
```

默认 Dataset ID：

```text
market.trade.us_equity.massive.aapl
market.quote.us_equity.massive.aapl
market.ohlcv.us_equity.massive.1m.aapl
```

### Hyperliquid 实时和历史

```python
data.connect("hyperliquid.perpetual.trade", instruments=["BTC"])
data.connect("hyperliquid.perpetual.orderbook", instruments=["BTC"])

data.use(
    "hyperliquid.perpetual.ohlcv.1h",
    instruments=["BTC"],
    start="2026-01-01T00:00:00+00:00",
    end="2026-02-01T00:00:00+00:00",
)
```

默认 Dataset ID：

```text
market.trade.crypto.hyperliquid.perpetual.btc
market.orderbook.crypto.hyperliquid.perpetual.btc
market.ohlcv.crypto.hyperliquid.perpetual.1h
```

## 4. 用户自定义数据

用户自己的 CSV、Parquet 或协议 connector 需要显式 Dataset ID：

```bash
kairospy data add signals.csv --name research.signal --time event_time
kairospy data connect connectors/my_live.py --as research.live_signal
```

内置产品使用 canonical Dataset ID；用户自定义数据使用用户提供的 Dataset ID。

## 5. 文件结构

目标结构：

```text
.kairos/data/
  datasets/
    market/
      orderbook/
        crypto/
          binance/
            spot/
              btc-usdt/
                dataset.json
                data/
                live/
                  default/
                    state.json
                tmp/
  aliases/
    btc_book.ref
  index/
    cache.sqlite3
```

`dataset.json` 是可选说明文件，只影响展示，不影响读取。`index/cache.sqlite3` 只能作为可删除、可重建的缓存，不能作为数据真相来源。
