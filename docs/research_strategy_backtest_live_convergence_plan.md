# Trader 产品形态与研究、策略、回测、实盘贯通改造方案

状态：Proposed  
日期：2026-07-17  
适用范围：整个项目，重点包括 `trading.data`、`trading.research`、`trading.features`、`trading.strategies`、`trading.backtest`、`trading.application`、`trading.execution`、CLI、`examples/`、`research/` 和 `data/` 中的治理产物。

## 1. 文档目的

本文先审计当前项目的真实产品形态，再定义目标产品和用户场景，最后给出围绕这些场景的项目调整计划。

本文回答：

1. Research 应该灵活还是固定；
2. Trader 最终应该以什么产品形态被使用；
3. 研究员、策略开发者和交易运维人员分别如何使用系统；
4. 当前设计与目标产品之间有哪些差距；
5. 如何通过一个最简策略建立第一条端到端链路；
6. 因子研究、交易策略、Portfolio/Risk 和 Execution 的边界；
7. 每个阶段如何通过清晰的场景用例验收。

本文不是要求推翻现有系统。项目的数据治理、领域模型、回测组件、执行安全和恢复能力已经具有较好基础。下一阶段的重点，是把现有模块组织成一套可理解、可重复、可晋级的产品工作流。

实施状态和逐场景权威证据见
[`product_acceptance_matrix.md`](product_acceptance_matrix.md)。本地 deterministic 场景 1–8 已形成
一键 acceptance；真实 Binance Testnet/IBKR Paper 继续遵守显式外部 L4 门禁，不能由 fixture 冒充。

## 2. 结论摘要

### 2.1 Research 应该“探索灵活，晋级固定”

Research 不应被完全固定，否则会失去提出假设、尝试新数据、使用新统计方法和快速失败的能力；Research 也不能完全自由，否则研究结论无法复现，因子无法进入回测，回测无法证明运行的是同一套逻辑。

因此 Research 分为两层：

```text
Research Sandbox
  自由探索、Notebook、DataFrame、临时特征、图表、诊断
                  |
                  | 显式 freeze / register
                  v
Governed Research
  固定输入、时间语义、FactorSpec、StudySpec、代码版本、证据和结论
```

灵活的是研究过程；固定的是进入共享、复现、回测和部署边界的产物。

### 2.2 Trader 的目标产品形态

Trader 应被建设为：

> 一个本地优先、可审计、支持从数据探索到策略实盘晋级的量化策略生命周期平台。

它不只是回测库，也不只是交易执行器。它应提供一条完整产品主线：

```text
发现数据
 -> 探索与提出假设
 -> 冻结研究
 -> 注册因子
 -> 构建策略
 -> 可执行回测
 -> 历史模拟运行
 -> Paper/Testnet
 -> Limited Live
 -> 监控、归因和迭代
```

### 2.3 当前最主要的问题

当前项目已经拥有大量正确的模块，但产品工作流仍然分裂：

- 数据使用体验相对统一，策略使用体验不统一；
- 研究可以产出统计结果，但不能自然发布成运行因子；
- 研究中的策略原型可能与正式 Strategy 是两份实现；
- 回测存在多种专用入口和循环；
- 当前 `trade --strategy` 实际是带策略标签的人工订单；
- RunMode 已有设计，但尚未真正组装统一运行内核；
- StrategySpec 描述了策略，却未完整绑定运行代码、因子和参数；
- 系统内部概念丰富，但用户缺少一条明确的“下一步做什么”的主路径。

因此下一阶段不应优先增加更多策略、模型或 Venue，而应先完成一个最小但真实的产品闭环。

## 3. 当前项目审计

### 3.1 当前已经成立的产品能力

### 数据产品

已经较为成熟：

- Dataset Product、不可变 Release、Alias 和质量等级；
- Source、Canonical、Curated、Feature 等数据层次；
- `ResearchDataClient` 统一查询入口；
- point-in-time、`available_time`、冻结 Release 和防隐式联网；
- 数据搜索、描述、准备、查询、冻结、质量检查；
- Market Event、OHLCV、MarketSlice、Option Snapshot、Feature 等质量 Profile。

这部分已经接近一个可供研究使用的数据产品。

### 领域和账务

已经较为扎实：

- Instrument、ProductSpec、ListingDefinition 和 Capability；
- Intent、Order、Execution 和 Ledger；
- Portfolio、Risk、Pricing、Volatility 和生命周期事件；
- 多资产、多账户和策略虚拟持仓归属。

### 回测和运行安全

已经具备较多模块能力：

- 确定性 Clock、Feed、Fill Model 和审计 hash；
- Catalog-backed、Ledger-backed 回测；
- Runtime Store 和持久订单状态机；
- Readiness、Reconciliation、Recovery 和 Kill Switch；
- simulated、IBKR、Binance 等 Adapter；
- Golden、failure matrix 和 soak 验收基础。

### 策略治理

已经有正确方向：

- StrategySpec；
- EconomicIntent；
- ExecutionPolicy；
- Strategy Registry 和 Lifecycle；
- Research Validation 和 Promotion Gate。

### 3.2 当前产品体验的断点

### 断点一：Research 的出口不明确

用户可以通过 Notebook、`research/<study>`、`trading.research.features`、`trading.features` 等多种方式计算特征，但没有统一回答：

- 哪些只是临时研究列；
- 哪些是可复现因子；
- 如何把研究代码冻结成因子；
- 哪个因子版本被某个策略使用；
- 在线因子状态如何恢复；
- batch 和 incremental 是否一致。

### 断点二：策略原型与正式策略之间需要人工翻译

研究目录可以直接计算 `strategy_pnl`，正式回测则运行 `Strategy.on_market()`。如果二者不是同一个 Strategy Model，就会出现：

```text
研究证明的是 A
回测实现的是 A'
实盘运行的可能又是 A''
```

系统目前没有把这种语义漂移作为强制失败条件。

### 断点三：回测入口并未完全统一

当前至少存在：

- 通用期权 `BacktestEngine`；
- reference scenario；
- SMA 专用回测函数；
- research 中的 trade proxy 或收益模拟；
- canonical replay 示例。

这些能力各有价值，但产品上没有清楚区分：

- 信号研究；
- trade proxy；
- executable backtest；
- historical simulation。

### 断点四：当前实盘入口没有运行策略

当前 `trade run --strategy ...` 仍要求用户提供 instrument、side、quantity 和 price，然后直接构造 OrderRequest。

它验证了执行和运行安全链路，但不等于自动策略实盘。产品上应明确拆成：

```text
人工订单运维工具
自动策略运行工具
```

### 断点五：RunMode 仍主要是架构声明

项目已经定义 Research、Backtest、Historical Simulation、Live Paper 和 Live 的组合差异，但尚未让这些组合真正构造同一个 Strategy Run Loop。

所以“只替换 Event Source、Clock 和 Execution Driver”仍是目标，而不是强制成立的事实。

### 断点六：治理产物和运行对象绑定不足

StrategySpec 已经描述 features、signal、entry/exit 和 execution capability，但仍需要显式绑定：

- Strategy implementation；
- FactorSpec 和具体版本；
- 参数；
- 代码 hash；
- 数据能力；
- execution policy；
- promotion evidence；
- 兼容 RunMode。

否则 Registry 管理的是策略描述，而 Runtime 加载的可能是另一份实现。

### 断点七：CLI 是功能集合，不是用户旅程

当前 CLI 包含大量强大命令，但用户需要自己知道它们的组合顺序。例如：

```text
data search/prepare/freeze
research readiness/governance-audit
backtest run/validate/replay
trade run
runtime golden/failure-matrix/l4-preflight
```

这些命令更多按模块组织，而不是按“开发一个策略并逐步部署”的产品旅程组织。

## 4. 目标产品形态

### 4.1 产品定位

Trader 面向个人或小型策略团队，提供本地优先的研究、验证、模拟和交易运行环境。核心价值不是替代 Notebook，而是保证一个研究想法在离开 Notebook 后，不发生数据、因子、策略和执行语义漂移。

产品由五个工作区组成：

```text
Data Workspace
Research Workspace
Strategy Workspace
Run Workspace
Operations Workspace
```

### Data Workspace

用户完成：

- 搜索已有数据；
- 查看覆盖、质量和 lineage；
- 规划和获取缺失数据；
- 冻结研究输入；
- 比较 Release。

### Research Workspace

用户完成：

- 创建研究；
- 在 Sandbox 中探索；
- 定义 hypothesis、label 和 factor；
- 运行样本内、样本外和稳健性分析；
- 冻结 Study Release；
- 注册可运行 Factor Release。

### Strategy Workspace

用户完成：

- 创建 Strategy Candidate；
- 绑定 Factor Release；
- 定义 signal、universe、portfolio construction、entry/exit；
- 运行 trade proxy 和 executable backtest；
- 生成 Strategy Release；
- 查看晋级状态和缺失证据。

### Run Workspace

用户使用同一个 Strategy Release 运行：

- Backtest；
- Historical Simulation；
- Paper/Testnet；
- Live。

### Operations Workspace

用户完成：

- readiness 和 reconciliation；
- 运行状态、订单、成交和策略决策检查；
- restart/recovery；
- kill switch；
- live capture/replay；
- PnL、signal 和 execution attribution。

### 4.2 产品的核心对象

目标产品只向用户暴露少量稳定对象：

| 对象 | 作用 |
|---|---|
| Dataset Release | 固定数据输入 |
| Study Release | 固定研究问题、方法和证据 |
| Factor Release | 可在 batch/replay/live 中计算的因子 |
| Strategy Release | 可运行的策略版本 |
| Run | 某 Strategy Release 在某 RunMode 下的一次执行 |
| Run Artifact | 输入、输出、hash、日志、指标和验收证据 |

内部可以保留丰富模块，但用户主路径应围绕这些对象，而不是围绕 Python 包目录。

### 4.3 推荐的产品入口

保留 Python API 供研究使用，同时将 CLI 调整为产品旅程：

```text
trader data ...
trader study ...
trader factor ...
trader strategy ...
trader run ...
trader ops ...
trader order ...
```

建议的关键命令：

```bash
trader study create <study-id>
trader study freeze <study-id>
trader factor register <study-id> --factor <factor-id>
trader strategy create <strategy-id>
trader strategy validate <strategy-id>
trader strategy promote <strategy-id> --to <stage>
trader run backtest <strategy-release>
trader run simulate <strategy-release>
trader run paper <strategy-release>
trader run live <strategy-release> --confirm-live
trader run inspect <run-id>
trader ops reconcile <run-id>
trader ops replay <run-id>
trader order submit ...
```

`order submit` 明确表示人工订单；`run paper/live` 才表示持续运行自动策略。

## 5. Research 的灵活性设计

### 5.1 Sandbox：允许灵活

Sandbox 应允许：

- Notebook、Python 脚本、Pandas、Polars、DuckDB；
- 临时 join、临时列和可视化；
- 快速尝试不同窗口、模型和统计方法；
- 使用临时 synthetic fixture 验证机制；
- 失败研究和未完成研究；
- 不稳定 API，只要不进入治理边界。

Sandbox 的硬约束只应包括：

- 正式数据必须通过 `ResearchDataClient`；
- 不应直接依赖 Provider SDK 对象；
- 时间字段和时区必须明确；
- corrected/final data 必须显式选择；
- 不允许把 forward label 当作在线 feature；
- 不允许将 Sandbox 结果直接部署。

### 5.2 Governed Research：必须固定

研究一旦要满足以下任一条件，就必须 freeze：

- 与他人共享正式结论；
- 作为 StrategySpec 的证据；
- 进入回测或策略晋级；
- 注册为 Factor；
- 消耗 test window；
- 支持资金或部署决策。

Freeze 后必须记录：

- Dataset Release ID 和 content hash；
- StudySpec；
- Universe；
- 时间窗口和 split；
- primary/available time 语义；
- 因子公式、参数和代码 hash；
- label 定义；
- 数据处理和缺失值规则；
- 统计方法；
- 结果、限制和允许声明；
- test window 使用情况；
- 环境和依赖版本。

### 5.3 固定的是契约，不是研究方法

平台不应强制所有研究使用同一种统计模型。它应固定：

- 输入身份；
- 时间语义；
- 输出格式；
- 证据等级；
- 晋级规则；
- 可复现性。

研究员仍然可以使用线性回归、bootstrap、树模型、神经网络、事件研究或领域专用方法。

## 6. 角色与核心使用场景

### 6.1 角色 A：因子研究员

目标：判断一个因子是否有稳定的预测或解释能力。

典型旅程：

```text
搜索数据
 -> 冻结输入 Release
 -> 创建 Study
 -> Sandbox 探索
 -> 定义 Factor 和 Label
 -> 运行 OOS/Robustness
 -> Freeze Study
 -> Register Factor
```

成功输出：Factor Release 和 Study Release，而不是交易订单。

### 6.2 角色 B：策略开发者

目标：把一个或多个因子映射为经济持仓。

典型旅程：

```text
选择 Factor Release
 -> 创建 Strategy Candidate
 -> 定义 signal/universe/position/entry/exit
 -> Trade Proxy
 -> Executable Backtest
 -> Robustness
 -> Strategy Release
```

成功输出：Strategy Release，而不是 Notebook 中的一条累计收益曲线。

### 6.3 角色 C：交易运维人员

目标：安全、持续地运行已经批准的 Strategy Release。

典型旅程：

```text
检查 Strategy Lifecycle
 -> Preflight
 -> Historical Simulation
 -> Paper/Testnet
 -> Reconciliation/Soak/Restart Drill
 -> Limited Live
 -> Monitor/Replay/Kill Switch
```

运维人员不修改因子、signal 或策略腿结构。

### 6.4 角色 D：项目维护者

目标：维护数据、Adapter、运行时和治理契约。

维护者关注：

- 数据和 Reference 质量；
- Adapter capability；
- Runtime recovery；
- 跨模式 parity；
- Artifact schema migration；
- 架构边界测试。

## 7. 清晰的场景用例

### 场景一：探索一个新因子，但不进入策略

用户目标：研究 BTC 1h 的短期趋势是否对下一时段收益有解释力。

操作：

```bash
trader data search --dimension instrument=BTC-USDT --dimension frequency=1h
trader data freeze --dataset market.ohlcv.crypto.binance.btc-usdt.1h \
  --start 2024-01-01T00:00:00Z --end 2026-01-01T00:00:00Z
trader study create btc-short-trend-v1 --input <snapshot-id>
```

用户在 Notebook 中自由尝试 SMA、EMA、momentum、不同窗口和统计方法。

预期结果：

- 可以快速探索；
- 未 freeze 前不产生正式 Factor；
- 不能被 Strategy Release 引用；
- 不允许直接进入 paper/live。

### 场景二：将一个研究因子正式化

用户目标：把经过样本外验证的 SMA 差值注册为可运行因子。

操作形态：

```bash
trader study freeze btc-short-trend-v1
trader factor register btc-short-trend-v1 --factor sma-spread-v1
trader factor verify sma-spread-v1 --mode batch,replay
```

预期结果：

- 生成 FactorSpec、implementation hash 和 Factor Release；
- batch 与 incremental replay 在同一输入上的输出一致；
- forward return 只保留在 Study 中，不进入 FactorSnapshot；
- 因子可以被策略引用。

### 场景三：使用因子构建最简策略

用户目标：当 fast SMA 高于 slow SMA 时持有 BTC，否则空仓。

操作形态：

```bash
trader strategy create sma-cross-v1 --factor sma-spread-v1
trader strategy validate sma-cross-v1 --stage trade-proxy
trader run backtest sma-cross-v1@candidate
```

预期结果：

- Strategy 只输出 `TargetPositionIntent`；
- 仓位规模由 approved capital 和 Portfolio Governance 决定；
- 费用、滑点和 next-bar fill 属于 ExecutionPolicy/Fill Model；
- 研究、回测不再维护独立的策略收益循环。

### 场景四：历史模拟验证正式 Runtime

用户目标：证明策略在正式持久化运行时中可以停止和恢复。

操作形态：

```bash
trader run simulate sma-cross-v1@1.0.0 --dataset <release-id>
trader run inspect <run-id>
trader ops restart-drill <run-id>
```

预期结果：

- 使用 Replay Event Source 和 Replay Clock；
- 使用正式 Strategy Runtime、Risk、Order State、Execution Ingestion 和 Ledger；
- 只替换为 Simulated Execution Driver；
- 与 Backtest 在 execution boundary 前的 decision/intent hash 一致；
- 中断恢复后结果与连续运行一致。

### 场景五：将同一策略运行在 Testnet/Paper

用户目标：在 Binance testnet 或 IBKR paper 中持续运行 SMA Strategy Release。

操作形态：

```bash
trader run preflight sma-cross-v1@1.0.0 \
  --mode paper --venue binance --account default
trader run paper sma-cross-v1@1.0.0 \
  --venue binance --account default --duration 24h
```

预期结果：

- 用户不提供 side、quantity 和 limit price；
- Runtime 持续消费实时 Canonical Event；
- Factor Runtime 在线更新；
- Strategy 产生 EconomicIntent；
- Portfolio/Risk 决定允许规模；
- ExecutionPolicy 和 Adapter 负责订单；
- 决策、订单、成交、Ledger 和 reconciliation 可关联查询。

### 场景六：回放一次 Paper 决策

用户目标：解释为什么某时刻买入，以及离线能否复现。

操作形态：

```bash
trader run inspect <run-id> --at 2026-07-17T08:00:00Z
trader ops replay <run-id> --until 2026-07-17T08:00:00Z
```

预期结果：

- 展示输入 event IDs；
- 展示 fast/slow SMA 和 Factor state hash；
- 展示 Portfolio、working orders 和 approved capital；
- 展示 StrategyDecision 和 EconomicIntent；
- 离线 replay 产生相同 decision/intent hash；
- 如果真实成交不同，只归因于 Execution Driver 和外部 Venue 状态。

### 场景七：人工提交运维订单

用户目标：在策略之外执行一个明确的人工减仓或测试订单。

操作形态：

```bash
trader order submit --venue binance --environment testnet \
  --instrument crypto:binance:spot:BTCUSDT --side sell --quantity 0.001
```

预期结果：

- 不伪装成自动策略运行；
- 必须提供 actor、reason 和 correlation；
- 仍经过 readiness、risk、order state、recovery 和 ledger；
- 可以标记为 manual/operations intent。

### 场景八：研究一个复杂期权策略

用户目标：研究 SPXW skew 并形成 Bull Put Spread 策略。

正确流程：

```text
研究 skew 的预测能力
 -> 注册 point-in-time skew Factor
 -> Strategy 负责期限、短腿 delta、宽度、入退出和结构
 -> Trade Proxy 只证明经济映射
 -> Executable Backtest 使用同步多腿 Quote 和正式 Fill Model
 -> Historical Simulation 使用正式 Runtime
```

预期结果：研究代码不再拥有另一份与正式 Strategy 不同的 `_simulate_spread_trade` 作为最终策略证据。

## 8. 因子研究与交易策略的边界

### 8.1 因子研究

因子研究回答：

> 截至某个时刻可见的数据，能否计算出一个对未来状态具有稳定解释或预测能力的变量？

负责：

- 数据、Universe 和时间语义；
- 因子公式、参数、窗口和 warm-up；
- 缺失、异常值和标准化；
- 研究 label；
- IC、条件效应、分组、衰减和稳定性；
- OOS、walk-forward 和 robustness；
- batch/incremental parity；
- FactorSpec 和 Factor Release。

不负责：

- 买卖多少；
- 当前是否已有仓位或工作订单；
- 入场、止损、止盈和再平衡状态机；
- 账户资本分配；
- 订单类型、Venue 和成交；
- Ledger。

### 8.2 交易策略

交易策略回答：

> 给定当前因子、市场、策略持仓、工作订单和风险预算，现在希望持有什么经济风险？

负责：

- 引用哪些 Factor Release；
- 因子如何组合为 signal；
- 阈值、去抖和状态机；
- Universe 中选择哪些合约；
- 目标 exposure、权重或结构；
- 入场、退出、再平衡和 hedge requirement；
- 输出 EconomicIntent；
- 记录 StrategyDecision。

不负责：

- 直接读取供应商 SDK 或物理文件；
- 在策略内部重新定义正式因子；
- 修改 Ledger/Portfolio；
- 直接下 Venue Order；
- 绕过资本和风险门禁。

### 8.3 其他模块边界

| 问题 | 所属模块 |
|---|---|
| 这个数值如何从当前可见数据计算？ | Factor Runtime |
| 这个数值是否有预测能力？ | Factor Research |
| 当前希望持有什么 exposure？ | Strategy |
| 策略允许使用多少资本？ | Portfolio Governance |
| 是否超过风险限制？ | Risk |
| 如何转换为 Order/Combo？ | Execution Planner |
| 使用 maker、taker 还是拆腿？ | ExecutionPolicy |
| 如何提交、恢复和对账？ | Execution Driver/Operations |
| 成交后现金和持仓如何变化？ | Ledger/Portfolio Projection |

## 9. 目标架构

### 9.1 统一运行主干

```text
EventSource
   -> Canonical Event
   -> Market Projection
   -> Valuation Projection
   -> Factor Runtime
   -> Strategy Context Builder
   -> Governed Strategy Runtime
   -> EconomicIntent
   -> Portfolio Governance
   -> Risk Gate
   -> Execution Planner
   -> Execution Driver
   -> Ack/Reject/Fill/Cancel
   -> Runtime Store + Ledger + Portfolio
   -> Monitoring / Next Decision
```

### 9.2 RunMode 只替换外围组件

| 模式 | Event Source | Clock | Execution | Persistence | 主要用途 |
|---|---|---|---|---|---|
| Research | Frozen Release/Batch Query | Analysis | None/Label Proxy | Study Artifact | 预测证据 |
| Backtest | Frozen Release | Replay | Fill Model | Backtest Artifact | 快速经济与执行验证 |
| Historical Simulation | Frozen Canonical Replay | Replay | Simulated Venue | Runtime Store | 正式 Runtime 验证 |
| Paper/Testnet | Live Canonical Feed | System | Paper/Testnet Venue | Runtime Store | 外部联调和 soak |
| Live | Live Canonical Feed | System | Live Venue | Runtime Store | 真实交易 |

FactorSpec、StrategySpec、Strategy implementation、Intent 和 Risk 语义保持不变。

### 9.3 核心运行对象

建议新增或收敛：

```text
FactorSpec
FactorSnapshot
FactorRuntime
FactorRegistry

StrategyRelease
StrategyFactory
StrategyContextBuilder
GovernedStrategyRuntime

StrategyRunLoop
RunComposition
RunManifest
RunRepository
```

## 10. 项目调整计划

### Phase 0：产品和术语收敛

目标：让用户知道每类入口解决什么问题。

工作：

1. 明确 Sandbox Research 与 Governed Research；
2. 明确 Signal Study、Trade Proxy、Executable Backtest、Historical Simulation；
3. 将人工订单和自动策略运行分开命名；
4. 定义 Dataset/Study/Factor/Strategy/Run 六个产品对象；
5. 为现有 CLI 建立旧命令到新产品入口的映射；
6. 暂不立即删除兼容命令。

验收用例：新用户只阅读一个 Quickstart，就能从数据搜索走到 SMA Historical Simulation，并能解释每一步产物。

### Phase 1：因子产品化

目标：研究因子可以安全进入 replay/live。

工作：

1. 建立通用 `FactorSpec` 和字典式/类型化 `FactorSnapshot`；
2. 建立有状态 `FactorRuntime` 和恢复契约；
3. 建立 Factor Registry/Release；
4. 为 SMA 实现 batch 和 incremental；
5. 建立 point-in-time、warm-up、missing/stale 状态；
6. 建立 batch/replay parity test；
7. 将当前期权 `FeatureSnapshot` 迁移为一个或多个 Factor bundle，而不是全局固定 schema。

验收用例：场景一、二。

### Phase 2：最简正式策略

目标：SMA 成为真正的 Strategy Model，而不是专用回测函数。

工作：

1. 实现 `SmaCrossStrategy`；
2. 输出 `TargetPositionIntent`；
3. 建立 Position Sizer/Portfolio Governance；
4. 将手续费、滑点、next-bar fill 移至 ExecutionPolicy/Fill Model；
5. 建立 StrategyFactory 和 StrategyRelease；
6. 扩展 GovernedStrategyRuntime 覆盖完整生命周期；
7. 固定 context/decision/intent hash。

验收用例：场景三。

### Phase 3：统一 Strategy Run Loop

目标：回测和 Runtime 共享同一个决策主干。

工作：

1. 从 `BacktestEngine` 抽取 Market -> Factor -> Strategy -> Intent -> Risk 主循环；
2. 将 Backtest Fill Model 变为 Execution Driver；
3. 使用统一 Context Builder；
4. 所有 Run 保存统一 RunManifest；
5. 记录 event、factor、decision、intent、order、fill hash 链；
6. 迁移 SMA 回测；
7. 保留旧 Backtest API 作为兼容 facade，内部委托新 Run Loop。

验收用例：场景三，以及 batch/replay/backtest decision parity。

### Phase 4：Historical Simulation

目标：建立回测到实盘之间的正式桥梁。

工作：

1. 用 Frozen Canonical Event 驱动正式 Run Loop；
2. 接入 SimulatedExecutionAdapter；
3. 使用 SQLite Runtime Store、Durable Order State 和 Execution Ingestion；
4. 所有 Fill 进入正式 Ledger；
5. 建立 pause/restart/recovery；
6. 建立 Backtest/Simulation execution-boundary parity。

验收用例：场景四。

### Phase 5：自动策略 Paper/Testnet

目标：当前实盘基础设施真正运行 Strategy Release。

工作：

1. 新增 `trader run paper` 或等价 Application 入口；
2. 加载 StrategyRelease/FactorRelease；
3. 接入实时 Canonical Event Source；
4. 接入正式 Portfolio/Risk/Coordinator；
5. 将当前 `trade run` 调整为 `order submit` 兼容入口；
6. 保存 live canonical capture；
7. 建立 live/replay parity；
8. 完成 24h/72h soak、restart 和 kill-switch drill。

验收用例：场景五、六、七。

### Phase 6：迁移复杂策略

建议顺序：

1. Spot/Perpetual Carry；
2. Covered Call；
3. Bull Put Spread；
4. BTC Iron Condor；
5. 多策略组合。

复杂期权策略需要补充：

- point-in-time Universe Runtime；
- 同步多腿 Quote；
- Greeks/Surface Factor Release；
- native combo/legging ExecutionPolicy；
- settlement/lifecycle replay；
- 研究 trade proxy 与正式 Strategy parity。

验收用例：场景八。

### Phase 7：产品体验和治理收尾

工作：

1. `run inspect` 展示完整决策链；
2. 提供策略晋级缺口报告；
3. 提供统一 Quickstart 和项目模板；
4. 清理重复 research/backtest 模拟实现；
5. 废弃旧命令和旧 artifact schema；
6. 提供失败研究、策略暂停和版本回滚体验；
7. 加入 signal、portfolio、execution 三层 PnL attribution。

## 11. 第一条最简策略的具体范围

第一条贯通链路使用：

```text
数据：BTCUSDT 1h OHLCV
因子：fast SMA、slow SMA、spread
策略：fast > slow 时目标 100% long，否则 0%
Intent：TargetPositionIntent
风险：单策略资本上限、最大目标 notional
回测成交：下一根 Bar open
模拟成交：SimulatedExecutionAdapter
外部环境：Binance testnet
```

明确不包含：

- 做空和杠杆；
- 多资产；
- 多策略净额；
- order book；
- maker 策略；
- 期权；
- 机器学习；
- 收益有效性证明。

第一条链路证明的是产品和架构闭环，不是 SMA 的投资价值。

## 12. 跨场景验收矩阵

| 能力 | Research | Backtest | Historical Simulation | Paper/Testnet | Live |
|---|:---:|:---:|:---:|:---:|:---:|
| 同一 Dataset/Canonical Schema | 是 | 是 | 是 | Capture 后可比 | Capture 后可比 |
| 同一 FactorSpec | 是 | 是 | 是 | 是 | 是 |
| 同一 Strategy implementation | 候选/正式 | 是 | 是 | 是 | 是 |
| 同一 decision/intent 语义 | Trade Proxy 起 | 是 | 是 | 是 | 是 |
| Runtime Store | 否 | 可选 | 是 | 是 | 是 |
| 正式 Ledger ingestion | 否 | 语义一致 | 是 | 是 | 是 |
| Execution Driver | Label Proxy | Fill Model | Simulated | Paper/Testnet | Live |
| Replay parity | Factor | 必须 | 必须 | Capture 后必须 | Capture 后必须 |
| Recovery/Reconciliation | 否 | 否 | 必须 | 必须 | 必须 |

## 13. 完成定义

项目可以认为第一阶段真正打通，必须满足：

1. 新用户可以按单一 Quickstart 完成 SMA 的研究、注册、回测和历史模拟；
2. 至少一个 Factor Release 同时支持 batch、replay 和 live incremental；
3. 至少一个 Strategy Release 同时运行于 Backtest、Historical Simulation 和 Paper/Testnet；
4. 三种模式使用同一个 Strategy implementation；
5. 进入 Execution Driver 之前产生相同的 factor、decision 和 intent hash；
6. Historical Simulation 和 Paper 的成交进入正式 Order State、Execution Ingestion、Ledger 和 Portfolio；
7. Paper captured events 可以离线 replay 并复现决策；
8. Runtime 可以停止、恢复、对账和 fail closed；
9. 人工订单入口与自动策略入口明确分离；
10. 研究中的最终策略证据不再依赖另一份重复收益模拟实现。

最终产品完成定义是：

> 用户从一个灵活的研究假设开始，将结果冻结为 Factor Release，构建并发布 Strategy Release；随后只改变 RunMode，就可以在回测、历史模拟、paper/testnet 和 live 中运行，并能对任意一次决策完成输入、因子、策略、风险、订单、成交和账务的完整解释与回放。

## 14. 近期不建议优先扩展的方向

在 SMA 垂直链路完成之前，不建议把主要精力投入：

- 新增更多孤立策略；
- 新增更多无法在线运行的因子；
- 新增另一套回测引擎；
- 新增更多一次性 CLI；
- 扩展复杂期权模型作为主任务；
- 增加更多 Venue；
- 继续写治理文档但不建立跨模式自动化证据。

当前系统不缺零件。下一阶段最重要的产品成果，是一条用户能够实际操作、系统能够自动验证、策略能够持续晋级的完整生产线。
