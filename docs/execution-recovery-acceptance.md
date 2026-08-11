# Execution Recovery Acceptance

本文档是 Execution 连接恢复的沙盒/真实环境验收清单。它不创建订单，也不替代交易所的测试账户审批；所有命令默认只读或只做本地对账。

## 前置条件

```bash
export WORKSPACE=/path/to/workspace
export PROVIDER=binance
export PRODUCT=spot
export SYMBOL=BTCUSDT
export EXECUTION_CLI=target/debug/kairos-execution-cli
```

真实环境必须使用最小权限 API key、IP 白名单和独立测试账户。首次执行只允许查询和对账，不允许 `submit`、`cancel` 或 `--confirm-live`。

## 基线

```bash
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" snapshot
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" remote-open-orders --symbol "$SYMBOL"
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" remote-history --symbol "$SYMBOL" --limit 100
```

记录本地 `event_sequence`、`exchange_event_watermark_unix_nanos`、本地订单数和远端订单数，作为演练前基线。

## 五类时序演练

| 场景 | 操作 | 必须观察到的结果 |
|---|---|---|
| Execution 先收到成交 | 让 Execution private stream 先投递成交，再让 Account stream 投递同一 `fill_id` | Execution 先记账；Account 正式成交幂等合并，不重复结算 |
| Account 先收到成交 | 让 Account private stream 先投递成交 | Account 只产生 `ObservedFill` 并进入 reconciliation，不改变余额/持仓；Execution 正式 Fill 到达后观察事实被清除 |
| 两边同时收到 | 并发投递同一 `fill_id` 到两条流 | 最终只有一条成交事实；重复事实为 `Duplicate` 或幂等 `Applied`，不得产生双重结算 |
| 单边断线后恢复 | 断开一条 private stream，制造一笔成交，恢复连接 | 连接主动 `reconnect()`；runtime 通过 watermark 查询历史；本地累计成交量补齐缺口 |
| 远端未知订单 | 在交易所测试账户创建本地没有 journal 记录的订单 | 对账后出现 `UnknownRemoteOrder`；可查询、人工 `link-unknown` 或标记人工处理，不得静默丢弃 |

## 恢复与证据

```bash
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" reconcile-remote --symbol "$SYMBOL" --limit 200
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" unknown-remote-orders
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" snapshot
```

对于确认属于本地订单的未知远端订单，记录人工审批后再执行：

```bash
$EXECUTION_CLI --workspace "$WORKSPACE" --provider "$PROVIDER" --product "$PRODUCT" \
  link-unknown --remote-order-id "$REMOTE_ORDER_ID" --local-order-id "$LOCAL_ORDER_ID"
```

验收通过标准：重启前后的 snapshot、fills、订单状态、watermark 和未知订单处置状态一致；同一组命令重复执行不会增加成交数量或生成第二个 child order。任何远端累计成交量小于本地已记账量、远端成交缺少价格或两边事实冲突，都必须保留在 `Unknown`/reconciliation 状态，交给人工处理。
