# 期权策略研究框架设计

## 1. 目标

本项目在现有 `InstrumentDefinition + Catalog + Ledger` 多资产事实模型上，补齐一套可独立于券商模型运行的期权研究闭环：

```text
Market Data
  Spot / Forward / Rates / Dividends / Option Chain / Quotes
                              |
                              v
Pricing & Vol Engine
  Pricing / Implied Vol / Greeks / Smile / Surface / Calibration
                              |
                              v
Strategy Engine
  Universe / Features / Signals / Structure Construction / Intent
                              |
                              v
Backtest
  Point-in-time Data / Execution / Lifecycle / Ledger / Replay
                              |
                              v
Risk Analytics
  Exposures / Scenarios / PnL Explain / VaR / Expected Shortfall
```

框架必须同时满足以下目标：

- 同一份标准化市场数据可供研究、回测、paper 和 live 使用；
- 券商提供的 IV 和 Greeks 只是带来源的观测值，不是唯一事实源；
- 所有定价、曲面、策略决策和风险结果都能追溯输入及模型版本；
- 回测只能使用决策时点已经可见的合约和数据；
- 策略只产生经济 Intent，不直接依赖 Venue API 或执行细节；
- 风险情景通过完整重估计算，Greeks 近似仅作为解释和快速预估；
- 相同数据、代码、配置和随机种子必须产生相同结果及 audit hash。

## 2. 现有能力与迁移原则

### 2.1 保留的核心

下列边界已经具备正确方向，应扩展而不是替换：

- `trading.domain`：Instrument、ProductSpec、MarketEvent、Intent；
- `trading.reference`：内部 InstrumentId 与 ListingDefinition 的 point-in-time 映射；
- `trading.research`：snapshot、series capture、数据质量问题；
- `trading.backtest`：确定性 feed/clock、无同 slice 成交、fill model、结算和 replay；
- `trading.accounting`：Ledger 与 Portfolio；
- `trading.execution` / `trading.orchestration`：执行计划、路由、对账和 kill switch；
- `trading.risk`：交易前限制与统一敞口视图。

### 2.2 必须消除的隐含耦合

- 策略不得把 IBKR `modelGreeks` 当成必需输入；
- 研究分析不得把原始 option mid 直接等同于可靠校准输入；
- 历史数据不得在整个采集周期固定一次期权合约集合；
- 曲面不得只以散点集合存在而缺少模型、参数、质量和版本；
- 回测不得用未来才上市、未来才知道 settlement 或未来校准出的曲面；
- 风险不得只汇总静态 Greeks 而缺少 spot/vol/time 完整重估。

迁移期间允许同时保存 `vendor` 与 `internal` 估值。两者必须通过 `source` 和模型版本明确区分。

## 3. 分层架构和模块所有权

### 3.1 Market Data

建议模块：

```text
trading/market_data/
  service.py
  quality.py
  repository.py
  forward.py
```

职责：

- 聚合 spot/index、option chain、bid/ask/size、rates、dividends 和交易日历；
- 保留原始事件并产生标准化 point-in-time snapshot；
- 标记 stale、crossed、locked、zero bid、异常 spread、重复和时间错位；
- 根据利率、股息或 put-call parity 生成带方法说明的 forward；
- 管理数据来源优先级，但不进行期权模型校准。

核心类型：

```text
RateCurve(as_of, currency, nodes, day_count, source)
DividendInput(as_of, underlying_id, cashflows|yield, source)
ForwardEstimate(as_of, underlying_id, expiry, value, method, quality)
OptionMarketObservation(instrument_id, bid, ask, sizes, event_time, source)
MarketDataSnapshot(as_of, spot, forwards, chains, observations, quality_issues)
```

时间语义必须区分：

- `event_time`：市场或来源声明事件发生的时间；
- `received_time`：本系统收到事件的时间；
- `as_of`：计算结果允许使用的数据截止时间；
- `effective_from/to`：Instrument 或 reference data 生效区间。

### 3.2 Pricing Engine

建议模块：

```text
trading/pricing/
  models.py
  black_scholes.py
  black76.py
  implied_vol.py
  service.py
```

统一契约：

```text
PricingInput(
  underlying_or_forward, strike, time_to_expiry,
  risk_free_rate, dividend_yield, volatility, right
)

PricingResult(
  price, delta, gamma, theta, vega, rho,
  model, input_hash, diagnostics
)
```

第一阶段支持：

- Black-Scholes：股票和 ETF 欧式近似；
- Black-76：指数 forward、期货和现金结算产品；
- 稳健 implied-vol solver：价格边界检查、bracket、收敛容差和诊断；
- analytic Greeks；
- vendor/internal Greeks 对账。

数值计算使用 `float` 及成熟数值库；订单、现金、Ledger 和 Venue 约束继续使用 `Decimal`。所有数值结果在领域边界显式转换，不允许隐式混算。

美式股票期权后续增加离散股息模型或有限差分/二叉树模型。在实现之前必须将模型标记为欧式近似，不能伪装成精确估值。

### 3.3 Vol Engine

建议模块：

```text
trading/volatility/
  observation.py
  smile.py
  surface.py
  calibration.py
  arbitrage.py
  repository.py
```

处理流水线：

```text
Option quotes
 -> executable price interval
 -> forward and discounting
 -> implied-vol observations
 -> quality filters
 -> per-expiry smile calibration
 -> cross-expiry surface
 -> arbitrage diagnostics
 -> versioned SurfaceSnapshot
```

标准坐标：

- `k = ln(K / F)`：log-forward-moneyness；
- `w = sigma^2 T`：total variance；
- 时间使用明确 day-count 计算出的 year fraction。

首个正式校准模型采用 SVI。线性/单调插值可以作为 bootstrap 和 fallback，但不能被标记成 SVI 校准成功。

`SurfaceSnapshot` 至少包含：

```text
surface_id, underlying_id, as_of, model, model_version,
input_hash, forward_curve, parameters_by_expiry,
fit_errors, rejected_observations, arbitrage_diagnostics,
calibration_status
```

校准必须检查：

- 期权价格上下界；
- vertical spread 单调性；
- butterfly convexity；
- calendar total variance 单调性；
- bid/ask 范围内拟合率；
- parameter bounds、收敛状态和残差。

失败时保留失败结果和诊断。不得静默沿用旧曲面；是否允许 stale surface 必须由调用方策略显式配置。

### 3.4 Strategy Engine

策略分为四个职责：

```text
Universe -> Features -> Signal -> Structure -> Intent
```

- Universe：按产品、DTE、delta/moneyness、流动性和数据质量筛选；
- Features：IV rank、IV percentile、skew、term structure、realized vol、VRP；
- Signal：只表达可测试的市场假设；
- Structure：选择 legs、ratio、quantity 和退出规则；
- Intent：交给现有 risk、planner 和 execution 流程。

`StrategyContext` 将增加只读服务：

```text
valuation: ValuationService
surface: SurfaceView
features: FeatureSnapshot
```

策略不得：

- 调用 adapter；
- 在本地重新拟合曲面；
- 读取决策时点之后的数据；
- 根据成交结果反向修改历史信号；
- 用 vendor Greeks 缺失作为停止所有研究的唯一理由。

首个端到端策略为 SPXW Bull Put Spread。它将从 internal surface 获取 delta，并保留 vendor delta 对比字段。

### 3.5 Backtest

现有 `MarketSlice` 和确定性引擎继续作为核心。数据格式升级后，每个 slice 必须描述当时可见的动态合约宇宙。

回测事件顺序：

```text
reference/listing changes
 -> market events
 -> curve/surface calibration
 -> valuation and portfolio mark
 -> strategy decision
 -> pre-trade risk
 -> order scheduling
 -> later-slice execution
 -> lifecycle/settlement
 -> Ledger and metrics
```

强制约束：

- 信号在当前 slice 计算，最早在下一 slice 成交；
- 合约必须在决策时点已经上市且可交易；
- 校准输入必须满足 `event_time <= decision_time`；
- settlement 最终值只在官方可用时间之后进入系统；
- 缺失报价默认不可成交，不得用理论价制造 fill；
- 理论价可用于组合 mark，但结果必须标识 model-priced coverage；
- development、validation、test split 之间不得共享拟合参数；
- 所有配置、数据、模型版本和代码版本进入 audit hash。

回测输出除现有收益指标外增加：

- quote-priced/model-priced/unpriced coverage；
- vendor/internal IV 和 Greeks 偏差；
- surface calibration failure/stale rate；
- 按 IV rank、skew、term structure 和流动性分组的 PnL；
- 参数稳定性和 walk-forward 结果；
- capacity、spread、slippage 和成交敏感性。

### 3.6 Risk Analytics

现有静态敞口和交易前 limits 保留，并增加统一 `ScenarioEngine`：

```text
RiskSnapshot
  positions
  base market state
  base surface
  valuation model versions

Scenario
  spot shock
  parallel vol shock
  skew twist
  term twist
  rate shock
  time advance
```

每个情景对所有持仓完整重估，输出：

- portfolio PnL；
- instrument/structure/account/expiry PnL；
- Greeks before/after；
- margin 和 collateral 变化；
- 最差情景与风险限额占用。

`PnLExplain` 分解为 delta、gamma、theta、vega、rates、carry、交易、费用和 residual。Residual 超过阈值必须产生质量告警。

VaR/Expected Shortfall 第一版使用历史情景法。任何 VaR 都必须声明 horizon、confidence、lookback、sampling、估值方法和数据覆盖率。

## 4. 数据与持久化

建议采用三层数据：

```text
raw/          原始 provider payload 或不可变标准化事件
normalized/   point-in-time MarketDataSnapshot / MarketSlice
derived/      forwards / IV observations / surfaces / features / risk results
```

每个 derived artifact 必须包含：

- 唯一 ID；
- `as_of`；
- 输入 artifact ID 或 content hash；
- 模型名和版本；
- 参数；
- 代码版本；
- 质量状态及诊断。

schema 变更必须提升版本并提供显式迁移或拒绝旧版本，禁止以字段默认值掩盖语义变化。

## 5. 质量与可复现性

### 5.1 数据质量门禁

以下问题默认阻止校准或交易：

- 标的或 forward 缺失；
- crossed market；
- quote 超龄；
- 到期时间非正；
- 期权价格违反静态边界；
- InstrumentDefinition 或 multiplier 不明确；
- snapshot span 超过配置阈值。

zero bid、宽 spread、稀疏 wings 可配置为 warning 并降权，而不是自动当作零价值。

### 5.2 测试层次

```text
unit
  pricing identities, IV round-trip, analytic Greeks, SVI equations
property
  monotonicity, parity, convexity, deterministic hashes
fixture
  hand-reconcilable chains, malformed markets, calibration failures
integration
  Market Data -> Surface -> Strategy -> Backtest -> Risk
optional contract
  IBKR/Binance read-only comparison
```

关键数值测试必须使用独立 benchmark 或已知解析结果，不能用同一实现生成 expected value。

## 6. 分阶段落地与验收

### 阶段 A：Pricing Foundation

- 新增 pricing 类型、Black-Scholes、Black-76、IV solver 和 Greeks；
- 增加 rate/dividend/forward 类型；
- vendor/internal 对账报告；
- 数值 benchmark、边界、round-trip 和失败诊断测试。

验收：无 vendor Greeks 时能从标准化行情独立计算 IV 和 Greeks。

### 阶段 B：Volatility Surface

- IV observation 清洗；
- per-expiry smile 和 SVI；
- cross-expiry surface 查询；
- 无套利诊断、版本化存储和 CLI 报告。

验收：给定固定 snapshot 能确定性生成曲面、参数、质量报告和 hash。

### 阶段 C：Strategy Integration

- 扩展 StrategyContext；
- 增加 universe、features、signal 和 structure 层；
- Bull Put Spread 改用 internal surface delta；
- vendor/internal 差异进入策略决策审计。

验收：移除 vendor Greeks 的 fixture 后策略仍能选择合约并产生 Intent。

### 阶段 D：Point-in-time Backtest

- 动态期权宇宙；
- 数据集 schema 升级；
- slice 内 curve/surface artifact；
- walk-forward 和模型估值 coverage；
- 无未来数据属性测试。

验收：历史链滚动、上市/退市、到期和 settlement 全程无 look-ahead。

### 阶段 E：Risk Analytics

- ScenarioEngine；
- spot/vol/skew/term/rate/time 情景；
- PnL explain；
- historical VaR/ES；
- risk limits 和报告集成。

验收：同一持仓可从基础估值完整重估到最差情景，并能解释主要 PnL 来源。

### 阶段 F：Productization

- CLI：capture、calibrate、surface show、backtest、risk scenario；
- Notebook 示例和运行手册；
- 全量测试、compile、diff check；
- SPXW 固定数据端到端 golden scenario。

验收命令：

```bash
./pyenv/bin/python -m compileall -q trading tests
./pyenv/bin/python -m unittest discover -s tests -v
git diff --check
```

## 7. 完成定义

只有同时满足以下条件，整体改造才算完成：

- 五层都有清晰领域接口和可运行实现；
- 定价与 Greeks 不依赖外部 vendor 模型；
- 曲面可校准、查询、诊断、版本化和复现；
- 至少一个期权策略完全消费内部估值和研究特征；
- 回测使用动态 point-in-time 期权链并通过无前视测试；
- 风险支持完整情景重估、PnL explain、VaR 和 ES；
- CLI 和文档能重现 SPXW 端到端研究流程；
- 全量自动化验收通过，且不存在未说明的质量降级。

## 8. 当前实现与验收证据

以下设计已经落地：

| 层 | 实现 | 自动化证据 |
|---|---|---|
| Market Data | RateCurve、forward/parity、期权 quote 质量门禁、动态 point-in-time 合约宇宙 | `test_pricing.py`, `test_market_data_quality.py`, `test_series_capture.py` |
| Pricing | Black-Scholes、Black-76、解析 Greeks、带边界和诊断的 IV solver | `test_pricing.py` |
| Vol Engine | IV observations、确定性 SVI、标准 `g(k)` butterfly 与 calendar 检查、期限插值、版本化 SurfaceRepository | `test_volatility.py` |
| Strategy | StrategyContext valuation/surface/features、IV rank/percentile/skew/term、Bull Put Spread internal-surface delta | `test_internal_valuation.py` |
| Backtest | 逐 slice 内部估值、动态 universe、无 vendor Greeks、估值/曲面覆盖率、情景尾部指标 | `test_internal_valuation.py`, `test_options_research_end_to_end.py` |
| Risk | spot/vol/skew/term/rate/time 完整重估、结构/账户归因、PnL explain、historical VaR/ES | `test_risk_analytics.py` |
| CLI | `pricing option`、`vol calibrate`、`risk scenario` | `test_options_research_cli.py` |

SPXW golden 流程使用满足静态价格边界的五档期权链，不包含 vendor Greeks。测试证明从 quote 到 IV、SVI、surface delta 选腿、下一 slice 成交、风险情景和 replay 的闭环是确定性的。
