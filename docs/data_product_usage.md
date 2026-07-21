# Data 产品使用文档

日期：2026-07-21  
适用对象：使用 Kairos 接入、检查、查询、回放历史数据和实时数据的用户

## 1. 你需要理解什么

Data 产品的用户心智只有几个概念：

| 概念 | 含义 |
|---|---|
| Dataset | 一个稳定的数据名字，例如 `features.my_signal` |
| Time | 数据的主时间字段，例如 `date`、`timestamp`、`event_time` |
| Historical | 已经落地、可以查询或回放的历史数据 |
| Live | 当前可以订阅、监控或采样的实时数据 |
| Ready | 这份 Dataset 当前能不能用于 workspace、backtest、shadow、paper、live |
| Account | 实时或受限 provider 需要的账号/凭据引用 |

普通使用不需要理解 release、manifest、hash、source cache、journal、lake path。默认 CLI 输出会隐藏这些内部证据；需要审计时再使用 `data audit --verbose`。

## 2. 推荐入口：Data

Python API 只有一个产品入口：`Data`。接入、管理、检查都从 `Data` 开始；需要消费已经 ready 的 Dataset 时，由 `Data.reader()` 产生读取客户端。

```python
from kairospy.product_surface import Data

data = Data(".kairos/data")

data.add("signals.csv", name="features.my_signal")
data.doctor("features.my_signal")

reader = data.reader()
rows = reader.get("features.my_signal").collect("rows")
```

`kairos.data.toml` 不是 Data 的必经入口。它只是可选的批量清单，适合 CI、项目 bootstrap、团队共享和一次性声明多个 Dataset。

## 3. 接入历史数据

### 3.1 CSV 或 Parquet 文件

Python：

```python
data.add("signals.csv", name="features.my_signal")
```

CLI：

```bash
kairospy data add signals.csv --name features.my_signal
```

如果系统无法识别时间字段，会返回 `needs_time`，并提示可用字段和示例命令：

```bash
kairospy data add signals.csv --name features.my_signal --time trade_day
```

### 3.2 检查状态

```python
doctor = data.doctor("features.my_signal")
print(doctor["status"])
```

```bash
kairospy data doctor features.my_signal
```

## 4. 使用内置数据产品

查看内置产品：

```bash
kairospy data product list
```

Python：

```python
data.use(
    "massive.equity.ohlcv.1d",
    as_dataset="market.ohlcv.equity.us.1d",
    start="2026-01-01T00:00:00+00:00",
    end="2026-02-01T00:00:00+00:00",
    for_use="backtest",
)
```

CLI：

```bash
kairospy data use massive.equity.ohlcv.1d \
  --as market.ohlcv.equity.us.1d \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00 \
  --for backtest
```

## 5. 接入实时数据

Python：

```python
data.connect(
    "binance.orderbook",
    as_dataset="market.orderbook.crypto.binance.btc-usdt",
    account="binance-testnet",
    instruments=("BTCUSDT",),
    market="spot",
    levels=20,
    interval="100ms",
    for_use="paper",
)
```

CLI：

```bash
kairospy data connect binance.orderbook \
  --as market.orderbook.crypto.binance.btc-usdt \
  --account binance-testnet \
  --instrument BTCUSDT \
  --market spot \
  --levels 20 \
  --interval 100ms \
  --for paper
```

如果 freshness、drop、overflow 或 capture 还没达标，`doctor` 会返回 `needs_fix`，并阻止 `paper/live`。

### 5.1 采样实时数据

采样是临时查看 live source 的动作，参数应显式传入，不通过 manifest 隐式解析：

```python
sample = data.sample(
    "binance.orderbook",
    instruments=("BTCUSDT",),
    market="spot",
    levels=20,
    interval="100ms",
    limit=5,
)
```

```bash
kairospy data sample binance.orderbook \
  --instrument BTCUSDT \
  --market spot \
  --levels 20 \
  --interval 100ms \
  --limit 5
```

### 5.2 重连

`reconnect` 只用于恢复已经连接过的 live Dataset，不用于接入新数据：

```bash
kairospy data reconnect market.orderbook.crypto.binance.btc-usdt
```

接入新的 live 数据请使用 `Data.connect` 或 `kairospy data connect`。

## 6. 消费 Dataset

查询和回放使用 Dataset 名字，不需要 release id 或文件路径。

```python
from kairospy.data import OutputFormat

reader = data.reader()
rows = reader.get(
    "features.my_signal",
    start="2026-01-01T00:00:00+00:00",
    end="2026-02-01T00:00:00+00:00",
).collect(OutputFormat.ROWS)
```

不同用途可以有多个 reader，它们共享同一个 Data root，但有不同治理约束：

```python
workspace = data.reader(run_mode="workspace")
backtest = data.reader(run_mode="backtest")
live = data.reader(run_mode="live")
```

CLI：

```bash
kairospy data query features.my_signal --limit 10
kairospy data replay features.my_signal --limit 20
```

## 7. 可选：批量清单

`kairos.data.toml` 是 Data manifest：声明一个项目要准备哪些 Dataset。它适合批量应用，不是单次查询、采样或日常接入的必经入口。

```toml
[datasets.my_signal]
kind = "file"
source = "./signals.csv"
dataset = "features.my_signal"

[datasets.btc_orderbook]
kind = "live"
source = "binance.orderbook"
dataset = "market.orderbook.crypto.binance.btc-usdt"
account = "binance-testnet"
instrument = "BTCUSDT"
market = "spot"
levels = 20
interval = "100ms"
for = "paper"
```

Python：

```python
data.apply("kairos.data.toml")
data.apply("kairos.data.toml", only="my_signal", dry_run=True)
```

CLI：

```bash
kairospy data apply kairos.data.toml
kairospy data apply kairos.data.toml --only my_signal --dry-run
```

## 8. 用户自定义数据协议

Data 支持两类用户协议：

| 协议 | 用途 |
|---|---|
| HistoricalDataProtocol | 批量返回带时间的数据 |
| LiveDataProtocol | 持续推送带时间的数据 |

查看协议类型：

```bash
kairospy data protocol list
```

生成模板：

```bash
kairospy data protocol template --kind historical --output connectors/my_history.py
kairospy data protocol template --kind live --output connectors/my_live.py
```

检查协议文件：

```bash
kairospy data protocol check connectors/my_history.py --kind historical
```

接入用户 historical protocol：

```bash
kairospy data add connectors/my_history.py \
  --name features.vendor_signal \
  --protocol historical
```

接入用户 live protocol：

```bash
kairospy data connect connectors/my_live.py \
  --as market.live_signal \
  --account paper-feed \
  --instrument AAPL
```

## 9. 提升用途等级

刚接入的文件数据通常是 `ready_for_workspace`。如果要用于 backtest，需要显式提升：

```bash
kairospy data promote features.my_signal --for backtest
```

提升后再检查：

```bash
kairospy data doctor features.my_signal
```

## 10. 审计

普通命令默认隐藏内部证据：

```bash
kairospy data describe features.my_signal
kairospy data doctor features.my_signal
kairospy data query features.my_signal
kairospy data replay features.my_signal
```

需要追溯 exact evidence 时使用：

```bash
kairospy data audit features.my_signal --verbose
```

`audit --verbose` 会展开 release id、content hash、manifest path、lineage、source cache、quality report，以及 live freshness evidence。

## 11. 与 Workspace / Run 的边界

Data 负责接入、检查、查询、回放数据。

Workspace 负责绑定已经准备好的 Dataset 和用户代码上下文，不负责重复下载或重复配置 Data。

Run 使用 Dataset 名字消费数据：

```bash
kairospy run start \
  --workspace alpha \
  --mode paper \
  --entrypoint my_strategies.sma_cross:build
```

用户不需要传 LiveViewManifest、journal path、capture segment path。
