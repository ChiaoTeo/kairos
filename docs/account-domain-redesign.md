# ExternalAccount 领域模型重设计

## 1. 设计决定

ExternalAccount 不再被视为一个包含所有账户相关能力的万能对象，但必须保留为“一个外部账号”的领域聚合根。

本项目的 ExternalAccount 领域拆成以下边界：

```text
ExternalAccountIdentity      稳定地标识哪个机构的哪个外部账号
ExternalAccount              一个账号及其多个账户分区的聚合根
AccountSegment         账号中的哪个资金/交易分区
AccountRuntimeContext       在哪个运行环境中使用某个分区
AccountState         当前账户事实和可用状态
AccountPolicy        账户允许如何交易
AccountMarketProfile 某账户交易某市场时的具体规则
AccountSession       当前连接和认证生命周期
Portfolio            跨账户、跨市场的估值和风险视图
```

这些对象不能被压缩为一个万能对象，也不能让一个 `book` 字段同时承担身份、分区、路由和查询键的职责。
`ExternalAccount` 负责表达“一个账号拥有多个分区”；`AccountDirectory` 只负责查找多个账号，不能替代 `ExternalAccount` 聚合。

## 2. 现实交易环境中的账户层次

现实中的“账号/账户”至少包含四个维度：

```text
机构/交易场所
    └── 外部账号主体（ExternalAccount）
          ├── 账户模式（AccountModel）
          ├── 交易/资金分区（AccountSegment[]）
          ├── 凭据与会话（Credentials / AccountSession）
          └── 运行环境（AccountRuntimeContext）
```

例如：

```text
Binance / main / spot / live
Binance / main / usd_m_futures / live
IBKR    / paper-account / equity / paper
```

因此：

- `broker` 或 `venue` 不是账户本身；
- `account_id` 不是账户分区；
- `spot`、`usd_m_futures`、`equity` 是产品族或账户分区，不能都直接当作账号模式；
- `paper`、`live` 是运行环境，不是账户身份；
- 一个 `ExternalAccount` 可以拥有多个 `AccountSegment`；
- 同一个账户分区可能在不同运行环境中被模拟或连接。

环境不是账号聚合边界。`live` 和 `paper` 可以是同一个 `ExternalAccountIdentity` 的两个运行上下文，
但它们必须拥有独立的运行状态和连接生命周期。

## 3. 核心身份模型

### 3.1 ExternalAccountIdentity

`ExternalAccountIdentity` 只回答“这是哪个外部账号”：

```python
from kairospy.domain.reference import ExternalAccountId, BrokerId

@dataclass(frozen=True, slots=True)
class ExternalAccountIdentity:
    broker: BrokerId
    account_id: ExternalAccountId
```

它不包含：

- `Environment`；
- spot、futures 等分区；
- 连接状态；
- 当前余额；
- 交易权限。

### 3.2 ExternalAccount 聚合

`ExternalAccount` 是账号聚合根，负责维护同一外部账号下的分区集合和账号级状态。
它不是余额、仓位或连接对象的容器；余额和仓位仍由对应的 `AccountSegment` 状态拥有。

```python
@dataclass(frozen=True, slots=True)
class ExternalAccount:
    identity: ExternalAccountIdentity
    segments: tuple[AccountSegment, ...]
    configured_model: AccountModel | None = None
    observed_model: AccountModel | None = None
    status: AccountStatus = AccountStatus.UNKNOWN
```

`ExternalAccount` 的不变量包括：

- 所有 segment 必须属于同一个 `ExternalAccountIdentity`；
- 同一个 segment key 只能出现一次；
- segment 的环境不属于聚合身份，环境由 `AccountRuntimeContext` 表达；
- `observed_model` 未确认或与配置不一致时，不得假设账号可交易；
- 账号模式切换期间，相关 segment 必须进入 `RECONCILING` 或 `TYPE_MISMATCH`。

### 3.3 AccountModel 与 AccountSegment

账户模型与交易产品是两个不同维度。

账户模型描述资金、保证金和仓位如何被交易场所组织，例如：

```python
class AccountModel(StrEnum):
    NO_MARGIN = "no_margin"
    MARGIN = "margin"
    CONTRACT = "contract"
    CONTRACT_UNIFIED = "contract_unified"
    UNIFIED = "unified"
    PORTFOLIO_MARGIN = "portfolio_margin"
```

```python
class ProductFamily(StrEnum):
    SPOT = "spot"
    USD_M_FUTURES = "usd_m_futures"
    COIN_M_FUTURES = "coin_m_futures"
    OPTIONS = "options"
    EQUITY = "equity"
    FUNDING = "funding"
    EARN = "earn"
```

`AccountStatus` 至少需要表达 `UNKNOWN`、`READY`、`TYPE_MISMATCH`、`RECONCILING`、
`SUSPENDED` 和 `UNAVAILABLE`。它描述账号当前是否可以安全使用，不等同于连接是否在线。

它不表示具体产品。BTC/USDT、ETH/USDT、股票、期权合约等产品由 Market/Instrument 和
`ProductFamily` 模型表达；账户模型只决定这些产品的资产、保证金和持仓如何结算。

因此必须分开：

```text
AccountModel       账号采用的资金/风险组织方式
ProductFamily      spot / futures / options / equity 等产品族
AccountSegment       一个可独立查询、交易或结算的分区
MarginMode         cross / isolated 保证金模式
```

`AccountSegment` 只回答“这个账户中的哪一块”：

```python
@dataclass(frozen=True, slots=True)
class AccountSegment:
    identity: ExternalAccountIdentity
    segment_id: AccountSegmentId
    model: AccountModel
    product_family: ProductFamily | None = None
    qualifier: str = ""
```

`model` 表示账号或分区采用的资金和风险模型，`product_family` 表示该分区主要服务的产品族，二者不能互换。
一个账号可以存在多个 segment，例如传统模式下分别存在 spot、USD-M futures 和 Coin-M futures segment；
统一模式下则可能只有一个 unified segment，但该 segment 的能力覆盖多个产品族。

`segment_id` 必须是稳定的路由标识。不能把可变的 `observed_model` 直接当作历史状态的身份键；
否则从 Spot 切换到 Unified 会让同一个分区的订单、仓位和事件失去连续性。若交易所确实把切换
视为新分区，应保留旧 segment 的历史记录，并通过 `AccountModelTransition` 建立新旧 segment 的关联。

最终模型不保留 `kind`/`book` 兼容别名；配置和 CLI 使用 `segments`、`segment` 与 `product_family`，领域代码使用强类型的 `AccountModel` 和 `ProductFamily`。

账户模型的典型语义包括：

```python
AccountModel.NO_MARGIN
AccountModel.MARGIN
AccountModel.CONTRACT
AccountModel.CONTRACT_UNIFIED
AccountModel.UNIFIED
AccountModel.PORTFOLIO_MARGIN
```

`SPOT`、`USD_M_FUTURES`、`COIN_M_FUTURES` 和 `OPTIONS` 不再作为同一层的
`AccountModel` 枚举值，而作为 `ProductFamily` 或具体 segment 的分类信息。

#### 资产与抵押物不是账户模型

`AccountModel` 不表示结算币种，也不表示账户只有一个现金余额。Binance Spot 可以同时持有
USDT、USDC、USD1、BTC 等多种资产，因此模拟账户必须以资产数量初始化：

```toml
[initial_balances]
USDT = "10000"
USDC = "5000"
BTC = "0.25"
```

真实交易所账户由 API key 绑定，不能通过本地配置初始化资产；上述配置只适用于 paper、backtest
和 simulation。保证金账户还需要独立的 `CollateralBalance` 集合，记录每种抵押资产的钱包数量、
可用数量、估值和折扣率。多币种保证金不能用单一余额数量和余额资产字段表示。

这是一次有意的破坏性迁移：旧的单一现金余额配置/API 不再保留兼容入口，旧模拟账户配置必须改写为
`initial_balances`。

```text
AssetBalance       账户实际持有的资产
CollateralBalance  可用于保证金的资产及其风险折算
AccountValuation   按指定估值资产汇总后的权益
```

账户域中的资产符号使用强类型 `AssetCode`，交易所返回的字符串只在 adapter 边界转换为该值对象。

交易所特殊的账户分区可以通过 `qualifier` 表达，但不能通过任意对象隐式表达。

### 3.4 账户类型是可变化的状态

`ExternalAccountIdentity` 是稳定身份，但 `AccountModel` 可能随交易所账户配置或迁移操作发生变化。不能把账户类型只当作启动配置，也不能在一个账户下同时假设所有互斥模型都有效。

典型的互斥关系包括：

```text
UNIFIED
    与传统的 SPOT / USD_M_FUTURES / COIN_M_FUTURES 分区组合互斥

CONTRACT_UNIFIED
    与独立的 USD_M_FUTURES / COIN_M_FUTURES / OPTIONS 分区组合互斥
```

| AccountModel | 典型 segment/product family | 资产/仓位组织 | 主要能力 |
| --- | --- | --- | --- |
| `NO_MARGIN` | Spot | 多资产余额和订单冻结 | 资产查询、现货下单、划转 |
| `MARGIN` | Spot / Margin | 余额、借贷负债、保证金 | 资产、负债、保证金、下单 |
| `CONTRACT` | USD-M / Coin-M Futures | 结算资产、合约仓位、保证金 | 资产、仓位、杠杆、下单 |
| `CONTRACT_UNIFIED` | 多种合约/期权产品 | 合约统一保证金和风险单元 | 资产、仓位、风险、下单、模式查询 |
| `UNIFIED` | Spot + Futures + Options | 统一资产、保证金和风险单元 | 按产品族路由的资产、仓位和下单 |
| `PORTFOLIO_MARGIN` | 多产品族 | 跨产品组合保证金 | 资产、仓位、组合风险和下单 |

表中的“典型”不是交易所兼容性保证。实际能力必须由 adapter 观察并返回，不能仅凭枚举值推断。

因此账户需要区分：

```text
ExternalAccountIdentity          稳定身份
ExternalAccount                  账号聚合根
ConfiguredAccountModel   期望或配置的类型
ObservedAccountModel     从交易所确认的当前类型
AccountModelTransition   类型切换事实或命令
```

当配置类型和观察类型不一致时，账户不能假设自己可交易，应进入 `type_mismatch` 或 `reconciling` 状态。

账户类型切换属于 ExternalAccount application use case，而不是简单修改字段。
切换不会修改 `ExternalAccountIdentity`，也不能静默地把旧 segment 改名；它必须经过状态迁移、重新校准和能力重建：

```text
SwitchAccountModelCommand
        ↓
检查交易所支持和切换前置条件
        ↓
执行切换或请求人工确认
        ↓
重新读取账户能力和账户状态
        ↓
发布 AccountModelChangedEvent
```

切换前必须明确处理：未完成订单、未平仓仓位、借贷负债、保证金、资金划转和 private stream。
交易所不支持自动切换时，`AccountModelSwitcher` 只能返回需要人工操作的结果，不能伪造切换成功。

### 3.5 AccountRuntimeContext

`AccountRuntimeContext` 表示一次运行时使用上下文：

```python
@dataclass(frozen=True, slots=True)
class AccountRuntimeContext:
    segment: AccountSegment
    environment: Environment
```

它是 application 和 Actor 进行路由、查询和生命周期管理的主要上下文，但不是账户状态本身。

### 3.6 AccountDirectory 与配置

`AccountDirectory` 是 application/composition 层的目录，用于根据用户别名、账号 ID 或索引
找到 `ExternalAccount`。它不拥有余额、仓位、模式切换或连接状态。

配置中的 `accounts` 表示多个 `ExternalAccount`；每个账号内部用 `segments` 表达多个账户分区。
`environment` 可以出现在运行上下文配置中，但不能导致同一个 `ExternalAccountIdentity` 被复制成两个领域账号。

账户配置记录和 TOML 加载器属于 Account application 的配置适配层；Workspace 只负责项目路径、
凭证、锁和生命周期资源，不拥有 `AccountRecord` 或 `AccountStore`。这样配置文件的读取不会把
Workspace 误当成账户聚合的拥有者。

```text
AccountDirectory
├── binance-main : ExternalAccount
│   ├── spot segment
│   └── usd_m_futures segment
└── okx-main : ExternalAccount
    └── unified segment
```

## 4. AccountState 与外部快照

### 4.1 AccountState

`AccountState` 是 ExternalAccount Actor 持有的当前领域状态，包含：

- balances；
- positions；
- liabilities；
- margins；
- open orders；
- observed time；
- freshness 和 completeness；
- 当前状态来源。

它不包含：

- vendor raw payload；
- SDK 对象；
- 连接对象；
- 账户目录；
- 费率规则；
- 展示 schema。

资产和仓位是两类不同事实：

```text
AccountState
├── assets / balances
│     现金、代币、可用、冻结、负债和结算货币
└── positions
      合约仓位、方向、数量、开仓价、PnL、保证金和清算信息
```

现货账户通常只有资产余额；合约账户同时拥有资产余额和仓位。不能把合约仓位折叠成资产余额，也不能把资产余额当作仓位。

### 4.2 ContractPosition 与保证金模式

合约仓位的保证金模式独立于账户模型和交易产品：

```python
class MarginMode(StrEnum):
    CROSS = "cross"
    ISOLATED = "isolated"
```

- `CROSS`：仓位共享账户或统一账户的保证金池；
- `ISOLATED`：仓位拥有独立保证金和清算边界。

`PositionSnapshot.margin_mode` 表达该仓位当前的保证金模式；账户级 Policy 可以表达默认模式和是否允许切换。`MarginState.scope` 则表达数据是账户级、instrument 级还是 position 级，两者不能混为一谈。

### 4.3 杠杆账户与杠杆状态

杠杆不是单一的账户类型，而是三个层次的组合：

```text
AccountModel
    是否支持借贷、保证金或杠杆

LeveragePolicy
    账户/市场允许的默认杠杆、最大杠杆和调整规则

    PositionState
    当前仓位实际使用的杠杆、保证金模式和抵押资产
```

典型情况：

```text
ProductFamily.SPOT + AccountModel.NO_MARGIN
    不支持杠杆，或只允许现金交易

AccountModel.MARGIN
    允许借贷和杠杆，通常可以选择 CROSS / ISOLATED

ProductFamily.USD_M_FUTURES / ProductFamily.COIN_M_FUTURES
    通常支持逐仓或全仓，并按合约或仓位配置杠杆

UNIFIED / CONTRACT_UNIFIED
    杠杆、保证金和抵押资产可能按产品、仓位或账户风险单元计算
```

因此 `can_borrow` 只能表达粗粒度能力，不能代替杠杆模型。`LeveragePolicy` 至少应表达：

- 是否允许杠杆或借贷；
- 默认杠杆和最大杠杆；
- 允许的调整范围；
- 调整杠杆所需的保证金模式、仓位状态和权限。

`PositionState` 或 `PositionSnapshot` 还应记录交易所确认的实际 `leverage`。
下单前由 Risk/ExternalAccount application 根据账号模式、产品规则、保证金模式、杠杆策略和当前仓位共同校验。

### 4.4 AccountSnapshot

`AccountSnapshot` 表示一次已经被 adapter 规范化的外部观察事实。

```text
Vendor JSON / SDK
        ↓
ExternalAccount adapter
        ↓
Normalized AccountSnapshot
        ↓
ExternalAccount Actor
        ↓
AccountState
```

Domain `AccountSnapshot` 不包含 `raw: Mapping[str, object]`。如果诊断需要保留原始数据，
应放在 infrastructure 的 observation record 中，并通过关联 ID 追踪。`AccountSnapshot` 必须明确
属于哪个 `AccountSegment`，不能用一个未带 segment 的总余额快照代替多个分区快照。

### 4.5 AccountEvent

余额变化、成交、手续费、资金费、入金、出金、结算和人工调整应作为事实事件：

```python
class AccountEventKind(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FILL = "fill"
    FEE = "fee"
    FUNDING = "funding"
    SETTLEMENT = "settlement"
    ADJUSTMENT = "adjustment"
```

`AccountLedger` 负责事件顺序、幂等和重放；`AccountState` 是事件、外部快照和未完成订单约束形成的当前投影。

## 5. 账户类型与账户规则

账户模型决定余额和风险如何计算，不应该只由字符串字段表达。它与交易产品独立。

```text
Spot/Cash ExternalAccount
    全额结算，锁定未完成订单对应的资金

Contract/Margin ExternalAccount
    追踪初始保证金、维持保证金、借贷和清算风险

如果未来支持 Betting，应建立独立的 `BettingAccountModel` 或扩展模型，追踪 stake 和 payout，
不能复用普通杠杆模型。
```

以下概念已经从 `AccountMarketProfile` 的混合字段中拆成独立的强类型 Policy；`AccountPolicySet`
可以将它们组合到一个账户分区/市场 profile：

- `AccountPolicy`：账户级交易权限和能力；
- `AccountModel`：资产、仓位、结算和保证金的组织方式；
- `MarginPolicy`：初始/维持保证金以及保证金范围；
- `LeveragePolicy`：默认杠杆、最大杠杆、可调范围和调整前置条件；
- `PositionPolicy`：净持仓、双向持仓、杠杆和 reduce-only 规则；
- `FeePolicy`：账户费率、市场费率和支付货币规则；
- `SettlementPolicy`：结算货币和资产结算方式。

当前实现同时保留 `AccountMarketProfile` 的交易所观察字段，因为这些字段是外部事实；Policy
表达的是本地可执行规则，二者不能互相替代。

### 5.1 不同账户类型使用不同能力接口

不同账户类型的差异不能只体现在 `if account_type == ...`，也不能要求所有账户实现一套空方法或返回 `None` 的万能接口。
应把“能力描述”和“能力执行接口”分开：

```text
AccountCapabilities
    当前账号/分区支持哪些能力以及限制是什么

AccountAssetReader / AccountPositionReader / AccountOrderExecutor ...
    消费者真正需要调用的最小业务接口
```

能力属于 `AccountSegment`，账号级能力只能表达跨 segment 的操作，例如账号模式查询或账号级资金划转。
应把能力拆成消费者需要的最小 Protocol：

```text
AccountAssetReader
    读取资产余额、可用余额、冻结余额和负债

AccountPositionReader
    读取合约/期权仓位、保证金、PnL 和清算信息

AccountOrderExecutor
    下单、撤单、查询订单状态

AccountTransferService
    资产划转、入金、出金或账户间转移

AccountModelSwitcher
    查询和切换账户类型
```

一个账户分区通过能力组合表达，而不是通过继承树表达：

```text
传统 Spot segment
    AccountAssetReader
    AccountOrderExecutor
    AccountTransferService

USD-M Futures segment
    AccountAssetReader
    AccountPositionReader
    AccountOrderExecutor
    AccountTransferService

Unified segment
    AccountAssetReader
    AccountPositionReader
    AccountOrderExecutor
    AccountTransferService
    AccountModelSwitcher  # 账号级能力，不属于每个 segment 的普通下单接口
```

缺失能力必须通过明确的 capability result 或不可用错误表达，不能以 `object`、空实现或隐式降级掩盖。

下单和查看资产也应分成不同 application 用例：

- 资产查询只依赖 `AccountAssetReader`；
- 仓位查询只依赖 `AccountPositionReader`；
- 下单只依赖 `AccountOrderExecutor` 和交易前账户状态；
- 账户类型切换只依赖 `AccountModelSwitcher`；
- ExternalAccount Actor 负责将这些能力更新到同一账户状态，但不把所有能力都暴露给每个调用方。

## 6. ExternalAccount 与 Portfolio 的边界

`ExternalAccount` 负责一个外部账号及其多个 `AccountSegment` 的身份、模式、能力和生命周期；
每个 `AccountSegment` 负责自己的一组余额、仓位、订单和交易约束。
`ExternalAccount` 可以汇总账号级状态，但不负责跨账号估值。

Portfolio 负责：

- 多个 ExternalAccount 的 AccountSegment 聚合；
- 估值货币转换；
- equity；
- realized/unrealized PnL；
- net exposure；
- 跨账户风险；
- 缺少价格或汇率时的不可用状态。

只有满足货币和估值条件时，Portfolio 才允许聚合不同账号的金额。ExternalAccount 不应自行承担跨账号估值，
也不应把不同 segment 的金额未经估值直接相加。

## 7. 运行时与基础设施边界

ExternalAccount Actor 是单一运行时状态拥有者：

```text
REST snapshot ─────┐
private stream ────┼──→ ExternalAccount Actor ──→ AccountState
execution events ─┘             │
                                 ├──→ ExternalAccount events
                                 └──→ Application queries
```

以下对象不应进入 Domain ExternalAccount：

- Binance、IBKR、CCXT 等 SDK client；
- WebSocket connection；
- credentials；
- private stream subscription；
- retry、reconnect 和 polling 状态；
- vendor raw payload。

这些能力由 ExternalAccount application protocol 声明，由 infrastructure adapter 和 composition 注入。

## 8. Application API 目标

application 公开 API 应围绕业务用例设计：

```python
QueryAccountState
QueryAccount
QueryAccountScopes
RefreshAccountState
ReconcileAccountState
SubmitAccountAction
CancelAccountAction
QueryAccountCapabilities
QueryAccountMarketProfile
SwitchAccountModel
```

请求和结果使用明确的数据类，不暴露 service、SDK、record 或 `object`：

```python
@dataclass(frozen=True, slots=True)
class QueryAccountStateRequest:
    context: AccountRuntimeContext
    freshness: FreshnessPolicy = FreshnessPolicy()


@dataclass(frozen=True, slots=True)
class QueryAccountStateResult:
    state: AccountState
```

查询整个账号时接受 `ExternalAccountIdentity` 或 `ExternalAccount` 级请求；查询和交易单个分区时接受
`AccountSegment`；需要环境、连接和运行生命周期时才接受 `AccountRuntimeContext`。
不再使用 `book: object` 作为多义入口。

## 9. Account application 与 CLI 边界

账户 CLI 不再依赖一个聚合所有能力的 `AccountCommandApplication`。账户用例按业务动作拆分为明确的 application API：

```text
AccountAdministrationApplication  本地绑定的查看、修改、删除、诊断和 schema
AccountConnectionApplication       credential 连接、远程账号发现、账户绑定
AccountLiveQueryApplication        balance、positions、open orders、snapshot
AccountSimulationApplication       paper/backtest/simulation 账户和多资产初始化
AccountLeaseApplication            账户标识到 Workspace lease 的适配
AccountModelApplication             账户类型/模型切换
```

composition 只负责装配这些应用，CLI 只负责参数转换和结果渲染。Workspace 拥有 lease 和工作区生命周期，Account application 只负责把账户身份转换为 Workspace 的 lease subject；远程读取通过 `AccountCommandResources` 注入的强类型 port 完成。

完成迁移后，旧的 `account/application/commands.py` 入口被删除，不保留兼容 facade。

## 10. 迁移原则

### 第一阶段：建立新模型

- 新增 `ExternalAccount`、`ExternalAccountIdentity`、`AccountSegment` 和明确的 `AccountRuntimeContext` 语义；
- 明确 `AccountModel`、`ProductFamily`、`MarginMode` 和 `LeveragePolicy` 的边界；
- 将同一账号的多个 segment 组织到同一个 ExternalAccount 聚合，不按 environment 复制账号；
- 直接删除旧类型和旧字段，不建立长期兼容入口；
- 新 application API 只使用新模型；
- 不让新代码继续依赖 `book: object`。

CLI 也遵循同一边界：live 账户通过 `account connect --credential` 连接并发现，`--alias` 只是本地 binding 名称；paper 账户使用
`account simulate --balance ASSET=QUANTITY`（可重复），余额查询使用可重复的
`account query balance ACCOUNT --segment SEGMENT`。CLI 只把文本参数转换为 application request，
不自行拼装交易所参数或访问账户服务实现。

多个 API key 访问同一个远端账号时，`account connect` 会按已发现的远端身份复用同一个本地 binding，并将
`readonly`、`trade` 等凭据作为访问凭据附加到该 binding；凭据不会产生新的 ExternalAccount 聚合。

### 第二阶段：迁移 ExternalAccount State

- 移除 Domain snapshot 的 `raw`；
- 按 AccountSegment 隔离余额、仓位、订单和保证金状态；
- 增加账号级配置模式、观察模式和类型不匹配状态；
- 明确 `AccountSnapshot` 和 `AccountState` 的差异；
- 将 freshness、completeness 和 source 形成明确值对象；
- 让 ExternalAccount Actor 成为唯一状态更新入口。

### 第三阶段：拆分 Policy/Profile/Session（已完成）

- 将费率、保证金、权限和结算规则拆出；
- 将账号级切换和分区级交易能力拆开；
- 将账户登录、连接和 private stream 移到 application/infrastructure；
- 删除 Actor 和 assembly 中的 `object` 依赖。

### 第四阶段：建立 Portfolio（已落地基础能力）

- 将跨账户聚合和估值移出 ExternalAccount；
- 只在货币和价格条件明确时进行聚合；
- 区分 native values、converted values 和 unavailable values。

当前已支持余额、负债、持仓敞口和未实现盈亏的跨账户估值；后续只允许增加新的明确估值事实，
不能回退到跨账户直接相加。

### 第五阶段：删除旧抽象（已完成）

- 删除旧的万能账户分区路由对象及其职责；
- 删除长期兼容的 `book` 字符串入口；
- 删除 `AccountBinding` 承担领域聚合职责的做法，让它只保留配置/目录适配职责；
- 删除 account/application/services 之间重复的 ExternalAccount façade；
- 增加架构测试禁止新代码使用宽泛 ExternalAccount API；当前测试覆盖旧抽象文件、旧 CLI 入口、旧请求字段和 Actor/assembly 依赖边界。

## 11. 设计验收标准

- 账户身份、分区、环境、状态、策略、会话和组合可以独立解释；
- 一个 `ExternalAccount` 能明确拥有多个 `AccountSegment`，且同一账号的 live/paper context 不会被误识别为两个账号；
- 一个 `AccountSegment` 能唯一定位一个可交易的账户分区；
- 账户模式切换不会改变 `ExternalAccountIdentity`，并能表达切换中、类型不匹配和人工操作状态；
- `AccountModel`、`ProductFamily`、`MarginMode` 和杠杆状态没有重复语义；
- ExternalAccount 状态不含 vendor raw payload；
- ExternalAccount 不负责跨账户估值；
- Cash、Margin、Betting 不共享隐含的错误计算规则；
- REST、stream、execution 和 simulation 都能转换为同一套 ExternalAccount 事实模型；
- application API 不暴露 `object` 作为主要账户业务类型；
- ExternalAccount Actor 是状态唯一拥有者；
- 连接、凭证和重连状态不进入 Domain；
- CLI、strategy、monitor 都只能通过 application API 读取 ExternalAccount。

## 12. 参考实现方向

本设计吸收了成熟交易系统的共同做法：

- NautilusTrader：区分 Cash/Margin/Betting，独立建模余额、保证金、账户状态和 Portfolio；
- Freqtrade：将 Wallet 与 PositionWallet 分离；
- Hummingbot：让 Connector 管理外部余额、in-flight orders 和实时更新；
- vn.py：将 AccountData 与 PositionData 分离，并使用 gateway/account identity。

本项目不直接复制任何一个项目的类，而是采用更适合自身 Actor、Usecase 和 Domain 边界的组合模型。
