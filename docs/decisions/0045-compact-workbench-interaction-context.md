# 紧凑的 Workbench 交互与命令上下文

- Status: Accepted
- Date: 2026-08-30
- Refines: [Decision 0042](0042-terminal-activity-and-live-control.md)、[Decision 0043](0043-task-and-scope-oriented-workbench-navigation.md)
- Scope: Kairos Workbench Interaction Region、Command Dock 与响应式 presentation

## Context

Workbench 的 Choice/Input 说明使用带标题 Panel，边框、空行和重复路径会挤压动作；Command Dock 又只显示
当前页面路径，跨任务修复时缺少稳定心智锚点。展示完整访问栈虽信息充分，却增加理解和用户教育成本。

## Decision

Workbench 保持单屏、单输入和条目化动作，并使用以下固定结构：

```text
Activity Stream
对象 · 用途                         当前结论（仅必要时）
[1] 动作  ·  一句结果导向的说明
[2] 动作
‹  稳定锚点 › 问题域 › 当前对象                 状态
› 唯一输入框
```

Choice 的普通说明不再使用 Panel；对象说明默认一行，只有约束、错误或安全信息无法由动作说明表达时增加一行。
普通菜单不显示对象说明，Confirm、Running 和 Control 保留各自必要的完整表达。

Command Dock 同时显示返回符号、最多三段语义路径、短状态和唯一输入框。语义路径表达“首要任务、当前问题域、
当前对象”，不等同页面访问历史；多次跳转仍由现有逻辑页面栈管理，`Esc` 返回一步，完成前置条件后自动回到
最近检查点，完整返回历史只在 `/back` 中按需显示。

稳定锚点由真实进入任务决定，例如 `paper-demo › 行情连接 › Massive`、`AAPL › 标的目录 › Massive` 或
`连接与配置 › 行情连接 › Massive`。导航包统一映射路径；产品 flow 只提供 owner-owned 对象名称、当前结论
和短状态，不拼接跨任务路径，也不新增任务栈或业务状态 owner。

空间不足时删除次要信息，不缩小核心文字：100 列及以上显示三段路径和动作说明，68–99 列折叠中间段，
60–67 列保留首尾锚点并隐藏对象补充说明和动作 description，低于 60×20 延续现有最小输入、帮助和退出策略。

Activity Stream 仍只保存终态活动；导航、当前说明、参数提示和自动恢复不进入 Activity。Workspace Header 的
状态表示瞬时运行状态，Command Dock 的状态表示当前交互结论，两者不得互相代替。

## Consequences

- 普通对象交互预计回收两到四行，动作和输入获得稳定空间。
- 同一权威页面可按真实进入任务显示不同语义锚点，但不复制页面或业务逻辑。
- `ChoiceInteraction` 的紧凑标题、可选正文和动作需要分离；旧的任意 summary 不再默认套装饰性 Panel。
- 导航包新增响应式无关的语义路径视图，宽度降级继续由 Screen/Widget 负责。
- 危险确认、复杂诊断和持续 Control 不为追求紧凑而删除必要信息。

## Verification

- 行为测试覆盖直接进入、跨任务修复、多次跳转、`Esc`、`/back`、自动恢复与 `/home` 清理；
- 100×30、80×24、60×20 快照覆盖响应式层级、对象操作、错误恢复和可以继续的终态；
- 行为测试覆盖 Secret 单输入、复制脱敏和暂存凭据清理；
- copy/transcript 测试证明窄屏隐藏的信息仍可完整脱敏复制；
- 真实 PTY 验证单输入、焦点、滚动、热键和无截断，Python 类型检查与 Workbench 全量测试作为交付门槛。
