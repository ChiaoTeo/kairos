# Usecase 与 Integration 架构设计

本文记录 Usecase 与 Integration 之间的协作边界，以及 Account、Market、Execution、Reference 等业务领域如何使用这套边界。目标不是把不同平台抽象成完全相同的系统，而是让真实差异落在正确的位置。

## 1. 核心判断

交易平台之间的差异分为两类：

1. **协议差异**：请求字段、认证方式、REST/WebSocket、symbol、错误码、分页和限流。这些由 Integration Gateway 消化。
2. **业务架构差异**：账户是否统一、余额和保证金是否共享、仓位如何计算、风险单元是什么、订单产生什么业务状态。这些不能被 Integration 假装抹平，必须进入账户和 Usecase 的业务模型。

因此，系统不追求一个“大一统账户”或一个“所有平台都一样”的万能连接。

```text
业务真实差异       → Account / Domain / Usecase
外部协议和实现差异 → Integration / Gateway
```

## 2. 交易产品、资产和来源是不同维度

以下概念必须保留，而且不能互相替代：

```text
ProductFamily：交易机制和结算方式
AssetType：     资产是什么
InstrumentType：具体金融工具是什么
BrokerId：      通过谁交易
ProviderId：    从谁获取数据
ExchangeId：    在哪里撮合或交易
```

### ProductFamily

`ProductFamily` 是共享的交易产品和账户产品类型，只保留一份定义。它描述交易机制和结算方式，不描述底层资产类别，也不描述账户服务。示例：

```text
SPOT
USD_M_FUTURES
COIN_M_FUTURES
OPTIONS
```

Account、Market、Execution 和 Integration 都可以使用这份类型，但不能在不同模块各自定义一份同名枚举。Margin 是账户模型或保证金策略，Earn 是账户服务能力，都不属于 ProductFamily。

### AssetType 和 InstrumentType

股票不属于 `ProductFamily`。股票是资产或金融工具类型：

```text
AssetType：
  CRYPTO、EQUITY、FIAT、FUND、INDEX

InstrumentType：
  EQUITY、SPOT、PERPETUAL、FUTURE、OPTION
```

例如：

```text
BTC 现货：
  AssetType       = CRYPTO
  InstrumentType  = SPOT
  ProductFamily   = SPOT

BTC U 本位永续：
  AssetType       = CRYPTO
  InstrumentType  = PERPETUAL
  ProductFamily   = USD_M_FUTURES

AAPL 股票：
  AssetType       = EQUITY
  InstrumentType  = EQUITY
  ProductFamily   = SPOT
```

### BrokerId

`BrokerId` 表示交易执行和外部账户归属方。例如，某个美股账户通过 Binance 执行交易时，Binance 可以是该账户的 broker。

`BrokerId` 不表示：

- 行情数据来源；
- 具体 SDK；
- 具体 REST/WebSocket 实现；
- 某一个 Gateway 类。

### ProviderId

`ProviderId` 表示外部数据或参考信息来源。一次业务运行可以同时使用多个 provider。例如，交易美股时可以同时启动：

```text
Broker：  Binance
Provider：Binance
Provider：Massive
```

因此，Provider 不能被当成 Broker 的别名。Broker 和 Provider 恰好是同一个平台时，只是两个身份值碰巧相同。

### ExchangeId

`ExchangeId` 表示订单撮合或市场挂牌所在的交易所。当前系统中原来使用的 `venue` 字段如果语义是交易所，底层值必须使用 `ExchangeId`；不再新增平行的 `VenueId`。`BrokerId` 和 `ExchangeId` 仍然可以相同，但表示不同身份：

```text
BrokerId   = ibkr
ExchangeId = nasdaq
ProviderId = massive
```

`Venue` 只作为历史兼容字段名逐步迁移，不作为新的领域身份类型。

## 3. Account 是一个重要业务例子

账户模型以 `ExternalAccount` 和 `AccountSegment` 为主轴。它是本架构中最能说明“业务架构差异不能被 Integration 抹平”的例子：

```text
ExternalAccount
  └── AccountSegment
        ├── AccountModel
        ├── ProductFamily
        ├── AccountPolicySet
        └── AccountCapability
```

`ExternalAccount` 是外部账户容器，`AccountSegment` 是具有独立余额、仓位、保证金、权限或风险语义的账户域。

例如，一个 Binance 外部账户可以表达为：

```text
binance/main
├── spot
├── margin
├── usd_m_futures
├── coin_m_futures
├── options
└── portfolio_margin
```

这些 segment 不应被合并成一个“统一账户连接”，因为它们可能拥有不同的：

- 资产余额；
- 结算方式；
- 保证金范围；
- 仓位模式；
- 杠杆策略；
- 风险计算方式；
- 权限和 user stream；
- 订单和成交语义。

股票账户如果采用现金/现货交易机制，应表达为 `AssetType.EQUITY + ProductFamily.SPOT`，而不是新增 `ProductFamily.EQUITY`。

项目现有的 `ExternalAccountIdentity`、`AccountSegment`、`AccountModel`、`AccountPolicySet` 和 `AccountCapability` 应继续收敛成这套唯一的账户业务模型，而不是再增加一套平行的 `UnifiedAccount`、`BrokerAccount` 或 `ProviderAccount` 模型。

## 4. 各业务 Usecase 与 Integration

本设计不在 Usecase 和 Integration 之间额外增加一层 Port。各业务 Usecase 可以直接持有对应的 Integration Connection，并使用 Integration 定义的稳定模型：

```text
Usecase
  ↓ 直接持有
Integration Connection
  ↓
Integration Gateway
  ↓
Vendor API
```

例如，Execution Usecase 可以直接持有交易连接：

```python
ExecutionOrderService(
    connection=binance_usd_m_futures_connection,
)
```

Market Usecase 也可以同时持有多个数据连接：

```python
MarketUsecase(
    connections=(binance_market_connection, massive_market_connection),
)
```

同一个市场的多源订阅必须显式携带 `ProviderId`，并且 Provider 必须参与订阅身份和连接选择：

```python
MarketDataSubscriptionSpec(
    market=market,
    selectors=(Quote,),
    provider=ProviderId("massive"),
)
```

因此同一市场可以同时存在 Massive 和 Binance 两个独立订阅，而不会互相覆盖。

Reference Usecase 则可以使用不同 Provider 的参考数据连接。它们共享 Integration 的连接和模型边界，但各自仍然拥有自己的业务规则、状态和用例流程。

这里的 Connection 可以是运行时对象，并且应该明确知道自己服务哪个账户段和产品能力：

```python
connection.identity.account_segment
connection.identity.product_family
connection.capabilities
```

Usecase 可以直接使用 `ConnectionOrderSubmissionRequest`、`ConnectionOrderSubmissionResult` 等 Integration 模型，但这些模型必须是项目自己的稳定模型，不能暴露 vendor SDK 或原始 payload。

## 5. Usecase 和 Integration 各自控制什么

### Usecase 控制

Usecase 控制业务意图、选择和流程：

- 对哪个账户段执行动作；
- 使用哪个 broker 账户；
- 使用哪个 provider；
- 请求哪个产品类型；
- 请求什么能力；
- 是否同时订阅多个 provider；
- 多源数据如何比较、校验或组合；
- 如何执行风险检查和订单状态迁移。

例如：

```python
market_usecase.subscribe(
    market=market,
    providers=(
        ProviderId("massive"),
        ProviderId("binance"),
    ),
)
```

Usecase 可以根据账户业务模型做判断：

```python
if account.model is AccountModel.PORTFOLIO_MARGIN:
    ...
```

但不应把业务规则写成供应商名称分支：

```python
if provider == "binance":
    ...
```

### Integration 控制

Integration 控制外部获取和技术实现：

- 选择具体 Gateway；
- 选择 REST 或 WebSocket；
- 处理认证、连接和重连；
- 转换 symbol 和请求字段；
- 处理分页、限流、重试和错误码；
- 解析 vendor payload；
- 将原始数据转换为项目定义的 Integration 模型。

Integration 必须知道自己如何获取数据，不能只是一个被动的“统一接口”。但它不应改变账户模型或隐藏账户段之间的业务差异。

Integration 的路由需要同时区分产品和资产，例如：

```text
Binance + CRYPTO + SPOT + MARKET_DATA
Binance + CRYPTO + PERPETUAL + USD_M_FUTURES
Massive + EQUITY + STOCK + MARKET_DATA
IBKR + EQUITY + STOCK + ORDER_ENTRY
```

## 6. Gateway 的位置

Gateway 已经承担外部适配和实现选择，因此不再需要公开的 `AdapterId`。

推荐的路由信息是：

```text
BrokerId / ProviderId
  + ProductFamily
  + Capability
  + Transport
  + Access
  + Credential
        ↓
Integration Gateway Registry / Factory
        ↓
具体 Gateway
```

例如：

```text
Binance + EQUITY + STOCK + ORDER_ENTRY + REQUEST_API
  → Binance Equity Gateway

Massive + EQUITY + STOCK + QUOTE_STREAM + WEBSOCKET
  → Massive Market Gateway
```

Gateway 是 Integration 内部的实现，不需要通过 `AdapterId` 暴露到 Usecase 或 Domain。只有在未来确实需要让用户显式选择同一能力下的多个实现时，才考虑把实现选择作为 Integration 内部配置，而不是业务模型。

## 7. 依赖方向

```text
CLI / System / Actor
        ↓
Usecase Application
        ↓ 直接使用
Integration Application / Connection
        ↓
Integration Gateway
        ↓
Vendor SDK / HTTP / WebSocket
```

```text
Domain
  不持有 Connection、Gateway、SDK 或网络资源
```

System 或 Composition 负责创建和绑定 Connection。Actor 负责运行时状态、数据循环和连接生命周期。Usecase 使用 Connection 完成当前业务动作。Domain 只负责业务规则和状态迁移。

## 8. 当前项目的收敛方向

后续重构应优先遵循以下顺序：

1. 保留一份共享的 `ProductFamily`。
2. 明确 `BrokerId` 表示交易执行和账户归属。
3. 明确 `ProviderId` 表示数据或参考信息来源，并支持多个 provider 并行运行。
4. 以 `AccountSegment + AccountModel + AccountPolicySet` 作为账户业务主模型，不让 Account 细节扩散成全局抽象。
5. 允许 Market、Execution、Reference 等 Usecase 直接持有各自所需的 Integration Connection。
6. 删除或避免在 Usecase 和 Integration 之间重复定义 Port。
7. 删除 `AdapterId` 这类已经被 Gateway 吸收的技术身份。
8. 将 `Mapping[str, object]` 形式的泛化参数逐步替换为明确的业务请求类型。
9. 将 vendor 参数、symbol 转换和原始错误处理下沉到 Gateway。
10. 按业务目标、账户段、Provider 和能力绑定 Connection，而不是按 provider 名称在 Usecase 中分支。

## 9. 示例

一个系统可以同时存在以下运行关系：

```text
交易执行：
  AssetType      = EQUITY
  InstrumentType = EQUITY
  ProductFamily  = SPOT
  BrokerId       = binance
  AccountSegment = binance/main/spot
  Connection    = Binance Equity Gateway

行情来源：
  AssetType     = EQUITY
  ProductFamily = SPOT
  ProviderId    = massive
  Connection    = Massive Market Gateway

行情来源：
  AssetType     = EQUITY
  ProductFamily = SPOT
  ProviderId    = binance
  Connection    = Binance Market Gateway
```

Usecase 决定是否同时使用两路行情，以及如何将它们用于策略、校验或展示；每个 Gateway 决定各自如何从外部系统获取数据。

## 10. 各领域的最终分工

```text
Market Usecase
  选择市场、数据类型、Provider 和多源组合方式

Execution Usecase
  选择账户段，执行风险、订单和成交业务流程

Reference Usecase
  选择参考数据来源，维护领域目录和生命周期事实

Integration
  为这些 Usecase 提供连接、数据获取和外部协议实现
```

## 11. 最终原则

```text
ProductFamily： 交易机制和结算方式
AssetType：      资产类别
InstrumentType： 金融工具形状
BrokerId：       通过谁交易
ProviderId：     从谁获取数据
ExchangeId：     在哪里撮合或交易
AccountSegment： 哪个独立账户/风险域
Usecase：        业务意图、选择、组合和状态流程
Connection：     对某个账户段或数据源提供运行时访问
Gateway：        外部协议和获取实现
```

系统不追求消灭真实差异，而是保证：

> 业务架构差异留在业务模型中，外部协议差异留在 Integration Gateway 中，Usecase 直接控制业务选择并使用 Connection 完成用例。
