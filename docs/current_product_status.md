# Kairos 当前产品状态

状态：Usability hardening in progress  
日期：2026-07-19

本文是面向使用者的当前状态页。更详细的历史计划和实施证据见
`product_acceptance_matrix.md`、`system_convergence_progress.md` 和 `runtime_l4_soak_runbook.md`。

## 已完成并可本地验收

- 本地确定性生命周期：`Study -> Factor -> Strategy -> Backtest -> Historical Simulation -> Shadow -> Live Paper fixture -> Replay -> Audit`。
- 统一产品命令：`study`、`factor`、`strategy`、`run`、`order`、`runtime`。
- Notebook/Python 入口：`Kairos(...).backtest(...)` 返回可查看 summary、trades、equity 和 explain 的结果视图。
- 统一运行入口：`kairos run backtest/simulate/shadow/paper --strategy sma-cross-v1@1.2.0 ...`。
- Shadow 入口：`kairos run shadow --strategy ...` 使用 Capture/fixture 计算完整决策和假设 Intent，但不提交订单。
- 真实行情模拟盘入口：`kairos run paper --strategy ... --live-binance-symbol BTCUSDT ...` 使用 Binance 公共 K 线作为输入，执行仍走模拟账户，不需要账户凭据，不会真实下单。
- 策略晋级入口：`kairos strategy promote ... --evidence ...`，晋级证据会被哈希、经过 promotion gate，并写入可独立审计的 evidence bundle。
- 晋级预检查入口：`kairos strategy check-promotion ... --evidence ...` 使用同一套 gate、hash 和生命周期顺序检查，但不会修改 Strategy Release。
- 执行校准入口：`kairos runtime calibrate-execution ...` 可从 Runtime Store 的订单/成交事实生成 `ExecutionCalibrationRelease`。
- 回测执行校准对比：`kairos run backtest ... --execution-calibration <manifest>` 会校验校准 release hash，在 Run Artifact 中记录绑定状态、release id/hash、样本数与适用 venue/environment，并给出按校准平均 `fee_bps` 重估的基线/校准后权益对比。
- 人工订单与自动策略入口分离：人工运维使用 `order submit`；外部运行验收使用 `runtime soak`。
- Run Artifact 可解释：`run inspect --artifact ... --at ...`。
- Replay 可验证：`run artifact-replay` 和 `run capture-replay`。
- 公共 Binance Quote/OrderBook 短时 capture、rotation、restart 和 replay 机制已有自动化证据。

## 本地可用但不能冒充外部就绪

- `run shadow --fixture` 和 `run paper --fixture` 是 deterministic acceptance，不需要凭据，也不会证明真实外部运行稳定；`run paper --live-binance-symbol ...` 可验证真实行情输入下的模拟盘链路，但仍不是实盘下单证据。
- 本地 `ExecutionCalibrationRelease` 证明校准机制可工作；只有真实 Paper/Testnet/Live 样本生成的 release 才能用于外部执行质量判断。
- synthetic fixture、synthetic backtest、trade proxy 只能证明机制，不能作为 live promotion 或收益有效性证据。
- Strategy promotion gate 已区分本地/外部证据：`PAPER_APPROVED` 需要非 fixture 的 decision-OOS L5 证据和显式 Paper/Testnet readiness；`LIVE_LIMITED`/`LIVE_APPROVED` 需要通过的外部 soak artifact。
- `runtime l4-preflight --evidence-artifact ...` 可写出带 `kind=runtime_l4_preflight` 和 `audit_hash` 的 readiness evidence，用于 `PAPER_APPROVED` 晋级审计。
- `runtime soak ... --soak-artifact ...` 写出的 soak artifact 带 `kind=runtime_l4_soak` 和 `audit_hash`；只有 `passed=true` 且全部 acceptance 通过的外部 artifact 才能用于 `LIVE_LIMITED`/`LIVE_APPROVED` 晋级。
- Promotion gate 会复算外部 readiness/soak artifact 的 `audit_hash`，证据内容被改动后会 fail-closed。
- 公共行情短时 smoke 证明 capture/replay 机制可工作，不替代 24-72 小时持续 soak。

## 外部 Paper/Testnet 缺口

正式外部可用性仍需要至少完成一条真实 Venue 路径，例如 Binance Testnet 或 IBKR Paper：

1. 有策略版本凭非 fixture L5 稳健性证据和 Paper/Testnet readiness evidence 晋级到 `PAPER_APPROVED`。
2. Reference Catalog 中有目标 instrument 的有效 Venue listing。
3. 凭据或 Gateway 只由环境注入，不能写入命令、配置或 artifact。
4. `runtime l4-preflight` 通过。
5. 24-72 小时 soak 通过。
6. restart drill、kill-switch drill、reconciliation 和 unresolved order drill 均生成通过 artifact。
7. Live/Paper capture 可离线 replay，factor/decision/intent/audit hash 与原运行一致。
8. 真实订单/成交生成 `ExecutionCalibrationRelease`，并绑定到后续回测 Run Artifact。

当前仓库不能用 fixture 结果替代上述 L4 证据。

## 推荐可用性验收命令

```bash
./pyenv/bin/python -m unittest tests.test_kairos_api tests.test_product_cli tests.test_strategy_registry tests.test_strategy_promotion_gate
./pyenv/bin/python examples/lifecycle/full_product_acceptance.py
./pyenv/bin/python -m unittest discover -s tests
./pyenv/bin/python -m compileall -q kairos examples tests studies
git diff --check
```

真实外部验收另按 `runtime_l4_soak_runbook.md` 执行，默认不在本地测试中冒充通过。

## 下一批最有价值的改造

1. 将更多策略迁移到通用 `run backtest --strategy ...`，逐步淡化策略专用命令。
2. 将校准 release 从“fee_bps 报告对比”推进到 slippage、latency、partial fill 和 venue bucket 的 Fill Model 参数化。
3. 完成一条真实 Binance Testnet 或 IBKR Paper L4 证据链。
