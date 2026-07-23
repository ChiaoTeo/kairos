# Data Space 与 Data Product 改造设计

状态：目标设计草案（基于现有 data 资产演进）  
日期：2026-07-23  
适用对象：Data Core、Data Product、Workspace Projection、Run live/paper/backtest 输入体验

本文总结下一版数据系统的产品方向：不是推倒重写当前 `kairospy/data`，而是在现有 DatasetStore、Built-in Data Product、connector、quality、live/acquisition service 等资产上向前推进，把它们整理成两层清晰产品语言。底层 data 代码继续作为 Data Core 的实现底座，负责数据存储与访问；上层 Data Product 继续复用现有 provider 能力、协议和 connector，负责如何从交易所、数据商或用户代码获取和维护数据。

目标变化是用户语言和边界，而不是删除现有 data 系统。用户日常不应该理解 `market.orderbook.crypto.binance.usdm-perpetual.btc-usdt` 这类内部分类路径，也不应该先理解 `binance.orderbook` 这类 connector 能力名。用户应该直接表达自己要的交易数据空间和数据流；现有 Dataset ID、BuiltInDataProduct key 和 canonical contract 应作为兼容层、审计属性和 resolver 输入资产保留下来。

设计约束：

1. 保留当前 `kairospy/data` 目录作为主演进位置，不新建一套平行系统。
2. 保留 DatasetStore 文件树事实源设计，并把它命名和能力逐步推广为 Data Core。
3. 保留 BuiltInDataProductRegistry、DataProductContract、DataProtocolRegistry 和 connector 协议资产，把它们从主 UX 入口降级为 Product Resolver 的内部材料。
4. 新增 Space/Stream 是面向用户的目标语言；旧 Dataset API 和命令需要兼容一段时间。
5. 改造优先采用 adapter、alias、resolver、显示层收敛，避免把已经可用的 provider 和存储代码重写掉。

## 1. 当前痛点

当前项目里已经有两个方向：

1. `DatasetStore` 正在简化成文件树事实源，历史数据放 `data/`，实时状态放 `live/`。
2. `BuiltInDataProduct` 同时承担用户入口、provider 能力注册和 canonical Dataset ID 生成。

问题在第二点。`binance.orderbook`、`hyperliquid.perpetual.ohlcv.1h` 这类名字对实现者清楚，但对用户不直观。用户不是在想“我要调用 Binance orderbook 产品”，而是在想：

```text
我要 Binance BTCUSDT 永续盘口
我要 Hyperliquid BTC 永续资金费率
我要读取 Binance USD-M 多个永续标的的 1h K 线
我要把自己的 momentum 信号写进去供策略使用
```

另一方面，`market.orderbook.crypto.binance.usdm-perpetual.btc-usdt` 这种 Dataset ID 虽然规范，但它把内部分类学暴露给用户。它适合作为迁移兼容或审计属性，不适合作为主产品语言。

因此 Dataset 这个用户侧概念应该从主 UX 里退到兼容和内部身份层，而不是从代码库中删除。新的用户主概念应该是：

```text
Data Space  数据空间
Data Stream 数据流
```

在实现上，第一阶段可以让 `DataStreamId` 映射到现有 `DatasetId`，让 `DatasetStore` 继续负责真实读写；后续再把目录和 API 命名逐步调整到 Space/Stream。这样目标设计可以驱动产品体验，同时保护当前 data 代码里已经沉淀的设计资产。

## 2. 新概念

### 2.1 Data Core

Data Core 是当前 data 存储、读写、索引和文件树契约的产品化边界。它可以先由现有 `DatasetStore`、`DatasetLayout`、`DatasetReader`、`DatasetWriter` 和 `DataApi` 承担，不要求第一版新建平行实现。

Data Core 只管数据，不认识交易所业务。

它负责：

- 创建和解析 Data Space。
- 创建和解析 Data Stream。
- 兼容解析旧 Dataset ID 和 alias。
- 管理固定文件树，并可按需导出审计/备份产物。
- 管理历史数据 `data/`。
- 管理实时状态和捕获 `live/`。
- 读、写、查询、导入用户数据。
- 管理 alias 和 view。
- 做基础 primary_time 和文件可读性校验。

它不负责：

- 调 Binance、Hyperliquid、Massive API。
- 管理 credential。
- 生成 provider subscription。
- 启动 websocket。
- 决定全市场下载计划。
- 做 connector capability registry。

第一版实现原则：

```text
Data Space / Data Stream 是新的用户语言。
DatasetStore / DatasetId 是现有实现资产。
Data Core API 先做兼容包装和命名收敛，再逐步调整内部目录。
```

### 2.2 Data Product

Data Product 只管“怎么得到或维护某个 Data Stream”。它应复用当前 `BuiltInDataProductRegistry`、`DataProductContract`、`DataProtocolRegistry`、historical/live service 和 connector 代码，而不是重写 provider 能力。

它负责：

- 把用户想要的数据解析成产品计划。
- 识别 provider、market、symbol、channel、interval、universe。
- 调 connector 拉历史数据。
- 为 Run 生成实时连接计划。
- 把 realtime capture compact 到 historical `data/`。
- 写入 Data Core。
- 给 CLI 展示 acquisition/live probe plan。

Data Product 不应该成为用户读取数据时必须记住的 ID。它是执行计划，不是数据身份。旧的 product key 仍然保留，作为高级入口、兼容入口和 resolver 可用材料。

### 2.3 存储边界

Data Core 负责存储，Data Product 不负责存储。当前代码里的 provider、protocol、historical acquisition 和 live service 可以继续存在，但最终写入必须收敛到 Data Core/DatasetStore 这一条路径。

更准确地说：

```text
Data Core is the data warehouse and read/write engine.
Data Product is the producer and maintainer.

Product owns source_plan.
Data Core owns storage_plan.
```

Data Core 知道：

```text
space
stream
primary_time
data/live path
append/read/replace/clean
```

Data Product 知道：

```text
用户请求应该落到哪个 space.stream
用哪个 provider/connector
外部 API 怎么请求
历史任务怎么切
实时订阅怎么配
原始字段怎么标准化成目标 stream 的表结构
```

Data Product 可以决定外部怎么取数据、怎么切任务、怎么标准化字段，但它不负责最终落在哪个根目录。它不应该自己拼最终目录，也不应该绕过 Data Core 写文件。历史文件写入、实时状态写入、capture 写入、replace 和 clean 都必须经过 Data Core。现有代码里如果已有直接写目录的 connector 或 pipeline，应作为迁移对象逐步改到 writer/client 接口，而不是删除整块 provider 实现。

Data Core 不负责业务语义层面的去重、冲突处理和 provenance。第一版不要把 `instrument`、`dimensions`、`key`、`source_runs` 这类上层概念塞进 Data Core。Product 如果需要去重，应该先在标准化输出里处理好；Data Core 只接收最终要写入的表或事件文件。

典型关系：

```text
用户: data get binance_swap_btcusdt.ohlcv_1h
  -> Product 解析出 target stream
  -> Product 从 Binance 拉数据
  -> Product 标准化 rows
  -> Data Core append/replace("binance_swap_btcusdt.ohlcv_1h", rows)

用户/策略: data read binance_swap_btcusdt.ohlcv_1h
  -> Data Core read(...)
```

历史数据调用链：

```text
data get binance_swap_btcusdt.ohlcv_1h
  -> Product Resolver
  -> Historical Acquisition Plan
  -> Connector fetch/archive download/paginated REST
  -> normalized rows/table
  -> Data Core writer.append/replace(stream, rows)
```

实时数据主调用链：

```text
run start --config ...
  -> Workspace Projection 声明 binance_swap_btcusdt.orderbook
  -> Product Resolver
  -> Live Source Plan
  -> RuntimeFeedServicePlan
  -> Managed feed service starts websocket
  -> Data Core writes live capture under stream live/
```

实时数据不是 Data Core 的静态配置前置条件。Workspace 声明需要哪个 Stream，Run 启动时才真正连接、订阅、capture、freshness check 和断线重连。

Product 可以传写入 hint，例如推荐 primary time 或时间窗口；Data Core 可以接受、修正或拒绝这些 hint。最终文件根目录属于 Data Core 的契约。

### 2.4 Stream 不强制 Instrument

Data Core 不应该理解 `instrument`。对于交易市场数据，推荐默认存储仍按单个可交易/可分析对象的 Space 隔开；批量读取由 Data Core 的 multi-stream scan/query 实现，而不是要求用户创建一个全市场 Space。

Data Core 真正需要知道的是：

```text
primary_time
stream data root
stream live root
```

因此 Data Product 可以认识 symbol、coin、instrument、series、region；Data Core 只认识 stream、primary_time 和文件根目录。宏观数据、用户因子、账户数据都只是不同 stream，不需要 Data Core 预先理解它们的业务维度。

## 3. 用户侧命名

用户主 ID 使用：

```text
<data_space>.<stream>
```

示例：

```text
binance_swap_btcusdt.orderbook
binance_swap_btcusdt.trades
binance_swap_btcusdt.ohlcv_1m
binance_swap_btcusdt.ohlcv_1h
binance_swap_btcusdt.funding

binance_spot_btcusdt.orderbook
hyperliquid_perp_btc.orderbook
hyperliquid_perp_btc.trades
hyperliquid_perp_btc.funding
hyperliquid_perp_btc.ohlcv_1h

my_research.momentum_1h
my_model.prediction_5m
manual.adjustments
```

这里 `binance_swap_btcusdt` 是 Data Space，`orderbook` 是 Data Stream。

### 3.1 单品种 Space

单品种交易数据：

```text
binance_swap_btcusdt
hyperliquid_perp_btc
massive_equity_aapl
```

它们表示一个用户能自然理解的交易空间。

### 3.2 批量读取不是全市场 Space

默认不要把全市场做成用户必须理解的 Space。交易市场数据优先按一个可交易/可分析对象一个 Space 存储：

```text
binance_swap_btcusdt
binance_swap_ethusdt
binance_swap_solusdt
```

每个 Space 下有自己的 Stream：

```text
binance_swap_btcusdt.ohlcv_1h
binance_swap_ethusdt.ohlcv_1h
binance_swap_solusdt.ohlcv_1h
```

需要全读或批量读时，由 Data Core 做 multi-stream scan：

```python
data.read_many([
    "binance_swap_btcusdt.ohlcv_1h",
    "binance_swap_ethusdt.ohlcv_1h",
])

data.read_pattern("binance_swap_*.ohlcv_1h")
```

这样既不影响用户抽象，也不影响批量研究。全市场下载仍然可以是 Product 的 acquisition strategy：

```text
Product 从外部 full-market archive 下载
  -> 标准化 rows
  -> 按 instrument 拆分
  -> Data Core 分别 append/replace 到各自 Space/Stream
```

如果未来确实需要横截面研究，也可以提供派生集合或查询视图，但它是读取便利或优化，不是默认存储身份。

### 3.3 用户自定义 Space

用户自定义数据不应该被迫套进 `market.xxx.crypto...`。

推荐入口：

```bash
kairospy data create-space my_research --kind research
kairospy data import my_research.momentum_1h ./momentum.parquet --time event_time
```

Python：

```python
data.write(
    "my_research.momentum_1h",
    frame,
    kind="feature",
    primary_time="event_time",
)
```

## 4. 内部存储结构

Data Core 不应该同时维护文件事实源和数据库事实源。既然文件结构固定，就应该把唯一事实源收敛到文件树：

```text
File Tree: 唯一事实源
Index DB: 可删除、可重建的性能缓存
Export/Backup: 显式导出的审计或恢复产物
```

也就是说，Data Core 通过固定目录推导数据身份、data root 和 live root；parquet/jsonl/csv 文件保存真实数据；实时状态放在 stream 的 `live/` 目录下。SQLite 如果存在，只能是 `index/cache.sqlite3`，用于加速 list/inspect/read planning，删除后必须能从文件树重建。

当前 `DatasetStore` 已经满足这个方向：`datasets/` 是事实源，`aliases/` 是轻量引用，`index/cache.sqlite3` 可重建。改造不应该先废弃这套结构。第一阶段应在当前目录上增加 Space/Stream 解析和显示层映射：

```text
<space>.<stream>            用户看到的 stream id
DatasetId                   当前内部读写身份
datasets/<dataset parts>/   当前事实源目录
data/                       历史数据
live/                       实时状态和捕获
```

例如第一阶段可以把：

```text
binance_swap_btcusdt.ohlcv_1h
```

映射到现有 canonical Dataset ID：

```text
market.ohlcv.crypto.binance.usdm-perpetual.btc-usdt.1h
```

也可以先把 stream id 自身作为合法 Dataset ID 写入：

```text
datasets/binance_swap_btcusdt/ohlcv_1h/
```

具体选择由兼容成本决定，但读取、写入、索引和清理仍复用当前 `DatasetStore`。

默认存储结构里不需要强制新增 `space.json`、`stream.json`、`catalog.sqlite3`、`space.snapshot.json` 或 `stream.snapshot.json`。需要审计、迁移、备份或人工查看时，再通过命令显式导出。

长期目标目录可以演进为更显式的 Space/Stream 结构：

```text
.kairos/data/
  spaces/
    binance_swap_btcusdt/
      streams/
        orderbook/
          live/
            default/
              state.json
              capture/
          data/
        ohlcv_1h/
          data/

    binance_swap_ethusdt/
      streams/
        ohlcv_1h/
          data/
            event_day=2026-07-22/
              part-00000.parquet

    hyperliquid_perp_btc/
      streams/
        orderbook/
          live/
        funding/
          data/
          live/

    user/
      my_research/
        streams/
          momentum_1h/
            data/

  tmp/
  product_runs/
  exports/
  index/
    cache.sqlite3
```

但这不是第一阶段必须完成的迁移条件。只要 current `datasets/` 文件树仍是事实源，Space/Stream 可以先是逻辑视图：

```text
Current:
  .kairos/data/datasets/<dataset parts>/data/
  .kairos/data/datasets/<dataset parts>/live/

Target optional:
  .kairos/data/spaces/<space>/streams/<stream>/data/
  .kairos/data/spaces/<space>/streams/<stream>/live/
```

长期 `spaces/` 目录下，Data Core 可以从路径解析：

```text
space id  = spaces/<space>/
stream id = <space>.<stream>
data root = spaces/<space>/streams/<stream>/data/
live root = spaces/<space>/streams/<stream>/live/
tmp root  = tmp/
```

在 `datasets/` 兼容阶段，Data Core 从 resolver/alias 解析：

```text
stream id  = <space>.<stream>
dataset id = resolver.resolve(stream id)
data root  = DatasetStore.data_path(dataset id)
live root  = DatasetStore.live_path(dataset id)
```

可选 index cache 可以保存：

```text
stream id
dataset id
data root
live root
file count
latest mtime
```

但 cache 不是事实源。任何时候都可以删除：

```bash
rm .kairos/data/index/cache.sqlite3
kairospy data index rebuild
```

Export 的用途：

```text
file-tree export
run audit artifact
git diff friendly backup
manual disaster recovery reference
```

Export 不应该作为读取门禁。缺失或损坏 export 不应导致 `data.read(...)` 失败；文件树才是主事实源。

### 4.1 原子写入与崩溃恢复

Data Core writer 应使用 staged write：

```text
1. 创建 tmp/write-<id>/。
2. Product 或 writer 把标准化数据交给 Data Core。
3. Data Core 写 parquet/jsonl 到 tmp。
4. 校验文件可读、primary_time 字段可定位。
5. 原子 rename 到 stream data/capture 目录。
6. 清理 tmp。
```

如果过程中崩溃：

```text
tmp/write-* 可以清理。
已经提交到正式目录的文件就是事实源。
index cache 如果存在，删除后重建。
```

Data Core 需要单 stream 写锁，避免两个 writer 同时 compact/replace 同一个 Stream：

```text
.kairos/data/locks/<stream-hash>.lock
```

第一版用文件锁约束并发写入即可。

### 4.2 文件树校验与清理

文件树是事实源，所以不存在 catalog 和磁盘打架的问题。校验只检查目录和文件本身是否符合 Data Core 约定：

```bash
kairospy data check
kairospy data clean-tmp
kairospy data index rebuild
```

`data check` 只回答：

```text
stream directory shape invalid
data file unreadable
primary_time missing when requested
tmp write directory stale
index cache stale
```

`clean-tmp` 只清理临时目录，不删除正式 `data/`。正式历史数据如果错了，正确做法是删掉对应 stream data 后重新运行 Product：

```bash
kairospy data delete-stream-data binance_swap_btcusdt.ohlcv_1h --start ... --end ...
kairospy data get binance_swap_btcusdt.ohlcv_1h --start ... --end ...
```

如果第一阶段仍使用 `datasets/` 目录，删除命令内部应通过 resolver 找到真实 DatasetStore 路径；不要让用户手动判断当前物理目录是 `datasets/` 还是 `spaces/`。

实时数据由 Run 管理。需要重新连接时重启或恢复对应 Run，而不是先 `data connect`。

### 4.3 历史数据存储

外部历史接口差异很大：

```text
Binance archive: 按天 zip / 按月 zip / full market
Massive: REST 分页 / flat files 全量下载
Hyperliquid: snapshot/history endpoint
IBKR: 单合约分页
用户文件: CSV/parquet/jsonl 导入
```

这些复杂性属于 Data Product。Product 负责 acquisition plan：

```text
tasks
request window
download URL
pagination cursor
rate limit
resume state
raw artifact
normalization
```

进入 Data Core 后必须统一成逻辑 Stream 表：

```text
stream = binance_swap_btcusdt.ohlcv_1h
primary_time = period_start
data/
  year=2026/
    month=01/
      day=01/
        part-00000.parquet
```

目录推荐按年月日递进，但层级缺失不影响任何读取逻辑。Data Core 读取时递归扫描 stream data root。以下布局都合法：

```text
data/part-00000.parquet
data/year=2026/part-00000.parquet
data/year=2026/month=01/day=01/part-00000.parquet
```

Product run metadata 如果需要，可以留在 Product 自己的运行目录或 Run artifact，不进入 Data Core。当前已有 acquisition evidence、quality artifact、provider artifact 的代码可以继续保留；改造目标是清晰标注它们不是 Data Core 的事实源。

### 4.4 交叉数据

数据交叉属于 Product 或上层标准化逻辑，不属于 Data Core。Data Core 不按业务 key 做 merge，也不记录 conflict policy。

典型场景：

```text
Product 先通过外部 full-market archive 获取 Binance USD-M 全市场 1h K 线。
Product 按交易对象拆分并调用 Data Core 写入：
  binance_swap_btcusdt.ohlcv_1h
  binance_swap_ethusdt.ohlcv_1h
```

如果后来单品种 API 又拉了 BTCUSDT，Product 应该选择 replace window 或 append new file。Data Core 提供文件级能力：

```text
append(stream, table)
replace_window(stream, start, end, table)
delete_stream_data(stream)
```

Data Core 不判断 “同一根 K 线是否重复”。如果 Product 想避免重复，它应该在调用 Data Core 前完成。


## 5. UX 目标

### 5.1 Resolve

用户应该能先解释一个数据 ID：

```bash
kairospy data resolve binance_swap_btcusdt.orderbook
```

输出重点是人能看懂的解释：

```text
Data:       binance_swap_btcusdt.orderbook
Space:      binance_swap_btcusdt
Stream:     orderbook
Product:    Binance swap BTCUSDT orderbook
Historical: missing
Live:       available via Product at Run time
```

### 5.2 Prepare Historical

```bash
kairospy data get binance_swap_btcusdt.ohlcv_1h --start 2026-01-01 --end 2026-02-01
kairospy data get binance_swap_ethusdt.ohlcv_1h --start 2026-01-01 --end 2026-02-01
```

`get` 是 Data Product 入口。它会生成 acquisition plan，然后写入 Data Core。即使 Product 选择外部 full-market archive，目标仍然可以是多个 per-instrument Stream。

批量读取走 Data Core：

```bash
kairospy data read 'binance_swap_*.ohlcv_1h' --start 2026-01-01 --end 2026-02-01
```

### 5.3 Live Runtime

```bash
kairospy workspace add funding-arb binance_swap_btcusdt.orderbook
kairospy workspace add funding-arb hyperliquid_perp_btc.orderbook
kairospy run start --config configs/runs/funding-arb-live.toml
```

实时长连接不是 `data` 的主路径。Workspace 只声明需要哪些 live Stream；Run 启动时根据 Workspace Projection 调 Product Resolver，生成 live source plan，并作为 managed service 启动。

`data` 可以提供临时探测命令，用于调试连接和查看前几条事件，但不写长期 live 配置：

```bash
kairospy data probe binance_swap_btcusdt.orderbook --limit 10
kairospy data probe hyperliquid_perp_btc.orderbook --limit 10
```

### 5.4 Inspect

```bash
kairospy data inspect binance_swap_btcusdt.orderbook
```

应该显示用户关心的信息：

```text
Data:       binance_swap_btcusdt.orderbook
Venue:      Binance
Market:     USD-M perpetual
Symbol:     BTCUSDT
Type:       orderbook
Historical: missing
Live:       no active Run capture
Storage:    .kairos/data/spaces/binance_swap_btcusdt/streams/orderbook
Dataset:    market.orderbook.crypto.binance.usdm-perpetual.btc-usdt
```

`Storage` 可以显示当前真实路径：兼容阶段可能是 `.kairos/data/datasets/...`，长期目录可能是 `.kairos/data/spaces/...`。内部 connector/product/canonical metadata 可以在 `--verbose` 或 JSON 中出现，但不应该是主显示。

### 5.5 Workspace

Workspace 不再强制用户给数据取策略短名。默认直接使用 Stream ID。

```bash
kairospy workspace add funding-arb binance_swap_btcusdt.orderbook
kairospy workspace add funding-arb hyperliquid_perp_btc.orderbook
kairospy workspace add funding-arb hyperliquid_perp_btc.funding
```

需要角色绑定时才 alias：

```bash
kairospy workspace alias funding-arb hedge_book binance_swap_btcusdt.orderbook
kairospy workspace alias funding-arb lead_book hyperliquid_perp_btc.orderbook
```

策略简单场景：

```python
book = context.market["binance_swap_btcusdt.orderbook"]
```

策略可迁移场景：

```python
book = context.market["hedge_book"]
```

结论：Workspace alias 是高级能力，不是必需路径。

### 5.6 动态订阅与换标的

一个策略一个 Workspace 跑时，经常需要在运行中换标的或扩展订阅集合。当前项目里的 Workspace 本来就是管理数据绑定的工具，所以设计上不应该否认 Workspace 的数据管理职责。更准确的边界是：持久 Workspace 管理允许使用的数据空间；Run 启动时从 Workspace 生成一个运行时 Workspace Session；策略运行中请求变更的是这个 session 的订阅集合，不是直接改持久 `workspace.json`。

边界：

```text
Persistent Workspace
  管理数据空间、stream、role/alias、默认订阅规则。

Run Workspace Session
  Run 启动时从 Workspace snapshot 派生出来的运行时数据管理对象。
  记录当前实际交易/订阅哪些标的。

Runtime Subscription Manager
  根据 Workspace Session 的订阅请求展开实际 stream，并管理 feed service。
```

例如 Workspace 可以管理 stream 模板：

```text
{space}.orderbook
{space}.ohlcv_1h
```

Run 启动时指定 active streams 或 active spaces：

```bash
kairospy run start --config configs/runs/funding-arb-live.toml \
  --param streams=binance_swap_btcusdt.orderbook,hyperliquid_perp_btc.orderbook
```

运行中变更 active streams：

```bash
kairospy run live streams add --run-id funding-arb binance_swap_solusdt.orderbook
kairospy run live streams remove --run-id funding-arb binance_swap_btcusdt.orderbook
kairospy run live streams set --run-id funding-arb binance_swap_btcusdt.orderbook,hyperliquid_perp_btc.orderbook
```

如果 Workspace 为一个 Space 定义了默认 streams，也可以按 space 变更：

```bash
kairospy run live spaces add --run-id funding-arb binance_swap_solusdt
kairospy run live spaces remove --run-id funding-arb binance_swap_btcusdt
```

内部流程：

```text
1. CLI 或 Strategy 提交 MarketDataSubscriptionRequest。
2. Runtime 把请求写入 OperatorCommand/runtime command bus。
3. Run supervisor 收到 subscription update。
4. Strategy 暂停新订单或进入 safe transition。
5. Runtime 校验请求是否在 Workspace 允许的数据空间/模板范围内。
6. Feed manager 根据请求更新 websocket subscription。
7. Data Core 确认新 stream 文件根目录存在。
8. Workspace Session 更新 MarketView/SubscriptionView。
9. 风控检查新标的限制、精度、账户权限和数据 freshness。
10. Strategy 收到 subscription changed event。
11. 安全检查通过后恢复决策。
```

移除标的时需要安全策略：

```text
pause new orders
cancel open orders or require explicit keep-open policy
optional flatten position
stop feed after order/position policy completes
emit subscription changed event
```

因此换标的可以由策略发起，但不能让策略直接修改持久 Workspace 或直接操作 Data Core。正确链路是：

```text
Strategy -> subscription request -> Runtime -> Workspace Session -> Product/Data Core
```

持久 Workspace 仍然是数据管理工具；Run Workspace Session 是运行时控制面。

## 6. 当前项目改造方案

### 阶段一：把现有 DatasetStore 产品化为 Data Core

不新建一套平行 data core。第一阶段以当前模块为底座：

```text
kairospy/data/ids.py
kairospy/data/layout.py
kairospy/data/storage/store.py
kairospy/data/storage/reader.py
kairospy/data/storage/writer.py
kairospy/data/api.py
```

新增或扩展的核心类型建议放在当前 data 包内，避免形成两个事实源：

```text
DataSpaceId
DataStreamId
DataStreamRef
DataStreamResolver
DataCoreClient 或 DataApi stream-facing facade
```

第一阶段能力：

```python
data.resolve_stream("binance_swap_btcusdt.orderbook")
data.read("binance_swap_btcusdt.ohlcv_1h")
data.append("my_research.momentum_1h", frame, primary_time="event_time")
data.live("binance_swap_btcusdt.orderbook")
```

实现上可以仍然调用：

```text
DatasetStore.resolve(...)
DatasetStore.data_path(...)
DatasetStore.live_path(...)
DatasetReader.read(...)
DatasetWriter.append(...)
DatasetWriter.upsert(...)
```

`DatasetId` 不删除。它保留为内部数据身份、兼容 API 和迁移审计对象。Space/Stream 是新的用户入口和显示语言。

如果后续确实需要 `kairospy/data/core/` 子包，也应该只是把现有 storage/api 代码搬迁或重导出，不是并行重写。

### 阶段二：保留 BuiltInDataProduct，新增 Product Resolver

当前 `BuiltInDataProduct` 不删除。它已经承载 provider、capability、primary_time、protocol_name、canonical Dataset ID 生成和 product alias 等资产。改造目标是让它从主用户入口退到内部执行计划材料。

新增：

```text
kairospy/integrations/data_products/resolver.py
kairospy/integrations/data_products/catalog.py
```

输入：

```text
binance_swap_btcusdt.orderbook
hyperliquid_perp_btc.funding
binance_swap_ethusdt.ohlcv_1h
```

输出：

```text
DataProductPlan
  target_stream
  source_plan
  provider
  connector_protocol
  historical/live capability
  acquisition/live runtime params
```

Resolver 可以复用：

```text
BuiltInDataProductRegistry
built_in_dataset_id(...)
DataProductContract
DataProtocolRegistry
HistoricalDataService
LiveDataService
connector datasets/protocols
```

旧的 `binance.orderbook`、`hyperliquid.perpetual.ohlcv.1h` 等 product key 保留为 hidden/advanced/compatible product key，但不要作为主 UX。旧 key 解析结果应能给出推荐 stream id。

### 阶段三：改 CLI

新增或调整：

```bash
kairospy data resolve <stream-id>
kairospy data inspect <stream-id>
kairospy data get <stream-id> --start ... --end ...
kairospy data probe <stream-id> --limit 10
kairospy data create-space <space-id> --kind ...
kairospy data import <stream-id> <file>
kairospy data write <stream-id>
```

兼容旧命令：

```bash
kairospy data use <old-product-key>
```

旧命令输出提示：

```text
Resolved legacy product key to stream: binance_swap_btcusdt.orderbook
```

保留当前 `data use`、`data connect`、`data product list/doctor`、`data protocol`、`data sample` 的实现价值。第一阶段可以让新命令调用旧 service，再把输出渲染成 Space/Stream 语言。

### 阶段四：Workspace Projection 改用 DataStream

当前 `WorkspaceProjection` 已经有 attachment/node/preflight 结构，可以最小改造：

- `dataset` 字段兼容保留。
- 新增 `stream` 字段作为主字段。
- `workspace add` 默认使用 stream id 作为 node name。
- alias 只作为可选 role binding。
- 支持模板 stream，例如 `{space}.orderbook`。
- preflight 按文件树检查 historical/live availability。

### 阶段五：Run live/paper/backtest 统一消费 Stream

Run 不应该读取 BuiltInDataProduct。Run 读取 WorkspaceProjection 中的 Stream：

```text
backtest -> stream.data/ replay
paper/live -> Product live source plan -> managed service -> stream.live/ capture
simulation/shadow -> 可用 historical 或 live mirror
```

实时服务登记到现有 `RuntimeFeedServicePlan`。CLI 展示和策略消费同一个 runtime state。

订单侧保持 durable outbox：CLI 下单和策略下单都写同一个 command/outbox/order state。

### 阶段六：Run Subscription Set 与 Subscription Manager

新增 Run runtime 能力：

```text
RunSubscriptionSet
RuntimeSubscriptionManager
SubscriptionChangedEvent
SubscriptionUpdateCommand
```

CLI：

```bash
kairospy run live streams show --run-id <run-id>
kairospy run live streams add --run-id <run-id> <stream-id>
kairospy run live streams remove --run-id <run-id> <stream-id>
kairospy run live streams set --run-id <run-id> <stream-id>...

kairospy run live spaces show --run-id <run-id>
kairospy run live spaces add --run-id <run-id> <space-id>
kairospy run live spaces remove --run-id <run-id> <space-id>
kairospy run live spaces set --run-id <run-id> <space-id>...
```

这些命令不直接改 Workspace，也不直接改 Data Core。它们提交 operator command，由 Run supervisor 安全执行。

## 7. 验收条件

### Data Core

- 可以创建 `binance_swap_btcusdt` space。
- 可以创建 `binance_swap_btcusdt.orderbook` stream。
- 现有 `DatasetStore`、`DatasetReader`、`DatasetWriter` 测试继续通过。
- 旧 Dataset ID 可以继续读取、写入、inspect 和 alias。
- 新 stream id 可以解析到 Dataset ID 或直接作为 Dataset ID 读写。
- 可以写入和读取 `my_research.momentum_1h`。
- 可以写入和读取没有 `instrument` 字段的 `macro_us.fed_funds_rate`。
- Data Core 只要求固定 stream 文件根目录，读取时可指定或推断 `primary_time`。
- 可以用 `read_many` 或 pattern scan 批量读取多个 per-instrument Stream。
- index cache 删除后可以从文件树重建。
- `data inspect` 不要求任何 provider 存在。
- `.kairos/data/datasets/` 兼容阶段仍可作为事实源；切换到 `.kairos/data/spaces/` 不是阶段一验收前置条件。

### Data Product

- `data resolve binance_swap_btcusdt.orderbook` 能解析出 Binance swap BTCUSDT orderbook plan。
- `data resolve hyperliquid_perp_btc.funding` 能解析出 Hyperliquid BTC funding plan。
- 现有 BuiltInDataProductRegistry 继续可列出、doctor 和 resolve 旧 product key。
- 旧 product key 可以返回推荐 stream id 和兼容 dataset id。
- `data get binance_swap_btcusdt.ohlcv_1h` 可以选择单品种 API 或外部 full-market archive 策略，并在 plan 里说清楚。
- 外部 full-market archive 获取后能按 instrument fanout 写入多个 per-instrument Stream。
- `data probe binance_swap_btcusdt.orderbook --limit 10` 能临时连接并输出样例事件，不写长期 live 配置。
- Product 只能调用 Data Core writer，不能直接写最终 stream 目录。
- Product run metadata 如果需要，保存在 Product/Run 自己的 artifact 中，不进入 Data Core。
- 当前 Binance、Hyperliquid、Massive、IBKR 等 connector 资产继续复用；迁移只改变 resolver 和写入边界。

### Workspace

- `workspace add alpha binance_swap_btcusdt.orderbook` 不要求 `--name`。
- `workspace alias alpha hedge_book binance_swap_btcusdt.orderbook` 可选。
- Workspace 可以管理 stream template，例如 `{space}.orderbook`。
- `workspace inspect alpha` 展示 stream、role、historical/live 状态。
- `workspace inspect-code` 的 preflight 按 stream view 检查 backtest/live 是否可用。

### Run

- backtest 读取同一个 stream 的 `data/`。
- live/paper 读取同一个 stream 的 `live/` 并能 supervision。
- live/paper 根据 Workspace Projection 自动启动 live feed，不要求用户先 `data connect`。
- Run 可以启动时指定 active streams，例如 `streams=binance_swap_btcusdt.orderbook,hyperliquid_perp_btc.orderbook`。
- Run status 显示当前 active spaces、active streams 和 active subscriptions。
- `run live streams/spaces add/remove/set` 通过 operator command 更新 Run Workspace Session，不直接修改持久 Workspace。
- Strategy 可以提交 market data subscription request，由 Runtime 审批和执行。
- add stream/space 后新 feed 启动，Data Core 对应 stream live/capture 路径出现。
- remove stream/space 前触发 pause-new-orders/cancel/flatten policy。
- Strategy 收到 subscription changed event。
- CLI status 和策略看到同一个 feed service/freshness/runtime state。
- CLI 下单和策略下单进入同一个 durable outbox。

## 8. 设计原则

1. 用户 ID 表达交易直觉，不表达数据库范式。
2. 内部文件结构可以规范，但不应成为主 UX。
3. Data Core 不认识 provider。
4. Data Product 不拥有数据身份，只拥有获取计划。
5. 默认一个可交易/可分析对象一个 Data Space；全市场是获取策略或批量读取能力，不是默认存储身份。
6. 用户自定义数据是一等公民，不套交易所命名。
7. Workspace alias 可选，不是必需概念。
8. Run 只消费冻结后的 WorkspaceProjection，不直接依赖 Product key。
9. Product owns source_plan，Data Core owns storage_plan。
10. Data Core 不强制 instrument、key、dimensions 或 schema，只强制 primary_time 和 stream 文件根目录。
11. 外部接口切分方式不进入 Stream ID。
12. 数据交叉由 Product 处理；Data Core 只提供 append/replace/delete/read。
13. Workspace 是持久数据管理工具，Run Workspace Session 是运行时数据控制面。
14. 换标的可以由策略或 CLI 发起请求，但必须通过 Runtime 审批和执行，不能直接修改持久 Workspace。
15. 当前 `kairospy/data` 代码是设计资产，不是待删除包；优先通过 facade、resolver、alias、rendering 和 service 边界演进。
16. Dataset ID 是兼容和内部身份资产；Space/Stream 是新的用户产品语言。
17. Built-in product key 是兼容和 provider 能力资产；Product Resolver 是新的用户请求解释层。
18. 第一阶段不要求目录从 `datasets/` 迁到 `spaces/`；目录迁移必须可选、可回滚、可审计。
