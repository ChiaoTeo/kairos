# Decision 0005：Conflux 纳管 typed Stream、View 与 Control 的运行时

- Status: Accepted, control transport superseded by
  [Conflux JSON-RPC control boundary](../architecture/conflux-jsonrpc-control.md); former view mechanics
  superseded by [Decision 0034](0034-unified-current-view-storage.md)
- Scope: Conflux 驱动的业务进程及其进程边界资源

## Context

Risk、Execution、Account、Market 与 Reference 曾分别在 binary、composition 或 application 中创建 Axum server、UDS listener、Aeron publisher 与独立 current-view publisher。业务请求虽然最终进入 Actor，但 transport 的启动、readiness、健康、背压和关闭分散在各模块，动态 Market view 甚至由 Application 直接创建 writer。

业务行为不应随请求来自 HTTP、WebSocket、UDS、TCP 或进程内 handle 而变化；对外 current view 也不应与模块私有 journal/checkpoint persistence 混为一谈。

## Decision

Conflux 按交互语义纳管三类平台能力：

- Stream：Aeron 模块事件流，以及由 typed `ManagedConnections` 管理的 provider WebSocket 数据流；
- View：该项的早期机制已由 Decision 0034 的 owner-scoped LMDB indexed current view 取代；
- Control：进程控制由 Conflux 纳管；当前业务模块使用 Contract-owned
  JSON-RPC over Conflux runtime。

Contract 定义 typed request/response/event/view、wire codec、业务 key、sequence/generation 与一致性 metadata。业务 Composition 声明需要的输出及其配置，但不创建或持有 publisher/writer。Conflux 创建并独占 Aeron publication，同时拥有 listener、session、connection、队列、resource state、revision、readiness、失败状态和 shutdown。Current view 已迁移为由各业务 owner Actor 原子提交的 owner-scoped LMDB indexed store，具体边界见 Decision 0034。Actor 只接收或产生 typed 值，不接触 Axum、socket、HTTP method 或 WebSocket frame。

依赖边界以 Cargo package 为准：业务主包（包括 `bin/` server、Composition、Application 与 Services）依赖 Conflux 和本模块 Contract，不直接依赖 `kairos-transport`；Contract 可以依赖 protocol/transport 来实现 wire codec、client、reader 与 stream adapter；Conflux 可以依赖 transport 来创建、持有和关闭具体 I/O 资源。Contract 对 transport 的依赖不授权业务主包绕过 Conflux 取得资源所有权。

Control transport 已收敛到 module-owned JSON-RPC service trait。Conflux 将
jsonrpsee service 调用序列化进唯一 Actor ingress；请求超时发生在提交后时返回
`result_unknown`，队列关闭且未提交时返回 `not_sent`。业务 Contract 继续负责明确拒绝与成功响应。

固定事件输出由 Composition 通过 `system.outputs().aeron.declare(...)` 声明。声明只包含通用 transport 参数、resource key 与 revision；Conflux 根据声明创建并持有底层管道。Conflux 不提供了解业务名字的输出 API。

运行时才出现的 current-view key（例如 Market 动态订阅）由业务 Contract 编码为稳定的 LMDB binary key；同一 owner 的 Actor 在一次事务中提交该业务事件产生的全部 indexed mutations。Current view 是对外派生视图；journal、checkpoint 和权威恢复状态仍由模块私有 persistence 所有。

不使用 `Any`、`TypeId`、downcast、开放式 registry 或 `serde_json::Value` 业务 envelope。不同 Contract 保持具体 typed collection，不通过类型擦除追求表面统一。

## Consequences

- Risk、Execution、Account、Market、Reference 与 Capital 不再直接拥有 Axum 或 UDS/TCP listener；Control 的 readiness、health file 和 shutdown 行为一致。
- Risk 的 Aeron event 由业务 Composition 声明、Conflux 创建并持有；各 owner 的 current view 由其 LMDB indexed store 持有。
- Market 保留动态订阅语义；Market Contract 决定 typed view key、named database 与专用 FlatBuffers current root。
- JSON-RPC control、Aeron 与 indexed current view 具有真实协议或跨语言端到端测试。
- Provider WebSocket 继续使用各 provider 的具体 typed connection collection；在出现模块间 WebSocket event stream 的真实生产调用方前，不增加通用业务 stream envelope。
- 新模块若需要进程边界能力，应先扩展所属 Contract；业务 Composition 通过 Conflux 通用输出 API 声明管道，不得持有底层 publisher/writer，不得把业务命名的构造 API 加入 Conflux，也不得在业务 crate 中重新建立 transport host。
- 业务主包及其 server 不直接依赖 `kairos-transport`；Contract 保留实现协议与 transport adapter 所需的依赖。
