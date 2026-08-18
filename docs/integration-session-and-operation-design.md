# Integration 细粒度连接与 Conflux 资源模型

## 0. 文档状态

- 状态：当前实施基线已落地并通过集中验证（2026-08-18）。
- 范围：`crates/platform/integration` 的 participant-native 连接，以及
  `crates/platform/conflux` 对这些连接的持有、生命周期和事件接入。
- 首批 provider：OKX、Binance Stocks、Binance Spot、Binance USD-M、Binance COIN-M 和
  Binance Options。
- 本文是 `docs/integration-adapter-references/` 的架构基线。各 provider note 记录协议证据、
  上游参考与迁移状态，不另行定义连接模型。
- 本文不授权一次性重写所有 provider。迁移按 provider、API family、transport 切片进行；
  新路径通过退出条件后，删除同一切片的旧 facade、旧 connection 和重复 session。
- Integration connection 实施范围仍只包含 `crates/platform/integration` 与
  `crates/platform/conflux`。业务 modules 可按
  `docs/proposal/module-conflux-adoption.md` 同步进行 M0-M2 的所有权清理、typed dispatch 与
  composition 准备，但不得依赖本提案正在删除的 facade/principal API，也不得提前固化过渡期
  `ConfluxSystem`。Integration 旧 API 被删除后，modules 暂时不能编译仍是当前阶段明确允许且
  预期的状态；module 的 Conflux runtime cutover 不属于本提案的 provider slice 退出条件。

## 1. 决策摘要

本提案作出以下决定：

1. Integration 的长期资源单位是细粒度 concrete connection，不是聚合整个 provider 的大
   facade。
2. `Connection` 表示可执行 provider I/O 的具体链路，不是 HTTP 的同义词。REST、request/
   response WebSocket、push stream、FIX、TCP/library session 都可以是 connection；同一个
   order command trait 可以由 REST connection 和 WebSocket API connection 分别实现。
   endpoint、限流、故障、重连和替换语义不同的链路必须独立管理。
3. public/private 只有在 provider 协议确实定义了不同 endpoint、认证或 session 时才拆分；
   它不是强加给所有 provider 的固定矩阵。API-key 认证的公共市场数据也不自动成为 private
   account connection。
4. `Principal` 表示认证身份，不表示网络连接。第一版不把 `OkxPrincipal`、
   `BinancePrincipal` 或 `ManagedPrincipal` 暴露为 Conflux 资源。
5. 同一认证身份需要被 private REST 和 private WebSocket 共享的 credential、user/account
   identity、clock 或 quota runtime，是 Integration 内部共享上下文，不是 Conflux
   connection。
6. WebSocket channel/subscription 是 provider adapter 内部协议状态。一个 WebSocket
   connection 可以承载多个 channel；它们随 socket epoch 一起失效和恢复，但 Integration
   不为 private channel 再公开一套通用 subscription enum/trait。
7. concrete connection 直接实现 Integration-owned capability traits。不得为 provider 再
   创建平行 projection/registry/operation 层；但现有 trait 无法忠实表达交易所真实协议时，
   必须基于协议证据扩展它，必要时新增稳定 capability trait，而不是强迫 provider 适配错误
   抽象。
8. composition 决定创建几条物理 WebSocket，以及哪些 subscription 共享同一条连接。
   Integration 实现登录、订阅确认、demultiplex、心跳、重连和重新订阅；Conflux 不替 provider
   实现这些协议。
9. Conflux 只持有当前系统明确支持的 concrete connection named lists，不持有 provider
   facade、principal、capability registry 或订阅目录。
10. Conflux 不使用 type erasure、`Any`、`TypeId`、universal `Connection` trait、通用
    provider operation enum 或 `execute(operation, payload)`。

## 2. 当前问题

当前 Integration 混用了四种不同概念：

```text
provider/egress context
credential/principal context
physical or logical transport connection
capability/event source
```

例如当前 OKX 模型是：

```text
OkxConnection
├── public HTTP clients
├── public capability factories
└── creates OkxPrincipalConnection
    ├── credential and private REST client
    ├── creates OkxTradingAccountEvents -> owns one private socket
    └── creates OkxTradingOrderEvents   -> owns another private socket
```

这里存在三个边界错误：

- `OkxConnection` 实际是 provider/egress context，不是一条完整连接；
- `OkxPrincipalConnection` 实际是 credential/private REST context，不是一条 private
  WebSocket；
- `OkxTradingAccountEvents` 与 `OkxTradingOrderEvents` 才持有 socket 和 connection
  lifecycle，但名称又把 transport 所有权隐藏成 capability。

当前 Binance 也存在类似问题。四个 API family connection 表达 endpoint family，方向基本
正确；但 `BinanceSpotPrincipalConnection`、`BinanceFuturesPrincipalConnection` 与
`BinanceOptionsPrincipalConnection` 主要表达 credential-bound client，而
`BinanceMarginPrincipalConnection` 甚至只是从 Spot principal 克隆 client 后形成的 domain
projection。把这些类型全部平铺进 Conflux 会给共享的 credential/client 创建重复的
revision、epoch 和 lifecycle。

### 2.1 直接风险

- 父 provider context 替换后，已派生的 private context 可能继续持有旧 HTTP worker 或 quota；
- credential rotation 与 WebSocket reconnect 被混成一种 lifecycle；
- Account 和 Order capability 可能在 composition 不知情的情况下隐式创建两条私有 socket；
- Margin projection 与 Spot principal 对同一 client 形成两个 managed resource；
- Actor 必须理解哪些 `*Connection` 是根资源、哪些只是 factory 或 projection；
- Conflux 将 Integration 尚未稳定的内部结构固化为系统 API。

## 3. 术语与资源判定规则

### 3.1 Connection

Connection 是拥有独立技术生命周期的 concrete transport resource。

“transport”在这里表示实际 I/O 路径，而不要求 provider 主动 push。一个 REST connection
不仅可以执行 bounded query/command，也可以持有 polling scheduler、cursor、dedup 与 bounded
event queue，用周期性 REST query 实现 `AccountStream`、`ExecutionStream` 或
`MarketDataStream`。因此 capability 不按 HTTP/WS 命名，Connection 也不是 socket 的同义词。

对于 WebSocket，它通常对应一条实际 socket/session，并拥有：

- endpoint；
- connect/login handshake；
- connection epoch；
- heartbeat；
- bounded inbound queue；
- reconnect/resubscribe；
- terminal close。

对于 REST，connection 是长期复用的逻辑 HTTP transport runtime，可能内部使用 connection
pool，不要求永远对应一条 TCP socket。它拥有：

- base URL 与环境；
- HTTP client/pool；
- signing 或 public request middleware；
- clock/time synchronization；
- transport retry policy；
- provider/IP/user quota runtime；
- close/drop lifecycle。

REST 与 WebSocket 即使共享 endpoint family 或 credential，也不能因为属于同一 provider 就
合并为一个 managed connection。

同一种能力可以由多个 concrete connection 实现。当前 provider-push 实现直接由
`BinanceSpotWebSocketConnection` 实现 `MarketDataStream`；当出现明确的 polling 调用者时，
相应 REST connection 也可以拥有 scheduler、cursor、baseline 与 bounded queue，并实现同一
trait。第一版不为证明抽象而创建一个无人调用的 polling wrapper。

```rust,ignore
impl MarketDataStream for BinanceSpotWebSocketConnection { /* provider push */ }
```

Actor/composition 明确选择 named connection。Conflux 不自动把 WS failover 到 REST polling，
因为两者的 latency、freshness、ordering、quota 和恢复语义不同；若需要 fallback，必须由明确
composition policy 和 Actor readiness/freshness 规则决定。

### 3.2 Private shared context

Private shared context 是 Integration 内部实现细节，例如：

```rust,ignore
struct OkxPrivateShared {
    credentials: RotatingOkxCredentials,
    user_id: Option<String>,
    private_query_quota: SharedFixedWindowQuota,
    private_order_quota: SharedFixedWindowQuota,
}
```

它可以通过 `Arc` 被多条 private REST/WebSocket connection 共享，但不进入
`ConfluxSystem`，也不拥有独立的 Conflux revision/epoch。实现可以使用临时 builder 或
connection factory 创建共享上下文后返回可分别持有的 concrete connections；该 builder
不是运行时 facade。

### 3.3 Capability traits 重新设计

本轮不保留当前 trait 名称作为兼容约束。当前命名存在三个问题：

- `Async*` 把默认执行模型写进名称；新 Integration 默认 async，blocking adapter 只留在
  `kairos_integration::blocking`；
- `*Connection` 同时被 capability trait 和 concrete transport struct 使用，混淆能力与资源；
- 部分 trait 按历史实现切得过碎，部分又把 lifecycle、command 和 event 混在一起。

命名规则是：只有拥有具体 I/O 链路和生命周期的 struct 使用 `*Connection`；trait 使用业务
能力名。目标能力集合先按以下语义收敛，最终方法由 provider inventory 与当前调用者共同验证：

| 领域 | 目标 trait | 语义 |
| --- | --- | --- |
| Reference | `InstrumentCatalogQuery` | instrument discovery/catalog query |
| Account | `AccountQuery` / `AccountMarketProfileQuery` | account snapshot 与 market/account profile query |
| Account | `AccountStream` | normalized balance/position/account event stream |
| Security | `AccountCredentialQuery` | remote credential permission/status query |
| Execution | `OrderCommand` | submit、cancel、amend、cancel-all、batch 等 command |
| Execution | `OrderQuery` | open/history/detail/trade query |
| Execution | `ExecutionStream` | normalized order/fill/execution event stream |
| Multiplexed provider stream | `ParticipantEventStream` | one physical reader returning a closed account/execution/market event enum |
| Market | `MarketQuoteQuery` / `MarketTradeQuery` / `MarketBarQuery` / `MarketOrderBookQuery` | typed bounded current-data query |
| Market | `MarketTickerQuery` / `MarketMarkPriceQuery` / `MarketIndexPriceQuery` | typed ticker and reference-price query |
| Market | `MarketFundingRateQuery` / `MarketOpenInterestQuery` / `MarketGreeksQuery` / `MarketStatusQuery` | typed derivative and status query |
| Market | `MarketSubscriptionCommand` | subscribe/unsubscribe command 与异步 ACK 结果 |
| Market | `MarketDataStream` | ordered live event consumption；唯一方法名为 `next` |
| Market | `HistoricalBarQuery` / `HistoricalQuoteQuery` / `HistoricalTradeQuery` | typed bounded historical window query |
| Funding | `FundsTransferCommand` | typed transfer command |
| Funding | `EarnQuery` | product/position/reward query |
| Funding | `EarnCommand` | subscribe/redeem command |

旧到新的迁移不是纯 rename：

| 当前 | 目标 |
| --- | --- |
| `AsyncInstrumentCatalogConnection` | `InstrumentCatalogQuery` |
| `AsyncAccountReadConnection` + `AsyncAccountMarketProfileConnection` | `AccountQuery` + `AccountMarketProfileQuery` |
| `AsyncAccountEventSource` | `AccountStream` |
| `AsyncAccountCredentialInspectionConnection` | `AccountCredentialQuery` |
| `AsyncOrderEntryConnection` | 扩展为 `OrderCommand` |
| `AsyncOrderQueryConnection` | `OrderQuery` |
| `AsyncOrderEventSource` | `ExecutionStream` |
| `AsyncMarketSnapshotConnection` + `AsyncMarketQuoteConnection` | 按具体事实拆为 `*Query`；删除 snapshot facade |
| `AsyncMarketEventSource` | 拆为 `MarketSubscriptionCommand` + `MarketDataStream` |
| `AsyncHistoricalMarketDataConnection` | 按 bar/quote/trade 拆为具体 historical Query |
| `AsyncTransferConnection` | `FundsTransferCommand` |
| `AsyncEarnConnection` | 拆为 `EarnQuery` + `EarnCommand` |

这些名字表达的是候选最终边界，不要求机械地一对一重命名旧 trait。如果 provider 事实证明两个
能力必须分开，或一个 trait 仍混合 command/query/stream，就继续调整。新增 trait 必须有真实
provider 行为和当前 Integration/Conflux caller；不得仅为某个 provider 换一套同义名字。

后缀是强语义约束：`*Query` 只能做 bounded read；`*Command` 表示有副作用且必须报告 delivery
certainty；`*Stream` 表示长生命周期、有 ordering/backpressure/recovery 语义的事件流，只有
`next` 消费入口；subscribe/unsubscribe 属于独立 `*Command`。receiver 不做全局强制；第一版所有
async-first capability trait 统一从 `Send` + `&mut self` 开始。这允许 connection 直接拥有
mutable HTTP client、socket、cursor 或 library session，不要求为了满足 trait 提前增加
interior mutability、`Arc<Mutex<_>>` 或 `Sync`。Query/Command 保留完整业务动词；所有 Stream
统一暴露 `next`，trait 本身负责给出 account/execution/market 语义，必要时使用 UFCS 消歧。

`capabilities/` 与 `blocking/` 只拥有 trait，不拥有 request/result/event/subscription 类型。
所有稳定对象归入 `domain/`；participant wire payload 留在对应 participant 内部。blocking
使用与 async surface 相同的 trait 名，依靠 `kairos_integration::blocking::*` namespace 区分，
不再使用 `Blocking*` 前缀，也不 re-export concrete participant objects。

一个 concrete connection 可以实现多个 trait，同一个 trait 也可由多种 transport 实现：

```rust,ignore
impl OrderCommand for BinanceSpotRestConnection { /* HTTP */ }
impl OrderCommand for BinanceSpotWebSocketApiConnection { /* WS request/response */ }

impl AccountStream for OkxPrivateWebSocketConnection { /* shared socket */ }
impl ExecutionStream for OkxPrivateWebSocketConnection { /* same shared socket */ }
impl ParticipantEventStream for OkxPrivateWebSocketConnection { /* one physical reader */ }
```

当一条物理 connection 同时承载多个领域频道时，Conflux 必须使用
`ParticipantEventStream::next` 启动唯一 reader；不能并行轮询同一对象上的多个领域 Stream。
单领域调用者仍可直接使用 `AccountStream`、`ExecutionStream` 或 `MarketDataStream`。该统一
Stream 是物理多路复用协议的直接能力，不创建 handle/projection，也不擦除 concrete type。

Actor/composition 选择具体 named connection，不由 framework 在 REST 与 WS 间隐式路由。两种
connection 可以复用相同的 typed request/result 和 delivery-certainty 语义，但各自保留真实
quota、timeout、correlation 和 recovery 行为。

### 3.4 MarketDataStream 必须支持异步 control/event 交错

当前 `AsyncMarketEventSource` 不成立为最终边界，因为它把 connect lifecycle、subscribe/
unsubscribe command 和 event consumption 混在一个要求 `&mut self` 的 trait 中。当前
`MarketSubscription` 也只有 `symbols`，迫使不同 adapter 私自决定频道：Binance Spot
硬编码 trade/bookTicker/depth/kline，OKX 硬编码 books/trades，Massive 只订阅 quote，
derivatives 又只订阅 bookTicker。这不是统一能力。

目标移除 lifecycle，并拆开 command 与 stream：

```rust,ignore
pub trait MarketSubscriptionCommand: Send {
    fn subscribe(
        &mut self,
        request: MarketSubscriptionRequest,
    ) -> impl Future<Output = MarketSubscriptionOutcome<MarketSubscription>> + Send;

    fn unsubscribe(
        &mut self,
        subscription: MarketSubscriptionId,
    ) -> impl Future<Output = MarketSubscriptionOutcome<()>> + Send;
}

pub trait MarketDataStream: Send {
    fn next(&mut self) -> impl Future<Output = Result<MarketEvent, IntegrationError>> + Send;
}
```

`&mut self` 只限制外部 capability calls 串行，不允许它阻塞 connection 内部 I/O。WS
connection 在 `subscribe()` 等待 response/ACK 时仍由其内部 reader/pump 持续读取 raw socket
并缓存 market events；method 返回后，调用者再通过 `MarketDataStream::next()` 按序消费。调用者不能
持有 raw socket，也不能让两个外部 future 竞争读取同一个 socket。

未来若真实 caller 必须同时执行 subscription control 与 event consumption，可以在取得测试和
性能证据后，把该 trait 改为 `&self + Sync`，或者拆分明确的 control/stream owner。receiver 是
每个 trait 的协议设计选择，不要求所有 Integration traits 永远一致；但不能只为了“看起来
async”就给所有实现强加 interior mutability。

`MarketSubscriptionRequest` 必须显式列出 feed，而不是只给 symbols：每项至少包含 optional participant
symbol、`MarketDataKind`、bar interval、book depth/update speed 等已稳定的 typed selection。
无 symbol 的 calendar/all-market feed 也必须可表达。provider 独有且尚未形成共享语义的字段
留在 concrete typed request/method，不使用 JSON options map。

`MarketSubscription` 是 confirmed receipt，至少包含 local subscription ID、connection
epoch、实际确认的 feeds 与 confirmation time。`MarketSubscriptionOutcome<T>` 明确区分
`Confirmed(T)`、`Rejected` 和 `Indeterminate`，不能用 `Result<MarketSubscriptionId, _>` 把 response
丢失误报成普通失败。

### 3.5 WebSocket command/event dispatcher

每个同时承载 command response 与 push event 的 connection 只有一个 raw socket reader。内部
dispatcher 按 frame 类型路由：

```text
raw WebSocket frame
    -> command response / ACK, by correlation id -> pending command waiter
    -> market/account/order event               -> bounded ordered event queue
    -> ping/pong/error/control                   -> connection lifecycle
```

`subscribe()` 发送命令后不能立即返回成功。它只在 provider ACK 确认后返回
`Confirmed(MarketSubscription)`；明确拒绝返回 `Rejected`；timeout、断线或 response 丢失返回
`Indeterminate` 并触发 reconnect/reconcile。对于 Binance Stocks 这类 URL 即订阅且没有 ACK 的
协议，新的 socket/combined-stream handshake 成功就是该 provider 定义的 confirmation point，
不能伪造不存在的 ACK。

等待 ACK 期间到达的行情必须按接收顺序进入 bounded event queue，ACK 前后都不能丢。已有
subscription 的 event 可继续被 `next_event()` 消费；新 feed 的 pre-ACK event 至少要保留到
结果确定。若队列溢出，connection 进入 degraded/resync-required，pending command 失败，不能
丢事件后仍报告订阅成功。unsubscribe、login 和 WS order command 使用同一 correlation/
dispatch 原则。

重连只恢复已经 confirmed 的 desired set。`Indeterminate` command 不直接写入 confirmed set；
connection 必须通过 provider query、list-subscriptions（若有）或 clean reconnect 重新建立确定
状态。每个 response、event 和 subscription receipt 都绑定 connection epoch，旧 epoch frame
不能完成新 epoch waiter。

同一 connection 上的 subscription mutations 默认串行化，但 raw reader 永不暂停。一个
subscribe/unsubscribe command pending 时，dispatcher 将期间到达的 market events 放入有界
interleaved buffer；confirmation 后按原接收顺序释放给 `MarketDataStream`。若明确 rejection 前
已经收到只可能属于新 feed 的 event，provider 状态自相矛盾，应按 `Indeterminate` 处理并
reconcile，而不是丢掉 event 后返回 `Rejected`。timeout 或 buffer overflow 同样要求
resync/reconnect。

第一版由 Conflux 对每个 concrete connection 保持唯一 mutable owner，并串行调用其 capability
methods。需要并行运行的 raw reader、polling loop、ACK waiter 和 event buffer 是 connection
内部实现，不通过 cloneable capability handle 暴露。每种 stream trait 默认只允许一个逻辑
consumer；额外 fan-out 必须发生在 normalized `ConfluxEvent` 之后，不能多人竞争底层 queue。

OKX 的 private channel、Binance user-data event 以及其他 provider channel name 仍留在各
concrete adapter 内部；重新设计共享 market subscription 不意味着增加
`OkxPrivateSubscription` 之类的 provider 能力枚举。

当前实现审计已经确认该改造不是预防性设计：OKX、Binance、Hyperliquid 与 Massive 的 market
`subscribe()` 都在写出 frame 后立即返回 local ID，没有等待 provider ACK；随后的 event loop
又跳过 ACK/status frame。当前代码既无法证明订阅成功，也没有统一保证等待确认时的 interleaved
market events。迁移测试必须构造 `event -> event -> ACK`、`event -> rejection`、ACK timeout、
buffer overflow 与旧 epoch late ACK 五种顺序。

### 3.6 REST polling 也是 event connection

REST polling implementation 直接实现同一组 query/subscription/event traits，不建立
`PollingAdapter`、fake WebSocket 或第二套 event API。它至少包含：

- typed polling subscription 与 interval/jitter policy；
- provider cursor、watermark 或 overlapping time window；
- snapshot diff、dedup 和 stable local sequence；
- request quota 与 bounded concurrent polls；
- bounded ordered event queue；
- poll timeout、partial page、rate limit 与 recovery；
- shutdown/cancellation 和 connection epoch fencing。

Polling subscription 没有 provider ACK。它的 confirmation point 是：request 已验证、poll job
已安装，并按 capability contract 获得首个有效 baseline；只有完成 baseline 后才能返回
`Confirmed`/Ready。首轮 query 期间其他 poll job 产生的 events 继续进入 bounded queue。若首轮
失败或结果不完整，返回 `Rejected` 或 `Indeterminate`，不能先报告成功再异步暴露初始化失败。

Polling 不能伪造 provider push 语义：

- REST order-book snapshot 仍是 `BookSnapshot`，不能凭两次 snapshot 自动宣称 provider-native
  `BookDelta`；
- REST account/order diff 产生的是 Kairos observed transition，不能伪造 provider event ID；
- polling 漏掉的中间状态必须被 delivery contract 承认，不能宣称完整逐事件 replay；
- provider observation time、request start/end、received time 与 local sequence 必须可区分。

`MarketSubscription`/event envelope 必须携带 delivery metadata，例如
`DeliveryMode::ProviderPush` 或 `DeliveryMode::Polling { interval }`，以及 connection epoch 和
freshness timestamps。`MarketStreamCapabilities` 也要从当前简单的 `realtime/historical` 集合
升级为按 data kind 描述 push、poll、snapshot、history、resync 与最小 cadence；业务层才能
正确设定 stale threshold 和 route preference。

### 3.7 判断一个类型是否进入 Conflux

一个 Integration 类型只有同时满足以下条件，才进入 `ManagedConnections`：

1. 它是当前生产 composition 直接创建和使用的 concrete provider resource；
2. 它拥有独立 connect/ready/degraded/stop/drop 生命周期；
3. 它的 replacement 不必隐式替换另一种 transport；
4. 它不是 credential、配置、descriptor、trait receiver 或 provider channel；
5. 它的多个命名实例具有真实用途，例如不同环境、出口、账户、隔离级别或故障域。

### 3.8 Integration 内部架构允许破坏性重组

当前 `application/participants`、`application/capabilities`、`services/participants` 与大型
`connection.rs` 是历史结构，不是本提案必须兼容的架构。为了形成清晰的领域所有权，本轮允许
移动、重命名、拆分和删除 Integration 内部模块、public exports 与 tests；不建立旧路径
re-export 或 compatibility facade，也不因 business modules 无法编译而停止 framework
重构。

目标目录按“领域对象、公共能力、participant 实现、共享 transport/composition”组织，例如：

```text
crates/platform/integration/src/
├── capabilities/              # public Query / Command / Stream traits
│   ├── connection.rs
│   ├── reference.rs
│   ├── account.rs
│   ├── execution.rs
│   ├── market.rs
│   └── funding.rs
├── blocking/                  # 同名同步 Query / Command / Stream traits
│   ├── connection.rs
│   ├── reference.rs
│   ├── account.rs
│   ├── execution.rs
│   ├── market.rs
│   └── funding.rs
├── domain/                    # typed request/result/fact/delivery semantics
│   ├── connection.rs
│   ├── operation.rs
│   ├── account.rs
│   ├── execution.rs
│   ├── market.rs
│   ├── funding.rs
│   └── reference.rs
├── participants/              # participant-native concrete connections
│   ├── okx/
│   ├── binance/
│   │   ├── spot/
│   │   ├── margin/
│   │   ├── usdm/
│   │   ├── coinm/
│   │   └── options/
│   ├── massive/
│   ├── hyperliquid/
│   └── ibkr/
├── services/                  # private reusable signing/quota/codec/normalizer/dispatcher
├── composition/               # credential/config assembly for Integration entry points
└── transport/                 # private HTTP / WS / TCP primitives
```

这不是要求为空 family 建目录，而是 ownership 规则：

- capability/blocking 只拥有 traits；所有稳定对象归入 domain，且不依赖 participant；
- participant family 拥有 endpoint、auth、wire types 与 concrete connections；
- services 只提供被多个 concrete connections 复用的私有机制，不定义第二套 public wrapper 或
  真正的 lifecycle owner；
- generic transport 不包含 Binance/OKX vocabulary；
- dispatcher/polling/quota 只有确实复用时才进入 `services/`，否则留在 participant family；
- participant wire payload 不得从 `participants/` 穿越 public capability boundary；
- `lib.rs` 只导出 public traits/domain objects 和已经落地的 concrete connection types；
- Conflux 依赖 concrete connection types 与 Query/Command/Stream traits，不依赖 participant 内部
  protocol、transport 或 service module。

文件名只表达一个单词；复合概念必须拆成嵌套模块。每个 participant family 按真实 transport
拆为 `rest.rs`、`websocket.rs`，需要继续拆分时使用 `market/stream.rs`、`order/command.rs`
这类嵌套路径，不使用 `public_rest.rs`、`market_data.rs`、`order_events.rs`。不得恢复包住所有
链路的 participant facade，也不得把所有 wire requests 堆回单个 `connection.rs`。

## 4. OKX 目标模型

OKX 不需要一个包住所有能力的运行时 `OkxConnection`。目标 concrete resource 是：

```text
OkxPublicRestConnection
OkxPublicWebSocketConnection
└── public subscriptions

OkxPrivateRestConnection
└── shared private context

OkxPrivateWebSocketConnection
├── shared private context
└── private subscriptions
    ├── account
    ├── orders
    ├── positions
    └── balance-and-position
```

当实际 capability 使用 `/ws/v5/business` 时，再增加
`OkxBusinessWebSocketConnection`。Business WebSocket 与 `/ws/v5/private` 是不同 endpoint
和连接生命周期，不能为了都需要登录而合并；当前未使用的 Business capability 不提前进入
Conflux。

`InstrumentType::{Spot, Margin, Swap, Futures, Option}` 是 provider request/filter 维度，
不是五种 connection。`TradingMode::{Cash, Cross, Isolated}` 是 operation/account mode
维度，也不是 connection。

同一个 OKX unified account 可以拥有多个 API key；同一个 API key 也可以建立多条 private
WebSocket 以隔离 Account、Execution 或不同吞吐域。因此 private connection 使用业务明确的
命名 key，而不是仅以 Rust type 或 principal ID 自动路由。

### 4.1 官方链路核对

2026-08-17 按 OKX V5 官方 API Guide 核对：

- Production public WebSocket 是 `/ws/v5/public`；
- 普通 private WebSocket 是 `/ws/v5/private`，建立连接后先 login；
- `account`、`orders`、`positions` 与 `balance_and_position` 都是该 private endpoint 上的
  channel；
- 一次 subscribe 可以携带一个或多个 channel argument，因此 `account` 与普通 `orders`
  可以共享同一个已登录 socket；
- login/subscribe/unsubscribe 请求额度按 connection 计算；受限频道的连接数按 sub-account、
  channel 计算，官方明确说明不同 channel 使用同一条或不同 connection 都按各自 channel
  统计；
- algo order 等部分频道位于 `/ws/v5/business`，不能放入普通 private socket。

官方来源：<https://www.okx.com/docs-v5/en/>。

因此不能把 `OkxTradingAccountEvents` 与 `OkxTradingOrderEvents` 理解成两种交易所要求的
物理链路。它们首先是同一 private WebSocket 协议上的两个逻辑消费需求；是否拆成两条 socket
是本地部署与故障域决策。

当前代码的真实行为是：

- `OkxTradingAccountEvents::connect_channel()` 新建一条 socket，并在同一次 subscribe 中同时
  订阅 `account` 和 `orders`；其 parser 也同时把两个 channel 转成 Account facts；
- `OkxTradingOrderEvents::connect_channel()` 另外新建一条 socket，只订阅 `orders` 并转成
  Execution facts；
- 当两个 capability 位于不同业务进程时，它们无法共享内存中的 socket，分别连接是正常的；
- 当它们位于同一进程、同一 principal 和同一故障域时，再创建第二条 `orders` socket 必须是
  composition 的显式选择，不能由 capability factory 隐式产生。

目标默认策略是：同一进程、principal、endpoint 与故障域先创建一条
`OkxPrivateWebSocketConnection`，在其上复用 Account、Orders、Positions 等 subscriptions；
只有跨进程、独立故障隔离、吞吐或 provider 限制提供证据时，才创建第二个命名 connection。

### 4.2 OKX public REST

`OkxPublicRestConnection` 直接提供当前 public REST 能力：

- instrument catalog；
- market snapshot；
- provider time 或其他明确的 public query。

它拥有 IP/egress scope quota，不持有 private credential。

### 4.3 OKX public WebSocket

`OkxPublicWebSocketConnection` 拥有一条 public socket 和一组 desired subscriptions。行情
频道的 snapshot/delta continuity、checksum 和 resync 仍由具体 Integration stream 能力与
消费它的业务 source driver 按现有所有权处理；Conflux 只监督 connection/source task，不能
成为 Market 状态 owner。

### 4.4 OKX private REST

`OkxPrivateRestConnection` 提供 account query、order entry/query 和 credential inspection。
它引用 private shared context，并遵守 User ID、instrument 或 instrument family 维度的真实
quota。command 在可能已发送后不得透明重试，query 只在 provider 语义允许时做有界重试。

### 4.5 OKX private WebSocket

`OkxPrivateWebSocketConnection` 负责：

- socket connect；
- login；
- 多 channel subscribe acknowledgement；
- channel demultiplex；
- heartbeat；
- connection count/error 处理；
- bounded queue 与 backpressure；
- reconnect 后重新登录和恢复 desired subscriptions。

同一进程、principal、endpoint 和故障域中的 Account 与普通 Orders 默认共用 socket。
composition 可以基于明确的进程边界、故障隔离或吞吐需求创建多个命名 socket；不能再由调用
`trading_account_events()` 或 `trading_order_events()` 隐式决定。

## 5. Binance 目标模型

Binance 与 OKX 不应被强行做成同一种 participant facade。Binance 的 endpoint、签名路径、quota
和 stream 协议按 API family 分离。第一版公共目录直接按产品族和传输组织：

- Spot API family；
- USD-M Futures API family；
- COIN-M Futures API family；
- Options API family；
- Advanced Trading 下的 Portfolio Margin、Portfolio Margin Pro、Algo、Copy、Institutional
  Loan、Alpha 与 Stocks Trading。

第一步先建立以下 concrete connection owner；能力迁入时仍须按真实物理协议检查是否需要在
对应 `rest.rs` 或 `websocket.rs` 内继续拆出 WebSocket API、market stream 和 user-data
stream 类型，不能用一个对象隐藏多条独立 socket：

```text
BinanceSpotRestConnection
BinanceSpotWebSocketConnection
BinanceMarginRestConnection
BinanceMarginWebSocketConnection
BinanceUsdMRestConnection
BinanceUsdMWebSocketConnection
BinanceCoinMRestConnection
BinanceCoinMWebSocketConnection
BinanceOptionsRestConnection
BinanceOptionsWebSocketConnection

BinancePortfolioMarginRestConnection
BinancePortfolioMarginProRestConnection
BinanceAlgoTradingRestConnection
BinanceCopyTradingRestConnection
BinanceInstitutionalLoanRestConnection
BinanceAlphaTradingRestConnection
BinanceAlphaTradingWebSocketConnection
BinanceStocksRestConnection
BinanceStocksWebSocketConnection
```

没有真实生产能力的组合不提前创建空类型；上表是目标 inventory，按 adapter slice 落地。

### 5.1 Spot、Funding 与 Margin

Spot REST/auth scope 当前同时承载 Spot、Funding、Cross Margin 和 Isolated Margin 能力，但这
不意味着它们都是独立 principal connection：

- Spot、Funding、Cross Margin 与 Isolated Margin 的 private REST operation 可以复用 Spot
  auth/HTTP family，但仍保留各 endpoint 的 provider-native request 语义；
- Isolated Margin subscription 需要 provider symbol 时，该 symbol 是 subscription/route
  参数；
- `BinanceMarginPrincipalConnection` 不再作为 managed connection；
- Cross/Isolated projection 不拥有独立 credential revision；
- 如果 provider 协议要求不同的 listen-key/socket，则创建不同的 concrete private
  WebSocket connection，并用命名实例表达故障域。

Spot 当前官方 User Data Stream 是 Spot WebSocket API connection 上的 subscription，不是
独立 endpoint，因此由 `BinanceSpotWebSocketApiConnection` 持有。它同时推送
balance/account update 与 `executionReport` order/fill update；同一进程和 account scope
不应分别为 Account 与 Execution parser 隐式建立两条相同 connection。Cross Margin 与
Isolated Margin 使用各自的 user-data stream/listen-key 语义；isolated stream 还带 symbol
scope，不能仅作为 Spot connection 上的逻辑 parser。

### 5.2 Futures 与 Options

USD-M、COIN-M 和 Options 保持不同 family connection。一个 Rust type 不应通过隐藏的
`ConnectionDomain` 同时表示 USD-M 与 COIN-M managed list；类型名必须直接表达 endpoint
family。

one-way/hedge position mode、margin mode 和其他账户模式是 private account profile 或
operation 参数，不自动成为 connection 类型。Portfolio Margin、Unified Account 等只有在
完成官方 capability inventory 并确认其 endpoint/auth/quota/session 边界后，才增加对应
concrete connection；不能先把它塞入 Spot 或 Futures 的通用 enum。

### 5.3 官方链路核对与当前代码

2026-08-17 按 Binance 官方文档核对：

- Spot public market streams 使用 `stream.binance.com`，一条连接可通过 `SUBSCRIBE` 携带多个
  stream name，也支持 combined stream；
- Spot WebSocket API User Data Stream 在当前 WebSocket connection 上建立 subscription，
  同一 subscription 推送账户余额变化和 `executionReport` 订单/成交变化；
- Spot User Data Stream 可以使用 authenticated session subscription，或
  `userDataStream.subscribe.signature`；
- USD-M 既有独立 WebSocket API，也有 listen-key user-data stream；后者由 REST 创建/续期
  listen key，当前官方文档声明 60 分钟失效窗口，因此二者不能合成一个 connection；
- USD-M、COIN-M、Options 和 Margin 的具体 listen-key、endpoint、event inventory 必须按
  family 分别保留，不能从 Spot 行为推断为统一协议。

官方来源：

- <https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams>
- <https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/user-data-stream-requests>
- <https://developers.binance.com/docs/binance-spot-api-docs/user-data-stream>
- <https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Keepalive-User-Data-Stream>
- <https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update>
- <https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Order-Update>

当前代码已经有一个方向正确但尚未完成的 shared channel：
`BinanceSpotAsyncUserDataChannel` 持有真实 socket/auth/reconnect state，并允许 Account parser
消费全部 user-data message；但 `BinanceSpotAsyncOrderEventSource` 又实现了一套几乎相同的
socket/subscription lifecycle。Futures、Margin 与 Options 也分别存在 Account/Order event
source 各自持有 socket 的情况。

目标是每个真实 user-data session 只有一个 concrete connection owner：

```text
BinanceSpotWebSocketApiConnection
├── balance/account facts
└── order/fill facts

BinanceCrossMarginUserDataStreamConnection
├── account facts
└── order/fill facts

BinanceIsolatedMarginUserDataStreamConnection(symbol scope)
├── account facts
└── order/fill facts

BinanceUsdMUserDataStreamConnection
├── account/position facts
└── order/fill facts
```

COIN-M 与 Options 同样按其官方 family-native session 建模。Account 与 Execution 在不同进程
时分别建立 user-data connection 是正常的；同一进程、account scope 与故障域中的第二条相同
连接必须由 composition 显式说明。

### 5.4 Binance Stocks 是独立 P0 connection family

Binance Stocks 不是 Spot 的 instrument variant，也不是把 Massive/IBKR stock adapter 换一个
provider。官网把它列为独立 Advanced Trading 产品：REST 全部位于
`/sapi/v1/equity/*`，WebSocket base URL 是
`wss://nbstream.binance.com/equity`。它支持美股与 ETF，provider symbol 是 `AAPL`、`SPY`
这类裸大写 ticker，默认 quote asset 是 USDC，价格以 USD 表达。

第一版 connection owner 是：

```text
BinanceStocksRestConnection
├── exchange info
├── tokenized assets
├── latest quote
├── place/cancel/cancel-all order
├── open/history/detail/trade history
├── tokenized mint/redeem/status/history
├── US equity disclaimer
└── listen-key create/renew

BinanceStocksWebSocketConnection
├── public price/quote/kline/calendar/tradability/trading-status streams
└── listen-key order-report stream
```

Stocks WebSocket 是单向 push，没有 subscribe/unsubscribe RPC；stream name 编码在 URL path。
单 stream 使用 `/ws/<streamName>`，combined stream 使用
`/stream?streams=<A>/<B>/<C>`。public 与 order-report 使用同一 endpoint/protocol，因此属于
同一种 concrete connection；能否在同一 combined socket 混合两类 stream 要由 contract/live
test 验证，不能仅凭 URL 形式推断。composition 可按验证结果与故障隔离选择一个或两个同类型
named instances，不再创建 public/private WebSocket trait 或 subscription enum。connection 直接实现
目标 `MarketDataStream` 与 `ExecutionStream`，内部负责 URL、PING/PONG、listen-key 续期和重连。

Stocks 属于 P0，首批至少覆盖 exchange info、latest quote、public quote/kline/trading-status、
place/cancel/cancel-all、open/history/detail、trade history、listen-key 与 order report。还必须纳入
以下 provider 规则：

- 首次交易前必须签署 US Equity disclaimer；
- 下单有每 UID 200 requests/min 的额外限制；
- `MARKET`/`LIMIT`、`RTH`/`EXTENDED`/`24H` 等枚举使用大写；
- limit price 最多两位小数，订单有效期使用股票语义，不能复用 crypto 的隐含默认；
- order-report listen key 的 TTL 为 60 分钟，需要主动续期；
- WebSocket kline 从 5m 起，不伪造官网未提供的 1m stream；
- tokenized mint/redeem 列为 P1；先用 concrete methods，只有形成稳定能力边界与真实 caller 时
  才加入 Integration-owned trait。

迁移结束时必须物理删除旧 Binance facade、旧 participant 文件和已经搬空的 private service
实现。禁止保留 compatibility re-export、type alias、旧 module 壳或“双入口”；Git 历史就是迁移
追溯手段，不需要在活动源码中保留旧心智。

官方来源：

- <https://developers.binance.com/en/docs/products/stocks/introduction>
- <https://developers.binance.com/en/docs/products/stocks/general-info>
- <https://developers.binance.com/en/docs/products/stocks/websocket-streams-general-info>
- <https://developers.binance.com/en/docs/products/stocks/quick-start>

### 5.5 Binance 官方能力目录与接入规范

2026-08-17 的 Binance 官方 catalog 列出 36 个产品、1065 个 endpoint，其中核心产品规模为：
Spot 118、USD-M 133、COIN-M 93、Options 54、Margin 65、Wallet 50；Portfolio Margin 109，
Simple Earn 41。目标是保留完整 inventory 并优先接入交易系统常用闭环，不把 1065 个 endpoint
机械翻译成 1065 个 Rust trait 或 connection。

官方产品目录：<https://developers.binance.com/en/docs/catalog>。

| 产品/链路 | 官网主要能力 | Integration 表达方式 | 优先级 |
| --- | --- | --- | --- |
| Stocks REST + streams | 美股/ETF catalog/quote、order/cancel/query、order report、calendar/tradability、tokenized mint/redeem | target catalog/market/order traits + concrete methods；独立 family | P0/P1 |
| Spot public REST | exchange info、depth、trades/aggTrades、klines、ticker、book ticker、reference price | `InstrumentCatalogQuery`、具体 Market Query、具体 Historical Query | P0 |
| Spot private REST | account、commission、orders/open orders/history、trades、order lists | `AccountQuery`、`OrderCommand`、`OrderQuery` | P0 |
| Spot market streams | trade/aggTrade、depth、book ticker、kline、ticker、block trade、reference price | `MarketDataStream` | P0/P1 |
| Spot WebSocket API | public query、下单/撤单/改单、order list、SOR、session user-data subscription | concrete connection 直接实现目标 query/order/event traits | P0/P1 |
| Cross/Isolated Margin REST + streams | account、borrow/repay、transfer、order/OCO/OTO/OTOCO、risk/interest、account/order events | 目标 account/order/funding traits；其余 concrete methods | P0/P1 |
| USD-M / COIN-M public REST + streams | exchange info、depth/trade/kline、mark/index price、funding、open interest、liquidation、long/short ratios | target catalog/query/subscription/event/historical traits | P0/P1 |
| USD-M / COIN-M private REST/WS API | account/balance/position、order/cancel/modify/batch、algo、leverage、margin/position mode、income/history | target account/order traits；配置与特有操作为 concrete methods | P0/P1 |
| USD-M / COIN-M user-data stream | account/position、order/trade、margin call、config/listen-key lifecycle | 同一 connection 直接实现 `AccountStream` / `ExecutionStream` | P0 |
| Options | instrument/mark/depth/trade/kline/open interest、account/position、order/batch、MMP、kill-switch、user data | target catalog/market/account/order traits + concrete methods | P1 |
| Wallet | wallet/assets、universal transfer、deposit/withdraw、fees、API permission | target account/funding/security traits + concrete methods | P1 |
| Simple Earn | flexible/locked products、positions、subscribe/redeem、rewards/history | `EarnQuery` + `EarnCommand` | P1 |
| Portfolio Margin | UM/CM/Margin unified balance/risk/order、transfer、user-data stream | target account/order/funding/event traits；独立 connection family | P2 |
| Convert、Staking、Loans、Sub-account、Algo、Institutional | quote/order、投资、借贷、账户管理与机构能力 | 保留 inventory；有真实调用者后在 concrete connection 上实现 | P2/P3 |

Spot 官方 REST/WS API 还包括 amend-keep-priority、cancel-replace、OCO、OTO、OTOCO、OPO、
OPOCO 与 SOR；USD-M/COIN-M 包括改单、批量单、algo order、自动撤单、杠杆、逐仓保证金与持仓
模式；Options 包括批量单、MMP 和 kill-switch。这些是重点常见或生产安全能力，不能被
`submit/cancel` 两个最小方法永久遗漏，但也不为每一项建立新 trait。第一版遵循：

1. 跨 provider 语义已经稳定的操作，纳入重新设计后的 Integration-owned trait；
2. Binance 特有且当前有调用者的操作，默认作为对应 concrete connection 的 inherent method；
   若它形成有真实 caller 的稳定协议能力，也允许新增 Integration-owned trait；
3. 没有当前调用者的 endpoint 只进入 capability inventory，不提前写空 adapter；
4. request/response 始终使用 owned typed struct/enum，不使用 JSON operation envelope；
5. command 返回 delivery certainty；HTTP/WS timeout 或 5xx 不能被当作确定失败并透明重试；
6. 每个 endpoint 记录 security type、request weight、order count、IP/account quota 与数据源；
7. market stream 必须验证 snapshot/delta 衔接、sequence、重连、resync 与 backpressure；
8. user-data frame 只读取一次并 demultiplex，Account 与 Order trait 实现共享同一 session owner。

### 5.6 Binance 分阶段功能基线

P0 建立可交易闭环：

- Stocks 的 exchange info/quote/market status、order command/query、listen-key/order report；
- Spot、USD-M、COIN-M 的 exchange info、depth snapshot、book ticker、trade、kline 与增量深度；
- account/balance/position snapshot；
- new/cancel/query/open/history 与 order/fill/account events；
- server time、签名、限流、listen-key/session keepalive、重连与 resync；
- Cross/Isolated Margin 的账户、普通下单/撤单/查询和 user-data event。

P1 补齐常用生产能力：

- amend、cancel-replace、cancel-all、batch、conditional/algo、order list；
- leverage、margin type、position mode、position margin、income/funding/commission；
- Options 常规交易、MMP、kill-switch；
- Wallet universal transfer、deposit/withdraw query、fee/API permission；
- Simple Earn flexible/locked 查询、申购、赎回与奖励。

P2/P3 再处理 Portfolio Margin、Convert、Staking、Loans、Sub-account、机构与其他低频产品。每个
slice 仍按 REST、WebSocket API、market stream、user-data stream 的真实 endpoint 分开，不为
产品目录中的营销分类创建大 facade。

Binance 官方来源：

- <https://developers.binance.com/en/docs/catalog>
- <https://developers.binance.com/en/docs/products/spot/rest-api>
- <https://developers.binance.com/en/docs/products/spot/websocket-api>
- <https://developers.binance.com/en/docs/products/spot/websocket-streams>
- <https://developers.binance.com/en/docs/products/spot/user-data-stream>
- <https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures>
- <https://developers.binance.com/en/docs/products/derivatives-trading-coin-futures>
- <https://developers.binance.com/en/docs/products/derivatives-trading-options>
- <https://developers.binance.com/en/docs/products/margin-trading>
- <https://developers.binance.com/en/docs/products/wallet>
- <https://developers.binance.com/en/docs/products/simple-earn>
- <https://developers.binance.com/en/docs/products/stocks/introduction>

## 6. Massive 目标模型

Massive 是 API-key 认证的 market/reference data provider，不存在交易所账户意义上的
public/private account connection。当前支持的目标资源是：

```text
MassiveRestConnection
├── reference queries
├── historical market queries
└── dividend queries

MassiveStocksWebSocketConnection
└── stocks subscriptions

MassiveOptionsWebSocketConnection
└── options subscriptions
```

Futures、Indices、Forex 与 Crypto 在业务真正接入后按各自 asset-class WebSocket endpoint
增加 concrete type，不提前创建空 collection。

### 6.1 官方链路核对

2026-08-17 按 Massive 官方文档核对：

- REST 使用 `https://api.massive.com`，通过 API key 认证；
- live WebSocket 按 asset class 使用不同路径，例如 Stocks 使用
  `wss://socket.massive.com/stocks`；
- socket 建立后先发送 `auth`，再发送一个或多个 channel/symbol subscription；
- 默认并发限制是每个 asset class 一条 WebSocket connection，需要同 asset class 多连接时
  应有套餐/支持证据。

官方来源：

- <https://massive.com/docs/rest>
- <https://massive.com/docs/websocket/quickstart>

当前 `MassiveConnection` 同时充当 REST client factory 和 WebSocket capability factory，而
真正 socket 位于 `MassiveAsyncMarketStream`。目标迁移是将前者收敛为
`MassiveRestConnection`，把后者提升并按当前真实 asset class 分成 concrete WebSocket
connection。各 concrete connection 直接实现目标 catalog、historical、market traits；
不增加只转发 concrete method 的 Massive 镜像 trait。`InstrumentType`/query filter 仍是 typed
request 参数，不为
Equity/Option 重复创建 REST connection。

## 7. Hyperliquid 目标模型

Hyperliquid 也不适合套用 OKX public/private WebSocket 拆分：官方同一个 WebSocket endpoint
同时承载 market subscription 和按 user address 过滤的 user subscription。目标资源是：

```text
HyperliquidInfoRestConnection
├── perpetual/spot metadata
├── snapshots
└── user/account information query

HyperliquidWebSocketConnection
├── public subscriptions: l2Book, trades, candle, allMids, ...
└── user-address subscriptions: orderUpdates, userEvents, userFills, ...

HyperliquidExchangeRestConnection       # 交易 slice 接入后
└── signed order/cancel/transfer actions
```

`Info` 与 `Exchange` 都可能使用 HTTP POST，但请求语义、签名、delivery certainty 与风险完全
不同，所以不能因为 host 相同而合并成一个 operation facade。Exchange command 在可能已广播
后不得透明重试。

### 7.1 官方链路核对

2026-08-17 按 Hyperliquid 官方文档核对：

- mainnet WebSocket endpoint 是 `wss://api.hyperliquid.xyz/ws`；
- 一条 socket 可以发送多个独立 subscribe message；unsubscribe 只移除匹配的 subscription；
- public `l2Book`/`trades` 与 user-address `orderUpdates`/`userEvents`/`userFills` 使用同一个
  WebSocket API；
- Spot 与 Perpetual 的 Info queries/WebSocket subscriptions 使用同一 API，区别主要在
  provider-native coin/address 参数；
- streaming user feed 的首次消息可能是 snapshot，reconnect 必须处理 snapshot acknowledgement
  或通过 Info query 补回缺失数据。

官方来源：

- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>
- <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint>

旧 `HyperliquidConnection` Info REST factory 与 `HyperliquidLiveMarket` 已从公共 participant
源码删除。当前 concrete owner 已拆为 `HyperliquidInfoRestConnection` 与
`HyperliquidWebSocketConnection`；能力迁移后它们直接实现现有
目标 market/account/order traits，provider subscription state 与 event demultiplex 留在 connection
内部。不能仅因为 subscription 带 user address 就再开一条所谓 private socket；是否拆多条同
endpoint socket 仍由 composition 的故障域和吞吐证据决定。

## 8. IBKR 目标模型

IBKR TWS API 不是 REST/WebSocket，而是到已登录 TWS 或 IB Gateway 的 stateful TCP message
protocol。项目直接依赖 `ibapi` crate 是实现选择；Integration 把第三方 library 的 session
虚拟为多个 Kairos-owned concrete connection，每个 connection 使用独立 client ID：

```text
IbkrAccountQueryConnection(client_id A)     -> AccountQuery
IbkrAccountStreamConnection(client_id B)    -> AccountStream
IbkrOrderConnection(client_id C)            -> OrderCommand + OrderQuery
IbkrExecutionStreamConnection(client_id D)  -> ExecutionStream
IbkrMarketDataConnection(client_id E)       -> MarketQuoteQuery（后续扩展历史与流）
```

这里的“虚拟”表示 Integration 对第三方 library transport 的明确包装。不同 connection 使用
不同 client ID，因此也对应独立的 TWS TCP session、故障域与并发消费边界。library 返回的
subscription object 只作为私有 service 状态，不再公开
`IbkrAccountChannel`、`IbkrOrderUpdates`、`IbkrMarketDataSubscription` 等能力包装层，也不能
作为多个平级 `ManagedConnections` 重复管理。

### 8.1 官方链路与 library 核对

2026-08-17 按 IBKR Campus 与本地 `ibapi 3.3.0` 核对：

- TWS/IB Gateway 在 host/port 上充当 TCP server；API application 使用 client ID 建立一条
  socket session；
- 一个 TWS/Gateway 最多接受 32 个 API client connection，client ID 区分连接并影响订单可见性
  和修改权限；
- handshake 后得到 server version、managed accounts、next valid order ID 等 session data；
- 断开该 client connection 会终止它的 ongoing requests/subscriptions，不影响其他 client ID；
- `ibapi::Client` 已提供 async `connect`、`is_connected`、`disconnect`，以及 account/order/
  market subscription API；subscription 是同一 Client 上的逻辑 request stream。

官方来源：

- <https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/>
- <https://ibkrcampus.com/docs/excel/rtd/connection-parameters>

旧 `IbkrConnection`、`IbkrTwsConnection` 和 `IbkrAsync*` service 命名均已删除。当前五个
concrete connections 分别直接实现其 capability；每个 connection 内部使用 `SessionService`
和领域 service，共享只发生在单条 connection 内部，不公开 projection facade。

目标改造：

1. 五种 connection 都拥有显式 lifecycle 与 health；
2. `SessionService` 是内部库适配，不是公开 capability；
3. Trading connection 保持 session 级 serialized order-ID allocator 和 notice stream；
4. Query、Stream、Order、Execution、MarketData connection 直接实现 Integration traits；
5. Conflux 分别管理五个 concrete named lists，每个 key 对应唯一 `(host, port, client_id)`；
6. composition 必须给并行连接分配不同 client ID，并保留 workspace exclusive lease。

## 9. Conflux 目标模型

`ConfluxSystem` 保持一个 concrete entity，不定义 trait，不带泛型参数，也不使用类型擦除。
它继续包含：

1. 本服务依赖的其他进程 Contract concrete clients；
2. 本服务可以按需使用的 Integration concrete connections。

本服务向外提供的唯一 Contract 由 `Conflux<A>` 直接持有，不进入 `ConfluxSystem`，也不使用
`ManagedContract`。

### 9.1 OKX 字段

完成 OKX 迁移后，目标字段为：

```rust,ignore
pub okx_public_rest_connections:
    ManagedConnections<String, OkxPublicRestConnection>,
pub okx_public_websocket_connections:
    ManagedConnections<String, OkxPublicWebSocketConnection>,
pub okx_private_rest_connections:
    ManagedConnections<String, OkxPrivateRestConnection>,
pub okx_private_websocket_connections:
    ManagedConnections<String, OkxPrivateWebSocketConnection>,
```

删除：

```rust,ignore
pub okx_connections: ManagedConnections<String, OkxConnection>,
pub okx_principals: ManagedConnections<String, OkxPrincipalConnection>,
```

### 9.2 Binance 字段

Binance 字段按已经完成迁移的 product family 与真实 transport 显式增加。REST connection
可以同时实现同一 endpoint 上真实存在的 public query 与 authenticated query/command；不为
“public/private”标签虚构第二个 HTTP pool。当前字段为：

```rust,ignore
pub binance_spot_rest_connections:
    ManagedConnections<String, BinanceSpotRestConnection>,
pub binance_spot_websocket_connections:
    ManagedConnections<String, BinanceSpotWebSocketConnection>,
pub binance_spot_user_websocket_connections:
    ManagedConnections<String, BinanceSpotUserWebSocketConnection>,
pub binance_spot_websocket_api_connections:
    ManagedConnections<String, BinanceSpotWebSocketApiConnection>,
pub binance_margin_rest_connections:
    ManagedConnections<String, BinanceMarginRestConnection>,
pub binance_margin_websocket_connections:
    ManagedConnections<String, BinanceMarginWebSocketConnection>,
pub binance_margin_user_websocket_connections:
    ManagedConnections<String, BinanceMarginUserWebSocketConnection>,
pub binance_stocks_rest_connections:
    ManagedConnections<String, BinanceStocksRestConnection>,
pub binance_stocks_websocket_connections:
    ManagedConnections<String, BinanceStocksWebSocketConnection>,
pub binance_stocks_user_websocket_connections:
    ManagedConnections<String, BinanceStocksUserWebSocketConnection>,
```

USD-M、COIN-M 使用各自的 `RestConnection`、`WebSocketConnection`、
`WebSocketApiConnection` 与 `UserWebSocketConnection`；Options 使用自己的 REST、market 与
user WebSocket concrete types。Portfolio Margin、Algo、Copy、Institutional Loan、Alpha 与
Stocks 也按官方 API family 独立命名。
它们不能因都使用 WebSocket 而合并。删除当前平铺的
`binance_*_principals`，也不增加 `binance_principals` 通用集合。

### 9.3 Massive、Hyperliquid 与 IBKR 字段

完成相应迁移后，目标字段为：

```rust,ignore
pub massive_rest_connections:
    ManagedConnections<String, MassiveRestConnection>,
pub massive_stocks_websocket_connections:
    ManagedConnections<String, MassiveStocksWebSocketConnection>,
pub massive_options_websocket_connections:
    ManagedConnections<String, MassiveOptionsWebSocketConnection>,

pub hyperliquid_info_rest_connections:
    ManagedConnections<String, HyperliquidInfoRestConnection>,
pub hyperliquid_account_rest_connections:
    ManagedConnections<String, HyperliquidAccountRestConnection>,
pub hyperliquid_exchange_rest_connections:
    ManagedConnections<String, HyperliquidExchangeRestConnection>,
pub hyperliquid_websocket_connections:
    ManagedConnections<String, HyperliquidWebSocketConnection>,

pub ibkr_account_query_connections:
    ManagedConnections<String, IbkrAccountQueryConnection>,
pub ibkr_account_stream_connections:
    ManagedConnections<String, IbkrAccountStreamConnection>,
pub ibkr_order_connections:
    ManagedConnections<String, IbkrOrderConnection>,
pub ibkr_execution_stream_connections:
    ManagedConnections<String, IbkrExecutionStreamConnection>,
pub ibkr_market_data_connections:
    ManagedConnections<String, IbkrMarketDataConnection>,
```

Hyperliquid signed exchange slice 已落地并使用官方 Rust SDK。IBKR library session 被五个
concrete virtual connections 封装；Massive 未接入的 asset class 不创建空 collection。

### 9.4 Key、revision 与 epoch

第一版 collection key 继续使用 `String`，但 key 必须由 composition 明确提供并在同一进程内
稳定。建议命名包含用途而不是把 provider 属性拼成隐式路由，例如：

```text
okx-public-market-main
okx-private-account-primary
okx-private-execution-primary
binance-usdm-private-execution-hedge
```

- revision 表示该 named resource 的配置版本；
- epoch 表示同一 named resource 被替换后的 runtime incarnation；
- WebSocket 自身每次 reconnect 的 channel/socket epoch 由 Integration connection 内部维护，
  不与 Conflux replacement epoch 混用；
- subscription acknowledgement 必须记录所属 socket epoch，禁止旧连接的迟到消息污染新连接。

### 9.5 Actor 与事件入口

Actor 仍只有一个 `handle`。Integration connection 输出的 owned provider fact 被包装进当前
全局 `ConfluxEvent`，然后通过同一个 bounded ingress 串行交给 Actor：

```text
concrete Integration connection
    -> provider parser/normalizer
    -> owned Integration fact
    -> ConfluxEvent::Integration(...)
    -> Actor::handle(...)
```

Conflux 不把 raw vendor payload 暴露给业务 Actor，不根据 connection type 自动选择业务路由，
也不让 connection task 并发修改 Actor/Application。

## 10. 生命周期与所有权

| 责任 | Owner |
| --- | --- |
| endpoint、认证、签名、provider clock | Integration concrete connection |
| IP/User/instrument quota 与共享关系 | Integration |
| WebSocket login、heartbeat、reconnect | Integration concrete WebSocket connection |
| desired subscriptions 与重新订阅 | Integration concrete WebSocket connection |
| REST polling schedule、cursor、baseline、dedup | Integration concrete REST connection |
| named connection 配置与实例选择 | module/system composition |
| connection collection、replacement、drop | Conflux |
| source task supervision 与 bounded ingress | Conflux |
| provider fact normalization | Integration |
| route/source/account/segment 选择 | business composition |
| snapshot/event barrier、dedup、业务 resync | owning business Actor/process |
| canonical Market/Account/Execution state | owning business Actor |

一条 connection 进入 `Ready` 只表示技术连接满足自身声明的 handshake/login/subscription
条件。它不等于 Market source、Account segment 或 Execution route 已具备业务可消费性。

## 11. 构造与使用示例

下面示例只说明所有权，不规定最终 constructor 名称：

```rust,ignore
let (private_rest, private_websocket) =
    OkxPrivateConnections::connect(private_config)?;

system
    .okx_private_rest_connections
    .ensure_with("okx-private-primary".into(), revision, || private_rest)?;

system
    .okx_private_websocket_connections
    .ensure_with("okx-private-account".into(), revision, || private_websocket)?;

let connection = system
    .okx_private_websocket_connections
    .get_mut(&"okx-private-account".to_owned())?;

connection.connection_mut().connect().await?;
let account_event = AccountStream::next(connection.connection_mut()).await?;
```

这里刻意只调用 Integration 的 `AccountStream`。需要的 OKX private channels、
登录、确认、demultiplex 与重订阅都是 `OkxPrivateWebSocketConnection` 内部状态；不存在
`OkxPrivateSubscription`，也不存在由 Conflux 管理的 capability handle。该 connection 同时
实现 `ExecutionStream`，两个 trait 共享同一个内部 socket owner。

`OkxPrivateConnections` 若存在，只是一次性 assembly result/builder，用于安全创建共享 private
context 后拆出 concrete connections；它不进入 `ConfluxSystem`，不拥有运行时生命周期，也不
成为 Actor 调用 facade。如果不需要共享构造保证，直接使用两个 concrete constructor。

## 12. 不做的事情

本次改造不做：

- 不定义 `Connection`、`PublicConnection`、`PrivateConnection` 通用 trait；
- 不把所有 provider 塞进统一 enum 或 registry；
- 不把 REST capability 模拟成 session `start/stop/reconnect`；
- 不把 WebSocket subscription 升级成独立 process 或 Actor；
- 不增加只转发 concrete connection 的 provider-specific 镜像 trait；真实协议能力可以进入
  Integration-owned trait，但 provider channel/ACK 仍是 connection 内部状态；
- 不让 Conflux 理解 OKX `instType`、Binance product symbol 或业务 route；
- 不在 provider slice 内实施 Market、Account、Execution、Reference module 的 Conflux runtime
  cutover；这些 modules 的 M0-M2 准备可按独立迁移提案并行推进；
- 明确允许本轮 Integration 破坏性改造后这些 modules 不能编译，直到未来 module migration；
- 不为了字段数量少而恢复 `OkxConnection`、`BinanceConnection` 大 facade；
- 不保留新旧 connection facade 的长期 compatibility wrapper。

## 13. 分阶段迁移计划

### Phase 0：协议与调用者 inventory

1. 为每个 provider/API family 列出真实 REST、WebSocket、TCP/library session endpoint，及其
   认证和 subscription 边界；
2. 标记当前每个 concrete type 实际持有 HTTP client、socket、credential、quota 和 lifecycle
   的位置；
3. 列出 Account、Market、Execution、Reference 当前生产调用者；
4. 记录哪些 event capability 当前隐式创建独立 socket；
5. 更新对应 `docs/integration-adapter-references/` provider note。

退出条件：每一个要迁移的类型都能被分类为 connection、shared context、目标 trait
implementation、provider 内部 channel state 或 obsolete facade。

### Phase 1：Capability surface 与 WS dispatcher

1. 按 3.8 建立 domain/capabilities/blocking/participants/services/transport/composition ownership，
   并删除旧内部路径；
2. 删除 `Async*Connection` 命名，按 3.3 定义 async-first `&mut self` 业务能力 traits；
3. 将 lifecycle 移出 stream traits，把 subscription control 与 event consumption 拆为
   `MarketSubscriptionCommand` 和 `MarketDataStream`；
4. 用 typed feeds 替换只有 symbols 的 `MarketSubscription`；
5. 实现单 socket reader、correlation waiter、bounded ordered event queue 与 epoch fencing；
6. 用 `event -> event -> ACK` 等交错测试固定 confirmation 与 buffering 语义；
7. 当第一个真实 polling caller 出现时，让对应 REST connection 实现 subscription/event traits，
   并验证 baseline、delivery mode、cursor/dedup、freshness、quota 与 overflow；本阶段不为证明
   抽象创建无人使用的 polling connection；
8. 删除旧 trait/export，不保留 compatibility wrapper；不调整 business modules。

退出条件：Integration/Conflux 可以基于新 traits 编译和测试；现有 provider 不再将 frame write
误报为 subscribe success；modules 能否编译不检查，也不作为 gate。

### Phase 2：OKX private WebSocket

1. 新增 `OkxPrivateWebSocketConnection`；
2. 将 login、socket、heartbeat、pending frames、epoch 与 reconnect 收归该类型；
3. 直接实现 `AccountStream` 与 `ExecutionStream`，不新增 OKX-specific capability 或
   subscription 类型；
4. 在 adapter 内部让一条 socket 支持 Account、Orders、Positions 等频道及 acknowledgement；
5. 同一进程/principal/fault-domain 默认复用一条 socket；composition 只有在跨进程、故障隔离、
   吞吐或 provider 限制有证据时才显式拆分；
6. 删除 `OkxTradingAccountEvents`、`OkxTradingOrderEvents` 各自拥有 socket 的旧路径。

退出条件：测试证明同 socket 多频道、分 socket 隔离、登录失败、部分订阅失败、重连重订阅、
迟到旧 epoch 消息和 queue overflow 行为。

### Phase 3：OKX REST

1. 将 public REST 能力移动到 `OkxPublicRestConnection`；
2. 将 private REST 能力移动到 `OkxPrivateRestConnection`；
3. 提取仅供 Integration 内部共享的 private credential/quota context；
4. 删除 `OkxPrincipalConnection`；
5. 删除运行时 `OkxConnection` 大 facade。

退出条件：public/private REST 可独立构造、替换和失败；private REST 与 private WebSocket
共享正确 credential/quota 语义；命令 delivery certainty 测试通过。

### Phase 4：Conflux 接入 OKX

1. 在 `ConfluxSystem` 增加四类 OKX concrete collection；
2. 删除 `okx_connections` 与 `okx_principals`；
3. 将 concrete WebSocket fact source 接入唯一 `ConfluxEvent` ingress；
4. 验证 replacement、shutdown 和 final drop。

退出条件：Conflux 不再引用 `OkxConnection` 或 `OkxPrincipalConnection`，Actor 可按名称使用
REST connection，并从 WebSocket connection 接收 normalized facts。

### Phase 5：Binance Stocks

1. 实现一条同时承载公开查询与签名账户命令的 Stocks REST concrete connection；
2. 分别实现 URL-bound public market WebSocket 与 listen-key private user WebSocket；二者是
   独立物理链路，不共享 socket owner；
3. 完成 P0 catalog/quote/market status/order command-query/order-report 闭环；
4. 覆盖 disclaimer、股票交易时段、价格精度、UID quota、listen-key TTL 和 combined stream；
5. 在 Conflux 加入三个 Stocks named collections，不调整任何 business module。

退出条件：Integration 与 Conflux focused tests 通过；没有 Stocks capability trait、subscription
enum、Spot connection 复用或 raw vendor payload；module 编译不作为本阶段 gate。

### Phase 6：Binance Spot/Margin

1. 先拆 public/private REST；
2. 将 public market WebSocket 与 account-scoped user-data connections 提升为真实 socket
   owner；
3. Spot user data 默认统一 demultiplex Account 与 Execution facts；Cross Margin 和 Isolated
   Margin 按真实 listen-key/socket scope 保留独立 connection；
4. 删除 `BinanceMarginPrincipalConnection` 与 `BinanceSpotPrincipalConnection` managed path；
5. 更新 Conflux Spot REST/market/WS API 与 Margin user-data stream collections。

退出条件：同一 private context 共享 credential/clock/quota，Margin projection 没有第二套
managed lifecycle，isolated symbol 与 listen-key/socket 边界有覆盖测试。

### Phase 7：Binance USD-M、COIN-M、Options

每个 family 独立迁移 public/private REST、WebSocket API（官方存在时）、market stream 与
user-data stream connection，不复用一个隐藏 domain 的 Futures principal type。按 5.6 先完成
P0 可交易闭环，再扩展 P1；一个 family 通过退出条件并删除旧路径后，再开始下一个 family。

### Phase 8：其余 provider

1. Massive 先拆 `MassiveRestConnection`，再按当前 Stocks/Options endpoint 提升 concrete
   WebSocket connections；
2. Hyperliquid 拆 Info REST、Account REST、Exchange REST 与 unified WebSocket；
3. IBKR 将 library client/session 虚拟为 Account Query/Stream、Order、Execution Stream、
   MarketData 五种 concrete connection，并用独立 client ID 隔离并行消费；
4. 每个 provider 完成后更新 Conflux concrete fields 并删除旧大 facade。

退出条件：只有真实存在的 transport 组合进入 Conflux；没有为了矩阵对称创建空
public/private REST/WebSocket 类型。

## 14. 验证要求

当前迁移状态（2026-08-18）：

- capability/domain/blocking/participant/service/transport 分层和 concrete connection 集合已建立；
- OKX、Binance、Massive、Hyperliquid、IBKR 的 public connection 已直接实现对应 Query、
  Command、Stream 与 lifecycle/health traits；
- provider 协议编码、签名、REST client、socket 与 payload normalizer 已收归
  `services/participants/<provider>`，participant 仅持有 concrete lifecycle、desired state、
  bounded queue 和 capability 实现；
- obsolete principal、generic connection facade、type-erased registry、blocking transport 与旧
  service quota facade 已删除；
- Account 已切换到 concrete Account Query/Stream connections；Funding Wallet 使用独立
  `BinanceFundingRestConnection` 的 signed-POST query，且不伪造不存在的 stream；
- Market 已把 Binance、OKX、Hyperliquid、Massive 与 IBKR 的具体 market connections 安装到
  `ConfluxSystem` 的精确 named collections；Actor 按 subscription 取用，旧 `SourceActivator`、
  provider composition facade、独立 control runtime 与旧 publication fan-out 已删除；
- Execution 的生产入口已切换为 `ExecutionApplication: Contract + ConfluxActor` 与薄
  `ExecutionHost`；具体 REST/order stream connections 由 `ConfluxSystem` 持有并按 route 取用，
  旧 `ExecutionProcess`、`services/control`、direct connection aggregate、旧 snapshot/Aeron
  publisher facade 已删除；durable audit 与 simulation settlement 已并入 Actor 发布阶段；
- Risk 已收敛为单一 Actor handle 与薄 `RiskHost`，REST Contract/Domain 转换使用显式 typed
  mapping，不再通过 JSON value round-trip；
- Reference 已完成 Contract、Aeron 与 Actor handle 切换；Binance Spot/USD-M/COIN-M/Options/
  Stocks、OKX、Hyperliquid、Massive Equity/Options 的具体 REST connections 先进入
  `ConfluxSystem` 精确 named collections，再由 Actor 激活；生产 source 聚合使用闭合 concrete
  enum，不再保存 `Box<dyn ReferenceSource>`；Massive Options 运行时新增 scope 也经过同一 managed
  collection，测试专用 fake 才允许 `cfg(test)` trait object；
- Execution 已删除旧 blocking execution-stream pull/consume 路径，provider execution facts 只从
  Conflux 监督的 concrete stream 进入统一 `handle`；
- 最终 focused/full 验证已执行：`cargo test --workspace` 全部通过；`uv run pytest -q`
  为 385 passed、8 skipped；格式、diff、crate layout 与 obsolete abstraction 静态搜索通过。

迁移期间只运行所改 slice 的 focused compile/test；所有 compatibility 路径删除后统一运行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

每个迁移切片至少验证：

- public 与 private connection 可独立构造和关闭；
- REST 与 WebSocket 可独立替换和失败；
- 同 concrete type 多个 named instance 相互隔离；
- private REST/WebSocket 的 credential rotation 一致；
- provider quota 的 IP、User ID、instrument 与 connection 作用域正确；
- command 在可能已发送后不透明重试；
- query 只执行声明过的有界安全重试；
- WebSocket heartbeat、断线、登录失败、订阅拒绝和 reconnect；
- reconnect 后 desired subscriptions 恢复；
- REST polling 只有在首个 baseline 完成后 confirmed，且不会把 snapshot/diff 冒充 provider push；
- socket epoch 阻止迟到消息；
- bounded queue overflow 不静默丢失；
- shutdown deadline 内停止 source、关闭 socket 并 drop concrete connection；
- normalized fact 只通过一个 bounded `ConfluxEvent` ingress 进入 Actor；
- 无 raw vendor payload 穿越业务 application boundary。

静态搜索至少包含：

```text
rg -n "OkxConnection|OkxPrincipalConnection" crates/platform/integration crates/platform/conflux
rg -n "Binance.*PrincipalConnection" crates/platform/integration crates/platform/conflux
rg -n "MassiveConnection|HyperliquidConnection|IbkrConnection" crates/platform/integration crates/platform/conflux
rg -n "dyn Connection|ConnectionSpec|IntegrationCapability" crates/platform/integration crates/platform/conflux
rg -n "Box<dyn Any>|TypeId|downcast" crates/platform/conflux
rg -n "trait .*Capability|enum .*Subscription" crates/platform/integration
```

匹配结果必须逐项归类；完成某个迁移切片时，该切片的 obsolete facade/connection 匹配应为零。
最后一项允许命中既有 `MarketSubscription` 等已批准边界，但必须证明迁移没有新增平行能力层。

## 15. 抽象引入检查

1. **当前解决的问题是什么？** 解决 provider context、principal、transport connection 与
   capability 混合，导致重复 socket、重复 lifecycle 和 Conflux 资源边界错误。
2. **当前调用者是谁？** 当前实施调用者是负责持有 concrete connection 的 Conflux runtime；
   business modules 留待后续单独迁移。
3. **现有边界为什么不足？** 当前 `OkxConnection`/`OkxPrincipalConnection` 和 Binance
   principal 系列不能准确表达 public/private、REST/WebSocket 的独立故障与生命周期。
4. **最简单的实现是什么？** 按 provider 真实 transport 拆 concrete structs；共享认证状态留在
   Integration 内部；Conflux 使用明确 named collections。
5. **迁移后删除什么？** `OkxConnection` 大 facade、`OkxPrincipalConnection`、各 event
   capability 自持 socket 的重复路径、Binance managed principal/projection 路径，以及已迁移
   slice 的旧 registry/compatibility wrapper。
6. **什么证据证明改造有用？** 多频道单 socket、分 socket 隔离、独立 replacement、credential/
   quota 共享、重连重订阅、epoch 与 backpressure 测试，以及 Integration/Conflux 不再构造旧
   类型；module 恢复编译属于后续迁移证据。

## 16. 最终退出条件

本提案完成时：

1. `Connection` 命名只用于拥有独立 transport lifecycle 的 concrete type；
2. Conflux 不管理 principal、capability 或 subscription；
3. OKX 不存在运行时大 `OkxConnection` facade；
4. OKX public/private REST/WebSocket 可独立管理；
5. Binance 已支持 family 的 REST、market stream 与 user-data stream 边界明确；
6. Margin 与 account/trading mode 不再伪装成 principal connection；
7. Massive 按 REST 与 asset-class WebSocket 管理，不伪造 private account；
8. Hyperliquid 同 endpoint 的 public/user subscriptions 可以由一条 concrete socket 承载；
9. IBKR library client 被五个细粒度 connection 完整封装，第三方 subscription 不成为公开资源；
10. composition 明确控制 socket 数量和 subscription placement；
11. 所有 Integration facts 经过 normalized owned type 和唯一 Conflux ingress；
12. 新路径具备 lifecycle、quota、failure、recovery 和 backpressure 测试；
13. 对应旧 facade、旧 registry、重复 socket owner 和 compatibility path 已删除。
