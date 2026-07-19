# Runtime L4 Paper/Testnet Soak Runbook

本 Runbook 用于执行架构蓝图中的 L4 验收。模拟环境只能验证机制，不能替代本验收。

## 前置条件

1. 选择一个非 Live 环境：IBKR Paper 或 Binance Testnet。
2. Strategy Lifecycle 至少为 `PAPER_APPROVED`；不得为了运行测试跳过晋级门禁。
3. Instrument Catalog 包含目标 Venue 的有效 Listing。
4. Runtime Store、Event Log 和 Artifact 路径位于同一个受控运行根目录。
5. 凭证只通过环境变量或本地密钥管理器注入，不写入命令、配置或 Artifact。
6. 账户资金和下单上限必须限制在专用测试账户允许的最小范围。

`PAPER_APPROVED` 本身不能由本地 fixture 结果直接产生：promotion gate 要求非 fixture 的
decision-OOS L5 稳健性证据，并额外提供 Paper/Testnet readiness evidence。完成本 Runbook 的
passed soak artifact 之后，才可作为 `LIVE_LIMITED` / `LIVE_APPROVED` 晋级证据。

readiness evidence 可由 preflight 直接写出：

```bash
trader runtime l4-preflight \
  --venue binance --environment testnet \
  --strategy sma-cross-v1 \
  --instrument '<approved-instrument-id>' \
  --evidence-artifact data/runtime/binance-testnet/preflight-readiness.json
```

该 artifact 会写入 `kind=runtime_l4_preflight`、检查项、原因和 `audit_hash`，可作为
`strategy promote ... --to PAPER_APPROVED --evidence ...` 的 readiness evidence；`ready=false`
的 artifact 只用于诊断，不能通过 promotion gate。

## 标准命令

Binance Testnet 示例：

```bash
export BINANCE_TESTNET_API_KEY='...'
export BINANCE_TESTNET_API_SECRET='...'

trader \
  --lake-root data \
  --catalog-path data/catalog/instruments.json \
  --runtime-db data/runtime/binance-testnet/runtime.sqlite3 \
  --event-log-path data/runtime/binance-testnet/events.jsonl \
  trade run \
  --strategy spot-perp-carry \
  --venue binance \
  --environment testnet \
  --product futures \
  --account-id runtime-soak \
  --instrument '<approved-instrument-id>' \
  --side buy \
  --quantity '<bounded-quantity>' \
  --order-type limit \
  --limit-price '<bounded-price>' \
  --market-data-ready \
  --soak-seconds 86400 \
  --cycle-seconds 5 \
  --kill-switch-drill \
  --restart-drill \
  --soak-artifact data/runtime/binance-testnet/soak-24h.json
```

72 小时验收将 `--soak-seconds` 改为 `259200`。

## Supervisor 每周期检查

- Account lock lease heartbeat；
- ACKNOWLEDGED/PARTIALLY_FILLED/UNKNOWN Order 的 Venue recovery；
- IBKR commission-triggered Fill backfill 或 Binance REST Fill backfill；
- Binance Futures Funding 重叠窗口回补；
- Ledger balance/position、Open Order 和 Strategy Position Book 对账；
- 到期衍生品 Settlement completeness；
- Critical Alert 与 Kill Switch 状态；
- Runtime Store checkpoint。

## Artifact 通过条件

以下条件必须全部为 true：

```text
duration_met
all_cycles_healthy
no_critical_alerts
restart_drill_passed
kill_switch_drill_passed
```

任何 UNKNOWN Order、重复 Order、遗漏/重复 Ledger fact、未解释对账差异或 Critical Alert 都必须使验收失败。

`--soak-artifact` 写出的 artifact 带 `kind=runtime_l4_soak`、acceptance 检查项和 `audit_hash`。
只有 `passed=true` 且全部 acceptance 检查为 true 的外部 Paper/Testnet/Live artifact 才能作为
`strategy promote ... --to LIVE_LIMITED` 或 `--to LIVE_APPROVED` 的 evidence。

## 当前环境检查（2026-07-17）

```text
BINANCE_TESTNET_CREDENTIALS=missing
IBKR_4001=unreachable
IBKR_7497=unreachable
eligible PAPER_APPROVED CLI strategy=missing
```

因此当前仓库已经具备 L4 执行工具和 fail-closed Artifact，但尚没有真实外部环境证据，不能标记 L4 完成。
