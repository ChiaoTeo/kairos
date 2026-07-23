# KairoSpy 专业实盘运行与控制面设计

状态：Draft  
日期：2026-07-22  
适用对象：`runtime/`、`execution/`、`governance/`、`market/`、`integrations/`、`surface/cli/`、`workspace/`、`RunConfig`

本文定义 KairoSpy 从当前“本地 CLI + runtime store + live daemon 雏形”推进到专业量化交易系统所需的运行时架构、控制面协议、状态机、持久化模型和分阶段改造计划。

目标不是把 CLI 做得更复杂，而是建立一个可审计、可恢复、可远程/本地一致控制的实盘运行平面：

```text
CLI / UI / automation
  -> OperatorCommandBus
  -> LiveRunDaemon
  -> RuntimeServices
  -> DurableOrderOutbox
  -> ExecutionGateway
  -> Venue

Venue events / fills / account state
  -> Ingestion
  -> RuntimeStore
  -> Ledger / Reconciliation
  -> Strategy Views / Governance Artifacts
```

## 1. 当前状态判断

当前代码已经有若干正确的基础：

- `kairospy/runtime/live_daemon.py`：`LiveRunDaemon` 定义长驻 live run 的 `start/recover/status/stop` 和故障降级边界。
- `kairospy/runtime/store/runtime_store.py`：`SQLiteRuntimeStore` 持久化订单、outbox、ledger facts、runtime state。
- `kairospy/execution/outbox.py`：`DurableOrderCommandService` 和 `DurableOrderDispatcher` 已具备 durable command 雏形。
- `kairospy/governance/kill_switch.py`：kill switch 状态已经可写入 runtime store。
- `kairospy/runtime/service_supervisor.py`：`AsyncServiceSupervisor` 管理异步服务生命周期、故障和重启。
- `kairospy/surface/product.py`：`run live <start|recover|status|stop|pause|resume|reduce-only|clear-reduce-only|cancel-all|reconcile|commands> --run-id` 已经按 `run_id` 隔离 runtime root/database/state key；顶层 `run status/stop/pause/resume/reduce-only/clear-reduce-only/cancel-all/reconcile/commands` 作为 mode-neutral 用户入口复用同一控制面。

当前实盘长驻运行控制闭环状态：

- stop、pause/resume、reduce-only、cancel-all、reconcile、kill-switch、reload-risk-limits 和 status snapshot 已进入 durable operator command bus，并由 daemon 写回 ack。
- daemon heartbeat、PID、host、进程版本、config hash、stale status 和 account-lock lease 冲突 fail-closed 已接入 status。
- `run live start/recover --config` 已能从 RunConfig 装配 strategy-run、market feed 和 outbox dispatcher，测试注入只保留给单元测试。
- 周期 reconciliation、outbox dispatcher、risk monitor 和 optional fill ingestion service 已统一纳入 provider-bound 长驻 daemon；Binance spot/futures/inverse/options listen-key user stream 已作为 provider fill source 接入。
- `cancel_all` 对 provider-bound runtime 会进入 reduce-only，并通过 `ExecutionCoordinator.cancel_all_orders(...)` 取消 runtime store 中本地已知 working orders；reconciliation 已能把 venue 侧未知 open order 明确标记为 `unknown_external_open_order_ids` 并纳入 risk monitor blocking。
- 启动恢复、外部订单确认、reconciliation、kill switch 和风控已经通过 runtime store、daemon managed services、operator command ack 和 status/metrics 控制面串联；tick-level market event age 已通过 Live View freshness evidence 纳入 runtime metrics。

## 2. 设计原则

### 2.1 CLI 是控制面客户端，不是运行时 owner

CLI 只负责：

- 解析用户命令。
- 写入 operator command。
- 读取 runtime state / command ack / heartbeat。
- 渲染状态和审计结果。

CLI 不负责：

- 持有策略对象。
- 持有 connector SDK。
- 直接操作 live daemon 内存。
- 绕过 runtime store 修改执行事实。

### 2.2 所有跨进程控制都必须持久化

任何会影响实盘行为的控制动作都必须进入持久命令流：

- stop。
- pause/resume。
- reduce-only。
- kill switch。
- cancel all。
- reload risk limits。
- request snapshot。
- manual order resolution。

命令必须有：

- `command_id`。
- `run_id`。
- `type`。
- `payload`。
- `actor`。
- `reason`。
- `idempotency_key`。
- `status`。
- `ack/error`。
- `created_at/accepted_at/completed_at`。

### 2.3 Desired state 与 observed state 分离

专业系统不能把“用户想让系统停止”和“系统已经停止”混成一个字段。

推荐模型：

```text
desired_state:
  running | paused | reduce_only | stopping | stopped

observed_state:
  created | starting | recovering | ready | running | degraded
  reduce_only | stopping | stopped | failed | stale | unknown_external_state
```

CLI `status` 必须同时展示：

- 用户最后下达的 desired state。
- daemon 最后上报的 observed state。
- heartbeat 是否新鲜。
- 未完成 operator commands。
- 未恢复 orders。
- kill switch / reduce-only 状态。

### 2.4 实盘默认 fail closed

以下情况必须禁止新开仓：

- runtime heartbeat 过期。
- 启动恢复未完成。
- reconciliation 不匹配。
- 存在 `SUBMITTING/UNKNOWN/CANCELLING` 订单。
- kill switch active。
- 行情 freshness 不达标。
- risk limits 未加载或 hash 不匹配。
- promotion/readiness evidence 不匹配。
- account lock 不属于当前 runtime。

## 3. 目标组件

### 3.1 OperatorCommandBus

新增 owner：`runtime/control.py` 或 `runtime/operator_commands.py`。

职责：

- 定义 operator command 类型和状态。
- 提供命令写入、claim、ack、complete、fail、list API。
- 保证幂等和跨进程可见。
- 不执行 broker 操作。
- 不承载策略逻辑。

建议公开类型：

```python
class OperatorCommandType(StrEnum):
    STOP = "stop"
    PAUSE_NEW_ORDERS = "pause_new_orders"
    RESUME = "resume"
    SET_REDUCE_ONLY = "set_reduce_only"
    CLEAR_REDUCE_ONLY = "clear_reduce_only"
    KILL_SWITCH = "kill_switch"
    RESET_KILL_SWITCH = "reset_kill_switch"
    CANCEL_ALL = "cancel_all"
    RELOAD_RISK_LIMITS = "reload_risk_limits"
    REQUEST_STATUS_SNAPSHOT = "request_status_snapshot"
    REQUEST_RECONCILIATION = "request_reconciliation"

class OperatorCommandStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
```

### 3.2 LiveRunRegistry

新增 owner：`runtime/live_registry.py`。

职责：

- 记录每个 `run_id` 的 runtime root、runtime database、pid、host、process id、version、config hash、heartbeat。
- 提供 stale detection。
- 支持 CLI/status 不创建 runtime database 的 read-only 查询。

### 3.3 LiveRunDaemon

现有 `LiveRunDaemon` 扩展为长驻进程 owner。

新增职责：

- 启动时注册 process identity。
- 定期 heartbeat。
- 消费 operator commands。
- 管理 runtime services。
- 在 stop/reduce-only/kill-switch/cancel-all 等命令上写回 command ack。
- 统一持久化 observed state。

仍然不负责：

- connector discovery。
- credential resolution。
- strategy implementation。
- order state machine 规则。
- governance artifact repository。

### 3.4 RuntimeServices

实盘 daemon 下至少应有这些服务：

| Service | Owner | 说明 |
|---|---|---|
| `strategy-run:<run_id>` | `runtime/live_daemon.py` + `runtime/kernel.py` | 调用 `LiveRunKernelService` 运行策略调度 |
| `market-feed:<name>` | `market/` + `integrations/live_ports.py` | 维护 live market event source |
| `order-dispatcher:<account>` | `execution/outbox.py` | 消费 durable order outbox 并提交 venue |
| `fill-ingestion:<account>` | `execution/ingestion.py` | 拉取/接收 fills 并写 runtime store |
| `account-reconciliation:<account>` | `governance/reconciliation.py` | 周期性本地/venue 对账 |
| `risk-monitor:<run_id>` | `risk/` + `governance/` | 运行时风控、PnL、仓位和行情检查 |
| `command-consumer:<run_id>` | `runtime/control.py` | 消费 operator commands |
| `heartbeat:<run_id>` | `runtime/live_registry.py` | 写 daemon heartbeat |

服务由 `AsyncServiceSupervisor` 管理，critical 服务失败时进入 reduce-only 或 failed。

## 4. Runtime Store 扩展设计

当前 `SQLiteRuntimeStore` 已有 `runtime_state`、`orders`、`order_outbox`、`execution_events`、`ledger_transactions` 等表。建议新增以下表。

### 4.1 `operator_commands`

```sql
CREATE TABLE operator_commands(
    command_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claimed_by TEXT,
    claimed_at TEXT,
    accepted_at TEXT,
    completed_at TEXT,
    expires_at TEXT,
    result_json TEXT,
    error_type TEXT,
    error_message TEXT,
    UNIQUE(run_id, idempotency_key)
);
```

索引：

```sql
CREATE INDEX operator_commands_pending_idx
    ON operator_commands(run_id, status, created_at);
CREATE INDEX operator_commands_created_idx
    ON operator_commands(run_id, created_at);
```

### 4.2 `runtime_heartbeats`

```sql
CREATE TABLE runtime_heartbeats(
    run_id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL,
    process_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    host TEXT NOT NULL,
    version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    observed_state TEXT NOT NULL,
    desired_state TEXT NOT NULL,
    state_json TEXT NOT NULL
);
```

### 4.3 `runtime_incidents`

```sql
CREATE TABLE runtime_incidents(
    incident_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    details_json TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT,
    close_reason TEXT
);
```

### 4.4 `risk_runtime_state`

```sql
CREATE TABLE risk_runtime_state(
    run_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, scope_key)
);
```

### 4.5 `market_runtime_state`

```sql
CREATE TABLE market_runtime_state(
    run_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, source_key)
);
```

## 5. Operator Command 生命周期

### 5.1 写入

CLI 调用：

```text
kairospy run live stop --run-id venue-a-live --reason "maintenance"
```

转换为：

```json
{
  "command_type": "stop",
  "run_id": "venue-a-live",
  "payload": {
    "graceful": true,
    "timeout_seconds": 30
  },
  "actor": "cli:<user>@<host>",
  "reason": "maintenance",
  "idempotency_key": "stop:venue-a-live:<date-or-user-supplied-key>"
}
```

### 5.2 Claim

daemon 的 command consumer 使用 `BEGIN IMMEDIATE` claim 一条 pending command：

```text
pending -> claimed
```

只有当前 `process_id` 可以继续更新该 command。

### 5.3 Accept / Reject

daemon 校验：

- command 是否属于当前 `run_id`。
- command 是否过期。
- actor/reason 是否非空。
- 当前 observed state 是否允许该命令。
- 是否需要 live confirmation 或 elevated operator policy。

结果：

```text
claimed -> accepted
claimed -> rejected
```

### 5.4 Run / Complete / Fail

命令开始执行：

```text
accepted -> running
```

执行成功：

```text
running -> succeeded
```

执行失败：

```text
running -> failed
```

所有 terminal command 都写 `result_json` 或 `error_type/error_message`。

## 6. 命令语义详细设计

### 6.1 `stop`

语义：

- 设置 desired state 为 `stopping`。
- 停止策略调度。
- 停止产生新 order commands。
- 等待 outbox dispatcher 完成当前已 claim 的提交或进入 recoverable unknown。
- 停止 market feed。
- 释放 account lock。
- observed state 写为 `stopped`。

超时策略：

- graceful timeout 内未完成，写 incident。
- 如果仍有外部未知订单，进入 `unknown_external_state`，禁止下次 start 直接运行。
- `force-stop` 通过 durable STOP command payload 表达，daemon 在 graceful timeout 后写 `runtime-force-stop:<run_id>` incident 并完成控制面停止；仍不由 CLI 直接 `kill -9`。

### 6.2 `pause_new_orders`

语义：

- 策略可以继续读行情和更新 state。
- 禁止非 reduce-only 新订单进入 durable outbox。
- 不取消现有挂单。
- `OrderView` 和 `BudgetView` 应展示 `blocked_reason=pause_new_orders`。

### 6.3 `resume`

语义：

- 清除 pause 状态。
- 仅当 kill switch 未触发、risk limits 正常、reconciliation 正常、market freshness 正常时恢复新订单。
- 如果任一 gate 失败，命令 `rejected` 或 `failed`，并写明 gate evidence。

### 6.4 `set_reduce_only`

语义：

- 禁止扩大风险的订单。
- 允许严格降低当前风险暴露的订单。
- 写入 runtime state 和 risk state。
- 重启后必须继续生效。

### 6.5 `kill_switch`

语义：

- 设置 kill switch active。
- 设置 reduce-only。
- 对目标 account/venue 发起 cancel-all 或按本地 open orders 逐个 cancel。
- 写入 cancel 结果、失败、未确认订单。
- 若 cancel 状态无法确认，进入 `unknown_external_state`。

该命令优先级最高。即使普通 command consumer backlog 很长，也必须优先处理。

### 6.6 `cancel_all`

语义：

- 不一定触发 kill switch。
- 取消当前 run/account/scope 下 open orders。
- 需要明确 scope：`run_id`、`account`、`strategy_id`、`instrument`、`venue`。
- 默认不取消其他 run 的订单，除非 operator policy 显式允许 account-wide cancel。

### 6.7 `reload_risk_limits`

语义：

- 从 RunConfig 或指定 risk config artifact 加载新限制。
- 校验 schema、hash、effective time。
- 写入 risk runtime state。
- 对当前仓位和挂单做立即评估。
- 如果新限制使当前状态违规，进入 reduce-only 或 pause。

### 6.8 `request_status_snapshot`

语义：

- daemon 主动写一份完整 snapshot：
  - heartbeat。
  - services。
  - order summary。
  - outbox summary。
  - market state。
  - risk state。
  - reconciliation state。
  - kill switch state。
  - latest incidents。

CLI `status --fresh` 可以先写该命令，再等待 ack。

## 7. Heartbeat 与 stale detection

### 7.1 Heartbeat 内容

每次 heartbeat 写：

```json
{
  "run_id": "venue-a-live",
  "runtime_id": "venue-a-live",
  "process_id": "host:pid:start_time:uuid",
  "pid": 12345,
  "host": "trading-box-1",
  "version": "0.x.y",
  "config_hash": "...",
  "started_at": "...",
  "heartbeat_at": "...",
  "observed_state": "running",
  "desired_state": "running",
  "services": [...],
  "open_orders": 3,
  "pending_outbox": 0,
  "market_freshness_seconds": 0.12,
  "reconciliation": "matched",
  "kill_switch": false
}
```

### 7.2 Status 判定

CLI 读 status 时：

```text
if no runtime db:
  not_started
elif no heartbeat:
  unknown
elif now - heartbeat_at > stale_after_seconds:
  stale
elif observed_state == running:
  running
else:
  observed_state
```

`runtime_state` 里的最后 phase 不能单独作为 running 证据。

### 7.3 Lease 与 account lock

现有 `account_locks` 已有 lease。需要：

- daemon heartbeat 同步刷新 account lock。
- account lock 过期后，新 daemon 可以尝试 recover。
- 旧 daemon 如果恢复写 heartbeat 发现 lease 不属于自己，必须停止交易并进入 failed/stale conflict。

## 8. LiveRunDaemon 详细流程

### 8.1 Start

```text
1. Discover ProjectConfig
2. Load RunConfig
3. Resolve secrets through credential refs
4. Freeze resolved config artifact
5. Create runtime root/database
6. Register process identity
7. Acquire account locks
8. Run startup recovery
9. Run reconciliation
10. Run readiness probes
11. Build live runtime components
12. Start supervised services
13. Write observed_state=running
14. Enter command/heartbeat loop
```

### 8.2 Recover

```text
1. Read previous runtime database
2. Check heartbeat stale or same-process restart
3. Re-acquire account lock
4. Recover unresolved orders from venue
5. Ingest missing fills/accounting events
6. Reconcile account and ledger
7. Rebuild strategy views from durable facts
8. Resume only if desired state permits running
```

### 8.3 Stop

```text
1. Accept stop command
2. desired_state=stopping
3. Pause new orders
4. Stop strategy service
5. Drain or park outbox dispatcher
6. Stop ingestion services
7. Stop market services
8. Write final snapshot
9. Release locks
10. observed_state=stopped
11. Complete stop command
```

### 8.4 Critical fault

如果 critical service 失败：

```text
1. observed_state=reduce_only or failed
2. Set reduce-only gate
3. Write runtime incident
4. Optionally trigger kill switch based on policy
5. Keep command consumer alive if possible
6. Require operator command to resume or stop
```

## 9. Durable Order Outbox 改造

当前 `DurableOrderCommandService` 已有 submit-before-gateway 模型。专业化需要补齐：

### 9.1 策略只能写 outbox

Live profile 下：

- 策略输出 `EconomicIntent`。
- risk/planner 生成 `OrderRequest`。
- `DurableOrderCommandService.submit()` 写 `order_outbox`。
- `DurableOrderDispatcher` 异步提交 venue。
- fill ingestion 更新 order status 和 ledger。

不允许策略同步直接调用 execution gateway。

### 9.2 Outbox command 类型扩展

当前 outbox 主要是 submit order。需要支持：

- submit single order。
- submit combo order。
- cancel order。
- replace/amend order。
- cancel all scoped orders。

建议从 `order_outbox` 演进为更通用的 `execution_commands`，或保留兼容表并新增 `command_kind`。

### 9.3 幂等与恢复

每个 execution command 必须包含：

- `command_id`。
- `client_order_id`。
- `intent_id`。
- `strategy_id`。
- `scope_key`。
- `idempotency_key`。
- `request_hash`。

重启时：

- `PENDING` 可重新 claim。
- `DISPATCHING` 必须 venue recovery。
- `UNKNOWN` 必须 venue recovery。
- terminal 状态不可重放。

## 10. 订单恢复与 Reconciliation

### 10.1 启动恢复硬门禁

启动时必须处理：

- `SUBMITTING`：可能已发到 venue 但未持久化 ack。
- `UNKNOWN`：本地无法确认外部状态。
- `CANCELLING`：cancel 已发出但未确认。
- `ACKNOWLEDGED`：venue 有挂单或已成交。
- `PARTIALLY_FILLED`：可能有后续 fill。

恢复结果：

```text
all recovered -> continue
some unresolved -> unknown_external_state
reconciliation mismatch -> unknown_external_state or reduce_only
```

### 10.2 Venue recovery contract

每个 execution connector 应提供：

```python
class OrderRecoveryPort(Protocol):
    def recover_order(self, account: AccountRef, client_order_id: str, venue_order_id: str | None) -> OrderRecoveryEvidence: ...
    def open_orders(self, account: AccountRef) -> tuple[VenueOrder, ...]: ...
    def recent_fills(self, account: AccountRef, since: datetime | None) -> tuple[VenueFill, ...]: ...
```

恢复 evidence 需要说明：

- venue 是否找到了订单。
- 当前 venue order status。
- 累计成交数量。
- 最新 fill cursor。
- 是否需要人工处理。

### 10.3 Reconciliation 分类

Reconciliation 不只是 matched/unmatched，应分类：

| Kind | 行为 |
|---|---|
| `matched` | 可运行 |
| `cash_diff` | 进入 pause 或 reduce-only，视阈值 |
| `position_diff` | 禁止新开仓，要求恢复或人工确认 |
| `open_order_diff` | 进入 unknown external state |
| `stale_account_state` | 暂停新订单 |
| `venue_unreachable` | reduce-only 或 stop，视策略 |

## 11. Kill Switch 与 Reduce-only

### 11.1 Kill switch 状态

kill switch 需要从单一 runtime state 扩展为 scoped state：

```text
scope:
  global
  run:<run_id>
  account:<account_ref>
  strategy:<strategy_id>
  instrument:<instrument_id>
```

状态字段：

- `triggered`。
- `reduce_only`。
- `triggered_by`。
- `reason`。
- `triggered_at`。
- `scope`。
- `cancel_attempts`。
- `cancel_failures`。
- `reset_by/reset_reason/reset_at`。

### 11.2 Reset policy

reset 不能只是普通方法调用。必须通过 operator command：

```text
reset_kill_switch
```

要求：

- actor。
- reason。
- scope。
- optionally approval evidence。
- reconciliation matched。
- risk state healthy。
- no unresolved orders。

## 12. 运行时风控服务

### 12.1 风控层级

| Scope | 示例 |
|---|---|
| account | 最大杠杆、最大净敞口、最大日损 |
| strategy | 最大 capital allocation、最大订单频率 |
| instrument | 最大持仓、最大单笔订单、禁用品种 |
| venue | 最大 open orders、rate limit、维护窗口 |
| market | 最大价格偏离、最小 liquidity、行情 freshness |

### 12.2 风控状态

风险服务每个周期写：

```json
{
  "scope_key": "strategy:carry-v1",
  "status": "ok|warning|blocked|reduce_only",
  "limits_hash": "...",
  "checks": [
    {
      "name": "max_position",
      "passed": true,
      "value": "1.2",
      "limit": "2.0"
    }
  ],
  "blocked_reason": null,
  "updated_at": "..."
}
```

### 12.3 下单前 gate

`DurableOrderCommandService.submit()` 前必须检查：

- application operational。
- not paused。
- kill switch inactive or reduce-only order。
- risk service state fresh。
- order-specific risk approval。
- market freshness。
- account reconciliation freshness。

## 13. Market runtime plane

### 13.1 Market service 状态

每个 live source 写：

- source key。
- sequence。
- last event time。
- last receive time。
- freshness seconds。
- reconnect count。
- gap count。
- snapshot version。
- subscription hash。
- quality status。

### 13.2 Gap / reconnect 策略

如果行情 gap：

```text
latest-only strategies:
  pause until fresh snapshot rebuild

ordered-event strategies:
  require sequence recovery or stop

execution-sensitive strategies:
  reduce-only if market state unknown
```

### 13.3 Strategy Context

策略仍只读取 `MarketView`，不能看到 connector raw object。

`MarketView` 应继续承载：

- data binding。
- event window。
- available time。
- freshness。
- source state hash。

## 14. 可观测性与审计

### 14.1 必需 artifacts

每个 live run 至少写：

- resolved RunConfig。
- startup readiness report。
- recovery report。
- reconciliation report。
- service timeline。
- operator command log。
- order timeline。
- fill timeline。
- risk state snapshots。
- market source snapshots。
- incident log。
- final run manifest。

### 14.2 Metrics

建议最小 metrics：

- heartbeat age。
- market freshness。
- order submit latency。
- ack latency。
- fill ingestion latency。
- outbox pending count。
- unresolved order count。
- reconciliation age。
- command backlog。
- service restart count。
- risk blocked count。
- run health status：`ok | inactive | stopping | reduce_only | stale | blocking | failed`。

当前已完成 `run metrics` / `run live metrics` 的控制面指标汇总、独立 artifact 导出和一组服务内部时序指标：heartbeat age/stale、operator command backlog、service count/restart/failure、risk blocked/reason count、unresolved orders、orders requiring recovery、open incidents、health status、outbox pending/dispatching/unknown/backlog、order submit/ack latency、fill ingestion latency、market freshness gate/status、Live View monitor update age、tick-level market event age 和 channel failure count。metrics 可以写成 JSON 文件，也可以通过 `--prometheus` 写成 Prometheus text format。

### 14.3 CLI status 输出

`kairospy run live status --run-id X` 应展示：

```text
Run: X
Observed: running
Desired: running
Heartbeat: fresh, 0.7s ago
Runtime DB: ...
Config Hash: ...
Services: 7 running, 0 failed
Market: fresh, 0.12s
Outbox: 0 pending, 0 unknown
Orders: 3 open, 0 unresolved
Risk: ok
Reconciliation: matched, 4.2s ago
Kill Switch: inactive
Pending Commands: 0
Incidents: 0 open
```

## 15. 配置与 Secrets

### 15.1 RunConfig 扩展

`configs/runs/live.toml` 推荐字段：

```toml
[run]
name = "venue-a-live"
mode = "live"
workspace = "alpha"
entrypoint = "strategies.carry:build"

[strategy]
spec = "strategies.carry:spec"

[bindings]
account = "binance_live_main"
market = ["btc_usdt_book"]

[bindings.live_views.btc_usdt_book]
dataset = "market.binance.btcusdt.orderbook"
live_view_id = "..."
supervise_services = true

[live]
provider = "binance"
bind_provider = true
execution_driver = "spot"

[control]
heartbeat_seconds = 1.0
command_poll_seconds = 0.25
stale_after_seconds = 5.0
graceful_stop_timeout_seconds = 30.0

[risk]
limits = "configs/risk/live-limits.toml"
reloadable = true

[evidence]
readiness = ".kairos/governance/readiness/live.json"
promotion = ".kairos/governance/promotion/live.json"
```

`[strategy]` 只负责引用或声明 `StrategySpec`。`stop_policy` 不写入 RunConfig；
停机动作规则必须来自策略代码里的 `StrategySpec.default_stop_policy`，再由治理层施加系统风险地板。
这样可以避免 operator 修改 TOML 时误把正常 stop 改成 flatten 之类的高风险动作。

### 15.2 Secrets

Secrets 继续只通过 credential refs 解析：

- 不写入 RunConfig。
- 不写入 RunManifest。
- 不写入 command payload。
- 不写入 runtime_state。

Resolved config artifact 只能写 credential ref 和 hash，不写 secret 值。

## 16. CLI 设计

### 16.1 Start

```bash
kairospy run live start \
  --config configs/runs/binance-live.toml \
  --run-id binance-btc-live \
  --confirm-live
```

说明：

- `--run-id` 仍必填。
- `--config` 用于装配服务。
- 默认 spawn 一个独立 daemon 进程，父 CLI 返回 `run_id`、pid、runtime database 和 log file。
- `--foreground` 用于调试或受外部 supervisor 托管时在当前 CLI 进程内运行 daemon。
- `--duration-seconds` 是 bounded foreground run，用于测试和演练。

### 16.2 Status

```bash
kairospy run status --run-id binance-btc-live
kairospy run live status --run-id binance-btc-live
kairospy run live status --run-id binance-btc-live --fresh --wait 5
```

`run status` 是 mode-neutral 用户入口：优先读取 live daemon runtime state/heartbeat，找不到 live runtime 时退回 `.kairos/run/<run_id>/manifest.json`。`run live status` 保留为 live runtime 的直接控制入口。

`--fresh` 会写 `request_status_snapshot` 并等待 command ack。

### 16.3 Stop

```bash
kairospy run stop --run-id binance-btc-live --actor alice --reason "scheduled maintenance" --timeout-seconds 30
kairospy run force-stop --run-id binance-btc-live --actor alice --reason "emergency stop" --timeout-seconds 1
kairospy run live stop \
  --run-id binance-btc-live \
  --reason "scheduled maintenance"
```

`run stop` 是用户主入口，当前对 live runtime 提交 durable `STOP` operator command；`run live stop` 是等价的 live-specific surface。`--timeout-seconds` 和 `--force` 会进入 STOP payload，由 daemon 消费并写回 command result。

### 16.4 Pause / Resume

```bash
kairospy run pause --run-id binance-btc-live --reason "market data stale"
kairospy run resume --run-id binance-btc-live --reason "feed recovered"
kairospy run live pause --run-id binance-btc-live --reason "market data stale"
kairospy run live resume --run-id binance-btc-live --reason "feed recovered"
```

### 16.5 Reduce-only / Kill switch

```bash
kairospy run reduce-only --run-id binance-btc-live --reason "risk review"
kairospy run clear-reduce-only --run-id binance-btc-live --reason "approved"
kairospy run live reduce-only --run-id binance-btc-live --reason "risk review"
kairospy run live clear-reduce-only --run-id binance-btc-live --reason "approved"
kairospy run live kill-switch --run-id binance-btc-live --reason "unexpected exposure"
kairospy run live reset-kill-switch --run-id binance-btc-live --actor alice --reason "reconciled"
```

### 16.6 Commands

```bash
kairospy run commands --run-id binance-btc-live
kairospy run live commands --run-id binance-btc-live
```

### 16.7 Metrics / Incidents

```bash
kairospy run metrics --run-id binance-btc-live
kairospy run live metrics --run-id binance-btc-live
kairospy run metrics --run-id binance-btc-live --output .kairos/metrics/binance-btc-live.json
kairospy run live metrics --run-id binance-btc-live --prometheus --output .kairos/metrics/binance-btc-live.prom
kairospy run incidents --run-id binance-btc-live
kairospy run close-incident --run-id binance-btc-live --incident-id runtime-health:binance-btc-live --reason "recovered"
```

### 16.8 Export

```bash
kairospy run export --run-id binance-btc-live
kairospy run live export --run-id binance-btc-live --output .kairos/exports/binance-btc-live
```

export 写出 `manifest.json`、`status.json`、`metrics.json`、`commands.json`、`incidents.json` 和 `runtime_state.json`，manifest 记录每个文件的 hash。
如果 live runtime root 下存在 `runtime.jsonl`，export 也会复制为 `runtime_log.jsonl` 并纳入 manifest/hash。

### 16.9 Structured Runtime Log

live daemon 默认在 `.kairos/runtime/live/<run-id>/runtime.jsonl` 写 append-only JSONL 运维日志。每行包含：

- `schema_version`
- `timestamp`
- `run_id`
- `level`
- `event`
- `payload`
- `record_hash`

已接入的事件包括 spawn requested/spawned/failed、daemon start requested/started/failed、stop requested/stopped/force-stopped、heartbeat、reduce-only 和 fail-closed。

### 16.10 Deployment Examples

`examples/deploy/live-run/` 提供长期运行部署模板：

- systemd：`systemd/kairos-live-run.service`
- launchd：`launchd/com.example.kairos.live-run.plist`
- Docker：`docker/Dockerfile` 和 `docker/entrypoint.sh`
- Kubernetes：`kubernetes/configmap.yaml` 和 `kubernetes/deployment.yaml`

这些模板均使用 `run live start --foreground`，让外部 supervisor 管进程生命周期和重启策略；另一个 CLI 进程继续通过 `run status`、`run stop`、`run metrics` 和 `run export` 管理同一个 `run_id`。

## 17. Python API 设计

建议提供：

```python
from kairospy.runtime.control import OperatorCommandBus

bus = OperatorCommandBus(store)
command = bus.submit(
    run_id="binance-btc-live",
    command_type="pause_new_orders",
    payload={},
    actor="ops/alice",
    reason="venue maintenance",
    idempotency_key="pause:venue-maintenance:2026-07-22",
)
```

以及：

```python
from kairospy.runtime.live_registry import LiveRunRegistry

registry = LiveRunRegistry(store)
status = registry.status("binance-btc-live")
```

## 18. 测试计划

### 18.1 Unit tests

- command bus idempotency。
- command status transition。
- stale heartbeat 判定。
- run_id scoped command isolation。
- kill switch reset policy。
- risk state freshness gate。
- market freshness gate。

### 18.2 Integration tests

- CLI stop 写 command，daemon 消费并 stopped。
- pause 后策略继续运行但不产生 outbox。
- reduce-only 后非 reducing order 被拒绝。
- kill switch cancel-all 写完整 evidence。
- heartbeat stale 时 status 显示 stale。
- account lock conflict 时新 daemon 拒绝启动或 recover。

### 18.3 Fault injection

- command claimed 后进程崩溃。
- order DISPATCHING 后崩溃。
- ack 后、持久化前崩溃。
- fill duplicate。
- fill late arrival。
- cancel race。
- venue timeout。
- market feed gap。
- reconciliation mismatch。

### 18.4 Soak tests

- paper/testnet 6 小时运行。
- market reconnect campaign。
- order submit/cancel/fill lifecycle。
- restart recovery drill。
- kill switch drill。
- command backlog drill。

## 19. 分阶段改造计划

### Phase 1：控制面最小闭环

目标：把 `stop_requested` 替换为正式 operator command。

工作：

1. 新增 `runtime/control.py`。
2. 新增 `operator_commands` schema migration。
3. 为 `SQLiteRuntimeStore` 增加 command bus 方法。
4. `run live stop/status` 改为写/读 command bus。
5. `LiveRunDaemon` 增加 command consumer，支持 `stop`。
6. 保留旧 `stop_requested` 作为兼容投影，最终由 command bus 派生。

验收：

- 一个 CLI 进程能让另一个 daemon 进程停止。
- stop command 有 terminal ack。
- 不同 `run_id` 命令隔离。

### Phase 2：Heartbeat 与进程注册

目标：CLI 能判断正在运行、已停止、崩溃、stale。

工作：

1. 新增 `runtime/live_registry.py`。
2. 新增 `runtime_heartbeats` schema。
3. daemon start 注册 process identity。
4. daemon 周期 heartbeat。
5. status 使用 heartbeat freshness 判定。
6. 已完成：status heartbeat 暴露 config hash/version/pid/host。
7. 已完成：account lock heartbeat 与 daemon heartbeat 对齐；续租失败时 daemon fail-closed，status 保留 failed 原因。

验收：

- kill daemon 后，status 在 stale threshold 后显示 stale。
- missing run status 不创建 runtime db。
- config hash/version/pid/host 可见。

### Phase 3：RunConfig 驱动长驻 daemon 装配

目标：`run live start --config ... --run-id ...` 能真实装配服务。

工作：

1. 已完成：扩展 CLI parser：`run live start --config`。
2. 已完成：从 RunConfig 构造 `LiveRuntimeComponents`。
3. 已完成：构造 `LiveRunKernelService`。
4. 已完成：构造 market feed managed services。
5. 已完成：构造 outbox dispatcher service。
6. 已完成：构造 reconciliation monitor services；fill ingestion 通过 provider-bound `LiveRuntimeComponents.fill_ingestion_service()` 接入。
7. 已完成产品路径的 RunConfig 装配；`_managed_services` 仅保留为测试注入口。

验收：

- live daemon 可从项目 RunConfig 启动。
- service snapshots 已覆盖 strategy/market/outbox/reconciliation。
- stop 能按顺序停止所有 services。

### Phase 4：执行链路 outbox-only

目标：live 策略所有订单都走 durable outbox。

工作：

1. 已完成：live binding 默认使用 `DurableOutboxCommandSubmitter`。
2. 已完成：默认 `dispatch_immediately = false`，策略 submit 只入 durable outbox。
3. 已完成：`DurableOrderDispatcherService` 作为 managed service 持续 drain outbox。
4. 已完成：outbox/order 状态持久化进入 runtime store，供 `OrderView/IntentView` 读取。
5. 已完成：现有 crash recovery 覆盖 `DISPATCHING/UNKNOWN`。

验收：

- 策略产生订单后，先落 runtime store，再提交 venue。
- crash 后不会重复提交不同内容订单。
- unresolved order 会阻止新开仓。

### Phase 5：恢复、reconciliation、kill switch 闭环

目标：实盘启动和事故处理 fail closed。

工作：

1. 已完成：`OrderRecoveryPort` 已由 provider-bound `LiveRuntimeComponents` 使用。
2. 已完成：`KairosApplication.start()` 启动时恢复 unresolved orders，未恢复进入 `unknown_external_state`。
3. 已完成：`ReconciliationMonitorService` 周期写入 `reconciliation:<run_id>:<account>` 与 `reconciliation:last`。
4. 已完成：`run live kill-switch/reset-kill-switch` 写入 operator command，由 daemon ack。
5. 已完成：当前每个 live run 使用独立 runtime DB，kill switch state 对 `run_id` 天然隔离。
6. 已完成：reset 必须提供 actor/reason/reconciliation evidence。

验收：

- 未恢复订单导致 `unknown_external_state`。
- kill switch 重启后仍阻止非 reducing order。
- reset 必须有 actor/reason/reconciliation evidence。

### Phase 6：风控与行情运行平面

目标：运行时风控和行情 freshness 成为硬 gate。

工作：

1. 已完成基础门禁：`reconciliation:last.matched = false` 阻止非 reduce-only order 入 outbox。
2. 已完成基础门禁：kill switch active 或 application reduce-only 阻止非 reduce-only order。
3. 已完成基础行情门禁：RunConfig live market view 启动前必须通过 Live View freshness gate。
4. 已完成状态接入：daemon heartbeat/status 返回 heartbeat、operator command、run config binding 和聚合 `health` 摘要。
5. 已完成基础 risk reload：`run live reload-risk-limits` 写入 `risk_runtime:last` 并带 command ack。
6. 已完成 runtime risk monitor service：provider-bound daemon 周期写 `risk_monitor:<run_id>:last` / `risk_runtime:last`，对 kill switch、reconciliation mismatch、未知外部 open orders、unresolved orders、account-lock heartbeat failure 进入 blocking/reduce-only；不会覆盖 operator pause。
7. 已完成 optional fill ingestion service：`DurableFillIngestionService` 可消费 provider user-fill event source 的 `UserFillUpdate`，写入 durable execution/ledger/cursor，并作为 `fill-ingestion:<run_id>` managed service 受 daemon 监督；Binance spot/futures/inverse/options provider ports 已提供 listen-key websocket fill source。

验收：

- Live View freshness gate 不通过时拒绝启动配置化 market feed。
- reconciliation mismatch、未知外部 open order、kill switch active、reduce-only 均禁止新开仓。
- reload risk limits 有 command ack，并刷新 `risk_runtime:last.limits_hash`。
- provider-bound daemon 的 service snapshot 包含 `risk-monitor:<run_id>`，status 能读取其 durable risk state。
- provider 提供 user-fill event source 时，daemon service snapshot 包含 `fill-ingestion:<run_id>`，fill 会以 durable execution fact 写入 runtime store。
- `run live status` / `run status` 输出 `health` 摘要，能把 stale heartbeat、risk blocking、unresolved orders、reconciliation mismatch 和未知外部 open order 聚合成单一控制面判断。

### 当前 CLI

```bash
kairospy run live start --config configs/runs/live.toml --run-id binance-btc-live --confirm-live
kairospy run live status --run-id binance-btc-live
kairospy run live stop --run-id binance-btc-live --actor alice --reason "maintenance" --timeout-seconds 30
kairospy run live force-stop --run-id binance-btc-live --actor alice --reason "emergency stop" --timeout-seconds 1
kairospy run live kill-switch --run-id binance-btc-live --actor alice --reason "risk breach"
kairospy run live reset-kill-switch --run-id binance-btc-live --actor alice --reason "reconciled" --reconciliation-evidence "reconciliation:matched"
kairospy run live reload-risk-limits --run-id binance-btc-live --actor alice --reason "approved limits" --risk-limits-hash "sha256:..."
```

### Phase 7：运维体验与部署集成

目标：可以作为长期运行服务部署。

工作：

1. 已完成基础 metrics control surface、外部 metrics artifact export、结构化 runtime JSONL 日志、outbox pending 指标、order submit/ack latency、fill ingestion latency、runtime market freshness 指标与 tick-level market event age。
2. 已完成 status snapshot command 与 status health summary。
3. 已完成 runtime incident store、status 自动 open/close health incident、`run incidents` / `run close-incident` 与 `run live incidents` / `run live close-incident`。
4. 已完成 systemd/launchd/Docker/Kubernetes 部署示例：`examples/deploy/live-run/` 覆盖 foreground supervisor 模式、stop hook、status/readiness、metrics/export 操作入口。
5. 已完成 graceful stop timeout 和 force-stop 控制面：STOP command payload 支持 `timeout_seconds`/`force`，daemon 超时 force stop 会写 runtime incident。
6. 已完成 runtime control-plane artifacts/export：`run export` / `run live export` 写出 status、metrics、commands、incidents、runtime_state、可选 runtime_log 与 hashed manifest。

验收：

- 运维可以在不知道 Python 内部对象的情况下诊断 run 状态。
- 所有重要运行事件都有 audit trail。

## 20. 推荐实施顺序

最小可交付顺序：

```text
1. operator_commands
2. stop command consumer
3. runtime_heartbeats
4. status stale detection
5. RunConfig-driven live daemon start
6. outbox dispatcher service
7. fill ingestion/reconciliation service
8. kill switch command
9. risk/market freshness gates
10. deployment and observability
```

不要先做 UI，不要先扩展更多交易策略。没有控制面、恢复和风控闭环，策略越多风险越高。

## 21. 边界不变项

这些规则必须持续保持：

- `Strategy Context` 不暴露 submit/cancel/gateway/store。
- Connector 不定义全局 capability domain。
- `RunKernel` 不实现 connector、fill model、order state machine、ledger writer。
- Governance artifact repository 由 governance owner 注入。
- CLI 不直接持有 live service lifecycle。
- Live run 多实例只用 `run_id` 表达，不新增额外的 live-specific id 字段。
- 不同 `run_id` 不共享 runtime database、outbox 或 in-memory supervisor。

## 22. 最终用户心智

用户最终应该只需要理解：

```text
RunConfig 描述可重复启动的运行意图。
run_id 标识一次实际执行或一个长驻 live 实例。
run live start 默认 spawn 一个 live daemon process。
run live status 读取 daemon 观察状态和审计事实。
run live stop/pause/resume/kill-switch 写入 operator command。
daemon 负责消费命令并写回 ack。
所有订单、成交、恢复、风控和运维动作都有持久证据。
```

这使 KairoSpy 从“CLI 可以触发一些实盘动作”升级为“CLI、daemon、执行状态机、风控、恢复和审计通过持久协议协作”的专业量化运行系统。
