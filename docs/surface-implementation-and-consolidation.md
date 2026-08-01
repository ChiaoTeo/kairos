# KairosPy Surface 实现梳理与收敛建议

本文基于当前代码实现梳理 KairosPy CLI surface。目标不是只看命令名称，而是把每个顶层命令实际调用的 facade、运行时通道、workspace 存储和 integration 能力串起来，然后判断哪些应该保留为产品域，哪些应该下沉或删除。

## 当前顶层

当前 `kairospy --help` 暴露的推荐顶层命令是：

```text
shell
project
launch
account
order
market
catalog
system
```

这些命令在 `kairospy/surface/cli/app.py` 注册。整体入口是 Typer app，所有命令通过 CLI 层薄封装后进入 application facade 或 timeline server。`reference`、`credential`、`config`、`timeline` 和空的 `strategy` 已从顶层 surface 删除；能力分别迁入 `catalog`、`account credential`、`project config` 和 `launch timeline`。

## 实现分层

当前实现大致有四层：

```text
surface/cli
  Typer 命令、参数解析、输出格式、交互式 browse/shell

application/*/facade
  面向 CLI 的用例 facade，例如 LaunchFacade、AccountFacade、OrderFacade

application/service、application/support/runtime、application/support/system/workspace
  运行模式、runtime command channel、workspace store、operation journal、projector

infrastructure/integrations
  broker/exchange/provider/driver connector，适配 CCXT、Massive、Binance 等外部系统
```

所以 surface 收敛时要区分两件事：

- 用户是否需要把它当成顶层产品对象。
- 实现是否只是另一个顶层命令的配置、控制面或 artifact 视图。

## `shell`

### 当前功能

`shell` 是稳定的交互式命令 shell。无参数且 stdin/stdout 是 TTY 时，`kairospy` 默认进入 shell。

### 实现路径

- CLI：`kairospy/surface/cli/app.py`
- 交互：`kairospy/surface/interactive/*`
- 导航：`kairospy/surface/interactive/navigation.py`
- 执行：shell 内部仍调用同一个 Typer app，通过 `_execute_product_command()` 执行普通 CLI 命令。

### 判断

这是 surface 形态，不是产品域。保留顶层合理，因为它是用户进入交互模式的入口。

建议：保留 `shell`。

## `project`

### 当前功能

`project` 管理 workspace：

```text
project init
project status
project doctor
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/project.py`
- Facade：`kairospy/application/support/system/facade/project.py`
- 存储：`.kairos/kairos.toml`、`.kairos/accounts`、`.kairos/launches`、`.kairos/data`、`.kairos/reference`、`.kairos/state/operations.jsonl`

`init` 创建 `.kairos` 目录结构和 manifest。`status` 统计 workspace、账户、launch、market datasets、reference root。`doctor` 检查 workspace 目录和 manifest。

### 判断

这是清晰的产品基础域。它不是交易对象，但所有其它能力都依赖 workspace。

建议：保留顶层 `project`。

## `config`

### 当前功能

`config` 管理 workspace 配置、manifest、profile 和 operation journal：

```text
config paths
config show
config manifest
config doctor
config explain
config operations
config profile list/use/create
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/config.py`
- Facade：`kairospy/application/support/system/facade/config.py`
- 存储：workspace manifest、profiles、state selection、operations journal

`config explain` 实际解释 launch config 和 workspace/account source，与 `launch diagnose explain` 有重叠。

### 判断

`config` 是 workspace 控制面，不是交易产品域。它和 `project` 有明显重叠：`project status/doctor` 与 `config paths/manifest/doctor` 都在回答 workspace 状态。

建议：

- 已收敛到 `project config ...`。
- 顶层 `config` 已删除。

推荐目标：

```text
project config paths
project config manifest
project config explain
project config operations
project config profile list/use/create
```

## `launch`

### 当前功能

`launch` 是策略/配置运行生命周期的核心入口：

```text
launch targets add/remove/index/list/browse
launch diagnose validate/explain
launch start/stop/status
launch logs/artifacts/instances
launch replay events
launch timeline list/export/open/api
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/launch.py`
- Facade：`kairospy/application/support/launch/facade.py`
- 控制：`LaunchControl`
- 启动：`TradingSystemLauncher`
- 存储：`.kairos/launches/<mode>/<launch_id>/...`
- Runtime command channel：`submit_command()` 写 command file，可选择等待 response file。

`LaunchFacade` 覆盖以下用例：

- 注册/解析 launch config：`register_target()`、`specs()`、`validate()`、`explain()`
- 启停和状态：`start()`、`stop()`、`records()`
- artifact：`logs()`、`artifacts()`
- replay：`launch_events()`
- daemon：`daemon()` 作为 facade 内部能力，供后台启动实现复用，不作为 CLI surface 暴露。
- system runtime：`system_up()`、`system_down()`、`system_restart()`、`system_command()`、`system_inspect()` 由顶层 `system` 调用。

### 判断

`launch` 是 KairosPy 的主产品域。README 的核心链路也是 CLI -> Launch Facade -> Runtime -> Strategy/Services/Artifacts。

问题在二级命令：

- `launch system` 和顶层 `system` 重复，已删除。
- `launch daemon`、`launch run` 都在表达运行生命周期，已收敛到 `launch start/stop/status/logs/artifacts/instances`。
- `targets index` 语义偏内部，和 list/specs/register 关系不够直观。

建议：

- 保留顶层 `launch`。
- system runtime 只保留顶层 `system`。
- `run` 和 `daemon` 已整理成一个 lifecycle 心智：

```text
launch start
launch stop
launch status
launch logs
launch artifacts
launch targets ...
launch diagnose ...
launch replay ...
```

后台启动通过 `launch start --background ...` 表达；普通启动使用 `launch start TARGET`。

## `system`

### 当前功能

`system` 管理内置 system runtime：

```text
system up
system down
system restart
system status
system inspect
system command
system attach
system account trade-status/trade-acquire/trade-release
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/system.py`
- Facade：同样使用 `LaunchFacade`
- Runtime mode：`RuntimeMode.SYSTEM`
- 默认 launch id：`kairos-system`

迁移前，`system.py` 和 `launch.py` 中的 system runtime 实现基本重复，最终都走 `LaunchFacade.system_*()`。当前 CLI 只注册顶层 `system`。

### 判断

`system` 是控制面，但它足够重要，因为它代表一个长驻的内置交易运行时，负责账户视图、命令处理和 trade-lock 协调。顶层保留合理。

迁移结果：

- 保留顶层 `system` 作为唯一主入口。
- 删除 `launch system`。
- `system command` 保留为高级入口。
- `system account trade-*` 保留为 system runtime 控制入口；普通账户侧的 lock/status 已下沉到 `account trade-lock ...`。

## `account`

### 当前功能

`account` 现在承担三类功能：

配置管理：

```text
account list/browse
account schemas/schema
account create/modify/delete/remove/show
account credential add/list/create/show/delete
account doctor
```

账户查询：

```text
account query balance/current/balances/positions/open-orders/snapshot
```

锁和 system trade 能力：

```text
account trade-lock status/list/show/release
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/account.py`
- Facade：`kairospy/application/support/system/facade/account.py`
- Workspace：`workspace.accounts`、`workspace.credentials`、`workspace.account_locks`
- Broker connector：`broker(..., DriverName.ccxt, credential=...)`
- Runtime command：`account.current`、`account.balances`、`account.positions`、`account.trade-status`

配置类命令直接读写 `.kairos/accounts/*.toml` 和 `.kairos/credentials/*.toml`。`balance/open-orders/snapshot` 直接创建 broker client 查询外部 broker。`current/balances/positions/trade-status` 则通过 launch/system command channel 请求运行中的 runtime。

### 判断

`account` 是核心产品域，应该保留顶层。问题是内部动作平铺导致用户无法区分“配置账户”“查 broker”“问 runtime”“管理交易锁”。

建议重组为：

```text
account list
account browse
account show
account create
account modify
account delete
account doctor

account schema list/show
account credential add/list/create/show/delete
account query balance/current/balances/positions/open-orders/snapshot
account trade-lock list/show/release/status
```

旧短路径已删除。查询类能力统一走 `account query ...`，交易锁统一走 `account trade-lock ...`。

## `credential`

### 当前功能

`credential` 管理 credential store：

```text
credential list
credential create
credential show
credential delete/remove
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/credential.py`
- Facade：`kairospy/application/support/system/facade/credential.py`
- 存储：`.kairos/credentials/<credential_id>.toml`

`CredentialFacade` 使用 account schema 推断 credential kind，写 credential TOML，并在 operations journal 记录操作。

### 判断

这是账户连接配置的一部分，不应该是一等产品域。顶层暴露会让用户误以为 credential 是独立业务对象，真实路径应归入 `account credential ...`。

建议：

- 已迁到 `account credential ...`。
- 顶层 `credential` 已删除。

## `order`

### 当前功能

`order` 负责订单查询和手工下单：

```text
order open/list/browse
order history/closed
order place
order cancel
order replace
order show/inspect
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/order.py`
- Facade：`kairospy/application/support/system/facade/order.py`
- Direct broker：`broker(...).fetch_open_orders()`、`fetch_closed_orders()`、`create_order()`、`cancel_order()`
- Journal：`.kairos/orders/journals/<account>.jsonl`
- Runtime command：`order.submit`、`order.cancel`、`order.status`

`place/cancel/replace` 默认 dry run，只有 `--submit` 才真实执行；live 账户还需要 `--confirm-live`。如果传入 `--launch`，则不直接操作 broker，而是通过 launch command channel 发给运行中的 runtime；底层 `--mode/--launch-id/--root` 仍作为隐藏高级参数保留给测试和诊断。

### 判断

`order` 是核心产品域，保留顶层合理。

迁移前，direct broker path 和 runtime command path 通过一组底层选项混在同一命令里：

```text
--account
--launch
--mode
--launch-id
--root
```

这会让用户不清楚订单命令是在直接访问 broker，还是发给一个运行中的策略/system。

迁移结果：

- 保留 `order open/history/place/cancel/replace/show`。
- 直接 broker 路径继续使用 `--account`。
- runtime 路径使用产品化参数 `--launch <target>`；`--mode/--launch-id/--root` 已从普通 help 隐藏。
- 高级 system runtime command 留给 `system command`；普通 launch 不暴露通用 daemon command。

## `market`

### 迁移前问题

`market` 曾经平铺混合四类功能：

能力检查：

```text
market capabilities
market check
market doctor
```

历史数据：

```text
market download
market prefetch
```

本地 dataset 管理：

```text
market list
market inspect
market alias
market prune
market read
```

live/replay：

```text
market watch
market persist
market replay
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/market.py`
- Facade：`kairospy/application/support/system/facade/market.py`
- Service：`MarketDataOperationsService`
- Resolver：`MarketDataResolver`
- Store：`data_store(root, storage_format)`
- Exchange connector：`exchange(exchange_name, driver_name)`
- Backtest prefetch：读取 launch config，执行 strategy `on_start()` 收集订阅，再下载所需历史数据。

### 判断

`market` 是核心产品域，保留顶层合理。问题是它同时表示“市场数据源”和“本地数据集仓库”，迁移前二者平铺。

已重组为：

```text
market source capabilities/check/doctor
market data download/prefetch
market dataset list/inspect/alias/prune/read
market stream watch/persist/replay
```

旧短路径已删除。默认 help 只暴露 `source/data/dataset/stream` 四个子域。

## `reference`

### 当前功能

`reference` 管理 reference catalog：

```text
reference sync binance/hyperliquid/massive
reference participants brokers/exchanges/providers
reference assets add/list/browse/show
reference markets list/browse/resolve
reference events
reference events sync
reference catalog view/query/search/show/status
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/reference.py`
- Core：`kairospy/core/reference/*`
- Store：`reference_store(root)`，包括 SQLite catalog 和 lifecycle events
- Connectors：exchange/provider reference clients
- Sync：`refresh_exchange_reference()`、`refresh_provider_reference()`、`sync_lifecycle_events()`

`reference` 已经有较清晰的二级结构。它不是简单查行情，而是管理 entity、asset、instrument、listing、market、event 的 catalog。

### 判断

这个域应该存在，但命名 `reference` 偏内部。对用户更清晰的产品词是 `catalog`，因为它是“标的、市场、参与方和生命周期事件目录”。

已落地：

- 主路径改成 `catalog`。
- `catalog query/search/show/status/view` 已提升到 `catalog` 顶层。
- `reference` 和内部 `catalog catalog ...` 已删除。

目标：

```text
catalog sync binance
catalog participants exchanges
catalog assets list
catalog markets resolve BTC/USDT --venue binance
catalog search BTC
catalog status
```

## `strategy`

### 当前功能

当前 `strategy` 顶层是空 group，没有子命令。

### 实现路径

- CLI：`kairospy/surface/cli/commands/strategy.py`

虽然 application 层有 `kairospy/application/usecases/strategy/*`，但 surface 上没有暴露实际策略管理能力。

### 判断

这是当前 surface 最应该立即处理的问题。空顶层会制造产品承诺，但用户进入后没有任何功能。

建议：

- 立即从默认 help 隐藏 `strategy`。
- 如果未来要恢复，必须先定义真实 lifecycle：

```text
strategy check
strategy list
strategy scaffold
strategy inspect
```

在此之前，策略运行入口应该留在 `launch`，例如 `launch start --strategy module:callable`。

## `timeline`

### 当前功能

`timeline` 查看 launch artifacts：

```text
timeline list
timeline export
timeline open
timeline api
```

### 实现路径

- CLI：`kairospy/surface/cli/commands/timeline.py`
- Loader：`kairospy/surface/timeline/loader.py`
- Server：`kairospy/surface/timeline/server.py`
- Projector：`LaunchProjectionService(...).load_timeline_view()`
- Static viewer：`kairospy/surface/timeline/static`

`timeline open` 会启动本地 HTTP server，选择 launch instance，提供 `/api/instances` 和 `/api/timeline`，并打开 React viewer。

### 判断

timeline 是 launch artifact viewer，不是独立交易产品域。它很有价值，但主语是 launch。

建议：

- 已迁到 `launch timeline ...`。
- 顶层 `timeline` 已删除。

目标：

```text
launch timeline list
launch timeline export
launch timeline open --latest <launch_id>
```

## `browse` 能力

### 当前功能

多个列表资源支持 `browse`：

```text
account browse
launch targets browse
order browse
catalog assets browse
catalog markets browse
```

### 实现路径

- Application：`kairospy/application/support/system/browsing`
- Surface：`kairospy/surface/tui`、`ResourceListBrowser`
- CLI domain command 负责提供 rows 和 columns

### 判断

`browse` 是横切交互能力，不应成为顶层产品域。当前作为每个资源的子命令是合理的。

## 当前主要问题

### 1. 顶层混合了产品域、控制面、配置面和 artifact 视图

核心产品域应该是用户自然理解的对象：

```text
project
launch
account
order
market
catalog
system
```

已删除或下沉的入口是：

```text
credential
strategy
config
timeline
```

其中 `credential/config/timeline` 已下沉，`strategy` 当前没有真实 CLI surface。

### 2. 重复入口造成维护和认知成本

迁移前最明显的是：

```text
system ...
launch system ...
```

两者最终都走 `LaunchFacade.system_*()`，CLI 层曾经有重复实现。当前已删除 `launch system`，只保留顶层 `system`。

### 3. 同一层混用了资源和动作

迁移前 `account` 同层有：

```text
create
balance
trade-status
locks
release-lock
snapshot
```

这些分别属于配置、查询、runtime command、lock 管理和 journaling。当前已收敛为 `account credential ...`、`account query ...`、`account trade-lock ...`。

### 4. 过多底层 routing 参数暴露给普通用户

典型例子是 order runtime path：

```text
--launch
--mode
--launch-id
--root
```

这些对实现很准确，但对用户不够产品化。

### 5. 命名偏实现历史

`reference` 更像内部领域名；对 CLI 用户，`catalog` 更直观。

`targets index` 也偏实现存储，用户可能更习惯 `targets list` 或 `targets show`。

## 最终建议

我建议把默认顶层收敛为：

```text
shell       交互入口
project     workspace
launch      策略/配置运行生命周期
account     账户配置与账户视图
order       订单查询与手工下单
market      行情、历史数据、本地 dataset、stream
catalog     标的/市场/参与方/reference catalog
system      内置 system runtime 管理
```

已删除或下沉：

```text
credential  -> account credential ...
config      -> project config ...
timeline    -> launch timeline ...
strategy    -> 删除，直到有真实命令
reference   -> catalog
launch system -> system
```

## 推荐目标结构

```text
kairospy shell

kairospy project init/status/doctor
kairospy project config paths/manifest/explain
kairospy project config profile list/use/create
kairospy project config operations

kairospy launch targets add/remove/list/browse
kairospy launch diagnose validate/explain
kairospy launch start/stop/status
kairospy launch logs/artifacts/instances
kairospy launch replay events
kairospy launch timeline list/export/open/api

kairospy system up/down/restart/status/inspect/attach/command
kairospy system account trade-status/trade-acquire/trade-release

kairospy account list/browse/show/create/modify/delete/doctor
kairospy account schema list/show
kairospy account credential add/list/create/show/delete
kairospy account query balance/current/balances/positions/open-orders/snapshot
kairospy account trade-lock list/show/release/status

kairospy order open/history/place/cancel/replace/show/browse

kairospy market source capabilities/check/doctor
kairospy market data download/prefetch
kairospy market dataset list/inspect/alias/prune/read
kairospy market stream watch/persist/replay

kairospy catalog sync binance/hyperliquid/massive
kairospy catalog participants brokers/exchanges/providers
kairospy catalog assets add/list/browse/show
kairospy catalog markets list/browse/resolve
kairospy catalog events
kairospy catalog events sync
kairospy catalog query/search/show/status
```

## 迁移结果

1. 空的 `strategy` 顶层已删除。
2. `system` 是唯一 system runtime 入口，`launch system` 已删除。
3. `reference` 已产品化为 `catalog`，旧顶层 `reference` 已删除。
4. `credential` 已迁入 `account credential ...`，旧顶层 `credential` 已删除。
5. `config` 已迁入 `project config ...`，旧顶层 `config` 已删除。
6. `timeline` 已迁入 `launch timeline ...`，旧顶层 `timeline` 已删除。
7. `account` 已按 `credential/query/trade-lock` 子域重组，旧短路径已删除。
8. `market` 已按 `source/data/dataset/stream` 子域重组，旧短路径已删除。

9. `order` runtime 路径已收敛到 `--launch <target>`；`--mode/--launch-id/--root` 已从普通 help 隐藏。
10. `account query current/balances/positions` 未指定 `--launch` 时默认查询当前 system runtime；`--mode/--launch-id/--root` 已从普通 help 隐藏。
11. `account trade-lock status` 默认面对当前 system runtime；`--root/--launch-id` 已从普通 help 隐藏。

## 我的最终结论

KairosPy 目前不是“命令太多”，而是顶层暴露了太多实现历史。真正应该稳定为产品域的，是：

```text
project / launch / account / order / market / catalog / system
```

`credential`、`config`、`timeline` 已下沉；`strategy` 当前已删除；`reference` 已产品化为 `catalog`；`launch system` 已收敛到顶层 `system`。

这样收敛后，CLI 的第一屏会表达 KairosPy 的真实产品模型：建立 workspace，配置账户和 catalog，准备 market data，launch 策略/runtime，查看 account/order/system/timeline。实现层仍可以保持现有 facade 和 service，不需要大规模重写。
