# Runtime Composition and Protocol Boundaries

本文是对当前 `application`、`runtime`、`integration` 和 `persistence` 边界的进一步调整说明。总体架构边界和冲突解决规则见 `docs/architecture-boundaries.md`。它补充：

- `docs/application-ports-and-domain-boundaries.md`
- `docs/integration-boundaries.md`

本文重点解决以下问题：

- `Gateway`、`Translator`、`Connector` 是否是同一个概念
- 账户 runtime、账户 catalog 和 launch directory 如何分离
- runtime 是否应该直接依赖大量 application contracts
- live、paper、backtest 如何共享运行时协议
- composition 和 system 的边界在哪里

## 结论摘要

目标设计不再把所有接口都称为 `Port`。接口按使用者和边界分为三类：

```text
Integration Gateway
  外部系统语言，允许 raw payload

Application Use-case Contract
  应用用例语言，返回 core 类型

Runtime Role Contract
  runtime 运行协作角色，面向跨模式运行时能力
```

三者不能混用。

运行模式不应该产生三套 contract。live、paper、backtest 应该实现同一组 runtime role contract，由 composition 层替换具体实现。

当前代码已经删除宽 `AccountPort`。账户能力由 `AccountRuntime`、`AccountCatalog` 和 launch directory 分别表达；`LaunchScopedAccountRuntime` 只是 composition 边界的装配对象，不是新的业务 aggregate。

## 改造策略：架构重写，分阶段替换

当前项目尚未正式投入使用，因此不需要为旧的 application contract、旧的 runtime 入口或旧的 integration aggregate 保留长期兼容层。

本次改造采用以下组合策略：

```text
架构边界：重写
实现过程：分阶段替换
业务兼容：不保留
```

这里的“重写”指重写模块边界、依赖方向和公共协议，不是把整个仓库一次性删除后重新实现。已有的 core 模型、driver、connector 逻辑和测试可以在确认职责后继续复用，但必须迁移到目标边界中。

### 不采用的方案

不采用大爆炸式全量重写，原因是：

- 会同时失去已有行为验证和测试反馈；
- 很难判断问题来自业务模型还是基础设施实现；
- 迁移周期过长，期间无法形成可运行的系统。

也不采用长期渐进兼容，原因是：

- 旧的宽接口会继续被新代码使用；
- `Mapping[str, object]`、records 和 vendor 参数会继续向 application 泄漏；
- 两套协议并存会让 composition 和测试变得更复杂。

因此，每个能力切片都必须完成一次完整切换：定义目标协议、迁移所有调用方和实现、删除旧协议，然后再进入下一个切片。

### 改造原则

1. **先定边界，再改调用方**：先确定 core 类型、application contract、runtime role 和 persistence record 的职责。
2. **按能力切片迁移**：一次完整迁移 market history、account runtime 或 execution，而不是全仓库机械移动文件。
3. **优先打通垂直链路**：先让一条最小 backtest 流程完整运行，再扩展到 paper 和 live。
4. **不为模式复制协议**：live、paper、backtest 只替换 runtime role 的实现。
5. **不为兼容保留旧入口**：切片迁移完成后，旧接口、旧 alias 和旧 aggregate 一并删除。
6. **用行为测试保护迁移**：测试应验证领域状态、事件和结果，不只验证方法调用。

### 第一条垂直链路

第一阶段以最小 backtest 为架构验证对象：

```text
Historical Dataset
  -> ReplayMarketRuntime
  -> Strategy
  -> OrderIntent
  -> SimulatedExecutionRuntime
  -> SimulatedAccountRuntime
  -> AccountState / Backtest Result
```

这条链路需要先稳定以下对象和角色：

```text
Core:
  Bar / Quote / Instrument
  OrderIntent / Order / OrderEvent
  AccountSnapshot / AccountState

Runtime:
  MarketRuntime
  ExecutionRuntime
  AccountRuntime
  RuntimeClock
```

历史数据下载不是这条运行时链路的一部分。若需要自动下载，先通过 `BarHistoryPort` 或 `FundingRateHistoryPort` 准备 `Dataset`，再把数据集交给 `ReplayMarketRuntime`。

### 分阶段落地顺序

```text
阶段 0  冻结目标架构和依赖规则
阶段 1  稳定 core 模型、事件和不变量
阶段 2  建立 runtime role contract 与 RuntimeComponents
阶段 3  完成 backtest vertical slice
阶段 4  清理 persistence records 和 projectors
阶段 5  接入 paper runtime
阶段 6  接入 live Gateway、Translator 和 Runtime
阶段 7  删除旧 integration protocol、旧 aggregate 和遗留入口
```

每个阶段都应留下可运行状态。阶段之间按架构边界切分，而不是通过 compatibility adapter 让新旧系统长期共存。

### 阶段验收标准

一个能力切片只有同时满足以下条件，才算迁移完成：

- application service 不再 import 具体 integration 实现；
- application-facing 返回值不再使用 raw `Mapping[str, object]` 表达业务结果；
- runtime 只依赖目标 runtime role contract；
- records 只存在于 persistence/data 边界；
- live、paper、backtest 使用同一组 runtime contract；
- 旧协议及其调用方已经删除；
- 测试覆盖正常流程、边界状态和失败路径。

### 首个实际任务

改造开始时，不应先批量重命名目录。第一项工作应是建立 backtest vertical slice 的目标模块清单，并标出当前代码中的：

- core 类型和事件；
- runtime 调用方；
- 当前 integration 依赖；
- persistence record 和写入位置；
- composition root；
- 需要迁移或删除的测试。

清单确认后，先迁移这条 backtest 链路，完成后再按同样规则迁移 account、execution 和 live integration。

## Gateway、Translator、Connector

### Gateway

Gateway 是外部系统能力的边界。它通常接近第三方 API 或 SDK，负责调用外部系统：

- 认证、签名
- HTTP/WebSocket 请求
- 分页、重试、限流
- vendor 参数
- 原始响应读取

Gateway 可以返回 raw payload：

```python
class AccountBootstrapGateway(Protocol):
    def fetch_account(
        self,
        account: AccountBookRef,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        ...
```

Gateway 使用外部系统语言：

```text
fetch_balance
fetch_ohlcv
watch_orders
create_order
```

Gateway 不负责把 payload 转成 KairosPy 的业务模型。

### Translator

Translator 是翻译层。它把 Gateway 返回的外部 payload 转成 KairosPy 的 core model 或 domain event：

```text
AccountBootstrapGateway
  -> AccountPayloadTranslator
  -> AccountSnapshot / AccountEvent
```

Translator 负责：

- 解析外部字段
- 处理不同供应商的字段差异
- 处理外部错误和状态映射
- 生成 `Bar`、`AccountSnapshot`、`OrderEvent` 等内部模型

Translator 的输出使用 KairosPy 语言：

```text
snapshot
bars
events
```

Translator 不表达运行时协作角色，也不作为 application service 的长期兼容包装。实现 application contract 的基础设施对象用 `Gateway` 命名；实现 runtime role 的对象用 `Runtime` 命名；composition 里的接线对象用 `Binding` 或 `Factory` 命名。

### Connector

`Connector` 是一个组织和产品命名，不是第三种独立的技术边界。

Connector 可以包含 Gateway、Translator 以及少量该外部参与方的连接配置：

```text
BinanceMarketDataConnector
  - Binance REST/WebSocket Gateway
  - raw payload parser
  - Bar / Quote / MarketEvent translator
```

因此：

```text
Gateway + Translator 不是 Connector 的严格等式
```

更准确的关系是：

```text
Connector
  = 某个外部参与方的 integration 组织单元
  > 可能包含 Gateway、Translator、parser、routing
```

小型接入可以把 Gateway 和 Translator 合并在一个 Connector 类中。复杂接入应拆开，但对外仍可以用 Connector 作为 infrastructure 组织名。

### Driver

Driver 是更底层的技术封装：

```text
CcxtDriver
MassiveDriver
HTTP client
WebSocket client
```

推荐关系：

```text
External API
  -> Driver
  -> Gateway
  -> Translator
  -> Core Model / Domain Event
```

Driver 不应该直接进入 core、runtime kernel 或策略。

## 三类应用接口

### Integration Gateway

位置通常在：

```text
kairospy/infrastructure/integrations/
```

特征：

- 接近外部 API
- 可以返回 `Mapping[str, object]`
- 可以接收 vendor 参数
- 只供 integration translator 或 connector 使用

例子：

```python
class FundingRateHistoryGateway(Protocol):
    def fetch_funding_rates(
        self,
        request: Mapping[str, object],
    ) -> Iterable[Mapping[str, object]]:
        ...
```

### Application Use-case Contract

位置：

```text
kairospy/application/usecases/<area>/
```

它表达 application service 的业务需求，而不是第三方 API 形状：

```python
class FundingRateHistoryPort(Protocol):
    def fetch_funding_rates(
        self,
        request: FundingRateHistoryRequest,
    ) -> Iterable[RateObservation]:
        ...
```

Gateway 和 use-case contract 可以同时存在：

```text
FundingRateHistoryGateway
  -> FundingRateTranslator
  -> FundingRateHistoryPort
  -> HistoricalDataService
```

如果没有对应的 application use case，integration 内部不需要创建 application contract，Gateway 和 Translator 可以直接完成接入。

### Runtime Role Contract

Runtime 不应该直接依赖所有细粒度 use-case port。它只依赖少量跨模式稳定的运行时角色：

```text
MarketRuntime
AccountRuntime
ExecutionRuntime
RuntimeClock
```

这些接口表达 runtime 如何协作，而不是如何调用某个交易所。

示意：

```python
class AccountRuntime(Protocol):
    async def events(self) -> AsyncIterator[AccountEvent]:
        ...

    def snapshot(self, account: AccountBookRef) -> AccountSnapshot | None:
        ...

    def state(self, account: AccountBookRef) -> AccountState | None:
        ...
```

Runtime role contract 可以由 application runtime 或 application composition 层拥有。它不应该依赖 `infrastructure.integrations.protocols`。

## Account 重新划分

当前账户 runtime contract 位于：

```text
kairospy/application/support/runtime/contracts.py
```

`LaunchScopedAccountRuntime` 位于 `kairospy/application/support/launch/scoped_account.py`，只负责 launch composition。

目标角色分别包含：

```text
AccountRuntime: events(), snapshot(), state()
AccountCatalog: accounts(), capabilities(), fees()
LaunchAccountDirectory: launch alias、index、book routing
```

上下文边界如下：

| 能力 | 所属上下文 |
|---|---|
| `events()` | runtime 账户事件流 |
| `snapshot()` | runtime 账户查询 |
| `state()` | runtime 账户状态 |
| `accounts()` | launch/account catalog |
| `capabilities()` | account metadata |
| `fees()` | account metadata |
| `directory()` | launch composition |

目标上拆为：

```text
AccountRuntime
  events
  snapshot
  state

AccountCatalog
  accounts
  capabilities
  fees

LaunchAccountDirectory
  账户绑定、book 路由和 launch 配置
```

不要求每个方法都立刻变成一个独立 protocol。拆分的目标是让调用方只依赖它需要的角色，避免产生大量只有一个方法的接口。

### Account 的 live 流程

```text
AccountBootstrapGateway
PrivateAccountStreamGateway
  -> AccountPayloadTranslator
  -> AccountSnapshot / AccountEvent
  -> LiveAccountRuntime
  -> Runtime Kernel
```

### Account 的 paper/backtest 流程

```text
SimulatedAccountState
  -> PaperAccountRuntime / BacktestAccountRuntime
  -> Runtime Kernel
```

paper 和 backtest 不需要伪造外部账户 Gateway。

## Runtime 依赖边界

Runtime 不负责：

- 选择 Binance、OKX 或其他 Connector
- 创建 Driver 或 Gateway
- 解析 vendor payload
- 判断某个外部 API 使用什么参数
- 根据运行模式分支创建依赖

Runtime 只接收已经组装好的组件：

```python
@dataclass(frozen=True)
class RuntimeComponents:
    market: MarketRuntime
    account: AccountRuntime
    execution: ExecutionRuntime
    clock: RuntimeClock
```

Runtime kernel 负责：

- 消费市场事件
- 驱动策略
- 处理交易意图
- 调用 `ExecutionRuntime`
- 消费账户和执行事件
- 更新 runtime views
- 管理生命周期

这不是为了隐藏依赖，而是为了把“运行时协作能力”和“外部接入细节”分开。

## Live、Paper、Backtest

三种模式共享 runtime role contract，只在 composition 层替换实现：

```text
Live
  LiveMarketRuntime
  LiveAccountRuntime
  LiveExecutionRuntime
  WallClock

Paper
  LiveMarketRuntime
  PaperAccountRuntime
  PaperExecutionRuntime
  WallClock

Backtest
  ReplayMarketRuntime
  BacktestAccountRuntime
  BacktestExecutionRuntime
  SimulatedClock
```

不要创建：

```text
LiveAccountRuntime
PaperAccountRuntime
BacktestAccountRuntime
```

因为它们表达的是同一个运行时角色的不同实现。

历史数据准备与 runtime market data 仍然分开：

```text
FundingRateHistoryPort / BarHistoryPort
  -> Historical Dataset
  -> ReplayMarketRuntime
  -> Runtime Kernel
```

历史查询 port 不是 runtime market port。

## Backtest、Mock 和 Fake

Backtest 不需要实现所有 application contract，也不应该通过大量 mock 来模拟整个外部世界。

Backtest 只需要实现当前运行路径所消费的 runtime role contract：

```text
Backtest
  ReplayMarketRuntime
  SimulatedAccountRuntime
  SimulatedExecutionRuntime
  SimulatedClock
```

它们分别实现：

```text
MarketRuntime
AccountRuntime
ExecutionRuntime
RuntimeClock
```

Backtest 不需要实现：

```text
LiveAccountGateway
PrivateAccountStreamGateway
OrderSubmissionGateway
OrderCancellationGateway
```

因为这些接口属于真实外部系统接入，不属于回测运行路径。

### Backtest 优先使用 Simulation/Fake

回测中的替身应该有可观察的真实行为：

```text
ReplayMarketRuntime
  从历史数据产生 MarketEvent

SimulatedAccountRuntime
  维护内存账户状态

SimulatedExecutionRuntime
  根据成交、滑点和手续费模型产生模拟成交

SimulatedClock
  根据事件时间推进时间
```

这类组件是 simulation 或 fake，不是简单的 mock。它们用于验证策略、账户和执行逻辑在一条完整运行链路上的行为。

Mock 主要用于 application service 的单元测试，例如：

```python
account.snapshot.return_value = snapshot
execution.submit.return_value = order_state
```

不要用大量 mock 代替完整的 backtest runtime，否则测试只能证明某些方法被调用，不能证明账户状态、成交逻辑和策略结果正确。

### 历史数据准备与回测运行分开

如果历史数据已经准备好：

```text
Dataset
  -> ReplayMarketRuntime
```

此时 backtest runtime 不需要实现：

```text
BarHistoryPort
FundingRateHistoryPort
```

如果启动 backtest 时允许自动下载数据，历史 port 只属于准备阶段：

```text
BarHistoryPort
  -> Dataset Preparation
  -> Dataset
  -> ReplayMarketRuntime
```

历史数据 port 不应因此被加入 `RuntimeComponents`。

### Backtest 组件组装

目标形式：

```python
def build_backtest_components(config) -> RuntimeComponents:
    dataset = load_dataset(config.dataset)

    return RuntimeComponents(
        market=ReplayMarketRuntime(dataset),
        account=SimulatedAccountRuntime(config.account),
        execution=SimulatedExecutionRuntime(config.execution),
        clock=SimulatedClock(),
    )
```

如果需要自动准备数据，准备阶段单独接收 application contract：

```python
def prepare_backtest_dataset(
    config,
    history: BarHistoryPort,
) -> Dataset:
    ...
```

准备完成后，再创建 backtest runtime。

### Port 实现规则

判断一个 backtest 是否需要某个接口，只看当前调用路径：

| 调用路径 | 所需实现 |
|---|---|
| 纯回放已有数据 | `ReplayMarketRuntime` |
| 回放前下载历史数据 | `BarHistoryPort` 的 provider gateway 或 fake |
| 测试账户查询服务 | `AccountQueryPort` fake |
| 运行策略回测 | `AccountRuntime`、`ExecutionRuntime` |
| 测试 live runtime | 对应 Gateway mock/fake |

不应为了满足一个过大的统一接口，让 backtest 实现无意义的 `NotImplementedError`。

推荐把必需和可选能力分开：

```text
Backtest Runtime
  必须：MarketRuntime
  必须：AccountRuntime
  必须：ExecutionRuntime
  必须：RuntimeClock

Backtest Preparation
  可选：BarHistoryPort
  可选：FundingRateHistoryPort
  可选：ReferenceRuntime
```

这也是区分 `Application Use-case Contract` 和 `Runtime Role Contract` 的实际原因：回测只实现运行时需要的角色，数据准备和单元测试按各自的应用边界提供 gateway、runtime 或 fake。

## Composition 和 System

`composition` 不是 `system` 的同义词。

### Composition

Composition 负责组装对象图：

```text
读取模式配置
  -> 创建 Gateway
  -> 创建 Translator
  -> 创建 Runtime Role
  -> 创建 Clock
  -> 组装 RuntimeComponents
```

它不消费市场事件，也不执行策略。

### System

System 负责产品级生命周期和运行管理：

```text
创建 session
启动 runtime
处理 command channel
记录 launch 状态
停止 runtime
生成 launch result
```

当前代码里 `launch`、`system`、mode resource factory 已经共同承担了 composition 职责。目标不是立刻增加一套新的顶层系统，而是先在现有 launch 边界内把两个阶段分开：

```text
Mode Composition Factory
  -> RuntimeComponents
  -> TradingSystem / RuntimeKernel
```

建议的未来目录：

```text
kairospy/application/composition/
  live.py
  paper.py
  backtest.py
  common.py

kairospy/application/support/runtime/
  components.py
  kernel.py
  orchestration/

kairospy/application/support/system/
  facade/
  workspace/
  session/
```

短期可以继续让 composition 代码位于 `application/support/launch` 或 mode factory 中，只要不让 `TradingSystem` 同时负责具体 Connector 创建和 runtime 执行。

## Persistence 边界

Integration 不直接返回 records，也不直接写 DataStore：

```text
External API
  -> Gateway
  -> Translator
  -> Core Model / Domain Event
  -> Application Service
  -> Persistence Projector
  -> Record
  -> DataStore
```

`BarRecord`、`RateRecord`、`QuoteRecord` 等属于：

```text
infrastructure/persistence/market_data/
```

但不应重新放回 `core`，也不应作为 runtime 或 application contract 的默认返回值。

## 当前代码的改造方向

改造不以保留旧协议为目标。每个能力迁移完成后只保留目标协议。

推荐顺序：

1. 账户 runtime 和 catalog 分别实现目标角色。
2. 将 integration raw gateway 和 application runtime role 分开。
3. 让 `LiveAccountService` 接收 Gateway 和 Translator，不创建具体 adapter。
4. 让 composition factory 负责创建 Gateway、Translator 和 runtime role。
5. 让 `TradingSystem` 只接收已经组装好的 `RuntimeComponents`。
6. execution、market、reference 按相同规则迁移，并删除旧 aggregate。
7. records 统一放在 `infrastructure/persistence/market_data`。

## 判断标准

新增一个接口前，先回答：

1. 谁调用它？
2. 它表达的是外部 API、应用用例，还是 runtime 协作角色？
3. 它返回 raw payload 还是 core model？
4. 它是否只属于某一个运行模式？如果是，通常应该是实现，不应该是新协议。
5. 它是否包含账户目录、配置或 composition 信息？如果是，不应直接放进 runtime role。

最终目标不是减少所有接口数量，而是让每个接口只位于一个清晰的调用边界中。
