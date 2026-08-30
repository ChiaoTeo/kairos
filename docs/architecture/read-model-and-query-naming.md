# 查询、当前视图与派生数据命名

本文规定 Kairos 读取侧的当前词汇，并盘点仓库中被 `projection` 混称的对象。目标不是把
`projection` 机械替换成另一个通用词，而是让名称直接表达对象究竟是查询、当前视图、
快照、本地依赖状态、目录、映射还是计算结果。

## 结论

`projection` 不再作为 Kairos 的通用架构名词。业务代码、跨进程 contract、CLI 和 SDK
必须优先使用以下具体名称：

| 对象 | 统一名称 | 含义 |
| --- | --- | --- |
| 请求后由 owner 在线处理的有界读取 | `Query` | 有参数、有明确结果边界，可能包含授权或即时计算 |
| owner 发布的最新业务状态 | `CurrentView` / `LatestView` | 有明确 key、完整性、版本、水位和新鲜度语义 |
| 一次读取获得的不可变结果 | `Snapshot` | 强调一次读取及其一致性边界，不表示存储机制 |
| 消费者定时刷新并持有的外部事实 | `DependencyState` | 消费方私有状态，有 ready/stale 规则，不是 owner contract |
| 可过滤、分页和关联读取的持久目录 | `Catalog` | 当前实现可由 SQLite 支撑，但业务名称不暴露存储选择 |
| 持久历史读取 | `HistoryQuery` / `AuditQuery` | 有范围、分页和截断语义，不能伪装成完整当前视图 |
| 边界模型转换 | `map_*` / `decode_*` / `from_*` | 明确源和目标，不建立新的“投影层” |
| 确定性分析 | `Calculation` / 具体业务结果 | 例如 Greeks 计算，不称为投影 |
| 数据帧选择列 | `column projection` | 关系代数中的精确术语，可以保留在局部分析代码中 |

这项规则不影响 `project_root`、`project_id`、`project_template` 等表示“项目”的英文标识；
它们与本文讨论的读模型投影无关。

## 先区分读取语义，再选择机制

RPC、Aeron 和 indexed current-view storage 是交付机制，不是三种业务数据类型。同一业务
事实可以在 owner 进程内直接读取，再发布为 LMDB current view；不能因此同时把它叫
“直接查询数据”和“LMDB 数据”。应先确定读取语义，再选择机制。

| 读取语义 | 默认机制 | 适用条件 | 不适用条件 |
| --- | --- | --- | --- |
| 命令、健康、状态、路由、即时能力判断 | JSON-RPC request/control | owner 必须在线参与；结果有界；调用方需要明确成功或失败 | 大型当前状态镜像、高频轮询、完整历史 |
| 运行中的最新业务状态 | LMDB `CurrentView` / `LatestView` | 同机、稳定业务 key、单实体或有界范围读取 | 任意复杂过滤、跨机、无限历史 |
| Reference 目录与生命周期检索 | SQLite-backed `Catalog` / `HistoryQuery` | 持久、可过滤、分页、事务内一致读取 | 复制每个运行模块的私有数据库供外部读取 |
| 同一 main package 内部读取 | Application 方法 | 调用者属于 owner 包；无需跨进程 | 其他业务包绕过 contract 调用 owner application |
| 低延迟变化通知 | Aeron notification | best-effort 实时触发、同 incarnation 去重和丢失诊断 | 替代当前状态、durable audit 或任意历史查询 |

选择顺序如下：

1. 需要 owner 在线进行授权、计算或控制吗？使用请求式 `Query` 或 command。
2. 读取的是按稳定业务 key 发布的最新完整状态吗？使用 LMDB `CurrentView`。
3. 需要过滤、分页、关联读取或完整历史吗？使用 owner contract 提供的持久查询。
4. 调用者是否与 owner 位于同一个 main Cargo package？是则直接进入 Application；否则
   必须进入 owner contract。

### 当前模块的机制边界

| 模块 | Request/control | indexed 当前视图目标 | 持久查询 | 事件流 |
| --- | --- | --- | --- | --- |
| Account | health、refresh、reconcile、simulation control | account、segment、balance、position、observed-order families | 无公共数据库查询 | Account events |
| Market | health、data routes、subscription control | keyed latest observations/books/freshness plus indexed retained bars | 历史数据集由明确的数据读取边界负责，不开放 Actor 存储 | Market events |
| Execution | health、routes、intent/order control、reconcile | keyed orders、intents、runs、commitments、reservations、unknown remotes | typed durable `order_audit` query | Execution events |
| Risk | health、authorization、reservation/circuit control | keyed policies、usage、reservations、circuits | 无公共数据库查询 | Risk events |
| Capital | health、availability query、funding control | keyed objectives、demands、routes、plans、reservations、operations、alerts | 无公共数据库查询 | Capital events |
| Reference | health、source control、refresh、mutation | 无；SQLite 是唯一当前事实读取面 | catalog、market/instrument 查询；不开放持久化 event history | Reference events |

Reference 不发布第二份 current-view store 是有意的架构选择，见
[`reference/tests/architecture.rs`](../../crates/modules/reference/tests/architecture.rs)。其他
模块也不得因为 Reference 暴露 contract-owned SQLite reader，就把自己的私有持久表变成
跨业务 API。Current view 的物理存储与迁移规则见
[`current-view-storage.md`](current-view-storage.md)。生产 current view 只有 owner-scoped LMDB
indexed 路径，不存在第二套 snapshot transport、fallback 或兼容入口。

## 全仓库命名盘点

本盘点覆盖 Rust 模块、platform、Python SDK、schemas、scripts、tests 和现有文档；生成代码
和 `project_*` 同形词不计入。测试中的同名 fake、断言和错误文本随生产 API 改名，不形成
独立架构概念。

### Reference：Catalog、Snapshot 和同步批次被混称

Reference 是当前最集中的误用点。

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `contract/src/transport/projection.rs` | 一个 `Market` 别名和一个由 Execution 构造的水位 DTO | 删除文件；直接使用 `Market`；水位使用 `ReferenceWatermark` 或消费者私有状态 |
| `ReferenceMarket = Market` | 无转换、无新约束的别名 | 删除 `ReferenceMarket`，统一使用 contract-owned `Market` |
| `ReferenceHealth` | Execution 私有 Reference 依赖就绪信息；`status` 当前固定为 `ready` | 从 Reference contract 删除；在 Execution 用 `ReferenceDependencyState` 的存在性、时间和水位表达 |
| `ReferenceProjectionSnapshot` | 一次事务读取并按消费者裁剪的 Reference 目录快照 | 拆为窄的 `MarketReferenceSnapshot`、`ExecutionReferenceSnapshot`、`AccountReferenceSnapshot`；若暂不拆型，先改 `ReferenceCatalogSnapshot` |
| `market_projection()`、`execution_projection()`、`account_projection()` | 从完整目录筛选消费者所需记录 | 改为 `for_market()`、`for_execution()`、`for_account()`，或直接在各窄 snapshot 构造器中完成 |
| SQLite `ReferenceProjection` | market 查询结果及其关联 instruments、水位 | `ReferenceMarketCatalogPage` 或 `MarketCatalogResult` |
| `ReferenceSqliteReader::projection()` | 在一个 SQLite transaction 中读取 market 及关联 instrument | 公共语义改为 `market_catalog()`；SQLite 留在 `catalog/sqlite.rs` 的实现名中 |
| `transport/sqlite.rs` | contract-owned Reference 持久目录读取面 | 移至 `catalog/sqlite.rs`；公共 client 暴露 `catalog()`、`market(s)`、`instrument(s)` 等语义 API |
| `PROVIDER_PROJECTION_VERSION`、`prepare_projection()` | provider 增量扫描的规范化格式版本和 staging 重置 | `PROVIDER_SCAN_FORMAT_VERSION`、`prepare_scan()` |
| `reference_provider_projection_version` | provider 未完成扫描所用的内部格式版本表 | 迁移为 `reference_provider_scan_format`；数据库升级必须保留既有状态 |
| 旧 provider scan reset 日志名 | 扫描格式变化后重置未完成 staging 的日志事件 | 全量迁移为 `reference_provider_scan_reset`；不再识别旧事件 |
| 旧 scan version 日志字段 | provider scan format 版本 | 全量迁移为 `scan_format_version`；不再识别旧字段 |
| `current_projection_is_empty()` | 当前 canonical catalog 是否为空 | `current_catalog_is_empty()` |
| “provider projections” | 每个 provider 规范化后的 `ProviderCatalog` | 直接称 `provider catalogs` |
| “health projection” | application 将 runtime status 映射为 control response | `health response mapping` 或 `contract health mapping` |

相关实现集中在
[`catalog/model.rs`](../../crates/modules/reference/contract/src/catalog/model.rs)、
[`catalog/sqlite.rs`](../../crates/modules/reference/contract/src/catalog/sqlite.rs) 和
[`provider_sync.rs`](../../crates/modules/reference/src/services/storage/provider_sync.rs)。

需要特别处理公共 API 的无效状态：当前同一个 `ReferenceProjectionSnapshot` 通过
`Default` 把无关集合留空。调用方无法区分“消费者不需要该集合”和“数据实际为空”。窄
snapshot 类型能消除这种无效状态，比只改名字更重要。

### Execution：当前 indexed-view 读取和依赖缓存被混称

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `services/dependencies/projection/mod.rs` | 后台轮询 Account/Market/Risk，并保存 Reference snapshot；检查 ready/stale | `services/dependencies/state/mod.rs` 或 `current.rs` |
| `AccountProjection` | Execution 私有的 Account 依赖状态 | `AccountDependencyState` |
| `ProjectedBalance`、`ProjectedPosition` | 从 Account indexed current view 解码后供 admission 使用的窄事实 | `AvailableBalance` / `AccountBalanceFact`、`AccountPositionFact` |
| `MarketProjection` | quote indexed view 的 event sequence 和提交时间 | `MarketDependencyState` |
| `ReferenceProjection` | Reference markets、水位和刷新时间 | `ReferenceDependencyState` |
| `RiskProjection` | Risk health | `RiskDependencyState` |
| `DependencyProjection` | 四类依赖状态的容器 | `DependencyState` |
| `DependencyProjectionRuntime` | 拥有刷新线程和共享依赖状态的服务 | `DependencyStateRuntime` 或更具体的 `DependencyRefreshRuntime` |
| `read_account_projection()` | 读取并校验 Account indexed current families | `read_account_dependency_state()` |
| `project_reference_snapshot()` | 把 Reference snapshot 变成 Execution 私有依赖状态 | `reference_dependency_state()` 或 `ReferenceDependencyState::from_snapshot()` |
| `read_orders()` in connected facade | 从 Execution indexed current view 映射 CLI result | 保持 owner-owned typed current-view read |
| `with_backtest_*_without_projection` | backtest 是否允许缺少依赖事实 | `allow_backtest_without_reference_state`、`allow_backtest_without_account_state` |
| “bounded worker projection” in Risk adapter | 后台 worker 持有的 Risk 依赖状态 | `bounded worker state` |

当前依赖实现见
[`services/dependencies/state/mod.rs`](../../crates/modules/execution/src/services/dependencies/state/mod.rs)，
connected indexed current-view 读取见
[`application/connected.rs`](../../crates/modules/execution/src/application/connected.rs)。

Execution 已提供 owner-owned durable `order_audit` query，并已硬切到 indexed current
view。旧聚合快照的截断 fill/event windows 与 `recent-*` 读取已经删除，没有复制进 LMDB；
current storage 不承担无限历史。

### Market：Application 入口、订单簿命令和 indexed reader 被混称

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `application/observations/projection.rs` | `current_view`、query、checkpoint 和 event drain 入口 | 拆为 `read.rs` 与 `events.rs`，或先改 `access.rs` |
| `application/observations/order_book/projection.rs` | 单个 `ingest_orderbook_snapshot` command | 并入 `order_book/mod.rs` 或改 `snapshot.rs`；它不是投影 |
| “current projection use cases” | observation ingestion 和当前状态读取 | `observation ingestion and current-view reads` |
| Market observation key 的 “current market-data projection” | 一份带 scope/provider/kind 的当前视图资源 | `current market-data view` |
| Python `MarketProjection` | 按 `MarketViewKey` 打开和解码 indexed entity | owner-native `MarketCurrentView` |
| composition 的 `current projection` | Strategy 侧 Market current-view access | `current views` |
| `projected_markets` | Reference catalog 解析出的 Market runtime routes 数量 | `resolved_markets` |
| “stable projection” | Actor 已确认的 provider/source current state | 按实际对象称 `current view`、`route state` 或 `source state` |

Rust 的两个同名文件职责可见
[`observations/access.rs`](../../crates/modules/market/src/application/observations/access.rs)
和
[`order_book/snapshot.rs`](../../crates/modules/market/src/application/observations/order_book/snapshot.rs)。
Python Market current-view 实现在 owner companion
[`market/contract/py/src/lib.rs`](../../crates/modules/market/contract/py/src/lib.rs)，Python facade 只做公开导出。

### Account：segment view 和 indexed reader 被混称

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `AccountActor::projection(segment_key)` | 从 Actor 当前状态生成一个 `AccountSegmentView` | `segment_view()` |
| runtime 局部变量 `projection` | 用于 paper settlement / mark-to-market 的 segment view | `segment_view` |
| Python `AccountCurrentProjection` | 读取、校验、解码 Account indexed current families | owner-native `AccountCurrentView` |
| Python `AccountObservedOrdersProjection` | 读取 indexed `observed_orders` family | `AccountCurrentView.snapshot().observed_orders` |
| Python `AccountApplication._projections` | 每个 AccountId 对应的 indexed current-view reader | `_current_views` |
| `current_projection()`、`observed_orders_projection()` | System client 构造 indexed reader | `account_current()` / `current_view()`、`observed_orders()` |
| “private-stream projections migrate” | provider private stream 产生的 Account 当前事实 | `private-stream ingestion` / `account current state` |

Actor 已经公开准确的 `current_view()`，因此 `projection()` 没有提供额外含义，见
[`account/src/services/actor.rs`](../../crates/modules/account/src/services/actor.rs)。Python reader
由 owner companion [`account/contract/py/src/lib.rs`](../../crates/modules/account/contract/py/src/lib.rs) 实现。

### Risk 和 Capital：Python query facade 及 contract model 文件被混称

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| Python `RiskProjection` | indexed Risk databases 上的查询便利方法 | owner-native `RiskCurrentView` |
| `latest_projection()` | 创建 Risk indexed reader | `current_view()` |
| Python `CapitalProjection` | indexed Capital databases 上的查询便利方法 | owner-native `CapitalCurrentView` |
| `current_projection()` | 创建 Capital current-view reader | `current()` / `current_view()` |
| Rust Capital contract `projection.rs` | `CapitalCurrentView` 及其组成的 contract-owned current models | `current.rs` 或 `current_view.rs` |
| application 字段 `_projection` | current-view query object | `_current_view` / `_current_queries` |

这些类型没有从事件重建状态；它们只是读取已经发布的 view。对应实现为
[`risk/contract/py/src/lib.rs`](../../crates/modules/risk/contract/py/src/lib.rs)、
[`capital/contract/py/src/lib.rs`](../../crates/modules/capital/contract/py/src/lib.rs) 和
[`capital/contract/src/current.rs`](../../crates/modules/capital/contract/src/current.rs)。

### Python Execution、Launch、Portfolio 和分析代码

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `infrastructure/contracts/execution/projection.py`、`ExecutionProjection` | 打开 Execution current view 并提供 current/diagnostic 查询 | 文件改 `current.py`；类型改 `ExecutionCurrentViews` 或 `ExecutionViewQueries` |
| `ExecutionApplication._projection` | Execution current-view query dependency | `_current_views` |
| Strategy `Projection` 测试 fake | current-view query fake | 跟随具体依赖改为 `CurrentViews` / `ExecutionQueries` |
| `StaleProjectionError` | SDK 中未绑定具体资源的空异常类型 | 若用于 current-view freshness，改 `StaleCurrentViewError`；若无调用方则删除 |
| `StrategyLaunchConfig` 注释中的 “typed projection” | 已经命名准确的规范化 launch 输入 | 注释改为 `typed normalized launch inputs`；类型无需改 |
| Portfolio package 注释中的 “record and projection” | Portfolio application、events 和 snapshots | 改为 `state and current snapshots` |
| `OptionGreeksProjectionRequest/Result` | 用 Black-Scholes 对当前观测做确定性计算 | `OptionGreeksCalculationRequest/Result` |
| 旧通知 journal record | 通知已经调用 publisher 后记录的投递受理结果 | 全量迁移为 `notification_submission_recorded`，不保留旧 discriminator |

Greeks 计算没有把权威事件流投成读模型，也不是未来价格预测，见
[`market/analytics.py`](../../kairospy/investment/apps/market/application/analytics.py)。通知路径实际先调用
`publish()` 再写 journal，因此旧 discriminator 没有描述已发生的动作，见
[`strategy/apps/decisions/application/application.py`](../../kairospy/strategy/apps/decisions/application/application.py)。

### Platform Integration：同步 API、规范化 DTO 和共享 client 被混称

| 当前名称或位置 | 实际职责 | 目标名称或处理 |
| --- | --- | --- |
| `integration::blocking` 的 “synchronous projections” | async capability 的显式同步调用面 | `blocking API` / `synchronous facade`；模块名 `blocking` 已足够 |
| “capability projections share reqwest pool” | capability adapter 复用一个 HTTP client | `capability clients` / `adapters` |
| Hyperliquid “scalar-only projection” | provider-native ledger 的规范化记录 DTO | `normalized ledger record` |
| Binance WebSocket “not a capability projection” | 连接用途说明 | 直接说明是 API connection；删除反向定义 |
| Conflux “SQLite projections” | contract-owned SQLite data-plane reader | `SQLite catalog readers` |

这里的问题不是公开类型冲突，而是 platform 文档把 facade、adapter 和 DTO 都纳入一个没有
约束的总称。相关位置包括
[`integration/src/blocking/mod.rs`](../../crates/platform/integration/src/blocking/mod.rs)、
[`integration/src/transport/http/mod.rs`](../../crates/platform/integration/src/transport/http/mod.rs)
和
[`hyperliquid/account/history.rs`](../../crates/platform/integration/src/participants/hyperliquid/account/history.rs)。

### Schemas、检查脚本和现有文档

| 当前位置 | 当前混称 | 处理原则 |
| --- | --- | --- |
| `schemas/README.md` | 把 publication 文件统称为 `projection/` delivery shape，但当前 v2 已按 `views/` 组织 | 改为 event、view、type、control 四种实际 contract shape |
| `schemas/v2/capital/README.md`、`schemas/v2/system/README.md` | storage projection、operational projection | 分别改为 `CapitalCurrentView`、`System current view` |
| `schemas/v2/registry.md` | “Market Reference projection” consumer | 改为 Market 的 `Reference catalog consumer` / `market-universe resolution` |
| `schemas/v2/README.md` | compatibility projection | 改为 compatibility view/adapter；这里表达的是禁止兼容外壳，不是读模型 |
| `scripts/check/check_cli_boundary.py` | contract/projection command、projection method token | 随公共 API 改成 control/current-view/catalog 分类，并分别检查 |
| `scripts/maintenance/repair_reference_symbol_identity.py` | current projection rows | 改为 current catalog rows |
| `docs/architecture/cli-boundary.md` | 用 `projection` 同时表示 current storage、Reference SQLite、runtime state 和 connected API | 按命令实际来源逐项改为 `current view`、`catalog query`、`runtime state` 或 `contract query` |
| `docs/architecture/capital-management.md` | current projection、alerts projected | `CapitalCurrentView`、`alerts are emitted/published` |
| `docs/architecture/portfolio-management.md` | record、state、persistence 和 evidence 都称 projection | 使用 `Portfolio state`、`PortfolioSnapshot`、`valuation evidence`、`persistence` |
| `docs/guides/operations.md` | Reference SQLite projection、Execution projection、projection schema | `Reference catalog`、`Execution current view`、`view schemas` |
| `docs/guides/strategy-notifications.md` | intent lifecycle notification projection | `intent lifecycle notification policy` / `notification routes` |
| integration provenance 文档 | principal、product、socket、blocking adapter 被称 projection | 改为 participant connection、product capability、stream、blocking facade |
| `docs/integrations/capability-matrix.md` 的 OKX ticker projection | 实际 provider ticker endpoint/stream capability | 写明 `ticker endpoint` 或 `stream` |

Decision 0003、0007、0008、0011 曾使用旧术语，其中有些还引用了已删除或移动的文件路径。
本次硬迁移同步修正这些失效表述，并由 Decision 0013 记录统一词汇和迁移后果。

测试和 fixture 中的 `Projection` fake、测试函数名、错误匹配，以及
`tests/test_account_projection_transport.py` 文件名，都只是生产命名的镜像。它们应在对应 API
迁移的同一提交中改成 `ViewReader`、`CurrentViews`、`Catalog` 或 `Calculation`，不能为测试
保留第二套兼容 facade。

### 可以保留的精确用法

[`DatasetAnalyticalView`](../../kairospy/research/apps/data/application/readers.py) 中的 “bounded column
projection” 表示从 tabular dataset 选择列，是关系代数/数据帧里的精确局部术语。它不会被
当成进程 contract、indexed reader 或状态所有者，可以保留。

如果未来确实引入由事件历史重建并独立维护的 materialized read model，也应直接按用途命名，
例如 `OrderAuditIndex` 或 `ReferenceSearchIndex`。实现说明可以称它“由事件派生”，但公共类型
仍不需要叫 `Projection`。

## Contract 和目录的目标形状

跨进程 contract 按能力组织，而不是为所有模块制造同样的层：

```text
contract/src/
  control/    commands、health 和有界 request/query
  view/       indexed current view 的 key、reader、schema 和校验
  catalog/    仅在确有持久目录查询时存在；当前主要是 Reference
  event/      增量 business events
```

因此目标模块形状为：

```text
Account / Market / Execution / Risk / Capital
  control + view + event

Reference
  control + catalog + event
```

公共 client 以业务能力命名，隐藏具体 reader：

```rust
account.account_current(account_id)?.read()?;
market.quote(scope, provider, qualifier)?.read()?;
execution.current_execution(identity)?.read()?;
risk.latest(actor_id)?.read()?;
capital.current(group_id)?.read()?;

reference.catalog().market(&market_id)?;
reference.catalog().markets(query)?;
reference.catalog().for_execution()?;
```

`Sqlite`、`Mmap` 可以出现在私有实现类型、构造配置和运维诊断中，但不应取代业务 API 的
`Catalog`、`CurrentView` 或 `Snapshot` 语义。

## 迁移顺序

### 第一阶段：修正 Reference 公共边界

1. 引入消费者窄 snapshot 或先引入 `ReferenceCatalogSnapshot`。
2. 迁移 Rust 和 Python 调用方，不再导出 `ReferenceMarket`、`ReferenceHealth`。
3. 将 SQLite 查询结果和入口改为 `Catalog` 语义。
4. 删除 `contract/src/transport/projection.rs`。

这是最高优先级，因为错误名称已经进入跨业务 contract，并且当前大 snapshot 可以表达
“字段因裁剪为空”和“权威数据为空”两种无法区分的状态。

### 第二阶段：统一 indexed reader 语言

1. Rust connected facade 将 `current_projection()` 改为 `read_current()`。
2. Python Account、Market、Execution、Risk、Capital 类型改为 `*ViewReader`、
   `*CurrentViews` 或 `*ViewQueries`。
3. System clients、composition、application 字段和测试 fake 同步迁移。
4. 输出错误统一使用 `current view`、`snapshot metadata`、`view unavailable/stale`。

### 第三阶段：消除消费者私有“投影层”

1. Execution `dependencies/projection` 改为 `dependencies/state`。
2. ready、stale、refresh、水位等规则继续由 Execution 私有 service 持有。
3. Market 和 Account 的 `projection.rs`/`projection()` 分别归入 read、events、snapshot
   ingestion 和 segment view 的真实职责。

### 第四阶段：全量迁移持久标识

1. 将 Reference provider scan format 表、SQL 常量和已有数据库统一迁移到新名称。
2. 日志统一使用 `scan_format_version`、`reference_provider_scan_reset`，不再识别旧字段和事件。
3. Strategy journal 全量迁移为 `notification_submission_recorded`，不再接受旧 discriminator。
4. 更新已有 Decision 中已经失效的名称和路径，并用新的 Decision 记录术语收敛。

### 第五阶段：文档、schema 和架构检查收口

1. 更新 `schemas/README.md`，不再声称 publication 文件属于 `projection/` delivery shape。
2. 将 schema、operations guide、CLI boundary 中的 runtime projection 改为具体的 current
   view、catalog、snapshot、query 或 event stream。
3. 更新测试名、fake 和 `check_cli_boundary.py` 的错误文本。
4. 增加词汇检查：除本文件、`project_*` 和获准的数据列投影外，生产代码中
   不再新增 `projection`。

## 迁移约束与验收

- 改名不能改变业务 owner：跨模块读取仍必须经过 owner contract。
- indexed reader 必须继续验证 key/identity、applied revision、rebuild state 和
  freshness；改名不能弱化一致性检查。
- Reference 多表结果必须在同一个 SQLite read transaction 中取得同一 watermark。
- `CurrentView` 必须有界。完整 history/audit 不能因为调用方能从 current view 看到部分记录就宣称
  已经实现。
- 数据库表、日志事件、journal discriminator 和现有 fixture 必须一次性迁移，不保留双写或兼容别名。
- 先迁移公共 contract 和生产调用方，再更新测试名称；不能让测试兼容 facade 永久保留旧
  API。

完成迁移后应满足：

1. 生产 Rust/Python 公共 API 不再暴露无限定的 `Projection` 类型或方法。
2. `projection.rs` 不再作为 current-view reader、Application 入口或 DTO 收纳文件。
3. 每个跨进程读取入口都能明确回答：这是 request query、indexed current view、SQLite
   catalog/history query，还是 event stream。
4. `rg -i 'projection|projected'` 的剩余结果仅包括本说明、精确的数据列投影，以及与“项目”
   含义相关的 `project_*` 标识。
