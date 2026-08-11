# 领域身份与标识符规范

## 1. 文档目的

当前系统在多个边界使用 `market_id`、`instrument_id`、交易所 symbol 和 provider symbol。它们目前大多以 `String` 传递，且部分 Integration 代码通过拼接字符串或 `strip_prefix` 推断身份。

这会造成以下风险：

- `Instrument`、`Listing`、`Market` 和执行通道的身份被混用；
- provider symbol 进入跨模块业务接口后，成为隐含的系统身份；
- 同一个交易对象在不同交易所的关系无法稳定表达；
- `market_id` 和 `instrument_id` 可以被调用方任意互换；
- ID 格式、大小写、空值和历史稳定性没有统一验证；
- 业务层只能依赖字符串约定，编译器无法发现身份传错。

本文固定身份模型、字符串编码规则、代码归属和迁移步骤。本文是后续 Reference、Market、Execution、Account、Risk 和 Integration 改造的共同约束。

## 2. 设计目标与非目标

### 2.1 目标

1. 每种领域身份有唯一的语义和所有者。
2. 业务应用层使用强类型 ID，不直接使用裸 `String` 表示身份。
3. provider 的外部 symbol 只存在于 Integration/contract 边界。
4. 一个 Instrument 可以关联多个 Listing；一个 Market 可以关联多个 TradingSession 和 ExecutionAccess。
5. ID 的解析、格式化、序列化和错误信息可测试、可审计。
6. 迁移过程中可以逐模块推进，最终删除字符串兼容路径。

### 2.2 非目标

- 不建立一个万能的 `Identifier` 或 `EntityId` 来替代所有领域身份；
- 不要求所有 ID 都使用 UUID；
- 不把 provider 的命名强行改造成系统内部的 canonical ID；
- 不在 domain 层引入 SDK、FlatBuffers、数据库记录或 Integration 实现；
- 不为了目录形式统一而新增 manager、registry 或 protocol 层。

## 3. 领域对象模型

```text
Asset
  └── Instrument
        └── Listing
              └── Market
                    └── ExecutionAccess
```

### 3.1 Asset

资产是 BTC、USDT、USD 等可持有或结算的对象。Asset 不是交易市场，也不是交易所 symbol。

示例：

```text
asset:BTC
asset:USDT
```

### 3.2 Instrument

Instrument 是稳定的、可被业务引用的金融工具本体。它表达交易对象及其经济属性，不表达某个 provider 的连接方式。

在本系统中，现货 Instrument 表示标的资产，不表示某一个 quote pair。BTC/USDT 可以表达为：

```text
instrument:spot:BTC
```

`USDT` 是具体 Market 的 quote asset。同一个 `instrument:spot:BTC` 可以同时存在 BTC/USDT、BTC/USDC 等多个 Market。

永续合约和期权应携带足以区分经济条款的规范属性，例如：

```text
instrument:perpetual:BTC-USDT
instrument:option:BTC-USDT:20261225:100000:C
```

所有衍生品到期日统一使用 UTC 日历日期 `YYYYMMDD`。禁止将 provider 的
epoch 秒、毫秒、纳秒或 epoch day 直接拼入 Instrument ID；这些值只保存在
`expiry_unix_nanos` 等结构化字段中。永续合约没有到期日，因此不得伪造到期日
参与身份。

对于 Instrument 的具体字段，优先使用结构化字段表达，不依赖从 ID 反向解析全部业务属性。

### 3.3 Listing

Listing 表示 Instrument 在 Exchange 的某个产品/结算上下文中的挂牌关系。由于同一个标的可以有多个 quote asset，Listing 在需要区分挂牌对手资产时必须包含 quote/settlement 关系；否则它就无法唯一定位 BTC/USDT 与 BTC/USDC。

```text
listing:binance:spot:BTC:USDT
```

Listing 持有交易所的外部挂牌代码，例如 Binance 的 `BTCUSDT`，但该代码不是 Instrument 的 canonical identity。若系统确认 Listing 与 Market 永远一一对应，则可以删除独立 Listing 层；不能保留一个语义不清且无法唯一定位的中间对象。

### 3.4 Market

Market 表示可获得行情、具有交易规则并可以被选择的具体市场。Market 通常绑定 Exchange、Listing、市场类型和规则。

```text
market:binance:spot:BTCUSDT
```

Market 可以包含：

- `instrument_id`；
- `listing_id`；
- `exchange_id`；
- `market_type`；
- base/quote asset；
- price tick、quantity tick、precision；
- minimum quantity、minimum notional、contract size；
- 生效和失效时间。

### 3.5 TradingSession 与 MarketPhase

盘前、正式交易、盘后、夜盘和集合竞价通常不是不同的 Market，而是同一个 Market 下的交易时段或市场阶段：

```text
Instrument(AAPL common stock)
  └── Listing(NASDAQ AAPL/USD)
        └── Market(NASDAQ AAPL)
              ├── TradingSession(pre-market)
              ├── TradingSession(regular)
              ├── MarketPhase(opening-auction)
              ├── MarketPhase(continuous)
              ├── MarketPhase(closing-auction)
              └── TradingSession(after-hours)
```

因此默认关系基数为：

```text
Instrument 1 ── N Listing 1 ── 1 Market 1 ── N TradingSession
                                      └────── N ExecutionAccess
```

当前领域模型固定 `Listing 1:1 Market`。`TradingSession` 负责交易日历、开始/结束时间和 session 状态；`MarketPhase` 负责集合竞价、连续竞价、收盘竞价等阶段。不能为了表达盘前盘后、夜盘或不同上市板块而复制 Market。

如果未来发现同一挂牌关系下确实存在两个独立 order book，并且它们需要独立订阅、下单、规则和生命周期，再基于真实业务证据引入新的市场分组关系。当前不预先建模这种关系。

### 3.6 Listing 与 Market 不合并

虽然当前默认基数是一对一，Listing 和 Market 仍然保留为两个领域概念：

```text
Listing = 证券/标的被交易所挂牌的关系
Market  = 该挂牌关系对应的可交易市场和交易规则
```

两者的生命周期也可能不同。例如股票暂时停牌时：

```text
Listing.status = active
Market.status  = halted
```

退市时才会使 Listing 进入 `delisted`，并使 Market 进入 `inactive`。因此不能因为当前是一对一关系，就把两个领域概念当作同一个对象。

字段所有权必须明确：

```rust
pub struct Listing {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: ExchangeId,
    pub ticker: ProviderSymbol,
    pub currency_asset_id: AssetId,
    pub status: ListingStatus,
    pub effective_from: Timestamp,
    pub effective_to: Option<Timestamp>,
}

pub struct Market {
    pub market_id: MarketId,
    pub listing_id: ListingId,
    pub market_type: MarketType,
    pub base_asset_id: Option<AssetId>,
    pub quote_asset_id: Option<AssetId>,
    pub trading_rules: TradingRules,
    pub status: MarketStatus,
}
```

其中：

- Listing 拥有 Instrument、Exchange、ticker 和挂牌生命周期；
- Market 拥有交易规则、交易状态、交易日历和可交易属性；
- ExecutionAccess 拥有 provider symbol 和执行路由；
- Market 不重复保存 Listing 的 ticker、exchange symbol 或挂牌状态；
- ExecutionAccess 不通过 InstrumentId 猜测 provider symbol，而是引用 MarketId。

物理存储可以在一个事务中保存，甚至在早期实现中使用同一张记录，但应用 API 和 domain 类型必须保留清晰边界。

建议的领域类型为：

```rust
pub struct TradingSession {
    pub session_id: TradingSessionId,
    pub market_id: MarketId,
    pub session_type: TradingSessionType,
    pub calendar_id: TradingCalendarId,
    pub opens_at: Timestamp,
    pub closes_at: Timestamp,
    pub status: SessionStatus,
}

pub struct MarketPhase {
    pub phase: MarketPhaseKind,
    pub market_id: MarketId,
    pub session_id: TradingSessionId,
    pub starts_at: Timestamp,
    pub ends_at: Option<Timestamp>,
}
```

### 3.7 ExecutionAccess

ExecutionAccess 表示通过某个 provider 或 broker 执行某个 Market 的具体路径。它允许“行情来源”和“执行来源”不是同一个系统，也允许同一个 Market 通过多个 broker 或账户执行。

```text
execution-access:binance:spot:BTCUSDT
```

ExecutionAccess 负责保存 `market_id`、provider symbol、连接能力、结算资产和执行状态；它不能被当成 Instrument 或 Market 传给其他业务模块。

## 4. 标识符分类

| 类型 | 所有者 | 语义 | 是否允许 provider scope | 示例 |
|---|---|---|---:|---|
| `AssetId` | Reference | 资产本体 | 否 | `asset:BTC` |
| `InstrumentId` | Reference | 金融工具/标的本体 | 否 | `instrument:spot:BTC` |
| `ListingId` | Reference | Instrument 在 Exchange、产品和结算上下文中的挂牌关系 | 是，必须是 exchange，不是任意 provider | `listing:binance:spot:BTC:USDT` |
| `MarketId` | Reference/Market | 具体行情和交易规则市场 | 是 | `market:binance:spot:BTCUSDT` |
| `ExecutionAccessId` | Integration/Reference | 具体执行访问路径 | 是 | `execution-access:binance:spot:BTCUSDT` |
| `ExchangeId` | Reference | 交易所或交易场所 | 否 | `exchange:binance` |
| `ProviderSymbol` | Integration | 外部接口使用的代码 | 是 | `BTCUSDT` |
| `SourceId` | Market | 行情来源实例或连接 | 是 | `source:binance:spot` |

`market_id` 和 `instrument_id` 不得因为目前恰好是一一对应，就被设计成同一个身份的不同前缀。

## 5. 编码规则

### 5.1 通用规则

- 使用 ASCII 小写 kind 前缀；
- 使用 `:` 分隔命名空间；
- 业务代码和 symbol 的大小写由各领域类型决定，不由调用方决定；
- 不允许首尾空白、空段、控制字符和隐式 trim；
- ID 只能通过所属类型的构造函数创建；
- `Display` 输出 canonical form；
- JSON、FlatBuffers、CLI 和数据库使用 canonical form 的字符串编码；
- canonical ID 一旦发布，不因为 provider symbol 改名而重写；
- provider symbol 改变应通过 Listing/ExecutionAccess 的变更表达。

### 5.2 不把所有字段编码进 ID

ID 用于稳定寻址，不是完整业务记录。以下字段必须保留为结构化字段：

- Instrument 的产品类型和衍生品条款；
- Market 的交易规则；
- Listing 的状态和有效区间；
- provider symbol 的来源、版本和有效期。

ID 需要能够区分实体，但不应成为唯一的业务解析入口。

## 6. 代码结构与依赖规则

### 6.1 共享值对象

当前工作树已有候选基础 crate：

```text
crates/kairos-domain-types/
```

该 crate 只放跨业务模块真正共享且不依赖基础设施的值对象，例如：

- `AssetId`；
- `InstrumentId`；
- `MarketId`；
- `ListingId`；
- `ExchangeId`；
- `ExecutionAccessId`；
- `ProviderSymbol`；
- 受约束的 `Price`、`Quantity` 和 `Decimal`。

它不得依赖 `kairos-integration`、Reference service、transport 或 SDK。

### 6.2 Reference domain

Reference 是实体身份和关系的权威所有者。建议在以下位置逐步落地：

```text
crates/business/reference/service/src/domain/ids.rs
crates/business/reference/service/src/domain/entities.rs
crates/business/reference/service/src/domain/catalog.rs
```

Reference domain 负责：

- Instrument、Listing、Market 的关系完整性；
- ID 的唯一性和稳定性；
- 资产、交易所和产品引用校验；
- 生命周期和历史有效区间。

### 6.3 Application 层

Application API 使用领域类型：

```rust
pub struct SubmitOrderRequest {
    pub instrument_id: InstrumentId,
    pub market_id: MarketId,
}
```

Application 不暴露 provider payload、数据库记录或 Integration client。它可以调用 Reference application 查询规范事实，但不能导入 Reference service 的私有实现。

### 6.4 Contract 和边界

FlatBuffers、JSON、CLI、Python facade 和数据库记录可以继续使用字符串，但必须在边界完成显式转换：

```rust
let instrument_id = InstrumentId::try_from(raw_instrument_id)?;
```

反向编码统一使用：

```rust
instrument_id.as_str()
```

不允许在业务服务中到处调用 `format!("market:{exchange}:{symbol}")`。

### 6.5 Integration

Integration 只负责：

1. 读取 provider 原始字段；
2. 解析为 `ProviderSymbol`；
3. 通过 Reference 能力获取或创建规范 Instrument/Market 关系；
4. 将 provider 事实映射为模块拥有的领域事实。

Integration 不得凭 provider symbol 自行创建跨模块 canonical `InstrumentId`。

## 7. 推荐的 Rust 类型形态

```rust
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct InstrumentId(String);

impl InstrumentId {
    pub fn parse(value: &str) -> Result<Self, IdentifierError> {
        // 校验 canonical prefix、段数和字符规则
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct MarketId(String);

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct ProviderSymbol(String);
```

每个类型应实现：

- `TryFrom<&str>` 和 `TryFrom<String>`；
- `Display`；
- `AsRef<str>`；
- 序列化和反序列化；
- 结构化错误类型；
- canonical form 测试。

不要为方便迁移而实现 `From<String>`，因为这会绕过验证。

## 8. 分阶段推进方案

### 阶段 A：冻结规范，禁止新增裸字符串

交付物：

- 本文进入架构文档；
- 确认 `Asset`、`Instrument`、`Listing`、`Market`、`ExecutionAccess` 的所有权；
- 列出所有当前 ID 生成点和 `strip_prefix` 兼容逻辑；
- 新代码禁止新增 `String` 类型的领域 ID 字段。

检查：

```bash
rg -n 'format!\("(market|instrument):|strip_prefix\("(market|instrument):' crates kairospy
rg -n 'instrument_id: String|market_id: String' crates/business
```

### 阶段 B：实现值对象和解析测试

在 `kairos-domain-types` 或 Reference domain 中实现强类型 ID。先覆盖：

- `AssetId`；
- `InstrumentId`；
- `ListingId`；
- `MarketId`；
- `ExchangeId`；
- `ProviderSymbol`。

测试必须覆盖：

- canonical form；
- 空值、空段和首尾空格；
- 大小写规范；
- 错误前缀；
- 序列化往返；
- 不同类型之间不可互换。

### 阶段 C：迁移 Reference

优先修改 Reference 的实体、catalog、provider merge 和 application query：

```text
ReferenceInstrument.instrument_id -> InstrumentId
ReferenceListing.listing_id       -> ListingId
ReferenceMarket.market_id         -> MarketId
ReferenceMarket.instrument_id     -> InstrumentId
```

同时建立关系校验：

- Market 引用的 Instrument、Listing、Exchange、Asset 必须存在；
- Listing 引用的 Instrument 和 Exchange 必须存在；
- `resolve_market` 的零条、多条结果必须返回明确错误；
- 同一 canonical Instrument 可以通过多个 Listing 关联多个 Market；每个 Listing 对应一个 Market；
- refresh 不得因为 provider symbol 变化而创建新的 Instrument。

### 阶段 D：迁移 Market、Execution、Account 和 Risk

按照调用方向迁移 application request/result 和内部 domain：

```text
Reference -> Market -> Execution -> Account/Risk
```

重点是把以下字段从 `String` 改成对应类型：

- market subscription 的 market identity；
- market observation 的 market/instrument identity；
- order intent 的 instrument/market identity；
- account position 和 open order 的 instrument identity；
- risk reservation 的 instrument identity。

如果某个业务事实允许没有 Market，例如指数、利率或曲线点，应使用显式的 subject 类型，不把空字符串当成语义。

### 阶段 E：迁移 Integration 和 transport 边界

清理 provider adapter 中的：

```rust
strip_prefix("instrument:binance:")
strip_prefix("instrument:")
format!("market:{exchange}:{symbol}")
```

替换为：

1. 从 Reference 查询规范 Market；
2. 从 Market 获取 provider/exchange symbol；
3. 仅在 provider request 编码时使用 `ProviderSymbol`。

FlatBuffers schema 可以暂时保持 `string`，但所有 reader/writer 必须在 contract 边界转换为强类型。协议字段名仍然可以是 `instrument_id` 和 `market_id`，因为它们已经有明确的领域含义。

### 阶段 F：删除兼容路径并建立架构门禁

最后删除：

- provider symbol 推断 Instrument 的逻辑；
- 业务层的 `format!("market:...")`；
- 没有验证的 `String` ID 构造；
- 同时接受 symbol、market ID、instrument ID 的模糊参数；
- 为旧调用方保留的永久兼容 facade。

新增架构测试，阻止跨模块导入私有 service，阻止 application 暴露 provider payload，并检查业务 crate 中的裸 ID 字段。

## 9. CLI 与 Python API 迁移

CLI 是外部输入边界，因此可以接收字符串，但应立即解析并输出明确错误：

```text
kairos reference market get \
  --market-id market:binance:spot:BTCUSDT
```

不建议长期保留一个同时接受以下三种含义的参数：

```text
--symbol BTCUSDT
--instrument-id instrument:...
--market-id market:...
```

如果确实需要按 symbol 查询，应使用明确的 selector：

```text
--exchange-id exchange:binance \
--provider-symbol BTCUSDT
```

Python facade 的 request model 可以继续以 `str` 作为序列化输入，但应在 application validation 中调用同一套规范，不能维护另一套 ID 解析规则。

## 10. 兼容与数据迁移

### 10.1 不做静默重写

旧值如 `instrument:binance:BTCUSDT` 不能直接静默转换为新的 `instrument:spot:BTC`。这两个值可能代表不同语义，必须通过 Reference migration 映射并记录结果；同时必须从旧 Market 或 provider metadata 中确认 quote asset，不能仅凭 `BTCUSDT` 字符串猜测。

### 10.2 显式迁移表

迁移期间可以建立一次性映射：

```text
legacy_instrument_id -> canonical_instrument_id
legacy_market_id     -> canonical_market_id
provider_symbol      -> listing_id / execution_access_id
```

映射应有来源、时间、置信度和冲突处理结果。迁移完成后，业务数据只保存 canonical ID。

### 10.3 历史事件

历史事件必须保留原始 wire 值和迁移后的 canonical value 的对应关系，不能通过重新生成 ID 破坏审计链。对于无法确定的旧身份，应标记为 unresolved 并阻止进入需要精确身份的执行流程。

## 11. 验收标准

### 11.1 领域完整性

- Instrument 不包含 provider scope；
- Market 明确绑定 Exchange、Listing 和 Instrument；
- 一个 Instrument 可以有多个 Listing 和 Market；每个 Listing 对应一个 Market；
- provider symbol 不作为跨模块身份；
- 失效 Listing/Market 保留历史关系。

### 11.2 类型安全

- application、domain 和 services 不使用裸 `String` 表示领域 ID；
- `InstrumentId`、`MarketId`、`ListingId` 不能相互传递；
- 所有外部输入经过 fallible parsing；
- 所有外部输出使用 canonical formatting。

### 11.3 架构安全

- domain 不依赖 Integration、SDK、transport；
- application 不暴露 provider payload 或 persistence record；
- 跨模块只通过 application/contract 进入；
- concrete provider 选择只发生在 composition；
- 不存在第二个 mutable state owner。

### 11.4 检查命令

```bash
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

迁移完成前，额外执行：

```bash
rg -n 'format!\("(market|instrument):|strip_prefix\("(market|instrument):' crates kairospy
rg -n 'instrument_id: String|market_id: String' crates/business
rg -n 'instrument:binance|instrument:okx|instrument:ibkr' crates kairospy docs
```

这些搜索结果不能单独证明完成，但任何命中都必须能解释为 contract/fixture/legacy migration 或明确的边界代码。

## 12. 推荐的第一批实现切片

第一批不要同时改所有业务模块，建议只完成一个端到端切片：

```text
Reference Instrument/Market
  -> Market query
  -> Binance spot subscription
  -> Quote event
  -> Execution preflight
```

这个切片需要证明：

1. `instrument:spot:BTC` 可以独立于 Binance 存在；
2. `market:binance:spot:BTCUSDT` 能解析到唯一 Market，并引用 `asset:BTC`、`asset:USDT` 和 `instrument:spot:BTC`；
3. Binance adapter 从 Reference 获得 `BTCUSDT`，而不是从 InstrumentId 猜出来；
4. Quote、order intent 和 preflight 使用强类型 ID；
5. contract 只在边界把强类型 ID 编码为 string；
6. 同一个 Instrument 可以建立 BTC/USDT 和 BTC/USDC 两个 Market，而不复制 Instrument；
7. 第二个交易所可以为同一个 Instrument 建立另一个 Market，而不复制 Instrument。

## 13. 证券与股票主数据扩展

### 13.1 为什么股票必须保留 Listing

证券市场中，Listing 不是 Market 的别名，而是证券与交易场所之间的挂牌关系。相同的证券可以在多个交易场所挂牌，并拥有不同的 ticker、交易货币、市场板块、交易日历和挂牌生命周期。

```text
Instrument: Apple common stock
  ├── Listing: NASDAQ / AAPL / USD
  ├── Listing: Xetra / APC / EUR
  └── Listing: LSE / 0HDZ / USD
```

因此股票场景中，推荐使用：

```text
Instrument = 哪个证券
Listing    = 证券在哪里、以什么挂牌身份交易
Market     = 该挂牌对应的具体交易市场或订单簿
ExecutionAccess = 通过哪个 broker/provider/账户执行
```

如果某个产品的 Listing 与 Market 永远一一对应，且没有独立的挂牌生命周期，可以在该产品切片中省略 Listing；但证券主数据不能因为某个 provider 的简单模型而删除 Listing 能力。

### 13.2 股票的推荐关系

```yaml
asset:
  - asset_id: asset:fiat:USD

instrument:
  - instrument_id: instrument:equity:US:APPLE:common
    instrument_type: common_stock
    issuer_id: issuer:US:APPLE
    share_class: common

listing:
  - listing_id: listing:nasdaq:APPLE:common:USD
    instrument_id: instrument:equity:US:APPLE:common
    exchange_id: exchange:nasdaq
    ticker: AAPL
    currency_asset_id: asset:fiat:USD
    is_primary: true

market:
  - market_id: market:nasdaq:equity:AAPL
    listing_id: listing:nasdaq:APPLE:common:USD
    market_segment: equity
    trading_currency: asset:fiat:USD

execution_access:
  - access_id: access:ibkr:nasdaq:AAPL
    market_id: market:nasdaq:equity:AAPL
    provider_symbol: AAPL
```

`AAPL` 是 ticker，不是全球 Instrument 身份。Massive、IBKR、Binance 或其他 provider 返回的 `AAPL` 必须先经过证券主数据匹配，再映射到同一个 canonical Instrument；不能生成 `instrument:massive:AAPL` 或 `instrument:binance:AAPL` 作为跨模块身份。

### 13.3 股票 Instrument 不能只由 ticker 决定

股票 Instrument 至少需要支持以下结构化属性：

- issuer；
- security type；
- share class；
- voting rights；
- primary currency；
- country of incorporation；
- active、suspended、delisted 等生命周期状态；
- ISIN、CUSIP、FIGI 等外部证券标识；
- corporate action 关联。

否则系统可能错误合并普通股、优先股、ADR、CFD、权证和期权标的：

```text
AAPL common stock
AAPL preferred stock
AAPL ADR
AAPL CFD
AAPL option underlying
```

这些对象必须由不同的 Instrument 或明确的 Product/Contract 类型表达。

### 13.4 股票 Listing 的字段

股票 Listing 建议逐步具备：

```rust
pub struct Listing {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: ExchangeId,
    pub market_segment_id: Option<MarketSegmentId>,
    pub ticker: ProviderSymbol,
    pub currency_asset_id: AssetId,
    pub country: Option<CountryCode>,
    pub is_primary: bool,
    pub listing_type: ListingType,
    pub lot_size: Quantity,
    pub status: ListingStatus,
    pub effective_from: Timestamp,
    pub effective_to: Option<Timestamp>,
}
```

关键约束是：

- 一个 Instrument 可以有多个 Listing；
- ticker 只在 exchange/market segment 范围内唯一；
- Listing 失效不删除历史记录；
- ticker 变化产生 Listing lifecycle event，而不是创建新的 Instrument；
- primary listing 与 secondary listing 必须可区分；
- listing currency、settlement currency 和 execution settlement asset 不应默认相同。

### 13.5 股票 Market 与 ExecutionAccess

Market 保存实际交易所需的市场事实：

- listing；
- order book 或交易场所；
- trading currency；
- tick size、lot size、minimum quantity；
- trading session 和 calendar；
- market status；
- short sale、fractional、auction 等市场能力。

ExecutionAccess 必须引用 Market，而不能只引用 Instrument：

```rust
pub struct ExecutionAccess {
    pub access_id: ExecutionAccessId,
    pub market_id: MarketId,
    pub provider_id: ProviderId,
    pub exchange_id: Option<ExchangeId>,
    pub provider_symbol: ProviderSymbol,
    pub settlement_asset_id: Option<AssetId>,
    pub status: AccessStatus,
}
```

下单时应使用 `MarketId` 或 `ExecutionAccessId`。provider adapter 必须从 Reference/ExecutionAccess 获取 provider symbol，不能通过以下逻辑猜测：

```rust
request.instrument_id.strip_prefix("instrument:equity:")
```

### 13.6 报价、交易和结算资产必须分开

股票、加密现货和经纪商产品可能出现不同的：

- base asset；
- quote/trading currency；
- settlement asset；
- account debit asset。

例如某个股票市场以 USD 报价，但 provider 账户以 USDC 结算。Reference 必须分别表达这些关系，不能让 `quote_asset_id` 和 `settlement_asset_id` 通过默认值隐式相等。

## 14. 不同产品的最终模型

### 14.1 加密现货

```text
Asset(BTC)
Asset(USDT)
  └── Instrument(spot BTC)
        └── Listing(Binance BTC/USDT)
              └── Market(Binance BTCUSDT)
                    └── ExecutionAccess(Binance BTCUSDT)
```

同一个 `instrument:spot:BTC` 可以对应：

```text
market:binance:spot:BTCUSDT
market:binance:spot:BTCUSDC
market:coinbase:spot:BTC-USD
```

### 14.2 股票

```text
Instrument(Apple common stock)
  ├── Listing(NASDAQ AAPL/USD)
  │     └── Market(NASDAQ AAPL)
  │           └── ExecutionAccess(IBKR NASDAQ AAPL)
  └── Listing(Xetra APC/EUR)
        └── Market(Xetra APC)
              └── ExecutionAccess(IBKR Xetra APC)
```

### 14.3 期权

```text
Instrument(Apple common stock)
  └── Instrument(Apple 2026-12-18 200 Call)
        └── Listing(CBOE)
              └── Market(CBOE option order book)
                    └── ExecutionAccess(broker option route)
```

期权 Instrument 必须保存 underlying、expiry、strike、option right、multiplier 和 settlement style，不能只依赖 provider option symbol。

## 15. 当前股票实现与目标架构的差距

当前工作树已经具备 Binance Equity、IBKR Equity、Massive Equity 的接入和 Reference/Market 骨架，但仍需完成以下迁移：

1. 将 `instrument:massive:<ticker>` 等 provider-scoped Instrument 改为 canonical Instrument；
2. 将股票 ticker 从 Instrument 身份中移出，放入 Listing/provider mapping；
3. 为股票 Instrument 增加 issuer、share class 和外部证券标识；
4. 为 Listing 增加 ticker、currency、primary、segment 和生命周期字段；
5. 让 ExecutionAccess 引用 `market_id`，而不只是 `instrument_id`；
6. 删除 `strip_prefix("instrument:equity:")` 等 symbol 推断逻辑；
7. 分开 quote/trading currency、settlement asset 和 account debit asset；
8. 增加跨 provider 的同一证券归并测试。

第一批股票验收数据至少应证明：

```text
Massive AAPL
IBKR AAPL
Binance AAPL
```

在有足够证券主数据证据时，可以归并到同一个 `Instrument`；不同交易所和 provider 只产生不同的 Listing、Market 或 ExecutionAccess，而不是复制 Instrument。

该切片完成后，再按同样模式迁移期货、永续、期权和其他 provider。这样可以用真实跨模块调用验证模型，而不是只完成孤立的类型替换。
