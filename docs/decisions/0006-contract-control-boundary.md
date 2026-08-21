# Decision 0006：Contract Control 边界与 Conflux 回调入口

- Status: Accepted, refined by
  [Conflux JSON-RPC control boundary](../architecture/conflux-jsonrpc-control.md)
- Scope: 模块 control contract、Conflux control runtime、业务进程 server 边界

## Context

Conflux 已经提供 typed handle，将进程边界请求送回 Actor 的单一事件循环。此前各业务模块逐步把 HTTP/UDS listener、readiness、shutdown 和请求超时交给 Conflux，但 control contract 的定义仍容易分散成多份：Contract DTO、HTTP codec、客户端路径拼接、服务端业务分发和外部文档可能各自维护。

Contract 应该是客户端和服务端共同面对的窗口。客户端通过 Contract 知道如何调用，服务端通过同一个 Contract 嵌入 Conflux，并只实现业务处理逻辑。Conflux 负责运行时边界；业务模块负责自己的对外语义。

## Decision

模块的 control surface 由该模块的 `contract/` crate 拥有。Contract crate 定义具体对外接口，包括 operation 名称、request/response 类型、路径和方法语义、业务错误响应、幂等与 readiness 语义。Rust client、Python client、服务端 Conflux adapter 和 contract tests 必须从同一个 Contract 定义出发，不能维护互相漂移的第二套接口。

Conflux 只提供通用 control runtime：

- 接收 HTTP over UDS、HTTP over TCP、WebSocket over TCP 等 transport；
- 调用 Contract 提供的 adapter 将 transport request 解码为 typed control request；
- 通过 `ConfluxHandle` 将 typed request 投递到 Actor；
- 处理队列关闭、请求超时、readiness、shutdown、health file、tracing 和 metrics；
- 调用 Contract 提供的 adapter 将 typed response 编码回 transport response。

业务模块的主 crate 不得自己建立或处理 control server 层。尤其是 Account、Risk、Execution、Market、Reference 等业务层不得在 `application/`、`services/` 或 `bin/` 中直接拥有 Axum handler、UDS/TCP listener、HTTP route match、WebSocket control session、请求 body 限制、HTTP status 映射或 stop/readiness transport 逻辑。Binary 只能解析启动参数、调用 composition，并运行由 Conflux 纳管的进程。

业务处理只允许通过 Conflux 的 typed callback 进入 Actor。服务端应用实现 typed control request 的业务分发，例如 `handle_control` 或当前迁移期的等价入口；它不读取 HTTP method/path/body，也不构造 transport response。所有 control 请求都必须经过：

```text
transport
  -> Contract decode
  -> ConfluxHandle typed ingress
  -> Actor/Application business handling
  -> Contract encode
  -> transport response
```

Control 服务协议直接采用 `jsonrpsee`。`kairos-protocol` 只提供项目级适配封装：重导出 jsonrpsee 的 service/client/server 宏与核心类型，定义 runtime error code、Conflux adapter 约束和测试辅助。Conflux 只实现并消费该协议的 runtime adapter，将 jsonrpsee service 接到 `ConfluxHandle` 和 Actor callback；它不拥有业务入口定义。`kairos-protocol` 不得定义 Account、Risk、Execution、Market、Reference 或 Capital 的具体业务 method，也不得引入会绕过 typed Contract 的开放式 registry、`Any`/downcast 或 `serde_json::Value` 业务 envelope。

业务模块的 `contract/` crate 定义自己的具体对外接口。一个模块的 operation 是否存在、如何命名、请求字段如何验证、成功和业务拒绝如何表达，都由该模块 Contract 拥有。跨业务调用者只能依赖 owner Contract；不得导入另一个业务模块主 crate 的 application、services、domain 或 server 细节。

## Required Shape

后续 control API 应收敛到 jsonrpsee service-first 的形态：每个 RPC method 只在 owner contract 中定义一次，并由 jsonrpsee 派生 client facade 与 server trait。Conflux adapter 实现生成的 server trait，将调用提交给 Actor。边界是 Contract-owned service definition，不是 Conflux route 或手写 HTTP codec。

迁移期兼容命名已经被 JSON-RPC control boundary 收口。当前业务模块新增或重构的
control surface 必须直接使用 Contract-owned jsonrpsee service trait、Conflux
actor invocation adapter 和 typed RPC actor method，不再保留迁移期兼容 facade。

## Prohibited Patterns

- 在业务模块主 crate 中新增 Axum route、HTTP listener、UDS listener 或 WebSocket control session。
- 在 `application/` 或 `services/` 中匹配 HTTP method、path、query string、status code 或 content type。
- 在业务 binary 中将 HTTP 请求直接映射到 application 方法。
- 为同一个 operation 同时维护 Contract DTO、手写 server route、手写 client route 和手写文档中的多份路径定义。
- 让 Conflux 暴露业务命名 API，例如 `enable_execution_intents_endpoint` 或 `AccountControlServer`。
- 为了统一 control surface 而把业务 command/query/result 放入 `kairos-protocol` 或 primitives。

## Consequences

- Contract crate 成为 control surface 的唯一事实源。
- Conflux 继续保持平台 runtime 身份，不变成业务 HTTP 框架。
- 服务端业务逻辑保留在 Actor/Application 的 typed callback 内，mutable state 仍只有一个所有者。
- Client 和 server 使用同一份 Contract 定义，减少路径、status 和 payload shape 漂移。
- `kairos-protocol` 可以沉淀 jsonrpsee 适配 helper 和 runtime error 映射，但不能拥有业务 method。
- Architecture tests 应逐步增加检查，阻止业务层重新出现私有 control server。
