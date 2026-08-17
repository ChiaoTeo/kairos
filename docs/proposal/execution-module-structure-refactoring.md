# Execution 模块边界显式化重构提案

## 1. 文档状态

- 状态：实施中（application capability traits 已删除；Process/control 与 composition 主要混合职责已拆分，正在收敛 Application/Services/Domain 与公开 API）
- 范围：`crates/modules/execution` 主 crate
- 不包含：Execution contract wire format 重设计、Integration provider capability 重设计、业务语义重写
- 主要目标：在不改变业务行为和状态所有权的前提下，将 Execution 整理为职责清晰、目录完整、依赖方向稳定的业务模块

## 2. 背景

Execution 已经具备仓库规定的四个主要层次：

```text
bin -> composition -> application -> services
                         \-> domain
```

当前问题不是缺少分层，而是若干文件聚合了过多不同职责：

- `application/process.rs` 同时处理进程生命周期、控制传输、命令分发、gateway、stream 恢复、readiness、simulation 和 publication；
- `composition/mod.rs` 同时处理 route 模型、多个 provider 的构造、同步兼容路径、writer fencing、store 和测试；
- `application/service.rs` 同时承担应用构造、连接管理、恢复、查询、事件和 outbox；
- application 内的 `service` 与 crate 私有层 `services` 仅靠单复数区分，语义容易混淆；
- `lib.rs` 同时导出 application、composition、domain 和部分 services 具体实现，推荐入口不够明确；
- 多个职责以 application 或 services 下的同层裸文件存在，难以继续细分而不让目录变成文件清单。

本次重构的目标不是机械缩短文件，也不是创建新的 facade、manager 或通用 adapter，而是让已有业务边界在代码结构中直接可见。

## 3. 架构判断

### 3.1 Execution 的业务所有权

Execution 拥有：

- strategy execution intent、execution plan 和 leg；
- exchange-facing order lifecycle；
- client order ID、route 选择结果与 reconciliation；
- order commitment；
- Execution 侧的 risk reservation saga 证据；
- unknown remote order；
- execution audit；
- route readiness 对 Execution 业务可用性的影响。

Execution 不拥有：

- Account 所有的余额、仓位、权益、freshness 和账户侧订单事实；
- Risk 所有的预算、reservation 和风险决策权威状态；
- Market 所有的行情、order book、subscription 和 market freshness；
- Reference 所有的 canonical identity、catalog 和 execution access 生命周期；
- Integration 所有的 provider connection、认证、签名、技术 quota、外部 payload 和 normalizer；
- Workspace/System 所有的路径、实例资源、全局进程生命周期和启动协调。

Account 的 live snapshot、order observation 和 fill observation 只从 Account 自己组装的 Integration account capability 进入。Execution 的 exchange-facing order lifecycle 可以与 Account 观察到的事实相关联，但不能把自己的 order/fill 再写回 Account，否则会产生两个事实源、重复成交和不确定的到达顺序。

唯一例外是 simulated/paper execution：它没有真实交易所 Account stream，因此可以向 Account 提交明确命名、仅模拟模式可用且幂等的 settlement command。这个例外不是通用的 `ExecutionAccountFacts`，也不包含 live order observation。

### 3.2 状态所有权

`ExecutionActor` 继续作为 Execution 可变业务状态的唯一所有者。目录拆分不能产生 `OrderActor`、`IntentActor` 或第二个进程级状态副本。

`ExecutionApplication` 是唯一公开业务 facade。订单、intent、reconciliation 和 query 可以分别组织实现，但不能演变成多个 application facade。

`ExecutionProcess` 是围绕 `ExecutionApplication` 的可复用 runtime facade。它可以拥有生命周期、health、readiness、event draining、recovery barrier 和 shutdown 行为，但不应拥有 transport wire parsing、provider 构造或业务状态。

### 3.3 依赖方向

目标依赖方向为：

```text
execution bin
    -> execution composition
        -> execution application
        -> execution services
        -> integration application capabilities
        -> account/risk/market/reference contracts

execution application
    -> execution services
    -> execution domain
    -> another module's application API/contract (when semantic coupling is intentional)

execution services
    -> execution domain

execution domain
    -> primitives only
```

禁止形成以下依赖：

```text
application -> composition
domain -> composition/services/其他业务模块/Integration
Execution production code -> Integration services、SDK 或 raw provider payload
其他业务模块 -> Execution services 或私有文件
process -> provider-specific constructor
```

## 4. 结构原则

### 4.1 完整目录模块

凡是已经包含多个职责、需要继续细分或具有稳定边界的概念，一律使用目录模块：

```text
application/process/mod.rs
application/process/recovery.rs
```

不采用：

```text
application/process.rs
application/process_recovery.rs
```

`mod.rs` 的职责是定义模块边界、核心类型、可见性和导出。它不应继续承载大量具体实现。

叶子实现仍然需要 Rust 源文件。“不做裸文件”指不在层级根目录持续堆放本应属于某个完整职责模块的并列文件，不要求为只有一个短小实现且没有子结构的概念制造空目录。

### 4.2 按真实职责创建目录

目标结构是边界设计，不是一次性创建空目录的清单。只有满足以下条件之一时才创建子模块：

1. 已有代码可以迁入；
2. 当前存在两个以上可区分的实现职责；
3. 存在明确调用者和稳定边界；
4. 拆分后能够删除原有混合职责或旧路径。

禁止为了目录对称创建空模块、占位 trait、manager、registry、coordinator 或 compatibility facade。

### 4.3 `application/service` 命名处理

不保留 `application/service` 与 `services` 的单复数并存。

- `application` 表示公开业务用例、请求、结果和编排；
- `services` 表示支撑 application 的私有 actor、worker、persistence、transport 和 simulation 实现。

`ExecutionApplication` 定义在 `application/mod.rs` 或 application 内部专用定义文件中，各用例目录共同为该类型提供 `impl`。不再使用含义模糊的 `application/service` 目录。

## 5. 目标结构

下面是最终边界。子文件只在迁移到相应职责时创建，不预先创建空文件。

```text
crates/modules/execution/
  contract/
  migrations/
  src/
    lib.rs

    bin/
      kairos-execution-server.rs
      kairos-execution-cli.rs

    application/
      mod.rs

      model/
        mod.rs
        command.rs
        query.rs
        result.rs
        event.rs
        snapshot.rs
        error.rs

      use_cases/
        mod.rs
        orders/
          mod.rs
          admission/
            mod.rs
            policy.rs
            facts.rs
          submission.rs
          cancellation.rs
          replacement.rs
          fills.rs
        intents/
          mod.rs
          planning/
            mod.rs
            policy.rs
            facts.rs
          submission.rs
          lifecycle.rs
          planning.rs
        reconciliation/
          mod.rs
          remote_orders.rs
          unknown_orders.rs
          recovery.rs
        queries/
          mod.rs

      process/
        mod.rs
        lifecycle.rs
        ingress.rs
        gateways.rs
        streams.rs
        readiness.rs
        recovery.rs
        publication.rs

      backtest/
        mod.rs
        market.rs
        model.rs
        evaluation.rs
        metrics.rs

    composition/
      mod.rs

      connections/
        mod.rs
        model.rs
        entry.rs
        query.rs
        events.rs
        routes.rs
        writer_fence.rs

      dependencies/
        mod.rs

      providers/
        mod.rs
        binance/
          mod.rs
          spot.rs
          futures.rs
          margin.rs
          options.rs
        okx/
          mod.rs
          trading.rs
        ibkr/
          mod.rs
          trading.rs

      persistence/
        mod.rs
        memory.rs
        file.rs
        sqlx.rs

    services/
      mod.rs

      dependencies/
        mod.rs
        access/
        planning/
        order_admission/
        projection/
        workers/

      actor/
        mod.rs
        orders.rs
        intents.rs
        fills.rs
        reconciliation.rs
        snapshot.rs

      gateway/
        mod.rs
        entry.rs
        query.rs
        events.rs

      routing/
        mod.rs
        route.rs
        validation.rs

      persistence/
        mod.rs
        state.rs
        outbox.rs
        audit.rs

      audit/
        mod.rs
        memory.rs
        sqlx.rs

      risk/
        mod.rs
        adapter.rs
        worker.rs

      publication/
        mod.rs
        events.rs
        snapshots.rs
        encoding.rs

      control/
        mod.rs
        transport.rs
        ingress.rs
        wire.rs

      simulation/
        mod.rs
        model.rs
        matching.rs
        settlement.rs

    domain/
      mod.rs

      order/
        mod.rs
        entity.rs
        fill.rs
        commitment.rs
        reservation.rs

      intent/
        mod.rs
        entity.rs
        leg.rs
        policy.rs
        planning.rs

  tests/
    architecture.rs
    application/
    process/
    composition/
    behavior/
```

## 6. 各层职责

### 6.1 `bin`

允许：

- 解析 CLI 参数和环境配置；
- 解析 workspace 路径和实例资源；
- 调用 composition；
- 启动 `ExecutionProcess`；
- 将终端输入输出映射到 application request/result。

禁止：

- 定义可复用业务行为；
- 直接修改 Actor 状态；
- 复制 application use case；
- 直接构造 provider SDK client；
- 成为第二个 application facade。

### 6.2 `composition`

负责具体选择和组装：

- provider-native Integration connection/capability；
- route、principal、account、binding 的业务组合；
- Account、Risk、Market、Reference contract adapter；
- persistence、publisher 和 worker 实现；
- live、simulated、paper 等运行模式；
- `ExecutionApplication` 和 `ExecutionProcess` 的最终构造。

Provider 子模块只能组合 Integration application 暴露的具体 capability，不复制签名、协议交互、payload normalizer 或 provider quota 实现。

### 6.3 `application`

负责公开业务边界：

- command、query、result、event 和 error；
- order 和 intent use case；
- reconciliation 与 recovery 编排；
- process runtime facade；
- 对 Actor、持久化、外部 capability 和 publisher 的调用顺序。

Application API 不暴露：

- SDK client；
- raw provider payload；
- persistence record；
- composition config；
- services 实例；
- `serde_json::Value` 形式的业务模型。

### 6.4 `services`

负责私有实现：

- Actor 和状态转换；
- bounded gateway workers；
- routing 执行与内部校验；
- persistence/outbox/audit 接口与内部机制；
- control transport 和 wire adaptation；
- simulation matching 与 settlement。

Services 不得成为其他模块、CLI 或 server 的直接业务入口。

### 6.5 `domain`

负责实体、值对象和业务不变量：

- order、fill、status transition；
- intent、plan、leg 和 policy；
- commitment；
- Execution 侧 reservation evidence；
- 纯业务验证和计算。

Domain 不依赖 transport、persistence、Integration、其他业务模块或 application facade。

## 7. 关键设计决定

### 7.1 保留单一 `ExecutionApplication`

订单、intent、查询和 reconciliation 按目录拆分，但共同实现同一个 `ExecutionApplication`。不增加 `OrderApplication`、`IntentApplication` 或 `ExecutionManager`。

### 7.2 保留单一 `ExecutionActor`

`services/actor/` 的子文件只是同一个 Actor 的状态转换实现。订单与 intent 状态需要一致性，不能为了文件边界拆成多个状态 owner。

### 7.3 Process 与 transport 分离

`application/process/` 保留：

- lifecycle；
- main state loop；
- readiness；
- stream recovery barrier；
- gateway 驱动；
- snapshot/event draining；
- shutdown。

`services/control/` 承担：

- UDS/Axum transport；
- JSON/control wire parsing；
- request envelope；
- transport response encoding；
- ingress classification。

Process 接收类型化的 control operation，不直接理解 HTTP/JSON wire 结构。

当前实现中 `ControlOperation` 与 `ControlResponse` 均由 `services/control/` 所有；
`application/process/ingress` 只分发 typed operation 并选择 typed response，不再构造
`serde_json::Value` 或 JSON object。JSON response shape 集中在
`services/control/response.rs`。

### 7.4 Provider 只存在于 composition 和 Integration

`composition/providers/<provider>/` 选择 Integration capability 并完成 Execution route 组装。Application、services 和 domain 不出现 Binance、OKX、IBKR 的具体连接类型。

现有异构 capability enum 在有真实调用者时可以作为迁移边界保留在 `composition/connections/`，但不能继续扩展成通用 provider adapter。对应 Integration slice 完成后，应评估并删除不再需要的 dispatch。

### 7.5 Simulation 与 backtest 分层

- `services/simulation/` 是 Execution 自己的模拟执行模型；
- `services/simulation/account_settlement.rs` 是唯一允许写入 Account 的 Execution 服务；它只由 simulated composition 构造，只提交 fill settlement，不发布 Account order observation；
- `application/backtest/` 是面向调用者的离线业务用例；
- `application/process/` 只负责运行时驱动，不拥有模拟订单状态；
- simulation/backtest 不伪装成 Integration provider connection。

Account 端只暴露 `/v1/simulation/settlements` 作为该例外的 control command，并默认关闭；只有 provider 为 `paper` 或 `simulated` 的 Account composition 才显式开启。原 `/v1/order-event`、`/v1/fill` 与 `/v1/simulated-fill` 不作为兼容接口保留。

### 7.6 Persistence 接口与具体实现分离

- application/services 使用 Execution-owned state store、outbox 和 audit capability；
- SQLx、file、memory 等具体选择由 composition 暴露和组装；
- persistence record 不进入 application API；
- snapshot 和 event publisher 直接映射 application/domain 类型到 contract 类型，不经过 JSON 模型 round trip。

### 7.7 Application 层级与端口审计

`orders`、`intents`、`reconciliation`、`queries` 都是同一个 `ExecutionApplication` 的业务用例实现，不再与 `core`、`model`、`capabilities`、`process` 平铺；统一归入 `application/use_cases/`。`use_cases` 只组织实现，不定义第二个 facade 或状态 owner。

原 `application/market_input/` 的类型只被 backtest、paper simulation 和 replay control 使用，不是通用 Execution application 输入。它迁入 `application/backtest/market.rs`，由 backtest 模块有选择地导出。Live planning/admission 不得复用这个 replay DTO；composition 从 Market typed view 读取后使用自己的私有投影记录。

项目横向审计表明，Application 并不需要把所有依赖都包成 trait。Account、Risk、Reference 的 application 都直接依赖本模块 domain 和私有 services；Execution 也已直接依赖 Integration application capability。DDD 下的默认规则应为：

1. Application 直接编排用例，并可直接实现属于本限界上下文的业务规则；
2. 纯业务计算下沉 domain policy/value object，需要状态、交易顺序或 saga 编排的规则留在 application use case；
3. 本模块的 Actor、store 和私有 service 可由 application 直接使用，不为了 DI 统一性再复制一层 trait；
4. 跨模块依赖优先直接使用对方的 application API 或独立 contract，不复制对方 API；
5. Trait 依赖只能来自已经形成并整合的下层模块或平台能力，且由该 owner 定义稳定的多态边界；Application 不得在上层复制一个依赖反转 trait。

因此，`application/capabilities/` 不作为四个 trait 的长期归宿。“capability”这个名字没有说明依赖方向，也把业务规则和外部 I/O 混在一起。收敛后删除该目录，且不新建 `ports/` 目录。Application 直接使用具体的本模块 service 或其他模块 application/contract。

### 7.8 Trait 去过度抽象审计

| Trait | 当前调用者/实现证据 | 结论 |
| --- | --- | --- |
| `ExecutionIntentPlanner` | 规则原位于 dependency planning adapter；worker/socket 只是包装同一实现 | **已删除 trait**。具体 typed-facts reader/worker 暂收于 `services/dependencies/`，intent 规则继续收敛到 `application/use_cases/intents/planning/`与 domain policy |
| `ExecutionOrderAdmission` | quantity/price、Reference 规则、commitment 计算原位于 dependency adapter | **已删除 trait**。纯 admission policy 已迁入 `application/use_cases/orders/admission/`；具体 typed-facts reader/worker 位于 `services/dependencies/` |
| `ExecutionRiskReservations` | Application 持久化 pending evidence 后调用 Risk command；当前 trait 只有 socket 实现及其 queued decorator | **删除**。将映射、delivery certainty 和 typed-view 恢复收入具体 `services/risk/` 模块，Application 直接依赖该 service；service 内部直接使用 `kairos-risk-contract` |
| `ExecutionAccountFacts` | Account 已直接消费 Integration 的 live snapshot/order/fill；Execution 再发布会形成第二事实源。只有 simulated/paper 没有交易所 Account stream | **删除且不替换为通用 port**。删除 live `publish_order_event`/`publish_fill` 链路；仅保留 `services/simulation/account_settlement/` 的具体模拟结算服务，由 simulated composition 构造，并调用 Account 明确的、模式受限的幂等模拟结算 command |
| `ExecutionStateStore` | 由 `services/persistence` 下层模块拥有；file、memory、SQLx 三个生产实现 | **保留**。符合“下层 owner 已形成稳定多态边界” |
| `ExecutionAuditSink` | memory、SQLx 是两个明确的 audit 持久化模式 | **已删除 trait**。SQLx/Memory 实现已迁入 `services/audit/`，由具体 `ExecutionAudit` 服务组合；Process 直接依赖该下层服务 |
| snapshot/event publisher traits | 三个 trait 由 Process/Application 定义，只有一组 mmap/Aeron 生产发布实现 | **已删除**。具体发布已迁入 `services/publication/`，Process 直接持有；测试使用该 service 的内存事件模式，没有聚合成新 port |

实现数量不能证明抽象必要：queued worker、socket wrapper 和测试替身可能只是同一依赖的 decorator。必须从业务 owner、依赖反转、交付语义和当前调用者证明 trait；测试方便不能单独作为理由。

## 8. 现有文件迁移映射

| 当前路径 | 目标模块 | 说明 |
| --- | --- | --- |
| `application/model.rs` | `application/model/` | 按 command/query/result/event/snapshot/error 拆分 |
| `application/service.rs` | `application/` 下的 orders/intents/reconciliation/queries | 删除含糊的 application service 命名 |
| `application/service/order_use_cases.rs` | `application/use_cases/orders/` | 继续实现同一个 `ExecutionApplication` |
| `application/service/intent_use_cases.rs` | `application/use_cases/intents/` | 继续实现同一个 `ExecutionApplication` |
| `application/process.rs` | `application/process/` | lifecycle、streams、recovery、publication 等按职责迁移 |
| `application/backtest.rs` | `application/backtest/` | 分离请求结果、评估和指标计算 |
| `application/intent_planning.rs` | `application/use_cases/intents/planning/` | 删除 planner trait，业务规则回归 Execution application/domain |
| `application/order_admission.rs` | `application/use_cases/orders/admission/` | 删除 admission trait，只保留 typed fact 输入边界 |
| `application/risk_reservations.rs` | `services/risk/` | 删除 application trait，使用直接依赖 Risk contract 的具体 service |
| `application/account_facts.rs` | 删除；模拟例外迁入 `services/simulation/account_settlement/` | live Account facts 由 Account 的 Integration ingress 独占；Execution 只在模拟模式提交 settlement command |
| `composition/mod.rs` provider 构造部分 | `composition/providers/<provider>/` | provider-by-provider 迁移 |
| `composition/mod.rs` capability/route 部分 | `composition/connections/` | 保留 Execution-owned route 语义 |
| `composition/dependencies*` | `services/dependencies/` | 具体 typed-view reader、projection cache 和 bounded worker 为私有服务；composition 只配置并构造 |
| `composition/v2_publishers.rs` | `services/publication/` | 按 event/view publisher 拆分，Process 直接依赖具体 service |
| `services/actor.rs` | `services/actor/` | 保持单一 Actor |
| `services/gateway.rs` | `services/gateway/` | command/query/event worker 分离 |
| `services/routing.rs` | `services/routing/` | route 模型和校验分离 |
| `services/control_transport.rs` | `services/control/` | transport、wire 和 ingress 分离 |
| `services/persistence.rs` | `services/persistence/` | state/outbox/audit 边界 |
| `services/sqlx_persistence.rs` | `composition/persistence/sqlx.rs` | 具体实现由 composition 选择；必要私有机制留在 services |
| `services/sqlx_audit.rs` | `composition/persistence/sqlx.rs` 或独立 audit 实现 | 最终位置以其调用边界为准，不暴露 services 类型 |
| `services/simulator.rs` | `services/simulation/` | model/matching/settlement 分离 |
| `domain/order.rs` | `domain/order/` | order/fill/commitment/reservation |
| `domain/plan.rs` | `domain/intent/` | intent/leg/policy/planning |

### 8.1 `dependency_projection` 的定位结论

原路径 `composition/dependency_projection.rs` 不应继续作为 composition 根目录裸文件，也不应并入 application 或 services：

- 它读取 Account、Risk、Market、Reference 的跨进程快照与 health；
- 它维护的是 composition adapter 的刷新缓存，不是 Execution 订单或 intent 业务状态；
- 它服务于 planning、order admission 等具体依赖适配器；
- 其线程生命周期随这些具体 adapter 的组装而建立和释放。

为避免删除 application trait 后形成 `application -> composition`，具体 reader、projection cache 和 bounded worker 的目标路径调整为：

```text
services/
  dependencies/
    mod.rs                 私有具体 dependency services
    access/mod.rs          manifest、socket、snapshot 资源解析与统一访问
    projection/mod.rs      跨进程只读投影及刷新 runtime
    account/mod.rs         Account 只读 fact adapter；不得反向发布 live facts
    planning/mod.rs        intent planning adapter
    order_admission/mod.rs order admission adapter
    admission/mod.rs       admission 纯校验与换算
    risk/                  Risk reservation adapter 与 worker
    workers/mod.rs         planning/admission bounded workers
```

这里的 `projection` 只能缓存外部只读事实及其 freshness/watermark，不得成为订单、intent、reservation 或 route 的第二状态所有者。Market 投影如果目前只有 readiness 占位信息，应保留清晰的迁移说明，待真实 Market contract projection 接入后替换，不能把占位 generation 当成权威业务事实。

## 9. 公开 API 收敛

目标 `lib.rs` 应表达推荐入口，而不是重导出内部目录的所有类型：

```rust
pub mod application;
pub mod composition;

mod domain;
mod services;

pub use application::{
    ExecutionApplication,
    ExecutionError,
    ExecutionProcess,
    // 经过审查的业务 request/result/event 类型
};
```

约束：

- 其他业务模块只依赖 Execution application API 或独立 contract；
- `domain` 默认私有，确实属于应用请求或结果的类型由 application 有选择地导出；
- `services` 保持私有；
- `composition` 因 package binary 需要可以公开，但只暴露顶层 compose request/result；
- provider enum、worker、store 具体类型和内部 adapter 默认不从 crate root 导出。

收紧可见性前必须搜索当前调用者。不得通过临时兼容 re-export 长期保留旧入口；调用者迁移完成后立即删除旧路径。

## 10. 分阶段迁移计划

### Phase 0：基线和架构保护

1. 记录当前公开 API 和调用者；
2. 记录 `ExecutionApplication`、`ExecutionActor` 和 `ExecutionProcess` 的字段及状态职责；
3. 增强 architecture tests；
4. 运行完整基线检查；
5. 将发现的既有失败与本次改动区分记录。

新增或强化的架构检查至少覆盖：

- `ExecutionActor` 是唯一订单和 intent 可变状态 owner；
- application 不导入 composition；
- domain 不导入 Integration、contract、transport、persistence 或其他业务模块；
- provider-specific 类型不进入 application/domain；
- bin 不实现 application use case；
- process 不构造 provider connection；
- business publisher 不使用 `serde_json::to_value/from_value` 作为 typed model adapter；
- 其他 crate 不导入 Execution services 或私有文件。

### Phase 1：Application model 和 capability 模块化

1. 将 `application/model.rs` 迁入 `application/model/`；
2. 先保持行为地将现有 trait 收入完整目录模块，作为过渡态；
3. 立即按 7.8 的审计结论迁移规则与 I/O，删除 `ExecutionIntentPlanner`、`ExecutionOrderAdmission`、`ExecutionAccountFacts`；
4. 删除 `ExecutionRiskReservations`，将具体行为收入 `services/risk/`；
5. 删除过渡态 `application/capabilities/` 目录，不保留转发模块。

第 2 步只是为了降低文件迁移风险，不是目标架构；Phase 1 只有在第 3–5 步完成后才算结束。

### Phase 2：Application use case 模块化

1. 将订单用例迁入 `application/use_cases/orders/`；
2. 将 intent 用例迁入 `application/use_cases/intents/`；
3. 将 remote reconciliation、unknown order 和 recovery 迁入 `application/use_cases/reconciliation/`；
4. 将只读查询迁入 `application/use_cases/queries/`；
5. 删除 `application/service` 命名；
6. 保持单一 `ExecutionApplication`。

本阶段不改变 Actor、persistence 或 external capability 语义。

### Phase 3：Process 模块化与 transport 分离

1. 创建 `application/process/`；
2. 先迁移 readiness 和 recovery 纯逻辑；
3. 再迁移 gateway/stream 驱动；
4. 迁移 publication；
5. 将 UDS/Axum/JSON wire 行为移至 `services/control/`；
6. 让 `ExecutionProcess::run` 仅组装和驱动这些私有过程；
7. 删除旧 `application/process.rs`。

本阶段风险最高，应单独提交，并重点验证 reconnect、resync barrier、required/optional route readiness 和 shutdown。

### Phase 4：Composition 模块化

1. 先抽取 connection/route model；
2. 按 Binance、OKX、IBKR 顺序迁移 provider composition；
3. 由 composition 配置 `services/dependencies/` 的具体 typed-facts readers/workers；
4. 迁移 persistence 和 publication concrete implementations；
5. 将 `composition/mod.rs` 缩减为顶层组装和有限导出；
6. 每迁走一段实现立即删除原位置，不保留重复 constructor。

Provider 迁移必须遵循 `docs/integration-session-and-operation-design.md`，不得在本次结构重构中发明新的 Integration 抽象。

### Phase 5：Services 模块化

依次迁移：

1. gateway；
2. routing；
3. persistence；
4. control；
5. simulation；
6. actor。

Actor 最后迁移，因为它是状态核心。Actor 拆分只允许组织同一类型的实现，不允许拆分状态 owner。

### Phase 6：Domain 模块化

1. 将 order、fill、commitment、reservation 归入 `domain/order/`；
2. 将 intent、plan、leg 和 policy 归入 `domain/intent/`；
3. 明确 domain 类型的可见性；
4. 删除不再有调用者的旧 re-export。

### Phase 7：公开 API 收敛与清理

1. 迁移 tests、bin 和真实外部调用者；
2. 收紧 crate root re-export；
3. 将 services 和 domain 默认设为私有；
4. 删除 compatibility path、重复 constructor、无调用者 trait 和旧模块；
5. 更新架构文档和相关 migration status。

## 11. 每阶段迁移规则

每个阶段必须满足：

1. 先确认业务 owner、状态 owner、调用者和目标层；
2. 优先做无行为变化的移动；
3. 不在同一提交中同时重写业务语义和目录；
4. 不建立只用于过渡的 manager、registry 或 facade；
5. 新路径通过测试后立即删除旧路径；
6. 一个提交只处理一个可审查的职责切片；
7. provider 迁移一次只处理一个 provider/product/capability slice；
8. 如果发现边界不清，先更新本提案或相关权威设计，而不是用兼容层掩盖。

## 12. 明确不做的事情

- 不新增 `execution-service`、`execution-runtime`、`execution-domain` 等 Cargo crate；
- 不新增 `ExecutionManager`、`ExecutionCoordinator` 或通用 provider registry；
- 不拆分 `ExecutionActor` 的业务状态所有权；
- 不创建多个 application facade；
- 不把 business route 放入 Integration；
- 不把 provider connection 或 payload 放入 application/domain；
- 不把 simulation 包装成 provider connection；
- 不为了统一依赖注入创建无第二实现、无隔离价值的 protocol；
- 不一次性创建目标树中的空目录和占位文件；
- 不在 publisher 中用 JSON 序列化往返完成 typed model 映射；
- 不在结构重构中顺便改变订单、intent、risk saga 或 reconciliation 语义。

## 13. 验收标准

### 13.1 可理解性

开发者应能仅凭目录回答：

1. 订单状态在哪里：`services/actor/`；
2. 下单用例在哪里：`application/use_cases/orders/`；
3. intent 生命周期在哪里：`application/use_cases/intents/`；
4. 远端订单恢复在哪里：`application/use_cases/reconciliation/` 和 `application/process/recovery.rs`；
5. Binance 如何组装：`composition/providers/binance/`；
6. Account/Market/Reference typed facts 如何接入：`services/dependencies/`，由 composition 配置；Risk command/recovery 位于 `services/risk/`；
7. UDS/JSON 控制协议在哪里：`services/control/`；
8. 谁能修改业务状态：仅 `ExecutionActor`。

### 13.2 文件和模块边界

- `application/process.rs`、`application/service.rs`、`application/model.rs`、`composition/mod.rs` 的混合职责被移除；
- application、composition、services、domain 下需要细分的稳定职责均采用完整目录模块；
- 层级根目录不再堆放属于同一职责族的裸文件；
- `mod.rs` 主要承担边界和导出，不重新膨胀为实现集合；
- 没有为目录对称创建空模块。

### 13.3 架构边界

- `ExecutionApplication` 是唯一公开业务 facade；
- `ExecutionActor` 是唯一可变业务状态 owner；
- application 不依赖 composition；
- domain 无基础设施和跨业务依赖；
- provider-specific concrete types 限制在 composition/Integration 边界；
- cross-module caller 只通过 application 或 contract；
- bin 只做输入适配、composition 和 invocation；
- transport wire model 不进入 application API。

### 13.4 行为保持

以下行为必须在迁移前后保持一致：

- submit/cancel/replace/fill；
- intent planning、completion 和 compensation；
- risk reservation recovery；
- remote reconciliation 和 unknown order resolution；
- duplicate/out-of-order external event 处理；
- required/optional route readiness；
- reconnect 与 resync barrier；
- persistence restore 和 outbox acknowledgement；
- snapshot/event publication；
- simulation/backtest 结果。

## 14. 验证命令

每个切片运行对应 focused tests，阶段结束后运行：

```text
cargo test -p kairos-execution
cargo test -p kairos-execution-contract
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

同时执行静态搜索：

```text
rg "crate::composition" crates/modules/execution/src/application
rg "kairos_(account|risk|market|reference|integration)" crates/modules/execution/src/domain
rg "kairos_integration::(services|participants)" crates/modules/execution/src/application crates/modules/execution/src/domain
rg "kairos_execution::(services|domain)" crates --glob '*.rs'
rg "serde_json::(to_value|from_value)" crates/modules/execution/src
rg -l "Integration::new" crates
rg -l "ConnectionSpec" crates
rg -l "IntegrationCapability" crates
rg -l "dyn Connection" crates
```

JSON 搜索结果需要逐项分类：显式 control/config/diagnostic 边界可以保留；event/snapshot business publication 路径必须使用 contract-owned typed mapping。

## 15. 风险与控制

### 15.1 Rust 可见性变化

跨目录移动会改变 `pub`、`pub(crate)` 和私有成员的可达性。迁移时应优先使用 `pub(crate)`，不得为了让移动后的代码编译而扩大到公共 API。

### 15.2 循环依赖

Application use case 拆分后可能通过相互调用形成隐式耦合。共享流程应归回 `ExecutionApplication` 的私有方法或现有 Actor/domain owner，不新增 helper manager。

### 15.3 Process 行为回归

Process 包含并发、reconnect 和 shutdown 行为。拆分应保持 channel ownership、任务生命周期、有界队列、ordering 和 recovery barrier 不变，并使用现有故障路径测试验证。

### 15.4 Provider 组装回归

Composition 拆分可能无意重复 provider context 或破坏 principal/quota scope。每个 provider slice 必须验证同一 binding 的共享关系、多 route 隔离和 credential redaction。

### 15.5 纯移动与语义修改混合

结构迁移容易暴露旧问题，但除非旧问题阻塞迁移，不应在同一提交顺便修复。需要语义修改时另开切片，明确 owner、旧概念删除项和行为测试。

## 16. 完成定义

本提案完成不是以“目录已经创建”为标准，而是同时满足：

1. 当前混合职责已经迁入明确目录模块；
2. 原裸文件和旧路径已经删除；
3. 没有新增重复 facade、状态 owner 或通用抽象；
4. 公开 API 已收敛到 application 和必要 composition 入口；
5. architecture tests 能持续保护依赖和所有权；
6. focused tests 与仓库级检查通过，或准确记录无关的既有失败；
7. 新开发者可以从目录直接理解 Execution 的业务边界和运行路径。
