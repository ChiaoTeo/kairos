# Kairos 工程质量提升计划

## 1. 文档目的

本文总结当前系统最值得优先投入的工程质量工作，目标是提升系统的：

- 可诊断性；
- 可恢复性；
- 可测试性；
- 可演进性；
- 运行安全性；
- 变更可控性。

本文不包含 Execution 领域类型迁移工作。Execution 的 `OrderId`、数量、价格和其他领域类型正在进行中，后续应在该迁移完成后再统一收紧相关架构门禁。

当前仓库已经具备较好的模块骨架：`bin -> composition -> application -> services -> domain`。下一阶段重点不应是继续增加抽象，而是把质量规则固化为代码、测试、日志和 CI 门禁。

## 2. 当前基线

当前已具备：

- Rust 和 Python 双运行时；
- Account、Execution、Market、Reference、Risk、Integration 等业务模块；
- FlatBuffers contract 和生成代码；
- Unix socket、mmap、事件流和快照等运行时通道；
- Actor、checkpoint、outbox 和进程 supervisor 基础能力；
- Python 测试和部分 Rust 架构测试；
- `cargo fmt`、pytest 和领域架构检查。

当前主要问题不是缺少模块，而是质量能力尚未形成统一标准：日志字段不统一、错误分类不完整、恢复语义不够明确、运行指标不足、测试更多集中在 happy path、CI 对架构规则的覆盖仍有限。

## 3. 优先级总览

| 优先级 | 主题 | 目标 |
|---|---|---|
| P0 | 统一日志和可观测性 | 出现问题时能回答“哪里、何时、哪个实例、哪个请求、哪条事件、造成什么影响” |
| P0 | 恢复和幂等 | 进程、连接、发布失败后可以安全重试和恢复 |
| P0 | 可编译和可验证基线 | 所有迁移分支都保持可编译、可测试、可回滚 |
| P1 | 错误模型 | 区分业务拒绝、暂时故障、永久故障和数据损坏 |
| P1 | 消息背压和生命周期 | 队列满、服务停止、连接断开时行为可预测 |
| P1 | 合同和版本治理 | Rust、Python、FlatBuffers 和外部接口不会静默漂移 |
| P1 | 测试体系 | 覆盖恢复、乱序、重复、超时、故障注入和边界数据 |
| P2 | 架构门禁和交付流程 | 将人工约定转化为自动检查和小步提交 |
| P2 | 性能和容量基线 | 在优化前知道吞吐、延迟、内存和队列容量的真实瓶颈 |

## 4. P0：日志和可观测性规范

日志不应只是“发生了什么”的文本记录，而应成为系统运行状态、事件追踪和故障恢复的基础数据。

### 4.1 统一日志事件模型

Rust 和 Python 日志都应输出 JSONL，并使用统一字段。建议每条日志至少包含：

```json
{
  "schema_version": 1,
  "system_time": "2026-08-10T12:00:00.123Z",
  "event_time": "2026-08-10T11:59:59.900Z",
  "event_time_source": "market_event",
  "level": "INFO",
  "event": "execution_order_submitted",
  "component": "execution",
  "module": "execution.service",
  "process_id": "execution",
  "instance_id": "...",
  "workspace_id": "...",
  "correlation_id": "...",
  "causation_id": "...",
  "request_id": "...",
  "event_id": "...",
  "event_sequence": 12345,
  "duration_ms": 3.2,
  "result": "accepted"
}
```

字段规则：

- `event` 使用稳定的 `snake_case` 事件名，不使用自由文本作为主要检索条件；
- `component` 表示业务模块，`module` 表示代码模块；
- `system_time` 表示机器记录时间；
- `event_time` 表示业务事件时间；
- `event_time_source` 必须说明时间来自市场事件、交易所事件、系统时钟或未知来源；
- `correlation_id` 用于贯穿一次业务流程；
- `causation_id` 用于标识触发当前事件的上游事件；
- `request_id` 用于一次控制面请求；
- `event_id` 用于一次不可变事件；
- `event_sequence` 只表示事件流序号，不得与时间戳混用。

### 4.2 事件命名规范

事件名应描述已经发生的事实，使用过去式或完成式语义：

```text
process_starting
process_ready
process_stopped
market_stream_connected
market_stream_gap_detected
execution_order_accepted
execution_order_rejected
execution_order_reconciled
account_snapshot_published
risk_reservation_created
risk_reservation_released
```

不要使用含义模糊的日志名：

```text
processing
update
handle_request
done
error
message_received
```

事件名应能直接用于指标聚合、告警规则和故障检索。

### 4.3 日志级别规范

| 级别 | 使用场景 |
|---|---|
| `TRACE` | 极细粒度调试，仅临时开启 |
| `DEBUG` | 队列轮询、数据解析、重试细节 |
| `INFO` | 生命周期、成功的业务状态变化、连接建立 |
| `WARN` | 可恢复异常、重试、降级、数据延迟、队列接近满 |
| `ERROR` | 当前操作失败、需要人工关注或影响业务能力 |
| `FATAL` | 进程无法继续运行，通常由顶层统一记录 |

禁止在高频行情循环中无条件输出 `INFO`；禁止把正常拒单、参数校验失败全部记录为 `ERROR`；禁止捕获异常后只输出字符串而丢失错误类型和上下文。

### 4.4 错误日志规范

错误日志至少包含：

```text
error_code
error_kind
retryable
operation
resource_id
attempt
backoff_ms
cause
```

推荐分类：

```text
validation_error
business_rejection
transport_error
timeout
dependency_unavailable
persistence_error
serialization_error
data_corruption
invariant_violation
```

错误日志必须保留原始错误链，不能只使用 `error.to_string()` 作为长期接口。对外响应可以使用稳定错误码，内部日志保留详细 cause。

### 4.5 交易审计日志与普通运行日志分离

订单、成交、撤单、风控授权、reservation、账户结算和对账结果属于审计事实，不应只依赖普通文本日志。

建议分为三类：

1. **运行日志**：用于诊断进程和依赖状态；
2. **业务事件**：用于状态重建和跨模块传播；
3. **审计记录**：用于合规、复盘和人工调查。

审计记录应具备：

- 不可变事件 ID；
- 操作者或触发方；
- 请求和因果关联；
- 前置状态版本；
- 后置状态版本；
- 原始业务输入摘要；
- 结果和拒绝原因；
- 事件时间和接收时间。

### 4.6 日志脱敏和安全

禁止记录：

- API secret、私钥、完整 token；
- 完整认证请求头；
- 未脱敏账户凭据；
- 不必要的个人信息。

应对账户、订单和请求标识做一致化处理。诊断需要时使用 hash 或局部掩码，而不是输出完整敏感值。

### 4.7 日志必须配套指标

仅有日志不够。建议为每个服务建立最小指标集：

```text
process_up
process_ready
operation_total
operation_failed_total
operation_duration_ms
queue_depth
queue_rejected_total
event_lag_ms
event_gap_total
reconnect_total
retry_total
snapshot_generation
outbox_pending
outbox_oldest_age_ms
checkpoint_age_ms
```

日志回答“发生了什么”，指标回答“是否正在恶化”，快照和审计回答“当前状态是什么”。三者不能互相替代。

### 4.8 日志落地验收标准

- Rust/Python 输出字段一致；
- 所有进程拥有稳定的 `component`、`process_id`、`instance_id`；
- 所有跨进程请求带 `correlation_id`；
- 所有业务事件带 `event_id` 和序列信息；
- 错误日志可以区分是否可重试；
- 高频循环默认不刷 INFO；
- 日志可被 JSON 工具直接过滤和聚合；
- 关键指标和日志事件存在一一对应关系；
- 测试验证敏感字段不会出现在日志中。

## 5. P0：恢复、幂等和持久化

所有带外部副作用的操作都必须回答三个问题：

1. 重复执行会发生什么？
2. 执行到一半进程崩溃会发生什么？
3. 本地状态和外部状态不一致时谁负责修复？

重点工作：

- 为业务事件建立显式 `event_id`，不要用序列化后的 payload 充当唯一键；
- 明确 checkpoint、outbox 和状态更新的事务边界；
- 增加 outbox 发布状态、重试次数、最后错误和 dead-letter；
- 为外部订单、成交和账户事件建立幂等键；
- 明确重启后的 reconcile 流程；
- 测试“写入成功但发布失败”和“发送成功但本地崩溃”两类故障。

## 6. P0：保持可编译、可测试的开发基线

建议将以下命令作为每次提交的最小门禁：

```bash
cargo fmt --all -- --check
cargo check --workspace --all-targets --locked
cargo test --workspace --all-targets --locked
uv run pytest -q
uv run python scripts/check_domain_architecture.py
git diff --check
```

当前 Execution 领域类型迁移完成前，不新增针对该迁移的额外架构限制，但必须保证每个迁移提交可编译，避免大量类型错误堆积到最后集中解决。

## 7. P1：错误模型和依赖故障处理

逐步替换 `Result<T, String>`，为业务模块建立明确错误枚举。错误需要支持：

- 稳定错误码；
- 是否可重试；
- 是否需要熔断；
- 是否影响进程健康；
- 是否需要人工介入；
- 原始错误链和上下文。

连接器、存储、序列化和业务拒绝必须分开处理，不能全部转换成普通字符串。

## 8. P1：消息队列、背压和进程生命周期

所有队列都应明确：

- 容量；
- 满载行为；
- 超时行为；
- 丢弃策略；
- 优先级；
- 监控指标；
- 停止时如何 drain；
- 消费者退出后生产者如何感知。

建议重点测试：

- exchange event 持续高于消费速度；
- command 队列满；
- query 洪峰；
- provider 断线重连；
- stop 请求与业务请求同时到达；
- 进程收到 SIGTERM 后仍有未发布事件。

## 9. P1：Contract 和生成代码治理

FlatBuffers schema、Rust generated code 和 Python generated code 必须视为一个版本化单元。

建议：

- 生成代码只能由脚本产生；
- CI 中重新生成并检查 diff；
- 为字段删除、重命名和默认值变化定义兼容策略；
- 为每个消息保留 schema version；
- 增加跨语言 golden fixture；
- Rust 写入的数据必须能被 Python 读取，反之亦然；
- 对未知字段和未知 enum 保留明确降级行为。

## 10. P1：测试体系升级

现有测试应从 happy path 扩展到状态和故障测试。

### 单元测试

- 领域不变量；
- Decimal 边界；
- 状态转换；
- 序列连续性；
- 事件去重；
- 配置校验。

### 集成测试

- Unix socket 断线；
- mmap 快照切换；
- provider 重连；
- SQLite 重启恢复；
- outbox 发布和确认；
- Rust/Python contract 互读。

### 故障注入测试

- 重复消息；
- 乱序消息；
- 消息 gap；
- 队列满；
- 写入中断；
- 外部请求超时；
- 外部状态与本地状态冲突；
- 进程在关键步骤被终止。

### 非功能测试

- Market 事件吞吐；
- snapshot 发布延迟；
- Execution command 延迟；
- outbox backlog 增长；
- 内存增长；
- 长时间运行稳定性。

## 11. P2：架构门禁和代码组织

继续完善 `scripts/check_domain_architecture.py`，逐步加入：

- 禁止跨模块访问 `services`；
- 禁止 Domain 依赖 transport 或 SDK；
- 禁止 Application 暴露 vendor payload；
- 禁止 Application 暴露 persistence record；
- 检查日志事件是否使用统一字段；
- 检查新增进程是否提供 health、ready、stop；
- 检查新增外部副作用操作是否具有幂等键。

大文件应以职责为依据拆分，而不是机械按行数拆分。优先整理 HTTP 适配、状态循环、持久化、事件发布和业务用例之间的边界。

## 12. P2：性能和容量基线

在没有 benchmark 和 profile 证据前，不建议引入零拷贝、复杂缓存或新的通用并发抽象。

至少建立以下基线：

- 单秒行情事件处理量；
- order book 更新延迟；
- snapshot 写入和读取延迟；
- command/query 队列最大深度；
- outbox 发布吞吐；
- 单进程内存增长；
- reconnect 后恢复耗时。

性能结果应保存测试数据、机器环境、配置和版本，避免只记录一个孤立数字。

## 13. 建议实施顺序

```text
第一阶段：日志和可观测性
  统一 JSONL 字段、事件名、错误字段、correlation_id 和指标

第二阶段：恢复和幂等
  event_id、outbox、checkpoint、重试、reconcile、故障注入

第三阶段：工程基线
  cargo check/test、pytest、schema regeneration、架构检查统一门禁

第四阶段：运行时可靠性
  队列背压、生命周期、断线恢复、健康检查和优雅退出

第五阶段：测试和性能
  状态机测试、跨语言 contract 测试、benchmark 和长稳测试

第六阶段：架构整理
  在功能和恢复语义稳定后，再拆分过大的 Application、Process 和 Composition 文件
```

## 14. 完成标准

当本计划的核心内容完成后，系统应满足：

- 任何关键业务流程都可以通过 `correlation_id` 串起；
- 任何关键业务事件都有不可变 `event_id`；
- 日志、指标、快照和审计记录职责清晰；
- 进程重启不会导致重复副作用或无法解释的状态；
- 外部连接断开后有可测试的恢复路径；
- 队列满载时行为明确且可观测；
- Rust、Python 和 FlatBuffers contract 有自动兼容性检查；
- 关键故障场景有自动化测试；
- 每次提交都能通过最小质量门禁；
- Execution 领域类型迁移可以独立推进，不被其他质量改造反复打断。
