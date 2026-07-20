# Kairos 产品贯通验收矩阵

状态：Local Acceptance Passed  
基线日期：2026-07-18

本文件是 [`study_strategy_backtest_live_convergence_plan.md`](study_strategy_backtest_live_convergence_plan.md)
的实施证据索引。完成状态以可运行 example、自动化测试和 artifact 为准，不以模块存在为准。

## 一键验收

```bash
./pyenv/bin/python examples/lifecycle/full_product_acceptance.py
```

顶层 `passed=true` 要求八个场景、SMA execution-boundary parity 和多资产 Strategy Release 同时成立。

## 场景矩阵

| 场景 | 产品行为 | Example | 自动化证据 | 状态 |
|---|---|---|---|---|
| 1 因子探索 | Sandbox workspace，不产生部署证据 | `examples/studies/sma_factor_lifecycle.py` | `test_study_workspace.py` | Passed |
| 2 因子正式化 | Study Candidate、Factor Release、batch/replay parity | 同上 | `test_factor_runtime.py` | Passed |
| 3 最简策略 | SMA Factor→Strategy→EconomicIntent→Backtest | `examples/backtest/governed_sma.py` | `test_strategy_run_loop.py` | Passed |
| 4 历史模拟 | Durable Order、Fill Ingestion、Ledger、restart | `examples/runtime/sma_historical_simulation.py` | `test_sma_historical_simulation.py` | Passed |
| 5 Shadow / Live Paper | Canonical Capture→Shadow zero-order decision→Simulated Venue→Runtime Store | `examples/runtime/sma_paper_session.py` | `test_sma_paper_session.py`, `test_product_cli.py` | Passed |
| 6 决策回放 | Run Artifact、按时间解释、capture replay | 同上 | `test_run_artifact.py`, `test_product_cli.py` | Passed |
| 7 人工订单 | `order submit`、actor/reason、正式运行门禁 | `examples/operations/manual_order.py` | `test_cli_multi_asset.py` | Passed |
| 8 复杂期权 | Skew Factor→BullPut Strategy→Combo Fill→Ledger→Replay | `examples/strategy/bull_put_spread_lifecycle.py` | `test_bull_put_spread_lifecycle.py` | Passed |

首次研究产品入口补充证据：`tutorial sma` 会发布 governed fixture Release；`study inspect/data/profile/scaffold`
以及 `open_study(...).data.pandas|polars|arrow` 由 `tests/test_study_session.py` 和
`examples/studies/study_dataframe.py` 验收。StudySession 打开时强制核对 Workspace input hash 与 Dataset Release。

多资产补充证据：`examples/strategy/multi_asset_reference_lifecycle.py` 覆盖 Covered Call、Protective Put、
Spot/Perpetual Carry、Funding、Corporate Action、Assignment、Conservative/Stress 和 Ledger replay。
`examples/strategy/multi_strategy_portfolio.py` 覆盖策略资本分配、虚拟持仓所有权和账户层净额。

## 架构与治理矩阵

| 要求 | 权威实现 | 证据 |
|---|---|---|
| Sandbox/Governed Study 分离 | `kairos.study_platform.workspace`、ValidationArtifactWriter | workspace 与 `data/studies` 不混用 |
| Study 可直接使用绑定数据 | `StudySession`、`StudyData` | Dataset hash 校验、Pandas/Polars/Arrow、profile、scaffold |
| Factor 一等公民 | FactorSpec/Snapshot/Runtime/Registry | SMA、SPXW skew、fear-cooling factors |
| Strategy Release 绑定代码和因子 | StrategyRegistry | implementation.json、factor_bindings.json、manifest.json |
| 参数改变产生新语义身份 | StrategySpec hash、Backtest run material | SMA/BullPut 1.2.0 和 factor spec hash |
| RunMode 可执行组合 | RunModeComposition.bind | `test_run_mode_composition.py` |
| 跨模式一致性 | factor/decision/intent/audit hashes | Full product acceptance |
| 运行恢复 | SQLite Runtime Store、RuntimeRecoveryService | historical simulation / paper restart_ready |
| 决策解释 | RunArtifactRepository.explain | `run inspect --artifact --at` |
| Live replay | CanonicalCaptureWriter/Source | `run capture-replay` |
| 执行校准 release | ExecutionCalibrationRelease | `runtime calibrate-execution`、`test_execution_calibration.py` |
| 回测绑定执行校准 | `run backtest --execution-calibration` | Run Artifact 记录 fill_model、release_id、release_hash、样本数、适用范围和校准后权益对比 |
| 三层归因 | RunAttribution | signal / portfolio / execution |
| 明确 active version | StrategyRegistry.activate | active.json、activations.jsonl |
| 审计回滚 | StrategyRegistry.rollback | `test_strategy_registry.py` |
| 晋级缺口与证据包 | StrategyRegistry.status / promote | complete、missing_files、next_promotion、latest_promotion_bundle、promotion-bundles manifest |
| 人工/自动入口分离 | `order submit` / `run paper` | actor/reason 与 Strategy Release 分离 |
| 研究 proxy 不冒充回测 | `TRADE_PROXY_ONLY` | SPXW study + complex option example |

## 正式命令入口

```text
kairos tutorial sma
kairos study create|inspect|data|profile|scaffold|freeze
kairos factor register-sma|verify-sma
kairos strategy register-sma|register-builtins|register-btc-iron-condor
kairos strategy inspect|status|activate|rollback|check-promotion|promote
kairos run backtest|simulate|shadow|paper|reference
kairos run inspect|artifact-replay|capture-replay
kairos order submit
kairos runtime calibrate-execution|reference-artifact|failure-policy|l4-preflight|soak
```

人工订单使用 `order submit`；外部运行验收和 soak evidence 使用 `runtime soak`。

## 外部环境证据边界

本地 deterministic acceptance 不需要凭据，已在普通测试中运行。真实 Binance Testnet 和 IBKR Paper
仍属于显式启用的外部 L4 验收：

```bash
kairos runtime l4-preflight --venue binance --environment testnet \
  --strategy sma-cross-v1 --instrument '<instrument-id>'
```

外部测试默认 skipped，原因是凭据/Gateway 属于运行环境而非仓库事实。它们不能由 fixture 冒充；正式部署前
必须取得 preflight、24–72h soak、restart、reconciliation 和 kill-switch drill artifact。

Strategy promotion gate 对外部阶段 fail-closed：`PAPER_APPROVED` 必须同时具备非 fixture 的
decision-OOS L5 证据和 Paper/Testnet readiness evidence；`LIVE_LIMITED` / `LIVE_APPROVED`
必须具备 passed external soak artifact，不能只凭本地 maximum_level 或 synthetic fixture 晋级。
`runtime l4-preflight --evidence-artifact <path>` 会写出 promotion-ready readiness artifact；
失败的 preflight artifact 只作诊断，不能通过 gate。
`runtime soak ... --soak-artifact <path>` 会写出 `kind=runtime_l4_soak` 的 promotion-ready soak artifact；
失败或本地 simulated soak artifact 不能通过 LIVE gate。
Promotion gate 会复算外部 readiness/soak artifact 的 `audit_hash`；证据内容被改动后不能继续晋级。

## 全量仓库验收

```bash
./pyenv/bin/python -m compileall -q kairos examples tests studies
./pyenv/bin/python -m unittest discover -s tests
git diff --check
```
