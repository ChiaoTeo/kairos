# Usecase 与 Integration 边界收敛设计

本文修正并细化 `docs/composition-convergence-design.md` 中关于 usecase Protocol 和 Integration adapter 的设计。

核心结论是：

> Integration 面向连接和外部系统；usecase 面向业务服务；composition 负责把两者组装起来。

usecase 不应通过大量 Protocol 描述交易所连接、市场流、账户私有流或外部订单接口。此类能力应由 Integration application 通过 connection-scoped typed capabilities 提供，usecase 通过内部 service 使用这些能力。

## 1. 当前设计的问题

当前系统存在以下结构性问题：

```text
usecase.protocol
        ↑
Integration service 直接实现多个 usecase Protocol
```

例如 Binance integration 同时依赖 account、execution、market、reference 的协议和 DTO。一个 physical connection 通过 `Any`、`__getattr__` 和多个业务协议向外提供能力。

这带来几个问题：

- Integration 需要知道多个业务模块的内部模型；
- Integration service 直接返回业务 DTO；
- Integration service 接收业务 coordinator 或 runtime service；
- account、execution、market 的边界混在一个外部连接中；
- usecase Protocol 实际上退化成外部 connector 的代理接口；
- composition 失去跨模块组装的意义；
- runtime 和业务模块容易穿透到 vendor service。

一个明确的问题示例是账户 bootstrap：Integration 不应接收 execution coordinator，并在解析外部订单时直接改变 execution 状态。Integration 只能提供外部账户快照和外部订单事实，业务状态如何变化应由 account/execution application 决定。

## 2. 目标架构

目标依赖关系：

```text
Integration
    面向 connection / route / transport / credential
    提供 connection-scoped typed capabilities

Composition
    选择具体 connection implementation
    创建 usecase runtime service
    将 connection capabilities 注入 usecase service

Usecase
    负责业务规则、业务状态和业务 application API
    不负责连接、vendor payload 和 transport

System / Runtime
    只消费已经组装好的业务运行组件
```

目标调用图：

```text
配置
  ↓
composition
  ├── integration.application.connect(spec)
  ├── build_market_runtime(access)
  ├── build_account_runtime(access)
  ├── build_execution_runtime(access)
  └── TradingSystem
          ↓
      system / runtime
```

依赖方向：

```text
composition
  -> integration.application
  -> usecase.application
  -> usecase.application.runtime

usecase.application/runtime
  -> integration.application connection capabilities
  -> own domain
  -> own internal services

usecase.services
  -> own domain
  -> own application data

integration.services
  -> integration domain
  -> vendor SDK / transport
```

usecase 可以依赖 Integration 的 application API，但不能依赖 Integration 的 `services`、connector、SDK 或 raw payload。

## 3. 三层能力模型

### 3.1 Integration connection capabilities

Integration application 提供连接范围内的、与 vendor 无关的 typed connection capabilities：

```text
ConnectionCapabilities
    public market capability
    private account capability
    request / execution capability
    reference capability
```

这些 capability 是一个 connection 的组成部分，表达外部连接能提供什么，不表达 account、execution 或 market 的完整业务流程。它们不应被建模成 Integration 下的业务 service，也不按 usecase 拆成 Integration 顶层模块。

例如：

```python
class PrivateAccountCapability:
    def snapshot(self, request: AccountSnapshotRequest) -> AccountSnapshotData:
        ...

    def open_orders(self, request: OpenOrdersRequest) -> tuple[ExternalOrder, ...]:
        ...

    def user_events(self) -> AsyncIterator[ExternalAccountEvent]:
        ...
```

Connection capability 不应包含：

- SDK client；
- vendor params；
- raw payload；
- usecase coordinator；
- domain state mutation；
- `Any` 加 `__getattr__` 的动态代理。

### 3.2 Usecase service

Usecase service 负责把外部事实应用到业务模型：

```python
class AccountRuntimeService:
    def refresh(self, request: RefreshAccountRequest) -> AccountSnapshot:
        ...

    async def events(self) -> AsyncIterator[AccountEvent]:
        ...
```

Account service 可以调用 `PrivateAccountAccess`，但它负责：

- 账户上下文和账户路由；
- 快照应用；
- 订单导入；
- 账户状态变化；
- 账户业务事件生成。

Execution service 负责：

- 订单业务校验；
- 账户交易权限；
- 订单状态；
- 外部提交结果应用；
- 取消和失败处理。

Integration 只负责把外部请求发出去，并返回 typed external result。

### 3.3 Composition adapter

如果 connection capability 和 usecase service 的输入不完全一致，由 composition 完成绑定：

```text
connection capability
        ↓
composition adapter
        ↓
usecase runtime service
```

adapter 可以实现 usecase 内部需要的最小接口，但它属于 composition，不属于 Integration。

这样可以避免 Integration 直接导入多个 usecase 的 Protocol，也避免 usecase 直接依赖具体 vendor service。

## 4. Usecase Protocol 的新规则

### 4.1 连接相关 Protocol 退出 usecase

以下类型不应继续作为 usecase 的公共 Protocol：

- 外部账户 bootstrap gateway；
- 私有账户 stream gateway；
- 外部账户 payload translator；
- 外部 market stream connection；
- 外部历史 market data client；
- 外部 order execution port；
- 外部 reference catalog source。

它们应迁移为 Integration application 的 connection capability，或由 composition adapter 包装；不应继续作为 Integration 里的 account/execution/market 业务 service。

### 4.2 Usecase 内部行为回归 service

以下类型应该改为 usecase 内部 service、value object 或普通 Callable：

- `AccountCatalog`；
- `ExecutionAccountRoute`；
- `ExecutionIntentContext`；
- `ExecutionViewSource`；
- `MarketFeedResolver`；
- `MarketSubscriptionProvider`；
- `AccountTradeLock`；
- `AccountEventFactory`；
- `SymbolResolver`；
- `RowWriter`；
- `ReferenceViewRegistry`；
- `ReferenceViewStore`。

如果一个 Protocol 只有一个实现，且不需要外部替换，应直接使用具体 service。

### 4.3 仍然保留的 Protocol

以下 Protocol 仍然有合理价值：

- `Strategy`：系统对策略插件的扩展契约；
- `IntentJournalPort`：如果 intent journal 仍有真实的存储替换需求；
- Integration 基础设施的 `CredentialValueReader`、`MillisecondClock`；
- Persistence boundary 的 state store、dataset store；
- runtime 内部的 `ConnectionManager`、`RuntimeEventLine`、`RuntimeProjector`；
- domain 中真正存在多个实现的结构性契约。

这些 Protocol 不应被误认为是 usecase 与 Integration 的统一连接入口。

## 5. 具体模块调整

### 5.1 Account

当前 `account/protocol.py` 中的：

```text
AccountBootstrapGateway
PrivateAccountStreamGateway
PrivateAccountStreamPayloadTranslator
```

应迁移为 Integration account access 或 composition adapter。

Account application 保留：

```text
AccountSnapshot
AccountState
AccountRuntimeService
AccountApplication
```

账户 bootstrap 的流程应变为：

```text
PrivateAccountAccess
  -> 外部快照 / 外部订单
  -> AccountRuntimeService
  -> AccountState / AccountEvent
```

Integration 不再接收 execution coordinator，也不直接应用 execution update。

### 5.2 Execution

当前 `execution/protocol.py` 中的订单 DTO 应移动到：

```text
execution/application/requests.py
execution/application/results.py
```

外部下单能力迁移为 Integration 的 `ExecutionAccess`：

```text
ExecutionAccess
  -> submit external order
  -> cancel external order
  -> return external execution result
```

Execution application 负责把外部结果转成订单状态和执行事件。

### 5.3 Market

当前 `market/protocol.py` 中的连接和外部数据访问迁移到 Integration connection capability；不再保留 market-owned connector protocol：

```text
PublicMarketAccess
ConnectionRemoteSubscription
ConnectionCapabilities
```

Market application 保留：

- 订阅业务语义；
- market event ingestion；
- replay；
- projection；
- market view。

Market 不应直接操作 vendor channel、CCXT client 或 raw event。

### 5.4 Reference

`ReferenceCatalogSource` 如果表示外部目录来源，应迁移为 Integration connection capability。

`ReferenceStore` 属于持久化边界，可以暂时保留为 reference application 的 store port；它不是 connection 能力，不应和 Integration connection capabilities 混为一谈。

## 6. Integration application 的目标结构

Integration application 应以 connection 和 assembly 为中心，不应按 usecase 拆成 `market_access.py`、`account_access.py`、`execution_access.py`、`reference_access.py`。

建议结构为：

```text
infrastructure/integrations/application/
├── connections.py
└── assembly.py
```

连接 assembly 只负责：

```python
assembly = integration_application.connect(spec)

assembly.connection
assembly.capabilities.public_market
assembly.capabilities.private_account
assembly.capabilities.execution
assembly.capabilities.reference
```

`connections.py` 中的 `ConnectionSpec`、`IntegrationConnection`、`ConnectionCapabilities` 和 typed capability 类型应有明确的方法和返回类型，不应通过 `implementation` 和 `__getattr__` 继续暴露内部服务。

物理连接可以共享底层资源，但连接能力面必须明确拆开；这不等于把 Integration 拆成多个业务模块：

```text
one Binance Spot connection
    └── ConnectionCapabilities
        ├── public market capability
        ├── private account capability
        ├── request / execution capability
        └── reference capability
```

共享底层资源不等于共享业务 service；连接能力也不应把 account、execution、market 的业务状态机合并到 Integration。

## 7. Composition 的新职责

Composition 不再负责实现所有 usecase Protocol，而负责：

1. 读取已解析的运行配置；
2. 创建 `IntegrationConnectionSpec`；
3. 调用 Integration application 获取 connection capabilities；
4. 调用 usecase `application/runtime.py` 创建业务 runtime service；
5. 创建必要的 composition adapter；
6. 组装 `RuntimeComponents` 和 `TradingSystem`。

示意：

```python
connection = integration.connect(spec)

market = build_market_runtime(
    access=connection.public_market,
)

account = build_account_runtime(
    access=connection.private_account,
)

execution = build_execution_runtime(
    access=connection.execution,
)

return TradingSystem(
    TradingRuntimeResources(
        market=market,
        account=account,
        execution=execution,
    )
)
```

Composition 可以依赖 Integration application 和 usecase application，但不应依赖任意模块的 `services`。

## 8. 迁移顺序

### 阶段一：冻结错误边界

停止新增：

- Integration service 直接实现多个 usecase Protocol；
- Integration 接收业务 coordinator；
- Integration 返回 raw payload；
- `Any` / `__getattr__` 动态 access；
- usecase Protocol 中直接暴露 vendor params。

### 阶段二：先迁移 account 和 execution

这两个模块耦合最深，优先处理：

1. 将 Binance account/execution 方法整理为 connection-scoped typed capabilities；
2. 移除 `bootstrap(..., coordinator)`；
3. 将外部订单导入改由 account/execution application 处理；
4. 将订单 DTO 从 `protocol.py` 移到 application requests/results；
5. 让 composition 创建 account/execution runtime service。

### 阶段三：迁移 market

1. 拆分 public market、stream、history、dataset access；
2. 将 vendor channel 和 payload translator 留在 Integration；
3. 将 market subscription、ingestion、replay 留在 Market；
4. 删除 usecase 对 connector-style Protocol 的直接依赖。

### 阶段四：迁移 reference

1. 将外部 catalog provider 迁移到 Integration reference access；
2. 保留 reference store 作为持久化边界；
3. 将 reference application 只保留目录生命周期和查询语义。

### 阶段五：清理 Protocol

1. 删除不再使用的 usecase Protocol；
2. 将内部 Protocol 移到实际消费者旁边；
3. 删除空的 composition、launch、runtime、system protocol.py；
4. 清理跨模块 `services` import；
5. 清理通过 protocol 文件导入 DTO 的调用方。

## 9. 验收标准

### Integration

- 不直接依赖 account/execution/market 的内部 service；
- 不接收业务 coordinator；
- 不修改业务状态；
- 不返回 vendor payload；
- 不使用 `Any` / `__getattr__` 作为 public access；
- 一个物理 connection 可以提供多个明确的 typed capabilities；
- Integration application 仍然以 connection/assembly 为中心，而不是以 usecase 为中心。

### Usecase

- 不直接创建 connector 或 SDK client；
- 不知道 transport、listen key、vendor channel 等细节；
- 业务流程由自己的 application/service 负责；
- 只有真实需要替换的外部边界才保留 Protocol；
- application DTO 与 Protocol 分离。

### Composition

- 负责模式选择和依赖组装；
- 负责跨模块 adapter；
- 不负责运行时业务协调；
- 不直接调用 concrete integration service；
- 不让其它模块通过 composition service 反向访问资源。

### Runtime/System

- 只消费组装好的 runtime service/component；
- 不判断具体 vendor 或 connector；
- 不区分 `LiveAccountService`、`PaperAccountService` 等内部实现；
- 不在运行期间重新选择或创建外部连接。

## 10. 最终判断

本次重构的目标不是让 Integration 实现更多 usecase Protocol，而是让这些 Protocol 逐步退出 usecase 与 Integration 的交互边界。

最终应形成：

```text
Integration connection/capability
    表达外部系统事实和连接能力

Usecase service
    表达业务状态和业务行为

Composition
    完成两者之间的装配和适配

System / Runtime
    使用统一的运行组件
```

如果某个 usecase 的 `protocol.py` 只是在描述“如何调用交易所”，它大概率是错误的抽象，应迁移为 Integration connection capability，或被 composition adapter 吸收。
# Superseded design note

This historical convergence draft predates the single-link Integration model.
The current contract is documented in
[`docs/integration-capability-lifecycle.md`](integration-capability-lifecycle.md):
one `ConnectionSpec` creates one permission-scoped HTTP/WebSocket Connection,
and business Protocols are implemented directly by that connection. The
`Access`/`ConnectionCapabilities`/multi-route assembly described below is no
longer the active design.
