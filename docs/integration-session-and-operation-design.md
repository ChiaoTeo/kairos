# Integration 外部能力、连接与接入设计

## 1. 文档用途

本文是 Integration 的长期架构基线，定义外部 provider 能力、连接、操作、身份和恢复语义。本文不记录某次迁移已经完成了哪些切片；实现进度、测试结果和遗留事项记录在 `docs/integration-migration-status.md`。

本文约束新增 provider、业务 composition、connection capability、外部事实和运行时控制代码。若实现与本文冲突，先修正设计或记录明确的架构决策，不通过兼容 facade 长期掩盖冲突。

## 2. 架构结论

### 2.1 不可改变的决定

- Integration 拥有 provider 原生连接、认证、quota、外部事实 normalizer 和 capability projection。
- Account、Execution、Risk、Market、Reference 各自拥有业务状态和业务组合方式。
- 业务进程在自己的 composition 中选择具体 provider connection；不存在中心 Integration server、共享 provider socket 或跨业务动态 connection registry。
- capability 使用类型化接口和具体 handle 表达，不使用运行时 capability enum 作为第二套 dispatch。
- HTTP 不是 session。只有需要持续状态、顺序、重连或 resync 的协议才建立 channel runtime。
- async API 是默认 API；同步 API 只通过 `kairos_integration::blocking` 显式提供。
- provider 之间不由 Integration 自动 failover。跨 provider 的目的地、账户、风险和业务意图由业务决定。

### 2.2 依赖方向

```text
business bin -> business composition -> kairos-integration application
                                      -> business application/services
                                      -> domain
```

Integration application 可以依赖 Integration services 和 domain，但业务 application 不得依赖 Integration services、SDK 或 provider payload。业务只消费 Integration application 定义的业务无关 capability 和外部事实。

### 2.3 两种 Execution

- Execution simulation/backtest 是业务自身的执行模型，不伪装成 provider connection。
- Execution live route 使用 Integration 的 OrderEntry、OrderQuery 和 OrderEvent capability；route/account/binding 仍由 Execution composition 和 Actor 拥有。

## 3. 所有权边界

### 3.1 Integration 拥有

- provider/principal context、endpoint、signer、credential binding 和技术 quota；
- provider-native instrument、venue、order、fill、account、market-data facts；
- command/query/stream 的 provider 交互、错误分类、交付确定性和恢复；
- capability handle、connection descriptor、channel health、channel epoch 和 provider request identity；
- provider-specific config 和 provider-specific extension。

### 3.2 业务模块拥有

- Account：余额、持仓、权益、freshness、intent 和账户侧订单事实；
- Execution：订单生命周期、执行审计、route、client order ID 和 reconciliation；
- Risk：预算、reservation 和风险决策；
- Market：观察、order book、订阅意图、freshness 和业务 feed；
- Reference：canonical asset/instrument/listing/market identity 和生命周期；
- Workspace/System：路径、进程、实例资源、credential allocation 和启动协调。

跨业务协作属于 application orchestration 或 system composition。domain 不依赖另一个业务模块的 application、services、protocol 或 infrastructure。

### 3.3 类型归属

provider payload 在 Integration boundary 内转换成 Integration-owned external facts；canonical identity 由 Reference 生成；业务 application 接收业务请求、结果和错误，不接收 SDK client、raw payload、persistence record 或 composition record。

## 4. Connection 与 capability 模型

### 4.1 Provider-native connection

每个 provider 用自己的 config、connection domain、principal context 和 instrument vocabulary 表达真实约束。公共 capability 只提升无损共性，例如：

- `OrderEntryConnection`；
- `OrderQueryConnection`；
- `AccountReadConnection`；
- `AsyncMarketEventSource`；
- `AsyncMarketSnapshotConnection`；
- `InstrumentCatalog`。

公共 capability 不得增加只服务一个 provider 的 optional map 或泛化字段。provider-only 能力保留在具体 connection 或 typed extension 中。

### 4.2 逻辑能力与物理连接

一个 provider context 可以显式投影多个 capability handle。HTTP client、clock、signer 和 provider quota 可以共享；private channel、principal/account auth、subscription、channel epoch 和业务 route 不得因共享物理资源而混淆。

业务可以为不同 principal、账户、用途或故障域创建多个物理 binding。目标不是全局只有一个 connection，而是同一 binding 不被无意重复构造。

### 4.3 不使用万能 Connection

能力不继承同时暴露 `start/stop/reconnect` 的万能父接口。HTTP command/query 是无状态 operation；有状态 channel 自己管理 connect、disconnect、reconnect、health、ordering、backpressure 和 resync。

不提供 `execute(operation, payload)`、`IntegrationOperation` 或将 Execution/Account/Market bundle 放入 Integration 的 facade。

## 5. 身份、配置与 quota

至少分离以下身份轴：

1. provider/participant；
2. environment；
3. provider context；
4. principal/account；
5. destination/source venue；
6. provider instrument 与 canonical instrument。

`ConnectionDescriptor` 只描述稳定外部 binding 和 provider-native connection domain；route/source key、业务 intent 和 canonical identity 由业务或 Reference 保存。不得从 `MarketId` 字符串反解析 provider symbol，也不得把 IBKR SMART 等 destination 误当成连接 identity。

Credential value、签名、authorization header 和 token 不进入 application API、日志、共享 quota ledger 或进程间 contract。credential allocation 和 `ExclusiveProcess` 约束在启动时校验。

Quota 必须按真实 provider scope 建模：provider/IP、principal/account、REST/WS 和 command/query lane 的共享关系由 provider manifest 声明；同机跨进程可使用不含 secret 的原子 quota ledger，但 ledger 不代理请求、不保存 pending command、不保存 session 或 signer。

## 6. 生命周期与 async 语义

### 6.1 Async-first

异步 capability 必须在调用方 runtime 中执行，不创建隐藏 runtime、线程或阻塞等待。blocking projection 位于 `kairos_integration::blocking`，并在 Tokio worker 中明确拒绝。

### 6.2 Readiness

TCP/WebSocket 建立不等于业务 ready。ready 至少应满足认证、订阅或 Logon、必要 snapshot/recovery 和 provider-specific readiness 条件。required route 失败影响业务 ready；optional route 进入 degraded。

### 6.3 Stateful channel

有状态 channel 必须定义 ordering、sequence、epoch、duplicate policy、reconnect、resubscribe、snapshot/replay、recovery barrier、每个 consumer 的有界队列、overflow 行为和 `ResyncRequired` 语义。reader 不被慢 consumer 阻塞；shutdown 不静默丢弃已经接收的 command 或 event。

## 7. Command、query 与 stream

### 7.1 三类操作

- Command：可能改变外部状态，例如下单、撤单、转账；
- Query：只读且可安全重试；
- Stream：持续外部事实，必须定义顺序和恢复。

### 7.2 交付确定性

Command 结果至少区分 `Confirmed`、`Rejected`、`NotSent` 和 `Indeterminate`。command 在可能写出后不得透明重试，不得自动换 provider。`Indeterminate` 必须保留 command ID、client order ID、route/account/binding，并在同 route/account 上 reconciliation。

Query 可使用有界重试和 provider-specific backoff。429、5xx、transport error、authentication、authorization、validation 和 provider rejection 必须保持可区分。

### 7.3 外部事件

外部事件信封至少包含 provider、binding、channel、epoch、provider event ID、sequence、observed time、received time 和 normalized facts。重复或乱序事件不能造成重复业务效果；外部事实不直接成为业务权威状态。

## 8. Market 特别约束

Market composition 拥有订阅意图、source-to-canonical 映射和业务 feed lifecycle；Integration 只提供 normalized market facts 和 provider channel/snapshot capability。

Market 的 provider adapter 默认使用 Integration async capability，并由 Market process runtime 驱动。不得用 blocking worker 作为 live provider 的默认接入方式；blocking 只保留给明确的 CLI、离线工具或同步调用方。

REST snapshot、WebSocket live、historical bulk 和 replay 是不同能力，不强行共享生命周期。order book 的 sequence gap/checksum failure 必须停止使用脏状态并触发单 market resync；snapshot 与增量之间的 recovery barrier 不能丢事件。

## 9. 目标代码结构

```text
crates/kairos-integration/src/
  application/{capabilities,participants,blocking}
  application/participants/<provider>/
    connection.rs
    connection/{account,execution,funding,market,reference,blocking}.rs
  composition/
  domain/
  services/{participants,transport,quota}

crates/business/<module>/service/src/
  bin/ composition/ application/ services/ domain/
```

Provider 的 `connection.rs` 只保存 provider context、principal context、配置校验和 capability
投影构造。Capability handle、协议交互实现和 normalizer 按所属 domain 放入
`connection/<capability>.rs`；显式同步兼容实现统一放在 `connection/blocking.rs`。Provider
差异通过这些具体类型表达，不为拆文件增加 manager、registry、通用 dispatcher 或第二套
facade。没有相应能力的 provider 不创建空目录或占位模块。

`bin` 只解析输入、加载 workspace 资源、调用 composition 和运行 facade。`composition` 选择 concrete implementation。`application` 暴露业务 use case。`services` 保存私有 actor、adapter、persistence 和 publisher。`domain` 保存实体、value object、验证和不变量。

## 10. 分阶段改造计划

### Phase 0：冻结边界

盘点所有权、构造拓扑、provider symbol/canonical 转换、错误语义和旧 facade 调用者；定义 focused tests 和删除项。

### Phase 1：命令安全

统一 `CommandOutcome`、`DeliveryCertainty` 和 `IntegrationError`；拆分 query/command 重试；为写前失败、写后响应丢失、429/5xx 和 reconciliation 增加故障测试。

### Phase 2：首个 provider vertical slice

建立 participant-native connection、principal context、typed capability 和 private channel；删除该切片的 registry、`ConnectionSpec` 和万能 lifecycle 路径。

### Phase 3–4：Execution 多账户、多 provider

Execution composition 同时持有多个 principal、账户和 provider route；route readiness、quota scope、duplicate fill、provider-specific order type 和同 route reconciliation 必须经过测试。

### Phase 5：Account

Account 直接组合多个 source/account connection；snapshot completeness、private stream、freshness、quota 和 health 互相隔离；async stream 不轮询等待、不创建隐藏 runtime。

### Phase 6：Market、Reference、Data Provider

Market 使用调用方 async runtime；live、snapshot、historical 和 replay 分开建模；Reference 是 canonical identity 唯一 owner；provider instrument ref 不携带 canonical ID。

### Phase 7：Broker、FIX 与硬 session

分离 brokerage session、principal、target account 和 destination；实现 FIX Logon、sequence、resend、message store、drop copy 和 `ExclusiveProcess` 启动约束。

这里的 FIX 工作以真实 provider/destination 和业务调用者为前提。当前 IBKR TWS/Gateway
slice 负责验证 Broker hard-session、principal、target account 与 `ExclusiveProcess` 边界；
在没有 FIX provider、配置或调用者时，不创建通用 FIX session、message store 或占位 facade。

### Phase 8：删除旧 facade

最后一个调用者迁移后删除旧 root facade、`ConnectionSpec`、运行时 capability dispatch、根级 product taxonomy、provider symbol 字符串约定和未使用的重复模型。每个切片应在迁移时同步删除自己的旧路径。

## 11. 验收矩阵

### 11.1 架构边界

- 业务 production code 不导入 Integration services、SDK 或 raw provider payload；
- composition 显式选择 provider-native constructor；
- mutable business state 只有一个 Actor owner；
- Integration 不定义业务 bundle、中心 registry 或万能 operation dispatch；
- canonical identity 只由 Reference 生成。

### 11.2 多 route 与隔离

- 一个业务进程可同时持有多个 provider、principal、账户和 route；
- route A 的断连不改变 route B 的 health、epoch 或 queue；
- required/optional route readiness 正确聚合；
- provider/IP quota 与 principal/account quota 的共享关系经过验证。

### 11.3 命令与恢复

- write 前失败是 `NotSent`；
- write 后响应丢失是 `Indeterminate`；
- `Indeterminate` 不自动重发、不自动换 provider；
- duplicate、out-of-order、replayed fill 只产生一次业务效果；
- snapshot/stream recovery 不丢事件；
- queue overflow、sequence gap、checksum failure 明确触发 resync。

### 11.4 身份与安全

- provider、environment、principal、account、destination、source venue 和 instrument identity 分离；
- credential value、签名和 token 不进入日志或 contract；
- credential concurrency、quota allocation 和 `ExclusiveProcess` 在启动前校验。

### 11.5 每阶段验证

先运行变更模块的 focused tests，再运行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

若无关既有失败阻塞全仓检查，必须记录精确失败并运行最窄有效检查。

## 12. 可观测性

Integration connection/channel 指标至少包含 binding、participant、environment、product scope、state、readiness、reconnect、auth failure、epoch、sequence gap、checksum failure、resync、command outcome、quota wait、consumer lag、queue depth、overflow 和 external duplicate。

业务进程补充 module/process instance、route/source、business readiness、reconciliation result 和 idempotency hit。日志 correlation 至少包含 binding ID、route/source ID、command ID、client order ID、provider request/order/fill ID、channel ID 和 epoch。

## 13. 明确拒绝的替代方案

- 中心 Integration 进程：会合并故障域、credential、排队和取消语义；
- 通用 SessionRegistry/ProviderSessionActor：会把无状态 HTTP 和独立 command/query lane 错误串行化；
- 业务自定义 provider port：重复 Integration API 并让 provider 适配业务形状；
- Integration 定义业务 bundle：让 Integration 感知业务组合；
- `execute(operation, payload)`：丢失类型、错误和重试语义；
- 只暴露 SDK/raw payload：让认证、normalization、recovery 和 identity 穿透业务；
- Integration 内自动跨 provider failover：改变订单目的地、账户和风险意图。

## 14. 完成定义

当且仅当满足以下条件，设计才算落地：

- Account、Execution、Market、Reference 独立管理自己的 provider connection；
- 一个 Execution 进程可以同时持有多个 provider、账户和 route；
- Integration 不感知业务模块的组合方式；
- provider concrete connection、capability、external facts 和错误由 Integration 定义；
- 业务不镜像 provider port，也不导入 Integration private services；
- HTTP command 不透明重试，`Indeterminate` 和同 route reconciliation 可验证；
- provider instrument 与 canonical Reference identity 显式映射；
- 有状态 channel 的 sequence、checksum、backpressure、replay 和 recovery 可验证；
- credential concurrency、quota allocation 和硬 session 约束可在启动前验证；
- 至少一个 Exchange、一个 Broker/FIX 和一个 Data Provider 场景验证边界；
- 各业务的 connection health、backpressure 和 recovery 相互隔离；
- 架构检查、focused tests 和相关 workspace checks 通过。

在此之前，项目只能声明正在按 provider capability + business composition 模型迁移，不能声明已经具备完整的多 provider 生产能力。
