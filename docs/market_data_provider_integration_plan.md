# Massive 主数据源接入与市场数据治理方案

## 1. 文档目的

本文定义 Kairos 接入外部研究数据源时的供应商选择、目标架构、数据契约、模块边界、实施阶段和验收标准。当前决策是：

- Massive 作为美股、指数、SPX/SPXW 和美股期权研究的首期主数据源；
- 供应商原始格式在 Source 层隔离，在 Canonical 层汇合；
- research、feature 和 backtest 不直接依赖任何供应商 SDK 对象。

本文不是供应商 API 的调用示例，而是项目后续实现、评审和数据使用的长期约束。

本文基于项目现有的统一事实模型和四层数据湖：

```text
InstrumentDefinition + ListingDefinition + Catalog
                         |
              normalized market events
                         |
        research / backtest / risk / execution
```

```text
data/source -> data/canonical -> data/features -> data/studies
```

Massive 必须接入上述边界，不建立 Massive 专用的策略、回测或身份模型。其他供应商不在当前计划范围内；未来如需评估，应另建 ADR 和实施方案。

## 2. 范围与非目标

### 2.1 首期范围

- 使用 Massive REST API 和 Flat Files 获取美股、指数和期权研究数据；
- 首个业务切片聚焦 SPX/SPXW，并用少量美股期权验证通用性；
- 保存 REST 原始响应或原始 Flat Files 及完整请求回执；
- 导入历史 option contracts、ticker events、splits、dividends、exchanges、conditions 和交易日历；
- 将 Quotes、Trades、Aggregate Bars 和 Option Chain Snapshot 转换为内部 canonical 数据；
- Massive vendor Greeks、IV 和 open interest 只作为带来源的观测值，内部定价和曲面继续独立计算；
- 使用 `available_time` 驱动确定性历史重放；
- 为成本、完整性、映射、时间、重复和序列建立质量门禁；
- 让现有 feature、study 和 backtest 只通过内部 Dataset ID 消费数据。

### 2.2 首期非目标

- 不在第一阶段接入 Massive WebSocket；
- 不接入其他新增市场数据供应商；
- 不让研究 Notebook 直接调用 Massive SDK；
- 不将 Massive ticker 作为永久内部身份；
- 不使用连续期货代码表示实际可成交合约；
- 不用供应商数据对象替代现有领域模型。

## 3. 标准量化市场数据链路

标准数据链路应将供应商事实、内部事实、派生数据和研究结果分开：

```text
Provider raw data
       |
       v
Immutable source archive
       |
       v
Decode and transport validation
       |
       v
Point-in-time reference and identity mapping
       |
       v
Canonical market events
       |
       v
Quality, deduplication, correction and ordering
       |
       v
Curated bars, quotes, books and snapshots
       |
       v
Point-in-time-safe features
       |
       v
Frozen study datasets and deterministic backtests
```

各层职责如下：

| 层 | 内容 | 主要约束 |
|---|---|---|
| Source | REST JSON、Flat Files、请求参数和下载回执 | 不可变、可重新转换、带哈希 |
| Reference | Definition、symbol mapping、交易日历、公司行为 | 带有效时间，禁止用当前状态覆盖历史 |
| Canonical | Trade、Quote、Book、Bar 等内部市场事实 | 与供应商 SDK 解耦，类型和单位明确 |
| Curated | 清洗和聚合后的 bars、NBBO、book snapshots | 记录规则、输入版本和质量结果 |
| Features | 波动率、流动性和通用因子 | point-in-time safe，不保存未来标签 |
| Studies | 标签、样本切分、参数、回测和报告 | 冻结输入数据哈希，允许复现 |

## 4. 当前系统能力与缺口

### 4.1 可以直接复用的能力

项目当前已有以下正确边界，应扩展而不是替换：

- `kairos.domain` 中统一的 `InstrumentDefinition`、Quote、Trade、Bar 和 OrderBook 类型；
- `kairos.reference` 中内部 `InstrumentId` 与外部 listing 的映射；
- `kairos.data` 中 Dataset Catalog、canonical pipeline 和 metadata；
- `kairos.storage` 中原子写入、SHA-256、coverage 和 manifest；
- `kairos.research_platform` 中 snapshot、series capture、追加式 session 和质量问题；
- `kairos.backtest` 中确定性 feed、point-in-time universe、审计哈希和 replay；
- UTC、ISO 8601、`[start,end)` 和 `available_time` 数据湖规范；
- source、canonical、features、studies 四层目录所有权。

### 4.2 接入前必须补齐的缺口

#### 多时间语义

当前主要市场类型通常只有一个 `event_time`。接入 Massive 后至少需要区分：

- `event_time`：交易所在业务上产生事件的时间；
- `receive_time`：供应商收到事件的时间；
- `available_time`：本系统允许策略最早看到事件的时间；
- `ingested_at`：本系统下载或写入数据的时间。

研究归属通常使用 `event_time`，但回测可见性必须使用 `available_time`。

#### Point-in-time symbology

Massive ticker、OCC option ticker、交易所 symbol、连续合约 symbol 和内部 `InstrumentId` 不能相互替代。股票会改名，期货会滚动，期权有明确生命周期。映射必须带有效时间。

#### 高频存储和读取

当前按月 CSV 和单个 `dataset.json` 适合低频研究或小型快照，不适合全市场期权 Quotes/Trades。Canonical 数据需要 Parquet，回测需要流式 scan，不能把整个历史数据集一次装入内存。

#### 交易日历

部分现有 coverage 使用 `24x7`。交易所数据必须理解 session、夜盘、DST、half day、auction、halt 和 futures rollover，否则会错误判断缺口。

#### 修正、撤单和乱序

供应商可能发布重复、修正或撤销事件。系统必须同时定义不可变原始事实和 canonical current view，不能简单按时间排序后将所有记录视为独立成交。

#### Dataset Catalog 扩展性

当前 `DataCatalog` 以少量硬编码研究数据集为主。接入多个供应商、端点和 symbol universe 后，应迁移到声明式 registry，避免为每个下载窗口增加代码常量。

## 5. 供应商能力与内部模型的映射

### 5.1 供应商选择

| 研究需求 | 主数据源 | 原因 |
|---|---|---|
| SPX/SPXW 和美股期权链研究 | Massive | Chain Snapshot 可提供合约、quote、trade、vendor Greeks、IV 和 OI |
| 美股、指数和期权历史 bars/trades/quotes | Massive | REST 与 Flat Files 接入直接，适合首期研究闭环 |
| 股票 reference、公司行为和市场状态 | Massive | Ticker、ticker events、splits、dividends、exchanges、conditions 和 calendar 接口完整 |

Massive 的便利不能替代 point-in-time 治理。当前 Option Chain Snapshot 适合实时或当前链采集；历史回测必须使用有明确历史时间语义的 Quotes、Trades、Aggregates 或 Flat Files。不得将今天取得的 chain snapshot 回填为历史链。

### 5.2 Endpoint、原始资产与内部 Dataset ID

以下概念必须分开：

| 概念 | 含义 |
|---|---|
| Massive REST endpoint | 按需查询的 API 资源，如 contracts、quotes、trades、aggregates、snapshot |
| Massive Flat File | 供应商提供的批量历史原始资产 |
| Kairos Dataset ID | 一个经过版本控制、可追溯、可复现的内部数据资产 |

内部 Dataset ID 不复制 URL、文件名或供应商套餐名称。它表达 canonical 语义和版本；endpoint、查询参数、分页、Flat File key 和供应商版本记录在 lineage 中。

### 5.3 Massive 数据选择原则

| 研究目标 | 首选 Massive 资源 | 备注 |
|---|---|---|
| 合约发现和生命周期 | Options Contracts | 必须按历史日期和有效状态处理 |
| 当前期权曲面探索 | Option Chain Snapshot | Vendor Greeks/IV/OI 只作观测和交叉验证 |
| 可执行期权回测 | Historical Quotes | 入场和退出使用 bid/ask，保留 condition 和 SIP 时间 |
| 成交行为分析 | Historical Trades | 不能把成交价直接当成可执行双边报价 |
| 低频方向和波动研究 | Aggregate Bars | 明确 bar 完成和最早可见时间 |
| 大批量历史研究 | Flat Files | 优先保留供应商原始文件后批量转换 |
| 标的与指数对齐 | Stocks/Indices Quotes、Trades、Aggregates | 标的和期权必须采用一致的可见时间规则 |

对于 SPXW，历史期权 Quotes 是可执行行情事实；`I:SPX` 是不可交易指数点位，不应期待股票式 bid/ask。
若账户不能读取 `I:SPX` 历史值，但同到期、同执行价的 SPXW Call/Put 历史双边报价可用，Curated
层允许按 put-call parity 构造 point-in-time synthetic forward：

```text
F(t,T,K) = K + exp(r * (T-t)) * (CallMid(t,T,K) - PutMid(t,T,K))
```

构造必须只使用 `available_time <= t` 且未超过报价新鲜度阈值的双边报价；多个有效执行价取中位数。
利率、配对数量和来源必须进入 lineage/质量信息。合成远期用于 Black-76、moneyness 和曲面研究，
不得标记为官方 SPX 现货点位，也不得用于要求官方指数收盘价或结算价的研究。

首期不要依赖 Massive 返回的技术指标。SMA、EMA、MACD、RSI 等应由内部 feature pipeline 基于冻结 canonical 数据计算，保证公式、窗口和 lineage 可控。

### 5.4 外部身份映射

建议增加以下 point-in-time 映射：

```text
ExternalInstrumentMapping(
  provider,
  source_namespace,
  publisher_id,
  external_instrument_id_or_ticker,
  internal_instrument_id,
  effective_from,
  effective_to
)
```

Massive 的普通 ticker 和 OCC option ticker 都只是外部身份。每条 canonical event 必须能解析到事件发生时有效的内部 `InstrumentId`。无法映射时写入隔离区并阻断正式数据集发布，不允许根据当前 symbol 猜测。

连续期货只能作为派生研究 series。需要模拟成交时，必须根据明确 rollover policy 映射到实际合约。

### 5.5 Canonical event envelope

供应商公共字段不应重复散落在每个 payload 中。建议引入统一 envelope：

```text
MarketEventEnvelope
  instrument_id
  event_time
  receive_time
  available_time
  ingested_at
  source
  source_dataset
  publisher_id
  source_instrument_id
  record_type
  source_order
  flags
  payload
```

`payload` 使用或扩展内部类型：

- `Trade`；
- `Quote`；
- `Bar`；
- `InstrumentDefinitionEvent`；
- `TradingStatusEvent`；
- `StatisticsEvent`。

Massive 的 nanosecond 时间、condition code、exchange code、分页字段和可空 snapshot 字段只允许在 connector/decoder 中解释。Notebook、feature 和 strategy 不得自行处理供应商编码规则。

## 6. 目标架构

```text
Massive REST API / Flat Files
        |
        +-- options contracts and corporate actions
        +-- quotes, trades, aggregates and snapshots
        +-- raw JSON/CSV files and request receipts
        |
        v
kairos.connectors.massive
        |
        +-- plan/entitlement/rate-limit checks
        +-- pagination and immutable source capture
        +-- endpoint-specific decode and validation
        v
Point-in-time Catalog mapping
        |
        v
Canonical event repository (Parquet)
        |
        +-- quality and reconciliation
        +-- correction and deduplication
        v
Curated datasets and features
        |
        v
Frozen research dataset
        |
        v
Streaming ReplayEventFeed
        |
        v
Backtest / feature / study
```

实时接入应在历史链路稳定后复用各自供应商 connector 和同一个 canonical envelope：

```text
Massive WebSocket -> massive decoder -> canonical events -> event bus
Massive historical -> massive decoder -> canonical events -> replay feed
```

## 7. 数据目录规划

建议在现有四层布局中增加 reference 和 curated 语义：

```text
data/
├── source/
│   └── provider=massive/
│       └── resource=<endpoint-or-flat-file>/
│           └── request_id=<fingerprint>/
│               ├── payload.json.gz | payload.csv.gz
│               └── receipt.json
├── reference/
│   └── provider=massive/
│       ├── option_contracts/
│       ├── ticker_events/
│       └── symbol_mappings/
├── canonical/
│   └── market/
│       ├── trades/
│       ├── quotes/
│       └── ohlcv/
├── curated/
├── features/
└── studies/
```

存储格式：

- Source：Massive 原始 JSON/CSV Flat Files；
- Reference/Canonical/Curated/Features：Parquet + ZSTD；
- metadata、配置和小型结果：JSON；
- 测试 fixture：小型 JSON、JSONL 或 CSV。

Canonical 分区优先考虑 `event_date/asset_class/event_type/provider`。不要默认按每个 symbol 建目录，否则大 universe 会产生大量小文件。

每次 ingestion 写入不可变文件。文件合并由显式 compaction job 完成，不原地修改历史分区。

## 8. 下载与 Source 层契约

建议模块：

```text
kairos/connectors/massive/
├── client.py
├── config.py
├── source.py
├── pipeline.py
├── reference.py
├── decoder.py
├── corporate_actions.py
├── reference_store.py
└── websocket.py
```

运行时入口固定为：

```text
REST and Flat Files: https://api.massiveprivateserver.site
WebSocket:           wss://socket.massiveprivateserver.site
```

代码不得向 Massive 官方公共 API host 或未知 host 发出请求。私有代理返回的官方 `next_url` 只能保留 path/query 并强制改写到私有 REST host；未知跨域 `next_url` 必须拒绝。官方站点只用于阅读文档，不能作为运行时请求目标。

下载器必须满足：

- API key 只从 `MASSIVE_API_KEY` 读取；
- 运行前检查套餐 entitlement、历史深度、延迟级别和 rate limit；
- 请求显式固定 endpoint/resource、ticker universe、filters、sort、limit、`start` 和 `end`；
- 完整跟随 `next_url` 分页，并保存每页 request ID；
- 时间窗口统一为 `[start,end)`；
- 使用请求参数的规范化哈希作为 fingerprint；
- 相同 fingerprint 的成功请求不得重复下载；
- 先写临时文件，校验后原子 rename；
- 保存原始响应或 Flat File，转换失败不能删除 source；
- Flat File 每月上限为 150 GB；下载前必须读取 `/usage` 并执行本地硬门禁；
- Flat File 只能在工作日纽约时间 09:30–16:00 之外启动下载，并使用 1 MiB chunk 流式落盘；
- 重试只处理可重试网络或限流错误；
- 认证、entitlement、参数和 response schema 错误立即失败。

`receipt.json` 至少包含：

```text
provider and API host
endpoint or Flat File object key
ticker universe and filters
request window and boundary
requested_at and completed_at
request fingerprint
Massive request IDs and pagination chain
SDK version, entitlement and data timeframe
rate-limit observations and cost/plan note
response bytes and record count
payload SHA-256
status and retry history
```

依赖应作为可选依赖安装，并锁定兼容范围：

```toml
[project.optional-dependencies]
massive = ["websockets>=12,<17"]
data = ["pyarrow>=22,<26"]
```

REST 和 Flat File 使用标准库实现，避免修改第三方 Massive package 的全局 `BASE` 并防止其他代码绕过私有域名门禁。

## 9. 时间与可见性契约

### 9.1 统一规则

- 全部时间使用 UTC 和 timezone-aware datetime；
- 所有查询和分区窗口采用 `[start,end)`；
- Massive 原始 participant、SIP、TRF 和更新时间字段按 endpoint 完整保留；
- `available_time` 的计算方法必须写入 lineage；
- 聚合 Bar 必须包含 `period_start`、`period_end`、`event_time` 和 `available_time`；
- feature 的时间不得早于其最晚输入的 `available_time`；
- 回测默认按 `(available_time, source_order)` 排序；
- 相同时间戳必须使用稳定且记录在案的 tie-breaker。

### 9.2 延迟模型

历史研究至少支持两种明确模式：

- `provider_published`：Massive 按 endpoint 选用 SIP timestamp 或供应商声明的 last-updated 时间；
- `simulated_delivery`：在供应商可见时间上增加配置化传输、处理和策略延迟。

禁止将 `event_time` 自动等同于 `available_time`，除非研究明确声明使用零延迟理想化模型。

### 9.3 当前代码修正

现有 `MarketSnapshotReplayFeed.between()` 使用包含结束点的判断。它应与数据湖契约统一为：

```python
start <= timestamp < end
```

该修正应在 Massive feed 上线前完成，并增加边界测试。

## 10. 修正、重复和重放策略

系统同时保留两个视图：

- `raw-as-received`：按供应商记录的到达顺序保留所有原始事件；
- `corrected-final`：应用已知修正和撤单后的最终研究视图。

每个研究和回测必须声明使用哪种视图：

- 研究市场当时实际可见信息时，使用 `raw-as-received`；
- 研究最终统计事实或生成清洁报表时，可使用 `corrected-final`；
- 不允许在同一数据集中静默混用。

去重键和 correction 规则按 endpoint/schema 单独定义。发生相同主键、不同内容时必须报告冲突，不能保留最后写入值并静默覆盖。

## 11. 数据质量与发布门禁

### 11.1 检查项

每个 ingestion run 至少检查：

- REST 分页或 Flat File 是否完整读取，响应状态和 request ID 是否有效；
- 返回的 endpoint/resource、ticker、时间窗口和字段契约是否与请求一致；
- source 文件大小、记录数和哈希；
- instrument mapping 覆盖率；
- 未知 exchange、condition、record type 和 flag；
- event/receive/available time 合法性；
- receive latency 分布和异常负延迟；
- 排序和乱序率；
- 重复主键率和内容冲突；
- trade price/size 合法性；
- quote 是否 crossed/locked；
- Massive 分页缺口、重复页和时间覆盖缺口；
- trading status 与 session 一致性；
- Options Contracts 或 Definition 对实际事件的覆盖；
- correction/cancel 引用完整性；
- requested、delivered、decoded、canonical 行数对账。

### 11.2 级别

| 级别 | 例子 | 发布行为 |
|---|---|---|
| ERROR | 解码失败、映射缺失、重复冲突、序列缺口、未来可见性 | 阻止正式发布 |
| WARNING | 延迟异常、宽点差、部分非关键字段缺失 | 允许带警告发布 |
| INFO | 合法闭市、halt、无成交区间 | 记录但不阻断 |

质量结果必须随 dataset 保存，不能只写运行日志。Coverage 必须使用对应交易所日历，不得对交易所数据沿用 `24x7`。

## 12. Canonical repository 与回测 Feed

建议定义与文件格式解耦的 repository：

```text
MarketEventRepository.write_batch(...)
MarketEventRepository.scan(
  start,
  end,
  instruments,
  event_types,
  columns
)
MarketEventRepository.metadata(...)
```

新增流式历史 feed：

```text
ReplayEventFeed
  start,
  end,
  instruments,
  event_types,
  clock="available_time",
  view="raw-as-received"
)
```

回测必须满足：

- 不一次加载完整高频 dataset；
- 默认按 `available_time` 驱动；
- 策略不可读取未来事件；
- Bar 信号只能在下一可成交事件执行；
- 同时间戳顺序确定且可复现；
- dataset version、manifest hash、mapping snapshot 和 view 写入结果；
- 相同数据、代码、配置和随机种子生成相同 audit hash。

## 13. 实施计划

### Phase 0：Massive 数据可用性验证

交付：

- 固定首个研究用例为 SPX/SPXW 历史期权研究；
- 用同一批日期验证 options contracts、quotes、trades、aggregates、chain snapshot 和标的/指数数据；
- 核对套餐 entitlement、历史深度、延迟、分页、rate limit 和 Flat Files 可用性；
- 确认历史 Greeks、IV 和 OI 的可获得范围，不假设当前 snapshot 字段可用于历史；
- 确定 SPXW 加少量美股期权的 symbol universe 和时间窗口；
- 定义订阅费用和下载预算；
- 确定 correction view 和延迟模型；
- 确认数据许可、保存和内部共享边界；
- 为首个数据集登记内部 Dataset ID。

完成标准：请求和输出语义不存在待实现代码自行决定的关键歧义。

### Phase 1：Massive Historical Source Connector

交付：

- 可选依赖和配置；
- entitlement、coverage 和 rate-limit 检查；
- REST 分页与 Flat Files 的幂等下载；
- request fingerprint 和 receipt；
- 原子写入、重试和错误分类；
- 小型 JSON/CSV fixture 和 contract tests。

完成标准：删除所有下游文件后，仍能从 Massive source payload 重新开始处理。

### Phase 2：Massive Reference 与 symbology

交付：

- options contracts、ticker events、splits 和 dividends decoder；
- `ExternalInstrumentMapping`；
- Catalog/InstrumentDefinition 历史版本导入；
- exchange、condition code 和交易日历映射；
- symbol 冲突和有效期检查；
- 未映射事件隔离区。

完成标准：正式 canonical 数据中每个事件都能解析到当时有效的内部 `InstrumentId`。

### Phase 3：Massive Canonical event 与 Parquet storage

交付：

- `MarketEventEnvelope`；
- Quotes、Trades、Aggregates 和 Option Chain Snapshot decoder；
- 明确的价格、数量和单位转换；
- Vendor Greeks、IV、OI 与内部估值字段分离；
- Parquet repository 和 predicate scan；
- immutable partition 和 compaction；
- schema、lineage、coverage 和 manifest v2。

完成标准：相同 source 输入生成相同 canonical dataset hash。

### Phase 4：质量、修正与对账

交付：

- endpoint/schema-specific 去重和 correction policy；
- 时间、映射、价格、quote、sequence 和 session 检查；
- raw/canonical reconciliation；
- dataset publish gate；
- 可机器读取的质量报告。

完成标准：任何阻断问题都不能被正式 Dataset ID 读取。

### Phase 5：流式 replay 和研究接入

交付：

- Parquet-backed `ReplayEventFeed`；
- `available_time` clock；
- `[start,end)` 边界修正；
- curated bars/snapshots；
- 首个 feature 和 study；
- 与现有 backtest 的端到端测试。

完成标准：重复运行产生相同 audit hash，且没有未来事件泄露。

### Phase 6：Massive WebSocket

历史链路稳定后按业务需要实施：

- reconnect、heartbeat 和 backpressure；
- sequence gap 检查和恢复；
- 实时 raw journal；
- 历史 backfill；
- Massive WebSocket 与 historical reconciliation；
- Massive 实时使用与历史相同的 canonical envelope。

## 14. 推荐首个 MVP

首个 MVP 应控制范围：

- 仅 Massive REST/Flat Files，不接 WebSocket；
- SPX/SPXW 为主，并加入 1 至 2 个美股标的验证通用性；
- Options Contracts + Historical Quotes + Trades；
- 标的/指数 Aggregates 或 Quotes；
- 当 `I:SPX` 历史权限不可用时，允许由 SPXW 同执行价 Call/Put Quotes 构造合成远期；
- Option Chain Snapshot 只用于当前链契约验证，不回填历史；
- 选取少量到期日和执行价，控制初始数据体积；
- 5 个交易日；
- Source 保存完整 JSON 分页响应或原始 Flat Files；
- Options Contracts 写入历史 Catalog；
- Trade/Quote 转换为 Parquet；
- Vendor Greeks/IV/OI 与内部计算结果分列保存和对账；
- 输出 entitlement、请求回执和质量报告；
- 使用 `available_time` 流式 replay；
- 完成一个与现有回测模型连接的端到端测试。

MVP 不接 WebSocket，只验证 Massive 历史数据闭环。

## 15. MVP 验收标准

- 同一请求重复运行不会产生重复数据；
- 运行前确认套餐包含所需历史、延迟和 endpoint 权限；
- REST 分页完整，没有丢页、重复页或无限循环；
- Source JSON/CSV 永不被 canonical 转换覆盖；
- 删除 canonical 后可由 source 重建相同 hash；
- 所有事件都有有效内部 `InstrumentId`；
- raw、decoded 和 canonical 行数差异有明确解释；
- 没有未知分页缺口、时间缺口或静默冲突；
- `end` 边界不会多读取一条事件；
- 回测按照 `available_time` 读取且不能看到未来事件；
- 相同输入重复回测的 audit hash 一致；
- decoder、mapping、endpoint response 或 schema 规则变化产生新版本，不覆盖旧结果；
- Vendor Greeks/IV 不作为内部估值事实，缺失时不会阻断内部定价；
- `I:SPX` 历史值不可用时，只要 SPXW Call/Put 配对报价满足 point-in-time、新鲜度和质量门禁，合成远期可通过内部 IV/Greeks readiness；
- 合成远期的公式、利率、配对数量和来源可审计，且不会被标记为官方指数点位；
- Notebook 只按内部 Dataset ID 读取数据。

## 16. 项目使用规范

以下规则应逐步变成代码检查或自动测试：

1. 研究代码不得直接实例化 Massive client。
2. Strategy、feature 和 backtest 不得读取 source 层。
3. 所有外部 symbol 必须通过 point-in-time Catalog 映射。
4. 所有窗口统一使用 `[start,end)`。
5. 所有回测默认按 `available_time` 驱动。
6. Source JSON 和 CSV 不可变，canonical 采用版本化不可变写入。
7. 下载前必须检查 entitlement、历史范围、rate limit 和预算。
8. Dataset ID 表达内部数据语义和版本，不能以文件名代替版本。
9. Feature lineage 必须引用 canonical dataset ID 和内容哈希。
10. 训练、验证和测试 split 必须在生成未来标签前冻结。
11. Schema、decoder、mapping 或 correction 规则变化必须生成新版本。
12. Notebook 只消费注册过的 Dataset ID。
13. 每次回测记录 provider、resource/schema、symbology snapshot、view 和 content hash。
14. 连续期货只用于研究序列，模拟成交必须落到实际合约。
15. Reference 和 corporate action 必须 point-in-time，禁止以当前 universe 回填历史。

## 17. 风险清单

| 风险 | 影响 | 控制措施 |
|---|---|---|
| Symbol 映射错误 | 数据归属到错误合约，结果无法察觉 | Definition + 有效期映射 + 发布门禁 |
| 使用 `event_time` 代替可见时间 | 前视偏差 | 保留多时间并按 `available_time` replay |
| 把当前 chain snapshot 当历史链 | 严重前视和错误曲面 | Snapshot 只按采集时点使用；历史使用 quotes/trades/flat files |
| Vendor Greeks/IV 当唯一事实 | 模型口径不明且历史字段可能缺失 | 作为 vendor observation；内部模型独立计算 |
| 套餐不含所需历史或实时级别 | 实施完成后仍无法研究 | Phase 0 entitlement/coverage bake-off |
| `I:SPX` 历史权限缺失 | 无法直接取得官方指数历史点位 | SPXW 历史双边报价 + point-in-time put-call parity 合成远期；需要官方收盘/结算的研究继续阻断 |
| 默认获取过高精度数据 | 成本和存储失控 | 按研究假设选择 endpoint/Flat File/schema |
| CSV/JSON 承载 canonical 高频数据 | 处理慢、内存高、无法扩展 | 原始格式只留 Source；Parquet canonical + 流式 scan |
| 将闭市误判为缺口 | 错误质量告警或错误填充 | 交易所日历和 trading status |
| 静默忽略修正或重复 | 成交统计和 PnL 错误 | raw/final 双视图和 schema-specific policy |
| Notebook 直接下载 | lineage、身份和质量约束失效 | 只允许 connector 下载、Dataset ID 消费 |
| SDK 升级改变解码结果 | 历史结果不可复现 | 锁定版本、fixture、contract test、新数据版本 |
| 连续合约直接成交 | 不现实的价格和换月收益 | 显式 rollover policy 和实际合约映射 |
| 数据许可边界不清楚 | 合规和共享风险 | Phase 0 记录许可、保存和再分发约束 |

## 18. 实施优先级

| 优先级 | 工作 | 原因 |
|---|---|---|
| P0 | 时间契约、symbology、raw receipt | 错误会形成隐蔽研究偏差 |
| P0 | Massive entitlement 与历史覆盖验证 | 避免在错误套餐和字段假设上实现 |
| P0 | REST/Flat File 原始文件和幂等下载 | 保证数据可重建 |
| P0 | Parquet 和流式 scan | 高频研究的基础 |
| P1 | Options Contracts 与 ticker events 导入 Catalog | 多市场、多品种扩展基础 |
| P1 | 质量报告、修正和序列检查 | 保证数据可信 |
| P1 | `available_time` replay | 防止前视 |
| P2 | Curated bars、NBBO 和 book snapshots | 提高研究效率 |
| P2 | Massive WebSocket | 历史链路稳定后复用 |

## 19. 关键决策记录

后续实施前需要在本节或独立 ADR 中记录以下实际选择：

| 决策 | 当前状态 |
|---|---|
| 首期主数据源 | Massive |
| 首个研究切片 | SPX/SPXW 历史期权研究 |
| 首批资源 | Options Contracts + Quotes + Trades + 标的/指数数据 |
| Option Chain Snapshot 用途 | 仅当前链采集和字段契约验证，不回填历史 |
| 首个 instrument universe | SPX/SPXW + AAPL；AAPL 用于通用股票期权链路 smoke test，不能替代 SPX |
| 首个历史窗口 | 正式 MVP 为连续 5 个交易日；2026-07-15 单日样本只作为 HTTPS 与转换链路 smoke test |
| Massive 套餐与 entitlement | Options contracts/quotes/trades/current chain 可用；`I:SPX` historical aggregates 当前返回 403；普通 SPXW 报价/IV/曲面研究使用合成远期继续，要求官方指数历史值的研究单独阻断 |
| Flat File 下载预算 | 配置上限 150 GB/月；同时读取服务端 `/usage`，当前服务端上限 100 GB，实际硬门禁取两者较小值 |
| 默认延迟模型 | `provider_published`：quote/trade 使用 SIP timestamp，aggregate 使用 period end |
| 默认 correction view | 回测默认 `raw-as-received`；最终统计可显式选择 `corrected-final` |
| Parquet 引擎 | PyArrow，兼容范围 `pyarrow>=22,<26`，ZSTD 压缩 |
| 交易日历来源 | 内部 `TradingCalendar` 的 US securities calendar；包含周末、主要假日、DST 与 half day，并允许注入额外休市日 |
| 数据许可和共享范围 | 在取得供应商书面条款前仅限本项目内部研究，不对外分发 Source/Canonical 数据；正式发布仍需账户所有者确认保存、团队共享与再分发边界 |

当前账户实测记录（2026-07-15，HTTPS 私有服务）：

- `/usage`、SPX/SPXW options contracts、historical quotes、historical trades 和 current option chain 可访问；
- `I:SPX` historical aggregates 返回 403，当前账户不能完成必须使用官方历史 SPX 点位的研究；
- `SPX` 股票聚合路径虽然返回成功，但结果为空，禁止将其当成指数数据或静默改用 SPY；
- AAPL underlying aggregates 与 options quotes 已完成 HTTPS smoke pipeline，证明通用股票期权链路可运行；
- `/usage` 实测返回 `limitGB=100`；下载器采用服务端额度与本地 150 GB 配置的较小值，因此当前账户按 100 GB 硬门禁；
- 实测 SPXW historical quotes 返回 bid/ask；指数历史权限缺失时，可由合格 Call/Put 配对报价构造合成远期并通过普通 IV/Greeks/曲面研究 readiness；官方指数收盘、结算和基准对账仍阻断。

权限复测记录（2026-07-16，窗口 2025-11-03 至 2025-11-28）：

- `AAPL` daily aggregates 返回 HTTP 200、供应商状态 `OK`、19 条结果；
- `I:SPX` daily aggregates 返回 HTTP 403、供应商状态 `NOT_AUTHORIZED`，错误明确为账户未订阅该数据；
- 因而私有 HTTPS 代理和股票聚合接口工作正常，剩余问题是 `I:SPX` entitlement，不是客户端、Notebook 或日期参数错误。
- 同日进一步实测 `O:SPXW251103C02800000` historical quotes 返回 HTTP 200，包含 bid/ask；因此 `I:SPX` 403 不等于 SPXW 盘口不可用。

合成远期闭环实测（2026-07-16，市场窗口 2025-11-03 14:30–14:35 UTC）：

- 选取 6750、6800、6850、6900 四个执行价，各自配对 SPXW Call/Put，共 8 个真实合约；
- Canonical Dataset ID：`options.us.massive.spxw.synthetic-forward.20251103.https.v1`，211,690 条 quote/trade 事件；
- Curated Dataset ID：`spxw.massive.synthetic-forward.20251103.https.v1`，5 个一分钟切片；
- 第一个切片在任何报价可见前保持 `missing`，没有回填未来报价；后续 4 个切片均显式标记 `synthetic_forward`；
- 后续每个切片 8/8 合约均有报价并完成内部估值，合成远期依次约为 6873.63、6872.59、6871.43、6869.85；
- Notebook 产生 32 个可估值观测，32/32 priceable，状态 `READY_FOR_INTERNAL_IV`；
- Canonical hash：`29c781e4434bd6e5875a27c66af05a937682ca8cac809c9c3256ab4aa4c3e37b`；Curated hash：`bbd9a2c79ae2b0f9b4f3099be4b8a108c385270ed6f2e1cb803501243d258d4a`。

## 20. 官方参考

Massive：

- [Massive Documentation](https://massive.com/docs/)
- [Massive Options REST API](https://massive.com/docs/rest/options/overview)
- [Massive Option Chain Snapshot](https://massive.com/docs/rest/options/snapshots/option-chain-snapshot)
- [Massive Options Quotes](https://massive.com/docs/rest/options/trades-quotes/quotes)
- [Massive Options Flat Files](https://massive.com/docs/flat-files/options)
- [Massive WebSocket Documentation](https://massive.com/docs/websocket/quickstart)

供应商 API、字段、套餐、历史范围和许可可能变化。实际实施时必须重新核对官方文档和账户 entitlement，不能仅以本文作为供应商事实来源。

## 21. 实现与验证证据

| 文档要求 | 当前实现 |
|---|---|
| HTTPS 私有 REST/Flat File host | `MassiveConfig` 强制 HTTPS；公共 next URL 只允许改写到私有 host |
| WSS 私有实时 host | `MassiveConfig` 强制 WSS，`MassiveWebSocketClient` 负责认证和订阅 |
| 密钥不落盘 | 只读取 `MASSIVE_API_KEY`，Header 鉴权，URL/receipt/异常脱敏测试 |
| Source 不可变与幂等 | `MassiveVendorArchiveClient`、内容 fingerprint、gzip pages、receipt 和 HTTPS cache gate |
| Flat File 限额和时段 | `/usage` 服务端额度与本地 150 GB 配置取较小值、纽约 09:30–16:00 禁止、1 MiB 流式写入 |
| Flat File 年度批次 | `massive-flat-file-batch --start/--end/--max-files` 按 US 交易日历生成 OPRA Day Aggregates key，支持 dry-run、已下载跳过、缓存中状态和内容寻址批次报告 |
| OPRA 年度 inventory | `SpxwDailyOhlcvPipeline.build_inventory` 以交易日历为预期集合，冻结 date/key/fingerprint/path/bytes/SHA-256 映射并阻断缺日或 hash 冲突 |
| SPXW Daily OHLCV Curated | `prepare-spxw-daily-ohlcv` 流式过滤 `O:SPXW`，校验 OCC/OHLC/window_start，写月度 ZSTD Parquet 和 0DTE/热门 Call-Put 每日滚动代表序列 |
| 通用期权根 Daily OHLCV | `prepare-option-daily-ohlcv --option-root` 复用同一 OPRA inventory，为 NVDA 等 OCC root 生成隔离的月度 Parquet |
| 股票 Daily OHLCV 与期权收盘 IV | `prepare-equity-daily-ohlcv --provider massive` 归档/转换调整后股票日线；`prepare-option-close-implied-volatility` 为全部 daily OHLCV 保留求解状态并物化内部 close-based IV |
| Point-in-time identity | `ExternalInstrumentMapping`、Catalog 历史版本和未映射隔离 |
| Reference/corporate actions | Options Contracts importer、code-table store、split/dividend/ticker-event decoder |
| Canonical Parquet | `MarketEventEnvelope`、ZSTD Parquet、内容寻址文件、声明式 Dataset Registry |
| 数据质量与重放视图 | publish gate、reconciliation、`raw-as-received`/`corrected-final` |
| 防前视时间 | SIP/period-end `available_time`、`[start,end)` scan、稳定 source order |
| 研究与回测桥接 | `MassiveMarketSnapshotBuilder` 生成现有 `MarketReplayDataset/MarketSnapshot` |
| SPXW 合成远期 | 缺少官方指数 bar 时，用新鲜 Call/Put 配对报价按 put-call parity 构造中位数远期；质量信息和 manifest source 显式标记 |
| 实时恢复 | raw journal、reconnect、sequence-gap 和 historical-backfill hooks |
| 人工数据体检 | `massive_data_quality.ipynb` 与 `massive_research_diagnostics.ipynb`，均已用 HTTPS smoke 数据无界面执行 |
| 热门合约探索 | `spxw_popular_options_2026.ipynb` 读取受管 Day Aggregates Dataset，展示具体 ticker 排名和每日滚动的最活跃/0DTE ATM Call-Put 日线；不在 Notebook 下载数据 |
| NVDA 年度探索 | `nvda_options_2026.ipynb` 读取受管 NVDA 股票/期权/IV Dataset，展示日 K、全量 IV 密度、近 ATM IV 和最新微笑；不在 Notebook 下载数据 |

2026 YTD OPRA Day Aggregates 实测：

- Source 覆盖 2026-01-02 至 2026-07-14，共 132/132 个 US 交易日、约 496.84 MiB gzip，月度额度计量约 0.485 GB；
- 冻结 inventory 包含每个交易日的 key、fingerprint、path、bytes 和实际复算 SHA-256，hash 为 `62893f5323f1f0e2ca0878c52543760819e71dfe2d7d15acfafef7d20b9f4f24`；
- Curated Dataset ID：`options.us.massive.spxw.day-aggs.2026-ytd.v2`，共 1,009,000 条 SPXW 日聚合和 7 个按月 ZSTD Parquet；v2 将可见时间明确设为下一自然日 11:00 America/New_York；
- Dataset hash：`b149bd27ea8e910f772f8774eb347508fb2eb6759541e9c4540b86199d2f196d`；
- Gold 每日代表序列 132 行；总成交量、活跃合约数、每日最活跃 Call/Put 和 0DTE ATM Call/Put 均有 132 个观测；
- `spxw_popular_options_2026.ipynb` 已对该完整 YTD Dataset 无界面执行通过。

NVDA 2026 YTD 实测：

- `equity.us.massive.nvda.day-aggs.2026-ytd.v1`：133 根调整后股票日线，content hash `933692b1072e3fb5ef3023a90e1fa28ef62a829beb5c2b8523a3379c01faee81`；
- `options.us.massive.nvda.day-aggs.2026-ytd.v1`：303,007 条 NVDA 期权日聚合，Dataset hash `e15d8608b63cad2979331a27da1763dedc2600f52f04ed7652e3a25cca29a0b8`；
- `features.us.massive.nvda.close-iv.2026-ytd.v1`：279,782/303,007 条 IV 收敛（约 92.3%），Dataset hash `2f6b394b3198ce2797e98915de2453268fd1a0795d5da3a47aba3ffd5055258b`；
- 其余状态为 `expired_at_close` 6,907、`price_out_of_bounds` 15,255、`not_bracketed` 1,063，均保留而非丢弃；
- `nvda_options_2026.ipynb` 已对上述真实 Dataset 无界面完整执行。
| 自动化证据 | `test_massive_*`、`test_market_event_repository.py` 和完整 unittest suite |
