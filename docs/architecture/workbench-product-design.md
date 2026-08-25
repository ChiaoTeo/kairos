# Kairos Workbench 产品设计

本文定义 Kairos 人工交互工作台的当前产品边界、信息架构、交互模型和验收标准。它是
[统一 Textual 工作台决策](../decisions/0014-unified-textual-workbench.md)的产品层展开；业务所有权、
Application/Contract 边界和显式 CLI 约束仍以项目架构规则及
[CLI boundary](cli-boundary.md)为准。

## 1. 产品定位

Kairos Workbench 是面向人工操作的统一终端工作台。它帮助用户在不知道完整 CLI 命令和内部模块
结构的前提下，完成行情、标的、策略、运行资源、研究数据和系统维护任务。

Workbench 是现有业务 Application 与 Contract 的输入适配和结果展示层，不是新的业务 API、业务
状态所有者或命令执行器。它只有一个 `KairosWorkbenchApp`、一个主工作屏和一个持续可见的输入框。

### 1.1 产品目标

- 以用户任务而不是内部模块或命令清单组织入口。
- 让新用户通过选择和上下文提示完成操作，让熟练用户继续直接输入命令。
- 在同一视觉和输入模型中完成导航、参数收集、确认、执行、结果查看和错误恢复。
- 让每次真正执行的操作都可理解、可审计，并在可能时可复制为等价 CLI。
- 对 live、外部写入和敏感凭据提供一致的安全边界。
- 保留适合 Agent 接手的脱敏 transcript 和页面复制能力。

### 1.2 非目标

- 不替代用于脚本、CI 和机器消费的显式 CLI 与结构化输出。
- 不复制 Application、Contract、Domain 或服务层的业务规则。
- 不解析 CLI stdout 作为业务结果，也不调用 Typer executor 执行 Workbench 操作。
- 不提供第二个 App、常驻 shell、按钮菜单、聊天记录或 UI 专属业务 facade。
- 不在交互层根据本地文件、Provider 可用性或错误结果偷偷切换业务作用域。

## 2. 用户与核心任务

Workbench 同时服务三类使用者：

- 新用户：需要发现能力、完成运行准备并获得下一步引导。
- 日常操作者：需要快速查看行情、控制 Launch、管理账户和诊断服务。
- 研究与开发人员：需要准备数据、运行研究流程、检查系统状态并把证据交给 Agent。

首页固定为六个稳定任务入口，不随内部命令或模块数量增长：

| 编号 | 产品入口 | 用户要完成的工作 | 主要业务所有者 |
| --- | --- | --- | --- |
| 1 | 查看市场行情 | 搜索报价、下载历史行情、浏览数据集、回放与连接运行服务 | Market、Reference、Integration |
| 2 | 查找市场标的 | 查找资产、交易所、合约、Market 与期权链 | Reference |
| 3 | 配置并运行策略 | 配置 Launch、启动实例、查看组件、跟随输出和控制执行 | Workspace/System、Execution、Market |
| 4 | 完成运行准备 | 配置账户、市场数据、模型、通知并检查连接 | Account、Integration、Workspace/System |
| 5 | 准备数据研究 | 管理 Dataset、Research Plan 和 Gate | Data/Research 所属 Application |
| 6 | 维护系统 | 管理项目、服务、配置、Profile、迁移和业务诊断工具 | Workspace/System 及对应业务所有者 |

首页只负责分组和导航，不拥有这些业务行为。

## 3. 信息架构

### 3.1 上下文模型

用户上下文由面包屑和显式选择表达，不使用 shell path。上下文可包含：

- 当前 Workspace；
- 当前产品入口；
- 当前 Launch 与 Instance；
- 当前账户；
- 当前 Market 或 Reference 记录；
- 当前数据源、服务或研究对象；
- 当前正在收集参数的叶子操作。

Workbench 只保存完成交互所需的临时选择。余额、订单、行情、Launch 生命周期等业务事实继续由所属
Application、Actor 或 Contract 拥有；界面不得成为第二个可变业务状态所有者。

### 3.2 导航层级

导航遵循以下层级：

```text
首页
  -> 产品入口
    -> 对象列表或能力分组
      -> 已选对象
        -> 叶子操作
          -> 参数 / 确认 / 执行 / 结果
```

并非每个流程都需要所有层级。对象唯一且业务规则允许时可以直接进入详情；存在多个候选或选择会改变
业务含义时，必须先让用户明确选择。

### 3.3 Standalone 与 connected 作用域

产品必须显式区分直接访问 Provider 的 standalone 操作和连接运行组件的 connected 操作。例如行情：

- standalone：报价、历史下载、本地数据集和独立回放；
- Workspace connected：连接 Workspace Market 服务；
- Launch connected：连接具体 Launch Instance 的 Market 组件。

不同作用域不得因文件存在、服务失败或 Provider 不可用而自动互相回退。当前作用域必须通过面包屑、
对象摘要或确认信息对用户可见。

## 4. 主界面结构

Workbench 使用一个垂直布局，从上到下包含：

| 区域 | 职责 | 内容生命周期 |
| --- | --- | --- |
| Workspace 摘要 | 展示当前 Workspace 身份和必要的全局状态 | 会话持续可见 |
| Activity Stream | 保留当前会话已经到达终态的操作、结果和证据 | 追加式，可清屏和复制 |
| Interaction Region | 展示当前选择、参数、确认、运行态或持续控制 | 随交互状态原地替换 |
| 状态栏 | 展示等待、执行中、成功、失败或暂停等瞬时状态 | 单行覆盖更新 |
| 命令栏 | 展示面包屑、收集当前输入 | 始终只有一个输入框 |
| 固定提示栏 | 展示全局可用的返回、帮助和退出方式 | 稳定且简短 |

### 4.1 内容区是 Activity Stream

内容区不是 stdout、聊天记录或当前页面模型，而是当前会话已经完成的业务活动和证据。一个有限操作在
成功、失败或执行后取消时最多形成一个 `ActivityRecord`；导航、候选、参数校验、确认请求、Running
提示和自动刷新不进入 Activity Stream。空内容区只显示一个不参与复制和 transcript 的弱提示。

`ActivityRecord` 使用关闭的 `kind` 和 `outcome`，并显式保存脱敏后的 `copy_text`、审计摘要及可选
Artifact 路径。产品 flow 只能返回 `AppendActivity` effect；只有 `ActivityStream` renderer 可以调用
底层 `RichLog.write()`。`/clear` 只清当前可见 Activity，不删除 transcript、Artifact 或业务状态。

用户位于底部时新 Activity 自动跟随；用户上滚查看旧结果时保持视口，并由状态栏提示未读数量，执行
`/bottom` 或 `Ctrl+End` 后回到底部。

### 4.2 Interaction Region

Interaction Region 是当前 `InteractionState` 的被动投影，承载动作列表、参数说明、确认摘要、运行状态和
持续控制。它不保存另一份产品状态，也不直接启动 Worker。快照类 Control 原地替换最新值；日志类
Control 使用容量为 500 行的 `LiveBuffer`，显示 following、unseen、dropped 和完整日志路径。

Market 自动刷新不产生 Activity；用户明确选择“保存当前快照”时才追加一条 Activity。Launch attach 的
暂停/继续只改变跟随状态，清空窗口不删除日志源，`/copy` 复制当前窗口；完整日志仍由 Launch 日志 owner
持有并在 Control 中显示路径。

### 4.3 动作列表

动作列表是当前上下文唯一的菜单呈现。每个动作包含：

- 稳定的动作 ID；
- 面向用户的名称；
- 一句结果导向的说明；
- 可选的数字或短字母快捷方式。

数字只在当前上下文中有意义，不进入业务 API、结果标题或长期审计记录。动作列表变化不应向内容区
追加同一菜单。

### 4.4 命令栏

命令栏始终包含当前上下文和一个输入框。placeholder 只说明此刻需要输入什么，不承载长篇帮助。
普通导航状态接受编号、动作名和 `/` 命令；参数状态只接受当前参数及允许的取消、帮助和退出命令。

## 5. 单输入交互模型

唯一输入框由五种互斥的 `InteractionState` 驱动：

| 模式 | 用途 | 输入行为 |
| --- | --- | --- |
| `ChoiceInteraction` | 选择动作或直接输入命令 | 解析当前动作、全局命令或显式 Kairos 命令 |
| `InputInteraction` | 收集普通或敏感字段 | 通过 `ActionToken(feature, action, field)` 回到所属产品 flow |
| `ConfirmInteraction` | 等待危险操作确认 | 保存同一个不可变 `OperationSpec`，不产生 Activity |
| `RunningInteraction` | 后台 worker 正在执行 | 阻止重复提交，允许取消当前操作 |
| `ControlInteraction` | 行情快照或持续日志 | 保持控制动作可用并原地更新视图 |

任何时刻只能有一个模式和一个输入所有者。参数向导、确认和 worker 不得各自创建第二个常驻输入框。

### 5.1 状态转换

```text
Choice
  -> Input          叶子操作缺少字段
  -> Confirm        参数齐全且需要确认
  -> Running        参数齐全且可直接执行
  -> Control        进入持续快照或日志

Input -> Input | Confirm | Running | Choice
Confirm -> Running | Choice
Running -> Choice | Control
Control -> Control | Choice
```

`GuidedSession` 是唯一交互状态所有者，并组合 Market、Reference、Operations、Research、Resources 和
Strategy 六个产品 Session。Widget 只投影 Session；Screen 不维护平行 prompt、pending operation 或结果
route 状态。

### 5.2 已实施的代码边界

```text
KairosWorkbenchApp
  -> CommandLineScreen                Textual 事件、Worker 入口、effect 应用
       -> GuidedSession               唯一交互状态所有者
       -> screens/flows/*             六类产品的 dispatch/success/failure/cancel
       -> OperationSpec/RunningTask   不可变操作意图与当前 Worker 绑定
       -> InteractionRegion           当前 InteractionState 的被动投影
       -> ActivityStream              终态 ActivityRecord 的追加投影
       -> LiveBuffer                  持续日志的有界可见窗口
  -> WorkbenchTranscript              脱敏、append-only 的会话审计
```

调用方向固定为 `Screen -> product flow -> Application/Contract`。产品 flow 不调用 `query_one`、
`run_worker`、`set_focus` 或 `push_screen`；Application/Contract 返回的对象由所属 flow 显式转换为
Interaction 或 Activity。新增产品动作通常只修改所属 flow、产品 Session 和测试，不要求在 Screen 的
dispatch、成功、错误和取消四处重复登记。

## 6. 导航与内容记录规则

### 6.1 纯导航不产生内容记录

当输入只改变导航上下文时，内容区完全不新增内容。系统只更新：

- 面包屑；
- 动作列表；
- 状态栏；
- 输入框 placeholder；
- transcript 中的语义化 `navigation` 事件。

这条规则适用于首页编号、子菜单选择、`/back` 和 `/home`。

### 6.2 参数不逐项回显

参数输入只进入当前向导状态。普通参数可进入输入历史和脱敏 transcript，但不以
`kairos › <原始值>` 的形式逐项写入内容区。Secret 参数既不进入输入历史，也不显示明文。

### 6.3 一个操作、一个意图、一个终态

参数齐全后，产品 flow 构造一个不可变 `OperationSpec`，其中包含 operation ID、稳定动作名、脱敏审计
摘要、可选等价 CLI、类型化 `ResultRoute` 和 callable。确认和 Worker 都复用同一个 spec；Screen 只以
`RunningTask(spec, worker)` 绑定运行实例，不从 Worker name 解析协议。

操作意图由 transcript 按 operation ID 去重记录。Activity Stream 不展示“开始执行”，只在 Worker 到达
成功、失败或执行后取消时使用同一个 operation ID 追加一个终态 Activity。确认前取消不产生 Activity。

### 6.4 特殊控制命令

| 命令 | 内容区行为 |
| --- | --- |
| `/back`、`/home` | 不回显，只改变上下文 |
| `/help` | 不回显命令，展示当前上下文帮助 |
| `/clear` | 只清空可见 Activity；状态栏反馈，业务状态和 transcript 不变 |
| `/copy` | 复制 Workspace、当前 Interaction 和 Activity，并统一脱敏 |
| `/copy-history` | 只复制 Activity Stream |
| `/bottom` | 跳到最新 Activity 并恢复自动跟随 |
| `/transcript` | 展示当前脱敏 transcript 路径 |
| `/exit` | 无任务时退出；有任务时进入明确的取消或退出确认 |

## 7. 参数、验证与向导

### 7.1 参数提示

短提示放在输入框 placeholder，当前阶段放在状态栏。只有在用户必须理解约束、风险或格式示例时，
内容区才显示一次参数说明面板。

参数错误必须在 `InputInteraction.error` 中就地恢复：

- 保留当前叶子操作和已经通过验证的非敏感参数；
- 在当前 Interaction 中显示具体错误；
- 在输入框继续请求出错字段；
- 提供 `/back` 取消当前步骤；
- 不把格式错误提交给 Application。

### 7.2 对象选择

Reference、Market、账户、Launch 和 Instance 等对象必须以业务名称和关键状态列出。内部 canonical ID
只在消歧、复制或“技术标识”动作中展示。选择结果后，Workbench 保存业务对象或稳定 ID，而不是列表
中的位置编号。

### 7.3 空值与默认值

允许直接回车时，placeholder 和说明必须明确默认行为。默认值由所属 Application、配置或显式产品规则
决定；界面不得根据猜测生成交易参数、Market ID、数据源或 live 作用域。

## 8. 执行、确认与安全

### 8.1 Application/Contract 调用

Workbench 直接调用业务所有者的 Application 或 Contract client，并把界面参数显式映射为业务请求。
它不得：

- 调用另一个业务主包的 Application；
- 通过顶层 CLI 或 stdout 间接访问业务行为；
- 在 UI 中复制领域校验；
- 把 Provider DTO、持久化记录或服务实例暴露给用户流程。

### 8.2 危险操作

live、外部写入、删除、停止、发布和其他不可轻易恢复的操作必须在执行前显示确认摘要。摘要至少包含：

- 操作名称；
- Workspace 和业务作用域；
- 目标对象；
- 关键非敏感参数；
- 模式（backtest、paper、live）；
- 主要后果。

确认模式只接受明确的确认、取消、帮助和退出。普通文本、编号或回车不得被解释为同意。

### 8.3 `--yes`、`--dry-run` 与 `--no-exec`

- `--yes` 只跳过允许自动确认的交互步骤，不绕过业务校验。
- `--dry-run` 展示计划、目标和等价操作，不产生外部副作用。
- `--no-exec` 禁止启动外部进程或执行写入，只允许界面和流程验证。
- 预览必须标明“未执行”，不得使用与成功结果相同的视觉语义。

### 8.4 Secret

Secret 输入必须满足：

- 输入框使用隐藏模式；
- 不进入命令历史；
- 不进入内容区、错误详情、快照和剪贴板；
- transcript 只保存 `<redacted>`；
- 向导完成、取消或失败后清理临时 Secret；
- 日志输出经过统一脱敏后才允许复制或持久化。

## 9. 结果、错误与恢复

### 9.1 结果呈现

结果按用户问题选择最小合适形式：

- 单一结论使用短文本或状态行；
- 多记录比较使用表格；
- 对象详情使用分组字段；
- 系统状态使用摘要加组件表；
- 配置、诊断和计划使用结构化面板；
- 持续输出使用可暂停和刷新的日志视图。

结果标题必须表达业务对象和作用域，不能只写“完成”或暴露内部 result kind。

### 9.2 错误信息

错误信息回答三个问题：

1. 哪个操作失败？
2. 用户可理解的原因是什么？
3. 现在可以执行什么恢复动作？

恢复建议必须与当前错误相关。固定的 `/back`、`/home`、`/help` 不应在每条结果后重复。

### 9.3 异步任务与结果路由

耗时操作通过单一 worker 执行：

- 开始时 `RunningInteraction` 和状态栏显示正在执行的完整操作；
- Busy 模式阻止重复提交；
- `Ctrl+C` 只取消当前可取消操作，不退出整个 Workbench；
- worker 成功、失败和取消都必须回到稳定的 Navigation 状态；
- 自动刷新只更新对应视图，不重复追加相同结果和操作记录；
- 用户在任务运行时提交新输入，才显示一次“任务仍在运行”的针对性反馈。

产品 vertical flow 同时拥有 dispatch、success、failure 和 cancel。它返回关闭的 presentation effects：
`AppendActivity`、`SetInteraction`、`RunOperation`、`SetStatus`，以及两个仅用于启动 Market/Launch 刷新的
Control adapter effect。Screen 负责应用 effect 和管理 Textual Worker，不推断产品结果含义。

## 10. 六大产品入口

### 10.1 市场行情

核心流程包括：

- 按代码或名称搜索有效标的并查看当前行情；
- 选择时间范围和目标位置下载历史行情；
- 浏览已准备的本地数据集；
- 将 JSONL 行情事件送入独立回放；
- 连接 Workspace 或 Launch Instance 的 Market 服务；
- 诊断 Market 定义、Reference 映射和 Provider route；
- 通过高级入口输入完整 Market 标识。

普通用户流程优先使用 Reference 搜索结果，完整 Market ID 只属于明确的高级入口。

### 10.2 市场标的

Reference 入口提供资产、交易所、合约、Market 和期权链。资产和合约按代码或名称搜索；数量较少的
交易所和 Provider 可以直接列出。详情页可以继续查看关联 listing、Market 和技术标识。

Reference generation、publication 和 Provider 同步状态属于系统诊断信息，不应混入普通标的详情。

### 10.3 策略运行

策略入口围绕 Launch 和运行 Instance 组织：

- 列出、创建和编辑 Launch；
- 选择 backtest、paper 或 live 模式；
- 启动、停止、等待和诊断实例；
- 查看组件与时间线；
- 跟随运行输出；
- 从具体 Instance 进入 Execution 或 Market 控制。

运行态 Execution 必须绑定具体 Launch Instance。Standalone 订单操作属于明确的账户或 Execution
作用域，不得因找不到实例而自动切换。

### 10.4 运行准备

运行准备按资源类型组织账户、市场数据、AI 模型和通知目标。配置向导必须区分普通字段与 Secret，
支持预览、验证、编辑和删除，并在结束时给出资源是否可用于 paper/live 的明确结论。

Account 拥有余额、仓位、权益和账户侧订单事实。Workbench 不允许 Execution 或其他模块向 Account
推送第二份 live 观察事实。

### 10.5 数据研究

数据研究区分数据准备和研究流程：

- Dataset 浏览、需求计划、执行和 Data Gate；
- Research Plan、锁定、执行证据和 Research Gate。

会改变数据或发布状态的步骤必须显示目标、计划 hash 或证据文件，并根据风险进入确认。

### 10.6 系统维护

系统维护提供项目工作区、实时观测、服务管理、诊断、stale 资源修复、配置、Profile、迁移和受控的
业务工具入口。系统组合可以连接业务 Contract 与进程资源，但不得绕过业务 Contract 调用其他业务
主包 Application。

## 11. Transcript 与 Agent 协作

终端画面不是审计事实来源。每个 Workbench 会话维护 append-only JSONL transcript，记录：

- 会话开始和结束；
- 语义化导航事件；
- 完整操作及其等价 CLI；
- 脱敏后的输入与输出；
- worker 生命周期；
- 确认、取消、复制和错误事件。

transcript 默认写入 Workspace 的 `logs/workbench/`；无法安全创建时可以退化为进程内记录。文件和
current pointer 使用仅当前用户可读写的权限。

“复制当前页”按 Workspace/上下文、当前 Interaction、可见 Activity 和相关 Artifact 路径组合；不复制
输入历史或 Secret。`/copy-history` 只复制 Activity。复制前统一脱敏，输出应可直接粘贴给 Agent，而不
要求 Agent 根据终端颜色或菜单编号重建上下文。Live Buffer 默认只复制当前窗口，完整范围通过 owner
提供的日志路径定位。

## 12. 键盘、终端与可访问性

- 所有核心流程必须只用键盘完成。
- Enter 提交当前输入；Esc 或 `/back` 取消当前参数步骤或返回上一级。
- `Ctrl+C` 取消当前任务，`Ctrl+Q` 退出，`Ctrl+P` 打开命令面板。
- 颜色只用于增强层级，状态和错误不能只依赖颜色表达。
- 60×20 终端必须保持输入框、当前上下文和主要动作可用；80×24 是标准快照尺寸。
- 长内容允许纵向滚动，核心表格需要换行或裁剪，不能依赖横向滚动才能理解关键结果。
- 禁用终端鼠标捕获，保留原生文本拖选；鼠标不得成为任何流程的必要条件。

## 13. 产品扩展规则

新增 Workbench 能力时按以下顺序判断：

1. 确认业务所有者和调用边界。
2. 判断它属于现有六个用户任务中的哪一个。
3. 判断它是导航分组、对象选择还是叶子操作。
4. 定义所需参数、Secret、确认条件和结果形式。
5. 提供语义化操作名称以及可能时的等价 CLI。
6. 在同一个产品 flow 中实现 dispatch、success、failure 和 cancel，并返回类型化 effect。
7. 直接调用所属 Application 或 Contract。
8. 添加行为、边界、错误、Secret、Activity 保留策略和快照测试。

不得仅为了新增能力而创建新的 App、首页入口、顶层 UI layer、manager、router、registry 或 UI-owned
port。只有用户任务确实无法归入现有产品入口时，才讨论增加首页分组。

## 14. 验收标准

### 14.1 通用交互

- 首页输入编号只更新动作列表和面包屑，内容区不新增菜单或原始输入。
- 子菜单导航、`/back` 和 `/home` 不污染内容区。
- 叶子操作参数未齐全时，内容区不逐项回显输入。
- 参数齐全后只写入一次完整、稳定、脱敏的操作记录。
- 每个结果、错误和确认都能识别 Workspace、作用域与目标对象。
- 输入焦点在导航、参数、确认、失败和取消后都返回唯一输入框。

### 14.2 安全

- live、外部写入和删除操作具有明确确认或受控 `--yes` 行为。
- `--dry-run` 与 `--no-exec` 不产生被禁止的副作用。
- Secret 不出现在内容区、历史、剪贴板、快照或 transcript 明文中。
- 取消和异常路径清理 Secret 与 pending 状态。

### 14.3 架构

- Workbench 只有一个 App 和一个主工作屏。
- 主屏只组合一个 `ActivityStream`、一个 `InteractionRegion` 和一个 `WorkbenchCommandInput`。
- UI 直接使用所属 Application/Contract，不调用 Typer executor 或解析 stdout。
- 跨业务访问遵守 Contract 边界，界面不拥有业务状态。
- 自动刷新、回放和跟随输出不创建第二套交互运行时。
- 产品 flow 不依赖 Textual Screen/App/Widget API；Screen 不保存产品状态转换表。
- 有限操作最多形成一个终态 Activity，自动刷新不追加 Activity。

### 14.4 验证证据

变更至少覆盖：

- `tests/workbench/test_app_*.py` 的分场景状态和行为测试；
- `tests/workbench/test_snapshots.py` 的 60×20、80×24 和关键流程快照；
- `tests/workbench/test_binary.py` 的真实入口测试；
- `tests/workbench/test_transcript.py` 的脱敏与审计测试；
- 使用 credential-free fixture 的真实 PTY 导航、参数、结果和取消检查。

文档描述的是统一产品合同。具体动作目录、Application 请求类型和结果字段仍以代码为准，不在本文复制
生成式清单。
