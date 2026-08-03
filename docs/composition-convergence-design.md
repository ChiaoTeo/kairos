# Composition 功能模块收敛设计

本文定义 `application/support/composition` 的定位、边界、统一装配入口以及 live、paper、backtest 三种运行模式的收敛方案。

本文遵循：

- `docs/module-boundaries.md` 中的模块调用和依赖规则；
- `docs/system-layer-design.md` 中 System、runtime、launch 和 composition 的职责划分；
- `docs/integration-convergence-design.md` 中 Integration connection assembly 的设计。

本文解决的问题不是让三种模式共享同一套实现，而是让它们能够通过同一组稳定能力被 `TradingSystem` 使用。

## 1. 结论

Composition 应定位为一个运行实例的组合根（composition root）：

> 根据配置和运行模式选择具体实现，完成依赖注入，构造统一的 `TradingSystem`。

Composition 负责“把系统造出来”，不负责“让系统跑起来”，也不负责运行期间的业务协调。

目标结构为：

```text
配置 / 运行模式
        ↓
composition
        ├── 选择 live / paper / backtest 实现
        ├── 创建 integration / persistence adapter
        ├── 创建 usecase runtime component
        ├── 注入各模块 protocol
        └── 组装 TradingSystem
                ↓
system
                ↓
runtime
```

收敛后，三种模式都产生相同形状的 `ComposedLaunch`，由 launch application 再创建系统根对象：

```text
LiveComposition     -> ComposedLaunch
PaperComposition    -> ComposedLaunch
BacktestComposition -> ComposedLaunch
                         ↓
                    TradingSystem
```

`TradingSystem` 和 runtime 不需要知道具体组件来自 live、paper 还是 backtest；它们只依赖统一的能力契约。

## 2. 为什么需要先抽象模块入口

当前回测、仿真和实盘服务在实现上存在天然差异：

| 能力 | Live | Paper | Backtest |
| --- | --- | --- | --- |
| 市场数据 | 外部实时连接、订阅和请求 | 实时或可替换事件源 | 历史数据迭代、数据下载 |
| 账户 | 真实账户、私有流、状态恢复 | 模拟账户 | 模拟账户、成交和权益曲线 |
| 执行 | 外部订单提交和撤单 | 模拟成交 | 回测撮合或模拟成交 |
| Reference | 外部或本地目录 | 本地或外部目录 | 历史数据对应的目录 |
| 生命周期 | 连接、checkpoint、恢复 | 通常无外部恢复 | 数据集和运行结果收尾 |

如果 composition 直接依赖这些具体服务，就会出现：

- 每种模式有一套不同的 System 入口；
- System 或 launch 需要判断当前模式并调用不同服务；
- runtime 逐渐知道具体服务和 vendor；
- application service 被迫暴露 connector、SDK 或 raw payload；
- composition 变成运行期间的万能协调器。

因此需要统一的是“上层使用的能力”，而不是底层服务的全部方法。

正确的抽象方式是：

```text
LiveMarketService
PaperMarketService
BacktestMarketService
        ↓ 实现
MarketRuntimeComponent
        ↓ 被注入
RuntimeComponents
        ↓ 被 System/runtime 使用
```

抽象的是能力契约，不是强行建立一个包含所有模式特性的巨大父类。

## 3. 两类入口必须分开

### 3.1 业务 Application API

业务模块对外暴露面向业务意图的入口，例如：

```python
market_application.bars(request)
account_application.snapshot(request)
execution_application.submit(request)
reference_application.catalog(request)
```

这些入口：

- 使用业务 request/result 类型；
- 不暴露 SDK、connector、raw payload 或 persistence record；
- 不暴露 service 实例；
- 不因为运行模式不同而改变业务语义。

它们供业务模块、surface 或 System application 使用，不是 composition 的具体工厂。

### 3.2 Runtime / Composition Assembly API

各 usecase 可以在自己的 `application/runtime.py` 提供运行时装配入口，例如：

```python
build_live_market(...)
build_paper_market(...)
build_backtest_market(...)

build_live_account(...)
build_simulated_account(...)

build_live_execution(...)
build_simulated_execution(...)
```

这些入口的职责是：

- 接收 composition 注入的 connection、store、配置和 protocol 实现；
- 创建本 usecase 内部的 runtime service；
- 返回统一的 runtime-facing component；
- 不让业务 usecase 依赖这些运行时实现。

它们是 composition/runtime assembly API，不是普通业务 API。

## 4. 统一能力契约的定义方式

### 4.1 只抽象 System/runtime 真正需要的能力

建议先从 `RuntimeComponents` 的消费者反推最小契约，而不是从现有具体 service 的全部方法反推接口。

示意：

```python
class MarketRuntimeComponent(Protocol):
    def events(self) -> RuntimeEventLine | None: ...


class AccountRuntimeComponent(Protocol):
    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None: ...


class ExecutionRuntimeComponent(Protocol):
    def submit(self, request: SubmitOrderRequest) -> SubmitOrderResult: ...
    def cancel(self, request: CancelOrderRequest) -> CancelOrderResult: ...
```

实际方法应以现有 runtime/application 的消费者为准。这里的重点是：

- 接口窄；
- 使用 domain/application 类型；
- 不使用 vendor 参数和 raw payload；
- 不把万能 connector 抽象成 protocol；
- 不把所有模式特有能力塞进公共接口。

### 4.2 模式专属能力单独表达

Live 的私有流、状态恢复和连接健康检查，不应为了与 backtest 对齐而塞进所有账户组件。

可以分为两层：

```text
AccountRuntimeComponent       # System/runtime 的共同能力
LiveAccountLifecycle           # live 专属生命周期能力
BacktestMetrics                # backtest 专属结果能力
```

Composition 可以同时装配这些对象，但 `TradingSystem` 只依赖其共同能力和明确的 lifecycle 接口。

### 4.3 Protocol 归消费方

如果 runtime 消费 `MarketRuntimeComponent`，协议应属于 runtime 或实际消费它的模块；如果 account application 需要一个账户快照端口，则协议属于 account application。

Composition 负责选择实现和注入，不应成为所有模块协议的拥有者，也不应通过自己的 `protocol.py` 变成万能服务目录。

## 5. Composition 的目标职责

### 5.1 配置解释和装配决策

Composition 可以读取已经解析和校验过的配置，并做以下决策：

- 运行模式；
- market source 类型；
- account / execution 实现；
- Integration connection spec；
- persistence store；
- runtime state store；
- artifact output factory；
- lifecycle implementation。

配置解析和业务配置校验仍归 launch/config application，不应全部迁入 composition。

### 5.2 具体实现选择

具体 connector、store、adapter 和模式实现只能在 composition、factory 或测试 fixture 中选择：

```text
(mode, exchange, product, credential, source)
        ↓
concrete implementation
```

不应在每次业务调用时重新按照 broker、account、market、provider 或 raw gateway 选择 connector。

### 5.3 依赖注入

Composition 将 Integration、Persistence 和其它模块的实现注入 usecase application/runtime：

```text
integration.application.connect(spec)
        ↓
connection-scoped typed capabilities / connection
        ↓
account application/runtime
execution application/runtime
market application/runtime
reference application/runtime
```

Composition 可以知道具体实现，但业务模块不应反向知道 composition 的实现细节。

### 5.4 组装 SystemSpec

Composition 最终应产出统一的 `SystemSpec` 或 `TradingSystem`：

```python
TradingSystem(
    TradingLaunchSpec(
        launch_id=...,
        mode=...,
        strategy=...,
        resources=TradingRuntimeResources(...),
        lifecycle=...,
        assembly=...,
    )
)
```

Composition 不直接调用 `system.run()`、`session.process()` 或 runtime event loop。

## 6. Composition 不应承担的职责

以下职责应移出 composition，或只保留“创建实现”的部分：

| 当前职责 | 目标归属 |
| --- | --- |
| 启动和执行运行会话 | `system` / `launch` |
| event pump、处理器和调度 | `runtime` |
| 订单状态迁移 | execution usecase/domain |
| 账户账本和账户规则 | account usecase/domain |
| 市场订阅业务语义 | market usecase/application |
| connector、SDK、payload 转换 | integration |
| backtest 指标计算 | backtest result/reporting |
| runtime event projector 实现 | runtime |
| CLI 参数解析和文本输出 | surface / launch |
| account lease 控制 | system control / launch，视最终语义决定 |

Composition 可以创建这些能力的实现并注入，但不应拥有它们的运行时协调逻辑。

## 7. 对当前代码的调整建议

### 7.1 `LiveComposition`、`PaperComposition`、`BacktestComposition`

当前三个类的 `launch()` 同时负责装配、运行、生命周期和输出。

建议调整为：

```python
class LiveComposition:
    def compose(self, configured: ConfiguredLive) -> ComposedLaunch: ...


class PaperComposition:
    def compose(self, configured: ConfiguredPaper) -> ComposedLaunch: ...


class BacktestComposition:
    def compose(self, configured: ConfiguredBacktest) -> ComposedLaunch: ...
```

`compose()` 内部只做：

1. 创建 connection scope；
2. 通过 Integration application 获取 connection-scoped typed capabilities；
3. 创建 market/account/execution/reference runtime component；
4. 创建 lifecycle 和 runtime assembly；
5. 组装 `TradingRuntimeResources`；
6. 返回 `ComposedLaunch`，其中包含 `TradingRuntimeResources`、可选 lifecycle 和结果构造器。

`launch application` 负责调用 `TradingSystem.run()`、account lease、状态日志、结果和 artifact 写入。

真正的 `start()`、`run()`、`process()` 由 System 负责。

### 7.2 `application/accounts.py`

当前文件同时包含：

- live/paper/backtest 账户装配；
- 账户路由；
- broker/client 选择；
- launch result 构建；
- backtest equity、trade、risk 结果转换。

建议拆成：

```text
composition/application/account_components.py
    live / paper / backtest account assembly

composition/application/account_bindings.py
    account book 到 connection / capability 的绑定

backtest/application/results.py 或 reporting/
    equity curve、closed trades、risk metrics

launch/application/result.py
    launch result 的组织和输出
```

Composition 只保留账户组件和 binding 的装配。

### 7.3 `projectors/`

当前 `composition/projectors/` 中的类会处理 runtime event、维护 view 并发布 projection，它们本质上是 runtime 实现，不是组合逻辑。

建议：

- projector 实现移动到 runtime 的 services/application；
- composition 只通过 `RuntimeAssembly.projectors(...)` 选择或创建 projector 集合；
- `RuntimeAssembly` 成为 composition 与 runtime 实现之间的窄边界。

### 7.4 `runtime_services.py`

该文件可以保留为 runtime assembly 入口，但需要明确其定位：

```python
compose_runtime_assembly() -> RuntimeAssembly
```

它不应被普通业务调用，也不应暴露具体 runtime service。`RuntimeApplicationServices` 如果只是内部实现，应避免成为跨模块构造参数类型。

### 7.5 `resources.py`

当前 `composition/application/resources.py` 同时提供 data store、reference store、market access、account access、execution access 等多个 factory，容易演变成全局资源服务定位器。

建议：

- 将这些 factory 按资源类型拆分；
- 只在 composition factory 内部调用；
- 不让 System facade 或普通业务模块依赖它；System facade 的一次性 CLI 资源应通过自己的 facade adapter，直接消费 Integration/Persistence application API；
- 对外只暴露最终装配后的 application/runtime component。

### 7.6 `services/accounts_authority.py`

该文件包含 account lease、交易授权以及 runtime wrapper。它不是纯粹的 concrete implementation factory，而是运行时控制策略。

应根据最终语义迁移到：

- `system/application/control` 或 launch control；或
- account application 的 authorization orchestration。

Composition 可以创建并注入它，但不应让其它模块通过 `composition.services` 直接导入它。

### 7.7 `artifacts.py`

`launch_output()` 作为 output factory 可以留在 composition-facing assembly 中；但 artifact 的写入、发布和生命周期应由 System/projector 或 launch application 负责。

## 8. 推荐的目标目录

目标结构可以逐步收敛为：

```text
kairospy/application/support/composition/
├── application/
│   ├── compose.py              # 统一 compose 入口
│   ├── live.py                # live 组件装配
│   ├── paper.py               # paper 组件装配
│   ├── backtest.py            # backtest 组件装配
│   ├── integrations.py        # Integration connection assembly
│   ├── runtime.py             # RuntimeAssembly 组装
│   └── persistence.py         # store / output / lifecycle 选择
├── services/
│   └── （仅保留 composition 私有 helper）
└── protocol.py                # 没有消费方协议时可以删除
```

同时，相关模块应提供：

```text
usecases/market/application/runtime.py
usecases/account/application/runtime.py
usecases/execution/application/runtime.py
usecases/reference/application/runtime.py
support/runtime/application/assembly.py
support/system/application/assembly.py
```

其中 `application/runtime.py` 或 `application/assembly.py` 是 composition-facing 入口，但不是普通业务 API。

## 9. 推荐的迁移顺序

### 阶段一：固定目标对象

先稳定：

- `TradingSystem`；
- `TradingLaunchSpec`；
- `TradingRuntimeResources`；
- `RuntimeComponents`；
- `RuntimeAssembly`。

这一步不移动大量实现，只确认 composition 最终要产出什么。

验收标准：live、paper、backtest 都能表达为同一种 `TradingSystem` 输入。

### 阶段二：盘点每个 usecase 的 runtime 能力

分别从 System/runtime 的消费者出发，列出：

- market 最小能力；
- account 最小能力；
- execution 最小能力；
- reference 最小能力；
- lifecycle 最小能力。

为每项能力确定 protocol 的归属和输入输出类型。

验收标准：没有 protocol 使用 vendor payload、SDK 类型或宽泛的万能 client。

### 阶段三：补齐各模式的装配入口

让各 usecase 的 `application/runtime.py` 提供模式对应的构造入口：

```text
market:    live / paper / backtest
account:   live / simulated
execution: live / simulated
reference: live / local / backtest
```

各模式实现可以不同，但返回的能力角色要统一。

验收标准：composition 不再直接构造 usecase 内部 service，而是调用 usecase 的 runtime assembly API。

### 阶段四：将三个 Composition 改为纯装配（已落地）

把 `launch()` 拆分为：

```text
compose()      # composition
start()        # system
run/process()  # system/runtime
```

已从 composition 中移除：

- 运行循环；
- 直接启动 runtime；
- 运行期间的事件协调；
- 业务结果计算；
- 业务输出写入。

当前实现为 `BacktestComposition.compose()`、`PaperComposition.compose()` 和
`LiveComposition.compose()`，统一返回 `ComposedLaunch`；运行和收尾集中在
`support/launch/application/launcher.py` 的 `_run_composed()`。

### 阶段五：迁移 projectors、metrics 和授权策略

按职责分别迁移：

- projector -> runtime；
- backtest metrics -> backtest reporting/result；
- account authority -> system control、launch 或 account authorization；
- launch result/output -> launch/system application。

### 阶段六：收紧依赖边界

执行静态检查：

```bash
rg 'from .*\.services|import .*\.services' kairospy tests
rg 'from .*\.protocol import|import .*\.protocol' kairospy tests
rg 'support\.composition\.application' kairospy/application/support/system kairospy/application/usecases
```

重点清理：

- `launch -> composition.services`；
- 普通业务模块 -> composition resources factory；
- System facade -> composition 作为资源服务定位器；
- composition -> 其它模块 services；
- runtime -> concrete connector 或 vendor SDK。

## 10. 目标依赖关系

推荐依赖图：

```text
surface
  -> launch.application / system.application

launch.application
  -> composition.application

composition.application
  -> usecase.application
  -> usecase.application.runtime
  -> integration.application
  -> persistence.application
  -> runtime.application assembly
  -> system.application assembly

system
  -> runtime.application
  -> 已组装的 RuntimeResources

runtime
  -> RuntimeComponents / RuntimeAssembly

usecase.application
  -> own protocol / own domain

integration.services
  -> vendor SDK
```

Composition 不应被其它业务模块当作业务能力依赖。它是应用启动和运行实例创建阶段使用的 assembly boundary。

`docs/system-layer-design.md` 中如果出现 `composition -> system.services`，应调整为 composition 对 System 的 composition-facing application/assembly 入口，避免违反模块边界规范。

## 11. 验收标准

### 结构

- 三种模式都返回同一种 `TradingSystem`；
- composition 是 concrete implementation 的唯一选择点；
- composition 不直接执行运行循环；
- composition services 不作为跨模块 API；
- projectors、metrics、授权策略不再因为“被组装”而全部归属于 composition。

### 类型和 API

- System/runtime 依赖窄的能力契约；
- protocol 由消费方定义；
- application API 不暴露 SDK、raw payload、params 或 persistence record；
- runtime assembly API 与业务 application API 明确区分。

### 行为

- 可以用 fake market/account/execution component 构造 System 测试；
- 替换 live、paper、backtest 不需要修改 System/runtime；
- 替换 Integration connector 不需要修改业务 usecase；
- 不支持的配置在 composition 阶段失败；
- System 启动后，运行期间不重新创建或解析 connector。

### 静态检查和测试

- 没有跨模块导入 `services`；
- 没有业务模块依赖 composition factory；
- 每种模式至少有一组从 composition 到 `TradingSystem` 的装配测试；
- runtime 可以通过 fake `RuntimeComponents` 单独测试；
- live、paper、backtest 的差异测试集中在各自 assembly，而不是散落在 System 中。

## 12. 最小落地目标

本轮不需要一次性重写所有实现。建议先完成以下最小切片：

1. 为 `TradingSystem` 定义稳定的统一输入；
2. 为 market、account、execution 建立最小 runtime-facing contract；
3. 在各 usecase `application/runtime.py` 暴露模式装配入口；
4. 将 `LiveComposition.launch()` 改为只返回 `TradingSystem`；
5. 以同样方式迁移 paper 和 backtest；
6. 由 System 统一负责 `start/run/process`；
7. 删除 `launch -> composition.services` 的跨模块依赖；
8. 再移动 projectors、metrics 和 authority 等次级职责。

这条路径能够先建立正确的装配边界，再逐步迁移实现，避免在没有稳定目标契约的情况下大规模搬移代码。
