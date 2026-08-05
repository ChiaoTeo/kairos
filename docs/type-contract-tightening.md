# 类型契约与模块边界收紧计划

## 1. 文档目的

本文定义项目整体减少宽泛类型、明确业务语义和收紧模块契约的改进方法。

本次工作不是机械地把所有 `str`、`object` 替换成更复杂的类型，而是识别每个值的业务含义、所有权、生命周期和依赖方向，使类型成为模块设计的一部分。

本文以 `docs/module-boundaries.md` 为架构基线。任何具体改动都必须同时遵守其中关于 Domain、Usecase、Actor、System、CLI、Infrastructure 和 application API 的约束。

## 2. 问题定义

当前代码中存在较多以下形式：

```python
value: object
Mapping[str, object]
dict[str, object]
status: str
kind: str
```

它们本身并不一定错误，但当它们出现在 Domain、application API、跨模块协议或业务事件中时，会产生以下风险：

- 调用方无法从接口判断输入和输出的真实结构；
- 非法值只能在运行时通过条件判断或异常暴露；
- 业务概念被字符串拼写和约定隐式表达；
- `getattr`、`str(...)` 和字典取值在系统中扩散；
- 重构时静态检查无法有效发现影响范围；
- 外部 SDK、原始 JSON 和持久化记录可能渗透到核心业务层；
- 模块之间的责任边界被通用字典或上下文对象掩盖。

## 3. 总体目标

### 3.1 目标

- application API 使用业务导向的 request、result、command、query 和 event 类型；
- Domain 使用实体、值对象、枚举和明确的不变量；
- 跨模块依赖只进入目标模块的 `application/` API；
- 外部 payload 只在 adapter/infrastructure 边界存在；
- 具体依赖通过消费者拥有的最小 `Protocol` 注入；
- 新增代码不再扩大 `object`、`Any` 和 `dict[str, object]` 的使用范围；
- 通过静态检查和架构测试持续阻止回退。

### 3.2 非目标

- 不追求消灭所有 `str`；
- 不为每一个字段创建没有业务含义的包装类；
- 不把所有字典改成一个巨大的 `Context` 或 `Config`；
- 不为了类型安全引入新的全局 Manager、Coordinator、Registry 或兼容 Facade；
- 不改变业务行为，除非当前隐式类型规则本身就是缺陷。

## 4. 判断方法：先识别语义，再选择类型

每个待修改的宽泛类型都必须回答以下问题：

1. 这个值表达什么业务概念？
2. 哪个模块拥有它的定义和状态？
3. 谁负责创建、解析和校验它？
4. 它是 Domain 数据、application 请求、外部数据还是展示数据？
5. 调用方真正需要知道哪些能力？
6. 这个类型是否会穿越模块边界？
7. 是否存在多个合法状态或明确的不变量？

如果这些问题无法回答，优先澄清业务归属和接口，而不是直接添加类型别名。

## 5. 类型选择规则

### 5.1 `str`

普通文本、日志内容、CLI 参数和外部符号可以继续使用 `str`。

以下情况应考虑专用类型：

| 语义 | 推荐表达 |
| --- | --- |
| 订单、账户、市场、资产标识 | 值对象或明确的 ID 类型 |
| 订单状态、运行模式、生命周期状态 | `Enum` |
| 事件类型、命令类型 | 明确的事件/命令类或受限枚举 |
| 交易方向、时间粒度、数据集类型 | 值对象或 `Enum` |
| 具有格式和校验规则的代码 | 值对象，并在构造时校验 |

类型别名只能改善可读性；如果需要运行时校验或防止不同 ID 互换，应使用 `NewType`、不可变值对象或领域类，而不是仅写 `OrderId = str`。

### 5.2 `object`

`object` 只应出现在真正未知的动态边界，例如：

- 外部 JSON 尚未解析的 payload；
- 通用序列化入口；
- 测试替身的无关占位参数。

一旦系统理解了数据结构，就应在边界处转换成明确 DTO、`TypedDict`、数据类、实体或值对象。`object` 不应作为主要的 application 业务输入或输出。

### 5.3 字典

- 临时的局部实现可以使用字典；
- 穿越 application 边界的结构应使用 request/result 数据类；
- 外部响应应使用 adapter 私有 DTO 或解析函数；
- 具有固定字段的消息不得使用无约束的 `dict[str, object]`；
- 具有动态键集合的配置，应明确动态部分的值类型、来源和校验规则。

### 5.4 Protocol

Protocol 属于消费者模块，必须最小化，只描述消费者真正需要的能力。

新增 Protocol 前必须确认：

- 存在明确消费者；
- 协议属于消费者，而不是实现者；
- 协议不暴露 SDK、数据库记录或 service 实例；
- 具体实现由 composition、adapter、infrastructure 或测试 fixture 注入。

## 6. 推荐的边界转换

动态外部数据必须沿以下方向收敛：

```text
外部 JSON / SDK 响应
        ↓
Infrastructure Adapter
        ↓
明确 DTO / Protocol
        ↓
Application Request / Result
        ↓
Domain Entity / Value Object
```

例如，交易所返回的原始字典可以存在于 gateway 内部，但不能直接传入 Domain、Actor 或跨模块 application API。

## 7. 分阶段实施方案

### 阶段一：建立基线

目标是知道问题在哪里，而不是立即大规模修改。

工作内容：

- 搜索 `object`、`Any`、`dict[str, object]`、`Mapping[str, object]`；
- 搜索业务层中高风险的裸 `str`；
- 标记每个位置的模块、层级、调用方和数据来源；
- 区分生产代码、测试代码、示例代码和外部边界；
- 记录当前测试和类型检查状态。

每个条目至少记录：

```text
文件和符号
当前类型
业务含义
状态拥有者
生产者
消费者
所在层
是否跨模块
迁移目标
风险等级
```

### 7.1 类型迁移登记模板

每次迁移以一个公开契约或一个紧密相关的契约组为单位登记。不要先按文件数量分批，也不要把同一批中没有业务关系的宽泛类型一起修改。

```markdown
### 迁移项：<契约名称>

- 文件 / 符号：
- 所属模块：
- 所在层：Domain / Application / Actor / System / Infrastructure / Surface
- 业务拥有者：
- 状态拥有者：
- 命令、事件或查询类型：
- 当前类型：
- 生产者：
- 消费者：
- 是否跨模块：是 / 否
- 当前问题：
- 目标类型：
- 边界解析位置：
- 兼容入口删除条件：
- 风险等级：低 / 中 / 高
- 重点测试：
- 状态：待处理 / 进行中 / 已完成 / 阻塞
```

登记时应优先填写 application API、`protocol.py`、Domain event、Actor Command 和 Actor Event。局部实现中的字典、测试占位参数以及 `object.__setattr__` 不作为独立迁移项，除非它们实际穿越了公开边界。

### 7.2 动态边界豁免登记

允许保留宽泛类型的位置必须能够说明动态性来自哪里，以及它在哪里结束。可使用以下格式登记：

```markdown
### 动态边界：<边界名称>

- 文件 / 符号：
- 动态数据来源：外部 JSON / SDK / 序列化 / 持久化 / 测试替身 / 展示字段
- 允许的宽泛类型：
- 解析后的明确类型：
- 解析位置：
- 禁止进入的层：Domain / Application API / 跨模块 Protocol
- 保留原因：
- 覆盖测试：
```

动态边界不是公开契约的豁免。原始 payload 只能停留在 adapter、infrastructure 或序列化入口，并必须在进入 Domain 或 application API 前完成解析。

### 阶段二：收紧公开契约

优先处理影响范围最大的接口：

- `application/` 的公开函数、命令和查询；
- `protocol.py` 中的跨层契约；
- 跨模块事件和消息；
- application result 和 view model；
- Actor 接收的 Command 和发布的 Event。

先定义新的业务类型和新接口，再迁移调用方，最后删除旧的宽泛入口。

### 阶段三：收紧 Domain

为关键业务概念建立值对象、枚举和明确的状态迁移规则：

- 标识不再依赖可互换的裸字符串；
- 状态变化通过 Domain 行为完成；
- 构造函数或工厂负责校验不变量；
- Domain 不依赖 application、infrastructure、surface 或外部 SDK。

此阶段不得把外部 payload 类型直接搬进 Domain。

### 阶段四：隔离外部动态数据

处理 Binance、IBKR、Massive、CCXT、HTTP、WebSocket、JSON 和持久化层：

- 在 adapter 内解析字段和处理 vendor 差异；
- 将数据转换为项目自己的 DTO 或 Domain 类型；
- 对缺失字段、类型错误、单位差异和枚举未知值进行明确处理；
- 禁止 application 直接依赖 vendor payload 或 SDK 类型。

### 阶段五：删除旧抽象并防止回退

- 删除已迁移的兼容函数、宽泛 Facade 和重复协议；
- 增加静态检查，阻止 application API 新增 `object` 或 `Any`；
- 增加跨模块 import 检查；
- 对动态边界建立明确豁免，并说明原因；
- 将类型检查纳入日常测试和 CI。

## 8. 优先级

按以下顺序处理可以获得较高收益：

1. 跨模块 application API 中的 `object` 和通用字典；
2. Domain 中表达业务状态、ID 和事件类型的裸 `str`；
3. Actor、System 和运行时对象之间的宽泛依赖；
4. 外部 gateway 与 application 之间的原始 payload；
5. persistence、CLI、TUI 等边缘层；
6. 测试和示例中的宽泛类型。

优先级判断依据是：影响范围、运行时风险、业务关键程度和迁移成本，而不是文件数量。

## 9. 验收标准

一次迁移只有满足以下条件才算完成：

- 公开 application API 不以 `object`、`Any` 或无约束字典作为主要业务接口；
- 核心业务状态和标识具备清晰语义；
- Domain 不依赖外部 payload、SDK 或其他业务模块；
- 跨模块调用只进入目标模块的 `application/`；
- 每份重要可变状态仍只有一个 Actor 或明确拥有者；
- System 没有因为类型改造变成新的业务协调层；
- 旧接口、兼容层和重复抽象已删除；
- 新增 Protocol 有明确消费者且保持最小化；
- 重点测试和全量测试通过；
- `git diff --check` 通过；
- 静态搜索未发现新增的跨模块 `services` 导入或宽泛公开契约。

建议每个阶段执行：

```bash
uv run pytest -q
git diff --check
rg -n "from .*\.services|import .*\.services" kairospy tests
rg -n "object|Any|dict\[str, object\]|Mapping\[str, object\]" kairospy
```

## 10. 风险与控制

### 迁移范围过大

控制方式：以模块和公开契约为单位分批迁移，保持每个提交可测试、可回滚。

### 类型过度设计

控制方式：只有存在业务语义、不变量或跨边界价值时才创建新类型。简单文本不必包装。

### 兼容层长期存在

控制方式：为兼容入口设置迁移目标和删除条件，不把旧 API 永久保留为“临时方案”。

### 静态类型变好但业务边界变差

控制方式：每次改动同时检查业务归属、状态所有权、依赖方向和 Actor 生命周期。

### 测试替身掩盖真实契约

控制方式：测试 fixture 也实现真实 Protocol，不用 `object()` 或任意 Mock 逃避接口设计。

## 11. 最终原则

本次工作的核心不是让代码看起来“类型更多”，而是让每个模块都能清楚回答：

```text
我拥有哪种业务状态？
我对外提供什么用例？
我依赖哪个最小契约？
我接受和返回什么业务类型？
哪些动态数据必须在边界被解析？
```

最终目标是：删除 CLI 后业务用例仍然成立；替换 Connector 后 Domain 仍然成立；停止一个 Actor 时只有它拥有的运行时能力停止；System 被简化后，业务规则不应消失。

## 12. 第一批迁移清单

第一批只处理跨边界、影响范围大且业务语义已经明确的类型。每个模块选择不超过 3～5 个契约，先完成一个模块的闭环，再扩展到下一个模块。

### 12.1 选择顺序

按以下顺序选择迁移项：

1. application API 中作为主要输入或输出的 `object`、`Any` 和无约束字典；
2. `protocol.py` 中暴露原始 payload、SDK 类型或 service 实例的契约；
3. Actor 接收的宽泛 Command 和发布的宽泛 Event；
4. Domain 中表达状态、身份和生命周期的裸 `str`；
5. adapter 到 application 之间尚未解析的外部数据。

以下内容暂不作为第一批目标：局部变量、日志字段、CLI 原始参数、测试替身、序列化实现细节，以及只用于 `__eq__`、`__setattr__` 或通用转换入口的 `object`。

### 12.2 第一批登记表

| 批次 | 契约 / 符号 | 所属模块 | 当前问题 | 目标类型 | 状态 |
| --- | --- | --- | --- | --- | --- |
| T1 | `ReferenceApplication.refresh_*` | reference | `source, **kwargs`，返回类型不明确 | `ReferenceRefreshRequest` + `ReferenceProviderRefreshResult` | 已完成 |
| T1 | `ReferenceCatalogSource` / lifecycle source | reference | source 请求使用隐式关键字和动态参数 | `ReferenceCatalogRequest`、`ReferenceLifecycleRequest`、最小 Protocol | 已完成 |
| T1 | `ReferenceCommandApplication` source access | reference | 以 `object` 暴露 connector | `ReferenceCatalogSource` / `ReferenceProviderSource` | 已完成 |
| T1 | `ReferenceApplication.resolve` / Domain market resolver | reference | 市场输入使用 `object` | `SymbolRef | MarketRef | str` | 已完成 |

登记完成后，先确认每一项的业务拥有者、状态拥有者和消费者，再开始修改代码。若其中任一项无法确认，应将它标记为“阻塞”，不得通过新增 `Context`、`Manager` 或兼容 Facade 暂时绕过。

### 12.3 单项迁移流程

每个迁移项都执行以下步骤：

1. 写清业务语义和不变量；
2. 在消费者所属模块定义 request、result、command、query、event 或最小 Protocol；
3. 在边界处把动态输入转换成明确类型；
4. 迁移所有生产者和消费者；
5. 删除旧的宽泛入口、重复类型和兼容包装；
6. 增加架构测试，防止同类契约回退；
7. 执行 focused tests、全量测试和静态检查。

### 12.4 单项完成标准

迁移项只有同时满足以下条件才可标记为“已完成”：

- [ ] 业务拥有者和状态拥有者已确认；
- [ ] application API 已使用业务导向类型；
- [ ] Protocol 属于明确的消费者且保持最小化；
- [ ] 外部动态数据已在边界解析；
- [ ] Domain 未引入 application、infrastructure、SDK 或其他业务模块依赖；
- [ ] 所有调用方已迁移；
- [ ] 旧入口和重复抽象已删除；
- [ ] 重要可变状态仍只有一个 Actor 或明确拥有者；
- [ ] focused tests 通过；
- [ ] `uv run pytest -q` 通过；
- [ ] `git diff --check` 通过；
- [ ] 静态搜索未发现新的跨模块 `services` 导入或宽泛公开契约。

### 12.5 阶段二退出条件

阶段二“收紧公开契约”完成的条件是：

- 被选中的 application API 不再以 `object`、`Any` 或无约束字典作为主要业务输入或输出；
- 每个保留的动态类型都已登记来源、解析位置和禁止穿透的层；
- 跨模块调用只进入目标模块的 `application/` API；
- 每个旧入口都有明确的删除结果，而不是长期保留的兼容层；
- 通过重点测试、全量测试、`git diff --check` 和架构静态检查。

### 12.6 第二个完整模块目标：market

`market` 作为一个完整迁移目标处理，但“完成”指跨边界契约闭环完成，不要求删除动态数据在 adapter 内部的合理使用。

| 契约 / 符号 | 业务拥有者 | 状态拥有者 | 当前问题 | 目标类型 | 状态 |
| --- | --- | --- | --- | --- | --- |
| `MarketApplication` data/query/ingestion/replay/subscription 入口 | market | Market Actor | 通过内部 application service 属性间接调用 | facade 方法：`read`、`download`、`subscribe`、`handle_event` 等 | 已完成 |
| `MarketDataSpec.kind`、dataset identity | market | market Domain | 数据类型依赖裸 `str` / `object` | `MarketDataKind`、明确的 dataset identity 输入 | 已完成 |
| `MarketDataReader` / `MarketDataWriter` / store | market | persistence adapter | store、client 和 rows 契约宽泛 | `MarketDataStore`、`MarketHistoricalClient`、`MarketDataRow` | 已完成 |
| market stream / replay runtime | Market Actor | Market Actor | runtime source、历史 client、构造函数使用 `object` 和 `**kwargs` | typed runtime source、connection、historical client 和显式构造参数 | 已完成 |
| market Actor / composition / CLI | Market Actor | Market Actor | 通过 service 属性或通用 connector 调用 market 能力 | 只调用 `MarketApplication` 和消费者 Protocol | 已完成 |
| Binance / Massive market adapter | infrastructure | adapter connection | vendor 时间和 options 类型穿入 market 契约 | `MarketTime`、`MarketOptions`，在 adapter 边界解析 payload | 已完成 |

#### market 动态边界登记

- 原始外部 JSON、CCXT / HTTP 响应和 websocket payload：保留在 infrastructure gateway、normalizer 和 client 内部；不得进入 `MarketApplication`、Market Actor 或 Domain。
- 持久化行：通过 `MarketDataRow` 进入 market application，并在读取后转换为 `Bar`、`Quote`、`TradePrint`、`OrderBookSnapshot`、`RateObservation` 或 `OptionGreeks`。
- 运行时 metadata：仅通过 `MarketWarmupStatus` 进入 market application；未知 vendor 字段不进入该类型。
- 连接 options：使用 `MarketOptions`，只允许字符串、数字、布尔值和空值；具体 vendor 参数由 infrastructure adapter 消费。

#### market 完成验收

- [x] application facade 不再要求调用方访问 `data`、`queries`、`ingestion`、`replay`、`subscriptions`、`projections` 或 `events` 属性。
- [x] market Protocol 不暴露 SDK client、service 实例或原始 vendor payload。
- [x] Domain 的数据类型、时间范围、订阅计划和 dataset identity 已具备明确语义。
- [x] Market Actor、runtime、composition、CLI 和 market gateway 已迁移到公开 facade / Protocol。
- [x] 原始动态数据只保留在登记的 infrastructure / persistence 边界。
- [x] 旧的 `MarketApplication` service-property 调用入口已删除。
