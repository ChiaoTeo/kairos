# 量化策略研究与验证框架

## 1. 目的

本文定义从市场假设到可部署策略的统一验证流程，回答两个核心问题：

1. 什么时候验证相关性和预测能力；
2. 什么时候构造策略并进行回测。

框架的目标不是让所有研究使用同一种统计模型，而是让不同策略使用相同的证据等级、时间语义、状态和准入门槛。信号有效、组合盈利和可实盘交易是三个不同命题，必须分别验证。

```text
数据可用
  -> 信号预测有效
  -> 信号可以映射为组合收益
  -> 历史价格下可执行
  -> 样本外稳健
  -> 组合层面可接受
  -> 模拟盘和实盘一致
```

## 2. 模块边界

通用能力与具体研究分离：

```text
kairos/research/validation/
  contracts.py       # 验证结果、状态和证据等级
  split.py           # 时间切分和 walk-forward
  predictability.py  # 相关性、条件效应和预测检验
  bootstrap.py       # block bootstrap、HAC 等时间序列推断
  robustness.py      # 子样本、参数和成本敏感性
  gates.py           # 阶段准入门槛
  report.py          # 标准化结果和报告

kairos/backtest/
  # 时钟、行情、成交、持仓、Ledger、保证金和资金曲线

studies/<study>/
  hypothesis.py
  signal_study.py
  trade_proxy.py
  executable_backtest.py
  robustness.py
  report.py

kairos/strategies/
  # 已冻结规则、准备回测或部署的策略实现
```

`kairos/research/validation` 只保存可跨策略复用的方法和数据契约。具体资产、特征、阈值、期权腿和研究结论保留在 `studies/<study>`。成交、保证金和资金曲线由 `kairos/backtest` 管理，不在统计验证库中重复实现。

### 2.1 长期目标架构

成熟系统不以“研究代码是否进入 production”划分边界，而以决策职责划分：

```text
Governed Data / Point-in-time Features
                    |
                    v
Research Platform
  hypothesis -> factor -> signal evidence
                    |
                    v
Strategy Model
  signal -> universe -> portfolio construction -> economic target
                    |
                    v
Portfolio & Risk Platform
  allocation -> netting -> constraints -> account target
                    |
                    v
Execution Platform
  plan -> Maker/Taker/Hybrid -> child orders -> fills
                    |
                    v
Ledger & Operations
  positions -> cash -> PnL -> reconciliation -> controls
                    |
                    +--------------------+
                                         v
                                 Research feedback
```

各层必须能独立回放，并通过版本化契约连接。研究平台不能直接调用 Venue；执行平台不能重新解释因子；风险平台不能静默改变策略经济含义。

### 2.2 研究平台

研究平台负责：

- 数据探索、标签、特征和因子；
- 经济假设、统计检验和样本外证据；
- 策略原型、组合构造和风险归因；
- 产生冻结的 `StrategySpec` 候选；
- 记录失败研究、测试集消耗和数据缺口。

研究员负责从因子走到经济持仓，而不只输出一个分数。对于线性横截面策略，经济持仓可以是目标权重；对于期权、套利和对冲策略，还必须表达合约结构、数量关系、风险预算和生命周期。

研究平台不得负责：

- Venue连接、认证、重连和限频；
- client order id 与订单幂等；
- 实盘余额和持仓事实；
- 绕过组合风险、kill switch 或对账；
- 为了使策略成交而修改历史研究结果。

### 2.3 Strategy Model

Strategy Model 是研究与交易之间的正式生产边界。它消费 point-in-time `StrategyContext`，输出经济目标，不输出 Venue 原生订单。

```text
StrategyContext
  as_of
  governed features
  current strategy positions
  approved capital allocation
  instrument universe
  risk state

StrategyDecision
  strategy_id / model_version / decision_id
  economic targets
  risk budget
  urgency and validity window
  hedge requirements
  execution preference
  diagnostics and input hashes
```

示例：

```json
{
  "strategy_id": "btc_iron_condor_v1",
  "model_version": "1.0.0",
  "decision_time": "2026-07-15T08:00:00Z",
  "target_structure": {
    "type": "iron_condor",
    "expiry": "2026-07-31T08:00:00Z",
    "legs": ["long_put", "short_put", "short_call", "long_call"]
  },
  "risk_budget_usd": 2000,
  "maximum_net_delta_btc": 0.05,
  "valid_until": "2026-07-15T09:00:00Z",
  "execution_preference": "maker_then_taker"
}
```

期限、Delta节点、腿数量、入场过滤、持有期、止损、对冲目标和单策略风险预算属于策略语义，由策略所有者开发并版本化。它们不能由通用执行系统根据一个因子分数自行猜测。

生产实现位于 `kairos/strategies/`，但其所有权可以属于研究/策略团队。`studies/<study>` 保存实验、诊断和候选实现；只有通过晋级门禁的冻结规则才提升为生产 Strategy Model。

### 2.4 Portfolio & Risk Platform

组合层接收多个策略的经济目标，生成账户层目标：

```text
strategy decisions
 -> capital allocation
 -> cross-strategy netting
 -> account and Venue constraints
 -> scenario and margin checks
 -> approved account targets
```

它负责：

- 策略资本分配和动态降额；
- 同一 instrument 的跨策略净额与归属；
- 账户、Venue、币种、产品和风险来源集中度；
- Delta、Gamma、Vega、DV01、basis、leverage 等统一敞口；
- 组合保证金、流动性和压力情景；
- pre-trade risk、限额和拒绝原因；
- 保留策略虚拟持仓与账户真实净持仓之间的映射。

组合层可以缩小或拒绝目标，但不得静默改变腿比例、到期关系或对冲语义。需要改变策略结构时必须返回结构化拒绝，由 Strategy Model 重新决策。

### 2.5 Execution Platform

执行平台消费已经批准的账户目标和 `ExecutionPolicy`，输出并管理订单：

```text
approved target
 -> execution plan
 -> native combo or legging plan
 -> parent/child orders
 -> router and execution gateway
 -> fills and reconciliation
```

它统一负责：

- Venue能力、tick、lot、minimum notional 和交易时段；
- Maker、Taker、Hybrid状态机；
- native combo、拆腿顺序和裸腿限额；
- 下单、改单、撤单、超时和partial fill；
- 订单幂等、重连、恢复和对账；
- 实现价差、market impact 和执行质量；
- kill switch、只减仓和运行告警。

执行平台不得决定策略是否看多、选择哪个期权期限或修改目标风险来源。它可以在已批准边界内优化成交方式。

### 2.6 Ledger & Operations

Ledger 是现金、持仓、费用、Funding、行权、结算和成交的唯一事实来源。研究、回测、paper和live最终使用同一套经济事件语义。

Operations负责：

- 外部订单、成交、余额和持仓对账；
- reference data、行情、账户和执行 readiness；
- 数据断流、时钟偏差、限频和认证告警；
- 策略、订单、成交和Ledger事件的correlation id；
- 重启恢复、灾难恢复和审计。

研究结果不能直接改写Ledger；实盘成交也不能静默回写历史信号。实盘数据通过新的受管数据集进入后续研究。

### 2.7 四个核心跨层契约

长期实现保留四个稳定对象：

|契约|生产者|消费者|回答的问题|
|---|---|---|---|
|`ResearchEvidence`|研究验证|研究门禁/治理|信号证据到了哪里|
|`StrategySpec`|策略研究|Strategy Model|信号如何变成经济持仓|
|`EconomicIntent`|Strategy Model|Portfolio & Risk|现在希望持有什么风险|
|`ExecutionPolicy`|策略与执行共同配置|Execution Platform|允许怎样成交|

`ResearchEvidence` 至少包含假设、效应量、样本、证据等级、多维状态和限制。

`StrategySpec` 至少包含：

- universe、features、signal和portfolio construction；
- 产品、收益来源和风险来源；
- 入场、退出、再平衡和生命周期；
- capital spec和风险预算；
- 所需数据能力与执行能力；
- 模型、参数、代码和spec hash。

`EconomicIntent` 表达目标权重、目标数量、目标组合或目标风险，不承载Venue symbol和API参数。结构化产品必须保留腿关系、ratio、原子性偏好和最大裸露风险。

`ExecutionPolicy` 表达Maker/Taker/Hybrid、超时、最大滑点、partial-fill处理、组合与拆腿政策、延迟预算和费用版本。策略团队定义经济容忍度，执行团队维护可实现模型和Venue约束。

### 2.8 所有权边界

|事项|研究/策略团队|Portfolio/Risk|Execution/Trading|
|---|:---:|:---:|:---:|
|假设、因子、标签|负责|审阅风险含义|不负责|
|Universe与组合构造|负责|约束与净额|检查可交易性|
|期限、腿、ratio、对冲目标|负责|检查组合风险|按原意执行|
|策略资本申请|提出|批准和动态分配|执行已批准规模|
|Maker/Taker偏好|提出经济约束|限制裸露风险|实现与优化|
|队列、滑点、订单状态机|提供研究需求|定义风险边界|负责|
|Venue API、恢复、对账|不负责|消费账户事实|负责|
|PnL归因|信号与模型归因|组合归因|执行成本归因|
|策略停用建议|提出|可强制降额/停用|执行停止与撤单|

策略实际执行因此与 `kairos` 放在一起，但不是把研究压缩为单一因子：研究/策略团队负责“预测什么、持有什么”，Portfolio/Risk负责“账户允许持多少”，Execution/Trading负责“如何安全成交并持续运行”。

### 2.9 同代码语义与不同运行环境

成熟系统应实现相同 `StrategySpec` 和 Strategy Model 在research replay、backtest、paper和live中使用同一决策语义。环境差异通过依赖注入提供：

- point-in-time feature repository；
- clock和market-data feed；
- execution simulator或live router；
- portfolio、margin和Ledger service。

研究notebook可以调用生产特征和策略组件，但notebook不是生产实现。禁止将研究中复制的一份策略公式与live中另一份公式长期并存。

回测和live可以使用不同Execution实现，但必须消费相同的`ExecutionPolicy`契约，并分别记录模型版本和能力差异。

### 2.10 策略晋级与变更治理

策略按不可跳级的生命周期晋级：

```text
DRAFT
 -> RESEARCH_VALIDATED
 -> TRADE_PROXY_VALIDATED (optional)
 -> EXECUTABLE_BACKTEST_VALIDATED
 -> ROBUSTNESS_VALIDATED
 -> PAPER_APPROVED
 -> LIVE_LIMITED
 -> LIVE_APPROVED
 -> SUSPENDED / RETIRED
```

每次晋级保存审批证据、`StrategySpec`版本、数据窗口、代码hash、资本上限和回滚条件。

以下修改需要新研究版本并重新经过相应门禁：

- 信号、特征、标签或阈值；
- universe、期限、腿、ratio或持有期；
- 风险预算或主要对冲逻辑；
- 从Taker改为Maker/Hybrid；
- 执行方式变化足以改变成交选择或收益来源。

纯Venue适配、bug fix或性能优化是否需要重新验证，由语义diff判定。任何影响决策、成交或PnL的修复必须生成新audit hash并至少重放受影响窗口。

## 3. 四维验证架构

验证框架由四个维度组合：

```text
第一层：通用证据流程
  数据 -> 信号 -> 组合映射 -> 可执行回测 -> 稳健性 -> 模拟盘/实盘

第二层：产品验证协议
  现货协议 | 期货协议 | 期权协议

第三层：收益来源协议
  方向 | 趋势 | 均值回归 | Carry | 基差 | 波动率 | Skew | 尾部风险

第四层：执行协议
  Maker | Taker | Hybrid
```

第一层解决“证据达到什么等级”，第二层解决“对这个产品，什么证据才算有效”，第三层解决“策略为什么赚钱以及必须承担什么风险”，第四层解决“订单如何进入市场以及成交能否实现”。所有产品使用统一状态和研究产物，但不能共用未经产品化的标签、成交模型、资本口径和风险指标。

例如：

- 现货趋势策略主要验证未来现货收益、换手和回撤；
- 期货基差策略主要验证基差收敛、Funding、换月和保证金；
- 期权波动率策略主要验证 IV-RV、曲面变化、Greeks、组合腿和结算。

同一种产品也可能需要完全不同的验证方法。例如买 Call 是方向策略，铁鹰是短波动和短 Gamma 策略，Calendar Spread 是期限结构策略。产品协议不能代替收益来源协议。

因此，通用库只实现时间切分、统计推断、状态和门禁；产品协议负责定义生命周期和资本；收益来源协议负责定义目标标签、PnL 来源和强制压力情景；执行协议负责定义队列、延迟、成交、撤单和市场冲击。

每项研究必须声明：

```json
{
  "products": ["crypto_option"],
  "strategy_archetypes": ["short_volatility", "short_gamma", "skew"],
  "return_drivers": ["theta", "variance_risk_premium"],
  "risk_drivers": ["gamma", "vega", "jump", "liquidity"],
  "execution_archetype": "taker"
}
```

|收益来源|最低验证要求|
|---|---|
|方向/趋势|未来收益、Beta、趋势制度和反转风险|
|均值回归|回归速度、结构变化、持有期和止损风险|
|Carry|收益是否覆盖价格变化、融资和交易成本|
|基差|收敛、Funding、换月、保证金和腿风险|
|波动率|IV-RV、Theta、Gamma、Vega和跳跃损失|
|Skew/曲面|固定节点变化、曲面暴露和对冲后PnL|
|流动性|成交率、市场冲击、容量和拥挤退出|

## 4. 六级通用验证流程

### L1：数据与标签验证

目标：证明输入足以支持所声明的研究。

必须检查：

- 数据来源、品种、字段和单位明确；
- UTC `[start, end)` 覆盖和缺口明确；
- `event_time <= decision_time`；
- 特征、标签和合约选择没有未来信息；
- 缺失、异常、重复和存活偏差有统计；
- 同步盘口、日内成交、日线或模型价格被明确区分。

标准产物：`data_quality.json`、`coverage.json` 和 `lineage.json`。

未通过 L1 时不得发布相关性或回测结论，状态为 `DATA_NOT_READY`。

### L2：信号预测验证

目标：验证市场假设预测什么，不涉及具体交易结构。

只有在 L1 通过后才进行。典型问题包括：

- 当前 Skew 是否预测未来 Skew 变化；
- 当前 IV 是否高于同期限未来 RV；
- 某个状态是否预测收益、波动率或最大回撤；
- 预测关系在哪些期限和市场状态下成立。

普通 Pearson 相关系数只能作为描述性结果，不能单独通过 L2。正式验证至少包含：

- 按时间切分 development、validation 和 test；
- Pearson、Spearman 或与假设匹配的条件效应；
- 预先定义的预测期限和标签；
- 对重叠标签使用 block bootstrap、HAC 或等价处理；
- 效应量、置信区间和最小样本量；
- 牛市、熊市、波动率制度等子样本稳定性；
- 多个期限或特征同时检验时说明多重检验处理。

相关性用于探索时标记为 `EXPLORATORY`；阈值、方向、期限和样本门槛预注册，并在未参与选择的测试集上通过后，才标记为 `SUPPORTED`。

### L3：信号到组合的映射

目标：判断预测关系能否转化为可解释的组合 PnL。

进入本层前应满足：

- 信号具有明确经济机制；
- 特征和目标期限已经冻结；
- 入场、退出和失效条件明确；
- 组合的 Delta、Gamma、Vega、Theta 和尾部风险可以计算。

本层允许快速构造策略原型和成交代理，但只用于回答：

- 收益来自信号、方向、波动率水平还是时间价值；
- 哪些风险会吞掉预测优势；
- 是否值得获取更好的执行数据。

成交代理必须标记为 `TRADE_PROXY_ONLY`，不得据此发布 CAGR、容量或可执行收益。

### L4：可执行回测

目标：验证在决策时可见且可成交的价格下，策略扣除成本后是否盈利。

正式策略回测至少要求：

- 信号在当前 slice 计算，订单最早在后续 slice 成交；
- 使用同步 bid/ask、订单簿或经过验证的成交模型；
- 缺失报价不可成交，理论价格不能制造 fill；
- 包含手续费、bid/ask、滑点、Funding 和换仓成本；
- 包含未成交、部分成交和组合拆腿风险；
- 使用真实或保守的保证金模型；
- 每笔风险资本和组合总资本明确；
- 到期、结算、行权和指派进入 Ledger；
- 所有数据、配置、代码和随机种子进入 audit hash。

只有 L4 以后才允许计算具有交易解释力的 CAGR、Sharpe、Calmar、最大回撤和资金容量。在此之前可以报告每笔 PnL 和风险归一化收益，但必须明确为研究代理。

### L5：样本外与稳健性

目标：排除参数挑选、单一市场制度和成本假设造成的假象。

必须包含：

- 冻结参数后的真正样本外测试；
- walk-forward 或多个不重叠测试窗口；
- 邻近参数敏感性，而不是只报告最优参数；
- 不同趋势、波动率和流动性制度；
- 保守成交和压力成本；
- 尾部情景、最大亏损与 Expected Shortfall；
- 与无条件策略、简单基准和替代结构比较。

同一测试集上选择出的最佳阈值只能标记为 `EXPLORATORY`，不能重新命名为样本外结果。

### L6：模拟盘与实盘验证

目标：验证回测假设与真实运行一致。

依次验证：

- 实时特征和历史特征一致；
- 信号、订单和取消可以稳定重放；
- 模拟成交与真实盘口偏差可接受；
- 实际成交率、滑点、保证金和容量符合回测；
- 风险限制、对账和 kill switch 可用；
- 小规模实盘与模拟盘的收益衰减在预设范围内。

## 5. 何时做相关性验证

满足以下条件时开始 L2：

1. 数据质量和时间语义已经通过 L1；
2. 自变量、目标变量和预测期限可以在研究前写清楚；
3. 标签能在信号时点之后计算；
4. 有足够数据进行按时间切分；
5. 研究问题不依赖具体成交规则。

例如“高 25Δ Put Skew 是否预测未来 14 天 Skew 下降”应先做信号验证；此时不需要决定卖哪一条 Put，也不应把组合 PnL 当作标签。

相关性验证的标准结果至少包含：

```json
{
  "validation_type": "signal_predictability",
  "hypothesis": "high put skew predicts lower future put skew",
  "horizon_days": 14,
  "observations": 180,
  "effect": -0.032,
  "confidence_interval": [-0.051, -0.016],
  "out_of_sample": true,
  "overlap_adjustment": "block_bootstrap",
  "status": "SUPPORTED"
}
```

以下情况只允许探索，不得宣布信号有效：

- 看完全部样本后才选择期限或方向；
- 同时尝试大量特征，只报告最显著结果；
- 使用随机切分破坏时间顺序；
- 标签重叠却使用独立同分布的标准误；
- 只有相关系数，没有效应量稳定性和样本外结果。

## 6. 何时写策略回测

策略代码可以在两个时点出现，但证据含义不同。

### 6.1 L3 研究原型

信号具有合理机制但尚未完全通过时，可以写最小组合原型，用于风险归因和数据需求评估。必须满足：

- 文件和结果命名包含 `trade_proxy` 或 `prototype`；
- 不输出“策略有效”或“预期年化”；
- 结果列明价格来源、不可成交假设和选择偏差；
- 参数选择不进入正式策略目录。

### 6.2 L4 正式回测

只有以下信息冻结后才开始：

1. 信号公式、方向和目标期限；
2. Universe 和流动性过滤；
3. 期权腿、数量和风险预算；
4. 入场、退出、止损和再平衡规则；
5. 执行价格与未成交规则；
6. 手续费、滑点、Funding 和保证金模型；
7. development、validation 和 test 的时间边界；
8. 主要指标、样本门槛和否决条件。

如果其中任何一项根据测试集结果反复调整，当前测试集即退回 development，必须保留新的未触碰样本。

## 7. 三类产品的验证协议

### 7.1 现货策略

现货研究的经济对象是资产价格本身。核心标签通常是未来收益、波动率、回撤或横截面相对收益。

#### 信号验证

至少检查：

- 信号对未来收益或风险的预测能力，而不是对同期价格的相关性；
- 复权、分红、拆股、退市和成分股历史，避免存活偏差；
- 加密现货的 Venue、计价资产和稳定币风险；
- 横截面研究使用 point-in-time universe；
- 做空策略明确借券、借币可得性和借贷成本。

#### 回测验证

至少包含：

- bid/ask、手续费、滑点和市场冲击；
- 成交量参与率与容量限制；
- 停牌、涨跌停、交易时段或 Venue 中断；
- 多币种现金和汇率转换；
- long-only 与 long-short 使用不同资本口径。

现货 long-only 的资本通常是实际现金占用；杠杆、融资和做空策略还必须加入融资、借券及保证金。主要指标包括 CAGR、波动率、Sharpe、最大回撤、换手、容量和相对基准收益。

### 7.2 期货与永续策略

期货研究的经济对象不仅是标的方向，还包括基差、期限结构、Funding 和合约生命周期。不能把期货简单当作带杠杆的现货。

#### 信号验证

按策略类型选择标签：

- 趋势策略：未来期货收益及其与现货收益的差异；
- 基差策略：基差变化和到期收敛，而不是单腿价格收益；
- 期限结构策略：calendar spread 的未来变化；
- 永续策略：Funding、永续溢价和未来净carry；
- 套利策略：组合的未来净收益，包含两腿价格与持有成本。

连续合约只能用于信号研究或展示。正式收益必须由真实可交易合约、真实换月时间和逐合约价格构建，不能使用经过后复权的连续价格制造 PnL。

#### 回测验证

至少包含：

- 合约乘数、tick、交割资产和结算方式；
- initial/maintenance margin 和保证金变化；
- Funding、手续费、滑点和换月成本；
- mark price、index price 与成交价格的不同用途；
- 强平、ADL、限仓和 Venue 风险；
- 到期、交割、现金结算和自动换月；
- 现货对冲腿的资金、借贷和执行成本。

资本口径必须同时报告名义敞口和保证金权益。年化收益基于完整账户权益，不能用保证金占用最小值或名义本金随意作为分母。主要指标除 CAGR 和回撤外，还包括杠杆、保证金利用率、强平距离、Funding 贡献、roll yield 和基差归因。

### 7.3 期权策略

期权研究的经济对象是非线性收益和波动率曲面。方向预测正确不代表组合盈利，Skew 或 IV 预测正确也不代表具体期权腿可执行盈利。

#### 信号验证

必须先说明研究的是哪一类命题：

- 方向：未来现货或 forward 收益；
- 波动率：ATM IV 与同期限未来 RV；
- Skew：固定 Delta 或固定 moneyness 节点的未来变化；
- 期限结构：固定期限 total variance 的未来变化；
- 尾部风险：未来跳跃、最大回撤或实现偏度。

曲面标签必须固定 Delta/moneyness 和期限，并明确插值、外推、forward、利率和 day-count。不能把“某张合约价格变化”直接当作稳定的 Skew 标签。

#### 回测验证

至少包含：

- point-in-time 动态合约 universe；
- 同步的多腿 bid/ask 和可成交数量；
- forward、曲面、IV 和 Greeks 的模型版本；
- Delta、Gamma、Vega、Theta、Skew 和残差 PnL 归因；
- 多腿未成交、部分成交和 legging risk；
- 组合保证金，而非简单相加单腿保证金；
- 到期结算、行权、指派和现金/实物交割；
- 波动率制度、跳跃和压力重估。

只有成交记录而没有同步盘口时，结果只能是 `TRADE_PROXY_ONLY`。固定风险结构以理论最大亏损作为研究尺度时，还必须检查成交代理是否违反无套利边界；正式 CAGR 必须基于真实账户权益和实际保证金资金曲线。

### 7.4 产品协议对照

|验证项|现货|期货/永续|期权|
|---|---|---|---|
|主要预测对象|未来收益、回撤、横截面排名|收益、基差、Funding、期限结构|IV-RV、Skew、曲面、方向和尾部|
|合约生命周期|通常无到期；公司行为/退市|换月、到期、交割、Funding|动态合约、到期、行权、指派|
|核心资本口径|现金净值|账户权益、名义敞口和保证金|账户权益、组合保证金和最大风险|
|主要执行风险|流动性和市场冲击|杠杆、换月、强平、基差腿|多腿同步、曲面、Greeks、跳跃|
|不可省略成本|手续费、价差、借贷|手续费、价差、Funding、roll|多腿价差、手续费、对冲和legging|
|代理数据限制|收盘价不能证明日内可成交|连续合约不能生成正式PnL|非同步成交不能生成组合CAGR|

混合策略必须同时通过涉及的全部产品协议。例如 cash-and-carry 同时经过现货与期货协议；Delta 对冲期权同时经过期权与期货/现货对冲协议。不能因为主要收益被命名为“期权策略”而省略对冲腿的 Funding、保证金和执行验证。

## 8. Maker、Taker 与 Hybrid 执行协议

执行类型是独立验证维度，不是手续费参数。本文统一使用 `Maker` 和 `Taker`，不使用含义不清的 `make strategy`、`take strategy`。

### 8.1 Maker

Maker 通过限价挂单提供流动性。潜在收益包括 spread capture 和返佣，核心风险包括队列劣势、逆向选择、库存、撤单延迟和对冲成本。

正式 Maker 回测至少需要：

- 带 sequence 的增量订单簿和逐笔成交；
- 价格档位的可见深度、成交和撤单变化；
- FIFO、pro-rata 或 Venue 对应的队列模型；
- 下单、确认、撤单和行情接收延迟；
- partial fill、超时、撤单失败和重连；
- 成交后的 markout 与 adverse-selection cost；
- 库存限制、对冲规则和对冲盘口；
- Maker 返佣、限频和订单限制。

仅凭价格触碰不能假设 Maker 成交。没有订单簿事件和队列模型时，最多标记为 `MAKER_FILL_PROXY_ONLY`，不能输出正式成交率、PnL或容量。

Maker 强制指标包括 fill rate、spread capture、不同时间尺度 markout、adverse selection、inventory PnL、hedge cost、cancel-to-fill ratio、最大库存和压力损失。

### 8.2 Taker

Taker 主动消耗流动性。低频、小规模 Taker 回测至少需要决策时同步 bid/ask、可成交 size、手续费和决策到订单的延迟。

- 买入使用 ask 或更差价格；
- 卖出使用 bid 或更差价格；
- 数量超过一档 size 时必须逐档行走订单簿；
- 缺少 size 时只能验证小额保守代理，不能验证容量；
- 多腿策略必须模拟组合订单或逐腿 legging risk。

Taker 强制指标包括实现价差、slippage、market impact、成交率、拒单率、延迟敏感性和成本后PnL。

### 8.3 Hybrid

Hybrid 必须用状态机描述，不能将 Maker 和 Taker 成本简单平均：

```text
submit maker
 -> wait until timeout
 -> partial fill
 -> cancel remainder
 -> cross spread for remainder or hedge exposure as taker
```

研究配置至少声明：

```json
{
  "execution_archetype": "hybrid",
  "maker_timeout_ms": 2000,
  "queue_model": "fifo_visible_depth",
  "order_latency_ms": 100,
  "cancel_latency_ms": 100,
  "partial_fill_policy": "hedge_immediately",
  "taker_slippage_model": "order_book_walk",
  "fee_schedule": "venue_versioned"
}
```

期权组合还必须说明是组合报价还是逐腿报价；一条腿 Maker 成交后由其他腿 Taker 对冲时，裸露的 Delta、Gamma 和 Vega 路径必须进入风险与PnL。

### 8.4 执行数据门槛

|执行协议|最低数据要求|最高可声明结果|
|---|---|---|
|低频小额 Taker|同步 bid/ask、size、费用|可执行回测|
|大额 Taker|多档订单簿和冲击模型|容量与市场冲击|
|Maker 代理|盘口快照和保守 fill 模型|`MAKER_FILL_PROXY_ONLY`|
|正式 Maker|增量订单簿、交易事件、队列和延迟|Maker 可执行回测|
|Hybrid|正式 Maker 数据加主动对冲盘口|完整状态机回测|

## 9. 多维状态与阶段门禁

研究不能只有一个总状态。至少分别记录数据、信号、执行和策略状态：

```json
{
  "data_status": "READY",
  "signal_status": "SUPPORTED",
  "execution_status": "DATA_NOT_READY",
  "strategy_status": "NOT_TESTED"
}
```

这表示数据足以验证信号、信号已经通过，但缺少可执行数据，因此不能验证策略。汇总状态只能由这些状态推导，不能覆盖更细粒度证据。

各维度统一使用以下状态值：

|状态|含义|
|---|---|
|`NOT_TESTED`|尚未运行规定验证|
|`DATA_NOT_READY`|数据类型、覆盖或样本量不足|
|`EXPLORATORY`|用于提出假设或选择设计，不是确认性证据|
|`TRADE_PROXY_ONLY`|组合研究使用不可执行价格|
|`MAKER_FILL_PROXY_ONLY`|Maker成交仅由触碰或简化概率代理|
|`SUPPORTED`|预注册命题在独立测试集上通过|
|`NOT_SUPPORTED`|证据不足以拒绝零假设|
|`REJECTED`|方向相反、风险不可接受或明确未通过|

建议的默认门禁：

|从|到|最低要求|
|---|---|---|
|L1|L2|覆盖、质量、lineage 和无前视检查通过|
|L2|L3|经济机制明确；至少一个核心信号 `SUPPORTED`，或显式标记探索|
|L3|L4|组合风险可解释；同步可执行数据和成本模型可用|
|L4|L5|扣费后结果达到预注册门槛；资金曲线与保证金完整|
|L5|L6|真正样本外通过；参数、制度和压力测试稳定|

样本量门槛由研究预注册，不由结果反推。少于门槛时，即使置信区间看似显著，状态仍为 `DATA_NOT_READY` 或 `EXPLORATORY`。

## 10. 资本与资金曲线协议

策略回测必须显式声明：

```json
{
  "initial_equity": 100000,
  "base_currency": "USD",
  "risk_budget_per_trade": 0.02,
  "portfolio_risk_limit": 0.10,
  "margin_model": "venue_portfolio_margin_v1",
  "capital_reinvestment": true,
  "allow_overlapping_positions": false,
  "idle_cash_return_model": "usd_cash_rate_v1",
  "liquidation_policy": "maintenance_margin"
}
```

资本规则必须覆盖：

- 初始账户权益和基础货币；
- 每笔风险预算、组合风险上限和数量取整；
- 现金占用、名义敞口、保证金和理论最大亏损；
- 持仓重叠、闲置现金、融资和复投；
- 保证金不足、强平和停止交易规则。

现货 long-only 以现金净值为主要分母；期货同时报告账户权益、名义敞口和保证金利用率；期权同时报告账户权益、组合保证金和压力最大风险。不同资本口径可以作为敏感性结果，但只能有一个预注册的主要口径。

CAGR 必须从完整账户权益曲线计算：

```text
CAGR = (ending_equity / initial_equity) ^ (365 / elapsed_days) - 1
```

如果代理价格使有限风险组合亏损超过无套利最大亏损，资金曲线验证自动失败，不得发布 CAGR。

## 11. 数据能力契约

数据集除 schema、lineage、coverage 和 manifest 外，还必须声明其研究能力：

```json
{
  "synchronous_quotes": false,
  "top_of_book": false,
  "quote_size": false,
  "order_book_depth": 0,
  "incremental_order_book": false,
  "sequence_numbers": false,
  "trade_events": true,
  "queue_reconstructable": false,
  "trade_direction": true,
  "point_in_time_universe": true,
  "settlement_price": true,
  "supported_validation_levels": ["L1", "L2", "L3"]
}
```

建议字段包括：

- 时间精度、事件时间和接收时间是否存在；
- trade、bid/ask、size、order book 和 mark 的可用性；
- 订单簿深度、增量事件、sequence、逐笔成交和队列可重建性；
- 多腿是否同步；
- universe 和 reference data 是否 point-in-time；
- Funding、利率、公司行为、到期和结算覆盖；
- 允许支持的产品协议与最高验证层级。

验证门禁必须读取该契约并拒绝不兼容研究。例如：

- 日线不能证明日内成交；
- 后复权连续期货不能生成正式 PnL；
- 非同步期权成交不能生成组合 CAGR；
- 缺少 size 的报价不能证明策略容量。

## 12. 数据不足处理协议

数据不足必须被分类、量化并形成补数计划，不能只输出一个 `DATA_NOT_READY`。

### 12.1 缺口分类

|缺口|示例|阻止的结论|
|---|---|---|
|覆盖不足|只有159天盘口|制度稳健性和独立样本|
|粒度不足|只有日线|日内入场、止损和延迟|
|同步不足|期权四腿来自不同时间|可执行组合价格和CAGR|
|字段不足|没有size或深度|容量、冲击和Maker成交|
|事件不足|没有Funding、结算或换月|完整资金曲线|
|制度不足|没有危机或低波动时期|压力稳健性|

结果必须列出 `blocked_capabilities` 和因此受限的 `maximum_validation_level`，例如：

```json
{
  "data_status": "READY_FOR_L3",
  "maximum_validation_level": "L3",
  "blocked_capabilities": [
    "executable_multi_leg_price",
    "maker_fill_simulation",
    "capacity_estimation",
    "tradable_cagr"
  ]
}
```

### 12.2 有效样本量

原始行数不等于独立样本数。必须同时报告：

- raw observations；
- non-overlapping observations；
- overlap-adjusted effective sample size；
- 条件信号和完整交易数；
- 覆盖的市场制度与极端事件数。

30D标签按日生成时相邻样本共享大量未来区间，不能把每一行视为独立证据。Bootstrap可以估计不确定性，但不会创造新信息。

### 12.3 最小可识别效应和统计功效

确认性研究应预注册最小有经济意义的效应和目标统计功效。例如“条件策略每笔至少改善风险资本1%，以80%功效识别”。运行前估算最低独立样本量，结果保存：

```json
{
  "minimum_detectable_effect": 0.01,
  "target_power": 0.80,
  "available_effective_samples": 18,
  "required_effective_samples": 75,
  "additional_samples_required": 57
}
```

样本不足时不得通过反复bootstrap、增加参数搜索、理论价填充或重用测试集来规避门槛。

### 12.4 允许的处理方式

按优先顺序处理：

1. 将结论降级到数据支持的最高层级；
2. 减少期限、阈值和结构数量，冻结最简单的确认版本；
3. 持续采集缺失的同步行情、事件和执行字段；
4. 在独立数据源上做定义一致的交叉验证；
5. 使用 synthetic 数据验证机制和风险边界，但不得证明市场收益；
6. 记录预计补齐样本所需的时间和下一次评估日期。

每个不足研究必须保存 `sample_sufficiency.json` 和 `data_gap_plan.json`。后者至少包含缺口、阻塞层级、采集字段、频率、开始时间、目标样本和重新评估条件。

## 13. 研究预注册与测试集消耗

每项确认性研究在读取测试结果前注册：

```text
study_id 和 version
hypothesis
products 和 strategy_archetypes
return_drivers 和 risk_drivers
development / validation / test 时间边界
features、labels 和 horizons
主要指标和次要指标
最小样本量
通过、否决和停止条件
数据能力要求
资本、执行和成本模型
```

注册内容产生不可变 `spec_hash`。修改信号、期限、阈值、结构、成本或主要指标会产生新版本。查看测试结果后进行的任何参数选择都会消耗该测试集：当前窗口退回 development，新版本必须使用未触碰的测试窗口。

失败、无显著性和数据不足的研究与成功研究使用相同格式保存，避免只保留正结果。

## 14. 样本外证据等级

不能只使用一个 `out_of_sample: true`。至少区分：

|类型|定义|
|---|---|
|`parameter_oos`|模型或阈值没有在该样本拟合|
|`time_oos`|样本时间晚于开发窗口|
|`decision_oos`|假设、参数、指标和门槛在查看结果前已经冻结|

`decision_oos` 是最强证据。只有时间靠后，但研究者在查看结果后反复修改规则，仍不属于 `decision_oos`。

结果文件必须记录测试窗口是否已经被其他研究版本使用，以及当前结论最高达到哪一种样本外等级。

## 15. 组合层验证

单策略通过 L5 后，在进入模拟盘前增加组合层门禁。组合层验证：

- 与现有策略和基准资产的普通相关性及压力相关性；
- 多个策略是否在同一市场状态同时触发；
- 风险预算、现金和保证金竞争；
- Venue、标的、币种和收益来源集中度；
- 组合 VaR、Expected Shortfall、压力损失和最大回撤；
- 新策略的边际收益、边际风险和边际资本占用；
- 一个策略退出、断流或强平对其他策略的影响。

混合产品组合沿用全部相关产品协议。组合层通过不改变单策略证据状态，只决定它是否适合加入特定账户。

## 16. 标准研究产物

每项研究在 `data/studies/<study_id>/` 下至少保存：

```text
study_spec.json       # 假设、指标、阈值、切分和否决条件
data_quality.json     # 数据覆盖和质量结论
results.json          # 机器可读结果和状态
REPORT.md             # 人可读结论与限制
```

策略研究额外保存：

```text
trades.json           # 逐笔交易及信号、价格和成本
equity_curve.*        # 固定资本口径的资金曲线
risk_decomposition.json
audit.json            # 数据、配置、代码和随机种子 hash
execution_spec.json   # Maker/Taker/Hybrid及成交状态机
```

`results.json` 必须同时声明 `validation_level`、`pricing_type`、`out_of_sample`、`status` 和 `limitations`，使报告不能脱离证据等级单独传播。

此外必须保存或引用：

```text
data_capabilities.json # 输入数据能够支持的最高验证层级
capital_spec.json      # 账户、风险预算和保证金口径
audit.json             # spec_hash、数据、代码和随机种子 hash
test_usage.json        # 测试窗口使用和消耗记录
sample_sufficiency.json # 有效样本量、MDE和统计功效
data_gap_plan.json      # 数据缺口、补数计划和重评条件
```

## 17. 允许声明的结论

报告措辞由最高通过层级约束：

|最高通过层级|允许声明|
|---|---|
|L1|数据可以支持指定研究|
|L2|信号具有预测证据|
|L3|信号可以映射为交易代理|
|L4|历史可执行回测扣费后达到门槛|
|L5|样本外和压力测试具有稳健性|
|L6|模拟盘或小规模实盘得到验证|

低层级结果不得使用高层级措辞。`TRADE_PROXY_ONLY` 可以说“值得继续验证”，不能说“策略有效”；没有完整账户权益曲线时不能报告预期 CAGR；没有 size 和市场冲击模型时不能报告容量。

报告生成器应根据多维状态和数据能力契约自动生成允许的结论，并在违反规则时失败，而不是只增加免责声明。

## 18. BTC 铁鹰研究的当前定位

|命题|层级|当前状态|
|---|---:|---|
|高 Put Skew 随后均值回归|L2|`SUPPORTED`|
|7D ATM IV 高于未来 RV|L2|`SUPPORTED`|
|14D/30D ATM IV 高于未来 RV|L2|`NOT_SUPPORTED`|
|高恐慌且 IV 停止上升可能改善铁鹰|L3|`EXPLORATORY`|
|Deribit 长期铁鹰成交代理|L3|`TRADE_PROXY_ONLY`|
|铁鹰扣除真实执行成本后盈利|L4|`DATA_NOT_READY`|
|Taker铁鹰可执行回测|L4|`DATA_NOT_READY`：缺少足够长期同步四腿bid/ask与size|
|Maker铁鹰可执行回测|L4|`NOT_TESTED`：缺少订单簿事件、队列与延迟|
|铁鹰 CAGR 和实盘容量|L4-L6|`NOT_TESTED`|

因此，当前可以继续积累同步 Deribit bid/ask 并冻结下一版规则，但不能把成交代理的美元 PnL 或粗略复利结果解释为策略年化收益。

当前研究的多维状态应表达为：

```json
{
  "data_status": "READY_FOR_L3",
  "signal_status": "PARTIALLY_SUPPORTED",
  "execution_status": "DATA_NOT_READY",
  "taker_execution_status": "DATA_NOT_READY",
  "maker_execution_status": "NOT_TESTED",
  "strategy_status": "TRADE_PROXY_ONLY",
  "maximum_claim": "signal can be mapped to an exploratory trade proxy"
}
```

## 19. 研究纪律

- 先写 `study_spec.json`，再查看测试集结果；
- 相关性证明预测关系，回测证明交易映射，两者不得互相替代；
- 胜率不能替代尾部风险和资金曲线；
- 没有同步可执行价格时，不报告可交易 CAGR；
- 任何参数重选都会消耗当前测试集；
- 失败结果与成功结果使用相同格式保存；
- 结论必须随数据等级降级，不能只在限制章节补一句免责声明。
- 价格触碰不能直接视为 Maker 成交；
- Bootstrap、synthetic 和理论价格不能替代独立真实样本。

## 20. 实施顺序

长期设计完整保留，实施按依赖关系分阶段，不以删减抽象换取短期速度。

### Phase A：治理基础

1. `ResearchEvidence`、多维状态和验证层级；
2. `StrategySpec`、`spec_hash` 和研究预注册；
3. 数据能力契约与数据缺口分类；
4. 时间切分、测试集消耗和audit hash；
5. 标准研究目录、结果和报告生成。

完成标准：任何研究都能回答用了什么数据、检验什么命题、最高允许声明什么，以及结果能否重现。

### Phase B：策略与资本契约

1. Strategy Model只读`StrategyContext`；
2. `EconomicIntent`支持权重、数量、结构和风险目标；
3. 统一capital spec、组合保证金和完整权益曲线；
4. 产品协议与收益来源协议成为可组合门禁；
5. 研究实现向`kairos/strategies`晋级的语义一致性测试。

完成标准：研究和生产使用同一冻结策略语义，能够生成不依赖Venue API的经济目标。

### Phase C：Portfolio & Risk

1. 多策略资本分配和虚拟持仓；
2. 跨策略净额与账户真实持仓映射；
3. 产品、Venue、币种和风险来源限额；
4. 组合保证金、压力测试和边际风险；
5. 结构化批准、缩量和拒绝原因。

完成标准：多个策略不能各自假设拥有全部现金和保证金，账户目标具有可审计来源。

### Phase D：执行协议

1. `ExecutionPolicy`与能力协商；
2. Taker同步盘口、深度、冲击和多腿执行；
3. Maker订单簿、队列、延迟、markout和库存；
4. Hybrid状态机、partial fill和紧急对冲；
5. 相同策略在backtest、paper和live的执行质量对账。

完成标准：执行收益和alpha收益可以分离，Maker/Taker结论不会超过数据能力。

### Phase E：晋级与运行治理

1. 样本充分性、MDE和数据补齐计划；
2. 样本外、稳健性和组合层门禁；
3. paper、limited live和正式live审批；
4. 漂移、PnL归因、容量和执行质量监控；
5. 降额、暂停、回滚和退役流程；
6. 根据证据等级自动限制报告结论。

完成标准：策略从研究到实盘的每次状态变化都有证据、责任人、资本上限和回滚条件。
