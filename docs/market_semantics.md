# Market 语义层

这份文档定义 `kairospy.core.market` 的目标定位。

当前 market 包混合了多类职责：

- provider-neutral 的市场事实模型
- 策略侧使用的字段名
- 订单簿状态
- 订阅状态
- stream/channel 规划
- dataset/stream identity 辅助函数

这会让 `core.market` 看起来像一个市场数据接入层。目标定位应该更窄：`core.market` 是市场事实的语义层，负责在 runtime、service、strategy 消费市场数据之前消除语义歧义。

## 目标定位

`kairospy.core.market` 应该拥有：

- 市场事实值对象，例如 bar、trade、quote、rate、order book level
- 市场事件值对象
- 策略和 projection 使用的标准市场对象定义
- 对标准对象属性的轻量 selector
- order book snapshot/update 的应用规则
- 用于区分相似值的轻量语义信息

`kairospy.core.market` 不应该拥有：

- live subscription registry
- provider channel planning
- exchange-specific stream name
- durable dataset identity
- connector request routing
- provider payload parsing
- runtime projection 或 view

这个包应该回答：“这个市场值到底是什么意思？”  
它不应该回答：“如何订阅 OKX 拿到这个值？”

## 是否重写

market 包目前不大，重写比继续小修小补更合适。这里应该做 breaking rewrite，而不是为旧字段体系保留兼容层。

原因是当前问题不是文件摆放问题，而是核心语义错位。继续保留 `MarketDataField("quote.bid")`、`FIELD_*`、`STREAM_*`、`MarketUpdate.fields` 这类旧入口，会把旧歧义继续带到新设计里。重写阶段应该同步迁移内部调用方和测试，让新语义成为唯一标准。

建议目标结构：

```text
kairospy/core/market/
  model.py        # Quote, Bar, TradePrint, RateObservation, MarketSubject
  selectors.py    # MarketSelector, 标准对象属性选择器
  events.py       # MarketEvent, 标准对象事件
  orderbook.py    # OrderBookSnapshot, OrderBookDelta, apply_orderbook_update
  __init__.py     # public exports
```

需要迁出 `core.market` 的内容：

```text
kairospy/service/domains/market/
  subscriptions.py  # subscription spec 和 registry
  planning.py       # requested objects/selectors -> provider-neutral stream plans
  identity.py       # 基于 MarketRef/DataSpec 的 dataset/stream identity
```

provider-specific 的内容继续放在 `kairospy.infrastructure.integrations`。

## 标准市场对象优先

策略作者应该主要面对标准市场对象，而不是一组很长的字符串字段。

这些对象比 `ticker.bid_price`、`top_of_book.bid_price` 这样的扁平字段更容易理解：

```text
Bar
Quote
OrderBook
TradePrint
FundingRate
OpenInterest
RateObservation
```

策略读取时应接近自然的数据模型：

```python
bar = context.market.bar("BTC-USDT", interval="1m")
quote = context.market.quote("BTC-USDT")
book = context.market.order_book("BTC-USDT")

price = bar.close
spread = quote.ask - quote.bid
best_bid = book.best_bid.price
```

订阅时也应该优先按对象表达：

```python
context.subscribe_market_data("BTC-USDT", Bar, interval="1m")
context.subscribe_market_data("BTC-USDT", Quote)
context.subscribe_market_data("BTC-USDT", OrderBook, depth=10)
context.subscribe_market_data("BTC-USDT", TradePrint)
```

如果策略只关心对象上的某些属性，可以使用 selector：

```python
context.subscribe_market_data("BTC-USDT", Bar.select("open", "close"), interval="1m")
context.subscribe_market_data("BTC-USDT", Quote.select("bid", "ask"))
```

selector 是对标准对象属性的选择，不是交易所 payload 字段，也不是新的 factor 体系。selector 只用于表达订阅/读取需求，不应该成为 runtime 事件的主要载体。

integration 层可以从多种输入生成这个事实：

- historical OHLCV row
- websocket kline update
- replayed dataset row
- 内部从 trade stream 聚合出来的 bar

这些输入来源是实现细节。数据进入 core/runtime 之后，应该被表达成稳定的标准市场对象，必要时再附带 selector 级别的属性更新。

## 歧义是核心问题

标准对象名本身不能把不同语义的值压成同一个东西。

`quote.bid` 有歧义，因为它可能表示：

- ticker payload 里的 best bid
- order book top-of-book 的 best bid
- broker 当前可成交 bid
- 多 venue 聚合后的 composite best bid
- 模拟环境里的 bid
- synthetic 或 inferred bid

这些值并不等价。策略使用其中一个值时，应该能明确表达自己要的是什么；projection 也不应该把它们静默覆盖到同一个字段里。

但解决方式不应该是把用户暴露的字段名改成长字符串：

```text
ticker.bid_price
ticker.ask_price
top_of_book.bid_price
top_of_book.ask_price
executable.bid_price
executable.ask_price
composite.bid_price
composite.ask_price
synthetic.bid_price
synthetic.ask_price
bar.open
bar.close
trade.price
funding_rate.rate
open_interest.value
```

这些名字虽然精确，但用户理解成本高，而且容易把实现细节暴露到策略 API。更好的方式是保留标准对象，把歧义放到对象上下文里表达：

```python
Quote(
    bid=Decimal("68000"),
    ask=Decimal("68001"),
    basis="ticker",
    source="okx",
)

Quote(
    bid=Decimal("67999"),
    ask=Decimal("68002"),
    basis="orderbook_top",
    source="okx",
)
```

或者：

```python
Quote(
    bid=Decimal("68000"),
    ask=Decimal("68001"),
    basis="broker_executable",
    source="ibkr",
)
```

这样策略仍然读 `quote.bid`，但系统不会把不同 `basis` 的 quote 当成同一个事实。

推荐的歧义消除维度：

- `type`：标准对象类型，例如 `Bar`、`Quote`、`OrderBook`
- `subject`：这个对象描述哪个 instrument、market、rate、index 或 curve
- `basis`：这个对象的构造依据，例如 `ticker`、`orderbook_top`、`broker_executable`、`composite`、`synthetic`
- `source`：数据来源，例如 `okx`、`binance`、`ibkr`、`replay`
- `derivation`：`direct`、`derived`、`inferred`

其中 `basis` 是最关键的轻量语义字段。它比长 field id 更自然，也比 `MarketFactorValue/Provenance` 这种完整对象体系更轻。

## Selector 是对象属性选择器

暂时不要引入过重的 factor 对象模型。需要字段级请求时，使用 selector 表达“标准对象上的哪些属性”，而不是创建独立的全局字段词表。

建议形态：

```python
@dataclass(frozen=True, slots=True)
class MarketSelector:
    model: type
    attributes: tuple[str, ...] = ()
    subject_type: str = "market"
    interval: str | None = None
    depth: int | None = None
    basis: str | None = None
    derivation: str = "direct"
```

字段含义：

- `model`：标准市场对象类型，例如 `Bar`、`Quote`、`OrderBook`
- `attributes`：可选属性列表，例如 `("open", "close")` 或 `("bid", "ask")`
- `subject_type`：通常是 `market`、`instrument`、`rate`、`index`、`curve`
- `interval`：时间桶字段需要，例如 bar
- `depth`：order book 需要
- `basis`：区分同一标准对象的不同语义来源
- `derivation`：`direct`、`derived`、`inferred`

这比 `MarketFactor`、`MarketFactorValue`、`MarketProvenance` 更轻，也比全局字符串字段词表更贴近用户心智模型。

旧的 `MarketDataField("bar.open")` 不再保留。它应直接替换为：

```python
MarketSelector(Bar, attributes=("open",))
```

旧的 `MarketDataField("quote.bid")` 不再保留，因为它缺少 basis。迁移时必须显式选择语义来源：

```python
MarketSelector(Quote, attributes=("bid",), basis="ticker")
```

不带 basis 的 `quote.bid` 不应该继续作为 API 存在。

## MarketEvent

目标事件应该直接承载标准市场对象，而不是 `model + values` 字典，也不是旧的 `fields` 字典。

```python
MarketEvent(
    subject=MarketSubject("market", "okx.spot.BTC-USDT"),
    observed_at=...,
    value=Quote(
        bid=Decimal("68000"),
        ask=Decimal("68001"),
        basis="ticker",
        source="okx",
    ),
)
```

关键变化是语义纪律：

- `value` 是标准市场对象，不是 provider payload key 字典
- `subject` 描述这个对象属于哪个 instrument、market、rate、index 或 curve
- `basis` 放在对象上，用来区分对象的构造依据
- `source` 放在对象或事件上，用来描述 observation 来源
- metadata 可以保留 raw id、sequence number、provider payload hint 等信息，但不应参与核心语义

多个输入可以更新同一个标准对象状态槽，但前提是它们拥有相同的 `type + subject + basis`。否则 projection 应该把它们保存在不同状态槽里。

`MarketUpdate.fields` 不应该保留为兼容入口。重写时应把内部生产者和消费者同步迁到 `MarketEvent(value=标准对象)`。

## Order Book 状态

order book 处理应该属于 `core.market`，因为交易所普遍推送增量，而把增量应用到订单簿是纯领域状态逻辑。

core 应该定义：

```text
OrderBookSnapshot
OrderBookDelta
OrderBookChange
apply_orderbook_update(snapshot, delta) -> OrderBookSnapshot
```

规则应该是 provider-neutral 的：

- size 为 0 的 level 表示删除该价格档
- bid level 按价格降序排序
- ask level 按价格升序排序
- update 应保留 market identity、source、sequence、event time
- stale sequence handling 应该显式处理

provider adapter 只负责把交易所原始 payload 解析成 `OrderBookDelta`。

## Subscriptions

subscriptions 是 runtime/service 状态，不是 core market 语义。

目标流程：

```text
Strategy requests standard market objects or selectors
  -> service market subscription registry 记录请求的对象/属性
  -> service market planner 按对象类型、basis、interval、depth 分组
  -> integration connector 把 plan 映射到 provider channel
  -> provider payloads 变成 MarketEvent / OrderBookDelta / domain facts
  -> runtime projections 发布 strategy-readable views
```

`MarketSubscriptionSpec` 可以放在 `service.domains.market`，因为它描述的是用户/runtime 对市场数据的请求。`MarketSubscriptionRegistry` 是 runtime-owned 的可变状态，不应该放在 core。

`MarketStreamPlan` 不是 core 概念。它是 service/runtime/integration 使用的 planning result。

## 迁移计划

1. 删除 `MarketDataField`、`FIELD_*`、`STREAM_*` 作为 `core.market` public API 的定位。
2. 新增 `core.market.orderbook`，定义 snapshot/update apply 逻辑。
3. 新增 `core.market.selectors`，只用于订阅/读取标准对象属性。
4. 用 `MarketEvent(value=标准对象)` 替换 `MarketUpdate(fields=...)`。
5. 把 subscription spec、registry、stream planning 迁到 `service.domains.market`。
6. 把 dataset/stream identity helper 迁到 `service.domains.market`。
7. 更新 integrations，让它们产出标准市场对象事件，例如 `Quote(basis="ticker")`、`Quote(basis="orderbook_top")`、`Bar(interval="1m")`。
8. 同步迁移 runtime projection、strategy context、tests，不保留旧字段 alias。

重写应该从语义开始，而不是从移动文件开始。如果只是移动文件，但不解决 `quote.bid` 这类歧义，设计问题会继续存在。
