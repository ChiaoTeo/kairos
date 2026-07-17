# Trader Examples Suite

这些示例按完整数据流组织，目标是让新贡献者不阅读测试内部实现也能跑通：

```text
Governed Dataset / Live WebSocket
  -> Canonical Event
  -> Projection / MarketSlice
  -> Same Strategy Interface
  -> Decision / Intent
  -> Capture / Replay / Audit Hash
```

所有命令从仓库根目录执行。

## 1. 回测与 Canonical Replay

不依赖外部数据的 fixture：

```bash
./pyenv/bin/python examples/backtest/governed_sma.py
```

消费正式 Q3/Q4 Dataset Release：

```bash
./pyenv/bin/python examples/backtest/governed_sma.py \
  --lake-root data \
  --dataset market.ohlcv.crypto.binance.btc-usdt.1h \
  --start 2026-01-01T00:00:00Z \
  --end 2026-07-01T00:00:00Z \
  --fast 20 --slow 50
```

示例同时运行 batch BarSeries 和 async Canonical EventSource，并要求结果完全相同。

## 2. Live Capture 与策略 Replay

无需账户凭据：

```bash
./pyenv/bin/python examples/realtime/binance_quote_capture.py \
  --symbol BTCUSDT --messages 10
```

输出 Raw Journal、Canonical Capture、最新 Quote，以及 Live/Replay 的 Projection、Decision、Intent 和 Audit Hash。

已有 Canonical Capture 可离线复核：

```bash
./pyenv/bin/python examples/replay/live_vs_replay_strategy.py \
  --capture example-output/binance-quote/<session>.canonical.jsonl
```

不传 `--capture` 时使用确定性 fixture，可在 CI 和无网络环境运行：

```bash
./pyenv/bin/python examples/replay/live_vs_replay_strategy.py
```

## 3. 实时 OrderBook

```bash
./pyenv/bin/python examples/realtime/binance_order_book.py \
  --symbol BTCUSDT --messages 1000 --depth 100 --print-events
```

该示例贯通 REST Snapshot、WebSocket Delta、首次桥接、gap/reconnect resync、Aligned Capture 和 Replay。策略只能读取 `valid=true` 的盘口。

## 4. 运行模式组合

```bash
./pyenv/bin/python examples/runtime/run_modes.py
```

输出 Research、Backtest、Historical Simulation、Live Paper 和 Live 的 EventSource、Clock、Execution、Persistence、Safety、Capture 与 composition hash。

## 5. Adapter / Rust 接入契约

```bash
./pyenv/bin/python examples/adapters/reference_adapter/verify_contract.py
```

详细说明见 [reference_adapter/README.md](adapters/reference_adapter/README.md)。未来 Rust gateway 必须使用相同 golden vectors 和 verifier，不能要求上层策略修改接口。

## 6. 长时间行情 Soak

长跑不是普通教学脚本，使用正式 CLI 和可审计 Artifact：

```bash
./pyenv/bin/python -m trading \
  --lake-root example-output/market-data-soak \
  data soak-binance \
  --symbol BTCUSDT --channel bookTicker \
  --duration-seconds 60 --minimum-events 100 \
  --restart-interval-seconds 20 \
  --capture-segment-events 1000
```

24–72 小时参数和验收边界见 [market_data_long_soak_runbook.md](../docs/market_data_long_soak_runbook.md)。

## 示例边界

- 公共 Binance Quote/Depth 示例无需 key；
- Paper/Testnet 下单不会放入一键 Example，必须走 `runtime_l4_soak_runbook.md` 的部署、凭据、限额和 Kill Switch 门禁；
- fixture 示例证明机制和确定性，不替代真实外部 Soak；
- Example 输出默认写入 `example-output/`，不作为持久事实源。
