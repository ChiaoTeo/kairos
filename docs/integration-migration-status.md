# Integration 迁移状态

本文记录实现进度，不定义架构。长期设计规则见
[`integration-session-and-operation-design.md`](integration-session-and-operation-design.md)。

## 2026-08-13 Reference SQLite read-model migration（主体完成，bounded promotion 待收口）

- Reference 数据面改为单写者 SQLite
  current tables + lifecycle publication cursor + consumer-owned bounded projection；
- 新增 schema metadata 和七类规范化 current tables，旧 whole-catalog JSON 在 migration
  中一次性导入后删除；Rust/Python contract 均以 read-only/query-only SQLite 连接读取；
- Account 已按 provider instrument scoped query 并按 generation 失效 identity cache；
  Execution 只监控 watermark、preflight 按 market/instrument 查询；Market 使用有界分页恢复；
- 删除 Reference 八视图 mmap writer/reader、manifest 依赖和 snapshot slot 配置；Python
  Strategy/CLI 已切换到 SQLite contract；
- lifecycle publication 改为 durable history + 单 `published_sequence` cursor，删除重复 payload
  outbox；current rows 按主键增量 upsert，未变化 payload 不重写；
- production ReferenceActor 只常驻 generation/sequence/count 元数据，provider last-good 在一次
  reconcile 内从 SQLite 装载并在完成后释放；健康读模型和事件发布不再克隆完整 catalog；
- 删除 Reference 全量 FlatBuffer snapshot schema、编码器与 golden fixture；CLI 读取直接走有界
  SQLite contract，`snapshot` 命令仅返回水位/计数和迁移提示，不再 dump 全量目录；
- focused SQLite round-trip、Market recovery、Execution watermark、Python Reference contract、
  Aeron change-event 和百万行有界读取验收通过；尚待把 refresh 期间的完整 canonical candidate
  与 Massive 完成页合并替换为 SQLite source-fact 分页 promotion，才能满足写端全程有界内存目标。
- Reference v2 contract 已按 Market 的 contract 形状收敛为 typed event roots + direct SQLite
  reader；Rust/Python 均提供 control、event decode/stream 和 SQLite reader 能力，Reference
  不提供 mmap/shared-memory view；旧 Python client 入口已删除，调用方统一使用
  `kairospy.infrastructure.contracts.reference`。

## 当前基线

- `Integration::new`、`ConnectionSpec`、`IntegrationCapability` 和 `dyn Connection` 在 `crates/` 中当前均为零命中。
- Integration 的公开结构以 `application/capabilities`、`application/participants`、`application/blocking`、`services/participants`、`services/transport` 和 `services/quota` 为主轴。
- 命令结果已区分 `Confirmed`、`Rejected`、以普通 pre-delivery error 表达的 `NotSent`，以及
  必须同 route reconciliation 的 `Indeterminate`；query 与 command 的重试语义分离。

## 已完成切片

- Binance、OKX participant-native connection 和 principal context；
- Execution 的 Binance + OKX 多 route 组合；
- Execution production server 的已支持 live route 仅构造 async entry/query/event
  capability；Binance USDⓈ-M 与 COIN-M Futures 已完成原生 async entry/query/event
  vertical slice；Binance Cross/Isolated Margin 与 Options execution 也已完成原生 async
  entry/query/event vertical slice；这些生产 route 不再回落到 blocking；
- `/sapi/v1/equity/market/exchangeInfo` 已使用 Workspace 中的 Binance readonly key 完成真实
  provider contract 验证：API-key header、HTTP 200、weight 1、全量 `symbols` shape 和 AAPL
  `BUY_SELL` 语义均已确认；Reference 因此恢复 catalog-only async capability。仍然没有恢复
  未验证的 Equity quote、下单、撤单或订单查询路径，不以一个已验证 catalog endpoint 推断
  其他 `/sapi/v1/equity/...` 协议存在；
- Binance USDⓈ-M `TRADIFI_PERPETUAL` 已从普通 dated future 分离：它保留 perpetual 生命周期、
  不使用 provider 的 2100 年占位 `deliveryDate`，并投影 canonical equity underlying；
- IBKR US equity 的 production 单 route 已投影原生 async entry/query/event capability；
  三者共享一个 TWS/Gateway client-id session 和串行 order-id allocator，readiness 会验证
  managed target account、`nextValidId` 与初始 open-order floor；
- Execution server 在 provider composition 前为规范化 IBKR `host:port:client_id` 获取
  Workspace `ExclusiveProcess` lease；同一工作区的第二个 owner 会在连接 TWS 前失败，
  identity 在 lock 文件名中只保留哈希；
- Execution 多 route 可组合多个 IBKR equity session，但每个 order-event route 必须分配
  不同的 TWS client ID；同一进程内重复 `host:port:client_id` 在 composition 阶段拒绝；
- IBKR connect、readiness/open/history query 和 event subscription 均有显式 deadline；
  submit/cancel 超时按可能已写入处理为 `Indeterminate`，不做透明 retry；
- IBKR direct CLI 已通过有界 async entry/query/event gateway proxy 使用同一原生 async
  session；旧 `IbkrOrderConnection`、`IbkrExecutionStreamConnection` 及其 blocking facade
  已删除；
- Execution required route 在认证、订阅和 health ready 前阻止进程 ready；optional
  route 可独立 degraded/recover，并通过 health route 状态暴露；
- Execution stream 在 provider `ResyncRequired`/backpressure 或业务 mailbox overflow
  后进入 reconciliation barrier；Actor query reconciliation 成功后才释放重连；
- Execution recovery query 已按失败 route 的 Integration `binding_id` 定向，成功后只释放
  对应 route 的屏障，避免一个 provider 的 gap 触发其他 provider 的无差别恢复；
- Execution 双 route 进程级 fixture 已证明：Binance route 停在 reconciliation barrier 时，
  OKX route 仍可重连并交付事件，失败 route 的 query target 不会扩散到健康 route；
- Binance USDⓈ-M 的原生 HTTP listen-key + WebSocket fixture 已覆盖
  `listenKeyExpired -> ResyncRequired -> 新 listen key -> channel epoch + 1 -> 恢复交付`；
- Account 的 Binance Spot/Funding 与 OKX Trading provider-native async snapshot/profile
  capability；Binance Spot 与 OKX Trading private stream 直接运行在 Account Tokio runtime；
- Account 的 Binance Cross Margin、USDⓈ-M Futures、COIN-M Futures 与 Options snapshot 和
  credential inspection 已投影为 participant-native async capability，production server 不再
  为这些 GET/query 路径回退到 blocking；默认 REST endpoint 按产品选择，同一个 Account
  composition 会拒绝混合不同 endpoint family；
- Account production server 不再回退到 blocking provider adapter；Binance Spot、Funding、
  Cross/Isolated Margin、USDⓈ-M/COIN-M Futures、Options，OKX Trading 与 IBKR equity 均走
  provider-native async composition，paper/simulated 仍使用本地 deterministic source；
  完整 async snapshot 是各 private stream 的 readiness/recovery 基线；
- Account readiness 已由首次完整 snapshot、私有流认证/订阅 health 聚合；只有启用写能力的
  Account 才要求 trade lease，显式 `readonly` credential 即使远端 key 具有 trade permission
  也不能开启写能力；
  private stream envelope 包含 participant/binding/channel/epoch/event-id/sequence/timestamp，
  snapshot refresh 期间使用有界 recovery buffer，duplicate/gap/overflow 触发 resync；
- IBKR Account snapshot 与 private account-update stream 已投影为同一个 TWS/Gateway hard
  session 上的原生 async capability；server/CLI 在连接前获取与 Execution 相同作用域的
  `ExclusiveProcess` client-id lease，避免两个进程争用同一 IBKR API client identity；
- IBKR session 使用 pre-bound global notice stream 保留 handshake/runtime notice；1100、
  1101、1300 在 Execution 与 Account private stream 上都映射为 `ResyncRequired`，其余
  subscription notice 显式记录而不再静默丢弃；
- Workspace `ExclusiveProcess` lease 与 provider quota mmap 均有真实子进程 fixture，分别
  证明跨进程 client-id 排他和共享配额 reservation 可见性；
- Binance、OKX、Massive、Hyperliquid 的 Reference provider facts → canonical identity 链；
- Account external position/open-order/fill facts 只携带 `ProviderInstrumentRef`；Account
  composition 从 Workspace Reference snapshot-set 解析唯一 canonical instrument/market，IBKR
  equity 只解析唯一 Reference instrument 且不伪造 broker market；
- Reference provider fan-in、分页状态和 SQLx persistence 已切换到调用方 Tokio runtime；
  Reference 不再使用 Integration blocking catalog、provider worker thread、隐藏 SQLx runtime
  或 server `block_in_place`；
- Massive 的 async live/historical/catalog capability；
- 旧 gateway、全局 registry、旧 provider facade 和已迁移切片的旧构造路径删除。

## 当前工作

- Market 继续按 provider source 将 live/snapshot/historical 能力迁到业务 Tokio runtime；
- IBKR Account 仍需真实 Gateway contract test 与断连/recovery 故障注入；
- Reference-owned canonical identity 已从 Account external facts 中拆出；本地进程隔离和
  组合 recovery fixture 已收口，下一阶段聚焦 provider contract test 与上游无法注入的
  IBKR socket delivery/notice lag 边界。

Account 的线上默认路径已经 async-first，但“全部 provider 已迁移”和“完整多 provider
生产能力”仍未完成。显式 CLI/离线同步调用可以继续使用
`kairos_integration::blocking`；business production server 不得调用它。

Account async slice 验收重点：

- Binance Spot/Funding 与 OKX snapshot/profile future 由 Account runtime 直接 poll；
- 私有流认证和订阅完成前不 ready；
- snapshot refresh 与 stream delta 之间有有界 recovery barrier；
- duplicate、sequence gap、queue overflow、reconnect 和 shutdown 有明确语义；
- production server 的源码架构检查禁止 blocking fallback。

## 后续切片

- Binance 及其余 Market provider 的 async source；
- IBKR socket write 前后的命令 delivery fault injection；当前 `ibapi 3.3` 没有公开 transport
  seam，且其 `NoticeStream` 在内部跳过 lag count，仅能通过 crate 内部 debug log 观察；在上游
  暴露结构化状态或真实 Gateway 故障测试前，不新增无真实调用者的 transport facade；
- IBKR Account 真实 Gateway contract test、断连/private-stream recovery fixture；
- Binance USDⓈ-M Futures testnet contract test；
- Binance Margin 的 Cross/Isolated testnet contract test；
- Binance Options 的真实账户 contract test；
- FIX 当前没有 provider、配置或业务调用者；IBKR 已完成设计要求的 Broker hard-session
  场景。按 anti-overdesign 规则，FIX 不作为当前迁移 slice，未来接入真实 FIX destination 时
  再随该 provider 一并实现 Logon、sequence/resend 和 message store。

## 验证记录规则

每次更新本文件时记录验证日期、focused test 命令、结果和未解决失败。不要把测试数量或一次工作区状态复制回设计基线文档。

### 2026-08-11 Execution route isolation、IBKR notice 与跨进程 lease 收口

- `cargo test -p kairos-execution-service resync_barrier_isolates_the_failed_route --lib`：
  1/1 通过；故障 route 保持 `resync_required` 且不重连时，健康 route 继续交付；
- `cargo test -p kairos-integration async_source_reconnects_with_a_new_listen_key_after_expiry --lib`：
  1/1 通过；使用真实本地 HTTP/WebSocket transport，而不是 mock capability；
- `cargo test -p kairos-integration`：137 passed、1 ignored；新增 IBKR 1100/1101/1300
  notice → reconciliation 分类并由 Account/Execution source 直接消费 session notice；
- `cargo test -p kairos-workspace workspace::tests::exclusive_process_lock_is_enforced_across_processes --lib`：
  1/1 通过；第二个真实子进程无法获取同一 IBKR client-id lease；
- `cargo fmt --all -- --check` 当前被并行 Market 的
  `crates/business/market/service/tests/architecture.rs` 格式差异阻塞，本轮修改文件自身通过 rustfmt；
- 未完成：真实 IBKR Gateway、Binance testnet/Margin/Options credential contract；`ibapi 3.3`
  公共 API 不暴露 notice lag count，也不提供 submit socket write 前后 fault injection seam。

### 2026-08-11 Execution async route 收口

- `cargo check -p kairos-execution-service --lib --bins`：通过；
- `cargo test -p kairos-execution-service --lib`：通过；
- focused coverage：production composition 不构造 blocking capability、required route
  readiness gate、optional route degraded readiness、async command/query/runtime 和 stream
  reconnect、resync reconciliation barrier。

### 2026-08-11 IBKR execution async vertical slice

- `cargo check -p kairos-integration --lib`：通过；
- `cargo check -p kairos-execution-service --lib --bins`：通过；
- `cargo test -p kairos-integration --lib async_execution::tests`：6/6 通过；
- `cargo test -p kairos-execution-service --lib -- --test-threads=1`：42/42 通过；
- `cargo test -p kairos-execution-service --test execution -- --test-threads=1`：33/33
  通过；
- `cargo test -p kairos-workspace workspace::tests::exclusive_process_lock_hashes_provider_identity_and_rejects_second_owner`：通过；
- `cargo test -p kairos-execution-service --bin kairos-execution-server ibkr_client_identity_is_exclusive_before_provider_composition`：通过；
- `cargo test -p kairos-execution-service --lib production_rejects_unmigrated_live_blocking_provider_slice`：通过；
- focused coverage：IBKR production composition 仅构造 async capability、canonical remote
  order identity、binding-scoped recovery target；
- 未完成：IBKR socket write 前后故障注入、ibapi 内部 lag/notice 可观测语义和真实
  TWS/Gateway contract test。

### 2026-08-11 Binance Futures execution async vertical slice

- `cargo check -p kairos-integration --lib`：通过；
- `cargo check -p kairos-execution-service --lib --bins`：通过；
- `cargo test -p kairos-integration --lib services::participants::binance::futures -- --test-threads=1`：3/3 通过；
- `cargo test -p kairos-execution-service --lib secret_tests -- --test-threads=1`：11/11 通过；
- focused coverage：production composition 仅投影 async entry/query/event、Reference-owned
  provider symbol、10 秒 command uncertainty、partial-fill/event identity、listen-key expiry
  与 queue overflow reconciliation、30 分钟 listen-key keepalive；
- USDⓈ-M 与 COIN-M 保留不同 connection domain、REST/stream endpoint 和 Execution
  route product；旧 blocking Futures order-entry public path已删除；
- 未完成：HTTP 写前/响应丢失故障注入、端到端 stream recovery fixture 和 USDⓈ-M /
  COIN-M testnet contract test。

### 2026-08-11 Binance Margin execution async vertical slice

- `cargo check -p kairos-integration --lib`：通过；
- `cargo check -p kairos-execution-service --lib --bins --tests`：通过；
- `cargo test -p kairos-integration --lib margin -- --test-threads=1`：3/3 通过；
- `cargo test -p kairos-execution-service --lib secret_tests -- --test-threads=1`：14/14 通过；
- Cross/Isolated 使用独立 connection domain 和 RouteProduct；Isolated route 必须显式提供
  `isolated_symbol`，listen key 按该 provider symbol 隔离；
- production 与 direct CLI 均使用 async entry/query/event，旧 public blocking margin
  order-entry path 已删除；
- 未完成：submit/cancel response-loss fault injection 与 Cross/Isolated testnet contract test。

### 2026-08-11 Binance Options execution async vertical slice

- `cargo check -p kairos-integration --lib`：通过；
- `cargo check -p kairos-execution-service --lib --bins --tests`：通过；
- Options 使用独立 connection domain、REST endpoint 和 private-stream endpoint；
  provider 默认 endpoint 现在同时按 provider/product 选择，避免把 Options、Futures、
  Margin 私有流错误发往 Spot WebSocket API；
- submit/cancel 单次发送，ambiguous failure 为 `Indeterminate`；query 仅在明确 `-1021`
  时校时后重试一次；`ORDER_TRADE_UPDATE` 保留 partial fill、trade/fee/time 和
  binding/channel/epoch；
- production 与 direct CLI 使用 async entry/query/event，旧 public blocking Options
  order-entry path 已删除；
- 未完成：写前/响应丢失 fault injection 和真实 Options 账户 contract test。

### 2026-08-11 Execution provider slice 本地收口

- `cargo test -p kairos-integration --lib -- --test-threads=1`：123/123 通过，1 个显式
  live credential test ignored；
- `cargo test -p kairos-execution-service --lib -- --test-threads=1`：47/47 通过；
- `cargo test -p kairos-execution-service --test execution -- --test-threads=1`：33/33
  通过；
- `cargo test -p kairos-integration --test architecture -- --test-threads=1`：7/7 通过；
- Binance Futures/Options 已使用各自 API-family connection，并按 family 隔离 endpoint 与 quota；继续
  同一 shared egress quota；Isolated Margin entry/query/event 共用并校验 route symbol；
- 删除无官方 provider contract 的 Binance Equity/Stocks Execution 下单/查询伪协议；
  当前生产 Execution 支持的真实 live route 均为 async entry/query/event，不再保留一个
  “待迁移”的 blocking live route。
- `cargo test --workspace` 已覆盖并通过 Integration、Execution、Account、Market 等改动；
  最终被并行 Reference 改动的
  `massive_persists_each_successful_page_before_a_later_page_fails` 阻塞（fixture 预期分页失败，
  实际返回成功），与本 Execution slice 无关；
- `uv run pytest -q`：171 passed、8 skipped；`git diff --check`：通过；
  `cargo fmt --all -- --check` 被并行 Market/Hyperliquid 未格式化改动阻塞，Execution 本轮
  文件已由 `cargo fmt --all` 格式化。

### 2026-08-11 Account async source 收口

- `cargo test -p kairos-account-service --tests`：通过（8 lib + 1 CLI + 24 account +
  14 architecture）；
- `cargo test -p kairos-integration --test account -- --test-threads=1`：16/16 通过；
- focused coverage：共享 principal context、async snapshot/profile、private-stream
  authentication/readiness、envelope continuity、snapshot recovery barrier、生产 server
  禁止 blocking fallback；
- `.kairos` Binance Spot live acceptance：真实 REST snapshot 成功；真实 HMAC
  `userDataStream.subscribe.signature` contract test 通过；诊断 Account 进程达到
  `status=ready`，required channel 为 authenticated/healthy，首次 snapshot 完成；
- live acceptance 修复：查询侧 Binance `-1021` 重新校时只重试一次，命令不重试；私有流
  subscription request ID 规范化为 provider-safe 字母数字/连字符；readonly binding 不再被
  远端 trade permission 提升为可写，也不错误要求 trade lease；
- focused commands：`cargo test -p kairos-integration
  generates_provider_safe_subscription_request_id --lib`、显式 ignored live contract test、
  `cargo test -p kairos-account-service --bin kairos-account-server` 均通过；
- 尚待人工验收：在保持上述 live 进程运行时，由用户在 Binance UI 手工挂单并撤单，确认
  Account 私有流的订单事实与状态序列均发生对应变化；
- 未完成：Binance Isolated Margin 与衍生品 Account private stream、IBKR hard session、
  Reference canonical mapping。

### 2026-08-11 Binance Account derivatives async snapshot slice

- `cargo check -p kairos-integration --lib`：通过；
- `cargo check -p kairos-account-service --lib --bins`：通过；
- `cargo test -p kairos-account-service --lib composition::account::secret_tests -- --test-threads=1`：
  7/7 通过；
- `cargo test -p kairos-integration --lib application::participants::binance::connection::tests -- --test-threads=1`：
  8/8 通过；
- `cargo test -p kairos-account-service --lib --bins -- --test-threads=1`：14/14 通过；
  Account architecture 14/14、Integration architecture 7/7、Integration Account 14/14 通过；
- `uv run pytest -q`：171 passed、8 skipped；`git diff --check`：通过；
- focused coverage：Cross Margin、USDⓈ-M、COIN-M、Options async snapshot capability；默认
  产品 endpoint；跨 endpoint family composition 拒绝；production async source selection；
- workspace 全测通过 Account、Execution、Integration 后，在并行 Market 改动的
  `application::process::tests::replay_pause_resume_controls_the_source_without_a_polling_path`
  持续无输出超过 60 秒；单独运行该测试可复现挂起，已中止而非误报成功；
- `cargo fmt --all -- --check` 被并行 Market replay 与 OKX Market 文件的未格式化改动阻塞，
  本切片文件已定向格式化；
- Binance Cross/Isolated Margin、USDⓈ-M/COIN-M 与 Options Account snapshot/private stream 已
  迁移到调用方 Tokio runtime；未完成：各产品真实账户 contract test。

### 2026-08-11 Market async runtime 与伪协议收口

- `cargo test -p kairos-market-service --lib`：30/30 通过；Market architecture 2/2、actor
  9/9、orderbook 4/4、replay 2/2 通过；
- 修复 one-shot/replay source 在 desired subscription command 尚未 flush 时先等待 provider
  input 的死锁；source input 前后均执行 subscription reconciliation；
- Binance Spot historical download 使用调用方 runtime 上的 async HTTP；USDⓈ-M、COIN-M 与
  Options live market 使用 provider-native async WebSocket；production Market 无 blocking
  provider I/O；
- 删除无法由官方资料验证的 Binance Equity Market/Reference capability、normalizer、Workspace
  binding 和业务 source；静态搜索 `/sapi/v1/equity` 在 `crates/` 中为零；
- Integration 123/123（1 ignored live）、Reference 40/40 及相关 package compile 通过；
  `git diff --check` 通过。

### 2026-08-11 Binance Futures Account private stream async slice

- USDⓈ-M 与 COIN-M Account composition 现在同时投影 async snapshot 与 async account-event
  source；listen-key HTTP、WebSocket receive/ping/close/backpressure、keepalive、epoch 和 health
  均运行在调用方 Tokio runtime；
- 本地 provider fixture 验证 listen-key 创建、`ACCOUNT_UPDATE` normalization、binding/epoch
  envelope 和断开；Account recovery barrier 复用既有 stream continuity 语义；
- `cargo test -p kairos-integration account_event_uses_the_callers_runtime_and_preserves_binding
  --lib`：1/1 通过；Account composition 7/7、architecture 15/15 通过；
- 未完成：真实 USDⓈ-M/COIN-M 只读账户 contract test；需要用户提供对应测试账户 credential
  后才执行，不以本地 fixture 冒充真实 provider 验收。

### 2026-08-11 Binance Options Account private stream async slice

- Account Options 复用同一 participant-native async private channel，只把已验证的
  `ORDER_TRADE_UPDATE` 映射为 Account order/fill facts；余额和持仓继续由完整 async snapshot
  提供 recovery baseline，不猜测未验证的 provider event shape；
- order/fill batch 保留 binding、channel epoch、provider event identity、成交 instrument、fee
  和 occurred/received time；listen-key expiry、close、backpressure 继续触发既有 resync barrier；
- Options projection 1/1、Account composition 7/7、architecture 15/15 通过；
- 未完成：真实 Options 账户 contract test，需要用户提供只读测试 credential。

### 2026-08-11 Binance Cross Margin Account private stream async slice

- Cross Margin Account 复用 native async listen-key/executionReport channel，并通过共享的
  Binance order→Account projection 输出 order/fill batch；完整余额/持仓由 async snapshot
  recovery baseline 负责；
- listen-key、WebSocket receive/ping/close/backpressure、credential generation 和 keepalive 均
  保留原生 async 语义；binding/channel epoch/provider event identity 进入 Account continuity gate；
- Margin provider fixture 1/1、Account composition 7/7、architecture 15/15 通过；
- 未完成：真实 Cross Margin contract test，需要用户提供只读测试 credential。

### 2026-08-11 Binance Isolated Margin Account binding 收口

- Workspace Account record 通过 `values.isolated_margin_symbol` 显式拥有 provider-native
  symbol；Account composition 将其传给 isolated connection、snapshot query 和 listen-key
  channel，不从 canonical Market ID、segment 名或字符串约定反解析；
- 缺少 symbol 时在 provider I/O 前拒绝启动；connection 同时校验 stream config symbol 与
  route-owned symbol 一致；
- Account composition 8/8、architecture 15/15 通过；
- 未完成：真实 Isolated Margin contract test，需要用户提供只读测试 credential。

### 2026-08-11 IBKR Account native async hard session

- Account snapshot、open-order query 与 private account-update stream 共享一个
  `IbkrAsyncSession`，均由调用方 Tokio runtime 直接 poll；生产路径不使用 blocking facade、
  `spawn_blocking` 或隐藏 runtime；
- server 与 CLI 在建立连接前按规范化 `host:port:client_id` 获取 Workspace
  `ExclusiveProcess` lease，与 Execution 使用相同冲突域；同一 client identity 的第二个进程
  会在访问 TWS/Gateway 前失败；
- `AvailableFunds` 与 `TotalCashValue` 在 stream source 内按币种合并；只收到 available 时不发布
  会覆盖总额的错误 delta，完整 snapshot 仍是 reconnect/recovery baseline；
- 本地 focused coverage 包含单 hard-session composition、生产架构边界、余额/equity event
  normalization；未完成：真实 TWS/Gateway contract test 与断连 recovery fixture，需要用户提供
  可用 Gateway/TWS 环境和只读测试账户后执行。

### 2026-08-11 Binance USDⓈ-M command delivery fault injection

- provider 时钟预检返回无效 payload 时，测试确认只发生 time query、订单 POST 从未发出，
  调用结果保持可证明的 pre-delivery error；
- 订单 POST 已被本地 provider fixture 完整读取但连接在响应前关闭时，结果为
  `CommandOutcome::Indeterminate`，同一 listener 未观察到第二次订单连接；
- HTTP 500 的既有测试继续证明 command server failure 不透明重试；三个场景共同覆盖
  write-before、response-loss 与显式 server failure 的 delivery certainty 边界；
- focused tests：`futures_submit_preflight_failure_is_proven_not_sent` 与
  `futures_submit_response_loss_is_indeterminate_and_not_retried` 均 1/1 通过；未完成真实 testnet
  contract test 和 provider stream→Execution reconciliation 的组合 fixture。

### 2026-08-11 Binance Margin / Options command delivery fault injection

- Cross Margin 与 Options 分别覆盖 provider time preflight 无效、订单 POST 写出后响应丢失；
  preflight 场景确认未建立订单连接，response-loss 场景确认结果为 `Indeterminate` 且没有第二次
  command 连接；
- Spot/Margin 共享的 async timestamp calibration 现在把远端时钟失败显式标为
  `ExchangeError::Preflight`，同时保留 local quota 的 `RateLimited` 分类，避免尚未构造订单请求的
  故障被误判为可能已送达；
- focused tests：`margin_submit_preflight_failure_is_proven_not_sent`、
  `margin_submit_response_loss_is_indeterminate_and_not_retried`、
  `options_submit_preflight_failure_is_proven_not_sent` 与
  `options_submit_response_loss_is_indeterminate_and_not_retried` 均通过；剩余为真实账户 contract
  test，不以本地 TCP fixture 代替 provider 验收。

### 2026-08-11 Account canonical identity ownership 收口

- 删除 Integration `canonical_account_identity` 及所有 provider adapter 中的 canonical
  instrument/market 构造；`ExternalPosition`、`ExternalOpenOrder`、`ExternalFillEvent` 统一只携带
  participant-owned `ProviderInstrumentRef`；
- Account composition 从 Reference read-only SQLite contract 按需加载 markets/instruments，按
  participant、provider instrument type 与 source symbol 要求唯一匹配；缺失或歧义会拒绝应用
  snapshot/event，不以字符串 grammar 回退；
- IBKR equity 通过 Reference canonical equity instrument symbol 解析，market 保持 `None`，不再
  生成不存在的 `market:ibkr:equity:*`；
- Integration 135/135（1 ignored live）、Account 60/60（其中 architecture 17/17）通过；
  focused resolver coverage 包含 Binance market、IBKR instrument、missing/ambiguous rejection。

### 2026-08-11 Reference async runtime 与公开 provider 验收

- `cargo test -p kairos-reference-service --all-targets`：42/42 通过；
  `cargo test -p kairos-reference-contract`：7/7 通过（包含真实嵌入式 Aeron driver）；
  `uv run pytest -q tests/test_reference_contract.py`：4/4 通过；
- `cargo test -p kairos-integration --lib`：115/115 通过、1 个显式凭证 live test ignored；
  Market Reference 定向测试 8/8、Execution Reference projection 1/1 通过；
- Binance Spot/USD-M/COIN-M/Options、OKX Spot/Swap/Futures/Options 与 Hyperliquid
  真实公开接口完成一次全量刷新；结果为 10,590 个 market、generation 1、28,060 条
  lifecycle event，九个 source 均为 `ready`；
- OKX Options 改为先枚举 public underlying，再逐 underlying 获取 instrument；否则
  provider 会拒绝缺少 `uly`/`instFamily` 的全量请求；
- 第二次全量刷新 `changed=false`，generation 与 event sequence 不变；验证完整快照可
  重复收敛而不会制造重复增量；
- Aeron 真实 Media Driver 验证覆盖大于 MTU 的分片消息重组；`kairospy reference stream`
  实际接收并解码剩余 27,292 条事件，连同此前成功确认的 768 条，28,060 条 outbox
  全部发布并清零；
- Massive 九页 catalog 的分页、逐页持久化、失败后从 cursor 恢复由本地 provider
  contract test 覆盖；真实 Massive 接口验收仍缺只读 API credential，未将 HTTP 401
  误报为功能通过。

### 2026-08-11 Reference canonical 冲突收口与 CLI 验收

- 对九个公开 source 的 persisted provider catalog 做逐 ID 审计：原实现有 2,020 次
  deterministic overwrite，其中 543 个共享 Instrument 的字段不同；主要原因是把 provider
  symbol、market product family、quote currency、listing status/expiry 放进了 canonical
  Instrument；
- Spot Instrument 现只表达 base asset；quote、provider symbol 和 listing expiry 留在
  Listing/Market。Future/Option 统一 canonical type/family/symbol，并使用 UTC `YYYYMMDD`
  到期身份；OKX Option 同时补齐 canonical underlying；
- canonical Entity/Asset/Instrument 不再保留一个由 source 顺序决定的 `source_id`；共享
  Instrument 只允许聚合 listing availability，其他同 ID 业务字段冲突直接拒绝整次 refresh；
- 新 normalization shape 增加 persisted last-good eligibility gate；旧 provider snapshot
  不会在新映射刷新失败时混入 catalog；
- 真实迁移刷新通过：九个公开 source 全部 `ready`，10,590 markets，generation 2，新增
  18,768 lifecycle events；`kairospy reference stream` 接收 74 个 Aeron batch/18,768
  events，首尾 sequence 为 28,061..46,828，outbox 清零；
- 紧接的第二次 `kairospy reference refresh` 返回 events 0、generation 2、sequence 46,828，
  证明新 canonical shape 稳定收敛；原始 provider snapshots 中 544 个共享 Instrument 在
  忽略 listing availability 后不可调和冲突为 0；
- 新增 `kairospy reference validate`，一次检查 process readiness、required provider、8 个
  snapshot view、snapshot/health watermark、catalog 数量、publication outbox 和 durable
  event tail；`--require-massive` 会在 Massive source 缺失时返回非零退出码；
- 最新 focused Python contract：7/7 通过。完整 Rust 回归在并发 Integration 改动稳定后
  重新记录。

### 2026-08-11 Reference Massive 真实全量与发布压力验收（进行中）

- 当前 workspace 的 `massive-readonly` credential 已用于真实 provider contract，Massive
  请求改用 `Authorization: Bearer` header；生产请求 URL 不再携带 `apiKey`，provider 返回的
  next URL 也会先移除该字段。历史日志已脱敏，但仍建议轮换曾进入旧日志的 key；
- Massive equity 已完成至少一轮 13,079 market 的全量扫描并保存 current-shape
  last-known-good；后台下一轮分页刷新期间继续以该完成快照保持 `ready`，不再错误退回
  `syncing`；
- Massive options 使用逐页 cursor 持久化真实推进；截至本记录已超过 12 万 active contract，
  游标从 2026-08-14 推进到 2026-08-20 到期日。最终 cursor 归零、全量 promotion 和
  `reference validate --require-massive` 尚未完成，因此不标记为已验收；
- 当前 workspace 的历史 outbox 起始约 802 万条。server 改为 1,024 条有界后台批次、批末
  sequence 水位、批量确认和常数时间 outbox counter；真实 Aeron 观察已接收首段 1,334
  batches / 1,366,016 events，首尾事件 sequence 为 157,731..1,523,746，控制面在发布期间
  保持可响应；
- 真实规模暴露了新的退出条件：Massive options 在 20 万级 active contract 时 RSS 峰值约
  3.8 GiB。分页候选现已改为 SQLite 逐页 staging（cursor 与页面同事务保存），并新增
  source 定向 refresh、pause/resume；Massive 暂停或单独推进不会轮询 Binance/OKX，暂停前
  的 last-known-good 仍可服务。
- 这不是 Massive options 全市场常驻的验收通过：最终 promotion 仍会产生完整 provider
  catalog，且 Reference snapshot 也不应承载百万级全市场 options。稳态方案必须是“当前
  可交易的显式 coverage + 受控 REST reconcile + WebSocket 发现后按单合约查询”，coverage
  的增删和过期清理应成为下一条独立的应用命令；不能用缩小 universe 冒充“全量”。
