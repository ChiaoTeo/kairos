# 代码质量现状与完成情况

更新时间：2026-08-10

命名约束：领域和代码中统一使用 `Exchange`；项目中不允许出现旧的交易场所术语。

本文总结当前项目在架构、领域建模、协议、测试和性能准备方面的状态。领域类型化与质量门禁已完成；性能 benchmark 属于下一阶段的优化基线，不改变当前 domain 改造的完成状态。

## 结论

项目已经通过质量门禁，模块边界也基本稳定，可以开始建立性能基线和分析热点。核心领域值对象迁移已经完成；剩余裸 `String`、`i64` 和 `u64` 仅位于明确的 contract/wire、CLI 输入和持久化兼容边界。

因此当前状态是：

- 可以开始性能测量和局部优化；
- 不应继续大规模引入新的通用抽象；
- 应收尾 policy 配置和 wire adapter 的显式转换，随后再把性能优化建立在稳定的语义 API 上。

## 已完成的改造

### 模块结构和依赖方向

主要业务模块已经遵循 `bin -> composition -> application -> services`，`domain` 保持基础设施无关。

已经落实的规则包括：

- `application` 是跨模块业务入口；
- 具体 provider、store、publisher 和模式选择放在 `composition`；
- 可变业务状态由单一 Actor 持有；
- 生产代码没有跨模块导入其他模块的 `services` 私有实现；
- Market 的具体 feed 实现已经从公开 composition API 中隐藏，外部只拿到 `Box<dyn MarketFeed>`；
- Execution simulator 已收回到 composition 出口，不再从 crate 根部直接暴露 service 实现。

权威规则见 [module-boundaries.md](module-boundaries.md)。

### 质量门禁

本轮改造后的稳定版 `cargo check --workspace --all-targets --offline`、
`cargo clippy --workspace --all-targets --all-features --offline -- -D warnings`、
`cargo fmt --all -- --check`、`git diff --check` 和领域架构门禁均已通过。
Execution、Market、Account 的定向 Rust 测试也已通过；Integration 最近一次为 60 个单元测试
和 17 个账户集成测试全部通过。Python 测试最近一次为 `139 passed, 3 skipped`。workspace 全量 Rust 测试
现已完成并返回成功。

Rust CI 已包含 fmt、clippy、workspace tests 和 diff 检查。

### 协议和生成代码

已经修复过 schema 与 Rust/Python 生成代码漂移、生成模块索引缺失、Reference contract 查询模块缺失等问题。FlatBuffers 生成脚本现在会对生成的 Rust 代码执行 `rustfmt`。

生成代码必须始终由脚本产生，不应直接手工修改 generated 文件。

### 已存在的领域类型

Account 已经建立了一套非空 ID 类型，见 [identity.rs](../crates/business/account/service/src/domain/identity.rs)：

- `AccountId`；
- `SegmentKey`；
- `AssetId`；
- `InstrumentId`；
- `ExternalOrderId`；
- `FillId`。

Risk 已有 `Amount`，Market 已有 `SubscriptionId`；共享数值类型现在进一步区分了数量、价格、金额、比例和有符号数量，避免用一个通用 `Decimal` 承载不同量纲。

### 本轮已落地

- 新增低依赖共享 crate `kairos-domain-types`，提供 `Symbol`、`Exchange`、
  `AccountId`、`OrderId`、`ClientOrderId`、`Currency`、`Quantity`、`Price`、`PriceDelta`、
  `Money`、`Rate`、`SignedQuantity`、`Ratio`、`Sequence`、`UnixNanos`、`Generation`、`DurationNanos`、`BasisPoints`，以及
  canonical `OrderSide`、`OrderStatus` 和 Risk/Execution identity types。
- Account 已改为复用共享 `AccountId`，Account 专有的身份类型仍留在
  Account domain 内。
- Integration 与 Execution 已复用同一个 `OrderSide`；
  `OrderEntryStatus`、`ExecutionOrderStatus` 到 canonical `OrderStatus`
  的转换为显式映射。
- 新增 `scripts/check_domain_architecture.py`，并接入 Rust CI，检查跨模块
  私有 services 导入、canonical enum 重复定义以及旧交易场所术语回流。

以上是类型化迁移的基础阶段。Risk authorization/reservation、ExecutionPlan 核心字段、
Market 全部 observation 数值、Account application command/query/result、snapshot
水印，以及 Integration 订单 request/response、账户和市场 normalized facts 的核心身份、数值
和时间字段已经完成一轮迁移；部分跨模块 wire API 仍在进行，
因此剩余重点已经从核心业务对象迁移，收敛为少量 policy 配置、provider/application wire adapter 的显式转换，以及持久化/兼容边界的复核。

本轮定向验证已通过：`kairos-domain-types` 测试、Market 全部定向测试、Account 全部定向测试、
Execution 17 个单元测试、Risk 18 个全目标测试、Risk architecture tests、Account architecture tests
与 Account domain tests。全 workspace 验证已完成。

## 领域类型化状态

| 对象 | 当前状态 | 主要问题 |
|---|---|---|
| `Symbol` | 核心 domain 已完成 | 核心 domain、normalized facts、Execution facade 和 quote observation 已使用 shared `Symbol`；provider/application wire 仍在 adapter 边界保留 `String` |
| `Exchange` | 核心 domain 已完成 | 核心 domain 已使用 shared `Exchange`；剩余 primitive 仅允许位于明确的 provider/application wire 边界 |
| `AccountId` | 核心 domain 已完成 | Account、Risk、Execution、Integration normalized facts 和 application facade 已使用 shared `AccountId`；composition/contract 仍是显式 wire 边界 |
| `OrderId` | 核心 domain 已完成 | Execution、Account、IntentState/IntentEvent、ExecutionEvent、ExecutionFillReport、ExecutionAuditQuery/Event 和 Integration order-entry request/event 已使用 shared `OrderId` |
| `ClientOrderId` | 核心 domain 已完成 | Integration `OrderRequest/Order`、Execution facade 和 normalized order facts 已使用 shared `ClientOrderId`；provider payload 保留 wire primitive |
| `Currency` | 核心 domain 已完成 | Account fill、market profile 手续费资产、Risk/Integration normalized facts 和 capability settlement assets 已类型化；asset code 只保留在 wire/adapter 边界 |
| `Quantity` | 核心 domain 已完成 | Execution、Market、Integration normalized facts 和 Account 的余额/仓位/订单/成交已按 `Quantity`/`SignedQuantity` 区分；wire 边界仍保留 decimal primitive |
| `Price` | 核心 domain 已完成 | Execution、Market、Integration normalized facts 和 Account position/fill/mark-to-market 已使用 shared `Price` |
| `OrderSide` | 已完成 | Execution 与 Integration 复用 shared canonical enum，contract 通过显式边界转换 |
| `OrderStatus` | 核心 domain 已完成 | Execution、Account open order、Execution 远端订单更新/未知订单、Integration execution stream/normalized open orders 和 remote-order query 已使用 typed status；provider status 保留显式映射 |
| `ReferenceStatus` | 已完成 | Reference domain、Integration reference normalized payload、Market 内部 descriptor/observation 已使用 typed lifecycle status；contract/wire 仍在边界保留字符串 |
| `Sequence` | 核心 domain 已完成 | Market、Risk、Account、Execution、Reference 和 Integration normalized facts 已使用 shared `Sequence`；持久化和 contract 边界仍保留 wire primitive |
| `UnixNanos` | 核心 domain 已完成 | 核心 domain 和 application facade 已使用 shared `UnixNanos`；snapshot/contract/provider 边界明确保留 wire timestamp 并通过 adapter 转换 |

仍保留 primitive 的代码均属于已审查的边界层，典型位置包括：

- [execution/domain/order.rs](../crates/business/execution/service/src/domain/order.rs)；
- [market/domain/market.rs](../crates/business/market/service/src/domain/market.rs)；
- [market/domain/observations.rs](../crates/business/market/service/src/domain/observations.rs)；
- [integration/domain/order.rs](../crates/kairos-integration/src/domain/order.rs)。

Execution 的订单数量、限价、已成交量和成交回报的数量/价格/费用，以及订单和
ExecutionPlan 的 plan/leg/intent/market/remote-order、账户/分段/订单标识已经类型化；Account snapshot 时间、fill/observation
的订单、资产、状态和时间戳，Market observation 的时间戳与全部主要数值字段也已经类型化。
ExecutionPlan、Risk domain、Market observation、Account application/domain、Integration
订单 request/response，以及 Integration 账户和市场 normalized facts 已收口；Market application
的 order-book update 端口和 Risk circuit 时间入口也已改为 shared `Sequence`/`UnixNanos`。
Reference service domain 的 canonical ID/时间字段、`ReferenceStatus` 生命周期状态、Reference catalog 的 `UnixNanos` reconcile 入口、Integration reference normalized payload 的 canonical ID/数值字段、Integration execution-access normalized fact、Reference listing/provider symbol/issuer identity、Execution 查询/事件的 Integration facade、Execution quote observation、`ExecutionFillReport`、Execution audit query/event、unknown remote order normalized fact、Execution snapshot 水印、intent event 的 `IntentId/Sequence/UnixNanos`、Execution event 的 `UnixNanos` 时间和订单/intent/plan/leg/remote-order/fill identity、IntentState 的 `OrderId` 列表、IntentState/IntentEvent 的完成量、内部调度订单键、`IntentState` 的更新时间和 quote refresh 时间、`HedgeRequirement` 的完整数量值、SubmitOrder、ExecuteStrategyIntent、RefreshQuoteIntent、`IntentLegRequest`、取消/过期/替换订单 API，以及 Execution simulator/backtest 已完成一轮迁移。Execution 的远端订单更新和未知远端订单状态、Market application 的 `ExecutionEstimate`、Market 内部 `MarketDescriptor` 与 `InstrumentStatus` 也已改为 typed status/value objects。Execution、Market、Account snapshot 的 actor identity，以及 Account Actor 的 `Generation` / `Sequence` 也已类型化；Risk 的 `current_pnl` 已改为 `Money`，Account 已完成 `SignedQuantity`/`Price`/`Money`/`Rate` 分层，Execution maker/split policy 已完成时间、库存和比例字段类型化。剩余工作集中在少量 wire adapter 和持久化兼容边界；这些字段需要明确转换，不能让 primitive 直接进入 domain。

## 其他剩余问题

### 重复的领域枚举

当前至少存在多套相近类型：

- `OrderSide`：Execution domain、Execution contract、Integration domain；
- order status：`OrderStatus`、`OrderEntryStatus`、`ExecutionOrderStatus` 和 Account 状态；
- 多个模块各自表示 ID、market、instrument 和外部订单标识。

需要先确定 canonical 类型，再为 provider-specific 状态保留显式映射，不能用 `String` 作为兼容层。

### Domain、contract、integration 边界

Contract 层为了 JSON/FlatBuffers 兼容，保留 `String` 和整数是合理的；但这些 primitive 不应直接泄漏回 domain。

目标已落实为：wire payload -> contract primitive -> 显式转换 -> domain value object；反向也经过显式转换。剩余 primitive 是 wire/CLI/persistence 的表示，不进入 domain 公共字段。

### 数值语义

项目仍在 wire 和 adapter 边界使用 `Decimal`、`Amount`、`DecimalValue` 以及 mantissa/scale；domain 已将货币金额、订单数量、价格、手续费、比例和风险限额区分为 `Quantity`、`SignedQuantity`、`Price`、`Money`、`Rate` 和 `BasisPoints`。

### 时间和序列语义

wire 层仍可能以 `u64` 表示时间、序列或 generation；domain/application API 已分别使用 `UnixNanos`、`Sequence` 和 `Generation`，避免这些值互换。

### 静态架构检查

以下规则已经加入测试或 CI：

- 禁止跨模块导入 `services`；
- 禁止 application 暴露 provider payload、persistence record 或 SDK client；
- 禁止 domain 依赖 transport/SDK；
- 禁止新增核心 ID、时间、序列字段的裸 primitive；当前已由脚本对 domain 公共字段执行门禁；
- 禁止重复定义 canonical `OrderSide` 和内部订单状态。

Reference application 的 service fixture 直接引用仅存在于测试边界，不影响生产依赖方向。

## 已完成项与后续优化

### P0：建立共享基础类型（已完成）

低依赖的 `kairos-domain-types` 已建立并提供：

`Symbol`、`Exchange`、`AccountId`、`OrderId`、`ClientOrderId`、`Currency`、`Quantity`、`Price`、`Sequence`、`UnixNanos`。

每个类型应具备构造校验、规范化规则、`Display`/只读访问、serde 支持，以及与 contract/integration 的显式 `TryFrom`/`From` 转换。不要允许任意 `String` 隐式转换。

### P1：迁移核心 domain（已完成）

以下迁移已完成：

1. Execution order、intent、plan；
2. Risk authorization、reservation；
3. Market descriptor、order book、observation；
4. Account position、open order；
5. Integration adapter 边界。

Contract model 可以暂时保留 primitive，以降低协议兼容风险，但必须在 adapter 边界完成转换。

### P2：统一 enum 和状态映射（已完成）

canonical `OrderSide` 和内部 `OrderStatus` 已确定；provider-specific 状态通过显式单向映射保留，不强行压成一个 enum。

### P3：把类型规则变成门禁（已完成）

检查已禁止核心 domain 公共 API 出现核心 ID、`symbol: String`、`exchange_id: String`、`*_unix_nanos: u64`、`sequence: u64`、`quantity: String` 和 `price: String`。Contract、generated 和 adapter 层可以保留 primitive。

### P4：建立性能基线后再优化

优先为 Market polling、order book 合并、Execution planning/preflight、Risk reservation 建立 benchmark，使用 profiling 确认真实热点，再决定是否引入批处理、缓存、泛型或零拷贝抽象。

## 完成标准

领域类型化阶段完成后，至少应满足：

- 12 个核心对象都有明确 domain 类型或经过审查的 enum；
- Execution、Risk、Market、Account 的核心 domain 不再用裸 `String` 表示核心 ID；
- 时间戳、事件序列和 generation 不再全部使用可互换的 `u64`；
- 数量、价格、金额和比例有不同语义类型；
- `OrderSide` 和内部订单状态拥有明确 canonical owner；
- contract/integration 与 domain 之间有显式转换；
- workspace tests、clippy、fmt、Python tests 全部通过；
- 架构规则由 CI 自动检查，而不是依赖人工搜索。

性能优化可以在当前架构上提前开始，但必须先记录 benchmark 基线和 profile 证据；大范围抽象整理应等领域类型化完成后进行。
