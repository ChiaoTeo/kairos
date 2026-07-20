# 数据系统收敛、领域边界与产品化改造方案

状态：Proposed
适用范围：`kairospy.domain`、`kairospy.data`、`kairospy.market_data`、`kairospy.reference`、`kairospy.study_platform`、`kairospy.backtest`、数据 CLI 与 `data/` 目录
目标：收敛系统运行路径，删除旧代码和旧数据，规范 Domain 与 Data 的关系，并形成可以被研究、回测和生产稳定消费的数据产品。

## 1. 执行摘要

当前系统已经具备 Dataset Catalog V3、不可变 Release、Parquet、Source/Canonical/Curated/Features/Studies 分层、Provider Connector、point-in-time 读取、研究快照和回测冻结等关键能力。核心数据链路已有较完整的自动化测试，说明新架构具备成为唯一正式路径的基础。

目前的主要问题不是缺少新能力，而是迁移尚未完成：

1. `DatasetClient`、`ParquetMarketEventRepository`、`MarketReplayDataset/DatasetRepository`、`BarRepository` 等多套读取和存储模型并存；
2. CLI 同时暴露 `data`、`history`、`study capture-series`、`backtest` 等相近但治理等级不同的入口；
3. 部分数据类型仍通过目录名称和文件形态识别，而不是由 Catalog 中的显式契约决定；
4. Domain、Market Data、Study、Backtest 之间存在对象重复和反向依赖；
5. Product 定义、Managed Dataset、Catalog Registry 和 Provider 配置承担了部分重复职责；
6. Parquet 与 CSV 双写、旧目录和独立 Repository 继续增加存储和维护成本；
7. 质量检查存在，但尚不足以自动决定一个 Release 能否进入研究、回测或生产；
8. 用户仍需要理解较多内部概念，才能完成发现、准备、读取、冻结和诊断。

本次改造的核心决策是：

> Catalog Release 是所有持久化研究数据的唯一治理身份；DatasetClient 是所有研究和回测数据的唯一公开入口；Domain 只定义业务事实与行为，不感知文件、Catalog、Provider、Release 或查询引擎。

完成改造后，系统只保留一条正式运行链路：

```text
Provider
  -> Source receipt/payload
  -> Canonical Release
  -> Curated/Feature Release
  -> DatasetClient
  -> Study / Backtest / Validation
  -> Study Snapshot / Backtest Manifest
```

## 2. 本次改造的完成定义

只有同时满足以下条件，改造才算完成：

- 新增数据不再进入 `data/history`、旧 `data/datasets`、独立 Surface Store 或未注册目录；
- 研究和回测代码不再直接实例化旧 Repository 或拼接数据路径；
- 所有正式数据都有 Product、Release、Schema、Transform、content hash、lineage、coverage、quality 和 usage；
- 所有数据读取都先解析为不可变 Release；
- 回测和正式研究只能消费冻结的、达到相应质量等级的 Release；
- Domain 不依赖 `kairospy.data`、`kairospy.reference`、`kairospy.storage`、`kairospy.study_platform` 或 `kairospy.backtest`；
- Data 层保存的是 Domain 事实的持久化表达，不把供应商原始对象或研究专属对象伪装成 Domain；
- CLI 为普通用户提供发现、准备、读取、验证、冻结和诊断的完整闭环；
- 旧代码和旧数据有明确迁移清单、删除日期、回退方案和自动化验收证据。

## 3. 当前系统审阅结论

### 3.1 应保留并强化的能力

以下能力方向正确，应作为目标系统基础：

- `DataCatalog` 的 Product、Release、Schema、Transform、source binding 和状态模型；
- Release 发布后不可覆盖，使用 content hash 证明内容；
- `DatasetClient.get()`、`replay()`、`replay_snapshots()`、`plan()`、`acquire()` 和研究冻结能力；
- Provider Connector 的显式获取、估算、限额和幂等发布；
- Source、Canonical、Curated、Features、Studies 五层布局；
- `event_time`、`available_time` 和 `[start,end)` 时间契约；
- Parquet 分区、列裁剪、DuckDB/Arrow 读取和事件确定性重放；
- Study Snapshot、Backtest Manifest 和审计 hash；
- 数据获取、数据湖、事件仓库和 DatasetClient 的现有自动化测试。

### 3.2 必须收敛的多套数据路径

| 当前路径 | 当前用途 | 目标处理 |
|---|---|---|
| `DatasetClient + DataCatalog` | 新治理入口 | 保留，成为唯一公开入口 |
| `ParquetMarketEventRepository` | Canonical event 读写 | 保留为 Data 内部 storage driver，不直接暴露给研究代码 |
| `MarketReplayDataset + DatasetRepository` | MarketSnapshot 回放 | 迁入统一 Release storage contract，Repository 降为内部 driver |
| `StudySnapshotCollectionStore` | MarketSnapshot append session | 合并到统一 Publisher/Collection Service |
| `BarRepository` | CSV OHLCV 与 SMA 示例 | 迁移并删除 |
| `SurfaceRepository` | 独立 JSON Surface | 迁移为 Feature Release 后删除 |
| `FileOptionCaptureRepository` | 期权快照采集运行记录 | 与 Study Artifact Store 明确分工；无消费者后合并生命周期 |
| Provider 专用 Store | Reference、cache、source | Source/Reference 契约内保留，不得成为研究读取入口 |

### 3.3 当前身份和存储耦合

当前部分代码通过目录名判断数据类型，例如识别 `canonical/market/dataset=*` 后选择 Event Repository，或通过 `dataset.json` 判断 MarketReplayDataset。这会导致：

- 目录迁移可能改变程序行为；
- 新 storage backend 难以接入；
- Catalog 无法完整说明 Release 应由哪个 Reader 读取；
- 测试容易验证某个目录结构，而没有验证数据产品契约。

目标是为 Release 增加显式存储描述：

```yaml
storage:
  kind: tabular | market_events | market_snapshots | reference
  format: parquet
  layout_version: 1
  partitioning: [event_year, event_month]
  primary_time: available_time
```

客户端只能根据 `storage.kind` 选择内部 Reader，禁止根据路径名称推断类型。

### 3.4 当前 Product 定义重复

当前逻辑产品定义、物理布局、Schema、Capabilities 和动态 Provider 配置分散在多个位置。目标是统一为一个 `DataProductContract`：

```text
DataProductContract
  identity: logical key, title, description, owner
  semantics: domain, data type, dimensions, primary time
  schema: schema ID and compatibility policy
  storage: storage kind and layout policy
  sources: provider bindings and source-selection policy
  quality: quality profile and minimum publication level
  usage: default view, supported operations and known limitations
```

内置常量和外部配置都必须先编译成这一种对象，再注册到 Catalog。Product Spec 不保存具体 Release 路径；路径由统一 Layout Policy 根据 Product 和 Release ID 生成。

### 3.5 当前质量门禁不足

通用 Writer 当前主要验证非空和主键重复，coverage 中的缺口不一定导致发布失败。目标系统必须按数据类型选择 Quality Profile，并由检查结果计算质量等级：

| Profile | 必要检查示例 |
|---|---|
| OHLCV | 主键、周期覆盖、OHLC 关系、负值、成交量、时区、available time |
| Quote | `bid <= ask`、负值、价差、staleness、crossed/locked 比例、来源完整性 |
| Trade | trade ID、重复率、价格和数量、乱序率、修正/撤销语义 |
| Market Event | available time、确定性顺序、事件类型 Schema、原始来源定位 |
| Option Snapshot | 合约覆盖、同步性、缺腿率、标的价格、到期和行权价完整性 |
| Feature | 输入 Release 冻结、空值/无限值、窗口完整性、无未来数据 |

发布调用方不得直接把任意数据声明为 Q2/Q3/Q4。质量等级必须是 Quality Engine 的输出。

## 4. Domain 与 Data 的目标关系

### 4.1 边界原则

Domain 回答“交易系统中的事实是什么、允许什么行为”；Data 回答“事实从哪里来、如何编码、如何版本化、如何查询和复现”。

依赖方向必须是：

```text
domain <- application services <- data/connectors/storage
domain <- strategy/risk/pricing/backtest
```

禁止：

```text
domain -> data
domain -> catalog implementation
domain -> storage
domain -> study
domain -> backtest
```

### 4.2 Domain 应包含什么

Domain 可以包含：

- `InstrumentId`、`AssetId`、`VenueId`、`AccountKey`；
- `InstrumentDefinition`、`ListingDefinition`、`InstrumentContractSpec`；
- `Quote`、`Trade`、`Bar`、`OrderBook`、`Greeks`、`DerivativeMarketState`；
- Order、Execution、Ledger、Intent、Position 和生命周期事件；
- 与业务事实直接相关的约束，例如价格非负、时间必须带时区、期权到期日在估值日之后。

Domain 不应包含：

- Dataset ID、Release ID、Alias、文件路径和 Parquet；
- Provider 下载请求、HTTP receipt、API 配额；
- quality level、Catalog promotion 和 acquisition policy；
- pandas/Arrow/DuckDB 对象；
- 研究样本切分、未来标签或报告文件；
- Backtest 专属 MarketSnapshot 存储结构。

### 4.3 Data 应如何表达 Domain

Data 层应定义稳定的 Canonical Record Schema，用于持久化 Domain 事实。例如：

```text
Domain Quote
  instrument_id
  bid / ask
  bid_size / ask_size
  event_time

Canonical Quote Record
  上述 Domain 字段
  + available_time
  + ingested_at
  + provider_id
  + venue_id
  + source_namespace
  + source_instrument_id
  + source_release_id
  + correction flags
```

其中：

- Domain 字段表达经济事实；
- Data 字段表达可见时间、来源、版本和审计语义；
- Provider connector 负责将 Provider Payload 转为 Canonical Record；
- Mapper 负责在需要时将 Canonical Record 转为 Domain 对象；
- Domain 对象不得携带 Provider 原始 payload；
- Canonical Record 可以保留 `payload_json`，但常用字段必须物理列化。

### 4.4 Catalog 的两种含义必须分开

系统目前同时有：

- Instrument Catalog：管理 Instrument Definition 和 Venue Listing；
- Dataset Catalog：管理 Dataset Product 和 Release。

两者都可保留，但命名和职责必须明确：

| Catalog | 主键 | 回答的问题 |
|---|---|---|
| Instrument Catalog | `InstrumentId` | 这是什么金融合约，在哪个 Venue 如何交易 |
| Dataset Catalog | `LogicalDatasetKey/ReleaseId` | 这是什么数据产品，具体使用哪份不可变数据 |

Dataset Product 可以声明 instrument universe 语义，但不得复制完整 Instrument Definition。Canonical 数据通过 `instrument_id` 引用 Instrument Catalog，并冻结所依赖的 reference release 或有效期版本。

### 4.5 需要修正的依赖

当前 `domain.strategy.StrategyContext` 直接引用具体 `ReferenceCatalog`，并通过类型引用 Backtest、Study 和 Volatility 对象。建议将 StrategyContext 移出 Domain，放入 `kairospy.strategies.runtime` 或应用层。

如果 Domain 行为确实需要查询合约定义，应依赖最小 Protocol：

```python
class InstrumentDefinitionProvider(Protocol):
    def get(self, instrument_id: InstrumentId, as_of: datetime) -> InstrumentDefinition: ...
```

具体 `ReferenceCatalog` 在组合根注入。Domain 不知道数据来自 JSON、Catalog、数据库或测试 fixture。

### 4.6 统一事件模型

目前 Domain `EventEnvelope` 与 Data/Market Data `MarketEventEnvelope` 各自合理，但命名接近且职责容易混淆。目标命名建议：

- `DomainEventEnvelope`：业务状态变化和领域事件；
- `MarketDataRecord`：可持久化、point-in-time 的市场数据事实；
- `SourceRecord`：供应商原始记录；
- `ReplayRecord`：DatasetClient 向重放消费者提供的只读记录。

禁止用一个万能 Event 类型同时承担 Domain Event、Source Event 和 Canonical Market Event。

## 5. 唯一正式运行路径

### 5.1 数据生产路径

```text
1. discover product
2. plan coverage
3. select source
4. acquire source payload
5. canonicalize
6. validate schema/coverage/quality/lineage
7. publish immutable release
8. promote alias
```

所有 Provider 必须通过同一个 Connector Protocol。Provider 不得自行更新 Catalog JSON，也不得在 Catalog 之外创建可被研究读取的“正式数据”。

### 5.2 数据消费路径

```text
1. resolve Product or Alias
2. enforce run-mode quality requirement
3. freeze Release ID
4. build query plan
5. dispatch internal reader by storage kind
6. return Arrow or deterministic replay feed
7. write input snapshot to study/backtest artifact
```

Study 和 Backtest 都不得直接调用 Provider、HTTP Client 或 acquisition。Backtest 模式必须继续禁止网络获取。

### 5.3 唯一公开 Python API

普通使用者只面向以下对象：

```python
data = DatasetClient("data")

product = data.find(
    data_type="ohlcv",
    instrument="BTC-USDT",
    frequency="1d",
).one()

prepared = data.prepare(
    product,
    start="2024-01-01",
    end="2026-01-01",
    minimum_quality="backtest",
    acquire="if-missing",
)

table = prepared.query(fields=["period_start", "close"]).pandas()
snapshot = prepared.freeze(study_id="btc-sma-v2")
```

高级用户仍可使用 `get()`、`replay()` 和 `sql()`，但不需要接触内部 Repository。

## 6. 旧代码与旧数据删除计划

### 6.1 删除原则

删除必须满足：

1. 已建立目标格式和目标入口；
2. 已有幂等迁移工具；
3. 迁移前后行数、时间范围、主键和 hash 可核对；
4. 已扫描所有代码、Notebook、文档和测试消费者；
5. 已经过至少一个版本的 deprecated 提示；
6. 已建立可从 Source 或备份重建的方式；
7. 删除动作与代码迁移分开提交，便于审阅和回退。

### 6.2 第一批删除对象

| 对象 | 迁移目标 | 删除验收 |
|---|---|---|
| `kairospy.history.BarRepository` | Canonical OHLCV Release | 全仓无生产 import；CLI 已替换；数据已核对 |
| `kairospy history *` | `kairospy data prepare/query` 与正式 backtest | CLI help 中不再出现 history |
| `strategies.sma_cross_study_backtest -> BarSeries` | Arrow rows 或统一 Bar Series Port | 策略不再 import `kairospy.history` |
| Parquet 的 CSV sidecar | 仅 Parquet | 新发布不生成 CSV；旧 CSV 经核对删除 |
| `data/history` | Canonical | 目录为空并删除 |
| 旧 `data/datasets` | Curated Release | Release 已注册且 hash 冻结 |
| `SurfaceRepository` / `data/surfaces` | Feature Release | 所有 Surface 查询走 DatasetClient |
| 空的 `raw/normalized/derived/study` | 五层标准目录 | 文档、CLI 和代码无引用 |

### 6.3 第二批收敛对象

- 将 `DatasetRepository` 变成 Data 内部 `MarketSnapshotStorageDriver`；
- 将 `StudySnapshotCollectionStore` 的 session append 能力合入 Collection Publisher；
- 将 `FileOptionCaptureRepository` 与 Studies Artifact Store 合并或明确完全不同的生命周期；
- Provider 专用 Reference Store 使用 Instrument Catalog/Reference Release 的统一接口；
- 删除依据目录名选择 Reader 的逻辑；
- 删除 Release 内局部 aliases 与 Catalog 全局 Alias Registry 的双轨机制。

### 6.4 旧数据迁移账本

迁移工具必须生成 `data/migrations/<migration-id>/report.json`：

```json
{
  "migration_id": "history-to-canonical-v1",
  "started_at": "...",
  "source": "data/history/...",
  "target_release_id": "ds_...",
  "source_rows": 1000,
  "target_rows": 1000,
  "source_window": {"start": "...", "end": "..."},
  "target_window": {"start": "...", "end": "..."},
  "primary_key_conflicts": 0,
  "content_verification": "passed",
  "source_deleted": false
}
```

只有 `content_verification=passed` 且目标 Release 可通过 DatasetClient 读取时，才允许单独执行 `--delete-source`。

## 7. 产品体验改造

### 7.1 用户核心任务

产品体验必须围绕六个任务，而不是围绕内部模块组织：

1. 找到数据；
2. 判断是否适合当前研究；
3. 准备缺失数据；
4. 查询或重放；
5. 冻结并复现；
6. 定位失败原因。

### 7.2 CLI 目标形态

建议收敛为：

```bash
kairospy data search
kairospy data describe <product>
kairospy data prepare <product> --start ... --end ... --quality backtest
kairospy data query <product-or-release> --start ... --end ...
kairospy data replay <product-or-release> --start ... --end ...
kairospy data compare <release-a> <release-b>
kairospy data freeze <study-id> --input ...
kairospy data doctor <product-or-release>
kairospy data migrate ...
```

Provider 运维命令可以保留在 `kairospy provider massive ...` 或内部运维组，不应与普通数据产品命令混在同一层级。

### 7.3 `describe` 必须回答的问题

一次 `describe` 应输出：

- 产品经济含义和 owner；
- asset class、instrument/universe、venue、provider、频率；
- 主时间字段及 `[start,end)` 语义；
- 可用 Release、Alias 和当前推荐 Release；
- 覆盖范围和已知缺口；
- Schema 字段、类型、单位和主键；
- 质量等级、最近一次质量报告及失败检查；
- 是否支持 point-in-time、SQL、事件 replay 和 MarketSnapshot replay；
- 已知限制；
- 一段可复制的 Python 和 CLI 示例。

### 7.4 `prepare` 是产品化关键

当前 plan、acquire、validate、publish、promote 分散。新增 `prepare()` 作为编排层：

```text
resolve intent
 -> inspect local coverage
 -> explain selected source and estimated cost
 -> acquire only when policy permits
 -> validate candidate
 -> publish immutable release
 -> optionally promote alias
 -> return PreparedDataset
```

`prepare` 必须默认可解释、幂等、可恢复，不得静默发起高成本下载。执行前输出：

- 缺失范围；
- Provider/Venue；
- 请求数和估算字节；
- entitlement/credential 状态；
- 将创建的层和数据产品；
- 达不到目标质量时的降级行为。

### 7.5 错误体验

错误信息必须包含：

- 用户请求；
- 失败阶段；
- 已解析 Product/Release；
- 缺失字段、覆盖或质量检查；
- 是否发生任何持久化修改；
- 下一条可执行命令。

禁止只抛出 `FileNotFoundError`、目录不存在或 Reader 不匹配等内部错误。

### 7.6 数据产品健康页

建议增加 `data diagnostics` 或生成静态报告，至少包含：

- Product 数量及 owner 覆盖率；
- Release 数量和各状态分布；
- Q1/Q2/Q3/Q4 分布；
- 无 provider、无 hash、无 lineage、无 usage 的 Release；
- Alias 指向及 promotion 时间；
- 数据新鲜度和覆盖缺口；
- quarantined/failed Release；
- 旧路径和未注册文件扫描结果。

## 8. 分阶段实施计划

### Phase 0：冻结边界与建立基线

工作：

- 批准本文档中的唯一正式路径；
- 生成代码 import 图、CLI 清单、Catalog 清单和数据目录清单；
- 为旧入口增加 deprecated 标记，禁止新增消费者；
- 建立旧代码和旧数据迁移账本；
- 给正式 Product 补 owner 和用途。

验收：

- CI 可检测新增的 `kairospy.history`、直接 `DatasetRepository` 和硬编码 `data/...` 读取；
- 所有旧入口都有 owner、迁移目标和删除阶段；
- 当前 Product、Release、目录和消费者清单已冻结到审计文件。

### Phase 1：建立统一 Release Storage Contract

工作：

- Release 增加 `storage.kind`、layout version 和 partition contract；
- DatasetClient 改为按 storage kind 分发；
- Event、Tabular、MarketSnapshot Reader 实现统一内部 Protocol；
- Product 定义合并为 DataProductContract；
- Alias 只保留 Catalog Registry。

验收：

- 客户端中不存在根据目录名或 `dataset.json` 推断数据类型的分支；
- 同一个 `describe/get/freeze` 流程覆盖三种 storage kind；
- Product Spec 只有一个事实来源；
- Catalog round-trip 和旧 Registry migration 测试通过。

### Phase 2：Domain/Data 解耦

工作：

- 将 StrategyContext 移出 Domain；
- 用 Protocol 替代 Domain 对具体 ReferenceCatalog 的依赖；
- 明确 Domain Event 与 MarketDataRecord；
- 建立 Canonical Record 到 Domain 对象的 Mapper；
- Reference 数据通过 Instrument ID 和有效时间关联。

验收：

- `kairospy/domain` 中无 `from kairospy.*` 的跨包实现依赖；
- Domain 单元测试不需要文件系统、Catalog JSON、Arrow 或 Provider；
- Connector contract 测试证明相同 Provider payload 可稳定映射到 Canonical Record；
- Replay contract 测试证明 Canonical Record 可稳定映射为 Domain 输入。

### Phase 3：质量与发布门禁

工作：

- 实现按数据类型配置的 Quality Profile；
- Quality Engine 计算质量等级；
- promotion 校验完整元数据和质量证据；
- 为现有 Release 重跑质量审计；
- 建立 Product health report。

验收：

- 不可能仅通过参数把 Release 声明为 Q3/Q4；
- 人为制造重复、时间泄漏、crossed quote 和覆盖缺口时，发布或 promotion 会失败；
- 所有 Backtest 输入均为 Q3/Q4；
- 所有 Production 输入均为 Q4；
- Quality report hash 进入 Alias promotion 和 Study Snapshot。

### Phase 4：产品体验与 CLI 收敛

工作：

- 实现 search、describe、prepare、query、freeze、doctor；
- Provider 运维命令退出普通数据工作流；
- 示例和 Notebook 统一使用 DatasetClient；
- 错误信息增加下一步建议；
- 输出产品健康报告。

验收：

- 新用户不阅读代码即可完成一个数据产品的发现、准备、读取和冻结；
- README 的主流程不出现 Repository、物理路径或 Dataset 内部格式；
- 所有官方 Notebook 不直接读取 CSV/Parquet 路径；
- CLI reference tests 覆盖成功、缺数据、质量不足、无凭证和禁止网络获取。

### Phase 5：迁移和删除

工作：

- 迁移 `data/history`、旧 `data/datasets`、Surface Store 和 CSV sidecar；
- 修改 SMA、旧研究和 Backtest 消费者；
- 删除旧 CLI、Repository、迁移兼容代码和空目录；
- 对全仓执行未注册数据扫描。

验收：

- 全仓无旧模块 import；
- 新发布不生成 CSV sidecar；
- 标准数据根目录只保留五层、catalog、reference、artifacts/migrations 和明确标记的 cache/quarantine；
- 所有迁移报告通过，旧源数据已在独立提交中删除；
- 全量测试、代表性研究和回测结果可复现。

## 9. 自动化验收设计

### 9.1 架构测试

新增 `tests/architecture/`：

```text
test_domain_dependencies.py
test_public_data_api.py
test_no_旧版_repository_imports.py
test_no_physical_data_paths_in_study.py
test_storage_driver_boundaries.py
```

关键断言：

- Domain 不依赖上层模块；
- Study/Strategies 不直接依赖 storage driver；
- Provider connector 不直接修改 Catalog Registry；
- 只有 Data Publishing workflow 可以发布 Release；
- 只有 DatasetClient 是研究数据公开入口。

### 9.2 Catalog 完整性测试

对每个非 Draft Product/Release 验证：

- logical key 唯一；
- owner、description、dimensions 和 primary time 存在；
- storage kind 已注册；
- relative path 位于允许的数据层；
- content hash 与 Manifest 一致；
- Schema、lineage、coverage、quality、usage、release 元数据齐全；
- provider/venue 与 Source Binding 一致；
- Alias 只指向已批准 Release；
- Backtest/Production Release 达到最低质量等级。

### 9.3 数据迁移测试

每个迁移器必须测试：

- dry-run 不修改文件；
- 重复执行幂等；
- 中断后可恢复；
- 行数、主键、时间范围和数值精度一致；
- 目标 Release 可读取、可冻结、可重放；
- `--delete-source` 只有验证成功后可用；
- 删除后仍能从 Source 或备份重建。

### 9.4 产品体验测试

至少建立以下端到端场景：

1. 本地已有完整 Release，prepare 不访问网络；
2. 本地缺少部分范围，plan 清楚解释缺口；
3. 用户允许获取，Connector 发布新 Release；
4. Provider 无凭证，返回可操作诊断且不产生半成品；
5. 数据质量不足，Study 可显式降级但 Backtest 拒绝；
6. Alias 更新后，已冻结研究仍使用原 Release；
7. 两次相同输入生成相同内容 hash 和 replay 顺序；
8. 用户可从 describe 输出复制示例并成功读取数据。

### 9.5 回归与可复现性测试

选择至少三条 Golden Pipeline：

- Binance BTC-USDT OHLCV -> Feature -> SMA/简单研究；
- Deribit BTC Option -> Curated/Feature -> 研究验证；
- Massive SPXW Events -> MarketSnapshot -> Backtest。

每条记录：

- Product Key 和 Release ID；
- content hash；
- 行数、覆盖和质量摘要；
- Study Snapshot 或 Backtest Manifest hash；
- 关键输出指标容差。

迁移后允许物理文件布局变化，但相同冻结输入的经济结果必须一致，或提供经过批准的差异说明。

## 10. 验收命令

最终 CI 至少执行：

```bash
./pyenv/bin/python -m compileall -q kairospy tests
./pyenv/bin/python -m unittest discover -s tests -v
./pyenv/bin/python -m kairospy data diagnostics --strict
./pyenv/bin/python -m kairospy data doctor --all-products
./pyenv/bin/python -m kairospy data migrate --audit-only
git diff --check
```

另增加静态扫描：

```bash
rg 'from kairospy\.history|import kairospy\.history' kairospy examples tests
rg 'DatasetRepository\(' kairospy examples
rg 'data/(history|datasets|surfaces|raw|normalized|derived)' kairospy examples docs
rg 'read_(csv|parquet)|open\(.+data/' examples studies
```

完成状态下，前两项旧依赖扫描应为空；路径扫描只允许出现在迁移器、迁移文档和专门测试中。

## 11. 量化验收指标

| 指标 | 当前基线 | 完成目标 |
|---|---:|---:|
| 正式 Python 数据入口 | 多个 | 1 个 |
| 市场历史数据公开 Repository | 至少 3 套 | 0，均为内部 driver |
| 新发布 CSV sidecar | 默认生成 | 0 |
| 未注册正式数据目录 | 存在 | 0 |
| Product owner 覆盖率 | 不完整 | 100% |
| Release content hash 覆盖率 | 高 | 100% |
| Release 完整元数据覆盖率 | 不完整 | 100% |
| Backtest 使用 Q3/Q4 比例 | 不完整 | 100% |
| Domain 对上层实现依赖 | 存在 | 0 |
| 官方 Notebook 直接读物理路径 | 存在 | 0 |
| 旧目录消费者 | 存在 | 0 |
| 正式研究冻结输入比例 | 不完整 | 100% |

## 12. 风险与控制

### 12.1 一次性删除过多

控制：先增加新契约和迁移器，再切换消费者，最后独立提交删除。旧数据删除不得和 Reader 重构放在同一个不可回退步骤中。

### 12.2 为统一而损失性能

控制：统一的是公开契约，不要求 Event、Tabular 和 MarketSnapshot 使用相同物理实现。内部 storage driver 可以针对 Arrow、Parquet 和确定性重放分别优化。

### 12.3 Domain 过度抽象

控制：只有跨运行模式稳定的经济事实进入 Domain。Catalog、DataFrame、样本切分、质量报告和数据版本继续留在对应应用或数据层。

### 12.4 旧研究结果无法复现

控制：迁移前冻结旧输入 hash、代码版本和代表性输出。迁移后运行 Golden Pipeline；无法等价的研究保留只读归档，不把旧入口永久保留在主运行路径。

### 12.5 本地数据被误删

控制：迁移默认 dry-run；删除需要显式参数；生成迁移账本；Source 与 Canonical 分开处理；无法从 Source 重建的数据必须先备份。

## 13. 推荐执行顺序

建议严格按以下顺序推进：

1. 建立架构测试和当前基线，阻止继续产生新旧路径；
2. 引入 storage kind 和统一 Product Spec；
3. 修正 Domain 依赖方向和事件命名；
4. 实现 Quality Engine 与发布门禁；
5. 实现 search/describe/prepare/doctor 产品体验；
6. 迁移 Bar、MarketSnapshot、Surface 和旧 Studies 数据；
7. 切换全部代码、CLI、Notebook 和文档消费者；
8. 删除旧代码；
9. 核对迁移账本后删除旧数据；
10. 运行全量验收和三条 Golden Pipeline。

不建议先重写所有底层存储。最先要解决的是公开入口、身份契约和依赖方向；这些稳定后，底层 Reader 可以逐个迁移而不影响用户。

## 14. 最终验收清单

### 架构

- [ ] Domain 无上层实现依赖；
- [ ] Dataset Catalog 与 Instrument Catalog 职责清晰；
- [ ] Release 显式声明 storage kind；
- [ ] Product Spec 只有一个事实来源；
- [ ] DatasetClient 是唯一公开数据入口。

### 数据治理

- [ ] 所有正式数据已注册为不可变 Release；
- [ ] 所有 Release 元数据完整；
- [ ] Quality Engine 自动决定等级；
- [ ] Alias promotion 可审计；
- [ ] 正式研究和回测输入已冻结。

### 删除与迁移

- [ ] `BarRepository` 和 `kairospy history` 已删除；
- [ ] 旧 `data/history`、`data/datasets` 和 `data/surfaces` 已迁移；
- [ ] CSV sidecar 已停止生成并完成清理；
- [ ] 空旧目录已删除；
- [ ] 所有迁移账本验证通过。

### 产品体验

- [ ] search/describe/prepare/query/replay/freeze/doctor 完整可用；
- [ ] 缺数据和质量失败具有可执行诊断；
- [ ] 官方示例不暴露物理路径和内部 Repository；
- [ ] 产品健康报告可用于日常运维；
- [ ] 新用户核心任务完成测试通过。

### 证据

- [ ] 全量单元和集成测试通过；
- [ ] 架构依赖测试通过；
- [ ] Catalog strict health check 通过；
- [ ] 三条 Golden Pipeline 通过；
- [ ] 代表性研究和回测结果可复现；
- [ ] 全仓旧依赖扫描为空。
