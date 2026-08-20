# Decision 0005：Conflux 纳管 typed Stream、View 与 Control 的运行时

- Status: Accepted
- Scope: Conflux 驱动的业务进程及其进程边界资源

## Context

Risk、Execution、Account、Market 与 Reference 曾分别在 binary、composition 或 application 中创建 Axum server、UDS listener、Aeron publisher 与 mmap publisher。业务请求虽然最终进入 Actor，但 transport 的启动、readiness、健康、背压和关闭分散在各模块，动态 Market view 甚至由 Application 直接创建 mmap writer。

业务行为不应随请求来自 HTTP、WebSocket、UDS、TCP 或进程内 handle 而变化；对外 current view 也不应与模块私有 journal/checkpoint persistence 混为一谈。

## Decision

Conflux 按交互语义纳管三类平台能力：

- Stream：Aeron 模块事件流，以及由 typed `ManagedConnections` 管理的 provider WebSocket 数据流；
- View：mmap current view 与原子替换的普通文件 current view；
- Control：HTTP over UDS、HTTP over TCP 与 WebSocket over TCP。

Contract 定义 typed request/response/event/view、wire codec、业务 key 到安全路径的解析、sequence/generation 与一致性 metadata。业务 Composition 声明需要的输出及其配置，但不创建或持有 publisher/writer。Conflux 创建并独占 Aeron publication、mmap writer 与 file writer，同时拥有 listener、session、connection、队列、resource state、revision、readiness、失败状态和 shutdown。Actor 只接收或产生 typed 值，不接触 Axum、socket、HTTP method 或 WebSocket frame。

依赖边界以 Cargo package 为准：业务主包（包括 `bin/` server、Composition、Application 与 Services）依赖 Conflux 和本模块 Contract，不直接依赖 `kairos-transport`；Contract 可以依赖 protocol/transport 来实现 wire codec、client、reader 与 stream adapter；Conflux 可以依赖 transport 来创建、持有和关闭具体 I/O 资源。Contract 对 transport 的依赖不授权业务主包绕过 Conflux 取得资源所有权。

所有 Control transport 复用同一个 module-owned `HttpControlCodec`，解码后通过唯一的 `ConfluxHandle` typed ingress 进入 Actor。WebSocket 控制帧包含版本和 request id，用于 correlation；HTTP 与 WebSocket 的请求超时发生在提交后时返回 `result_unknown`，队列关闭且未提交时返回 `not_sent`。业务 Contract 继续负责明确拒绝与成功响应。

固定输出由 Composition 通过 `system.outputs().aeron/mmap/file.declare(...)` 声明。声明只包含通用 transport 参数、resource key 与 revision；Conflux 根据声明创建并持有底层管道。Conflux 不提供 `AccountAeronEventOutput`、`RiskMmapViewOutput` 或 `enable_account_*` 这类了解业务名字的 API。

运行时才出现的 key（例如 Market 动态订阅）由 Actor 在 `handle` 中借用 `context.outputs().mmap` 声明。业务 Contract 先把 typed key 解析为安全路径，业务再提交通用声明；不传 selector 或构造闭包，也不能访问 Conflux 内部 collection。发布时 Actor 同样只借用 `context.outputs()` 提交已经按 Contract 编码的 payload。Conflux 在发布成功或失败后更新资源状态，并在进程停止阶段统一终止输出资源。

普通文件 View 使用带版本、checksum 和 `SnapshotEnvelopeMetadata` 的完整替换文件，并通过临时文件、flush 与 rename 原子发布。它是对外派生视图；journal、checkpoint 和权威恢复状态仍由模块私有 persistence 所有。

不使用 `Any`、`TypeId`、downcast、开放式 registry 或 `serde_json::Value` 业务 envelope。不同 Contract 保持具体 typed collection，不通过类型擦除追求表面统一。

## Consequences

- Risk、Execution、Account、Market、Reference 与 Capital 不再直接拥有 Axum 或 UDS/TCP listener；Control 的 readiness、health file 和 shutdown 行为一致。
- Risk 的 Aeron event、mmap view 与 file view，以及 Execution/Account/Market/Reference/Capital 的固定输出，由业务 Composition 声明、Conflux 创建并持有。
- Market 保留动态订阅语义；Market Contract 决定 typed view key 与路径，Conflux 在 Context 借用期内创建和管理动态 mmap 管道。
- HTTP/TCP、WebSocket/TCP、mmap 与普通文件具有真实协议或文件端到端测试；UDS 由 Risk 的 Contract 端到端测试覆盖。
- Provider WebSocket 继续使用各 provider 的具体 typed connection collection；在出现模块间 WebSocket event stream 的真实生产调用方前，不增加通用业务 stream envelope。
- 新模块若需要进程边界能力，应先扩展所属 Contract；业务 Composition 通过 Conflux 通用输出 API 声明管道，不得持有底层 publisher/writer，不得把业务命名的构造 API 加入 Conflux，也不得在业务 crate 中重新建立 transport host。
- 业务主包及其 server 不直接依赖 `kairos-transport`；Contract 保留实现协议与 transport adapter 所需的依赖。
