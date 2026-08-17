# Market Data Capability 与 Execution Route 收敛设计

## 1. 文档状态

- 状态：已实施；本文件是 Access 收敛与后续演进的架构权威。
- 范围：Reference 中的 `MarketDataAccess`、`ExecutionAccess`，以及 Market、
  Execution、Account、Python/CLI 和跨进程 contract 对它们的消费。
- 目标：以真实用户场景为起点，将系统能力、运行状态和已发生业务事实拆开，
  删除没有独立持久化价值的 Access 目录与生命周期。
- 不包含：引入智能路由算法、统一 provider adapter、重写 canonical Market 模型、
  改变 Account/Risk 的业务所有权。

Canonical Market 与 provider-native route 的边界随后由
[`reference-identity-and-business-route-separation.md`](./reference-identity-and-business-route-separation.md)
细化：Reference 仍不恢复两个 Access，但 provider provenance、product 和 symbol 也不再
寄生于 canonical Market；它们分别由 Market 和 Execution 的 composition/route 所有。

本文替代以下既有设计判断：

- Reference 拥有 market-data/execution access address 的生命周期；
- `MarketDataAccess` 和 `ExecutionAccess` 是 Reference 顶级实体；
- Market、Execution、Account 通过 Reference 持久化 access projection 获得运行地址。

实施已同步修订 `reference-module-structure-refactoring.md`、
`market-module-structure-refactoring.md`、`execution-module-structure-refactoring.md` 和
Reference README，避免并存两个互相冲突的架构权威。

## 2. 决策摘要

本提案作出以下目标决策：

1. Reference 只拥有 canonical Asset、Instrument、Listing、Market 及其有效关系，
   不再拥有 `MarketDataAccess` 或 `ExecutionAccess`。
2. “系统支持哪些行情/交易能力”是 Integration provider 实现的代码能力，不因可查询
   就自动成为数据库事实。
3. “当前 workspace 配置了什么”由 Workspace composition 解释；“当前是否 ready”由
   Market 或 Execution runtime 拥有。二者都不是 Reference 生命周期。
4. Market 在 composition 中把 canonical Market、provider capability、source 配置和
   runtime 状态组合为内存查询结果，不持久化一份 access catalog。
5. Execution 在 composition/application 中生成候选执行路线。候选路线不持久化为
   Reference 实体；真正选中的路线、发往 provider 的地址快照和 provider 回报的实际
   执行结果作为 Execution 业务事实持久化。
6. 策略订阅是 Market-owned command，可以用可选 `source_id` 指定期望的数据路径。
   Market 负责验证 source 与 Market/capability 是否相容，并把它解析成 provider-native
   地址；策略不直接提交 provider product/symbol。
7. Reference 中现有 Access 的业务概念分别收归 Market 和 Execution：Market 拥有数据源
   可用性、选择和订阅路径，Execution 拥有候选路线、路线选择及执行审计。底层
   provider-native capability/address 仍由 Integration 所有。
8. Account 不再使用 `market_data_access_id` 作为通用 provider identity。不同
   Integration capability 必须携带与该事实来源相符的 canonical mapping 证据。
9. 不新增通用 `Access`、`RouteManager`、`CapabilityRegistry`、port 或 gateway trait。
   provider capability 由现有 Integration owner 直接定义，Market/Execution composition
   直接组装当前生产实现。

简化后的信息流是：

```text
Reference canonical identity
          +
Integration code-owned capability
          +
Workspace configuration
          +
Market / Execution runtime state
          |
          +--> 当前可用性查询（内存结果，不落 Reference DB）

Execution route selection
          |
          +--> selected/submitted/reported facts（Execution DB + audit）
```

## 3. 用户使用场景

### 3.1 搜索可用行情

用户希望回答：

- 某个 Instrument 或 Market 能获得哪些 observation；
- Kairos 是否实现了对应 provider adapter；
- 当前 workspace 是否配置了该 source；
- 当前进程是否 ready，数据是否 fresh；
- provider 使用什么 product/symbol 访问目标 Market。

推荐的业务查询不是读取 Reference access 行，而是：

```text
available_market_data(
  instrument_id?,
  market_id?,
  observation_kind?,
  provider_id?,
  configured_only?,
  ready_only?
)
```

结果需要明确区分不同层次的状态：

```text
MarketDataAvailability
  canonical market identity
  provider/source identity
  supported observation capabilities
  supported_by_adapter
  configured_in_workspace
  runtime_ready
  freshness/readiness detail
  provider-native product/symbol
```

这是一份查询时组合的 current view。它可以被 CLI、策略上下文或运维 UI 使用，但不因
存在多个消费者而成为持久化领域实体。

策略可以先查询可用数据源，再在订阅命令中显式指定路径：

```text
available_market_data(market_id, observation_kind)
        -> source_id = "massive-primary"

subscribe_market_data(
  market_id,
  observation_selector,
  source_id = "massive-primary"
)
```

`source_id` 是 Market-owned runtime/source identity，不是 Reference access ID。订阅命令
遵循以下最小规则：

- 指定 `source_id` 时，Market 验证该 source 已配置、支持请求的 observation、覆盖目标
  Market，并满足订阅准入条件；
- 未指定 `source_id` 时，只有唯一合法来源或存在显式 Market-owned default 时才自动选择；
- 多个合法来源且没有显式 default 时返回歧义，不静默选择 provider；
- 策略不能提交 `provider_product`、`provider_symbol`、endpoint 或 credential；这些值由
  Market composition 从 source binding 和 Integration capability 解析；
- source readiness/freshness 变化由 Market 管理，策略的订阅意图不因一次断线被改写成
  另一个 provider 路径。未来如需 fallback，必须单独定义 Market-owned policy 和事件。

### 3.2 查看和选择执行路线

用户希望在下单前回答：

- 当前订单可以通过哪些 broker/provider 执行；
- 路线对应哪个 destination Market、provider product 和 provider symbol；
- 路线是否适用于当前 account、segment、instrument、order type 和 options；
- 当前连接是否 ready；
- 用户应该显式选择路线，还是允许 Execution 使用确定的选择规则。

推荐查询是：

```text
available_execution_routes(
  account_id,
  segment_key,
  instrument_id,
  market_id?,
  order_type,
  options
)
```

查询返回内存中的 `ExecutionRouteCandidate`。候选路线只描述“现在可以怎么走”，不是
已经发生的订单事实。

### 3.3 解释订单最终如何执行

订单提交和成交后，用户希望回答：

- 下单时选中了哪条路线；
- 实际向哪个 broker/provider 发送；
- 发送时使用哪个 provider product/symbol；
- provider 返回了哪个 remote order ID；
- broker 或交易所最终报告在哪个 venue 成交；
- 多次 attempt、reroute 或恢复分别发生了什么。

这些内容无法用一个可变的 access catalog 可靠解释。它们必须作为不可变 Execution
事实保存，并区分：

```text
selected route   用户或 Execution 决定怎么走
submitted route  实际向哪个 provider connection 发送
reported outcome provider 回报实际在哪里成交
```

例如订单可以提交给 IBKR，但最终由 IBKR 报告在 NYSE 成交。broker、提交地址和实际
execution venue 不是同一个字段，也不能由一个 `ExecutionAccess` ID 代替。

### 3.4 诊断与历史查询

Access 列表 CLI 当前具有一定诊断价值，但诊断价值不等于 Reference 持久化价值：

- “当前代码支持什么”读取 adapter capability；
- “当前配置了什么”读取 workspace composition；
- “当前是否可用”读取 runtime health/readiness；
- “某个订单当时怎么执行”读取 Execution order/audit；
- “canonical Market 当时是什么”读取 Reference lifecycle。

这些查询可以分别由其事实 owner 提供，不需要为了形成一个统一列表而制造第二份状态。

## 4. 持久化判据

一个值只有同时满足以下条件时，才应新增或保留独立持久化：

1. 它不是代码能力、当前配置或 runtime 状态的确定性派生；
2. 它拥有独立于现有 owner 的业务生命周期；
3. 跨重启、审计或历史回放必须解释它；
4. 不能从已持久化的输入和版本化代码安全重建；
5. 有当前生产调用者消费这份历史事实，而不只是 current view。

“用户能够查询”“计算成本较高”“可能有多个 provider”均不能单独成为持久化理由。

按此判据：

| 信息 | Owner | 是否持久化 |
|---|---|---|
| canonical Instrument/Market/Listing | Reference | 是 |
| provider adapter 支持的 observation/order capability | Integration 代码 | 否 |
| workspace source/connection 配置 | Workspace 配置 | 按配置边界保存 |
| source/connection 当前 readiness | Market/Execution runtime | 否；必要时只做运行检查点 |
| 当前可用行情列表 | Market application query | 否 |
| 当前候选执行路线 | Execution application query | 否 |
| 用户选择的执行路线 | Execution order/audit | 是 |
| 实际提交的 provider 地址快照 | Execution order attempt/audit | 是 |
| provider 回报的成交 venue | Execution fill/audit | 是 |

如果未来出现用户维护且无法重建的 provider symbol override，则应为该具体问题设计
owner-owned 配置或事实，不恢复一个包揽所有能力的 Reference Access catalog。

## 5. 迁移前实现与目标不一致之处（历史基线）

### 5.1 Access 没有独立来源

Reference 当前通过 `populate_market_data_accesses` 和
`populate_execution_accesses` 遍历每个 `Market` 自动生成记录。普通路径中的
provider product、symbol、status 和 effective interval 都从 `Market` 复制，access ID
由字符串规则生成。

这导致“发现了 Market”被近似为“系统具备行情或 order-entry capability”，但 provider
catalog 中存在一个产品不代表当前代码实现了对应实时行情或下单操作。

### 5.2 Access 被错误赋予独立生命周期

当前实现为两个 Access 提供：

- Reference domain entity；
- catalog map 和 reconciliation；
- current SQLite table；
- upsert/update lifecycle event；
- FlatBuffers schema 和 generated bindings；
- contract projection；
- Python application/client model；
- CLI 独立查询。

但 access 的 status/effective interval 通常复制自 Market，并没有独立变化来源。这造成
重复状态、重复事件和潜在不一致。

### 5.3 Market 查询混合了四种状态

Reference `MarketDataAccess.status` 不能回答：

- adapter 是否实现目标 observation；
- workspace 是否启用 source；
- credential 是否可用；
- connection 是否 ready；
- 当前 observation 是否 fresh。

因此即使保留数据库行，它也不能满足用户“数据是否可用”的真实查询。

### 5.4 Execution 只验证 ID 存在

Execution 启动时把 Reference access 降维为：

```text
ExecutionAccessId -> ProviderInstrumentRef
```

订单提交只验证 ID 是否存在，没有保留并验证 access 与订单 `instrument_id`、
`market_id`、account/segment 的一致性。错误的 access ID 可能把订单映射到错误的
provider symbol。

### 5.5 历史订单只保存可变目录 ID

`ExecutionOrder` 当前主要保存 `execution_access_id`，没有保存完整的 selected/submitted
route snapshot。目录变更或删除后，单独 ID 无法独立解释历史订单，也不能区分 broker 和
reported execution venue。

### 5.6 Account 泛化了 MarketDataAccess identity

Account 当前使用 `market_data_access_id` 把 provider observation 映射为 canonical
Instrument/Market。行情 access 不是所有 account snapshot、private order event 或 fill 的
通用身份。这个依赖把一个 capability-specific 地址错误提升为 provider identity。

## 6. 目标所有权

### 6.1 Reference

Reference 继续拥有：

- Asset、Instrument、Listing、Market；
- canonical identity 和有效关系；
- Reference lifecycle、catalog revision 和 event sequence；
- provider facts 到 canonical identity 的标准化与冲突处理。

Reference 不再拥有：

- adapter 是否支持某种 observation/order operation；
- workspace 是否配置 source/connection；
- runtime readiness；
- Market source 选择；
- Execution route candidate、route policy 或 route selection；
- selected/submitted/reported execution facts。

### 6.2 Integration

Integration/provider implementation 拥有：

- provider-native connection 和 operation；
- provider product/symbol/request discriminator；
- 当前代码实际实现的 capability；
- provider payload normalization；
- provider 层错误、delivery certainty、ordering 和 reconnect 语义。

Capability 应靠现有 provider-owned concrete API 或紧邻 provider 实现的静态描述暴露。
不要新增一个 universal registry 或 application-owned mirror trait。

### 6.3 Market

Market composition 负责把以下输入解析为 Market-owned 内存值：

```text
Reference Market
+ configured MarketSourceBinding
+ Integration provider capability/address
= ResolvedMarket + ResolvedMarketDataSource
```

Market application/Actor 继续拥有：

- subscription intent；
- 策略/调用者在订阅命令中提交的可选 source selection；
- Market 验证和解析后的 selected source；
- source epoch、readiness 和 recovery；
- observation continuity、freshness 和 current view。

“可用数据搜索”是 Market application query。CLI/策略不再从 Reference 查询
`MarketDataAccess`。

### 6.4 Execution

Execution composition 负责根据当前 concrete connections 和配置构造候选路线所需的
provider 地址。Execution application 负责：

- 根据订单业务字段验证候选路线；
- 执行显式路线选择；
- 如果未来确有生产需求，再加入最小的 Execution-owned route policy；
- 在任何 provider command 可能发出之前持久化 selected route；
- 记录 submitted attempt 和 reported outcome。

Integration connection 只执行已解析的 provider request，不选择业务路线。

### 6.5 Workspace/System

Workspace/System 继续拥有 source/connection/account/segment 的配置和进程装配，不把
配置复制进 Reference catalog。

### 6.6 Account

Account 继续从 Account-owned Integration account capability 接收 live facts。canonical
identity mapping 必须由对应 capability 的 typed fact 或 composition mapping 提供，不能
依赖行情 access 作为通用关联键。

### 6.7 Access 概念迁移后的边界

“收归 Market/Execution”指业务含义和业务 API 迁移，不表示两个业务模块接管 provider
协议：

```text
旧 Reference MarketDataAccess
  canonical Market relation      -> Reference Market 输入
  source availability/selection  -> Market
  subscription path/runtime      -> Market
  provider product/symbol        -> Integration capability，由 Market composition 消费

旧 Reference ExecutionAccess
  canonical Instrument/Market    -> Reference identity 输入
  candidate/selection/admission  -> Execution
  selected/submitted/reported    -> Execution persistence/audit
  provider product/symbol        -> Integration capability，由 Execution composition 消费
```

Market 和 Execution 不互相导入私有 route/service 类型，也不建立共享 Access primitive 以
重新制造跨领域目录。

## 7. 目标模型

以下类型用于说明职责，不要求照字面新增全部结构。实施时优先扩展已有 owner-owned 类型。

### 7.1 Market current availability

```rust
pub struct MarketDataAvailability {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub source_id: SourceId,
    pub provider_id: ProviderId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
    pub capabilities: ObservationCapabilities,
    pub configured: bool,
    pub runtime_status: MarketSourceStatus,
}
```

约束：

- 这是 application query result，不进入 Reference domain/contract/schema；
- `capabilities` 必须来自真实 provider 实现，不从 InstrumentKind 猜测；
- `configured` 来自当前 workspace；
- `runtime_status` 来自 Market runtime；
- 未运行 Market process 时可以返回 supported/configured，但必须明确 readiness unknown，
  不能伪装成 ready。

当前 `MarketDataRoute`/`ResolvedMarket` 可以继续作为 Market-owned 内存值，但不应要求
Reference 为其持久化一个 Access 行。

### 7.2 Execution route candidate

```rust
pub struct ExecutionRouteCandidate {
    pub route_id: ExecutionRouteId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub instrument_id: InstrumentId,
    pub market_id: Option<MarketId>,
    pub broker: ParticipantRef,
    pub destination: Option<Exchange>,
    pub provider_instrument: ProviderInstrumentRef,
    pub supported_order_features: OrderEntryCapabilities,
    pub readiness: ExecutionRouteReadiness,
}
```

约束：

- 候选路线由当前配置和 concrete connection 生成；
- route ID 必须稳定且可读，但稳定 ID 不要求存在 route catalog 表；
- 选择前验证 account、segment、instrument、market、order type 和 options；
- 不允许仅凭 route ID 存在就发送订单。

### 7.3 Selected and submitted route facts

```rust
pub struct SelectedExecutionRoute {
    pub route_id: ExecutionRouteId,
    pub selected_at_unix_nanos: UnixNanos,
    pub selection_kind: RouteSelectionKind,
    pub broker: ParticipantRef,
    pub destination: Option<Exchange>,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
}

pub struct ExecutionAttempt {
    pub attempt_id: ExecutionAttemptId,
    pub order_id: OrderId,
    pub selected_route: SelectedExecutionRoute,
    pub provider_connection_id: String,
    pub command_started_at_unix_nanos: UnixNanos,
    pub delivery_certainty: DeliveryCertainty,
    pub remote_order_id: Option<RemoteOrderId>,
}
```

selected route 必须在 command 可能发送前随订单状态原子持久化。若发生 reroute，每次 attempt
保留自己的 route snapshot，不能覆盖前一次尝试。

### 7.4 Reported execution facts

Fill/order event 应按 provider 实际可提供的信息记录：

```text
reported broker/provider
reported execution venue
provider product/symbol
remote order ID / trade ID
occurred time
```

provider 未报告 execution venue 时保持 `None/Unknown`，不能用 selected destination 填充并
伪装成实际成交 venue。

## 8. 目标查询边界

### 8.1 Market

Market application 提供业务查询：

```text
available_market_data(query) -> Vec<MarketDataAvailability>
```

Market application 同时提供业务命令：

```text
subscribe_market_data {
  market_id,
  observation_selector,
  source_id?,
}
```

查询用于发现路径，命令用于表达订阅意图。`source_id` 可以直接来自查询结果；命令进入
application 后由 Market 校验，不允许 composition/provider 地址直接穿过 application API。

CLI 从：

```text
kairospy reference market-data-accesses
```

迁移为：

```text
kairospy market data-sources
```

查询结果明确标明 supported、configured、runtime status 与 freshness 的差异。

### 8.2 Execution

Execution application 提供：

```text
available_execution_routes(query) -> Vec<ExecutionRouteCandidate>
```

提交命令允许显式 `route_id`。现阶段不因为模型迁移自动加入 smart routing；调用者未选择
且没有唯一合法路线时返回明确歧义错误。

CLI 从 Reference access discovery 迁移为 Execution-owned route inspection。

### 8.3 Reference

Reference application、contract、Python API 和 CLI 删除：

```text
execution_accesses
market_data_accesses
execution_access
market_data_access
ReferenceKind::ExecutionAccess
ReferenceKind::MarketDataAccess
```

Reference consumer projection 只携带 consumer 真正需要的 canonical identity 与 watermark。

## 9. 数据库与 wire contract 变化

### 9.1 Reference 删除项

迁移完成后删除：

- `reference_execution_accesses_current`；
- `reference_market_data_accesses_current`；
- 对应索引；
- provider candidate payload 中的两个 access record kind；
- reconcile key、current row replacement 和 count；
- Access upsert/update lifecycle event；
- Reference FlatBuffers Access models/events；
- contract encode/decode/query/projection；
- Python generated binding 以外的 application/client/event surface。

生成代码随 schema registry/build 流程更新，不手工编辑 generated 文件。

### 9.2 Reference schema migration

这是删除持久化模型的 schema 变更，Reference schema version 已提升为 2。项目采用显式、
可重复执行的原地迁移：

- 删除两张 derived current table，并保留所有 canonical rows 与 lifecycle history；
- Access current rows 可由 provider catalog 和当前代码能力重建，因此不做历史备份；
- 不保留无限期双写或兼容 facade；
- 删除 material data 前明确备份/重建行为和失败恢复方式。

### 9.3 Execution persistence migration

在删除 Reference access 读取之前，Execution 必须先能够持久化 selected route snapshot。
需要同步更新：

- domain order/attempt/fill model；
- state snapshot 和 restore；
- SQL/state store；
- audit store；
- business event contract；
- Python transport/application model；
- recovery/reconciliation。

历史只有 `execution_access_id` 的旧订单不能伪造 route snapshot。迁移后应保留旧 ID 并将
缺失字段标为 unknown，或在 schema migration 时从仍存在的旧 Reference 表做一次性快照
回填。采用哪种方式必须由迁移前的数据保留要求决定。

## 10. 分阶段迁移计划

每个阶段完成后删除同一业务 slice 的旧路径，不建立长期双模型。

### Phase 0：行为基线与决策冻结

1. 记录当前所有 Access producer、consumer、schema、event、CLI 和 Python API。
2. 为错误 execution route ID 与订单 instrument/market 不匹配补充回归测试。
3. 明确当前生产是否存在：
   - 用户自定义 provider symbol override；
   - 不可重建的 access lifecycle；
   - route reroute/multiple attempt；
   - broker reported execution venue。
4. 决定旧 Execution 历史记录的 route snapshot 回填策略。

退出条件：不存在尚未分类的 Access 消费者；迁移数据策略已确定。

### Phase 1：能力 owner 显式化

1. 在每个当前生产 provider 实现附近定义其真实 observation/order capability。
2. 从现有 `MarketSourceBinding` 和 execution connection options 解析 configured capability。
3. 不新增 universal capability trait/registry；只有当前 concrete callers。
4. 对每个 provider 添加 capability/address focused tests。

退出条件：Market/Execution 可以不读取 Reference Access 表而构造候选内存值。

### Phase 2：Market 查询迁移

1. 将 `MarketDataRoute` 的构造输入从 Reference `MarketDataAccess` 改为 canonical Market、
   source binding 和 provider capability/address。
2. 增加 Market-owned `available_market_data` current query。
3. 在 Market subscription command 中加入可选的 Market-owned `source_id`，补齐 exact
   source、唯一默认和歧义拒绝行为测试。
4. 将 Market collection 从 `market_data_access_id` 迁移为明确的 `source_id`；删除两个
   并行选择字段的歧义，不提供 provider product/symbol 作为替代输入。
5. 将 CLI/策略调用迁移到 Market query + subscription command。
6. Account 停止使用 `market_data_access_id` 做通用 identity resolution。

退出条件：Market 和 Account 不再消费 `ReferenceProjectionSnapshot.market_data_accesses`。

### Phase 3：Execution route 与审计迁移

1. 由 execution connections/config 构造 `ExecutionRouteCandidate`。
2. 提交前验证 route 与 account、segment、instrument、market、order options 一致。
3. 在 provider command 发送前原子持久化 selected route snapshot。
4. 为 attempt、delivery certainty、remote order ID 和 reported venue 建立事实模型。
5. 将 CLI/策略 route discovery 迁移到 Execution query。
6. 删除 `load_reference_execution_accesses` 和 Application 中的 Reference address map。

退出条件：Execution 启动、选路、下单、恢复和审计均不读取 Reference ExecutionAccess。

### Phase 4：Reference Access 删除

1. 删除 domain entities、ProviderCatalog 字段、validation 和 reconcile。
2. 删除 provider `populate_*_accesses` 及特殊构造。
3. 删除 SQL current tables、candidate records、indexes、counts 和 migration remnants。
4. 删除 application query kinds、publication 和 lifecycle events。
5. 删除 Reference contract models、codecs、projection fields 和 SQLite query。
6. 更新 schema registry 并重新生成 bindings。
7. 删除 Python Reference models/client/CLI/strategy exports。

退出条件：静态搜索找不到 Reference-owned Access 模型、表、事件或 API。

### Phase 5：命名与文档清理

1. 评估 `ExecutionAccessId` 是否仍有当前 caller；如果只表示选择后的 route，迁移为
   `ExecutionRouteId` 并删除旧 primitive。
2. 更新 README、示例、CLI help 和三个模块架构文档。
3. 删除兼容别名、旧 fixtures 和无当前 caller 的 helper。

退出条件：文档、类型名称和实际 owner 一致，不再把系统能力描述为 Reference lifecycle。

## 11. 测试与验收

### 11.1 Market 行为测试

- supported but not configured；
- configured but runtime unavailable；
- ready but stale；
- 同一 Market 的多个 source；
- 策略指定合法 `source_id` 时订阅绑定该 source；
- 策略指定不覆盖目标 Market/observation 的 source 时拒绝；
- 未指定 source 且存在唯一合法来源时成功；
- 未指定 source 且存在多个来源、无显式默认时返回歧义；
- provider 不支持请求的 observation kind；
- provider symbol/address 映射失败；
- 未运行 runtime 时 readiness 明确为 unknown，而不是 false ready。

### 11.2 Execution 行为测试

- route instrument 不匹配时拒绝，且 provider command 未发送；
- route market 不匹配时拒绝；
- account/segment 不适用时拒绝；
- order option/capability 不兼容时拒绝；
- 唯一路线时显式选择流程正确；
- 多路线且未指定时返回歧义；
- selected route 在 command 发送前持久化；
- command 结果不确定时保留 attempt 和 delivery certainty；
- reroute 不覆盖旧 attempt；
- selected destination 与 reported venue 不同时分别保存；
- Reference DB 不存在时 Execution 仍可由自身 composition 启动。

### 11.3 Reference 行为测试

- canonical catalog 构建不生成 Access；
- Market/Instrument/Listing lifecycle 不再产生 Access 影子事件；
- consumer projections 不含 Access；
- schema version 和 rebuild/migration 行为明确；
- Reference CLI/Python surface 不再暴露 Access 查询。

### 11.4 架构与静态检查

除仓库标准检查外，迁移完成后执行：

```text
rg -n "MarketDataAccess|ExecutionAccess" crates kairospy schemas tests
rg -n "market_data_accesses|execution_accesses" crates kairospy schemas tests
rg -n "reference_market_data_accesses_current|reference_execution_accesses_current" .
rg -n "MarketDataAccessUpserted|ExecutionAccessUpserted" crates kairospy schemas
rg -n "market_data_access_id" crates/modules/account crates/modules/market
rg -n "load_reference_execution_accesses" crates/modules/execution
```

每个残留必须属于明确的历史迁移代码或已批准的新 owner；否则删除。

仓库验证：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

## 12. 非目标与禁止项

本次迁移不做以下事情：

- 不为了替代两个 Access 新增统一 `Access`/`Capability` 顶级业务实体；
- 不新增通用 route manager、registry 或 application-owned provider trait；
- 不把 runtime health/readiness 写入 Reference；
- 不从 canonical Market 或 symbol 猜测 provider capability；
- 不把 selected destination 当作 reported execution venue；
- 不在迁移中顺便实现未经需求验证的 smart order routing；
- 不以兼容为由长期保留 Reference Access 双写、双读或 facade；
- 不允许 Execution 只验证 route ID 存在而不验证订单上下文。

## 13. 已采用的最小产品决策

本轮实施采用以下最小决策；未来变化必须在 owner 内增加明确 policy/fact，不能恢复
Reference Access catalog：

1. 普通订单必须显式提供 `execution_route_id`，不做隐式智能路由；
2. CLI 直接暴露稳定的 `route_id`，同时返回 broker/source/destination 解释字段；
3. Market 查询同时返回 supported、configured、runtime status 和 freshness；
4. 旧订单缺失的 route snapshot 不伪造，保持 unknown；
5. provider 未报告 execution venue 时省略该字段；
6. Market capability current query 由 Market process 提供；进程未运行时不制造 runtime
   readiness。

没有当前生产需求的选项采用最小实现：显式 route、无智能路由、未知事实保持 unknown、
current capability 查询不制造历史状态。

### 13.1 已落地接口与迁移记录（2026-08-17）

- Market：`available_market_data` 与 `GET /v1/data-sources` 返回查询时组合结果；订阅
  request 和 Python CLI 均支持可选 `source_id`，Market 验证 source 覆盖范围与 observation
  capability，并拒绝无默认的多来源歧义。
- Execution：`available_execution_routes` 与 `GET /v1/routes` 返回当前 route candidates；
  CLI 提供 `routes` 查询。提交前验证 account、segment、instrument、market、order type、
  options 与 readiness。
- Execution order 持久化 `SelectedExecutionRoute` 和 append-only `ExecutionAttempt`；发送前
  checkpoint 将 delivery certainty 置为 `indeterminate`，provider 回报更新 remote order ID
  和 certainty。Fill 单独保存 reporting provider/product/symbol、remote order ID 和 provider
  明确回报的 execution market。
- Reference：domain、catalog、provider population、SQLite current projection、events、wire
  schemas、Rust/Python contract 与 CLI Access surface 已删除。Schema version 2 会显式删除
  两张旧 derived current tables；canonical catalog 数据保留且可由 provider refresh 重建。
- Account：provider observation 通过 canonical market/source provenance 映射，不再借用
  `market_data_access_id`。
- 命名：仅表示执行路径的 primitive/API 已由 `ExecutionAccessId` 收敛为
  `ExecutionRouteId`，未保留兼容别名。

### 13.2 验收证据（2026-08-17）

- `cargo test -p kairos-market`：通过；
- `cargo test -p kairos-execution --lib`：107 项通过；Execution architecture：21 项通过；
- `cargo test -p kairos-reference -p kairos-reference-contract`：通过（两个明确标记的规模
  测试 ignored）；
- `uv run pytest -q`：377 项通过，8 项 skipped；
- `cargo fmt --all -- --check`、`git diff --check`、crate layout check：通过；
- `cargo test --workspace` 在无关的
  `account_server_restart_restores_state_and_republishes_a_new_mmap_incarnation` 失败：Account
  server 在 10 秒内未初始化 mmap。该测试单独复跑仍失败；本次改造涉及的 Account
  identity focused tests 与 Account 其余已运行测试均通过。

## 14. 完成定义

本改造只有同时满足以下条件才算完成：

- 用户仍能查询当前有哪些行情能力、是否配置、是否 ready；
- 策略能够在 Market subscription command 中显式选择查询结果中的 `source_id`；
- 用户仍能查看当前合法执行路线，并能显式选择；
- 每个订单可以脱离可变目录独立解释 selected/submitted/reported execution facts；
- Reference 不再持久化、发布或查询两个 Access；
- Market/Execution 不通过 Reference Access 获得 runtime provider 地址；
- Account 不再把行情 access 当作通用 provider identity；
- 旧表、事件、contract、Python API、CLI 和 compatibility path 已删除；
- focused behavior tests、architecture tests 和仓库检查通过，或精确记录无关的既有失败。
