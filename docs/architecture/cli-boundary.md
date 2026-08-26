# CLI 边界

本文定义 `kairos` / `kairospy` 的用户心智、业务模块 CLI 的执行模式，以及
`system`、`launch`、业务命令之间的产品边界。先定义用户应该如何理解命令，再定义
Python、Rust crate、contract client、socket、current view 的实现分工。

## 总原则

用户默认只需要理解 `kairos`。顶层命令按用户想完成的事情组织，而不是按 Rust crate
或进程组件组织。

```text
kairos
  project      创建和检查项目
  launch       管一次策略运行
  observe      看运行中系统总览
  data         准备研究和回测数据
  research     固化研究计划和证据

  account      账户独立工具和账户业务能力
  market       行情独立工具和行情业务能力
  order        Execution 的订单独立入口
  reference    标的目录独立工具和查询能力
  risk         风险策略、校验和 dry-run preview 独立入口
  capital      资金计划、校验和 direct action 预留入口

  system       运维当前 workspace runtime
  integration  高级 provider 原生工具
```

业务模块的 Rust CLI，例如 `kairos-market-cli`，仍然是模块 owner 的工程边界。
但它不是普通用户的第一心智。用户先选择 `kairos market`、`kairos system component
market`，还是 `kairos launch instance component market`，系统再决定背后调用哪个模块、
哪个 contract client 或哪个 current view。

核心规则：

- 用户入口按任务命名。
- 模块实现按 owner 归属。
- 当前系统里的 workspace-scoped 组件通过 `system component <name>` 进入。
- 某次策略运行里的 launch-scoped 组件通过 `launch instance component <name>` 进入。
- 顶层业务命令默认是业务模块的独立模式，不代表当前运行中的系统组件。
- 没有被纳入本文边界表的业务模块，不得临时增加顶层 CLI、Python registry 命令或
  Rust binary。新入口必须先更新本文和 `scripts/check/check_cli_boundary.py`。

## 用户模型

### 日常工作流

`project`、`launch`、`observe`、`data`、`research` 是用户工作流命令。

- `project` 管项目目录、模板、`.kairos/` 和 workspace readiness。
- `launch` 管一次策略运行的配置、启动、状态、日志、等待、报告和停止。
- `observe` 是运行中系统的总览入口。如果它只是打印一次状态，应收敛为
  `status` 或 `system status`；只有实时 TUI/观察界面才值得保留为顶层命令。
- `data` 管数据需求、获取、catalog、coverage 和数据 gate。
- `research` 管研究计划锁定、证据发布和可复现结论。若它只包装数据 gate，应降级到
  `data` 下。

### 业务独立入口

`account`、`market`、`order`、`reference`、`risk`、`capital` 是业务模块独立入口。
它们回答的是“我想使用这个业务能力”，不是“当前系统组件是否健康”。

- `kairos market validate` 是行情模块独立命令。
- `kairos market replay` 是行情模块独立命令。
- `kairos market download` 是行情模块独立命令。
- `kairos account balances` 可以是 Account 的直接查询命令。
- `kairos order submit` 可以是 Execution 的直接下单命令。
- `kairos reference query` 可以是标的目录的独立查询。
- `kairos risk preview` 可以是 Risk 的本地 dry-run 评估。
- `kairos capital schema` 可以是 Capital 的本地 request 解释和校验。

这些命令可以访问本地文件、registry、schema、catalog DB、fixture、历史数据或一次性
provider 调用，也可以执行明确的 direct one-shot 业务动作。它们不能暗中连接某个正在
运行的系统级 server，也不能把 direct one-shot 的结果伪装成当前 runtime current view。

### 连接模式组件入口

如果用户要查看或操作当前 workspace 中正在运行的共享 Market 组件，入口必须是：

```text
kairos system component market ...
```

例如：

```text
kairos system component market status
kairos system component market logs
kairos system component market doctor
kairos system component market dependents
```

如果用户要查看或操作某个 launch instance 自己拥有的 Market 组件，入口必须是：

```text
kairos launch instance component market ...
```

例如：

```text
kairos launch instance component market status <launch-id>
kairos launch instance component market sources <launch-id>
kairos launch instance component market snapshot <launch-id> quote
kairos launch instance component market freshness <launch-id>
```

这类命令本质上启用的是业务模块的连接模式：先解析当前要连接的 server，再创建 owner
contract client 或 current-view reader。

因此：

- `kairos market` 启动的是独立模式。
- `kairos system component market` 启动的是 workspace-scoped 连接模式。
- `kairos launch instance component market` 启动的是 launch-scoped 连接模式。
- `kairos market status` 这类名字应避免，因为它不清楚是业务状态、数据源 freshness，
  还是组件进程状态。

独立模式里的业务事实命令必须使用更具体的名字，例如：

```text
kairos market validate
kairos market replay
kairos market download
```

运行时组件状态和当前 server 业务事实必须放在：

```text
kairos system component market status
kairos system component market sources
kairos system component market subscriptions
kairos system component market freshness
kairos launch instance component market status
kairos launch instance component market sources
kairos launch instance component market snapshot
kairos launch instance component market freshness
```

## 业务模块 CLI 的两种模式

每个业务模块 CLI 应显式区分两种执行模式：独立模式和连接模式。独立模式不是“只能离线”；
它也可以包含不依赖 Kairos server 的 direct one-shot 业务能力。

| 模式 | 是否需要正在运行的 module server | 典型实现 | 示例 |
| --- | --- | --- | --- |
| 独立模式 | 不需要 | Rust module CLI、本地文件、本地 DB、provider one-shot、direct action | `market validate`、`account balances`、`order submit --account-id ...`、本地 Account registry 命令 |
| 连接模式 | 需要明确的 server 或当前 current view | contract client、current-view reader、Rust CLI connected mode | `system component market snapshot`、`launch instance component market snapshot`、`launch instance component account balances` |

独立模式和连接模式是工程执行模式，不是同一个命令族里的隐式细节。用户入口也要体现
这个区别：

- 顶层业务命令，例如 `kairos market`，默认走独立模式。
- 当前系统组件命令，例如 `kairos system component market`，走连接模式。
- 当前 launch instance 组件命令，例如 `kairos launch instance component market`，也走连接模式。

独立模式再细分为两类：

- Offline/local：只读写本地 registry、schema、fixture、catalog、历史数据或 dry-run
  preview。
- Direct one-shot：不启动 Kairos module server，但会直接访问 provider 或执行外部动作，
  例如查询交易所余额、直接下单、直接撤单、直接划转。

Direct one-shot 是合法的基础 CLI 能力，但必须满足：

- 命令名、帮助和输出明确显示这是 direct/provider action，不是 system/launch runtime。
- 必须显式选择 account、credential、provider、environment、market、segment 等关键目标。
- live、下单、划转、撤单、资金锁定等动作必须有确认或显式 safety flag。
- 结果只能作为一次性 provider/action result；不能写成 Account/Execution/Risk/Capital 的
  authoritative runtime state。
- 如果需要保持 runtime 一致性、策略协作、事件投递或 current view 更新，应使用连接模式。

### Direct One-shot 的技术边界

Direct one-shot 不等于“CLI 自己连 integration 并解释 provider payload”，也不等于“一定
要把完整 runtime application 组装起来”。它应有独立的 CLI application 边界：

```text
CLI
  -> owner module 的 Cli<Application>
  -> owner module 的 provider query service
  -> integration provider connection
  -> owner module 的 mapping / validation
  -> direct_provider result
```

例如 Account 可以形成：

```text
kairos-account-cli standalone balances
  -> CliAccountApplication
  -> AccountDirectQueryService / AccountSnapshotQueryService
  -> Account-owned integration connection
  -> Account-owned map_snapshot / validation
  -> direct_provider balances result
```

而运行态仍然是：

```text
kairos-account-server / connected component
  -> AccountApplication
  -> AccountRuntime / Actor / Conflux / current-view publisher
  -> Account contract / indexed current views
```

这里有两条边界必须同时成立：

- CLI 可以触发 provider 连接创建，但 provider payload 的业务解释必须留在 owner module。
  例如 `account balances` 不能在 CLI 里直接理解 Binance/OKX/IBKR 的余额结构，而应复用
  Account-owned 的 snapshot/query mapping。
- Direct one-shot 不应默认组装完整 runtime application。普通 `AccountApplication` 这类
  runtime application 通常包含 actor、event loop、Conflux、current-view publication、
  refresh worker、persistence 和 lifecycle。如果命令只需要“拉一次 provider 数据并映射
  成 owner-owned facts”，应进入 `CliAccountApplication` 这类 CLI application。

CLI application 和 connected application 的关系是并列复用，不是上下调用。每个有进程或
CLI 入口的业务模块应收敛到显式入口 facade：

```text
application/
  app.rs                  # AccountApplication / RiskApplication / owner main facade
  cli.rs                  # CliAccountApplication / CliMarketApplication / ...
  connected.rs           # ConnectedAccountApplication / ConnectedMarketApplication / ...
services/
  provider_query.rs       # provider snapshot/direct query 复用逻辑
  integration.rs          # provider payload -> Account facts mapping
  runtime.rs              # actor/runtime/current view 专用逻辑
```

`application/cli.rs` 是 standalone/direct one-shot 的 application facade。
`application/connected.rs` 是 connected/runtime 的 application facade。
`application/app.rs` 是 owner main facade，例如 `AccountApplication`、`RiskApplication`
或 `CapitalApplication`。业务模块 `src/application/` 下不要再新增 `service.rs`；这个名字
会和 `src/services/` 混淆，也会让主应用 facade 与 connected facade 的边界变脏。
旧代码如果已有 `application/conflux.rs` 或 `application/process.rs`，可以分阶段迁移，但
新增入口能力必须进入 `cli.rs` 或 `connected.rs` 的明确 facade，而不能继续散落在 `bin/`。

`CliAccountApplication` 可以依赖 `services/provider_query.rs` 和 `services/integration.rs`，
但不得依赖 runtime actor、Conflux context、current-view publisher 或 contract server。
`ConnectedAccountApplication` 可以复用相同的 provider query/mapping service，并额外承担
runtime state、event、freshness 和 current view 责任。

Account direct provider query 的正确技术落点不是 `bin/`，也不是 Python Typer。它应是
Account owner crate 内的 CLI 专用 application 能力：

```text
kairos account balances --account-id live-main
  -> kairos-account-cli standalone balances
  -> CliAccountApplication
  -> Account-owned provider query service
  -> Account-owned provider payload mapping
  -> direct_provider balances result
```

如果复用现有 async provider composition，必须先把“一次性 compose provider connection、
fetch snapshot、map snapshot、extract balances/positions”的流程抽成 owner-owned service
或 `CliAccountApplication` 私有方法。不能让 standalone CLI 调用 server/conflux facade
来获得一次性结果，因为那会把 direct one-shot 和 runtime lifecycle 重新绑在一起。
在这个 service 存在前，live account 的 `balances/positions/snapshot/open-orders` 只能保持
local paper/simulated registry 语义，或返回明确的 unsupported direct-provider 错误；运行中
Account current view 必须通过 `launch instance component account ...` 读取。当前产品没有
workspace-scoped Account component。

`CliAccountApplication` 明确不做 Conflux 生命周期：

- 不创建 `Conflux`。
- 不注册 Conflux actor。
- 不安装 contract/RPC server。
- 不发布 runtime current view。
- 不维护 runtime freshness、event sequence、generation 或 lifecycle state。
- 不写 Account authoritative runtime state。

它只拥有一次 CLI invocation 内的短生命周期 orchestration：解析已经归一化的 CLI request、
选择 credential/provider/segment、调用 Account-owned direct service、格式化 direct result。
如果一个能力需要 Conflux 生命周期，它就不是 `CliAccountApplication` 的能力，而是
`AccountApplication` 的 connected/runtime 能力。

这个模式可以推广到其他 owner，但名称和职责必须具体：

- `CliMarketApplication` 可以承载 `market once/download/validate` 这类 provider/local
  短路径。
- `CliExecutionApplication` 承载 direct order submit/cancel/replace，并复用
  Execution-owned validation、idempotency 和 provider action service。
- `CliCapitalApplication` 可以承载 direct transfer preview/action，但必须复用
  Capital-owned safety、confirmation 和 provider action service。
- `CliRiskApplication` 更适合 dry-run/preview，不应写 runtime reservation。

Risk/Capital 也必须遵守这个规则，即使当前只先预留能力边界。预留的含义是
“明确未来能力的落点和禁止区”，不是给半成品命令留一个松散入口：

- 只要有顶层 `kairos risk` / `kairos capital`，就必须有 owner Rust CLI 的
  `standalone` mode 和 `application/cli.rs` facade。
- 如果暂时没有可安全落地的 direct action，可以先只实现 `schema`、`doctor`、
  `preview` 这类本地能力，但不能把 runtime control 暂时塞进顶层业务入口。
- `system component risk/capital` 和 `launch instance component risk/capital` 本质上就是
  启用连接模式，必须创建对应 owner contract client 或 current-view reader。
- 预留 facade 可以很小，但检查脚本必须能看到它；后续新增 standalone/direct 能力必须
  先进这个 facade，而不是散落到 `bin/`、`root.py` 或 `launch.py`。
- 预留 connected 能力必须先定义 contract/resource/action 名称；没有 contract 或 typed
  current view 的命令不能先以 generic command、raw JSON-RPC 或 root 私有函数形式出现。
- 文档、检查脚本和 CLI enum 必须一起承认 Risk/Capital 是一等业务 owner。不能因为
  当前功能少，就让它们逃过 standalone/connected 分组和 application facade 规则。

CLI application 不是新的跨业务聚合层。它属于本 owner crate，只服务本 owner 的
standalone/direct 命令；跨 owner 的交互式编排仍属于 `kairospy` 聚合层，并通过 owner
contract 或 owner CLI application 的公开能力组合。

是否组装完整 application，按下表判断：

| 需要的能力 | 是否组装完整 application | 原因 |
| --- | --- | --- |
| 只读本地 registry/schema/catalog | 否 | 直接调用本地 store/query 即可。 |
| 拉一次 provider snapshot/quote/reference page 并输出 | 通常否 | 需要 provider connection 和 owner mapping，不需要 actor/current view/lifecycle。 |
| direct provider action，例如下单/撤单/划转 | 通常否 | 需要 owner validation、confirmation、idempotency 和 provider action service；不应启动 runtime current view。 |
| 需要 Account/Market/Execution/Risk/Capital 当前 authoritative state | 是，但应走 connected runtime | 这不是 standalone direct，而是当前 server/current view 语义。 |
| 需要发布事件、更新 current view、维护 freshness、参与策略协作 | 是，并且必须 connected | 这些是运行态职责，不能由短命令临时承担。 |
| 需要复用复杂 provider fetch orchestration，但不需要 runtime state | 有待 owner 设计判断 | 可以抽出 owner-owned direct query facade；不要为了复用代码而启动不必要的 actor/lifecycle。 |

因此 Account 的 direct 查询目标不是：

```text
kairos account balances
  -> CLI 组装完整 AccountApplication + Conflux
  -> 临时 refresh
  -> 读 current_view
```

更理想的目标是：

```text
kairos account balances
  -> CliAccountApplication
  -> Account provider snapshot service
  -> integration connection
  -> Account map_snapshot / validation
  -> direct_provider balances result
```

如果现有读取逻辑只存在于完整 runtime application 中，重构优先级应是把 provider fetch
和 mapping 提取到 owner module 的 service，再由 `CliAccountApplication` 和
`AccountApplication` 分别复用，而不是让 standalone CLI 依赖 runtime application。这样
既能复用 integration 连接和 Account-owned 映射，也不会把 standalone 短命令变成一个
短生命周期 server。

### 模式判定矩阵

命令放入 `standalone` 还是 `connected`，必须先判断它操作的事实归属和状态来源。
不能只看“有没有启动一个长生命周期 server”，也不能只看“命令是否来自业务 crate”。

| 命令类别 | Standalone | Connected | 说明 |
| --- | --- | --- | --- |
| 本地配置/registry/schema | 是 | 否 | 例如 account registry、credential、schema、fixture。它们写的是 workspace 配置，不是运行中业务事实。 |
| 本地只读 catalog/fixture/history | 是 | 否 | 例如 reference catalog 查询、market replay 文件检查。 |
| direct provider one-shot 查询 | 可以 | 否 | 必须明确 provider、account、credential、environment，输出标记为 direct result。 |
| direct provider one-shot 动作 | 可以，但必须高安全门槛 | 否 | 例如已落地的 direct order submit/cancel/replace，以及未来的 transfer。不能写 runtime current view。 |
| 当前 runtime current view 查询 | 否 | 是 | 即使只是 indexed/read-only，也依赖 launch/workspace runtime scope。 |
| 当前 runtime control | 否 | 是 | refresh、reconcile、pause/resume、subscription mutation 等都必须连接目标 server。 |
| 模拟 runtime 事实写入 | 否 | 是 | fill、settlement、mark-to-market、advance-time、simulated capital mutation 必须进入 paper/simulated server。 |
| authoritative 业务事实写入 | 否 | 是 | Account、Execution、Risk、Capital 的权威运行态写入不能在 standalone 里偷偷完成。 |

因此：

- `standalone` 可以写本地配置，但不能写运行中的 authoritative business state。
- `connected` 可以读 current view，也可以通过 owner contract 修改 runtime state。
- “短命令”不等于 standalone。短命令只有在实现是 offline/local 或 direct one-shot 时才
  能进入 standalone。
- 如果一个命令今天只是产品上想做成短路径，但实现仍依赖 runtime current view 或 runtime
  control，它今天必须留在 connected，并在聚合层提示用户进入 scoped component。

## 能力矩阵

能力矩阵用来区分“用户想做什么”和“这件事由哪种执行模式完成”。同一个业务动词可以
同时存在 standalone 和 connected 两种实现，但它们的语义必须不同：

- standalone 回答“我现在直接做一次/查一次/准备一次”。
- connected 回答“我对当前 workspace 或 launch instance 里的运行中组件做一次 API 操作”。

### 成熟 CLI 参照

Kairos 的 CLI 边界应参考成熟工具的共同做法，而不是只按 crate 或 server 拆命令：

- Kubernetes `kubectl` 以资源和操作组织命令，并通过 kubeconfig/context 决定连接哪个
  cluster；`kubectl config` 另管本地上下文配置。这说明“业务动作”和“连接上下文”要
  分开表达。参见 Kubernetes 的
  [kubectl overview](https://kubernetes.io/docs/reference/kubectl/) 和
  [kubectl config get-contexts](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_config/kubectl_config_get-contexts/)。
- Docker CLI 用 `docker context` 管理本地或远端 daemon 的连接上下文，普通命令再在当前
  context 上操作资源。这说明 `system component` / `launch instance component` 应明确
  表示 runtime context，而不是让 `kairos market status` 这类短命令隐式猜目标。参见
  Docker 的 [contexts guide](https://docs.docker.com/engine/manage-resources/contexts/)。
- AWS CLI 是 service/resource/action 形状，并用 `--profile`、`--region`、`--output`、
  `--query`、pagination 等全局约定提供脚本友好的远端 API 投影。这说明 connected mode
  应尽量贴近 owner contract / REST API，而 standalone 短命令可以更产品化。参见
  [AWS CLI command reference](https://docs.aws.amazon.com/cli/latest/reference/)。
- GitHub CLI 同时提供产品化短命令和 `gh api` 这样的底层 API escape hatch。这说明
  Kairos 可以同时保留用户短路径和高级 contract/API 入口，但两者必须显式区分。参见
  [GitHub CLI manual](https://cli.github.com/manual/) 和
  [`gh api`](https://cli.github.com/manual/gh_api)。

从这些工具抽象出的 Kairos 规则：

- 短路径服务用户任务，名字可以短，但数据来源必须清楚。
- Context/scoped component 服务当前 runtime，必须明确 workspace、launch、instance 或
  provider 目标。
- API 对齐入口服务脚本和自动化，命令结构应稳定、资源化、字段可追踪。
- 高级 escape hatch 可以存在，例如 integration/provider 原生工具，但不能取代 owner
  contract 语义。

### 顶层命令能力矩阵

顶层命令先按用户工作流分类。只有业务模块命令才需要进一步拆成 standalone /
connected；工作流命令可以聚合多个 owner，但不能成为新的 owner。

| 顶层入口 | 产品定位 | 应提供的短路径 | 不应承担的能力 | 实现边界 |
| --- | --- | --- | --- | --- |
| `project` | 创建、检查、解释 workspace/project | `init`、`status`、`doctor`、`scaffold` | 不启动业务 runtime，不写业务事实 | 只操作 workspace/project 文件和 readiness |
| `launch` | 管一次策略运行 | `start`、`status`、`wait`、`report`、`stop`、`logs`、`artifacts`、`instances` | 不提供局部 component restart，不绕过 component contract 写业务事实 | 可以聚合组件状态；component 事实进入 `launch instance component` |
| `launch instance component <name>` | 某次 launch instance 的 connected runtime 入口 | component `status`、snapshot、业务 API 投影 | 不做 workspace-scoped lifecycle，不自动切到其他 instance | 解析 launch/mode/instance 后创建 owner client/current-view reader |
| `observe` | 当前系统/launch 的观察视图 | 实时 TUI、总览、watch | 如果只是一次性状态，应收敛到 `system status` 或 `launch status` | 聚合只读视图，不写 owner state |
| `data` | 研究/回测数据准备 | `list`、`inspect`、`plan`、`execute`、`gate show`、`set list/show` | 不管理运行中 Market subscription，不替代 Reference lifecycle | 面向数据集、文件、coverage plan、gate evidence |
| `research` | 固化研究计划和证据 | `plan lock/show`、`gate publish/show` | 不下载原始数据，不启动 backtest runtime | 面向可复现 evidence 和 trust gate |
| `account` | Account standalone/direct 用户入口 | registry、credential、schema、doctor、direct balance/position query | 不操作当前 runtime current view，不写 simulated runtime facts | 调用 Account standalone CLI 或 provider one-shot |
| `market` | Market standalone/direct 用户入口 | validate、once、replay、download、reference-universe | 不看当前 Market server status/subscription | 调用 Market standalone CLI/provider historical API |
| `order` | Execution 的订单用户入口 | 账户作用域的 direct open-orders/history/order/fills/submit/cancel/replace | 不承载本地 evidence/preview 或 backtest，不隐式操作 launch runtime Execution | runtime 订单事实进入具体 `launch instance component execution`；回测归 `launch`/`data`/`research` |
| `reference` | Reference catalog/query 用户入口 | query、search、show、markets、assets、option-chain | 不控制运行中 provider refresh/pause/resume | 本地 catalog 查询；runtime 控制进 component |
| `risk` | Risk standalone/dry-run 用户入口 | schema、doctor、preview、本地 policy/fixture 检查 | 不写 runtime reservation，不控制 circuit/policy runtime | 调用 Risk owner CLI standalone；运行中风险事实进 scoped component |
| `capital` | Capital standalone/planning 用户入口 | schema、doctor、离线 plan/preview、direct transfer preview 预留 | 不隐式执行 runtime funding objective 或读取 launch current view | 调用 Capital owner CLI standalone；运行中资金事实进 scoped component |
| `system` | 当前 workspace runtime 运维入口 | status、list、logs、doctor、repair、component | 不拥有模块业务语义 | lifecycle 和 scoped component 连接入口 |
| `system component <name>` | workspace-scoped connected runtime 入口 | component status、logs、业务 API 投影、guarded lifecycle | 不自动选 launch instance，不绕过 dependent safety | 解析 workspace target，创建 owner client/current-view reader |
| `integration` | provider 原生/高级工具 | capabilities、transfer、earn、provider diagnostics | 不写 Account/Execution/Risk/Capital authoritative state | 可以是 provider escape hatch，输出必须标记 external/provider result |
| `config` | workspace/profile/manifest 配置解释 | paths、manifest、show、doctor、profile | 不做业务动作 | 只读/修改配置，不写业务事实 |

### Account 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `account list/show` | 读取本地 account registry | 不需要 | Standalone |
| `account register/modify/remove` | 修改本地 account registry | 不需要 | Standalone |
| `account credential-*` | 修改本地 credential store 或检查 provider credential | 不需要 | Standalone |
| `account schema/doctor` | 解释配置、schema、本地诊断 | 可扩展为 runtime doctor | Standalone 为主，runtime doctor 另走 component |
| `account simulate` | 创建 paper/simulated account 配置和初始余额 | 不写运行中 state | Standalone |
| `account balances/positions` | direct provider one-shot 查询；必须标记 direct result，不写 current view | 当前 runtime current view 查询 | 两者都合理，但实现和输出必须区分 |
| `account snapshot/open-orders` | direct provider one-shot 或本地只读快照；不依赖 launch current view | 当前 runtime indexed current-view 查询 | 两者都合理，但不得混淆 |
| `account refresh/reconcile` | 不应存在 | 调用 Account contract，让运行中 server 刷新或对账 | Connected |
| `account fill` | 不应存在 | paper/simulated Account server 的 simulated settlement 写入 | Connected |
| `account mark-to-market/advance-time` | 不应存在 | paper/simulated Account runtime control | Connected |

因此 `kairos account balances` 可以作为 standalone 短路径，但它必须是真正的 direct
provider query 或明确的本地只读结果；如果实现读取的是当前 launch current view，
那就是 connected 能力，应通过 launch instance scope：

```text
kairos launch instance component account balances
```

### Market 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `market validate` | 校验 market descriptor、schema、样本数据 | 不需要 | Standalone |
| `market once` | direct provider one-shot 拉一次行情样本 | 不写 runtime current view | Standalone |
| `market replay` | 从文件重放/验证 market events | 不操作运行中订阅 | Standalone |
| `market download` | 下载研究/回测数据 | 不操作运行中 Market server | Standalone |
| `market reference-universe` | 生成/检查研究 universe | 不操作运行中 Reference/Market server | Standalone |
| `market snapshot/sources/subscriptions/freshness` | 不应读取当前 runtime | 当前 Market server/current view 的事实或控制 | Connected |
| `market subscribe/unsubscribe/recover` | 不应存在 | 修改运行中 Market subscription/control state | Connected |

Market 的成熟工具类比是 Docker/Kubernetes 的 context + resource 操作：`kairos market`
像本地工具和 provider one-shot；`system component market` / `launch instance component
market` 像“对当前 context 中的 daemon/cluster 资源执行 API”。因此状态、订阅和恢复不应
挂在 `kairos market` 下。

### Reference 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `reference query/search/show` | 查询本地 catalog DB 或 fixture | 不需要 | Standalone |
| `reference catalog/assets/markets/events` | 本地 catalog 文件只读查询 | runtime current view 查询时必须 connected | 取决于数据源 |
| `reference status --catalog` | 本地 catalog readiness 诊断 | runtime health/status | Standalone/Connected 分开 |
| `reference providers/logs/doctor` | 不应假装本地 provider runtime | 当前 Reference server 的 provider/runtime 状态 | Connected |
| `reference refresh/sync/publish` | 不应存在 | 触发运行中 Reference server 工作 | Connected |
| `reference coverage add/remove` | 不应写 runtime coverage | 修改运行中 Reference coverage/control state | Connected |
| `reference assets/instruments/listings add` | 若只是编辑本地 fixture，可另建明确 offline 命令 | 当前 catalog owner 写入 | Connected |

Reference 有两类“目录”：本地 catalog DB/current view 和运行中 Reference server。短路径
可以读本地目录；任何 provider refresh、coverage mutation、pause/resume 都是 server
能力，必须 connected。

### Execution / Order 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `order inspect/audit/journal` | 不提供 | 当前 Execution runtime audit/current view | 只属于 connected runtime，不建立本地工具模式。 |
| `order submit/cancel/replace` | direct provider one-shot；用户显式选择 account，其他连接事实由 Account binding 决定，并对 live 动作确认 | 操作当前 launch instance 的 Execution server | 两者可共存，入口上下文决定目标。 |
| `order routes/orders/fills/snapshot` | 不应读取当前 runtime current view | 当前 Execution runtime current view/API | Connected |
| `order backtest` | 不应作为 Order 用户短路径 | 不需要 | 删除用户入口；回测由 `launch`/`data`/`research` 工作流承载 |
| `order fill` | 不应存在 | 不应由 Execution CLI 人工写入；成交事实来自 provider/reconciliation，Account simulated settlement 走 Account | 删除 CLI 入口 |

Order 的成熟工具类比是 GitHub CLI：用户需要 `gh pr create` 这种短路径，也需要 `gh api`
这种 API 对齐入口。`kairos order submit` 可以成为产品短路径，但如果它操作的是运行中
Execution server，就必须有显式 launch instance context；如果它是 standalone，就必须
是真正 provider direct order action，并具备确认、幂等和风险提示。

### Risk 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `risk assess/preview` | 本地 policy + intent 的 dry-run 评估，不占用预算 | 当前 runtime authorization | Standalone/Connected 分开 |
| `risk policy/schema/doctor` | 本地 policy、schema、fixture 诊断 | runtime policy/health | Standalone 为主，runtime 另走 component |
| `risk pre-trade-check/authorize-reserve/release/consume` | 不应改变 runtime budget；standalone 只允许 future local preview | 读取或修改运行中 Risk authorization/reservation state | Connected |
| `risk latest/health` | 不应伪装成本地 runtime snapshot；只有明确 fixture 文件时才可另设 fixture 命令 | 当前 Risk latest view/contract health | 默认 Connected；`status` 保留给 component process status |

Risk 的短路径以 preview/dry-run 为主。任何占用预算、释放预算、改变 circuit 或写入
decision log 的命令都必须 connected，因为它们影响策略协作和后续订单准入。

### Capital 能力矩阵

| 用户能力 | Standalone 语义 | Connected 语义 | 当前归属判断 |
| --- | --- | --- | --- |
| `capital plan/preview` | 本地资金计划、route preview、dry-run | 当前 runtime capital plan/reconcile | Standalone/Connected 分开 |
| `capital transfer` | direct provider transfer；必须显式 provider、account、asset、amount、确认和幂等 id | 通过 Capital runtime 协调资金动作 | 两者可共存；安全门槛不同 |
| `capital availability/demand` | 本地 fixture 或 provider direct query | 当前 Capital current view/API | 取决于数据源 |

Capital 的 direct transfer/action 是最高风险 standalone 能力之一。它可以作为工具存在，
但不应默认从运行中策略资金计划里偷上下文。若动作要影响策略资金约束、funding objective
或 demand reconciliation，就必须 connected。

### 当前实现差距

这张矩阵描述目标边界；当前代码可能还处于过渡状态。落地时按以下差距逐项清理：

| 区域 | 当前风险 | 目标修复 |
| --- | --- | --- |
| `kairos account balances/positions/snapshot/open-orders` | 作为 standalone 短路径是合理的，但实现必须确认是 local registry、direct provider query 还是 runtime current view read。历史实现曾在 standalone 下读取 launch-scoped current view，用户心智错误。 | local paper/simulated registry 查询可 standalone；live direct provider query 仍需 Account-owned provider query facade；runtime current view 查询只通过 `launch instance component account ...`。 |
| `kairos account fill` | 不能 standalone；它写 paper/simulated runtime facts。 | 只保留 connected component 入口，并通过 Account contract `apply_simulated_settlement`；不得保留 direct/local settlement 旁路。 |
| `kairos order submit/cancel/replace` | 已通过 Account binding 建立短生命周期 provider 直连；不得读取 Execution runtime。 | 保持 provider-direct，并继续要求账户、segment、交易凭据、确认和来源标记；runtime 同名动作只在 `launch instance component execution`。 |
| `reference assets/instruments/listings add` | 名字看起来像本地 catalog 编辑，但当前是 owner catalog 写入。 | 保持 connected；若未来需要 fixture 编辑，另建 `reference fixture ...` 或明确 offline 命令。 |
| `risk/capital` | Risk/Capital 都已有 standalone 顶层和 scoped connected。 | 先定义 contract/API，再补 resource/action 命令；不要先加半成品顶层入口。 |
| 聚合层交易/转账 | 用户需要高质量交互式体验，但 Python 不能复制 owner 业务规则。 | 在 `kairospy` 做 preview、证据收集、确认和编排；最终动作调用 owner contract。 |

### 落地状态矩阵

本表是代码改造清单。`目标语义` 来自上面的能力矩阵；`当前状态` 必须随着代码变化更新。
当一个命令还没实现正确语义时，允许先移除错误实现或返回明确错误，但不能继续把错误
语义伪装成已完成能力。

| Owner | 能力 | 目标语义 | 当前状态 | 下一步 |
| --- | --- | --- | --- | --- |
| Account | registry、credential、schema、simulate、connect | Standalone local/direct discovery | 已按 standalone 暴露；`list/browse/show/model switch/register/modify/simulate/remove/credential list/create/add/show/delete/schema/doctor/connect` 已进入 `CliAccountApplication`；`credential-add --check` 和 `connect` 的 provider probe 编排已收敛进 `CliAccountApplication`；bin 侧不再直接读 registry/credential store、拼 `AccountOptions` 或写 binding。 | 继续补真正的 Account-owned direct provider snapshot query service；新增命令前先判断是否写 runtime facts。 |
| Account | `balances/positions/snapshot/open-orders` | Standalone direct provider/local query；Connected runtime current view query | connected current view 已硬切到 Account indexed current view；standalone 已支持 paper/simulated account 的 local registry snapshot/balances/positions/open-orders，输出标记 `source=local_registry`；live direct provider 短路径待实现 | 在 `CliAccountApplication` 继续补 Account-owned provider snapshot service；current view 入口只保留在 component/connected。 |
| Account | `fill/refresh/reconcile` | Connected runtime control | `fill` 已从 standalone enum 移除，direct/local settlement 旁路已删除；connected 走 Account contract；`system/launch component account refresh/reconcile` 已 passthrough 到 Account owner Rust CLI connected | 把 Python 聚合层提示统一指向 `system/launch ... component account ...`；模拟 fill 仍只允许 paper/simulated connected Account。 |
| Market | validate、once、replay、download、reference-universe | Standalone local/direct/data | `validate/reference-universe/once/replay/download` 已进入 `CliMarketApplication`；不读 runtime | 继续删除顶层 runtime status/subscription；connected 保持 contract/current view client。 |
| Market | status、sources、snapshot、freshness、subscribe/unsubscribe、recover/pause-replay/resume-replay | Connected runtime current view/control | `status/sources/snapshot/freshness/subscribe/unsubscribe/recover/pause-replay/resume-replay` 已在 Rust connected enum 对齐；system 与 launch 的 `sources/snapshot/freshness` 都解析明确 socket/view root 后进入 Market owner Rust CLI connected；sources 支持 market/instrument/observation/provider/configured/ready typed filter；snapshot 输出 typed payload，缺失 view 返回结构化错误 | 交互层只从 Reference Market 和 connected source 列表选择，不接受自由输入。 |
| Reference | catalog/query/search/show/option-chain | Standalone local catalog query | 生产路径已通过 `CliReferenceApplication` 读 catalog；Python 顶层入口已收敛为 owner Rust CLI passthrough；`option-chain` 已作为 owner CLI standalone catalog query 落地；顶层已拒绝 `status/doctor/logs/coverage` 等 connected 命令和 `assets/instruments/listings add` catalog mutation；standalone/connected 不再归一化成总 command | 运行态 `health/providers/refresh/pause/resume/stream/coverage` 只保留 component/connected。 |
| Execution / Order | 账户作用域的交易所直连订单查询与动作 | Standalone direct | `open-orders/history/order/fills/submit/cancel/replace` 由 `CliExecutionApplication` 通过 Account 解析出的 binding 和 Integration provider connection 执行，输出标记 `scope=direct-provider`；不连接 Execution server，不读取本地 evidence 或 runtime current view | runtime audit/current view/reconciliation 只在具体 launch instance component；不提供本地工具或 preview 兼容命令。 |
| Risk | policy/schema/doctor/assess preview | Standalone local dry-run | Rust CLI 已移除空 application 的假 `status/snapshot`；`schema/doctor/preview` 已进入 `CliRiskApplication`，可解释和校验本地 typed request file，并用本地 policy 文件做 dry-run `pre_trade_check` | 继续补更完整的本地 policy fixture/workflow 工具；不要把 reservation/authorization 混进 standalone。 |
| Risk | health/latest/limits/reservations/circuits | Connected Risk runtime | 已落地；`status` 仍只表示 component process status；reader 只读取 keyed Risk indexed current families | `decisions` 不能从 current view 伪造，需使用明确的 event/journal query。 |
| Risk | pre-trade-check/authorize-reserve/release/consume/runtime snapshot | Connected Risk runtime | `pre-trade-check/authorize-reserve/release/consume/resize/open-circuit/close-circuit/publish-policy/advance-time` 已在 `kairos-risk-cli connected`、`system component risk`、`launch instance component risk` 对齐 typed contract；system/launch component 的 runtime control 都 passthrough 到 owner Rust CLI connected；`pre-trade-check`、`authorize-reserve` 和 `publish-policy` 都通过 typed request file，不是通用 JSON-RPC | 不要把会读取或修改 runtime budget 的能力加进 standalone；decision 查询必须等 typed current view 或 journal 查询能力。 |
| Capital | health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts | Connected Capital runtime | 已在 `system component capital health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts`、`launch instance component capital health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` 落地；`status` 仍只表示 component process status；Python connected `current` 从 indexed view 输出水位、summary、availabilities 和 alerts；Rust owner CLI 已提供对应 current view/control 读取 | connected 细分 current view 已覆盖主要业务资源。 |
| Capital | schema/doctor/plan/preview/availability | Standalone local/direct query 或 Connected current view/control | `schema/doctor/preview/plan` 已进入 `CliCapitalApplication` 和 `kairos capital` 顶层 standalone passthrough；standalone `preview` 解释单个 typed request，standalone `plan` 汇总本地 objective/demand/availability request bundle，不连接 server、不生成 runtime plan、不执行 transfer；connected `availability --file` 已进入 Rust owner CLI 并调用 Capital contract；`publish-funding-objective/observe-demand/cancel-funding-objective/reconcile-plan` 已按 scoped component 和 Rust owner CLI typed contract request 落地，system/launch component 的 runtime control 都 passthrough 到 owner Rust CLI connected | direct transfer 仍需完整安全确认和 evidence；若要真正 route selection，需要显式 offline fixture 或 connected runtime。 |
| Capital | transfer | Standalone direct provider transfer 或 Connected Capital runtime transfer | 已有 `CliCapitalApplication` 和顶层 `kairos capital`，但尚无 direct transfer 能力 | direct 必须高安全门槛；runtime 必须 owner contract。 |
| Kairospy 聚合层 | interactive order/transfer/workflow | 产品化交互、校验、证据收集、确认、编排 | 需要按 owner contract 重整 | 聚合层只做 orchestrator，不持有 Account/Execution/Risk/Capital 权威状态。 |

### 现有命令审计表

本表按当前源码中的命令清单审计。它不是未来愿望清单，而是指导下一步重构时如何处理
现有入口：保留、迁移、补 direct 实现、或改到 component/API 对齐入口。

#### Account 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `account browse/list/show` | 是 | 是 | 否 | 本地 account registry 查询，保留 standalone。 |
| `account register/modify/remove` | 是 | 是 | 否 | 本地 account registry 写入，保留 standalone；输出应说明它只改配置，不改 runtime facts。 |
| `account credential-list/add/create/show/delete` | 是 | 是 | 否 | 本地 credential store 和 credential inspection，保留 standalone。 |
| `account schemas/schema` | 是 | 是 | 否 | 本地 schema/配置解释，保留 standalone。 |
| `account doctor` | 是 | 是 | 可有 runtime doctor | 默认本地诊断；runtime doctor 应放在 component。 |
| `account simulate` | 是 | 是 | 否 | 创建 paper/simulated account 配置和初始余额，不写运行中 Account runtime。 |
| `account connect` | 是 | 是 | 否 | direct provider credential/account discovery，写本地 binding；不是 runtime connection。 |
| `account balances/positions` | 是 | local paper/simulated registry 已支持；live 目标上是 direct provider query | 是，若读 current view | standalone local 查询输出 `source=local_registry`；live direct provider query 必须先补 Account-owned provider query service。当前系统值必须通过 component。 |
| `account snapshot/open-orders` | 是 | local paper/simulated registry 已支持；live 目标上是 direct provider/local query | 是，若读 current view | connected `open-orders` 读取 Account indexed `observed_orders`；standalone local 查询输出必须标记数据源。 |
| `account fill` | 否 | 否 | 是 | paper/simulated runtime settlement 写入，必须 component + Account contract。 |
| `account refresh/reconcile` | 否 | 否 | 是 | 触发运行中 Account server 工作，必须 component；scoped component 统一调用 Account owner Rust CLI connected，不由 Python 聚合层直接拼 runtime control。 |

Account 的成熟 CLI 类比是 AWS CLI 的 profile + service action：`kairos account balances`
若是 standalone，应像 `aws --profile ... service get...` 一样显式选择 credential/provider
并返回远端一次性结果；若是 connected，应像 `kubectl get ...` 一样对当前 context 的
runtime current view 取值。

#### Market 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `market validate` | 是 | 是 | 否 | 本地 descriptor/schema 校验。 |
| `market reference-universe` | 是 | 是 | 否 | 本地/研究 universe 构造或检查，不操作 runtime。 |
| `market once` | 是 | 是 | 否 | direct provider one-shot market observation，输出必须标记 provider result。 |
| `market replay` | 是 | 是 | 否 | 文件事件重放/验证，适合 data/research 工作流复用。 |
| `market download` | 是 | 是 | 否 | 历史数据下载，属于 standalone/data 能力，不是 Market server subscription。 |
| `market connected status` | 否 | 否 | 是 | 当前 Market server health/status，产品入口应是 component；Rust connected enum 保留 owner contract 能力。 |
| `market connected sources` | 否 | 否 | 是 | 当前 Market server sources/API 投影，产品入口应是 component；Rust connected enum 保留 owner contract 能力。 |
| `market connected recover/pause-replay/resume-replay` | 否 | 否 | 是 | Market owner contract 控制命令；workspace component 已 passthrough 到 Market owner Rust CLI connected。 |
| `market snapshot` | 否，除非 direct quote | 可新增 direct quote 短路径 | 是，若读 runtime | 当前 runtime snapshot 必须 component；未来 direct quote 可进 standalone。 |
| `market subscribe/unsubscribe` | 否 | 否 | 是 | 修改 runtime subscription state，只能 component；已通过 Market owner CLI connected typed contract 承载，不能进入 standalone。 |

Market 的短路径主要是研究/数据工具和 provider one-shot。运行中订阅、freshness、sources、
snapshot 是当前 runtime state，应像 Docker daemon context 一样通过 scoped component。

#### Reference 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `reference snapshot` | 是 | 是，若读本地 catalog snapshot | 是，若读 runtime current view | 当前必须在输出中明确 catalog 来源。 |
| `reference assets list/show` | 是 | 是 | 否 | 本地 catalog 查询，保留 standalone。 |
| `reference catalog exchanges/assets/instruments/listings/markets/show` | 是 | 是 | 否 | 本地 catalog 查询，保留 standalone。 |
| `reference markets list/browse/resolve` | 是 | 是 | 否 | 标的目录短路径，保留 standalone。 |
| `reference events/query/search/show` | 是 | 是 | 否 | 本地 catalog/event 查询，保留 standalone。 |
| `reference connected status/health/doctor` | 否 | 否 | 是 | 当前 Reference server 状态，产品入口应是 component。 |
| `reference providers/logs` | 否 | 否 | 是 | provider runtime/source 状态，必须 component。 |
| `reference refresh/sync/publish` | 否 | 否 | 是 | 触发运行中 Reference server 工作，必须 component。 |
| `reference coverage add/remove` | 否 | 否 | 是 | 修改运行中 coverage/control state，必须 component。 |
| `reference assets/instruments/listings add` | 否，除非明确 fixture edit | 可新增 fixture/offline 编辑命令 | 是 | 当前 owner catalog 写入保持 connected；若做本地 fixture 编辑需另命名。 |

Reference 的短路径价值很高，因为用户经常需要查 market/listing/option chain。它的问题
不在短路径，而在“读本地 catalog”与“控制 Reference server”不能混在同一个语义里。

#### Execution / Order 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `order backtest` | 否 | 否 | 否 | 删除用户短路径；Execution 内部 backtest application/contract 可继续服务 launch/backtest workflow。 |
| `execution connected snapshot` | 否 | 否 | 是 | 当前 Execution current view/API，产品入口是 launch instance component。 |
| `execution connected routes` | 否 | 否 | 是 | 当前 runtime order route candidates/API。 |
| `execution connected active-orders/active-order` | 账户下有 direct 查询短路径 | `order/open-orders/history` 直接查询 provider | 是 | connected 名称表示 indexed operational current state；不复用 provider 历史查询名称。 |
| `execution connected audit` | 仅 fills 有 direct 查询短路径 | `fills` 直接查询 provider | 是 | `audit` 是持久化 JSON-RPC query；已删除的 recent-event/recent-fill 快照查询不成为 indexed view 或 audit 的别名。 |
| `execution connected submit/cancel/replace` | 是 | 是，使用账户 binding 直接调用 provider | 是 | standalone 不启动或发现 server；connected 只操作所选 instance。 |
| `execution connected reconcile/unknown-remote-orders` | 否 | 否 | 是 | runtime reconciliation/control，必须 component/API 对齐；`link-unknown` 尚未进入 ExecutionControlRpc，不能作为半入口暴露。 |
| `execution connected fill` | 否 | 否 | 否 | 删除半入口；Execution 成交事实来自 provider event/reconciliation，不提供人工 fill reporting CLI。Account paper/simulated settlement 走 Account component。 |

`kairos order` 是用户心智入口，不是 Execution runtime 的别名。它只提供账户作用域的 provider
direct `open-orders/history/order/fills/submit/cancel/replace`。Account owner 解析账户、segment、
credential role 和 provider binding，Execution composition 加载凭据并建立短生命周期 Integration
connection。它不提供 evidence 文件读取、preview 或第三种“本地工具”模式。runtime 订单事实与控制
统一通过 `kairos launch instance component execution ...`。

#### Risk 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `risk standalone status/snapshot` | 否 | 否 | 否 | 已删除空 application 伪 snapshot；未来 fixture/local snapshot 必须显式命名数据源。 |
| `risk connected status/snapshot` | 否 | 否 | 否 | `status` 只表示 component process status；`snapshot` 不能作为未定义半入口。已用 `connected health/latest` 表达真实 control/indexed-view 读法。 |
| `risk connected health/latest/limits/reservations/circuits` | 否 | 否 | 是 | `health` 走 Risk control RPC；`latest/limits/reservations/circuits` 读 Risk indexed current families。Python component connected 入口输出 policy_version、limits、active_reservations、circuits 和 summary，不只是存储元数据。 |
| `risk connected pre-trade-check/authorize-reserve/release/consume/resize/open-circuit/close-circuit/publish-policy/advance-time` | 否 | 否 | 是 | 已按 typed Risk contract 暴露；`pre-trade-check`、`authorize-reserve` 和 `publish-policy` 读取 typed request file；system/launch scoped component 统一调用 owner Rust CLI connected，不由 Python 聚合层拼业务 request。 |
| `risk assess/preview` | 可有 preview | 是 | 可有 runtime variant | standalone `preview` 已作为本地 policy + intent dry-run 落地，不占用 runtime budget；运行态评估使用 connected `pre-trade-check`。 |
| `risk policy/schema/doctor` | 是 | 是 | 可有 runtime variant | `schema/doctor` 已作为 standalone 本地工具落地，解释并校验 typed request file；runtime policy 写入使用 connected `publish-policy`。 |

Risk 的首要 CLI 价值是“解释为什么订单会被拒绝”和“预览风险影响”。任何真实 reservation
都必须 connected。

#### Capital 现有命令

| 当前命令 | 用户短路径 | 应在 standalone | 应在 connected | 当前定位和处理 |
| --- | --- | --- | --- | --- |
| `kairos capital schema/doctor/preview/plan` | 是 | 是 | 否 | 已作为 owner CLI standalone passthrough 落地；`schema/doctor` 解释和校验 typed request file，`preview/plan` 本地解释 typed request 或 request bundle，不连接 server、不写 runtime facts。 |
| `system/launch component capital status` | 否 | 否 | 是 | component process status，不是业务状态。 |
| `kairos-capital-cli connected current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` | 否 | 否 | 是 | owner Rust CLI 的连接模式 API 投影；system/launch component 可以复用同一语义。 |
| `system/launch component capital health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` | 否 | 否 | 是 | 已按 owner contract/indexed view 读取落地。 |
| `system/launch component capital publish-funding-objective/observe-demand/cancel-funding-objective/reconcile-plan` | 否 | 否 | 是 | 已按 typed Capital contract `--file` request 暴露；必须 scoped component，不进入顶层 `kairos capital`；system/launch scoped component 统一调用 owner Rust CLI connected，不由 Python 聚合层拼业务 request。 |
| `capital plan/preview/availability` | 可有 | 是，若本地 plan/direct provider query | 是，若读 runtime Capital current view | standalone `preview/plan` 已按本地 typed request 文件落地；connected availability 已通过 component indexed view 读取。 |
| `capital transfer` | 可有，但高风险 | direct transfer 可 standalone，必须确认和幂等 | 通过 Capital runtime 协调则 connected | 需要先定义 owner contract 和安全门槛。 |

Capital 不应因为“资金动作很常用”就先加顶层半入口。它应先明确 direct transfer 与 runtime
capital planning 的差别。

Risk/Capital 的预留判断标准：

- 顶层 `risk` / `capital` 可以先只提供本地 schema、doctor、preview，不要求一开始就能完成
  live action。
- 一旦新增会改变 runtime state 的命令，它必须先进入 connected component；只有实现了
  owner-owned direct service、确认、幂等和输出标记后，才允许作为 standalone direct
  action。
- `Risk` 的 standalone 永远不能占用 reservation；`Capital` 的 standalone 永远不能写
  runtime plan/current view。
- 检查脚本必须把 Risk/Capital 当成一等 owner，而不是文档里的 future work。

#### Workflow / Aggregation 现有命令

| 当前命令 | 用户短路径 | 所属层 | 当前定位和处理 |
| --- | --- | --- | --- |
| `interactive` / `i`、`observe` | 是 | 聚合层 | 人工操作进入同一个 Textual 工作台；`observe --once` 保留机器输出。 |
| `launch start/status/wait/report/stop/logs/artifacts/instances` | 是 | 工作流层 | 管一次策略运行；可以聚合组件状态，但组件事实写入走 owner component。 |
| `launch strategy status/decision/...` | 是 | launch-scoped workflow | 策略层状态/动作，不应替代 Account/Market/Risk/Capital owner。 |
| `launch instance component <name>` | 否 | launch-scoped connected | 对某个 instance 的组件 API 投影。 |
| `system up/down/restart/repair/supervise` | 否 | workspace runtime lifecycle | 需要 dependent safety；不拥有业务语义。 |
| `system component <name>` | 否 | workspace-scoped connected | 对 workspace 组件 API 投影。 |
| `data list/inspect/plan/execute/set/gate` | 是 | 数据工作流 | 准备研究/回测数据，不控制运行中 Market server。 |
| `research plan/gate` | 是 | evidence 工作流 | 固化研究计划和证据，不替代 data acquisition。 |
| `integration ...` | 高级短路径 | provider escape hatch | 输出 provider result，不写 owner authoritative state。 |

聚合层最容易滑向“第二业务 owner”。因此凡是会改变 Account、Execution、Risk、Capital、
Market、Reference 权威事实的动作，聚合层只能做确认、证据收集和调用 owner contract，
不能自己落库或手写协议。

#### Python surface 现有命令补充审计

Python surface 是用户真正看到的 `kairos` 入口。它可以比 Rust module CLI 更产品化，
但每个命令仍必须落到明确层级。

| 当前 surface 命令 | 所属层 | 是否短路径 | 目标定位 |
| --- | --- | --- | --- |
| `project init/status/scaffold/doctor` | project/workspace | 是 | 本地 workspace/project 管理，不触碰业务 runtime。 |
| `config paths/manifest/show/doctor/explain/operations/profile/status` | config | 是 | 本地配置解释和 profile 管理，不触碰业务事实。 |
| `notifications validate/test` | config/tooling | 是 | 通知配置验证和测试，不属于业务 module。 |
| `system inspect/attach/command` | advanced runtime tooling | 否 | 高级调试入口，应避免成为业务操作主路径。 |
| `system up/down/restart/repair/supervise` | workspace lifecycle | 否 | runtime lifecycle；破坏性动作必须做 dependent safety。 |
| `system status/list/logs/doctor` | workspace runtime observe | 否 | workspace runtime 总览，不拥有业务事实。 |
| `system component account ...` | 不存在 | 否 | Account 没有 workspace-scoped system component；standalone 读取走 `kairos account ...`，运行中 current view/control 走 `launch instance component account ...`。 |
| `system component market status/sources/snapshot/freshness/subscribe/unsubscribe/recover/pause-replay/resume-replay/dependents` | Market connected | 否 | 当前 workspace Market API；`sources/snapshot/freshness/subscribe/unsubscribe/recover/pause-replay/resume-replay` 进入 Market owner CLI connected，不能进入 standalone。 |
| `system component reference status/health/providers/catalog/validate/refresh/pause/resume/options-coverage/options-add/options-remove` | Reference connected | 否 | 当前 workspace Reference API；refresh/pause/resume/coverage 不能是 standalone。 |
| `system component risk status/health/latest/limits/reservations/circuits/pre-trade-check/authorize-reserve/release/consume/resize/open-circuit/close-circuit/publish-policy/advance-time` | Risk connected | 否 | `status` 是进程状态；`health/latest/limits/reservations/circuits` 是 Risk owner contract/indexed-view 读取；其他命令是 typed Risk runtime control 或 authorization。 |
| `system component capital status/health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts/publish-funding-objective/observe-demand/cancel-funding-objective/reconcile-plan` | Capital connected | 否 | `status` 是进程状态；`health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` 是 Capital owner contract/indexed-view 读取；其他命令是 typed Capital runtime control。 |
| `launch start/plan/status/report/wait/stop/cleanup` | launch workflow | 是 | 管一次策略运行；不局部重启依赖组件。 |
| `launch strategy status/decision/...` | strategy workflow | 是 | 策略层操作，不能替代模块 owner。 |
| `launch instance component account snapshot/balances/positions/open-orders/refresh/reconcile` | Account connected | 否 | 某次 launch instance 的 Account indexed current view 和 runtime control；`open-orders` 读取 `observed_orders` named database；`refresh/reconcile` 进入 Account owner CLI connected。 |
| `launch instance component market status/sources/snapshot/freshness` | Market connected | 否 | 某次 launch instance 的 Market current view/API；sources/snapshot/freshness 与 system scope 使用相同 Market owner connected 语义。 |
| `launch instance component execution status` | Execution component observe | 否 | 查看某个 instance 的 Execution 组件进程状态。 |
| `launch instance component execution routes/active-orders/active-order/audit/reconcile/unknown-remote-orders/submit/cancel/replace` | Execution connected | 否 | 解析 launch instance 后调用 Execution owner CLI；当前状态读 owner indexed current view，完整订单审计只走持久化 query。迁移删除 `snapshot/recent-fills/recent-order-events`，不保留别名。 |
| `launch instance component reference status/health/catalog` | Reference connected | 否 | 某次 launch instance 绑定的 Reference 事实。 |
| `launch instance component risk status/health/latest/limits/reservations/circuits/pre-trade-check/authorize-reserve/release/consume/resize/open-circuit/close-circuit/publish-policy/advance-time` | Risk connected | 否 | 某次 launch instance 的 Risk 进程状态、owner contract/indexed-view 读取和 typed runtime control/authorization。 |
| `launch instance component capital status/health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts/publish-funding-objective/observe-demand/cancel-funding-objective/reconcile-plan` | Capital connected | 否 | 某次 launch instance 的 Capital 进程状态、owner contract/indexed-view 读取和 typed runtime control。 |
| `launch instance component status` | launch component observe | 否 | instance 组件总览。 |
| `launch targets ...` / `launch diagnose ...` | launch setup/diagnostic | 是 | 本地/配置诊断，不是业务 owner。 |
| `launch instances/attach/logs/artifacts` | launch observe | 是 | launch evidence 和日志读取。 |
| `launch replay events` | launch evidence | 是 | 本地事件/evidence 重放，不操作 runtime。 |
| `launch instance timeline list/export` | launch evidence | 是 | instance timeline evidence。 |
| `data list/inspect/plan/execute/execution` | data workflow | 是 | 数据准备和执行计划；不控制运行中 Market server。 |
| `data set list/show` | data catalog | 是 | 本地 data set 目录。 |
| `data gate show` | data evidence | 是 | 数据 gate 证据读取。 |
| `research plan lock/show` | research evidence | 是 | 固化研究计划，不下载数据。 |
| `research gate publish/show` | research evidence | 是 | 发布/查看研究 gate 证据，不替代 data gate。 |
| `reference health/providers/refresh/pause/resume` | Reference connected | 否 | 只进入 scoped component；顶层不保留 redirect 或 deprecated shim。 |
| `reference catalog/events/markets/assets/exchanges/instruments/listings/option-chain` | Reference standalone | 是 | 本地 catalog/目录查询，保留短路径。 |
| `reference validate` | Reference diagnostic | 是，若本地校验 | 若校验 runtime source，应走 component；需要在 help/output 标明。 |
| `reference stream` | Reference connected/observe | 否 | 若跟随 runtime events，应迁移到 component 或 observe。 |
| `reference options-coverage/options-add/options-remove` | Reference connected | 否 | coverage read/mutation/control 已迁移到 component；顶层只保留拒绝提示。 |
| `account` passthrough | Account standalone/direct | 是 | 默认调用 Account standalone；connected 命令必须拒绝并提示 component。 |
| `market` passthrough | Market standalone/direct | 是 | 默认调用 Market standalone；runtime status/subscription 必须拒绝。 |
| `order` passthrough | Execution standalone/direct | 是 | 只允许账户作用域的 provider direct；runtime order API 走具体 launch instance component。 |
| `integration` passthrough | provider escape hatch | 高级短路径 | provider 原生命令，输出 external/provider result，不写 owner authoritative state。 |

这张表给出迁移优先级：最需要清理的是“顶层业务命令里仍带 connected 语义”的入口，
尤其是 `reference refresh/pause/resume/options-coverage/options-add/options-remove/stream`。这些只进入
`system component reference ...` 或 `launch instance component reference ...`，顶层旧入口直接删除。

### 命名风格：短命令 vs API 对齐

独立模式和连接模式的命名风格不同。

独立模式面向用户日常任务，应优先使用短命令：

```text
kairos account balances
kairos account positions
kairos market download
kairos market replay
kairos order submit
kairos order cancel
kairos reference markets
```

这些短命令可以在内部调用 Rust standalone CLI、provider one-shot、direct action 或本地
DB。它们追求用户心智清楚，但必须在 help/output 中说明是否是 offline/local 或 direct
provider action。

连接模式面向运行中的 server，应尽量和 owner contract / REST API 对齐。命令名应稳定、
资源化、显式，不追求过度缩写：

```text
kairos launch instance component account balances <launch-id> --instance <instance-id>
kairos system component market subscriptions list
kairos system component market subscriptions create
kairos launch instance component execution orders <launch-id> --instance <instance-id> --mode <mode>
kairos launch instance component execution cancel <launch-id> --instance <instance-id> --mode <mode> --order-id <order-id>
kairos launch instance component risk reservations release
kairos launch instance component capital reconcile-plan
```

连接模式的 CLI 是当前 runtime API 的命令行投影。它应该：

- 使用 contract / REST API 的资源名、动作名和字段名。
- 与 REST endpoint 或 contract method 有可追踪映射。
- 避免产品化短别名成为主入口。
- 在 destructive action 上保留明确的资源 id、scope 和确认参数。
- 输出结构尽量贴近 API response，方便脚本和自动化。

如果一个短命令要操作运行中 server，它只能是 connected 命令的显式 alias，并且必须在
help 中指向规范的 resource/action 入口。

### Rust CLI 必须显式分组

业务模块 Rust CLI 如果同时包含独立模式和连接模式，必须用 clap enum 显式分组，不要靠
命令名列表隐式判断。

推荐形状：

```text
kairos-market-cli
  standalone
    validate
    once
    replay
    download
  connected
    snapshot
    sources
    subscriptions
    freshness
```

Rust 结构应类似：

```rust
#[derive(clap::Subcommand)]
enum Command {
    Standalone(StandaloneCommand),
    Connected(ConnectedCommand),
}

#[derive(clap::Subcommand)]
enum StandaloneCommand {
    Validate(ValidateCommand),
    Replay(ReplayCommand),
    Download(DownloadCommand),
}

#[derive(clap::Subcommand)]
enum ConnectedCommand {
    Snapshot(SnapshotCommand),
    Sources(SourcesCommand),
    Subscriptions(SubscriptionsCommand),
    Freshness(FreshnessCommand),
}
```

显式分组不是 parser 外壳，不能在解析后立刻归一化成一个通用业务命令 enum。也就是说，
下面这种形状是禁止的：

```rust
enum Command {
    Standalone(StandaloneCommand),
    Connected(ConnectedCommand),
}

enum ModuleCommand {
    Snapshot,
    Submit(SubmitArgs),
    Cancel(CancelArgs),
}

impl Command {
    fn normalize(self) -> ModuleCommand {
        match self {
            Self::Standalone(command) => command.into(),
            Self::Connected(command) => command.into(),
        }
    }
}

impl From<StandaloneCommand> for ModuleCommand { /* ... */ }
impl From<ConnectedCommand> for ModuleCommand { /* ... */ }
```

原因是 standalone 和 connected 的产品语义、数据来源、参数完整度和输出含义会逐渐分化。
例如：

- `StandaloneCommand::OpenOrders` 表示 direct provider one-shot 查询，只能
  返回 provider 能直接证明的字段，并标记 `source=direct_provider`。
- `ConnectedCommand::Orders` 表示当前 launch/workspace runtime current view 查询，可以包含
  strategy id、intent id、risk reservation、event sequence、generation、current view
  completeness、runtime lifecycle 等运行态字段。
- `StandaloneCommand::Submit` 直接走 provider/order-entry action，并带
  credential、provider、environment、confirmation、idempotency 等 direct action 参数。
- `ConnectedCommand::Submit` 表示调用当前 Execution runtime contract，参数和返回值应贴近
  owner contract / REST API，并携带 launch instance scope。

因此每个业务 CLI 必须有两个独立执行入口：

```rust
async fn run_standalone(
    args: &Cli,
    workspace: &Workspace,
    command: StandaloneCommand,
) -> Result<(), Box<dyn std::error::Error>> {
    // direct provider one-shot
}

async fn run_connected(
    args: &Cli,
    workspace: &Workspace,
    command: ConnectedCommand,
) -> Result<(), Box<dyn std::error::Error>> {
    // contract client / current-view reader / runtime control
}
```

这两个入口的技术含义必须不同：

- `run_standalone` 构造本模块专用的 `CliXXApplication`，例如
  `CliAccountApplication`、`CliMarketApplication`、`CliExecutionApplication`。
  它执行短生命周期命令，直接读取 workspace 配置、本地 evidence、fixture、历史数据，
  或按命令显式创建 provider / integration 连接。它不拥有 server lifecycle，不启动
  Conflux，不读取当前 launch instance 的 indexed current view，不监听当前 instance 的 Aeron，也不把
  runtime current view 当作事实来源。
- `run_connected` 解析当前要连接的 server scope，然后构造 owner contract client、
  typed indexed current-view reader 或 Aeron 订阅。它的事实来源是运行中的 server，因此可以
  返回 generation、event sequence、view completeness、producer incarnation、
  strategy id、risk reservation、runtime lifecycle 等运行态字段。
- Standalone 即使需要 provider，也必须是 direct one-shot：命令显式选择 credential、
  provider、environment、account/market/order scope，并通过 `CliXXApplication` 复用
  owner service 的读取或动作逻辑。
- Connected 即使最终只是查询余额、订单、行情快照，也必须先创建对应的 contract
  client/current-view reader，不得复用 standalone 的 provider 直连实现假装是在看当前系统。

允许共享的是更低层、语义稳定的 helper，例如 decimal formatting、provider connection
construction、owner-owned mapping service、contract request builder、current view decoding。
不允许共享的是顶层 command enum、mode-dispatch 函数、或把两种模式折叠成同一套 request
model 的 adapter。

业务 crate 的 application facade 也必须显式分开：

```text
application/
  cli.rs       # CliXXApplication，standalone/local/direct one-shot
  connected.rs    # ConnectedXXApplication，connected/runtime/process facade
```

迁移期 `connected.rs` 可以先是既有 runtime application 的类型别名，例如
`pub type ConnectedAccountApplication = AccountApplication;`。这仍然有价值：它把“server
runtime 入口”和“standalone CLI 入口”在 owner crate 内命名分开，后续再迁移
`conflux.rs`、`process.rs`、`service.rs` 时不会继续把 CLI 能力塞进 runtime facade。
但 `connected.rs` 不能变成第二个业务实现，也不能被 standalone CLI 调用来绕过
`CliXXApplication`。

如果两个模式短期内看起来字段相同，也必须保持类型分离。字段相同只是当前实现巧合，
不是长期边界。新增命令时必须先回答：

1. Standalone 是否有自己的数据来源和输出语义？
2. Connected 是否需要 runtime scope、contract/view metadata 或更丰富字段？
3. 两者是否应该使用不同 request/result 类型？
4. 是否存在一个 owner-owned service 可以在两个 application 边界下复用，而不是复用 CLI
   command enum？

旧 flat command、alias 和兼容 shim 直接删除；新命令必须进入明确的模式分组。

### 全仓模块边界表

业务模块是否有用户入口，不由 crate 是否存在决定，而由产品心智和 runtime scope 决定。
每个模块都必须出现在下表中；如果状态变更，必须同时更新本文、CLI 实现和边界检查。

| 业务模块 | 顶层用户入口 | Rust CLI | Standalone mode | Connected mode | Workspace component | Launch component | 当前状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Account | `kairos account` | `kairos-account-cli` | registry、credential、schema、离线诊断、模拟 account 配置初始化；paper/simulated local snapshot/balances/positions/open-orders；live direct provider query 只有实现后才能进入 | runtime balances、positions、open orders、refresh/reconcile、paper/simulated fill/settlement | 无 workspace-scoped Account component | `launch instance component account` 可读 instance account | 已接入；standalone 读本地/direct，运行中 current view/control 只进入 launch instance component |
| Market | `kairos market` | `kairos-market-cli` | validate、once、replay、download、历史数据工具 | sources、freshness、snapshot、subscribe/unsubscribe、recover/pause-replay/resume-replay | `system component market` 管共享 Market；freshness、subscription、replay、recovery control 已 passthrough 到 owner CLI connected | `launch instance component market` 管 instance Market | 已接入；顶层只表示独立模式，当前系统组件只表示连接模式 |
| Reference | `kairos reference` | `kairos-reference-cli` | catalog 查询、events、markets、assets、listings、option chain | health、providers、validate、refresh、pause/resume、catalog | `system component reference` 管 workspace Reference | `launch instance component reference` 读 instance 绑定的 Reference | 已接入；Python 顶层入口只做 owner CLI passthrough |
| Execution | `kairos order` | `kairos-execution-cli` | 账户作用域的 provider direct `open-orders/history/order/fills/submit/cancel/replace` | runtime submit、cancel、replace、orders、fills、routes、snapshot、events、audit、trace、journal、reconcile | 当前产品无 workspace-scoped Execution 入口 | `launch instance component execution` 是运行态入口，已 passthrough 到 owner connected CLI | 已接入；`order` 不是独立模块，不提供 evidence/preview 本地工具模式 |
| Risk | `kairos risk`，只表示独立模式 | `kairos-risk-cli` | `schema/doctor/preview` 已落地；未来可补风险策略/fixture workflow | runtime health、policy、pre-trade-check、authorize-reserve、release/consume、latest current view、limits、reservations、circuits | 已接入 `system component risk status/health/latest/limits/reservations/circuits` 和运行态 control；`status` 为进程状态，`health/latest/limits/reservations/circuits` 为业务 runtime 读取 | 已接入 `launch instance component risk status/health/latest/limits/reservations/circuits` 和运行态 control；业务写入动作按 contract 补齐 | Python 顶层入口只做 owner CLI standalone passthrough；`CliRiskApplication` 已承载本地 schema/doctor/preview；禁止 connected 半成品入口；空 application `status/snapshot` 不作为能力 |
| Capital | `kairos capital`，只表示独立模式 | `kairos-capital-cli` | `schema/doctor/preview/plan` 已落地；`preview/plan` 只处理本地 typed request 文件；未来可补 fixture route preview、direct transfer preview 或明确 direct transfer | runtime health、current view、objectives、demands、availability、routes、plans、reservations、operations、alerts、funding objective、capital demand、plan reconcile；owner CLI connected 已按 contract/current view 暴露 | 已接入 `system component capital status/health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` 和运行态 control；`status` 为进程状态，其他为业务 runtime 读取 | 已接入 `launch instance component capital status/health/current/objectives/demands/availabilities/routes/plans/reservations/operations/alerts` 和运行态 control；业务写入动作按 contract 补齐 | Python 顶层入口只做 owner CLI standalone passthrough；`CliCapitalApplication` 已承载本地 schema/doctor/preview/plan；禁止 connected 半成品入口 |

解释：

- `Risk` 和 `Capital` 不应因为是策略依赖就塞进 `launch` 的普通子命令。只要用户要看
  或操作运行中的 Risk/Capital runtime，就必须进入 scoped component。
- `Capital` 已有 Rust CLI，当前承载 standalone `schema/doctor/preview/plan` 和 connected
  `current/objectives/demands/availabilities/plans/alerts`。未来新增能力时，必须先定义它
  属于 offline/local、direct one-shot，还是 connected。
- `Risk` 和 `Capital` 即使暂时没有完整用户能力，也必须有 owner crate 内的
  `application/cli.rs` 作为预留边界。预留 facade 可以为空，但它表达的是未来
  standalone/direct 能力的落点，不允许把半成品能力散落到 `bin/`、`root.py` 或
  `launch.py`。
- `kairos risk` 和 `kairos capital` 已是独立模式顶层入口；运行中策略实例的风险/资金事实
  仍然属于 `launch instance component <name>`。
- 不得在 Python 中先做未登记的业务半入口，也不得在 `root.py` 里加
  private registry 绕过本文。

## 连接模式规则

连接模式必须显式指定或解析当前要连接的 server，然后创建对应 owner 的 contract
client 或 current-view reader。

连接命令不能因为某个 socket 刚好存在，就静默连接到它。

连接目标 selector 可以来自命令参数，也可以来自明确的 workspace 默认值。根据 runtime
scope，selector 可以包括：

- `--workspace`
- `--mode`
- `--launch-id`
- `--instance-id`
- `--component`
- `--socket-name`
- `--socket`，仅用于高级调试和测试

连接模式的流程必须是：

1. 解析 connected command 和 connection selector。
2. 解析目标 runtime scope、socket、view root。
3. 创建 owner contract client 或 typed current-view reader。
4. 通过 owner boundary 执行业务命令。
5. 如果 selector 找不到唯一目标，报明确的 `target server not found` 或
   `ambiguous target`。

连接模式不得绕过 owner contract client 手写 raw JSON 请求。

## Runtime Scope 和依赖安全

runtime 生命周期有 scope。生命周期 owner 不等于可以随时 restart。

Market 是最容易混淆的例子。它有三个产品身份：

- 业务能力：行情模块能 validate、replay、download、解释 market data 语义。
- launch 依赖：某次策略运行需要一个 Market runtime。
- workspace 服务：一个共享 Market runtime 可能服务多个 launch 和人工查询。

每个 runtime 必须有 scope：

| Runtime scope | 生命周期 owner | 使用者 | 生命周期入口 |
| --- | --- | --- | --- |
| Workspace-scoped runtime | `system` | 多个 launch、人工查询、共享服务 | `kairos system component <name> ...`，且必须过依赖安全检查 |
| Launch-scoped runtime | `launch` | 单个 launch instance、策略和人工协作 | `kairos launch instance component <name> ...` 连接查看；破坏性 lifecycle 仍以整个 launch 为单位 |

破坏性生命周期操作包括：

- restart
- stop / down
- repair，尤其是删除 socket、health file、lock、checkpoint、本地 runtime state
- recreate / reset

非破坏性操作包括：

- status
- logs
- doctor
- describe
- dependents

`system` 可以拥有 workspace-scoped Market 的生命周期，但不能无条件 restart 它。执行
破坏性操作前必须检查依赖图：

- 哪些 launch 依赖这个 runtime。
- 这些 launch 是否 running、paused、stopping、completed。
- runtime 是 workspace-scoped 还是 launch-scoped。
- 命令是否声明了明确的影响策略。

默认安全策略：只要有 active dependent launches，拒绝破坏性 lifecycle 操作。拒绝信息
必须解释影响范围并给出安全命令。

示例：

```text
market restart refused

Market is used by running launches:
- mean-reversion / paper / default
- option-maker / live / default

Suggested actions:
- kairos launch stop mean-reversion
- kairos launch stop option-maker
- kairos system component market restart --after-dependents-stopped
```

影响策略必须显式，例如：

- 等 dependent launches 全部停止后再 restart。
- 停止指定 dependent launches 后再 restart。
- 只要有 live dependent 就拒绝。
- 未来有 rolling recovery 且能证明 launch 一致性不被破坏时，才允许不停服恢复。

不要添加宽泛的 `--force` 绕过依赖安全。任何强制选项都必须命名对 dependent launches
的影响。

`launch instance component <name>` 可以让人工连接到某次策略运行里的组件，查看状态、
日志、snapshot、subscriptions，或调用 owner contract 提供的非破坏性业务命令。这样策略
和人工可以围绕同一个 launch instance 的组件协作。

但 `launch` 不应暴露 `launch market restart` 这类局部依赖重启。launch 管的是一次策略
运行的一致性。若依赖不健康，应由 `launch recover` 保持一致性，或要求用户 stop/start
整个 launch。

## 各命令族规则

### `market`

`kairos market` 是 Market 的独立入口。它不表示当前系统中运行的 Market server。

适合放在 `kairos market` 下的命令：

- `validate`
- `once`
- `replay`
- `download`
- 本地 descriptor、schema、fixture、historical data 操作

不适合放在 `kairos market` 下的命令：

- 当前系统 Market 组件 status
- 当前 launch instance Market 组件 status
- 当前系统 Market 组件 logs
- 当前系统 Market 组件 restart / stop / repair
- 当前系统 Market 组件 dependents

这些必须放在对应 scope 的 component 入口：

```text
kairos system component market ...
kairos launch instance component market ...
```

如果要读取当前 server 的业务事实，例如当前 subscriptions、sources、freshness、
snapshot，也应通过对应 component 入口的连接模式进入，先解析目标 server，再创建
`MarketControlClient` 或 `MarketViewAccess`。

### `account`

`kairos account` 是 Account 的独立入口，例如 account registry、credential 维护、本地
配置检查、schema、离线诊断，以及明确的 direct provider 查询。

适合放在 `kairos account` 下的基础短路径：

- `credential-*` / `register` / `modify`：维护本地 Account 配置。
- `doctor` / `schema`：本地诊断和配置解释。
- `simulate`：初始化一个 paper/simulated account 配置，不写运行中 Account facts。

未来可以新增 `balances` / `positions` 这类 direct provider one-shot 短命令，但前提是
实现直接用 account registry、credential 和 provider 查询一次，并且输出标记为 direct
provider result。当前如果命令读取 launch/workspace current view，它属于 connected。

这些 direct 命令不要求启动 Account server，但必须明确输出来源是 direct provider
result，不是当前 runtime current view。

`fill` 不是 Account standalone 命令。它表示 paper/simulated Account runtime 的
settlement/fill 事实写入，必须通过 Account contract 连接当前目标 server。它的入口应是：

```text
kairos launch instance component account fill ...
```

当前 launch instance 级 Account server 的 balances、positions、open orders、runtime health
等连接命令，应放在：

```text
kairos launch instance component account ...
```

连接模式必须创建 Account owner 的 contract client 或 current-view reader。

### `order`

`kairos order` 是 Execution 模块的 standalone mode，也是面向用户的订单独立入口。
它不是独立的 Order module。调用者必须显式选择 Account；Account owner 返回不含 secret 的
provider binding，Execution composition 再从 workspace credential store 加载凭据并建立一次性
provider connection。当前命令为 `open-orders/history/order/fills/submit/cancel/replace`，结果标记
`scope=direct-provider`。命令不启动、不发现也不连接 Execution server，不读取 runtime current view，
也不保留 evidence/preview 本地工具命令。

`order backtest` 不应存在。回测是 launch/data/research 工作流，不是 Order 用户入口。
Execution 内部的 backtest application/contract 可以继续服务 launch/backtest runtime，
但不通过 `kairos order` 暴露。

`kairos order submit/cancel/replace` 可以作为短路径存在，但必须在产品语义上区分：

- direct one-shot：不启动 Execution server，直接通过账户解析出的 provider/order-entry 能力执行。
  用户显式选择 account；credential、provider 和 environment 由 Account owner binding 决定，
  live action 必须确认。它返回一次性 direct action result。
- connected runtime：操作当前 launch instance 的 Execution server，必须通过明确的
  launch instance selector 创建 Execution contract client。

connected runtime 入口应是：

```text
kairos launch instance component execution ...
```

`kairos order submit` 只表示 direct one-shot，不作为 connected runtime 的别名。运行态提交只进入
具体 `launch instance component execution`，并要求或解析唯一的 mode、launch 和 instance 三元组。

当前产品模型里 Execution 是 launch instance 组件，不是 workspace 级共享组件。
因此 `kairos order` 默认表示 Execution 的 standalone/direct order-facing mode；人工与
策略共同操作运行中 Execution 时，应进入对应 launch instance 的 component 入口。

### `reference`

`kairos reference` 是 Reference 的独立入口，例如本地 catalog 查询、schema、fixture、
repair 前诊断。

当前系统级或 launch instance 级 Reference server 的 health、providers、catalog
current view、pause/resume 等连接命令，应放在：

```text
kairos system component reference ...
kairos launch instance component reference ...
```

连接模式必须先解析目标 Reference server 或 catalog，再创建
`ReferenceClient`。如果 workspace 没有运行中的 Reference server，而命令要求连接模式，
必须报 connected target missing，不能 fallback 到无关本地状态。

### `risk`

`Risk` owns budgets、policies、reservations、risk decisions 和 circuit state。它是策略
运行的关键依赖；顶层 `kairos risk` 只承载本地 schema、doctor 和 dry-run preview。

当前规则：

- `kairos risk` 顶层用户入口只能表示独立模式或 direct preview。
- `kairos-risk-cli` 如果存在，必须显式拆分 `standalone` / `connected`。
- 独立模式允许本地 policy、fixture、snapshot、schema、diagnostic，以及不占用 runtime
  reservation 的 direct assess preview。
- 连接模式必须解析唯一 Risk server，然后创建 Risk owner contract client 或
  Risk current-view reader。

运行态入口应是：

```text
kairos system component risk ...
kairos launch instance component risk ...
```

适合的连接命令包括：

- health
- latest
- limits
- reservations
- circuits
- policies
- decisions
- circuit
- pre-trade-check / authorize-reserve / release / consume / advance-time

其中 `status` 不是 Risk 业务命令，而是 `system/launch component risk status` 的进程
状态检查。业务健康读数使用 `health`，当前 indexed 总览使用 `latest`，细分资源读取使用
`limits`、`reservations` 和 `circuits`。这样用户可以区分“组件是否活着”和“Risk owner 当前
发布了什么业务事实”。

当前 Risk indexed databases 包含 policies、limit usage、allocations、reservations 和 circuits；没有
decision current-value family。因此 `decisions` 不能作为 current view 的别名或半入口先暴露。若要落地
decision 查询，必须先新增 Risk owner 的 typed decision current view、journal 查询能力，或
明确从 Aeron/event store 读取的连接模式边界。

这些命令不能散落在 `kairos launch risk ...`、`kairos order ...` 或 `root.py` 的私有
registry 里。

### `capital`

`Capital` owns strategy-scoped capital objectives、demands、availability、routes 和
plans。它比 Account/Risk 更接近策略运行上下文；顶层 `kairos capital` 只承载本地 schema
和 doctor，不是运行中资金状态或资金动作入口。

当前规则：

- 当前已有 `kairos-capital-cli`。它必须显式拆分 `standalone` / `connected`：
  `standalone` 只服务顶层 `kairos capital` 的本地短路径；`connected` 是 Capital
  runtime contract/current view 的 owner CLI 投影，可被 `system/launch component capital`
  复用。
- 当前已有 `kairos capital` 顶层入口，但它只表示 standalone。
- `kairos capital schema/doctor/preview/plan` 只能解释、校验和汇总本地 typed request file，
  不连接 Capital server，也不读取 runtime indexed view。
- 未来新增 Capital 基础 CLI 时，必须先定义清楚它属于 offline/local、direct preview、
  direct transfer/action，还是 connected runtime。
- 独立模式可以包含离线资金计划、fixture、schema、route preview、availability preview、
  transfer preview 等不依赖 Capital server 的能力。
- direct transfer/action 可以存在，但必须显式选择 account、route、credential、
  environment，并对 live action 做确认；它返回 direct action result，不能伪装成
  runtime Capital plan/current view。
- 运行态 Capital 操作必须通过 scoped component，创建 Capital owner contract client
  或 current-view reader。

运行态入口应是：

```text
kairos system component capital ...
kairos launch instance component capital ...
```

已落地的连接读命令包括：

- health
- current
- objectives
- demands
- availabilities
- routes
- plans
- reservations
- operations
- alerts

其中 `current` 是当前 Capital current view 的总览；`availabilities` 和 `alerts` 是同一
current view 上的细分资源查询；`objectives`、`demands` 和 `plans` 是运行态资金意图、需求和
计划的资源化 current view 读取；`routes`、`reservations` 和 `operations` 是运行态资金路径、
资金占用和执行步骤的资源化 current view 读取。这些命令必须保留在 `connected` 资源化命令下，
并让 `system/launch component capital` 显式调用这个连接语义。

其中 `status` 不是 Capital 业务命令，而是 `system/launch component capital status` 的进程
状态检查。业务健康读数使用 `health`，当前 indexed-view 读数使用 `current`、
`availabilities` 和 `alerts`。

已落地的连接写命令包括：

- publish-funding-objective
- observe-demand
- cancel-funding-objective
- reconcile-plan

仍待设计安全门槛的连接写命令包括：

- direct transfer / external funding action
- plan authorization / submission action

如果某个 Capital runtime 只存在于 launch instance 中，则 `system component capital`
只能显示无 workspace target 或解释该 scope 不存在，不能自动寻找某个 launch instance。

### `system`

`system` 是当前 workspace runtime 的入口。

`system component <name>` 表示连接到当前系统中的某个组件 server。它本质上是业务模块
CLI 的 workspace-scoped 连接模式，但产品入口属于 system，因为用户关心的是“当前系统里的
组件”。

`system` 可以做：

- component status
- component logs
- component doctor
- component dependents
- guarded restart / repair / stop
- 创建 owner contract client 读取当前 server 的业务事实

`system` 不拥有模块业务语义。它只负责定位当前 server、创建 owner client、执行依赖安全
检查和展示当前 runtime 视角。

### `launch`

`launch` 管一次策略运行，不管单个 shared component 的局部 lifecycle。

`launch` 可以：

- start / stop 整个 launch
- status / logs / wait / report
- 解释依赖是否 ready
- recover 整个 launch，前提是保持一致性
- `launch instance component <name>`：连接到该 launch instance 拥有或绑定的组件，
  读取状态、日志和业务事实，或调用非破坏性 contract command

`launch` 不应提供：

- `launch market restart`
- `launch account restart`
- `launch execution restart`
- 任何局部依赖 restart

## kairospy 聚合层

`kairospy` 是用户产品层和交互层，不只是 Rust CLI 的薄 passthrough。它可以把多个 owner
的基础能力组织成一个可理解、可确认、可回退的用户工作流。典型场景包括：

- 用户交互式下单。
- 用户发起资金划转或资金调度。
- 用户在下单前查看 Account、Market、Reference、Risk、Capital、Execution 的组合证据。
- 用户需要确认 live action 的影响范围、账户、策略、标的、资金占用和风险结果。

这类命令不应该强行塞进单个业务模块的 Rust CLI。Rust module CLI 和 contract client
提供 owner-owned 的基础能力；`kairospy` 负责产品级编排、提示、确认和展示。

### 聚合层可以做什么

`kairospy` 可以：

- 解析用户意图，并把它拆成多个 owner contract 调用。
- 从 Reference 查询标的、listing、market、option chain 等目录事实。
- 从 Account 查询余额、仓位、账户权限、segment、credential 状态。
- 从 Market 查询当前 quote、bar、greeks、freshness 和订阅状态。
- 从 Risk 请求 pre-trade check、reserve、release、circuit 状态。
- 从 Capital 查询 availability、funding objective、demand、plan。
- 从 Execution 提交、撤销、替换订单，或查询 order/fill/audit。
- 在执行前做交互式确认，例如 live account、跨账户转移、资金锁定、风险拒绝、
  stale market data、Reference ambiguous listing、Capital shortfall。
- 将多个 owner 返回的证据组合成一个用户可读的 preview / plan / confirmation。

例如，一个交互式下单流程可以是：

```text
kairospy interactive order
  -> Reference resolve market/listing
  -> Account read balances/positions/permissions
  -> Market read quote/freshness
  -> Risk assess/reserve
  -> Capital availability/plan if needed
  -> 用户确认
  -> Execution submit
  -> Account/Execution/Risk/Capital follow-up facts
```

一个资金转移流程可以是：

```text
kairospy interactive transfer
  -> Account read balances/segments
  -> Capital availability / objective / route
  -> Risk assess transfer impact when applicable
  -> 用户确认
  -> owner contract command
  -> follow-up current view check
```

### 聚合层不能做什么

`kairospy` 不能因为承担交互式体验，就变成第二套业务 owner。它不得：

- 绕过 owner contract 直接改 Account、Risk、Capital、Execution、Market、Reference 的
  durable state。
- 在 Python 中复制 owner 的最终业务判定，例如订单准入、风险保留、资金计划授权、
  account mutation、reference lifecycle。
- 用 raw JSON 自定义协议替代 owner contract client。
- 因为聚合方便，把运行态命令挂回 `kairos order submit`、`kairos capital ...` 或
  `root.py` private registry。
- 在一个聚合命令里偷偷选择“刚好存在”的 socket；仍然必须解析明确的 runtime scope。

聚合层做的是 product orchestration，不是 domain ownership。最终状态变化必须由对应
owner contract 或 owner application 产生，并由对应 current view / event 作为证据。

### 聚合命令的入口命名

聚合命令应该按用户工作流命名，而不是按底层模块命名。优先入口包括：

- `kairos interactive`（短别名 `kairos i`）
- `kairos observe`（交互模式复用运行中心，`--once` 只输出当前活动实例、项目共享服务和支撑进程）
- `kairos launch attach`（复用 Workbench screen stack）
- 未来可加入 `kairos trade`、`kairos transfer`、`kairos workflow` 等产品入口

如果未来加入 `kairos trade` 或 `kairos transfer`，它们是聚合工作流入口，不是新的
Execution、Capital 或 Account owner。文档和检查脚本必须登记这些入口属于
`workflow_aggregate`，并要求它们只能调用 owner contract client / current-view reader。

## Python 适配器规则

### 独立模式 passthrough

独立模式 adapter 只允许：

- 解析 module binary。
- 注入 `--workspace`。
- 注入或保留 `--output` / `--format`。
- 转发 argv。
- 返回 stdout、stderr、exit code。
- 仅在 Python 内部组合命令需要时解析 JSON。

独立模式 adapter 不允许：

- 改写业务参数语义。
- 构造模块核心 command DTO。
- 在 Python 中重写 Rust CLI 子命令。
- 直接写模块 owned durable state。

### 连接模式 adapter

连接模式 adapter 必须：

- 解析 connection selector。
- 定位唯一 server、socket、view root。
- 创建 owner contract client 或 current-view reader。
- 对破坏性 lifecycle 操作做 dependent launch safety gate。
- 在无目标或多目标时清晰失败。

连接模式 adapter 不应放在 `kairospy/surface/cli/commands/market.py` 这类业务独立入口里，
而应归到 scoped component 的实现路径，例如 `system component <name>` 或
`launch instance component <name>`。

### 禁止散落入口

Python CLI 只能有三类业务入口：

- `surface/cli/commands/<business>.py`：顶层业务独立入口。
- `surface/cli/commands/system/`：workspace-scoped component
  连接入口。
- `surface/cli/commands/launch/`：launch-scoped component 连接入口。

不允许再出现这些形态：

- `root.py` 中的 private account/market/order command registry。
- `root.py` 中直接调用 Execution CLI 的 `order place/cancel/replace` 运行态入口。
- `system account ...`、`system component account ...` 这类伪 workspace Account 入口。
- `launch market restart`、`launch risk restart`、`launch capital restart` 这类局部
  runtime lifecycle 命令。
- 未在全仓模块边界表登记的其他顶层业务入口。

旧入口不保留兼容 shim、hidden alias 或 deprecated redirect。入口调整必须同步删除旧路由、帮助、
测试与文档描述；旧输入按未知或不支持命令处理。

## 建议目录

Python 用户入口：

```text
kairospy/surface/cli/commands/
  market.py              # 独立模式，passthrough 到 kairos-market-cli standalone
  account.py             # 独立模式
  order.py               # Execution standalone mode 的用户友好入口
  reference.py           # 独立模式
  system.py              # system component <name> workspace-scoped 连接模式
  launch.py              # launch instance component <name> launch-scoped 连接模式
```

Rust 模块 CLI：

```text
crates/modules/<module>/src/bin/kairos-<module>-cli.rs
  Command::Standalone(...)
  Command::Connected(...)
```

命名含义：

- `application/<module>/cli.py`：独立模式 subprocess adapter。
- `infrastructure/contracts/<module>`：连接模式 contract client。
- `surface/cli/commands/system.py`：当前系统组件连接入口。
- `surface/cli/commands/launch/`：launch instance 组件连接入口。
- `surface/cli/commands/<module>.py`：业务模块独立入口。

## 修复计划

### Step 1: 收敛用户 help

Help 文案先回答用户问题：

- `kairos market`：行情模块独立工具。
- `kairos system component market`：当前系统级 Market server。
- `kairos launch instance component market`：某次策略运行里的 Market server。
- `kairos launch`：一次策略运行。
- `kairos integration`：高级 provider 原生工具。

不要在普通 help 中要求用户理解 Rust crate 或 socket。

### Step 2: 业务模块 CLI 显式拆模式

以 Market 为起点：

- 在 `kairos-market-cli` 中增加 `StandaloneCommand` clap enum。
- 在 `kairos-market-cli` 中增加 `ConnectedCommand` clap enum。
- 将 `validate`、`once`、`replay`、`download` 放入独立模式。
- 将需要连接当前 server 的命令放入连接模式。
- 旧 flat command、alias 和 shim 直接删除。

其他业务模块按同样模式收敛。

### Step 3: Python 入口拆开

- `kairos market ...` 只走独立模式 passthrough。
- `kairos system component market ...` 走 workspace-scoped 连接模式 adapter。
- `kairos launch instance component market ...` 走 launch-scoped 连接模式 adapter。
- `kairos launch ...` 管 launch 整体一致性；局部组件连接只允许非破坏性操作。

### Step 4: 增加边界测试

测试应断言：

- `kairos market validate/replay/download` 不连接当前系统 server。
- `kairos system component market status` 必须解析目标 Market server。
- `kairos launch instance component market status` 必须解析目标 launch instance Market
  server。
- 连接命令在无目标 server 时清晰失败。
- 连接命令在多个候选 server 时清晰失败。
- 连接命令创建 owner contract client 或 current-view reader。
- component lifecycle 命令不出现在 `kairos market` 下。
- destructive `system` 命令在有 active dependent launch 时默认拒绝。
- `launch` 不暴露局部 dependency restart。

### Step 5: 增加 architecture checks

新增并维护：

```text
python3 scripts/check/check_cli_boundary.py
```

检查项：

- 同时包含独立和连接模式的业务模块 Rust CLI 必须有显式 clap enum 分组。
- Account、Market、Reference、Execution、Risk、Capital 都必须有 owner crate 内的
  `application/cli.rs`。即使暂时只预留，也要定义对应的 `CliXXApplication`，避免
  standalone/direct 能力继续在 `bin/` 中生长。
- Account、Market、Reference、Execution、Risk、Capital 都必须有 owner crate 内的
  `application/connected.rs`。迁移期可以先定义 `ConnectedXXApplication` 类型别名，但不能缺少
  connected/runtime facade 的显式命名。
- Account、Market、Reference、Execution、Risk、Capital 的 standalone 命令必须能在
  owner Rust CLI 中看到对 `CliXXApplication` 功能函数的直接调用。检查脚本不只检查
  enum 分组，也要防止 `run_standalone` 重新把业务读取、provider direct query 或本地
  evidence 解析写回 `bin/`。
- 独立模式命令不得创建 contract client。
- 连接模式命令必须解析唯一目标 server 后再创建 owner client。
- 聚合工作流命令可以调用多个 owner contract client，但必须登记为 `workflow_aggregate`，
  且不得直接写 owner durable state。
- connected mode 的命名必须能映射到 owner contract / REST API 的 resource/action；
  standalone mode 才允许产品短命令作为主入口。
- component status/logs/restart/repair/stop/dependents 必须在
  `system component <name>` 或 `launch instance component <name>` 下。
- Python 业务独立入口不得导入 runtime component lifecycle adapter。
- Python 连接入口不得手写 raw JSON 绕过 owner contract client。
- 全仓模块边界表中的每个模块必须被检查脚本识别。
- 有 Rust CLI 且包含两种模式的模块必须有 `StandaloneCommand` 和 `ConnectedCommand`。
- Rust CLI 不得再定义 `AccountCommand`、`ReferenceCommand`、`ExecutionCommand`、
  `RiskCommand`、`CapitalCommand` 这类跨模式总命令；每个 mode 必须直接 dispatch 到
  自己的实现。
- 无 Rust CLI 的模块必须显式登记为 `no_cli`，并检查没有对应 `kairos-<module>-cli.rs`
  或 Python 顶层入口。
- Risk/Capital 必须被纳入检查，不能停留在文档例外。
- Risk/Capital 的预留不是空目录占位，至少必须满足：
  - owner Rust CLI 有显式 `StandaloneCommand` / `ConnectedCommand` 分组；
- 顶层 `kairos risk` / `kairos capital` 只 passthrough 到 owner CLI standalone；
- Risk/Capital 的 direct 能力只能先预留为本地 schema、doctor、preview、plan 或 fixture
  workflow。涉及运行中 reservation、policy、funding objective、capital demand、plan reconcile、
  transfer execution 的命令必须留在 owner connected CLI 或 scoped component；未来若提供
  standalone direct action，也必须显式标记 direct provider source、幂等键、确认和 evidence，
  且不能写 runtime indexed view/actor state；
  - `CliRiskApplication` 至少承载 `schema/doctor/preview`；
  - `CliCapitalApplication` 至少承载 `schema/doctor/preview/plan`；
  - `ConnectedRiskApplication` / `ConnectedCapitalApplication` 必须显式命名 runtime facade；
  - connected resource/action 必须出现在 `system component ...` 或
    `launch instance component ...`，并能映射到 owner contract 或 typed current view。
- Reference 的 connected component 入口也必须被检查脚本保护。Workspace scope 至少覆盖
  `health/providers/catalog/validate/refresh/pause/resume/options-coverage/options-add/options-remove`；
  launch scope 至少覆盖 `health/catalog`，并且这些命令不能回流到顶层
  `kairos reference`。
- Reference owner Rust CLI 的 connected mode 必须显式保留
  `status/providers/doctor/logs/coverage/refresh/sync/publish/assets/instruments/listings`。
  `assets/instruments/listings add` 这类 catalog mutation 必须只在 connected owner command
  下出现，并通过 `ReferenceControlRpcClient`，不能作为顶层 standalone catalog 查询或
  Python 私有入口实现。
- Market 的 connected component 入口必须区分 workspace scope 和 launch scope。
  Workspace scope 至少覆盖 `status/sources/snapshot/subscribe/unsubscribe/recover/pause-replay/resume-replay`，
  因为 workspace Market 可以管理共享订阅和恢复；launch scope 至少覆盖 `status/snapshot`，
  表示读取某个策略实例绑定的 Market 视图，不提供局部依赖订阅控制或重启。
- Market owner Rust CLI 的 connected mode 必须显式保留
  `status/sources/snapshot/recover/pause-replay/resume-replay`。这些是连接态 owner
  command，不能回流到 `kairos market` 顶层 standalone，也不能被折叠成通用 command。
- Account 的 connected component 入口必须覆盖 workspace 和 launch 两个 scope。
  Workspace scope 至少覆盖 `status/snapshot/balances/positions/open-orders/refresh/reconcile`；
  launch scope 至少覆盖 `snapshot/balances/positions/open-orders/refresh/reconcile`。
  其中 `open-orders` 必须读取 Account observed-orders current view，不能退回 unsupported
  半入口或复用 generic account snapshot。
- Account owner Rust CLI 的 connected mode 必须显式保留
  `fill/snapshot/balances/positions/open-orders/refresh/reconcile`。其中
  `snapshot/balances/positions/open-orders` 是 indexed current-view 读取，
  `fill/refresh/reconcile` 是 typed Account control RPC；检查脚本必须能看到这两个分支，
  不能把它们折叠成通用 command。
- Execution 没有 workspace-scoped 用户入口；运行态订单事实和动作必须只通过
  `launch instance component execution ...`。检查脚本至少保护
  `routes/active-orders/active-order/audit/reconcile/unknown-remote-orders/submit/cancel/replace`，
  且继续禁止 `link-unknown` 和人工 `fill` 半入口。
- Execution owner Rust CLI 的 connected mode 也必须保护同一组真实
  contract/current view command：查询类读取 typed indexed current view，动作类调用
  `ExecutionControlRpcClient`。`fill`、`link-unknown`、`dry-run` 和未进入 owner contract 的
  半参数不得出现。已删除的 `snapshot/recent-fills/recent-order-events` 不加入兼容别名。
- `root.py` 不得保留 private business command registry 或绕过 scoped component 的
  order/account/market/reference/risk/capital 运行态命令。

## 验收标准

边界清晰的标准：

- 用户能区分 `kairos market` 是独立工具，`kairos system component market` 是当前系统
  组件，`kairos launch instance component market` 是某次策略运行里的组件。
- Rust 业务模块 CLI 显式分为独立模式和连接模式。
- 连接模式必须指定或解析唯一 server，并创建 owner contract client。
- 连接模式 CLI 与 owner contract / REST API resource/action 对齐；独立模式 CLI 使用面向
  用户任务的短命令。
- `system` 的破坏性 lifecycle 操作默认拒绝 active dependent launches。
- `launch` 不做局部组件 restart。
- Python 不成为第二套业务模块 application。
- `kairospy` 可以作为产品聚合层实现交互式下单、转移和确认流程，但这些流程必须通过
  owner contract / current view 取证和执行，不能拥有最终业务状态。
- Account、Market、Reference、Execution、Risk、Capital 都在全仓模块边界表和检查脚本
  中有明确状态。
- 这些 owner 的 standalone/direct 扩展点都落在各自 `application/cli.rs` 的
  `CliXXApplication`，不是落在 binary 或 Python 聚合层。
- `root.py` 不再是业务 CLI 的第二棵树；它只承载 project/config/system/notifications
  等非业务独立入口，或承载已登记的 scoped component 入口。
