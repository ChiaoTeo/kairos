# Binance Connection Boundary Remediation

> 文档状态：设计与迁移任务基线。当前实现已完成 API-family typed connection、Spot principal 收窄、独立 server clock、业务 composition 迁移和 family-scoped quota key；全仓库最终验证仍按本文完成定义执行。

## 1. 任务目标

本文把 Binance connection 的设计问题整理为可执行的架构修复任务。目标不是简单重命名，而是分离：

- Binance participant/provider；
- API family：Spot、USD-M、COIN-M、Options；
- venue product / provider access discriminator；
- principal/account binding；
- HTTP transport；
- provider/IP quota；
- server clock；
- Account、Execution、Market、Reference 的业务 route。

依据：integration-session-and-operation-design.md、domain-provider-taxonomy.md、domain-taxonomy-and-workspace-boundary-remediation.md，以及仓库根目录 AGENTS.md。

## 2. 核心结论

当前 BinanceConnection 存在真实设计问题，不只是命名问题。

它同时扮演 Binance 通用 provider context 和 Binance Spot API connection。当前对象持有 BinanceSpotProviderRuntime，却还能投影 USD-M Futures、COIN-M Futures、Options 和 Margin capability。因此类型系统允许错误的 endpoint/capability 组合，正确性依赖业务 composition 的约定，而不是由 Integration 类型保证。

正确原则：

> Connection 必须表示一个合法、稳定、不可歧义的 provider binding；Spot、USD-M、COIN-M、Options 等 API family 应在构造时确定，并在类型上避免互相混用。

项目总体架构方向没有问题：Integration 负责 provider 原生连接、认证、quota、外部事实和 capability；业务模块负责业务状态与 route；composition 选择具体 provider-native capability；application 不暴露 SDK、raw payload 或 persistence record。

## 3. 现有问题证据

### 3.1 注释、名称和能力矛盾

connection.rs:85 的 BinanceConnection 注释说它是“所有 Binance Spot principals 的 provider/IP scope”，字段也是 BinanceSpotProviderRuntime；但同一对象还能构造 Spot、USD-M、COIN-M、Options catalog，并从 Spot principal 创建 Futures、Options、Cross Margin、Isolated Margin connection。

因此 BinancePrincipalConnection 实际是 Spot root 加其他产品工厂。

### 3.2 一个 rest_base_url 覆盖多个 API family

BinanceConnectionConfig 只有一个 rest_base_url，但 path 和 time endpoint 不同：

| Family | Path family | Time endpoint |
| --- | --- | --- |
| Spot | /api/... | /api/v3/time |
| USD-M | /fapi/... | /fapi/v1/time |
| COIN-M | /dapi/... | /dapi/v1/time |
| Options | /eapi/... | /eapi/v1/time |

因此一个 Spot host 可以被拼接 Futures path，反之亦然。Account composition 目前通过改写 endpoint 并拒绝一次使用多个 endpoint family 来规避，见 crates/business/account/service/src/composition/account.rs:190。真实边界存在于业务代码，却没有进入 Integration 类型。

### 3.3 Spot runtime 不是中性 runtime

crates/kairos-integration/src/services/participants/binance/spot/runtime.rs:53 的 BinanceSpotProviderRuntime 同时包含 HTTP、async HTTP、quota、shared quota、Spot clock 和 principal order quota。其 clock 硬编码 /api/v3/time；Futures/Options 又各自维护 offset。因此不能只改名为 BinanceProviderRuntime，Spot 语义仍会泄漏。

### 3.4 shared quota 没有 API family

当前 shared ledger key 类似：

binance:{environment}:egress:{egress_scope}:request-weight-1m

见 connection.rs:151。它没有区分 API family、REST/WS、rate-limit type、interval 和 command/query lane。

Binance 官方文档说明 rate limit 会因产品和 endpoint 变化，不能因为 participant 都是 Binance 就假设全产品共享一个 lane：[Developer Documentation](https://developers.binance.com/en/docs/introduction)。

### 3.5 Spot principal quota 泄漏

BinancePrincipalOrderQuotaAllocation 固定为 orders_per_10_seconds 和 orders_per_day，并观察 Spot 风格的 x-mbx-order-count-10s / x-mbx-order-count-1d；带有这些 quota 的 runtime 又被 Futures/Options client 复用。Spot 官方文档将 IP request weight 与 account unfilled-order count 分开定义，不能直接推广到 Futures/Options：[Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)。

### 3.6 binding identity 默认 Spot

Spot 使用原始 binding_id，其他产品才追加 .usd-m-futures、.coin-m-futures、.options，见 connection.rs:449。这使 principal 根语义天然偏向 Spot。

## 4. 目标设计

### 4.1 Product-native typed connection

建议建立：

- BinanceSpotConnection；
- BinanceUsdMConnection；
- BinanceCoinMConnection；
- BinanceOptionsConnection。

每个类型拥有自己的 endpoint、domain、clock、quota、principal projection 和 channel constructor。

同一 API key 可以用于多个 family，但必须通过多个明确 family binding 投影，不能从 Spot principal 隐式派生 Futures/Options。

### 4.2 Spot、Margin、Funding

Spot、Cross Margin、Isolated Margin、Funding 是否共用物理 context，必须依据 endpoint family、认证、clock、quota、private channel 和官方 contract 判断。若条件确实相同，可以使用明确命名的 BinanceSpotApiConnection，并投影 spot、cross_margin、isolated_margin(symbol)、funding；不能为了减少文件数量把 Futures 和 Options 也塞进去。

### 4.3 共享 transport，隔离产品 runtime

真正中性的部分可以是私有 BinanceHttpTransport，包含 sync/async HTTP client。产品 runtime 自己拥有 family-specific quota 和 clock。

HTTP client 可以共享；private channel、principal auth、subscription、channel epoch、business route、command state 不能因共享物理资源而混淆。

### 4.4 quota 与 descriptor

quota key 至少表达 participant、environment、egress_scope、api_family、transport、rate_limit_type、interval。是否共享必须由 provider contract 或 manifest 决定；ledger 不保存 secret、pending command、session 或 signer。

ConnectionDescriptor.domain、binding_id 和 API family 应在 typed connection 构造时确定，不能通过 InstrumentType 或运行时 enum 任意切换。SegmentKey、MarketId、InstrumentId 和 source_symbol 不能反解析 provider product。

## 5. 不应采用的方案

- 只把 BinanceSpotProviderRuntime 改名为 BinanceProviderRuntime；Spot clock/quota/header 仍然泄漏。
- 一个万能 BinanceConnection 加 BinanceProduct enum，然后在内部大量 match；这仍是运行时 dispatcher，仍允许错误状态。
- 新增 connection manager、product registry、universal provider adapter、execute(operation, payload) 或跨产品 session manager；除非有当前 caller、明确边界或第二个真实实现。

## 6. 迁移步骤

### Phase 0：冻结事实与测试

建立 family matrix，覆盖 endpoint、time endpoint、order path、principal quota、stream、descriptor、command/query/stream failure semantics。

测试必须证明：family endpoint 不互用；clock endpoint 匹配；descriptor 匹配 connection；quota key 有真实 scope；principal quota 不跨 family；同一 API key 可通过多个独立 binding 使用；一个 family 的 channel failure 不影响另一个 family。

### Phase 1：提取中性 transport

从 BinanceSpotProviderRuntime 提取真正中性的 HTTP transport 和必要调度原语，不要同时提升 Spot clock、Spot quota 或 Spot order limiter。

### Phase 2：建立 typed connection

新增 BinanceSpotConnection 或 BinanceSpotApiConnection、BinanceUsdMConnection、BinanceCoinMConnection、BinanceOptionsConnection。每个 connection 自己持有 family-specific endpoint、domain、clock、quota、principal projection 和 channel constructor。

### Phase 3：迁移 principal/capability

将当前 Spot-root BinancePrincipalConnection 收窄为 Spot，或拆成按 family 绑定的 principal 类型。Spot principal 不再创建 Futures/Options；Futures/Options 不再依赖 Spot runtime 或 Spot principal quota。

### Phase 4：迁移业务 composition

Account、Execution、Market、Reference 逐个使用 typed constructor。route 仍由业务拥有；不从 SegmentKey、MarketId 或 symbol 推导 family；不新增 Integration manager；保留 async-first、blocking-only 和 command/query/stream 交付语义。

### Phase 5：删除旧路径

最后一个 caller 迁移后删除通用 BinanceConnection、Spot-root BinancePrincipalConnection、跨产品 BinanceSpotProviderRuntime、运行时 product dispatch 和无 caller 的兼容 constructor。同步更新 migration status、adapter reference、architecture tests、taxonomy tests。

## 7. 验收标准

### 类型与边界

- Spot connection 无法获得 USD-M capability；
- USD-M connection 无法访问 Spot path；
- COIN-M、USD-M、Options 不能仅靠 enum 参数共用错误 runtime；
- descriptor domain 在构造时确定；
- endpoint family 不依赖调用方字符串约定。

### quota 与 clock

- quota key 包含真实 provider scope；
- 只有同 family、同 egress、同 limiter 才共享 ledger；
- 不同 family 默认隔离 quota；
- principal quota 不跨 family 观察或消耗；
- 每个 family 使用正确 time endpoint。

### command/query/stream

- write 前失败是 NotSent；
- write 后响应丢失是 Indeterminate；
- command 不透明重试；
- query 仅按 provider contract 做有界重试；
- stream 定义 ordering、epoch、reconnect、resync、backpressure 和 duplicate policy；
- 一个 family 的失败不改变另一个 family 的 health。

### 业务边界

- business production code 不导入 Integration services、SDK 或 raw payload；
- composition 只选择 typed capability；
- route、account、canonical identity 仍由业务/Reference 拥有；
- 不从 SegmentKey、MarketId、InstrumentId 或 symbol 反推 provider access；
- 不新增中心 registry、万能 operation facade 或自动跨 provider failover。

## 8. 验证命令

实现后按由窄到宽运行：

    cargo test -p kairos-integration --lib -- --test-threads=1
    cargo test -p kairos-integration --test architecture -- --test-threads=1
    cargo test -p kairos-execution-service --lib -- --test-threads=1
    cargo test -p kairos-account-service --lib -- --test-threads=1
    cargo test --workspace
    uv run pytest -q
    cargo fmt --all -- --check
    git diff --check

静态搜索：

    rg -n "BinanceSpotProviderRuntime|BinanceConnection|BinancePrincipalConnection" crates --glob '*.rs'
    rg -n "api/v3/time|fapi/v1/time|dapi/v1/time|eapi/v1/time" crates/kairos-integration
    rg -n "ConnectionSpec|IntegrationCapability|dyn Connection" crates

若全仓库检查被无关预存失败阻塞，记录精确失败，并保留已通过的最窄验证结果。

## 9. 完成定义

任务只有在以下条件全部满足时完成：

1. participant、API family、principal、trading mode、provider instrument 和 canonical instrument 的所有权分离；
2. Spot 不再是 Binance 通用 connection 的隐式默认产品；
3. connection 类型能阻止错误 endpoint family 和 capability 组合；
4. HTTP transport、quota、clock 和 channel runtime 的共享关系有明确依据；
5. Account、Execution、Market、Reference 已迁移到 typed connection；
6. 旧 Spot-root compatibility facade 已删除；
7. command/query/stream、quota、recovery 和 descriptor 测试通过；
8. migration status、adapter references 和 architecture checks 已更新；
9. 全仓库验证结果已记录。

在此之前，简单重命名 BinanceConnection 或增加 product 参数都不算完成。

## 10. 本次验证记录

- `cargo test -p kairos-integration --lib -- --test-threads=1`：150 tests passed，1 live-network test ignored。
- `cargo test -p kairos-integration --test account -- --test-threads=1`：14 tests passed。
- `cargo test -p kairos-integration --test architecture -- --test-threads=1`：7 tests passed。
- `cargo test -p kairos-account-service --lib -- --test-threads=1`：20 tests passed。
- `cargo test -p kairos-execution-service --lib -- --test-threads=1`：58 tests passed。
- `uv run pytest -q`：346 passed，8 skipped。
- 本次改动文件的 `rustfmt --check` 与 `git diff --check`：通过；全仓库 `cargo fmt --all -- --check` 仍被预存的 `crates/business/market/service/src/application/process.rs:1066` 长行格式差异阻塞。
- `cargo test --workspace --no-run`：通过。
- `cargo test --workspace` 已运行；当前工作区中与 Binance 边界无关的 Market 预存测试仍失败：
  `application::process::tests::owner_release_is_scoped_idempotent_and_enforced_by_unsubscribe`（实际 422，期望 202），以及
  `application::process::tests::replay_pause_resume_controls_the_source_without_a_polling_path`（实际 422，期望 202）。
