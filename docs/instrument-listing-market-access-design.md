# Instrument、Listing、Market 与 Access 统一设计规范

## 1. 文档地位

本文是 Kairos 对以下概念及其跨模块使用方式的权威设计：

- Asset；
- Instrument；
- Listing；
- Market；
- MarketDataAccess；
- ExecutionAccess；
- 运行时 execution route/binding；
- provider-native instrument reference。

当本文与
[`domain-identity-and-identifier-standard.md`](./domain-identity-and-identifier-standard.md)
中旧的 `Listing 1:1 Market`、`Market.exchange_id == Listing.exchange_id` 或
`ExecutionAccess 必须绑定 Market` 假设冲突时，以本文为准。SQLite 单写者、生命周期和消费者恢复
仍以 [`reference-sqlite-read-model-design.md`](./reference-sqlite-read-model-design.md) 为准。

本文先固定业务含义和所有权，再规定代码与数据迁移。实现不得为了兼容当前字段而反向改变这些含义。

## 2. 需要解决的问题

现有模型接近以下严格树：

```text
Asset -> Instrument -> Listing -> Market -> ExecutionAccess
```

它隐含了三个不成立的假设：

1. 一个 Listing 只产生一个 Market；
2. Market 的交易场所必须等于 Listing 的挂牌场所；
3. 每条 ExecutionAccess 在下单前都能确定一个直接成交 Market。

股票场景直接反驳这些假设。一个证券可以在多个交易所挂牌；同一挂牌证券也可以在多个交易场所形成
独立订单簿或流动性。经纪商 SMART/SOR 路由更可能在下单后才知道实际成交场所。

当前实现还存在以下具体问题：

- `Market` 同时保存 canonical market、行情来源和 provider symbol；
- Reference 已保存 `ExecutionAccess`，但 typed SQLite contract 不能按 Market/Instrument 查询它；
- Execution preflight 只读取 Market；
- Execution 通过拆分 `MarketId` 猜 provider 和 symbol；
- `exchange_id` 的挂牌、交易和实际成交角色没有通过字段名区分，同时又与 broker/provider 混用；
- Strategy/CLI 的 instrument、market 和 symbol selector 容易产生含义不明的唯一性假设。

## 3. 统一语义

### 3.1 Asset

Asset 是可以持有、计价、交割或结算的经济资产，例如 BTC、USD、USDT。Account balance 使用
`AssetId`，不能使用 `InstrumentId` 表示现金或币余额。

### 3.2 Instrument

Instrument 是具有稳定经济同一性的证券、金融产品或合约。它描述“交易的是什么”，不描述在哪里
挂牌、在哪里交易、从谁获得行情或通过谁下单。

Instrument 的身份必须由决定经济同一性的事实构成，例如：

- 股票：issuer、security type、share class；
- 现货产品：base/quote 产品关系；
- 期货：underlying、quote/settlement、expiry、contract terms；
- 期权：underlying、expiry、strike、right、multiplier、settlement style。

provider symbol、ticker、exchange、broker、provider 和 connection binding 均不属于 Instrument identity。

现货 Instrument 是否包含 quote asset 会改变现有 canonical ID，必须作为独立数据迁移决策完成。
本文采用统一语义的目标形态：现货 Instrument 表达可交易产品 `BTC/USDT`，Asset(BTC) 才表达
BTC 持有物。因此推荐目标 ID 为：

```text
instrument:spot:BTC-USDT
instrument:perpetual:BTC-USDT
instrument:future:BTC-USDT:20261225
instrument:option:BTC-USDT:20261225:100000:C
instrument:equity:US:APPLE:common
```

在完成显式迁移前，旧的 `instrument:spot:BTC` 不得静默重写。迁移表必须记录旧 ID、新 ID、来源、
置信度和冲突结果。

### 3.3 Listing

Listing 是 Instrument 与挂牌场所之间的上市/挂牌关系，回答：

> 哪个证券以什么身份、代码和币种在什么场所挂牌？

Listing 拥有：

- `instrument_id`；
- `listing_exchange_id`；
- ticker/listing symbol；
- listing currency；
- primary/secondary 属性；
- board/segment/listing type；
- 挂牌、暂停挂牌、退市及有效区间。

同一 Instrument 可以有多个 Listing。ticker 只在明确的 listing exchange/segment 范围内唯一。

ADR、存托凭证、优先股、CFD 或具有不同权利的 share class 不是同一 Listing 的不同 ticker；当经济
权利不同，它们必须是不同 Instrument。

### 3.4 Market

Market 是某个 Instrument 在一个实际交易场所中的独立流动性与交易规则边界，回答：

> 订单簿、成交、交易状态和交易规则发生在哪里？

Market 拥有：

- `instrument_id`；
- `trading_exchange_id`；
- 可选的 `reference_listing_id`；
- market/segment/type；
- tick、lot、minimum quantity/notional 等交易规则；
- trading currency；
- calendar、session 和 market phase；
- 市场状态和有效区间。

Market 不拥有行情 provider、执行 provider、provider symbol、账户或 connection binding。

Listing 和 Market 是 Instrument 下两类独立事实，不再是强制父子树：

```text
                         ┌── Listing(listing exchange A)
Instrument ──────────────┼── Listing(listing exchange B)
                         ├── Market(trading exchange A)
                         ├── Market(trading exchange C)
                         └── Market(trading exchange D)
```

`reference_listing_id` 只表达有证据的关联；它不是 Market 存在的前提。若存在该引用，Listing 与
Market 必须指向相同 Instrument，但 listing exchange 不必等于 trading exchange。

### 3.5 Exchange、Broker 与 Provider

当前业务不引入通用 `VenueId`。三个概念足以表达现有事实：

- Exchange：挂牌、订单簿、撮合、流动性或可报告成交发生的交易场所；
- Broker：接受客户订单、持有账户关系并承担执行或路由责任的中介；
- Provider：提供 API、连接、行情或外部事实的技术参与方。

系统中的 Exchange 采用较宽但具体的业务定义，覆盖传统交易所、加密交易所、ATS、dark pool、
internalizer 等具有独立市场或成交身份的场所。只有出现 Exchange 无法表达的真实调用者后，才考虑
增加新的场所抽象。

同一个机构可以同时扮演多个角色，例如 IBKR 同时是 Broker 和 Provider；角色相同不代表身份类型
可以互换。业务使用 `ExchangeId`、`BrokerId` 和 `ProviderId` 三种强类型 ID。

Reference 维护这三种 canonical business identity 及其生命周期；同一法律/业务实体可以通过
`EntityId` 关联多个角色。Integration 仍拥有 provider-native participant、鉴权、连接和协议，composition
负责把 Reference `ProviderId` 映射到本次运行的 Integration participant/binding。

这不要求新增三套 registry、manager 或独立 current table。现有 `reference_entities_current` 可以保存
Entity 及 Exchange/Broker/Provider role，Listing、Market 和 Access 通过具名 typed ID 引用它们；只有
出现独立查询规模或生命周期需求后，才考虑物理拆表。

Exchange 在 Listing、Market 和成交 Market 中承担不同角色，通过字段名区分，不新增对应的新 ID 类型：

```text
listing_exchange_id    挂牌和退市生命周期所属的 Exchange
trading_exchange_id    Market 的订单簿、交易规则或流动性所属的 Exchange
execution_market_id    某次成交实际发生的 canonical Market；其 Exchange 从 Market 派生
broker_id              接受账户订单并负责执行或路由的 Broker
provider_id            提供实际 API、连接或行情的 Provider
```

前三个字段共享 `ExchangeId`，但调用方不得因为类型相同而要求值相等。`BrokerId`、`ProviderId`
与 `ExchangeId` 必须是不同类型。Binance 直连可以是 Exchange + Provider 而没有 Broker；Massive
行情只有 Provider，不是 Exchange 或 Broker。

### 3.6 MarketDataAccess

MarketDataAccess 是通过某 provider 观察一个 Market 的静态地址与能力，回答：

> 通过谁、使用哪个 provider-native reference 获取这个 Market 的哪些数据？

建议模型：

```rust
pub struct MarketDataAccess {
    pub access_id: MarketDataAccessId,
    pub market_id: MarketId,
    pub provider_id: ProviderId,
    pub provider_address: ProviderInstrumentAddress,
    pub capabilities: MarketDataCapabilities,
    pub status: AccessStatus,
    pub effective_from: UnixNanos,
    pub effective_to: Option<UnixNanos>,
}
```

一个 Market 可以有多个 MarketDataAccess。Market composition 根据配置选择 concrete source，
Market Actor 拥有订阅和实时 observation 状态；Reference 只拥有静态 mapping 和生命周期。

### 3.7 ExecutionAccess

ExecutionAccess 是通过某 broker/provider 执行一个 Instrument 的静态可达路径和能力，回答：

> 这个 provider 能用什么地址、目标和能力执行该 Instrument？

它不等于运行中的账户连接，也不必总是提前绑定一个 Market。建议模型：

```rust
pub enum ExecutionTarget {
    DirectMarket { market_id: MarketId },
    SmartRoute {
        instrument_id: InstrumentId,
        reference_listing_id: Option<ListingId>,
        destination_policy: String,
    },
}

pub struct ExecutionAccess {
    pub access_id: ExecutionAccessId,
    pub instrument_id: InstrumentId,
    pub target: ExecutionTarget,
    pub broker_id: Option<BrokerId>,
    pub provider_id: ProviderId,
    pub provider_address: ProviderInstrumentAddress,
    pub settlement_asset_id: Option<AssetId>,
    pub capabilities: ExecutionCapabilities,
    pub status: AccessStatus,
    pub effective_from: UnixNanos,
    pub effective_to: Option<UnixNanos>,
}
```

约束：

- direct access 必须引用 Market；
- smart/SOR access 必须引用 Instrument，Market 可以在成交后才确定；
- access 的 provider symbol 不能从 InstrumentId 或 MarketId 推断；
- settlement asset、trading currency 和 account debit asset 不得隐式视为相同；
- Fill 应记录实际 `execution_market_id`，并保留 access/route/binding lineage。实际成交 Exchange
  从该 Market 的 `trading_exchange_id` 派生，不在 Fill 中重复保存。

如果 provider 只返回尚未能解析为 canonical Market 的成交场所代码，Integration/audit 可以暂存
`reported_execution_exchange` 等原始事实；它不进入核心 Fill identity，也不能伪造 `MarketId`。
解析失败的记录进入 reconciliation，待 Reference 映射后再补齐 `execution_market_id`。

### 3.8 ExecutionRouteBinding

ExecutionRouteBinding 是 Execution composition 创建的运行时绑定，不属于 Reference catalog：

```rust
pub struct ExecutionRouteBinding {
    pub route_id: ExecutionRouteId,
    pub execution_access_id: ExecutionAccessId,
    pub account_id: AccountId,
    pub segment_key: SegmentKey,
    pub principal_id: PrincipalId,
    pub integration_binding_id: IntegrationBindingId,
}
```

Reference 说明“可以怎样执行”；Execution composition 决定“本次实例通过哪个账户连接执行”。
credential、quota、connection health 和 channel epoch 均属于 Integration/运行时绑定，不进入 Reference。

### 3.9 Provider address 与 ProviderInstrumentRef

Reference contract 使用自己拥有的最小 `ProviderInstrumentAddress` 保存 access mapping，例如 symbol、
provider instrument type、destination 或稳定 provider contract key。它是 Reference-owned typed fact，
不是 SDK payload，也不能包含 credential、client 或 connection state。

Integration application 使用 `ProviderInstrumentRef` 表达 provider-native 请求地址。Execution/Market
composition 在选择 Access 与 concrete connection 后，将 `ProviderInstrumentAddress` 显式转换为
Integration 的 `ProviderInstrumentRef`。Reference domain/contract 不依赖 `kairos-integration` 类型，
Integration 也不能从 provider address 生成 canonical ID。

## 4. 基数与不变量

目标基数：

```text
Instrument 1 ── N Listing
Instrument 1 ── N Market
Market     1 ── N MarketDataAccess
Instrument 1 ── N ExecutionAccess
Market     1 ── N direct ExecutionAccess
ExecutionAccess 1 ── N runtime ExecutionRouteBinding
```

Reference promotion 必须验证：

1. Listing 引用的 Instrument 和 listing Exchange 存在；
2. Market 引用的 Instrument 和 trading Exchange 存在；
3. Market 若引用 Listing，两者的 Instrument 相同；
4. 不要求 listing Exchange 等于 trading Exchange；
5. 不要求每个 Listing 恰好存在一个 Market；
6. MarketDataAccess 引用的 Market 存在；
7. MarketDataAccess 和 ExecutionAccess 引用的 Provider 存在；
8. ExecutionAccess 的 optional Broker 若存在，必须引用已知 Broker；
9. direct ExecutionAccess 引用的 Market 存在且 Instrument 相同；
10. smart ExecutionAccess 的 optional Listing 若存在，Instrument 相同；
11. provider symbol 变化产生 access/listing lifecycle change，不重写 InstrumentId；
12. source 顺序不能成为 canonical 冲突的隐藏 winner。

## 5. 股票示例

### 5.1 多挂牌、多 Market

```text
Instrument(Apple common stock)
  ├── Listing(NASDAQ, AAPL, USD, primary)
  ├── Listing(Xetra, APC, EUR, secondary)
  ├── Market(NASDAQ order book, reference NASDAQ listing)
  ├── Market(IEX order book, reference NASDAQ listing)
  ├── Market(Cboe BZX order book, reference NASDAQ listing)
  └── Market(Xetra order book, reference Xetra listing)
```

IEX 和 Cboe BZX Market 可以交易 NASDAQ primary-listed 的同一 Instrument，因此不能校验
`market.trading_exchange_id == listing.listing_exchange_id`。

### 5.2 行情和执行

```text
Market(IEX AAPL)
  ├── MarketDataAccess(direct IEX feed)
  ├── MarketDataAccess(provider=Massive, AAPL/IEX)
  └── ExecutionAccess(provider=direct API, broker=None, direct IEX route)

Instrument(Apple common stock)
  └── ExecutionAccess(broker=IBKR, provider=IBKR, SMART, no fixed destination Market)
        └── ExecutionRouteBinding(account=main, principal=U123, binding=ibkr-main)
```

SMART order 提交前可以使用 consolidated view 或一个定价 Market；它们不是最终成交 Market。成交回报
需要记录实际 `execution_market_id`；其 Exchange 从成交 Market 派生。

### 5.3 Consolidated view

NBBO、consolidated tape 和跨 Exchange composite 不是新的实际 Market。它们属于 Market 拥有的派生视图：

```rust
pub struct MarketView {
    pub view_id: MarketViewId,
    pub instrument_id: InstrumentId,
    pub member_market_ids: Vec<MarketId>,
    pub aggregation_policy: AggregationPolicy,
}
```

MarketView 可以用于定价、策略观察和 Execution preflight，但不得冒充 Fill 的
`execution_market_id`。

## 6. 各模块的使用规则

### 6.1 Reference

Reference 拥有 Asset、Instrument、Listing、Market、Exchange、Broker、Provider、MarketDataAccess、
ExecutionAccess 的 canonical identity、关系、静态属性和生命周期。Reference 不拥有订阅、订单、
账户 binding、credential 或运行连接。

### 6.2 Integration

Integration 提供 provider-native catalog、market data、order entry/query/event capability，并输出
ProviderInstrumentRef 和 normalized external facts。Integration 不生成 canonical InstrumentId、
ListingId、MarketId 或 AccessId。

### 6.3 Market

Market 通过 Reference scoped projection 获取：

- Instrument/Market identity；
- MarketDataAccess；
- trading Exchange 和静态 market rules。

Market composition 选择 concrete MarketDataAccess 和 provider connection。Market Actor 拥有订阅、
observation、order book、freshness 和 MarketView。Market 不能使用 ExecutionAccess，也不能从 ID 猜
provider symbol。

### 6.4 Execution

Execution planning 可以以 InstrumentId 表达经济目标，以 MarketId/MarketViewId 表达定价或直接市场
约束。提交订单前必须得到唯一 ExecutionAccess 和唯一运行时 route binding。

一次 concrete order 至少记录：

```text
instrument_id
pricing_market_id or market_view_id (optional)
destination_market_id (direct route only)
execution_access_id
route_id
integration_binding_id
reference_generation/event_sequence
```

Execution 不得从 `market_id.split(':')`、`rsplit(':')` 或 InstrumentId suffix 构造 provider request。

### 6.5 Account

- Balance 使用 AssetId；
- Position 使用 InstrumentId；
- market/access/exchange 可以作为 position 或 fill provenance，不能替代 Instrument identity；
- provider private facts 先由 account binding scoped resolver 映射 canonical identity；
- missing/ambiguous mapping 必须拒绝，不能伪造 Market。

### 6.6 Risk

Risk scope 按指标选择身份，而不是只使用 `instrument_id + exchange_id`：

- asset exposure：AssetId；
- product/contract exposure：InstrumentId；
- exchange concentration：MarketId 或 trading ExchangeId；
- broker/route concentration：ExecutionAccessId；
- account/strategy limit：AccountId/StrategyId。

不增加万能 `ScopeId`；每个 policy metric 只接受其有业务意义的 selector。

### 6.7 Strategy、Research、CLI

- Strategy universe 使用 InstrumentId；
- 行情订阅选择 MarketId 或显式 Market selector；
- execution intent 使用 InstrumentId 和显式 routing constraints，不接收 ProviderInstrumentRef；
- Research 用 InstrumentId 表达跨 Exchange 经济产品，用 MarketId 保留 Exchange lineage；
- CLI/Python 字符串输入必须立即解析为具名 selector，并使用有界分页。

## 7. Reference 查询规范

`list`、`resolve` 和 `get` 必须有不同语义：

```text
get_instrument(InstrumentId) -> Option<Instrument>
list_instruments(InstrumentQuery, PageRequest) -> Page<Instrument>

get_listing(ListingId) -> Option<Listing>
list_listings(ListingQuery, PageRequest) -> Page<Listing>

get_market(MarketId) -> Option<Market>
list_markets(MarketQuery{instrument_id?, listing_id?, trading_exchange_id?}, PageRequest) -> Page<Market>
resolve_market(InstrumentId, MarketConstraints) -> Result<Market, Missing|Ambiguous>

get_market_data_access(MarketDataAccessId) -> Option<MarketDataAccess>
list_market_data_accesses(MarketId, AccessQuery) -> Page<MarketDataAccess>

get_execution_access(ExecutionAccessId) -> Option<ExecutionAccess>
list_execution_accesses(InstrumentId, ExecutionAccessQuery) -> Page<ExecutionAccess>
resolve_execution_access(InstrumentId, RoutingConstraints)
    -> Result<ExecutionAccess, Missing|Ambiguous>
```

规则：

- `list_*` 允许零到多条并始终有界；
- `get_*` 只按 canonical ID；
- `resolve_*` 要求唯一，零条和多条分别报错；
- 生产下单不能取 list 第一条作为隐式 winner；
- provider symbol 查询必须同时带 provider/exchange/product scope。

## 8. 数据模型变动

### 8.1 Reference contract

目标变动：

- `Listing.exchange_id` 重命名为 `listing_exchange_id`；
- `Market.exchange_id` 重命名为 `trading_exchange_id`；
- `Market.listing_id` 改为 `reference_listing_id: Option<ListingId>`；
- 从 Market 移除 `source_id/source_symbol`；
- 新增 MarketDataAccess；
- ExecutionAccess 新增 `instrument_id` 和 direct/smart target；
- contract application 类型使用强类型 ID，wire/SQLite 边界显式转换。

### 8.2 SQLite current tables

增加或调整：

```text
reference_listings_current(
  listing_id, instrument_id, listing_exchange_id, ticker, status, ...)

reference_markets_current(
  market_id, instrument_id, trading_exchange_id,
  reference_listing_id NULL, market_type, status, ...)

reference_market_data_accesses_current(
  access_id, market_id, provider_id, provider_instrument_key, status, payload)

reference_execution_accesses_current(
  access_id, instrument_id, routing_mode,
  destination_market_id NULL, reference_listing_id NULL,
  broker_id NULL, provider_id, provider_instrument_key, status, payload)
```

用于 scoping/join 的字段必须是 indexed column，不能只存在 JSON payload 中。

## 9. 当前实现差距

### P0：会产生错误路由或错误身份

1. Reference 校验要求 Market 的 Listing/Exchange 一致；
2. Execution 通过拆分 MarketId 构造 ProviderInstrumentRef；
3. Execution preflight 未读取 ExecutionAccess；
4. Market/CLI 仍有 `format!("market:...")` 和 provider-scoped Instrument 构造；
5. Execution balance preflight 通过 InstrumentId 的 `USDT` suffix 猜资产。

### P1：模型和 contract 缺口

1. 缺少 MarketDataAccess current table 和 typed query；
2. ExecutionAccess typed SQLite query/projection 缺失；
3. Listing/Market 的 Exchange 角色未通过字段名分离；
4. concrete order/fill 缺少 access、binding 和 actual execution Market lineage；
5. Risk scope 只有 instrument/exchange，不能准确表达 market/access risk。

### P2：API 和历史迁移

1. Instrument/Market/List APIs 的 selector 与唯一性语义未统一；
2. 现货 Instrument identity 仍与 Asset 重叠；
3. 历史 canonical IDs 需要显式 migration mapping；
4. composite MarketView 需要保留 member market lineage。

## 10. 迁移计划

### Slice 0：冻结语义和门禁

- 本文成为权威语义规范；
- 新代码禁止新增 `Market.exchange_id/source_symbol` 依赖；
- 新代码禁止解析 InstrumentId/MarketId 得到 provider facts；
- 为旧路径增加明确 migration 注释和静态搜索。

退出条件：新增架构测试能够阻止新的字符串推断和 `Listing 1:1 Market` 校验。

### Slice 1：Reference Exchange 角色与关系模型

- 使用现有 ExchangeId，并引入缺失的 BrokerId/ProviderId 强类型；不增加 VenueId；
- Listing 改为 listing Exchange；
- Market 改为 trading Exchange + optional reference listing；
- 更新 provider normalizer、promotion SQL 和关系测试；
- 增加一个 primary listing 对应多个 trading Exchange Market 的 fixture。

退出条件：NASDAQ-listed AAPL 可以同时拥有 NASDAQ、IEX、Cboe BZX Market，promotion 不冲突。

### Slice 2：MarketDataAccess

- 从 Market 移出 source/provider symbol；
- 新增 MarketDataAccess domain、contract、SQLite table 和 typed query；
- Market composition 用 access 选择 concrete source；
- 保留 Market Actor 的 source runtime identity，避免 Reference 成为订阅 owner。

退出条件：同一 Market 可由两个 provider 观察，切换 source 不改变 MarketId。

### Slice 3：ExecutionAccess 端到端

- 扩展 ExecutionAccess direct/smart target；
- 增加 typed SQLite query 和 scoped projection；
- Execution preflight 解析唯一 access；
- route binding 显式引用 access；
- ProviderInstrumentRef 只由 selected access 的 provider address 显式转换得到；
- 删除 MarketId `split/rsplit` 和 InstrumentId fallback。

退出条件：IBKR SMART 和 direct Exchange route 均能下单；provider symbol 不来自 canonical ID grammar。

### Slice 4：订单、成交与 Account lineage

- concrete order 保存 access/route/binding/reference watermark；
- fill 保存实际 `execution_market_id`，Exchange 从 Market 派生；
- Account resolver 保存 instrument 及可用的 market/access provenance；
- recovery/query/event 路径使用相同 binding-scoped mapping。

退出条件：SMART order 的 pricing view、requested route 和 actual execution Market 可分别审计。

### Slice 5：Risk、Strategy、Research 和 API

- Risk 按 metric 增加 Asset/Market/Access scope；
- Strategy universe/subscription/execution selector 分离；
- Reference Rust/Python/CLI 提供一致的 get/list/resolve；
- Research 保留跨 Exchange lineage 和 composite policy。

### Slice 6：Instrument identity 和历史迁移

- 执行现货 Instrument 包含 quote 的已批准目标迁移；
- 建立 legacy-to-canonical mapping 和冲突报告；
- 双读/影子比较后删除旧 ID 路径；
- 历史事件保留 original wire identity 与 canonical mapping lineage。

## 11. 测试和验收

必须新增的行为测试：

1. 一个 Instrument 多个 Listing；
2. 一个 Listing 关联多个不同 trading Exchange Market；
3. Market trading Exchange 与 Listing exchange 不同仍合法；
4. Market 与 optional Listing 的 Instrument 不同会拒绝；
5. 一个 Market 多个 MarketDataAccess；
6. provider symbol 更新不改变 InstrumentId/MarketId；
7. direct access 必须有 destination Market；
8. smart access 不要求 destination Market；
9. Execution access 缺失或歧义时拒绝下单；
10. 下单路径不解析 canonical ID；
11. fill 保留 actual execution Market，并可由 Market 派生实际 Exchange；
12. Reference generation/sequence 变化触发 scoped projection recovery。

静态检查至少包括：

```bash
rg -n "split|rsplit|strip_prefix|strip_suffix" \
  crates/business/execution crates/business/market
rg -n 'format!\("(market|instrument):' crates/business crates/kairos-integration
rg -n "source_symbol|provider_symbol" crates/business/market/service/src/domain
rg -n "listing_id.*exchange_id|market.*exchange_id" crates/business/reference
```

所有命中必须属于 ID constructor、wire/persistence adapter、fixture 或带删除条件的迁移路径。

完成各切片后运行：

```bash
cargo test -p kairos-reference-contract -p kairos-reference-service --all-targets
cargo test -p kairos-market-service -p kairos-execution-service --all-targets
cargo test -p kairos-account-service -p kairos-risk-service --all-targets
uv run pytest -q tests/test_reference_contract.py
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```

## 12. 最终完成定义

只有同时满足以下条件，本次模型调整才算完成：

1. Listing 与 Market 不再是强制一对一父子关系；
2. listing Exchange、trading Exchange、Broker、Provider 和 execution Market 角色不再混用；
3. Market 不拥有 provider/source symbol；
4. MarketDataAccess 与 ExecutionAccess 都有 typed、scoped、bounded Reference contract；
5. Execution 的 provider request 只来自 selected ExecutionAccess；
6. direct 与 smart route 都能表达并具有不同校验；
7. order/fill 可审计 access、binding、Reference watermark 和实际成交 Market；
8. Account/Risk/Strategy/Research 按各自业务语义使用 Asset/Instrument/Market/Access；
9. 旧 canonical identity 和历史数据通过显式映射迁移；
10. 同一股票多挂牌、同一挂牌多交易 Exchange 的端到端测试通过。
