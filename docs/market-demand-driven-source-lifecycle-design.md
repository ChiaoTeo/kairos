# Market 按需 Source 生命周期设计

## 1. 文档状态与适用范围

本文定义 live/paper Market 的按需 Source 生命周期，是
[`market-target-architecture-and-migration-plan.md`](market-target-architecture-and-migration-plan.md)
中以下旧决策的架构修订：

- runtime profile 静态选择 `required_sources` / `optional_sources`；
- Market 进程启动时构造 profile 中的全部 source；
- 进程级 business readiness 由静态 required source 聚合；
- subscription 只能路由到启动时已经 attach 的 source。

上述内容与本文冲突时，以本文为准。原目标架构中的单 Actor、async-first、provider-native
Integration capability、bounded channel、epoch、order-book recovery、backpressure 和模块依赖
方向继续有效。

Integration 的 provider connection、认证、capability 和外部事实语义仍以
[`integration-session-and-operation-design.md`](integration-session-and-operation-design.md)
为准。本文不把 provider connection 或 normalizer 移入 Market domain。

本文同时记录当前迁移基线：live/paper 已不再从 runtime profile 读取
`required_sources` / `optional_sources`，空 source 启动可用，订阅会通过 composition
按需激活匹配的 source；replay 仍保留显式 source，因为它本身就是回放输入。后续仍需
source-level readiness 已下沉到 snapshot 中的 subscription/member projection；后续仍需在有
资源测量后补齐空闲 source 回收策略。

## 2. 决策摘要

### 2.1 核心决定

Live/paper Market 进程启动时不建立任何 provider source。进程只启动：

- 唯一 Market Actor；
- control、snapshot 和 event publication；
- Reference projection/recovery；
- composition 提供的按需 source activation capability。

策略或系统提交订阅后，Market 才根据已经解析的 canonical market route 判断需要哪一个
source。若 source 尚未存在，Market 发起一次幂等激活；激活完成后再建立 provider
subscription。

```text
subscribe intent
  -> Reference resolves canonical market members
  -> Market derives SourceRouteKey
  -> existing source? reuse : activate through composition
  -> wait for source readiness
  -> subscribe through Integration capability
  -> subscription member ready
```

### 2.2 配置只描述外部事实，不描述活跃拓扑

配置可以描述：

- provider 是否获准在当前 workspace 使用；
- credential reference、environment 和 endpoint override；
- provider/product 的 transport policy；
- 同一路由存在多个候选 source 时的显式选择；
- Actor 队列、snapshot、freshness、shutdown 等进程参数；
- shared/instance/replay scope。

配置不再描述：

- 当前进程启动哪些 source；
- `required_sources` / `optional_sources`；
- 手工维护的 live `SourceId` 清单；
- 为了覆盖未来可能订阅而预先建立的连接并集。

### 2.3 Readiness 从 source-global 改为 demand-scoped

- process readiness：Actor 和本地控制面可接受命令；零 active source 也可以 ready；
- source readiness：一个已经激活的外部 source 是否满足 provider readiness barrier；
- subscription readiness：某个订阅的 required members 是否已经获得 provider confirmation；
- data freshness：某个 market/data kind 的最近事实是否仍可使用。

Source 的故障只影响路由到该 source 的订阅，不自动降低无关订阅的 readiness。进程仍可运行、
查询已有 snapshot、接受 unsubscribe，并尝试恢复失败 source。

### 2.4 第一版不自动回收空闲 live source

第一版按需创建，但 source 在没有订阅后仍保留到进程 shutdown。这样可以先消除启动耦合，
同时避免订阅抖动导致反复连接、认证和 snapshot recovery。

只有在 connection 数量、quota、内存或费用测量证明需要后，才增加 idle timeout。不得先引入
通用连接池、LRU、registry 或 source manager。

## 3. 问题定义

历史实现曾把 source 的构造时机绑定到 runtime profile。当前迁移已移除 live/paper 的这条绑定，
保留 replay 的显式输入语义；当前按需路径为：

1. live/paper 进程以零 provider source 启动并保持 control-plane ready；
2. subscription intent 到达后由 composition activator 按 `MarketDescriptor` 选择 route；
3. source attach 后由 Actor 统一驱动订阅、输入、freshness 和事件；
4. source activation 或 reconciliation 失败会返回明确的可重试错误并回滚订阅 intent；
5. replay 仍由 replay composition 显式 attach，checkpoint 完成后不会向已关闭 channel 重发命令。

这会产生以下问题：

- 一个共享 Market 为覆盖所有 launch，被迫预装所有 provider/product 的并集；
- 未使用的 credential、endpoint 或 provider 故障可以阻止启动；
- optional 虽不影响 global readiness，仍在启动时被构造并占用资源；
- 新 launch 的 route 需求与已经运行的 shared Market profile 可能不兼容；
- source requirement 是部署配置，而不是由真实业务需求产生；
- source 没有覆盖订阅时缺少明确、可操作的失败结果。

目标不是删除 source。Source 仍然是 provider/product/transport 的必要 I/O、epoch、ordering、
recovery 和 backpressure 边界。目标是把 source 从“启动依赖”改为“订阅驱动的内部资源”。

## 4. 所有权与边界

### 4.1 Market 拥有

- subscription intent 及其 required/optional 业务语义；
- canonical market member 与 `SourceRouteKey` 的映射；
- 是否需要激活某个 source 的决定；
- pending activation、active source、provider subscription handle 的业务投影；
- source-to-subscription 依赖关系；
- observation、order book、freshness、snapshot 和业务事件；
- source failure 对具体 subscription 的影响；
- reconnect 后重新订阅与单 market resync 决策。

这些 mutable facts 仍然只有一个 owner：`MarketActor`。

### 4.2 Integration 拥有

- provider-native connection/config/capability；
- credential 使用、认证、quota 和 provider channel；
- provider subscription protocol、channel epoch 和 provider handle；
- provider ordering、normalization、reconnect 与外部错误分类；
- normalized external market facts。

Market 不复制 Integration API，不导入 Integration private services，也不从 raw payload 推导
业务状态。

### 4.3 Composition 拥有

- 根据 `SourceActivationRequest` 选择具体 Integration constructor；
- 加载 workspace credential 和 endpoint override；
- 应用 provider/product transport policy；
- 构造 source driver 和 bounded channel；
- 将构造结果作为 `SourceHandle` 返回给 Market process。

Composition 不保存 active source map、subscription intent 或第二份 lifecycle 状态。它是无业务
状态的具体工厂边界，不是中心 Integration registry。

### 4.4 Reference 拥有

- canonical instrument/listing/market identity；
- provider instrument reference 和有效期；
- Market 进行 route 选择所需的稳定 market facts。

Market 不从 `MarketId` 或 symbol 字符串反解析 provider、product 或 provider symbol。

### 4.5 Workspace/System 拥有

- process identity、scope、path、lock、socket 和 instance resource；
- credential allocation 与 live safety；
- shared/instance/replay 进程启动和停止。

## 5. 核心模型

### 5.1 `SourceRouteKey`

`SourceRouteKey` 是 Market-owned、可比较的路由需求，不包含 SDK config：

```rust
pub(crate) struct SourceRouteKey {
    pub provider: ParticipantId,
    pub exchange_id: Exchange,
    pub market_type: MarketType,
    pub asset_type: AssetType,
    pub environment: MarketEnvironment,
}
```

说明：

- `provider` 来自 Reference provider/source fact 或 composition 的显式 route policy；
- `exchange_id`、`market_type`、`asset_type` 来自 canonical `MarketDescriptor`；
- `environment` 来自 process/launch identity，不由策略任意指定；
- transport 不属于业务 route，WebSocket/REST snapshot 的选择留在 composition policy；
- provider symbol 由 Reference/provider reference 显式提供，不从 canonical ID 解析。

如果当前 Reference contract 尚不能无损提供 provider 和 provider symbol，先扩展 Reference 到
Market 的 application/contract mapping；不得临时依赖字符串命名约定。

### 5.2 `SourceId`

`SourceId` 仍然标识一个已激活 source，但由 composition 根据稳定 binding 事实确定性生成，
不再由 runtime profile 手写。例如：

```text
binance:public:spot:websocket
binance:public:options:websocket
massive:credential-alias:equity:websocket
```

`SourceId` 不能包含 secret。相同 `SourceRouteKey` 和相同 concrete binding 必须产生相同
`SourceId`，从而支持并发激活去重和日志关联。

### 5.3 Subscription requirement

`required/optional` 从 Source 移到 subscription intent/member：

```rust
pub enum SubscriptionRequirement {
    Required,
    Optional,
}
```

- required member 未 ready：该 subscription 未 ready；
- optional member 失败：subscription 可以 ready，但结果和 health 必须报告 degraded member；
- 一个 source 同时服务 required 和 optional members 时，其故障分别影响这些 member；
- Source 本身不携带全局 requirement。

### 5.4 Activation request/result

Market-owned request 只包含构造 source 所需的稳定业务/连接选择事实：

```rust
pub(crate) struct SourceActivationRequest {
    pub activation_id: SourceActivationId,
    pub route: SourceRouteKey,
    pub reason: SourceActivationReason,
}

pub(crate) enum SourceActivationResult {
    Activated {
        activation_id: SourceActivationId,
        source_id: SourceId,
        handle: SourceHandle,
    },
    Rejected {
        activation_id: SourceActivationId,
        error: SourceActivationError,
    },
}
```

`SourceHandle`、driver channel 和 task handle 是 crate-private runtime mechanics，不进入 Market
public application API 或 snapshot contract。

Activation request 不携带 provider instrument：一个 source 通常服务同一路由下的多个
instrument。provider instrument reference 只在后续 `SourceCommand::Subscribe` 中随具体 member
发送，避免第一次订阅的 symbol 意外成为 source identity。

### 5.5 最小 activation boundary

为保持 `application -> composition` 依赖禁止，同时允许运行时按需构造 concrete provider，定义
一个 crate-private、Market-owned 的最小 capability：

```rust
pub(crate) trait ActivateMarketSource: Send + Sync {
    fn activate(
        &self,
        request: SourceActivationRequest,
    ) -> Pin<Box<dyn Future<Output = SourceActivationResult> + Send + '_>>;
}
```

生产实现 `WorkspaceMarketSourceActivator` 位于 `composition/`，测试实现位于 Market fixture。
它只做一次 source 构造，不提供 list/get/register/reconnect/subscribe API，因此不是
`SourceRegistry`、manager 或替代 application facade。

这个边界的必要性：

1. 当前调用者是 `MarketProcess` 的 activation effect runner；
2. concrete provider constructor 和 credential 只能由 composition 看见；
3. Actor turn 不能执行长时间 provider I/O；
4. 测试需要确定性模拟成功、拒绝、延迟和取消；
5. 迁移完成后删除 eager `attach_workspace_market_sources` 和 profile source 列表。

## 6. 目标运行架构

```text
bin
  -> composition
       -> build MarketProcess
       -> inject WorkspaceMarketSourceActivator
  -> application::MarketProcess
       -> one MarketActorTask
            -> MarketApplication -> MarketActor
            -> activation effect runner
            -> source command/input drivers
       -> private control/publication/reference services
```

Actor 只产生 `ActivateSource` effect，不直接调用 composition。`MarketActorTask` 执行 effect，
等待 activation future，并把 result 放回同一个 Actor event loop。只有 Actor turn 能提交
pending/active source 和 subscription 状态变化。

Activation future 不得持有 Actor mutable reference。多个 future 可以并行，但同一个
`SourceRouteKey` 最多有一个 pending activation。

## 7. 状态模型

### 7.1 Actor-owned maps

```rust
struct MarketActor {
    subscriptions: BTreeMap<SubscriptionId, SubscriptionState>,
    route_activations: BTreeMap<SourceRouteKey, RouteActivationState>,
    sources: BTreeMap<SourceId, SourceState>,
    source_routes: BTreeMap<SourceRouteKey, SourceId>,
    pending_source_requests: BTreeMap<SourceRequestId, PendingSourceRequest>,
    // observations, books, freshness, events ...
}
```

`RouteActivationState`：

```rust
enum RouteActivationState {
    Pending {
        activation_id: SourceActivationId,
        waiting_members: BTreeSet<SubscriptionMemberKey>,
        attempt: u32,
    },
    Active {
        source_id: SourceId,
    },
    Failed {
        failure: SourceActivationFailure,
        retry_after: Option<Instant>,
    },
}
```

Driver task、receiver 和 provider handle 仍可保存在 Actor private attached-source state；snapshot
只发布可序列化 projection，不发布 channel/task。

### 7.2 Source 状态机

```text
Absent
  -> Activating
       -> Starting -> Ready <-> Reconnecting/WarmingUp
       -> ActivationFailed
  -> Stopping -> Stopped
```

约束：

- `Absent -> Activating` 只能由存在真实 subscription member 的需求触发；
- 相同 route 的并发需求加入 `waiting_members`，不能重复构造；
- `Activated` 后必须先 attach handle，再允许发送 subscribe；
- connection established 不等于 Ready；Integration readiness barrier 必须完成；
- reconnect 复用同一个 active source 和 source epoch 机制，不重新调用 activator；
- activation 失败与 active source 运行失败是不同错误；
- stale activation ID、source epoch 或 provider ack 不得改变当前状态。

### 7.3 Subscription member 状态机

```text
ResolvingReference
  -> WaitingForSource
  -> WaitingForSourceReady
  -> Subscribing
  -> Ready
  -> Recovering
  -> Unavailable | Rejected | Removed
```

一个 subscription 可以包含多个 members。聚合状态：

- `Ready`：全部 required members Ready；
- `Pending`：至少一个 required member 尚在 resolving/activating/subscribing；
- `Degraded`：required members Ready，但 optional member unavailable/recovering；
- `Unavailable`：至少一个 required member发生可重试运行故障；
- `Rejected`：intent、Reference、route 或 provider 明确拒绝，且不能靠自动恢复完成；
- `Removed`：unsubscribe 已提交并完成本地状态删除。

## 8. 订阅完整时序

### 8.1 新订阅

1. Control adapter 解码 `SubscribeMarket`，校验 command/idempotency envelope。
2. Actor 校验 subscription ID、owner、selector 和 requirement。
3. Actor 保存 intent，状态设为 `ResolvingReference`。
4. Reference projection 返回零个、一个或多个 canonical market members。
5. Actor 为每个 member 构造 `SourceRouteKey`。
6. 若 route 已 active，member 进入 `WaitingForSourceReady` 或 `Subscribing`。
7. 若 route 正在 activating，member 加入同一 pending activation。
8. 若 route absent，Actor 记录 pending activation 并产生 `ActivateSource` effect。
9. Process 使用 activator 构造 concrete Integration capability 和 driver。
10. Activation result 回到 Actor；Actor 校验 activation ID 并 attach `SourceHandle`。
11. Source 达到 provider readiness barrier 后发送 `SourceCommand::Subscribe`。
12. Provider ack 带 source ID、epoch 和 request ID 返回。
13. Actor 保存 confirmed handle，member 进入 `Ready`。
14. Control query/event 可观察从 Pending 到 Ready/Rejected 的状态变化。

初始 subscribe command 返回 `202 Accepted`，表示 intent 已被持久接受，不等于 provider 已
ready。调用者通过 command result、subscription status 或 lifecycle event 等待最终状态。

### 8.2 Reference 暂未就绪或零成员

- Reference projection 未 ready：subscription 保持 `ResolvingReference`，不是 source failure；
- dynamic query 当前零成员：根据 intent policy 表达 `ReadyEmpty` 或继续 Pending；
- static route 无 canonical market：返回 `reference.market_not_found`；
- Reference 后续新增/删除 member：Actor 对新增 member 执行相同 activation 流程，对删除 member
  发起 unsubscribe。

不得为了绕过 Reference readiness 从 symbol 或 MarketId 猜测 provider route。

### 8.3 无法选择 source

当前实现中的“零匹配直接跳过”必须删除。目标行为：

- provider/product 不受支持：`source.unsupported_route`；
- credential binding 缺失：`source.credential_unavailable`；
- provider 被 workspace policy 禁止：`source.not_authorized`；
- 多个候选 source 且没有选择策略：`source.ambiguous_route`；
- endpoint/config 无效：`source.invalid_binding`。

错误必须关联 subscription/member/route，并进入可查询状态。不能 accepted 后无事件、无失败。

### 8.4 Unsubscribe

1. Actor 删除或标记 subscription intent removing；
2. 对 confirmed provider handles 发送 unsubscribe；
3. pending activation 不因单个 member 删除而取消，除非已经没有任何 waiting member；
4. pending subscribe ack 到达时若 intent 已删除，立即发送对应 unsubscribe；
5. 第一版 source 即使没有 remaining members 也保持 active；
6. snapshot/event 明确发布 subscription removed。

### 8.5 并发与幂等

- command idempotency key 相同且 payload 相同：返回同一 intent/result；
- idempotency key 相同但 payload 不同：拒绝；
- 多个 subscription 同时请求同一路由：只产生一个 activation；
- activation 成功后所有 waiting members 独立发送 provider subscribe；
- 同一 business member 被多个 owner 订阅时，第一版允许独立 provider handles；只有 provider
  quota 测量证明需要时才增加共享 provider subscription multiplex；
- stale activation completion、epoch、ack 和 observation 全部被拒绝或忽略并记录指标。

## 9. Source 选择规则

按以下顺序选择 concrete source：

1. Reference market/provider reference 给出的显式 provider/source binding；
2. launch/workspace composition 对该 route 的显式 override；
3. 唯一的内置 provider-native capability；
4. 否则返回 ambiguous/unsupported，不做猜测和自动跨 provider failover。

同一 canonical market 的不同 provider 是不同 route。Market 可以同时保存它们的 observation，
但一个 provider 的值不能静默覆盖另一个 provider 的 source-aware projection。

Transport 选择属于 concrete composition，例如 Binance Spot 默认 WebSocket，必要 snapshot 由
同一个 Integration market capability 的 recovery barrier 获取。REST periodic snapshot 与
WebSocket live 是不同 source policy，不由策略直接选择，除非业务 API 明确需要 snapshot-only
语义。

## 10. 配置模型

### 10.1 Runtime 配置

Live runtime profile 不再包含 source 列表。若仍保留 profile 名称，它只选择进程资源和策略：

```toml
[market]
default_runtime = "shared-live"

[market.runtimes.shared-live]
scope = "shared"
source_input_capacity = 10000
publication_queue_capacity = 256
snapshot_interval_ms = 1000
freshness_check_interval_ms = 250
freshness_max_age_ms = 5000
reference_recovery_interval_ms = 1000
shutdown_timeout_ms = 5000
```

也可以在迁移后只有一套 live runtime defaults，从而让普通 workspace 完全不配置 runtime
profile。Launch 只选择 `scope = shared | instance`；replay 继续选择显式 replay policy/resource。

### 10.2 Provider/connection 配置

公开、具有安全默认 endpoint 的 provider 可以零配置激活。需要凭据或 override 时，只配置外部
连接事实：

```toml
[market.providers.massive]
credential_id = "massive-readonly"

[market.providers.binance]
environment = "public"

[market.providers.binance.options]
transport = "websocket"
```

目标 Rust config 仍使用 provider-native typed variants/tables，不能改成带任意 JSON payload 的
通用 provider map。

“配置 provider 可用”不等于创建 source。只有订阅解析到该 provider/product 后才读取相关
credential 并调用 Integration constructor。

### 10.3 安全约束

- 不通过扫描 credential 文件自动授权所有 provider；
- credential value 不进入 activation request、Actor state、日志或 snapshot；
- activation error 只能包含 credential ID/alias，不能包含 secret；
- live/testnet/environment 由 Workspace/System 分配，策略不能通过 subscription 覆盖；
- endpoint override 必须通过 provider-native config 校验；
- 不自动跨 provider failover。

### 10.4 Replay

Replay 是有限、instance-owned 的显式 source，继续由 launch materialize replay file。它不需要
live activator，也不与 live source 自动混合。Replay process 可以在构造时 attach 唯一 replay
producer，因为 replay resource 本身就是该实例的明确业务输入。

## 11. Readiness、health 与可观测性

### 11.1 Process health

零 source 的 live Market 返回：

```json
{
  "status": "ready",
  "active_source_count": 0,
  "pending_activation_count": 0
}
```

进程只有在 Actor/control/publication/Reference 必要资源不可用时才不是 process-ready。

### 11.2 Source health

每个 active/pending source 至少暴露：

- route key 与非敏感 binding identity；
- activation ID、source ID、status、epoch；
- waiting/ready subscription member count；
- last failure kind/time；
- reconnect/resync/overflow counters；
- input queue depth/lag。

### 11.3 Subscription health

每个 subscription 至少暴露：

- intent owner、selectors 和 requirement；
- resolved members；
- 每个 member 的 route/source/status；
- pending activation/source request ID；
- rejection/unavailable error；
- confirmed provider handle 的非敏感 projection；
- last observation/freshness watermark。

### 11.4 Launch readiness

Launch 不再通过“Market 的所有 required source ready”判断策略可运行。策略启动后提交 required
subscriptions，launch/strategy runtime 等待这些 subscription 达到 Ready，或在明确 timeout 后
失败。Optional subscriptions 不阻止 launch ready，但必须报告 degraded。

## 12. 错误与重试

至少区分：

```text
source.unsupported_route
source.ambiguous_route
source.not_authorized
source.credential_unavailable
source.invalid_binding
source.activation_failed
source.unavailable
source.backpressure
source.resync_required
subscription.rejected
subscription.timeout
reference.not_ready
reference.market_not_found
```

重试规则：

- 配置、授权、credential 缺失、unsupported/ambiguous 不自动重试；配置或 Reference 变化后可由
  显式 recover/reconcile 再试；
- DNS、connect、temporary provider unavailable 可使用 bounded exponential backoff；
- 同一路由 retry 仍复用同一 pending activation identity lineage，attempt 递增；
- active stateful channel 的 transport failure 由 Integration reconnect，不重新构造 source；
- queue overflow、order-book gap/checksum failure 按现有 affected-market resync 语义处理；
- shutdown 后不启动新的 activation/retry。

## 13. Backpressure 与故障隔离

- 每个 source 保留独立 bounded input queue；
- activation completion 使用独立 bounded internal result channel；
- activation result channel 满时不得丢弃，process 必须等待或进入明确 fatal runtime error；
- source A 激活/认证慢不能阻塞 source B 的 Actor inputs；
- source A 的 unavailable 只改变依赖 A 的 subscription members；
- publication slow client 继续独立移除，不阻塞 Actor；
- 同一 source 上的 order-book resync 只影响对应 market，除非 Integration 明确报告 channel-wide
  recovery barrier。

## 14. Shutdown

有序 shutdown：

1. 停止接受新的 subscribe/recover；
2. 标记所有 pending activation cancelling；
3. 等待或取消尚未交付 `SourceHandle` 的 activation future；
4. 若 completion 与 cancellation 竞态，Actor 接收 handle 后立即 shutdown，不能泄漏 driver；
5. 向全部 active source 发送 shutdown；
6. drain 已接收的 SourceInput 和 activation result；
7. 发布最终 subscription/source/snapshot watermark；
8. 等待 driver/publication 完成；
9. 超时错误列出 pending activation ID 和 unfinished source ID。

## 15. Public API 与内部 effect

Public application API 增加或明确以下业务结果，不暴露 activator/driver：

```rust
pub struct SubscribeMarketResult {
    pub subscription_id: SubscriptionId,
    pub status: SubscriptionStatus,
    pub members: Vec<SubscriptionMemberResult>,
}

pub enum SubscriptionStatus {
    Pending,
    Ready,
    Degraded,
    Unavailable,
    Rejected,
    Removed,
}
```

Actor/application 内部可以返回 effects：

```rust
pub(crate) enum MarketEffect {
    ActivateSource(SourceActivationRequest),
    SendSourceCommand { source_id: SourceId, command: SourceCommand },
    Publish(MarketEvent),
}
```

Effect 是同一 Actor turn 的输出，不是第二个 coordinator。`MarketActorTask` 是唯一 effect
runner，执行结果重新进入同一 Actor event loop。

## 16. 文件与依赖落点

建议在现有标准目录内收敛，不新增顶层 layer：

```text
src/
  composition/
    process.rs
    source_activation.rs             # WorkspaceMarketSourceActivator
    sources/{binance,massive,okx,hyperliquid}.rs
  application/
    process.rs                       # Actor task + effect runner
    service.rs                       # subscription command/result semantics
    facade.rs                        # Actor use cases/effects
  services/
    actor.rs                         # 唯一 mutable owner
    messages.rs                      # activation/source command/input
    sources/                         # concrete driver mechanics
  domain/
    source.rs                        # route/source/activation projections
    subscriptions.rs                 # intent/member/status invariants
```

依赖方向：

```text
bin -> composition -> application -> services/domain
          |                 |
          -> Integration    -> Market-owned activation capability
```

Application 不导入 composition；composition 实现 application/Market-owned crate-private
activation capability。其他业务模块仍只能通过 Market application/contract 访问 Market。

## 17. 明确拒绝的替代方案

### 17.1 启动时 attach 全部 configured source

拒绝。它把未来可能需求变成当前启动依赖，并扩大 credential、连接和故障域。

### 17.2 通用 SourceRegistry / ProviderManager

拒绝。当前只需要“按 route 构造一次 concrete source”的最小 capability；list/register/get、
跨业务复用和万能 provider dispatch 会形成第二 facade 和第二状态 owner。

### 17.3 Integration 中心进程或共享 socket

拒绝。它违反 Integration 权威设计，会合并 credential、quota、ordering 和故障域。

### 17.4 从字符串猜 provider

拒绝。不能从 `MarketId`、symbol 或 exchange 字符串拼接 provider symbol；必须使用 Reference
provider reference 和显式 composition policy。

### 17.5 自动跨 provider failover

拒绝。它会改变 source identity 和市场事实语义。多 provider 订阅必须由业务 intent 明确表达。

### 17.6 第一版自动关闭 idle source

拒绝。没有资源测量前不增加 timer/refcount teardown 状态机；进程 shutdown 是第一版唯一关闭
边界。

## 18. 分阶段迁移

### Phase 0：冻结当前行为（已完成）

- 为 eager attach、route no-match、required readiness 建立 characterization tests；
- 记录当前 `MarketRuntimeProfile.sources`、`SourceRequirement` 和
  `attach_workspace_market_sources` 调用图；
- 记录 shared Market 已运行时不同 profile 的行为；
- 不修改 provider driver 与 Integration semantics。

删除项：无。

### Phase 1：订阅状态与显式错误（已完成）

- 引入 subscription/member lifecycle projection；
- 将 route 零匹配从 silent `continue` 改为明确 pending/rejected result；
- process health 与 source/business readiness 解耦；
- 允许 live Market 在零 active source 下 ready；
- required/optional 进入 subscription intent。

删除项：source-global readiness 对 process health 的控制。

### Phase 2：Binance Spot 按需 vertical slice（已完成）

- 定义 `SourceRouteKey`、activation request/result 和最小 activator boundary；
- composition 只为 Binance Spot 实现按需构造；
- 并发 BTC subscriptions 验证 activation 去重；
- 现有 eager path 暂时只服务尚未迁移的 provider；
- 验证 activation failure、reconnect、unsubscribe 和 shutdown race。

删除项：Binance Spot 的 profile eager attach 路径。

### Phase 3：逐 provider/product 迁移（当前实现已覆盖 route selection；仍需逐切片补齐测试）

按 Binance derivatives/options、Massive、OKX、Hyperliquid 一次一个切片迁移：

1. 定义 route facts；
2. 实现 concrete activation branch；
3. 添加 success/failure/recovery tests；
4. 删除该切片的 eager branch；
5. 更新对应 adapter reference note。

不得先建立 universal provider activator enum 再批量迁移。

### Phase 4：删除 live source profile（已完成）

- `MarketRuntimeProfile` 删除 `sources`；
- Workspace live config 删除 `required_sources` / `optional_sources`；
- `SourceDescriptor` 删除全局 `requirement`；
- 删除 `attach_workspace_market_sources`；
- System/launch 不再传递 source-bearing profile；
- 保留 runtime resource policy 与 replay profile。

删除项：旧 source selection profile、兼容解析和静态 readiness 聚合。

### Phase 5：shared/instance 与配置收口（已完成）

- shared 和 instance live 使用相同 demand-driven activation；
- 校验 environment、credential allocation 和 route policy；
- 普通 public Market 验证零 provider/source 配置启动；
- credentialed provider 只在首次相关订阅时加载 credential；
- 更新 CLI、doctor、status、observe 和运行文档。

删除项：`diagnostic` 仅为允许空 source 而存在的特殊分支；若还有真实诊断调用者，可保留其明确
诊断语义。

### Phase 6：基于证据决定 idle teardown

只有满足以下至少一个条件才进入该阶段：

- provider 有明确 connection 数量或费用约束；
- profiling 显示 idle driver 占用不可接受；
- 长期 shared Market 累积 source 已成为实际问题。

届时单独设计 idle timeout、minimum residency、activation backoff 和 teardown race；不将其作为
本次迁移完成条件。

## 19. 验收矩阵

### 19.1 启动与激活

- live Market 无 source config、无 active source 时 process ready；
- 第一个 Binance Spot 订阅只创建 Binance Spot source；
- 未订阅 Massive 时不读取 Massive credential、不建立 Massive connection；
- N 个并发相同 route 订阅只执行一次 activation；
- 不同 route activation 可以并行；
- activation 失败不会停止 Market process。

### 19.2 路由与订阅

- 零匹配 route 返回明确 unsupported/rejected；
- 多匹配 route 返回 ambiguous，除非存在显式选择；
- required member 未 ready 时 subscription 不报告 Ready；
- optional member 失败只令该 subscription Degraded；
- source ready + provider subscribe ack 后 member 才 Ready；
- unsubscribe/activation/ack 竞态不泄漏 provider handle。

### 19.3 隔离与恢复

- Massive failure 不影响仅依赖 Binance 的 subscription；
- active source reconnect 不重新 activation；
- reconnect epoch advance 后 intent 自动 resubscribe；
- stale activation/epoch/ack/event 不改变 Actor 状态；
- order-book gap 仍只 resync affected market；
- source input backpressure 不阻塞其他 source。

### 19.4 生命周期

- 零订阅 source 第一版保持 active 到 process shutdown；
- shutdown 正确处理 pending activation completion race；
- shutdown timeout 列出 activation/source identity；
- final snapshot/event 包含最后 subscription/source watermark。

### 19.5 架构

- application/domain 不导入 composition 或 provider SDK；
- activator 不暴露 registry/list/get/manager API；
- active source map 和 pending activation 只有 Actor 一份；
- composition 不保存 subscription 或 source lifecycle 状态；
- production server binary 不增加 provider/product 参数；
- live config 不再出现 `required_sources` / `optional_sources`。

## 20. 验证命令与静态检查

每个切片先运行 focused tests，再运行：

```text
cargo test -p kairos-market-service
cargo test -p kairos-market-contract
cargo test -p kairos-integration
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

迁移期间增加静态搜索：

```text
rg -n "required_sources|optional_sources|SourceRequirement" \
  crates/business/market crates/kairos-workspace kairospy
rg -n "attach_workspace_market_sources|profile\.sources" \
  crates/business/market/service
rg -n "SourceRegistry|ProviderManager|ConnectionManager" \
  crates/business/market/service
```

最终前两组搜索只能命中 migration note、历史测试或 replay 特例；生产 live path 不得命中。

## 21. 非平凡抽象说明

### `SourceRouteKey`

1. 解决问题：稳定表达一个 subscription member 需要的 provider/product route，并支持激活去重。
2. 当前调用者：MarketActor subscription reconciliation。
3. 现有不足：`SourceDescriptor` 只描述已经 attach 的 source，不能表达尚未创建的需求。
4. 最简单实现：一个由 typed identity/value objects 构成的可排序 key。
5. 删除旧概念：profile 中手写 live Source ID 选择。
6. 证明：同 route 并发 activation 去重、ambiguous/unsupported route tests。

### `ActivateMarketSource`

1. 解决问题：application runtime 需要按需构造 source，但不能依赖 composition。
2. 当前调用者：唯一 `MarketActorTask` effect runner。
3. 现有不足：`attach_workspace_market_sources` 只能在启动时 eager 构造。
4. 最简单实现：只有一个 `activate(request) -> result` 的 crate-private async capability。
5. 删除旧概念：eager attach 函数和 live profile source 列表。
6. 证明：provider vertical slice、并发去重、failure/cancellation/shutdown tests。

### Subscription requirement/readiness

1. 解决问题：source-global required/optional 把无关消费者和故障域耦合。
2. 当前调用者：策略/launch subscription lifecycle。
3. 现有不足：global feed readiness 无法表达不同订阅对同一 source 的不同要求。
4. 最简单实现：requirement 放到 intent/member，聚合 subscription status。
5. 删除旧概念：`SourceDescriptor.requirement` 和 global business readiness。
6. 证明：required/optional members 与 route failure isolation tests。

## 22. 完成定义

当且仅当满足以下条件，本设计才算落地：

- live/paper Market 在零 active source 下可启动并 process-ready；
- source 只由真实 subscription member 按需激活；
- 相同 route 的并发需求只构造一个 source；
- MarketActor 是 pending activation、active source 和 subscription lifecycle 的唯一 owner；
- provider construction、credential 和 endpoint 仍由 composition/Integration 拥有；
- subscription 无 route 时明确失败，不再静默 accepted；
- readiness 以 subscription demand 为边界，不以静态 source profile 为边界；
- live runtime config 不再列出 required/optional source；
- replay 保持 instance-owned、显式、确定性的独立模型；
- 每个 provider/product 切片的新路径通过后，其 eager path 被删除；
- 没有新增 registry、manager、万能 adapter 或第二业务 facade；
- focused tests、架构检查和相关 workspace checks 通过。

本次验证记录：`cargo test -p kairos-market-service --lib` 39 tests 通过；Market
actor/architecture/orderbook/replay 集成测试全部通过；`uv run pytest -q` 为 175 passed、
8 skipped；`cargo fmt --all -- --check` 与 `git diff --check` 通过。曾尝试运行
`cargo test --workspace --lib`，但在既有 `kairos-integration` 测试二进制中长时间无输出，未将
该次未完成运行计作全仓通过；Market 相关范围已单独完成验证。
