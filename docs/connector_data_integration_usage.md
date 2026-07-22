# Provider 接入与 Data Product 使用手册

状态：当前 Data Store 版本  
日期：2026-07-22

Data 产品现在只围绕三件事：

1. Dataset ID 到文件结构的映射。
2. `data/` 历史数据和 `live/` 实时视图的读写。
3. 内置 Data Product 到 canonical Dataset ID 的映射。

旧的 release、publish、quality gate、audit gate、用户自定义内置 Dataset 名称设计已经移除。

## 用户入口

历史数据：

```bash
kairospy data add signals.csv --name research.signal --time event_time
kairospy data use hyperliquid.perpetual.ohlcv.1h \
  --instrument BTC \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00
kairospy data query market.ohlcv.crypto.hyperliquid.perpetual.1h --limit 10
```

实时数据：

```bash
kairospy data connect binance.orderbook --instrument BTCUSDT --market spot --levels 20
kairospy data connect massive.trade --instrument AAPL
kairospy data connect hyperliquid.perpetual.orderbook --instrument BTC
kairospy data alias market.orderbook.crypto.binance.spot.btc-usdt btc_book
kairospy data metadata btc_book
```

代码侧：

```python
from kairospy.data import DataApi

data = DataApi(".kairos/data")
data.connect("binance.orderbook", instruments=["BTCUSDT"], market="spot")
book = data.live("market.orderbook.crypto.binance.spot.btc-usdt")

data.use(
    "hyperliquid.perpetual.ohlcv.1h",
    instruments=["BTC"],
    start="2026-01-01T00:00:00+00:00",
    end="2026-02-01T00:00:00+00:00",
)
bars = data.read("market.ohlcv.crypto.hyperliquid.perpetual.1h")
```

内置 Data Product 不接受 `--as` 或 `as_dataset` 指定新名字。短名字只通过 alias 管理。

## 文件结构

Dataset Store 的文件树是 source of truth：

```text
.kairos/data/
  datasets/
    market/
      ohlcv/
        crypto/
          hyperliquid/
            perpetual/
              1h/
                dataset.json
                data/
                  event_day=2026-01-01/
                    part-00000.parquet
      orderbook/
        crypto/
          binance/
            spot/
              btc-usdt/
                dataset.json
                live/
                  default/
                    state.json
                    capture/
  aliases/
    btc_book.ref
  index/
    cache.sqlite3
```

`dataset.json`、`source.json`、`reader.json` 都是可选说明文件，不能作为读取门禁。Reader 递归扫描 `data/` 下的 parquet/csv 文件并返回一张逻辑表。

`index/cache.sqlite3` 是可删除缓存，可以用 `kairospy data repair-index` 从文件结构重建。

## 内置产品

当前产品映射：

| Product key | 能力 | Canonical Dataset ID |
|---|---|---|
| `binance.orderbook` | realtime | `market.orderbook.crypto.binance.<market>.<symbol>` |
| `binance.quote` | realtime | `market.quote.crypto.binance.<market>.<symbol>` |
| `massive.trade` | realtime | `market.trade.us_equity.massive.<symbol>` |
| `massive.quote` | realtime | `market.quote.us_equity.massive.<symbol>` |
| `massive.aggregate` | realtime | `market.ohlcv.us_equity.massive.<interval>.<symbol>` |
| `hyperliquid.perpetual.trade` | realtime | `market.trade.crypto.hyperliquid.perpetual.<coin>` |
| `hyperliquid.perpetual.orderbook` | realtime | `market.orderbook.crypto.hyperliquid.perpetual.<coin>` |
| `hyperliquid.perpetual.funding` | historical + realtime | `market.funding.crypto.hyperliquid.perpetual.<coin>` |
| `hyperliquid.perpetual.ohlcv.1m` | historical + realtime | `market.ohlcv.crypto.hyperliquid.perpetual.1m` |
| `hyperliquid.perpetual.ohlcv.1h` | historical | `market.ohlcv.crypto.hyperliquid.perpetual.1h` |

## Connector 边界

Provider connector 负责供应商交互、认证参数、symbol 映射和原始事件转换。它不负责 Dataset 命名，也不发布 release。

历史采集写入流程：

```text
DataApi.use / kairospy data use
  -> BuiltInDataProductRegistry
  -> provider historical client
  -> DatasetWriter.append/upsert
  -> datasets/<dataset-id>/data/
```

实时采集配置流程：

```text
DataApi.connect / kairospy data connect
  -> BuiltInDataProductRegistry
  -> LiveDataProtocol.runtime_config
  -> datasets/<dataset-id>/live/default/state.json
```

实时 capture 如需沉淀为历史数据，由产品自己的 writer 策略把 `live/default/capture/` compact 到 `data/`。

## 维护命令

```bash
kairospy data list
kairospy data metadata <dataset-or-alias>
kairospy data diagnostics
kairospy data repair-index
kairospy data clean-tmp [--dataset <dataset-or-alias>]
```

这些命令只能辅助展示、诊断和维护文件结构，不能因为缺少说明 JSON、hash、行数、quality 或 audit 文件而阻止读取。
