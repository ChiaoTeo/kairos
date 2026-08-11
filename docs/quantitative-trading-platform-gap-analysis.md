# Kairos 当前量化交易平台能力缺口分析

## 1. 结论

当前项目已经具备交易系统内核的主要业务边界，但还不是成熟的量化交易平台。

最缺的不是继续增加交易所连接器，而是把以下链路形成可重复、可审计、可恢复的完整闭环：

```text
研究数据 -> Alpha/Strategy -> Portfolio Construction -> Risk
  -> Execution -> Account/Settlement -> Performance
  -> Monitoring/Operations
```

当前项目更准确的定位是：一个已经完成核心领域边界设计、具备 Market、Reference、Account、Risk、Execution 和 Strategy 运行时骨架的交易系统基础设施项目。距离成熟平台还缺少研究生产化、组合管理、完整回测、生产级对账、会计结算、数据治理和团队协作流程。

## 2. 当前已经具备的能力

| 模块 | 当前职责 | 当前判断 |
|---|---|---|
| Reference | 资产、市场、合约、交易场所和生命周期目录 | 基础能力较完整，数据治理仍需加强 |
| Market | Quote、Trade、Bar、Greeks、OrderBook、订阅、replay | 骨架已形成，实时可执行性契约不完整 |
| Account | 余额、持仓、权益、账户刷新、对账基础 | 边界明确，账本和结算不完整 |
| Risk | 预算、授权、reservation、consume/release | 有 reservation 基础，不是完整风控系统 |
| Execution | Intent、订单、成交、撤单、改单、审计、持久化 | 生命周期已具备，恢复和多腿执行不足 |
| Strategy | Python 生命周期、行情订阅、target position | 运行时存在，研究治理不足 |
| Workspace/System | 配置、启动、进程管理、日志、状态、CLI | 运行时基础较完整，生产运维不足 |
| Contract/Transport | FlatBuffers、快照、事件序列、mmap/Unix transport | 边界清晰，部分 contract 未闭环 |

当前 ownership 设计是合理的：Strategy 产生意图；Execution 拥有订单生命周期；Account 拥有余额、持仓和权益；Risk 拥有预算和 reservation；Market 拥有行情、订单簿、订阅和 freshness；Reference 拥有市场目录；Integration 拥有外部连接；Composition/System 负责组装和运行。

参考文档：

- [Execution 目标架构](./execution-target-architecture-and-plan.md)
- [Market 交付定义](./market-module-delivery.md)
- [Reference Runtime 架构](./reference-runtime-architecture.md)
- [跨模块状态和事件](./cross-module-state-and-events.md)

## 3. 成熟项目对照

### 3.1 NautilusTrader

NautilusTrader 将研究、确定性回测、模拟交易和实盘交易放在尽量一致的事件驱动架构中，并将 data engine、message bus、cache、execution engine、portfolio 和 reconciliation 视为核心能力。

- [NautilusTrader GitHub](https://github.com/nautechsystems/nautilus_trader)
- [Architecture Overview](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/concepts/overview.md)

对 Kairos 的启示：Backtest、Paper、Live 应尽量共享执行语义；Portfolio、Cache、事件总线和状态恢复不能只是外围能力。

### 3.2 QuantConnect Lean

Lean 的 Algorithm Framework 明确拆分：

```text
Universe Selection -> Alpha -> Portfolio Construction -> Execution -> Risk Management
```

参考：[Lean Algorithm Framework](https://github.com/QuantConnect/Lean/blob/master/Algorithm/Framework/QCAlgorithm.Framework.cs)

对 Kairos 的启示：Strategy 不能同时承担 alpha、组合构建和执行决策；`target_position` 需要经过组合聚合、资金分配和风险调整。

### 3.3 Hummingbot

Hummingbot 的核心价值集中在多交易所 Connector、标准化交易所接口、订单跟踪和策略执行。

参考：[Hummingbot GitHub](https://github.com/hummingbot/hummingbot)

对 Kairos 的启示：Connector 不只是 REST/WebSocket 封装，还要提供订单、账户、成交和状态同步；订单跟踪、幂等和异常恢复是连接器的核心质量指标。

### 3.4 公开论坛和团队讨论

公开讨论中，成熟团队通常通过标准化的策略、订单、数据和结果接口连接研究系统与生产系统，流程通常是：

```text
研究假设 -> 数据准备 -> 回测 -> 前向测试 -> Paper
  -> 小资金 Canary -> 正式上线 -> 监控和复盘
```

参考：[Quant 团队回测系统架构讨论](https://www.reddit.com/r/quant/comments/1txzez0/what_is_the_overall_architecture_of_our_backtesting_system/)

论坛内容属于经验性资料，但共同说明：策略代码能运行，不等于策略已经可以上线。

## 4. P0：必须优先补齐的业务组件

### 4.1 真正的一体化回测引擎

这是当前最大的缺口。当前 `BacktestApplication` 主要接收外部 equity curve 和 fills，然后计算收益、回撤、Sharpe 等指标，并不是由历史行情驱动策略、撮合和账户结算的一体化回测引擎。

证据：[backtest.rs](../crates/business/execution/service/src/application/backtest.rs) 的模块注释明确说明，API 接受 normalized equity/fill facts，Market replay 和 runtime composition 在其外部。

应补齐：

- 历史数据读取、数据集版本和 replay。
- Quote、Trade、Bar、OrderBook 的事件驱动回放。
- Risk admission 和订单撮合。
- 部分成交、排队、延迟、滑点。
- 手续费、资金费率、借贷利息。
- 订单过期、撤单、拒单。
- 余额、权益、保证金和结算。
- 多策略、多账户、多市场回测。
- 确定性 seed、交易轨迹和可复现报告。

目标链路：

```text
Historical Data -> Market Replay -> Strategy -> Intent -> Risk
  -> Execution Simulator -> Fill -> Account Settlement -> Report
```

### 4.2 Portfolio Construction / Position Target Engine

当前策略可以调用 `target_position`，但没有完整的组合构建层。

应补齐：

- 多策略信号合并和账户级 netting。
- 目标仓位聚合、仓位权重和资金分配。
- 单标的、行业、币种、交易所和策略暴露约束。
- 现金、保证金和可用资金处理。
- 组合再平衡。
- 目标仓位到 Execution Plan 的转换。

推荐链路：

```text
Alpha/Signal -> Portfolio Target -> Risk-adjusted Target -> Execution Plan
```

第一阶段不需要复杂 optimizer，但至少需要 TargetPosition、组合聚合和基本约束。

### 4.3 Execution 的生产级恢复和对账

Execution 已有订单状态机，但还需要升级为生产级 OMS/EMS。

应补齐：

- 下单超时后的 `Unknown` 状态。
- 交易所订单查询和本地订单关联。
- 重复提交保护和统一 `client_order_id`。
- `remote_order_id`、`fill_id`、`execution_event_id` 幂等去重。
- 断线重连后的状态恢复。
- 本地与交易所状态冲突处理。
- 部分成交后的 reservation resize。
- 多腿订单的依赖、补偿和失败策略。
- OCO、OTO、reduce-only、post-only、IOC/FOK 等语义。
- TWAP、VWAP、拆单和多交易所路由。

当前 [Execution 目标文档](./execution-target-architecture-and-plan.md) 已将 `ReconciliationRequired`、`Compensating`、多腿 Intent 和 Execution Plan 列为后续能力。

### 4.4 Risk 不能只有 reservation

当前 Risk 已有预算、授权和 reservation，但不是完整风控系统。

应补齐：

- 杠杆、保证金和可用余额检查。
- 单笔、单策略、单账户限额。
- 单标的、单交易所、Gross/Net exposure。
- 价格偏离和行情过期检查。
- 订单频率限制。
- 最大日亏损、最大回撤、连续亏损熔断。
- 交易所异常熔断和全局 kill switch。
- 组合级 VaR、stress test、scenario test。
- Post-trade 风险检查。

当前 Risk 主要集中在 `authorize_and_reserve`、`consume`、`release` 和 reservation 状态管理，尚未覆盖完整的交易前、交易中和交易后风险流程。参考：[Risk Application](../crates/business/risk/service/src/application/service.rs)。

### 4.5 Account Accounting / Ledger / Settlement

Account 已具备余额、持仓、权益和 paper settlement 基础，但距离成熟账户系统仍有明显距离。

应补齐：

- Append-only accounting ledger，最好具备复式账本语义。
- Realized/unrealized PnL。
- 成本基础、FIFO/LIFO/平均成本。
- 手续费、资金费率、借贷利息。
- 初始保证金和维持保证金。
- 多币种估值和汇率。
- 期货交割和期权结算。
- 资金划转、充值、提现和内部转账。
- 账户日终快照和对账报告。
- 交易、成交、资金流水之间的可追溯关联。

成熟账户系统不应只保存当前余额和持仓，而应通过不可变事实和账本重建状态。

## 5. P1：当前明显不完整的组件

### 5.1 Market Data Quality 和可执行性判断

仓库已有 [Market 交付文档](./market-module-delivery.md) 明确列出：

- `Rate` 已在 schema 中存在，但没有进入完整 domain。
- OrderBook 缺少 `source_id` 和 checksum。
- freshness 只有 feed 总状态，缺少逐市场、逐数据类型 freshness。
- typed contract readers 不完整。
- current、orderbook、subscriptions、freshness、history 边界仍需固定。

Execution 不仅要知道“Binance WebSocket 已连接”，还需要知道：

```text
BTCUSDT 的 quote 是否新鲜？
OrderBook 是否连续？
数据源是否可靠？
是否发生 sequence gap？
当前数据是否来自预期 source？
```

建议增加：

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

### 5.2 数据平台和研究数据治理

当前项目已有 Parquet、Arrow、DuckDB、replay 等方向，但还缺少：

- 数据集注册表、版本和 hash。
- 数据来源 lineage。
- 数据质量检查、缺失值和异常值检测。
- 时间戳标准化和交易日历。
- 复权、corporate action 和 survivorship bias 处理。
- Point-in-time 数据。
- 训练集、验证集、测试集隔离。
- 研究结果与数据版本绑定。
- 历史数据增量更新和多源合并。

没有这些能力，回测结果难以复现，也难以通过团队研究审核。

### 5.3 Strategy / Alpha / Research 生命周期

当前 Strategy 主要是运行时协议，包含 `on_start`、`on_data`、`on_intent`、`on_clock` 等回调。参考：[Strategy Protocol](../kairospy/strategy/protocol.py)。

成熟量化团队还需要：

- Strategy Registry。
- Alpha、参数、配置、模型和特征版本管理。
- 研究实验记录和回测结果存档。
- 策略 owner、审批人和状态迁移。
- Paper、Canary、Live promotion gate。
- 多策略冲突和资金分配。
- 策略 kill、pause、resume。
- 线上版本与研究版本追踪。

### 5.4 运营、告警和人工干预系统

当前已有 CLI、日志和 system health，但还不够支持长期生产运行。

应补齐：

- Feed lag、drop、reconnect、backlog 指标。
- Order latency、reject rate、fill ratio。
- PnL 和风险暴露指标。
- 告警规则、告警状态和通知渠道。
- Kill switch、手工撤单、手工平仓和账户冻结。
- Reconciliation dashboard。
- 事故审计、值班和升级机制。
- Prometheus/OpenTelemetry 等监控导出。

系统健康不应只回答“进程是否存活”，还应回答“当前是否可以安全交易”。

## 6. P2：平台成熟后再补的能力

- Universe Selection。
- 因子库和指标库。
- Portfolio optimizer。
- 风险模型和协方差矩阵。
- 多资产统一估值。
- 期权定价和 Greeks 计算。
- 组合级 stress testing。
- 交易成本分析 TCA。
- 实盘与回测偏差分析。
- 订单路由优化。
- 多区域部署和高可用。
- 权限、审批和密钥轮换。
- Web 管理台。
- Notebook/研究环境集成。
- 多人协作的 experiment tracking。

## 7. 量化团队交互模式

### 7.1 角色划分

| 角色 | 主要产出 | 与系统的接口 |
|---|---|---|
| Quant Researcher | 假设、因子、模型、回测结果 | 数据集、研究 API、Backtest |
| Strategy Developer | 策略实现和参数 | Strategy Runtime、Intent |
| Portfolio/Risk | 资金分配、暴露限制、风控政策 | Portfolio、Risk API |
| Execution Engineer | 订单路由、执行算法、交易所适配 | Execution、Integration |
| Platform Engineer | 运行时、存储、消息、部署 | Workspace、Transport、Persistence |
| Trading/Ops | 监控、告警、人工干预、事故处理 | System、Operations、Reconciliation |

### 7.2 推荐协作流程

```text
Researcher 提交研究假设和数据版本
  -> Strategy Developer 形成可运行 Strategy
  -> Backtest 生成版本化结果和交易轨迹
  -> Risk/Portfolio 审核风险、暴露和资金需求
  -> Paper/Canary 验证实时行为和执行偏差
  -> Ops 审核告警、恢复和 kill switch
  -> Live 发布，并持续进行 PnL、风险和执行复盘
```

当前项目已有 Strategy、Launch、Risk、Execution 和 System 基础，但缺少把这些角色和阶段连接起来的 Registry、Experiment、Approval、Promotion 和 Audit 业务对象。

## 8. 当前代码基线问题

除了业务组件缺口，当前仓库还有一个直接阻塞开发基线的问题：`cargo test --workspace` 当前无法编译。

错误原因是 [risk contract model.rs](../crates/business/risk/contract/src/model.rs) 对 `Metric::as_str` 重复定义了三次：第 23、36、49 行。

这属于基础质量问题，不是新的业务组件，但必须先修复，否则无法稳定验证后续模块。

建议恢复以下检查：

```bash
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

## 9. 推荐实施顺序

### 阶段一：先形成可交易闭环

优先完成一个 Binance Spot 端到端闭环：

```text
Market Replay -> Strategy -> Intent -> Risk
  -> Execution Simulator -> Fill -> Account Settlement -> Report
```

必须覆盖手续费、滑点、部分成交、订单拒绝、账户结算和可复现运行。

### 阶段二：补生产安全边界

- Execution reconciliation。
- Account ledger。
- Risk pre-trade/post-trade checks。
- Market per-symbol freshness。
- Kill switch。
- 订单幂等。
- 断线恢复。
- 交易审计。

### 阶段三：补组合和团队协作

- Portfolio Construction。
- 多策略资金分配。
- Strategy Registry。
- Experiment Tracking。
- 回测结果版本化。
- Paper/Canary/Live 发布流程。
- 审批和策略 owner。

### 阶段四：扩展研究和资产类别

- 因子研究。
- Universe Selection。
- 期权和期货结算。
- 多币种估值。
- Portfolio optimizer。
- 风险模型和 TCA。

## 10. 最终判断

当前系统的模块划分已经接近成熟系统，但业务闭环完成度仍处于“交易运行时骨架到早期交易平台”之间。

最应该优先完成的不是继续堆叠 Manager 或 Provider，而是：

1. 一体化回测和模拟执行闭环。
2. Portfolio Construction 和多策略资金分配。
3. 生产级 Execution reconciliation。
4. Account ledger 和完整 settlement。
5. 覆盖交易前、交易中、交易后的 Risk。
6. Per-market freshness 和可执行性判断。
7. 数据集、策略、实验和发布生命周期治理。
8. 监控、告警、人工干预和 kill switch。

完成以上能力后，Kairos 才会从“架构合理的交易系统内核”逐步成为“可供量化团队长期协作和生产运行的平台”。

## 11. 逐项技术实施方案

本章节将前文列出的每一个缺失项转换为可执行的工程方案。所有新增能力都应优先进入对应业务模块的 application API，再由 composition 选择具体实现；不要让 Strategy、CLI 或 transport 直接依赖其他模块的 services。

### 11.1 一体化回测和模拟执行

#### 目标

让 Backtest 使用真实的 Market replay 驱动 Strategy、Risk、Execution Simulator 和 Account Settlement，而不是只计算外部传入的 fills。

#### 修改范围

```text
crates/business/market/service/
  application/runtime.rs       # replay runtime 控制
  services/replay.rs            # 数据源、游标、checkpoint
  domain/observations.rs        # 统一事件时间和 source identity

crates/business/execution/service/
  application/backtest.rs      # BacktestApplication 改为运行引擎入口
  services/simulator.rs         # 新增撮合和订单模拟
  services/latency.rs           # 新增延迟模型
  domain/fill.rs                # 新增或拆分成交事实

crates/business/account/service/
  services/settlement.rs       # 完整模拟结算
  domain/ledger.rs              # 新增模拟账本

kairospy/application/launch/
  application/configuration.py # 回测数据、撮合、费用和 seed 配置
```

#### 核心模型

新增：

```text
BacktestRun {
  run_id
  dataset_id
  dataset_version
  start_time
  end_time
  seed
  strategy_version
  execution_model
  fee_model
  slippage_model
}

SimulationEvent = MarketEvent | StrategyEvent | OrderEvent | FillEvent | ClockEvent
```

`BacktestRun` 必须保存配置快照、输入数据版本、策略版本和随机种子。所有输出都关联 `run_id`。

#### 执行链路

```text
ReplayFeed
  -> MarketActor
  -> StrategyHost
  -> ExecutionIntent
  -> RiskApplication
  -> SimulatedOrderEntry
  -> FillModel
  -> Account Settlement
  -> Backtest Report
```

#### 验收标准

- 相同数据、配置、策略版本和 seed 能得到相同订单及结果。
- 回测、paper 和 live 使用同一套 Intent、Order、Fill 领域模型。
- 能模拟手续费、滑点、部分成交、撤单、拒单和订单过期。
- 能从 run snapshot 恢复到指定事件序列。
- 报告可以追溯到每一个订单、成交和输入行情。

### 11.2 Portfolio Construction 和 Target Position Engine

#### 目标

将多个 Strategy 的 alpha 或 target position 合并为账户级目标，并经过风险调整后生成 Execution Plan。

#### 修改范围

新增 Portfolio 业务模块：

```text
crates/business/portfolio/contract/
crates/business/portfolio/service/src/
  bin/
  composition/
  application/
  services/
  domain/
```

如果第一阶段不希望新增业务 crate，可以先在 `Execution` application 中增加最小的 target aggregation，但长期应独立成 Portfolio 模块。Portfolio 不拥有订单，只拥有目标仓位和分配结果。

#### 核心模型

```text
StrategyTarget {
  strategy_id
  account_id
  instrument_id
  target_quantity
  priority
  valid_until
}

PortfolioTarget {
  account_id
  instrument_id
  target_quantity
  current_quantity
  delta_quantity
  source_strategies
  portfolio_version
}

AllocationPolicy {
  strategy_id
  account_id
  capital_limit
  weight_limit
  priority
}
```

#### API

```text
submit_strategy_target
aggregate_targets
apply_constraints
get_portfolio_targets
rebalance
```

#### 运行链路

```text
Strategy target_position
  -> PortfolioApplication.submit_strategy_target
  -> PortfolioActor aggregate
  -> RiskApplication.apply_constraints
  -> ExecutionApplication.submit_intent
```

#### 验收标准

- 两个策略对同一标的的目标能够确定性合并。
- 组合约束违反时不会直接产生订单。
- 目标仓位与当前 Account position 的差额能够生成可执行 Intent。
- 同一 portfolio version 可重放和审计。

### 11.3 Execution reconciliation、恢复和多腿执行

#### 目标

在进程重启、网络断开、交易所超时或状态冲突后，自动恢复本地 Execution 状态，并把无法确定的订单交给明确的 reconciliation 流程。

#### 修改范围

```text
crates/business/execution/service/src/domain/
  order.rs              # 完善状态迁移
  plan.rs               # Plan/Leg 关系和完成策略
  reconciliation.rs     # 新增冲突状态

crates/business/execution/service/src/services/
  reconciliation.rs     # 交易所对账服务
  persistence.rs        # outbox、cursor、幂等记录
  gateway.rs             # 查询、提交和回报关联

crates/business/execution/service/src/application/
  service.rs            # reconcile、resume、pause API
  process.rs            # 启动恢复顺序
```

#### 状态机

```text
Order:
Pending -> Submitting -> Accepted -> PartiallyFilled -> Filled
Submitting -> Unknown -> ReconciliationRequired
Accepted -> CancelRequested -> Canceled
Accepted -> Rejected | Expired
```

新增持久化实体：

```text
ExecutionIdentity {
  intent_id
  plan_id
  leg_id
  order_id
  client_order_id
  remote_order_id
  fill_id
}

ReconciliationCase {
  case_id
  order_id
  local_state
  remote_state
  discrepancy
  resolution
  operator_id
  resolved_at
}
```

#### 恢复流程

```text
启动 Execution
  -> 读取本地未完成订单
  -> 查询交易所 open orders/history
  -> 按 client_order_id/remote_order_id/fill_id 关联
  -> 幂等应用远端事实
  -> 冲突进入 ReconciliationCase
  -> 恢复 Execution snapshot 和 outbox
```

#### 验收标准

- 下单请求超时不会自动重复下单。
- 进程重启不会丢失未完成订单和成交。
- 同一 fill 重复到达不会重复更新状态或账户。
- 本地和远端冲突不会静默覆盖。
- 多腿 Intent 支持 AllOrNothing、BestEffort、Compensate 等策略。

### 11.4 Risk 风控扩展

#### 目标

将 Risk 从预算 reservation 服务扩展为交易前、交易中和交易后的风险决策服务。

#### 修改范围

```text
crates/business/risk/service/src/domain/budget.rs
  # 增加 exposure、margin、loss、rate 等 Metric

crates/business/risk/service/src/domain/
  exposure.rs       # 暴露计算
  margin.rs         # 保证金计算
  circuit.rs        # 熔断器和 kill state
  scenario.rs       # stress/scenario 规则

crates/business/risk/service/src/application/service.rs
  # 增加 pre_trade、post_trade、halt、resume、evaluate

crates/business/risk/service/src/services/actor.rs
  # RiskActor 继续作为预算和熔断状态唯一 owner
```

#### 核心模型

```text
RiskContext {
  account_snapshot_watermark
  market_freshness_watermark
  portfolio_version
  current_exposure
  current_margin
  available_margin
  current_pnl
  current_drawdown
  leverage_bps
  price_deviation_bps
  stress_loss
}

RiskCheck {
  check_id
  scope
  metric
  observed
  limit
  decision
  reason_code
}

CircuitState {
  scope
  state             # closed/open/half_open
  opened_at
  reset_at
  reason
}
```

#### API

```text
pre_trade_check
authorize_and_reserve
post_trade_check
resize_reservation
open_circuit
close_circuit
halt_scope
resume_scope
```

#### 验收标准

- Risk 决策带有 Account、Market、Portfolio 的 watermark。
- stale market、超限暴露、保证金不足和熔断状态会拒绝新订单。
- cleanup 操作可以在依赖短路时释放 reservation。
- 风控决策和 reason code 可以审计和回放。

### 11.5 Account Ledger 和完整 Settlement

#### 目标

把 Account 从“当前账户投影”扩展为可通过事实重建的账户和结算系统。

#### 修改范围

```text
crates/business/account/service/src/domain/
  ledger.rs          # 账本分录
  valuation.rs       # 多币种估值
  pnl.rs             # realized/unrealized PnL
  settlement.rs      # 现货、杠杆、期货、期权结算

crates/business/account/service/src/services/
  persistence.rs     # append-only ledger 持久化
  settlement.rs      # AccountActor 内部结算
  reconciliation.rs  # 账户流水对账
```

#### 核心模型

```text
LedgerEntry {
  entry_id
  account_id
  event_id
  asset_id
  debit
  credit
  currency
  occurred_at
  source
}

SettlementEvent {
  fill_id
  fee
  funding
  interest
  realized_pnl
  balance_delta
  position_delta
}
```

所有余额、持仓和权益变更都应由 AccountActor 应用 `SettlementEvent` 产生，不能由 Execution 直接修改。

#### 验收标准

- 账户快照可以从 ledger 重建。
- 同一 fill 重放不会重复记账。
- realized/unrealized PnL 可分别查询。
- 费用、资金费率和保证金变化有独立明细。
- 账户流水能和 Execution order/fill 完整关联。

### 11.6 Market Data Quality 和可执行性契约

#### 目标

让 Execution 能够直接判断某个市场、某种数据是否可执行，而不是只判断 feed 进程是否连接。

#### 修改范围

```text
crates/business/market/service/src/domain/
  freshness.rs       # 从 feed 状态扩展到 per source/market/kind
  orderbook.rs       # source_id、checksum、sequence、同步状态
  observations.rs    # event_time、receive_time、source identity

crates/business/market/service/src/application/query.rs
  # freshness/orderbook typed query

crates/business/market/contract/src/
  model.rs
  query.rs
  snapshot.rs        # 增加 typed readers
```

#### 核心模型

```text
MarketFreshness {
  source_id
  market_id
  data_kind
  last_event_time
  last_receive_time
  age_nanos
  sequence
  status
}

OrderBookIdentity {
  source_id
  market_id
  checksum
  depth_policy
  synchronized
}
```

#### API

```text
get_freshness(source_id, market_id, data_kind)
is_executable(market_id, requirements)
get_orderbook(market_id, source_id)
read_latest_trades
read_latest_bars
read_latest_greeks
read_latest_rates
read_freshness
```

#### 验收标准

- 同一 market 的不同 source 不会相互覆盖。
- sequence gap 后 OrderBook 进入未同步状态并触发 resync。
- stale quote 不能通过 Execution 的 pre-trade freshness 检查。
- Market contract 不要求消费者解析 generated FlatBuffers。

### 11.7 数据集、研究数据和 Replay 平台

#### 目标

让研究和回测使用可版本化、可验证、可复现的数据集。

#### 修改范围

```text
kairospy/application/market/
  dataset.py          # 数据集 application facade

kairospy/infrastructure/datasets/
  catalog.py          # Parquet/Arrow catalog
  quality.py          # 质量检查
  lineage.py          # 来源和版本

schemas/projection/market/v1/
  history.fbs         # 增加 dataset/version/watermark

crates/business/market/service/src/services/replay.rs
  # 支持 dataset_id、version、checksum 和 checkpoint
```

#### 核心模型

```text
Dataset {
  dataset_id
  version
  source
  instruments
  time_range
  schema_version
  content_hash
  quality_status
}

DatasetQualityReport {
  missing_ranges
  duplicate_count
  timestamp_errors
  sequence_gaps
  outlier_count
}
```

#### 验收标准

- BacktestRun 可以锁定 Dataset version 和 content hash。
- 数据质量失败时不能静默进入回测。
- replay 可以按 watermark 和 checkpoint 恢复。
- 数据处理、清洗和转换步骤具有 lineage。

### 11.8 Strategy / Alpha / Research 生命周期

#### 目标

把 Strategy 从“可加载 Python 模块”扩展为可注册、可版本化、可审核和可发布的研究产物。

#### 修改范围

```text
kairospy/application/strategy/
  registry.py         # 策略注册和查询
  experiments.py      # 实验记录
  promotion.py        # paper/canary/live 晋级

kairospy/application/launch/
  application/configuration.py
  application/registry.py

schemas/projection/strategy/v1/
  registry.fbs
  experiments.fbs
  promotions.fbs
```

#### 核心模型

```text
StrategyArtifact {
  strategy_id
  version
  source_revision
  parameter_hash
  dataset_versions
  owner
  status
}

ExperimentRun {
  experiment_id
  strategy_artifact
  dataset_version
  config_hash
  metrics
  report_path
}

Promotion {
  strategy_version
  from_mode
  to_mode
  approvers
  gate_results
  promoted_at
}
```

#### 验收标准

- 每个 live launch 都能追溯到 Strategy artifact、代码 revision、参数和数据版本。
- 没有通过 backtest、paper 和风险 gate 的版本不能晋级 live。
- 策略可以 pause、resume、rollback。
- 实验结果不能被后续运行覆盖。

### 11.9 运营、告警和人工干预

#### 目标

让系统健康状态能够直接支持交易决策和事故处理。

#### 修改范围

```text
schemas/projection/system/v1/
  metrics.fbs        # 新增指标快照
  alerts.fbs         # 扩展告警生命周期
  operations.fbs     # kill/pause/resume/manual actions

crates/kairos-workspace/src/
  observability.rs   # 指标采集和导出
  control.rs         # 运维命令

kairospy/surface/console/
  app.py             # 风险、订单、freshness 和告警视图
```

#### 核心模型

```text
OperationalAlert {
  alert_id
  component
  severity
  state
  condition
  first_seen
  last_seen
  acknowledged_by
}

OperationalCommand {
  command_id
  scope
  action             # halt, resume, cancel_all, flatten
  operator
  reason
  confirmation
}
```

#### 验收标准

- Feed stale、订单拒绝率、账户差异和风险超限可自动告警。
- `cancel_all`、`flatten`、`halt` 等命令经过权限和确认。
- kill switch 不依赖策略进程存活。
- 所有人工操作都有审计记录。

### 11.10 Universe Selection、因子和指标库

#### 目标

为成熟研究场景提供可复用的标的选择、特征和因子计算能力。

#### 修改范围

建议新增研究侧模块，不直接塞入 Market：

```text
kairospy/research/
  universe.py
  factors.py
  indicators.py
  feature_store.py
```

Reference 负责提供可用标的目录和生命周期；Research 负责按照时间点和规则生成 universe 与 feature snapshot；Strategy 只消费版本化结果。

#### 验收标准

- Universe 选择遵守 point-in-time 语义。
- 因子计算可缓存、可版本化、可复现。
- 指标结果与 Dataset version 和参数 hash 绑定。

### 11.11 Portfolio optimizer、风险模型和 TCA

#### 目标

在基本 TargetPosition 闭环稳定后，增加组合优化、风险归因和执行质量分析。

#### 修改范围

```text
kairospy/research/portfolio/
  optimizer.py
  risk_model.py
  attribution.py

kairospy/application/execution/
  tca.py
```

输入来自 Portfolio target、Account exposure、Market prices 和历史收益，不应让 Execution 自己实现优化器。

应支持：

- 目标收益/风险权衡。
- 行业、币种、策略和杠杆约束。
- 协方差矩阵和因子暴露。
- VaR、expected shortfall 和压力情景。
- 实盘成交价格与 arrival price、mid price、VWAP 的比较。

## 12. 技术实施优先级映射

| 阶段 | 主要修改 | 交付目标 |
|---|---|---|
| 0 | 修复 Risk contract 编译错误，恢复测试基线 | 所有后续改动可验证 |
| 1 | Market replay、Execution simulator、Account settlement | 可重复的一体化回测 |
| 2 | Execution reconciliation、Risk pre/post checks、Market freshness | 可安全运行 paper/canary |
| 3 | Account ledger、Portfolio Construction、目标仓位聚合 | 多策略组合闭环 |
| 4 | Dataset catalog、Strategy Registry、Experiment/Promotion | 研究到上线可审计 |
| 5 | Operations、alerts、kill switch、TCA | 生产运维和事故处理 |
| 6 | Universe、因子、optimizer、风险模型 | 成熟研究平台能力 |

## 13. 每个阶段的统一验收要求

所有业务模块新增能力都应同时提供：

1. Domain 单元测试。
2. Application API 测试。
3. Contract 编解码测试。
4. Snapshot/event watermark 测试。
5. 重启、重复、乱序和失败恢复测试。
6. 至少一个跨模块集成测试。
7. CLI 或可观测接口。
8. JSONL 结构化日志和关键指标。
9. 模块边界架构测试。
10. 对应的设计文档和运行手册。

最终的完成标准不是目录中出现了新模块，而是每项能力都能从 application API 进入、由唯一 Actor 管理状态、通过 contract 交付、在故障后恢复，并且可以被测试和审计。

## 14. 当前已落地的回测改造

截至当前工作区，第一段回测闭环已经落地：

- ExecutionSimulator 位于 crates/business/execution/service/src/services/simulator.rs，支持 Quote 驱动的市价单、限价单、报价数量限制、部分成交、撤单、手续费和滑点。
- BacktestApplication::run 支持接收 Market contract events 和 simulated orders，返回订单状态和 fills；现有 evaluate 指标接口继续保留。
- Execution process 新增 /v1/backtest/run，Python contract 新增 backtest_run。
- Account settlement 支持加权平均成本和 realized PnL，不再每次成交直接覆盖平均价格。
- Account 新增 mark_to_market application API 和 /v1/mark-to-market 控制端点，支持更新 mark price、unrealized PnL、equity 和 net profit。
- Python application 新增 run_backtest，负责把 Execution fills 交给 Account settlement，再推进 Account mark-to-market。

当前回测运行链路已进一步接入默认的 backtest 进程拓扑：

- simulated/paper Execution 进程启用 ExecutionSimulator；普通 `/v1/submit` 和策略 Intent 创建的订单会注册到模拟器。
- StrategyHost 在回测模式处理完一个 Market quote 后，将同一个 Quote 转发到 Execution `/v1/backtest/market`，因此订单先提交、行情后到达，符合事件驱动撮合时序。
- Execution 生成的 SimulationFill 复用既有 `record_fill -> preflight -> Account` 链路，Execution 继续拥有交易所侧订单生命周期，Account 继续拥有余额、持仓、成本和损益结算。
- simulated/paper preflight 会把成交发布到 Account `/v1/simulated-fill`，而非只记录外部成交事实；结算资产、成交金额和手续费因此进入回测账户账本。
- 独立的 `kairospy.application.backtest.run_backtest` 仍可用于无进程、确定性的批量回测；它直接编排 Execution contract、Account settlement 和 mark-to-market。
- Replay feed 现在有明确完成状态；Market 结束后关闭可回放事件流，Strategy 正常退出并生成 `state/backtest/report.json`，CLI 可用 `launch wait` 等待并清理运行时组件，用 `launch report` 读取结果。
- Strategy 回测进程按每个行情事件记录 Account snapshot 形成 equity curve，并把成交按事件时间插入 mark-to-market 过程。

仍需补齐的是带真实 Strategy Intent、真实 Account/Execution/Market 子进程的完整 Launch 集成测试，以及组合多账户/多标的报告汇总。核心单账户回测路径已经具备“行情回放—策略—执行—结算—权益曲线—报告”的闭环。
