# Provider 接入与 Data Product 使用手册

状态：Usage-first，持续演进  
日期：2026-07-21  
适用对象：使用、诊断、扩展 KairoSpy Data Provider / Data Product / Dataset 的用户和维护者

本文先给出用户可执行路径，再说明内部工程边界。公开接口优先使用 `Provider`、`Data Product`、`Dataset`；内部类名只在扩展、调试和维护场景出现。

## 快速选择

| 目标 | 推荐入口 |
|---|---|
| 看系统知道哪些外部服务 | `kairospy providers list` |
| 检查某个 Provider 是否配置好 | `kairospy providers doctor <provider>` |
| 看可用 Data Product | `kairospy data products list` |
| 检查某个 Data Product 能不能用 | `kairospy data products doctor <product-or-alias>` |
| 使用内置 Data Product 生成 Dataset | `kairospy data use <product-or-alias> --as <dataset>` |
| 接入 Python 代码资产 | Provider config file + `provider_extensions[]` + `products(context)` / `register(registry, context)` |
| 接入 C++/Rust/旧脚本 | Provider config file + `provider_extensions[]` + `kind: external_process` |

## 0. 用户工作流

常规用户入口只围绕三个对象组织：

```text
Provider -> Data Product -> Dataset
```

### 0.1 查看 Provider

查看当前系统知道哪些 Provider，以及每个 Provider 有多少可用 Data Product：

```bash
kairospy providers list
```

JSON 输出的稳定用户字段：

```json
{
  "product": "providers",
  "operation": "list",
  "providers": [
    {
      "provider": "massive",
      "status": "partial",
      "data_products": 6,
      "available_data_products": 4,
      "venues": ["us-securities"]
    }
  ]
}
```

Provider status 的含义：

| status | 含义 |
|---|---|
| `available` | 该 Provider 的已知 Data Product 都有可用接入 |
| `partial` | 该 Provider 只有部分 Data Product 有可用接入 |
| `needs_configuration` | 已知 Data Product 存在，但当前没有可用 Provider access |
| `unknown_provider` | 系统不知道这个 Provider |

`providers doctor <provider>` 中的 Data Product status 还可以出现：

| status | 含义 |
|---|---|
| `available` | 该 Data Product 当前可以通过此 Provider 获取或连接 |
| `needs_configuration` | 该 Data Product 需要账号、凭证或 provider 配置 |
| `not_available` | 系统知道这个 Data Product，但当前还没有对应实现或该 Provider 不支持 |

诊断某个 Provider：

```bash
kairospy providers doctor massive
```

这个命令可以显示具体 Data Product 是否 `available`，但默认仍不暴露 `ProviderConnector`、`DataProductBuilder`、`ProductSourceBinding`、`DatasetRelease` 这类内部名词。

如果用户使用额外 provider 配置：

```bash
kairospy providers doctor massive --provider-config ./providers.massive.json
```

### 0.2 查看 Data Product

推荐命令：

```bash
kairospy data products list
```

兼容命令：

```bash
kairospy data product list
```

输出字段应描述 Data Product 和默认 Dataset，例如 `key`、`title`、`capability`、`provider`、`venue`、`default_dataset_name`、`aliases`。

诊断一个 Data Product：

```bash
kairospy data products doctor massive.equity.ohlcv.1d
```

这个命令接受 Data Product key 或 alias。常规输出应回答：

```text
这个 Data Product 是否 known
当前是否 available
对应哪个 Provider / Venue
默认生成哪个 Dataset
下一步应该执行什么命令
```

### 0.3 使用 Data Product 生成 Dataset

推荐路径：

```bash
kairospy data use massive.equity.ohlcv.1d \
  --as market.ohlcv.equity.us.1d \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00 \
  --for backtest
```

用户应理解为：

```text
使用 Provider massive 提供的 Data Product
生成或刷新 Dataset market.ohlcv.equity.us.1d
并检查它是否适合 backtest 使用
```

常规成功输出应强调：

```text
Dataset
Status
Coverage
Quality summary
Provider
Venue
```

不应让用户在默认路径里选择或理解 `Release` / `Revision`。旧的 `data releases`、`data acquire`、`data copy --release` 等命令属于兼容和内部运维入口，后续需要逐步收敛到 `Dataset` / `Snapshot` 术语。

### 0.4 何时使用 Snapshot

只有在下面场景才显式使用 Snapshot：

- 固定某次研究或回测的输入数据。
- 复现某次历史结果。
- 审计数据构建证据。
- 比较两次构建产物的 content hash、coverage 或 quality。

不推荐把 Snapshot 当成常规数据版本管理方式。数据语义变化时，应创建新的 `dataset_id`。

## 1. 使用内置 Provider

内置 Provider 的常规路径是：

```bash
kairospy providers list
kairospy providers doctor massive
kairospy data products doctor massive.equity.ohlcv.1d
kairospy data use massive.equity.ohlcv.1d \
  --as market.ohlcv.equity.us.1d \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00 \
  --for backtest
```

如果需要额外配置：

```bash
kairospy data use <product> \
  --as <dataset> \
  --start <iso-start> \
  --end <iso-end> \
  --provider-config ./providers.json
```

## 2. 接入 Python Provider Extension

当用户已有 Python SDK wrapper、内部 API client 或历史代码时，推荐使用 Python in-process extension。

`providers.json`：

```json
{
  "provider_extensions": [
    {"path": "./my_provider.py"}
  ]
}
```

`my_provider.py`：

```python
from kairospy.data import (
    AcquisitionEstimate,
    DataProductContract,
    DataProductDefinition,
    DatasetKey,
    DatasetLayer,
    DatasetStorageKind,
    QualityLevel,
    SourceBinding,
)

KEY = "market.my_provider.signal.1d"


def products(context):
    product = DataProductDefinition(
        DatasetKey(KEY),
        "My Provider daily signal",
        DatasetLayer.CANONICAL,
        "Daily signal from my provider.",
        {"provider": "my-provider", "frequency": "1d"},
        "period_start",
        sources=(SourceBinding("my-provider", "internal", 100, QualityLevel.WORKSPACE, ("python",)),),
    )
    return (DataProductContract(
        product,
        "canonical/my-provider/signal/1d",
        "market.my_provider.signal.1d.v1",
        {"supported_products": ["equity"]},
        DatasetStorageKind.TABULAR,
        "1",
        "signal",
        QualityLevel.WORKSPACE,
    ),)


class MySignalBuilder:
    provider = "my-provider"

    def supports(self, logical_key):
        return logical_key == KEY

    def estimate(self, request):
        return AcquisitionEstimate(1, cost_class="internal")

    def acquire(self, request):
        # Return a DatasetRelease after writing governed dataset files.
        raise NotImplementedError


def register(registry, context):
    registry.register(MySignalBuilder(), products(context))
```

验证：

```bash
kairospy providers doctor my-provider --provider-config ./providers.json
kairospy data products doctor market.my_provider.signal.1d --provider-config ./providers.json
```

## 3. 接入外部进程 Provider

当用户已有 C++、Rust、旧 Python 脚本、独立行情网关或高性能下载器时，不需要改写成 KairoSpy 内部类。可以让外部进程通过 stdin 接收 request JSON，并向 stdout 输出 artifact manifest。

`providers.json`：

```json
{
  "provider_extensions": [
    {
      "kind": "external_process",
      "provider": "my-process",
      "venue": "internal",
      "command": ["./my_downloader", "--format", "kairospy-json"],
      "timeout_seconds": 300,
      "products": [
        {
          "logical_key": "market.my_process.signal.1d",
          "title": "My process daily signal",
          "primary_time": "period_start",
          "fields": ["period_start", "symbol", "value"],
          "dimensions": {"asset_class": "equity", "frequency": "1d"}
        }
      ]
    }
  ]
}
```

外部进程 stdout 输出 file-backed Source artifact manifest：

```json
{
  "artifact_kind": "source",
  "files": [{"path": "./rows.csv"}],
  "fields": ["period_start", "symbol", "value"],
  "row_count": 1000
}
```

`rows.csv` 至少需要包含 `products[].fields` 声明的字段。KairoSpy 会负责：

- 校验 CSV 字段。
- 生成 Dataset manifest、schema、lineage、coverage、quality、release metadata。
- 注册 catalog release。
- 校验 acquire 返回的 provider 和 Data Product 是否匹配。

使用：

```bash
kairospy data use market.my_process.signal.1d \
  --as market.my_process.signal.1d \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-01-02T00:00:00+00:00 \
  --provider my-process \
  --provider-config ./providers.json
```

`kairospy data acquire --dataset ...` 仍保留为高级/兼容入口。它的完成输出也应使用 Dataset 术语；内部 build record id 不作为常规用户字段暴露。

## 实现边界：背景

当前 `kairospy/connectors` 已经按 provider 分组，例如 `binance`、`massive`、`deribit`、`ibkr`。这符合用户心智，但代码里存在一个关键混淆：`connector` 同时表示“如何接入外部系统”和“如何把外部数据整理成 Data Product”。

这两个概念必须分开：

```text
用户看到：Provider、Data Product、Dataset
内部实现：ProviderConnector、ProductSourceBinding、DataProductBuilder、DatasetPublisher、DatasetBuildRecord
```

如果不拆开，后续会持续出现几个问题：

- Data Product 暴露给用户后，可能没有真正支持该 logical key 的外部接入实现。
- provider endpoint 越多，connector class 越碎，现货、合约、期权、日频、小时频、raw、adjusted 会继续分裂。
- provider 接入代码会重复实现 catalog dataset metadata、quality、coverage、schema、parquet publishing。
- 用户旧代码、C++/Rust 传输层、独立进程或低延迟网关很难复用当前 Data 治理能力。
- 未来 execution、account、transfer 也需要 connector 边界，不能被历史数据下载模型限制住。

本文目标是定义两个内部边界，同时保持用户接口简单：

```text
用户心智：Provider -> Data Product -> Dataset

内部边界：
ProviderConnector 层：负责外部系统接入。
DataProductBuilder 层：负责数据产品构建和治理。
```

后续改造应围绕这两个边界推进，而不是围绕某一个 provider 或某一个 endpoint 打补丁。

## 实现边界：术语决策

避免把 `Adapter` 作为核心术语。它过于笼统，无法说明对象到底是 provider、service、resource、transport、codec、binding，还是 data product builder。

用户侧推荐术语如下。

| 名称 | 含义 |
|---|---|
| `Provider` | 外部数据或交易服务提供方，例如 Massive、Binance、IBKR |
| `Data Product` | 可复用的数据产品定义，例如 US equity daily OHLCV |
| `Dataset` | 用户在研究或回测中直接使用的数据资产，默认应稳定且尽量不可变 |
| `Live View` | 受 freshness / health 约束的实时数据视图 |

内部实现术语如下。

| 名称 | 所属层 | 含义 |
|---|---|---|
| `ProviderConnector` | `connectors/` | 外部系统能力入口，用户文档中通常简称为 Provider |
| `ProviderService` | `connectors/` | provider 下的一组业务能力，例如 historical market data、live market data、execution、account、reference |
| `ProviderResource` | `connectors/` | 外部可访问资源，例如 aggregate bars、klines、depth stream、order endpoint |
| `Transport` | `connectors/` | HTTP、WebSocket、FIX、gRPC、native process、shared memory 等传输实现 |
| `ProviderCodec` | `connectors/` | provider raw payload 与 provider-neutral source artifact 的编码/解码 |
| `SourceArtifact` | 边界对象 | 外部来源证据、raw payload、receipt、fingerprint、provider metadata |
| `ProductSourceBinding` | 边界对象 | Data Product 与 provider service/resource 的内部绑定关系 |
| `DataProductBuilder` | `data/` | 根据 Data Product contract 构建 historical dataset 或 live view 的数据产品工具 |
| `IngestionPipeline` | `data` | task plan、source artifact、canonical transform、quality、publishing 的内部编排 |
| `DatasetPublisher` | `data/` | 统一写 parquet、metadata、content hash、catalog、alias、build record |
| `DatasetBuildRecord` | `data` | 内部构建记录，包含 coverage、lineage、quality、content hash；当前代码里可能仍叫 `DatasetRelease` 或 `DatasetRevision` |
| `DatasetSnapshot` | `data` | 高级冻结引用，只在研究输入固定、审计或复现场景显式使用 |

现有 [kairospy/data/acquisition.py](/Users/zhaoqian/Code/Github/trader/kairospy/data/acquisition.py) 里的 `ProviderConnector` 语义上更接近 `DataProductBuilder`。短期可以保留兼容 alias，但新增设计应使用更准确的名字。

## 实现边界：公开接口原则

公开接口应围绕用户任务组织，不围绕内部类名组织。

用户侧只需要理解：

```text
Provider
  -> 提供数据、行情、账户、交易能力

Data Product
  -> 描述可复用的数据产品

Dataset
  -> 用户在研究、回测、生产中直接使用的数据资产
```

推荐的 CLI/API 心智：

```bash
kairospy providers list
kairospy providers doctor massive
kairospy data products list
kairospy data use massive.equity.ohlcv.1d --as market.ohlcv.equity.us.1d
kairospy data describe market.ohlcv.equity.us.1d
```

用户不应该在常规工作流中看到：

```text
ProviderConnector
ProviderService
ProviderResource
ProductSourceBinding
DataProductBuilder
DatasetPublisher
```

这些是内部工程边界，只有高级扩展、调试、插件开发和维护文档需要暴露。

Dataset 默认应该被视为稳定数据资产。如果数据语义发生变化，优先使用新的 dataset id，而不是在同一个 dataset 下制造用户可见的多版本复杂度。例如：

```text
market.ohlcv.equity.us.1d.vendor_adjusted
market.returns.equity.us.1d.internal_adjusted
```

内部仍可以记录 build record、content hash、coverage、lineage、quality，用于审计和复现。用户只有在明确需要冻结研究输入、排查数据变更或做合规审计时，才需要看到 snapshot 细节。

### 3.1 Dataset 身份与变更规则

`Dataset Release` 不应作为默认用户概念。它容易让用户误以为每次 acquire 都在发布一个需要理解和选择的版本，这对常规研究和回测是过度设计。

推荐规则：

- `Dataset` 是用户直接使用的稳定资产，用户主要关心 `dataset_id`。
- `Data Product` 描述如何构建或刷新这个 dataset。
- 如果数据语义、复权逻辑、字段含义、主键、时间边界或 point-in-time 假设发生变化，应创建新的 `dataset_id`。
- 如果只是补齐缺失区间、重跑相同语义的数据、修复传输失败或刷新最新数据，应保持同一个 `dataset_id`。
- 内部可以记录 `build_id`、`content_hash`、`lineage`；只有冻结输入时才生成或暴露 `snapshot_id`。
- 只有在 `--pin`、`--freeze`、`--audit`、`--debug` 或 workspace/backtest 需要固定输入时，才显式暴露 snapshot。
- `data acquire` 作为兼容/高级入口时，完成输出也应默认叫 Dataset，不再使用 `Kairos Data Release` 或顶层 `release_id`。

换句话说，dataset id 承担语义版本职责；build record 承担工程审计职责；snapshot 承担用户主动冻结输入的职责。

用户侧推荐输出：

```text
Dataset: market.ohlcv.equity.us.1d
Status: ready_for_backtest
Coverage: 2026-01-01 to 2026-02-01
```

高级调试或冻结场景才显示：

```text
Dataset: market.ohlcv.equity.us.1d
Snapshot: snap_20260201_...
Content hash: sha256:...
```

不推荐的默认输出：

```text
DatasetRelease: rel_...
Revision: rev_...
```

Provider 扩展的用户配置也应该使用业务化字段，而不是内部 binding 类名：

```yaml
providers:
  massive:
    credentials: env:MASSIVE_API_KEY

data_products:
  us_equity_daily:
    product: market.ohlcv.equity.us.massive.1d.vendor_adjusted
    provider: massive
    source:
      resource: equity_ohlcv
      interval: 1d
      view: vendor_adjusted
```

系统内部可以把这段配置解析成 `ProductSourceBinding`，但用户不需要手写 Python binding 对象。

## 实现边界：内部分层

推荐内部分层如下：

```text
User / CLI / Notebook
  -> DataProductContract
  -> AcquisitionPlan
  -> ProductSourceBinding
  -> DataProductBuilder
  -> ProviderConnector.ProviderService
  -> ProviderResource / Transport / ProviderCodec
  -> SourceArtifact
  -> IngestionPipeline
  -> DatasetPublisher
  -> Dataset / LiveView
```

内部关键原则：

- Data 拥有数据契约、发布物、质量、freshness、可查询性、可回放性和用途准入。
- Connectors 拥有 provider credentials、endpoint、transport、pagination、retry、rate limit、stream lifecycle。
- `ProviderConnector` 不直接发布 Dataset，也不直接写 build record 或 snapshot。
- `DataProductBuilder` 可以调用 `ProviderConnector`，但不应该知道 provider 的 URL、socket、签名细节。
- 同一个 `ProviderResource` 可以绑定多个 Data product。
- 同一个 Data product 可以有多个 provider source candidate。
- Execution、Account、Transfer 是 connector service，不属于 Data connector。

## 实现边界：从 kairos_v2 吸收什么

旧版 `/Users/zhaoqian/Code/kairos_v2` 最值得吸收的是运行时边界，而不是整体搬迁。

### 5.1 Core 与外部接入分离

kairos_v2 的 `kairos-core` 保持自包含领域模型，外部数据由 system 层填充。这个思想应保留：

```text
Data / Trading contract 稳定
Provider 接入可替换
Transport/runtime 可替换
```

当前 KairoSpy 不应让 Data product contract 依赖 Massive/Binance 具体 client。

### 5.2 Provider 下组合多个 Service

kairos_v2 里一个 venue/provider 可以组合 market request、market stream、trading、user state。当前 `connectors/` 也应采用这个模型：

```text
ProviderConnector("binance")
  HistoricalMarketDataService
  LiveMarketDataService
  ReferenceService
  ExecutionService
  AccountService

ProviderConnector("massive")
  HistoricalMarketDataService
  LiveMarketDataService
  ReferenceService
```

不要让 `BinanceSpotKlineDailyConnector`、`BinanceUsdmKlineHourlyConnector`、`MassiveDailyAdjustedConnector` 这类顶层类无限扩张。

### 5.3 Worker / Business 分层

kairos_v2 的 Worker 只处理协议、网络事件、raw parse；Business 层负责 symbol、market、account、subscription、validation。当前可吸收为：

```text
connectors ProviderResource / Transport / ProviderCodec
  负责 provider 协议

connectors ProviderService
  负责 provider 内部 service 编排、health、rate limit、subscription lifecycle

data DataProductBuilder
  负责 Data product 语义、coverage、schema、quality、dataset/build record/snapshot
```

### 5.4 Command / Event 与 Readiness

kairos_v2 的 command/event/readiness 思想对未来 execution 和 live data 很重要：

- `subscribe`、`place_order`、`cancel_order` 是 command。
- provider 回包、stream event、order update 是 event。
- 行情 ready、账户流 ready、交易通道 ready 需要显式 readiness state。

这些应进入 `connectors/` 的 service contract，不应进入 historical Data acquisition 的临时逻辑。

### 5.5 Catalog / Identity

kairos_v2 的 instrument catalog、dense index、provider symbol mapping、trading rules 值得吸收。当前 Data 和 Execution 都需要稳定 identity：

- Data 需要 point-in-time instrument identity、symbol mapping、corporate action reference。
- Execution 需要 tick size、lot size、min notional、venue symbol、交易时段。

这部分应由 `ReferenceService` 和 KairoSpy 的 reference/catalog 层共同承接，不能散落在每个 market data 或 execution connector 里。

### 5.6 高性能 Runtime 边界

kairos_v2 的 reactor、network selector、SHM/ring buffer 适合作为未来数据面的实现参考。当前文档只需要保留边界：

```text
Python 控制面：plan、register、health、audit、risk gate
Rust/C++/外部进程数据面：download、stream decode、IPC、shared memory、low latency event delivery
```

不要现在把旧版 orchestrator/hub 整体搬进 Python 项目。

## 实现边界：ProviderConnector 层

`ProviderConnector` 是外部接入层，位于 `kairospy/connectors`。它的职责是让 KairoSpy 可以可靠地接入外部 provider 或 venue。

### 6.1 职责

`ProviderConnector` 负责：

- provider identity 和 capabilities。
- credentials、session、clock sync、request signing。
- HTTP/WebSocket/FIX/native SDK/gRPC/process/SHM transport。
- endpoint/resource request 构造。
- pagination、retry、rate limit、resume。
- stream reconnect、sequence、heartbeat、drop detection。
- provider raw payload parse。
- source receipt、request fingerprint、raw archive。
- health、readiness、diagnostics。
- execution/account/transfer 这类副作用 service 的接入。

`ProviderConnector` 不负责：

- Data product 的 logical key 语义。
- canonical dataset layout。
- Data quality profile。
- snapshot/content hash 规则。
- catalog dataset、build record 和 snapshot 注册。
- latest-workspace/latest-backtest alias。
- workspace/backtest/production 用途准入。

### 6.2 ProviderConnector 输出

历史数据接入输出 `SourceArtifact` 或 source batch：

```python
SourceArtifact(
    provider="massive",
    resource="stocks_aggregates",
    request_fingerprint="...",
    receipt_path="source/provider=massive/.../receipt.json",
    files=(...),
    coverage_hint={...},
    schema_hint={...},
)
```

实时行情输出 provider event 或 canonical stream event，但 live view 的治理仍由 Data 负责：

```text
ProviderEvent
  provider
  venue
  resource
  received_at
  sequence
  payload
```

Execution 输出 execution ack/report，不经过 Data：

```text
ExecutionCommand -> ExecutionAck / ExecutionReport
```

### 6.3 推荐接口草案

```python
class ProviderConnector(Protocol):
    provider_id: str

    def services(self) -> Mapping[str, "ProviderService"]:
        ...

    def health(self) -> Mapping[str, object]:
        ...


class ProviderService(Protocol):
    service_id: str
    service_kind: str

    def resources(self) -> Mapping[str, "ProviderResource"]:
        ...


class HistoricalMarketDataService(ProviderService, Protocol):
    def estimate(self, request: "ProviderDataRequest") -> "ProviderEstimate":
        ...

    def fetch(self, request: "ProviderDataRequest") -> "SourceArtifact":
        ...
```

`ProviderDataRequest` 使用 provider-neutral 字段：

```python
ProviderDataRequest(
    resource="equity_ohlcv",
    venue="us-securities",
    instruments=("AAPL", "MSFT"),
    start=...,
    end=...,
    params={"interval": "1d", "view": "vendor_adjusted"},
)
```

### 6.4 Provider 内部不按 endpoint 拆顶层类

以 Massive 为例，应该有一个 historical market data service：

```text
MassiveProviderConnector
  MassiveHistoricalMarketDataService
    resource: equity_ohlcv
    resource: option_trades
    resource: corporate_actions
  MassiveReferenceService
    resource: equity_tickers
```

以 Binance 为例：

```text
BinanceProviderConnector
  BinanceHistoricalMarketDataService
    resource: klines
    resource: agg_trades
  BinanceLiveMarketDataService
    resource: depth
    resource: book_ticker
  BinanceExecutionService
    resource: spot_order
    resource: futures_order
  BinanceAccountService
```

现货、USD-M 合约、期权不应导致 provider connector 顶层分裂。它们是 service/resource 参数、capability 和 binding 的差异。

## 实现边界：数据结构归属

`connectors` 应该有自己的数据结构，但不能把所有结构都定义在 `connectors` 里。推荐使用三层数据结构：

```text
Provider DTO
  -> Provider Boundary Model
  -> Trading / Data Model
```

这三层的归属和使用场景不同。

| 类型 | 归属 | 是否对外稳定 | 典型例子 |
|---|---|---|---|
| Provider DTO | `connectors/{provider}` | 否 | `MassiveAggregateBarPayload`、`BinanceKlinePayload`、`BinanceOrderResponse` |
| Provider Boundary Model | `connectors/artifacts.py` 等共享模块 | 是 | `SourceArtifact`、`ProviderEvent`、`ProviderHealth`、`ProviderEstimate` |
| Trading Model | `trading` / `ports` | 是 | `InstrumentId`、`OrderRequest`、`OrderAck`、`ExecutionReport`、`Position` |
| Data Model | `data` | 是 | `DataProductContract`、`AcquisitionPlan`、`Dataset`、`DatasetBuildRecord`、`DatasetSnapshot`、`LiveView` |

### 7.1 Provider DTO

Provider DTO 表达外部系统原始结构，由 provider connector 自己拥有。

```text
MassiveAggregateBarPayload
BinanceKlinePayload
BinanceOrderResponse
IbkrPositionPayload
```

Provider DTO 可以贴近外部 API，不要求稳定，也不要求对 Data 或 Trading 友好。它们允许保留 provider 特有字段、命名、枚举和异常语义。

Provider DTO 不应成为跨层接口。Data 层不应该知道 Massive 原始 JSON 字段，Execution 上层也不应该直接依赖 Binance order response。

### 7.2 Provider Boundary Model

ProviderConnector 对外应暴露 provider boundary model，而不是 provider DTO。

历史数据接入：

```text
Massive raw response
  -> Massive DTO
  -> SourceArtifact / provider-neutral source rows
  -> DataProductBuilder
  -> Dataset
```

实时行情接入：

```text
Binance WebSocket payload
  -> Binance DTO
  -> ProviderEvent
  -> LiveCapture / FreshnessGate
  -> LiveView
```

Provider boundary model 用来承载 provider-neutral 的接入证据、health、sequence、estimate、receipt、source files 等信息。它是 DataProductBuilder、LiveCapture、diagnostics、external process connector 之间的稳定接口。

### 7.3 Trading Model

涉及核心交易语义时，connector service boundary 应接收或返回 Trading / Ports 层结构。

例如 execution：

```text
OrderRequest
  -> BinanceOrderRequestPayload
  -> HTTP response
  -> BinanceOrderResponsePayload
  -> OrderAck / ExecutionReport
```

这里 `OrderRequest`、`OrderAck`、`ExecutionReport` 属于核心交易语义，应保持 provider-neutral。Binance/Massive/IBKR 原始请求和响应仍然属于 provider DTO，只存在于 connector 内部。

Reference 和 Account 也类似：

- instrument identity、venue id、account key 应使用 trading/reference 层结构。
- provider symbol、provider account payload、provider entitlement payload 应保留在 connector 内部。

### 7.4 Data Model

Data product 发布和消费必须使用 Data 层结构。

```text
DataProductContract
AcquisitionRequest
AcquisitionPlan
Dataset
DatasetBuildRecord
DatasetSnapshot
LiveView
DataInputSnapshot
ReplayFeed
```

ProviderConnector 不应该直接构造这些结构，除了通过兼容层临时返回现有 `DatasetRelease`。长期目标是让 DataProductBuilder / DatasetPublisher 统一构造 Data model。

当前代码中的 `DatasetRelease` 可以继续作为 legacy 内部结构存在，但用户文档和常规 CLI 不应强调 Release。更合适的内部概念是 `DatasetBuildRecord`；更合适的高级用户概念是 `Snapshot`，用于冻结研究输入或审计某次构建。

### 7.5 归属规则

新增结构时按下面规则判断归属：

- 外部 API 原样字段、provider enum、provider 错误码：放在 `connectors/{provider}`。
- provider-neutral 接入证据、source artifact、health、estimate、sequence：放在 `connectors/artifacts.py`、`connectors/health.py` 等共享模块。
- 订单、成交、仓位、账户、instrument identity 等核心交易语义：放在 `trading`、`ports` 或 reference 层。
- dataset contract、dataset identity、build record、snapshot、coverage、quality、publication：放在 `data`。

因此，`connectors` 既应该提供自己的数据结构，也应该在 service boundary 使用 trading、data 或 provider boundary 的稳定结构。关键是不让 provider DTO 泄漏到 Data 和 Trading。

## 实现边界：DataProductBuilder 层

`DataProductBuilder` 是 Data 层的数据产品工具，位于 `kairospy/data`。它负责把外部 source 变成可治理、可消费、可审计的 Dataset。

### 8.1 职责

`DataProductBuilder` 负责：

- 根据 `DataProductContract` 做 source selection。
- 根据 catalog metadata 做 coverage/missing range planning。
- 根据 product source binding 生成 provider-neutral request。
- 调用 `ProviderConnector` 获取 `SourceArtifact`。
- 将 source artifact 转换为 canonical table/event。
- 执行 schema validation、primary key validation、boundary policy。
- 执行 quality profile、coverage、freshness、point-in-time 检查。
- 生成 lineage、collection、capabilities metadata。
- 调用 `DatasetPublisher` 发布 Dataset，并记录内部 build record；需要冻结时再生成 snapshot。
- 注册 catalog dataset 和 alias。

`DataProductBuilder` 不负责：

- provider auth 和签名。
- provider endpoint URL。
- provider WebSocket 生命周期。
- execution side effect。
- provider SDK session 细节。

### 8.2 DataProductBuilder 输出

Data 层最终输出 governed artifact：

```text
Dataset
DatasetBuildRecord  # 内部审计记录；当前代码里可能仍叫 DatasetRelease
DatasetSnapshot  # 高级冻结/复现场景
LiveView
DataInputSnapshot
ReplayFeed
```

历史 acquisition 的用户侧结果应是可直接使用的 Dataset。内部可以返回当前代码已有的 `DatasetRelease`，但这是 Data 层 legacy 发布记录，不是 ProviderConnector 的返回结果，也不应成为常规用户心智。

### 8.3 推荐接口草案

```python
class DataProductBuilder(Protocol):
    def supports(self, product_key: str) -> bool:
        ...

    def plan(self, request: "AcquisitionRequest") -> "AcquisitionPlan":
        ...

    def acquire(self, request: "AcquisitionRequest") -> "DatasetBuildResult":
        ...
```

`DatasetBuildResult` 是 Data 层内部结果对象，用户侧仍只看到 Dataset：

```python
DatasetBuildResult(
    dataset_id="market.ohlcv.equity.us.1d",
    build_id="build_...",  # internal audit/debug field
    snapshot_id=None,      # only set for freeze/audit workflows
    status="ready_for_backtest",
    coverage={...},
    quality={...},
)
```

现有 `ProviderRegistry` 可以演化为：

```python
class DataProductBuilderRegistry:
    def register(self, builder: DataProductBuilder, products: tuple[DataProductContract, ...]) -> None:
        ...

    def get(self, product_key: str, provider: str | None = None) -> DataProductBuilder:
        ...
```

短期为了兼容，`ProviderRegistry` 可以继续存在，但它内部注册对象应逐步迁移为 `DataProductBuilder`。

### 8.4 ProductSourceBinding 的归属

`ProductSourceBinding` 是 Data 与 ProviderConnector 的边界对象。它描述“某个 Data product 如何使用某个 provider service/resource 获取 source”。

推荐由 provider 包声明候选 binding，由 Data registry 注册和解释：

```python
ProductSourceBinding(
    product_key="market.ohlcv.equity.us.massive.1d.vendor_adjusted",
    provider="massive",
    service="historical_market_data",
    resource="equity_ohlcv",
    venue="us-securities",
    params={"interval": "1d", "view": "vendor_adjusted"},
    universe_policy="explicit_or_reference_discovered",
    codec="massive.aggregate_ohlcv.v1",
)
```

这样 provider 包可以复用自身能力，Data 层仍掌握 product contract、dataset/snapshot 和用途准入。

## 实现边界：两类用户扩展

用户接入也要区分两类。

### 9.1 Provider 扩展

当用户要接入一个新的外部系统时，应实现 provider extension：

```text
ProviderConnector
  ProviderService
  ProviderResource
  Transport
  ProviderCodec
```

适用场景：

- 新 vendor API。
- 自研 C++ 下载器。
- 已有行情网关。
- 已有交易通道。
- 复用 vendor SDK wrapper。

当前 Python in-process 扩展入口建议使用 Provider config file 的 `provider_extensions`：

```json
{
  "provider_extensions": [
    {"path": "./my_provider.py"}
  ]
}
```

扩展模块暴露两个稳定函数：

```python
def products(context):
    return (MY_DATA_PRODUCT_CONTRACT,)


def register(registry, context):
    registry.register(MyDataProductBuilder(context.root), products(context))
```

`products(context)` 负责声明用户 Data Product contract，`register(registry, context)` 负责把用户的 DataProductBuilder 注册到 provider registry。`context` 是 `ProviderExtensionContext`，包含 `root`、`config_path`、`module_path` 和当前 extension 配置。

### 9.2 Data 扩展

当用户要把已有 source 整理成新的研究数据产品时，应实现 data extension：

```text
DataProductContract
DataProductBuilder
Transform / Normalizer
Quality profile
DatasetPublisher
```

适用场景：

- 从已有 source artifact 生成 features。
- 从 OHLCV 生成 returns。
- 从 option quotes 生成 volatility surface。
- 从交易日志生成 execution analytics。
- 整理本地 CSV/Parquet 到 governed dataset。

### 9.3 旧代码资产接入

旧代码不应强迫改写成内部 Python 类。推荐支持：

```text
Python in-process
External process
gRPC / Arrow Flight
Shared memory / ring buffer
Standard file + manifest
```

当前已支持外部进程输出 file-backed `SourceArtifactManifest` 或 `DatasetArtifactManifest`，由 Data 层继续治理。后续可扩展为让 `SourceArtifactManifest` 先进入 provider source cache，再交给特定 DataProductBuilder 做 canonical transform。

## 实现边界：Manifest 与协议

跨语言或外部进程接入不能依赖 Python object identity。稳定边界应使用 manifest。

### 10.1 SourceArtifactManifest

ProviderConnector 或外部数据面输出 source artifact manifest：

```json
{
  "manifest_version": 1,
  "artifact_kind": "source",
  "provider": "massive",
  "venue": "us-securities",
  "service": "historical_market_data",
  "resource": "equity_ohlcv",
  "transport": "https",
  "request_fingerprint": "...",
  "requested_at": "2026-01-01T00:00:00+00:00",
  "completed_at": "2026-01-01T00:00:10+00:00",
  "coverage_hint": {
    "start": "2026-01-01T00:00:00+00:00",
    "end": "2026-02-01T00:00:00+00:00",
    "boundary": "[start,end)"
  },
  "files": [],
  "receipt": {}
}
```

### 10.2 DatasetArtifactManifest

DataProductBuilder / DatasetPublisher 输出 governed dataset manifest：

```json
{
  "manifest_version": 1,
  "artifact_kind": "dataset",
  "product_key": "market.ohlcv.equity.us.massive.1d.vendor_adjusted",
  "dataset_id": "market.ohlcv.equity.us.1d",
  "build_id": "build_...",
  "snapshot_id": null,
  "provider": "massive",
  "venue": "us-securities",
  "schema_id": "market.ohlcv.equity.us.1d.v1",
  "format": "parquet",
  "coverage": {
    "start": "2026-01-01T00:00:00+00:00",
    "end": "2026-02-01T00:00:00+00:00",
    "boundary": "[start,end)"
  },
  "lineage": {},
  "quality": {},
  "content_hash": "..."
}
```

`build_id` 是内部审计字段。`snapshot_id` 只在冻结输入或审计复现场景写入。常规 `data use` 输出应默认只强调 dataset identity、status、coverage 和 quality summary。

### 10.3 LiveViewManifest

实时 live view manifest 由 Data 层管理，但 health/sequence/drop evidence 来自 ProviderConnector：

```json
{
  "manifest_version": 1,
  "artifact_kind": "live_view",
  "dataset_id": "market.orderbook.crypto.binance.btc-usdt",
  "provider": "binance",
  "venue": "binance",
  "schema_id": "market.orderbook.v1",
  "transport": "websocket",
  "channel": "depth",
  "freshness": {},
  "sequence": {},
  "capture": {},
  "health": {}
}
```

执行命令和回报需要独立 journal，不能复用 Data dataset manifest。

## 实现边界：Historical Data 联动模型

历史数据 acquisition 推荐链路：

```text
Data.use / DatasetClient.acquire
  -> DataProductContract
  -> CoveragePlanner
  -> ProductSourceBinding
  -> DataProductBuilder.plan
  -> ProviderConnector.HistoricalMarketDataService.estimate
  -> AcquisitionPlan
  -> ProviderConnector.HistoricalMarketDataService.fetch
  -> SourceArtifact
  -> CanonicalTransform
  -> QualityGate
  -> DatasetPublisher
  -> Dataset
```

Data 只消费 `SourceArtifact` 或 provider-neutral rows/events，不直接消费 provider client、socket、raw response 或 source cache path。

## 实现边界：Live Data 联动模型

实时数据 connect 推荐链路：

```text
Data.connect
  -> DataProductContract
  -> ProductSourceBinding
  -> ProviderConnector.LiveMarketDataService.subscribe
  -> ProviderEvent stream
  -> LiveCapture / FreshnessGate
  -> LiveView
```

ProviderConnector 负责 stream 生命周期：

- connect / reconnect
- subscribe / unsubscribe
- heartbeat
- sequence
- raw parse
- drop evidence
- provider health

Data 负责 live view 治理：

- schema contract
- capture snapshot
- freshness policy
- query/replay handoff
- live dataset identity
- workspace/shadow/paper/live 用途准入

## 实现边界：Execution 联动模型

Connector 未来必须承接下单、撤单、改单和成交回报。因此 execution 不能被塞进 Data connector。

执行链路应单独建模：

```text
Strategy Intent
  -> Risk Gate
  -> ExecutionService.place_order / place_combo_order
  -> Execution Report Stream
  -> Order State Machine
  -> Reconciliation
  -> Audit Log
```

第一版 `ExecutionService` 已对齐当前 `ports.ExecutionPort` / `OrderRecoveryPort` 的稳定形状：

- `place_order`：单腿订单提交，输入 KairoSpy trading/ports 的 `OrderRequest`，输出 `OrderAck`。
- `place_combo_order`：组合订单提交，只在 provider capability 声明支持时启用。
- `cancel_order`：按 account 和 venue order id 撤单。
- `open_orders`：查询 venue 当前 open order ids。
- `recover_order`：按 client order id / venue order id 做订单恢复和状态证明。
- `environment`、`capabilities`、`institution_id`、`venue_id`：声明交易环境和 provider 能力。

后续增强可以继续在 Execution/Risk/Orchestration 层加入：

- `preview`：无副作用预检查，返回数量精度、价格精度、保证金、交易时段等结果。
- `idempotency_key`：当前可通过 `client_order_id` 承载，长期应成为显式执行 command 字段。
- `replace`：改单能力需要根据 provider capability 暴露。
- `stream_reports`：实时订单和成交回报流。
- `reconcile`：周期性账户、订单、成交对账。
- `health`：交易链路健康检查。

执行侧要吸收 kairos_v2 的两个设计：

- HTTP 下单响应和真实订单状态更新分离。
- `client_order_id`、provider order id、internal order id 的映射由 order state machine 维护。

执行侧的治理要求高于数据侧：

- side effect 必须显式授权。
- command 必须幂等。
- provider order id 与内部 order id 必须可追踪。
- 每个 request / response / execution report 必须审计。
- kill switch 必须在 submit 前生效。
- connector 不能绕过 risk gate。

## 实现边界：Reference 与 Account 的作用

ReferenceService 负责 provider instrument、交易规则、symbol mapping、tick size、lot size、交易时段、合约生命周期。

AccountService 负责余额、仓位、保证金、账户权限和外部账户状态。

MarketDataService 和 ExecutionService 都依赖 ReferenceService，但不应该各自重复实现 symbol 解析。

```text
ReferenceService
  -> Instrument identity
  -> Trading rules
  -> Product lifecycle

MarketDataService
  -> subscription / acquisition universe

ExecutionService
  -> pre-trade validation / order normalization
```

## 实现边界：建议的包结构

短期不需要推倒现有 `kairospy/connectors`。建议新增少量共享模块，并逐步让 provider 包变薄。不要新增过大的 `core/` 包，否则容易变成所有内部对象的堆放处。

```text
kairospy/connectors/
  provider_contracts.py
  services.py
  resources.py
  transports.py
  codecs.py
  artifacts.py
  data_planes.py
  registry.py
  runtime.py
  manifests.py

  binance/
    provider.py
    resources.py
    market_data.py
    execution.py
    account.py
    reference.py
    bindings.py

  massive/
    provider.py
    resources.py
    market_data.py
    reference.py
    bindings.py

  ibkr/
    provider.py
    market_data.py
    execution.py
    account.py
    reference.py
```

Data 层建议新增：

```text
kairospy/data/
  builders/
    product_builders.py
    ohlcv.py
    reference.py
    market_events.py

  acquisition/
    planning.py
    bindings.py
    pipeline.py
    registry.py
    source_artifacts.py

  publishing.py
  quality.py
  catalog.py
  products.py
```

现有 `connectors/*/datasets.py` 可以保留为兼容层，但新增实现应优先使用 provider service + DataProductBuilder。

## 落地状态：Massive Daily 修复

`massive.equity.ohlcv.1d` 的原始问题是 built-in product alias 指向了 daily vendor-adjusted product，但默认 registry 没有注册真正支持该 logical key 的内置 acquisition 能力。

当前已落地的修复：

- 默认 registry 注册 `market.ohlcv.equity.us.massive.1d.raw`。
- 默认 registry 注册 `market.ohlcv.equity.us.massive.1d.vendor_adjusted`。
- `massive.equity.ohlcv.1d` 作为用户 alias 指向 vendor-adjusted daily product。
- `providers doctor massive` 可以看到 daily Data Product 是否 available。
- daily/hourly OHLCV 的 canonical row transform、schema、merge 和 publishing helper 已下沉到 `data/builders/ohlcv.py`，Massive connector 不再拥有这部分 data product 规则。
- Massive daily/hourly OHLCV connector 现在是兼容 wrapper，实际 acquire/estimate/task plan 委托给 `EquityOhlcvDataProductBuilder`。

不应只补一个特殊 case。推荐改造成：

```text
connectors/massive/
  MassiveProviderConnector
  MassiveHistoricalMarketDataService
  MassiveAggregateBarsResource
  MassiveVendorArchiveClient
  MassiveAggregateOhlcvProviderCodec

data/
  EquityOhlcvDataProductBuilder
  EquityOhlcvSourceBinding
  ProductSourceBinding(US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY)
  ProductSourceBinding(US_EQUITY_MASSIVE_RAW_DAILY)
  ProductSourceBinding(US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY)
  ProductSourceBinding(US_EQUITY_MASSIVE_RAW_HOURLY)
```

Massive provider service 只负责：

- 构造 `/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start}/{end}` 请求。
- 处理 adjusted/raw 参数。
- 处理 pagination、receipt、archive、resume。
- 返回 source artifact 或 provider-neutral aggregate rows。

DataProductBuilder 负责：

- 解析 Data product 的 interval/view/universe。
- full-market 或 explicit instruments 的 universe planning。
- trading calendar 和 `[start,end)` boundary。
- canonical OHLCV schema。
- point-in-time limitation 记录。
- quality/coverage/lineage。
- parquet publishing、catalog dataset、build record 和 snapshot。

这样 daily、hourly、raw、vendor-adjusted 都只是 binding 配置差异，不是四套独立 connector 逻辑。

## 落地状态：迁移计划

建议分阶段推进。

### 第一阶段：命名和兼容层

- 已新增 `connectors/provider_contracts.py`、`connectors/artifacts.py`，承载 ProviderConnector、ProviderService、ProviderResource、SourceArtifact 等基础边界。
- 已补齐 `connectors/services.py`、`connectors/resources.py`、`connectors/transports.py`、`connectors/codecs.py`，为 service/resource/transport/codec 拆分提供稳定薄边界。
- 已新增 `data/builders/product_builders.py`，承载 DataProductBuilder、ProductSourceBinding、DataProductBuilderRegistry。
- 已将现有 `data.acquisition.ProviderConnector` 标记为 legacy 语义，内部向 DataProductBuilder 兼容。
- 已将缺失实现和 acquire 校验中的用户可见错误文案从 `acquisition connector` 收敛为 `Data Product builder` / `Data Product` 术语，并提示 `providers doctor` / `data products doctor`。
- 已将 `data acquire` 完成输出从 `Kairos Data Release` 收敛为 Dataset summary，默认不暴露顶层 `release_id`。
- 不破坏现有 Binance/Deribit/Massive dataset connector。

### 第二阶段：Massive 样板

- 已抽出第一版 `MassiveHistoricalMarketDataService`。
- 已抽出第一版 `MassiveAggregateBarsResource`，负责 Massive aggregate bars 请求、provider cache 命中判断和 `SourceArtifact` 输出。
- 将 `MassiveVendorArchiveClient` 保留为 provider source archive primitive。
- 已新增 `data/builders/ohlcv.py`，承载 canonical OHLCV row transform、schema、merge 和 publishing helper。
- 已新增 `EquityOhlcvDataProductBuilder` 和 `EquityOhlcvSourceBinding`，daily/hourly/raw/adjusted 通过 binding 配置差异复用同一个 Data Product builder 编排器。
- 已注册 Massive daily raw/vendor-adjusted built-in product connector。
- 已修复 `massive.equity.ohlcv.1d` built-in connector 缺失问题。
- Massive daily/hourly wrapper 已改为兼容层，负责创建 provider service 和 source binding，再委托 Data Product builder acquire/estimate/task plan。

### 第三阶段：Data acquisition primitives

- 已将 `AcquisitionRequest`、`AcquisitionEstimate`、`AcquisitionPlan`、`TimeRange`、`AcquisitionLimits` 拆到稳定 `data/acquisition_primitives.py`，legacy `data.acquisition` 保留兼容导出。
- 已新增 `data/builders/planning.py`，承载 `TaskRangePlan`、`UniversePlan`、`DataProductTaskPlan`，让 OHLCV builder 和 external process builder 复用同一套 provider task plan 输出结构。
- 已新增 `DatasetPublisher` facade，统一 DataProductBuilder 注册 governed Dataset build output 的入口。
- 后续高级增强可以继续抽出 source artifact loading 和 quality gate，降低每个 DataProductBuilder 直接接触 catalog/build record/snapshot 的程度。
- 长期目标仍是让 provider connector 不直接注册 catalog dataset、build record 或 snapshot；当前 Massive OHLCV wrapper 已改为委托 DataProductBuilder / DatasetPublisher。

### 第四阶段：用户扩展

- 已支持 Provider config file 的 `provider_extensions[]` 加载 Python provider extension。
- CLI 推荐使用 `--provider-config`；旧 `--connector-config` 保留为兼容参数。
- 已支持 Python `products(context)` 注册用户 Data Product contract。
- 已支持 Python `register(registry, context)` 注册用户 DataProductBuilder。
- 已支持 `kind: external_process` 的 file-backed Dataset artifact manifest。
- 已支持 `kind: external_process` 的 file-backed Source artifact manifest。
- 后续可以支持 SourceArtifactManifest 先进入 provider source cache，再由特定 DataProductBuilder 做 canonical transform；当前 file-backed Source artifact manifest 已可由 external process 交给 Data 层治理。
- 已增加 `kairospy providers list`、`kairospy providers doctor <provider>`。
- 已增加 `kairospy data products list`，并保留 `kairospy data product list` 兼容入口。
- 已增加 `kairospy data products doctor <product>`，用于诊断 Data Product key 或 alias。
- `kairospy data doctor <dataset>` 保留为 Dataset 诊断入口。
- 调试模式可以显示内部 service、resource、binding、builder、build record、snapshot，但默认输出只使用 Provider、Data Product、Dataset 术语。

### 第五阶段：Execution Service

- 已新增 `connectors/execution.py`，定义 `ExecutionService`、`ComboExecutionService` 和 `ExecutionServiceSpec`。
- 第一版 `ExecutionService` 对齐当前 `ports.ExecutionPort` / `OrderRecoveryPort`，使用 trading/ports 的 `OrderRequest`、`ComboOrderRequest`、`OrderAck`、`VenueOrderRecovery`，不暴露 provider DTO。
- Binance execution gateway 已声明 provider service boundary：`spot_execution`、`usdm_futures_execution`、`coinm_futures_execution`、`options_execution` 属于同一个 provider 下的 execution service 差异，不再要求顶层 connector 分裂。
- IBKR 和 simulated execution gateway 已声明 `execution` service boundary。
- 后续增强再引入显式 idempotency command、risk preview、replace、execution report stream、audit、reconciliation。
- kairos_v2 的 order state machine 思想应继续由 Execution/Orchestration 层吸收，不能放进 Data connector。

### 第六阶段：高性能数据面

- 已新增 `connectors/data_planes.py`，定义 `DataPlaneEndpoint`、`ProviderDataPlaneSpec`、`ProviderDataPlane`。
- 高性能能力先作为 provider data plane 描述和发现 contract 存在，不把 gRPC、Arrow Flight、shared memory 直接变成核心依赖。
- `DataPlaneEndpoint.protocol` 可以声明 `external_process`、`grpc`、`arrow_flight`、`shared_memory` 等实现协议。
- `DataPlaneEndpoint.format` 可以声明 `jsonl`、`arrow-ipc`、`parquet`、provider binary frame 等输出格式。
- Rust/C++、网卡优化、长期运行 market data gateway 都应接入 `ProviderDataPlane`，再由 ProviderService / DataProductBuilder 使用，不直接污染 Data product contract。
- 低延迟流如果有 side effect 或交易含义，必须走 Execution/Risk/Audit；纯 market data 可以通过 DataPlane 输出 SourceArtifact 或 live event stream。

## 非目标

本文不要求一次性重写所有 connectors。

本文不要求所有 provider 使用同一种 transport。

本文不把低延迟交易逻辑放进 Data。

本文不让 ProviderConnector 直接发布 Dataset 或写入 build record/snapshot。

本文不要求用户旧代码改写成 KairoSpy 内部类。旧代码可以通过 process、gRPC、manifest 或标准文件产物接入。

## 判断标准

改造后的设计应满足：

- 新增一个 provider 不需要复制 dataset publishing 逻辑。
- 新增一个 endpoint 不需要新增一个完整 connector 顶层类。
- 同一个 resource 可以绑定多个 Data product。
- 同一个 provider 可以同时提供 market data、execution、account、reference。
- 用户可以只扩展 provider 接入，也可以只扩展 Data product 构建。
- Python、C++、旧脚本可以通过同一套 manifest / service contract 接入。
- Data 仍然只面向 Dataset、Live View、质量和用途准入；Snapshot 只在复现和审计场景显式暴露。
- 下单等 side effect 不经过 Data，必须经过 ExecutionService、Risk Gate 和 Audit。
