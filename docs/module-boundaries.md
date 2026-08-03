# 模块边界规范

本文是 KairosPy 的模块调用规范。它约束新增代码和重构代码的依赖方向；历史代码可以逐步迁移，但新代码不得继续扩大旧边界。

## 一、每个模块的三类职责

每个业务模块都按下面的方式组织：

```text
<module>/
  application/   # 对外能力：其它模块唯一允许调用的实现层
  services/      # 对内实现：业务编排、状态变化、私有细节
  protocol.py    # 对依赖的契约：本模块声明自己需要什么能力
```

三者的含义不是简单的目录命名，而是访问权限：

| 部分 | 责任 | 谁可以直接依赖 |
| --- | --- | --- |
| `application` | 对外用例、命令、查询、结果 DTO；稳定地表达本模块提供的能力 | 其它模块、surface、composition |
| `services` | 对内业务实现；可拆分为多个 service、handler 或 helper | 本模块自己的 `application`、本模块内部代码 |
| `protocol.py` | 本模块对依赖方的需求声明；定义 Protocol、输入端口和必要的错误契约 | 本模块自己的 `services` / `application`；实现方 |

`service` 可以实现上游模块的 `protocol`，但“实现协议”和“暴露实现”是两件事：实现类仍然属于本模块内部，调用者只能通过上游声明的协议或本模块自己的 `application` 入口访问它。

## 二、唯一允许的模块调用方式

模块 A 依赖模块 B 时：

```text
A -> B.application
```

不允许：

```text
A -> B.services.SomeService
A -> B.services.some_helper
A -> B.protocol.SomePort      # 除非 A 正在实现这个 protocol
A -> B 的任意内部文件
```

这里的“唯一允许”指跨模块调用。模块 B 自己的 `application` 可以调用 B 的 `services`；模块 B 内部也可以使用自己的 `protocol`。但任何其它模块都不能绕过 `application` 直接调用这些实现细节。

## 三、依赖协议的归属

Protocol 的定义权属于使用方，而不是实现方：

```text
使用方模块
  protocol.py  <- 声明需要的最小能力
       ^
       |
实现方 services / adapter / infrastructure
```

例如账户用例需要读取余额时，账户模块定义 `AccountSnapshotPort`，integration 或 persistence 模块实现它。账户模块不应该依赖 integration 的 concrete service；integration 也不应该要求账户模块导入自己的内部实现。

允许：

```python
# application/usecases/account/protocol.py
class AccountSnapshotPort(Protocol):
    def snapshot(self, request: AccountSnapshotRequest) -> AccountSnapshot: ...

# infrastructure/integrations/services/adapters/account.py
class BinanceAccountService:
    def snapshot(self, request: AccountSnapshotRequest) -> AccountSnapshot:
        ...
```

不允许：

```python
# application/usecases/account/services/reconcile.py
from kairospy.infrastructure.integrations.services.registry import IntegrationRegistry
from kairospy.infrastructure.integrations.connectors.binance import BinanceClient
```

具体实现应由 composition 组装后注入：

```text
composition
  -> concrete service / adapter
  -> account application service(AccountSnapshotPort)
```

## 四、依赖方向

推荐的总体方向是：

```text
surface / CLI
  -> module.application
  -> module.services
  -> module.protocol (声明依赖)

composition
  -> 选择 concrete implementation
  -> 注入 module.protocol 的实现

infrastructure / adapters
  -> 实现 application 或 protocol 契约
  -> 转换 vendor payload、存储 record 和外部错误
```

核心约束：

- DDD `domain` 不依赖其它业务模块，也不依赖 `application`、`infrastructure`、`surface` 或外部实现。
- `domain` 可以依赖稳定的通用能力，例如标准库、时间/金额等基础类型和明确版本化的 shared kernel；通用能力不得反向依赖业务模块。
- 业务模块之间的协作由 `application` 编排，不能把跨模块调用下沉到 Domain。
- `application` 不直接依赖 concrete connector、SDK、数据库 client 或 raw payload。
- `services` 不作为跨模块 API；不得从其它模块导入。
- `protocol` 是契约，不是服务定位器，不通过它暴露一组 concrete service。
- `surface` 只调用 `application`，不直接访问 `services`、connector 或 persistence。
- concrete implementation 的选择只发生在 composition、factory 或测试 fixture。
- integration 的 raw payload、SDK 类型和存储 record 必须在边界转换后再进入 application/domain。

### Domain 与业务模块的边界

Domain 只表达本模块稳定的业务概念和规则。即使另一个业务模块提供了看似有用的能力，Domain 也不能直接导入它的 Application、Service、Protocol 或 Infrastructure 实现：

```text
错误：account.domain -> market.application
错误：order.domain   -> integration.infrastructure
正确：account.application -> market.application
正确：composition -> account.domain + market.application + concrete adapter
```

如果 Domain 确实需要一个外部能力才能完成规则判断，应先判断它是否其实属于 Application 编排；只有本模块拥有的、稳定且最小的领域端口才可以留在 Domain 边界内。该端口由 Domain 定义，由上层 Application 或 Infrastructure 在组合根注入实现。不要为了复用方便，把另一个业务模块的 Application 伪装成 Domain 依赖。

## 五、application 的设计要求

`application` 是模块的公开 API，应当稳定、窄、面向业务意图：

- 入口使用 request/command DTO，返回 result DTO、domain model 或明确的 domain event。
- 方法名表达 KairosPy 的业务语义，例如 `submit`、`cancel`、`snapshot`、`bars`。
- 不透传 vendor 的 `params`、SDK 对象或数据库 row。
- 不把内部 service 实例、repository、connector 暴露给调用者。
- 模块包根 `__init__.py` 不做聚合导出；调用方直接依赖真实归属文件。

运行时装配例外：如果一个 usecase 需要为 runtime/composition 提供模式实现，可以在自己的 `application/runtime.py` 暴露窄的装配入口。该入口只服务于 runtime assembly，不代表业务调用方可以直接依赖 `services`，也不应被业务 usecase 或 surface 当作业务 API 使用。

模块对外提供的是能力，不是目录结构。调用方应依赖：

```python
from kairospy.application.usecases.account.application.reconciliation import AccountReconciliationService
```

而不是：

直接从其它模块导入 `services` 下的具体实现。

## 六、service 的设计要求

`services` 是实现层，不是第二个 API：

- service 可以调用本模块的 protocol、domain 和必要的其它模块 `application`；这条规则适用于 Application Service，不适用于 DDD Domain Service。
- DDD Domain Service 只能依赖本模块 Domain、稳定通用能力和本模块拥有的领域端口，不能调用其它业务模块的 `application`。
- service 可以实现其它模块声明的 protocol。
- service 不应被加入公共 `__init__.py`，不应作为跨模块构造参数类型暴露。
- service 的生命周期、缓存、重试、编排和内部 helper 都属于实现细节。
- 若一个 service 被多个模块直接调用，优先把稳定行为收敛到提供方 `application`，而不是继续扩大 service 的可见性。

## 七、protocol 的设计要求

`protocol.py` 只描述依赖能力的最小接口：

- 由消费方定义，放在消费方模块。
- 只包含消费方真正需要的方法。
- 参数和返回值使用 domain/application 类型，不使用 vendor payload。
- 方法名使用业务能力语义，例如 `bars`、`funding_rates`、`catalog`、`submit`、`cancel`；不要把 vendor/client 风格的 `fetch_*`、`watch_*`、raw payload 或 SDK 参数形状作为 usecase protocol 的公共契约。
- 不把整个 connector 或万能 client 抽象成一个宽 Protocol。
- 允许 infrastructure、persistence、runtime adapter 实现它。

例外：如果协议本身就是模块对外的稳定扩展点，可以由该模块的 `application` 一并公开；这时必须明确记录其生命周期和兼容性责任，不能仅因为“方便 import”就公开内部协议。

## 八、composition 是唯一的组装点

composition 负责把抽象和实现接起来：

```text
配置 / 运行模式
  -> composition
  -> concrete connector / store / service
  -> protocol implementation
  -> application service
  -> runtime / surface
```

以下位置不得自行创建 concrete implementation：

- 普通业务 `application`；
- 模块 `services`，除非创建的是本模块纯内部对象；
- runtime processor、dispatch、view；
- CLI/TUI command；
- domain。

这样可以让业务服务使用 fake/in-memory 实现测试，也能让 live、paper、backtest 只在 composition 中替换实现。

## 九、代码审查清单

新增或移动模块时，逐项确认：

- 跨模块 import 是否只指向目标模块的 `application`？
- 是否有其它模块 import 了目标模块的 `services`？有则拒绝。
- 这个 Protocol 是谁消费的？是否定义在消费方？
- concrete connector、SDK、store 是否只在 composition 或 infrastructure 边界出现？
- application DTO 是否仍然暴露 raw payload、vendor 参数或 persistence record？
- 是否因为一个调用方而暴露了整个 service？能否收敛成一个窄 application 用例？
- `__init__.py` 是否只导出稳定 API？
- 测试是否从 application 入口验证行为，并通过 protocol 注入 fake？

可用下面的搜索作为初步检查，结果需要结合语义判断：

```bash
rg 'from .*\.services|import .*\.services' kairospy tests
rg 'from .*\.protocol import|import .*\.protocol' kairospy tests
```

第一条命中通常意味着跨模块边界被绕过；第二条命中只有在实现协议或使用本模块内部协议时才合理。

## 十、迁移原则

边界迁移按能力切片完成，不增加长期兼容层：

1. 找出调用方、实现方、composition root 和测试。
2. 先定义目标 `application`、`services` 和 `protocol` 形状。
3. 一次性迁移调用方和实现方。
4. 删除被替代的跨模块 service import 和旧出口。
5. 通过静态搜索和测试确认没有新的越界依赖。

过渡桥接只能存在于同一次迁移中，并应在该切片完成前删除。规范的目标不是让旧结构永远可用，而是让模块之间只有一个清晰的调用入口。

## 十一、integration 与 runtime 的边界

`infrastructure/integrations` 只负责把外部系统能力接入系统，不负责管理业务状态机，也不拥有全局连接生命周期。

连接相关职责按单链路模型划分：

- `infrastructure/integrations/application` 定义一个连接的 request、权限范围、传输类型、生命周期 Protocol 和业务 Protocol；
- `infrastructure/integrations/services` 创建并实现一条具体的 HTTP/WebSocket 链路，持有 raw connector 和 vendor translator；
- `application/usecases/*` 通过业务 Protocol 调用连接，不知道连接内部的 vendor client；
- `composition` 根据 `ConnectionSpec` 选择具体交易所连接并注入业务 runtime。

一个 `ConnectionSpec` 只描述一个 participant、一个 product、一个 access scope 和一个 transport。一次 `connect` 返回一条 Connection，不返回包含多个 Access 的 assembly。连接可以同时实现多个窄业务 Protocol，例如 private REST 同时实现账户读取和订单提交；这只是同一链路的业务复用。

Integration 对外的长期连接类型包括 `MarketDataConnection`、`MarketStreamConnection`、`AccountConnection`、`AccountStreamConnection`、`OrderConnection` 和 `OrderUpdateConnection`。连接的 `connection_id`、participant、transport、lifecycle、health 和 binding 属于 Integration domain；业务状态机仍由各业务模块拥有。

旧 `IntegrationGatewayProvider`、`provider.raw.*` 和 raw facade 出口已从运行代码中删除。新的业务和 composition 只能通过 Integration application 组装分类 Connection；resolver、registry 和 raw connector 只存在于 Integration services 的内部边界。
