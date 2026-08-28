# Kairos Workbench 产品设计

本文定义 Kairos 人工交互工作台的当前产品边界、信息架构、交互模型和验收标准。它是
[统一 Textual 工作台决策](../decisions/0014-unified-textual-workbench.md)的产品层展开；业务所有权、
Application/Contract 边界和显式 CLI 约束仍以项目架构规则及
[CLI boundary](cli-boundary.md)为准。

## 1. 产品定位

Kairos Workbench 是面向人工操作的统一终端工作台。它帮助用户在不知道完整 CLI 命令和内部模块
结构的前提下，完成行情、标的、运行方案、运行准备、数据研究以及运行中心操作。

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

- 新用户：需要发现能力、通过运行前检查并获得下一步引导。
- 日常操作者：需要快速查看行情、控制 Launch、管理账户和诊断服务。
- 研究与开发人员：需要准备数据、运行研究流程、检查系统状态并把证据交给 Agent。

打开项目后的首页固定为七个稳定入口，不随内部命令或模块数量增长：

| 编号 | 产品入口 | 用户要完成的工作 | 主要业务所有者 |
| --- | --- | --- | --- |
| 1 | 查看市场行情 | 搜索报价、下载历史行情、浏览数据集、回放与连接运行服务 | Market、Reference、Integration |
| 2 | 查找市场标的 | 查找资产、交易所、合约、Market 与期权链 | Reference |
| 3 | 策略管理 | 创建和配置运行方案、启动运行实例、查看组件、跟随输出和控制执行 | Workspace/System、Execution、Market |
| 4 | 运行前检查 | 配置账户、市场数据、模型、通知并检查连接 | Account、Integration、Workspace/System |
| 5 | 数据与回测 | 管理 Dataset、Research Plan 和 Gate | Data/Research 所属 Application |
| 6 | 运行中心 | 查看活动运行实例、项目共享服务、支撑进程、依赖和日志 | Workspace/System |
| 7 | 项目管理 | 查看、检查、创建、打开或切换项目，安装项目模板 | Workspace/System |

首页只负责分组和导航，不拥有这些业务行为。

项目管理不隶属于“运行中心”。当前项目是整个 Workbench 的全局上下文，必须在正常首页持续可见；
首页第 7 项提供可发现的项目管理入口，`p` / `/project` 提供同一入口的快捷访问。项目管理虽然出现在
首页，但不拥有行情、Launch 或服务状态，只负责项目生命周期和项目文件检查。

### 2.1 产品对象与术语

Workbench 使用项目、运行方案、运行实例、项目共享服务和支撑进程表达运行结构：

```text
项目
  -> 运行方案（底层兼容名 Launch）
       -> 运行实例（Launch Instance）
            -> Strategy
            -> Account × N
            -> Risk
            -> Execution（可选）
            -> Capital（可选）
            -> Market（实例级或项目共享）
  -> 项目共享服务
       -> Reference
       -> Market
  -> 支撑进程
       -> System Supervisor
       -> Aeron
```

- `运行方案` 是可重复启动的规范化运行配置，底层继续兼容 `Launch` 命令和存储名称。
- `运行实例` 是某个运行方案的一次实际执行；启动和重启都会创建新的实例身份。
- `Strategy` 是运行实例内的进程和业务组件，不等于运行方案。
- “项目”是面向用户的顶层上下文；`Workspace` 只在技术证据或需要精确说明资源作用域时出现。
- “项目共享服务”只指具有 Workspace identity 的 Reference 和共享 Market。
- System Supervisor 和 Aeron 是支撑进程，可观察但不是普通业务服务。
- `Profile` 不是统一产品对象。Market runtime profile、Agent Profile 和通用 Config Profile 必须分别
  由自己的业务场景解释，不能合并为一个“管理 Profiles”入口。

### 2.2 全局项目上下文

项目决定业务数据、Launch 配置、运行资源和 transcript 的作用域，因此它不是某个业务任务下面的普通
子菜单。全局顶栏只持续展示产品身份与当前项目，例如 `KAIROS / trader`；不将 `p 项目管理`
这类导航动作拼接到项目身份上，也不在顶栏复制页面位置。当前页面由面包屑唯一表达，瞬时
任务状态由右侧状态区表达。

正常首页必须在固定项目摘要中展示：

- 面向用户的项目名称；
- 项目根路径或可消歧的缩略路径；
- 项目配置是否可用；
- 进入“项目管理”的 `p` / `/project` 提示；该提示仅属于首页摘要、项目管理入口和帮助，
  不得常驻在全局顶栏。

首页第 7 项“项目管理”和 `p` / `/project` 必须进入完全相同的上下文，不得维护两套项目流程。

项目摘要只保存已解析的 Workspace identity 和展示事实，不复制项目配置或业务状态。用户切换项目后，
Workbench 必须重新解析 Workspace，清理旧项目的临时选择、持续刷新和未完成向导，再以新项目进入正常
首页；不得让旧项目的 Launch、服务或资源选择泄漏到新项目上下文。

## 3. 信息架构

### 3.1 上下文模型

用户上下文由面包屑和显式选择表达，不使用 shell path。上下文可包含：

- 当前项目及其 Workspace identity；
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

全局项目上下文
  -> 项目管理
    -> 项目概览 / 检查 / 创建 / 打开或切换 / 安装模板
```

并非每个流程都需要所有层级。对象唯一且业务规则允许时可以直接进入详情；存在多个候选或选择会改变
业务含义时，必须先让用户明确选择。

### 3.3 运行作用域

产品必须显式区分 standalone、项目共享服务和 Launch Instance 三种作用域：

- standalone：直接访问 Provider、本地数据、目录或配置，不依赖正在运行的组件；
- 项目共享服务：连接具有 Workspace identity 的 Reference 或共享 Market；
- Launch Instance：连接具体 Instance 拥有或绑定的 Strategy、Account、Risk、Execution、Capital
  和 Market。

Backtest 和 Paper 的 Market 默认属于 Instance，Live 的 Market 默认项目共享，显式 Launch 配置可以在
业务规则允许时改变 Market scope。Account、Risk、Execution 和 Capital 不得因为 Workspace 下存在同名
socket 或文件而被解释为项目共享组件。

不同作用域不得因文件存在、服务失败或 Provider 不可用而自动互相回退。当前作用域必须通过面包屑、
对象摘要或确认信息对用户可见。从“运行中心”选择一个活动运行实例时，必须进入与“策略管理”共用的
实例详情，不得建立第二套运行详情或组件控制模型。

### 3.4 无项目状态

`kairos interactive` 无法解析项目时仍然进入同一个 Workbench App，但不得展示依赖 Workspace 的正常
首页。Interaction Region 直接进入项目启动状态，只提供创建回测示例项目、创建空项目、打开指定项目
和退出。创建或打开成功后刷新项目摘要并进入正常首页；项目存在之前不得展示或进入其余六个项目内
任务。

项目创建、模板安装和项目选择属于全局项目管理；无项目时由启动状态承载，已有项目时由 `p` /
`/project` 进入。它们不属于“运行中心”。需要 Workspace 的 `kairos observe`、Launch attach 等
深链接继续失败关闭并返回明确的项目解析错误。

### 3.5 项目管理

已有项目时，用户可以从固定项目摘要、`p` 或 `/project` 进入同一套项目管理上下文：

```text
项目管理

当前项目  trader-demo
路径      …/trader-demo/.kairos
状态      配置正常

[1] 项目概览  · 查看身份、路径、版本和关键配置
[2] 检查项目  · 检查目录结构、配置和必要资源
[3] 切换项目  · 打开另一个已有项目
[4] 创建项目  · 创建空项目或从模板创建
[5] 安装模板  · 安装可复用的项目模板
```

“检查项目”只诊断项目文件、目录和配置，不承担 Workspace 服务或 Launch 的运行诊断。项目管理不得
暴露通用配置清单、内部 Application 操作目录或未解释的 Profile 管理。成功创建或打开项目后必须明确
展示新项目身份，并返回该项目的正常首页；取消时保留原项目上下文。

## 4. 主界面结构

Workbench 使用一个垂直布局，从上到下包含：

| 区域 | 职责 | 内容生命周期 |
| --- | --- | --- |
| 项目摘要 | 展示当前项目身份、配置结论和项目管理入口 | 会话持续可见 |
| Activity Stream | 保留当前会话已经到达终态的操作、结果和证据 | 追加式，可清屏和复制 |
| Interaction Region | 展示当前选择、参数、确认、运行态或持续控制 | 随交互状态原地替换 |
| 状态栏 | 展示等待、执行中、成功、失败或暂停等瞬时状态 | 单行覆盖更新 |
| 命令栏 | 展示面包屑、收集当前输入 | 始终只有一个输入框 |
| 固定提示栏 | 展示全局可用的返回、帮助和退出方式 | 稳定且简短 |

### 4.1 内容区是 Activity Stream

内容区不是 stdout、聊天记录或当前页面模型，而是当前会话已经完成的业务活动和证据。一个有限操作在
成功、失败或执行后取消时最多形成一个 `ActivityRecord`；导航、候选、参数校验、确认请求、Running
提示和自动刷新不进入 Activity Stream。空内容区只显示一个不参与复制和 transcript 的弱提示。

`ActivityRecord` 使用关闭的 `kind` 和 `outcome`，并冻结展示标题、创建时间、业务作用域、脱敏后的
`copy_text`、审计摘要及可选 Artifact 路径。展示标题回答“哪个对象执行了什么动作”，审计摘要记录完整
操作意图，二者不得共用一个字段。`outcome` 表达本次用户交互怎样结束，不表达被查询业务对象是否健康；
业务结论由正文单独呈现。产品 flow 只能返回 `AppendActivity` effect；只有 `ActivityStream` renderer 可以
调用底层 `RichLog.write()`。`/clear` 只清当前可见 Activity，不删除 transcript、Artifact 或业务状态。

每条可见 Activity 在一个 Workbench 会话内获得单调递增的显示编号 `A001`、`A002`……。该编号是
Activity identity 的 UI 投影，不是业务序列；`/clear` 后不重用。内容区可通过 Tab 或鼠标单击聚焦，
普通单击会单选并以全宽矩形高亮对应 Activity，方向键移动 Activity 游标，Space 多选，`C` 复制所选项。
再次单击同一条目会取消选择；Command/Control+单击切换一条选择，Shift+单击
扩展连续范围。每个 Activity 统一在条目下方绘制分隔线，再保留一行非高亮空白，避免相邻内容过于
拥挤；分隔线始终展开到内容区有效宽度，不随条目内容长度或选择状态改变，首条不绘制额外顶边框。选择以
Activity identity 为单位，不以受终端宽度和换行影响的物理行号为单位；实时 tail 完成并形成终态
Activity 前不参与选择。内容区首次聚焦默认定位最新 Activity；没有显式选择时，
该游标条目作为默认复制目标显示全宽矩形高亮，`C` 直接复制它。终端能够转发时也接受
`Command+C`，但它不能作为 macOS 终端的可靠入口。之后恢复会话内最后
一次游标。切换到交互区或输入区时清空内容区的临时选择和高亮，但保留历史游标；再次进入内容区后
由该游标恢复默认复制目标。新 Activity 不得抢走历史游标，`/bottom` 明确回到最新 Activity。
Workspace Header 使用品牌、工作区和运行状态三段信息，底部以当前主题主色的弱化横线与内容区分隔；
该线属于区域边界，不随 Activity 滚动。头部状态采用响应式降级：100 列及以上显示完整状态，68–99 列
显示精简状态，低于 68 列隐藏右侧状态，优先保留工作区身份且不增加第二行。

用户位于底部时新 Activity 自动跟随；用户上滚查看旧结果时保持视口，并由状态栏提示未读数量，执行
`/bottom` 或 `Ctrl+End` 后回到底部。

Activity Stream 必须只把 `ActivityRecord` 作为展示事实源；RichLog 已经生成的字符行只是可丢弃的渲染
缓存。终端内容宽度变化后，Workbench 等待短暂的 resize quiet period，再按新宽度从事实源重建可见
历史。高度变化只调整视口，不重建内容。跟随状态在重建后回到底部；浏览状态按 Activity identity 和
Activity 内偏移恢复，不能复用宽度变化前的绝对终端行号。持续输出由 Interaction Region 的 Control 和
`LiveBuffer` 重建，不进入 Activity Stream 的 retained ranges。

Workbench 正常支持不小于 60 列、20 行的终端。低于 68 列使用窄屏 chrome，低于 24 行使用矮屏
chrome；低于正常支持尺寸时隐藏非必要内容并显示明确提示，但必须保留共享命令输入、帮助和退出能力。
响应式断点由主 Screen 统一发布，产品 flow、Application 和业务模块不得读取终端尺寸或拥有布局分支。

### 4.2 Interaction Region

Interaction Region 是当前 `InteractionState` 的被动投影，承载动作列表、参数说明、确认摘要、运行状态和
持续控制。它不保存另一份产品状态，也不直接启动 Worker。快照类 Control 原地替换最新值；日志类
Control 使用容量为 500 行的 `LiveBuffer`，显示等待首帧、跟随中、已暂停、刷新失败或已结束，以及
visible、unseen、dropped 和完整日志路径。同一时刻只有当前上下文拥有一个 Live Control；离开上下文必须
取消其 Worker，迟到的旧 generation 结果不得更新新上下文。

Market 自动刷新不产生 Activity；用户明确选择“保存当前快照”时才追加一条 Activity。Launch attach 的
暂停/继续只改变跟随状态，清空窗口不删除日志源，`/copy` 复制当前窗口；完整日志仍由 Launch 日志 owner
持有并在 Control 中显示路径。服务日志、Launch attach 和模型验证都使用这一 Control 规则，不得把每次
刷新、每行日志或每轮模型消息追加为 Activity。显式日志/Attach 会话结束时只追加一条终态摘要 Activity。

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

### 4.5 结果表达

Workbench 结果默认按“对象、结论、关键事实、影响、下一步动作”组织，不直接把 Application 返回的
mapping、内部字段名或 Python `repr` 作为产品页面。PID、socket、health file、process lock、Manifest
路径和规范化配置等证据只在明确的“查看进程信息”“查看技术证据”或复制给 Agent 的脱敏上下文中出现。

一个结果页不得只证明操作已经执行；它必须说明目标对象的最终状态。失败结果应在所属作用域内提供
重试、查看日志、修复配置或返回上一级等恢复动作，不把用户送到一个脱离当前对象的通用诊断菜单。

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
| `/copy 12`、`/copy 12-18` | 按稳定显示编号复制单条或连续 Activity |
| `/copy-selected` | 复制内容区中用 Space 选中的 Activity；没有显式选择时复制当前高亮条目 |
| `/copy-history` | 只复制 Activity Stream |
| `/goto 12` | 定位并聚焦指定 Activity |
| `/up 20`、`/down 20` | 按指定渲染行数滚动内容区；上滚暂停自动跟随 |
| `/bottom` | 跳到最新 Activity 并恢复自动跟随 |
| `/transcript` | 展示当前脱敏 transcript 路径 |
| `/project` | 进入全局项目管理；不向 Activity Stream 追加导航记录 |
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

确认模式默认聚焦“取消”；Tab 或 Shift+Tab 在取消和确认之间切换，Enter 执行当前选项。单独按 Enter 不得被解释为同意，`/y` 和 `/n` 仅作为兼容快捷方式保留。

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

Activity 正文统一按“对象、结论、关键事实、影响、下一步动作”组织；没有内容的区块省略。Activity 自身
已经是结果容器，正文不得再用一组嵌套彩色 Panel 制造卡片中的卡片。结果按用户问题选择以下六类最小
合适形式：

1. 状态概览：一句整体结论、一组关键事实和必要的异常组件表；
2. 对象详情：对象摘要和业务字段；
3. 集合列表：总数、稳定排序和多记录表格；
4. 操作结果：目标效果的最终状态，而不是仅说明请求已发出；
5. 检查与诊断：结论、原因、影响、恢复动作和技术入口；
6. 持续控制：Interaction Region 中可暂停、刷新、清空和复制的 Live Control。

空结果、等待/已受理、部分成功/需要注意、失败/结果未知和 Artifact 是覆盖上述模板的五种状态，不是新的
页面类型。Market Quote、Order Book、Account 费率等业务专属 renderer 可以保留自己的布局，但仍必须
满足相同的标题、结论、证据、复制和可访问性规则。

结果标题必须表达业务对象和作用域，不能只写“完成”或暴露内部 result kind。

Activity 头部使用关闭语义：`✓` 表示用户意图已经可靠完成，`×` 表示明确失败，`■` 表示确定未产生目标
效果的取消，黄色 `!` 表示部分成功、已受理、结果未知或其他需要注意的终态，`•` 只用于非操作性的产品
提示。查询成功返回“服务已降级”时，头部可以表示查询完成，正文仍必须明确显示降级；不得用头部标记
代替业务结论。取消时如果外部效果可能已经发生，必须使用结果未知而不是取消。

单对象事实使用无表头两列 grid，默认只保留支持结论的关键字段。多对象比较使用带表头表格；默认最多
展示 20 行，超过时同时显示完整总数、当前可见数和剩余数量，不得静默截断。排序不随终端宽度改变，
Provider 数、route 数、记录数等不同粒度必须明确命名。

### 9.2 错误信息

错误信息回答五个问题：

1. 哪个操作失败？
2. 用户可理解的原因是什么？
3. 哪些范围受到影响？
4. 现在可以执行什么恢复动作？
5. 重试是否安全？

恢复建议必须与当前错误相关。固定的 `/back`、`/home`、`/help` 不应在每条结果后重复。
订单、资金划转、通知发布和配置写入等操作在结果未知时不得给出通用重试建议；应提供 owner 已定义的
幂等状态查询。Preview/dry-run 必须明确“未执行任何修改”，accepted/submitted 必须明确“尚未确认生效”，
partial 必须同时列出成功与失败范围。

### 9.3 异步任务与结果路由

耗时操作通过单一 worker 执行：

- 开始时 `RunningInteraction` 和状态栏显示正在执行的完整操作；
- Busy 模式阻止重复提交；
- `Ctrl+C` 只取消当前可取消操作，不退出整个 Workbench；
- worker 成功、失败和取消都必须回到稳定的 Navigation 状态；
- 自动刷新只更新对应视图，不重复追加相同结果和操作记录；
- 每个操作和 Live Control 绑定稳定 operation ID 或 generation；离开上下文后取消 Worker，无法取消而迟到的
  结果因 Workspace、上下文或 generation 不匹配被丢弃；旧失败不得覆盖更新的成功；
- 用户在任务运行时提交新输入，才显示一次“任务仍在运行”的针对性反馈。

产品 vertical flow 同时拥有 dispatch、success、failure 和 cancel。它返回关闭的 presentation effects：
`AppendActivity`、`SetInteraction`、`RunOperation`、`SetStatus`，以及两个仅用于启动 Market/Launch 刷新的
Control adapter effect。Screen 负责应用 effect 和管理 Textual Worker，不推断产品结果含义。

### 9.4 证据、格式与 Artifact

业务观测和状态结果在 owner 提供事实时统一展示来源、业务时间、获取时间、新鲜度和完整性。默认页面优先
显示“3 秒前”“数据已过期”“部分完整”等决策信息；精确纳秒、source sequence 和原始 UTC 时间属于技术
证据。不得把 `_nanos`、`_ms`、`NS` 等实现字段名作为默认产品标签。

Workbench presentation 可以共享时间、duration、count 和 percentage 等纯机械 formatter：用户时间使用
本地时区并带 offset，技术证据保留 UTC；计数使用千位分隔；百分比说明分母，零分母显示“暂无样本”；
Price、Quantity、Money 和费率保持 owner 语义类型的十进制精度，不经 `float` 往返。格式化函数不得接受
任意整数并猜测时间或 duration 单位。

Artifact 结果必须区分“操作结束”和“产物可用”。只有已确认存在且属于当前操作的文件才设置
`artifact_path`；默认展示 Workspace 相对路径，并在 owner 提供时展示格式、记录数、文件大小、生成时间和
部分写入范围。`/clear` 不删除 Artifact，删除必须调用 owner 的明确动作。

### 9.5 复制、留存与线性化

单条 Activity 复制包含冻结的 display title、scope、created-at、正文和等价命令，不依赖复制时的当前
Workspace。二维 Rich 布局必须能转换为不依赖颜色和位置的纯文本；视觉截断值在复制时提供完整安全值或
明确标记同样被截断。`/copy-history` 不包含 Live Control；Live Control 只复制当前安全窗口，完整日志由
owner 路径定位。

Activity 显示编号不复用，旧 Activity 不得被静默淘汰。若后续根据长会话 reflow、内存和渲染测量引入
容量上限，界面必须明确提示更早记录只保留在 Transcript；不得无证据增加缓存或淘汰策略。模型验证每轮
消息不进入 Activity 或 `/copy-history`，结束时只保留验证摘要；需要完整对话时必须由明确的会话 Artifact
owner 持有。

## 10. 七个首页入口

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

Reference generation、publication 和 Provider 同步状态属于项目共享服务的运行信息，不应混入普通
标的详情。

### 10.3 策略管理

策略管理围绕运行方案和运行实例组织：

- 列出、创建和编辑运行方案；
- 选择 backtest、paper 或 live 模式；
- 启动、停止、等待和诊断实例；
- 查看组件与时间线；
- 跟随运行输出；
- 从具体 Instance 进入 Execution 或 Market 控制。

运行态 Execution 必须绑定具体 Launch Instance。Standalone 订单操作属于明确的账户或 Execution
作用域，不得因找不到实例而自动切换。

运行方案列表同时合并已发布配置和草稿；运行注册记录作为该方案的活动或历史实例展示。选择运行方案
后进入唯一的方案详情，活动实例和历史实例是下一级对象。界面不得把带 `instance_id` 的记录称为
“正在运行的 Launch”。

Instance 组件默认只提供状态、日志、current view 和所属业务 Contract 允许的动作。Account、Risk、
Execution、Capital 和实例级 Market 的生命周期由 Launch 统一管理，不提供局部启动、停止或重启入口。
项目共享 Market 可以从 Instance 详情进入其 connected 业务视图，但其 Workspace 生命周期仍由
“运行中心”负责。

### 10.4 运行准备

运行准备按资源类型组织账户、市场数据、模型服务端点、可用模型和通知目标。配置向导必须区分普通字段与 Secret，
支持预览、验证、编辑和删除，并在结束时给出资源是否可用于 paper/live 的明确结论。

Account 拥有余额、仓位、权益和账户侧订单事实。Workbench 不允许 Execution 或其他模块向 Account
推送第二份 live 观察事实。

### 10.5 数据研究

数据研究区分数据准备和研究流程：

- Dataset 浏览、需求计划、执行和 Data Gate；
- Research Plan、锁定、执行证据和 Research Gate。

会改变数据或发布状态的步骤必须显示目标、计划 hash 或证据文件，并根据风险进入确认。

### 10.6 运行中心

“运行中心”是当前项目实际运行拓扑的观察与 Workspace 服务控制入口，不是项目设置、运行方案配置、
CLI 命令目录或剩余能力集合。进入后直接显示按作用域分组的当前运行清单：

```text
运行中心

项目共享服务
  Reference    运行中 · 持续运行
  Market       运行中 · 按需启动 · 被 2 个实例使用

活动运行实例
  btc-paper / run-003      Paper · 正常
  options-live / run-007   Live · Execution 异常

支撑进程
  System Supervisor        运行中
  Aeron                     运行中
```

选择活动运行实例后进入 10.3 定义的同一个实例详情。支撑进程只提供状态、日志和
技术证据，不作为普通业务服务提供任意启停。`kairos observe` 直接进入这个页面并持续刷新；Workbench
中的 `/observe` 使用同一个分层结果模型；`observe --once` 继续保留机器可读输出。

#### 10.6.1 项目共享服务

当前只有 Reference 和共享 Market 具有 Workspace-scoped identity。服务详情必须同时展示：

- 运行状态；
- 运行方式；
- 是否由 System Supervisor 自动恢复；
- 当前依赖它的 Launch；
- 日志是否可用；
- 与状态匹配的可执行动作。

运行方式使用以下关闭词汇：

- `按需启动`：服务进程正在运行，但不在 Supervisor desired state 中；
- `持续运行`：服务已登记为 desired，Supervisor 会在异常退出后尝试恢复；
- `正在恢复`：Supervisor 正在按策略重新启动服务；
- `恢复已暂停`：自动重试达到上限，需要人工检查；
- `已停止`：进程未运行，也未登记为 desired。

用户主动启动已停止的 Workspace 服务时，产品动作命名为“启动并保持运行”。这个动作必须通过同一个
System Application 用例完成启动、等待就绪、登记 desired state 和启动或复用 Supervisor。Workbench
不得只调用 `ensure_running()`，否则它与 `kairos system up` 具有不同生命周期语义。

“停止服务”必须先检查活动运行实例依赖；存在依赖时拒绝并列出实例。允许停止时先取消 desired
state，再请求服务安全停止。“重启服务”同样先通过依赖安全检查，成功后保持重启前的 desired 语义。
Workbench 与显式 `system up/down/restart` 必须调用同一个 Application 用例，不得各自复制部分流程，
也不得通过 Typer executor 互相调用。

#### 10.6.2 运行实例与支撑进程

活动运行实例用 `launch_id / instance_id` 标识，摘要展示 mode、整体状态和异常组件。选择后进入实例
详情，由 Launch Application 统一停止或恢复整个 Instance。运行中心不得给 Instance-owned Account、
Risk、Execution、Capital 或 Market 提供通用局部生命周期操作。

System Supervisor 展示当前 desired 服务、恢复状态和最近错误；Aeron 展示进程状态和日志。它们是
Reference、Market 或 Launch 故障的技术证据，不成为首页能力，也不提供普通的独立启停流程。

#### 10.6.3 诊断与恢复

项目文件检查、Workspace 服务诊断和 Launch 诊断是三个不同用例：

- 项目文件和 Launch 配置问题在打开项目、运行前检查或 Launch 校验中呈现；
- socket、health file、process lock 和 Supervisor 状态在具体 Workspace 服务详情中呈现；
- Strategy 与 Instance 组件问题在具体 Launch/Instance 中呈现。

Workbench 不提供脱离对象的全局“问题诊断”入口。只有 Application 已证明 socket、health file 或其他
资源失效且没有活进程或锁所有者时，具体服务页面才显示“清理并重新启动”或“仅清理”。无法证明安全时
必须失败关闭并展示占用证据。

#### 10.6.4 不属于本入口的能力

- 项目创建、打开、切换和模板安装属于全局项目管理；无项目时直接显示为启动状态；
- 账户、市场数据连接、模型服务端点、可用模型和通知属于运行前检查；
- Market runtime profile 属于市场连接或 Launch，Agent Profile 属于 Agent 资源；
- Risk、Capital 和 Integration 的业务动作属于具体 Launch Instance、资源配置或显式 standalone CLI；
- Manifest、全部 TOML、配置路径、Application 操作清单和通用 Config Profile 不作为 Workbench 菜单。

System 组合可以定位 Workspace 组件、执行依赖安全检查并连接 owner Contract，但不拥有 Market、Risk、
Capital 等业务语义，也不得绕过业务 Contract 调用其他业务主包 Application。不存在 Workspace target
时，System 入口必须说明该 scope 不存在，不得自动寻找任意 Launch Instance 代替。

Workbench 不把 Workspace runtime 伪装成保留 ID `kairos-system` 的隐藏 Launch。Workspace 服务、
Supervisor 和普通 Launch 使用各自真实的 identity 与生命周期呈现。

### 10.7 项目管理

项目管理复用 3.4 和 3.5 定义的启动与全局项目上下文。首页第 7 项、项目摘要中的快捷提示以及
`p` / `/project` 进入同一流程。项目管理不复制“运行中心”的运行状态、服务诊断或恢复动作。

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
提供的日志路径定位。Activity 选择和编号范围复制直接从保留的 `ActivityRecord` 生成文本，不截取
RichLog 的屏幕行。macOS 同时使用系统 `pbcopy` 兜底。Terminal 和 iTerm 通常会自行消费
`Command+C`，Workbench 无法可靠接收；终端确实转发时仍接受该快捷键，但普通 `C` 和
`/copy-selected` 必须始终可用。

## 12. 键盘、终端与可访问性

- 所有核心流程必须只用键盘完成。
- Enter 提交当前输入；Esc 或 `/back` 取消当前参数步骤或返回上一级。
- `Ctrl+C` 取消当前任务，`Ctrl+Q` 退出，`Ctrl+P` 打开命令面板。
- Tab / Shift+Tab 在命令输入、可用交互选项和非空内容区之间循环焦点；内容区聚焦时使用方向键、
  Space 和 `C` 完成定位、选择和复制。
- macOS 不得要求用户配置 Option/Meta 键。`Fn+↑` / `Fn+↓` 可以承担 PageUp / PageDown，但
  `/up`、`/down`、`/bottom` 和复制命令是跨终端的可靠入口。
- 颜色只用于增强层级，状态和错误不能只依赖颜色表达。
- 60×20 终端必须保持输入框、当前上下文和主要动作可用；80×24 是标准快照尺寸。
- 长内容允许纵向滚动，核心表格需要换行或裁剪，不能依赖横向滚动才能理解关键结果。
- 启用终端鼠标事件以支持 Activity 单击聚焦和组合键多选；原生终端文本框选使用 Shift+拖拽。鼠标
  不得成为任何流程的必要条件，所有选择和复制能力必须保留等价键盘与命令入口。
- 交互区中的鼠标点击只移动高亮选项，不确认、不执行；用户必须按 Enter 提交当前高亮项。

## 13. 产品扩展规则

新增 Workbench 能力必须遵循“归类 → 复用 → 改造 → 新增模式”的顺序：

1. 确认业务所有者、调用边界，以及用户实际要回答的问题。
2. 判断它属于现有六个用户任务中的哪一个，并为结果选择六类结果模板之一和所需的五种状态覆盖；
   空结果、等待、部分成功、失败和 Artifact 不得被包装成新的页面类型。
3. 判断它是导航分组、对象选择还是叶子操作，并查找当前生产代码中可复用的 flow、业务 renderer 和
   presentation primitive。
4. 能复用时直接复用；不能完整表达时，优先对所属 flow、业务 renderer 或无业务语义的共享 primitive
   做最小改造，不能因为字段或视觉略有差异就复制一套模式。
5. 只有现有六类模板和五种状态覆盖无法真实表达当前用户问题时，才可以提出新模式。新增模式必须在变更
   说明或 Decision 中回答：当前具体问题和调用者是什么、为什么复用或改造不成立、最小新增语义是什么、
   如何与终态 Activity 和 Live Control 边界组合，以及哪些行为、文案、无障碍和快照测试证明其必要性。
6. 定义所需参数、Secret、确认条件和结果形式，提供语义化操作名称以及可能时的等价 CLI。
7. 在同一个产品 flow 中实现 dispatch、success、failure 和 cancel，返回类型化 effect，并直接调用所属
   Application 或 Contract。
8. 添加行为、边界、错误、Secret、Activity 保留策略和快照测试。

不得仅为了新增能力而创建新的 App、首页入口、顶层 UI layer、manager、router、registry 或 UI-owned
port，也不得为了表面统一而引入接受任意业务数据的通用 renderer。只有用户任务确实无法归入现有产品
入口时，才讨论增加首页分组；视觉差异或假设的未来调用者不足以证明新模式成立。

## 14. 验收标准

### 14.1 通用交互

- 首页输入编号只更新动作列表和面包屑，内容区不新增菜单或原始输入。
- 正常首页持续展示当前项目，并可通过 `p` / `/project` 进入项目管理。
- 正常首页第 7 项进入与 `p` / `/project` 相同的项目管理上下文。
- 无法解析项目时不展示正常首页，只提供创建、打开和退出，其余六个项目内入口不可见且不可达。
- 切换项目会停止旧项目的持续刷新、清理临时选择和向导，并以新项目身份返回首页。
- 子菜单导航、`/back` 和 `/home` 不污染内容区。
- 叶子操作参数未齐全时，内容区不逐项回显输入。
- 参数齐全后只写入一次完整、稳定、脱敏的操作记录。
- 每个结果、错误和确认都能识别 Workspace、作用域与目标对象。
- 产品结果按对象、结论、关键事实、影响和下一步动作呈现，不直接展示 Python mapping repr。
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
- Observe 结果分别表达项目共享服务、Launch Instance 和支撑进程，不在 Workspace scope 虚构
  Account、Risk、Execution 或 Capital 状态。
- “运行中心”和“策略管理”选择同一个实例时进入同一套 Instance Session 与页面。
- Workspace 服务启动登记 Supervisor desired state；停止先取消 desired state；停止和重启都执行活动
  Launch 依赖检查。
- Instance-owned 组件只通过所属 Launch Instance 定位，Workbench 不提供局部生命周期捷径。
- Workbench 不暴露通用“高级设置”“管理 Profiles”“查看可用 Application 操作”或全局 repair 菜单。

### 14.4 验证证据

变更至少覆盖：

- `tests/workbench/test_app_*.py` 的分场景状态和行为测试；
- `tests/workbench/test_snapshots.py` 的 60×20、80×24 和关键流程快照；
- `tests/workbench/test_binary.py` 的真实入口测试；
- `tests/workbench/test_transcript.py` 的脱敏与审计测试；
- 使用 credential-free fixture 的真实 PTY 导航、参数、结果和取消检查。
- Workspace 服务按需运行、持续运行、依赖阻止停止、Supervisor 恢复和安全清理的状态测试。
- Backtest/Paper Instance Market、Live shared Market 以及显式 Market scope 的边界测试。

文档描述的是统一产品合同。具体动作目录、Application 请求类型和结果字段仍以代码为准，不在本文复制
生成式清单。
