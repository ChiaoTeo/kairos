# KairosPy CLI scenario guide

这篇导览用用户场景来理解 `kairos` / `kairospy` 命令。它不是完整命令手册；
完整参数、配置和故障处理见 [`operations.md`](operations.md)。

CLI 的第一层应该先回答“我现在想完成什么”，再暴露内部模块名。现有命令可以按
四条线理解：

1. 项目与策略运行
2. 系统运维
3. 便捷查询与一次性操作
4. 数据与研究

`kairos` 和 `kairospy` 是同一个入口。用户文档优先使用较短的 `kairos`。

## 第一条线：项目与策略运行

这条线服务于策略作者和研究者的日常路径：创建项目、创建 launch 配置、启动策略、
查看状态、等待报告、停止运行。

常用入口：

```bash
kairos quickstart
kairos project init my-project --id my-project --template backtest
kairos project doctor
kairos config agent setup
kairos launch init my-launch
kairos launch diagnose validate my-launch
kairos launch start my-launch
kairos launch status my-launch
kairos launch logs my-launch
kairos launch wait my-launch
kairos launch stop my-launch
kairos observe
```

用户心智：

- `project` 管工作区：项目目录、`.kairos/`、模板和 readiness。
- `config agent setup` 准备 Workspace 级 Agent credential、Profile 和只读 MCP 资源；
  API Key 不进入 launch 配置。
- `launch` 管一次策略运行：配置、实例、日志、报告、生命周期。
- `observe` 是运行中的总览，不替代 `launch` 或 `system`。
- `quickstart` 是不知道从哪开始时的入口。

这条线应该排在 CLI 帮助的最前面，因为它是新用户和策略开发者最常走的路径。

## 第二条线：系统运维

这条线服务于维护 workspace runtime 的人：启动组件、看健康状态、查日志、修复 stale
资源、检查 socket 和锁。

常用入口：

```bash
kairos system up
kairos system status
kairos system list
kairos system logs --component market --lines 100
kairos system doctor
kairos system repair
kairos system down
```

用户心智：

- `system` 管基础运行环境和组件进程。
- `launch` 管策略实例，策略实例需要的组件由 launch 按需拉起。
- 如果问题是“策略为什么没起来”，先看 `launch status` / `launch logs`。
- 如果问题是“组件、socket、锁、日志、健康检查是否正常”，再看 `system`。

这条线应该从“策略运行”中分出来。它不是便捷查询，也不是业务模块入口。

## 第三条线：便捷查询与一次性操作

这条线服务于临时查看或操作某类业务事实：账户、行情、订单、Reference 查询和通知目的地。
底层 Provider 命令保留给脚本和故障排查，不作为交互式产品导航入口。

常用入口：

```bash
kairos account list
kairos account snapshot --account-id main --workspace my-project
kairos account balances --account-id main --workspace my-project
kairos order open-orders --account-id main --workspace my-project
kairos order history --account-id main --symbol BTCUSDT --workspace my-project
kairos order fills --account-id main --symbol BTCUSDT --workspace my-project
kairos market validate
kairos system component market snapshot quote --market-id market:binance:spot:BTCUSDT --provider binance
kairos launch instance component execution status <launch-id> --instance <instance-id> --mode <mode> --workspace my-project
kairos reference markets --active-only --workspace my-project
kairos reference markets --asset-code AAPL --active-only --workspace my-project
kairos reference listings --symbol BTCUSDT --workspace my-project
kairos reference markets --market-id market:binance:spot:BTCUSDT --workspace my-project
kairos reference option-chain --underlying instrument:equity:US:AAPL:common
kairos notifications validate --workspace my-project
kairos integration earn --help
```

用户心智：

- `account` 回答“我的账户和仓位是什么状态”。
- `market` 回答“行情输入和快照是什么状态”。
- `order` 在明确账户后直接查询或操作 provider 订单；运行中 Execution 生命周期从具体
  `launch instance component execution` 查看。
- `reference` 回答“有哪些 market / listing、某个市场是什么、如何按 symbol 检索”。
- `notifications` 回答“策略消息能不能发出去”。
- `integration` 是直接调用 Provider 能力的低层入口，默认比 `account`、`market`
  更接近外部交易所。

这条线适合放在“便捷功能”或“业务查询”分组下。它们不应该和 `system` 混成一个
笼统的 Operations 分组。

## 第四条线：数据与研究

这条线服务于研究数据和可复现研究流程：审阅数据需求、执行数据获取、锁定研究计划、
发布 gate 证据。

常用入口：

```bash
kairos data list
kairos data plan requirements.json
kairos data execute requirements.json --expected-plan-hash <plan-hash>
kairos data set list
kairos data gate show <composition-hash>
kairos research plan lock research-plan.json
kairos research gate publish research-plan.json research-evidence.json
```

用户心智：

- `data plan` 只审阅，不下载。
- `data execute` 才获取或生成数据。
- `research plan lock` 固定研究计划，避免事后改变问题定义。
- `research gate publish` 固定证据和结论。

这条线可以靠近“项目与策略运行”，但不要埋在系统运维或业务查询下面。

## 低频高级工具

这些命令通常用于调试、自动化或维护，不应该占据第一视野：

```bash
kairos config
kairos browse
kairos version
```

用户心智：

- `config` 是高级配置检查，不是第一天入口。
- `browse` 是低层文件浏览，不是常规项目导航。
- `version` 是安装检查。

## 建议的顶层排列

从用户习惯出发，顶层帮助可以按下面的顺序呈现：

```text
Getting started
  quickstart
  project

Strategy workflow
  launch
  observe

Research and data
  data
  research

System operations
  system

Convenience commands
  account
  market
  order
  reference
  notifications
  integration

Advanced tools
  config
  browse
  version
```

这个排列不要求立即重命名命令。第一步只需要让文档和 `--help` 的分组一致；第二步再看
哪些便捷命令需要更友好的别名或更清楚的二级 help。

## 后续整理原则

- 保留稳定命令名，先调整分组、说明和示例。
- 新用户路径优先：`quickstart`、`project`、`launch`、`observe` 应该最容易发现。
- 系统运维单独成线：`system` 不和账户、行情、订单混在一起。
- 便捷功能按用户问题命名：账户、行情、订单、Reference、通知、Provider 操作。
- passthrough 命令需要补人类可读 help，避免直接暴露底层 Rust CLI 的心智模型。
- 文档中优先使用 `kairos`；兼容性说明再提 `kairospy`。

## 输出格式规范

CLI 默认面向人类阅读；脚本、CI 和外部集成显式请求机器格式。

```bash
kairos reference markets --active-only
kairos reference markets --active-only --format json
```

格式含义：

- `text`：默认的人类可读摘要，适合状态、诊断、详情和下一步建议。
- `table`：列表视图，适合 markets、listings、balances、positions、orders。
- `json`：机器可读输出，适合脚本、CI、测试和外部集成。

新建 workspace 默认使用 `text`。如果团队希望所有命令默认返回 JSON，可以在
`.kairos/kairos.toml` 中显式配置：

```toml
[cli]
format = "json"
```

交互式入口是产品体验，应该显式选择 `text` 或 `table`，不受机器默认格式影响。用户仍然
可以离开交互式入口，直接运行同一命令并加上 `--format json`。

## 交互式 CLI 方向

场景分线解决的是“命令怎么排列”，但仍要求用户知道自己应该敲命令。KairosPy 使用
统一交互式入口，把用户从“记命令”带到“选择我要完成的事”。

推荐形态：

```bash
kairos interactive
```

也可以保留短别名：

```bash
kairos i
```

两个入口打开同一个 Textual 工作台。首页按用户任务组织：

```text
1. 查看市场行情
2. 搜索交易标的
3. 创建并运行策略
4. 运行前检查
5. 数据与回测
6. 系统维护
```

“搜索交易标的”使用 Reference 完成资产、交易所、交易品种、具体市场与期权链检索；
“查看市场行情”同时提供 Provider 直读、Workspace Market 和 Launch Instance Market 三种
明确作用域。

Header 始终显示 Workspace；页面栈保存当前 Launch、Instance、账户、Market 或 Reference
选择；Footer 只显示当前页面有效的返回、刷新、帮助和取消操作。`?` 打开上下文帮助，
`Ctrl+P` 打开命令面板，不再提供需要记忆的 shell path、`home/back` 命令或第二套命令地图。

Reference 页面以表格显示检索候选，选中后进入详情；canonical ID 等实现标识只在
“技术标识”动作中出现。

用户在交易品种目录中先选择股票、现货、永续合约、交割合约、期权或指数，再输入代码或
名称并选择结果。交易所详情可以继续查看其上市信息；交易品种详情可以继续查看 listing 和
具体 market。Reference 的 generation、provider 和 publication 状态仍只出现在
后台服务页面。

交易所、券商和数据提供商数量较少，进入对应目录后直接列出并允许选择详情，不额外要求
用户先输入检索条件。资产、交易品种和具体市场仍采用“检索后选择”的流程。

交互式入口不是第二套业务 API。它只做三件事：

1. 读取当前 workspace 状态。
2. 根据场景给出可选动作。
3. 收集输入并直接调用所属 application/contract，在统一页面中展示结果。

普通 CLI、脚本、CI 和交互式入口因此共享应用路径，但不共享终端输出解析。

Observe、Launch 编辑与跟随输出、资源配置也都打开这一个 App，不会切换到另一套视觉或输入模型。

### 新用户路径

`Start from scratch` 应该引导用户完成：

```text
Create project -> choose template -> show generated files -> run demo -> show report
```

对应现有命令：

```bash
kairos project init my-project --id my-project --template backtest
kairos project doctor
kairos launch start demo-backtest
kairos launch wait demo-backtest
```

### 策略运行路径

`Run or inspect a strategy` 应该列出已有 launch，并提供动作：

```text
demo-backtest  backtest  stopped
btc-sma        paper     running

Actions:
1. Start
2. Status
3. Logs
4. Attach
5. Wait for report
6. Stop
7. Diagnose config
8. Edit launch config
```

对应现有命令：

```bash
kairos launch start <launch-id>
kairos launch status <launch-id>
kairos launch logs <launch-id>
kairos launch attach <launch-id>
kairos launch wait <launch-id>
kairos launch stop <launch-id>
kairos launch diagnose validate <launch-id>
kairos launch edit <launch-id>
```

### 系统运维路径

`Operate the runtime system` 应该先解释它管理的是 workspace runtime，而不是某个策略实例。
可选动作：

```text
1. Show system status
2. Start components
3. Stop components
4. Restart components
5. Show logs
6. Run doctor
7. Repair stale resources
```

对应现有命令：

```bash
kairos system status
kairos system up
kairos system down
kairos system restart
kairos system logs
kairos system doctor
kairos system repair
```

### 便捷查询路径

`Check account, market, order, or trading-instrument data` 应该先问用户要查什么，而不是先暴露
Account、Market、Execution、Reference 这些模块边界：

```text
1. Account balances or positions
2. Market quote, bar, or greeks snapshot
3. 选择 Account 后查看 provider 未完成订单、历史订单或成交记录
4. 市场目录：资产、参与方、交易品种或具体市场
5. Notification destination
```

确认后再映射到现有命令，例如：

```bash
kairos account balances --account-id main
kairos system component market snapshot quote
kairos launch instance component execution status <launch-id> --instance <instance-id> --mode <mode>
kairos reference option-chain
kairos notifications validate
kairos integration earn
```

### 诊断路径

`Diagnose what is wrong` 应该根据 workspace 状态推荐顺序：

1. 找不到 workspace：提示 `kairos project init` 或选择 `--workspace`。
2. 找不到 launch config：提示 `kairos launch init` 或 `project scaffold`。
3. launch 配置无效：执行 `launch diagnose validate`。
4. launch 已启动但策略无输出：先看 `launch status` 和 `launch logs`。
5. 组件或 socket 异常：转到 `system doctor`。
6. 市场快照或订阅异常：转到 `system component market sources` /
   `system component market snapshot`。

交互式诊断的价值不是隐藏命令，而是帮用户选择第一条排障命令。

### 实现边界

- 默认只执行安全命令；涉及 live、下单、转账、删除、repair 的动作必须二次确认。
- 每次执行前显示完整命令。
- 提供 `--dry-run`，只打印计划命令，不执行。
- 提供 `--no-exec` 或 “copy command” 模式，便于用户在执行前审阅动作。
- 不在交互式入口里重新实现业务逻辑；只调用现有 Application 或现有 CLI 命令。
- 交互式入口可以使用 `typer.prompt` 做第一版；如果需要列表选择、刷新状态和键盘导航，再升级到
  Textual。
