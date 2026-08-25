# Decision 0015：Provider 连接、凭据与账户访问

- Status: Accepted
- Date: 2026-08-25
- Scope: Workspace 运行资源、Integration 连接、Account/Execution 访问与 Workbench 运行准备
- Extends: [Decision 0014](0014-unified-textual-workbench.md)

## Context

Kairos 当前需要同时覆盖四类配置：需要鉴权的交易所行情、只有 API Key 与 Endpoint 的
Massive 数据服务、交易所只读账户，以及允许下单的交易账户。它们都可能使用 API Key，
但 API Key 只是认证材料，不能决定资源属于 Market、Account 还是 Execution。

现有实现已经分别具有 Workspace Credential、Massive 配置、Account credential binding 和
Integration provider capability，但交互和配置仍有重叠：Massive 配置由 Reference 顺带改写
Market provider；市场数据入口没有统一的 Provider Connection；Account 的 `readonly` 与
`trade` 容易被呈现成两种账户；一个 `verification_status` 也无法区分行情、账户读取与订单交易
是否分别可用。Launch 因而只能按资源种类粗略检查就绪状态，不能按实际需要的能力判断。

## Decision

### 1. 资源按业务事实分类，不按是否需要 API Key 分类

Kairos 使用以下四个不同概念：

- **Credential** 是 Workspace-local Secret 容器，保存 API Key、Secret、Passphrase 等认证材料。
  它不表示数据源、账户或交易能力。
- **Provider Connection** 描述 Integration 如何连接一个 Provider，包括 Provider、环境、Endpoint、
  Credential 引用和可测试的连接能力。它不拥有余额、持仓、行情或订单等业务状态。
- **Account** 表示一个远端经济账户身份及其 segment。Account 继续唯一拥有余额、持仓、权益、
  freshness、intent 和账户侧订单事实。
- **Binding** 表示一个业务能力被明确允许使用哪个 Connection 或 Credential。Market/Reference
  数据源、Account 读取和 Execution 交易分别拥有自己的用途绑定。

分类依据是返回或修改的业务事实：报价、K 线和 Order Book 属于 Market；标的目录属于
Reference；余额、持仓和账户订单属于 Account；提交、修改和撤销订单属于 Execution。
交易所要求鉴权不会把行情连接变成 Account。Massive 不提供经济账户事实，因此始终是数据
Provider Connection，不是 Account。

### 2. Provider Connection 是 Integration 边界，业务模块只保存用途绑定

Integration 继续拥有 Provider 认证、连接生命周期和 normalized external facts；Workspace/System
拥有配置文件、Secret 文件和实例资源的持久化。Provider Connection 使用 Integration 已有的具体
connection capability，不新增 UI-owned facade、通用 manager 或重复 protocol。

Reference 和 Market 分别保存自身需要的数据源绑定，并通过稳定 `connection_id` 取得 concrete
Integration connection。它们不复制 Endpoint 或 Secret，也不由其中一个业务模块替另一个模块
选择 Provider。Massive 先迁移为这一模型；需要鉴权的 Binance、OKX 行情使用同一连接边界。

### 3. 同一远端账户只创建一个 Account

只读和交易不是两种 Account。一个 Account 可以具有多个用途明确的 credential binding：

- `account-read`：身份、余额、持仓、账户事件和只读历史；
- `order-trade`：订单查询、提交、修改和撤销；
- 高风险资金移动若未来实现，必须使用独立用途，不能由 `order-trade` 隐式获得。

给只读账户启用交易是在同一个 Account 上增加 `order-trade` binding，并验证远端身份一致；不得
创建第二个 `*-trade` Account。Account live facts 仍只有 Account-owned Integration snapshot/event
capability 这一条 authoritative ingress，Execution 不向 Account 推送重复观察事实。

### 4. 系统授权与 Provider 实际权限分开记录

Binding purpose 表示 Kairos 允许该 Credential 做什么；测试得到的 observed capabilities 表示
Provider 实际授予了什么。二者不能由一个自由字符串 `role` 代替。

- Execution 只能选择 `order-trade` binding；`account-read` 或 `market-data` binding 即使引用了
  Provider 上权限过高的 Key，也不得用于订单命令。
- Provider 权限高于 binding purpose 时，验证结果给出最小权限警告，但不扩大 Kairos 授权。
- Provider 权限低于 purpose 时，验证失败并阻止依赖该能力的 live Launch。
- Credential 可以被多个显式 binding 引用，但必须匹配 Provider；复用必须由用户选择并显示引用，
  不能通过名称或文件存在自动推断。

### 5. 配置采用草稿、验证、确认、提交生命周期

新增或修改 Provider Connection、Account read binding 和 order-trade binding 时统一采用：

```text
选择用途 -> 收集普通字段 -> 选择或创建 Credential -> 低成本真实验证
         -> 脱敏确认 -> 原子提交 -> 能力化结果
```

Secret 只存在于隐藏输入和内存草稿中，不进入命令历史、Activity、日志、快照、剪贴板或 transcript。
失败或取消必须清理草稿 Secret。允许保存未验证资源时必须由所属 Application 显式支持，并显示
“不可用于 live”，不得由 Workbench 自行绕过验证。

### 6. Workbench 保留六个首页入口，在“运行前检查”内按任务呈现

不增加“API Key”首页入口或第二个 App。运行前检查继续包含交易账户、市场数据、AI 模型、通知和
就绪检查，但资源列表无论是否为空都必须显示稳定的“添加”动作。

“市场数据”向导依次选择 Provider、产品/能力、Endpoint 和鉴权方式。鉴权方式只能是无需凭据、
选择已有 Credential 或安全创建新 Credential。普通详情显示 Reference、行情查询、行情订阅及产品
entitlement 的逐能力状态；Credential hash、完整引用和 schema version 留在高级信息。

“交易账户”向导区分模拟账户与交易所账户，交易所账户先建立 `account-read`；用户可以在同一账户的
“管理账户访问”中增加、替换或停用 `order-trade` binding。启用交易属于安全敏感变更，必须显示账户、
Provider、环境、segment、Credential identity、权限和后果并明确确认。

Credential 不是普通用户首先管理的业务对象。选择已有 Credential、创建新 Credential 和查看引用
嵌入上述向导；独立删除、轮换、技术字段和引用图只出现在“安全与高级信息”。删除被引用 Credential
必须失败关闭或先显式解除引用。

Workbench 继续使用一个输入框和现有 InteractionState：对象与用途选择使用 `ChoiceInteraction`，
普通字段和 Secret 使用 `InputInteraction`，验证使用 `RunningInteraction`，live/交易授权/删除使用
`ConfirmInteraction`。导航和逐字段输入不产生 Activity；一次验证或提交最多产生一个终态 Activity。

### 7. 验证和 Launch readiness 按能力判断

资源不再只暴露一个笼统的“已验证”。至少分别投影：

- Reference catalog read；
- Market query；
- Market stream；
- Account read；
- Order query；
- Order trade。

通用运行检查显示 Workspace 当前具备的能力。Launch readiness 根据 mode、所选 Market route、Account
scope 和 `trade` 意图检查实际需求：只读 live Launch 不因缺少 `order-trade` 被阻止；需要交易的 live
Launch 在交易 binding 缺失、失效、身份不一致或未验证时必须阻止启动，并给出指向具体资源动作的
下一步。Launch 向导选择业务对象和“只读观测/允许交易”，不要求用户手写账户 ID 列表或 scope JSON。

## Consequences

- 同一个 Secret 可以被安全复用，但 Credential、Connection、Account 和业务用途不再互相冒充。
- Market/Reference、Account 和 Execution 保持各自业务所有权，Integration 只提供连接与 normalized facts。
- Massive、Binance 鉴权行情、只读账户和交易账户可以进入同一运行准备体验，同时保留不同验证语义。
- Account 与 Provider Connection 的列表、详情、向导和 readiness 需要迁移到能力化状态视图。
- 旧 `credential_role`、内嵌 Endpoint/Credential 的 Market provider 和 Massive 特例只能作为迁移输入；
  新写入路径不得继续扩大这些兼容形态，迁移完成后删除重复 facade。
- 所有 live 访问、Secret、交易授权、删除和引用变更继续遵守 Decision 0014 的单输入、安全确认、脱敏
  transcript 和一个操作一个终态规则。

## Completion criteria

本决策完成需要同时满足：

1. Workspace 能配置、列出、验证、编辑、停用和安全删除 Provider Connection，并由 Massive、至少一个
   Binance 鉴权行情产品和至少一个 OKX 行情产品使用。
2. Reference/Market 通过 `connection_id` 使用连接，普通业务配置不再复制 Secret；Massive 配置不再由
   Reference 私自改写 Market provider。
3. Account 支持同一远端身份上的 `account-read` 与 `order-trade` binding，能够从只读升级交易而不创建
   第二个 Account，并对身份、用途和 observed permission 失败关闭。
4. Workbench 实现本文规定的列表、向导、详情、“管理账户访问”、Credential 选择/创建及恢复路径；
   60×20 和 80×24 下保持唯一输入框和主要动作可用。
5. 运行检查和 Launch 向导按具体能力选择与阻断，live 确认明确显示 Market、Account、trade scope、风险
   和脱敏 Credential identity。
6. 行为、边界、架构、Secret、transcript、snapshot、binary 和真实 PTY 测试覆盖成功、失败、取消、
   重测、配置漂移、权限过高、权限不足、引用删除和从只读升级交易。
7. 相关旧配置有确定性迁移或兼容读取路径；重复 facade 删除，Workspace dependency、crate layout、
   documentation、Rust/Python 测试和格式检查全部通过，或准确记录与本改造无关的既有失败。
