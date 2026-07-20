# Kairos Examples Suite

这些示例按完整数据流组织，目标是让新贡献者不阅读测试内部实现也能跑通：

```text
Governed Dataset / Live WebSocket
  -> Canonical Event
  -> Projection / MarketSnapshot
  -> Same Strategy Interface
  -> Decision / Intent
  -> Capture / Replay / Audit Hash
```

所有命令从仓库根目录执行。

## CLI 输出模式

产品工作流命令默认输出适合终端阅读的本地化字段视图或表格，不输出 JSON：

```bash
./pyenv/bin/kairos --lang zh-CN tutorial sma
```

语言默认根据系统 locale 选择，也可显式使用 `--lang zh-CN` 或 `--lang en-US`。脚本、CI 和 Agent 集成必须显式请求稳定的机器契约：

```bash
./pyenv/bin/kairos --format json tutorial sma
```

只需要退出码时使用 `--quiet`。JSON 字段名保持稳定且不参与翻译；国际化只作用于人类展示层。

## 第一次使用：从零完成一个研究

如果你还不知道 Study、Factor、Strategy 和 Run 应该怎样串起来，请不要先运行完整验收，也不要先连接 Paper/Live。按照 [第一次研究教程](../docs/tutorial_first_study.md) 逐条执行。教程会解释每一步的目的、产物、继续条件和停止条件，并使用一个最终应被拒绝的 SMA 结果示范“系统跑通”与“策略有效”之间的区别。

完成 `kairos tutorial sma` 后，可以直接打开 Study 绑定的数据为 DataFrame：

```bash
./pyenv/bin/python examples/studies/study_dataframe.py
```

该示例只使用 `open_study(...).data.pandas()`，不导入 fixture 构造函数、不遍历领域 `Bar`，也不手工转换价格类型。

## 一键完整产品验收

以下入口在一个隔离目录中依次运行本文档的八个场景，并输出逐场景布尔证据和详细 artifact：

```bash
./pyenv/bin/python examples/lifecycle/full_product_acceptance.py
```

只有八个场景、SMA execution-boundary parity 和多资产 Strategy Release 全部成立时，顶层
`passed` 才为 `true`。

## 0. Study Sandbox 到 Factor Release

Study 采用“探索灵活、晋级固定”的双层模型。下面的确定性示例创建 Sandbox workspace、冻结
Study Candidate、注册 SMA Factor Release，并证明 batch 与 Canonical replay 完全一致：

```bash
./pyenv/bin/python examples/studies/sma_factor_lifecycle.py
```

冻结的 candidate 仍不是正式的 Study Validation Evidence；它必须经过现有
`StudyValidationResult` 和治理门禁后，才能支持 Strategy promotion。

同一流程也可以完全通过产品 CLI 执行：

```bash
kairos tutorial sma --output-root example-output/sma-lifecycle
kairos --lake-root example-output/sma-lifecycle study inspect btc-sma-first
kairos --lake-root example-output/sma-lifecycle study data btc-sma-first --head 10
kairos --lake-root example-output/sma-lifecycle study profile btc-sma-first
kairos --lake-root example-output/sma-lifecycle study scaffold btc-sma-first
kairos --lake-root example-output/sma-lifecycle study freeze btc-sma-first
kairos --lake-root example-output/sma-lifecycle factor register-sma \
  --input-identity fixture:sma-bars-v1 --fast 5 --slow 15
kairos --lake-root example-output/sma-lifecycle factor verify-sma --fixture --fast 5 --slow 15
kairos --lake-root example-output/sma-lifecycle strategy register-sma \
  --input-identity fixture:sma-bars-v1 --fast 5 --slow 15
kairos --lake-root example-output/sma-lifecycle run backtest --strategy sma-cross-v1 --fixture --fast 5 --slow 15
kairos --lake-root example-output/sma-lifecycle run simulate --strategy sma-cross-v1 --fixture --fast 5 --slow 15 \
  --run-root example-output/sma-lifecycle/runs/sma
kairos --lake-root example-output/sma-lifecycle run shadow --strategy sma-cross-v1 --fixture --fast 5 --slow 15 \
  --run-root example-output/sma-lifecycle/runs/shadow
kairos --lake-root example-output/sma-lifecycle run inspect \
  --db example-output/sma-lifecycle/runs/sma/runtime/runtime.sqlite3
```

示例统一使用带 `--strategy` 的通用入口。所有运行入口都会返回不可变 Run Artifact 路径。
Shadow 使用同一 Factor/Strategy，但只记录假设 Intent，`orders=0` 且 `fills=0`。可以解释指定时刻的
因子、决策和 EconomicIntent，或使用相同输入离线重放：

```bash
kairos run inspect --artifact '<manifest.json>' --at 2026-01-02T00:00:00Z
kairos run artifact-replay --artifact '<manifest.json>' --fixture
```

Replay 会分别比较 factor、decision、intent 和完整 strategy-run audit hash；任一项不一致都会返回
`passed=false`。

## 5. Paper Trading Session 与 Capture Replay

以下 deterministic acceptance 先生成带 manifest 的 Canonical Bar capture，再用同一 SMA
Factor/Strategy 启动 `paper-trading` composition。订单经过 Simulated Venue、Durable Order、Fill
Ingestion 和 Ledger；session 停止后从 capture 离线重放：

```bash
./pyenv/bin/python examples/runtime/sma_paper_session.py
```

对应产品命令：

```bash
kairos run shadow --fixture --fast 5 --slow 15 \
  --run-root example-output/sma-shadow/runtime \
  --artifact-root example-output/sma-shadow/artifacts
kairos run paper --fixture --fast 5 --slow 15 \
  --run-root example-output/sma-paper/runtime \
  --artifact-root example-output/sma-paper/artifacts
kairos run capture-replay --artifact '<manifest.json>' --capture '<capture.jsonl>'
```

Shadow 和 deterministic Paper acceptance 都不需要账户凭据。真实 Binance Testnet/IBKR Paper 仍必须经过
`runtime l4-preflight`、外部凭据/连接、reconciliation、soak、restart 和 kill-switch 门禁。

## 6. 人工运维订单

人工订单与自动策略运行使用不同入口，并强制留下 actor/reason：

```bash
kairos order submit --venue simulated --environment testnet \
  --instrument crypto:sim:spot:BTCUSDT --side sell --quantity 0.001 \
  --limit-price 50000 --actor operator@example --reason 'manual risk reduction'
```

该入口仍经过 KairosApplication、Coordinator、Durable Order State、readiness 和 kill switch，但
不会伪装成已经注册和晋级的自动 Strategy Release。

## 7. 复杂期权策略生命周期

SPXW skew 研究与正式 Bull Put Spread 不再是两套互不关联的逻辑。示例注册 point-in-time
`spxw-put-skew` Factor Release，让正式 `BullPutSpreadStrategy` 显式消费 `skew_rank`，绑定
StrategySpec/ExecutionPolicy 后运行 conservative/stress 多腿可执行回测和确定性 replay：

```bash
./pyenv/bin/python examples/strategy/bull_put_spread_lifecycle.py
```

示例使用 synthetic fixture，只证明 Factor→Strategy→Combo Fill→Ledger→Replay 机制；研究收益模拟
被明确标记为 `TRADE_PROXY_ONLY`，不能支持 executable 或 live promotion。

Covered Call、Protective Put 和 Spot/Perpetual Carry 也已经实现正式 Strategy Protocol，并在
Strategy Release 中绑定 implementation hash。参考场景不再直接调用专用 `intents()` 绕过运行层：

```bash
./pyenv/bin/python examples/strategy/multi_asset_reference_lifecycle.py
```

示例验证 Covered Call 和 Carry 的 GovernedStrategyRuntime、EconomicIntent、产品 Fill Model、
Funding/Corporate Action/Assignment、Ledger、Conservative/Stress 和 deterministic replay。

多策略资本分配、虚拟持仓归属和账户净额：

```bash
./pyenv/bin/python examples/strategy/multi_strategy_portfolio.py
```

对应产品命令：

```bash
kairos strategy register-builtins
kairos strategy register-btc-iron-condor --study-spec-hash '<governed-study-spec-hash>'
kairos strategy inspect covered-call-v1 --version 1.1.0
kairos strategy status covered-call-v1 --version 1.1.0
kairos strategy activate covered-call-v1 --version 1.1.0 --actor operator@example --reason 'approved baseline'
kairos strategy rollback covered-call-v1 --actor operator@example --reason 'observed regression'
kairos run reference --strategy covered-call
kairos run reference --strategy spot-perp-carry
```

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

输出 Study、Backtest、Historical Simulation、Paper Trading 和 Live 的 EventSource、Clock、Execution、Persistence、Safety、Capture 与 composition hash。

正式 SMA Historical Simulation 使用与回测相同的 FactorSpec、Strategy implementation 和
EconomicIntent，但将执行边界替换为 Simulated Venue、Durable Order State、Execution Ingestion、
SQLite Runtime Store 和正式 Ledger：

```bash
./pyenv/bin/python examples/runtime/sma_historical_simulation.py
```

该示例还会停止并重启 KairosApplication，只有 Ledger、Venue balance/position 和对账恢复一致时
才输出 `restart_ready=true`。`tests/test_examples_suite.py` 会同时运行 governed backtest 与 historical
simulation，并要求二者在 execution driver 之前的 factor、decision 和 intent hash 完全一致。

## 5. Connector / Rust 接入契约

```bash
./pyenv/bin/python examples/connectors/reference_connector/verify_contract.py
```

详细说明见 [reference_connector/README.md](connectors/reference_connector/README.md)。未来 Rust gateway 必须使用相同 contract vectors 和 verifier，不能要求上层策略修改接口。

## 6. 长时间行情 Soak

长跑不是普通教学脚本，使用正式 CLI 和可审计 Artifact：

```bash
./pyenv/bin/kairos \
  --lake-root example-output/market-data-soak \
  data soak-binance \
  --symbol BTCUSDT --channel bookTicker \
  --duration-seconds 60 --minimum-events 100 \
  --restart-interval-seconds 20 \
  --capture-segment-events 1000
```

绑定 Strategy paper/live 使用的 Live View 时，把 `data write --live` 生成的 manifest 路径传给
`--live-view-manifest`；通过后会写回 freshness 与 channel diagnostics，供 `run start --mode paper|live`
门禁读取。

24–72 小时参数和验收边界见 [market_data_long_soak_runbook.md](../docs/market_data_long_soak_runbook.md)。

## 示例边界

- 公共 Binance Quote/Depth 示例无需 key；
- Paper/Testnet 下单不会放入一键 Example，必须走 `runtime_l4_soak_runbook.md` 的部署、凭据、限额和 Kill Switch 门禁；
- fixture 示例证明机制和确定性，不替代真实外部 Soak；
- Example 输出默认写入 `example-output/`，不作为持久事实源。
