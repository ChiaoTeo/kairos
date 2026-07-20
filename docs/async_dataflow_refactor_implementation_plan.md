# Kairos 异步数据流改造实施计划

状态：Proposed  
版本：1.0  
基线日期：2026-07-17  
上位设计：`docs/async_dataflow_runtime_refactor.md`
用途：将架构原则转换为可以分工、实施、验收和追踪的项目工作

## 1. 文档目标

本文回答四个实施问题：

1. 本次系统改造要完成哪些工作；
2. 每项工作如何实施；
3. 每项工作的验收边界和证据是什么；
4. 各模块如何协作、允许依赖什么、禁止依赖什么。

本文是实施清单和验收依据，不重复讨论为什么选择异步数据流、为什么不迁移 `kairospy_v2`，相关决策以上位设计为准。

## 2. 项目完成目标

最终需要交付一套统一运行体系：

```text
Study
  -> Governed Dataset Release
  -> Deterministic Backtest
  -> Historical Runtime Simulation
  -> Live Market Paper Runtime
  -> Testnet
  -> Live
```

上述模式共享：

- Canonical Event Contract；
- Market/Account/Order Projector；
- Strategy Contract；
- Intent；
- Risk/Governance；
- Execution Plan；
- Durable Order State；
- Execution Event；
- Ledger；
- Recovery、Reconciliation 和 Monitoring。

模式间只允许替换：

- Event Source；
- Clock；
- Execution Driver；
-运行环境的 capability、limit 和 safety policy。

## 3. 工作分解总览

本次改造分为 12 个 Workstream：

| ID | Workstream | 主要交付物 | 优先级 |
|---|---|---|---|
| W0 | 架构边界与基线 | ADR、依赖规则、基线证据 | P0 |
| W1 | 统一消息 Contract | Envelope、typed payload、兼容规则 | P0 |
| W2 | Event Source 与通道 | 异步端口、有界通道、背压和 gap | P0 |
| W3 | 数据 Capture 与 Replay | Raw/Canonical Capture、Session Release | P0 |
| W4 | 声明式订阅 | Requirement、Planner、Reconciliation | P0 |
| W5 | Application Runtime | 结构化并发、生命周期、任务监督 | P0 |
| W6 | 市场数据 Connector 收敛 | Massive/Binance/IBKR 统一接入 | P1 |
| W7 | Market State Projection | Quote/Trade/Book/Derivative State | P1 |
| W8 | Execution 异步化 | Durable Outbox、Dispatcher、Private Events | P0 |
| W9 | 全运行模式统一 | Study/Backtest/Sim/Paper/Live | P0 |
| W10 | 调试与可观测性 | Trace、Fault、CLI、Metrics | P0 |
| W11 | Rust Gateway Spike | 跨语言 Contract、可选共享内存 | P2 |

P0 工作构成首个完整闭环；P1 完成真实 Venue 的生产化；P2 只能在前述语义稳定后开始。

## 4. 总体依赖关系

```text
W0 架构边界
 │
 ├──▶ W1 统一 Contract
 │      ├──▶ W2 Event Source/Channels
 │      │      ├──▶ W5 Application Runtime
 │      │      └──▶ W7 Market Projections
 │      ├──▶ W3 Capture/Replay
 │      ├──▶ W4 Subscription
 │      ├──▶ W6 Venue Connectors
 │      └──▶ W8 Execution Async
 │
 ├──▶ W9 全运行模式统一
 │       依赖 W2/W3/W5/W7/W8
 │
 ├──▶ W10 调试与可观测性
 │       横跨 W1-W9，不是最后补做
 │
 └──▶ W11 Rust Gateway
         依赖 W1/W2/W3/W4/W6/W10 稳定
```

关键路径：

```text
W0 -> W1 -> W2 -> W5 -> W8 -> W9
```

Capture、Replay 和调试能力必须与主链路同步建设，不能等功能完成后补做。

## 5. W0：架构边界与实施基线

### 5.1 要完成的工作

- 记录正式架构决策；
- 明确唯一正式 Application 组合根；
- 明确 Market Event、Order、Execution、Ledger 的唯一事实模型；
- 建立模块依赖规则；
- 建立当前测试、性能、数据质量和 Runtime 基线；
- 标记需要迁移和最终删除的旧接口；
- 建立项目实施进度文档。

### 5.2 如何完成

1. 为以下决策新增 ADR：
   - 单一 Canonical Event Contract；
   -数据面与控制面分离；
   -关键事实 durable、Projection 可重建；
   -Live 与 Replay 使用同一 EventSource 接口；
   -共享内存只作为可替换数据面；
   -Rust Contract First；
   -at-least-once + idempotent ingestion。
2. 扩展 AST/import 架构测试；
3. 输出当前模块依赖图；
4. 固定当前 Golden、Runtime Store schema 和 Dataset Catalog schema；
5. 为旧接口标记 owner、迁移目标和删除条件。

### 5.3 验收边界

必须满足：

- 存在唯一正式组合根定义；
- Domain 不依赖 Data、Connector、Runtime、Storage；
- Strategy 不依赖具体 Venue Connector；
- Risk 不依赖 WebSocket/HTTP/SQLite 实现；
- Connector 不直接修改 Ledger、Portfolio 或 Strategy State；
- 新增平行 Event/Order/Ledger 类型会被架构测试阻止；
- 基线测试命令、通过数量和外部跳过项已记录；
- 所有待迁移接口都有明确删除条件。

验收证据：

- ADR 文件；
- 架构测试；
-依赖扫描报告；
-基线测试报告；
-迁移清单。

## 6. W1：统一消息 Contract

### 6.1 要完成的工作

- 合并 `domain.event.EventEnvelope` 与 `market_data.events.MarketEventEnvelope` 的正式语义；
- 定义统一 Envelope；
- 定义 typed market payload；
- 定义 execution/account/control command 和 event；
- 定义 Schema ID、版本和兼容规则；
- 定义 Decimal、时间、ID、enum 和 optional 字段编码；
- 建立 Python contract vectors；
- 为未来 Rust binding 准备语言无关 schema。

### 6.2 如何完成

1. 建立字段映射表，列出两套 Event 模型的字段、语义和消费者；
2. 定义 `CanonicalEventEnvelope`，不立即删除旧模型；
3. 为 Quote、Trade、Bar、Book、Mark、Index、Funding、Greeks、Trading Status 建立 typed payload；
4. 编写旧模型到新模型的单向迁移 Connector；
5. 禁止新代码直接扩展旧模型；
6. 为每个消息类型生成 JSON/binary contract vectors；
7. 定义兼容测试：旧 reader 读新 optional 字段、新 reader 读旧版本、未知 enum 处理；
8. 迁移完成后删除旧模型和转换层。

### 6.3 验收边界

必须满足：

- 正式 Runtime、Replay 和 Capture 使用同一 Envelope；
- payload 不再以无约束 Mapping 作为正式跨模块契约；
- Decimal round-trip 精确；
- nanosecond UTC 时间 round-trip 不丢精度；
-每个消息有 schema ID/version；
-每个消息有稳定 identity、source、partition 和因果字段；
-旧模型消费者迁移清单归零后才允许删除旧模型；
- Python contract vectors 全部通过；
-同 identity 冲突内容能够被检测。

不在本 Workstream 内完成：

- Rust sidecar 实现；
-共享内存实现；
-所有 Venue Connector 迁移。

## 7. W2：Event Source、Sink 与有界通道

### 7.1 要完成的工作

- 定义异步 `EventSource`、`EventSink`、`CommandSink`；
- 定义 ChannelSpec；
- 实现有界通道；
- 实现 delivery、overflow、ordering 和 recovery 语义；
- 实现 consumer sequence、lag 和 gap；
- 实现 Critical、Stateful、Lossy、Capture 四类通道；
- 建立 deterministic test channel。

### 7.2 如何完成

1. 先用 Python `asyncio` 实现，不先引入共享内存；
2. Channel 创建时强制提供：
   - capacity；
   - delivery semantics；
   - partition/order scope；
   - overflow policy；
   - criticality；
   - recovery policy；
3. 实现以下通道：
   - `DurableCriticalChannel`；
   - `OrderedStateChannel`；
   - `ConflatedLatestChannel`；
   - `CaptureChannel`；
4. 对 overflow 生成结构化事件，不允许静默丢弃；
5. 提供测试 hook，能够暂停消费者和注入慢处理；
6. EventSource 只负责生产事件，不直接调用 Strategy；
7. Consumer 独立持有 cursor/sequence，不把全局进度藏在 Source 中。

### 7.3 验收边界

必须验证：

- 有界容量真实生效；
-慢消费者不会导致无限内存增长；
- Lossy channel 覆盖时产生 count/lag 证据；
- Ordered channel 发生 gap 后不会继续提供看似连续的状态；
- Critical channel 不会静默丢失；
-取消任务后生产者和消费者可在超时内退出；
-每条通道可查看 depth、capacity、producer sequence、consumer sequence 和 lag；
-相同测试可替换内存 transport 实现而不修改消费者。

验收测试：

- overflow matrix；
-slow consumer；
-producer crash；
-consumer crash/restart；
-cancellation；
-ordering；
-conflation；
-gap recovery。

## 8. W3：Raw Capture、Canonical Capture 与 Replay

### 8.1 要完成的工作

- 定义 `RawFrame`；
- 实现 append-only raw journal；
- 实现 canonical capture writer；
- 定义 Capture Session；
- 实现 Session Manifest/Quality/Coverage；
- 将 Capture Session 发布为不可变 Dataset Release；
- 实现异步 Replay EventSource；
- 实现 live/replay hash 对比。

### 8.2 如何完成

1. Raw Capture 先使用简单、可检查的分块文件格式；
2. 每条 RawFrame 保存 session、connection epoch、receive sequence、timestamp、subscription revision 和 checksum；
3. Decoder 处理前先提交或排队进入可靠 raw capture；
4. Canonical Capture 保存统一 Envelope；
5. Session 结束时生成：
   - manifest；
   - file hash；
   - sequence gap；
   - reconnect；
   - decode/validation fault；
   - coverage；
   - quality；
6. Replay EventSource 按 available time 和 canonical ordering 输出；
7. Replay 不读取浮动 alias，只接受冻结 Release/Session；
8. 使用现有 Data Catalog 和 Release 治理，不建立第二套实时 Catalog。

### 8.3 验收边界

必须满足：

- 任意 canonical event 可定位到 raw capture offset；
- Decoder 崩溃后原始输入仍可重新处理；
- Session 可冻结为不可变 Release；
-相同 Session Replay 的 event count/hash 一致；
-Replay EventSource 与 Live EventSource 使用同一消费者接口；
-Capture writer lag 可观测；
-Capture 不可写时触发明确降级，不允许继续无证据运行；
-存在 malformed raw frame、decode failure 和 gap 的质量报告。

## 9. W4：声明式订阅与能力协调

### 9.1 要完成的工作

- 定义 Strategy Data Requirement；
- 定义 Account/Execution Capability Requirement；
- 实现 Subscription Planner；
- 实现目标订阅与实际订阅 reconciliation；
- 实现 revision、plan hash 和变更审计；
- reconnect 后恢复目标订阅；
- 将 subscription readiness 接入 Application。

### 9.2 如何完成

1. Strategy/Deployment 声明 instrument、channel、depth、freshness、capture 和 warm-up；
2. Planner 通过 Catalog 将 InstrumentId 映射为有效 Venue Listing；
3. Planner 通过 Connector capability 验证支持程度；
4. 合并多个策略的重复需求；
5. 生成连接级 Subscription Plan；
6. 对比 actual state，仅生成增量 subscribe/unsubscribe；
7. 保存每次 plan revision、原因和 Venue response；
8. reconnect 时以 target state 为准重建，不以断线前内存 callback 为准。

### 9.3 验收边界

必须验证：

-多个策略共享同一订阅时只建立一次 Venue subscription；
-一个策略停止不会误取消其他策略仍需要的订阅；
-不支持的 channel/depth 在启动前明确拒绝；
-Listing 过期或缺失阻止订阅；
-reconnect 后 target/actual 最终一致；
-plan revision 可解释每次订阅变化；
-订阅未确认、数据未 warm-up 或 freshness 不满足时策略不 READY。

## 10. W5：Application Runtime 与任务监督

### 10.1 要完成的工作

- 将 Application 从状态容器演进为真实异步 Runtime；
- 实现结构化 Task Supervisor；
- 管理所有长期任务；
- 实现确定启动、降级、恢复和停止顺序；
- 将 heartbeat、recovery、reconciliation 改为异步受监督服务；
- 统一 task fault 传播；
- 保留现有 Runtime Store、Account Lock 和安全状态。

### 10.2 如何完成

1. 保留 `KairosApplication` 的状态机和持久状态；
2. 新增异步 Runtime facade，避免一次性重写所有调用者；
3. 使用 `asyncio.TaskGroup` 或等价结构化并发；
4. 每个 task 注册：name、criticality、restart policy、health probe、shutdown timeout；
5. Startup 分阶段执行 recovery、reconciliation、capture、stream、warm-up、strategy activation；
6. Shutdown 先停止新决策，再 drain outbox、保存 cursor、flush capture；
7. Critical task 退出触发 reduce-only 或 failed state；
8. 测试中使用 FixedClock/controlled scheduler，避免真实 sleep。

### 10.3 验收边界

必须满足：

- `RUNNING` 表示关键长期任务真实存活，而不只是状态字段；
-启动未完成 recovery/reconciliation 时无法运行 Strategy；
-任何 task 可列出状态、最后事件、错误和 restart count；
-critical task 崩溃会传播到 Runtime 安全状态；
-graceful shutdown 在规定超时内结束；
-Capture、cursor、outbox 在停止时保持可恢复；
-重启后 unresolved order 不会重复提交；
-所有测试无未回收 task 和资源泄漏。

## 11. W6：市场数据 Connector 收敛

### 11.1 要完成的工作

- 将 Massive、Binance 和 IBKR 接入统一生命周期；
- 分离 Transport、Venue Codec、Normalizer 和 Port Implementation；
- 统一 RawFrame、sequence、reconnect 和 fault；
- 统一 health/readiness；
- 清除 Connector 直接调用上层 Consumer 的特殊路径。

### 11.2 推荐实施顺序

1. Massive：已有 asyncio、raw journal 和 gap hook，作为第一个试点；
2. Binance Public Stream：将阻塞 receiver 包装或替换为 async transport；
3. Binance Private Stream：统一 Fill/Order/Account Event；
4. IBKR：将 callback 适配为 EventSource，由 Runtime 消费；
5. 最后统一 Reference/Snapshot HTTP 辅助路径。

### 11.3 如何完成

每个 Connector 固定四层：

```text
Transport
  -> Venue Codec
  -> Normalizer
  -> Port Implementation
```

- Transport 只负责连接、认证、心跳、重连和 bytes；
- Codec 只负责 Venue 协议；
- Normalizer 只负责 Canonical Contract；
- Port Implementation 只负责生命周期、capability 和 EventSource/Sink。

### 11.4 验收边界

每个 Connector 必须通过同一 Contract Test：

- connect/authenticate；
-subscribe/unsubscribe；
-reconnect/resubscribe；
-raw capture；
-decode contract vector；
-sequence/gap；
-malformed payload；
-slow consumer；
-health/readiness；
-graceful shutdown。

禁止：

- Connector 直接更新 Strategy State；
- Connector 直接写 Ledger；
- Venue raw object 离开 Connector；
-无结构化 fault 的 `except Exception: retry`；
-仅靠进程内 set 提供正式幂等。

## 12. W7：Market State Projection

### 12.1 要完成的工作

- 建立统一 Market State Projector；
- 支持 Quote、Trade、Bar、Book、Mark、Index、Funding、Greeks；
- 支持 snapshot + delta；
- 支持 stale/invalid 状态；
- 为 Strategy 提供只读 State View；
- 支持从 Replay 重建相同状态；
-生成 state version/hash。

### 12.2 如何完成

1. Projector 只消费 Canonical Event；
2. Reducer 尽量保持纯函数或确定性状态转换；
3. 每次更新生成单调 state version；
4. 保存输入 event ID 和 source sequence；
5. Book gap 时将状态标记 invalid，等待新 snapshot；
6. Strategy 读取 immutable/read-only view；
7. Latest Value Store 只是 Projection 的发布形式，不成为事实源。

### 12.3 验收边界

- Live 和 Replay 输入相同事件后 state hash 一致；
-乱序、重复和 gap 行为有固定测试；
-stale 状态阻止需要新鲜数据的策略；
-Projector 不访问 Venue、网络或持久化实现；
-进程重启可通过 Capture/Release 重建；
-Strategy 无法直接修改 Projection。

## 13. W8：Execution Durable Outbox 与私有事件流

### 13.1 要完成的工作

- 将同步 Venue submit 演进为 durable command outbox；
- 新增异步 Dispatcher；
- 区分本地 CommandAccepted 与 Venue Ack；
- 统一 Order/Fill/Account Event；
- 将 Private WS 和 REST recovery 接入相同 ingestion；
- 保留并扩展现有 Order State、Runtime Store 和 Ledger 事务；
-建立 outbox retry、UNKNOWN 和 recovery 规则。

### 13.2 如何完成

1. 在同一事务中持久化 Order 和 Outbox Command；
2. 返回本地 command receipt；
3. Dispatcher 按 account/venue 顺序或限流发送；
4. 保存 dispatch attempt 和 transport result；
5. Venue Ack/Reject/Fill 通过事件流进入 Durable Ingestion；
6. timeout 进入 UNKNOWN，不自动生成新 client order ID 重提；
7. REST recovery 与 WS 使用同一 external identity；
8. Fill、Order state、Ledger、cursor 同事务提交；
9. Kill Switch 和 reduce-only 在生成和 dispatch 两处检查。

### 13.3 验收边界

必须通过以下 crash window：

- Order/Outbox 事务前崩溃：无订单；
-事务后、发送前崩溃：重启后发送一次；
-发送后、Venue Ack 前崩溃：恢复，不重复提交；
-Venue 接受后、本地 Ack 前崩溃：通过 client ID 恢复；
-partial fill 后崩溃：只入账一次；
-WS 漏 Fill：REST backfill；
-WS/REST 重复：幂等；
-相同 external ID 冲突：拒绝并 critical；
-cancel timeout：UNKNOWN + recovery；
-Kill Switch 后禁止新非 reduce-only dispatch。

## 14. W9：研究、回测、模拟、Paper 和 Live 统一

### 14.1 要完成的工作

- 定义统一 `RunModeComposition`；
- Study 输出可晋级 Strategy Artifact；
- Backtest 使用 Canonical Event/Projector；
- Historical Simulation 使用正式异步 Runtime；
- Paper Trading 使用实时 EventSource + 模拟 Execution Driver；
- Testnet/Live 只替换 Connector 和 Safety Policy；
-建立 Promotion Manifest 和阶段门禁。

### 14.2 如何完成

每个模式显式组合：

```text
EventSource
Clock
ExecutionDriver
PersistencePolicy
SafetyPolicy
CapturePolicy
```

不得在 Strategy 中使用：

```python
if live:
    ...
elif backtest:
    ...
```

环境差异通过依赖注入和 capability 表达。

### 14.3 阶段门禁

#### Study -> Backtest

-冻结输入 Release；
-研究 Claim 和 Validation 通过；
-策略逻辑可由流式接口运行；
-不存在未来数据依赖。

#### Backtest -> Historical Simulation

-确定性 audit hash；
-保守和压力成交模型；
-风险和费用完整；
-同一 Canonical Event/Projector。

#### Historical Simulation -> Paper Trading

-异步队列、restart、gap、partial fill fault tests 通过；
-Runtime Store 可恢复；
-无未受监督 task。

#### Paper Trading -> Testnet

-实时 capture/replay 一致；
-订阅、freshness、reconnect 稳定；
-Kill Switch drill；
-对账通过。

#### Testnet -> Live

-24–72 小时 soak；
-真实 Connector contract tests；
-风险限额和人工 runbook；
-明确 live confirmation；
-无 unresolved critical fault。

### 14.4 验收边界

-同一 Strategy 类和配置可依次运行于所有模式；
-同一事件输入产生相同 Decision/Intent；
-模式差异可从 composition manifest 完整解释；
-每阶段有不可伪造的 Artifact/hash；
-不能跳过前置晋级门禁直接进入 live。

## 15. W10：调试、Trace 与可观测性

### 15.1 要完成的工作

- 建立端到端 correlation/causation；
- 定义 Runtime Fault Event；
- 建立 task、stream、queue、capture、execution metrics；
-实现 Debug Tap；
-实现 trace/explain/replay CLI；
-建立 live/replay comparison report。

### 15.2 如何完成

1. 在 Contract 阶段加入 trace 字段，不能后补；
2. RawFrame、CanonicalEvent、State、Decision、Intent、Command、Order、Execution 和 Ledger 保留因果链接；
3. 每个异步 task 自动记录 lifecycle；
4. 所有 retry 先生成 fault evidence；
5. Debug Tap 使用独立 lossy channel，不能阻塞生产；
6. CLI 从正式 store/capture 读取，不抓取内部对象；
7. 输出 machine-readable JSON 和简洁 human view。

### 15.3 验收边界

必须能够完成以下操作：

- 从 Order 找到 Intent、Risk Decision 和输入市场事件；
- 从 Strategy Decision 找到 Market State version 和 Canonical Event；
- 从 Canonical Event 找到 RawFrame；
-查看 task 是否存活、为何重启；
-查看 queue depth、lag 和 overflow；
-查看 stream reconnect、gap 和 subscription revision；
-冻结线上 Session 并本地 Replay；
-比较 live 与 replay 的 state/decision hash；
-异常重试不存在无证据黑洞。

## 16. W11：Rust Market Gateway 与共享内存 Spike

### 16.1 前置条件

以下条件全部满足后才允许开始：

- W1 Contract 稳定；
- W2 Channel semantics 稳定；
- W3 Capture/Replay 可用；
- W4 Subscription contract 稳定；
-至少一个 Python Connector 通过完整 Contract Test；
-W10 可以定位跨进程事件。

### 16.2 要完成的工作

- 生成 Python/Rust contract binding；
- 实现一个 Venue/产品线的 Rust market gateway；
-实现 control channel；
-比较 framed Unix socket 与 shared-memory ring；
-实现 writer epoch、sequence、gap、checksum、schema negotiation；
-运行与 Python Connector 相同的测试；
-输出性能和复杂度报告。

### 16.3 验收边界

- Python/Rust contract vectors 完全一致；
-上层 Runtime 无需修改 Strategy/Projector 即可切换；
-process restart 能检测 writer epoch；
-慢消费者能够检测 overflow；
-共享内存损坏或版本不兼容时拒绝消费；
-raw capture 足以重放 sidecar 输入；
-性能收益达到预先定义阈值；
-调试步骤不比 Python Connector 显著恶化；
-可通过配置切回 Python 实现。

未满足收益或调试要求时，Spike 可以结论为不采用共享内存，这不影响总体架构完成。

## 17. 模块协作边界

### 17.1 Domain

职责：稳定业务身份、值对象、业务事实和纯规则。

允许依赖：

- Python 标准库；
- Domain 内部模块。

禁止依赖：

- Data/Catalog 实现；
- Connector；
- Runtime；
- SQLite/Parquet；
- asyncio/WebSocket/HTTP；
-具体 Clock 实现。

### 17.2 Contracts

职责：跨模块、跨进程和跨语言消息协议。

允许依赖：

-稳定 ID/value primitive；
-schema runtime。

禁止依赖：

-Strategy、Risk、Ledger service；
-具体 Venue SDK；
-存储实现。

### 17.3 Catalog

职责：Instrument/Product/Listing point-in-time 定义。

对外提供：

- InstrumentId -> Definition；
- InstrumentId + Venue + time -> Listing；
- capability 验证所需产品事实。

禁止：

-管理实时 subscription；
-保存订单或行情状态；
-决定 Strategy 行为。

### 17.4 Data

职责：Dataset Product、Release、Capture 发布、查询、质量、lineage 和冻结。

依赖：

- Contracts；
-Catalog identity；
-Storage drivers。

禁止：

-管理 Runtime task；
-直接调用 Strategy；
-成为实时内存状态所有者；
-隐式联网满足 Backtest。

### 17.5 Market Data Runtime

职责：订阅、RawFrame、Canonical Event、sequence、channels、capture 和 projection 编排。

依赖：

- Contracts；
-Catalog ports；
-Connector ports；
-Runtime service supervisor；
-Data capture publisher。

禁止：

-生成交易 Intent；
-修改 Ledger；
-直接下单。

### 17.6 Strategy

职责：消费只读状态和 Clock，生成 Economic Intent。

依赖：

- Domain；
-Strategy Contract；
-只读 Market/Portfolio/Risk view ports。

禁止：

-Venue symbol/raw payload；
-WebSocket/HTTP；
-SQLite/Parquet 路径；
-直接调用 Execution Gateway；
-按 run mode 分叉核心逻辑。

### 17.7 Risk/Governance

职责：审查 Intent、额度、风险和环境门禁。

依赖：

- Domain；
-Catalog facts；
-Portfolio/Risk projections；
-deployment policy。

禁止：

-直接下单；
-读取 Venue raw response；
-负责网络恢复。

### 17.8 Execution

职责：Intent -> Plan -> Durable Command -> Venue Event -> Order State。

依赖：

- Domain/Contracts；
-Catalog/Capability；
-Risk approval；
-Runtime Store ports；
-Execution Gateway ports。

禁止：

-Strategy 规则；
-市场数据订阅；
-跳过 durable store 直接下单；
-直接修改 Portfolio 数量。

### 17.9 Accounting/Ledger

职责：将 Execution/Funding/Settlement 等事实归约为账务。

依赖：

- Domain facts；
-stable calculators/conversion ports；
-durable transaction repository。

禁止：

-调用 Venue；
-推断不存在的 Fill；
-接受未归一化 raw event。

### 17.10 Application

职责：唯一组合根、生命周期、结构化并发和安全状态。

Application 可以依赖所有 Port 并组装实现，但不得重新实现各模块业务规则。

### 17.11 Connectors

职责：实现外部协议到内部 Port/Contract 的转换。

依赖：

- Contracts；
-Domain identity/product facts；
-外部 SDK/transport。

禁止：

-外部对象进入 Strategy/Risk/Ledger；
-Connector 内建立第二套 Order State；
-Connector 内决定全局 readiness；
-Connector 直接提交 Ledger。

## 18. 模块依赖矩阵

`A -> B` 表示 A 可以依赖 B 的公开 Port/Contract。

| 模块 | Domain | Contracts | Catalog | Data | Market Runtime | Strategy | Risk | Execution | Accounting | Application | Connectors | Storage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Domain | 内部 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 |
| Contracts | ID primitive | 内部 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | 否 |
| Catalog | 是 | 可选 | 内部 | 否 | 否 | 否 | 否 | 否 | 否 | 否 | Reference Port | Repository Port |
| Data | 是 | 是 | 是 | 内部 | 否 | 否 | 否 | 否 | 否 | 否 | Provider Port | 是 |
| Market Runtime | 是 | 是 | Port | Capture Port | 内部 | 否 | 否 | 否 | 否 | Supervisor Port | Market Connector Port | 可选 |
| Strategy | 是 | Event View | Port | 否 | Read-only View | 内部 | Read-only View | 否 | Portfolio View | Clock Port | 否 | 否 |
| Risk | 是 | 可选 | Port | 否 | Read-only View | Intent Contract | 内部 | Approval Contract | Portfolio View | Policy | 否 | 否 |
| Execution | 是 | 是 | Port | 否 | 可选价格 View | Intent Contract | Approval | 内部 | Execution Event | Runtime Port | Execution Port | Store Port |
| Accounting | 是 | Event Fact | 可选 | 否 | 否 | Strategy ID | 可选计算 | Execution Fact | 内部 | 否 | 否 | Repository Port |
| Application | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 内部 | 是 | 是 |
| Connectors | 是 | 是 | Listing 输入 | 否 | Port 实现 | 否 | 否 | Port 实现 | 否 | 生命周期 Port | 内部 | 否 |

矩阵中的“可选”必须通过窄 Port，禁止导入具体实现。

## 19. 协作接口与交付约定

每个 Workstream 的公开交付必须包含：

1. Contract/Port；
2. Reference implementation；
3. Fake/Test implementation；
4. Contract tests；
5. Metrics/Fault 行为；
6. Migration guide；
7. Removal criteria；
8. 示例 composition。

模块间协作遵循：

- 消费方先评审 Contract，再由提供方实现；
- Contract 变化必须提供兼容判断；
- 不通过共享可变对象传递所有权；
- 不允许消费方导入提供方 private implementation；
- 事件消费者必须声明 delivery 和 recovery 需求；
-每个 Port 必须有 deterministic fake；
-跨模块错误使用 typed fault，不依赖字符串匹配；
-异步接口必须定义 cancellation、timeout 和 retry owner。

Retry owner 规则：

| 错误位置 | Retry owner |
|---|---|
| Transport reconnect | Connector/Transport |
| Subscription target reconciliation | Market Runtime |
| Command dispatch | Execution Dispatcher |
| UNKNOWN Venue state | Recovery Service，不自动重提 |
| Consumer crash | Task Supervisor |
| Data quality failure | Data Publisher/Quarantine，不自动忽略 |

## 20. 项目阶段与里程碑

### M0：边界冻结

包含：W0。  
退出：ADR、依赖测试和迁移清单完成。

### M1：统一事件骨架

包含：W1、W2、W3 的最小版本。  
退出：Dataset Replay 和 Synthetic Live 使用同一异步 EventSource，Capture 可重放。

### M2：首个异步市场闭环

包含：W4、W5、Massive Connector、W7、W10 基础。
退出：Massive Live -> Capture -> Projection -> Strategy -> Intent 可运行、停止、重启和 Replay。

### M3：异步执行闭环

包含：W8。  
退出：Intent -> Outbox -> Simulated Venue -> Fill -> Ledger，全部 crash matrix 通过。

### M4：全模式统一

包含：W9。  
退出：同一策略通过 Study、Backtest、Historical Sim 和 Paper Trading。

### M5：真实 Venue 与 L4

包含：Binance/IBKR 收敛、Paper/Testnet soak。  
退出：24–72 小时、restart/reconnect/kill-switch/replay 验收通过。

### M6：Rust Spike

包含：W11。  
退出：根据性能、稳定性和调试成本决定是否生产采用。

## 21. 每项工作的完成定义

任何任务只有同时满足以下条件才可标记完成：

- 代码已实现；
-单元测试通过；
-跨模块 Contract Test 通过；
-故障和取消路径已测试；
-metrics/fault evidence 已实现；
-文档和示例已更新；
-不存在未说明的兼容分支；
-旧接口已删除，或有明确 owner、截止里程碑和删除条件；
-`compileall`、全量测试和 `git diff --check` 通过；
-若影响 Runtime，至少完成一次 restart/recovery 测试；
-若影响数据，生成并核验 content hash/quality/lineage；
-若影响执行，完成幂等和 crash-window 验收。

“代码已合并但调用方未迁移”“只实现正常路径”“暂时保留两套正式入口”均不算完成。

## 22. 首轮执行清单

建议下一轮只启动 M0 和 M1，具体顺序如下：

1. 创建 ADR 和依赖边界测试；
2. 盘点两套 Market Event 模型的全部生产者和消费者；
3. 定义 `CanonicalEventEnvelope`；
4. 定义 Quote/Trade/Bar/Book typed payload；
5. 建立 contract vectors 和兼容测试；
6. 定义异步 EventSource/EventSink；
7. 实现有界内存 Channel 和 overflow matrix；
8. 将现有 `ReplayEventFeed` 适配为异步 EventSource；
9. 实现最小 Raw/Canonical Capture Session；
10. 使用一条现有策略验证 Synthetic Live 与 Dataset Replay 的相同 Decision hash；
11. 更新实施进度文档，再开始 Massive Connector 迁移。

首轮明确不包含：

-共享内存；
-Rust sidecar；
-Binance/IBKR 全面迁移；
-Execution Outbox；
-Live 环境变更。

首轮退出标准：

> 一套统一、typed、可 capture、可 replay、具有有界背压语义的异步事件骨架已经成立，并由一条真实策略证明 Live-style EventSource 与历史 Release EventSource 可以互换。
