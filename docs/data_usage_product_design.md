# 用户入口、项目模型与扩展 API 设计

状态：Draft  
适用范围：Data Product、Study Product、Strategy Product、Run Product、用户区扩展、研究到策略的一致性  
目标读者：研究用户、策略开发者、数据工程维护者

本文描述目标态产品设计。核心四产品 CLI 已开始按本文落地一条最小纵切；除非小节明确写出“当前已实现入口”，
示例 CLI 和 Python API 仍是目标态 API，用于指导后续收敛。当前仓库仍保留
`kairos run backtest/simulate/shadow/paper --strategy ...`、`kairos study create/start/freeze`、
`kairos factor register-sma` 等过渡入口。

## 1. 设计目标

这份文档把原来的“数据使用产品化设计”提升为更高维度的用户入口设计。核心问题不只是用户如何读取数据，
而是用户如何从研究一路走到策略，并保证研究阶段和策略阶段使用同一套数据、特征、参数和语义。

目标用户体验：

```bash
kairos data download us-equity-momentum-data

kairos study open us-equity-momentum
kairos study add-data --workspace us-equity-momentum --name returns --dataset market.returns.equity.us.1d
kairos study add-factor --workspace us-equity-momentum --name momentum_12_1 --file factors/momentum_12_1.py
kairos run start --study us-equity-momentum --mode research
kairos study freeze us-equity-momentum --version 1.0.0

kairos strategy open us-equity-momentum-long-only --from-study us-equity-momentum@1.0.0
kairos strategy bind-factor --workspace us-equity-momentum-long-only --name primary --study-factor momentum_12_1
kairos strategy freeze us-equity-momentum-long-only --version 1.0.0
kairos run start --snapshot us-equity-momentum-long-only@1.0.0 --mode backtest
```

系统要保证：

- Study 面向 Historical View，Strategy 面向 Historical/Live View，但共享同一个 DataSet identity、contract、API 和质量门禁；
- Study 证明的信号，就是 Strategy 使用的信号；
- Study 冻结的数据 Release，就是 Strategy 回测使用的数据 Release；
- Strategy paper/live 使用的实时数据流，必须和研究/回测中的历史 DataSet 共身份、共 schema 语义；
- Study 中的本地因子代码 hash，进入 Strategy 的锁文件或 promotion evidence；
- Strategy 不重新实现一套漂移后的因子逻辑；
- 回测、paper、live 只改变运行环境，不改变策略语义。

## 2. 核心产品形态

最重要的架构原则：

```text
上下层级之间只通过“契约”和“产物”沟通。
上层消费命名产物，不依赖下层实现。
下层承诺契约，不感知上层业务。
```

这意味着 Data、Factor、Study、Strategy 和 Run 都必须有清晰边界：

| 层 | 对外产物 | 对外契约 | 上层可见 | 必须隐藏 |
|---|---|---|---|---|
| Data Layer | Historical Release、Live View | DataSet Contract | `dataset_id`、schema、time/grain、quality/freshness、能力状态 | provider、CSV、connector、存储路径、清洗流程 |
| Factor Layer | Factor Output、Frozen Factor | Factor Contract | 输入别名、输出 schema、代码 hash、参数、point-in-time 语义 | 内部计算细节、临时 notebook 对象 |
| Study Layer | Study Lock | Study Contract | 数据别名、factor 名称、研究窗口、证据 hash | 数据来源实现、factor 临时实验过程 |
| Strategy Layer | Strategy Lock | Strategy Contract | 输入表、策略代码 hash、risk/execution 边界、可运行模式 | Study 内部探索过程、数据接入过程 |
| Run Layer | Run Manifest、Run Outputs | Runtime Contract | snapshot、clock、feed、execution gateway、运行结果 | 编辑中的 workspace 文件 |

层间依赖只能指向稳定产物：

```text
Factor 依赖 DataSet Contract，不依赖 Data Connector
Study 依赖 Data Release / Factor Contract，不依赖 CSV/vendor/source path
Strategy 依赖 Study Lock / Frozen Factor / DataSet Live View，不依赖 Study 的探索过程
Run 依赖 Strategy Snapshot，不依赖编辑中的 Strategy Product workspace
```

所以，对于上层的 Factor 和 Study 来说，Data 只是一个有名字、有契约、有能力状态的数据集。它不需要知道数据
是来自平台 util、外部 provider、CSV 导入，还是用户实时 connector。实现细节只属于 Data Layer 内部。

用户面对的核心产品不是很多分散命令，而是四个独立产品：

| 产品 | 产品形态 | 用户是否编辑 | 主要职责 |
|---|---|---|---|
| Data Product | 独立数据产品 | 通过 download/write contract 扩展 | 下载内置数据；把外部数据写入契约；提供 Release、Live View、质量和 lineage |
| Study Product | 独立研究产品 | 是 | 组合 Data、编写因子、验证假设、冻结研究证据 |
| Strategy Product | 独立策略产品 | 是 | 把 Frozen Study 的信号变成决策、风险约束和可运行策略 |
| Run Product | 独立执行产品 | 否；由系统生成运行工作区 | 执行 Study/Strategy 的冻结或候选目标，管理 research/backtest/paper/live、运行证据、回放和诊断 |

四个产品的核心产物：

| 产物 | 来源 | 用途 |
|---|---|---|
| Data Release | Data Product | 不可变数据版本 |
| Live View | Data Product | 实时推进视图 |
| Study Lock | Study Product | 冻结研究输入、因子代码、参数和证据 |
| Strategy Lock | Strategy Product | 冻结策略代码、Study 证据、数据和执行约束 |
| Run Workspace | Run Product | 单次 research/backtest/paper/live 执行目录，只运行明确 target |
| Run Manifest | Run Product | 记录一次运行的环境、输入、输出和 hash |

每个产品都必须按同一套口径定义：

```text
Product = CLI + API + Contract + Extension Surface + Artifacts
```

| 产品 | 提供什么 | CLI | API | Contract | 开放什么 | 产出什么 |
|---|---|---|---|---|---|---|
| Data | 数据生产和数据身份 | `data download/write/list/describe/quality/compare` | `DataProductApi.download/write_file/write_live` | DataSet Contract、Download Spec、Write Contract | download key、write connector、schema、quality/freshness profile | Data Release、Live View、manifest、quality/freshness report |
| Study | 研究工作区和因子验证 | `study open/add-data/add-factor/inspect/freeze` | `StudyProductApi.open/add_data/add_factor/freeze` | Study Contract、Factor Contract | study spec、factor code、参数、notebook exploration | Study Lock、Factor profile、research evidence |
| Strategy | 策略代码和决策边界 | `strategy open/bind-factor/set-risk/freeze/promote` | `StrategyProductApi.open/bind_factor/freeze` | Strategy Contract、InputTable Contract | `model.py`、risk policy、execution policy | Strategy Lock、promotion evidence |
| Run | 执行、回放和诊断 | `run start/inspect/replay/compare` | `RunProductApi.start_study/start_snapshot/inspect/replay/compare` | Run Contract、Runtime Contract | run mode、clock、runtime connector、safety gate | Run Workspace、Run Manifest、outputs、diagnostics |

一句话：

```text
Data Product 解决“可信数据如何独立生产，并以同一身份提供给研究和策略”
Study Product 解决“这个信号是否值得相信”
Strategy Product 解决“这个信号如何变成可执行决策”
Run Product 解决“如何执行、记录、回放和诊断一次 Study/Strategy 运行”
Lock/Manifest 解决“产品之间如何证明没有语义漂移”
```

不过度设计的边界：

- 不新增独立 Factor Product；Factor 是 Study Product 中满足协议的代码；
- 不给 Strategy 单独设计数据读取面；Strategy 复用 DataSet API 和 Study Lock 输入；
- 不让 Study/Strategy 承担数据下载或外部数据写入职责；
- Run 是独立产品，但 run 目录不是用户手写项目；Run Workspace 是系统生成的执行证据目录；
- 不要求探索期所有临时数据都注册；只有 freeze、backtest、paper/live 才收紧门禁；
- 不为每种资产单独设计一套 Study/Strategy API。
- 不把 Data Layer 的实现细节透传给 Factor、Study 或 Strategy。

Hash 的使用边界：

```text
路径用于编辑，hash 用于冻结和跨产物引用。
```

本地 Draft workspace、notebook exploration、临时 CSV 和用户正在编辑的 factor/model 文件，不需要每一步都重新 hash
或 fail closed。系统可以提示 dirty、missing field 或 schema warning，但不应该把本地探索变成持续审计。

只有在以下 artifact 边界收紧 hash：

- Data Release / Live View 发布时记录 content/contract/manifest hash；
- Study freeze 时记录 Data Release 和 Factor code hash；
- Strategy freeze 时检查引用的 Study Lock、Factor hash 和 Data Release evidence；
- Run Manifest 记录本次执行的 frozen snapshot 和 input artifact evidence；
- replay/audit/promotion 使用 hash 复核冻结产物是否被替换。

质量检查也按用途分层：artifact identity、contract、point-in-time 和安全语义是 gate；coverage、history length、
source receipt 完整度、统计异常和运行指标默认是 diagnostic，除非某个 Q3/Q4 晋级策略明确把它们提升为 gate。

## 3. 用户心智模型

用户首先理解四个产品：

1. **Data**：独立数据产品，负责下载内置数据、把外部数据写入契约，并产出 Data Release/Live View。
2. **Study**：独立研究产品，负责探索数据、编写因子、验证假设，并产出 Study Lock。
3. **Strategy**：独立策略产品，负责把 Frozen Study 的信号变成目标持仓、交易意图和风控约束，并产出 Strategy Lock。
4. **Run**：独立执行产品，负责执行 Study research run 或 Strategy Snapshot，并产出 Run Workspace、Run Manifest 和运行结果。

配套概念：

- **Factor/Feature**：Study Product 内满足协议的代码或发布后的信号表。
- **Lock/Snapshot**：Study、Strategy 或 Run 的冻结证据。

推荐工作区布局：

```text
studies/us-equity-momentum/
  study.yaml
  data.yaml
  factors/
    momentum_12_1.py
    liquidity_filter.py
  notebooks/
  runs/
  locks/

strategies/us-equity-momentum-long-only/
  strategy.yaml
  model.py
  risk.yaml
  execution.yaml
  locks/

runs/us-equity-momentum-long-only/<run_id>/
  snapshot.json
  manifest.json
  inputs/
  decisions/
  intents/
  orders/
  fills/
  logs/
```

用户日常编辑 Study Product workspace 和 Strategy Product workspace；需要新增数据时，用户通过 Data Product 的 download/write 入口维护
connector/contract。Run Workspace 由 Run Product 生成，用户通过 inspect/replay/compare 使用它，而不是手写它。
发布后的 DataSet 由统一数据身份通道治理，平台 util 数据和用户接入数据不走两套规则。

## 4. Data Product API

Data Product API 面向可信数据资产，不表达某次研究。DataSet 是 Data Product 对外暴露的核心契约：

```text
DataSet = 带 primary_time、schema、质量等级，并可形成 Historical Release / Live View 的时间序列数据集
```

Data 是独立产品，不是 Study 的附属功能。它对用户只暴露两个主职责：

| 职责 | 用户动作 | 输入 | 产物 |
|---|---|---|---|
| 下载内置/已注册数据 | `kairos data download <data_key>` | data product key、时间范围、质量目标 | Historical Release、quality report、download report |
| 写入外部数据到契约 | `kairos data write ... --as <dataset_id> --contract <contract>` | 文件、批处理目录、用户 connector 或实时流 | Data Release 或 Live View、manifest、quality/freshness report |

Data Product 定义：

| 维度 | 内容 |
|---|---|
| 提供 | Catalog、DataSet query、download、write、quality/freshness gate、release/live view 管理 |
| 开放 | download key、Data Download Spec、Data Write Contract、用户 historical/live connector、schema/profile |
| Contract | DataSet Contract、Download Spec、Write Contract、Quality/Freshness Profile |
| 产出 | Historical Release、Live View、Data Release Manifest、Live View Manifest、quality report、freshness report |
| 不提供 | Study 研究逻辑、Factor 计算逻辑、Strategy 决策逻辑、Run 执行逻辑 |

其他命令如 `list/search/describe/quality/compare` 都是围绕这两个主职责的辅助能力。Study、Factor 和 Strategy
不能触发隐式下载，也不能直接写外部数据；它们只能引用 Data 已经产出的命名数据产物。

DataSet 可以来自外部供应商、内部转换、用户自定义数据源或已发布 feature table。来源记录在 Data Layer 的
manifest 中，不进入 Factor、Study 或 Strategy 的业务代码。只要进入正式流程：

- 供 Study/backtest 使用的历史数据，必须形成 Historical Release；
- 供 paper/live 使用的实时数据，必须形成 Live View；
- 二者必须挂在同一个 DataSet identity 和 contract 下。

历史数据和实时数据不是两套系统，而是同一个 DataSet identity 的两个视图：

```text
Historical View = 用于 Study/backtest 的历史查询和回放
Live View = 用于 Strategy paper/live 的实时推进
```

两者必须共享：

- 同一个 `dataset_id`；
- 同一个 schema 或兼容 schema；
- 同一个 `primary_time` 和 grain；
- 同一个 identity/reference 语义；
- 同一个质量和 freshness 门禁；
- 同一个 DataSet API。

差异只在数据交付方式：

| 视图 | 交付方式 | 典型消费者 |
|---|---|---|
| Historical View | immutable Data Release、query、replay | Study、Backtest |
| Live View | subscription/stream、snapshot、freshness monitor | Strategy Paper/Live |

因此，Strategy 看到的是实时推进，Study 看到的是历史数据，但本质上必须是同一份数据定义。

Data 的规范程度应该按用途分层，而不是一开始就要求所有研究素材都进入强治理。

| 层级 | 形态 | 用户自由度 | 系统要求 | 可进入 |
|---|---|---|---|---|
| Exploratory Data | 临时文件、notebook 中间表、外部 CSV | 最高 | 不承诺复现，不进入正式冻结 | Draft Study |
| Study Data | Study Product 声明的数据输入 | 中等 | 最小 data contract、绑定产物 ID 和质量状态 | Candidate/Frozen Study |
| Registered Data | Catalog 中的 Data Product/Release | 低 | schema、primary_time、grain、quality、lineage、coverage | Study；达到 Q3 后进入正式 Backtest |
| Production Data | Q4/production Release 或 live feed | 最低 | 新鲜度、监控、fail-closed、对账、回放一致性 | Paper/Live |

因此，Data 不应该被设计成“所有表都必须长一样”。只要是有明确 `primary_time` 的时间序列，就应该允许进入
Study；`entity_key` 只在 panel/cross-sectional 数据中强制。真正必须统一的是最小公共契约。

DataSet Contract 描述稳定语义，不描述某次导入或某个供应商实现：

```yaml
data_contract:
  identity:
    dataset_id: market.returns.equity.us.1d
  time:
    primary_time: decision_date
    boundary_policy: "[start,end)"
  grain:
    kind: panel
    entity_key: instrument_id
  schema:
    fields:
      - instrument_id
      - decision_date
      - total_return_1d
  semantics:
    timezone: UTC
    point_in_time: true
    available_time: required_for_backtest
  governance:
    minimum_quality: Q2
    lineage: required
    coverage: required
  capabilities:
    historical: required
    live: optional
```

具体产物记录在 manifest 中：

```yaml
data_release_manifest:
  dataset_id: market.returns.equity.us.1d
  release_id: returns_...
  contract_hash: sha256:...
  content_hash: sha256:...
  quality_level: Q2
  source_archive_ref: source://...

live_view_manifest:
  dataset_id: market.returns.equity.us.1d
  live_view_id: returns_live_...
  contract_hash: sha256:...
  freshness_status: healthy
  source_archive_ref: source://...
```

这个 contract 不限制研究员新增字段、新数据类型或新频率；它只保证数据能被连接、冻结、复现、回测和审计。
一旦用户把数据发布为 Data Release 或 Live View，它就从研究素材变成平台资产，必须比 Factor 和 Study 更严格。

DataSet 支持的最小 grain：

| grain | 必须字段 | 例子 | join 语义 |
|---|---|---|---|
| `single_series` | `primary_time` | risk-free rate、VIX、宏观指标 | 按时间 as-of join 或广播到横截面 |
| `panel` | `primary_time` + `entity_key` | 股票收益、股票池、逐证券特征 | 按时间和实体 join |
| `event_stream` | `primary_time` + `event_time` | quote、trade、新闻事件 | 按事件时间/可见时间回放 |
| `calendar` | `session_date` 或 `primary_time` | 交易日历、调仓日 | 按日历约束 join |

这意味着用户可以把一个只有日期和数值的 CSV 注册为 Study Data；它不需要伪造 `instrument_id`。
事件流可以把 `available_time` 声明为 `primary_time`，同时保留 `event_time` 用于事件顺序和延迟审计。

### 4.1 CLI

```bash
kairos data list
kairos data search equity momentum
kairos data describe market.returns.equity.us.1d

kairos data download us-equity-momentum-data
kairos data register-download --key us-equity-momentum-data --spec us-equity-momentum-data.yaml

kairos data write --file analyst_estimates.csv --as reference.estimates.equity.us --contract estimates.contract.yaml
kairos data write --live --connector user_data_sources/realtime_quotes.py --as market.quotes.equity.us --contract quotes.contract.yaml

kairos data quality market.returns.equity.us.1d
kairos data compare returns_a returns_b
```

### 4.2 Python API

```python
from kairos.data import ResearchDataClient

data = ResearchDataClient("data")
download = data.download("us-equity-momentum-data")
external = data.write_file(
    file="analyst_estimates.csv",
    as_dataset="reference.estimates.equity.us",
    contract="estimates.contract.yaml",
)
dataset = data.dataset("market.returns.equity.us.1d")
returns = dataset.query(
    start=start,
    end=end,
).collect("pandas")
```

等价的简写可以保留：

```python
returns = data.get("market.returns.equity.us.1d", start=start, end=end).collect("pandas")
```

### 4.3 平台提供的工具

平台可以内置一些 provider、schema、quality profile、download 模板和 write 模板，但这些只是工具，不是特殊通道。

平台提供：

- Data Product registry；
- Release catalog；
- Provider connector util；
- Source archive util；
- quality profile util；
- lineage 和 coverage util；
- point-in-time 查询；
- live subscription/freshness util；
- run mode 下的联网限制。

`kairos data download` 的主参数应该是 data product key，而不是 YAML 文件路径：

```bash
kairos data download us-equity-momentum-data
```

YAML 是这个 key 的定义文件或注册规格，只在平台维护者、数据工程用户或高级研究员创建/修改 data download
product 时出现：

```bash
kairos data register-download --key us-equity-momentum-data --spec us-equity-momentum-data.yaml
```

这样上层用户只依赖命名产物，避免把 spec 文件路径当成正式接口。

### 4.4 统一数据接入通道

系统内置数据和用户数据必须走同一条身份通道：

```text
Data Source Connector
  -> Source Archive / Live Event Ingestion
  -> DataSet Contract
  -> Quality/Freshness Gate
  -> Historical Release and/or Live View
  -> DataSet API
```

允许用户扩展：

- `user_data_sources/` 下的数据源适配器；
- 历史数据导入；
- 实时数据流接入；
- 自定义 schema；
- 自定义 quality/freshness profile；
- 自定义 Data Download Spec；
- 自定义 Data Write Contract。

约束：

- 用户数据源不能绕过 DataSet identity/contract 直接进入 Study/Strategy；
- 历史输入必须先经过 Source 归档、schema 校验、quality gate 和 Release 注册；
- 实时输入必须先经过 schema 校验、freshness gate、identity 映射和 live view 注册；
- 正式回测和 live 不允许读取未治理目录。

平台内置 connector 和用户自定义 connector 的差别只在便利程度：

| 来源 | 平台提供什么 | 是否特殊治理 |
|---|---|---|
| Built-in Connector | 现成 connector、schema 模板、quality profile | 否 |
| User Connector | 用户提供 connector/schema/profile | 否 |

两者发布出的 DataSet 在 Study/Strategy 看来没有身份差别。

### 4.5 外部历史数据写入

CSV 是用户最常见的外部历史数据入口。平台应该支持把 CSV 写入 DataSet contract，但 CSV 只是输入格式，
不是上层可依赖的正式数据产物。

命令形态：

```bash
kairos data write \
  --file analyst_estimates.csv \
  --as reference.estimates.equity.us \
  --contract estimates.contract.yaml
```

面板数据 `estimates.contract.yaml`：

```yaml
kind: data.contract
dataset_id: reference.estimates.equity.us
primary_time: available_time
grain:
  kind: panel
  entity_key: instrument_id
boundary: "[start,end)"
schema:
  fields:
    - name: instrument_id
      type: string
      required: true
    - name: available_time
      type: timestamp
      required: true
    - name: estimate_eps
      type: decimal
      required: false
semantics:
  timezone: UTC
  point_in_time: true
governance:
  minimum_quality: Q2
```

单时间序列 `macro_rate.contract.yaml`：

```yaml
kind: data.contract
dataset_id: macro.risk_free_rate.us.1d
primary_time: observation_date
grain:
  kind: single_series
boundary: "[start,end)"
schema:
  fields:
    - name: observation_date
      type: date
      required: true
    - name: rate
      type: decimal
      required: true
semantics:
  timezone: UTC
  point_in_time: true
  join_policy: asof
governance:
  minimum_quality: Q2
```

导入规则：

- CSV 必须映射到明确 `dataset_id`；
- 必须声明 `primary_time` 和 `grain`；
- `grain.kind=panel` 时必须声明 `entity_key`；
- 时间字段必须可解析并带时区或可安全归一化；
- 字段类型必须通过 schema 校验；
- 写入会生成 Data Release、content hash、lineage 和 quality report；
- 不符合 contract 的 CSV 只能作为 Exploratory Data 留在 Draft Study，不能 freeze。

### 4.6 外部实时数据写入

实时数据流和历史数据使用同一个 DataSet contract。实时 connector 只是外部输入形态，正式产物是 Live View。

命令形态：

```bash
kairos data write --live \
  --connector user_data_sources/realtime_quotes.py \
  --as market.quotes.equity.us \
  --contract quotes.contract.yaml
```

最小实时 connector 协议：

```python
def subscribe(params, context):
    """Yield normalized records compatible with the DataSet contract."""
```

实时 contract 至少声明：

```yaml
kind: data.contract
dataset_id: market.quotes.equity.us
primary_time: available_time
grain:
  kind: event_stream
schema:
  fields:
    - name: instrument_id
      type: string
      required: true
    - name: event_time
      type: timestamp
      required: true
    - name: available_time
      type: timestamp
      required: true
    - name: bid
      type: decimal
      required: true
    - name: ask
      type: decimal
      required: true
freshness:
  max_age_seconds: 5
governance:
  minimum_quality: Q4
```

接入规则：

- live records 必须通过同一 schema；
- live records 必须有 `primary_time`；
- live records 必须通过 identity/reference 映射；
- paper/live 前必须通过 freshness monitor；
- live view 可以同时归档为 Source，用于之后回放和对账；
- 缺少 live view 时，Strategy 不能通过 paper/live gate。

### 4.7 异步实时数据平面

WebSocket、SSE、私有订阅、轮询和用户自定义实时 connector 都不是 Study 或 Strategy 的直接依赖。它们属于
Data Product 的 Source Transport；正式对外产物只能是同一个 DataSet identity 下的 Live View。

目标链路：

```text
Source Transport(WebSocket/SSE/Polling/User Connector)
  -> Source Connector
  -> CanonicalEventEnvelope / DataSetRecord
  -> EventSource
  -> BoundedEventChannel / ConflatedLatestChannel
  -> Live View
  -> Run Runtime Contract
  -> Strategy InputTable
```

当前实现中的对应关系：

| 设计概念 | 当前实现 |
|---|---|
| Source Transport | `kairos.connectors.binance.market_stream`、`kairos.connectors.massive.websocket` |
| Source Connector | `BinanceCanonicalStreamService`、`MassiveCanonicalStreamService` |
| Canonical Event | `CanonicalEventEnvelope` |
| Async Channel | `BoundedEventChannel`、`ConflatedLatestChannel` |
| Capture / Replay | `CanonicalCaptureWriter`、`CapturedCanonicalEventSource`、`ReplayEventFeed` |
| Supervised Runtime | `AsyncKairosRuntime`、`AsyncServiceSupervisor`、`ManagedServiceSpec` |
| Data CLI | `kairos data live-binance`、`kairos data soak-binance`、`kairos data write --live --connector ...` |

关键边界：

- WebSocket 只能出现在 Data Product 或 connector 层，不进入 Study、Factor、Strategy 的用户代码；
- Strategy paper/live 消费的是 Live View 或 Runtime Contract 中绑定的 `EventSource`，不能直接订阅 provider WebSocket；
- Run Product 负责启动和监督异步服务，记录 service snapshot、fault、restart、capture 和 freshness 证据；
- Historical Release 与 Live View 必须共享 `dataset_id`、schema 语义、`primary_time`、identity/reference 语义；
- live capture 必须能形成可回放证据，用于 paper/live 复盘、对账和之后发布为 Historical Release；
- channel overflow、drop、conflation、reconnect 和 sequence gap 必须进入 manifest/diagnostics，不能静默丢失。

`ws` 在 Kairos CLI 中不作为正式术语使用：工作区参数使用 `--workspace`，WebSocket 只作为 Source Transport
实现细节出现在 connector 文档或诊断中。旧的 `--ws` 仅保留为兼容别名。

### 4.8 Data 晋级路径

Data 注册后的目标不是只服务一次研究，而是成为长期可复用资产。推荐晋级路径：

```text
local/exploratory file
  -> Study declared input
  -> Registered Data Release
  -> Approved for Research (Q2)
  -> Approved for Backtest (Q3)
  -> Approved for Production (Q4 historical + live view)
```

各等级含义：

| 等级 | 用户含义 | 系统门禁 |
|---|---|---|
| 未注册 | 可以探索，但不承诺复现 | 只允许 Draft Study |
| Q2 Research | 可以进入正式研究 | schema、hash、lineage、基础 quality |
| Q3 Backtest | 可以进入正式回测 | coverage、point-in-time、关键异常处置 |
| Q4 Production | 可以进入 paper/live | live 接入、新鲜度、监控、对账、fail-closed |

这也是 Data 与 Factor/Study 的核心差异：

```text
Data 更硬：保证事实、时间、来源、质量、监控
Factor 更软：允许用户快速写、快速试，但 freeze 时记录代码 hash
Study 软到硬：Draft 灵活，Frozen 后严格
Strategy 更硬：只能消费 Frozen Study 和达标 Data
```

所以“注册 Data 理论上可以复现，接入实盘后可以直接用”是正确目标，但必须通过 Q2/Q3/Q4 分级来表达适用范围。

## 5. Study Product API

Study Product 是研究阶段的独立产品。它通常以用户可编辑 workspace 的形态存在，必须足够灵活，因为研究中
数据和因子会动态增加。

Study 的边界是组合和冻结证据，不是实现数据接入。它对下层只认三类东西：

- Data alias 绑定到 DataSet/Release；
- Factor name 绑定到本地代码和 Factor Contract；
- Study Lock 记录所有输入产物、契约 hash、代码 hash 和参数。

Study 不关心 DataSet 是由平台 util、CSV 导入、外部 provider 还是用户 connector 生成。它只要求 Data Layer
已经产出满足契约的数据产物。

Study Product 定义：

| 维度 | 内容 |
|---|---|
| 提供 | 研究 workspace、数据别名绑定、Factor 注册、研究配置、冻结研究证据 |
| 开放 | `study.yaml`、本地 factor code、factor 参数、notebook exploration、标签/报告配置 |
| Contract | Study Contract、Factor Contract、Study Lock Schema |
| 产出 | Study Lock、Factor profile、research evidence、可供 Strategy 绑定的 Frozen Factor |
| 不提供 | 数据下载、外部数据写入、策略执行、broker/execution gateway |

### 5.1 CLI

```bash
kairos study open us-equity-momentum
kairos study add-data --workspace us-equity-momentum --name returns --dataset market.returns.equity.us.1d
kairos study add-data --workspace us-equity-momentum --name universe --dataset market.universe.equity.us.1d
kairos study add-data --workspace us-equity-momentum --name estimates --dataset reference.estimates.equity.us
kairos study add-factor --workspace us-equity-momentum --name momentum_12_1 --file factors/momentum_12_1.py
kairos study inspect us-equity-momentum
kairos study freeze us-equity-momentum --version 1.0.0
```

`study add-data` 只绑定 Data Product 已产出的数据集：

| 形式 | 示例 | 结果 |
|---|---|---|
| 已注册 DataSet | `--dataset market.returns.equity.us.1d` | Study 记录数据别名和 Data Product |
| Data 写入后的 DataSet | `--dataset reference.estimates.equity.us` | Study 记录数据别名和 Data Release |

文件输入不能绕过 Data Product。CSV、connector、source archive 和清洗细节属于 Data Layer；Study 的正式 spec
只绑定命名数据产物。没有 contract 的文件只能挂到 Draft Study 的 exploration 区，不能进入 Candidate/Frozen
Study。

### 5.2 Study Contract (study.yaml)

```yaml
kind: study.workspace
id: us-equity-momentum
hypothesis: 过去 12-1 个月总收益较高的股票，未来一个月继续相对跑赢
window:
  start: 2005-01-01T00:00:00-05:00
  end: 2026-07-01T00:00:00-04:00
data:
  returns:
    product: market.returns.equity.us.1d
    quality: Q2
  universe:
    product: market.universe.equity.us.1d
    quality: Q2
factors:
  momentum_12_1:
    path: factors/momentum_12_1.py
    inputs:
      returns: returns
    output:
      primary_time: decision_date
      fields: [instrument_id, decision_date, momentum_12_1]
    point_in_time: true
labels:
  forward_return_1m:
    from: returns
    horizon: 1m
    created_in: study
```

### 5.3 Python API

目标态 API：

```python
from kairos.research_platform import open_study

study = open_study("us-equity-momentum")

returns = study.data("returns").pandas()
signal = study.factor("momentum_12_1").pandas()
report = start_research_run(study="us-equity-momentum")
```

Study API 的职责是把 Dataset 和 Factor Code 组合起来：

```python
study.add_data("returns", "market.returns.equity.us.1d")
study.add_data("estimates", "reference.estimates.equity.us")
study.add_factor("momentum_12_1", path="factors/momentum_12_1.py")
study.freeze(version="1.0.0")
```

当前仓库的 `kairos.research_platform.open_study`、`StudySession.data` 和 `kairos study create/start/freeze`
仍是过渡形态；它们应逐步收敛到上面的多数据输入、命名 factor、Research Run Client 和 Study Lock 语义。

### 5.4 生命周期

| 状态 | 行为 | 适用场景 |
|---|---|---|
| Draft | 可以添加/删除数据、因子、标签和实验配置 | 日常探索 |
| Candidate | 输入解析为候选 Release，生成一致性诊断 | 准备复现或晋级 |
| Frozen | 数据 Release、因子代码、参数和窗口形成冻结快照，并记录 hash | 正式报告、策略晋级、回测 |
| Archived | 只读保留 | 历史复现 |

### 5.5 冻结内容

`locks/1.0.0/study.lock.json` 必须记录：

- Study spec hash；
- 所有 Data Release ID 和 content hash；
- freeze 时采样的 factor 文件 hash；
- factor 参数、依赖和输出 schema；
- Python/package 版本；
- 查询窗口；
- quality 和 lineage 检查结果；
- Run Product 生成的 research run manifest。

## 6. Strategy Product API

Strategy Product 是策略阶段的独立产品。它通常以用户可编辑 workspace 的形态存在，从 Frozen Study 创建，
并复用 Study Lock 中的数据和因子，输出可运行策略。策略阶段不重新选择数据、不重新定义因子；如果需要改变
输入或信号，先生成新的 Study Lock。

Strategy 是代码项目，不是配置文件。`strategy.yaml` 只描述边界和绑定关系，真正的决策逻辑在 `model.py`。
Strategy Product 不负责执行；执行由 Run Product 完成。运行时不得直接执行工作区里的可变文件；必须先选择或
生成 Strategy Lock，然后交给 Run Product 创建独立 Run Workspace。

Strategy 的上游依赖必须停在契约和产物层：

- 通过 Study Lock 引用 Frozen Factor；
- 通过 DataSet identity 引用 Historical Release 或 Live View；
- 通过 `InputTable` 消费所有输入；
- 通过 Strategy Lock 固定模型代码、risk/execution 边界和运行模式。

Strategy 不关心 factor 如何在 Study 中探索出来，也不关心 DataSet 的接入实现。paper/live 跑不起来时，
应该暴露缺失的 DataSet capability 或 freshness/quality 问题，而不是要求用户改 Strategy 内部逻辑。

paper/live 下的 factor 输入必须先被显式运行化，Strategy 不能临时在 `model.py` 里补一套计算逻辑。允许的
运行化形态只有两类：

| 形态 | 适用 | Strategy 看到什么 | 必须记录 |
|---|---|---|---|
| Frozen Factor Replay | backtest 或 historical-simulation | Historical Release 上重放后的 `InputTable` | Study Lock hash、factor code hash、参数 hash、输入 Release hash |
| Published Feature / Factor Runtime | paper/live | 同 DataSet identity 下的 Feature Live View 或 runtime 产出的 `InputTable` | Feature contract hash、runtime code hash、输入 Live View identity、freshness 状态 |

如果 Frozen Factor 依赖的输入没有 live capability，或者 factor runtime 尚未声明，paper/live gate 必须
fail closed，并输出 `factor_runtime_missing_input` 或等价诊断。

Strategy Product 定义：

| 维度 | 内容 |
|---|---|
| 提供 | 策略 workspace、Study Lock 绑定、InputTable 绑定、策略模型代码、risk/execution 边界、promotion |
| 开放 | `strategy.yaml`、`model.py`、risk policy、execution policy、promotion evidence |
| Contract | Strategy Contract、InputTable Contract、Strategy Lock Schema |
| 产出 | Strategy Lock、strategy model hash、risk/execution policy hash、promotion bundle |
| 不提供 | 数据接入、Factor 探索、Run 执行、运行结果存储 |

### 6.1 CLI

```bash
kairos strategy open us-equity-momentum-long-only --from-study us-equity-momentum@1.0.0
kairos strategy bind-factor --workspace us-equity-momentum-long-only --name primary --study-factor momentum_12_1
kairos strategy set-risk us-equity-momentum-long-only risk.yaml
kairos strategy freeze us-equity-momentum-long-only --version 1.0.0
kairos strategy inspect us-equity-momentum-long-only
kairos strategy promote --snapshot us-equity-momentum-long-only@1.0.0 --to paper
```

### 6.2 Strategy Contract (strategy.yaml)

```yaml
kind: strategy.workspace
id: us-equity-momentum-long-only
derived_from:
  study: us-equity-momentum
  version: 1.0.0
model:
  path: model.py
inputs:
  signal:
    from_study_factor: momentum_12_1
  universe:
    from_study_data: universe
portfolio:
  construction: long_only_top_decile
  rebalance: monthly
risk:
  max_position_weight: 0.02
  max_turnover: 0.25
execution:
  decision_time: session_close
  execution_time: next_session_open
  order_style: market_on_open_proxy
run_modes:
  allowed: [backtest, historical-simulation, paper]
```

### 6.3 Python API

目标态 API：

```python
strategy = open_strategy_snapshot("us-equity-momentum-long-only", version="1.0.0")

decision = strategy.decide(context)
intent = decision.to_intent()
```

`open_strategy(..., version=...)` 打开的是 Strategy Lock/Snapshot。编辑中的 Strategy Product workspace 只能用于修改和
freeze，不能作为正式 run 的执行对象。

当前仓库已有 `StrategySpec`、Strategy Registry 和 `kairos strategy register-*/inspect/status/promote`
等过渡入口；`open_strategy_snapshot` 是目标态代码入口命名，不代表当前包已经完整提供该模块。

### 6.4 Strategy 输入平面

Strategy 拿到的数据和因子必须共用一种输入格式。对 Strategy 来说，Data 和 Frozen Factor 都是
`InputTable`：

```yaml
input_table:
  name: primary
  kind: factor
  primary_time: decision_date
  grain:
    kind: panel
    entity_key: instrument_id
  schema_hash: sha256:...
  contract_hash: sha256:...
  artifact_ref: study://us-equity-momentum/locks/1.0.0/factors/momentum_12_1
  source_hash: sha256:...
```

最小 Python 形态：

```python
signal = context.input("primary")
universe = context.input("universe")
```

约束：

- `context.input(name)` 只能返回 Strategy Lock 中声明的输入；
- Data 输入的 `source_hash` 是 Data Release hash；
- Factor 输入的 `source_hash` 是 Study Lock 中记录的 factor code hash 和参数 hash；
- `artifact_ref` 指向 Data Release、Live View 或 Frozen Factor 产物，不指向物理文件路径；
- 所有输入都有 `primary_time`、`grain` 和 schema；
- Strategy 不直接区分“这是 data 还是 factor 的读取路径”，只消费统一输入表。

### 6.5 Strategy Lock

`locks/1.0.0/strategy.lock.json` 必须记录：

- Strategy spec hash；
- strategy model code hash；
- derived Study ID/version/hash；
- 绑定的 Study factor hash；
- 绑定的 Data Release hash；
- risk 和 execution policy hash；
- allowed run modes；
- promotion evidence；
- 可选的历史 Run evidence 引用。

## 7. Run Product API

Run Product 是独立执行产品。它不负责研究编辑、不负责策略编辑、不负责下载数据；它只负责拿一个 Study
target 或 Strategy Snapshot，在指定运行模式下执行，并产出可审计、可回放、可诊断的运行证据。

Run Product 是独立产品，但不是用户手写项目。用户通过 CLI/API 创建、查看、回放和比较 Run；Run Workspace
由系统生成。

Run Product 定义：

| 维度 | 内容 |
|---|---|
| 提供 | research/backtest/paper/live 执行、运行工作区、manifest、inspect、replay、compare、diagnostics |
| 开放 | Run Contract、Runtime Contract、mode、clock、feed binding、execution gateway、safety gate |
| Contract | Run Contract、Runtime Contract、Run Manifest Schema |
| 产出 | Run Workspace、Run Manifest、decisions/intents/orders/fills、reports、diagnostics |
| 不提供 | Study 编辑、Strategy 编辑、数据下载、外部数据写入 |

### 7.1 CLI

```bash
kairos run start --study us-equity-momentum --mode research
kairos run start --snapshot us-equity-momentum-long-only@1.0.0 --mode backtest
kairos run inspect --run-id run_...
kairos run replay --run-id run_...
kairos run compare --first run_... --second run_...
```

### 7.2 Python API

```python
runs = open_run_client()
research = runs.start(study="us-equity-momentum", mode="research")
backtest = runs.start(snapshot="us-equity-momentum-long-only@1.0.0", mode="backtest")
report = runs.inspect(backtest.run_id)
replay = runs.replay(backtest.run_id)
```

### 7.3 Run Contract

```yaml
kind: run.request
target:
  kind: strategy
  snapshot: us-equity-momentum-long-only@1.0.0
mode: backtest
clock:
  start: 2020-01-01T00:00:00-05:00
  end: 2026-07-01T00:00:00-04:00
data_plane:
  historical: required
  live: forbidden
execution:
  connector: simulated
```

Run Contract 只描述本次执行需要的运行环境，不改变 Study 或 Strategy 语义。Research、backtest、paper、live
可以替换 clock、feed、execution gateway 和 safety gate，但不能替换 Study Lock、Strategy Snapshot、
Frozen Factor 或 DataSet identity。

### 7.4 Run Workspace

每次执行都创建单独的 run workspace：

```text
runs/us-equity-momentum-long-only/<run_id>/
  snapshot.json
  manifest.json
  inputs/
  decisions/
  intents/
  orders/
  fills/
  logs/
  reports/
```

运行规则：

- `run start` 必须使用明确 target，例如 `--study <study_id>` 或 `--snapshot <strategy_id>@<version>`；
- run workspace 中保存本次使用的 `strategy.lock.json` 副本或引用 hash；
- 运行开始前比较工作区当前 hash 与 snapshot hash；
- 如果工作区有未冻结改动，运行仍只使用 snapshot，并在 manifest 中记录 `workspace_dirty=true`；
- 如果用户要求运行当前工作区，系统必须先 freeze 新版本；
- backtest、paper、live 都使用同一个 snapshot 语义，只替换 clock、feed、execution gateway 和安全门禁。

这避免了“看起来跑的是策略 A，实际执行了编辑中的 A'”。

## 8. Factor Code API

Factor 不是一个必须独立存在的产品项目。对用户来说，Factor 首先是一段满足协议的可运行代码，由
Study Product 管理。

不是所有探索结果都应该被称为 Factor。Notebook 中临时生成的列、一次性图表、临时筛选条件和未声明输入的
中间表，只是 exploration artifact。只有当用户显式声明输入、输出、时间语义、参数，并且系统可以记录
代码 hash 和运行依赖时，它才成为 Factor。

Factor Code 的本质是：

```text
Factor = Dataset -> Signal Table 的可冻结代码转换
```

更精确地说，Factor 不是直接依赖 Data Layer 实现，而是依赖 Study 注入的输入契约：

```text
Factor Input = study alias + DataSet Contract + bound Historical Release/Live View capability
Factor Output = Signal Table + Factor Contract + code/param hash
```

Factor 代码只看 `inputs["returns"]` 这样的命名输入，不解析 `dataset_id`，不读取 provider、CSV、source connector 或
source path。Data 怎么来、如何清洗、如何归档，是 Data Layer 内部问题。

分层如下：

| 层级 | 形态 | 是否可复用 | 是否可进 Strategy |
|---|---|---|---|
| Exploration Artifact | notebook 临时列、临时图表、中间 DataFrame | 否 | 否 |
| Candidate Factor | Study 中声明的 factor 代码 | 仅限当前 Study | Candidate/Frozen 后可引用 |
| Frozen Factor | Study Lock 中记录 hash 的 factor | 可由 derived Strategy 复用 | 是 |
| Published Feature | 发布为 Data/Feature Release 的 factor 输出 | 可跨 Study/Strategy 复用 | 取决于质量等级 |

### 8.1 CLI

```bash
kairos study add-factor --workspace us-equity-momentum --name momentum_12_1 --file factors/momentum_12_1.py
kairos study factor-run us-equity-momentum momentum_12_1
kairos study factor-inspect us-equity-momentum momentum_12_1
kairos study publish-factor us-equity-momentum momentum_12_1 --as features.momentum.equity.us.1d
```

### 8.2 Python

目标态 decorator API：

```python
@factor(
    inputs={"returns": "returns"},
    primary_time="decision_date",
    fields=["instrument_id", "decision_date", "momentum_12_1"],
    point_in_time=True,
)
def momentum_12_1(inputs, params, context):
    returns = inputs["returns"]
    return returns.compute_momentum(
        lookback_sessions=params["lookback_sessions"],
        skip_recent_sessions=params["skip_recent_sessions"],
    )
```

当前仓库尚未提供完整 `kairos.study.factor` decorator；在 decorator 落地前，最小无装饰器协议和
外部 metadata 文件可以承担同样的 contract 描述职责。

最小无装饰器协议：

```python
def compute(inputs, params, context):
    """Return a table keyed by instrument_id and decision_date."""
```

### 8.3 Factor Metadata

每个 Factor 必须声明或可推导：

- 输入别名；
- 参数；
- 输出字段；
- 主时间字段；
- 是否 point-in-time；
- 是否允许在 Strategy 中使用；
- 依赖包；
- 代码 hash。

这些元数据是 Factor 和普通探索代码的边界。没有这些元数据，系统可以允许用户继续探索，但不能允许它进入
Study Lock、Strategy Lock 或正式回测。

### 8.4 Factor 限制

Factor Code 不允许：

- 直接联网；
- 直接读未声明路径；
- 直接解析 Data Catalog alias；
- 直接写 Data Product；
- 在 Strategy 阶段改变窗口、参数或过滤规则。

本地 Factor 成熟后，可以发布为共享 Feature Release。发布后它作为 DataSet 被读取，但其来源仍然记录为
Factor Code hash 和输入 Data Release hash。

## 9. Study 与 Strategy 的一致性契约

最重要的产品规则：

```text
Study 和 Strategy 共享同一个数据平台。
Strategy 不能重新发明 Study 已证明的信号。
Strategy 必须引用 Study Lock 中的 factor、data 和参数，或者显式创建新的 Study version。
```

必须检查：

- Strategy 引用的是 Frozen Study，不是 Draft Study；
- Strategy 通过同一个 DataSet API 读取数据，而不是另建数据读取路径；
- Strategy 绑定的 factor 名称存在于 Study Lock；
- Strategy 使用的 factor code hash 与 Study Lock 一致；
- Strategy 使用的 Data Release 与 Study Lock 一致；
- Strategy 没有重新计算一套不同窗口或不同过滤规则的信号；
- Strategy model 只把信号转换为目标持仓或经济意图；
- Strategy run 只执行 Strategy Lock/Snapshot，不执行编辑中的 workspace 文件；
- Backtest/Paper/Live 使用同一个 Strategy Lock；
- 不同 run mode 只能替换 clock、feed、fill/execution gateway 和 safety gate。

如果策略需要改变因子定义，流程是：

```text
修改 Study Product workspace
  -> 重新 run
  -> freeze 新 Study version
  -> Strategy 绑定新 Study version
  -> 重新 backtest/promote
```

不能直接在 Strategy Product workspace 里偷偷改因子。

## 10. 平台提供什么，允许用户扩展什么

### 10.1 平台提供的通用能力

系统应该内置通用能力，而不是给“平台数据”和“用户数据”做两套身份通道：

- Data Product 和 Release 治理；
- Live View 和 freshness 治理；
- Study Product workspace 模板；
- Strategy Product workspace 模板；
- Study/Strategy lock 生成；
- Factor 本地运行协议；
- Strategy model 协议；
- quality、lineage、point-in-time 检查；
- promotion gate；
- backtest/paper/live run mode；
- report 和 manifest 生成。

平台内置 provider、schema、quality profile、download 模板和 write 模板只是一组 util。它们可以降低接入成本，但发布后的
DataSet 仍必须和用户接入数据经过同样的 identity、contract、quality/freshness gate。

### 10.2 用户可以编辑

用户可以编辑：

- Study 的 `study.yaml`；
- Study 的本地 factor 文件；
- Study notebook 和报告；
- Strategy 的 `strategy.yaml`；
- Strategy 的 `model.py`；
- Strategy 的 risk/execution 配置；
- 用户区自定义数据源。

### 10.3 用户扩展方式

推荐用户区：

```text
user/
  data_sources/
  studies/
  strategies/
  factors_shared/
```

扩展 API：

| 扩展点 | 入口 | 必须满足 |
|---|---|---|
| Data Source Connector | `user/data_sources/*.py` | Source 归档、identity、schema、quality、Release |
| Live Source Connector | `user/data_sources/live_*.py` | schema、identity、freshness、Live View、Source 归档 |
| Study Factor | `studies/<id>/factors/*.py` | 输入声明、输出 schema、point-in-time；freeze 时记录 hash |
| Shared Factor | `user/factors_shared/*.py` | 可被多个 Study 引用，freeze 时记录 hash |
| Strategy Model | `strategies/<id>/model.py` | 只读 StrategyContext，输出 EconomicIntent |
| Risk Policy | `strategies/<id>/risk.yaml` | 环境门禁和限额 |
| Execution Policy | `strategies/<id>/execution.yaml` | 可执行假设、订单样式、venue 能力 |

## 11. 推荐最小协议

### 11.1 DataSet 协议

```python
dataset = data.dataset("market.returns.equity.us.1d")
query = dataset.query(start=start, end=end, fields=["instrument_id", "decision_date", "total_return_1d"])
frame = query.collect("pandas")
```

要求：

- 查询必须经过 Catalog 解析；
- backtest/Historical 模式必须解析到不可变 Release；
- paper/live 模式必须解析到同 identity 的 Live View；
- 必须带 `primary_time`；
- `panel` 数据必须带 `entity_key`，`single_series` 数据不需要伪造实体键；
- quality 不足时 fail closed；
- 不暴露物理路径给用户代码。

### 11.2 Study Factor 协议

```python
def compute(inputs, params, context):
    """Return a table keyed by instrument_id and decision_date."""
```

要求：

- `inputs` 只能来自 Study 声明；
- `params` 必须可 JSON 序列化；
- `context` 提供 calendar、window、run_mode；
- 输出必须有主时间字段；
- 不允许联网；
- 不允许读未声明路径。

如果 Factor 声明可用于 paper/live，还必须额外声明：

- 每个输入 alias 对应的 DataSet live capability；
- live 下的计算触发方式：按 bar、按事件、按 session，或仅消费 Published Feature Live View；
- 输出延迟和 freshness 预算；
- replay 与 live runtime 的一致性检查方式。

缺少这些声明时，Factor 可以进入 Study 和 backtest，但不能作为 paper/live Strategy 输入。

### 11.3 Strategy Model 协议

```python
def decide(context):
    """Return StrategyDecision or EconomicIntent."""
```

要求：

- `context` 只包含 Strategy Lock 固定的输入；
- 不直接读取 Data Catalog alias；
- 不直接调用 broker；
- 不做数据下载；
- 不修改 factor 定义；
- 输出经济意图，由 Portfolio/Risk/Execution 后续处理。

## 12. 当前实现差距

当前已有：

- Dataset Product、Release、Catalog；
- Source/Canonical/Reference/Features/Studies 分层；
- `ResearchDataClient`；
- 一键准备受限版美股动量数据；
- Study 启动时冻结美股动量相关输入快照；
- StrategySpec、ExecutionPolicy、Strategy Registry；
- Backtest 和 run mode 的基础模块；
- readiness、quality、lineage 的部分门禁。

当前新增最小纵切：

- `kairos data download <data_key>`：已支持 credential-free 的 `tutorial-sma-data`，走 Data Catalog 和 Release 产物；
- `kairos data write --file ... --as <dataset_id> --contract <contract>`：已支持 CSV 时间序列按 Data Contract 写入 Release；
- `kairos data write --live --connector ... --as <dataset_id> --contract <contract>`：已支持生成带 `live_data_plane` 证据的 Live View manifest；
- `kairos data live-binance/soak-binance`、`BoundedEventChannel`、`AsyncKairosRuntime`：已有 provider WebSocket 到 canonical async channel 的运行基线；
- `kairos study open/add-data/add-factor/inspect/freeze`：已支持一个 Study workspace 绑定多个 Data Release 和 factor code hash；
- `kairos strategy open/bind-factor/set-risk/inspect/freeze`：已支持从 Frozen Study 创建 Strategy workspace，并复用 Study factor hash；
- `kairos run start/inspect/replay/compare`：已支持从 Study 或 Strategy snapshot 创建 Run Workspace 和 Run Manifest；
- 最小四产品 surface 已开始记录 P0 证据链：Data download/write/live manifest 暴露 `contract_hash`、`manifest_hash` 和 `artifact_ref`；
  Study Lock 记录 Data Release evidence 和 factor code hash；Strategy Lock 继承 Frozen Study 的 Data evidence 并执行
  study/factor/data hash 一致性检查；Run Manifest 记录 `input_artifacts`，把本次执行指回 Data Release 和 Frozen Factor 证据；
- `kairos.data.contracts` 已提供最小正式 artifact 模型：`DataSetContractArtifact`、`DataReleaseManifest` 和
  `LiveViewManifest`；四产品 surface 的 Data write/live 和 release evidence 已使用这些模型生成稳定 hash/ref；
- `publish_release` 主发布路径已写出 `data_release_manifest.json`，并在 `release.json` 中记录 `contract_hash`、
  `data_release_manifest_hash` 和 `artifact_ref`；四产品 Data evidence 优先读取正式 release manifest；
- columnar publishing 经由 `publish_release` 产出正式 release manifest；MarketReplayDataset metadata 补全路径已把
  `data_release_manifest.json` 纳入必备元数据，并在 `release.json` 中记录 manifest hash/ref；
- `DatasetQualityService` 已开始区分 gate 与 diagnostic：artifact/contract/时间语义类检查仍可 fail closed，
  coverage、history length、source receipt、streaming execution 等本地诊断不再默认阻塞 `assessment.passed`；
- `DataPreparationService` 已开始通过 `DataPromotionPolicyProfile` / `DataPromotionPolicyResult` 显式判断请求的
  Q2/Q3/Q4 晋级目标：quality report 提供 gate/diagnostic 事实，promotion policy 可按用途选择哪些 diagnostic
  需要升级为用途门禁；
- `DataProductContract.capabilities["promotion_policy"]` 已可声明产品默认 promotion policy，DataPreparation 会优先使用
  产品契约中的 Q2/Q3/Q4 policy profile，再回退到默认宽松策略；
- 内置 `research-default` / `backtest-default` / `production-default` promotion policy profile 已有只读 registry，
  产品契约可以直接引用内置 profile 名称；默认 profile 不把本地 diagnostic 自动升级为硬门禁；
- `LiveViewFreshnessPolicy` / `LiveViewFreshnessGateResult` 已建立最小 freshness gate 模型，区分 Live View
  已配置 freshness 与 paper/live 所需的 healthy freshness；
- 四产品 surface 的 `run start --mode paper/live` 已接入最小 freshness gate：Strategy data input 必须存在
  匹配 DataSet contract 的 healthy Live View，Run Manifest 会记录 `freshness_gates`；
- paper/live freshness gate 已要求 Live View manifest 携带 channel diagnostics；drop、overflow 和 sequence gap
  会 fail closed，reconnect/conflation 作为显式证据记录，不再静默丢失；
- `kairos data soak-binance --live-view-manifest ...` 已可把审计后的 soak 结果写回 Live View manifest，自动更新
  `freshness_status`、`channel_diagnostics` 和 `freshness_evidence`；drop、overflow 或 sequence gap 会写成
  `unhealthy`；
- Live View manifest 已有统一读写/查找 API：`live_view_manifest_path`、`load_live_view_manifest`、
  `write_live_view_manifest`、`find_live_view_manifest`；四产品 surface 和 freshness 写回路径共享同一套解析逻辑；
- Python API：`DataProductApi`、`StudyProductApi`、`StrategyProductApi`、`RunProductApi` 已提供和 CLI 同源的最小调用面；
- 完整示例：`examples/four_product_user_path.sh`；
- 自动化验收：`tests/test_four_product_surface.py`。

目标态剩余缺口：

1. DataSet Contract、Data Release Manifest、Live View Manifest 已建立最小正式模型，并接入四产品 surface、`publish_release`、columnar publishing 和 MarketReplayDataset metadata 补全路径；质量报告已开始区分 gate/diagnostic，DataPreparation 已有可配置 promotion policy profile，DataProductContract capabilities 已可声明产品默认 policy，内置 Q2/Q3/Q4 profile registry 已建立，freshness gate 已有最小 policy/result 模型并接入四产品 paper/live run 边界，channel diagnostics 已进入该 gate，`soak-binance` 可显式写回 Live View health/diagnostics，Live View manifest 读写/查找已有共享 API；剩余缺口是通用 freshness monitor 和实际实时 runtime 还没有自动持续写回 Live View health/diagnostics；
2. 旧 `StudyWorkspace` 仍以单 `input_release_id` 为主模型，新的 Study Product workspace 还没有替换旧模型；
3. Study Product 的 Draft/Frozen 生命周期已最小落地，但状态机、质量门禁和目录结构还没有统一到正式模型；
4. 本地 factor 已记录 code hash，但依赖、参数、输出 schema 和 point-in-time 检查还没有正式约定；
5. Strategy Product workspace 已最小落地，但 `model.py`、risk/execution policy 和 promotion evidence 还不完整；
6. Strategy 已要求从 Frozen Study 打开，但还没有完整自动检查 Data Release hash 与 Factor Output 语义一致；
7. Study factor 与 Strategy model 的语义一致性还没有自动检查；
8. Data Product 已有最小 `data download` 和 `data write` 入口，但 download spec、YAML contract、quality report 还没有完整实现；
9. 实时数据流接入已能生成 Live View manifest，并已有 provider WebSocket/canonical channel 运行基线；四产品 paper/live run 已要求 healthy Live View freshness 和 channel diagnostics，`soak-binance` 已能把审计结果写回指定 Live View manifest，但通用 freshness monitor、订阅 API、持续诊断写回和历史回放归档还没有完整统一到 DataSet identity；
10. Run Product 已有最小 `run start/inspect/replay/compare` API，但还没有接入真实 InputTable、clock、feed 和 execution gateway；
11. `data.dataset(name)`、`study.data(name)`、`study.factor(name)`、`strategy.decide(context)` 和 `run.start(...)` 这种用户 API 还没有完成；
12. Factor Code decorator/metadata/hash 协议还没有完成；
13. Exploratory/Study/Registered/Production Data 的分层门禁还没有统一实现；
14. Exploration Artifact 与 Candidate/Frozen Factor 的边界还没有模型化；
15. 用户区自定义历史/实时数据源还没有治理入口。

## 13. 推荐实施顺序

### 阶段 0：分层契约基线

- 定义 DataSet Contract、Factor Contract、Study Contract、Strategy Contract 和 Runtime Contract；
- 区分 Contract、Artifact Manifest 和 Lock；
- 确保上层 API 只消费命名产物和 contract hash；
- 禁止 Factor/Study/Strategy 暴露 provider、CSV、connector、source archive 和物理路径；
- 为所有正式 run 输出层间产物引用链。

### 阶段 A：收敛 Data Product

- 增加 `kairos data download <data_key>`；
- 增加 `kairos data register-download --key <data_key> --spec <spec.yaml>`；
- 增加 `kairos data write --file ... --as <dataset_id> --contract <contract>`；
- 增加 `kairos data write --live --connector ... --as <dataset_id> --contract <contract>`；
- 明确 Data Download Spec 和 Data Write Contract 的边界；
- 建立 Historical Release、Live View、quality/freshness report 和 manifest。

### 阶段 B：收敛 Study Product

- `StudyWorkspace` 支持多个数据输入；
- 增加 Draft/Candidate/Frozen/Archived 状态；
- 定义工作区目录结构；
- 支持 `study.data(name)`；
- 冻结时生成 `study.lock.json`。

### 阶段 C：支持 Study 本地因子

- 定义 `studies/<study_id>/factors/`；
- 增加 `kairos study add-factor`；
- 增加 Factor Code decorator 或 metadata 协议；
- freeze 时记录本地 factor code hash、参数、依赖和输出 schema；
- 区分 notebook exploration artifact、candidate factor、frozen factor 和 published feature；
- 支持 `study.factor(name)`；
- 检查 point-in-time、lineage 和 schema。

### 阶段 D：推出 Strategy Product

- 定义 `strategies/<strategy_id>/`；
- 增加 `kairos strategy open --from-study`；
- 增加 `strategy.yaml`、`model.py`、risk/execution 配置；
- 生成 `strategy.lock.json`；
- 强制 Strategy 绑定 Frozen Study。

### 阶段 E：推出 Run Product

- 增加 `kairos run start --snapshot <strategy_id>@<version> --mode <mode>`；
- 增加 `kairos run start --study <study_id> --mode research`；
- 增加 `kairos run inspect/replay/compare`；
- 生成独立 Run Workspace；
- `run start` 只接受明确 target，运行前比较 workspace hash 与 target hash；
- Run Manifest 记录 snapshot、输入产物、runtime contract、环境和输出 hash。

### 阶段 F：一致性门禁

- Study 和 Strategy 共用同一个 DataSet API；
- 检查 Strategy factor hash 与 Study Lock 一致；
- 检查 Data Release hash 与 Study Lock 一致；
- 检查 Strategy model 不重新定义 factor；
- Run Product 只接收 Study target 或 Strategy Lock/Snapshot；
- Paper/Live 只接收已晋级 Strategy Lock。

### 阶段 G：用户扩展

- 支持用户区 historical/live data source connector；
- 支持共享 factor；
- 支持自定义 risk/execution policy；
- 所有扩展都必须进入 lock/manifest。

## 14. 目标能力摘要

目标状态下，用户应该能做到：

- 不拼路径完成数据准备；
- 普通用户用 data download key，一键下载内置/已注册数据；维护者用 spec 注册或更新这个 key；
- 用户可以通过 Data Product 的 write 入口，把外部历史文件或实时流写入 DataSet Contract；
- 上层代码只消费命名产物，不依赖下层实现；
- 注册 Data 后可以复现，Q3/Q4 以后可以进入回测和生产链路；
- 打开一个 Study Product 开始研究；
- 在 Study 中动态增加数据和本地因子；
- 不改 package 就能编写用户区因子；
- Data 通过 DataSet API 使用，Factor 通过 Code API 编写；
- 临时探索结果不会被误认为正式 Factor；
- 冻结 Study 后得到可复现研究证据；
- 从 Frozen Study 创建 Strategy Product；
- Strategy 复用 Study 的 factor 和 data，不产生语义漂移；
- Strategy 是代码项目，但不直接负责执行；
- Run Product 执行 Study research target 或 Strategy snapshot，每次执行都有独立 Run Workspace；
- 同一个 Strategy Lock 可以进入 backtest、paper 和 live 晋级链路；
- 报告能解释每一列数据、每个信号、每个策略决策来自哪个 Release 或代码 hash；
- 数据或策略质量不够时，系统给出明确下一步。

最终产品主线：

```text
Data Product
  -> Study Product
  -> Study Lock
  -> Strategy Product
  -> Strategy Lock
  -> Run Product
  -> Run Manifest / Backtest / Paper / Live Outputs
```

## 15. 完整场景：美股动量从研究到策略

本场景用于验收用户入口、数据平面、Study 工作区、Factor Code、Strategy 工作区和一致性门禁是否跑通。
场景目标不是证明策略盈利，而是证明研究到策略不会发生数据或因子语义漂移。

### 15.1 场景边界

| 项目 | 约定 |
|---|---|
| 研究主题 | 美股横截面动量 |
| 数据范围 | 可先使用 bounded configured products，正式 Q3 再扩展 full-market |
| 信号 | `momentum_12_1` |
| 数据输入 | 历史 returns、universe、liquidity；paper/live 需要同 identity 的实时 returns/quotes/universe view |
| Study 输出 | factor report、forward return report、study lock |
| Strategy 输出 | long-only top decile target intent、strategy lock |
| Run 输出 | backtest run workspace、run manifest、run report |
| 非目标 | 不在 Strategy 中重新计算另一套 momentum |

### 15.2 准备数据

用户命令：

```bash
kairos data download us-equity-momentum-data
```

`us-equity-momentum-data` 是一个 data download key。它背后的定义可以由平台内置，也可以由用户注册：

```bash
kairos data register-download --key us-equity-momentum-data --spec us-equity-momentum-data.yaml
```

最小 download spec：

```yaml
kind: data.download
key: us-equity-momentum-data
scope:
  asset_class: equity
  region: us
  provider: massive
  frequency: 1d
  start: 2020-01-01T00:00:00-05:00
  end: 2026-07-01T00:00:00-04:00
products:
  - market.returns.equity.us.1d
  - market.universe.equity.us.1d
  - features.liquidity.equity.us.1d
quality:
  minimum: Q2
mode:
  acquire_missing: true
```

期望产物：

```text
data/catalog.json
data/releases/<release_id>
data/quality/<release_id>.json
data/downloads/us-equity-momentum-data/report.json
```

必须满足：

- 每个注册 Data Release 有 `release_id`、`content_hash`、schema、primary_time、grain；
- `single_series` 输入不需要 `entity_key`，但必须声明 join_policy；
- Study/Strategy 后续只能通过 DataSet API 访问这些 release；
- 如果只能达到 bounded scope，report 必须明确 `ready_for_research=true`、`ready_for_backtest=false` 或同等诊断。

如果用户要进入 paper/live，还需要接入同一 DataSet identity 的 Live View：

```bash
kairos data write --live \
  --connector user_data_sources/live_returns.py \
  --as market.returns.equity.us.1d \
  --contract returns.contract.yaml
```

必须满足：

- Live View 使用和 Historical Release 相同的 `dataset_id`；
- schema、`primary_time`、grain、identity/reference 语义兼容；
- 通过 freshness monitor 和 Q4 production gate；
- live records 同步归档为 Source，以便之后回放、对账和复盘。

### 15.3 打开 Study Product Workspace

用户命令：

```bash
kairos data write --file analyst_estimates.csv --as reference.estimates.equity.us --contract estimates.contract.yaml

kairos study open us-equity-momentum
kairos study add-data --workspace us-equity-momentum --name returns --dataset market.returns.equity.us.1d
kairos study add-data --workspace us-equity-momentum --name universe --dataset market.universe.equity.us.1d
kairos study add-data --workspace us-equity-momentum --name liquidity --dataset features.liquidity.equity.us.1d
kairos study add-data --workspace us-equity-momentum --name estimates --dataset reference.estimates.equity.us
```

期望目录：

```text
studies/us-equity-momentum/
  study.yaml
  factors/
  notebooks/
  runs/
  locks/
```

`study.yaml` 至少包含：

```yaml
kind: study.workspace
id: us-equity-momentum
window:
  start: 2020-01-01T00:00:00-05:00
  end: 2026-07-01T00:00:00-04:00
data:
  returns:
    product: market.returns.equity.us.1d
    quality: Q2
  universe:
    product: market.universe.equity.us.1d
    quality: Q2
  liquidity:
    product: features.liquidity.equity.us.1d
    quality: Q2
  estimates:
    product: reference.estimates.equity.us
    release_id: estimates_...
    quality: Q2
```

`source`、`contract_hash` 和 `content_hash` 保存在 Data Release Manifest 中，Study 只引用命名数据产物。

必须满足：

- Study 可以继续处于 Draft；
- Draft Study 可以新增数据输入；
- 数据别名稳定，后续 factor 和 strategy 只引用别名，不拼物理路径。
- 外部文件必须先通过 Data Product 的 `data write` 生成 Data Release；失败时只能作为 Exploratory Data，不能进入 Frozen Study。
- Study spec 不记录 CSV 路径、provider、source connector 或 source archive 作为正式依赖。

### 15.4 编写 Candidate Factor

用户新增文件：

```text
studies/us-equity-momentum/factors/momentum_12_1.py
```

示例协议：

```python
def compute(inputs, params, context):
    """Return a table keyed by instrument_id and decision_date."""
    returns = inputs["returns"]
    return returns.momentum(
        lookback_sessions=params["lookback_sessions"],
        skip_recent_sessions=params["skip_recent_sessions"],
    )
```

用户命令：

```bash
kairos study add-factor --workspace us-equity-momentum --name momentum_12_1 --file factors/momentum_12_1.py
kairos study factor-run us-equity-momentum momentum_12_1
```

`study.yaml` 增加：

```yaml
factors:
  momentum_12_1:
    path: factors/momentum_12_1.py
    inputs:
      returns: returns
    output:
      primary_time: decision_date
      fields: [instrument_id, decision_date, momentum_12_1]
    point_in_time: true
    parameters:
      lookback_sessions: 252
      skip_recent_sessions: 21
```

必须满足：

- `momentum_12_1` 是 Candidate Factor；
- notebook 中未声明输入/输出/schema 的临时列仍是 Exploration Artifact，不能被 Strategy 绑定；
- `factor-run` 输出 factor profile，至少包含行数、字段、主时间、空值、point-in-time 检查结果；
- Factor 不允许联网，不允许读取 Study 未声明路径。

### 15.5 冻结 Study

用户命令：

```bash
kairos run start --study us-equity-momentum --mode research
kairos study freeze us-equity-momentum --version 1.0.0
```

期望产物：

```text
runs/us-equity-momentum/<run_id>/manifest.json
studies/us-equity-momentum/locks/1.0.0/study.lock.json
```

`study.lock.json` 必须包含：

```yaml
study_id: us-equity-momentum
version: 1.0.0
data:
  returns:
    release_id: returns_...
    content_hash: sha256:...
factors:
  momentum_12_1:
    code_hash: sha256:...
    inputs:
      returns:
        release_id: returns_...
    parameters:
      lookback_sessions: 252
      skip_recent_sessions: 21
    output_schema_hash: sha256:...
quality:
  minimum: Q2
  point_in_time: passed
```

必须满足：

- Study Lock 不保存浮动 alias 作为唯一证据；
- Study Lock 中的 factor code hash、data release hash 和参数不可缺失；
- 如果数据质量不足，freeze 失败并输出缺口诊断；
- Frozen Study 不能再隐式联网、不能重新解析 latest。

### 15.6 创建 Strategy Product Workspace 并复用 Study

用户命令：

```bash
kairos strategy open us-equity-momentum-long-only --from-study us-equity-momentum@1.0.0
kairos strategy bind-factor --workspace us-equity-momentum-long-only --name primary --study-factor momentum_12_1
kairos strategy freeze us-equity-momentum-long-only --version 1.0.0
```

期望目录：

```text
strategies/us-equity-momentum-long-only/
  strategy.yaml
  model.py
  risk.yaml
  execution.yaml
  locks/
```

`strategy.yaml` 至少包含：

```yaml
kind: strategy.workspace
id: us-equity-momentum-long-only
derived_from:
  study: us-equity-momentum
  version: 1.0.0
inputs:
  signal:
    from_study_factor: momentum_12_1
  universe:
    from_study_data: universe
portfolio:
  construction: long_only_top_decile
run_modes:
  allowed: [backtest, historical-simulation, paper]
```

必须满足：

- Strategy 只能绑定 Frozen Study；
- Strategy 通过 DataSet API 读取 Study Lock 中的数据；
- Strategy 在 backtest 中读取 Historical Release，在 paper/live 中读取同 DataSet identity 的 Live View；
- Strategy 使用的 `momentum_12_1` code hash 必须等于 Study Lock 中的 hash；
- Strategy 不允许在 `model.py` 里重新计算另一套 momentum；
- `strategy.lock.json` 记录 Study Lock hash、factor hash、Data Release hash、model hash、risk/execution hash。

### 15.7 通过 Run Product 执行 Strategy Snapshot

用户命令：

```bash
kairos run start --snapshot us-equity-momentum-long-only@1.0.0 --mode backtest
```

期望目录：

```text
runs/us-equity-momentum-long-only/<run_id>/
  snapshot.json
  manifest.json
  inputs/
  decisions/
  intents/
  reports/
```

必须满足：

- Run Product 创建独立 run workspace；
- Run Product 只运行 `us-equity-momentum-long-only@1.0.0` snapshot；
- Run Manifest 记录 Strategy Lock hash、Data Release/Live View hash、runtime contract 和输出 hash；
- Run Product 不允许读取编辑中的 Strategy Product workspace 文件；
- backtest、paper、live 都通过 Run Product 执行，只改变 runtime contract。

### 15.8 策略跑不起来时的正确行为

如果 Strategy 在 backtest、paper 或 live 中跑不起来，系统不能允许用户在 Strategy 中偷偷改因子或临时补数据。
Run Product 必须输出数据平面或运行契约缺口诊断。

常见诊断：

| 缺口 | 系统提示 | 用户下一步 |
|---|---|---|
| 缺历史 Data Release | `missing_history_release` | 运行 `kairos data download` 或补 Data Source |
| 缺实时数据映射 | `missing_live_dataset` | 接入 live source connector 或注册 production data |
| 缺字段 | `missing_dataset_fields` | 扩展 Data Product schema 或调整 Study factor |
| 缺 identity/reference | `missing_identity_reference` | 准备 reference identity release |
| 缺公司行为 | `missing_corporate_actions` | 准备 corporate action release |
| 数据质量不足 | `quality_level_too_low` | 修复数据质量并晋级 Q3/Q4 |
| Factor 无法实时计算 | `factor_runtime_missing_input` | 补实时输入或明确该 Strategy 不支持 live |

必须满足：

- 诊断指向 Data Source、Data Product、Feature runtime 或 quality gate；
- 不建议用户在 Strategy 内复制/修改 Study factor；
- 如果缺口未解决，Strategy 不能晋级 paper/live。

## 16. 验收标准

### 16.1 文档验收

| 项 | 验收标准 |
|---|---|
| 产品边界 | Data Product、Study Product、Strategy Product、Run Product 没有重叠职责 |
| 产品定义 | 每个产品都明确 CLI、API、Contract、开放扩展点和产物 |
| 分层边界 | 上下层只通过 contract、manifest、lock 和命名产物沟通 |
| Data 定义 | DataSet 是数据集；注册后是平台资产；探索数据和注册数据分层清楚 |
| Factor 定义 | Exploration Artifact、Candidate Factor、Frozen Factor、Published Feature 边界清楚 |
| Study 定义 | Study 是独立研究产品，允许 Draft 动态增加数据和 factor |
| Strategy 定义 | Strategy 从 Frozen Study 派生，不能重新定义 Study factor |
| Run 定义 | Run 是独立执行产品，只执行 Strategy Snapshot 并产出 manifest/output |
| 数据平面 | Study 和 Strategy 共享 DataSet API、Data Release/Live View 解析和质量门禁 |
| 统一身份通道 | 平台 util 数据和用户接入数据都经过同一个 DataSet identity/contract/gate |
| 历史与实时 | Historical View 和 Live View 共 `dataset_id`、schema 语义、primary_time、grain 和 identity |
| 策略输入 | Strategy 通过统一 `InputTable` 消费 Data 和 Factor |
| 晋级路径 | Q2/Q3/Q4 对 research/backtest/paper-live 的边界清楚 |
| 场景完整性 | 至少包含 Data download/write、Study、Factor、Study Lock、Strategy、Strategy Lock、Run、缺口诊断 |
| 冲突检查 | 不存在“随意探索列也是 Factor”或“Strategy 可重写因子”的描述 |

### 16.2 功能验收

| 阶段 | 必须验证 | 失败时行为 |
|---|---|---|
| Data Download | 下载内置/已注册数据并生成 Release、quality report、download report | 输出缺口，不创建可研究 Release |
| Contract Boundary | Contract 不包含 provider、connector、CSV 路径或存储路径；manifest 记录具体产物证据 | 混入实现细节时拒绝 freeze/promote |
| Data Write Historical | `data write --file` 必须校验 contract 并生成 Release | 无 contract 或 schema 不匹配时留在 Draft exploration，不允许 freeze |
| Data Write Live | `data write --live` 必须校验 contract、identity、freshness 并生成 Live View | 缺 schema/identity/freshness 时拒绝 paper/live |
| Async Live Plane | WebSocket/实时 connector 只能产出 Live View、canonical event、channel metrics 和 capture evidence | Strategy 直接订阅 provider WebSocket 或静默丢 event 时拒绝 paper/live |
| Study Draft | 可 add-data、add-factor、run | 未声明输入时 factor-run fail closed |
| Factor Boundary | Exploration Artifact 不能被 strategy bind | 提示先 `study add-factor` |
| Study Freeze | 生成 `study.lock.json`，含 data/factor hash | 冻结快照缺 hash、schema、quality 时拒绝冻结 |
| Strategy Open | 只能从 Frozen Study 创建 | Draft Study 拒绝 |
| Strategy Bind | factor hash 与 Study Lock 一致 | hash 不一致拒绝绑定 |
| Run Start | 使用 Strategy snapshot、Run Workspace 和统一 InputTable | 缺数据输出 data plane diagnosis |
| Paper/Live Gate | 只接受 Q4/production data 和已晋级 Strategy Lock | 缺实时数据或 freshness 监控时拒绝晋级 |

### 16.3 完整场景验收

完整场景通过时必须同时具备：

- `data preparation report`；
- 至少三个 Data Release：returns、universe、liquidity；
- 可选 CSV 输入若参与 Study，必须有 contract、Data Release 和 content hash；
- `studies/us-equity-momentum/study.yaml`；
- `studies/us-equity-momentum/factors/momentum_12_1.py`；
- `studies/us-equity-momentum/locks/1.0.0/study.lock.json`；
- `strategies/us-equity-momentum-long-only/strategy.yaml`；
- `strategies/us-equity-momentum-long-only/locks/1.0.0/strategy.lock.json`；
- `runs/us-equity-momentum-long-only/<run_id>/snapshot.json`；
- `runs/us-equity-momentum-long-only/<run_id>/manifest.json`；
- Strategy Lock 中的 factor hash 等于 Study Lock 中的 factor hash；
- Strategy Lock 中的 Data Release hash 等于 Study Lock 中对应 Data Release hash；
- Strategy context 中 Data 和 Factor 都以 `InputTable` 暴露；
- Run manifest 引用 Strategy snapshot，而不是引用 Draft Study、编辑中的 workspace 或浮动 alias；
- 若运行 paper/live，Live View 与 Historical Release 必须共 DataSet identity，并记录 freshness 状态；
- 人为删除 live data mapping 后，paper/live 晋级失败并输出 `missing_live_dataset` 或等价诊断。

### 16.4 不允许通过的情况

以下情况必须失败：

- Strategy 直接读取 `canonical/...`、`reference/...` 或 notebook 临时文件；
- Study Lock 或 Strategy Lock 依赖 CSV 路径、provider 名称、connector 文件或 source archive 路径作为正式输入；
- Factor 直接解析 Data Catalog alias 或 DataSet 物理路径；
- Strategy 重新实现 `momentum_12_1`；
- Run Product 直接执行编辑中的 `model.py` 而不是 Strategy Snapshot；
- Strategy 使用的 factor 参数与 Study Lock 不一致；
- Strategy 读取的 Data Release hash 与 Study Lock 不一致；
- 未声明输入的 notebook 列被绑定为 Factor；
- Q2 数据直接进入正式 backtest 或 live；
- live 缺少 freshness/monitoring/fail-closed 仍允许晋级；
- 平台内置 connector 绕过 DataSet contract、quality 或 freshness gate；
- 用户 connector 走一条和平台 connector 不同的二等数据路径；
- Strategy 直接订阅 live connector，而不是通过 DataSet Live View；
- 缺数据时系统建议用户在 Strategy 里临时补逻辑。
