# Conflux 闭合 Contract 运行时设计

## 0. 文档状态

- 状态：提案。
- 范围：`crates/platform/conflux`、五个业务 module 的主进程、各 module
  Contract crate，以及 composition 注入的 provider-native Integration
  connection。
- 目标：把 Account、Market、Execution、Reference、Risk 当前重复实现的
  “输入、单写者变更、持久化、事件、当前视图、恢复、健康和关闭”闭环收归一个
  统一运行时，同时保持业务状态、Contract 词汇和 provider 语义的既有所有权。
- 与既有文档的关系：本提案是
  `docs/proposal/conflux-actor-runtime-framework.md` 的目标模型修订候选。在本提案被接受
  前，既有文档仍描述当前已接受方向；接受后，应同步修订其中关于 HTTP-shaped
  handler、`ContractCatalog`、`IntegrationCatalog`、`TypeId` 私有存储和 erased
  dispatch 的章节，不能长期保留两套目标架构。
- 本提案不授权一次性迁移所有 module。迁移必须逐 module 完成，新路径通过退出条件后
  删除该 module 的旧 process loop、listener/task registry 和 publication plumbing。

## 1. 决策摘要

本提案作出以下目标决策：

1. Conflux 的抽象单位不是通用 HTTP route、任意消息、资源类型目录或 provider
   operation，而是一个编译期闭合的 module 运行环境。
2. module Contract crate 仍拥有该 module 的完整跨进程协议；当前 Conflux 只抽象 REST 的
   `Request -> Response` 类型对。View、Aeron 和 Reference 的 SQLite projection 等能力继续
   由各 module 的 concrete client 暴露，等真实实现形成稳定共同形态后再考虑自举。
3. Conflux 不再要求 Actor 使用 `#[handle(post = "...")]` 重复声明 HTTP method/path。
   Actor 实现 Contract 已声明的 operation；HTTP-over-UDS、HTTP-over-TCP 或其他 framing
   是 Contract-owned transport projection。
4. Actor ingress 由其服务的 Contract、消费的 Contract event、Integration fact、内部
   completion 和 timer 在编译期组成闭合 enum。正常 Actor dispatch 不使用
   `Box<dyn Any>`、`TypeId`、downcast 或运行时 route lookup。
5. 每个进程向外提供且只提供一个 owning module Contract。该 concrete Contract 直接进入
   Conflux；对外暴露的是 Contract，不是 Conflux
   route、HTTP handler 或第二套 application facade。
6. system composition 编译期集成所有可用 module Contract client 类型和所有
   provider-native Integration connection 类型，形成全局完整的 `Clients` 与
   `Connections` named list。Actor 不声明自己的资源子集，而是在系统全集中按需创建和使用
   命名实例。Conflux 不建立跨类型 `ContractCatalog` 或 `IntegrationCatalog`。
7. 同一 concrete type 的动态多个实例使用同构、类型化的命名集合，例如
   `ManagedClients<AccountId, AccountClient>` 或
   `ManagedConnections<ExecutionRouteId, BinanceSpot>`，不升级为跨类型 registry。
8. Actor 直接调用 concrete Contract client 和 Integration connection。Conflux 不定义
   universal Contract client trait、universal Integration trait、provider session facade 或
   `execute(operation, payload)`。
9. managed client 或 connection 创建的长生命周期 stream 可以交给 Conflux 的通用 typed source
   supervision；source 只能通过同一个 bounded Actor ingress 回送拥有的消息，不能并发
   修改 Application。
10. 每次 Actor handler 调用形成一个 turn。turn 在 handler 成功后提交 staged Contract
   event、Contract view replacement、resource/readiness change 和通用 background work；
   普通业务拒绝不能自动终止进程。
11. Conflux 负责 bounded admission、串行 Actor authority、source/task supervision、
    readiness、fault propagation、一个绝对 shutdown deadline、drain、metrics 和确定性
    harness；它不拥有业务路由选择、provider retry 语义、业务持久化模型或业务状态。

## 2. 当前共同数据流

五个业务 module 当前虽然文件布局和 transport 细节不同，但主数据流一致：

```text
恢复 authoritative state
        |
        v
接收 Contract command / dependency Contract event / Integration fact /
timer / asynchronous completion
        |
        v
校验 identity、revision、epoch、sequence、dedup、freshness
        |
        v
唯一 Application / Actor 执行业务状态变更
        |
        v
generation / event_sequence 前进，持久化业务事实
        |
        +--> ordered business events / outbox --> publish --> acknowledge
        |
        +--> latest current view/projection ------> replace published generation
        |
        +--> concrete Contract / Integration work -> completion 回到 Actor
        |
        +--> health/readiness/resource state
```

具体重复包括：

- Account：private account event、snapshot refresh、sequence gap、buffered replay、pending
  business event 和 dirty snapshot；
- Market：control/source input、continuity、change drain、history/event publication、current
  view 和 freshness；
- Execution：control/exchange fact、route selection、order attempt、delivery certainty、audit、
  outbox、reconciliation 和 current view；
- Reference：provider refresh、candidate reconciliation、transactional catalog/event/
  publication、read projection 和 lifecycle watermark；
- Risk：control/dependency fact、journal/checkpoint、pending event、current view 和 expiry tick。

这不是五种 runtime。它们是同一数据流拓扑在不同业务 owner 下的五种实例。差异应保留在
Contract 类型、Integration capability、业务 commit、continuity 后果和 delivery
certainty 中，不应通过五套手写 process loop 表达。

## 3. 目标闭环

一个 module 的外部数据边界只有 Contract 和 Integration：

```text
Consumed Contract event/view
          |
Own Contract command
          |
Integration fact/snapshot
          v
    Module Actor/Application
          |
          +--> Own Contract ordered event
          +--> Own Contract current view/projection
          +--> Consumed Contract command/query
          +--> Integration concrete operation
```

完整跨系统闭环是：

```text
Contract command
    -> Module Actor/Application
    -> provider-native Integration command/query
    -> provider
    -> normalized Integration fact/snapshot/outcome
    -> Module Actor/Application
    -> Contract event/view
    -> downstream Contract client
```

内部异步输入只有两类：

```text
typed background work -> typed completion
timer                 -> typed tick
```

任何 transport callback、provider callback、publisher worker 或 persistence worker 都不能
绕开该闭环直接修改 Actor/Application。

## 4. Contract 是完整可执行协议

### 4.1 Contract 的所有权

每个 module Contract crate 继续独立 depend-able，并拥有：

- control operation 的 request、response、typed error 和 metadata；
- command/query 的 idempotency、admission 和 delivery 声明；
- control client 和 server host；
- Contract event enum、frame、metadata、encoder、decoder、publisher 和 stream；
- current view/projection marker、key、value、frame、metadata、publisher 和 reader；
- Contract watermark、generation、applied event sequence 和 incarnation；
- 当前 transport implementation，例如 HTTP-over-UDS、Aeron、mmap 或 SQLite projection。

Contract 不拥有 module Actor、Application、provider connection、persistence record 或
vendor payload。

Contract crate 可以具体提供 REST/control、current view、ordered event stream 或 SQLite
projection，但这些能力目前并没有一个真实统一的 Rust shape：Account、Market、Execution
和 Risk client 提供 control、events 与 keyed view；Reference client 提供 events 与
consumer-scoped SQLite projection query。Conflux 因此只记录已经稳定的 REST request/response
类型关系，不为 View、Aeron、Endpoint 或统一 Client 发明通用 trait。

```rust,ignore
pub trait RestContract: Send + 'static {
    type Request: Send + 'static;
    type Response: Send + 'static;
}

pub trait Contract: Send + 'static {
    type Rest: RestContract;
}
```

`RestContract` 只表达闭合的 request/response pair。method/path、serialization、transport、
typed error 如何进入 response，以及具体 client/host 的构造仍由 owning Contract crate 决定。
Conflux 不定义 `ServedContract`，也不要求依赖 client 实现 `Contract`。

当前 concrete client 能力清单如下；这是暂不抽象 View/Aeron 的直接依据：

| concrete client | 当前公开能力 |
| --- | --- |
| `AccountClient` | `control()`、`events(capacity)`、`view(AccountViewKey)` |
| `MarketClient` | `control()`、`events(capacity)`、`view(MarketViewKey)` |
| `ExecutionClient` | `control()`、`events(capacity)`、`view(ExecutionViewKey)` |
| `RiskClient` | `control()`、`events(capacity)`、`view(RiskViewKey)`；连接本身可失败 |
| `ReferenceClient` | `events(capacity)`、`watermark()`、三类 consumer-scoped SQLite snapshot query |

虽然多个 client 都出现 `events`，但 stream 的恢复、订阅和长期监督语义尚未形成稳定 owner-defined
capability；Reference 也不是 keyed mmap view。此时提取 `ViewContract` 或 `AeronContract` 只会
隐藏差异。system composition 直接保存这些 concrete client，Actor 调用它们当前已有的方法。

每个 module 主进程只向外提供一个 owning module Contract。第一版由 `Conflux<A>` 直接持有
`A::Contract`，不增加 managed wrapper：

```rust,ignore
pub struct Conflux<A: ConfluxActor> {
    contract: A::Contract,
    system: ConfluxSystem,
}
```

`A::Contract` 是本进程具体的对外 Contract 服务对象。它把 REST request 适配为 Actor 的
own-contract ingress，并把 response 编码回 Contract transport。Conflux 不在其外层再定义
HTTP route、通用 service API 或公开 handler 目录。同一个 Contract 可以投影到
HTTP-over-UDS、HTTP-over-TCP 或其他 framing，但这些
transport host 共同组成一个逻辑 Contract，不构成多个业务服务。只有出现真实的 Contract
热替换需求时，才重新评估 revision/epoch 管理。

### 4.2 REST command/query operation

第一版只要求 owning Contract 给出一个闭合 request enum 与对应 response enum：

```rust,ignore
enum ExecutionRestRequest {
    SubmitIntent(SubmitIntentRequest),
    CancelOrder(CancelOrderRequest),
    Health,
}

enum ExecutionRestResponse {
    SubmitIntent(Result<SubmitIntentResponse, ExecutionControlError>),
    CancelOrder(Result<CancelOrderResponse, ExecutionControlError>),
    Health(Result<Health, ExecutionControlError>),
}
```

HTTP method/path、UDS framing、JSON codec、client 与 host 都继续由 Contract crate 具体实现。
Conflux 只通过 `RestContract::{Request, Response}` 固定这一对类型，不定义 operation trait、
route macro 或通用 error。Contract host 将 request 封装进 Actor 的闭合 ingress，并在 turn
提交后发送 response。

业务读取默认通过 typed current view/projection。只有要求 Actor 强一致性的 query 才进入
Actor；runtime health 可以由 Contract host 直接投影 Conflux status 和 module business
readiness。

### 4.3 Ordered event

Contract event 是已提交业务变化的跨进程事实，必须拥有明确顺序、frame、watermark 和
delivery 语义。Conflux 不再用通用 `Event<E>` 替代 Contract-owned event envelope。

ordered event publication 遵循：

- 同一 Contract source 内顺序稳定；
- publish 成功后才 acknowledge；
- publish 失败保留 pending；
- clean Drain 必须处理或明确失败所有已接受 event；
- event model 直接映射到 Contract-owned wire type，禁止 JSON round trip adapter。

### 4.4 Current view/projection

mmap、SQLite projection 或未来其他 current-state transport 都是 Contract view 的具体实现。
统一语义是 latest typed projection，而不是统一成 mmap API。

每个 view marker 关联一个准确 value type：

```rust,ignore
pub trait ContractView {
    type Value: Send + Sync + 'static;
    const NAME: &'static str;
}
```

view publication 遵循：

- value 带 generation 和 applied event sequence；
- 新 generation 可以合并尚未发布的旧 generation；
- publish 失败不能清除 dirty；
- reader 和 publisher 使用同一 Contract frame；
- Actor/Application 不暴露 mmap handle、SQLite row 或其他 transport object。

### 4.5 Event 与 View 的恢复闭环

当 Contract 同时提供 current view 和 ordered event 时，其恢复协议是：

```text
读取 current view at applied_event_sequence N
    -> 从 N + 1 消费 event
    -> 连续应用
    -> gap / epoch replacement / overflow
    -> resource Degraded
    -> 重新读取 current view
    -> 从新的 watermark 继续
```

是否支持精确 replay、是否只能 resnapshot、buffer 上限和 gap 后的业务准入后果由 owning
Contract 与 consuming module 明确声明，Conflux 不凭空假设。

## 5. 依赖 Client 与 Integration Connection 是完整声明的 managed resource

### 5.1 不新增通用 Integration 协议

Integration 已拥有 provider-native connection 及稳定的真实 capability，例如 order entry、
order query、order event、account snapshot/event、market snapshot/event 和 reference
catalog。Conflux 和业务 module 直接使用这些 owner-defined capability，不镜像第二套
port/gateway/protocol trait。

禁止引入：

```text
UniversalIntegration
ProviderAdapter
SessionRegistry
execute(operation, payload)
start/stop/reconnect 伪装所有 HTTP command/query
```

commands 不在可能已经发送后透明重试；queries 的安全 bounded retry 属于 Integration；
streams 的 reconnect、ordering 和 provider protocol 属于 Integration，gap 后的业务后果
属于 consuming module。

### 5.2 三类资源必须分离

每个 Actor 的运行环境明确分成三类，不合并为一个任意资源 namespace：

1. `A::Contract`：本进程唯一向外提供的 owning module Contract；
2. `ConfluxSystem` 上的 concrete client 字段：Kairos 当前集成的其他进程 Contract clients；
3. `ConfluxSystem` 上的 concrete connection 字段：Kairos 当前集成的 Integration connections。

workspace/system composition 构造资源全集；各 Actor 不再重复声明自己的 client 和
connection struct。例如：

```rust,ignore
pub struct ConfluxSystem {
    pub account_clients: ManagedClients<String, AccountClient>,
    pub execution_clients: ManagedClients<String, ExecutionClient>,
    pub market_clients: ManagedClients<String, MarketClient>,
    pub reference_clients: ManagedClients<String, ReferenceClient>,
    pub risk_clients: ManagedClients<String, RiskClient>,

    pub binance_spot_connections: ManagedConnections<String, BinanceSpotConnection>,
    pub okx_connections: ManagedConnections<String, OkxConnection>,
    pub ibkr_connections: ManagedConnections<String, IbkrConnection>,
    // 其余当前 provider-native connection 同样是明确字段。
}
```

`ConfluxSystem` 是唯一具体实体，不是 trait，也不带泛型参数。每个字段的类型在编译期已知，
但字段内部同类型实例的数量、名称、revision 和 epoch 可以在运行时变化。
新增一种 Contract 或 Integration connection 类型必须修改 system composition 并重新编译；
Conflux 不支持在运行时向一个开放目录塞入未知类型。Actor 从全集中按需调用
`ensure_with` 创建实例，未使用的类型保持空 collection。

Actor 通过 Context 直接借用：

```rust,ignore
let outcome = context
    .system()
    .binance_spot_connections
    .get(&execution_route_id)?
    .connection()
    .order_entry()
    .submit_order(&request)
    .await;

let decision = context
    .system()
    .risk_clients
    .get(&risk_process_id)?
    .client()
    .control()
    .authorize_and_reserve(&request)
    .await?;
```

Conflux 不解释这些方法，也不替 provider 选择 route、retry 或 subscription。

### 5.3 同类型的多个命名实例

Contract client 和 Integration connection 都允许同一类型存在多个命名实例。集合是普通、
同构、静态类型化的 collection，不是跨类型 registry：

```rust,ignore
pub struct ManagedClients<K, C> {
    entries: HashMap<K, ManagedClient<C>>,
}

pub struct ManagedClient<C> {
    pub client: C,
    pub revision: u64,
    pub epoch: u64,
    pub state: ResourceState,
}

pub struct ManagedConnections<K, C> {
    entries: HashMap<K, ManagedConnection<C>>,
}

pub struct ManagedConnection<C> {
    pub connection: C,
    pub revision: u64,
    pub epoch: u64,
    pub state: ResourceState,
}
```

例如：

```text
ManagedClients<AccountProcessId, AccountClient>
ManagedClients<ReferenceProcessId, ReferenceClient>
ManagedConnections<ExecutionRouteId, BinanceSpot>
ManagedConnections<MarketSourceId, OkxMarketData>
```

`K` 由 consuming module 或 composition 拥有，可以是 process identity、account、principal、
route、source 或经过验证的 resource name。Contract、Integration 和 Conflux 都不能强迫
所有调用者使用同一个无业务含义的字符串 key。固定且很少的角色也可以使用字段明确的
named struct；需要确定排序时可以选择 `BTreeMap` 或保持顺序的 map。

跨 Contract 或跨 provider 类型仍使用 composition-owned 显式 struct 或 enum。Conflux 的
resource、message、dispatch 和 output 路径不使用 `HashMap<TypeId, Box<dyn Any>>`、downcast、
erased dispatch 或其他类型擦除。

### 5.4 长生命周期 stream

`ManagedClient<C>` 直接持有 concrete Contract client，`ManagedConnection<C>` 直接持有 concrete
Integration connection 可以创建 typed stream。若 stream 必须
长期运行，Actor 将已经创建的 stream 交给通用 source supervision：

```rust,ignore
let stream = context
    .system()
    .binance_spot_connections
    .get(&execution_route_id)?
    .connection()
    .order_events()
    .await?;

context
    .sources()
    .attach(stream)
    .to(ExecutionActor::routes().binance_order_event())
    .required()
    .start()?;
```

同一机制适用于 Contract event stream。Conflux 只拥有 bounded polling、task lifecycle、
failure reporting、cancellation、revision/epoch stamp 和 typed route delivery；它不知道
subscribe/unsubscribe 的 provider 语义。

短、受控且需要严格串行的 concrete operation 可以直接在 handler 中 await。只有真实
latency/profile 或并发需求证明有必要时，才把具体 future 交给通用 task/completion
能力；不得先构造通用 Integration operation layer。

## 6. 编译期闭合 Actor

### 6.1 Actor 关联资源

Actor 与它服务的 Contract 是一个不可拆开的运行时定义。Actor 只关联唯一
`Contract`、闭合 `Ingress` 和闭合 `Output`；它不再声明 `Clients` 或 `Connections`。
资源全集由具体 `ConfluxSystem` 提供：

```rust,ignore
pub trait ConfluxActor: Send + Sized + 'static {
    type FatalError: Error + Send + Sync + 'static;
    type Contract: Contract;
    type Ingress: Send + 'static;
    type Output: Send + 'static;
}
```

Context 对当前 Actor 的资源是静态类型化的：

```rust,ignore
pub struct Context<'runtime, A>
where
    A: ConfluxActor,
{
    contract: &'runtime mut A::Contract,
    system: &'runtime mut ConfluxSystem,
    runtime: RuntimeAuthority<A>,
}
```

`A::Contract` 在类型层面保证本进程只有一个对外业务 Contract，并让 Actor handler 与该
Contract 的 REST request/response pair 一起演进。View、Aeron 或其他 concrete capability
由 owning Contract 自己演进。`ConfluxSystem` 的明确字段是系统集成的完整资源全集；Actor
可以看到全集，但只在业务需要时创建和使用具体命名实例。
系统不会为每个 Actor 复制一份类型声明，也不会根据 Actor handler 构造运行时类型目录。

### 6.2 闭合 ingress

宏根据 Actor 服务和消费的协议生成普通 Rust enum：

```rust,ignore
enum ExecutionIngress {
    Contract(execution_contract::ExecutionCall),
    AccountEvent(account_contract::AccountEvent),
    MarketEvent(market_contract::MarketEvent),
    RiskEvent(risk_contract::RiskEvent),
    BinanceEvent(ExternalExecutionEvent),
    OkxEvent(ExternalExecutionEvent),
    Completion(ExecutionCompletion),
    Tick(ExecutionTick),
}
```

宏生成普通 `match` dispatch。每种 variant 精确携带 request、fact 或 reply port。添加、删除
或修改 Contract operation/event 会触发编译错误，迫使 host、client、Actor binding 和测试
同步演进。

正常 ingress queue 不使用 erased dispatch。wire bytes 在 Contract/Integration 边界 decode
为 typed value 后才进入 Conflux。

Actor output 同样是宏生成或 module 显式定义的闭合 enum：

```rust,ignore
enum ExecutionOutput {
    ContractEvent(execution_contract::ExecutionEvent),
    ContractView(execution_contract::ExecutionViewUpdate),
    Work(ExecutionWork),
    Readiness(ExecutionReadinessChange),
}
```

当前 turn 保存 `A::Output`，而不是保存任意 publisher/view callback。Conflux 通过生成的
静态 match 把每个 output 交给准确的 Contract publisher、view publisher 或 typed work
runner。由此，闭合输入不能通过 turn 输出路径重新退化为类型擦除。

### 6.3 错误分类

handler 的业务错误是 typed Contract/Application response，不自动失败 runtime。只有以下
情况进入 `FatalError`：

- Actor/Application authoritative state 可能已经不一致；
- 必需的 durable commit 处于不确定状态；
- runtime invariant 被破坏；
- required resource 根据 module policy 无法恢复且进程不允许 Degraded 运行。

Conflux 不把业务错误压成 `String` 再作为唯一 reply。panic、task cancellation、source
exit 和 fatal error 必须分别可观测。

## 7. Turn 与输出提交

### 7.1 Turn 的目的

Conflux 的价值不只是抽象一个 `select!` 循环，而是统一并强制每次单写者调用的输出协议。
handler 中声明的跨边界动作先进入当前 turn：

```text
Contract ordered event
Contract latest view replacement
resource/readiness change
generic typed background work
shutdown request
```

handler 业务拒绝时丢弃尚未提交的 runtime action。handler 成功后，Conflux 按确定顺序
提交。Application 内部业务 mutation 和持久化原子性仍由 owning module 保证；Conflux 不
假装能够回滚任意已经执行的业务代码。

### 7.2 输出类别

Conflux 只统一三种已经被所有 module 证明存在的运行语义：

1. **Ordered output**：业务 event/outbox/history；按序、成功后 ack、失败保留、Drain
   必须处理。
2. **Latest output**：current view/projection；保留最新 generation、失败保持 dirty、成功
   更新 published watermark。
3. **Typed work/completion**：后台具体 future/stream；bounded、受监督、completion 回到
   Actor、panic 和取消可见。

Conflux 不定义 AccountEvent、ExecutionSnapshot、provider order 或 persistence record。
Application/domain model 到 Contract model 的直接类型化映射由 module composition 或
publication adapter 拥有。

### 7.3 提交顺序

通用顺序是：

```text
validate input
    -> Application/Actor 执行业务 use case
    -> module 完成所需 durable commit / journal / transactional outbox
    -> handler 成功
    -> commit staged runtime actions
    -> ordered output 尝试 publish/ack
    -> latest output 标记并尝试 publish
    -> 启动已接受 typed work
    -> 完成 typed reply
```

Execution 等 may-have-been-sent command 必须先记录业务 intent/attempt/audit，再调用
Integration，并保存 typed delivery certainty。Conflux 不能通过取消 future 假装命令未发送。

## 8. Resource 状态、readiness 与健康

Conflux 可以保存不含 concrete value 的 runtime resource metadata：

```rust,ignore
pub struct ResourceNode {
    pub id: ResourceId,
    pub revision: u64,
    pub epoch: u64,
    pub required: bool,
    pub state: ResourceState,
}
```

concrete outward Contract service 直接位于 `Conflux<A>`；依赖 client 和 Integration
connection 分别位于 `ConfluxSystem` 的明确 managed collection 字段中。
Conflux 可以统一关联下列 metadata，但不把 concrete value 搬入类型擦除的 metadata
registry。metadata 用于：

- required/optional readiness；
- source/task lifecycle；
- replacement fencing；
- failure feedback；
- health 和 metrics；
- shutdown progress。

技术 readiness 与业务 readiness 分离：

```text
runtime phase
+ required resource states
+ bounded queue/task health
+ module-owned business readiness/freshness
= admission decision
```

source task 正常结束、panic、overflow 或 cancellation 不能留下仍显示 Ready 的资源记录。
optional resource 失败可以进入 Degraded；required resource 的后果由 module policy 明确。

## 9. 生命周期与关闭

Conflux 拥有一个绝对 shutdown deadline，phase 至少包括：

```text
Starting
Running / Degraded
Quiescing
StoppingSources
DrainingInputs
FlushingOutputs
ClosingResources
Stopped / Forced / Failed
```

Drain 顺序：

1. 关闭外部 Contract command admission；
2. 通知 Actor quiescing，禁止创建新的业务工作；
3. 停止 Contract/Integration source runners，并允许已经形成的 terminal completion 入队；
4. 处理已接受 input；
5. flush ordered output 和最新 dirty view；
6. 调用 Actor drained/stopping hook，由 Actor 直接执行 concrete Contract/Integration 的
   provider-specific async close/logout；
7. 关闭 host、publisher、view、task 和 transport；
8. 清理 Conflux-owned UDS 等资源；
9. 仅在没有静默丢弃 accepted work 时报告 `Stopped`。

Immediate 或 deadline expiry：

- 关闭 admission；
- abort cancel-safe source/task；
- fail pending typed reply；
- 丢弃 queued input 并计量；
- 对可能已经发送的 Integration command 保留 uncertain outcome/audit；
- best-effort close concrete resource；
- 报告 `Forced`。

Conflux 不要求所有 Integration connection 实现虚假的统一 lifecycle trait。Actor 在 lifecycle
hook 中直接调用 concrete connection 的真实 close/logout；Drop 是最终兜底，不代替必要的
异步关闭。

## 10. 依赖和所有权方向

目标依赖方向：

```text
module main crate
    -> its own Contract
    -> consumed module Contracts
    -> kairos-conflux
    -> kairos-integration provider-native capabilities

kairos-conflux
    -> no business module Contract
    -> no provider implementation

module Contract
    -> protocol/transport/primitives
    -> optional small Conflux contract descriptor API if accepted

kairos-integration
    -> provider protocol and normalized external facts
    -> no business module main crate
```

所有权：

| Concern | Owner |
|---|---|
| business state/invariants | module Application/Actor |
| public command/event/view vocabulary | owning module Contract |
| Contract client/host/codec/publisher/reader | owning module Contract |
| provider auth/protocol/capability/normalized facts | Integration |
| concrete endpoint/connection selection | module composition |
| unique outward `A::Contract` | owning module Contract defines it; composition constructs it; Conflux owns it directly |
| concrete dependency client fields | `ConfluxSystem` declares them; composition constructs named instances |
| concrete Integration connection fields | `ConfluxSystem` declares them; composition constructs named instances |
| same-type named instance key and cardinality | consuming module/composition |
| source/task scheduling, bounded ingress, turn commit, readiness, shutdown | Conflux |
| provider route/source selection and business consequences | consuming module Application/composition |
| Application/domain to Contract projection | module composition/publication adapter |

Conflux 是 runtime capability，不是第二个 application facade。跨 module 调用仍通过对方
Contract client 或 application API，不能导入对方 services/private files。

## 11. 非目标

- 不为所有 provider 创建 universal Integration trait。
- 不把 provider-native payload 放进 Actor 或 Contract。
- 不让 Domain 依赖 Contract transport、mmap、Aeron、HTTP 或 provider SDK。
- 不让 Conflux 选择 Execution route、Market source 或 Account principal。
- 不允许一个 module process 通过 Conflux 暴露多个业务 Contract；一个进程只有一个 owning
  module Contract，多个 transport host 是该 Contract 的 transport projections。
- 不把所有 query 强制串行进入 Actor；current view/projection 是默认 read plane。
- 不要求所有 view 使用 mmap；Reference typed SQLite projection 是合法 Contract view。
- 不承诺 runtime-loaded unknown plugin。如果未来要求不重新编译即可加载未知 provider，必须
  单独设计动态边界，不能提前污染当前闭合 workspace。
- 不仅为了文件布局一致而迁移已经责任清晰的 transport-only code。

## 12. 为什么该抽象现在成立

1. **当前问题：** 五个 module 已经重复实现相同的数据流、source/task ownership、
   event/view publication、readiness 和 shutdown，但行为与故障语义不一致。
2. **当前调用者：** Account、Market、Execution、Reference 和 Risk 的长运行 process 是
   五个真实生产候选，而不是测试 fake。
3. **现有边界不足：** Contract 已拥有 client/event/view 的大部分客户端边界，但没有完整
   generated host/operation binding；Integration 已拥有 concrete capability，但各 module
   重复管理其 task、completion 和 health；workspace/process loop 没有统一保证。
4. **最小实现：** 编译期闭合 ingress、Actor-associated resource structs、Contract-owned
   host/event/view、typed source supervision、turn output protocol 和一个 shutdown state
   machine。
5. **迁移后删除：** module 自定义 HTTP route match、重复 listener/task registry、重复
   event ack/view dirty loop、Conflux TypeId catalogs、erased Actor dispatch 和同 slice 的旧
   process loop。
6. **验证：** module 行为测试、Contract client/host round trip、source gap/resync、typed
   compile-fail、failure/shutdown、stress/benchmark 和 architecture search。

## 13. 迁移计划

### Phase 0：接受目标并冻结扩张

- 决定本提案是否替代既有 Conflux RFC 中 TypeId catalog 和 HTTP-shaped handler 目标；
- 在决定前，不继续给旧 catalog 增加 publisher/view/task manager 等新层；
- 建立五个 module 的 control operation、event、view、Integration resource 和 process
  responsibility inventory。

退出：仓库只有一份明确目标架构，冲突文档已同步。

### Phase 1：最小 REST Contract

- 先以不依赖任何业务 module 的抽象示例定义准确的 REST `Request -> Response` 类型对；
- Actor 关联唯一 concrete Contract，Contract 关联该 REST pair；
- transport host 将 request 适配为 Actor 的闭合 ingress，并负责返回 response；
- 不抽象 Endpoint、client、View、Aeron、publisher、reader 或 stream；
- system resources 直接持有各 module Contract crate 当前已有的 concrete client。

退出：修改 request 或 response 会约束 owning Contract 与对应 ingress 适配；Conflux 中没有
为了统一现有模块而创建的 client/view/stream trait。

### Phase 2：闭合 ingress 与静态资源

- Actor 只声明唯一 `Contract`、闭合 `Ingress` 和闭合 `Output`，不声明 client/connection
  子集；
- `ConfluxSystem` 明确列出全量 concrete client 与 connection 字段，并把同一实体暴露给
  Actor 按需创建和使用实例；
- module composition 创建 `A::Contract` 并与 Actor、`ConfluxSystem` 一起移动进 Conflux；
- 移除该 Actor 路径的 erased dispatch、`ContractCatalog` 和 `IntegrationCatalog`；
- 为真实多个实例实现 `ManagedClients<K, C>` 与 `ManagedConnections<K, C>`；固定角色可以
  使用显式 named struct，不引入跨类型 registry。

退出：静态搜索在迁移 slice 找不到 `Any`、`TypeId`、downcast 或 `dyn Connection`。

### Phase 3：Contract event/view 输出闭环

- Contract event 和 view marker 成为唯一跨进程输出类型；
- Conflux turn 统一 ordered ack 和 latest dirty generation；
- Application/domain 直接映射到 Contract type，删除 JSON model adapter；
- client 可以用 view watermark + event sequence 完成恢复测试。

退出：事件失败不误 ack，view 失败不清 dirty，restart/gap 可恢复。

### Phase 4：Contract/Integration typed source supervision

- concrete client/connection 创建 typed stream；
- Conflux 监督 bounded polling、failure、cancellation、fencing 和 route delivery；
- source exit/panic/overflow 更新 resource state 和 readiness；
- module 明确 gap 后 resync、reconcile、stale 或 fatal 后果。

退出：无 unmanaged source task，无第二个 Actor mutation入口。

### Phase 5：逐 module 迁移

建议顺序：

1. Risk：验证第一个业务 Contract 能复用最小 REST request/response pair；
2. Market：验证高吞吐 source、freshness、gap/resync 和 publication backpressure；
3. Account：验证 snapshot recovery、buffered replay、多 principal 和 persistence worker；
4. Reference：验证 typed SQLite projection、transactional publication 和 provider refresh；
5. Execution：最后验证 may-have-been-sent command、audit、reconciliation 和多 route。

每个 module 必须按业务 slice 迁移；新 slice 通过后删除同 slice 旧路径，不保留 compatibility
facade。

## 14. 验证与退出条件

### 14.1 Compile-time

- Contract REST request/response 不匹配时编译失败；
- 每个 Actor 只能声明一个 owning `Contract`；
- `Clients` 与 `Connections` 必须是 system 的完整静态 named list，system composition 缺少
  任何已集成类型的 required field 时编译失败；Actor 不能声明另一份局部类型目录；
- source item 与 Actor target ingress 不匹配时编译失败；
- Actor 无法访问 system resource universe 之外的 Contract/Integration 类型；
- 迁移 Actor dispatch 无 `Any`/`TypeId`/downcast。

### 14.2 Behavior

- control client -> Contract host -> Actor -> typed response round trip；
- business rejection 不终止 runtime；fatal invariant failure 强制 cleanup；
- ordered event publish/ack/retry；
- latest view generation coalescing；
- view + event watermark 恢复；
- Contract 和 Integration stream replacement fencing；
- source normal exit、panic、overflow、reconnect 和 ignored cancellation；
- command/query/stream overload 分别满足其策略；
- provider command 不在 may-have-been-sent 后透明 retry；
- typed completion 只能通过 Actor 改变业务状态。

### 14.3 Shutdown

- idle Drain；
- accepted backlog Drain；
- source/task/ordered output backlog；
- stopping hook failure；
- one absolute deadline；
- Immediate 丢弃计量；
- uncertain Integration operation 保留审计；
- run future cancellation/panic 后 handle 不继续报告 Running。

### 14.4 Repository checks

除 focused tests 外，执行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

并搜索：

```text
TypeId / Any / downcast / erased dispatch in Conflux resource, ingress, dispatch and output paths
dyn Connection / ConnectionSpec / IntegrationCapability legacy paths
custom module HTTP method/path dispatch replaced by Contract host
cross-module services/private imports
serde_json::to_value/from_value in event/view publication
unmanaged tokio::spawn and thread::spawn in migrated process code
duplicate mutable state owners
```

## 15. 成功判据

本提案成功不是因为少写了一个 `select!`，而是迁移后的 module 在结构上不能出现：

- Contract client 与服务端 route/type 漂移；
- source task 已死但 runtime 仍 Ready；
- handler 业务拒绝导致整个进程退出；
- event publish 失败却被 acknowledge；
- view publish 失败却清除 dirty generation；
- stale resource epoch 的 fact 修改当前状态；
- background completion 绕过 Actor 修改 Application；
- shutdown 丢弃 accepted work 却报告 clean；
- Integration command 可能已发送却被当作未发送；
- module 忘记 join 一个 runtime-owned source/task；
- Actor 通过任意类型目录访问未声明资源；
- Contract event/view 经 JSON round trip 适配。

最终目标是：

```text
每个进程以唯一 concrete A::Contract 提供本 module 的完整对外服务；
ConfluxSystem 以明确字段全量集成其他 module clients 和 Integration connections；
Actor 按需创建每种 client/connection 的命名实例；
Contract 定义 Kairos module 之间的闭合类型化协议；
Integration 提供 module 与 provider 之间的 concrete capability；
Application/Actor 拥有业务状态和不变量；
module composition 构造唯一服务，workspace/system composition 构造全量 clients 和 connections；
Conflux 不以 TypeId、Any、downcast 或 erased dispatch 表示资源和消息；
Conflux 保证所有输入、状态转换、输出、资源和关闭经过同一个可证明的数据流。
```
