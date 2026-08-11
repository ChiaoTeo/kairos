# Market 模块交付定义与实现路线

## 1. 文档目的

本文用于明确 Market 模块到底交付什么、哪些能力应归入 Market、哪些能力应由其他模块负责，以及后续实现和验收顺序。

本文遵循仓库中的模块架构约束：

```text
bin -> composition -> application -> services
                         \\-> domain
```

Market 的目标不是成为“交易所连接器集合”，而是向策略、Execution 和其他消费者提供标准化、可验证、带顺序和新鲜度信息的市场事实。

## 2. Market 的目标定义

Market 是以下业务事实的唯一 Owner：

1. 当前市场行情：Quote、Trade、Bar、Greeks，以及后续的 Rate 等市场数据。
2. 当前订单簿：快照、增量、sequence 连续性、同步状态和 resync 状态。
3. 市场数据新鲜度：数据是否及时、是否断流、是否处于恢复或降级状态。
4. 市场订阅意图：静态订阅、动态订阅、数据类型筛选和 Reference 变化后的成员重算。
5. 对外市场投影：当前快照、事件流、查询结果、健康状态和订阅状态。

Market 不负责解释策略信号，也不负责创建或管理交易所订单。

## 3. Market 应交付的能力

### 3.1 实时行情

第一阶段应稳定交付：

- Quote：买一、卖一及其数量。
- Trade：成交价格、数量、成交时间和成交标识。
- Bar：时间周期、OHLC、成交量和数据来源。
- Greeks：期权到期日、行权价、Greeks 和隐含波动率。

后续应补充：

- Rate：资金费率、利率、标记价格等。
- Index price、mark price。
- Open interest。
- 其他有明确业务消费者的市场数据。

每条行情事实都应包含稳定的市场身份、数据来源、事件时间和必要的派生来源信息。

### 3.2 订单簿

Market 应负责：

- 接收订单簿快照。
- 接收并应用订单簿增量。
- 校验市场身份和 sequence 范围。
- 检测 sequence gap。
- 在 gap 或未同步时拒绝继续累积不可靠状态。
- 请求单市场 resync。
- 对外发布 synchronized 状态。

订单簿必须能区分数据来源。建议最终使用以下逻辑身份：

```text
OrderBookKey = source_id + market_id
```

不能只使用 `market_id`，否则同一市场的多个数据源可能互相覆盖。

### 3.3 订阅

Market 应交付两种订阅：

- 静态订阅：明确指定一个或多个市场。
- 动态订阅：根据 exchange、market type、asset type、symbol、生命周期等条件选择市场。

订阅还应支持：

- quote、trade、bar、greeks、orderbook 等 selector。
- bar timeframe qualifier，例如 `bar:1m`。
- owner identity。
- 成员数量上限。
- 幂等的 Reference reconciliation。
- provider 实际订阅失败后的恢复和重试。

订阅意图属于 Market；provider 订阅句柄属于 Market services，不应暴露给跨模块调用者。

### 3.4 Reference 联动

Reference 拥有市场目录和市场生命周期，Market 只维护用于订阅重算的本地投影。

正常流程为：

```text
Reference snapshot/event
        -> Market 本地市场投影
        -> 动态订阅 reconcile
        -> provider subscription reconcile
```

如果 Market 发现 Reference event watermark 有 gap，应重新读取 Reference snapshot，再重算动态订阅，而不是依赖每个事件都携带完整目录。

### 3.5 当前投影与事件流

Market 应向消费者提供：

- 当前最新 Quote、Trade、Bar、Greeks、Rate。
- 当前订单簿。
- 事件 sequence 和 snapshot generation。
- 事件时间和发布时间。
- 数据来源和 freshness。
- 订阅状态。

建议将投影拆成不同的读取边界：

```text
current          当前最新行情
orderbook        当前订单簿
subscriptions    订阅状态
freshness        数据新鲜度和健康
history          历史行情和回放数据
```

不建议把所有数据塞进一个无限扩大的 Market snapshot。

## 4. 能力归属边界

| 能力 | 归属 |
|---|---|
| Quote、Trade、Bar、Greeks、Rate | Market |
| OrderBook 快照、增量、sequence、resync | Market |
| 行情当前值和 view projection | Market |
| 订阅意图和动态成员 | Market |
| provider 订阅与业务订阅的映射 | Market services |
| feed freshness 和 Market health | Market |
| 市场目录、Instrument、Exchange、Listing 生命周期 | Reference |
| 交易所 SDK、HTTP/WebSocket、鉴权 | Integration |
| provider payload 归一化为标准事件 | Integration / Market adapter |
| provider route、endpoint 和具体实现选择 | Composition |
| 账户模式、保证金、持仓模式和账户费率 | Account / Integration |
| 下单、订单状态、成交生命周期 | Execution |
| 风险预算和 reservation | Risk |
| 策略指标、alpha 和交易信号 | Strategy |
| mmap、Aeron、FlatBuffers 和传输细节 | Contract / Transport |

跨模块调用只能进入目标模块的 application 或 contract，不得直接依赖其他模块的 services 或私有文件。

## 5. 推荐模块结构

当前目录结构总体已经符合目标形态，后续应继续保持：

```text
crates/business/market/
  contract/
    src/
      model.rs       # 对外业务模型
      query.rs       # 查询协议
      event.rs       # 事件协议
      snapshot.rs    # 快照读取
      transport.rs   # mmap/Aeron 等传输

  service/
    src/
      bin/            # server 和 CLI
      composition/    # provider、publisher、模式和依赖组装
      application/    # MarketApplication、MarketRuntime、MarketProcess
      services/       # actor、feed、connection、replay、worker 等内部实现
      domain/         # 行情、订单簿、订阅、freshness、快照等业务模型
```

### 5.1 Domain

Domain 应包含：

- `MarketDescriptor`。
- `MarketObservation` 及其具体类型。
- `OrderBook` 和 `OrderBookDelta`。
- `SubscriptionState` 和 selector 规则。
- `FeedStatus` 以及更完整的 freshness 模型。
- `MarketSnapshot`。

Domain 不得依赖交易所 SDK、Integration、传输协议或其他业务模块的 infrastructure 实现。

### 5.2 Application

Application 是 Market 的公开业务边界，应提供：

- 创建和管理订阅。
- 摄取标准化行情。
- 摄取订单簿快照和增量。
- 查询当前市场数据。
- 查询订单簿、订阅和 freshness。
- Reference reconciliation。
- feed 恢复和 resync 控制。

Application API 不应暴露 provider payload、provider subscription handle、FlatBuffers generated type 或 persistence record。

### 5.3 Services

Services 负责：

- 唯一可变状态 Actor。
- provider feed 生命周期。
- provider subscription reconciliation。
- Integration 到 Market domain 的适配。
- worker、polling、WebSocket 和 replay。
- 内部事件缓冲和背压处理。

Actor 是业务状态的唯一 Owner。Process facade 可以驱动 Actor，但不能维护第二套行情状态。

### 5.4 Composition

Composition 负责：

- 选择 Binance、OKX、Massive 等 provider。
- 选择 REST、WebSocket、replay 或 simulated 模式。
- 构造具体 publisher 和 contract client。
- 组装 Reference source、Market feed 和运行参数。

业务应用层不应自己选择 provider 或创建具体连接。

## 6. 当前实现中的主要缺口

### 6.1 Rate 已存在于 schema，但没有进入 domain

当前 `schemas/market/v1/data.fbs` 已定义 `Rate`，但 Market domain 目前主要包含 Quote、Trade、Bar 和 OptionGreeks。

需要明确是否把资金费率、标记价格、指数价格等纳入 Market v1。建议至少补齐 `Rate`，避免 schema、Integration 和 domain 长期分叉。

### 6.2 OrderBook 缺少 source identity

当前订单簿状态主要按 `market_id` 管理，而行情 view 已经把 `source_id` 纳入身份。

建议为 OrderBook 增加：

- `source_id`。
- `checksum`。
- depth 或 level policy。
- event time 和 receive time。

并将内部索引从：

```text
BTreeMap<market_id, OrderBook>
```

调整为：

```text
BTreeMap<(source_id, market_id), OrderBook>
```

### 6.3 Freshness 只有 feed 总状态

当前 `Disconnected`、`Ready`、`Reconnecting`、`WarmingUp`、`Degraded` 只能表达连接总体状态，不能支持 Execution 判断某个市场是否可执行。

建议补充：

```text
MarketFreshness {
  source_id
  market_id
  data_kind
  last_event_time
  last_receive_time
  age
  sequence
  status
}
```

Execution 最终需要能够判断：

- Quote 是否在允许延迟内。
- OrderBook 是否 synchronized。
- 数据源是否 degraded。
- 当前数据是否来自预期 source。

### 6.4 Market contract 读取能力不完整

当前 contract 已有 Quote 快照读取能力，但 Market 实际已经发布了更多内容。

建议补齐 typed readers：

```text
read_latest_quotes
read_latest_trades
read_latest_bars
read_latest_greeks
read_latest_rates
read_orderbooks
read_subscriptions
read_freshness
read_watermark
```

消费者不应被迫直接解析原始 FlatBuffers 或自行读取 Market service 内部结构。

### 6.5 source_id 语义需要统一

当前 Integration 适配中，`source_id` 与 exchange identity 的关系比较接近。未来同一个 exchange 可能同时有 REST、WebSocket、多个账户或多个 route。

建议明确区分：

```text
exchange_id      = binance
connection_id = binance.spot.websocket.public
source_id     = 产生事实的稳定数据源身份
```

同一 Market view 不应因多个连接而发生不透明覆盖。

### 6.6 current 与 history 的边界需要固定

`current` 应服务低延迟策略和 Execution；`history` 应服务回测、研究、导出和 replay。

两者应有不同的读取节奏、容量和保留策略，不应因为已有 history schema 就把历史存储强行塞进实时 Actor。

## 7. 实现路线

### 阶段一：实时行情基础闭环

交付：

- Quote、Trade、Bar、Greeks。
- OrderBook snapshot/delta。
- sequence gap 检测和 resync。
- 静态和动态订阅。
- Reference 变化后的动态订阅重算。
- 当前 snapshot。
- Market event stream。
- Market health。
- mmap snapshot contract。
- 至少一个稳定的 Binance Spot 端到端 feed。

验收链路：

```text
订阅市场
  -> provider 建立订阅
  -> 收到标准化行情
  -> Actor 更新状态
  -> 发布 current snapshot
  -> 发布 event
  -> 客户端读取 quote/orderbook
```

### 阶段二：可执行性与可靠性

交付：

- per-market、per-data-kind freshness。
- source identity。
- OrderBook checksum。
- receive timestamp。
- stale data 检测。
- feed recovery。
- provider subscription 状态。
- snapshot watermark。
- 完整的 typed contract readers。
- 明确的事件背压和丢弃策略。

完成这一阶段后，Execution 才能安全将 Market 作为执行前检查的 advisory projection 来源。

### 阶段三：市场数据扩展

交付：

- Rate、Funding、Mark/Index price。
- Open interest。
- 更完整的期权 Greeks。
- 多 provider source。
- 多资产类型和多市场类型。
- 历史行情存储。
- replay/backtest 输入。
- 历史查询 contract。

### 阶段四：运行和运维能力

交付：

- subscription snapshot。
- source/feed health。
- lag、drop、resync、provider error 指标。
- operator CLI。
- replay diagnostics。
- 长时间断流和恢复测试。

## 8. 交付验收标准

### 业务正确性

- 同一市场的不同数据类型不会互相覆盖。
- 同一数据类型的旧事件不会回退当前投影。
- 订单簿增量连续时正确合并。
- sequence gap 后进入未同步状态，并能恢复。
- 动态订阅对相同 Reference watermark 具有幂等性。
- 静态订阅不会被 Reference reconcile 意外修改。

### 边界正确性

- Market application 不暴露 provider 类型。
- Market domain 不依赖 Integration 实现。
- 跨模块代码不导入 Market services 私有文件。
- Composition 才选择具体 provider 和 transport。
- Contract reader 隐藏 FlatBuffers generated type。

### 运行可靠性

- provider 订阅部分失败时可以回滚。
- feed 断开后能进入明确状态。
- orderbook gap 能触发 resync。
- 事件 backlog 超限时有可观测、可恢复的行为。
- snapshot generation 和 event sequence 单调递增。
- freshness 能够被消费者直接查询。

### 可观测性

至少记录：

- feed start、stop、reconnect。
- subscription accepted、rejected、reconciled。
- provider subscribe/unsubscribe。
- observation rejected。
- orderbook gap、resync、恢复结果。
- snapshot publish。
- event backlog、drop 和 provider error。

## 9. 推荐的近期工作顺序

1. 固定 Market v1 业务模型，决定是否纳入 Rate。
2. 为 OrderBook 增加 source identity 和 checksum 语义。
3. 将 freshness 从 feed 总状态升级为逐市场、逐数据类型状态。
4. 补齐 Market contract 的 typed readers。
5. 固定 current、orderbook、subscriptions、freshness、history 的投影边界。
6. 为每个 projection 写端到端验收测试。
7. 再扩展新的 provider 和市场数据类型。

## 10. 最终结论

Market 应交付的是：

> 标准化、可验证、带顺序保证和新鲜度信息的当前市场事实。

Market 拥有行情状态、订单簿状态、订阅意图和 freshness；Reference 拥有市场目录；Integration 拥有交易所连接和外部事实归一化；Contract 拥有跨进程交付格式；Composition 负责组装具体实现。

当前实现已经具备较完整的骨架，下一步重点不是继续堆叠 manager 或 provider，而是先收敛并补齐以下四个契约：

1. Rate 是否属于 Market v1。
2. OrderBook 的 source identity。
3. per-market freshness。
4. 完整的 Market contract readers。

## 11. Legacy 实现复盘

当前 `trader` 工作树没有独立的 `legacy/` 目录。Legacy 主要存在于 Git 的 `main` 分支历史中，相关实现集中在：

- `kairospy/domain/market/model.py`
- `kairospy/domain/market/orderbook.py`
- `kairospy/domain/market/events.py`
- `kairospy/domain/market/selection.py`
- `kairospy/domain/market/selectors.py`
- `kairospy/application/usecases/market/domain/specs.py`
- `kairospy/application/usecases/market/domain/subscriptions.py`
- `kairospy/application/usecases/market/application/replay.py`

Legacy 已经实现或表达过的市场语义包括：

### 11.1 统一 Market Subject

Legacy 使用 `MarketSubject` 区分：

- instrument subject
- market subject
- rate subject
- curve subject
- index subject

这说明行情不一定都直接挂在一个 instrument 上。资金利率、曲线点、指数值可以有自己的 subject identity。

当前 Rust 模型主要依赖 `market_id + instrument_id`，后续应保留 subject 的扩展能力，至少允许非 instrument 的市场事实存在。

### 11.2 事件元数据

Legacy 的通用 `MarketObservation` 具备：

- subject
- kind
- observed_at
- available_at
- source
- sequence
- payload

其中 `observed_at` 与 `available_at` 的区分很重要：前者是市场事件发生时间，后者是系统可以消费它的时间。当前 Market 应把这两个时间概念正式纳入模型，而不是只保留一个 `observed_at`。

### 11.3 已有的行情类型

Legacy 已经表达过：

- quote
- orderbook
- trade
- bar
- option_greeks
- funding_rate
- interest_rate
- curve_point
- index_value
- rate observation

其中 `Quote` 还包含 `basis` 和 `derivation`；`TradePrint` 包含 side、price、size、cost；`OptionGreeks` 包含 rho、mark price、underlying price；`RateObservation` 包含 tenor、basis、mark price。

这比当前 Rust domain 的四种 `MarketObservation` 更完整，尤其提示 Market v1 不应把 Rate 和 Index price 继续当作 schema 中的“预留字段”。

### 11.4 订单簿同步语义

Legacy 的订单簿模型已经表达了：

- snapshot 与 delta 分离。
- `first_nonce`、`last_nonce`、`previous_nonce`。
- checksum。
- stale delta 忽略。
- gap 后进入 stale 状态。
- reset snapshot 恢复。
- bid/ask 分侧排序。
- top-of-book 读取。
- update_count 和 gap_count。

当前 Rust 版本已有 sequence gap 和 resync 骨架，但应吸收 nonce cursor、checksum、统计计数和 stale 状态这些语义。

### 11.5 历史数据和回放

Legacy 的 Market application 不只处理实时流，还包含：

- MarketDataSpec。
- OHLCV、funding rate、ticker、quote、orderbook、trades、option greeks、rate、event 数据集。
- dataset resolver。
- dataset partition。
- historical download。
- append/overwrite 持久化模式。
- replay。
- subscription 到 dataset 的解析。
- live/backtest/paper/replay runtime。

因此 Market 的完整交付应区分两个应用能力：

```text
LiveMarketApplication       实时接入、同步、当前投影
HistoricalMarketApplication  数据集、查询、下载、回放
```

二者可以共享 Market domain event，但不应让实时 Actor 直接承担历史存储职责。

### 11.6 Selector 和 capability

Legacy 的 selector 不只是字符串，而是带有：

- 稳定 selector key。
- source/provider identity。
- historical/live capability。
- timeframe 是否必填。
- provider、exchange、market、driver 的能力检查。
- source check 和 doctor 结果。

当前 selector 规则已经有基础实现，但还需要把“某 provider 是否支持某个数据类型”提升为可查询的 capability contract，避免订阅成功后才发现数据类型不支持。

## 12. `kairos_v2` 的启示

`~/Code/kairos_v2` 不是当前模块的直接实现，但其中的市场实体揭示了需要补齐的语义范围。

### 12.1 数据类型应按市场层次分组

`kairos_v2/kairos-core/src/core/entities/market` 中已经区分：

- `BasicTicker`：高频 BBO。
- `Ticker24h`：24 小时统计 ticker。
- `Trade` 和 `TradeAggregate`。
- `Candle`。
- `DepthSnapshot`、`DepthUpdate`、`OrderBookLevel`。
- `MarkPrice`、`IndexPrice`。
- `FundingRate`。
- `OpenInterest`。
- `LiquidationOrder`。
- `LongShortRatio`。
- 期权 Greeks 和 instrument event。

这说明 `Quote` 不应同时承担 BasicTicker 和 24h ticker 两种语义：

```text
BasicQuote / BBO       高频最优买卖价
Ticker24h              统计窗口内的市场统计
Trade                  单笔成交
TradeAggregate         成交聚合
```

建议在 Market v1 中至少区分 `Quote` 与 `Ticker24h`，而不是把 24h 统计字段持续追加到 Quote。

### 12.2 Candle 需要完整时间和闭合语义

`kairos_v2` 的 Candle 包含：

- start timestamp
- end timestamp
- event timestamp
- interval
- UTC/UTC+8 timezone
- base volume
- quote volume
- taker buy volume
- trade count
- closed

当前 Rust `Bar` 只有一个 observed time 和一个 timeframe。后续应补充：

- start_time
- end_time
- closed
- volume_base
- volume_quote
- taker_buy_volume_base
- taker_buy_volume_quote
- trade_count
- timezone/session basis

否则实时未闭合 K 线、历史闭合 K 线和回放中的边界会混淆。

### 12.3 OrderBook 需要 depth policy 和 cursor 类型

`kairos_v2` 将 order book depth 明确分成：

- full
- top5
- top10
- top20
- BBO TBT
- L2 TBT
- L2 TBT 50

同时将不同交易所的增量 cursor 区分为：

- range cursor
- pair cursor
- simple sequence

当前 MarketFeed 只暴露统一的 `first_sequence/last_sequence`。建议保留统一 sequence，同时在 provider adapter 内保留原始 cursor，并在标准模型中增加：

```text
DepthPolicy
DepthCursor
checksum
```

这样既能支持 Binance range sequence，也能支持 OKX pair sequence 和不同的深度订阅粒度。

### 12.4 衍生品市场需要独立语义

`kairos_v2` 明确区分：

- funding rate
- funding period
- next funding time
- index price
- mark price
- estimated settlement price
- open interest contracts
- open interest quote value
- open interest 24h change
- liquidation order
- long/short ratio

这些不应全部塞入一个 Greeks 或 Rate 结构。建议按事实类型拆分，并共享统一的 subject、source 和 time metadata。

### 12.5 期权 Greeks 不只是五个 Greek 数值

`kairos_v2` 和 legacy 都提示期权行情还需要：

- mark price
- underlying price
- implied volatility
- rho
- best bid/ask price
- best bid/ask IV
- price limits
- risk-free rate
- expiry
- strike

当前 `OptionGreeks` 只覆盖部分字段，下一版应补齐，或者明确将报价字段放在 OptionQuote、敏感度字段放在 OptionGreeks，避免一个结构承担两种事实。

### 12.6 MarketContext 的缓存方式值得借鉴，但不能照搬

`kairos_v2` 的 `MarketContext` 为单个 market 缓存：

- depth
- bid1/ask1
- trade list
- ticker
- basic ticker
- kline data
- derivative data
- option data
- freshness/max age

这适合作为消费者的快速读取 view，但不适合作为 Market 的唯一原始状态模型。新架构应采用：

```text
Actor-owned canonical facts
        -> typed current views
        -> derived convenience queries
```

例如 mid price、spread、current price 可以由当前 view 计算，不需要作为独立事实持久化。

## 13. GitHub 成熟项目的语义对照

本节参考了成熟开源交易和行情项目的公开模型，重点看它们稳定交付的市场语义，而不是照搬它们的 API。

### 13.1 NautilusTrader

NautilusTrader 将以下内容作为统一市场数据类型：

- `OrderBookDelta`。
- `OrderBookDeltas`。
- 固定深度快照，例如 `OrderBookDepth10`。
- `QuoteTick`。
- `TradeTick`。
- `Bar`。
- `InstrumentStatus`。
- `InstrumentClose`。
- `FundingRateUpdate`。
- Mark price、Index price 和 Open Interest 等衍生品数据。

它还将 bar aggregation 的 price type 区分为 BID、ASK、MID、LAST，并支持 tick、volume、value、imbalance、runs 等非时间型 bar。

对本项目最重要的启示是：

1. 行情原始事实和聚合 Bar 应有清楚的输入关系。
2. OrderBook delta 是一等公民，不应只提供“当前深度”。
3. Instrument status/close 是市场可交易性事件，应有明确的事件类型。
4. Funding、Mark、Index、Open Interest 需要独立类型。
5. 历史和实时应使用相同的 typed data model，保证回测/实盘语义一致。

参考：[NautilusTrader data concepts](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/concepts/overview.md)、[NautilusTrader data types](https://nautilustrader.io/docs/latest/concepts/data/)。

### 13.2 QuantConnect LEAN

LEAN 的基础市场数据至少区分：

- Trade Tick。
- Quote Tick。
- TradeBar。
- QuoteBar。
- Open Interest。

同时将 Delisting、SymbolChanged、Split、Dividend 等事件作为独立数据类型，而不是混在价格数据里。

对本项目的启示是：

1. TradeBar 和 QuoteBar 不能混为一个 Bar。
2. 行情事件应能表达交易所、交易条件、可疑数据等质量 metadata。
3. Instrument lifecycle 和 corporate action 需要独立事件边界。
4. Open Interest 是标准市场数据，不应仅作为 provider 私有字段。

参考：[LEAN core data types](https://www.quantconnect.com/docs/v2/lean-engine/data-format/core-data-types)、[LEAN handling data](https://www.quantconnect.com/docs/v1/algorithm-reference/handling-data)。

### 13.3 CCXT

CCXT 的统一市场 API 覆盖：

- order book / L2 / L3。
- ticker。
- OHLCV。
- public trades。
- exchange time/status。
- borrow rates。
- funding rate 和 funding history。
- open interest history。
- volatility history。
- liquidations。
- Greeks 和 option chain。

它对本项目的启示是 provider capability 不能只声明“支持 MarketStream”，而应声明到数据类型和操作粒度，例如：

```text
live quote
historical quote
live orderbook
historical orderbook
funding current
funding history
open interest current
open interest history
```

参考：[CCXT manual](https://github.com/ccxt/ccxt/wiki/manual)。

### 13.4 Hummingbot

Hummingbot 的 Market Data Router 将实时和历史数据放在统一的访问面中，覆盖：

- candles。
- historical candles。
- current prices。
- order book。
- funding information。
- VWAP calculation。

对本项目的启示是：Market 对外不仅要提供原始事件，还应提供少量执行直接需要的查询：

- best bid/ask。
- mid price。
- spread。
- 给定数量的 VWAP。
- 给定数量的买入/卖出滑点估计。

这些属于由 Market 当前事实派生的 read model，不应被当作新的外部事实写回行情源。

参考：[Hummingbot API client market data router](https://github.com/hummingbot/hummingbot-api-client)。

## 14. 建议新增的市场语义

综合 Legacy、`kairos_v2` 和成熟项目后，建议将新增语义分成三层。

### 14.1 必须纳入 Market v1

这些语义会直接影响数据正确性或 Execution 判断：

- `Quote` / BBO。
- `Ticker24h`。
- `Trade`，包含 side、cost 和 trade timestamp。
- `TradeBar`。
- `QuoteBar` 或明确的 quote aggregation。
- `OrderBookSnapshot`。
- `OrderBookDelta`。
- `DepthPolicy`。
- `DepthCursor`。
- `checksum`。
- `MarkPrice`。
- `IndexPrice`。
- `FundingRate`、funding period 和 next funding time。
- `OpenInterest`。
- instrument status / close event。
- observed time、available/receive time、sequence、source。
- stale、synchronized、closed 等状态。
- per-source/per-market/per-kind freshness。

### 14.2 建议纳入 Market v2

这些语义对衍生品、跨市场和研究场景很有价值，但不应阻塞第一阶段实时闭环：

- Liquidation event。
- Long/short ratio。
- Borrow rate。
- Interest rate / curve point。
- OptionQuote 与完整 OptionGreeks 拆分。
- Option chain。
- Implied volatility surface。
- Volatility history。
- Trade aggregate。
- Taker buy volume、trade count。
- 非时间型 Bar：tick、volume、value、imbalance、runs。
- corporate action 和 symbol change 的 Market consumer projection。

### 14.3 作为派生查询，不作为原始事实

这些能力可以由 Market current view 计算或投影：

- mid price。
- spread 和 spread percentage。
- top-N depth。
- depth imbalance。
- notional depth。
- 给定数量的 VWAP。
- 预估滑点。
- 可成交数量。
- cross-source best price。
- cross-source spread。

其中 `cross-source best price` 和 `cross-source spread` 只有在明确存在统一 source selection policy 后才进入 Market；否则应留给策略或 Execution application。

## 15. 更新后的优先级

基于本次复盘，近期工作顺序调整为：

1. 固定 `subject + market/instrument + source + kind` 的统一身份模型。
2. 拆分 `Quote`、`Ticker24h`、`TradeBar` 和 `QuoteBar`。
3. 补齐 OrderBook cursor、depth policy、checksum 和 stale 状态。
4. 纳入 MarkPrice、IndexPrice、FundingRate 和 OpenInterest。
5. 增加 observed time 与 available/receive time。
6. 增加 instrument status/close 的 Market 消费语义。
7. 将 provider capability 细化到数据类型、实时/历史和操作粒度。
8. 增加 mid、spread、VWAP、slippage 等只读派生查询。
9. 再引入 liquidation、long/short ratio、borrow rate 和更复杂的期权/波动率语义。

核心原则仍然是：先把原始市场事实、顺序、质量和新鲜度做正确，再增加复杂的派生指标和跨市场聚合。

## 16. 本轮落地验收状态

本设计已经落实到以下可执行交付物：

- domain：`Ticker24h`、`MarkPrice`、`IndexPrice`、`FundingRate`、`OpenInterest`、`Rate`、`TradeBar`、`QuoteBar`、`InstrumentStatus`、`MarketFreshness`；
- application：上述类型的 typed query、selector、source-aware order book query；
- application：基于 current view 的 `mid_price`、`spread`、Decimal 精确 VWAP/slippage execution estimate 派生查询；
- contract：typed readers 已覆盖 quote、trade、bar、trade bar、quote bar、greeks、rate、24h ticker、mark/index price、funding、open interest、freshness 和 instrument status；
- integration：normalized `MarketEventKind` 到 Market domain observation 的映射；
- integration：新增 `MarketDataKind` / `MarketStreamCapabilities`，区分实时、历史和订单簿 resync 能力，并由 Binance Spot/Derivatives、Massive 等连接器声明实际能力；
- contract：current/history schema、事件消息 schema、订单簿 depth/cursor/checksum 字段、Rust/Python 生成物，以及 typed snapshot reader；订单簿 contract model 与 domain 的嵌套 cursor 结构保持兼容。
- verification：Market service、Market contract、Integration 的 all-targets 编译与测试已通过，workspace `cargo check --workspace --all-targets`、`cargo test --workspace --all-targets`、`cargo fmt --all -- --check` 与 `git diff --check` 均已通过。Market actor fixture 已同步当前 Reference contract 约束。Python 全量测试当前为 130 passed / 1 failed，唯一失败在与 Market 无关的 `test_process_launch.py` 进程健康等待；Market 相关 Python transport/command 测试为 19 passed。

Provider 是否真正提供某一种衍生品数据，仍由 Integration provider capability 和具体连接器决定；Market 不会伪造 provider 没有给出的事实。
