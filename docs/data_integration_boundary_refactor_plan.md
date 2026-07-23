# Data 与 Integration 边界调整计划

状态：迁移执行中  
日期：2026-07-24  
适用对象：Data Core、Data Product Catalog、Provider Integrations、Live Market Data、Historical Acquisition

## 1. 目标

将 `kairospy.data` 收敛为更底层的 Data Core：只负责数据契约、数据集/数据流身份、存储、读写、manifest、quality/freshness 和通用协议接口。所有 provider-specific 数据产品、driver 选择、外部 API/WS 连接和供应商 symbol 规则迁移到 `kairospy.integrations`。

最终依赖方向必须是单向的：

```text
kairospy.data
  <- kairospy.integrations
  <- kairospy.runtime / kairospy.surface
```

允许：

```text
integrations -> data contracts/types
runtime/surface -> data + integrations
```

禁止：

```text
data -> integrations
integrations -> surface
```

## 2. 为什么要调整

迁移前 `kairospy.data` 内含大量 provider-specific 资产：

- `kairospy/data/products/builtin/binance.py`
- `kairospy/data/products/builtin/massive.py`
- `kairospy/data/products/builtin/deribit.py`
- `kairospy/data/products/registry.py` 中的 `binance.orderbook`、`hyperliquid.perpetual.*`、`massive.*`
- provider-specific symbol 解析，如 Binance `BTCUSDT`、Hyperliquid coin、Massive ticker
- provider-specific live runtime config，如 Binance websocket stream、Hyperliquid subscription、Massive subscription

这些属于 integration/product catalog concern，不属于 Data Core。Data Core 如果知道 Binance、Massive、Hyperliquid、CCXT，就会阻碍后续接入 IBKR、Massive、CCXT Pro、native exchange connector，也会引入循环依赖风险。

近期为了接 `ccxt-pro`，临时出现了一个需要回收的方向：

```python
# kairospy/data/products/registry.py
from kairospy.integrations.live_market_data import ...
```

这条依赖应该移除。正确做法是由 runtime/surface/application service 读取 data intent 和 project config，再调用 integrations。

当前执行口径：

- 不保留 `kairospy.data.products.*`、`kairospy.data.acquisition.*`、`kairospy.data.extensions.*` 兼容导出。
- Data Product catalog、resolver、provider registry、acquisition planning/builders、provider extensions、HTTP downloader helper 由 `kairospy.integrations` 承接。
- `kairospy.data` 只保留 Data Core facade、contracts、storage、catalog、protocols、quality、streams、用户自定义 file/protocol add/connect 服务。

## 3. 目标边界

### 3.1 `kairospy.data` 保留

Data Core 负责：

- `DatasetStore`
- `DatasetLayout`
- `DatasetReader`
- `DatasetWriter`
- `DataApi` 的 read/write/alias 基础能力
- `DataProductContract`
- `DataProductDefinition`
- `SourceBinding`
- `LiveDataRequest`
- `HistoricalDataRequest`
- `DataProtocolRegistry` 作为通用接口
- `LiveViewManifest`
- freshness/quality 通用逻辑
- stream/dataset id normalization

Data Core 不负责：

- 注册 Binance/Massive/Deribit/Hyperliquid built-in products
- 解析 provider driver
- 读取 `providers.*.services.*.driver`
- 构建 `ccxt.pro` exchange
- 下载 Massive flat files
- 下载 Binance archive
- 调 provider REST 分页 API
- 拼 Binance stream name
- 拼 Hyperliquid subscription
- 创建 Massive client
- 决定 provider credential

### 3.2 `kairospy.integrations` 承接

Integrations 负责：

- provider data product declarations
- provider acquisition builders
- provider live source builders
- provider driver dispatch
- credential/config resolution
- external SDK/API/WS lifecycle
- provider symbol mapping
- CCXT/CCXT Pro/native connector selection

目标目录：

```text
kairospy/integrations/data_products/
  __init__.py
  binance.py
  deribit.py
  massive.py
  hyperliquid.py
  ccxt.py

kairospy/integrations/live_market_data.py
kairospy/integrations/historical_market_data.py
```

### 3.3 `runtime` / `surface` 负责协调

Runtime/surface 负责 wiring：

```text
user request / run config / workspace binding
  -> data resolves generic product/data intent
  -> integration catalog resolves provider product/source
  -> integration builder creates connector/runtime source
  -> data writes manifest/state
  -> runtime supervises event source
```

也就是说，data 不主动调用 integrations；上层 orchestrator 同时依赖两边。

### 3.4 用户命令与下载实现分离

用户入口命令可以继续保持 data 语言，因为用户意图是“获取或连接数据”：

```bash
kairos data use ...
kairos data acquire ...
kairos data connect ...
```

但这些命令背后的 provider-specific 下载、拉取、订阅和标准化实现应该位于 integrations：

```text
kairospy/integrations/acquisition/binance.py
kairospy/integrations/acquisition/massive.py
kairospy/integrations/acquisition/deribit.py
kairospy/integrations/acquisition/hyperliquid.py
kairospy/integrations/acquisition/ccxt.py
```

目标关系：

```text
surface command
  -> parse user data intent
  -> resolve integration-provided data product/source plan
  -> integration downloads/fetches/subscribes/normalizes
  -> Data Core writer persists rows/events/manifests
```

因此：

- CLI UX 可以留在 `data` 命名空间。
- provider 下载实现不能留在 `kairospy.data`。
- data 层只接收 normalized rows/events/artifacts 并负责存储。
- integration 层负责外部 API、credential、retry、rate limit、pagination、archive URL、websocket lifecycle。

## 4. 目标调用链

### 4.1 Live connect

目标：

```text
DataApi.connect("binance.orderbook", instruments=["BTCUSDT"])
  -> surface/live data orchestration
  -> Data Core validates dataset/live view shape
  -> Integration catalog resolves provider product
  -> Integrations reads kairos.toml provider service
  -> driver = ccxt-pro
  -> build CcxtOrderBookEventSource config
  -> Data Core writes live manifest
```

Data manifest 可以记录 resolved runtime source summary：

```json
{
  "provider": "binance",
  "service": "live_market_data",
  "driver": "ccxt-pro",
  "exchange_id": "binance",
  "symbol": "BTC/USDT",
  "channel": "orderbook"
}
```

但 data 不能根据 `driver` 分支构建 exchange。

### 4.2 Runtime bind

目标：

```text
runtime reads LiveViewManifest
  -> integrations.build_live_market_data_event_source(...)
  -> runtime supervises EventSource
```

### 4.3 Historical acquisition

目标：

```text
data use / acquire
  -> surface orchestration
  -> integration provider catalog selects builder
  -> builder fetches/normalizes rows
  -> Data Core writer persists rows
```

## 5. 配置模型

Provider service config 属于 integrations：

```toml
[providers.binance.services.live_market_data]
driver = "ccxt-pro"
exchange_id = "binance"
timeout_ms = 30000

[providers.binance.services.live_market_data.options]
defaultType = "spot"
fetchMarkets = { types = ["spot"] }

[providers.okx.services.live_market_data]
driver = "ccxt-pro"
exchange_id = "okx"
timeout_ms = 30000

[providers.hyperliquid.services.live_market_data]
driver = "ccxt-pro"
exchange_id = "hyperliquid"
timeout_ms = 30000

[providers.hyperliquid.services.live_market_data.options]
defaultType = "swap"
```

Data request 不应该包含 driver：

```python
data.connect("binance.orderbook", instruments=["BTCUSDT"])
```

或未来的 Space/Stream 入口：

```python
data.connect("crypto.binance.btc_usdt.orderbook")
```

## 6. 分阶段迁移

### Phase 0：边界审计与保护

目标：先防止进一步扩大循环依赖。

任务：

- 增加 architecture boundary test：`kairospy.data` 不允许 import `kairospy.integrations`。
- 标记并移除临时违规点：`data/products/registry.py -> integrations.live_market_data`。
- 列出所有 provider-specific 字符串在 `kairospy/data` 中的位置。
- 冻结新增 provider built-in product 到 data 目录的做法。

验收：

- 新测试能明确暴露当前违规。
- 后续迁移每完成一段，违规列表减少。

### Phase 1：建立 integration product catalog

目标：在 integrations 中建立新的 provider catalog，并删除旧 data-owned product modules；不做 data 兼容导出。

任务：

- 新增 `kairospy/integrations/data_products/`。
- 将 provider product declaration 复制或搬迁为 integration-owned modules：
  - Binance
  - Massive
  - Deribit
  - Hyperliquid
  - CCXT generic crypto live products
- 提供统一注册入口：

```python
def register_integration_data_products(registry, config, root): ...
```

- 暂时让旧 `data.products.builtin` 通过 compatibility import 读取 integration catalog。

验收：

- 现有 product list 和 product key 不变。
- 旧测试不需要大规模重写。
- 新测试验证 integration catalog 可独立注册 products。

### Phase 2：把 provider-specific runtime config 移出 data

目标：移除 `BuiltInLiveDataProtocol` 中的 provider 分支。

任务：

- 新增上层 orchestration function，例如：

```python
kairospy.surface.live_data_binding.resolve_live_data_binding(...)
```

或：

```python
kairospy.runtime.live_data_binding.resolve_live_data_binding(...)
```

- 该函数同时接收：
  - project config
  - data product request
  - integration catalog
  - Data Core writer/manifest writer
- 将 `_binance_quote_runtime_config`、`_massive_runtime_config`、`_hyperliquid_runtime_config` 迁移到 integrations。
- `BuiltInLiveDataProtocol` 退化为通用 protocol wrapper 或删除 provider branches。

验收：

- `kairospy.data` 不 import `kairospy.integrations`。
- data connect 仍可配置 live manifest。
- Binance native、Massive、Hyperliquid、CCXT Pro live config 仍有测试覆盖。

### Phase 3：把 historical acquisition provider registry 移出 data

目标：`ProviderRegistry`、acquisition request/plan/evidence、provider extension bootstrap 全部移到 integrations；data 不保留 acquisition 包。

任务：

- 将 `data/extensions/bootstrap.py` 中的 provider-specific imports 移到 `integrations.data_products.bootstrap`，并删除 `data/extensions` 源码。
- `default_provider_registry(...)` 改为 integration-owned。
- data 不保留 provider registry / acquisition registry。
- configured Massive product handling 移到 Massive integration。
- 将 provider-specific downloader/acquisition 实现迁移到 `kairospy.integrations.acquisition.*`。
- surface 的 `kairos data use/acquire` 命令只调用上层 orchestration，不直接 import provider downloader。

验收：

- Massive/Binance/Deribit historical acquisition 测试通过。
- data extensions 不再 import provider connector modules。
- `kairospy.data` 不再包含 Massive/Binance/Deribit 下载实现或默认注册逻辑。

### Phase 4：兼容层收敛

目标：用户入口不破坏，内部依赖方向变干净；不通过 data 做兼容导出。

任务：

- 删除 `kairospy.data.products.builtin` / `kairospy.data.products.registry` / `kairospy.data.products.resolver`。
- 需要兼容的用户命令由 surface/CLI 显式调用 integrations。
- CLI 输出不变。
- 文档统一使用“integration-provided data product”，不再暗示 product 由 Data Core 拥有。

验收：

- 老 product key 可用。
- 新 integration catalog API 可用。
- architecture boundary test 通过。

### Phase 5：清理 data provider-specific 残留

目标：Data Core 不含 provider 知识。

任务：

- 删除或迁出 data 目录中的 provider-specific symbol parser。
- 删除 data 目录中的 provider-specific runtime config builder。
- 删除 data 目录中的 provider-specific default product list。
- 保留 generic aliases/resolver，但数据源材料由 integrations 注入。

验收：

- `rg "binance|massive|hyperliquid|deribit|ccxt|okx|kraken" kairospy/data` 只剩：
  - 测试 fixture 文本
  - user-provided metadata examples
  - compatibility shim 的明确注释

## 7. 迁移时保持兼容的规则

- 不改现有 Dataset ID。
- 不改现有 product key，除非同时提供 alias。
- 不改现有 manifest schema，只新增 optional fields。
- 不删除 `BuiltInDataProductRegistry`，先降级为 compatibility facade。
- 不让 connector 直接写最终目录；写入仍走 Data Core writer/store。
- 不让 data 读取 provider credentials。

## 8. 风险点

### 8.1 过早删除 built-ins

风险：CLI、workspace projection、tests 大量依赖 built-in keys。

处理：先转发，不删除。等 integration catalog 稳定后再删旧实现。

### 8.2 runtime 和 surface 职责混乱

风险：surface 写业务逻辑过多。

处理：抽一个 application service，例如 `runtime/live_data_binding.py` 或 `surface/live_data_binding.py`，surface 只调用它。

### 8.3 manifest 中记录 driver 过多

风险：manifest 变成 connector config dump。

处理：manifest 只记录可审计的 runtime source summary；credential 和 secret 不能写入 manifest。

### 8.4 integration 直接绕过 Data Core

风险：provider builder 自己拼目录，破坏数据一致性。

处理：integration builder 输出 rows/events/artifacts，由 Data Core writer 落盘。

## 9. 建议的第一批代码改动

优先顺序：

1. 加 boundary test，禁止 `kairospy.data` import `kairospy.integrations`。
2. 回撤当前临时 `data -> integrations.live_market_data` import。
3. 新建上层 `live_data_binding` orchestration 模块。
4. 把 `ccxt-pro` driver config 解析放到 orchestration 调 integrations。
5. 将 `data/products/registry.py` 中的 provider runtime config 迁到 `integrations/data_products/*`。
6. 删除旧 `data.products` 入口，让 surface/CLI 直接调用 integration catalog/resolver。

第一步完成后的目标依赖图：

```text
data.products.registry
  no provider driver dispatch

integrations.live_market_data
  ccxt-pro/native driver dispatch

surface/runtime live_data_binding
  data request + project config + integrations source config
```

## 10. 完成定义

迁移完成时应满足：

- `kairospy.data` 没有 provider connector import。
- `kairospy.data` 没有 provider driver dispatch。
- `kairospy.data` 没有 credential/config provider service parsing。
- Provider data products 位于 `kairospy.integrations.data_products`。
- `kairospy.integrations` 可注册 Binance/OKX/Hyperliquid/Massive/IBKR 相关 data products。
- data read/write API 对用户保持稳定。
- runtime/surface 是唯一协调 data 与 integrations 的层。
