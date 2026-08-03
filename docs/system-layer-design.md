# System 层设计

本文讨论 `kairospy` 当前缺失的 System 层，以及它与 usecase、runtime、integration、composition 和 launch 的职责边界。

本文是目标架构设计，不要求一次性完成目录迁移。后续迁移应按能力切片逐步进行，并遵守 [`docs/module-boundaries.md`](./module-boundaries.md) 中的模块契约。

## 1. 背景

当前代码已经存在若干与 System 相关的实现，但它们分散在不同位置：

```text
application/support/system
  LaunchControl、LaunchDaemonService、LaunchAttachSession

application/support/launch/application/runtime_host.py
  TradingSystem、TradingSystemSession

application/support/runtime
  RuntimeEngine、RuntimeEnvelopePump、projection、connection lifecycle

application/support/composition
  live、paper、backtest 的资源组装和具体实现选择
```

这些代码分别承担了控制面、运行面、执行机制和组合根职责，但缺少一个统一的 System 对象来表达：

> 一个正在运行的交易系统实例，以及它拥有的业务组件、连接资源、事件路由和生命周期。

这导致以下概念容易混淆：

- usecase application 是否就是 system；
- runtime engine 是否拥有业务组件；
- composition 是否应该直接驱动运行流程；
- integration provider 是否既是 adapter factory 又是 raw connector locator；
- launch daemon 是否拥有 system 生命周期。

## 2. 设计目标

System 层需要达到以下目标：

1. 为一个运行中的交易系统提供明确的根对象。
2. 统一管理 account、market、execution、reference、strategy 等业务组件。
3. 直接调用 runtime session/kernel 完成系统级运行，但不实现具体业务规则。
4. 让 live、paper、backtest 和 builtin system 使用同一种运行模型。
5. 让 runtime 提供运行机制，System 直接调用 runtime；integration 负责外部系统接入，composition 负责具体实现组装。
6. 不让业务 usecase 反向依赖 connector、runtime host 或 launch daemon。

## 3. 核心概念

### 3.1 Usecase

Usecase 表示一个业务动作或业务查询，例如：

- bootstrap account；
- reconcile account；
- submit order；
- cancel order；
- subscribe market；
- refresh reference；
- query current account state。

Usecase 负责业务状态变化和业务规则编排，不负责系统进程、事件循环、连接生命周期或组件发现。

### 3.2 System

System 表示一组业务能力作为一个运行实例存在：

```text
TradingSystem
├── identity
├── runtime mode
├── component registry
├── runtime session
├── connection resources
└── launch projection
```

System 可以调用 usecase application，但不应该把账户、订单或市场规则复制到自身。

### 3.3 Runtime

Runtime 负责运行机制：

- event pump；
- event loop 或调度循环；
- session；
- projection 执行；
- mode-specific execution mechanics；
- 连接资源的启动和停止钩子。

Runtime 不拥有业务组件的业务含义。System 直接创建并调用 runtime session；不额外引入 `SystemRuntimeFactory`、System lifecycle state 或 System health model。

### 3.4 Integration

Integration 负责把外部系统能力转换为业务模块需要的 port：

```text
external SDK / CCXT / HTTP / WebSocket
  -> raw connector
  -> payload translator
  -> application protocol adapter
  -> usecase
```

Integration 不拥有订单状态机、账户账本或市场订阅状态，也不负责跨业务模块编排。

### 3.5 Composition

Composition 是具体实现的组合根，负责：

- 读取配置；
- 选择 live、paper、backtest 实现；
- 创建 connector、store 和 adapter；
- 创建 usecase application；
- 创建 `SystemComponents`；
- 创建 `TradingSystem`。

Composition 不应成为长期运行中的业务协调者。它把资源和配置交给 System，由 System 直接创建 runtime。

## 4. 目标模块结构

建议继续使用现有的 `application/support/system` 作为 System 模块，而不是再创建一套平行的顶层 `kairospy/system`。

当前落地结构如下：

```text
kairospy/application/support/system/
├── application/
│   ├── __init__.py
│   └── control/
├── domain/
│   ├── __init__.py
│   ├── identity.py
│   └── components.py
├── services/
│   ├── __init__.py
│   ├── system.py
│   ├── artifacts/
│   └── projectors/
└── protocol.py
```

`services/system.py` 是系统根对象；`application/control` 和查询相关代码属于控制面。`protocol.py` 暂不定义 System 的协调协议，避免为了抽象而再造一层与 runtime 重复的模型。

## 5. System 的核心对象

### 5.1 SystemComponents

System 通过一个组件集合获得业务能力：

```python
@dataclass(frozen=True, slots=True)
class SystemComponents:
    market: object | None = None
    account: object | None = None
    account_catalog: object | None = None
    execution: object | None = None
    reference: object | None = None
    strategy: object | None = None

    def runtime_components(self) -> RuntimeComponents: ...
```

这里的对象必须是 application-facing API 或由 composition 组装好的业务组件，不应该是：

- integration service；
- CCXT/Binance client；
- persistence record；
- runtime service 的私有实现。

如果某个组件在某种模式下不存在，应使用明确的 optional/null implementation 或 protocol，而不是让 System 内部到处判断具体类型。

### 5.2 TradingSystem

```python
@dataclass(slots=True)
class TradingSystem:
    spec: SystemSpec

    def start(self) -> SystemSession: ...
    def process(self, event: RuntimeEnvelope) -> tuple[RuntimeStep, ...]: ...
    def run(self) -> RuntimeLaunchResult: ...
```

`TradingSystem` 是系统根对象，直接拥有一次 runtime session，负责协调以下动作：

```text
start
  -> start connections
  -> initialize components
  -> transition to running

process event
  -> dispatch system event
  -> invoke affected usecase
  -> collect steps/events
  -> publish projections

submit command
  -> resolve command handler
  -> invoke usecase or system operation
  -> return business/system result

stop
  -> stop components
  -> stop connections
  -> publish stopped state
```

System 不应直接解析 Binance payload，也不应在这里创建具体 connector。

### 5.3 SystemSession

`TradingSystemSession` 可以继续存在，但它应当成为 System 的运行会话，而不是隐藏在 `launch/application/runtime_host.py` 中的主要系统入口。

它负责：

- 保存一次运行实例的 session state；
- 调用 runtime engine；
- 处理 `process`、`run`、`finish`、`close`；
- 连接 projector 和 artifact writer；
- 保证关闭操作幂等。

它不应重新拥有一套独立的业务组件注册逻辑。

## 6. System 的四类服务

### 6.1 组件集合

`SystemComponents` 是系统根对象持有的业务组件集合：

```python
components = system.components
components.require("account")
components.get("reference")
```

它只提供系统级展示和组装需要的查找能力，不是全局 service locator，也不允许业务 usecase 通过字符串查找另一个业务模块。

### 6.2 Runtime 调用

System 不定义事件协调协议，也不定义自己的运行状态机。它直接调用 runtime application API：

```text
TradingSystem.start()
  -> RuntimeStores
  -> RuntimeEngineSpec
  -> create_runtime_launch_session()
  -> RuntimeLaunchSession.process/run/finish
```

runtime 负责事件泵、处理器、投影和运行步骤；System 只负责把 composition 提供的资源转换为 runtime 所需输入，并管理一次 `TradingSystemSession`。

### 6.3 控制面

CLI/TUI 的控制和查询可以调用 `system.application`：

```text
system.start / system.stop / system.attach / system.query
```

控制面最终仍通过 `TradingSystem` 或 launch registry 操作实例；不另建一套平行的 System 状态模型。

## 7. System 与现有模块的关系

```text
surface
  -> system.application
       -> TradingSystemControl
       -> TradingSystemQuery

composition
  -> SystemFactory
       -> usecase applications
       -> integration adapters
       -> runtime resources
       -> TradingSystem

TradingSystem
  -> runtime session/kernel
  -> SystemComponents
  -> launch projection

runtime
  -> event pump
  -> scheduling
  -> projection
  -> session mechanics

integration
  -> connector
  -> raw gateway
  -> protocol adapter
```

依赖方向应保持：

```text
surface -> system.application
composition -> system.services + usecase.application + integration.application
system.services -> runtime.application + usecase.application
usecase.application -> own protocol / domain
integration.services -> external SDK
```

运行时实现的装配入口位于各 usecase 的 `application/runtime.py`。它是 composition 和 runtime adapter 的唯一入口；具体实现仍保留在 usecase 内部 `services/runtime`，业务 usecase 不依赖这些运行时实现。这里的 `application/runtime.py` 是运行时装配 API，不是业务用例 API；业务调用仍应使用各模块的业务 request/result 入口。

System 不应该成为所有模块的万能依赖。业务模块之间的协作仍然放在 application orchestration 中；System 只负责把组合好的组件交给 runtime 并管理运行会话。

`TradingSystem` 不反向 import composition。composition 通过 `RuntimeAssembly` 注入 runtime service 组装、runtime projector 组装和 launch artifact output；System 只调用这些 runtime assembly 能力。

## 8. 与 `kairos_v2` 的对应关系

`kairos_v2` 中可以观察到类似的物理承载关系：

```text
kairos-core       核心状态和数据模型
kairos-exlib      外部交易所能力
kairos-system     系统级业务组件、orchestrator、reactor、worker
kairos-system-tokio runtime 和具体异步基础设施
kairos-framework  strategy lifecycle、execution framework、subscription framework
kairos-strategies 策略业务
kairos-catalog    catalog 查询、同步和持久化工作流
```

`kairospy` 当前没有完全对应 `kairos-system` 的独立承载位置，导致 System 能力散落在 `support/runtime`、`support/launch`、`support/composition` 和 `infrastructure/integrations` 中。当前目标是把运行根对象归位到 `support/system/services/system.py`，由它直接使用 runtime；不再额外引入 runtime factory 或 system-runtime 协议层。

借鉴的重点不是复制 Rust crate，而是补充一个明确的承载位置：

> System 拥有运行中的组件集合和 runtime session；运行状态模型与协调机制仍由 runtime 提供。

## 9. 不应该放入 System 的内容

以下内容仍然属于其它模块：

| 内容 | 归属 |
| --- | --- |
| 订单预占、订单状态迁移 | execution usecase/domain |
| 账户余额和持仓规则 | account usecase/domain |
| 市场订阅业务语义 | market usecase/domain |
| Binance/CCXT 请求和 payload | integration services |
| HTTP/WebSocket client | integration/runtime infrastructure |
| live/paper/backtest 具体实现选择 | composition |
| event pump 和异步调度机制 | runtime |
| CLI 参数解析和文本输出 | surface |
| launch 文件、daemon、attach | system application/control 或 launch support |

## 10. 迁移映射

建议的目标映射如下：

| 当前实现 | 目标归属 |
| --- | --- |
| `launch/application/runtime_host.py::TradingSystem` | `system/services/system.py` |
| `TradingSystemSession` | `system/services/system.py` |
| `LaunchControl` | `system/application/control.py` |
| `LaunchAttachSession` | `system/application/attach.py` |
| `LaunchDaemonService` | system control plane / launch support |
| `RuntimeEngine` | runtime |
| `RuntimeEnvelopePump` | runtime |
| `DefaultConnectionManager` | runtime connection support |
| `BacktestComposition` / `LiveComposition` | composition |
| `IntegrationGatewayProvider` | integration adapter provider |
| `ExecutionCoordinator` | execution usecase |
| `AccountService` | account usecase |

System 的配置、workspace、artifact、browsing 和 projection 能力也通过 `system/application` 暴露，surface、launch 和 composition 不再直接穿透到 System 的 `domain` 或 `services`。

## 11. 实施顺序

建议按以下顺序迁移：

### 第一步：定义 System 入口（已完成）

新增并稳定以下类型：

- `SystemIdentity`；
- `SystemSpec`；
- `SystemComponents`；
- `TradingSystem`；
- `TradingSystemSession`。

先不移动大量实现，只明确 System 的输入、输出和所有权。System 的实现直接导入和调用 runtime application；不再为 runtime 复制一套协调协议和状态模型。

### 第二步：提取 System 根对象（已完成）

将 `TradingSystem` 和 `TradingSystemSession` 从 `launch/application/runtime_host.py` 提取到 `system/services`，保留短期兼容入口。System 直接创建 `RuntimeEngineSpec` 和 `RuntimeLaunchSession`。

System 通过 `SystemSpec` 接收 composition 组装的 resources、strategy、连接和持久化 lifecycle，并在 `start()` 内直接创建/调用 runtime。System 不创建 connector，也不抽象 runtime 的协议。

### 第三步：统一组件组装

引入 `SystemFactory` 或等价的 composition 工厂，让 live、paper、backtest 和 builtin system 最终都创建相同的 `TradingSystem`。

```text
LiveComposition     -> TradingSystem
PaperComposition    -> TradingSystem
BacktestComposition -> TradingSystem
SystemComposition   -> TradingSystem
```

### 第四步：收紧边界

完成迁移后检查：

```bash
rg 'from kairospy\.application\.usecases\.[^.]+\.services' kairospy
```

重点确认：

- 跨模块只依赖 application；
- System 不导入具体 connector；
- composition 是具体实现的唯一组装点；
- runtime 不直接承担业务状态机；
- integration application 不再同时承担业务 API 和 raw connector API。

## 12. 判断标准

一次 System 层设计是否合理，可以用以下问题检查：

1. 是否能明确指出一个运行实例的根对象？
2. live、paper、backtest 是否都能产生同一种 System？
3. System 是否拥有组件和运行入口，而不是订单或账户规则？
4. 是否可以替换 runtime，而不改变业务 usecase？
5. 是否可以替换 integration，而不改变 System API？
6. 是否可以通过 fake components 测试 System orchestration？
7. surface 是否只调用 System application API？
8. composition 是否是唯一创建 concrete connector 和 adapter 的位置？

本轮可用以下检查复核边界：

```bash
uv run pytest -q
rg 'from kairospy\\.application\\.usecases\\.[^.]+\\.services' kairospy
rg 'from kairospy\\.application\\.support\\.system\\.services' kairospy/surface kairospy/application/support/launch kairospy/application/support/composition
```

第二、第三条在跨模块调用方中不应有命中；usecase 自己的 `application/runtime.py` 和 `services/runtime` 内部引用属于模块内部实现关系。

如果这些问题都能得到肯定回答，System 层就承担了它应该承担的责任，而不会重新退化成一个更大的 `Service` 或 `Manager`。
