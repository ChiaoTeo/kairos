# 产物写入点梳理

本文档梳理当前代码中会写入仓库或 `.kairos` 数据的路径。重点是区分：

- run instance 的输出文件
- run group / daemon 的运行状态
- workspace 级操作状态
- 领域数据存储

目的是在继续调整 `timeline`、`view`、`artifact` 格式之前，先明确“谁负责写、写到哪里、通过什么入口写”。

## 术语

- **Run instance 输出**：某一次具体运行实例目录下的文件，例如
  `.kairos/runs/<mode>/<run-id>/instances/<instance-id>/`。
- **Run group 状态**：`.kairos/runs/<mode>/<run-id>/` 下指向或镜像当前实例的状态文件。
- **Workspace 状态**：`.kairos/state`、`.kairos/accounts`、`.kairos/orders`、profiles、操作日志和本地 workspace 配置。
- **领域数据存储**：非 run artifact 的持久化数据，例如市场数据集、reference 快照、运行恢复状态。

## Run Instance 输出

### `RunOutput`

源码：`kairospy/application/system/artifacts/output.py`

当前定位：run instance 输出的上层统一写入入口。

写入：

- `summary.json`
- `config.normalized.json`
- `metrics.json`
- `timeline.jsonl`
- `account/current.json`
- 当 `write_legacy_jsonl=True` 时，可选写入根目录 legacy JSONL：
  `equity.jsonl`、`fills.jsonl`、`trades.jsonl`、`intent_states.jsonl`、
  `decision_trace.jsonl`、`risk_snapshots.jsonl`
- 当 `write_legacy_jsonl=True` 时，可选写入 account legacy JSONL：
  `account/equity.jsonl`、`account/positions.jsonl`、`account/orders.jsonl`

调用方：

- `TradingSystem` 创建一个 `RunOutput` 和 `RunArtifactProjector`，在 runtime step 边界写 run artifacts。
- `TradingSystemLauncher` 和 `RunBuilder` 直接调用 `RunOutput.write_result` 写 run 结束汇总。

状态：

- run result、account current、timeline 的写入已基本收敛到 `RunOutput`。
- runtime 不再接收 artifact output，也不再调用 artifact 写入方法。
- `AccountCurrentProjector` 和 `TimelineProjector` 位于 system projectors 层，通过 `RuntimeStep` 与 `ViewStore` 做投影写入。

### `RunInstanceStore`

源码：`kairospy/infrastructure/artifacts/store.py`

当前定位：低层 instance 目录数据工具。

职责：

- 按 `namespace + 类型 + 名称` 映射到 instance 目录下的相对文件。
- 提供 `json(name).write/read`、`jsonl(name).append/replace/read`、`namespace(name)`。
- 通过 `jsonable` 统一 JSON 序列化。
- 校验名称，并通过 `path_for` 防止路径逃逸。

状态：

- 这一层刻意不理解 `account`、`timeline` 等业务产物含义。
- 适合作为未来 run instance 数据写入继续收敛的低层基础设施边界。

### `RunOutputLog` 和 `write_run_log_section`

源码：`kairospy/application/system/artifacts/logging.py`

写入：

- `run.log`

调用方：

- `RunBuilder`
- `TradingSystemLauncher`

状态：

- 目前仍独立于 `RunOutput`。
- 它也是 run instance 输出，但文本日志捕获和 JSON/JSONL 资源行为不同，可以暂时独立，后续也可以并入 `RunOutput` 的辅助能力。

## Run Daemon 和 Run Group 状态

### `_RunDaemonStore`

源码：`kairospy/application/system/control/daemon.py`

写入：

- group-level `current.json`
- instance-level `state.json`
- 可选镜像到 group-level `state.json`
- instance-level `summary.json`
- instance-level `events.jsonl`
- 镜像到 group-level `events.jsonl`

同文件中的其他写入辅助：

- `_RunHeartbeat` 会更新 `state.json` 中的 heartbeat 字段。
- `_active_current_instance` 可能把 stale instance 的 `state.json` 标记为 abandoned。
- `_write_json` 是 daemon 状态代码本地使用的 JSON 写入函数。

状态：

- 目前还没有使用 `RunInstanceStore` 或 `RunOutput`。
- 它和普通 run result 输出不同，因为它负责 daemon 生命周期、active instance 抢占、heartbeat、group mirror。
- 后续可以保留单独的 daemon/group state store，但底层 JSON/JSONL 写入能力可以考虑复用和 `RunInstanceStore` 相同的安全路径工具。

### `RunRegistry`

源码：`kairospy/application/system/control/registry.py`

写入：

- 少量 control/status mutation 路径会写 JSON 状态，例如 stop/control 更新。

读取/发现：

- `summary.json`
- `state.json`
- group `current.json`

状态：

- 主要是读取和发现，也包含部分状态修改。
- 它属于 run control / group state，不属于 run instance output。

## Run 启动辅助写入

### `RunBuilder._write_current`

源码：`kairospy/application/system/builder.py`

写入：

- group-level `current.json`

状态：

- 目前仍直接写 group current state。
- 这和 daemon group state 在概念上重叠，不属于 run instance 输出。

### `TradingSystemLauncher._write_artifacts`

源码：`kairospy/application/system/facade/trading.py`

写入：

- 直接调用 `RunOutput.write_result`。

状态：

- run 结束汇总写入已收敛到 `RunOutput`。

## Workspace 级状态

### `OperationJournal`

源码：`kairospy/application/system/workspace/operations.py`

写入：

- `.kairos/state/operations.jsonl`

调用方：

- project init
- account create/delete/snapshot
- order commands
- config profile commands
- run register/control commands
- market operations

状态：

- 这是 workspace 操作历史，不是 run output。
- 它确实是 append-only journal，`Journal` 这个命名在这里是合适的。

### `RunIndex`

源码：`kairospy/application/system/workspace/run_index.py`

写入：

- `.kairos/state/run-index.json`

状态：

- workspace 级 run config 注册表。
- 不属于 run artifact。

### `ProjectFacade.init`

源码：`kairospy/application/system/facade/project.py`

写入：

- `.kairos/kairos.toml`
- 创建 `.kairos/accounts`、`.kairos/state`、`.kairos/runs`、
  `.kairos/data`、`.kairos/reference`、`.kairos/orders/journals`
- 追加 `.kairos/state/operations.jsonl`

状态：

- workspace 初始化入口。

### `ConfigFacade`

源码：`kairospy/application/system/facade/config.py`

写入：

- `.kairos/state/selection.json`
- `.kairos/profiles/<name>.toml`
- operation journal entries

状态：

- workspace 配置和 profile 状态。

## Account / Order Workspace Journals

### `AccountFacade.create`

源码：`kairospy/application/system/facade/account.py`

写入：

- `.kairos/accounts/<account-id>.toml`
- operation journal entry

状态：

- account 配置写入入口。
- 这里包含本地 credential-sensitive 配置，和 run output 分开。

### `AccountFacade.snapshot`

源码：`kairospy/application/system/facade/account.py`

写入：

- `.kairos/accounts/journals/<account-id>.jsonl`
- operation journal entry

状态：

- workspace 级 account snapshot journal。
- 和 run instance 下的 `account/current.json` 是两类东西。

### `OrderFacade._write_journal`

源码：`kairospy/application/system/facade/order.py`

写入：

- `.kairos/orders/journals/<account-id>.jsonl`
- operation journal entry

状态：

- workspace 级 order command journal。
- 和 core 里的 `OrderJournal` 不同；core `OrderJournal` 是内存领域状态对象。

## 领域数据存储

### `DataStore`

源码：`kairospy/infrastructure/data/store.py`

写入：

- `.kairos/data/**/data.parquet`
- `.kairos/data/**/data.jsonl`
- dataset identifier marker files

状态：

- 市场/历史数据 lake writer。
- 和 run artifact 写入独立。

### `ReferenceStore`

源码：`kairospy/application/service/domain/reference/store.py`

写入：

- 配置的 reference root 下的 reference catalog snapshots 和 lifecycle events。

状态：

- reference 领域持久化。
- 和 run output 独立。

### `JsonExecutionStateStore`

源码：`kairospy/application/service/domain/execution/state.py`

写入：

- 调用方提供路径下的 execution coordinator recovery state。

状态：

- runtime/domain recovery state store。

### `JsonLiveRuntimeStateStore`

源码：`kairospy/application/system/resources/live_state.py`

写入：

- 调用方提供路径下的 live runtime state snapshot，通常是 live run 目录中的 `live_state.json`。

状态：

- live mode recovery state。
- 目前和 `RunOutput` 分离，因为它由 live runtime resources 加载和恢复。

## 当前问题

- run instance 输出已通过 `RunOutput` 和 system projectors 收敛，但 daemon/group state 仍然直接写 JSON/JSONL。
- runtime 不再包含 artifact output processor。
- `RunOutputLog` 仍然在 `RunOutput` 之外。
- workspace 级 journals 和 run instance outputs 在概念上是分开的，但它们各自还有本地 JSON/JSONL helper。
- `TimelineDataLoader` 已降级为 surface 兼容适配器，实际读取和数据派生由 `RunProjectionService` 承担。

## 建议的下一步收敛

1. 判断 daemon/group state 是否需要自己的低层 store wrapper，并复用与 `RunInstanceStore` 相同的 JSON/JSONL resource primitives。
2. 保持 workspace operation/account/order journals 和 run output 分离；它们是生命周期和保留策略不同的产品。
3. 在上述写入边界稳定后，再回头调整 `timeline.jsonl` 的内容模型。
