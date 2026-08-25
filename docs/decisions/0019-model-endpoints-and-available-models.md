# Decision 0019：模型服务端点与可用模型

- Status: Accepted
- Date: 2026-08-25
- Scope: Agent 模型资源、Workspace 配置、Workbench 与 Launch
- Supersedes: [Decision 0016](0016-model-connections-and-model-references.md)

## Context

Decision 0016 使用 Model Connection 同时表达服务访问边界和该服务提供的模型集合，并用
`connection_id + model_id` 表达 Launch 选择。实践中这两个对象具有不同生命周期：Endpoint、协议或
凭据变化影响其下所有模型，而新增、停用或验证一个模型不应改写 Endpoint 配置。

`Connection` 没有明确指出它连接的是模型推理服务，`Binding` 又只描述实现关系，没有表达它是用户可
选择和验证的模型资源。模型目录接口还可能因 Provider 权限返回 403，而具体推理接口仍可用；目录发现
不应成为 Endpoint 配置落盘的前置条件。

## Decision

### 1. 使用 Endpoint 与 Model 两个用户对象

- **模型服务端点（Model Endpoint）**描述如何访问一个推理服务，拥有 Provider、API 模式、Base URL、
  Credential 引用、timeout 和 enabled 状态。
- **可用模型（Available Model）**是 Kairos 中可被测试和 Launch 选择的模型，拥有稳定的 Kairos
  `model_id`、Endpoint 引用和 Provider 原生模型 ID。

产品文案和新代码不再把二者称为 Model Connection 或 Model Binding。实现类型应使用能保留业务含义的
名称，例如 `ModelEndpoint`、`AvailableModel`、`ModelEndpointApplication` 和
`AvailableModelApplication`。

### 2. Workspace 配置按 AI 资源分组

新配置写入：

```text
config/ai/endpoints/<endpoint-id>.toml
config/ai/models/<model-id>.toml
```

Credential 继续由 `config/credentials/` 拥有。Endpoint 只引用 Credential ID，任何 AI 配置都不得内联
Secret。Provider 原生模型 ID 可能包含 `/`、`:` 或其他不适合作为路径的字符，因此模型文件名使用独立、
路径安全的 Kairos `model_id`。

### 3. Endpoint 先保存，模型目录发现是可选能力

Workbench 添加 Endpoint 时收集 Provider、API 模式、地址和凭据，显示脱敏确认后原子提交 Endpoint 与
新 Credential。目录发现属于已保存 Endpoint 的辅助动作；失败只产生诊断，不撤销或阻止 Endpoint
保存。

添加可用模型时，用户选择 Endpoint，填写稳定模型名称和 Provider 原生模型 ID，然后保存为待验证。
真实的一轮对话测试更新该模型的验证状态。目录发现可以预填候选模型，但用户始终可以手动输入 Provider
原生模型 ID。

### 4. 验证和 Launch 引用属于可用模型

模型验证证据以 Kairos `model_id` 为身份，并绑定以下事实的组合 hash：

- 模型自身配置；
- Endpoint 的调用配置；
- Credential 资源 hash。

任一事实变化都会使验证进入 `retest_required`。模型测试不得保存对话正文、Provider 原始响应或 Secret。
只有 enabled、配置完整且验证当前有效的可用模型才能进入 Launch 候选项。

Launch 新配置只保存模型引用：

```toml
[agent.model]
ref = "primary-reasoning"
```

运行时先解析可用模型，再解析 Endpoint 和 Credential。Launch 不直接拥有 Endpoint、Provider 模型 ID 或
Credential。

### 5. 依赖关系决定删除顺序

- Credential 被 Endpoint 引用时不得直接删除。
- Endpoint 被可用模型引用时不得直接删除。
- 可用模型被 Launch 引用时不得直接删除。

强制删除仍必须通过 Workspace 资源生命周期 Application，报告完整影响并移除失效验证证据；Workbench
不得绕过引用检查直接删除文件。

### 6. 旧配置只读兼容并迁移

迁移器读取 `config/model-connections/*.toml`，将每个旧 Connection 转成一个 Endpoint，并将其 `models`
数组拆成独立可用模型。旧 `agent.model.connection + agent.model.model` 在迁移窗口内继续可读；所有新写入
使用 `agent.model.ref`。迁移完成并有测试覆盖后删除兼容读取，不能长期维护两套可写形态。

## Consequences

- Endpoint、Credential 和 Model 的生命周期及删除影响变得明确。
- 一个 Endpoint 可提供多个独立验证、启停和引用的模型，无需复制认证配置。
- Provider 禁止列出模型时，用户仍能保存 Endpoint 并手动添加、测试模型。
- Launch 引用稳定的 Kairos 模型身份，不再解析复合字符串或直接耦合 Endpoint。
- 实施需要迁移 Workspace 路径、Application API、Workbench 流程、Launch schema、readiness、引用查询和
  现有验证证据。
