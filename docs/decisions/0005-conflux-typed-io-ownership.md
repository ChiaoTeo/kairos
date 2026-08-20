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

Contract 定义 typed request/response/event/view、wire codec、sequence/generation 与一致性 metadata。业务 Composition 选择具体 Contract adapter，构造 publisher，并决定业务 key、view kind、路径和编码规则。Conflux 拥有 listener、session、connection、队列、resource state、revision、readiness、恢复和 shutdown；已构造的 typed publisher 交给 Conflux 的通用 resource collection 纳管。Actor 只接收或产生 typed 值，不接触 Axum、socket、HTTP method 或 WebSocket frame。

所有 Control transport 复用同一个 module-owned `HttpControlCodec`，解码后通过唯一的 `ConfluxHandle` typed ingress 进入 Actor。WebSocket 控制帧包含版本和 request id，用于 correlation；HTTP 与 WebSocket 的请求超时发生在提交后时返回 `result_unknown`，队列关闭且未提交时返回 `not_sent`。业务 Contract 继续负责明确拒绝与成功响应。

固定输出由 Composition 构造后调用通用 `bind_output` 登记。Conflux 不提供 `AccountAeronEventOutput`、`RiskMmapViewOutput` 或 `enable_account_*` 这类了解业务名字和 Contract 构造规则的 API。

运行时才出现的 key（例如 Market 动态订阅）由 Actor 在 `handle` 中调用通用 `Context::declare_output` 声明。业务模块传入 typed key、目标 typed collection 和构造闭包；Context 只应用 revision、去重和 resource state 规则。该接口只用于输出资源，并使用静态泛型，不使用类型擦除或开放注册表。

普通文件 View 使用带版本、checksum 和 `SnapshotEnvelopeMetadata` 的完整替换文件，并通过临时文件、flush 与 rename 原子发布。它是对外派生视图；journal、checkpoint 和权威恢复状态仍由模块私有 persistence 所有。

不使用 `Any`、`TypeId`、downcast、开放式 registry 或 `serde_json::Value` 业务 envelope。不同 Contract 保持具体 typed collection，不通过类型擦除追求表面统一。

## Consequences

- Risk、Execution、Account、Market 与 Reference 不再直接拥有 Axum 或 UDS/TCP listener；Control 的 readiness、health file 和 shutdown 行为一致。
- Risk 的 Aeron event、mmap view 与 file view，以及 Execution/Account/Market/Reference 的固定输出，由业务 Composition 创建、Conflux 通用 resource collection 持有。
- Market 保留动态订阅语义；Market 决定 typed view 的 key 与构造方式，Context 负责动态登记和生命周期状态。
- HTTP/TCP、WebSocket/TCP、mmap 与普通文件具有真实协议或文件端到端测试；UDS 由 Risk 的 Contract 端到端测试覆盖。
- Provider WebSocket 继续使用各 provider 的具体 typed connection collection；在出现模块间 WebSocket event stream 的真实生产调用方前，不增加通用业务 stream envelope。
- 新模块若需要进程边界能力，应先扩展所属 Contract；业务 Composition 通过 Conflux 通用资源 API 绑定 adapter，不得把业务命名的构造 API加入 Conflux，也不得在业务 crate 中重新建立 transport host。
