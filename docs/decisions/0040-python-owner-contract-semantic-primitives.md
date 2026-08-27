# Decision 0040：Python Owner Contract 语义 Primitive 收敛

- Status: Accepted
- Date: 2026-08-28
- Extends: [Decision 0037](0037-complete-owner-contract-python-convergence.md)
- Scope: Python owner contract、Application 与 Strategy API 中的共享 identity 和精确数值

## Context

Rust owner contract、domain 与 application 已使用 `kairos-primitives` 中的共享语义类型，例如
`MarketId`、`InstrumentId`、`AccountId`、`OrderId`、`Price`、`Quantity`、`SignedQuantity`、
`Money`、`Rate` 与 `PriceDelta`。这些类型区分业务含义、约束有效值，并让相同表示但不同语义的值
不能被意外互换。

Python 边界尚未保持同等语义：

- Pyo3 companion 接收字符串后会构造 Rust identity primitive，但 Python property 又返回 `str`；
- Account、Execution、Market 与 Risk companion 分别暴露同名但彼此不同的 `NativeDecimal` class；
- `NativeDecimal` 只表达 `mantissa + scale`，不能区分价格、数量、金额、比率和差值；
- Application 与 Strategy 通过 `.value` 或重复读取 `mantissa / scale` 转成 `decimal.Decimal`；
- 自动生成的 `.pyi` 将许多已知 property 和 method result 标为 `object`，静态检查无法阻止语义值误用；
- 部分 Python business model 已使用 `MarketId` 等 primitive，部分同义字段仍使用 `str`，形成不完整的
  迁移状态。

`DecimalParts` 和 wire string 是正确的精确边界表示，但它们没有业务含义。把它们作为公开 business
value 会迫使每个 consumer 重复验证和分类，也与仓库“既有 primitive 必须直接使用”的规则冲突。

本 Decision 只规定语义 primitive 的跨语言表示和使用规则。它不改变 owner 对 command、event、query、
current view 和 mutable state 的所有权，也不建立新的 contract facade。

## Decision

### 1. 同一业务含义跨 Rust 与 Python 使用同一类语义类型

凡 Rust owner contract 字段已经使用共享 primitive，Python owner contract 对应字段必须暴露同义 Python
primitive。Contract、Application、Strategy API 和 consumer-owned typed model 不得为了保留 JSON、
FlatBuffers、Pyo3、CLI 或 provider 表示而将其降级成 `str`、`int`、`Decimal` 或 `object`。

表示形式不决定类型。字段的 owner 和业务含义决定类型：

| 含义 | Python primitive | 核心约束 |
| --- | --- | --- |
| 可观察或可交易市场身份 | `MarketId` | 非空、无首尾空白、与 Rust identity validation 对齐 |
| 经济标的身份 | `InstrumentId` | 同上 |
| 账户、Intent、Order、Fill 等身份 | 对应 `AccountId`、`IntentId`、`OrderId`、`FillId` | 不得互换 |
| 非负数量 | `Quantity` | `value >= 0`；要求成交或下单数量为正时由 owning request 追加约束 |
| 可正可负的持仓或库存数量 | `SignedQuantity` | 允许负值和零 |
| 有效价格 | `Price` | `value > 0` |
| 价格差或价格变动 | `PriceDelta` | 允许负值和零 |
| 金额、盈亏、费用、权益 | `Money` | 允许负值和零 |
| 资金费率、收益率和其他有符号十进制比率 | `Rate` | 允许负值和零；额外范围由 owner rule 约束 |

不能按字段名机械归类。`quantity` 可能是 `Quantity` 或 `SignedQuantity`，`price_change_abs` 是
`PriceDelta` 而不是 `Price`，Greek、波动率、倍数和无量纲统计量也不能在没有相同 Rust primitive
语义时被强行归入 `Rate`。迁移前必须逐字段核对 Rust contract 类型；若 Rust contract 仍使用无语义的
`DecimalParts`，先由字段 owner 决定其稳定业务类型，或明确记录该字段为何仍是无语义精确数值。

### 2. Python primitive 由共享 primitives package 拥有

Python 的 canonical primitive 放在 `kairospy.primitives` 的 owner namespace 或 cross-cutting decimal/time
namespace 中。Identity 继续按 account、execution、reference 等治理词汇组织；精确数值放在
`kairospy.primitives.decimal`。不得在每个 owner companion 中定义另一套 `MarketId`、`NativeDecimal`、
`Price` 或相似 wrapper，也不得从 `kairospy.primitives` 根添加抹平 owner namespace 的 wildcard
re-export。

Python decimal primitive 是 immutable、hashable、slots-based value object，内部 canonical value 为标准库
`decimal.Decimal`。它至少提供：

```python
@property
def value(self) -> Decimal: ...

@property
def mantissa(self) -> int: ...

@property
def scale(self) -> int: ...

def __str__(self) -> str: ...
```

公开构造接受同类型值、`Decimal`、规范十进制字符串和非 `bool` 整数；不接受 `float`，避免二进制浮点
误差被静默解释成精确业务值。构造必须与 Rust primitive 保持以下一致：

- coefficient 可表示为 `i64`；
- scale 不超过 18；
- 零规范化为 scale 0；
- 移除不改变数值的末尾小数零；
- 各语义类型执行自己的正负和零值约束；
- 无效值和运算溢出显式失败，不截断、不舍入、不饱和。

Primitive 可以与 `Decimal` 显式互操作，但不得通过继承 `Decimal` 或宽泛的运算符把语义类型再次擦除。
支持的业务运算必须返回确定类型并与 Rust checked operation 对齐，例如：

```text
Quantity * Price        -> Money
SignedQuantity * Price  -> Money
Price - Price            -> PriceDelta
Money / SignedQuantity   -> Price
```

同类型的比较、加减以及增量整除等操作只在 Rust primitive 已拥有相同语义时提供。Python 不独立发明舍入、
币种换算、单位换算或溢出策略。普通研究计算可以显式取 `.value` 后使用 `Decimal`；计算结果重新进入
contract 或 business model 时必须再次构造目标 primitive。

### 3. 原始表示只存在于明确的边界

以下位置允许保留原始字符串、固定宽度整数或 `mantissa + scale`：

- generated FlatBuffers accessor 和 verified wire decode 的内部值；
- provider SDK DTO、CLI/config 输入和 persistence row；
- Pyo3 参数提取与返回对象构造期间的局部 adapter；
- 明确命名且确实无业务语义的 diagnostic、extension 或 research value。

Adapter 在边界处只转换一次。完成转换后，原始 identity、decimal parts 或 native wrapper 不得继续流经
owner contract、Application 或 Strategy model。JSON presentation 可以把 primitive 序列化为稳定字符串，
但 presentation mapping 不反向定义业务类型。

### 4. Pyo3 companion 直接投影 canonical Python primitive

Private extension 继续是 Decision 0037 定义的唯一可执行 owner contract adapter，但其 Python-visible
property 必须返回共享 Python primitive，而不是 companion-owned wrapper。

Pyo3 内部可以暂存 Rust primitive 或 `DecimalParts`，但 `NativeDecimal` 不注册为公开 Python class，
不出现在 facade、`.pyi`、Application protocol 或 Strategy API 中。Getter 在一次 projection 中从已验证
Rust value 构造对应 Python primitive；constructor 和 method 参数提取对应 primitive 并交给 Rust owner
contract。为便于调用，公开 input 可以显式声明 `Price | Decimal | str | int` 等被支持的构造输入，但
内部 request 只保存 `Price`。Output 只返回一种 canonical primitive，不返回 union。

Companion 导入 `kairospy.primitives` 只是同一 wheel 内的表示适配，不转移业务 ownership。它不得在 Rust
侧复制 Python primitive 的验证规则：原始输入先由共享的 extraction helper 转成精确 parts，最终仍由
Rust primitive constructor 验证；cross-language test 证明 Python constructor 与 Rust constructor 接受
和拒绝相同的值。

### 5. Static typing 是 contract 的组成部分

Owner extension stub generator 必须生成具体的参数、property 和 result 类型。已知 contract 字段不得使用
`object`、隐式 `Any` 或无注解参数。Facade re-export 后，调用方看到的类型必须仍是 canonical primitive，
而不是 private native implementation type。

Stub verification 除检查 class/member/signature shape 外，还必须检查：

- 每个公开 property 和 method return 都有非 `Any`、非 `object` annotation；
- 已建立 primitive 的 semantic field 使用对应 primitive；
- optional、sequence 和 mapping 容器保留准确 element type；
- runtime value 与 stub 声明属于同一 canonical Python class；
- private native type 不泄漏到 `kairospy.strategy` 的公开 API。

### 6. 迁移采用最终类型 hard cut

本次升级不建立 `NativeDecimal -> Decimal -> semantic primitive` 两级公开迁移。每个完成迁移的 surface
直接从原始表示切换到最终 primitive，consumer 与测试在同一 change 中迁移。

可以按 owner 或 capability 分批实现，但每个字段只有一条 production path。兼容处理只允许：

- input 暂时接受旧的 `str` 或 `Decimal`，在入口立即构造最终 primitive；
- 旧 import name 直接 alias 到同一 canonical class；
- 已发布 API 确有外部兼容要求时，短期 deprecated property 可以返回旧 presentation value。

兼容层不得定义平行 DTO、保留 `NativeDecimal` output、让同一 output 随调用路径返回两种类型，或绕过
Rust owner validation。没有明确外部兼容约束时不保留 deprecated surface。

## Technical migration

迁移按以下顺序推进；步骤可以跨多个 change，但一个公开 surface 的切换必须原子完成：

1. 建立字段清单：从 Rust owner contract 类型生成或审计 Python control、event、current-view 和
   Application/Strategy 字段，记录 owner、Rust type、目标 Python type、可空性和输入兼容要求。
2. 在 `kairospy.primitives.decimal` 实现 `Quantity`、`SignedQuantity`、`Price`、`PriceDelta`、`Money`、
   `Rate`，复用一个私有 fixed-decimal mechanics，不公开无语义的 generic decimal value。
3. 增加 Rust/Python constructor、normalization、ordering、serialization 和 checked-operation conformance
   fixture；覆盖 `i64` 边界、scale 18/19、负零、末尾零、非有限 Decimal、float 拒绝和溢出。
4. 为 Pyo3 companion 建立共享 extraction/projection helper，逐 owner 替换 identity string 和
   `NativeDecimal` property；禁止 companion 自己实现 decimal text、乘法或饱和运算。
5. 升级 owner-contract stub generation 和 verification，生成 canonical import 与精确 container/result
   annotation，并移除 `object` fallback。
6. 同步迁移 `kairospy.infrastructure.contracts` re-export、Application mapping、Strategy protocol、CLI/
   presentation adapter、fixture 和 public type-contract test。
7. 删除无调用的 `NativeDecimal` class、重复 `_native_decimal` helper、consumer-side `mantissa / scale`
   reconstruction，以及业务模型中已存在 primitive 对应的裸 `str`、`int` 和 `Decimal`。
8. 增加 architecture check，拒绝 owner companion 注册 `NativeDecimal`、拒绝公开 semantic field 使用
   raw type，并维护少量按路径和字段理由命名的边界 allowlist。

### Field classification examples

以下示例说明分类方法，不替代 owner contract 源码：

| Surface field | Target |
| --- | --- |
| Market quote `bid_price / ask_price` | `Price | None` |
| Market quote/order-book `bid_quantity / ask_quantity / level.quantity` | `Quantity | None` 或 `Quantity` |
| Market bar `open / high / low / close` | `Price` |
| Market bar `volume` | 由 schema 的单位决定 `Quantity` 或明确的无语义统计值 |
| Ticker `price_change_abs` | `PriceDelta | None` |
| Ticker `price_change_pct`、funding rate | `Rate | None` |
| Account long/short/net position quantity | `SignedQuantity` |
| Order requested/filled quantity | `Quantity` |
| Average、mark、limit、fill price | `Price | None` 或 `Price` |
| Equity、PnL、fee、margin、reservation amount | `Money` |
| `market_id / instrument_id / account_id / order_id` | 对应 identity primitive |

字段若缺少 currency、asset 或 unit context，不得仅凭表名假装已解决量纲问题。现有 `Money` 和 `Quantity`
表达数值类别，不携带 currency/asset；currency/asset identity 继续由同一 contract record 的 typed field
提供。只有当前调用方确实需要并且 owner 能定义稳定不变量时，才另行引入带单位的复合 value。

## Verification

迁移完成必须同时具备以下证据：

- Python primitive unit test 与 Rust `kairos-primitives` test 使用共享 acceptance fixture；
- owner native binding test 证明 runtime property 是 canonical primitive class；
- cross-language event/current-view/control fixture 比较类型、值、optional behavior 和 decimal text；
- public API type-contract test 直接断言 `Price`、`Quantity`、`Money`、`Rate` 和 identity，而不是 `.value`
  上的裸 `Decimal` 或 `.pyi` 中的 `object`；
- architecture check 扫描 private native import、`NativeDecimal` 注册、重复 conversion helper 和已知字段的
  raw annotation；
- `make python-type-check` 无 error 或 warning；
- focused Rust/Python test、`cargo test --workspace`、`uv run pytest -q`、format、documentation、workspace
  dependency 与 `git diff --check` gate 通过。

迁移清单只有在 implementation 和 verification 同时完成时才能标记完成；仅修改 annotation 或仅包装
runtime value 都不构成收敛。

## Consequences

- Rust 与 Python consumer 对共享 identity 和精确数值使用同一业务词汇和不变量。
- Pyo3 companion 保留 owner contract adaptation 职责，但不再发布 contract-local primitive 系统。
- Python 调用方可以在类型检查阶段发现 price、quantity、money、rate 和不同 identity 的误传。
- Wire、provider、persistence 与 CLI 继续使用适合各自边界的原始表示，不需要改变现有 wire shape。
- 公开 Python API 会发生一次有意的 breaking change；通过直接迁移到最终 primitive，避免第二次
  `Decimal -> semantic primitive` 变更。
- 业务计算需要显式选择保持语义的 checked operation，或显式降到 `Decimal` 做研究计算后重新验证。
- 字段分类审计可能暴露 Rust contract 中仍使用 `DecimalParts`、`String` 或无量纲 decimal 的语义缺口；
  这些缺口由字段 owner 修复，不由 Python facade 猜测。
