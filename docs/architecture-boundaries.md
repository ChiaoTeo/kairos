# KairosPy Architecture Boundaries

本文定义 KairosPy 的目标架构边界。它不是迁移备忘录，也不是所有历史目录的解释文档。读完本文后，新增能力应该能直接判断：

- 稳定业务模型放哪里；
- use case 和 runtime service 如何分工；
- 外部系统 connector、adapter、resolver 如何进入应用；
- 哪些抽象值得保留，哪些只是过度方案；
- 启动时由谁选择 concrete implementation。

细节文档：

- `docs/application-ports-and-domain-boundaries.md`
- `docs/integration-boundaries.md`
- `docs/integration-application-layer.md`
- `docs/runtime-composition-and-protocols.md`

若细节文档与本文冲突，以本文为准。

## 目标场景

目标架构只有一个核心事实：KairosPy 的业务语言在内侧，外部系统语言在 integration 边界，运行模式选择集中在 composition。

```text
surface / CLI / TUI / interactive
  -> application/support/system facade
  -> application/support/launch composition
  -> application/support/runtime services
  -> application/usecases
  -> core

infrastructure integrations
  -> application/usecases contracts only when implementing one
  -> core

infrastructure persistence
  -> application/usecases contracts only when implementing one
  -> core
```

禁止方向：

```text
core -> application
core -> infrastructure
application/usecases -> concrete connector / SDK / raw payload
runtime processors -> integration protocols
runtime dispatch/context -> external capability contracts
infrastructure connector -> runtime service / launch / surface / persistence store
surface/system facade -> concrete connector / driver
```

最终启动路径应类似：

```text
workspace/config/mode
  -> launch composition
  -> integration application service
  -> IntegrationResolver / IntegrationBinding / ConnectorFactory
  -> connector / driver / store implementation
  -> adapter implementing narrow application contract
  -> runtime service or use case
  -> RuntimeEnvelope / Result DTO / core model
```

读者应该优先按这个最终场景理解代码，不要先设计兼容层、过渡层或全局抽象。

## 核心分层

### Core

`kairospy/core/` 放稳定业务模型和领域规则。

适合：

- market：`MarketRef`、`MarketEvent`、`Quote`、`Bar`、`OrderBookSnapshot`
- account：`AccountBookRef`、`AccountSnapshot`、`PositionSnapshot`
- order/execution：`OrderRequest`、`OrderState`、`OrderEvent`
- reference：`ReferenceCatalog`、`MarketDefinition`、`InstrumentDefinition`

不适合：

- 第三方 SDK payload；
- JSONL / Parquet / CSV row schema；
- connector、driver、credential、workspace path；
- runtime subscription state、view cache、dispatch context。

判断句：

```text
如果一个类型离开 KairosPy runtime、离开某个交易所、离开某种存储格式后仍然有业务意义，才放 core。
```

### Application Use Cases

application 的业务目录按 KairosPy 的用例语言组织，而不是按 live/paper/backtest 或第三方 API 组织。

`application` 只有两个子域：`usecases` 和 `support`。业务能力只放 `usecases`，运行、启动、系统操作面只放 `support`。

目标业务 areas：

```text
kairospy/application/
  usecases/
    reference/
    market/
    account/
    execution/
    strategy/
```

support areas：

```text
kairospy/application/
  support/
    runtime/
    launch/
    system/
```

业务 area 承载 use case、业务 DTO 和必要的 local contract。runtime event store 的 query/view 归 `application/support/runtime`；CLI/TUI 列表浏览和分页形状归 `application/support/system`。support area 可以编排业务 area，但不拥有业务决策。

推荐 use case 形态：

```text
application/usecases/<area>/<business_action>.py
  Request / Command DTO
  Result DTO
  UseCase class or function
  local Protocol only when this use case owns the dependency
```

例子：

```text
application/usecases/reference/refresh.py
application/usecases/reference/source.py
application/usecases/reference/store.py

application/usecases/market/subscriptions.py
application/usecases/market/history.py
application/usecases/market/datasets.py
application/usecases/market/replay.py

application/usecases/account/bootstrap.py
application/usecases/account/reconciliation.py
application/usecases/account/routing.py

application/usecases/execution/live.py
application/usecases/execution/simulation.py

application/usecases/strategy/protocol.py
application/usecases/strategy/context.py
```

use case 可以：

- 编排 core model 和 domain service；
- 调用本 area 拥有的 local contract；
- 做 application-level 校验，例如缺配置、缺 reference、账户 capability 不匹配；
- 产出 result DTO、core event 或 runtime service 可消费对象。

use case 不应该：

- 直接 import concrete connector、driver、SDK client；
- 处理 raw SDK payload；
- 管理 runtime lifecycle、daemon、pump、stop file；
- 写 CLI/TUI 输出格式；
- 依赖 `surface` 或 `application/support/launch/composition`；
- 把数据库 row schema 当成业务模型。

### Runtime

`application/support/runtime/` 负责把业务能力接入持续运行的事件系统。runtime 不是业务 area。

```text
runtime services
  可以调用注入的外部能力，产生 RuntimeEnvelope。

runtime processors
  消费 RuntimeEnvelope，更新 view/state。
  不依赖外部 capability contract。

runtime orchestration
  调度 runtime components。
  不选择 concrete connector。

runtime dispatch/context
  暴露 strategy-safe API。
  不暴露 connector、store、raw payload。
```

允许：

```text
application/support/runtime/services/market
  -> local MarketFeed Protocol or adapter

application/support/runtime/services/execution
  -> local LiveOrderExecution Protocol or adapter

application/support/runtime/services/reference
  -> application/usecases/reference contract if it is a reference use case dependency
```

禁止：

```text
runtime/processors -> infrastructure.integrations.protocols
runtime/dispatch -> application/usecases/reference|market|account external contracts
runtime/contracts -> external capability contracts or integration protocols
```

### Launch Composition

`application/support/launch/composition/` 是 application 下唯一集中选择 concrete implementation 的地方。

职责：

- 根据 live / paper / backtest 选择 runtime service；
- 调用 `IntegrationResolver` 获取 connector、driver 或 adapter；
- 装配 persistence store；
- 把 concrete implementation 包装成 runtime service 或 use case 需要的窄 contract；
- 把资源注入 runtime host。

不属于 composition 以外的模块：

```text
application/support/runtime/services/*
application/support/runtime/orchestration/*
application/support/system/facade/*
surface/cli/*
```

这些模块不应直接 new connector、driver 或 persistence implementation。

### System Facade

`application/support/system/` 是用户操作面和 workspace 管理面。

拥有：

- workspace manifest 和 records；
- CLI/TUI/interactive facade；
- launch artifact projector；
- diagnostics；
- command dispatcher。

不拥有：

- connector selection；
- runtime service implementation；
- raw persistence record；
- 交易业务决策。

如果 system facade 需要外部资源，应委托 launch composition、query service 或明确的 workspace resource provider。

### Infrastructure Integrations

`kairospy/infrastructure/integrations/` 是外部系统接入 bounded context。它可以在内部采用 DDD 结构，但这个 domain 描述的是“如何接入外部系统”，不是 KairosPy 的交易业务领域。

它的职责是把 vendor API、SDK、payload、凭证、连接生命周期、限流和能力路由隔离在外侧，再输出 KairosPy 的 core model、event 或 use-case DTO。

目标结构：

```text
kairospy/infrastructure/integrations/
  domain/
    participants.py     # ParticipantRef, ParticipantRole
    capabilities.py     # Capability, ProductLine, EndpointProfile
    bindings.py         # IntegrationBinding
    sessions.py         # IntegrationSession, connection state
    health.py           # IntegrationHealth
    errors.py           # IntegrationError taxonomy
    policies.py         # rate limit / reconnect policy when shared

  services/
    resolver.py         # IntegrationResolver
    registry.py         # participant/capability registration
    factories.py        # connector/client factories
    credentials.py      # credential resolution boundary

  protocols.py          # raw/vendor protocols only, transitional
  drivers/
  payloads/
  connectors/
    exchange/<exchange>/
    broker/<broker>/
    provider/<provider>/
  adapters/
```

角色定义：

```text
exchange
  交易场所公开能力：market data、reference、rules、trading status。
  不放账户、余额、下单、私有流。

broker
  账户、托管、下单、撤单、私有流，以及 broker product-specific 行情。

provider
  非交易执行的数据供应：历史行情、reference universe、corporate actions、fundamentals。

driver
  SDK / HTTP / websocket / signing / retry / pagination 技术封装。

payloads
  raw payload -> core model / adapter DTO。

adapter
  integration 到 application/core 的 anti-corruption boundary。
```

DDD 归属：

```text
Value Object
  ParticipantRef, Capability, ProductLine, EndpointProfile, CredentialScope

Entity
  IntegrationSession, ManagedConnection, ListenKeySession

Aggregate
  ParticipantCapability
    participant + role + product line + capability + endpoint profile。

Domain Service
  CapabilityRouter, CredentialScopePolicy, RateLimitPolicy, ReconnectPolicy。

Integration Application Service
  IntegrationResolver, IntegrationRegistry, IntegrationFactory。
```

判断句：

```text
如果规则描述“如何和外部系统正确通信、选择能力、管理连接”，它属于 integrations/domain 或 integrations/services。
如果规则描述“KairosPy 如何理解交易、账户、市场、策略”，它属于 core 或 application use case。
```

connector 可以使用 vendor 语言，例如 `fetch_ohlcv`、`watch_ticker`、`create_order`。adapter 和 application contract 应使用 KairosPy 业务语言，例如 `history`、`quotes`、`submit`、`snapshot`。

禁止：

```text
connector -> application/support/runtime/services
connector -> application/support/launch
connector -> surface
connector -> persistence store
driver -> application
payload translator -> application/support/runtime
integrations/domain -> application
integrations/domain -> drivers / connectors / payloads
```

connector 不写 storage。写入 dataset、reference store 或 artifact store 是 application use case、runtime service 或 projector 的职责；persistence 负责具体落盘。

### Infrastructure Persistence

`kairospy/infrastructure/persistence/` 放本地存储实现、records、codecs 和 projectors。

适合：

- SQLite reference store；
- market dataset store；
- JSONL / Parquet rows；
- record projector；
- artifact store implementation。

存储 contract 若存在，应贴近 owning use case：

```text
application/usecases/reference/store.py
application/usecases/market/datasets.py
```

具体实现属于 persistence，不属于 integrations。

## Contract 规则

### 默认规则

不要把所有外部能力都提升成全局 application contract。默认顺序是：

```text
1. 能直接在 composition 注入 adapter，就直接注入。
2. 只有一个 runtime service 使用的能力，把 Protocol 放在该 service 附近。
3. 一个业务 area 内多个 use case 共享的能力，放到 application/usecases/<area>/。
4. 跨 area 共享时，先检查是否其实属于 core、runtime contract 或 composition 编排。
5. 仍然确有稳定跨 area 价值时，才允许创建清晰命名的 application-level contract。
```

这意味着 `application/ports` 不再是目标层，也不应重新引入。所有 contract 必须放在 owning application area、runtime service 附近，或由 composition 直接注入 adapter。

### 值得抽象

值得保留 contract 的情况：

- 一个能力被多个 application use case 共享；
- 需要替换实现以支持 live / paper / backtest；
- 需要隔离 persistence implementation；
- 测试 fake 表达的是业务能力，而不是 SDK payload；
- 方法名和 DTO 使用 KairosPy 业务语言。

不值得抽象的情况：

- 只是把 connector 再包一层；
- 只有 live runtime 会调用；
- 只有一个 concrete implementation；
- protocol 方法仍是 `watch_ticker`、`fetch_balance`、`create_order` 等 vendor 形状；
- 调用方和实现方总是一起变化。

判断句：

```text
如果删除这个抽象后，composition 仍能注入实现，测试仍能注入 fake，并且没有跨用例重复依赖，那么这个抽象就不该存在。
```

### 当前目标归属

```text
MarketDataSubscriptionSpec / DataSubscription
  -> application/usecases/market/subscriptions.py

ReferenceStore / ReferenceCatalogSource
  -> application/usecases/reference/store.py
  -> application/usecases/reference/source.py

MarketDatasetStore / HistoricalMarketDataPort
  -> application/usecases/market/datasets.py
  -> application/usecases/market/history.py

MarketStreamGateway
  -> local Protocol in application/support/runtime/services/market
     or direct injected adapter

OrderExecutionPort
  -> local Protocol in application/support/runtime/services/execution
     or application/usecases/execution/live.py if used outside runtime
```

## Area 归属

### Reference

`application/usecases/reference/` 负责 KairosPy 如何获取、刷新、校验、保存和构建 reference catalog。

拥有：

- refresh reference catalog；
- reference source/store contract；
- lifecycle transition；
- universe construction / filtering；
- market/instrument registration application action。

不拥有：

- exchange raw reference API；
- `MarketDefinition`、`InstrumentDefinition` 等稳定模型；
- SQLite row schema；
- CLI 输出 payload。

### Market

`application/usecases/market/` 负责行情订阅、历史行情、dataset、replay 和 market data use case。

拥有：

- subscription spec / subscription planning；
- market selector planning；
- historical data loading contract；
- market dataset store contract；
- replay planning；
- missing data download policy if it is application behavior。

不拥有：

- live websocket loop；
- raw `watch_ticker` / `fetch_ohlcv`；
- `Bar`、`Quote`、`TradePrint` 等稳定模型；
- Parquet/JSONL record schema。

多交易所行情按 subscription 的 `MarketRef` 路由：

```text
subscription spec
  -> composition feed resolver
  -> IntegrationResolver.market_feed_for_subscription(...)
  -> concrete connector
  -> MarketStreamAdapter
  -> StreamingMarketDataService
```

不要在启动时按账户 venue 固定唯一 feed。

### Account

`application/usecases/account/` 负责账户状态、快照、reconciliation、baseline 和 account book identity 的 application 决策。

拥有：

- account bootstrap；
- account snapshot gateway if shared；
- account reconciliation；
- account baseline；
- account book identity validation；
- account capability validation。

不拥有：

- broker credential lookup implementation；
- raw `fetch_balance` payload parsing；
- live private stream loop；
- simulated fill model；
- `AccountSnapshot`、`PositionSnapshot`、`AccountBookRef` 等稳定模型。

### Execution

`application/usecases/execution/` 负责订单执行 use case、模拟执行、执行路由、提交/撤单 contract 和执行结果 DTO。

拥有：

- submit/cancel use case when outside runtime；
- live execution contract if shared；
- execution simulation；
- order/intent -> account/broker routing；
- slippage / commission application policy when not core invariant；
- execution query/result DTO。

不拥有：

- broker raw `create_order` / `cancel_order`；
- runtime execution pump；
- `OrderRequest`、`OrderState`、`OrderEvent` 等稳定模型；
- matching engine invariant if it becomes core domain rule。

### Strategy

`application/usecases/strategy/` 是用户策略面对 KairosPy 的 application API。

拥有：

- strategy protocol；
- strategy entrypoint loading；
- runtime-safe context helper；
- strategy view readers；
- strategy command/event boundary。

不拥有：

- connector；
- persistence；
- launch config parsing；
- order execution implementation；
- market subscription planning except strategy request DTO。

## 新能力决策表

| 新增内容 | 位置 |
| --- | --- |
| 稳定业务模型 / 不变量 | `kairospy/core/` |
| reference use case / contract | `kairospy/application/usecases/reference/` |
| market use case / contract | `kairospy/application/usecases/market/` |
| account use case / contract | `kairospy/application/usecases/account/` |
| execution use case / contract | `kairospy/application/usecases/execution/` |
| strategy-facing API/context/event | `kairospy/application/usecases/strategy/` |
| runtime envelope / runtime line / runtime query / runtime read model | `kairospy/application/support/runtime/events.py`, `kairospy/application/support/runtime/lines.py`, `kairospy/application/support/runtime/query`, `kairospy/application/support/runtime/views` |
| launch/run mode | `kairospy/application/support/launch/modes.py` |
| system list browsing/pagination | `kairospy/application/support/system/browsing`, `kairospy/application/support/system/pagination.py` |
| 只被一个 runtime service 使用的外部能力 | service-local Protocol 或 direct injected adapter |
| 一个业务 area 共享的外部能力 | owning `application/usecases/<area>/` |
| runtime role contract | `kairospy/application/support/runtime/contracts.py` |
| runtime service | `kairospy/application/support/runtime/services/` |
| runtime processor | `kairospy/application/support/runtime/processors/` |
| exchange public market data/reference | `infrastructure/integrations/connectors/exchange/<exchange>/` |
| broker account/execution/private stream | `infrastructure/integrations/connectors/broker/<broker>/` |
| provider data capability | `infrastructure/integrations/connectors/provider/<provider>/` |
| third-party SDK wrapper | `infrastructure/integrations/drivers/` |
| raw payload translator | `infrastructure/integrations/payloads/` |
| integration-to-application adapter | `infrastructure/integrations/adapters/` |
| raw integration protocol | `infrastructure/integrations/protocols.py` only when connector-local Protocol is not enough |
| participant/factory lookup | `infrastructure/integrations/services/registry.py` and `services/resolver.py` |
| SQLite / file / dataset implementation | `infrastructure/persistence/` |
| live / paper / backtest assembly | `application/support/launch/composition/` |
| user-facing operation helper | `application/support/system/facade/` or `surface/cli/` |

## 迁移策略

本文建议的是切实可行的收敛方案：按 capability 切片迁移，而不是一次性重排所有目录，也不引入长期兼容层。

允许：

- 短期本地 helper；
- 同一 change 内更新调用方、实现、测试和 `__all__`；
- 切片完成后删除旧入口。

不允许：

- 长期兼容 re-export；
- 同一 capability 同时保留两个 application-facing contracts；
- 重新引入 `application/ports`；
- fallback 同时支持新旧 protocol。

推荐顺序：

```text
1. Keep `application/ports` deleted and architecture-tested.
2. Keep `application/domain` deleted and architecture-tested.
3. Move account resource assembly from application/support/system/resources into launch/composition.
4. Continue extracting integration DDD domain/services.
5. Add architecture tests for each completed boundary.
```

每个切片完成标准：

```text
all imports point to the target location
tests are updated
old module entrypoint is deleted
no compatibility alias remains
architecture test covers the boundary
```

## Boundary Tests

架构规则应该由测试约束，而不是只写在文档里。

推荐断言：

- `core` does not import `application` or `infrastructure`.
- `application/ports` does not exist.
- code does not import `application/ports`.
- `runtime/processors` does not import external capability contracts.
- `runtime/dispatch` and `runtime/contracts` do not import external capability contracts.
- `application/support/runtime/services` does not import `infrastructure.integrations.protocols`.
- application/usecases do not import concrete connector modules.
- `infrastructure/integrations/domain` does not import `application`, `surface`, `persistence`, `connectors`, `drivers`, `payloads`, or `adapters`.
- `infrastructure/integrations/connectors` do not import `application/support/runtime`, `application/support/launch`, `surface`, or `infrastructure/persistence`.
- `infrastructure/integrations/drivers` do not import `application`.
- `infrastructure/integrations/payloads` do not import `application/support/runtime` or `surface`.
- `infrastructure/integrations/protocols.py` does not define broad aggregates such as `BrokerClient`, `ExchangeProtocol`, or `ProviderProtocol`.
- integration adapters may import use-case contracts; connectors should not.
- vendor exception types do not cross adapter/use-case boundaries.
- `surface` and `system/facade` do not directly import concrete connector modules, driver adapters, or persistence implementations.
- `launch/config` does not import concrete infrastructure connectors or persistence implementations.
- `launch/composition` is the only application package allowed to choose concrete infrastructure implementations.
