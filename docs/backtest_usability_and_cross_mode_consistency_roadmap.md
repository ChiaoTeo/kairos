# Trader 便捷回测与跨模式一致性建设路线图

状态：Proposed  
日期：2026-07-19  
适用范围：`trading.data`、`trading.features`、`trading.strategies`、`trading.backtest`、`trading.application`、`trading.market_data`、`trading.execution`、CLI、Examples 和运行验收体系

## 1. 文档目的

本文定义 Trader 下一阶段的两条产品主线：

1. 为策略开发者提供便捷、统一、低样板代码的回测开发体验；
2. 系统化管理研究、回测、历史模拟、Live Paper 和 Live 之间的数据与运行差异。

本文不是要求让所有运行模式得到完全相同的收益或成交结果。目标是：

> 策略在不同模式中复用相同的数据契约、特征状态转换、策略实现和经济意图；不可避免的数据与执行差异必须被记录、测量、回放、校准并纳入晋级门禁。

本文用于指导后续实施、任务拆分和验收。完成状态以代码、自动化测试、可运行 Example 和审计 Artifact 为准，不以类或接口已经存在为准。

## 2. 用户目标

### 2.1 便捷开发回测

策略开发者应当能够：

- 用很少的样板代码定义策略；
- 通过 Dataset Release、时间范围和参数直接运行回测；
- 不需要手工组装 Catalog、Feed、Factor Runtime、Strategy Runtime、Portfolio 和 Fill Model；
- 在 Python API、CLI 和 Notebook 中得到一致行为；
- 快速查看交易、权益、回撤、归因和数据质量；
- 从单次试跑自然升级到参数扫描、Walk-forward、历史模拟和 Paper；
- 在晋级时继续使用同一份 Factor 和 Strategy 实现，而不是重写策略。

目标开发体验：

```python
from trading import Trader

result = Trader("./data").backtest(
    strategy="sma-cross-v1@1.2.0",
    dataset="market.binance.btcusdt.1h@2026-07-01",
    start="2024-01-01",
    end="2026-01-01",
    capital=100_000,
    parameters={"fast": 20, "slow": 60},
    execution="bar-close-conservative",
)

result.summary()
result.trades()
result.equity()
result.explain(at="2025-03-01T08:00:00Z")
```

对应 CLI：

```bash
trader backtest run \
  --strategy sma-cross-v1@1.2.0 \
  --dataset market.binance.btcusdt.1h@2026-07-01 \
  --start 2024-01-01 --end 2026-01-01 \
  --capital 100000 \
  --param fast=20 --param slow=60 \
  --execution bar-close-conservative
```

### 2.2 支持跨模式一致性

系统应当明确回答：

- Study、回测、模拟、Paper 和 Live 分别使用了什么数据；
- 不同来源是否具有相同字段定义、时间语义和聚合规则；
- 同一批 Canonical Event 是否产生相同 Factor、Decision 和 EconomicIntent；
- Live 当时看到的数据能否被完整 Capture 并确定性 Replay；
- 历史 Provider 数据与 Live Capture 存在哪些统计差异；
- 回测 Fill Model 与真实成交的偏差有多大；
- 哪些差异在允许范围内，哪些差异会阻止晋级或触发运行降级。

## 3. 当前基线

项目已经具备以下基础：

- Dataset Product、不可变 Release、content hash 和 point-in-time 查询；
- `CanonicalEventEnvelope` 及 `event_time`、`receive_time`、`available_time`；
- Frozen Release 的确定性 Replay；
- `FactorRuntime`、`GovernedStrategyRuntime` 和 `GovernedStrategyRunLoop`；
- Factor、Decision、Intent 和 Audit Hash；
- Historical Simulation、Simulated Venue、Runtime Store、Ledger 和重启恢复；
- Raw/Canonical Capture 和 Capture Replay；
- Research、Backtest、Historical Simulation、Live Paper、Live 的 `RunModeComposition`；
- SMA 主链的 batch/replay/execution-boundary parity；
- 期权和多资产领域模型、专用回测与运行能力。

当前主要缺口：

1. 回测入口仍然分散，开发者需要理解过多内部组件；
2. SMA 主链已经贯通，但其他策略尚未全部收敛到统一运行协议；
3. 研究 DataFrame、旧 BacktestEngine 和正式 Strategy Runtime 仍可能形成多份实现；
4. Canonical Schema 统一不等于不同 Provider 的经济语义等价；
5. 缺少历史 Release 与 Live Capture 的自动等价性报告；
6. Bar、MarketSlice、Option Snapshot 等在线/离线 Builder 尚未全部共享实现；
7. Live Paper 的本地验收偏重 Capture Replay，真实持续流、背压、迟到事件和恢复证据不足；
8. Fill Model 已有 `ExecutionCalibrationRelease` 产物入口，回测 Artifact 已可绑定校准 release；真实外部订单样本与持续校准闭环尚未完成；
9. 缺少统一的 Shadow → Paper → Limited Live 晋级产品流程。

## 4. 目标架构

### 4.1 单一策略运行内核

所有可部署策略必须经过：

```text
EventSource[CanonicalEvent]
  -> Market State / Projection
  -> FactorRuntime
  -> GovernedStrategyRuntime
  -> StrategyDecision
  -> EconomicIntent
  -> Portfolio / Risk
  -> Execution Driver
  -> Order / Fill / Ledger
```

各模式只允许替换以下组件：

| 组件 | Backtest | Historical Simulation | Paper | Live |
|---|---|---|---|---|
| EventSource | Frozen Release Replay | Frozen Release Replay | Live Canonical Stream | Live Canonical Stream |
| Clock | Replay Clock | Replay Clock | System Clock | System Clock |
| Execution Driver | Fill Model | Simulated Venue | Simulated/Paper Venue | Real Venue |
| Persistence | Run Artifact | Runtime Store | Durable Runtime Store | Durable Runtime Store |
| Capture | 可选 | Canonical | Raw + Canonical | Raw + Canonical |

Factor、Strategy、EconomicIntent 和核心风控定义不得因运行模式而分叉。

### 4.2 两种一致性

系统必须区分：

#### 确定性一致性

相同 Canonical 输入、相同代码和相同参数应产生完全相同的：

- Factor Snapshot；
- Strategy Decision；
- EconomicIntent；
- Audit Hash。

该层原则上使用精确相等。

#### 统计与经济一致性

历史 Provider 和 Live Provider 不会产生完全相同的事件，需要比较：

- 覆盖率和缺失率；
- OHLCV 差异；
- Quote/Trade/Depth 差异；
- 延迟、迟到和乱序；
- symbol、合约和公司行动映射；
- 信号翻转率和目标仓位差异；
- 回测滑点与真实滑点。

该层使用版本化阈值、差异报告和人工审批，不要求逐事件 hash 相同。

## 5. 工作流设计

### 5.1 回测快速路径

默认回测应只要求：

- Strategy Release；
- Dataset Release；
- 时间范围；
- 资金；
- 参数覆盖；
- Execution Profile。

系统自动完成：

1. 解析并冻结 Strategy 和 Dataset；
2. 校验所需数据能力；
3. 校验 Factor Binding 和参数；
4. 构造 Replay Source、Clock、Portfolio、Risk 和 Execution；
5. 运行并持久化 Artifact；
6. 输出摘要、交易、权益、风险、归因和诊断；
7. 提供可复制的 Replay 命令。

### 5.2 高级回测路径

高级用户可以显式覆盖：

- Feed/Projection；
- Commission、Slippage、Latency 和 Fill Model；
- Portfolio/Risk Policy；
- warm-up；
- universe；
- benchmark；
- checkpoint；
- result sink。

高级配置不得迫使普通用户接触内部编排对象。

### 5.3 一条命令晋级

在输入能力满足时，应支持：

```bash
trader strategy promote <strategy-release> --to historical-simulation
trader strategy promote <strategy-release> --to shadow
trader strategy promote <strategy-release> --to paper
trader strategy promote <strategy-release> --to limited-live
```

晋级命令不自动绕过门禁，只负责运行检查、生成报告并在全部满足时发布目标环境部署声明。

## 6. 实施阶段

### M1：统一易用回测产品 API

#### 工作内容

- 新增稳定的 `BacktestRequest`、`BacktestRunner`、`BacktestResultView`；
- 建立 `Trader.backtest()` facade；
- 建立统一 `trader backtest run|inspect|compare|replay` CLI；
- 自动解析 Strategy Release、Factor Binding、Dataset Release 和 Execution Profile；
- 提供结构化配置文件，并允许 CLI 参数覆盖；
- 统一输出 summary、trades、equity、drawdown、exposure、fees 和 attribution；
- 错误信息直接给出缺失能力、失败门禁和下一条建议命令；
- 保留底层 API，但停止为每个策略增加独立回测入口。

#### 验收标准

- SMA 回测的最小 Python 示例不超过 15 行有效代码；
- CLI 在一个命令中完成数据解析、运行和 Artifact 写入；
- Python、CLI 和配置文件运行产生相同 audit hash；
- 同一请求重复运行产生相同 factor/decision/intent hash；
- 结果至少包含收益、回撤、交易、费用、仓位、风险和三层归因；
- 缺少数据时在联网前失败，并输出可执行的数据准备建议；
- 不允许通过 mutable alias 静默改变已保存 Run Artifact 的输入；
- SMA、Bull Put Spread 和一个多资产策略均通过统一入口运行。

### M2：统一策略与回测协议

#### 工作内容

- 定义所有可部署策略必须实现的运行协议；
- 将旧专用回测逐步迁移为统一 Strategy/Factor/Intent 路径；
- 明确 Research Label、Trade Proxy 与 Executable Backtest 的边界；
- 禁止研究目录复制生产策略逻辑后直接作为晋级证据；
- Strategy Release 强制绑定 implementation hash、Factor Release、参数 schema、数据需求和支持模式；
- 建立兼容层和弃用计划，避免一次性删除现有期权回测能力。

#### 验收标准

- 可部署策略不存在第二份回测专用信号实现；
- 参数变化会改变策略语义身份或 Run Artifact 身份；
- 相同输入在 batch factor 与 incremental factor 下满足精确或声明误差内 parity；
- Backtest 与 Historical Simulation 在 execution boundary 前的 factor/decision/intent hash 相同；
- Trade Proxy 输出明确标记 `TRADE_PROXY_ONLY`，不能通过部署门禁；
- 架构测试阻止新增绕过统一运行协议的可部署策略。

### M3：统一在线与离线数据 Builder

#### 工作内容

- 为 Bar、Quote State、OrderBook、MarketSlice、Option Snapshot 建立共享增量 Builder；
- 离线构建通过高速 Replay 调用相同状态转换，不再另写 pandas/SQL 业务语义；
- 对 Bar finalization、迟到事件、修订、session、时区、空窗口建立明确策略；
- Builder 版本写入 Dataset lineage、Capture Manifest 和 Run Artifact；
- 为不同 Provider 建立显式 Decoder，禁止 Provider 特有字段进入策略层。

#### 验收标准

- 相同有序原始事件输入时，离线与在线 Builder 输出完全一致；
- 迟到、重复、乱序、sequence gap、重连和修订均有测试；
- Bar 的 open/close 边界、final 时间和 `available_time` 有版本化定义；
- Builder 升级不会静默覆盖旧 Release；
- Live Capture 可使用相同 Builder 重建相同最终状态和状态 hash；
- 策略只能消费 Canonical Event 或版本化 Market Projection。

### M4：Dataset Equivalence 数据等价性体系

#### 工作内容

- 新增 `DatasetEquivalenceSpec` 和 `DatasetEquivalenceReport`；
- 支持比较 Historical Release、Live Capture 和多个 Provider；
- 建立字段、时间、覆盖、聚合和经济语义检查；
- 针对 Bar、Quote、Trade、OrderBook、Options 定义不同 Profile；
- 生成按时间、instrument 和 session 分组的差异统计；
- 将阈值版本化并绑定 Strategy Data Requirement；
- 报告写入 Dataset/Strategy Promotion Evidence。

#### 最低指标

- 事件/Bar 缺失率和重复率；
- OHLCV 绝对和相对误差；
- close/sign return 差异；
- Quote midpoint/spread 差异；
- sequence gap 和最大静默；
- event/receive/available latency 分布；
- universe、symbol 和合约定义差异；
- Factor Snapshot 差异；
- Decision/Intent disagreement rate。

#### 验收标准

- 一条命令能够比较一个 Frozen Release 和一个 Capture；
- 报告包含输入 hash、比较规范版本、指标、阈值、通过状态和差异样本；
- 关键字段或时间语义不兼容时 fail closed，而不是继续计算相似度；
- 超阈值报告能够阻止 Strategy 晋级；
- 阈值变更产生新规范版本并保留历史结果；
- 至少为 Binance Bar/Trade、Massive Quote 和一个期权数据集建立 Profile。

### M5：持续 Live Capture 与 Replay

#### 工作内容

- 将 Raw + Canonical Capture 设为 Paper/Live 强制能力；
- 统一 session、segment、rotation、retention 和 content hash；
- 保存 decoder、builder、schema、subscription 和 reference snapshot 版本；
- 建立 T+1 Capture 发布流程，使真实线上输入成为后续最权威的回放数据；
- 支持按 Run、时间和 Decision 追溯到 Canonical Event 和 Raw Message；
- 建立自动 replay parity job。

#### 验收标准

- 每个 Paper/Live Run 都能定位到完整 Capture Manifest；
- Capture 缺失、损坏或 hash 不匹配时不能声明运行验收通过；
- 同一 Capture Replay 后 factor/decision/intent/audit hash 与原运行一致；
- rotation、重启和跨 segment replay 不丢事件、不重复消费；
- 24 小时运行的磁盘、内存、channel peak、drop 和 reconnect 指标可审计；
- T+1 发布的数据保留原始 lineage，不能被供应商修订静默替换。

### M6：Shadow、Paper 与 Limited Live 晋级

#### 工作内容

- 新增 Shadow 模式：使用实时数据、计算完整决策，但禁止发单；
- Shadow 输出假设订单和目标仓位，用于与 Replay/Paper 对比；
- 建立 Promotion Gate：Backtest → Historical Simulation → Shadow → Paper → Limited Live → Live；
- Limited Live 支持资本、instrument、时段、订单类型和最大损失限制；
- 每个阶段生成统一 Evidence Bundle；
- 数据、Runtime、Risk、Reconciliation 或 Capture 不健康时自动阻止晋级。

#### 验收标准

- Shadow 与同一 Capture Replay 的 factor/decision/intent hash 相同；
- Paper 的计划订单与 Shadow 的假设订单差异可解释；
- 连续运行、重启恢复、断线重连、迟到事件和背压测试通过；
- Limited Live 无法突破声明的资本、仓位、订单和损失边界；
- Kill Switch、reduce-only、reconciliation 和 unresolved order drill 形成 Artifact；
- 未满足前一阶段门禁时不能激活后一阶段。

### M7：执行模型校准闭环

#### 工作内容

- 保存决策时行情、计划订单、提交时间、Ack、Fill、Cancel 和 Ledger 全链路；
- 计算真实 latency、slippage、fill ratio、partial fill、cancel success 和 fee；
- 按 Venue、instrument、订单类型、规模、波动率和流动性分桶；
- 从真实成交生成版本化 `ExecutionCalibrationRelease`；本地 Runtime Store 生成机制已落地，真实外部样本待 L4；
- Fill Model 显式绑定 Calibration Release；本地机制已记录到 Backtest Run Artifact，真实外部样本待 L4；
- Backtest 报告同时展示未校准结果和校准后结果；当前已落地按校准平均 `fee_bps` 的权益对比，slippage/latency/partial fill 参数化待补。

#### 验收标准

- 任一真实 Fill 可追溯到 Decision、Intent、Order 和当时 Capture；
- Calibration Release 具有时间窗口、样本数、适用范围、hash 和质量等级；
- 回测 Artifact 记录 Fill Model、Calibration Release 与校准后权益对比；本地验收已覆盖，真实外部 release 待补；
- 真实执行分布显著漂移时告警并阻止使用过期校准晋级；
- 至少完成一个 Binance/Testnet 或 IBKR Paper 的端到端校准案例；
- 校准改善使用独立留出窗口验证，不能只在拟合样本上证明。

### M8：开发者体验与批量实验

#### 工作内容

- 支持 parameter grid、随机搜索和可插拔优化器；
- 支持 development/validation/test 和 walk-forward；
- 支持并行运行、缓存和失败恢复；
- 以 request hash 去重相同实验；
- 提供 Notebook 友好的 Result API 和图表；
- 提供策略脚手架、类型检查和本地快速 fixture；
- 建立从快速 fixture 到正式 Frozen Release 的显式切换。

#### 验收标准

- 参数扫描不会重复读取或转换相同冻结输入；
- 中断后可以恢复未完成实验；
- development/validation/test 参数冻结规则被强制执行；
- fixture 结果带醒目标记，不能作为策略晋级证据；
- Notebook、CLI 和批量运行使用同一 BacktestRequest；
- 示例覆盖单资产 Bar、事件驱动、期权组合和多资产组合。

## 7. 统一验收矩阵

| 层级 | 必须相同或受控的内容 | 验收证据 |
|---|---|---|
| Study → Factor | 定义、输入 Release、时间语义、batch/incremental | Factor parity report |
| Factor → Backtest | Factor Release、Strategy Release、参数 | Run Artifact/hash |
| Backtest → Historical Simulation | execution boundary 前决策；执行差异显式 | Boundary parity report |
| Historical Simulation → Shadow | 同一 Capture Replay 决策一致 | Capture replay report |
| Shadow → Paper | 数据和决策一致；订单模拟差异可解释 | Shadow/Paper comparison |
| Paper → Limited Live | Runtime、风险、恢复、对账、Capture 健康 | Promotion evidence bundle |
| Limited Live → Live | 真实执行和风险在批准阈值内 | Live readiness approval |
| Live → Future Backtest | Capture 可发布、可回放、可校准 | T+1 release/calibration |

## 8. 非目标和禁止做法

### 非目标

- 不要求历史数据和实时数据逐事件完全相同；
- 不要求回测、Paper 和 Live 收益完全相同；
- 不以一个万能 Fill Model 覆盖所有 Venue 和产品；
- 不强制 Research Sandbox 放弃 DataFrame、Notebook 或临时代码。

### 禁止做法

- 策略直接消费 Provider 原始 JSON；
- 用 `event_time` 冒充 `available_time` 而不声明理想化延迟模型；
- 回测过程中隐式联网补数；
- 使用 mutable alias 作为不可复现实验的唯一身份；
- Research、Backtest、Live 各维护一份信号逻辑；
- 使用今天获取的当前期权链回填历史；
- fixture、synthetic 或 trade proxy 冒充可部署证据；
- 只比较最终收益，不比较数据、Factor、Decision 和 Intent；
- 用本地模拟测试冒充真实 Paper/Live 外部验收。

## 9. 实施优先级

建议按以下顺序推进：

1. **M1 统一易用回测 API**：立即改善开发效率并建立稳定产品入口；
2. **M2 统一策略协议**：避免继续产生新的分叉实现；
3. **M3 共享数据 Builder**：从源头保证在线/离线语义一致；
4. **M4 数据等价性报告**：让 Provider 差异可观测、可门禁；
5. **M5 Capture/T+1 Replay**：建立真实线上数据闭环；
6. **M6 Shadow/Paper/Limited Live**：建立安全晋级路线；
7. **M7 执行校准**：用真实运行改进回测可信度；
8. **M8 批量实验和开发者体验**：在稳定接口上扩展效率工具。

M1、M2、M3 完成前，不建议继续为单一策略增加新的专用回测框架。M4、M5、M6 完成前，不应把本地 deterministic acceptance 描述为真实 Live readiness。

## 10. Definition of Done

本路线图完成需要同时满足：

- 一个新策略可以通过统一脚手架创建，并用不超过 15 行 Python 完成首次回测；
- SMA、期权组合和多资产策略均通过统一 Backtest API；
- 所有可部署策略共享 Factor/Strategy/Intent 实现；
- 在线和离线 Builder 对相同输入通过 parity；
- Historical Release 与 Live Capture 可以自动生成等价性报告；
- 每个 Paper/Live Run 可 Capture、Replay 和解释；
- Shadow、Paper、Limited Live 和 Live 有强制晋级门禁；
- 回测执行模型绑定真实校准版本；
- 24–72 小时外部运行、重启、重连、对账和 Kill Switch drill 通过；
- 全量单元测试、集成测试、Example acceptance、compileall、Catalog strict health 和 `git diff --check` 通过；
- 所有结果均有不可变输入身份、代码身份、参数、模式组合和审计 hash。

达到以上条件后，系统才能宣称：

> Trader 既提供便捷的回测开发体验，也能以可验证、可解释的方式管理研究、回测、模拟和实盘之间不可避免的差异。
