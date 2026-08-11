# 依赖组件成熟度与替换评估

## 1. 结论

> 实施状态：依赖收敛、Ruff/Hypothesis、Python CI 门禁、Reference/Execution
> SQLite migrations、Market 的 proptest/Criterion 入口、可选 Rust/Python
> OpenTelemetry、integration secret 保护和 nextest CI 入口已落地。
> tokio-tungstenite 与 SQLx 已进入实际落地；NATS JetStream 明确不在当前范围内。
> 本轮已完成测试 fake 的 Pyright 门禁、SQLite 持久化全量迁移到 SQLx、Rust/Python
> FlatBuffers golden fixture、schema compatibility CI、Provider 关键顺序/heartbeat 语义测试、
> Account/Execution 配置 secret 类型化和 Python CLI 输出脱敏。当前落地项已完成，剩余仅为
> 随 schema、Provider 和 Integration 重构持续维护的协作事项。

当前项目依赖的底层组件并不普遍落后。Tokio、Axum、Reqwest、Tracing、FlatBuffers、Aeron、Clap 和 Typer 都是合理且成熟的选择，不建议为了“使用更多成熟组件”而整体替换。

当前更大的问题是：

- 关键能力仍由项目自行实现，缺少成熟组件承载；
- Rust 依赖版本没有完全统一；
- Python 主依赖中存在暂时没有实际使用的包；
- 数据库迁移、遥测、测试运行、密钥保护等基础能力还不完整；
- Aeron、Unix socket、mmap、JSON control plane 和本地 supervisor 之间的边界尚未形成清晰的组件选型策略。

建议采用“保留正确的热路径组件，补充成熟的工程基础设施，谨慎替换自研可靠性代码”的原则。

## 2. 当前组件分层判断

| 能力 | 当前组件/实现 | 结论 |
|---|---|---|
| 异步运行时 | Tokio | 保留 |
| HTTP 服务 | Axum | 保留 |
| HTTP 客户端 | Reqwest | 保留，但统一版本和调用模型 |
| 日志 | tracing + tracing-subscriber | 保留，补 OpenTelemetry |
| 跨语言数据契约 | FlatBuffers | 保留 |
| 低延迟数据通道 | Aeron + mmap | 保留在热路径 |
| WebSocket | Integration 的 provider WebSocket 已统一到共用 tokio-tungstenite Tokio worker | 保持 worker 边界，继续补齐 provider 特有的心跳/恢复测试 |
| SQLite | Execution、Reference 全部使用 SQLx | 保留 SQLx，维护版本化 migration 与恢复测试 |
| 数据库 migration | Execution、Reference 使用 `sqlx::migrate!` | SQLx migrations 是唯一 schema 来源 |
| 持久消息 | 本地 outbox / 自研队列 | 当前保留本地方案，不引入 NATS |
| Rust 测试 | cargo test | 补 cargo-nextest、proptest、criterion |
| Python 测试 | pytest + Hypothesis + Ruff + Pyright | 源码和 tests 均已纳入 Pyright 门禁 |
| Python HTTP | aiohttp | OTel aiohttp instrumentation 已接入；Rust blocking REST 仍在隔离迁移 |
| 凭据保护 | Integration、Account、Execution 使用 secrecy；CLI 统一 redaction | 继续扩大错误链与日志覆盖 |

## 3. P0：应该优先补入的成熟组件

### 3.1 OpenTelemetry：统一日志、指标和分布式追踪

这是当前最值得引入的组件。

项目已经有 `tracing`、JSONL 日志、health 文件和运行时状态，但 Rust/Python 之间还没有统一的 trace、metric、log correlation。OpenTelemetry 提供 vendor-neutral 的 traces、metrics 和 logs 模型，并可以通过 Collector 接入不同的后端。[OpenTelemetry 官方文档](https://opentelemetry.io/docs/)

Rust 侧建议：

```text
tracing
  + tracing-opentelemetry
  + opentelemetry-otlp
  + OpenTelemetry Collector
```

Python 侧建议：

```text
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp
opentelemetry-instrumentation-aiohttp-client
opentelemetry-instrumentation-logging
```

Rust OpenTelemetry 当前支持 traces、metrics、logs，但官方状态仍标注为 Beta，因此应先用于观测和诊断，不要把业务一致性依赖在 telemetry exporter 上。[Rust OpenTelemetry 状态](https://opentelemetry.io/docs/languages/rust/)

Python 官方文档已经提供 HTTPX 等客户端的自动 instrumentation，以及日志中的 trace context 注入能力。[Python instrumentation](https://opentelemetry.io/docs/languages/python/libraries/)、[Python logging instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html)

落地状态：

1. `service.name`、`instance_id`、`workspace_id` 已进入 Rust/Python bootstrap；
2. control request、process start、Execution outbox、Reference refresh 已有 span/metric 入口；
3. OTLP Collector 已提供 traces 和 metrics pipeline，业务进程不依赖 Collector 可用性；
4. Python aiohttp client 和 logging instrumentation 已按可选依赖接入；
5. provider request、reconnect、queue depth 和 event lag 仍应按业务边界继续补充，不能一次性泛化埋点。

不建议现在替换 `tracing`。正确做法是让 OpenTelemetry 作为 `tracing` 的导出层。

### 3.2 SQLx migrations：统一 SQLite schema

Reference 和 Execution 现在通过 SQLx 的 `sqlx::migrate!` 使用独立、版本化的
`migrations/` 目录；不再把运行时 `CREATE TABLE IF NOT EXISTS` 作为默认 schema 管理方案。
当前策略是全量迁移：已有旧数据库文件不由业务进程自动升级，部署时应重建数据库或由发布
流程执行一次性外部迁移，应用只接受当前 SQLx schema。

已完成空库初始化、重启恢复和真实子进程异常退出后的 outbox 恢复测试。

### 3.3 cargo-nextest：替换 CI 中的默认测试运行器

`cargo test` 仍然保留作为标准兼容入口，但 CI 和本地开发可以引入 `cargo-nextest`。它是面向 Rust workspace 的专用测试运行器，支持更好的测试隔离、并行和失败报告。[cargo-nextest 文档](https://docs.rs/cargo-nextest)

建议：

```bash
cargo nextest run --workspace --all-targets
    cargo test --workspace --doc
```

不建议在当前 Rust workspace 仍无法稳定编译时马上引入。应先恢复编译基线，再替换 CI 测试执行层。

### 3.4 Ruff：统一 Python lint 和 formatter

当前 Python 项目只有 pytest，没有统一的 lint、import 排序和格式检查。Ruff 可以同时承担主要 lint 和 formatter 能力，并兼容大量 Flake8、isort、pyupgrade 等规则。[Ruff linter](https://docs.astral.sh/ruff/linter/)、[Ruff formatter](https://docs.astral.sh/ruff/formatter/)

已加入 dev dependency 和 CI：

```bash
uv run ruff check kairospy tests
uv run ruff format --check kairospy tests
```

Pyright 已加入 CI，并对 `kairospy` 源码形成阻断门禁；当前源码检查为 0 errors、0 warnings。
测试 fake 已纳入同一 Pyright 门禁；当前 `kairospy` 和 `tests` 均要求 0 errors、0 warnings。

### 3.5 proptest、Hypothesis 和 Criterion

当前交易系统最需要的不是更多普通样例，而是边界、乱序、重复和长序列测试。

Rust：

- `proptest`：验证 order book、decimal、reservation、状态机不变量；
- `criterion`：建立 Market、snapshot、replay、outbox 的性能基线。[proptest](https://docs.rs/proptest/latest/proptest/)、[Criterion](https://docs.rs/criterion/latest/criterion/)

Python：

- `Hypothesis`：验证配置解析、事件序列、快照解码、策略状态转换。[Hypothesis](https://hypothesis.readthedocs.io/en/latest/)

## 4. P1：可以引入，但需要架构判断

### 4.1 NATS JetStream：当前不引入

当前项目没有必要引入 NATS JetStream。项目是以单机 workspace、进程 supervisor、Actor 单一状态所有者、本地 SQLite/outbox、Unix socket 和 Aeron/mmap 为核心的架构。为了可靠业务事件增加独立消息服务器，会带来部署、权限、监控、网络故障和数据运维成本，但暂时没有对应的业务收益。

更合理的分工是：

```text
Market hot path / low latency snapshot  -> Aeron + mmap
Local control / health / CLI             -> Unix socket
State and audit durability              -> SQLite + local outbox
Actor command/event flow                -> in-process bounded channel
```

可靠性问题优先通过本地事务、显式 event id、outbox ack/retry、checkpoint、重启恢复、provider reconciliation 和故障注入测试解决。

只有未来出现多机部署、跨主机消费、独立事件订阅方、异步任务积压或跨服务水平扩展需求时，才重新评估 JetStream、Kafka 或其他消息系统。即使届时引入，也应放在非热路径，不替换 Aeron 和本地 Actor mailbox。

### 4.2 tokio-tungstenite：统一 Rust 异步 WebSocket

当前 Integration 使用同步 `tungstenite`，并通过线程承载 WebSocket。`tokio-tungstenite` 提供 Tokio 集成，WebSocket stream 实现 `Stream`/`Sink`，适合与现有 Tokio process runtime 组合。[tokio-tungstenite 文档](https://docs.rs/tokio-tungstenite/latest/tokio_tungstenite/)

适合替换的条件：

- 连接管理要统一进入 Tokio runtime；
- 需要多个连接共享异步 cancellation；
- 需要统一 timeout、select、backpressure 和 reconnect；
- 当前线程模型已经成为连接扩展瓶颈。

当前已完成 Integration provider WebSocket 的统一迁移。Provider 仍实现已有同步
`MarketStreamConnection`，但底层 socket I/O 运行在独立 Tokio worker 中：读取和写入由
`Stream`/`Sink` 驱动，业务线程通过有界的同步 facade 获取事件和发送订阅命令。这样不
需要在一次改造中把所有 REST、replay、polling 连接都改成 async trait，也避免在 Tokio
 engine 中直接进行阻塞 socket read。共用 worker 位于
`crates/kairos-integration/src/services/streams/tokio_socket.rs`，避免每个 Provider
重复实现连接线程、命令和事件转发。

每个 Provider 仍必须独立验证心跳、非阻塞轮询、订阅恢复、sequence gap 和 REST snapshot reconcile；
当前已补齐 Massive 的 Ping/Pong + sequence、OKX 私有流 Ping/Pong + account event、Binance
Spot depth gap 与 reconnect 测试。后续新增 Provider 仍按同一契约补测，不能把“换成异步库”
当作恢复语义已经完成。

### 4.3 SQLx：异步 SQLite persistence 的渐进落地

SQLx 提供 async 数据库访问、连接池、compile-time checked queries 和 migration 管理，支持 SQLite、PostgreSQL、MySQL 等。[SQLx 官方仓库](https://github.com/launchbadge/sqlx)、[SQLx migrations](https://docs.rs/sqlx/latest/sqlx/migrate/)

但当前项目的状态 owner 和持久化代码大量使用同步 Actor/worker，把 SQLite 访问统一到 SQLx 仍需要处理：

- async/sync 边界重新设计；
- connection pool 生命周期变化；
- 编译时间增加；
- SQLite 行为和事务测试需要重做；
- 业务 actor 与数据库 executor 耦合。

当前已完成 Execution 和 Reference state persistence 的迁移切片：

- server 和 CLI 的 Execution state store 已切换为 `SqlxExecutionStore`；
- 使用 `sqlx::migrate!` 执行 `migrations/0001_execution_state.sql`；
- checkpoint、outbox append、event + checkpoint 原子事务、pending 查询和 ack 均由 SQLx 承载；
- Execution audit 的 order/intent event 写入、幂等键、查询过滤和 migration 也已由
  `SqlxExecutionAudit` 承载，server 默认不再使用第二套 SQLite audit sink；
- 现有同步 `ExecutionStateStore` 仍是业务边界，SQLx 只在 services persistence 内部出现；
- 原同步 SQLite Store 已删除；不保留第二套运行时 Store 或旧 schema 自动兼容路径。
- Reference 的 catalog、lifecycle、publication outbox、provider cursor 和 last-good
  snapshot 已由 `SqlxCatalogStore` / `SqlxProviderSyncStore` 承载，并通过同一套
  `sqlx::migrate!` schema 初始化；同步 application trait 通过 Store 持有的长生命周期
  current-thread Tokio runtime 调用 SQLx，不会每次操作创建 runtime。
- Reference 只由当前 SQLx schema 承载，旧 JSON 结构不再由业务进程解释或改写。

SQLx 迁移验证清单：空库初始化、重启恢复、outbox append/ack、audit 幂等写入和真实进程
崩溃后的 outbox 恢复均已覆盖。

SQLx 0.9 需要 Rust 1.94+，workspace 已通过 `rust-toolchain.toml` 固定到 1.95。后续
schema 变更继续以一个 Store、一个 migration 和一组恢复测试为单位。

## 5. P1：依赖清理和统一

### 5.1 Rust Reqwest 版本统一

项目直接依赖已经统一到 Reqwest `0.13`；当前依赖树中仍出现 Reqwest `0.12`，来源是
OpenTelemetry OTLP 的传递依赖，不是业务 crate 直接选择的版本。另有部分 crate 使用
`blocking`，部分 Integration 也使用 blocking client。

建议：

- 继续保持 workspace 直接依赖统一版本，并等待上游传递依赖自然收敛；
- 统一 TLS feature；
- 明确哪些调用允许 blocking；
- 禁止在 Tokio async task 内直接执行阻塞 HTTP；
- 长期将 provider REST client 收敛到 async adapter 或隔离到 blocking worker。

这属于低风险、高收益的依赖治理，不需要更换 HTTP 框架。

### 5.2 Python 删除或下沉未使用的核心依赖

当前 `pyproject.toml` 的主依赖包含：

```text
httpx
requests
tenacity
pandas
pyarrow
```

但当前源码中主要使用的是 `aiohttp`，上述多个包没有对应的核心运行时 import。建议：

- 未使用的包从主依赖移除；
- Notebook/Data/Query 能力放入 optional extras；
- 只有真正使用 tenacity 时才保留；
- 通过 `uv tree` 和源码扫描定期清理依赖。

依赖越少，安装越快，供应链风险越低，版本冲突越少。

### 5.3 不要同时维护多套同类客户端

当前策略：

```text
Rust HTTP       -> reqwest（同步 provider REST 暂留在明确的 blocking adapter）
Rust WebSocket  -> 新增连接统一 tokio-tungstenite；旧同步连接按 Provider 切片迁移
Python server   -> aiohttp（如果继续使用现有 REST server）
Python client   -> 一个统一 HTTP client
```

不要让业务层知道具体 HTTP/WebSocket 库；具体库只能存在于 Integration adapter 或 infrastructure 层。
下一步应把阻塞 REST 调用收敛到专用 worker，并增加静态检查，禁止在 Tokio handler 内直接
构造或调用 `reqwest::blocking`。

## 6. P1：安全相关组件

Rust 的 API key、secret、token 和私钥不应长期以普通 `String` 暴露在结构体、Debug 输出或错误链中。

可以评估 `secrecy`，它提供 `SecretString`/`SecretBox`，通过显式 `ExposeSecret` 访问，并尝试在 drop 时清理秘密内容。[secrecy 文档](https://docs.rs/secrecy)

已落地：

- Integration API secret；
- access token；
- private key；
- listen key；
- 交易所认证材料。

Account/Execution 的连接配置已使用 `SecretString`，并有 Debug 脱敏测试；Python CLI 的
JSON/text/table 输出已统一递归 redaction 并有测试。Risk 当前没有外部凭据配置，因此不
强行引入无意义的 secret 字段；错误链仍应随着新 Provider/配置入口继续补充断言。

注意：它不能解决操作系统、交换区或进程内存中的所有秘密保护问题，也不能替代权限隔离和 secret manager。它的主要价值是减少 Debug、日志和无意序列化泄漏。

## 7. 不建议替换的组件

### 7.1 FlatBuffers

当前系统已经围绕 FlatBuffers 生成 Rust/Python contract、mmap snapshot 和跨进程读取建立了大量代码。除非确认 schema 演进、工具链或性能出现明确问题，否则不建议切换 Protobuf、Cap’n Proto 或 MessagePack。Reference 已加入 Rust/Python 共读的二进制 golden fixture，并在 CI 中重新生成两侧代码后检查工作树无漂移。

建议改进的是：

- schema version；
- generated code CI 校验；
- Rust/Python golden fixtures；
- 兼容性测试；
- contract 与 domain 的显式转换。

### 7.2 Aeron

Aeron 适合当前低延迟、顺序事件和高吞吐数据平面。NATS、Kafka 或 RabbitMQ 的可靠性模型与部署模型不同，不能简单按“更成熟”直接替换。

建议保留 Aeron 在 Market data plane；可靠控制事件和非热路径业务事件当前继续使用
SQLite outbox、checkpoint、重试和 reconciliation，不引入 NATS JetStream。

### 7.3 Tokio 和 Axum

当前项目已经使用 Tokio 的 process、Unix socket、timer、blocking worker 和 Axum control plane。替换成 Actix、Warp 或其他 runtime 的收益不足，迁移成本较高。

应把精力放在：

- async/blocking 边界；
- cancellation；
- backpressure；
- timeout；
- graceful shutdown；
- health/readiness。

### 7.4 Clap、Typer 和 Textual

CLI 和终端界面不是当前系统的主要质量瓶颈。保留现有选择，补充 lint、contract 测试和错误输出即可。

## 8. 推荐落地顺序

```text
第一阶段：依赖清理（已基本完成）
  统一 Reqwest 版本
  删除未使用 Python 主依赖
  固定 workspace dependency policy

第二阶段：工程基础组件（主体已完成）
  OpenTelemetry + Collector
  SQLx migrations
  Ruff
  cargo-nextest

第三阶段：测试和性能
  proptest
  Hypothesis
  Criterion
  provider contract golden tests

第四阶段：本地可靠性（核心闭环已完成）
  完善 SQLite migration、outbox ack/retry、checkpoint 和 reconcile
  已增加真实进程崩溃恢复与重复投递测试

第五阶段：连接模型评估（worker 与当前 Provider 语义测试已完成）
  已覆盖共用 Tokio worker、Massive/OKX heartbeat、Binance sequence gap/reconnect；新增
  Provider 按同一契约补测
  对比线程模型和 Tokio 模型的延迟、吞吐、重连复杂度

第六阶段：长期数据库演进
  只有确实需要 PostgreSQL 时再评估数据库方言与部署形态变化；当前 SQLx 同步 facade
  已通过长生命周期 runtime 避免运行时创建开销。
```

## 9. 最终建议

当前最值得做的不是大面积换库，而是以下五项：

1. `tracing + OpenTelemetry Collector`，补齐跨 Rust/Python 的可观测性；
2. SQLx migrations，统一本地 SQLite 的版本化 schema；不保留第二套 SQLite 驱动；
3. `cargo-nextest + proptest + criterion + Hypothesis`，补齐测试和性能工具链；
4. 统一 Reqwest 和 Python HTTP 依赖，清理未使用包；
5. 暂不引入 NATS JetStream；先完善 SQLite/outbox/checkpoint/reconcile 的本地可靠性闭环。

## 10. 持续维护与协作项

本轮设计对应的落地项已完成。以下是不会阻塞本轮交付的持续维护事项：

1. 随 schema 版本演进继续维护 Rust/Python FlatBuffers golden fixture 和生成代码 CI；
2. 随新 Provider/配置入口继续补充错误链、日志字段和 CLI 输出的 secret redaction 断言；
3. Integration 当前重构完成后，再由其 owner 统一收敛最终格式化和全仓库门禁策略。

已完成但需要持续维护的基础边界：同步 provider REST 已由长生命周期 blocking worker 承载，
CI 已禁止业务 async application 直接引入 `reqwest::blocking`；部分 Rust credential
Debug 与策略日志已覆盖 redaction 测试。`cargo-nextest` 已进入 CI 入口，隔离 target 下
workspace all-targets check/test 均已通过。

这些改造对现有业务边界侵入较小，也不会影响正在进行的 Execution 领域类型迁移。当前
workspace all-targets check 与 workspace all-targets tests 已通过；`cargo fmt --check`
仍会报告 Integration 重构分支和少量并行 Execution 格式变更，需由对应 owner 在重构
收敛后统一执行，不在本次工作中覆盖其最新代码。
