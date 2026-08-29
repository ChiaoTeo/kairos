# Decision 0043：按用户任务与运行作用域组织 Workbench 导航

- Status: Accepted
- Date: 2026-08-29
- Refines: [Decision 0014](0014-unified-textual-workbench.md)、[Decision 0017](0017-run-plans-instances-and-operations-center.md)
- Scope: Kairos Workbench 首页、导航语义、Market、Account、Resources 与 Operations 入口

## Context

Workbench 原首页同时使用业务任务、业务模块和系统能力分组。“市场行情”既承载标的与行情任务，也直接
暴露项目共享 Market 的 route、全部订阅、启停、日志和回放控制；账户运行查询藏在“运行前检查”的账户
配置详情中；Reference 又以独立首页入口重复用户寻找标的的任务。相同的 Market 词汇还可能表示
standalone 查询、项目共享服务或某次运行实例组件，用户难以判断动作影响范围。

导航规则虽已部分集中，但首页目录、父子关系、面包屑和入口转换分散在多个 Screen 文件中。继续增加条件
分支会让产品导航与业务 flow 互相侵入，也难以证明旧路径已经删除。

## Decision

### 1. 首页按用户任务固定为七个入口

首页使用“市场与标的、策略与运行、账户与交易、连接与配置、数据与回测、运行中心、项目管理”。
Reference 目录并入“市场与标的”；它仍由 Reference owner 提供事实和查询，不因导航合并改变业务所有权。

账户余额、持仓、订单、费率和资金操作进入“账户与交易”。交易账户凭据、访问绑定、验证、修改和停用
进入“连接与配置”。两个入口复用 Account owner 的现有 Application，不复制账户事实或配置规则。

### 2. Market 动作按作用域分离

普通“我的实时行情”只展示当前 Workbench 会话拥有的关注、添加与退出操作，以及用户可理解的来源和
新鲜度。项目共享 Market 的实际 route、全部运行订阅、生命周期、日志和回放时钟只在“运行中心”的同一
服务详情出现。具体运行实例的 Market 状态和控制只在该实例组件中出现。

实时行情服务不可用时，Market 任务可调用 System owner 的既有服务操作进行显式 Workspace 范围确认；
查看详情进入运行中心的同一服务对象，成功修复后恢复原 Market 任务。不得自动切换到 standalone 或其他
实例作用域，也不增加通用 Recovery Manager。

### 3. 导航语义形成 Workbench 专有模块

`screens/navigation/` 统一拥有稳定首页目录、上下文父子关系、返回路径、面包屑和当前动作投影。
Textual Screen 只提交入口、应用 effects 和展示当前导航结果。各产品 flow 继续拥有动作定义、可用性、
参数、状态转换和业务调用；导航模块可以组合这些 presentation facts，但不是通用 router、业务 registry、
新的 Application facade 或可变业务状态 owner。

### 4. 删除重复旧路径

Resources 不再提供账户运行菜单，普通 Market 不再提供项目共享服务控制菜单，Reference 不再占用独立
首页编号。跨任务入口进入权威对象页面，不建立兼容页面或第二套详情。

### 5. 按实际访问路径返回，并在任务内修复前置条件

Workbench 在单一 Textual Screen 内维护逻辑页面栈。进入页面时压入上下文，`/back` 弹出实际访问帧；
同一详情从“策略与运行”或“运行中心”进入时，因此返回各自的真实来源，而不是由固定父级推断。首页和
切换项目清空旧栈。页面帧不复制业务记录、Provider 响应或凭据。

用户搜索市场但目录缺失时，Market 消费方流程可以保存原查询，并通过 Reference owner contract 规划
交易所与品种的目录准备方案。需要账号时进入现有市场数据配置向导，完成后返回最近的准备检查点；确认
准备后使用 Reference 返回的来源身份启动刷新，目录可用后重新执行原搜索。推荐来源、实际同步范围、
账号要求、可用性和进度由 Reference owner 返回，Workbench 不维护第二份 Provider 能力矩阵。

运行方案校验失败时，Strategy flow 保存类型化的 readiness 投影，并按诊断 owner 进入 Resources 的账户、
行情、模型或通知权威配置页；返回后回到原 readiness 检查点。方案内风险、执行和作用域问题复用现有
运行方案编辑向导。Workbench 不复制资源表单，也不引入通用 Repair Manager。

### 6. 产品切片和控制流身份必须显式

`flows/account` 与 `flows/resources` 是独立垂直切片：前者回答账户运行与交易问题，后者回答连接配置与
验证问题。它们分别拥有 `AccountSession` / `Feature.ACCOUNT` 和 `ResourcesSession` /
`Feature.RESOURCES`。其他产品 flow 同样直接拥有自己的动作、参数续接和状态转换；跨任务入口通过逻辑
页面栈进入对方权威页面，不增加协调器或兼容 facade。

控制流不得由各 flow 重复拼写字符串协议。顶层任务使用 `Section`，稳定页面使用 `Routes`，共享输入使用
`Feature`，首页和一级任务动作使用导航目录内按任务划分的 `StrEnum`，叶子稳定动作使用 owner flow 内的
`StrEnum`。动态对象身份、外部 Provider/进程词汇和用户文案仍保留在各自边界，不建立跨产品的万能动作
枚举或通用业务 registry。

普通路径只展示当前上下文可执行的连续编号动作，并支持方向键、Enter、Esc 与 `?`。高级命令必须显式
进入；未匹配的普通文本不得静默回退为 CLI 命令。

## Consequences

- 用户先选择要完成的任务，再在对象摘要、面包屑和确认中辨认作用域。
- Market、Reference、Account、Integration 和 System 的业务所有权及 Application/Contract 边界不变。
- 首页编号发生一次有意的不兼容调整；自动化和快照必须使用新目录验证，不保留旧编号别名。
- `AccountSession` 独立保存账户任务的临时选择，`ResourcesSession` 只保存连接配置交互状态。
- 账户运行代码位于独立 `flows/account`，其输入 token 不再由 Resources 猜测和转发。
- 原始导航元组只允许在 `navigation.identity` 定义；产品 flow 使用 canonical route symbols。
- 一级任务目录不得使用裸动作字符串；架构测试保护 route 和 task action 的唯一符号来源。
- 导航包会依赖产品 flow 提供的 presentation action definitions，但不得执行它们的业务行为。
- 前置条件修复会增加跨产品页面帧，但不会改变 Credential、Integration、Reference 或 Market 的所有权。
- Backtest 报告归属于具体运行实例；Research 数据执行与 Gate 发布保持各自 owner 的连续参数流程。

## Verification

- 导航行为测试覆盖七个首页入口、语义返回路径、统一面包屑和旧 Resources 账户路径删除；
- Market 测试覆盖会话关注、项目共享服务不可用恢复、运行中心权威详情和作用域确认；
- Account/Resources 测试分别覆盖运行查询与连接配置，并证明各自使用独立临时选择；
- 快照覆盖首页、实时行情和关键窄终端状态；
- credential-free fixture 的真实 PTY 验证首页、跨入口、返回和单输入交互。
