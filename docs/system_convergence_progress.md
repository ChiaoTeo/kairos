# 系统架构收敛实施进度

本文记录 `system_architecture_convergence_blueprint.md` 的实际落地状态。只有代码、测试和运行证据已存在的项目才标记完成。

## 当前基线

- 开始改造前：250 tests passed，3 external integration tests skipped。
- 当前：331 tests passed，3 external integration tests skipped，compileall、Catalog strict health 和 `git diff --check` 通过。
- 工作区包含改造开始前已有的数据平台变更；实施过程避免覆盖无关用户改动。

## 已完成

### 异步数据流 M1 基础

- [x] 新增 `kairos.contracts`，建立 transport-independent `CanonicalEventEnvelope`。
- [x] Quote、Trade 和 Bar 使用 typed payload；其余旧事件类型通过 immutable compatibility payload 迁移，禁止向新 Runtime 暴露可变 Mapping。
- [x] 既有 persisted `MarketEventEnvelope` 可稳定转换为 Canonical Event，相同输入产生相同 message ID。
- [x] 新增异步 `EventSource` 和有限 iterable source。
- [x] 新增有界事件通道，支持阻塞背压、显式 fail、drop-oldest gap evidence 和按 key conflated latest value。
- [x] Channel close 会排空已接收事件，不会为了关闭静默丢弃队列内容。
- [x] `ReplayEventFeed` 同时提供兼容同步迭代与统一异步 Canonical EventSource。
- [x] 新增 Canonical Bar Projection；现有 SMA 策略使用 batch BarSeries 与 async Canonical EventSource 得到完全相同结果。
- [x] 新增 9 个 Contract/Channel/Replay/Strategy parity 测试；全量基线更新为 339 tests passed，3 external integration tests skipped。

证据：

- `kairos/contracts/market_data.py`
- `kairos/market_data/stream.py`
- `kairos/market_data/projections.py`
- `kairos/data/feed.py`
- `tests/test_async_market_stream.py`

### 异步数据流 M2 Runtime 与实时试点

- [x] 新增 `AsyncServiceSupervisor`，统一拥有长期任务，记录状态、attempt、restart count 和结构化 fault。
- [x] Managed Task 支持 criticality、有限 restart、正常完成声明和 bounded shutdown。
- [x] 新增 `AsyncKairosRuntime`，将异步任务健康状态绑定到既有持久 `KairosApplication` 生命周期。
- [x] Critical stream failure 会将正式 Application 持久降级为 `REDUCE_ONLY`。
- [x] 新增声明式 `MarketDataRequirement`，覆盖 Venue、Instrument、MarketDataKind、delivery、freshness、depth 和 capture policy。
- [x] `SubscriptionPlanner` 使用 Catalog Listing 和 connector capability 在联网前验证需求，并合并多个策略的重复订阅。
- [x] `SubscriptionReconciler` 生成增量 subscribe/unsubscribe；断线 reset 后可从目标 Plan 完整恢复。
- [x] Massive WebSocket reconnect fault 不再静默吞掉，输出结构化 `MassiveStreamFault`。
- [x] 新增 `MassiveCanonicalStreamService`，将 raw-journaled Massive message 转换并发布为 Canonical Event。
- [x] 新增受监督 Live-style Massive -> Canonical Channel -> SMA Strategy 测试，与 frozen batch backtest 结果完全一致。
- [x] 全量基线更新为 349 tests passed，3 external integration tests skipped。

证据：

- `kairos/application/service_supervisor.py`
- `kairos/application/async_runtime.py`
- `kairos/market_data/subscriptions.py`
- `kairos/connectors/massive/websocket.py`
- `tests/test_async_runtime_subscription.py`
- `tests/test_async_live_pipeline.py`
- `tests/test_massive_websocket.py`

### 异步数据流 M3 Durable Command Outbox

- [x] Runtime Store schema v7 新增 `order_outbox`，Order 与 Submit Command 可在同一事务创建。
- [x] Outbox command 具有稳定 command ID、状态、attempt、last error 和更新时间。
- [x] `DurableOrderCommandService` 在入队前强制 Application operational、Kill Switch 和注入式风险验证门禁。
- [x] `DurableOrderDispatcher` 作为 Async Runtime 长期任务运行，并使用线程卸载兼容现有同步 Venue execution gateway。
- [x] Dispatcher 原子 claim command，同时将 Order 从 PLANNED 经 APPROVED 推进到 SUBMITTING。
- [x] 重启前未 claim 的 PENDING command 只发送一次；claim 后崩溃保持 SUBMITTING 并 fail closed，不会自动重提。
- [x] Venue Ack 将 Outbox 和 Order 原子推进为 COMPLETED/ACKNOWLEDGED。
- [x] 确定性校验错误进入 FAILED_TERMINAL/REJECTED；模糊 transport 错误进入 UNKNOWN/UNKNOWN。
- [x] 既有 Venue Order Recovery 推进 Order 后会同步修复对应 Outbox 状态。
- [x] 新增 7 个 Outbox/Dispatcher/Safety/Crash-window 测试；全量基线更新为 356 tests passed，3 external integration tests skipped。

证据：

- `kairos/execution/command.py`
- `kairos/execution/outbox.py`
- `kairos/orchestration/runtime_store.py`
- `tests/test_durable_order_outbox.py`

### 异步数据流 M4 模式组合与 M5 Binance 公共实时行情

- [x] 新增可审计 `RunModeComposition`，显式声明 Study、Backtest、Historical Simulation、Paper Trading 和 Live 的 EventSource、Clock、Execution Driver、Persistence、Safety 与 Capture。
- [x] Backtest 强制 Replay Clock；Live/Paper 强制 Capture 和 durable persistence；Live 禁止 simulated execution driver。
- [x] Canonical Contract 新增 OrderBook Delta、Index/Mark Price、Funding Rate 和 Open Interest typed payload。
- [x] 新增 Domain Market Data -> Canonical Event 转换，DerivativeMarketState 可拆分为多个有序 canonical facts。
- [x] `BinanceStreamSession` 支持长期运行、主动 stop、线程安全连接关闭、Raw Journal 和 reconnect 计数。
- [x] 新增 `BinanceCanonicalStreamService`，将 websocket-client 阻塞 transport 接入受监督 Async Runtime 和有界 Canonical Channel。
- [x] Binance market-data-only 端点使用标准 TLS 443，并兼容不携带 `e` 字段的真实 bookTicker 报文。
- [x] 新增 `CanonicalCaptureWriter`、Session Manifest/hash 和 `CapturedCanonicalEventSource`，Live Capture 可经相同 EventSource 确定性 Replay。
- [x] Canonical Event 保存 raw-line capture offset，可从策略输入追踪到原始消息。
- [x] 新增 `CanonicalQuoteProjection`，向 Live/Replay 策略提供相同的 midpoint、freshness、source sequence 和 versioned read-only state。
- [x] 新增 `kairos data live-binance`，无需账户凭据即可采集公开 Binance Quote/Trade/Depth，输出 Raw 与 Canonical Journal。
- [x] 真实公网 `BTCUSDT@bookTicker` 集成验证通过：2 raw messages、2 canonical Quote、Capture hash、Replay 和 Quote Projection 完全一致。
- [x] 新增 `kairos data soak-binance` 和可审计 Soak Artifact，显式验收持续时间、事件数、重连、序列回退、最大静默、尾部静默、生产者错误、Raw/Canonical 数量和未规范化消息。
- [x] 修复主动关闭 WebSocket 时空 frame 被误记为 Raw 消息的问题；空 frame 现在按 EOF 处理，不污染 capture 计数。
- [x] 修复后真实公网 60 秒 Soak 通过：60.230105 秒、4102 raw、4102 canonical、0 ignored、0 reconnect、0 sequence regression，最大事件间隔 1.254073 秒，Artifact audit hash 为 `ea591e5576d9b566bb5ab0fd12a63a489970a9c21fffd5f3185d7cf798a9f58d`。
- [x] 独立核验 Canonical 文件 4102 行，SHA-256 `4cf638bd3e1a6af2830d14f26d57cdbcb9383106c94f3adf419d2b8588f6f496` 与 Manifest 完全一致。
- [x] Canonical Contract 新增 typed `OrderBookSnapshotPayload`，Snapshot 与 Delta 均可 Capture 和确定性 Replay。
- [x] 新增 `CanonicalOrderBookProjection`：Snapshot 建书、Delta overlap、零数量删除、严格排序、重复 Delta 幂等跳过、sequence gap 结构化证据、crossed book 失效和新 Snapshot 恢复。
- [x] OrderBook 在缺少 Snapshot、发生 gap 或盘口交叉时 fail closed，清空策略可见深度；失效期间拒绝继续应用 Delta，避免策略读取伪连续盘口。
- [x] Capture Replay 可重建与 Live 完全相同的 OrderBook State 和 Gap Evidence。
- [x] 新增 `BinanceOrderBookSnapshotProvider`，通过无需凭据的 REST Depth 接口获取 typed Snapshot。
- [x] 新增 `BinanceOrderBookSyncService`，实现 WebSocket Delta 有界缓冲、首次 Snapshot 桥接、stale delta 丢弃、gap 自动重新 Snapshot、当前 Delta 重试和有限重同步失败。
- [x] Transport reconnect 后的首条 Canonical Event 带 `transport_reconnected` flag；OrderBook Sync 即使序列表面连续也强制重新 Snapshot，避免把不同连接 epoch 当成连续事实。
- [x] Aligned OrderBook Stream 单独 Capture，策略只消费已对齐的 Snapshot/Delta；Capture Replay 可重建相同最终盘口。
- [x] 真实公网 `BTCUSDT` Depth 集成验证通过：公开 REST Snapshot 与 `btcusdt@depth@100ms` 的 25 条 WebSocket Delta 完成对齐，最终盘口有效、非交叉，Live/Replay Projection 完全一致。
- [x] 新增 `CanonicalQuoteSliceProjection`，将 Canonical Quote 转为正式 StrategyContext 使用的 point-in-time MarketSnapshot，Live 与 Backtest/Replay 不需要两套策略入口。
- [x] 新增 `CanonicalStrategyEventSession`，通过正式 `Strategy.on_start/on_market/on_end` 接口输出 Event IDs、Projection、StrategyDecision、Intent 和组合 Audit Hash。
- [x] 修复 `EconomicIntent.create()` 使用随机 `uuid4` 导致相同 Replay 输入产生不同 decision ID 的问题；decision ID 现在由 Strategy Spec、时间、Domain Intents、Risk Budget、Execution Policy 和 Evidence 确定性派生。
- [x] Strategy Quote Projection 拒绝非正价格和 crossed quote，异常行情不会进入策略决策。
- [x] 真实公网 `BTCUSDT@bookTicker` Capture 的 Live 与 Replay 通过同一正式 Strategy 接口产生完全相同的 Projection、Decision、Intent 和 Audit Hash。
- [x] Market Soak 聚合改为 O(1) 内存，不再为长跑保留全部 Canonical Event；新增 Channel peak depth/utilization/drop、Raw/Canonical bytes 和 Capture segment count 资源证据。
- [x] 新增 Rotating Canonical Capture：按事件数/字节分段、总磁盘预算、Segment Manifest/hash、Rotation Manifest/hash 和跨段确定性 Replay。
- [x] 新增主动 Restart Campaign：每个新 WebSocket Session 独立 Capture/Artifact，Campaign 验收总事件数、Leg 健康、restart count 和跨 Session sequence regression。
- [x] 真实公网强制 Rotation 验证通过：10.133146 秒、1315 events、27 segments、Channel peak 1/4096、0 drop、跨段 Replay 1315/1315，audit `91e91f5d43ce9b4d5cc43a4f70270f983e49be634892fac638a0bb9d2598c4e4`。
- [x] 真实公网主动 Restart 验证通过：12.651622 秒、3 sessions、2 restarts、918 events、0 boundary sequence regression，Campaign audit `c3dbea1883494a4f3953357c7248bcf990235caf68b7fe1aaf1c3eb300e1a4f9`。
- [x] 新增 `docs/market_data_long_soak_runbook.md`，固定 24–72 小时命令、资源边界、验收条件和 Replay 核验步骤。
- [x] 全量基线更新为 393 tests passed，5 optional external integration tests skipped；`compileall` 和 `git diff --check` 通过。

### Examples Suite

- [x] 新增 `examples/README.md`，按 Governed Backtest、Live Quote、OrderBook、Replay、Run Mode、Connector Contract 和长跑 Soak 组织入口。
- [x] 新增可离线执行的 SMA batch/async Canonical parity example，并可切换到正式 Q3/Q4 Dataset Release。
- [x] 新增无需账户凭据的 Binance Quote Capture example，输出 Raw/Canonical Capture 和 Strategy Projection/Decision/Intent/Audit Hash。
- [x] 新增 Binance REST Snapshot + WebSocket Delta example，输出对齐指标、有效盘口与 Replay equality。
- [x] 新增 Live-vs-Replay Strategy example、五模式 Composition example 和 Python/Rust 共用 connector contract-vector verifier。
- [x] 新增 Examples subprocess smoke tests 与 Repository hygiene tests，示例不再只靠文档声明可运行。
- [x] 真实治理 Release example 通过：4344 bars、84 trades、batch/replay 相同，audit `65dee8f2a7c104db803bf7fc9d6240350855ffc8eaf9ed6986c6ac33ab752f35`。
- [x] 真实公共 Quote example 通过：3 events、0 reconnect、Strategy live/replay 相同。
- [x] 真实公共 OrderBook example 通过：25 raw deltas、11 stale、14 aligned deltas、有效盘口、Capture Replay 相同。
- [x] Example 公网运行发现并修复 Snapshot/Buffered Delta 的 available-time 回退；保留原 event time，并以 `snapshot_aligned` 标记策略可用时点。
- [x] 删除 Notebook checkpoint，加入全局 ignore 和 secret-shape regression test；疑似已暴露 Provider key 仍需在外部控制台轮换。
- [x] Examples 改造后全量基线为 421 tests passed、5 optional external integration tests skipped；公共 Binance Quote/Depth 两项显式 live integration 均通过。

证据：`docs/examples_suite_acceptance.md`。

证据：

- `kairos/application/modes.py`
- `kairos/connectors/binance/stream.py`
- `kairos/connectors/binance/order_book.py`
- `kairos/market_data/capture.py`
- `kairos/market_data/soak.py`
- `kairos/market_data/projections.py`
- `kairos/strategies/event_session.py`
- `tests/test_binance_async_stream.py`
- `tests/test_binance_order_book_sync.py`
- `tests/test_binance_public_live.py`
- `tests/test_market_data_soak.py`
- `tests/test_capture_rotation.py`
- `tests/test_order_book_projection.py`
- `tests/test_strategy_event_session.py`
- `tests/test_run_mode_composition.py`

### Domain 边界

- [x] 将 `Strategy`、`StrategyContext`、`StrategyDecision` 从 `kairos.domain` 移到 `kairos.strategies.strategy_protocols`。
- [x] 删除 `kairos/domain/strategy.py`。
- [x] 新增 AST 架构测试，禁止 Domain 依赖 Data、Catalog、Backtest、Study、Storage 等上层模块。

证据：

- `tests/test_architecture_boundaries.py`
- 全仓无 `kairos.domain.strategy` 运行契约引用。

### Application 基础

- [x] 新增 `Clock` Port、`SystemClock`、`FixedClock`。
- [x] Readiness、Reconciliation、Monitoring、Kill Switch 和 Coordinator 支持统一 Clock 注入。
- [x] 新增 `ApplicationConfig` 和集中式 `RuntimePaths`。
- [x] 新增 KairosApplication 生命周期：STARTING、RECOVERING、RECONCILING、READY、RUNNING、REDUCE_ONLY、STOPPED。
- [x] Persistence 和可组合 Readiness Probe 在 READY 前执行。
- [x] `kairos trade` 先经过 KairosApplication Probe，再启动 Coordinator。
- [x] 账户锁支持 lease、heartbeat 和过期接管。
- [x] 有账户的 Runtime 强制配置持久恢复与对账服务，不能绕过恢复门禁进入 READY。
- [x] Runtime 从 SQLite 重建 Ledger、Portfolio 和 UnifiedRiskView，并持久化恢复快照。
- [x] Portfolio 估值不完整或 Venue 对账不一致时进入 UNKNOWN_EXTERNAL_STATE。
- [x] Coordinator 的 submit/combo/cancel 在组合 Application 后强制检查正式 Runtime 生命周期门禁，未完成启动恢复时不能执行。
- [x] 新增正式 `kairos runtime reference-artifact --root <isolated-root>` Application 场景，贯通 Market Data → Strategy → Intent → Risk → Order → Fill → Ledger → Portfolio → Reconciliation → Restart READY。
- [x] Runtime Golden 保存固定 Ledger hash 和全链路 audit hash，并由自动化测试锁定。

证据：

- `kairos/application/clock.py`
- `kairos/application/config.py`
- `tests/test_application_foundation.py`

### 事务运行状态和订单恢复基础

- [x] 新增 SQLite Runtime Store，启用 WAL、事务和 schema version。
- [x] 新增持久订单状态机和合法转换检查。
- [x] client order ID 具有唯一约束，并拒绝相同 ID 的不同请求。
- [x] Order 在调用 Venue 前依次持久化为 PLANNED、APPROVED、SUBMITTING。
- [x] Ack 持久化为 ACKNOWLEDGED；模糊外部失败持久化为 UNKNOWN。
- [x] 重启后 ACKNOWLEDGED Order 不重复提交。
- [x] 重启后 UNKNOWN Order fail closed，要求 Venue recovery。
- [x] 新增账户控制锁的基础数据库契约。
- [x] `kairos trade` 已接入 SQLite Runtime Store。
- [x] Combo Order 和 Cancel 使用相同持久状态机。
- [x] 执行事件、Order Fill 状态、Ledger Transaction 和 cursor 单事务提交。
- [x] 重复 Fill 幂等，冲突的相同外部 ID 拒绝。
- [x] Ledger 可从 Runtime Store 重建。
- [x] 旧 JSON Ledger 可幂等导入 Runtime Store，相同 transaction ID 的冲突内容会被拒绝。
- [x] 新增 Venue Order Recovery 契约，可按 client order ID / venue order ID 恢复 Ack、Reject、Fill 和 Cancel 证据。
- [x] SUBMITTING crash window 可在不重提订单的情况下恢复 ACKNOWLEDGED。
- [x] UNKNOWN Fill 可连同 Execution、Ledger 和 Cursor 原子恢复；缺少 Venue 证据时继续 fail closed。
- [x] Binance Spot/Futures/Options 通过 REST order query 和 user trade history 实现 Ack、Partial Fill、Fill、Cancel、Expire、Reject 恢复。
- [x] IBKR 通过 synchronized trades/fills 实现单腿订单 Ack、Fill、Cancel、Reject 恢复。
- [x] IBKR Combo Fill 按 fill contract/conId 映射为 leg-level TradeExecution，不再以聚合 BAG 状态推断各腿。
- [x] ACKNOWLEDGED/PARTIALLY_FILLED 工作订单也在启动时执行 Venue recovery，可恢复断线期间遗漏的 Fill 和 cursor，而不只处理 UNKNOWN/SUBMITTING。
- [x] 新增 IBKR commissionReport/connect event 驱动的 Durable Fill backfill 服务；缺失 commission report 时 fail closed，避免先记零费用后产生冲突事实。
- [x] `kairos trade` 已组合 VenueOrderRecoveryService，启动时先恢复 crash-window order 再执行 Projection/Reconciliation。
- [x] Runtime Store schema v5 新增通用 Ledger Event，Funding/Settlement、Ledger Transaction 和 Cursor 单事务提交。
- [x] Runtime Store schema v6 新增人工 Order Resolution 审计事实，actor、reason、evidence 缺一不可，状态转换与审计记录单事务提交。
- [x] Binance WebSocket Fill 与 REST Trade History 使用同一 external key、execution ID 和 cursor 语义。
- [x] 重复 WebSocket/REST Fill 或 Funding 回补幂等，精确重复可安全推进 cursor，冲突内容拒绝。
- [x] Binance Funding History 已接入 DurableAccountingIngestionService。
- [x] Crypto Option Settlement 支持先构建事务、持久提交后更新内存 Projection，并可跨重启重建。
- [x] `account`、`trade` CLI 只从 SQLite Runtime Store 加载 Ledger，不再以 JSON 为运行事实源。
- [x] 旧 JSON Ledger 支持一次性 hash 审计迁移；迁移后源文件变化会拒绝静默重导入。
- [x] 架构测试限制 SQLiteRuntimeStore 只能出现在迁移/导出代码和兼容测试。

证据：

- `kairos/execution/order_state.py`
- `kairos/orchestration/runtime_store.py`
- `tests/test_runtime_store.py`
- `tests/test_durable_coordinator.py`
- `tests/test_order_recovery.py`
- `tests/test_kairos_application.py`

### 持久 Kill Switch

- [x] Kill Switch 状态写入 Runtime Store。
- [x] 重启后保持 triggered/reduce-only。
- [x] Reset 要求 actor 和 reason，并留下状态记录。

证据：`tests/test_runtime_store.py`。

### Dataset Storage Contract

- [x] Dataset Release 显式保存 `storage_kind` 和 `layout_version`。
- [x] 支持 tabular、market_events、market_snapshots、reference。
- [x] DatasetClient 按 storage kind 分发 Event 和 MarketSnapshot Reader。
- [x] 新发布的 Massive Event 和 Curated Slice Release 明确声明 storage kind。
- [x] 旧 Registry 在加载时兼容推断 storage kind。

### 数据产品体验和质量

- [x] 新增 `data search/describe/doctor/diagnostics --strict`。
- [x] 14 个既有 Product 补齐 owner/description，Bootstrap 不会再次抹除治理字段。
- [x] 浮动 Alias 从 Release 内嵌字段迁移到全局 Alias Registry。
- [x] 新增 typed OHLCV Quality Profile，校验字段、OHLC、时间、覆盖、主键和 Manifest/Release hash。
- [x] 新增 `data prepare`，编排 plan/acquire/validate/显式 promote。
- [x] 新增 `data query/freeze`，支持列裁剪、分区裁剪和 Study Input Snapshot。
- [x] BTC 1d 和 1h OHLCV 均达到 Q3、approved_for_backtest。
- [x] 新增治理 Release 驱动的 `backtest sma`，冻结输入并输出确定性 audit hash Artifact。
- [x] 新增统一 DataProductContract，覆盖 Product 身份、Schema、物理布局、storage kind、能力、质量 Profile 和最低发布等级。
- [x] Catalog schema v4 持久化 DataProductContract；当前 15 个 Product 全部拥有统一 Contract。
- [x] 内置产品、动态 Massive 配置、Provider Registry、Catalog Bootstrap 和 Publisher 消费同一 DataProductContract。
- [x] 删除 `models.Datasets` 的平行产品定义；兼容 Datasets handle 由权威 DataProductContract 派生。
- [x] 旧受管数据集概念已收敛到 DataProductContract，不再是第二种产品模型。
- [x] DataProductContract 发布门禁阻止低于声明最低质量等级的 Release。
- [x] Quote Profile：价格有效性、bid/ask 非交叉、主键、point-in-time 和确定性顺序。
- [x] Trade Profile：trade ID、价格/数量、方向、point-in-time、去重和确定性顺序。
- [x] Market Event Profile：来源身份、事件唯一性、available time 和 source order。
- [x] Option Snapshot Profile：合约期限、strike、bid/ask、IV、快照唯一性和 point-in-time。
- [x] Feature Profile：窗口完整性、无未来数据、finite values、输入 Release ID/hash 和确定性顺序。
- [x] Reference Profile：Instrument 版本身份和有效期范围。
- [x] Quality Engine 只按 DataProductContract 选择 Profile，不再由目录或字段猜测正式产品类型。
- [x] Q0 失败 Release 自动进入 Quarantined；approved 状态与质量等级由 Catalog Health 校验。
- [x] 3 条既有 Feature Release、1 条 Deribit Option Snapshot 和 1 条 Massive smoke Event 通过新 Profile。
- [x] 1 条存在物理顺序问题的 Massive AAPL Event Release 被识别并隔离为 Q0。
- [x] MarketSnapshot Profile：Manifest/Release 身份、切片顺序、Universe Definition、合约/报价覆盖、staleness、future fact、crossed quote、critical issue 和冻结输入。
- [x] 从 Massive SPXW Event 与旧 MarketSnapshot 双重冻结输入生成新的完整 Curated MarketSnapshot Release。
- [x] 新 SPXW Release 过滤首个缺失行情切片后达到 4 slices、100% contract/quote coverage、0 stale、0 future fact、0 critical issue，并晋级 Q3。
- [x] 新增 `backtest spxw-reference-scenario`，固定 Event、Source Snapshot、Curated Snapshot、Quality Report 和 conservative/stress audit hash；旧固定回放兼容命令已删除。
- [x] Trade/Market Event Profile 改为 DuckDB 流式聚合，不再通过 load_rows 物化全量数据。
- [x] 21,930,528 条 Deribit Trade 在约 3 秒内完成去重、值域、point-in-time 和物理顺序检查。
- [x] 原 Deribit Trade Release 识别出 8,620 处亚秒级 ISO 字符串物理乱序并隔离为 Q0。
- [x] 通过按月单线程外部排序生成新 Trade Release `ds_46f3c7aba02d299eaa6240b3`，完整保留 21,930,528 条记录并通过 Q2。
- [x] `derivatives.option_trades.crypto.deribit.btc@latest-validated` 已指向新 Trade Release。
- [x] 327,489 条 Massive SPXW Event 在约 0.42 秒完成流式检查，并因 1 处物理乱序隔离为 Q0。
- [x] 失败的并行排序派生 Release 经审计后 purge，避免保留约 750MB 冗余副本。

### 旧 History 和 CSV 收敛

- [x] 新 Parquet 发布停止生成 CSV sidecar。
- [x] 340 个既有 CSV sidecar 与 Parquet 核对 22,739,500 行后删除，未覆盖 Parquet。
- [x] `data/history/btcusdt-1h` 迁移为不可变 Q3 OHLCV Release，4344 行核对后删除源目录。
- [x] 删除 `kairos.history`、`BarRepository`、旧顶级 `kairos history` 和旧测试。
- [x] README 和 History Notebook 改用 DatasetClient 和治理回测。
- [x] 旧 `data/history` 已不存在；迁移报告记录 Release、行数、质量与 report hash。

### MarketSnapshot 与 Surface 旧路径收敛

- [x] 从 `kairos.backtest.feed` 删除公开 DatasetRepository，物理读写迁入 Data 内部 MarketSnapshotStorageDriver。
- [x] 正式 Study、Volatility 和 Backtest CLI 只通过 DatasetClient 解析不可变 Release。
- [x] StudySnapshotCollectionStore 更名并收敛为 MarketSnapshotCollectionPublisher。
- [x] 7 条旧 `data/datasets` Release 在核验 Manifest/Release hash 后迁至统一 Curated Release 布局。
- [x] 所有迁移后的 MarketSnapshot 均通过 DatasetClient 重放，并与原 content hash 一致。
- [x] 4 个独立 Surface JSON 迁移为一条 typed Feature Release，并验证 Surface ID 完整一致。
- [x] 删除 SurfaceRepository、`data/datasets`、`data/surfaces` 和旧 Notebook 物理路径读取。
- [x] 新增架构测试，禁止重新引入 DatasetRepository、StudySnapshotCollectionStore 和 SurfaceRepository。

证据：

- `kairos/data/contracts.py`
- `kairos/data/catalog.py`
- `kairos/data/client.py`
- `tests/test_data_storage_contract.py`

## 尚未完成

### Application Runtime

- [x] 正式 Kairos CLI 只通过 KairosApplication/RuntimeSupervisor 启动；Coordinator 不再以布尔参数激活正式路径。
- [x] Coordinator 正式 `activate()` 只接受 Application READY 门禁；旧布尔 Readiness 已隔离为 `start_旧版()` 测试兼容入口，生产调用为零。
- [x] Binance/IBKR 真实 order status、open orders/trades 和 fill history 接入 Venue Order Recovery 契约。
- [x] 实现 `runtime orders` unresolved order 查询与人工处置 CLI；只允许显式终态，要求 actor/reason/evidence 并保存不可缺失的审计记录。
- [x] 新增长期 RuntimeSupervisor，统一 account-lock heartbeat、Venue Fill backfill、周期对账、Kill Switch、安全降级和 Runtime Store checkpoint。
- [x] 新增 soak Artifact 验收契约；实际时长、全周期健康、Critical Alert、restart drill、Kill Switch drill 任一不满足均不得通过。

### Order/Execution/Ledger

- [x] Expire 和 Venue 主动 Cancel 通过周期 Venue recovery 接入 Runtime Store，并保留 Venue proof。
- [x] Venue open-order/fill recovery service 核心契约与模拟故障场景。
- [x] Binance WebSocket/REST Fill 接入持久去重和 cursor。
- [x] IBKR Durable Fill event/backfill 服务已组合进长期 RuntimeSupervisor；Binance/通用 Venue recovery 使用同一周期 backfill 生命周期。
- [x] Binance Funding 与 Crypto Option Settlement 接入通用持久 Ledger Event。
- [x] Commission、Dividend、Corporate Action、Funding 和 Settlement 均可通过通用持久 Ledger Event 单事务写入并幂等重建。
- [ ] IBKR 外部 Dividend/Corporate Action 活动源的真实账户端到端接入与回补验收（需要 Gateway/Flex 外部数据）。
- [x] 正式 Runtime Ledger Repository 迁移到事务存储；JSON 仅保留迁移/导出用途。
- [x] 启动时 Ledger/Portfolio/Risk Projection 和 balance/position Reconciliation 门禁。
- [x] 启动时从 Runtime Store 重建本地 Open Order 与策略 Position Book，并与 Venue open orders、Ledger account positions 一并进入 READY 对账门禁。
- [x] Binance Futures Funding 在 RuntimeSupervisor 中按重叠窗口周期回补并持久去重；遗漏 Funding 会通过 Venue balance 对账 fail closed。
- [x] 到期 Future/Listed Option/Crypto Option 若仍有 Ledger Position，会以“缺少 durable settlement”阻止 READY；Settlement 后仍由 balance/position 对账验证 Venue completeness。
- [x] IBKR Combo Fill 的 leg-level execution recovery。

### 数据产品

- [x] 合并 Dataset 定义和动态配置为 DataProductContract。
- [x] 为 Quote、Trade、Market Event、Option Snapshot、Feature、Reference 实现 typed Quality Profile。
- [x] 正式 Study Diagnostics 和 Reference/SMA Backtest 只消费冻结 Q3/Q4 Release，并可通过 `data audit-artifact` 独立核验 Release ID、content hash、质量和批准状态。
- [x] 数据专项最终验收通过：92 项数据/研究/回测测试、离线 search/describe/query/freeze/replay/backtest/audit 用户路径和 Catalog strict health 全部通过；详见 `docs/data_system_final_acceptance.md`。

### 旧系统删除

- [x] DatasetRepository 删除，MarketSnapshotStorageDriver 仅作为 Data 内部物理 Driver。
- [x] SurfaceRepository 迁移为 Feature Release 后删除。
- [x] 迁移删除旧 `data/datasets`、`data/surfaces` 和空旧目录。
- [x] 更新其余直接使用旧 Repository/物理路径的 Notebook、Study 和 CLI。

### 最终验收

- [x] L2 单进程正式 Application 闭环。
- [x] L3 全部崩溃窗口和故障注入。
- [x] 两条治理 OHLCV -> SMA Golden Pipeline（BTC 1d、BTC 1h）。
- [x] Massive SPXW Event -> MarketSnapshot -> Strategy Golden Pipeline。
- [ ] 24–72 小时 Paper/Testnet L4 验收。

## 下一实施顺序

1. 补齐剩余旧 Market Event 的 typed payload，消除正式 Runtime 中的 `GenericMarketPayload` 兼容面；
2. 按 `docs/market_data_long_soak_runbook.md` 实际运行公共行情 24–72 小时，保留 Rotation、Restart、资源门禁和 Replay Artifact；
3. 在凭据和外部环境可用后运行 Binance Testnet 或 IBKR Paper 24–72 小时 L4，完成 order ack/fill recovery、restart、reconnect、Kill Switch 和 reconciliation drill；
4. 根据真实 L4 结果修复 connector、Runtime 或运维缺口，全部退出条件有证据后再关闭 M5。

M5 当前边界：公共 Binance 实时数据的短时采集、规范化、落盘、哈希和确定性重放已经通过；
OrderBook 的状态机、自动 REST Snapshot/Delta 缓冲协调、gap/reconnect recovery 和真实公网 Replay 已通过。
同一正式 Strategy 接口的 live-vs-replay Projection/Decision/Intent Hash 也已通过。
这证明实时市场数据与策略链路可用，但不等于 M5 完成。尚缺 24–72 小时长稳以及真实 Paper/Testnet
私有执行故障演练。

当前 L4 外部前置检查：Binance Testnet 凭证缺失，IBKR Paper Gateway 的 4001/7497 端口均不可达，
且 CLI 可选策略尚无 `PAPER_APPROVED` 版本。执行步骤和通过条件见 `docs/runtime_l4_soak_runbook.md`。
可使用 `kairos runtime l4-preflight --venue ... --environment ... --strategy ... --instrument ...`
重复检查，输出不会包含凭证内容。

## Runtime L2 Golden

```text
Scenario: runtime-l2-spot-target-position-v1
Command: kairos runtime reference-artifact --root data/runtime-reference-artifact
Stages: Market Data -> Strategy -> Intent -> Risk -> Order -> Fill -> Ledger
        -> Portfolio -> Reconciliation -> restart READY
Ledger hash: 82ef3dc27fada5ed8db0fb241a74376cbc62e40b511a0dd75656a8893082a6bd
Runtime audit: 1fa8996ace7160718d82b4c2f824ae0d6cb433f1e8d7b4a351272a33d7a50812
Artifact: data/runtime-reference-artifact/artifacts/runtime-l2-spot-target-position-v1/manifest.json
```

该场景使用正式 KairosApplication、ExecutionCoordinator、ExecutionRouter、SQLite Runtime Store、
DurableExecutionIngestionService 和 RuntimeRecoveryService，并验证成交后重启可从持久事实重建
Ledger、Portfolio 和 Risk，且 Venue balance/position 对账一致。它完成 L2，但不替代 L3 crash-window
矩阵或 L4 Paper/Testnet 长时间运行验收。

## Runtime L3 Failure Policy

```text
Matrix: runtime-l3-failure-policy-v1
Command: kairos runtime failure-policy --root data/runtime-reference-artifact
Cases: crash before Venue call
       Venue accepted before Ack persistence
       partial Fill crash/restart
       WebSocket disconnect + REST backfill
       REST/WebSocket duplicate
       Ledger transaction interruption
       Kill Switch restart
       reconciliation mismatch
       account-lock expiry/takeover
Audit: bf74045ae8c728dc55518815bfcd717b19f60d68e1c2d9141e202e121096a917
Artifact: data/runtime-reference-artifact/artifacts/runtime-l3-failure-policy-v1/manifest.json
```

矩阵在真实 Coordinator 和 SQLite 事务边界注入一次性故障，固定验证 duplicate orders、lost Ledger
facts、duplicate Ledger facts 和 unknown-state risk expansion 均为 0。下单前故障产生的模糊状态通过
`runtime orders` 的 actor/reason/evidence 审计流程显式关闭，不会静默重提或静默拒绝。

## 大规模 Trade 质量证据

```text
Source Release: derivatives.option_trades.crypto.deribit.btc.v1
Rows: 21,930,528
Source issue: 8,620 physical ordering inversions
Curated Release: ds_46f3c7aba02d299eaa6240b3
Curated hash: 46f3c7aba02d299eaa6240b39f9ea8d16ca061aa5f41e63f3aa75fa5a585121c
Quality: Q2 / approved_for_study
Streaming assessment time: approximately 3 seconds
```

## SPXW reference pipeline

```text
Event Release: options.us.massive.spxw.synthetic-forward.20251103.https.v1
Event hash: 29c781e4434bd6e5875a27c66af05a937682ca8cac809c9c3256ab4aa4c3e37b
Source MarketSnapshot: spxw.massive.synthetic-forward.20251103.https.v1
Source hash: bbd9a2c79ae2b0f9b4f3099be4b8a108c385270ed6f2e1cb803501243d258d4a
Curated Q3 MarketSnapshot: ds_c5cd07c6a542b4af665709b6
Curated hash: c5cd07c6a542b4af665709b6581b7417a66f3171dc6292f312a352f650921460
Conservative audit: a838da42e0c168a0bf491494b8d7e80d28fce35e38963a0ddf5d473dbcafae51
Stress audit: 15fee523b9504d3f91b1f47a0933c12f2f9111448d52bbf73cd6d97921e31732
Pipeline audit: 94bc5133a7b5822c3f354cb32c6988b9e53d5e07ac0972d3d97b1033309c194d
Governed consumed-input audit: dc542279b2aa4c64d66cdeb3d4d2c00440d3ef93286451652b4a255f5dab73ef
```

该 Pipeline 证明 Event → Curated MarketSnapshot → Strategy Backtest 的技术复现闭环。其样本仅包含 4 个一分钟切片，只能作为 Golden mechanics evidence，不能作为策略统计有效性或 Live 晋级证据。

## 正式研究/回测输入审计

```text
BTC 1d SMA input audit: 009bcb95771ca3485396cfe2e5359c8cd1ada73c9e116758470738fd07627687
BTC 1h SMA input audit: ef07f8fa6775f0998e83af602049a68b2b8b40605bd4f677ad2807bc663e449d
SPXW Golden input audit: dc542279b2aa4c64d66cdeb3d4d2c00440d3ef93286451652b4a255f5dab73ef
SPXW Study Readiness input audit: 4ea51a266d2f09d2dc80b41fec0075532e3488beb2158fa5e3dfa68414294f29
```

上述审计证明直接消费输入均为冻结且批准的 Q3/Q4 Release。SPXW Readiness 的输入治理通过，
但研究结论仍为 `DATA_NOT_READY`：当前仅 1 个交易日、4 个一分钟切片，缺少真实 collection session、
足够 observation 和 surface calibration。输入合规与统计有效性是两个独立验收门，不能互相替代。
