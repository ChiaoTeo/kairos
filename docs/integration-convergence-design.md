# Integration 收敛设计

本文定义 `infrastructure/integrations` 的目标结构，以及它与 `usecase`、`System`、`composition` 和 Binance 外部接口之间的职责边界。

本文是目标设计文档。现有代码允许渐进迁移，但新增代码应遵守本文和 [`module-boundaries.md`](./module-boundaries.md)。

> 术语修订：本文最终命名以第 20 节为准：Integration application 对外提供 `IntegrationConnection`；内部使用 `*ConnectionService`、`*Client`、`*Operations`、`*Stream` 和 `*Translator`，不新增过大的 participant service。

## 1. 设计结论

Integration 与 usecase 使用不同的分类轴：

```text
Integration：外部系统如何接入
Usecase：系统想完成什么业务动作
```

Integration 的主分类为：

```text
Integration
├── participant
│   ├── exchange
│   │   ├── binance
│   │   ├── okx
│   │   └── hyperliquid
│   ├── broker
│   │   ├── binance
│   │   ├── ibkr
│   │   └── okx
│   └── provider
│       └── massive
├── product
│   ├── spot
│   ├── equity
│   ├── usd_margined_futures
│   ├── coin_margined_futures
│   └── options
└── access
    ├── public
    │   └── market
    └── private
        └── account
```

其中：

- `exchange` 表示交易所身份，例如 Binance、OKX。
- `broker` 表示账户、凭证和账户状态提供方，例如 Binance Broker、IBKR。
- `provider` 表示市场数据提供方，例如 Massive。
- `product` 表示交易所产品线，例如 Spot、USDⓈ-M Futures。
- `access` 表示外部访问边界，而不是业务模块边界。
- `public.market` 负责公开市场数据和市场目录。
- `private.account` 负责需要账户凭证的账户、订单、成交和交易操作。

最终的 Integration 组合不是只有一个 participant。典型的 Binance Spot 系统可能同时包含：

```text
BinanceSpotExchangeConnection
├── public.market
└── execution venue rules

BinanceSpotBrokerConnection
└── private.account

MassiveMarketDataProvider
└── public.market alternative
```

而不是：

```text
BinanceBroker
BinanceMarketDataConnector
BinanceReferenceConnector
BinanceExecutionConnector
```

后者把业务能力、外部身份和传输方式混在了一起。

## 2. 为什么采用这个分类

### 2.1 三类 Participant 的职责

三类 Participant 必须保留，而且不是同一个概念的不同命名：

```text
Exchange
    交易场所
    提供盘口、交易规则、symbol、撮合状态和交易执行接口

Broker
    账户提供方
    提供 credential、账户余额、账户订单、成交、持仓和私有事件

Provider
    数据提供方
    提供行情、历史数据、参考数据或其它外部数据
```

一个现实系统可以让同一个外部机构同时承担多个角色，但 Domain 中仍然要保留角色区别：

```text
Binance as Exchange
Binance as Broker
```

它们可以共享底层 HTTP/WebSocket driver，也不能合并成一个无角色的 `BinanceParticipant`。

### 2.2 Integration 与 usecase 的分类轴不同

业务 usecase 可以是：

```text
market
reference
account
execution
strategy
```

这些表示业务意图和业务状态。

Integration 则表示：

```text
通过什么外部系统
访问什么产品
以什么权限范围
通过什么传输协议
```

同一个 Integration 能力可以被多个 usecase 使用：

```text
BinanceSpotExchangeConnection.public.market
    ├── market usecase
    └── reference usecase

BinanceSpotBrokerConnection.private.account
    ├── account usecase
    └── execution usecase
```

因此，`reference` 和 `execution` 不再作为 Integration 的平行顶层分类。

### 2.3 Reference 融入 public.market

市场 reference 通常包括：

- 交易对和 symbol；
- base / quote；
- 价格最小变动单位；
- 数量步长；
- 最小名义金额；
- 交易状态；
- 合约类型；
- 到期时间；
- 杠杆和合约规则；
- 交易所 symbol 与系统 MarketRef 的映射。

这些信息属于公开市场目录，而不是一种独立的外部参与者类型。

所以 Integration 内部不再需要独立的：

```text
BinanceReferenceConnector
ReferenceGateway
ReferenceSourceResolver
```

而是由 exchange 或 provider 的 public market 能力提供：

```text
BinanceSpotExchangePublicMarketAccess.catalog()
MassivePublicMarketAccess.catalog()
```

提供。

`reference` usecase 仍然可以独立存在，因为它还负责 catalog 的业务生命周期、本地保存、版本和 lifecycle event。Integration 只负责获取和转换外部市场目录。

### 2.4 Execution 融入 private.account，但保留 exchange/broker 双边关系

下单、撤单、订单查询、成交查询和持仓查询通常需要：

- API key / secret；
- 账户权限；
- 账户级 rate limit；
- 账户订单状态；
- private user stream；
- account scope。

账户侧的 execution 入口属于：

```text
private.account
```

但执行请求的完整路由关系通常是：

```text
execution usecase
    -> broker private account access
    -> exchange execution venue
    -> exchange REST / WebSocket request API
```

Broker 负责账户身份和账户权限，Exchange 负责交易场所规则和执行接口。Binance 同时扮演两者时，仍然通过两个 typed participant 表达。

这不表示 execution 业务要并入 account usecase。业务层仍保持：

```text
account usecase
execution usecase
```

两者通过各自的 application protocol 使用同一个 `PrivateAccountAccess` 实现。

## 3. 与 Binance API 的对应关系

Binance 的 API 不是一个单一 client 可以完整表达的接口族。至少要区分：

```text
Binance Spot
├── REST API
├── public market WebSocket streams
├── private user data stream
└── WebSocket request API

Binance USDⓈ-M Futures
├── futures REST API
├── futures market WebSocket streams
└── futures user data stream
```

这些传输方式属于对应的 exchange/broker connection service 的内部实现，不直接暴露给 usecase。

参考资料：

- [Binance Spot REST API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api)
- [Binance Spot WebSocket Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [Binance Spot User Data Stream](https://developers.binance.com/docs/binance-spot-api-docs/user-data-stream)
- [Binance Spot WebSocket API](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md)

Integration 的目标不是抹平 Binance 所有差异，而是把差异封装在产品级 connection service 内，并把稳定的系统模型输出给业务模块。

### 3.1 CCXT 的定位

CCXT 不是 Participant，也不是 provider、exchange 或 broker。它只是 Integration 内部可选的统一 market driver：

```text
BinanceSpotConnectionService
└── BinanceSpotPublicMarketService
    ├── BinanceSpotRestClient       # 官方接口，强交易所语义
    ├── BinanceSpotMarketStream     # 官方 WebSocket
    └── CcxtMarketClient            # 未来可选的公共 REST driver
```

CCXT 的约束如下：

- 只能位于 `services/drivers/` 或 `services/clients/` 内部；
- `ccxt.Exchange`、`ccxt.Market`、`ccxt.Order` 和 CCXT `params` 不能进入 application、domain 或 usecase；
- CCXT 的 `exchange_id` 不能替代 typed `ExchangeRef`；
- public historical data、ticker 等统一接口可以考虑使用 CCXT；
- private account、user stream、下单、撤单和交易所特有规则优先使用官方 endpoint；
- CCXT driver 必须经过 Integration 内部 translator 转换为系统模型；
- 暂不恢复旧的 `drivers/ccxt.py` connector，等 public market 的消费协议和 translator 边界稳定后再接入。

因此，当前 Binance Spot 第一阶段不依赖 CCXT；未来接入 CCXT 只会新增内部 driver，不会新增 `CcxtConnection` 或 `CcxtProvider`。

## 4. 产品级 Connection Service

### 4.1 Connection Service 的粒度

System-facing connection 按一个运行时连接 spec 建立；内部再按 participant、product 和具体 Access 类型组合实现：

```text
BinanceSpotConnectionService
BinanceUsdMarginedFuturesConnectionService
IbkrEquityConnectionService
MassivePublicMarketConnectionService
```

一个 connection service 不是一个业务 service，而是一个 participant 在某条产品线或数据能力上的外部连接资源组合。

推荐的内部组合形状：

```python
PublicMarketAccess
    connection_id: str
    route: MarketDataRoute
    transports: tuple[TransportKind, ...]

PrivateAccountAccess
    connection_id: str
    route: AccountRoute
    execution: ExecutionRoute | None
    transports: tuple[TransportKind, ...]
```

这是组合根使用的结构，不是业务 usecase 直接依赖的具体 service 类型。

participant connection service 的组合关系为：

```text
BinanceSpotConnectionService
    ├── public_market
    └── private_account + execution route

BinanceSpotConnectionService
    └── private_account

MassiveMarketConnectionService
    └── public_market
```

业务 route 的来源为：

```text
MarketRoute
    -> BinanceSpotConnectionService.public_market
    或 MassiveMarketConnectionService.public_market

AccountRoute
    -> BinanceSpotConnectionService.private_account

ExecutionRoute
    -> BinanceSpotConnectionService.private_account
    + BinanceSpotConnectionService 的 ExecutionRoute
```

### 4.2 PublicMarketAccess

`PublicMarketAccess` 是 composition 从 connection assembly 取得的具体访问类型，表达某个 exchange/provider 的 public market route 和 transport。它不是一个万能业务接口，也不承载 reference 或 market 的业务状态机。

真正的操作协议由消费方 usecase 定义，例如 market usecase 可以定义自己的 `HistoricalMarketDataPort`，reference usecase 定义自己的 `ReferenceCatalogSource`。同一个 `PublicMarketAccess` 对象对应的内部 connection service 可以实现这些协议，但协议定义权不属于 Integration。

```python
class PublicMarketAccess:
    connection_id: str
    route: MarketDataRoute
    transports: tuple[TransportKind, ...]
```

具体实现可以支持 ticker、trades、order book、option greeks 等更细的协议，但这些细节不应在 Integration 顶层形成大量平行 connector 类型。

### 4.3 PrivateAccountAccess

`PrivateAccountAccess` 是 composition 从 connection assembly 取得的具体访问类型，表达 broker account route；如果有执行需求，它同时携带 `ExecutionRoute`。它不把 execution 再拆成第三个 Integration 顶层访问类型。

账户和执行操作协议仍由各自 usecase 定义，例如 account usecase 的 snapshot/stream protocol 与 execution usecase 的 submit/cancel protocol。Integration 内部使用同一组 private client、operations、stream 和 translator 实现这些协议。

```python
class PrivateAccountAccess:
    connection_id: str
    route: AccountRoute
    execution: ExecutionRoute | None
    transports: tuple[TransportKind, ...]
```

这里的 `OrderRequest`、`OrderResult`、`AccountSnapshot` 等必须使用系统的 domain/application 类型，不得暴露 Binance payload、CCXT 对象或 vendor `params`。

### 4.4 为什么不是每个动作一个 Port

当前设计不建议把以下对象全部作为 Integration 的顶层入口：

```text
OrderExecutionPort
AccountBootstrapGateway
PrivateAccountStreamGateway
MarketStreamConnection
ReferenceCatalogSource
```

这些名称可以作为 usecase 模块内部的最小协议，但 Integration 内部应通过具体 Access 类型和 connection service 组合实现它们。

例如：

```text
BinanceSpotPrivateAccountService
    implements account.AccountAccess
    implements execution.ExecutionAccess
```

这样既保留依赖倒置，也避免产生大量生命周期、工厂和 registry 对象。

## 5. usecase 与 Integration 的依赖关系

推荐依赖图：

```text
surface
   ↓
usecase.application
   ↓
usecase.domain
   ↑
usecase.protocol  ←  integration connection service implements
   ↑
composition binds concrete connection service
```

具体例子：

```text
market.protocol.PublicMarketAccess
        ↑
BinanceSpotPublicMarketService

reference.protocol.ReferenceCatalogSource
        ↑
BinanceSpotPublicMarketService

account.protocol.AccountAccess
        ↑
BinanceSpotPrivateAccountService

execution.protocol.ExecutionAccess
        ↑
BinanceSpotPrivateAccountService
```

业务模块不能直接 import：

```python
from kairospy.infrastructure.integrations.application.connections import IntegrationConnection
from kairospy.infrastructure.integrations.services.connectors.binance import BinanceClient
```

具体 connection service 只能由 Integration application 内部或测试 fixture 创建。

## 6. Composition 的职责

Composition 是唯一的具体实现组装点：

```text
配置
  ↓
IntegrationConnectionFactory
  ↓
IntegrationConnection (Binance Spot spec)
  ↓
public.market / private.account
  ↓
各业务 Application Service
  ↓
TradingSystem
```

推荐工厂接口：

```python
connection = integration_connection_factory.create(
    exchange=ExchangeId.BINANCE,
    product=ProductFamily.SPOT,
    credential=credential,
    mode=RuntimeMode.LIVE,
)
```

工厂选择的是：

```text
(exchange, product, mode)
```

而不是每次业务调用时重新选择：

```text
(broker, book, market, provider, raw gateway)
```

传输方式由 connection service 内部决定：

```text
public.market
├── REST
└── market WebSocket

private.account
├── REST
├── user WebSocket
└── request WebSocket
```

## 7. 生命周期边界

### Integration 负责

- 外部 client 创建；
- API 认证和签名；
- HTTP / WebSocket 连接；
- Binance ping/pong；
- 连接级 reconnect；
- vendor payload 解析；
- vendor error 转换；
- 外部请求 rate limit；
- 外部 symbol、状态和字段转换。

### System / Runtime 负责

- System-scoped 资源持有；
- start / stop；
- 业务订阅状态；
- checkpoint；
- 业务级重订阅；
- account reconcile；
- order state machine；
- projection 和事件路由。

Integration 可以实现连接生命周期钩子，但不拥有业务生命周期。

例如：

```text
System.start()
  -> start BinanceSpotConnection resources
  -> initialize account / market components
  -> restore business subscriptions
  -> start runtime
```

不能让 Integration 自己决定账户账本、订单状态或 market subscription 的业务状态。

## 8. 对 kairos_v2 的借鉴

`kairos_v2` 中值得保留的结构是：

```text
Exchange endpoint / connection service
    ↓
endpoint request / response traits
    ↓
generic HTTP executor
    ↓
exchange-specific payload converter
```

在当前项目中对应为：

```text
IntegrationConnection
    ↓
Binance endpoint implementations
    ↓
HTTP / WebSocket driver
    ↓
Binance translators
    ↓
domain/application models
```

不应直接照搬 `kairos_v2` 中每个 handler 内部按 exchange match 并即时创建 client 的方式。当前项目应把 participant/product 选择集中到 composition，并让 connection service 在一个 System 实例中长期持有底层资源。

## 9. 目标目录结构

当前收敛后的目录如下：

```text
kairospy/infrastructure/integrations/
├── application/
│   ├── __init__.py
│   ├── connections.py
│   └── assembly.py
├── protocol.py
├── domain/
│   ├── __init__.py
│   ├── participants.py
│   ├── products.py
│   ├── bindings.py
│   ├── connections.py
│   ├── credentials.py
│   ├── policies.py
│   ├── updates.py
└── services/
    ├── connections/
    │   └── base.py
    ├── connection_services/
    │   └── binance_spot.py
    ├── clients/
    ├── operations/
    ├── streams/
    ├── endpoints/
    ├── translators/
    ├── drivers/
    └── factories/
```

`connection_services/` 是内部产品级资源组合对象；`connections/` 提供 System connection 实现；`endpoints/`、`translators/`、`drivers/` 都属于 integration 内部实现。registry 只位于 `services/factories/`，不再有 resolver。

## 10. 现有代码的迁移映射

### 10.1 旧 broker / exchange connector

```text
BinanceBroker
BinanceEquityBroker
BinanceSapiClient
    -> BinanceSpotPrivateAccountService
```

```text
BinanceMarketDataConnector
BinanceEquityMarketDataConnector
BinanceEquityReferenceConnector
    -> BinanceSpotPublicMarketService
```

### 10.2 旧 registry

现有：

```text
IntegrationRegistry
├── market_feed
├── market_feed_for_market
├── broker
├── broker_for_book
└── reference_source
```

目标：

```text
IntegrationConnectionRegistry
└── (participant, product, access scope) -> ConnectionServiceFactory
```

reference 和 execution 不再有独立的顶层 factory。

### 10.3 旧 resolver

逐步删除或限制以下接口：

```text
broker_for_book()
account_balance_for_book()
order_query_for_book()
order_execution_for_book()
private_account_stream_for_book()
reference_data()
```

替换为：

```text
resolve(exchange, product, mode, credential)
```

account book 到 connection binding 的映射由 account routing 或 composition 完成。

## 11. 迁移顺序

迁移应按能力切片，而不是一次性移动整个目录。

### 阶段一：建立产品级 Connection Service（已落地）

实现：

```text
IntegrationConnection (Binance Spot spec)
BinanceSpotPublicMarketService
BinanceSpotPrivateAccountService
```

旧 connector 入口已删除；REST、WebSocket、operations 和 translator 已迁移到新的 `services/` 内部结构。

### 阶段二：合并 public market（第一阶段已落地）

迁移：

```text
market data
reference catalog
```

验证：

- market bars 能正常获取；
- market stream 能正常订阅；
- catalog 能转换为 `ReferenceCatalog`；
- reference application 不再依赖独立 reference connector。

### 阶段三：合并 private account（第一阶段已落地）

迁移：

```text
account bootstrap
balance
orders
trades
private stream
```

验证：

- account snapshot 能正常生成；
- user stream 能转换为业务事件；
- account runtime 仍然拥有 reconcile 和 checkpoint。

### 阶段四：将 execution 接入 private account（第一阶段已落地）

迁移：

```text
submit order
cancel order
open orders
positions
order history
```

验证：

- execution application 只依赖自己的 protocol；
- 下单和撤单返回业务结果；
- Binance 参数转换只存在于 integration translator；
- 订单状态机仍然属于 execution usecase。

### 阶段五：扩展 Futures 和其它 exchange

按产品建立：

```text
BinanceUsdMarginedFuturesConnection
OkxSpotConnection
HyperliquidPerpetualConnection
```

不应先抽象一个包含所有交易所差异的万能 `GenericExchangeClient`。

## 12. 边界检查清单

新增或迁移代码时确认：

- integration 的主对象是否是 System-scoped connection？
- reference 是否位于 `public.market`？
- execution 是否位于 `private.account`？
- usecase 是否仍然拥有自己的 protocol？
- integration 是否只实现 protocol，而不定义业务状态机？
- transport 是否被隐藏在 connection service 内部？
- application DTO 是否暴露 vendor params、SDK 类型或 raw payload？
- 是否在 Integration application 之外创建 concrete connection service？
- 是否有业务代码依赖 integration `services`？
- 是否有 `broker_for_book`、`provider.raw` 之类的 service locator 继续扩大？

建议每次迁移后运行：

```bash
rg 'from .*\.services|import .*\.services' kairospy tests
rg 'from kairospy\.infrastructure\.integrations\.services' kairospy tests
pytest tests/test_integration_connections.py
```

## 13. 最终原则

最终结构可以概括为：

```text
ExchangeProductConnection
├── public.market
│   ├── market data
│   └── reference catalog
└── private.account
    ├── account state
    ├── user events
    └── execution operations
```

业务模块仍然按照业务意图存在：

```text
market
reference
account
execution
```

但它们共享同一个产品级 Integration connection 的不同能力视图。

核心原则是：

> Integration 按外部系统、产品和访问边界组织；usecase 按业务意图和业务状态组织；composition 将 connection 中的能力注入多个 usecase。

## 14. Participant 与 Integration Domain

`Participant` 不应删除。它是 Integration Domain 中表达“外部参与者身份”的基础模型，而不是旧 registry 的临时辅助类型。

当前 `ParticipantRef` 的问题不是存在本身，而是：

- `role` 是普通 `Literal`；
- `name` 是裸字符串；
- `ProductLine` 也是裸字符串包装；
- `ReferenceSourceRef` 同时混合 participant、market、book 三种维度；
- `ConnectionState.venue` 使用了已经不再准确的命名；
- `policies.py` 把 account book 和 CCXT 参数直接放进 Integration Domain。

目标是保留 Participant，并把它加强为稳定的 Domain 身份模型。

### 14.1 身份模型

建议定义以下类型：

```python
class ParticipantKind(StrEnum):
    EXCHANGE = "exchange"
    PROVIDER = "provider"
    BROKER = "broker"


class ExchangeId(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    HYPERLIQUID = "hyperliquid"


class BrokerId(StrEnum):
    BINANCE = "binance"
    OKX = "okx"
    IBKR = "ibkr"


class ProviderId(StrEnum):
    MASSIVE = "massive"


class ProductFamily(StrEnum):
    SPOT = "spot"
    EQUITY = "equity"
    USD_M_FUTURES = "usd_margined_futures"
    COIN_M_FUTURES = "coin_margined_futures"
    OPTIONS = "options"


class AccessScope(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class TransportKind(StrEnum):
    REST = "rest"
    MARKET_STREAM = "websocket_market_stream"
    USER_STREAM = "websocket_user_stream"
    REQUEST_API = "websocket_request_api"
```

`ProductFamily` 表达外部产品线，不等同于 account domain 的 `AccountBookKind`。例如 `funding`、`earn` 可能是账户产品或账户余额分区，不应直接伪装成交易所市场产品。上面重点表达的是“身份不是任意字符串”。

Participant 应保留为统一外部身份：

```python
@dataclass(frozen=True, slots=True)
class ParticipantRef:
    kind: ParticipantKind
    id: ExchangeId | BrokerId | ProviderId
```

更严格的实现可以拆成：

```python
@dataclass(frozen=True, slots=True)
class ExchangeRef:
    id: ExchangeId


@dataclass(frozen=True, slots=True)
class BrokerRef:
    id: BrokerId


@dataclass(frozen=True, slots=True)
class ProviderRef:
    id: ProviderId
```

推荐优先使用专门类型；只有确实需要在 registry 中处理多种 participant 时，才使用带 kind 的 `ParticipantRef`。

### 14.2 IntegrationBinding

`ParticipantRef` 只表示“谁”，不表示“通过什么能力访问谁”。建议增加组合身份：

```python
@dataclass(frozen=True, slots=True)
class IntegrationBinding:
    participant: ParticipantRef
    product: ProductFamily | None
    access: AccessScope
    transport: TransportKind
```

对于 execution，单个 binding 不足以表达账户和交易场所的双边关系，应增加 route 模型：

```python
@dataclass(frozen=True, slots=True)
class ExecutionRoute:
    broker: ParticipantRef
    exchange: ParticipantRef
    product: ProductFamily
```

对于 market data，来源可以是 exchange 或 provider：

```python
@dataclass(frozen=True, slots=True)
class MarketDataRoute:
    source: ParticipantRef  # kind 必须是 exchange 或 provider
    product: ProductFamily | None
```

对于账户能力，来源必须是 broker：

```python
@dataclass(frozen=True, slots=True)
class AccountRoute:
    broker: ParticipantRef  # kind 必须是 broker
    product: ProductFamily
```

典型 binding：

```text
binance + spot + public + rest
binance + spot + public + market_stream
binance + spot + private + rest
binance + spot + private + user_stream
```

产品级 connection 的身份可以进一步表达为：

```python
@dataclass(frozen=True, slots=True)
class ExchangeProductRef:
    exchange: ExchangeId
    product: ProductFamily
```

所有 connection、连接状态和错误都应尽可能携带 `ExchangeProductRef` 或 `IntegrationBinding`，而不是散落多个 `str` 参数。

### 14.3 字符串的使用边界

不是所有字符串都需要消灭。以下内容可以继续是字符串：

- 外部 symbol，例如 `BTCUSDT`；
- Binance order id；
- connection id；
- client order id；
- credential id；
- 不可预先枚举的 provider 扩展标识。

以下内容不应继续使用裸字符串：

- exchange 名称；
- data source 名称；
- product/book 类型；
- access scope；
- transport 类型；
- connection 中的 `venue` 字段；
- reference source 的 kind。

## 15. 当前 Integration 全量调整指引

以下是当前 `kairospy/infrastructure/integrations` 各部分的目标定位。

### 15.1 顶层 package

| 当前位置 | 目标动作 | 最终定位 |
|---|---|---|
| `infrastructure/integrations/__init__.py` | 保留并保持空导出 | 包边界，不暴露实现 |
| `application/__init__.py` | 保留空导出 | 只允许从 `application/connections.py` 或 `application/assembly.py` 调用真实 API |
| `services/__init__.py` | 保留，但不导出实现 | 明确 services 是私有实现 |

不要通过 package `__init__.py` 重新聚合 connection service、resolver 或 connector。

### 15.2 当前 application 层

| 当前文件 | 目标动作 | 说明 |
|---|---|---|
| `application/connections.py` | 保留 | 定义 `IntegrationConnectionSpec`、`IntegrationConnection`、`PublicMarketAccess`、`PrivateAccountAccess` 和 assembly DTO |
| `application/assembly.py` | 保留 | composition-facing 的 registry 组装入口；不把 concrete service 放进返回值 |
| `application/integration.py` | 重写 | 从 `market/account/execution/reference` 四个业务 facade 改为 composition-facing 的 connection assembly 入口 |
| `application/market.py` | 删除或合并 | `MarketIntegrationApplication` 改为 `PublicMarketAccess` 的组装实现；reference 能力并入此边界；来源可以是 exchange 或 provider |
| `application/reference.py` | 删除 | reference 不再是 Integration 顶层入口；保留 reference usecase 自己的 application/protocol |
| `application/account.py` | 重写 | 变成 broker-owned `PrivateAccountAccess` 的组装入口，覆盖 bootstrap、balance、private events |
| `application/execution.py` | 删除或合并 | execution 适配能力并入 `PrivateAccountAccess`，但保留 `ExecutionRoute(broker, exchange, product)` 的双边路由 |
| `application/payloads.py` | 删除 | 不应从 application 暴露 CCXT payload translator；translator 归 services 内部 |

最终推荐：

```text
application/
├── exchanges.py       # connection assembly / runtime-facing API
└── connections.py     # 如果确实需要暴露长期连接资源
```

application 层不应继续出现 `ReferenceIntegrationApplication`、`ExecutionIntegrationApplication` 这类按业务 usecase 命名的 Integration facade。

### 15.3 当前 domain 层

| 当前文件 | 目标动作 | 说明 |
|---|---|---|
| `domain/participants.py` | 加强并保留 | 引入 `ParticipantKind`、`ExchangeId`、`ProviderId`、BrokerRef 和专门 Participant 类型 |
| `domain/products.py` | 重写 | `ProductLine.value: str` 改为 `ProductFamily`，需要扩展时再增加 typed product spec |
| `domain/bindings.py` | 重写 | `ReferenceSourceRef` 改为 `IntegrationBinding`、`MarketDataRoute`、`AccountRoute` 和 `ExecutionRoute` |
| `domain/connections.py` | 重写 | `venue` 改成 `exchange` 或 `participant`；`ConnectionTransport` 扩展为正式 `TransportKind` |
| `domain/policies.py` | 拆分 | 保留纯身份和 access 判断；CCXT params、broker book 参数移到 services/translators |
| `domain/updates.py` | 重写 | raw `fields: Mapping[str, object]` 不应成为长期 domain 模型；改为 typed integration event 或留在 payload translator 内部 |
| `domain/__init__.py` | 更新 | 只导出稳定的 domain identity、binding、connection 类型 |

Domain 层可以表达：

```text
Participant
ExchangeProductRef
IntegrationBinding
ConnectionState
PublicMarketAccess
PrivateAccountAccess
```

Domain 层不能表达：

```text
ccxt params
Binance endpoint path
SDK response
account application coordinator
broker book routing implementation
```

### 15.4 当前旧实现目录（迁移前）

| 当前文件 | 目标动作 | 最终定位 |
|---|---|---|
| 旧 `services/` 下的连接实现 | 重写或删除 | 按 `connections/`、`connection_services/`、`clients/`、`operations/`、`streams/`、`translators/` 重新定位 |

这些旧实现不应该分别被 registry 暴露。它们应该被迁移为 connection service 内部组件：

```text
BinanceSpotConnectionService
├── BinanceSpotPublicMarketService
└── BinanceSpotPrivateAccountService
```

### 15.5 当前 services/connectors

| 当前位置 | 目标动作 | 说明 |
|---|---|---|
| `connectors/broker/binance/crypto_execution.py` | 重写并移动 | 作为 Binance Spot private account 的 order/account 实现；不再命名为 broker |
| `connectors/broker/binance/equity_execution.py` | 分析后删除或独立 data integration | 如果确实是股票/权益接口，不能伪装成 Binance Spot；按真实产品和 exchange 重新建 connection service |
| `connectors/broker/binance/equity_market_data.py` | 删除或移动 | 与 Binance crypto Spot 不属于同一 product，先确认真实外部 API 再归类 |
| `connectors/broker/binance/equity_reference.py` | 删除或移动 | reference 并入对应产品的 public market |
| `connectors/broker/binance/sapi.py` | 保留并移动 | 作为 Binance private REST driver，不是业务 connector |
| `connectors/broker/okx.py` | 重写并移动 | 归入 `connection_services/okx/<product>/private_account.py` |
| `connectors/broker/ibkr.py` | 重命名/重新定位 | IBKR 可以保留 broker 身份，但应按实际 product/access 建 connection service |
| `connectors/exchange/binance/market_data.py` | 重写并移动 | 归入 `connection_services/binance/spot/public_market.py`，内部使用 endpoint/driver |
| `connectors/exchange/binance/reference.py` | 合并 | 并入 Binance public market catalog translator |
| `connectors/exchange/okx/market_data.py` | 重写并移动 | 归入 OKX 产品级 public market |
| `connectors/exchange/hyperliquid/market_data.py` | 重写并移动 | 归入 Hyperliquid 产品级 public market |
| `connectors/provider/massive.py` | 保留并重命名 | 归入 `providers/massive/market_data.py` |
| `connectors/provider/massive_reference.py` | 合并 | 若提供市场目录，归入 Massive public market/catalog；否则拆成真实数据能力 |
| 各级 `__init__.py` | 收紧导出 | 不暴露 concrete connector |

`broker` 目录不是全部删除。只有当“broker”确实是外部参与者的真实身份时才保留这个 Domain 概念；它不再是默认的 connector 目录分类。

### 15.6 当前 services/drivers

| 当前文件 | 目标动作 | 最终定位 |
|---|---|---|
| `drivers/ccxt.py` | 暂不恢复 | CCXT 仅作为未来可选的 public market driver；接入前必须先确定消费方 protocol 和 translator 边界 |
| `drivers/massive.py` | 保留 | Massive HTTP/WebSocket driver |
| `drivers/binance_reference.py` | 删除或合并 | reference driver 不再独立存在，归 public market endpoint |
| `drivers/__init__.py` | 保持私有 | 不作为跨模块 API |

Driver 只负责：

- 创建 SDK client；
- 发送请求；
- 管理 transport session；
- 返回 vendor payload。

Driver 不负责：

- account ledger；
- order state machine；
- market subscription state；
- reference lifecycle；
- usecase orchestration。

### 15.7 当前 services/payloads

| 当前位置 | 目标动作 | 说明 |
|---|---|---|
| `payloads/types.py` | 保留为私有基础类型 | `RawPayload` 只允许停留在 integration services 内部 |
| `payloads/ccxt_market.py` | 暂不恢复 | 未来移动为 `services/translators/ccxt_market.py`，不进入 application |
| `payloads/ccxt_account.py` | 删除旧入口 | private account 优先使用官方 Binance payload translator；未来若确有需要再新增内部 translator |
| `payloads/ccxt_execution.py` | 删除旧入口 | execution 不通过 CCXT 统一接口承载 |
| `payloads/ccxt_parsing.py` | 删除旧入口 | parsing helper 随具体 driver 一起重新设计 |
| `payloads/binance_equity_execution.py` | 移动或删除 | 先确定真实 product；不再以 equity 假定 Binance crypto Spot |
| `payloads/__init__.py` | 删除公共导出 | translator 由 connection service 内部直接使用 |

转换链必须保持：

```text
usecase request / domain model
    -> vendor request
    -> vendor response / event
    -> domain/application result
```

vendor payload 不得越过 integration 边界。

### 15.8 当前 services/protocol.py 与 protocols.py

| 当前文件 | 目标动作 | 说明 |
|---|---|---|
| `services/protocol.py` | 大幅收缩 | 删除宽泛的 `RawMarketDataGateway`、`AccountBootstrapClient` 等基础设施公共协议 |
| `services/protocols.py` | 删除或并入 services 内部 | 不作为跨模块 port 定义位置 |

业务所需 protocol 应回到消费方：

```text
application/usecases/market/protocol.py
application/usecases/reference/protocol.py
application/usecases/account/protocol.py
application/usecases/execution/protocol.py
```

Integration service 可以实现这些 protocol，但不拥有它们的定义权。

### 15.9 当前 registry、resolver、builtins

| 当前文件 | 目标动作 | 说明 |
|---|---|---|
| `services/registry.py` | 重写 | 从多组 `broker/market/reference` factory 改为 `(participant_kind, participant_id, product) -> connection service factory` |
| `services/resolver.py` | 删除或极度收缩 | 删除 `broker_for_book`、`reference_data`、`order_execution_for_book` 等业务路由方法 |
| `services/builtins.py` | 重写 | 注册 `BinanceSpotConnectionFactory`、`BinanceUsdMarginedFuturesConnectionFactory` 等 connection service factory |
| `services/credentials.py` | 保留并强化 | credential id 可以是 typed `CredentialRef`；读取逻辑仍属于 infrastructure |

目标 registry：

```python
registry.register(
    participants=(ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),),
    product=ProductFamily.SPOT,
    factory=BinanceSpotConnectionService,
)

registry.register(
    participants=(
        ParticipantRef(ParticipantKind.PROVIDER, ProviderId.MASSIVE),
        ParticipantRef(ParticipantKind.BROKER, BrokerId.BINANCE),
        ParticipantRef(ParticipantKind.EXCHANGE, ExchangeId.BINANCE),
    ),
    product=ProductFamily.SPOT,
    factory=BinanceSpotConnectionService,
)
```

registry 的 key 是完整 System connection 的 participant 集合和 product；access 与 transport 由 `IntegrationConnectionSpec.bindings()` 从 typed route 推导，不单独注册成业务 factory。

不再保留 resolver。Composition 直接把完整 spec 交给 registry：

```python
connection = registry.create(spec)
```

不能由 registry 根据 account book 和业务请求猜测 broker、exchange、provider 或 raw gateway。`ExecutionRoute` 所需的 broker/exchange 组合必须由 composition 明确提供。

## 16. 删除、重写与保留规则

### 保留

- Participant 及其 domain identity 方向；
- connection lifecycle model；
- credential loading；
- vendor drivers；
- payload translator；
- 产品和交易所差异化实现；
- composition registry。

### 重写

- `ParticipantRef` 为 typed participant identity；
- `ProductLine` 为 `ProductFamily` 或 typed product reference；
- `ConnectionState` 的 `venue` 为 `exchange` / `participant`；
- registry 改为 participant + product connection service registry；
- connection service 改为 exchange/product、broker/product 或 provider/data-source 粗粒度对象；
- public market 与 private account access；
- raw translator 的边界；
- `policies.py` 的 CCXT 参数映射位置。

### 删除

- Integration 顶层 `ReferenceIntegrationApplication`；
- Integration 顶层 `ExecutionIntegrationApplication`；
- `provider.raw.*` 一类 raw facade；
- 以业务 book 为主键的全局 resolver；
- application 层的 payload re-export；
- 把 reference、execution 作为与 market、account 平行的 Integration 分类；
- 没有明确产品归属的 `equity` connector，直到确认其真实外部接口。

### 已删除的旧入口

- 旧 connector 的具体实现入口；
- 旧 application facade；
- 旧 registry alias。

这些对象不再作为迁移桥接保留；新实现直接使用 System connection 和内部 connection service 组合。

## 17. 第一阶段实施范围

建议第一阶段只处理 Binance Spot：

```text
Participant / ExchangeId
    ↓
ExchangeRef(BINANCE) + ProductFamily.SPOT
    ↓
IntegrationConnection
    ├── PublicMarketAccess
    │   ├── catalog
    │   ├── bars
    │   └── market stream
    └── PrivateAccountAccess
        ├── snapshot
        ├── private events
        ├── submit / cancel
        └── order queries
```

验收条件：

- 新代码不再以裸字符串表示 exchange、product、transport；
- reference 通过 public market access 获取；
- execution 通过 private account access 获取；
- usecase 不 import integration services；
- composition 只创建一个 Binance Spot System connection；
- transport 和 vendor payload 完全留在 connection service 内部；WebSocket 采用懒连接，不在 `connect()` 或 `start()` 时强制访问网络；
- 旧 broker/exchange/provider resolver 不再被新调用方使用；
- 旧 facade、registry alias 和 raw protocol 在切片完成后删除。

## 18. 最终原则

Participant 是 Integration Domain 的稳定身份模型，不应被丢弃；它需要从字符串包装升级为 typed identity。

Integration 的粗粒度边界是：

```text
IntegrationConnection
├── PublicMarketAccess
└── PrivateAccountAccess
```

业务模块仍然按照业务意图存在：

```text
market
reference
account
execution
```

但它们共享产品级 connection 的不同能力视图。

最终原则是：

> Participant 表达外部是谁以及它承担的角色；ExchangeProductRef 表达 exchange 提供哪条产品线；Access 表达以公开还是私有权限访问；Transport 表达如何连接；Usecase 表达系统要完成什么业务。

## 19. Participant 三角色的最终约束

本节覆盖前文中所有仍使用“单一产品 connection”简写的地方，是 Participant 设计的最终约束。

### Exchange

Exchange 是交易场所，负责：

- 盘口和公开行情规范；
- symbol、market rule 和交易状态；
- 撮合相关的订单参数和执行规则；
- exchange-side order endpoint；
- public market stream。

典型对象：

```text
BinanceSpotConnectionService
├── BinanceSpotPublicMarketService
├── BinanceSpotPrivateAccountService
└── BinanceSpotExecutionVenueService
```

### Broker

Broker 是账户提供方，负责：

- credential 和账户身份；
- 余额和账户快照；
- 账户订单与成交；
- 持仓和保证金状态；
- private user stream；
- 以账户权限提交或撤销交易操作。

典型对象：

```text
BinanceSpotConnectionService
└── BinanceSpotPrivateAccountService
```

### Provider

Provider 是数据提供方，负责：

- 历史行情；
- 实时行情；
- 市场目录或 reference 数据；
- 公司行动、新闻或其它外部数据。

典型对象：

```text
MassivePublicMarketConnectionService
└── PublicMarketAccess
```

### 三者的组合

业务 route 不应把三种角色强行合并：

```text
MarketRoute
    source = ExchangeRef | ProviderRef
    product = ProductFamily | None

AccountRoute
    broker = BrokerRef
    product = ProductFamily

ExecutionRoute
    broker = BrokerRef
    exchange = ExchangeRef
    product = ProductFamily
```

当 Binance 同时承担 exchange 和 broker 角色时，允许两个 participant 使用相同的外部名称，但必须是不同的 typed identity：

```text
ParticipantRef(EXCHANGE, ExchangeId.BINANCE)
ParticipantRef(BROKER, BrokerId.BINANCE)
```

这不是重复建模，而是保留了盘口/交易场所和账户/凭证两个不同边界。

因此最终 Integration 结构是：

```text
Integration
├── participant
│   ├── exchange
│   ├── broker
│   └── provider
├── product
├── access
└── transport
```

而不是把所有能力归到一个无角色的 `BinanceConnection` 中。

## 20. Connection 对外，ConnectionService 对内

前文中的 participant-specific connection service 只应作为 Integration services 内部实现概念，不能成为 Integration application 对外提供的对象。

Integration 的真正对外对象是 System connection：

```text
System
  └── IntegrationConnection
      ├── lifecycle
      ├── health
      ├── participant bindings
      └── typed Access bindings
```

### 20.1 Connection service 的正确定位

新的内部实现不使用一个过大的 participant connection service，而使用 connection service 组合更小的 client、operations、stream 和 translator。

Connection service 负责：

- 调用 Binance、OKX、IBKR、Massive 等外部接口；
- 处理 REST/WebSocket/SDK 差异；
- 转换请求、响应和事件；
- 组装并绑定消费方定义的 protocol；
- 处理 vendor error、签名、重试和字段兼容。

Connection service 不负责：

- 对外暴露 Integration application API；
- 取代 System 的生命周期根对象；
- 被业务模块直接构造；
- 作为全局 resolver 返回的万能对象；
- 管理账户账本、订单状态机或业务订阅状态。

推荐的内部结构：

```text
BinanceSpotConnectionService
├── BinanceSpotPublicMarketService
├── BinanceSpotPrivateAccountService
└── BinanceSpotExecutionVenueService

MassiveMarketConnectionService
└── MassivePublicMarketService
```

这些对象都属于 `services/`，其具体类名和组合方式可以随实现变化。

### 20.2 Connection 的正确定位

Connection 是一个 System-scoped 的长期外部资源，负责：

- 表达本次连接使用的 Participant 和 product；
- 持有一个或多个底层 connection service；
- 管理 REST client、WebSocket session 和 user stream；
- 暴露 start、stop、reconnect、health；
- 通过 `PublicMarketAccess` 和 `PrivateAccountAccess` 描述可用的具体访问对象；
- 记录 connection state、transport、credential scope 和远端订阅状态。

Connection 不拥有业务状态机。它只拥有外部资源和技术连接状态。

### 20.3 推荐的 application API

Integration application 对外只提供 connection assembly：

```python
@dataclass(frozen=True, slots=True)
class IntegrationConnectionSpec:
    connection_id: str
    market_source: ParticipantRef | None
    account: AccountRoute | None
    execution: ExecutionRoute | None
    credential: CredentialRef | None
    mode: RuntimeMode


class IntegrationConnectionApplication(Protocol):
    def connect(self, spec: IntegrationConnectionSpec) -> IntegrationConnectionAssembly:
        ...
```

`IntegrationConnection` 是 application-facing 的 system resource：

```python
class IntegrationConnection(Protocol):
    @property
    def identity(self) -> ConnectionIdentity: ...

    @property
    def state(self) -> ConnectionState: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def reconnect(self) -> None: ...
    def health(self) -> ConnectionHealth: ...
```

具体的 `BinanceSpotConnectionService`、client 和 translator 不出现在这个 API 中。

### 20.4 具体 Access 类型如何注入 usecase

Connection 由 System 持有，composition 从 Integration connection assembly 获取具体的 Access 类型，并注入业务组件：

```text
integration.application.connect(spec)
        ↓
IntegrationConnectionAssembly
        ├── connection lifecycle resource
        ├── public_market: PublicMarketAccess | None
        └── private_account: PrivateAccountAccess | None
        ↓
composition
        ├── market application
        ├── reference application
        ├── account application
        └── execution application
        ↓
System
```

这里返回的是 connection、typed bindings 和具体 Access 类型，不是 concrete connection service、client 或 translator。`PrivateAccountAccess` 内部可以带有 `ExecutionRoute`，因此 execution 不需要再成为第三种独立类型。Composition 根据 Access 类型将自身定义的 usecase protocol 注入业务组件；System 只持有 Connection；connection service 始终留在 Integration services 内部。

`IntegrationConnectionAssembly` 是 runtime/composition assembly API，不是业务 usecase API。它可以提供多个业务协议的绑定，但不能暴露 connection service、SDK、raw payload 或 resolver。

### 20.5 三类 Participant 如何进入一个 Connection

一个交易系统的 connection spec 可以同时指定三类 Participant：

```text
ConnectionSpec
├── market_source
│   └── ExchangeRef(BINANCE) 或 ProviderRef(MASSIVE)
├── broker
│   └── BrokerRef(BINANCE)
├── execution_exchange
│   └── ExchangeRef(BINANCE)
└── product
    └── Spot
```

因此：

```text
public market
    -> market_source

private account
    -> broker

execution
    -> broker account
    + execution_exchange venue
```

当市场数据来自 Massive、账户来自 Binance、订单仍然提交到 Binance 时，Connection 可以表达：

```text
market_source = Massive
broker = Binance
execution_exchange = Binance
```

这比单一 `BinanceConnection` 更准确，也比让业务模块直接选择多个 connector 更稳定。

### 20.6 目标目录修正

Integration 的 application 目录应以 Connection 为中心：

```text
infrastructure/integrations/
├── application/
│   ├── connections.py
│   └── assembly.py
├── protocol.py
├── domain/
│   ├── participants.py
│   ├── products.py
│   ├── bindings.py
│   ├── connections.py
└── services/
    ├── connections/
    │   └── base.py
    ├── connection_services/
    │   └── binance_spot.py
    ├── clients/
    ├── operations/
    ├── streams/
    ├── endpoints/
    ├── translators/
    ├── drivers/
    └── factories/
```

这里：

- `application/connections.py` 是对外 connection API；
- `services/connections/` 负责提供具体 System connection；
- `services/connection_services/` 负责把多个内部实现组装为一个 connection；
- `services/clients/`、`operations/`、`streams/`、`translators/` 是更小粒度的私有实现；
- `services/endpoints/` 和 `services/drivers/` 是更底层的外部接口实现；
- `domain/` 只表达 typed identity、binding、connection state 和 access scope。

### 20.7 对此前文档术语的修正

从本节开始，文档中的术语按以下方式理解：

```text
Integration application
    提供 IntegrationConnection / ConnectionAssembly

Integration services
    持有并组合各类 ConnectionService、Client、Operations、Stream 和 Translator

System
    持有 IntegrationConnection，管理其生命周期

Composition
    从 ConnectionAssembly 获取业务 protocol 实现并注入 usecase

Usecase
    只依赖自身 protocol，不知道 ConnectionService 和 vendor client
```

因此，后续代码不得新增：

```python
IntegrationApplication -> IntegrationConnection
```

而应新增：

```python
IntegrationApplication -> IntegrationConnection
```

`BinanceSpotConnectionService` 是内部实现，不应直接成为 application API。其内部按具体 Access 类型拆分为：

```text
BinanceSpotConnectionService
├── BinanceSpotRestClient
├── BinanceSpotOrderOperations
├── BinanceSpotUserStream
└── BinanceSpotPayloadTranslator
```

exchange 和 provider 侧分别使用：

```text
BinanceSpotPublicMarketService
BinanceSpotExecutionVenueService
MassivePublicMarketConnectionService
```

这些对象只能出现在：

```text
infrastructure/integrations/services/
```

并且只能被 Integration connection service、factory 和测试 fixture 使用。

### 20.8 内部命名规则

```text
IntegrationConnection
    System-facing 的长期连接资源

*ConnectionService
    Integration 内部的连接组装和生命周期协调实现

*RestClient / *RequestClient
    REST 或 request-response transport 封装

*OrderOperations / *AccountOperations
    一组相近的外部操作

*UserStream / *MarketStream
    WebSocket 长连接实现

*Translator
    vendor payload 与系统模型之间的转换

*Endpoint
    单个外部接口或 request/response 定义
```

不再新增：

```text
GenericPrivateAccountConnectionService
GenericPublicMarketConnectionService
```

这里的旧目录名仅用于迁移对照。新实现不再使用一个含义过宽的连接实现名称；连接生命周期由 `*ConnectionService` 负责，外部接口分别由 `*Client`、`*Operations`、`*Stream`、`*Translator` 负责。
# Superseded design note

This document describes an earlier multi-route Access assembly and is kept as
historical context only. The active design is the single-link model in
[`docs/integration-capability-lifecycle.md`](integration-capability-lifecycle.md):
one connection owns one participant, access scope, transport, lifecycle, and
its directly implemented business methods.
