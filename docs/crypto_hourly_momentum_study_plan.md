# 加密货币小时级横截面动量研究执行手册

## 1. 文档目的

本文是一份按顺序执行的研究手册，用于检验以下假设：

> 在加密货币市场的每一个时段，只有少数币种具有显著的独立行情；一旦某个币种的动量启动，该方向通常会持续若干小时，并可能形成扣除真实交易成本后仍可交易的横截面策略。

本研究不以“找到一条赚钱回测曲线”为目标，而是依次回答三个不同问题：

1. **现象是否存在**：小时级行情是否真的集中在少数币种？
2. **现象是否可预测**：已经启动的动量是否能预测未来收益？
3. **现象是否可交易**：加入成交延迟、费用、滑点、资金费率和容量约束后是否仍有收益？

必须按以下顺序推进：

```text
研究协议
  -> 数据获取与治理
  -> 数据质量验收
  -> 动态币种池
  -> 行情稀疏性检验
  -> 动量持续性检验
  -> 策略原型
  -> 可执行回测
  -> 稳健性与样本外检验
  -> 模拟盘
  -> 是否晋级的结论
```

每个阶段都有明确产物和退出条件。上一阶段未验收，不进入下一阶段。

---

## 2. 研究范围与第一版约定

第一版研究主动缩小范围，避免同时修改过多变量。

| 项目 | 第一版约定 |
| --- | --- |
| 交易场所 | Binance |
| 产品 | USDⓈ-M USDT 线性永续合约 |
| K线周期 | 1小时 |
| 内部时区 | UTC |
| 研究起点 | 2020-01-01，或交易所可得的最早完整日期 |
| 研究终点 | 下载时最近一个完整 UTC 日，不包含未完成K线 |
| 方向 | 同时研究正向和负向动量 |
| 调仓频率 | 每小时 |
| 最早成交 | 信号K线结束后的下一根K线 |
| 基准信号 | 12小时波动调整残差动量 |
| 前瞻期限 | 1、4、12、24、48小时 |
| 初始策略 | 多头版与市场中性多空版各一套 |

为什么第一版选择永续合约：它与最终可做多、可做空的交易对象一致，并且可以显式纳入资金费率。现货和永续不能混成同一个数据集。后续可以把现货作为独立稳健性样本，但不能静默填补永续数据缺口。

当前仓库的 Binance 历史归档能力主要覆盖 BTCUSDT 现货日线。本研究开始前，需要新增“多币种、USDT 永续、1小时K线”的 Source → Canonical → Quality Gate → immutable Release → Catalog 数据链路。

---

## 3. 研究协议：编码前先冻结的问题

新建研究目录：

```text
studies/crypto_hourly_cross_sectional_momentum/
  README.md
  protocol.yaml
  universe.py
  data_quality.py
  sparsity.py
  event_study.py
  portfolio_sort.py
  strategy.py
  backtest.py
  robustness.py
  report.py
  outputs/
```

`protocol.yaml` 至少记录：

```yaml
study_id: crypto-hourly-cross-sectional-momentum-v1
hypothesis:
  sparsity: significant idiosyncratic moves are concentrated in a minority of coins
  persistence: momentum activation predicts same-direction future returns
venue: binance
market_type: usdt_margined_perpetual
bar_interval: 1h
timezone: UTC
decision_time: bar_close
earliest_execution: next_bar_open
formation_hours: [3, 6, 12, 24, 48, 72]
forward_hours: [1, 4, 12, 24, 48]
selection_quantiles: [0.05, 0.10, 0.20]
primary_formation_hours: 12
primary_selection_quantile: 0.10
universe:
  minimum_history_hours: 720
  liquidity_lookback_hours: 720
  liquidity_measure: trailing_median_quote_volume
cost_scenarios_bps_per_side: [2, 5, 10, 20]
```

研究协议还要用自然语言回答：

- 什么结果支持“行情稀疏性”？
- 什么结果支持“动量持续性”？
- 什么结果会推翻假设？
- 主结果是哪一个，哪些只是稳健性检验？
- 哪段时间用于探索、验证和最终样本外？
- 最终样本外数据允许打开几次？

建议提前写下以下否决条件：

- 扣除合理成本后，样本外多空组合收益不为正；
- 效果只存在于一个孤立参数点；
- 利润主要来自无法以合理容量成交的极低流动性币种；
- 大部分利润来自一两个币种或单一月份；
- 使用 point-in-time 动态币种池后效果消失；
- 下一根K线成交后效果消失；
- 资金费率和退市处理后效果消失。

### 阶段 0 检查清单

- [ ] 创建研究目录和 `protocol.yaml`
- [ ] 明确主假设、辅助假设和否决条件
- [ ] 固定第一版主参数，不根据结果临时改变
- [ ] 固定时间切分规则
- [ ] 记录代码版本、依赖版本和随机种子

**退出条件**：另一个人只阅读协议，就能复述研究对象、信息边界、主检验和失败标准。

---

## 4. 阶段 1：获取真实小时级数据

### 4.1 需要的数据

### 必需数据

小时K线：

| 字段 | 含义 |
| --- | --- |
| `instrument_id` | 稳定的内部合约标识 |
| `symbol` | 交易所 symbol |
| `open_time` | K线开始时间 |
| `close_time` | K线结束时间 |
| `available_time` | 研究中最早可用时间 |
| `open/high/low/close` | OHLC |
| `base_volume` | 基础资产成交量 |
| `quote_volume` | USDT 成交额 |
| `trade_count` | 成交笔数 |
| `taker_buy_base_volume` | 主动买入量 |
| `taker_buy_quote_volume` | 主动买入成交额 |

合约参考数据：

- 上线时间；
- 下线时间；
- 合约状态；
- 基础资产和计价资产；
- 合约类型；
- tick size、step size、最小名义金额及其生效区间。

永续辅助数据：

- funding rate 及结算时间；
- mark price；
- index price；
- open interest，若历史覆盖足够完整。

### 可后补数据

- 盘口价差；
- 盘口深度；
- 逐笔成交；
- 清算数据；
- 多空账户比例。

第一轮不依赖可后补数据得出预测结论，但进入真实成本和容量评估前至少要有价差或保守滑点模型。

### 4.2 推荐数据来源

优先使用 Binance 官方公开历史归档，并用公开 REST API：

- 补最近尚未进入月度归档的数据；
- 获取交易规则与合约状态；
- 对随机月份进行交叉校验。

每次下载必须保存原始文件、校验和、请求参数或归档 URL、下载时间和 provider receipt。不要把下载后的 CSV 直接当成正式研究数据。

数据必须经过仓库既有治理链路：

```text
Binance Source
  -> 原始归档与 receipt
  -> Canonical 小时K线/资金费率/参考数据
  -> 数据质量门禁
  -> 不可变 Dataset Release
  -> Catalog 注册
  -> Study 绑定固定 Release 和 hash
```

### 4.3 建议的物理分区

```text
source/provider=binance/dataset=usdm_klines/
  symbol=BTCUSDT/interval=1h/event_year=2024/event_month=01/

canonical/market/ohlcv/
  asset_class=crypto/venue=binance/product=perpetual/
  instrument=BTC-USDT-PERP/interval=1h/event_year=2024/event_month=01/

canonical/derivatives/funding/
  asset_class=crypto/venue=binance/product=perpetual/
  instrument=BTC-USDT-PERP/event_year=2024/event_month=01/
```

正式研究只通过 `DatasetClient` 和批准的 Dataset Release 读取，不在研究脚本中拼接物理路径。

### 4.4 下载与发布验收

- [ ] 能列出每个历史时点真实存在的 USDT 永续合约
- [ ] 原始归档文件保留并具有 SHA-256
- [ ] Canonical schema 与单位固定
- [ ] `event_time`、`close_time`、`available_time` 含义明确
- [ ] 每次发布产生新的不可变 Release，不覆盖旧版本
- [ ] Dataset Catalog 能展示 coverage、quality、provider、venue 和 hash
- [ ] 随机抽取至少 5 个币种、12 个月份与官方数据核对

**退出条件**：研究可以绑定一份固定 Release，其他人能够用相同 Release 和代码得到完全相同的行数与内容哈希。

---

## 5. 阶段 2：数据质量验收

为每个币种、每个月生成质量报告，至少检查：

### 5.1 结构检查

- 主键 `(instrument_id, open_time)` 唯一；
- 时间严格按小时排列；
- 所有时间带 UTC 时区；
- `open_time < close_time <= available_time`；
- 只包含已完成K线；
- OHLC 满足 `low <= open, close <= high`；
- 价格为正、成交量非负；
- 没有无说明的重复或静默填充。

### 5.2 缺口检查

加密货币是 24×7 市场。正常交易期间缺少一根小时K线就需要解释：

- 交易所停机；
- 合约暂停；
- 合约刚上线或已经下线；
- 下载失败；
- 原始归档缺失。

不要默认用前值填充 OHLC。缺失数据应保留为缺失，并在该时点把币种移出可交易池，除非研究协议定义了其他可审计处理。

### 5.3 异常值检查

- 单小时收益绝对值超过预设阈值；
- `high / low` 极端；
- 成交额突然为零；
- 与相邻小时价格相差数个数量级；
- 合约面值或 symbol 迁移；
- 官方历史文件之间存在修订。

异常值不能因为“不好看”而删除。每一个排除都要有原因代码和审计表。

### 5.4 资金费率对齐

资金费率应在真实结算时刻进入持仓收益。不能平均摊到事前，也不能使用尚未公布的数据构造信号。

### 阶段 2 检查清单

- [ ] 生成全市场数据质量总表
- [ ] 生成每币种 coverage 图
- [ ] 记录缺口、重复和异常值处置
- [ ] 验证 24×7 小时网格
- [ ] 验证资金费率时间对齐
- [ ] 确认没有把未完成K线用于决策

**退出条件**：所有进入研究的记录通过质量门禁；未通过记录被隔离，而不是被研究代码临时修补。

---

## 6. 阶段 3：构建 point-in-time 动态币种池

不能用今天仍然存在的币种回看历史。每个小时都必须重新确定当时可交易的币种池。

币种 (i) 在时刻 (t) 可进入币种池，当且仅当：

1. 当时已经上线且尚未下线；
2. 当前处于可交易状态；
3. 至少拥有 720 小时历史数据；
4. 最近 24 小时没有关键K线缺失；
5. 过去 30 天流动性达到阈值；
6. 价格和成交量字段在时刻 (t) 已经可用；
7. 没有处于协议定义的上线冷静期或退市风险窗口。

建议流动性指标：

\[
Liquidity_{i,t}=\operatorname{median}(QuoteVolume_{i,t-720:t})
\]

第一版可以保留每小时流动性排名前 80% 且达到绝对成交额下限的币种。绝对阈值必须在探索期确定，不得用完整样本期均值筛选。

输出一张 point-in-time universe 表：

```text
decision_time
instrument_id
eligible
exclusion_reason
history_hours
trailing_quote_volume
liquidity_rank
listing_age_hours
```

### 必做诊断

- 每小时币种池数量；
- 新上市和退市数量；
- 各币种进入样本的首尾时间；
- 流动性分布；
- 被每种规则排除的观测数；
- 结果对流动性阈值的敏感性。

### 阶段 3 检查清单

- [ ] 所有筛选仅使用时刻 (t) 已知信息
- [ ] 不存在幸存者偏差
- [ ] 不用未来成交量决定历史币种池
- [ ] 上线与退市边界经过人工抽查
- [ ] 每一个排除都有结构化原因

**退出条件**：任意选择一个历史小时，都能解释当时有哪些币、为什么进入或未进入研究。

---

## 7. 阶段 4：检验“只有少数币种有行情”

这一阶段不构造交易策略，只检验行情稀疏性。

### 7.1 收益定义

使用已完成K线收盘价：

\[
r_{i,t}=\log(P_{i,t}/P_{i,t-1})
\]

用过去 7 天或 30 天的滞后数据估计波动率：

\[
z_{i,t}=\frac{r_{i,t}}{\sigma_{i,t}^{lagged}}
\]

不能使用包含当前小时之后数据的全样本波动率。

### 7.2 剔除共同市场因子

原始上涨可能只是 BTC 带动全市场。至少并行研究三种收益：

1. 原始收益；
2. 减去当时动态币种池的等权市场收益；
3. 对 BTC、ETH 和市场等权收益做滚动回归后的残差收益。

例如：

\[
r_{i,t}=\alpha_{i,t}+\beta_{BTC,i,t}r_{BTC,t}
+\beta_{MKT,i,t}r_{MKT,t}+\epsilon_{i,t}
\]

滚动回归只能使用 (t) 之前的数据。核心稀疏性结论以残差收益 \(\epsilon_{i,t}\) 为主，原始收益作为对照。

### 7.3 核心指标

异常行情覆盖率：

\[
Breadth_t(c)=\frac{\#\{|z_{i,t}|>c\}}{N_t},\quad c\in\{1,2,3\}
\]

前 (K) 个币种的绝对行情贡献：

\[
TopKShare_t=\frac{\sum_{i\in TopK}|r_{i,t}^{resid}|}
{\sum_j|r_{j,t}^{resid}|}
\]

集中度：

\[
w_{i,t}=\frac{|r_{i,t}^{resid}|}{\sum_j|r_{j,t}^{resid}|},\qquad
HHI_t=\sum_i w_{i,t}^2
\]

同时报告：

- Top 1、Top 5、Top 10 和 Top 10% Share；
- 横截面离散度；
- 横截面偏度和峰度；
- 正向异常和负向异常分别的覆盖率；
- 同方向币种比例，区分普涨普跌与个币行情。

### 7.4 支持假设的证据标准

第一版不强行规定一个必然正确的经济阈值，但在看结果前要固定主展示：

- `Top 10% Share` 的时间序列和分布；
- `Breadth(|z| > 2)` 的分布；
- 原始收益和残差收益的对比；
- 牛市、熊市、震荡市分别统计；
- 按年份分别统计。

如果集中度只在全市场暴涨暴跌时升高，而残差收益并不集中，则原始“少数币有行情”的解释不成立，需要改写为市场状态假设。

### 阶段 4 产物

- `sparsity_summary.parquet`
- `sparsity_by_regime.parquet`
- `breadth_timeseries.png`
- `top_share_distribution.png`
- `raw_vs_residual_sparsity.png`
- 一页结论：支持、部分支持或拒绝

**退出条件**：明确回答行情是否稀疏、稀疏的是总收益还是独立收益、在哪些市场状态下出现。

---

## 8. 阶段 5：检验动量是否持续

这一阶段仍然先研究预测能力，不直接引入复杂仓位规则。

### 8.1 基准动量分数

形成期 (L) 小时的残差累计收益：

\[
M_{i,t,L}=\frac{\sum_{k=0}^{L-1}r^{resid}_{i,t-k}}
{\sigma^{lagged}_{i,t}\sqrt{L}}
\]

第一版主信号使用 (L=12)，其余形成期只做参数稳定性检验。

可选确认变量单独研究，不立即加入主信号：

- 成交量异常；
- 突破过去高点/低点；
- open interest 变化；
- 市场总体状态。

### 8.2 未来收益标签

\[
F_{i,t,H}=\log(P_{i,t+H}/P_{i,t}),
\quad H\in\{1,4,12,24,48\}
\]

标签可以存在于 Study，但不得进入生产 Factor，也不得参与时刻 (t) 的币种池、特征或归一化。

### 8.3 事件研究

动量启动事件定义为分数第一次穿越阈值：

```text
正向事件：M(t) >= threshold 且 M(t-1) < threshold
负向事件：M(t) <= -threshold 且 M(t-1) > -threshold
```

为避免一个连续行情被重复计算：

- 主结果使用“首次穿越”；
- 同一币种设置至少 12 小时冷却期；
- 另行报告允许重叠事件的敏感性结果。

绘制事件发生后 0～72 小时平均累计残差收益，并报告：

- 均值、中位数；
- 胜率；
- 分位数区间；
- block bootstrap 置信区间；
- 正负动量分别的路径；
- 按流动性、年份和市场状态分组的路径。

### 8.4 横截面排序组合

每小时按 (M_{i,t,L}) 把币种分为 5 组或 10 组，观察未来收益是否随排名单调变化。

主检验：

\[
Spread_H=R_{top,H}-R_{bottom,H}
\]

支持动量的证据不应只是 top-bottom 为正，还应包括：

- 分组未来收益大致单调；
- 多空两侧分别有贡献；
- 相邻参数结果方向一致；
- 不依赖单一币种和单一时期；
- 样本外仍然存在。

### 8.5 回归检验

使用横截面预测回归作为辅助证据：

\[
r_{i,t+H}=\alpha_t+\beta M_{i,t}
+\gamma_1 Volatility_{i,t}
+\gamma_2 Liquidity_{i,t}
+\gamma_3 ListingAge_{i,t}+u_{i,t}
\]

报告经济效应与置信区间，不只报告 p 值。由于持有期重叠、同币种自相关和同小时共同冲击，使用适合面板和重叠标签的稳健标准误或 block bootstrap。

### 阶段 5 产物

- `momentum_events.parquet`
- `event_path_by_horizon.parquet`
- `portfolio_sort_results.parquet`
- `predictive_regression_results.parquet`
- `event_study.png`
- `decile_monotonicity.png`
- `formation_holding_heatmap.png`

**退出条件**：明确回答动量是否持续、持续多长时间、正负方向是否对称、效果是否只是低流动性溢价。

---

## 9. 阶段 6：建立最小策略原型

只有阶段 5 通过后才定义策略。

### 9.1 策略 A：多头动量

每小时：

1. 读取当时动态币种池；
2. 计算 12 小时波动调整残差动量；
3. 选择排名前 10% 的币种；
4. 按波动率倒数分配权重；
5. 单币权重设上限；
6. 信号K线结束后，在下一根K线成交；
7. 持有固定 (H) 小时，或按独立分批持仓实现重叠组合。

### 9.2 策略 B：市场中性多空动量

- 做多排名前 10%；
- 做空排名后 10%；
- 多空名义金额相等；
- 可进一步控制 BTC beta 或市场 beta；
- 多头和空头分别做波动率缩放；
- 单币、单侧和总杠杆均设上限。

### 9.3 必须提前固定的组合规则

- 权重公式；
- 单币权重上限；
- 总杠杆；
- 缺失信号处理；
- 并列排名处理；
- 新上市币处理；
- 退市前后处理；
- 资金不足时的缩放规则；
- 持有期重叠时的子组合聚合规则；
- 是否允许同一币种在相邻小时反向。

不要在第一版加入机器学习、止盈止损、多重择时和大量例外规则。先确认原始效应能否映射为稳定组合收益。

### 阶段 6 检查清单

- [ ] Factor 与未来标签完全分离
- [ ] Strategy 只消费当时可用 Factor
- [ ] 批量计算与逐小时 replay 结果一致
- [ ] 每一个目标权重都能解释
- [ ] 参数来自研究协议，而非最佳回测点

**退出条件**：给定任一历史决策时刻，可以仅用当时信息重建完全相同的目标持仓。

---

## 10. 阶段 7：可执行回测

### 10.1 时间语义

若使用 10:00–11:00 UTC 的K线收盘计算信号：

- 决策时间不早于 11:00 UTC；
- 不得按该K线收盘价无摩擦成交；
- 基准回测在下一根K线开盘成交；
- 保守场景增加额外延迟或不利滑点。

必须显式防止：

- `shift` 方向错误；
- 使用全样本标准化；
- 当根K线 close 产生信号又按同一 close 成交；
- 用未来币种池或未来成交量筛选；
- 在退市后仍以最后价格平仓。

### 10.2 收益组成

净收益必须拆解为：

```text
价格 PnL
+ funding PnL
- 手续费
- 买卖价差
- 滑点/冲击成本
- 强平或借贷等其他成本
= 净 PnL
```

资金费率方向要正确：持仓跨越结算时点才产生 funding cash flow。

### 10.3 成本场景

至少报告：

| 场景 | 单边总摩擦假设 |
| --- | --- |
| 零成本 | 0 bps，仅用于观察信号上限 |
| 乐观 | 2 bps |
| 基准 | 5 bps |
| 保守 | 10 bps |
| 压力 | 20 bps 或更高 |

实际费率应根据账户等级、maker/taker 假设和历史盘口证据更新。不能把“限价单”默认当成一定成交，也不能同时假设 maker 费率和立即完全成交。

盈亏平衡成本：

\[
BreakEvenCost=\frac{GrossPnL}{OneWayTurnover}
\]

### 10.4 容量约束

每笔计划交易额不得超过过去成交额的固定比例：

\[
Participation_{i,t}=\frac{|TradeNotional_{i,t}|}
{TrailingHourlyQuoteVolume_{i,t}}
\]

至少测试 1、5、10 bps 的成交参与率上限或其他经过论证的阈值，并报告不同资金规模下的收益衰减。

### 10.5 回测报告

必须包含：

- 年化收益、波动率、Sharpe、Sortino、Calmar；
- 最大回撤及持续时间；
- 毛收益与净收益；
- 换手率、交易次数、平均持有期；
- funding、费用和滑点分别贡献；
- 多头和空头分别贡献；
- 每个币种、月份和年份的贡献；
- 最差 1 日、1 周、1 月；
- 市场 beta、BTC beta、行业或主题集中度；
- 不同资金规模下的容量；
- 盈亏平衡成本。

### 阶段 7 否决检查

- [ ] 下一根K线成交后仍有收益
- [ ] 基准成本后仍有收益
- [ ] 收益不是资金费率方向错误造成
- [ ] 收益不是退市币价格处理错误造成
- [ ] 收益不是少数异常币种垄断
- [ ] 保守成本下结果没有灾难性恶化

**退出条件**：回测结果可以逐项还原为市场收益、持仓、成交、费用和资金费率，不存在无法解释的 PnL。

---

## 11. 阶段 8：稳健性和样本外验证

### 11.1 时间切分

根据真实数据终点调整，但原则固定：

- 探索期：最早数据至 2022-12-31；
- 验证期：2023-01-01 至 2024-12-31；
- 最终样本外：2025-01-01 至数据终点。

如果数据覆盖不足，则按约 50% / 25% / 25% 的连续时间切分。禁止随机打乱小时观测。

最终样本外只在协议、参数、币种池和成本模型冻结后打开一次。打开后若修改策略，原最终样本外立即降级为已使用验证集，新版本需要未来数据或新的独立市场验证。

### 11.2 Walk-forward

主样本外结果之外，再执行滚动验证：

```text
过去 24 个月估计波动率、beta 和允许估计的参数
  -> 接下来 3 个月固定运行
  -> 窗口向前滚动
```

策略主参数不在每个窗口中寻找最优值；只有协议允许的统计估计量随窗口更新。

### 11.3 必做稳健性检验

- 形成期：3、6、12、24、48、72小时；
- 持有期：1、4、12、24、48小时；
- 选择比例：5%、10%、20%；
- 等权与波动率倒数权重；
- 原始动量与残差动量；
- 不同流动性门槛；
- 排除最小市值/最低流动性组；
- 去掉贡献最大的 1、5、10 个币种；
- 去掉表现最好的月份；
- 牛市、熊市、震荡市；
- 高波动与低波动时期；
- 正动量与负动量分别检验；
- 不同成本、延迟和参与率；
- 现货数据作为独立市场结构对照，若后续具备数据。

稳健的结果应该形成一片参数区域，而不是一个孤立的最优点。

### 11.4 多重检验

记录尝试过的所有参数和模型，不只保存成功结果。参数组合较多时，使用适当的多重检验修正、deflated Sharpe、reality check 或其他数据窥探调整。至少在报告中披露测试数量。

### 阶段 8 检查清单

- [ ] 探索、验证、最终样本外严格按时间隔离
- [ ] 每次查看最终样本外都有记录
- [ ] 参数邻域结果方向一致
- [ ] 去掉头部贡献币种后结论仍成立
- [ ] walk-forward 多数窗口为正
- [ ] 结论不依赖单一市场状态

**退出条件**：形成“接受、限制性接受或拒绝”结论，并清楚说明适用市场、成本和容量边界。

---

## 12. 阶段 9：模拟盘验证

回测通过后，不直接实盘。先连续运行至少 4～8 周模拟盘：

1. 每小时等待K线完整结束；
2. 保存输入 Dataset/Feature hash；
3. 生成并持久化目标权重；
4. 按真实盘口估计可成交价格；
5. 模拟 maker/taker 成交与未成交；
6. 记录资金费率、费用、延迟和数据缺口；
7. 将实时决策与历史 replay 对比。

每天检查：

- 是否漏K线或晚到；
- batch 与 replay 信号是否一致；
- 模拟成交和K线回测成交差异；
- 实际观察价差是否超出成本假设；
- 调仓时间是否发生漂移；
- symbol 上线、下线与规则变更是否被正确处理。

模拟盘通过标准应提前写入协议，例如：

- 运行可用率达到 99.5%；
- 决策可重复率为 100%；
- 实际成本中位数不高于回测基准假设；
- paper PnL 与相同信号的 replay PnL 差异可解释；
- 不存在越过风险限额或币种池规则的目标持仓。

---

## 13. 最终研究报告模板

最终报告按以下顺序撰写：

1. 一句话假设；
2. 数据 Release、hash、覆盖区间和质量；
3. point-in-time 币种池规则；
4. 稀疏性检验结果；
5. 动量事件研究；
6. 排序组合和预测回归；
7. 最小策略定义；
8. 毛收益、成本和净收益；
9. 样本外与 walk-forward；
10. 参数、市场状态和容量稳健性；
11. 失败模式与已知限制；
12. 结论：拒绝、继续研究、进入模拟盘或允许小资金试运行。

结论不能只写“Sharpe 为多少”，而要逐项回答：

- 行情是否真的只集中在少数币种？
- 这是共同市场行情还是币种独立行情？
- 动量启动后平均持续多久？
- 正向和负向动量是否对称？
- 哪些市场状态下有效或失效？
- 收益是否足够覆盖真实成本？
- 策略容量大致是多少？
- 最容易导致结论失真的偏差是什么？

---

## 14. 推荐执行里程碑

### M1：研究协议冻结

产物：`protocol.yaml`、研究 README、时间切分和否决条件。

### M2：真实数据 Release

产物：小时K线、合约参考数据、资金费率的不可变 Release 与质量报告。

### M3：动态币种池

产物：逐小时 universe 表和幸存者偏差审计。

### M4：现象验证

产物：稀疏性报告、事件研究、排序组合与预测回归。

### M5：最小可执行回测

产物：多头版、多空版、成本拆解和容量分析。

### M6：样本外结论

产物：最终样本外、walk-forward、稳健性矩阵和研究结论。

### M7：模拟盘

产物：连续运行记录、实际成本校准和 replay 一致性报告。

---

## 15. 研究日志要求

每次实验追加记录，不覆盖历史结果：

```text
experiment_id
created_at
git_commit
dataset_release_id
dataset_sha256
protocol_version
parameters
sample_period
cost_model
result_summary
decision
notes
```

建议遵守三条规则：

1. 失败实验也保存；
2. 任何参数变化都产生新的 experiment ID；
3. 报告中的每张表和图都能追溯到实验、代码和 Dataset Release。

---

## 16. 立即开始时只做这五件事

不要同时开始写策略和回测。第一轮按以下顺序执行：

1. 创建 `studies/crypto_hourly_cross_sectional_momentum/` 和研究协议；
2. 为 Binance USDT 永续 1小时K线、合约状态和资金费率设计 Canonical schema；
3. 实现官方历史归档下载、校验、质量门禁和不可变 Release；
4. 生成 point-in-time 动态币种池；
5. 先完成稀疏性统计，再决定是否继续做动量策略。

第一项真正需要回答的研究问题不是“回测赚了多少”，而是：

> 剔除 BTC 和全市场共同波动后，每个小时的绝对残差收益是否仍由少数币种贡献，并且这种集中现象是否跨年份、流动性分组和市场状态稳定存在？

如果这个问题的答案是否定的，应停止或修改原假设；如果答案是肯定的，再进入动量持续性和策略阶段。

### 16.1 已实现的一条命令入口

正式 Logical Product 名称为：

```text
market.ohlcv.crypto.binance.usdm-perpetual.1h
```

从仓库根目录执行以下命令，可以一次完成官方 Raw 归档下载、Canonical OHLCV 整理、质量检查、
不可变 Dataset Release 发布、Study 创建、Release 内容哈希绑定和研究脚本生成：

```bash
./pyenv/bin/python -m kairos study start crypto-hourly-momentum \
  --start 2020-01-01T00:00:00+00:00 \
  --end 2026-07-01T00:00:00+00:00
```

默认不指定 `--symbol` 时，下载器会结合 Binance 官方历史归档目录和当前合约元数据发现全部历史
USDT 本位永续合约。原始压缩包和 receipt 保存到：

```text
data/source/provider=binance/dataset=usdm_klines/
```

整理后的不可变 Release 保存到：

```text
data/canonical/market/ohlcv/asset_class=crypto/venue=binance/product=usdm-perpetual/interval=1h/
```

Study 和生成的 `study.py` 保存到：

```text
data/study-workspaces/crypto-hourly-momentum/1.0.0/
```

命令输出中的 `next` 是运行研究脚本的下一条命令。也可以手动执行：

```bash
./pyenv/bin/python data/study-workspaces/crypto-hourly-momentum/1.0.0/study.py
```

首次验证网络、字段和时间窗口时，可以用 `--symbol` 做有界烟雾测试；这不是正式全市场研究：

```bash
./pyenv/bin/python -m kairos \
  --lake-root example-output/crypto-momentum-smoke \
  study start crypto-hourly-momentum-smoke \
  --start 2025-01-01T00:00:00+00:00 \
  --end 2025-01-21T00:00:00+00:00 \
  --symbol BTCUSDT
```

`--start/--end` 表示K线 `period_start` 的右开窗口。由于 Study 使用 point-in-time safe 的
`available_time`，系统会自动把 Study 查询窗口向后移动一小时，避免遗漏最后一根K线或使用尚未
完成的K线。

### 16.2 下载进度、缓存和断点恢复

可以先执行只读规划，不下载K线：

```bash
./pyenv/bin/python -m kairos study plan crypto-hourly-momentum \
  --start 2020-01-01T00:00:00+00:00 \
  --end 2026-07-20T00:00:00+00:00
```

规划和正式下载使用同一个可复用的 `TerminalProgressMatrix` 组件。矩阵按年份和月份聚合，每个
单元格显示 `已完成官方归档文件数 / Binance 当月实际发布文件数`。规划器先读取 Binance S3 官方
对象索引中的真实 `.zip` key，不再生成 `全部 symbol × 全部月份` 的笛卡尔积，因此合约尚未上市的
月份不会进入下载队列。月度 manifest 缓存 6 小时，当前月份的日度 manifest 缓存 1 小时。

在交互式终端中首次只绘制一次，后续通过
ANSI 光标控制原地更新单元格及底部汇总，不会不断向下追加日志。例如：

```text
Binance USD-M perpetual 1h acquisition progress (completed / planned)
          01      02      03  ...
2020 | 787/787 787/787 420/787 ...
2021 |   0/787   0/787   0/787 ...
Progress: 1994/62173 downloaded=160 cached=1120 unavailable=714 failed=0
```

重定向到文件或 CI 等非 TTY 环境无法移动光标时，组件自动退化为低频矩阵快照。矩阵写入 stderr，
因此 `--format json` 的 stdout 仍然保持为合法 JSON。

字段含义：

- `downloaded`：本次新下载并原子落盘的月度 ZIP；
- `cached`：Raw 目录已经存在、直接复用且没有再次联网的 ZIP；
- `unavailable`：只在官方索引与实际对象短暂不一致时使用；正常规划下应接近 0；
- `failed`：网络或服务异常，任务结束时会明确失败而不会发布不完整 Release；
- `rows`：当前已经解析并落入本轮内存批次的K线行数。

每个月度文件先写入 `payload.zip.part`，完整写入后再原子重命名为 `payload.zip`，避免中断后把半个
ZIP 当成有效缓存。`receipt.json` 缺失时会根据已有完整 payload 自动补建。

下载可以使用 `Ctrl-C` 随时中断。第一次 `Ctrl-C` 进入 graceful shutdown：停止派发新文件，等待
最多 12 个在途请求完成原子落盘，保存 Raw ZIP/receipt 后以退出码 130 结束；第二次 `Ctrl-C` 才会
立即强制退出。已经完成的 `payload.zip` 和 receipt 会保留；重新执行完全相同的命令即可恢复，系统
会显示为 `cached` 并只下载缺失文件：

```bash
./pyenv/bin/python -m kairos study start crypto-hourly-momentum \
  --start 2020-01-01T00:00:00+00:00 \
  --end 2026-07-20T00:00:00+00:00
```

只有全部计划分区完成且不存在 `failed` 后，系统才进入 Canonical 整理、质量检查、Release 发布和
Study 创建。Raw 数据因此可以跨重跑、跨 Study 和后续增量窗口复用。

---

## 17. 与仓库现有文档的关系

- 数据发现、Release、Catalog 和时间语义遵循 [study_data_guide.md](study_data_guide.md)；
- 从假设到 Study、Factor、Strategy 和 Run 的流程参考 [tutorial_first_study.md](tutorial_first_study.md)；
- 预测能力、组合映射和可执行性分层遵循 [study_validation_framework.md](study_validation_framework.md)；
- 研究、回测和实盘一致性遵循 [study_strategy_backtest_live_convergence_plan.md](study_strategy_backtest_live_convergence_plan.md)。

本文只定义本次“加密货币小时级横截面动量”研究的具体执行顺序和验收标准；通用平台能力不在研究目录内重复实现。
