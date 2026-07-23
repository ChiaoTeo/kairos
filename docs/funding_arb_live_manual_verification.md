# Funding Arb Live Manual Verification

状态：真实 key 前置 runbook  
日期：2026-07-23

本文只描述用户手动验证步骤，不保存、不打印、不生成任何 API key。自动化验收到 `funding-arb-live-preflight.toml` 为止；真实下单必须由用户在本机显式运行。

## 1. 当前边界

已自动验证：

1. `workspace attach` 可接入 Hyperliquid/Binance Dataset。
2. `workspace inspect-code --mode paper|live` 可冻结 projection graph 和 preflight。
3. `funding-arb-paper.toml` 可跑出 pair trade check、risk checks、orders blocked state 和 treasury transfer intent。
4. `funding-arb-live-preflight.toml` 可 validate，并保持 `bind_provider = false` / `bind_ports = false`。
5. `accounts doctor hyperliquid_live_perp` 可检查 AccountBinding 和 credential env readiness，但不打印 private key，也不查询真实账户。
6. Hyperliquid official SDK loader、execution gateway、account gateway 已用 fake SDK/info/exchange 测试覆盖；真实 signing/order 仍需用户本机手动验证。

未自动验证：

1. Hyperliquid official SDK signed `/exchange` adapter with real key。
2. Binance spot + Hyperliquid perpetual 两边真实账户状态。
3. 真实小额下单、撤单、订单恢复和 fill ingestion。
4. treasury transfer route。

## 2. 必须先满足的代码门

真实 live run 前必须通过：

1. `kairospy run config validate examples/configs/runs/funding-arb-live-preflight.toml`
2. `kairospy workspace inspect-code examples.workspace.funding_arb:build_workspace --mode live ...`
3. `kairospy accounts doctor binance_live_spot`
4. `kairospy accounts doctor hyperliquid_live_perp`

真实下单前仍需用户手动确认：

1. Hyperliquid SDK `exchange` / `info` objects are created from the official SDK with the user's local key.
2. Reference catalog contains Hyperliquid BTC perpetual listing and Binance BTCUSDT spot listing.
3. RunConfig uses explicit cross-venue execution binding instead of `manual-cross-venue`.
4. Treasury transfer route has separate manual evidence.

## 3. 用户本机手动环境

在 shell 中配置，不写入 RunConfig：

```bash
export KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY=...
export KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET=...
export KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY=...
export KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS=...
```

如果账户只允许减仓或测试权限，应保持：

```toml
[guards]
start_reduce_only = true
```

## 4. 手动验证顺序

先检查配置和 workspace projection：

```bash
kairospy run config validate examples/configs/runs/funding-arb-live-preflight.toml

kairospy workspace inspect-code examples.workspace.funding_arb:build_workspace \
  --mode live \
  --param workspace_profile=funding-arb \
  --param instrument=BTC \
  --param target_notional=10000
```

再做账户只读 readiness：

```bash
kairospy accounts doctor binance_live_spot
kairospy accounts doctor hyperliquid_live_perp
```

最后才允许用户手动启动 live run：

```bash
kairospy run live start \
  --run-id funding-arb-live-YYYYMMDD-manual \
  --config configs/runs/funding-arb-live.toml \
  --confirm-live \
  --supervise-live-services
```

## 5. 停止条件

以下任一条件出现，应立即停止，不继续下单：

1. workspace preflight has any `error`.
2. readiness evidence missing or stale.
3. either account doctor fails.
4. Hyperliquid order recovery is unbound.
5. Binance or Hyperliquid open order reconciliation fails.
6. treasury transfer route is unverified.
7. strategy decision still contains `blocked_until = "live_execution_keys_and_risk_inputs_verified"`.

## 6. 首次真实交易限制

首次 live 验证建议只允许：

1. reduce-only or minimum notional.
2. one pair trade attempt.
3. immediate order recovery check.
4. no treasury transfer until transfer route has separate manual evidence.

成功标准：

1. Run manifest freezes workspace code hash, strategy hash, projection, preflight and risk gates.
2. Two-leg order intent remains paired.
3. Hyperliquid short leg and Binance long leg are both acknowledged or both cancelled.
4. Recovery can explain venue order ids and final state.
5. No unhedged single-leg exposure remains after stop.
