# 美股横截面动量研究数据建设与治理方案

## 1. 文档目的

本文定义 Kairos 使用 Massive 建设美股横截面动量研究数据的完整方案，包括研究边界、数据产品、
身份与时间契约、复权方法、动态股票池、存储布局、质量门禁、发布流程、实施阶段和验收标准。

本文首先解决“在任意历史决策时点，系统实际知道什么、允许选择什么、能够以什么价格交易”这三个
问题。因子公式、组合优化和策略参数只有在数据链路达到相应质量等级后才进入研究。

目标链路为：

```text
Massive reference / aggregates / corporate actions
        |
        v
Immutable Source archive + request receipt
        |
        v
Point-in-time identity and reference mapping
        |
        v
Canonical raw prices + corporate actions
        |
        v
Adjusted prices + total-return series
        |
        v
Point-in-time daily universe + liquidity features
        |
        v
Quality Gate + immutable Dataset Release
        |
        v
Frozen Study -> Factor -> Backtest
```

本文遵循仓库既有的 `Dataset Product -> immutable Release -> Study` 数据治理模型。研究代码不得直接
调用 Massive SDK、读取 Source 文件或依赖浮动的 `latest` 数据。

---

## 2. 当前系统基础与关键缺口

### 2.1 可以直接复用的能力

现有 Binance 全市场小时数据链路已经具备以下模式：

- 自动发现历史标的；
- 保存供应商原始归档和 receipt；
- 转换为统一 canonical OHLCV；
- 按主键增量合并并发布内容寻址的不可变 Release；
- 执行质量检查并登记到 Data Catalog；
- Study 固定 Release ID、内容哈希和查询窗口；
- 通过 `ResearchDataClient` 读取，不拼接物理路径。

Massive 适配器已有以下基础：

- REST 请求、分页和原始响应归档；
- 股票日聚合数据准备；
- ticker reference 和内部 Catalog 导入；
- splits、dividends 和 ticker change 解码；
- Parquet、manifest、lineage、coverage 和 quality 元数据；
- 美股交易日历和 `available_time` 基础语义。

### 2.2 当前实现不能直接用于正式动量回测的原因

现有股票日聚合管线以单 ticker、单窗口为单位，并使用 `equity:us:{ticker}` 构造身份。它适合局部
研究准备，不足以形成无幸存者偏差的全市场面板：

1. 没有全市场 point-in-time 股票池；
2. ticker 变更前后可能被视为不同证券；
3. 只保存 Massive `adjusted=true` 结果，缺少原始价格和内部复权审计；
4. 没有完整区分普通股、ETF、优先股、权证、单位、基金和 OTC；
5. 退市、停牌、缺失下载和正常无成交尚未形成明确状态；
6. 股票日线没有接入统一 `DataProductContract`、Connector、质量晋级和 Study 一键启动流程；
7. 当前通用 OHLCV 质量规则不能独立证明历史股票池完整或公司行为处理正确。

因此，本项目不是给现有单 ticker 命令套一层循环，而是建立一个独立的“美股全市场日频数据产品”。

---

## 3. 第一版研究范围

第一版主动限制范围，先获得一套可审计、可复现的日频横截面研究数据。

| 项目 | 第一版约定 |
| --- | --- |
| 数据供应商 | Massive |
| 交易市场 | 美国主要证券交易所 |
| 证券类型 | 普通股；其他类型默认排除 |
| 计价币种 | USD |
| 数据频率 | 日频 |
| 目标历史 | 2005-01-01 至最近一个完整交易日 |
| 交易日历 | US Securities，America/New_York |
| 决策时点 | 月末交易日收盘后 |
| 最早成交 | 下一交易日开盘；VWAP/收盘作为敏感性场景 |
| 主信号 | 12-1 个月总收益动量 |
| 辅助信号 | 6-1、3-1、12个月含最近月、短期反转 |
| 股票池 | point-in-time 动态股票池 |
| 价格门槛 | 原始收盘价不低于 5 USD |
| 流动性门槛 | 过去20日平均成交额不低于 10M USD |
| 最短历史 | 252 个有效交易日 |
| 调仓频率 | 月度 |

研究终点必须是数据准备时已经完成且可见的交易日。不得保存或使用未完成日线。

### 3.1 第一版非目标

- 分钟级或逐笔动量；
- 盘前、盘后和隔夜盘口建模；
- 新闻、财报文本和分析师预期；
- 借券可得性和历史借券费的精确重建；
- 用当前指数成分股替代历史全市场股票池；
- 在数据建设阶段寻找最优因子参数；
- 使用 Massive 技术指标接口替代内部特征计算。

只做多组合可先完成信号研究。任何多空或市场中性结论必须在后续补充历史借券约束，并将“可做空”
与“存在日线”分开建模。

---

## 4. 核心设计原则

### 4.1 Provider、Venue、Ticker 和 Instrument 分离

- `massive` 是数据 Provider，不是交易 Venue；
- NYSE、Nasdaq、NYSE American 等才是 Venue；
- ticker 是供应商在一段有效期内使用的外部标识；
- `InstrumentId` 是系统内部稳定证券身份；
- 同一证券改 ticker 后仍应保持同一个 `InstrumentId`；
- 被其他公司重新使用的 ticker 不得继承旧证券身份。

Canonical 数据、Universe、Feature 和回测必须以 `instrument_id` 关联。`provider_symbol` 只用于
lineage、审计和供应商请求。

### 4.2 Point-in-time 优先

对于历史决策时点 `t`：

```text
只允许使用 available_time <= t 的记录
只允许选择 effective_from <= t < effective_to 的证券和挂牌
只允许使用在 t 时已经发布的公司行为信息
不得用今天的 active、exchange、shares outstanding 或 ticker 状态回填历史
```

如果供应商只提供最终修订值而无法重建历史可见版本，数据必须标注为 `corrected-final`，不能冒充
`raw-as-received`。日频价格研究可以使用最终修正版做研究，但结论中必须披露该限制。

### 4.3 Source 不可变，Canonical 可重建

每次 Massive 请求必须保存：

- endpoint/resource；
- 完整查询参数；
- 请求开始和完成时间；
- request ID；
- HTTP 状态和分页信息；
- 原始 payload 或 Flat File；
- payload SHA-256；
- API host、connector version 和 entitlement 错误；
- 重试和限流信息。

Source 文件一旦完整写入就不原地修改。Decoder、Schema 或复权规则变化时，从相同 Source 重新发布
新的 Canonical Release。

### 4.4 数据层与研究层分离

Canonical 层保存市场和证券事实；Features 层保存可复用的历史特征；Studies 层才保存未来收益、
标签、样本切分和策略专用排除规则。

例如：

- `close_raw` 属于 Canonical；
- `split_factor` 和 `total_return_index` 属于 Curated；
- `adv20` 和 `momentum_12_1` 属于 Features；
- `forward_return_21d` 只能属于 Studies。

---

## 5. 数据产品规划

### 5.1 Reference 产品

#### `reference.instruments.equity.us.massive`

保存供应商发现的证券及其有效状态：

```text
instrument_id
provider
provider_ticker
provider_composite_figi       # 若套餐和端点提供
provider_share_class_figi     # 若提供
security_type
name
currency
locale
primary_exchange
listing_date
delisting_date
active
effective_from
effective_to
available_time
source_record_id
```

FIGI 等供应商字段只能帮助身份解析，不自动成为内部主键。身份规则必须版本化，并对冲突记录隔离。

#### `reference.symbol_mappings.equity.us.massive`

```text
provider
namespace
external_symbol
instrument_id
effective_from
effective_to
mapping_reason
source_event_id
available_time
```

所有 ticker change 必须先更新映射，再允许后续行情进入正式 Release。无法映射的行情进入 quarantine。

#### `reference.corporate_actions.equity.us.massive`

```text
event_id
instrument_id
action_type
ex_date
execution_date
record_date
pay_date
declaration_date
split_from
split_to
cash_amount
currency
distribution_type
event_time
available_time
source_record_id
```

第一版必须覆盖 split、cash dividend 和 ticker change。merger、spinoff、stock dividend、特殊分派和
退市如果不能可靠自动处理，应明确标记为 unsupported，并在受影响区间隔离或降级质量。

#### `reference.calendar.equity.us`

```text
session_date
venue_scope
opens_at
closes_at
is_trading_day
is_early_close
calendar_version
available_time
```

不得通过某只股票是否存在 K 线推断市场休市。

### 5.2 Canonical 市场产品

#### `market.ohlcv.equity.us.massive.1d.raw`

这是价格和成交模拟的基础事实：

```text
instrument_id
provider_symbol
session_date
period_start
period_end
event_time
available_time
venue
interval
open
high
low
close
volume
vwap
transactions
currency
provider_adjusted
source_record_id
source_revision
```

第一版 `provider_adjusted` 必须为 `false`。主键：

```text
(instrument_id, session_date, interval, source_revision)
```

用于研究的 current view 应根据明确的 revision policy 选出每个主键的最终有效版本，而不是静默覆盖。

#### `market.ohlcv.equity.us.massive.1d.vendor_adjusted`

单独保存 Massive `adjusted=true` 结果，用于供应商对照和复权校验。不得把它与 raw bars 混在同一列
后让消费者猜测语义。

### 5.3 Curated 产品

#### `market.returns.equity.us.1d`

由 raw bars 和 corporate actions 内部构建：

```text
instrument_id
session_date
close_raw
split_factor
close_split_adjusted
cash_dividend
price_return_1d
total_return_1d
total_return_index
adjustment_status
available_time
```

该产品必须记录输入 Raw Release、Corporate Action Release、算法版本和异常处置。供应商 adjusted bars
只用于 reconciliation，不作为内部算法的隐式输入。

#### `market.universe.equity.us.1d`

```text
decision_date
instrument_id
provider_symbol
security_type
primary_exchange
listed
active
eligible_reference
exclusion_reason_reference
listing_age_sessions
available_time
```

Reference Universe 只回答证券在该日是否属于允许研究的证券类别，不掺入策略参数。历史长度、价格和
流动性门槛在 Feature/Study 层应用。

### 5.4 Feature 产品

#### `features.liquidity.equity.us.1d`

```text
instrument_id
decision_date
raw_close
dollar_volume
adv20
adv60
median_dollar_volume_20d
zero_volume_days_20d
observed_sessions_252d
stale_price_days
available_time
```

成交额优先使用 `close_raw * volume`，并同时保留 VWAP 口径用于敏感性检查。所有 rolling 特征只允许
使用决策日当时已经可见的数据。

#### `features.momentum.equity.us.1d`

```text
instrument_id
decision_date
momentum_12_1
momentum_6_1
momentum_3_1
return_21d
volatility_63d
valid_observations
feature_available_time
```

基准 `12-1` 信号定义为：

```text
momentum_12_1(t) = product(1 + total_return[d]) - 1
                   for d in sessions[t-252, t-21]
```

窗口应按交易 session 索引而不是自然日近似。是否允许窗口中的少量缺失必须在 Feature 版本中固定。

---

## 6. Massive 数据获取方案

### 6.1 数据资源映射

首期需要的 Massive 资源类别为：

| 需求 | 资源类别 | 获取策略 |
| --- | --- | --- |
| 全证券发现 | ticker reference | 分页全量抓取 active/inactive 股票 |
| 历史时点详情 | ticker details/reference | 按历史日期或事件补全有效期 |
| ticker 变化 | ticker events | 保存全部 ticker change 事件 |
| 拆股 | splits reference | 按日期窗口增量获取 |
| 分红 | dividends reference | 按 ex-date 窗口增量获取 |
| 日线 | daily aggregates / group daily / Flat Files | 批量资源优先，单 ticker REST 用于补缺和核验 |
| 交易日历 | market status/holidays + internal calendar | 供应商与内部日历交叉验证 |

具体 endpoint、分页上限和套餐权限可能变化，应封装在 Connector 内，不写入研究脚本。开发前通过
diagnostics 命令验证账户的历史深度、inactive ticker 可见性、Flat File 权限和速率限制。

### 6.2 批量优先原则

全市场二十年日线不应默认执行 `ticker × date-range` 的逐标的 REST 请求。优先顺序为：

1. Massive Flat Files 或按日全市场聚合文件；
2. group daily/bulk aggregates；
3. 单 ticker aggregates 仅用于近期补尾、缺口修复和抽样核验。

Connector 的 `estimate()` 必须在下载前给出：

- 文件或请求数量；
- 预计字节数；
- 日期分区数；
- 预计证券数；
- entitlement/cost class；
- 是否超过配置的 acquisition limits。

### 6.3 分区与断点恢复

Raw 建议按供应商数据资产的天然粒度分区：

```text
data/source/provider=massive/dataset=stocks_daily_ohlcv/
  event_year=2024/event_month=01/event_date=2024-01-02/
    payload.csv.gz
    receipt.json
```

Reference 数据按资源和抓取批次保存：

```text
data/source/provider=massive/dataset=tickers/
  as_of=2024-01-02/request=<fingerprint>/
data/source/provider=massive/dataset=splits/
  event_year=2024/request=<fingerprint>/
data/source/provider=massive/dataset=dividends/
  event_year=2024/request=<fingerprint>/
```

下载先写 `.part`，校验完成后原子重命名。已存在且哈希匹配的 Source 分区直接复用；缺 receipt 时允许
从完整 payload 补建 receipt；payload 损坏必须删除临时缓存并重新下载。

只有所有计划分区完成、无失败请求且 reference 可解析时，才允许进入 Canonical 发布阶段。

### 6.4 增量更新

每日更新分为：

```text
T 日收盘后
  -> 等待供应商日线达到稳定延迟阈值
  -> 获取 T 日 raw/vendor-adjusted bars
  -> 获取最近公司行为和 reference 变化
  -> 重算受影响证券的调整序列
  -> 执行质量检查
  -> 发布包含历史完整快照的新 Release
  -> 更新交互式 alias
```

公司行为可能晚到或被修订，因此至少回看最近 30 个自然日。任何历史变化都产生新 Release 和 compare
报告，不覆盖原 Release。

---

## 7. 身份与历史股票池

### 7.1 稳定身份分配

身份解析按证据强弱依次使用：

1. 供应商稳定标识和明确 ticker event；
2. FIGI/share-class 标识与有效期；
3. 公司行为或并购事件中的明确继承关系；
4. 人工审计映射。

名称相同、ticker 相同或价格连续都不能单独证明是同一证券。出现一对多、多对一或 ticker 重用时，
记录冲突并进入 quarantine，禁止猜测。

### 7.2 Reference Universe 资格

证券在日期 `t` 进入基础股票池必须同时满足：

1. `listing_date <= t < delisting_date`，或有等价的有效期证据；
2. `security_type` 属于批准的普通股类型集合；
3. 主要挂牌属于批准的美国交易所；
4. 计价币种为 USD；
5. 在 `t` 时有效的 ticker 映射唯一；
6. 不处于 reference 冲突或未支持公司行为隔离区间。

第一版默认排除：

- ETF、ETN、CEF 和共同基金；
- ADR，除非研究协议后续明确纳入；
- preferred stock；
- warrant、right、unit；
- SPAC unit 和未拆分组合证券；
- OTC 和灰色市场；
- 测试证券和临时代码；
- 无法确定证券类型的记录。

所有类型映射使用白名单，不采用“不是 ETF 就当普通股”的反向推断。

### 7.3 每日研究资格

在基础股票池之上，动量研究的每日 eligibility 为：

```text
eligible = eligible_reference
           and observed_sessions_252d >= required_history
           and raw_close >= price_floor
           and adv20 >= liquidity_floor
           and no_critical_gap
           and adjustment_status == "valid"
```

建议输出：

```text
decision_date
instrument_id
eligible
exclusion_reasons[]
listing_age_sessions
observed_sessions_252d
raw_close
adv20
history_complete
```

保留多个 exclusion reason，便于分析筛选顺序和股票池变化。

### 7.4 退市和停止交易

退市证券不得从历史面板删除。最后一根日线之后需要区分：

- 正常退市；
- 并购现金退出；
- 换股并购；
- 破产或清算；
- 临时停牌；
- 数据源缺失；
- 供应商尚未覆盖。

如果缺少可靠退市回报，第一版必须：

1. 在数据质量报告中量化受影响证券和持仓；
2. 对回测采用明确、保守且可配置的退市处置；
3. 单独报告有无退市处置的结果差异；
4. 不得用最后价格无限前填。

在完整 reference 和退市事件接入前，Feature 构建器至少必须把内部 US securities calendar 认为应交易、
但某证券缺少日线的日期 materialize 成 `price_observation_status = "missing_bar"` 的状态行，并写入
`missing_reason`、`critical_gap` 和 `exclusion_reasons[]`。这类行不得进入 eligible universe，也不得
增加有效历史观测数。若构建时提供 Massive equity identity/reference 目录，`missing_reason` 必须至少
使用 `listing_date` / `delisting_date` 区分 `not_yet_listed` 和 `delisted_after_reference_end`；
其余缺失仍保守标记为 `expected_trading_session_without_bar`。后续还必须继续细分为停牌、下载失败
或供应商覆盖缺口。

无法获得完整退市回报是美股数据库相对于 CRSP 类数据源的重要剩余风险，必须写入研究结论。

---

## 8. 价格调整与收益构造

### 8.1 保存两套供应商价格

同一窗口分别请求：

- `adjusted=false`：Raw 交易价格；
- `adjusted=true`：Massive 调整价格，只用于 reconciliation。

Raw 价格用于：

- 价格门槛；
- 订单数量和成交模拟；
- gap/limit/异常价格检查；
- 公司行为前后核验。

内部总收益序列用于：

- 动量信号；
- 历史持有期收益；
- 横截面排名和组合收益。

### 8.2 拆股

对 ex-date 之前的历史价格应用累计拆股因子，对历史成交量应用反向因子。必须验证：

```text
raw_close(t-1) / raw_open(t)
```

的大幅跳变能由 split ratio 解释。拆股事件缺失或比例冲突时，受影响证券不得晋级 Q3。

### 8.3 现金分红和总收益

现金分红在 ex-date 进入总收益：

```text
total_return(t) = (close_raw(t) * split_alignment + cash_dividend(t))
                  / close_raw(t-1) - 1
```

实际实现必须统一所有价格到同一 share basis，并处理同日拆股与分红的顺序。普通、特殊和返还资本分派
应保留类型；第一版若只支持现金分红，需要明确支持集合并隔离其他分派。

### 8.4 与 Massive 调整价格对账

按证券和日期比较内部 split-adjusted/total-return 序列与 Massive adjusted bars：

- 拆股日前后价格连续性；
- 1日、21日、252日累计收益偏差；
- 现金分红日差异；
- 供应商历史修订。

Massive 调整方法未明确包含现金分红时，不得假定 adjusted close 就是 total-return close。对账阈值和已知
差异必须写入 transform 文档与 quality report。

---

## 9. 时间契约

### 9.1 四类时间

| 字段 | 含义 |
| --- | --- |
| `event_time` | 市场事实发生或 bar 完成时间 |
| `available_time` | 策略最早允许看到该记录的时间 |
| `ingested_at` | 本系统实际获取并归档的时间 |
| `effective_from/to` | reference 或身份事实的有效区间 |

对正常日线：

```text
period_start   = 当日常规交易时段开盘
period_end     = 当日常规交易时段收盘
event_time     = period_end
available_time >= event_time
```

如果使用供应商批量日终文件，应根据真实发布时间或保守可用延迟定义 `available_time`，不能仅因 bar 的
业务区间结束就假设数据立即可见。

### 9.2 信号与成交

月末收盘数据生成的信号不能以同一收盘价成交。基准回测使用下一交易日开盘，另报告：

- 下一交易日 VWAP；
- 下一交易日收盘；
- 开盘价加冲击成本；
- 延迟一日成交。

Universe、流动性和动量必须共享同一决策时间边界。任何输入的 `available_time` 晚于决策时间时，该证券
在该次决策中不可用。

### 9.3 日历规则

- 内部时间使用带时区 UTC；
- session 归属使用 `America/New_York`；
- 所有查询窗口使用 `[start,end)`；
- 正常半日市仍是一条完整日线；
- 周末和市场假日不是缺口；
- 单证券停牌不能被解释成市场休市；
- 日历版本必须进入 lineage。

---

## 10. 存储布局与发布模型

### 10.1 建议物理布局

```text
data/source/provider=massive/...

data/reference/provider=massive/
  instruments/
  symbol_mappings/
  corporate_actions/

data/canonical/market/ohlcv/
  asset_class=equity/region=us/provider=massive/interval=1d/view=raw/
  asset_class=equity/region=us/provider=massive/interval=1d/view=vendor-adjusted/

data/curated/market/returns/
  asset_class=equity/region=us/interval=1d/

data/curated/market/universe/
  asset_class=equity/region=us/frequency=1d/

data/features/equity/
  region=us/feature_set=liquidity-v1/frequency=1d/
  region=us/feature_set=momentum-v1/frequency=1d/

data/studies/us-equity-momentum/<version>/
```

Canonical 和 Feature Parquet 建议按 `event_year` 分区，必要时增加 bucket，而不是为每个 ticker 创建
数万个小目录：

```text
event_year=2024/bucket=017/part-<hash>.parquet
```

bucket 由稳定的 `instrument_id` hash 决定。日常横截面查询也可以按 session year/month 分区；最终布局
应通过 DuckDB/Arrow 的主要查询进行 benchmark 后确定。

### 10.2 Release 元数据

每个正式 Release 必须包含：

```text
manifest.json
schema.json
lineage.json
coverage.json
quality.json
capabilities.json
```

Lineage 至少记录：

- 输入 Source receipt 和 SHA-256；
- 输入 Reference/Corporate Action Release；
- transform name/version；
- schema version；
- calendar version；
- adjustment policy version；
- identity policy version；
- universe policy version；
- request windows 和 `[start,end)`；
- provider、venue scope 和数据 view；
- point-in-time 限制与已知缺口。

### 10.3 不可变发布

Release ID 由规范化后的内容和产品契约共同决定。增量获取时：

1. 读取旧 Release；
2. 按主键合并新分区；
3. 重新执行受影响范围的转换和质量检查；
4. 发布一个包含完整覆盖的新 Release；
5. 保留旧 Release；
6. compare 身份、Schema、Coverage、Quality 和内容变化；
7. 仅在批准后移动 alias。

正式 Study 和 Backtest 记录解析后的 Release ID 与内容 hash，不记录浮动 alias 作为唯一证据。

---

## 11. 数据质量门禁

### 11.1 Q0：归档完成

- 所有计划 Source 文件存在；
- receipt、请求参数和 SHA-256 完整；
- 无未处理下载失败；
- 压缩文件可解压；
- 分页完整且 request ID 可追踪。

### 11.2 Q1：结构与完整性

- Schema 字段和单位正确；
- 主键唯一；
- 时间全部带时区；
- `period_start < period_end <= event_time <= available_time`；
- OHLC 为正且 `low <= open/close <= high`；
- volume、transactions 非负；
- instrument mapping 唯一；
- 未映射记录已隔离；
- Source 行数、Canonical 行数和隔离行数可对账。

### 11.3 Q2：研究质量

- 交易日历覆盖正确；
- 每日股票池可按历史 reference 重建；
- 证券类型白名单生效；
- ticker change 前后身份连续；
- 公司行为覆盖和比例通过抽样验证；
- Raw 与 vendor-adjusted 对账在阈值内；
- 缺失、停牌、未上市和退市状态可区分；
- 所有 rolling feature 满足 point-in-time；
- 无未来标签进入 Feature Release；
- 已知数据限制进入 quality report。

### 11.4 Q3：回测质量

除 Q2 外还必须满足：

- 全研究窗口的历史股票池完成率达到预设阈值；
- 不存在未处置的重大公司行为；
- 退市处置规则已定义并有敏感性报告；
- 决策时间与下一可成交时间经过自动检查；
- 回测所需 raw price、total return、universe 和 liquidity Release 相互一致；
- 随机抽取至少 50 只证券、20 个交易日与供应商原始数据核验；
- 强制覆盖至少 10 个拆股、10 个分红、10 个 ticker change 和若干退市案例；
- 重跑相同输入得到相同行数、内容 hash 和质量报告。

### 11.5 Q4：生产质量

日频研究达到 Q3 不代表可以实盘。Q4 还需要：

- 实时/日终数据延迟监控；
- live 与 historical pipeline 一致性；
- IBKR 或实际执行 Venue 的 symbol/catalog 对账；
- 当前挂牌、交易规则和公司行为监控；
- 订单前行情新鲜度和缺失数据 fail-closed；
- 运行时 reconciliation、告警和回滚。

### 11.6 必须输出的质量诊断

- 每日基础股票池和 eligible 股票数；
- 每日新增上市、退市和 ticker change 数；
- 按证券和年份的日线覆盖率；
- 缺失日线原因分布；
- 公司行为数量和未支持类型；
- Raw/adjusted 收益偏差分布；
- 极端1日收益及解释状态；
- 无成交、零成交量和 stale price 分布；
- quarantine 行数和原因；
- 各质量等级的失败项。

异常记录不能因影响研究结果而删除。修复、隔离和保留都必须带原因码。

---

## 12. Point-in-time Feature 构建

### 12.1 特征计算顺序

```text
Raw bars + Corporate actions
  -> total returns
  -> daily reference universe
  -> trailing history and liquidity
  -> eligible universe at decision time
  -> momentum feature
  -> cross-sectional ranks
```

不能先在完整样本上筛出“拥有完整历史”的股票，再计算早期股票池。每个日期都要独立应用当时可见的
历史长度和流动性条件。

### 12.2 缺失值规则

- 不前填 OHLC 或收益；
- 停牌期间不生成虚假的零收益日；
- rolling window 记录实际观测数；
- 关键形成窗口缺失时，信号为 invalid，而不是零；
- 横截面排名只在当日 eligible 且 signal valid 的股票中计算；
- IPO 冷静期通过历史 session 数控制；
- 公司行为隔离日不得因价格跳变生成动量信号。

### 12.3 横截面标准化

若后续执行 winsorize、z-score、行业中性化或市值中性化：

- 参数只使用当日横截面；
- 行业分类和市值必须是 point-in-time 数据；
- 处理规则和缺失行业策略进入 Feature 版本；
- 未获得可靠历史行业/市值数据前，不把当前分类或当前市值回填历史。

第一版可以只做原始总收益排序，避免引入尚未治理的基本面数据。

---

## 13. 研究与回测的数据绑定

### 13.1 Study 必须冻结的输入

```yaml
study_id: us-equity-cross-sectional-momentum-v1
inputs:
  raw_bars_release: ds_...
  corporate_actions_release: ds_...
  total_returns_release: ds_...
  reference_universe_release: ds_...
  liquidity_features_release: ds_...
  momentum_features_release: ds_...
time:
  decision_time: session_close
  earliest_execution: next_session_open
  boundary: "[start,end)"
universe:
  security_types: [approved_common_stock_types]
  minimum_history_sessions: 252
  minimum_raw_close_usd: 5
  minimum_adv20_usd: 10000000
signal:
  primary: momentum_12_1
rebalance: monthly
```

每个输入同时记录内容 hash、Schema/Transform Version、Quality Level 和数据 view。

### 13.2 前瞻标签边界

未来收益只在 Study 层产生：

```text
forward_return_1m
forward_return_3m
next_open_to_open_return
next_close_to_close_return
```

标签不得注册为 point-in-time Feature，也不能进入 live strategy 输入。

### 13.3 可执行价格

第一版日线只能提供粗粒度执行假设：

- 基准：下一交易日 open；
- 保守：下一交易日 open 加单边冲击；
- 敏感性：下一交易日 VWAP 或 close；
- 容量：成交名义金额不超过 trailing ADV 的给定比例。

不能声称日线 OHLC 能证明开盘集合竞价中的真实可成交规模。进入模拟盘前需要 quote/分钟数据验证成本。

---

## 14. 实施阶段与验收标准

### 阶段 0：权限与样本探测

任务：

- 增加 Massive equity diagnostics 检查；
- 验证 active/inactive tickers、历史详情、splits、dividends、ticker events；
- 验证 raw 和 adjusted day aggregates；
- 验证 Flat File/bulk 访问权限、最早日期、文件大小和限流；
- 选择经历拆股、分红、改名、退市和长期停牌的测试证券。

产物：`readiness.json`、端点能力矩阵和估算报告。

退出条件：确认能够获取研究所需历史，或明确记录套餐缺口和替代方案。

### 阶段 1：历史 Reference 与身份

任务：

- 全量导入股票 reference，包括 inactive；
- 建立稳定 `InstrumentId` 和有效期 symbol mapping；
- 导入公司行为；
- 建立证券类型白名单；
- 建立 quarantine 和人工映射入口。

测试重点：ticker 重用、ticker change、同名不同证券、并购和缺失 listing date。

退出条件：任意样本日期都能唯一解析当时 ticker；冲突不会被静默猜测。

当前进展：已增加 Massive active/inactive 普通股 ticker reference 同步入口，结果会保存为内容寻址的
`reference/provider=massive/equity_tickers/version=<hash>/records.json`，可作为后续
`build-provider-equity-identity --provider massive --reference-rows` 的输入：

```bash
./pyenv/bin/python -m kairos data sync-provider-reference --provider massive --equity-tickers
```

该入口解决“全市场股票清单如何进入本地 Source/Reference”的前置问题，但还没有完成全历史 ticker
事件补齐、冲突隔离审核和 full-market identity Release 验收。

### 阶段 2：全市场 Raw 日线

任务：

- 声明 `DataProductContract`；
- 实现全市场 Massive Connector；
- 批量下载 Raw 与 vendor-adjusted 日线；
- 支持 plan、estimate、缓存、恢复和增量合并；
- 写入 Parquet 和完整 Release 元数据。

退出条件：指定窗口能够发布不可变全市场 Release，重复运行得到相同内容 hash。

### 阶段 3：复权和总收益

任务：

- 实现版本化 adjustment engine；
- 处理 split 和 cash dividend；
- 生成 split-adjusted 和 total-return 序列；
- 与 Massive adjusted 数据对账；
- 对未支持公司行为 fail closed。

退出条件：公司行为样本测试通过，所有显著偏差均可解释或被隔离。

### 阶段 4：动态股票池与流动性

任务：

- 按日 materialize reference universe；
- 计算历史长度、raw price、ADV 和缺失状态；
- 输出 eligibility 与全部 exclusion reason；
- 生成股票池变化诊断。

退出条件：历史日期的股票池不依赖当前 active 列表，退市股票保留在历史样本中。

### 阶段 5：质量门禁和 Q3 晋级

任务：

- 新增 equity OHLCV、reference、corporate action、returns 和 universe 质量 profile；
- 执行结构、时间、身份、coverage、复权和 point-in-time 检查；
- 生成 compare 和 quarantine 报告；
- 完成手工抽样核验。

退出条件：输入 Release 获得 `APPROVED_FOR_BACKTEST / Q3`，否则研究只能停留在探索状态。

### 阶段 6：Momentum Feature 与 Study 工作流

任务：

- 发布 liquidity 和 momentum Feature Release；
- 增加 `study start` 的美股默认产品或专用入口；
- 自动固定所有上游 Release 和 hash；
- 生成研究工作区和最小研究脚本。

目标命令形态：

```bash
./pyenv/bin/python -m kairos data prepare-us-equity-momentum \
  --raw-dataset market.ohlcv.equity.us.massive.1d.raw \
  --connector-config examples/data/massive_connector.example.json \
  --start 2005-01-01T00:00:00-05:00 \
  --end 2026-07-01T00:00:00-04:00 \
  --sync-corporate-actions \
  --dataset-id us-equity-momentum.bounded.v1

./pyenv/bin/python -m kairos study plan us-equity-momentum \
  --dataset market.ohlcv.equity.us.massive.1d.raw \
  --start 2005-01-01T00:00:00-05:00 \
  --end 2026-07-01T00:00:00-04:00

./pyenv/bin/python -m kairos study start us-equity-momentum \
  --dataset market.ohlcv.equity.us.massive.1d.raw \
  --start 2005-01-01T00:00:00-05:00 \
  --end 2026-07-01T00:00:00-04:00
```

实际 CLI 可以在实现时增加 asset-specific 参数，但不能让用户手工拼接物理路径或单独管理一组不一致
的 Release。

`data prepare-us-equity-momentum` 是当前受限版一键入口。它必须串联 raw OHLCV prepare/acquire、
可选 Massive split/dividend 公司行为归档、Feature Release 构建、Study 输入固定和 readiness 报告。
该命令可以直接用于配置好的 Massive equity 产品，但在 active/inactive 全市场 reference、全市场 Source
规划、退市收益和完整 coverage 证据完成前，输出必须标记为 bounded-configured-products，不能声称
full-market backtest ready。

设置 `--sync-corporate-actions` 且未显式传入 `--corporate-actions-directory` 时，命令会从已准备好的
raw release 中读取 bounded ticker 与 `instrument_id`，按相同日期窗口归档 Massive splits/dividends，
合并为一个受限范围的 corporate action 输入目录，并把该目录传给 Feature 构建器。该路径适合配置篮子的
研究使用；在完整 point-in-time identity mapping 建成前，它仍不能替代全市场级别的公司行为证据。

Feature 构建必须消费已归档的公司行为输入，而不是在研究脚本中临时下载或手工修正收益。当前本地
构建入口保留显式参数：

```bash
./pyenv/bin/python -m kairos features build \
  --feature-set us-equity-momentum-v1 \
  --source-directory canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=<raw-release> \
  --corporate-actions-directory reference/provider=massive/corporate_actions/ticker=<TICKER>/version=<hash> \
  --dataset-id us-equity-momentum.<scope>.v1
```

没有 `--corporate-actions-directory` 时，构建器必须在 Release `quality.json` 和 readiness 报告中明确披露：
`total_return` 退化为 raw close return，不能声称已完成独立复权和总收益审计。

`kairos data us-equity-momentum-diagnostics` 必须汇总 universe release 中的缺失状态，包括 `observed_rows`、
`missing_bar_rows`、`critical_gap_rows` 和 `missing_reason_counts`。存在
`expected_trading_session_without_bar` 时仍可作为受限研究输入使用，但 readiness 必须以 warning 暴露，
并提示补充 reference/coverage 证据，将其拆分为停牌、退市、下载失败或供应商覆盖缺口。

退出条件：另一台环境使用相同 Source、配置和代码能生成相同 Study 输入 hash。

### 阶段 7：运行维护

任务：

- 每日增量和历史回看；
- 数据延迟、缺口、修订和股票池规模监控；
- alias 晋级审批；
- 旧 Release 保留和依赖审计；
- Provider schema/entitlement 变化告警。

退出条件：连续运行至少 30 个交易日，无静默缺数、身份漂移或不可解释历史重写。

---

## 15. 测试计划

### 15.1 单元测试

- Massive row decoder 和字段缺失；
- 时区、DST、正常日和半日市；
- ticker change 有效区间；
- ticker 重用不合并；
- split ratio 和累计因子；
- dividend ex-date 总收益；
- 同日 split/dividend；
- rolling window 不读未来；
- `[start,end)` 边界；
- content release ID 确定性。

### 15.2 集成测试

- bounded ticker 烟雾测试；
- 小时间窗口的全市场批量导入；
- Source 缓存与断点恢复；
- base release 增量合并；
- Reference -> Raw -> Returns -> Universe -> Feature 完整链路；
- 质量失败时不发布 approved release；
- Study 固定 Release 后不联网、不重新解析 alias。

### 15.3 Golden cases

固定一组真实证券生命周期案例，至少覆盖：

- 多次拆股；
- 普通和特殊现金分红；
- ticker 变更；
- IPO；
- 退市；
- 并购；
- 长期停牌；
- ticker 被另一证券重用；
- 极端价格跳变但无公司行为；
- 交易日历半日市。

Golden fixture 应保存最小供应商原始响应和预期 canonical/curated 结果，不依赖测试执行时联网。

### 15.4 性能测试

- 20年以上全市场日线的 Parquet 写入时间；
- DuckDB 按日期横截面扫描；
- 单证券全历史扫描；
- 全量 rolling feature 构建峰值内存；
- 增量更新只重算受影响分区；
- Catalog 和 metadata 不随分区数出现不可接受退化。

---

## 16. 运行监控与故障策略

每日任务至少监控：

```text
source_partition_expected / received
source_bytes
request_failures / retries / rate_limits
raw_rows / canonical_rows / quarantined_rows
unmapped_symbols
new_listings / delistings / ticker_changes
corporate_action_revisions
unexplained_extreme_returns
universe_size
eligible_universe_size
release_id / quality_level / publication_status
```

以下情况必须 fail closed，不移动正式 alias：

- 计划分区未完整；
- 出现新的未映射 ticker；
- 主键重复无法确定 revision；
- 大规模股票池异常收缩；
- 公司行为导致无法解释的价格断点；
- 供应商 schema 改变；
- `available_time` 或时区规则失效；
- 质量等级低于产品最低发布要求。

网络失败、限流和套餐权限错误不得被转换成“当天没有行情”。

---

## 17. 已知风险与后续数据

### 17.1 退市收益

Massive 行情和 reference 未必提供 CRSP 式完整退市收益。正式论文级结论应考虑引入独立退市数据源做
交叉验证。未补齐前必须报告这一偏差风险。

### 17.2 历史市值

动量研究常用市值过滤或市值权重。当前 shares outstanding 若不是 point-in-time，不能用于历史市值。
后续应单独建设历史股本/市值产品，并记录财务数据的 filing/available time。

### 17.3 行业分类

当前行业分类会发生变化。未建设 point-in-time 分类前，不能使用今天行业对历史做中性化并声称完全
无前视偏差。

### 17.4 借券与交易成本

日线成交额不能证明小盘股可按模型价格成交，多空策略还受借券可得性和费用约束。策略晋级前至少补充：

- bid/ask 或分钟数据；
- 开盘滑点核验；
- participation rate 容量模型；
- short availability/borrow fee 的保守场景。

### 17.5 供应商修订和历史可见性

回溯下载通常取得供应商最终修订数据，而不一定能重建当日首次发布版本。数据 view、修订政策和潜在
差异必须进入 Release 与研究报告。

---

## 18. 完成定义

美股动量数据层只有同时满足以下条件才算完成：

- [ ] 全市场 Source 可规划、估算、断点恢复和增量更新；
- [ ] active 与 inactive 普通股都进入历史 Reference；
- [ ] ticker change 和 ticker 重用由稳定身份正确处理；
- [ ] Raw 与 vendor-adjusted 日线分开保存；
- [ ] 拆股、分红和总收益可独立重建并审计；
- [ ] 任意历史交易日都能重建当时基础股票池；
- [ ] 价格、历史长度和流动性过滤只使用当时可见数据；
- [ ] 退市、停牌、休市和下载失败不会混为同一种缺失；
- [ ] Dataset Release 不可变并带完整 hash、lineage、coverage 和 quality；
- [ ] Q3 质量门禁覆盖身份、时间、公司行为、股票池和复权；
- [ ] Study 固定所有输入 Release，回测期间不联网、不解析浮动 alias；
- [ ] 重跑得到相同内容 hash 和研究输入；
- [ ] 已知的退市收益、历史市值、行业和借券限制在报告中明确披露。

最终验收问题是：

> 给定历史日期 `t` 和证券 `i`，系统能否证明 `i` 当时是否存在、使用哪个 ticker、属于哪类证券、
> 当时已知哪些价格与公司行为、是否满足股票池条件，以及策略最早能在何时以何种价格假设成交？

只有这个问题能由不可变数据和 lineage 完整回答，动量回测结果才具备研究意义。

---

## 19. 与现有文档的关系

- 通用供应商接入、Canonical Event 和时间治理遵循
  `docs/market_data_provider_integration_plan.md`；
- Dataset Product、Release、Catalog 和研究读取遵循 `docs/research_data_guide.md`；
- 研究流程和分阶段验收参考 `docs/crypto_hourly_momentum_research_plan.md`；
- 本文只定义 Massive 美股横截面动量所需的数据建设，不替代通用架构文档或最终策略研究协议。
