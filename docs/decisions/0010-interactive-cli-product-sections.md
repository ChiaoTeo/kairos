# Decision 0010：Interactive CLI 产品 Section 边界

- Status: Accepted
- Date: 2026-08-23
- Scope: `kairospy.surface.cli.interactive`、交互式 CLI 测试

## Context

Interactive CLI 曾由单个 `interactive.py` 同时管理会话循环、导航状态、命令执行、一次性预览和所有
产品入口。随着 Account、Launch、Reference、Market、Data 和 System 功能增长，该文件超过两千行，
菜单、帮助、命令映射和测试开始共享同一个变化边界。较小能力也缺少明确位置，容易被合并进新的
通用操作模块。

交互式 CLI 是现有业务 application、contract 和命令的用户界面适配层，不应成为新的业务 facade，
也不应通过动态路由框架模糊产品所有权。

## Decision

`kairospy.surface.cli.interactive` 使用 package 结构。`session.py` 只管理输入循环和显式 section 分派；
`context.py` 统一管理 workspace context、刷新和 `home/back` 状态；`execution.py` 统一管理命令确认、
执行、输出边界和 workspace 参数；`models.py` 只定义交互状态与命令描述。

产品行为按稳定能力放入 `sections/`：Getting Started、Strategy、Research & Data、Business 和 System。
Account、Market、Reference、Order/Execution、Risk、Capital、Integration、Notifications、Data、Research、
Launch、Observe、Project、System Runtime 和 Config 各自拥有独立文件。每个 section 使用普通的
`print_menu`、`print_help` 和 `handle` 函数约定；不建立 Router、Registry、Manager 或 Handler 基类。

section 负责自己的菜单、帮助、输入适配、`GuidedCommand` 构建和业务特有展示。Session 不保存业务
argv。常驻 shell 与 `--dry-run` / `--no-exec` 预览复用 section 的命令构建函数。公共 Python API 仅为：

```python
from kairospy.surface.cli.interactive import run_interactive
```

首页按用户任务组织，不直接展示内部 section 清单。稳定的一层入口为 Strategy、Trade Management、
Reference、Data & Research、System & Integration、Observe、Project & Help 和 Market 行情。根据
[Decision 0011](0011-trade-workbench-and-execution-modes.md)，Trade Management 使用 `/trade` 账户优先
工作台：先进入 Account 列表并选择账户，再进入账户事实或 Execution standalone 订单操作；运行态
Execution 必须从具体 Launch Instance 的 component 进入。本次不重组 Risk 与 Capital。新增 section
不自动获得新的首页编号，必须先判断它属于哪个用户任务入口。

Market 行情交互分为两层。顶层 `/market` 是 standalone/direct，直接调用 provider 或读取本地
文件，对应 `kairos market once/validate/replay/download/reference-universe`；它可以从 Reference
选择描述，也允许显式输入底层 Market 描述。`/system/market` 和
`/launch/<launch-id>/instances/<instance-id>/components/market` 是 connected runtime 作用域：Market 必须从 Reference catalog
搜索结果中选择，Source 必须从目标 Market runtime 的 `data_sources` 结果中选择，交互层不接受
原始 Market ID 或 Source ID。launch 存在多个 instance 时也必须先从 registry 列表选择，并在
当前 context 中保存 instance、Market 和 Source 选择。两层不根据文件是否存在互相回退。

交互测试镜像生产 section 目录。对内部函数的测试和 monkeypatch 指向实际所有者模块，不通过 package
入口重导出私有实现。

## Consequences

- 新产品入口必须放入其所有者 section，并同时提供菜单、帮助、handler 和行为测试。
- 首页编号按用户任务分组，不随内部 section 数量增长；导航分组不得持有业务命令。
- `session.py`、`context.py` 和 `execution.py` 不得吸收具体业务命令映射。
- 不因能力当前较小而将不同所有者合并到 `operations.py`、`common.py` 或类似兜底模块。
- Account、Execution、Risk、Market、Reference、Integration 和 Workspace/System 的既有业务所有权不变；
  交互 section 只适配现有 application、contract 或 CLI。
- 新增交互行为可以独立测试，跨 section 端到端测试只保留关键用户路径。
