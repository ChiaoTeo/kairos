# Strategy Application 与多业务事件流重构设计

## 1. 文档目的

本文定义 Kairos Python Strategy runtime 下一阶段的重构目标，集中解决以下问题：

1. 当前只有 Market 增量事件流真正接入 Strategy，Account、Risk 和 Execution 主要只有查询或命令能力；
2. `StrategyHost` 同时承担生命周期、业务 mapping、多流消费、回调、时钟、回测和报告职责，已经成为过大的运行时对象；
3. Strategy 定义的 `EventStream`、`LifecycleJournal`、`BacktestRuntime` 和
   `StrategyRuntimeDependencies` 重复抽象了业务模块或具体运行模式已有的能力；
4. Market transport 依赖 Strategy 私有 `RawEventEnvelope`，依赖方向错误；
5. Account、Risk 和 Execution 的 mapping 虽然位于各自业务目录，但 mapper 选择、事件 kind 判断和 scope
   处理仍由 `StrategyHost` 承担，业务知识没有真正归还业务模块；
6. `StrategyApplications`、`StrategyHost`、`StrategyProcessApplication` 等名称开始产生职责歧义。

本文是对以下文档中 Strategy 事件流和运行时职责部分的进一步收敛：

- `docs/python-strategy-sdk-public-api-design.md`；
- `docs/strategy-composition-and-module-configuration-design.md`；
- `docs/cross-module-state-and-events.md`；
- `docs/time-driven-runtime-and-backtest-design.md`。

当上述文档中仍以 `StrategyHost + StrategyRuntimeDependencies + RawEventEnvelope` 作为目标结构时，本
文关于事件流和 Strategy runtime 内部结构的结论优先。各业务模块的权威状态所有权、Launch/System
的进程所有权以及已有回测时间语义不因本次重构改变。

本文同时记录边界、职责、迁移顺序和最终验收证据。本轮迁移已完成；后续新增事件类型仍必须遵守本文
定义的所有权和“mmap snapshot 永远不存在事件”约束。

## 2. 重构前基线与问题结论

### 2.1 重构前模块接入状态

当前 Strategy 面向各业务模块的能力并非全部缺失，而是“查询/命令面已部分完成，增量事件面没有完成”：

| 模块 | 当前 Strategy 能力 | 增量事件状态 | 目标 |
|---|---|---|---|
| Reference | SQLite current-state 查询 | 不直接进入 Strategy | 暂时保持查询式访问 |
| Market | 订阅、快照、Unix event stream | 已接入 `on_market` | 迁回 Market-owned typed stream |
| Account | `snapshot()` 一次性 mmap 查询 | 未生产接线 | 保留 `snapshot()`，另增 `events()` Aeron 流 |
| Risk | `status()` 一次性 mmap 查询 | 未生产接线 | 保留 `status()`，另增 `events()` Aeron 流 |
| Execution | 命令、order/intent 一次性 mmap 查询 | 未生产接线 | 保留查询，另增 `events()` Aeron 流 |
| Clock | Strategy runtime 内部驱动 | 已有 `on_clock` | 归 Strategy Application 调度 |
| System | 类型和回调存在 | 缺少真实 ingress | 由 Strategy Application 产生运行时事实 |

Strategy protocol 已声明 `on_account`、`on_risk` 和 `on_execution`，`StrategyHost` 也已经包含对应
mapper 和多流队列骨架；但生产 composition 只把 Market stream 传入
`StrategyRuntimeDependencies.market_events`，`application_events` 仍为空。因此这些回调当前不是完整
的生产能力。

### 2.4 最终实施状态（2026-08-14）

本文已经从概念设计进入实现收口阶段。目前完成情况如下：

| 范围 | 最终状态 | 验收结论 |
|---|---|---|
| Strategy facade | 已建立 `StrategyApplication`；进程控制归 Launch 的 `StrategyProcessController` | `start()` 在执行策略前检查已启用的生产 Aeron source readiness；失败进入 `FAILED` |
| callback host | 已提取 `StrategyCallbackHost` | 不打开 transport、不做业务 mapping、不读取 mmap |
| 多流 ingress | `StrategyEventIngress` 合并四个业务 Application 的 typed event | 有界队列、source failure、gap、shutdown 和 Market 无订阅不启动均有测试 |
| Market | Actor → Aeron → decoder → `MarketApplication.events()` | Bar、Quote、Trade、Greeks 已公开；OrderBook 尚无 Strategy 调用者，因此不扩展公开联合 |
| Account | Actor 直接产生 balance/position/equity/status 变化并经 Aeron 推送 | `segment_key` 由 Account 业务事件贯穿 contract 和 Python 类型；不由 Strategy 猜测 |
| Execution | commit 后直接形成 intent/order/fill 事件并经 Aeron 推送 | instance、strategy 和全部 account scope 校验及缺失 identity 负向测试通过 |
| Risk | Actor 产生 decision/reservation/circuit 事件并经 Aeron 推送 | launch/instance scope、sequence 和失败路径测试通过 |
| mmap 边界 | 四模块使用独立 current-view contract/publisher 输入 | schema、静态搜索和边界测试确认不含事件、cursor 或 join 位置 |
| 旧抽象 | 目标代码已删除 `StrategyHost`、`StrategyProcessApplication`、`RawEventEnvelope`、`BacktestRuntime` 等旧路径 | 只允许在本设计的迁移说明或历史文档中出现 |

第 11 节的架构、事件、API 和全仓回归标准已经全部满足。本轮迁移状态为：**完成**。

### 2.2 `StrategyHost` 的职责膨胀

当前 `StrategyHost` 同时负责：

- Strategy lifecycle 状态机；
- Market subscription readiness；
- 一次性 snapshot 查询可用性与事件流生命周期；
- Market 和其他 Application stream 消费；
- transport/runtime envelope 到业务事件的 mapping；
- Account scope 判断；
- 多流 dispatch sequence；
- 用户 callback 调用；
- command 与 callback 串行化；
- Strategy state、clock 和 timer；
- backtest execution 驱动、Account mark-to-market；
- equity curve、fill 和 event trace 收集；
- lifecycle journal、日志和清理。

这些职责至少横跨 Application facade、事件 ingress、用户代码 host、模块适配和 backtest driver 五个
边界。继续向同一个类增加 Account、Risk、Execution event source 会进一步放大错误所有权。

### 2.3 当前依赖方向错误

当前 Market transport 返回 Strategy 私有 `RawEventEnvelope`，Market composition 还通过
`TYPE_CHECKING` 引用 Strategy 的 `EventStream` protocol。其效果是：

```text
Market transport -> Strategy runtime contract
```

正确方向应为：

```text
Market transport -> Market Application -> MarketEvent
Strategy Application -> consume MarketApplication.events()
```

Account、Risk 和 Execution 不得复制当前 Market 的反向依赖。

## 3. 核心设计结论

### 3.1 业务 Application 正式拥有事件用例

每个产生策略可见增量事实的业务模块，都由自己的 Application 提供类型化事件方法：

```python
class MarketApplication:
    async def events(self) -> AsyncIterator[MarketEvent]: ...


class AccountApplication:
    async def events(self) -> AsyncIterator[AccountEvent]: ...


class RiskApplication:
    async def events(self) -> AsyncIterator[RiskEvent]: ...


class ExecutionApplication:
    async def events(self) -> AsyncIterator[ExecutionEvent]: ...
```

Application 同时保留独立的一次性 snapshot/query 方法：

```python
class AccountApplication:
    def snapshot(self) -> AccountsSnapshot: ...
    def account(self, account: AccountId) -> AccountSnapshot: ...
    async def events(self) -> AsyncIterator[AccountEvent]: ...


class RiskApplication:
    def status(self, account: AccountId) -> RiskStatus: ...
    async def events(self) -> AsyncIterator[RiskEvent]: ...


class ExecutionApplication:
    def order(self, order_id: OrderId) -> Order | None: ...
    def intent(self, intent_id: IntentId) -> ExecutionIntent | None: ...
    async def events(self) -> AsyncIterator[ExecutionEvent]: ...
```

这里是两个并列能力，不是一条数据链：

```text
mmap snapshot/query
  -> 调用时读取一次当前 projection
  -> 返回当前状态

Aeron events
  -> 持续接收增量业务事实
  -> 驱动 Strategy callback
```

调用 `snapshot()` 不会创建事件订阅；消费 `events()` 也不等于反复读取 mmap。一次业务状态变更可以让
Actor 同时产生新的状态 projection 和业务 event，但二者是由 Actor 分别产出的两种结果：mmap publisher
只能发布 projection，Aeron publisher 只能发布当次变更直接产生的 event。不得让 Aeron publisher 比较
前后两份 snapshot 来合成事件；消费者也不得假定两种 publication 同时可见。

必须遵守以下硬约束：

> mmap snapshot 永远不存在事件。

mmap snapshot 只是调用时读取的一份只读状态投影。它可以携带 generation、as-of time 等状态投影
metadata，但不得携带或公开 event stream ID、event sequence、event cursor、join point 或 replay
position。不得从 mmap 更新通知、文件变化或两次 snapshot diff 合成业务事件，也不得向策略公开
`AccountSnapshotEvent`、`RiskSnapshotEvent` 或 `ExecutionSnapshotEvent` 这类概念。

这里的“永远不存在”也包括实现和命名层面：不得用 event sequence 生成 snapshot ID，不得为了事件恢复
给 mmap reader 增加 `after_sequence`，也不得把一次 mmap publication 描述成 event。Actor 为自身崩溃
恢复保存的持久化 checkpoint/journal 可以记录内部处理进度，但它不是对外 mmap snapshot，不能通过查询
API 暴露给 Strategy，更不能据此建立 snapshot-to-event join。

这条边界也适用于 publisher 的输入。mmap snapshot publisher 的输入类型不得包含 event、event list、
event sequence 或 event cursor；Aeron event publisher 必须接收 Actor 在处理命令或外部事实时直接产出的
业务 event/outbox record。禁止把当前 Actor state、mmap payload 或连续两份 projection 交给 event
publisher 后通过 diff 推导事件。

为避免 `snapshot` 一词继续混淆三种完全不同的用途，代码和评审统一使用下列术语：

| 概念 | 用途 | 是否允许事件字段或事件位置 |
|---|---|---|
| current view / mmap view | 对外一次性查看当前业务状态 | 永远不允许 |
| business query result | Application 执行业务查询返回的结果 | 不允许借此承载或恢复事件流 |
| internal checkpoint | Actor 崩溃恢复和内部持久化 | 可以记录内部处理进度，但不得进入 mmap 或 Strategy 查询 API |
| Aeron contract event | 持续交付业务变化 | 必须拥有独立 stream identity 和 source sequence |

实现层必须为 mmap 使用专门的 current-view 输入类型。即使内部 checkpoint 类型仍因恢复需要保留
`event_sequence`、watermark 或 journal position，也不能把该类型直接传给 mmap encoder/publisher。
当前模块对应关系为：

- Market：`MarketCurrentView`；
- Account：无事件字段的 `AccountsSnapshot`（后续可仅为命名一致性改为 `AccountCurrentView`）；
- Execution：`ExecutionCurrentView`；
- Risk：`RiskCurrentView`。

策略事件只有一个来源：对应业务模块通过 Aeron 发布的 contract event。事件流的起点、cursor、retention、
gap 和 resync 只能由 Aeron contract 与事件消费者自身解决；任何情况下都不能读取 snapshot 来决定事件
游标或补偿事件缺口。

这些方法是业务 Application use case，不是 Aeron API 的公开转发。Strategy 不获得：

- Aeron publication/subscription；
- Unix socket reader；
- FlatBuffer table；
- transport fragment；
- provider payload；
- 模块内部 event record；
- 模块 snapshot 文件路径。

生产增量业务事件使用模块拥有的 Aeron contract；replay 和测试可以使用对应的具体 source。当前
Market Unix event stream 是迁移实现，不应成为其他模块复制的目标。模块 composition 负责构造具体
source 并交给模块 Application，Application 对 Strategy 只返回稳定业务事件。

### 3.2 不增加通用 `split()`

不把所有 Application 机械设计成：

```python
application, stream = application.split()
```

原因是：

- Python 中没有必须通过 `split()` 表达的 ownership move；
- snapshot query 和 event stream 是两个独立访问能力，但都不能成为第二个业务状态 owner；
- `split()` 无法表达 scope、事件游标和订阅的业务语义；
- 它容易重新产生一个通用 stream abstraction。

默认入口是明确的 `Application.events()`。如果某模块 composition 必须返回复合构造结果，可使用模块
拥有的具名结果，例如 `StrategyMarketAccess(application, event_source)`，但该类型属于模块
composition，不能成为跨模块统一框架。

### 3.3 单模块正确性归业务 Application

每个业务 Application 的事件入口负责：

1. 解码本模块 contract；
2. 校验 schema、discriminator 和 stream identity；
3. 维护本模块 source sequence；
4. 发现重复、乱序和 gap；
5. 执行 launch、instance、account、strategy 等本模块需要的 scope 过滤；
6. 将 contract record 映射为模块公开事件；
7. 暴露明确的 reconnect、backpressure 和终止行为；
8. 在发生 gap 时按 Aeron event contract 的 retention/resync 规则恢复，或明确失败。

snapshot 始终由独立查询方法一次读取，既不参与正常事件消费，也不参与异常恢复。mmap projection 由
业务 Actor 发布，Python 事件消费者不负责在 callback 前修改它。

`StrategyApplication` 不调用模块 mapper，也不判断模块内部事件 kind。

### 3.4 跨模块交付归 Strategy Application

Strategy Application 只负责业务事件已经成立后的跨模块问题：

1. 并发启动已启用模块的 `events()`；
2. 将多个独立有序流合并为单个 Strategy callback 序列；
3. live 模式记录实际观察顺序；
4. replay/backtest 模式使用明确的同时间点领域优先级；
5. 分配 instance-local `dispatch_sequence`；
6. 保留模块 source stream ID、source sequence、event time 和 causation；
7. 使用有界队列和明确 backpressure 策略；
8. 串行调用用户 Strategy；
9. 将 stream failure、degraded、resync 和 shutdown 转换为 Strategy system fact。

它不成为全局 EventBus、不维护业务模块权威状态，也不把多个模块 sequence 伪装成一个全局业务
sequence。

## 4. 目标结构

```text
Launch/System
  -> StrategyProcessController
      -> StrategyApplication
          -> ReferenceApplication
          -> MarketApplication.events()       -> MarketEvent
          -> AccountApplication.snapshot()    -> AccountSnapshot  # one-shot mmap read
          -> AccountApplication._events()     -> AccountEvent     # runtime-only Aeron changes
          -> RiskApplication.status()         -> RiskStatus       # one-shot mmap read
          -> RiskApplication.events()         -> RiskEvent        # Aeron changes
          -> ExecutionApplication queries     -> current views    # one-shot mmap read
          -> ExecutionApplication.events()    -> ExecutionEvent   # Aeron changes
          -> StrategyEventIngress
          -> StrategyCallbackHost
          -> StrategyLifecycleJournal
          -> StrategyBacktestDriver?          # backtest only
```

### 4.1 `StrategyApplication`

`StrategyApplication` 是进程内公开 use-case facade，负责：

- `start()`；
- `enable()`；
- `pause()`；
- `resume()`；
- `refresh()`；
- `stop()` / `close()`；
- `command()`；
- `status()`；
- Strategy lifecycle 和 readiness；
- 启停 `StrategyEventIngress`；
- callback、clock、journal 和 cleanup 的协调；
- instance-local dispatch sequence；
- 对外提供稳定的运行结果和状态。

不建议最终命名为 `StrategyHostApplication`。`Host` 和 `Application` 同时出现在名称中会继续混淆
“用户代码托管”与“公开用例 facade”。迁移期可以先提供短期别名，但目标名称是
`StrategyApplication` 或在确有歧义时使用 `StrategyRuntimeApplication`。

### 4.2 `StrategyEventIngress`

这是 Strategy 私有 service，负责：

- 启动具体的 Market、Account、Risk、Execution Application event iterator；
- 每个来源一个 pump；
- 有界 ingress queue；
- live/replay 调度规则；
- dispatch sequence；
- 将已类型化事件交给 `StrategyApplication`。

它不定义通用业务 `EventStream` protocol，也不接收 `RawEventEnvelope`。

一个可接受的内部形态是使用明确的来源函数：

```python
async def pump_market(self) -> None:
    async for event in self.market.events():
        await self._queue.put(MarketDispatch(event))


async def pump_account(self) -> None:
    async for event in self.account._events():
        await self._queue.put(AccountDispatch(event))
```

这里的 dispatch wrapper 只用于 Strategy 内部标识 callback domain，不复制业务 payload。

### 4.3 `StrategyCallbackHost`

这是从当前 `StrategyHost` 缩减后的私有 service，只负责：

- 托管一个用户 Strategy 实例；
- 持有 `StrategyContext`；
- 调用 `on_start/on_market/on_account/on_risk/on_execution/on_clock/on_system/on_end`；
- command 与 callback 串行化；
- Strategy state；
- callback 返回值和异常约束。

它不负责启动业务数据流、mapping、gap recovery、Account scope、backtest settlement 或进程控制。

### 4.4 `StrategyLifecycleJournal`

保留一个具体、Strategy-owned 的私有 persistence service：

```python
class StrategyLifecycleJournal:
    def append(self, record: LifecycleRecord) -> None: ...
```

生产环境当前只有 JSONL 实现，因此不保留 `LifecycleJournal(Protocol)`。测试使用临时文件或具体
in-memory journal helper；测试 fake 本身不足以证明需要生产抽象。

如果未来出现第二个真实生产实现，再根据实际差异提取最小协议。

### 4.5 `StrategyBacktestDriver`

当前 `BacktestRuntime` 将 clock、Execution simulation 和 Account mark-to-market 混合在一起，并使用
`RawEventEnvelope -> object` 接口。该 protocol 删除。

回测模式如果仍需要跨 Account 和 Execution 的具体协调对象，可以保留一个有真实调用者的具体类：

```python
class StrategyBacktestDriver:
    def advance_time(self, occurred_at: datetime) -> None: ...
    def observe_market(self, event: MarketEvent) -> tuple[ExecutionEvent, ...]: ...
    def mark_account(self, event: MarketEvent) -> tuple[AccountEvent, ...]: ...
```

最终方法名和返回类型应以 Account/Execution Application 的真实用例为准，不能照搬上述示意。该类：

- 只在 backtest composition 中创建；
- 不成为 live/paper 的 optional-everywhere 通用 runtime；
- 不拥有 Account、Execution 或 Market 权威状态；
- 不绕过各模块 Application 修改状态；
- 保留当前“完成 bar 不能立即按同一 close 成交”等时间语义。

如果具体业务 Application 已能直接表达所有回测用例，则连该类也不需要，Strategy Application 可以
直接编排具体 Application 方法。

## 5. 事件类型与 metadata

### 5.1 删除 Strategy `RawEventEnvelope`

`RawEventEnvelope` 是 transport/runtime 中间类型，不应成为跨模块公共输入。目标是从 Strategy domain
删除它。

各模块可以在自己的 contract 或 services 中保留私有 wire envelope，但必须在进入 Strategy 之前映射成：

- `MarketEvent`；
- `AccountEvent`；
- `RiskEvent`；
- `ExecutionEvent`。

### 5.2 分离 source metadata 与 dispatch metadata

当前业务 mapper 接收 `dispatch_sequence`，导致模块 mapping 依赖 Strategy 的最终调度顺序。模块在产生
业务事件时不可能知道该事件最终是 Strategy 收到的第几个事件。

模块事件 metadata 只包含 source facts：

```python
@dataclass(frozen=True, slots=True)
class EventMetadata:
    stream_id: str
    sequence: int
    schema_version: int
    producer: str
    occurred_at: datetime | None
    occurred_at_unix_nanos: int | None
    causation_id: str | None
```

Strategy ingress 在交付时增加自己的 metadata：

```python
@dataclass(frozen=True, slots=True)
class StrategyDispatch[TEvent]:
    event: TEvent
    dispatch_sequence: int
```

也可以由 Strategy context 单独绑定 `dispatch_sequence`，但不得修改或伪装模块 source sequence。

### 5.3 业务 mapping 所有权

以下知识必须从 Strategy runtime 删除：

- Market 的 bar/quote/trade/order-book/greeks discriminator；
- Account 的 balance/position/equity/status 等事件 discriminator；
- Risk 的 reservation/status/circuit discriminator；
- Execution 的 intent/order/fill discriminator；
- canonical decimal 和 ID 转换；
- Account scope fallback；
- 模块 contract schema/version 兼容。

每个业务模块提供一个模块拥有的统一 mapping 入口，例如：

```python
def map_account_event(record: AccountEventRecord) -> AccountEvent: ...
def map_risk_event(record: RiskEventRecord) -> RiskEvent: ...
def map_execution_event(record: ExecutionEventRecord) -> ExecutionEvent: ...
```

Strategy 不再逐个导入 `map_account_snapshot`、`map_balance`、`map_execution_fill` 等细分 mapper。

## 6. 删除的抽象与命名调整

### 6.1 删除 Strategy `EventStream(Protocol)`

删除：

```python
class EventStream(Protocol):
    stream_id: str
    def can_join(self, event_sequence: int) -> bool: ...
    def events(self, after_sequence: int = 0) -> AsyncIterator[RawEventEnvelope]: ...
```

原因：

- 它由 Strategy 定义，却要求所有业务模块实现；
- 返回 Strategy 私有 transport envelope；
- `can_join()` 暴露了不需要的通用 stream/join 模型；
- 不存在需要该统一抽象的第二个真实 Strategy use case。

### 6.2 删除 `LifecycleJournal(Protocol)`

以具体 `StrategyLifecycleJournal.append(LifecycleRecord)` 取代
`append(record: object)`。不要把文件操作直接堆进 `StrategyApplication`，删除的是不必要的 protocol，
不是合理的私有 persistence service。

### 6.3 删除 `BacktestRuntime(Protocol)`

不再使用：

```python
advance_time(int)
apply_market(RawEventEnvelope) -> object
mark_account(RawEventEnvelope) -> object
```

使用具体业务 Application API，或一个具体的 backtest-only driver。

### 6.4 删除 `StrategyRuntimeDependencies`

当前字段的归属调整为：

| 原字段 | 目标归属 |
|---|---|
| `market_events` | `MarketApplication.events()` |
| `application_events` | 各业务 `Application.events()` |
| `history_root` | 当前无真实 StrategyHost 使用者，删除；需要时回到数据 owner |
| `state_path` | `StrategyState` 的明确构造参数 |
| `backtest` | 具体 `StrategyBacktestDriver | None` 或具体业务 Application |

### 6.5 删除或重命名 `StrategyApplications`

`StrategyApplication` 与 `StrategyApplications` 同时存在会造成明显歧义。

首选方案是删除 `services/applications.py`，由 `StrategyContext` 或 `StrategyApplication` 明确接收五个
固定业务 Application：

```python
StrategyApplication(
    reference=reference,
    market=market,
    account=account,
    risk=risk,
    execution=execution,
)
```

五个参数表达稳定、真实的业务依赖，不需要动态 registry。如果迁移期间必须保留容器，则重命名为
`StrategyContextApplications`，并在迁移完成后重新评估是否仍有价值。

禁止增加：

- `ApplicationRegistry`；
- `ApplicationManager`；
- `ApplicationFactory[T]`；
- 按字符串查找模块的容器；
- 所有字段都是 optional 的通用 bundle。

### 6.6 调整 `StrategyProcessApplication`

当前 `StrategyProcessApplication` 负责启动 Python 子进程、等待 Unix health socket 和停止进程。它不是
进程内 Strategy business application。

目标名称建议为：

- `StrategyProcessController`，或
- `StrategyProcessClient`。

它归 Launch/System 的进程控制边界。避免最终出现：

```text
StrategyProcessApplication
StrategyApplication
StrategyApplications
```

三个近似名称表达完全不同的职责。

## 7. 各模块的事件流要求

### 7.1 Market

Market 是第一条迁移切片：

- 把 `UnixMarketEventStream` 和 wire decoder 收归 Market；
- 取消 Market transport 对 Strategy `RawEventEnvelope` 的依赖；
- `MarketApplication.events()` 返回 `MarketEvent`；
- Market 内部完成 Aeron event sequence、gap 和 event-native resync；
- 保持 owner-scoped subscription lease 和 stop cleanup；
- 保持 live 与 replay 的明确终止差异。

### 7.2 Account

Account 必须通过 Aeron 主动发布策略需要观察的增量业务事实，不能要求 Strategy 轮询 mmap snapshot
并自行比较状态。第一批事件至少包括：

- `BalanceChangedEvent`：总额、可用额或预留额变化；
- `PositionChangedEvent`：持仓数量、成本或账户侧估值事实变化；
- `EquityChangedEvent`：账户权益变化；
- `AccountStatusChangedEvent`：账户 readiness、stale/freshness 或交易可用状态变化。

Account-owned order observation、fill settlement 或 intent facts 是否直接成为 Strategy `AccountEvent`
variant，应由第一个真实策略调用者决定；Execution order/fill 生命周期仍由 Execution 事件流负责，不能
在 Account 中复制权威订单状态。

`AccountApplication.snapshot()` 保持一次性读取本 Strategy 已启用账户的 mmap current view；
`AccountApplication.account(account)` 用于读取一个逻辑账户及其全部 segment。它们只返回调用时可见的
账户投影，适合显式状态查询，不负责事件启动、事件恢复或驱动 callback。Strategy runtime 内部的
`AccountApplication._events()` 独立消费 Aeron 增量流，不能通过定时读取 snapshot、读取 snapshot
metadata 或做 diff 来伪造/恢复事件；用户策略只通过 `on_account()` 接收 typed callback。

Account 完成事件流前必须同时具备：

- Rust contract event schema；
- Actor 到 Aeron 的真实跨进程 publisher；
- Python decoder；
- `account.events:<account_id>` 稳定 logical stream identity（多个账户可以共享物理 Aeron publication，
  但不能伪装成共享一个 sequence）；
- event source sequence；
- event-native gap/resync；
- launch instance 和绑定 account scope filter；
- typed balance/position/equity/status change events；
- 每个 change 显式携带 `segment_key`，不能只靠 `account_id` 推断 segment；
- mmap snapshot 与 Aeron event 完全独立的边界测试。

不得在 snapshot API 中暴露 `account.events`、event sequence 或 cursor 来暗示 snapshot 与事件流存在
join 关系。只有 Actor 实际向 Aeron publication 发送 contract event，并且 Python Application 可以
独立消费，才算接入完成。目标 `AccountEvent` 联合中不存在 `AccountSnapshotEvent`；完整账户状态只能
通过 `snapshot()` 或 `account(account)` 查询。

### 7.3 Execution

Execution 事件流至少交付：

- `IntentUpdateEvent`；
- `OrderUpdateEvent`；
- `FillEvent`。

它必须保留 intent/order/fill identity、Strategy identity、account scope、source sequence、event time、
causation 和 delivery/reconciliation 语义。查询 `/v1/intent-events` 或读取 mmap snapshot 不能替代实时
`execution.events` contract。

### 7.4 Risk

Risk 事件流只交付对 Strategy 有真实意义且可正确过滤的事实，例如：

- authorization rejection；
- reservation 变化；
- circuit 状态；
- 与当前 Strategy/account scope 相关的 risk status 变化。

已有 event encoder 而未接入 server publisher，不算完成事件流。Risk 的预检查 projection 也不能替代
Execution 权威命令路径上的 Risk authorization/reservation。

### 7.5 Reference

本阶段不增加 `on_reference`。Reference current-state 查询继续由 `ReferenceApplication` 提供，Market
继续消费 `reference.events` 更新自己的 Reference projection。

只有动态 universe 或 Reference lifecycle 变化出现第一个真实 Strategy 调用者后，才评估 Strategy
可见 Reference event。不能为了接口对称增加空回调。

## 8. 生命周期、恢复与错误语义

### 8.1 Startup readiness

`StrategyApplication.start()` 至少确认：

- 用户 Strategy 加载成功；
- Strategy `on_start` 成功；
- Market subscription 请求已达到可接受状态；
- 每个已启用 Aeron 事件流可以从配置的 source cursor 开始消费；
- scope 配置有效。

具体模块 readiness 由模块 Application 给出，Strategy 不读取其 mmap header 或 transport 状态。

### 8.2 Gap 与 resync

模块 source gap 由模块 Application 按 Aeron event contract 处理。可恢复流必须通过事件日志 retention、
持久化 cursor、重新订阅或专门的 event resync contract 重建连续性；不可恢复流必须明确失败，不能用
mmap snapshot 跳过缺口。Strategy Application 只观察结果：

- 恢复成功：继续接收 typed event，并可产生 system resync notice；
- 暂时不可恢复：进入 degraded/waiting 状态；
- 不可恢复：Strategy lifecycle 失败或按明确策略暂停。

禁止任何层使用 `_recover_snapshot()` 恢复事件流，也禁止正常事件循环通过轮询 mmap snapshot 模拟
增量事件。

### 8.3 Scope

Account 和 Risk 的 scope 过滤必须在业务 Application 事件入口完成。Execution 还必须按当前
Strategy/instance/account ownership 过滤订单和 intent 生命周期。

Strategy callback host 不通过“只有一个 account 时自动补 account ID”修复不完整 wire event。缺失必要
identity 属于 contract/adapter 错误。

### 8.4 Backpressure

每个模块定义自身 transport/source 的 backpressure 和重连语义；Strategy ingress 定义跨模块合流队列
的容量和满载行为。两者不能用一个全局默认策略代替。

Strategy command 执行期间的 callback queue 也必须有界。溢出应产生明确故障或暂停，不静默丢弃
Account、Risk 或 Execution 生命周期事实。

## 9. 回测与实时模式

### 9.1 共享契约

Backtest、paper 和 live 使用相同的：

- Strategy public protocol；
- 业务 Application request/result/event 类型；
- Strategy callback host；
- Strategy state；
- dispatch metadata 语义。

### 9.2 不共享的实现

不同模式可以使用不同的具体 source 和 driver：

- live：provider/Aeron/Unix incremental stream；
- paper：真实 Market + simulated Execution/Account；
- backtest：finite replay source + deterministic clock + simulator。

不为形式统一创建所有模式都实现的 `BacktestRuntime` 或通用 provider stream protocol。

### 9.3 确定性交付

live 模式记录实际进入 ingress 的顺序。backtest/replay 必须定义同一业务时间点的稳定优先级，并保留
以下现有约束：

- timer 可在 Market 数据间隙触发；
- 同时间点 timer 与 Market event 的顺序明确；
- 完成 bar 不能无意中使用同一个 close 立即成交；
- Execution fill、Account settlement 和 Risk reservation 的因果关系不能只靠时间戳猜测；
- 最终 Account mark 和回测报告不得停留在最后一次 fill 之前。

## 10. 推荐迁移计划

### Phase 0：冻结行为和调用者

1. 记录当前 `StrategyHost` 所有公开调用者；
2. 为 lifecycle、Market callbacks、timer、command serialization、backtest report 建立行为测试；
3. 记录当前 live/replay 顺序和错误语义；
4. 不在本阶段增加 Account/Risk/Execution 假事件流。

退出标准：重构过程中可判断行为是否变化。

### Phase 1：建立 `StrategyApplication`

1. 将公开 lifecycle facade 收敛为 `StrategyApplication`；
2. composition 返回 `.application`，不再把 `.host` 作为进程主对象；
3. REST control server 只调用 `StrategyApplication`；
4. 保持 Market-only 行为不变；
5. 将 `StrategyHostStatus` 重命名为 `StrategyApplicationStatus` 或稳定的
   `StrategyStatus`。

退出标准：外部进程和回测行为不变，公开 lifecycle 只有一个 facade。

### Phase 2：提取 callback host 与 ingress

1. 提取 `StrategyCallbackHost`；
2. 提取 `StrategyEventIngress`；
3. ingress 先只消费 Market；
4. command 和 callback 仍严格串行；
5. 删除原 `StrategyHost` 或保留一个发布窗口的内部别名。

退出标准：callback host 不打开 stream、不执行 mapping、不读取 snapshot。

### Phase 3：Market Application-owned stream

1. 将 Market wire envelope、decoder、sequence 和 event-native gap recovery 移回 Market；
2. 增加 `MarketApplication.events()`；
3. Strategy ingress 直接消费 `MarketEvent`；
4. 分离 source sequence 与 dispatch sequence；
5. 删除 Strategy `EventStream` 和 `RawEventEnvelope`。

退出标准：Market 代码不依赖 Strategy runtime；Strategy 不导入 Market mapper。

### Phase 4：删除通用 runtime 抽象

1. 具体化 lifecycle journal；
2. 删除 `LifecycleJournal(Protocol)`；
3. 删除 `BacktestRuntime(Protocol)`；
4. 使用具体业务 Application 或具体 `StrategyBacktestDriver`；
5. 删除 `StrategyRuntimeDependencies`；
6. 删除未使用的 `history_root`。

退出标准：Strategy runtime 不再接受 `object` 类型的 backtest 结果。

### Phase 5：Account vertical slice

完成 Account Actor → Aeron publisher、contract、Python decoder、Application events、scope、
event-native gap/resync、ingress 和 `on_account` 端到端链路。保持 `snapshot()`/`account(account)` 为完全独立的
一次性 mmap 查询，并删除公共 `AccountSnapshotEvent` 以及 snapshot 中的 event cursor/join 语义。

退出标准：余额、持仓、权益和账户状态变化由 Aeron 主动推送；策略能可靠观察绑定账户的变化，不能
观察其他账户；正常 callback 不依赖 mmap 轮询。

### Phase 6：Execution vertical slice

完成 intent/order/fill lifecycle stream，并验证从 Strategy command receipt 到最终 fill 的可观察闭环。

退出标准：策略不轮询 mmap 或控制 API，也能完整观察自己的 Execution 生命周期。

### Phase 7：Risk vertical slice

完成与当前 Strategy/account scope 相关的 decision/reservation/circuit 事件。

退出标准：Risk 主链路仍由 Execution 权威调用，Strategy 事件仅用于观察和响应。

### Phase 8：命名和兼容路径清理

1. 删除或重命名 `StrategyApplications`；
2. 将 `StrategyProcessApplication` 收敛为 `StrategyProcessController/Client`；
3. 删除 `StrategyHost` 兼容别名；
4. 更新 README、examples、tests 和架构文档；
5. 删除旧 mapper 调用和空回调兼容路径。

退出标准：没有两个同义 Strategy lifecycle facade，没有通用 application registry。

## 11. 验收标准

### 11.1 架构验收

- Strategy infrastructure 不再定义其他业务模块必须实现的 `EventStream`；
- Market、Account、Risk、Execution 不导入 Strategy runtime/domain 私有类型；
- Strategy Application 不导入业务 mapper 或 FlatBuffer generated 类型；
- Strategy callback host 不知道业务 event kind；
- 各模块 Application 是 Strategy 的唯一跨模块入口；
- 每份 mutable business state 仍只有对应 Actor 一个 owner；
- composition 选择具体 transport，Application 不创建 provider SDK connection；
- 不增加通用 EventBus、registry、manager 或 provider adapter。

### 11.2 事件验收

- 每条模块流保留独立 source stream ID 和 sequence；
- Strategy 分配独立 dispatch sequence；
- duplicate、out-of-order 和 gap 都有测试；
- gap 只能通过 event-native retention/resync 恢复，或明确失败；
- snapshot 查询不会读取、改变或重建 event cursor；
- Account/Risk/Execution scope 过滤有负向测试；
- live arrival order 可记录；
- replay 同时间点顺序可复现；
- queue overflow 和 source failure 不静默丢事件。

### 11.3 API 验收

- 用户 Strategy 只接收 `MarketEvent/AccountEvent/RiskEvent/ExecutionEvent`；
- 用户代码看不到 `RawEventEnvelope`；
- mmap snapshot 永远只通过查询 API 返回，不进入任何 `*Event` 联合，也不参与事件 join/resync；
- Application event API 不返回 `object`；
- lifecycle journal 只接受 `LifecycleRecord`；
- backtest API 使用业务事件和业务结果；
- Reference 保持明确查询语义，不为对称增加无调用者的 `on_reference`。

### 11.4 回归验证

每个迁移阶段运行聚焦测试，然后运行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

同时执行静态搜索：

```text
rg -n "RawEventEnvelope|StrategyRuntimeDependencies|class EventStream" kairospy tests
rg -n "map_(market|account|risk|execution)" kairospy/application/strategy
rg -n "application\.strategy" kairospy/application/{market,account,risk,execution}
rg -n "StrategyApplications|StrategyHost|StrategyProcessApplication" kairospy tests docs
```

迁移完成后，前三项搜索在 Strategy 跨模块边界不应再有命中；最后一项只允许出现在明确的迁移记录或
历史文档中。

## 12. 非目标

本次重构不做以下事项：

- 不改变 Account、Execution、Risk、Market、Reference 的业务所有权；
- 不创建全局 EventBus；
- 不创建通用 application registry；
- 不让 Strategy 直接持有 Aeron；
- 不统一所有模块的构造函数签名；
- 不为测试 fake 提前设计生产 protocol；
- 不增加没有真实调用者的 Reference Strategy event；
- 不顺带重排整个 Python package；
- 不改变 live safety、Risk authorization 或 Execution delivery certainty；
- 后续新增事件类型仍按单一业务切片迁移，不为接口对称一次扩展所有模块。

## 13. 最终结论

本次重构的目标不是简单把 `StrategyHost` 改名，而是建立三个清晰 owner：

```text
业务 Application
  单模块 contract、mapping、scope、sequence、gap、resync 和 typed event

StrategyEventIngress
  多模块合流、backpressure、live/replay 顺序和 dispatch sequence

StrategyApplication
  Strategy lifecycle、command、clock、callback 协调、journal 和 cleanup
```

用户代码托管进一步由私有 `StrategyCallbackHost` 承担。具体回测协调只在 backtest 模式存在。

最终应删除 Strategy 自建的 `EventStream`、`LifecycleJournal`、`BacktestRuntime`、
`StrategyRuntimeDependencies` 和 `RawEventEnvelope` 跨模块边界，让 Strategy Application 直接调用各
业务模块公开的具体 Application API。这样既保留现有模块所有权，也为 Account、Execution 和 Risk
事件流提供一个可逐条验证、不会继续膨胀 `StrategyHost` 的接入路径。

## 14. 最终验证记录（2026-08-14）

已完成：

- Python 完整测试集：344 passed，8 skipped（其中 342 项一次运行通过；两个使用固定 `/tmp` process
  runtime 的测试在清理孤儿测试进程后分别通过）；
- `uv run pyright`：0 errors（3 个既有动态 `__all__` warning）；
- `uv run ruff check kairospy tests` 与 `uv run ruff format --check kairospy tests` 通过；
- Strategy ingress、四模块 Python event flow、stream identity、scope、duplicate/stale、gap、live late join
  和 Aeron bridge 异常退出测试通过；
- Account：library、process、integration、architecture 测试通过；
- Execution：54 项 library 测试、33 项 integration 测试、2 项 architecture 测试通过；
- Market：42 项 library 测试及 actor、architecture、orderbook、replay integration 测试通过，1 项真实
  Binance 网络测试按约定 ignored；
- Risk：library、19 项 integration 测试、2 项 architecture 测试通过，包含 launch/instance identity 保真；
- 四模块所有 target 的 `cargo check` 通过；四个 event bridge 都通过真实 embedded Aeron Media Driver
  的跨进程 readiness 测试；
- `cargo fmt --all -- --check` 与 `git diff --check` 通过；
- `cargo test --workspace` 已在全新 `CARGO_TARGET_DIR` 中完整通过；
- 静态搜索确认 Strategy 不再包含 `RawEventEnvelope`、`StrategyRuntimeDependencies`、
  `EventStream(Protocol)`、`StrategyHost`、`StrategyProcessApplication` 或 `BacktestRuntime`；
- 静态搜索确认 Strategy 不导入 Market、Account、Risk、Execution mapper；
- mmap schema 与四模块 current-view contract/publisher 输入均不含事件集合、event stream identity、event
  sequence 或 event cursor；
- Account、Execution、Risk event record 已校验 launch/instance/account 等业务 scope；共享 Market 流不伪造
  instance scope；
- live Aeron source 允许首次观察时从最新可见记录建立纯事件流基线，随后严格检查连续性；duplicate/stale
  被忽略，gap 与 bridge 意外结束明确失败，整个过程不读取 mmap snapshot。
- `StrategyApplication.start()` 对已启用的 Account、Risk、Execution 以及有订阅需求的 Market source
  执行实际 bridge `--check`；启动失败产生 Strategy-owned system fact 并进入 `FAILED`，shutdown 同样产生
  明确 system fact；
- Account Actor 的聚焦测试一次覆盖 balance、position、equity、status 四类必要变化；Account-owned
  `segment_key` 已进入业务事件、FlatBuffers/Aeron contract、decoder 和公开事件数据；
- Market Greeks 已有真实公开订阅调用者并进入 `MarketEvent`；OrderBook 没有 Strategy 调用者，按
  anti-overdesign 规则保持在模块内部能力，不为形式完整性扩展公开 API。

此前 `kairos-execution-cli` test binary 的启动挂起已定位为现有 Cargo target 目录包含约 65,535 个文件后
触发的本机动态加载环境问题：同一 binary 移出该目录可立即运行，使用全新 target 目录后全 workspace
测试完整通过。因此它不再是产品代码或本次迁移的验收阻塞项。

生产 Aeron contract 当前明确采用 non-retained 语义：首次 live observation 从最新可见事件建立基线，
之后 duplicate/stale 被忽略，任何 gap、bridge 退出或 source failure 都明确失败并由 Strategy 转换为
system fact。当前没有 event-log retention/resync 实现，因此不宣称自动重连或恢复；更不会以 mmap
snapshot 跳过缺口。若未来增加 retained event log，应作为独立业务切片设计和验证。

### 14.1 验收结论映射

| 第 11 节标准 | 实现与证据 |
|---|---|
| 架构边界 | Strategy 只组合四个业务 Application；无通用 `EventStream`、业务 mapper、FlatBuffer 或 Aeron 对象泄漏 |
| 事件连续性 | 四模块分别维护 source cursor；duplicate/stale、gap、late join、scope 和异常退出均有正反测试 |
| mmap 独立性 | current-view schema/publisher 输入无 event 字段；readiness、正常消费和 gap 失败路径都不读取 mmap |
| backpressure 与失败 | ingress 使用有界 `asyncio.Queue`；source failure 与 shutdown 转为 system fact，不静默丢失 |
| 业务事件覆盖 | Market typed data、Account 四类变化、Execution intent/order/fill、Risk decision/reservation/circuit 均端到端接入 |
| 全仓回归 | Rust workspace、Python tests、Rust/Python format、Ruff、Pyright 与 `git diff --check` 全部通过 |

因此，Phase 0–8 均已达到退出标准，本文所定义的 Strategy Application 与多业务事件流迁移已经完成。
