# Execution 模块最终形态与实施计划

## 1. 总体结论

Execution 应成为交易系统中“执行计划、订单生命周期、成交事实、意图状态机”的唯一 owner。

Account 应成为“余额、持仓、权益、账户状态、账户权限和结算结果”的唯一 owner。Risk 负责风险预算和 reservation；Market 负责行情、订单簿和市场新鲜度；策略只产生业务意图，不直接管理交易所订单。

当前代码已经接近这个方向，但仍有两个关键问题：

1. Account 和 Execution 可能同时接收同一交易所的订单/成交事实。
2. 当前 Intent 主要表达“单标的目标数量”，还不能自然表达双边套利、组合仓位等多腿业务意图。

后续工作不应首先继续增加 Intent 字段，而应先冻结模块边界、统一订单事实来源，再将 Intent 升级为通用多腿执行计划。

## 2. 当前代码现状

### 2.1 Execution

Execution 已经拥有：

- 本地订单状态：Pending、Submitting、Accepted、PartiallyFilled、Filled、Canceled、Rejected、Failed；
- 本地订单与 exchange order id 的映射；
- Fill 记录和订单审计事件；
- Intent 状态和 Intent 事件；
- 状态持久化、snapshot 和幂等键；
- 订单提交、撤单和替换；
- 交易所订单查询和执行回报流；
- 将订单和成交事实发布给 Account。

主要实现：

- crates/business/execution/service/src/application/service.rs
- crates/business/execution/service/src/domain/order.rs
- crates/business/execution/service/src/services/gateway.rs
- crates/business/execution/service/src/composition/mod.rs

交易所连接由 composition 创建：

    execution composition
      -> Integration
          -> OrderEntryConnection
          -> OrderQueryConnection
          -> ExecutionStreamConnection

OrderEntryConnection 负责下单和撤单；OrderQueryConnection 负责远端订单查询；ExecutionStreamConnection 负责订单/成交回报；Execution application 负责本地订单状态。

### 2.2 Account

Account 也有自己的交易所连接，但连接类型不同：

- AccountReadConnection：余额、持仓、权益、账户状态和 open orders；
- AccountMarketProfileConnection：账户模式、保证金模式、持仓模式和费率；
- AccountEventStreamConnection：账户 private stream 中的 snapshot、订单观察和成交事实。

主要实现：

- crates/business/account/service/src/services/integration.rs
- crates/business/account/service/src/composition/account.rs
- crates/business/account/service/src/bin/kairos-account-server.rs

因此，Account 确实有自己的账户连接，但不是用来执行订单的。Account 不实现 OrderEntryConnection，也不应该拥有自己的下单、撤单状态机。

## 3. 订单和成交事实的边界

当前可能存在两条输入路径：

    交易所 private stream
           ├──> AccountEventStream -> AccountActor
           └──> ExecutionStreamConnection -> ExecutionApplication

同时，Execution 在收到订单和成交事实后，会通过 Account contract 发布：

    Execution
      -> Account.plan_order(...)
      -> Account.publish_order_event(...)
      -> Account.publish_fill(...)

同一笔成交可能因此来自 Execution 的执行回报，也可能来自 Account 自己的账户流。必须明确权威来源、同步/恢复来源、去重方式、冲突处理方式，以及 Account 是否允许反向改变 Execution 状态。

推荐规则：

- Execution 是订单生命周期和成交事实的权威 owner；
- Account 是余额、持仓和权益的权威 owner；
- Account private stream 是账户同步和对账输入，不是第二个订单真相源；
- Account 收到账户流事实时，以 fill_id、remote_order_id、order_id 幂等合并；
- 冲突进入 reconciliation 状态，不静默覆盖；
- Account 不得反向改变 Execution 的订单状态。

## 4. 最终架构

    Strategy
       | Intent
       v
    Execution Contract -> Execution Application -> Execution Plan
                                                  -> Execution Legs
                                                  -> Child Orders
                                                  -> Order Gateway
                                                  -> Exchange / Broker

    Execution -> Account：授权、订单事实、成交事实
    Execution -> Risk：reserve、resize、release、consume
    Execution -> Strategy：Intent snapshot、Intent events、执行查询
    Account -> Balances / Positions / Equity

| 模块 | 唯一职责 |
|---|---|
| Strategy | 产生交易信号和业务意图 |
| Execution | 意图、计划、leg、订单、成交和执行状态 |
| Account | 余额、持仓、权益、账户状态和结算 |
| Risk | 预算、风险评估和 reservation |
| Market | 行情、订单簿和市场新鲜度 |
| Integration | 交易所连接、SDK 适配和外部事实 |
| Composition/System | 连接组装、进程启动和依赖注入 |

必须长期保持：只有 Execution 可以下单、撤单和改单；只有 Execution 可以决定订单状态；只有 Account 可以修改余额、持仓和权益；Intent 仍归 Execution，不新增独立 Intent 业务模块。

## 5. Execution 最终内部结构

    execution/
      contract/
        command.rs
        query.rs
        event.rs
        snapshot.rs
        model.rs
        transport/
      service/
        src/
          bin/
          composition/
          application/
          services/
          domain/
            intent.rs
            execution_plan.rs
            execution_leg.rs
            order.rs
            fill.rs
            state_machine.rs

建议职责：ExecutionActor 是唯一可变状态 owner；IntentPlanner 生成 ExecutionPlan；OrderManager 管理 child order；ReconciliationService 处理交易所对账；ExecutionProcess 负责运行时控制；composition 负责具体连接组装。不增加泛化 coordinator、processor 或兼容 facade。

## 6. 核心领域模型

### 6.1 Intent

Intent 表达策略想达到的业务结果，不直接表达交易所订单：

    ExecutionIntent {
      intent_id,
      intent_type,
      strategy_id,
      launch_id,
      instance_id,
      account_scope,
      completion_policy,
      failure_policy,
      timeout_policy,
      source_watermarks
    }

第一期支持 SingleOrder 和 TargetPosition；第二期支持 PairArbitrage、PortfolioRebalance、QuoteProvisioning 和 Hedge。不要把所有新需求继续塞进一个拥有大量可选字段的万能 Intent。

### 6.2 Plan、Leg 和 Order

    Intent -> ExecutionPlan -> ExecutionLeg -> ChildOrder -> RemoteOrder

ExecutionPlan 保存 plan_id、intent_id、legs、completion_policy 和 failure_policy。ExecutionLeg 保存 leg_id、account_id、segment_key、instrument_id、market_id、target_quantity、side、order_policy 和 dependency_legs。

所有对象必须保存稳定关联：intent_id、plan_id、leg_id、order_id、remote_order_id、fill_id。当前仅有 intent_id -> order_ids，必须增加 plan_id 和 leg_id。

双边套利是两个 leg：买现货 BTC、卖 BTC 永续。组合仓位是多个目标仓位 leg。多腿 Intent 的完成状态不能通过所有订单数量简单求和得到。

## 7. 状态机

Intent：

    Accepted -> Planning -> Planned -> Executing -> PartiallyFilled -> Satisfied
    Planning -> Rejected
    Planned -> Rejected
    Executing -> CancelRequested -> Canceled
    Executing -> Failed
    Executing / PartiallyFilled -> Expired
    Unknown -> ReconciliationRequired
    Executing -> Compensating

Leg：

    Pending -> Ready -> Executing -> PartiallyFilled -> Satisfied
    Executing -> Canceled / Failed / Compensating

Order：

    Pending -> Submitting -> Accepted -> PartiallyFilled -> Filled
    Accepted -> CancelRequested -> Canceled
    Accepted -> Rejected / Expired
    Submitting -> Unknown -> ReconciliationRequired

建议新增 Planned、CancelRequested、Expired、ReconciliationRequired 和 Compensating。所有状态迁移集中到 domain state machine，禁止业务函数直接任意修改 status。

CompletionPolicy：AllLegsSatisfied、AllOrNothing、BestEffort、HedgeWithinTolerance、TargetQuantityReached。

FailurePolicy：CancelRemaining、ContinueOtherLegs、Compensate、PauseForManualIntervention、MarkReconciliationRequired。

## 8. 跨模块执行顺序

    1. 读取 Account/Market/Reference/Risk projection
    2. 生成 ExecutionPlan
    3. Execution 持久化 Plan
    4. Risk.reserve
    5. Account.plan_order / authorize
    6. Execution 持久化 child order
    7. 调用交易所下单
    8. 接收 order acknowledgement
    9. 接收 fill
    10. Execution 更新 Order/Leg/Intent
    11. 发布订单/成交事实给 Account
    12. Account 结算余额和持仓
    13. Risk.consume 或 release

| 失败位置 | 补偿动作 |
|---|---|
| Risk reserve 失败 | 不创建订单 |
| Account authorize 失败 | release Risk |
| 交易所下单失败 | release Account plan 和 Risk |
| 下单超时 | 标记 Unknown，进入 reconciliation |
| 订单取消 | release 剩余 reservation |
| 部分成交 | resize reservation |
| 完全成交 | consume reservation，触发 Account settlement |

## 9. Contract 设计

优先完善 kairos-execution-contract。Contract 不依赖 service crate，不暴露 Integration SDK 类型。

公共模型：ExecutionIntent、ExecutionPlan、ExecutionLeg、ExecutionOrder、ExecutionFill、IntentState、LegState、OrderState、IntentEvent、OrderEvent、FillEvent。

命令：SubmitIntent、CancelIntent、PauseIntent、ResumeIntent、CancelLeg、SubmitOrder、CancelOrder、ReplaceOrder、ReconcileOrder。

查询：GetIntent、ListIntents、GetIntentEvents、GetPlan、GetLegs、GetOrders、GetFills、GetExecutionTrace、GetReconciliationStatus。

事件流：execution.events。事件至少携带 stream_id、sequence、schema_version、producer_id、event_time、intent_id、plan_id、leg_id、order_id 和 payload。snapshot 必须携带 generation 和 event sequence；消费者从 watermark 继续消费，发现 gap 时重新读取 snapshot。

## 10. 完整实施阶段

### 阶段 0：冻结边界和验收标准

1. 明确 Execution、Account、Risk 的 owner；
2. 列出所有交易所连接和使用者；
3. 列出订单、成交、账户事实的全部来源；
4. 确认每类事实的唯一 owner；
5. 定义重复、乱序和冲突处理；
6. 更新模块边界文档，补齐当前缺失的 docs/module-boundaries.md，或将现有架构文档合并为唯一权威文档。

验收：只有 Execution 使用 OrderEntryConnection；Account 没有下单接口；Account 订单观察不会改变 Execution 状态；Execution 成交不会绕过 Account 修改余额或持仓。

### 阶段 1：完善 Execution Contract

创建 Intent、Plan、Leg、Order、Fill 的公共模型；增加命令、查询、结果、错误和 execution event stream；定义 schema version、generation、event sequence、watermark；增加 Rust/Python fixture、snapshot 稳定读取和 gap recovery 测试。

验收：Contract 独立编译，不依赖 service 或 SDK；Rust/Python 解码同一 fixture；事件可从 snapshot watermark 继续消费；断档可恢复。

### 阶段 2：重构 Execution 领域模型

从当前 ExecuteStrategyIntent 提炼 ExecutionIntent；增加 ExecutionPlan 和 ExecutionLeg；为 Order 增加 plan_id 和 leg_id；集中化状态迁移；按 leg 维护进度；明确 completion policy。

验收：现有单订单和单标的场景行为不变；多账户按账户分别计算；多 leg 不用简单数量求和判断完成；非法状态迁移会被拒绝并记录原因。

### 阶段 3：整理 Application

Application 只暴露 submit_intent、cancel_intent、get_intent、list_intents、submit_order、cancel_order、replace_order、get_execution_trace 等业务用例。

具体交易所连接、SDK payload、Unix socket 解析、Account/Risk client 构造和 provider 线程移到 composition 或 services。验收时静态检查 Application 不创建连接、不暴露 SDK、不依赖其他模块 services。

### 阶段 4：重构 Preflight 和跨模块协作

Advisory projection 来自 Account、Market、Reference、Risk snapshot/health/watermark，用于新鲜度、目标差额、市场规则、计划生成，不能替代 authoritative command。

Authoritative command 通过 Contract 执行：Account.plan_order、Account.publish_order_event、Account.publish_fill、Risk.reserve、Risk.resize、Risk.release、Risk.consume。

验收：snapshot 只做 advisory validation；Risk 自己持有 reservation；Account 自己决定 authorization；Execution 记录依赖 watermark；依赖不可用时进入明确 degraded/rejected 状态。

### 阶段 5：收敛 Execution / Account 事实来源

统一 fill_id、remote_order_id、client_order_id、execution_event_id；Execution 负责订单事件主要消费；Account private stream 负责账户同步和对账；Account 对两类事实幂等合并；冲突进入 reconciliation；禁止 Account 反向修改 Execution 状态。

必须测试：重复 fill、乱序 fill、双通道同时收到 fill、Account 先收到 fill、Execution 先收到 fill、Execution 重启远端对账、账户流出现未知本地订单。

### 阶段 6：完善单标的 Intent

增加 Planned、Intent 取消、超时、子订单失败策略、部分成交策略、Intent 恢复、Intent 版本、事件分页、状态查询、freshness policy 和 source watermark 校验。

验收：Execution 重启后可继续或进入明确恢复态；策略可查询状态和历史；相同 idempotency key 不创建重复 Intent；取消会处理所有 child order；所有 reservation 正确清理。

### 阶段 7：实现双边套利

PairArbitrageIntent 包含两个或更多 leg。必须定义比例、最小价差、最大滑点、最大等待时间、主腿顺序、对冲失败补偿、部分成交调整、净暴露上限、完成判定、取消和熔断策略。

推荐初版：同时 reserve 两腿 Risk；验证账户权限；提交两腿；一腿部分成交时按比例调整另一腿；超时取消剩余订单；仍有净暴露时进入 Compensating；无法补偿时进入 ReconciliationRequired。

验收：一腿成交另一腿拒单不会标记 Satisfied；两腿进度分别可查；整体状态由 completion policy 计算；reservation 按 leg 释放。

### 阶段 8：实现组合仓位

先实现目标仓位再平衡，不一开始实现复杂 portfolio optimizer。

输入为 account_id、instrument_id、target_quantity 或 target_weight、tolerance。计划由当前 Account snapshot、目标组合和 Market 行情计算每个 instrument 的 delta，再生成多条 ExecutionLeg。

必须定义目标单位、定价来源、最小交易单位、费用、现金保留比例、执行顺序、临时偏离、失败策略和最终偏差容忍度。

验收：每个 leg 可独立查询；组合 completion policy 明确；单 leg 失败不会伪装成组合完成；支持 BestEffort/AllOrNothing；重启不会重复提交已完成 leg。

### 阶段 9：策略和 Python 接入

Rust Contract 稳定后同步 Python facade。策略主路径只使用 submit_intent、get_intent、list_intents、cancel_intent、get_intent_events。单订单接口保留给运维、人工和测试，策略不直连交易所，也不调用 Account.publish_fill。

验收：Python/Rust 字段语义一致；Python 不读 service 私有状态；策略只能通过 Execution Contract 提交意图；Intent snapshot 和 event stream 可供策略观察。

## 11. 推荐实施顺序

    P0  冻结模块边界和事件所有权
    P1  完善 execution contract
    P2  增加 Plan / Leg / stable identity
    P3  集中化状态机
    P4  整理 Application 与 Composition 边界
    P5  收敛 Execution / Account 订单事实来源
    P6  完善单标的 Intent
    P7  实现 PairArbitrage
    P8  实现 PortfolioRebalance
    P9  完善 Python/Strategy 对外接口
    P10 做全链路恢复、对账和故障演练

不要先实现套利字段，也不要先增加新的意图模块。第一步应是把当前单订单和单标的 Intent 做成边界清晰、可以恢复、可以对账的执行核心。

## 12. 最终完成标准

1. 只有 Execution 可以访问订单入口连接；
2. 只有 Execution 可以修改订单状态；
3. 只有 Account 可以修改余额、持仓和权益；
4. Execution 与 Account 对同一成交事实幂等合并；
5. Intent、Plan、Leg、Order、Fill 关联完整；
6. Intent 支持恢复、取消、超时、部分成交和补偿；
7. 双边套利不需要新增业务模块；
8. 组合仓位不绕过 Execution 直接创建订单；
9. 策略只提交 Intent，不直接操作交易所；
10. 所有跨模块读写经过对应 Contract；
11. snapshot、event stream、watermark 和 gap recovery 有测试；
12. cargo test --workspace、uv run pytest -q、格式检查和架构搜索全部通过。

## 13. 当前仓库参考

- docs/cross-module-state-and-events.md
- docs/contract-service-migration.md
- crates/business/execution/service/src/application/service.rs
- crates/business/execution/service/src/composition/preflight.rs
- crates/business/execution/service/src/composition/mod.rs
- crates/business/account/service/src/services/integration.rs
- crates/business/account/service/src/composition/account.rs

模块边界的权威补充文档为 [docs/module-boundaries.md](module-boundaries.md)；本文件负责 Execution 的目标能力、状态机和实施顺序，二者应保持一致。

## 14. 当前落地状态

本轮改造已经落地：

- `ExecutionIntent -> ExecutionPlan -> ExecutionLeg -> ExecutionOrder -> ExecutionFill` 的稳定关联，其中订单和成交保存 `plan_id`、`leg_id`；
- 单标的 Intent 的持久化、幂等恢复、取消、过期、部分成交和未知订单对账状态；
- 单个逻辑 leg 的确定性拆单：child order 数量总和严格等于目标量，拆单间隔和 maker 最小间隔进入 Execution 持久化调度；
- maker 执行的订单 cadence、窗口限频和 Account snapshot + 已有 reservation 共同计算的库存保护；
- 显式多腿 Intent 的订单规划：`PairArbitrage` 按 leg 生成两条或多条订单，`PortfolioRebalance` 按 Account 当前持仓计算每个目标的 delta；
- `QuoteProvisioning` 双边 maker Intent：以 bid/ask 两个 limit leg 交付初始报价，并共享拆单、cadence、窗口限频和库存保护；
- `QuoteProvisioning` 的 Execution-owned refresh API：接收带观察时间的新 bid/ask，校验报价有效期和最小重挂间隔，撤掉旧双腿并把新订单挂回同一 Plan/Leg；
- Execution runtime tick 会从 composition 提供的 Market projection 读取最新双边报价，只在价格发生变化时触发上述 refresh，避免策略进程成为第二个订单 owner；
- 双边 Intent 的实际成交驱动对冲进度查询，以及 leader 成交超过已有 hedge active quantity 时自动创建补偿 child order；
- 对冲补偿最大尝试次数和熔断：超过上限或补偿关闭时进入 `ReconciliationRequired`，不再无限重试；
- 双腿 Intent 的买卖方向校验、预估手续费扣除后的净 edge 校验，以及跨市场数量比例/合约乘数由 `HedgePolicy` 驱动；
- Python 策略入口的双边套利、组合再平衡提交接口，以及 Intent 查询接口；
- Account 对重复成交幂等合并，对同 `fill_id` 的冲突事实进入 `Reconciling`；
- Rust Execution contract 的多腿载荷、事件关联字段、snapshot/event watermark 元数据；
- Intent event 查询支持 `after_sequence`/`limit` 分页，策略可从已持久化 watermark 继续读取并在发现断档时回退到 snapshot；
- 仓库唯一模块边界文档 [docs/module-boundaries.md](module-boundaries.md)。

上线前仍需针对每个 exchange 做真实合约规格/费用 profile 的配置验收，以及在具备凭证的环境执行 private-stream 双通道演练；这属于 deployment acceptance，不再引入新的 Execution 业务 owner。代码层已具备 runtime quote refresh、报价年龄/最小重挂间隔、净 edge 扣费、显式合约乘数和补偿熔断。

## 15. 对“复杂 Intent 是否完成”的明确判断

当前实现已经具备复杂 Intent 所需的代码能力：数据结构、拆单调度、双腿 runtime refresh、实际成交对冲、净 edge 扣费、合约乘数和补偿熔断。`PairArbitrage` 支持 leader fill 驱动的补偿 child order；`QuoteProvisioning` 支持双边 post-only 初始报价、cadence、库存保护、报价年龄校验和 Execution-owned refresh API；runtime tick 会从 Market projection 发现报价变化并重挂。真实 exchange profile 和 private-stream 演练属于上线前环境验收，不改变本模块的实现边界。

## 16. 故障收敛改造进度

本节记录连接级故障改造的实际落地状态，不能用普通单元测试代替真实 exchange 演练：

- 已落地：Execution 对未知远端订单持久化 `UnknownRemoteOrder`，包含首次/最近观测时间、成交信息、处置状态，并随 Execution snapshot 重启恢复。
- 已落地：Account 增加独立 `ObservedFill` 事实。Account 先收到成交时只记录观察事实并进入 reconciliation，不结算余额、不修改 Execution；Execution 确认后的正式 Fill 可校验并清除观察事实。
- 已落地：Execution stream 读取错误后显式调用连接的 `reconnect()`，而不是只等待后继续读取原连接。
- 已落地：Execution 提供远端 open-orders/history 对比入口 `/v1/reconcile-remote`，并提供 `/v1/unknown-remote-orders` 查询待处理未知订单。
- 已落地：Account private stream 的成交映射为 `ObservedFill`；Integration 缓冲流读取失败后会自动重连并继续投递后续事实。
- 已落地：CLI 增加 `reconcile-remote`、`unknown-remote-orders` 和 `link-unknown`，可用于沙盒或真实凭证环境的人工验收。
- 已落地：验收步骤、五类连接时序、只读前置命令和证据标准集中在 [docs/execution-recovery-acceptance.md](execution-recovery-acceptance.md)。
- 已落地：运行时监督器会在存在远端查询能力时按 watermark 定期执行 open-orders/history 对比，并将结果纳入 Execution 持久化状态；测试覆盖 Execution 断线重连、Account 先观察成交、未知远端订单持久化/恢复/关联等组合边界。
- 已补齐：远端对账不只更新订单状态，也会根据交易所返回的累计成交量与本地已记账成交量计算缺口，生成确定性恢复 Fill；重复对账不会重复结算，缺少成交价或远端累计量落后于本地时进入 `Unknown`/人工对账路径。
- 待完成：在具备凭证和可控沙盒的环境中执行真实 private-stream 演练，逐项验证 Execution 先收到、Account 先收到、两边同时收到、单边断线恢复、远端本地未知订单五种连接时序；这些是 deployment acceptance，不是当前代码机制缺失。

执行能力建议按以下层次交付：

1. 单订单生命周期：提交、撤单、超时、部分成交、恢复、对账和幂等。
2. 拆单执行：按数量、名义金额、时间窗口或盘口流动性生成 child orders；每个 child order 独立关联到同一 leg，并维护剩余量、已成交量、撤单量和失败原因。
3. 被动做市/Spread：报价、撤单重挂、价格偏移和库存边界；重点控制成交速率、库存风险和报价有效性。
4. 双边执行：两条 leg 的成交量按 hedge ratio 绑定；主腿成交后只能释放对应数量的对冲腿；对冲腿拒单或流动性不足时进入补偿、暂停或人工处理。
5. 组合再平衡：按目标仓位和风险预算排序执行，组合层只负责计划，订单仍由 Execution 统一持有。

对于 USDC/USDT 这类低波动、深度相对稳定的 maker spread 策略，核心通常确实不是单纯“预测方向”，而是控制：

- 报价价差和订单在盘口中的位置；
- 成交速率/成交占比，避免过快吃掉库存或被单边成交；
- 库存偏离和净敞口，触发偏价、缩量、暂停或主动对冲；
- 报价存活时间、撤单重挂频率、最小订单间隔和交易所限频；
- 手续费、返佣、滑点、资金/链路延迟后的真实净收益。

因此，maker spread 不应被建模成一个普通 `PairArbitrage` Intent。更合适的最终形态是 `QuoteProvisioning` 或 `MarketMaking` 执行策略：策略给出报价区间、目标库存和风险边界，Execution runtime 负责从 Market projection 发现报价变化、管理 child order 生命周期、撤单重挂、成交速率控制和库存保护；一旦形成净暴露，再由 Hedge/Compensation 子计划处理。跨市场或跨账户套利仍复用同一双腿 Plan/Leg 状态机。

### 15.1 复杂执行的新增验收标准

- 拆单后可查询 parent leg、child order、剩余量和聚合成交量；重启不会重复提交 child order。
- 双边任意一侧部分成交、拒单、断流、延迟时，净暴露都能计算并触发确定的动作。
- 对冲量由实际成交量驱动，而不是由原始目标量驱动。
- maker 策略有明确的成交速率、库存、报价年龄、撤单/重挂频率和限频约束；触发边界后进入可观察状态。
- 补偿失败不会伪装成成功，必须进入 `ReconciliationRequired`、`Paused` 或人工处理状态。
- 所有 child order 和补偿订单仍归属于原 Intent/Plan/Leg，Account 只接收并幂等合并成交事实。
