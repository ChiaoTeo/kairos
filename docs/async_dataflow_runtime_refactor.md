# Kairos 异步数据流与全生命周期统一改造总纲

状态：Proposed  
版本：1.0  
基线日期：2026-07-17  
适用范围：`kairospy` 全包、实时数据接入、研究、回测、模拟、paper/testnet/live Runtime，以及未来 Python/Rust 混合部署
目标读者：系统维护者、数据工程人员、策略开发者、执行与基础设施开发者

## 1. 文档目的

本文专门用于指导 Kairos 下一阶段的系统级改造，统一以下目标：

1. 保留当前系统已经成立的领域模型、数据治理、订单状态、Ledger、恢复与运行安全能力；
2. 不复用已经失控的 `kairospy_v2` 代码，但吸收其中经过实践验证的设计思想；
3. 将当前以同步编排、周期轮询和分散回调为主的 Runtime，演进为可恢复、可观测、可回放的异步数据流系统；
4. 建立从研究、回测、历史模拟、实时模拟、paper/testnet 到 live 的统一事实、统一策略和统一执行链路；
5. 为未来将市场数据接入、网络传输或执行网关替换为 Rust 提供稳定、语言无关的接入规范；
6. 将“容易调试”设为与正确性、可靠性和性能同等级的架构要求，避免再次形成只能运行、难以解释和难以复现的系统。

本文不替代现有领域与数据平台总纲：

- `docs/system_architecture_convergence_blueprint.md` 继续定义系统模块边界、事实来源和既有收敛路线；
- `docs/data_system_convergence_and_productization.md` 继续定义研究数据产品治理；
- 本文在上述基础上，重点定义实时数据流、运行时、调试、跨语言接口以及全生命周期统一路径。

发生冲突时，必须先明确事实所有权和运行安全要求，再同步更新相关文档，不允许通过兼容层长期保留两套正式模型。

## 2. 改造立场

### 2.1 不迁移 `kairospy_v2` 代码

本次改造明确不直接复用 `kairospy_v2` 的工程、crate、共享内存实现、网络栈或运行框架。

原因不是其中所有设计均无价值，而是该系统已经出现以下失控特征：

- 自研范围同时覆盖 HTTP、WebSocket、Reactor、IPC、共享内存、框架、执行、策略和追踪；
- 基础设施边界与业务边界互相渗透；
- Venue、产品线、Worker 和事件类型存在纵向重复；
- 共享内存数据结构承担了过多领域接口职责；
- 运行状态主要存在于内存中，故障发生后难以重建完整因果链；
- 异常恢复倾向于继续运行，但缺少足够的结构化证据解释发生了什么；
- 代码规模和抽象数量超过了团队能够稳定理解、测试和演进的范围。

因此本次只迁移思想、用例和经验，不迁移实现。

### 2.2 吸收的核心思想

从 `kairospy_v2` 吸收以下设计思想：

- 策略声明数据和账户能力需求，Runtime 负责汇总、去重和应用；
- WebSocket、HTTP、共享内存等 transport 对上层暴露统一事件源；
- 高频市场数据使用有界、低开销的数据面；
- 最新值与逐事件流采用不同交付语义；
- 订阅变化通过期望状态与实际状态的 reconciliation 生成增量命令；
- Strategy、Intent、Policy 和 Executor 分离；
- 从接收、解析、投影、决策到执行保留端到端延迟和因果追踪；
- Rust 适合承担连接、解析、序列检查和高频数据分发，但不应改变上层业务事实。

### 2.3 保留当前系统的核心优势

当前系统以下部分应作为改造基础，不应推翻重写：

- `InstrumentDefinition + InstrumentContractSpec + ListingDefinition + Capability`；
- 内部稳定 `InstrumentId`、`AccountKey`、策略和 Intent 身份；
- Dataset Product、不可变 Release、content hash、lineage、coverage 和 quality；
- point-in-time 数据语义和确定性 Replay；
- Strategy Intent、Execution Plan、Router 和 Venue capability 校验；
- Durable Order State Machine；
- Fill、Order、Ledger 和 cursor 的事务提交与幂等；
- Portfolio、Risk、Strategy Position Projection；
- Recovery、Reconciliation、Readiness、Kill Switch 和 fail-closed；
- Study Validation、Backtest Golden 和审计 Artifact。

本次改造的原则是：

> 保留当前系统的业务正确性与数据治理骨架，在其外部建立统一的异步运行时、事件通道、调试证据和跨语言接入层。

## 3. 当前系统判断

### 3.1 已经成立的能力

当前系统已经能够在模块和受控场景中形成：

```text
Frozen Data Release
  -> Strategy
  -> Intent
  -> Risk
  -> Order
  -> Fill
  -> Ledger
  -> Portfolio
  -> Reconciliation
  -> Restart Recovery
```

数据平台已经能够回答：

- 使用了哪个逻辑数据产品；
- 解析到了哪个不可变 Release；
- Schema 和 Transform 是哪一版；
- 数据来自哪个 Provider/Venue；
- content hash、quality、coverage 和 lineage 是什么；
- 回测使用 raw-as-received 还是 corrected-final；
- 相同 Release 能否按确定顺序重放。

订单与账务路径已经能够回答：

- Order 在调用 Venue 前是否持久化；
- client order ID 是否幂等；
- 超时后为何进入 UNKNOWN；
- Fill 是否与 Ledger 和 cursor 单事务提交；
- 重启后如何恢复订单、持仓和账务；
- Venue 与本地状态不一致时是否阻止 READY。

### 3.2 尚未成立的能力

当前系统仍不是完整的异步数据流系统：

- Application 生命周期和 Supervisor 以同步调用、周期轮询为主；
- `run()` 尚未代表一组持续运行、受监督的异步任务；
- Market Data Client 的正式端口仍以 `snapshot()` 为主；
- Binance、Massive、IBKR 分别使用阻塞 WebSocket、async iterator 和 callback，生命周期不统一；
- 下单路径仍以同步函数返回 `OrderAck` 为主要交互方式；
- 领域事件、Canonical Market Event 和实时传输事件尚未完全统一；
- 缺少统一的有界通道、背压、优先级、消费者 lag 和 gap recovery 语义；
- Raw Journal、sequence 检查和 reconnect backfill 只在部分 Connector 中成立；
- 缺少从线上事件到本地确定性复现的标准操作路径；
- Python `Protocol + dataclass` 尚不能作为 Rust 接入规范。

## 4. 总体目标

最终系统应形成一个以事件和命令驱动、以持久事实为权威、以 Projection 提供当前状态的运行体系：

```text
Venue / Provider
  -> Raw Frame Capture
  -> Venue Decode
  -> Canonical Event
  -> Validation / Sequence / Quality
  -> Typed Event Channels
  -> Market / Account / Order Projectors
  -> Strategy Runtime
  -> Economic Intent
  -> Portfolio Governance / Risk
  -> Execution Plan
  -> Durable Command Outbox
  -> Execution Gateway
  -> Venue Order / Fill / Account Events
  -> Durable Ingestion
  -> Ledger / Order State / Cursor
  -> Portfolio / Risk / Strategy Position
  -> Reconciliation / Monitoring
  -> Next Decision
```

研究、回测、模拟和实盘共享相同的 Canonical Event、Projector、Strategy、Intent、Risk、Order State 和 Ledger 语义，只替换事件源、Clock 和 Execution Driver。

## 5. 核心架构原则

### 5.1 单一事实，多种投影

权威事实保持如下分工：

| 事实 | 权威来源 |
|---|---|
| Instrument、Product、Venue Listing | Instrument Catalog |
| 历史与冻结研究输入 | Dataset Catalog + Immutable Release |
| 实时原始输入 | Raw Capture Journal |
| Canonical 市场事实 | Canonical Event Capture / Release |
| 订单生命周期 | Durable Order State Store |
| 成交事实 | Normalized Execution Event |
| 现金和持仓账务 | Ledger |
| 策略持仓归属 | Strategy Position Book |
| 当前市场、账户、风险状态 | 可重建 Projection |
| Runtime 安全状态 | Runtime State Store |

共享内存、内存对象、缓存、DataFrame、最新行情和风险快照均为 Projection，不得成为唯一事实源。

### 5.2 异步不等于不受控并发

系统采用异步 I/O 和事件通道，但不允许任意组件自行创建无法监督的任务。

所有长期任务必须由 Application Runtime 管理：

- 有明确名称和所有者；
- 有启动和停止顺序；
- 有 readiness 条件；
- 有取消与 graceful shutdown；
- 有 retry/backoff 策略；
- 有失败等级和传播规则；
- 有 queue depth、lag、last-event 和 health 指标；
- 有结构化故障事件；
- 关键任务退出时默认 fail closed。

### 5.3 可调试性是硬约束

每条关键数据或命令必须能够回答：

- 从哪里来；
- 原始内容在哪里；
- 由哪个版本的 Decoder/Normalizer 处理；
- 何时发生、何时收到、何时可见、何时处理；
- 经过哪些 Projector 和 Consumer；
- 是否发生乱序、重复、覆盖、丢失或修正；
- 导致了哪个 Strategy Decision、Intent、Order 和 Execution；
- 如何使用相同输入重放。

任何优化如果破坏上述问题的可回答性，都不得进入正式 Runtime。

### 5.4 数据面与控制面分离

系统采用两个逻辑平面。

数据面负责高频、可重建市场数据：

- Quote；
- Trade；
- Order Book Snapshot/Delta；
- Mark/Index；
- Greeks；
- Funding Rate；
- Derived Market State。

控制面负责不可静默丢失的命令和事实：

- Subscription Command；
- Order/Cancel Command；
- Order Ack/Reject/Unknown；
- Fill；
- Account/Balance/Position Update；
- Funding Payment；
- Settlement；
- Kill Switch；
- Runtime Fault；
- Recovery/Reconciliation Result。

数据面可以使用共享内存；控制面必须使用可靠 IPC、durable outbox/event log 和幂等消费。

### 5.5 默认至少一次，消费者幂等

跨进程、重连、REST backfill 和 WebSocket 实时事件不追求脆弱的 exactly-once transport。

正式语义为：

```text
at-least-once delivery
  + stable external identity
  + idempotent durable ingestion
  + conflict detection
  = effectively-once business fact
```

重复内容允许安全重放；相同 identity 对应冲突内容必须拒绝并进入告警或隔离。

## 6. 目标模块结构

建议逐步收敛为以下结构，名称可在实施中调整，但职责不能重新混合：

```text
kairospy/
├── domain/                  # 稳定业务事实和值对象
├── contracts/               # 语言无关 Event/Command schema 的 Python binding
├── catalog/                 # Instrument/Product/Listing
├── data/                    # Dataset Product、Release、查询、质量、冻结
├── market_data/
│   ├── contracts.py         # Typed canonical market payload
│   ├── runtime.py           # 流式接入编排
│   ├── subscriptions.py     # 声明、合并、reconciliation
│   ├── capture.py           # Raw/Canonical capture
│   ├── sequence.py          # sequence、gap、snapshot+delta
│   ├── channels.py          # 有界通道与 delivery semantics
│   ├── projections.py       # MarketState/LatestValue/Book
│   └── replay.py            # 与 live 相同的事件源接口
├── strategies/              # Strategy contract、runtime、registry
├── risk/                    # Governance、limits、risk projection
├── execution/
│   ├── commands.py          # Order/Cancel command
│   ├── outbox.py            # durable command outbox
│   ├── dispatcher.py        # 异步执行分发
│   ├── order_state.py       # durable state machine
│   ├── ingestion.py         # Fill/Order event durable ingestion
│   └── recovery.py
├── accounting/              # Ledger reducer 和 projection
├── application/
│   ├── runtime.py           # 顶层生命周期
│   ├── service_supervisor.py # 结构化并发
│   ├── composition.py       # 唯一正式组合根
│   └── modes.py             # study/replay/sim/paper/live
├── orchestration/           # readiness/reconciliation/kill switch/monitoring
└── connectors/
    ├── ports/               # Port contracts
    ├── simulated.py
    ├── binance/
    ├── ibkr/
    └── rust_gateway/        # 可选 sidecar binding
```

## 7. 统一事件与命令契约

### 7.1 Envelope

系统只保留一套正式传输 Envelope。领域 payload 可以继续使用领域对象，但实时、Capture、Replay 和跨语言传输必须映射到同一份版本化契约。

建议公共字段：

```text
message_id
message_type
schema_version
source
source_instance
stream_id
partition_key
source_sequence
receive_sequence
canonical_sequence
event_time
receive_time
available_time
published_time
correlation_id
causation_id
trace_id
capture_offset
flags
payload
```

语义要求：

- `message_id` 是规范化消息的稳定身份；
- `source_sequence` 保存 Venue/Provider 原生序号，没有时为空；
- `receive_sequence` 是单连接 epoch 内本系统接收顺序；
- `canonical_sequence` 处理一条 raw frame 拆分为多事件的顺序；
- `partition_key` 决定需要保持局部有序的范围；
- `correlation_id` 连接同一业务操作；
- `causation_id` 指向直接原因；
- `capture_offset` 定位原始输入；
- Decimal 禁止使用 binary float；
- 所有时间使用 UTC，跨语言 wire format 使用 epoch nanoseconds；
- 未知字段必须可忽略，未知必需 enum 必须拒绝或进入 quarantine；
- Schema 的 breaking change 必须升级 major version。

### 7.2 Typed Market Payload

正式市场事件至少包含：

- `QuoteEvent`；
- `TradeEvent`；
- `BarEvent`；
- `OrderBookSnapshotEvent`；
- `OrderBookDeltaEvent`；
- `MarkPriceEvent`；
- `IndexPriceEvent`；
- `FundingRateEvent`；
- `OpenInterestEvent`；
- `GreeksEvent`；
- `VolatilitySurfacePointEvent`；
- `TradingStatusEvent`；
- `InstrumentDefinitionEvent`；
- `DataWarningEvent`。

不得继续以无约束 `Mapping[str, object]` 作为跨语言正式契约。物理存储可以保留 `payload_json`，但必须由 typed payload 生成。

### 7.3 Execution Command 与 Event

命令：

- `SubmitOrderCommand`；
- `SubmitComboOrderCommand`；
- `CancelOrderCommand`；
- `ReplaceOrderCommand`；
- `TransferCommand`；
- `SubscribeCommand`；
- `UnsubscribeCommand`。

事件：

- `CommandAccepted`；
- `CommandRejected`；
- `VenueOrderAcknowledged`；
- `VenueOrderRejected`；
- `VenueOrderPartiallyFilled`；
- `VenueOrderFilled`；
- `VenueOrderCancelled`；
- `VenueOrderExpired`；
- `VenueOrderUnknown`；
- `ExecutionReported`；
- `BalanceUpdated`；
- `PositionUpdated`。

`CommandAccepted` 只表示本地 durable outbox 已可靠接收命令，不表示 Venue 已接受订单。

## 8. 数据流与通道语义

### 8.1 通道分类

不能把所有事件放进一个统一队列。至少定义以下类别：

| 通道 | 示例 | 是否允许覆盖 | 恢复方式 |
|---|---|---:|---|
| Critical Durable | Fill、Order、Account、Kill Switch | 否 | durable log/backfill |
| Stateful Ordered | Order Book Delta、Trading Status | 否，检测 gap 后失效 | snapshot + delta recovery |
| Lossy Latest | Quote、Mark、Index、Greeks | 是 | 跳到最新值 |
| Historical Capture | Raw/Canonical Event | 否 | append-only journal/release |
| Derived | Signal、Risk Snapshot | 取决于声明 | 从上游事实重建 |

### 8.2 背压策略

每条通道必须显式声明：

- capacity；
- ordering scope；
- delivery semantics；
- overflow policy；
- consumer lag limit；
- persistence policy；
- recovery policy；
- criticality；
- metrics labels。

允许的 overflow policy：

- `BLOCK_PRODUCER`：仅用于不会拖垮 Venue 接收的内部可靠链路；
- `DROP_OLDEST_WITH_GAP`：允许丢历史但必须产生 gap 证据；
- `CONFLATE_LATEST`：按 key 只保留最新值；
- `FAIL_STREAM`：数据完整性无法保证时使流失效；
- `SPILL_TO_DISK`：允许临时落盘后追赶。

禁止静默丢弃。

### 8.3 Shared Memory 定位

共享内存只作为可选的本机高速 transport，适用范围：

- Rust market gateway 到 Python Runtime；
- 高频 Quote/Trade/Book 数据；
- Latest Value Store；
- 有明确 gap detection 的 Event Ring。

共享内存不得作为：

- Order/Cancel 的唯一命令通道；
- Fill/Account Event 的唯一事实源；
- Ledger 或 Order State Store；
- 跨机器协议；
- 无版本领域对象的直接内存暴露。

共享内存 Header 至少包含：

```text
magic
protocol_major
protocol_minor
record_type
record_size
capacity
writer_epoch
oldest_available_sequence
latest_published_sequence
schema_hash
```

每条记录至少包含：

```text
sequence
payload_length
flags
event_time_ns
receive_time_ns
published_time_ns
partition_hash
checksum
payload
```

消费者必须检测：

```text
latest_published_sequence - next_consumer_sequence >= capacity
```

出现覆盖时必须生成 `ConsumerGapDetected`，不得继续把读取到的槽位解释为连续事件。

## 9. Raw Capture、Canonical Capture 与 Replay

### 9.1 三阶段数据路径

所有正式实时 Connector 遵循：

```text
RawFrame
  -> VenueMessage
  -> CanonicalEvent
```

`RawFrame` 保存：

- 原始 bytes 或稳定引用；
- session/connection epoch；
- receive sequence；
- receive timestamp；
- subscription revision；
- transport metadata；
-压缩和 checksum 信息。

`VenueMessage` 只在 Connector 内部存在，用于表达 Provider 原生协议。

`CanonicalEvent` 是系统其余模块消费的唯一市场事件格式。

### 9.2 写入顺序

正式接入默认顺序：

```text
receive raw
  -> append raw journal
  -> decode
  -> validate identity/time/sequence
  -> publish canonical event
  -> append canonical capture
  -> update projections
```

极端性能场景可以异步批量落盘，但必须保证：

- raw frame 在内存覆盖前已经进入可靠 capture；
- capture writer lag 可观测；
-超出安全 lag 时 stream 降级或 fail closed；
-任何未持久化窗口均有明确指标和告警。

### 9.3 Session Release

一次实时运行应能够冻结为 Capture Release：

```text
session_id
runtime_id
connector_build
contract_version
decoder_version
normalizer_version
subscription_revisions
raw files and hashes
canonical files and hashes
sequence gaps
reconnects
quality report
coverage
started_at / ended_at
```

线上故障必须可以通过 session ID 固定输入并进入 Replay。

### 9.4 Live 与 Replay 同接口

Strategy 和 Projector 只依赖：

```python
class EventSource(Protocol):
    async def events(self) -> AsyncIterator[CanonicalEvent]: ...
```

实现可以是：

- `LiveVenueEventSource`；
- `SharedMemoryEventSource`；
- `CapturedSessionEventSource`；
- `DatasetReleaseEventSource`；
- `SyntheticEventSource`。

Replay 不得重新实现一套 Strategy 数据入口。

## 10. 声明式订阅与能力需求

### 10.1 Strategy Requirement

策略不直接操作 Venue 订阅。策略或部署配置声明：

```text
required instruments
required market channels
depth
freshness requirement
delivery semantics
capture policy
account state requirement
execution capabilities
warm-up window
decision cadence
```

### 10.2 Subscription Planner

Runtime 汇总全部 Strategy Requirement，并与 Connector capability、Catalog Listing 和当前订阅状态求交集，生成 `SubscriptionPlan`。

Planner 必须：

- 按 Venue/Product/Connection 限制分组；
- 合并重复需求；
- 选择满足所有消费者的最小充分数据等级；
- 显式拒绝 Connector 不支持的需求；
- 记录 plan hash 和 revision；
- 只产生增量 subscribe/unsubscribe；
- 等待或验证 Venue acknowledgement；
- reconnect 后重放目标订阅状态；
- 将实际订阅与目标订阅持续 reconciliation。

### 10.3 Readiness

策略只有在以下条件满足时才能开始决策：

- Catalog Listing 有效；
- 所有 critical subscription 已确认；
- 市场状态已完成 warm-up；
- snapshot + delta 已同步；
-数据 freshness 满足策略要求；
-账户、订单、持仓与 Venue 已恢复和对账；
- Ledger/Portfolio/Risk Projection 已重建；
- Kill Switch 未阻止对应行为；
- Capture 和 durable store 处于安全状态。

## 11. Application Runtime 与结构化并发

### 11.1 Runtime 职责

Application Runtime 负责组合和监督：

- Raw Capture Writer；
- Venue Market Stream；
- Private/User Stream；
- Canonical Normalizer；
- Subscription Controller；
- Market Projectors；
- Strategy Runtime；
- Risk/Governance；
- Command Outbox Dispatcher；
- Order/Execution Ingestion；
- REST Recovery/Backfill；
- Funding/Settlement Ingestion；
- Reconciliation；
- Monitoring/Alerting；
- Account Lock Heartbeat；
- Capture Session Finalizer。

### 11.2 启动顺序

```text
Load Config / Contracts
  -> Acquire Account Locks
  -> Open Durable Stores
  -> Recover Order/Ledger/Cursors
  -> Resolve Unknown External State
  -> Rebuild Projections
  -> Reconcile Venue State
  -> Start Capture Writers
  -> Start Private Streams
  -> Start Market Streams
  -> Apply Subscription Plan
  -> Warm Market State
  -> Run Readiness Gates
  -> Start Strategy Decisions
  -> RUNNING
```

### 11.3 停止顺序

```text
Stop New Strategy Decisions
  -> Stop New Non-reducing Commands
  -> Drain Durable Outbox or mark pending
  -> Persist Consumer Cursors
  -> Stop Market/Private Streams
  -> Flush Capture Writers
  -> Finalize Session Manifest
  -> Stop Background Recovery
  -> Release Account Locks
  -> STOPPED
```

### 11.4 失败等级

| 失败 | 默认处理 |
|---|---|
| Lossy Quote Consumer 落后 | conflation + warning |
| Order Book gap | invalidate book + snapshot recovery |
| Raw Capture 不可写 | reduce-only 或停止策略 |
| Private Stream 断开 | reduce-only + REST recovery |
| Order 状态 UNKNOWN | fail closed，禁止重提 |
| Ledger/Store 不可写 |停止新风险，触发 critical |
| Strategy task 崩溃 |隔离策略，按部署策略决定 Runtime 是否继续 |
| Reconciliation mismatch | Kill Switch / reduce-only |
| Contract version 不兼容 |拒绝启动 |

## 12. Execution 改造

### 12.1 Durable Command Outbox

现有 Coordinator 在调用 Venue 前持久化 Order，这一正确性必须保留，并演进为显式 outbox：

```text
Intent
  -> Risk Approval
  -> Execution Plan
  -> persist Order + Command in one transaction
  -> local CommandAccepted
  -> async dispatcher
  -> Venue
  -> async Venue Order Event
```

Outbox record 至少包含：

- command ID；
- client order ID；
- strategy/intent/correlation/causation ID；
- account/instrument/listing；
- payload contract version；
- created/available/attempt timestamps；
- attempt count；
- dispatch state；
- last transport result；
- Venue proof/reference。

### 12.2 Ack 语义

同步 API 返回不得再被视为唯一权威订单状态。

需要区分：

- 本地命令已持久接收；
- 请求已发送；
- Venue transport 返回；
- Venue 订单已确认；
- 订单状态来自 WS；
- 订单状态来自 REST recovery；
- 订单最终状态已持久提交。

任意超时必须通过 client order ID / venue order ID 恢复，不允许直接重提。

### 12.3 私有事件

Order、Fill、Account Event 使用 Critical Durable Channel：

- 先归一化身份；
- 与 REST recovery 使用相同 external key；
- durable ingestion 后再推进 Projection；
- cursor 与业务事实同事务提交；
-重复事件幂等；
-冲突事件触发 critical fault；
-消费者不得以进程内 set 作为正式去重依据。

## 13. 研究、回测、模拟与实盘统一

### 13.1 统一维度

所有模式共享：

- Domain facts；
- Canonical Events；
- Projectors；
- Strategy contract；
- Intent；
- Risk/Governance；
- Execution Plan；
- Order State Machine；
- Execution Event；
- Ledger reducer；
- Portfolio/Risk/Strategy Position；
- Monitoring event types；
- Audit identity。

模式之间只允许以下差异：

| 维度 | Study | Backtest | Historical Sim | Live Sim/Paper | Testnet/Live |
|---|---|---|---|---|---|
| Event Source | Frozen Release | Frozen Release | Frozen Release | Live Venue | Live Venue |
| Clock | Analysis Clock | Replay Clock | Replay Clock | System Clock | System Clock |
| Execution Driver | 无/分析 | Fill Model | Simulated Venue | Simulated/Paper Venue | Real Venue |
| Latency | 可分析 | 模型化 | 模型化 | 实际数据延迟+模拟执行 | 实际 |
| Persistence | Study Artifact | Backtest Artifact | Runtime Store 可选 | Runtime Store | Runtime Store |
| Safety Gate | Study Validation | Backtest Gate | Simulation Gate | Paper Gate | Live Gate |

### 13.2 Study

Study 使用 `DatasetClient` 解析冻结 Release，输出：

- Study Input Snapshot；
- code/environment version；
- feature lineage；
- hypothesis/claim；
- validation evidence；
- promotion decision。

Study 可以使用批处理 API，但产出的正式策略逻辑必须能够映射到流式 Strategy contract，不允许形成只有 Notebook 能运行的第二套策略实现。

### 13.3 Backtest

Backtest 使用 Canonical Event Replay 和同一 Projector：

```text
Release EventSource
  -> Market Projector
  -> Strategy
  -> Intent
  -> Risk
  -> Simulated Execution Driver
  -> Execution Event
  -> Order State + Ledger
```

禁止策略直接读取未来完整 DataFrame。任何窗口和特征必须由 available-time 驱动的状态构建。

### 13.4 Historical Simulation

Historical Simulation 比传统 Backtest 更接近 Runtime：

- 使用异步 EventSource；
-运行真实队列和 task supervision；
-注入 latency、disconnect、gap、partial fill 和 restart；
-使用 Runtime Store；
-验证 consumer lag、recovery 和 graceful shutdown。

它是进入 paper/testnet 前的关键阶段。

### 13.5 Live Simulation / Paper

使用真实实时行情、真实订阅和 Capture，但 Execution Driver 为模拟或 paper Venue。

此模式验证：

- live sequence 和 gap；
-订阅 reconciliation；
-实时策略延迟；
-订单状态机；
-重启恢复；
-对账和 Kill Switch；
-线上 Capture 到 Replay 的一致性。

### 13.6 Testnet / Live

只替换 Execution Gateway 和环境安全配置。Strategy 不允许基于环境分叉业务逻辑；环境差异通过 capability、limits、fees、latency、listing 和 deployment config 表达。

Live 必须额外要求：

-策略和数据达到生产晋级等级；
-合约版本锁定；
-L4 soak 通过；
-restart/kill-switch/recovery drill 通过；
-capture 和 durable store 容量满足要求；
-明确的最大风险和人工操作手册。

## 14. Python/Rust 接入规范

### 14.1 Contract First

先定义语言无关契约，再选择 transport。不得让 Rust 直接实现 Python `Protocol`，也不得让 Python 直接读取 Rust 领域 struct 内存布局。

建议新增：

```text
contracts/
├── common.proto
├── market_data.proto
├── execution.proto
├── account.proto
├── control.proto
├── compatibility.md
└── test_vectors/
```

具体格式可在 Spike 后决定使用 Protobuf、FlatBuffers 或其他方案，但必须满足：

-稳定 schema ID/version；
-Decimal 精确编码；
-UTC nanosecond 时间；
-向前/向后兼容规则；
-未知字段处理；
-跨语言 contract vectors；
-编码、解码和 round-trip 测试；
-错误输入和边界值测试。

### 14.2 Rust 组件适合承担的职责

优先级从高到低：

1. WebSocket/HTTP transport；
2. Venue raw decode；
3. sequence/gap 检测；
4. order book reconstruction；
5. Canonical Event 编码；
6. Raw/Canonical capture batching；
7. Shared Memory publisher；
8. Execution transport gateway。

短期不优先迁移：

- Domain；
-Strategy；
-Risk policy；
-Order State；
-Ledger；
-Study Governance。

### 14.3 Sidecar 边界

首个 Rust 组件建议为 `market-gateway` sidecar：

```text
Control: Unix socket/gRPC
  subscribe/unsubscribe
  health
  session info
  schema negotiation

Data: shared memory or framed Unix socket
  canonical market events

Capture:
  raw frames
  sequence/gap metadata
```

Python Runtime 仍负责：

- Subscription Plan；
- Catalog identity；
- Strategy/Risk；
- Execution Intent；
- durable Order/Ledger；
-全局 readiness 和 safety。

### 14.4 Contract Test

每个 Connector，包括 Rust sidecar，必须通过同一套 contract tests：

- subscription apply/reconcile；
- reconnect 后恢复目标订阅；
- sequence/gap detection；
- raw -> canonical contract vector；
- Decimal/time round-trip；
- unknown field compatibility；
- slow consumer overflow；
- process restart/writer epoch；
- malformed payload isolation；
- health/readiness；
- graceful shutdown。

## 15. 可调试性设计

### 15.1 Trace 与因果链

系统必须能够形成：

```text
RawFrame
  -> CanonicalEvent
  -> MarketStateVersion
  -> StrategyDecision
  -> Intent
  -> RiskDecision
  -> ExecutionPlan
  -> Command
  -> VenueOrderEvent
  -> Execution
  -> LedgerTransaction
```

每个节点必须保留 trace/correlation/causation identity。

### 15.2 结构化故障事件

不得使用捕获全部异常后静默重试的模式。每次错误至少记录：

```text
runtime_id
task_name
connector
stream_id
session_id
connection_epoch
subscription_revision
stage
exception_type
message
traceback reference
last_raw_offset
last_source_sequence
last_canonical_sequence
queue_depth
consumer_lag
retry_attempt
retry_at
occurred_at
```

### 15.3 Debug Tap

Event Channel 支持只读 debug tap，但不得让调试消费者阻塞生产消费者。

Debug tap 可以：

-按 instrument/message type/trace ID 过滤；
-输出事件阶段与时间；
-保存短窗口 capture；
-比较两个 Projector 或 Strategy；
-检查 queue lag 和 sequence gap。

### 15.4 CLI 目标

后续应提供：

```text
kairospy runtime tasks
kairospy runtime streams
kairospy runtime queues
kairospy runtime trace <trace-id>
kairospy data trace --instrument ... --start ... --end ...
kairospy data explain-event <event-id>
kairospy data inspect-gap <stream-id>
kairospy data replay-session <session-id>
kairospy data compare-session <live-session> <replay-run>
kairospy execution explain-order <client-order-id>
```

### 15.5 Replay 验收

对冻结 Session，Replay 至少验证：

- Canonical Event count/hash 相同；
- Projector final hash 相同；
- Strategy Decision 序列相同；
- Intent 序列相同；
-在确定性 Execution Driver 下 Order/Ledger hash 相同；
-所有 gap、warning 和 fault 均可重现或明确标记为 transport-only。

## 16. 可观测性

每个流和消费者至少暴露：

- connected；
- connection epoch；
- subscription revision；
- messages/bytes received；
- decode failures；
- source sequence gaps；
- reconnect count；
- last event time；
- receive/event lag；
- queue depth/capacity；
- consumer sequence/lag；
- overflow/conflation count；
- capture lag；
- processing latency histogram；
- last successful checkpoint；
- health/readiness status。

Execution 额外暴露：

- outbox pending/oldest age；
- submit latency；
- Ack/Fill latency；
- UNKNOWN orders；
- recovery lag；
- duplicate/conflict event count；
- reconciliation mismatch。

## 17. 测试策略

### 17.1 单元测试

- Envelope/schema validation；
- channel overflow policy；
- sequence/gap detection；
- snapshot + delta；
- subscription diff；
- projector reducer；
- outbox state transition；
- consumer idempotency。

### 17.2 Contract Tests

同一套 Connector contract test 运行于：

- Python fake；
- Simulated Connector；
- Binance/IBKR/Massive Connector；
-未来 Rust gateway。

### 17.3 Deterministic Golden

- raw -> canonical hash；
- canonical -> market state hash；
- release -> strategy decision hash；
- intent -> simulated execution hash；
- execution -> ledger hash；
- full runtime restart hash。

### 17.4 Fault Matrix

必须覆盖：

- WebSocket disconnect；
- reconnect 重复事件；
- sequence gap；
- shared-memory consumer overflow；
- raw capture writer slow/failure；
- decoder panic/exception；
- strategy task crash；
- crash after outbox commit before Venue；
- Venue accept before local Ack persist；
- partial fill then crash；
- private WS missing fill + REST backfill；
- REST/WS duplicate；
- SQLite busy/full/corruption simulation；
- incompatible contract version；
- graceful shutdown timeout。

### 17.5 Soak

Paper/Testnet L4 至少验证：

- 24–72 小时持续运行；
-无未解释 sequence gap；
-无 critical event loss；
- capture 可完整冻结；
-随机重连可恢复；
-进程重启后 READY；
-Kill Switch drill；
-Replay 与 live projection/decision 对比通过；
-队列、磁盘和内存水位有安全余量。

## 18. 分阶段实施路线

### Phase 0：冻结决策与边界

目标：防止在异步化过程中再次形成平行模型。

工作项：

-批准本文；
-明确现有两套 Market Event 模型的收敛目标；
-确定事实所有权；
-列出正式 Runtime 组合根；
-禁止新增 Venue-specific 上层事件类型；
-禁止新增直接物理路径读取和未治理实时缓存。

退出标准：架构测试能够阻止新的平行事实模型。

### Phase 1：统一 Contract 与 Replay 接口

目标：先稳定语义，不先做共享内存。

工作项：

-定义统一 Envelope；
-定义 typed market payload；
-定义 EventSource/CommandSink；
-将 Dataset Release Replay 适配到统一异步 EventSource；
-建立 Python contract contract vectors；
-为现有 Strategy 建立流式运行入口。

退出标准：同一 Strategy 可从 Release EventSource 和 Synthetic Live EventSource 运行。

### Phase 2：Python 异步 Runtime

目标：使用成熟 asyncio 组件跑通完整语义。

工作项：

-引入结构化 task supervision；
-实现有界 typed channels；
-统一 Massive/Binance/IBKR stream lifecycle；
-实现 subscription declaration/planner/reconciliation；
-实现 raw/canonical capture；
-实现 queue/lag/fault metrics；
-将周期 recovery 作为受监督异步任务运行。

退出标准：单进程 Python Runtime 可以长期运行、停止、重启和 Replay。

### Phase 3：Execution Outbox 与私有事件流

目标：下单从同步调用栈演进为 durable command/event 流。

工作项：

-新增 command outbox；
-异步 dispatcher；
-区分 local command accepted 与 Venue order acknowledged；
-统一 WS/REST order/fill identity；
-关键事件 durable channel；
-完善 UNKNOWN/recovery/reconciliation。

退出标准：所有 crash window 均不会重复下单或遗漏 Ledger 事实。

### Phase 4：历史模拟与 Live Paper 统一

目标：验证全模式共享相同 Runtime 组件。

工作项：

-Historical Simulation 使用正式异步 Runtime；
-Live Paper 使用真实行情和 Capture；
-Replay Session 对比 live decision；
-建立从 Study Artifact 到 Deployment 的 promotion manifest。

退出标准：Study -> Backtest -> Historical Sim -> Live Paper 使用同一 Strategy 和 Projector。

### Phase 5：Rust Market Gateway Spike

目标：在语义稳定后验证跨语言和共享内存，不提前扩大范围。

工作项：

-生成 Rust/Python contract binding；
-实现单 Venue、单产品线 market gateway；
-控制面使用 Unix socket/gRPC；
-数据面先比较 framed socket 与 shared memory；
-实现 writer epoch、gap detection、schema negotiation；
-运行相同 contract/contract/fault tests；
-对比延迟、吞吐、CPU、内存和调试成本。

退出标准：Rust 与 Python EventSource 可互换，上层 hash 和行为一致。

### Phase 6：生产化与可选 Rust Execution Gateway

只有在 Market Gateway 稳定且确有收益后，才评估 Execution Gateway Rust 化。

退出标准：不改变 Order State、Ledger、Recovery 和审计语义即可切换实现。

## 19. 明确不做的事情

本次改造不做：

-直接搬迁 `kairospy_v2` crate；
-重新自研完整 HTTP/TLS/WebSocket 网络栈；
-让所有消息都经过共享内存；
-使用共享内存替代持久订单、Fill 和 Ledger；
-同时维护 Python 和 Rust 两套领域模型；
-为了形式上的异步而把纯计算函数改成 `async`；
-在 Contract 稳定前进行大规模 Rust 重写；
-让 Strategy 直接依赖 Venue symbol、raw payload 或 transport；
-让 Replay 使用与 Live 不同的 Strategy API；
-捕获所有异常后无证据地无限重试；
-为了吞吐静默丢弃或覆盖关键事件。

## 20. 完成定义

只有同时满足以下条件，本次改造才算完成：

### 架构

-只有一套正式 Canonical Event/Command contract；
-只有一个正式 Application Runtime 组合根；
-Study、Backtest、Simulation、Paper 和 Live 使用同一 Strategy/Projector/Intent/Risk/Order/Ledger 语义；
-Python/Rust connector 通过同一语言无关契约接入；
-共享内存严格限定为可替换数据面 transport。

### 正确性

-所有关键事件可幂等重放；
-所有订单 crash window 可恢复；
-Fill、Order、Ledger、cursor 保持事务一致；
-gap、overflow、duplicate 和 conflict 均有明确处理；
-无法证明外部状态时 fail closed。

### 可调试性

-任意线上事件可定位到 raw capture；
-任意 Strategy Decision 可追溯到输入事件；
-任意 Order/Fill 可追溯到 Intent 和 Risk Decision；
-冻结 Session 可在本地确定性重放；
-任务、流、队列、lag 和 fault 可通过 CLI/metrics 检查；
-不存在无结构化证据的静默重试和静默丢数。

### 运行验收

-单元、contract、golden、fault matrix 全部通过；
-Historical Simulation 验收通过；
-Live Paper/Testnet 24–72 小时 soak 通过；
-restart、reconnect、recovery、kill switch drill 通过；
-live capture 与 replay 的 projection/decision 对比通过；
-Rust gateway 若启用，可在不修改上层业务代码的情况下切回 Python 实现。

## 21. 第一批实施任务

本轮建议先实施以下最小闭环，不立即开发共享内存：

1. 新增统一 `CanonicalEventEnvelope` 和 typed market payload；
2. 写出旧 `domain.event` 与 `market_data.events` 的迁移映射和删除计划；
3. 定义异步 `EventSource`、`EventSink`、`CommandSink` 和 Connector lifecycle；
4. 将 `ReplayEventFeed` 适配为异步 EventSource；
5. 建立 `RawFrame -> CanonicalEvent` golden contract tests；
6. 新增声明式 `StrategyDataRequirement` 和 `SubscriptionPlan`；
7. 建立有界通道、overflow policy、lag 和 gap 事件；
8. 将 Massive stream 作为首个统一异步 Connector 试点；
9. 将 raw journal、异常证据和 session manifest 提升为通用 Runtime 能力；
10. 用一条现有策略贯通 Release Replay 与 Live Paper 两种 EventSource；
11. 在语义和调试闭环稳定后，再开展 Rust market gateway 与共享内存 Spike。

这组任务完成后，系统将第一次具备清晰的异步数据流主干，同时保留当前已经建立的数据治理、订单安全和账务正确性，并为后续 Rust 替换留下稳定边界。
