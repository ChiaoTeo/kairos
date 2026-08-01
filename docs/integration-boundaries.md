# Integration 边界定义

本文定义 KairosPy 中 `exchange`、`broker`、`provider`、`driver`、`connector` 的定位，以及新增外部接口时应该放在哪里。目标是让 integration 层按业务边界组织，而不是按历史实现或第三方 SDK 名字随意扩展。

应用端口、领域类型、raw payload 的依赖方向见 `docs/application-ports-and-domain-boundaries.md`。本文只定义 infrastructure integration 内部的参与方和目录归属。

## 核心原则

Integration 层只负责把外部系统适配成 KairosPy 的端口和领域模型。业务流程、运行模式、风控、账户路由、策略订阅不应写进 connector。

按下面的顺序判断归属：

1. 先判断外部系统在业务上扮演什么角色：交易场所、经纪商、数据供应商，还是底层技术驱动。
2. 再判断要实现的能力：公开行情、reference、账户、下单、私有流、历史数据、corporate actions。
3. 最后才选择目录和文件名。

不要因为某个第三方库叫 `exchange`，就把所有能力都放到 `connectors/exchange/`。例如 CCXT 把下单和行情都挂在 exchange object 上，但 KairosPy 仍应把公开市场能力和账户执行能力分开建模。

## 参与方定义

### Exchange

`exchange` 是交易场所或撮合场所，负责公开市场结构和交易规则。

典型能力：

- symbol、market、contract、tick size、lot size、listing/delisting
- 公开行情：ticker、trade、order book、kline、funding、greeks
- 市场状态：trading status、session、halt、delist schedule

示例：

- `binance` crypto venue
- `okx`
- `hyperliquid`
- `nasdaq`
- `nyse`

应放在：

```text
kairospy/infrastructure/integrations/connectors/exchange/<exchange>/
```

适合的文件名：

```text
market_data.py
reference.py
rules.py
streams.py
```

不应放在 exchange 下：

- 账户余额
- 下单、撤单
- 私有订单流
- 账户权限、disclaimer、KYC、PDT、margin eligibility

### Broker

`broker` 是账户、资金、托管和交易执行入口。Broker 可以直接接入一个 exchange，也可以接入多个 venue，或者提供 broker 自己定义的产品线。

典型能力：

- 账户余额、持仓、open orders、closed orders、fills
- 下单、撤单、改单
- 私有订单流、账户流、listen key
- 交易资格、账户限制、disclaimer、风控前置检查
- book/product 路由，例如 spot、margin、usd_m_futures、equity

示例：

- `binance` broker
- `okx` broker
- `ibkr` broker

应放在：

```text
kairospy/infrastructure/integrations/connectors/broker/<broker>/
```

适合的文件名：

```text
account.py
execution.py
market_data.py
reference.py
private_streams.py
<product>_account.py
<product>_execution.py
<product>_market_data.py
<product>_reference.py
```

`broker/<broker>/market_data.py` 只用于 broker 专属行情，例如 Binance Stocks 的 equity quote。如果是公开交易所行情，优先放在 `exchange/<exchange>/market_data.py`。

### Provider

`provider` 是非交易执行的数据供应方。Provider 不拥有 KairosPy 的交易账户，也不负责下单。

典型能力：

- reference universe
- 历史行情
- corporate actions
- 新闻、基本面、财报、宏观数据
- 第三方归一化数据

示例：

- `massive`

应放在：

```text
kairospy/infrastructure/integrations/connectors/provider/<provider>/
```

适合的文件名：

```text
reference.py
market_data.py
corporate_actions.py
news.py
fundamentals.py
```

### Driver

`driver` 是底层技术适配层，不代表 KairosPy 的业务参与方。

典型能力：

- 封装第三方 SDK
- HTTP request、签名、分页、rate limit、重试
- websocket client
- payload 原始读取

示例：

- `CcxtDriver`
- `MassiveDriver`
- Binance SAPI client

应放在：

```text
kairospy/infrastructure/integrations/drivers/
```

或者在某个 broker/provider connector 内部作为私有技术 client：

```text
kairospy/infrastructure/integrations/connectors/broker/binance/sapi.py
```

Driver 不应直接泄漏到 `core`。Application 可以在 composition/factory 层创建 driver，但运行时服务应依赖 application port。Integration protocol 只作为 connector/gateway/translator 层的原始能力接口。

### Connector

`connector` 是某个参与方的具体能力实现。它把外部系统返回的 payload 转成 KairosPy 的领域模型、领域事件，或 application port 需要的 DTO。

命名建议：

- 类名包含参与方和能力：`BinanceMarketDataConnector`、`MassiveReferenceConnector`
- 执行类优先使用 broker 语义：`BinanceBroker`、`OkxBroker`、`BinanceEquityBroker`
- product-specific 能力带产品名：`BinanceEquityReferenceConnector`

## 接口归属

KairosPy 有两层接口，不要混用。

### Application ports

`kairospy/application/ports/` 定义应用运行时依赖的端口。它们表达 KairosPy 内部用例，不表达第三方 API 形状。

适合放这里：

- `MarketRuntime`
- `ExecutionRuntime`
- `AccountRuntime`、`AccountCatalog`
- `ReferenceRuntime` / `ReferenceCatalogSource`
- 订阅、运行时事件、执行计划等应用级抽象

这些接口应由 application runtime/service 使用。Connector 不需要实现完整 application port；通常由 capability-specific gateway 或 runtime role implementation 包装 connector。

### Integration protocols

`kairospy/infrastructure/integrations/protocols.py` 定义 connector 需要满足的低层能力接口。它是过渡兼容层，不是新增 application service 的首选依赖。

适合放这里：

- `HistoricalMarketDataClient`
- `RawMarketDataGateway`
- `RawReferenceGateway`
- `AccountBalanceClient`
- `OrderQueryClient`
- `OrderExecutionClient`
- `AccountBootstrapClient`
- `PrivateAccountStream`
- 不再定义 `BrokerClient` compatibility aggregate

这些接口应足够接近外部系统能力，但返回值仍应是 KairosPy 可解析的 mapping/event，而不是 SDK 专属对象。它们只能停留在 `infrastructure/integrations`，不能作为 application service 的公共依赖。

### Protocol design

Integration protocol 应表达能力，不应表达参与方身份。

不推荐定义大而全的身份接口：

```python
class ExchangeProtocol(Protocol): ...
class BrokerProtocol(Protocol): ...
class ProviderProtocol(Protocol): ...
```

这些名字看似清楚，但会把身份和能力绑死。现实里一个参与方常常同时提供多种能力：Binance crypto 既有 exchange public market data，也有 broker execution；Binance Stocks 的行情和 reference 来自 broker product endpoint；Massive 是 provider，但也能提供 historical market data 和 reference。

推荐拆成小能力接口：

```python
class IntegrationParticipant(Protocol):
    name: str


class RawReferenceGateway(Protocol):
    def fetch_markets(...): ...


class HistoricalMarketDataClient(Protocol):
    def fetch_ohlcv(...): ...


class RawMarketDataGateway(Protocol):
    def watch_ticker(...): ...
    def watch_order_book(...): ...
    def watch_trades(...): ...


class OrderExecutionClient(Protocol):
    def create_order(...): ...
    def cancel_order(...): ...


class OrderQueryClient(Protocol):
    def fetch_open_orders(...): ...
    def fetch_closed_orders(...): ...


class AccountBalanceClient(Protocol):
    def fetch_balance(...): ...


class PrivateAccountStream(Protocol):
    def watch_orders(...): ...
    def watch_my_trades(...): ...
    def watch_balance(...): ...
```

组合规则：

- Connector 可以实现一个或多个能力 protocol。
- Factory 可以按 account/book/source 选择 connector，再把 connector 注入到需要该能力的 runtime service。
- Runtime service 应依赖最小能力接口。例如只查询余额的服务依赖 `AccountBalanceClient`，不应要求完整 `BrokerClient`。
- 参与方身份通过 `core/reference` 的 `BrokerId`、`ExchangeId`、`ProviderId` 或 connector metadata 表达，不通过 protocol 名称表达。
- 不应为了兼容保留 `BrokerClient` 作为新的 application-facing aggregate。

`BrokerClient` 把下单、撤单、余额、订单查询绑定在一起，适合 CCXT-style broker，但不适合能力分阶段启用的 connector。例如 Binance equity 当前可以查询 open orders 和 reference，但 live order placement 尚未启用；这种 connector 不应该被迫实现无意义的 `NotImplementedError`。

目标做法是让 resolver 或 composition factory 按实际能力创建窄接口的 Gateway、Translator 和 Runtime。迁移某个能力时，所有调用方一次性切换到目标协议，完成后删除 `BrokerClient` aggregate，不保留新旧协议并存的兼容阶段。

### Core model

`kairospy/core/` 只放领域模型和领域服务，不依赖 infrastructure。

适合放这里：

- `MarketRef`
- `AccountBookRef`
- `BrokerId`、`ExchangeId`、`ProviderId`
- `OrderRequest`
- `AccountSnapshot`
- `ReferenceCatalog`

Core 可以知道 broker/exchange/provider 的概念，但不能知道 Binance、CCXT、Massive 这些具体实现。

## 放置决策表

| 要新增的东西 | 放置位置 |
| --- | --- |
| 新交易所公开行情 | `connectors/exchange/<exchange>/market_data.py` |
| 新交易所 reference/market rules | `connectors/exchange/<exchange>/reference.py` |
| 新 broker 下单/撤单 | `connectors/broker/<broker>/execution.py` |
| 新 broker 账户余额/持仓 | `connectors/broker/<broker>/account.py` |
| 新 broker 私有订单流 | `connectors/broker/<broker>/private_streams.py` |
| 新 broker 专属产品线 | `connectors/broker/<broker>/<product>_*.py` |
| 新数据供应商 reference | `connectors/provider/<provider>/reference.py` |
| 新数据供应商 corporate actions | `connectors/provider/<provider>/corporate_actions.py` |
| 第三方 SDK 封装 | `drivers/` 或 connector 内部私有 client |
| payload 到领域模型转换 | `infrastructure/integrations/payloads/` |
| 应用运行时依赖的端口 | `application/ports/` |
| 参与方 id 和领域身份 | `core/reference/` |
| live/paper/backtest 工厂选择 | `application/service/modes/common/integrations.py` |

## 命名规则

使用这些词时保持一致：

- `venue`：市场发生的地点或市场引用字段，通常对应 exchange 或 broker 暴露的 venue。
- `exchange`：公开市场和撮合场所。
- `broker`：账户和执行入口。
- `provider`：外部数据供应商。
- `source`：具体数据来源或 connector 输出来源，可比 participant 更细。
- `book`：broker account 下的产品/账本，例如 `spot`、`margin`、`equity`。
- `market`：市场类型，例如 `spot`、`swap`、`option`、`equity`。
- `driver`：技术实现，不参与业务路由。

避免这些命名：

- 用 `provider` 指代 broker。旧配置兼容可以保留，但新代码应使用 `broker`。
- 用 `exchange` 指代 broker 下单入口。
- 用 `market` 决定 broker factory。Broker 应由 account/book 决定。
- 在 connector 文件名里使用第三方 SDK 名字作为业务名，例如 `ccxt_broker.py`。CCXT 是 driver，不是 broker。
- 在 account/book routing 中使用 `provider` 表达 broker。新代码应使用 `broker=`；`provider=` 只作为旧调用兼容。

## Binance 现状和约定

当前 Binance 有两类能力：

```text
connectors/exchange/binance/
  market_data.py  # crypto public market data/reference via CCXT
  reference.py    # crypto public reference/lifecycle

connectors/broker/binance/
  crypto_execution.py
  sapi.py
  equity_reference.py
  equity_market_data.py
  equity_execution.py
```

`connectors/exchange/binance/` 下不应出现 broker/account/private stream 能力。Binance broker 能力的唯一入口是 `connectors/broker/binance/`。

后续推荐补齐目标：

```text
connectors/broker/binance/
  crypto_account.py
  crypto_private_streams.py
  equity_execution.py
  equity_account.py
  equity_market_data.py
  equity_reference.py
```

## Binance 接入示例

接入 Binance 时先拆能力，不要把所有 Binance 能力放进同一个 connector。

Binance crypto public market data/reference 属于 exchange 能力：

```text
connectors/exchange/binance/
  market_data.py
  reference.py
```

Binance crypto account/execution/private stream 属于 broker 能力：

```text
connectors/broker/binance/
  crypto_account.py
  crypto_execution.py
  crypto_private_streams.py
```

Binance equity 属于 Binance broker 的 equity product endpoint，不是普通 crypto exchange market type：

```text
connectors/broker/binance/
  sapi.py
  equity_account.py
  equity_execution.py
  equity_market_data.py
  equity_reference.py
  equity_streams.py
```

调用方不直接 import Binance connector。策略和应用层只表达业务意图：

```python
context.subscribe(
    MarketRef.ephemeral(
        venue="binance",
        market="spot",
        source_symbol="BTC/USDT",
    )
)
```

如果订阅 Binance equity：

```python
context.subscribe(
    MarketRef.ephemeral(
        venue="binance",
        market="equity",
        source_symbol="AAPL",
    )
)
```

账户配置用 broker account book 表达交易账户能力：

```toml
[accounts.main]
ref = "binance_main"
books = ["spot"]
trade = true
```

Binance equity account book：

```toml
[accounts.main]
ref = "binance_main"
books = ["equity"]
trade = false
```

行情调用链：

```text
Strategy context.subscribe(...)
  -> application MarketStreamGateway
  -> market feed resolver
  -> venue=binance, market=spot   -> Binance exchange market data connector
  -> venue=binance, market=equity -> Binance equity market data connector
  -> CcxtDriver / BinanceSapiClient
```

交易调用链：

```text
Strategy emits intent/order request
  -> application ExecutionRuntime
  -> account/book broker resolver
  -> AccountBookRef("binance", "main", "spot")   -> Binance crypto execution connector
  -> AccountBookRef("binance", "main", "equity") -> Binance equity execution connector
```

Reference refresh 调用链：

```text
reference sync --exchange binance --market spot
  -> Binance exchange reference connector

reference sync --broker binance --book equity
  -> Binance equity broker reference connector

reference sync --provider massive
  -> Massive provider reference connector
```

Reference sync 输出应使用来源语义：

```text
source_kind=exchange, source=binance     # exchange reference
source_kind=provider, source=massive     # data provider reference
```

旧字段 `provider` 可以暂时保留为输出兼容，但不要再用它判断 Binance/Hyperliquid 这类 exchange sync 的身份。

`IntegrationResolver` 是唯一应该 import 具体 Binance/OKX/Massive connector 的位置。策略、CLI facade、runtime service 应依赖 application port。Integration capability protocol 只用于 connector/gateway/translator 边界；迁移完成后不保留兼容桥接。

具体 connector 不从聚合 package root 再导出。不要从下面这些入口拿具体 Binance/OKX/Massive/IBKR connector：

```python
from kairospy.infrastructure.integrations import BinanceBroker
from kairospy.infrastructure.integrations.connectors import BinanceMarketDataConnector
```

需要手动测试或 connector 层单测时，直接 import 具体归属路径：

```python
from kairospy.infrastructure.integrations.connectors.broker.binance import BinanceBroker
from kairospy.infrastructure.integrations.connectors.exchange.binance import BinanceMarketDataConnector
```

应用、CLI、runtime 代码则不直接 import 具体 connector，应通过 `IntegrationResolver` 或 facade capability factory 获取。

示例 resolver：

```python
def market_feed_for(ref: MarketRef) -> RawMarketDataGateway:
    if str(ref.venue) == "binance" and str(ref.market) == "spot":
        return BinanceMarketDataConnector(CcxtDriver())
    if str(ref.venue) == "binance" and str(ref.market) == "equity":
        return BinanceEquityMarketDataConnector.from_credential(...)
    raise ValueError(f"unsupported market feed: {ref}")


def order_execution_for(book: AccountBookRef) -> OrderExecutionClient:
    if str(book.broker) == "binance" and str(book.book) == "spot":
        return BinanceCryptoExecution.from_credential(...)
    if str(book.broker) == "binance" and str(book.book) == "equity":
        return BinanceEquityBroker.from_credential(...)
    raise ValueError(f"unsupported order execution book: {book}")
```

使用规则：

- 市场数据按 `MarketRef(venue, market, source_symbol)` 路由。
- 交易执行按 `AccountBookRef(broker, account, book)` 路由。
- Reference refresh 按 `exchange`、`broker/book` 或 `provider` 路由。
- 策略不选择 connector，也不传 driver 参数。
- Application runtime 不依赖第三方 SDK，也不新增对 integration protocol 的依赖；新增能力优先定义 application port。

## 工厂和路由规则

Integration connector 的统一装配入口是：

```text
kairospy/infrastructure/integrations/resolver.py
```

`IntegrationResolver` 负责把 application/facade 传入的业务引用解析成具体 connector：

```python
market_feed_for_subscription(spec) -> RawMarketDataGateway
account_balance_for_book(book, credential) -> AccountBalanceClient
order_query_for_book(book, credential) -> OrderQueryClient
order_execution_for_book(book, credential) -> OrderExecutionClient
account_bootstrap_for_book(book, credential) -> AccountBootstrapClient
private_account_stream_for_book(book, credential) -> PrivateAccountStream
broker_for_book(book, credential) -> narrow raw account/execution capability
reference_data(ReferenceSourceRef(...)) -> RawReferenceGateway
```

当前实现的转换链路是：

```text
RawMarketDataGateway -> MarketStreamAdapter -> MarketStreamGateway -> MarketRuntime
RawReferenceGateway -> ReferenceCatalogAdapter -> ReferenceCatalogSource -> reference use case
raw account/order gateways -> account payload adapter -> account runtime/service
```

其中 `ReferenceCatalogSource.fetch_catalog()` 返回 `ReferenceCatalog`，因此 reference application service 不再接触 `fetch_markets()` 的原始行。市场、账户和执行 runtime 的 `events()` 也分别使用具体的 `MarketRuntimeEnvelope`、`AccountRuntimeEnvelope`、`ExecutionRuntimeEnvelope`，不会退化为无参数的 `RuntimeEnvelope`。

Application common factory、system facade resource factory、reference sync CLI 不应各自手写 Binance/OKX/Massive 的 connector 选择逻辑；它们应委托 resolver。

Runtime 工厂按 account/book 选择 broker：

```text
AccountBookRef("binance", "main", "spot")   -> Binance crypto broker connector
AccountBookRef("binance", "main", "equity") -> Binance equity broker connector
AccountBookRef("okx", "main", "swap")       -> OKX broker connector
```

System facade 和 CLI facade 中，broker factory 应使用 broker 语义命名，例如 `BrokerName`、`broker_name`、`account broker/provider`。不要用 `ExchangeName` 或 `exchange_name` 作为下单、余额、订单查询入口的参数名；旧调用可以兼容，但新代码应走 broker 名称。

Account book route 应使用 broker 语义：

```python
account_book_route(AccountBookRef("binance", "main", "spot"), broker="binance")
```

`provider=` 是历史 alias，仅用于兼容旧调用。

Workspace account 和 credential 记录也应以 broker 为主语义：

```toml
[account]
broker = "binance"
environment = "live"

[credential]
broker = "binance"
kind = "api_key_secret"
```

读取旧文件时可以接受 `provider`，但当 `broker` 和 `provider` 同时存在时必须以 `broker` 为准。Python 代码应优先读 `.broker`；`.provider` 只作为旧字段 alias 保留。

公开市场数据 feed 可以按 market subscription 的 `MarketRef.venue` 和 `MarketRef.market` 选择：

```text
venue=binance, market=spot   -> Binance exchange market data connector
venue=binance, market=equity -> Binance equity broker market data connector, if source is Binance Stocks
venue=nasdaq, market=equity  -> provider or future exchange connector, depending on source
```

Live/paper runtime 不应只在配置加载时按账户 venue 固定一个 market feed。默认路径应把 `MarketDataSubscriptionSpec` 交给 resolver，由 resolver 根据 `spec.market` 选择 feed。旧的 `market_feed_factory(venue)` 可以作为兼容入口保留，但应包装成 subscription resolver：

```text
MarketDataSubscriptionSpec.market.venue=binance, market=spot
  -> BinanceMarketDataConnector

MarketDataSubscriptionSpec.market.venue=binance, market=equity
  -> BinanceEquityMarketDataConnector
```

Binance equity 当前至少应支持 quote/ticker 订阅；order book、trade stream、option greeks 没有实现时要显式报不支持，而不是静默落到 crypto exchange feed。

Reference refresh 使用 `ReferenceSourceRef` 按数据来源选择：

```text
provider=massive             -> Massive provider reference connector
exchange=binance, market=spot -> Binance exchange reference connector
broker=binance, book=equity  -> Binance equity broker reference connector
```

## 代码评审检查清单

新增 integration 代码时检查：

- 是否先明确了参与方类型：exchange、broker、provider、driver。
- 是否把账户/下单/private stream 放在 broker 下。
- 是否把公开行情/reference/rules 放在 exchange 或 provider 下。
- 是否避免让 `core` import `infrastructure`。
- 是否让 application runtime 依赖 application port，而不是直接依赖第三方 SDK 或 integration protocol。
- 是否把 payload 解析放在 `payloads/` 或 connector 私有 parser 中。
- 是否有明确的 factory 路由入口，而不是在业务流程里散落 `if venue == ...`。
- 是否明确本次切片完成后删除旧 alias 和旧 import 入口。
- 是否避免在 `infrastructure.integrations` 或 `connectors` package root 再导出具体 connector。

## 当前推荐落地顺序

1. 新增 broker/account/execution/private stream 能力时，一律放到 `connectors/broker/<broker>/`。
2. 新增 exchange public market data/reference/rules 时，一律放到 `connectors/exchange/<exchange>/`。
3. 具体 connector 选择一律通过 `IntegrationResolver`，不要在 runtime/facade/CLI 分散 `if name == ...`。
4. 新 runtime service 依赖目标 runtime role；application service 依赖小 application port，不引入 integration capability protocol 或 `BrokerClient` aggregate。
5. 为 Binance/OKX 等 broker 补齐 account/private stream 的产品线文件，并通过 resolver 注册路由。
