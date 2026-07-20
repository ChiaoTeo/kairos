# Kairos 系统架构收敛与改造总纲

状态：Active Blueprint  
版本：1.0  
基线日期：2026-07-17  
适用范围：整个 `kairospy` 包、`studies` 工作区、CLI、运行时持久化与 `data/` 数据目录
目标读者：系统维护者、策略开发者、数据工程人员和后续改造实施者

## 1. 文档目的

本文是 Kairos 后续系统改造的总纲，统一回答以下问题：

1. `kairospy` 各模块分别负责什么，相互之间应如何依赖；
2. 当前系统哪些能力已经成立，哪些只是模块级能力、尚未形成完整运行闭环；
3. 如何收敛研究数据、回测、模拟、paper/testnet/live 的运行路径；
4. 如何规范 Domain、Data、Catalog、Study、Backtest 和 Runtime 的边界；
5. 哪些旧代码和旧数据需要迁移、降级为内部实现或删除；
6. 如何逐阶段实施，以及每个阶段用什么证据验收。

本文整合并提升以下既有专题设计：

- `architecture.md` 中的多资产领域模型和运行安全方向；
- `data_system_convergence_and_productization.md` 中的数据系统收敛方案；
- `study_data_platform_redesign.md` 中的数据产品身份、Release 和来源治理；
- `study_validation_framework.md` 中的研究证据等级和策略晋级门禁。

后续专题文档可以继续存在，但发生冲突时，应先更新本文的系统级决策，再同步专题文档。

## 2. 当前基线与总体判断

当前代码已经具备较完整的模块能力：

- 多资产 Instrument、InstrumentContractSpec、ListingDefinition 和 Capability；
- 市场数据归一化、Provider connector 和历史数据获取；
- Dataset Catalog V3、不可变 Release、Parquet 和 point-in-time 查询；
- 策略 Intent、执行计划、路由、组合订单和 Venue 规则校验；
- Ledger、Portfolio、Risk、Pricing、Volatility 和产品生命周期；
- 确定性 Backtest、Study Validation 和策略治理；
- Readiness、Reconciliation、Kill Switch、事件日志和部分故障恢复；
- IBKR、Binance、Deribit、Massive 和模拟 connector。

当前全量基线为：

```text
300 tests passed
3 external integration tests skipped unless explicitly enabled
compileall passed
Catalog strict health passed
git diff --check passed
```

因此当前主要矛盾不是单个算法或模型缺失，而是：

> 各模块虽然分别可用，但尚未由一个统一、可恢复、可持续运行的 Application Runtime 串成完整闭环。

目前 paper/testnet/live 的典型运行更接近“一次性受控 CLI 下单”：

```text
加载 Catalog 和 Ledger
  -> 创建 connector
  -> Reconciliation
  -> Readiness
  -> 提交 Order
  -> 输出 Ack
  -> 进程结束
```

最终系统需要形成：

```text
Market Data / Reference / Account State
  -> Strategy Decision
  -> Economic Intent
  -> Portfolio Governance / Risk
  -> Execution Plan
  -> Durable Order State Machine
  -> Venue Ack / Reject / Fill / Cancel
  -> Idempotent Execution Ingestion
  -> Ledger Transaction
  -> Portfolio / Risk / Strategy Position
  -> Reconciliation / Monitoring
  -> Next Decision
```

### 2.1 当前落地状态

本文既描述目标架构，也作为后续改造的验收依据。当前实现状态如下：

| 领域 | 当前状态 | 判断 |
|---|---|---|
| Domain 边界 | Strategy Runtime 已移出 Domain，并有 AST 边界测试 | 基本完成 |
| Application 基础 | KairosApplication、Clock、Config、生命周期和账户锁已建立 | 基础完成，闭环未完成 |
| Order 持久化 | SQLite Runtime Store、订单状态机、Combo/Cancel、UNKNOWN fail-closed 已建立 | 主干完成，Venue 恢复待补 |
| Fill/Ledger | Execution、Order、Ledger、Cursor 已可单事务提交并幂等重建 | 基础完成，真实 execution gateway 待接入 |
| Portfolio/Risk/Reconciliation | 重启 Projection 和 balance/position READY 门禁已落地；Open Order、Funding、Settlement 尚待覆盖 | 主干完成，范围待扩展 |
| 数据产品 | DataProductContract、Typed/Streaming Quality、OHLCV/MarketSnapshot Q3、Golden SMA/SPXW 已落地 | 主干完成 |
| 旧 History/CSV | History 代码、CLI、目录和 340 个 CSV sidecar 已安全迁移删除 | 完成 |
| 旧 Dataset/Surface | 已迁移为内部 MarketSnapshot Driver 和 Feature Release，旧代码/目录已删除 | 完成 |
| 外部运行验收 | 单元/集成基线稳定 | L3/L4 尚未完成 |

因此，当前系统不是“从零建设”，而是处于从模块能力走向正式 Application 闭环的中后期。下一阶段的第一优先级不是继续增加模型，而是完成：

```text
Runtime recovery
  -> Venue order/fill resolution
  -> Ledger rebuild
  -> Portfolio/Risk projection
  -> Reconciliation
  -> READY gate
```

详细落地证据和逐项进度见 `system_convergence_progress.md`。

## 3. 总体设计原则

### 3.1 单一领域事实模型

所有运行模式共享：

- Instrument 和 Product 定义；
- Market Data 经济事实；
- Intent、Order、Execution 和 Ledger；
- Position、Portfolio 和 Risk；
- 产品生命周期和结算规则。

研究、回测、模拟、paper/testnet/live 只能在数据来源、Clock、Fill/Execution Gateway 和运行安全等级上不同，不得各自维护一套业务事实模型。

### 3.2 依赖指向稳定抽象

允许的总体依赖方向：

```text
                    domain
                  /   |    \
          products  pricing  accounting
              |        |        |
          strategies   risk      |
               \       |       /
                application/runtime
              /    |      |       \
           data  backtest execution connectors
              \     |      |       /
                 storage/infrastructure
```

更严格地说：

- Domain 不依赖任何上层或基础设施实现；
- Application Service 负责编排，不重新定义业务规则；
- Connector 只实现端口并完成外部对象转换；
- Storage 只提供持久化能力，不决定业务状态转换；
- Study 和 Backtest 是 Domain/Application 的消费者，不反向进入 Domain；
- CLI 只是入口，不是业务逻辑和依赖组装的长期归宿。

### 3.3 一份事实，一个权威来源

| 事实 | 唯一权威来源 |
|---|---|
| Instrument 身份和合约定义 | Instrument Catalog |
| 数据产品及其不可变版本 | Dataset Catalog |
| 订单生命周期 | Order Store / Order State Machine |
| 成交事实 | Normalized Execution Event + 去重记录 |
| 现金和持仓账务 | Ledger |
| 策略虚拟持仓归属 | Strategy Position Book |
| 系统运行安全状态 | Runtime State Store |
| 研究输入版本 | Study Snapshot |
| 回测输入和结果 | Backtest Manifest |
| 策略生命周期 | Strategy Registry |

缓存、Projection 和报告均可重建，不得成为第二事实源。

### 3.4 默认失败关闭

以下情况不得继续扩大风险：

- Catalog 缺失或 Listing 已过期；
- Market Data 过期或订阅不完整；
- Order 状态不确定；
- Fill 回补未完成；
- Ledger 与 Venue 不一致；
- Kill Switch 已触发；
- 数据、策略或执行策略未达到环境要求；
- 持久化不可用；
- 时钟偏差超过阈值。

## 4. `kairospy` 模块关系梳理

### 4.1 `kairospy.domain`

职责：定义与运行模式无关的业务事实、值对象、实体、状态转换输入和核心约束。

当前主要内容：

- `identity`：Asset、Instrument、Venue、Account 等稳定身份；
- `product`：Equity、Listed Option、Spot、Future、Perpetual、Crypto Option 等 InstrumentContractSpec；
- `instrument`：InstrumentDefinition、ListingDefinition、OptionChain；
- `market_data`：Quote、Trade、Bar、OrderBook、Greeks 等经济事实；
- `intent`、`order`、`execution`：交易意图、订单和成交；
- `ledger`：不可变账务事实；
- `corporate_action`、`derivative_event`：产品生命周期事件；
- `capability`：端口能力契约；
- `strategy_contract`：策略身份和经济契约。

应保留：

- 纯业务对象和约束；
- 不依赖 I/O 的 reducer 或验证逻辑；
- 可在测试中直接构造的稳定类型。

应移出：

- 直接引用具体 `ReferenceCatalog` 的 StrategyContext；
- 对 Backtest MarketSnapshot、Study Feature、Volatility Repository 等上层对象的依赖；
- 文件、Dataset、Provider、Release、DataFrame 和运行环境概念。

目标依赖：Domain 只能依赖 Python 标准库和 Domain 内部模块。

### 4.2 `kairospy.reference`

职责：管理 Instrument Definition 和 Venue Listing 的 point-in-time 版本。

回答：

- 这是哪个金融合约；
- 其 InstrumentContractSpec 是什么；
- 在某个 Venue 的 symbol、external ID、tick、lot 和 minimum notional 是什么；
- 某时刻哪一版定义有效。

不负责：

- Dataset Release；
- 市场数据文件位置；
- 策略选择；
- 订单状态；
- Provider 下载任务。

目标形态：

- `ReferenceCatalog` 作为应用服务依赖的端口或稳定服务；
- Repository 作为可替换基础设施；
- Connector 通过 Reference Port 更新 Catalog；
- 执行和估值只通过 InstrumentId 查询，不猜测 Venue symbol。

### 4.3 `kairospy.data`

职责：管理研究数据产品的身份、获取、版本、发布、查询、质量和复现。

主要概念：

- Dataset Product；
- Dataset Release；
- Schema/Transform Version；
- Source Binding；
- Coverage/Quality/Lineage；
- Acquisition Plan；
- Alias Promotion；
- Study Input Snapshot。

目标定位：

- `DatasetClient` 是 Study 和 Backtest 的唯一公开数据入口；
- Event、Tabular、MarketSnapshot Reader 都是内部 storage driver；
- 所有正式数据读取必须先解析为不可变 Release；
- Backtest 禁止隐式联网获取；
- 正式研究必须冻结输入 Release 和 content hash。

与 Domain 的关系：

- Data 保存 Domain 事实的可审计持久化表达；
- Canonical Record 比 Domain 对象多 available time、provider、source ID 和 lineage；
- Data 可以将 Canonical Record 映射为 Domain 对象；
- Domain 不知道 Data Product 和 Release。

### 4.4 `kairospy.market_data`

职责：定义实时/事件市场数据的 Canonical Record、质量检查、读取和基础估值输入。

目标边界：

- `domain.market_data` 保存经济事实；
- `market_data.events` 保存带来源和 point-in-time 语义的 Canonical Record；
- `market_data.repository` 是 Data 内部 Event Storage Driver；
- `quality`、`quality_gate` 提供运行时行情质量，而 Dataset Quality Engine 负责 Release 级质量。

需要避免：

- 与 Domain Event 共用含义不清的 Event 名称；
- 研究代码直接依赖 Repository；
- Provider payload 直接进入 Strategy/Risk/Pricing。

### 4.5 `kairospy.connectors`

职责：把外部 Venue/Provider 接口实现为系统端口。

端口包括：

- ReferenceDataPort，由 ReferenceDataClient 实现；
- MarketDataPort，由 MarketDataClient 实现；
- ExecutionPort，由 ExecutionGateway 实现；
- AccountPort，由 AccountGateway 实现；
- CorporateActionPort，由 CorporateActionClient 实现；
- Funding/Settlement Port，由 FundingSettlementClient 实现；
- Dataset Provider Connector。

Connector 必须：

- 声明 environment、venue 和 port-scoped capability；
- 将外部 ID 映射为 InstrumentId；
- 将外部对象转换为 Canonical/Domain 类型；
- 实现幂等键、分页、限流、重连和错误分类；
- 不让外部 SDK 对象进入上层。

Connector 不得：

- 自行决定策略和风险；
- 直接修改 Ledger；
- 直接修改 Dataset Catalog JSON；
- 通过内部 InstrumentId 猜 Venue symbol；
- 在调用链中吞掉关键异常。

### 4.6 `kairospy.products`

职责：实现不同 InstrumentContractSpec 的财务计算和生命周期规则。

包括：

- 合约乘数和 PnL；
- linear/inverse/quanto 计算；
- option/future/perpetual settlement；
- 产品专属 position calculator。

目标：

- 只依赖 Domain；
- 不依赖 Venue；
- 同一规则被 Backtest、Simulation 和 Live Ledger 使用；
- 产品规则通过明确 registry 选择，不散落在 if/else 中。

### 4.7 `kairospy.pricing`

职责：无 Venue 副作用的定价、隐含波动率和估值服务。

输入：

- Domain InstrumentContractSpec；
- 市场观察；
- Rate/Dividend/Forward；
- Volatility Surface。

输出：

- 价格、Greeks、诊断和 Valuation Snapshot。

目标：

- 纯计算核心无 I/O；
- 数据获取由应用层完成；
- vendor analytics 与内部估值明确分开；
- 相同输入在 Study、Backtest 和 Runtime 结果一致。

### 4.8 `kairospy.volatility`

职责：波动率曲面模型、校准和查询。

目标调整：

- Calibration/Surface/SVI 保留为计算模块；
- `SurfaceRepository` 迁移为 Feature Dataset Storage；
- 正式曲面通过 Dataset Catalog Release 管理；
- 曲面输入冻结 Canonical Release 和算法版本；
- 实时内存曲面可以存在，但必须能追踪输入和计算版本。

### 4.9 `kairospy.features`

职责：构建可跨策略复用、point-in-time safe 的特征数据产品。

必须满足：

- 输入是冻结 Release；
- Transform ID/Version 明确；
- 不包含未来标签；
- 输出发布为 Feature Release；
- offline 与 incremental 结果一致；
- Feature 不以单个策略命名。

### 4.10 `kairospy.study_platform`

职责：研究工作流、样本、报告、实验和验证产物。

允许：

- 假设、未来标签、样本切分和统计检验；
- Study-specific artifact；
- 研究 Snapshot 和报告；
- 显式 development/validation/test。

禁止：

- 直接读取 Provider；
- 直接拼接 `data/...` 路径；
- 直接使用旧 Repository；
- 将研究专属标签发布为 Canonical/Feature；
- 只记录浮动 Alias 而不冻结 Release。

### 4.11 `kairospy.strategies`

职责：根据 StrategyContext 产生 Economic Intent，不直接下单。

目标：

- StrategyContext 位于 strategies/runtime 或 application，而不是 Domain；
- 策略只依赖稳定的只读视图；
- 策略不感知 Venue symbol、API、文件和 Dataset 路径；
- 策略版本与执行策略版本分别治理；
- 策略不直接修改 Portfolio 或 Ledger；
- 相同策略可以运行于 Backtest、Simulation 和 Live Runtime。

### 4.12 `kairospy.risk`

职责：订单前风险、结构风险、Portfolio 风险、场景分析和策略资本治理。

建议分为：

- Pre-trade Risk：订单/组合订单准入和 resize；
- Portfolio Risk：敞口、Greeks、Margin、Liquidation；
- Scenario Risk：spot/vol/skew/term/rate/time shock；
- Governance Risk：策略预算、drawdown、生命周期和组合分配。

目标：

- 所有下单必须通过统一 Risk Decision；
- Risk Decision 持久化并关联 Intent/Order；
- hard limit 不能由 Strategy 覆盖；
- 未知价格、缺失 conversion 或 stale data 默认拒绝扩大风险。

### 4.13 `kairospy.execution`

职责：把 Economic Intent 转换为可执行计划，并完成 Venue 路由和成交归一化。

子职责：

- `strategy_planner`：Intent -> typed execution plan；
- `policy`：执行语义和 legging policy；
- `router`：Venue capability、listing、tick、lot、notional 校验；
- `ingestion`：Ack/Fill/Funding/Settlement 归一化和去重。

目标增强：

- 增加持久 Order State Machine；
- Submit 前先持久化；
- Ack、Reject、Fill、Cancel、Expire 都是状态事件；
- UNKNOWN 状态必须查询 Venue，不能盲目重发；
- Fill 统一进入 LedgerService。

### 4.14 `kairospy.accounting`

职责：通过不可变 Ledger 重建现金、持仓、费用、Funding、公司行为和结算。

目标：

- Ledger 是财务事实唯一来源；
- Venue Account State 只用于 Reconciliation；
- Fill、Funding、Corporate Action、Settlement 通过统一 Ingestion 进入 Ledger；
- Portfolio 和 Risk View 是 Ledger Projection；
- Ledger 持久化必须具备事务、唯一约束和崩溃恢复。

### 4.15 `kairospy.backtest`

职责：用冻结历史输入、确定性 Clock 和 Fill Model 驱动正式 Strategy/Application 逻辑。

目标：

- Backtest 不维护平行业务规则；
- 使用正式 Strategy、Risk、Execution Plan 和 Ledger reducer；
- 仅替换 Market Data Feed、Clock 和 Execution Gateway；
- 无同 slice fill；
- 输出完整审计 hash；
- 使用 Q3/Q4 Release；
- Conservative 和 Stress 必须成对评估。

### 4.16 `kairospy.orchestration`

职责：系统运行编排、安全门禁、恢复和操作状态。

当前已有：

- ExecutionCoordinator；
- Readiness；
- Reconciliation；
- Kill Switch；
- Persistent Event Log；
- Operational Monitor；
- Strategy Monitoring。

目标升级：

- 演进为统一 KairosApplication Runtime；
- Readiness 从调用方布尔值升级为真实 Probe；
- Kill Switch、cursor 和恢复状态持久化；
- 编排 Market Data、Strategy、Risk、Execution、Ledger 和 Monitoring 循环；
- 明确 start/recover/run/degrade/shutdown 生命周期。

### 4.17 `kairospy.storage`

职责：提供序列化、事务状态存储和数据湖底层实现。

目标分工：

- Parquet：大规模市场数据、Feature 和研究数据；
- SQLite 或等价事务存储：Order、Fill、Ledger、Runtime State、Cursor、Alert；
- JSON/Markdown/CSV：只作为报告、交换或小型 Artifact；
- Repository 是基础设施实现，不应成为研究用户 API。

### 4.18 `kairospy.history`

状态：已删除。

已完成的迁移包括：

- OHLCV 数据迁入不可变 Dataset Release；
- 查询统一进入 DatasetClient；
- SMA 示例改为治理 Release 驱动的 Backtest；
- 删除 `kairospy.history`、`BarRepository` 和顶级 `kairospy history` CLI；
- 旧 CSV sidecar 经行数核验后删除，新发布不再双写 CSV。

后续架构测试应持续禁止重新引入 History import、CLI 和物理目录。

## 5. 目标系统分层

建议将逻辑架构稳定为六层。

### 5.1 Domain Layer

包含：

- identity、product、instrument；
- market facts；
- intent、order、execution、ledger；
- lifecycle events；
- 纯业务约束。

特点：无 I/O、无当前环境、无 Provider、无文件路径。

### 5.2 Application Layer

包含：

- KairosApplication；
- StudyApplication；
- BacktestApplication；
- DataPreparationApplication；
- Strategy Runtime；
- Risk/Execution/Accounting 编排。

特点：通过端口组织用例和事务，不实现外部协议。

### 5.3 Port Layer

包含 Protocol：

- InstrumentDefinitionProvider；
- MarketDataSource；
- ExecutionVenue；
- AccountStateSource；
- OrderStore；
- LedgerStore；
- RuntimeStateStore；
- DatasetReader/Publisher；
- Clock；
- AlertSink。

### 5.4 Connector Layer

包含 IBKR、Binance、Deribit、Massive 和 Simulation 实现。

### 5.5 Infrastructure Layer

包含：

- SQLite；
- Parquet/Arrow/DuckDB；
- 文件 Artifact Store；
- HTTP/WebSocket transport；
- structured logging 和 metrics。

### 5.6 Interface Layer

包含：

- CLI；
- Notebook/Python API；
- 后续可选 daemon/API；
- health/report 输出。

Interface 只能调用 Application Layer。

## 6. 目标 Application Runtime

### 6.1 组件

```text
KairosApplication
├── ApplicationConfig
├── Clock
├── ReferenceCatalog
├── DatasetCatalog / DatasetClient
├── MarketDataRuntime
├── StrategyRuntime
├── PortfolioGovernance
├── RiskService
├── ExecutionPlanningService
├── OrderService / OrderStore
├── ExecutionIngestionService
├── LedgerService / LedgerStore
├── PortfolioProjection
├── ReconciliationService
├── RuntimeStateStore
├── ReadinessService
├── KillSwitchService
└── Monitoring / AlertSink
```

### 6.2 生命周期

```text
CREATED
  -> STARTING
  -> RECOVERING
  -> RECONCILING
  -> READY
  -> RUNNING
  -> DEGRADED | REDUCE_ONLY
  -> STOPPING
  -> STOPPED
```

异常状态：

```text
FAILED_START
UNKNOWN_EXTERNAL_STATE
PERSISTENCE_UNAVAILABLE
EMERGENCY_STOPPED
```

运行状态必须持久化。重启不能默认从 CREATED 直接进入 READY。

### 6.3 启动顺序

1. 加载并校验配置；
2. 获取单实例账户控制锁；
3. 打开并迁移事务数据库；
4. 加载 Instrument Catalog 和 Strategy Registry；
5. 恢复 Kill Switch、Order、Cursor 和上次运行状态；
6. 初始化 connector；
7. 恢复 Market/Private Stream，并执行 REST Backfill；
8. 查询 Venue balances、positions 和 open orders；
9. 解决 SUBMITTING/UNKNOWN Orders；
10. 将缺失 Fill/Funding/Settlement 幂等写入 Ledger；
11. 执行 Reconciliation；
12. 执行所有 Readiness Probe；
13. 只有全部通过才允许新开仓。

### 6.4 关闭顺序

1. 停止产生新 Intent；
2. 停止新开仓；
3. 继续处理 Ack、Fill 和 Cancel；
4. 持久化 cursor 和运行状态；
5. 关闭 stream 和 connector；
6. 释放账户控制锁；
7. 写入 shutdown audit event。

## 7. Order、Execution 与 Ledger 闭环

### 7.1 Order 状态机

```text
PLANNED
  -> APPROVED
  -> SUBMITTING
  -> ACKNOWLEDGED | REJECTED | UNKNOWN
  -> PARTIALLY_FILLED
  -> FILLED | CANCELLING | EXPIRED
CANCELLING
  -> CANCELLED | FILLED | UNKNOWN
```

原则：

- 每次转换必须保存事件、时间和原因；
- 非法转换必须拒绝；
- Order Request 在外部调用前持久化；
- Ack 不是成交；
- UNKNOWN 不允许自动创建新订单替代；
- Cancel 与 Fill 并发时以 Venue 成交事实和幂等 Ledger 为准。

### 7.2 崩溃窗口治理

必须覆盖：

- 持久化前崩溃：没有外部副作用；
- SUBMITTING 后、调用 Venue 前崩溃：恢复时可以安全判断未提交；
- Venue 接受后、本地 Ack 前崩溃：通过 client order ID 查询 Venue；
- Ack 后、Fill 前崩溃：恢复 open order 和 private events；
- Partial Fill 后崩溃：REST/WebSocket 回补且只入账一次；
- Cancel 请求后崩溃：恢复订单真实最终状态。

### 7.3 Execution Ingestion

所有来源统一进入：

```text
Venue WebSocket Fill
Venue REST Fill History
Simulation Fill
Backtest Fill
Funding / Settlement / Corporate Action
        -> Normalize
        -> Validate
        -> Deduplicate
        -> Persist external event
        -> LedgerService
        -> Persist ledger transaction
        -> Update projections
```

### 7.4 事务边界

至少以下动作需要单事务：

- 保存外部执行事件；
- 写入去重键；
- 生成 Ledger Transaction；
- 更新 Order Fill Quantity/State；
- 更新 consumer cursor。

否则崩溃会产生“事件已消费但未入账”或“已入账但 cursor 未推进”的不一致。

## 8. 运行状态持久化

建议使用 SQLite 或等价本地事务数据库管理运行态：

```text
runtime_sessions
runtime_state
account_locks
orders
order_events
execution_events
external_event_dedup
ledger_transactions
ledger_entries
consumer_cursors
reconciliation_reports
kill_switch_events
alerts
```

Parquet 不适合订单状态和 Ledger 事务；JSONL 可以作为审计导出，但不应继续承担唯一事务存储。

必须具备：

- schema migration；
- unique constraint；
- foreign key；
- WAL/事务；
- 备份恢复；
- 单账户单控制进程锁；
- 审计导出。

## 9. Readiness、Degraded 与 Kill Switch

### 9.1 Readiness Probe

Readiness 必须由实际探针产生，不接受调用方直接传入 `True`。

| Probe | 通过条件 |
|---|---|
| Persistence | DB 可读写、migration 完成、账户锁成功 |
| Instrument Catalog | 所需 Instrument、Listing 和有效期完整 |
| Dataset/Feature | 策略依赖的数据达到最低等级 |
| Market Data | 连接正常、数据新鲜、订阅完整 |
| Account | 余额、持仓、open orders 查询成功 |
| Execution | 环境、权限、endpoint、clock 和 capability 正确 |
| Recovery | unknown orders 和 backfill 已处理 |
| Reconciliation | 差异在容差内 |
| Strategy | lifecycle 允许当前环境 |
| Risk | limits 和 capital allocation 有效 |

### 9.2 Degraded 行为

| 故障 | 默认行为 |
|---|---|
| Market Data stale | 禁止新开仓，可撤单/减仓 |
| Reconciliation mismatch | 进入 reduce-only 或 suspend |
| Persistence unavailable | 停止一切外部写操作 |
| Order UNKNOWN | 禁止同账户扩大风险 |
| Rate limit warning | 降低非关键请求 |
| Authentication failure | 停止执行并告警 |
| Strategy drawdown | 按策略政策 degrade/suspend |
| Kill Switch | 取消 working orders，持久 reduce-only |

### 9.3 Kill Switch

必须持久化：

- 是否触发；
- 原因；
- 操作者/触发器；
- 触发时间；
- 已取消和失败订单；
- 是否仍有未确认 working order；
- 解除审批记录。

重启后必须继续保持 reduce-only，不能自动解除。

## 10. 配置、Clock、ID 与日志

### 10.1 ApplicationConfig

统一管理：

- environment；
- workspace/data/runtime roots；
- Instrument/Dataset Catalog；
- account 和 product line；
- Venue endpoint；
- credential reference；
- risk limits；
- reconciliation tolerance；
- freshness/clock/rate limit；
- logging/metrics；
- strategy deployment；
- feature flags。

配置必须在启动时一次性校验。Live 配置不得使用 testnet endpoint，凭证值不得写入配置 Artifact。

### 10.2 Clock

所有业务时间来自 Clock Port：

- SystemClock；
- BacktestClock；
- FixedClock。

业务模块不得直接调用 `datetime.now()`。外部事件时间、接收时间、可用时间和处理时间必须分别保存。

### 10.3 ID

统一关联：

```text
strategy_id
strategy_version
intent_id
correlation_id
internal_order_id
client_order_id
venue_order_id
execution_id
ledger_transaction_id
runtime_session_id
```

必须明确哪些由系统生成、哪些来自 Venue、哪些跨重启稳定。

### 10.4 Logging 与 Metrics

结构化日志至少包含：

- timestamp；
- environment；
- runtime session；
- venue/account；
- strategy/intent/correlation/order/execution ID；
- component；
- event/action；
- result/error class。

关键 Metrics：

```text
market_data_age_seconds
open_orders
unknown_orders
fill_ingestion_lag_seconds
reconciliation_difference_count
ledger_last_event_age_seconds
venue_clock_skew_ms
rate_limit_utilization
websocket_reconnect_count
strategy_drawdown
kill_switch_state
runtime_state
```

日志和 Artifact 不得包含 API key、secret 或不必要的账户敏感信息。

## 11. 数据系统收敛

### 11.1 核心决策

```text
Dataset Catalog Release
  = 所有持久化研究数据的唯一治理身份

DatasetClient
  = Study 和 Backtest 的唯一公开数据入口
```

底层可以存在不同 storage driver，但不允许成为用户入口。

### 11.2 五层模型

```text
data/
├── source
├── canonical
├── curated
├── features
└── studies
```

配套治理目录可以包括：

```text
catalog
reference
migrations
artifacts
cache
quarantine
```

其中 cache 可删除且不是事实来源；quarantine 不得被正式研究消费。

### 11.3 数据身份

必须分离：

- Logical Dataset Product；
- Dataset Release；
- Schema Version；
- Transform Version；
- Alias；
- Physical Location。

正式研究和回测保存 Release ID 与 content hash，不只保存 Alias。

### 11.4 DataProductContract

统一当前分散的 Dataset 定义和 Provider 配置：

```text
identity
semantics/dimensions
schema contract
storage contract
source bindings
quality profile
usage/default view
owner/SLA/limitations
```

Product Spec 不保存具体 Release 路径。Layout Policy 根据 Product 和 Release ID 生成路径。

### 11.5 Storage Kind

Release 显式声明：

```text
tabular
market_events
market_snapshots
reference
```

DatasetClient 按 storage kind 选择内部 Reader，禁止根据目录名、`dataset=*` 或 `dataset.json` 推断。

### 11.6 Canonical 与 Domain

Canonical Record 保存：

- Domain 经济字段；
- event_time；
- available_time；
- ingested_at；
- provider_id；
- venue_id；
- source namespace/instrument ID；
- source release；
- correction/cancel flags；
- lineage。

Domain 对象不保存 Dataset Release 和 Provider payload。

### 11.7 Quality Engine

质量等级必须由检查结果计算：

- Q0 Archived；
- Q1 Integrity；
- Q2 Study；
- Q3 Backtest；
- Q4 Production。

发布调用方不能直接指定更高等级。

按数据类型定义 Profile：

- OHLCV；
- Quote；
- Trade；
- Market Event；
- Option Snapshot；
- Feature；
- Reference。

### 11.8 产品体验

目标 CLI：

```bash
kairospy data search
kairospy data describe <product>
kairospy data prepare <product> --start ... --end ... --quality backtest
kairospy data query <product-or-release>
kairospy data replay <product-or-release>
kairospy data compare <release-a> <release-b>
kairospy data freeze <study-id> --input ...
kairospy data doctor <product-or-release>
kairospy data diagnostics --strict
kairospy data migrate ...
```

`prepare` 统一 plan、source selection、acquire、validate、publish 和可选 promotion，但不得静默发起昂贵请求。

### 11.9 需要删除或收敛的旧路径

| 旧对象 | 目标 |
|---|---|
| `BarRepository` | Canonical OHLCV + DatasetClient |
| `kairospy history` | `kairospy data` + 正式 backtest |
| `DatasetRepository` 公开使用 | 内部 MarketSnapshot storage driver |
| `StudySnapshotCollectionStore` | Collection Publisher |
| `SurfaceRepository` | Feature Release |
| CSV sidecar | 停止生成并迁移删除 |
| `data/history` | Canonical |
| 旧 `data/datasets` | Curated Release |
| `data/surfaces` | Features |
| 空 raw/normalized/derived/study | 删除 |
| Release local aliases | Catalog Alias Registry |

### 11.10 迁移安全

每次数据迁移必须：

- 默认 dry-run；
- 幂等；
- 支持中断恢复；
- 核对行数、主键、时间范围、精度和 hash；
- 生成 migration report；
- 目标 Release 可 query/freeze/replay；
- 只有验证通过后允许 `--delete-source`；
- 删除代码和删除数据分开提交。

## 12. Study、Backtest 与 Live 的统一关系

### 12.1 允许替换的组件

| 运行模式 | Clock | Market Input | Execution |
|---|---|---|---|
| Study | Fixed/System | Frozen Release/Table | 无或分析模型 |
| Backtest | BacktestClock | Frozen Replay | Fill Model |
| Simulation | SystemClock | Live/Replay | SimulatedExecutionGateway |
| Paper/Testnet | SystemClock | Venue | VenueExecutionGateway |
| Live | SystemClock | Venue | VenueExecutionGateway |

以下组件应共享：

- Strategy；
- Intent；
- Risk；
- Execution Planning；
- Order State Machine；
- Ledger reducer；
- Portfolio projection；
- Strategy lifecycle 和监控。

### 12.2 Backtest 合格条件

- 数据 Release 达到 Q3/Q4；
- 输入冻结；
- no same-slice fill；
- 参数在 validation/test 冻结；
- Fill、Fee、Slippage、Margin 模型有版本；
- Conservative 和 Stress 同时输出；
- Ledger/Portfolio 可对账；
- replay audit hash 稳定。

### 12.3 Live 合格条件

- Strategy Registry 已批准 Live；
- Study/Backtest/Paper 证据链完整；
- 数据和执行能力满足策略 Claim；
- Runtime L3/L4 验收通过；
- Risk limits 和 capital allocation 明确；
- Kill Switch 和恢复 Runbook 已演练；
- 人工责任人和告警路径明确。

## 13. 改造工作包

### EPIC A：架构边界和组合根

工作：

- 新建 KairosApplication/ApplicationConfig；
- CLI 只调用 Application Service；
- 定义 Clock、Store、Catalog、Venue 和 Alert Ports；
- 将 StrategyContext 移出 Domain；
- 增加架构依赖测试。

完成证据：

- Domain 无上层依赖；
- `__main__.py` 不组装业务细节；
- Simulation、Paper、Backtest 使用同一 Application 用例。

### EPIC B：事务运行状态

工作：

- 引入 SQLite Runtime Store；
- schema migration；
- account process lock；
- Order、Fill、Ledger、Cursor、Kill Switch 和 Alert 表；
- JSON/JSONL 只作为迁移和审计导出。

完成证据：

- 强杀进程后数据库可恢复；
- 唯一键阻止重复订单和重复成交；
- 两进程不能同时控制同账户。

### EPIC C：Order/Fill/Ledger 闭环

工作：

- Order State Machine；
- submit-before-side-effect persistence；
- private stream + REST backfill；
- execution dedup；
- Ledger 单事务 ingestion；
- Portfolio/Risk projection。

完成证据：

- Ack、Reject、Partial Fill、Fill、Cancel、Expire 全覆盖；
- 崩溃窗口故障注入通过；
- Venue、Order Store、Ledger 和 Portfolio 对账一致。

### EPIC D：Runtime Safety

工作：

- 真实 Readiness Probe；
- persistent Kill Switch；
- degraded/reduce-only 状态机；
- reconciliation scheduler；
- monitoring、metrics、alert sink；
- startup recovery 和 graceful shutdown。

完成证据：

- 任一关键探针失败时禁止新开仓；
- 重启不解除 Kill Switch；
- mismatch 能自动降级并告警。

### EPIC E：数据产品收敛

工作：

- DataProductContract；
- storage kind；
- unified Reader drivers；
- Quality Engine；
- search/describe/prepare/doctor；
- Alias 统一；
- Product health report。

完成证据：

- DatasetClient 唯一公开入口；
- Backtest 全部使用 Q3/Q4 frozen Release；
- Catalog strict health 通过。

### EPIC F：旧系统迁移和删除

工作：

- 迁移 history、datasets、surfaces 和 CSV sidecar；
- 修改 SMA、Notebook、Study 和 Backtest 消费者；
- 删除旧 CLI、Repository 和空目录；
- 删除目录推断 Reader 的兼容逻辑。

完成证据：

- 全仓旧 import/path 扫描为空；
- migration reports 全部通过；
- Golden Pipeline 结果可复现。

### EPIC G：产品化和运维

工作：

- 统一 CLI 任务模型；
- 配置模板和环境诊断；
- 数据/策略/运行状态健康报告；
- Runbook；
- Paper/Testnet soak test；
- Live promotion checklist。

完成证据：

- 新用户可独立完成数据准备、回测和模拟；
- 运维可定位订单、成交、Ledger 和告警；
- 24–72 小时 Paper/Testnet 稳定运行。

## 14. 推荐实施阶段

阶段状态以 2026-07-17 的代码和数据为准：Phase 0、Phase 1 的核心工作已完成；Phase 2、Phase 3 已完成主干但仍有收尾项；Phase 4–6 尚未达到退出标准。阶段编号表达依赖顺序，不要求机械串行；任何旧路径都必须在替代链路验收后才能删除。

### Phase 0：冻结基线

状态：已完成。

- 批准本文目标架构；
- 记录现有 import 图、CLI、Catalog、数据目录和测试基线；
- 旧入口标记 deprecated，禁止新增消费者；
- 建立架构测试骨架和迁移账本。

退出标准：改造期间不会继续产生新的平行路径。

### Phase 1：组合根与端口

状态：核心完成。

- 建立 ApplicationConfig、Clock 和 KairosApplication；
- 将 CLI 组装迁移到 bootstrap；
- 修正 Domain 依赖；
- 建立端口和测试 connector。

退出标准：模拟闭环由 Application Runtime 驱动。

### Phase 2：事务状态与订单闭环

状态：进行中。订单和成交事务基础已完成，Venue 恢复、Portfolio/Risk Projection 和完整 Reconciliation 尚未完成。

- Runtime Store；
- Order State Machine；
- Execution Ingestion；
- Ledger Transaction；
- 恢复和故障注入。

退出标准：L2 单进程闭环和 L3 崩溃恢复通过。

### Phase 3：数据系统收敛

状态：进行中。统一 Release、storage kind、OHLCV Quality、产品 CLI 和两条 Golden SMA 已完成；DataProductContract、其他 Quality Profile、Dataset/Surface 迁移仍待完成。

- Product Spec、storage kind、Quality Engine；
- 统一 DatasetClient；
- 产品体验命令；
- 迁移旧数据。

退出标准：正式研究/回测只使用治理 Release。

### Phase 4：运行安全和观测

状态：进行中。生命周期、Probe、持久 Kill Switch 和账户锁已有基础，真实运行恢复、周期对账、告警和故障矩阵尚未完成。

- Probe、Reconciliation schedule、persistent Kill Switch；
- degraded state；
- logs、metrics、alerts；
- startup/shutdown recovery。

退出标准：故障自动停止扩大风险，恢复路径可证明。

### Phase 5：删除旧系统

状态：部分完成。History 和 CSV sidecar 已删除；DatasetRepository、SurfaceRepository 和旧目录尚未退出。

- 删除 History、旧 Repository、CLI 和目录；
- 删除兼容分支和 CSV 双写；
- 更新全部文档、Notebook 和示例。

退出标准：旧依赖静态扫描为空。

### Phase 6：Paper/Testnet 和 Live Readiness

状态：未开始正式验收。

- 24–72 小时连续运行；
- 重连、回补、重启、Kill Switch 演练；
- Runbook 和告警路径；
- Live promotion review。

退出标准：L4 通过；Live 策略逐项完成 L5。

## 15. 验收等级

### L0：静态架构

- Domain 无反向依赖；
- Interface 只依赖 Application；
- Study/Strategy 不直接依赖 Storage Driver；
- 无硬编码正式数据路径；
- Product、Order、Ledger 等事实源唯一。

### L1：模块正确

- 全量 Unit/Contract Tests 通过；
- 定价、风险、产品和 Ledger 可手算核对；
- 数据 Release/Hash/Schema 测试通过；
- Connector 不联网的 Contract Tests 通过。

### L2：单进程业务闭环

```text
Market Data
 -> Strategy
 -> Intent
 -> Risk
 -> Order
 -> Simulated Fill
 -> Ledger
 -> Portfolio
 -> Reconciliation
```

要求：整个流程由正式 Application Runtime 驱动，最终 Ledger hash 固定。

### L3：故障恢复

注入：

- 下单前崩溃；
- Venue 接受后、Ack 本地持久化前崩溃；
- Partial Fill 后崩溃；
- WebSocket 断线；
- REST 回补重复；
- Ledger 写事务中断；
- Kill Switch 后重启；
- Reconciliation mismatch。

要求：不重复下单、不重复记账、不丢成交、不在未知状态扩大风险。

### L4：Paper/Testnet 稳定性

- 连续运行 24–72 小时；
- 无未解释 UNKNOWN Order；
- Ledger 与 Venue 周期性一致；
- reconnect/backfill 正常；
- 数据新鲜度和 clock skew 达标；
- restart recovery drill 通过；
- Kill Switch drill 通过；
- 无凭证泄露和未处理 Critical Alert。

### L5：Live Readiness

- Strategy Lifecycle 批准；
- 数据、回测、Paper 证据完整；
- hard limits、capital limit 和 daily loss limit 生效；
- 人工和自动 Kill Switch 可用；
- Runbook、值守和告警责任明确；
- 小额、受限、可回滚的 Live rollout 方案批准。

## 16. 自动化验收矩阵

### 16.1 架构测试

建议新增：

```text
tests/architecture/test_domain_dependencies.py
tests/architecture/test_application_boundaries.py
tests/architecture/test_public_data_api.py
tests/architecture/test_no_旧版_imports.py
tests/architecture/test_no_physical_data_paths.py
tests/architecture/test_single_source_of_truth.py
```

### 16.2 Runtime 测试

建议新增：

```text
tests/runtime/test_application_lifecycle.py
tests/runtime/test_order_state_machine.py
tests/runtime/test_crash_recovery.py
tests/runtime/test_execution_ingestion.py
tests/runtime/test_persistent_kill_switch.py
tests/runtime/test_runtime_readiness.py
tests/runtime/test_single_account_lock.py
tests/runtime/test_transactional_ledger_ingestion.py
```

### 16.3 数据测试

建议新增或强化：

```text
tests/data/test_product_spec_registry.py
tests/data/test_storage_kind_dispatch.py
tests/data/test_quality_profiles.py
tests/data/test_catalog_health.py
tests/data/test_migration_idempotency.py
tests/data/test_prepare_end_to_end.py
tests/data/test_frozen_study_inputs.py
```

### 16.4 Golden Pipelines

至少固定：

1. Binance BTC-USDT OHLCV -> Feature -> Strategy/Study；
2. Deribit BTC Option -> Curated/Feature -> Validation；
3. Massive SPXW Events -> MarketSnapshot -> Backtest；
4. Simulated Venue -> Order -> Fill -> Ledger -> Reconciliation；
5. Testnet Venue -> Ack/Fill Backfill -> Ledger。

每条保存：

- 输入 Release/Fixture hash；
- Application/Strategy/Execution Policy version；
- Ledger hash；
- Backtest/Study audit hash；
- 关键结果和容差。

## 17. 标准验收命令

基础 CI：

```bash
./pyenv/bin/python -m compileall -q kairospy tests studies
./pyenv/bin/python -m unittest discover -s tests -v
git diff --check
```

目标 CI 增加：

```bash
./pyenv/bin/python -m kairospy data diagnostics --strict
./pyenv/bin/python -m kairospy data doctor --all-products
./pyenv/bin/python -m kairospy runtime doctor --environment simulated
./pyenv/bin/python -m kairospy runtime recovery-test --scenario all
./pyenv/bin/python -m kairospy data migrate --audit-only
```

静态扫描：

```bash
rg '^from kairospy\.(data|catalog|storage|study|backtest)' kairospy/domain
rg 'from kairospy\.history|import kairospy\.history' kairospy examples tests
rg 'DatasetRepository\(' kairospy examples
rg 'data/(history|datasets|surfaces|raw|normalized|derived)' kairospy examples docs
rg 'read_(csv|parquet)|open\(.+data/' examples studies
```

最终状态：

- Domain 依赖扫描为空；
- History 依赖扫描为空；
- DatasetRepository 仅允许出现在内部 driver 和专门测试；
- 旧路径仅允许出现在 migration 文档/工具；
- Notebook/Study 不直接读取物理文件。

## 18. 量化验收指标

| 指标 | 完成目标 |
|---|---:|
| 全量自动化测试 | 100% 通过 |
| Domain 上层实现依赖 | 0 |
| 正式数据公开入口 | 1 |
| 正式 Runtime 组合根 | 1 |
| 新发布 CSV sidecar | 0 |
| 未注册正式数据目录 | 0 |
| Product owner 覆盖率 | 100% |
| Release 完整元数据覆盖率 | 100% |
| Backtest 使用 Q3/Q4 Release | 100% |
| 正式研究冻结输入 | 100% |
| 外部执行事件去重覆盖率 | 100% |
| Order 状态持久化覆盖率 | 100% |
| Fill 到 Ledger 可追踪率 | 100% |
| UNKNOWN Order 未处置数量 | 0 |
| 重启后 Kill Switch 保持率 | 100% |
| Paper/Testnet Reconciliation 未解释差异 | 0 |
| 官方 Notebook 直接物理路径读取 | 0 |

## 19. 系统级 Definition of Done

系统逻辑真正跑通，应同时满足：

1. 所有模式共享 Domain、Strategy、Risk、Execution Plan 和 Ledger；
2. KairosApplication 是唯一运行组合根；
3. Order 在外部副作用前持久化，并有完整状态机；
4. Ack、Fill、Cancel、Funding 和 Settlement 可幂等恢复；
5. Ledger 是现金和持仓唯一事实源；
6. 任意时刻强杀并重启，不会重复下单、不丢成交、不重复记账；
7. 恢复和 Reconciliation 完成前不能新开仓；
8. Kill Switch 和 reduce-only 跨重启保持；
9. 数据只通过治理 Release 被研究和回测消费；
10. Domain 与 Data、Catalog、Storage、Study、Backtest 解耦；
11. 旧数据路径、旧 Repository 和平行 CLI 已删除；
12. 日志、Metrics、Alerts 能串联 Intent -> Order -> Fill -> Ledger；
13. L0–L3 自动化验收通过；
14. Paper/Testnet 完成 L4 后，策略才有资格申请 L5。

最核心的系统验收命题是：

> 任意时刻终止进程再重启，系统能够仅依赖持久化事实和 Venue 查询恢复真实状态，证明没有重复订单、没有遗漏成交、Ledger 与 Venue 一致，并且只有在所有恢复和安全门禁通过后才重新允许开仓。

## 20. 当前优先改造清单

以下顺序是从当前代码基线到系统逻辑闭环的最短路径。

| 优先级 | 工作 | 交付物 | 最小验收证据 |
|---:|---|---|---|
| P0 | Runtime Recovery 与 Projection | 从 SQLite Ledger 重建 Portfolio、UnifiedRiskView 和运行快照 | 重启后重建结果确定，未完成恢复时不能 READY |
| P0 | Venue Order/Fill Recovery | 按 client order ID、venue order ID 查询状态、open orders 和 fill history | SUBMITTING/UNKNOWN/CANCELLING 均有明确恢复结果，未知时不重提 |
| P0 | Reconciliation READY Gate | Ledger、Position、Balance、Open Order 的启动对账 | matched 才 READY；mismatch 进入 UNKNOWN_EXTERNAL_STATE 或 REDUCE_ONLY |
| P0 | 真实 connector Durable Ingestion | Binance/IBKR WebSocket 与 REST backfill 接入事务 ingestion | 重复事件幂等、cursor 可恢复、断线不丢成交 |
| P1 | Ledger Repository 收敛 | SQLite 成为运行时 Ledger 唯一事实源，JSON 仅作迁移/导出 | CLI 与 Runtime 均从 SQLite 恢复；旧 JSON 可一次性迁移 |
| P1 | DataProductContract | 合并 Dataset 定义和动态 Provider 配置 | 一个 Product 只有一个编译后 Spec，Catalog health 可验证 |
| P1 | Typed Quality Profiles | Quote、Trade、Market Event、Option Snapshot、Feature、Reference 质量规则 | 质量等级由 Engine 计算，调用方不能任意声明 Q3/Q4 |
| P1 | Dataset/Surface 迁移 | DatasetRepository 内部化，Surface 转 Feature Release | 正式消费者不再直接使用旧 Repository 或物理目录 |
| P2 | Reference 与 Failure Policy | SPXW reference pipeline、全部 crash-window 场景 | L2/L3 自动化通过，固定 input/output/audit hash |
| P2 | Paper/Testnet Soak | 24–72 小时运行、重连、重启、Kill Switch 演练 | L4 报告无未解释 UNKNOWN、账务差异或 Critical Alert |

每项工作开始前应明确将删除的旧路径；完成时必须同时提交代码、迁移证据、测试和文档更新。只增加新实现而保留平行旧入口，不视为完成。

## 21. 后续使用方式

后续实施建议以本文 EPIC 和 Phase 为基础拆分任务。每个改造 PR 必须说明：

- 属于哪个 EPIC/Phase；
- 改变了哪个模块边界；
- 删除了哪条旧路径或是否引入兼容层；
- 数据或状态迁移方案；
- 新增的自动化验收证据；
- 回退方式；
- 是否改变 Golden Pipeline 或审计 hash。

任何新的模块、Repository、数据入口、CLI 顶级命令或持久化目录，都必须先证明现有目标架构无法承载，再更新本文，而不是直接增加新的平行路径。
