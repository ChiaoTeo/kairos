# Decision 0016：模型连接与模型引用

- Status: Superseded by [Decision 0019](0019-model-endpoints-and-available-models.md)
- Date: 2026-08-25
- Scope: Agent 模型配置、验证证据、Workbench 运行准备与 Launch
- Extends: [Decision 0014](0014-unified-textual-workbench.md)

## Context

原模型向导把 Provider 当成一项自由文本配置，并分别收集连接与模型字段。Endpoint 没有稳定的显式
步骤；验证证据也只保存连接最后测试的一个模型。同一 Endpoint 和 Credential 通常可以访问多个模型，
因此“每个模型一份连接”会重复认证与路由配置，而“连接级已验证”又无法证明 Launch 选择的具体模型
确实可调用。

## Decision

### 1. 一条 Model Connection 表示一个服务访问边界

Model Connection 拥有 `connection_id`、Provider、接口协议、解析后的 Endpoint、Credential 引用、
timeout 和 enabled 状态。一条连接可以发现并包含多个模型。只有 Endpoint、认证、Provider、计费或
权限边界不同时才创建另一条连接；模型 ID 不同本身不产生新连接。

Credential 继续只保存 Secret 与认证身份，不拥有 Endpoint、协议或模型选择。Provider catalog 是已知
Provider 的默认协议、Endpoint 和认证要求的唯一来源。

### 2. Endpoint 是显式输入，留白表示使用 Provider 默认值

所有模型连接都经过 Endpoint 步骤。OpenAI、Anthropic、OpenRouter、Ollama 和 LM Studio 留白时使用
Provider catalog 的官方或本地默认地址；输入完整 HTTP(S) 地址时使用用户配置。自定义 Provider 没有
默认 Endpoint，因而必须输入地址并选择协议。确认摘要展示解析后的确切地址及其来源。

### 3. 运行时选择具体 ModelRef

具体模型用 `connection_id + model_id` 唯一标识，并在 UI 中显示为 `connection_id / model_id`。Launch
不再要求分别手写连接 ID 和模型 ID，而是从已验证 ModelRef 中选择。持久化继续使用现有
`agent.model.connection` 与 `agent.model.model` 字段，避免引入第二种配置形态。

### 4. 验证证据属于 ModelRef

一条连接下每个模型分别保存验证结果、测试时间、错误类别、能力和连接配置 hash。测试模型 B 不得覆盖
模型 A 的有效证据。模型目录刷新不改变调用边界，不使证据失效；Endpoint、协议、Credential 内容或
其他影响调用的连接事实变化时，相关证据进入 `retest_required`。

旧 version 2 单模型证据继续可读；下一次写入时升级为可容纳多个模型的 version 3 结构。

### 5. Workbench 使用草稿、发现、测试、原子提交

模型连接向导按以下顺序运行：

```text
选择 Provider -> 连接名称 -> Endpoint -> 条件化 Secret
-> 内存草稿 -> 发现模型 -> 选择或手输模型 -> 最小文本调用
-> 脱敏确认 -> 原子提交 Credential、Connection 与验证证据
```

失败时不写入半成品；取消、返回首页或会话清理时丢弃草稿 Secret。Launch 只列出 enabled、配置完整、
连接 hash 当前且具体模型验证成功的 ModelRef。没有候选项时可以保存未完成 Launch 草稿，但发布
readiness 必须失败关闭。

## Consequences

- 用户按账户/Endpoint 维度管理连接，避免为每个模型重复保存 API Key。
- 一个连接可以同时提供多个已验证模型，Launch 的实际选择保持明确和可审计。
- Endpoint 默认值可用但不再隐藏；代理、企业网关和本地远程实例拥有清晰覆盖路径。
- 模型发现是目录信息，不再意外使其他模型的验证失效。
- Workbench 需要保留模型发现、测试失败后的可恢复状态，并持续保证 Secret 不进入 Activity、错误或
  transcript。
