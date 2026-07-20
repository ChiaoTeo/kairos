# 研究数据平台改造 RFC

状态：Draft
适用范围：`kairos.data`、市场数据适配器、数据湖、研究和回测模块
目标读者：研究员、数据工程人员、策略开发人员和平台维护者

## 1. 背景与结论

当前项目已经具备 Parquet、Dataset Catalog、`ResearchDataClient`、point-in-time 事件读取、
Source/Canonical/Features/Studies 分层，以及 Massive、Binance、Deribit 等数据获取能力。
这些能力解决了文件格式和部分统一读取问题，但还没有形成稳定的“研究数据产品”模型。

当前最重要的问题是：

1. logical name、Dataset ID、数据版本、Schema 版本和转换版本仍有混用；
2. 名称中混入数据类型、市场、供应商、频率和版本，但各名称的结构不统一；
3. Catalog 能把名称解析到路径，但还不能完整回答数据含义、质量和适用场景；
4. Event Dataset、MarketReplayDataset 和普通 Canonical Dataset 仍暴露不同读取方式；
5. 研究员仍需记忆 Dataset、字段、view、output 和 acquisition policy 等字符串；
6. Curated 层定位不足，公共清洗和对齐逻辑仍可能在研究中重复实现；
7. 本地数据缺失时，已有下载能力没有形成统一的发现、规划、获取、验证和注册闭环；
8. 多 venue、多 provider 数据的身份、选择、回退、拼接和冲突处理尚无统一契约；
9. 数据质量更多表现为元数据，还没有成为统一的发布准入门禁；
10. 正式研究的数据依赖还需要更自动化地冻结和复现。

本 RFC 的核心目标是：

> 研究员按经济含义请求数据，平台负责发现、选择来源、检查覆盖、按策略获取、验证、发布、查询，并为正式研究冻结不可变版本。

## 2. 目标与非目标

### 2.1 目标

- 建立稳定且无歧义的数据身份模型；
- 为 Python 研究提供可发现、类型化、IDE 友好的接口；
- 为 CLI、JSON、YAML 和跨语言系统保留稳定的字符串协议；
- 统一 Event、Bar、Snapshot、Curated 和 Feature 的研究读取体验；
- 支持本地优先、显式受控、幂等可恢复的数据获取；
- 正确处理同一数据从多个 provider 获取以及同一资产在多个 venue 交易；
- 建立 Source、Canonical、Curated、Feature、Study 五层契约；
- 建立 Schema、coverage、quality、lineage 和发布状态门禁；
- 为主要流程提供文档、示例、Notebook、测试和错误指引；
- 保持现有数据和研究可迁移、可验证、可回退。

### 2.2 非目标

- 不把所有动态标的和合约硬编码为 Python 常量；
- 不允许普通读取在没有明确策略时静默发起昂贵网络请求；
- 不在 Canonical 层把不同 venue 的交易事实混成一个虚构市场；
- 不要求一次性重写所有 Repository 和旧研究；
- 不把对象存储、分布式计算或云服务作为第一阶段前置条件。

## 3. 设计原则

1. **身份与位置分离**：数据是什么，不由它存放在哪里决定。
2. **发布不可变**：补数、修错、Schema 或算法变化均产生新 Release。
3. **经济语义优先**：Logical Dataset 描述稳定经济含义，不描述一次下载任务。
4. **结构化维度优先**：不能依赖拆分字符串恢复 provider、venue、underlying 等语义。
5. **point-in-time 默认安全**：默认使用 `available_time` 和 `[start,end)`。
6. **本地优先、获取受控**：获取前可解释缺口、来源、请求数、数据量、权限和配额。
7. **渐进式复杂度**：普通研究默认使用 Curated/Feature，高级研究才接触事件和 Source。
8. **质量即准入**：目录存在不等于已注册，已注册不等于可用于研究。
9. **类型化使用、字符串互操作**：代码优先对象和枚举，系统边界使用受校验字符串。
10. **来源不被抹去**：任何聚合、回退和拼接均保留逐行来源与完整 lineage。

## 4. 数据身份模型

### 4.1 必须分离的概念

| 概念 | 回答的问题 | 示例 |
|---|---|---|
| Logical Dataset | 这是什么数据产品 | `market.option_quotes.us.spxw` |
| Dataset Release | 这次具体使用哪份不可变内容 | `ds_01K...` |
| Schema Version | 字段契约是什么 | `market.option_quote@2` |
| Transform Version | 使用什么转换算法 | `massive.option_quote@3` |
| Alias | 当前推荐指向哪个 Release | `@research` |
| Physical Location | 数据实际存在哪里 | Parquet 路径或对象存储 URI |

任何含义不清的 `v1` 均不得进入新模型。

### 4.2 Logical Dataset Key

推荐形式：

```text
market.option_quotes.us.spxw
market.option_trades.crypto.btc_usdt
market.ohlcv.crypto.btc_usdt
reference.option_contracts.us
curated.option_chain.us.spxw
curated.consolidated_trades.crypto.btc_usd
feature.volatility_surface.us.spxw
```

Logical Key 不包含日期、hash、临时任务、环境名称和 Release 版本。provider 默认也不进入
Logical Key；venue 是否进入由数据产品语义决定。

Catalog 必须另外保存结构化维度：

```yaml
logical_key: market.option_quotes.us.spxw
domain: market
data_type: option_quote
asset_class: option
region: us
underlying: SPX
contract_family: SPXW
frequency: event
venue_scope: consolidated
currency: USD
calendar: XNYS
primary_time: available_time
```

### 4.3 Dataset Release

```yaml
release_id: ds_01K0SPXW...
logical_key: market.option_quotes.us.spxw
release_version: 2026.07.16.1
schema_id: market.option_quote
schema_version: 2
transform_id: massive.option_quote.canonicalizer
transform_version: 3
content_hash: sha256:...
provider: massive
venue_scope: opra
status: approved_for_research
```

Release 发布后不可覆盖。补充时间范围、纠正数据、改变输入来源或转换算法都创建新 Release。

### 4.4 Alias

标准 Alias 为 `@latest`、`@latest-validated`、`@research` 和 `@production`。

- 探索可以使用 Alias；
- 正式研究执行时必须解析并冻结 Release ID；
- 回测、训练、验证和生产产物不得只保存浮动 Alias；
- Alias promotion 必须记录操作者、时间、原因、前后 Release 和质量报告。

## 5. Provider、Venue 与多来源数据

### 5.1 概念区分

- **provider**：向本系统交付数据的机构或接口，例如 Massive；
- **venue**：交易实际发生的市场，例如 Binance、Deribit 或 OPRA；
- **instrument**：被交易的经济合约；
- **data product**：平台向研究员提供的稳定数据语义；
- **source release**：某 provider 对某 venue/范围提供的一次不可变发布。

Binance 和 Deribit 通常既表现为交易 venue，也直接提供自身数据。即使两边都有 BTC 交易，
它们也不是可以互相填补缺口的同一事实：价格、流动性、合约规格、计价币、交易规则和交易时间
都可能不同。

### 5.2 多来源的三种情况

#### A. 同一 venue、同一事实、多个 provider

例如同一 OPRA 期权报价分别由两个供应商提供。这些 Release 可以是候选替代来源，但不能静默
逐行混合。选择需要基于 entitlement、覆盖、延迟、质量、成本和优先级，并记录选择结果。

#### B. 同一经济资产、不同 venue

例如 Binance BTCUSDT 与 Deribit BTC-PERP。它们属于不同 venue 的独立 Canonical Dataset。
一个 venue 缺失时不能用另一个 venue 的数据冒充补洞。

推荐身份：

```text
market.trades.crypto.btc_usdt + venue=binance
market.trades.crypto.btc_perpetual + venue=deribit
```

#### C. 明确定义的跨 venue 数据产品

如果研究需要“BTC 综合市场价格”或“跨 venue 成交”，应在 Curated 层创建独立产品：

```text
curated.consolidated_trades.crypto.btc_usd
curated.reference_price.crypto.btc_usd
```

该产品必须声明：

- 纳入哪些 venue 和 instrument；
- quote currency 如何归一；
- spot、future、perpetual 是否允许混合；
- 时间对齐和迟到数据规则；
- venue 权重；
- 异常值和停牌处理；
- 某 venue 缺失时的降级行为；
- 每行或每个聚合窗口的来源贡献。

### 5.3 Source Selection Policy

Catalog 为一个 Logical Dataset 维护候选 Source Binding：

```yaml
logical_key: market.option_quotes.us.spxw
sources:
  - provider: massive
    venue_scope: opra
    priority: 100
    quality_level: Q3
    estimated_cost: metered
  - provider: secondary_vendor
    venue_scope: opra
    priority: 80
    quality_level: Q2
```

选择过程必须可解释，并依次考虑：

1. 请求的 venue/instrument 语义是否完全匹配；
2. point-in-time 和字段能力是否满足；
3. 请求时间和 universe 覆盖是否满足；
4. Release 质量等级和新鲜度；
5. entitlement、凭证、配额和下载窗口；
6. 成本、速度和团队配置的优先级。

支持显式选择：

```python
data.get(product, source=Sources.MASSIVE)
data.get(product, venue=Venues.BINANCE)
```

不显式选择时使用版本化的 policy，并把最终选择写入查询和 Study Snapshot。

### 5.4 禁止的隐式行为

- 不允许 Deribit 数据静默填补 Binance 的时间缺口；
- 不允许将 spot、future、perpetual 仅因 underlying 相同而直接拼接；
- 不允许同一查询前半段来自 A provider、后半段来自 B provider 而不披露；
- 不允许用更低质量来源替换 approved source 而不产生告警和 lineage；
- 不允许 provider 切换后继续复用原 Release ID；
- 不允许去掉 `provider_id`、`venue_id`、`source_release_id` 等来源字段后发布聚合数据。

### 5.5 允许的组合方式

1. **选择**：一个请求选择一个完全满足条件的 Source Release；
2. **分段拼接**：仅在同 venue、Schema 兼容且 policy 明确允许时使用，必须记录时间段来源；
3. **交叉校验**：主来源用于研究，第二来源用于质量对账；
4. **显式回退**：主来源不可用时使用备选，并在结果和 Snapshot 中标记 degraded；
5. **Curated 聚合**：不同 venue 通过有版本的聚合算法形成新数据产品。

## 6. 数据分层契约

### L0 Source

保存不可修改的供应商响应、请求参数、receipt、状态、下载时间和 hash。只用于审计、重放和
connector 开发，普通研究不得直接依赖。

### L1 Canonical

保存 reference、trade、quote、OHLCV、order book、corporate action 和 vendor analytics 等统一
事实。必须保留 provider、venue、原始 instrument ID、事件时间、可用时间和来源 lineage。

### L2 Curated

提供定频 NBBO、期权链快照、复权行情、连续期货、标准化合约面板、交易日历对齐和跨 venue
聚合等可复用研究产品。公共清洗规则属于本层，策略专属筛选不属于本层。

### L3 Features

提供 point-in-time safe 的 IV、RV、skew、term structure、liquidity 和 order imbalance 等公共
特征。必须声明输入 Release、lookback、warm-up、availability lag、null policy 和算法版本。

### L4 Study

保存 label、future return、样本切分、策略参数、模型、回测结果、报告以及冻结的数据依赖。
Study 可以引用 L1-L3，不能反向污染公共数据层。

## 7. 类型化、可发现的用户接口

### 7.1 原则

不应让研究员记忆所有字符串，但也不能把开放世界的所有 Dataset、ticker 和合约静态写死。
公共 API 接受 `DataProductDefinition | DatasetKey | str`，文档优先展示 `DataProductDefinition`。

```python
quotes = data.get(
    Datasets.MARKET_OPTION_QUOTES_US_SPXW,
    view=DataView.RAW_AS_RECEIVED,
    acquire=AcquirePolicy.IF_MISSING,
    fields=OptionQuoteFields.TOP_OF_BOOK,
)
```

### 7.2 应类型化的有限集合

- `AcquirePolicy`；
- `DataView`；
- `OutputFormat`；
- `DatasetLayer`；
- `DatasetStatus`；
- `QualityLevel`；
- `AssetClass`；
- `MarketDataType`；
- `Frequency`；
- `TimeSemantics`；
- `ProviderId` 和 `VenueId`；
- Schema 绑定的 Field 引用和常用字段组。

### 7.3 不应静态枚举的开放集合

- 任意 ticker 和期权合约；
- 动态新增的 Dataset Product；
- Release ID 和内容 hash；
- 用户创建的 Feature；
- 时间范围和 SQL view name。

这些对象通过 Catalog 搜索、构造器或受校验字符串创建。

### 7.4 Dataset Product 对象

Dataset Product 不只是字符串常量，应能提供：

```python
product.describe()
product.schema()
product.sources()
product.coverage()
product.quality()
product.plan(start, end)
product.query(start, end)
```

类型化目录和字段引用应从 Catalog/Schema 自动生成，CI 检查生成结果是否同步。字符串仍作为
CLI、JSON、YAML 和跨语言稳定协议，并在系统边界立即解析和校验。

## 8. 统一研究查询体验

```python
query = data.get(
    Datasets.MARKET_OPTION_QUOTES_US_SPXW,
    start=start,
    end=end,
    instruments=instruments,
    fields=OptionQuoteFields.TOP_OF_BOOK,
    view=DataView.RAW_AS_RECEIVED,
    acquire=AcquirePolicy.NEVER,
)

frame = query.collect(OutputFormat.POLARS)
```

`DataQuery` 应为惰性对象，支持：

- `collect(arrow|polars|pandas|rows)`；
- `stream()`；
- `sql()`；
- `explain()`；
- Parquet 列裁剪、谓词下推和分区裁剪；
- 有界内存；
- Event、Bar、Snapshot、Curated 和 Feature 的一致入口。

每个 Dataset 必须显式声明 primary time，读取层不得继续通过字段名称顺序猜测时间语义。

## 9. 本地缺失时的数据获取

### 9.1 Acquisition Policy

| 策略 | 行为 |
|---|---|
| `NEVER` | 只使用本地数据，缺失时报错 |
| `PLAN` | 不联网，返回缺失范围和获取计划 |
| `IF_MISSING` | 只获取本地缺失范围 |
| `REFRESH` | 检查远端新数据或修订并创建新 Release |

禁止在没有明确策略时静默联网。正式批处理建议先 `PLAN` 再审批执行。

### 9.2 Coverage Planner

Planner 输入 Logical Dataset、时间、universe、字段、frequency、view、venue/source 偏好；输出：

- 本地可用 Release；
- 已覆盖和缺失的时间、instrument、字段；
- 候选 provider 和 venue；
- 预计请求数、字节数、费用和耗时级别；
- 凭证、entitlement、配额和下载时间窗状态；
- 计划生成的新 Source、Canonical/Curated Release；
- 无法满足请求的具体原因。

### 9.3 获取闭环

```text
Research Request
→ Resolve Product
→ Select Source Policy
→ Inspect Local Coverage
→ Plan Missing Partitions
→ Check Credentials/Quota/Cost
→ Fetch Source
→ Canonicalize
→ Validate
→ Publish Immutable Release
→ Register Catalog
→ Execute Query
```

现有 Massive、Binance、Deribit 下载代码应包装为统一 Provider Connector，而不是重写其正确的
Source 归档逻辑。

### 9.4 安全和幂等要求

- 支持 dry-run、取消、断点续传和请求 fingerprint 去重；
- 设置最大请求数、最大下载量和成本限制；
- 凭证不进入日志或 lineage；
- Source 不可覆盖；
- 失败和不完整数据进入 quarantine；
- 未完成或质量失败的数据不得发布为 approved；
- 并发任务不得重复下载同一分区；
- 第二次相同请求不得重复获取已验证内容。

推荐同时提供显式 API 和 CLI：

```python
plan = data.plan(product, start=start, end=end)
result = data.acquire(plan)
```

```bash
kairos data plan --dataset market.option_quotes.us.spxw --start 2025-01-01 --end 2025-02-01
kairos data acquire --plan PLAN_ID
```

## 10. Catalog V3 与发布治理

Catalog 应拆分以下实体：

- `DataProductDefinition`；
- `DatasetRelease`；
- `SchemaContract`；
- `TransformDefinition`；
- `ProviderCapability`；
- `SourceBinding`；
- `QualityReport`；
- `AliasBinding`；
- `LineageGraph`。

状态机：

```text
draft → registered → validating → validated
      → approved_for_research → approved_for_backtest → approved_for_production
      → deprecated | quarantined | failed
```

Catalog 必须能够回答数据含义、字段、覆盖、质量、适用场景、owner、更新方式、候选来源和已知限制，
而不仅是名称到路径的映射。目录 discovery 仅作为迁移工具；长期不得把未注册物理目录自动视为
受管数据。

## 11. Schema、质量与准入门禁

每个 Release 必须具备等价的受管元数据：

```text
release.json
schema.json
lineage.json
coverage.json
quality.json
manifest.json
usage.json
```

Schema 至少定义字段、Arrow 类型、可空性、单位、货币、枚举、主键、时间字段、去重规则和兼容
策略。通用质量门禁包括 Schema、hash、主键重复、null、coverage、分区缺失、时区和行数异常；
市场专项门禁包括 crossed/stale quotes、OHLC 关系、负 volume、合约有效性、instrument mapping、
corporate action 及 `available_time` 合法性。

质量等级：

| 等级 | 含义 |
|---|---|
| Q0 | 仅存档，未验证 |
| Q1 | Schema 与完整性通过 |
| Q2 | 适合探索性研究 |
| Q3 | 适合正式回测 |
| Q4 | 适合生产决策 |

## 12. Study Snapshot 与可复现

正式研究运行自动保存：

```yaml
inputs:
  - logical_key: market.option_quotes.us.spxw
    release_id: ds_...
    content_hash: sha256:...
    provider: massive
    venue_scope: opra
    source_policy_version: 1
    schema_version: 2
    transform_version: 3
    view: raw-as-received
    requested_window: {start: ..., end: ...}
    actual_coverage: ...
    quality_level: Q3
code: {git_commit: ...}
environment: {dependency_lock_hash: ...}
```

配置可以写 Logical Key 或 Alias，但执行产物必须冻结 Release。升级数据必须显式产生新的 Snapshot，
并能比较输入来源、覆盖、质量和内容差异。

## 13. 必须提供的用户用例

1. 搜索 SPXW 期权报价并查看字段、覆盖、质量和候选来源；
2. 使用类型化 Dataset、View、Field 和 Output 读取本地数据；
3. 查看本地部分缺失的 acquisition plan，不联网；
4. 明确授权后仅获取缺失分区，第二次调用不重复下载；
5. 缺少凭证时给出缺失范围、所需配置和可执行命令；
6. provider 无权限、超配额或不在下载窗口时安全失败；
7. 在同 venue 的多个 provider 中显式选择、自动选择和交叉校验；
8. 分别读取 Binance 与 Deribit 数据，不允许互相隐式补洞；
9. 构建带完整 lineage 的 BTC 跨 venue Curated 产品；
10. 使用 `raw-as-received` 进行事件级 point-in-time 回放；
11. 使用 Curated 期权链，验证 stale/crossed 和合约选择规则；
12. 构建 Feature 并验证 lookback、warm-up 和 availability lag；
13. 使用 Alias 探索并在正式 Study 中自动冻结 Release；
14. 修复错误数据时发布新 Release，老研究仍可复现；
15. 比较两个 Release 的来源、Schema、覆盖、质量和内容差异；
16. 动态新增 Dataset，不要求修改核心常量源码。

每个用例需要同时提供 Python 示例、CLI 示例、预期元数据、常见错误和测试。

## 14. 测试策略

### 14.1 单元测试

覆盖 Key、Release 不可变性、Alias promotion、Schema compatibility、coverage 差集、source
selection、acquisition policy、状态机、分区裁剪和 point-in-time 过滤。

### 14.2 Provider Contract Tests

Massive、Binance、Deribit 使用同一套契约测试：计划、Source 归档、幂等重试、lineage、凭证脱敏、
错误标准化和不完整数据隔离。

### 14.3 集成测试

使用本地 fake provider 完成：

```text
请求 → 缺口 → 计划 → 下载 → Source → Canonical → 验证 → 发布 → 查询
```

真实网络测试与普通 CI 分离。

### 14.4 Golden Dataset

覆盖正常、重复、crossed、乱序、迟到、修订、coverage gap、Schema evolution、多 provider 冲突和
跨 venue 时间对齐。

### 14.5 性能测试

- 单日查询不扫描全量；
- 少数字段不读取全部列；
- 大型事件支持流式读取；
- 峰值内存有界；
- 重复请求不重复下载；
- Catalog 搜索不遍历整个数据湖。

## 15. 文档和示例交付物

本 RFC 落地时应拆出身份、分层、时间、质量、获取、研究流程、Provider 开发和迁移指南，并提供：

```text
examples/data/discover_datasets.py
examples/data/load_local_dataset.py
examples/data/plan_missing_data.py
examples/data/acquire_missing_data.py
examples/data/select_multiple_sources.py
examples/data/build_cross_venue_product.py
examples/data/point_in_time_replay.py
examples/data/freeze_study_snapshot.py
examples/data/compare_releases.py
```

Notebook 至少覆盖数据发现、质量检查、期权链探索、Feature 研究和可复现 Study。

## 16. 实施阶段

### 阶段 0：冻结 RFC

确认身份、命名词典、分层、时间、多来源、获取和研究 API 契约；完成现有 Dataset 映射表。

### 阶段 1：Catalog V3

引入 Product/Release、结构化维度、独立 Schema/Transform 版本、状态机、来源绑定和 Alias 审计；
兼容读取 Catalog V2，并把旧 Dataset ID 迁移为 旧版 alias。

### 阶段 2：类型化与统一查询

提供 Dataset Product、Enum、Field 对象和自动生成目录；实现惰性 `DataQuery`、统一时间契约、列
裁剪、谓词下推、分区裁剪和一致的 collect/stream/sql/explain。

### 阶段 3：Coverage 与 Acquisition

实现 Coverage Planner、Source Selection Policy、Provider Capability Registry、任务状态、限额、
幂等和恢复；先接入 Massive，再接入 Binance 和 Deribit。

### 阶段 4：Curated 数据产品

优先交付 SPXW 定频期权链、underlying 对齐、标准 quote/trade 视图、BTC 标准面板和一个明确
定义的跨 venue BTC 产品。

### 阶段 5：质量门禁与 Snapshot

统一质量框架、专项门禁、质量等级、Release promotion、Study Snapshot 和 Release diff。

### 阶段 6：迁移和清理

逐个迁移现有 research 模块；Repository 降级为内部接口；废弃物理目录 fallback；验证后删除
兼容 CSV；合并重复 MarketReplayDataset 抽象。

## 17. 迁移策略

采用双轨迁移而非一次性重写：

```text
Catalog V2          → 只读兼容
旧 Dataset ID       → 旧版 alias
旧 Repository       → 内部保留并 deprecated
旧研究脚本          → 逐个迁移和对照验证
兼容 CSV            → 所有消费者验证后删除
新数据发布          → 只进入 Catalog V3
```

迁移不得覆盖现有 Release，也不得改变旧研究的解析结果。

## 18. 验收标准

### 身份与治理

- 每个 Product 有稳定 Logical Key 和结构化维度；
- 每次正式发布有不可变 Release ID；
- Release、Schema、Transform 版本完全分离；
- 正式研究不保存浮动 Alias；
- 多 provider/venue 选择和组合均有版本化 policy 与 lineage。

### 用户体验

- 研究员只需要一个 Research Data API；
- 文档默认使用可自动补全的 Dataset、Enum 和 Field 对象；
- 用户可搜索而无需背诵 Key；
- Event、Curated 和 Feature 使用体验一致；
- 错误消息包含候选名称、缺失覆盖和下一步命令。

### 自动获取

- 本地缺失能准确生成 plan；
- `IF_MISSING` 只获取缺失范围；
- 支持 dry-run、配额、成本、凭证和下载窗口保护；
- 获取过程幂等、可恢复、可审计；
- 不完整或质量失败数据不会进入 approved 状态。

### 多来源正确性

- Binance 和 Deribit 的独立市场事实不会互相隐式补洞；
- 同 venue 多 provider 可选择、回退和交叉验证；
- 跨 venue 数据只通过明确定义的 Curated Product 提供；
- 查询结果和 Study Snapshot 可追溯到每个实际 Source Release。

### 性能与复现

- 过滤下推到扫描层，大型数据支持流式处理；
- Study 自动记录 Release、hash、source policy、view、范围、代码和环境；
- 数据修复产生新 Release，旧结果可继续复现。

## 19. 回测、模拟与实盘兼容性

### 19.1 结论

本设计可以同时服务研究、回测、模拟和实盘，但兼容的应当是**数据语义与事件契约**，而不是强行
让四种场景共用同一个读取执行器。

应共享：

- `InstrumentId`、provider、venue 和合约定义；
- Canonical Schema 与 `MarketEventEnvelope`；
- `event_time`、`receive_time`、`available_time`、`ingested_at` 的定义；
- Feature Definition 和状态更新算法；
- 数据质量、序号缺口、stale 和 correction 语义；
- 策略接收的 `MarketView`/`MarketSnapshot` 契约；
- 订单、风控、组合和估值领域模型。

不应共享同一个具体实现：

- 研究使用惰性批量查询；
- 回测使用不可变 Release、确定性排序和虚拟时钟；
- 模拟使用实时或历史事件源、模拟成交和隔离账户；
- 实盘使用 WebSocket/stream、durable journal、重连、backfill 和真实执行通道。

目标结构为：

```text
                          ┌─ HistoricalReplaySource ─ 回测
Canonical Event Contract ├─ PaperStreamSource      ─ 模拟
                          ├─ LiveStreamSource       ─ 实盘
                          └─ BatchQuerySource       ─ 研究

                         ↓
                Shared MarketView Builder
                         ↓
                  Strategy / Risk / Valuation
```

### 19.2 回测兼容要求

回测必须：

- 冻结 Dataset Release、内容 hash、source policy 和 transform version；
- 只按 `available_time` 推进虚拟时钟；
- 使用稳定排序键，例如 `(available_time, source_order, source_namespace, source_instrument_id)`；
- 对相同输入、配置和代码产生相同事件顺序、ID 和结果；
- 显式模拟网络延迟、订单延迟、撮合、滑点、手续费、拒单和部分成交；
- 禁止回测过程中使用 `@latest` 或按需下载新数据；
- 把 correction、late arrival 和 gap policy 固定到 Run Snapshot；
- 对 warm-up 数据与可交易窗口进行区分。

现有 `BacktestClock`、确定性 ID 和 Dataset hash 是正确基础，但 MarketReplayDataset 的生成必须继续
收敛到统一 Canonical/Curated Release 和时间契约。

### 19.3 模拟交易兼容要求

模拟交易分为两种模式：

1. **Historical simulation**：与回测共享 Replay Connector，但使用更接近实盘的异步调度和执行模型；
2. **Live paper trading**：消费与实盘相同的实时市场流，但订单发送到模拟执行 connector。

Live paper trading 的策略、MarketView Builder、风控和监控应尽可能与实盘一致；账户、成交、清算
和权限必须显式标记为 simulated，防止结果或状态混入真实账本。

### 19.4 实盘兼容要求

实盘不得依赖研究查询客户端的批量 `collect()` 路径。实时链路必须具备：

- append-only raw journal，先持久化或有明确 durability policy 后再分发；
- provider/venue sequence 检查；
- 心跳、断连、重连和 backfill；
- watermark 与迟到事件策略；
- bounded queue、backpressure 和 overload policy；
- 去重键和幂等消费；
- clock skew、延迟和 stale 数据监控；
- 数据质量降级时的 risk gate、暂停和 kill switch；
- 明确的订阅生命周期和 universe 变更事件；
- 实时事件最终沉淀为新的受管 Source/Canonical Release，供事后复现。

现有 Massive live journal、sequence-gap 和 reconnect-backfill hook 可以成为 Live Connector 的起点，
但还需要 durable offset、背压、watermark、运行状态和失败恢复契约。

### 19.5 Feature 的 offline/online 一致性

同一个 Feature Definition 应尽量共享纯计算核心，但运行载体不同：

- offline builder 对不可变 Release 批量计算；
- replay feature engine 按虚拟时钟增量更新；
- online feature engine 按实时事件增量更新并持久化 checkpoint。

必须提供 offline/online parity test：相同有序事件输入下，批量结果与增量结果在允许误差内一致。
Feature 必须显式声明状态、lookback、warm-up、迟到事件处理和重启恢复方式。

### 19.6 统一运行模式

建议使用类型化运行模式，而不是字符串和隐式环境判断：

```text
RunMode.RESEARCH
RunMode.BACKTEST
RunMode.HISTORICAL_SIMULATION
RunMode.PAPER_TRADING
RunMode.LIVE
```

运行模式决定允许的数据来源、Alias、acquisition policy、时钟、执行 connector、账户类型和质量门槛。
例如 `BACKTEST` 强制不可变 Release 和 `AcquirePolicy.NEVER`，`LIVE` 强制实时 source、真实账户
审批和 Q4 门槛。策略代码不得通过读取环境变量自行改变这些语义。

## 20. 容易形成的设计陷阱与禁止项

这里的主要风险不是代码技巧不足，而是“看起来统一”的抽象掩盖了不同运行环境的真实差异。

### 20.1 伪统一 Feed

陷阱：让批量 DataFrame、历史迭代器和实时 WebSocket 实现完全相同的同步接口。

后果：实时背压、断连、gap、迟到事件和取消语义被隐藏，回测也无法保持严格确定性。

规定：共享事件和 MarketView 契约，分别实现 batch、replay 和 stream connector。

### 20.2 `available_time` 被过度简化

陷阱：把 `event_time`、provider timestamp 或文件下载时间直接当作 `available_time`。

后果：回测未来数据泄漏，尤其是日线、修订数据、vendor analytics 和 corporate action。

规定：四种时间分别保存；`available_time` 的推导方法属于版本化 transform，并接受质量审计。

### 20.3 回测中隐式获取或使用 Alias

陷阱：本地缺数据时自动下载，或者每次运行解析 `@latest`。

后果：相同代码和参数产生不同结果。

规定：回测只接受冻结 Release；获取发生在独立准备阶段。

### 20.4 不同 venue 静默补洞

陷阱：Binance 缺一天数据就用 Deribit 替代。

后果：研究对象改变，却仍使用原数据名称和结果解释。

规定：不同 venue 只能在显式 Curated Product 中聚合；降级必须改变状态并记录来源贡献。

### 20.5 “统一 Schema”退化为最小公分母

陷阱：为了兼容所有 provider，只保留极少数字段或大量无类型 `payload_json`。

后果：供应商独有但重要的信息丢失，研究再次依赖原始 payload。

规定：Canonical core fields + provider extension fields；扩展字段有命名空间、类型和 Schema 契约。

### 20.6 把静态常量当成 Catalog

陷阱：每新增 Dataset、ticker 或合约都修改 Python 常量源码。

后果：扩展必须发布代码，动态数据无法发现，生成文件产生巨大维护成本。

规定：常用 Product 可以生成类型化入口，Catalog 始终是事实来源，开放集合通过查询和构造器使用。

### 20.7 Source fallback 掩盖数据降级

陷阱：主 provider 失败后自动选择低质量来源，用户无感知。

后果：回测和实盘行为突然改变，结果不可比较。

规定：fallback policy 必须版本化；降级结果带 `degraded` 状态，实盘是否继续由 risk policy 决定。

### 20.8 把 Catalog 放进实时热路径

陷阱：每条实时事件同步查询磁盘 Catalog、Schema 或远端元数据。

后果：延迟抖动、单点故障和吞吐下降。

规定：启动时加载并验证版本化快照，运行时使用内存索引；变更通过受控 control event 生效。

### 20.9 Exactly-once 幻觉

陷阱：假设网络、journal、backfill 和消费者能够天然 exactly-once。

后果：重连后重复成交、重复特征更新或状态不一致。

规定：采用 at-least-once + 稳定事件 ID + 幂等消费者 + checkpoint/reconciliation。

### 20.10 Instrument 身份随 ticker 漂移

陷阱：用供应商 ticker 作为长期主键。

后果：ticker change、合约复用、公司行动和不同 provider 映射导致历史串线。

规定：内部 `InstrumentId` 稳定；外部 symbol mapping 必须带 provider namespace 和有效时间区间。

### 20.11 Offline/online Feature 偏移

陷阱：研究用 Pandas 全表计算，实盘另写一套增量算法。

后果：研究信号无法在实盘重现。

规定：共享 Feature Definition 和核心状态转移，并强制 parity、重启恢复和迟到事件测试。

### 20.12 把查询便利性泄漏到策略层

陷阱：策略在 `on_market` 中临时调用 `data.get()`、SQL 或网络获取。

后果：不可控延迟、未来泄漏、不可复现和实盘阻塞。

规定：策略只消费上下文中已准备的 MarketView、Feature 和 Reference Snapshot；数据依赖在运行前声明。

### 20.13 过度设计风险

Catalog V3、类型生成、多来源 policy 和运行时 connector 不应一次性构建成通用分布式平台。每一阶段必须
由现有用例驱动，优先覆盖 SPXW、BTC、Massive、Binance、Deribit，并保持接口可替换。

以下信号说明设计过度：

- 需要大量框架代码才能读取一个本地 Dataset；
- 新增一个 provider 必须修改多个核心分支而不是实现契约；
- Product/Release/Schema 对象在运行中频繁互相转换；
- 为尚不存在的资产类别预建大量抽象；
- 类型化 API 无法从 Catalog 自动生成或动态查询；
- 简单研究的推荐示例超过必要的业务参数。

验收时必须保留一条“最短路径”：本地已有数据时，研究员能用一个 Product、时间范围和输出格式
完成查询；高级 policy 只在多来源、获取和正式发布时出现。

## 21. 推荐实施顺序

```text
Dataset Identity 与命名词典
→ Layer 与多来源契约
→ Catalog V3
→ 类型化 Dataset/Field API
→ 惰性统一查询
→ Coverage Planner
→ Massive/Binance/Deribit Acquisition
→ Curated 数据产品
→ Quality Gate 与 Study Snapshot
→ 旧研究迁移和旧入口清理
```

自动获取必须排在身份和来源模型稳定之后。否则获取越方便，越容易产生大量含义不清、来源混杂、
无法复现的数据。

## 22. 实现映射与验证

截至 2026-07-17，本 RFC 的数据平台范围已按下表落地。第 19 节描述的是共享契约和兼容边界；它
不表示本项目已经提供完整的实盘 durable stream 基础设施。实盘仍必须满足该节列出的 offset、
backpressure、watermark 和恢复门禁，不能用研究读取器冒充实盘总线。

| RFC 范围 | 实现 | 主要验证 |
|---|---|---|
| 4、10 身份与 Catalog V3 | `kairos/data/contracts.py`、`catalog.py`、`publishing.py` | V1/V2 迁移、V3 round-trip、Release 不可变、promotion audit |
| 5 多来源与 policy | Product `sources`、`source_policy_version`、provider/venue 过滤 | 优先级选择、错误 venue 拒绝、Snapshot 冻结实际来源与 policy |
| 6 分层 | `source/`、Canonical Connector、Curated Builder、Feature Builder、Study artifacts | 八份 Release metadata、Feature input hash、Study audit |
| 7 类型化接口 | `Datasets`、`DataProductDefinition`、Enum、FieldRef | Product 常量与受管 Product 使用同一对象，不要求背 key 字符串 |
| 8 统一查询 | `ResearchDataClient.get/stream/sql/replay/replay_snapshots` | Arrow/Polars/Pandas/rows、字段校验、半开时间范围 |
| 9 获取闭环 | Coverage Planner、Provider Registry、Connector、limits | 缺口差集、plan 只读、配额前置、增量合并、幂等发布 |
| 11 质量门禁 | quality level、status state machine、metadata audit | Q2/Q3/Q4 RunMode 门禁与 promotion gate |
| 12 可复现 | `StudyInputSnapshot`、冻结 Release/hash/source policy | 正式 BTC Study snapshot、Alias 变化后旧查询不漂移 |
| 13 用户用例 | `examples/data/` 与 `research_data_guide.md` | 发现、读取、plan、acquire、多来源、跨 venue、replay、freeze、compare |
| 14 测试 | Connector contract、golden domain tests、性能契约 | Massive/Binance/Deribit、分区/列裁剪、有界 batch、确定性 replay |
| 17 迁移 | V1/V2 loader、metadata upgrade、Parquet migration | 不覆盖 Source，CSV 删除前校验行数，旧 key 仅作 Alias |
| 19 运行模式 | `RunMode`、Replay Feed、Feature offline/online parity | 回测禁获取、冻结 hash、事件确定排序、Feature parity |
| 20 禁止项 | 无 Catalog BUILTINS、无 BTC Pipeline、无 Repository 研究入口 | 静态搜索和完整单元测试 |

标准验收命令：

```bash
python3 -m unittest discover -s tests -v
python3 -m kairos data catalog
python3 -m kairos data doctor
python3 -m compileall -q kairos examples/data tests studies
```

外部网络、IBKR 和 Binance testnet 测试继续与普通离线测试隔离。没有相应 entitlement 或凭证时，
离线测试通过不代表外部服务可用。
