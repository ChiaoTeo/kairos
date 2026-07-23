# Position Lab Manual Control Flow

状态：先模拟控制和回测，真实下单前停止  
日期：2026-07-23

目标流程：

1. 启动一个常驻 run。
2. 策略先只打印行情。
3. CLI 给 run 发 `target-position` 命令。
4. run/strategy 接收目标仓位意图，并先持久化，不自动下单。
5. 后续再把目标仓位意图转换为订单、风控、对账和恢复。

## 1. Workspace

Dataset attachment 由 CLI 管：

```bash
./pyenv/bin/python -m kairospy workspace inspect position-lab
```

Workspace 投影由代码管：

```bash
./pyenv/bin/python -m kairospy workspace inspect-code examples.workspace.position_lab:build_workspace \
  --mode live \
  --param workspace_profile=position-lab
```

## 2. 听行情

这些命令不需要交易 key：

```bash
./pyenv/bin/python -m kairospy --format json data sample binance.orderbook \
  --instrument BTCUSDT --market spot --levels 5 --limit 1

./pyenv/bin/python -m kairospy --format json data sample hyperliquid.perpetual.orderbook \
  --instrument BTC --limit 1

./pyenv/bin/python -m kairospy --format json data sample hyperliquid.perpetual.funding \
  --instrument BTC --limit 1
```

## 3. 空策略回测

不写因子、不写策略逻辑时，默认使用 `empty_workspace + EmptyStrategy`：

```bash
./pyenv/bin/python -m kairospy run start \
  --config examples/configs/runs/position-lab-backtest.toml
```

## 4. 常驻打印行情 run

配置文件：

```bash
./pyenv/bin/python -m kairospy run config validate \
  examples/configs/runs/position-lab-live-print.toml
```

真实 live daemon 启动仍然要求用户显式确认并解锁：

```bash
./pyenv/bin/python -m kairospy config set execution.live_trading_enabled true

./pyenv/bin/python -m kairospy run live start \
  --run-id position-lab-live \
  --config examples/configs/runs/position-lab-live-print.toml \
  --confirm-live
```

连接实时控制台查看输出并提交控制命令：

```bash
./pyenv/bin/python -m kairospy run live attach --run-id position-lab-live
```

## 5. CLI 发仓位命令

目标仓位命令使用 durable operator command bus：

```bash
./pyenv/bin/python -m kairospy run live target-position \
  --run-id position-lab-live \
  --leg binance,spot,BTCUSDT,long,0.001 \
  --leg hyperliquid,perpetual,BTC,short,0.001 \
  --intent-id manual-pair-001 \
  --actor zq \
  --reason "manual pair position target"
```

查看命令：

```bash
./pyenv/bin/python -m kairospy run live commands --run-id position-lab-live
./pyenv/bin/python -m kairospy run live status --run-id position-lab-live --fresh --wait 2
```

当前 `target-position` 默认只写入：

```text
target_position:<run_id>:last
```

不会自动下单。下一步需要策略或 execution translator 把目标仓位意图转换为成对订单，并在下单前检查：

1. account doctor passed。
2. market freshness passed。
3. reduce-only / kill-switch 状态。
4. 订单成对提交和失败回滚。
5. recovery / reconciliation 可解释最终仓位。

## 6. 真实 key 边界

真实 key 只放环境变量：

```bash
export KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY=...
export KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET=...
export KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY=...
export KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS=...
```

真实控制仓位前先跑：

```bash
./pyenv/bin/python -m kairospy --format json accounts doctor binance_live_spot
./pyenv/bin/python -m kairospy --format json accounts doctor hyperliquid_live_perp
```
