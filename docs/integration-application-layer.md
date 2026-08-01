# Integration Application Layer

本文定义 `kairospy/infrastructure/integrations/application/` 的定位。它是 integration bounded context 内部的 application layer，负责把外部接入能力组装成 KairosPy 上层 application 需要的端口实现。

相关边界：

- 总体架构：`docs/architecture-boundaries.md`
- application contract 和 raw payload 规则：`docs/application-ports-and-domain-boundaries.md`
- integration bounded context：`docs/integration-boundaries.md`
- runtime composition：`docs/runtime-composition-and-protocols.md`

## 核心结论

Integration 不是只向上层暴露 connector，也不应该再增加一层“返回 Protocol 的 factory service”。Integration 应该按业务领域提供 concrete application service，直接承接上层 runtime / use case 当前需要的集成行为。

目标形态：

```text
kairospy/application/usecases/<area>
  defines application-facing Port / Request / Result

kairospy/infrastructure/integrations/application/<domain>.py
  resolves external participant/product/capability
  builds connector/gateway/translator
  implements the application/runtime-facing behavior directly

kairospy/infrastructure/integrations/adapters
  adapts connector/gateway output when translation is still large enough to justify a helper
```

也就是说，`MarketIntegrationApplicationService` 可以实现 `MarketStreamGateway` 这类 application/runtime 需要的结构行为，但不应该提供 `market_stream_gateway(...) -> MarketStreamGateway` 这样的二次抽象。Protocol 的定义权仍属于消费方；integration application 只提供领域 concrete service。

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
    market.py
    order.py
    account.py
    reference.py
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

当前 `integrations/services/` 可以视为这个 application layer 的过渡形态。新代码优先收敛到 `integrations/application/<domain>.py` 的领域 service。旧 `services/resolver.py`、`services/registry.py` 后续应逐步变成领域 service 的内部实现细节，而不是对 launch composition 暴露的新入口。

## 依赖方向

允许：

```text
application/usecases/execution
  -> core
  -> local OrderExecutionPort

infrastructure/integrations/application/market
infrastructure/integrations/application/order
infrastructure/integrations/application/account
infrastructure/integrations/application/reference
  -> application/runtime or usecase local Protocols only as structural contracts
  -> integrations/domain
  -> integrations/adapters
  -> integrations/connectors

application/support/launch/composition
  -> infrastructure/integrations/application.<Domain>IntegrationApplicationService
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

普通 business use case 不直接依赖 integration application service。它只依赖自己定义的本地 contract。`launch/composition` 是集中构造 integration application service 并注入 concrete service 的地方。

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

Integration application service 是 integration context 对 composition 暴露的 concrete 服务。它负责：

- 把 `AccountBookRef`、`MarketRef`、`ReferenceSourceRef` 等上层业务引用解析成 integration binding；
- 根据 `ParticipantRef`、`ProductLine`、`CapabilityRef` 选择 connector factory；
- 解析 credential scope；
- 创建 connector/gateway/translator；
- 使用 adapter 或 translator；
- 直接实现上层 application/runtime 当前需要的行为。

示例：

```python
@dataclass(frozen=True, slots=True)
class OrderIntegrationApplicationService:
    resolver: IntegrationResolver

    def submit_order(
        self,
        request: OrderSubmissionRequest,
    ) -> OrderSubmissionResult:
        connector = self.resolver.order_execution_for_book(
            request.account,
        )
        response = connector.create_order(...)
        return translate_order_submission(response)
```

调用方应是 composition：

```python
orders = OrderIntegrationApplicationService(book=account_book, credential=credential)
live_execution = LiveOrderExecutionUseCase(orders)
```

### Adapter Translates Connector Language

Adapter 是 anti-corruption helper，不是必须独立存在的一层。只有当外部 payload 翻译足够复杂、多个入口复用、或者测试需要隔离时，才保留 adapter；否则翻译逻辑可以直接内聚在领域 integration application service 的私有方法里。

```text
BinanceEquityBroker.create_order(...)
  -> OrderIntegrationApplicationService.submit(...)
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

## Domain Services

Integration application layer 按业务领域拆 concrete service，而不是用一个万能 service，也不是按每个低层 capability 预先拆成大量 factory。

领域 service 负责当前 application/runtime 需要的对应集成行为：

```text
MarketIntegrationApplicationService.watch_ticker_updates(...)
MarketIntegrationApplicationService.watch_order_book_updates(...)
OrderIntegrationApplicationService.submit(...)
OrderIntegrationApplicationService.fetch_open_orders(...)
AccountIntegrationApplicationService.fetch_balance(...)
AccountIntegrationApplicationService.watch_balance(...)
ReferenceIntegrationApplicationService.fetch_catalog(...)
```

它们可以在内部解析：

```text
AccountBookRef("binance:equity")
  -> ParticipantRef("broker", "binance")
  -> ProductLine("equity")
  -> CapabilityRef("order_execution")
  -> BinanceEquityExecutionConnector
  -> BrokerOrderExecutionAdapter
```

行情链路同理：

```text
MarketRef("binance:spot:BTC/USDT")
  -> ParticipantRef("exchange", "binance")
  -> ProductLine("spot")
  -> CapabilityRef("market_data")
  -> BinanceMarketDataConnector
  -> MarketStreamAdapter
```

reference 链路：

```text
ReferenceSourceRef("provider", "massive")
  -> ParticipantRef("provider", "massive")
  -> CapabilityRef("reference")
  -> MassiveReferenceConnector
  -> ReferenceCatalogAdapter
```

## Naming Rules

Integration application service 使用领域 concrete service 命名：

```text
MarketIntegrationApplicationService
OrderIntegrationApplicationService
AccountIntegrationApplicationService
ReferenceIntegrationApplicationService
```

公开方法名使用业务行为，不使用 factory / port-returning 语义：

```text
watch_ticker_updates
submit_order
bootstrap_account
fetch_reference_catalog
```

不要在 integration application service 的公开方法上暴露 SDK 语言，也不要新增返回 Protocol 的方法：

```text
create_order_client
fetch_balance_client
watch_ticker_gateway
market_stream_gateway
execution_port
```

这些命名可以保留在 connector/gateway 内部。

## Binding Flow

推荐的完整调用链：

```text
workspace/config/mode
  -> application/support/launch/composition
  -> integrations/application.OrderIntegrationApplicationService
  -> integrations/domain.IntegrationBinding
  -> integrations/services/resolver or future internal resolver
  -> integrations/connectors/broker/binance/BinanceEquityExecutionConnector
  -> optional translator/adapter
  -> application/usecases/execution.LiveOrderExecutionUseCase
```

核心原则：

```text
Use case owns the abstraction.
Integration domain application service is the concrete implementation.
Launch composition injects the concrete domain service.
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

`OrderIntegrationApplicationService` 可以选择这些 connector，并直接实现订单提交行为。

不要让上层 composition 直接到处 new connector。composition 应调用 integration application service。

## Migration Guidance

从当前结构迁移时，按领域切片逐步做，不要先机械移动所有文件，也不要把每个低层 capability 都变成一个新的 service 文件。

每个切片完成以下事项：

1. 在消费方确认是否真的需要 Protocol；只保留 runtime/usecase 本地需要的最小结构约束。
2. 在对应领域 integration application service 上直接实现该行为。
3. 只有翻译逻辑复杂或复用明确时，才保留 `integrations/adapters/` helper。
4. 让 `application/support/launch/composition` 构造 concrete domain integration application service。
5. 删除 composition 中直接 new connector 或直接包 adapter 的代码。
6. 删除旧 resolver 中对应硬编码分支，或改为只服务领域 service 的内部解析。

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
