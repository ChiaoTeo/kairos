# Decision 0014：统一 Textual 工作台

- Status: Accepted
- Date: 2026-08-24
- Scope: Kairos 人工交互入口与终端 UI
- Supersedes: [Decision 0010](0010-interactive-cli-product-sections.md)

## Context

原 interactive shell 以路径、菜单编号和命令字符串组织会话；Observe 又拥有独立 TUI。
用户需要同时理解页面任务、shell 路径、CLI 命令和多套返回方式。交互层还会构造 CLI argv、
解析终端输出，并重复实现确认、取消和上下文管理，因此新增能力容易只覆盖其中一个入口。

## Decision

Kairos 只保留一个人工交互运行时：`KairosWorkbenchApp`。`kairos interactive`、`kairos i`、
交互式 Launch/资源配置、`launch attach` 和非 `--once` 的 `observe` 都进入同一个 Textual App
及其 screen stack。首页固定为行情、标的、策略、资源、研究和系统六个用户任务；Header 展示
Workspace，上下文由 screen stack 和明确选择保存，Footer 展示当前页面可用操作。

Workbench screen 直接调用所属 application 或 contract client。它不得调用 Typer executor、
拼装用于自身执行的顶层 CLI 命令，也不得通过解析 stdout 获得业务结果。表单、表格、确认框、
日志、异步 worker、错误与取消均在同一个 App shell 中呈现。危险或外部操作统一支持确认、
`--yes`、`--dry-run` 和 `--no-exec`；自动刷新和只读视图仍可用于建立页面上下文。

显式、非交互 Typer 命令继续作为脚本和 CI API。它们与 Workbench 共享 application/contract，
但不是第二套人工交互机制。旧 `surface.cli.interactive` 会话、`GuidedCommand`、prompt-toolkit
依赖和独立 Observe App 被删除，不保留兼容运行时或隐藏入口。

## Consequences

- 新的人工交互能力必须进入现有 Workbench screen stack，不得新增另一个 `App` 或常驻 shell。
- 用户上下文以 Workspace 和所选业务对象表达，不再显示或维护 shell path。
- 业务行为和安全校验属于 application/contract；Textual 只负责输入适配与展示。
- `observe --once` 和显式 CLI 命令保留机器可读输出；其交互版本复用同一 Workbench。
- 删除旧交互代码前必须以功能矩阵和端到端测试证明 Account、Order、Market、Reference、
  Launch/Instance、资源、Data/Research、System、Risk、Capital 与 Integration 均有对应路径。

Workbench 的信息架构、单输入状态机、内容记录、安全和验收规范见
[Kairos Workbench 产品设计](../architecture/workbench-product-design.md)。
