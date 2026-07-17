# Examples Suite 验收

日期：2026-07-17

## 目标

Examples 必须让开发者从公开入口理解并运行本次改造，而不是只能阅读内部测试。覆盖范围包括：治理回测、Canonical Replay、公共实时 Quote、实时 OrderBook、策略 Live/Replay、运行模式组合、长跑运维和 Python/Rust Adapter 契约。

## 覆盖矩阵

| 能力 | Example | 外部依赖 | 自动验收 |
|---|---|---|---|
| Batch 与 async Canonical 回测一致 | `examples/backtest/governed_sma.py` | fixture 无；正式 Release 需要本地 Data Lake | `test_examples_suite` |
| 公共 Quote Capture 与策略 Replay | `examples/realtime/binance_quote_capture.py` | 公网，无账户凭据 | 公共 live integration + 手工 smoke |
| REST Snapshot + WebSocket Delta | `examples/realtime/binance_order_book.py` | 公网，无账户凭据 | 公共 live integration + 手工 smoke |
| Projection/Decision/Intent hash | `examples/replay/live_vs_replay_strategy.py` | fixture 无 | `test_examples_suite` |
| 五种 Run Mode Composition | `examples/runtime/run_modes.py` | 无 | `test_examples_suite` |
| Python/Rust Adapter boundary | `examples/adapters/reference_adapter/verify_contract.py` | Python reference 无 | `test_examples_suite` |
| Rotation/Restart/Resource Soak | 正式 `data soak-binance` CLI | 公网 | `test_capture_rotation` / `test_market_data_soak` |

## 已执行证据

治理 Release 回测：

```text
Release: ds_697a08e565a778f3112adddb
Bars: 4344
Trades: 84
Batch == Canonical Replay: true
Audit: 65dee8f2a7c104db803bf7fc9d6240350855ffc8eaf9ed6986c6ac33ab752f35
```

公共 Quote：

```text
Events: 3
Reconnects: 0
Live == Replay: true
Strategy audit: 364da1c8f016c66fc0e2a3ae48defc81060a5c8c74dbb877270359ff95db2f16
```

公共 OrderBook：

```text
Raw deltas: 25
Stale buffered deltas: 11
Aligned deltas: 14
Aligned events including Snapshot: 15
Book valid: true
Live == Replay: true
```

该 OrderBook example 额外发现并修复了 REST Snapshot 晚于 buffered Delta 时 Aligned Capture available-time 回退的问题。修复后保留原始 `event_time`，但将策略 `available_time` 推进到 Snapshot 对齐完成之后，并添加 `snapshot_aligned` flag。

## 安全卫生

- 删除所有已跟踪或未跟踪的 `.ipynb_checkpoints` 文件；
- `.gitignore` 全局忽略 `.ipynb_checkpoints/`；
- `test_repository_hygiene` 阻止 checkpoint 和常见 live secret 形态重新进入 examples/docs；
- 工作树已不再包含先前发现的疑似 Massive key。

删除文件不能撤销已经暴露的凭据；对应 Massive key 仍必须在 Provider 控制台轮换。Git 历史清理属于单独的破坏性仓库操作，不在本次自动执行范围内。

## 自动回归

```text
compileall trading/tests/examples: passed
full suite: 421 passed, 5 optional external integrations skipped
public Binance Quote/Depth integration: 2 passed
git diff --check: passed
checkpoint scan: clean
common live-secret shape scan: clean
```
