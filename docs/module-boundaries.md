# DDD 项目构建方法论（Agent 基线）

本文是本项目进行新增功能、重构和删除代码时的架构基线。Agent 在修改代码前必须阅读本文，并以“业务归属、状态所有权、依赖方向”作为主要判断依据。

## 1. 总目标

项目持续朝以下方向收敛：

```text
Domain      定义业务规则
Usecase     执行业务用例
Actor       持有状态、管理数据流和运行循环
System      负责装配和生命周期
Monitor     负责观察、投影和输出
CLI         负责入口适配
Infrastructure 提供具体实现
```

不要为了目录整齐而拆分模块。每个概念必须有唯一的业务归属者、状态拥有者和生命周期拥有者。

## 2. 依赖方向

允许的主要依赖方向如下：

```text
CLI / HTTP / Scheduler
        ↓
System / Actor
        ↓
Usecase Application
        ↓
Usecase Domain
        ↓
本模块 Protocol / Port
        ↑
Infrastructure / Adapter / Persistence
```

必须遵守：

- 跨模块只能依赖目标模块的 `application/` API。
- 不得跨模块导入 `services/`、`domain/` 内部实现或具体 Connector。
- Domain 不依赖 Actor、System、CLI、Infrastructure 或其他业务模块。
- Usecase 不依赖 CLI、运行时表面或具体厂商 SDK。
- 具体实现只能在 composition、factory、adapter、infrastructure 或测试 fixture 中选择和注入。
- System 不承载业务规则；它只能进行系统级装配、启停和生命周期协调。

## 3. 模块内部结构

每个业务模块优先采用：

```text
module/
├── application/   对外公开的业务用例 API
├── domain/        业务实体、值对象、不变量和状态迁移
├── protocol.py    本模块作为消费者所需要的最小契约
└── services/      本模块内部实现，不是跨模块 API
```

判断代码应该放在哪里：

- 代码表达业务规则，放 `domain/`。
- 外部调用者需要主动执行某个业务动作，放 `application/`。
- 代码只是实现 application 的内部细节，放 `services/`。
- 代码是具体 SDK、数据库、Connector 或运行模式实现，放 adapter/infrastructure/services/runtime。
- 代码负责把已经存在的组件接起来，放 composition/factory，而不是伪装成业务服务。

`application/` 是公开边界，不代表其中每个类都必须面向终端用户。只要被模块外部调用，就必须经过 application API。

## 4. Domain 原则

Domain 只处理业务语言和业务不变量，例如：

- 订单状态是否允许迁移。
- 风险预算是否足够。
- 账户状态是否满足执行条件。
- reference 生命周期事件如何改变业务状态。

Domain 不处理：

- 消息总线和 Actor 生命周期。
- CLI 参数、配置文件和日志输出。
- 数据库、网络请求和第三方 SDK。
- 系统启动、停止和运行模式。

如果 Domain 需要外部能力，应由消费者在 `protocol.py` 定义最小 Port，再由外部实现注入。

## 5. Usecase 原则

Usecase Application 负责一个可描述的业务动作或业务查询：

- 接收业务请求类型。
- 调用 Domain。
- 组织本模块服务。
- 协调跨模块 application API。
- 管理事务和返回业务结果。

Usecase 不应成为：

- 全局 Service Locator。
- 系统运行时总管。
- Actor 状态仓库。
- CLI 的参数和输出层。
- 多模块所有能力的聚合入口。

公开 API 不得暴露 service 实例、SDK 类型、原始 vendor payload、数据库 record 或通用 `object` 作为主要业务接口。

## 6. Actor 原则

Actor 是运行时业务拥有者，不是另一个名称的 Service。

当前业务 Actor 方向：

```text
actor/market   reference / market usecases
actor/account  account / execution usecases，以及 Intent 和订单状态
actor/risk     risk usecase 和风险状态
actor/monitor  生命周期观察、Timeline、Projector 和输出
actor/support  通用 Actor 基类、Supervisor、命令和连接支持
```

Actor 应负责：

- 持有自己的可变运行时状态。
- 接收明确的 Command。
- 在 `start()` 后启动自己的数据循环。
- 自己管理订阅、连接和数据流。
- 调用对应 Usecase 完成业务动作。
- 将产生的 Event 发布到 Bus。
- 汇报自身生命周期和错误状态。

外部可以向 Actor 发命令，但不应持续驱动 Actor 的内部循环。System/Supervisor 负责启停和监视，不负责替 Actor 轮询业务数据。

Actor 不应复制 Domain 规则，也不应持有其他 Actor 的内部状态。跨 Actor 协作应使用公开 application API 或消息。

### 状态所有权

每份重要状态只能有一个权威拥有者：

| 状态 | 拥有者 |
| --- | --- |
| 行情订阅和行情数据流 | Market Actor |
| 多账户状态 | Account Actor |
| 订单和成交状态 | Account Actor |
| IntentJournal | Account Actor |
| 风险预算和风险状态 | Risk Actor |
| Timeline 和展示投影 | Monitor Actor |
| 系统启停状态 | System / Supervisor |

其他模块只能通过命令、Usecase 查询或事件观察这些状态，不能共享可变对象并各自解释。

## 7. System 原则

System 是系统运行壳，不是业务层。它只负责：

- 读取和转换启动资源。
- 组装 Actor、Supervisor、Bus 和 Monitor。
- 注入 Connector、Store、Usecase 实现。
- 启动、等待和停止运行时。
- 处理系统级命令和生命周期。

System 中出现以下内容时应优先下沉：

- 账户、订单、成交、Intent 的状态变更 → Account Actor。
- 行情订阅和 reference 刷新 → Market Actor。
- 风险判断 → Risk Usecase/Risk Actor。
- 展示和 Timeline → Monitor Actor。
- 业务不变量 → Domain。

System 可以做跨 Actor 的总装配，但不应持有跨 Actor 业务状态，也不应成为新的 `BusinessManager`。

## 8. Command、Event、Query

三种消息必须区分：

```text
Command  请求某个拥有者执行动作
Event    描述某件事已经发生
Query    读取状态，不改变状态
```

例如：

```text
SubscribeMarketDataCommand → MarketActor
MarketDataReceivedEvent    → Bus → Strategy / Account / Monitor
SubmitOrderCommand         → AccountActor
OrderFilledEvent           → Bus → Account / Monitor
```

规则：

- Command 必须有明确目标和执行者。
- Event 应尽量是事实，不隐藏额外的业务副作用。
- Query 不得修改状态。
- 不要用 callback、processor 或 generic handler 把三者重新混在一起。

## 9. CLI 和组合层

CLI 只负责参数解析、配置转换、调用公开 application API 和格式化结果。

CLI 不得：

- 调用其他模块的 `services/`。
- 直接创建或管理业务状态。
- 直接操作 Domain 内部对象。
- 自己实现业务流程。
- 依赖具体 Connector。

Composition/factory 负责选择具体实现并注入依赖。它可以知道多个模块，但不应被伪装成业务模块或业务 Actor。

Actor 自己的组装逻辑可以放在 Actor 自己的 `application/assembly.py`；不要重新建立一个全局 `actor/application` 聚合层。

## 10. Protocol 使用原则

新增 Protocol 前必须确认：

1. 存在明确的消费者。
2. Protocol 属于消费者，而不是被实现者。
3. 契约足够小，只描述消费者真正需要的能力。
4. 至少存在可替换实现，或测试确实需要隔离实现。

以下情况不应新增 Protocol：

- 只是给一个具体对象改名。
- 没有消费者。
- 只是为了让依赖图“看起来解耦”。
- Protocol 暴露了过多 service 方法或内部状态。

没有业务语义、只转发调用的抽象应删除。

## 11. 命名和反模式

以下名称需要特别谨慎：

```text
Manager / Coordinator / Processor / Handler / Callback
Context / Registry / Facade / Runtime / Projector
```

使用前必须回答：

- 它属于哪个模块？
- 它拥有哪份状态？
- 输入、输出和生命周期是什么？
- 为什么不能由已有 Actor、Usecase 或 Domain 承担？

如果回答不清楚，优先删除、合并或下沉，而不是继续增加中间层。

## 12. Agent 修改流程

每次修改必须按以下顺序执行：

### A. 先确定业务归属

把需求改写为业务动作，并确定唯一的状态拥有者。

### B. 再确定边界

判断它属于 Domain、Usecase、Actor、System、Monitor、CLI 还是 Infrastructure。

### C. 先设计公开接口

先定义 Command、Query、Event 或 application request/result，再实现内部细节。

### D. 保持依赖单向

检查跨模块 import，确保只进入目标模块 `application/`。

### E. 删除旧概念

重构不是新增一层兼容包装。完成迁移后删除旧的 service、protocol、callback、manager 和重复入口。

### F. 验证

至少执行：

```bash
uv run pytest -q
git diff --check
rg -n "from .*\.services|import .*\.services" kairospy tests
rg -n "Manager|Coordinator|Processor|Callback|Registry" kairospy/application
```

并检查是否存在跨模块依赖、重复状态拥有者和无消费者抽象。

## 13. 完成标准

一次架构修改只有同时满足以下条件才算完成：

- 业务规则位于正确的 Domain 或 Usecase。
- 可变运行时状态只有一个拥有者。
- Actor 负责自己的循环、连接和数据流。
- System 没有新增业务判断。
- CLI 没有绕过 application 边界。
- 没有跨模块导入私有 services。
- 无用的旧概念和兼容层已经删除。
- 重点测试和全量测试通过。
- 静态搜索没有引入新的边界违规。

最终判断标准：

> 删除 CLI 后业务用例仍然成立；替换 Connector 后 Domain 仍然成立；停止某个 Actor 时，只有它拥有的运行时能力停止；System 被简化后，业务规则不应消失。
