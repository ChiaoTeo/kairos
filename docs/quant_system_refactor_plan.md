# KairoSpy 量化系统正交化重构规划

状态：Draft，final-target + migrated-state aligned
日期：2026-07-22  
适用对象：KairoSpy data、market、strategy、portfolio、risk、execution、runtime、governance、integrations 的最终目标架构

本文目标是把 KairoSpy 从“功能模块并列”推进到“正交平面组合”。专业量化系统不应该让研究、策略、回测、执行、风控、数据接入和运行治理互相嵌入。它们应该通过稳定契约组合，每个平面只拥有自己的事实、规则和生命周期。

核心原则：

```text
运行模式是系统配置，不是业务逻辑分支。
策略只产生经济意图，不直接下单、不写账本、不调用 connector。
Data 产品负责数据接入和治理，Market Plane 负责运行时行情状态。
RunKernel 统一流程骨架，BacktestProfile / SimulationProfile / LiveProfile 区分模式假设。
Connector 不建立业务 capability 领域模型，只提供端口实现、最小服务描述和 readiness evidence。
运行时具体接线由 Runtime/Profile binding 记录为 evidence；具体端口/事件源 adapter 归 `runtime/bindings.py`，live 运行组件组合归 `runtime/live_binding.py`，不回写到 connector capability 或策略 Context。
```

架构决策：不建立 `ConnectorCapabilityModel`。

原因：

- provider 是否“理论上支持”某个服务，只是 adapter metadata，不是业务事实模型。
- run 是否“现在可以启动”，是 readiness evidence，不是 connector 静态 capability。
- 本次 run “实际接了什么”，是 runtime binding evidence，不应该反向污染 connector 或 profile hash。
- 订单类型、行情订阅粒度、reference support、margin/position mode 都属于各自 owner：`execution/`、`market/`、`reference/`、`risk/portfolio`，不能再汇总成一个横跨系统的 capability graph。

因此，connector 只交付四类东西：

| 信息 | 归属 | 用途 |
|---|---|---|
| Port implementation | `integrations/ports/` + `integrations/connectors/*` | 给 runtime/execution/data/reference 调用外部系统 |
| Service/resource spec | `integrations/connectors/*` | 静态描述 provider service、resource、environment、venue、instrument family |
| Readiness evidence | `governance/readiness.py` + provider doctor | 启动前 fail fast / degraded / operator 诊断 |
| Runtime binding evidence | `runtime/kernel.py`、`runtime/bindings.py`、`runtime/live_binding.py` | 记录本次 run 实际接入的 event source、gateway、recovery、service snapshot |

只有当多个 provider 在某个具体 owner 下反复出现稳定差异时，才在该 owner 内提升强类型。例如行情订阅差异进 `market/subscriptions.py`，订单语义差异进 `execution/orders.py` 或 `execution/policy.py`，而不是新增一个全局 connector capability domain。

## 1. 当前问题判断

KairoSpy 已经有清晰的分层意图；下面这些是历史结构暴露出来的职责来源，不代表目标目录应该保留：

- `trading` 曾定义产品、账户引用、订单、成交、意图、账本等交易业务事实。
- `ports` 和 `connectors` 隔离外部 provider、venue、account、market data。
- `data` 管理 Dataset、release、quality、reader、acquisition。
- `strategy` 提供用户策略协议。
- `application` 和 `orchestration` 曾承载运行、恢复、监控、kill switch。
- `backtest` 曾作为顶层目录提供确定性回放和模拟成交。

问题不在“没有分层”，而在几个关键类和产品入口已经承担了多个平面的职责。

### 1.1 BacktestEngine 是耦合中心

旧 `kairospy/backtest/engine.py` 暴露了系统耦合中心问题。当前实现已迁到 `kairospy/runtime/profiles/backtest/engine.py`，但它仍然提示哪些职责不应该长期压在 BacktestProfile 内：

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

### 1.2 Context 曾暴露太多系统内部对象

已识别的问题是：策略上下文对象不应该暴露 working orders、catalog、valuation、surface、risk state、strategy positions、factor snapshots、intent executions 这类系统内部对象。目标架构中这个策略输入对象统一命名为 `Context`，并且只承载七个稳定 View。

当前已落地第一层公开边界：`Context` 持有 `MarketView`、`PortfolioView`、`FeatureView`、`ReferenceView`、`OrderView`、`IntentView`、`BudgetView`；策略输出语言已从旧 `trading/intent.py` 拆到 `strategy/intents.py`，策略模板意图已进入 `strategy/archetypes.py`。七个 View 现在通过 `ViewSchema` / `ViewFieldSchema` 暴露字段级 schema、time semantics、forbidden dependency、schema hash 和实例 view hash，并已完成首轮 owner-side evidence 落地。后续工作是继续把更多 owner 内部事实投影进既有 View，而不是扩宽 `Context`。

策略获得的信息越多，越容易依赖运行环境细节。专业系统里，策略应该依赖稳定的输入视图，而不是直接看到估值服务、reference repository、risk 内部状态或执行追踪细节。

### 1.3 Trading Intent 混入了策略 archetype

旧 `kairospy/trading/intent.py` 曾同时存在通用意图和业务模板意图，例如：

- `TargetPositionIntent`
- `TargetExposureIntent`
- `OpenStructureIntent`
- `CloseStructureIntent`
- `CoveredCallIntent`
- `ProtectivePutIntent`
- `CashAndCarryIntent`

前四类是基础策略意图，后几类更像策略 archetype 或 portfolio construction 模板。当前目标边界已经把通用意图放入 `strategy/intents.py`，把 `CoveredCallIntent`、`ProtectivePutIntent`、`CashAndCarryIntent` 放入 `strategy/archetypes.py`；后续还应把 archetype intent 进一步收窄为 builder 输出的通用 intent。

### 1.4 Product surface 有变厚风险

当前根层用户入口文件已经承担 application、data、capture 等多个模块的编排。如果这个入口继续承载 provider 选择、运行状态机、治理规则和业务状态转移，它会变成新的耦合层。

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
- `integrations/`：ports、connector metadata、readiness evidence、provider-neutral envelope、external gateway contracts。

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
- `CoveredCallIntent`、`ProtectivePutIntent`、`CashAndCarryIntent` 已归入 `strategy/archetypes.py`，后续可以继续收敛为 intent builder。
- 通用交易目标已进入 `strategy/intents.py`，因为它是策略输出语言。
- `AccountRef` 可以作为稳定标识存在，但不应放在一个大而泛的 `trading/` 内；目标位置是 `identity/accounts.py`。

当前 `trading/` 文件建议拆分如下：

| 当前文件 | 目标 owner | 原因 |
|---|---|---|
| `trading/identity.py` | `identity/` | 只保留 AssetId、InstrumentId、VenueId、InstitutionId、AccountRef 等稳定标识 |
| `trading/product.py` | `reference/contracts.py` + `products/specs.py` | 已删除；产品定义/合约摘要归 reference，产品族规则归 products |
| `trading/market_data.py` | `market/types.py` | 已删除；quote、trade、bar、order book 是 Market Plane 输入/状态 |
| `trading/market_state.py` | `market/state.py` / `market/projections.py` | 已删除；market state 是 Market Plane 的运行时投影，不再属于通用交易总包 |
| `trading/order.py` | `execution/orders.py` + `execution/fills.py` | 已删除；order/fill 是执行状态机事实 |
| `trading/execution.py` | `execution/events.py` + `portfolio/ledger_events.py` | 已删除；trade execution 属于 execution，funding/dividend payment 属于 portfolio ledger/product lifecycle |
| `trading/ledger.py` | `portfolio/ledger.py` | 已删除；ledger 是 portfolio/accounting 唯一事实源 |
| `trading/intent.py` | `strategy/intents.py` + `strategy/archetypes/` | intent 是策略输出语言，archetype 不应污染核心事实模型 |
| `trading/capability.py` | `market/subscriptions.py` + `execution/orders.py`/`execution/policy.py` + `reference/contracts.py` + integrations connector metadata/readiness evidence | 已删除；market data kind 归 market，order/TIF/margin/position mode 归 execution，reference support 归 reference，connector 只提供 profile 启动所需的 metadata/readiness evidence |
| `trading/corporate_action.py` | `products/equity/corporate_actions.py` | 已删除；corporate action 是 equity 产品生命周期规则 |
| `trading/derivative_event.py` | `products/common/lifecycle/derivatives.py` | 已删除；derivative lifecycle event 是产品规则事实 |
| `trading/event.py` | `market/events.py` + `integrations/events.py` + `governance/events.py` | 已删除；市场事件归 market，broker lifecycle event 归 integrations，data/operator warning 归 governance |
| `trading/strategy_contract.py` | `strategy/contracts.py` | strategy contract 是 Strategy SDK 边界 |
| `trading/__init__.py` | 删除 | 已删除；最终目标不提供 `trading` 聚合入口，公开 API 由 `surface/` 和具体产品 owner 提供 |

### 3.1.1 Account 边界

`account` 不是一个单一领域对象。专业量化系统里，账户至少要拆成几种不同概念：

| 概念 | 归属 | 说明 |
|---|---|---|
| `AccountRef` | `identity/accounts.py` | 订单、成交、ledger fact 上引用的账户维度，只是稳定标识 |
| Account binding | `workspace/` / `runtime/` | 某个 run 使用哪个 account、environment、capital scope |
| Account state | `portfolio/` | cash、positions、margin、buying power、account equity 等投影 |
| Account facts | `portfolio/ledger` | cash movement、transfer、fee、settlement、balance adjustment 等账本事实 |
| Account connector metadata/readiness | `integrations/connectors/*` service/resource spec + `integrations/ports/account.py` + governance readiness evidence | connector 是否支持 account query、margin、position sync、transfer，以及当前 account binding 是否 ready |
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
- 策略只返回 `EconomicIntent` 或一组策略 intent，由 runtime 包装成 governed intent。
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
| `OrderView` | Execution Plane | 读取本策略相关 working order、pending command、client/venue order id、command/order status 和 last_state_at/state_hash | 提交、撤单、恢复订单 |
| `IntentView` | Execution Plane | 读取 intent 进度、剩余量、active/terminal 状态、关联 command/order、last execution/state time 和 execution event count | 直接改 intent status 或 execution tracker |
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
- 三个 profile 可以复用小服务和稳定契约，但内部实现不需要强行一致。
- durable startup gate 归 `runtime/` 与 `governance/`，不承载策略循环。

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
- 收集 run evidence，输出 `RunResult`，并通过 artifact hash/ref 关联 governance `RunArtifact`。

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

`RunModeComposition` 的最终形态不是三个对等的用户可见 Engine。可以在实现内部有小的 engine/service，但产品边界应该是 owner-based chain：

| 责任链 | Owner | 输入 | 输出 | 不能拥有 |
|---|---|---|---|---|
| Market chain | `data/` + `market/` + `integrations/` | connector/source、Dataset Release、Live Binding、canonical market event | MarketProjection、MarketView、freshness/gap evidence | strategy decision、portfolio state、order lifecycle |
| Decision chain | `runtime/` + `strategy/` + `risk/` | MarketView、PortfolioView、FeatureView、ReferenceView、OrderView、IntentView、BudgetView | StrategyDecision、EconomicIntent、RiskApproval/RiskRejection | connector、outbox、ledger writer、mutable account |
| Execution chain | `execution/` + `integrations/` + `portfolio/` | approved intent、OrderCommand、ExecutionGateway event | ExecutionEvent、OrderView、IntentView、Fill、LedgerFact | strategy signal、data acquisition、risk policy mutation |

因此，`RunKernel` 只编排这三条责任链的顺序和 evidence，不把它们合并成一个大 engine；`RunProfile` 只决定三种运行模式下这些链路的具体 adapter、clock、store、gate policy。

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

当前已落地最小 RunProfile adapter：`kairospy/runtime/profiles/backtest/profile.py` 定义 `BacktestProfile` 和 `backtest_profile()`。它负责校验 dataset/strategy/config hash、backtest readiness evidence、deterministic fill model 和 artifact policy；`submit()` 不接真实 execution gateway，`recover()` 明确为不需要真实恢复。

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

当前已落地最小 profile contract：`kairospy/runtime/profiles/simulation/profile.py` 定义 `SimulationProfile`、market source、execution adapter、clock、dataset/config/strategy hash、required ports 和 readiness evidence，并已接入 `RunProfile` 的 prepare、market/execution events、submit、recover、finalize 方法。它只负责 run mode 的非真实风险执行声明，不拥有 connector SDK、真实订单提交、通用 order state machine 或 backtest deterministic fill model；未绑定模拟 gateway 时 `submit()` fail closed。

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

当前已落地最小 RunProfile adapter：`kairospy/runtime/profiles/live/profile.py` 定义 `LiveProfile` 和 `live_profile()`。它负责校验 live readiness evidence、promotion evidence、account binding、data/strategy/config hash、execution driver 和 recovery/artifact policy；未绑定真实 gateway/recovery 时 fail closed，不进入策略 runner。

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
  -> 让每个 profile 的用户语义简单
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
| `trading/product.py` | former listed option、crypto option 合约 spec | `reference/contracts.py` + `products/*/contracts.py` |
| `analytics/pricing/` | Black、implied vol、option valuation | `analytics/pricing/` |
| `analytics/volatility/` | SVI、surface、calibration | `analytics/volatility/` |
| `products/listed_option/` | listed option lifecycle | 产品业务规则包 |
| `products/crypto_option/` | crypto option settlement | 产品业务规则包 |
| `risk/option_structure.py` | option structure 风险 | risk 平面的期权扩展 |
| `risk/extensions/covered_call.py` | covered call collateral 风险扩展 | risk extension；消费 archetype-neutral request 并输出 evidence，不进入核心 risk engine |
| `capture/option_*` | option universe、snapshot、分析 | `research/capture/` |
| `analytics/features/option_skew.py` | option skew feature | `analytics/features/` |
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

因此，期权能力应该通过明确的 owner service contract 和 View 进入系统，而不是被写死在 `BacktestEngine` 或 `Context` 里。这里不需要先建立一个通用 `BusinessCapability` 模型；更稳妥的做法是让每个 owner 交付具体 contract：

```text
analytics/pricing        -> PricingService / ValuationResult
analytics/volatility     -> VolatilitySurfaceProvider / CalibrationEvidence
products/listed_option   -> OptionLifecycleRules / SettlementRule
risk option extension    -> RiskPolicyExtension / RiskEvidence
strategy/archetypes      -> IntentBuilder
```

### 4.3 期权能力最终落点

目标状态：

- `analytics/pricing/`、`analytics/volatility/`、`products/*option/` 是内置业务能力包。
- 具体期权策略意图不进入基础事实模型；`CoveredCall`、`ProtectivePut`、`CashAndCarry` 由 `strategy/archetypes/` 或 `strategy/intent_builders.py` 生成通用 intent。
- `risk/option_structure.py` 是 option structure risk helper，输入只能是 `PortfolioView`、`MarketView`、`ReferenceView` 和 valuation evidence。
- `risk/extensions/covered_call.py` 是 covered call collateral risk extension，输入是 archetype-neutral `CoveredCallCollateralRequest`、ledger/account/reference，只输出 `CoveredCallCollateralEvidence`，不 import `strategy.archetypes`。
- `research/capture/option_*` 是研究样本和分析工具，不参与 runtime kernel。
- Backtest、simulation、live 使用 option valuation 时，都通过 valuation service contract 注入，不能直接 import 具体模型。
- option pricing、vol surface、option structure risk 都通过具体 service binding 进入 runtime composition，不经过一个全局 capability registry。
- 没有配置期权 service binding 时，股票/永续/期货策略不加载任何期权模型。

## 5. 当前文件夹定位清单

本节梳理当前 `kairospy/` 下所有一级文件夹的定位、最终职责、边界风险和目标归属。`__pycache__` 是运行产物，不属于源码结构，应该忽略并避免提交。

### 5.1 当前 `trading/` 的最终拆分

当前 `kairospy/trading/` 不是一个应该被保留的目标产品目录。它把身份、产品引用、行情事实、订单成交、账本、策略意图、系统 capability 和产品生命周期混在一个名字下面，导致后续任何“交易相关”的对象都容易被放进来。

最终处理方式是按产品 owner 拆开，而不是缩小后保留：

| 当前文件 | 最终归属 | 产品定位 | 交付物 | 允许依赖 | 禁止内容 |
|---|---|---|---|---|---|
| `trading/identity.py` | `identity/` | 稳定身份产品 | AssetId、InstrumentId、VenueId、InstitutionId、AccountRef | Python stdlib、基础序列化 | mutable account、credential、permission、balance、margin |
| `trading/product.py` | `reference/contracts.py` + `products/*/contracts.py` | former reference/product contract source | 合约摘要、产品族 contract spec | identity、calendar | 已删除旧文件；pricing model、策略模板、运行状态不进入 reference contract |
| `trading/market_data.py` | `market/types.py` | former Market Plane type source | Quote、Trade、Bar、OrderBookSnapshot | identity、reference id | 已删除旧文件；provider DTO、dataset release、策略信号不进入 market type |
| `trading/market_state.py` | `market/state.py` / `market/projections.py` | former 行情状态投影来源 | MarketState、projection update | market events、reference | 已删除旧文件；portfolio/risk/execution 状态不能进入 market state |
| `trading/order.py` | `execution/orders.py` + `execution/fills.py` | 执行事实模型 | Order、OrderStatus、Fill、ExecutionInstruction | identity、reference、risk approval id | 已删除旧文件；provider SDK、outbox store 和 portfolio mutation 不进入模型 |
| `trading/execution.py` | `execution/events.py` + `portfolio/ledger_events.py` | former execution/ledger event source | TradeExecution、FundingPayment、DividendPayment | identity、execution order ids、portfolio ledger ids | 已删除旧文件；connector implementation、run loop 不进入事实模型 |
| `trading/ledger.py` | `portfolio/ledger.py` | former ledger fact source | LedgerEntry、LedgerTransaction、LedgerBook | identity、execution fill ids、product lifecycle events | 已删除旧文件；strategy write API、portfolio projection mutation 不进入 ledger 模型 |
| `trading/intent.py` | `strategy/intents.py` + `strategy/archetypes/` | 策略输出语言 | EconomicIntent、TargetPosition、TargetExposure、Open/CloseStructure | strategy contracts、identity、reference summaries | CoveredCall 等具体 archetype 直接进入基础 intent |
| `trading/capability.py` | `market/subscriptions.py` + `execution/orders.py`/`execution/policy.py` + `reference/contracts.py` + integrations connector metadata/readiness evidence | former mixed capability/enums source | MarketDataKind、OrderType、TimeInForce、MarginMode、PositionMode、ReferenceCapabilities | owner 基础类型、integrations/readiness evidence | 已删除旧文件；禁止恢复一个通用 capability 模型或业务事实模型 |
| `trading/corporate_action.py` | `products/equity/corporate_actions.py` | former equity lifecycle source | split、dividend、symbol change 等规则事件与处理服务 | reference、identity、portfolio ledger ids | 已删除旧文件；strategy signal、execution command 不进入产品生命周期模型 |
| `trading/derivative_event.py` | `products/common/lifecycle/derivatives.py` | former derivative lifecycle source | expiry、cash settlement、liquidation、ADL 等 position lifecycle event | identity、reference、portfolio account ref | 已删除旧文件；backtest 主循环、connector DTO 不进入生命周期事实 |
| `trading/event.py` | `market/events.py` + `integrations/events.py` + `governance/events.py` | former mixed event source | market event、connector lifecycle event、data/operator warning event | identity、market types、integrations contracts | 已删除旧文件；禁止恢复一个全局 Event 总线 |
| `trading/strategy_contract.py` | `strategy/contracts.py` | Strategy SDK contract | StrategySpec、StrategyLifecycle、EconomicIntent contract | strategy views、identity/reference ids | runtime profile、connector、portfolio mutable object |
| `trading/__init__.py` | 删除 | former 聚合入口 | 无 | 无 | 已删除旧文件；禁止作为聚合包继续承接新职责 |

最终边界测试应保证：

```text
identity        -> no data/runtime/strategy/execution dependency
reference       -> identity only, no pricing/runtime/strategy dependency
market          -> identity/reference/canonical events, no portfolio/risk/connector DTO dependency
strategy        -> views + intent builders, no runtime profile/connectors dependency
execution model -> identity/reference/risk approval ids, no provider SDK dependency
portfolio ledger-> execution/product lifecycle facts, no strategy direct write API
products        -> identity/reference/calendar, no strategy/runtime dependency
integrations    -> maps provider payload to owner products through ports/contracts
```

### 5.2 当前一级目录的层级问题

当前 `kairospy/` 下的一级目录不能全部视为同一层产品边界。它们混在了一起：

- **一级产品域**：用户或系统能直接感知的能力边界，例如 Data Product、Market Plane、Strategy SDK、Run Runtime、Execution、Risk。
- **二级能力包**：服务某个产品域的内部能力，例如 pricing、volatility、features、accounting、capture、validation。
- **集成/基础设施包**：ports、contracts、connectors、storage 这类边界和实现设施。
- **组合职责来源**：application、orchestration 这类历史组合层必须拆到 surface、runtime、governance、execution、integrations。

因此，目标不是继续给每个现有一级目录找“一级定位”，而是判断它应该成为：

1. 目标一级产品目录。
2. 某个一级产品目录下的二级能力包。
3. 集成或基础设施目录。
4. 删除的历史聚合目录。

### 5.2.1 现有目录的目标层级归属

| 当前目录 | 目标层级 | 目标归属 | 原因 |
|---|---|---|---|
| `trading/` | 删除的历史聚合目录 | `identity/`、`reference/`、`market/`、`execution/`、`portfolio/`、`strategy/`、`products/`、`integrations/` | 当前承载多个 owner，目标不再保留大而泛的交易总包 |
| `data/` | 一级产品域 | `data/` | 交付 Data Product、Dataset Release、Data Binding、quality/lineage |
| `market_data/` | 一级产品域，但应改名 | `market/` | 交付运行时 Market Plane，不只是 market data 文件 |
| `identity/` | 新增一级基础产品 | `identity/` | 交付跨所有产品稳定身份和引用，不承载状态或业务流程 |
| `reference/` | 一级产品域 | `reference/` | 交付版本化 reference catalog 和 instrument resolution |
| `workspace/` | 一级产品域 | `workspace/` | 交付用户项目上下文、data binding snapshot、strategy source metadata |
| `strategy/` | 一级产品域 | `strategy/` | 交付 Strategy SDK、Context、StrategyDecision、intent builder |
| `portfolio/` | 一级产品域 | `portfolio/` | 交付 portfolio projection、cash/position/PnL/exposure、PortfolioView |
| `risk/` | 一级产品域 | `risk/` | 交付 RiskApproval、RiskRejection、BudgetView、limits、margin |
| `execution/` | 一级产品域 | `execution/` | 交付 order lifecycle、outbox、OrderView、IntentView、recovery |
| `runtime/` | 新增一级产品域 | `runtime/` | 交付 RunKernel、RunProfile、RunRequest、RunResult、RuntimeRunLauncher、LiveRuntimeBindingConfig、LiveRunDaemon、run lifecycle、artifact ref/hash |
| `governance/` | 新增一级产品域 | `governance/` | 交付 readiness、promotion、audit、artifact、incident evidence |
| `research/` | 新增一级产品域 | `research/` | 交付 research study、hypothesis、capture、validation、report |
| `products/` | 一级能力产品 | `products/` | 交付资产/产品族规则包，例如 option exercise、funding、settlement |
| `analytics/features/` | 已收口的二级能力包 | `analytics/features/` | feature/factor 是 analytics 能力，不应该和 Data/Runtime 同级 |
| `analytics/pricing/` | 已收口的二级能力包 | `analytics/pricing/` | pricing 是 model capability，通过 valuation/view 接入 |
| `analytics/volatility/` | 已收口的二级能力包 | `analytics/volatility/` | volatility surface 是 model capability，不是一级系统产品 |
| `lifecycle/` | 已收口的二级能力包 | `products/common/lifecycle/` 或 `products/<family>/lifecycle.py` | lifecycle 是产品规则能力，不应独立成顶层 |
| `portfolio/accounting/` | 已收口的二级能力包 | `portfolio/accounting/` | accounting 是 portfolio projection 的子能力 |
| `portfolio/treasury/` | 已收口的二级能力包，外部 adapter 已拆分 | `portfolio/treasury/` + `integrations/connectors/transfer/` | cash/treasury state 属于 portfolio；外部 transfer adapter 属于 integrations |
| `capture/` | 已收口的二级能力包 | `research/capture/` + `data/acquisition/` | research sample capture 归 research；正式数据接入归 data |
| `validation/` | 已收口的二级能力包 | `research/validation/` + `governance/audit.py`/`governance/promotion.py` | research validity 归 research；governance audit/promotion 归 governance |
| `connectors/` | 集成实现包 | `integrations/connectors/` | provider/venue adapter 是集成能力，不是策略或数据产品本体 |
| `ports/` | 集成契约包 | `integrations/ports/` | ports 是依赖倒置边界，不应和业务产品同级 |
| `contracts/` | 集成契约包，部分拆分 | `integrations/contracts/`，业务事实下沉到 `identity/`、`reference/`、`market/`、`execution/` | provider-neutral payload 属于 integration；业务事实属于对应 owner |
| `storage/` | 已收口的基础设施包 | `infrastructure/storage/` | physical store 是基础设施，不应直接暴露给策略或产品域 |
| `application/` | 删除的组合层 | `surface/` + `runtime/` + `governance/` | 用户用例入口归 surface；运行生命周期归 runtime；artifact/readiness 归 governance |
| `orchestration/` | 删除的组合层 | `runtime/` + `governance/` + `execution/` + `integrations/` | supervisor/store/recovery 归 runtime；readiness/audit/reconciliation/incident 归 governance；order/recovery 归 execution/integrations |

### 5.2.2 一级目录保留标准

一个目录只有满足下面条件，才应该成为目标一级目录：

- 它交付一个稳定的内部产品，而不是一组工具函数。
- 它的输入、输出、artifact、contract 可以被独立测试。
- 它有清楚的上游和下游，不需要知道整个系统。
- 它不会因为 backtest/simulation/live 的差异改变自身语义。
- 它可以被用户文档或架构图用一句话解释清楚。

按这个标准，`pricing/`、`volatility/`、`features/`、`accounting/`、`treasury/`、`lifecycle/`、`capture/`、`validation/`、`ports/`、`contracts/`、`storage/` 都不应该作为最终一级产品目录。其中 `features/`、`pricing/`、`volatility/` 已收口到 `analytics/`，`accounting/`、`treasury/` 已收口到 `portfolio/`，`lifecycle/` 已收口到 `products/common/lifecycle/`，`storage/` 已收口到 `infrastructure/storage/`，`capture/`、`validation/` 已收口到 `research/` 和 `governance/`；其余仍应继续拆到对应 owner。

### 5.3 根文件定位

| 文件 | 定位 | 建议 |
|---|---|---|
| `__init__.py` | 公共 Python API 出口 | 只暴露稳定公开 API，不暴露内部模块 |
| `__main__.py` | 薄 CLI 包装 | 只保留 `python -m kairospy` 入口，实际 dispatch 在 `surface/cli/main.py` |
| `configuration.py` | 已迁移到 `infrastructure/configuration.py` | 配置基础设施；与业务规则分离 |
| `surface/cli/main.py` | CLI dispatch | 只做命令解析和 use-case dispatch |
| `surface/project.py` | 已承接旧 `project.py` | 项目初始化和渲染入口；不承担 workspace/run/data 业务规则 |
| `surface/product.py` | 当前用户产品入口来源 | 最终归入 `surface/product.py`，调用明确产品 use case |
| `surface/providers.py` | 当前 provider 诊断入口来源 | 最终归入 `surface/providers.py`，调用 integrations/data doctor |
| `surface/cli/output.py` | CLI 输出格式 | 不承载业务判断 |
| `surface/cli/progress.py` | CLI 进度展示 | 不承载业务判断 |

### 5.4 最终边界约束

目标结构必须满足这些依赖方向：

```text
identity -> no upper-layer dependency
reference -> identity only, no runtime/strategy/analytics dependency
market -> identity/reference/data event source, no portfolio/risk/execution dependency
strategy -> no backtest/connectors dependency
risk -> no connectors dependency
runtime -> depend on stable ports/readiness evidence, not concrete connectors
integrations/connectors -> map provider payload to integrations/contracts + owner product values only
integrations connector metadata/readiness -> declare minimal static support + runtime readiness evidence only
```

组合目录必须拆解成 owner：

```text
surface/
  user-facing product use cases

runtime/
  composition.py
  coordinator.py
  service_supervisor.py
  kernel.py
  contracts.py
  store/
  testing/
  profiles/
    backtest/
    simulation/
    live/

execution/
  orders.py
  fills.py
  outbox.py
  state_machine.py

governance/
  events.py
  kill_switch.py
  observability.py
  readiness.py
  audit.py
  artifact.py
  reconciliation.py
  strategy_monitoring.py

portfolio/
  ledger.py
  snapshot.py
  projections.py

integrations/
  events.py
  ports/
  contracts/
  connectors/
    resources.py
    services.py
    provider_contracts.py
```

任何不能落到上述 owner 的文件，都说明它仍然在混合系统生命周期、业务事实和用户入口。

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
| `surface/` | 用户入口和产品用例层 | workspace、data、runtime、integrations doctor | Python API、CLI use case、provider/data/run surface | 不拥有核心业务规则；不直接下单；不直接处理 provider DTO |
| `workspace/` | 用户工作区产品 | project config、storage repository、data/account binding contract | Workspace snapshot、DataBinding snapshot、AccountBinding snapshot、strategy source metadata | 不保存 live order state；不保存 credential；不成为 research/run 混合空间 |
| `identity/` | 稳定身份产品 | Python stdlib、基础序列化 | AssetId、InstrumentId、VenueId、InstitutionId、AccountRef、namespace | 不承载 mutable account、credential、balance、margin、订单状态、产品生命周期 |
| `data/` | Data Product 产品 | integrations/connectors、infrastructure/storage、reference | DatasetRelease、DataBinding、quality report、lineage、reader、live binding | 不交付策略可见行情状态；不直接驱动策略循环 |
| `data/acquisition/` | 正式数据接入能力 | integrations/connectors、data contracts、storage | acquisition job、normalized data write、source evidence | 不做 research sample 解释；不进入 Strategy Context |
| `market/` | Market Plane 运行时行情产品 | data release/live binding、canonical events、identity、reference | MarketProjection、MarketView、MarketSnapshot、freshness/gap evidence | 不拥有 provider acquisition；不暴露 connector payload；不做 risk/strategy 决策 |
| `reference/` | Reference Data 产品 | integrations/connectors、storage、identity | versioned catalog、instrument resolver、contract summary、ReferenceView input | 不承载交易决策；不修改 identity 模型 |
| `products/` | 产品族规则包产品 | identity、reference、calendar | settlement、expiry、funding、exercise、corporate action rule packs | 不定义通用身份模型；不直接下单；不写 portfolio |
| `products/common/lifecycle/` | 通用生命周期能力 | identity、reference、product calendars | generic settlement/exercise/expiry/funding lifecycle events | 不作为独立一级目录；不承载 backtest 主循环 |
| `analytics/features/` | Feature/Factor 能力 | market view、reference、pricing/volatility service contract | FeatureView、factor value、feature metadata、feature hash | 不直接读 provider；不内置仓位管理 |
| `analytics/pricing/` | 定价能力 | identity、reference、products、market inputs、volatility | valuation result、greeks、pricing evidence | 不决定交易；不写 portfolio；不进入 Context as service |
| `analytics/volatility/` | 波动率能力 | option market inputs、reference、storage/cache | surface、calibration artifact、surface quality evidence | 不成为期权策略模板；只通过 valuation/FeatureView 暴露 |
| `strategy/` | Strategy SDK 产品 | identity、reference summaries、Context views、intent builders | Context、Strategy protocol、StrategyDecision、archetype builders | 不依赖 backtest/live/integrations；不提交订单；不写 ledger |
| `portfolio/` | 组合状态和账本投影产品 | identity、execution fills/events、market view、reference、account facts | PortfolioView、AccountStateView、positions、cash、margin、PnL、exposure、ledger projection | 不接收策略直接写入；不调用 connector submit；不保存 credential |
| `portfolio/accounting/` | 会计投影能力 | ledger facts、currency/reference、storage | accounting projection、cash balance view、reconciliation input | 不重复定义 ledger fact；不处理 transfer workflow |
| `portfolio/treasury/` | 资金和现金状态能力 | ledger facts、account/reference、transfer facts | treasury state、cash movement plan、transfer reconciliation view | 不实现 provider transfer API；不直接修改 portfolio projection |
| `risk/` | 风险评估和预算产品 | portfolio view、market view、reference、policy config | RiskApproval、RiskRejection、BudgetView、risk state、limit evidence | 不下单；不依赖 connector；不混入具体策略 archetype |
| `execution/` | 执行状态机产品 | identity、reference、strategy intents、risk approval、integrations/ports、runtime store | OrderCommand、Order、Fill、outbox、ExecutionEvent、OrderView、IntentView、recovery service | 不包含 provider SDK 细节；不使用 backtest-only fill 假设 |
| `runtime/` | Run 产品 | workspace、strategy、market、portfolio/risk、execution、governance、profiles、integrations/ports | RunKernel、RunProfile、BoundRunProfile、RuntimeRunLauncher、LiveRuntimeBindingConfig、LiveRuntimeComponents、LiveRunDaemon、runtime binding adapters、RunRequest、RunResult、runtime lifecycle facts、runtime binding evidence、account lock lifecycle | 不实现 provider connector、pricing model、order state machine 细节；不持有 account credential；不持久化审计 artifact |
| `runtime/profiles/backtest/` | BacktestProfile 能力 | data release、market replay、strategy、risk、deterministic fill model | BacktestResult、performance metrics、deterministic evidence | 不接入真实 execution；不承担 live recovery |
| `runtime/profiles/simulation/` | SimulationProfile 能力 | market replay/live binding、execution simulator、runtime store | simulated order lifecycle、soak/recovery evidence、simulation artifact | 不提交真实风险账户订单；不等同于 backtest |
| `runtime/profiles/live/` | LiveProfile 能力 | live market、execution gateway、durable store、governance | live runtime facts、recovery/reconciliation evidence、incident evidence | 不使用 deterministic fill；不绕过 readiness/outbox |
| `research/` | 研究产品 | data release、features、strategy config、storage | study artifact、hypothesis、label/feature definition、research report | 不直接启动 live；不保存 live order state |
| `research/capture/` | 研究样本捕获能力 | data readers、reference、storage | study snapshot、sample series、tutorial/research dataset | 不进入 live runtime path |
| `research/validation/` | 研究验证能力 | research artifacts、data release、backtest artifact | validity claim、robustness report、no-lookahead evidence | 不替代 live readiness；audit/promotion 归 governance |
| `governance/` | 运行治理产品 | runtime evidence、research validation、connector readiness evidence、policy config | ReadinessGate、PromotionPolicy、AuditSink、RunArtifact、RunAttribution、incident evidence | 只记录治理证据，不修改策略经济决策；不下单；不实现 runtime 主循环 |
| `integrations/ports/` | 依赖倒置端口能力 | identity、reference、market、execution 基础类型 | ExecutionGateway、MarketDataPort、ReferencePort、AccountPort | 不包含实现；不绑定具体 provider |
| `integrations/contracts/` | 外部集成契约能力 | identity、market、reference、execution 基础类型、serialization policy | canonical envelope、provider-neutral payload contract | 不定义业务服务；不替代 owner 产品模型 |
| `integrations/live_ports.py` | live provider/runtime feed factory 能力 | project config、reference catalog、Data Product live view、provider connector constructors | LiveProviderPorts、LiveMarketEventSourceBinding、execution/account/order-recovery port instances、market EventSource channel | 不生成 RunProfile；不做 promotion/readiness 决策；不定义 connector capability graph |
| `integrations/connectors/` | 外部系统接入能力 | provider SDK/API、transport、codec、ports/contracts | provider adapters、connector metadata、readiness checks、provider diagnostics | 不暴露 provider DTO 给 Context；不拥有业务决策；不定义 PortfolioView；不拥有本次 run 的 binding evidence |
| `infrastructure/storage/` | 物理存储能力 | filesystem/database/object store、codec | repository、data lake path、durable store primitive | 不暴露物理路径给 strategy；不写业务规则 |

这个矩阵有一个直接后果：`market_data/` 不能作为最终目标目录名。它实际交付的是 Market Plane 产品，不只是 market data 文件或 feed。最终目录名是 `market/`：

```text
kairospy/market/
  types.py
  events.py
  source_events.py
  source_quality.py
  capture.py
  repository.py
  soak.py
  state.py
  stream.py
  projections.py
  snapshot.py
  view.py
  forward.py
  quality.py
  subscriptions.py
```

当前已落地：

- `market_data/subscriptions.py` 已删除，`MarketDataKind`、`MarketDataCapabilities`、`CapturePolicy`、`MarketDataRequirement`、`SubscriptionPlanner`、`SubscriptionReconciler` 等 subscription contract 统一由 `market/subscriptions.py` 承接。
- `market_data/stream.py` 已删除，`EventSource`、`BoundedEventChannel`、`ConflatedLatestChannel`、`IterableEventSource`、`OverflowPolicy`、`StreamClosed`、`StreamOverflow`、`ConsumerGap`、`ChannelMetrics` 等运行时事件源和 channel/backpressure 契约统一由 `market/stream.py` 承接。
- `market_data/projections.py` 已删除，`CanonicalBarSeriesProjection`、`CanonicalQuoteProjection`、`CanonicalOrderBookProjection`、`QuoteState`、`OrderBookState`、`OrderBookGap` 等 canonical event 到 Market Plane read model 的投影统一由 `market/projections.py` 承接。
- `market_data/events.py` 已删除，`MarketEventEnvelope`、`MarketEventType` 等 source event envelope 统一由 `market/source_events.py` 承接；它和 `market/events.py` 的运行时 MarketEvent 分离，避免把 repository/source payload 误暴露给策略。
- `market_data/types.py` 已删除，`RateCurve`、`RateNode`、`ForwardEstimate`、`OptionMarketObservation`、`MarketQualityIssue` 等 market input fact 统一由 `market/types.py` 承接。
- `market_data/forward.py` 已删除，`zero_rate`、`cost_of_carry_forward`、`parity_forward` 等 forward/rate helper 统一由 `market/forward.py` 承接。
- `market_data/quality.py` 已删除，`validate_option_observation`、`blocking_issues` 等 option market observation quality rule 统一由 `market/quality.py` 承接。
- `market_data/quality_gate.py` 已删除，`EventQualityReport`、`QualitySeverity`、`validate_events`、`require_publishable` 等 source event quality gate 统一由 `market/source_quality.py` 承接；其交易日历依赖已改到 `products/common/calendars.py`，避免 Market Plane 依赖 backtest。
- `market_data/capture.py`、`market_data/repository.py`、`market_data/soak.py` 和 `market_data/__init__.py` 已删除；canonical capture/replay artifact 由 `market/capture.py` 承接，source event repository 由 `market/repository.py` 承接，market stream soak/restart evidence 由 `market/soak.py` 承接。

最终公开文档、测试和新代码都按 Market Plane 定义职责，避免把它误解成 Data Plane 的子目录。

### 5.6 目录正交性的判定规则

判断一个目录是否正交，不看它“现在是不是独立”，而看它是否满足下面几条：

1. **单一交付物**：一个目录最多交付一个主产品，其他对象都服务这个主产品。
2. **依赖方向稳定**：底层事实模型不依赖上层运行系统；策略不依赖 connector；connector 不依赖策略。
3. **输入输出可测试**：每个目录都能用 contract test 验证输入、输出和禁止依赖。
4. **运行模式不泄漏**：backtest/simulation/live 的差异只能进入 profile 或 profile-owned adapter，不能散落进 strategy、identity、reference、market、analytics、portfolio、execution。
5. **业务服务可插拔**：期权、波动率、funding、settlement 可以内置，但必须通过 owner-owned service contract 和 view 接入，不能写死到 kernel/context，也不能被塞进一个泛化 capability 模型。
6. **用户心智稳定**：公开文档和 examples 只展示产品级入口，不暴露内部组合结构。

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
  __init__.py
  __main__.py

  surface/
    product.py
    providers.py
    data_features.py
    cli/
      main.py
      output.py
      progress.py

  workspace/
    project.py
    data_bindings.py
    account_bindings.py
    snapshots.py

  identity/
    assets.py
    instruments.py
    venues.py
    institutions.py
    accounts.py
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
    types.py
    events.py
    source_events.py
    source_quality.py
    capture.py
    repository.py
    soak.py
    state.py
    stream.py
    projections.py
    snapshot.py
    view.py
    forward.py
    quality.py
    replay.py
    subscriptions.py

  reference/
    contracts.py
    identity.py
    catalog.py
    resolver.py
    repository.py
    sync.py
    view.py

  products/
    common/
      lifecycle/
        derivatives.py
      calendars.py
    equity/
      corporate_actions.py
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
    intents.py
    contracts.py
    intent_builders.py
    archetypes/

  portfolio/
    ledger.py
    ledger_events.py
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
    orders.py
    fills.py
    events.py
    commands.py
    outbox.py
    state_machine.py
    planner.py
    router.py
    recovery.py
    views.py

  runtime/
    application.py
    async_runtime.py
    composition.py
    config.py
    coordinator.py
    contracts.py
    kernel.py
    clock.py
    recovery.py
    service_supervisor.py
    supervisor.py
    store/
    testing/
    profiles/
      backtest/
      simulation/
      live/
        reference_artifact.py

  research/
    studies.py
    artifacts.py
    capture/
    validation/
    reports/

  governance/
    events.py
    readiness.py
    promotion.py
    audit.py
    artifact.py
    attribution.py
    reconciliation.py
    incidents.py
    kill_switch.py
    observability.py
    strategy_monitoring.py

  integrations/
    events.py
    ports/
    contracts/
    connectors/
      resources.py
      services.py
      provider_contracts.py
      binance/
      deribit/
      ibkr/
      massive/
      transfer/

  infrastructure/
    configuration.py
    storage/
```

优先目标是让每个目录的产品交付物清楚，而不是让树看起来整齐。

关键收口：

- `market_data/` 最终收口到 `market/`。
- `trading/` 不作为最终一级目录；当前文件拆到 `identity/`、`reference/`、`market/`、`execution/`、`portfolio/`、`strategy/`、`products/`、`integrations/`。
- `features/`、`pricing/`、`volatility/` 已收口到 `analytics/features/`、`analytics/pricing/`、`analytics/volatility/`。
- `accounting/`、`treasury/` 已收口到 `portfolio/accounting/`、`portfolio/treasury/`，外部 transfer adapter 留在 `integrations/connectors/transfer/`。
- `lifecycle/` 已收口到 `products/common/lifecycle/`；通用 settlement/exercise/expiry/funding lifecycle 不能作为一级目录回流。
- `capture/`、`validation/` 已收口到 `research/`；`audit_governance` 已迁到 `governance/audit.py`，run readiness 和 promotion 归 `governance/`。
- `connectors/`、`ports/`、`contracts/` 收口到 `integrations/`。
- `storage/` 已收口到 `infrastructure/storage/`；物理路径、codec、dataset writer 不能作为一级产品目录回流。
- `application/` 和 `orchestration/` 不作为最终产品域；分别拆到 `surface/`、`runtime/`、`governance/`、`execution/`、`integrations/`。
- `backtest/` 不再作为独立一级系统产品；目标是 `runtime/profiles/backtest/`。

### 6.1 文件级定位 Inventory

目录结构不能只从当前文件名推导。已经新增单独的文件级定位清单：

```text
docs/kairospy_file_positioning_inventory.md
```

该 inventory 按当前 `kairospy/` 源码树维护文件级定位；每个文件按以下维度分析：

- 源码实际信号：top-level class/function、docstring、内部 import。
- 产品视角：它最终服务哪个内部产品或用户能力。
- 系统视角：它承担模型、服务、状态机、gateway、store、artifact、report 等哪类职责。
- 用户视角：普通用户、策略作者、研究员、运维是否直接感知。
- 目标归属：最终产品文件夹。
- 边界备注：是否存在 backtest leakage、connector leakage、account boundary、fat surface 等风险。

从文件级 inventory 看，当前结构的主要问题不是“目录名字不好”，而是这些实际耦合：

1. 旧 `connectors/`、`ports/`、`contracts/` 已收口到 `integrations/connectors/`、`integrations/ports/`、`integrations/contracts/`。这个文件群仍是最大集成边界，承担 provider transport、dataset connector、reference sync、market stream、execution gateway、account gateway、transfer gateway。边界要求不是恢复顶层包，也不是新增 connector capability domain，而是通过 ports 暴露运行能力，并用 connector metadata/readiness evidence 说明是否可用于某个 profile；不进入 strategy/runtime 内部。
2. `data/` 有 39 个文件，已经是 Data Product 形态；`data/feed.py` 负责 frozen DatasetRelease 的确定性 replay 入口，`data/market_snapshot_*` 负责 MarketSnapshot release 的物理发布/读取，但它们不再拥有通用行情快照类型。目标约束是：通用 `MarketSnapshot`、`MarketReplayDataset`、manifest 和 replay feed contract 由 `market/snapshots.py` 拥有，Data Product 只负责 catalog release、存储 driver 和发布 metadata，runtime profile 只消费已冻结 release。
3. `application/` 和旧 `orchestration/` 的文件实际横跨 runtime、governance、execution recovery、artifact、supervisor。它们不应作为最终产品域，当前已经按 owner 完成顶层路径收口：runtime lifecycle/readiness/clock/config/async/supervisor/recovery/kernel 进入 `runtime/`，BacktestProfile immediate helper 进入 `runtime/profiles/backtest/`，LiveProfile reference artifact 进入 `runtime/profiles/live/`，run artifact/attribution/failure policy 进入 `governance/`，旧 orchestration 的 coordinator/store/event log/fault drill 进入 `runtime/`，kill switch/observability/reconciliation/strategy monitoring 进入 `governance/`。最终约束是把 `runtime/kernel.py`、`runtime/composition.py`、profile adapter 和 governance artifact 的 contract 写硬，不恢复 application 聚合。
4. 旧 `backtest/engine.py` 不是普通回测模块，而是当前系统组合中心。当前已删除顶层 `kairospy/backtest/`，并把实现迁入 `runtime/profiles/backtest/`。最终拆分规则不是再保留一个独立 BacktestEngine 产品，而是把共享策略调用、Context assembly、risk/execution/ledger evidence 收敛到 `runtime/kernel.py`、`strategy/`、`risk/`、`execution/`、`portfolio/`，只把 deterministic replay/fill/result 留在 BacktestProfile。
5. `analytics/pricing/option_valuation.py`、`risk/engine.py`、`analytics/features/option_skew.py` 已切断对 BacktestProfile `MarketSnapshot` / `PortfolioSnapshot` 的直接 import，改为依赖 `market/slices.py` 的只读 `MarketSlice` contract 或 risk-owned `PortfolioRiskSnapshot` protocol；Data Product 的 historical MarketSnapshot release 物理 driver 也已改为消费 `market/snapshots.py` 的 `MarketSnapshot`、`MarketReplayDataset`、manifest 和 replay feed contract，`runtime/profiles/backtest/feed.py` 只保留 profile entrypoint/re-export，不再拥有通用 dataset 类型。
6. `identity/` 已承接 AssetId、InstrumentId、VenueId、InstitutionId、AccountRef 等稳定身份；账户状态、权限、绑定、锁、凭证必须落到 workspace/portfolio/runtime/integrations。
7. 策略 archetype 已从旧 `trading/intent.py` 移入 `strategy/archetypes.py`；`risk/covered_call.py` 已删除，covered call 抵押校验收口到 `risk/extensions/covered_call.py`，并改为消费 archetype-neutral request、输出 collateral evidence。后续策略 archetype 的进一步方向是 builder 输出通用 intent，而不是让 core risk 认识具体策略模板。

因此，最终落位要以文件级 inventory 为依据：

```text
current file actual responsibility
  -> product owner
  -> target package
  -> boundary test
  -> final placement
```

不能反过来用目标目录树硬套当前文件名。

## 7. 最终落地顺序

这里的顺序是为了降低实现风险，不代表对用户暴露中间结构。文档、examples 和公开 API 只描述最终心智。

### 7.1 架构边界测试

目标：用测试把最终 owner 关系写死。

工作：

- 扩展 `tests/test_architecture_boundaries.py`。
- 禁止 `identity/` 依赖 data、market、strategy、portfolio、execution、runtime、integrations。
- 禁止 `reference/` 依赖 strategy、risk、execution、runtime、analytics。
- 禁止 `strategy/` 依赖 runtime profiles、connectors、backtest internals。
- 禁止 `risk/` 依赖 concrete connectors。
- 禁止 `execution/` 依赖 provider SDK；只能依赖 `integrations/ports/`。
- 禁止 `runtime/` 直接 import concrete connector。
- 禁止任何 `kairospy.application` import 或顶层 `application/` 目录作为运行契约入口回流。

验收：

- 每个新增文件必须落到明确 owner。
- 边界测试能指出违规 import 和跨层泄漏。

### 7.2 文件按最终 owner 落位

目标：把当前目录从“功能平铺”改成“内部产品交付”。

工作：

- 建立 `identity/`、`market/`、`analytics/`、`runtime/`、`research/`、`governance/`、`integrations/`。
- 拆除 `trading/` 的目标职责，按 5.1 的文件级 owner 归入对应目录。
- 保持 `application/`、`orchestration/` 顶层目录删除状态；新增运行/治理/恢复/监控能力必须进入明确 owner，不能借旧聚合层回流。
- 保持 `connectors/`、`ports/`、`contracts/` 顶层目录删除状态；新增 provider 实现、port、canonical integration contract 必须进入 `integrations/`。
- 保持 `features/`、`pricing/`、`volatility/` 顶层目录删除状态；新增 feature、pricing、volatility 能力必须进入 `analytics/`。
- 保持 `accounting/`、`treasury/` 顶层目录删除状态；新增 accounting projection、treasury workflow 必须进入 `portfolio/`，transfer adapter 归入 `integrations/connectors/transfer/`。
- 保持 `lifecycle/` 顶层目录删除状态；新增通用 lifecycle 能力必须进入 `products/common/lifecycle/`，产品族特有 lifecycle 能力进入 `products/<family>/`。
- 保持 `storage/` 顶层目录删除状态；新增物理存储 primitive 必须进入 `infrastructure/storage/`，业务 repository 由对应产品 owner 包装。
- 保持 `capture/`、`validation/` 顶层目录删除状态；研究样本捕获和研究验证能力必须进入 `research/`，治理审计/晋级能力进入 `governance/`。

验收：

- 目标结构中没有顶层 `trading/`、`market_data/`、`backtest/`、`application/`、`orchestration/`、`connectors/`、`ports/`、`contracts/`、`features/`、`pricing/`、`volatility/`、`accounting/`、`treasury/`、`lifecycle/`、`storage/`、`capture/`、`validation/`。
- 任何目录都能用“依赖什么、交付什么、禁止什么”解释清楚。

### 7.3 Context 与 View schema

目标：策略只消费稳定 View，不看到系统内部服务。

工作：

- 已将策略输入对象统一定名为 `Context`。
- 已让 `Context` 只暴露 `MarketView`、`PortfolioView`、`FeatureView`、`ReferenceView`、`OrderView`、`IntentView`、`BudgetView`。
- 为每个 View 定义最小字段、禁止字段、时间语义和 hash/evidence。
- 将 valuation、surface、catalog、risk state、execution tracker 包装为 View 数据，不暴露服务对象。

验收：

- 用户策略不 import backtest、connector、runtime、repository。
- 同一策略能在 BacktestProfile、SimulationProfile、LiveProfile 下消费同一种 `Context`。
- View 中没有 submit/cancel、ledger writer、risk mutator、connector client。

### 7.4 RunKernel 与三种 Profile

目标：统一 run flow，区分三种运行模式的真实差异。

工作：

- 已在 `runtime/kernel.py` 定义 `RunStatus`、`RunRequest`、`PreparedRun`、`SubmitResult`、`RecoveryResult`、`ProfileResult`、`RunResult`、`RunProfile`、`RunKernel`。
- `RunKernel` 负责 run identity、profile dispatch、recovery/finalize 编排、strategy run evidence collection；`RunArtifact` 仍由 `governance/artifact.py` 拥有，runtime 只通过 `RunArtifactWriter` 边界接收 artifact hash/ref。`StrategyRunResult` 已记录最后一个可重建 `Context` 的 `context_view_hashes` 与 `context_hash`，并把非空 `context_hash` 纳入 strategy audit hash。
- `governance/artifact.py` 已提供 `GovernanceRunArtifactWriter`，把 `RunArtifactRepository` 绑定到 `RunKernel` 的 artifact writer protocol；run artifact 已写入 `context_view_hashes`、`context_hash` 和 `context_evidence_refs = context-view:<view>:<hash>`，并在 load 时校验 component hash 与 context hash。
- 已将 `BacktestProfile`、`SimulationProfile`、`LiveProfile` 接入 `RunProfile` contract。
- `runtime/kernel.py` 已提供 `BoundRunProfile`、`IterableRunEventProvider`、`RunCommandSubmitterBinding`、`RuntimeRecoveryBinding`，用于把本次 run 的 event source、execution gateway、runtime recovery 接到 profile 外层，并把 binding id/hash 写入 evidence。
- `runtime/bindings.py` 已提供 `EventSourceRunEventProvider`、`ExecutionPortCommandSubmitter`、`DurableOutboxCommandSubmitter`、`ExecutionRecoveryBinding`、`CompositeRecoveryBinding` 和 `ManagedServiceEvidenceProvider`，把现有 `market.stream.EventSource`、`integrations/ports` execution gateway、durable outbox dispatcher、execution recovery service 和 supervisor snapshots 包装成 runtime evidence adapter。
- `runtime/launch.py` 已提供 `RuntimeRunLauncher`，把 `KairosApplication` startup gates、`RunKernel`、service evidence provider、可选 `managed_services` supervisor lifecycle 和 artifact writer factory 组合成 paper/live run 启动用例；当调用方传入 `ManagedServiceSpec` 时，launcher 会启动 `AsyncServiceSupervisor`、在 artifact 写入前停止 services 并刷新 final service evidence；artifact repository 仍由 governance owner 注入。
- `runtime/live_config.py` 已提供 `LiveRuntimeBindingConfig` 和 `[runtime.live]` 配置解析，把 live readiness、promotion、account binding、recovery binding 转成 `BoundRunProfile` evidence；它不保存 credential，也不定义 connector capability model。
- `runtime/live_binding.py` 已提供 `LiveRuntimeComponents` 和 `bind_live_runtime_components`，把项目级 live evidence、`KairosApplication`、runtime store、reference catalog、live market event source、execution/account/order-recovery port、durable outbox dispatcher 和 recovery chain 组合成本次 run 的 `BoundRunProfile`；它只接受 `integrations/ports` 的 live 环境端口实例，不发现 provider、不持有 credential、不定义 connector capability model。
- `runtime/live_daemon.py` 已提供 `LiveRunDaemon`、`LiveRunDaemonPhase` 和 `LiveRunDaemonSnapshot`，把长驻 live run session 的 start/status/stop/recover/critical-fault 语义落在 Runtime owner：它只管理 `KairosApplication` gates、`AsyncServiceSupervisor` 服务生命周期和 runtime store evidence；不调用 connector discovery、不持有 credential、不运行策略、不写 governance artifact。
- `integrations/live_ports.py` 已提供 `LiveProviderPorts`、`LiveMarketEventSourceBinding`、`build_live_provider_ports`、`build_live_market_event_source` 和 `parse_account_ref`，把显式 provider binding 配置转成 live execution/account/order-recovery port 实例，把 Data Product Live View / provider runtime feed 转成 market EventSource channel；它不返回 runtime profile，不读取策略 Context，不做 capability discovery。
- `tests/test_run_mode_composition.py` 已覆盖 BacktestProfile、SimulationProfile、LiveProfile 三类 profile 通过同一个 `GovernanceRunArtifactWriter` 写出可解释的 `Context` evidence：`context_hash`、七个 view hash 和 `context-view:<view>:<hash>` refs 在 artifact load/explain 时保持一致。LiveProfile 即使以 fail-closed profile status 结束，只要 recovery/binding 允许进入策略循环，也必须产出同结构治理 artifact。
- 顶层 package export 不能通过 eager import 拉起下游 owner 或具体 connector。`integrations/__init__.py` 只稳定导出事件对象，并对 live port 入口做 lazy export；`infrastructure/configuration.py` 只在调用 `massive_config()` 时按需加载 Massive connector config。这个规则防止基础设施、集成和运行时包在 import 阶段形成循环依赖，也让 owner 边界可被 architecture boundary tests 验证。
- `run start --mode live` 已从一行硬编码 fail-closed 改为：要求 `--confirm-live`、`execution.live_trading_enabled = true`、`[runtime.live]` hash/evidence 匹配，然后走 `RuntimeRunLauncher`；当 `[runtime.live.provider_binding] enabled = true` 时，surface 加载 reference catalog、通过 `integrations/live_ports.py` 构建 live ports，再交给 `LiveRuntimeComponents` 注入 durable outbox、account lock 和 recovery evidence；当 `[runtime.live.market_binding] enabled = true` 时，surface 从 Data Product Live View 构建 provider runtime feed channel，并把它作为 `market_event_source` 注入 `LiveRuntimeComponents`。同步 `run start` 默认仍只记录 feed service bundle 的 created evidence，避免短生命周期 surface 命令隐式变成长驻真实行情进程；显式传入 `--supervise-live-services` 或配置 `[runtime.live.market_binding] supervise_services = true` 时，surface 才会把 `market_source.managed_services` 交给 `RuntimeRunLauncher(managed_services=...)`，由 launcher 管理启停并在治理 artifact 写入前记录 stopped evidence。长驻语义不复用 `RuntimeRunLauncher`，而由 `LiveRunDaemon` 固定 start/status/stop/recover/critical-fault contract；下一步是决定是否把该 contract 暴露成用户可见的 `run live`/daemon surface 命令。
- `BacktestProfile` 负责 historical Dataset Release、replay clock、deterministic fill model、run-local artifact。
- `SimulationProfile` 负责 replay/live market source、simulated/test execution gateway、runtime store、soak/recovery evidence。
- `LiveProfile` 负责 live market connector、real execution connector、durable store、account lock、readiness/reconciliation。

验收：

- 回测、模拟、实盘的 clock、market source、execution、persistence、recovery 语义写在 profile。
- 实际 connector/service instance 的绑定写在 `BoundRunProfile` evidence，不污染 `profile_hash`。
- `RunKernel` 不实现 fill model、connector、order state machine、portfolio accounting、pricing model。

### 7.5 Execution state machine

目标：simulation/live 共享真实订单生命周期语义，backtest 也输出同结构 evidence。

工作：

- 定义 `ExecutionGateway`、`OrderCommand`、`OutboxRecord`、`ExecutionEvent`、`OrderView`、`IntentView`。
- 所有 submit 先写 outbox，再调用 gateway。
- 所有命令有 idempotency key。
- 明确 ack、reject、working、partial fill、filled、cancel requested、cancel ack、expired、unknown external state、recovered/reconciled。
- provider adapter 只在 `integrations/connectors/`，通过 `integrations/ports/` 接入。
- `strategy/views.py` 已提供只读 execution projection：`OrderView` 暴露 working order、outbox command、last_state_at、state_hash；`IntentView` 暴露 intent progress、command_ids、order_states、last_order_update_at、last_execution_at、execution_event_count、state_hash。它们只消费 execution owner 的 durable order/outbox/execution facts，不暴露 submit/cancel、gateway、store、tracker mutator。

验收：

- 重启后不会重复提交 live order。
- partial fill 能更新 intent progress。
- simulation 和 live 的 execution event contract 一致。

### 7.6 Portfolio / Ledger / Risk ownership

目标：账本、组合投影、风险预算三者分清。

工作：

- `portfolio/ledger.py` 定义唯一 ledger fact 源。
- `portfolio/projection.py` 作为 portfolio owner 的薄投影入口，复用 `portfolio/accounting/portfolio.py` 从 ledger/accounting facts 生成的 `PortfolioSnapshot`，再结合 ledger、account state、MarketView evidence 生成 `PortfolioView`。
- `risk/` 只读 PortfolioView/MarketView/ReferenceView，输出 RiskApproval/RiskRejection/BudgetView。
- treasury、accounting 作为 portfolio 子能力；外部 transfer gateway 在 integrations。

验收：

- PortfolioView 可从 `PortfolioSnapshot` + ledger/account/market evidence 重建。
- 策略不能写 ledger 或 mutable portfolio。
- risk 输出不直接下单，只批准、拒绝或给出 reduce-only directive。

### 7.7 Governance 与 promotion

目标：研究、回测、模拟、实盘之间有可审计的 promotion gate。

工作：

- `governance/` 统一 readiness、promotion、audit、artifact、reconciliation、incidents、observability。
- Runtime start 前执行 readiness。
- 每次 profile 切换生成新的 run artifact。
- `LiveProfile` 启动前检查 strategy hash、dataset/config hash、connector metadata/readiness evidence、account binding、promotion evidence。

验收：

- backtest/simulation/live 都生成同结构 artifact。
- readiness 失败不会进入策略循环。
- 任意 live order 能追溯到 Context、StrategyDecision、RiskApproval、OrderCommand、ExecutionEvent、LedgerFact。

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

当前已落地：`MarketSnapshot` / `MarketView` 暴露可见时间和 freshness；`ValuationSnapshot`、`SurfaceSnapshot`、`FactorSnapshot` 和 research `FeatureSnapshot` 都持有继承自输入市场切片或 canonical event 的 `available_time`；`FeatureView` 会把每个 feature value 的 `available_time` 和整体 `available_time` 暴露给策略。也就是说，策略不需要知道 feature runtime、valuation service 或 surface calibration 的内部对象，也能追溯特征当时是否合法可见。

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

### 8.5 Connector Metadata 与 Readiness Evidence

这里的结论是：不需要 `ConnectorCapabilityModel`。专业系统确实需要知道某个 connector 能不能用于某个 run，但这不是新的业务领域，也不应该形成全局 capability graph、feature ontology 或可组合 capability 模型。

目标收口：

```text
需要：ports + connector metadata + readiness evidence + runtime binding evidence
不需要：ConnectorCapabilityModel / PortCapability / capability graph
```

判断标准很简单：如果一个字段只是为了启动前解释“能不能跑、为什么不能跑、出事后怎么追溯”，它属于 connector metadata 或 readiness evidence；如果它会改变订单、行情、reference、portfolio、risk 的业务语义，它必须回到对应 owner 的 contract。

这四类信息的边界不能混：

| 层 | 回答的问题 | 是否稳定产品模型 |
|---|---|---|
| Port contract | 外部系统必须实现哪些调用形状 | 是，属于 `integrations/ports/` |
| Connector metadata | 这个 provider/adapter 静态覆盖哪些服务、环境、venue、instrument family | 否，只是 adapter 描述 |
| Readiness evidence | 启动前这个账号、权限、心跳、gap、idempotency、recovery 是否 ready | 否，是治理证据 |
| Runtime binding evidence | 本次 run 实际接到了哪个 event source、gateway、recovery handler | 否，是 run evidence |

最小边界只服务三个运行问题：

1. `RunProfile` 是否能选择这个 adapter。
2. readiness gate 是否能在启动前 fail fast。
3. incident/recovery 是否有可追溯证据。

字段按用途分散到现有 owner，而不是合并成一个大模型：

| 信息 | Owner | 内容 | 不包含 |
|---|---|---|---|
| Port contract | `integrations/ports/` | market/reference/execution/account/transfer gateway protocol | provider SDK、业务规则、策略 archetype |
| Connector metadata | `integrations/connectors/*` 的 service/resource spec 或 provider contract | provider id、service kind、environment、port coverage、venues、instrument families | 订单状态机、portfolio state、risk policy |
| Readiness evidence | `governance/readiness.py` 或 provider doctor report | status、reason code、account binding、entitlement、heartbeat/gap/idempotency/recovery 检查结果 | strategy decision、provider DTO、组合投影 |
| Runtime binding evidence | `runtime/kernel.py` 的 `BoundRunProfile` 和 `runtime/bindings.py` 的 concrete adapters | binding id、market/execution provider id、gateway id、recovery handler id、service snapshot、binding hash | connector capability graph、profile hash、策略可见对象 |

owner-owned 类型仍然放回各自产品目录：

| 当前 capability 内容 | 最终 owner |
|---|---|
| `MarketDataKind`、subscription granularity | `market/subscriptions.py` |
| `OrderType`、`TimeInForce`、execution instruction enum | `execution/orders.py` / `execution/policy.py` |
| `MarginMode`、`PositionMode` | `risk/margin.py` 或 `portfolio/account_state.py`，由 execution policy 引用 |
| `ReferenceCapabilities` | `reference/contracts.py` |
| provider 是否支持某个 port | connector metadata + readiness evidence，只用于 profile selection/readiness |

这里故意不建 `PortCapability`。heartbeat、rate limit、gap、idempotency、recovery 是 readiness 检查项和 port contract 文档，不需要变成新的领域对象。只有当多个 connector 的运行差异已经反复造成实现分叉时，才把某一项提升为更强类型。

硬规则：

- RunProfile 只能选择满足 required ports 且 readiness 通过的 connector。
- BacktestProfile 不依赖 connector capability model，只需要 Data Product、Reference 和 replay input 的可用性证据。
- SimulationProfile 需要声明 market source、simulated execution adapter、paper/testnet 环境隔离和 readiness evidence。
- LiveProfile 必须检查 execution、account、recovery、entitlement、account binding、idempotency evidence。
- provider DTO 不能穿透到 Context。
- entitlement、account binding、execution recovery 不足必须在 readiness 阶段 fail fast 或进入 degraded/reduce-only。
- `BoundRunProfile` 只能描述“本次 run 接了什么”，不能声明“connector 理论上支持什么”；理论支持仍然只能作为 metadata/readiness 的输入。

验收：

- `kairospy providers doctor` 能解释某个 profile 是否可运行。
- live 启动前能发现缺少 execution/account entitlement。
- market stream gap 能进入 degraded/reconnect 状态。
- connector metadata/readiness 不进入策略 API，不成为新的业务模型层。

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

## 9. 最终公开 API

公开 API 只表达用户产品，不暴露内部目录结构。根包可以重新导出稳定入口，但内部实现必须归属到 `surface/`、`workspace/`、`data/`、`runtime/`、`strategy/` 等 owner。

Python 入口：

```python
from kairospy import Kairos, Workspace
from kairospy.strategy import Context, Strategy, StrategyDecision
from kairospy.strategy.views import (
    BudgetView,
    FeatureView,
    IntentView,
    MarketView,
    OrderView,
    PortfolioView,
    ReferenceView,
)
```

产品入口：

```python
kairos = Kairos.open()

workspace = kairos.workspace.open("my-project")
data_release = kairos.data.use("equity.ohlcv.daily", workspace=workspace)

result = kairos.run.backtest(
    workspace=workspace,
    strategy="strategies.momentum:MomentumStrategy",
    data=data_release,
)
```

CLI 入口：

```bash
kairospy workspace ...
kairospy data ...
kairospy providers ...
kairospy run backtest ...
kairospy run simulation ...
kairospy run live ...
```

公开 API 硬规则：

- `Context` 只暴露七个 View。
- Strategy API 不暴露 connector、repository、runtime profile、outbox、ledger writer。
- Provider diagnostics 通过 `surface/providers.py` 调用 integrations/data doctor。
- Run API 只选择 profile，不让用户手动拼接 MarketEngine/DecisionEngine/ExecutionEngine。
- examples 只展示最终产品路径：workspace、data、strategy、run、providers。

## 10. 优先级建议

最高优先级：

1. 最终目录 owner 和边界测试。
2. `trading/`、`market_data/`、`backtest/`、`application/`、`orchestration/` 的目标拆分。
3. RunKernel 和三种 Profile 契约。
4. 七个 `Context` View 的字段级 schema 与 owner-side evidence 已完成首轮落地，后续只允许在既有 View 内增加 owner 投影字段，不能扩宽 `Context`。
5. Time semantics 和 Execution state machine。

中优先级：

1. Intent archetype 归入 `strategy/archetypes/`。
2. Portfolio/Ledger/Risk ownership 收敛。
3. ExecutionGateway contract。
4. Governance 平面收敛。
5. Connector metadata/readiness evidence 边界，不建立 connector capability model。

低优先级：

1. CLI 文案细节。
2. 非核心 examples 排版。
3. 内部 helper 文件命名统一。

## 11. 关键验收标准

重构完成后，应该能回答这些问题：

1. 一个策略是否能不 import backtest 模块而运行 backtest？
2. 一个策略是否能不 import connector 模块而运行 simulation/live？
3. backtest、simulation、live 的目标和边界是否清楚不同？
4. simulation 和 live 是否共享 execution event 语义？
5. data release hash、strategy decision hash、intent hash、order/fill evidence 是否可独立重放？
6. 顶层 `trading/` 是否已经拆到明确 owner，而不是继续承接新职责？
7. 公开 API 是否只调用产品 use case，而不拥有核心规则？
8. 策略在任意 run mode 下能看到的数据是否有明确 `available_time`？
9. live 在 unknown external state 下是否 fail closed？
10. 每个 live/simulation connector 是否能通过 metadata/readiness evidence 解释 rate limit、heartbeat、entitlement、account binding 和 recovery 检查结果？

如果答案都是 yes，系统就从“功能分层”进入了“正交建模”。

## 12. 目标落地验收切片

第一个可验收切片应该同时覆盖目录 owner、Context、RunKernel 和 BacktestProfile，而不是只新增空目录。

必须交付：

- 最终目录 owner 的 architecture boundary tests。
- `identity/`、`market/`、`analytics/`、`runtime/`、`governance/`、`integrations/` 的目标骨架。
- `runtime/composition.py` 承接 run mode composition 与 feed/execution/strategy service plan；`runtime/service_supervisor.py` 承接 async service lifecycle。
- `Context` 与七个 View 的字段级 schema、schema hash、view hash。
- `RunRequest`、`RunResult`、`RunProfile`、`RunKernel` contract；`RunArtifact` 由 governance owner 持有，runtime 通过 artifact hash/ref 关联。
- `BacktestProfile`、`SimulationProfile`、`LiveProfile` 的模式语义和最小 connector metadata/readiness evidence。
- `ExecutionGateway`、`OrderCommand`、`OutboxRecord`、`ExecutionEvent`、`OrderView`、`IntentView` 的状态机 contract。
- `portfolio/ledger.py`、`portfolio/projection.py`、`risk/approvals.py` 的 owner 关系。

验收标准：

```text
KairoSpy 的用户心智已经明确为：
workspace -> data product -> market plane -> Context -> intent -> risk -> execution -> ledger -> artifact

运行心智已经明确为：
RunKernel + BacktestProfile / SimulationProfile / LiveProfile

目录心智已经明确为：
owner-based product packages, no top-level trading/backtest/market_data/application/orchestration products
```

## 13. 未决边界与路径收口

本节记录当前文档中仍然需要钉死的路径。它们不是“以后再优化”的小问题，而是会决定重构是否真正正交的分叉点。

### 13.1 `RunKernel` contract 必须字段级明确

当前已在 `runtime/kernel.py` 落地最小字段级 contract：`RunRequest` 固定 run identity、workspace/data/strategy/config hash；`PreparedRun` 固定 profile 提供的 market source、execution driver、store/recovery/artifact policy；`RunProfile` 固定 prepare、market/execution event source、submit、recover、finalize 方法；`BoundRunProfile` 固定本次 run 的 runtime binding evidence；`RunKernel` 固定 profile dispatch、strategy runner 调用、recovery/finalize、evidence hash 和 artifact ref 连接。

具体 BacktestProfile / SimulationProfile / LiveProfile 的最小 adapter 已接到这个 contract 上；artifact writer 已通过 runtime protocol + governance adapter 绑定；runtime binding 已通过 `BoundRunProfile`、`IterableRunEventProvider`、`RunCommandSubmitterBinding`、`RuntimeRecoveryBinding` 接入；`EventSourceRunEventProvider`、`ExecutionPortCommandSubmitter`、`DurableOutboxCommandSubmitter`、`ExecutionRecoveryBinding`、`CompositeRecoveryBinding` 和 `ManagedServiceEvidenceProvider` 已把具体 event source、execution port、durable outbox、order recovery、live recovery 组合和长生命周期 service evidence 接到 binding adapter；`RuntimeRunLauncher` 已把 application startup gate、service evidence、可选 `managed_services` supervisor lifecycle 和 artifact writer factory 接入 run 启动用例，managed services 会在 artifact 写入前停止并刷新 final evidence；`runtime/live_config.py` 已把 `[runtime.live]` 的 readiness/promotion/account/recovery evidence 转成 `BoundRunProfile`；`runtime/live_binding.py` 已把 live 运行组件组合成 market event provider、durable outbox command submitter 和 recovery chain；`runtime/live_daemon.py` 已把长驻 live session 的 start/status/stop/recover/critical-fault evidence 固定到 runtime store；`integrations/live_ports.py` 已把显式 provider binding 转成 live execution/account/order-recovery port 实例，并把 Data Product Live View / provider runtime feed 转成 live market EventSource channel；`run start --mode paper` 和配置化的 `run start --mode live` 都已迁到这个 launch use case 并写 governance artifact evidence；`run start --mode live --supervise-live-services` 或 `[runtime.live.market_binding] supervise_services = true` 已把 Data Product live view 的 `ManagedServiceSpec` bundle 接到 `RuntimeRunLauncher(managed_services=...)`；`GovernedStrategyRunLoop` 已通过测试证明 profile strategy loop 能产出可重建的 final Context view hashes，并由 governance artifact 持久化为 cross-view evidence refs；BacktestProfile / SimulationProfile / LiveProfile 已通过 profile-specific reconstruction tests 证明同一 artifact explain contract 可以跨模式还原 `Context` evidence。下一步不是扩张 connector capability model，而是决定长驻 live daemon 是否暴露为独立 surface 命令，以及它和策略 run loop 的长期调度关系。

这个 contract 目前明确了：

- `RunKernel` 通过 `strategy_runner(prepared)` 进入策略循环；具体 `Context` assembly 由该 runner 或后续 profile adapter 提供，不由 profile 内部私有化。
- `RunKernel` 通过 `RunProfile.market_events(prepared)` 和 `RunProfile.execution_events(prepared)` 接收 profile 侧事件源。
- `BoundRunProfile` 可以替换本次 run 的 market event provider、execution event provider、command submitter 和 recovery handler，并把 binding id/hash 写入 `PreparedRun.evidence`。
- `runtime/bindings.py` 只能做端口/服务到 run binding 的薄适配：`EventSourceRunEventProvider` 收集有限 async event source，`ExecutionPortCommandSubmitter` 把 `OrderCommand` / `OrderRequest` / `ComboOrderRequest` 路由到 execution port，`DurableOutboxCommandSubmitter` 把 live submit 先写入 durable outbox 再交给 dispatcher，`ExecutionRecoveryBinding` 映射 order recovery report，`CompositeRecoveryBinding` 组合 live 恢复链路，`ManagedServiceEvidenceProvider` 记录 supervisor snapshot。
- `runtime/live_binding.py` 只能做 live run 的组件装配：校验 execution/account/order-recovery gateway 都是 live environment，把 market event source、durable outbox、execution router、account lock、runtime recovery 和 order recovery 接到 `BoundRunProfile`；它不是 connector registry，也不是 capability discovery。
- `RunKernel` 不直接写 ledger，也不实现 order state machine。
- `RunKernel` 不直接拥有 risk engine；risk 应在 strategy runner / execution chain 中以 evidence 形式回填。
- `RunKernel` 通过 `RunResult.evidence_hash`、`strategy_run_hash`、`recovery_hash`、`profile_result_hash`、`artifact_hash/ref` 写 run evidence 边界；artifact 持久化由注入的 `RunArtifactWriter` 完成。非空 Context evidence 会进入 `StrategyRunResult.context_hash/context_view_hashes`，并参与 strategy audit hash 与 governance artifact 校验。
- `RunKernel` 只依赖 `RunProfile` 方法，把 profile 差异限制在 profile adapter 内。
- `RuntimeRunLauncher` 只负责启动门禁、可选 managed service lifecycle 和 artifact evidence 接线：`KairosApplication.start/run` 通过后启动传入的 `ManagedServiceSpec`，再调用 `RunKernel.run`，并在 artifact writer 执行前停止 services、刷新 `launch_evidence["services"]`，通过 `artifact_writer_factory(launch_evidence)` 把 runtime/service evidence 注入 governance artifact。
- `LiveRunDaemon` 只负责长驻 live session 生命周期：start/recover 会重新创建 `AsyncServiceSupervisor` 并通过 `KairosApplication` gates 进入 running，status/stop 会把 service snapshots 和 application status 写入 runtime store，critical managed service fault 会把 application 降级到 reduce-only 并持久化 daemon evidence。
- `LiveRuntimeBindingConfig` 只负责读取项目级 live evidence：`data_binding_hash`、`strategy_hash`、`config_hash` 必须和本次 run 匹配；promotion/readiness/recovery evidence 必须先通过 profile prepare/recovery gate，缺失或不匹配时不能进入策略循环。

推荐收口：

```text
RunKernel owns:
  run identity
  run lifecycle
  context assembly boundary
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

最终 `RunProfile` contract 应该只暴露少数稳定能力：

```text
RunProfile
  prepare(request) -> PreparedRun
  market_events(prepared) -> Iterable[MarketEvent]
  execution_events(prepared) -> Iterable[ExecutionEvent]
  submit(commands) -> SubmitResult
  recover(prepared) -> RecoveryResult
  finalize(prepared) -> ProfileResult
```

如果 contract 过细，系统会被迫提前抽象；如果过粗，`RunKernel` 会变成新的耦合中心。

### 13.2 `SimulationProfile` 必须从产品语义上定名

`SimulationProfile` 现在是最容易混乱的模式。它不能只是“比 backtest 更像 live”的模糊层。

必须区分：

| 形态 | 是否属于 SimulationProfile | 说明 |
|---|---|---|
| 历史事件回放 + 完整模拟订单生命周期 | 是 | 用于 runtime 演练和恢复演练，不是 performance-only backtest |
| 实时行情 + 模拟成交 | 是 | 典型 simulation/paper runtime |
| broker paper account | 可以作为 SimulationProfile adapter | 但仍不能视为真实资金账户 |
| exchange testnet | 可以作为 SimulationProfile adapter | 需要声明环境隔离、账号范围和 readiness evidence |
| backtest deterministic fill | 否 | 属于 BacktestProfile |
| live 真实账户小资金 | 否 | 属于 LiveProfile 的 `LiveLimited` gate |

推荐定义：

```text
SimulationProfile = runtime rehearsal with non-production-risk execution.
```

当前代码边界应按这个定义收口：`historical_replay_simulation_profile()` 表达历史/录制事件驱动的 runtime rehearsal；`paper_simulation_profile()` 表达 broker paper account；`exchange_testnet_simulation_profile()` 表达 exchange testnet。三者都必须声明 dataset/config/strategy hash、required ports 和 readiness evidence，且不能把 `RunMode.LIVE` 包进 SimulationProfile。

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

当前已落地的最小证据链：

- `market/snapshots.py` 的 `MarketSnapshot` 持有 `data_binding`、`event_window`、`available_time`、`freshness_seconds`。
- Data Product historical release 通过 `ReplaySnapshotFeed` / `MarketSnapshotReplayFeed` 进入 Market Plane；当 snapshot 未显式声明 data binding 时，replay feed 会把 frozen dataset manifest id 注入为 `data_binding`。
- `runtime/kernel.py` 的 `CanonicalBarMarketProjection` 从 canonical bar event 生成 MarketSnapshot 时，保留 source instance、bar event window、available time 和 freshness。
- `strategy/views.py` 的 `MarketView.from_snapshot()` 只暴露这些稳定可解释字段，不暴露 DatasetRelease、DataClient 或 connector payload。

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

当前已在 `strategy/views.py` 落地最小字段级 contract：每个 View 的 dataclass 字段都必须出现在对应 `ViewSchema` 中，每个字段必须声明 time semantics 和 evidence，每个 View 都有 stable `view_hash`。否则很容易把旧的 fat context 换成新的 fat view。

推荐补充规则：

| View | 必须交付 | 明确禁止 |
|---|---|---|
| `MarketView` | 当前可见行情、universe、market quality、freshness、available_time | DataClient、DatasetRelease、connector payload |
| `PortfolioView` | reporting_asset、accounts、balances、cash、positions、equity、valuation status、unpriced assets/positions、ledger/account/market evidence、state_hash | mutable portfolio、ledger writer、broker account client |
| `FeatureView` | feature value、model output、valuation summary、feature hash | feature recompute service、model internals、calibration service |
| `ReferenceView` | instrument/product/listing/route identity、product type distribution、contract summary、reference version window、integrity evidence、catalog_hash | reference sync client、provider reference DTO |
| `OrderView` | working order、pending command、client/venue order id、order status、command status、last_state_at、state_hash | submit/cancel method、outbox writer、gateway |
| `IntentView` | intent progress、remaining quantity、active/terminal status、command_ids、order_states、last_order_update_at、last_execution_at、execution_event_count、state_hash | intent state mutator、execution tracker internals |
| `BudgetView` | approved budget、risk remaining、risk/allocation decision counts、risk/limit/governance hashes、reduce-only/blocked reason、state_hash | risk approval service、limit mutator |

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

- `execution/` 放 Order、Fill、ExecutionEvent、outbox、order state machine、intent progress、execution service。
- `strategy/` 放 EconomicIntent 和 intent builder。
- `portfolio/` 放 ledger fact 和 portfolio projection。
- `identity/` / `reference/` 放订单、成交和组合事实引用的稳定标识。
- `integrations/connectors/` 放 provider adapter。
- `integrations/ports/` 放 gateway protocol。
- `runtime/` 只编排，不拥有订单状态机。
- `runtime/profiles/backtest/` 可以有 deterministic fill model，但不能成为通用 execution 语义的来源。

当前已落地的最小证据链：

- `execution/command.py` 的 `OrderCommand` / `OutboxRecord` 是命令幂等和状态恢复事实。
- `execution/order_state.py` 的 `DurableOrderRecord` 是 durable order lifecycle 事实。
- `execution/events.py` 的 execution/trade event 是成交和执行回报事实。
- `strategy/views.py` 的 `OrderView.from_execution_state()` 将 durable order + outbox command 投影成策略只读订单状态。
- `strategy/views.py` 的 `IntentView.from_executions()` 将 tracker progress + durable order/outbox/execution record 合并成策略只读 intent 进度。

如果这里不清楚，最危险的后果是 simulation/live 共享不了 execution evidence，实盘也无法保证幂等和恢复。

### 13.6 Portfolio、Accounting、Treasury 的路径必须收敛

历史上这几个来源最容易重叠：

- `accounting/`
- `portfolio/`
- `treasury/`
- former `trading/ledger.py`
- `backtest/portfolio.py`

最终归属：

```text
portfolio/
  ledger.py
  ledger_events.py
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
- PortfolioView 必须可从 ledger facts + account state + MarketView evidence 重建；当前 `portfolio/projection.py` 通过 `portfolio_view_from_snapshot()` 固定这个入口。
- Treasury 产生 transfer/cash movement intent 或 fact，不直接改 portfolio projection。
- Backtest portfolio 只是 profile adapter，不定义通用组合模型。

### 13.7 `market_data/` 命名最终收口

当前 `market_data/` 名字容易让人误解为 Data Plane 的子集，但它实际交付 Market Plane。

最终结论：

- 目标目录是 `market/`。
- `data/` 只负责 Data Product、Dataset Release、Live Binding、quality、lineage。
- `market/` 负责运行时行情事件、投影、freshness、gap、staleness、MarketView。
- connectors 输出 raw/provider payload 后，必须先进入 data product 或 canonical market event contract，再进入 Market Plane。

目标不是美化命名，而是让用户和贡献者理解：

```text
data/   = data product
market/ = runtime market plane
```

当前已落地：顶层 `kairospy/market_data/` Python 包已删除；原有 source event、source quality、subscription、stream、projection、market input、forward、quality、capture、repository、soak 能力已分别进入 `kairospy/market/` 的 owner 模块。

### 13.8 `runtime/profiles/backtest/` 与 `backtest/engine.py` 的关系必须明确

目标目录是 `runtime/profiles/backtest/`，顶层 `backtest/` 不作为最终产品目录。

当前已落地：顶层 `kairospy/backtest/` Python 包已删除；原有 `clock.py`、`engine.py`、`execution.py`、`feed.py`、`fill.py`、`maker.py`、`metrics.py`、`portfolio.py`、`repository.py`、`result.py`、`settlement.py`、`synthetic_scenarios.py` 已迁入 `kairospy/runtime/profiles/backtest/`。所有代码 import 已切到 `kairospy.runtime.profiles.backtest.*`，边界测试禁止旧 `kairospy.backtest` import 回流。

推荐归属：

- `runtime/profiles/backtest/clock.py` 拥有 replay clock；`market/snapshots.py` 拥有通用 historical `MarketSnapshot`、`MarketReplayDataset`、manifest 和 replay feed contract；`runtime/profiles/backtest/feed.py` 只是 BacktestProfile 的薄入口/re-export，不能重新长成 dataset owner。
- `runtime/profiles/backtest/fill.py` 拥有 deterministic fill model。
- `runtime/profiles/backtest/metrics.py` 拥有 backtest-specific performance metrics。
- `runtime/profiles/backtest/result.py` 拥有 BacktestResult。
- 共享策略调用、Context assembly、risk/execution/ledger evidence 归 `runtime/kernel.py`、`strategy/`、`risk/`、`execution/`、`portfolio/`。

这样用户看到的不是一个独立 `BacktestEngine` 产品，而是 `RunKernel` 下的 BacktestProfile。

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

1. 固定最终目录 owner，并把目录矩阵转成 architecture boundary tests。
2. 保持顶层 `trading/`、`market_data/`、`backtest/`、`application/`、`orchestration/`、`connectors/`、`ports/`、`contracts/`、`features/`、`pricing/`、`volatility/`、`accounting/`、`treasury/`、`lifecycle/`、`storage/`、`capture/`、`validation/` 删除状态，并用边界测试禁止旧 import 回流；当前核心包由 architecture boundary tests 约束，用户可见 examples/notebook 由 repository hygiene test 扫描，manual order / realtime / reference connector 示例已迁到新 owner import。
3. `RunKernel` / `RunProfile` contract、三种最小 profile adapter、artifact writer boundary、runtime binding boundary、event source binding、execution port command submitter、durable outbox command submitter、execution recovery binding、composite live recovery binding、长生命周期 service evidence、RuntimeRunLauncher 启动用例、managed service supervisor lifecycle、artifact evidence factory、profile-specific Context reconstruction tests、`LiveRunDaemon` 长驻 session contract、`run start --mode paper` surface 接入、`[runtime.live]` 配置证据读取、`LiveRuntimeComponents` live 组件装配、`integrations/live_ports.py` provider port / market EventSource factory、`[runtime.live.provider_binding]` 到 `LiveRuntimeComponents` 的 live execution/account/order-recovery 注入、`[runtime.live.market_binding]` 到 `LiveRuntimeComponents` 的 Data Product live view market source 注入、`run start --mode live` 启动门禁接入和显式 market service supervision 已落地；下一步决定长驻 live daemon 是否暴露为独立 `run live`/daemon surface 命令。
4. 七个 Context View 的字段级 schema 已落地：`MarketView`、`PortfolioView`、`FeatureView`、`ReferenceView`、`OrderView`、`IntentView`、`BudgetView`；`MarketView` 已补齐 data binding、event window、available_time/freshness 证据，`FeatureView` 已补齐继承自 feature/valuation/surface 输入的 available_time 证据，`PortfolioView` 已补齐 ledger/account/market projection evidence，`ReferenceView` 已补齐 point-in-time catalog identity、contract summary、version window、integrity evidence，`BudgetView` 已补齐 risk/allocation decision、risk limits、runtime/governance 状态 evidence，`OrderView`/`IntentView` 已补齐 durable order、outbox command、execution record 的只读 evidence；run artifact 已持久化 `context_view_hashes`、`context_hash` 和 cross-view evidence refs，并已证明 BacktestProfile、SimulationProfile、LiveProfile 都能通过同一 artifact explain contract 还原这些 evidence。下一步是在真实 profile runner 中继续接入更细粒度的 owner evidence，而不是扩展新的 Context 字段。
5. 固定 Data Product -> Market Plane -> MarketView 路径，明确数据产品接入、行情平面投影、策略视图三者的交付物；historical replay 已把 frozen dataset id 作为策略可见 data binding 证据，canonical bar projection 已把 source instance/event window/available_time/freshness 传入 MarketView。
6. ExecutionEvent / Outbox / OrderView / IntentView 归属已固定在 `execution/` 与 `strategy/views.py` 的只读投影边界；后续要继续补充状态转移 contract test，避免 execution state machine 与 runtime coordinator 混成一个产品。
7. 固定 Portfolio / Accounting / Treasury / Ledger 归属，明确 account identity、account binding、account state、credential、lock 的不同 owner。
8. 明确 SimulationProfile 的产品语义和 adapter 范围，区分历史回放、paper/testnet、模拟成交和真实成交。
9. analytics/risk/features 的显式 backtest-specific 输入已收口到 `market/slices.py` 的 Market owner contract；Data Product historical MarketSnapshot release 已收口到 `market/snapshots.py` 的 Market owner contract，并由 architecture boundary test 禁止回流到 `runtime/profiles/backtest/feed.py`；Strategy 七个 View 的首轮 owner-side evidence 已落地，run artifact 的 cross-view evidence references 与三类 profile reconstruction tests 也已落地。后续证据原则应扩展到真实 backtest/simulation/live runner 的完整订单、组合、ledger 因果链。

这几项完成后，目录结构、运行结构和用户 API 才是一套一致的最终目标。
