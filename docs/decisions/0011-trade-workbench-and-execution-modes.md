# Decision 0011：账户交易工作台与 Execution 操作模式

- Status: Accepted
- Date: 2026-08-23
- Scope: Interactive CLI、Execution CLI、Account 与 Launch 导航

## Context

Interactive CLI 当前从首页进入“交易管理”后，继续并列展示 Account、Execution、Risk 和 Capital。
进入 Execution 后，独立命令、Launch Instance 连接命令、evidence 文件读取和请求 preview 又被平铺在
同一个菜单中。用户必须理解内部模块和运行方式，才能判断一个动作实际操作哪个账户、是否连接正在
运行的 Execution server，以及数据来自交易所还是 runtime current view。

这个结构还存在三项导航问题：

- `/trade-control` 是内部控制面风格的名称，不是自然的用户任务路径；
- 从 `/trade-control` 选择订单后直接跳到 `/order`，父级和账户上下文丢失；
- Launch 连接模式先选择 component，再临时询问 instance id，没有建立稳定的实例上下文。

人工交易首先针对一个明确账户。账户决定 provider binding、credential、交易环境和可用市场；订单动作
仍由 Execution 拥有。运行中策略的 Execution 操作则首先针对一个明确的 Launch Instance，再连接该
实例绑定的 Execution server。两条路径不能互相替代或隐式回退。

## Decision

### `/trade` 是账户优先的产品工作台

Interactive CLI 使用 `/trade` 作为“交易”产品入口，完全替代 `/trade-control`。`/trade` 是
交互层组织用户任务的路径，不创建新的 Trade 业务模块，也不改变 Account、Execution、Risk 和
Capital 的所有权。

进入 `/trade` 后必须先从 Account owner 提供的账户列表中选择账户：

```text
/
/trade
/trade/accounts
/trade/accounts/<account-id>
```

`/trade-control` 同时从路由、文本命令、菜单、帮助、测试和文档中删除，不保留别名、重定向、兼容
提示或 deprecated shim。输入旧路径按未知命令处理。

选中账户后，Interactive Context 保存经过 Account owner 验证的 `AccountId`。页面同时展示账户名称、
provider、产品、环境和连接可用性，使 live、paper 或测试连接在任何高风险动作前都清晰可见。

账户工作台提供以下一级任务：

```text
账户工作台：
1. 账户概览
2. 资产与余额
3. 交易仓位
4. 订单管理
5. 理财与质押
6. 费率与账户等级
7. 资金划转
8. 配置与凭据
s. 切换账户
b. 返回账户列表
```

对应路径是：

```text
/trade/accounts/<account-id>/overview
/trade/accounts/<account-id>/assets
/trade/accounts/<account-id>/positions
/trade/accounts/<account-id>/orders
/trade/accounts/<account-id>/earn
/trade/accounts/<account-id>/fees
/trade/accounts/<account-id>/transfer
/trade/accounts/<account-id>/settings
```

`switch` 是上下文导航，不占用普通业务编号。切换账户时先清除旧账户拥有的 Market、Order 等子选择，
再返回 `/trade/accounts`。

### 账户下的订单管理是 Execution 独立模式

订单管理路径是：

```text
/trade/accounts/<account-id>/orders
```

它调用 Execution standalone CLI，由短生命周期的 CLI 进程使用当前账户的 provider binding 和
credential 直接连接交易所：

```text
Interactive CLI
  -> Execution standalone application
  -> Integration provider connection
  -> exchange
```

独立模式不启动、不发现也不连接 Execution server，不读取 Launch Instance 的 current view，也不借用
Launch Context。命令完成后，本次命令拥有的交易所连接随进程结束。

订单管理菜单提供：

```text
1. 未完成订单
2. 历史订单
3. 成交记录
4. 查询订单
5. 下单
6. 撤单
7. 修改订单
b. 返回账户工作台
```

规范子路径包括：

```text
/trade/accounts/<account-id>/orders/open
/trade/accounts/<account-id>/orders/history
/trade/accounts/<account-id>/orders/fills
/trade/accounts/<account-id>/orders/<order-id>
```

从未完成订单列表选中订单后，应进入具体订单路径，再执行详情、刷新、撤单或修改，不要求用户复制
Order ID。下单、撤单和修改订单必须显式携带当前 `AccountId`，并继续满足 live 环境确认、幂等、凭据
保护和错误映射要求。

账户只有一个交易 segment 时可以自动使用；存在多个 segment 时必须在进入订单工作台时选择，后续命令
显式携带该 segment，不得默认取账户配置中的第一项。

独立查询返回交易所直接查询结果。输出必须标记 `scope=direct-provider`，不得表现为 Execution server
的 current view、journal 或审计状态。Provider 不支持某个动作时，应报告明确的 capability error，
不得回退到 Launch Instance、缓存文件或其他运行时状态。

Execution 不定义“本地工具”或第三种产品模式。Evidence 文件读取和请求 preview 不出现在账户订单
工作台中；如果工程诊断仍需保留此类能力，应由诊断与观测入口明确承载。

### Launch Instance 下的 Execution 是连接模式

运行中策略的 Execution 操作必须先选择具体 Launch Instance，再进入该实例的组件：

```text
/launch
/launch/<launch-id>
/launch/<launch-id>/instances
/launch/<launch-id>/instances/<instance-id>
/launch/<launch-id>/instances/<instance-id>/components
/launch/<launch-id>/instances/<instance-id>/components/execution
```

连接链路是：

```text
Interactive CLI
  -> launch instance resolver
  -> Execution contract/client
  -> running Execution server
  -> provider integration
  -> exchange
```

Interactive Context 在进入组件前保存经过 Launch Registry 验证的 `LaunchId`、mode 和 `InstanceId`。
进入 Execution 后，命令直接继承这些值，不得再次询问 launch id 或 instance id，也不得从 `/trade`
隐式跳入某个运行中 server。

连接模式可以提供与独立模式同名的提交、撤销、修改、单笔查询、未完成订单、历史订单和成交记录，
也可以提供只属于 server runtime 的 status、snapshot、route、lifecycle event、audit、trace、journal
和 reconciliation。输出必须标记 `scope=launch-instance`。

### Instance ID 与 `current`

一个 Launch 可以在不同 mode 下拥有多个历史或同时存在的 instance。稳定身份是：

```text
(mode, launch_id, instance_id)
```

因此规范路径和所有写操作必须携带解析后的具体 `instance-id`。只有一个可用实例时，Interactive CLI
可以自动选择；存在多个实例时必须展示列表；存在多个运行实例时不得猜测目标。

`current` 只允许作为一次性便捷选择器，表示“解析当前唯一运行实例”。解析成功后，Context 和提示符
立即使用真实 InstanceId：

```text
/launch/grid-btc/current
  -> /launch/grid-btc/instances/run-20260823-02
```

`current` 不作为稳定身份传递给后续命令，避免当前实例切换后同一交互会话误操作另一个 server。

### 相同动作与不同作用域

相同订单动作可以出现在两种模式中，但目标、状态来源和一致性保证不同：

| 动作 | `/trade/accounts/<account-id>/orders` | Launch Instance Execution |
| --- | --- | --- |
| 提交、撤销、修改 | CLI 直接调用交易所 | 调用 Execution server control API |
| 单笔、未完成、历史订单 | 直接查询交易所 | 读取 server API 或权威 current view |
| 成交记录 | 直接查询交易所 | 读取 server 管理的成交事实 |
| snapshot、route、event、audit、journal | 不提供 | Execution server runtime 能力 |

模式由用户进入的路径决定，不能根据 `submit`、`open-orders` 或 `history` 等动作名称推断。同一个命令
名称可以存在于两种 facade，但不得共享隐式目标。

### 业务所有权保持不变

账户优先的产品导航不改变业务所有权：

| 用户任务 | 所有者 | 交互层职责 |
| --- | --- | --- |
| 账户概览、余额、仓位、账户侧订单观察事实 | Account | 传递已选 AccountId 并展示结果 |
| 下单、撤单、改单和交易所侧订单生命周期 | Execution | 构造 Execution 命令并展示结果 |
| Provider 连接与外部事实适配 | Integration | 由 owner composition 选择具体连接 |
| Launch、Instance 和组件绑定 | Workspace/System | 解析并验证唯一运行目标 |

Interactive CLI 可以在账户工作台聚合这些用户任务，但不得把 Execution 业务实现移入 Account，也不得
让 Python 聚合层实现交易所协议、订单规则或新的跨模块 facade。

## Navigation behavior

- `back` 或 `b` 只返回真实父路径并清除离开路径所拥有的子选择；
- `home` 或 `/` 返回产品首页并清除全部选择；
- `switch` 或 `s` 从账户工作台返回账户列表并清除当前账户上下文；
- 只有一个账户或 instance 时可以自动选择，但提示符必须显示真实 ID；
- 危险写操作必须展示 account、provider、environment、scope，以及 Launch connected 模式下的具体
  launch 和 instance。

## Delivery scope

本次调整按以下顺序交付：

1. 删除 `/trade-control` 路由并切换到 `/trade`，不保留兼容入口，同时保留完整父路径；
2. 建立 `/trade/accounts/<account-id>` 账户选择和工作台上下文；
3. 建立账户下的 standalone Execution 订单路径；
4. 将现有 connected Execution 命令迁入具体 Launch Instance 的 Execution component；
5. 先实现 standalone 单笔订单、未完成订单、历史订单和成交记录等只读 provider direct 查询；
6. 验证连接、来源标记和错误边界后，再实现 standalone 下单、撤单和修改订单。

Risk 和 Capital 的菜单、standalone/connected 能力及产品路径不在本次范围内，不为统一导航而提前调整。

## Consequences

- 用户进入交易产品后先选择账户，后续 standalone 操作不再重复询问 AccountId。
- `kairos order` 默认且只表示直接连接交易所的 Execution standalone 模式。
- `kairos launch instance component execution` 默认且只表示连接运行中 Execution server 的模式。
- 顶层账户订单菜单不得混入 connected 命令；Launch component 菜单不得回退为交易所直连。
- Interactive CLI 必须先选择 instance，再选择 component，并在路径中保存真实 InstanceId。
- `/trade-control` 立即失效；实现和测试不得保留旧名称或兼容分支。
- `/trade` 是产品工作流，不是新的 Cargo package、业务 owner、manager 或 facade。
- 本 Decision 取代其他文档中 `/trade-control`、Execution standalone 为 evidence/preview-only，以及
  component 先于 instance 选择的产品描述；后续实现和文档以本 Decision 为准。
