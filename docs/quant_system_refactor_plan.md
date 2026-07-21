# KairoSpy 量化系统正交化重构规划

状态：Draft，architecture-first  
日期：2026-07-22  
适用对象：KairoSpy trading、data、strategy、backtest、simulation、live、execution、runtime、governance 的中长期重构

本文目标是把 KairoSpy 从“功能模块并列”推进到“正交平面组合”。专业量化系统不应该让研究、策略、回测、执行、风控、数据接入和运行治理互相嵌入。它们应该通过稳定契约组合，每个平面只拥有自己的事实、规则和生命周期。

核心原则：

```text
运行模式是系统配置，不是业务逻辑分支。
策略只产生经济意图，不直接下单、不写账本、不调用 connector。
Data 产品负责数据接入和治理，Market Plane 负责运行时行情状态。
RunKernel 统一流程骨架，BacktestProfile / SimulationProfile / LiveProfile 区分模式假设。
```

## 1. 当前问题判断

KairoSpy 已经有清晰的分层意图：

- `trading` 定义产品、账户引用、订单、成交、意图、账本等交易业务事实。
- `ports` 和 `connectors` 隔离外部 provider、venue、account、market data。
- `data` 管理 Dataset、release、quality、reader、acquisition。
- `strategy` 提供用户策略协议。
- `application` 和 `orchestration` 承载运行、恢复、监控、kill switch。
- `backtest` 提供确定性回放和模拟成交。

问题不在“没有分层”，而在几个关键类和产品入口已经承担了多个平面的职责。

### 1.1 BacktestEngine 是耦合中心

`kairospy/backtest/engine.py` 目前同时负责：

- market replay。
- option valuation。
- feature update。
- strategy callback。
- risk evaluation。
- execution planning。
- fill model。
- settlement。
- portfolio mutation。
- metrics。
- validity classification。
- run identity。

这会让 backtest 变成一套独立交易系统。后续 simulation/live 若重新实现类似流程，语义会分叉；若继续复用 `BacktestEngine`，又会把 backtest 假设泄漏到 live runtime。

### 1.2 Context 暴露了太多系统内部对象

当前代码里的旧 `StrategyContext` 暴露 market、portfolio、working orders、catalog、valuation、surface、features、risk state、strategy positions、factor snapshots、intent executions。目标架构中这个策略输入对象应重命名为 `Context`。

策略获得的信息越多，越容易依赖运行环境细节。专业系统里，策略应该依赖稳定的输入视图，而不是直接看到估值服务、reference repository、risk 内部状态或执行追踪细节。

### 1.3 Trading Intent 混入了策略 archetype

`kairospy/trading/intent.py` 同时存在通用意图和业务模板意图，例如：

- `TargetPositionIntent`
- `TargetExposureIntent`
- `OpenStructureIntent`
- `CloseStructureIntent`
- `CoveredCallIntent`
- `ProtectivePutIntent`
- `CashAndCarryIntent`

前四类是基础交易意图，后几类更像策略 archetype 或 portfolio construction 模板。把 archetype 放进 trading，会让核心 trading 随策略品类膨胀。

### 1.4 Product facade 有变厚风险

`kairospy/product_surface.py` 是好的用户入口，但它引入了 application、data、capture 等多个模块。如果这个入口继续承载 provider 选择、运行状态机、治理规则和业务状态迁移，它会变成新的耦合层。

门面层应该只做用例编排和用户体验适配，不拥有核心规则。

## 2. 目标架构

目标不是简单拆目录，而是建立几个互相正交的平面。

```text
Shared Fact Language
  稳定身份、交易事实、产品引用和跨平面事实类型。目标不再是一个膨胀的 trading 目录，而是按 owner 拆到 identity/reference/market/execution/portfolio/strategy/products。

Data Plane
  数据接入、release、quality、lineage、point-in-time 数据治理

Market Plane
  运行时行情状态、市场事件投影、市场质量和时态语义

Research Plane
  假设、特征、标签、验证、研究 artifact

Strategy Plane
  用户代码入口，消费 Context，产生 EconomicIntent

Portfolio & Risk Plane
  账本事实、组合投影、风险预算、限制、保证金、审批

Execution Plane
  intent -> order command -> venue/simulated execution -> fill -> recovery

Runtime Plane
  RunKernel + BacktestProfile / SimulationProfile / LiveProfile

Governance Plane
  readiness、promotion、audit、kill switch、reconciliation、evidence
```

Backtest、simulation、live 应该共享同一条上层语义：

```text
Canonical Event
  -> MarketView
  -> Context
  -> EconomicIntent
  -> RiskApproval / RiskRejection
  -> OrderCommand
  -> ExecutionEvent / Fill
  -> LedgerFact
  -> PortfolioView
  -> RunArtifact
```

它们只替换 profile 和外部世界 adapter：

| Mode | Clock | Market Source | Execution | Persistence |
|---|---|---|---|---|
| backtest | replay clock | historical Dataset Release | deterministic fill model | run-local artifact |
| simulation | replay clock 或 system clock | recorded replay 或 live market connector | simulated venue / test harness | runtime store |
| live | system clock + venue timestamps | live market connector | real execution connector | durable runtime store |

## 3. 平面边界

### 3.1 Shared Fact Language

`trading/` 这个名字如果表示“所有交易相关业务”，范围太大；如果只表示“交易事实语言”，又容易持续诱导新功能往里面塞。因此目标架构不应该继续把 `trading/` 作为一级产品目录。

更准确的设计是：没有一个叫 `trading` 的最终产品域。当前 `trading/` 里的文件按事实 owner 拆到各自产品边界。

职责：

- `identity/`：asset、instrument、venue、institution、AccountRef 等稳定标识。
- `reference/`：产品和合约定义、instrument resolution、point-in-time reference。
- `market/`：quote、trade、bar、order book、market state、market event。
- `execution/`：order、fill、execution report、order status、execution instruction。
- `portfolio/`：ledger fact、cash movement、position lot、fee、settlement。
- `strategy/`：EconomicIntent、TargetPosition、TargetExposure、intent builder、strategy contract。
- `products/`：corporate action、derivative lifecycle、funding、exercise、settlement 规则。
- `integrations/`：capability、provider-neutral envelope、external gateway contracts。

不负责：

- 作为“所有交易业务”的总包。
- 数据源选择。
- 数据质量等级。
- 策略生命周期。
- run artifact。
- backtest fill model。
- connector payload。
- account state、credential、entitlement、lock、recovery。
- CLI 输出。

建议：

- 不再把 `trading/` 作为目标一级产品目录。
- 最终公开结构中删除 `trading/` 这个汇总包；当前文件必须拆到明确 owner。
- 将 `CoveredCallIntent`、`ProtectivePutIntent`、`CashAndCarryIntent` 迁移为 strategy archetype 或 intent builder。
- 通用交易目标也不应留在 `trading.intent`，而应进入 `strategy/intents.py`，因为它是策略输出语言。
- `AccountRef` 可以作为稳定标识存在，但不应放在一个大而泛的 `trading/` 内；目标位置是 `identity/accounts.py`。

当前 `trading/` 文件建议拆分如下：

| 当前文件 | 目标 owner | 原因 |
|---|---|---|
| `trading/identity.py` | `identity/` | 只保留 AssetId、InstrumentId、VenueId、InstitutionId、AccountRef 等稳定标识 |
| `trading/product.py` | `reference/contracts.py` + `products/specs.py` | 产品定义归 reference，产品族规则归 products |
| `trading/market_data.py` | `market/types.py` | quote、trade、bar、order book 是 Market Plane 输入/状态 |
| `trading/market_state.py` | `market/state.py` / `market/projection.py` | market state 是运行时市场投影 |
| `trading/order.py` | `execution/orders.py` + `execution/fills.py` | order/fill 是执行状态机事实 |
| `trading/execution.py` | `execution/events.py` + `portfolio/ledger_events.py` | trade execution 属于 execution，funding/dividend payment 属于 portfolio ledger/product lifecycle |
| `trading/ledger.py` | `portfolio/ledger.py` | ledger 是 portfolio/accounting 事实源 |
| `trading/intent.py` | `strategy/intents.py` + `strategy/archetypes/` | intent 是策略输出语言，archetype 不应污染核心事实模型 |
| `trading/capability.py` | `integrations/capabilities.py` + `execution/capabilities.py` + `market/subscriptions.py` | capability 是系统能力声明，不是交易事实 |
| `trading/corporate_action.py` | `products/equity/corporate_actions.py` 或 `products/common/lifecycle/` | corporate action 是产品生命周期规则 |
| `trading/derivative_event.py` | `products/common/lifecycle/derivatives.py` | derivative lifecycle 属于产品规则 |
| `trading/event.py` | `market/events.py` + `integrations/events.py` | 市场事件归 market，broker/system event 归 integrations/governance |
| `trading/strategy_contract.py` | `strategy/contracts.py` | strategy contract 是 Strategy SDK 边界 |
| `trading/__init__.py` | 删除 | 最终目标不提供 `trading` 聚合入口，公开 API 由 `surface/` 和具体产品 owner 提供 |

### 3.1.1 Account 边界

`account` 不是一个单一领域对象。专业量化系统里，账户至少要拆成几种不同概念：

| 概念 | 归属 | 说明 |
|---|---|---|
| `AccountRef` | `identity/accounts.py` | 订单、成交、ledger fact 上引用的账户维度，只是稳定标识 |
| Account binding | `workspace/` / `runtime/` | 某个 run 使用哪个 account、environment、capital scope |
| Account state | `portfolio/` | cash、positions、margin、buying power、account equity 等投影 |
| Account facts | `portfolio/ledger` | cash movement、transfer、fee、settlement、balance adjustment 等账本事实 |
| Account capability | `integrations/capabilities.py` | connector 是否支持 account query、margin、position sync、transfer |
| Account connector | `integrations/connectors/*` | broker/exchange account API adapter |
| Account lock/recovery | `runtime/` / `governance/` | 实盘账户锁、启动恢复、reconciliation、unknown external state |
| Account entitlement/credential | `integrations/` / configuration secret store | provider 权限、key、sub-account、permission scope |

硬规则：

- 最终目标没有 `trading/` 账户模型。
- `identity/accounts.py` 只能有 `AccountRef`、account id、account namespace 这类稳定标识。
- balance、margin、buying power、credential、permission、lock、recovery 必须分别归 portfolio、integrations、runtime、governance。
- `PortfolioView` 展示账户状态，但状态来自 ledger、market 和外部 account facts 的投影。
- `LiveProfile` 可以锁定 account binding，但锁属于 runtime/governance，不属于身份模型。

因此，当前 `trading/` 的最终处理不是“缩小后保留”，而是：

```text
identity + reference + market + execution + portfolio + strategy + products + integrations
```

### 3.2 Data Plane

职责：

- Dataset logical key。
- release、schema、quality、lineage。
- point-in-time view。
- historical acquisition。
- live view registration。
- normalized canonical events。
- replay source。

不负责：

- 策略是否赚钱。
- 风控批准。
- 订单状态机。
- 账户恢复。

建议：

- `DataProductDefinition` 和 `DatasetRelease` 继续保留。
- 明确区分 `Dataset`、`LiveView`、`EventSource`。
- Strategy 和 runtime 不直接依赖 lake path、provider credential、source cache。
- Run 只消费 data snapshot 或 event source contract。

### 3.2.1 Data 产品和 Market Plane 的关系

Data 产品负责“接入和治理数据”，Market Plane 负责“把数据变成运行可消费的市场状态”。这两个概念不能混在一起。

```text
Provider / User File / External Feed
  -> Data Product
  -> Dataset Release 或 Live View
  -> Canonical Event Source
  -> Market Plane
  -> MarketView / MarketSnapshot / Market Projection
```

Data 产品回答：

- 数据从哪里来。
- 数据是否有权限。
- 数据 schema 是什么。
- 数据质量等级是什么。
- 数据 release/hash/lineage 是什么。
- 历史数据是否完整。
- live view 是否配置好。

Market Plane 回答：

- 当前 point-in-time 市场状态是什么。
- 当前可见 instrument universe 是什么。
- quote、trade、bar、order book、mark、index、funding 等事件如何合并。
- 历史 replay 和 live stream 如何映射成同一种市场事件语义。
- 策略看到的 market view 是否满足时间语义和质量门槛。

因此，策略和 run loop 不应该直接读 Data Product；它们应该消费 Market Plane 产出的 `MarketView`。Data Product 是输入资产治理，Market Plane 是运行时市场状态。

### 3.3 Strategy Plane

职责：

- 定义策略协议。
- 加载用户 entrypoint。
- 管理策略 callback。
- 产生 `EconomicIntent`。
- 记录 strategy decision evidence。

不负责：

- 直接下单。
- 直接读取 connector。
- 账户锁。
- 风控通过或拒绝。
- fill model。
- 账本入账。

建议：

- 收窄 `Context`，改为稳定输入视图：

```python
@dataclass(frozen=True, slots=True)
class Context:
    now: datetime
    market: MarketView
    portfolio: PortfolioView
    features: FeatureView
    reference: ReferenceView
    orders: OrderView
    intents: IntentView
    budget: BudgetView
```

- `valuation`、`surface`、`factor_snapshots` 必须通过 feature/model view 暴露，不直接暴露具体 service 或内部 snapshot。
- 策略只返回 `EconomicIntent` 或一组 trading intent，由 runtime 包装成 governed intent。
- 目标公开命名使用 `Context`。在 `kairospy.strategy` 边界内，`Context` 已经足够表达“策略运行时输入”；`Strategy` 前缀留给 `StrategyDecision`、`StrategyRuntime` 这类确实需要区分的对象。

### 3.3.1 Context 边界

`Context` 是策略代码唯一应该依赖的运行时输入。它不是系统内部对象的集合，也不是 backtest/live 的透传容器。

命名上，`View` 表示策略可读的输入视图；`Decision` 只保留给策略产生的决策记录，例如 `StrategyDecision`。

它应该只暴露决策所需的稳定视图：

| View | 来源平面 | 策略能做什么 | 策略不能做什么 |
|---|---|---|---|
| `MarketView` | Market Plane | 读取点时市场状态、行情、可见 universe、市场质量信号 | 直接访问 DataClient、DatasetRelease、connector payload |
| `PortfolioView` | Portfolio/Risk Plane | 读取现金、持仓、敞口、组合风险摘要 | 修改账本、触发结算、调用 risk engine |
| `FeatureView` | Feature/Model Plane | 读取因子、估值摘要、模型输出、特征 hash | 调用定价服务、重新校准 surface、读取模型内部状态 |
| `ReferenceView` | Reference Plane | 读取稳定 instrument/product identity 和必要合约摘要 | 访问 reference repository、同步外部 reference provider |
| `OrderView` | Execution Plane | 读取本策略相关 working order 或 pending command 摘要 | 提交、撤单、恢复订单 |
| `IntentView` | Execution Plane | 读取 intent 进度、剩余量、是否 active | 直接改 intent status 或 execution tracker |
| `BudgetView` | Risk/Governance Plane | 读取 approved capital、risk budget、reduce-only 状态 | 批准自己的风险预算 |

明确不应该出现在 `Context` 里的对象：

- `DataClient`
- `DatasetRelease`
- `DataCatalog`
- concrete connector client
- REST/WebSocket transport
- provider DTO
- execution gateway
- order recovery service
- ledger service
- risk engine
- backtest fill model
- pricing service
- volatility calibration service
- full reference repository

策略的职责是：

```text
Context -> EconomicIntent
```

不是：

```text
Context -> Order
Context -> Fill
Context -> Ledger mutation
Context -> Data acquisition
Context -> Connector call
```

### 3.4 Portfolio & Risk Plane

职责：

- 从 ledger/fill/market 派生 portfolio view。
- 计算 exposure、greeks、margin、cash、concentration。
- 执行 pre-trade 和 post-trade 检查。
- 给出 `RiskApproval`、`RiskRejection`、`ReduceOnlyDirective`。

不负责：

- 下单。
- 生成策略信号。
- 读取原始 provider 数据。
- 管理 runtime startup。

建议：

- 风控输入使用不可变 snapshot。
- 风控输出是 approval/rejection，不直接创建 venue order。
- post-trade 风险降低应输出 reduce-only directive，由 execution/run loop 决定如何生成关闭意图。
- Ledger facts 是事实源；PortfolioView 是 projection；BudgetView 是 risk/governance 输出。

### 3.5 Execution Plane

职责：

- 将 approved intent 规划为 order command。
- 管理 client order id、幂等、outbox。
- 处理 venue order 状态。
- 摄取 execution report 和 fill。
- 执行 order recovery。

不负责：

- 策略信号。
- 数据采集。
- 组合估值。
- run readiness policy。

建议：

```python
class ExecutionGateway(Protocol):
    def submit(self, command: OrderCommand) -> OrderAck: ...
    def cancel(self, command: CancelCommand) -> CancelAck: ...
    def events(self) -> Iterable[ExecutionEvent]: ...
```

- SimulationProfile 和 LiveProfile 应共享 execution event 语义。
- BacktestProfile 也必须输出同一种 execution evidence；差异只在 deterministic fill model 和 replay clock。
- IBKR、Binance、simulated/test harness 都实现同一 gateway contract。

### 3.6 Runtime Plane

职责：

- 管理 mode。
- 管理 startup/shutdown。
- 驱动统一 run flow。
- 组合 profile 提供的 market source、execution driver、store/recovery/gate policy。

不负责：

- 定义业务产品。
- 定义策略 archetype。
- 实现 provider 数据下载。
- 持有具体 connector 实现。

建议：

- 引入薄的 `RunKernel`，但不要让它吞掉所有实现细节。
- `RunKernel` 负责统一编排、统一证据、统一 run artifact。
- 三种 run mode 通过不同 profile/driver 注入差异部分：

```text
RunKernel
  BacktestProfile
  SimulationProfile
  LiveProfile
```

- Kernel 统一的是流程骨架，不统一每个 mode 的具体市场源、执行方式、持久化和恢复策略。
- 三个 profile 可以复用小服务和稳定契约，但不要求内部实现完全一致。
- `KairosApplication` 继续负责 durable startup gate，但不承载策略循环。

### 3.6.1 RunKernel 和三种 Profile

KairoSpy 需要 `RunKernel`，但用户心智不应该是十几个组件自由组合。更合理的结构是：

```text
RunKernel
  mode: backtest | simulation | live
  profile: BacktestProfile | SimulationProfile | LiveProfile
```

`RunKernel` 固定职责：

- 接收 Workspace snapshot、data binding snapshot、strategy entrypoint、run config。
- 建立本次 run 的身份、时间边界和 artifact 边界。
- 让 profile 提供 market source、execution driver、store/recovery/gate policy。
- 构造 `Context`。
- 驱动 `Context -> EconomicIntent -> RiskApproval -> execution` 的主流程。
- 收集 run evidence，输出 `RunArtifact`。

`RunKernel` 不应该拥有的职责：

- 不直接知道 Binance、IBKR、Massive 等具体 connector。
- 不直接实现期权定价、fill model、venue recovery。
- 不直接决定某个 run mode 的成交语义。
- 不把 backtest 的同步假设带进 live。
- 不把 live 的 durable recovery 强加给简单 backtest。

共同稳定流程：

```text
Input:
  Workspace snapshot
  Data binding snapshot
  Strategy entrypoint
  Run mode config

Stable flow:
  MarketView
    -> Context
    -> EconomicIntent
    -> RiskApproval / RiskRejection
    -> OrderCommand 或 SimulatedOrder
    -> ExecutionEvent / Fill
    -> Ledger fact
    -> PortfolioView
    -> RunArtifact
```

差异由 profile 提供。

### 3.6.2 三种 Profile 的差异点

只有这些部分应该按 backtest、simulation、live 明确区分：

| 差异点 | BacktestProfile | SimulationProfile | LiveProfile |
|---|---|---|---|
| Clock | replay clock | replay clock 或 system clock | system clock + venue timestamps |
| Market source | historical Dataset Release replay | recorded replay 或 live market connector | live market connector |
| Market completeness | 固定历史窗口和数据覆盖率 | replay/live 质量门槛 | live freshness、drop、latency、halt 状态 |
| Execution driver | deterministic fill model | simulated venue / test harness | real execution connector |
| Order lifecycle | 可以简化 | 应完整模拟 ack/pending/partial/reject/cancel | 必须完整、持久、可恢复 |
| Fill source | 本地模型 | simulated venue | venue execution report |
| Store | run-local artifact | runtime store 建议开启 | durable runtime store 必须开启 |
| Recovery | 不需要真实恢复 | 用于演练恢复 | 必须恢复和 reconcile |
| Readiness gates | 数据覆盖和配置检查 | market/adapter/store readiness | account lock、order recovery、venue reconciliation、kill switch |
| Connector | 历史数据和 reference 可用 | market/reference 可真实，execution 模拟 | market/reference/execution/transfer 真实 |

Profile contract：

```text
RunProfile
  market_source()
  execution_driver()
  store_policy()
  readiness_gates()
  recovery_policy()
  artifact_policy()
```

具体 profile 决定这些实现，kernel 只调用 profile 提供的能力。

### 3.6.3 BacktestProfile / BacktestEngine

`BacktestProfile` 的目标是“可复现的历史评估”，不是模拟真实交易系统的所有异步细节。当前 `kairospy/backtest/engine.py` 可以视为 BacktestProfile 的实现参考。

输入：

- approved historical Dataset Release。
- frozen reference catalog。
- strategy entrypoint 和参数。
- backtest config：时间窗口、初始资金、手续费、fill model、force close、数据覆盖阈值。

内部流程：

```text
validate inputs
derive deterministic run id
build replay feed
build deterministic id factory
build reference catalog
build valuation/feature services
build risk engine
build simulated planner/fill model
build ledger-backed portfolio

for market slice in historical replay:
  advance replay clock
  value market / build feature view
  advance existing simulated orders against current market
  apply fills to portfolio ledger
  apply due settlements
  build portfolio snapshot
  run post-trade risk checks
  build Context
  call strategy callbacks
  evaluate intents with risk
  plan approved intents into simulated orders
  record snapshots, fills, decisions, metrics evidence

finish:
  call strategy.on_end
  cancel remaining simulated orders
  optionally force close
  reconcile cash
  calculate metrics
  build BacktestResult / RunArtifact
```

边界：

- 可以使用 deterministic fill model。
- 可以在同一个 market tick 内推进 order/fill/portfolio/strategy，因为目标是确定性评估。
- 不需要真实 connector execution。
- 不需要 durable order recovery。
- 不需要处理 venue ack/reject 的真实异步生命周期。

输出：

- `BacktestResult`
- intents、risk decisions、orders、fills、settlements、portfolio snapshots、metrics、validity reasons、audit hash。

Connectors 用途：

- 可以用于历史数据 acquisition。
- 可以用于 reference/data normalization。
- 不参与订单提交。

### 3.6.4 SimulationProfile

`SimulationProfile` 的目标是“用实时或回放行情驱动一个更接近交易系统的执行过程”，它介于 backtest 和 live 之间。

它和 backtest 的区别：

- backtest 是历史评估，通常是同步、确定性、批处理。
- simulation 是运行时演练，可以是 live clock，也可以是 recorded replay clock。
- simulation 应该保留 order ack、pending、partial fill、cancel、reject、latency、rate limit 等执行状态。
- simulation 不提交真实订单，但要逼近真实 execution lifecycle。

输入：

- Dataset replay、recorded event replay 或 live market stream。
- strategy entrypoint。
- simulated account。
- simulated venue model。
- simulation config：latency、liquidity model、partial fill policy、reject policy、fee model、slippage model。

内部流程：

```text
start simulated runtime
connect market source
initialize simulated account and store

on market event:
  update market view
  update simulated execution state
  emit simulated fills/rejections/cancels if conditions are met
  update ledger and portfolio
  update risk/budget/reduce-only state
  build Context
  call strategy
  risk-check intents
  send approved commands to simulated venue
  persist runtime facts

on simulated execution event:
  update order state
  update intent progress
  update ledger if fill
  expose updated OrderView / IntentView
```

边界：

- 使用 simulated execution connector 或 simulated venue adapter。
- 可以使用真实 market data connector。
- 不调用真实 broker/exchange submit。
- 需要 runtime store，因为它用于演练恢复、重启和状态推进。
- 比 backtest 更重视 order lifecycle，而不是只看最终成交价格。

输出：

- simulation run artifact。
- order lifecycle evidence。
- simulated fills。
- intent progress。
- portfolio/risk timeline。
- readiness/soak/recovery evidence。

Connectors 用途：

- market data connector 可以是真实 live。
- reference connector 可以是真实。
- execution connector 应该是 simulated/test harness，不是真实 live submit。

### 3.6.5 LiveProfile

`LiveProfile` 的目标是“真实运行”，核心不是收益评估，而是安全、恢复、幂等、审计和外部状态一致性。

输入：

- live market view。
- live account binding。
- strategy entrypoint。
- execution connector。
- runtime store。
- readiness gates。
- risk limits。
- kill switch policy。

内部流程：

```text
start application runtime
acquire account lock
recover unresolved orders
reconcile external venue state
run readiness probes
connect market data stream
connect execution stream

on market event:
  update market view
  update portfolio/risk view from latest ledger and external facts
  build Context
  call strategy
  risk-check intents
  write order command to outbox
  submit through execution connector if operational

on execution event:
  ingest ack/reject/fill/cancel
  update order state
  update intent progress
  post ledger facts
  reconcile with venue when needed
  update portfolio/risk state

on fault:
  degrade or reduce-only
  stop submitting new risk
  preserve external-state evidence
```

边界：

- 必须通过 connectors 提交订单和接收 execution reports。
- 必须有 durable outbox、idempotency、recovery、reconciliation。
- 不能使用 backtest fill model。
- 不能假设同步成交。
- 不能在 unknown external state 下继续正常运行。

输出：

- durable runtime facts。
- order/fill/ledger events。
- reconciliation reports。
- run artifact。
- incident/degradation evidence。

Connectors 用途：

- market data connector：真实行情。
- reference connector：必要的产品和 venue reference。
- execution connector：真实 order submit/cancel/status/execution stream。
- transfer connector：真实资金调拨和 treasury facts。

### 3.6.6 共享 Kernel，不共享模式假设

三个 profile 应该共享稳定契约：

- `MarketView`
- `Context`
- `EconomicIntent`
- `RiskApproval` / `RiskRejection`
- `OrderCommand`
- `ExecutionEvent`
- `Fill`
- `LedgerEntry`
- `PortfolioView`
- `IntentView`
- `RunArtifact`

但不应该共享模式假设。更好的演进方式：

```text
先明确 RunKernel + BacktestProfile / SimulationProfile / LiveProfile
  -> 保持每个 profile 的用户语义简单
  -> 只抽出明显稳定的小服务
  -> 等重复流程稳定后再把小服务沉到 kernel 周边
```

## 4. 内置业务能力边界

KairoSpy 可以内置期权、波动率、期货、永续、资金费率、公司行为等业务能力。问题不在“是否内置”，而在这些能力不能和系统内核混在一起。

专业量化系统通常会同时有：

- 通用系统内核：运行、数据、执行、风控、账本、治理。
- 内置业务能力包：期权估值、波动率曲面、期权结构风险、期货结算、永续资金费率。
- 用户业务代码：策略、研究、私有因子、组合构建。

内置业务能力包必须满足三个边界：

1. 只能依赖更底层的通用契约。
2. 不能反向修改 runtime、data、execution 的核心语义。
3. 必须能被替换、禁用或绕过。

### 4.1 期权逻辑可以内置，但应该分层

当前期权相关能力分布较广：

| 当前位置 | 当前内容 | 推荐定位 |
|---|---|---|
| `trading/product.py` | listed option、crypto option 合约 spec | `reference/contracts.py` + `products/*/contracts.py` |
| `pricing/` | Black、implied vol、option valuation | `analytics/pricing/` |
| `volatility/` | SVI、surface、calibration | `analytics/volatility/` |
| `products/listed_option/` | listed option lifecycle | 产品业务规则包 |
| `products/crypto_option/` | crypto option settlement | 产品业务规则包 |
| `risk/option_structure.py` | option structure 风险 | risk 平面的期权扩展 |
| `risk/covered_call.py` | covered call 风险/模板 | strategy archetype 或 risk extension，不应是核心 risk |
| `capture/option_*` | option universe、snapshot、分析 | `research/capture/` |
| `features/option_skew.py` | option skew feature | `analytics/features/` |
| connector option chain/archive/snapshot | provider 数据适配 | `integrations/connectors/*` |

最终边界：

```text
Reference / Products
  Option contract terms
  Exercise style
  Settlement type
  Multiplier
  Expiry / exercise / assignment / settlement rules

Analytics
  Option pricing
  Implied volatility
  Volatility surface
  Greeks
  Valuation evidence

Risk / Strategy
  Option structure risk extension
  Covered call
  Protective put
  Cash and carry
  Iron condor
  Strategy archetype builders that emit generic intents

Integrations
  IBKR option chain
  Deribit option chain
  Binance option snapshots
  Massive close implied volatility
```

这里的关键是：`OptionSpec` 属于 reference/products，`BlackScholes` 属于 analytics/pricing，`ListedOptionSettlement` 属于 products/listed_option，`CoveredCallIntent` 不属于基础事实模型，`IBKROptionChainProvider` 属于 integrations/connectors，不能成为策略可见对象。

### 4.2 内置业务能力的依赖方向

允许：

```text
analytics/pricing -> reference + products + market inputs
analytics/volatility -> reference + market inputs
products/listed_option -> reference + identity
risk option extension -> portfolio view + market view + analytics valuation
strategy archetype -> strategy intents + reference summaries
connector option adapter -> integrations contracts + reference/market mappings
```

不允许：

```text
identity -> analytics/pricing
reference -> analytics/volatility
products -> strategy archetypes
runtime kernel -> concrete option pricing model
strategy protocol -> backtest option snapshot
connector -> strategy archetype
```

因此，期权能力应该通过 service/view/capability registration 进入系统，而不是被写死在 `BacktestEngine` 或 `Context` 里。

### 4.3 期权能力最终落点

目标状态：

- `analytics/pricing/`、`analytics/volatility/`、`products/*option/` 是内置业务能力包。
- 具体期权策略意图不进入基础事实模型；`CoveredCall`、`ProtectivePut`、`CashAndCarry` 由 `strategy/archetypes/` 或 `strategy/intent_builders.py` 生成通用 intent。
- `risk/option_structure.py` 是 risk extension，输入只能是 `PortfolioView`、`MarketView`、`ReferenceView` 和 valuation evidence。
- `research/capture/option_*` 是研究样本和分析工具，不参与 runtime kernel。
- Backtest、simulation、live 使用 option valuation 时，都通过 valuation capability 注入，不能直接 import 具体模型。

```python
@dataclass(frozen=True, slots=True)
class BusinessCapability:
    capability_id: str
    product_types: tuple[ProductType, ...]
    services: tuple[str, ...]
```

- option pricing、vol surface、option structure risk 都通过 capability 注册到 runtime composition。
- 没有注册期权能力时，股票/永续/期货策略不加载任何期权模型。

## 5. 当前文件夹定位清单

本节梳理当前 `kairospy/` 下所有一级文件夹的定位、应保留职责、边界风险和迁移建议。`__pycache__` 是运行产物，不属于源码结构，应该忽略并避免提交。

### 5.1 `trading/` 定位

`trading/` 是当前核心交易业务模型层。它直接表达这里放的是跨运行模式稳定的交易事实和交易语义。这里的“交易”不能泛化成所有交易系统业务对象，尤其不能把完整账户系统放进去。

当前 `trading/` 承载：

- instrument、asset、venue、institution、AccountRef 等身份值对象。
- product contract spec。
- order、fill、execution side 等交易事实。
- ledger、market state 等业务事实。
- intent 等策略到执行之间的经济目标。

它不是泛化的“所有业务逻辑”目录，而是：

```text
trading business model
```

推荐结构：

```text
kairospy/trading/
  identity.py
  products.py
  instruments.py
  orders.py
  executions.py
  intents.py
  ledger.py
  market_state.py
```

命名规则：

- 用复数文件名表示模型集合，例如 `orders.py`、`products.py`、`intents.py`。
- 不放 service implementation。
- 不放 mutable account state。
- 不放 account binding、credential、entitlement、lock、recovery。
- 不放 connector payload。
- 不放 backtest-specific model。
- 不放 pricing model。
- 不放具体策略模板。

边界测试应持续保证：

```text
Trading model must not depend on:
  data
  pricing
  volatility
  products
  risk
  strategy
  execution service
  runtime
  backtest
  connectors
```

### 5.2 当前一级目录的层级问题

当前 `kairospy/` 下的一级目录不能全部视为同一层产品边界。它们混在了一起：

- **一级产品域**：用户或系统能直接感知的能力边界，例如 Data Product、Market Plane、Strategy SDK、Run Runtime、Execution、Risk。
- **二级能力包**：服务某个产品域的内部能力，例如 pricing、volatility、features、accounting、capture、validation。
- **集成/基础设施包**：ports、contracts、connectors、storage 这类边界和实现设施。
- **迁移期组合包**：application、orchestration 这类历史组合层，长期应该拆到 surface、runtime、governance。

因此，目标不是继续给每个现有一级目录找“一级定位”，而是判断它应该成为：

1. 目标一级产品目录。
2. 某个一级产品目录下的二级能力包。
3. 集成或基础设施目录。
4. 兼容 facade 或迁移期目录。

### 5.2.1 现有目录的目标层级归属

| 当前目录 | 目标层级 | 目标归属 | 原因 |
|---|---|---|---|
| `trading/` | 一级基础模型产品 | `trading/` | 交付跨所有运行模式稳定的交易事实和交易语义 |
| `data/` | 一级产品域 | `data/` | 交付 Data Product、Dataset Release、Data Binding、quality/lineage |
| `market_data/` | 一级产品域，但应改名 | `market/` | 交付运行时 Market Plane，不只是 market data 文件 |
| `reference/` | 一级产品域 | `reference/` | 交付版本化 reference catalog 和 instrument resolution |
| `workspace/` | 一级产品域 | `workspace/` | 交付用户项目上下文、data binding snapshot、strategy source metadata |
| `strategy/` | 一级产品域 | `strategy/` | 交付 Strategy SDK、Context、StrategyDecision、intent builder |
| `portfolio/` | 一级产品域 | `portfolio/` | 交付 portfolio projection、cash/position/PnL/exposure、PortfolioView |
| `risk/` | 一级产品域 | `risk/` | 交付 RiskApproval、RiskRejection、BudgetView、limits、margin |
| `execution/` | 一级产品域 | `execution/` | 交付 order lifecycle、outbox、OrderView、IntentView、recovery |
| `runtime/` | 新增一级产品域 | `runtime/` | 交付 RunKernel、RunProfile、run lifecycle、run artifact |
| `governance/` | 新增一级产品域 | `governance/` | 交付 readiness、promotion、audit、artifact、incident evidence |
| `research/` | 新增一级产品域 | `research/` | 交付 research study、hypothesis、capture、validation、report |
| `products/` | 一级能力产品 | `products/` | 交付资产/产品族规则包，例如 option exercise、funding、settlement |
| `features/` | 二级能力包 | `analytics/features/` | feature/factor 是 analytics 能力，不应该和 Data/Runtime 同级 |
| `pricing/` | 二级能力包 | `analytics/pricing/` | pricing 是 model capability，通过 valuation/view 接入 |
| `volatility/` | 二级能力包 | `analytics/volatility/` | volatility surface 是 model capability，不是一级系统产品 |
| `lifecycle/` | 二级能力包 | `products/common/lifecycle/` 或 `products/<family>/lifecycle.py` | lifecycle 是产品规则能力，不应独立成顶层 |
| `accounting/` | 二级能力包 | `portfolio/accounting/` | accounting 是 portfolio projection 的子能力 |
| `treasury/` | 二级能力包，部分拆分 | `portfolio/treasury/` + `integrations/connectors/transfer/` | cash/treasury state 属于 portfolio；外部 transfer adapter 属于 integrations |
| `capture/` | 二级能力包，部分拆分 | `research/capture/` + `data/acquisition/` | research sample capture 归 research；正式数据接入归 data |
| `validation/` | 二级能力包，部分拆分 | `research/validation/` + `governance/promotion.py` | research validity 归 research；run gate/promotion 归 governance |
| `connectors/` | 集成实现包 | `integrations/connectors/` | provider/venue adapter 是集成能力，不是策略或数据产品本体 |
| `ports/` | 集成契约包 | `integrations/ports/` | ports 是依赖倒置边界，不应和业务产品同级 |
| `contracts/` | 集成契约包，部分拆分 | `integrations/contracts/`，必要时下沉到 `trading/` 或 `market/` | provider-neutral payload 属于 integration；业务事实属于 trading/market |
| `storage/` | 基础设施包 | `infrastructure/storage/` | physical store 是基础设施，不应直接暴露给策略或产品域 |
| `application/` | 迁移期组合层 | `surface/` + `runtime/` | 用户用例入口归 surface；运行生命周期归 runtime |
| `orchestration/` | 迁移期组合层，必须拆分 | `runtime/` + `governance/` | supervisor/store/recovery 归 runtime；readiness/audit/reconciliation/incident 归 governance |

### 5.2.2 一级目录保留标准

一个目录只有满足下面条件，才应该成为目标一级目录：

- 它交付一个稳定的内部产品，而不是一组工具函数。
- 它的输入、输出、artifact、contract 可以被独立测试。
- 它有清楚的上游和下游，不需要知道整个系统。
- 它不会因为 backtest/simulation/live 的差异改变自身语义。
- 它可以被用户文档或架构图用一句话解释清楚。

按这个标准，`pricing/`、`volatility/`、`features/`、`accounting/`、`capture/`、`validation/`、`ports/`、`contracts/`、`storage/` 都不应该作为最终一级产品目录。它们更适合成为二级能力包。

### 5.3 根文件定位

| 文件 | 定位 | 建议 |
|---|---|---|
| `__init__.py` | 公共 Python API 出口 | 只暴露稳定 facade，不暴露内部模块 |
| `__main__.py` | CLI 入口 | 只做命令解析和 use-case dispatch |
| `configuration.py` | 配置基础设施 | 保持和业务规则分离 |
| `project.py` | 项目发现和项目元数据 | 不承担 workspace/run/data 业务规则 |
| `product_surface.py` | 用户产品门面 | 保持薄门面，调用 application services |
| `provider_surface.py` | provider 诊断门面 | 不直接实现 provider 能力 |
| `cli_output.py` | CLI 输出格式 | 不承载业务判断 |
| `cli_progress.py` | CLI 进度展示 | 不承载业务判断 |

### 5.4 目录收敛建议

第一阶段保持目录现状，但新增边界约束：

```text
trading -> no upper-layer dependency
strategy -> no backtest/connectors dependency
risk -> no connectors dependency
runtime/application -> depend on ports, not concrete connectors
connectors -> map provider payload to contracts/trading values only
```

目标结构下对应为：

```text
strategy -> no runtime profiles/integrations dependency
risk -> no integrations/connectors dependency
runtime -> depend on integrations/ports, not concrete connectors
integrations/connectors -> map provider payload to integrations/contracts + trading/market values only
```

第二阶段增加新目录承接职责。注意这些是迁移期承接点，不是最终完整树：

```text
runtime/
  kernel.py
  contracts.py
  profiles/
    backtest/
    simulation/
    live/

governance/
  readiness.py
  audit.py
  artifact.py
  reconciliation.py

portfolio/
  snapshot.py
  projection.py

integrations/
  ports/
  contracts/
  connectors/
```

### 5.5 从产品交付视角定义目录

目录不是代码分类，也不是团队分工的影子。每个目标一级目录或二级能力包都应该像一个内部产品一样定义：

- 它服务哪个使用者。
- 它接收什么输入。
- 它依赖哪些下游能力。
- 它交付什么稳定产物。
- 它禁止暴露什么内部细节。
- 它的验收测试是什么。

如果一个目录不能说清自己交付的产物，它就会自然膨胀成“工具箱”；如果一个目录交付多个互相独立的产物，它就应该拆边界。

推荐用下面的产品交付矩阵约束目标目录。这里的“目录”既包括一级产品目录，也包括必须明确边界的二级能力包。

| 目录 | 内部产品定位 | 允许依赖 | 交付产物 | 禁止依赖/禁止交付 |
|---|---|---|---|---|
| `surface/` | 用户入口和产品用例层 | workspace、data、runtime、integrations doctor | Python facade、CLI use case、provider/data/run surface | 不拥有核心业务规则；不直接下单；不直接处理 provider DTO |
| `workspace/` | 用户工作区产品 | project config、storage repository、data/account binding contract | Workspace snapshot、DataBinding snapshot、AccountBinding snapshot、strategy source metadata | 不保存 live order state；不保存 credential；不成为 research/run 混合空间 |
| `trading/` | 交易事实和交易语义模型产品 | Python stdlib、少量稳定基础类型 | Instrument、ProductSpec、AccountRef、Order、Fill、LedgerFact、EconomicIntent | 不依赖 data、strategy、risk、execution、runtime、integrations；不交付 service、mutable account、credential、entitlement |
| `data/` | Data Product 产品 | integrations/connectors、infrastructure/storage、reference | DatasetRelease、DataBinding、quality report、lineage、reader、live binding | 不交付策略可见行情状态；不直接驱动策略循环 |
| `data/acquisition/` | 正式数据接入能力 | integrations/connectors、data contracts、storage | acquisition job、normalized data write、source evidence | 不做 research sample 解释；不进入 Strategy Context |
| `market/` | Market Plane 运行时行情产品 | data release/live binding、canonical events、reference | MarketProjection、MarketView、MarketSnapshot、freshness/gap evidence | 不拥有 provider acquisition；不暴露 connector payload；不做 risk/strategy 决策 |
| `reference/` | Reference Data 产品 | integrations/connectors、storage、trading identity | versioned catalog、instrument resolver、contract summary、ReferenceView input | 不承载交易决策；不修改 trading identity 模型 |
| `products/` | 产品族规则包产品 | trading product spec、reference、calendar | settlement、expiry、funding、exercise、corporate action rule packs | 不定义通用 trading model；不直接下单；不写 portfolio |
| `products/common/lifecycle/` | 通用生命周期能力 | trading facts、product calendars | generic settlement/exercise/expiry/funding lifecycle events | 不作为独立一级目录；不承载 backtest 主循环 |
| `analytics/features/` | Feature/Factor 能力 | market view、reference、pricing/volatility capability | FeatureView、factor value、feature metadata、feature hash | 不直接读 provider；不内置仓位管理 |
| `analytics/pricing/` | 定价能力 | trading product spec、reference、market inputs、volatility | valuation result、greeks、pricing evidence | 不决定交易；不写 portfolio；不进入 Context as service |
| `analytics/volatility/` | 波动率能力 | option market inputs、reference、storage/cache | surface、calibration artifact、surface quality evidence | 不成为期权策略模板；只通过 valuation/FeatureView 暴露 |
| `strategy/` | Strategy SDK 产品 | trading、Context views、intent builders | Context、Strategy protocol、StrategyDecision、archetype builders | 不依赖 backtest/live/integrations；不提交订单；不写 ledger |
| `portfolio/` | 组合状态和账本投影产品 | trading ledger facts、market view、reference、account facts | PortfolioView、AccountStateView、positions、cash、margin、PnL、exposure、ledger projection | 不接收策略直接写入；不调用 connector submit；不保存 credential |
| `portfolio/accounting/` | 会计投影能力 | ledger facts、currency/reference、storage | accounting projection、cash balance view、reconciliation input | 不重复定义 ledger fact；不处理 transfer workflow |
| `portfolio/treasury/` | 资金和现金状态能力 | ledger facts、account/reference、transfer facts | treasury state、cash movement plan、transfer reconciliation view | 不实现 provider transfer API；不直接修改 portfolio projection |
| `risk/` | 风险评估和预算产品 | portfolio view、market view、reference、policy config | RiskApproval、RiskRejection、BudgetView、risk state、limit evidence | 不下单；不依赖 connector；不混入具体策略 archetype |
| `execution/` | 执行状态机产品 | trading order/fill facts、risk approval、integrations/ports、runtime store | OrderCommand、outbox、ExecutionEvent、OrderView、IntentView、recovery service | 不包含 provider SDK 细节；不使用 backtest-only fill 假设 |
| `runtime/` | Run 产品 | workspace、strategy、market、portfolio/risk、execution、governance、profiles | RunKernel、RunProfile、RunRequest、RunResult、RunArtifact、account lock lifecycle | 不实现 provider connector、pricing model、order state machine 细节；不持有 account credential |
| `runtime/profiles/backtest/` | BacktestProfile 能力 | data release、market replay、strategy、risk、deterministic fill model | BacktestResult、performance metrics、deterministic evidence | 不接入真实 execution；不承担 live recovery |
| `runtime/profiles/simulation/` | SimulationProfile 能力 | market replay/live binding、execution simulator、runtime store | simulated order lifecycle、soak/recovery evidence、simulation artifact | 不提交真实风险账户订单；不等同于 backtest |
| `runtime/profiles/live/` | LiveProfile 能力 | live market、execution gateway、durable store、governance | live runtime facts、recovery/reconciliation evidence、incident evidence | 不使用 deterministic fill；不绕过 readiness/outbox |
| `research/` | 研究产品 | data release、features、strategy config、storage | study artifact、hypothesis、label/feature definition、research report | 不直接启动 live；不保存 live order state |
| `research/capture/` | 研究样本捕获能力 | data readers、reference、storage | study snapshot、sample series、tutorial/research dataset | 不进入 live runtime path |
| `research/validation/` | 研究验证能力 | research artifacts、data release、backtest artifact | validity claim、robustness report、no-lookahead evidence | 不替代 live readiness；promotion 归 governance |
| `governance/` | 运行治理产品 | runtime evidence、research validation、connector capability、policy config | ReadinessGate、PromotionPolicy、AuditSink、RunArtifactBuilder、incident evidence | 不改变策略经济决策；不下单 |
| `integrations/ports/` | 依赖倒置端口能力 | trading/contracts 基础类型 | ExecutionGateway、MarketDataPort、ReferencePort、AccountPort | 不包含实现；不绑定具体 provider |
| `integrations/contracts/` | 外部集成契约能力 | trading/market/reference 基础类型、serialization policy | canonical envelope、provider-neutral payload contract | 不定义业务服务；不替代 trading model |
| `integrations/connectors/` | 外部系统接入能力 | provider SDK/API、transport、codec、ports/contracts | provider adapters、account/market/reference/execution capability declaration、provider diagnostics | 不暴露 provider DTO 给 Context；不拥有业务决策；不定义 PortfolioView |
| `infrastructure/storage/` | 物理存储能力 | filesystem/database/object store、codec | repository、data lake path、durable store primitive | 不暴露物理路径给 strategy；不写业务规则 |

这个矩阵有一个直接后果：`market_data/` 的名字长期并不理想。它实际交付的是 Market Plane 产品，不只是 market data 文件或 feed。长期更清晰的命名是 `market/`：

```text
kairospy/market/
  events.py
  projection.py
  snapshot.py
  view.py
  quality.py
  subscriptions.py
```

短期可以保留 `market_data/`，但文档、测试和新代码应该按 Market Plane 来定义职责，避免把它当成 Data Plane 的子目录。

### 5.6 目录正交性的判定规则

判断一个目录是否正交，不看它“现在是不是独立”，而看它是否满足下面几条：

1. **单一交付物**：一个目录最多交付一个主产品，其他对象都服务这个主产品。
2. **依赖方向稳定**：底层事实模型不依赖上层运行系统；策略不依赖 connector；connector 不依赖策略。
3. **输入输出可测试**：每个目录都能用 contract test 验证输入、输出和禁止依赖。
4. **运行模式不泄漏**：backtest/simulation/live 的差异只能进入 profile 或 profile-owned adapter，不能散落进 strategy、trading、pricing、features。
5. **业务能力可插拔**：期权、波动率、funding、settlement 可以内置，但必须通过 capability/service/view 接入，不能写死到 kernel/context。
6. **用户心智稳定**：公开文档和 examples 只展示产品级入口，不暴露迁移期内部结构。

最危险的非正交信号：

- 一个目录既定义模型，又调用 provider，又写状态机。
- 一个目录既服务 research，又服务 live runtime。
- 一个目录的输出只能被某一种 run mode 理解。
- 一个目录为了方便直接 import 另一个高层目录的内部类。
- 一个目录的名字描述实现细节，而不是它交付的产品能力。

## 6. 目标产品文件夹结构

目标结构应该体现“一级产品域 + 二级能力包”，而不是把所有现有目录平铺在 `kairospy/` 下。

```text
kairospy/
  surface/
    python_api.py
    product.py
    providers.py
    cli/

  workspace/
    project.py
    data_bindings.py
    account_bindings.py
    snapshots.py

  trading/
    identity.py
    account_ref.py
    instruments.py
    products.py
    orders.py
    executions.py
    intents.py
    ledger.py
    money.py

  data/
    products.py
    releases.py
    bindings.py
    readers.py
    quality.py
    lineage.py
    acquisition/
    publishing/

  market/
    events.py
    projection.py
    snapshot.py
    view.py
    quality.py
    replay.py
    subscriptions.py

  reference/
    catalog.py
    resolver.py
    repository.py
    sync.py
    view.py

  products/
    common/
      lifecycle/
      calendars.py
    equity/
    listed_option/
      contracts.py
      exercise.py
      settlement.py
    crypto_option/
    future/
    perpetual/

  analytics/
    features/
    pricing/
    volatility/
    valuation.py

  strategy/
    context.py
    views.py
    protocols.py
    decisions.py
    intent_builders.py
    archetypes/

  portfolio/
    projection.py
    account_state.py
    positions.py
    cash.py
    pnl.py
    exposure.py
    views.py
    accounting/
    treasury/

  risk/
    approvals.py
    budgets.py
    limits.py
    margin.py
    scenarios.py
    policies.py

  execution/
    commands.py
    outbox.py
    state_machine.py
    planner.py
    router.py
    recovery.py
    views.py

  runtime/
    contracts.py
    kernel.py
    clock.py
    store/
    supervisor.py
    profiles/
      backtest/
      simulation/
      live/

  research/
    studies.py
    artifacts.py
    capture/
    validation/
    reports/

  governance/
    readiness.py
    promotion.py
    audit.py
    artifact.py
    reconciliation.py
    incidents.py
    observability.py

  integrations/
    capabilities.py
    ports/
    contracts/
    connectors/
      binance/
      deribit/
      ibkr/
      massive/
      transfer/

  infrastructure/
    configuration.py
    storage/

  product_surface.py       # compatibility facade during migration
  provider_surface.py      # compatibility facade during migration
```

优先目标是让每个目录的产品交付物清楚，而不是让树看起来整齐。

关键收口：

- `market_data/` 最终收口到 `market/`。
- `features/`、`pricing/`、`volatility/` 收口到 `analytics/`。
- `accounting/`、`treasury/` 收口到 `portfolio/`，外部 transfer adapter 留在 `integrations/connectors/transfer/`。
- `capture/`、`validation/` 收口到 `research/`；run readiness 和 promotion 迁到 `governance/`。
- `connectors/`、`ports/`、`contracts/` 收口到 `integrations/`。
- `storage/` 收口到 `infrastructure/storage/`。
- `application/` 和 `orchestration/` 不作为最终产品域；分别拆到 `surface/`、`runtime/`、`governance/`。
- `backtest/` 不再作为独立一级系统产品；目标是 `runtime/profiles/backtest/`，旧 `kairospy/backtest` 可以作为兼容 facade 或历史实现承接点。

### 6.1 文件级定位 Inventory

目录结构不能只从当前文件名推导。已经新增单独的文件级定位清单：

```text
docs/kairospy_file_positioning_inventory.md
```

该 inventory 覆盖当前 `kairospy/` 下 300 个 Python 文件，每个文件按以下维度分析：

- 源码实际信号：top-level class/function、docstring、内部 import。
- 产品视角：它最终服务哪个内部产品或用户能力。
- 系统视角：它承担模型、服务、状态机、gateway、store、artifact、report 等哪类职责。
- 用户视角：普通用户、策略作者、研究员、运维是否直接感知。
- 目标归属：推荐迁移后的产品文件夹。
- 边界备注：是否存在 backtest leakage、connector leakage、account boundary、fat facade 等风险。

从文件级 inventory 看，当前结构的主要问题不是“目录名字不好”，而是这些实际耦合：

1. `connectors/` 有 65 个文件，是最大文件群。它们同时承担 provider transport、dataset connector、reference sync、market stream、execution gateway、account gateway、transfer gateway。目标应收口为 `integrations/connectors/`，并通过 capability/ports 暴露，不进入 strategy/runtime 内部。
2. `data/` 有 39 个文件，已经是 Data Product 形态，但 `data/feed.py`、`data/market_snapshot_*` 仍依赖 backtest 概念。目标应把 replay/event source 语义移到 `market/` 或 `runtime/profiles/backtest/`。
3. `application/` 和 `orchestration/` 的文件实际横跨 runtime、governance、execution recovery、artifact、supervisor。它们不应作为最终产品域，只能作为迁移期组合层。
4. `backtest/engine.py` 不是普通回测模块，而是当前系统组合中心。目标不是简单搬文件，而是让它成为 `runtime/profiles/backtest/` 的历史实现或 adapter。
5. `pricing/option_valuation.py`、`risk/engine.py`、`features/*` 中有明显 backtest 依赖，说明 analytics/risk/feature 还没有完全按 MarketView/PortfolioView 输入建模。
6. `trading/identity.py` 目前包含 AccountType/AccountKey。目标只允许 `AccountRef` 这种交易事实引用；账户状态、权限、绑定、锁、凭证必须落到 workspace/portfolio/runtime/integrations。
7. `risk/covered_call.py` 和 `trading/intent.py` 中的策略 archetype 仍然让核心模型随策略品类膨胀，应迁往 `strategy/archetypes/` 或 strategy intent builders。

因此，后续迁移要以文件级 inventory 为依据：

```text
current file actual responsibility
  -> product owner
  -> target package
  -> boundary test
  -> migration PR
```

不能反过来用目标目录树硬套当前文件名。

## 7. 迁移计划

### Phase 0：冻结边界测试

目标：先防止继续恶化。

工作：

- 扩展 `tests/test_architecture_boundaries.py`。
- 禁止 `trading` 依赖 strategy、risk、data、runtime、backtest、connectors。
- 禁止 `strategy` 依赖 connectors。
- 禁止 `risk` 依赖 connectors。
- 禁止 `backtest` 直接依赖 concrete connector。
- 标记 `BacktestEngine` 为 legacy composition root，不再往里面塞新职责。

验收：

- 边界测试能指出违规 import。
- 新功能必须选择明确平面落点。

### Phase 1：明确 RunKernel 和三种 Profile 契约

目标：先固定 `RunKernel + BacktestProfile / SimulationProfile / LiveProfile` 的边界。

工作：

- 新增 `kairospy/runtime/contracts.py` 或等价位置。
- 新增 `kairospy/runtime/kernel.py`、`kairospy/runtime/contracts.py` 和 `kairospy/runtime/profiles/` 的最小骨架。
- 定义统一 `RunRequest` / `RunResult`。
- 定义三种 profile：
  - `BacktestProfile`
  - `SimulationProfile`
  - `LiveProfile`
- 明确共享 evidence 字段：strategy decisions、intents、risk decisions、orders、fills/execution events、ledger facts、portfolio timeline、run artifact。
- 保持现有 `BacktestEngine` 可用，不改 CLI。

验收：

- 文档和类型上能清楚区分 kernel 统一职责与三种 profile 差异。
- 用户不需要理解内部 clock/feed/risk/gateway 组合。
- 当前 backtest 行为不变。

### Phase 2：补出 SimulationProfile 边界

目标：把“模拟盘/运行演练”从 backtest 和 live 中间明确切出来。

工作：

- 定义 `SimulationProfile` 的最小 contract。
- 定义 simulated venue adapter / simulated execution connector 的职责。
- 明确 simulation 可以使用 live market connector，但不能真实 submit。
- 明确 simulation 要保留 order ack、pending、partial fill、reject、cancel、latency 等执行生命周期。
- 不要求 SimulationProfile 一开始复用 BacktestProfile 的实现。

验收：

- backtest 和 simulation 的目标、clock、execution、store、recovery 语义清楚不同。
- simulation 的 connector 边界明确。
- 后续实现时不会把 simulation/live 逻辑塞回 backtest。

### Phase 3：收窄 Context

目标：策略依赖视图，不依赖系统内部对象。

工作：

- 新增 `MarketView`、`PortfolioView`、`FeatureView`、`ReferenceView`、`OrderView`、`IntentView`、`BudgetView`。
- 新策略协议使用窄 context。
- 将 valuation/surface/catalog 访问包进 view。

验收：

- 用户策略不再需要 import `backtest.feed.MarketSnapshot`。
- Strategy protocol 不依赖 backtest 模块或 connector 模块。
- 新策略可以同时在 backtest、simulation、live 下运行。

### Phase 4：瘦身 BacktestEngine

目标：让 BacktestEngine 仍然是独立运行引擎，但不继续承载不属于回测的职责。

工作：

- 保留当前同步确定性主循环。
- 抽出 metrics collector。
- 抽出 backtest-only fill model 和 commission model 的接口边界。
- 抽出 valuation/feature view 构造边界。
- 保持 `BacktestEngine.run()` 直接返回 `BacktestResult`，不强行改成 adapter。

验收：

- 现有 backtest 行为不变。
- `BacktestEngine` 的职责是历史评估，而不是 live runtime 内核。
- backtest 不依赖 concrete live connector。

### Phase 5：清理 Intent 模型

目标：trading intent 稳定，不随策略品类膨胀。

工作：

- 保留通用 trading intents：
  - `TargetPositionIntent`
  - `TargetExposureIntent`
  - `OpenStructureIntent`
  - `CloseStructureIntent`
  - `TransferIntent`
  - `CancelIntent`
- 将 `CoveredCallIntent`、`ProtectivePutIntent`、`CashAndCarryIntent` 移到 `strategy/archetypes.py` 或 `strategy/intent_builders.py`。
- archetype builder 输出通用 intent。

验收：

- trading intent 不包含具体策略名称。
- covered call、protective put、cash and carry 示例仍能生成同等交易意图。

### Phase 6：统一 ExecutionGateway 契约

目标：让 SimulationProfile 和 LiveProfile 共享执行事件语义；BacktestProfile 可以适配这套契约，但不强制第一阶段使用。

工作：

- 定义 `ExecutionGateway` protocol。
- 定义 `ExecutionEvent`、`OrderAck`、`OrderReject`、`Fill`、`CancelAck` 等稳定事件。
- 将 simulation venue adapter 对齐这套 gateway。
- 将 Binance/IBKR execution connector 适配为 gateway。
- `ExecutionService` 统一处理 outbox、idempotency、ack、fill、recovery。

验收：

- SimulationProfile 和 LiveProfile 不暴露 provider DTO。
- execution tests 以同一套 contract 测 simulated、Binance、IBKR adapter。

### Phase 7：治理平面收敛

目标：readiness、audit、promotion、reconciliation 不散落在业务流程里。

工作：

- 新增或收敛 `governance` 模块。
- 定义 `ReadinessGate`、`AuditSink`、`RunArtifactBuilder`、`PromotionPolicy`。
- runtime start 前执行 readiness。
- run 过程中只写 evidence，不改变业务决策。
- run 结束后生成 artifact 和 reconciliation report。

验收：

- backtest/simulation/live 都生成同结构 run artifact。
- readiness 失败不会进入策略循环。
- audit hash 可以从输入 evidence 重放计算。

## 8. 必须补充的专业系统设计

当前架构方向是专业的，但还缺少几个必须写硬的状态机和语义。下面这些不是可选优化，而是专业量化交易系统的基本骨架。

### 8.1 Time Semantics

必须明确所有事实和视图的时间语义：

| 时间字段 | 含义 | 谁产生 | 用途 |
|---|---|---|---|
| `event_time` | 市场或业务事件自身发生时间 | exchange/provider/source | 排序、回放、事件语义 |
| `exchange_time` | 交易所标记时间 | exchange | live 对齐、延迟分析 |
| `receive_time` | 系统收到事件时间 | connector/runtime | 延迟、freshness、drop 判断 |
| `available_time` | 策略合法可见时间 | Market Plane/Data Plane | 防止 lookahead bias |
| `decision_time` | 策略产生 intent 的时间 | RunKernel | audit、valid_until、因果链 |
| `submit_time` | 订单提交到 gateway 的时间 | Execution Plane | execution audit |
| `ack_time` | venue/test harness ack/reject 时间 | ExecutionGateway | order lifecycle |
| `fill_time` | 成交事实时间 | venue/fill model | ledger posting、PnL |
| `ledger_time` | 账本入账时间 | Portfolio/Risk Plane | accounting projection |
| `artifact_time` | 证据写入时间 | Governance Plane | audit/replay |

硬规则：

- 策略只能看到 `available_time <= decision_time` 的数据。
- backtest replay 必须按 `available_time` 驱动策略，而不是只按 `event_time`。
- simulation/live 必须记录 `receive_time - exchange_time` 和 `available_time - event_time`。
- MarketView 必须携带 freshness/staleness evidence。
- feature、valuation、surface 都必须继承输入数据的 `available_time`。

否则 backtest、simulation、live 很容易在“策略当时能看到什么”上语义漂移。

验收：

- 任意 `StrategyDecision` 都能追溯到当时可见的 MarketView/FeatureView。
- 任意 backtest 指标都能证明没有使用未来数据。
- 任意 live 延迟告警都能定位到 source、connector、runtime 或 strategy。

### 8.2 Order / Execution State Machine

必须明确：

```text
OrderCommand
  -> Outbox
  -> Submitted
  -> Acked | Rejected
  -> Working
  -> PartiallyFilled
  -> Filled
  -> CancelRequested
  -> CancelAcked
  -> Expired
  -> UnknownExternalState
  -> Recovered / Reconciled
```

核心对象：

| 对象 | 所属平面 | 说明 |
|---|---|---|
| `EconomicIntent` | Strategy Plane | 策略表达的经济目标 |
| `RiskApproval` / `RiskRejection` | Portfolio & Risk Plane | 风控批准或拒绝证据 |
| `OrderCommand` | Execution Plane | 可提交的订单命令 |
| `OrderRequestRecord` | Execution Plane | outbox 中的幂等请求 |
| `OrderAck` / `OrderReject` | ExecutionGateway | venue/test harness 返回 |
| `ExecutionEvent` | Execution Plane | ack、reject、cancel、status、fill 的统一事件 |
| `Fill` | Trading/Execution | 成交事实 |
| `IntentView` | Execution Plane | intent 执行进度投影 |
| `OrderView` | Execution Plane | 策略可见订单状态投影 |

硬规则：

- 所有 submit 必须先写 outbox，再调用 gateway。
- 所有 order command 必须有 idempotency key。
- live 不能绕过 outbox 直接 submit。
- partial fill 必须更新 intent progress。
- cancel request 和 cancel ack 是两个不同事件。
- unknown external state 必须 fail closed 或 reduce-only。
- recovery 只能基于 durable outbox、venue state 和 ledger facts。

验收：

- 重启后不会重复提交同一个 live order。
- venue ack 丢失时能进入 unknown external state，而不是假设成功或失败。
- fill 重放不会重复入账。
- simulation 和 live 使用同一种 execution event 语义。

### 8.3 Ledger / Portfolio / Risk Ownership

必须明确：

- `Fill`、`CashMovement`、`Settlement` 是 ledger fact。
- `PortfolioView` 是 projection。
- `RiskApproval` 是决策证据。
- `BudgetView` 是 risk/governance 输出。
- 策略不能写 ledger，也不能直接修改 portfolio。

所有权：

| 事实/视图 | Owner | 说明 |
|---|---|---|
| Ledger facts | Portfolio/Risk Plane | 成交、现金、费用、结算、转账入账 |
| PortfolioView | Portfolio/Risk Plane | 从 ledger + market 派生 |
| RiskState | Portfolio/Risk Plane | 限制、保证金、敞口、reduce-only |
| BudgetView | Portfolio/Risk/Governance | 策略可用资金和风险预算 |
| Context | Strategy Plane | 只读视图组合 |
| EconomicIntent | Strategy Plane | 策略输出 |
| RiskApproval | Portfolio/Risk Plane | intent 能否进入 execution |

硬规则：

- ledger 是事实源，portfolio 是 projection。
- 风控输入必须是不可变 view。
- 风控输出不能直接下单，只能批准/拒绝/降级。
- post-trade risk 只能通过 directive 影响后续 intent/execution。
- Context 中不能暴露 LedgerService、RiskEngine、Portfolio mutable object。

验收：

- 任何 PortfolioView 都能从 ledger facts 和 MarketView 重建。
- cash reconciliation 可以独立计算。
- 策略不能通过任何公开 API 修改账本或风险状态。

### 8.4 Promotion Gates

策略从研究到实盘至少需要：

- research evidence。
- out-of-sample。
- walk-forward。
- cost/slippage/capacity analysis。
- data quality evidence。
- backtest validity。
- simulation soak。
- live limited。
- live approved。

推荐生命周期：

```text
Draft
  -> ResearchValidated
  -> BacktestValidated
  -> RobustnessValidated
  -> SimulationApproved
  -> LiveLimited
  -> LiveApproved
  -> Suspended / Retired
```

每一关的证据：

| Gate | 必需证据 |
|---|---|
| ResearchValidated | hypothesis、input snapshot、feature definition、label definition |
| BacktestValidated | approved Dataset Release、no-lookahead evidence、cost model、risk report |
| RobustnessValidated | out-of-sample、walk-forward、parameter sensitivity、capacity analysis |
| SimulationApproved | order lifecycle soak、latency/drop evidence、recovery drill、kill switch drill |
| LiveLimited | limited capital、reconciliation pass、operator approval、incident plan |
| LiveApproved | live evidence、drawdown/risk within policy、stable operations |

硬规则：

- 不能从 research 直接 live。
- backtest 通过不代表 simulation/live 通过。
- 每次 profile 切换都必须生成新的 run artifact。
- promotion 记录必须引用具体 dataset hash、strategy hash、config hash。

验收：

- 任意 live strategy 都能追溯到 promotion evidence。
- strategy config 修改后需要重新走相关 gate。
- data release 改变后需要重新验证相关 gate。

### 8.5 Connector Capability Model

Connector 不只是 adapter，还要声明 capability：

- market data capability。
- reference capability。
- execution capability。
- account capability。
- transfer capability。
- entitlement。
- rate limit。
- heartbeat。
- reconnect。
- sequence gap。

建议 capability contract：

```python
@dataclass(frozen=True, slots=True)
class ConnectorCapability:
    connector_id: str
    provider: str
    environment: str
    market_data: tuple[str, ...]
    reference_data: tuple[str, ...]
    execution: tuple[str, ...]
    account: tuple[str, ...]
    transfer: tuple[str, ...]
    rate_limits: tuple[RateLimit, ...]
    heartbeat_policy: HeartbeatPolicy
    reconnect_policy: ReconnectPolicy
    entitlement_policy: EntitlementPolicy
```

硬规则：

- RunProfile 只能选择满足 capability 的 connector。
- live execution connector 必须声明 idempotency/recovery 支持程度。
- market data connector 必须声明 sequence、gap、stale、heartbeat 语义。
- provider DTO 不能穿透到 Context。
- entitlement 不足必须在 readiness 阶段 fail fast。

验收：

- `kairospy providers doctor` 能解释某个 profile 是否可运行。
- live 启动前能发现缺少 execution/account entitlement。
- market stream gap 能进入 degraded/reconnect 状态。

### 8.6 Operational Observability

实盘系统必须有：

- structured logs。
- metrics。
- traces。
- alert。
- incident evidence。
- runbook。
- kill switch audit。
- degradation reason。

建议事件分类：

| 类别 | 示例 |
|---|---|
| Run lifecycle | start、ready、running、degraded、reduce-only、stopped |
| Market health | stale、gap、heartbeat missed、reconnected |
| Strategy | decision、intent emitted、no-op decision |
| Risk | approved、rejected、reduce-only directive |
| Execution | outbox write、submit、ack、reject、fill、cancel |
| Ledger | posting、reconciliation difference |
| Governance | readiness pass/fail、kill switch、promotion decision |
| Incident | unknown external state、connector failure、operator stop |

硬规则：

- 所有 live submit 必须有 trace id。
- 所有 risk rejection 必须有 machine-readable reason。
- 所有 degrade/reduce-only 必须有 reason 和 source。
- kill switch 必须写 audit evidence。
- run artifact 不能只存最终结果，必须能链接关键事件。

验收：

- live 事故后能还原“当时策略看到了什么、为什么下单、风控为什么批准、订单在 venue 发生了什么”。
- operator 能区分 data fault、strategy fault、risk fault、connector fault、venue fault。

## 9. API 兼容策略

短期保持现有用户入口：

```python
from kairospy import Workspace
from kairospy.product_surface import Data
```

CLI 也保持：

```bash
kairospy data ...
kairospy workspace ...
kairospy run ...
```

内部迁移策略：

- 旧 API 调用新 service。
- 新 service 先以 internal API 形式存在。
- 文档和 examples 只展示新心智。
- 删除旧路径前先加 deprecation warning 和迁移测试。

不建议马上做的事：

- 不要一次性大规模重命名所有目录。
- 不要先改 CLI 参数再改内核。
- 不要把 `research`、`strategy` 再做成新的 workspace 概念。
- 不要让 provider connector 直接返回策略可见对象。

## 10. 优先级建议

最高优先级：

1. RunKernel 和三种 Profile 契约。
2. `SimulationProfile` 边界。
3. `Context` 收窄。
4. Time semantics。
5. Execution state machine。

中优先级：

1. BacktestEngine 瘦身。
2. Intent archetype 迁移。
3. ExecutionGateway contract。
4. Governance 平面收敛。
5. Connector capability model。

低优先级：

1. 目录重排。
2. CLI 文案统一。
3. 旧 examples 清理。

## 11. 关键验收标准

重构完成后，应该能回答这些问题：

1. 一个策略是否能不 import backtest 模块而运行 backtest？
2. 一个策略是否能不 import connector 模块而运行 simulation/live？
3. backtest、simulation、live 的目标和边界是否清楚不同？
4. simulation 和 live 是否共享 execution event 语义？
5. data release hash、strategy decision hash、intent hash、order/fill evidence 是否可独立重放？
6. trading 是否不包含具体策略 archetype？
7. product facade 是否只调用 application service，而不拥有核心规则？
8. 策略在任意 run mode 下能看到的数据是否有明确 `available_time`？
9. live 在 unknown external state 下是否 fail closed？
10. 每个 connector 是否声明 capability、rate limit、heartbeat 和 recovery policy？

如果答案都是 yes，系统就从“功能分层”进入了“正交建模”。

## 12. 推荐第一步落地任务

建议先开一个小 PR，只做运行边界：

- 新增 `kairospy/runtime/contracts.py`。
- 新增 `kairospy/runtime/kernel.py`、`kairospy/runtime/contracts.py` 和 `kairospy/runtime/profiles/` 的最小骨架。
- 定义 `RunRequest/RunResult` 和 `BacktestProfile/SimulationProfile/LiveProfile` 的最小类型。
- 为现有 `BacktestEngine` 写一层薄的 request/result 契约对齐测试。
- 不实现完整 SimulationProfile。
- 不重写 BacktestEngine。
- 不改 CLI。
- 不改用户策略 API。
- 不改 connector。

第一步的成功标准不是功能变多，而是证明：

```text
KairoSpy 的运行心智已经明确为 RunKernel + BacktestProfile / SimulationProfile / LiveProfile。
```

这个点一旦成立，后续再按真实重复点抽小服务，而不是先发明中间态大内核。

## 13. 未决边界与路径收口

本节记录当前文档中仍然需要钉死的路径。它们不是“以后再优化”的小问题，而是会决定重构是否真正正交的分叉点。

### 13.1 `RunKernel` contract 必须字段级明确

当前文档已经确定需要 `RunKernel + BacktestProfile / SimulationProfile / LiveProfile`，但还需要把 contract 写到方法和事件级别。

必须明确：

- `RunKernel` 什么时候创建 `Context`。
- `RunKernel` 如何接收 market event 和 execution event。
- `RunKernel` 是否直接写 ledger。
- `RunKernel` 如何调用 risk。
- `RunKernel` 如何写 run evidence。
- `RunKernel` 如何把 profile 差异限制在 profile adapter 内。

推荐收口：

```text
RunKernel owns:
  run identity
  run lifecycle
  context assembly
  strategy invocation
  evidence collection
  profile dispatch

RunKernel does not own:
  market projection implementation
  order state machine implementation
  connector implementation
  fill model implementation
  portfolio accounting rules
  pricing model
```

第一版 `RunProfile` contract 应该只暴露少数稳定能力：

```text
RunProfile
  prepare(request) -> PreparedRun
  market_events(prepared) -> Iterable[MarketEvent]
  execution_events(prepared) -> Iterable[ExecutionEvent]
  submit(commands) -> SubmitResult
  recover(prepared) -> RecoveryResult
  finalize(prepared) -> ProfileResult
```

如果第一版 contract 过细，系统会被迫提前抽象；如果过粗，`RunKernel` 会变成新的耦合中心。

### 13.2 `SimulationProfile` 必须从产品语义上定名

`SimulationProfile` 现在是最容易混乱的模式。它不能只是“比 backtest 更像 live”的模糊层。

必须区分：

| 形态 | 是否属于 SimulationProfile | 说明 |
|---|---|---|
| 历史事件回放 + 完整模拟订单生命周期 | 是 | 用于 runtime 演练和恢复演练，不是 performance-only backtest |
| 实时行情 + 模拟成交 | 是 | 典型 simulation/paper runtime |
| broker paper account | 可以作为 SimulationProfile adapter | 但仍不能视为真实资金账户 |
| exchange testnet | 可以作为 SimulationProfile adapter | 需要声明 capability 和环境隔离 |
| backtest deterministic fill | 否 | 属于 BacktestProfile |
| live 真实账户小资金 | 否 | 属于 LiveProfile 的 `LiveLimited` gate |

推荐定义：

```text
SimulationProfile = runtime rehearsal with non-production-risk execution.
```

也就是说，simulation 的核心不是收益评估，而是验证：

- order lifecycle。
- latency/drop/gap 处理。
- store/recovery。
- reconciliation drill。
- kill switch。
- strategy 在接近 live 的事件语义下是否稳定。

### 13.3 Data Product 到 Market Plane 的路径必须固定

当前关键关系应该固定成：

```text
integrations/connectors
  -> Data Product / Dataset Release / Live Binding
  -> Canonical Market Event
  -> Market Projection
  -> MarketView
  -> Context
```

硬规则：

- strategy 和 run loop 不直接消费 raw data product。
- Data Plane 负责数据接入、版本、质量、发布。
- Market Plane 负责运行时市场状态、freshness、staleness、gap、available_time。
- `MarketView` 必须能解释每个字段来自哪个 data binding、哪个 event window、哪个 `available_time`。

这条路径不清楚，backtest、simulation、live 会在“策略当时能看到什么”上产生不同语义。

### 13.4 `Context` 的 View 需要字段级 schema

当前 View 名称已经清楚：

- `MarketView`
- `PortfolioView`
- `FeatureView`
- `ReferenceView`
- `OrderView`
- `IntentView`
- `BudgetView`

但还需要给每个 View 写最小字段和禁止字段。否则很容易把旧的 fat context 换成新的 fat view。

推荐补充规则：

| View | 必须交付 | 明确禁止 |
|---|---|---|
| `MarketView` | 当前可见行情、universe、market quality、freshness、available_time | DataClient、DatasetRelease、connector payload |
| `PortfolioView` | cash、positions、exposure、PnL、margin summary | mutable portfolio、ledger writer、broker account client |
| `FeatureView` | feature value、model output、valuation summary、feature hash | feature recompute service、model internals、calibration service |
| `ReferenceView` | instrument/product identity、contract terms summary、calendar summary | reference sync client、provider reference DTO |
| `OrderView` | working order、pending command、last known state | submit/cancel method、outbox writer、gateway |
| `IntentView` | intent progress、remaining quantity、active/terminal status | intent state mutator、execution tracker internals |
| `BudgetView` | approved budget、risk remaining、reduce-only/blocked reason | risk approval service、limit mutator |

`Decision` 这个词应该只留给策略或系统产生的决策记录，例如 `StrategyDecision`、`RiskDecision`。策略输入视图只叫 `View`。

### 13.5 Execution 的物理归属必须钉死

执行链路应该固定为：

```text
Strategy
  -> EconomicIntent
  -> RiskApproval
  -> OrderCommand
  -> ExecutionService / Outbox
  -> ExecutionGateway
  -> ExecutionEvent
  -> OrderView / IntentView
  -> Ledger fact
```

目录归属：

- `trading/` 放 Order、Fill、ExecutionEvent 等稳定事实模型。
- `execution/` 放 outbox、order state machine、intent progress、execution service。
- `integrations/connectors/` 放 provider adapter。
- `integrations/ports/` 放 gateway protocol。
- `runtime/` 只编排，不拥有订单状态机。
- `runtime/profiles/backtest/` 可以有 deterministic fill model，但不能成为通用 execution 语义的来源。

如果这里不清楚，最危险的后果是 simulation/live 共享不了 execution evidence，实盘也无法保证幂等和恢复。

### 13.6 Portfolio、Accounting、Treasury 的路径必须收敛

当前这几个目录最容易重叠：

- `accounting/`
- `portfolio/`
- `treasury/`
- `trading/ledger.py`
- `backtest/portfolio.py`

推荐长期归属：

```text
trading/
  ledger facts only

portfolio/
  ledger projection
  portfolio view
  cash/position/PnL/exposure
  accounting/
  treasury/

portfolio/treasury/
  transfer workflow
  cash movement planning
  treasury reconciliation

portfolio/accounting/
  accounting projection
  cash balance view
  reconciliation input

runtime/profiles/backtest/
  backtest-specific portfolio adapter only
```

硬规则：

- ledger fact 类型只能有一个事实源。
- PortfolioView 必须可从 ledger facts + MarketView 重建。
- Treasury 产生 transfer/cash movement intent 或 fact，不直接改 portfolio projection。
- Backtest portfolio 只是 profile adapter，不定义通用组合模型。

### 13.7 `market_data/` 命名需要长期收口

当前 `market_data/` 名字容易让人误解为 Data Plane 的子集，但它实际交付 Market Plane。

推荐：

- 短期保留 `market_data/`，避免立即大规模迁移。
- 文档和新 contract 使用 Market Plane 术语。
- 长期迁移到 `market/`。

目标不是美化命名，而是让用户和贡献者理解：

```text
data/   = data product
market/ = runtime market plane
```

### 13.8 `runtime/profiles/backtest/` 与 `backtest/engine.py` 的关系必须明确

目标目录里可以有 `runtime/profiles/backtest/`，但它不应该复制 `BacktestEngine`。

推荐归属：

- `kairospy/backtest/engine.py` 保留历史评估实现。
- `kairospy/runtime/profiles/backtest/` 只做 `BacktestProfile` adapter。
- `BacktestEngine.run()` 短期保持用户可用。
- 新的 `RunKernel` 可以通过 adapter 调用现有 backtest 能力。

这样可以一次性明确用户心智，又不强迫第一步重写回测引擎。

### 13.9 Promotion Gates 必须绑定运行入口

Promotion 不能只是文档流程。它必须在 runtime start 前成为 gate。

推荐路径：

```text
validation/
  research/backtest/robustness evidence

governance/
  promotion policy
  readiness gate
  audit sink

runtime/
  start run only after readiness pass
```

硬规则：

- `LiveProfile` 启动前必须检查 promotion evidence。
- `SimulationProfile` 进入 soak/recovery drill 前必须检查 dataset/config/strategy hash。
- strategy config、data release、connector environment 变化后，相关 gate 必须重新评估。
- readiness 失败不能进入策略循环。

### 13.10 路径收口优先级

下一轮文档和代码落地应该按这个顺序：

1. 写硬 `RunKernel` / `RunProfile` contract。
2. 写七个 Context View 的字段级 schema。
3. 固定 Data Product -> Market Plane -> MarketView 路径。
4. 固定 ExecutionEvent / Outbox / OrderView / IntentView 归属。
5. 固定 Portfolio / Accounting / Treasury / Ledger 归属。
6. 明确 SimulationProfile 的产品语义和 adapter 范围。
7. 把目录矩阵转成 architecture boundary tests。

这几项完成后，再做目录迁移才不会产生新的中间态。
