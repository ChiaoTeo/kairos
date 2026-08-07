# Account、Execution 与 Workspace 设计

状态：架构决策与迁移路线

日期：2026-08-07

本文记录 Kairos 完成 Rust workspace 全量迁移后，账户、执行、交易所接入和工作区的边界。它是后续实现的约束；不保留 legacy 运行时或配置兼容层。

## 1. 总体结论

原 kairos-platform 不应继续作为业务组合层。它应收敛并改名为 kairos-workspace，只提供通用工作区、进程、控制通道和生命周期基础设施。kairos-workspace 作为库存在，不承载业务二进制；二进制入口归属对应的业务 crate，系统级组合入口归属 kairospy/system。

依赖方向必须是业务模块依赖基础设施：

~~~text
kairos-account   ─┐
kairos-execution ─┼──> kairos-workspace ──> kairos-network
kairos-market    ─┤             │
kairos-risk      ─┘             └──────────> kairos-transport
kairos-reference ─┘

kairos-account   ─┐
kairos-execution ─┼──> kairos-integration
kairos-market    ─┤
kairos-reference ─┘
~~~

kairos-integration 和 kairos-workspace 都不能依赖任何业务模块。跨多个业务模块的最终组合由 kairospy/application/system 负责。当前不提前引入统一 trading runtime，先让每个 Rust 业务模块能够独立运行、测试和被 CLI 调用。

## 2. 模块职责

### 2.1 kairos-workspace

负责：

- workspace manifest 和 identity；
- 配置、状态、运行目录、日志、数据和 reference 目录解析；
- process、launch instance、socket、health 等通用资源路径；
- 通用进程启动、停止、健康检查和 control channel；
- 通用文件、SQLite、transport 和 binary resolution 能力；
- 路径安全校验和 workspace 初始化。

不能包含：

- AccountRegistry、账户 credential、账户 lease；
- account/execution/market/risk application；
- provider 选择和交易所连接组装；
- 账户、订单、intent、成交或风险业务规则；
- 账户、市场、执行、风险或 reference CLI/server 的业务入口；
- 通用 control/snapshot 二进制。系统级控制入口由 kairospy/system 提供。

### 2.2 kairos-integration

负责：

- HTTP/REST、WebSocket、认证、签名、重试和限流；
- Connection 生命周期；
- provider gateway 和 provider payload 解析；
- Binance、OKX 等交易所的账户、订单、资金和 Earn endpoint；
- provider-neutral integration DTO；
- order entry、account read、stream、transfer connection。

不能依赖：

- kairos-account、kairos-execution；
- kairos-risk、kairos-market、kairos-reference；
- 任何业务 domain、Actor、application service 或业务 persistence。

Integration DTO 只能表达外部连接事实，例如 BalanceFact、PositionFact、OpenOrderFact、AccountSnapshotFact、OrderEntryEvent。它们不能直接使用 account domain 或 execution domain 类型。

### 2.3 kairos-account

Account 是账户状态和账户业务事实的唯一拥有者，负责：

- account identity 和 account segment；
- balance、collateral、position；
- open order 的账户视图；
- snapshot、freshness、stale 和 reconcile；
- intent journal；
- 账户侧 order fact 和 fill 对账户状态的影响；
- market profile、fee schedule、margin mode、position mode；
- 状态持久化、快照发布和查询。

Account application 通过自己的 protocol 接收外部能力。具体 integration connection 在 account composition 中注入，并在边界转换为 account domain 类型。

### 2.4 kairos-execution

Execution 是外部订单执行生命周期的唯一拥有者，负责：

- submit、cancel、replace；
- 外部订单状态；
- submitting、accepted、partially filled、filled、rejected、expired、unknown、failed 等生命周期；
- execution event history 和 trace；
- execution audit；
- live、paper、backtest、simulation 执行实现。

Execution 不拥有账户内部状态，也不直接修改 account domain。它通过 integration 的 OrderEntryConnection 发送订单，并把执行事件交给 account application 或更高层 system composition。

### 2.5 kairos-reference

Reference 是目录和市场参考数据的业务状态拥有者，负责：

- asset、instrument、listing、market、venue 等 reference domain；
- provider catalog 的归一化和 reconcile；
- reference lifecycle event；
- SQLite/catalog persistence；
- reference snapshot 和查询；
- reference 的 CLI/server 入口。

Reference 依赖 kairos-integration 获取交易所目录和市场元数据。Integration 只返回自己的 provider-neutral reference DTO，例如 ReferenceCatalogPayload；Reference 在 application/composition 边界把它转换为自己的 ProviderCatalog 和 ReferenceCatalog。Integration 不得拥有 Reference 的实体、生命周期或持久化状态。

### 2.6 kairospy

后续负责：

- workspace 和 launch 配置；
- account、market、risk、execution server 的组合；
- 启停、health、restart、diagnostics；
- launch instance 和 strategy process；
- CLI/TUI/研究层；
- 多业务模块之间的 command/event 连接。

它不拥有 Rust 业务状态，也不实现账户、风险或订单状态机。

### 2.7 二进制归属

二进制必须和它所拥有的业务状态或生命周期归属在同一个 crate：

| 二进制 | 归属 |
| --- | --- |
| kairos-account-cli/server | kairos-account |
| kairos-execution-cli/server | kairos-execution |
| kairos-market-cli/server | kairos-market |
| kairos-risk-cli/server | kairos-risk |
| kairos-reference-cli/server | kairos-reference |
| system/supervisor/control | kairospy/application/system，或未来独立的 system crate |

kairos-integration、kairos-workspace、kairos-network 和 kairos-transport 默认只提供 library。它们不因为拥有某个连接或通用资源类型，就创建面向终端用户的业务 binary。

## 3. Account 与 Execution 的协作边界

Account 和 Execution 不共享可变订单对象：

~~~text
Account record Intent
    → plan business Order
    → Risk reservation
    → Execution submit
    → Execution event
    → Account apply order/fill fact
~~~

| 状态 | 拥有者 |
| --- | --- |
| Intent | Account |
| 账户侧 Order fact | Account |
| 外部提交状态 | Execution |
| 外部订单生命周期 | Execution |
| Fill 事件 | Execution 产生，Account 应用到账户状态 |
| 余额、持仓、权益 | Account |
| Execution audit | Execution |
| Risk reservation | Risk |

早期实现可以在 application 层使用同步调用；后续跨进程时使用版本化 Command/Event/Query 协议。不要因为跨模块协作而创建新的全局 Coordinator、Manager 或统一 runtime。

## 4. CLI 与 Server

每个业务模块都提供两种入口：

~~~text
<module>-cli     一次性进程，调用 application，输出 stdio 后退出
<module>-server  长期进程，持有 Actor、连接和运行时状态
~~~

CLI 不依赖 server 才能运行。两者共享 application request/result、composition/factory、workspace resource resolver 以及错误模型。

CLI 不得导入 services、直接创建 vendor client、直接读取业务数据库、自己实现业务流程，或依赖另一个 server 才能完成本地验证。

Account CLI 需要覆盖：

~~~text
account list/show/register/remove
credential list/create/delete
schema/schemas/doctor
trade-lock list/acquire/release/status
connect
snapshot/balances/positions/open-orders/orders/intents
market-profile/market-profiles
refresh/reconcile/submit-intent
~~~

Execution CLI 需要覆盖：

~~~text
snapshot/orders/open-orders/history/events/trace/status/inspect
submit/cancel/replace/intent-execute
~~~

## 5. Legacy 能力映射

Legacy account 不只是余额查询，还包括：

| Legacy 能力 | 新模块归属 |
| --- | --- |
| account configuration、schema、doctor | Account application |
| credential 配置和校验 | workspace persistence + Account application |
| account directory/provisioning/login | Account application |
| snapshot、balance、position、open orders | Account Actor/application |
| private stream | Integration connection，由 Account Actor 消费 |
| reconciliation | Account Actor/application |
| market profile、fee schedule | Account application + integration profile connection |
| account model、margin/position mode | Account domain |
| account lock/trade authority | workspace persistence，由 account/system application 使用 |
| intent journal、order/fill projection | Account domain/application |
| paper/backtest account | Account composition/runtime |
| live/paper/backtest execution | Execution composition/runtime |
| execution audit | Execution |
| launch lifecycle | kairospy/system + workspace resources |

Legacy integration 已覆盖 Binance Spot、Futures、Options、Equity、Funding、Transfer、private stream，以及 OKX Spot、Swap/Futures、Options。CCXT private account/execution 暂不迁移，因为 Rust 没有可直接使用的 CCXT 库；CCXT 可以保留在 market/reference provider 能力中。

## 6. Workspace 设计

当前只有 workspace.toml、root、state/run/log 路径的设计仍然不完整。Legacy workspace 还承担 project、配置、凭据、launch、数据和操作审计的统一资源边界。

推荐布局：

~~~text
project/
└── .kairos/
    ├── kairos.toml
    ├── accounts/
    ├── credentials/
    ├── profiles/
    ├── state/
    │   ├── operations.jsonl
    │   ├── launch-index.json
    │   └── account-locks/
    ├── launches/
    ├── data/
    ├── reference/
    └── orders/journals/
~~~

运行实例：

~~~text
.kairos/launches/<mode>/<launch_id>/
├── current.json
└── instances/<instance_id>/
    ├── state.json
    ├── command.json
    ├── run.sqlite
    ├── launch.log
    └── strategy.sock
~~~

### Workspace 通用 API

kairos-workspace 只提供通用 API：

~~~text
Workspace::open
Workspace::init
Workspace::config_root
Workspace::state_root
Workspace::run_root
Workspace::logs_root
Workspace::data_root
Workspace::reference_root
Workspace::process_dir(name)
Workspace::launch_dir(id)
Workspace::control_socket(name)
Workspace::health_file(name)
~~~

它不提供 account_registry()、execution_audit() 这种业务语义 API。业务模块基于通用 workspace root 定义自己的资源路径。

| 资源 | 所有者 |
| --- | --- |
| workspace manifest | kairos-workspace |
| process socket/health | kairos-workspace |
| launch index/instance | kairospy/system，使用 workspace 路径 |
| account config/credential | Account application |
| account state/snapshot/journal | Account |
| account lease | workspace persistence，由 account/system application 使用 |
| execution state/audit | Execution |
| market data | Market/data application |
| reference database | Reference |
| operations journal | Workspace/system application |

## 7. 迁移顺序

### 阶段一：清理 crate 边界

1. 将 kairos-platform 改名为 kairos-workspace。
2. 删除 workspace 对所有业务 crate 的依赖。
3. 删除原 platform/src/account.rs 中的账户注册、连接组合和业务代码。
4. 将 account CLI/server/composition 移入 kairos-account。
5. 将 market consumer 等市场业务入口移回 kairos-market。
6. 将 control/snapshot 等系统入口移到 kairospy/system 或独立 system crate。
7. 删除 integration 对所有业务 crate 的依赖。

### 阶段二：补齐 workspace

1. 定义 workspace manifest 和初始化流程。
2. 补齐 config、credentials、profiles、state、run、logs、data、reference、launches。
3. 增加 launch index、operation journal、launch instance resource。
4. 保持 workspace API 只使用通用路径和生命周期概念。

### 阶段三：完成 integration 中性 DTO

1. 定义 account snapshot、balance、position、open order、fill、stream event 的 integration DTO。
2. gateway 只生成 integration DTO。
3. account composition 将 DTO 转为 account domain。
4. execution composition 将 order entry event 转为 execution domain。
5. provider payload 不得跨出 gateway normalizer。

### 阶段四：先打通一个完整 provider

优先完成 Binance Spot：

~~~text
account CLI
  → account application
  → account composition
  → Binance connection
  → provider gateway
  → integration DTO
  → account adapter
  → Account Actor
  → state/snapshot/stdout
~~~

链路稳定后，再扩展 Binance Futures、Options、Funding、Equity，以及 OKX Spot、Swap/Futures、Options。

### 阶段五：接入 execution

1. 独立验证 submit/cancel/replace。
2. 接入 order entry connection。
3. 接入 execution audit 和 trace。
4. 将 execution event 接回 Account。
5. 再接 risk reservation 和 intent execution。

### 阶段六：由 kairospy/system 组合

最后才由 system 组合 account、execution、market、risk，负责启动顺序、health、停止、重启和策略进程。此阶段之前不创建统一 trading runtime。

## 8. 验收约束

~~~text
kairos-workspace 不依赖业务 crate
kairos-integration 不依赖业务 crate
跨模块只进入 application API
CLI 不进入 services
provider payload 不离开 gateway
Account 是账户状态唯一拥有者
Execution 是外部执行生命周期唯一拥有者
CLI 可脱离 server 一次性运行
server 可独立运行并持有 Actor
workspace 只负责资源和生命周期，不负责业务规则
~~~

验证命令：

~~~bash
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
rg -n 'kairos_(account|execution|risk|market|reference)' crates/kairos-workspace crates/kairos-integration
rg -n '::services' crates kairospy tests
~~~

核心目标不是机械搬运 legacy 文件，而是在保留 legacy 能力范围的同时，建立单向依赖、唯一状态拥有者和清晰的 workspace 资源边界。

## 9. Launch Instance 是运行时隔离边界

`Launch` 是配置和逻辑定义，`Launch Instance` 是一次真实运行。所有有状态的运行时业务对象都必须属于一个 instance，不允许在 workspace 中创建一个供多个 launch 共享的全局 market、execution 或 account。

一个 instance 的组成是：

~~~text
Launch Instance
├── Market Instance
├── Strategy Instance
├── Execution Instance
├── Account Instance
└── Instance Data Environment
~~~

因此，下列状态必须按 instance 隔离：

- market event sequence、订阅关系、回放位置和 checkpoint；
- strategy process、参数和运行状态；
- execution process、订单命名空间、open orders、fills、执行序列和 session；
- account balance、position、equity、PnL、账户事件和 reconcile 状态；
- 日志、运行产物、projection、恢复信息和生命周期状态。

不允许采用下面的全局共享结构：

~~~text
Workspace
└── Global Execution
    ├── Launch A
    ├── Launch B
    └── Launch C
~~~

正确结构是每个 instance 独立拥有自己的业务进程和数据目录：

~~~text
Workspace
├── backtest/A/instance/default/{market,execution,account}
├── paper/B/instance/default/{market,execution,account}
└── live/C/instance/default/{market,execution,account}
~~~

Workspace 可以复用配置定义、credentials 和通用连接能力，但不能复用这些 instance 的可变运行状态。

### 9.1 Account Definition 与 Account Instance

账户需要区分定义和运行时实例：

~~~text
Account Definition
    │ workspace 级别的配置和 credentials 引用
    ▼
Account Instance
    │ 属于一个 Launch Instance
    ├── state
    ├── snapshot
    ├── journal
    ├── checkpoint
    └── reconciliation state
~~~

`Account Definition` 可以被多个配置引用，但 `Account Instance` 不共享余额、持仓、订单和同步状态。

对 backtest 和 paper：

- account instance 必须是虚拟且隔离的；
- 由配置提供初始资金、币种和账户模式；
- 不读取或写入真实账户状态；
- 如果未来允许以真实账户快照作为 paper 初始状态，只能在启动时复制一次，不能持续共享真实账户 projection。

对 live：

- account instance 可以引用 workspace 中的真实 account definition；
- 本地仍然拥有自己的 account projection、checkpoint 和 reconciliation 状态；
- 底层可以映射到同一个真实交易所账户；
- 多个 live instance 不得同时控制同一个真实账户，必须通过 instance-level lease 或 exclusive ownership 防止并发写入。

### 9.2 Execution Instance 与 Account Instance 的绑定

`Execution Instance` 和 `Account Instance` 同属于一个 launch instance，但职责不合并：

- Execution 拥有订单提交、撤销、替换、外部订单状态、成交和执行审计；
- Account 拥有余额、持仓、权益、PnL、账户侧订单视图和账户事实；
- Execution 产生 fill/order event；
- Account application 应用这些事实并更新账户状态；
- 两者不共享可变订单对象，也不复制对方的业务状态。

运行时关系为：

~~~text
Strategy
    │ intent
    ▼
Execution Instance
    │ order/fill event
    ▼
Account Instance
    │ account state event
    ▼
Strategy / monitor
~~~

跨模块的调用和事件连接由 application/composition 负责，不把 account 对 execution 的依赖藏进 account domain，也不在 workspace 或 integration 中实现这段业务编排。

## 10. Market、Execution、Account 的运行模式

backtest、paper、live 是三种不同的运行语义，不只是同一 server 的三个参数。

| 模式 | Market 数据 | Execution | Account | 生命周期 |
| --- | --- | --- | --- | --- |
| backtest | 历史数据回放 | simulated | 隔离的虚拟账户 | 回放完成后退出 |
| paper | 实时数据或实时 replay | simulated | 隔离的虚拟账户 | 持续运行直到 stop |
| live | 实时数据 | live | 真实账户的本地 projection | 持续运行直到 stop |

统一约束：

- backtest 不能使用真实账户和 live execution；
- paper 不能产生真实订单；
- live 才能使用真实 account definition 和 live order entry；
- simulated execution 可以被 backtest 和 paper 复用，但它们的 state 和 projection 必须独立；
- paper 可以复用 live 的 market integration connection 类型，但不能复用 live market instance、execution state 或 account state。

### 10.1 Market Instance 必须独立运行

回测不能依赖一个正在运行的 live market server，也不能把历史事件推送到共享的全局事件总线。每次回测应创建一个属于自己的 market process：

~~~text
backtest instance
    └── kairos-market --provider replay --instance <instance-id>

paper instance
    └── kairos-market --provider live --instance <instance-id>

live instance
    └── kairos-market --provider live --instance <instance-id>
~~~

这里的 fork 是运行时实例 fork，不是复制 market 代码。每个 market instance 都拥有自己的：

- 事件时钟；
- 订阅关系；
- event sequence；
- replay position 或连接 checkpoint；
- 事件日志；
- 进程生命周期。

回测 market 读取历史事件，按顺序发布，回放完成后结束。paper 的 market 可以使用真实行情或带实时速度的 replay。live 的 market 独立连接真实行情。即使 paper 和 live 使用同一个 provider，它们也不能共享订阅状态、checkpoint 或内部事件流。

### 10.2 Strategy 发起订阅，Market 提供数据

数据订阅意图属于 Strategy，但 Strategy 不直接连接交易所、不读取历史文件，也不依赖具体 provider。Strategy 通过 Market application 表达自己需要的 symbol、timeframe 和 channel：

~~~text
Strategy
    → subscribe(symbol, timeframe, channels)
    → Market application
    → Market provider/replay source
    → Market events
    → Strategy
~~~

职责边界是：

- Strategy 决定自己需要订阅什么数据；
- Market application 管理订阅生命周期和事件分发；
- Market composition 选择具体的数据 source；
- integration connection 负责真实 provider 的连接和协议细节；
- Replay Market 负责历史文件读取、过滤和虚拟时间推进。

同一个 Strategy 可以运行在不同模式中，而不改变订阅逻辑：

~~~text
Strategy subscription
        │
        ▼
Market application
        │
        ├── Backtest: historical replay source
        ├── Paper: exchange stream or replay source
        └── Live: exchange stream source
~~~

Strategy 不应通过检查当前时间、环境变量或 provider 名称自行决定回测逻辑。Market 也不应在运行时比较历史事件时间和机器 wall-clock time，然后自行 fork 进程。运行模式和数据 source 必须由 Launch 在启动时根据 TOML 明确选择。

### 10.3 Replay Market 是显式选择的独立实例

当 launch mode 是 backtest 时，Launch 应直接创建 `ReplayMarketInstance`；当 mode 是 paper 或 live 时，Launch 创建相应的 live market instance，paper 也可以显式配置为 replay source：

~~~text
mode = backtest
    → ReplayMarketInstance

mode = paper
    → LiveMarketInstance 或 ReplayMarketInstance

mode = live
    → LiveMarketInstance
~~~

这里的 fork 是运行时 instance/process 的 fork，不是由一个已经运行的 live market 在发现时间不一致后自我分叉，也不是复制 market 代码。Live market、paper market 和 backtest replay market 都有独立的订阅、事件序列、checkpoint 和生命周期。

Backtest 的历史窗口由配置确定：

~~~toml
[backtest.market]
events = "data/btc-usdt-1m.jsonl"
start = "2024-01-01T00:00:00Z"
end = "2024-02-01T00:00:00Z"

[backtest.replay]
speed = 0.0       # 尽快完成，不等待历史时间间隔
# speed = 1.0     # 按历史时间间隔运行
# speed = 10.0    # 十倍速运行
~~~

Replay Market 的职责是：

1. 加载历史数据；
2. 按 `start` 和 `end` 过滤事件；
3. 等待 Strategy 完成有效订阅；
4. 按 event time 顺序发布事件；
5. 使用 virtual clock 推进回测时间，而不是使用机器当前时间；
6. 在数据结束或达到 `end` 时发布 replay completed；
7. flush 自己的 projection 并退出。

## 10.4 Replay Start Barrier

由于订阅由 Strategy 发起，Replay Market 不能在 Strategy 启动前就把全部历史事件发布完。回测需要一个明确的 `replay start barrier`：

~~~text
Replay Market 启动但暂停回放
    → Strategy 启动并注册订阅
    → Market 确认订阅
    → Launch 或 Market 发布 replay-ready
    → Replay Market 开始推进历史时间
~~~

如果 Strategy、Market、Execution 是独立进程，`replay-ready` 必须通过明确的 application/control/event 协议表达，不能依赖进程启动顺序或固定 sleep。只有订阅建立后，Replay Market 才能把对应 channel 的历史事件交给 Strategy。

这样可以保证：

- Strategy 不会错过回测开始阶段的事件；
- 不同订阅的事件过滤在 Market 侧完成；
- 回测输入边界可复现；
- execution 不会因为 market 先结束而错过订单处理；
- backtest 的结束条件可以明确绑定 `replay completed`。

## 11. Execution 的生命周期

Execution 是有状态的常驻业务进程，但常驻范围是一个 launch instance，而不是整个 workspace。

### 11.1 Backtest

回测 execution 从回测开始运行到 replay 完成：

~~~text
启动 execution
    → 等待 strategy intent
    → 接收 market event
    → 按模拟规则判断成交
    → 发布 fill
    → 写入 execution projection
    → replay 完成
    → flush state
    → 退出
~~~

它不是一个无限期 server，时间推进由 replay market 驱动。相同输入事件、策略版本、配置和初始账户状态必须得到相同结果。

### 11.2 Paper

Paper execution 长期运行，但始终是 simulated provider：

- 持有虚拟 open orders；
- 处理 market order、limit order、取消和拒绝；
- 计算手续费和滑点；
- 产生模拟 fills；
- 将成交事实交给 paper account instance；
- 不拥有任何真实交易所订单 session；
- 不允许取得 live order entry connection。

### 11.3 Live

Live execution 长期运行，并在启动或恢复时完成真实订单对账：

~~~text
读取 execution checkpoint
    → 恢复本地订单状态
    → 查询交易所 open orders
    → reconciliation
    → 恢复未完成订单
    → 允许继续执行
~~~

在 reconciliation 完成之前，live execution 不应继续接受新的真实下单请求。live 的真实 account 可以被多个配置引用，但同一真实账户必须由 lease/exclusive ownership 保护。

### 11.4 Execution 的进程边界

每个 instance 的 execution 数据应位于：

~~~text
workspace/launches/<mode>/<launch-id>/instances/<instance-id>/execution/
~~~

至少包括：

~~~text
execution/
├── state.json
├── orders.jsonl
├── fills.jsonl
├── checkpoint.json
└── session.json          # live 可选
~~~

不能使用下面的共享目录：

~~~text
workspace/execution/
~~~

因为这会把不同 launch 的订单命名空间、恢复状态和执行会话混在一起。

## 12. Instance Data Environment 与 Projection

`LaunchEnvironment` 不应只创建日志目录，而应为每个 instance 创建完整的数据环境。推荐目录：

~~~text
workspace/launches/<mode>/<launch-id>/instances/<instance-id>/
├── normalized-config.json
├── state.json
├── command.json
├── market/
│   ├── input/
│   ├── events.jsonl
│   └── checkpoint.json
├── execution/
│   ├── state.json
│   ├── orders.jsonl
│   ├── fills.jsonl
│   └── checkpoint.json
├── account/
│   ├── state.json
│   ├── balances.jsonl
│   ├── positions.jsonl
│   └── checkpoint.json
├── strategy/
├── logs/
└── results/
~~~

三类 projection 的边界如下：

### Market projection

由 Market instance 拥有，记录：

- 最新市场状态；
- event sequence；
- replay position；
- 连接或订阅 checkpoint；
- 必要的事件日志。

### Execution projection

由 Execution instance 拥有，记录：

- orders；
- order status；
- fills；
- rejection/unknown/failed 状态；
- execution sequence；
- simulated matching state 或 live session；
- execution audit。

### Account projection

由 Account instance 拥有，记录：

- cash/asset balance；
- positions；
- average price；
- realized PnL；
- unrealized PnL；
- equity；
- account event sequence；
- freshness、stale 和 reconciliation 状态。

Projection 是运行时状态的持久化表现，不是跨模块共享数据库。每个 projection 只能由对应的状态拥有者写入；其他模块通过 application API 或版本化事件读取或请求变更。

## 13. 三种模式的配置语义

配置继续使用 TOML，CLI 结果使用 text/json。不同 mode 的配置表必须表达不同的资源边界，并在加载阶段拒绝非法组合。

### 13.1 Backtest

~~~toml
[launch]
id = "btc-sma"
mode = "backtest"
strategy = "strategies.btc_sma:run"

[backtest.market]
events = "data/btc-usdt-1m.jsonl"
start = "2024-01-01T00:00:00Z"
end = "2024-02-01T00:00:00Z"
storage_format = "jsonl"

[backtest.account]
initial_cash = 10000.0
currency = "USDT"

[execution]
provider = "simulated"
fee_rate = 0.001
slippage_bps = 2
~~~

Backtest 必须具备历史事件、时间范围和虚拟账户初始状态。`account.ref`、live execution 和真实 credentials 不得用于 backtest。

### 13.2 Paper

~~~toml
[launch]
id = "btc-paper"
mode = "paper"
strategy = "strategies.btc_sma:run"

[paper.market]
provider = "binance"
symbol = "BTC/USDT"
timeframe = "1m"

[paper.account]
initial_cash = 10000.0
currency = "USDT"

[execution]
provider = "simulated"
fee_rate = 0.001
slippage_bps = 2
~~~

Paper 也可以使用 replay market：

~~~toml
[paper.market]
provider = "replay"
events = "data/btc-usdt-1m.jsonl"

[paper.replay]
speed = 1.0
on_end = "stop"
~~~

Paper replay 的 market 仍然是独立 instance，不能直接向 live market instance 订阅。

### 13.3 Live

~~~toml
[launch]
id = "btc-live"
mode = "live"
strategy = "strategies.btc_sma:run"

[account]
ref = "binance-main"

[live.market]
provider = "binance"
symbol = "BTC/USDT"
timeframe = "1m"

[execution]
provider = "live"

[live]
trading_enabled = false
require_limit_orders = true
max_order_notional = 100.0
~~~

Live 必须显式启用真实交易，并通过安全配置限制订单类型和金额。没有完成账户和订单 reconciliation 时不能继续下单。

## 14. 启动、停止与恢复顺序

### 14.1 Backtest 启动和结束

回测需要避免 market 先发布全部事件、strategy 后启动而丢失事件。建议顺序：

~~~text
1. 创建 instance data environment
2. 初始化 Account instance
3. 启动 Execution instance
4. 启动 Replay Market instance，但暂停回放
5. 启动 Strategy instance
6. Strategy 发起 subscription
7. Market 确认 subscription 并建立 replay-ready barrier
8. 开始推进历史时间并等待 replay 完成
9. 停止/flush Strategy
10. 停止/flush Execution
11. 停止/flush Account
12. 生成 results 和 summary
13. 输出 CLI 结果并退出
~~~

回测的结束条件是 market replay 完成，不是任意一个进程短暂退出。所有 projection 在生成结果前必须 flush。

### 14.2 Paper/live 启动

~~~text
1. 创建或恢复 instance data environment
2. 启动 Account instance
3. 启动 Execution instance
4. 启动 Market instance
5. 启动 Strategy instance
6. 完成 health check
7. enable launch
~~~

Paper 和 live 的停止顺序为：

~~~text
1. 停止 Strategy
2. 停止 Market
3. flush Execution
4. flush Account
5. 写入 instance stopped 状态
~~~

### 14.3 恢复

恢复只能针对同一个 instance：

~~~text
恢复 instance
    → 读取该 instance 的 market checkpoint
    → 读取该 instance 的 execution checkpoint
    → 读取该 instance 的 account checkpoint
    → 恢复 open orders 和账户状态
    → 重新建立连接
    → 继续该 instance 的生命周期
~~~

Paper 恢复虚拟订单、虚拟账户和 replay/market checkpoint。Live 恢复本地 projection 后，必须查询交易所并完成订单和账户 reconciliation。Backtest 默认是确定性的一次性运行；如果需要断点续跑，也只能从该 backtest instance 的 checkpoint 继续。

## 15. CLI 与 Server 的运行方式

CLI 仍然是一次性入口，不依赖 server：

~~~text
<module>-cli
    → 读取 workspace/config
    → 创建或打开 instance/application
    → 执行一次 use case
    → 输出 stdout
    → 退出
~~~

Server 是长生命周期入口，持有属于某个 instance 的 Actor、连接和运行状态：

~~~text
<module>-server
    → 打开 instance data environment
    → 启动 Actor/process
    → 持续处理 command/event
    → 按 instance stop/restore
~~~

两者共享 application request/result、composition/factory、workspace resource resolver 和错误模型，但 CLI 不通过调用 server 才能完成账户、执行或回测验证。

回测建议优先提供一次性 CLI：

~~~bash
kairos launch backtest btc-sma
kairos launch backtest btc-sma --output json
~~~

Paper/live 使用 launch server 生命周期：

~~~bash
kairos launch start btc-paper
kairos launch status btc-paper
kairos launch logs btc-paper
kairos launch stop btc-paper
~~~

stdout 只输出命令结果，日志和运行过程信息进入日志文件或 stderr；配置使用 TOML，JSON 只作为输出、normalized config、projection/result 等机器可读产物。

## 16. 模拟执行的最小业务范围

Backtest 和 paper 共用 simulated execution 的业务规则，但不能共用实例状态。第一阶段不需要复杂撮合，先实现确定性的最小规则：

- market order 按当前 market event price 成交；
- limit buy 在最低价不高于 limit price 时成交；
- limit sell 在最高价不低于 limit price 时成交；
- 支持手续费和固定滑点；
- 产生 order、fill、reject 和 cancel 事件；
- 保存 open order 和 execution checkpoint。

后续再增加部分成交、订单簿深度和更复杂的撮合规则。规则必须由 simulated execution 拥有，不能散落在 market、account 或 strategy 中。

## 17. 逐步落地顺序

不提前构建统一 trading runtime，按以下顺序推进：

### 阶段一：固定 instance 语义

1. 明确 `Launch` 与 `Launch Instance` 的区别。
2. 让 `LaunchEnvironment` 创建 market、execution、account、strategy、logs、results 目录。
3. 给每个 instance 分配独立的资源路径、process identity 和状态文件。
4. 在配置校验中拒绝 backtest/live、paper/live 等非法 provider 组合。

### 阶段二：完成 backtest 最小闭环

~~~text
jsonl historical events
    → Replay Market Instance
    → Strategy Instance
    → Simulated Execution Instance
    → Backtest Account Instance
    → summary.json
~~~

先支持一个 symbol、market order、全额成交、固定手续费和一次性 CLI，验证确定性、projection 和结果产物。

### 阶段三：用同一 simulated execution 支持 paper replay

复用订单生命周期、fill、fee/slippage、account apply fill 和持久化能力，只替换 market feed 和生命周期：

~~~text
Backtest = Replay Market + process exits
Paper    = Realtime/Replay Market + long-running process
~~~

先让 paper replay 能长期运行、停止、恢复和生成实时状态，再接真实行情。

### 阶段四：接入真实行情

先接一个 provider 的 market data：

~~~text
Real Market Integration
    → Paper Market Instance
    → Strategy
    → Simulated Execution
    → Paper Account
~~~

验证连接恢复、事件顺序、背压和 instance checkpoint 后，再扩展其他 provider。

### 阶段五：接入 live execution

最后才开放：

1. live account definition 和 account reconciliation；
2. live execution order entry；
3. open order recovery；
4. trading safety gate、limit order 限制和金额限制；
5. 同一真实账户的 instance lease。

## 18. 最终架构决策

本设计的不可变约束是：

~~~text
一个 Launch Instance
    = 一个 Market Instance
    + 一个 Strategy Instance
    + 一个 Execution Instance
    + 一个 Account Instance
    + 一个独立 Instance Data Environment
~~~

并且：

- Execution 生命周期跟随 instance；
- Account 的运行时生命周期跟随 instance；
- Market replay 必须是本次 backtest 的独立 market instance；
- Backtest 和 paper 都拥有独立的 simulated execution/account environment；
- Paper 可以复用 live 的 market connection 类型，但不能复用 live 的 market state、execution state 或 account state；
- Live 可以引用 workspace 级 account definition，但必须拥有自己的 account projection，并通过 lease 保护真实账户；
- 不创建 workspace 级 global execution、global account 或 global market；
- integration 只提供连接和中性 DTO；
- workspace 只提供通用资源和生命周期能力；
- 业务状态由对应业务模块拥有；
- 跨业务协作通过 application/composition 完成；
- 当前不提前引入统一 trading runtime，未来由 kairospy/system 在需要时负责更高层组合。

## 19. Instance Workspace 与 Socket、Actor、FlatBuffers

当前项目已经具备一套适合 instance 运行环境的底层架构：

- Actor 是 projection 的唯一写入者；
- FlatBuffers 描述跨进程 snapshot 和 event contract；
- mmap 双 slot 提供 snapshot/read plane；
- Unix socket 提供 event/command/control plane；
- snapshot header 已经包含 owner actor、event stream、event sequence 和 generation；
- Strategy 已经遵循 snapshot 加 event stream 的启动和恢复语义；
- Account、Execution、Market 已经分别拥有自己的 projection schema。

因此不再创建另一套独立的 workspace 抽象。接下来的 workspace 应该围绕这套 `socket + Actor + FlatBuffers` 架构，把所有资源从 workspace 级收敛到 `Launch Instance` 级。

### 19.1 四层 Instance 结构

一个 instance 应形成四层运行时边界：

~~~text
Launch Instance
├── Workspace namespace
├── Actor runtime
├── Snapshot data plane
└── Socket event/control plane
~~~

它对应的业务资源是：

~~~text
Launch Instance
├── Market Read Workspace
├── Account State Workspace
├── Execution State Workspace
├── Strategy Workspace
└── Instance Metadata
~~~

这里的 workspace 是 instance 内部的资源和状态上下文，不是每个业务模块重新创建一个顶层 workspace。所有 socket、snapshot、checkpoint 和 Actor identity 都必须绑定到同一个 instance。

## 20. Snapshot Plane 与 Event/Command Plane

底层 transport 分成两条明确的通道。

### 20.1 Snapshot/read plane

通过 mmap 和 FlatBuffers 读取稳定的当前状态：

~~~text
mmap + FlatBuffers
~~~

典型内容包括：

- Market 当前 quote、trade、bar、rate、Greeks；
- Market 当前 order book；
- Market subscription 状态；
- Account 当前 balance、position、equity；
- Execution 当前 order 和 fill；
- Strategy 启动时需要的最新业务快照。

Snapshot 的原则是：

- Actor 是唯一写入者；
- Actor 构造完整的新 payload 后发布 generation；
- reader 只能读取，不能重建或写入 Actor projection；
- mmap generation 保护 snapshot slot 的原子发布；
- event sequence 保护 snapshot 与事件流的连续性；
- generation 和 event sequence 是两种不同的递增值，不能混用。

### 20.2 Event/command plane

通过 Unix socket 和 length-prefixed FlatBuffers message 进行实时交互：

~~~text
Unix socket + length-prefixed FlatBuffers message
~~~

典型内容包括：

- Strategy → Market：subscription request；
- Market → Strategy：quote/trade/bar event；
- Strategy → Execution：order intent；
- Execution → Account：order/fill event；
- CLI → Actor：query/command；
- system → Actor：start/stop/restore；
- Actor → system：health/lifecycle event。

Snapshot 适合读取“现在的完整状态”，socket 适合传递“状态变化和命令”。二者都必须使用同一个 instance identity 和 event sequence 语义。

## 21. Workspace 资源必须 instance-aware

当前 Rust market 进程使用的资源形式类似：

~~~text
workspace/state/market/market.snapshot
workspace/run/market/market.sock
workspace/run/market-events/market-events.sock
~~~

这仍然是 workspace 级资源，多个 backtest、paper 和 live instance 运行时会产生路径和状态冲突。应当收敛为：

~~~text
workspace/launches/backtest/btc-sma/instances/one/
├── market/current.snapshot
├── market/events.sock
└── ...

workspace/launches/paper/btc-paper/instances/default/
├── market/current.snapshot
├── market/events.sock
└── ...
~~~

`kairos-workspace` 应提供通用的 instance resource resolver，例如：

~~~rust
Workspace::launch_instance(mode, launch_id, instance_id)

InstanceResources::snapshot_path(actor, view)
InstanceResources::event_socket(actor, stream)
InstanceResources::command_socket(actor)
InstanceResources::checkpoint_path(actor)
InstanceResources::state_dir(actor)
~~~

它只负责：

- instance identity；
- 路径解析和路径安全；
- run/state/logs/data 目录；
- socket 和 snapshot 文件位置；
- process、health、control 资源；
- instance 的生命周期元数据。

它不能提供业务语义 API，例如：

~~~rust
workspace.account_state()
workspace.execution_orders()
workspace.market_snapshot()
~~~

这些业务语义由 Account、Execution、Market 各自的 application 提供。Workspace 只提供业务模块使用的通用 root 和资源句柄。

## 22. FlatBuffers 必须携带 Instance Identity

当前 `SnapshotHeader` 已包含：

~~~text
owner_actor_id
event_stream_id
event_sequence
version
generation
as_of
generated_at
complete
~~~

这些字段是正确的基础，但当多个 instance 共享同一个 workspace 时，仅凭 actor 和 socket 路径不足以验证消息归属。Snapshot 和跨进程 Message 都应该能够明确回答：

~~~text
哪个 workspace？
哪个 launch？
哪个 instance？
哪个 actor？
哪个 stream？
第几个事件？
对应哪个业务时间？
~~~

因此公共 FlatBuffers contract 应补充或等价表达：

~~~text
workspace_id
launch_id
instance_id
owner_actor_id / producer_actor_id
stream_id
sequence
event_time
publish_time
~~~

可以将 workspace、launch、instance 放入公共 header，也可以使用不可歧义的 instance-scoped stream identity，但不能仅依靠 socket 文件路径推断消息归属。消息离开 socket 后仍然应该能够完成 instance 校验。

Account、Execution、Market 的业务 payload 还必须携带自己的业务 identity，例如：

- `account_id`；
- `instrument_id`；
- `order_id`；
- `intent_id`；
- `execution_id`。

## 23. 下单校验围绕 Market Snapshot 和 Sequence

下单不能只根据 Strategy intent 直接执行。Execution 需要确认 intent 对应的行情上下文，并记录校验时使用的 snapshot/event identity。

建议的消息关系是：

~~~text
MarketActor
    → publish snapshot generation=42
    → event_sequence=108

Strategy
    → receive event_sequence=108
    → produce OrderIntent
    → include market_sequence=108

ExecutionActor
    → read/confirm sequence=108 market context
    → validate order
    → accept or reject
    → record validation sequence=108
~~~

Market 通过自己的 application 提供只读行情上下文，例如：

~~~text
MarketSnapshot
├── instrument
├── bid/ask
├── last price
├── event time
├── event sequence
└── snapshot generation
~~~

Execution 不直接读取 Market 的文件、mmap 内存或内部数据库，而是通过 Market application/query 边界取得这个只读上下文。Execution 也不能把 Market projection 复制成自己的事实来源。

OrderIntent 或 Execution validation record 至少需要能够追踪：

~~~text
market_event_sequence
market_event_time
market_snapshot_generation
validation_price
validation_result
~~~

这样可以防止回测时间穿越，并让 paper/live 的价格、交易规则和订单校验过程可审计、可恢复。

## 24. Strategy Subscription 与 Replay Market

数据订阅意图属于 Strategy，但 Strategy 不直接连接交易所、不读取历史文件，也不依赖具体 provider。消息流应为：

~~~text
Strategy
    → subscribe(symbol, timeframe, channels)
    → Market application
    → Market provider/replay source
    → Market events
    → Strategy
~~~

Strategy 决定需要什么数据，Market application 管理订阅和分发，Market composition 选择数据 source，integration connection 负责真实 provider 细节，Replay Market 负责历史文件和虚拟时间。

Backtest 不应让一个运行中的 MarketActor 比较历史 `event_time` 和机器 wall-clock time，然后自行 fork。Launch 在创建 instance 时就选择 Replay Market：

~~~text
mode = backtest
    → ReplayMarketActor/process

mode = paper
    → LiveMarketActor/process 或 ReplayMarketActor/process

mode = live
    → LiveMarketActor/process
~~~

这里的 fork 是 instance/process 的独立运行，不是 MarketActor 的自我分叉，也不是复制 Market 代码。所有 MarketActor 都使用同一业务协议，但拥有不同 feed、socket、snapshot、checkpoint 和生命周期。

## 25. Replay Start Barrier 与虚拟时间

Replay Market 需要等待 Strategy 建立订阅，不能在 Strategy 启动之前把全部历史事件发布完。回测流程应包含明确的 `replay-ready` barrier：

~~~text
Replay Market 启动但暂停回放
    → Strategy 启动并注册 subscription
    → Market 确认 subscription
    → Launch/Market 发布 replay-ready
    → Replay Market 开始推进历史时间
~~~

这个 barrier 必须通过 application/control/event 协议表达，不能依赖进程启动顺序或固定 sleep。

Replay Market 的职责是：

1. 加载历史数据；
2. 按 backtest `start` 和 `end` 过滤事件；
3. 只发布已订阅的 channel；
4. 按历史 event time 顺序发布；
5. 使用 virtual clock，而不是机器当前时间；
6. 在数据结束或达到 `end` 时发布 `replay completed`；
7. flush Market projection 和 checkpoint 后退出。

这样可以保证：

- Strategy 不会错过回测开始阶段的事件；
- Market 侧完成订阅过滤；
- 回测输入边界可复现；
- Execution 不会因 Market 先结束而错过订单处理；
- backtest 的结束条件可以明确绑定 `replay completed`。

## 26. Account Application 与多个账户 Actor

一个 Account Application 可以服务多个账号，但它服务的是多个独立 Account Actor，而不是一份共享账户状态：

~~~text
一个 Launch Instance
└── Account Application
    ├── AccountActor(account-a)
    ├── AccountActor(account-b)
    └── AccountActor(account-c)
~~~

每个 Account Actor 必须拥有：

- `account_id`；
- `instance_id`；
- 独立 balance/position state；
- 独立 snapshot；
- 独立 journal；
- 独立 checkpoint；
- 独立 account event sequence；
- 独立 live reconciliation state（live 场景）。

一个账户一个进程还是一个 application 进程承载多个 Actor，是 composition/deployment 决策，不改变 Account 的业务边界。

第一阶段建议：

~~~text
一个 Launch Instance
└── 一个 Account Application process
    └── 一个或多个 Account Actor
~~~

以下情况可以拆成多个进程：

- live 账户需要不同 credentials；
- 账户使用不同 integration connection；
- 账户需要独立重启；
- 账户生命周期不同；
- 安全隔离要求更高；
- 不同账户属于不同 launch instance。

多账户目录可以是：

~~~text
workspace/launches/<mode>/<launch-id>/instances/<instance-id>/account/
├── index.json
├── account-a/
│   ├── current.snapshot
│   ├── state.json
│   ├── balances.jsonl
│   ├── positions.jsonl
│   └── checkpoint.json
├── account-b/
└── account-c/
~~~

Execution intent、order 和 fill 必须明确携带 `account_id` 与 `instance_id`，由 Account Application 路由到正确的 Account Actor，不能依赖全局默认账户。

## 27. Instance 内的完整数据流

最终的 instance 内数据流应是：

~~~text
MarketActor
    ├── mmap FlatBuffers snapshot
    └── Unix event socket
            │ market event
            ▼
StrategyActor
    ├── 订阅 Market
    └── 产生 OrderIntent
            │ intent socket / FlatBuffers command
            ▼
ExecutionActor
    ├── 读取 Market application 的 snapshot context
    ├── 校验订单
    ├── 写入 order/fill projection
    └── 发布 order/fill event
            │ fill event socket / FlatBuffers event
            ▼
AccountActor
    ├── apply fill
    ├── 写入 balance/position projection
    └── 发布 account state snapshot/event
~~~

各 Actor 只写自己的 projection：

~~~text
MarketActor    → market projections
ExecutionActor → order/fill projections
AccountActor   → balance/position/equity projections
StrategyActor  → strategy-local model and indicator state
~~~

Monitor、CLI、UI 和 system 都是 reader/orchestrator，不得成为这些 projection 的第二个写入者。

## 28. 基于现有 Transport 架构的后续工作

后续实现应围绕现有 socket、Actor、FlatBuffers 和 mmap 继续收敛：

1. 为 Workspace 增加 instance-aware resource resolver。
2. 将 Market 当前 snapshot、event socket 和 checkpoint 从 workspace 级路径移到 instance 级路径。
3. 为 Account 和 Execution 创建同样的 instance 级 snapshot、event、command 和 checkpoint 资源。
4. 在公共 FlatBuffers header 中补充或等价表达 workspace/launch/instance identity。
5. 确保所有 snapshot 和 event 都带有 owner actor、stream、sequence、event time 和 publication generation。
6. 为 Strategy subscription 增加明确的 instance-scoped Market application command。
7. 为 Replay Market 增加历史时间窗口、virtual clock 和 replay-ready barrier。
8. 让 Execution 的订单校验记录 Market snapshot generation 和 event sequence。
9. 让 Account Application 支持一个 instance 内多个独立 Account Actor。
10. 让 account/order/fill 消息明确携带 account_id、instance_id 和业务对象 identity。
11. 为 backtest、paper、live 分别验证独立的 socket、snapshot、checkpoint 和恢复行为。

最终目标不是把所有业务状态写入一个共享 workspace 数据库，而是让同一个 instance 通过统一的 identity、socket namespace、FlatBuffers contract 和 Actor ownership 收敛成一个可恢复、可验证、不会互相污染的运行环境。

## 29. Workspace 第一阶段落地状态

本阶段已经在当前实现中建立了以下基础能力：

1. Python Workspace 暴露 `InstanceWorkspace`，负责 instance 根目录、socket、health、state、snapshot 和 log 资源定位。
2. Rust `kairos-workspace` 暴露同等语义的 `InstanceWorkspace`，供 Rust 组件使用。
3. `launch start` 创建并准备 instance 目录，并把 market、account 启动参数绑定到 `mode/launch_id/instance_id`。
4. Market 的 snapshot、事件 socket 和控制 socket 可以落到 instance 目录。
5. Account 的 state、snapshot、health 和控制 socket 可以落到 instance 目录。
6. Execution 的 state、audit 和控制 socket 可以落到 instance 目录。
7. 不带 launch identity 的独立 CLI/server 仍使用 project Workspace 资源；这属于模块自身的一次性入口，不是 instance runtime。

这一步只完成资源隔离，不宣称 backtest/paper/live 的完整业务运行时已经完成。后续仍需补充：

- FlatBuffers 公共 header 的 instance identity；
- Strategy subscription 与 replay-ready barrier；
- 多 Account Actor 的 instance 内路由；
- Execution 对 market snapshot generation/sequence 的订单校验；
- launch stop/recovery 对 instance 组件的完整生命周期管理。

## 30. Instance 生命周期与传输身份的当前落地

在 Workspace 资源隔离之后，组件控制也必须使用同一组 instance 资源：

- `ComponentProcessApplication.status()` 支持读取 instance-scoped socket；
- `ComponentProcessApplication.stop()` 支持停止 instance-scoped component；
- `launch stop` 会按 instance 停止 market、execution、account，再释放账户 lease；
- Account、Execution、Risk server 都要求 launch identity，只使用 instance socket/state；
  模块 CLI 的 one-shot 管理命令可以直接组合 application，但不会启动 project-level
  business runtime。

公共 FlatBuffers 的 `MessageHeader` 和 `SnapshotHeader` 已增加可选的：

```text
workspace_id
launch_id
instance_id
```

这些字段保持可选是为了让 project-level 的一次性 CLI 消息继续拥有合法的协议形状；对于 launch instance 内的具体输出 writer，composition 必须填充它们。Market 和 Account 的 instance writer 已经注入并写入这些字段，Transport reader 也会解码校验；不能把 project-level 消息中的空字段误认为 instance identity。

## 31. Publisher 抽象的收敛原则

Publisher 不应被一律抽象成业务 protocol。实际代码中的职责不同：

- Market snapshot publisher 只是 `MarketProcess` 的输出步骤，因此已删除 `MarketPublisher` protocol，process 直接调用具体的 `MmapMarketSnapshotPublisher`。
- Account snapshot writer 已移动到 `AccountProcess`，AccountActor/Application 不再持有 publisher，也不再接收 publisher protocol。
- Execution audit writer 已移动到 `ExecutionProcess`，ExecutionApplication 只保留待输出事件，Process 直接调用具体 SQLite audit writer。
- Execution 启动时会从持久化 snapshot 恢复待输出事件；SQLite audit 使用 event identity 去重，避免恢复时重复写入。
- Market event 不再通过 `MarketEventPublisher` protocol 发送。MarketActor 只产生待发送的业务事件，MarketProcess 直接调用具体 FlatBuffers 编码并写入 instance event socket。

因此 Account、Execution、Market 的 publisher protocol 都已经移除。剩余的 `AccountStateStore`、`ExecutionStateStore` 属于业务状态持久化依赖，不是 publisher 抽象。

Risk 和 Reference 也遵循同一条规则：RiskActor 只保留待发送事件，ReferenceApplication/Actor 只负责业务状态和刷新结果；具体 snapshot/event writer 由 process 或 composition 直接持有和调用。全仓不再存在 `*Publisher`、`SnapshotPublisher`、`RiskEventSink` 等输出协议，输出协议的具体实现不再伪装成业务依赖。

## 32. Instance identity 的实际传输落地

实例启动时由 Workspace/System 组合出唯一的 `InstanceIdentity(workspace_id,
launch_id, instance_id)`，并注入 Market 与 Account 的具体输出 writer：

1. Market mmap snapshot 的 `SnapshotHeader` 写入三项 identity。
2. Market Unix event socket 的 `MessageHeader` 写入三项 identity。
3. Account FlatBuffers snapshot 的 `SnapshotHeader` 写入三项 identity。
4. `kairos-transport` 解码 Market snapshot 时保留三项 identity，调用方可以拒绝跨 instance 数据。
5. one-shot CLI 可以使用空 identity 直接调用 application；Account、Execution、Risk
   server 本身不再接受没有 launch identity 的运行时启动。

因此实例边界同时由 socket/snapshot 路径和消息 header 保证：路径隔离防止进程互相读写，header identity 防止消息离开传输层后被错误归属。

## 33. Market live 共享与 backtest instance runtime

Market 的作用域和运行拓扑以
[`workspace-scope-and-market-runtime.md`](workspace-scope-and-market-runtime.md)
为准：不新增 `LaunchWorkspace` 或 `MarketWorkspace`。`Market Instance`
表示正在运行的 `kairos-market-server`，不是额外的业务状态层。

- live 模式允许 Workspace 级共享一个 Market server，复用 provider connection
  和原始行情 feed；每个 launch consumer 的订阅、cursor、freshness 和派生投影仍须隔离。
- backtest 模式由每个 Launch Instance 独立运行 Market server；历史数据源可以复用，
  但 replay clock、event cursor、checkpoint、order book 和完成状态必须归属于该 instance。
- paper 模式根据数据来源选择共享 live feed 或 instance-owned replay，不改变 Account、
  Execution 和 Strategy 的 instance 隔离要求。

Binance Equity 行情已通过 Integration 的 REST quote polling 接入：它调用
`/sapi/v1/equity/market/quote`，并以统一的 MarketStream 语义向 MarketActor 提供
bid/ask。长期配置应将 provider 和 credential 放在 Workspace 级连接定义中，launch
只绑定连接名称：

~~~toml
[market.connections.binance-equity]
provider = "binance-equity-rest"
credential_id = "binance-equity-readonly"

[paper.market]
connection = "binance-equity"
scope = "instance"
~~~

该连接使用 API key，签名交易操作才需要 API secret。

具体标的和订阅意图由 Strategy Instance 通过 `context.subscribe(...)` 定义，不放在
`paper.market` 中。`paper.market` 只选择连接和 Market runtime scope；Market process
根据策略订阅请求创建对应的市场 descriptor 和 provider subscription。

当前实现中，Workspace 会准备 `market/connections/` 作为共享 live connection 元数据目录；
instance 会准备 `market/`，Replay Market 将 cursor 写入该目录。`launch start` 对 live
默认使用 Workspace 级 Market，对 replay/backtest 默认使用 instance 级 Market；launch
可以通过 `<mode>.market.scope = "instance"` 为 live 或 paper 选择独立 Market，并始终
按 instance 启动 Account 与 Execution。`launch stop` 不会因停止一个使用 shared scope
的 launch 而停止共享 Market。

## 34. Account、Execution、Risk 的 instance-only runtime

Account、Execution、Risk 不提供 Workspace 级业务运行实例：

- Account 的 balance、position、equity、account order facts 和 refresh state
  写入 `<instance>/state/account/`，控制 socket 和 snapshot 也在 instance 下。
- Execution 的 order lifecycle、fill、audit 和恢复状态写入
  `<instance>/state/execution/`。
- Risk 的 budget、reservation、generation 和 event sequence 写入
  `<instance>/state/risk/risk-state.json`。

三个 server 都由 System/launch 传入同一个 `InstanceWorkspace`。Rust server 只解析
这个运行上下文，不判断自己是“全局”还是“局部”；没有 launch identity 的 server
启动会被拒绝。Workspace 级的 accounts、credentials、profiles 只是配置和凭证资源，
不是业务状态 owner。

一次 launch 的启动顺序是：

    InstanceWorkspace
    ├── Account server
    ├── Execution server
    ├── Risk server
    └── Strategy server

停止 launch 时按同一 instance 停止 Risk、Execution、Account；共享 Market 仍由
Workspace/System 的 shared runtime 生命周期管理。
