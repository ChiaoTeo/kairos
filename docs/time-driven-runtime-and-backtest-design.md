# 时间驱动运行时与回测设计

## 1. 背景

当前项目已经具备 Market Replay、Strategy、Execution Simulator、Account
Settlement 和 Risk 等回测基础能力，但主要链路仍然是行情事件驱动的。

Strategy 协议已经预留了 `on_clock` 生命周期，不过当前运行时还没有稳定地产生
策略可见的 ClockEvent。Execution、Account、Risk 和 Market 也没有完全收敛到同一
套业务时间语义中。

如果只给 Strategy 增加定时器，会出现以下问题：

```text
Strategy 认为当前时间是 10:00
Execution 仍然认为订单刚刚提交
Account 仍然停留在 09:00
Risk 使用机器真实时间判断限额
```

因此需要建立统一的业务时间轴，而不是给每个模块各自增加一套互不关联的 Timer。

## 2. 结论

所有业务模块需要共享同一个可复现的业务时间，但不需要接收完全相同的定时器事件。

目标模型是：

```text
                         ┌──> Market
                         ├──> Strategy
Replay / Live Clock ─────┼──> Execution
                         ├──> Account
                         └──> Risk
```

核心原则：

1. 业务逻辑使用统一的 `event_time`。
2. 回测使用虚拟时间，不能使用机器当前时间决定业务结果。
3. Strategy、Execution、Account、Risk 的时间规则由各自模块拥有。
4. 时间推进和事件顺序由运行时统一控制。
5. 回测、Paper 和 Live 使用一致的业务事件语义，只替换时钟和具体实现。

本设计的第一个可交付目标是先实现两类策略的完整回测：

1. Quote 驱动策略：通过实时价、买一卖一报价和报价数量产生交易决策。
2. 小时 Bar 驱动策略：通过 `1h` 周期的 OHLCV 行情产生交易决策。

两类策略必须走同一条回测主链路：

```text
Market Replay
  -> Strategy on_data
  -> Intent
  -> Risk
  -> Execution Simulator
  -> Fill
  -> Account Settlement
  -> Mark-to-Market
  -> Report
```

差异只存在于 Market observation 的类型和 Strategy 的信号逻辑，不应为 Quote
和 Bar 各自建立一套独立的回测引擎。

在实现策略之前，优先完成两个可复现的历史数据集：

1. Massive：使用 SPY 数据作为股票市场回测样本。
2. Binance：使用 BTCUSDT Spot 数据作为加密现货回测样本。

这两个数据集是第一阶段的固定数据入口，不先扩展更多 Provider、品种或数据类型。

## 3. 时间模型

必须区分三种时间：

| 时间 | 作用 | 是否决定回测结果 |
|---|---|---:|
| 业务时间 / Event Time | 行情、订单、成交、结算、风控窗口 | 是 |
| 处理时间 / Processing Time | 进程实际收到和处理事件的时间 | 否 |
| 墙上时间 / Wall Clock | 日志、进程生命周期、真实网络超时 | 否 |

回测业务逻辑不得依赖：

```text
SystemTime::now()
datetime.now()
asyncio.sleep()
```

这些 API 只能用于日志、进程监控和真实网络 I/O 超时。

## 4. 首批历史数据目标

### 4.1 Massive SPY

Massive SPY 用于验证股票市场数据和小时 Bar 策略。第一阶段建议下载并保存：

```text
provider: massive
symbol: SPY
asset type: equity
observation: 1h Bar
```

如果 Provider 的原始粒度不是 1 小时，先在数据边界完成确定性的 Bar 聚合，再将
聚合后的结果作为 Market-owned `Bar` 保存。聚合规则必须固定：

```text
open   = 区间第一条价格
high   = 区间最高价
low    = 区间最低价
close  = 区间最后一条价格
volume = 区间成交量之和
```

需要记录：

- Provider symbol 与 Kairos InstrumentId 的映射。
- 交易所/市场时区。
- Bar 的起止边界和时间戳语义。
- 是否包含盘前、盘后数据。
- Corporate action 或 split/adjustment 状态。
- API 请求参数和下载时间。

SPY 数据优先用于：

```text
HourlyBarStrategy -> 1h Bar -> target position -> simulated execution
```

### 4.2 Binance BTCUSDT Spot

Binance BTCUSDT 用于验证加密现货行情、连续交易时段和 Quote 驱动策略。第一阶段
建议准备：

```text
provider: binance
market: spot
symbol: BTCUSDT
observation: Quote 或可规范化为 Quote 的历史盘口数据
```

如果 Binance 首批历史源只提供 Kline/Bar，则先支持 BTCUSDT 的 `1h` Bar 回测，
并将 Quote 策略安排在具备 bid/ask 历史数据之后。不能把只有 close 的 Kline
伪装成真实 Quote；如果需要从 Bar 生成模拟 Quote，必须明确标注为 synthetic quote，
并在报告中记录生成规则。

BTCUSDT 数据优先用于：

```text
QuoteStrategy -> bid/ask Quote -> execution simulator
```

如果首批数据无法提供真实 bid/ask，允许先使用以下降级路径验证主链路：

```text
BTCUSDT 1h Bar -> synthetic bid/ask -> QuoteStrategy
```

但该结果只能作为撮合流程测试，不能当作真实盘口回测结果。

### 4.3 数据集规范

两个数据集都必须经过同一套 Market normalization，输出 Market-owned observation，
而不是让 Strategy 直接读取 Massive 或 Binance 原始 payload。

推荐存储：

```text
原始 Provider 响应       -> 可选，作为下载审计资料
规范化 JSONL             -> 小型 fixture 和调试
规范化 Parquet           -> 正式回测数据集
dataset manifest         -> 数据集身份和完整性校验
```

Manifest 至少记录：

```json
{
  "dataset_id": "massive:SPY:1h:2024-01-01:2024-03-31",
  "provider": "massive",
  "symbol": "SPY",
  "instrument_id": "instrument:massive:equity:SPY:common",
  "observation_type": "bar",
  "timeframe": "1h",
  "start_time": "...",
  "end_time": "...",
  "event_count": 1234,
  "storage_format": "parquet",
  "source_parameters_hash": "..."
}
```

回测启动前必须验证：

1. Manifest 的 event count 与文件实际记录数一致。
2. 事件时间单调或可被确定性排序。
3. `instrument_id`、`source_id` 和 observation type 完整。
4. 数据集时间范围覆盖 launch 配置的回测窗口。
5. 数据类型与策略订阅匹配。

推荐的数据目录：

```text
data/
  datasets/
    massive/
      spy-1h.parquet
      spy-1h.manifest.json
    binance/
      btcusdt-spot-1h.parquet
      btcusdt-spot-1h.manifest.json
```

数据下载入口已经由 Market CLI 提供。首批数据应按以下方式生成，时间参数使用
Unix milliseconds：

```bash
# Massive SPY 1h Bar
uv run kairospy market data download \
  --provider massive \
  --symbol SPY \
  --start <start-ms> \
  --end <end-ms> \
  --interval 1h \
  --api-key "$MASSIVE_API_KEY" \
  --name massive-spy-1h \
  --file data/raw/massive-spy-1h.jsonl \
  --storage-format parquet

# Binance BTCUSDT Spot 1h Bar
uv run kairospy market data download \
  --provider binance \
  --symbol BTCUSDT \
  --start <start-ms> \
  --end <end-ms> \
  --interval 1h \
  --name binance-btcusdt-spot-1h \
  --file data/raw/binance-btcusdt-spot-1h.jsonl \
  --storage-format parquet
```

这两个命令生成的是规范化 Bar 数据。它们可以直接供小时 Bar 策略使用；Quote
策略需要真实 bid/ask 数据，或显式配置 Bar-to-synthetic-Quote 的测试模式，不能
把普通 OHLC close 数据默认为真实 Quote。

当 Binance 历史接口只提供 Kline/Bar 时，可以显式生成合成 Quote 数据集：

```python
from pathlib import Path

from kairospy.application.market import MarketDataApplication

market = MarketDataApplication(Path(".kairos/state/market"))
market.derive_synthetic_quotes(
    "binance-btcusdt-spot-1h",
    "binance-btcusdt-synthetic-quotes",
    spread_bps="1",
)
```

该数据集的每条 Quote 都带有 `derivation = "synthetic_quote"`。它用于验证
QuoteStrategy 的事件、订单和账户链路，不代表真实历史盘口，正式结果必须在报告中
区分 `provider` 和 `synthetic_quote` 数据。

## 5. 统一时间协议

建议在 Rust/Python 共享协议中统一以下概念：

```rust
pub struct EventContext {
    pub event_time: UnixNanos,
    pub sequence: Sequence,
}
```

时间推进事件：

```rust
pub struct TimeAdvance {
    pub from: UnixNanos,
    pub to: UnixNanos,
    pub sequence: Sequence,
}
```

定时器事件：

```rust
pub struct ClockEvent {
    pub timer_id: String,
    pub scheduled_at: UnixNanos,
    pub event_time: UnixNanos,
    pub sequence: Sequence,
}
```

所有会改变业务状态的命令和事件都应携带 `EventContext`，包括：

- Market observation。
- Strategy intent。
- Risk authorization。
- Execution order lifecycle。
- Execution fill。
- Account settlement。
- Account mark-to-market。
- Reservation 创建、消费、释放和过期。

建议优先复用已有的 `UnixNanos`、`Sequence` 和事件身份类型，不创建新的通用时间
原语。

可能涉及的协议位置：

```text
kairos-domain-types
kairos-protocol
market contract
execution contract
account contract
risk contract
kairospy.strategy
```

## 6. Clock Driver

回测需要一个唯一的时间推进者。建议将其实现为回测应用编排能力，而不是新的通用
业务 Manager。

现有的 `BacktestApplication` 可以逐步扩展为这一边界；具体模块的状态仍由各自的
Actor/Application 拥有。

Clock Driver 维护一个按时间排序的事件队列：

```text
(event_time, phase_priority, sequence, event)
```

候选事件包括：

- 下一条 Market Replay observation。
- Strategy timer。
- Execution order timeout 或模拟延迟完成。
- Account settlement 或 funding 事件。
- Risk reservation expiry 或窗口维护。

基本算法：

```text
while queue is not empty:
    item = queue.pop_min()
    clock.advance_to(item.event_time)
    broadcast TimeAdvance
    process item
    drain all causal events at the same business time
```

在回测中，Clock Driver 必须是唯一的业务时间推进来源。

### 6.1 两类首批回测策略

#### Quote 驱动策略

Quote 策略订阅包含以下信息的报价事件：

- `bid_price`
- `ask_price`
- 可选的 `bid_quantity`
- 可选的 `ask_quantity`
- `instrument_id`
- `observed_at_unix_nanos`
- `source_id`

策略可以基于 spread、mid price、报价数量或盘口变化生成 Intent。例如：

```text
spread <= threshold -> 允许做市或建立仓位
mid price 突破阈值 -> 产生目标仓位
报价数量不足 -> 不下单或减少数量
```

Quote 回测必须验证：

1. 买单使用 ask 侧成交价。
2. 卖单使用 bid 侧成交价。
3. 报价数量约束能限制成交量。
4. limit price、手续费和滑点被正确应用。
5. Quote 的 event time 被传递到订单、成交和 Account。

#### 小时 Bar 驱动策略

小时 Bar 策略订阅 `1h` 周期的 Bar 事件：

- `open`
- `high`
- `low`
- `close`
- `volume`
- `timeframe = "1h"`
- `observed_at_unix_nanos`

策略可以基于收盘价、均线、突破或成交量产生 Intent。例如：

```text
每根 1h Bar 完成后计算信号
close > moving_average -> 建立目标仓位
close < moving_average -> 平仓
```

小时 Bar 回测必须明确：

1. 策略在 Bar 完成后才能使用该 Bar 的 close。
2. 使用当前 Bar 生成的订单默认不能回到该 Bar 的历史区间内成交。
3. 订单最早在下一条可执行行情上成交，避免 look-ahead bias。
4. 如果只有 Bar 数据，成交价模型必须明确使用 open、close 或模拟滑点价格。
5. Bar 的时间戳、周期和数据完整性必须写入回测报告。

首个版本建议采用以下简单规则：

```text
1h Bar 在 event_time 到达时视为完成
Strategy 在 on_data 中读取该 Bar
Strategy 产生的订单从下一条 MarketEvent 开始撮合
```

如果使用小时 Bar 直接生成订单并用同一根 Bar 的 close 成交，必须显式标记为
`close-on-close` 模式，并在报告中记录，不能作为默认行为。

## 7. 事件排序与因果关系

同一业务时间点的处理顺序必须稳定。建议第一版采用：

```text
1. ClockEvent
2. MarketEvent
3. Strategy decision / Intent
4. Risk authorization
5. Execution state transition
6. Fill
7. Account settlement
8. Mark-to-market
9. Snapshot and report
```

例如：

```text
10:00:00  ClockEvent(rebalance)
10:00:00  Strategy emits target position
10:00:00  Risk authorizes
10:00:00  Execution accepts order
10:00:00  MarketEvent arrives
10:00:00  Execution generates fill
10:00:00  Account settles fill
10:00:00  Account marks position
```

具体是 ClockEvent 在 MarketEvent 前还是后，需要作为明确的回测配置或版本化规则；
第一版建议固定为 ClockEvent 优先，并通过测试锁定。

## 8. Strategy 时间 API

Strategy 公共 SDK 已经有 `on_clock`，需要补齐时间能力：

```python
class StrategyBase:
    def on_clock(self, context, event):
        pass
```

Context API 第一版只需要：

```python
context.clock.now()
context.clock.every("rebalance", "1h")
context.clock.at("session-open", timestamp)
context.clock.cancel("rebalance")
```

不建议让用户注册任意后台 Python callback。统一进入 `on_clock` 可以保持生命周期、
异常处理、日志和回测复现的一致性。

定时器内部状态可以是：

```python
TimerRegistration(
    timer_id,
    next_due_at,
    interval,
    policy,       # skip / catch_up
    generation,
)
```

周期定时器必须按计划时间推进，避免漂移：

```text
正确：next_due = previous_scheduled_at + interval
错误：next_due = actual_fire_time + interval
```

## 9. 各模块职责

### 8.1 Market

Market Replay 负责产生带 `event_time` 的行情事件。现有
`MarketReplayClock::Maximum` 和 `EventTime` 继续保留，但它们只代表回放速度：

```text
Maximum    尽快执行
EventTime  按历史时间间隔等待
```

还需要明确：

```text
Replay business time = 当前正在处理的历史 event_time
```

Market freshness 判断在回测中应使用业务时间，而不是机器当前时间。

### 8.2 Strategy

Strategy 接收两类主要事件：

```text
ClockEvent  -> on_clock
MarketEvent -> on_data
```

Strategy 只能通过 `context.clock.now()` 读取当前业务时间。

### 8.3 Execution

Execution 的订单生命周期时间全部使用业务时间：

```text
submitted_at
accepted_at
filled_at
expired_at
canceled_at
```

模拟延迟也使用业务时间。例如订单在 10:00:00 提交，模拟延迟 100ms，则成交时间为：

```text
10:00:00.100
```

Execution 的时间规则包括：

- 订单超时。
- IOC/FOK 过期。
- 模拟网络/撮合延迟。
- 部分成交窗口。
- 撤单有效时间。

Execution 不一定需要接收所有 Strategy timer，但必须处理 Clock Driver 发出的时间推进，
以便执行自身到期规则。

### 8.4 Account

Account 使用业务时间处理：

- Fill settlement。
- Mark-to-market。
- Equity curve。
- Funding。
- 借贷利息。
- 结算周期。
- Account freshness。

现有的 `occurred_at_unix_nanos` 和 `observed_at_unix_nanos` 应逐步统一到同一套
`EventContext.event_time` 语义中。

### 8.5 Risk

Risk 使用业务时间处理：

- Reservation expiry。
- 订单频率窗口。
- 日损失窗口。
- Circuit breaker 冷却期。
- 行情 freshness。
- 风控预算的周期重置。

例如 reservation 过期应判断：

```text
expires_at <= current_business_time
```

不能判断机器当前时间。

## 10. 跨进程同步

当前模块是多进程架构，仅广播事件不足以保证确定性。回测模式需要明确的
request/ack 或 barrier。

基本模式：

```text
BacktestDriver
  -> TimeAdvance(10:00) to Market
  -> ack
  -> TimeAdvance(10:00) to Risk
  -> ack
  -> TimeAdvance(10:00) to Execution
  -> ack
  -> TimeAdvance(10:00) to Account
  -> ack
  -> process next event
```

事件也需要确认其因果处理完成：

```text
MarketEvent -> Market ack
Intent      -> Risk ack
Order       -> Execution ack
Fill        -> Account ack
```

当前回测运行时已经落实显式时间边界：StrategyHost 在每次收到 MarketEvent 或推进
timer 前，按固定顺序将同一个 `event_time_unix_nanos` 发送到 Account、Risk 和
Execution 的 `POST /v1/time/advance`；Risk 在 backtest 模式关闭墙上时间的定期
expiry，只在这个边界上执行 reservation expiry。Execution 的模拟器保存最近一次
replay event time，订单提交和 intent expiry 使用该业务时间；Account 保留自己的
单调业务时间水位，同时由 fill/mark-to-market 的 observation event time 更新账户
事实。这样四个模块虽然不接收相同的 timer 事件，但在同一因果时间点上处理各自职责。

没有因果来源时间的人工或实时命令，才允许在模块边界回退到 processing time；该回退
不能出现在 replay 的策略 intent、订单提交、成交、账户结算或 Risk reservation 中。

第一版可以牺牲回测吞吐，使用串行 barrier 换取确定性。后续在有性能数据后，再考虑
批量推进或并行化。

## 11. Live、Paper 与 Backtest

三种模式共享业务事件模型，但使用不同的 Clock 实现：

| 模式 | 时间来源 | 业务结果是否依赖墙上时间 |
|---|---|---:|
| Backtest | 历史数据和虚拟 Clock | 否 |
| Paper | 真实时间 + 模拟执行 | 订单逻辑不应依赖，调度依赖 |
| Live | 真实时间 + Provider 事件 | 网络超时可依赖 |

建议抽象成：

```text
BusinessClock
  ├── ReplayClock
  ├── RealTimeClock
  └── TestClock
```

`BusinessClock` 暴露当前业务时间和时间推进能力；具体实现由 Composition 选择。

## 12. 分阶段落地方案

### 阶段一：统一时间字段

目标：不改变整体调度架构，先统一语义。

- 所有行情事件拥有 `event_time`。
- 订单命令拥有 `submitted_at`。
- 成交拥有 `occurred_at`。
- Account/Risk 状态变化拥有业务时间。
- 业务代码禁止使用真实系统时间。
- 为时间字段增加边界测试。

阶段一的业务验收固定为两个策略：

```text
QuoteStrategy
  -> 输入 Quote
  -> 产生一次开仓和一次平仓
  -> 验证 bid/ask、数量、手续费和滑点

HourlyBarStrategy
  -> 输入 1h Bar
  -> 根据 Bar close 产生一次开仓和一次平仓
  -> 验证下一事件成交和无 look-ahead bias
```

两种策略都必须使用同一个模拟账户、同一个 Execution Simulator、同一个 Risk
边界和同一种报告格式。

### 阶段二：启用 `on_clock`

- StrategyContext 增加 Clock capability。
- 支持 `every`、`at`、`cancel`。
- Replay 模式产生 ClockEvent。
- StrategyHost 分发 `on_clock`。
- 日志记录 ClockEvent 的 `event_time` 和 `sequence`。

Python StrategyHost 已支持 `context.clock.now()`、`every`、`at`、`cancel` 和
确定性 timer queue；ClockEvent 会进入回测报告。跨进程 Account、Risk、Execution
time-advance barrier 也已接入。回放模式会先取得有限的 replay 事件，再由 Driver
按业务时间处理行情和 timer，因此没有任何 MarketEvent 的时间空档也会逐个触发
到期的 ClockEvent。

最小实现可以在收到下一条 MarketEvent 时触发所有已到期 timer：

```text
收到 MarketEvent at T
  -> 触发所有 due_at <= T 的 timer
  -> 处理 MarketEvent
```

这是最小改动方案；当前完整 replay driver 已在有限回放源上取代该方案，因此空档
timer 不会被合并为只在最后时间点触发。

### 阶段三：完整 Backtest Driver（已落地）

当前实现支持“没有行情时定时器也能触发”的完整回放队列：

- Clock Driver 拥有虚拟时间。
- Market、Strategy、Execution、Account、Risk 参与时间推进。
- 所有模块按 barrier 确认。
- 同一时间点的事件按固定 phase 排序。
- 回测结果包含完整事件轨迹。

完整 Driver 首先服务于上述两类策略。Quote 和 `1h` Bar 已在同一事件调度模型下
运行；Trade、OrderBook、Tick Bar 和多周期数据仍属于后续扩展。

### 阶段四：生产级时间规则

后续增加：

- 交易日历。
- 市场开盘、收盘和休市事件。
- 时区和 session。
- funding/interest 周期。
- 订单 timeout 和 provider latency。
- timer missed policy。
- 多市场时间轴。

## 13. 验收标准

第一阶段完整验收至少包括：

```text
相同数据和配置重复运行，结果完全一致
ClockEvent 有稳定 event_time 和 sequence
没有行情事件时，显式 timer 仍能触发
Strategy 能在指定时间执行再平衡
Execution 订单过期使用业务时间
Account 结算和盯市使用业务时间
Risk reservation 使用业务时间过期
报告包含 ClockEvent、订单、成交和账户状态顺序
Strategy 无法通过公共 API 读取真实系统时间
```

针对首批两个策略，还必须满足：

```text
QuoteStrategy 可以从 Quote 事件产生并完成订单生命周期
QuoteStrategy 的买卖成交分别基于 ask/bid
QuoteStrategy 的报价数量限制和滑点可测试
HourlyBarStrategy 只能读取已完成的 1h Bar
HourlyBarStrategy 不使用当前 Bar close 回溯成交
HourlyBarStrategy 的订单从下一条可执行事件开始撮合
两种策略都能生成 fills、equity curve、PnL 和最终账户状态
两种策略的报告包含数据类型、周期、时间范围和配置摘要
相同数据和配置重复运行，两种策略结果都完全一致
```

建议增加的核心测试：

1. 定时器按计划时间触发，不发生漂移。
2. 同一时间点 ClockEvent 与 MarketEvent 顺序稳定。
3. 无行情区间仍能触发 timer。
4. 订单延迟和过期时间可复现。
5. reservation 在虚拟时间到期时释放。
6. Account settlement 与 mark-to-market 顺序稳定。
7. Backtest、Paper、Live 使用相同 Strategy lifecycle。

## 14. 最终目标

最终回测循环应具备如下语义：

```text
while clock is not finished:
    event = driver.next_event()
    clock.advance_to(event.time)
    components.advance_time(clock)

    match event:
        ClockEvent:
            strategy.on_clock(event)
        MarketEvent:
            strategy.on_data(event)
        Intent:
            risk.authorize(event)
            execution.submit(event)
        Fill:
            execution.apply(event)
            account.settle(event)
        Settlement:
            account.mark_to_market(event)
```

核心目标不是让每个模块拥有一套相同的 Timer，而是：

```text
一个统一、可复现的业务时间轴
多个模块各自拥有的时间规则
一个确定性的事件排序和推进机制
```
