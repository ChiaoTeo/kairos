# Integration、Usecase、System 与 Runtime 架构设计

本文基于当前仓库的模块边界规范、`System` 设计、`integration/usecase` 实现现状，以及对 `kairos_v2`、成熟交易系统和 DDD 分层思想的对照，明确后续架构方向。

本文回答四个问题：

1. `integration` 的真正价值是什么？
2. `usecase` 应该负责什么？
3. `System` 与 `runtime` 的关系是什么？
4. 连接、业务组件和外部适配器由谁持有、谁管理生命周期？

相关边界规则以 [`module-boundaries.md`](./module-boundaries.md) 为准。本文是对其中 `integration`、`usecase`、`System` 和 `runtime` 部分的进一步收敛。

---

## 一、最终结论

当前架构应形成下面的职责关系：

```text
System
    持有一个运行中的交易系统实例
    持有业务组件
    持有 integration 连接资源
    管理生命周期
    驱动 runtime

Usecase
    表达业务动作和业务查询
    协调领域对象和业务状态
    依赖业务模块自己的 Protocol
    不创建或管理外部连接

每个业务模块提供一个 System-scoped 的 Application Component，作为
runtime 和 system 共同使用的唯一业务入口；Component 内部可以继续拆分
多个具体 usecase handler。

Integration
    表示外部系统能力的适配边界
    持有外部连接和 vendor client
    转换请求、响应、事件和错误
    实现 usecase 所需的 Protocol
    不提供业务状态机

Runtime
    提供纯粹的运行机制
    负责事件泵、调度、生命周期机制和通用事件路由
    不知道 account、market、execution、intent 等业务含义
```

核心原则是：

> 将 Integration 从“按调用创建能力的函数集合”改造成“由 Integration 实现连接资源、由 System 持有 System-scoped ConnectionScope、由 Application Component 通过最小业务 Port 使用一个或多个连接资源的长期对象集合”。

这里必须分开三类责任：

```text
Integration
    实现技术连接和业务 Port adapter
    负责 vendor client、认证、心跳、底层 reconnect 和 payload 转换

Usecase Runtime
    持有业务会话状态
    负责订阅、checkpoint、业务重订阅、bootstrap、reconcile 和业务恢复
    通过连接选择/租约接口使用一个或多个 Connection

System
    持有 System-scoped ConnectionScope 和 Application Components
    管理整个系统实例的 start、stop、health 和资源生命周期
```

“Integration 实现 Connection”是本文的中心判断；Connection 的技术实现属于 Integration，Connection 的生命周期管理属于 System。业务模块所需的最小业务 Port 和连接选择能力仍然由消费方 Usecase 定义。业务 Application 不直接依赖 `IntegrationRegistry`、connector 或 vendor 类型。

整体依赖方向：

```text
surface / CLI
        ↓
System.application
        ↓
System
        ├── Usecase.application
        ├── Integration ConnectionScope
        └── pure Runtime

Usecase.application
        ↓
Usecase.domain

Integration adapters
        ↑ implements
Usecase.protocol

Integration connectors
        ↓
vendor SDK / REST / WebSocket / provider API
```

纯 runtime 不依赖任何业务模块；integration adapter 可以实现业务模块声明的 Protocol；System 是这些对象的组合和生命周期边界。

---

## 二、为什么需要重新定义 Integration

### 2.1 Integration 不是一个业务服务

当前 `infrastructure/integrations` 容易被理解成一个“交易服务”，但它实际上不拥有稳定的业务动作。

它不应该定义：

- 下单业务流程；
- 账户账本；
- 订单状态机；
- 市场订阅状态；
- 账户 reconcile 进度；
- 策略生命周期；
- 跨业务模块编排。

这些属于 account、market、execution、strategy 或 System。

Integration 的职责是技术性的：

```text
外部系统差异
    → 统一的业务端口
```

因此它更准确的名称是：

```text
Infrastructure Adapter Boundary
```

而不是：

```text
Business Integration Service
```

### 2.2 当前 Integration 的问题

迁移前 integration 同时承担了：

```text
connector resolver
service locator
raw gateway factory
adapter factory
connection cache
vendor payload translator
application facade
```

迁移前需要删除的旧入口包括：

```python
IntegrationGatewayProvider
provider.raw.broker_for_book(...)
provider.raw.order_execution_for_book(...)
default_broker_for_book(...)
order_execution_port_for_book(...)
```

这些接口的问题不是抽象太少，而是抽象方向不对：

```text
业务侧
    → integration provider
    → resolver
    → registry
    → connector
```

业务侧仍然需要知道 broker、book、credential、mode、raw gateway 等基础设施概念。Integration 只是把内部实现换了一个路径暴露出去，没有真正完成边界隔离。

### 2.3 Integration 的真正价值

新的 Integration application 应该以分类 Connection 为入口；每个 connection 由 Integration domain 持有连接基础信息和状态，由 adapter 完成三类转换：

```text
1. 输入转换
   application request → vendor request

2. 输出转换
   vendor response / event → domain/application result

3. 错误转换
   vendor exception → application/integration error
```

例如：

```python
class BinanceOrderExecutionAdapter(OrderExecutionPort):
    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        vendor_request = self._to_binance_request(request)
        response = self._client.create_order(vendor_request)
        return self._to_order_result(response)
```

如果 adapter 只是：

```python
return self.client.create_order(...)
```

那么它只是转发器，不是有价值的抽象。

---

## 三、System 是连接和业务组件的拥有者

### 3.1 System 的定位

`System` 表示一个已经组装并运行中的交易系统实例：

```text
TradingSystem
├── identity
├── mode
├── business components
├── integration resources
├── connection scope
├── runtime session
├── event routing
├── lifecycle
└── health / projection
```

它不是某一个业务 usecase，也不是纯 runtime。

System 负责把以下对象组织成一个运行实例：

```text
外部连接
    + integration adapters
    + account / market / execution / reference usecase
    + strategy
    + runtime session
```

### 3.2 System-scoped singleton

大多数连接确实不应该被每次调用重新创建。这里的 singleton 应理解为：

```text
一个 TradingSystem 实例内的长期单例
```

而不是进程级全局单例。

例如：

```text
live System A
    → Binance live integration

paper System B
    → Paper integration

backtest System C
    → Historical data integration
```

三个 System 可以各自拥有独立的资源、状态和连接，互不污染。

### 3.3 System 管理 Connection 生命周期

System 的生命周期应当控制 Connection 和 integration resources：

```text
System.start()
    → business components initialize
    → start ConnectionScope 中的 Connection resources
    → start business runtime adapters
    → start pure runtime session / event pump
    → system becomes running

System.stop()
    → stop business runtime components
    → stop pure runtime session / event pump
    → stop ConnectionScope 中的 Connection resources
    → system becomes stopped
```

System 是 ConnectionScope 的唯一生命周期 owner；Application Component 和业务 runtime 只能使用由 scope 注入的业务 Port，不能自行 `start`、`stop` 或替换连接。Connection 可以提供技术生命周期能力：

```python
start()
stop()
reconnect()
health()
```

但这些是技术连接资源的生命周期能力；是否启动、何时停止、如何汇总健康状态以及如何记录系统状态，由 System / ConnectionScope 协调。底层 transport reconnect 可以由 Connection 执行，但业务恢复不能由 Connection 决定。业务 runtime 只能请求重订阅、重新 bootstrap 或 reconcile。

### 3.4 当前代码的正确方向

当前 `TradingSystem` 已经存在几个正确的设计信号（字段名仍属于迁移期实现）：

```python
resources.connection_scope.start()
...
resources.connection_scope.stop()
```

并且 System 通过 `resources` 获取业务组件和运行资源。

当前实现已经改为强化分类 Connection application，不再扩大 `IntegrationGatewayProvider` 的公共 API；旧入口已从运行代码中移除。

建议资源模型逐步明确为：

```python
@dataclass(slots=True)
class SystemResources:
    connection_scope: ConnectionScope
    components: SystemComponents
```

其中：

```text
ConnectionScope
    当前 System 已经组装完成并持有的、可按 connection_id 访问的长期 Connection 集合
    负责 start、stop、health、transport reconnect 和连接替换

Integration resources
    由 Integration 实现的具体连接和 adapter
    只能在 composition 中注册到 ConnectionScope

SystemComponents
    当前 System 唯一持有的 AccountApplication、MarketApplication、
    ExecutionApplication、ReferenceApplication 等模块级 Application Component
```

两者都不是业务侧的按需解析服务。业务 runtime 和 System command/query dispatcher 应该从同一组 `SystemComponents` 取得业务入口；业务组件通过注入的分类 Connection 使用 ConnectionScope 中的一个或多个连接。

### 3.5 三种对象必须分开

“连接”在系统中不是一个单一概念，应明确分成三类对象：

```text
Connection resource
    外部技术连接
    由 Integration 实现，由 System 的 ConnectionScope 持有
    负责 HTTP/WebSocket、认证、心跳、transport reconnect 和 vendor 转换

Business connection port
    由 Usecase 定义的最小业务能力
    例如 MarketStreamSource、OrderExecutionPort
    不暴露 SDK，也不包含 System、ConnectionManager 或 resolver

Business Runtime Session
    业务连接会话
    由 Usecase 的 services/runtime 实现
    负责当前订阅、checkpoint、业务 resubscribe、bootstrap、reconcile 和恢复

System
    系统实例
    持有 ConnectionScope、Application Components 和 Business Runtime
    负责统一生命周期、健康状态和组件协调
```

例如市场断线时：

```text
MarketConnection
    处理 socket reconnect

MarketRuntimeService
    判断订阅是否仍然有效
    执行业务 resubscribe 或 reconcile

TradingSystem
    汇总健康状态
    决定系统是否进入 degraded / failed
```

Usecase Runtime 可以持有由 System 注入的业务 connection port，但不能持有具体 SDK client、ConnectionScope 或 resolver。

### 3.6 一个 System 内允许多个同类 Connection

System-scoped 不表示一个 System 只能有一个连接。一个 System 可以直接持有多个已经分类的连接对象：

```text
System
├── market_stream_connections
│   ├── binance-public-1
│   └── binance-public-2
├── market_request_connections
├── account_connections
├── execution_connections
└── reference_connections
```

每个连接都有唯一 `connection_id`，拥有独立的物理线路、认证/session、连接状态和健康状态。Application Component 接收对应类别的连接集合；它可以明确使用一个连接，也可以同时使用多个连接，不需要通用的 capability registry、selector、pool 或 lease 抽象。

例如：

```python
class MarketApplicationService:
    def __init__(
        self,
        *,
        stream_connections: Mapping[str, MarketStreamConnection],
        request_connections: Mapping[str, MarketRequestConnection],
    ) -> None:
        ...
```

连接由 System/Composition 创建并注入；业务 Application 不向 `TradingSystem` 动态创建连接，也不接收 raw connector。需要广播、分片或故障转移时，使用方直接基于连接集合和明确的业务配置实现，不把这些策略下沉成 Integration 的通用能力模型。

---

## 四、Integration 的目标模型

### 4.1 Integration 实现 Connection，Usecase 定义业务 Port

如果一个外部系统同时提供市场、账户、交易和参考数据能力，那么 Integration 可以为这些能力实现长期 Connection resource：

```text
BinanceIntegration
├── BinanceMarketConnection
├── BinanceAccountConnection
├── BinanceExecutionConnection
├── BinanceReferenceConnection
├── connection resources
└── health
```

也可以按 System 实际配置拆分为多个 participant。关键是：这些 Connection 在 System 初始化时确定，使用唯一 `connection_id` 注册到 ConnectionScope，并在生命周期内复用。Integration 不决定业务模块使用哪一个连接。

它们不是业务 API。业务模块只看到 Integration application 提供的分类 Connection，或自己定义的最小业务 Port：

```text
MarketStreamConnection / MarketRequestConnection
Account bootstrap / private account connection
Execution connection
Reference connection
```

业务模块不应该知道：

```text
BinanceIntegration
CCXT client
BinanceBroker
IntegrationResolver
IntegrationRegistry
```

### 4.2 从函数思维改成 Connection 思维

不推荐：

```python
provider.order_execution_port(book)
provider.market_feed(venue)
旧实现曾通过 `provider.raw.broker_for_book(book)` 获取 connector；当前应由 Integration application 在 System 组装阶段创建并返回分类 Connection。
```

这些 API 都表达了“现在创建或解析一个资源”，属于函数思维和 service locator 思维。

推荐：

```text
System 初始化时
    创建长期 Connection
    将 Connection 交给对应 Usecase Runtime
    创建 account / market / execution / reference usecase
    将业务组件、Connection 和 Runtime 一起交给 TradingSystem
```

之后 Usecase Runtime 只使用已经存在的、由 System 注入的分类连接对象：

```python
class MarketRuntimeService:
    def __init__(
        self,
        *,
        stream_connections: Mapping[str, MarketStreamConnection],
        request_connections: Mapping[str, MarketRequestConnection],
    ):
        self._stream_connections = stream_connections
        self._request_connections = request_connections

    def subscribe(self, request: SubscribeMarketRequest) -> None:
        connection = self._stream_connections[request.connection_id]
        self._subscribe_one(connection, request)
```

执行期间不再调用 resolver 或 provider。函数可以继续存在于 Integration 内部，用于 payload 解析、请求构造和 composition 阶段的一次性装配，但不能成为 Usecase 的长期依赖边界。连接创建和注册由 composition 决定。

### 4.3 Resolver、Registry 和 Raw Gateway 的位置

这些对象不是没有价值，而是应该限制可见范围：

```text
IntegrationRegistry
    integration 内部注册实现

IntegrationResolver
    integration 内部解析实现

Raw gateway
    integration adapter 内部或 System 初始化阶段使用

Concrete connector
    integration services 内部
```

它们不应该作为普通模块的公共业务依赖。

旧 `raw_gateways.py` 已删除；resolver、registry 和 raw connector 只在 Integration services 内部或 composition 的一次性装配阶段可见。

---

## 五、Usecase 的目标定位

### 5.1 Usecase 表达业务动作

Usecase 应回答：

> 系统要完成哪个业务动作或业务查询？

例如：

```text
Account
    bootstrap account
    reconcile account
    acquire trade authorization
    release trade authorization
    query account state

Market
    subscribe market
    unsubscribe market
    ingest market event
    query historical bars
    query current market state

Execution
    plan order
    submit order
    cancel order
    apply execution update
    query order state

Reference
    refresh catalog
    resolve market
    apply lifecycle event

Strategy
    handle signal
    emit trade intent
```

Usecase 负责业务状态变化和业务规则编排，不负责创建连接或运行整个进程。

### 5.2 Usecase 不是业务模块的垃圾桶

当前 `AccountService`、`MarketDataService`、`ExecutionService` 已经接近模块 facade，但内部包含了过多职责：

```text
业务 API
状态容器
projection
replay
simulation
runtime worker
streaming
跨模块 coordinator
```

它们可以暂时保留为 module facade，但实现应该逐步按 usecase 拆开：

```text
account/application/
├── bootstrap.py
├── reconcile.py
├── authorization.py
├── queries.py
└── facade.py

market/application/
├── subscribe.py
├── ingest.py
├── replay.py
├── history.py
└── queries.py

execution/application/
├── plan_order.py
├── submit_order.py
├── cancel_order.py
├── apply_update.py
└── queries.py
```

facade 只提供稳定的业务入口，不应该成为所有内部服务的重新导出点。

### 5.3 Usecase 的标准结构

每个业务模块保持：

```text
<module>/
├── application/
├── domain/
├── services/
└── protocol.py
```

职责如下：

```text
domain
    Entity、Value Object、Aggregate、业务规则、Domain Event

application
    Command、Query、Request、Result、Usecase Handler

services
    私有实现、内部协作者、状态 reducer、projection、业务 runtime adapter

protocol.py
    本业务模块真正需要的外部能力
```

### 5.4 技术 Connection 与业务 Connection 必须统一

目标模型不再额外引入 `MarketStreamGateway` 这一层。旧 gateway 已删除；`MarketStreamConnection` 本身就是 Integration 提供给 Market Application 的长期业务连接，也是 System 持有的连接资源：

```text
MarketStreamConnection
    一条独立的逻辑/物理流式线路
    支持多次 subscribe/unsubscribe
    持有自身连接级订阅状态
    可以查询远端订阅状态

MarketRequestConnection
    一条独立的逻辑请求线路
    支持多次 request/response
```

这两个 Protocol 是 Integration application 对外提供的分类连接契约；连接的身份、传输类型、生命周期状态和远端订阅快照属于 Integration domain。Integration application 返回隐藏 provider/broker/SDK 细节的实现。System 负责启动、停止、重连和健康汇总；业务 Application 使用连接的业务方法，不负责底层生命周期。

例如 execution 需要外部执行能力，因此由 execution 定义业务 Port：

```python
class OrderExecutionPort(Protocol):
    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        ...

    def cancel(self, request: OrderCancelRequest) -> OrderCancelResult:
        ...
```

Integration 的 Execution adapter 可以实现这个业务 Port，或者由 Integration 内部的 adapter 将某个 Connection resource 转换为这个 Port：

```python
class BinanceOrderExecutionConnection(OrderExecutionPort):
    ...
```

而不是由 Integration 定义一个宽泛的 `BrokerService` 再让 Usecase 依赖它。

对于持续事件流，`MarketStreamConnection` 直接负责订阅管理：

```python
class MarketStreamConnection(Protocol):
    async def subscribe(self, request: MarketSubscriptionRequest) -> MarketSubscription: ...
    async def unsubscribe(self, subscription_id: str) -> None: ...
    async def remote_subscriptions(self) -> RemoteSubscriptionSnapshot: ...
```

该契约放在 Integration 的 `application/market.py`，因为 Integration application 是连接的提供方；实现放在 Integration `services/` 中，状态模型放在 Integration `domain/` 中。多个连接直接通过 `Mapping[connection_id, MarketStreamConnection]` 注入，不再增加通用 Selector/Pool 层。

### 5.5 Usecase 不应暴露 vendor 参数

需要重点收敛以下形式：

```python
params: Mapping[str, object]
integration_options: Mapping[str, object]
raw: Mapping[str, object]
```

每个字段都应判断：

1. 它是业务概念还是 vendor 参数？
2. 能否变成 typed request 或业务 policy？
3. 能否只存在于 integration adapter 内部？
4. 能否由 System 初始化阶段完成转换？

Usecase 的公开输入输出应使用业务类型，不应暴露 SDK 类型、raw payload、数据库 record 或 connector 参数。

以下内容不得进入业务 Protocol：

```text
params / adapter_options / integration_options
    vendor 请求参数，应在 composition 或 integration adapter 内部完成绑定

raw Mapping
    vendor 响应或事件，应在 adapter 边界转换为业务结果/事件

具体 coordinator、service 或另一个业务模块的实现类
    应改成消费方拥有的最小 Port，或由 application orchestration 协调
```

特别是账户 bootstrap 不能把 execution coordinator 传入 integration adapter。Integration 只返回账户快照和外部订单事实；账户 application 再通过 execution application 自己拥有的最小 Port 完成订单导入或状态协调。这样可以避免 `account.protocol` 反向依赖 `execution.application` 的具体 coordinator。

### 5.6 模块级 Application Component

`runtime` 和 `system` 都可能调用业务模块的 application。如果两者分别直接调用不同的 usecase service，就会出现状态、依赖和生命周期不一致的问题：

```text
runtime → AccountBootstrapService
system  → AccountService
```

或者：

```text
runtime → 一个 ExecutionCoordinator
system  → 另一个 ExecutionService
```

因此，每个 bounded context 应提供一个模块级 Application Component：

```text
AccountApplication
MarketApplication
ExecutionApplication
ReferenceApplication
StrategyApplication
```

它是一个 System-scoped 实例，不是全系统大而全的 Service。

以 account 为例：

```text
account/application/
├── component.py
├── commands/
│   ├── bootstrap.py
│   ├── reconcile.py
│   └── authorization.py
├── queries/
│   └── accounts.py
└── service.py
```

对外只暴露稳定的业务入口：

```python
class AccountApplication:
    def bootstrap(self, request: BootstrapAccountRequest) -> BootstrapAccountResult:
        ...

    def reconcile(self, request: ReconcileAccountRequest) -> ReconcileAccountResult:
        ...

    def snapshot(self, request: AccountSnapshotQuery) -> AccountSnapshotResult:
        ...

    def authorize_trade(
        self,
        request: TradeAuthorizationRequest,
    ) -> TradeAuthorizationResult:
        ...
```

内部实现仍然可以拆分：

```python
class AccountApplicationService:
    def __init__(self, ...):
        self._bootstrap = BootstrapAccountUseCase(...)
        self._reconcile = ReconcileAccountUseCase(...)
        self._queries = AccountQueryService(...)

    def bootstrap(self, request):
        return self._bootstrap.execute(request)

    def reconcile(self, request):
        return self._reconcile.execute(request)
```

这里需要区分：

```text
统一对象实例
    ≠
大而全的公共 API
```

Component 统一实例，内部实现仍然按 command、query、domain service、projection 和 runtime adapter 拆分。

### 5.7 Runtime 和 System 必须共享同一个 Application Component

System 初始化时，为每个业务模块创建一次 Application Component，并将同一个实例注入 System 和业务 runtime：

```text
System 初始化
    → 创建 AccountApplication
    → 创建 MarketApplication
    → 创建 ExecutionApplication
    → 创建 ReferenceApplication
    → 放入 SystemComponents
    → 为每个 Component 注入消费方定义的分类 Connection
    → 注入对应业务 runtime
```

对象关系应当是：

```text
TradingSystem
├── components.account
├── components.market
├── components.execution
├── components.reference
└── runtime session

AccountRuntimeService
    └── 使用 components.account

SystemCommandDispatcher
    └── 使用 components.account
```

这样可以保证 runtime 和 system 使用相同的：

```text
业务状态
依赖对象
Connection 集合、业务 Port 和连接 policy
projection
生命周期
```

禁止 runtime 和 system 各自创建或寻找自己的 application service。

Application Component 不持有 `TradingSystem`、`ConnectionManager` 或 Integration Provider。它只持有 composition 注入的分类 Connection，例如：

```python
class MarketApplicationService:
    def __init__(
        self,
        *,
        stream_connections: Mapping[str, MarketStreamConnection],
        request_connections: Mapping[str, MarketRequestConnection],
        subscription_store: MarketSubscriptionStore,
    ) -> None:
        self._stream_connections = stream_connections
        self._request_connections = request_connections
        self._subscription_store = subscription_store
```

因此，“向 System 申请多连接”的准确含义是：System/Composition 在初始化时创建并注入多个分类 Connection。Application 通过 `connection_id` 使用这些已经存在的连接，不直接调用 `system.acquire_connection()`，也不接收 raw Connection。

### 5.8 Runtime 和 System 的调用职责

两者可以调用同一个 Application Component，但调用的入口类型不同。

Runtime 主要调用业务动作和运行时事件入口：

```text
MarketRuntime
    → market.ingest(event)

AccountRuntime
    → account.apply_private_event(event)
    → account.reconcile(request)

ExecutionRuntime
    → execution.apply_update(update)
```

System 主要调用系统控制和业务查询入口：

```text
System
    → account.snapshot(query)
    → account.authorize_trade(request)
    → market.subscriptions(query)
    → execution.current_orders(query)
```

System 和 runtime 都只能依赖 Application Component 的稳定 API，不能直接导入：

```text
BootstrapAccountUseCase
AccountProjectionService
ExecutionCoordinator
MarketSubscriptionService
```

### 5.9 Component API 的三类入口

每个 Application Component 的公开 API 建议分成三类：

```text
Commands
    产生业务状态变化

Queries
    读取业务状态

Runtime inputs
    接收外部或系统事件
```

例如：

```python
class ExecutionApplication:
    # Commands
    def plan_order(self, request: PlanOrderRequest) -> PlanOrderResult: ...
    def submit_order(self, request: SubmitOrderRequest) -> SubmitOrderResult: ...
    def cancel_order(self, request: CancelOrderRequest) -> CancelOrderResult: ...

    # Queries
    def current_view(self, request: CurrentExecutionQuery) -> ExecutionView: ...

    # Runtime inputs
    def apply_update(self, update: ExecutionUpdate) -> None: ...
```

`Runtime inputs` 是 System/Runtime 认可的业务事件入口，不表示 application 需要暴露内部 reducer 或 projection service。

对于多连接事件，Runtime input 必须保留来源上下文：

```python
@dataclass(frozen=True, slots=True)
class ConnectionEventContext:
    connection_id: str
    sequence: int | None = None
    observed_at: datetime | None = None


class MarketApplication:
    def ingest(
        self,
        event: MarketEvent,
        *,
        context: ConnectionEventContext,
    ) -> IngestMarketResult: ...
```

来源上下文不是 vendor raw payload，而是系统级事件元数据。它用于 checkpoint、排序、去重、故障转移和审计，不得在 adapter 中丢弃。

---

## 六、Runtime 必须保持纯净

### 6.1 纯 Runtime 的职责

`application/support/runtime` 只负责通用运行机制：

```text
event envelope
event pump
event loop
generic scheduler
dispatch mechanism
session
connection scope lifecycle mechanism（不持有具体 vendor connection）
start / stop mechanism
health mechanism
projection execution mechanism
mode execution mechanism
```

纯 runtime 不应知道：

```text
account
market
execution
intent
order
reference
strategy
Binance
CCXT
```

也不应出现业务命名的通用 processor：

```text
AccountProcessor
MarketProcessor
ExecutionWorker
OrderProjection
IntentProcessor
```

纯 runtime 只处理抽象的运行组件和事件。它可以驱动由 System 注入的 ConnectionScope 生命周期协议，但不能选择 venue、credential 或 connection_id，也不能创建具体连接：

```python
class RuntimeProcessor(Protocol):
    def process(self, envelope: RuntimeEnvelope) -> RuntimeSteps:
        ...
```

### 6.2 业务 runtime service 不应迁入纯 Runtime

以下目录属于各自业务模块，不应迁入 `application/support/runtime`：

```text
application/usecases/account/services/runtime/
application/usecases/market/services/runtime/
application/usecases/execution/services/runtime/
application/usecases/intent/services/runtime/
```

它们虽然包含 runtime 逻辑，但本质上是业务模块对通用运行机制的适配实现。

例如：

```text
account/services/runtime/
    消费 account private stream
    转换 account 业务事件
    更新 account checkpoint
    调用 account application
    更新 account projection
```

这些职责仍然属于 account，而不是纯 runtime。

### 6.3 业务 Runtime Adapter 的依赖

业务 runtime service 可以依赖：

```text
本业务 application
本业务 domain
本业务 protocol
纯 runtime protocol
```

但纯 runtime 不得反向依赖这些业务模块：

```text
纯 runtime → account       错误
纯 runtime → market        错误
纯 runtime → execution     错误

account runtime → pure runtime   正确
market runtime → pure runtime    正确
```

---

## 七、System、Usecase、Integration、Runtime 的完整协作流程

### 7.1 System 初始化

```text
配置
    ↓
System 初始化
    ↓
创建 System-scoped connection scope
    ↓
根据配置创建一个或多个长期 Connection resource
    ↓
以唯一 connection_id 注册到 ConnectionScope
    ↓
将分类 Connection 注入对应 Usecase
    ↓
将分类 Connection 注入 Application Component 和对应 Usecase Runtime
    ↓
创建 account / market / execution / reference usecase
    ↓
创建业务 runtime adapters
    ↓
创建纯 runtime session
    ↓
形成 TradingSystem
```

这里可以有一次性的 System 组装逻辑，但不应把它设计成业务侧调用的 factory API。组装是 System 建立过程的一部分，不是运行期间的服务调用。重点不是引入一个 `IntegrationFactory`，而是让 System 在建立时得到长期 Connection，并在整个生命周期内持有它。

### 7.2 System 启动

```text
TradingSystem.start()
    → start connection scope
    → start Connection resources
    → initialize business runtime adapters
    → initialize pure runtime session
    → publish system running state
```

### 7.3 市场数据事件

```text
external market connector
    → integration market adapter
    → MarketEvent
    → pure runtime event pump
    → market runtime adapter
    → MarketIngestionUseCase
    → market domain state
    → business event / projection
```

### 7.4 订单提交

```text
strategy
    → SubmitOrderUseCase
    → execution domain validation
    → ExecutionConnection
    → Integration ExecutionConnection adapter
    → external broker / exchange
    → OrderSubmissionResult
    → execution domain state
    → projection / event
```

执行过程中，usecase 不创建连接，integration 不决定订单业务状态，runtime 不解析订单业务含义。

### 7.5 System 停止

```text
TradingSystem.stop()
    → stop business runtime adapters
    → stop pure runtime
    → stop Connection resources
    → stop connection scope
    → publish system stopped state
```

### 7.6 多 Connection 的完整协作流程

以同一个 System 内两个 Binance market connection 为例：

```text
配置
    ↓
Composition 创建 binance-public-1、binance-public-2
    ↓
注册到 System-owned ConnectionScope
    ↓
注入 MarketApplication 和 MarketRuntimeAdapter
    ↓
MarketApplication 根据业务请求使用一个或多个已注入的 connection_id
    ↓
MarketStreamConnection 在自己的物理线路上建立多个 subscription
    ↓
MarketRuntime 为每个 connection_id 建立独立 subscription/checkpoint
    ↓
各连接事件携带 connection_id 进入统一 Runtime event pump
    ↓
MarketApplication.ingest(event, context=ConnectionEventContext(...))
```

这里有四个必须分开的概念：

```text
Connection resource
    System 生命周期内的实际技术连接

Connection selection
    Application 根据 typed policy 决定使用哪些 connection_id

Business subscription
    MarketApplication/Runtime 在某个连接上的业务订阅状态

Event provenance
    每个事件来自哪个 connection_id、序号和观测时间
```

多连接不是把两个事件流简单合并成一个匿名队列。至少应定义以下规则：

- `connection_id` 是 System 内唯一稳定标识，不能使用 vendor 名称作为唯一标识；
- 同一 `connection_id` 的事件保持顺序；跨连接事件只在业务明确需要时排序；
- checkpoint 按 `connection_id + subscription key` 保存；
- broadcast 产生重复数据时使用业务去重键，不能仅按到达时间去重；
- shard 迁移时先停止旧连接上的业务订阅，再在新连接 bootstrap/reconcile，迁移窗口由业务 runtime 管理；
- failover 时底层连接可由 ConnectionScope reconnect，但业务订阅恢复必须由 MarketRuntime 执行；
- 候选连接全部不可用时，Application 返回明确的 `NoAvailableConnection`/degraded 结果，不在运行期间偷偷创建新 connector；
- System health 同时报告连接级健康和业务订阅级健康，不能只报告整个 scope 的一个布尔值。

### 7.7 连接创建、持有和释放

Application 不在运行期间创建连接。连接由 Integration application 在 composition 阶段组装，由 System 持有：

```text
composition
    创建并注册 Connection resources

ConnectionScope
    持有资源、生命周期和健康状态

Application/Runtime
    使用已注入的分类 Connection
    创建或取消连接上的业务 subscription

System.stop()
    统一释放所有业务 subscription 和 Connection resources
```

业务 `unsubscribe` 只取消连接上的订阅，不关闭底层 Connection。底层 Connection 只由 System 生命周期关闭。

---

## 八、推荐目录方向

### 8.1 System

```text
application/support/system/
├── application/
│   ├── control/
│   ├── lifecycle.py
│   ├── commands.py
│   └── queries.py
├── domain/
│   ├── components.py
│   ├── identity.py
│   └── lifecycle.py
├── services/
│   ├── system.py
│   ├── session.py
│   ├── component_registry.py
│   ├── dispatcher.py
│   └── health.py
└── protocol.py
```

System 负责持有和协调，不实现 account、market、execution 的业务规则。

### 8.2 Pure Runtime

```text
application/support/runtime/
├── application/
├── domain/
│   ├── events.py
│   ├── connections.py
│   ├── modes.py
│   └── session.py
├── services/
│   ├── engine.py
│   ├── pump.py
│   ├── scheduler.py
│   └── lifecycle.py
└── protocol.py
```

这里不得出现业务模块专属的 processor 或 adapter。

### 8.3 Business Usecase

```text
application/usecases/execution/
├── application/
│   ├── plan_order.py
│   ├── submit_order.py
│   ├── cancel_order.py
│   ├── apply_update.py
│   └── queries.py
├── domain/
│   ├── state.py
│   ├── order.py
│   └── events.py
├── services/
│   └── runtime/
│       ├── live.py
│       ├── paper.py
│       └── backtest.py
└── protocol.py
```

业务 runtime adapter 留在业务模块内部。

### 8.4 Integration

```text
infrastructure/integrations/
├── domain/
│   ├── participants.py
│   ├── connections.py
│   └── bindings.py
├── application/
│   ├── account.py
│   ├── execution.py
│   ├── integration.py
│   ├── market.py
│   └── reference.py
├── services/
│   ├── connectors/
│   ├── adapters/
│   ├── drivers/
│   ├── payloads/
│   ├── resolver.py
│   └── registry.py
└── domain state + composition-owned connection registration
```

Connection manager/scope 属于 `application/support/runtime` 的 System 资源，而不是 Integration 的公共 service。对业务模块公开的只能是 Integration application 提供的分类 Connection 契约，以及业务模块自身定义的最小业务 Port。

---

## 九、当前代码的迁移原则

### 第一阶段：切换到分类 Connection application

不要继续新增或调用：

```text
Integration application
default_xxx()
xxx_for_book()
provider.raw.xxx()（旧入口，已删除）
resolver.xxx()
```

这些入口只允许在迁移期间保留，不能成为新的业务或 composition 依赖；新代码必须从 Integration application 组装分类 Connection。

### 第二阶段：明确 System 资源集合

将 System 的资源分成：

```text
ConnectionScope
    integration Connection objects
business components
business runtime sessions
pure runtime session
```

不要让业务 usecase 自己持有全局 `ConnectionManager`，也不要让 runtime session 自己寻找 integration。业务 Usecase Runtime 可以持有被 System 注入的分类 Connection；连接集合和生命周期由 `resources.connection_scope` 统一管理。

### 第三阶段：把长期 Connection 注入业务 Runtime

例如：

```text
System
    → 持有 OrderExecutionConnection
    → 注入 ExecutionService 或 ExecutionRuntimeService
    → Usecase Runtime 在生命周期内持续使用 Connection
```

而不是：

```text
ExecutionService
    → 注入的 ExecutionConnection
    → submit/cancel
```

对于多连接，迁移目标不是把一个 provider 换成一个更大的 provider，而是：

```text
System composition
    → 创建多个带唯一 connection_id 的 Connection
    → 注册到 ConnectionScope
    → 按 market/account/execution/reference 分类
    → 注入同一个 Application Component 和对应 Runtime
```

业务模块应先支持显式 `connection_id` 和分类 Connection 集合。broadcast、shard、failover 先由具体业务模块基于连接集合实现；迁移期间不得通过全局 resolver 隐藏连接创建。

### 第四阶段：收窄 application Protocol

优先清理：

```text
vendor params
integration_options
raw Mapping
SDK object
具体 coordinator
```

把它们替换成：

```text
业务 Request
业务 Result
业务 Policy
业务 Event
最小 Protocol
```

### 第五阶段：保留业务 runtime，但避免污染纯 runtime

不把以下目录迁入 `support/runtime`：

```text
account/services/runtime
market/services/runtime
execution/services/runtime
intent/services/runtime
```

它们应继续作为业务模块内部实现，并只依赖纯 runtime 的通用协议。

### 第六阶段：补齐多连接的一致性语义

迁移完成后的设计和测试验收：

- 同一 System 注册两个同类连接，启动/停止/健康检查互不覆盖；
- Application 可以显式选择一个连接或同时选择多个连接；
- 每个连接的订阅、事件来源和 checkpoint 独立保存；
- 一个连接断开时不会错误地清空其它连接的状态；
- failover、broadcast、shard 分别有重复、排序、迁移和 reconcile 测试；
- Application 不会在运行期间隐式创建 connector；
- System stop 能释放业务订阅并最终关闭全部 Connection resource；
- 业务模块只依赖注入的分类 Connection 契约，不依赖 System 或 Integration 内部实现类。

---

## 十、代码审查标准

### Integration 审查

- 是否实现了长期 Connection resource，并由 System ConnectionScope 持有？
- 是否支持同一 System 内多个同类 Connection，并为每个连接分配稳定 connection_id？
- integration 是否在做 payload、request、error 转换？
- 是否仍然暴露 raw connector？
- 是否仍然需要业务侧调用 resolver？
- 是否创建了按调用发生的连接？
- 是否把技术连接生命周期交给 System/ConnectionScope？
- 是否把业务 Port 与 start/stop/reconnect 生命周期协议分离？
- 是否把业务订阅、checkpoint 和 reconcile 错误地放进 Connection？
- adapter 是否实现消费方定义的 Protocol？
- 是否把 vendor 参数泄漏进 application？
- 多连接事件是否携带 connection_id、序号和观测时间？
- checkpoint、订阅、health 是否按连接隔离？

### Usecase 审查

- API 是否表达业务动作，而不是 connector 动作？
- 是否使用业务 Request/Result？
- 是否直接依赖 infrastructure？
- 是否创建连接或调用 resolver？
- 是否把业务状态放在 Domain 或明确的业务 application service 中？
- 是否通过其他业务模块 application API 或最小 Protocol 协作？
- 多连接是否通过消费方定义的分类 Connection 集合，而不是 System、resolver 或 provider？
- 是否把 runtime loop 混入业务 usecase？

### Runtime 审查

- 是否包含 account、market、execution 等业务判断？
- 是否出现业务专属 processor？
- 是否依赖 integration connector？
- 是否只使用通用 envelope、scheduler、session、lifecycle？
- 是否由 System 驱动，而不是自行发现业务组件？
- 是否不负责连接选择和具体连接创建？

### System 审查

- 是否持有 System-scoped 的 Connection？
- 是否持有 ConnectionScope，而不是让业务组件直接持有 ConnectionManager？
- 是否允许一个 System 同时拥有多个 market/account/execution Connection？
- 是否将业务会话状态交给对应 Usecase Runtime？
- 是否统一管理 start、stop、reconnect、health？
- 是否只负责协调而不实现业务规则？
- 是否将业务组件作为已组装对象注入？
- 是否允许 live、paper、backtest 使用同一运行模型？
- 是否明确三种模式的数据源、时钟、执行和连接差异？

---

## 十一、参考设计思想

DDD 的核心分层要求 Domain 专注于业务概念、业务状态和业务规则，Application Layer 定义系统要完成的任务并协调领域对象，Infrastructure 负责外部技术和持久化细节。[Eric Evans DDD Reference](https://www.domainlanguage.com/ddd/reference/)、[Microsoft DDD architecture guidance](https://learn.microsoft.com/en-us/dotnet/architecture/microservices/microservice-ddd-cqrs-patterns/ddd-oriented-microservice)

成熟交易系统也普遍将运行引擎、业务能力和外部适配器分开：NautilusTrader 使用统一的事件驱动引擎和模块化 adapters；QuantConnect Lean 将数据、交易、实时事件和算法处理拆分为不同 handler；Hummingbot 将各交易所 REST/WebSocket 能力封装为 connector。[NautilusTrader](https://github.com/nautechsystems/nautilus_trader)、[QuantConnect Lean](https://github.com/QuantConnect/Lean)、[Hummingbot](https://github.com/hummingbot/hummingbot)

本文不建议机械复制这些项目的目录，而是吸收其中共同的职责边界：

```text
System / Engine
    负责运行和生命周期

Business / Usecase
    负责业务能力和业务状态

Integration / Adapter
    负责外部系统差异
```

---

## 十二、最终判断标准

以后判断一个类应该放在哪里，可以先问五个问题：

### 它是否在表达业务规则？

是：放在业务 Domain 或业务 Application。

### 它是否在表达一个业务动作？

是：放在 Usecase Application。

### 它是否在处理外部 SDK、网络协议或 vendor payload？

是：放在 Integration Adapter/Connector。

### 它是否在控制运行循环、连接生命周期或系统实例？

是：放在 System 或纯 Runtime。

### 它是否在选择、租用或管理多个连接？

如果是在管理底层资源生命周期：放在 System/ConnectionScope；如果是在表达业务侧的连接选择、分片、广播或故障转移策略：放在消费方 Usecase Application，并基于注入的分类 Connection 集合实现。

最终可以浓缩为：

> Usecase 定义系统要做什么，并可以通过注入的分类 Connection 使用一个或多个业务连接；Integration application 提供并隐藏外部系统连接细节；System 持有 ConnectionScope 并管理所有连接生命周期；Runtime 只负责让系统运行起来，不创建连接。
