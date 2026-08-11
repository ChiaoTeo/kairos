# Integration 外部能力、连接与接入重构设计

> 状态：可执行的目标设计与逐切片重构依据
>
> 最后校准：2026-08-10
>
> 适用范围：Exchange、Broker、Market Data Provider 的请求、实时流和批量数据接入
>
> 架构基线：保持 Account、Execution、Market、Reference 多进程自治，不增加中心 Integration 进程

## 1. 文档用途

本文不是最终代码 API 的逐字规范，而是重构时必须遵守的所有权、语义、安全和迁移约束。每个迁移阶段都给出当前问题、目标改动、删除项、测试和退出条件；只有退出条件满足后才能进入下一阶段。

文中的术语强度如下：

- 必须：正确性或架构边界要求，不能通过实现偏好绕开。
- 应当：默认选择；偏离时必须在变更说明中给出当前调用方和证据。
- 可以：provider 或业务切片按需要采用。
- 暂不：没有当前调用方或运行证据，不在本轮重构中实现。

重构过程中遵循以下规则：

1. 每次只迁移一个业务/provider/product 切片。
2. 新路径通过故障注入和边界测试后，删除同一切片的旧路径。
3. 不为尚未出现的调用方保留双 facade、兼容 registry 或万能 dispatch。
4. provider 原生连接是事实来源；公共能力只抽象能够保持相同语义的部分。
5. Integration 不导入任何业务模块的 application、services、contract 或 domain。
6. 业务 application 不暴露 Integration connection、provider SDK 或 vendor payload。

## 2. 最终架构结论

### 2.1 不可改变的决定

1. Kairos 保持多进程架构。Account、Execution、Market、Reference 各自创建、持有和关闭自己需要的外部连接。
2. 不增加 kairos-integration-server、中心 session proxy、中心请求代理或中心 quota service；允许同机业务进程映射一个只含原子计数器的 quota ledger，它不代理请求也不持有连接。
3. Integration 只感知 Exchange、Broker、Data Provider 提供的能力以及它们产生的外部事实，不感知 Kairos 的 Execution、Account、Market、Reference 业务形态。
4. 连接接口、Integration 请求/结果、外部事实和 provider 扩展由 kairos-integration 定义；业务模块不再定义镜像 provider port。
5. provider 原生连接是第一等 API。Integration 公共 capability trait 只是原生连接在语义无损时提供的能力视图。
6. 哪些能力组合成一个 Execution route、Account source、Market feed 或 Reference source，由对应业务 composition 决定。
7. 一个业务进程可以同时持有多个 provider、多个 principal/账户、多个 product 和多种能力连接。
8. 业务 Actor 不持有 provider SDK 或具体连接；业务私有 IO worker 可以直接持有 Integration connection，Actor 只持有轻量命令句柄并继续作为唯一业务状态所有者。
9. HTTP client 不是 session。只有 WebSocket、FIX、需要登录状态的 Broker channel 等有状态通道才拥有显式 ChannelRuntime。
10. 第一阶段不实现通用 SessionRegistry、ProviderSessionActor 或 IntegrationRuntime。composition 显式创建 provider connection，并通过 Arc 或 provider 自己的 handle 共享资源。
11. 命令、查询和流有不同的重试、交付确定性和恢复语义，不能被万能 Connection.start/stop/reconnect 或 execute(operation, payload) 掩盖。
12. 系统采用 at-least-once 外部事实投递和业务幂等，不承诺网络 exactly-once。

### 2.2 核心依赖方向

~~~mermaid
flowchart LR
    EXT["Exchange / Broker / Data Provider"]
    NATIVE["Provider 原生连接<br/>Integration 定义"]
    CAPS["外部能力接口与外部事实<br/>Integration 定义"]
    COMP["业务 composition<br/>选择 provider、账户、能力组合"]
    WORKER["业务 services IO worker<br/>队列、背压、隔离"]
    ACTOR["业务 Actor<br/>唯一业务状态所有者"]
    APP["业务 application<br/>业务命令与查询"]

    EXT <--> NATIVE
    NATIVE --> CAPS
    COMP --> NATIVE
    COMP --> WORKER
    CAPS --> WORKER
    APP --> ACTOR
    ACTOR <--> WORKER
~~~

Integration 不是无语义的 HTTP/SDK wrapper。它理解下单、撤单、订单更新、余额快照、行情增量、provider sequence 等外部金融语义；但它不理解 Kairos 的订单状态机、业务路由、账户权威、风险预算或跨 provider 策略。

### 2.3 两种 Execution 必须区分

交易领域中存在两个同名概念：

- 外部 execution：FIX ExecutionReport、provider fill、订单成交结果。这是 provider 能力或外部事实，属于 Integration。
- Kairos Execution：订单意图、权威订单状态机、路由、reconciliation、审计。这是业务 bounded context，属于 Execution。

因此 ExternalExecutionReport 可以是 Integration 类型，但 ExecutionConnectionGroup、ExecutionRouteId、ExecutionApplication 不能由 Integration 定义或引用。

## 3. 当前实现评估

当前代码能够提供多种 provider 接入，但它的抽象把选择、能力、传输、生命周期和身份混在了一起，继续扩展会放大重复构造和错误语义。

| 当前代码 | 观察 | 直接风险 | 目标 |
|---|---|---|---|
| crates/kairos-integration/src/integration.rs | 全局 `GatewayRegistry` 已删除；文件仍保存尚未迁移切片的多组 capability entry/open closure 和 `with_provider` 方法 | 兼容 service locator 仍随剩余 provider/能力增长 | 文件已明确标为 legacy compatibility root；业务 composition 逐切片改为直接构造 participant concrete context，随后删除对应 entry/open closure |
| crates/kairos-integration/src/integration_connections.rs | connect_registered 按 route/product/transport 查找后每次调用 open | 同一业务组合重复创建 client，无法表达共享 auth、clock、quota | provider factory 一次创建具体 connection；需要复用时显式 clone handle |
| application/connection.rs | 所有请求和流 capability 继承 Connection，并暴露 start/stop/reconnect | HTTP 被伪装成 session；代理状态容易陈旧；一个 handle 可错误影响其他使用者 | capability 不继承万能 lifecycle；有状态 channel 自己管理 runtime |
| domain/spec.rs | ConnectionSpec 混合 route、product、access、transport、capability、credential、asset type | 正交维度相互约束；public 不能表达 credentialed data；IBKR SMART 等被错误建模 | provider-specific config + 稳定外部身份 + 业务自己的 route/source key |
| domain/capabilities.rs | IntegrationCapability 用于运行时选择 | 类型化接口又被 enum 重复 dispatch | concrete capability handle + trait 已完整表达能力；迁移后删除 enum |
| application/error.rs | 错误类型过少，部分 capability 仍返回 String | 无法区分鉴权、授权、限流、已写入超时和 provider 拒绝 | 所有 Integration application capability 返回 IntegrationError/CommandOutcome |
| services/drivers/http.rs | POST、DELETE、429、5xx 和 transport error 统一重试 | provider 已受理但响应丢失时可能重复下单、撤单、转账 | driver 接收明确请求语义；命令写入后不透明重试 |
| Execution composition | 分别构造 order entry、query、stream 的 Integration；provider/product 解析重复 | 同一个 route 的能力彼此无明确关联，client 和配置重复 | Execution composition 一次构造每个 route 的 provider connection，再组合所需能力 |
| Account composition/server | 每 segment 和 private stream 可能分别构造 Integration | 相同 principal 的 client、签名器、限流重复 | Account composition 对每个 source 显式创建并持有连接 |
| Market/Reference composition | 每 provider 使用 Integration::new().with_xxx().connect_xxx | factory 注册层没有带来实际运行期价值 | 直接 provider factory；业务持有多个 source |
| domain/account.rs 与 provider order adapter | Integration 制造 canonical identity，部分 adapter 从 MarketId 字符串反解析 provider symbol | Integration 侵入 Reference 映射；字符串约定脆弱 | ProviderInstrumentRef 只保存外部身份；composition 使用 Reference projection 显式映射 |
| buffered account stream | 有界 sync_channel 使用阻塞 send | 慢消费者阻塞 provider reader | 每消费者独立有界队列；溢出产生 Lagged/ResyncRequired |
| Execution gateway worker | 代理复制初始 ConnectionState | health 可能不再反映真实 worker/connection | health 来自实际连接或 worker 状态，不复制快照作为权威 |

可执行性结论：当前 API 可以编译和运行部分 provider 切片，但不应视为已经具备生产级命令安全和恢复一致性。尤其在 Phase 1 修复透明命令重试前，不应继续扩大 live order、transfer 或 earn command 的使用范围。本文已经给出可以开始重构的阶段、文件、删除项和验收条件，但其中的目标能力尚不能被声明为已实现。

当前 Integration application 已经拥有 OrderEntryConnection、OrderQueryConnection、AccountReadConnection、MarketStreamConnection 等 capability trait。它们的所有权方向是正确的；需要修正的是万能 Connection 父接口、错误语义、命名和构造方式，而不是把这些接口搬到业务模块。

本文替代旧文档中的以下假设：

- 不把 LocalSessionKey → SessionRegistry → ProviderSessionActor 作为所有 provider 的通用核心。
- 不提供 IntegrationOperation 驱动的万能 operation facade。
- 不提供 Integration 定义的 Execution/Account/Market bundle。
- 不要求每个业务进程先创建一个 IntegrationRuntime 才能使用 provider。

### 3.1 实施台账（2026-08-10）

本表是重构期间的事实记录，不是目标声明。状态只能在代码、测试和同切片旧路径删除后更新；“部分完成”不得作为进入生产的依据。

| 阶段 | 状态 | 已落地 | 尚未完成或阻塞 |
|---|---|---|---|
| Phase 0 | 完成 | 已完成核心抽象静态盘点、逐业务构造拓扑、`ConnectionSpec` 字段 owner 和 symbol/canonical 转换热点；已确认命令 POST/DELETE 透明重试风险并建立基线测试 | 后续每个 phase 继续刷新静态命中数 |
| Phase 1 | 完成 | 已引入 `CommandResult`、`CommandOutcome`、`DeliveryCertainty`、`ProviderRejection`、`IndeterminateCommand`；Order Entry、Transfer、Earn 写操作已迁移；HTTP Query/Command 重试策略已分离；Execution 明确区分 Failed/NotSent 与 Unknown/Indeterminate | provider code、quota scope 和 request ID 继续随 provider 切片细化，不阻塞命令安全基线 |
| Phase 2 | 完成 | 已增加 participant-native `BinanceConnection -> BinancePrincipalConnection` 与 `OkxConnection -> OkxPrincipalConnection`；共享层保存 HTTP/credential/quota 等物理资源，WebSocket endpoint/queue 留在 channel config；Binance principal 投影 Spot order/account、Funding account/credential inspection、Earn、Transfer concrete handle；能力由 concrete handle 的 trait 实现证明，不保存 capability metadata | 继续按相同形态迁移尚存的 Binance legacy domain，但不再阻塞原生 connection 边界成立 |
| Phase 3–4 | 进行中 | OKX Trading 已以原生 `OkxInstrumentType`/`OkxTradingMode` 投影 Account/Execution REST 与 stream；Execution server 可从非敏感 route table 同时装配 Binance+OKX，多账户按 endpoint/egress 共享 provider context；旧 OKX order registry 已删除 | required/optional route readiness、更多 Binance product 和 IBKR route 仍待完成 |
| Phase 5 | 进行中 | Account 已直接组合 Binance Spot/Funding 与 OKX Trading concrete handle；async REST/stream 由 Account runtime 驱动，blocking facade 拒绝 Tokio worker；Funding/Earn/Transfer 共用 Funding domain；业务私有异构 source 只作穷举分派，不再实现 Integration capability trait，`AccountProcess` 也不再用泛型空 source 伪装可选能力 | Binance 其他账户域与 IBKR 仍有 legacy 构造路径 |
| Phase 6 | 进行中 | OKX Reference/Market、Binance Spot/USD-M/COIN-M/Options Reference、Hyperliquid Reference，以及 Massive Reference/live/historical 已直接创建 participant-native projection；Massive/Hyperliquid 旧 `Integration` methods 与 gateway 已删除；catalog/live/historical 按适用能力提供 async-first 与显式 blocking 双接口；公开 facade 已统一到 `application/participants`，旧 `application/providers` 已删除；Integration 内部已建立 `application/capabilities`、`application/participants`、`services/participants`、`services/transport`、`services/quota` 唯一目录轴 | Market 业务的 Massive adapter 仍需从 blocking worker 切换到业务 Tokio runtime；Binance market 与其他 Data Provider 继续逐切片迁移；删除根部 legacy facade |
| Phase 7–8 | 进行中 | 无业务调用者的全局 `GatewayRegistry`、万能 `Integration::connect()`、factory selector traits 和空 protocol 已删除；legacy `Integration` root 只允许缩减 | 完成 IBKR/FIX 原生 session，以及剩余 `ConnectionSpec`/`IntegrationCapability`/legacy facade 删除 |

当前静态基线（按 `crates/` 中的文本命中统计）：

| 旧概念 | 文件数 | 命中数 | owner/迁移阶段 |
|---|---:|---:|---|
| `Integration::new` | 7 | 36 | Integration 自用/测试与 Account、Execution、Market、Reference；Phase 2–6 分切片迁移，Phase 8 清理 |
| `ConnectionSpec` | 29 | 101 | Integration provider adapter 与四个业务 composition；Phase 2–7 替换，Phase 8 删除 |
| `IntegrationCapability` | 37 | 130 | 当前运行期 entry 选择和诊断；provider-native 切片不再使用，Phase 8 删除 |
| `dyn Connection`（精确类型） | 0 | 0 | 全局 `GatewayRegistry` 及其万能 connection factory 已删除；剩余 trait object 均为具体 capability 兼容边界 |

盘点命令必须原样保留在每次迁移检查中：

~~~text
rg -l "Integration::new" crates
rg -l "ConnectionSpec" crates
rg -l "IntegrationCapability" crates
rg -l 'dyn Connection\b' crates
~~~

Phase 1 当前兼容性说明：为了先封住重复命令这一最高风险，尚未迁移的 provider 仍可暂时实现 legacy `Connection` 并经旧 capability facade 构造；这不是目标 API，也不得再增加调用者。全局 service locator 已删除，完成一个 participant-native 切片时必须同时删除该切片的旧构造路径。

当前验证记录：五个相关 crate 的 `cargo check --all-targets` 通过；删除无业务调用者的全局 registry/factory 后，`kairos-integration --lib` 当前 107/107，legacy integration tests 16/16，新结构守卫 4/4，Reference lib 30/30，Market lib 16/16 通过；Account package 当前 lib 5/5、CLI 1/1、integration 24/24、architecture 11/11 通过；Execution integration test 33/33、Execution lib 30/30、Execution server 2/2、launch/process/CLI 聚焦测试 70/70 在前序切片通过。结构守卫明确禁止 `services/gateways|drivers|streams|connections|factories`、根部 `blocking.rs|credentials.rs|protocol.rs`、旧 application capability 双归属和 `domain/reference.rs` 重新出现，并要求稳定 domain 轴使用 `participant/connection/instrument/operation` 命名。故障覆盖包括 async/sync command 500/429 不重试、响应丢失映射 Indeterminate、read-only GET/POST 有界重试、显式 provider rejection/auth 分类、submit/cancel 的 NotSent 与 Indeterminate 状态差异、重复 fill 幂等、Binance/OKX execution event envelope 归一化、WebSocket 登录/签名订阅/ping-pong、egress/principal mmap 配额共享与撤单配额保留、异步下单/查询/事件以及 Binance/OKX/Massive/Hyperliquid catalog、Massive live/historical 与 OKX ticker snapshot 运行在调用方 runtime、shutdown 不丢弃已出队 command、blocking facade 拒绝 Tokio worker 内调用、async source 无轮询等待/断线重连/队列溢出显式报错，以及 Funding AccountRead/credential inspection/Earn/Transfer 共享一个 `ConnectionDomain::Funding`、OKX Account/Execution 共享一个 `ConnectionDomain::Trading`、catalog/snapshot handle 均不维护 capability metadata；Reference 测试证明 Binance（含 Equity）、OKX、Massive、Hyperliquid canonical identity 只在 Reference 生成，Massive 测试证明 participant-native venue code 原样进入 external facts，Market 测试证明订阅和 canonical market 映射留在 Market；launch route table 禁止内联 secret。

### 3.2 Exchange、Broker、Data Provider 接入链成熟度

三类参与者已经在身份和 route 语义上分开，但截至本台账日期，不能说整个接入链已经完成迁移。这里必须区分“能标记类型”和“拥有原生构造链”：

| 类型 | route 表达 | 原生构造链 | 当前结论 |
|---|---|---|---|
| Exchange | `IntegrationRoute::exchange(exchange)` | Binance Spot 已形成 `BinanceConnection -> BinancePrincipalConnection -> OrderEntry/OrderQuery/OrderEvents`，Binance Spot/USD-M/COIN-M/Options 已有 participant-native public catalog；OKX Trading 已形成 `OkxConnection -> OkxPrincipalConnection -> AccountRead/CredentialInspection/MarketProfile/AccountEvents/OrderEntry/OrderQuery/OrderEvents`，MarketData 提供 catalog/snapshot；同一业务进程可从一个 provider context 创建多个 principal，Execution 可在同一进程持有两家 participant route | 目标边界已被两家交易所的 Account/Execution/Reference/Market 真实切片验证；其他 Binance domain 和测试兼容路径仍待迁移 |
| Broker | `IntegrationRoute::broker(broker)` 或 `broker_at(broker, exchange)` | IBKR 已有 order/account/private stream 具体实现，但仍由 `ConnectionSpec`/registry 分别构造，尚无共享 brokerage/principal/session context | 只有身份分类，没有完成端到端 Broker-native 链 |
| Data Provider | `IntegrationRoute::data_provider(provider)` 或 `data_provider_for(provider, exchange)` | Massive 已形成 `MassiveConnection -> InstrumentCatalog/LiveMarket/HistoricalMarket` 原生链；三类 projection 均有 async-first 与显式 blocking API，API key 与 source venue 分离，旧 `Integration` entry/open closure 已删除 | Data-Provider-native 构造链和双接口已由 Massive 验证；Market 业务 async adapter 与 host-local provider quota 仍待完成 |

这里的“分链”不意味着定义 `ExchangeConnection`、`BrokerConnection`、`DataProviderConnection` 三个万能接口。分类只决定身份、约束和构造拓扑；业务实际持有的仍是 Integration 定义的具体 provider connection 及其 capability projection。原因是 Broker 可能同时具有账户、订单、行情、历史数据和硬 session 约束，Data Provider 也可能同时具有 REST bulk 与 WebSocket live source；按参与者类型做大接口仍会迫使无关能力互相伪装。

完整落地的验收条件是：

1. composition 不再用 `ParticipantKind + IntegrationCapability` 做运行期 service-locator dispatch，而是显式选择 provider-native constructor；
2. Exchange 的 provider/IP quota 与 principal/account auth 分离，Broker 的 brokerage session/principal/target account 分离，Data Provider 的 entitlement/source/channel 分离；
3. 同一个业务进程可以同时持有多类、多 provider、多 principal 的连接，route/source 选择由业务拥有；
4. Integration 不出现 Execution、Account、Market、Reference bundle，也不因“分链”新增三类万能 trait；
5. IBKR 与 Massive 各完成一个原生切片并删除同切片旧 registry 路径后，才把三类接入链状态改为“已落地”。

当前构造拓扑基线：

| 业务进程 | 当前构造方式 | 单个逻辑 route/source 的潜在物理实例 | 迁移 owner |
|---|---|---|---|
| Execution | Binance Spot 与 OKX Trading 已由业务 composition 构造 provider/private principal；server 的 order entry/query/events 走 participant concrete async projection，由 Execution runtime 直接 poll/await；业务私有 route table 按 account/segment/participant/product 选择 binding，remote query 可按 binding 查询或 fan-out 并保留 binding identity；OKX composition 把业务配置映射为原生 `instType`/`tdMode`，order events 按 `tdMode` 过滤；`compose_execution_routes` 可同时持有 Binance+OKX，多账户按 participant context 共享允许共享的 HTTP/quota 资源但保留独立 principal/channel；launch `[[execution.routes]]` 已贯通 launcher/server，只有 credential 引用进入参数；旧 `with_okx_order_*` registry 路径已删除；直连同步调用使用显式 blocking projection | Binance+OKX 多 route 已可执行；required/optional route readiness、更多 Binance product 和 IBKR native context 仍待接入 | Phase 2–4，Execution composition |
| Account | Binance Spot/Funding 已从一个 provider/principal context 投影各自 async snapshot reader，只有 Spot 投影 private event source；OKX 的全部当前 trading-account segment 从一个 provider/principal context 投影 async snapshot、market profile 与 private event source；HTTP pool、credential 与 host-local mmap quota scope 在各 participant principal 内共享；REST Future 由 Account runtime poll，private stream 无轮询直接 await；业务私有 `AccountAsyncEventSource` 只以固有方法穷举 concrete handle，不实现 Integration trait，非泛型 `AccountProcess` 直接持有这些 source；Funding 与 OKX credential inspection 均由 Account composition 选择 concrete handle；对应旧 ProductFamily/registry 路径已删除 | Binance 其他 domain 和 IBKR 等 participant 仍有 legacy 构造路径 | Phase 5，Account composition |
| Market | OKX 已直接创建 provider context 和 concrete snapshot projection；Massive 已直接创建 concrete async/blocking live/historical projection；Market 私有 adapter 管理订阅、canonical market 映射和业务 feed lifecycle；其他 provider 仍经旧 facade | Massive 不再经过 registry；当前业务 adapter 仍明确选择 blocking projection，下一切片切换到业务 runtime | Phase 6，迁移 Market async adapter 与 Binance source |
| Reference | OKX、Binance Spot/USD-M/COIN-M/Options、Massive Equity/Options 与 Hyperliquid 已直接创建 concrete instrument catalog；Integration 返回无 canonical ID 的 provider facts，Reference 显式生成 Asset/Instrument/Listing/Market ID；Massive cursor/page 由 concrete catalog capability 承载 | canonical identity 的 OKX、Binance、Massive、Hyperliquid 重复 owner 已消除 | Phase 6，继续迁移 Binance Equity 与其他 Data Provider catalog |

这张表按代码路径描述上界，实际数量由进程配置启用的 route/source/segment 决定。重构验收比较的是“同一 binding 是否无意重复”，不是追求全局只保留一个连接；不同业务进程、不同 principal、不同用途 channel 本来就应当隔离。

当前 route/source → `ConnectionSpec` 所有权映射：

| 当前 `ConnectionSpec` 字段 | 当前事实来源 | 目标 owner |
|---|---|---|
| `connection_id` | 业务 composition 手工拼接 | Integration concrete connection 的 descriptor/binding ID；业务另有 route/source ID |
| `route` | 业务按 provider 名称构造 | provider/participant identity 属于 Integration；业务路由选择属于业务 |
| `product` | 业务字符串解析为全局 `ProductFamily` | connection descriptor 改为 participant-native `ConnectionDomain`；交易标的分类留在 instrument/reference 语义中 |
| `access` | 常被 HTTP/public/private 粗略推断 | Integration auth + visibility + entitlement 三轴 |
| `transport` | 业务选择 REST/WebSocket/UserStream | provider concrete config/channel topology |
| `capability` | enum 运行期选择已类型化 trait | 删除字段和 enum；concrete handle 实现的 trait 是唯一能力证明 |
| `credential_id` | 常量 provider 或 `execution` | Workspace credential reference + Integration principal binding；不保存 secret |
| `asset_type` | 部分 provider 特例 | provider instrument fact；canonical asset/instrument 由 Reference 映射 |

### 3.3 Connection domain 与交易产品分离

当前全局 `ProductFamily` 同时混入了三类不同概念：venue market model（Spot、Margin、Futures）、资产类别（Equity）和账户/钱包域（Funding）。这些概念并不具备跨 Exchange、Broker、Data Provider 的稳定同构关系；继续向全局枚举增加 variant 会迫使 Binance、OKX、IBKR 和数据供应商互相迁就。

目标模型采用 participant 原生连接域。它描述端点族、账户域、鉴权/配额和可投影连接的范围，不描述 canonical instrument 或金融产品：

~~~rust
pub mod binance {
    pub enum ConnectionDomain {
        Spot,
        CrossMargin,
        IsolatedMargin,
        UsdMFutures,
        CoinMFutures,
        Options,
        Funding,
    }
}

pub mod okx {
    pub enum ConnectionDomain {
        Trading,
        Funding,
        MarketData,
    }
}
~~~

约束如下：

1. provider-native config 和 connection descriptor 使用对应 namespace 的连接域，例如 `binance::ConnectionDomain::Funding`；`ProviderInstrumentRef` 使用独立的 provider instrument 分类，不能复用连接域。
2. concrete provider connection 已经决定 participant，因此其 API 不需要中央 `enum ProviderConnectionDomain { Binance(...), Okx(...) }` 做二次 service-locator dispatch。
3. 异构连接集合由业务 composition 持有；若诊断或 route collection 需要类型擦除，使用 descriptor 中与 participant 一起解释的 `ConnectionDomainRef`，裸 domain code 不能跨 participant 比较。
4. 能力不再是 descriptor/config 的一个枚举维度。Earn 的抽象就是 `AsyncEarnConnection` trait；`BinanceSimpleEarn` 实现该 trait 本身就是编译期能力证明。不得再记录 `capability = Earn`，也不得增加 `EarnCapability` 对象、capability descriptor 或 registry key。只有在业务调用方确实需要跨 participant 泛型编程或测试替身时才以 trait 作为边界；否则 composition 直接持有 participant 提供的 concrete Earn handle。
5. Binance Funding Wallet 和 Simple Earn 共处 `binance::ConnectionDomain::Funding`；前者的 concrete handle 实现 `AsyncAccountReadConnection`，后者实现 `AsyncEarnConnection`，并由 `EarnProductType` 区分 Flexible/Locked。
6. 同一 Binance principal/provider context 可以共享 credential、HTTP、clock 和 quota，再分别投影 Spot、Funding、Earn、Transfer handle；共享资源不代表把这些操作压成万能接口。

迁移不能一次机械替换全部全局枚举。先为每个 provider-native 新切片引入 participant-owned `ConnectionDomain` 与 instrument type，更新其 descriptor/config 和测试；同时把 canonical 金融产品分类留在 Reference 模型。最终删除根级 `domain::ProductFamily` 及其 `products.rs`，不只是停止在连接选择中使用它。participant 可以按自己的 API 词汇定义 `binance::InstrumentType`、`okx::InstrumentType`，或在供应商确实使用该术语时定义 `binance::ProductFamily`；这些类型不得提升为跨 participant 枚举。异构诊断或业务 route collection 只能携带 participant 和 participant-native code 的组合，不能重新建立 `enum ParticipantProduct { Binance(...), Okx(...) }` 形式的 service locator。迁移期禁止再给根级 `ProductFamily` 增加 variant。

当前 symbol/canonical identity 转换热点也已定位：Binance Futures/Equity、OKX 和 IBKR 的部分 legacy order adapter 仍通过 `MarketId.rsplit(':').next()` 反推 provider symbol；IBKR 的 legacy reference/account normalizer 仍直接制造 canonical `MarketId`。Binance、OKX、Massive、Hyperliquid catalog 已改为只返回 provider facts，并由 Reference 生成 canonical identity。剩余调用点归 Phase 4/6/7：order request 显式携带 `ProviderInstrumentRef`，Reference projection 成为 canonical identity 的唯一映射 owner；迁移完成前不得继续增加新的字符串解析约定。

## 4. 调研依据

### 4.1 供应商约束

| 供应商 | 官方约束或行为 | 对 Kairos 的影响 |
|---|---|---|
| Binance Spot | WebSocket 有生命周期和 ping/pong 要求；错误 -1007 表示执行状态未知；限流有 IP 权重和账户订单等维度 | 下单超时不能当普通失败重发；同进程共享时钟与相关 quota；private channel 独立恢复 |
| OKX | REST 与 WebSocket 下单可能共享交易限额；连接、User ID、instrument 等还有独立限制 | 逻辑能力不等于物理连接；同 provider context 中协调相关 quota |
| Bybit | Private stream 与 WebSocket Trade 是不同端点；订单流可能对同一成交状态产生重复 Filled | 不能强求一个 socket；成交按 provider fill identity 幂等 |
| Coinbase Exchange | REST、WebSocket、FIX Order Entry、FIX Market Data、Drop Copy 分离；FIX response 可能乱序 | Channel role 与 correlation 是一等概念；TCP reconnect 不等于 FIX 恢复 |
| IBKR Web API | 用户名、brokerage session、账户不是同一身份；一个用户名有严格 session 约束；SMART 是订单路由能力 | principal、target account、destination 必须分开；启动前校验 credential concurrency |
| Databento Live | 恢复可能依赖 intraday replay、snapshot、自然刷新和 heartbeat | Market 按 dataset 选择恢复模式，不用统一 reconnect 模板 |
| Massive | 公共市场数据需要 API key；连接限制按资产类别；股票可提供 SIP consolidated feed | Public 不等于 Anonymous；provider 与 source exchange 不应混为一体 |
| Kraken | order book 提供 checksum 校验方法 | gap 或 checksum mismatch 必须触发 rebuild |

官方材料：

- [Binance Spot WebSocket API 与限流](https://developers.binance.com/en/docs/products/spot/web-socket-api#rate-limits)
- [Binance Spot User Data Stream](https://github.com/binance/binance-spot-api-docs/blob/master/user-data-stream.md)、[WebSocket API signature subscription](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md#subscribe-to-user-data-stream-through-signature-subscription-user_stream)、[listen-key 下线记录](https://github.com/binance/binance-spot-api-docs/blob/master/CHANGELOG.md#2026-01-21)
- [OKX API v5](https://www.okx.com/docs-v5/)
- [Bybit WebSocket 连接](https://bybit-exchange.github.io/docs/v5/ws/connect)、[订单事件重复说明](https://bybit-exchange.github.io/docs/v5/websocket/private/order)、[限流](https://bybit-exchange.github.io/docs/v5/rate-limit)
- [Coinbase Systems and Operations](https://docs.cdp.coinbase.com/exchange/introduction/systems-operations)、[FIX Best Practices](https://docs.cdp.coinbase.com/exchange/fix-api/best-practices)、[WebSocket sequence](https://docs.cdp.coinbase.com/exchange/websocket-feed/overview)
- [IBKR Web API](https://ibkrcampus.com/campus/ibkr-api-page/webapi-doc/)、[IBKR Contracts and SMART routing](https://ibkrcampus.com/campus/ibkr-api-page/contracts/)
- [Databento live recovery](https://databento.com/docs/api-reference-live/client/live-blocking)、[reconnect callback](https://databento.com/docs/api-reference-live/client/add-reconnect-callback)
- [Massive Stocks REST](https://massive.com/docs/rest/stocks/overview)、[WebSocket quickstart](https://massive.com/docs/websocket/quickstart?auth=signup)、[Flat Files](https://massive.com/docs/flat-files/quickstart)
- [Kraken order-book checksum](https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2)

### 4.2 成熟项目与社区实现

| 项目 | 可观察模式 | Kairos 采用的推论 |
|---|---|---|
| NautilusTrader | adapter 连接外部系统并按 venue scope 共享 API limiter；RiskEngine 另行限制 submit/modify 的业务速率，cancel/query 不经过 Risk；execution engine 做启动和持续 reconciliation | provider 技术限流与业务风险限流是两个连续但独立的边界；transport recovery 与业务 reconciliation 分层 |
| Hummingbot | connector 内 REST/WS assistant 共享 auth、time synchronizer、`AsyncThrottler`；限额可按每个 Hummingbot instance 配置；ClientOrderTracker 保存 active/cached/lost order | provider 原生 connector 可以共享技术资源；实例 allocation 与 connector 执行限流分开；未知订单是业务状态而非普通错误 |
| CCXT | rate limiter 属于 exchange instance，并明确建议复用同一 instance，避免同 IP/API key 多实例各自重置 limiter；RequestTimeout 后创建订单结果可能未知 | 不按请求构造 client；provider limiter 跟随 provider connection，而不是放进策略 Risk；命令和查询的重试规则不同 |
| QuickFIX/QuickFIXJ | FIX session identity、sequence store、message store 参与恢复 | FIX session 不可简化成 URL + credential；重连后必须完成 protocol recovery |
| LEAN | Brokerage 接口覆盖订单和账户外部能力，实时引擎拥有业务处理 | provider capability 与业务 owner 可以分层，但不能把业务状态放入 adapter |
| VeighNa | gateway 是明确接入实例，由本地 engine 管理 | 一个进程可以持有多个 gateway；不需要中心 Integration 进程 |

项目材料：

- [NautilusTrader architecture](https://nautilustrader.io/docs/latest/concepts/architecture/)、[adapter rate-limit guidance](https://nautilustrader.io/docs/nightly/developer_guide/adapters/)、[execution/Risk flow](https://nautilustrader.io/docs/nightly/concepts/execution/)、[Hyperliquid adapter limiter](https://nautilustrader.io/docs/latest/integrations/hyperliquid/)
- [NautilusTrader IB Dockerized Gateway implementation](https://github.com/nautechsystems/nautilus_trader/blob/develop/crates/adapters/interactive_brokers/src/gateway/dockerized.rs)
- [Hummingbot API throttler](https://hummingbot.org/connectors/connectors/api_throttler/)、[shared WebAssistantsFactory](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/core/web_assistant/web_assistants_factory.py)、[ClientOrderTracker](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/connector/client_order_tracker.py)、[Binance connector](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/connector/exchange/binance/binance_exchange.py)
- [CCXT manual](https://github.com/ccxt/ccxt/wiki/manual)
- [QuickFIX configuration](https://quickfixengine.org/c/documentation/getting-started/configuration.html)、[QuickFIX/J session store and recovery](https://quickfixj.org/docs/configuration/)
- [LEAN brokerage integration](https://www.quantconnect.com/docs/v2/lean-engine/contributions/brokerages)、[live orders](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/trading-and-orders)
- [VeighNa source](https://github.com/vnpy/vnpy)

这些材料证明 provider connector 内共享技术资源、明确连接 owner、幂等和 reconciliation 的价值，但不推出统一业务模型、中心 Integration 服务或万能 session actor。

## 5. 所有权边界

### 5.1 Integration 拥有什么

Integration 拥有：

- provider-specific config、认证、签名、token、时钟同步和 nonce。
- provider-native concrete connection 和对 SDK 的封装。
- Integration-defined capability trait。
- 请求编码、响应解析、provider error 分类。
- provider quota、connection/channel limit 和 cooldown。
- HTTP pool、WebSocket/FIX/native channel 的技术生命周期。
- heartbeat、sequence、checksum、cursor、resend 和 protocol recovery。
- provider instrument/account/order/fill 等外部身份。
- normalized external facts、source envelope 和 delivery certainty。
- provider-specific typed extensions。

Integration 不拥有：

- Kairos 业务 RouteId 或 source selection。
- 订单、余额、持仓、order book 的权威业务状态。
- 多 provider 路由、fallback、账户选择、策略和风险判断。
- Execution/Account/Market/Reference readiness 聚合。
- 业务审计、业务 reconciliation 结果或业务事件发布。

### 5.2 业务模块拥有什么

| 业务 | 持有的 Integration 能力组合 | 权威业务状态 |
|---|---|---|
| Execution | order entry、order query、order/fill event source、必要的 drop copy | 订单意图、订单状态机、成交、执行审计、跨 provider 路由 |
| Account | balance/position/equity snapshot、account event source、credential inspection | 余额、持仓、权益、新鲜度、账户同步状态 |
| Market | live market source、snapshot、historical/bulk data | observation、order book、订阅、新鲜度 |
| Reference | instrument catalog、trading rules、calendar、symbology | canonical catalog 和 lifecycle facts |

业务模块可以定义只用于 composition/services 的连接组合记录和 HashMap，但不能定义镜像 Integration trait。

允许：

~~~rust
struct ExecutionRouteConnections {
    entry: Arc<dyn kairos_integration::OrderEntryConnection>,
    query: Option<Arc<dyn kairos_integration::OrderQueryConnection>>,
    events: Option<Box<dyn kairos_integration::OrderEventSource>>,
}
~~~

不允许：

~~~rust
trait ExecutionProviderPort {
    // 重新复制 submit/cancel/query/event API
}
~~~

前者是业务装配数据，后者会让 Integration 被业务接口反向塑形。

### 5.3 类型归属判断

判断一个概念是否属于 Integration，先问：

1. 只阅读 provider API 文档，能否定义它？
2. 另一个业务是否可能直接消费它？
3. 它的不变量是否关于远端协议、认证、配额或外部事实？

三个答案主要为是时，放在 Integration。

判断一个概念是否属于业务，先问：

1. 是否需要 Kairos 的业务所有权或状态机才能定义？
2. 是否组合了多个 provider capability？
3. 是否包含 provider/账户选择、业务 route、fallback、权威状态或跨源 reconciliation？

任一答案为是时，优先放在业务 application、services 或 composition。

## 6. Integration 公共模型

### 6.1 Provider 原生连接优先

每个 provider/product 暴露 Integration 自己拥有的具体连接 facade。它是 opaque Integration 类型，不是 vendor SDK 类型。

示意：

~~~rust
pub struct BinancePrincipalConnection {
    inner: Arc<BinancePrincipalInner>,
}

pub struct OkxSwapPrivateConnection {
    inner: Arc<OkxSwapPrivateInner>,
}

pub struct IbkrBrokerageConnection {
    inner: Arc<IbkrBrokerageInner>,
}
~~~

业务 composition 直接创建并持有这些连接。需要把不同 provider 放入异构 route/source 集合时，由业务 private services/composition 定义 concrete enum，并通过 inherent method 做穷举分发；该 enum 只是 sum type/container，不再实现 Integration capability trait，否则会把业务容器伪装成新的 provider capability 实现。trait 只由 Integration concrete handle 或真正的测试替身实现。不为迁就旧 registry 把新异步接口降级成同步 trait object。

provider 原生连接允许暴露 provider-specific typed method：

~~~rust
impl OkxSwapPrivateConnection {
    fn place_algo_order(
        &self,
        request: OkxAlgoOrderRequest,
    ) -> Result<CommandOutcome<OkxAlgoOrderAck>, IntegrationError>;
}
~~~

不能把 vendor SDK client、raw JSON 或未清洗 payload 暴露给业务 application。

### 6.2 Integration-defined capability

Capability 描述外部机构提供的能力，不描述 Kairos 业务模块。

第一批保留或调整为：

- OrderEntryConnection：普通订单提交、撤销；只有语义一致时包含 amend。
- OrderQueryConnection：按 provider/client identity 查询订单、open orders、recent orders。
- OrderEventSource：外部订单更新、fill、execution report。
- AccountSnapshotConnection：余额、持仓、权益等 provider snapshot。
- AccountEventSource：余额、持仓、margin 等 provider private facts。
- MarketDataSource：实时 trades、quotes、book、rate 等。
- HistoricalDataConnection：历史或批量市场数据。
- InstrumentCatalogConnection：provider instrument、rule、calendar、symbology。
- CredentialInspectionConnection：credential permission/account profile。
- `AsyncTransferConnection`、`AsyncEarnConnection`：各自保留独立命令语义；trait 本身就是 capability 边界，不再创建 `TransferCapability`/`EarnCapability`、capability descriptor 或 registry。participant concrete handle 实现 trait 即可，blocking namespace 只是同一实现的同步适配，不形成第二套能力模型。

示意 API：

~~~rust
pub trait OrderEntryConnection: Send {
    fn descriptor(&self) -> &ConnectionDescriptor;

    fn submit_order(
        &self,
        request: &SubmitOrderRequest,
    ) -> Result<CommandOutcome<ExternalOrderAck>, IntegrationError>;

    fn cancel_order(
        &self,
        request: &CancelOrderRequest,
    ) -> Result<CommandOutcome<ExternalCancelAck>, IntegrationError>;
}

pub trait OrderQueryConnection: Send {
    fn query_order(
        &self,
        request: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError>;
}

pub trait OrderEventSource: Send {
    fn next_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalTradingFact>, IntegrationError>;
}
~~~

目标接口使用 Rust 原生 `async fn in trait`。provider-native connection 不要求 `dyn` object safety；业务 private concrete enum 也不实现该 trait，而以 inherent async method 穷举 concrete variant。旧 registry 所需的 `Box<dyn ...>` 只属于迁移兼容层，不能反向限制目标 API。只有未来出现真正的动态插件边界时，才为该边界增加 boxed-future adapter。

### 6.3 公共能力的准入条件

一项 provider 操作只有同时满足以下条件，才进入公共 capability：

1. 至少两个真实 provider 或一个当前多业务调用方需要。
2. 请求语义可以精确定义，不依赖 provider 名称猜测。
3. 成功、拒绝、未知结果和错误语义可以一致表达。
4. 不需要大量 nullable 字段、字符串 option 或 raw extension map。
5. 不会静默降级 provider 特性。
6. 有边界测试证明不同实现遵守同一契约。

如果不满足，保留在 provider 原生 connection 或 provider-specific extension trait。

### 6.4 公共核心与 Provider 扩展

公共请求只包含真正共享的外部语义。provider-only 字段不得通过以下方式混入：

- 大量 Option 字段。
- HashMap<String, String>。
- 任意 serde_json::Value。
- 根据 participant 解释同一字段的不同含义。

正确方式：

- 普通限价/市价订单走公共 OrderEntryConnection。
- OKX algo、IBKR order condition、Binance 特有响应模式走 provider 原生方法或 Integration-owned extension trait。
- 业务只有出现当前调用方时才保留具体连接引用。
- provider 不支持某项公共请求组合时，返回 UnsupportedCapability 或 InvalidRequest，不自动替换参数。

### 6.5 不再继承万能 Connection

目标 capability 不继承统一 Connection.start/stop/reconnect。

- HTTP/query client 构造后即可调用，不伪装 session 生命周期。
- stateful stream 在 provider factory 或显式 subscribe 时建立。
- reconnect 由拥有该 channel 的 provider runtime 处理。
- shutdown 由业务 composition 持有的具体 connection/runtime guard 触发。
- health 是对真实 connection/channel 的观察，不是可由业务任意修改的状态副本。

只有多个 provider 证明存在相同调用者和相同语义时，才提取共享 HealthWatch 或 ShutdownHandle；第一切片可以先使用 provider concrete type。

### 6.6 不使用万能 Operation dispatch

目标调用方式：

~~~rust
order_entry.submit_order(&request)
order_query.query_order(&query)
market_source.subscribe(&subscription)
~~~

不是：

~~~rust
integration.execute(
    IntegrationOperation::SubmitOrder,
    binding_id,
    payload,
)
~~~

不定义 `IntegrationCapability` 或 capability descriptor。启动诊断、配置校验、能力展示和测试矩阵通过实际构造出的 concrete handle 类型与其 trait 实现生成；不能再维护一套与 Rust 类型系统平行、可能漂移的能力枚举。

## 7. 业务如何组合多个 Provider 与账户

### 7.1 Execution 示例

Execution 为每条业务 route 保存自己需要的 Integration connection：

~~~rust
enum ExecutionAsyncOrderEntry {
    BinanceSpot(BinanceSpotOrderEntry),
    OkxTrading(OkxTradingOrderEntry),
}

struct ExecutionConnections {
    entry: RoutedAsyncOrderEntry<ExecutionAsyncOrderEntry>,
    query: RoutedAsyncOrderQuery<ExecutionAsyncOrderQuery>,
    events: Vec<ExecutionAsyncEventSource>,
}
~~~

这些 enum/route collection 由 Execution 私有 composition 定义，只承载异构 concrete handle，不重新声明 provider port，也不进入 Integration。Integration 的 `AsyncOrderEntryConnection`、`AsyncOrderQueryConnection`、`AsyncOrderEventSource` trait 本身就是能力边界。

ExecutionRouteId 是业务配置标识，例如：

- binance-main-spot。
- binance-hedge-usdm。
- okx-main-swap。
- ibkr-securities-smart。

它不进入 Integration connection identity，也不由 Integration 解析。技术 `binding_id` 由 participant、`principal_scope_id` 与 participant-native connection domain/`instType`/`tdMode` 形成；业务 `route_id` 单独保存在 Execution route table。两者不得互相替代。

composition 可以同时装配：

~~~rust
let binance = BinanceConnection::connect(binance_provider_config)?;
let binance_main = binance.principal_connection(binance_main_principal)?;
let okx = OkxConnection::connect(okx_provider_config)?;
let okx_main = okx.principal_connection(okx_main_principal)?;

entry_routes.push(ExecutionRoute::new(
    "binance-main-spot",
    ExecutionAsyncOrderEntry::BinanceSpot(binance_main.spot_order_entry()?),
));
entry_routes.push(ExecutionRoute::new(
    "okx-main-swap",
    ExecutionAsyncOrderEntry::OkxTrading(
        okx_main.trading_order_entry(OkxInstrumentType::Swap, OkxTradingMode::Cross)?,
    ),
));
~~~

当前 launch 配置使用非敏感 route table，credential 只保存引用：

~~~toml
[execution]

[[execution.routes]]
route_id = "binance-main-spot"
account_id = "main"
segment_key = "spot"
provider = "binance"
product = "spot"
credential_id = "binance-main"
principal_scope_id = "binance-main-account"

[[execution.routes]]
route_id = "okx-main-swap"
account_id = "main"
segment_key = "swap"
provider = "okx"
product = "swap"
credential_id = "okx-main"
principal_scope_id = "okx-main-account"
~~~

Launcher 只把上述非敏感 route JSON 交给 Execution server；server 在自身 composition 中按 `credential_id` 读取 workspace credential，再调用 `compose_execution_routes`。`api_key`、secret、passphrase 禁止写入 route table 或进程参数。

Execution 决定：

- 订单使用哪条 route。
- 使用哪个账户和 product。
- route 不健康时拒绝、排队还是由新业务意图切换。
- 多 provider 订单如何聚合。
- Indeterminate 订单如何 reconciliation。

Integration 不能在 Binance 超时时自动把订单发送到 OKX。跨 provider failover 是新的业务决策，不能隐藏在 transport retry 中。

### 7.2 其他业务组合

Account：

~~~text
AccountSourceId
  -> balance snapshot connection
  -> position/equity connection
  -> account event source
~~~

Market：

~~~text
MarketSourceId
  -> Binance live trades/book
  -> OKX live trades/book
  -> Databento dataset
  -> Massive feed or flat files
~~~

Reference：

~~~text
ReferenceSourceId
  -> exchange instrument catalog
  -> broker contract search
  -> data-provider symbology
~~~

这些 source/route collection 属于业务 private composition/services，不应上提成 Integration 的 AccountBundle、MarketBundle 或 ReferenceBundle。

### 7.3 逻辑能力与物理连接分离

业务看到 entry、query、events 三项能力，不代表必须存在三个 socket：

- Binance 可以让多个 HTTP capability 共享 client、clock 和 IP quota。
- OKX REST/WS 下单可能共享交易 quota，但通道不同。
- 一个 private WebSocket 可以产生 order、fill、balance、position 多种外部事实。
- FIX Order Entry、Drop Copy、Market Data 通常是独立 session。
- 一个逻辑 order capability 也可能使用请求 channel 加 private event channel 完成确定性。

Provider connection 决定如何共享或拆分物理资源；业务只决定自己消费哪些逻辑能力。

### 7.4 业务 Actor 与连接隔离

现有 QueuedOrderEntry、QueuedOrderQuery 等 worker 模式可以保留：

~~~text
Execution Actor
  -> bounded gateway handle
  -> Execution private IO worker
  -> Integration OrderEntryConnection
  -> provider concrete connection
~~~

Worker 是业务进程的 IO 隔离设施，不是新 Provider Port。它负责有界队列、超时、取消、结果回传和关闭；连接接口仍直接来自 Integration。

## 8. 身份与配置模型

### 8.1 分离六个身份轴

| 轴 | 含义 | 示例 | 所有者 |
|---|---|---|---|
| Access participant | 实际连接的机构 | Binance、OKX、IBKR、Databento | Integration |
| Environment/endpoint set | prod/test 与可切换端点集合 | Binance production | Integration config |
| Principal | provider 眼中的认证主体 | API UID、IBKR username | Integration |
| Target account | 请求作用的远端账户 | subaccount、broker account | Integration request/config |
| Participant-native domain/type/dataset | participant 自己的 endpoint、instrument type、dataset/schema | OKX SWAP、Binance Funding、Databento GLBX.MDP3 | 对应 Integration participant 实现 |
| Canonical product/asset classification | 跨来源的业务金融分类 | Equity、Option、Perpetual | Reference/使用该分类的业务 |
| Business route/source | Kairos 选择用哪个接入 | okx-main-swap | 业务 |

Transport、capability 和业务 route 不应再混入同一个 ConnectionSpec。

### 8.2 Provider-specific config，公共 identity

第一阶段允许每个 provider 定义精确 config：

~~~rust
struct BinancePrincipalConfig {
    environment: ProviderEnvironment,
    endpoint_set: BinanceEndpointSet,
    principal: BinancePrincipalRef,
    credential_id: CredentialId,
    account: Option<BinanceAccountRef>,
    quota_allocation: BinanceQuotaAllocation,
}
~~~

不要先设计一个能容纳所有 Exchange/Broker/DataProvider 的万能 Profile。只有确实共享的稳定身份进入公共 descriptor：

~~~rust
struct ConnectionDescriptor {
    binding_id: IntegrationBindingId,
    participant: ParticipantRef,
    environment: ProviderEnvironment,
    domain: ConnectionDomainRef,
    principal_scope: Option<PrincipalScopeId>,
}
~~~

binding_id 是 composition 传入的技术实例标识，用于外部事实、health、日志和指标；它不是业务 RouteId。

### 8.3 Provider context 与 principal context

同一业务进程内，多账户要求两层技术共享：

~~~text
Binance provider context
├── endpoint set
├── HTTP pool
├── server clock
├── IP quota
├── main principal context
│   ├── signer
│   ├── UID/account quota
│   └── private channel
└── hedge principal context
    ├── signer
    ├── UID/account quota
    └── private channel
~~~

第一切片应实现 BinanceProviderContext 和 BinancePrincipalContext 等 concrete 类型，不先提取通用 ProviderContext trait。第二个 provider 出现重复实现后，再按真实共性抽取内部 helper。

### 8.4 Participant、destination 与 source venue

连接对象、订单目的地和市场事实必须分离：

- IBKR 是 access participant。
- DU account 是 target account。
- SMART 或 XNAS 是订单 route/destination。
- 实际 execution venue 是返回 fill/execution report 的事实。
- Massive 是 data provider。
- SIP 是 dataset/feed。
- XNAS 等 source exchange 是 market event 的事实。

不再使用 exchange/broker/data_provider 三个可选字段互斥表达所有关系。

### 8.5 Instrument identity

Integration 只使用 provider 外部 instrument identity：

~~~rust
struct ProviderInstrumentRef {
    participant: ParticipantRef,
    instrument_type: ParticipantInstrumentTypeRef,
    native_symbol: String,
    dataset: Option<DatasetId>,
}
~~~

`ParticipantInstrumentTypeRef` 只是异构请求边界上的 type-erased code，必须和 `participant` 一起解释；provider concrete API 仍接收 `binance::InstrumentType`、`okx::InstrumentType` 等强类型。它不是新的全局产品分类，也不能用于生成 canonical Reference identity。

业务 composition 通过 Reference projection 做显式映射：

~~~text
Execution InstrumentId / ExecutionAccessId
  -> Reference projection
  -> ProviderInstrumentRef
  -> Integration request
~~~

禁止：

- Integration 为 provider symbol 制造 canonical MarketId/InstrumentId。
- adapter 从 MarketId 字符串切片反解析 provider symbol。
- 通过 symbol 命名约定猜测 product 或 venue。

### 8.6 可见性、认证与 entitlement

Public/Private 不能同时表示数据可见性和认证：

~~~rust
enum DataVisibility {
    PublicMarket,
    CustomerPrivate,
}

enum AuthRequirement {
    Anonymous,
    ApiCredential,
    OAuth,
    LoginSession,
    MutualTls,
}
~~~

Massive、Databento 等公开数据可以要求 API key、付费 entitlement 和 redistribution permission。credential value 不进入 identity、日志、metrics、错误或事件。

## 9. 连接与通道生命周期

### 9.1 HTTP 不是 Session

HTTP request capability 通过共享 client/context 复用：

- connection pool。
- TLS 配置。
- provider clock。
- signer。
- provider/IP/principal quota。
- endpoint health。

它不需要 Actor 来串行所有请求，也不提供通用 start/stop/reconnect。命令、查询、bulk 可以使用独立有界 lane，同时共享上述技术资源。

### 9.2 ChannelRuntime 只用于有状态协议

以下场景可以有 provider-specific ChannelRuntime：

- WebSocket authentication、heartbeat、subscription 和 reconnect。
- FIX logon、sequence、message store、resend 和 gap fill。
- IBKR login/brokerage session。
- 带 cursor/replay 状态的 native stream。

ChannelRuntime 负责：

- transport connect/reconnect。
- authentication。
- heartbeat。
- subscription intent replay。
- protocol sequence/checksum/cursor。
- channel epoch 和 health。
- 产生 gap、lag、recovery notification。

它不负责业务订单、余额、持仓或 order book 状态。

### 9.3 暂不实现通用 Registry/SessionActor

Phase 2/3 采用显式 composition：

1. composition 为一个 route/source 创建 concrete provider connection。
2. concrete connection 内部用 Arc 共享 context。
3. 业务把 capability handle 分配给 worker。
4. process facade 持有 concrete connection/runtime guard 到 shutdown。

只有同时出现以下证据，才讨论本地 registry：

- 同一进程内存在两个无法合并的当前调用方独立 acquire 相同 binding。
- 重复 acquire 已实际创建冲突 session 或造成显著资源问题。
- 显式 composition/Arc clone 无法保持所有权清晰。
- 有并发测试证明 get-or-create 是必要边界。

即便引入，也先实现 provider-specific cache/factory；不直接上升为全 provider SessionRegistry。

### 9.4 Readiness

Readiness 必须按 binding 和 capability 表达：

- order entry ready。
- order query ready。
- order events recovering。
- account snapshot ready。
- market book resyncing。

业务 composition 标注某 route/source 是 required 还是 optional，业务 process 聚合自己的 readiness。Integration 只报告 connection/channel/capability health，不决定整个 Execution 或 Market 是否 ready。

一个 provider 降级不能自动拖垮同进程全部 route；是否停止业务由业务配置决定。

### 9.5 Provider 单 session 硬约束

处理顺序：

1. 为不同业务分配独立 principal、username、API key 或 subaccount。
2. 若 provider 允许，使用不同 protocol session identity。
3. 若确实 ExclusiveProcess，Workspace/System 配置只允许一个业务进程使用该 principal。
4. 其他业务需求通过明确的业务 contract 和单独 ADR 解决，不私自增加通用 Integration proxy。

这是 provider-specific 部署约束，不是引入中心服务的理由。

### 9.6 Async-first、blocking facade 与执行 lane

当前代码是迁移中的混合架构：

- Integration application 的旧 command/query/stream trait 大多是同步方法，以 `&mut self -> Result<...>` 暴露。
- 通用旧 provider 的 REST 仍大量使用 `reqwest::blocking::Client`，并由专用 worker thread 隔离；Binance Spot 的 order entry/query/private subscription 已有共享 `reqwest::Client` 的 async 主路径。
- Execution 的单状态 owner 保持同步串行：未迁移 provider 仍用有界 `std::sync::mpsc` gateway worker 隔离 blocking SDK；Binance Spot 则用有界 Tokio gateway，provider Future 在 Execution runtime 上执行，结果再回到状态 owner。
- Binance Spot Execution order command/query/stream 已使用 async trait + ambient `tokio::spawn`，由 Execution runtime 直接 poll/await；Binance Spot Account private stream 也已切换为 async source，其他 Account provider 仍处于“专用 IO worker 限时等待 + `Notify`”过渡期；Market 和其他 provider 的旧接口仍是 pull/try-recv。
- 各业务 server 的监听、控制面和进程 runtime 是 async Tokio。

目标 API 学习 reqwest 的产品边界，但针对本项目的多进程/长连接约束做两点强化：

1. 默认命名空间是 async-first API；Exchange、Broker、Data Provider 的 concrete connection 和无损 capability trait 使用 Rust 原生 `async fn in trait`。
2. `kairos_integration::blocking` 是显式同步 facade；它复用相同 request/result、认证、quota、解析和错误核心，但调用者从命名空间即可知道会阻塞。
3. async 方法只返回惰性的 Future，本身不创建或绑定 runtime；业务既可直接 `.await`，也可把该 Future `tokio::spawn` 到当前 Tokio runtime。provider 确有后台读写任务时同样使用 ambient `tokio::spawn`，自然共享当前业务进程 runtime；普通配置和 connection 都不注入或持有 `Runtime`/`Handle`，并禁止每个 WebSocket 偷建一个 runtime thread。
4. blocking facade 类似 `reqwest::blocking`，在 facade context 内维护专用 driver runtime/thread 并转发到 async core；它不在外部 Tokio worker 上执行，也不承诺与 async Client 共享连接池实例。

这里“复用核心”不等于 async client 和 blocking client 是同一个对象。它们可以共享模型、builder、签名器、quota ledger 和 codec，但 runtime ownership、连接池和 lifecycle 分开，避免一个同步调用卡住业务 runtime。

建议命名和形态：

~~~rust
// 默认：异步主接口
pub trait AccountEventStreamConnection: Send {
    async fn start(&mut self) -> Result<(), IntegrationError>;
    async fn next_account_event(
        &mut self,
    ) -> Result<ExternalEventEnvelope<ExternalAccountEvent>, IntegrationError>;
    async fn stop(&mut self) -> Result<(), IntegrationError>;
    fn health(&self) -> ConnectionHealth;
}

// 显式兼容层：kairos_integration::blocking::account
pub trait AccountEventStreamConnection: Send {
    fn start(&mut self) -> Result<(), IntegrationError>;
    fn next_account_event(
        &mut self,
        timeout: Duration,
    ) -> Result<AccountEventReceive, IntegrationError>;
    fn stop(&mut self) -> Result<(), IntegrationError>;
    fn health(&self) -> ConnectionHealth;
}
~~~

调用 `async fn` 得到 Future 后可直接传给 `tokio::spawn`；但跨 task 调度要求该 Future 为 `Send + 'static`。稳定 Rust 的原生 `async fn in trait` 尚不能在 trait 方法签名中直接向调用者承诺返回 Future 的 `Send` bound，因此实际公共 trait 使用其无装箱等价形式 `fn ... -> impl Future<Output = ...> + Send`，provider impl 仍可写 `async fn`。这不是注入 runtime、退回同步接口，也不引入 `async_trait` 的 boxed-future 分配。

不同 lane 的目标如下：

| lane | 典型能力 | 外部 API | 内部实现 | 所有权与约束 |
|---|---|---|---|---|
| Command lane | submit/cancel/amend/transfer/earn action | 默认 async 类型化 `CommandResult<T>`；`blocking` 提供镜像 facade | async HTTP/shared Tokio runtime；blocking SDK 由单独 driver thread/session 包装 | 每 binding 独立有界队列；优先保证顺序、确定性和审计，不能因 async 或 Future cancellation 自动重试 |
| Query lane | order/balance/reference query | 默认 async `Result<T, IntegrationError>`；`blocking` 提供镜像 facade | async client 共享 HTTP pool；只读 query 按预算并发和重试 | 不与 command lane 串行；reconciliation 有独立优先级和限额 |
| Stream lane | market/private/FIX/drop copy | async start/stop/next 或 bounded receiver；blocking facade 提供 timeout receive | Tokio task spawn 到调用 async 方法时的当前业务 runtime；native SDK 可使用 provider-specific driver thread | reader 不能被慢 consumer 阻塞；断线、sequence、recovery 属于 channel runtime |
| Bulk lane | historical/download/replay | job/iterator/stream handle，按 provider 原生分页与取消语义 | async 下载、文件流或 blocking bulk worker | 不占用 command lane；支持进度、取消、checkpoint |

因此本项目采用“async-first + 显式 blocking facade”，而不是让同一个含糊接口按实现决定是否阻塞。理由：

1. 当前业务 server 已经运行 Tokio；async provider 可以与控制面、timer 和其他连接共享每个业务进程自己的 runtime，并用 `select!` 管理 shutdown/readiness。
2. Command 的交付确定性、顺序和幂等由结果类型和 ordering domain 保证；改成 async 不改变 `NotSent/MayHaveBeenSent`，Future drop 也不是 provider cancel。
3. Rust 原生支持 async trait。目标 provider-native connection 不依赖旧 `dyn` registry；异构 route 由业务 private concrete enum 承担。
4. 阻塞 SDK（如部分 Broker/native API）不伪装成非阻塞调用：async facade 将其发送到有界 driver thread，blocking facade 可以直接使用同一 driver/core。
5. 多 provider、多账户在一个业务 runtime 内并发，但各 binding 的 queue、quota、health 和 ordering domain 仍隔离；多进程架构完全不变。

强制规则：

- async `configure/build` 只做校验和本地资源创建；远端 connect/logon/subscribe 由显式 async `start().await` 驱动。
- async provider 只能在 async `start/connect` 的 Future 被 `.await` 或 `spawn` 后使用所在 executor；同步 `build/configure` 不得创建 runtime 或后台 task。只有调用方明确要跨 runtime 投递时，才由调用方自己使用目标 `Handle::spawn`，不把 `Handle` 变成 Integration 配置或 connection 状态。
- 默认 async API 可在业务 Tokio task 中直接 await，但 Actor 的唯一状态所有权不变；连续网络读取由 application/services runtime 驱动，事件通过 Actor command/apply 边界进入状态。
- `blocking` API 在检测到 Tokio runtime context 时必须快速报错或 panic（行为需统一并测试）；async caller 如必须调用 native blocking API，只能经 provider 的 bounded driver 或 `spawn_blocking` adapter。
- blocking runtime/context 按 provider context 复用，不按 request 或 socket 创建；drop 必须完成 channel shutdown，再回收 runtime thread。
- async 取消不等于 provider command 取消；Future 被 drop 后若请求可能已写入，结果仍必须是 `Indeterminate` 并 reconciliation。
- 当指标证明 query concurrency 不足时，只增加同 binding 的受限 async concurrency；不要并行化同一 command ordering domain。

reqwest 值得直接借鉴的不是“blocking 内部实现细节必须完全照搬”，而是 API 可见性：默认 Client 是 async，`blocking::Client` 明确阻塞，两套 API 共享绝大多数能力和类型。Kairos 的 async Client 同样由调用方 runtime poll，额外要求 blocking context 按 provider 复用，以及 trading command 保留交付确定性。

本仓库当前锁定的 reqwest 0.13.4 实现提供了可直接验证的基线：

- 默认 [`reqwest::Client`](https://docs.rs/reqwest/0.13.4/reqwest/struct.Client.html) 返回 Future，由调用方 async runtime 驱动。
- [`reqwest::blocking`](https://docs.rs/reqwest/0.13.4/reqwest/blocking/index.html) 使用独立命名空间，并明确禁止在 async runtime 内直接调用。
- blocking `Client` 在创建时启动名为 `reqwest-internal-sync-runtime` 的 current-thread runtime，clone 共享同一个 client handle；请求经 channel 交给 async core，而不是每个请求创建 runtime。

Kairos 映射为：`BinanceConnection` 等 provider context 相当于可 clone/reuse 的 async Client；`blocking::BinanceConnection` 相当于复用一个 blocking runtime/driver 的 facade。禁止把 runtime 粒度做成“每个 capability projection 一个”或“每个 WebSocket 一个”。

#### 9.6.1 Stream 不能用含糊的 `Option` 表达等待语义

旧 `AccountEventStreamConnection` 是：

~~~rust
fn next_account_event(&mut self)
    -> Result<Option<ExternalAccountEvent>, IntegrationError>;
~~~

这个签名没有说明调用是否阻塞，也无法区分“本次没有事件”“等待超时”和“流已结束”。实际实现把异步 WebSocket 降级成 `try_recv -> Ok(None)`，Integration buffer worker 每 5ms 调一次，Account Tokio 主循环又每 5ms 扫一次 buffer。它有四个问题：

1. 空闲连接持续唤醒线程和 runtime，是双层定时忙等。
2. Tokio 主循环无法对 provider readiness 做 `select!`。
3. 新 provider 若把方法实现成无限阻塞，stop、reconnect 和 worker 回收都会失控。
4. IBKR 的 legacy“stream”实际是 REST snapshot polling，曾可能被 5ms 循环放大成 API hammering。

为立即消除忙等，Account 迁移兼容层已先采用以下 blocking 边界：

~~~rust
enum AccountEventReceive {
    Event(ExternalAccountEvent),
    Idle, // bounded wait elapsed; not EOF
}

trait AccountEventStreamConnection: Connection {
    fn recv_account_event(
        &mut self,
        timeout: Duration,
    ) -> Result<AccountEventReceive, IntegrationError>;
}
~~~

这不是让 Actor 调阻塞方法。调用链固定为：

~~~text
provider async/native reader
  -> provider bounded socket queue (overflow => Backpressure/resync)
  -> Account-owned dedicated IO worker: recv_account_event(timeout)
  -> Account bounded event queue + Notify
  -> Account Tokio select! wakes and drains a bounded batch
  -> Account Actor applies facts
~~~

这是过渡期 `kairos_integration::blocking` 的语义基线，不是最终 async 主路径。最终调用链为：

~~~text
Account Tokio task
  -> async provider connection.next_account_event().await
  -> provider bounded channel driven by Account process shared runtime
  -> Account application applies a bounded batch to Actor
~~~

迁移规则：

- WebSocket provider 的 task 必须在 async start/connect 中 spawn 到当前业务 runtime；不能保留每 socket 一个隐藏 runtime thread。
- async trait 直接 await event，不需要用 `Idle` 表达一次空轮询；业务 timeout 用 `tokio::time::timeout` 或 `select!` 明确表达。
- blocking facade 的专用 IO worker 等待必须有界、可停机，并明确返回 `Idle`，禁止 sleep + try-recv polling。
- 迁移期业务 runtime 用 `Notify`/receiver readiness await，不使用固定 5ms timer；async provider 完成后删除这层 thread + Notify bridge。
- queue 满时不能静默 drop，也不能永久阻塞 provider reader；必须终止该连续性并要求 snapshot/replay resync。
- Binance Spot Order stream 已完成 async 主路径；其旧 `try_next_*` 只从 `kairos_integration::blocking` compatibility facade 暴露。Account、Market 和其他 provider 按同一双接口语义迁移，旧接口不能直接放进高频轮询。

对外既可以暴露原生 async trait 方法，也可以在确有多个 consumer 需要组合器时暴露实现 `Stream` 的 bounded receiver handle。二者都必须保留单 consumer ownership、backpressure、shutdown 和 resync 语义；不能为了 `dyn` object safety 退回同步接口。

## 10. 命令、查询与流语义

### 10.1 三类能力

| 类型 | 示例 | 默认语义 |
|---|---|---|
| Query | order query、balance snapshot、historical data、catalog | 未观察到响应且预算允许时可有界重试；返回 freshness/completeness |
| Command | submit、cancel、amend、transfer、earn action | 可能写入后不透明重试；必须表达交付确定性 |
| Stream | order event、account event、market data、FIX drop copy | at-least-once；声明 ordering、replay、snapshot 和 recovery |

HTTP method 不能单独决定重试。某些 POST 是 query，某些 DELETE 是具有副作用的命令；请求语义必须由 capability 调用方显式传给 driver。

### 10.2 CommandOutcome

~~~rust
enum CommandOutcome<T> {
    Confirmed(T),
    Rejected(ProviderRejection),
    Indeterminate(IndeterminateCommand),
}

enum DeliveryCertainty {
    NotSent,
    MayHaveBeenSent,
    Acknowledged,
}

type CommandResult<T> = Result<CommandOutcome<T>, IntegrationError>;
~~~

含义：

- Confirmed：已收到足以确认 provider 接受或完成的响应。
- Rejected：provider 明确拒绝，且不会产生预期副作用。
- Indeterminate：请求可能已到达 provider，但结果无法确定。
- `CommandResult::Err` 只允许表示在副作用发生前已证明 `NotSent` 的本地校验、readiness、认证/授权失败，或明确发生在 write 前的传输失败；provider 已返回且能证明未接受命令的认证/授权响应也属于此类。
- 一旦开始 dispatch，任何无法证明 provider 未收到的 transport、429、5xx 或响应解析失败都必须映射为 `Indeterminate`，不能返回普通 error。

这一约束让业务层不需要猜测某个 transport error 是否能重试：Execution 对普通 error 可以记录本次 submission 为 `Failed/NotSent`，对 `Indeterminate` 必须记录 `Unknown` 并在原 route/account reconciliation。Cancel 的普通 error 不改变原订单状态；cancel 的 `Indeterminate` 才使状态进入待核对。

### 10.3 IntegrationError

所有 Integration application capability 使用同一稳定分类，不能返回 raw String：

- InvalidRequest。
- UnsupportedCapability。
- NotReady。
- Authentication。
- Authorization。
- Entitlement。
- RateLimited，包含 scope 和 retry_at。
- Transport，包含 delivery certainty。
- InvalidPayload。
- SequenceGap。
- ResyncRequired。
- Backpressure。
- Unavailable。

错误可以附加经过脱敏的 provider code、request ID、binding ID 和 channel ID，但不能携带 secret、authorization header 或不受控 payload。

命令收到明确的 provider 业务拒绝时使用 CommandOutcome::Rejected，不再同时包装成 IntegrationError；只有无法形成有效拒绝结果的协议、解析或可用性问题才进入 IntegrationError。

### 10.4 重试矩阵

| 操作 | 默认行为 |
|---|---|
| 纯查询 | 未收到响应时有界重试；遵守 retry-after、deadline 和 quota |
| subscription intent | 根据 ack 和 provider 规则重放相同 intent |
| SubmitOrder | MayHaveBeenSent 后不重发；返回 Indeterminate，并按同 route/account/client order ID 查证 |
| AmendOrder | 默认同 SubmitOrder；只有 provider 明确强幂等才允许自动重试 |
| CancelOrder | provider-specific；无明确幂等保证时不透明重试 |
| Transfer/Earn | 不透明重试；进入 reconciliation 或人工处理 |

services/drivers/http.rs 当前对 POST/DELETE/429/5xx 的统一三次重试必须在任何 live execution 扩展前修复。

### 10.5 未知下单结果

Execution 的业务流程：

1. 持久化 command ID、client order ID、route ID、account 和请求摘要。
2. 调用该 route 的 Integration OrderEntryConnection。
3. Confirmed/Rejected 推进业务状态。
4. Indeterminate 禁止重新选择 provider 或生成新 client order ID。
5. 使用同一 route/account 的 OrderQueryConnection、OrderEventSource、recent orders/fills 查证。
6. provider retention 内仍无法判断时保持可审计的未知状态，进入风险门禁或人工处理。

Integration 只返回外部事实和确定性，不拥有上述业务状态机。

## 11. 外部事实、幂等与顺序

### 11.1 外部事件信封

~~~rust
struct ExternalEventEnvelope<T> {
    binding_id: IntegrationBindingId,
    participant: ParticipantRef,
    connection_instance_id: ConnectionInstanceId,
    channel_id: Option<ChannelId>,
    channel_epoch: Option<ChannelEpoch>,
    source_event_id: Option<SourceEventId>,
    source_sequence: Option<SourceSequence>,
    occurred_at: Option<Timestamp>,
    received_at: Timestamp,
    replay: ReplayKind,
    snapshot: SnapshotSemantics,
    correlation: Option<CorrelationId>,
    payload: T,
}
~~~

Integration 信封不包含 BusinessModuleId、ExecutionRouteId 或业务状态。业务 worker 在进入 Actor 前补充自己的 route/source context。

规则：

- received_at 必填；occurred_at 缺失时不得用 received_at 冒充。
- source sequence 保持 provider 原始顺序域，不制造全局 sequence。
- channel epoch 只表示技术通道代次，不表示业务 generation。
- replay 区分 Live、ProviderReplay、LocalReplay。
- snapshot 区分 Full、Partial、Delta，并带 as_of、watermark、completeness。
- 只有明确 authoritative Full 的空快照才能表达远端为空。

### 11.2 外部事实不是业务事实

Integration 可以返回：

- ExternalOrderAck。
- ExternalOrderUpdate。
- ExternalFill。
- ExternalBalanceSnapshot。
- ExternalPositionUpdate。
- ExternalTrade。
- ExternalBookDelta。

业务 composition/services 显式映射为：

- Execution order/fill fact。
- Account balance/position fact。
- Market observation/order-book input。
- Reference catalog fact。

Integration 不直接构造 Execution domain Order、Account Position、Market OrderBook 或 Reference canonical Instrument。

### 11.3 幂等

- Integration 在 provider 提供稳定 event/trade ID 时过滤 exact duplicate。
- 业务 Actor 仍用包含 route/source 的业务幂等键保证一次业务效果。
- provider order ID、fill ID 不是全局唯一，必须至少与 participant、principal/account、product 或 binding 一起使用。
- 没有稳定 ID 时，adapter 定义 provider-specific deterministic key，并把 collision 暴露为错误或冲突事实。
- Bybit 等重复 Filled 不得形成第二笔业务成交。

### 11.4 顺序保证

有状态 channel 的 provider-native protocol contract 可以声明顺序语义；这不是通用 capability descriptor，也不参与能力发现：

- ProviderOrdered。
- ChannelOrdered。
- CorrelationOnly。
- Unordered。

不同 binding、channel、业务进程之间不推导全局顺序。FIX 乱序 response 按 ClOrdID/request ID 关联；order book 依靠 provider sequence/checksum/recovery contract。

## 12. 背压与恢复

### 12.1 背压规则

- provider reader 不得阻塞在任一慢消费者上。
- 每个消费者使用独立有界队列和 lag 计数。
- order、fill、execution report、book delta 溢出时不静默 drop；产生 Lagged 或 ResyncRequired。
- balance、position、top-of-book 只有契约明确 latest-state 时才允许 coalesce。
- trade tape/full-depth delta 是否允许丢弃由具体 capability 配置决定。
- queue size 是运行参数，不是正确性保证。

Tokio broadcast 的 bounded queue 和 Lagged 语义可以作为实现参考：[Tokio broadcast](https://docs.rs/tokio/latest/tokio/sync/broadcast/index.html)。

### 12.2 恢复责任

Integration provider connection/ChannelRuntime 负责：

- reconnect、authentication、heartbeat。
- subscription intent 恢复。
- protocol sequence/checksum/cursor/resend。
- exact duplicate filtering。
- 检测 gap 并产生 ResyncRequired。

业务负责：

- Execution：open/recent orders、fills 与本地订单状态 reconciliation。
- Account：balance、position、equity 与业务状态 reconciliation。
- Market：snapshot + delta/checksum/replay 后重建 order book。
- Reference：catalog/version 重新加载。

Connection channel Live 不等于业务 Ready。

### 12.3 流恢复屏障

provider 能支持时使用：

1. 建立 channel，认证并递增 channel epoch。
2. 订阅并开始有界缓冲。
3. 查询 snapshot/history 或从 cursor replay。
4. 用 watermark/sequence/time boundary 合并缓冲事件。
5. exact dedupe、gap 和 completeness 校验。
6. 发出 RecoveryCompleted 或 ResyncRequired。
7. 业务 Actor 完成 reconciliation 后开放业务 readiness。

没有可靠 watermark 时使用 subscribe-first + snapshot/history + 第二次有界核对。缓冲溢出必须重新开始恢复，不能假设仍完整。

### 12.4 行情与 FIX

Market source 按 provider/dataset 选择：

- SnapshotThenDelta。
- ReplayFromCheckpoint。
- NaturalRefresh。
- ChecksumRebuild。
- ProviderSpecific。

FIX 本地持久化：

- incoming/outgoing sequence。
- message store。
- schedule/reset policy。
- resend/gap-fill progress。

TCP reconnect 后必须完成 Logon 和 sequence recovery 才能标记 channel Live。

## 13. Credential、Quota 与多进程约束

### 13.1 Credential

业务 composition 只传 CredentialId 给 Integration provider factory，由 credential resolver 在 composition/integration 边界解析：

- secret 不进入 ConnectionDescriptor。
- secret rotation 更新 provider principal context/auth generation。
- startup preflight 验证 permission，不输出 secret。
- 不同 credential 默认不合并，即使当前值相同。

Provider manifest 声明：

~~~rust
enum CredentialConcurrency {
    ConcurrentProcessesSafe,
    DistinctCredentialPerProcess,
    ExclusiveProcess,
}
~~~

Workspace/System 在启动前检查 credential 分配，不使用共享文件锁或临时 IPC 偷偷协调 credential 并发。此规则不禁止 13.3 节 quota ledger 在初始化 immutable slot metadata 时短暂使用文件锁；provider 请求额度抢占仍只使用 mmap atomics。

### 13.2 同进程 quota

provider concrete context 按真实维度协调：

- IP。
- credential/principal/UID。
- account。
- connection/channel。
- endpoint group。
- instrument/product。

一个 operation 可以同时消耗多个 quota。优先级默认：

~~~text
撤单和风险降低
  > 新订单
  > reconciliation
  > bulk/history
~~~

是否共享 REST/WS 额度由 provider adapter 事实决定，不由通用 Transport enum 推断。

#### 13.2.1 Provider 技术配额与 Risk 业务配额

“配额”必须按意图拆成两个 owner，不能因为都表现为 rate limit 就合并：

| 配额 | 示例 | Owner | 拒绝/等待的含义 |
|---|---|---|---|
| Provider 技术配额 | Binance request weight/order count、WebSocket connect/subscribe、IBKR pacing、Massive request limit | Integration concrete provider/principal/channel context | 当前调用会违反外部协议或触发 ban；适用于 command、query、stream control、bulk |
| 业务风险配额 | 策略/账户下单频率、单笔名义金额、总敞口、预算、日损、concentration | Risk application/domain | 当前业务意图超过风险授权；只处理业务 command，不解释 provider HTTP/WS 计数 |

调用顺序通常是 `Execution -> Risk authorization -> Integration provider throttler -> provider`。Risk 允许不代表当前可以立即写 provider；Integration 有技术容量也不代表业务获准交易。尤其：

- account/reference/history query 消耗 provider 配额，但不应经过 Risk；
- cancel、reduce-risk 和 reconciliation 必须有 Integration 技术保留容量，不能依赖 Risk 进程在线；
- Risk 可以限制 submit/modify 的业务速率，但不能替代 provider adapter 对所有 endpoint weight、429 和连接上限的处理；
- 同机跨业务进程的 provider scope 由 Workspace/System 配置并映射共享 mmap ledger，Integration 原子抢占；Risk 不成为中心 request proxy。

这与成熟项目的分层一致：CCXT 把 limiter 放在 exchange instance；Hummingbot 把 `AsyncThrottler` 放在 connector；NautilusTrader 一方面要求同 venue scope 的 adapter client 共享 limiter，另一方面由独立 RiskEngine 检查 submit/modify rate，cancel/query 不走 Risk。

### 13.3 同机跨进程共享 quota ledger

同一台机器上的 Account、Execution、Market、Reference 可能经同一公网 NAT/代理出口访问 provider，也可能使用同一个 principal。固定给每个进程切片会浪费空闲额度；因此 Workspace/System 为整台机器提供一个 mmap quota ledger，所有 Integration provider context 在真正发请求前通过原子 CAS 抢占对应 slot。它共享额度，不共享连接：

~~~text
Execution process ─┐
Account process ───┼─ mmap atomic quota ledger ── provider limits
Market process ────┤          ↑
Reference process ─┘    no request proxy

each process still owns its HTTP/WS/FIX/native connections
~~~

slot key 不是简单的 `网卡 + account` 拼接键，而是一个真实 provider 限额维度：

~~~rust
struct ProviderQuotaScopeKey {
    provider: ProviderId,
    environment: ProviderEnvironment,
    scope_kind: QuotaScopeKind, // Egress, Principal, Account, Connection, InstrumentGroup
    scope_id: String,
    rate_limit_id: String,
}
~~~

`Egress` 的 `scope_id` 使用部署配置的 `egress_scope_id`，表示 provider 实际看到的 NAT/代理公网出口，不直接使用本机网卡名称。credential secret、API key 和账户敏感值不得作为 mmap key；使用稳定、非敏感的 principal/account scope ID。

一个 operation 可以声明多个 claim，例如 Binance 新单同时抢占：

1. `Egress/request-weight-1m`；
2. `Principal/unfilled-orders-10s`；
3. provider 真实存在时再加 `Account/order-count-day` 或 instrument scope。

不能只建立 `egress + account` 的组合 counter：那会让两个账户各自获得一份完整 IP 配额，仍可能把共享出口打爆。各 scope 分开计数，operation 同时消费。多 scope 按稳定 key 顺序抢占；后续 scope 失败时，已抢占额度默认不回滚，允许保守浪费到窗口结束，避免并发回滚或进程崩溃造成额度重复消费。

mmap 约束：

- 固定 magic/version/header/slot size，slot 按 cache line 对齐；运行期只做对齐的 `AtomicU64` CAS/load/store。
- 文件锁只允许用于首次创建和 immutable slot metadata 注册/校验；每次请求不得获取文件锁。
- 一个 slot 保存 scope hash、limit、reserve、window 和 packed `window_id + used`；provider observed count 只能把 used 单调抬高，不能退款。
- 进程在 acquire 后、write 前崩溃时额度浪费到窗口结束，这是安全结果；不得通过进程存活 lease 自动返还。
- command 在任何 scope acquire 失败时必须是 `NotSent`；query/bulk 可以等到 retry-after，但不能在业务 Actor 上阻塞。
- ledger 不可用或配置冲突时，required live connection 启动失败关闭；不静默退回每进程独立 limiter。
- mmap 不保存 credential、socket/session、pending command、订单或业务风险预算。
- 当前范围明确限制为单机。若多个机器共享一个公网出口，由运维保证部署隔离或按机器静态分片；本设计不增加网络一致性协议。

第一版实现固定窗口原子 slot，先覆盖 Binance request weight；rolling window、token bucket、IBKR pacing 等只有在对应 provider 切片迁移时按真实算法增加，不用固定窗口假装等价。

Workspace/System 仍配置 provider limits、safety margin 和 ledger path，但不代理每次请求：

~~~rust
struct ProviderQuotaPlan {
    provider_limits: Vec<ExternalQuotaLimit>,
    host_ledger: SharedQuotaLedgerConfig,
    safety_margin: Ratio,
}
~~~

约束：

- 对每个外部 quota scope，ledger limit 不超过 provider limit 减 safety margin。
- 同出口 IP 的不同业务映射同一个 Egress slot，不按业务复制 counter。
- 有外部程序共享 credential/IP 时增加 margin。
- provider observed count 超过本进程预期时保守退避并告警。
- 不同机器之间由运维隔离或静态分片，不把 mmap 演进为网络 coordinator。

## 14. Provider 接入准则

每增加一个 provider/product，必须提交以下清单：

1. Provider capability inventory：原生支持的 query、command、stream、bulk。
2. Identity：participant、principal、target account、product/dataset、destination。
3. Authentication/entitlement：credential 类型、rotation、concurrency。
4. Physical topology：HTTP pool、WS/FIX/native channel 和共享关系。
5. Quota dimensions：IP、UID、account、connection、instrument 等。
6. Command certainty：哪些响应是 Confirmed/Rejected/Indeterminate。
7. Retry matrix：按 capability 而不是 HTTP method。
8. Event identity/ordering：event ID、sequence、fill ID、duplicate 行为。
9. Recovery：snapshot/replay/checksum/resend。
10. Provider-specific extension：无法无损进入公共 capability 的功能。
11. Fault-injection tests。
12. Business composition example，但不得在 Integration 中实现业务 bundle。

### 14.1 Provider manifest 的作用

Manifest 是 provider 事实和 preflight 元数据，不是运行时 dispatch registry。它可以描述：

- supported capabilities。
- credential concurrency。
- quota dimensions。
- order type/TIF/client-order-id 支持。
- ordering/recovery mode。
- connection limits。

业务 composition 使用 manifest 做启动校验；实际调用仍是类型化 connection method。

### 14.2 不扭曲 Provider 的检查

实现 review 必须回答：

1. 为了公共 trait 是否丢失了 provider 返回信息？
2. 是否新增了只服务一个 provider 的通用 Option 字段？
3. 是否发生静默参数转换或能力降级？
4. provider-specific method 是否更直接、更安全？
5. 公共层是否有第二个真实实现或当前调用方？
6. 哪个旧概念会在迁移后删除？

任一问题无法回答时，功能先留在 provider concrete API。

## 15. 目标代码结构

Integration 最终只保留一套正交目录。第一维是公开语义，放在 `application/`；第二维是 participant 原生实现，放在 `services/participants/`；HTTP/WebSocket/FIX 与 quota 是跨 participant 的技术机制，分别放在 `services/transport/` 和 `services/quota/`。不得再建立与这三条轴平行的 gateway、registry、stream、runtime 或 public-reference 层。

~~~text
crates/kairos-integration/src/
  lib.rs
  bin/
    kairos-integration-cli.rs
  composition/
    cli.rs                         # 只装配 Integration 自身诊断 CLI
  application/
    error.rs
    outcome.rs
    descriptor.rs
    credential.rs
    capabilities/                 # Integration 定义的窄能力与 external facts
      account.rs                  # read / inspection / private events
      execution.rs                # order entry / query / execution events
      market.rs                   # snapshot / live / historical
      reference.rs                # instrument catalog / symbology
      funding.rs                  # earn / transfer
    participants/                 # 对外公开的 participant-native facade
      binance/
        config.rs
        types.rs                  # ConnectionDomain / InstrumentType 等 Binance 词汇
        connection.rs             # provider/principal context
        spot.rs
        funding.rs
        derivatives.rs
      okx/
        config.rs
        types.rs                  # instType / tdMode 等 OKX 词汇
        connection.rs
        trading.rs
        market_data.rs
      ibkr/
        config.rs
        types.rs
        connection.rs
        session.rs
      massive/
        config.rs
        types.rs
        connection.rs
        reference.rs
        market_data.rs
    blocking/                     # 显式同步 trait 与 handle re-export
  domain/
    participant.rs                # Exchange / Broker / DataProvider identity
    connection.rs                 # endpoint/principal/channel identity、health
    instrument.rs                 # ProviderInstrumentRef，不含 canonical ID
    operation.rs                  # request id、delivery certainty、recovery facts
  services/
    participants/                 # private protocol/SDK/normalizer 实现
      binance/
        connection.rs
        signing.rs
        quota.rs
        spot/
          account.rs
          execution.rs
          channel.rs
        funding/
          account.rs
          earn.rs
          transfer.rs
        usd_m/
        coin_m/
        options/
        market_data/
          catalog.rs
          snapshot.rs
      okx/
        connection.rs
        signing.rs
        trading/
          account.rs
          execution.rs
          channel.rs
        market_data/
          catalog.rs
          snapshot.rs
      ibkr/
        connection.rs
        session.rs
        account.rs
        execution.rs
        market_data.rs
      massive/
        connection.rs
        reference.rs
        market_data.rs
    transport/                    # 无 participant 语义的 I/O 原语
      http/
        async_client.rs
        blocking_client.rs
        request_semantics.rs
        polling.rs
      websocket/
        channel.rs
        framing.rs
      fix/                        # 有真实 FIX participant 后再落地
    quota/
      scope.rs                    # egress/principal/channel scope key
      ledger.rs                   # mmap 原子共享空间
      fixed_window.rs
      observation.rs
~~~

### 15.1 每一层只回答一个问题

| 层 | 唯一问题 | 允许包含 | 禁止包含 |
|---|---|---|---|
| `application/capabilities` | 调用者需要什么窄能力和 external fact？ | async-first trait、sync trait、请求/结果/事件 | participant 枚举 dispatch、SDK payload、业务 canonical ID |
| `application/participants` | 如何用该机构自己的词汇创建 context 并投影 concrete handle？ | Binance/OKX/IBKR 原生 config、domain、instrument type、opaque handle | `ExecutionBundle`、`AccountBundle`、全局 ProductFamily、capability metadata |
| `domain` | 哪些 Integration 语义跨 participant 稳定？ | participant/connection/instrument/operation identity 与不变量 | 业务实体、HTTP/WS client、provider endpoint 细节 |
| `services/participants` | 某一 participant 如何签名、调用、恢复和归一化？ | SDK/REST/WS/FIX、provider payload、normalizer、原生 channel lifecycle | 业务 route 选择、Reference canonical ID、跨 provider failover |
| `services/transport` | 如何安全发送一种协议请求？ | async/blocking I/O、request semantics、frame/polling 原语 | Binance/OKX 错误码或产品枚举 |
| `services/quota` | 如何在本机多进程原子抢占技术配额？ | mmap ledger、scope、算法、provider observation | 风险预算、策略频率、业务 exposure |

依赖方向固定为：

~~~text
bin -> composition -> application participant facade -> services participant implementation
                              |                         -> transport
                              |                         -> quota
                              +-> application capability <-+
                              +-> domain <------------------+
~~~

`services/transport` 和 `services/quota` 不得反向依赖 participant；`services/participants/binance` 不得依赖 OKX；任何 Integration 模块不得依赖 Account、Execution、Market、Reference 或 Risk 业务 crate。

### 15.2 Participant 内部以原生连接域分层

Participant 是实现树的第一层，participant 自己的物理/协议域是第二层，能力是这些 concrete handle 实现的 trait，而不是第三套目录或 registry key。例如 Binance 的 Spot、Funding、USD-M、COIN-M、Options 可以有不同 endpoint、签名和恢复语义；OKX 则可以按 Trading 与 MarketData context 组织。两家的第二层目录不需要人为同构。

同一个功能只能有一个实现归属：Binance instrument catalog normalizer 属于 `services/participants/binance/market_data/catalog.rs`；OKX private order channel 属于 `services/participants/okx/trading/channel.rs`。不得再放入全局 `public_reference.rs`、`okx_stream.rs` 或 `streams/` 形成第二归属。

### 15.3 Public API 形态

异步接口是主接口，调用方 runtime 直接 poll Future；同步接口位于显式 `blocking` namespace，复用同一个 provider/principal/quota context，不创建隐藏 Tokio runtime：

~~~rust
use kairos_integration::{
    capabilities::execution::AsyncOrderEntryConnection,
    participants::binance::{BinanceConnection, BinanceConnectionConfig},
};

let provider = BinanceConnection::connect(config)?;
let principal = provider.principal(principal_config)?;
let mut orders = principal.spot_order_entry();
let outcome = orders.submit(request).await?;

// 只有同步调用方显式选择 blocking projection；Tokio worker 内调用会被拒绝。
let mut catalog = provider.blocking_instrument_catalog(instrument_type);
let instruments = catalog.fetch_instruments()?;
~~~

业务需要同时持有多个 participant/账户时，在自己的 private composition/services 中定义 concrete route collection。Integration 不提供跨 participant enum、route registry 或业务 bundle。

### 15.4 现有孤立模块的唯一归宿

| 当前路径 | 最终处理 |
|---|---|
| `integration.rs`、`integration_connections.rs` | 所有调用者迁移后删除，不建立替代总 facade |
| `domain/products.rs`、根级 `ProductFamily` | 随 legacy `ConnectionSpec` 与业务旧 route 迁移后删除；连接域和 instrument type 收归各 participant，canonical 产品分类归 Reference/业务 |
| `application/providers/*.rs` | 已删除；Binance、OKX、Hyperliquid、Massive 统一从 `application/participants/<participant>/` 导出。四者的 `mod.rs` 已只承担模块声明/re-export，config/types/connection/reference/market-data 已按适用项拆分；Binance Spot/Funding 与 OKX Trading/MarketData 的 connection implementation 仍需继续物理拆分 |
| `services/gateways/*` | 已删除；实现已按 participant/native domain 拆入 `services/participants/`。Binance Funding 与 MarketData 已形成第二层原生目录 |
| `services/gateways/public_reference.rs` | 删除；normalizer 回到具体 participant 的 `market_data/catalog.rs` |
| `services/gateways/okx.rs`、`okx_stream.rs` | 拆为 OKX connection/signing/trading/market_data，不保留平行大文件 |
| `services/streams/*` | 通用 framing/polling 原语进入 `transport`；participant lifecycle 回到对应 channel |
| `services/connections/*` | 随万能 `Connection`/`ConnectionSpec` 删除；有状态 channel 自己拥有 lifecycle |
| `services/auth.rs` | 通用密码学原语最小化进入 transport/support；签名 canonicalization 留在 participant |
| 根部 `blocking.rs`、`credentials.rs` | 移入 `application/blocking` 与 `application/credential`，由 `lib.rs` 保持稳定 re-export |

当前结构迁移证据：Integration CLI 已形成 `bin -> composition/cli -> application participant facade`；公开能力只存在于 `application/capabilities/{account,execution,market,reference,funding}.rs`；Massive facade 已拆为 `config/types/connection/reference/market_data`，Hyperliquid facade 已拆为 `config/connection/reference`，Binance/OKX 已先拆出 `config/types/connection`；transport 已形成 `http/` 与 `websocket/channel.rs`，quota 已成为独立目录；`domain/operation.rs` 已承接 command delivery/recovery 稳定事实，`domain/reference.rs` 已随 canonical identity 迁回 Reference 而删除。根部 `integration.rs`、`integration_connections.rs`、`domain/products.rs` 及仍依赖它们的 legacy domain 文件是明确的未完成项，不得把当前结构标记为最终完成。

最终 `src/` 根部除 `lib.rs` 外只出现标准层目录，不再增长独立功能文件。

### 15.5 结构约束

- capability trait 只由 Integration concrete handle 或真正的测试替身实现；业务私有 enum 只用 inherent method 穷举分派。
- participant concrete connection 可以在 application facade 中持有 private inner，但 inner 类型和 SDK payload 不得泄漏。
- business composition 只导入 Integration public API，不导入 `services`。
- 没有第二个真实 participant 实现或当前泛型调用者时，不抽公共 helper/trait。
- 一次迁移一个可执行切片，并在同一切片删除旧入口、旧 normalizer 和旧状态 owner；不得长期保留双实现。

目标构造方式：

~~~rust
let provider = kairos_integration::participants::binance::BinanceConnection::connect(config)?;
let principal = provider.principal(principal_config)?;

let order_entry = principal.spot_order_entry();
let order_query = principal.spot_order_query();
let order_events = principal.spot_order_events(channel_config)?;
~~~

不再通过：

~~~rust
Integration::new()
    .with_binance_spot_order_entry(...)
    .connect_order_entry(&ConnectionSpec { ... })
~~~

## 16. 分阶段重构计划

### Phase 0：冻结边界与建立基线

目标：不改行为，先让后续删除和迁移可验证。

改动：

- 为现有 Integration capability、Execution gateway、Account stream、Market stream 增加 characterization tests。
- 列出所有 Integration::new、ConnectionSpec、IntegrationCapability、dyn Connection 调用点。
- 为 Binance Spot 和 OKX 当前使用能力建立 provider capability inventory。
- 明确每个业务进程当前配置的 provider、credential、account、product 和 required/optional source。
- 将本文架构决定作为变更 review 基线。

必须记录：

- 当前 live command 是否可能经 perform_post 重试。
- 当前每个 server 启动会创建多少 provider client/channel。
- 当前 business route/source 与 ConnectionSpec 的映射。
- 当前 provider symbol 与 canonical identity 的转换点。

退出条件：

- 所有旧调用点有 owner 和迁移 phase。
- focused tests 可以在当前分支稳定重复。
- Phase 1 不需要先引入 Registry、runtime facade 或新业务 port。

### Phase 1：命令安全与错误统一

优先文件：

- crates/kairos-integration/src/application/error.rs
- crates/kairos-integration/src/application/connection.rs 或新的 outcome.rs
- crates/kairos-integration/src/services/drivers/http.rs
- crates/kairos-integration/src/services/gateways/binance/spot/order.rs
- crates/business/execution/service/src/services/gateway.rs
- Execution 持久化/Actor 提交路径

改动：

1. 引入 CommandOutcome、DeliveryCertainty 和稳定 IntegrationError。
2. 所有 OrderEntry capability 不再返回 String。
3. HTTP driver 接收 Query/Command/Bulk 等明确请求语义。
4. 移除命令类 POST/DELETE 对 transport、429、5xx 的透明重试。
5. Execution 在发送前持久化 command ID、client order ID、route/account。
6. MayHaveBeenSent 映射为 Indeterminate，并进入同 route reconciliation。

删除：

- perform_post 中命令统一三次重试。
- provider adapter 把 timeout 简化成普通 rejection/error 的路径。

故障注入：

- write 前断连。
- provider 接受后响应丢失。
- 429 + retry-after。
- 5xx 前后不确定。
- 重复/乱序 fill。

退出条件：

- 一个 SubmitOrder 在 MayHaveBeenSent 后不会自动产生第二个远端订单。
- 查询仍有有界重试。
- 所有迁移后的 command 返回 CommandOutcome。

### Phase 2：Binance Spot 原生 Connection 与 Capability 边界

目标：建立第一个不依赖 Integration service locator 的 provider vertical slice。

改动：

1. 在 Integration application public API 暴露 Binance Spot private config、opaque concrete connection 和 factory。
2. concrete connection 内共享 HTTP client、clock、signer、IP/principal quota。
3. OrderEntryConnection、OrderQueryConnection 不再继承万能 Connection。
4. OrderEventSource 使用真实 private channel runtime。
5. 定义 ConnectionDescriptor、ProviderInstrumentRef、ExternalEventEnvelope。
6. provider factory 返回 concrete connection；不经过 with_xxx/open closure/ConnectionSpec。
7. 为 public/authenticated/entitled 场景分离配置语义。

暂不做：

- 通用 ProviderContext trait。
- SessionRegistry。
- ProviderSessionActor。
- 全 provider 文件移动。

删除：

- Binance Spot 已迁移 capability 对应的 registry entry/open closure。
- 迁移切片对 ConnectionSpec 和 Connection.start/stop/reconnect 的依赖。

退出条件：

- 单测可直接构造 BinancePrincipalConnection。
- entry/query/events 可以共享必要资源但拥有各自正确语义。
- provider-specific 字段没有被塞入通用 optional map。
- business/application 未导入 Integration services 或 SDK。

### Phase 3：Execution 单 Provider、多账户切片

目标：证明一个业务进程可以持有同 provider 的多个 principal/account binding。

优先文件：

- crates/business/execution/service/src/composition/mod.rs
- crates/business/execution/service/src/bin/kairos-execution-server.rs
- crates/business/execution/service/src/services/gateway.rs
- crates/business/execution/service/src/services/actor.rs

改动：

1. composition 一次解析 Binance provider config。
2. 为 main/hedge 等真实账户分别创建 private connection。
3. 用 ExecutionRouteId → ExecutionRouteConnections 保存 entry/query/events。
4. gateway worker 直接持有 Integration OrderEntryConnection/OrderQueryConnection。
5. external order/fill identity 带 binding/account/product，进入 Actor 前补充 route。
6. process facade 持有所有 concrete connection/channel guard 并统一 shutdown。
7. required route 与 optional route 分别聚合 health。

删除：

- 每个 capability 单独 Integration::new 的 Binance Execution 路径。
- 硬编码或错误复用的 connection_id。
- gateway 中复制初始 ConnectionState 作为真实 health 的逻辑。

测试：

- 同时启动两个 Binance account route。
- 两个账户的 order/fill ID 不冲突。
- 一个账户 channel 断开不停止另一个账户。
- Indeterminate 只在原 route/account 查询。

退出条件：

- Execution 进程同时持有至少两个真实或测试 principal binding。
- Actor 仍是唯一订单状态 owner。
- 没有 business-defined provider port。

### Phase 4：Execution 多 Provider 切片

目标：用 Binance Spot + OKX Spot/Swap 或 Bybit USDT Perpetual 验证公共能力边界。

改动：

1. 为第二 provider 实现原生 concrete connection。
2. 只有无损共性实现相同 Integration capability。
3. provider-only order/algo/position-mode 参数保留在 concrete API。
4. Execution composition 同时装配 Binance 和第二 provider route。
5. startup preflight 校验每条 required route 的 capability、credential、quota 和 recovery。
6. 业务 route 决定 provider，不允许 Integration 自动跨 route fallback。

重点验证：

- 不同 private/trade channel topology。
- REST/WS shared quota。
- duplicate Filled。
- provider-specific order type/TIF。
- source identity 和外部 order ID scope。

退出条件：

- 一个 Execution 进程可以同时向 Binance 和第二 provider 提交测试订单。
- 公共 capability 未增加只服务第二 provider 的畸形字段。
- provider 特有能力仍可通过具体 connection 使用。

### Phase 5：Account 切片

目标：Account 自己持有多 source/account connection，同时不形成订单权威。

优先文件：

- crates/business/account/service/src/composition/account.rs
- crates/business/account/service/src/bin/kairos-account-server.rs
- crates/business/account/service/src/services/integration.rs
- crates/kairos-integration/src/application/account.rs

改动：

1. composition 为每个 AccountSourceId 创建 concrete provider connection。
2. snapshot/profile/events 使用 Integration capability，不定义 Account provider port。
3. private stream 与 snapshot 共享 provider/principal 技术 context 时显式复用。
4. Full/Partial/Delta snapshot 和 completeness 进入外部事实。
5. Account 只更新余额、持仓、权益和账户同步状态。
6. 先用 `recv_account_event(timeout) + Notify` 删除忙等，再迁移为默认 async trait，直接使用 Account process shared runtime；blocking 行为只保留在 `kairos_integration::blocking`。

删除：

- server 再构造第二个 Integration 获取 stream 的路径。
- 每 segment 的旧 Integration service locator。
- Account 将 provider order event 变成权威订单状态的路径。

退出条件：

- Account crash/restart 不影响 Execution connection/readiness。
- partial empty snapshot 不会清空业务状态。
- 多账户 identity、quota 和 health 相互独立。
- 空闲 private stream 不产生固定周期 polling；事件到达后 Account runtime 能被唤醒，队列 overflow 会显式要求 resync。
- async Account stream 不创建隐藏 runtime/thread；blocking facade 在 Tokio context 中调用会被拒绝并有边界测试。

### Phase 6：Market、Reference 与 Data Provider

Market 改动：

- 直接创建 Binance/OKX/Databento/Massive concrete source。
- live、historical、bulk 不强行共享生命周期。
- sequence/checksum/replay/bulk semantics 进入 Integration external facts。
- 每 consumer 有界队列；lag 触发 resync。

Reference 改动：

- 直接创建 instrument catalog/contract search/symbology connection。
- Integration 返回 ProviderInstrumentRef 和 provider rule。
- composition 显式映射到 Reference canonical identity。
- 删除 Integration 制造 canonical MarketId 和 adapter 解析 MarketId 字符串。

当前已落地切片：

- `OkxConnection::instrument_catalog(OkxInstrumentType)` 实现 `AsyncInstrumentCatalogConnection`，`blocking_instrument_catalog` 是同一 provider context 的同步投影；两者返回 `ExternalInstrumentCatalog`，该类型没有 canonical ID 字段。
- Reference 的 `OkxSource` 接收 provider facts，并且是 OKX canonical Asset/Instrument/Listing/Market ID 的唯一生成点；旧 `okx_catalog` 与 `with_okx_reference` 已删除。
- `OkxConnection::market_snapshot(OkxInstrumentType)` 实现 `AsyncMarketSnapshotConnection`，同步 Market worker 使用 `blocking_market_snapshot`；Market 私有 `OkxSnapshotMarketFeed` 自己维护订阅、轮询状态及 provider symbol 到 canonical market 的映射。
- `OkxInstrumentType` 是 OKX 请求词汇，不再为了连接选择把 SPOT/SWAP/FUTURES/OPTION 映射为全局 `ProductFamily`；catalog 与 ticker handle 同属 `okx::ConnectionDomain::MarketData`，它们的 trait 实现即能力证明。
- `BinanceConnection::instrument_catalog(BinanceInstrumentType::{Spot, UsdMFutures, CoinMFutures, Option})` 实现同一 Integration-owned catalog capability；async 版本由调用方 runtime poll，blocking 版本拒绝 Tokio worker，二者复用 Binance provider context、HTTP driver 和 host-local quota scope。
- Binance catalog normalizer 只保留 native symbol、base/quote、instrument kind、合约元数据与 tick/step/minimum notional 等 provider rule；它不生成 `AssetId`、`InstrumentId`、`ListingId` 或 `MarketId`。
- Reference 的 Binance Spot/derivatives/options source 直接持有 concrete blocking catalog handle，并在业务边界生成 canonical identity；旧 Binance reference methods、factory selector、Integration 内 canonical normalizer 与全局 `public_reference.rs` 已删除。
- `BinanceInstrumentType` 与 `OkxInstrumentType` 都是 participant-owned 请求词汇，而不是全局 product taxonomy；能力由 concrete handle 实现 `AsyncInstrumentCatalogConnection`/`InstrumentCatalogConnection` 证明，不另存 capability 枚举。
- Massive 已直接落入目标目录 `application/participants/massive` 和 `services/participants/massive`；`MassiveConnection` 按 participant-owned `InstrumentQuery`/`MarketType` 投影 instrument catalog、live market 与 historical market handle，不再经过 `Integration::new`、`ConnectionSpec` 或 capability registry。
- Massive live/historical 实现链已完全使用 Massive-owned `MarketType::{Equity, Option}`；不再把它转换成根级产品枚举，legacy `ConnectionDescriptor.product` 在这条 participant-native 链中为空。
- Massive async catalog 使用调用方 runtime，blocking catalog 拒绝 Tokio worker；`ExternalInstrumentCatalogPage` 将 cursor、complete 与 provider facts 作为 catalog capability 的分页语义，不再借用 legacy `ReferenceCatalogPage`。
- Integration 仅保存 Massive ticker、原生 venue code、underlying、expiry、strike、right 与 contract size；Reference 将 XNAS 等原生 venue 映射为 canonical exchange，并生成 Equity/Option 的 Asset/Instrument/Listing/Market ID。
- `services/gateways/massive` 及全部 `with_massive_*` 方法已删除；Market/Reference composition 直接持有 concrete Massive handle。`AsyncMarketEventSource` 直接 await WebSocket 事件，`AsyncHistoricalMarketDataConnection` 直接 await 分页 HTTP；显式 blocking projection 拒绝 Tokio worker。当前剩余缺口是 Market 业务 adapter 切换到 async handle，以及为真实 Massive request-limit 配置 host-local quota scope。
- Hyperliquid 已形成 `HyperliquidConnection -> InstrumentCatalog` 原生链；async read-only JSON POST 在调用方 runtime 执行并保持 Query 重试语义，blocking projection 拒绝 Tokio worker。
- Hyperliquid Integration normalizer 只返回 symbol、base/quote/settlement、perpetual kind 与 `szDecimals`；Reference 才生成 Hyperliquid exchange、BTC-USDC perpetual instrument/listing/market identity。旧 `with_hyperliquid_reference` 与 `services/gateways/hyperliquid.rs` 已删除。
- `application/providers` 已整体删除，所有公开 participant facade 统一位于 `application/participants`；这消除了 providers/participants 两棵并行公开树。Binance/OKX facade 文件的下一步是按各自原生 connection domain 内部分解，而不是重新建立公共 provider 层。

退出条件：

- credentialed public data 不再被标成 private。
- Massive consolidated source、Databento dataset、Kraken checksum 可表达。
- Market provider 故障不影响其他业务进程。
- Reference 是 canonical identity 的唯一 owner。

### Phase 7：Broker、FIX 与硬 Session 约束

范围：

- IBKR username/brokerage session/account/destination 分离。
- Coinbase 或其他 FIX 的 Order Entry、Drop Copy、Market Data 独立 channel。
- FIX sequence/message store 持久化。
- ExclusiveProcess credential 启动校验。

原则：

- 只为有状态协议实现 ChannelRuntime。
- TCP reconnect 不标记业务 ready。
- 单 principal 限制不演进为中心 Integration service。
- 需要跨业务协作时写单独 ADR，并通过业务 application contract。

退出条件：

- FIX sequence gap/resend 测试通过。
- IBKR SMART 不进入 connection identity。
- 第二进程使用 ExclusiveProcess principal 时启动失败。

### Phase 8：删除旧通用 Facade

仅当最后一个调用方迁移后执行：

该阶段按切片渐进执行，而不是等待最后一天一次性删除：全局 `GatewayRegistry`、万能 `Integration::connect()` 和 factory selector traits 已先删除；根级 `providers::{binance, okx}` 是新 composition 的明确入口，`Integration` 已标记为只容纳未迁移切片的 compatibility root。以后每迁移一个 provider/domain，就同时删除该切片在 compatibility root 中的 entry、`with_*` 和 `connect_*` 路径，禁止向旧 facade 增加新方法。

- 删除 crates/kairos-integration/src/integration.rs。
- 删除 crates/kairos-integration/src/integration_connections.rs。
- 删除 ConnectionSpec。
- 删除 IntegrationCapability enum 及其运行时 dispatch/诊断镜像。
- 删除根级 ProductFamily/products.rs；participant-native connection domain 与 instrument type 留在各自 participant namespace，业务 route 分类由对应业务拥有。
- 全局 `GatewayRegistry` 已删除；继续按迁移切片删除 legacy capability entry/open closure。
- 删除或收窄 application/connection.rs 中万能 Connection。
- 删除未使用的重复 OrderRequest、ExecutionReport、MarketQuote、MarketTrade 等旧模型。
- 删除 provider symbol 与 MarketId 字符串约定。
- 删除所有业务中的 Integration::new().with_xxx().connect_xxx 路径。

静态退出检查：

~~~text
rg 'Integration::new|ConnectionSpec|IntegrationCapability|dyn Connection\b' crates
rg "kairos_integration::services" crates/business
rg "MarketId.*split|split.*MarketId|parse.*MarketId" crates/kairos-integration
~~~

第一条中的四个旧概念最终都必须零命中；第二条必须无业务生产代码命中。

## 17. 测试与验收矩阵

### 17.1 架构边界

- Integration 不依赖任何 business crate。
- business application API 不暴露 Integration connection、SDK 或 vendor payload。
- business composition/services 不定义镜像 provider trait。
- business 不导入 kairos-integration services。
- provider concrete factory 只通过 Integration public/application API 暴露。
- 不存在中心 Integration server/proxy。

### 17.2 多 Provider、多账户

- Execution 同时持有 Binance + OKX/Bybit route。
- 同 provider 两个账户共享允许共享的技术资源，但 signer/private channel/账户 quota 独立。
- route A 断连不改变 route B 的 channel epoch 或 health。
- provider order/fill ID 在不同 route/account 不冲突。
- required route 失败影响业务 ready；optional route 失败只进入 degraded。

### 17.3 命令安全

- write 前失败返回 NotSent。
- write 后响应丢失返回 Indeterminate。
- Indeterminate 不自动重发、不自动换 provider。
- client order ID 在 reconciliation 中保持不变。
- duplicate/out-of-order/replayed fill 只产生一次业务效果。
- cancel/amend/transfer 按 provider 规则验证。

### 17.4 恢复和数据完整性

- subscribe 与 snapshot 之间发生的事件不丢失。
- recovery buffer overflow 产生 ResyncRequired。
- Partial empty snapshot 不清空业务状态。
- order book sequence gap/checksum mismatch 停止使用脏 book 并重建。
- FIX 完成 Logon/sequence/resend 后才 Live。
- slow consumer 不阻塞 provider reader。

### 17.5 Identity、credential 与 quota

- IBKR participant/account/SMART destination 分离。
- public market data 可以 credentialed + entitled。
- consolidated event 可携带 source venue，但 participant 仍是 data provider。
- ExclusiveProcess credential 不能分配给两个业务进程。
- 所有 ProcessQuotaAllocation 之和不超过 limit 减 safety margin。
- REST/WS 是否共享 quota 按 provider manifest 验证。

### 17.6 每阶段命令

先运行变更模块的 focused tests，再运行：

~~~text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
~~~

若全仓被无关既有失败阻塞，记录精确失败并仍运行最窄有效检查。

## 18. 可观测性

Integration connection/channel 输出低基数指标：

- binding_id、participant、environment、product scope。
- connection/channel state、capability readiness。
- reconnect_total、auth_failure_total、channel_epoch。
- sequence_gap_total、checksum_failure_total、resync_total。
- command_outcome_total：Confirmed/Rejected/Indeterminate。
- quota_wait、provider_429、cooldown、allocation utilization。
- consumer_lag、queue_depth、queue_overflow、coalesced_total。
- external_duplicate_total。

业务进程补充：

- business module/process instance。
- route/source ID。
- business readiness。
- reconciliation duration/result。
- business idempotency hit。

日志 correlation 至少包含：

- Integration binding ID。
- business route/source ID，由业务 worker 添加。
- command ID、client order ID。
- provider request/order/fill ID。
- connection/channel ID 和 epoch。

禁止记录 credential value、签名、authorization header、OAuth token 或未经清洗的 payload。

## 19. 明确拒绝的替代方案

### 19.1 中心 Integration 进程

拒绝原因：改变故障域、部署、延迟和业务自治；用户明确要求每个业务管理自己的连接。

这里不能类比 Aeron Media Driver。Media Driver 共享的是进程间 transport fabric：它不持有交易所 principal、不签名订单、不决定 request weight，也不把 provider command 的交付确定性变成一次跨进程 RPC。若建立 `kairos-integration-server` 或“Integration 共享空间”来代理 provider 请求，会产生新的中心故障域、跨进程排队和取消语义、credential 汇聚风险，并让一个业务的慢查询/重连影响另一个业务的撤单。

允许共享的基础设施事实只有：

- Workspace/System 写入的只读 provider manifest、credential allocation 和静态 quota plan；
- 同机 `provider-quota.mmap` 中不含 secret 的原子技术配额 slot；它由各进程内 Integration 直接 CAS，不代理请求；
- Aeron/共享内存承载已经由业务 owner 发布的外部事实或业务事件；
- 不包含 secret 的 health、quota observation 和 telemetry snapshot；
- Market 业务按自身职责发布公共行情，其他业务消费 Market contract，而不是绕过 Market 直接共享 Integration socket。

不允许放入共享空间的内容：provider private connection/socket、signer/secret、pending command、FIX/IBKR session state、订单 command queue，以及跨业务的动态 get-or-create connection registry。共享范围必须小于业务连接所有权边界。

只有未来出现“provider 明确要求全 workspace 唯一物理 session，且无法为业务分配独立 principal/session identity”的硬证据，才单独写 ADR 评估专用 gateway process；它是该 provider 的部署例外，不把 Integration 整体改造成中心服务。

### 19.2 通用 SessionRegistry + ProviderSessionActor 作为第一原则

拒绝原因：HTTP 不是 session，当前调用方可以由 composition 显式创建；统一 actor 会把独立 command/query/bulk lane 不必要地串行化。只有真实重复 acquire 证据出现后按 provider 局部解决。

### 19.3 业务自定义 Provider Port

拒绝原因：重复 Integration API，并迫使 provider 实现适配业务形状。业务只定义业务命令、route/source 和组合记录。

### 19.4 Integration 定义 Execution/Account/Market Bundle

拒绝原因：Integration 会感知业务模块对能力的组合方式。Integration 提供能力，业务 composition 组合。

### 19.5 万能 execute(operation, payload)

拒绝原因：丢失编译期类型、错误/重试差异和 provider 扩展；形成第二套 dispatch。

### 19.6 只暴露 Provider SDK 或 Raw Payload

拒绝原因：认证、错误、确定性、identity、recovery 和 normalization 会在业务中重复；vendor 类型会穿透业务 application。

### 19.7 Integration 内自动跨 Provider Failover

拒绝原因：跨 provider 改变订单目的地、账户、风险和业务意图，必须由 Execution 明确决定。

## 20. 当前默认与未来触发条件

| 主题 | 当前默认 | 何时重新评估 |
|---|---|---|
| Provider context | provider-specific concrete context | 第二个真实实现出现重复且语义一致 |
| Registry | 无；composition 显式创建并 Arc 共享 | 同进程真实重复 acquire 造成冲突且无法合并 composition |
| Session Actor | 仅 provider-specific stateful ChannelRuntime | 多个有状态 provider 有相同 owner/caller/恢复语义 |
| Session TTL | 无；随业务进程生命周期 | 指标证明 idle connection 有实质资源压力 |
| 同机跨进程 quota | Workspace/System provision 的 mmap atomic ledger；Integration 按真实 scope 抢占 | provider 需要 fixed-window 以外算法时增加对应 slot algorithm |
| 跨机器 quota | 运维隔离出口或按机器静态分片 | 明确出现同一公网出口必须被多机动态共享的生产约束 |
| Provider plugin registry | 无 | 有外部动态 provider 加载的当前需求 |
| Provider extension | concrete method/typed extension trait | 第二 provider 证明可无损上提 |
| Central Integration service | 不允许 | 架构基线由单独决策明确改变 |

这些默认保证 Phase 1 至 Phase 4 没有抽象前置阻塞。

## 21. 重构变更说明模板

每个非平凡抽象或 provider 切片在 PR/变更说明中回答：

1. 当前解决什么具体问题？
2. 当前调用方是谁？
3. 为什么现有 owner/boundary 不足？
4. 最简单的实现是什么？
5. 哪个旧概念在迁移后删除？
6. 哪个测试或指标证明变更有效？
7. 是否保留 provider 原生语义？
8. 是否新增业务对 Integration services/SDK 的依赖？
9. 是否改变多进程连接 owner？
10. 命令的 Confirmed/Rejected/Indeterminate 如何验证？

没有删除项或测试证据的抽象，不进入公共层。

## 22. 完成定义

当且仅当满足以下条件，本设计才算落地：

- Account、Execution、Market、Reference 保持独立进程并分别管理自己的 provider connection。
- 一个 Execution 进程可以同时持有多个 provider、多个账户和多条 route。
- Integration 不感知 Execution/Account/Market/Reference 的业务组合。
- capability trait、provider concrete connection、外部事实和错误由 Integration 定义。
- 业务没有镜像 provider port，只在 composition/services 中组合 Integration capability。
- provider 原生特性可以通过具体 connection 使用，公共能力没有畸形 option map。
- Integration service locator、ConnectionSpec、万能 Connection lifecycle 和运行时 capability dispatch 已从全部迁移切片删除。
- 根级 `ProductFamily` 已删除，Integration domain 不再用一个枚举统一 Exchange、Broker、Data Provider 的产品/账户/数据集语义。
- HTTP command 不透明重试已删除，Indeterminate 和同 route reconciliation 通过故障注入。
- provider instrument 与 canonical Reference identity 显式映射，不再解析字符串约定。
- 有状态 channel 的 sequence、checksum、replay 和恢复经过验证。
- 多进程 credential concurrency 和 quota allocation 在启动前验证。
- 至少一个非 Binance 交易 provider、一个 Broker/FIX 场景和一个 Data Provider 场景验证了边界。
- 每个业务的 connection health、backpressure 和 recovery 相互隔离。
- 全仓架构检查、focused tests 和相关 workspace checks 通过。

在此之前，项目应声明正在按 provider capability + business composition 模型迁移，不应声明已经具备完整的命令安全、恢复一致性或多 provider 生产能力。
