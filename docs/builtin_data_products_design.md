# 内置数据产品设计边界

日期：2026-07-22  
状态：设计约定，待逐步落地  
适用对象：维护 Kairos 内置 Data Product、Provider Connector、Dataset 写入链路的工程师

补充：Data 文件结构契约、JSON 门禁删除/降级和 `DatasetStore` 迁移方向见 [Data 文件结构与读取体验设计草案](data_store_simplification_design.md)。

## 1. 背景

内置数据产品不是临时 downloader，也不应该通过 JSON/YAML 堆字段来模拟产品能力。它们是 Kairos 自带的稳定数据产品线，需要在代码里明确：

- 产品 key；
- selector 参数；
- canonical Dataset ID 映射；
- provider connector 调用方式；
- 历史数据写入方式；
- 实时数据连接方式。

内置产品不做用户自定义 Dataset 命名，只维护单一 Dataset 数据目录，不做 JSON 门禁。

## 2. 目标

内置 Data Product 至少要成熟处理四件事：

| 能力 | 含义 |
|---|---|
| Dataset 映射 | 根据 product key 和 selector 生成 canonical Dataset ID |
| 标的选择 | 支持按 instrument 裁剪、查询和采集，但不把一次 instrument selection 变成新的产品身份 |
| 增量写入 | 能基于已有数据文件推断可补范围，并 append/upsert 到同一个 Dataset |
| 物理布局 | 每个内置产品自行决定 `data/` 下的分区层级，Reader 对用户隐藏这些层级 |

此外，不同标的类型要有明确处理方案，包括现货、永续合约、交割合约和期权。

## 3. 核心边界

### 3.1 Dataset Product 定义可进入范围

`dataset` 是产品身份，不是一次下载选择，也不是运行环境选择。

例如：

```text
market.ohlcv.equity.us.massive.1h.adjusted
```

表达的是 Massive US equity adjusted 1h OHLCV 这个产品。它可以长期接纳 NVDA、AAPL 或全市场数据。`equity:us:NVDA` 只是一次 acquisition selection，不应进入 dataset key。

推荐心智：

```text
Dataset Product 定义什么数据可以属于这里
Acquisition Selection 定义这次拉哪些成员
Dataset Data 定义已经落地了哪些数据文件
```

### 3.2 内置产品不允许 as_dataset

内置产品的 Dataset ID 必须由系统生成：

```python
data.connect(
    "binance.orderbook",
    instruments=["BTCUSDT"],
    market="spot",
)

# market.orderbook.crypto.binance.spot.btc-usdt
```

不支持：

```python
data.connect(
    "binance.orderbook",
    as_dataset="market.orderbook.crypto.binance.btc-usdt",
    instruments=["BTCUSDT"],
    market="spot",
)
```

原因很直接：内置产品的价值就是稳定命名、稳定结构、稳定读取。如果允许用户改 Dataset 名，产品目录会失去一致性。

如果用户需要短名：

```python
data.alias("market.orderbook.crypto.binance.spot.btc-usdt", "btc_book")
```

alias 是读取便利，不改变 Dataset 的真实身份。

### 3.3 目录不承载产品语义以外的身份

物理目录应该稳定表达产品，不应该把一次 selection 伪装成产品层级。`data/` 下面的分区层级由内置产品自己决定。

推荐：

```text
.kairos/data/datasets/market/ohlcv/equity/us/massive/1h/adjusted/
  dataset.json
  data/
    event_day=2026-01-02/
      part-00000.parquet
```

不同产品可以选择不同物理布局：

```text
data/
  event_day=2026-01-02/
    part-00000.parquet

data/
  event_day=2026-01-02/
    event_hour=14/
      part-00000.parquet

data/
  event_day=2026-01-02/
    instrument_bucket=4f/
      part-00000.parquet

data/
  event_month=2026-01/
    part-00000.parquet
```

如果数据量需要按标的分散目录，优先使用 bucket，而不是直接为每个 instrument 创建高基数目录。

`instrument_id` 仍然作为列存在，并进入 primary key、merge key 和 query filter。

## 4. 内置产品的代码组织

### 4.1 不使用 JSON/YAML 定义内置产品

内置产品定义应留在 Python 代码里。配置文件只负责：

- provider credential 引用；
- endpoint、rate limit、环境覆盖；
- 项目级默认路径；
- 外部 extension 的声明。

不建议把内置产品写成：

```yaml
products:
  - key: market.ohlcv.equity.us.massive.1h.adjusted
    storage_layout: ...
    incremental: ...
```

这会把产品能力拆散到不可检查的配置里，最后又回到“契约很多，但真正行为还是代码遥控”的问题。

### 4.2 推荐模块结构

```text
kairospy/data/products/
  __init__.py
  registry.py
  binance.py
  massive.py
  hyperliquid.py
```

每个 provider 模块导出产品定义：

```python
Product(
    key="hyperliquid.perpetual.ohlcv.1h",
    mode="historical",
    connector="hyperliquid",
    dataset=lambda s: DatasetId("market.ohlcv.crypto.hyperliquid.perpetual.1h"),
)
```

实时产品：

```python
Product(
    key="binance.orderbook",
    mode="realtime",
    connector="binance",
    dataset=lambda s: DatasetId(
        f"market.orderbook.crypto.binance.{s.market}.{normalize_symbol(s.instrument)}"
    ),
)
```

这里的 `mode` 只是 product 调用类型，不写入 Dataset 身份。

## 5. 写入与查询约定

### 5.1 OHLCV

推荐字段：

```python
columns = (
    "venue",
    "instrument_id",
    "provider_symbol",
    "period_start",
    "period_end",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
```

推荐写入约束：

```python
merge_key = ("venue", "instrument_id", "period_start", "interval")
order_by = ("period_start", "instrument_id")
```

物理布局由产品自己决定。例如，普通小时线可以按日，低频数据可以按月，高基数数据可以加 bucket：

```python
storage_layout = ("event_day",)
storage_layout = ("event_month",)
storage_layout = ("event_day", "instrument_bucket")
```

选择依据是数据量、查询模式、重写成本和文件数量，不作为所有产品的默认要求。

### 5.2 增量写入 helper

增量下载需要的是 helper 约定，不是全局契约对象：

```python
watermark_field = "period_start"
complete_until_field = "latest_complete_period_end"
overlap = "1 period"
merge_key = ("venue", "instrument_id", "period_start", "interval")
correction_policy = "replace_by_merge_key"
```

增量流程：

1. 从已有数据文件推断已有时间范围。
2. 计算需要补的 ranges。
3. 对最后一段窗口追加 overlap。
4. 下载 selection 对应数据。
5. 按 merge key 合并旧数据和新数据。
6. append 或 upsert 回同一个 Dataset 的 `data/`。
7. 可选写入 `source.json`，用于展示本次采集参数。

这只是对同一个 Dataset 的数据补齐，不产生新的 Dataset。

### 5.3 最小可读性约定

不设置质量门禁，但内置产品 builder 应尽量写出统一、好读、好合并的数据：

- 数据非空时才写入；
- primary key 不重复；
- primary time 有 timezone；
- period_start / period_end 边界正确；
- OHLCV 数值满足基本域约束。

这些属于 builder 的正常输出约定。它们不要求单独生成 quality JSON，也不阻塞 reader 读取已有数据文件。

## 6. 标的类型处理方案

### 6.1 现货

适用：股票现货、Crypto spot。

推荐主键：

```text
instrument_id, period_start, interval
```

重点：

- `instrument_id` 使用内部稳定身份，不直接依赖 provider symbol。
- provider symbol 由 product/connector 做映射；如需追踪，可写入可选 `source.json` 或 reference mapping。
- 股票需要处理 symbol rename、退市、split、dividend。
- Crypto spot 需要处理 venue symbol 变更、quote asset、base asset。

现货 OHLCV 可以先共享普通 `ohlcv` helper，不需要单独抽象 spot policy。

### 6.2 永续合约

适用：Binance USD-M perpetual、Hyperliquid perpetual。

推荐主键：

```text
instrument_id, period_start, interval
```

重点：

- `instrument_id` 表达稳定合约，例如 `crypto:perp:binance:BTCUSDT` 或内部规范化 ID。
- funding、mark price、index price 不要硬塞进 OHLCV，应创建独立 Dataset。
- 永续不需要 expiry 维度。

推荐产品：

```text
market.ohlcv.crypto.binance.usdm-perpetual.1h
market.funding.crypto.binance.usdm-perpetual
market.ohlcv.crypto.hyperliquid.perpetual.1h
market.funding.crypto.hyperliquid.perpetual
```

### 6.3 交割合约

适用：到期 futures。

重点：

- `instrument_id` 必须包含 expiry。
- continuous contract 是 curated view，不是原始合约数据。
- 原始 futures 和 continuous futures 应分成不同 Dataset。

推荐产品：

```text
market.ohlcv.future.cme.es.1h.raw
market.ohlcv.future.cme.es.1h.continuous.front
```

### 6.4 期权

适用：OPRA / vendor option data。

重点：

- option contract 高基数，必须避免每个 contract 一个 Dataset。
- `instrument_id` 应包含 underlying、expiry、strike、right。
- chain/reference 数据与 quote/trade/greeks 分开。

推荐产品：

```text
market.ohlcv.option.us.massive.1h.raw
market.quote.option.us.opra.1m
market.greeks.option.us.opra.1m
reference.chain.option.us.opra
```

## 7. 何时创建新的 Dataset Product

不要因为用户只下载了一个 instrument 就创建新的 Dataset Product。

应该创建新产品的情况：

- schema 不同；
- view 不同，例如 raw、vendor_adjusted、internally_adjusted；
- lifecycle 语义不同；
- curated 逻辑不同，例如 continuous future；
- SLA、owner 或用途不同；
- 数据源语义不同，无法通过 product/connector 映射表达。

不应该创建新产品的情况：

- 这次只下载了 NVDA；
- 这次只补了 2026-01-02；
- 这次只选择了两个 OPRA option contracts；
- 这次因为 provider limit 分批下载。

这些都属于 acquisition selection 或数据覆盖范围，不属于 Dataset ID。

## 8. 落地路线

### 阶段一：先固化 DatasetStore 文件结构

优先落地：

- Dataset ID 到 `.kairos/data/datasets/...` 的路径映射；
- `data/` 历史数据目录；
- `live/` 实时数据目录；
- `aliases/*.ref`；
- reader 从文件结构直接读取 parquet/csv；
- 旧 catalog JSON fallback。

这一步收益最高：用户读取体验先变简单，JSON 说明文件不再卡住数据可用性。

### 阶段二：固化写入与增量 helper

把当前 intraday / OHLCV 写入中的重复约束收口到 helper：

- primary key merge；
- time partition；
- optional instrument bucket；
- overlap merge；
- compact。

helper 应该服务当前产品，不要变成通用插件框架。

### 阶段三：按 provider 接入内置产品

迁移顺序建议：

1. Binance realtime：已有基础，最快验证新 `live/` 体验。
2. Massive realtime：已有 websocket，补产品注册即可。
3. Hyperliquid historical：新 connector，先验证 `data/` 写入。
4. Hyperliquid realtime：接入 `live/`。

### 阶段四：重复出现后再抽象 policy

只有当同一类逻辑在多个产品模块中重复出现，并且 helper 已经不足以表达时，才引入更明确的类。

可以接受的后续抽象包括：

- `OhlcvIncrementalSpec`
- `PartitioningSpec`
- `ProductSelector`

暂不需要：

- 全局 `DataProduct` 基类；
- 插件式 policy registry；
- 用配置解释所有内置产品行为；
- 为现货、永续、交割、期权提前建立完整继承树。

## 9. 测试要求

内置产品成熟度应由测试保证，而不是由抽象层级保证。

每个内置产品至少覆盖：

- product 能注册进 product registry；
- selector 能生成稳定 canonical Dataset ID；
- bounded acquire 不创建新 dataset key；
- acquire 能写入目标 Dataset 的 `data/`；
- realtime connect 能维护目标 Dataset 的 `live/`；
- 增量 acquire 能基于旧数据 merge，且 primary key 不重复；
- dry-run / max-requests / max-instruments 能阻止危险下载；
- query 能按 time 和 instrument filter 读到正确数据。

高风险产品额外覆盖：

- 股票 symbol rename / delisting；
- 期货 expiry / continuous contract 边界；
- 期权 chain 高基数和 explicit contract selection；
- perpetual funding / mark price 与 OHLCV 的产品边界。

## 10. 判断标准

这套设计的判断标准不是“抽象是否优雅”，而是：

- 用户看到稳定 Dataset ID，不需要自己命名内置产品产物。
- 用户不需要理解 catalog JSON，也不会因为说明文件缺失读不了数据。
- Dataset 是单一数据目录。
- 增量补数不会因为 partial selection 创建新 Dataset。
- 物理目录能支持时间裁剪和大规模标的。
- 新增一个内置产品时，优先写清楚 product key、Dataset ID mapping、layout 和 writer helper，而不是新增配置解释逻辑。

一句话：

```text
内置产品要代码化、产品化、可读；但暂时不要框架化，也不要把说明文件做成门禁。
```
