# Binance 美股交易接入技术方案

本文档总结 Binance Stocks Trading API 的官方架构，并给出 KairosPy 接入美股/ETF 交易的领域建模、模块拆分、改造范围和分阶段实施计划。

## 背景

Binance 已推出 Stocks Trading API，用于通过 Binance 账户交易 US-listed stocks 和 ETFs。该能力不是 Binance crypto spot/futures 的一个普通 market type，也不是当前 CCXT Binance connector 已覆盖的交易对形态。它更接近 Binance 作为 brokerage 入口，对接美股/ETF 交易、账户限制、结算和 tokenized stock 转换服务。

因此，KairosPy 接入时应将 Binance 视为 broker，并在 broker 下新增 equity 产品线，而不是把它简单建成 `exchange=binance, type=equity` 后复用现有 CCXT 适配层。

## 官方接口架构

Binance Stocks Trading 使用 SAPI endpoint family：

- REST base URL: `https://api.binance.com`
- REST prefix: `/sapi/v1/equity/*`
- WebSocket base URL: `wss://nbstream.binance.com/equity`
- Symbol: 裸美股 ticker，例如 `AAPL`、`NVDA`、`TSLA`、`SPY`、`QQQ`
- Quote asset: 默认 `USDC`
- Price: USD 语义
- Timestamp: UTC epoch milliseconds
- Signing: 复用 Binance SAPI 的 HMAC / Ed25519 签名机制

官方 endpoint 分组：

| 分组 | 能力 |
| --- | --- |
| Market Data | Exchange Info, Tokenized Assets, Latest Quote |
| Trade | Place Order, Cancel Order, Cancel All Orders, Current Open Orders, Order History, Order Detail, Trade History |
| Tokenized | Mint, Redeem, Convert Status, Convert History |
| Account | Sign US Equity Disclaimer |
| User Data Streams | Listen Key, Order Report |
| WebSocket Streams | Price, Quote, Kline, Calendar, Trading Status, Tradability |

关键 REST endpoint：

| 能力 | Method | Path | 备注 |
| --- | --- | --- | --- |
| 交易规则/标的列表 | GET | `/sapi/v1/equity/market/exchangeInfo` | 需要 `X-MBX-APIKEY`，不需要签名 |
| tokenized assets | GET | `/sapi/v1/equity/market/tokenized-assets` | underlying equity 与 tokenized asset 映射 |
| 最新 quote | GET | `/sapi/v1/equity/market/quote` | 单 ticker bid/ask，最多约 5 秒 stale |
| 下单 | POST | `/sapi/v1/equity/order/place` | signed TRADE endpoint |
| 撤单 | POST | `/sapi/v1/equity/order/cancel` | signed TRADE endpoint |
| 当前 open orders | GET | `/sapi/v1/equity/order/open` | signed USER_DATA endpoint |
| 订单历史 | GET | `/sapi/v1/equity/order/history` | signed USER_DATA endpoint |
| 订单详情 | GET | `/sapi/v1/equity/order/detail` | signed USER_DATA endpoint，含 trades |
| 成交历史 | GET | `/sapi/v1/equity/trade/history` | signed USER_DATA endpoint |
| 签署免责声明 | POST | `/sapi/v1/equity/account/disclaimer` | 下单前必须完成 |

关键 WebSocket stream：

| Stream | 格式 | 示例 |
| --- | --- | --- |
| 所有 symbol price | `price` | `price` |
| 单 symbol quote | `{SYMBOL}@quote` | `AAPL@quote` |
| Kline | `{SYMBOL}@kline_{interval}` | `AAPL@kline_5m` |
| Calendar | `calendar` | `calendar` |
| Trading status | `{SYMBOL}@tradingStatus` | `AAPL@tradingStatus` |
| Tradability | `{SYMBOL}@tradability` | `AAPL@tradability` |
| 用户订单回报 | `{listenKey}@orderReport` | `{listenKey}@orderReport` |

WebSocket kline 只发布 `5m`、`1h`、`1d`、`1w`、`1M`，不发布 `1m`。

## 订单语义差异

Binance Stocks 的下单字段组合与 CCXT-style crypto order 明显不同：

| Side | OrderType | Required | Forbidden |
| --- | --- | --- | --- |
| BUY | LIMIT | `price`, `quantity`, `tradingSession` | `notional` |
| BUY | MARKET | `notional` | `price`, `quantity`, `tradingSession` |
| SELL | LIMIT | `price`, `quantity`, `tradingSession` | `notional` |
| SELL | MARKET | `quantity` | `price`, `notional`, `tradingSession` |

需要特别处理：

- `BUY MARKET` 是按金额买入，必须传 `notional`，不能传 `quantity`。
- `LIMIT` 价格最多 2 位小数。
- fractional shares 会触发额外限制。
- fractional `GTC` 必须配 `tradingSession = EXTENDED` 或 `24H`。
- extended hours 只有部分股票支持。
- extended hours 可能只能使用 limit order。
- 下单前账户必须完成 US Equity Disclaimer。
- 错误码分两层：SAPI pre-check negative codes，以及 equity business `486xxx` codes。

## KairosPy 当前相关架构

当前 Binance connector 已按 exchange/broker 边界拆分：

```text
kairospy/infrastructure/integrations/connectors/exchange/binance/
  market_data.py  # crypto public market data via CCXT
  reference.py    # crypto public reference/lifecycle

kairospy/infrastructure/integrations/connectors/broker/binance/
  crypto_execution.py
  sapi.py
  equity_reference.py
  equity_market_data.py
  equity_execution.py
```

现有 crypto 实现主要是 CCXT wrapper：

- `BinanceBroker.create_order()` 调用 `CcxtDriver.create_order(...)`
- `BinanceMarketDataConnector.fetch_markets()` 调用 CCXT `fetch_markets`
- 行情 payload 通过 `payloads/ccxt_market.py` 转成 KairosPy market model
- 执行 payload 通过 `payloads/ccxt_execution.py` 转成 execution update

Binance equity 当前已具备：

- `BinanceSapiClient`：SAPI request、API key、签名、timestamp。
- `BinanceEquityReferenceConnector`：`exchangeInfo` 到 equity reference catalog。
- `BinanceEquityMarketDataConnector`：REST quote polling，并实现 `watch_ticker()`。
- `BinanceEquityBroker`：open/closed orders 查询和 disclaimer 入口；live 下单/撤单仍显式禁用，等待 order 字段翻译和 safety policy 补齐。

执行链路中：

- `LiveExecutionRuntime` 从 `TradeIntent` 生成 `OrderRequest`
- `OrderRequest` 只有 `quantity`、`order_type`、`limit_price`
- `ExecutionCoordinator.submit_order()` 调用 broker 的 `create_order(symbol, side, type, amount, price, params)`

这些边界适合 crypto spot/swap/options，但不能完整表达 Binance Stocks 的 `notional`、`quoteAsset`、`tradingSession`、disclaimer、PDT/fractional/extended-hours 等 brokerage 规则。

## 推荐领域建模

推荐将 Binance 建模为 broker：

```text
broker = "binance"
product = "equity"
market = "equity"
source = "binance_equity"
source_symbol = "AAPL"
quote_asset = "USDC"
```

在 reference model 中：

- `Asset.asset_type = AssetType.EQUITY`
- `InstrumentDefinition.instrument_type = InstrumentType.EQUITY`
- `MarketDefinition.venue = "binance"`
- `MarketDefinition.market = "equity"`
- `MarketDefinition.source_symbol = "AAPL"`
- `MarketDefinition.price_precision = 2`
- `MarketDefinition.amount_tick = exchangeInfo.stepSize`
- `MarketDefinition.min_amount = exchangeInfo.minQty`
- `MarketDefinition.min_notional = exchangeInfo.minNotional`
- `MarketDefinition.metadata` 保留：
  - `tradability`
  - `tradabilityUpdateTime`
  - `overnightSupported`
  - `fractionable`
  - `fractionableEh`
  - `extendedSession`
  - `maxNumOrders`
  - `maxQty`
  - `maxNotional`
  - `multiplierUp`
  - `multiplierDown`
  - `listingTime`
  - `delistingTime`

如果后续接入真实 primary listing venue，可在 metadata 中补充：

- `primary_listing_venue = "NASDAQ"` 或 `"NYSE"`
- `broker = "binance"`
- `settlement_asset = "USDC"`

## 推荐模块拆分

新增 Binance broker/product connector，避免污染现有 CCXT connector：

```text
kairospy/infrastructure/integrations/connectors/broker/binance/
  __init__.py
  sapi.py
  equity_reference.py
  equity_market_data.py
  equity_execution.py
  equity_account.py
  equity_streams.py

kairospy/infrastructure/integrations/payloads/
  binance_equity_reference.py
  binance_equity_market.py
  binance_equity_execution.py
  binance_equity_account.py
```

职责：

| 模块 | 职责 |
| --- | --- |
| `sapi.py` | API key、签名、timestamp、recvWindow、HTTP request、错误 envelope |
| `equity_reference.py` | `exchangeInfo`、`tokenized-assets` 转 KairosPy reference catalog |
| `equity_market_data.py` | REST latest quote 与 public WS quote/kline/tradability |
| `equity_execution.py` | 下单、撤单、订单详情/历史、trade history |
| `equity_account.py` | disclaimer、账户快照、open orders bootstrap |
| `equity_streams.py` | listenKey 创建/续期、orderReport stream |

现有 exchange connector 只保留公开市场能力：

```text
connectors/exchange/binance/
  market_data.py  # crypto CCXT-style
  reference.py
```

## 配置建议

Live launch 配置不声明行情 symbol、stream kind 或 broker order defaults。行情需求由策略通过 `context.subscribe(...)` 声明；账户和 book 决定 broker/product runtime binding；live 配置只保留运行期和风控边界。

```toml
[launch]
id = "binance-equity-aapl-live"
mode = "live"
strategy = "examples.strategies.aapl_strategy:AaplStrategy"

[account]
ref = "binance_main"

[accounts.main]
ref = "binance_main"
books = ["equity"]
trade = false

[strategy.params]
symbol = "AAPL"

[live.safety]
trading_enabled = false
require_limit_orders = true
max_order_notional = "500"
allow_fractional = false
allow_extended_hours = false
```

策略负责构造 market ref 并订阅行情：

```python
from kairospy.application.usecases.strategy import StrategyBase
from kairospy.core.market import Quote
from kairospy.core.reference import MarketRef

class AaplStrategy(StrategyBase):
    strategy_id = "aapl"

    def __init__(self, *, symbol: str = "AAPL") -> None:
        self.market = MarketRef.ephemeral(venue="binance", market="equity", source_symbol=symbol)

    def on_start(self, context) -> None:
        context.subscribe(self.market, selectors=(Quote,), identity=self.strategy_id)
```

## 需要改造的核心点

### 1. Broker 选择

当前 `default_broker("binance")` 返回 CCXT `BinanceBroker`。需要根据 market/product 选择：

```text
binance + account book spot/swap/option -> Binance crypto connector
binance + account book equity           -> Binance equity broker connector
```

broker factory 已经以 `AccountBookRef` 为入口，而不是 `(venue, credential)`。不要通过 `live.market` 选择 broker。

### 2. 行情订阅

行情订阅应只从策略 `context.subscribe(...)` 产生。`live.symbol`、`live.stream`、`live.market_data` 这类配置会让策略逻辑和运行配置漂移，已不作为推荐入口。

`StreamingMarketDataService` 已支持 feed resolver：

```text
MarketFeedResolver.resolve(market_ref) -> MarketStreamGateway
```

解析规则：

```text
binance + market_ref.market=spot   -> Binance crypto market data connector
binance + market_ref.market=equity -> Binance equity market data connector
```

### 3. 私有账户同步

私有同步应由 live account 引用推导：

```text
mode = live
AND resolved account exists
AND broker/account runtime supports private stream
=> enable private sync by default
```

没有私有流时降级到 polling。配置最多提供高级覆盖：

```toml
[live.private_sync]
enabled = false
```

默认不要求用户配置。

### 4. OrderRequest 表达能力

短期方案：

- 保持 `OrderRequest.quantity`
- `BUY MARKET` 的 `notional` 从 strategy intent metadata 或后续扩展的 order intent 传入
- `BinanceEquityBroker.create_order()` 内部按 Binance Stocks 字段翻译

长期方案：

- 在 order domain 增加 notional order 表达：
  - `quantity: Decimal | None`
  - `notional: Decimal | None`
  - 校验 quantity/notional 二选一或按订单类型组合
- 增加 broker-neutral execution params：
  - `quote_asset`
  - `trading_session`
  - `client_order_id`
  - `time_in_force`

### 5. SafetyPolicy

当前 `LiveTradingSafetyPolicy.max_order_notional` 依赖 `quantity * limit_price`。需要支持：

- 对 `notional` 订单直接校验金额
- `BUY MARKET` 必须有 notional 上限
- extended hours 必须明确 opt-in
- fractional 必须明确 opt-in
- 根据 `exchangeInfo.tradability` 做 buy/sell/none 拒单
- 把 `486xxx` 错误映射成可读原因

### 6. MarketData payload

新增 Binance equity quote parser：

```json
{
  "symbol": "AAPL",
  "bidPrice": "180.50",
  "askPrice": "180.52",
  "bidSize": 100,
  "askSize": 200
}
```

映射到 `Quote`：

- `instrument_id = instrument:equity:aapl` 或 reference catalog resolved id
- `market_id = market:binance:equity:aapl`
- `bid = bidPrice`
- `ask = askPrice`
- `bid_size = bidSize`
- `ask_size = askSize`
- `source = "binance"`
- `basis = "quote"`

### 7. Execution payload

订单详情/历史字段：

- `orderId`
- `symbol`
- `quote`
- `side`
- `orderType`
- `limitPrice`
- `avgFilledPrice`
- `qty`
- `notional`
- `filledQty`
- `filledTotal`
- `fee`
- `session`
- `status`
- `createdAt`
- `updatedAt`
- `trades[]`

状态映射：

| Binance status | KairosPy status/event |
| --- | --- |
| `NEW` | `ACKNOWLEDGED` |
| `ACCEPTED` | `ACKNOWLEDGED` |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED` |
| `FILLED` | `FILLED` |
| `CANCELED` | `CANCELED` |
| `EXPIRED` | `EXPIRED` |
| `REJECTED` | `REJECTED` |

成交映射：

- `executionId` -> fill id metadata
- `executionAt` -> fill timestamp
- `price` -> fill price
- `qty` -> fill quantity
- `fee` -> fee amount
- `quote` or default `USDC` -> settlement/fee currency

## 分阶段实施计划

### Phase 1: 只读 reference + quote

目标：让系统能识别 Binance equity market，并消费 AAPL/SPY 等实时 quote。

已完成：

- 新增 SAPI unsigned/key-only client
- 实现 `exchangeInfo` parser
- 实现 `latest quote` parser
- 新增 `market = "equity"` 的 reference/market-data tests
- live/paper market feed resolver 能按 `venue=binance, market=equity` 选择 Binance equity quote feed

未完成：

- `tokenized-assets` parser
- public WS `AAPL@quote`

验收：

- `reference markets browse --venue binance --market equity` 能看到 AAPL 等 symbol
- `market check --exchange binance --market equity --symbol AAPL --kind quote` 通过
- live quote subscription 能通过 resolver 进入 Binance equity quote feed

### Phase 2: Live 下单与轮询

目标：支持保守 live 下单，先不依赖 user stream。

已完成：

- 实现 signed SAPI client
- 实现 `fetch_open_orders`
- 支持 disclaimer connector 入口
- 实现 `BinanceEquityBroker.create_order` 的 Binance Stocks 字段翻译与 signed endpoint 调用
- 实现 `BinanceEquityBroker.cancel_order`
- 实现 `fetch_order_detail`
- 实现 `fetch_trade_history`
- 新增 Binance equity order/trade payload parser 与 fake SAPI transport 测试

未完成：

- 支持 disclaimer command 或 bootstrap check
- 扩展 safety policy
- 增加 order status polling/reconciliation
- 打开 runtime equity order execution route；当前仍保持禁用，等待 safety policy 和 bootstrap check 补齐

验收：

- limit buy/sell 能下单并记录 venue order id
- cancel 能正确进入 canceled 或 cancel pending 状态
- detail/history 能回灌 fills
- disabled trading 默认拒单

### Phase 3: User stream

目标：通过 `{listenKey}@orderReport` 获取私有订单回报。

任务：

- 创建/续期 listenKey
- 接入 orderReport WebSocket
- 自动续期 60 分钟 TTL
- reconnect 后恢复订阅
- parser 映射成 `ExecutionUpdate`

验收：

- live 下单后无需轮询即可更新 acknowledged/filled/canceled
- listenKey 续期失败有明确 runtime event

### Phase 4: Tokenized stock

目标：支持 mint/redeem 和 convert history。

任务：

- 实现 tokenized mint/redeem command
- 实现 convert status/history
- 将 tokenized asset 建成独立 asset/instrument 或 reference metadata
- 增加异步 convert lifecycle projection

验收：

- 可查询 `AAPLB -> AAPL` 映射
- mint/redeem request 可追踪到 terminal status

## 测试建议

单元测试：

- `binance_equity_reference` parser
- `binance_equity_market` quote/kline/tradability parser
- `binance_equity_execution` order/trade parser
- SAPI signing canonical query string
- Binance equity error code mapping

集成测试：

- 使用 fake SAPI transport 验证 request path、method、headers、签名参数
- 使用 fake WebSocket stream 验证 quote/orderReport event 转换
- `accounts.*.books = ["equity"]` 正确选择 equity account route
- 策略 `context.subscribe(..., market="equity")` 正确选择 Binance equity market data gateway/runtime

回归测试：

- 现有 Binance spot/swap/funding 测试不应变化
- `default_broker("binance")` 的 crypto 行为不能被 equity 改造破坏

## 风险与边界

- API 是新功能，字段和限制可能继续变化，应集中封装在 `binance_equity_*` payload translator 中。
- Binance Stocks API 需要 API key 才能查询部分 market data，即使不需要签名。
- 交易资格、地区限制、disclaimer、PDT、sell-only、fractional、extended-hours 都属于 broker/account 级规则，不应硬编码成普通 market rule。
- `BUY MARKET` 按 notional 下单会影响策略意图模型，不能只靠 `quantity` 语义长期支撑。
- WebSocket 没有 subscribe/unsubscribe RPC，stream name 写在 URL 中，订阅管理要和现有 CCXT Pro 模型分开。
- Kline 不支持 `1m`，现有 1m bar 策略不能直接迁移到 Binance equity WS。

## 官方文档

- Binance Stocks Introduction: https://developers.binance.com/en/docs/products/stocks/introduction
- Binance Stocks REST Market Data: https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/market-data
- Binance Stocks REST Trade: https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade
- Binance Stocks REST Account: https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/account
- Binance Stocks WebSocket Streams: https://developers.binance.com/en/docs/products/stocks/websocket-streams-general-info
- Binance Stocks Error Code: https://developers.binance.com/en/docs/products/stocks/error-code
