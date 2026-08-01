# Integration Application Layer

本文定义 `kairospy/infrastructure/integrations/application/` 的定位。它是 integration bounded context 内部的 application layer，负责把外部接入能力组装成 KairosPy 上层 application 需要的端口实现。

相关边界：

- 总体架构：`docs/architecture-boundaries.md`
- application contract 和 raw payload 规则：`docs/application-ports-and-domain-boundaries.md`
- integration bounded context：`docs/integration-boundaries.md`
- runtime composition：`docs/runtime-composition-and-protocols.md`

## 核心结论

Integration 不是只向上层暴露 connector。Integration 应该通过自己的 application layer，按能力提供上层所需的抽象实现。

目标形态：

```text
kairospy/application/usecases/<area>
  defines application-facing Port / Request / Result

kairospy/infrastructure/integrations/application/<capability>
  resolves external participant/product/capability
  builds connector/gateway/translator
  returns an implementation of the application-facing Port

kairospy/infrastructure/integrations/adapters
  adapts connector/gateway output into the application-facing Port
```

也就是说，`integration/application/order.py` 可以提供 `OrderExecutionPort` 的实现，但 `OrderExecutionPort` 的定义权仍属于 `kairospy/application/usecases/execution`。

## 推荐目录

```text
kairospy/infrastructure/integrations/
  domain/
    participants.py
    capabilities.py
    bindings.py
    credentials.py
    endpoints.py
    policies.py
    health.py
    errors.py

  application/
    order.py
    market.py
    account.py
    reference.py
    registry.py
    factories.py
    credentials.py
    health.py

  adapters/
    order_execution.py
    market_stream.py
    account_snapshot.py
    reference_catalog.py

  connectors/
    exchange/
    broker/
    provider/

  drivers/
  payloads/
  protocols.py
```

当前 `integrations/services/` 可以视为这个 application layer 的过渡形态。新代码优先按 `integrations/application/<capability>.py` 组织；旧 `services/resolver.py`、`services/registry.py` 后续应迁移或拆分到 `application/registry.py`、`application/factories.py` 和各 capability service。

## 依赖方向

允许：

```text
application/usecases/execution
  -> core
  -> local OrderExecutionPort

infrastructure/integrations/application/order
  -> application/usecases/execution.OrderExecutionPort
  -> integrations/domain
  -> integrations/adapters/order_execution
  -> integrations/connectors

application/support/launch/composition
  -> infrastructure/integrations/application/order
```

禁止：

```text
application/usecases/execution
  -> infrastructure/integrations/application/order

application/usecases/execution
  -> infrastructure/integrations/connectors/broker/binance

application/usecases/execution
  -> infrastructure/integrations/protocols
```

普通 business use case 不直接依赖 integration application service。它只依赖自己定义的 port。`launch/composition` 是集中调用 integration application service 并注入实现的地方。

## 分层职责

### Application Use Case Owns the Port

上层业务 area 定义自己需要的 contract。

示例：

```text
kairospy/application/usecases/execution/live.py
  OrderSubmissionRequest
  OrderSubmissionResult
  OrderExecutionPort
  LiveOrderExecutionUseCase
```

Port 使用 KairosPy 业务语言：

```python
class OrderExecutionPort(Protocol):
    def submit(self, request: OrderSubmissionRequest) -> OrderSubmissionResult:
        ...

    def cancel(self, request: OrderCancelRequest) -> OrderCancelResult:
        ...
```

不要让这个 port 变成第三方 SDK 形状：

```python
class OrderExecutionPort(Protocol):
    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        ...
```

后一种形状属于 integration connector/gateway，不是 application use case contract。

### Integration Application Provides the Implementation

Integration application service 是 integration context 对 composition 暴露的服务门面。它负责：

- 把 `AccountBookRef`、`MarketRef`、`ReferenceSourceRef` 等上层业务引用解析成 integration binding；
- 根据 `ParticipantRef`、`ProductLine`、`CapabilityRef` 选择 connector factory；
- 解析 credential scope；
- 创建 connector/gateway/translator；
- 包装 adapter；
- 返回上层 application port 的实现。

示例：

```python
@dataclass(frozen=True, slots=True)
class OrderIntegrationService:
    resolver: IntegrationResolver

    def execution_port(
        self,
        account: AccountBookRef,
        *,
        credential: str | None = None,
    ) -> OrderExecutionPort:
        connector = self.resolver.order_execution_for_book(
            account,
            credential=credential,
        )
        return BrokerOrderExecutionAdapter(connector)
```

调用方应是 composition：

```python
order_port = order_integrations.execution_port(account_book, credential=credential)
live_execution = LiveOrderExecutionUseCase(order_port)
```

### Adapter Translates Connector Language

Adapter 是 anti-corruption layer。它把 connector 的外部系统语言转换成 application port 语言。

```text
BinanceEquityBroker.create_order(...)
  -> BrokerOrderExecutionAdapter.submit(...)
  -> OrderSubmissionResult
```

Adapter 可以依赖：

- application use-case contract；
- integration connector/gateway protocol；
- payload translator；
- core model。

Adapter 不应该：

- 选择 live/paper/backtest；
- 读取 workspace；
- 管理 runtime lifecycle；
- 写 persistence store；
- 输出 CLI/TUI payload。

## Capability Services

Integration application layer 应按 capability 分文件，而不是做一个越来越大的万能 resolver。

### order.py

负责订单相关外部能力：

```text
execution_port(account) -> OrderExecutionPort
order_query_port(account) -> OrderQueryPort
private_order_stream_port(account) -> PrivateOrderStreamPort
```

它可以解析：

```text
AccountBookRef("binance:equity")
  -> ParticipantRef("broker", "binance")
  -> ProductLine("equity")
  -> CapabilityRef("order_execution")
  -> BinanceEquityExecutionConnector
  -> BrokerOrderExecutionAdapter
```

### market.py

负责行情相关外部能力：

```text
market_stream_port(market) -> MarketStreamPort
bar_history_port(source) -> BarHistoryPort
market_snapshot_port(market) -> MarketSnapshotPort
```

它可以解析：

```text
MarketRef("binance:spot:BTC/USDT")
  -> ParticipantRef("exchange", "binance")
  -> ProductLine("spot")
  -> CapabilityRef("market_data")
  -> BinanceMarketDataConnector
  -> MarketStreamAdapter
```

### account.py

负责账户相关外部能力：

```text
account_snapshot_port(account) -> AccountSnapshotPort
account_bootstrap_port(account) -> AccountBootstrapPort
private_account_stream_port(account) -> PrivateAccountStreamPort
```

### reference.py

负责 reference 和 catalog 相关外部能力：

```text
reference_catalog_port(source) -> ReferenceCatalogPort
reference_refresh_source(source) -> ReferenceRefreshSource
```

它可以解析：

```text
ReferenceSourceRef("provider", "massive")
  -> ParticipantRef("provider", "massive")
  -> CapabilityRef("reference")
  -> MassiveReferenceConnector
  -> ReferenceCatalogAdapter
```

## Naming Rules

Integration application service 使用 capability service 命名：

```text
OrderIntegrationService
MarketIntegrationService
AccountIntegrationService
ReferenceIntegrationService
```

方法名返回上层 port 时，用 port 语义：

```text
execution_port
market_stream_port
account_snapshot_port
reference_catalog_port
```

不要在 integration application service 的公开方法上暴露 SDK 语言：

```text
create_order_client
fetch_balance_client
watch_ticker_gateway
```

这些命名可以保留在 connector/gateway 内部。

## Binding Flow

推荐的完整调用链：

```text
workspace/config/mode
  -> application/support/launch/composition
  -> integrations/application/order.OrderIntegrationService
  -> integrations/domain.IntegrationBinding
  -> integrations/application/registry.CapabilityRegistry
  -> integrations/application/factories.ConnectorFactory
  -> integrations/connectors/broker/binance/BinanceEquityExecutionConnector
  -> integrations/adapters/order_execution.BrokerOrderExecutionAdapter
  -> application/usecases/execution.OrderExecutionPort
  -> application/usecases/execution.LiveOrderExecutionUseCase
```

核心原则：

```text
Use case owns the abstraction.
Integration application supplies the implementation.
Launch composition wires them together.
```

## Relationship with Integration Domain

`integrations/domain` 不知道上层 application port。它只定义 integration context 的稳定语言：

```text
ParticipantRef
ParticipantRole
ProductLine
CapabilityRef
IntegrationBinding
CredentialScope
EndpointProfile
RateLimitPolicy
IntegrationHealth
IntegrationError
```

`integrations/application` 使用这些 domain object 做解析和装配，但不会把交易领域规则下沉到 integration domain。

例如：

- Binance equity 是否需要 SAPI signed endpoint：integration domain/application。
- Binance equity live order 当前是否禁用：integration capability policy 或 launch safety policy。
- 一个订单意图是否通过风控：application/core。
- fill simulation 怎么发生：application/core。

## Relationship with Connectors

Connector 是外部参与方能力实现，不是上层 application service。

推荐：

```text
BinanceEquityExecutionConnector
  participant = binance
  role = broker
  product_line = equity
  capability = order_execution

BinanceSpotMarketDataConnector
  participant = binance
  role = exchange
  product_line = spot
  capability = market_data
```

`integrations/application/order.py` 可以选择这些 connector，并把它们包装成 `OrderExecutionPort`。

不要让上层 composition 直接到处 new connector。composition 应调用 integration application service。

## Migration Guidance

从当前结构迁移时，按 capability 逐步做，不要先机械移动所有文件。

每个切片完成以下事项：

1. 在 `application/usecases/<area>` 确认或收窄上层 port。
2. 在 `integrations/adapters/` 实现该 port。
3. 在 `integrations/application/<capability>.py` 增加 service 方法，返回 port implementation。
4. 让 `application/support/launch/composition` 调用 integration application service。
5. 删除 composition 中直接 new connector 的代码。
6. 删除旧 resolver 中对应硬编码分支，或改为只服务新 capability service。

优先迁移顺序：

```text
order execution
account bootstrap
market stream
reference catalog
market history
private streams
health diagnostics
```

验收标准：

- business use case 不 import `infrastructure.integrations.*`；
- composition 不直接 new 具体 connector；
- integration application service 返回上层 port implementation；
- adapter 不泄漏 raw payload；
- connector 仍保持外部系统语言；
- resolver 硬编码按 capability 逐步收敛到 binding/registry/factory。
