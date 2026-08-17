# Market 领域定位与模块边界显式化重构提案

## 1. 文档状态

- 状态：已按执行计划完成 Market 领域与目录重构；后续仅保留增量治理
- 范围：`crates/modules/market` 主 crate
- 不包含：Integration provider capability 重设计、Reference catalog 业务语义重写、行情 normalization 和 Order Book continuity 规则重写
- 主要目标：先明确 Market 在交易系统中的领域定位、事实权威性和上下游契约，再将代码和模型收敛为职责清晰、依赖方向稳定的行情运行时业务模块。第一阶段整理目录和依赖；第二阶段删除 Market 内部的 Reference 镜像、统一 snapshot/view 语义并收敛运行时市场模型

### 1.1 当前实施状态（2026-08-17）

领域概念、主要依赖边界和目标目录均已完成迁移。执行细节、证据和逐阶段验收记录见 `docs/proposal/market-module-refactoring-execution-plan.md`；本文保留领域决策与长期约束，后续只做增量治理。

已经落地的边界包括：

- `application`、`composition`、`services`、`domain` 四个层根目录只保留 `mod.rs`，稳定概念统一使用目录模块，消除了同层 `foo.rs`/`foo/` 并存；
- `MarketApplication` 与 `MarketActor` 仍分别是唯一 application facade 和唯一可变业务状态 owner；
- application 已按 `subscriptions`、`observations`、`queries`、`sources`、`process`、`replay` 分区，旧 `facade.rs`、`service.rs`、`query.rs` 和单文件 `process.rs` 已删除；
- Order Book 已迁入 `domain/observation/order_book/`，其 application ingestion 入口位于 `application/observations/order_book/`；它是具有 continuity/resync 附加规则的 Observation，不再与 Observation 并列；
- canonical `InstrumentKind` 与 typed observation capability matrix 已收敛在 `domain/market/`；无状态 Spot、Perpetual、Future、Option wrapper 已删除；
- 私有 source driver 统一位于 `services/source/`，provider-specific concrete connection 限制在 composition；replay service 不再依赖 composition；
- process lifecycle、typed ingress、maintenance、universe、source recovery、publication 与 control wire 已按职责拆分；JSON command record 的定义和解析位于 `services/control/wire.rs`；
- event publication 的 bounded fanout/backpressure 位于 services，contract mapping、typed encoding 和 mmap publisher 位于 `composition/publication/`；业务 publisher 不使用 JSON model round trip；
- `domain` 已改为 crate-private，application 只选择性导出合法的业务 request/result 类型；跨进程消费者继续使用 `kairos-market-contract`；
- architecture tests 固化了四层根目录、单一 Actor、依赖方向、provider 隔离、control wire、Order Book 归属、market-kind 结构和私有 Domain 等约束。

第二阶段进一步完成了领域语义收敛：

- `domain/reference/` 与 Market-owned Reference SQLite reader 已删除；`composition/reference/` 通过 `ReferenceClient::market_snapshot()` 的 consumer-scoped current projection 和 event watermark 生成 `ReconcileMarketUniverse`；
- `MarketDescriptor` 已由 `ResolvedMarket` 与必填 `MarketDataRoute` 取代，provider access 不再通过 canonical symbol 或 market kind 推断；
- 所有公开 observation kind 都位于 `domain/observation/<kind>/mod.rs`，`order_book/` 与 quote、trade、bar、rate 等保持同级；
- `ObservationKind`、`ObservationSelector`、`MarketViewKey.kind` 与 freshness identity 已类型化，字符串别名只在 control/config 输入边界解析；
- Actor current state 只维护一份 `views` projection；旧 `latest` map 和泛化 latest query 已删除；
- `MarketView`、私有 `ReplayCheckpoint` 与 `MarketChange` 已拆分；`domain/snapshot/`、公共 `snapshot()` API 和 `MarketSnapshotPublisher` 已删除；
- publication 接收 `MarketChange`，使用 `MarketChangePublisher` 命名；current view 的构造不再先克隆 replay checkpoint。

Observation 的目录对称现在按真实调用链落实：每个公开 kind 都有 `domain/observation/<kind>/`，并通过 `application/observations/<kind>/` 的 typed ingestion 入口进入 `services/actor/observations/<kind>/` 的 typed 状态转换入口。它们不是空 re-export；Order Book 只因 continuity/snapshot/delta 机制复杂而拥有更多内部实现。

尚未完成的目录迁移不得再用“候选文件可省略”掩盖。文件可以因没有独立职责而省略，但已存在且可识别的职责必须进入目标目录，例如 Process 的 actor task/ingress/maintenance/shutdown、Sources 的 attachment/subscriptions/recovery，以及 Actor 的 subscriptions/sources/freshness/read model/events。完成状态以实际文件树和 architecture test 为准，而不是以文档示意图或删除旧文件为准。

### 1.2 验证结果（2026-08-17）

- `cargo test -p kairos-market --no-fail-fast`：通过，包含 48 个 lib tests、10 个 Actor integration tests、19 个 architecture tests、4 个 Order Book tests 和 2 个 replay tests；architecture tests 已覆盖每个 Observation kind 的 Domain/Application/Actor 三层同名目录和真实入口；
- `cargo test -p kairos-market-contract`：通过，2 个 contract tests；
- `uv run pytest -q`：通过，361 passed、8 skipped；
- `python3 scripts/check/check_crate_layout.py`：通过；
- `cargo fmt -p kairos-market -- --check` 与 Market/doc scoped `git diff --check`：通过；
- `cargo test --workspace` 与 `cargo fmt --all -- --check`：被工作树中无关的 Execution 改动阻断；`crates/modules/execution/src/application/use_cases/intents/mod.rs:7` 开始的函数存在未闭合 delimiter，rustc/rustfmt 在第 1106 行报告错误。Market focused checks 已独立完成。

### 1.3 第二阶段问题陈述（2026-08-17）

第一阶段解决了顶层裸文件和部分依赖方向不可见的问题，但尚未拆完所有大实现文件；目录整理同时暴露出以下领域模型未收敛问题：

- `domain/reference/ReferenceChanged` 使用外部模块名称表达 Market application input，不是 Market 自身的领域概念；
- `services/reference/ReferenceProjection` 持有 Reference SQLite 路径并直接执行分页、join 和 watermark 校验，使 Market 私有 service 同时成为 Reference client 和 persistence adapter；
- `MarketDescriptor` 同时承载 canonical identity、Reference 兼容字段、provider access 和 runtime source constraint，尚未成为含义单一的 Market-owned 值；
- `MarketSnapshot` 与 `MarketCurrentView` 字段高度重叠，`current_view()` 通过先构造完整 snapshot 再逐字段搬运实现；
- Actor 同时维护 `latest` 与按 observation kind 区分的 `views`，同一 observation 被写入两份 current-state projection；
- `MarketViewKey.kind` 和 subscription selector 继续使用自由字符串，导致 observation capability、查询和 publication 依赖隐式约定；
- Spot、Perpetual、Future、Option 目录当前仅包装静态 selector allowlist，尚未形成值得独立类型和文件的业务行为；
- `MarketSnapshotPublisher::publish` 实际接收 `MarketChange`，名称继续混淆 checkpoint、current view 和 change publication。

第二阶段不合并顶层 Market 与 Reference 模块。它要做的是从 Market domain 中移除 Reference 技术词汇和读取机制，只保留 Reference 解析后对 Market 有业务意义的 `ResolvedMarket`/`MarketDataRoute`，并建立唯一、可解释的 current-state projection。

## 2. 背景

Market 已经具有仓库规定的四个主要层次：

```text
bin -> composition -> application -> services
                         \-> domain
```

第一阶段启动时的问题不是缺少分层，而是边界只存在于概念上，多个大文件仍同时承担不同职责：

- `application/process.rs` 超过 2,000 行，同时处理进程生命周期、Actor task、control command JSON、命令幂等、Reference 事件、source 驱动、history、publication、freshness timer 和 shutdown；
- `application/facade.rs` 同时处理 source attachment、source mailbox、subscription reconciliation、provider confirmation、observation ingestion、order book resync、freshness 和 shutdown；
- `application/service.rs` 继续为 `MarketApplication` 实现 subscription、Reference reconciliation、ingestion、query 和 event draining，`facade` 与 `service` 的职责命名不清；
- `services/actor.rs` 同时包含 subscription、source、observation、order book、freshness、snapshot 和 event 状态转换；
- `services/source/stream.rs` 同时处理连接恢复、subscribe/unsubscribe、normalization、resync、status 和 failure classification；
- `composition/publisher.rs` 同时承担 mmap publisher 和几乎全部 snapshot/view FlatBuffers 编码；
- `services/event_wire_v2.rs` 是另一套大型 event wire 编码实现，publication 职责分散在 application、services 和 composition；
- `composition/config.rs` 聚合配置 DTO、运行时模型、provider 产品枚举和默认值；
- `composition/mod.rs` 仍保留 provider 构造、route 匹配和 replay attachment，和已经建立的 `composition/sources/` 形成新旧路径并存；
- `services/source/replay.rs` 依赖 `crate::composition::MarketReplayClock`，形成 private service 反向依赖 composition；
- `services/source/binance.rs` 依赖具体 Binance connection，provider-specific 类型未完全限制在 composition/Integration 边界；
- `lib.rs` 公开整个 `domain` 并大量重导出 domain 类型，推荐的 application/contract 入口不够明确。

第一阶段不是按行数机械拆文件，也不是创建新的 engine、manager、registry 或通用 source adapter。它让 Market 已有的业务所有权、运行路径和外部边界在目录结构中直接可见；第二阶段进一步删除整理后暴露出的重复模型和错误边界。

### 2.1 先定义领域，再定义目录

Market 的结构不能从现有大文件或 Execution 的目录树反推。应先回答以下领域问题：

1. 系统中的“市场”由谁定义；
2. provider 发来的数据何时成为 Kairos 可消费的行情事实；
3. 谁决定需要订阅哪些市场和哪些数据种类；
4. 谁负责行情连续性、时效性和可用性；
5. Execution、Risk 等消费者依赖的是原始事件、当前视图还是 readiness；
6. replay、history 和 live data 是否具有相同的业务语义；
7. Market 发布的事实具有什么水位和可追溯性。

目标目录必须由这些答案派生。文件大小只能作为混合职责的证据，不能作为领域边界的依据。

### 2.2 Market 在系统中的定位

Market 是 Kairos 的**行情运行时投影与交付边界**。

它位于 Reference、Integration 与业务消费者之间：

```text
Reference                              Integration
canonical Market / MarketDataAccess   provider capability / normalized external fact
          \                              /
           \                            /
            -> Market subscription and projection ->
                 continuity / freshness / readiness
                 current views / ordered events
                              |
                 +------------+------------+
                 |                         |
             Execution                   Risk
        planning/admission/simulation   freshness gate/context
```

这个定位包含三层含义：

1. Market 不是 Reference catalog。它不定义一个 instrument 在哪个 canonical Market 上交易，也不拥有 provider access 的生命周期；
2. Market 不是 Integration adapter。它不负责认证、协议、签名、raw payload 或 provider session；
3. Market 不是被动消息中转。它拥有订阅意图、source 业务状态、各类 observation 的投影质量、freshness、readiness，以及对外可消费的当前视图和有序变化事实。Order Book 是 observation/view 的一种，只是它需要 snapshot/delta continuity 和 resync 机制维护。

因此，Market 的核心领域能力不是“连上交易所”或“维护市场目录”，而是：

> 根据业务订阅意图和已经解析的市场数据路由，从 Integration 接收外部事实，将其纳入 Kairos 的来源、顺序、连续性与时效性语义，并交付可判断质量和水位的 current view 与 ordered change。

### 2.3 与相邻模块的领域关系

#### Reference -> Market：身份与可访问性

Reference 提供：

- canonical `Market`、Instrument、Listing 等身份；
- `MarketDataAccess`，即 canonical Market 到 provider/product/symbol 的数据访问地址；
- access status、effective time 和生命周期变化；
- Reference snapshot/event sequence。

Market 使用这些事实解析 dynamic subscription、选择可用 source，并在 Reference 变化时 reconciliation。跨模块读取必须通过 `kairos-reference-contract` 拥有的 `ReferenceClient`/typed view/event boundary 完成；Market 不得直接打开 Reference SQLite、依赖其表结构或自行实现第二套 Reference client。

`composition/reference/` 负责把 Reference contract fact join 和映射为 Market-owned `ResolvedMarket` 与 `MarketDataRoute`。进入 application 后，类型名称和用例只表达 Market 所需的“已解析市场集合”及其上游 watermark，不再暴露 `ReferenceChanged`、SQLite reader、Reference transport frame 或完整 catalog。Market 可以记录已消费 watermark，但不能修改、补全或重新定义 canonical identity。

`MarketDataAccess` 与 `ExecutionAccess` 必须保持分离：观察某市场使用的 provider/symbol 不意味着下单使用同一 provider/symbol。Market 不拥有 execution route。

#### Integration -> Market：外部能力与 provider facts

Integration 提供：

- provider-native async stream/snapshot/historical capability；
- provider authentication、connection、protocol 和 quota 行为；
- provider payload 到 Integration application fact 的 normalization；
- provider 层错误和 connection lifecycle。

Market 将 Integration fact 映射为 Market-owned observation、source epoch 和 subscription confirmation。Market 决定这些事实是否满足当前订阅、是否连续、是否过期，以及如何形成对外 view/event。Quote、Trade、Bar、Order Book、Greeks、Rate 等都属于 Market Observation；它们的投影算法不同，但不构成不同的上层领域。

Integration 的 `MarketEvent` 是输入事实，不是 Market 对外发布的业务事件。二者不应共享类型或被当作同一层事件。

#### Market -> Execution：定价、准入与模拟输入

Execution 当前通过 `kairos-market-contract` 的 typed view 读取 quote 等市场事实，并将可用性映射为 planning/admission/risk context。未来消费仍应满足：

- live planning/admission 读取 Market typed view，而不是 provider DTO；
- 每次使用都能识别 `market_id`、`source_id`、view kind、generation、applied event sequence 和发布时间；
- stale/missing/ambiguous view 不被解释为一个有效价格；
- backtest/simulation 的 Market 输入保持显式离线边界，不冒充 live Market view。

Market 不替 Execution 决定成交策略、滑点政策、订单价格或 execution route。`estimate_execution` 若保留，应被定义为基于 order book 的 Market query 结果，而不是订单准入或执行决策。

#### Market -> Risk：时效性证据

Risk 拥有是否接受风险请求的决策权。Market 只提供 freshness/readiness 和相应水位证据，不替 Risk 制定 stale-data policy。

Risk context 中的 `market_is_fresh` 和 `market_freshness_watermark` 应来自一个明确的 Market freshness view 或由调用方基于该 view 显式映射，不能长期由“quote 是否存在”或占位 generation 推断。

#### Account 与 Market：没有直接状态所有权关系

Account 的余额、仓位、权益、账户订单事实及其 freshness 不属于 Market。估值可能消费 Market view，但估值结果仍由 Account 或上层业务用例拥有。Market 不因被用于 mark-to-market 而拥有 position valuation。

#### Workspace/System -> Market：进程资源

Workspace/System 提供路径、实例身份、socket、mmap/Aeron 资源和启动协调。MarketProcess 管理自身 runtime facade，但不成为全局 process supervisor。

### 2.4 Market 的领域输入、命令、查询和输出

Market 的边界应区分四种交互，避免统一成含糊的 `handle(payload)` 或通用 event bus。

#### 业务命令

- `Subscribe`：声明调用者希望持续获得哪些 canonical Market 和哪些 observation/view；
- `Unsubscribe`：撤销某个调用者拥有的订阅意图；
- `ReleaseOwner`：调用者生命周期结束时释放其全部订阅；
- `RecoverSource`/运维控制：恢复已有 source，不创建新的业务含义。

命令改变 Market-owned subscription intent 或 source lifecycle，必须具有明确 owner、idempotency 和结果。Transport JSON envelope 不是命令本身。

#### 外部事实输入

- Reference snapshot/change；
- Integration stream event、snapshot result、subscription acknowledgement 和 connection failure；
- replay 中按明确 clock/checkpoint 产生的 Market-owned observation input；
- maintenance clock，用于 freshness evaluation 和 recovery deadline。

外部事实必须先映射为 typed application/services input，才能进入 Actor。Raw payload 不进入 Market domain。

#### 查询

- latest quote/trade/bar/derivatives observation；
- order book；
- freshness/readiness；
- subscription status；
- 基于 order book 的只读 execution estimate；
- process health（仅运行状态，不泄露业务快照）。

跨进程高频业务查询优先通过 typed replacement view；control HTTP 只承担 command、health 和低频控制，不提供第二套业务查询 facade。

#### 输出事实

Market 对外输出两类能力：

- **Event**：发生过什么，例如 quote updated、trade occurred、bar completed、order book delta、resync required；
- **View**：当前已知什么，例如 latest quote、bar window、greeks、rate、mark/index/funding/open-interest、order book 和 freshness。

Event 用于有序增量消费和审计式处理；View 用于快速读取当前事实。两者必须通过 generation、applied event sequence、published time 和 source identity 建立一致性联系，不能把 mmap view 当作事件日志，也不能让事件消费者通过自行聚合成为无约束的第二权威 Market state。

#### 交付承诺：best effort，而不是可用性保证

Market 的核心责任是**尽全力交付带身份、来源、水位和质量信息的市场视图**。Subscribe 表达业务需求，不构成 provider 一定在线、每条外部消息一定到达或某个 view 永远可用的保证。

当 source 断开、输入丢失、顺序不连续或事实过期时，Market 应当：

1. 不把不可信状态继续伪装成有效当前视图；
2. 显式推进 source/freshness/readiness 状态或发布 resync requirement；
3. 在能力允许时自动 reconnect、resubscribe、重新获取 snapshot 并 resync；
4. 恢复后继续交付新的 current view 和 event；
5. 让消费者依据 freshness、epoch、generation 和 watermark 决定是否使用。

因此，Market 保证的是明确的身份、状态转换、恢复动作和质量信号，不保证外部市场数据源本身的完整性、持续可用性或零丢失。不同交付通道的语义也不同：replacement view 尽力提供最新可用状态；bounded event stream 在慢消费者或断连时必须显式表现 gap/disconnect，而不是承诺无限缓存。

### 2.5 事实权威性分层

Market 内外的“事实”具有不同权威层级：

| 层级 | 所有者 | 含义 | Market 的责任 |
| --- | --- | --- | --- |
| Canonical identity | Reference | Market 是什么、生命周期如何 | 只读消费并记录 Reference 水位 |
| Data access | Reference | 通过哪个 provider/product/symbol 观察 Market | 解析为 source route，不重新定义 |
| Provider fact | Integration | provider 实际返回了什么 | 接收 typed fact，不拥有协议语义 |
| Market observation | Market | 外部 fact 已映射到 canonical Market/source | 校验身份、订阅关联和时间语义 |
| Derived state | Market | current view、order book、freshness、readiness | 单一 Actor 维护并发布水位 |
| Business decision | Execution/Risk/Account | 是否下单、拒绝、估值 | Market 提供证据，不替消费者决策 |

这一区分决定代码放置：provider normalization 不能因最终产生 quote 就移入 Market domain；risk stale policy 不能因读取 freshness 就移入 Market；Reference projection 不能因被 Market 缓存就成为 Market canonical state。

### 2.6 核心聚合与不变量

当前实现使用单一 `MarketActor` 保证多个强关联状态在同一顺序边界内更新。本次重构保留这一设计，并明确以下领域不变量：

#### Subscription 不变量

- 每个 subscription 具有稳定 ID、owner、mode、selectors 和成员状态；
- dynamic subscription 的成员来自某个明确 Reference 水位；
- 同一 owner 的 unsubscribe/release 不能释放其他 owner 的订阅；
- subscription desired state 与 provider confirmation 必须区分；
- source 不可用时，subscription 不能被错误标记为完整可用；
- max dynamic members 是业务约束，不是 provider quota 的替代品。

#### Source 不变量

- `SourceId` 标识 Market-owned 数据来源实例/路由，不等同于 provider ID；
- 每次重连或恢复推进 source epoch，旧 epoch 输入不得污染新状态；
- source readiness 来自所需订阅、connection status、snapshot/resync barrier 的组合；
- provider connection lifecycle 是 Integration 技术事实，Market source status 是业务可消费性，两者不能合并为一个枚举；
- command/input channel 和 task handle 是运行机制，不应出现在公开 domain model。

#### Observation 不变量

- 每个 observation 必须关联 canonical `market_id` 和明确 `source_id`；
- provider symbol 只能作为 provenance/access fact，不能替代 canonical Market identity；
- latest view 的更新规则必须按 observation kind 明确，不能由通用 JSON map 决定；
- event time、receive time 和 publish time 若语义不同，必须分别保留，不能压成一个含糊 timestamp；
- duplicate/out-of-order 处理规则必须可测试并保持 provider/source 隔离。

#### Order Book Observation 的附加不变量

- Order Book 与 Quote、Trade、Bar 一样属于 Market Observation，并形成对应的 current view；
- snapshot 建立 continuity barrier，delta 只能应用于匹配 source/market/epoch 的 book；
- gap、乱序或不兼容 sequence 触发明确的 resync requirement；
- resync 是 Market 为继续尽力交付该 Observation 而执行的内部恢复过程，不是新的领域用例或独立 application facade；
- resync 完成前，该 Order Book view 必须携带不可用、stale 或 rebuilding 等明确质量状态，不能被误报为当前可用；
- Market 拥有 canonical Order Book observation/view，Integration 只提供 provider facts 和 snapshot capability；
- order book query/estimate 不产生 execution commitment。

#### Freshness 与 readiness 不变量

- freshness 是针对具体 market/source/view 的时间质量事实，不是进程 health 的别名；
- freshness policy 的输入包括最后有效 observation 时间、当前时间和允许 age；
- readiness 表示 source/subscription 是否满足消费前置条件，freshness 表示已有事实是否仍及时，二者不能合并；
- process health 只回答 runtime 是否存活/可服务，不暴露或替代业务 freshness；
- published freshness 必须携带可供消费者比较的 generation/watermark。

#### 发布不变量

- Actor mutation、Market event sequence 和 view applied sequence 必须保持可解释的顺序关系；
- 同一 change 不得因多个 publisher 路径产生不一致业务编码；
- 慢消费者策略必须有界且显式，不得无限增长 Actor 内存；
- replacement view 可以覆盖旧快照，但必须保留 producer incarnation、generation 和 applied event sequence；
- publisher failure 不能反向创建第二份业务状态。

### 2.7 Market 的内部领域能力划分

基于上述定位，Market 内部应围绕以下领域能力组织，而不是围绕 provider 或 transport 组织：

1. **Subscription Demand**：表达谁需要哪些 Market facts，并管理 static/dynamic intent；
2. **Market Universe Reconciliation**：接收 composition 已从 Reference contract 解析好的 `ResolvedMarket`，在明确上游水位上更新 dynamic subscription 成员；
3. **Source Supervision**：把 resolved demand 转换为 source desired state，并追踪 acknowledgement、epoch、recovery 和 readiness；
4. **Observation Projection**：把 typed provider facts 纳入 canonical Market/source 身份，更新 Quote、Trade、Bar、Order Book、Greeks、Rate 等 current views；其中 Order Book 投影内部维护 snapshot/delta continuity 和 resync barrier；
5. **Freshness Evaluation**：根据事实时间和 policy 产生 freshness/readiness evidence；
6. **Fact Delivery**：以 ordered event 和 replacement view 两种语义尽力交付事实；
7. **Replay/History**：在明确 provenance、clock 和 checkpoint 下重放或记录 Market facts。

这些是一个 Market bounded context 内的协作能力，不意味着八个 facade 或八个 Actor。它们共同通过单一 application facade 和单一 Actor 一致性边界实现。

### 2.8 领域定位对目标结构的约束

由上述领域分析直接得出：

- application 一级目录优先使用 `subscriptions`、`observations`、`universe`、`queries`、`process` 等业务能力名称；`universe` 在 Market 上下文内表示 Market-owned 可订阅市场集合，不是 Reference 镜像；所有公开 Observation kind 在 `observations/` 下使用同名平级目录，Order Book 不获得特殊顶层地位；
- source driver、control transport、publication queue 是支撑业务能力的 services，不与 subscription/observation 并列为公开 facade；
- provider 名称只出现在 composition；
- Reference client 和 adapter 只存在于 composition；它把 contract fact 投影为 Market-owned reconciliation input；
- `domain/reference` 不存在，Market domain 不出现 `Reference*` 类型；
- contract 按 control/event/view 三种不同交互语义组织，不暴露 Actor snapshot；
- freshness/readiness 必须成为一等领域输出，而不是散落在 process timer 和 source status 中；
- replay/history 必须标记 provenance，不能让 replay fact 在未声明的情况下冒充 live source fact。

### 2.9 市场类别必须在领域规则中显式存在

Market 不能把 `exchange + symbol` 当作完整市场身份，也不能只在 composition 的 provider enum 中区分产品。以 Binance 为例，Spot、永续合约、交割合约和期权是不同的 canonical market kind：

- **Spot**：主要交付 quote、trade、bar、Order Book；
- **Perpetual**：除基础价格/成交/Order Book 外，通常还需要 mark price、index price、funding rate 和 open interest；
- **Future/Delivery**：除基础 observation 外，需要 expiry/交割语义、mark/index price 和 open interest，但不能假定存在 perpetual funding；
- **Option**：除基础 observation 外，需要 option greeks、underlying/index price、mark price、open interest，并依赖明确的 expiry、strike、right 和 underlying identity。

这些类别由 Reference 的 canonical `InstrumentKind`/Instrument terms 决定，不由 Binance/OKX 等 provider product code 决定。`usd-m-futures`、`coin-m-futures`、`SWAP` 等是 provider access/product surface；同一个 provider product surface 可能承载多个 canonical market kind，不能替代 Spot/Perpetual/Future/Option 身份。

市场类别必须进入 typed identity 和 capability rule，但不要求一类市场对应一个目录或空壳类型。第一阶段建立的 `SpotMarket`、`PerpetualMarket`、`FutureMarket`、`OptionMarket` 当前只有静态 `supports()`，没有独立状态、生命周期或不变量；第二阶段将它们收敛为一个由 `InstrumentKind` 驱动的 observation capability rule。

因此目标结构优先表达真实机制：

```text
domain/
  market/
    resolved.rs
    data_route.rs
    selection.rs
  observation/
    identity/
      kind.rs
      key.rs
      qualifier.rs
      capability.rs
    quote/
    trade/
    bar/
    trade_bar/
    quote_bar/
    ticker_24h/
    option_greeks/
    rate/
    mark_price/
    index_price/
    funding_rate/
    open_interest/
    order_book/
```

- `ObservationKind` 表达 quote、trade、bar、funding、greeks、Order Book 等 typed capability；
- capability rule 决定某个 `InstrumentKind` 在业务上允许哪些 observation；
- source/provider capability 决定当前 route 实际支持哪些 observation；
- subscription readiness 同时要求业务允许且 route 支持；
- 每个公开 `ObservationKind` 都对应一个同名目录模块，目录层级不表达复杂度高低；
- `order_book/` 与 `quote/`、`trade/`、`bar/` 等处于同一层；它拥有更多内部文件只是因为 snapshot/delta continuity 和 resync 实现更复杂；
- 只有当 Spot、Perpetual、Future 或 Option 出现独立状态、不变量和当前调用者时，才为该类别恢复专门模块。

`ResolvedMarket` 和 `MarketSelectionQuery` 必须包含 canonical `InstrumentKind`，不能继续让名为 `market_type` 的 `ProviderProductCode` 同时承担 canonical kind 与 provider product 两种语义。旧 `market_type` 在迁移期只能存在于 composition 的 Reference contract mapping，最终路由使用以下三个互不替代的维度：

1. canonical `instrument_kind`：Spot/Perpetual/Future/Option 等；
2. Reference `MarketDataAccess.provider_product`：provider 产品面；
3. Market `source_id`：具体运行来源/路由。

selector 的合法性也应按 market kind 判断。例如 Spot subscription 不应悄悄接受 `funding_rate` 或 `greeks`；Perpetual 可以请求 funding/mark/index/open-interest；Option 可以请求 greeks，但必须具备完整期权身份。若 provider/source 不支持某个该市场类别允许的 Observation，subscription 应表现为 unsupported/degraded，而不是假装 ready。

## 3. 架构判断

### 3.1 Market 的业务所有权

Market 拥有：

- normalized market observations 和最新 observation view，其中包括 Order Book；
- Order Book observation 特有的 snapshot/delta sequence continuity 和 resync requirement；
- static/dynamic subscription intent、成员解析结果和 subscription lifecycle；
- Market source 的业务路由结果、状态、epoch 和 readiness；
- 行情 freshness、feed status 和 Market view generation；
- observation、order book、subscription、source 和 freshness 变化事件；
- Market 侧对 Reference change 的消费水位和 reconciliation 结果。

Market 不拥有：

- Reference 所有的 canonical market identity、catalog 和 market-data access 生命周期；
- Reference client、SQLite persistence schema、catalog join 和 contract event decode；这些是 contract/composition 边界职责；
- Integration 所有的 provider connection、认证、协议、技术 quota、raw payload 和 provider normalizer；
- Execution 所有的 execution intent、order lifecycle 和 route commitment；
- Account 所有的余额、仓位、权益和账户侧订单事实；
- Workspace/System 所有的路径、实例资源、全局进程生命周期和启动协调。

### 3.2 状态所有权

`MarketActor` 继续作为 Market 可变业务状态的唯一所有者，包括：

- observations 和 views，其中 Order Book 作为需要连续性维护的 observation；
- subscriptions 和 dynamic intents；
- freshness、feed status 和 source business state；
- source request correlation 和待发布 changes/events；
- 当前实现中与 source mailbox 紧密关联的运行状态。

Actor 内部只有一份 current-state authority。`views` 是按 source、market、observation kind 和 qualifier 标识的唯一 observation current projection；不得再维护“最后到达的任意 observation”这一份兼容性 `latest` 状态。Order Book 可以因 continuity 算法使用专门存储，但对外仍表现为一种 typed Market view，不形成第二个聚合或查询 facade。

`ActorState`、`ReplayCheckpoint`、`MarketView` 和 `MarketChange` 必须使用不同术语：

- `ActorState` 是 Actor 内部可变状态，不作为 application result 或 contract model；
- `ReplayCheckpoint` 只服务显式 replay/recovery，包含恢复所需的完整状态和位置；
- `MarketView` 是消费者读取的当前事实，可被 replacement publication 覆盖；
- `MarketChange` 是带 sequence 的有序增量，不是 snapshot；
- `OrderBookSnapshot` 是订单簿 continuity bootstrap 的业务术语，可以保留，不等同于 Actor snapshot。

目录拆分不能产生 `SubscriptionActor`、`OrderBookActor`、`SourceActor` 或第二份 Market view。`services/actor/` 的子文件只能为同一个 `MarketActor` 组织状态转换。

`MarketApplication` 是唯一公开业务 facade。subscription、ingestion、query、reconciliation 和 source orchestration 可以分别组织实现，但不能演变成多个 application facade。

`MarketProcess` 是围绕 `MarketApplication` 的可复用 runtime facade。它可以拥有 lifecycle、typed ingress dispatch、timer 驱动、publication draining、market-universe reconciliation、source recovery 和 shutdown 行为，但不应理解 HTTP/JSON wire、Reference SQLite、Reference transport frame、构造 provider connection 或拥有 Market 业务状态副本。

### 3.3 数据流与依赖方向

目标运行流为：

```text
Reference contract client
    -> composition/reference adapter
        -> ReconcileMarketUniverse(ResolvedMarket, upstream watermark)
            -> MarketApplication
                -> MarketActor

provider-native Integration capability
    -> services source driver
        -> typed SourceInput
            -> MarketApplication
                -> MarketActor
                    -> MarketChange / MarketEvent / MarketView
                        -> publication capability
                            -> contract-owned wire/view
```

目标依赖方向为：

```text
market bin
    -> market composition
        -> market application
        -> market services
        -> integration application capabilities
        -> reference contract

market application
    -> market services
    -> market domain

market services
    -> market domain
    -> integration application capabilities (source I/O only)

market composition/reference
    -> reference contract client
    -> market application reconciliation input

market domain
    -> primitives only
```

禁止形成：

```text
application -> composition
services -> composition
domain -> application/services/composition/Integration/其他业务模块
domain -> Reference contract 或任何 `Reference*` 类型
services -> Reference SQLite reader/path/schema
Market production code -> Integration services、SDK 或 raw provider payload
process -> provider-specific constructor
其他业务模块 -> Market services 或私有 domain 路径
```

## 4. 结构原则

### 4.1 完整目录模块

已经包含多个职责、具有稳定边界或需要继续细分的概念使用目录模块：

```text
application/process/mod.rs
application/process/lifecycle.rs
```

不继续使用：

```text
application/process.rs
application/process_lifecycle.rs
```

`mod.rs` 定义模块边界、核心类型、可见性和有限导出，不应重新膨胀成实现集合。短小且没有子职责的叶子概念仍可保留为普通 Rust 文件。

### 4.2 按 Market 的真实职责拆分

只有满足以下条件之一才创建子模块：

1. 已有代码可以迁入；
2. 当前存在两个以上可区分的职责；
3. 存在明确调用者和稳定边界；
4. 拆分后能够删除原混合职责或旧路径。

禁止为了与 Execution 对称而创建空目录、占位 trait、source manager、feed engine、registry 或 compatibility facade。

### 4.3 删除 `facade`/`service` 含糊命名

不保留 application 内的 `facade.rs` 与 `service.rs` 分工：

- `application/mod.rs` 定义 `MarketApplication` 和经过审查的公开导出；
- subscriptions、observations、market-universe、queries 和 sources 子目录共同为同一个 `MarketApplication` 提供 `impl`；
- `MarketError` 进入 `application/model/error.rs`；
- 不增加 `MarketService`、`MarketEngine` 或新的 facade。

### 4.4 Observation 目录对称

每个公开 `ObservationKind` 使用一个同名目录模块。Quote、Trade、Bar、FundingRate、OrderBook 等在领域层级上完全平等，不用 `quote.rs` 与 `order_book/` 的形态差异暗示 Order Book 是更高层领域。

目录对称遵循以下规则：

1. `domain/observation/<kind>/` 是每个 Observation kind 的稳定定义位置；
2. 当该 kind 存在 application ingestion/projection 行为时，使用 `application/observations/<kind>/`；
3. 当该 kind 存在 Actor 状态转换时，使用 `services/actor/observations/<kind>/`；
4. 三层使用相同 kind 名称，形成可顺向查找的纵向路径；
5. 目录层级对称，目录内部文件数量不要求对称；
6. 简单 Observation 可以只在 `mod.rs` 中定义其值和规则，复杂后再在目录内拆分；
7. Order Book 可以拥有 `book.rs`、`delta.rs`、`continuity.rs` 等更多文件，但仍与 `quote/`、`trade/`、`bar/` 平级；
8. 不使用 `derivatives.rs` 聚合多个独立 Observation kind。MarkPrice、FundingRate、OpenInterest、OptionGreeks 等各自拥有目录，其适用市场由 capability rule 表达。

该规则不是为视觉对称创建空模块。目标目录中的 kind 模块必须至少承载该 kind 的类型定义、校验、更新策略或真实调用入口；没有 layer-specific 行为时，不创建只做 re-export 的空目录。

## 5. 目标结构

以下结构表达最终边界，子文件只在真实代码迁入时创建，不预建空文件：

```text
crates/modules/market/
  contract/
  src/
    lib.rs

    bin/
      kairos-market-server.rs
      kairos-market-cli.rs

    application/
      mod.rs

      model/
        mod.rs
        command.rs
        query.rs
        result.rs
        event.rs
        view.rs
        checkpoint.rs
        error.rs

      subscriptions/
        mod.rs
        static_subscription.rs
        dynamic_subscription.rs
        lifecycle.rs
        resolution.rs

      observations/
        mod.rs
        projection.rs
        source_inputs.rs
        quote/
          mod.rs
        trade/
          mod.rs
        bar/
          mod.rs
        trade_bar/
          mod.rs
        quote_bar/
          mod.rs
        ticker_24h/
          mod.rs
        option_greeks/
          mod.rs
        rate/
          mod.rs
        mark_price/
          mod.rs
        index_price/
          mod.rs
        funding_rate/
          mod.rs
        open_interest/
          mod.rs
        order_book/
          mod.rs
          projection.rs
          continuity.rs
          resync.rs

      universe/
        mod.rs
        reconciliation.rs
        recovery.rs

      queries/
        mod.rs
        observations.rs
        order_books.rs
        freshness.rs
        execution_estimate.rs

      sources/
        mod.rs
        attachment.rs
        subscriptions.rs
        recovery.rs

      process/
        mod.rs
        lifecycle.rs
        actor_task.rs
        ingress.rs
        maintenance.rs
        market_universe.rs
        recovery.rs
        publication.rs
        shutdown.rs

      replay/
        mod.rs
        model.rs
        loader.rs

    composition/
      mod.rs

      config/
        mod.rs
        dto.rs
        runtime.rs
        sources.rs
        defaults.rs

      runtime/
        mod.rs
        process.rs
        diagnostic.rs

      sources/
        mod.rs
        routing.rs
        activation.rs
        binance.rs
        okx.rs
        hyperliquid.rs
        massive.rs
        replay.rs

      reference/
        mod.rs
        client.rs
        events.rs
        projection.rs

      history/
        mod.rs
        jsonl.rs

      publication/
        mod.rs
        views.rs
        events.rs
        encoding.rs

    services/
      mod.rs

      actor/
        mod.rs
        subscriptions.rs
        observations/
          mod.rs
          views.rs
          quote/
            mod.rs
          trade/
            mod.rs
          bar/
            mod.rs
          trade_bar/
            mod.rs
          quote_bar/
            mod.rs
          ticker_24h/
            mod.rs
          option_greeks/
            mod.rs
          rate/
            mod.rs
          mark_price/
            mod.rs
          index_price/
            mod.rs
          funding_rate/
            mod.rs
          open_interest/
            mod.rs
          order_book/
            mod.rs
            continuity.rs
        sources.rs
        freshness.rs
        universe/
          mod.rs
        read_model.rs
        checkpoint.rs
        events.rs

      source/
        mod.rs
        messages.rs
        driver.rs
        stream.rs
        snapshot.rs
        replay.rs
        normalization.rs
        recovery.rs

      control/
        mod.rs
        transport.rs
        wire.rs
        ingress.rs
        response.rs

      publication/
        mod.rs
        fanout.rs
        queue.rs

    domain/
      mod.rs

      observation/
        mod.rs
        identity/
          mod.rs
          kind.rs
          key.rs
          qualifier.rs
          capability.rs
        quote/
          mod.rs
        trade/
          mod.rs
        bar/
          mod.rs
        trade_bar/
          mod.rs
        quote_bar/
          mod.rs
        ticker_24h/
          mod.rs
        option_greeks/
          mod.rs
        rate/
          mod.rs
        mark_price/
          mod.rs
        index_price/
          mod.rs
        funding_rate/
          mod.rs
        open_interest/
          mod.rs
        order_book/
          mod.rs
          book.rs
          level.rs
          delta.rs
          continuity.rs

      subscription/
        mod.rs
        intent.rs
        member.rs
        selector.rs
        status.rs

      source/
        mod.rs
        identity.rs
        route.rs
        state.rs
        readiness.rs

      freshness/
        mod.rs
        status.rs
        evaluation.rs

      market/
        mod.rs
        resolved.rs
        data_route.rs
        selection.rs

  tests/
    architecture.rs
    application/
    process/
    composition/
    behavior/
```

## 6. 各层职责

### 6.1 `bin`

允许解析 CLI 参数和环境、解析 workspace 资源、调用 composition、启动 `MarketProcess`，以及在 one-shot diagnostic CLI 中调用经过审查的 application API。

禁止直接修改 Actor、复制 subscription/ingestion/query 用例、构造 production provider SDK client，或成为第二个 Market facade。历史下载若属于 Integration diagnostic capability，应保持显式边界，不进入 Market 业务 runtime。

### 6.2 `composition`

负责：

- provider-native Integration capability 的选择和构造；
- Market-owned source route 与 provider/product 的映射；
- Reference contract reader/event source 和只读 projection 的组装；
- history、publisher、transport 和运行配置的具体实现；
- live、paper、replay、diagnostic 模式；
- `MarketApplication` 与 `MarketProcess` 的最终构造。

Provider 子模块只能消费 Integration application 暴露的 capability，不复制认证、协议交互、raw payload normalizer 或技术 quota 逻辑。

### 6.3 `application`

负责公开业务边界和用例编排：

- subscription command、query、result、event、view、checkpoint 和 error；
- static/dynamic subscription lifecycle；
- normalized observation ingestion，包括 Order Book snapshot/delta；
- market-universe/source reconciliation；
- typed Market query；
- process runtime facade；
- Actor、source driver、publisher 和 history capability 的调用顺序。

Application API 不暴露 SDK client、raw provider payload、transport request、composition config、services 实例、persistence/history record 或 `serde_json::Value` 业务模型。

### 6.4 `services`

负责私有机制：

- 单一 Actor 及其状态转换；
- source command/input channel、bounded driver 和 reconnect/resync 机制；
- Integration application capability 到 typed `SourceInput` 的 I/O 驱动；
- control transport、wire decoding 和 response encoding；
- publication fanout、queue 和 backpressure。

Services 不得依赖 composition，也不得成为其他模块、CLI 或 server 的直接业务入口。

### 6.5 `domain`

负责纯业务实体、值对象和不变量：

- 每个公开 Observation kind 的平级目录模块，其中包括与 Quote、Trade、Bar 平级的 Order Book；
- typed `ObservationKind`、qualifier 和 observation capability rule；
- Order Book observation 特有的 snapshot、delta 和 continuity 规则；
- subscription intent、selector、member requirement 和状态派生；
- source identity、route、state、epoch 和 readiness；
- freshness 状态与纯计算；
- `ResolvedMarket`、`MarketDataRoute` 和 selection query。

Domain 不依赖 transport、persistence/history、Integration、Reference contract、其他业务模块或 application facade。Domain 不定义 `ReferenceChanged`、`ReferenceProjection` 或其他以外部模块命名的输入；上游事实进入 Domain 前必须转换为 Market-owned language。

## 7. 关键设计决定

### 7.1 保留单一 `MarketApplication`

不同用例按目录拆分，但共同实现一个 `MarketApplication`。不增加 `SubscriptionApplication`、`MarketQueryApplication`、`MarketEngine` 或 `SourceManager`。

### 7.2 保留单一 `MarketActor`

Actor 子文件只是同一类型的状态转换实现。subscription、source、各类 observation（包括 Order Book）和 freshness 需要共享 generation、event sequence 和一致性边界，不能拆成多个状态 owner。

需要在 Phase 0 明确记录 Actor 当前拥有的 operational source state。若未来要把 channel handle/task ownership 从 Actor 分离，必须作为独立语义设计处理，不能在本次目录重构中顺便改变。

### 7.3 Process 与 control wire 分离

`application/process/` 保留 lifecycle、typed ingress dispatch、Actor task、maintenance timers、Reference recovery、publication draining 和 shutdown。

`services/control/` 承担 Axum/UDS transport、JSON envelope/payload parsing、schema validation、operation classification 和 response encoding。Process 只接收类型化 operation，不直接使用 `SubscribePayload`、`CommandEnvelope<T>` 或 `serde_json::Value` 表达业务命令。

JSON 可保留在显式 control/config/history 边界，但不得成为 Market application/domain 模型适配器。

### 7.4 Provider 只存在于 composition 和 Integration

具体 Binance、OKX、Hyperliquid、Massive connection 类型只允许出现在 `composition/sources/<provider>.rs` 或 Integration。

`services/source/stream.rs` 和 `snapshot.rs` 可以依赖 Integration application 的最小 async capability trait，但不能依赖 `participants::<provider>` 具体类型。现有 `services/source/binance.rs` 应先搜索调用者：若只包装通用 driver，迁入 composition 或删除；不得扩展成 provider registry。

### 7.5 Replay 不是 provider connection

Replay 是 Market-owned source driver：

- replay clock、checkpoint 和 completion 是 Market replay 语义；
- composition 负责从 DTO 转换为 application/services 可用的 replay model；
- `services/source/replay.rs` 不得依赖 composition config；
- replay 不伪装成 Integration provider connection。

### 7.6 Reference projection 的定位

Reference canonical state 不属于 Market。Market 只维护 dynamic subscription reconciliation 所需的 `ResolvedMarket` 集合和已消费上游 watermark，不维护完整 Reference catalog，也不把该集合命名为 Reference projection。

具体 Reference client、view/event reader、资源路径解析、watermark catch-up 和 contract-to-Market mapping 全部属于 `composition/reference/`。application 只接收 `ReconcileMarketUniverse`；services/domain 不依赖 `kairos-reference-contract`，不持有 Reference SQLite 路径，也不执行 Reference market/instrument/access join。

推荐恢复流程为：Reference event 只作为 invalidation signal；composition 随后通过 `ReferenceClient::view()` 读取 current view，并确认 view watermark 不落后于事件 sequence；满足水位后映射为 `ResolvedMarket` 集合并调用 application reconciliation。若现有 Reference current view 无法高效提供 Market 所需的窄投影，应扩展 Reference contract，而不是让 Market 直接读取 Reference persistence。

### 7.7 Publication 与 contract encoding

publication 分为三类责任：

- application/process 只表达待交付的 typed `MarketChange` 与 `MarketViewUpdate`；
- services 负责 bounded queue、fanout、ordering、backpressure 和 client lifecycle；
- composition 选择 mmap/Aeron/UDS 等具体 publisher；
- contract crate 拥有 wire schema 及适合复用的 encode/decode。

现有 `composition/publisher.rs` 与 `services/event_wire_v2.rs` 应按上述边界归并。业务 view/event 必须直接映射为 contract-owned 类型，不使用 JSON round trip。若只有一个生产 publication service，不在 application 创建只为测试 fake 服务的 publisher trait。

### 7.8 Domain 公开 API 收敛

目标不是立即隐藏所有 Market domain 类型。`MarketObservation`、`OrderBook` 等可能是 application request/result 的合法业务类型，但应由 application 有选择地导出，而不是公开整个 `domain` 模块。

收紧前必须搜索 bin、tests、bench 和真实跨 crate 调用者；迁移完成后删除旧路径，不长期保留兼容 re-export。

### 7.9 删除 `domain/reference`

`ReferenceChanged` 不是 Market domain event。它表示外部模块状态发生变化，且同时携带上游 watermark 与 Market descriptor 集合，属于 application reconciliation input。第二阶段将其替换为 Market-owned `ReconcileMarketUniverse`，随后删除 `domain/reference/` 及 crate-root `ReferenceChanged` re-export。

Market domain 可以记录 `upstream_generation`/`upstream_sequence` 作为 reconciliation evidence，但字段或类型不应绑定具体 transport、数据库或 Reference client。这样未来 Reference contract 的交付方式变化不会改写 Market domain。

### 7.10 `MarketDescriptor` 收敛为已解析市场路由

当前 `MarketDescriptor` 同时包含 canonical identity、Reference 兼容字段、provider access 与可选 runtime source，含义过宽。第二阶段将其收敛为组合完成后的 Market-owned input：

```rust
pub struct ResolvedMarket {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub instrument_kind: InstrumentKind,
    pub route: MarketDataRoute,
}

pub struct MarketDataRoute {
    pub access_id: String,
    pub provider_id: ProviderId,
    pub provider_product: ProviderProductCode,
    pub provider_symbol: ProviderSymbol,
}
```

具体字段以真实 subscription/source caller 为准，不为示意图补字段。`market_type`、`source_symbol` 等兼容输入只允许出现在 composition mapping；迁移后从 Domain 删除。Market runtime `source_id` 由 Market composition/activation 分配，不能与 Reference ingestion provenance 或 provider ID 混用。

### 7.11 Snapshot、View、Checkpoint 与 Change 分离

禁止继续让 `MarketSnapshot` 同时承担 Actor dump、application query、replay restore 和 publication input。第二阶段采用以下语义：

- application query 返回 typed result 或 `MarketView`，不克隆完整 Actor state；
- replacement publication 编码 `MarketViewUpdate`/typed current view；
- replay 需要完整恢复时使用私有或 replay-scoped `ReplayCheckpoint`；
- Actor 内部状态不通过 crate root 导出；
- `MarketChange` 携带 event sequence 并进入 event/change publication；
- Order Book 的 provider/bootstrap snapshot 保留 `OrderBookSnapshot` 术语。

`MarketCurrentView` 与 `MarketSnapshot` 不长期并存为两份字段近似相同的模型。迁移期先统计真实调用者，再让每个调用者转向 typed view 或 replay checkpoint，最后删除泛化 `MarketSnapshot`。

### 7.12 Current projection 只有一个权威

按 source + market 保存“最后到达的任意 observation”的 `latest` 没有稳定业务含义：其类型取决于消息到达顺序。按 source + market + observation kind + qualifier 标识的 `views` 才是可查询的 current projection。

第二阶段删除 `latest` map、`latest()`/`latest_from_source()` compatibility query 和对应 contract/publication 字段。Quote、Trade、Bar、Rate 等通过 typed view key 读取。Order Book 可保留专门数据结构以维护 continuity，但 key、freshness、watermark 和对外交付遵循同一 view identity。

### 7.13 Observation kind 与 market-kind capability 类型化

subscription selector、`MarketViewKey.kind`、freshness data kind 和 observation capability 不再依靠自由字符串互相匹配。引入一个 Market-owned `ObservationKind`，并为 timeframe、rate ID 等建立显式 qualifier。contract-owned enum 在 contract mapping 边界转换，不直接泄露进 Domain。

`ObservationKind` 与目录一一对应。目标至少覆盖：

```text
quote
trade
bar
trade_bar
quote_bar
ticker_24h
option_greeks
rate
mark_price
index_price
funding_rate
open_interest
order_book
```

以上 kind 在 `domain/observation/` 下平级；存在 application 或 Actor 专属行为时，在对应层使用相同目录名。禁止继续采用 `quote.rs`、`trade.rs`、`derivatives.rs` 与 `order_book/` 混合的非对称结构，也禁止通过 `derivatives.rs` 把多个不同 view identity 合并成含糊类别。

Spot、Perpetual、Future、Option 当前只有静态 `supports()` allowlist，不构成四个独立领域对象。将其收敛为 `ObservationCapabilities::for_instrument_kind(kind)` 或等价纯规则。只有当某一 market kind 出现真实独立状态、不变量和调用者时，才重新建立对应子模块。

### 7.14 Publication 按真实语义命名

发布 `MarketChange` 的能力不得命名为 `MarketSnapshotPublisher`。第二阶段将其改为具体 `MarketChangePublisher`/publication service，或直接由 process 持有现有具体 service。若只存在一个生产实现，不为测试 fake 保留 application-owned publisher trait。current view replacement publication 与 ordered event/change publication保持两个明确通道。

## 8. 现有文件迁移映射

| 当前路径 | 目标模块 | 说明 |
| --- | --- | --- |
| `application/facade.rs` | `application/sources/`、`observations/`、`observations/order_book/`、`universe/` | 保留单一 `MarketApplication`，删除 facade 命名 |
| `application/service.rs` | `application/subscriptions/`、`observations/`、`queries/`、`model/error.rs` | 删除含糊的 service 命名 |
| `application/query.rs` | `application/model/query.rs`、`application/queries/` | 分离 query result model 与查询实现 |
| `application/process.rs` | `application/process/` 与 `services/control/` | Process 逻辑按职责拆分，wire parsing 移出 application |
| `application/replay.rs` | `application/replay/` | 分离 replay model 和 loader；JSONL 保持显式文件边界 |
| `composition/config.rs` | `composition/config/` | DTO、profile model、source config 和 defaults 分离 |
| `composition/process.rs` | `composition/launch/process.rs` | 顶层 process 构造 |
| `composition/diagnostic.rs` | `composition/launch/diagnostic.rs` | one-shot CLI 专用组装 |
| `composition/mod.rs` source 构造 | `composition/sources/` | provider-by-provider 迁移并删除旧 constructor |
| `composition/mod.rs` route 匹配 | `composition/sources/routing.rs` | 保留 Market-owned source route 语义 |
| `composition/reference.rs` | `composition/reference/events.rs` | 具体 Aeron Reference event source |
| `services/reference_projection.rs` | 第一阶段暂迁 `services/reference/projection.rs`；第二阶段删除 | 第一阶段保持行为；第二阶段由 composition 的 Reference contract client 取代，Market 不再读取 SQLite |
| `composition/publisher.rs` | `composition/publication/` | publisher、snapshot/event encoding 分离 |
| `services/event_wire_v2.rs` | contract encode 或 `composition/publication/encoding.rs` | 按复用边界定位，不保留重复 encoder |
| `services/event_publication.rs` | `services/publication/` | fanout、queue、client lifecycle |
| `services/control.rs` | `services/control/` | transport、wire、ingress、response 分离 |
| `services/messages.rs` | `services/source/messages.rs` | source driver 私有 typed messages |
| `services/source/stream.rs` | `services/source/stream.rs`、`normalization.rs`、`recovery.rs` | 通用 async driver 职责拆分 |
| `services/source/snapshot.rs` | `services/source/snapshot.rs`、`recovery.rs` | snapshot driver 与失败恢复分离 |
| `services/source/replay.rs` | `services/source/replay.rs` | 消除对 composition 的依赖 |
| `services/source/binance.rs` | `composition/sources/binance.rs` 或删除 | provider concrete type 不留在 services |
| `services/history.rs` | `composition/history/jsonl.rs` | 具体 JSONL 实现由 composition 选择 |
| `services/actor.rs` | `services/actor/` | 保持单一 Actor，按状态转换职责拆分 |
| `domain/observations.rs` | `domain/observation/` | 按 quote/trade/bar/derivatives/order_book 组织 |
| `domain/orderbook.rs` | `domain/observation/order_book/` | Order Book 是 Observation；子模块表达其 book/delta/continuity 附加机制 |
| `domain/subscriptions.rs` | `domain/subscription/` | intent/member/selector/status |
| `domain/source.rs` | `domain/source/` | identity/route/state/readiness |
| `domain/freshness.rs` | `domain/freshness/` | status/evaluation |
| `domain/market.rs` | `domain/market/` | descriptor/selection |

第二阶段在第一阶段结果上继续执行以下语义迁移：

| 当前路径/概念 | 目标模块/概念 | 删除项与边界 |
| --- | --- | --- |
| `domain/reference/ReferenceChanged` | `application/universe/ReconcileMarketUniverse` | 删除 `domain/reference/` 和 crate-root `ReferenceChanged` 导出 |
| `services/reference/ReferenceProjection` | `composition/reference/client.rs`、`projection.rs` | services 不再持有 SQLite path，不再依赖 `ReferenceSqliteReader` |
| `services/reference/resolution.rs` | application subscription resolution 或 composition projection | provider/contract mapping 留在 composition；纯 Market selection rule 留在 application/domain |
| `MarketDescriptor` | `ResolvedMarket` + `MarketDataRoute` | 删除 Domain 中的 `market_type`、`source_symbol` 等兼容字段 |
| `domain/market/{spot,perpetual,future,option}.rs` | `domain/observation/capability.rs` | 删除无状态 wrapper type，以 typed capability rule 替代 |
| string selector / `MarketViewKey.kind: String` | `ObservationKind` + typed qualifier | 删除跨模块字符串分派和别名兼容 |
| `domain/observation/{quote.rs,trade.rs,bar.rs,derivatives.rs}` | `domain/observation/<kind>/mod.rs` | 每个公开 Observation kind 独立平级目录；删除 `derivatives.rs` 聚合 |
| application/Actor 中的通用 observation 实现 | `application/observations/<kind>/`、`services/actor/observations/<kind>/` | 有真实 kind-specific 行为时使用与 Domain 相同名称，不创建空 re-export 模块 |
| Actor `latest` + `latest_views` | 单一 current `views` projection | 删除 `latest` map、query、snapshot/view/contract 字段 |
| `MarketSnapshot` + `MarketCurrentView` | typed `MarketView` + replay-scoped `ReplayCheckpoint` | 删除泛化 Actor snapshot 公共 API |
| `MarketSnapshotPublisher` | `MarketChangePublisher` 或具体 publication service | 名称与输入语义一致；无真实多实现时删除 application-owned trait |

## 9. 公开 API 收敛

目标 `lib.rs` 表达推荐入口：

```rust
pub mod application;
pub mod composition;

mod domain;
mod services;

pub use application::{
    MarketApplication,
    MarketError,
    MarketProcess,
    // 经过审查的业务 request/result/event 类型
};
```

约束：

- 跨模块调用者优先依赖 `kairos-market-contract`；需要同进程业务调用时只使用 Market application API；
- `domain` 默认私有，确实构成 application request/result 的业务类型由 application 有选择地导出；
- `services` 保持私有；
- `composition` 只暴露顶层 compose request/result 和明确的 diagnostic 入口；
- provider enum、source handle、driver、projection、publisher concrete type 默认不从 crate root 导出；
- bench、tests 和 CLI 不能成为永久保留过宽公共 API 的理由，应迁移到审查后的入口。

## 10. 分阶段迁移计划

### Phase 0：基线和架构保护

1. 记录 `MarketApplication`、`MarketActor` 和 `MarketProcess` 的字段及状态职责；
2. 搜索 crate root/domain/composition 公开 API 的真实调用者；
3. 记录 source command/input ordering、subscription correlation、generation 和 event sequence 规则；
4. 记录 stream reconnect、order book resync、freshness 和 publication backpressure 行为；
5. 增强 architecture tests；
6. 运行完整基线检查并区分既有失败。

架构检查至少覆盖：

- `MarketActor` 是唯一 Market 可变状态 owner；
- application 和 services 不导入 composition；
- domain 不导入 Integration、Reference contract、transport、history/persistence 或其他业务模块；
- provider-specific 类型不进入 application/domain/services；
- process 不解析 JSON wire、不构造 provider connection；
- bin 不实现 subscription/ingestion/query 用例；
- business publisher 不使用 JSON model round trip；
- 其他 crate 不导入 Market services 或私有 domain 路径。

### Phase 1：Application model 与查询模块化

1. 建立 `application/model/`；
2. 迁移 query/result/snapshot/event/error 类型；
3. 建立 `application/queries/` 并迁移查询实现；
4. 保持公开类型名称和行为；
5. 删除旧 `query.rs`，不保留转发模块。

这是低风险结构迁移。

### Phase 2：Application use case 模块化

1. 将 static/dynamic subscription 用例迁入 `application/subscriptions/`；
2. 将 observation/source input 用例迁入 `application/observations/`；
3. 将 Order Book observation 的 continuity/resync 机制迁入 `application/observations/order_book/`；
4. 将 Reference/source recovery 迁入 `application/universe/` 与 `application/sources/`；
5. 将 source attachment/subscription synchronization 迁入 `application/sources/`；
6. 删除 `facade.rs` 和 `service.rs`；
7. 保持单一 `MarketApplication` 和 `MarketActor`。

本阶段不改变 source channel、Actor 状态或事件语义。

### Phase 3：Process 模块化与 control 分离

1. 建立 `application/process/`；
2. 先迁移 lifecycle、maintenance 和 shutdown；
3. 再迁移 Actor task、Reference recovery 和 publication draining；
4. 将 HTTP/UDS/JSON envelope、payload parsing 和 response encoding 移至 `services/control/`；
5. Process 改为接收 typed control operation；
6. 删除旧 `application/process.rs`。

这是最高风险阶段，应单独提交并重点验证 command idempotency、owner release、Reference recovery、timer fairness、bounded queues 和 shutdown。

### Phase 4：Source driver 边界整理

1. 迁移私有 source messages；
2. 拆分 stream normalization、subscription commands、recovery 和 resync；
3. 拆分 snapshot driver 和 recovery；
4. 将 replay clock/model 移出 composition dependency；
5. 删除或迁移 provider-specific services 文件；
6. 验证 services 只依赖 Integration application capability。

该阶段不能改变 command/query/stream 的 retry 与 delivery semantics。命令在可能发送后不得透明重试；stream 必须保持 ordering、reconnect、backpressure 和 resync 行为。

### Phase 5：Composition 模块化

1. 拆分 config DTO、profile model、source binding 和 defaults；
2. 抽取 source routing/activation；
3. 按 Binance、OKX、Hyperliquid、Massive 顺序迁移 provider composition；
4. 迁移 Reference reader/event source；
5. 迁移 history 和 publication concrete implementation；
6. 将 `composition/mod.rs` 缩减为顶层组装和有限导出；
7. 每迁移一段立即删除原 constructor。

Provider 迁移必须遵循 `docs/integration-session-and-operation-design.md`。若迁移或重写了上游 adapter 逻辑，按仓库规则更新 `docs/integration-adapter-references/`。

### Phase 6：Publication 收敛

1. 固定 Market change/event/snapshot 的 typed mapping；
2. 将 queue/fanout/backpressure 留在 services；
3. 将具体 transport/publisher 选择留在 composition；
4. 将可复用 wire encode/decode 归入 contract；
5. 删除重复 event/view encoder；
6. 验证 wire compatibility、sequence、client disconnect 和慢消费者行为；
7. 验证 best-effort 语义：失败时暴露 gap/stale/unavailable，恢复时 resync 并继续交付，不暗示零丢失或持续可用保证。

### Phase 7：Actor 模块化

依次迁移 subscriptions、observations（其中包含 Order Book 的附加投影机制）、sources、freshness、reconciliation、snapshot 和 events 实现。

Actor 最后迁移，因为它是状态核心。只允许为同一个 `MarketActor` 拆分 `impl`，不得移动字段所有权、复制 map、改变 generation/event sequence 或建立第二 mailbox owner。

### Phase 8：Domain 模块化

1. 按 observation、subscription、source、freshness、market 迁移，其中 Order Book 归入 observation 子模块；
2. 保持 business invariants 和类型名称；
3. 明确 domain 类型的 application 导出；
4. 删除不再有调用者的旧 re-export。

### Phase 9：公开 API 收敛与清理

1. 迁移 tests、bench、bin 和真实外部调用者；
2. 将 domain/services 默认私有；
3. 收紧 composition 导出；
4. 删除 compatibility path、重复 constructor、无调用者 trait 和旧模块；
5. 更新架构文档和 migration status。

以上 Phase 0-9 是第一阶段目录重构。第二阶段按以下切片继续，不与第一阶段“纯移动”提交混合。

### Phase 10：Reference client 外置与 application input 改名

1. 在 `composition/reference/` 使用 `kairos_reference_contract::ReferenceClient` 读取 events 和 current view；
2. event 只触发 invalidation，读取的 view watermark 必须不落后于触发事件 sequence；
3. 在 composition 完成 contract market、instrument、MarketDataAccess 到 `ResolvedMarket` 的 typed mapping；
4. application 新增 `reconcile_market_universe`，只接收 Market-owned type 和上游 watermark；
5. 迁移 dynamic subscription、process recovery 和测试；
6. 删除 `services/reference/ReferenceProjection`、Reference SQLite path、`domain/reference/ReferenceChanged` 及旧导出。

若 Reference current view 暂时无法提供所需数据，本阶段先扩展 `kairos-reference-contract` 的窄 Market projection；不得以继续直接读 SQLite 作为最终状态。

### Phase 11：Resolved Market 与 route 收敛

1. 记录 `MarketDescriptor` 每个字段的真实 caller；
2. 建立最小 `ResolvedMarket` 和 `MarketDataRoute`；
3. composition 显式转换 canonical identity、instrument kind 和 active MarketDataAccess；
4. source activation 只消费 resolved route，不推断 provider product/symbol；
5. 删除 `market_type`、`source_symbol`、Reference ingestion `source_id` 等兼容字段；
6. 使用 missing/ambiguous access 测试固定失败语义。

### Phase 12：Observation identity 与 capability 类型化

1. 建立 `ObservationKind` 和必要 typed qualifier；
2. 为每个公开 kind 建立 `domain/observation/<kind>/`，把 Quote、Trade、各类 Bar、Ticker24h、OptionGreeks、Rate、MarkPrice、IndexPrice、FundingRate、OpenInterest、OrderBook 迁入平级目录；
3. 将真实 kind-specific ingestion/projection 和 Actor update 迁入同名的 `application/observations/<kind>/`、`services/actor/observations/<kind>/`；
4. 删除 `quote.rs`、`trade.rs`、`bar.rs`、`derivatives.rs` 等非对称旧文件，不保留转发模块；
5. 迁移 selector validation、view key、freshness key、query 和 publication mapping；
6. 将 Spot/Perpetual/Future/Option 的静态 allowlist 收敛为一处 capability rule；
7. 删除 string alias 分派和无状态 market-kind wrapper；
8. 用每种 `InstrumentKind` 的支持/拒绝矩阵，以及每个 `ObservationKind` 的 domain/application/Actor 路径检查固定行为。

### Phase 13：Current view 单一权威

1. 搜索 `latest`、`views`、`order_books` 的生产和消费位置；
2. 将所有 typed query 迁移到 view identity；
3. 先停止写入 `latest`，以 focused tests 验证无行为依赖；
4. 删除 `latest` map、query API、publication/contract 字段和兼容测试；
5. 保留 Order Book 专门存储，但统一 view key、freshness 和 watermark；
6. 验证多 source 时 source-agnostic query 继续拒绝 ambiguous result。

### Phase 14：Snapshot/View/Checkpoint 与 publication 语义收敛

1. 分类所有 `MarketSnapshot` 调用者为 query、publication、replay/recovery 或 diagnostic；
2. query 改为 typed result，publication 改为 typed view/change；
3. replay/recovery 建立最小 `ReplayCheckpoint`，不通过 crate root 暴露 Actor state；
4. 删除 `MarketCurrentView`/`MarketSnapshot` 重复模型中的一个，最终删除泛化 snapshot API；
5. 将错误命名的 `MarketSnapshotPublisher` 收敛为 change publication service；
6. 若 contract wire 需要兼容，先保持旧 schema reader，再以独立 contract migration 删除旧字段，不在 Actor 里维持双状态。

## 11. 每阶段迁移规则

每个阶段必须满足：

1. 先确认业务 owner、状态 owner、调用者和目标层；
2. 优先做无行为变化的移动；
3. 不在同一提交中同时重写业务语义和目录；
4. 不建立过渡 manager、engine、registry 或 facade；
5. 新路径通过测试后立即删除旧路径；
6. 一个提交只处理一个可审查的职责切片；
7. provider 迁移一次只处理一个 provider/product/capability slice；
8. 涉及并发时保持 channel ownership、capacity、ordering、fairness 和 shutdown 顺序；
9. 发现边界不清时先更新本提案或权威设计，不用兼容层掩盖。

## 12. 明确不做的事情

- 不新增 `market-service`、`market-runtime`、`market-domain` 等 Cargo crate；
- 不新增 `MarketManager`、`MarketEngine`、`SourceManager` 或通用 provider registry；
- 不拆分 `MarketActor` 的业务状态所有权；
- 不创建多个 application facade；
- 不把 Market source route 和 subscription intent 放入 Integration；
- 不把 provider connection、raw payload 或 SDK 类型放入 application/domain；
- 不把顶层 Reference 模块合入 Market；
- 不在 Market 内重新实现 Reference client、catalog、SQLite reader 或 catalog join；
- 不创建 application-owned `ReferenceGateway`/`ReferencePort` 镜像 trait；优先使用 Reference contract 已有 client boundary；
- 不把 replay 包装成 provider connection；
- 不在本次结构重构中改变 observation normalization、order book continuity 或 subscription 业务语义；
- 不为目录对称创建空模块和占位 trait；
- 不使用 JSON 序列化往返映射 business event/snapshot；
- 不为了内部模型收敛在同一切片顺便重设计 contract wire format；确需删除旧 wire 字段时作为独立兼容迁移。

## 13. 验收标准

### 13.1 可理解性

开发者应能仅凭目录回答：

1. 谁修改 Market 状态：仅 `services/actor/` 中的 `MarketActor`；
2. subscription 用例在哪里：`application/subscriptions/`；
3. 每种 observation 的定义、用例和状态转换在哪里：沿 `domain/observation/<kind>/`、`application/observations/<kind>/`、`services/actor/observations/<kind>/` 顺向查找；
4. Order Book observation 的 continuity/resync 在哪里：`domain/observation/order_book/`、`application/observations/order_book/` 与 `services/actor/observations/order_book/`；
5. typed query 在哪里：`application/queries/`；
6. stream reconnect 在哪里：`services/source/`；
7. Binance/OKX 如何组装：`composition/sources/`；
8. Reference 如何接入：仅 `composition/reference/` 使用 contract client，application 接收 `ReconcileMarketUniverse`；
9. UDS/JSON control 在哪里：`services/control/`；
10. publication queue 与具体 wire/publisher 分别在哪里：services、contract/composition。

### 13.2 文件和模块边界

- `application/process.rs`、`application/facade.rs`、`application/service.rs`、`services/actor.rs` 的混合职责被移除；
- `composition/mod.rs` 不再包含各 provider 的具体 constructor；
- `services/source/replay.rs` 不再依赖 composition；
- provider-specific services 路径被删除；
- publication 不再存在职责重叠的巨型 event/view encoder；
- `mod.rs` 主要承担边界和导出；
- 没有为目录对称创建空模块；每个 kind 目录至少有类型、规则、更新策略或真实入口；
- 不存在 `domain/reference/` 和 `services/reference/ReferenceProjection`；
- 不存在同时写入的 `latest` 与 `views` 两份 observation current projection；
- 不存在仅包装静态 `supports()` 的 Spot/Perpetual/Future/Option 类型；
- 所有公开 Observation kind 在 `domain/observation/` 下使用平级目录，不存在与 `order_book/` 同层的 `quote.rs`、`trade.rs`、`bar.rs` 或聚合多个 kind 的 `derivatives.rs`；
- application 和 Actor 中存在 kind-specific 行为时使用相同目录名，不使用另一套分类词汇。

### 13.3 架构边界

- `MarketApplication` 是唯一公开业务 facade；
- `MarketActor` 是唯一可变业务状态 owner；
- application/services 不依赖 composition；
- domain 无基础设施和跨业务依赖；
- provider-specific concrete types 限制在 composition/Integration；
- `kairos-reference-contract` 和 Reference client 只出现在 composition；
- application/services/domain 不读取 Reference SQLite，不持有其路径或 schema query；
- Market domain 不定义 `Reference*` 类型；
- cross-module caller 只通过 application 或 contract；
- process 不理解 HTTP/JSON wire；
- transport/history record 不进入 application API。

### 13.4 行为保持

以下行为在迁移前后保持一致：

- static/dynamic subscribe、unsubscribe 和 owner release；
- market-universe reconciliation、上游 watermark catch-up 和成员上限；
- source route resolution、activation 和 subscription confirmation；
- duplicate/out-of-order source input 处理；
- observation typed view 更新，包括 Order Book observation；
- Order Book snapshot/delta continuity 和自动 resync；
- source epoch、status、readiness 和 reconnect；
- freshness evaluation 和 feed status；
- replay pause/resume、显式 checkpoint 和 completion；
- command idempotency；
- event/change sequence、current view publication 和 event fanout；
- bounded queue backpressure、慢消费者和 shutdown。

## 14. 验证命令

每个切片运行 focused tests，阶段结束后运行：

```text
cargo test -p kairos-market
cargo test -p kairos-market-contract
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
```

同时执行静态搜索：

```text
rg "crate::composition" crates/modules/market/src/application crates/modules/market/src/services
rg "kairos_(integration|reference|account|risk|execution)" crates/modules/market/src/domain
rg "kairos_reference_contract|ReferenceSqliteReader|SqliteMarket" crates/modules/market/src/application crates/modules/market/src/services crates/modules/market/src/domain
rg "ReferenceChanged|ReferenceProjection|domain::reference" crates/modules/market
rg "kairos_integration::participants" crates/modules/market/src/application crates/modules/market/src/services crates/modules/market/src/domain
rg "kairos_market::(services|domain)" crates --glob '*.rs'
rg "serde_json::(to_value|from_value)" crates/modules/market/src
rg "MarketSnapshot|MarketCurrentView|MarketSnapshotPublisher" crates/modules/market
rg "latest_from_source|\.latest|latest_views" crates/modules/market/src
rg 'kind: String|selectors: Vec<String>' crates/modules/market/src/domain crates/modules/market/src/application
find crates/modules/market/src/domain/observation -maxdepth 1 -type f ! -name mod.rs
rg "derivatives\.rs|mod derivatives" crates/modules/market/src
rg -l "Integration::new" crates
rg -l "ConnectionSpec" crates
rg -l "IntegrationCapability" crates
rg -l "dyn Connection" crates
```

JSON 搜索结果逐项分类：control/config/history/diagnostic 显式边界可以保留；business observation/event/snapshot publication 必须使用 typed mapping。

## 15. 风险与控制

### 15.1 Rust 可见性和公共 API

当前 tests、bench 和 CLI 使用大量 crate-root domain re-export。迁移时优先使用 `pub(crate)`，不得为了移动后编译而扩大公共 API。收紧入口前先迁移真实调用者。

### 15.2 Actor 一致性

subscription、source、各类 observation（包括 Order Book）、freshness 共享 generation 和 event sequence。拆分实现时不得把 map 或计数器移入新的 owner，也不得改变同一输入产生 change/event 的顺序。

### 15.3 Process 并发回归

Process 同时选择 control command、source input、Reference event 和 timers。拆分必须保持 select fairness、channel capacity、command result cache、shutdown timeout 和 task ownership，并用故障路径测试验证。

### 15.4 Stream 恢复与 order book continuity

stream 拆分可能改变 reconnect、resubscribe、epoch、snapshot bootstrap 和 buffered delta 顺序。必须围绕断线、乱序、重复、gap 和 resync failure 建立 focused tests。

### 15.5 Provider context 重复

composition 拆分可能重复 connection/principal context 或破坏 provider quota scope。每个 provider slice 应验证同一 binding 的共享关系、不同 route 的隔离和 credential redaction。

### 15.6 Publication compatibility

移动 encoder 可能改变 FlatBuffers union、缺省值、sequence 或 view layout。结构迁移阶段不得重设计 schema，并应以 contract decode/round-trip 和现有 consumer fixture 验证兼容性。

### 15.7 纯移动与语义修改混合

若迁移暴露现有 bug，除非阻塞迁移，不在同一提交修复。需要语义修改时另开切片，明确 owner、旧概念删除项和行为测试。

### 15.8 Reference event 与 view 水位竞态

收到 Reference event 不代表 current view 已经可见同一 sequence。composition adapter 必须读取 view metadata 并确认 `view.event_sequence >= required_sequence` 后才提交 reconciliation；超时表现为 degraded/retryable recovery，不使用旧 catalog 假装已完成。多个 event 可以合并到最高 required sequence，但不得倒退已消费水位。

### 15.9 删除 snapshot/latest 的兼容风险

`MarketSnapshot` 和 `latest` 可能被 replay、tests、CLI 或 contract encoder 隐式依赖。删除前按调用语义分类，先迁移真实消费者，再停止双写并用测试证明行为。不能通过长期保留两份 map 或每次构造完整 snapshot 来换取兼容。

### 15.10 Observation kind wire compatibility

Domain 引入 `ObservationKind` 不要求立即改变 FlatBuffers enum 或资源 key。composition/contract encoder 可以在一个明确兼容期执行 typed enum 到既有 wire value 的映射；wire 升级独立进行，并用旧 reader fixture 与新 reader round-trip 验证。

### 15.11 目录对称与空壳模块

Observation 目录对称用于稳定导航和表达同级领域概念，不能演变为无行为的三层镜像。Domain 中每个公开 kind 必须有自己的目录；Application/Actor 只有存在该 kind 的真实 ingestion、projection、validation 或 update 行为时才建立对应目录。Architecture test 校验名称一致性和禁止裸 observation 文件，但不要求每个 Domain kind 在所有层都有空目录。

## 16. 完成定义

本提案完成不是以目标目录已经创建为标准，而是同时满足：

1. 当前混合职责已经迁入明确目录模块；
2. 原裸文件、旧 constructor 和 provider-specific services 路径已经删除；
3. application/services 不再反向依赖 composition；
4. Process 接收 typed operation，不解析 control JSON；
5. 没有新增 facade、状态 owner 或通用 provider 抽象；
6. 公开 API 已收敛到 application、必要 composition 入口和独立 contract；
7. architecture tests 能持续保护依赖、状态所有权、provider 边界和 publication mapping；
8. focused tests 与仓库级检查通过，或准确记录无关的既有失败；
9. 新开发者可以从目录直接理解 Market 的业务边界、source 数据流和运行路径；
10. `domain/reference/`、Market-owned Reference SQLite reader 和 `ReferenceChanged` 已删除；
11. Reference contract fact 只在 composition 映射为 `ResolvedMarket`/`MarketDataRoute`；
12. observation current state 只有一份权威 projection，不再同时维护 `latest` 与 `views`；
13. snapshot、current view、replay checkpoint 和 ordered change 具有不同类型和调用边界；
14. observation kind/capability 使用 typed rule，Domain 不再依赖自由字符串别名和无状态 market-kind wrapper；
15. 每个第二阶段切片都删除了对应旧概念，并通过 Reference watermark、multi-source ambiguity、replay recovery、freshness 和 publication compatibility 测试；
16. 每个公开 Observation kind 在 Domain 使用独立平级目录，Application/Actor 的 kind-specific 行为通过同名路径可定位；
17. Order Book 只因内部机制复杂而拥有更多子文件，不再拥有特殊领域层级。
