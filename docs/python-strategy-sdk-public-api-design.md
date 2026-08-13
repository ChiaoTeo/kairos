# Python Strategy SDK 公共 API 与跨进程映射设计

## 1. 文档用途

本文定义 `kairospy.strategy` 面向策略作者的长期公共边界，包括：

- Strategy 生命周期和泛型、可判别的类型化事件；
- `StrategyContext` 的能力分组与命名；
- Reference、Market、Account、Risk、Execution 面向策略的 Python 投影；
- Aeron、FlatBuffers 与 Python 公共模型之间的适配边界；
- 状态、错误、时间、数值、身份和版本兼容规则；
- 从当前 `EventEnvelope.payload: object`、通用字典和兼容入口迁移到稳定 SDK 的步骤。

本文是 Strategy Python 公共入口的目标设计。模块所有权仍以
`docs/module-boundaries.md`、各业务模块目标架构和仓库根目录 `AGENTS.md` 为准；
Integration 连接与操作语义仍以
`docs/integration-session-and-operation-design.md` 为准。若本文示例与业务模块的
权威所有权发生冲突，先修正文档，不通过 Python facade 转移业务状态所有权。

本文不要求一次性重写 Strategy runtime。实施必须按一个可运行的业务切片逐步迁移，
每个切片在新入口通过验收后删除相同语义的旧入口。

## 2. 背景与问题

当前 Strategy runtime 已经提供订阅、行情事件、状态、账户、风险和执行能力，但公共入口
仍暴露了过多运行时与传输边界特征：

- `EventEnvelope.kind` 是开放字符串，`payload` 是 `object`；
- `context.state` 是 `MutableMapping[str, object]`；
- Market Python contract 直接重新导出 transport 中的 `BarView`、`QuoteView` 等类型；
- Risk 查询返回 `Mapping[str, Any]`；
- Reference 和 Execution 暴露接近进程客户端或 transport port 的对象；
- 用户需要填写 selector 字符串、拼接 instrument ID，并自行转换时间和 Decimal；
- `context.target_position`、`context.orders.target_position` 等入口并存，标准入口不明确；
- 示例不在 Pyright 的默认检查范围内，因此公共接口退化不会被 CI 发现。

这类接口能够驱动运行时，却不是成熟的策略 SDK。它要求策略作者理解事件信封、序列化、
跨进程客户端和模块内部结构，IDE 也无法为 `object` 提供可靠补全。

根本问题不是某个 `int(object)` 的类型错误，而是 transport contract、runtime contract 和
user contract 尚未形成清晰的三段边界。

## 3. 设计目标与非目标

### 3.1 目标

1. 策略作者只依赖 `kairospy.strategy`，不接触 Aeron、FlatBuffers、socket、mmap、provider SDK
   或 generated code。
2. Reference、Market、Account、Risk、Execution 以强类型、业务导向的 Python 能力呈现。
3. 使用 `on_market`、`on_account`、`on_risk`、`on_execution`、`on_clock`、`on_system`、
   `on_command` 这些稳定的领域级门面，同时让 IDE 精确收窄事件数据；标准策略无需 `cast()`、
   `type: ignore` 或访问 `object` 属性。
4. backtest、paper 和 live 使用同一套 Strategy API，仅由 composition 选择实现。
5. 公共模型不复制业务模块全部内部结构，只提供策略用例需要的稳定投影。
6. Python facade 不成为第二个业务状态 owner，也不绕过模块 application API。
7. 命令、查询和事件语义明确；事件回调中不发生隐藏的跨进程阻塞查询。
8. 公共类型、转换、错误和版本兼容可通过自动化测试验证。

### 3.2 非目标

- 不把 Rust domain entity、FlatBuffers table 或数据库 record 一比一翻译到 Python。
- 不在 Python SDK 中重建 Reference、Market、Account、Risk 或 Execution 的业务状态机。
- 不提供万能 `client.call(operation, payload)`、动态 capability registry 或通用 payload map。
- 不允许策略直接使用 Integration provider connection 或绕过 Execution application/Actor 向交易所
  提交订单；允许通过 `ctx.execution` 使用 Execution-owned 的订单命令。
- 不承诺第一阶段覆盖每个模块的所有管理和运维能力。
- 不为了静态类型而引入庞大的 Pydantic 模型树、ORM 或运行时反射框架。

## 4. 核心架构决定

### 4.1 公共门面与真实系统边界

`kairospy.strategy` 是策略作者的唯一公共门面。Python SDK 内部不再照搬 Rust
业务模块的六边形分层，也不在 business application 与 infrastructure 之间复制一套
Port/Protocol。Python business application 直接依赖具体 Unix、mmap 和 FlatBuffers
实现；真正需要稳定契约的边界是 Python SDK 与 Rust Application API 之间的跨进程边界。

标准数据路径为：

```text
Business Application Contract
  -> versioned FlatBuffers message
  -> Aeron transport
  -> Python transport decoder
  -> Strategy application mapper
  -> module application model re-exported by kairospy.strategy
  -> user callback
```

标准命令路径为：

```text
user strategy
  -> ctx.execution typed intent/order command
  -> concrete Python business application
  -> concrete Python infrastructure transport
  -> versioned FlatBuffers command
  -> Aeron transport
  -> Execution application
  -> ExecutionActor
```

Aeron 负责传输，FlatBuffers 负责跨进程编码；两者都不是策略业务 API。

标准同步查询路径为：

```text
user strategy
  -> ctx.<business application>.typed_query(...)
  -> module-owned synchronous projection reader
  -> mmap snapshot + FlatBuffers decode
  -> module application model
  -> typed result
```

同步查询不经过 Strategy 通用 `SnapshotEnvelope`，也不通过 socket/Aeron request-response
伪装成本地读取。mmap 的双槽读取、generation、watermark、FlatBuffers table 和重试校验由相应
业务 application 的 projection reader 封装。策略只获得 `Bar | None`、`AccountSnapshot`、
`RiskStatus`、`Order | None` 等业务结果。Strategy Host 为 stream join 读取 Market-owned snapshot
watermark，但不把 snapshot envelope 放入 Context，也不缓存或解释其业务 payload。

### 4.2 公共模型由业务模块 application contract 拥有

Reference、Market、Account、Risk、Execution 各自的 Python application contract 应拥有本模块的
公共 request、result、event 和 projection 类型。Strategy 不再复制 `StrategyBar`、
`StrategyPosition`、`StrategyOrder` 等第二套模型；`kairospy.strategy` 为用户便利重导出同一个类型
对象。

例如：

```text
kairospy.application.market.Bar
            ^ same class object
            |
kairospy.strategy.Bar  (re-export)
```

这样 Market application、Strategy runtime、backtest adapter 和用户策略对 `Bar` 有同一个类型身份，
不需要跨 application 再做一次同义模型转换。

Python 公共类型仍按 application use case 设计，不是 Rust domain entity、FlatBuffers table 或
数据库 record 的逐字段镜像。一个公共事件可以引用其他模块拥有的稳定 ID 或只读引用，但不得
合并不同模块的可变权威状态。

例如 `Bar` 可以携带 Reference-owned 的 `InstrumentRef` 和 Market-owned 的观察值，但 `Bar` 只是
不可变 event projection，不因此拥有 Instrument 或 Market 状态。

字段进入公共模型前必须满足：

1. 当前策略用例直接需要；
2. 语义稳定且 owner 明确；
3. 不是 transport、persistence、provider 或进程管理细节；
4. 能定义缺失、未知、时间和版本兼容行为；
5. 有 mapper 和 contract test 证明转换正确。

跨模块真正共享的标量和身份，例如 `InstrumentId`、`MarketId`、`AccountId` 和 UTC/nanos helper，
可以进入一个与 Rust `kairos-domain-types` 对齐的轻量 Python shared domain-types 包。只提升确实
共享的语义，不创建万能 `EntityId`、万能 Event 或全局模型仓库。

事件 discriminator 的识别由各模块 application mapper 自己维护。不要建立一个全局动态 type
registry：Market mapper 识别 Market union，Execution mapper 识别 Execution union，Strategy
runtime 只按 application event domain 分发。

### 4.3 Context 分组不改变模块所有权

公共 Context 固定使用以下能力名：

```python
ctx.reference
ctx.market
ctx.account
ctx.risk
ctx.execution
ctx.state
ctx.clock
ctx.logger
```

其中：

- Reference、Market、Account、Risk 对策略是只读或订阅能力；
- Execution 是交易 intent 命令和执行查询入口；
- State 只拥有某个 Strategy instance 的私有持久化状态；
- Context 是 application facade，不拥有任何业务模块的可变状态。

Context 的具体实现只保存 composition 已创建的 Application 引用，并转发属性访问；它不构造
业务 request、不解析 projection、不根据 operation 字符串路由，也不维护 application snapshot
cache。Strategy instance identity、当前事件 causation 和 request ID 由私有 composition adapter
附加，request 校验、用例语义和 typed receipt 仍由各业务 Application 定义。

`ctx.execution.target_position()` 是用户可理解的执行能力入口，但内部仍由 Execution application
编排 Account、Risk、Reference 和 Market projection。这个命名不表示 Python Context 或 Strategy
接管了 Execution intent、order 或 fill 状态。

### 4.4 Context 组合具体 application，不新增公共 Capability 层

`ctx.market`、`ctx.reference`、`ctx.account`、`ctx.risk`、`ctx.execution` 直接持有对应模块公开的
Python application facade 或其明确的 strategy-safe application view：

```python
class StrategyContext(Protocol):
    reference: ReferenceApplication
    market: MarketApplication
    account: AccountApplication
    risk: RiskApplication
    execution: ExecutionApplication
```

不再把 `ReferenceCapability`、`MarketCapability`、`RiskCapability` 等作为用户需要理解和导入的
第二套抽象。若模块现有 Application 已经提供恰当用例，Context 直接调用它；只有现有 Application
同时暴露不应交给 Strategy 的管理/运维写操作时，才由该业务模块定义一个有当前调用者的
strategy-safe application view。

内部 application 直接调用当前唯一的具体 infrastructure client。测试可以利用 Python 的结构化
调用特性传入轻量 fake，但不得为了测试在生产代码中增加 Port/Protocol。backtest/paper/live 的
差异通过 endpoint、配置和真实执行语义解决，不以“有多个运行模式”为理由复制五套 Capability
interface。

## 5. 目标用户体验

第一阶段的标准策略应当接近下面的形式：

```python
from decimal import Decimal

from kairospy.strategy import BarEvent, MarketEvent, Strategy, StrategyContext


class SpyHourlyStrategy(Strategy):
    strategy_id = "massive-spy-hourly"

    def on_start(self, ctx: StrategyContext) -> None:
        spy_market = ctx.reference.require_market(
            symbol="SPY",
            exchange="massive",
            market_type="equity",
        )
        ctx.market.subscribe_bars(spy_market, timeframe="1h")

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
        if not isinstance(event, BarEvent):
            return

        bar = event.data
        count = ctx.state.increment("bar_count")

        if count == 1:
            ctx.execution.target_position(
                instrument=bar.instrument,
                quantity=Decimal("1"),
                account="paper-account",
                reason="enter after the first completed hourly bar",
            )
        elif count == 3:
            ctx.execution.close_position(
                instrument=bar.instrument,
                account="paper-account",
                reason="close after the third completed hourly bar",
            )
```

该代码必须满足：

- 不访问 `payload: object`；
- `isinstance(event, BarEvent)` 或 `match` 能将 `event.data` 静态收窄为 `Bar`；
- 不拼接 canonical ID；
- 不填写 selector 字符串；
- 不自行把 wire decimal、时间戳或枚举转换成业务类型；
- Pylance/Pyright 能识别所有字段和方法；
- 同一份代码可运行于 backtest、paper 和 live。

## 6. 公共包与依赖规则

### 6.1 唯一入口

策略作者只从顶层包导入稳定 API：

```python
from kairospy.strategy import (
    Bar,
    BarEvent,
    InstrumentRef,
    Market,
    MarketEvent,
    Strategy,
    StrategyContext,
)
```

禁止用户文档和模板导入：

```text
kairospy.application.strategy.services.*
kairospy.infrastructure.*
kairospy.infrastructure.transport.generated.*
```

### 6.2 建议代码结构

```text
kairospy/strategy/
  __init__.py                 stable re-export surface
  base.py                     Strategy lifecycle defaults
  context.py                  public StrategyContext Protocol
  errors.py                   public SDK errors
  events.py                   generic Strategy event envelopes and aliases
  state.py                    Strategy-owned state application

kairospy/domain_types/
  identity.py                 genuinely shared typed IDs
  time.py                     UTC/unix-nanos conversions

kairospy/application/
  reference/{application,models,events,mapping}.py
  market/{application,models,events,mapping}.py
  account/{application,models,events,mapping}.py
  risk/{application,models,events,mapping}.py
  execution/{application,models,events,mapping}.py

kairospy/application/strategy/
  services/
    context.py                concrete Context implementation
    dispatch.py               envelope-to-typed-callback dispatch

kairospy/infrastructure/
  transport/
    aeron/                    transport implementation
    generated/                FlatBuffers generated code
```

实际拆文件应随首个迁移切片创建，不为了目录完整性提前增加空文件。模块 application 可以直接
依赖 infrastructure，但 infrastructure 不得反向拥有业务用例或形成同义 contract facade。每个
mapper 由其业务模块拥有，把具体 decoder 结果转换为 application model，并且不反向依赖
Strategy。Strategy dispatcher 仅按 domain 选择对应业务 mapper 与 callback；
`kairospy.strategy` 只重导出允许策略使用的 application 类型。

### 6.3 公共名称

- 用户基类命名为 `Strategy`；迁移期 `StrategyBase` 可作为弃用别名。
- 用户上下文命名为 `StrategyContext`；它可以由 `Protocol` 实现，但公共名称不暴露
  `Protocol` 这一实现技术。
- 具体运行时类使用私有名称，例如 `_RuntimeStrategyContext`，不从 `kairospy.strategy` 导出。
- 能力使用单数模块名：`account`、`execution`；不同时保留 `accounts`、`portfolio`、`orders`
  等重复 facade。

## 7. 公共标量与身份规则

### 7.1 ID

Python SDK 必须保留 Reference 身份规范中的语义差异，至少区分：

```python
InstrumentId
ListingId
MarketId
ExchangeId
AccountId
IntentId
OrderId
FillId
```

第一阶段可以使用不可变、可序列化的轻量 value object：

```python
@dataclass(frozen=True, slots=True)
class InstrumentId:
    value: str
```

不要建立一个万能 `EntityId`。ID 的构造应验证非空和格式；普通策略通过 Reference 查询获得 ID，
不应手工拼接。公开模型的 `str(id)` 可返回 wire value，JSON checkpoint 可以保存其字符串值。

### 7.2 数值

- 价格、数量、金额、费率、PnL 和风险额度统一使用 `Decimal`；
- sequence、count 和 unix nanos 使用 `int`；
- 公共 API 不暴露 transport 的 `DecimalValue` wrapper；
- float 可以作为便利命令输入的过渡兼容类型，但公共模型输出永远使用 `Decimal`；
- mapper 从十进制字符串构造 `Decimal`，不得先经过 float。

### 7.3 时间

- 公共事件时间统一为带 UTC timezone 的 `datetime`；
- wire 继续使用整数 unix nanos；
- mapper 必须保留纳秒原值或明确记录 Python `datetime` 的微秒精度损失；
- 需要无损审计时，公共事件可额外提供 `occurred_at_unix_nanos: int`；
- Strategy 决策使用 `ctx.clock.now`，不得以机器墙上时间替代业务时间。

### 7.4 枚举

稳定、封闭的业务状态使用公开 Enum，例如 `OrderStatus`、`IntentStatus`、`MarketStatus`。
开放的 provider code 不提升为全局 Enum。遇到未知的已版本化枚举值时，mapper 必须产生明确的
`UnsupportedContractValueError` 或映射到显式 `UNKNOWN`，具体策略由相应 contract 固定，不能静默丢弃。

## 8. Reference 公共投影

Reference 拥有 canonical identity、listing、market、交易规则和生命周期事实。Strategy 只获得
只读投影。

### 8.1 最小类型

```python
@dataclass(frozen=True, slots=True)
class InstrumentRef:
    id: InstrumentId
    display_symbol: str


@dataclass(frozen=True, slots=True)
class TradingRules:
    price_increment: Decimal | None
    quantity_increment: Decimal | None
    minimum_quantity: Decimal | None
    minimum_notional: Decimal | None
    contract_multiplier: Decimal | None


@dataclass(frozen=True, slots=True)
class Market:
    id: MarketId
    instrument: InstrumentRef
    listing_id: ListingId
    exchange_id: ExchangeId
    symbol: str
    market_type: str
    base_asset: str | None
    quote_asset: str | None
    status: MarketStatus
    trading_rules: TradingRules
```

`InstrumentRef` 用于高频事件，避免在每个 Quote/Trade/Bar 中复制完整 Reference record。
需要完整 Market 事实时，通过 `ctx.reference.market(id)` 读取 runtime 已维护的 Reference projection。

### 8.2 Reference Application

```python
class ReferenceApplication:
    def market(self, market_id: MarketId) -> Market | None: ...

    def require_market(self, market_id: MarketId) -> Market: ...

    def require_market(
        self,
        *,
        symbol: str,
        exchange: str,
        market_type: str,
    ) -> Market: ...

    def find_markets(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
        active_only: bool = True,
    ) -> tuple[Market, ...]: ...
```

Python 实现可以通过 overload 提供两种 `require_market` 形式。查询结果为不可变 snapshot；
不返回数据库行、provider catalog payload 或底层 `ReferenceClient`。

符号查询可能返回多个 Market，`require_market` 只有在恰好匹配一个结果时成功；零结果和多结果都
产生包含过滤条件的明确错误，禁止自动选择第一个 market。

## 9. Market 公共投影

Market 拥有 observations、order book、subscriptions 和 freshness。Market 数据是不可变事实。

### 9.1 观察类型

```python
@dataclass(frozen=True, slots=True)
class Bar:
    market_id: MarketId
    instrument: InstrumentRef
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None


@dataclass(frozen=True, slots=True)
class Quote:
    market_id: MarketId
    instrument: InstrumentRef
    bid_price: Decimal | None
    bid_quantity: Decimal | None
    ask_price: Decimal | None
    ask_quantity: Decimal | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None


@dataclass(frozen=True, slots=True)
class Trade:
    market_id: MarketId
    instrument: InstrumentRef
    price: Decimal
    quantity: Decimal
    aggressor_side: AggressorSide | None
    occurred_at: datetime
    occurred_at_unix_nanos: int
    source_id: str | None
```

OrderBook、Greeks 和 MarketStatus 等后续按真实策略调用者增加，不为了与 FlatBuffers schema
字段对齐而提前导出。

### 9.2 泛型事件与可判别联合

`Bar`、`Quote`、`Trade` 是业务数据；事件信封负责 stream、sequence、schema 和 producer 等
交付元数据。公共事件使用泛型保存“事件携带哪种数据”的关系：

```python
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeAlias, TypeVar


TData_co = TypeVar("TData_co", covariant=True)


@dataclass(frozen=True, slots=True)
class EventMetadata:
    stream_id: str
    sequence: int
    dispatch_sequence: int
    schema_version: int
    producer: str
    occurred_at: datetime | None
    causation_id: str | None = None


@dataclass(frozen=True, slots=True)
class DataEvent(Generic[TData_co]):
    data: TData_co
    metadata: EventMetadata


@dataclass(frozen=True, slots=True)
class BarEvent(DataEvent[Bar]):
    kind: Literal["bar"] = field(init=False, default="bar")


@dataclass(frozen=True, slots=True)
class QuoteEvent(DataEvent[Quote]):
    kind: Literal["quote"] = field(init=False, default="quote")


@dataclass(frozen=True, slots=True)
class TradeEvent(DataEvent[Trade]): 
    kind: Literal["trade"] = field(init=False, default="trade")


MarketEvent: TypeAlias = BarEvent | QuoteEvent | TradeEvent
```

泛型解决 `DataEvent[T]` 与 `data: T` 的类型关系；`Literal` 判别联合解决一个 `on_market` 同时接收
多种事件时的静态收窄。二者职责不同，仅使用其中一个都不完整。

以下形式都应被 Pyright 正确收窄：

```python
def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
    if event.kind == "bar":
        reveal_type(event.data)  # Bar


def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None:
    match event:
        case BarEvent(data=bar):
            reveal_type(bar)  # Bar
        case QuoteEvent(data=quote):
            reveal_type(quote)  # Quote
        case TradeEvent(data=trade):
            reveal_type(trade)  # Trade
```

公共 event 不再使用 `payload: object`。`data` 命名表达这是已经解码和映射的业务数据，而不是
不透明 transport payload。

### 9.3 订阅接口

```python
class MarketApplication:
    def subscribe_bars(
        self,
        market: Market | MarketId,
        *,
        timeframe: str,
    ) -> Subscription: ...

    def subscribe_quotes(
        self,
        market: Market | MarketId,
    ) -> Subscription: ...

    def subscribe_trades(
        self,
        market: Market | MarketId,
    ) -> Subscription: ...

    def unsubscribe(self, subscription: Subscription) -> None: ...

    def latest_bar(
        self,
        market: Market | MarketId,
        *,
        timeframe: str,
    ) -> Bar | None: ...
```

`selector="bar:1h"` 保留在 Strategy adapter 到 Market application request 的映射内部，
不再是普通用户 API。订阅仍由 MarketActor 持有，Subscription 只是 Strategy instance 所有的
lease handle；Strategy 停止时继续按 owner 自动释放。

历史查询应通过明确的 `ctx.market.history.bars(...)` 或独立 Data Application 提供，
并遵守 backtest time frontier。第一阶段不要把同步远程历史请求伪装成 `latest_bar()`。

## 10. Account 公共投影

Account 是 balances、positions、equity、freshness、account status 和 settlement 的唯一 owner。
Strategy 对 Account 只读。

```python
@dataclass(frozen=True, slots=True)
class Balance:
    account_id: AccountId
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    account_id: AccountId
    instrument: InstrumentRef
    quantity: Decimal
    average_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    account_id: AccountId
    equity: Decimal | None
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    freshness: DataFreshness
    generation: int
    event_sequence: int
```

Account Application：

```python
class AccountApplication:
    def snapshot(self, account: AccountId | str) -> AccountSnapshot: ...

    def balance(
        self,
        asset: str,
        *,
        account: AccountId | str,
    ) -> Balance | None: ...

    def position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
    ) -> Position | None: ...
```

不提供 `account.submit_order`、`account.cancel_order` 或修改 balance/position 的入口。
`accounts` 与 `portfolio` 兼容别名在迁移结束后删除，标准入口仅为 `ctx.account`。

## 11. Risk 公共投影

Risk 是预算、authorization decision、reservation 和 circuit state 的 owner。Strategy 可以观察
风险状态，但正常交易授权必须由 Execution 主链路调用 `authorize_and_reserve`，不能由 Strategy
先检查再绕过授权。

```python
@dataclass(frozen=True, slots=True)
class RiskViolation:
    code: str
    message: str
    limit: Decimal | None
    actual: Decimal | None


@dataclass(frozen=True, slots=True)
class RiskStatus:
    account_id: AccountId
    trading_allowed: bool
    available_notional: Decimal | None
    reserved_notional: Decimal
    utilization: Decimal | None
    violations: tuple[RiskViolation, ...]
    generation: int
    event_sequence: int
```

Risk Application：

```python
class RiskApplication:
    def status(self, *, account: AccountId | str) -> RiskStatus: ...
```

第一阶段不暴露通用 `current() -> Mapping[str, object]`，也不允许 Strategy 创建、consume 或
release reservation。管理预算、策略和 circuit 的接口属于运维或 Risk application，不属于
StrategyContext。

## 12. Execution 公共投影与命令

Execution 是 intent、plan、leg、exchange-facing order、fill 和 execution audit 的 owner。
`ctx.execution` 是 Strategy 唯一的交易写入口。

### 12.1 Intent 命令

第一阶段提供当前已有真实调用者需要的命令：

```python
class ExecutionApplication:
    def target_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        account: AccountId | str,
        limit_price: Decimal | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
    ) -> IntentReceipt: ...

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
        reason: str = "",
        intent_id: IntentId | None = None,
    ) -> IntentReceipt: ...

    def cancel_intent(
        self,
        intent_id: IntentId,
        *,
        reason: str = "",
    ) -> IntentReceipt: ...

    def intent(self, intent_id: IntentId) -> ExecutionIntent | None: ...

    def order(self, order_id: OrderId) -> Order | None: ...

    def open_orders(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
    ) -> tuple[Order, ...]: ...
```

`close_position` 是 `target_position(quantity=Decimal("0"))` 的明确便利入口，不建立新的业务语义。

PairArbitrage、PortfolioRebalance 和 QuoteProvisioning 使用各自的类型化 request，按现有真实
use case 逐项迁移。不要把它们压入 `dict[str, object]`，也不要创建拥有大量 optional 字段的
万能 intent request。

### 12.2 订单命令

高层 Intent 和低层订单命令是同一个 Execution application 的两种用例，不互相冲突：

- Intent API 表达策略希望达到的业务结果，例如目标仓位、组合再平衡和双边套利；
- Order API 表达策略明确选择的战术订单动作，例如提交限价单、撤单和改单；
- 两者都由 ExecutionActor 接受，经过风险授权、持久化、幂等、状态机、审计和 provider route；
- 两者都不能让 Strategy 获得 `OrderEntryConnection`、provider client 或 raw exchange command。

公共订单入口为：

```python
class ExecutionApplication:
    def submit_order(self, request: OrderRequest) -> OrderCommandReceipt: ...

    def cancel_order(
        self,
        order_id: OrderId,
        *,
        reason: str = "",
    ) -> OrderCommandReceipt: ...

    def replace_order(
        self,
        order_id: OrderId,
        request: ReplaceOrderRequest,
    ) -> OrderCommandReceipt: ...

    def cancel_all(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
        reason: str = "",
    ) -> BulkOrderCommandReceipt: ...
```

两档 API 的选择标准为：

| 用户意图 | 公共入口 | Execution 内部语义 |
|---|---|---|
| “把仓位调整到 100” | `target_position` | TargetPosition intent，可生成多个订单 |
| “把仓位清零” | `close_position` | TargetPosition(quantity=0) |
| “按这个价格挂一张限价单” | `submit_order` | SingleOrder intent + local order |
| “撤销这张订单” | `cancel_order` | Execution order state transition |
| “修改这张活动订单” | `replace_order` | cancel/replace 或 provider-native replace，由 Execution 决定 |
| “撤销这个范围内的活动订单” | `cancel_all` | scoped bulk Execution command |

订单 request 不使用一个拥有大量 optional 字段的万能结构。第一阶段至少定义判别明确的类型：

```python
@dataclass(frozen=True, slots=True)
class MarketOrderRequest:
    instrument: InstrumentRef | InstrumentId
    account: AccountId | str
    side: OrderSide
    quantity: Decimal
    time_in_force: TimeInForce = TimeInForce.IOC
    reduce_only: bool = False
    reason: str = ""
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class LimitOrderRequest:
    instrument: InstrumentRef | InstrumentId
    account: AccountId | str
    side: OrderSide
    quantity: Decimal
    limit_price: Decimal
    time_in_force: TimeInForce = TimeInForce.DAY
    post_only: bool = False
    reduce_only: bool = False
    reason: str = ""
    request_id: str | None = None


OrderRequest = MarketOrderRequest | LimitOrderRequest


@dataclass(frozen=True, slots=True)
class ReplaceOrderRequest:
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    expected_revision: int | None = None
    reason: str = ""
    request_id: str | None = None
```

Stop、stop-limit、trailing 或 provider-specific order 只有在 Execution contract 已有明确语义和
真实调用者时才增加相应 request variant。`ReplaceOrderRequest` 构造时必须至少提供一个允许修改的
字段；`expected_revision` 用于防止 Strategy 基于过期 Order projection 覆盖更新。

提交示例：

```python
receipt = ctx.execution.submit_order(
    LimitOrderRequest(
        instrument=bar.instrument,
        account="paper-account",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        limit_price=bar.close,
        time_in_force=TimeInForce.DAY,
        reason="enter at the completed bar close",
    )
)
```

`submit_order` 在 Execution 内部建立 SingleOrder intent、plan、leg 和 child order 的稳定关联，
而不是直接调用 provider。这样订单型策略获得简洁入口，同时仍保留：

```text
strategy/request
  -> intent
  -> plan
  -> leg
  -> local order
  -> remote order
```

`cancel_order` 和 `replace_order` 操作的是 Execution-owned `OrderId`，不是裸 remote order ID。
Execution 负责验证订单状态、Strategy/account ownership、route、交付确定性和 reconciliation。
已经终态的订单按幂等规则返回当前结果，不能重新打开状态机。

每个订单命令必须携带稳定 `request_id`、Strategy identity 和当前 source watermark。调用方未提供
`request_id` 时由 runtime 生成；调用方需要在失败恢复后重提同一逻辑命令时，应复用原
`request_id`。Execution 对同一 request ID 幂等，不因 Python 重连生成重复订单。

`replace_order` 是 Execution 业务命令，不承诺 provider 一定支持原生 replace。Execution 可以按
route capability 选择原生 replace 或受控 cancel-and-replace，但必须保留旧/新 Order 关联、风险
reservation 和交付确定性；中间状态不可被 facade 隐藏为一次同步成功。

`cancel_all` 是有破坏范围的批量命令，必须满足以下约束：

- 至少提供 `instrument` 或 `account`；
- 如果 Strategy composition 只绑定一个 account，可以显式定义该 account 为默认 scope；
- 多 account Strategy 未提供 account 时不得隐式跨账户撤单；
- 默认只影响当前 Strategy/instance 有权管理的 Execution orders；
- receipt 返回匹配、已提交撤单、已终态、失败和 reconciliation-required 的数量；
- 不提供无 scope 的全系统撤单快捷方式。

Order API 不要求策略先自行调用 `ctx.risk.status()`。Risk status 是观察投影，Execution 仍必须在
权威命令路径中执行 `authorize_and_reserve`。Strategy 的预检查不能替代该步骤。

### 12.3 返回值和生命周期

命令提交返回 typed receipt，而不是把最终成交伪装成同步结果：

```python
@dataclass(frozen=True, slots=True)
class IntentReceipt:
    request_id: str
    intent_id: IntentId | None
    status: SubmissionStatus
    error: StrategyCommandError | None
```

`ACCEPTED` 只表示 Execution application/Actor 已接受 intent，不表示已经下单或成交。后续状态通过
`on_execution(ctx, event)` 中的 `IntentUpdateEvent`、`OrderUpdateEvent`、`FillEvent` 分支或查询
projection 获得。

订单命令使用独立 receipt，避免把“命令已受理”和“订单已被交易所接受”混为一谈：

```python
@dataclass(frozen=True, slots=True)
class OrderCommandReceipt:
    request_id: str
    intent_id: IntentId | None
    order_id: OrderId | None
    status: SubmissionStatus
    delivery_certainty: DeliveryCertainty
    error: StrategyCommandError | None


@dataclass(frozen=True, slots=True)
class BulkOrderCommandReceipt:
    request_id: str
    matched: int
    submitted: int
    already_terminal: int
    failed: int
    reconciliation_required: int
```

`OrderCommandReceipt.status == ACCEPTED` 只表示本地 Execution 已持久化并接受命令。
provider acknowledgement、partial fill、fill、reject、cancel 和 replace 结果继续通过 Order lifecycle
event 表达。命令在可能写出后不得透明重试；`delivery_certainty == INDETERMINATE` 时必须进入
Execution reconciliation，并保留 request/intent/order/route/account 关联。

公共执行投影至少包括：

```python
ExecutionIntent
Order
Fill
IntentUpdate
OrderUpdate
```

这些类型保留 intent/plan/leg/order/fill 的稳定关联，但不暴露 provider raw response、数据库
row、FlatBuffer offset 或 Aeron session。

## 13. Strategy 生命周期与类型化分发

### 13.1 保留领域级生命周期门面

核心 Strategy Protocol 保留当前已经形成的少量、稳定回调，不按每一种 payload 扩张方法数量：

```python
class Strategy:
    strategy_id = "strategy"

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_market(self, ctx: StrategyContext, event: MarketEvent) -> None: ...

    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None: ...

    def on_risk(self, ctx: StrategyContext, event: RiskEvent) -> None: ...

    def on_execution(self, ctx: StrategyContext, event: ExecutionEvent) -> None: ...

    def on_clock(self, ctx: StrategyContext, event: ClockEvent) -> None: ...

    def on_system(self, ctx: StrategyContext, event: SystemEvent) -> None: ...

    async def on_command(
        self,
        ctx: StrategyContext,
        command: StrategyCommand,
    ) -> CommandResult: ...

    def on_end(self, ctx: StrategyContext) -> None: ...
```

这些门面分别表达不同的调度语义：

| 回调 | 类型 | 语义 |
|---|---|---|
| `on_market` | event notification | Market observation，按 stream 顺序处理 |
| `on_account` | event notification | balance、position、equity、freshness 等 Account 事实 |
| `on_risk` | event notification | 与当前 Strategy/account scope 相关的 Risk 决策、reservation、circuit 事实 |
| `on_execution` | event notification | Execution intent/order/fill 生命周期事实 |
| `on_clock` | event notification | 确定性业务时间和 timer |
| `on_system` | event notification | readiness、health、gap、shutdown 等系统事实 |
| `on_command` | async request/response | 外部控制命令，必须返回结构化结果 |

Strategy 基类为事件回调提供 no-op 默认实现；`on_command` 默认返回 unsupported command。
策略只覆盖需要的领域回调。

不把 `on_bar`、`on_quote`、`on_trade`、`on_order_book`、`on_greeks` 等全部加入核心基类。
否则每增加一种 Market 数据或 Execution 生命周期事件都需要扩张公共 Strategy Protocol，形成
大而稀疏的 facade。细分发生在 event union 内，而不是生命周期方法名上。

`on_market` 替代含义过宽的 `on_data`；`on_execution` 替代只表达 Intent、无法覆盖 Order/Fill 的
`on_intent`。这是一次明确的公共 API 迁移，不同时永久保留两套同义生命周期方法。

`on_account` 和 `on_risk` 只有在相应 application event contract、Aeron stream、ordering、gap、
snapshot/resync 和 scope filter 全部存在后才进入实现。设计文档定义目标名称，不允许先增加两个
永远收不到可靠事件的空回调来追求表面对称。

### 13.2 各领域事件也是判别联合

Market、Execution、Clock 和 System 使用相同的泛型 envelope 方式，但拥有各自封闭的公共联合：

```python
MarketEvent = BarEvent | QuoteEvent | TradeEvent

AccountEvent = (
    BalanceChangedEvent
    | PositionChangedEvent
    | EquityChangedEvent
    | AccountFreshnessChangedEvent
)

RiskEvent = (
    RiskDecisionEvent
    | ReservationChangedEvent
    | CircuitChangedEvent
)

ExecutionEvent = (
    IntentUpdateEvent
    | OrderUpdateEvent
    | FillEvent
)

ClockEvent = TimerFiredEvent | TimeAdvancedEvent

SystemEvent = (
    ReadinessChangedEvent
    | DataGapDetectedEvent
    | ResyncRequiredEvent
    | ShutdownRequestedEvent
)
```

各 union 只包含已稳定并对 Strategy 有意义的事件。FlatBuffers 中新增内部事件不会自动扩张
Python union；必须先完成 owner、mapper、兼容和用户用例评审。

增加新的 union variant 通常不破坏只处理部分事件的策略。使用 `assert_never()` 做穷举匹配的策略
会在升级 SDK 后得到静态提示，这是有价值的显式兼容信号。

### 13.3 泛型可以解决什么

泛型适合表达可复用算法，例如：

```python
TData = TypeVar("TData")


def event_data(event: DataEvent[TData]) -> TData:
    return event.data
```

也可以让内部 mapper 的结果保持精确类型：

```python
def map_event(raw: RawBarEvent) -> BarEvent: ...
```

但泛型不能根据 Strategy 在 `on_start()` 中调用了哪些动态订阅，自动改变 `on_market` 的参数类型。
Python 泛型在运行时被擦除，Pyright 也不会从 `subscribe_bars()` 的副作用推导后续 callback 只接收
Bar。因此不采用下面的核心设计：

```python
class Strategy(Generic[TMarketEvent]): ...
```

它会让订阅多种数据的策略需要声明复杂 union，也无法阻止 runtime 因动态订阅变更交付其他事件。

同样不使用 `@overload` 重载多个 `on_market` 实现；Python 运行时只保留最后一个同名方法。
`singledispatchmethod` 或 decorator router 可以作为未来的可选 convenience，但不作为核心协议，
因为它们引入注册顺序、继承组合和隐藏 dispatch 的复杂度。

### 13.4 内部分发

Runtime 使用通用内部 envelope 维持 transport ordering、sequence、watermark 和日志上下文，但
必须在调用用户代码前完成严格解码和领域分发：

```text
FlatBuffer MarketEvent
  -> validate schema/version/discriminator
  -> decode Bar wire fields
  -> map to public BarEvent(data=Bar, metadata=...)
  -> bind Strategy event context
  -> Strategy.on_market(ctx, event)
```

Runtime 首先根据 application event domain 选择
`on_market/on_account/on_risk/on_execution/on_clock/on_system`，用户再通过判别联合选择该领域中的
具体事件。这样保留两级清晰分发：

```text
runtime: domain -> lifecycle method
strategy: event variant -> business behavior
```

domain、kind 与 data 类型不匹配时属于 contract/adapter 错误，不调用用户回调，也不把未知
`object` 交给策略自行判断。

### 13.5 多 Application 事件合流

增加 Account、Risk 和 Execution 事件后，Strategy Host 会消费多个 application stream。Aeron 只保证
相应 stream/channel 的顺序，不能虚构一个所有业务模块天然共享的全局 sequence。

Strategy application 内部应有一个明确、固定的 event ingress/multiplexer，负责：

1. 分别校验每个模块的 stream sequence、gap 和 schema；
2. 调用所属模块 mapper，得到 `MarketEvent`、`AccountEvent`、`RiskEvent` 或 `ExecutionEvent`；
3. 在交付前更新相应只读 projection；
4. 按 live 或 replay 的明确调度规则串行调用 Strategy；
5. 为实际交付顺序分配 instance-local `dispatch_sequence` 并写入 Strategy journal；
6. 保留 producer `stream_id/sequence` 和 `causation_id`，不把 dispatch sequence 伪装成模块 sequence。

这只是现有 StrategyHost 的显式多源 ingress，不是全局 event bus、动态 type registry 或新的业务状态
owner。它不合并模块业务状态，只负责 Strategy callback 的确定性串行调度。

live 模式记录实际已观察的 dispatch order；backtest/replay 按
`docs/time-driven-runtime-and-backtest-design.md` 固定同一业务时间点的领域优先级。存在因果关系时，
例如 Execution fill 导致 Account position 更新，应通过 causation/source watermark 保留关联，不能仅靠
时间戳猜测先后。

Account 和 Risk ingress 必须按当前 launch、Strategy、account binding 和授权范围过滤。Strategy
不能因为注册 `on_account` 或 `on_risk` 就观察其他 Strategy 或未绑定账户的全局事件。

### 13.6 `on_system` 与 `on_command`

`on_system` 保持事件通知语义。它适合 readiness、data gap、resync、degraded 和 shutdown 等由
runtime 推送的事实，不返回业务结果。

`on_command` 保持现有异步请求/响应语义。它是 Strategy 接受外部控制、交互和自定义命令的扩展点，
不应被合并进 `on_system` 或 `on_market`：

```python
async def on_command(
    self,
    ctx: StrategyContext,
    command: StrategyCommand,
) -> CommandResult:
    if command.kind != "strategy.rebalance":
        return CommandResult.rejected(
            command.request_id,
            "unsupported command",
        )

    request = command.require_payload(RebalanceCommand)
    return await self.rebalance(ctx, request)
```

`StrategyCommand` 的 envelope 可以保持非泛型，以支持开放的用户命令 kind；通过
`require_payload(ModelType)` 在 handler 内完成运行时验证和静态类型恢复。只有当多个真实命令共享
稳定封闭集合时，再为该集合定义判别联合，不建立全局命令 registry。

### 13.7 高级原始事件入口

普通 Strategy API 不导出 `EventEnvelope[object]`。若未来确有诊断、录制或未知事件透传调用者，
可以在独立的 advanced/experimental 包提供版本化 raw envelope；它不得成为模板、教程或稳定
StrategyProtocol 的必选回调。

## 14. Strategy State

### 14.1 公共能力

`MutableMapping[str, object]` 不能作为标准公共接口。第一阶段提供 JSON-safe 的类型化状态能力：

```python
class StrategyState(Protocol):
    def contains(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...

    def get_int(self, key: str, default: int = 0) -> int: ...
    def get_decimal(self, key: str, default: Decimal = Decimal("0")) -> Decimal: ...
    def get_str(self, key: str, default: str = "") -> str: ...
    def get_bool(self, key: str, default: bool = False) -> bool: ...

    def set_int(self, key: str, value: int) -> None: ...
    def set_decimal(self, key: str, value: Decimal) -> None: ...
    def set_str(self, key: str, value: str) -> None: ...
    def set_bool(self, key: str, value: bool) -> None: ...

    def increment(self, key: str, amount: int = 1) -> int: ...
```

`bool` 必须在 int 检查前明确排除，因为 Python 中 `bool` 是 `int` 的子类。
Decimal 以十进制字符串持久化，不以 float 持久化。

### 14.2 错误与恢复

类型不匹配时产生 `StrategyStateTypeError`，至少包含 strategy ID、instance ID、key、期望类型和
实际 JSON 类型。不能静默调用 `int(value)`、`str(value)` 自动纠正损坏状态。

State 仍是 instance-scoped、版本化、JSON-only、原子 checkpoint。checkpoint 生命周期由 runtime
管理；普通策略不需要在每次回调后手工调用。

复杂 typed state schema 只有在出现多个真实调用者后再设计。第一阶段不引入通用泛型 state、
Pydantic dependency 或自动 migration registry。

## 15. 查询、缓存与回调阻塞规则

Strategy 回调是确定性事件循环的一部分。Context 中看似读取的方法必须有明确语义：

- `ctx.reference.*`、`ctx.account.*`、`ctx.risk.*`、`ctx.execution.*` 查询默认同步读取各业务模块
  已发布的 immutable mmap projection；Market 的 `latest_*` 使用同一规则；
- 查询返回 snapshot generation 和 event sequence，必要时暴露 freshness；
- 标准同步回调内不执行隐藏的网络、socket 或 Aeron request/response 阻塞；
- 不定义跨业务 `SnapshotEnvelope`，也不由 Strategy Context 缓存 `payload: object`；每个业务模块的
  projection reader 直接返回本模块 application model；
- 需要远程或历史加载的操作使用显式 async/request API，并通过完成事件交回 Strategy；
- backtest projection 只能读取不晚于当前 time frontier 的事实，防止 look-ahead；
- gap、stale 或 resync 状态不能伪装成正常空结果。

例如 `position(...) is None` 表示当前 snapshot 中没有仓位；Account projection 不可用或已过期时，
应产生 `ApplicationUnavailableError` 或返回携带 freshness 的结果，不能同样返回 `None`。

## 16. 错误模型

公共异常层级建议为：

```text
StrategySdkError
  ApplicationUnavailableError
  AmbiguousReferenceError
  ReferenceNotFoundError
  StaleProjectionError
  ContractDecodeError
  UnsupportedContractVersionError
  UnsupportedContractValueError
  StrategyStateError
    StrategyStateTypeError
  StrategyCommandRejectedError
```

业务拒绝优先通过 typed command receipt 表达；编程错误、contract 损坏、能力不可用或无法建立可靠
语义时使用异常。错误不得泄露 credential、authorization header、provider secret 或完整 raw payload。

每个 mapper 错误至少记录：

- producer/module；
- stream ID、sequence 和 schema version；
- event discriminator；
- 失败字段和安全的值摘要；
- strategy/launch/instance（如果已经绑定）。

## 17. Aeron 与 FlatBuffers 映射规则

### 17.1 generated 类型永不越过 infrastructure

FlatBuffers generated class 只允许出现在 `kairospy.infrastructure.transport.generated` 和紧邻的
decoder 中。公共模型、StrategyContext Protocol、模板、示例和用户测试不得导入 generated 类型。

### 17.2 Decoder 与 mapper 分离

- decoder 验证 file identifier、schema version、required field、vector 边界和 wire encoding；
- mapper 将已验证 wire DTO 转成模块语义明确的公共模型；
- dispatcher 根据已验证 domain 调用生命周期回调，并交付对应的类型化判别联合；
- 不允许在 Strategy callback 中延迟解析 FlatBuffer buffer，因为 buffer 生命周期和异常会泄露到
  用户边界。

### 17.3 映射表

每个公开切片维护明确映射：

| Wire/contract fact | Public type | 转换规则 |
|---|---|---|
| decimal string/value | `Decimal` | 直接从字符串构造 |
| unix nanos | UTC `datetime` + raw nanos | 统一 helper，测试边界值 |
| canonical instrument ID | `InstrumentId` | 验证格式，不反解析 provider symbol |
| market ID | `MarketId` | 保留 Market/Instrument 区别 |
| enum discriminator | public Enum | 未知值按 contract 规则失败或 `UNKNOWN` |
| optional scalar | `T | None` | 不使用空字符串代替缺失 |
| vector | immutable `tuple` | 解码后与 buffer 生命周期脱离 |
| provider raw payload | 不映射 | 保留在 Integration/transport 边界 |

### 17.4 版本和兼容

- wire schema version 与 Python package version 独立；
- mapper 明确声明支持的 schema version；
- 新增 optional 字段可以向后兼容，删除、重命名或改变语义必须升 contract version；
- 公共 Python dataclass 新增字段时优先提供兼容默认值，但不得用默认值掩盖 required wire 字段缺失；
- unsupported version 在启动 readiness 或收到首条消息时明确失败，不能降级成 `object`。

## 18. backtest、paper 与 live 一致性

三种模式共享：

- 同一 `Strategy` 生命周期；
- 同一 `StrategyContext` 能力协议；
- 同一 Reference/Market/Account/Risk/Execution 公共模型；
- 同一命令 receipt 和 lifecycle event；
- 同一业务时间 API；
- 同一状态持久化和错误语义。

composition 只替换事实来源与具体实现：

```text
backtest -> replay projection + simulated Execution
paper    -> live/replay Market + paper Account/Execution
live     -> live business process projections + provider-backed Execution
```

不得在 Strategy 中出现：

```python
if ctx.mode == "backtest":
    # use a different order API
```

如果某个 Application 在某模式不可用，composition 应在启动 readiness 阶段失败，或对应 facade
明确报告 unavailable；不要把属性设置成无类型 `None` 后等待用户运行到某行才失败。

## 19. 可观测性与安全

Runtime 自动为日志和命令附加：

```text
strategy_id
launch_id
instance_id
event stream/sequence
business time
request_id
intent_id/order_id（已知时）
```

公共 `ctx.logger` 保持结构化日志接口。用户无需手工重复 identity 字段。

严禁在公共事件、异常或日志中记录：

- provider credential、token、签名或 authorization header；
- 未脱敏的账户 secret；
- 完整 raw provider payload；
- FlatBuffer backing buffer 内容。

mapper latency、decode failure、unsupported schema、projection freshness、callback duration 和 command
receipt status 应有计数与耗时指标。

## 20. 静态和运行时验证

### 20.1 类型检查

Pyright 必须包含：

```text
kairospy
examples
tests/public_api 或等价的 SDK contract fixtures
```

每个文档示例应作为可执行或可类型检查 fixture。验收要求：

- 无 `reportArgumentType`；
- 无 `reportAttributeAccessIssue`；
- 标准示例无 `Any`、`cast()`、`type: ignore`；
- callback override 与公共基类签名一致。

### 20.2 Mapper contract tests

每个公开事件至少覆盖：

- 正常完整 payload；
- optional 字段缺失；
- Decimal 精度；
- unix nanos 转换；
- 未知 enum；
- 错误 schema version；
- 空/非法 ID；
- event discriminator 与 payload 不匹配；
- vector/buffer 生命周期脱离。

### 20.3 行为测试

- typed `BarEvent` 只通过 `on_market` 交付，且 `event.data` 静态类型为 `Bar`；
- callback 前 projection 已更新；
- Account/Risk event 只对绑定的 Strategy/account scope 可见；
- 多 application stream 保留各自 sequence，并产生可复现的 instance dispatch sequence；
- backtest 不可读取未来 snapshot；
- Strategy 停止自动释放订阅 owner；
- `target_position` 返回 accepted receipt 后，状态通过 intent event 推进；
- Risk 拒绝不会产生 exchange order；
- Account position 只能由 Account facts 更新；
- transport gap 进入 resync/stale，不交付伪连续事件；
- state 类型损坏产生稳定、可定位错误。

### 20.4 架构检查

静态搜索应拒绝：

- `kairospy.strategy` 导入 `kairospy.infrastructure`；
- examples 导入 application services、transport 或 generated code；
- 公共 API 返回 `Mapping[str, Any]`、裸 `dict` 或 `payload: object`；
- Strategy 为已有 application contract 类型复制 `StrategyBar`、`StrategyOrder` 等同义模型；
- 在已有具体 Application 可满足调用者时导出同义 `XxxCapability` 公共 Protocol；
- 为只有一个生产实现的 business application 增加 `ports.py`、内部 command/query Protocol 或
  仅转发参数的 bound adapter；
- Strategy 获得 provider client、Aeron publication/subscription 或 FlatBuffer table；
- `ctx.target_position`、`ctx.orders`、`ctx.execution` 三套同义入口长期并存；
- Strategy 直接调用 OrderEntryConnection；
- Python mapper 修改模块权威状态。

## 21. 迁移计划

### Phase 0：冻结公共边界

1. 将本文作为 Strategy SDK 目标设计。
2. 盘点当前所有 `kairospy.strategy` exports、Context 属性、examples 和 workspace template。
3. 为现有公共调用者建立使用清单，不按文件数量推测兼容需求。
4. 将 examples 加入 Pyright，记录当前 baseline，但不通过 ignore 消除错误。

退出标准：当前入口、调用者、错误和删除目标全部可追踪。

### Phase 1：首个 Bar vertical slice

1. 增加公共 `InstrumentId`、`MarketId`、`InstrumentRef`、`Market`、`Bar`、`DataEvent[T]`
   和 `BarEvent`。
2. 增加严格的 FlatBuffer/wire Bar 到公共 Bar mapper。
3. Runtime 将 Bar 映射为 `BarEvent`，并通过 typed `on_market(ctx, event)` 分发。
4. 增加 `ctx.reference.require_market()`。
5. 增加 `ctx.market.subscribe_bars()`。
6. 增加 `ctx.state.increment()` 和 typed state errors。
7. 将 `target_position` 标准入口迁到 `ctx.execution.target_position()`。
8. 迁移 SPY hourly example 和 workspace template。

退出标准：目标示例零类型错误；backtest 行为不变；Strategy 不导入 transport 类型。

### Phase 2：Quote/Trade 与 Account

1. 增加 Quote、Trade mapper，并扩展 `MarketEvent` 判别联合。
2. 增加 AccountSnapshot、Balance、Position 投影。
3. 在 Account application 已具备 typed event、Aeron stream、gap/resync 和 scope filtering 后，增加
   `AccountEvent` 与 `on_account`。
4. 删除示例中的 `payload: object` 和通用 account mapping 访问。
5. 验证 projection watermark、callback-before/after-update 和 instance dispatch sequence。

退出标准：quote 示例只使用公共类型；Account 不获得执行写能力。

### Phase 3：Risk 与 Execution lifecycle

1. 增加 typed RiskStatus。
2. 在 Risk application 已具备可靠 event contract 和 scope filtering 后，增加 `RiskEvent` 与
   `on_risk`。
3. 增加 IntentReceipt、ExecutionIntent、IntentUpdate、Order、OrderUpdate、Fill。
4. 增加 `submit_order`、`cancel_order`、`replace_order`、scoped `cancel_all` 及 typed receipt。
5. 将 Execution lifecycle variants 通过 typed `on_execution` 联合交付。
6. 明确 accepted receipt、risk rejection、indeterminate delivery 和 reconciliation 的表现。

退出标准：策略可以完全通过 typed projection 观察 intent 到 fill 的生命周期；Risk 仍由 Execution
主链路调用。

### Phase 4：删除兼容路径

在所有仓库调用者迁移后删除：

- 顶层 `context.subscribe()`；
- 顶层 `context.target_position()`；
- `context.orders`；
- `context.accounts` / `context.portfolio` 重复别名；
- 旧 `on_data(EventEnvelope[object])` 和 `on_intent(EventEnvelope[object])` 生命周期入口，分别迁移为
  `on_market(MarketEvent)` 和 `on_execution(ExecutionEvent)`；
- 公共 `MutableMapping[str, object]` state；
- contracts 对 transport `*View` 的公共重导出；
- `infrastructure.contracts.market`、Market command port 和 Strategy Market bound adapter；
- `application/execution/ports.py`、Strategy Execution bound/query adapter 和 disabled fake port；
- Risk `Mapping[str, Any]` 公共结果。

兼容期最多跨一个明确发布窗口，并通过 `DeprecationWarning`、迁移文档和静态搜索跟踪；
不保留无当前调用者的永久 compatibility facade。

### Market 收敛状态（2026-08-13）

Market 纵向切片已经完成内部删层：`MarketApplication` 直接持有具体 command client 和 mmap
projection reader；`application/market/ports.py`、`infrastructure/contracts/market.py`、
`BoundMarketCommands` 与 `MarketSnapshotQueries` 已删除。`ctx.market` 的公共 API、Market 的 Rust
状态所有权以及跨进程协议语义保持不变。Execution、Account、Risk 和 Reference 仍按独立纵向
切片迁移，不通过一次性通用重构处理。

### Execution 收敛状态（2026-08-13）

Execution 纵向切片已经完成内部删层：`ExecutionApplication` 直接持有具体 command client 和 mmap
projection，并拥有策略调用身份、事件因果和 request ID。`application/execution/ports.py`、
`BoundExecutionCommands`、`ExecutionQueries`、`DisabledIntentCommandPort` 与共用的
`StrategyCommandScope` 已删除；只包一层 applications 的 `StrategyApplicationRuntime` 也随之删除。
没有 Execution endpoint 时由 Application 直接返回 typed rejected receipt；`ctx.execution`、交易
安全策略、Execution Rust 状态所有权和跨进程投递语义保持不变。Account、Risk 和 Reference
继续按独立纵向切片迁移。

### Account 收敛状态（2026-08-13）

Account 纵向切片已经完成内部删层：`AccountApplication` 直接持有
`Mapping[AccountId, AccountMmapProjection]`，并负责 launch scope 内的账户选择和拒绝。
Strategy composition 中的 snapshot closure、`Mapping[str, object]` 以及
`getattr/callable/isinstance` 动态适配已删除。多账户映射被保留，因为它表达真实的 launch
账户隔离语义；`ctx.account`、Account Rust 状态所有权和 mmap projection 语义保持不变。
Risk 和 Reference 继续按独立纵向切片迁移。

### Risk 收敛状态（2026-08-13）

Risk 纵向切片已经完成内部删层：`RiskApplication` 直接持有具体
`RiskMmapProjection | None`，并将 projection 不可用作为明确的 Application 边界错误。
Strategy composition 中的 status closure、`risk: object` 以及
`getattr/callable/isinstance` 动态适配已删除。`ctx.risk.status(account=...)`、Risk Rust 状态
所有权、账户过滤和 mmap projection 语义保持不变。

### Reference 收敛状态（2026-08-13）

Reference 纵向切片已经完成内部删层：`ReferenceApplication` 直接持有具体
`ReferenceClient | None`，并通过该 client 读取 Reference SQLite current-state read model。
Strategy composition 中的 `Callable`、bound `reference.markets` 注入和 `client(...)` 工厂已删除；
Application 包也不再反向公开基础设施 client。`ctx.reference.find_markets/require_market/market`
仍然只返回不可变业务类型，Reference Rust 状态所有权、SQLite publication 和查询语义保持不变。

## 22. 第一阶段 API 验收清单

首个 Bar slice 完成时，必须同时满足：

1. 用户只导入 `kairospy.strategy`。
2. `on_market` 参数为 `MarketEvent`，Bar 分支中的 `event.data` 静态推断为 `Bar`。
3. `bar.open/high/low/close/volume` 是 `Decimal`。
4. `bar.instrument` 是 canonical `InstrumentRef`。
5. Reference 查询对零匹配和多匹配明确失败。
6. Market subscription 不要求 selector 字符串。
7. State counter 不读取 `object`。
8. 所有 Intent 和订单写操作只通过 `ctx.execution`；不存在顶层或 `ctx.orders` 重复入口。
9. 用户不能获得 OrderEntryConnection 或 provider client。
10. accepted command 不被误认为最终成交。
11. backtest、paper 和 live 共享接口。
12. examples 进入 Pyright 和行为测试。
13. mapper 覆盖错误 schema、非法字段和精度测试。
14. 旧 Bar 路径在迁移完成后被删除，而不是永久双轨。

## 23. 需要保持的设计纪律

后续增加 SDK 能力前必须回答：

1. 哪个真实策略调用者现在需要它？
2. 对应事实或状态由哪个业务模块拥有？
3. 现有 Reference、Market、Account、Risk、Execution 或 State 能力为何不足？
4. 它是 command、query 还是 event？同步和失败语义是什么？
5. 哪些 wire/internal 字段被有意隐藏？
6. mapper、类型检查和行为测试如何证明它可用？
7. 新入口迁移后删除哪个旧入口？

没有清楚答案时，不新增 manager、registry、万能 request、raw payload escape hatch 或第二套 facade。

## 24. 最终结论

Kairos 应为 Reference、Market、Account、Risk 和 Execution 提供面向策略的 Python 映射，但该映射
是稳定、只读、use-case-driven 的业务投影，不是模块内部结构体的逐字段副本。

Strategy 作者看到的是：

```text
stable lifecycle + typed discriminated events
+ typed business facts
+ application-oriented context
+ Execution intent/order commands
+ deterministic state and clock
```

Strategy 作者看不到的是：

```text
Aeron
FlatBuffers generated classes
transport envelope
provider payload
socket/mmap client
persistence record
business service instance
```

这一边界使 Python API 便捷、一目了然，同时保持 Rust 业务模块的唯一状态所有权、跨进程协议的
可演进性，以及 backtest、paper、live 的一致行为。
