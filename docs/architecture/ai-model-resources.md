# AI 模型资源架构

本文定义 Kairos 当前采用的模型服务端点、可用模型、验证证据和 Launch 引用边界。术语和资源拆分来自
[Decision 0019](../decisions/0019-model-endpoints-and-available-models.md)。旧 Model Connection 布局仅在
迁移期间作为兼容输入，不是新功能的写入目标。

## 1. 所有权与边界

模型资源属于 Strategy Agent 主包。Agent Application 负责 Endpoint 与 Model 的配置、查询、测试和验证
状态；Workbench 和显式 CLI 只适配输入并展示 Application 结果。Workspace/System 拥有路径、配置事务、
引用查询和删除协调，不拥有模型语义。

Credential 由 Workspace Credential Application 管理。Endpoint 引用 Credential ID，但不读取、复制或
返回 Secret。Composition 在构造 Agent runtime 时解析 Model、Endpoint 和 Credential，并选择现有的具体
runtime 实现；Domain、其他业务模块和 Launch 配置不得导入 Provider SDK 或模型服务响应。

## 2. 统一术语

| 用户术语 | 代码术语 | 身份 | 负责内容 |
| --- | --- | --- | --- |
| 模型服务端点 | Model Endpoint | `endpoint_id` | Provider、API 模式、Base URL、Credential、timeout、enabled |
| 可用模型 | Available Model | `model_id` | Endpoint 引用、Provider 原生模型 ID、enabled |
| 模型验证 | Model Verification | `model_id` + 资源 hash | 最近测试、结果、能力和失效原因 |
| 模型引用 | Model Ref | `model_id` | Launch 对一个已验证可用模型的稳定引用 |

`Provider` 是 Endpoint 的一个属性，不是另一层可变资源。`Provider model ID` 是外部服务词汇，例如
`gpt-5.6-sol` 或 `company/model-a`；它不等于 Kairos 的路径安全 `model_id`。

Workbench 面向用户将 Model Endpoint 展示为“供应商账号”。这里的“账号”表示一套可复用的模型服务访问
配置，也可以是 Ollama 等无需认证的本地服务；代码、配置文件和架构边界仍使用 Model Endpoint。

## 3. 资源关系

```text
Launch agent.model.ref
  -> Available Model
       -> Model Endpoint
            -> Credential
```

一个 Endpoint 可以被多个 Model 引用；一个 Model 只引用一个 Endpoint。Launch 只引用 Model，不重复保存
Endpoint、Provider 原生模型 ID 或 Credential。不存在 Model 绕过 Endpoint 直接引用 Credential 的路径。

## 4. Workspace 布局

```text
.kairos/
  config/
    ai/
      endpoints/
        <endpoint-id>.toml
      models/
        <model-id>.toml
    credentials/
      <credential-id>.toml
  state/
    configuration/
      ai-models/
        <model-id>.json
```

目录按需创建。配置是用户可理解、可迁移的输入；验证 JSON 是可重建的状态证据，不得混入配置目录。

### 4.1 Endpoint 文档

```toml
[endpoint]
version = 1
id = "ikun"
provider = "custom"
api_mode = "openai-responses"
base_url = "https://models.example.com/v1"
credential_id = "ikun-auth"
timeout_seconds = 60.0
enabled = true
```

Endpoint 的配置 hash 包含影响调用的字段以及 Credential 资源 hash。模型目录缓存不是调用配置，刷新目录
不得改变 hash 或使模型验证失效。

### 4.2 Model 文档

```toml
[model]
version = 1
id = "primary-reasoning"
endpoint = "ikun"
provider_model = "gpt-5.6-sol"
enabled = true
```

`model_id` 是 Kairos 身份，应表达用户用途并满足路径安全规则。`provider_model` 保留 Provider 的精确值，
不能为适配路径而改写。Model 配置不得复制 Endpoint 地址、协议或 Credential。

### 4.3 Launch 文档

```toml
[agent.model]
ref = "primary-reasoning"
request_timeout_seconds = 5
max_turns = 6
max_tool_calls = 8
max_input_tokens = 32000
max_output_tokens = 2000
```

请求预算属于 Launch 对这次 Agent 运行的约束，不属于 Available Model。模型资源只证明调用边界和具体
Provider 模型当前可用。

## 5. 添加和测试流程

### 5.1 AI 模型入口

Workbench 的 AI 模型入口只显示两个并列资源：

```text
1. 模型列表
2. 供应商账号列表
```

空列表不在操作区上方重复渲染空内容面板。

### 5.2 添加供应商账号

```text
选择 Provider
  -> 输入 Endpoint 名称
  -> 选择 API 模式（自定义服务需要）
  -> 输入或接受 Base URL
  -> 隐藏输入 Credential
  -> 脱敏确认
  -> 原子保存 Credential + Endpoint
```

保存完成代表 Endpoint 配置完整，不代表任意模型已验证。对 `/models` 的目录发现只能在保存后运行，403、
404 或协议不支持应显示诊断并允许继续手动添加模型。

### 5.3 添加 Available Model

```text
选择已有供应商账号，或进入供应商账号向导就地添加
  -> 新账号保存后返回当前模型向导并自动选中
  -> 输入 Kairos 模型名称
  -> 从目录选择或手输 Provider 模型 ID
  -> 脱敏确认
  -> 保存为 pending
  -> 可选的一轮对话测试
  -> verified / failed
```

一轮对话测试复用 Endpoint 的协议、地址、Credential 和 timeout，展示用户消息与解析后的文本回复。测试
活动可以进入脱敏 Workbench transcript，但持久验证证据只记录结果元数据，不保存消息、回复或原始响应。

选择一个已保存模型后进入模型上下文，只显示“对话、删除、修改”。进入对话后隐藏操作区，底部输入直接
发送消息，用户消息和模型回复追加到内容区；`/back` 返回模型上下文。

## 6. 验证状态

Available Model 使用以下状态：

- `pending`：尚未完成真实文本调用；
- `verified`：当前资源 hash 下文本调用成功；
- `failed`：当前资源 hash 下调用失败；
- `retest_required`：先前成功，但 Model、Endpoint 或 Credential 已变化；
- `disabled`：Model 或其 Endpoint 被停用。

验证成功至少证明 Endpoint 可达、认证可用、Provider 接受模型 ID、响应协议可解析且包含非空文本。它不
证明图片输入、工具调用、结构化输出、流式响应、吞吐或生产配额，未测试能力必须继续显式列出。

Readiness 只返回当前为 `verified` 的 Model。Endpoint 自身不进入 Launch 模型候选，因为 Endpoint 可达不
能证明具体 Provider 模型可用。

## 7. 引用与生命周期

引用方向固定为：

```text
Credential <- Endpoint <- Available Model <- Launch
```

删除 Application 必须先查询反向引用。普通删除在存在引用时失败并列出引用位置；强制删除由 Workspace
资源生命周期统一协调。Endpoint 修改会使其下全部 Model 需要重测，Model 修改只影响自身。目录发现结果
变化不得自动删除、停用或改写已经配置的 Model。

## 8. 迁移边界

旧布局：

```text
config/model-connections/<connection-id>.toml
```

迁移按一个旧 Connection 生成一个 Endpoint，并按 `models` 数组生成零到多个 Available Model。旧验证
记录按 Provider 模型 ID 分配给对应 Model；无法确定唯一身份时保留为 `pending`，不得猜测验证成功。

旧 Launch 的 `agent.model.connection` 与 `agent.model.model` 在迁移窗口内继续通过只读兼容路径解析。
所有新建和编辑只写 `agent.model.ref`。迁移器必须幂等、保留 Credential 引用，并留下可审计的 Workspace
migration record；旧文件的删除不属于自动迁移步骤。
