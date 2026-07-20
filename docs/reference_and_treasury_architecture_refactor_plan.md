# Reference Data 与 Treasury 架构改造计划

## 1. 文档目的

本文给出 Kairos 项目从当前多资产交易框架演进为稳定、可扩展、可审计的交易系统框架的完整改造方案，集中解决两类问题：

1. 当前“标的”模型的抽象粒度不足：资产、经济产品、具体合约、交易所挂牌、供应商代码、定价基准和执行路径之间的边界尚未完全分离；
2. 当前“转账”模型的抽象粒度不足：只能较好表达账户间同步划转，无法完整覆盖加密资产链上转账、交易所内部划转、银行现金转账、在途资金、手续费、失败、退回和对账。

改造目标不是建设一个无限复杂的证券主数据平台，而是建立一套足以支撑研究、回测、模拟、实盘交易、资金调拨、风险穿透和历史重放的最小正确抽象。

本文是目标架构和实施计划。迁移期间应保持现有业务可运行，采用增量替换，不进行一次性大爆炸式重写。

## 当前实施状态（2026-07-17）

已经落地：

- V2 ReferenceCatalog、独立 Product/Series/Instrument/Listing/ProviderMapping/ExecutionRoute；
- Asset、Entity、Venue、Benchmark、NetworkAsset、SettlementRail 和 Location 定义；
- InstrumentReference、SettlementTerms、Deliverable 与 point-in-time 版本库；
- 旧版 Catalog 确定性迁移、V2 JSON 持久化、CLI migration 和 connector sync bridge；
- Execution、Pricing、Risk、Ledger、Portfolio、Lifecycle、Backtest、MarketData subscription、RuntimeRecovery 和主要 Strategy 的 V2 读取路径；
- Treasury Intent/Instruction/Operation、显式审批、状态机、SQLite 恢复、Provider 对账和 Ledger 在途投影；
- Binance 内部划转/链上提现 connector 与 provider-neutral Bank transfer connector；
- Research reference evidence hash 和 Massive curated V2 lineage。

当前全量验证为 434 tests passed、5 skipped，固定 runtime Golden audit hash 保持不变。

尚未完成：

- live CLI 的所有交易/行情入口完全切换为 V2 Catalog；
- 旧版 IBKR/Binance connector 内部参数从 `InstrumentDefinition` 改为解析后的轻量 contract；
- 旧 ResearchSnapshot builder 和少量 synthetic compatibility 分支移除；
- 旧版 `kairos.reference.ReferenceCatalog`、嵌套 `ListingDefinition` 和兼容 facade 的最终删除；
- 完成删除后的全量故障注入与最终逐项验收审计。

## 2. 当前系统基础与核心判断

当前系统已经具备三个正确且应继续保留的基础：

- `InstrumentId` 是行情、订单、持仓和具体金融合约的稳定内部主键；
- `AssetId` 是余额、费用、保证金、结算和 Ledger 记账的资产主键；
- Ledger 使用不可变双边分录记录已经确认的经济事实。

现有标的模型大致包含五层：

| 层级 | 当前对象 | 主要职责 |
|---|---|---|
| 资产 | `AssetId` | 表达现金、币、费用和结算资产 |
| 合约身份 | `InstrumentId` | 表达系统内部稳定的具体金融工具 |
| 产品定义 | `InstrumentDefinition + InstrumentContractSpec` | 表达产品类型和经济条款 |
| Venue 挂牌 | `ListingDefinition` | 表达 symbol、tick、lot 和最小交易规则 |
| 供应商映射 | `ExternalInstrumentMapping` | 将供应商外部代码映射为内部合约 |

这个结构可以支持当前股票、ETF、期权、加密现货、永续和部分期货业务，但存在以下结构性不足：

- `AssetId`、`InstrumentId` 和自由字符串形式的 index/settlement reference 混合承担 underlying 语义；
- 缺少经济产品家族与具体合约之间的中间层；
- Venue、数据 Provider、Broker 和执行 Route 在部分数据和接口中混用；
- `InstrumentDefinition.symbol`、`ListingDefinition.symbol` 和外部 mapping 存在语义重叠；
- Listing 生命周期嵌套在 InstrumentDefinition 生命周期中，无法独立版本化；
- `ProductType` 和 `InstrumentContractSpec` 可能构造非法组合；
- 结算条款不足以表达复杂现金结算、实物交割和调整合约；
- `TransferIntent` 只有来源账户、目标账户、资产和金额，无法表达异步资产移动。

## 3. 总体设计原则

### 3.1 身份与职责分离

目标模型遵循以下关系：

```text
Entity / Asset / Benchmark
           |
           v
    EconomicProduct
           |
           v
  TradableInstrument
           |
           v
     ListingDefinition
           |
           v
ProviderSymbol / ExecutionRoute
```

每类对象只回答一类问题：

- Asset：账本中移动和结算的是什么；
- Benchmark：定价、结算或风险引用的不可交易基准是什么；
- Product：具体合约属于哪个经济产品家族；
- Instrument：持仓、定价和交易的具体合约是什么；
- Listing：具体合约在哪里、按什么规则交易；
- Provider mapping：外部数据系统如何称呼该对象；
- Execution route：某账户通过哪个 Broker/Connector 交易该 Listing；
- Treasury operation：资产如何在 Location 之间移动；
- Ledger：哪些经济变化已经实际发生。

### 3.2 热路径保持简单

交易系统的热路径继续遵守：

```text
MarketEvent.instrument_id
Order.instrument_id
Position.instrument_id
Ledger cash entry.asset_id
Ledger position entry.instrument_id
```

产品、Listing、Provider mapping 和 Reference Graph 用于解析、规划、校验、定价和风险穿透，不应把复杂主数据对象复制进每个市场事件或 Ledger 分录。

### 3.3 时间有效性是一等语义

以下对象必须支持独立的 point-in-time 版本：

- Asset definition；
- Benchmark definition；
- Economic product；
- Instrument definition；
- Listing definition；
- Provider mapping；
- Instrument reference；
- Execution route；
- Network asset 和 transfer rail 配置。

所有正式解析必须显式传入 `as_of`，不得使用当前 symbol 猜测历史身份。

### 3.4 Intent、Instruction、Operation、Ledger 分离

无论交易还是转账，都采用四阶段语义：

```text
Intent：希望达成什么经济结果
Instruction：决定使用什么具体通道和参数执行
Operation：外部系统中实际执行到什么状态
Ledger：哪些余额或持仓变化已经确认
```

Intent 不能直接作为已完成事实入账；Operation 状态也不能替代 Ledger。

## 4. Reference Data 目标模型

### 4.1 身份类型

在 `kairos.reference.identity` 中增加：

```python
EntityId
BenchmarkId
ProductId
SeriesId
ListingId
ProviderId
BrokerId
InstitutionId
RouteId
CalendarId
NetworkId
NetworkAssetId
RailId
LocationId
```

继续保留：

```python
AssetId
InstrumentId
VenueId
AccountKey
```

所有 ID 都应是不可变值对象，负责最小规范化和非空校验，但不应通过字符串格式隐式承载完整业务语义。

### 4.2 AssetDefinition

`AssetId` 继续作为 Ledger 资产键，新增定义对象补充主数据：

```python
class AssetType(StrEnum):
    FIAT = "fiat"
    CRYPTO = "crypto"
    SECURITY = "security"
    FUND_SHARE = "fund_share"
    COMMODITY = "commodity"


@dataclass(frozen=True, slots=True)
class AssetDefinition:
    asset_id: AssetId
    asset_type: AssetType
    name: str
    issuer_id: EntityId | None
    decimals: int | None
    effective_from: datetime
    effective_to: datetime | None = None
```

不是所有 Instrument 都必须对应 Asset。现金结算指数期权本身是 Instrument，但不是可交割 Asset。

### 4.3 EntityDefinition

Entity 表达公司、发行人、指数管理人、交易场所、Broker、银行、托管机构和清算机构。它主要用于公司行为、发行人风险、托管风险和账户归属，不进入普通订单热路径。

```python
@dataclass(frozen=True, slots=True)
class EntityDefinition:
    entity_id: EntityId
    entity_type: EntityType
    legal_name: str
    country: str | None
    effective_from: datetime
    effective_to: datetime | None = None
```

### 4.4 BenchmarkDefinition

用 `BenchmarkId` 替换 `index_id: str` 和 `settlement_index: str`：

```python
@dataclass(frozen=True, slots=True)
class BenchmarkDefinition:
    benchmark_id: BenchmarkId
    benchmark_type: BenchmarkType
    name: str
    currency: AssetId
    administrator_id: EntityId | None
    calendar_id: CalendarId | None
    effective_from: datetime
    effective_to: datetime | None = None
```

Benchmark 可以表示指数、利率、FX fixing、mark/index price 和官方结算值。其观测数据使用 `BenchmarkObservation`，不强行伪装为可交易 Instrument 行情。

### 4.5 EconomicProduct

新增产品家族层：

```python
@dataclass(frozen=True, slots=True)
class EconomicProduct:
    product_id: ProductId
    product_type: ProductType
    name: str
    issuer_id: EntityId | None
    currency: AssetId | None
    effective_from: datetime
    effective_to: datetime | None = None
```

典型 Product：

- AAPL common stock；
- SPX index；
- SPXW options；
- CME ES futures；
- Binance BTCUSDT perpetual product。

Product 不直接下单和持仓，用于 universe、合约发现、风险聚合、option chain 和期货换月。

### 4.6 ContractSeries

新增可选的 Series 层，用于衍生品合约分组：

```python
@dataclass(frozen=True, slots=True)
class ContractSeries:
    series_id: SeriesId
    product_id: ProductId
    expiry: datetime | None
    trading_class: str | None
    settlement_terms_id: str | None
    effective_from: datetime
    effective_to: datetime | None = None
```

期权 Series 可以表达 root、trading class、expiry、行权和结算风格；期货 Series 可以表达合约月份、first notice、last trade 和交割规则。具体 strike/right 或具体交割月最终仍形成 Instrument。

### 4.7 InstrumentDefinition

重构后的 InstrumentDefinition 只描述一个具体可持仓、可定价的金融工具：

```python
@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    instrument_id: InstrumentId
    product_id: ProductId
    series_id: SeriesId | None
    instrument_type: InstrumentType
    contract_spec: ContractSpec
    lifecycle: InstrumentLifecycle
    effective_from: datetime
    effective_to: datetime | None = None
```

改造要求：

- 删除或弃用顶层 `symbol`；若仅用于 UI，改为明确不可交易的 `display_name`；
- 将 `listings` 从 InstrumentDefinition 中拆出；
- 将 `base_asset/quote_asset` 移到对应 ContractSpec；
- 将 schema version 放入持久化 envelope，不作为领域事实字段；
- `instrument_type` 与 `contract_spec` 必须强校验匹配；
- 所有金额、乘数、执行价、合约大小和时间关系必须验证。

### 4.8 InstrumentReference

统一当前 `underlying`、`underlying_asset`、`reference_equity`、`index_id` 和 `settlement_index`：

```python
class ReferenceRole(StrEnum):
    ECONOMIC_UNDERLYING = "economic_underlying"
    PRICING_UNDERLYING = "pricing_underlying"
    SETTLEMENT_BENCHMARK = "settlement_benchmark"
    DELIVERABLE = "deliverable"
    REFERENCE_INSTRUMENT = "reference_instrument"
    HEDGE_PROXY = "hedge_proxy"


@dataclass(frozen=True, slots=True)
class ReferenceTarget:
    asset_id: AssetId | None = None
    instrument_id: InstrumentId | None = None
    benchmark_id: BenchmarkId | None = None
    product_id: ProductId | None = None


@dataclass(frozen=True, slots=True)
class InstrumentReference:
    source_instrument_id: InstrumentId
    role: ReferenceRole
    target: ReferenceTarget
    weight: Decimal
    effective_from: datetime
    effective_to: datetime | None = None
```

`ReferenceTarget` 必须保证恰好设置一个目标字段。关系图负责定价输入解析、风险穿透、交割和 hedge proxy 发现。

### 4.9 SettlementTerms 与 Deliverable

将结算从简单枚举升级为独立条款：

```python
@dataclass(frozen=True, slots=True)
class Deliverable:
    asset_id: AssetId
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class SettlementTerms:
    method: SettlementMethod
    settlement_asset: AssetId | None
    benchmark_id: BenchmarkId | None
    determination_at: datetime | None
    settlement_at: datetime | None
    session: SettlementSession
    deliverables: tuple[Deliverable, ...]
```

它必须覆盖现金结算、普通实物交割、调整期权的股票加现金篮子、inverse 合约以及多资产费用/结算。

### 4.10 ListingDefinition

Listing 从 InstrumentDefinition 中独立：

```python
@dataclass(frozen=True, slots=True)
class ListingDefinition:
    listing_id: ListingId
    instrument_id: InstrumentId
    venue_id: VenueId
    trading_symbol: str
    venue_instrument_id: str | None
    trading_currency: AssetId
    trading_rules: TradingRules
    effective_from: datetime
    effective_to: datetime | None = None
```

Venue 只表示实际市场或交易设施。Massive 等数据供应商、IBKR 等 Broker 不得作为 Venue 写入 Catalog。

TradingRules 第一阶段包含：

```python
price_increment
quantity_increment
minimum_quantity
maximum_quantity
minimum_notional
```

后续可增加分段 tick table、价格带、订单数量阶梯和 session-specific 规则。

### 4.11 ProviderSymbolMapping

外部映射不再只能指向 Instrument：

```python
@dataclass(frozen=True, slots=True)
class ProviderSymbolMapping:
    provider_id: ProviderId
    namespace: str
    external_id: str
    target_type: MappingTargetType
    target_id: str
    publisher_id: str | None
    effective_from: datetime
    effective_to: datetime | None = None
```

允许映射到：

- Instrument；
- Listing；
- Benchmark；
- Product；
- Synthetic series。

### 4.12 ExecutionRoute

Broker、Venue 和 Connector 分离：

```python
@dataclass(frozen=True, slots=True)
class ExecutionRoute:
    route_id: RouteId
    broker_id: BrokerId
    account_key: AccountKey
    listing_id: ListingId
    broker_contract_id: str | None
    capabilities: ExecutionCapabilities
    effective_from: datetime
    effective_to: datetime | None = None
```

执行解析链路为：

```text
InstrumentId
  -> active ListingDefinition
  -> permitted ExecutionRoute
  -> broker connector
```

`AccountKey` 应从 `venue_id` 迁移到 `institution_id/broker_id`，因为账户属于 Broker、Exchange、Bank 或 Custodian，而不是属于证券挂牌市场。

## 5. Treasury 与 Asset Movement 目标模型

### 5.1 业务范围

Treasury domain 负责所有非成交型资产移动：

- 同账户 book reclassification；
- 交易所 Spot、Margin、Futures、Options 钱包内部划转；
- 主账户和子账户之间划转；
- Exchange/Broker/Custodian 之间调拨；
- 加密资产链上提现和充值；
- 自托管钱包之间转账；
- 银行 ACH、wire、SWIFT、SEPA 等现金转账；
- 外部入金、出金、退回和冲正；
- 未来的证券 FOP/DVP/ACATS 过户。

Treasury 不负责市场成交。涉及换汇或资产转换时，应拆为 Trade/Conversion 加 Transfer，Transfer 本身原则上不改变经济资产类型。

### 5.2 Institution、Account 与 Location

引入 AssetLocation：

```python
class LocationType(StrEnum):
    BROKER_ACCOUNT = "broker_account"
    EXCHANGE_SPOT = "exchange_spot"
    EXCHANGE_MARGIN = "exchange_margin"
    EXCHANGE_DERIVATIVES = "exchange_derivatives"
    BANK_ACCOUNT = "bank_account"
    CUSTODIAL_WALLET = "custodial_wallet"
    ONCHAIN_WALLET = "onchain_wallet"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class AssetLocation:
    location_id: LocationId
    location_type: LocationType
    institution_id: InstitutionId | None
    account_id: str | None
    subaccount: str | None
    network_id: NetworkId | None
    address: str | None
```

Ledger 余额最终按 `account/location + book + asset` 查询。第一阶段可让现有 AccountKey 继续承担受控 Location，随后再完成显式 LocationId 迁移。

### 5.3 NetworkAssetDefinition

Crypto 链上执行不能只依赖 `AssetId("USDT")`：

```python
@dataclass(frozen=True, slots=True)
class NetworkAssetDefinition:
    network_asset_id: NetworkAssetId
    asset_id: AssetId
    network_id: NetworkId
    contract_address: str | None
    decimals: int
    deposit_enabled: bool
    withdrawal_enabled: bool
    effective_from: datetime
    effective_to: datetime | None = None
```

`AssetId(USDT)` 表示经济资产，`NetworkAssetId(ETHEREUM:USDT)` 表示可通过特定网络发送的具体 token。跨链桥应拆成多个 Transfer/Conversion Operation，不应伪装为同一次普通转账。

### 5.4 SettlementRail

现金转账使用类型化 Rail：

```python
@dataclass(frozen=True, slots=True)
class SettlementRail:
    rail_id: RailId
    rail_type: RailType
    supported_assets: tuple[AssetId, ...]
    calendar_id: CalendarId | None
    cutoff_rule: str | None
    effective_from: datetime
    effective_to: datetime | None = None
```

Rail 包括 internal、ACH、Fedwire、SWIFT、SEPA、区块链 Network 等。

### 5.5 AssetMovementIntent

将当前 TransferIntent 收敛为内部同步划转的兼容类型，新建通用经济意图：

```python
@dataclass(frozen=True, slots=True)
class AssetMovementIntent:
    intent_id: UUID
    owner_id: str
    source_location_id: LocationId
    destination: TransferDestination
    asset_id: AssetId
    requested_amount: Decimal
    amount_mode: AmountMode
    preferred_rail_id: RailId | None
    fee_policy: FeePolicy
    urgency: TransferUrgency
    reason: str
```

`AmountMode` 至少支持：

- `GROSS`：源端总共扣除指定金额；
- `NET`：目标必须收到指定净额；
- `ALL`：转移可用余额，但必须经过限额和 fee 计算。

Destination 使用 tagged union：

- InternalAccountDestination；
- CryptoAddressDestination；
- BankAccountDestination；
- ExternalCustodianDestination。

### 5.6 TransferInstruction

Planner 将 Intent 解析为可执行 Instruction：

```text
InternalTransferInstruction
CryptoTransferInstruction
BankTransferInstruction
CustodianTransferInstruction
SecurityTransferInstruction（后续）
```

Instruction 必须包含已解析的 source/destination、network 或 rail、精度、手续费策略、限额版本和幂等键。

### 5.7 TransferOperation

Operation 表达外部执行事实：

```python
@dataclass(frozen=True, slots=True)
class TransferOperation:
    transfer_id: str
    intent_id: UUID
    instruction_id: str
    status: TransferStatus
    requested_amount: Decimal
    debited_amount: Decimal | None
    credited_amount: Decimal | None
    fee_amount: Decimal | None
    fee_asset: AssetId | None
    provider_reference: str | None
    transaction_hash: str | None
    created_at: datetime
    submitted_at: datetime | None
    completed_at: datetime | None
```

Operation 应采用事件驱动状态转换并持久化每次状态变化，不能仅保存最后状态。

### 5.8 Transfer 状态机

通用状态：

```text
CREATED
  -> VALIDATED
  -> APPROVED
  -> SUBMITTED
  -> SOURCE_DEBITED
  -> IN_TRANSIT
  -> DESTINATION_CREDITED
  -> COMPLETED
```

异常状态：

```text
REJECTED
FAILED
CANCELLED
EXPIRED
RETURNED
REVERSED
MANUAL_REVIEW
```

Crypto 扩展状态：

```text
BROADCAST -> CONFIRMING -> CONFIRMED
```

Cash 扩展状态：

```text
PROCESSING -> SETTLED -> RETURNED
```

状态转换必须具备幂等、单调性和 provider event 去重；发生冲正时记录新的补偿事件，不覆盖历史。

### 5.9 Ledger 在途记账

内部即时划转可以继续使用单笔平衡事务：

```text
Source.CASH       -100 USDT
Destination.CASH  +100 USDT
```

跨机构、链上或银行转账分阶段入账：

源端确认扣款：

```text
Source.CASH        -100 USDT
IN_TRANSIT          +100 USDT
```

目标端确认到账：

```text
IN_TRANSIT          -100 USDT
Destination.CASH    +100 USDT
```

手续费单独入账：

```text
Source.CASH          -1 USDT
FEE_EXPENSE          +1 USDT
```

建议增加 LedgerBook：

```text
TRANSFER_PENDING
IN_TRANSIT
TRANSFER_RECEIVABLE
TRANSFER_PAYABLE
```

第一阶段只实现 `IN_TRANSIT` 和现有 `LOCKED` 即可。Ledger 只记录外部已确认事实；尚未提交的 Intent 和未确认到账的期望值不能直接记为 destination cash。

### 5.10 Deposit 与 Withdrawal 语义

当前使用 `EXTERNAL` 平衡入金和出金，适合测试和 opening balance，但生产语义需要细分：

- `OPENING_BALANCE`：系统接入前已存在余额；
- `CAPITAL_CONTRIBUTION`：资本注入；
- `CAPITAL_DISTRIBUTION`：资本返还；
- `TRANSFER_IN/OUT`：与具体外部 Location 的资产移动；
- `SUBSCRIPTION/REDEMPTION`：客户资金申购赎回；
- `RETURN/REVERSAL`：银行退回或链上业务补偿。

系统控制的两端必须记录为两个真实 Location 之间的 Transfer，不能使用匿名 EXTERNAL 隐藏所有权和在途状态。

## 6. 新 Catalog 与服务边界

将当前 ReferenceCatalog 改造为 ReferenceCatalog 门面：

```python
class ReferenceCatalog:
    assets: AssetRepository
    entities: EntityRepository
    benchmarks: BenchmarkRepository
    products: ProductRepository
    series: SeriesRepository
    instruments: InstrumentRepository
    listings: ListingRepository
    mappings: ProviderMappingRepository
    references: InstrumentReferenceRepository
    routes: ExecutionRouteRepository
    locations: LocationRepository
    network_assets: NetworkAssetRepository
    rails: SettlementRailRepository
```

业务查询接口：

```python
catalog.instrument(instrument_id, as_of)
catalog.product(product_id, as_of)
catalog.active_listings(instrument_id, as_of)
catalog.resolve_provider_symbol(provider_key, as_of)
catalog.resolve_execution_route(account, instrument_id, as_of)
catalog.references(instrument_id, role, as_of)
catalog.contracts(product_id, filters, as_of)
catalog.network_asset(asset_id, network_id, as_of)
catalog.transfer_rails(source, destination, asset_id, as_of)
```

Catalog 负责：

- point-in-time 唯一性；
- 时间区间不重叠；
- 引用完整性；
- Provider namespace 唯一性；
- Instrument、Listing 和 Route 生命周期一致性；
- Network asset 与 network/contract 一致性；
- 版本 lineage 和 snapshot hash。

## 7. 下游系统改造

### 7.1 Market Data

Canonical 可交易行情继续引用 `InstrumentId`，并增加解析证据：

```text
provider_id
namespace
external_id
publisher_id
mapping_version
```

指数、fixing、mark 和 settlement value 使用 BenchmarkObservation。正式 Dataset 发布前，所有外部代码必须 point-in-time 解析成功；失败事件进入 quarantine。

### 7.2 Pricing

新增 `PricingContextResolver`：

```text
InstrumentId
 -> ContractSpec
 -> InstrumentReference
 -> Benchmark/Instrument observations
 -> PricingContext
```

Pricing model 只消费标准 PricingContext，不再直接读取多种 InstrumentContractSpec 并使用 `getattr` 猜测 underlying、multiplier 或 settlement asset。

### 7.3 Risk

Risk 通过 Reference Graph 穿透：

```text
position instrument
 -> product
 -> economic underlying
 -> issuer
 -> currency
 -> settlement asset
 -> venue
 -> broker/custodian
```

增加 underlying、issuer、currency、settlement asset、product family、venue、broker 和 custodian 维度。

### 7.4 Execution

订单保持引用 InstrumentId。提交前生成不可变解析快照：

```python
ResolvedExecutionContract:
    instrument_id
    listing_id
    venue_id
    route_id
    trading_symbol
    broker_contract_id
    trading_rules_version
    resolved_at
```

订单发出后不得因 Catalog 更新改变 symbol、tick、lot 或 route。

### 7.5 Lifecycle

Settlement、exercise、assignment 和 corporate action 统一消费显式 SettlementTerms、Deliverable 和 Reference Graph。调整期权必须通过新的 Definition/Deliverable 版本表达，不能只修改 symbol 或 multiplier。

### 7.6 Treasury

新增：

```text
TreasuryPlanner
TransferPolicyEngine
TransferCoordinator
TransferOperationStore
TransferReconciliationService
```

Coordinator 负责 readiness、限额、审批、白名单、幂等提交、状态推进和恢复；Reconciliation 负责将 Exchange、链上和银行事件与内部 Operation、Ledger 对齐。

### 7.7 Ledger 与 Portfolio

保留 `AssetId + InstrumentId` 主边界。新增 Location/InTransit 维度和转账 entry type，但不让 Ledger 承担 Provider symbol、网络路由或状态机解析。

Portfolio 需要区分：

- total balance；
- available；
- locked；
- in transit；
- receivable/payable；
- borrowed/collateral。

在途资产可以计入 NAV，但必须按策略配置 haircut，且不能计入可交易余额。

## 8. 建议项目目录

```text
kairos/
  reference/
    identity.py
    asset.py
    entity.py
    benchmark.py
    product.py
    series.py
    instrument.py
    listing.py
    relationship.py
    settlement.py
    provider_mapping.py
    route.py
    location.py
    network.py
    rail.py
    catalog.py
    validation.py
    repository.py

  treasury/
    intent.py
    destination.py
    instruction.py
    operation.py
    event.py
    state_machine.py
    policy.py
    planner.py
    coordinator.py
    reconciliation.py
    ledger_posting.py

  connectors/
    transfer/
      transfer_gateway.py
      internal.py
      binance.py
      onchain.py
      bank.py
      simulation.py

  market_data/
  pricing/
  execution/
  lifecycle/
  ledger/
  risk/
  strategy/
```

实际迁移可以先保留当前包路径，通过兼容 facade 引用新实现，最后再移动模块，避免文件移动和业务语义修改同时发生。

## 9. 实施阶段

### Phase 0：基线与架构防线

目标：冻结当前行为，确保后续重构可验证。

工作项：

1. 为 ReferenceCatalog、provider mapping、定价、执行解析、Ledger transfer 补充 characterization tests；
2. 建立 Catalog fixture，覆盖股票、指数、上市期权、现货、永续、期货和加密期权；
3. 建立转账 fixture，覆盖内部划转、链上提现、链上充值、银行转账、手续费、失败和退回；
4. 建立当前 Catalog 和 Ledger snapshot/hash 基线；
5. 禁止新代码继续从 InstrumentId 字符串解析 Venue symbol。

完成标准：现有研究、回测、模拟和实盘 preflight 测试保持绿色，并有可比较的行为基线。

### Phase 1：身份、命名和强约束

目标：先消除概念混用，不改变主要数据流。

工作项：

1. 增加 ProviderId、BrokerId、InstitutionId、BenchmarkId、ProductId、ListingId、LocationId、NetworkId 和 RailId；
2. 明确 VenueId 只表示交易场所；迁移 Massive/IBKR 等错误 Venue 数据；
3. 弃用 InstrumentDefinition.symbol；
4. 增加 ProductType/Spec 匹配校验和完整 invariants；
5. AccountKey 增加 institution/broker 语义兼容层；
6. 增加 ReferenceCatalog facade，暂时委托现有 ReferenceCatalog。

完成标准：所有对象身份可分类；正式路径中 Provider、Broker、Venue 不再互相替代。

### Phase 2：Listing 和 Provider Mapping 独立化

目标：拆开 Instrument、挂牌和外部代码生命周期。

工作项：

1. 新建 ListingRepository；
2. 将 InstrumentDefinition.listings 迁移为独立 ListingDefinition；
3. 新建可指向多种目标的 ProviderSymbolMapping；
4. Catalog 增加 point-in-time resolve；
5. Market/reference connectors 改为写新 mapping；
6. Execution 增加 ResolvedExecutionContract；
7. 保留旧 `catalog.get/resolve/listing` 兼容接口。

完成标准：单个 Listing 的 symbol/tick/lot 变化不要求复制完整 InstrumentDefinition；历史解析结果稳定。

### Phase 3：Product、Series、Reference Graph 和结算统一

目标：解决 underlying 和衍生品条款不一致。

工作项：

1. 新建 EconomicProduct 和 ProductRepository；
2. 为期权、期货引入 ContractSeries；
3. 新建 InstrumentReferenceRepository；
4. 将 underlying、underlying_asset、reference_equity、index_id、settlement_index 迁移为类型化关系；
5. 新建 SettlementTerms 和 Deliverable；
6. 改造 PricingContextResolver；
7. 改造 Risk exposure graph；
8. 改造 lifecycle settlement/exercise/assignment。

完成标准：所有衍生品都能明确回答经济标的、定价标的、结算基准和交割资产；下游不再使用自由字符串或 `getattr` 猜测这些语义。

### Phase 4：Treasury 核心和内部划转迁移

目标：建立统一 Asset Movement 框架，先覆盖低风险内部划转。

工作项：

1. 新建 AssetLocation、AssetMovementIntent、TransferInstruction、TransferOperation；
2. 实现状态机和 Operation event store；
3. 将当前 TransferIntent 定义为 InternalTransferIntent 兼容别名；
4. 实现 InternalTransferGateway；
5. TreasuryCoordinator 接入 readiness、限额和幂等；
6. Ledger 增加 LOCKED/IN_TRANSIT 处理；
7. 迁移 Spot/Futures、主/子账户划转。

完成标准：内部转账经过 Intent、Instruction、Operation、Ledger 四层，重复提交不会重复划款或重复入账。

### Phase 5：Crypto Network Transfer

目标：支持生产级加密资产充值和提现生命周期。

工作项：

1. 新建 NetworkDefinition 和 NetworkAssetDefinition；
2. 实现地址、memo/tag、精度、最小提现和手续费校验；
3. 实现 CryptoTransferGateway 接口和 BinanceTransferGateway；
4. 实现 BROADCAST/CONFIRMING/CONFIRMED 状态；
5. 接入链上 tx hash、确认数和目标 Exchange deposit observation；
6. 实现 gross/net amount 和 fee asset；
7. 实现地址白名单、审批和 withdrawal kill switch；
8. 实现跨系统对账和长时间 pending 告警。

完成标准：源扣款、链上在途、手续费和目标到账可以独立审计；重启和重复 webhook 不改变最终结果。

### Phase 6：Cash Transfer

目标：支持银行和 Broker 现金调拨。

工作项：

1. 新建 SettlementRail 和 BankAccountDestination；
2. 实现 cutoff、calendar、beneficiary、currency 和限额校验；
3. 实现 BankTransferGateway 协议；
4. 支持 PROCESSING/SETTLED/RETURNED/REVERSED；
5. 实现银行手续费、中间行费用和实际到账差异；
6. 接入 statement/reconciliation；
7. 将 opening balance、capital flow 和普通 transfer 分开。

完成标准：现金在途、到账、退回和冲正能够用不可变 Operation events 与 Ledger 补偿分录重建。

### Phase 7：执行路由、产品 Universe 和高级能力

目标：完成框架收敛并释放新模型能力。

工作项：

1. ExecutionRoute 全量接管 Instrument -> Connector 解析；
2. 策略 universe 支持 Product/Series 筛选；
3. OptionChain 改为 Catalog 查询视图；
4. 增加期货连续合约、roll policy 和 synthetic series；
5. 增加 issuer、custodian、broker 和 in-transit risk；
6. 删除旧 Catalog、旧 TransferIntent 和旧嵌套 Listing 兼容层。

完成标准：旧字段和兼容路径无生产调用；系统只保留一套 reference 和 treasury 事实模型。

## 10. 数据迁移策略

采用“双读、单写新模型”：

```text
旧 Catalog JSON
    -> migration command
    -> 新 Reference repositories
    -> compatibility facade
    -> 现有调用方
```

迁移要求：

1. 每个旧 InstrumentDefinition 生成 Product、Instrument 和 Listing；
2. 顶层 symbol 仅作为迁移线索，不能自动成为权威 Venue symbol；
3. Massive 等 provider listing 进入 quarantine，必须解析真实 Venue 或标记为 provider-only mapping；
4. underlying/index 字符串无法可靠解析时进入 quarantine，不允许静默猜测；
5. 每次迁移产生 source hash、target hash、迁移版本和诊断报告；
6. 迁移必须幂等；
7. 新旧 Catalog 对同一历史时间查询结果应有可解释差异报告。

Treasury 历史迁移：

- 当前同步 `TRANSFER` 可以迁移为已完成 InternalTransferOperation；
- 当前 DEPOSIT/WITHDRAWAL 如果缺少对手 Location，标记为 旧版 external movement；
- 不根据 Ledger 分录猜测链上 tx hash、network 或银行 rail；缺失事实保持 unknown；
- opening balance 单独标记，不伪装为真实入金。

## 11. 兼容与发布策略

### 11.1 兼容 Facade

迁移期间保留：

```python
catalog.get(instrument_id, at)
catalog.resolve(venue_id, external_id, at)
definition.listing(venue_id, at)
ledger_service.transfer(...)
```

内部逐步代理到新 repositories 和 TreasuryLedgerPostingService，同时记录 deprecated metrics。调用量归零后删除。

### 11.2 Feature Flags

建议使用：

```text
reference_catalog_v2_read
reference_catalog_v2_write
execution_route_v2
treasury_internal_transfer_v2
treasury_crypto_transfer
treasury_cash_transfer
ledger_in_transit
```

所有涉及外部提现或银行转账的开关默认关闭，必须经过 simulation、paper 和小额 canary。

### 11.3 Fail Closed

以下情况必须拒绝正式执行：

- Instrument、Listing 或 Route point-in-time 解析不唯一；
- Provider mapping 缺失或冲突；
- network、token contract 或 destination address 不匹配；
- fee/amount 语义不明确；
- 转账操作缺少幂等键；
- Ledger/reconciliation 状态不一致；
- 外部 provider 状态倒退或出现未知终态；
- 提现白名单、审批或限额未满足。

## 12. 测试计划

### 12.1 Reference 单元测试

- 所有版本区间和重叠检查；
- Product/Instrument/Spec 类型匹配；
- Listing 生命周期约束；
- Provider namespace 冲突；
- ReferenceTarget 单一目标约束；
- SettlementTerms 与 Deliverable 合法性；
- historical symbol rename；
- delisting/relisting；
- option adjustment；
- future expiry/roll；
- Benchmark observation 解析。

### 12.2 Treasury 状态机测试

- 每个合法状态迁移；
- 非法跳转拒绝；
- 重复 provider event 幂等；
- source debit 后失败；
- destination credit 延迟；
- fee 与到账额不同；
- bank return；
- reversal 使用补偿事件；
- 重启恢复；
- out-of-order event；
- manual review 恢复。

### 12.3 Ledger 属性测试

- 每笔 transaction 按 Asset 平衡；
- 不产生负的可用余额，除非显式借贷；
- pending/in-transit 不能计入 available；
- internal transfer 不改变 owner NAV；
- fee 正确降低 NAV；
- controlled locations 之间转移不产生虚假 PnL；
- external capital flow 与投资 PnL 分离；
- 重放得到相同余额和状态。

### 12.4 集成测试

- Provider symbol -> Instrument -> Listing -> Route -> Order；
- Option position -> Reference Graph -> Pricing/Risk/Settlement；
- Binance Spot -> Futures 内部转账；
- Binance withdrawal -> chain confirmation -> destination deposit；
- bank wire -> processing -> settlement；
- bank return -> compensating ledger entries；
- operation store、provider reconciliation 与 Ledger 三方一致。

### 12.5 故障注入

- connector timeout；
- submit 成功但客户端未收到响应；
- webhook 重复或乱序；
- provider 查询短暂返回 unknown；
- blockchain reorg；
- bank return 延迟数日；
- Catalog 在 Operation 期间更新；
- 系统在每个状态转换点重启。

## 13. 可观测性和审计

每次解析和操作必须带：

```text
correlation_id
intent_id
instruction_id
operation_id
reference snapshot/version
provider reference
ledger transaction ids
actor/approver
timestamps
```

核心指标：

- unresolved provider mapping；
- ambiguous listing/route；
- transfer pending duration；
- in-transit amount by asset/location；
- source debited but destination uncredited；
- reconciliation mismatch；
- duplicate provider event；
- withdrawal failure/return rate；
- manual review queue；
- compatibility facade call count。

审计必须能够从 Intent 一路追踪到 Instruction、Operation events、Provider reference 和 Ledger transactions，也能从任一 Ledger transfer 分录反向找到其 Operation。

## 14. 安全和治理要求

Crypto 和 cash 转账属于高风险外部状态变更，应比普通订单使用更严格的权限边界：

- 默认禁止新 destination；
- destination 白名单和冷却期；
- 金额分级审批；
- 日累计和单笔限额；
- 多签或双人复核接口；
- withdrawal 独立 kill switch；
- secret/signing 与策略进程隔离；
- connector 不向策略暴露 API secret；
- 所有人工覆盖必须记录 actor、reason 和 before/after；
- dry-run、simulation、testnet 和小额 canary 先行；
- 外部地址和银行敏感信息在日志中脱敏。

## 15. 优先级与工作量建议

优先级分为三档：

### P0：正确性基础

- Provider/Broker/Venue 分离；
- Listing 独立；
- InstrumentContractSpec 强校验；
- Reference Graph；
- SettlementTerms；
- Treasury Operation 状态机；
- Ledger IN_TRANSIT；
- 幂等和 reconciliation。

### P1：生产可用

- NetworkAsset；
- Crypto transfer connector；
- destination whitelist/approval；
- ExecutionRoute；
- PricingContextResolver；
- Risk 穿透；
- bank transfer rail。

### P2：高级主数据能力

- Entity/issuer 全量治理；
- ContractSeries；
- continuous futures；
- synthetic instrument；
- 证券过户；
- 多法人、多 owner 总账。

建议不要为了 P2 阻塞 P0/P1。对当前项目，最有价值的交付顺序是：Listing 独立化、Reference Graph、Treasury 状态机、Crypto 内部划转与链上转账。

## 16. 最终验收标准

### 16.1 Reference Data

系统必须无歧义回答：

1. 一个 ID 表示 Asset、Benchmark、Product、Series、Instrument 还是 Listing；
2. 具体 Instrument 的经济条款、生命周期和结算条款；
3. 它依赖哪个经济标的、定价标的和结算基准；
4. 指定时间在哪些 Venue 可交易；
5. 某 Provider 外部代码在事件发生时对应哪个对象；
6. 某 Account 可通过哪个 Route 交易；
7. 下单时使用的 symbol、tick、lot 和 route 版本；
8. 到期时交付什么资产或现金；
9. 持仓如何穿透到资产、发行人、币种、Venue、Broker 和 Custodian；
10. 历史重放是否得到相同解析结果。

### 16.2 Treasury

系统必须无歧义回答：

1. 转移的是哪个经济 Asset；
2. 源和目标分别是什么 Location；
3. 使用哪个 Network 或 cash Rail；
4. requested、debited、credited 和 fee amount 分别是多少；
5. 当前处于哪个状态，状态如何到达；
6. 哪个外部 reference 或 tx hash 对应本次操作；
7. 资产是 available、locked、in-transit 还是 settled；
8. 是否经过限额、白名单和审批；
9. 是否已与 provider、链上或银行 statement 对账；
10. 重复事件、重启、失败、退回和冲正是否保持幂等且可审计。

### 16.3 架构完成定义

以下条件全部满足后，改造才算完成：

- Instrument、Listing、Provider、Broker 和 Route 不再混用；
- 所有 underlying 和 settlement reference 都是类型化、可版本化关系；
- 订单和市场事件继续使用稳定 InstrumentId；
- Ledger 余额继续使用稳定 AssetId；
- 转账通过 Intent、Instruction、Operation、Ledger 完整闭环；
- 异步转账不会提前增加目标可用余额；
- 正式执行和 Dataset 不允许 unresolved reference；
- 历史数据、订单和转账均能 point-in-time 重放；
- 旧 Catalog 和旧 Transfer 兼容路径已无生产调用并被删除。

## 17. 总结

本次改造包含两条相互连接但职责独立的主线：

```text
Reference Data：定义交易什么、依赖什么、在哪里交易、如何解析和路由
Treasury：定义什么资产从哪里经什么通道移动到哪里，以及进行到什么状态
Ledger：记录交易和资产移动已经产生的可审计经济事实
```

最终保持三个稳定核心：

```text
InstrumentId = 可交易具体合约的稳定主键
AssetId      = 可记账和可移动资产的稳定主键
Ledger       = 已确认经济事实的唯一账务来源
```

其余 Product、Series、Listing、ProviderMapping、Reference、Route、Location、Network、Rail 和 TransferOperation 都围绕这三个核心提供解析、治理、执行和审计能力。这样既能覆盖股票、ETF、期权、期货、永续和加密产品，也能正确覆盖 crypto 与 cash 的多种资产移动方式，而不会让单个 Instrument 或 Transfer 对象承担不属于它的全部复杂度。
